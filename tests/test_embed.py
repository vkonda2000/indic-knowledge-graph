"""Passage embeddings at build time (docs/08 A1). A fake embedder stands in for the model, so no test
downloads one or needs a network."""

import hashlib
import json

import numpy as np
import pytest
import yaml
from conftest import make_passage, make_work

from ikg import cli, config, embed, pipeline
from ikg.embed import EmbedError, OllamaEmbedder, embed_passages, read_embeddings
from ikg.pipeline import BuildError, Runtime, build

DIM = 8
PASSAGES = {
    "mahabharata": [make_passage("mbh.b01.s001.p01", "mahabharata", "Partha asked Kesava about righteousness."),
                    make_passage("mbh.b01.s001.p02", "mahabharata", "Krishna spoke of dharma and the Self.")],
    "ramayana": [make_passage("ram.b01.s001.p01", "ramayana", "Sita and Rama walked in the forest.", kind="verse")],
}


class FakeEmbedder:
    """Deterministic vectors from a hash of the text; records what it was asked to embed."""
    name, library = "fake/hash-8", "test"

    def __init__(self):
        self.seen: list[str] = []

    def embed(self, texts):
        self.seen.extend(texts)
        rows = [[b - 127.5 for b in hashlib.sha256(t.encode()).digest()[:DIM]] for t in texts]
        return np.asarray(rows, dtype=np.float32)


@pytest.fixture
def corpus(tmp_path, monkeypatch, gazetteer):
    """Config pointed at tmp_path, and a build that segments two works into PASSAGES without downloads."""
    for name, rel in [("USER_GAZETTEER_FILE", "gazetteer.yaml"), ("PASSAGES_FILE", "p/passages.jsonl"),
                      ("MENTIONS_FILE", "p/mentions.jsonl"), ("INDEX_FILE", "p/bm25.pkl"),
                      ("GRAPH_FILE", "p/g.graphml"), ("RDF_FILE", "p/ikg.ttl"),
                      ("GAZETTEER_SNAPSHOT_FILE", "p/gazetteer.yaml"), ("MANIFEST_FILE", "p/manifest.json"),
                      ("BUILD_LOCK_FILE", "p/.build.lock"), ("EMBEDDINGS_FILE", "p/embeddings.npy"),
                      ("EMBEDDINGS_META_FILE", "p/embeddings.json")]:
        monkeypatch.setattr(config, name, tmp_path / rel)
    entities = [{k: v for k, v in vars(e).items() if v} for e in gazetteer.entities.values()]
    config.USER_GAZETTEER_FILE.write_text(yaml.safe_dump({"entities": entities}), encoding="utf-8")
    monkeypatch.setattr(pipeline, "segment_work", lambda w: PASSAGES[w.id])
    monkeypatch.setattr(config, "EMBED", False)
    return [make_work("mahabharata"), make_work("ramayana")]


def _build(works, **kw):
    return build(works, log=lambda _: None, download=False, **kw)


def test_build_writes_normalised_embeddings_in_passage_order(corpus):
    fake = FakeEmbedder()
    stats = _build(corpus, embedder=fake)
    assert stats["embedding_model"] == "fake/hash-8"
    ids = [p.id for ps in PASSAGES.values() for p in ps]
    assert fake.seen == [p.text for ps in PASSAGES.values() for p in ps]  # the text only, no metadata

    manifest = json.loads(config.MANIFEST_FILE.read_text(encoding="utf-8"))
    assert manifest["embedding"] == {"model": "fake/hash-8", "library": "test", "dim": DIM,
                                     "normalized": True, "text": "passage text"}
    assert {"embeddings", "embeddings_meta"} <= manifest["artefacts"].keys()

    rt = Runtime.load()
    assert rt.embeddings.shape == (3, DIM) and rt.embeddings.dtype == np.float32
    assert np.allclose(np.linalg.norm(rt.embeddings, axis=1), 1.0)
    assert rt.embedding["model"] == "fake/hash-8"
    # Row i is passage i: the same text gives the same vector.
    again = embed_passages(list(rt.index.passages), FakeEmbedder(), log=lambda _: None)
    assert np.array_equal(rt.embeddings, again)
    assert json.loads(config.EMBEDDINGS_META_FILE.read_text(encoding="utf-8"))["ids"] == ids


