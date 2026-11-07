"""Nearest-passage search over the build's embeddings (docs/08 A2).

One interface, two stores:

- `FileVectorStore`: exact search over the `embeddings.npy` matrix in memory. The default, and
  what CI uses. About 15k x 384 floats: a full scan is a few milliseconds.
- `PgVectorStore`: Postgres with pgvector and an HNSW index (`docker compose up db`), selected
  with `IKG_VECTOR_STORE=postgresql://...`. Approximate; `ikg vectors compare` measures its
  recall against the exact store.

Both take the same filters, applied inside the store rather than after it, so a search limited to
one work or to primary text still returns `k` passages when there are `k` to return. (For pgvector
this holds while the table is smaller than `hnsw.max_scan_tuples`, 20,000 by default; a much larger
corpus with a very selective filter may need it raised.) Filters are the passage's work and kind:
commentary stays commentary in every path (docs/08 rule 1).

Rows are L2-normalised (ikg.embed), so the inner product is the cosine similarity and is the score
both stores return. The file store breaks exact ties by passage order, including at the k-th place,
so its results are deterministic; Postgres orders by distance only, so among exactly tied passages
it may pick others.

The Postgres table mirrors one build. It records the SHA-256 of the `embeddings.npy` it was loaded
from (from the manifest), and `PgVectorStore.sync` reloads it in one transaction when the build
changes, under an advisory lock so two processes loading the same new build load it once. During a
reload, searches already running finish on the old rows; searches that start after it began wait
for it (seconds at this size) and then see the new rows. The tables are a disposable copy: tests
that point IKG_TEST_PG_DSN at the development database drop them, which costs one reload.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Sequence

from ikg.embed import _numpy

if TYPE_CHECKING:
    import numpy as np

    from ikg.segment import Passage

TABLE = "ikg_passage_vectors"
META_TABLE = "ikg_vector_build"
LOCK_KEY = 0x494B4756  # "IKGV": pg_advisory_xact_lock key for syncs
HNSW_M, HNSW_EF_CONSTRUCTION = 16, 64
EF_SEARCH_MIN = 100  # candidates the HNSW scan keeps; raised to 2k for larger k
EF_SEARCH_MAX = 1000  # pgvector's upper limit for hnsw.ef_search


class VectorStoreError(Exception):
    pass


@dataclass(frozen=True)
class VectorHit:
    passage_id: str
    score: float  # cosine similarity, -1 to 1


class VectorStore(Protocol):
    name: str

    def search(self, vector: "np.ndarray", k: int, works: Sequence[str] | None = None,
               kinds: Sequence[str] | None = None) -> list[VectorHit]:
        """The `k` passages nearest to `vector` (normalised, same model as the build), best first,
        among those whose work is in `works` and kind in `kinds` (None: no limit)."""
        ...


def _query_vector(vector, dim: int) -> "np.ndarray":
    np = _numpy()
    v = np.asarray(vector, dtype=np.float32).reshape(-1)
    if v.shape[0] != dim:
        raise VectorStoreError(f"query vector has {v.shape[0]} dimensions, the build's embeddings have {dim}")
    if not np.all(np.isfinite(v)):
        raise VectorStoreError("query vector has non-finite values")
    return v


class FileVectorStore:
    """Exact inner-product search over the build's matrix."""
    name = "file"

    def __init__(self, matrix: "np.ndarray", passages: Sequence["Passage"]):
        np = _numpy()
        if matrix.shape[0] != len(passages):
            raise VectorStoreError(f"{matrix.shape[0]} vectors for {len(passages)} passages")
        self.matrix = matrix
        self.ids = [p.id for p in passages]
        self.works = np.array([p.work for p in passages], dtype=object)
        self.kinds = np.array([p.kind for p in passages], dtype=object)

    def search(self, vector, k, works=None, kinds=None) -> list[VectorHit]:
        np = _numpy()
        if k <= 0:
            return []
        v = _query_vector(vector, self.matrix.shape[1])
        rows = np.arange(self.matrix.shape[0])
        if works is not None:
            rows = rows[np.isin(self.works[rows], list(works))]
        if kinds is not None:
            rows = rows[np.isin(self.kinds[rows], list(kinds))]
        if rows.size == 0:
            return []
        scores = self.matrix[rows] @ v
        if rows.size > k:  # keep everything that scores at least the k-th best, ties included
            kth = np.partition(scores, rows.size - k)[rows.size - k]
            keep = scores >= kth
            rows, scores = rows[keep], scores[keep]
        order = np.lexsort((rows, -scores))[:k]  # score descending, then passage order
        return [VectorHit(self.ids[rows[i]], float(scores[i])) for i in order]


