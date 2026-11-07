"""Retrieval: BM25 with knowledge-graph query expansion, dense vectors, or both fused.

A question about "dharma" also retrieves passages where the translator wrote
"righteousness" or "duty", and a question naming "Partha" retrieves passages that
say "Arjuna". Expansion terms come from the gazetteer, so they are inspectable.
Passages that mention the question's entities get a boost.

Modes (docs/08 A3):
- "bm25": the above.
- "dense": nearest passages by embedding (`dense`, a function the runtime provides), nothing else.
- "hybrid": reciprocal rank fusion (RRF_K = 60) of the BM25 top CANDIDATES (with expansion, before
  the entity boost) and the dense top CANDIDATES, then the entity boost on the fused score.
Work and primary-text filters apply in every mode before ranking, so k results come back.
"""

import math
import pickle
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ikg.entities import Gazetteer
from ikg.normalize import tokenize
from ikg.segment import PRIMARY_KINDS, Passage

EXPANSION_WEIGHT = 0.4
ENTITY_BOOST = 0.15  # fraction of the top score added per matched question entity
MODES = ("bm25", "dense", "hybrid")
CANDIDATES = 50  # per list, before fusion
RRF_K = 60

# (question, n, works or None, kinds or None) -> [(passage_id, similarity)], best first
DenseSearch = Callable[[str, int, Sequence[str] | None, Sequence[str] | None], list[tuple[str, float]]]


@dataclass
class Hit:
    passage: Passage
    score: float


class BM25Index:
    def __init__(self, passages: list[Passage], k1: float = 1.5, b: float = 0.75):
        self.passages = passages
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []
        for i, p in enumerate(passages):
            tokens = tokenize(p.text)
            self.lengths.append(len(tokens))
            for term, tf in Counter(tokens).items():
                self.postings[term].append((i, tf))
        self.avg_len = sum(self.lengths) / max(len(self.lengths), 1)
        n = len(passages)
        self.idf = {t: math.log(1 + (n - len(ps) + 0.5) / (len(ps) + 0.5)) for t, ps in self.postings.items()}

    def score(self, weighted_terms: dict[str, float]) -> dict[int, float]:
        scores: dict[int, float] = defaultdict(float)
        for term, weight in weighted_terms.items():
            idf = self.idf.get(term)
            if idf is None:
                continue
            for doc, tf in self.postings[term]:
                norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lengths[doc] / self.avg_len))
                scores[doc] += weight * idf * norm
        return scores

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path: Path) -> "BM25Index":
        with path.open("rb") as f:
            return pickle.load(f)


def query_terms(question: str, gazetteer: Gazetteer, expand: bool = True) -> tuple[dict[str, float], list[str]]:
    """Weighted query terms plus the entity ids detected in the question."""
    terms: dict[str, float] = {t: 1.0 for t in tokenize(question)}
    entities = gazetteer.entities_in(question)
    if expand:
        for ent in entities:
            for surface in gazetteer.expansion_terms(ent):
                for t in tokenize(surface):
                    terms.setdefault(t, EXPANSION_WEIGHT)
    return terms, entities


def _rows(index: BM25Index) -> dict[str, int]:
    """Passage id -> row, cached on the index (rebuilt with each new index)."""
    rows = index.__dict__.get("_rows")
    if rows is None:
        rows = index.__dict__["_rows"] = {p.id: i for i, p in enumerate(index.passages)}
    return rows


def search(
    index: BM25Index,
    gazetteer: Gazetteer,
    question: str,
    k: int = 8,
    works: list[str] | None = None,
    primary_only: bool = False,
    passage_entities: dict[str, set[str]] | None = None,
    expand: bool = True,
    mode: str = "bm25",
    dense: DenseSearch | None = None,
) -> list[Hit]:
    if mode not in MODES:
        raise ValueError(f"retrieval mode must be one of {', '.join(MODES)}, not {mode!r}")
    works = works or None  # an empty list means no filter, as for BM25
    if mode != "bm25":
        if dense is None:
            raise ValueError(f"{mode} retrieval needs embeddings: rebuild with `ikg build --embed`")
        return _search_with_vectors(index, gazetteer, question, k, works, primary_only, passage_entities,
                                    expand, mode, dense)
    terms, entities = query_terms(question, gazetteer, expand)
    scores = index.score(terms)
    if not scores:
        return []
    top = max(scores.values())
    if passage_entities and entities:
        for doc in list(scores):
            matched = passage_entities.get(index.passages[doc].id, set()) & set(entities)
            scores[doc] += ENTITY_BOOST * top * len(matched)
    hits = []
    for doc, score in sorted(scores.items(), key=lambda x: -x[1]):
        p = index.passages[doc]
        if works and p.work not in works:
            continue
        if primary_only and p.kind not in PRIMARY_KINDS:
            continue
        hits.append(Hit(p, score))
        if len(hits) == k:
            break
    return hits


def _search_with_vectors(index, gazetteer, question, k, works, primary_only, passage_entities, expand,
                         mode, dense) -> list[Hit]:
    kinds = PRIMARY_KINDS if primary_only else None
    rows = _rows(index)
    nearest = [(pid, s) for pid, s in dense(question, k if mode == "dense" else CANDIDATES, works, kinds)
               if pid in rows]  # a vector store loaded from another build may hold ids this one lacks
    if mode == "dense":
        return [Hit(index.passages[rows[pid]], score) for pid, score in nearest[:k]]

    terms, entities = query_terms(question, gazetteer, expand)
    lexical = [(doc, s) for doc, s in index.score(terms).items()
               if (not works or index.passages[doc].work in works)
               and (not primary_only or index.passages[doc].kind in PRIMARY_KINDS)]
    lexical.sort(key=lambda x: (-x[1], x[0]))
    fused: dict[int, float] = defaultdict(float)
    for rank, (doc, _) in enumerate(lexical[:CANDIDATES], 1):
        fused[doc] += 1 / (RRF_K + rank)
    for rank, (pid, _) in enumerate(nearest, 1):
        fused[rows[pid]] += 1 / (RRF_K + rank)
    if not fused:
        return []
    top = max(fused.values())
    if passage_entities and entities:
        for doc in fused:
            matched = passage_entities.get(index.passages[doc].id, set()) & set(entities)
            fused[doc] += ENTITY_BOOST * top * len(matched)
    ranked = sorted(fused.items(), key=lambda x: (-x[1], x[0]))[:k]
    return [Hit(index.passages[doc], score) for doc, score in ranked]