def test_identical_builds_give_identical_embeddings_and_manifest(corpus):
    _build(corpus, embedder=FakeEmbedder())
    first = config.EMBEDDINGS_FILE.read_bytes(), config.MANIFEST_FILE.read_text(encoding="utf-8")
    _build(corpus, embedder=FakeEmbedder())
    assert (config.EMBEDDINGS_FILE.read_bytes(), config.MANIFEST_FILE.read_text(encoding="utf-8")) == first


def test_build_without_embeddings_removes_earlier_vectors(corpus):
    _build(corpus, embedder=FakeEmbedder())
    _build(corpus)  # IKG_EMBED is off
    assert not config.EMBEDDINGS_FILE.exists() and not config.EMBEDDINGS_META_FILE.exists()
    manifest = json.loads(config.MANIFEST_FILE.read_text(encoding="utf-8"))
    assert "embedding" not in manifest and "embeddings" not in manifest["artefacts"]
    rt = Runtime.load()
    assert rt.embeddings is None and rt.embedding is None


def test_runtime_refuses_changed_embeddings(corpus):
    _build(corpus, embedder=FakeEmbedder())
    with config.EMBEDDINGS_FILE.open("wb") as f:
        np.save(f, np.zeros((3, DIM), dtype=np.float32))
    with pytest.raises(ValueError, match="does not match the build manifest"):
        Runtime.load()


def test_rows_must_be_the_passages_in_order(corpus):
    _build(corpus, embedder=FakeEmbedder())
    ids = [p.id for ps in PASSAGES.values() for p in ps]
    read_embeddings(config.EMBEDDINGS_FILE, config.EMBEDDINGS_META_FILE, ids)
    with pytest.raises(ValueError, match="not the built passages in order"):
        read_embeddings(config.EMBEDDINGS_FILE, config.EMBEDDINGS_META_FILE, list(reversed(ids)))


@pytest.mark.parametrize("rows, message", [
    (np.zeros((2, DIM), dtype=np.float32), "returned shape"),          # one row short
    (np.zeros((3, DIM), dtype=np.float32), "zero vector"),
    (np.full((3, DIM), np.nan, dtype=np.float32), "non-finite"),
])
def test_a_bad_embedder_fails_the_build_and_keeps_the_previous_one(corpus, rows, message):
    _build(corpus)
    before = config.MANIFEST_FILE.read_text(encoding="utf-8")

    class Bad(FakeEmbedder):
        def embed(self, texts):
            return rows

    with pytest.raises(BuildError, match=message):
        _build(corpus, embedder=Bad())
    assert config.MANIFEST_FILE.read_text(encoding="utf-8") == before
    Runtime.load()


def test_a_failed_write_keeps_the_previous_build(corpus, monkeypatch):
    _build(corpus, embedder=FakeEmbedder())
    before = config.MANIFEST_FILE.read_text(encoding="utf-8"), config.EMBEDDINGS_FILE.read_bytes()

    def disk_full(*args):
        raise OSError("disk full")
    monkeypatch.setattr(pipeline, "write_embeddings", disk_full)
    with pytest.raises(BuildError, match="disk full"):
        _build(corpus, embedder=FakeEmbedder())
    assert (config.MANIFEST_FILE.read_text(encoding="utf-8"), config.EMBEDDINGS_FILE.read_bytes()) == before
    assert not list(config.PASSAGES_FILE.parent.glob("*.tmp"))
    assert Runtime.load().embeddings.shape == (3, DIM)


