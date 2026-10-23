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
    assert request["model"] == "claude-opus-5-5"
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
