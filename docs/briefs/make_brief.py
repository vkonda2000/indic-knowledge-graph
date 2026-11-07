"""Two-page brief (docs/briefs/ikg-brief.pdf): what IKG provides, how it widens access, and five
complementary projects. Regenerate: pip install reportlab; python docs/briefs/make_brief.py docs/briefs/ikg-brief.pdf
(uses the Calibri and Georgia fonts that ship with Windows)."""

import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = sys.argv[1]
F = "C:/Windows/Fonts/"
pdfmetrics.registerFont(TTFont("Body", F + "calibri.ttf"))
pdfmetrics.registerFont(TTFont("Body-Bold", F + "calibrib.ttf"))
pdfmetrics.registerFont(TTFont("Body-Italic", F + "calibrii.ttf"))
pdfmetrics.registerFont(TTFont("Body-BoldItalic", F + "calibriz.ttf"))
pdfmetrics.registerFont(TTFont("Head", F + "georgiab.ttf"))
pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic",
                              boldItalic="Body-BoldItalic")

INK = colors.HexColor("#1f2328")
MUTED = colors.HexColor("#57606a")
ACCENT = colors.HexColor("#8a3b12")      # deep saffron-brown
TINT = colors.HexColor("#fbf3ec")
RULE = colors.HexColor("#e2c9b6")

title = ParagraphStyle("title", fontName="Head", fontSize=19, leading=23, textColor=ACCENT, spaceAfter=2)
subtitle = ParagraphStyle("subtitle", fontName="Body-Italic", fontSize=10.5, leading=13, textColor=MUTED,
                          spaceAfter=8)
h2 = ParagraphStyle("h2", fontName="Head", fontSize=11.5, leading=14, textColor=ACCENT, spaceBefore=7,
                    spaceAfter=3)
body = ParagraphStyle("body", fontName="Body", fontSize=9.6, leading=12.2, textColor=INK, alignment=TA_LEFT,
                      spaceAfter=3)
bullet = ParagraphStyle("bullet", parent=body, leftIndent=10, bulletIndent=0, spaceAfter=1.6)
cell = ParagraphStyle("cell", fontName="Body", fontSize=8.5, leading=10.4, textColor=INK)
cellb = ParagraphStyle("cellb", parent=cell, fontName="Body-Bold")
headcell = ParagraphStyle("headcell", parent=cell, fontName="Body-Bold", textColor=colors.white)
note = ParagraphStyle("note", parent=body, fontSize=8.3, leading=10.2, textColor=MUTED)


def bullets(items):
    return [Paragraph(t, bullet, bulletText="•") for t in items]


def table(rows, widths, header_bg=ACCENT, zebra=True):
    data = [[Paragraph(c, headcell) for c in rows[0]]]
    for r in rows[1:]:
        data.append([Paragraph(r[0], cellb)] + [Paragraph(c, cell) for c in r[1:]])
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
    ]
    if zebra:
        style += [("BACKGROUND", (0, i), (-1, i), TINT) for i in range(2, len(data), 2)]
    t.setStyle(TableStyle(style))
    return t


def callout(paragraphs, width):
    t = Table([[paragraphs]], colWidths=[width])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Body", 7.8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.6 * inch, 0.42 * inch,
                      "Indic Knowledge Graph (IKG) · project brief · November 2026 · working prototype")
    canvas.drawRightString(letter[0] - 0.6 * inch, 0.42 * inch, f"{doc.page} / 2")
    canvas.restoreState()


W = letter[0] - 1.2 * inch
s = []

# ---------------- Page 1 ----------------
s.append(Paragraph("Indic Knowledge Graph", title))
s.append(Paragraph("Trustworthy, cited access to the Upanishads, Mahābhārata, Rāmāyaṇa and Purāṇas: "
                   "what IKG provides, and how it widens access to them", subtitle))

s.append(Paragraph("The problem", h2))
s.append(Paragraph(
    "India's classical texts run to millions of words. Most readers meet them through 19th-century English "
    "translations they can't easily search, or through general AI chatbots. A chatbot answers fluently, but it "
    "blends sources, invents verses, presents a commentator's opinion as the text itself, and hides the fact "
    "that \u201crighteousness\u201d is one translator's word for <i>dharma</i>. A reader can't check any of it.", body))

