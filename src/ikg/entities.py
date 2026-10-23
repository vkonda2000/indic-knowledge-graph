"""Gazetteer-based entity and concept extraction.

Deterministic on purpose: every mention can be traced to an exact surface form in an
exact passage, which makes the graph auditable and lets evaluation separate retrieval
errors from extraction errors. LLM-assisted extraction can be layered on later.
"""

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from ikg import config
from ikg.normalize import fold
from ikg.segment import Passage


@dataclass
class Entity:
    id: str
    type: str
    label: str
    iast: str = ""
    aliases: list[str] = field(default_factory=list)
    aliases_cs: list[str] = field(default_factory=list)
    renderings: list[str] = field(default_factory=list)
    works: list[str] = field(default_factory=list)
    aliases_by_work: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Mention:
    entity: str
    passage: str
    work: str
    surface: str
    via: str  # alias | rendering


@dataclass
class _Pattern:
    entity: str
    via: str
    works: frozenset | None  # None means all works


class Gazetteer:
    def __init__(self, entities: list[Entity]):
        self.entities = {e.id: e for e in entities}
        # surface form -> pattern info, split by case sensitivity
        self._ci: dict[str, _Pattern] = {}
        self._cs: dict[str, _Pattern] = {}
        for e in entities:
            scope = frozenset(e.works) if e.works else None
            for a in e.aliases:
                self._ci[fold(a).lower()] = _Pattern(e.id, "alias", scope)
            for r in e.renderings:
                self._ci[fold(r).lower()] = _Pattern(e.id, "rendering", scope)
            for a in e.aliases_cs:
                self._cs[fold(a)] = _Pattern(e.id, "alias", scope)
            for work, aliases in e.aliases_by_work.items():
                for a in aliases:
                    self._ci[fold(a).lower()] = _Pattern(e.id, "alias", frozenset([work]))
        self._ci_re = self._compile(self._ci, re.IGNORECASE)
        self._cs_re = self._compile(self._cs, 0)

    @staticmethod
    def _compile(table: dict, flags: int) -> re.Pattern | None:
        if not table:
            return None
        # Longest first, so "krishna-dwaipayana" wins over "krishna".
        alts = sorted(table, key=len, reverse=True)
        body = "|".join(re.escape(a).replace(r"\ ", r"\s+") for a in alts)
        return re.compile(rf"(?<![\w-])({body})(?![\w-])", flags)

    @classmethod
    def load(cls, path: Path | None = None) -> "Gazetteer":
        path = path or config.gazetteer_file()
        return cls.from_yaml(path.read_text(encoding="utf-8"))

    @classmethod
    def from_yaml(cls, text: str) -> "Gazetteer":
        """Parse and validate gazetteer YAML; raises ValueError with a readable message."""
        data = yaml.safe_load(text)
        if not isinstance(data, dict) or not isinstance(data.get("entities"), list):
            raise ValueError("gazetteer must be a mapping with an 'entities' list")
        entities, seen = [], set()
        for i, e in enumerate(data["entities"]):
            try:
                entity = Entity(**e)
            except TypeError as exc:
                raise ValueError(f"entity #{i + 1}: {exc}") from exc
            if entity.id in seen:
                raise ValueError(f"duplicate entity id: {entity.id}")
            seen.add(entity.id)
            entities.append(entity)
        return cls(entities)

    def find(self, text: str, work: str | None = None) -> list[tuple[str, str, str, int, int]]:
        """Return (entity_id, surface, via, start, end) for each match, non-overlapping."""
        folded = fold(text)
        hits, taken = [], []
        for regex, table, lower in ((self._ci_re, self._ci, True), (self._cs_re, self._cs, False)):
            if regex is None:
                continue
            for m in regex.finditer(folded):
                surface = m.group(1)
                key = re.sub(r"\s+", " ", surface.lower() if lower else surface)
                pat = table.get(key)
                if pat is None or (pat.works is not None and work is not None and work not in pat.works):
                    continue
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue
                taken.append((m.start(), m.end()))
                hits.append((pat.entity, surface, pat.via, m.start(), m.end()))
        return hits

    def entities_in(self, text: str) -> list[str]:
        """Distinct entity ids mentioned in free text (e.g. a question), in order of appearance."""
        seen: dict[str, None] = {}
        for ent, *_ , start, _end in sorted(self.find(text), key=lambda h: h[3]):
            seen.setdefault(ent, None)
        return list(seen)

    def expansion_terms(self, entity_id: str) -> list[str]:
        e = self.entities[entity_id]
        terms = e.aliases + e.aliases_cs + e.renderings
        for aliases in e.aliases_by_work.values():
            terms += aliases
        return terms


def extract(passages: list[Passage], gazetteer: Gazetteer) -> list[Mention]:
    mentions = []
    for p in passages:
        for ent, surface, via, _s, _e in gazetteer.find(p.text, work=p.work):
            mentions.append(Mention(ent, p.id, p.work, surface, via))
    return mentions


def write_mentions(mentions: list[Mention], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for m in mentions:
            f.write(json.dumps(asdict(m), ensure_ascii=False) + "\n")


def read_mentions(path: Path) -> list[Mention]:
    with path.open(encoding="utf-8") as f:
        return [Mention(**json.loads(line)) for line in f if line.strip()]


def mentions_by_passage(mentions: list[Mention]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        index[m.passage].add(m.entity)
    return index
