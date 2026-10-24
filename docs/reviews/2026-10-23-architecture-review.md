# Architecture and code review, 2026-10-23

Independent review of `main` at `61533a6`, run on Fable through the `fable-reviewer` practice
(ONBOARDING §7). Read-only. Findings are tracked as limitations L19–L30 in
[06 §2](../06-roadmap.md#2-known-limitations) and placed in the phases there. The main session
re-checked the findings marked **verified** in the code; the others are the reviewer's
analysis and are still to be confirmed when they are worked on.

## Code findings

| # | Sev. | Where | Finding | Failure scenario | Fix | Roadmap |
|---|---|---|---|---|---|---|
| A1 | High | `pipeline.Runtime.load` | **Verified.** The runtime loads the *current* gazetteer, not the one the build used | Admin removes an entity or resets the gazetteer, doesn't rebuild, restarts: passage, entity and Ask views raise `KeyError` for mentions of the removed entity | Snapshot the gazetteer into `processed/` with a build manifest; refuse mismatched artefacts | L19 |
| A2 | High | `qa._documents`, `qa.ask` | **Verified.** The graph summary is sent as a document with citations enabled. Citations to it are dropped, but the sentence stays in the answer unmarked | An answer sentence drawn from co-occurrence statistics appears with no citation, against NFR-1 | Disable citations on the summary or move it out of the documents; count dropped citations | L20 |
| A3 | High | `web/app.py` `home`, `RateLimiter` | **Verified** (GET-triggered Ask). Spend limits are per client address only; Ask runs on `GET /?mode=ask`; idle limiter keys never expire | Crawlers or link previews trigger paid calls; many IPv6 addresses bypass the per-address limit; limiter memory grows | POST-only Ask, a global budget, evict idle keys, log token usage | L21 |
| A4 | Medium | `Gazetteer.from_yaml` | No field-type or `type` checks | `aliases: krishna` (a string) becomes seven one-letter aliases; `type: Persn` is accepted | Validate list-of-string fields, the `type` enum and `aliases_by_work` shape | L22 |
| A5 | Medium | `/contribute`, `screen.py`, `segment.py` | Unauthenticated uploads run pypdf on up to 20 MB in the web worker, with no upload rate limit | A crafted PDF pins CPU; repeated uploads tie up Ollama | Rate-limit uploads; parse PDFs in a subprocess with a timeout and page cap | L23 |
| A6 | Medium | `pipeline.build` | Artefacts are written in place, one after another; CLI and web builds don't share a lock | A crash mid-build leaves an index and mentions from different builds | Build into a temporary directory and rename; a shared lock file | L19 |
| A7 | Medium | `sources.fetch` | Downloads are not pinned by checksum | A Gutenberg re-edit changes sections and IDs silently; NFR-2 can't be verified | `sha256` per file in `sources.yaml`, checked on fetch and build | L24 |
| A8 | Medium | `web/app.py` login and contribute | **Verified.** `secrets.compare_digest` on `str` raises `TypeError` for non-ASCII input | A token like `pässword` returns 500 instead of "Invalid token" | Compare encoded bytes | L25 |
| A9 | Low | `/api/build/status` | An expired session gets a 303 to HTML; the admin poller loops silently | Rebuild status never updates after the session expires | Return 401 JSON under `/api/` | L26 |
| A10 | Low | `qa.py`, `app.py`, `sources.py` | `max_tokens` truncation not flagged; `"primary_only": "false"` is truthy; the Gutenberg start marker on the last line raises | Truncated answers shown as complete; filter wrongly on | Handle each case | L26 |

## Design findings

| # | Finding | Consequence | Recommendation | Roadmap |
|---|---|---|---|---|
| B1 | Work and edition are the same thing in the data model (`Passage.work`, IRIs, gazetteer scoping, graph weights, eval prefixes) | A second translation of the same text becomes a new "work", which breaks parallel translations and C2 | Add `edition` to `Passage`; scope the gazetteer by work; carry the edition in IDs and IRIs. Decide before Phase 1 | L27 |
| B2 | The `.pNN` ordinal in passage IDs depends on chunking parameters | Changing `MAX_CHARS` or the sentence splitter renumbers every ID that eval and fidelity items refer to; pages and timestamps don't fit | Separate the locator (book/section/verse, page, time) from the chunk ID; freeze a v1 scheme; record chunking parameters in the manifest | L28 |
| B3 | Eval expectations are book-level prefixes (`mbh.b12` is the whole Santi Parva) | hit@8 = 0.958 measures "a passage from the right book", so the expansion ablation can't show much either way | Section- or verse-level expectations; MRR; then grow the set | L1 (extended) |
| B4 | The RDF shape doesn't yet support Phase 2: passages omitted by default, `Section` never instantiated, `Rendering` not tied to an edition, `ordinal` never exported, placeholder namespace | Claims curated against these IRIs would need migrating | Fix these before SME curation writes claims | L13, L29 |
| B5 | Phase 1 (copyrighted material) and Phase 2 (curation provenance) come before Phase 4 (identity, roles, audit) | Provenance ("who approved") can't be recorded correctly with one shared admin token | Pull forward per-curator identity and an append-only provenance log; keep copyrighted ingestion private until auth exists | L30 |
| B6 | Speaker attribution is deterministic and small, and fidelity "attribution" items depend on it | Waiting until Phase 2 delays C1 | Consider moving it into Phase 0/1 | Phase 2 note |
| B7 | Upload screening fails closed and its accuracy is not measured on a labelled set | Phase 1 classification would inherit an unmeasured pattern | Build a labelled set before extending it | Phase 1 note |

## Docs that disagreed with the code

- 03 §6 and 07 T6 say the graph summary can't be cited; the code enables citations on it (A2).
- 01 NFR-5 (bounded API spend) is not met (A3). NFR-2 (reproducible builds) has no source checksums or gazetteer snapshot (A1, A6, A7).
- 03 §2.3 says IDs come only from document structure; the chunk ordinal does not (B2).
- 04 §1's diagram shows Section → Passage; the export links Passage → Book and creates no Section instances (B4).
- README, 07 T4 and 06 §1 call query expansion "from the knowledge graph"; it comes from the gazetteer, and the graph only feeds the prompt summary (B3).
- Test counts in README (25) and 03 §12 (37) were stale; 39 collected on `main` (corrected to 51 with this branch).

## Decisions taken

On 2026-10-23 the maintainer accepted the recommendations for B1, B2, B4 (namespace) and B5.
They are recorded as D9–D12 in [02 §6](../02-architecture.md#6-key-decisions): w3id.org
namespace, `edition` separate from `work`, today's IDs frozen as v1 plus a `locator` field,
and named curator tokens with a provenance log before Phase 2.

## Reviewer's suggested first steps

1. Stop the grounding leak and the spend hole (A2, A3, A8).
2. Make builds self-describing and atomic (A1, A6, A7).
3. Decide the data model before writing 150 eval items: edition on passages, a v1 ID and locator scheme, renderings per edition, a permanent namespace (B1, B2, B4, L13).
4. Make the eval mean something: section-level expectations, MRR, the `--llm` baseline (B3).
5. Gazetteer schema validation (A4).
