"""FastAPI app.

Viewer (public):   /            search and cited answers
                   /graph       interactive knowledge-graph explorer
                   /entity/{id} /passage/{id}
                   /contribute  upload material for admin review (lands as "pending")
Admin (token):     /admin       sources, approvals, gazetteer, rebuild
JSON API:          /api/search /api/ask /api/graph /api/entity/{id} /api/build/status

Admin login uses the IKG_ADMIN_TOKEN environment variable. If it is unset, a random
token is generated and printed at startup. Set IKG_CONTRIBUTOR_TOKEN to require a
token for contributor uploads as well.

Cited answers call the Claude API, so they run only on POST (never on a link or a crawler's
GET), non-admin visitors are limited to IKG_ASK_LIMIT questions per hour per client address
(default 20; 0 restricts Ask to admins), and all Ask calls together stop for the rest of the
UTC day once their estimated cost reaches IKG_ASK_BUDGET_USD (default 5).
"""

import dataclasses
import hashlib
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Security, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ikg import __version__, config, curators, screen, sources
from ikg.entities import Gazetteer
from ikg.describe import entity_info, snippet
from ikg.graph import neighbours
from ikg.embed import EmbedError
from ikg.limits import RateLimiter, SpendBudget
from ikg.localqa import LocalAnswerError
from ikg.pipeline import Runtime, build
from ikg.retrieve import search
from ikg.segment import KINDS, PARSERS, WORK_LABELS
from ikg.vectorstore import VectorStoreError

DENSE_ERRORS = (EmbedError, VectorStoreError)
DENSE_NOTICE = "Meaning-based search is unavailable right now, so these are keyword (BM25) results."

HERE = Path(__file__).parent
SESSION_COOKIE = "ikg_admin"
SESSION_TTL = 12 * 3600
MAX_SEARCH_K = 50
MAX_ASK_K = 20
MAX_QUESTION_CHARS = 2000

API_DESCRIPTION = """Cited search over the Upanishads, Mahabharata, Ramayana and Vishnu Purana in public-domain
English translations, with a knowledge graph of people, places and concepts.

Cite every claim with the passage id (e.g. mbh.b03.s108.p02), its ref and the translator. A passage's kind
says what it is: 'verse' and 'narrative' are the translated primary text; 'commentary' and 'introduction'
are the translator's own explanation; 'secondary' is modern scholarship. Never present commentary or
scholarship as what the text itself says. Many stories are told in more than one work: unless the user
names a work, search without `works`, and report each telling under its own work. If the passages don't
support an answer, say so."""


# Request and response shapes for the OpenAPI document that API clients (a custom GPT's Actions) import.
# The handlers build plain dicts; these only describe them.

class SearchResult(BaseModel):
    id: str = Field(description="The passage id: cite it, and read the passage with getPassage")
    ref: str = Field(description="Human-readable reference, including the translator")
    kind: str = Field(description="verse | narrative (primary text), commentary | introduction (the "
                                  "translator's), secondary (modern scholarship)")
    score: float
    text: str


class SearchResponse(BaseModel):
    query: str
    retriever: str = Field(description="bm25, dense or hybrid")
    entities: list[str] = Field(description="Knowledge-graph entities recognised in the query")
    results: list[SearchResult]


class AskRequest(BaseModel):
    question: str = Field(description="The question", max_length=MAX_QUESTION_CHARS)
    k: int = Field(8, ge=1, le=MAX_ASK_K, description="Passages to answer from")
    works: list[str] | None = Field(None, description="Limit to these work ids (upanishads, mahabharata, "
                                                      "ramayana, vishnu_purana); omit unless the user names one")
    primary_only: bool = Field(False, description="Only the translated primary text")


class AskCitation(BaseModel):
    passage_id: str
    ref: str
    cited_text: str


class AskResponse(BaseModel):
    answer: str = Field(description="The answer, with numbered citations")
    abstained: bool = Field(description="True when the passages don't support an answer")
    refused: bool
    truncated: bool
    model: str
    cached: bool
    citations: list[AskCitation]
    retrieved: list[str] = Field(description="Ids of the passages the answer was written from")


class PassageResponse(BaseModel):
    id: str
    ref: str
    work: str
    work_title: str
    edition: str
    kind: str
    translator: str
    text: str
    source_url: str
    previous_id: str | None = Field(description="The passage before it in the same edition")
    next_id: str | None = Field(description="The passage after it in the same edition")
    entities: list[str] = Field(description="Knowledge-graph entities mentioned; see getEntity")


