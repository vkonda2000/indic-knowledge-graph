"""Source-grounded question answering (GraphRAG-lite) with Claude.

Pipeline: detect entities in the question -> expanded BM25 retrieval with entity boost
-> knowledge-graph neighbourhood summary -> Claude answers from the passages only,
using the API's native citations so every claim maps to a passage id.
"""

from dataclasses import dataclass, field

import anthropic
import networkx as nx

from ikg import config
from ikg.entities import Gazetteer
from ikg.graph import graph_context
from ikg.retrieve import BM25Index, Hit, search

NOT_IN_SOURCES = "NOT_IN_SOURCES"

SYSTEM_PROMPT = f"""You answer questions about Indian texts (the Upanishads, Mahabharata, Ramayana and Puranas) using ONLY the passages provided as documents.

Rules:
- Ground every factual statement in the provided passages and cite them. Do not use outside knowledge, even if you are confident.
- Each document's context says its kind. "verse" and "narrative" are the translated primary text. "commentary" and "introduction" are the translator's own explanation. "secondary" is modern scholarship (books, papers, lectures). Never present commentary or scholarship as what the text itself says; attribute it ("the translator comments...", "<author> argues...").
- These are 19th and early-20th century English translations. When a passage uses an English word for a Sanskrit concept (e.g. "righteousness" for dharma, "Self" for atman), you may name the Sanskrit term, but do not attribute the translator's wording to the original.
- Represent each speaker, text and school of thought on its own terms. If passages disagree or come from different works, say so and keep the views separate instead of blending them.
- The knowledge-graph summary shows co-occurrence statistics only. Use it for orientation; never cite it as evidence of what a text says.
- If the passages do not contain enough to answer, reply with exactly {NOT_IN_SOURCES} on the first line, then one sentence on what is missing.
- Be concise. Prefer quoting short phrases from the passages over paraphrasing at length."""


@dataclass
class Citation:
    passage_id: str
    ref: str
    cited_text: str


@dataclass
class Answer:
    question: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    abstained: bool = False
    refused: bool = False
    model: str = ""


def _documents(hits: list[Hit], kg_summary: str) -> list[dict]:
    docs = []
    for h in hits:
        p = h.passage
        docs.append({
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": p.text},
            "title": f"[{p.id}] {p.ref}",
            "context": f"passage_id={p.id}; work={p.work}; kind={p.kind}; translator={p.translator}",
            "citations": {"enabled": True},
        })
    if kg_summary:
        docs.append({
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": kg_summary},
            "title": "Knowledge-graph summary (co-occurrence statistics, not a source)",
            "context": "kind=graph",
            "citations": {"enabled": True},
        })
    return docs


def ask(
    question: str,
    index: BM25Index,
    gazetteer: Gazetteer,
    graph: nx.Graph | None = None,
    passage_entities: dict[str, set[str]] | None = None,
    k: int = 8,
    works: list[str] | None = None,
    primary_only: bool = False,
    client: anthropic.Anthropic | None = None,
    model: str = config.MODEL,
) -> Answer:
    hits = search(index, gazetteer, question, k=k, works=works, primary_only=primary_only,
                  passage_entities=passage_entities)
    entities = gazetteer.entities_in(question)
    kg = graph_context(graph, entities, gazetteer) if graph is not None else ""
    answer = Answer(question=question, text="", hits=hits, entities=entities, model=model)
    if not hits:
        answer.text, answer.abstained = f"{NOT_IN_SOURCES}\nNo passages matched the question.", True
        return answer

    client = client or anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        messages=[{
            "role": "user",
            "content": _documents(hits, kg) + [{"type": "text", "text": question}],
        }],
    )
    answer.model = response.model
    if response.stop_reason == "refusal":
        answer.refused = True
        return answer

    pieces, numbering = [], {}
    for block in response.content:
        if block.type != "text":
            continue
        pieces.append(block.text)
        markers = []
        for c in block.citations or []:
            idx = getattr(c, "document_index", None)
            if idx is None or idx >= len(hits):
                continue  # citation of the graph summary, or an unexpected shape
            p = hits[idx].passage
            if p.id not in numbering:
                numbering[p.id] = len(numbering) + 1
                answer.citations.append(Citation(p.id, p.ref, getattr(c, "cited_text", "")))
            markers.append(numbering[p.id])
        if markers:
            pieces.append("".join(f"[{n}]" for n in sorted(set(markers))))
    body = "".join(pieces).strip()
    answer.abstained = body.startswith(NOT_IN_SOURCES)
    refs = "\n".join(f"[{numbering[c.passage_id]}] {c.ref} - {c.passage_id}" for c in answer.citations)
    answer.text = f"{body}\n\nSources:\n{refs}" if refs else body
    return answer
