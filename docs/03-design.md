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
| `generic` | Level 1: Markdown `#` or `BOOK/PART/VOLUME/KANDA/PARVA n`. Level 2: `##` or `CHAPTER/SECTION/CANTO/ADHYAYA/SARGA n`. Headings are at most 100 characters | `<work>.f1.b2.s003.p01` | taken from the work (chosen on upload) | Text with no headings becomes one book with one section. Multi-file works get `f<n>` per file. Used for all uploads and added Gutenberg texts unless an admin picks another parser. For a PDF, the build reads each page (`read_pdf`), joins them with a blank line exactly as before, and passes the page of every line beside the text (`line_pages`), never inside it, so the paragraphs, chunks and IDs are exactly what the text alone gives and nothing in a document can forge a page. Each passage records the pages its paragraphs came from (`chunk_sources` mirrors `chunk`), and the PDF's printed page labels when it defines them |

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

### 5.3 Dense and hybrid retrieval (docs/08 A3)

`search(..., mode=...)` takes `bm25` (above), `dense` or `hybrid`; the default is `IKG_RETRIEVER`,
`hybrid`, and it falls back to `bm25` when the build has no embeddings. An explicit `--mode` that
can't be served is an error rather than a silent fallback.

- **Query embedding** (`embed.embed_query`): the question is embedded with the model the build
  used (the manifest's `embedding.model`, loaded on first use), with that model's query
  instruction (`QUERY_PREFIXES`; for BGE, "Represent this sentence for searching relevant
  passages: "), and normalised. Without the instruction hybrid's hit@8 is the same and its MRR
  0.013 lower.
- **Dense:** the vector store's nearest `k` passages (`vectorstore`), with the work and kind
  filters applied in the store. No expansion, no entity boost.
- **Hybrid:** the BM25 ranking (with expansion, filtered, *before* the entity boost) and the dense
  ranking, top `CANDIDATES = 50` of each, fused by reciprocal rank fusion:
  `score = Σ 1 / (RRF_K + rank)` with `RRF_K = 60`. Then the entity boost is applied to the fused
  score (`ENTITY_BOOST · top · |matched entities|`). Ties go to the earlier passage.

Fusion uses ranks, not scores, so BM25's unbounded scores and cosine similarities need no
calibration. A passage high in both lists beats one that tops only one.

**In the web app.** The query model and the vector store are loaded when a build is loaded (startup,
after a Rebuild), not on a visitor's first search. If that fails, or dense search fails later, the
site serves BM25 with a notice ("keyword (BM25) results"), `/api/search` reports `"retriever": "bm25"`,
and the next build that loads tries again. An explicit mode in the CLI (`--mode hybrid`) exits with
the reason instead. A vector store loaded from another build may return ids this build lacks; they
are skipped. Hybrid scores are fused rank scores (about 0.02–0.04), not BM25 scores.

**Reusing vectors.** `embeddings.json` records `texts_sha256` (every passage's ID and text, in
order) and `matrix_sha256`. A build with `--embed` reuses the previous vectors when the model,
the library version and `texts_sha256` match and the file is intact, so a rebuild that changes
no passage text (a gazetteer edit, an admin Rebuild, CI with cached vectors) takes seconds
instead of minutes.

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

### 6.1 Local models (`localqa.py`)

With `IKG_ANSWER_MODEL=ollama:<model>` (for example `ollama:qwen3.5:9B`), or `--model ollama:...`,
a model served by Ollama answers instead, with the same retrieval, knowledge-graph summary and
system prompt. A local model has no native citations, so the citation contract is enforced in code:

1. The passages are numbered and labelled with work, kind, translator and reference, and the model
   must reply in a JSON schema (Ollama's `format`): `abstain` and `missing`, or `sentences`, each
   with `text`, `passages` (numbers) and a short verbatim `quote`.
2. A sentence is kept only if its quote has at least 4 content words (stopwords excluded, so a
   name or "O monarch" is not enough) and occurs as whole words, case-, diacritic- and
   punctuation-insensitively, in a passage the sentence lists. Only the listed passages that
   contain the quote are cited. Anything else is dropped and counted in `dropped_sentences`; the
   answer says how many were removed. `[n]` markers and line breaks the model wrote itself are
   removed from the sentence before the real markers are added.
3. If nothing survives, the answer abstains with `NOT_IN_SOURCES`. `abstained` counts these too;
   `model_abstained` records whether the model itself said it could not answer, and the eval
   reports it separately (`adversarial_model_abstain_rate`).

What the quote check proves: each cited passage contains words the model copied, so the citation
points where the model read. It does not prove the sentence follows from the passage or that
commentary was attributed as commentary; like the Claude path, those rest on the prompt. Passage
delimiters in passage text are neutralised, so an uploaded text cannot close its passage and
relabel what follows as primary text.

`cost_usd` is 0.0, the tokens are Ollama's counts, `effort` is `n/a`. Unreachable Ollama or an
unusable reply raises `LocalAnswerError`, which the web app reports without charging the budget
and without the Ollama host name. Temperature is 0; the context window is
`IKG_LOCAL_ANSWER_CONTEXT` (16K tokens; eight passages need about 4K). An answer takes tens of
seconds on a laptop and holds a server thread meanwhile, so the hourly Ask limit matters more.

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
- **Authentication.** A login token identifies a curator (D12): `IKG_ADMIN_TOKEN` logs in as
  "admin", and each token created with `ikg curator add <name>` logs in as that name. Only the
  token's SHA-256 is stored (`data/curators.yaml`); comparisons are constant-time on bytes. A
  successful login creates a random 32-byte session ID, holding its expiry (12 h) and curator,
  in an in-memory dict; expired sessions are pruned at login. Every admin request re-checks that
  the curator is still registered, so `ikg curator remove` also ends their live sessions. Failed
  logins are recorded (with the client address, never the token) and limited per client
  (`IKG_LOGIN_FAILURE_LIMIT`, 10 an hour). A broken `curators.yaml` shows a generic message on
  the login page; the env admin token still works.
- **Provenance.** `curators.record()` appends `{time, curator, action, target, detail}` to
  `data/provenance.jsonl` for logins and logouts, public submissions (curator "public", with the
  contributor's name), works added and status changes, gazetteer saves (with a hash of the text)
  and resets, rebuilds started and finished, and curators added or removed (curator "system").
  Nothing rewrites the file. A failure to write the log never fails or undoes the action itself.
  The admin page shows the latest 30 entries; `ikg provenance` prints the log. Entries are single
  short appended lines, safe across the CLI and the web app; `curators.yaml` has no cross-process
  lock, so add or remove curators from one place at a time (single host, L12). The cookie is `HttpOnly`, `SameSite=Strict`, and `Secure` over
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

  Search is not rate-limited: it never calls a generative model, only the local embedding model
  for the query (milliseconds on the CPU). If that model or the vector store fails, search and Ask
  fall back to BM25 and the page says so, until the next build loads. `/api/ask` runs the Claude call in a worker
  thread so it does not block other requests.
- **Bounds.** `k` is clamped to 1–20 for Ask and 1–50 for search. Uploads are read in 1 MB
  chunks and stop at 20 MB. Contributor text fields are truncated (title 200, notes 2000).
  Non-admin uploads are limited per client address (`IKG_UPLOAD_LIMIT`, 10 an hour) and in total
  (`IKG_UPLOAD_GLOBAL_LIMIT`, 100 an hour). The limits and a `Content-Length` over 21 MB are
  checked in a middleware before the body is received (a route only runs after the framework has
  spooled the whole multipart body); an upload is counted only when both limits have room. A
  saved upload whose registration fails is deleted.
- **PDFs** are read by `ikg.pdftext` in a child Python process with a minimal environment (no API
  key or tokens), at most two at a time. The child is killed after `IKG_PDF_TIMEOUT` (120 s) and
  refuses more than `IKG_PDF_MAX_PAGES` (2,000) pages or `IKG_PDF_MAX_CHARS` (50 million)
  characters of text, so the parent never receives unbounded output. On POSIX its memory is
  capped at `IKG_PDF_MAX_MEMORY_MB` (2,048); on Windows memory is bounded only by pypdf 6's
  per-stream decompression limits and the text cap. On upload this runs off the event loop. The
  same limits apply in the build: an approved PDF over them fails the build, naming the work (a
  multi-volume compilation in one file may need a higher `IKG_PDF_MAX_PAGES`).
- **Admin `/api/` routes** answer a missing session with a JSON 401; admin HTML pages redirect to
  the login page. The admin page's build poller sends the admin to the login page on a 401.
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

### 11.1 Fidelity benchmark (`fidelity.py`)

`ikg eval --fidelity` scores the items in `eval/fidelity/items.jsonl` on the four properties of
[07 §2](07-target-alignment.md#2-sot-fidelity-definition-and-measurement). Each answer comes from
this system (`qa.ask`, with citations) or, with `--baseline`, from the same model with no
retrieval and a neutral prompt; a judge model grades it against the item's `must` and
`must_not` checks with structured JSON output. The judge is blind to the mode (citation markers
and the source list are removed, and the prompt is the same), and sourcing is scored
mechanically for the system as `source_coverage`. Missing, truncated or invalid verdicts fail;
empty or abstained answers fail without a judge call; results are written even if a run stops. Only
expert-approved, current-corpus items are scored; the command refuses to run otherwise unless
`--include-drafts` is given. Format, procedure and calibration rule: `eval/fidelity/README.md`.

## 12. Testing

`pytest` runs over 200 tests in about 20 s, with no network, model or API key needed. CI runs them on
Python 3.10 and 3.12 (the MCP tests need the SDK, Python 3.11+, and are skipped on 3.10):

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

## 13. MCP server (`mcp_server.py`, `sparql.py`)

`ikg mcp` exposes one loaded build as Model Context Protocol tools, built with the MCP Python SDK v2
(`MCPServer`). Over stdio there is no authentication (the client launched the process); over
Streamable HTTP (`--http`, at `/mcp`, stateless, JSON responses) every request must carry
`Authorization: Bearer $IKG_MCP_TOKEN`, compared in constant time, and DNS-rebinding protection checks
the `Host` header when bound to localhost. OAuth discovery and registration paths answer 404, so a client
asks for the token instead of starting an OAuth flow. With `IKG_MCP_PUBLIC=on` the HTTP server needs no
token and leaves out `ikg_ask`, the one tool that costs money: for ChatGPT's custom MCP servers and
claude.ai's connectors, which offer OAuth or no authentication but no static token. Calls are then logged
with the caller `public` and limited to `IKG_MCP_PUBLIC_LIMIT` an hour (default 600, one bucket for every
client); a token set alongside is ignored, with a warning at start. In any mode at most two SPARQL queries
run at once, since each is a child process.

| Tool | Returns (JSON schema) | Notes |
|---|---|---|
| `ikg_search_passages` | `query`, `retriever`, recognised `entities`, `results[]` with `passage_id`, `ref`, `work`, `edition`, `kind`, `translator`, `score`, `text` | `k` 1–20; `works`, `primary_only`, `mode` (default: `IKG_RETRIEVER`) |
| `ikg_get_passage` | the passage, its `entities`, `source_url`, `previous_id`/`next_id`, and `before`/`after` neighbours (0–2) | same edition only |
| `ikg_get_entity` | aliases, `renderings`, mentions per work, `surface_forms`, strongest co-occurrence `connections`, passage ids | by id or any alias ("Partha") |
| `ikg_sparql_query` | `columns`, `rows`, `boolean` (ASK), `truncated` | see below |
| `ikg_ask` | `text`, `abstained`, `refused`, `citations[]` (`n`, `passage_id`, `ref`, `cited_text`), `model`, `retriever`, `cost_usd` | budgeted, rate-limited |

The resource `ikg://ontology` serves `ontology/ikg.ttl` (or `IKG_ONTOLOGY`). The server's instructions
and every tool description carry the traceability rules, because the calling model sees nothing else:
cite passage ids and translators, never present commentary or scholarship as the text, keep works and
speakers apart (search every work for a story told in several), and report `NOT_IN_SOURCES` rather than
answer from general knowledge. A client's model may or may not load a skill (the Claude plugin's
`ikg-scholar` adds detail and examples), so these rules can't live only there (integrations/README.md).

- **SPARQL** (`sparql.py`): the query must parse as SELECT or ASK (every update form fails to parse as a
  query), must not contain SERVICE, and runs in a child process (`python -m ikg.sparql`) that is killed
  after `IKG_SPARQL_TIMEOUT` seconds; at most 500 rows come back, with `truncated` set.
- **Cost:** only `ikg_ask` can cost money. It is refused when the server's daily budget
  (`IKG_ASK_BUDGET_USD`) is spent or `IKG_ASK_LIMIT` questions have been asked this hour, both per server
  process: with one static token every HTTP client shares them, and `IKG_ASK_LIMIT=0` turns `ikg_ask`
  off. A failed call counts against the budget only if it may have cost money (`qa.may_have_been_billed`,
  shared with the web app): a timeout or a server error does; a rejected request, a local model or a
  retrieval failure doesn't. If dense search fails, the answer is retried with BM25, as in the web app.
  With a local answer model it costs nothing.
- **Errors** name the kind of failure, never a host, path or traceback.
- **Host checks:** bound to localhost (`127.0.0.1`, `localhost`, `::1`), the SDK refuses requests whose
  `Host` header isn't local, against DNS rebinding. Behind a reverse proxy, which forwards its public
  `Host`, bind to `0.0.0.0` (as the compose service does) and rely on the token.
- **Call log:** every call appends `time`, `transport` (`stdio` or `http`), `caller` (`local`, or `token`
  for the shared HTTP token; a client identity once OAuth arrives), `tool`, a short `summary`, `cost_usd`
  and `ok` to `data/mcp-calls.jsonl` (separate from the provenance log, which records curation).
- **Shared code:** the budget and rate limiter (`limits.py`) and the entity view (`describe.py`) are the
  web app's own, moved out of `web/app.py` so the MCP server needs no web dependency.

