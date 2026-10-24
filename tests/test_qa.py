"""QA tests with a fake Anthropic client: no network, no API key."""

from types import SimpleNamespace

from conftest import make_passage

from ikg.qa import NOT_IN_SOURCES, ask
from ikg.retrieve import BM25Index

PASSAGES = [
    make_passage("upa.katha.p1.v20", "upanishads", "Nachiketas asked Yama what becomes of a man after death.", kind="verse"),
    make_passage("upa.katha.p1.v20.c1", "upanishads", "The translator explains that Nachiketas asked about death.", kind="commentary"),
]


class FakeClient:
    def __init__(self, response):
        self.response, self.calls = response, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def text_block(text, cited_docs=()):
    citations = [SimpleNamespace(document_index=i, cited_text="quote") for i in cited_docs]
    return SimpleNamespace(type="text", text=text, citations=citations)


def response(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, model="claude-opus-5-5")


def run(gazetteer, resp):
    client = FakeClient(resp)
    ans = ask("What did Nachiketas ask Yama about death?", BM25Index(PASSAGES), gazetteer, client=client)
    return ans, client


def test_citations_are_numbered_and_listed(gazetteer):
    ans, client = run(gazetteer, response(text_block("He asked what happens after death", cited_docs=[0, 1]),
                                          text_block(".")))
    assert [c.passage_id for c in ans.citations] == [h.passage.id for h in ans.hits]
    assert "[1][2]" in ans.text and "Sources:" in ans.text
    request = client.calls[0]
    assert request["model"] == "claude-haiku-5-5"  # the default; see config.MODEL
    docs = [b for b in request["messages"][0]["content"] if b["type"] == "document"]
    assert all(d["citations"] == {"enabled": True} for d in docs)
    assert "kind=commentary" in " ".join(d["context"] for d in docs)


def test_abstention_detected(gazetteer):
    ans, _ = run(gazetteer, response(text_block(f"{NOT_IN_SOURCES}\nThe passages do not say.")))
    assert ans.abstained and not ans.citations


def test_refusal_handled(gazetteer):
    ans, _ = run(gazetteer, response(stop_reason="refusal"))
    assert ans.refused


def test_no_hits_skips_api_call(gazetteer):
    client = FakeClient(None)
    ans = ask("zzzz qqqq", BM25Index(PASSAGES), gazetteer, client=client)
    assert ans.abstained and client.calls == []


def test_graph_summary_is_not_a_citable_document(gazetteer):
    import networkx as nx
    g = nx.Graph()
    g.add_node("yama", type="Deity", label="Yama", mentions=1, works="upanishads:1")
    client = FakeClient(response(text_block("He asked about death", cited_docs=[0])))
    ask("What did Nachiketas ask Yama?", BM25Index(PASSAGES), gazetteer, graph=g, client=client)
    content = client.calls[0]["messages"][0]["content"]
    docs = [b for b in content if b["type"] == "document"]
    assert all(d["context"].startswith("passage_id=") for d in docs)
    texts = [b["text"] for b in content if b["type"] == "text"]
    assert texts[-1] == "What did Nachiketas ask Yama?"


def test_model_effort_cost_and_fallbacks(gazetteer):
    resp = response(text_block("He asked about death", cited_docs=[0]))
    resp.usage = SimpleNamespace(input_tokens=3000, output_tokens=1000)
    client = FakeClient(resp)
    ans = ask("What did Nachiketas ask Yama about death?", BM25Index(PASSAGES), gazetteer, client=client,
              model="claude-opus-5-5", effort="high")
    assert client.calls[0]["output_config"] == {"effort": "high"}
    assert client.calls[0]["extra_body"] == {"fallbacks": "default"}
    assert ans.cost_usd == (3000 * 4.00 + 1000 * 20.00) / 1_000_000

    resp.model = "claude-haiku-5-5"
    client = FakeClient(resp)
    ans = ask("What did Nachiketas ask Yama about death?", BM25Index(PASSAGES), gazetteer, client=client,
              model="claude-haiku-5-5", effort="low")
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["output_config"] == {"effort": "low"}
    assert "extra_body" not in call and "betas" not in call  # Haiku has no server-side fallback
    assert ans.cost_usd == (3000 * 0.10 + 1000 * 0.50) / 1_000_000 and ans.effort == "low"


def test_truncation_is_flagged(gazetteer):
    ans, _ = run(gazetteer, response(text_block("He asked", cited_docs=[0]), stop_reason="max_tokens"))
    assert ans.truncated and "cut off at the length limit" in ans.text


def test_invalid_effort_is_rejected_before_any_call(gazetteer):
    import pytest
    client = FakeClient(None)
    with pytest.raises(ValueError, match="effort must be one of"):
        ask("What did Nachiketas ask Yama?", BM25Index(PASSAGES), gazetteer, client=client, effort="hgih")
    assert client.calls == []


def test_haiku_long_prompt_price():
    from ikg.qa import estimate_cost
    assert estimate_cost("claude-haiku-5-5", 100_000, 0) == 100_000 * 0.10 / 1_000_000
    assert estimate_cost("claude-haiku-5-5", 200_000, 1000) == (200_000 * 0.50 + 1000 * 2.50) / 1_000_000
    assert estimate_cost("some-unpriced-model", 10, 10) is None
