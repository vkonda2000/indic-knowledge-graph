"""Dense and hybrid retrieval (docs/08 A3), the query embedding, and reuse of vectors across builds.
A fake dense search or the fake embedder from test_embed stands in for the model."""

import json

import numpy as np
import pytest
from conftest import make_passage
from test_embed import FakeEmbedder, _build, corpus  # noqa: F401  (corpus is a fixture)

from ikg import cli, config, embed, pipeline
from ikg.embed import embed_query
from ikg.retrieve import CANDIDATES, RRF_K, BM25Index, search

PASSAGES = [
    make_passage("a.1", "alpha", "Krishna spoke of dharma to Arjuna on the field.", kind="verse"),
    make_passage("a.1.c1", "alpha", "The translator notes that dharma means duty here.", kind="commentary"),
    make_passage("a.2", "alpha", "The forest was quiet and the river ran clear.", kind="narrative"),
    make_passage("b.1", "beta", "Sita walked beside Rama through the forest.", kind="verse"),
    make_passage("b.2", "beta", "The sage taught that the Self is unborn and undying.", kind="verse"),
]
INDEX = BM25Index(PASSAGES)


def fake_dense(ranking):
    """A dense search that returns `ranking` (passage ids, best first), honouring the filters."""
    by_id = {p.id: p for p in PASSAGES}
    calls = []

    def dense(question, n, works=None, kinds=None):
        calls.append((question, n, works, kinds))
        ok = [pid for pid in ranking if (works is None or by_id[pid].work in works)
              and (kinds is None or by_id[pid].kind in kinds)]
        return [(pid, 1.0 - i / 10) for i, pid in enumerate(ok[:n])]
    dense.calls = calls
    return dense


def ids(hits):
    return [h.passage.id for h in hits]


def test_dense_mode_is_the_dense_ranking_only(gazetteer):
    dense = fake_dense(["b.2", "a.2", "a.1"])
    assert ids(search(INDEX, gazetteer, "What is the Self?", k=2, mode="dense", dense=dense)) == ["b.2", "a.2"]
    assert dense.calls[0][1] == 2  # dense mode asks for k, not the fusion candidates


def test_hybrid_fuses_both_rankings(gazetteer):
    # BM25 alone ranks a.1 first for "dharma"; dense alone ranks b.2 first; a.1.c1 is second in both.
    dense = fake_dense(["b.2", "a.1.c1", "a.2", "a.1", "b.1"])
    bm25 = ids(search(INDEX, gazetteer, "dharma", k=5, passage_entities=None))
    hybrid = search(INDEX, gazetteer, "dharma", k=5, mode="hybrid", dense=dense, passage_entities=None)
    assert bm25[:2] == ["a.1.c1", "a.1"] or bm25[:2] == ["a.1", "a.1.c1"]
    assert ids(hybrid)[0] == "a.1.c1"  # high in both lists beats first in one
    assert set(ids(hybrid)) == {p.id for p in PASSAGES}  # dense brings in passages BM25 never matched
    expected_top = 1 / (RRF_K + bm25.index("a.1.c1") + 1) + 1 / (RRF_K + 2)
    assert hybrid[0].score == pytest.approx(expected_top)
    assert dense.calls[0][1] == CANDIDATES


def test_hybrid_filters_apply_before_ranking(gazetteer):
    dense = fake_dense(["a.1.c1", "b.2", "a.2", "a.1", "b.1"])
    hits = search(INDEX, gazetteer, "dharma", k=5, mode="hybrid", dense=dense, primary_only=True)
    assert "a.1.c1" not in ids(hits) and len(hits) == 4  # commentary stays out, the rest come back
    assert dense.calls[0][3] == ("verse", "narrative")
    hits = search(INDEX, gazetteer, "dharma", k=5, mode="hybrid", dense=dense, works=["beta"])
    assert ids(hits) and all(h.passage.work == "beta" for h in hits)


def test_hybrid_applies_the_entity_boost_after_fusion(gazetteer):
    dense = fake_dense(["a.2", "b.1", "a.1"])
    entities = {"a.1": {"krishna", "arjuna"}}
    plain = search(INDEX, gazetteer, "Krishna and Arjuna", k=3, mode="hybrid", dense=dense)
    boosted = search(INDEX, gazetteer, "Krishna and Arjuna", k=3, mode="hybrid", dense=dense,
                     passage_entities=entities)
    assert ids(boosted)[0] == "a.1" and boosted[0].score > plain[ids(plain).index("a.1")].score


