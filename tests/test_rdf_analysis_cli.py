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
from ikg.pipeline import artefacts, write_manifest
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
    # Renderings are per edition, as that translation uses them, with a count.
    rendering = DATA["rendering/mahabharata/dharma/righteousness"]
    assert (rendering, IKG.renders, dharma) in rdf
    assert (rendering, IKG.surface, Literal("righteousness")) in rdf
    assert (rendering, IKG.fromEdition, DATA["edition/mahabharata"]) in rdf
    assert int(rdf.value(rendering, IKG["count"])) == 1
    assert (DATA["rendering/mahabharata/dharma/duty"], None, None) not in rdf  # listed, never used
    assert (DATA["entity/arjuna"], IKG.coOccursWith, DATA["entity/krishna"]) in rdf
    # Mentions are aggregated per entity, book and match type.
    rows = rdf.query("""SELECT ?via ?n WHERE { ?m ikg:entity ?e ; ikg:via ?via ; ikg:count ?n }""",
                     initNs={"ikg": IKG}, initBindings={"e": dharma})
    assert sorted((str(v), int(n)) for v, n in rows) == [("alias", 1), ("rendering", 1)]


def test_two_translations_of_one_text_stay_apart(gazetteer):
    """D10: two editions of the same work share the work, but books, mentions and renderings
    are per edition, so translators can be compared."""
    a = make_passage("mbh.b01.s001.p01", "mahabharata", "Dharma is righteousness.")
    b = make_passage("mbhd.b01.s001.p01", "mahabharata", "Dharma is duty.")
    a.edition, b.edition = "ganguli-1883", "dutt-1895"
    ganguli, dutt = make_work("mahabharata"), make_work("mahabharata-dutt")
    ganguli.edition, dutt.edition, dutt.work = "ganguli-1883", "dutt-1895", "mahabharata"
    mentions = extract([a, b], gazetteer)
    rdf = to_rdf([ganguli, dutt], [a, b], mentions, gazetteer, build_cooccurrence(mentions, gazetteer))
    work = DATA["work/mahabharata"]
    assert set(rdf.subjects(IKG.editionOf, work)) == {DATA["edition/ganguli-1883"], DATA["edition/dutt-1895"]}
    assert (DATA["book/ganguli-1883/1"], IKG.partOf, work) in rdf
    assert (DATA["book/dutt-1895/1"], IKG.partOf, work) in rdf
    assert (DATA["rendering/ganguli-1883/dharma/righteousness"], IKG.fromEdition, DATA["edition/ganguli-1883"]) in rdf
    assert (DATA["rendering/dutt-1895/dharma/duty"], IKG.fromEdition, DATA["edition/dutt-1895"]) in rdf
    assert (DATA["rendering/dutt-1895/dharma/righteousness"], None, None) not in rdf


