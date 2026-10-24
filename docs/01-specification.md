# 01 Specification

## 0. Target system and this prototype

**Target.** An ontology-based AI knowledge system for Indian civilization, philosophy, history
and intellectual traditions. Such a system:

- organises a large body of scholarship (books, research papers, lectures, videos) into
  ontologies and knowledge graphs of concepts, relationships, schools of thought (SOT), texts,
  thinkers and philosophical frameworks;
- answers complex, multi-turn questions accurately, with reliable and traceable sources, and
  represents Indic traditions on their own terms, without ideological or cultural distortion;
- minimises hallucination, identifies knowledge gaps, and is evaluated continuously for
  retrieval, citation accuracy and SOT fidelity, including adversarial and multi-turn tests;
- is refined together with researchers and subject-matter experts (SMEs).

This is an independent project. The target is the scope it is designed toward.

**This prototype** implements the core of that architecture end to end on a public-domain
corpus: four classical works in English translation. It shows source-grounded GraphRAG-style
answering, an OWL ontology with RDF/SPARQL export, translator-aware concept modelling,
contributor ingestion with review, and an evaluation harness. Sections 1–5 specify the
prototype as built. The gap to the target is analysed in [07 Target alignment](07-target-alignment.md),
and the plan to close it is in [06 Roadmap](06-roadmap.md).

## 1. Purpose

The Indic Knowledge Graph (IKG) answers questions about classical Indian texts, and every
answer can be traced to a specific passage in a specific edition by a named translator. It
combines three things:

1. A **segmented corpus** of public-domain English translations, split into citable passages.
2. A **knowledge graph** of people, deities, places, concepts and schools of thought, built from
   where they are mentioned, and exported as RDF that follows a small OWL ontology.
3. A **question-answering layer** that retrieves passages, sends them to Claude, and returns
   an answer whose citations are mapped back to passage IDs.

The defining goal is **traceability**. General-purpose retrieval-augmented generation (RAG)
systems tend to blur three distinctions that this system keeps:

| Distinction | How it is kept |
|---|---|
| Primary text vs. commentary or scholarship | Every passage has a `kind`. Answers must attribute commentary and scholarship rather than present them as the text. |
| Sanskrit concept vs. translator's English word | The gazetteer records *renderings* ("righteousness" for *dharma*), and mentions record whether they matched an alias or a rendering. |
| Which work, which speaker | Passages carry their work and translator. The prompt tells the model to keep views from different works and speakers apart. |

## 2. Users and roles

| Role | Authentication | Can |
|---|---|---|
| **Reader** | None | Search, ask cited questions (rate-limited), explore the graph, read entity and passage pages, use the JSON read API |
| **Contributor** | None, or `IKG_CONTRIBUTOR_TOKEN` if set | Upload `.txt`, `.md` or `.pdf` material. It is screened for relevance, then queued as *pending* for admin review |
| **Admin** | `IKG_ADMIN_TOKEN`, then a session cookie | Approve or reject submissions, add Project Gutenberg texts, upload directly, edit the gazetteer, trigger rebuilds; not rate-limited |
| **Developer / researcher** | Local shell | Use the `ikg` CLI: fetch, build, search, ask, analyse, evaluate, run SPARQL |

## 3. Functional requirements

### Corpus and ingestion

| ID | Requirement | Implemented in |
|---|---|---|
| FR-1 | Download registered works from Project Gutenberg, skipping files already present | `sources.fetch` |
| FR-2 | Split each edition into passages of at most ~1,500 characters that keep book, section and ordinal position, with stable, human-readable IDs | `segment.py` |
| FR-3 | Label each passage with a kind: `verse`, `narrative` (primary text), `commentary`, `introduction`, `secondary` (scholarship) | `segment.KINDS` |
| FR-4 | Give the bundled works dedicated parsers. Parse added works with a generic heading-based parser | `segment.PARSERS` |
| FR-5 | Accept uploaded `.txt`, `.md` and `.pdf` files up to 20 MB, stored under random names and never executed | `sources.save_upload`, `web/app.read_upload` |
| FR-6 | Reject uploads that are not about Indic texts and traditions, using a local model, before storing them. Reject if the model is unavailable | `screen.py` |
| FR-7 | Keep contributor uploads out of the corpus until an admin approves them | `sources.load_works` (`status`) |

### Knowledge extraction

