"""Fidelity benchmark with a fake Anthropic client: no network, no API key."""

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from conftest import make_passage

from ikg import config, fidelity
from ikg.qa import SYSTEM_PROMPT
from ikg.retrieve import BM25Index

PASSAGES = [make_passage("upa.katha.p2.v18", "upanishads", "The Self is unborn, eternal; it is not slain.", kind="verse")]


def item(**kw):
    base = dict(id="fid-x", type="fidelity", property="own_terms", question="Is the Self unborn?",
                corpus="current", positions=[{"holder": "Katha Upanishad", "holder_type": "work", "sources": ["upa.katha"]}],
                must=["Uses atman"], must_not=["Treats soul as an exact equivalent"], status="approved",
                terms={"keep": ["ātman"], "avoid_as_equivalent": ["soul"]})
    return fidelity.FidelityItem(**{**base, **kw})


def text(t, citations=()):
    return SimpleNamespace(type="text", text=t, citations=list(citations))


class FakeClient:
    """Answers questions, and judges with `verdicts` (index -> passed), or a canned bad judge reply."""

    def __init__(self, verdicts, answer="The Katha calls it the atman, which is unborn.", judge_reply=None,
                 judge_stop="end_turn", fail_answer=False):
        self.verdicts, self.answer, self.judge_reply, self.judge_stop = verdicts, answer, judge_reply, judge_stop
        self.fail_answer, self.calls = fail_answer, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100)
        if kw["system"] == fidelity.JUDGE_SYSTEM:
            reply = self.judge_reply if self.judge_reply is not None else json.dumps(
                {"checks": [{"index": i, "passed": p, "reason": "because"} for i, p in self.verdicts.items()]})
            return SimpleNamespace(content=[SimpleNamespace(type="thinking"), text(reply)],
                                   stop_reason=self.judge_stop, model=kw["model"], usage=usage)
        if self.fail_answer:
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
        cites = [SimpleNamespace(document_index=0, cited_text="unborn")] if kw["system"] == SYSTEM_PROMPT else []
        return SimpleNamespace(content=[text(self.answer, cites)], stop_reason="end_turn", model=kw["model"],
                               usage=usage)

    def judge_prompt(self):
        return next(c for c in self.calls if c["system"] == fidelity.JUDGE_SYSTEM)["messages"][0]["content"]


def runtime(gazetteer):
    return SimpleNamespace(index=BM25Index(PASSAGES), gazetteer=gazetteer, graph=None, passage_entities=None)


def test_judge_is_blind_to_the_mode(gazetteer):
    system, base = FakeClient({1: True, 2: True}), FakeClient({1: True, 2: True})
    fidelity.run([item()], runtime(gazetteer), client=system)
    fidelity.run([item()], baseline=True, client=base)
    # Same answer text in both modes, so the judge prompts must be identical: no markers, no source list.
    assert system.judge_prompt() == base.judge_prompt()
    prompt = system.judge_prompt()
    assert "[1]" not in prompt and "Sources:" not in prompt and "upa.katha.p2.v18" not in prompt
    assert "Katha Upanishad (work)" in prompt and "avoid_as_equivalent" in prompt
    assert "1. [must] Uses atman" in prompt and "2. [must not] Treats soul" in prompt


def test_system_mode_reports_source_coverage_mechanically(gazetteer):
    result = fidelity.run([item()], runtime(gazetteer), client=FakeClient({1: True, 2: True}))
    row, s = result["rows"][0], result["summary"]
    assert row["passed"] and row["cited"] == ["upa.katha.p2.v18"] and row["source_coverage"] == 1.0
    assert s["mode"] == "system" and s["source_coverage"] == 1.0 and s["by_property"]["own_terms"] == {"n": 1, "pass_rate": 1.0}
    assert s["answer_cost_usd"] > 0 and s["judge_cost_usd"] > 0
    assert fidelity.source_coverage(item(positions=[{"holder": "A", "sources": ["vp."]},
                                                    {"holder": "B", "sources": ["upa.katha"]}]), ["upa.katha.p1.v01"]) == 0.5


