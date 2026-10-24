"""Evaluation harness.

Question types (eval/questions.jsonl):
  retrieval    - answer must come from passages whose id starts with one of `expected`
  adversarial  - the corpus does not support an answer; the system must abstain

Offline metrics (no API calls): retrieval hit@k for retrieval questions.
With --llm: citation precision against `expected`, citation presence, and abstention
on adversarial questions. Results are written to eval/results/ (git-ignored).

Regression gate: ``ikg eval --check eval/baseline.json`` exits non-zero if any metric in the
committed baseline got worse. CI runs it on every pull request.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

from ikg.retrieve import search


@dataclass
class EvalItem:
    id: str
    type: str
    question: str
    expected: list[str]
    works: list[str] | None = None


def load_items(path: Path) -> list[EvalItem]:
    with path.open(encoding="utf-8") as f:
        return [EvalItem(**json.loads(line)) for line in f if line.strip()]


def _matches(pid: str, prefixes: list[str]) -> bool:
    return any(pid.startswith(p) for p in prefixes)


def run(items, index, gazetteer, passage_entities, k: int = 8, llm: bool = False,
        graph=None, out_dir: Path | None = None, expand: bool = True) -> dict:
    """``expand=False`` disables knowledge-graph query expansion and entity boosting (ablation)."""
    rows = []
    for item in items:
        hits = search(index, gazetteer, item.question, k=k, works=item.works,
                      passage_entities=passage_entities if expand else None, expand=expand)
        row = {"id": item.id, "type": item.type,
               "retrieved": [h.passage.id for h in hits]}
        if item.type == "retrieval":
            row["hit_at_k"] = any(_matches(pid, item.expected) for pid in row["retrieved"])
            row["hit_at_1"] = bool(hits) and _matches(hits[0].passage.id, item.expected)
        if llm:
            from ikg.qa import ask
            ans = ask(item.question, index, gazetteer, graph=graph,
                      passage_entities=passage_entities, k=k, works=item.works)
            cited = [c.passage_id for c in ans.citations]
            row.update(answer=ans.text, cited=cited, abstained=ans.abstained, refused=ans.refused)
            if item.type == "retrieval":
                row["cites_expected"] = any(_matches(pid, item.expected) for pid in cited)
                row["citation_precision"] = (sum(_matches(pid, item.expected) for pid in cited) / len(cited)) if cited else 0.0
                row["has_citations"] = bool(cited)
                row["false_abstain"] = ans.abstained
        rows.append(row)

    summary = summarize(rows)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (out_dir / f"run-{stamp}.json").write_text(
            json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"summary": summary, "rows": rows}


# Metrics where a higher value is worse; every other numeric metric is better when higher.
# Item counts (n_retrieval, n_adversarial) belong in the baseline too, so that deleting a
# failing question does not pass the gate.
LOWER_IS_BETTER = {"false_abstain_rate"}


def regressions(summary: dict, baseline: dict) -> list[str]:
    """Describe each baseline metric that the new summary does worse on (empty list: no regression)."""
    found = []
    for key, base in baseline.get("metrics", {}).items():
        new = summary.get(key)
        if not isinstance(base, (int, float)):
            found.append(f"{key}: baseline value {base!r} is not a number; fix eval/baseline.json")
        elif new is None:
            found.append(f"{key}: missing (baseline {base})")
        elif (new > base) if key in LOWER_IS_BETTER else (new < base):
            found.append(f"{key}: {new} (baseline {base})")
    return found


def summarize(rows: list[dict]) -> dict:
    def rate(subset, key):
        vals = [r[key] for r in subset if key in r]
        return round(sum(vals) / len(vals), 3) if vals else None

    retrieval = [r for r in rows if r["type"] == "retrieval"]
    adversarial = [r for r in rows if r["type"] == "adversarial"]
    summary = {"n_retrieval": len(retrieval), "n_adversarial": len(adversarial),
               "retrieval_hit_at_1": rate(retrieval, "hit_at_1"),
               "retrieval_hit_at_k": rate(retrieval, "hit_at_k")}
    if any("answer" in r for r in rows):
        summary.update(
            cites_expected=rate(retrieval, "cites_expected"),
            citation_precision=rate(retrieval, "citation_precision"),
            has_citations=rate(retrieval, "has_citations"),
            false_abstain_rate=rate(retrieval, "false_abstain"),
            adversarial_abstain_rate=rate(adversarial, "abstained"),
        )
    return summary
