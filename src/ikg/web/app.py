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
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from ikg import config, screen, sources
from ikg.entities import Gazetteer
from ikg.graph import neighbours
from ikg.pipeline import Runtime, build
from ikg.retrieve import search
from ikg.segment import KINDS, PARSERS, WORK_LABELS

HERE = Path(__file__).parent
SESSION_COOKIE = "ikg_admin"
SESSION_TTL = 12 * 3600
MAX_SEARCH_K = 50
MAX_ASK_K = 20
MAX_QUESTION_CHARS = 2000


class RateLimiter:
    """Sliding one-hour window of events per key, kept in memory."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self.lock = threading.Lock()
        self.events: dict[str, deque] = {}
        self.calls = 0

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            self.calls += 1
            if self.calls % 256 == 0:  # forget clients idle for an hour, so memory stays bounded
                for idle in [k for k, q in self.events.items() if not q or q[-1] <= now - 3600]:
                    del self.events[idle]
            q = self.events.setdefault(key, deque())
            while q and q[0] <= now - 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                return False
            q.append(now)
            return True


class SpendBudget:
    """Estimated Claude spend per UTC day, shared by every visitor, kept in memory."""

    UNKNOWN_COST = 0.25  # charged when an answer reports no cost (unpriced model, or an error)

    def __init__(self, usd_per_day: float):
        self.limit = usd_per_day
        self.lock = threading.Lock()
        self.day, self.spent = "", 0.0

    def _roll(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.day:
            self.day, self.spent = today, 0.0

    def available(self) -> bool:
        with self.lock:
            self._roll()
            return self.spent < self.limit

    def charge(self, usd: float | None):
        with self.lock:
            self._roll()
            self.spent += self.UNKNOWN_COST if usd is None else usd


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

    def start(self, on_success) -> bool:
        if not self.lock.acquire(blocking=False):
            return False
        self.state, self.log, self.stats, self.error = "running", [], None, ""
        self.started, self.finished = time.strftime("%H:%M:%S"), None

        def work():
            try:
                self.stats = build(log=self.log.append)
                on_success()
                self.state = "done"
            except Exception as exc:  # surface any failure in the admin UI
                self.state, self.error = "error", str(exc)
                self.log.append(f"ERROR: {exc}")
            finally:
                self.finished = time.strftime("%H:%M:%S")
                self.lock.release()

        threading.Thread(target=work, daemon=True).start()
        return True


def snippet(text: str, n: int = 320) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= n else flat[:n].rsplit(" ", 1)[0] + "..."


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
    text = screen.extract_text(filename, content)
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
    app = FastAPI(title="Indic Knowledge Graph")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["snippet"] = snippet

    admin_token = os.environ.get("IKG_ADMIN_TOKEN")
    if not admin_token:
        admin_token = secrets.token_urlsafe(18)
        print(f"\n  IKG_ADMIN_TOKEN not set; admin token for this run: {admin_token}\n")
    contributor_token = os.environ.get("IKG_CONTRIBUTOR_TOKEN", "")
    sessions: dict[str, float] = {}
    ask_limiter = RateLimiter(int(os.environ.get("IKG_ASK_LIMIT", "20")))
    budget = SpendBudget(config.ASK_BUDGET_USD)
    app.state.budget = budget
    answer_cache = AnswerCache(config.ANSWER_CACHE_SIZE)
    app.state.answer_cache = answer_cache
    job = BuildJob()
    state = {"runtime": None, "error": "", "generation": 0}

    def reload_runtime():
        state["generation"] += 1
        try:
            state["runtime"], state["error"] = Runtime.load(), ""
        except Exception as exc:
            state["runtime"], state["error"] = None, str(exc)
        answer_cache.clear()  # cached answers cite passages of the previous build

    reload_runtime()

    def runtime() -> Runtime:
        if state["runtime"] is None:
            raise HTTPException(503, state["error"] or "Corpus not built yet. Ask an admin to run a rebuild.")
        return state["runtime"]

    def is_admin(request: Request) -> bool:
        sid = request.cookies.get(SESSION_COOKIE, "")
        expiry = sessions.get(sid)
        return bool(expiry and expiry > time.time())

    def require_admin(request: Request):
        if not is_admin(request):
            raise HTTPException(303, headers={"Location": "/admin/login"})

    def ask_refusal(request: Request) -> str:
        """Why this Ask may not call the API now; empty if it may."""
        if not same_origin(request):
            return "Cited answers can only be requested from this site."
        if not budget.available():
            return "The daily budget for cited answers has been used up. Search still works."
        if not (is_admin(request) or ask_limiter.allow(request.client.host if request.client else "")):
            return "Question limit reached for this hour. Search still works."
        return ""

    def cached(q: str, k: int, works, primary_only: bool):
        return answer_cache.get(AnswerCache.key(q, k, works, primary_only, state["generation"]))

    def answer(request: Request, q: str, k: int, works, primary_only: bool):
        rt = runtime()
        from ikg.qa import ask
        generation = state["generation"]
        try:
            ans = ask(q, rt.index, rt.gazetteer, graph=rt.graph, passage_entities=rt.passage_entities,
                      k=k, works=works, primary_only=primary_only)
        except anthropic.APITimeoutError:  # a subclass of APIConnectionError, but may have been billed
            budget.charge(None)
            raise
        except (ValueError, anthropic.BadRequestError, anthropic.AuthenticationError,
                anthropic.PermissionDeniedError, anthropic.NotFoundError, anthropic.APIConnectionError):
            raise  # rejected or never sent: not billed
        except Exception:
            budget.charge(None)  # e.g. a server error: the request may still have been billed
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
                except Exception as exc:  # missing API key, network, rate limit...
                    ctx["error"] = (f"Answering failed: {exc}. Cited answers need Claude API credentials "
                                    "(set ANTHROPIC_API_KEY before starting the server). Search still works.")
            ctx["hits"] = (ctx["answer"].hits if ctx["answer"] else
                           search(rt.index, rt.gazetteer, q, k=k, works=works, primary_only=primary_only,
                                  passage_entities=rt.passage_entities))
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
        try:
            filename, content = file.filename or "upload.txt", await read_upload(file)
            verdict = await screen_upload(filename, content, title.strip()[:200], notes.strip()[:2000])
            entry = sources.save_upload(filename, content)
            work = sources.add_user_work(title.strip()[:200], author.strip()[:200], year.strip()[:20], [entry],
                                         kind=kind if kind in KINDS else "secondary",
                                         submitted_by=contributor.strip()[:100], notes=notes.strip()[:2000],
                                         screening=verdict)
            ctx["message"] = f"Thank you. '{work.title}' was submitted and is waiting for admin review."
        except ValueError as exc:
            ctx["error"] = str(exc)
        return render(request, "contribute.html", **ctx)

    # ------------------------------------------------------------------ admin

    @app.get("/admin/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html", error="")

    @app.post("/admin/login")
    def login(request: Request, token: str = Form(...)):
        if not token_ok(token, admin_token):
            return render(request, "login.html", error="Invalid token.")
        now = time.time()
        for old in [s for s, expiry in sessions.items() if expiry <= now]:
            del sessions[old]
        sid = secrets.token_urlsafe(32)
        sessions[sid] = now + SESSION_TTL
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="strict", max_age=SESSION_TTL,
                        secure=request.url.scheme == "https")
        return resp

    @app.post("/admin/logout")
    def logout(request: Request):
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
                      gazetteer_custom=config.USER_GAZETTEER_FILE.exists(), message=message, error=error)

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
                                         submitted_by="admin", status="approved")
        except ValueError as exc:
            return admin_page(request, error=str(exc))
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
                                         submitted_by="admin", status="approved", screening=verdict)
        except ValueError as exc:
            return admin_page(request, error=str(exc))
        return admin_page(request, message=f"Uploaded '{work.title}'. Rebuild to include it.")

    @app.post("/admin/works/{work_id}/status", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    def change_status(request: Request, work_id: str, status: str = Form(...)):
        try:
            work = sources.set_status(work_id, status)
        except (KeyError, ValueError) as exc:
            return admin_page(request, error=str(exc))
        return admin_page(request, message=f"'{work.title}' is now {status}. Rebuild to apply.")

    @app.post("/admin/gazetteer", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    def save_gazetteer(request: Request, yaml_text: str = Form(...), action: str = Form("save")):
        if action == "reset":
            config.USER_GAZETTEER_FILE.unlink(missing_ok=True)
            return admin_page(request, message="Gazetteer reset to the bundled version. Rebuild to apply.")
        try:
            gz = Gazetteer.from_yaml(yaml_text)
        except Exception as exc:
            return admin_page(request, error=f"Gazetteer not saved: {exc}")
        config.USER_GAZETTEER_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.USER_GAZETTEER_FILE.write_text(yaml_text, encoding="utf-8")
        return admin_page(request, message=f"Gazetteer saved ({len(gz.entities)} entities). Rebuild to apply.")

    @app.post("/admin/rebuild", dependencies=[Depends(require_admin)])
    def rebuild(request: Request):
        started = job.start(on_success=reload_runtime)
        return admin_page(request, message="Rebuild started." if started else "",
                          error="" if started else "A rebuild is already running.")

    # ------------------------------------------------------------------ JSON API

    @app.get("/api/build/status", dependencies=[Depends(require_admin)])
    def build_status():
        return job.snapshot()

    @app.get("/api/search")
    def api_search(request: Request, q: str, k: int = 8, primary_only: bool = False):
        rt = runtime()
        hits = search(rt.index, rt.gazetteer, q, k=clamp_k(k, MAX_SEARCH_K), works=request.query_params.getlist("works") or None,
                      primary_only=primary_only, passage_entities=rt.passage_entities)
        return {"query": q, "entities": rt.gazetteer.entities_in(q), "results": [
            {"id": h.passage.id, "ref": h.passage.ref, "kind": h.passage.kind, "score": round(h.score, 3),
             "text": h.passage.text} for h in hits]}

    @app.post("/api/ask")
    async def api_ask(request: Request):
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
        refusal = "" if ans is not None else ask_refusal(request)
        if refusal:
            raise HTTPException(429, refusal)
        try:
            # The Claude call blocks for seconds; keep it off the event loop.
            if ans is None:
                ans = await run_in_threadpool(answer, request, question, k, works or None, primary_only)
        except Exception as exc:  # missing API key, network, rate limit...
            raise HTTPException(503, f"Answering failed: {exc}") from None
        return {"answer": ans.text, "abstained": ans.abstained, "refused": ans.refused,
                "truncated": ans.truncated, "model": ans.model, "cached": ans.cached,
                "citations": [c.__dict__ for c in ans.citations],
                "retrieved": [h.passage.id for h in ans.hits]}

    @app.get("/api/graph")
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

    @app.get("/api/entity/{entity_id}")
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
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(request, "error.html", {
            "status": exc.status_code, "detail": exc.detail, "is_admin": is_admin(request),
            "corpus_ready": state["runtime"] is not None, "corpus_error": state["error"]},
            status_code=exc.status_code)

    app.state.job = job
    app.state.reload_runtime = reload_runtime
    app.state.sessions = sessions
    app.state.ask_limiter = ask_limiter
    return app


def entity_info(rt: Runtime, entity_id: str, passages: int) -> dict:
    e = rt.gazetteer.entities[entity_id]
    node = rt.graph.nodes[entity_id] if entity_id in rt.graph else {}
    works = {}
    for part in str(node.get("works", "")).split(";"):
        if part:
            w, _, c = part.partition(":")
            works[w] = int(c)
    renders = {}
    for m in rt.mentions:
        if m.entity == entity_id:
            renders[(m.surface.lower(), m.via)] = renders.get((m.surface.lower(), m.via), 0) + 1
    return {
        "id": e.id, "label": e.label, "type": e.type, "iast": e.iast,
        "aliases": e.aliases + e.aliases_cs, "renderings": e.renderings,
        "mentions": int(node.get("mentions", 0)), "works": works,
        "surface_forms": [{"surface": s, "via": v, "count": c}
                          for (s, v), c in sorted(renders.items(), key=lambda x: -x[1])[:15]],
        "neighbours": [{"id": nb, "label": rt.gazetteer.entities[nb].label, "weight": int(w)}
                       for nb, w in neighbours(rt.graph, entity_id, 12)],
        "passages": [{"id": p.id, "ref": p.ref, "kind": p.kind, "snippet": snippet(p.text, 200)}
                     for p in rt.passages_for_entity(entity_id, passages)],
    }
