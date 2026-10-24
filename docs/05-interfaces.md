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
| GET | `/api/graph` | public | `work`, `types` (comma-separated), `min_weight` (default 5), `max_nodes` (≤ 300, default 80) | Cytoscape elements: `{nodes: [{data: {id, label, type, mentions}}], edges: [{data: {id, source, target, weight}}]}` |
| GET | `/api/entity/{id}` | public | none | `{id, label, type, iast, aliases, renderings, mentions, works, surface_forms, neighbours, passages}` (10 passages) |
| GET | `/api/build/status` | admin | none | `{state: idle\|running\|done\|error, log: [str], stats, error, started, finished}` |

**Errors** are `{"detail": "..."}` with these status codes:

| Status | When |
|---|---|
| 400 | Malformed `/api/ask` body or a non-integer `k` |
| 404 | Unknown entity or passage |
| 422 | FastAPI parameter validation failed |
| 429 | Ask limit reached |
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
| `ikg fetch` | Download registered Gutenberg texts | `--works ID...`, `--force` | network |
| `ikg build` | Segment, extract, index, graph, RDF | `--works ID...`, `--with-passages` (passages in RDF) | raw texts |
| `ikg stats` | Passages per work and kind | none | built corpus |
| `ikg search "Q"` | Ranked passages, no API call | `-k`, `--works`, `--primary-only` | built corpus |
| `ikg ask "Q"` | Cited answer, then the model, tokens and estimated cost | `-k`, `--works`, `--primary-only`, `--show-retrieval`, `--model`, `--effort` | `ANTHROPIC_API_KEY` |
| `ikg analyze` | Write `reports/corpus-analysis.md` | none | built corpus |
| `ikg eval` | Retrieval metrics; failures listed as `MISS` | `-k`, `--llm`, `--no-expand`, `--model`, `--effort` (with `--llm`), `--check BASELINE` (exit 1 if any baseline metric got worse; used by CI) | built corpus (+ API key with `--llm`) |
| `ikg eval --fidelity` | SOT-fidelity benchmark: pass/fail per item and check, rates per property | `--baseline` (plain model, no retrieval), `--include-drafts`, `--judge-model`, `--model`, `--effort`, `-k`; not combinable with `--check`, `--llm`, `--no-expand` | API key; built corpus unless `--baseline`; at least one approved item |
| `ikg sparql "QUERY"` | Query `processed/ikg.ttl` | none | built corpus |
| `ikg serve` | Run the web app | `--host` (127.0.0.1), `--port` (8000) | `[web]` extras |

`python -m ikg` is equivalent to `ikg`.

## 4. Environment variables

Set these in the shell or in a `.env` file (template: `.env.example`). The `ikg` command loads
`.env` from the current directory upwards before reading any setting; values already set in the
shell take precedence. `.env` is git-ignored.

| Variable | Default | Effect |
|---|---|---|
| `IKG_DATA` | `data` | Data directory (raw, processed, uploads, registries) |
| `IKG_ADMIN_TOKEN` | random, printed at startup | Admin login token |
| `IKG_CONTRIBUTOR_TOKEN` | unset (open uploads) | Token required on `/contribute` |
| `IKG_ASK_LIMIT` | `20` | Ask requests per hour per client IP for non-admins; `0` = admins only |
| `ANTHROPIC_API_KEY` | none | Claude API key for Ask |
| `IKG_ANSWER_MODEL` | `claude-haiku-5-5` | Claude model for cited answers (D13) |
| `IKG_ANSWER_EFFORT` | `low` | `low`, `medium`, `high`, `xhigh` or `max` |
| `IKG_ASK_BUDGET_USD` | `5` | Estimated Ask spend per UTC day, all visitors together, before Ask stops |
| `IKG_ANSWER_CACHE_SIZE` | `1000` | Answers kept in memory for repeated questions; `0` disables the cache |
| `IKG_JUDGE_MODEL` | `claude-opus-5-5` | Judge for `ikg eval --fidelity` |
| `IKG_JUDGE_EFFORT` | `medium` | Judge effort |
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
