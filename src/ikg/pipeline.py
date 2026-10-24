"""Build pipeline and loaded runtime, shared by the CLI and the web app.

A build is self-describing and all-or-nothing (L19). It snapshots the gazetteer it used, writes
every artefact to a temporary file first and swaps them in only after all of them succeeded, and
writes the manifest last, with each artefact's SHA-256. `Runtime.load` refuses artefacts that don't
match the manifest, so a failure during the swap, a crash or a stray edit can't serve a mix of
two builds. One lock file keeps a CLI build and a web rebuild from running at the same time.
"""

import hashlib
import json
import os
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import networkx as nx

from ikg import config
from ikg.entities import Gazetteer, Mention, extract, mentions_by_passage, read_mentions, write_mentions
from ikg.graph import build_cooccurrence, load_graph, save_graph, to_rdf
from ikg.retrieve import BM25Index
from ikg.segment import _SENTENCE_END_RE, ID_SCHEME, MAX_CHARS, Passage, read_passages, segment_work, write_passages
from ikg.sources import Work, fetch, load_works

LOCK_STALE_SECONDS = 30 * 60  # a full build takes seconds; an older lock was left by a crash


class BuildError(Exception):
    pass


def artefacts() -> dict[str, Path]:
    """The files one build produces, by name. The manifest records a checksum for each."""
    return {"passages": config.PASSAGES_FILE, "mentions": config.MENTIONS_FILE, "index": config.INDEX_FILE,
            "graph": config.GRAPH_FILE, "rdf": config.RDF_FILE, "gazetteer": config.GAZETTEER_SNAPSHOT_FILE}


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
          with_passages: bool = False, download: bool = True) -> dict:
    """Fetch missing sources, segment, extract entities, index, and write graph + RDF."""
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
    with build_lock():
        return _build(works, log, with_passages, t0)


def _build(works, log, with_passages, t0) -> dict:
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

    final = artefacts()
    tmp = {name: path.with_name(path.name + ".tmp") for name, path in final.items()}
    try:
        tmp["passages"].parent.mkdir(parents=True, exist_ok=True)
        write_passages(passages, tmp["passages"])
        write_mentions(mentions, tmp["mentions"])
        BM25Index(passages).save(tmp["index"])
        save_graph(graph, tmp["graph"])
        rdf.serialize(tmp["rdf"], format="turtle")
        tmp["gazetteer"].write_text(gazetteer_text, encoding="utf-8")
        for name, path in final.items():
            _replace(tmp[name], path)
        write_manifest(works, passages)
    except OSError as exc:
        # Before the swap nothing changed. During it, the old manifest's checksums no longer match,
        # so Runtime refuses the mixed files with a message to rebuild.
        raise BuildError(f"writing the build failed ({exc}); run the build again") from exc
    finally:
        for path in tmp.values():
            path.unlink(missing_ok=True)
    stats = {"works": len(works), "passages": len(passages), "mentions": len(mentions),
             "entities": graph.number_of_nodes(), "edges": graph.number_of_edges(),
             "triples": len(rdf), "seconds": round(time.time() - t0, 1)}
    log(f"graph: {stats['entities']} entities, {stats['edges']} edges; RDF: {stats['triples']:,} triples")
    log(f"built in {stats['seconds']}s")
    return stats


def _replace(src: Path, dst: Path, attempts: int = 10) -> None:
    """os.replace, retried briefly: on Windows it fails while another process (a search loading the
    index, a virus scanner) has the target open."""
    for attempt in range(1, attempts + 1):
        try:
            os.replace(src, dst)
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


def write_manifest(works: list[Work], passages: list[Passage], files: dict[str, Path] | None = None) -> None:
    """Record what the build was made from: the passage ID scheme and the chunking parameters
    it depends on (D11), each edition with its pinned sources, and a checksum of every artefact
    (`files`, by default all of them). No timestamp, so identical inputs give an identical file."""
    counts = Counter(p.edition for p in passages)
    files = artefacts() if files is None else files
    manifest = {
        "id_scheme": ID_SCHEME,
        "chunking": {"max_chars": MAX_CHARS, "sentence_end": _SENTENCE_END_RE.pattern},
        "editions": [{"edition": w.edition_id, "work": w.work_id, "registry_id": w.id,
                      "translator": w.translator, "parser": w.parser, "passages": counts[w.edition_id],
                      "sources": w.files} for w in works],
        "passages": len(passages),
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
        paths = artefacts()
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
        return rt

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