class PgVectorStore:
    """Postgres + pgvector. `dsn` is a libpq connection string or URL."""
    name = "pgvector"

    def __init__(self, dsn: str):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as exc:
            raise VectorStoreError('the pgvector store needs psycopg and pgvector: pip install -e ".[pg]"') from exc
        self._psycopg, self._register = psycopg, register_vector
        self.dsn = dsn
        self.dim: int | None = None
        self._conn = None
        self._lock = threading.Lock()  # one connection: web requests run in a thread pool

    def _connect(self):
        if self._conn is None or self._conn.closed:
            try:
                conn = self._psycopg.connect(self.dsn, autocommit=True, connect_timeout=10)
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                self._register(conn)
            except self._psycopg.Error as exc:
                raise VectorStoreError(f"cannot use Postgres at {_redact(self.dsn)}: {exc}") from exc
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def loaded_build(self) -> dict | None:
        """What the table holds: {sha256, model, dim, rows}, or None if it was never loaded."""
        conn = self._connect()
        present = conn.execute("SELECT to_regclass(%s), to_regclass(%s)", (META_TABLE, TABLE)).fetchone()
        if not all(present):  # either table dropped by hand: treat as never loaded
            return None
        row = conn.execute(f"SELECT sha256, model, dim, rows FROM {META_TABLE} WHERE id = 1").fetchone()
        return dict(zip(("sha256", "model", "dim", "rows"), row)) if row else None

    def sync(self, matrix: "np.ndarray", passages: Sequence["Passage"], sha256: str, model: str,
             log=print) -> bool:
        """Make the table hold exactly this build. Returns True if it had to reload."""
        if matrix.shape[0] != len(passages):
            raise VectorStoreError(f"{matrix.shape[0]} vectors for {len(passages)} passages")
        dim = int(matrix.shape[1])
        with self._lock:
            return self._sync(matrix, passages, sha256, model, dim, log)

    def _sync(self, matrix, passages, sha256, model, dim, log) -> bool:
        current = self.loaded_build()
        if current and (current["sha256"], current["rows"], current["dim"]) == (sha256, len(passages), dim):
            self.dim = dim
            return False
        conn = self._connect()
        t0 = time.time()
        try:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                conn.execute(f"CREATE TABLE IF NOT EXISTS {META_TABLE} (id int PRIMARY KEY CHECK (id = 1), "
                             "sha256 text NOT NULL, model text NOT NULL, dim int NOT NULL, rows int NOT NULL)")
                row = conn.execute(f"SELECT sha256, rows, dim, to_regclass(%s) IS NOT NULL FROM {META_TABLE} "
                                   "WHERE id = 1", (TABLE,)).fetchone()
                if row and row == (sha256, len(passages), dim, True):  # another process just loaded it
                    self.dim = dim
                    return False
                # DDL is transactional in Postgres: nobody sees a half-loaded table.
                conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
                conn.execute(f"CREATE TABLE {TABLE} (row_id int PRIMARY KEY, passage_id text NOT NULL UNIQUE, "
                             f"work text NOT NULL, kind text NOT NULL, embedding vector({dim}) NOT NULL)")
                with conn.cursor().copy(f"COPY {TABLE} (row_id, passage_id, work, kind, embedding) "
                                        "FROM STDIN WITH (FORMAT BINARY)") as copy:
                    copy.set_types(["int4", "text", "text", "text", "vector"])
                    for i, (p, vec) in enumerate(zip(passages, matrix)):
                        copy.write_row((i, p.id, p.work, p.kind, vec))
                conn.execute(f"CREATE INDEX ON {TABLE} USING hnsw (embedding vector_ip_ops) "
                             f"WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})")
                conn.execute(f"CREATE INDEX ON {TABLE} (work)")
                conn.execute(f"CREATE INDEX ON {TABLE} (kind)")
                conn.execute(f"INSERT INTO {META_TABLE} VALUES (1, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE "
                             "SET sha256 = EXCLUDED.sha256, model = EXCLUDED.model, dim = EXCLUDED.dim, "
                             "rows = EXCLUDED.rows", (sha256, model, dim, len(passages)))
        except self._psycopg.Error as exc:
            raise VectorStoreError(f"loading vectors into Postgres failed: {exc}") from exc
        self.dim = dim
        log(f"pgvector: loaded {len(passages):,} vectors in {time.time() - t0:.1f}s")
        return True

    def search(self, vector, k, works=None, kinds=None) -> list[VectorHit]:
        if k <= 0:
            return []
        if self.dim is None:
            raise VectorStoreError("the pgvector store has not been synced with a build")
        v = _query_vector(vector, self.dim)
        conn = self._connect()
        where, params = [], []
        if works is not None:
            where.append("work = ANY(%s)")
            params.append(list(works))
        if kinds is not None:
            where.append("kind = ANY(%s)")
            params.append(list(kinds))
        sql = (f"SELECT passage_id, row_id, -(embedding <#> %s) AS score FROM {TABLE}"
               + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY embedding <#> %s LIMIT %s")
        try:
            with self._lock, conn.transaction():
                # Filters are applied during the index scan; iterative scans keep going until k rows
                # pass them (pgvector >= 0.8), instead of returning fewer.
                conn.execute(f"SET LOCAL hnsw.ef_search = {min(EF_SEARCH_MAX, max(EF_SEARCH_MIN, 2 * int(k)))}")
                conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                rows = conn.execute(sql, [v, *params, v, k]).fetchall()
        except self._psycopg.Error as exc:
            raise VectorStoreError(f"pgvector search failed: {exc}") from exc
        rows.sort(key=lambda r: (-r[2], r[1]))  # relaxed_order may be slightly out of order
        return [VectorHit(pid, float(score)) for pid, _, score in rows]


def compare(exact: VectorStore, other: VectorStore, matrix: "np.ndarray", passages: Sequence["Passage"],
            primary_kinds: Sequence[str], n: int = 200, k: int = 8, seed: int = 0) -> dict:
    """Recall@k of `other` against the exact store, and the latency of both, for `n` sampled passage
    vectors used as queries, under three filters: none, primary text only, and one work's primary
    text (the query passage's work). Passage vectors stand in for questions; they measure the index,
    not retrieval quality, which the eval measures (docs/08 A3)."""
    np = _numpy()
    rows = np.random.default_rng(seed).choice(len(passages), size=min(n, len(passages)), replace=False)
    report = {"stores": [exact.name, other.name], "queries": int(rows.size), "k": k}
    for label in ("no filter", "primary only", "one work, primary only"):
        recalls, times = [], {exact.name: [], other.name: []}
        for r in rows:
            works = [passages[r].work] if label.startswith("one work") else None
            kinds = list(primary_kinds) if label != "no filter" else None
            found = {}
            for store in (exact, other):
                t0 = time.perf_counter()
                found[store.name] = [h.passage_id for h in store.search(matrix[r], k, works, kinds)]
                times[store.name].append((time.perf_counter() - t0) * 1000)
            truth = found[exact.name]
            if truth:
                recalls.append(len(set(truth) & set(found[other.name])) / len(truth))
        report[label] = {
            "recall_at_k": round(float(np.mean(recalls)), 4) if recalls else None,
            "min_recall": round(float(np.min(recalls)), 4) if recalls else None,
            "p50_ms": {name: round(float(np.percentile(t, 50)), 2) for name, t in times.items()},
            "p95_ms": {name: round(float(np.percentile(t, 95)), 2) for name, t in times.items()},
        }
    return report


def _redact(dsn: str) -> str:
    """The DSN without its password, for error messages."""
    import re
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", re.sub(r"(password=)\S+", r"\1***", dsn))


def open_store(spec: str, matrix: "np.ndarray", passages: Sequence["Passage"], sha256: str, model: str,
               log=print) -> VectorStore:
    """The store `spec` names ("file", or a postgresql:// URL), holding this build's vectors."""
    if spec == "file":
        return FileVectorStore(matrix, passages)
    if spec.startswith(("postgresql://", "postgres://")):
        store = PgVectorStore(spec)
        store.sync(matrix, passages, sha256, model, log)
        return store
    raise VectorStoreError(f"IKG_VECTOR_STORE must be 'file' or a postgresql:// URL, not {spec!r}")

