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
None is scored until a subject expert reviews it and sets `status` to `approved`. `ikg eval`
does not read this directory yet.

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
| `must` | list of strings | checks a faithful answer passes |
| `must_not` | list of strings | the specific distortions the item targets |
| `status` | `"draft"`, `"approved"` or `"retired"` | only `approved` items are scored |
| `author`, `reviewer` | string or null | who wrote the item, and the subject expert who approved it |
| `notes` | string | anything the reviewer should check, such as section numbers to confirm |

## Scoring (planned)

For each item, a judge model reads the answer, the item's `must` and `must_not` checks and
the cited passages. It returns pass or fail for each check, with a reason. An item passes
when every check passes. The rate for a property is the share of its approved items that
pass. Before the judge's rates are reported, they are compared with expert labels on a
sample of answers. If the judge and the experts disagree on more than 10% of checks, the
rubric is revised first.

## Review checklist for subject experts

1. Is the question one a real user might ask?
2. Are the `positions` correct, complete and attributed to the right holder?
3. Do `must` and `must_not` capture the distortion that matters, in your tradition's terms?
4. Are the `sources` the right passages? Correct the section numbers where `notes` asks.
5. Set `status` to `approved` and add your name as `reviewer`, or rewrite the item.