def test_a_build_with_embeddings_needs_numpy_to_load(corpus, monkeypatch):
    _build(corpus, embedder=FakeEmbedder())

    def no_numpy(*args):
        raise EmbedError('embeddings need numpy: pip install -e ".[embed]"')
    monkeypatch.setattr(pipeline, "read_embeddings", no_numpy)
    with pytest.raises(ValueError, match="cannot be read: embeddings need numpy"):
        Runtime.load()


def test_matrix_shape_must_match_its_metadata(corpus):
    _build(corpus, embedder=FakeEmbedder())
    meta = json.loads(config.EMBEDDINGS_META_FILE.read_text(encoding="utf-8"))
    config.EMBEDDINGS_META_FILE.write_text(json.dumps({**meta, "dim": DIM + 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="has shape"):
        read_embeddings(config.EMBEDDINGS_FILE, config.EMBEDDINGS_META_FILE, meta["ids"])


def test_no_passages_skips_embeddings(corpus, monkeypatch):
    monkeypatch.setattr(pipeline, "segment_work", lambda w: [])
    stats = _build(corpus, embedder=FakeEmbedder())
    assert stats["passages"] == 0 and stats["embedding_model"] is None
    assert not config.EMBEDDINGS_FILE.exists()


def test_embed_setting_loads_the_configured_model(corpus, monkeypatch):
    loaded = []
    monkeypatch.setattr(pipeline, "load_embedder", lambda: loaded.append(1) or FakeEmbedder())
    _build(corpus)
    assert not loaded
    monkeypatch.setattr(config, "EMBED", True)
    _build(corpus)
    assert loaded and config.EMBEDDINGS_FILE.exists()
    _build(corpus, embed=False)  # the CLI's --no-embed wins over IKG_EMBED
    assert not config.EMBEDDINGS_FILE.exists()


def test_embedder_that_cannot_load_fails_before_the_lock(corpus, monkeypatch):
    def unavailable():
        raise EmbedError('embeddings need fastembed: pip install -e ".[embed]"')
    monkeypatch.setattr(pipeline, "load_embedder", unavailable)
    with pytest.raises(BuildError, match=r"embeddings: .*\[embed\]"):
        _build(corpus, embed=True)
    assert not config.BUILD_LOCK_FILE.exists()


def test_cli_embed_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "build", lambda works, **kw: calls.append(kw["embed"]))
    monkeypatch.setattr(cli, "_select_works", lambda names: [])
    for argv, expected in ((["build"], None), (["build", "--embed"], True), (["build", "--no-embed"], False)):
        cli.main(argv)
        assert calls[-1] is expected


def test_ollama_embedder(monkeypatch):
    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[1.0, 0.0], [0.0, 2.0]]}

    def post(url, json, timeout):
        sent.update(url=url, body=json)
        return Response()

    monkeypatch.setattr(embed.requests, "post", post)
    e = embed.load_embedder("ollama:nomic-embed-text")
    assert isinstance(e, OllamaEmbedder) and e.name == "ollama:nomic-embed-text"
    rows = e.embed(["a", "b"])
    assert sent["url"].endswith("/api/embed")
    assert sent["body"] == {"model": "nomic-embed-text", "input": ["a", "b"]}
    assert rows.shape == (2, 2) and rows.dtype == np.float32

    monkeypatch.setattr(embed.requests, "post", lambda *a, **k: Response.__new__(type(
        "Short", (Response,), {"json": lambda self: {"embeddings": [[1.0]]}})))
    with pytest.raises(EmbedError, match="1 embeddings for 2 texts"):
        e.embed(["a", "b"])

    def refused(*args, **kwargs):
        raise embed.requests.ConnectionError("connection refused")
    monkeypatch.setattr(embed.requests, "post", refused)
    with pytest.raises(EmbedError, match="nomic-embed-text.*connection refused"):
        e.embed(["a"])
