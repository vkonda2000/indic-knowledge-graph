"""Read-only views of the built corpus, shared by the web app and the MCP server."""

from ikg.graph import neighbours
from ikg.pipeline import Runtime


def snippet(text: str, n: int = 320) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= n else flat[:n].rsplit(" ", 1)[0] + "..."


def entity_info(rt: Runtime, entity_id: str, passages: int) -> dict:
    e = rt.gazetteer.entities[entity_id]
    node = rt.graph.nodes[entity_id] if entity_id in rt.graph else {}
    works = {}
    for part in str(node.get("works", "")).split(";"):
        if part:
            w, _, c = part.partition(":")
            works[w] = int(c)
    renders = {}
    for m in rt.mentions:
        if m.entity == entity_id:
            renders[(m.surface.lower(), m.via)] = renders.get((m.surface.lower(), m.via), 0) + 1
    return {
        "id": e.id, "label": e.label, "type": e.type, "iast": e.iast,
        "aliases": e.aliases + e.aliases_cs, "renderings": e.renderings,
        "mentions": int(node.get("mentions", 0)), "works": works,
        "surface_forms": [{"surface": s, "via": v, "count": c}
                          for (s, v), c in sorted(renders.items(), key=lambda x: -x[1])[:15]],
        "neighbours": [{"id": nb, "label": rt.gazetteer.entities[nb].label, "weight": int(w)}
                       for nb, w in neighbours(rt.graph, entity_id, 12)],
        "passages": [{"id": p.id, "ref": p.ref, "kind": p.kind, "snippet": snippet(p.text, 200)}
                     for p in rt.passages_for_entity(entity_id, passages)],
    }
