# 01 Specification

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
| NFR-5 Cost control | The public cannot run up unbounded API spend | Ask is rate-limited per client (`IKG_ASK_LIMIT`); `k` is capped at 20 |
| NFR-6 Privacy of uploads | Upload content is not sent to third parties for screening | Screening runs on a local Ollama model |
| NFR-7 Security | Uploads are never executed, admin actions need a session, output is HTML-escaped, the admin cookie is `HttpOnly` and `SameSite=Strict`, and `Secure` over HTTPS | Met for a single-admin deployment; see the limits in 06 |
| NFR-8 Offline operation | Search, graph, analysis and retrieval evaluation work without network access or API keys, once the texts are fetched | Met |
| NFR-9 Licensing | The bundled corpus is public domain | Met: all four editions come from Project Gutenberg |
| NFR-10 Portability | Python ≥ 3.10 on Windows, macOS and Linux | Met: file-based storage, no database |

## 5. Current scope

### In scope (implemented)

- **Corpus:** four works, about 3.1 million words, 15,257 passages.

  | Work | Edition | Passages |
  |---|---|---|
  | Upanishads (Isa, Katha, Kena) | Swami Paramananda, 1919 | 268 (165 verse, 92 commentary, 11 introduction) |
  | Mahabharata, 18 books | K. M. Ganguli, 1883–96 | 12,561 |
  | Ramayana, books 1–6 | R. T. H. Griffith, 1870–74 | 1,687 |
  | Vishnu Purana, parts 1–6 | M. N. Dutt after H. H. Wilson, 1896 | 741 |

  Plus any material an admin adds or approves.
- **Entities:** a curated seed gazetteer of 76 entries (41 Person, 15 Concept, 9 Deity, 9 Place, 2
  SchoolOfThought). 75 of them occur in the current corpus.
- **Graph:** passage-level co-occurrence with no typed relations, plus RDF export.
- **QA:** single-turn questions answered by Claude with citations, filterable by work and kind.
- **Web app:** single-process FastAPI server with a reader UI, contributor uploads and an admin console.
- **Evaluation:** 28 hand-written questions (24 retrieval, 4 adversarial).

### Out of scope (today)

- Sanskrit or other original-language texts, and alignment between originals and translations.
- Typed relations between entities (kinship, teacher–student, speaker–listener) and populated
  `ikg:Claim` statements. The ontology defines them but nothing generates them.
- Speaker attribution within dialogues, and coreference ("he", "the son of Pandu").
- Dense or semantic retrieval, re-ranking, and multi-turn conversation.
- Multi-user accounts, roles beyond one admin token, audit logs, and a database.
- Horizontal scaling: state is in process memory and on local disk.
- Languages other than English for the UI and queries; no Devanagari or IAST input handling
  beyond diacritic folding.

The phased plan for extending each of these is in [06 Roadmap](06-roadmap.md).