class AnswerCache:
    """Recent answers by question and settings, so a repeated question makes no API call.

    Cleared whenever the corpus is reloaded, because an answer is only valid for the passages
    it was built from. Keys also carry the build generation, so an answer that finishes after a
    rebuild is never served. Truncated or refused answers and errors are never stored.
    """

    def __init__(self, size: int):
        self.size = size
        self.lock = threading.Lock()
        self.items: OrderedDict = OrderedDict()

    @staticmethod
    def key(q: str, k: int, works, primary_only: bool, generation: int) -> tuple:
        return (" ".join(q.casefold().split()), k, tuple(sorted(works or [])), primary_only,
                config.MODEL, config.EFFORT, generation)

    def get(self, key):
        with self.lock:
            if key not in self.items:
                return None
            self.items.move_to_end(key)
            return dataclasses.replace(self.items[key], cached=True, cost_usd=0.0)

    def put(self, key, answer):
        if self.size <= 0 or answer.truncated or answer.refused:
            return
        with self.lock:
            self.items[key] = answer
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)

    def clear(self):
        with self.lock:
            self.items.clear()


def _record(*args) -> None:
    """Provenance logging must never undo or fail a completed action."""
    try:
        curators.record(*args)
    except OSError as exc:
        print(f"  could not write the provenance log: {exc}")


def filter_works(works) -> list[dict]:
    """One entry per text for the work filters: passages, filters and graph weights are keyed by
    the work, so two translations of the same text are one choice (D10)."""
    seen: dict[str, dict] = {}
    for w in works:
        seen.setdefault(w.work_id, {"id": w.work_id, "title": WORK_LABELS.get(w.work_id, w.title)})
    return list(seen.values())


def same_origin(request: Request) -> bool:
    """False for a browser request sent from another site (an auto-submitted form or a fetch),
    which would spend the visitor's quota and the shared budget. Clients that send neither
    header, such as curl or scripts, are allowed and still limited per address."""
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return origin.split("://", 1)[-1] == request.headers.get("host", "")


def token_ok(given: str, expected: str) -> bool:
    # compare_digest on str rejects non-ASCII input with TypeError; bytes accept anything.
    return secrets.compare_digest(given.encode(), expected.encode())