def test_vector_modes_need_a_dense_search(gazetteer):
    with pytest.raises(ValueError, match="build --embed"):
        search(INDEX, gazetteer, "dharma", mode="hybrid")
    with pytest.raises(ValueError, match="retrieval mode"):
        search(INDEX, gazetteer, "dharma", mode="semantic", dense=fake_dense([]))


def test_query_embedding_uses_the_models_instruction(monkeypatch):
    seen = []

    class Recorder(FakeEmbedder):
        name = "BAAI/bge-small-en-v1.5"

        def embed(self, texts):
            seen.extend(texts)
            return super().embed(texts)

    v = embed_query("What is dharma?", Recorder())
    assert seen == [embed.QUERY_PREFIXES["BAAI/bge-small-en-v1.5"] + "What is dharma?"]
    assert v.dtype == np.float32 and np.linalg.norm(v) == pytest.approx(1.0)
    seen.clear()
    embed_query("What is dharma?", FakeEmbedder())  # a model without an instruction gets the bare text
    assert FakeEmbedder.name not in embed.QUERY_PREFIXES


def test_runtime_retrieval_defaults_and_fallback(corpus, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "RETRIEVER", "hybrid")
    _build(corpus)
    rt = pipeline.Runtime.load()
    assert rt.retrieval() == {"mode": "bm25"}  # no embeddings: the default falls back
    assert rt.retrieval("hybrid")["dense"] is None  # an explicit request is not silently changed
    _build(corpus, embedder=FakeEmbedder())
    rt = pipeline.Runtime.load()
    monkeypatch.setattr(pipeline, "load_embedder", lambda name: FakeEmbedder())
    r = rt.retrieval()
    assert r["mode"] == "hybrid"
    hits = r["dense"]("Krishna spoke of dharma and the Self.", 1, None, None)
    assert hits[0][0] == "mbh.b01.s001.p02"  # the fake embedder maps identical text to the same vector


def test_rebuild_reuses_vectors_when_passage_texts_are_unchanged(corpus, monkeypatch):  # noqa: F811
    _build(corpus, embedder=FakeEmbedder())
    first = config.EMBEDDINGS_FILE.read_bytes()
    fake = FakeEmbedder()
    logs = []
    pipeline.build(corpus, log=logs.append, download=False, embedder=fake)
    assert fake.seen == [] and any("reused" in m for m in logs)
    assert config.EMBEDDINGS_FILE.read_bytes() == first
    pipeline.Runtime.load()  # the manifest matches the reused files


@pytest.mark.parametrize("change", ["text", "model", "corrupt"])
def test_vectors_are_recomputed_when_anything_they_depend_on_changed(corpus, monkeypatch, change):  # noqa: F811
    from test_embed import PASSAGES as SEGMENTED
    _build(corpus, embedder=FakeEmbedder())
    fake = FakeEmbedder()
    if change == "text":
        edited = {w: [make_passage(p.id, p.work, p.text + " Edited.", kind=p.kind) for p in ps]
                  for w, ps in SEGMENTED.items()}
        monkeypatch.setattr(pipeline, "segment_work", lambda w: edited[w.id])
    elif change == "model":
        fake.name = "fake/other-model"
    else:
        raw = bytearray(config.EMBEDDINGS_FILE.read_bytes())
        raw[-1] ^= 0xFF
        config.EMBEDDINGS_FILE.write_bytes(bytes(raw))
    _build(corpus, embedder=fake)
    assert fake.seen  # embedded again
    pipeline.Runtime.load()


def test_cli_search_mode_and_eval_retriever_flags(corpus, capsys, monkeypatch):  # noqa: F811
    _build(corpus)
    with pytest.raises(SystemExit, match="hybrid retrieval needs embeddings"):
        cli.main(["search", "dharma", "--mode", "hybrid"])
    cli.main(["search", "dharma", "--mode", "bm25"])
    baseline = config.MANIFEST_FILE.with_name("baseline.json")
    baseline.write_text(json.dumps({"k": 8, "retriever": "hybrid", "metrics": {}}), encoding="utf-8")
    monkeypatch.setattr(config, "EVAL_FILE", config.MANIFEST_FILE.with_name("items.jsonl"))
    config.EVAL_FILE.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="recorded with the hybrid retriever"):
        cli.main(["eval", "--retriever", "bm25", "--check", str(baseline)])
    with pytest.raises(SystemExit, match="retrieval only"):
        cli.main(["eval", "--retriever", "all", "--llm"])


