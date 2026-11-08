"""The MCP server (docs/08 B1), through an in-process MCP client, and its HTTP bearer guard.
No network, no model: `ikg_ask` gets a fake answerer. Skipped where the MCP SDK isn't installed
(it needs Python 3.11+)."""

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402
from test_embed import _build, corpus  # noqa: E402,F401  (corpus is a fixture)

import ikg  # noqa: E402
from ikg import cli, config, qa, sparql  # noqa: E402
from ikg.limits import RateLimiter, SpendBudget  # noqa: E402
from ikg.mcp_server import build_server, http_app  # noqa: E402
from ikg.pipeline import Runtime  # noqa: E402

TOOLS = {"ikg_search_passages", "ikg_get_passage", "ikg_get_entity", "ikg_sparql_query", "ikg_ask"}


def call(server, tool, args):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args)
    return asyncio.run(go())


@pytest.fixture
def server(corpus, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "MCP_LOG_FILE", config.MANIFEST_FILE.with_name("mcp-calls.jsonl"))
    _build(corpus)
    return build_server(Runtime.load(), caller="test")


def test_tools_describe_themselves(server):
    async def go():
        async with Client(server) as c:
            return (await c.list_tools()).tools, (await c.read_resource("ikg://ontology")).contents[0].text
    tools, ontology = asyncio.run(go())
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.description and len(t.description) > 80, t.name  # the calling model only sees this
        assert t.output_schema, t.name
        assert t.annotations.read_only_hint and not t.annotations.destructive_hint
    search = next(t for t in tools if t.name == "ikg_search_passages")
    assert "commentary" in search.description and "primary" in search.description
    assert "once per work" in search.description  # clients without the skill still see this rule
    from ikg.mcp_server import INSTRUCTIONS
    assert "passage_id" in INSTRUCTIONS and "once per work" in INSTRUCTIONS
    assert "ikg:Concept" in ontology


def test_search_keeps_kinds_and_filters(server):
    r = call(server, "ikg_search_passages", {"query": "Krishna dharma Self", "k": 5})
    s = r.structured_content
    assert not r.is_error and s["retriever"] == "bm25" and s["results"]
    assert {"passage_id", "ref", "work", "edition", "kind", "translator", "text"} <= s["results"][0].keys()
    r = call(server, "ikg_search_passages", {"query": "Sita Rama forest", "k": 5, "primary_only": True,
                                             "works": ["ramayana"]})
    assert all(h["work"] == "ramayana" and h["kind"] in ("verse", "narrative") for h in r.structured_content["results"])
    assert call(server, "ikg_search_passages", {"query": "x", "k": 99}).is_error
    r = call(server, "ikg_search_passages", {"query": "x", "mode": "hybrid"})
    assert r.is_error and "embeddings" in r.content[0].text


def test_get_passage_with_neighbours(server):
    s = call(server, "ikg_get_passage", {"passage_id": "mbh.b01.s001.p02", "neighbours": 1}).structured_content
    assert s["previous_id"] == "mbh.b01.s001.p01" and [b["passage_id"] for b in s["before"]] == ["mbh.b01.s001.p01"]
    assert s["after"] == [] and s["source_url"] and "dharma" in s["entities"]
    assert call(server, "ikg_get_passage", {"passage_id": "nope"}).is_error


def test_get_entity_by_alias(server):
    s = call(server, "ikg_get_entity", {"entity_id_or_name": "Kesava"}).structured_content
    assert s["id"] == "krishna" and "kesava" in s["aliases"] and s["passages"]
    assert call(server, "ikg_get_entity", {"entity_id_or_name": "Zeus"}).is_error