| ID | Requirement | Implemented in |
|---|---|---|
| FR-8 | Recognise entity mentions deterministically from an editable gazetteer, matching across diacritic variants (Ráma = Rama) | `entities.Gazetteer`, `normalize.fold` |
| FR-9 | Distinguish mentions via a name or Sanskrit term (`alias`) from mentions via a translator's English word (`rendering`) | `Mention.via` |
| FR-10 | Restrict ambiguous names to the works where they are unambiguous (`works`, `aliases_by_work`) | `Gazetteer.find` |
| FR-11 | Build a weighted entity co-occurrence graph with per-work edge weights | `graph.build_cooccurrence` |
| FR-12 | Export works, editions, books, entities, renderings, aggregated mentions and co-occurrence as RDF (Turtle) that follows `ontology/ikg.ttl`, queryable with SPARQL | `graph.to_rdf`, `ikg sparql` |

### Retrieval and answering

| ID | Requirement | Implemented in |
|---|---|---|
| FR-13 | Rank passages with BM25, expand queries with entity aliases and renderings from the gazetteer, and boost passages that mention the question's entities | `retrieve.search` |
| FR-14 | Filter retrieval by work and by *primary text only* | `retrieve.search` |
| FR-15 | Answer questions using only retrieved passages, with native citations mapped to passage IDs and numbered references | `qa.ask` |
| FR-16 | Abstain (`NOT_IN_SOURCES`) when the passages do not support an answer, and report model refusals | `qa.ask` |
| FR-17 | Give the model a knowledge-graph summary for orientation, but never count it as a citation | `qa._documents`, citation filter |

### Exploration, administration and analysis

| ID | Requirement | Implemented in |
|---|---|---|
| FR-18 | Interactive graph explorer filtered by work, entity type and minimum edge weight | `/graph`, `/api/graph`, `graph.js` |
| FR-19 | Entity page: aliases, renderings, surface forms with counts, per-work counts, strongest neighbours, passages | `/entity/{id}` |
| FR-20 | Passage page: full text, recognised entities, previous/next navigation, source link | `/passage/{id}` |
| FR-21 | Admin rebuild in the background, one at a time, with a live log; the runtime reloads on success | `BuildJob` |
| FR-22 | Validate gazetteer edits before saving, and allow a reset to the bundled version | `/admin/gazetteer` |
| FR-23 | Generate a corpus analysis report (concept density, rendering vs. direct terms, Mahabharata trajectory, central figures, cross-work figures) | `analysis.py`, `ikg analyze` |
| FR-24 | Evaluate retrieval (hit@1, hit@k), optionally answers (citation precision, abstention), and support an ablation without graph expansion | `evaluate.py`, `ikg eval` |

## 4. Non-functional requirements

| ID | Requirement | Current state |
|---|---|---|
| NFR-1 Traceability | Every answer claim maps to a passage ID, edition and translator | Met through native citations and `Passage.ref` |
| NFR-2 Reproducibility | A build from the same sources and gazetteer produces the same passages, IDs and mentions | Met: segmentation and extraction are deterministic, with no model in the build |
| NFR-3 Build time | A full rebuild of ~3.1M words runs in seconds on a laptop | About 10 s |
| NFR-4 Query latency | Search responds interactively | Pages and search in under 15 ms locally. Ask is bound by the Claude API |
| NFR-5 Cost control | The public cannot run up unbounded API spend | Ask runs only on POST; repeated questions come from an answer cache; a daily budget across all visitors (`IKG_ASK_BUDGET_USD`) and a per-client hourly limit (`IKG_ASK_LIMIT`); `k` is capped at 20; the default model is the cheapest that held quality on the eval (D13) |
| NFR-6 Privacy of uploads | Upload content is not sent to third parties for screening | Screening runs on a local Ollama model |
| NFR-7 Security | Uploads are never executed, admin actions need a session, output is HTML-escaped, the admin cookie is `HttpOnly` and `SameSite=Strict`, and `Secure` over HTTPS | Met for a single-admin deployment; see the limits in 06 |
| NFR-8 Offline operation | Search, graph, analysis and retrieval evaluation work without network access or API keys, once the texts are fetched | Met |
| NFR-9 Licensing | The bundled corpus is public domain | Met: all four editions come from Project Gutenberg |
| NFR-10 Portability | Python ≥ 3.10 on Windows, macOS and Linux | Met: file-based storage, no database |

## 5. Scope

### 5.1 Prototype: implemented

