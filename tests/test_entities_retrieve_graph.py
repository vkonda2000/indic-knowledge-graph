from conftest import make_passage

from ikg.entities import extract, mentions_by_passage
from ikg.graph import build_cooccurrence, neighbours
from ikg.retrieve import BM25Index, search


def ids(hits):
    return [h[0] for h in hits]


def test_longest_alias_wins(gazetteer):
    assert ids(gazetteer.find("Krishna-Dwaipayana composed it, and Krishna listened.")) == ["vyasa", "krishna"]


def test_case_sensitive_alias(gazetteer):
    assert ids(gazetteer.find("Know the Self.")) == ["atman"]
    assert ids(gazetteer.find("know thy self")) == []


def test_work_restricted_aliases(gazetteer):
    assert ids(gazetteer.find("O Bharata, Rama spoke", work="mahabharata")) == []
    assert sorted(ids(gazetteer.find("O Bharata, Rama spoke", work="ramayana"))) == ["bharata_ram", "rama"]
    assert ids(gazetteer.find("Dasarathi", work="mahabharata")) == ["rama"]


def test_renderings_are_tagged(gazetteer):
    hits = gazetteer.find("Dharma is righteousness.")
    assert [(h[0], h[2]) for h in hits] == [("dharma", "alias"), ("dharma", "rendering")]


def test_diacritics_match(gazetteer):
    assert ids(gazetteer.find("Sítá wept", work="ramayana")) == ["sita"]


PASSAGES = [
    make_passage("a", "mahabharata", "Partha asked Kesava about righteousness and duty in battle."),
    make_passage("b", "mahabharata", "The horses were watered at the river and the camp slept."),
    make_passage("c", "ramayana", "Sita and Rama walked in the forest.", kind="verse"),
    make_passage("d", "mahabharata", "A commentary on war and horses.", kind="commentary"),
]


def test_expansion_retrieves_translator_rendering(gazetteer):
    index = BM25Index(PASSAGES)
    pe = mentions_by_passage(extract(PASSAGES, gazetteer))
    # "dharma" never appears in passage a, only its renderings do
    assert [h.passage.id for h in search(index, gazetteer, "What is dharma?", k=1, passage_entities=pe)] == ["a"]
    assert search(index, gazetteer, "What is dharma?", k=1, expand=False) == []


def test_filters(gazetteer):
    index = BM25Index(PASSAGES)
    assert sorted(h.passage.id for h in search(index, gazetteer, "horses", works=["mahabharata"])) == ["b", "d"]
    assert [h.passage.id for h in search(index, gazetteer, "horses", primary_only=True)] == ["b"]


def test_cooccurrence_graph(gazetteer):
    mentions = extract(PASSAGES, gazetteer)
    g = build_cooccurrence(mentions, gazetteer)
    assert g["arjuna"]["krishna"]["weight"] == 1
    assert g["arjuna"]["krishna"]["weight_mahabharata"] == 1
    assert g.nodes["dharma"]["mentions"] == 2
    assert neighbours(g, "rama") == [("sita", 1)]
