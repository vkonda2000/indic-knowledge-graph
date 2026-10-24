"""Named curators and the provenance log (D12)."""

import json

import pytest

from ikg import cli, config, curators


@pytest.fixture(autouse=True)
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CURATORS_FILE", tmp_path / "curators.yaml")
    monkeypatch.setattr(config, "PROVENANCE_FILE", tmp_path / "provenance.jsonl")


def test_tokens_are_stored_only_as_hashes_and_identify_their_curator():
    token = curators.add_curator("lakshmi")
    assert token not in config.CURATORS_FILE.read_text(encoding="utf-8")
    assert curators.identify(token, admin_token="env-secret") == "lakshmi"
    assert curators.identify("env-secret", admin_token="env-secret") == "admin"
    assert curators.identify("wrong", admin_token="env-secret") is None
    assert curators.identify("pässword", admin_token=None) is None  # non-ASCII input doesn't crash


def test_names_are_validated_and_unique():
    curators.add_curator("ravi")
    for bad, message in [("ravi", "already exists"), ("Ravi K", "lowercase"), ("admin", "not one of"), ("", "lowercase"),
                         ("ravi2\n", "lowercase")]:
        with pytest.raises(ValueError, match=message):
            curators.add_curator(bad)


def test_removed_curator_can_no_longer_log_in():
    token = curators.add_curator("ravi")
    curators.remove_curator("ravi")
    assert curators.identify(token, admin_token=None) is None
    with pytest.raises(ValueError, match="no curator"):
        curators.remove_curator("ravi")


def test_log_is_appended_and_read_newest_first():
    curators.record("ravi", "work_status", "gita_notes", "approved")
    curators.record("lakshmi", "gazetteer_saved", "", "76 entities")
    with config.PROVENANCE_FILE.open("a", encoding="utf-8") as f:
        f.write("not json\n{}\n[1, 2]\n")  # damaged or foreign lines are skipped or blanked, never fatal
    entries = curators.recent()
    entries = [e for e in entries if e["action"]]
    assert [e["action"] for e in entries] == ["gazetteer_saved", "work_status"]
    assert entries[1] == {**entries[1], "curator": "ravi", "target": "gita_notes", "detail": "approved"}
    assert entries[0]["time"].endswith("+00:00")


def test_cli_adds_lists_removes_and_shows_the_log(capsys):
    cli.main(["curator", "add", "meera"])
    token = capsys.readouterr().out.split("not stored):")[1].strip()
    assert curators.identify(token, None) == "meera"
    cli.main(["curator", "list"])
    assert capsys.readouterr().out.startswith("meera")
    cli.main(["provenance"])
    assert "curator_added" in capsys.readouterr().out
    cli.main(["curator", "remove", "meera"])
    with pytest.raises(SystemExit, match="no curator"):
        cli.main(["curator", "remove", "meera"])
    lines = [json.loads(l) for l in config.PROVENANCE_FILE.read_text(encoding="utf-8").splitlines()]
    assert [l["action"] for l in lines] == ["curator_added", "curator_removed"]
