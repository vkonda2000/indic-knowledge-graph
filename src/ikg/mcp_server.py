"""The IKG as an MCP server (docs/08 B1): search, passages, entities, SPARQL and cited answers as tools.

`ikg mcp` serves over stdio (for a local client such as Claude Desktop or Claude Code); `ikg mcp --http`
serves Streamable HTTP at /mcp and requires `Authorization: Bearer $IKG_MCP_TOKEN`, unless
IKG_MCP_PUBLIC=on: then it needs no token and leaves out `ikg_ask`, the one tool that costs money, so the
public, read-only tools can be added to a client that can't send a static token (a ChatGPT custom MCP
server with "No authentication"; claude.ai's custom connectors).

Every tool keeps IKG's distinctions: results carry each passage's work, edition, translator and kind
(verse / narrative are the primary text; commentary, introduction and secondary are not), and
`ikg_ask` may answer NOT_IN_SOURCES. Tool descriptions say so, because the calling model only sees them.

Limits: `ikg_ask` is the only tool that can cost money. It has a daily budget (IKG_ASK_BUDGET_USD) and
IKG_ASK_LIMIT questions an hour, both per server process: with one static token every HTTP client shares
them, and IKG_ASK_LIMIT=0 turns ikg_ask off. Per-client limits come with OAuth (docs/08 B2-B4).
`ikg_sparql_query` accepts SELECT and ASK only, without SERVICE, runs in a child process with a time
limit, and returns at most SPARQL_MAX_ROWS rows. Every call is appended to the MCP call log
(IKG_DATA/mcp-calls.jsonl) with the caller, the tool, a short summary and the cost.

Needs the `[mcp]` extra (the MCP Python SDK, Python 3.11+).
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ikg import __version__, config, sparql
from ikg.describe import entity_info
from ikg.limits import RateLimiter, SpendBudget
from ikg.pipeline import Runtime
from ikg.retrieve import search
from ikg.segment import PRIMARY_KINDS

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_types import ToolAnnotations
except ImportError as exc:  # the [mcp] extra is optional
    raise ImportError('the MCP server needs the MCP SDK: pip install -e ".[mcp]" (Python 3.11+)') from exc

SEARCH_MAX_K = 20
# The ontology is in the repository, not the package; the Docker image copies it and sets IKG_ONTOLOGY.
ONTOLOGY_FILE = Path(os.environ.get("IKG_ONTOLOGY", Path(__file__).resolve().parents[2] / "ontology" / "ikg.ttl"))

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
KINDS_NOTE = ("Each passage has a kind: 'verse' and 'narrative' are the translated primary text; 'commentary' "
              "and 'introduction' are the translator's own explanation; 'secondary' is modern scholarship. Never "
              "present commentary or scholarship as what the text itself says.")

INSTRUCTIONS = f"""Indic Knowledge Graph: the Upanishads, Mahabharata, Ramayana and Vishnu Purana in public-domain
English translations, with a knowledge graph of people, places and concepts.

