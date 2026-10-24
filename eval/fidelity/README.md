# Fidelity benchmark (draft)

Questions that test whether an answer represents Indic texts and schools of thought
faithfully, scored on the four properties defined in
[docs/07 §2](../../docs/07-target-alignment.md#2-sot-fidelity-definition-and-measurement):

| Property | The answer fails if it… |
|---|---|
| `attribution` | presents a view without saying which text, speaker, school or translator holds it, or presents commentary as the text |
| `separation` | blends the positions of different works, speakers or schools into one composite view |
| `own_terms` | silently replaces a Sanskrit concept with an approximate English word ("soul" for *ātman*, "God" for *Brahman*) |
| `debate_position` | reports a view a text sets out in order to refute it (*pūrvapakṣa*) as the text's own conclusion (*siddhānta*) |

The same items are run against a plain model with no retrieval and against this system, so
the benchmark measures how much the design reduces distortion, not only whether answers cite.

**Status: draft.** The items in `items.jsonl` are examples written by the developers.
None is scored until a subject expert reviews it and sets `status` to `approved`. The scorer
exists (`ikg eval --fidelity`, below) and refuses to report until at least one item is approved.

## Item format

One JSON object per line in `items.jsonl`:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `fid-NN`, stable once approved |
| `type` | `"fidelity"` | |
| `property` | string | the property the item mainly tests, one of the four above |
| `question` | string | as a user would ask it |
| `corpus` | `"current"` or `"repository"` | `current`: answerable from the bundled works today. `repository`: needs texts not yet ingested, such as Shankara's commentaries |
| `positions` | list | the views a faithful answer must keep apart. Each has `holder` (who holds the view), `holder_type` (`work`, `speaker`, `school`, `thinker`, `translator` or `commentary`) and `sources` (passage-id prefixes that ground it, as in `eval/questions.jsonl`) |
| `terms` | object, optional | `keep`: Sanskrit terms the answer must use. `avoid_as_equivalent`: English words it must not present as their equivalent (it may mention them as a translator's rendering) |
| `must` | list of strings | checks a faithful answer passes, about what it says (not whether it cites) |
| `must_not` | list of strings | the specific distortions the item targets |
| `status` | `"draft"`, `"approved"` or `"retired"` | only `approved` items are scored |
| `author`, `reviewer` | string or null | who wrote the item, and the subject expert who approved it |
| `notes` | string | anything the reviewer should check, such as section numbers to confirm |

## Scoring

```bash
ikg eval --fidelity                    # this system: retrieval, citations, its prompt
ikg eval --fidelity --baseline         # the same answering model, no retrieval, a neutral prompt
ikg eval --fidelity --include-drafts   # development only: also score unapproved items
```

For each item, the answer, the item's positions and terms, and its `must` and `must_not`
checks go to a judge model (`IKG_JUDGE_MODEL`, default `claude-opus-5-5`, effort `medium`),
which returns pass or fail for each check with a reason, as validated JSON.

- **The judge is blind to the mode.** It sees the answer with citation markers and the source
  list removed, and the same prompt either way, so it can't favour the system for citing.
  Checks therefore judge what an answer says, never whether it cites ("cites passages for
  each" is not a valid check).
- **Sourcing is measured mechanically** for the system: `source_coverage` is the share of an
  item's positions with at least one citation from that position's `sources`.
- **Failures count as fails.** A check the judge leaves out, and a judge reply that is refused,
  truncated or not valid JSON, fail. An empty, refused or abstained answer fails every check
  without a judge call. An API error fails that item and the run continues.
- An item passes when every check passes. The rate for a property is the share of its items
  that pass. Only `approved` items with `corpus: current` are scored.

Results go to `eval/results/fidelity-<mode>-<time>.json` (answers, citations, retrieved
passages, verdicts, judge model and costs), written even if a run stops part-way. Each run also
writes `fidelity-<time>-for-labelling.jsonl` with each question, the answer as the judge saw it
and the checks, but no verdicts and no mode, so experts can label answers blind.

Every run calls the Claude API up to twice per item (answer and judge): roughly $0.02–0.05 per
item with the default models (Haiku 5.5 answers, Opus 5.5 judge), more with `--model
claude-opus-5-5`. Checks are matched to verdicts by position, so once an item is approved, don't
reorder its `must`/`must_not` lists without re-running. Before the judge's rates are reported, they are compared with expert labels
on a sample of answers. If the judge and the experts disagree on more than 10% of checks, the
rubric is revised first.

## Review checklist for subject experts

1. Is the question one a real user might ask?
2. Are the `positions` correct, complete and attributed to the right holder?
3. Do `must` and `must_not` capture the distortion that matters, in your tradition's terms?
4. Are the `sources` the right passages? Correct the section numbers where `notes` asks.
5. Set `status` to `approved` and add your name as `reviewer`, or rewrite the item.
