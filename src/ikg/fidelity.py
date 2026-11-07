"""SOT-fidelity benchmark (C1, docs/07 §2).

Each item in eval/fidelity/items.jsonl asks a question whose faithful answer keeps views
attributed and apart, uses Sanskrit concepts on their own terms, and respects debate positions.
An answer is produced either by this system (retrieval and citations) or, with `baseline`, by the
same model with no retrieval, so the benchmark measures what the design adds.

A judge model grades the answer against the item's `must` and `must_not` checks. The judge is kept
blind to which mode produced the answer: it sees the answer text with citation markers and the
source list removed, plus the item's positions and terms, and the same prompt in both modes.
Whether the system cited the right sources is measured mechanically instead (`source_coverage`,
system mode only), because a baseline answer can't cite by construction. An item passes when
every check passes; a property's rate is the share of its items that pass. An empty, refused or
abstained answer fails every check without a judge call.

Only items an expert has approved are scored, unless `include_drafts` is set for development.
The judge's verdicts must be compared with expert labels before its rates are reported; each run
also writes a blind file (questions and answers, no verdicts, no mode) for that labelling.
"""

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import anthropic

from ikg import config
from ikg.qa import NOT_IN_SOURCES, estimate_cost

PROPERTIES = ("attribution", "separation", "own_terms", "debate_position")
STATUSES = ("draft", "approved", "retired")

BASELINE_SYSTEM = "You answer questions about Indian texts and traditions. Be concise."