def test_empty_works_list_means_no_filter_in_every_mode(gazetteer):
    dense = fake_dense(["b.2", "a.2", "a.1"])
    for mode in ("dense", "hybrid"):
        assert search(INDEX, gazetteer, "Self", k=3, works=[], mode=mode, dense=dense)
    assert dense.calls[0][2] is None


def test_ids_unknown_to_this_build_are_skipped(gazetteer):
    def stale(question, n, works=None, kinds=None):
        return [("gone.1", 0.9), ("b.2", 0.8)]
    assert ids(search(INDEX, gazetteer, "Self", k=2, mode="dense", dense=stale)) == ["b.2"]
    assert "b.2" in ids(search(INDEX, gazetteer, "Self", k=5, mode="hybrid", dense=stale))


def test_web_search_reports_hybrid_and_falls_back_to_bm25(corpus, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient

    from ikg.vectorstore import VectorStoreError
    from ikg.web.app import create_app
    monkeypatch.setattr(config, "RETRIEVER", "hybrid")
    monkeypatch.setattr(pipeline, "load_embedder", lambda name: FakeEmbedder())
    _build(corpus, embedder=FakeEmbedder())
    monkeypatch.setenv("IKG_ADMIN_TOKEN", "secret")
    with TestClient(create_app()) as client:
        r = client.get("/api/search", params={"q": "Krishna spoke of dharma and the Self."})
        assert r.json()["retriever"] == "hybrid" and r.json()["results"]

        def down(self, *args, **kwargs):
            raise VectorStoreError("cannot use Postgres at postgresql://ikg:***@db/ikg")
        monkeypatch.setattr(pipeline.Runtime, "dense", down)
        r = client.get("/api/search", params={"q": "dharma"})
        assert r.status_code == 200 and r.json()["retriever"] == "bm25" and r.json()["results"]
        page = client.get("/", params={"q": "dharma"}).text
        assert "keyword (BM25) results" in page and "Partha asked Kesava" in page


def test_web_startup_falls_back_when_the_query_model_cannot_load(corpus, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient

    from ikg.embed import EmbedError
    from ikg.web.app import create_app
    monkeypatch.setattr(config, "RETRIEVER", "hybrid")
    _build(corpus, embedder=FakeEmbedder())

    def offline(name):
        raise EmbedError("could not load embedding model: no network")
    monkeypatch.setattr(pipeline, "load_embedder", offline)
    monkeypatch.setenv("IKG_ADMIN_TOKEN", "secret")
    with TestClient(create_app()) as client:
        r = client.get("/api/search", params={"q": "dharma"})
        assert r.status_code == 200 and r.json()["retriever"] == "bm25"


def test_eval_check_passes_with_a_matching_retriever(corpus, capsys, monkeypatch):  # noqa: F811
    _build(corpus)
    monkeypatch.setattr(config, "EVAL_FILE", config.MANIFEST_FILE.with_name("items.jsonl"))
    config.EVAL_FILE.write_text(json.dumps({"id": "t-1", "type": "retrieval", "question": "dharma",
                                            "expected": ["mbh.b01"]}) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "EVAL_RESULTS_DIR", config.MANIFEST_FILE.with_name("results"))
    baseline = config.MANIFEST_FILE.with_name("baseline.json")
    baseline.write_text(json.dumps({"k": 8, "retriever": "bm25", "metrics": {"n_retrieval": 1}}), encoding="utf-8")
    cli.main(["eval", "--retriever", "bm25", "--check", str(baseline)])
    assert "No regression" in capsys.readouterr().out


@pytest.mark.parametrize("damage", ["float64", "not normalised", "nan"])
def test_reuse_refuses_vectors_that_fresh_ones_would_never_be(corpus, damage):  # noqa: F811
    _build(corpus, embedder=FakeEmbedder())
    meta = json.loads(config.EMBEDDINGS_META_FILE.read_text(encoding="utf-8"))
    with config.EMBEDDINGS_FILE.open("rb") as f:
        matrix = np.load(f)
    matrix = {"float64": matrix.astype(np.float64), "not normalised": matrix * 2,
              "nan": np.where(np.arange(matrix.size).reshape(matrix.shape) == 0, np.nan, matrix)}[damage]
    with config.EMBEDDINGS_FILE.open("wb") as f:
        np.save(f, matrix)
    import hashlib
    meta["matrix_sha256"] = hashlib.sha256(config.EMBEDDINGS_FILE.read_bytes()).hexdigest()  # a consistent fake
    config.EMBEDDINGS_META_FILE.write_text(json.dumps(meta), encoding="utf-8")
    fake = FakeEmbedder()
    _build(corpus, embedder=fake)
    assert fake.seen  # recomputed, not reused
