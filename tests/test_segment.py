from conftest import make_work

from ikg.segment import chunk, parse_mahabharata, parse_ramayana, parse_upanishads, parse_vishnu_purana

MBH = """BOOK 1

ADI PARVA

Translator's preface that must be skipped.

THE MAHABHARATA

ADI PARVA

SECTION I

Om! Having bowed down to Narayana.

Ugrasrava approached the sages.

SECTION II

The second section.

The end of Adi Parva

BOOK 8

Karna-parva

1

Vaishampayana said, "After Drona had been slain."

42 warriors fell.

2

Sanjaya said.
"""


def test_mahabharata_roman_and_arabic_sections():
    ps = parse_mahabharata(MBH, make_work("mahabharata"), "url")
    assert [p.id for p in ps] == ["mbh.b01.s001.p01", "mbh.b01.s002.p01", "mbh.b08.s001.p01", "mbh.b08.s002.p01"]
    assert "preface" not in " ".join(p.text for p in ps)
    assert "The end" not in ps[1].text
    # a stray number that is not the next section stays in the text
    assert "42 warriors" in ps[2].text
    assert ps[0].book_label == "Book 1 (Adi Parva)"
    assert ps[2].book_label == "Book 8 (Karna Parva)"
    assert ps[2].locator == {"book": 8, "section": 1}


RAM = """Book I.
Contents listing.

BOOK I.(6)

Canto I. Nárad.(7)

  To sainted Nárad, prince of those
Whose lore in words of wisdom flows.(9)

Canto II. Brahmá’s Visit

Second canto.

BOOK II.

Canto I. The Heir Apparent.

Bharat speaks.

APPENDIX.

Notes that must be excluded.
"""


def test_ramayana_books_cantos_and_footnote_markers():
    ps = parse_ramayana(RAM, make_work("ramayana"), "url")
    assert [p.id for p in ps] == ["ram.b1.c001.p01", "ram.b1.c002.p01", "ram.b2.c001.p01"]
    assert ps[0].section_label == "Canto 1: Nárad"
    assert "(9)" not in ps[0].text and "\n" in ps[0].text  # verse lines kept
    assert all("Notes" not in p.text for p in ps)


UPA = """Contents
Introduction
Isa-Upanishad

                          Introduction

General introduction text.

                         Isa-Upanishad

Introduction to the Isa.

                         Isa-Upanishad

Peace Chant

OM!  That is whole.

OM!  PEACE!  PEACE!  PEACE!

Commentary on the peace chant.

I

All this should be covered by the Lord.

The translator explains verse one.

II

Second verse without commentary.

                         Kena-Upanishad

Introduction to the Kena.

KENA-UPANISHAD

                           Part First

I

By whom commanded does the mind go?
"""


def test_upanishads_separate_verse_commentary_and_introduction():
    ps = {p.id: p for p in parse_upanishads(UPA, make_work("upanishads"), "url")}
    assert ps["upa.general.intro01"].kind == "introduction"
    assert ps["upa.isa.intro01"].kind == "introduction"
    assert ps["upa.isa.peace"].kind == "verse" and "PEACE!" in ps["upa.isa.peace"].text
    assert ps["upa.isa.peace.c1"].kind == "commentary"
    assert ps["upa.isa.v01"].text == "All this should be covered by the Lord."
    assert ps["upa.isa.v01.c1"].text == "The translator explains verse one."
    assert "upa.isa.v02.c1" not in ps
    assert ps["upa.kena.p1.v01"].section_label == "Part 1, Verse 1"
    # Locators give the citation position; the commentary shares its verse's locator.
    assert ps["upa.kena.p1.v01"].locator == {"upanishad": "kena", "part": 1, "verse": 1}
    assert ps["upa.isa.v01.c1"].locator == {"upanishad": "isa", "verse": 1}
    assert ps["upa.isa.peace"].locator == {"upanishad": "isa", "verse": "peace"}
    assert ps["upa.isa.intro01"].locator == {"upanishad": "isa"}


VP = """PART I.
Contents summary.

PART I.

SECTION I.

Om![1] Salutation unto Vasudeva!

SECTION II.

Second.

PART V.

SECTION XXVIII.

Twenty-eighth.

SECTION XXVIII.

Actually the thirty-eighth (typo in source).

[1] Footnote text.
"""