- **Corpus:** four works, about 3.1 million words, 14,617 passages.

  | Work | Edition | Passages |
  |---|---|---|
  | Upanishads (Isa, Katha, Kena) | Swami Paramananda, 1919 | 268 (165 verse, 92 commentary, 11 introduction) |
  | Mahabharata, 18 books | K. M. Ganguli, 1883–96 | 11,916 |
  | Ramayana, books 1–6 | R. T. H. Griffith, 1870–74 | 1,687 |
  | Vishnu Purana, parts 1–6 | M. N. Dutt after H. H. Wilson, 1896 | 741 |

  Plus any material an admin adds or approves.
- **Entities:** a curated seed gazetteer of 76 entries (41 Person, 15 Concept, 9 Deity, 9 Place, 2
  SchoolOfThought). 75 of them occur in the current corpus.
- **Graph:** passage-level co-occurrence with no typed relations, plus RDF export.
- **QA:** single-turn questions answered by Claude with citations, filterable by work and kind.
- **Web app:** single-process FastAPI server with a reader UI, contributor uploads and an admin console.
- **Evaluation:** 28 hand-written questions (24 retrieval, 4 adversarial).

### 5.2 Target: in scope, not yet built

These come from the target system (§0). The current status of each is in
[07](07-target-alignment.md) and the plan is in [06](06-roadmap.md).

| Area | Target capability |
|---|---|
| **Corpus** | A large, private scholarly repository: books (PDF, EPUB, DOCX), research papers, lecture and video transcripts. Each is cited by page, section or timestamp, alongside the primary texts it discusses |
| **Ingestion** | Pipelines that extract, segment, classify (topic, SOT, work discussed, genre) and connect content at scale. Scholarship links to the primary passages it cites |
| **Ontology** | SOT, thinkers, works, concepts and frameworks as first-class classes. `Claim`s attributed to a thinker or SOT and grounded in passages. Relations such as *critiques*, *responds to* and *interprets*. Sanskrit terms that resist translation modelled explicitly (an extension of today's `Rendering`). Versioned and refined with SMEs |
| **Knowledge graph** | Typed, evidence-backed relations instead of co-occurrence only, queryable as RDF/SPARQL or a property graph (e.g. Neo4j) |
| **Retrieval** | Hybrid lexical + semantic (embeddings in a vector store) + graph traversal (GraphRAG). Retrieval aware of SOT and speaker |
| **Answering** | Multi-turn. Answers attribute each view to its SOT or thinker, keep traditions distinct, use the passages only, and abstain or report a knowledge gap when sources are insufficient |
| **Reliability** | Each answer sentence is checked against its citations. Unsupported claims are flagged or removed. Questions with no coverage are logged as knowledge gaps for researchers |
| **Evaluation** | Datasets written with SMEs for single-turn, multi-turn and adversarial questions. Metrics for retrieval, citation accuracy and **SOT fidelity**. Regression checks on every change |
| **Collaboration** | Curation workflows for researchers and SMEs: review queues for entities, relations and claims, corrections to answers, and ontology change proposals |
| **Platform** | Access-controlled hosting for copyrighted material, roles, audit logs, a database and job queue, a choice of hosted or local LLMs to suit data-governance needs |

### 5.3 Out of scope, even for the target

- **Taking positions of its own.** The system reports what sources and schools of thought say,
  attributed to them. It does not decide disputed doctrinal or historical questions itself.
- **Answering beyond the repository.** Questions the sources do not cover are answered as gaps,
  not filled in from the model's general knowledge.
- **Publishing copyrighted text.** Repository content is shown to authorised users in cited
  excerpts. The platform is not a channel for redistributing it.
- **General-purpose chat** unrelated to Indian civilization and intellectual traditions.

### 5.4 Not yet built in the prototype

- Sanskrit or other original-language texts, and alignment between originals and translations.
- Typed relations between entities (kinship, teacher–student, speaker–listener) and populated
  `ikg:Claim` statements. The ontology defines them but nothing generates them.
- Speaker attribution within dialogues, and coreference ("he", "the son of Pandu").
- Dense or semantic retrieval, re-ranking, and multi-turn conversation.
- Video and audio ingestion, page-level citations for books, and EPUB/DOCX input.
- Multi-user accounts, roles beyond one admin token, audit logs, and a database.
- Horizontal scaling: state is in process memory and on local disk.
- Languages other than English for the UI and queries; no Devanagari or IAST input handling
  beyond diacritic folding.
