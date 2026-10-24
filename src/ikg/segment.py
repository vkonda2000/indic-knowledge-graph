"""Split each edition into citable passages that keep their place in the text.

Every passage gets a stable id (e.g. ``mbh.b05.s033.p02``, ``upa.katha.p1.v12``) and a
``kind`` that separates translated primary text (verse, narrative) from the translator's
commentary and introductions. Keeping that distinction is what lets the QA layer say
"the Upanishad says X" versus "the translator comments X".
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ikg.normalize import roman_to_int
from ikg.sources import Work, strip_gutenberg

PRIMARY_KINDS = ("verse", "narrative")
# secondary = modern scholarship (books, papers, lecture transcripts) added by contributors
KINDS = ("verse", "narrative", "commentary", "introduction", "secondary")

# Passage IDs, scheme v1 (docs/02 D11). The `.pNN` chunk number depends on MAX_CHARS and the
# sentence splitter in chunk(), so both are frozen for existing editions: changing them
# renumbers IDs that eval items, fidelity items and curated data refer to, and needs an
# explicit ID migration map. The build records them in data/processed/manifest.json.
ID_SCHEME = "v1"
MAX_CHARS = 1500


@dataclass
class Passage:
    id: str
    work: str
    book: str
    book_label: str
    section: int
    section_label: str
    ordinal: int
    kind: str  # verse | narrative | commentary | introduction
    text: str
    translator: str
    source_url: str
    work_title: str = ""
    # `work` is the text (e.g. mahabharata); `edition` the translation it comes from (D10).
    edition: str = ""
    # Where the passage sits in its edition, for citations: e.g. {"book": 5, "section": 33},
    # {"upanishad": "katha", "part": 1, "verse": 12}; later {"page": 112} or {"start": 754.0}.
    locator: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        parts = [self.work_title or WORK_LABELS.get(self.work, self.work), self.book_label]
        if self.section_label:
            parts.append(self.section_label)
        ref = ", ".join(p for p in parts if p)
        if self.kind not in PRIMARY_KINDS:
            ref += f" [{self.kind}]"
        by = "by" if self.kind == "secondary" else "tr."
        return f"{ref} ({by} {self.translator})" if self.translator else ref

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "Passage":
        return cls(**json.loads(line))


WORK_LABELS = {
    "upanishads": "Upanishads",
    "mahabharata": "Mahabharata",
    "ramayana": "Ramayana",
    "vishnu_purana": "Vishnu Purana",
}


def paragraphs(lines: list[str], joiner: str = " ") -> list[str]:
    """Group lines into blank-line-separated paragraphs."""
    out, current = [], []
    for line in lines:
        if line.strip():
            current.append(line.strip())
        elif current:
            out.append(joiner.join(current))
            current = []
    if current:
        out.append(joiner.join(current))
    return out


_SENTENCE_END_RE = re.compile(r"(?:(?<=[.!?;])|(?<=[.!?;][\"')\]]))\s+(?=[\"'(A-Z])")


def _split_long(para: str, max_chars: int) -> list[str]:
    """Split an over-long paragraph at sentence boundaries."""
    if len(para) <= max_chars:
        return [para]
    pieces, current = [], ""
    for sentence in _SENTENCE_END_RE.split(para):
        if current and len(current) + len(sentence) + 1 > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        pieces.append(current)
    return pieces


def chunk(paras: list[str], max_chars: int = MAX_CHARS) -> list[str]:
    """Pack paragraphs into chunks of at most ``max_chars``, splitting over-long ones by sentence."""
    chunks, current, size = [], [], 0
    for para in (piece for p in paras for piece in _split_long(p, max_chars)):
        if current and size + len(para) > max_chars:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(para)
        size += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# ---------------------------------------------------------------- Mahabharata

MBH_BOOK_RE = re.compile(r"^BOOK (\d+)\s*$")
# Books 1-7 and 12-15 use "ADI PARVA" / "SECTION XII"; books 8-11 and 16-18 use
# "Karna-parva" / a bare Arabic numeral on its own line.
MBH_PARVA_RE = re.compile(r"^([A-Z][A-Z\- ]*PARVA|[A-Z][A-Za-z]*(?:-[A-Za-z]+)*-parva)\s*$")
MBH_SECTION_RE = re.compile(r"^SECTION ([IVXLC]+)\s*$")
MBH_ARABIC_SECTION_RE = re.compile(r"^(\d{1,3})\s*$")
MBH_SKIP_RE = re.compile(r"^(THE MAHABHARATA|The [Ee]nd of .*)\s*$")


def parse_mahabharata(text: str, work: Work, source_url: str) -> list[Passage]:
    passages: list[Passage] = []
    book, parva, section, buf = 0, "", 0, []

    def flush():
        if book and section and buf:
            label = f"Book {book} ({parva.replace('-', ' ').title()})" if parva else f"Book {book}"
            for i, body in enumerate(chunk(paragraphs(buf)), 1):
                passages.append(Passage(
                    id=f"{work.id_prefix or 'mbh'}.b{book:02d}.s{section:03d}.p{i:02d}", work=work.id,
                    book=f"{book:02d}", book_label=label, section=section,
                    section_label=f"Section {section}", ordinal=i, kind="narrative",
                    text=body, translator=work.translator, source_url=source_url,
                    locator={"book": book, "section": section}))

    for line in text.split("\n"):
        stripped = line.strip()
        if m := MBH_BOOK_RE.match(stripped):
            flush()
            book, parva, section, buf = int(m.group(1)), "", 0, []
        elif m := MBH_PARVA_RE.match(stripped):
            if not section:
                parva = m.group(1)
        elif m := MBH_SECTION_RE.match(stripped):
            flush()
            section, buf = roman_to_int(m.group(1)), []
        elif (m := MBH_ARABIC_SECTION_RE.match(stripped)) and book and int(m.group(1)) == section + 1:
            # Only accept the next number in sequence, so stray numerals in the text don't split sections.
            flush()
            section, buf = int(m.group(1)), []
        elif MBH_SKIP_RE.match(stripped):
            continue
        elif section:
            buf.append(line)
    flush()
    return passages


# ---------------------------------------------------------------- Ramayana

RAM_BOOK_RE = re.compile(r"^BOOK ([IVX]+)\.")
RAM_CANTO_RE = re.compile(r"^Canto ([IVXLC]+)\.\s*(.*?)\s*$")
RAM_END_RE = re.compile(r"^APPENDIX\.\s*$")
PAREN_NOTE_RE = re.compile(r"\(\d+\)")
RAM_KANDAS = {1: "Bala Kanda", 2: "Ayodhya Kanda", 3: "Aranya Kanda",
              4: "Kishkindha Kanda", 5: "Sundara Kanda", 6: "Yuddha Kanda"}


def parse_ramayana(text: str, work: Work, source_url: str) -> list[Passage]:
    lines = text.split("\n")
    first_canto = next(i for i, l in enumerate(lines) if RAM_CANTO_RE.match(l))
    # Start at the BOOK heading just before the first canto (earlier ones are the table of contents).
    start = max((i for i in range(first_canto) if RAM_BOOK_RE.match(lines[i])), default=first_canto)
    passages: list[Passage] = []
    book, canto, title, buf = 1, 0, "", []

    def flush():
        if canto and buf:
            label = f"Book {book} ({RAM_KANDAS.get(book, '')})"
            section_label = f"Canto {canto}" + (f": {title}" if title else "")
            for i, body in enumerate(chunk(paragraphs(buf, joiner="\n")), 1):
                passages.append(Passage(
                    id=f"{work.id_prefix or 'ram'}.b{book}.c{canto:03d}.p{i:02d}", work=work.id, book=str(book),
                    book_label=label, section=canto, section_label=section_label,
                    ordinal=i, kind="verse", text=body,
                    translator=work.translator, source_url=source_url,
                    locator={"book": book, "canto": canto}))

    for line in lines[start:]:
        clean = PAREN_NOTE_RE.sub("", line).rstrip()
        if RAM_END_RE.match(clean):
            break
        if m := RAM_BOOK_RE.match(clean):
            flush()
            book, canto, buf = roman_to_int(m.group(1)), 0, []
        elif m := RAM_CANTO_RE.match(clean):
            flush()
            canto, title, buf = roman_to_int(m.group(1)), m.group(2).rstrip("."), []
        elif canto:
            buf.append(clean)
    flush()
    return passages


# ---------------------------------------------------------------- Upanishads

UPA_NAME_RE = re.compile(r"^\s*(ISA|KATHA|KENA)-UPANISHAD\s*$", re.IGNORECASE)
UPA_INTRO_RE = re.compile(r"^\s{5,}Introduction\s*$")
UPA_PART_RE = re.compile(r"^\s*Part (First|Second|Third|Fourth|Fifth|Sixth)\s*$")
UPA_VERSE_RE = re.compile(r"^\s*([IVXL]+)\s*$")
UPA_PEACE_RE = re.compile(r"^\s*Peace Chant\s*$")
ORDINALS = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5, "Sixth": 6}


def parse_upanishads(text: str, work: Work, source_url: str) -> list[Passage]:
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if UPA_INTRO_RE.match(l))
    # unit key: (upanishad, mode, part, verse_label) -> lines
    units: dict[tuple, list[str]] = {}
    order: list[tuple] = []
    seen: dict[str, int] = {}
    name, mode, part, verse = "general", "intro", 0, ""

    def key():
        return (name, mode, part, verse)

    for line in lines[start + 1:]:
        if m := UPA_NAME_RE.match(line):
            name = m.group(1).lower()
            seen[name] = seen.get(name, 0) + 1
            mode = "intro" if seen[name] == 1 else "text"
            part, verse = 0, ""
            continue
        if mode == "text":
            if m := UPA_PART_RE.match(line):
                part, verse = ORDINALS[m.group(1)], ""
                continue
            if UPA_PEACE_RE.match(line):
                verse = "peace" if not any(k[0] == name and k[3] == "peace" for k in order) else "peace-close"
                continue
            if m := UPA_VERSE_RE.match(line):
                verse = str(roman_to_int(m.group(1)))
                continue
        k = key()
        if k not in units:
            units[k] = []
            order.append(k)
        units[k].append(line)

    passages: list[Passage] = []
    for upa, mode, part, verse in order:
        paras = paragraphs(units[(upa, mode, part, verse)])
        if not paras:
            continue
        book_label = "Introduction" if upa == "general" else f"{upa.title()} Upanishad"
        section_label = f"Part {part}" if part else ""
        base = f"{work.id_prefix or 'upa'}.{upa}" + (f".p{part}" if part else "")

        locator = {"upanishad": upa, **({"part": part} if part else {})}

        def add(pid, kind, body, ordinal, label=section_label, verse=None):
            passages.append(Passage(
                id=pid, work=work.id, book=upa, book_label=book_label, section=part,
                section_label=label, ordinal=ordinal, kind=kind, text=body,
                translator=work.translator, source_url=source_url,
                locator={**locator, **({"verse": verse} if verse else {})}))

        if mode == "intro" or not verse:
            for i, body in enumerate(chunk(paras), 1):
                add(f"{base}.intro{i:02d}" if mode == "intro" else f"{base}.pre{i:02d}",
                    "introduction", body, i)
            continue

        if verse.startswith("peace"):
            split = next((i + 1 for i, p in enumerate(paras) if "PEACE!" in p.upper()), 1)
            vid, vlabel = f"{base}.{verse}", ", ".join(filter(None, [section_label, "Peace Chant"]))
            ordinal = 0
        else:
            split = 1
            vid = f"{base}.v{int(verse):02d}"
            vlabel = ", ".join(filter(None, [section_label, f"Verse {verse}"]))
            ordinal = int(verse)
        vloc = verse if verse.startswith("peace") else int(verse)
        add(vid, "verse", "\n\n".join(paras[:split]), ordinal, vlabel, vloc)
        for i, body in enumerate(chunk(paras[split:]), 1):
            add(f"{vid}.c{i}", "commentary", body, ordinal, vlabel, vloc)
    return passages


# ---------------------------------------------------------------- Vishnu Purana

VP_PART_RE = re.compile(r"^PART ([IVX]+)[.:]\s*$")
VP_SECTION_RE = re.compile(r"^SECTION ([IVXLC]+)\.\s*$")
VP_NOTES_RE = re.compile(r"^\[1\] ")
BRACKET_NOTE_RE = re.compile(r"\[\d+\]")


def parse_vishnu_purana(text: str, work: Work, source_url: str) -> list[Passage]:
    lines = text.split("\n")
    first_section = next(i for i, l in enumerate(lines) if VP_SECTION_RE.match(l.strip()))
    start = max(i for i in range(first_section) if VP_PART_RE.match(lines[i].strip()))
    passages: list[Passage] = []
    part, section, buf = 0, 0, []

    def flush():
        if part and section and buf:
            for i, body in enumerate(chunk(paragraphs(buf)), 1):
                passages.append(Passage(
                    id=f"{work.id_prefix or 'vp'}.b{part}.s{section:03d}.p{i:02d}", work=work.id, book=str(part),
                    book_label=f"Part {part}", section=section,
                    section_label=f"Section {section}", ordinal=i, kind="narrative",
                    text=body, translator=work.translator, source_url=source_url,
                    locator={"part": part, "section": section}))

    for line in lines[start:]:
        if VP_NOTES_RE.match(line):
            break
        stripped = line.strip()
        if m := VP_PART_RE.match(stripped):
            flush()
            part, section, buf = roman_to_int(m.group(1)), 0, []
        elif m := VP_SECTION_RE.match(stripped):
            flush()
            # The Gutenberg text has a typo (Part V repeats "SECTION XXVIII." where XXXVIII
            # is meant), so keep numbering monotonic within a part.
            section, buf = max(roman_to_int(m.group(1)), section + 1), []
        elif section:
            buf.append(BRACKET_NOTE_RE.sub("", line))
    flush()
    return passages


# ---------------------------------------------------------------- generic (added and uploaded texts)

_NUM = r"(?:\d+|[IVXLC]+)\b"
GEN_L1_RE = re.compile(rf"^(?:#\s+(?P<md>.+)|(?P<kw>(?:BOOK|PART|VOLUME|KANDA|PARVA)\s+{_NUM}.*))$", re.IGNORECASE)
GEN_L2_RE = re.compile(rf"^(?:##\s+(?P<md>.+)|(?P<kw>(?:CHAPTER|SECTION|CANTO|ADHYAYA|SARGA)\s+{_NUM}.*))$",
                       re.IGNORECASE)
MAX_HEADING_CHARS = 100


def parse_generic(text: str, work: Work, source_url: str, file_index: int = 1) -> list[Passage]:
    """Split on common heading patterns (BOOK/PART ..., CHAPTER/SECTION/CANTO ..., or Markdown
    # / ##). Text without headings becomes one book with one section."""
    passages: list[Passage] = []
    book, section = 1, 1
    book_label, section_label = (f"File {file_index}" if file_index > 1 else ""), ""
    buf: list[str] = []
    started_book = False

    def heading(m: re.Match) -> str:
        return (m.group("md") or m.group("kw")).strip().rstrip(".")

    def flush():
        for i, body in enumerate(chunk(paragraphs(buf)), 1):
            passages.append(Passage(
                id=f"{work.id_prefix or work.id}.f{file_index}.b{book}.s{section:03d}.p{i:02d}", work=work.id,
                book=f"{file_index}.{book}", book_label=book_label or f"Part {book}",
                section=section, section_label=section_label, ordinal=i, kind=work.kind,
                text=body, translator=work.translator, source_url=source_url,
                locator={"file": file_index, "book": book, "section": section}))

    def has_text():
        return any(line.strip() for line in buf)

    for line in text.split("\n"):
        stripped = line.strip()
        if len(stripped) <= MAX_HEADING_CHARS and (m := GEN_L1_RE.match(stripped)):
            flush()
            if started_book or has_text():
                book += 1
            started_book, buf = True, []
            book_label, section, section_label = heading(m), 1, ""
        elif len(stripped) <= MAX_HEADING_CHARS and (m := GEN_L2_RE.match(stripped)):
            flush()
            if section_label or has_text():
                section += 1
            buf, section_label = [], heading(m)
        else:
            buf.append(line)
    flush()
    return passages


# ---------------------------------------------------------------- driver

PARSERS = {
    "mahabharata": parse_mahabharata,
    "ramayana": parse_ramayana,
    "upanishads": parse_upanishads,
    "vishnu_purana": parse_vishnu_purana,
    "generic": parse_generic,
}


def read_source(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return strip_gutenberg(path.read_text(encoding="utf-8", errors="replace"))


def segment_work(work: Work, raw_dir: Path | None = None) -> list[Passage]:
    parser = PARSERS[work.parser]
    passages: list[Passage] = []
    for n, (path, url) in enumerate(zip(work.raw_paths(raw_dir), work.source_urls()), 1):
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing; run `ikg fetch` first")
        text = read_source(path)
        if parser is parse_generic:
            passages.extend(parse_generic(text, work, url, file_index=n))
        else:
            passages.extend(parser(text, work, url))
    title = WORK_LABELS.get(work.work_id, work.title)
    for p in passages:
        # Parsers build IDs from the registry entry; the passage records the text and edition.
        p.work, p.edition, p.work_title = work.work_id, work.edition_id, title
    return passages


def write_passages(passages: list[Passage], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for p in passages:
            f.write(p.to_json() + "\n")


def read_passages(path: Path) -> list[Passage]:
    with path.open(encoding="utf-8") as f:
        return [Passage.from_json(line) for line in f if line.strip()]
