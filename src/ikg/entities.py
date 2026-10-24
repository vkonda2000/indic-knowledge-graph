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


def _overlap(a: frozenset | None, b: frozenset | None) -> bool:
    return a is None or b is None or bool(a & b)


def _pick(pats: list[_Pattern], work: str | None) -> _Pattern | None:
    """The pattern that applies in ``work``. Without a work (e.g. a question), a form shared by
    entities in different works is ambiguous and matches nothing."""
    if work is None:
        return pats[0] if len(pats) == 1 else None
    return next((p for p in pats if p.works is None or work in p.works), None)


class Gazetteer:
    def __init__(self, entities: list[Entity]):
        self.entities = {e.id: e for e in entities}
        # surface form -> patterns, split by case sensitivity. A form has several patterns only
        # when it names different entities in disjoint sets of works (e.g. two Bharatas).
        self._ci: dict[str, list[_Pattern]] = {}
        self._cs: dict[str, list[_Pattern]] = {}
        for e in entities:
            scope = frozenset(e.works) if e.works else None
            for a in e.aliases:
                self._add(self._ci, fold(a).lower(), _Pattern(e.id, "alias", scope), a)
            for r in e.renderings:
                self._add(self._ci, fold(r).lower(), _Pattern(e.id, "rendering", scope), r)
            for a in e.aliases_cs:
                self._add(self._cs, fold(a), _Pattern(e.id, "alias", scope), a)
            for work, aliases in e.aliases_by_work.items():
                for a in aliases:
                    self._add(self._ci, fold(a).lower(), _Pattern(e.id, "alias", frozenset([work])), a)
        # The case-insensitive pass runs first and takes the span, so a clashing case-sensitive
        # form of another entity would never match.
        for key, pats in self._cs.items():
            for pat in pats:
                for owner in self._ci.get(key.lower(), []):
                    if owner.entity != pat.entity and _overlap(owner.works, pat.works):
                        raise ValueError(f"surface form '{key}' is used by both '{owner.entity}' and "
                                         f"'{pat.entity}' (case-sensitive) in the same works")
        self._ci_re = self._compile(self._ci, re.IGNORECASE)
        self._cs_re = self._compile(self._cs, 0)

    @staticmethod
    def _add(table: dict[str, list[_Pattern]], key: str, pat: _Pattern, surface: str) -> None:
        # Letting a later entry win would silently re-route a form, so clashes are errors.
        pats = table.setdefault(key, [])
        for i, owner in enumerate(pats):
            if owner.entity == pat.entity:
                # The same entity listing a form twice keeps the wider scope.
                wider = None if owner.works is None or pat.works is None else owner.works | pat.works
                pats[i] = _Pattern(owner.entity, owner.via, wider)
                return
            if _overlap(owner.works, pat.works):
                raise ValueError(f"surface form '{surface}' is used by both '{owner.entity}' and "
                                 f"'{pat.entity}' in the same works; restrict them to different works")
        pats.append(pat)

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
                pat = _pick(table.get(key, []), work)
                if pat is None:
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