def test_vishnu_purana_parts_sections_and_typo_repair():
    ps = parse_vishnu_purana(VP, make_work("vishnu_purana"), "url")
    assert [p.id for p in ps] == ["vp.b1.s001.p01", "vp.b1.s002.p01", "vp.b5.s028.p01", "vp.b5.s029.p01"]
    assert "[1]" not in ps[0].text
    assert all("Footnote" not in p.text for p in ps)


def test_chunk_packs_paragraphs_and_splits_long_ones():
    long_para = " ".join(f"Sentence number {i} ends here." for i in range(200))
    chunks = chunk(["short one", "short two", long_para], max_chars=500)
    assert chunks[0] == "short one\n\nshort two"
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks).count("Sentence number") == 200


def test_segment_work_records_work_and_edition(tmp_path):
    from ikg.segment import segment_work
    work = make_work("mahabharata-dutt", parser="mahabharata")
    work.work, work.edition, work.files = "mahabharata", "dutt-1895", [{"gutenberg": 1}]
    work.id_prefix = "mbhd"  # a second edition of a bundled text needs its own ID prefix (D11)
    (tmp_path / "pg1.txt").write_text(MBH, encoding="utf-8")
    ps = segment_work(work, raw_dir=tmp_path)
    assert {(p.work, p.edition) for p in ps} == {("mahabharata", "dutt-1895")}
    assert ps[0].work_title == "Mahabharata"
    ganguli = {p.id for p in parse_mahabharata(MBH, make_work("mahabharata"), "url")}
    assert ps[0].id == "mbhd.b01.s001.p01" and not ganguli & {p.id for p in ps}


# A fixed text and the exact chunks v1 makes of it. Passage IDs number these chunks, so any
# change to chunk(), the sentence splitter or MAX_CHARS that fails this test renumbers IDs
# for every edition and needs an ID migration map (D11).
SNAPSHOT_PARAS = [
    "First short paragraph.",
    "Second paragraph. " * 60,
    "Third (with a quotation). \"Then he spoke!\" And it ended; the end. " * 25,
    "Last.",
]


SNAPSHOT_LENGTHS = [1104, 1494, 162]
SNAPSHOT_PATTERN = r"(?:(?<=[.!?;])|(?<=[.!?;][\"')\]]))\s+(?=[\"'(A-Z])"


def test_chunking_v1_snapshot():
    from ikg.segment import _SENTENCE_END_RE
    chunks = chunk(SNAPSHOT_PARAS)
    assert [len(c) for c in chunks] == SNAPSHOT_LENGTHS
    assert _SENTENCE_END_RE.pattern == SNAPSHOT_PATTERN


def test_mahabharata_footnotes_are_not_part_of_a_section():
    text = (
        "BOOK 12\n\nSECTION I\n\nThe last real paragraph of the parva.\n\n"
        "FOOTNOTES\n\n1. A translator's note.\n\n2\n\n3. Another note, after a bare number.\n\n"
        "SECTION II\n\nThe next section's text.\n")
    ps = parse_mahabharata(text, make_work("mahabharata"), "url")
    assert [p.id for p in ps] == ["mbh.b12.s001.p01", "mbh.b12.s002.p01"]
    assert "translator's note" not in " ".join(p.text for p in ps)
    assert ps[0].text.strip() == "The last real paragraph of the parva."


def test_upanishad_part_heading_is_case_insensitive():
    text = UPA + "\n                           Part fourth\n\nI\n\nShe (Uma) said: It is Brahman.\n"
    ps = {p.id: p for p in parse_upanishads(text, make_work("upanishads"), "url")}
    assert ps["upa.kena.p4.v01"].text == "She (Uma) said: It is Brahman."
    assert ps["upa.kena.p4.v01"].locator == {"upanishad": "kena", "part": 4, "verse": 1}