Cite every claim with the passage_id (e.g. mbh.b03.s108.p02) and the ref it comes from, and name the
translator: the id lets a reader open the exact passage. {KINDS_NOTE}
A translator's English word is not the Sanskrit concept: "righteousness" is Ganguli's rendering of dharma.
Keep different works and speakers separate. Many stories are told in more than one work (the descent of
the Ganga, Parashurama, Rama, Savitri): unless the user names a work, don't restrict `works` to one
guess; search without it, or once per work, and report each telling under its own work.
If the passages don't support an answer, say so.
Start with ikg_search_passages; read context with ikg_get_passage; ikg_ask writes a cited answer."""


# ---------- outputs ----------

class PassageHit(BaseModel):
    passage_id: str
    ref: str = Field(description="Human-readable reference: work, book, section or verse, translator")
    work: str
    edition: str
    kind: str = Field(description="verse | narrative (primary text) | commentary | introduction | secondary")
    translator: str
    score: float = Field(description="Ranking score; its scale depends on the retriever")
    text: str


class SearchResult(BaseModel):
    query: str
    retriever: str = Field(description="bm25, dense or hybrid: how the passages were found")
    entities: list[str] = Field(description="Gazetteer entities recognised in the query (they widen the search)")
    results: list[PassageHit]


class Passage(BaseModel):
    passage_id: str
    ref: str
    work: str
    edition: str
    kind: str
    translator: str
    text: str
    source_url: str
    entities: list[str] = Field(description="Entity ids mentioned in the passage")
    previous_id: str | None
    next_id: str | None
    before: list[PassageHit] = Field(description="Up to `neighbours` preceding passages, nearest last")
    after: list[PassageHit] = Field(description="Up to `neighbours` following passages, nearest first")


class Rendering(BaseModel):
    surface: str = Field(description="The word as it appears in the text")
    via: str = Field(description="How it matched: alias, rendering (a translator's English word), ...")
    count: int


class Connection(BaseModel):
    id: str
    label: str
    weight: int = Field(description="Passages in which both entities appear")


class EntityResult(BaseModel):
    id: str
    label: str
    type: str
    iast: str = ""
    aliases: list[str]
    renderings: list[str] = Field(description="English words translators use for this concept (not the concept)")
    mentions: int
    works: dict[str, int] = Field(description="Mentions per work")
    surface_forms: list[Rendering]
    connections: list[Connection] = Field(description="Strongest co-occurrence links (appear together, not a relation)")
    passages: list[str] = Field(description="Passage ids that mention it, first ones in corpus order")


class SparqlResult(BaseModel):
    columns: list[str]
    rows: list[list[str | None]]
    boolean: bool | None = Field(default=None, description="The answer of an ASK query")
    truncated: bool


class Citation(BaseModel):
    n: int
    passage_id: str
    ref: str
    cited_text: str


class AskResult(BaseModel):
    text: str = Field(description="The answer with [n] markers and a Sources list, or NOT_IN_SOURCES")
    abstained: bool
    refused: bool
    citations: list[Citation]
    model: str
    retriever: str
    cost_usd: float | None = Field(description="Estimated API cost; null if unknown; 0 for a local model")


# ---------- helpers ----------

def _hit(p, score: float) -> PassageHit:
    return PassageHit(passage_id=p.id, ref=p.ref, work=p.work, edition=p.edition, kind=p.kind,
                      translator=p.translator, score=round(float(score), 4), text=p.text)


class CallLog:
    """Append-only JSONL log of MCP calls: who called which tool, what for, at what cost."""

    def __init__(self, path: Path):
        self.path, self.lock = path, threading.Lock()

    def write(self, caller: str, tool: str, summary: str, cost_usd: float | None = None, ok: bool = True,
              transport: str = "") -> None:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "transport": transport,
                 "caller": caller, "tool": tool, "summary": summary[:200], "cost_usd": cost_usd, "ok": ok}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------- server ----------

def build_server(runtime: Runtime, caller: str = "stdio", budget: SpendBudget | None = None,
                 limiter: RateLimiter | None = None, ask=None, transport: str = "",
                 with_ask: bool = True) -> MCPServer:
    """An MCP server over one loaded build. `caller` identifies who is calling (today "local" over stdio
    or "token" for the shared HTTP token; a client identity once OAuth arrives), `transport` how.
    `ask` defaults to qa.ask (tests pass a fake). `with_ask=False` leaves the ikg_ask tool out."""
    budget = budget or SpendBudget(config.ASK_BUDGET_USD)
    limiter = limiter or RateLimiter(int(os.environ.get("IKG_ASK_LIMIT", "20")))
    log = CallLog(config.MCP_LOG_FILE)
    rt = runtime
    instructions = INSTRUCTIONS if with_ask else INSTRUCTIONS.replace("; ikg_ask writes a cited answer", "")
    server = MCPServer("ikg_mcp", title="Indic Knowledge Graph", version=__version__, instructions=instructions,
                       description="Cited search and answers over the Upanishads, Mahabharata, Ramayana and "
                                   "Vishnu Purana in public-domain English translations")

    def logged(tool: str, summary: str):
        def done(cost: float | None = None, ok: bool = True):
            log.write(caller, tool, summary, cost, ok, transport or caller)
        return done

    @server.tool(name="ikg_search_passages", title="Search passages", annotations=READ_ONLY, description=(
        "Find passages for a question or topic, ranked. Use before answering anything about these texts.\n"
        "query: a question or keywords; Sanskrit names and terms are understood "
        "(e.g. \"What does Krishna say about dharma?\").\n"
        f"k: number of passages, 1-{SEARCH_MAX_K}.\n"
        "works: limit to these work ids: upanishads, mahabharata, ramayana, vishnu_purana. Only when the user "
        "names a work: many stories are told in several, so otherwise search all, or once per work.\n"
        "primary_only: only the translated primary text (verse, narrative), no commentary or scholarship.\n"
        "mode: retrieval method; 'default' is the server's (hybrid when available).\n" + KINDS_NOTE))
    def search_passages(query: str, k: int = 8, works: list[str] | None = None, primary_only: bool = False,
                        mode: Literal["default", "bm25", "dense", "hybrid"] = "default") -> SearchResult:
        done = logged("ikg_search_passages", query)
        if not 1 <= k <= SEARCH_MAX_K:
            done(ok=False)
            raise ToolError(f"k must be between 1 and {SEARCH_MAX_K}")
        retrieval = rt.retrieval(None if mode == "default" else mode)
        if retrieval["mode"] != "bm25" and retrieval.get("dense") is None:
            done(ok=False)
            raise ToolError(f"{mode} retrieval needs a build with embeddings; use mode='bm25'")
        try:
            hits = search(rt.index, rt.gazetteer, query, k=k, works=works or None, primary_only=primary_only,
                          passage_entities=rt.passage_entities, **retrieval)
        except Exception as exc:  # dense search unavailable: say so, without internal details
            done(ok=False)
            raise ToolError(f"search failed ({type(exc).__name__}); retry with mode='bm25'") from None
        done()
        return SearchResult(query=query, retriever=retrieval["mode"], entities=rt.gazetteer.entities_in(query),
                            results=[_hit(h.passage, h.score) for h in hits])

    @server.tool(name="ikg_get_passage", title="Get a passage", annotations=READ_ONLY)
    def get_passage(passage_id: str, neighbours: int = 0) -> Passage:
        """Read one passage by id, with the entities it mentions and its source URL. `neighbours` (0-2) adds
        that many passages before and after it from the same edition, for context."""
        done = logged("ikg_get_passage", passage_id)
        p = rt.passages.get(passage_id)
        if p is None or not 0 <= neighbours <= 2:
            done(ok=False)
            raise ToolError(f"unknown passage id {passage_id!r}" if p is None else "neighbours must be 0, 1 or 2")
        before, after = [], []
        cur = passage_id
        for _ in range(neighbours):
            prev, _nxt = rt.neighbour_passages(cur)
            if prev is None:
                break
            before.insert(0, _hit(prev, 0))
            cur = prev.id
        cur = passage_id
        for _ in range(neighbours):
            _prev, nxt = rt.neighbour_passages(cur)
            if nxt is None:
                break
            after.append(_hit(nxt, 0))
            cur = nxt.id
        prev, nxt = rt.neighbour_passages(passage_id)
        done()
        return Passage(passage_id=p.id, ref=p.ref, work=p.work, edition=p.edition, kind=p.kind,
                       translator=p.translator, text=p.text, source_url=p.source_url,
                       entities=sorted(rt.passage_entities.get(p.id, set())),
                       previous_id=prev.id if prev else None, next_id=nxt.id if nxt else None,
                       before=before, after=after)

    @server.tool(name="ikg_get_entity", title="Get an entity", annotations=READ_ONLY)
    def get_entity(entity_id_or_name: str) -> EntityResult:
        """Look up a person, deity, place, concept or school of thought by id ("krishna") or by any name or
        alias ("Partha", "Kesava", "atman"). Returns aliases, the English renderings translators use for it,
        mentions per work, its strongest co-occurrence links, and passages that mention it. Co-occurrence
        means the two appear in the same passages, not that the texts state a relation between them."""
        done = logged("ikg_get_entity", entity_id_or_name)
        key = entity_id_or_name.strip()
        entity_id = key if key in rt.gazetteer.entities else next(iter(rt.gazetteer.entities_in(key)), None)
        if entity_id is None:
            done(ok=False)
            raise ToolError(f"no entity matches {entity_id_or_name!r}; try ikg_search_passages")
        info = entity_info(rt, entity_id, 20)
        done()
        return EntityResult(id=info["id"], label=info["label"], type=info["type"], iast=info.get("iast") or "",
                            aliases=info["aliases"], renderings=info["renderings"], mentions=info["mentions"],
                            works=info["works"], surface_forms=[Rendering(**s) for s in info["surface_forms"]],
                            connections=[Connection(**n) for n in info["neighbours"]],
                            passages=[p["id"] for p in info["passages"]])

    @server.tool(name="ikg_sparql_query", title="SPARQL query (read-only)", annotations=READ_ONLY, description=(
        "Run a read-only SPARQL SELECT or ASK query over the knowledge graph (RDF). Read the resource "
        "ikg://ontology first for its classes and properties. Prefixes ikg:, rdf: and rdfs: are added if the "
        f"query declares none. SERVICE and any update are refused; at most {sparql.MAX_ROWS} rows; slow queries "
        "are stopped. Example: SELECT ?label WHERE { ?e a ikg:Concept ; rdfs:label ?label }"))
    def sparql_query(query: str) -> SparqlResult:
        done = logged("ikg_sparql_query", " ".join(query.split()))
        try:
            result = sparql.run(query, config.RDF_FILE)
        except sparql.SparqlError as exc:
            done(ok=False)
            raise ToolError(str(exc)) from None
        done()
        return SparqlResult(**result)

    @server.tool(name="ikg_ask", title="Ask (cited answer)",
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False,
                                             openWorldHint=True))
    def ask_tool(question: str, works: list[str] | None = None, primary_only: bool = False) -> AskResult:
        """Answer a question from the passages only, with numbered citations to passage ids. May answer
        NOT_IN_SOURCES when the texts don't support an answer; report that rather than answering from your
        own knowledge. Calls a language model, so it is rate-limited and budgeted; prefer
        ikg_search_passages when you can write the cited answer yourself."""
        done = logged("ikg_ask", question)
        if not budget.available():
            done(ok=False)
            raise ToolError("the daily budget for cited answers is used up; use ikg_search_passages")
        if not limiter.allow(caller):
            done(ok=False)
            raise ToolError("question limit reached for this hour; use ikg_search_passages")
        from ikg.embed import EmbedError
        from ikg.qa import ask as qa_ask
        from ikg.qa import may_have_been_billed
        from ikg.vectorstore import VectorStoreError
        answer = (ask or qa_ask)
        retrieval = rt.retrieval()

        def run(retrieval):
            return answer(question, rt.index, rt.gazetteer, graph=rt.graph, passage_entities=rt.passage_entities,
                          works=works or None, primary_only=primary_only, retrieval=retrieval)
        try:
            try:
                ans = run(retrieval)
            except (EmbedError, VectorStoreError) as exc:  # before any model call: answer from BM25 instead
                rt.dense_error = str(exc)
                retrieval = {"mode": "bm25"}
                ans = run(retrieval)
        except Exception as exc:
            if may_have_been_billed(exc):
                budget.charge(None)
            done(ok=False)
            raise ToolError(f"answering failed ({type(exc).__name__}); use ikg_search_passages") from None
        budget.charge(ans.cost_usd)
        done(ans.cost_usd)
        return AskResult(text=ans.text, abstained=ans.abstained, refused=ans.refused, model=ans.model,
                         retriever=retrieval["mode"], cost_usd=ans.cost_usd,
                         citations=[Citation(n=i, passage_id=c.passage_id, ref=c.ref, cited_text=c.cited_text)
                                    for i, c in enumerate(ans.citations, 1)])

    @server.resource("ikg://ontology", name="ontology", title="IKG ontology (Turtle)", mime_type="text/turtle")
    def ontology() -> str:
        """The OWL ontology: classes and properties to write SPARQL against."""
        if not ONTOLOGY_FILE.exists():
            return "# The ontology file is not available in this installation (ontology/ikg.ttl)."
        return ONTOLOGY_FILE.read_text(encoding="utf-8")

    if not with_ask:
        server.remove_tool("ikg_ask")
    return server


# ---------- HTTP with a bearer token ----------

def http_app(server: MCPServer, token: str | None, host: str = "127.0.0.1"):
    """The Streamable HTTP app at /mcp, refusing any HTTP request without `Authorization: Bearer <token>`.
    `token=None` serves without a token (IKG_MCP_PUBLIC: only for a server built `with_ask=False`)."""
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import JSONResponse

    if token is not None and len(token) < 16:
        raise ValueError("IKG_MCP_TOKEN must be at least 16 characters")
    security = None
    # Bound to localhost, the SDK checks the Host header against DNS rebinding. Bound to all interfaces (a
    # container, or behind a reverse proxy, which sends its public Host), the token is the guard.
    if host not in ("127.0.0.1", "localhost", "::1"):
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    inner = server.streamable_http_app(stateless_http=True, json_response=True, host=host,
                                       transport_security=security)
    expected = token.encode() if token is not None else None
    refused = JSONResponse({"error": "unauthorized", "detail": "send Authorization: Bearer <IKG_MCP_TOKEN>"},
                           status_code=401, headers={"WWW-Authenticate": "Bearer"})

    # No OAuth here: answer a client's OAuth discovery and registration with 404, not the token's 401, so it
    # asks for the bearer token instead of starting an OAuth flow that can't succeed.
    no_oauth = JSONResponse({"error": "not_found", "detail": "this server has no OAuth; send Authorization: "
                             "Bearer <IKG_MCP_TOKEN>"}, status_code=404)

    async def app(scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith("/.well-known/oauth-") or path in ("/register", "/authorize", "/token"):
                await no_oauth(scope, receive, send)
                return
            if expected is None:  # public: no token, but still no OAuth paths
                await inner(scope, receive, send)
                return
            scheme, _, given = dict(scope.get("headers") or []).get(b"authorization", b"").partition(b" ")
            if scheme.lower() != b"bearer" or not hmac.compare_digest(given.strip(), expected):
                await refused(scope, receive, send)
                return
        await inner(scope, receive, send)  # lifespan and authorised requests
    return app


def serve(http: bool = False, host: str = "127.0.0.1", port: int = 8001) -> None:
    runtime = Runtime.load()
    if runtime.embeddings is not None and config.RETRIEVER != "bm25":
        runtime.prepare_dense()
    if not http:
        build_server(runtime, caller="local", transport="stdio").run("stdio")
        return
    import uvicorn
    if os.environ.get("IKG_MCP_PUBLIC", "").strip().lower() in ("on", "1", "true", "yes"):
        print("IKG_MCP_PUBLIC is on: serving without a token, read-only tools only (no ikg_ask)", file=sys.stderr)
        uvicorn.run(http_app(build_server(runtime, caller="public", transport="http", with_ask=False), None, host),
                    host=host, port=port)
        return
    token = os.environ.get("IKG_MCP_TOKEN", "")
    if not token:
        raise SystemExit("ikg mcp --http needs IKG_MCP_TOKEN (a long random string clients send as a Bearer "
                         "token), or IKG_MCP_PUBLIC=on for read-only tools without a token")
    uvicorn.run(http_app(build_server(runtime, caller="token", transport="http"), token, host),
                host=host, port=port)