@pytest.mark.parametrize("query, message", [
    ("INSERT DATA { <a> <b> <c> }", "not a SPARQL SELECT or ASK"),
    ("DELETE WHERE { ?s ?p ?o }", "not a SPARQL SELECT or ASK"),
    ("LOAD <http://example.org/x.ttl>", "not a SPARQL SELECT or ASK"),
    ("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", "only SELECT and ASK"),
    ("SELECT * WHERE { SERVICE <http://example.org/sparql> { ?s ?p ?o } }", "SERVICE"),
])
def test_sparql_refuses_anything_but_local_reads(server, query, message):
    r = call(server, "ikg_sparql_query", {"query": query})
    assert r.is_error and message in r.content[0].text


def test_sparql_select_ask_row_cap_and_timeout(server, monkeypatch):
    s = call(server, "ikg_sparql_query", {"query": "SELECT ?label WHERE { ?e a ikg:Concept ; rdfs:label ?label }"})
    assert s.structured_content["columns"] == ["label"] and ["Dharma"] in s.structured_content["rows"]
    assert call(server, "ikg_sparql_query", {"query": "ASK { ?e rdfs:label \"Dharma\" }"}).structured_content["boolean"]
    capped = sparql.run("SELECT ?s WHERE { ?s ?p ?o }", config.RDF_FILE, max_rows=2)
    assert len(capped["rows"]) == 2 and capped["truncated"]
    with pytest.raises(sparql.SparqlError, match="longer than"):
        sparql.run("SELECT ?s WHERE { ?s ?p ?o }", config.RDF_FILE, timeout=0.001)


def test_ask_is_cited_budgeted_limited_and_logged(corpus, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "MCP_LOG_FILE", config.MANIFEST_FILE.with_name("mcp-calls.jsonl"))
    _build(corpus)
    seen = []

    def fake_ask(question, index, gazetteer, **kwargs):
        seen.append(kwargs)
        return qa.Answer(question=question, text="Krishna speaks of dharma.[1]", model="fake", cost_usd=0.6,
                         citations=[qa.Citation("mbh.b01.s001.p02", "Mahabharata 1.1", "dharma")])
    budget, limiter = SpendBudget(1.0), RateLimiter(5)
    server = build_server(Runtime.load(), caller="test", budget=budget, limiter=limiter, ask=fake_ask)
    s = call(server, "ikg_ask", {"question": "What is dharma?", "primary_only": True}).structured_content
    assert s["citations"] == [{"n": 1, "passage_id": "mbh.b01.s001.p02", "ref": "Mahabharata 1.1", "cited_text": "dharma"}]
    assert s["cost_usd"] == 0.6 and s["retriever"] == "bm25" and seen[0]["primary_only"] is True
    call(server, "ikg_ask", {"question": "And karma?"})  # 1.2 spent: over the budget
    r = call(server, "ikg_ask", {"question": "And yoga?"})
    assert r.is_error and "budget" in r.content[0].text
    log = [json.loads(line) for line in config.MCP_LOG_FILE.read_text(encoding="utf-8").splitlines()]
    assert [(e["tool"], e["caller"], e["cost_usd"], e["ok"]) for e in log] == [
        ("ikg_ask", "test", 0.6, True), ("ikg_ask", "test", 0.6, True), ("ikg_ask", "test", None, False)]
    limited = build_server(Runtime.load(), caller="test", budget=SpendBudget(100), limiter=RateLimiter(1), ask=fake_ask)
    call(limited, "ikg_ask", {"question": "one"})
    r = call(limited, "ikg_ask", {"question": "two"})
    assert r.is_error and "limit" in r.content[0].text


def test_http_requires_the_bearer_token(server):
    from starlette.testclient import TestClient
    token = "t" * 32
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(http_app(server, token), base_url="http://127.0.0.1:8001") as client:  # Host is checked
        assert client.post("/mcp", json=init, headers=headers).status_code == 401
        r = client.post("/mcp", json=init, headers={**headers, "Authorization": "Bearer wrong"})
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        r = client.post("/mcp", json=init, headers={**headers, "Authorization": f"Bearer {token}"})
        info = r.json()["result"]["serverInfo"]
        assert r.status_code == 200 and info["name"] == "ikg_mcp" and info["version"] == ikg.__version__
        # No OAuth: discovery and registration are 404, so a client asks for the token instead
        for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-authorization-server",
                     "/register"):
            assert client.get(path).status_code == 404 and client.post(path, json={}).status_code == 404
    with pytest.raises(ValueError, match="16 characters"):
        http_app(server, "short")


