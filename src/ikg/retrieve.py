"""BM25 retrieval with knowledge-graph query expansion.

A question about "dharma" also retrieves passages where the translator wrote
"righteousness" or "duty", and a question naming "Partha" retrieves passages that
say "Arjuna". Expansion terms come from the gazetteer, so they are inspectable.
Passages that mention the question's entities get a boost.
"""

import math
import pickle
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ikg.entities import Gazetteer
from ikg.normalize import tokenize
from ikg.segment import PRIMARY_KINDS, Passage

EXPANSION_WEIGHT = 0.4
ENTITY_BOOST = 0.15  # fraction of the top score added per matched question entity


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


def search(
    index: BM25Index,
    gazetteer: Gazetteer,
    question: str,
    k: int = 8,
    works: list[str] | None = None,
    primary_only: bool = False,
    passage_entities: dict[str, set[str]] | None = None,
    expand: bool = True,
) -> list[Hit]:
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
