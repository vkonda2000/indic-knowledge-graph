"""Vector stores (docs/08 A2). The file store is tested everywhere. The pgvector tests need a Postgres
with pgvector and run only when IKG_TEST_PG_DSN names one (CI's `pgvector` job; locally:
`docker compose up -d db`, then IKG_TEST_PG_DSN=postgresql://ikg:ikg@127.0.0.1:5433/ikg)."""

import os

import numpy as np
import pytest
from conftest import make_passage
from test_embed import FakeEmbedder, _build, corpus  # noqa: F401  (corpus is a fixture)

from ikg import cli
from ikg.pipeline import Runtime
from ikg.segment import PRIMARY_KINDS
from ikg.vectorstore import FileVectorStore, PgVectorStore, VectorStoreError, compare, open_store

PG_DSN = os.environ.get("IKG_TEST_PG_DSN")
needs_pg = pytest.mark.skipif(not PG_DSN, reason="set IKG_TEST_PG_DSN to a Postgres with pgvector")

# Six passages in two works, primary and commentary, on a 3-d unit circle-ish layout.
PASSAGES = [
    make_passage("a.1", "alpha", "x", kind="verse"),
    make_passage("a.1.c1", "alpha", "x", kind="commentary"),
    make_passage("a.2", "alpha", "x", kind="narrative"),
    make_passage("b.1", "beta", "x", kind="verse"),
    make_passage("b.1.c1", "beta", "x", kind="commentary"),
    make_passage("b.2", "beta", "x", kind="verse"),
]
MATRIX = np.array([[1, 0, 0], [0.99, 0.141, 0], [0.6, 0.8, 0], [0, 1, 0], [0, 0.99, 0.141], [0, 0, 1]],
                  dtype=np.float32)
MATRIX /= np.linalg.norm(MATRIX, axis=1, keepdims=True)
Q = np.array([1, 0, 0], dtype=np.float32)


def ids(hits):
    return [h.passage_id for h in hits]


def check_store(store):
    """The behaviour both stores share."""
    hits = store.search(Q, 3)
    assert ids(hits) == ["a.1", "a.1.c1", "a.2"]
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)
    assert hits[0].score >= hits[1].score >= hits[2].score
    # Filters are applied in the store, so k results come back even when the nearest are excluded.
    assert ids(store.search(Q, 3, kinds=PRIMARY_KINDS)) == ["a.1", "a.2", "b.1"]
    assert ids(store.search(Q, 2, works=["beta"])) == ["b.1", "b.1.c1"]
    assert ids(store.search(Q, 5, works=["beta"], kinds=PRIMARY_KINDS)) == ["b.1", "b.2"]
    assert store.search(Q, 3, works=["gamma"]) == []
    assert store.search(Q, 0) == []
    assert len(store.search(Q, 50)) == 6
    with pytest.raises(VectorStoreError, match="dimensions"):
        store.search(np.zeros(4, dtype=np.float32), 3)


def test_file_store():
    check_store(FileVectorStore(MATRIX, PASSAGES))


def test_file_store_breaks_ties_by_passage_order():
    tied = np.tile(np.array([[1, 0, 0]], dtype=np.float32), (6, 1))
    assert ids(FileVectorStore(tied, PASSAGES).search(Q, 4)) == ["a.1", "a.1.c1", "a.2", "b.1"]


def test_file_store_ties_at_the_kth_place_go_to_the_earlier_passage():
    rng = np.random.default_rng(2)
    for _ in range(50):
        n = int(rng.integers(5, 40))
        values = rng.integers(0, 4, size=n).astype(np.float32)  # many exact ties
        matrix = np.stack([values, np.ones(n, dtype=np.float32)], axis=1)
        passages = [make_passage(f"p.{i}", "w", "x") for i in range(n)]
        k = int(rng.integers(1, n))
        expected = sorted(range(n), key=lambda i: (-values[i], i))[:k]
        got = FileVectorStore(matrix, passages).search(np.array([1, 0], dtype=np.float32), k)
        assert ids(got) == [f"p.{i}" for i in expected]


def test_file_store_rejects_mismatched_rows():
    with pytest.raises(VectorStoreError, match="5 vectors for 6 passages"):
        FileVectorStore(MATRIX[:5], PASSAGES)


def test_open_store_rejects_unknown_spec():
    with pytest.raises(VectorStoreError, match="'file' or a postgresql:// URL"):
        open_store("qdrant://x", MATRIX, PASSAGES, "sha", "m")


def test_runtime_vector_store_needs_embeddings(corpus):  # noqa: F811
    _build(corpus)
    with pytest.raises(VectorStoreError, match="build --embed"):
        Runtime.load().vector_store()


