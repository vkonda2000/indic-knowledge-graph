# 05 Interfaces

## 1. Web pages

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/` | public | Search. Query params: `q`, `mode` (`search`\|`ask`), `works` (repeatable), `primary_only`, `k` (search ≤ 50, ask ≤ 20). With `mode=ask` it only fills in the form; it never calls the API |
| POST | `/` | public; Ask is budget- and rate-limited | The search form. Same fields as form data. `mode=ask` gives a cited answer (from the answer cache when possible) |
| GET | `/graph` | public | Graph explorer |
| GET | `/entity/{id}` | public | Entity profile; 404 if the ID is unknown |
| GET | `/passage/{id}` | public | Passage with entities and previous/next; 404 if the ID is unknown |
| GET, POST | `/contribute` | public, or token if `IKG_CONTRIBUTOR_TOKEN` is set | Upload form. Form fields: `title`*, `author`, `year`, `kind`, `contributor`, `notes`, `token`, `file`* |
| GET, POST | `/admin/login` | public | Token login; sets the `ikg_admin` cookie and redirects to `/admin` |
| POST | `/admin/logout` | admin | Ends the session |
| GET | `/admin` | admin | Review queue, works, gazetteer editor, rebuild |
| POST | `/admin/gutenberg` | admin | `gutenberg_id`*, `title`*, `translator`, `year`, `kind`, `parser`; added as approved, downloaded on the next rebuild |
| POST | `/admin/upload` | admin | Like `/contribute` but approved immediately (still screened) |
| POST | `/admin/works/{id}/status` | admin | `status` = `pending`\|`approved`\|`rejected` (user-added works only) |
| POST | `/admin/gazetteer` | admin | `yaml_text`, `action` = `save`\|`reset`; validated before saving |
| POST | `/admin/rebuild` | admin | Starts a background rebuild (one at a time) |

\* required. Admin routes redirect to `/admin/login` (303) without a valid session.

## 2. JSON API

| Method | Path | Access | Request | Response |
|---|---|---|---|---|
| GET | `/api/search` | public | `q`*, `k` (1–50, default 8), `works` (repeatable), `primary_only` | `{query, entities: [id], results: [{id, ref, kind, score, text}]}` |
| POST | `/api/ask` | public; budget- and rate-limited; cache hits are free | `{"question"*: str, "k": int ≤ 20, "works": [str], "primary_only": bool}` (`primary_only` must be a JSON boolean) | `{answer, abstained, refused, truncated, model, cached, citations: [{passage_id, ref, cited_text}], retrieved: [id]}`; 429 when the daily budget or the hourly limit is reached, or for a browser request from another site |
| GET | `/api/passage/{id}` | public | none | `{id, ref, work, work_title, edition, kind, translator, text, source_url, previous_id, next_id, entities}`; 404 for an unknown id |
| GET | `/openapi.json` | public | none | OpenAPI document of the `/api/*` routes (operation ids `searchPassages`, `getPassage`, `askQuestion`, `getGraph`, `getEntity`) with `servers` from `IKG_PUBLIC_URL`, for GPT Actions |
| GET | `/api/graph` | public | `work`, `types` (comma-separated), `min_weight` (default 5), `max_nodes` (≤ 300, default 80) | Cytoscape elements: `{nodes: [{data: {id, label, type, mentions}}], edges: [{data: {id, source, target, weight}}]}` |
| GET | `/api/entity/{id}` | public | none | `{id, label, type, iast, aliases, renderings, mentions, works, surface_forms, neighbours, passages}` (10 passages) |
| GET | `/api/build/status` | admin | none | `{state: idle\|running\|done\|error, log: [str], stats, error, started, finished}` |

**Errors** are `{"detail": "..."}` with these status codes:

| Status | When |
|---|---|
| 400 | Malformed `/api/ask` body or a non-integer `k` |
| 401 | An admin `/api/` route without a valid admin session (HTML admin pages redirect to the login page instead) |
| 404 | Unknown entity or passage |
| 422 | FastAPI parameter validation failed |
| 413 | Upload larger than 20 MB (refused before the body is read) |
| 429 | Ask limit or upload limit reached |
| 503 | Corpus not built, or the Claude call failed |

### Example

```bash
curl -s "http://127.0.0.1:8000/api/search?q=Nachiketas&k=1"
curl -s -X POST http://127.0.0.1:8000/api/ask -H 'content-type: application/json' \
     -d '{"question": "What does Yama teach Nachiketas about the Self?", "works": ["upanishads"]}'
```

## 3. Command line (`ikg`)

| Command | Purpose | Key options | Needs |
|---|---|---|---|
| `ikg fetch` | Download registered Gutenberg texts and verify pinned checksums | `--works ID...`, `--force` | network. If a pin fails because Gutenberg re-issued a text, take the pinned copies with `gh release download pinned-sources-v1 --dir data/raw --pattern 'pg*.txt' --clobber` |
| `ikg build` | Segment, extract, index, graph, RDF; with `--embed`, passage embeddings | `--works ID...`, `--with-passages` (passages in RDF), `--embed` / `--no-embed` (overrides `IKG_EMBED`) | raw texts; `--embed` needs the `[embed]` extra and, the first time, a network to download the model |
| `ikg stats` | Passages per work and kind | none | built corpus |
| `ikg search "Q"` | Ranked passages, no API call | `-k`, `--works`, `--primary-only`, `--mode bm25\|dense\|hybrid` | built corpus; dense and hybrid need embeddings |
| `ikg ask "Q"` | Cited answer, then the model, tokens and estimated cost | `-k`, `--works`, `--primary-only`, `--show-retrieval`, `--mode`, `--model`, `--effort` | `ANTHROPIC_API_KEY`, or Ollama with `--model ollama:<model>` |
| `ikg analyze` | Write `reports/corpus-analysis.md` | none | built corpus |
| `ikg eval` | Retrieval metrics; failures listed as `MISS` | `-k`, `--llm`, `--no-expand`, `--retriever bm25\|dense\|hybrid\|all` (`all`: a side-by-side table per question type, each run saved; not with `--llm` or `--check`), `--model`, `--effort` (with `--llm`), `--check BASELINE` (exit 1 if any baseline metric got worse, or the baseline was recorded with another retriever; used by CI) | built corpus (+ API key with `--llm`) |
| `ikg eval --fidelity` | SOT-fidelity benchmark: pass/fail per item and check, rates per property | `--baseline` (plain model, no retrieval), `--include-drafts`, `--judge-model`, `--model`, `--effort`, `-k`; not combinable with `--check`, `--llm`, `--no-expand` | API key; built corpus unless `--baseline`; at least one approved item |
| `ikg vectors compare` | Recall@k of a vector store against exact search, and both latencies, over sampled passage vectors, unfiltered, primary only, and one work | `--store` (a `postgresql://` URL or `file`; default `IKG_VECTOR_STORE`), `-n` (200), `-k` (8) | a build with embeddings; for Postgres, the `[pg]` extra and `docker compose up -d db` |
| `ikg sparql "QUERY"` | Query `processed/ikg.ttl` | none | built corpus |
| `ikg mcp` | Serve the corpus as MCP tools (docs/03 §13): over stdio, or Streamable HTTP at `/mcp` | `--http`, `--host` (127.0.0.1), `--port` (8001) | built corpus; the `[mcp]` extra (Python 3.11+); `--http` needs `IKG_MCP_TOKEN` |
| `ikg curator add\|remove\|list [NAME]` | Manage named curators; `add` prints the new login token once | none | none |
| `ikg provenance` | Print the provenance log, oldest first | `-n` (50) | none |
| `ikg serve` | Run the web app | `--host` (127.0.0.1), `--port` (8000) | `[web]` extras |

`python -m ikg` is equivalent to `ikg`.

## 4. Environment variables

Set these in the shell or in a `.env` file (template: `.env.example`). The `ikg` command loads
`.env` from the current directory upwards before reading any setting; values already set in the
shell take precedence. `.env` is git-ignored.

| Variable | Default | Effect |
|---|---|---|
| `IKG_DATA` | `data` | Data directory (raw, processed, uploads, registries) |
| `IKG_ADMIN_TOKEN` | random, printed at startup | Admin login token; logs in as the curator "admin" |
| `IKG_CONTRIBUTOR_TOKEN` | unset (open uploads) | Token required on `/contribute` |
| `IKG_ASK_LIMIT` | `20` | Ask requests per hour per client IP for non-admins; `0` = admins only. Also the MCP server's `ikg_ask`, per server process; `0` turns it off there. |
| `ANTHROPIC_API_KEY` | none | Claude API key for Ask |
| `IKG_ANSWER_MODEL` | `claude-haiku-5-5` | Model for cited answers: a Claude model (D13), or `ollama:<model>` for a local model through Ollama, e.g. `ollama:qwen3.5:9B` (no API key or cost; citations checked in code, docs/03 §6.1) |
| `IKG_ANSWER_EFFORT` | `low` | `low`, `medium`, `high`, `xhigh` or `max` (Claude only) |
| `IKG_LOCAL_ANSWER_TIMEOUT` | `180` | Seconds a local model may take per answer |
| `IKG_LOCAL_ANSWER_CONTEXT` | `16384` | Context window (tokens) requested from Ollama for local answers |
| `IKG_ASK_BUDGET_USD` | `5` | Estimated Ask spend per UTC day, all visitors together, before Ask stops. The MCP server keeps its own daily budget with this limit. |
| `IKG_ANSWER_CACHE_SIZE` | `1000` | Answers kept in memory for repeated questions; `0` disables the cache |
| `IKG_JUDGE_MODEL` | `claude-opus-5-5` | Judge for `ikg eval --fidelity` |
| `IKG_JUDGE_EFFORT` | `medium` | Judge effort |
| `IKG_UPLOAD_LIMIT` | `10` | Contributor uploads per hour per client address (admins are not limited) |
| `IKG_UPLOAD_GLOBAL_LIMIT` | `100` | Contributor uploads per hour across all clients |
| `IKG_LOGIN_FAILURE_LIMIT` | `10` | Failed admin logins per hour per client address before login is refused |
| `IKG_PDF_MAX_PAGES` | `2000` | PDFs with more pages are rejected |
| `IKG_PDF_TIMEOUT` | `120` | Seconds a PDF may take to read before it is rejected |
| `IKG_PDF_MAX_CHARS` | `50000000` | PDFs with more text are rejected |
| `IKG_PDF_MAX_MEMORY_MB` | `2048` | Memory cap for the PDF reader process (POSIX only) |
| `IKG_PDF_MAX_PARALLEL` | `2` | PDFs read at the same time |
| `IKG_EMBED` | `off` | `on`/`1`/`true`/`yes`: builds (CLI and admin Rebuild) also write passage embeddings |
| `IKG_MCP_TOKEN` | unset | Bearer token `ikg mcp --http` requires from every client (16+ characters) |
| `IKG_MCP_PUBLIC` | `off` | `on`: `ikg mcp --http` needs no token and leaves out `ikg_ask` (read-only tools only), for clients that can't send a static token (a ChatGPT custom MCP server, claude.ai). Anyone with the URL can then search |
| `IKG_API_TOKEN` | unset | Bearer token API clients (a custom GPT's Actions) send to `/api/ask` from outside the site; skips the same-origin check (16+ characters) |
| `IKG_API_ASK_LIMIT` | `60` | Ask calls per hour for all API-token clients together |
| `IKG_PUBLIC_URL` | unset | The app's public `https://` address, put in `/openapi.json` `servers` |
| `IKG_MCP_PORT` | `8001` | Host port of the `mcp` service in `compose.yaml` |
| `IKG_SPARQL_TIMEOUT` | `10` | Seconds an MCP SPARQL query may take, including loading the graph in its child process |
| `IKG_ONTOLOGY` | `ontology/ikg.ttl` in the source tree | Ontology file served as the MCP resource `ikg://ontology` (the Docker image sets it) |
| `IKG_RETRIEVER` | `hybrid` | Retrieval for search, Ask and eval: `bm25`, `dense` or `hybrid` (docs/03 §5.3). Falls back to `bm25` when the build has no embeddings |
| `IKG_VECTOR_STORE` | `file` | Nearest-passage search: `file` (exact, in memory) or a `postgresql://` URL with pgvector, loaded from the build on first use |
| `IKG_PG_PORT`, `IKG_PG_PASSWORD` | `5433`, `ikg` | Host port and password of the Postgres in `compose.yaml` (local development) |
| `IKG_APP_PORT` | `8000` | Host port of the app container in `compose.yaml` |
| `IKG_TEST_PG_DSN` | unset | Tests only: a Postgres with pgvector for `tests/test_vectorstore.py`; unset skips those tests |
| `IKG_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | fastembed model for embeddings, or `ollama:<model>` (e.g. `ollama:nomic-embed-text`) for a local Ollama |
| `IKG_SCREEN` | `on` | `off`/`0`/`false`/`no` disables upload screening |
| `IKG_SCREEN_MODEL` | `qwen3.5:9B` | Ollama model for screening |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server URL |
| `IKG_SCREEN_TIMEOUT` | `180` | Seconds per screening call |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` (uvicorn) | Proxies whose `X-Forwarded-For` is trusted, which determines the client IP for rate limits |

## 5. Library entry points

For notebooks and scripts:

```python
from ikg.pipeline import Runtime, build
from ikg.retrieve import search
from ikg.qa import ask

rt = Runtime.load()                                   # after `ikg build`
hits = search(rt.index, rt.gazetteer, "dharma of a kshatriya", k=5,
              passage_entities=rt.passage_entities, primary_only=True)
answer = ask("What does Bhishma say about kingship?", rt.index, rt.gazetteer,
             graph=rt.graph, passage_entities=rt.passage_entities)
print(answer.text, [c.passage_id for c in answer.citations])
```
