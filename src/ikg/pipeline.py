"""Build pipeline and loaded runtime, shared by the CLI and the web app."""

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

import networkx as nx

from ikg import config
from ikg.entities import Gazetteer, Mention, extract, mentions_by_passage, read_mentions, write_mentions
from ikg.graph import build_cooccurrence, load_graph, save_graph, to_rdf
from ikg.retrieve import BM25Index
from ikg.segment import Passage, read_passages, segment_work, write_passages
from ikg.sources import Work, fetch, load_works


class BuildError(Exception):
    pass


def build(works: list[Work] | None = None, log: Callable[[str], None] = print,
          with_passages: bool = False, download: bool = True) -> dict:
    """Fetch missing sources, segment, extract entities, index, and write graph + RDF."""
    t0 = time.time()
    works = works if works is not None else load_works()
    if download:
        written = fetch(works)
        if written:
            log(f"Downloaded {len(written)} file(s)")
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
    write_passages(passages, config.PASSAGES_FILE)

    gazetteer = Gazetteer.load()
    mentions = extract(passages, gazetteer)
    write_mentions(mentions, config.MENTIONS_FILE)
    log(f"{len(mentions):,} entity mentions")

    BM25Index(passages).save(config.INDEX_FILE)
    graph = build_cooccurrence(mentions, gazetteer)
    save_graph(graph, config.GRAPH_FILE)
    rdf = to_rdf(works, passages, mentions, gazetteer, graph, with_passages=with_passages)
    rdf.serialize(config.RDF_FILE, format="turtle")
    stats = {"works": len(works), "passages": len(passages), "mentions": len(mentions),
             "entities": graph.number_of_nodes(), "edges": graph.number_of_edges(),
             "triples": len(rdf), "seconds": round(time.time() - t0, 1)}
    log(f"graph: {stats['entities']} entities, {stats['edges']} edges; RDF: {stats['triples']:,} triples")
    log(f"built in {stats['seconds']}s")
    return stats


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
        return all(p.exists() for p in (config.PASSAGES_FILE, config.INDEX_FILE,
                                        config.MENTIONS_FILE, config.GRAPH_FILE))

    @classmethod
    def load(cls) -> "Runtime":
        if not cls.available():
            raise FileNotFoundError("No built corpus found. Run `ikg build` or use Rebuild in the admin.")
        index = BM25Index.load(config.INDEX_FILE)
        mentions = read_mentions(config.MENTIONS_FILE)
        rt = cls(index=index, gazetteer=Gazetteer.load(), mentions=mentions, graph=load_graph(config.GRAPH_FILE))
        rt.passages = {p.id: p for p in index.passages}
        rt.order = {p.id: i for i, p in enumerate(index.passages)}
        rt.passage_entities = mentions_by_passage(mentions)
        return rt

    def neighbour_passages(self, pid: str) -> tuple[Passage | None, Passage | None]:
        i = self.order[pid]
        ps = self.index.passages
        prev = ps[i - 1] if i > 0 and ps[i - 1].work == ps[i].work else None
        nxt = ps[i + 1] if i + 1 < len(ps) and ps[i + 1].work == ps[i].work else None
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
