"""Build pipeline and loaded runtime, shared by the CLI and the web app.

A build is self-describing and all-or-nothing (L19). It snapshots the gazetteer it used, writes
every artefact to a temporary file first and swaps them in only after all of them succeeded, and
writes the manifest last, with each artefact's SHA-256. `Runtime.load` refuses artefacts that don't
match the manifest, so a failure during the swap, a crash or a stray edit can't serve a mix of
two builds. One lock file keeps a CLI build and a web rebuild from running at the same time.

Embeddings (ikg.embed) are optional artefacts: written and checksummed when the build embeds, removed
when it doesn't, so a manifest never lists vectors from a different build.
"""

import hashlib
import json
import os
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import networkx as nx

from ikg import config
from ikg.embed import (Embedder, EmbedError, embed_passages, embed_query, load_embedder, read_embeddings,
                       reusable_embeddings, write_embeddings)
from ikg.entities import Gazetteer, Mention, extract, mentions_by_passage, read_mentions, write_mentions
from ikg.graph import build_cooccurrence, load_graph, save_graph, to_rdf
from ikg.retrieve import BM25Index
from ikg.segment import _SENTENCE_END_RE, ID_SCHEME, MAX_CHARS, Passage, read_passages, segment_work, write_passages
from ikg.sources import Work, fetch, load_works
from ikg.vectorstore import VectorStore, VectorStoreError, open_store

LOCK_STALE_SECONDS = 30 * 60  # a full build takes seconds; an older lock was left by a crash


class BuildError(Exception):
    pass


def artefacts() -> dict[str, Path]:
    """The files one build produces, by name. The manifest records a checksum for each."""
    return {"passages": config.PASSAGES_FILE, "mentions": config.MENTIONS_FILE, "index": config.INDEX_FILE,
            "graph": config.GRAPH_FILE, "rdf": config.RDF_FILE, "gazetteer": config.GAZETTEER_SNAPSHOT_FILE}


def embedding_artefacts() -> dict[str, Path]:
    """The files a build writes only when it embeds passages."""
    return {"embeddings": config.EMBEDDINGS_FILE, "embeddings_meta": config.EMBEDDINGS_META_FILE}


@contextmanager
def build_lock():
    """One build at a time across processes: the CLI and the web app share this lock file."""
    path = config.BUILD_LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue  # released in the meantime
            if attempt == 2 or age < LOCK_STALE_SECONDS:
                raise BuildError(f"another build is running (lock file {path}); "
                                 "if none is, delete the file") from None
            # Rename the stale lock away first: if two builds try at once, only one rename succeeds.
            try:
                os.replace(path, path.with_name(f"{path.name}.stale-{os.getpid()}"))
                path.with_name(f"{path.name}.stale-{os.getpid()}").unlink(missing_ok=True)
            except FileNotFoundError:
                pass
    else:
        raise BuildError(f"could not take the build lock {path}")
    os.write(fd, f"{os.getpid()}\n".encode())
    os.close(fd)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def build(works: list[Work] | None = None, log: Callable[[str], None] = print,
          with_passages: bool = False, download: bool = True, embed: bool | None = None,
          embedder: Embedder | None = None) -> dict:
    """Fetch missing sources, segment, extract entities, index, and write graph + RDF.

    `embed` (default IKG_EMBED) also writes passage embeddings with the IKG_EMBED_MODEL model;
    passing an `embedder` implies it."""
    t0 = time.time()
    try:
        works = works if works is not None else load_works()
    except ValueError as exc:
        raise BuildError(f"source registry: {exc}") from exc
    if download:  # before the lock: downloads can be slow, and the lock is for the processed files
        try:
            written = fetch(works)
        except (ValueError, OSError) as exc:  # checksum mismatch, or a failed download
            raise BuildError(f"fetch: {exc}") from exc
        if written:
            log(f"Downloaded {len(written)} file(s)")
    if embedder is None and (config.EMBED if embed is None else embed):
        try:  # before the lock too: the first use downloads the model
            embedder = load_embedder()
        except EmbedError as exc:
            raise BuildError(f"embeddings: {exc}") from exc
    with build_lock():
        return _build(works, log, with_passages, t0, embedder)


