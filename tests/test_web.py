"""End-to-end tests for the web app on a temporary data directory (no network, no API key)."""

import time

import pytest
from conftest import make_work

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ikg import config, pipeline, sources  # noqa: E402
from ikg.segment import parse_generic  # noqa: E402

MATERIAL = b"""# Book One: The Dialogue

## Chapter 1

Arjuna asked Krishna about dharma on the field of Kurukshetra.

Krishna answered that duty must be performed without attachment.

## Chapter 2

Bhishma spoke to Yudhishthira about the duties of a king.
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name, rel in [("RAW_DIR", "raw"), ("PROCESSED_DIR", "processed"), ("UPLOADS_DIR", "uploads"),
                      ("USER_WORKS_FILE", "works.yaml"), ("USER_GAZETTEER_FILE", "gazetteer.yaml"),
                      ("PASSAGES_FILE", "processed/passages.jsonl"), ("MENTIONS_FILE", "processed/mentions.jsonl"),
                      ("INDEX_FILE", "processed/bm25.pkl"), ("GRAPH_FILE", "processed/g.graphml"),
                      ("RDF_FILE", "processed/ikg.ttl")]:
        monkeypatch.setattr(config, name, tmp_path / rel)
    # Build only user-added works, so the test needs no Gutenberg downloads.
    monkeypatch.setattr(pipeline, "load_works", lambda: [w for w in sources.load_works() if not w.bundled])
    monkeypatch.setenv("IKG_ADMIN_TOKEN", "secret")
    from ikg.web.app import create_app
    app = create_app()
    with TestClient(app) as c:
        c.app_ref = app
        yield c


def login(client):
    r = client.post("/admin/login", data={"token": "secret"}, follow_redirects=False)
    assert r.status_code == 303


def wait_for_build(client, timeout=30):
    job = client.app_ref.state.job
    deadline = time.time() + timeout
    while job.state == "running" and time.time() < deadline:
        time.sleep(0.1)
    assert job.state == "done", job.snapshot()


def test_parse_generic_markdown_headings():
    ps = parse_generic(MATERIAL.decode(), make_work("talk", "generic"), "upload:talk.md")
    assert [p.section_label for p in ps] == ["Chapter 1", "Chapter 2"]
    assert ps[0].book_label == "Book One: The Dialogue"
    assert ps[0].id == "talk.f1.b1.s001.p01"


def test_admin_requires_login(client):
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    assert "Invalid token" in client.post("/admin/login", data={"token": "wrong"}).text
    assert client.get("/api/build/status", follow_redirects=False).status_code == 303


def test_contribute_review_rebuild_search_graph(client):
    assert "not been built" in client.get("/").text

    r = client.post("/contribute", data={"title": "Gita notes", "author": "A. Scholar", "kind": "secondary",
                                         "contributor": "someone@example.org"},
                    files={"file": ("notes.md", MATERIAL, "text/markdown")})
    assert "waiting for admin review" in r.text
    assert sources.load_works() == [w for w in sources.load_works() if w.bundled]  # pending is not built

    login(client)
    page = client.get("/admin").text
    assert "Gita notes" in page and "Submissions awaiting review (1)" in page
    client.post("/admin/works/gita_notes/status", data={"status": "approved"})
    client.post("/admin/rebuild")
    wait_for_build(client)

    r = client.get("/", params={"q": "What did Krishna say about dharma?"})
    assert "gita_notes.f1.b1.s001.p01" in r.text
    assert "[secondary]" in r.text  # scholarship is labelled as such

    data = client.get("/api/search", params={"q": "duties of a king"}).json()
    assert data["results"][0]["id"] == "gita_notes.f1.b1.s002.p01"

    graph = client.get("/api/graph", params={"min_weight": 1}).json()
    ids = {n["data"]["id"] for n in graph["nodes"]}
    assert {"arjuna", "krishna", "dharma"} <= ids
    assert any(e["data"]["source"] in ("arjuna", "krishna") for e in graph["edges"])

    ent = client.get("/api/entity/dharma").json()
    assert {"surface": "duty", "via": "rendering", "count": 1} in ent["surface_forms"]
    assert client.get("/passage/gita_notes.f1.b1.s001.p01").status_code == 200
    assert client.get("/graph").status_code == 200
    assert client.get("/entity/krishna").status_code == 200


def test_upload_validation_and_escaping(client):
    r = client.post("/contribute", data={"title": "bad"}, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert "unsupported file type" in r.text
    client.post("/contribute", data={"title": "<script>alert(1)</script>"},
                files={"file": ("a.txt", b"some text", "text/plain")})
    login(client)
    page = client.get("/admin").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_gazetteer_editor_validates(client):
    login(client)
    assert "Gazetteer not saved" in client.post("/admin/gazetteer", data={"yaml_text": "entities: 5"}).text
    good = "entities:\n  - {id: x, type: Concept, label: X, aliases: [xyz]}\n"
    assert "Gazetteer saved (1 entities)" in client.post("/admin/gazetteer", data={"yaml_text": good}).text
    assert config.USER_GAZETTEER_FILE.read_text() == good
