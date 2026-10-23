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

## Corpus

| Work | Edition | Passages |
|---|---|---|
| Upanishads (Isa, Katha, Kena) | Swami Paramananda, 1919 (Gutenberg #3283) | 268 (165 verse, 92 commentary, 11 introduction) |
| Mahabharata, all 18 books | Kisari Mohan Ganguli, 1883–96 (Gutenberg #15474–15477) | 12,561 |
| Ramayana, books 1–6 | Ralph T. H. Griffith, 1870–74 (Gutenberg #24869) | 1,687 |
| Vishnu Purana, parts 1–6 | M. N. Dutt after H. H. Wilson, 1896 (Gutenberg #66208) | 741 |

About 3.1 million words. The Vishnu Purana is the only Purana on Project Gutenberg. Others
(Bhagavata, Markandeya, Garuda) can be added through the admin interface once a
public-domain edition is located; see `src/ikg/data/sources.yaml`.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; on macOS/Linux: source .venv/bin/activate
pip install -e ".[web,dev]"

ikg fetch                         # download the texts (~18 MB) from Project Gutenberg
ikg build                         # segment, extract entities, index, build graph + RDF (~10 s)
ikg serve                         # web app at http://127.0.0.1:8000
```

Cited answers call Claude (`claude-opus-5-5`) and need an API key: set `ANTHROPIC_API_KEY`.
Search, the graph explorer, analysis and the retrieval evaluation work without one.

## Web app

| Page | Who | What it does |
|---|---|---|
| `/` | everyone | Search passages, or **Ask** for an answer with numbered citations. Filter by work; tick *Primary text only* to exclude commentary and scholarship. Shows which entities were recognised in the question. |
| `/graph` | everyone | Interactive entity graph (Cytoscape). Filter by work, entity type and minimum shared passages. Click a node for its aliases, translators' renderings, strongest connections and passages. |
| `/entity/{id}`, `/passage/{id}` | everyone | Entity profile (surface forms, counts per work, connections, passages); full passage text with entities, previous/next navigation and source link. |
| `/contribute` | contributors | Upload `.txt`, `.md` or `.pdf` material (papers, book chapters, lecture transcripts, translations). Submissions wait in a review queue. |
| `/admin` | admin | Approve or reject submissions; add a Project Gutenberg text by ebook number; upload files directly; edit the gazetteer (entities, aliases, renderings) with validation; **Rebuild** the graph in the background with a live log. |

**Access control.** Admin login uses the `IKG_ADMIN_TOKEN` environment variable. If it is unset,
a random token is printed at startup. Set `IKG_CONTRIBUTOR_TOKEN` to require a token for
uploads too. Cited answers cost API credits, so visitors who are not logged in as admin get
`IKG_ASK_LIMIT` questions per hour per client address (default 20; `0` restricts Ask to admins).
Behind a reverse proxy the client address comes from `X-Forwarded-For`, which uvicorn trusts
only from 127.0.0.1; for a proxy on another host, set `FORWARDED_ALLOW_IPS` to its address. Sessions are `HttpOnly`, `SameSite=Strict` cookies, marked `Secure`
when served over HTTPS. Uploads are limited to 20 MB,
stored under random names, never executed, and never enter the corpus without admin approval.
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
prose (`narrative`) or verse, or `commentary`.

## Command line

```bash
ikg search "What does Yama teach Nachiketas about the Self?"       # retrieval only
ikg ask "What does Yama teach Nachiketas about the Self?" --primary-only
ikg analyze                       # writes reports/corpus-analysis.md
ikg eval                          # retrieval metrics; add --llm for answer/citation metrics
ikg eval --no-expand              # ablation without knowledge-graph query expansion
ikg sparql "SELECT ?label WHERE { ?e a ikg:Concept ; rdfs:label ?label }"
ikg stats
```

## How it works

```
Gutenberg / uploads ──► segment ──► passages (id, work, book, section, kind, translator, source)
                                      │
               gazetteer ──► entity extraction ──► mentions (alias | rendering)
                                      │
             ┌────────────────────────┼─────────────────────────┐
         BM25 index          co-occurrence graph           RDF (ontology/ikg.ttl)
             │                  (networkx)                   SPARQL
             └──── query expansion + entity boost ────┐
                                                      ▼
                      Claude with native citations → answer + passage references
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
- **Retrieval** (`retrieve.py`): BM25, with the query expanded from the gazetteer: a question
  about *dharma* also searches "righteousness" and "duty", and one about "Partha" also searches
  "Arjuna". Passages mentioning the question's entities get a boost.
- **Answering** (`qa.py`): the top passages go to Claude as documents with citations enabled,
  with a short knowledge-graph summary for orientation. The system prompt requires grounding,
  separate attribution for commentary and scholarship, no blending of views across works, and an
  explicit `NOT_IN_SOURCES` abstention. Refusals are handled, and server-side fallback is enabled.

## Evaluation

`eval/questions.jsonl` has 24 retrieval questions across all four works and 4 adversarial
questions the corpus cannot answer (for example, Hanuman in the Katha Upanishad).

| Retrieval (k = 8) | hit@1 | hit@8 |
|---|---|---|
| With graph expansion | 62.5% | 95.8% |
| Without (`--no-expand`) | 62.5% | 95.8% |

What this shows so far:

- **No net gain from expansion on this set.** On the 5 questions phrased with Sanskrit terms
  where the translations use English words (`con-*`), expansion improves hit@1 from 1/5 to 2/5
  and hit@3 from 3/5 to 4/5. On the questions phrased in the translators' own words, it costs
  one hit@1. The set is too small to call either way; a larger set written by subject experts
  is the next step.
- **One known failure, kept deliberately.** `con-02` asks about a kshatriya's dharma "according
  to Krishna". The Bhagavad Gita (Bhishma Parva, sections 25–42) loses to Santi Parva passages on
  kingship.
- **Answer-level metrics not yet measured.** `ikg eval --llm` measures citation precision,
  citation presence, false abstention and abstention on adversarial questions. It has not been
  run yet because it needs an API key.

## Analysis

`ikg analyze` writes [reports/corpus-analysis.md](reports/corpus-analysis.md). Findings include:

- **Translators mostly render dharma in English.** In Ganguli's Mahabharata, "dharma" appears
  372 times, against 2,986 English renderings ("duties", "righteousness", "duty"). In Griffith's
  Ramayana the Sanskrit word never appears. Searching for the Sanskrit term alone misses most of
  the material.
- **Dharma vocabulary is densest in the didactic books.** It peaks in the Santi Parva (28.2 per
  10,000 words), the Svargarohanika Parva (25.2) and the Anusasana Parva (22.6), against 2.5–5
  in the battle and narrative books.
- **The character graph splits into two communities.** Mahabharata figures cluster around
  Arjuna and Krishna, Ramayana figures around Rama. Narada, Vasishtha, Vishvamitra and the
  deities bridge them.

## Limitations

- The texts are 19th- and early-20th-century English translations. The system can surface
  translation choices but cannot yet compare them against the Sanskrit. The next step is
  aligning passages to Sanskrit originals from GRETIL or the Digital Corpus of Sanskrit.
- Entity matching is lexical. Epithets missing from the gazetteer are missed, and patronymics
  inflate some counts ("son of Pandu").
- Claims and schools of thought are modelled in the ontology but not yet populated. They are
  meant to be curated by subject experts through the admin workflow.
- The web app keeps sessions in memory and runs one rebuild at a time. It is built for a
  small team, not public scale.

## Tests

```bash
pytest
```

25 tests cover the edition parsers (with fixtures that reproduce each edition's quirks), entity
matching, retrieval and expansion, the graph, QA citation handling with a mocked Claude client,
and the web app end to end: contribute, review, rebuild, search, graph, and auth, upload
validation and HTML escaping.
