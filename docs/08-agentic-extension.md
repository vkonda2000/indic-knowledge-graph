# 08 Agentic extension: hybrid retrieval, an MCP server, three platforms, an agent

**Status:** proposed, 2026-11-07. Builds on Phase 3 of [06 Roadmap](06-roadmap.md#phase-3-graphrag-semantic-retrieval-and-the-reliability-loop-t4-t6-t8-t9-t14)
and does not replace it. Four workstreams (A–D), eleven pull requests, about two weeks of evenings.

## 0. Goal and rules

Today IKG answers in one shot: retrieve with BM25, then one cited Claude call. This extension adds

- **A. Hybrid retrieval**: dense embeddings beside BM25, fused, measured by the existing eval.
- **B. An MCP server**: search, passages, entities, SPARQL and cited answers as tools, then the
  same backend packaged for Claude, ChatGPT and Microsoft 365 Copilot.
- **C. An agent**: a LangGraph graph that plans, calls those tools, drafts a cited answer,
  checks every citation, and stops for a person where a claim would enter the graph.
- **D. (Optional) A regulation corpus**: the same pipeline over U.S. anti-money-laundering rules,
  to show the design carries to compliance text.

Rules that hold across every PR:

1. **Traceability is not traded for anything.** Primary text vs. commentary, concept vs. rendering, and
   which work / which speaker stay separate in every new path (tools, agent, plugins).
2. **The CI gate never goes down.** `ikg eval --check eval/baseline.json` passes on every PR. A PR that
   moves a number updates the baseline in the same PR and says why.
3. **No test needs a network or a key.** Embeddings, MCP and the agent are tested with fixtures and a
   mocked Claude client, as today.
4. **Every model call has a cost.** New paths report tokens and USD the way `qa.Answer` does and count
   against `IKG_ASK_BUDGET_USD`.
5. **Unknown is unknown.** A metric, a cost or a usage figure that cannot be read is `null`, never 0.

## A. Hybrid retrieval (3 PRs)

The measured problem ([README](../README.md#evaluation)): hit@8 is 0.62; 12 of 15 `skt-` items and
8 of 16 `spk-` items miss, and Upanishad commentary outranks its verse (L36).

| PR | What | Where |
|---|---|---|
| A1 | **Embeddings at build time.** `fastembed` with `BAAI/bge-small-en-v1.5` (ONNX, CPU, 384 dims; about 15k passages in a few minutes, deterministic, runs in CI). Writes `data/processed/embeddings.npy` + its row order, with checksums in `manifest.json` like every other build output. `IKG_EMBED_MODEL` selects another; Ollama's `nomic-embed-text` as an option for local use. | `pipeline.build()`, new `embed.py` |
| A2 | **A `VectorStore` interface, two implementations.** `FileVectorStore` (numpy, the default and CI's) and `PgVectorStore` (Postgres + pgvector, HNSW index, `docker compose up db`). Same `search(vec, k, filter)` with work and kind filters pushed into the store. | new `vectorstore.py`, `compose.yaml` |
| A3 | **Fusion and the eval.** `search(..., mode="bm25"\|"dense"\|"hybrid")`, hybrid = reciprocal rank fusion (k = 60) over BM25 (with gazetteer expansion) and dense top-50, then the entity boost. `ikg eval --retriever` runs all three; the README table gains rows. Default switches to hybrid **only if** hit@8 and MRR beat BM25; the baseline is updated in this PR. | `retrieve.py`, `evaluate.py`, `cli.py` |

**Done when:** one table of BM25 / dense / hybrid on all 71 items, broken out by `skt-`, `spk-`,
`xw-`, with the decision and its reason written under it. If hybrid loses, say so and keep BM25: the
gate catching it is part of the story.

Later, not in this extension: a cross-encoder re-ranker over the top 50 (roadmap Phase 3).

## B. MCP server and three platforms (4 PRs)

### B1. The server

`ikg mcp` (stdio) and `ikg mcp --http` (Streamable HTTP), built with the official MCP Python SDK
(`mcp`, FastMCP). New optional dependency group `mcp`.

| Tool | Input | Output (JSON schema) | Notes |
|---|---|---|---|
| `search_passages` | `query`, `k` (≤ 20), `works[]`, `primary_only`, `mode` | `[{passage_id, ref, work, kind, translator, score, text}]` | Read-only |
| `get_passage` | `passage_id`, `neighbours` (0–2) | the passage, its entities, previous/next ids, source URL | Read-only |
| `get_entity` | `entity_id` or `name` | aliases, renderings by translator, counts per work, top connections | Read-only |
| `sparql_query` | `query` | `{columns, rows, truncated}` | **SELECT and ASK only** (parsed with rdflib, anything else refused), 5 s timeout, 500-row cap |
| `ask` | `question`, `works[]`, `primary_only` | `{text, abstained, citations[{n, passage_id, ref, cited_text}], cost_usd, model}` | Calls Claude; budget- and rate-limited like `/api/ask` |

Every tool declares an `outputSchema` and returns `structuredContent`; tool descriptions state the
traceability rules (what "commentary" means, that `ask` may answer `NOT_IN_SOURCES`). A resource
`ikg://ontology` serves `ontology/ikg.ttl` so a client can write SPARQL against it.

**Auth:** stdio needs none. HTTP requires a bearer token (`IKG_MCP_TOKEN`) at first, then OAuth 2.1
through an identity provider for the remote connectors that need it. Every call is written to the
provenance log with the caller, the tool and the cost.

**Tests:** an in-process MCP client calls each tool against the test fixtures; the SPARQL guard is
tested with `INSERT`, `DELETE`, `LOAD`, `SERVICE` and a slow query.

### B2–B4. The same backend on three platforms

One backend, two published faces: the MCP server, and the existing FastAPI JSON API with its
generated OpenAPI document (`/openapi.json`, tidied so each operation has an id, a summary and
response schemas). Each platform gets a thin package in `integrations/<platform>/`.

| PR | Platform | Package | Needs |
|---|---|---|---|
| B2 | **Claude** | A remote **connector** (the MCP URL) and a **skill** (`integrations/claude/ikg-scholar/SKILL.md`): when to use which tool, how to cite, when to abstain. Also a Claude Code plugin bundling both. | A public HTTPS endpoint (§E) |
| B3 | **ChatGPT / OpenAI Enterprise** | A **custom GPT** with **Actions** from the OpenAPI document (bearer auth), instructions carrying the same rules; plus the MCP server added as a connector where the workspace allows it. | A ChatGPT plan that can build GPTs |
| B4 | **Microsoft 365 Copilot** | A **declarative agent** (manifest, instructions, conversation starters) with an **API plugin** built from the same OpenAPI document, made with the Microsoft 365 Agents Toolkit; Copilot Studio as the alternative path. | A Microsoft 365 developer tenant with a Copilot licence (the one real cost and access risk; check first) |

**Done when:** the same three questions (one `upa-`, one `xw-`, one `adv-`) asked on each platform
give answers that cite the same passage ids, and the unanswerable one abstains on all three.
Screenshots and the questions go in `integrations/README.md`, with what differed per platform
(auth, how citations show, tool-choice behaviour).

## C. The agent (3 PRs)

### C1. The graph

LangGraph, in `src/ikg/agent/`, with the MCP tools from B1 as its tools (through the
`langchain-mcp-adapters` client, so the agent uses exactly what the platforms use) and Claude through
the Anthropic SDK.

```
question
   │
   ▼
 plan ── splits into sub-questions by work and speaker; picks tools; writes a plan (structured output)
   │
   ▼
 gather ◄─┐  calls search_passages / get_entity / sparql_query / get_passage
   │      │  loops until the plan's sub-questions each have evidence, or the budget ends
   └──────┘  (at most 6 tool calls and $0.05 per question)
   │
   ▼
 draft ── one cited answer over the union of evidence (the existing qa path and prompt)
   │
   ▼
 verify ── each answer sentence against its cited passage (structured verdict:
   │        supported / partial / unsupported); unsupported sentences removed, or one revise
   │        pass; nothing left → NOT_IN_SOURCES
   ▼
 answer   + a trace: plan, tool calls, verdicts, tokens and USD per step
```

- **State and short-term memory:** a LangGraph checkpointer (SQLite) per thread; a follow-up is
  rewritten as a standalone question before `plan`.
- **Long-term memory:** a verified answer can be proposed as a **Claim** (the ontology already has
  `Claim`, `statedBy`, `heldBy`, `groundedIn`). Proposing is the agent's; accepting is a curator's.
- **Human in the loop:** a LangGraph `interrupt` before any Claim is written. The admin page shows
  the pending claim with its passages; approve or reject writes the triples and a provenance line
  with the curator's name.
- **Guardrails:** the tool and cost budget above; SPARQL read-only (B1); a sentence with no
  citation never survives `verify`; the system prompt's rules unchanged.

Surfaces: `ikg agent "<question>"`, `POST /api/agent`, and an "Ask (agent)" choice on `/`.

### C2. Evaluation, including LLM-as-a-judge

`ikg eval --agent` over the same items, reporting beside single-shot:

| Metric | How |
|---|---|
| Cites an expected passage | As today (section / verse level) |
| Citation precision | As today |
| Abstains when it should / false abstentions | As today, on the 15 adversarial items |
| **Faithfulness** | An LLM judge (a stronger model than the answerer) scores each sentence against its cited passage with a rubric and a structured verdict |
| **Attribution** | The judge checks commentary is attributed and works are not blended |
| Tool calls, latency, USD per question | From the trace |

**Judge calibration:** hand-label 20 answers first; report the judge's agreement with them
(per-sentence accuracy and Cohen's kappa). A judge that disagrees with people is not used to gate.

**Done when:** a table of single-shot vs. agent on every item group, with cost and latency, and a
sentence on where the agent is worth its cost (expected: `xw-` and `spk-`) and where it is not.

### C3. Prompt versions

Prompts move to `src/ikg/prompts/<name>.v<N>.md`; every answer, trace and eval result records the
prompt versions it used; `eval/results/` is comparable across versions.

## D. (Optional) A regulation corpus (1 PR)

A second, small corpus beside the Indic one, to show the transfer directly.

- **Source:** 31 CFR Chapter X (the Bank Secrecy Act rules administered by FinCEN), from the eCFR API
  (U.S. government text, public domain). Parts 1010 (general), 1020 (banks), 1023 (broker-dealers).
- **Passages:** one per paragraph, ids like `cfr31.1023.320.a.2`. Kinds map onto the existing ones:
  the rule text is primary; FinCEN guidance and FAQs are commentary; a firm's own policy (a sample
  written for the demo) is secondary.
- **Gazetteer:** terms and their variants ("suspicious activity report", "SAR"; "covered
  transaction"); the same concept-vs-wording distinction as dharma vs. "duty".
- **Eval:** 20 items with expected paragraphs, 5 unanswerable.
- **The demo question:** "Which obligations in 31 CFR 1023.320 does this sample policy not cover?"
  run through the agent, with each gap cited to the rule paragraph.

## E. Running it somewhere others can reach

B2–B4 need a public HTTPS endpoint. A `Dockerfile` (the app, the MCP HTTP server, the built corpus),
`compose.yaml` with Postgres + pgvector, and one small host behind HTTPS (or a tunnel). The existing
budget cap, rate limits and admin tokens apply; the corpus is public domain. A short `RUNBOOK.md`:
start, stop, rebuild, rotate tokens, read the logs, what the budget alarm means.

## F. Order and size

| # | PR | Size |
|---|---|---|
| 1 | A1 embeddings at build | S |
| 2 | A2 vector store (file + pgvector) | M |
| 3 | A3 hybrid + eval table | S |
| 4 | B1 MCP server | M |
| 5 | E Docker, deploy, runbook | S |
| 6 | B2 Claude connector + skill | S |
| 7 | B3 ChatGPT GPT + actions | S |
| 8 | B4 Copilot declarative agent | M (tenant setup) |
| 9 | C1 agent graph | M–L |
| 10 | C2 agent eval + judge calibration | M |
| 11 | C3 prompt versions | S |
| — | D regulation corpus (optional) | M |

B4 depends on a Copilot-licensed tenant; check access before starting it. If it is not available,
ship B2 and B3 and document B4's manifest without the live test.

## G. What each part demonstrates

| Workstream | Shows |
|---|---|
| A | Vector stores, pgvector, a decision made by measurement and guarded in CI |
| B1 | MCP server design, JSON-schema'd tools, structured outputs, a read-only guard, auth |
| B2–B4 | One backend packaged as plugins for Claude, OpenAI and Microsoft 365 Copilot |
| C1 | Multi-step planning, tool calling, short- and long-term memory, guardrails, human approval |
| C2–C3 | Benchmarks, LLM-as-a-judge with calibration, prompt versioning, cost per question |
| D | The design carried to regulation text: rule vs. guidance vs. policy, and a cited gap analysis |
| E | Containers, deployment, operations documentation |
