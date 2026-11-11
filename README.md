# Indic Knowledge Graph

An ontology, knowledge graph and source-grounded question-answering system over the
**Upanishads, Mahabharata, Ramayana and Vishnu Purana**, with a web viewer and an admin
interface for adding texts and rebuilding the graph.

The design goal is **traceability**. Every answer cites a specific passage in a specific
edition by a named translator, and the system keeps three distinctions that most RAG systems
lose:

1. **Primary text vs. commentary.** A translator's commentary, an editor's introduction and
   modern scholarship are labelled as such and never presented as what the text says.
2. **Sanskrit concept vs. translator's word.** The graph records when a translator wrote
   "righteousness" or "duty" for *dharma*, or "Self" for *ātman*, so translation choices stay visible.
3. **Which text, which speaker.** Answers keep views from different works and speakers separate
   instead of blending them.

**What it does today:**

- **Cited answers.** Every sentence of an answer cites a passage id, edition and translator. If the passages
  don't answer the question, it says `NOT_IN_SOURCES` instead of guessing. Answers come from Claude
  (Haiku 5.5, about $0.001 each), or free from a local model through Ollama (e.g. Qwen 3.5 9B), whose
  citations are checked in code.
- **Hybrid search.** Keyword (BM25 with gazetteer expansion) and meaning (dense embeddings) are fused. The
  right passage comes first for 41% of the evaluation questions, up from 28% with keywords alone.
- **A knowledge graph** of people, deities, places and concepts, exported as RDF/OWL for SPARQL.
- **An MCP server** (`ikg mcp`), so AI assistants such as Claude, ChatGPT or Copilot can search, read
  passages, look up entities, query the graph and get cited answers as tools.
- **A web app and admin** for searching, asking, exploring the graph, contributing texts and curating
  them, with named curators and an append-only provenance log.
- **Measured.** An 86-question evaluation gates every pull request in CI.

**Where it's going** ([docs/08](docs/08-agentic-extension.md)): the same backend as plugins for Claude,
ChatGPT and Microsoft 365 Copilot, then an agent that plans multi-step questions and checks every citation,
with claims entering the graph only after a curator approves them. [docs/09](docs/09-related-projects.md)
looks at Sanskrit-side projects that could add the original text, and there is a
[two-page brief](docs/briefs/ikg-brief.pdf) for readers outside the code.

Specification, architecture, design, data model, interfaces and roadmap are in [`docs/`](docs/README.md).

## Corpus