JUDGE_SYSTEM = """You grade whether an answer about Indic texts and traditions represents them faithfully.

You get the question, the answer, the positions a faithful answer must keep apart (who holds each view), any Sanskrit terms that matter, and numbered checks.

Grade each check against the answer's own text:
- A "must" check passes only if the answer clearly does what the check describes.
- A "must not" check passes only if the answer does not do what the check describes.
Use the positions and terms to understand the checks: who holds which view, and which English words are acceptable only when presented as a translator's rendering. Judge what the answer says, not how well it is sourced, and do not give credit for what the answer might have meant. For each check, give a one-sentence reason, quoting the answer where you can."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"checks": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, "passed": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["index", "passed", "reason"],
        "additionalProperties": False,
    }}},
    "required": ["checks"],
    "additionalProperties": False,
}

_MARKERS_RE = re.compile(r"(?:\[\d+\])+")


@dataclass
class FidelityItem:
    id: str
    type: str
    property: str
    question: str
    corpus: str
    positions: list
    must: list[str]
    must_not: list[str]
    status: str = "draft"
    terms: dict = field(default_factory=dict)
    author: str | None = None
    reviewer: str | None = None
    notes: str = ""


def load_items(path: Path) -> list[FidelityItem]:
    items = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                item = FidelityItem(**json.loads(line))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{path}:{n}: {exc}") from exc
            if item.type != "fidelity" or item.property not in PROPERTIES or item.status not in STATUSES:
                raise ValueError(f"{path}:{n}: needs type 'fidelity', a property in {PROPERTIES} "
                                 f"and a status in {STATUSES}")
            if item.corpus not in ("current", "repository"):
                raise ValueError(f"{path}:{n}: corpus must be 'current' or 'repository'")
            if not item.must and not item.must_not:
                raise ValueError(f"{path}:{n}: an item needs at least one 'must' or 'must_not' check")
            items.append(item)
    return items


def _create(client, model: str, effort: str, **kwargs):
    fallback = ({"betas": ["server-side-fallback-2026-07-01"], "extra_body": {"fallbacks": "default"}}
                if model in config.FALLBACK_MODELS else {})
    output_config = {"effort": effort, **kwargs.pop("output_config", {})}
    return client.beta.messages.create(model=model, max_tokens=16000, output_config=output_config,
                                       **kwargs, **fallback)


def _cost(response) -> float | None:
    usage = getattr(response, "usage", None)
    return estimate_cost(response.model, usage.input_tokens, usage.output_tokens) if usage else None


def _text(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def blind(answer: str) -> str:
    """The answer as the judge sees it: without `[n]` citation markers or the Sources list, which
    only system answers have and which would tell the judge the mode."""
    return _MARKERS_RE.sub("", answer.split("\n\nSources:\n")[0]).strip()


def baseline_answer(question: str, client, model: str, effort: str) -> dict:
    """The same model with no retrieval and no instructions about sources: what a user gets without
    this system."""
    response = _create(client, model, effort, system=BASELINE_SYSTEM,
                       messages=[{"role": "user", "content": question}])
    refused = response.stop_reason == "refusal"
    return {"text": "" if refused else _text(response), "cost": _cost(response), "refused": refused,
            "truncated": response.stop_reason == "max_tokens", "abstained": False, "cited": []}


def judge(item: FidelityItem, answer: str, client, model: str, effort: str) -> tuple[list[dict], dict]:
    """Grade an answer against the item's checks. A check the judge leaves out, or a judge reply
    that is refused, truncated or not valid JSON, fails."""
    checks = [("must", c) for c in item.must] + [("must_not", c) for c in item.must_not]
    listed = "\n".join(f"{i}. [{'must' if kind == 'must' else 'must not'}] {text}"
                       for i, (kind, text) in enumerate(checks, 1))
    positions = "\n".join(f"- {p.get('holder')} ({p.get('holder_type')})" for p in item.positions) or "(none given)"
    terms = json.dumps(item.terms, ensure_ascii=False) if item.terms else "(none given)"
    prompt = (f"Question:\n{item.question}\n\nAnswer:\n{answer}\n\nPositions:\n{positions}\n\n"
              f"Terms:\n{terms}\n\nChecks:\n{listed}")
    response = _create(client, model, effort, system=JUDGE_SYSTEM,
                       output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
                       messages=[{"role": "user", "content": prompt}])
    meta = {"judge_model": response.model, "judge_stop_reason": response.stop_reason, "judge_cost": _cost(response)}
    verdicts, problem = {}, ""
    if response.stop_reason in ("refusal", "max_tokens"):
        problem = f"The judge's reply ended with {response.stop_reason}."
    else:
        try:
            verdicts = {v["index"]: v for v in json.loads(_text(response)).get("checks", [])}
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
            problem = "The judge's reply was not valid JSON."
    results = []
    for i, (kind, text) in enumerate(checks, 1):
        v = verdicts.get(i)
        results.append({"kind": kind, "check": text, "passed": bool(v and v["passed"]),
                        "reason": v["reason"] if v else problem or "The judge gave no verdict for this check."})
    return results, meta


def source_coverage(item: FidelityItem, cited_ids: list[str]) -> float | None:
    """Share of the item's positions that have at least one citation from their own sources."""
    positions = [p for p in item.positions if p.get("sources")]
    if not positions:
        return None
    covered = sum(any(c.startswith(s) for c in cited_ids for s in p["sources"]) for p in positions)
    return round(covered / len(positions), 3)


def _answer(item, runtime, baseline, client, model, effort, k) -> dict:
    if baseline:
        return baseline_answer(item.question, client, model, effort)
    from ikg.qa import ask
    ans = ask(item.question, runtime.index, runtime.gazetteer, graph=runtime.graph,
              passage_entities=runtime.passage_entities, client=client, model=model, effort=effort, k=k,
              retrieval=runtime.retrieval())
    return {"text": ans.text, "cost": ans.cost_usd, "refused": ans.refused, "truncated": ans.truncated,
            "abstained": ans.abstained, "cited": [c.passage_id for c in ans.citations],
            "retrieved": [h.passage.id for h in ans.hits]}