def test_baseline_answers_without_retrieval():
    client = FakeClient({1: True, 2: False})
    result = fidelity.run([item()], baseline=True, client=client)
    answer_call = next(c for c in client.calls if c["system"] == fidelity.BASELINE_SYSTEM)
    assert answer_call["messages"] == [{"role": "user", "content": "Is the Self unborn?"}]
    row = result["rows"][0]
    assert not row["passed"] and row["cited"] == [] and "source_coverage" not in result["summary"]
    assert [c["passed"] for c in row["checks"]] == [True, False] and result["summary"]["check_pass_rate"] == 0.5


@pytest.mark.parametrize("client, reason", [
    (FakeClient({1: True}), "no verdict"),
    (FakeClient({}, judge_reply="not json"), "not valid JSON"),
    (FakeClient({1: True, 2: True}, judge_stop="max_tokens"), "max_tokens"),
    (FakeClient({1: True, 2: True}, judge_stop="refusal"), "refusal"),
])
def test_bad_judge_replies_fail_checks_without_crashing(client, reason):
    row = fidelity.run([item()], baseline=True, client=client)["rows"][0]
    assert not row["passed"] and reason in " ".join(c["reason"] for c in row["checks"] if not c["passed"])


def test_empty_or_abstained_answers_fail_without_a_judge_call():
    client = FakeClient({1: True, 2: True}, answer="NOT_IN_SOURCES\nThe passages do not say.")
    row = fidelity.run([item()], baseline=True, client=client)["rows"][0]
    assert not row["passed"] and not any(c["passed"] for c in row["checks"])
    assert not any(c["system"] == fidelity.JUDGE_SYSTEM for c in client.calls)


def test_api_error_fails_the_item_and_results_are_still_written(tmp_path):
    result = fidelity.run([item(id="a"), item(id="b")], baseline=True, client=FakeClient({}, fail_answer=True),
                          out_dir=tmp_path)
    assert [r["passed"] for r in result["rows"]] == [False, False] and result["summary"]["errors"] == 2
    full = next(tmp_path.glob("fidelity-baseline-*.json"))
    assert json.loads(full.read_text(encoding="utf-8"))["summary"]["n_items"] == 2
    blind = [json.loads(l) for l in next(tmp_path.glob("*-for-labelling.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert [b["id"] for b in blind] == ["a", "b"] and not any("checks" in b or "mode" in b for b in blind)


def test_only_approved_current_corpus_items_are_scored():
    items = [item(id="a"), item(id="b", status="draft"), item(id="c", corpus="repository"),
             item(id="d", status="retired")]
    client = FakeClient({1: True, 2: True})
    assert [r["id"] for r in fidelity.run(items, baseline=True, client=client)["rows"]] == ["a"]
    with_drafts = fidelity.run(items, baseline=True, include_drafts=True, client=client)
    assert [r["id"] for r in with_drafts["rows"]] == ["a", "b"] and with_drafts["summary"]["includes_drafts"]


def test_items_are_validated(tmp_path):
    assert len(fidelity.load_items(config.FIDELITY_FILE)) >= 10
    for bad_item, message in [({**item().__dict__, "property": "style"}, "property"),
                              ({**item().__dict__, "must": [], "must_not": []}, "at least one")]:
        bad = tmp_path / "items.jsonl"
        bad.write_text(json.dumps(bad_item) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            fidelity.load_items(bad)


def test_cli_refuses_unreviewed_items_and_conflicting_flags(monkeypatch, tmp_path):
    from ikg import cli
    path = tmp_path / "items.jsonl"
    path.write_text(json.dumps({**item(status="draft").__dict__}) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "FIDELITY_FILE", path)
    with pytest.raises(SystemExit, match="No fidelity item is approved"):
        cli.main(["eval", "--fidelity", "--baseline"])
    with pytest.raises(SystemExit, match="can't be combined"):
        cli.main(["eval", "--fidelity", "--check", "eval/baseline.json"])
