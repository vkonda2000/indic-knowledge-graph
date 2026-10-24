"""RDF export, the analysis report, the CLI's offline commands, gazetteer validation, source
pinning and the eval gate."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from conftest import make_passage, make_work
from rdflib import RDF, Graph, Literal, URIRef

from ikg import cli, config, sources
from ikg.analysis import write_report
from ikg.entities import Gazetteer, extract, write_mentions
from ikg.evaluate import regressions
from ikg.graph import DATA, IKG, build_cooccurrence, save_graph, to_rdf
from ikg.retrieve import BM25Index
from ikg.segment import write_passages

ONTOLOGY = Path(__file__).resolve().parents[1] / "ontology" / "ikg.ttl"

PASSAGES = [
    make_passage("mbh.b01.s001.p01", "mahabharata", "Partha asked Kesava about righteousness in battle."),
    make_passage("mbh.b01.s001.p02", "mahabharata", "Krishna spoke of dharma and the Self."),
    make_passage("ram.b01.s001.p01", "ramayana", "Sita and Rama walked in the forest.", kind="verse"),
]
WORKS = [make_work("mahabharata"), make_work("ramayana")]


def _export(gazetteer, with_passages=False):
    mentions = extract(PASSAGES, gazetteer)
    return to_rdf(WORKS, PASSAGES, mentions, gazetteer, build_cooccurrence(mentions, gazetteer),
                  with_passages=with_passages)


def test_rdf_entities_renderings_and_mentions(gazetteer):
    rdf = _export(gazetteer)
    dharma = DATA["entity/dharma"]
    assert (dharma, RDF.type, IKG.Concept) in rdf
    rendering = DATA["rendering/dharma/righteousness"]
    assert (rendering, IKG.renders, dharma) in rdf
    assert (rendering, IKG.surface, Literal("righteousness")) in rdf
    assert (DATA["entity/arjuna"], IKG.coOccursWith, DATA["entity/krishna"]) in rdf
    # Mentions are aggregated per entity, book and match type.
    rows = rdf.query("""SELECT ?via ?n WHERE { ?m ikg:entity ?e ; ikg:via ?via ; ikg:count ?n }""",
                     initNs={"ikg": IKG}, initBindings={"e": dharma})
    assert sorted((str(v), int(n)) for v, n in rows) == [("alias", 1), ("rendering", 1)]


def test_rdf_passages_only_on_request(gazetteer):
    passage = DATA["passage/ram.b01.s001.p01"]
    assert (passage, None, None) not in _export(gazetteer)
    rdf = _export(gazetteer, with_passages=True)
    assert (passage, RDF.type, IKG.Verse) in rdf
    assert (passage, IKG.partOf, DATA["book/ramayana/1"]) in rdf


def test_rdf_uses_only_terms_declared_in_ontology(gazetteer):
    ontology = Graph().parse(ONTOLOGY, format="turtle")
    declared = {s for s in ontology.subjects() if isinstance(s, URIRef) and s.startswith(str(IKG))}
    rdf = _export(gazetteer, with_passages=True)
    used = {t for s, p, o in rdf for t in (p, o) if isinstance(t, URIRef) and t.startswith(str(IKG))}
    assert used - declared == set()


def test_analysis_report(tmp_path, gazetteer):
    path = write_report(PASSAGES, extract(PASSAGES, gazetteer), gazetteer, tmp_path)
    report = path.read_text(encoding="utf-8")
    assert path.name == "corpus-analysis.md"
    assert report.startswith("# Corpus analysis")
    for heading in ("## Corpus", "## 1. Concept density", "## 2. Concepts across", "## 3. Central figures",
                    "## 4. Figures who appear"):
        assert heading in report
    # Dharma: one direct mention ("dharma") and one rendering ("righteousness") in the Mahabharata.
    assert "(1/1)" in report


@pytest.fixture
def built(tmp_path, monkeypatch, gazetteer):
    """A small built corpus on disk, as `ikg build` would leave it."""
    for name, rel in [("USER_GAZETTEER_FILE", "gazetteer.yaml"), ("PASSAGES_FILE", "passages.jsonl"),
                      ("MENTIONS_FILE", "mentions.jsonl"), ("INDEX_FILE", "bm25.pkl"),
                      ("GRAPH_FILE", "g.graphml")]:
        monkeypatch.setattr(config, name, tmp_path / rel)
    entities = [{k: v for k, v in vars(e).items() if v} for e in gazetteer.entities.values()]
    config.USER_GAZETTEER_FILE.write_text(yaml.safe_dump({"entities": entities}), encoding="utf-8")
    mentions = extract(PASSAGES, gazetteer)
    write_passages(PASSAGES, config.PASSAGES_FILE)
    write_mentions(mentions, config.MENTIONS_FILE)
    BM25Index(PASSAGES).save(config.INDEX_FILE)
    save_graph(build_cooccurrence(mentions, gazetteer), config.GRAPH_FILE)


def test_cli_stats(built, capsys):
    cli.main(["stats"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:2] == ["mahabharata", "2"]
    assert "narrative=2" in lines[0]
    assert lines[1].split()[:2] == ["ramayana", "1"]


def test_cli_search(built, capsys):
    cli.main(["search", "Sita forest", "-k", "1"])
    out = capsys.readouterr().out
    assert "ram.b01.s001.p01" in out
    assert "mbh." not in out


def test_cli_search_without_build(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PASSAGES_FILE", tmp_path / "missing.jsonl")
    with pytest.raises(SystemExit, match="No built corpus"):
        cli.main(["search", "anything"])


def _gz(*entities):
    return Gazetteer.from_yaml(yaml.safe_dump({"entities": list(entities)}))


def test_gazetteer_rejects_surface_form_shared_by_two_entities():
    with pytest.raises(ValueError, match="'Kesava' is used by both 'krishna' and 'kesava_other'"):
        _gz({"id": "krishna", "type": "Person", "label": "Krishna", "aliases": ["krishna", "kesava"]},
            {"id": "kesava_other", "type": "Person", "label": "Other", "aliases": ["Kesava"]})
    # A scoped entity still overlaps an unscoped one.
    with pytest.raises(ValueError, match="used by both"):
        _gz({"id": "a", "type": "Person", "label": "A", "aliases": ["bharata"]},
            {"id": "b", "type": "Person", "label": "B", "aliases": ["bharata"], "works": ["ramayana"]})


def test_gazetteer_rejects_case_sensitive_clash_across_tables():
    with pytest.raises(ValueError, match="'Self' is used by both 'self_word' and 'atman' \\(case-sensitive\\)"):
        _gz({"id": "atman", "type": "Concept", "label": "Atman", "aliases_cs": ["Self"]},
            {"id": "self_word", "type": "Concept", "label": "Self", "aliases": ["self"]})


def test_gazetteer_allows_same_form_in_disjoint_works():
    gz = _gz({"id": "bharata_ram", "type": "Person", "label": "Bharata", "aliases": ["bharata"],
              "works": ["ramayana"]},
             {"id": "bharata_king", "type": "Person", "label": "King Bharata", "aliases": ["bharata"],
              "works": ["mahabharata"]})
    assert [h[0] for h in gz.find("Bharata ruled.", work="ramayana")] == ["bharata_ram"]
    assert [h[0] for h in gz.find("Bharata ruled.", work="mahabharata")] == ["bharata_king"]
    assert gz.find("Bharata ruled.", work="upanishads") == []
    # In a question there is no work, so the form is ambiguous and links to neither.
    assert gz.entities_in("Who was Bharata?") == []


def test_gazetteer_repeat_within_one_entity_keeps_wider_scope():
    gz = _gz({"id": "rama", "type": "Person", "label": "Rama", "aliases": ["rama"],
              "aliases_by_work": {"ramayana": ["rama"]}})
    assert [h[0] for h in gz.find("Rama spoke.", work="mahabharata")] == ["rama"]


def test_cli_reports_invalid_gazetteer(built):
    config.USER_GAZETTEER_FILE.write_text(yaml.safe_dump({"entities": [
        {"id": "a", "type": "Person", "label": "A", "aliases": ["x"]},
        {"id": "b", "type": "Person", "label": "B", "aliases": ["x"]}]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="Gazetteer error"):
        cli.main(["search", "anything"])


def test_fetch_verifies_pinned_checksum(tmp_path, monkeypatch):
    content = b"*** START OF THE PROJECT GUTENBERG EBOOK X ***\ntext\n"
    monkeypatch.setattr(sources.requests, "get", lambda url, timeout: FakeResponse(content))
    good = make_work("w")
    good.files = [{"gutenberg": 1, "sha256": hashlib.sha256(content).hexdigest()}]
    assert sources.fetch([good], raw_dir=tmp_path) == [tmp_path / "pg1.txt"]
    sources.fetch([good], raw_dir=tmp_path)  # an existing file is verified too
    (tmp_path / "pg1.txt").write_bytes(b"edited upstream")
    with pytest.raises(ValueError, match="does not match the sha256"):
        sources.fetch([good], raw_dir=tmp_path)
    bad = make_work("w")
    bad.files = [{"gutenberg": 2, "sha256": "0" * 64}]
    with pytest.raises(ValueError, match="downloaded pg2"):
        sources.fetch([bad], raw_dir=tmp_path)
    assert not (tmp_path / "pg2.txt").exists()


def test_bundled_sources_are_pinned():
    for work in sources.load_works():
        if work.bundled:
            assert all(len(f.get("sha256", "")) == 64 for f in work.files), work.id


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def test_bundled_gazetteer_is_valid():
    assert len(Gazetteer.load(config.package_file("gazetteer.yaml")).entities) > 0


def test_regressions():
    baseline = {"metrics": {"retrieval_hit_at_k": 0.958, "false_abstain_rate": 0.1}}
    assert regressions({"retrieval_hit_at_k": 0.958, "false_abstain_rate": 0.1}, baseline) == []
    assert regressions({"retrieval_hit_at_k": 1.0, "false_abstain_rate": 0.0}, baseline) == []
    assert regressions({"retrieval_hit_at_k": 0.9, "false_abstain_rate": 0.2}, baseline) == [
        "retrieval_hit_at_k: 0.9 (baseline 0.958)", "false_abstain_rate: 0.2 (baseline 0.1)"]
    assert regressions({}, baseline)[0] == "retrieval_hit_at_k: missing (baseline 0.958)"
    # Deleting eval items is a regression; a null pasted into the baseline is reported, not a crash.
    assert regressions({"n_retrieval": 23}, {"metrics": {"n_retrieval": 24}}) == ["n_retrieval: 23 (baseline 24)"]
    assert "is not a number" in regressions({"citation_precision": 0.5},
                                            {"metrics": {"citation_precision": None}})[0]


def test_committed_baseline_is_well_formed():
    baseline = json.loads((ONTOLOGY.parents[1] / "eval" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["k"] == 8
    assert set(baseline["metrics"]) >= {"n_retrieval", "n_adversarial", "retrieval_hit_at_1", "retrieval_hit_at_k"}