def _build(works, log, with_passages, t0, embedder: Embedder | None = None) -> dict:
    passages: list[Passage] = []
    for w in works:
        try:
            ps = segment_work(w)
        except Exception as exc:  # one bad upload should name itself, not crash silently
            raise BuildError(f"{w.id}: {exc}") from exc
        log(f"{w.id:<20} {len(ps):>6} passages")
        passages.extend(ps)
    dupes = [pid for pid, n in Counter(p.id for p in passages).items() if n > 1]
    if dupes:
        raise BuildError(f"duplicate passage ids: {dupes[:5]}")

    # The gazetteer is read once and snapshotted, so the runtime uses exactly what the build used.
    gazetteer_text = config.gazetteer_file().read_text(encoding="utf-8")
    try:
        gazetteer = Gazetteer.from_yaml(gazetteer_text)
    except ValueError as exc:
        raise BuildError(f"gazetteer: {exc}") from exc
    mentions = extract(passages, gazetteer)
    log(f"{len(mentions):,} entity mentions")
    graph = build_cooccurrence(mentions, gazetteer)
    rdf = to_rdf(works, passages, mentions, gazetteer, graph, with_passages=with_passages)
    vectors = None
    if embedder is not None and not passages:
        log("embeddings: skipped, no passages")
    elif embedder is not None:
        t_embed = time.time()
        vectors = reusable_embeddings(passages, embedder, config.EMBEDDINGS_FILE, config.EMBEDDINGS_META_FILE)
        if vectors is not None:
            log(f"embeddings: reused {vectors.shape[0]:,} x {vectors.shape[1]} from the previous build "
                f"(same {embedder.name}, same passage texts)")
        else:
            try:
                vectors = embed_passages(passages, embedder, log)
            except EmbedError as exc:
                raise BuildError(f"embeddings: {exc}") from exc
            log(f"embeddings: {vectors.shape[0]:,} x {vectors.shape[1]} with {embedder.name} "
                f"in {time.time() - t_embed:.0f}s")

    final = artefacts() | (embedding_artefacts() if vectors is not None else {})
    tmp = {name: path.with_name(path.name + ".tmp") for name, path in final.items()}
    try:
        tmp["passages"].parent.mkdir(parents=True, exist_ok=True)
        write_passages(passages, tmp["passages"])
        write_mentions(mentions, tmp["mentions"])
        BM25Index(passages).save(tmp["index"])
        save_graph(graph, tmp["graph"])
        rdf.serialize(tmp["rdf"], format="turtle")
        tmp["gazetteer"].write_text(gazetteer_text, encoding="utf-8")
        embedding = None
        if vectors is not None:
            embedding = write_embeddings(vectors, passages, embedder, tmp["embeddings"], tmp["embeddings_meta"])
        else:  # vectors from an earlier build must not sit beside these passages. Removed before the
            for path in embedding_artefacts().values():  # swap, so failing here changes nothing
                _retry(lambda: path.unlink(missing_ok=True))
        for name, path in final.items():
            _replace(tmp[name], path)
        write_manifest(works, passages, final, embedding)
    except OSError as exc:
        # Before the swap nothing changed. During it, the old manifest's checksums no longer match,
        # so Runtime refuses the mixed files with a message to rebuild.
        raise BuildError(f"writing the build failed ({exc}); run the build again") from exc
    finally:
        for path in tmp.values():
            path.unlink(missing_ok=True)
    stats = {"works": len(works), "passages": len(passages), "mentions": len(mentions),
             "entities": graph.number_of_nodes(), "edges": graph.number_of_edges(),
             "triples": len(rdf), "embedding_model": embedder.name if vectors is not None else None,
             "seconds": round(time.time() - t0, 1)}
    log(f"graph: {stats['entities']} entities, {stats['edges']} edges; RDF: {stats['triples']:,} triples")
    log(f"built in {stats['seconds']}s")
    return stats


def _replace(src: Path, dst: Path, attempts: int = 10) -> None:
    _retry(lambda: os.replace(src, dst), attempts)