def run(items: list[FidelityItem], runtime=None, baseline: bool = False, include_drafts: bool = False,
        client=None, judge_model: str | None = None, model: str | None = None, effort: str | None = None,
        k: int = 8, out_dir: Path | None = None) -> dict:
    """Answer and judge each selected item. `runtime` is needed unless `baseline` is set. Results are
    written even if the run stops part-way, so paid answers are never lost."""
    client = client or anthropic.Anthropic()
    judge_model = judge_model or config.JUDGE_MODEL
    model, effort = model or config.MODEL, effort or config.EFFORT
    mode = "baseline" if baseline else "system"
    selected = [i for i in items if i.corpus == "current"
                and (i.status == "approved" or (include_drafts and i.status == "draft"))]
    rows: list[dict] = []
    summary: dict = {}
    try:
        for item in selected:
            row = {"id": item.id, "property": item.property, "status": item.status}
            try:
                a = _answer(item, runtime, baseline, client, model, effort, k)
                row.update(answer=a["text"], refused=a["refused"], abstained=a["abstained"], truncated=a["truncated"],
                           cited=a["cited"], retrieved=a.get("retrieved", []), answer_cost=a["cost"])
                if not baseline:
                    row["source_coverage"] = source_coverage(item, a["cited"])
                shown = blind(a["text"])
                if a["refused"] or a["abstained"] or not shown or shown.startswith(NOT_IN_SOURCES):
                    reason = "No answer to grade: the model refused or abstained, or the answer was empty."
                    row["checks"] = [{"kind": kind, "check": c, "passed": False, "reason": reason}
                                     for kind, cs in (("must", item.must), ("must_not", item.must_not)) for c in cs]
                else:
                    row["checks"], meta = judge(item, shown, client, judge_model, config.JUDGE_EFFORT)
                    row.update(meta)
            except anthropic.APIError as exc:
                row.update(error=str(exc), checks=[{"kind": kind, "check": c, "passed": False, "reason": f"API error: {exc}"}
                                                   for kind, cs in (("must", item.must), ("must_not", item.must_not))
                                                   for c in cs])
            row["passed"] = all(c["passed"] for c in row["checks"])
            rows.append(row)
    finally:
        summary = summarize(rows, mode)
        summary.update(mode=mode, answer_model=model, answer_effort=effort, judge_model=judge_model,
                       includes_drafts=include_drafts, n_selected=len(selected),
                       answer_cost_usd=round(sum(r.get("answer_cost") or 0 for r in rows), 4),
                       judge_cost_usd=round(sum(r.get("judge_cost") or 0 for r in rows), 4))
        if out_dir:
            _write(out_dir, mode, summary, rows, selected)
    return {"summary": summary, "rows": rows}


def _write(out_dir: Path, mode: str, summary: dict, rows: list[dict], selected: list[FidelityItem]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"fidelity-{mode}-{stamp}.json").write_text(
        json.dumps({"summary": summary, "rows": rows, "items": [asdict(i) for i in selected]},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    # For expert labelling: what the judge saw, without verdicts or mode.
    by_id = {i.id: i for i in selected}
    with (out_dir / f"fidelity-{stamp}-for-labelling.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            item = by_id[r["id"]]
            f.write(json.dumps({"id": r["id"], "question": item.question, "answer": blind(r.get("answer", "")),
                                "must": item.must, "must_not": item.must_not}, ensure_ascii=False) + "\n")


def summarize(rows: list[dict], mode: str = "system") -> dict:
    def rate(rs):
        return round(sum(r["passed"] for r in rs) / len(rs), 3) if rs else None

    checks = [c for r in rows for c in r["checks"]]
    summary = {
        "n_items": len(rows),
        "item_pass_rate": rate(rows),
        "check_pass_rate": round(sum(c["passed"] for c in checks) / len(checks), 3) if checks else None,
        "by_property": {p: {"n": sum(r["property"] == p for r in rows),
                            "pass_rate": rate([r for r in rows if r["property"] == p])} for p in PROPERTIES},
        "errors": sum("error" in r for r in rows),
    }
    if mode == "system":
        cov = [r["source_coverage"] for r in rows if r.get("source_coverage") is not None]
        summary["source_coverage"] = round(sum(cov) / len(cov), 3) if cov else None
    return summary
