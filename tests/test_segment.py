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