def test_runtime_vector_store_searches_the_build(corpus):  # noqa: F811
    _build(corpus, embedder=FakeEmbedder())
    rt = Runtime.load()
    store = rt.vector_store()
    assert store is rt.vector_store()  # opened once
    first = rt.index.passages[0]
    hits = store.search(rt.embeddings[0], 1)
    assert ids(hits) == [first.id]


def test_compare_file_with_itself_has_full_recall():
    report = compare(FileVectorStore(MATRIX, PASSAGES), FileVectorStore(MATRIX, PASSAGES), MATRIX, PASSAGES,
                     PRIMARY_KINDS, n=6, k=2)
    assert report["queries"] == 6
    for label in ("no filter", "primary only", "one work, primary only"):
        assert report[label]["recall_at_k"] == 1.0


def test_cli_vectors_compare(corpus, capsys):  # noqa: F811
    _build(corpus, embedder=FakeEmbedder())
    cli.main(["vectors", "compare", "--store", "file", "-n", "3", "-k", "2"])
    assert '"recall_at_k": 1.0' in capsys.readouterr().out


@pytest.fixture
def pg():
    store = PgVectorStore(PG_DSN)
    conn = store._connect()
    conn.execute("DROP TABLE IF EXISTS ikg_passage_vectors, ikg_vector_build")
    yield store
    conn.execute("DROP TABLE IF EXISTS ikg_passage_vectors, ikg_vector_build")
    store.close()


@needs_pg
def test_pg_store(pg):
    assert pg.loaded_build() is None
    assert pg.sync(MATRIX, PASSAGES, "sha-1", "fake") is True
    assert pg.loaded_build() == {"sha256": "sha-1", "model": "fake", "dim": 3, "rows": 6}
    check_store(pg)


@needs_pg
def test_pg_sync_reloads_only_when_the_build_changes(pg):
    assert pg.sync(MATRIX, PASSAGES, "sha-1", "fake") is True
    assert pg.sync(MATRIX, PASSAGES, "sha-1", "fake") is False
    other = PgVectorStore(PG_DSN)  # a second process sees the loaded build and does not reload
    assert other.sync(MATRIX, PASSAGES, "sha-1", "fake") is False
    other.close()
    flipped = MATRIX[::-1].copy()
    assert pg.sync(flipped, PASSAGES, "sha-2", "fake") is True
    assert ids(pg.search(Q, 1)) == ["b.2"]  # row 5 of the new build is [1, 0, 0]


@needs_pg
def test_pg_sync_reloads_when_the_vector_table_was_dropped(pg):
    pg.sync(MATRIX, PASSAGES, "sha-1", "fake")
    pg._connect().execute("DROP TABLE ikg_passage_vectors")
    assert pg.loaded_build() is None
    assert pg.sync(MATRIX, PASSAGES, "sha-1", "fake") is True
    assert ids(pg.search(Q, 1)) == ["a.1"]


@needs_pg
def test_pg_large_k_stays_within_ef_search_limit(pg):
    pg.sync(MATRIX, PASSAGES, "sha-1", "fake")
    assert len(pg.search(Q, 600)) == 6


@needs_pg
def test_pg_store_needs_a_sync_before_search():
    with pytest.raises(VectorStoreError, match="not been synced"):
        PgVectorStore(PG_DSN).search(Q, 3)


@needs_pg
def test_pg_store_matches_exact_search_on_a_larger_set():
    rng = np.random.default_rng(1)
    n, dim = 3000, 32
    matrix = rng.normal(size=(n, dim)).astype(np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    passages = [make_passage(f"p.{i}", ("alpha", "beta", "gamma")[i % 3], "x",
                             kind=("verse", "commentary")[i % 2]) for i in range(n)]
    store = PgVectorStore(PG_DSN)
    try:
        store.sync(matrix, passages, "sha-big", "fake")
        report = compare(FileVectorStore(matrix, passages), store, matrix, passages, PRIMARY_KINDS, n=50, k=10)
    finally:
        store._connect().execute("DROP TABLE IF EXISTS ikg_passage_vectors, ikg_vector_build")
        store.close()
    for label in ("no filter", "primary only", "one work, primary only"):
        assert report[label]["recall_at_k"] >= 0.9, (label, report[label])


def test_pg_dsn_password_is_redacted_in_errors():
    pytest.importorskip("psycopg")
    store = PgVectorStore("postgresql://ikg:s3cret@127.0.0.1:1/ikg")
    with pytest.raises(VectorStoreError) as err:
        store._connect()
    assert "s3cret" not in str(err.value) and "***" in str(err.value)
