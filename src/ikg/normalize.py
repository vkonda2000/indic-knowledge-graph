"""Text normalisation shared by segmentation, entity matching and retrieval.

Victorian and Indian translators spell Sanskrit names inconsistently
(Ráma / Rama, Vishṇu / Vishnu, Parāçara / Parasara). Folding diacritics to
plain ASCII lets one alias list match every edition.
"""

import re
import unicodedata

_PUNCT_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset("""
a an and are as at be been but by did do does for from had has have he her hers him his how i
if in into is it its me my no not o of on or our she so that the thee their them then there these
they thou thy this those to unto upon us was we were what when where which who whom why will with
ye you your shall hath doth art
""".split())


def fold(text: str) -> str:
    """Strip diacritics and normalise quotes/dashes, keeping case."""
    text = text.translate(_PUNCT_MAP)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    tokens = _TOKEN_RE.findall(fold(text).lower())
    if drop_stopwords:
        return [t for t in tokens if t not in STOPWORDS]
    return tokens


def roman_to_int(numeral: str) -> int:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total, prev = 0, 0
    for ch in reversed(numeral.upper()):
        value = values[ch]
        total = total - value if value < prev else total + value
        prev = max(prev, value)
    return total