| Work | Edition | Passages |
|---|---|---|
| Upanishads (Isa, Katha, Kena) | Swami Paramananda, 1919 (Gutenberg #3283) | 268 (165 verse, 92 commentary, 11 introduction) |
| Mahabharata, all 18 books | Kisari Mohan Ganguli, 1883–96 (Gutenberg #15474–15477) | 11,916 |
| Ramayana, books 1–6 | Ralph T. H. Griffith, 1870–74 (Gutenberg #24869) | 1,687 |
| Vishnu Purana, parts 1–6 | M. N. Dutt after H. H. Wilson, 1896 (Gutenberg #66208) | 741 |

About 3.1 million words. The Vishnu Purana is the only Purana on Project Gutenberg. Others
(Bhagavata, Markandeya, Garuda) can be added through the admin interface once a
public-domain edition is located; see `src/ikg/data/sources.yaml`.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; on macOS/Linux: source .venv/bin/activate
pip install -e ".[web,dev,embed]"   # add mcp for the MCP server (Python 3.11+), pg for Postgres

gh release download pinned-sources-v1 --dir data/raw --pattern 'pg*.txt' --clobber
                                  # the exact pinned texts (~18 MB). Gutenberg re-issued two of them,
ikg fetch                         # and a re-issued text changes passage IDs; fetch verifies the pins
gh release download embeddings-bge-small-en-v1.5 --dir data/processed --pattern 'embeddings.*' --clobber
                                  # optional: published passage embeddings, so the next step reuses
ikg build --embed                 # them instead of computing them for ~10 minutes (it checks they
                                  # match; the 65 MB model still downloads once). Plain `ikg build`
                                  # works too, with keyword-only search
ikg serve                         # web app at http://127.0.0.1:8000
```

A build records checksums of everything it writes, and the app refuses a build that doesn't
match them. After upgrading from a version without them, run `ikg build` once.

Cited answers call Claude (Haiku 5.5 by default, about $0.001 a question) and need an API key,
or run on a local model with `IKG_ANSWER_MODEL=ollama:qwen3.5:9B` (no key, no cost, slower;
citations are checked in code, see docs/03 §6.1). Copy `.env.example` to `.env`
and set `ANTHROPIC_API_KEY` and `IKG_ADMIN_TOKEN` there; `ikg` loads it from the current
directory, and `.env` is git-ignored. Variables set in the shell take precedence.
Search, the graph explorer, analysis and the retrieval evaluation work without one.

### With Docker

After `ikg build --embed` on the host (the image serves the corpus in `./data`; an admin Rebuild
from the site rebuilds it there, embeddings included). On Linux, `sudo chown -R 1000:1000 data` first:

```bash
docker compose up -d --build              # app on http://127.0.0.1:8000, Postgres + pgvector on 5433
docker compose exec app ikg vectors compare   # pgvector recall against exact search, from the container
docker compose down                       # add -v to delete the database volume (a disposable copy)
```

## Web app

| Page | Who | What it does |
|---|---|---|
| `/` | everyone | Search passages, or **Ask** for an answer with numbered citations. Filter by work; tick *Primary text only* to exclude commentary and scholarship. Shows which entities were recognised in the question. |
| `/graph` | everyone | Interactive entity graph (Cytoscape). Filter by work, entity type and minimum shared passages. Click a node for its aliases, translators' renderings, strongest connections and passages. |
| `/entity/{id}`, `/passage/{id}` | everyone | Entity profile (surface forms, counts per work, connections, passages); full passage text with entities, previous/next navigation and source link. |
| `/contribute` | contributors | Upload `.txt`, `.md` or `.pdf` material (papers, book chapters, lecture transcripts, translations). Submissions wait in a review queue. |
| `/admin` | admin | Approve or reject submissions; add a Project Gutenberg text by ebook number; upload files directly; edit the gazetteer (entities, aliases, renderings) with validation; **Rebuild** the graph in the background with a live log. |

**Access control.** Admin login uses the `IKG_ADMIN_TOKEN` environment variable (logged in as
"admin"; if it is unset, a random token is printed at startup), or a named curator's token from
`ikg curator add <name>`. Every admin action and public submission is recorded with who did it in
an append-only provenance log, shown on the admin page and by `ikg provenance`. Failed logins are
recorded and limited per client. Set `IKG_CONTRIBUTOR_TOKEN` to require a token for
uploads too. Cited answers cost API credits, so Ask runs only when the form is submitted on this site
(never on a GET, and not from other sites), repeated questions are answered from a cache at no cost, all Ask calls stop for the day
at `IKG_ASK_BUDGET_USD` (default $5), and visitors who are not logged in as admin get
`IKG_ASK_LIMIT` questions per hour per client address (default 20; `0` restricts Ask to admins).
Behind a reverse proxy the client address comes from `X-Forwarded-For`, which uvicorn trusts
only from 127.0.0.1; for a proxy on another host, set `FORWARDED_ALLOW_IPS` to its address. Sessions are `HttpOnly`, `SameSite=Strict` cookies, marked `Secure`
when served over HTTPS. Uploads are limited to 20 MB and to
`IKG_UPLOAD_LIMIT` per hour per client (10; 100 per hour in total), PDFs are read in a separate
process with time, page and text limits and no access to secrets, and uploads are stored under random names, never executed, and never enter the corpus without admin approval.
For a public deployment, put the app behind HTTPS and a real identity provider.

**Relevance screening.** Before an upload is stored, a local model served by
[Ollama](https://ollama.com) reads excerpts from its start, middle and end, together with the
title and notes, and rejects material that is not about Indic texts and traditions. The reason
is shown to the uploader. Accepted uploads keep the model's verdict, which appears in the admin
review queue. If Ollama cannot be reached, uploads are rejected rather than accepted unchecked.
Screening filters out off-topic material; it does not replace admin review.

```bash
ollama pull qwen3.5:9B            # default screening model (~6.6 GB; qwen3.5:4B also works)
```

| Variable | Default | |
|---|---|---|
| `IKG_SCREEN` | `on` | `off` disables screening |
| `IKG_SCREEN_MODEL` | `qwen3.5:9B` | any Ollama chat model |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | |
| `IKG_SCREEN_TIMEOUT` | `180` | seconds; the first call loads the model |

JSON API: `GET /api/search?q=`, `POST /api/ask {"question": ...}`, `GET /api/graph`,
`GET /api/entity/{id}`, `GET /api/build/status` (admin).

**Uploaded material** is split into sections on common headings (`BOOK 1`, `PART II`,
`CHAPTER 3`, `SECTION IV`, `CANTO 12`, or Markdown `#` and `##`). Text without headings
becomes one section. Choose the passage kind on upload: scholarship (`secondary`), primary
prose (`narrative`) or verse, or `commentary`. Passages from a PDF keep their page numbers, so
answers cite them as, for example, *Gita notes, Part 1, Chapter 2, p. 14 [secondary] (by A. Scholar)*,
or *pp. 14–15*; when the PDF defines printed page labels (roman-numbered front matter, a book that
starts at page 1 on its fifth sheet), those are used.

## Command line

```bash
ikg search "What does Yama teach Nachiketas about the Self?"       # retrieval only
ikg ask "What does Yama teach Nachiketas about the Self?" --primary-only
ikg analyze                       # writes reports/corpus-analysis.md
ikg eval                          # retrieval metrics; add --llm for answer/citation metrics
ikg eval --no-expand              # ablation without gazetteer query expansion
ikg eval --check eval/baseline.json   # regression gate used by CI
ikg sparql "SELECT ?label WHERE { ?e a ikg:Concept ; rdfs:label ?label }"
ikg search "What does Vidura say about krodha?" --mode dense       # bm25 | dense | hybrid (default)
ikg eval --retriever all          # BM25, dense and hybrid side by side, per question type
ikg ask "Who is Nachiketas?" --model ollama:qwen3.5:9B             # a local model, no API key
ikg mcp                           # the corpus as MCP tools over stdio (--http for remote)
ikg vectors compare --store postgresql://ikg:ikg@127.0.0.1:5433/ikg   # pgvector recall vs exact search
ikg stats
```

## Use it from an AI assistant (MCP)

`ikg mcp` serves the corpus as [Model Context Protocol](https://modelcontextprotocol.io) tools. Install the
extra first (`pip install -e ".[mcp]"`, Python 3.11+).

| Tool | What it does | Cost |
|---|---|---|
| `ikg_search_passages` | Ranked passages for a question, with work, edition, translator and kind; filters by work and primary text | free |
| `ikg_get_passage` | One passage with its entities, source URL and up to two neighbours each side | free |
| `ikg_get_entity` | A person, place or concept by id or any alias: renderings by translator, mentions per work, strongest links | free |
| `ikg_sparql_query` | Read-only SPARQL (SELECT or ASK; no updates, no SERVICE; time- and row-limited) | free |
| `ikg_ask` | A cited answer, or `NOT_IN_SOURCES` | budgeted and rate-limited (free with a local model) |

The resource `ikg://ontology` serves the ontology for writing SPARQL. Every tool output has a JSON schema,
and every call is logged to `data/mcp-calls.jsonl` with the caller, tool and cost.

**As a Claude Code plugin**, with the `ikg-scholar` skill that teaches Claude to cite passage ids and
translators, search across works and abstain ([integrations/](integrations/README.md)):

```
/plugin marketplace add vkonda2000/indic-knowledge-graph
/plugin install indic-knowledge-graph@indic-knowledge-graph    # asks for the server URL and token
```

**Local (stdio)**, for Claude Code, Claude Desktop or any MCP client that launches a command:

```bash
claude mcp add ikg -- ikg mcp          # Claude Code, from the project folder with the .venv activated
                                       # (or give the full path to .venv/Scripts/ikg.exe)
```

For Claude Desktop, add to `claude_desktop_config.json`. It doesn't start in the project folder, so give
full paths, and any settings `.env` would have provided:

```json
{"mcpServers": {"ikg": {"command": "D:/Projects/indic-knowledge-graph/.venv/Scripts/ikg.exe",
                        "args": ["mcp"],
                        "env": {"IKG_DATA": "D:/Projects/indic-knowledge-graph/data",
                                "IKG_ANSWER_MODEL": "ollama:qwen3.5:9B"}}}}
```

**Remote (Streamable HTTP)** at `/mcp`, protected by a bearer token:

```bash
IKG_MCP_TOKEN=<16+ random characters> ikg mcp --http --port 8001
# or with Docker, after setting IKG_MCP_TOKEN in .env:
docker compose --profile mcp up -d mcp     # http://127.0.0.1:8001/mcp
```

With `IKG_MCP_PUBLIC=on` the server needs no token and serves only the read-only tools (no `ikg_ask`),
within an hourly call limit, for clients that can't send a token: a ChatGPT custom MCP server or a
claude.ai connector ([integrations/README.md](integrations/README.md)). A ChatGPT custom GPT uses the
web app's `/openapi.json` instead. A Microsoft 365 Copilot agent is next
([docs/08 B2–B4](docs/08-agentic-extension.md)).

## Roadmap: the agentic extension

[docs/08](docs/08-agentic-extension.md) adds hybrid retrieval, an MCP server, three assistant platforms
and an agent, one measured pull request at a time.

| Step | What | Status |
|---|---|---|
| A1–A3 | Embeddings at build time; a vector store (exact, or Postgres + pgvector); hybrid retrieval, adopted because it beat BM25 on the evaluation | Done |
| — | Cited answers from a local model (Ollama), citations checked in code | Done |
| B1 | MCP server: search, passages, entities, SPARQL and answers as tools; stdio and HTTP with a token | Done |
| E (local) | Docker image and compose stack (app, Postgres, MCP) | Done; public hosting and a runbook next |
| B2 | Claude: a Claude Code plugin (the MCP server and the `ikg-scholar` skill) and a plugin marketplace; the skill tested on three questions over stdio ([integrations/](integrations/README.md)) | Done; an installed-plugin run over HTTP and the claude.ai connector (needs hosting) remain |
| B3–B4 | The same backend as a ChatGPT custom GPT and a Microsoft 365 Copilot declarative agent | Next |
| C1–C3 | A LangGraph agent that plans, gathers evidence through the MCP tools, drafts, and verifies every citation; curator approval before a claim enters the graph; an LLM-judge evaluation with calibration; versioned prompts | Planned |
| D | A regulation corpus (31 CFR Chapter X), to show the design carries to compliance text | Optional |

## How it works

```
Gutenberg / uploads ──► segment ──► passages (id, work, book, section, kind, translator, source)
                                      │
               gazetteer ──► entity extraction ──► mentions (alias | rendering)
                                      │
             ┌───────────────┬────────┼─────────────────────────┐
         BM25 index   embeddings (bge-small)   co-occurrence graph   RDF (ontology/ikg.ttl)
             │          + vector store            (networkx)          SPARQL
             └── hybrid: rank fusion + query expansion + entity boost ──┐
                                                                         ▼
       Claude with native citations, or a local model with code-checked citations
                                → answer + passage references
                                                                         ▲
           web app · CLI · MCP server (search, passages, entities, SPARQL, ask)
```

- **Segmentation** (`segment.py`): one parser per edition keeps the text's own structure
  (parva/section, kanda/canto, Upanishad/part/verse, amsha/section) and gives every passage a
  stable id such as `mbh.b12.s090.p03` or `upa.katha.p1.v20`. In the Upanishads, verse text is
  split from the translator's commentary. Two source quirks are handled explicitly: Ganguli's
  books 8–11 and 16–18 number sections in Arabic numerals, and the Vishnu Purana text repeats
  "SECTION XXVIII" where XXXVIII is meant.
- **Ontology** (`ontology/ikg.ttl`): Work, Edition, Book, Section, Passage (Verse, Narrative,
  Commentary, Introduction, Secondary); Person, Deity, Place, Concept, SchoolOfThought;
  Mention (with `via`), Rendering, and Claim (with `statedBy`, `heldBy`, `groundedIn`) for
  expert-curated assertions.
- **Entities** (`src/ikg/data/gazetteer.yaml`, editable in the admin): 76 seed entities with IAST
  names, aliases, case-sensitive aliases ("Self" but not "self"), translator renderings, and
  aliases limited to one work. For example, "Bharata" means Rama's brother only in the Ramayana,
  and the bare name "Rama" is matched only there, because in the Mahabharata it often means
  Balarama or Parasurama. Matching is deterministic, so every graph edge traces back to a
  surface form in a passage.
- **Retrieval** (`retrieve.py`): hybrid by default. BM25, with the query expanded from the
  gazetteer (a question about *dharma* also searches "righteousness" and "duty", and one about
  "Partha" also searches "Arjuna"), is fused with dense search over passage embeddings
  (`embed.py`, `vectorstore.py`) by reciprocal rank fusion. Passages mentioning the question's
  entities get a boost. Without embeddings it falls back to BM25.
- **Answering** (`qa.py`, `localqa.py`): the top passages go to Claude as documents with citations
  enabled, or to a local Ollama model whose cited quotes are verified in code,
  with a short knowledge-graph summary for orientation. The system prompt requires grounding,
  separate attribution for commentary and scholarship, no blending of views across works, and an
  explicit `NOT_IN_SOURCES` abstention. Refusals are handled, and server-side fallback is enabled.

## Evaluation

`eval/questions.jsonl` has 71 retrieval questions across all four works and 15 adversarial
questions the corpus cannot answer (for example, Hanuman in the Katha Upanishad). Retrieval
questions include ones phrased with the Sanskrit term where the translation uses an English
word (`skt-`), ones about what a named speaker says (`spk-`), and stories told in more than one
work (`xw-`).

Each retrieval item names the section or verse that answers it (checked by reading the
passages), e.g. `mbh.b08.s090` for Karna's death or `upa.katha.p2.v18` for the unborn Self.

Only primary text counts as a hit, not commentary on the expected verse.

| Retrieval (k = 8) | hit@1 | hit@8 | MRR |
|---|---|---|---|
| **Hybrid: BM25 + dense, fused (default)** | **40.8%** | **63.4%** | **0.476** |
| Dense only (`bge-small-en-v1.5`) | 39.4% | 57.7% | 0.457 |
| BM25 with gazetteer expansion | 28.2% | 62.0% | 0.423 |
| BM25 without (`--no-expand`) | 29.6% | 62.0% | 0.420 |

`ikg eval --retriever all` prints the first three side by side, per question type. Hybrid puts the
right passage first for 9 more of 71 questions; it finds it in the top 8 for only one more. It
gains Sanskrit-phrased, speaker and Vishnu Purana questions and loses two Ramayana ones, where it
ranks other passages that also describe the event first (Narad's summary in Bala Kanda 1, Sundara
Kanda 55). Hybrid needs a build with embeddings (`pip install -e ".[embed]"`, `ikg build --embed`);
without one, search falls back to BM25.
Computing the embeddings takes about 10 minutes on a laptop. To skip that, take the published ones
(the build checks they match your passages and model, and re-embeds if not):
`gh release download embeddings-bge-small-en-v1.5 --dir data/processed --pattern 'embeddings.*' --clobber`,
then `ikg build --embed`. (They were made with the fastembed version the `[embed]` extra pins.)

What this shows so far:

- **Finding the right passage is much harder than finding the right book.** On the original 24 items
  with book-level expectations BM25 scored 62.5% / 95.8%. Hybrid retrieval still misses 26 of 71
  items entirely; questions phrased with Sanskrit terms (11 of 15 miss) and questions about what
  a named speaker says (7 of 16 miss) are the weakest. Hybrid helps most with ranking; a
  re-ranker (Phase 3) is next.
- **For the Upanishads, the translator's commentary outranks the verse itself**, because it is
  longer and wordier.
- **Gazetteer expansion is mixed here:** the same hit@8, slightly better MRR, slightly worse hit@1;
  it is what finds the few Sanskrit-phrased questions that hit at all. The set is
  still small; a larger set written by subject experts is the next step.
- **One known failure, kept deliberately.** `con-02` asks about a kshatriya's dharma "according
  to Krishna". The Bhagavad Gita (Bhishma Parva, sections 25–42) loses to Santi Parva passages on
  kingship.
- **Answers: Haiku 5.5 matches Opus 5.5 here at 1/44 of the cost.** `ikg eval --llm` on the
  original 28 items:

  | Model, effort | Cites right book | Citation precision | Abstains when it should | False abstentions | Cost / question |
  |---|---|---|---|---|---|
  | Opus 5.5, high | 0.917 | 0.702 | 1.0 | 0.167 | $0.051 |
  | Haiku 5.5, medium | 0.917 | 0.735 | 1.0 | 0.083 | $0.0013 |
  | **Haiku 5.5, low (default)** | 0.917 | 0.747 | 1.0 | 0.083 | **$0.0012** |

  One run each, with the earlier book-level expectations on 28 items, and the set is small; it
  doesn't yet test fidelity to schools of thought. The comparison will be re-run when that
  benchmark exists.
- **Current answer baseline** (the first 57 items, section level, [eval/baseline-llm.json](eval/baseline-llm.json)):
  the default cites the answering passage for 66% of answerable questions, matching retrieval
  hit@8. On retrieval misses it usually abstains (10 of 16) but answered from the wrong passages
  5 times; it abstains on all 10 unanswerable questions. Retrieval is the bottleneck.
- **Retrieval is guarded in CI.** The row with gazetteer expansion is committed as
  [eval/baseline.json](eval/baseline.json), and a pull request that lowers either number fails.

## Analysis

`ikg analyze` writes [reports/corpus-analysis.md](reports/corpus-analysis.md). Findings include:

- **Translators mostly render dharma in English.** In Ganguli's Mahabharata, "dharma" appears
  340 times, against 2,832 English renderings ("duties", "righteousness", "duty"). In Griffith's
  Ramayana the Sanskrit word never appears. Searching for the Sanskrit term alone misses most of
  the material.
- **Dharma vocabulary is densest in the didactic books.** It peaks in the Santi Parva (30.3 per
  10,000 words), the Svargarohanika Parva (25.2) and the Anusasana Parva (23.2), against 2.5–5
  in the battle and narrative books.
- **The character graph splits into two communities.** Mahabharata figures cluster around
  Arjuna and Krishna, Ramayana figures around Rama. Narada, Vasishtha, Vishvamitra and the
  deities bridge them.

## Limitations

- The texts are 19th- and early-20th-century English translations. The system can surface
  translation choices but cannot yet compare them against the Sanskrit. Aligning passages to
  Sanskrit originals from the Digital Corpus of Sanskrit is proposed in [docs/09](docs/09-related-projects.md).
- Retrieval finds the answering passage in the top 8 for about two-thirds of the questions; ones
  phrased with Sanskrit terms are the weakest (11 of 15 miss).
- Entity matching is lexical. Epithets missing from the gazetteer are missed, and patronymics
  inflate some counts ("son of Pandu").
- Claims and schools of thought are modelled in the ontology but not yet populated. They are
  meant to be curated by subject experts through the admin workflow.
- The web app keeps sessions in memory and runs one rebuild at a time. It is built for a
  small team, not public scale.

## Tests

```bash
pytest
ikg eval --check eval/baseline.json   # fails if retrieval got worse than the committed baseline
```

Over 200 tests cover the edition parsers (with fixtures that reproduce each edition's quirks), entity
matching and gazetteer validation, checksum pinning of the source texts, retrieval and expansion, the graph and its RDF export
(checked against the ontology), the analysis report, the CLI's `search` and `stats`, QA
citation handling, model choice and cost with a mocked Claude client, and the web app end to end: contribute, review,
rebuild, search, graph, auth, upload validation, HTML escaping, the answer cache, the daily
budget, and that Ask never runs on a GET; embeddings, the vector store and hybrid fusion with a
fake embedder; local-model answers with a fake Ollama; and every MCP tool through an in-process MCP
client. No test needs a network connection, a model or an API key.

**Continuous integration.** GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml))
runs the tests on Python 3.10 and 3.12 for every pull request, the vector-store tests against a
pgvector container, and a Docker image build. It also builds the full corpus with embeddings and
runs the eval check above against the hybrid baseline. Every PR also gets an independent review by a Fable subagent
([.claude/agents/fable-reviewer.md](.claude/agents/fable-reviewer.md)); see
[ONBOARDING.md §7](ONBOARDING.md#7-how-we-work).

## Technology

Python 3.10+, a BM25 index implemented in the project, fastembed (`bge-small-en-v1.5`) for
embeddings with an exact or Postgres + pgvector vector store, a curated YAML gazetteer, networkx,
rdflib (OWL, RDF, SPARQL), Claude (Haiku 5.5 by default) with native citations for answers or a
local Ollama model, the MCP Python SDK, FastAPI with Jinja2, Cytoscape.js, Docker, pytest, and
GitHub Actions. The corpus is stored as files; Postgres is optional, a copy of the vectors. The full table with versions and
where each technology is used is in [docs/02 §7](docs/02-architecture.md#7-technology-stack).
