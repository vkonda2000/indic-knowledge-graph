"""Corpus analyses written as Markdown reports.

1. Concept density per work, split into direct Sanskrit terms vs. translator renderings.
2. How concept density moves across the 18 books of the Mahabharata.
3. Central characters per work (weighted degree and betweenness in the co-occurrence graph).
4. Figures who appear across several works.
"""

from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

from ikg.entities import Gazetteer, Mention
from ikg.normalize import tokenize
from ikg.segment import WORK_LABELS, Passage


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def word_counts(passages: list[Passage]) -> tuple[Counter, Counter]:
    """Words per work and per (work, book), counting primary text only."""
    per_work, per_book = Counter(), Counter()
    for p in passages:
        n = len(tokenize(p.text, drop_stopwords=False))
        per_work[p.work] += n
        per_book[(p.work, p.book)] += n
    return per_work, per_book


def concept_density(passages, mentions, gazetteer) -> str:
    per_work, _ = word_counts(passages)
    concepts = [e for e in gazetteer.entities.values() if e.type in ("Concept", "SchoolOfThought")]
    counts = Counter((m.entity, m.work, m.via) for m in mentions)
    works = sorted(per_work)
    rows = []
    for c in concepts:
        row = [f"{c.label} ({c.iast})"]
        for w in works:
            direct, rendered = counts[(c.id, w, "alias")], counts[(c.id, w, "rendering")]
            per10k = (direct + rendered) * 10_000 / per_work[w]
            row.append(f"{per10k:.1f} ({direct}/{rendered})")
        rows.append(row)
    rows.sort(key=lambda r: -sum(float(x.split()[0]) for x in r[1:]))
    return _table(["Concept"] + [WORK_LABELS.get(w, w) for w in works], rows)


def mahabharata_trajectory(passages, mentions, gazetteer, concepts=("dharma", "moksha", "yajna", "tapas", "yoga")) -> str:
    _, per_book = word_counts(passages)
    book_of = {p.id: p.book for p in passages if p.work == "mahabharata"}
    labels = {p.book: p.book_label for p in passages if p.work == "mahabharata"}
    counts = Counter((m.entity, book_of[m.passage]) for m in mentions if m.passage in book_of)
    rows = []
    for book in sorted(labels):
        words = per_book[("mahabharata", book)]
        rows.append([labels[book], f"{words:,}"] +
                    [f"{counts[(c, book)] * 10_000 / words:.1f}" for c in concepts])
    return _table(["Book", "Words"] + [gazetteer.entities[c].label for c in concepts], rows)


def central_figures(mentions, gazetteer, top: int = 10) -> str:
    sections = []
    for work in sorted({m.work for m in mentions}):
        per_passage = defaultdict(set)
        for m in mentions:
            if m.work == work and gazetteer.entities[m.entity].type in ("Person", "Deity"):
                per_passage[m.passage].add(m.entity)
        g = nx.Graph()
        for ents in per_passage.values():
            ents = sorted(ents)
            for i, a in enumerate(ents):
                for b in ents[i + 1:]:
                    w = g[a][b]["weight"] + 1 if g.has_edge(a, b) else 1
                    g.add_edge(a, b, weight=w)
        if g.number_of_nodes() < 2:
            continue
        strength = dict(g.degree(weight="weight"))
        rows = [[gazetteer.entities[n].label, strength[n], g.degree(n)]
                for n in sorted(strength, key=lambda n: -strength[n])[:top]]
        pairs = sorted(g.edges(data="weight"), key=lambda e: -e[2])[:top]
        pair_rows = [[f"{gazetteer.entities[a].label} - {gazetteer.entities[b].label}", w] for a, b, w in pairs]
        sections.append(f"### {WORK_LABELS.get(work, work)}\n\n"
                        + _table(["Figure", "Co-occurrence strength", "Distinct co-mentioned figures"], rows)
                        + "\n\nStrongest pairs:\n\n" + _table(["Pair", "Shared passages"], pair_rows))
    return "\n\n".join(sections)


def cross_work_figures(mentions, gazetteer, min_works: int = 3) -> str:
    per_entity = defaultdict(Counter)
    for m in mentions:
        if gazetteer.entities[m.entity].type in ("Person", "Deity", "Place"):
            per_entity[m.entity][m.work] += 1
    works = sorted({m.work for m in mentions})
    rows = []
    for ent, counts in per_entity.items():
        if len(counts) >= min_works:
            rows.append([gazetteer.entities[ent].label] + [counts.get(w, 0) for w in works])
    rows.sort(key=lambda r: -sum(r[1:]))
    return _table(["Figure"] + [WORK_LABELS.get(w, w) for w in works], rows)


def write_report(passages: list[Passage], mentions: list[Mention], gazetteer: Gazetteer, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    per_work, _ = word_counts(passages)
    kinds = Counter((p.work, p.kind) for p in passages)
    corpus_rows = [[WORK_LABELS.get(w, w), next(p.translator for p in passages if p.work == w),
                    f"{per_work[w]:,}", sum(v for (pw, _), v in kinds.items() if pw == w),
                    ", ".join(f"{k}: {v}" for (pw, k), v in sorted(kinds.items()) if pw == w)]
                   for w in sorted(per_work)]
    report = f"""# Corpus analysis

Generated by `ikg analyze`. Counts come from the gazetteer in `src/ikg/data/gazetteer.yaml`,
so every number can be traced to specific surface forms in specific passages.

## Corpus

{_table(["Work", "Translator", "Words", "Passages", "Passage kinds"], corpus_rows)}

## 1. Concept density per 10,000 words

Each cell is `density (direct Sanskrit term / translator rendering)`. A high rendering
count means the translator mostly used an English word (e.g. "righteousness" for dharma),
which keyword search on the Sanskrit term alone would miss.

{concept_density(passages, mentions, gazetteer)}

## 2. Concepts across the 18 books of the Mahabharata (mentions per 10,000 words)

{mahabharata_trajectory(passages, mentions, gazetteer)}

## 3. Central figures per work

Co-occurrence strength sums, over every other figure, the passages shared with that figure.
Patronymics inflate some counts: "son of Pandu" names the Pandavas, not Pandu himself.

{central_figures(mentions, gazetteer)}

## 4. Figures who appear in three or more works (mention counts)

{cross_work_figures(mentions, gazetteer)}

## Caveats

- Matching is lexical. Epithets not in the gazetteer are missed, and some aliases are
  ambiguous (e.g. "Hari" names Vishnu but can also mean a monkey or a lion).
- Renderings such as "duty" or "sacrifice" are common English words, so rendering counts
  overstate the concept's presence. They are reported separately for that reason.
- All four works are English translations; densities partly measure translators' style.
"""
    path = out_dir / "corpus-analysis.md"
    path.write_text(report, encoding="utf-8")
    return path
