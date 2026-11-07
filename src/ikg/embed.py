"""Dense passage embeddings, written at build time (docs/08 A1).

When embeddings are on (`ikg build --embed`, or `IKG_EMBED=on` for web rebuilds), the build embeds
every passage's text and writes two artefacts beside the BM25 index:

- `embeddings.npy`: a float32 matrix, one L2-normalised row per passage, so a dot product is the
  cosine similarity;
- `embeddings.json`: the model, the library and its version, the dimension, and the passage ID of
  every row, in order.

Both are checksummed in the manifest like every other artefact, and `Runtime` refuses rows whose IDs
do not match the passages it loaded. Search does not use them yet (docs/08 A2, A3).

The default model is `BAAI/bge-small-en-v1.5` through `fastembed` (ONNX on the CPU, 384 dimensions,
no API key, no cost). It is deterministic on a given machine; across CPUs the last bits of a float can
differ, which is why the manifest records the model and library version and checksums are per build.
`IKG_EMBED_MODEL=ollama:<model>` uses a local Ollama server instead (for example
`ollama:nomic-embed-text`).

numpy and fastembed are optional (`pip install -e ".[embed]"`), so they are imported only here and
only when embeddings are used.
"""

from __future__ import annotations

import hashlib
import json
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

import requests

from ikg import config

if TYPE_CHECKING:
    import numpy as np

    from ikg.segment import Passage

BATCH_SIZE = 64
# Instructions some models expect before a query (not before passages). fastembed does not add them.
QUERY_PREFIXES = {
    "BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-large-en-v1.5": "Represent this sentence for searching relevant passages: ",
}
OLLAMA_PREFIX = "ollama:"
OLLAMA_TIMEOUT = 300.0  # per batch; the first call loads the model


class EmbedError(Exception):
    pass


class Embedder(Protocol):
    name: str      # what the manifest records, e.g. "BAAI/bge-small-en-v1.5"
    library: str   # e.g. "fastembed 0.7.1", or "ollama"

    def embed(self, texts: list[str]) -> "np.ndarray":
        """One row per text, float32. Rows need not be normalised; `embed_passages` normalises."""
        ...


def _numpy():
    try:
        import numpy
    except ImportError as exc:
        raise EmbedError('embeddings need numpy: pip install -e ".[embed]"') from exc
    return numpy


class FastEmbedder:
    """A local ONNX model through fastembed. The model files are downloaded once and cached."""

    def __init__(self, name: str):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbedError('embeddings need fastembed: pip install -e ".[embed]"') from exc
        try:
            self._model = TextEmbedding(model_name=name)
        except Exception as exc:  # an unknown model name, or no network for the first download
            raise EmbedError(f"could not load embedding model {name!r}: {exc}") from exc
        self.name = name
        self.library = f"fastembed {metadata.version('fastembed')}"

    def embed(self, texts: list[str]) -> "np.ndarray":
        np = _numpy()
        try:
            return np.asarray(list(self._model.embed(texts, batch_size=BATCH_SIZE)), dtype=np.float32)
        except Exception as exc:  # a corrupt cached model, out of memory in ONNX Runtime
            raise EmbedError(f"{self.name} failed: {exc}") from exc


