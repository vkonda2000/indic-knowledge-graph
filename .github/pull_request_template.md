## What and why

<!-- One or two sentences. Link the roadmap item (docs/06-roadmap.md) or issue. -->

## Evidence

<!-- For changes to search, entities, segmentation or answering: `ikg eval` before and after.
     If the numbers moved, update eval/baseline.json in this PR and explain why. -->

## Checklist

- [ ] `pytest -q` passes locally (CI also runs it)
- [ ] `ikg eval --check eval/baseline.json` passes, or the baseline is updated with a reason
- [ ] Docs updated where behaviour changed: `docs/`, the roadmap checklist (docs/06 §3) and the technology stack (docs/02 §7)
- [ ] Independent Fable review run (`fable-reviewer` agent in `.claude/agents/`) and its findings fixed or answered below

## Review findings

<!-- Paste the Fable review summary: what was found, what you fixed, what you disagreed with and why. -->
