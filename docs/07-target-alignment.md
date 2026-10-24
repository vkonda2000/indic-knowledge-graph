# 07 Target alignment

This document compares the target system ([01 §0](01-specification.md#0-target-system-and-this-prototype))
with what the prototype has today. Each capability is rated **Built**, **Partial** or
**Not started**, and the next step is linked to the [roadmap](06-roadmap.md).

## 1. Capability gap analysis

| # | Target capability | Status | What exists today | What is missing / next step |
|---|---|---|---|---|
| T1 | Ontology-based representation of civilizational knowledge | **Partial** | OWL ontology (`ontology/ikg.ttl`) for works, editions, books, passages by kind, entities by type, mentions, renderings, co-occurrence. `Claim`, `statedBy`, `heldBy`, `groundedIn` and `about` are defined | Thinker, Framework and Argument classes. Relations for critique and interpretation. Classes for debate structure (§3). Versioning and SME review. Roadmap Phase 2 |
| T2 | Knowledge graph of concepts, relationships, SOT, texts, thinkers, frameworks | **Partial** | 76 curated entities including concepts and 2 SOT. Weighted co-occurrence graph (75 nodes, 1,903 edges). RDF export queryable with SPARQL. Interactive explorer | Typed, evidence-backed relations. SOT and thinker coverage. Claims. Co-occurrence is too dense (0.69) to carry meaning. Phase 2 |
| T3 | QA over a repository of books, research papers and videos | **Partial** | Cited QA over 15k passages. Contributor and admin uploads of `.txt`/`.md`/`.pdf` scholarship as kind `secondary`, attributed as scholarship in answers | Ingestion of the actual repository: page-cited books, papers, video transcripts with timestamps, metadata catalogue. Phase 1 |
| T4 | LLMs, RAG, GraphRAG, semantic search | **Partial** | Claude with native citations. BM25 with query expansion from the gazetteer (aliases and renderings) and an entity boost. A graph summary in the prompt (GraphRAG-lite) | Dense embeddings + vector store, hybrid fusion, re-ranking, retrieval by traversing the graph (claims, SOT neighbourhoods). Phase 3 |
| T5 | Pipelines to ingest, process, classify and connect content at scale | **Partial** | Deterministic build pipeline (~10 s for 3.1M words). Per-edition parsers plus a generic parser. Relevance classification of uploads with a local model. Review queue | Classifying content by topic, SOT and work discussed. Linking scholarship to the primary passages it cites. EPUB/DOCX/audio/video. Incremental builds and a job queue. Phases 1 and 4 |
| T6 | Accurate, source-grounded, traceable answers | **Built** (core) | Every citation maps to a passage ID, edition and translator. Passages only. Commentary and scholarship attributed rather than presented as the text. Abstains with `NOT_IN_SOURCES`. The prompt forbids citing the graph summary and such citations are dropped, but a sentence drawn from it can remain unmarked (L20) | Page/timestamp citations for the new media. Checking each sentence against its citation (T8). Phase 3 |
| T7 | Reflect Indic traditions without ideological or cultural distortion | **Partial** | Distinctions built into the design: primary text vs. commentary vs. scholarship. Sanskrit concept vs. the translator's English word (`Rendering`, with counts per translator). Views kept per work and speaker. Prompt forbids blending | SOT-level attribution. Debate structure so an opponent's view is never reported as the author's (§3). Editions that follow Indic traditions of interpretation alongside the 19th-century European translations. A fidelity metric (§2). Phases 1–3 |
| T8 | Minimise hallucinations | **Partial** | Grounding in documents, native citations, explicit abstention, refusal handling, adversarial questions in the eval set | Check each answer sentence against its cited passage with a local model, then flag or remove unsupported sentences. Measure the unsupported-claim rate. Phase 3 |
| T9 | Identify knowledge gaps | **Not started** | Abstentions are returned to the user but not recorded | Log abstentions and low-confidence answers. Report concepts and questions with no coverage to researchers. Coverage dashboards per SOT and topic. Phase 3 |
| T10 | Evaluation datasets: multi-turn, adversarial, regression; retrieval, citation accuracy, SOT fidelity | **Partial** | 57 items (47 retrieval at section/verse level, 10 adversarial), with Sanskrit-phrased, speaker and cross-work types. hit@1/hit@k/MRR offline, by question type. Citation precision, abstention and false-abstain with `--llm`. Ablation switch. CI regression gate on hit@1/hit@k against a committed baseline (`eval/baseline.json`) | Multi-turn items. SOT-fidelity items and metric. SME-written gold answers. Gating answer-level metrics. Grow to about 150 items in Phase 0, then a larger set written with SMEs |
| T11 | Work with researchers and SMEs to translate frameworks into computational structures | **Partial** | Admin gazetteer editor with validation. Review queue for submissions. Entity pages show surface forms and renderings for review | Role-based curation UI for entities, relations, claims and ontology change proposals. Ways to correct answers. Phases 2 and 4 |
| T12 | Scalable system design | **Partial** | Clean separation between the core library and its interfaces. Deterministic, stable passage IDs that allow incremental builds. Millisecond queries at 15k passages | Database, job queue, vector store, graph store, multi-worker web tier, authentication. Phase 4 |
| T13 | Knowledge representation tech: RDF, OWL, SPARQL, Neo4j | **Partial** | RDF + OWL + SPARQL (rdflib). GraphML for networkx | A persistent triple store or Neo4j for queries at scale (decision in Phase 4). A stable namespace |
| T14 | Vector databases, embeddings | **Not started** | A local embedding model is available (`nomic-embed-text` via Ollama) | Phase 3 |
| T15 | Large, domain-specific document collections | **Built** (at prototype scale) | 3.1M words across 4 works with edition-specific parsing that keeps book, section and verse structure | Corpus size and format variety. Phase 1 |

**Summary:** 2 Built, 11 Partial, 2 Not started. The prototype covers the architecture end to
end, so every target capability has a place in the existing design. The largest gaps are in
the content (the actual repository and media types), relational knowledge (typed relations,
SOT, claims), semantic retrieval, and the reliability loop (checking sentences against
citations, logging gaps, measuring SOT fidelity).

## 2. SOT fidelity: definition and measurement

The target asks that answers reflect each school of thought faithfully. To make that
measurable, the system treats it as four properties, each with its own check:

| Property | Meaning | Check |
|---|---|---|
| **Attribution** | Every view is attributed to the SOT, thinker, text or speaker that holds it | SME-labelled gold attributions; automatic check that each attributed sentence cites a passage from that SOT's sources |
| **Separation** | Views of different schools (e.g. Advaita and Vishishtadvaita on *maya*) are not blended into one composite view | Contrastive eval items; the answer must keep the positions apart, judged by SMEs or a rubric-guided LLM judge that is checked against SME labels |
| **Own terms** | Concepts are explained in the school's own vocabulary. Sanskrit terms are not silently replaced by approximate English equivalents | Uses the `Rendering` model. Items where a careless answer would substitute "soul" for *ātman* or "God" for *Brahman* |
| **Debate position** | A *pūrvapakṣa* (the opponent's view a text sets out in order to refute it) is never reported as the text's own conclusion (*siddhānta*) | Passages tagged by debate role (§3); eval items built from such passages |

Each property gets a rate between 0 and 1 in `ikg eval`. Together they form the SOT-fidelity
score and are tracked as regression metrics alongside retrieval and citation accuracy.

## 3. Ontology extensions the target needs

These extend `ontology/ikg.ttl` without breaking the existing classes:

| Addition | Purpose |
|---|---|
| `ikg:Thinker` (subclass of `Person`) with `ikg:authored` linking to `Work` | Modern and classical authors as first-class nodes |
| `ikg:Framework` (subclass of `Entity`) | Named intellectual frameworks or theses, from a classical SOT or a modern scholar |
| `ikg:Claim` populated, plus `ikg:critiques`, `ikg:respondsTo`, `ikg:interprets` (between Claims) and `ikg:hasDebateRole` (`purvapaksha` \| `siddhanta`) | The structure of arguments within and between works; supports the debate-position property of §2 |
| `ikg:citesPassage` linking a `Secondary` passage to primary `Passage`s | Connects scholarship to the verses it discusses, e.g. a book chapter quoting Katha Upanishad 1.2.18, so answers can show both |
| `ikg:Rendering` with range widened to `ikg:Entity`, plus `ikg:renderingNote` | Supports the idea of Sanskrit terms that resist translation (*Sanskrit Non-Translatables*, Malhotra & Satyanarayana Dasa Babaji, 2020); records per translator how and why a term was rendered |
| `ikg:Segment` with `ikg:page`, `ikg:startTime`, `ikg:endTime` | Page citations for books, timestamp citations for lectures and videos |
| Provenance (`prov:wasAttributedTo`, `prov:generatedAtTime`) on curated facts | Records which SME or model proposed a fact and who approved it |

## 4. Constraints the target adds

| Constraint | Effect on the design |
|---|---|
| **Copyrighted, private corpus** | Authentication on every route that serves text. Excerpt-length display limits. No public bulk API. Audit log. Repository files stored outside the web root. Admin-added Gutenberg texts stay separate from the private repository |
| **Data governance for LLM calls** | Configure the hosted model's data-retention settings, or route sensitive workloads to local models. The `qa.ask` provider interface (Phase 3) and the existing local screening model make this practical |
| **SME-centred workflow** | Facts produced by models (entities, relations, claims, classifications) enter the graph only after an SME approves them, with provenance. This is the same approval pattern used for uploads today |
| **Reproducibility at scale** | Keep the build deterministic. Store approved model output as data and replay it during the build rather than calling models in the build |
| **Video and audio** | Local speech-to-text (e.g. Whisper) with timestamps. A transcript is a `Secondary` source cited as *title @ mm:ss* |