s.append(Paragraph("What IKG provides", h2))
s += bullets([
    "<b>Answers you can check.</b> Every sentence of an answer cites the exact passage, edition and translator, "
    "and links to it. When the texts don't answer a question, IKG says so instead of guessing.",
    "<b>The text, the commentary and the scholar, kept apart.</b> Each passage is labelled as primary text, "
    "a translator's commentary or modern scholarship, and answers attribute each one.",
    "<b>Sanskrit concepts kept separate from English wording.</b> A curated gazetteer links <i>dharma</i>, "
    "<i>ātman</i> or <i>Pārtha</i> to every way each translator rendered them, so a search finds them all, "
    "and translators can be compared.",
    "<b>Search by meaning and by keyword.</b> Hybrid search combines both: since November it finds the "
    "answering passage first 41% of the time, up from 28%.",
    "<b>Open, inspectable data.</b> A knowledge graph of people, places and concepts, exported as standard "
    "linked data (RDF/OWL, queryable with SPARQL). Public-domain sources, pinned by checksum, rebuilt "
    "identically every time.",
    "<b>Low cost, or none.</b> About $0.001 per answer with Claude, or $0 with a local open model (Qwen) "
    "on an ordinary laptop, offline.",
])

s.append(Paragraph("How it adds to knowledge dissemination and access", h2))
s.append(table([
    ["Who", "What changes for them"],
    ["Students and general readers", "Ask in plain English and get the actual verses to read, with the translator "
     "named. Learning starts from the source, not from a summary of a summary."],
    ["Readers without Sanskrit, including the diaspora",
     "Sanskrit terms are bridged to the English words translators actually used, so <i>moksha</i> also finds "
     "\u201cemancipation\u201d. The original concept isn't lost in translation."],
    ["Teachers", "Every claim comes with a passage to assign, and the system refuses to answer trick questions "
     "about things the texts never say."],
    ["Scholars", "Translators can be compared side by side, schools of thought kept distinct, and the data "
     "queried with SPARQL. Every answer and build is reproducible."],
    ["Communities and curators", "Texts and lectures can be contributed. Named curators review them, and an "
     "append-only log records who changed what."],
    ["Anyone, on any budget", "It runs on a laptop with a free local model. A shared server costs a few dollars a day, "
     "under a hard spending cap."],
], [1.55 * inch, W - 1.55 * inch]))

s.append(Spacer(1, 5))
s.append(callout([
    Paragraph("<b>Where it stands (November 2026).</b> A working prototype on four works: 14,617 citable "
              "passages, 75 curated entities, 1,903 graph connections, and an 86-question evaluation run on every "
              "change. With Claude, answers declined all 10 trick questions they were tested on, and a local open model "
              "matched Claude's citation precision at no cost. It runs as a web app, a command-line tool and a "
              "Docker stack. <b>Known limits:</b> English translations only; the right passage reaches the "
              "top 8 results for about two-thirds of questions; questions phrased in Sanskrit are the weakest "
              "(11 of 15 still miss).", body)], W))

# ---------------- Page 2 ----------------
s.append(PageBreak())
s.append(Paragraph("Next: making IKG reachable where people already work", h2))
s.append(Paragraph(
    "The same verified backend will be packaged as a standard tool interface (an MCP server) and as plugins for "
    "Claude, ChatGPT and Microsoft 365 Copilot, so people can ask cited questions from the assistants they "
    "already use. An agent will plan multi-step questions, such as comparing what two works say, and check "
    "every citation before answering. Any new claim enters the knowledge graph only after a named curator "
    "approves it.", body))

s.append(Paragraph("Five related projects: complements, not replacements", h2))
s.append(Paragraph(
    "None of these projects offers cited question answering over sources with this provenance. Most work on the "
    "Sanskrit original, which is IKG's main gap, so they fit alongside it.", body))
