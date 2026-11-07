"""Evaluation harness.

Question types (eval/questions.jsonl):
  retrieval    - answer must come from passages whose id starts with one of `expected`
  adversarial  - the corpus does not support an answer; the system must abstain

Offline metrics (no API calls): retrieval hit@1, hit@k and MRR (mean reciprocal rank of the
first correct passage within the top k) for retrieval questions. `expected` holds passage-ID
prefixes at section or verse level (e.g. `mbh.b08.s090`, `upa.katha.p2.v18`), so a hit means
the answering part of the text was found, not just the right book. Only primary text (verse,
narrative) counts as a hit; commentary on an expected verse does not.
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
from ikg.segment import PRIMARY_KINDS


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


def _answers(passage, prefixes: list[str]) -> bool:
    """A retrieved passage answers the item if it is primary text in an expected section or verse.
    Commentary on an expected verse doesn't count: the question asks what the text says (rule 2)."""
    return passage.kind in PRIMARY_KINDS and _matches(passage.id, prefixes)


def run(items, index, gazetteer, passage_entities, k: int = 8, llm: bool = False,
        graph=None, out_dir: Path | None = None, expand: bool = True,
        model: str | None = None, effort: str | None = None) -> dict:
    """``expand=False`` disables gazetteer query expansion and entity boosting (ablation).
    ``model``/``effort`` choose the answering model for ``llm`` runs (default: config)."""
    rows = []
    for item in items:
        hits = search(index, gazetteer, item.question, k=k, works=item.works,
                      passage_entities=passage_entities if expand else None, expand=expand)
        row = {"id": item.id, "type": item.type,
               "retrieved": [h.passage.id for h in hits]}
        if item.type == "retrieval":
            row["hit_at_k"] = any(_answers(h.passage, item.expected) for h in hits)
            row["hit_at_1"] = bool(hits) and _answers(hits[0].passage, item.expected)
            rank = next((i for i, h in enumerate(hits, 1) if _answers(h.passage, item.expected)), None)
            row["reciprocal_rank"] = 1 / rank if rank else 0.0
        if llm:
            from ikg.qa import ask
            ans = ask(item.question, index, gazetteer, graph=graph,
                      passage_entities=passage_entities, k=k, works=item.works, model=model, effort=effort)
            cited = [c.passage_id for c in ans.citations]
            row.update(answer=ans.text, cited=cited, abstained=ans.abstained, refused=ans.refused,
                       truncated=ans.truncated, model=ans.model, effort=ans.effort,
                       input_tokens=ans.input_tokens, output_tokens=ans.output_tokens, cost_usd=ans.cost_usd)
            if ans.dropped_sentences is not None:  # a local model (ikg.localqa)
                row["dropped_sentences"] = ans.dropped_sentences
                row["model_abstained"] = ans.model_abstained
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
LOWER_IS_BETTER = {"false_abstain_rate", "truncated_rate", "mean_input_tokens", "mean_output_tokens",
                   "total_cost_usd", "mean_cost_usd", "mean_dropped_sentences"}


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
               "retrieval_hit_at_k": rate(retrieval, "hit_at_k"),
               "retrieval_mrr": rate(retrieval, "reciprocal_rank")}
    # Per question type, from the item id prefix (upa-, skt-, spk-, xw-, ...), to see where retrieval is weak.
    by_type: dict[str, list] = {}
    for r in retrieval:
        by_type.setdefault(r["id"].split("-")[0], []).append(r)
    summary["retrieval_by_type"] = {
        t: {"n": len(rs), "hit_at_1": rate(rs, "hit_at_1"), "hit_at_k": rate(rs, "hit_at_k"),
            "mrr": rate(rs, "reciprocal_rank")} for t, rs in sorted(by_type.items())}
    if any("answer" in r for r in rows):
        summary.update(
            cites_expected=rate(retrieval, "cites_expected"),
            citation_precision=rate(retrieval, "citation_precision"),
            has_citations=rate(retrieval, "has_citations"),
            false_abstain_rate=rate(retrieval, "false_abstain"),
            adversarial_abstain_rate=rate(adversarial, "abstained"),
            truncated_rate=rate(rows, "truncated"),
        )
        answered = [r for r in rows if "answer" in r]
        costs = [r["cost_usd"] for r in answered if r.get("cost_usd") is not None]
        summary.update(
            model=sorted({r["model"] for r in answered}), effort=answered[0]["effort"],
            mean_input_tokens=round(sum(r["input_tokens"] for r in answered) / len(answered)),
            mean_output_tokens=round(sum(r["output_tokens"] for r in answered) / len(answered)),
            total_cost_usd=round(sum(costs), 4) if costs else None,
            mean_cost_usd=round(sum(costs) / len(costs), 5) if costs else None,
        )
        dropped = [r["dropped_sentences"] for r in answered if "dropped_sentences" in r]
        if dropped:  # local models: sentences removed because their citation could not be verified
            summary["mean_dropped_sentences"] = round(sum(dropped) / len(dropped), 2)
            # `abstained` also counts answers whose every sentence was dropped; this is the model alone.
            summary["adversarial_model_abstain_rate"] = rate(adversarial, "model_abstained")
    return summary