def _retry(op: Callable[[], None], attempts: int = 10) -> None:
    """Run a file operation, retried briefly: on Windows replacing or deleting a file fails while
    another process (a search loading the index, a virus scanner) has it open."""
    for attempt in range(1, attempts + 1):
        try:
            op()
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(0.2 * attempt)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(works: list[Work], passages: list[Passage], files: dict[str, Path] | None = None,
                   embedding: dict | None = None) -> None:
    """Record what the build was made from: the passage ID scheme and the chunking parameters
    it depends on (D11), each edition with its pinned sources, the embedding model if the build
    embedded passages, and a checksum of every artefact (`files`, by default the core ones).
    No timestamp, so identical inputs give an identical file on one machine. (Embeddings can differ
    in the last float bits across CPUs, and with them their checksum.)"""
    counts = Counter(p.edition for p in passages)
    files = artefacts() if files is None else files
    manifest = {
        "id_scheme": ID_SCHEME,
        "chunking": {"max_chars": MAX_CHARS, "sentence_end": _SENTENCE_END_RE.pattern},
        "editions": [{"edition": w.edition_id, "work": w.work_id, "registry_id": w.id,
                      "translator": w.translator, "parser": w.parser, "passages": counts[w.edition_id],
                      "sources": w.files} for w in works],
        "passages": len(passages),
        **({"embedding": embedding} if embedding else {}),
        "artefacts": {name: {"file": path.name, "sha256": _sha256(path)} for name, path in files.items()},
    }
    config.MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.MANIFEST_FILE.with_name(config.MANIFEST_FILE.name + ".tmp")
    try:
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _replace(tmp, config.MANIFEST_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def built_gazetteer() -> Gazetteer:
    """The gazetteer the current build was made with (not the one being edited)."""
    return Gazetteer.from_yaml(config.GAZETTEER_SNAPSHOT_FILE.read_text(encoding="utf-8"))


@dataclass
class Runtime:
    """Everything the search/QA/graph views need, loaded once from data/processed."""
    index: BM25Index
    gazetteer: Gazetteer
    mentions: list[Mention]
    graph: nx.Graph
    passages: dict[str, Passage] = field(default_factory=dict)
    passage_entities: dict[str, set[str]] = field(default_factory=dict)
    order: dict[str, int] = field(default_factory=dict)
    embeddings: object | None = None  # numpy (n, dim) matrix in passage order, if the build embedded
    embedding: dict | None = None     # its model, library and dimension, from the manifest
    embeddings_sha256: str | None = None  # of embeddings.npy, from the manifest: what a vector store mirrors
    _vector_store: VectorStore | None = field(default=None, repr=False)
    _query_embedder: Embedder | None = field(default=None, repr=False)
    _dense_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    dense_error: str = ""  # why dense search is unavailable, if it was tried and failed

    @classmethod
    def available(cls) -> bool:
        return all(p.exists() for p in (config.PASSAGES_FILE, config.INDEX_FILE, config.MENTIONS_FILE,
                                        config.GRAPH_FILE, config.GAZETTEER_SNAPSHOT_FILE, config.MANIFEST_FILE))

    @classmethod
    def verify(cls) -> None:
        """Raise unless every artefact the manifest lists is present and unchanged."""
        if not cls.available():
            raise FileNotFoundError("No complete built corpus found. Run `ikg build` or use Rebuild in the admin.")
        listed = json.loads(config.MANIFEST_FILE.read_text(encoding="utf-8")).get("artefacts")
        if not listed:
            raise FileNotFoundError("The corpus was built by an older version that recorded no checksums. "
                                    "Rebuild it with `ikg build` or Rebuild in the admin.")
        paths = artefacts() | embedding_artefacts()
        for name, info in listed.items():
            path = paths.get(name)
            if path is None or not path.exists() or _sha256(path) != info["sha256"]:
                raise ValueError(f"{info['file']} does not match the build manifest: a build was interrupted "
                                 "or a file was changed. Rebuild with `ikg build` or Rebuild in the admin.")

    @classmethod
    def load(cls) -> "Runtime":
        cls.verify()
        index = BM25Index.load(config.INDEX_FILE)
        mentions = read_mentions(config.MENTIONS_FILE)
        rt = cls(index=index, gazetteer=built_gazetteer(), mentions=mentions, graph=load_graph(config.GRAPH_FILE))
        rt.passages = {p.id: p for p in index.passages}
        rt.order = {p.id: i for i, p in enumerate(index.passages)}
        rt.passage_entities = mentions_by_passage(mentions)
        manifest = json.loads(config.MANIFEST_FILE.read_text(encoding="utf-8"))
        if "embeddings" in manifest["artefacts"]:
            try:
                rt.embeddings, _ = read_embeddings(config.EMBEDDINGS_FILE, config.EMBEDDINGS_META_FILE,
                                                   [p.id for p in index.passages])
            except EmbedError as exc:  # numpy missing in this install
                raise ValueError(f"the build has embeddings but they cannot be read: {exc}") from exc
            rt.embedding = manifest.get("embedding")
            # The store must mirror the vectors and their row ids: an id change can leave the matrix
            # bytes identical, so both checksums identify what it holds.
            rt.embeddings_sha256 = (manifest["artefacts"]["embeddings"]["sha256"] + ":"
                                    + manifest["artefacts"]["embeddings_meta"]["sha256"])
        return rt

    def prepare_dense(self) -> bool:
        """Load the query model and open the vector store, once, under a lock. The web app calls this
        when it loads a build, so the first visitor doesn't wait for it. On failure `dense_error` says
        why and the default retrieval falls back to BM25. Returns whether dense search is ready."""
        if self.embeddings is None:
            self.dense_error = "this build has no embeddings: rebuild with `ikg build --embed`"
            return False
        with self._dense_lock:
            if self._query_embedder is not None:
                return True
            try:
                self.vector_store(log=lambda _: None)
                self._query_embedder = load_embedder(self.embedding["model"])
                self.dense_error = ""
                return True
            except (EmbedError, VectorStoreError) as exc:
                self.dense_error = str(exc)
                return False

    def dense(self, question: str, n: int, works=None, kinds=None) -> list[tuple[str, float]]:
        """Nearest passages to the question, embedded with the model the build used (retrieve.DenseSearch).
        Raises EmbedError or VectorStoreError if that can't be done."""
        if not self.prepare_dense():
            raise VectorStoreError(self.dense_error)
        vector = embed_query(question, self._query_embedder)
        return [(h.passage_id, h.score) for h in self.vector_store().search(vector, n, works, kinds)]

    def retrieval(self, mode: str | None = None) -> dict:
        """Keyword arguments for retrieve.search: `mode` (default IKG_RETRIEVER) and its dense search.
        The default falls back to BM25 when the build has no embeddings or dense search failed to start
        (`dense_error`); an explicit dense or hybrid mode is never changed."""
        requested = mode or config.RETRIEVER
        if requested == "bm25" or (mode is None and (self.embeddings is None or self.dense_error)):
            return {"mode": "bm25"}
        return {"mode": requested, "dense": self.dense if self.embeddings is not None else None}

    def close(self) -> None:
        """Release what the runtime holds outside the process (a Postgres connection)."""
        close = getattr(self._vector_store, "close", None)
        if close:
            close()

    def vector_store(self, spec: str | None = None, log: Callable[[str], None] = print) -> VectorStore:
        """The store for nearest-passage search (IKG_VECTOR_STORE unless `spec`), opened on first use.
        A Postgres store is loaded with this build's vectors if it holds another build's."""
        spec = spec or config.VECTOR_STORE
        if spec == config.VECTOR_STORE and self._vector_store is not None:
            return self._vector_store
        if self.embeddings is None:
            raise VectorStoreError("this build has no embeddings: rebuild with `ikg build --embed`")
        store = open_store(spec, self.embeddings, self.index.passages, self.embeddings_sha256,
                           self.embedding["model"], log)
        if spec == config.VECTOR_STORE:
            self._vector_store = store
        return store

    def neighbour_passages(self, pid: str) -> tuple[Passage | None, Passage | None]:
        i = self.order[pid]
        ps = self.index.passages
        prev = ps[i - 1] if i > 0 and ps[i - 1].edition == ps[i].edition and ps[i - 1].work == ps[i].work else None
        nxt = (ps[i + 1] if i + 1 < len(ps) and ps[i + 1].edition == ps[i].edition and ps[i + 1].work == ps[i].work
               else None)
        return prev, nxt

    def passages_for_entity(self, entity_id: str, limit: int = 50) -> list[Passage]:
        seen, out = set(), []
        for m in self.mentions:
            if m.entity == entity_id and m.passage not in seen:
                seen.add(m.passage)
                out.append(self.passages[m.passage])
                if len(out) == limit:
                    break
        return out


def passages_from_disk() -> list[Passage]:
    return read_passages(config.PASSAGES_FILE)
