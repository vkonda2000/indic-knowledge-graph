"""Source-grounded question answering (GraphRAG-lite) with Claude.

Pipeline: detect entities in the question -> expanded BM25 retrieval with entity boost
-> knowledge-graph neighbourhood summary -> Claude answers from the passages only,
using the API's native citations so every claim maps to a passage id.

With `IKG_ANSWER_MODEL=ollama:<model>` (or `model="ollama:..."`) a local model answers instead, and
the citations are checked in code (ikg.localqa).
"""

from dataclasses import dataclass, field

import anthropic
import networkx as nx

from ikg import config
from ikg.entities import Gazetteer
from ikg.graph import graph_context
from ikg.localqa import answer_locally, is_local
from ikg.retrieve import BM25Index, Hit, search

NOT_IN_SOURCES = "NOT_IN_SOURCES"

SYSTEM_PROMPT = f"""You answer questions about Indian texts (the Upanishads, Mahabharata, Ramayana and Puranas) using ONLY the passages provided as documents.

Rules:
- Ground every factual statement in the provided passages and cite them. Do not use outside knowledge, even if you are confident.
- Each document's context says its kind. "verse" and "narrative" are the translated primary text. "commentary" and "introduction" are the translator's own explanation. "secondary" is modern scholarship (books, papers, lectures). Never present commentary or scholarship as what the text itself says; attribute it ("the translator comments...", "<author> argues...").
- These are 19th and early-20th century English translations. When a passage uses an English word for a Sanskrit concept (e.g. "righteousness" for dharma, "Self" for atman), you may name the Sanskrit term, but do not attribute the translator's wording to the original.
- Represent each speaker, text and school of thought on its own terms. If passages disagree or come from different works, say so and keep the views separate instead of blending them.
- A knowledge-graph summary may follow the documents. It shows co-occurrence statistics only and is not a source: use it for orientation, and never state anything that only the summary supports.
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
    truncated: bool = False
    model: str = ""
    effort: str = ""
    input_tokens: int = 0
    output_tokens: int = 0  # includes thinking tokens, which are billed as output
    cost_usd: float | None = None
    cached: bool = False  # served from the web app's answer cache; no API call was made
    dropped_sentences: int | None = None  # local models only: sentences removed for an unverifiable citation
    model_abstained: bool | None = None   # local models only: the model itself said it can't answer


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated USD cost from first-party list prices; None for a model without a known price."""
    price = config.PRICES.get(model)
    if price is None:
        return None
    if input_tokens > 100_000 and model in config.PRICES_LONG_PROMPT:
        price = config.PRICES_LONG_PROMPT[model]
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def _documents(hits: list[Hit]) -> list[dict]:
    docs = []
    for h in hits:
        p = h.passage
        docs.append({
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": p.text},
            "title": f"[{p.id}] {p.ref}",
            "context": f"passage_id={p.id}; work={p.work}; edition={p.edition}; kind={p.kind}; translator={p.translator}",
            "citations": {"enabled": True},
        })
    return docs


def _question_blocks(question: str, kg_summary: str) -> list[dict]:
    # The summary is plain text, not a document: citations must be on for every document or
    # none, and a sentence drawn from a citable summary would lose its citation and look grounded.
    blocks = []
    if kg_summary:
        blocks.append({"type": "text",
                       "text": f"Knowledge-graph summary (co-occurrence statistics, not a source):\n{kg_summary}"})
    return blocks + [{"type": "text", "text": question}]


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
    model: str | None = None,
    effort: str | None = None,
    retrieval: dict | None = None,
) -> Answer:
    model, effort = model or config.MODEL, effort or config.EFFORT
    if is_local(model):
        effort = "n/a"  # effort is a Claude setting
    elif effort not in config.EFFORTS:
        raise ValueError(f"effort must be one of {', '.join(config.EFFORTS)}, not {effort!r}")
    hits = search(index, gazetteer, question, k=k, works=works, primary_only=primary_only,
                  passage_entities=passage_entities, **(retrieval or {}))
    entities = gazetteer.entities_in(question)
    kg = graph_context(graph, entities, gazetteer) if graph is not None else ""
    answer = Answer(question=question, text="", hits=hits, entities=entities, model=model, effort=effort)
    if not hits:
        answer.text, answer.abstained = f"{NOT_IN_SOURCES}\nNo passages matched the question.", True
        answer.cost_usd = 0.0  # no API call was made
        return answer
    if is_local(model):
        return answer_locally(answer, hits, kg, SYSTEM_PROMPT, NOT_IN_SOURCES)

    client = client or anthropic.Anthropic()
    fallback = ({"betas": ["server-side-fallback-2026-07-01"], "extra_body": {"fallbacks": "default"}}
                if model in config.FALLBACK_MODELS else {})
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": _documents(hits) + _question_blocks(question, kg)}],
        **fallback,
    )
    answer.model = response.model
    usage = getattr(response, "usage", None)
    if usage is not None:
        answer.input_tokens, answer.output_tokens = usage.input_tokens, usage.output_tokens
        answer.cost_usd = estimate_cost(response.model, usage.input_tokens, usage.output_tokens)
    answer.truncated = response.stop_reason == "max_tokens"
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
                continue  # an unexpected shape; only passages are documents
            p = hits[idx].passage
            if p.id not in numbering:
                numbering[p.id] = len(numbering) + 1
                answer.citations.append(Citation(p.id, p.ref, getattr(c, "cited_text", "")))
            markers.append(numbering[p.id])
        if markers:
            pieces.append("".join(f"[{n}]" for n in sorted(set(markers))))
    body = "".join(pieces).strip()
    answer.abstained = body.startswith(NOT_IN_SOURCES)
    if answer.truncated:
        body += "\n\n[The answer was cut off at the length limit and may be incomplete.]"
    refs = "\n".join(f"[{numbering[c.passage_id]}] {c.ref} - {c.passage_id}" for c in answer.citations)
    answer.text = f"{body}\n\nSources:\n{refs}" if refs else body
    return answer
