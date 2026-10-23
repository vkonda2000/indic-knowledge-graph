"""Knowledge graph construction: a weighted entity co-occurrence graph (networkx) and an
RDF export that follows ontology/ikg.ttl (queryable with SPARQL)."""

from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import networkx as nx
from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef
from rdflib.namespace import XSD

from ikg.entities import Gazetteer, Mention
from ikg.segment import Passage
from ikg.sources import Work

IKG = Namespace("https://example.org/ikg#")
DATA = Namespace("https://example.org/ikg/data/")


def build_cooccurrence(mentions: list[Mention], gazetteer: Gazetteer) -> nx.Graph:
    """Nodes are entities; an edge means both were mentioned in the same passage.

    Node attrs: type, label, mentions, works (per-work counts as "work:count;...").
    Edge attrs: weight (passages shared), plus one weight_<work> attribute per work.
    """
    per_passage: dict[str, set[str]] = defaultdict(set)
    passage_work: dict[str, str] = {}
    node_counts: Counter = Counter()
    node_work_counts: dict[str, Counter] = defaultdict(Counter)
    for m in mentions:
        per_passage[m.passage].add(m.entity)
        passage_work[m.passage] = m.work
        node_counts[m.entity] += 1
        node_work_counts[m.entity][m.work] += 1

    g = nx.Graph()
    for ent, count in node_counts.items():
        e = gazetteer.entities[ent]
        g.add_node(ent, type=e.type, label=e.label, mentions=count,
                   works=";".join(f"{w}:{c}" for w, c in sorted(node_work_counts[ent].items())))
    for pid, ents in per_passage.items():
        work = passage_work[pid]
        for a, b in combinations(sorted(ents), 2):
            if g.has_edge(a, b):
                g[a][b]["weight"] += 1
            else:
                g.add_edge(a, b, weight=1)
            g[a][b][f"weight_{work}"] = g[a][b].get(f"weight_{work}", 0) + 1
    return g


def neighbours(g: nx.Graph, entity: str, n: int = 8, work: str | None = None) -> list[tuple[str, int]]:
    if entity not in g:
        return []
    key = f"weight_{work}" if work else "weight"
    ranked = [(nb, data.get(key, 0)) for nb, data in g[entity].items()]
    return sorted((x for x in ranked if x[1] > 0), key=lambda x: -x[1])[:n]


def graph_context(g: nx.Graph, entities: list[str], gazetteer: Gazetteer, n: int = 8) -> str:
    """Compact text summary of the graph around the question's entities, for the QA prompt."""
    lines = []
    for ent in entities:
        if ent not in g:
            continue
        node = g.nodes[ent]
        nbs = ", ".join(f"{gazetteer.entities[nb].label} ({w})" for nb, w in neighbours(g, ent, n))
        lines.append(f"{node['label']} [{node['type']}]: mentioned in {node['works'].replace(';', ', ')}. "
                     f"Most frequently co-mentioned with: {nbs}.")
    return "\n".join(lines)


def to_rdf(works: list[Work], passages: list[Passage], mentions: list[Mention],
           gazetteer: Gazetteer, g: nx.Graph, with_passages: bool = False) -> Graph:
    rdf = Graph()
    rdf.bind("ikg", IKG)
    rdf.bind("d", DATA)

    for w in works:
        wu, eu = DATA[f"work/{w.id}"], DATA[f"edition/{w.id}"]
        rdf.add((wu, RDF.type, IKG.Work))
        rdf.add((wu, RDFS.label, Literal(w.title)))
        rdf.add((eu, RDF.type, IKG.Edition))
        rdf.add((eu, IKG.editionOf, wu))
        rdf.add((eu, IKG.translator, Literal(w.translator)))
        rdf.add((eu, IKG.year, Literal(w.year)))
        for url in w.source_urls():
            rdf.add((eu, IKG.sourceUrl, Literal(url, datatype=XSD.anyURI)))

    books = {}
    for p in passages:
        key = (p.work, p.book)
        if key not in books:
            bu = DATA[f"book/{p.work}/{p.book}"]
            books[key] = bu
            rdf.add((bu, RDF.type, IKG.Book))
            rdf.add((bu, RDFS.label, Literal(p.book_label)))
            rdf.add((bu, IKG.partOf, DATA[f"work/{p.work}"]))
            rdf.add((bu, IKG.fromEdition, DATA[f"edition/{p.work}"]))
        if with_passages:
            pu = DATA[f"passage/{p.id}"]
            rdf.add((pu, RDF.type, IKG[p.kind.title()]))
            rdf.add((pu, IKG.partOf, books[key]))
            rdf.add((pu, RDFS.label, Literal(p.ref)))

    for e in gazetteer.entities.values():
        eu = DATA[f"entity/{e.id}"]
        rdf.add((eu, RDF.type, IKG[e.type]))
        rdf.add((eu, RDFS.label, Literal(e.label)))
        if e.iast:
            rdf.add((eu, IKG.iast, Literal(e.iast, lang="sa-Latn")))
        for a in e.aliases + e.aliases_cs:
            rdf.add((eu, IKG.alias, Literal(a)))
        for r in e.renderings:
            ru = DATA[f"rendering/{e.id}/{r.replace(' ', '_')}"]
            rdf.add((ru, RDF.type, IKG.Rendering))
            rdf.add((ru, IKG.renders, eu))
            rdf.add((ru, IKG.surface, Literal(r)))

    # Aggregated mentions per (entity, book, via)
    passage_book = {p.id: (p.work, p.book) for p in passages}
    counts = Counter((m.entity, passage_book[m.passage], m.via) for m in mentions if m.passage in passage_book)
    for (ent, (work, book), via), count in counts.items():
        mu = DATA[f"mention/{ent}/{work}/{book}/{via}"]
        rdf.add((mu, RDF.type, IKG.Mention))
        rdf.add((mu, IKG.entity, DATA[f"entity/{ent}"]))
        rdf.add((mu, IKG.inUnit, books[(work, book)]))
        rdf.add((mu, IKG["count"], Literal(count, datatype=XSD.integer)))  # IKG.count is str.count
        rdf.add((mu, IKG.via, Literal(via)))

    for a, b in g.edges():
        rdf.add((DATA[f"entity/{a}"], IKG.coOccursWith, DATA[f"entity/{b}"]))
    return rdf


def save_graph(g: nx.Graph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(g, path)


def load_graph(path: Path) -> nx.Graph:
    return nx.read_graphml(path)


def rdf_uri(entity_id: str) -> URIRef:
    return DATA[f"entity/{entity_id}"]