s.append(table([
    ["Project", "What it is", "How it could strengthen IKG"],
    ["samskritam.ai", "A Sanskrit reading and learning tool with word-by-word analysis, built on the Digital Corpus "
     "of Sanskrit (CC BY 4.0) and the Vidyut Pāṇinian grammar engine, plus an AI grammar benchmark.",
     "Align IKG passages to the Sanskrit original, so a concept like <i>dharma</i> links to the actual Sanskrit "
     "words. This targets IKG's weakest questions. The rule-graded benchmark is a model for IKG's fidelity tests."],
    ["IISc Sanskrit speech (Prathosh A.P.)", "Chanted text-to-speech (Vāgdhenu), Sanskrit speech recognition "
     "(Su-śrotā) and reading apps. Open model weights.",
     "An audio layer: hear a cited verse recited, or ask a question by voice, for oral learners and for "
     "accessibility."],
    ["Sarvamūla", "A Sanskrit reader with audio for 37 works, almost certainly Madhvācārya's Sarvamūla "
     "works (Dvaita Vedānta).",
     "A school of thought on the same texts: commentary to attribute and keep separate from the source text. "
     "Needs permission and edition details first; until then, link out from each verse."],
    ["Siddhāntakośa: \u201cśāstra as a rulebase\u201d", "A research proposal to turn śāstras into machine-readable "
     "rules, piloted on Āyurveda texts and linked by traditional Tantrayukti relations.",
     "Shares IKG's aim of real relations, not just co-occurrence. A source of relation types for the "
     "ontology, and a possible collaborator. Nothing released yet."],
    ["Dharmamitra (UC Berkeley, Tohoku University, Tsadra Foundation)", "Machine translation from Sanskrit, Pāli, Tibetan "
     "and Chinese, plus OCR, a lexicon, and open models for translation and Sanskrit grammatical analysis.",
     "Its lexicon of attested translations (how translators have rendered each Sanskrit word) could check and "
     "extend IKG's gazetteer. Its models could analyse Sanskrit text, or draft translations of untranslated "
     "texts, labelled as machine translation."],
], [1.18 * inch, 2.45 * inch, W - 3.63 * inch]))

s.append(Paragraph("Principles for working with them", h2))
s += bullets([
    "<b>Machine translations are never sources.</b> They can appear beside a passage, clearly labelled, "
    "but an answer cites only the published text, as with commentary.",
    "<b>Licences come first.</b> Of these, only the Digital Corpus of Sanskrit states one (CC BY 4.0). "
    "Nothing else is ingested without the owner's permission and a stated licence.",
    "<b>Measure before adopting.</b> Every addition is tested on IKG's evaluation and kept only if it "
    "improves the numbers, as hybrid search was.",
])

s.append(Paragraph("Suggested order", h2))
s.append(table([
    ["#", "Step", "Why"],
    ["1", "Sanskrit originals aligned to passages, with word-level analysis (DCS; Vidyut or Dharmamitra's "
     "Sanskrit models)", "Improves Sanskrit-phrased questions, IKG's weakest area"],
    ["2", "Dharmamitra's translation lexicon to check and extend the gazetteer's renderings",
     "More accurate concept-to-English links"],
    ["3", "Madhvācārya's commentaries as an attributed school of thought, with permission",
     "Represents a tradition on its own terms"],
    ["4", "Recited audio and voice questions (IISc tools)", "Reaches oral learners; aids accessibility"],
    ["5", "Collaborate with Siddhāntakośa on rule and relation vocabularies", "A step from co-occurrence toward "
     "real relations"],
], [0.25 * inch, 3.95 * inch, W - 4.2 * inch], zebra=True))

s.append(Spacer(1, 6))
s.append(Paragraph(
    "Sources: samskritam.ai; prathosh.in/sanskrit-complinguistics.html; sarvamulavani.com; "
    "siddhantakosha.org/initiatives/shaastra-as-a-rulebase; dharmamitra.org and its model guide "
    "(dharmamitra.github.io). Project details as published by each site in November 2026; licences not "
    "stated there are treated as unknown. IKG figures come from its evaluation (docs/06 §1).", note))

doc = SimpleDocTemplate(OUT, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                        topMargin=0.55 * inch, bottomMargin=0.65 * inch,
                        title="Indic Knowledge Graph: project brief", author="Indic Knowledge Graph project",
                        subject="What IKG provides, how it widens access, and five complementary projects")
doc.build(s, onFirstPage=footer, onLaterPages=footer)
