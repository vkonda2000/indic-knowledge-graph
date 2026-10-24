# 03 Design

This document describes how each module works and why. The data formats it refers to are
specified in [04 Data model](04-data-model.md).

## 1. Source registry (`sources.py`)

Two YAML registries are merged:

- `src/ikg/data/sources.yaml`: bundled, curated works (`bundled=True`, always approved).
- `data/works.yaml`: works added through the admin or contributor UI.

`load_works()` returns approved works only. `load_works(include_unapproved=True)` adds
`pending` and `rejected` ones for the admin page. All writes to `data/works.yaml` take a
module-level `threading.Lock`, so concurrent requests in the single server process can't lose
updates. Work IDs are slugs of the title, made unique with a numeric suffix.

Each work has a list of files. Each file is either `{gutenberg: <ebook number>}`, downloaded
to `data/raw/pg<n>.txt`, or `{upload: <uuid>.<ext>, original_name: ...}`, stored in
`data/uploads/`. `strip_gutenberg()` removes the Project Gutenberg header and licence between
the `*** START` and `*** END` markers.

## 2. Segmentation (`segment.py`)

### 2.1 Passage model

A passage is the smallest citable unit. It records its work (the text), its edition (the
translation, D10), book, section, ordinal, `kind`, translator, source URL, and a `locator`
giving its position in the edition for citations (D11; keys per parser in
[04 §4](04-data-model.md#4-record-schemas)). `segment_work` sets `work` and `edition` from the
registry entry after parsing, so parsers only build IDs and locators. `Passage.ref` builds the human citation, for example
*"Upanishads, Katha Upanishad, Part 1, Verse 19 (tr. Swami Paramananda)"*. It appends
`[commentary]` or another kind tag for non-primary text and uses "by" instead of "tr." for
scholarship.

### 2.2 Chunking

`paragraphs()` groups lines into blank-line-separated paragraphs. `chunk()` packs paragraphs
into chunks of at most `MAX_CHARS = 1500` characters. A paragraph longer than that is split
at sentence boundaries by `_split_long()`, using a regex that ends a sentence after `.!?;`
(optionally followed by a closing quote or bracket) when the next sentence starts with a
capital or an opening quote. Chunks never cross a section boundary, so a passage always
belongs to exactly one section.

### 2.3 Edition-specific parsers

Each bundled edition has a different layout, so each has a parser keyed by `work.parser`:

| Parser | Structure recognised | Passage ID | Kind | Edition quirks handled |
|---|---|---|---|---|
| `mahabharata` | `BOOK n` → parva name → `SECTION XII` *or* a bare Arabic numeral | `mbh.b05.s033.p02` | narrative | Books 8–11 and 16–18 number sections with bare numerals. Only the *next* number in sequence starts a section, so stray numerals in the text don't split it. Repeated running heads are skipped A `FOOTNOTES` block after a parva's last section is skipped until the next `BOOK` or `SECTION` heading (its numbered notes would otherwise look like section numbers). |
| `ramayana` | `BOOK IV.` → `Canto XII. <title>` | `ram.b4.c012.p01` | verse | Starts at the BOOK heading just before the first canto, skipping the table of contents. Stops at `APPENDIX.` Strips `(12)` note markers. Keeps line breaks inside verse |
| `upanishads` | Upanishad name → `Part First` → Roman verse number → `Peace Chant` | `upa.katha.p1.v19`, `.c1` for commentary, `.intro01`, `.peace` | verse / commentary / introduction | The first paragraph after a verse number is the verse and the rest is commentary. The first occurrence of an Upanishad's name opens its introduction, the second opens its text. Peace chants split at "PEACE!" Part headings are matched case-insensitively (the Kena's last is "Part fourth"). |
| `vishnu_purana` | `PART I.` → `SECTION XII.` | `vp.b1.s012.p01` | narrative | Stops at the footnotes (`[1] `). Strips `[n]` markers. Section numbers are forced to increase, to work around a typo in Part V |
| `generic` | Level 1: Markdown `#` or `BOOK/PART/VOLUME/KANDA/PARVA n`. Level 2: `##` or `CHAPTER/SECTION/CANTO/ADHYAYA/SARGA n`. Headings are at most 100 characters | `<work>.f1.b2.s003.p01` | taken from the work (chosen on upload) | Text with no headings becomes one book with one section. Multi-file works get `f<n>` per file. Used for all uploads and added Gutenberg texts unless an admin picks another parser |

`read_source()` extracts PDF text with `pypdf` and runs `strip_gutenberg` on text files.
`segment_work()` sets `work_title` on each passage. The bundled works use fixed display labels
(`WORK_LABELS`); added works use their title.

**Design rule:** passage IDs are derived only from document structure, never from a counter
across the corpus. Rebuilding with an extra work therefore does not change any existing ID,
so citations and evaluation expectations stay valid (NFR-2).

## 3. Normalisation (`normalize.py`)

- `fold()` maps curly quotes and dashes to ASCII, decomposes to NFKD, and drops combining
  marks: *Rāma*, *Ráma* and *Rama* all become `Rama`. Case is kept, so case-sensitive aliases
  still work.
- `tokenize()` folds, lowercases, splits on `[a-z0-9]+`, and removes stopwords. The stopword
  list includes archaic English common in Victorian translations (`thou`, `thee`, `thy`, `hath`,
  `doth`, `art`, `unto`, `ye`).

## 4. Entity extraction (`entities.py`)

### 4.1 Gazetteer

Each entity has an `id`, a `type` (Person, Deity, Place, Concept, SchoolOfThought), a `label`,
an optional `iast` name, and four kinds of surface form:

| Field | Matching | Recorded as |
|---|---|---|
| `aliases` | Case-insensitive | `via=alias` |
| `aliases_cs` | Case-sensitive (e.g. `Self` for *atman*, so that "self" does not match) | `via=alias` |
| `renderings` | Case-insensitive; the translator's English word | `via=rendering` |
| `aliases_by_work` | Case-insensitive, only inside the named work | `via=alias` |

`works: [...]` limits all of an entity's forms to those works. For example, `bharata_ram`
matches only in the Ramayana, because in the Mahabharata "Bharata" is usually a form of
address meaning "descendant of Bharata".

`Gazetteer.from_yaml()` checks the structure, unknown fields, duplicate IDs and field types
(`type` is one of `ENTITY_TYPES`; alias, rendering and works fields are lists of non-empty
strings, so `aliases: krishna` can't become one alias per letter), and raises
`ValueError` with the position of the bad entry. Building the lookup tables also rejects a
surface form claimed by two different entities (§4.2). The admin editor uses it so that an invalid
gazetteer is never saved.

### 4.2 Matching

All surface forms are folded and compiled into two alternation regexes, one case-insensitive
and one case-sensitive:

- Alternatives are **sorted longest first**, so `Krishna-Dwaipayana` (Vyasa) wins over `Krishna`.
- Boundaries are `(?<![\w-])` and `(?![\w-])`, so hyphenated compounds are not split.
- Spaces in multi-word forms match any whitespace, including line breaks.
- The case-insensitive pass runs first. The case-sensitive pass skips spans already taken,
  so matches never overlap.
- A match whose entity is scoped to other works is discarded.

Each surface form maps to exactly one entity: the lookup tables are keyed by the folded
surface, and hold one pattern per entity that uses the form. Two entities may share a form
only if their `works` scopes are disjoint (e.g. Rama's brother Bharata in the Ramayana and a
King Bharata in the Mahabharata). Then a match in a passage takes the entity whose scope
contains the passage's work, and a match in a question (no work) is ambiguous and links to
neither. Any other clash raises `ValueError` naming both entities, so the admin editor
refuses to save it and the build fails. This includes a case-sensitive form (`aliases_cs`)
that another entity also lists case-insensitively, because the case-insensitive pass would
always take that span. The same form listed twice under one entity keeps the wider scope.

### 4.3 Outputs

`extract()` produces one `Mention(entity, passage, work, surface, via)` for each match:
66,411 for the current corpus. Mentions are the single source for the graph, the RDF
mention counts, the entity pages' surface-form tables, and the retrieval entity boost.

## 5. Retrieval (`retrieve.py`)

### 5.1 Index

`BM25Index` is an in-memory inverted index (`term → [(doc, tf)]`) with document lengths, built
from `tokenize(passage.text)`. Parameters are `k1 = 1.5` and `b = 0.75`, and

```
idf(t) = ln(1 + (N − df + 0.5) / (df + 0.5))
score(d) = Σ_t  w_t · idf(t) · tf·(k1+1) / (tf + k1·(1 − b + b·|d|/avgdl))
```

The index is pickled to `bm25.pkl` (~30 MB). The pickle also holds the passages, so loading
the runtime needs only this file plus the mentions and graph.

### 5.2 Query construction

1. Question tokens get weight `w = 1.0`.
2. **Gazetteer expansion:** for each entity detected in the question, every alias, rendering
   and work-specific alias is tokenised and added with weight `EXPANSION_WEIGHT = 0.4`, unless
   the token is already present. A question about *dharma* therefore also matches
   "righteousness" and "duty", and "Partha" also matches "Arjuna".
3. Score all passages that contain any term.
4. **Entity boost:** each passage gets `ENTITY_BOOST · top_score · |entities in question ∩
   entities in passage|` added, with `ENTITY_BOOST = 0.15`.
5. Sort, then apply the filters (`works`, `primary_only` = kind in `verse`/`narrative`) while
   collecting the top `k`.

Filtering happens after scoring, so a filtered query still scores the whole index. At 15k
passages this takes milliseconds.

`expand=False` turns off steps 2 and 4. `ikg eval --no-expand` uses it for the ablation.

## 6. Cited question answering (`qa.py`)

1. Retrieve the top `k` passages (§5) and detect the entities in the question.
2. Build a **knowledge-graph summary** with `graph_context()`. For each question entity it
   lists the works it appears in, with counts, and its eight strongest co-occurring entities.
3. Send to Claude: by default `claude-haiku-5-5` at `effort=low` (`IKG_ANSWER_MODEL`,
   `IKG_ANSWER_EFFORT`, or `--model`/`--effort`), `max_tokens=16000`. The server-side refusal
   fallback (`fallbacks: "default"`) is sent only to models that accept it (Opus 5.5,
   Sonnet 5.5); Haiku 5.5 has none, so a refusal there is shown as a refusal. The request contains:
   - one `document` block per passage, with `citations.enabled`, title `[<passage id>] <ref>`,
     and context `passage_id=…; work=…; kind=…; translator=…`
   - the graph summary as a plain text block, labelled *not a source*. It is not a document:
     the API requires citations on all documents or none, and a citable summary would let a
     sentence drawn from it lose its citation and still look grounded
   - the question.

   Why Haiku 5.5 at low effort: on the eval set it matched or beat Opus 5.5 at high effort on
   every answer metric at 1/44 of the cost ([06 §1](06-roadmap.md#1-measured-baseline), D13).
4. The system prompt requires the model to:
   - use the passages only
   - attribute commentary and scholarship instead of presenting them as the text
   - not attribute a translator's English word to the Sanskrit original
   - keep different works and speakers separate
   - never state anything only the graph summary supports
   - reply `NOT_IN_SOURCES` on the first line when the passages are insufficient.
5. Post-processing:
   - Each citation's `document_index` maps back to a hit; only passages are documents.
   - Passages are numbered in order of first citation, `[n]` markers are added after each text
     block, and a *Sources* list is appended.
   - `abstained` is set when the text starts with `NOT_IN_SOURCES`.
   - `refused` is set when `stop_reason == "refusal"`; `truncated` when it is `max_tokens`,
     and the answer then says it was cut off.
   - `input_tokens`, `output_tokens` (thinking included) and `cost_usd`, estimated from
     `config.PRICES`, are recorded on the answer. `ikg ask` prints them and `ikg eval --llm`
     sums them.

If retrieval returns nothing, the answer abstains without calling the API.

## 7. Knowledge graph (`graph.py`)

### 7.1 Co-occurrence graph

- **Nodes:** entities with at least one mention. Attributes are `type`, `label`, `mentions`,
  and `works` (a string like `mahabharata:812;ramayana:40`).
- **Edges:** an undirected edge between every pair of entities mentioned in the same passage.
  `weight` is the number of shared passages; `weight_<work>` is the same count per work.

The graph is stored as GraphML. Today it has 75 nodes and 1,903 edges. It feeds the explorer
(`/api/graph`), entity neighbours, the QA graph summary, and the analysis report.

### 7.2 RDF export

`to_rdf()` writes Turtle following `ontology/ikg.ttl`, under the placeholder namespaces
`https://example.org/ikg#` (vocabulary) and `https://example.org/ikg/data/` (instances):

| Instances | From |
|---|---|
| `ikg:Work`, `ikg:Edition` (translator, year, source URLs) | Work registry |
| `ikg:Book` (`partOf` work, `fromEdition`), one per edition and book | Passages |
| `ikg:Verse`/`Narrative`/`Commentary`/... passages | Only with `ikg build --with-passages` |
| Entities typed `ikg:Person`, `ikg:Concept`, ..., with `iast` and `alias` | Gazetteer |
| `ikg:Rendering` (`renders` concept, `surface`, `fromEdition`, `count`), one per edition, concept and word actually used | Mentions with `via=rendering` |
| `ikg:Mention` aggregated per (entity, edition and book, via), with `count` | Mentions |
| `ikg:coOccursWith` (unweighted) | Graph edges |

Mentions are aggregated per book rather than per passage to keep the default export small
(11k lines). `ikg:Section`, `ikg:Claim`, `statedBy`, `heldBy`, `groundedIn` and `about` are
defined in the ontology but not yet populated.

## 8. Upload screening (`screen.py`)

1. `extract_text()` checks the extension, decodes text files as UTF-8 (replacing bad bytes),
   and reads PDFs from memory with `pypdf`. Unreadable PDFs raise `ValueError`.
2. Text shorter than 200 characters (after collapsing whitespace) is rejected without a model
   call. This covers scanned PDFs without a text layer.
3. `excerpts()` takes three evenly spaced 2,000-character windows (start, middle, end), so a
   relevant opening cannot hide unrelated content.
4. The title, the submitter's notes and the delimited excerpts are sent to Ollama `/api/chat`
   with:
   - a JSON schema `{related: bool, reason: string}`
   - temperature 0 and thinking off
   - a system prompt that defines what is in and out of scope and says the submission is
     untrusted data whose instructions must be ignored.
5. Unrelated material is rejected and the reason shown. Related material is stored with
   `screening = "<model>: <reason>"`.
6. A connection error, an HTTP error, invalid JSON or a missing `related` field raises
   `ScreeningUnavailable`. The web layer rejects the upload (fail closed) and logs the cause.

The web layer runs the blocking call in a thread pool so it does not stall the event loop.
Measured on `qwen3.5:9B`, RTX 5080: 0.6–2 s per check once the model is loaded, about 40 s
for the first check while it loads. 8 of 8 hand-picked cases were classified correctly,
including two prompt-injection attempts.

## 9. Web application (`web/app.py`)

- **App factory.** `create_app()` builds the FastAPI app, loads the `Runtime`, and keeps all
  mutable state (sessions, rate limiter, build job) in closures. Each test gets an isolated app.
- **Corpus not built.** If loading fails, `state["error"]` holds the reason, pages show a
  notice, and corpus routes return 503.
- **Authentication.** Tokens are compared with `secrets.compare_digest` on UTF-8 bytes (on
  `str` it raises for non-ASCII input). A successful
  login creates a random 32-byte session ID in an in-memory dict with a 12 h expiry; expired
  sessions are pruned at login. The cookie is `HttpOnly`, `SameSite=Strict`, and `Secure` over
  HTTPS. `SameSite=Strict` is the CSRF defence for admin POSTs.
- **Ask spends money, so it is guarded in four ways.**
  - *POST only, same site only.* `GET /?mode=ask` fills in the form but never calls the API,
    so links, link previews and crawlers cost nothing. Submitting the form POSTs. A browser
    request from another site (an auto-submitted form or a `fetch`, detected by
    `Sec-Fetch-Site` or `Origin`) is refused, so other pages can't spend visitors' quotas.
    Clients that send neither header (curl, scripts) are allowed and limited per address.
    Questions are cut to 2,000 characters on both routes.
  - *Answer cache.* `AnswerCache` keeps up to `IKG_ANSWER_CACHE_SIZE` (1,000) recent answers,
    keyed by the question (case and spacing folded), `k`, works, `primary_only`, model, effort
    and the build generation. A hit makes no API call and uses no budget or rate limit. The
    cache is cleared on every corpus reload, and an answer that finishes after a rebuild is
    not stored, because answers cite passages of the build they came from. Truncated or
    refused answers and errors are not cached.
  - *Daily budget.* `SpendBudget` adds up the estimated cost of every Ask (admins included) per
    UTC day and stops Ask at `IKG_ASK_BUDGET_USD` ($5). Questions that match no passages cost
    nothing (no API call). Requests the API rejects or never received (bad request, auth,
    connection) are not charged; a timeout, server error or unpriced model is charged $0.25,
    since it may have been billed. The check runs before the call and the charge after, so
    concurrent requests can overshoot slightly (L32).
  - *Rate limit.* `RateLimiter` keeps a one-hour sliding window per client IP for non-admins,
    and forgets clients idle for an hour so memory stays bounded.

  Search is not limited: it never calls a model. `/api/ask` runs the Claude call in a worker
  thread so it does not block other requests.
- **Bounds.** `k` is clamped to 1–20 for Ask and 1–50 for search. Uploads are read in 1 MB
  chunks and stop at 20 MB. Contributor text fields are truncated (title 200, notes 2000).
- **Background rebuild.** `BuildJob` uses a non-blocking lock, so only one rebuild runs at a
  time. It runs `build()` in a daemon thread, appends log lines to a list that the admin page
  polls through `/api/build/status`, and on success swaps in a new `Runtime`. Requests in
  flight keep the old runtime object, so there is no partial state. `build()` itself also takes
  the cross-process build lock and writes all-or-nothing ([02 §3.1](02-architecture.md#31-build-offline-deterministic)),
  so a failed rebuild leaves the previous build in place.
- **Rendering.** Jinja2 autoescaping is on for all HTML templates, so user-supplied titles and
  notes are escaped (covered by a test). The graph page loads Cytoscape 3.30.2 from jsDelivr;
  `graph.js` builds DOM nodes with `textContent`, never `innerHTML`.
- **Errors.** `HTTPException` is rendered as JSON under `/api/` and as `error.html` elsewhere.
  A 303 is used internally to redirect to login.

## 10. Analysis (`analysis.py`)

`ikg analyze` writes `reports/corpus-analysis.md` with:

1. Concept density per 10,000 words for each work, split into direct Sanskrit term vs.
   translator rendering. Example: Ganguli writes *dharma* 340 times but its renderings 2,832
   times.
2. Concept density across the 18 books of the Mahabharata.
3. Central figures per work: weighted degree in a Person/Deity co-occurrence graph, plus the
   strongest pairs.
4. Figures who appear in at least three works.

## 11. Evaluation (`evaluate.py`)

`eval/questions.jsonl` holds hand-written items. A `retrieval` item lists `expected` passage-ID
*prefixes* at section or verse level (e.g. `mbh.b08.s090`, `upa.katha.p2.v18`), each checked by
reading the passages; only a primary-text passage (verse, narrative) under one of them counts as
a hit, not commentary on it; a long discourse is listed section by section (Bhishma's Rajadharma is
`mbh.b12.s055`…`s130`). An `adversarial` item has no answer in the corpus.

| Metric | Mode | Definition |
|---|---|---|
| `retrieval_hit_at_1` / `hit_at_k` | Offline | Top-1 / any of top-k passage IDs start with an expected prefix |
| `retrieval_mrr` | Offline | Mean over items of 1 / rank of the first matching passage in the top k (0 if none) |
| `retrieval_by_type` | Offline | hit@1, hit@k and MRR per question type, from the item ID prefix (`skt`, `spk`, `xw`, ...) |
| `cites_expected` | `--llm` | At least one citation matches an expected prefix |
| `citation_precision` | `--llm` | Share of citations that match expected prefixes |
| `has_citations` | `--llm` | The answer has at least one citation |
| `false_abstain_rate` | `--llm` | Answerable questions where the model abstained |
| `adversarial_abstain_rate` | `--llm` | Adversarial questions where the model abstained |

Each run is written to `eval/results/run-<timestamp>.json` (git-ignored). Current offline
results and their interpretation are in [06 §1](06-roadmap.md#1-measured-baseline).

**Regression gate.** `eval/baseline.json` is committed and holds the metrics the project
promises not to lose, with the `k` they were measured at. `ikg eval --check eval/baseline.json`
compares the run's summary with it through `evaluate.regressions()` and exits non-zero if any
metric got worse. Higher is better for every metric except `false_abstain_rate`. The item
counts `n_retrieval` and `n_adversarial` are gated too, so deleting a failing question doesn't
pass. A metric missing from the run, or a non-numeric baseline value, also fails the check. CI runs the check on every pull request
([02 §5](02-architecture.md#5-deployment)). When a change legitimately moves a number, or the
eval set grows, update the baseline in the same PR and explain why.

## 12. Testing

`pytest` runs 97 tests in about 3 s, with no network or API key needed. CI runs them on
Python 3.10 and 3.12:

- Unit tests: normalisation, segmentation (all parsers, using fixture text), gazetteer
  matching, retrieval and graph, QA citation mapping with a fake client, screening with a
  fake model and fake HTTP.
- End-to-end web tests: each runs in a temporary `IKG_DATA` directory and covers
  contribute → screen → approve → rebuild → search → graph → entity → passage. They also
  cover admin auth, escaping, gazetteer validation, rate limits and upload limits.

- RDF, analysis and CLI tests (`tests/test_rdf_analysis_cli.py`): key triples of the RDF
  export, and a check that every `ikg:` term it uses is declared in `ontology/ikg.ttl`; the
  analysis report's sections and counts; `ikg stats` and `ikg search` on a small corpus built
  on disk; surface-form clash rules; checksum pinning in `fetch`; the eval regression check
  and the committed baseline.

Not covered by tests: `ikg fetch`, `build`, `ask`, `sparql` and `serve` as CLI commands
(the functions behind `build` and `ask` are tested), and parsing of the full Gutenberg texts,
which the CI eval gate exercises instead.