class OllamaEmbedder:
    """A model served by a local Ollama (`/api/embed`)."""

    def __init__(self, model: str):
        self.model = model
        self.name = OLLAMA_PREFIX + model
        self.library = "ollama"

    def embed(self, texts: list[str]) -> "np.ndarray":
        np = _numpy()
        try:
            r = requests.post(f"{config.OLLAMA_HOST}/api/embed", json={"model": self.model, "input": texts},
                              timeout=OLLAMA_TIMEOUT)
            r.raise_for_status()
            rows = r.json()["embeddings"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise EmbedError(f"Ollama embedding with {self.model!r} at {config.OLLAMA_HOST} failed: {exc}") from exc
        if len(rows) != len(texts):
            raise EmbedError(f"Ollama returned {len(rows)} embeddings for {len(texts)} texts")
        return np.asarray(rows, dtype=np.float32)


def load_embedder(name: str | None = None) -> Embedder:
    """The embedder named by `name`, or by IKG_EMBED_MODEL."""
    name = name or config.EMBED_MODEL
    if name.startswith(OLLAMA_PREFIX):
        return OllamaEmbedder(name[len(OLLAMA_PREFIX):])
    return FastEmbedder(name)


def passage_text(p: "Passage") -> str:
    """What is embedded for a passage: its text only. Work, translator and kind stay filters, not
    content, so they cannot pull a commentary passage towards a query about the verse."""
    return p.text


def embed_passages(passages: list["Passage"], embedder: Embedder,
                   log: Callable[[str], None] = print) -> "np.ndarray":
    """An (n, dim) float32 matrix of L2-normalised rows, in the order of `passages`."""
    np = _numpy()
    if not passages:
        raise EmbedError("no passages to embed")
    parts, step = [], BATCH_SIZE * 16
    for start in range(0, len(passages), step):
        batch = passages[start:start + step]
        rows = embedder.embed([passage_text(p) for p in batch])
        if rows.ndim != 2 or rows.shape[0] != len(batch):
            raise EmbedError(f"{embedder.name} returned shape {rows.shape} for {len(batch)} passages")
        parts.append(rows.astype(np.float32, copy=False))
        log(f"embedded {min(start + step, len(passages)):,} / {len(passages):,} passages")
    matrix = np.concatenate(parts)
    if not np.all(np.isfinite(matrix)):
        raise EmbedError(f"{embedder.name} returned non-finite values")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise EmbedError(f"{embedder.name} returned a zero vector")
    return (matrix / norms).astype(np.float32)


def embed_query(text: str, embedder: Embedder) -> "np.ndarray":
    """One normalised query vector, with the model's query instruction if it has one. Use the
    embedder the build used (the manifest's `embedding.model`): vectors from two models don't mix."""
    np = _numpy()
    rows = embedder.embed([QUERY_PREFIXES.get(embedder.name, "") + text])
    if rows.ndim != 2 or rows.shape[0] != 1:
        raise EmbedError(f"{embedder.name} returned shape {rows.shape} for one query")
    v = rows[0].astype(np.float32)
    norm = float(np.linalg.norm(v))
    if not np.isfinite(norm) or norm == 0:
        raise EmbedError(f"{embedder.name} returned an unusable query vector")
    return v / norm


def texts_sha256(passages: list["Passage"]) -> str:
    """What the vectors depend on: every passage's id and embedded text, in order."""
    h = hashlib.sha256()
    for p in passages:
        h.update(f"{p.id}\t{passage_text(p)}\n".encode("utf-8"))
    return h.hexdigest()


def reusable_embeddings(passages: list["Passage"], embedder: Embedder, vectors_path: Path,
                        meta_path: Path) -> "np.ndarray | None":
    """The vectors already on disk, if they were made by this model and library version from exactly
    these passage texts, and the file is the one its metadata describes; otherwise None. A full
    re-embedding takes minutes, so a rebuild that changes no passage text (a gazetteer edit, CI with
    cached vectors) reuses them."""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (meta.get("model"), meta.get("library")) != (embedder.name, embedder.library):
            return None
        if meta.get("texts_sha256") != texts_sha256(passages):
            return None
        if hashlib.sha256(vectors_path.read_bytes()).hexdigest() != meta.get("matrix_sha256"):
            return None
        matrix, _ = read_embeddings(vectors_path, meta_path, [p.id for p in passages])
    except (OSError, ValueError, KeyError):
        return None
    np = _numpy()
    # The same checks as freshly computed vectors: a hand-made file must not slip through.
    if (matrix.dtype != np.float32 or not np.all(np.isfinite(matrix))
            or not np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-3)):
        return None
    return matrix


def write_embeddings(matrix: "np.ndarray", passages: list["Passage"], embedder: Embedder,
                     vectors_path: Path, meta_path: Path) -> dict:
    """Write the matrix and its metadata; return the summary the manifest records."""
    np = _numpy()
    with vectors_path.open("wb") as f:  # a file object, so np.save doesn't append ".npy" to a .tmp name
        np.save(f, matrix, allow_pickle=False)
    summary = {"model": embedder.name, "library": embedder.library, "dim": int(matrix.shape[1]),
               "normalized": True, "text": "passage text"}
    meta = {**summary, "texts_sha256": texts_sha256(passages),
            "matrix_sha256": hashlib.sha256(vectors_path.read_bytes()).hexdigest(),
            "ids": [p.id for p in passages]}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def read_embeddings(vectors_path: Path, meta_path: Path, passage_ids: list[str]) -> tuple["np.ndarray", dict]:
    """Load the matrix and its metadata, and check its rows are exactly these passages in this order."""
    np = _numpy()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    with vectors_path.open("rb") as f:
        matrix = np.load(f, allow_pickle=False)
    if matrix.ndim != 2 or matrix.shape != (len(meta["ids"]), meta["dim"]):
        raise ValueError(f"{vectors_path.name} has shape {matrix.shape}, its metadata says "
                         f"({len(meta['ids'])}, {meta['dim']}); rebuild with `ikg build --embed`")
    if meta["ids"] != passage_ids:
        raise ValueError(f"{vectors_path.name} rows are not the built passages in order; "
                         "rebuild with `ikg build --embed`")
    return matrix, meta
