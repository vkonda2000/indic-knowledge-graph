# Onboarding: Indic Knowledge Graph

Welcome! This guide is for a Python developer joining the project without much AI background.
Most of the code is ordinary Python (regex, dataclasses, files, FastAPI, pytest). The AI parts
are small and readable, so you will learn the AI concepts by reading and changing this code,
not by taking courses first.

## 1. What this project is

A system that answers questions about classical Indian texts (Upanishads, Mahabharata,
Ramayana, Vishnu Purana) where **every answer cites the exact passage, edition and translator
it came from**. It has three layers:

1. **Corpus:** the texts split into about 15,000 citable passages.
2. **Knowledge graph:** people, deities, places, concepts and schools of thought, linked by
   where they are mentioned, and exported as RDF (a standard graph data format).
3. **Question answering:** find the relevant passages (search), then have Claude answer from
   those passages only, with citations.

The long-term goal is a much larger system for Indian civilizational knowledge: books,
papers, lectures, schools of thought. The current code is a working prototype of its core.
The scope is in [docs/01 §0](docs/01-specification.md#0-target-system-and-this-prototype) and
[docs/07](docs/07-target-alignment.md).

## 2. Set up (day 1)

You need: Python 3.10+, Git, the GitHub CLI (`gh`, logged in with `gh auth login`), access to
the GitHub repo (ask your mentor to add you), and an
Anthropic API key with a spending limit (your mentor will arrange one).

```powershell
git clone git@github.com:vkonda2000/indic-knowledge-graph.git
cd indic-knowledge-graph
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -e ".[web,dev]"

copy .env.example .env                 # macOS/Linux: cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY and IKG_ADMIN_TOKEN (a long random string)
# for your own admin login, ask for a curator token: ikg curator add <your-name>

gh release download pinned-sources-v1 --dir data/raw --pattern 'pg*.txt' --clobber
                                       # the exact pinned texts (~18 MB); gutenberg.org has re-issued two
ikg fetch                              # verify each text against its pinned checksum
ikg build                              # build passages, index, graph (~10 s)
pytest -q                              # all tests should pass
ikg serve                              # open http://127.0.0.1:8000
```

**Local model (optional on day 1).** Uploads are checked by a local model through
[Ollama](https://ollama.com). Install it and run `ollama pull qwen3.5:9B` (6.6 GB; use
`qwen3.5:4B` on a smaller GPU and set `IKG_SCREEN_MODEL` in `.env`). Without Ollama, set
`IKG_SCREEN=off` in `.env`. The same model can answer questions without an API key:
`ikg ask "..." --model ollama:qwen3.5:9B` (docs/03 §6.1).

**Check it works:**
- Search: `ikg search "How did Karna die?"`
- Cited answer: `ikg ask "What does Yama teach Nachiketas about the Self?"`
- Retrieval quality: `ikg eval`, which should print `retrieval_hit_at_k` of about 0.96.

## 3. Ground rules

These matter more than any technique. Every reviewer will check them.

1. **Traceability.** Every claim in an answer must map to a passage ID. Never add a feature
   that produces unattributed content.
2. **Primary text ≠ commentary ≠ scholarship.** Each passage has a `kind`. Keep the
   distinction in everything you build.
3. **A translator's English word is not the Sanskrit concept.** Ganguli writes "righteousness"
   for *dharma*. The gazetteer records this as a *rendering*, not as the concept itself.
4. **The build is deterministic.** `ikg build` never calls a generative model. (The one model it
   may run, with `--embed`, is a fixed local embedding model whose name the manifest records.) If a model proposes
   something (an entity, a relation), a person reviews it and it is stored as data.
5. **No improvement claim without numbers.** If you change search or answering, run
   `ikg eval` before and after, and put both results in the PR.
6. **Secrets stay in `.env`.** It is git-ignored. Never paste keys into code, issues or chat.
7. **API calls cost money.** Tests must mock the model (see `tests/test_qa.py`). Ask your
   mentor before running `ikg eval --llm` or any large batch.
8. **Branch and PR for everything.** Never push to `main`. Keep PRs small, with tests.

## 4. What to learn, and where it is in the code

Learn each topic by reading the file next to it. The files are short.

### 4.1 Search (most important)

Answer quality depends on search finding the right passages first.

| Concept | Read | Then |
|---|---|---|
| Tokenising, stopwords, diacritic folding | [src/ikg/normalize.py](src/ikg/normalize.py) (47 lines) | Try `fold("Rāma")` in a Python shell |
| Inverted index, TF-IDF, **BM25** | [src/ikg/retrieve.py](src/ikg/retrieve.py) (107 lines) | *Introduction to Information Retrieval* (Manning, Raghavan & Schütze, free online), ch. 1 and 6, plus §11.4.3 on BM25 |
| Query expansion from the knowledge graph | `query_terms()` in `retrieve.py` | Compare `ikg eval` with `ikg eval --no-expand` |
| Measuring search: hit@1, hit@k | [src/ikg/evaluate.py](src/ikg/evaluate.py), [eval/questions.jsonl](eval/questions.jsonl) | Read the `MISS` lines `ikg eval` prints and work out why each one fails |

### 4.2 LLMs and RAG

| Concept | Read | Then |
|---|---|---|
| Prompts, system prompts, tokens, temperature | Anthropic docs: prompt engineering overview | Try prompts in Ollama: `ollama run qwen3.5:4B` |
| **RAG** (retrieval-augmented generation): retrieve, then answer only from what was retrieved | [src/ikg/qa.py](src/ikg/qa.py) (133 lines) | Ask the same question with and without *Primary text only* in the web app and compare |
| Native citations | Anthropic docs: citations | Find where `qa.py` maps `document_index` to a passage ID |
| Why models hallucinate and how we reduce it | The `SYSTEM_PROMPT` in `qa.py`, `NOT_IN_SOURCES` | Ask something the texts don't cover and see the system abstain |
| Structured (JSON) output, local models | [src/ikg/screen.py](src/ikg/screen.py) (106 lines) | Upload an off-topic file at `/contribute` and read the rejection reason |
| Testing code that calls models | [tests/test_qa.py](tests/test_qa.py), [tests/test_screen.py](tests/test_screen.py) | Note that no test needs a key or network |

### 4.3 Knowledge graphs

| Concept | Read | Then |
|---|---|---|
| Graphs, nodes, edges, weights (networkx) | [src/ikg/graph.py](src/ikg/graph.py), `build_cooccurrence()` | Explore `/graph` in the web app |
| Entity extraction from a gazetteer (name list) | [src/ikg/entities.py](src/ikg/entities.py), [src/ikg/data/gazetteer.yaml](src/ikg/data/gazetteer.yaml) | Why must the longest alias win? (`test_longest_alias_wins`) |
| RDF triples, Turtle, OWL classes and properties | [ontology/ikg.ttl](ontology/ikg.ttl), `to_rdf()` in `graph.py` | W3C *RDF 1.1 Primer* |
| SPARQL queries | [docs/04 §5](docs/04-data-model.md#5-example-sparql) | Run the examples with `ikg sparql` and change them |

### 4.4 Later: embeddings and vector search

Needed for the next phase (hybrid search). Learn what an embedding is and how cosine
similarity ranks results. Then read [src/ikg/embed.py](src/ikg/embed.py): `pip install -e ".[embed]"`,
run `ikg build --embed`, and compare `IKG_EMBED_MODEL=ollama:nomic-embed-text` after
`ollama pull nomic-embed-text`. Then [src/ikg/vectorstore.py](src/ikg/vectorstore.py): run
`docker compose up -d db` and `ikg vectors compare --store postgresql://ikg:ikg@127.0.0.1:5433/ikg`
to see what an approximate (HNSW) index gives up against exact search. Search does not use the
vectors yet (docs/08 A3).

### 4.5 Domain knowledge: enough to judge results

You don't need to be an expert; subject experts will review content. You do need to tell a
good answer from a bad one.

- **Text structure:** Mahabharata books (*parvas*) and sections; Ramayana books (*kandas*) and
  cantos (*sargas*); Upanishad verses vs. the translator's commentary. Browse passage pages in
  the app to see them.
- **Transliteration:** IAST diacritics (*ātman*, *mokṣa*, *Rāma*) and why we fold them
  to plain ASCII for matching.
- **About a dozen concepts:** dharma, karma, ātman, brahman, mokṣa, yoga, māyā, tapas, yajña,
  saṃsāra, bhakti, ahiṃsā. They are all in the gazetteer with their translators' English
  renderings.
- **Schools of thought:** Advaita, Viśiṣṭādvaita, Sāṃkhya, at the level of "what they disagree
  about".
- **pūrvapakṣa / siddhānta:** the opponent's view a text sets out in order to refute it, vs.
  the text's own conclusion. The system must never report the first as the second.
- **Translators:** the bundled editions are 19th- and early-20th-century translations. See how
  the analysis report counts each translator's word choices: [reports/corpus-analysis.md](reports/corpus-analysis.md).

## 5. Reading order for the docs

1. [docs/01 Specification](docs/01-specification.md): what the system does and its scope.
2. [docs/07 Target alignment](docs/07-target-alignment.md): where it is heading.
3. [docs/02 Architecture](docs/02-architecture.md): the components and data flow (diagrams).
4. [docs/03 Design](docs/03-design.md): read the section for whichever module you are working on.
5. [docs/06 Roadmap](docs/06-roadmap.md): where your tasks come from.

Keep [docs/05 Interfaces](docs/05-interfaces.md) open as a reference for CLI commands and
routes.

## 6. First four weeks

Each week pairs learning with a real task from the roadmap. Open a PR at the end of each task.

### Week 1: Run it, trace it, fix a small gap

- Set up (§2). Read docs 01, 07 and 02.
- **Trace one question by hand:** start from `home()` in `src/ikg/web/app.py`, follow it
  into `search()` and `ask()`, and write down every function it passes through.
- **Task: make the ontology accept renderings of a school of thought** (roadmap L14). In
  `ontology/ikg.ttl`, `ikg:renders` has range `ikg:Concept`, but the gazetteer gives
  `Advaita` (a `SchoolOfThought`) renderings too, so the RDF export contradicts the ontology.
  Widen the range (for example to the union of `Concept` and `SchoolOfThought`), and update
  [docs/04 §1](docs/04-data-model.md#1-conceptual-model).
  - Done when: a test in `tests/test_rdf_analysis_cli.py` exports a `SchoolOfThought` with a
    rendering and checks that the rendering's target is in the declared range, and CI is green.

### Week 2: Search and evaluation

- Read §4.1 and the IR book chapters.
- **Task: grow the eval set from 86 to about 120 questions** (roadmap Phase 0, the most valuable
  work for the project right now). Add to `eval/questions.jsonl`:
  - about 15 phrased with the Sanskrit term where the translation uses English (e.g. "What
    does Bhishma say about *moksha*?" where Ganguli writes "emancipation");
  - about 10 asking what a specific speaker says ("according to Krishna...");
  - about 10 across works (the same figure in the Mahabharata and the Ramayana);
  - about 10 adversarial (false premises, wrong attributions, things the texts don't contain).
- For each, find the correct passage with `ikg search` and the passage pages, and **read the
  passage to confirm it really answers the question**. `expected` takes ID prefixes at
  section or verse level, such as `mbh.b08.s090` or `upa.katha.p2.v18`, never a whole book
  (see [docs/04 §2](docs/04-data-model.md#2-identifiers)).
  - Done when: every item is verified, and the PR lists `ikg eval` and `ikg eval --no-expand`
    results before and after, with a few sentences on what changed.

### Week 3: LLMs and RAG

- Read §4.2. Try prompts in Ollama. Read the Anthropic citations docs.
- **Task: answer-quality report.** With your mentor's OK on cost, run `ikg eval --llm`. Read
  the answers in `eval/results/` and write a short report: where citations are wrong, where it
  abstained but shouldn't have, and where commentary was presented as the text.
- **Task: measure rendering precision** (roadmap L6). Renderings such as "duty" or "sacrifice"
  are ordinary English words, so some matches are not the concept. For each ambiguous
  rendering, sample 100 mentions from `data/processed/mentions.jsonl` with a fixed random seed,
  read each passage, and label it as the concept or not. Report precision per rendering, and
  propose which renderings to drop or limit to certain works.
  - Done when: the labelled sample and the report are in the PR, and any gazetteer change keeps
    `ikg eval --check eval/baseline.json` passing (or updates the baseline with a reason).

### Week 4: Knowledge graph feature

- Read §4.3, the RDF Primer, and the SPARQL examples.
- **Task: speaker attribution for the Mahabharata** (roadmap Phase 2). Ganguli marks speakers
  like `"Bhishma said, ...`: about 3,600 times, in about 24% of passages.
  - Add `speaker: str = ""` to `Passage` in `src/ikg/segment.py`.
  - In `parse_mahabharata`, detect the marker and carry the last speaker forward within a
    section.
  - **Passage IDs must not change.**
  - Add tests in `tests/test_segment.py`, and report what share of passages gets a speaker.
  - Stretch: a `speaker` filter in `retrieve.search`, and see whether it fixes eval miss
    `con-02`.

## 7. How we work

- **Branches:** `feat/...`, `fix/...`, `docs/...` from `main`. Open a PR and ask for review.
- **Before every PR:** `pytest -q`, and `ikg eval --check eval/baseline.json` if you touched
  search, entities or segmentation. CI ([.github/workflows/ci.yml](.github/workflows/ci.yml))
  runs both on every PR. Don't merge on a red check (it becomes a hard block once required
  status checks are set on `main`). If your change legitimately moves
  the numbers, or you add eval items, update `eval/baseline.json` in the same PR and say why.
- **Independent review on every PR.** Before opening a PR, run the `fable-reviewer` agent in
  Claude Code (defined in [.claude/agents/fable-reviewer.md](.claude/agents/fable-reviewer.md)).
  It reviews your branch against `main` on the Fable model, read-only, for bugs, the ground
  rules in §3, security, evidence, tests and doc drift. Fix what it finds, or explain in the PR
  why you disagree. Paste a summary into the "Review findings" section of the PR template. A
  whole-codebase and architecture review also runs periodically.
- **Docs are part of the change.** If behaviour changes, update `docs/` in the same PR. That
  includes the status checklists in [docs/06](docs/06-roadmap.md) and the technology table in
  [docs/02 §7](docs/02-architecture.md#7-technology-stack). Collaborators rely on them to know
  exactly what is implemented.
- **Match the surrounding code:** short docstrings explaining *why*, type hints, no
  unnecessary abstractions.
- **Stuck for more than an hour?** Ask. Write down what you tried; it makes help faster.
- **Weekly:** explain one design decision back to your mentor, e.g. why BM25 instead of
  embeddings, or why the build never calls a generative model. [docs/02 §6](docs/02-architecture.md#6-key-decisions)
  lists them.

## 8. Glossary

| Term | Meaning |
|---|---|
| Passage | The smallest citable unit of text (≤ ~1,500 characters) with a stable ID like `mbh.b05.s033.p02` |
| `kind` | `verse`/`narrative` = primary text; `commentary`, `introduction`, `secondary` (scholarship) = not |
| Gazetteer | The curated list of entities and their surface forms (`gazetteer.yaml`) |
| Alias / rendering | A name or Sanskrit term for an entity / a translator's English word for a concept |
| Mention | One occurrence of an entity in a passage |
| BM25 | The standard keyword-ranking formula used by search |
| hit@k | Share of eval questions where a correct passage is in the top k results |
| Ablation | Turning one feature off to measure what it contributes (`--no-expand`) |
| RAG | Retrieval-augmented generation: retrieve passages, then answer only from them |
| GraphRAG | RAG that also uses a knowledge graph to find or explain context |
| Hallucination | Model output not supported by the sources |
| Abstain | The system declines to answer because the sources don't support one (`NOT_IN_SOURCES`) |
| Embedding | A vector representing the meaning of a text, used for semantic search |
| RDF / triple | Graph data as (subject, predicate, object) statements |
| OWL / ontology | The schema for RDF: which classes and properties exist |
| SPARQL | The query language for RDF |
| SOT | School of thought (e.g. Advaita) |
| SME | Subject-matter expert: a researcher who reviews content and answers |
| IAST | The standard scheme for writing Sanskrit in Latin letters with diacritics |
| pūrvapakṣa / siddhānta | The opponent's view a text sets out to refute / the text's own conclusion |