def _text_pdf(pages: list[str], page_labels: str = "") -> bytes:
    """A minimal PDF with one line of text per page (pypdf can read but not write text).
    `page_labels` is an optional /PageLabels number tree, e.g. "0 << /S /r >> 2 << /S /D >>"."""
    catalog = "<< /Type /Catalog /Pages 2 0 R" + (f" /PageLabels << /Nums [{page_labels}] >>" if page_labels else "") + " >>"
    objs = [catalog, None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_page_tracking_changes_no_paragraph_chunk_or_id():
    from ikg.segment import _paragraphs_with_pages, chunk_sources, join_pages, paragraphs, parse_generic
    pages = ["# Book One\n\n## Chapter 1\n\n" + ("A long sentence about dharma. " * 40),
             "Second page text continues.\n\nAnother paragraph.", "", "## Chapter 2\n\nThird page.",
             # Content that a text marker could be confused with: none of it may change anything.
             "\x0cpage 7\nform feeds\x0c and a NUL\x00 in the text layer", "   ", "Last page."]
    text, line_pages = join_pages(pages)
    lines = text.split("\n")
    paras, spans = _paragraphs_with_pages(lines, line_pages)
    assert paras == paragraphs(lines) and len(spans) == len(paras)
    assert len(chunk_sources(paras)) == len(chunk(paras))
    work = make_work("notes", "generic")
    before = parse_generic(text, work, "u")
    after = parse_generic(text, work, "u", line_pages=line_pages)
    assert [(p.id, p.text) for p in before] == [(p.id, p.text) for p in after]
    assert all("page" not in p.locator for p in before)
    assert after[0].locator["page"] == 1
    assert after[-1].locator["page_end"] == 7 and "page 7" in after[-1].text  # a forged marker is just text
    spanning = [p for p in after if "page_end" in p.locator]
    assert spanning, "the long first chapter runs onto page 2"
    first, last = spanning[0].locator["page"], spanning[0].locator["page_end"]
    assert f"pp. {first}\u2013{last}" in spanning[0].ref


def test_pdf_upload_gets_page_citations(tmp_path):
    from ikg.segment import parse_generic, read_pdf, read_source
    path = tmp_path / "book.pdf"
    path.write_bytes(_text_pdf(["CHAPTER 1", "Arjuna spoke to Krishna.", "CHAPTER 2", "Krishna answered him."]))
    text, line_pages, labels = read_pdf(path)
    assert text == read_source(path)  # the text is exactly what it was before page tracking
    passages = parse_generic(text, make_work("book", "generic"), "upload:book.pdf", line_pages=line_pages,
                             page_labels=labels)
    by_text = {p.text: p for p in passages}
    arjuna = next(p for t, p in by_text.items() if "Arjuna" in t)
    krishna = next(p for t, p in by_text.items() if "answered" in t)
    assert arjuna.locator["page"] == 2 and krishna.locator["page"] == 4
    assert arjuna.section_label == "CHAPTER 1" and arjuna.ref.split(", ")[-1].startswith("p. 2")


def test_pdf_page_ranges_and_printed_labels(tmp_path):
    from ikg.segment import parse_generic, read_pdf
    path = tmp_path / "book.pdf"
    # Two front-matter pages numbered i, ii; the body starts again at 1 on the third page.
    path.write_bytes(_text_pdf(["Preface text.", "More preface.", "CHAPTER 1", "Arjuna spoke.", "He asked again."],
                               page_labels="0 << /S /r >> 2 << /S /D >>"))
    text, line_pages, labels = read_pdf(path)
    assert labels == ["i", "ii", "1", "2", "3"]
    passages = parse_generic(text, make_work("book", "generic"), "u", line_pages=line_pages, page_labels=labels)
    preface = next(p for p in passages if "Preface" in p.text)
    body = next(p for p in passages if "Arjuna" in p.text)
    assert preface.locator == {**preface.locator, "page": 1, "page_end": 2, "page_label": "i", "page_end_label": "ii"}
    assert "pp. i\u2013ii" in preface.ref
    # Physical pages 4-5 carry printed labels 2-3; the citation uses the printed ones.
    assert body.locator["page"] == 4 and body.locator["page_label"] == "2" and "pp. 2\u20133" in body.ref


def test_nul_in_a_pdf_text_layer_does_not_shift_pages(tmp_path):
    from ikg.segment import read_pdf
    path = tmp_path / "nul.pdf"
    path.write_bytes(_text_pdf([r"before\000after", "page two", "page three"]))  # \000 is a NUL in the PDF string
    text, line_pages, _ = read_pdf(path)
    assert "\x00" in text and max(line_pages) == 3


def test_zero_page_pdf_and_hostile_labels(tmp_path):
    from ikg.segment import _page_locator, join_pages, parse_generic
    assert join_pages([]) == ("", [])
    assert parse_generic("", make_work("e", "generic"), "u", line_pages=[]) == []
    hostile = "A" * 5000 + "\nINJECTED\x07"
    loc = _page_locator(1, 1, [hostile])
    assert len(loc["page_label"]) <= 24 and "\n" not in loc["page_label"]