def test_registry_rejects_duplicate_edition_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_WORKS_FILE", tmp_path / "works.yaml")
    (tmp_path / "works.yaml").write_text(yaml.safe_dump({"works": [
        {"id": "other", "title": "T", "translator": "X", "year": "1900", "parser": "generic",
         "edition": "ganguli-1883"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="edition ids must be unique.*ganguli-1883"):
        sources.load_works()


def test_bundled_editions_and_passage_ids_scheme():
    from ikg.segment import ID_SCHEME, MAX_CHARS
    # Frozen for v1 passage IDs (D11): changing either renumbers IDs and needs a migration map.
    assert (ID_SCHEME, MAX_CHARS) == ("v1", 1500)
    eds = {w.id: (w.work_id, w.edition_id) for w in sources.load_works() if w.bundled}
    assert eds["mahabharata"] == ("mahabharata", "ganguli-1883")


def test_rdf_passages_only_on_request(gazetteer):
    passage = DATA["passage/ram.b01.s001.p01"]
    assert (passage, None, None) not in _export(gazetteer)
    rdf = _export(gazetteer, with_passages=True)
    assert (passage, RDF.type, IKG.Verse) in rdf
    assert (passage, IKG.partOf, DATA["book/ramayana/1"]) in rdf  # edition defaults to the work id here


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
                      ("GRAPH_FILE", "g.graphml"), ("GAZETTEER_SNAPSHOT_FILE", "snapshot.yaml"),
                      ("MANIFEST_FILE", "manifest.json"), ("RDF_FILE", "ikg.ttl")]:
        monkeypatch.setattr(config, name, tmp_path / rel)
    entities = [{k: v for k, v in vars(e).items() if v} for e in gazetteer.entities.values()]
    text = yaml.safe_dump({"entities": entities})
    config.USER_GAZETTEER_FILE.write_text(text, encoding="utf-8")
    config.GAZETTEER_SNAPSHOT_FILE.write_text(text, encoding="utf-8")
    mentions = extract(PASSAGES, gazetteer)
    write_passages(PASSAGES, config.PASSAGES_FILE)
    write_mentions(mentions, config.MENTIONS_FILE)
    BM25Index(PASSAGES).save(config.INDEX_FILE)
    save_graph(build_cooccurrence(mentions, gazetteer), config.GRAPH_FILE)
    files = {k: p for k, p in artefacts().items() if k != "rdf"}  # this fixture writes no RDF
    write_manifest([], PASSAGES, files)


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
    with pytest.raises(SystemExit, match="No complete built corpus"):
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


def test_runtime_uses_the_gazetteer_the_build_used(built, capsys):
    """Editing (even breaking) the gazetteer after a build changes nothing until the next build (A1)."""
    config.USER_GAZETTEER_FILE.write_text(yaml.safe_dump({"entities": [
        {"id": "a", "type": "Person", "label": "A", "aliases": ["x"]},
        {"id": "b", "type": "Person", "label": "B", "aliases": ["x"]}]}), encoding="utf-8")
    cli.main(["search", "Sita forest", "-k", "1"])
    assert "ram.b01.s001.p01" in capsys.readouterr().out
    from ikg.pipeline import Runtime
    assert "sita" in Runtime.load().gazetteer.entities


def test_runtime_refuses_artefacts_that_do_not_match_the_manifest(built):
    with config.MENTIONS_FILE.open("a", encoding="utf-8") as f:
        f.write('{"entity": "sita", "passage": "x", "work": "w", "surface": "Sita", "via": "alias"}\n')
    with pytest.raises(SystemExit, match="mentions.jsonl does not match the build manifest"):
        cli.main(["search", "anything"])
    config.MANIFEST_FILE.write_text('{"id_scheme": "v1"}', encoding="utf-8")  # an older build: no checksums
    with pytest.raises(SystemExit, match="older version"):
        cli.main(["search", "anything"])


def test_build_lock(tmp_path, monkeypatch):
    import os
    from ikg.pipeline import BuildError, build_lock
    monkeypatch.setattr(config, "BUILD_LOCK_FILE", tmp_path / ".build.lock")
    with build_lock():
        assert config.BUILD_LOCK_FILE.exists()
        with pytest.raises(BuildError, match="another build is running"):
            with build_lock():
                pass
    assert not config.BUILD_LOCK_FILE.exists()
    config.BUILD_LOCK_FILE.write_text("1234\n")
    os.utime(config.BUILD_LOCK_FILE, (0, 0))  # left by a crash long ago
    with build_lock():
        pass


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


def test_build_manifest_records_id_scheme_and_editions(tmp_path, monkeypatch):
    from ikg.pipeline import write_manifest
    monkeypatch.setattr(config, "MANIFEST_FILE", tmp_path / "manifest.json")
    work = make_work("mahabharata")
    work.edition, work.files = "ganguli-1883", [{"gutenberg": 1, "sha256": "ab" * 32}]
    p = make_passage("mbh.b01.s001.p01", "mahabharata", "text")
    p.edition = "ganguli-1883"
    write_manifest([work], [p], files={})
    m = json.loads(config.MANIFEST_FILE.read_text(encoding="utf-8"))
    assert m["id_scheme"] == "v1" and m["chunking"]["max_chars"] == 1500 and m["passages"] == 1
    assert m["chunking"]["sentence_end"]  # the splitter pattern is recorded too
    assert m["editions"][0]["edition"] == "ganguli-1883" and m["editions"][0]["work"] == "mahabharata"
    assert m["editions"][0]["passages"] == 1 and m["editions"][0]["sources"][0]["sha256"] == "ab" * 32


def test_eval_reciprocal_rank(gazetteer):
    from ikg.evaluate import EvalItem, run
    index = BM25Index(PASSAGES)
    items = [EvalItem("a", "retrieval", "Sita forest", ["ram.b01"]),          # found first
             EvalItem("b", "retrieval", "Krishna dharma Self", ["mbh.b01.s001.p02"]),
             EvalItem("c", "retrieval", "Sita forest", ["vp.b9"])]           # never found
    result = run(items, index, gazetteer, None, k=3)
    rr = {r["id"]: r["reciprocal_rank"] for r in result["rows"]}
    assert rr["a"] == 1.0 and rr["c"] == 0.0 and 0 < rr["b"] <= 1.0
    assert result["summary"]["retrieval_mrr"] == round(sum(rr.values()) / 3, 3)


def test_eval_breaks_results_down_by_question_type(gazetteer):
    from ikg.evaluate import EvalItem, run
    items = [EvalItem("skt-1", "retrieval", "Sita forest", ["ram.b01"]),
             EvalItem("spk-1", "retrieval", "Sita forest", ["vp.b9"])]
    by_type = run(items, BM25Index(PASSAGES), gazetteer, None, k=3)["summary"]["retrieval_by_type"]
    assert by_type == {"skt": {"n": 1, "hit_at_1": 1.0, "hit_at_k": 1.0, "mrr": 1.0},
                       "spk": {"n": 1, "hit_at_1": 0.0, "hit_at_k": 0.0, "mrr": 0.0}}


@pytest.mark.parametrize("entity, message", [
    ({"id": "k", "type": "Person", "label": "K", "aliases": "krishna"}, "'aliases' must be a list"),
    ({"id": "k", "type": "Persn", "label": "K"}, "type must be one of"),
    ({"id": "k", "type": "Person", "label": ""}, "'label' must be a non-empty string"),
    ({"id": "k", "type": "Person", "label": "K", "works": "ramayana"}, "'works' must be a list"),
    ({"id": "k", "type": "Person", "label": "K", "aliases_by_work": {"ramayana": "rama"}}, "'aliases_by_work'"),
    ({"id": "k", "type": "Person", "label": "K", "renderings": ["duty", ""]}, "'renderings' must be a list"),
])
def test_gazetteer_rejects_malformed_fields(entity, message):
    with pytest.raises(ValueError, match=message):
        _gz(entity)
    with pytest.raises(ValueError, match="must be a mapping"):
        Gazetteer.from_yaml(yaml.safe_dump({"entities": ["krishna"]}))


def test_registry_rejects_unknown_work_and_duplicate_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_WORKS_FILE", tmp_path / "works.yaml")
    entry = {"title": "T", "translator": "X", "year": "1900", "parser": "generic"}

    def write(*entries):
        (tmp_path / "works.yaml").write_text(yaml.safe_dump({"works": list(entries)}), encoding="utf-8")

    write({**entry, "id": "dutt", "work": "mahabharta"})  # typo for mahabharata
    with pytest.raises(ValueError, match="'work' must name an existing work id: dutt -> mahabharta"):
        sources.load_works()
    write({**entry, "id": "dutt", "work": "mahabharata", "edition": "dutt-1895"})
    assert any(w.edition_id == "dutt-1895" for w in sources.load_works())
    write({**entry, "id": "ramayana", "edition": "other"})
    with pytest.raises(ValueError, match="registry ids must be unique.*ramayana"):
        sources.load_works()


def test_gazetteer_blank_optional_fields_mean_unset():
    gz = Gazetteer.from_yaml("entities:\n  - id: k\n    type: Person\n    label: K\n    iast:\n    works:\n")
    assert gz.entities["k"].iast == "" and gz.entities["k"].works == []