def test_cli_http_needs_a_token(server, monkeypatch):
    monkeypatch.delenv("IKG_MCP_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="IKG_MCP_TOKEN"):
        cli.main(["mcp", "--http"])


def test_sparql_passes_non_ascii_text_intact(server):
    r = call(server, "ikg_sparql_query", {"query": 'SELECT (STRLEN("K\u1e5b\u1e63\u1e47a") AS ?n) WHERE {}'})
    assert r.structured_content["rows"] == [["5"]]  # 5 characters, not the 11 UTF-8 bytes


@pytest.mark.parametrize("error, billed", [
    (ValueError("bad effort"), False),
    (RuntimeError("server error"), True),
])
def test_ask_failures_count_against_the_budget_only_if_they_may_have_cost(corpus, monkeypatch, error, billed):  # noqa: F811
    from ikg.localqa import LocalAnswerError
    monkeypatch.setattr(config, "MCP_LOG_FILE", config.MANIFEST_FILE.with_name("mcp-calls.jsonl"))
    _build(corpus)

    def failing(*args, **kwargs):
        raise error
    budget = SpendBudget(5.0)
    server = build_server(Runtime.load(), caller="test", budget=budget, limiter=RateLimiter(10), ask=failing)
    r = call(server, "ikg_ask", {"question": "What is dharma?"})
    assert r.is_error and "bad effort" not in r.content[0].text and "server error" not in r.content[0].text
    assert budget.spent == (SpendBudget.UNKNOWN_COST if billed else 0.0)
    assert not qa.may_have_been_billed(LocalAnswerError("ollama down"))


def test_ask_falls_back_to_bm25_when_dense_search_fails(corpus, monkeypatch):  # noqa: F811
    from ikg.vectorstore import VectorStoreError
    monkeypatch.setattr(config, "MCP_LOG_FILE", config.MANIFEST_FILE.with_name("mcp-calls.jsonl"))
    _build(corpus)
    modes = []

    def ask(question, index, gazetteer, retrieval=None, **kwargs):
        modes.append(retrieval["mode"])
        if retrieval["mode"] != "bm25":
            raise VectorStoreError("cannot use Postgres at postgresql://ikg:***@db/ikg")
        return qa.Answer(question=question, text="NOT_IN_SOURCES", abstained=True, model="fake", cost_usd=0.0)
    rt = Runtime.load()
    monkeypatch.setattr(rt, "retrieval", lambda mode=None: {"mode": "hybrid", "dense": lambda *a: []})
    budget = SpendBudget(5.0)
    s = call(build_server(rt, caller="test", budget=budget, ask=ask), "ikg_ask", {"question": "x"}).structured_content
    assert modes == ["hybrid", "bm25"] and s["retriever"] == "bm25" and budget.spent == 0.0


def test_bearer_scheme_is_case_insensitive_and_calls_are_logged_with_transport(server):
    from starlette.testclient import TestClient
    token = "t" * 32
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(http_app(server, token), base_url="http://127.0.0.1:8001") as client:
        assert client.post("/mcp", json=init, headers={**headers, "Authorization": f"bearer {token}"}).status_code == 200
        assert client.get("/mcp", headers={"Authorization": f"Basic {token}"}).status_code == 401
    call(server, "ikg_get_entity", {"entity_id_or_name": "Kesava"})
    entry = json.loads(config.MCP_LOG_FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["tool"] == "ikg_get_entity" and entry["caller"] == "test" and "transport" in entry