class BuildJob:
    """One background rebuild at a time; the log streams to the admin page."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state, self.log, self.stats, self.error = "idle", [], None, ""
        self.started = self.finished = None

    def snapshot(self) -> dict:
        return {"state": self.state, "log": self.log[-200:], "stats": self.stats, "error": self.error,
                "started": self.started, "finished": self.finished}

    def start(self, on_success, on_finish=None, on_start=None) -> bool:
        """Run a build in a thread. `on_start()` runs before it begins and `on_finish(state, error)`
        when it ends, either way."""
        if not self.lock.acquire(blocking=False):
            return False
        if on_start:
            on_start()
        self.state, self.log, self.stats, self.error = "running", [], None, ""
        self.started, self.finished = time.strftime("%H:%M:%S"), None

        def work():
            try:
                self.stats = build(log=self.log.append)
                if error := on_success():
                    raise RuntimeError(f"the new build could not be loaded ({error}); still serving the previous one")
                self.state = "done"
            except Exception as exc:  # surface any failure in the admin UI
                self.state, self.error = "error", str(exc)
                self.log.append(f"ERROR: {exc}")
            finally:
                self.finished = time.strftime("%H:%M:%S")
                self.lock.release()
                if on_finish:
                    on_finish(self.state, self.error)

        threading.Thread(target=work, daemon=True).start()
        return True


def clamp_k(k, upper: int) -> int:
    try:
        return max(1, min(int(k), upper))
    except (TypeError, ValueError):
        raise HTTPException(400, "k must be an integer") from None


async def read_upload(file: UploadFile) -> bytes:
    """Read an upload in chunks, stopping as soon as it exceeds the size limit."""
    chunks, size = [], 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > sources.MAX_UPLOAD_BYTES:
            raise ValueError(f"file is larger than {sources.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


async def screen_upload(filename: str, content: bytes, title: str, notes: str) -> str:
    """Reject material unrelated to Indic texts; returns the verdict summary to store."""
    if not screen.enabled():
        return ""
    text = await run_in_threadpool(screen.extract_text, filename, content)  # may take up to PDF_TIMEOUT
    try:
        verdict = await run_in_threadpool(screen.screen, text, title, notes)
    except screen.ScreeningUnavailable as exc:
        print(f"  upload screening unavailable: {exc}")
        raise ValueError("The upload could not be checked for relevance right now. Please try again later.") from None
    if not verdict.related:
        raise ValueError(f"This material does not appear to be about Indic texts or traditions, "
                         f"so it was not accepted. Reason: {verdict.reason}")
    return verdict.summary()


def create_app() -> FastAPI:
    public_url = os.environ.get("IKG_PUBLIC_URL", "").rstrip("/")
    app = FastAPI(title="Indic Knowledge Graph", version=__version__, description=API_DESCRIPTION,
                  servers=[{"url": public_url}] if public_url else None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["snippet"] = snippet

    admin_token = os.environ.get("IKG_ADMIN_TOKEN")
    if not admin_token:
        admin_token = secrets.token_urlsafe(18)
        print(f"\n  IKG_ADMIN_TOKEN not set; admin token for this run: {admin_token}\n")
    contributor_token = os.environ.get("IKG_CONTRIBUTOR_TOKEN", "")
    # API clients (a custom GPT's Actions) send `Authorization: Bearer <IKG_API_TOKEN>`: Ask skips the
    # same-origin check for them and counts against their own hourly limit, not the visitors'.
    api_token = os.environ.get("IKG_API_TOKEN", "")
    if api_token and len(api_token) < 16:
        raise ValueError("IKG_API_TOKEN must be at least 16 characters")
    api_ask_limiter = RateLimiter(int(os.environ.get("IKG_API_ASK_LIMIT", "60")))
    bearer = HTTPBearer(auto_error=False, description="IKG_API_TOKEN, for API clients such as a custom GPT")
    sessions: dict[str, tuple[float, str]] = {}  # session id -> (expiry, curator)
    ask_limiter = RateLimiter(int(os.environ.get("IKG_ASK_LIMIT", "20")))
    upload_limiter = RateLimiter(config.UPLOAD_LIMIT)
    login_failures = RateLimiter(config.LOGIN_FAILURE_LIMIT)  # failed admin logins per client per hour
    upload_global = RateLimiter(config.UPLOAD_GLOBAL_LIMIT)
    app.state.upload_limiter, app.state.upload_global = upload_limiter, upload_global
    budget = SpendBudget(config.ASK_BUDGET_USD)
    app.state.budget = budget
    answer_cache = AnswerCache(config.ANSWER_CACHE_SIZE)
    app.state.answer_cache = answer_cache
    job = BuildJob()
    state = {"runtime": None, "error": "", "generation": 0}

    def reload_runtime() -> str:
        """Load the build on disk. If that fails while a build is already being served, keep serving
        it and return the error, so a bad rebuild can't take the site down."""
        try:
            runtime = Runtime.load()
        except Exception as exc:
            if state["runtime"] is None:
                state["error"] = str(exc)
            return str(exc)
        if config.RETRIEVER != "bm25" and runtime.embeddings is not None and not runtime.prepare_dense():
            # Load the query model and the vector store now, not on a visitor's first search.
            print(f"Dense search unavailable, serving BM25: {runtime.dense_error}", flush=True)
        state["generation"] += 1
        previous = state["runtime"]
        state["runtime"], state["error"] = runtime, ""
        if previous is not None:
            previous.close()
        answer_cache.clear()  # cached answers cite passages of the previous build
        return ""

    reload_runtime()

    def runtime() -> Runtime:
        if state["runtime"] is None:
            raise HTTPException(503, state["error"] or "Corpus not built yet. Ask an admin to run a rebuild.")
        return state["runtime"]

    def curator(request: Request) -> str | None:
        """The curator logged in on this request, or None."""
        session = sessions.get(request.cookies.get(SESSION_COOKIE, ""))
        if not session or session[0] <= time.time():
            return None
        return session[1] if curators.is_current(session[1]) else None  # a removed curator is logged out

    def is_admin(request: Request) -> bool:
        return curator(request) is not None

    def require_admin(request: Request):
        if not is_admin(request):
            if request.url.path.startswith("/api/"):  # scripts and the admin page's poller expect JSON
                raise HTTPException(401, "Admin login required", headers={"WWW-Authenticate": "Cookie"})
            raise HTTPException(303, headers={"Location": "/admin/login"})

    def api_client(credentials: HTTPAuthorizationCredentials | None) -> bool:
        """True for a request with the API token; 401 for one with a wrong token."""
        if credentials is None:
            return False
        if not api_token or not token_ok(credentials.credentials, api_token):
            raise HTTPException(401, "Invalid API token", headers={"WWW-Authenticate": "Bearer"})
        return True

    def ask_refusal(request: Request, api: bool = False) -> str:
        """Why this Ask may not call the API now; empty if it may. `api`: the request has the API token."""
        if not (api or same_origin(request)):
            return "Cited answers can only be requested from this site."
        if not budget.available():
            return "The daily budget for cited answers has been used up. Search still works."
        allowed = (api_ask_limiter.allow("api") if api else
                   ask_limiter.allow(request.client.host if request.client else ""))
        if not (is_admin(request) or allowed):
            return "Question limit reached for this hour. Search still works."
        return ""

    def cached(q: str, k: int, works, primary_only: bool):
        return answer_cache.get(AnswerCache.key(q, k, works, primary_only, state["generation"]))

    def find(rt, q: str, **kwargs) -> tuple[list, str]:
        """Search with the default retriever, and the mode used. If dense search fails, BM25 for this
        and later requests (until the next build loads), rather than an error page."""
        retrieval = rt.retrieval()
        try:
            return search(rt.index, rt.gazetteer, q, passage_entities=rt.passage_entities, **kwargs,
                          **retrieval), retrieval["mode"]
        except DENSE_ERRORS as exc:
            rt.dense_error = str(exc)
            print(f"Dense search failed, serving BM25: {exc}", flush=True)
            return search(rt.index, rt.gazetteer, q, passage_entities=rt.passage_entities, **kwargs), "bm25"

    def answer(request: Request, q: str, k: int, works, primary_only: bool):
        rt = runtime()
        from ikg.qa import ask
        generation = state["generation"]

        def ask_with(retrieval):
            return ask(q, rt.index, rt.gazetteer, graph=rt.graph, passage_entities=rt.passage_entities,
                       k=k, works=works, primary_only=primary_only, retrieval=retrieval)
        from ikg.qa import may_have_been_billed
        try:
            try:
                ans = ask_with(rt.retrieval())
            except DENSE_ERRORS as exc:  # retrieval failed before any model call: retry with BM25
                rt.dense_error = str(exc)
                print(f"Dense search failed, serving BM25: {exc}", flush=True)
                ans = ask_with({"mode": "bm25"})
        except Exception as exc:
            if may_have_been_billed(exc):  # e.g. a timeout or a server error
                budget.charge(None)
            raise
        budget.charge(ans.cost_usd)
        if state["generation"] == generation:  # a rebuild during the call makes this answer stale
            answer_cache.put(AnswerCache.key(q, k, works, primary_only, generation), ans)
        return ans

    def registry(include_unapproved: bool = False) -> tuple[list, str]:
        """The work registry, or no works and the reason: a broken data/works.yaml shows as a notice
        instead of failing every page."""
        try:
            return sources.load_works(include_unapproved=include_unapproved), ""
        except ValueError as exc:
            return [], f"The work registry is invalid: {exc}. Fix {config.USER_WORKS_FILE}."

    def render(request: Request, name: str, **ctx):
        rt = state["runtime"]
        works, registry_error = registry()
        return templates.TemplateResponse(request, name, {
            "is_admin": is_admin(request), "corpus_ready": rt is not None,
            "corpus_error": state["error"], "registry_error": registry_error,
            "all_works": filter_works(works), **ctx})

    @app.middleware("http")
    async def limit_contributions(request: Request, call_next):
        """Refuse an oversized or over-limit upload before its body is received: by the time a route
        runs, the framework has already read and spooled the whole multipart body."""
        if request.method == "POST" and request.url.path == "/contribute" and not is_admin(request):
            refusal = ""
            try:
                too_big = int(request.headers.get("content-length", "0")) > sources.MAX_UPLOAD_BYTES + (1 << 20)
            except ValueError:
                too_big = False
            client_key = request.client.host if request.client else ""
            if too_big:
                refusal = f"The file is larger than {sources.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            elif not (upload_limiter.has_room(client_key) and upload_global.has_room("*")):
                refusal = "Too many uploads in the last hour. Please try again later."
            else:  # both have room: record the upload against both
                upload_limiter.allow(client_key)
                upload_global.allow("*")
            if refusal:
                response = render(request, "contribute.html", kinds=KINDS, needs_token=bool(contributor_token),
                                  message="", error=refusal)
                response.status_code = 413 if too_big else 429
                return response
        return await call_next(request)

    # ------------------------------------------------------------------ viewer

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, q: str = "", mode: str = "search", primary_only: bool = False, k: int = 8):
        # A GET never calls the API (links, previews and crawlers would spend credits);
        # an Ask link only fills in the form, and submitting it POSTs.
        return results(request, q, mode, primary_only, k, request.query_params.getlist("works"), post=False)

    @app.post("/", response_class=HTMLResponse)
    async def home_post(request: Request):
        form = await request.form()
        return await run_in_threadpool(
            results, request, str(form.get("q", "")), str(form.get("mode", "search")),
            form.get("primary_only") == "true", form.get("k", 8), form.getlist("works"), True)

    def results(request: Request, q: str, mode: str, primary_only: bool, k, works: list[str], post: bool):
        q, works = q[:MAX_QUESTION_CHARS], works or None
        k = clamp_k(k, MAX_ASK_K if mode == "ask" else MAX_SEARCH_K)
        ctx = {"q": q, "mode": mode, "primary_only": primary_only, "k": k, "selected_works": works or [],
               "hits": [], "answer": None, "error": "", "ask_pending": mode == "ask" and q and not post}
        if q and state["runtime"] is not None:
            rt = runtime()
            hit = cached(q, k, works, primary_only) if mode == "ask" and post else None
            refusal = ask_refusal(request) if mode == "ask" and post and hit is None else ""
            if hit is not None:
                ctx["answer"] = hit  # free: no API call, no budget or rate-limit use
            elif refusal:
                ctx["error"] = refusal
            elif mode == "ask" and post:
                try:
                    ctx["answer"] = answer(request, q, k, works, primary_only)
                except LocalAnswerError:  # its message names the Ollama host: keep that out of the page
                    ctx["error"] = ("Answering failed: the local model could not answer. Cited answers need a "
                                    "local Ollama server with that model. Search still works.")
                except Exception as exc:  # missing API key, network, rate limit...
                    ctx["error"] = (f"Answering failed: {exc}. Cited answers need Claude API credentials "
                                    "(set ANTHROPIC_API_KEY before starting the server). Search still works.")
            ctx["hits"] = (ctx["answer"].hits if ctx["answer"] else
                           find(rt, q, k=k, works=works, primary_only=primary_only)[0])
            if config.RETRIEVER != "bm25" and rt.embeddings is not None and rt.dense_error:
                ctx["retrieval_notice"] = DENSE_NOTICE
            ctx["entities"] = [rt.gazetteer.entities[e] for e in rt.gazetteer.entities_in(q)]
        return render(request, "search.html", **ctx)

    @app.get("/graph", response_class=HTMLResponse)
    def graph_page(request: Request):
        types = sorted({e.type for e in runtime().gazetteer.entities.values()})
        return render(request, "graph.html", types=types)

    @app.get("/entity/{entity_id}", response_class=HTMLResponse)
    def entity_page(request: Request, entity_id: str):
        rt = runtime()
        if entity_id not in rt.gazetteer.entities:
            raise HTTPException(404, "Unknown entity")
        return render(request, "entity.html", e=rt.gazetteer.entities[entity_id], info=entity_info(rt, entity_id, 100))

    @app.get("/passage/{pid}", response_class=HTMLResponse)
    def passage_page(request: Request, pid: str):
        rt = runtime()
        p = rt.passages.get(pid)
        if p is None:
            raise HTTPException(404, "Unknown passage")
        prev, nxt = rt.neighbour_passages(pid)
        ents = [rt.gazetteer.entities[e] for e in sorted(rt.passage_entities.get(pid, set()))]
        return render(request, "passage.html", p=p, prev=prev, nxt=nxt, entities=ents)

    # ------------------------------------------------------------------ contributors

    @app.get("/contribute", response_class=HTMLResponse)
    def contribute_form(request: Request):
        return render(request, "contribute.html", kinds=KINDS, needs_token=bool(contributor_token),
                      message="", error="")

    @app.post("/contribute", response_class=HTMLResponse)
    async def contribute(request: Request, title: str = Form(...), author: str = Form(""),
                         year: str = Form(""), kind: str = Form("secondary"), contributor: str = Form(""),
                         notes: str = Form(""), token: str = Form(""), file: UploadFile = File(...)):
        ctx = {"kinds": KINDS, "needs_token": bool(contributor_token), "message": "", "error": ""}
        if contributor_token and not token_ok(token, contributor_token):
            ctx["error"] = "Invalid contributor token."
            return render(request, "contribute.html", **ctx)
        entry = None
        try:
            filename, content = file.filename or "upload.txt", await read_upload(file)
            verdict = await screen_upload(filename, content, title.strip()[:200], notes.strip()[:2000])
            entry = sources.save_upload(filename, content)
            work = sources.add_user_work(title.strip()[:200], author.strip()[:200], year.strip()[:20], [entry],
                                         kind=kind if kind in KINDS else "secondary",
                                         submitted_by=contributor.strip()[:100], notes=notes.strip()[:2000],
                                         screening=verdict)
            ctx["message"] = f"Thank you. '{work.title}' was submitted and is waiting for admin review."
        except (ValueError, OSError) as exc:
            if entry:  # saved but not registered: don't leave an orphan file behind
                (config.UPLOADS_DIR / entry["upload"]).unlink(missing_ok=True)
            ctx["error"] = str(exc) if isinstance(exc, ValueError) else "The upload could not be saved. Please try again later."
        else:
            _record("public", "work_submitted", work.id, f"by {contributor.strip()[:100] or 'anonymous'}")
        return render(request, "contribute.html", **ctx)

    # ------------------------------------------------------------------ admin

    @app.get("/admin/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html", error="")

    @app.post("/admin/login")
    def login(request: Request, token: str = Form(...)):
        client_key = request.client.host if request.client else ""
        if not login_failures.has_room(client_key):
            return render(request, "login.html", error="Too many failed logins. Try again later.")
        try:
            name = curators.identify(token, admin_token)
        except ValueError as exc:  # a broken curators file: keep the detail out of the public page
            print(f"  curator registry error: {exc}")
            return render(request, "login.html", error="The curator registry is unavailable; ask the operator.")
        if name is None:
            login_failures.allow(client_key)
            _record("public", "login_failed", "", client_key)  # never the token itself
            return render(request, "login.html", error="Invalid token.")
        now = time.time()
        for old in [s for s, (expiry, _) in sessions.items() if expiry <= now]:
            del sessions[old]
        sid = secrets.token_urlsafe(32)
        sessions[sid] = (now + SESSION_TTL, name)
        _record(name, "login")
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="strict", max_age=SESSION_TTL,
                        secure=request.url.scheme == "https")
        return resp

    @app.post("/admin/logout")
    def logout(request: Request):
        if name := curator(request):
            _record(name, "logout")
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        resp = RedirectResponse("/", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    def admin_page(request: Request, message: str = "", error: str = ""):
        works, _ = registry(include_unapproved=True)
        rt = state["runtime"]
        counts = {}
        if rt:
            for p in rt.index.passages:
                edition = p.edition or p.work
                counts[edition] = counts.get(edition, 0) + 1
        gz_text = config.gazetteer_file().read_text(encoding="utf-8")
        return render(request, "admin.html", works=works, counts=counts, job=job.snapshot(), kinds=KINDS,
                      parsers=sorted(PARSERS), gazetteer_text=gz_text,
                      gazetteer_custom=config.USER_GAZETTEER_FILE.exists(), message=message, error=error,
                      curator=curator(request), provenance=curators.recent(30))

    @app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    def admin(request: Request):
        return admin_page(request)

    @app.post("/admin/gutenberg", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    def add_gutenberg(request: Request, gutenberg_id: int = Form(...), title: str = Form(...),
                      translator: str = Form(""), year: str = Form(""), kind: str = Form("narrative"),
                      parser: str = Form("generic")):
        if parser not in PARSERS or kind not in KINDS:
            return admin_page(request, error="Unknown parser or kind.")
        try:
            work = sources.add_user_work(title.strip(), translator.strip(), year.strip(),
                                         [{"gutenberg": gutenberg_id}], parser=parser, kind=kind,
                                         submitted_by=curator(request), status="approved")
        except ValueError as exc:
            return admin_page(request, error=str(exc))
        _record(curator(request), "work_added", work.id, f"Gutenberg #{gutenberg_id}, approved")
        return admin_page(request, message=f"Added '{work.title}' (Gutenberg #{gutenberg_id}). "
                                           "It is downloaded on the next rebuild.")

    @app.post("/admin/upload", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    async def admin_upload(request: Request, title: str = Form(...), author: str = Form(""),
                           year: str = Form(""), kind: str = Form("narrative"), file: UploadFile = File(...)):
        try:
            filename, content = file.filename or "upload.txt", await read_upload(file)
            verdict = await screen_upload(filename, content, title.strip(), "")
            entry = sources.save_upload(filename, content)
            work = sources.add_user_work(title.strip(), author.strip(), year.strip(), [entry],
                                         kind=kind if kind in KINDS else "narrative",
                                         submitted_by=curator(request), status="approved", screening=verdict)
        except ValueError as exc:
            return admin_page(request, error=str(exc))
        _record(curator(request), "work_added", work.id, f"upload {entry.get('original_name', '')}, approved")
        return admin_page(request, message=f"Uploaded '{work.title}'. Rebuild to include it.")

    @app.post("/admin/works/{work_id}/status", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    def change_status(request: Request, work_id: str, status: str = Form(...)):
        try:
            work = sources.set_status(work_id, status)
        except (KeyError, ValueError) as exc:
            return admin_page(request, error=str(exc))
        _record(curator(request), "work_status", work_id, status)
        return admin_page(request, message=f"'{work.title}' is now {status}. Rebuild to apply.")

    @app.post("/admin/gazetteer", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    def save_gazetteer(request: Request, yaml_text: str = Form(...), action: str = Form("save")):
        if action == "reset":
            config.USER_GAZETTEER_FILE.unlink(missing_ok=True)
            _record(curator(request), "gazetteer_reset")
            return admin_page(request, message="Gazetteer reset to the bundled version. Rebuild to apply.")
        try:
            gz = Gazetteer.from_yaml(yaml_text)
        except Exception as exc:
            return admin_page(request, error=f"Gazetteer not saved: {exc}")
        config.USER_GAZETTEER_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.USER_GAZETTEER_FILE.write_text(yaml_text, encoding="utf-8")
        _record(curator(request), "gazetteer_saved", "",
                        f"{len(gz.entities)} entities, sha256 {hashlib.sha256(yaml_text.encode()).hexdigest()[:12]}")
        return admin_page(request, message=f"Gazetteer saved ({len(gz.entities)} entities). Rebuild to apply.")

    @app.post("/admin/rebuild", dependencies=[Depends(require_admin)])
    def rebuild(request: Request):
        who = curator(request)

        def finished(state: str, error: str):
            _record(who, f"rebuild_{state}", "", error)

        started = job.start(on_success=reload_runtime, on_finish=finished,
                            on_start=lambda: _record(who, "rebuild_started"))
        return admin_page(request, message="Rebuild started." if started else "",
                          error="" if started else "A rebuild is already running.")

    # ------------------------------------------------------------------ JSON API

    @app.get("/api/build/status", dependencies=[Depends(require_admin)])
    def build_status():
        return job.snapshot()

    @app.get("/api/search", operation_id="searchPassages", summary="Find passages for a question or topic",
             description="Ranked passages. Sanskrit names and terms are understood. Use before answering "
                         "anything about these texts.", responses={200: {"model": SearchResponse}})
    def api_search(q: str = Query(description="A question or keywords"),
                   k: int = Query(8, description=f"Number of passages, 1-{MAX_SEARCH_K}"),
                   primary_only: bool = Query(False, description="Only the translated primary text"),
                   works: list[str] | None = Query(None, description="Limit to these work ids (upanishads, "
                                                   "mahabharata, ramayana, vishnu_purana); omit unless the user "
                                                   "names a work")):
        rt = runtime()
        hits, mode = find(rt, q, k=clamp_k(k, MAX_SEARCH_K), works=works or None, primary_only=primary_only)
        return {"query": q, "retriever": mode, "entities": rt.gazetteer.entities_in(q), "results": [
            {"id": h.passage.id, "ref": h.passage.ref, "kind": h.passage.kind, "score": round(h.score, 3),
             "text": h.passage.text} for h in hits]}

    @app.get("/api/passage/{pid}", operation_id="getPassage", summary="Read one passage by id",
             description="The passage with its ref, translator, kind and source, the ids of the passages "
                         "before and after it, and the entities it mentions.",
             responses={200: {"model": PassageResponse}})
    def api_passage(pid: str):
        rt = runtime()
        p = rt.passages.get(pid)
        if p is None:
            raise HTTPException(404, "Unknown passage")
        prev, nxt = rt.neighbour_passages(pid)
        return {"id": p.id, "ref": p.ref, "work": p.work, "work_title": p.work_title, "edition": p.edition,
                "kind": p.kind, "translator": p.translator, "text": p.text, "source_url": p.source_url,
                "previous_id": prev.id if prev else None, "next_id": nxt.id if nxt else None,
                "entities": sorted(rt.passage_entities.get(pid, set()))}

    @app.post("/api/ask", operation_id="askQuestion", summary="A cited answer to a question",
              description="Searches, then writes an answer citing passage ids, or abstains when the passages "
                          "don't support one. Slower than searchPassages and limited per hour.",
              responses={200: {"model": AskResponse}},
              openapi_extra={"requestBody": {"required": True, "content": {
                  "application/json": {"schema": AskRequest.model_json_schema()}}}})
    async def api_ask(request: Request, credentials: HTTPAuthorizationCredentials | None = Security(bearer)):
        api = api_client(credentials)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Request body must be JSON") from None
        if not isinstance(body, dict) or not str(body.get("question", "")).strip():
            raise HTTPException(400, "Body must be an object with a non-empty 'question'")
        works = body.get("works")
        if works is not None and not (isinstance(works, list) and all(isinstance(w, str) for w in works)):
            raise HTTPException(400, "'works' must be a list of work ids")
        primary_only = body.get("primary_only", False)
        if not isinstance(primary_only, bool):  # the string "false" would otherwise read as true
            raise HTTPException(400, "'primary_only' must be true or false")
        k = clamp_k(body.get("k", 8), MAX_ASK_K)
        runtime()
        question = str(body["question"])[:MAX_QUESTION_CHARS]
        ans = cached(question, k, works or None, primary_only)
        refusal = "" if ans is not None else ask_refusal(request, api)
        if refusal:
            raise HTTPException(429, refusal)
        try:
            # The Claude call blocks for seconds; keep it off the event loop.
            if ans is None:
                ans = await run_in_threadpool(answer, request, question, k, works or None, primary_only)
        except LocalAnswerError:  # its message names the Ollama host
            raise HTTPException(503, "Answering failed: the local model could not answer.") from None
        except Exception as exc:  # missing API key, network, rate limit...
            raise HTTPException(503, f"Answering failed: {exc}") from None
        return {"answer": ans.text, "abstained": ans.abstained, "refused": ans.refused,
                "truncated": ans.truncated, "model": ans.model, "cached": ans.cached,
                "citations": [c.__dict__ for c in ans.citations],
                "retrieved": [h.passage.id for h in ans.hits]}

    @app.get("/api/graph", operation_id="getGraph", summary="The co-occurrence graph, for drawing")
    def api_graph(work: str = "", types: str = "", min_weight: int = 5, max_nodes: int = 80):
        rt = runtime()
        g = rt.graph
        allowed = set(filter(None, types.split(",")))
        key = f"weight_{work}" if work else "weight"

        def node_mentions(n):
            if not work:
                return int(g.nodes[n].get("mentions", 0))
            for part in str(g.nodes[n].get("works", "")).split(";"):
                w, _, c = part.partition(":")
                if w == work:
                    return int(c)
            return 0

        nodes = [n for n in g.nodes if (not allowed or g.nodes[n].get("type") in allowed) and node_mentions(n) > 0]
        nodes = sorted(nodes, key=lambda n: -node_mentions(n))[:max(1, min(max_nodes, 300))]
        keep = set(nodes)
        edges = [{"data": {"id": f"{a}__{b}", "source": a, "target": b, "weight": int(d.get(key, 0))}}
                 for a, b, d in g.edges(data=True)
                 if a in keep and b in keep and int(d.get(key, 0)) >= min_weight]
        return {"nodes": [{"data": {"id": n, "label": g.nodes[n].get("label", n),
                                    "type": g.nodes[n].get("type", ""), "mentions": node_mentions(n)}}
                          for n in nodes],
                "edges": edges}

    @app.get("/api/entity/{entity_id}", operation_id="getEntity",
             summary="An entity: names, renderings, works, neighbours and passages",
             description="Entity ids appear in searchPassages (entities) and getPassage (entities), e.g. "
                         "krishna, dharma.")
    def api_entity(entity_id: str):
        rt = runtime()
        if entity_id not in rt.gazetteer.entities:
            raise HTTPException(404, "Unknown entity")
        return entity_info(rt, entity_id, 10)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 303:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        return templates.TemplateResponse(request, "error.html", {
            "status": exc.status_code, "detail": exc.detail, "is_admin": is_admin(request),
            "corpus_ready": state["runtime"] is not None, "corpus_error": state["error"]},
            status_code=exc.status_code)

    # The OpenAPI document is for API clients: the JSON API only, not the HTML pages and admin routes.
    for route in app.routes:
        if isinstance(route, APIRoute) and (not route.path.startswith("/api/") or route.path == "/api/build/status"):
            route.include_in_schema = False

    app.state.job = job
    app.state.reload_runtime = reload_runtime
    app.state.sessions = sessions
    app.state.ask_limiter = ask_limiter
    app.state.api_ask_limiter = api_ask_limiter
    return app
