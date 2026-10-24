"""End-to-end tests for the web app on a temporary data directory (no network, no API key)."""

import time

import pytest
from conftest import make_work

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ikg import config, pipeline, screen, sources  # noqa: E402
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
                      ("RDF_FILE", "processed/ikg.ttl"), ("MANIFEST_FILE", "processed/manifest.json"),
                      ("GAZETTEER_SNAPSHOT_FILE", "processed/gazetteer.yaml"), ("BUILD_LOCK_FILE", "processed/.build.lock")]:
        monkeypatch.setattr(config, name, tmp_path / rel)
    # Build only user-added works, so the test needs no Gutenberg downloads.
    monkeypatch.setattr(pipeline, "load_works", lambda: [w for w in sources.load_works() if not w.bundled])
    monkeypatch.setenv("IKG_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(config, "SCREEN_ENABLED", True)
    monkeypatch.setattr(screen, "_call_model", fake_screen_model)
    from ikg.web.app import create_app
    app = create_app()
    with TestClient(app) as c:
        c.app_ref = app
        yield c


def fake_screen_model(system, user):
    """Stands in for the local model: anything about widgets is off-topic."""
    if "widget" in user.lower():
        return {"related": False, "reason": "It is a business report about widgets."}
    return {"related": True, "reason": "It discusses Krishna and dharma."}


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
    assert client.get("/api/build/status", follow_redirects=False).status_code == 401


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
                files={"file": ("a.txt", MATERIAL, "text/plain")})
    login(client)
    page = client.get("/admin").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_gazetteer_editor_validates(client):
    login(client)
    assert "Gazetteer not saved" in client.post("/admin/gazetteer", data={"yaml_text": "entities: 5"}).text
    good = "entities:\n  - {id: x, type: Concept, label: X, aliases: [xyz]}\n"
    assert "Gazetteer saved (1 entities)" in client.post("/admin/gazetteer", data={"yaml_text": good}).text
    assert config.USER_GAZETTEER_FILE.read_text() == good


def build_sample(client):
    """Approve and build the sample material so the corpus endpoints are available."""
    login(client)
    client.post("/admin/upload", data={"title": "Gita notes", "kind": "secondary"},
                files={"file": ("notes.md", MATERIAL, "text/markdown")})
    client.post("/admin/rebuild")
    wait_for_build(client)
    client.post("/admin/logout")


def fake_ask(question, index, gazetteer, **kwargs):
    from ikg.qa import Answer
    fake_ask.calls.append(kwargs)
    return Answer(question=question, text="An answer.")


def test_ask_is_rate_limited_and_validated(client, monkeypatch):
    from ikg import qa
    fake_ask.calls = []
    monkeypatch.setattr(qa, "ask", fake_ask)
    build_sample(client)
    client.app_ref.state.ask_limiter.per_hour = 2

    assert client.post("/api/ask", content=b"not json").status_code == 400
    assert client.post("/api/ask", json={"question": ""}).status_code == 400
    assert client.post("/api/ask", json={"question": "x", "k": "abc"}).status_code == 400
    assert client.post("/api/ask", json={"question": "x", "works": "ramayana"}).status_code == 400
    assert fake_ask.calls == []  # invalid requests do not reach the model

    assert client.post("/api/ask", json={"question": "x", "primary_only": "false"}).status_code == 400
    assert fake_ask.calls == []  # invalid requests do not reach the model

    assert client.post("/api/ask", json={"question": "dharma?", "k": 500}).json()["answer"] == "An answer."
    assert fake_ask.calls[-1]["k"] == 20  # capped
    client.post("/", data={"q": "What is dharma?", "mode": "ask", "k": "100000"})
    assert fake_ask.calls[-1]["k"] == 20
    assert client.post("/api/ask", json={"question": "karma?"}).status_code == 429
    assert "Question limit reached" in client.post("/", data={"q": "yoga?", "mode": "ask"}).text
    assert len(fake_ask.calls) == 2

    login(client)  # admins are not limited
    assert client.post("/api/ask", json={"question": "karma?"}).status_code == 200


def test_ask_never_runs_on_get(client, monkeypatch):
    from ikg import qa
    fake_ask.calls = []
    monkeypatch.setattr(qa, "ask", fake_ask)
    build_sample(client)
    page = client.get("/", params={"q": "dharma?", "mode": "ask"}).text
    assert "Press Go to get a cited answer" in page
    assert fake_ask.calls == []  # a link, preview or crawler costs nothing
    assert "An answer." in client.post("/", data={"q": "dharma?", "mode": "ask"}).text


def test_daily_budget_stops_ask_for_everyone(client, monkeypatch):
    from ikg import qa

    def costly(question, index, gazetteer, **kwargs):
        return qa.Answer(question=question, text="An answer.", cost_usd=0.6)

    monkeypatch.setattr(qa, "ask", costly)
    build_sample(client)
    client.app_ref.state.budget.limit = 1.0
    login(client)  # the budget applies to admins too
    assert client.post("/api/ask", json={"question": "dharma?"}).status_code == 200
    assert client.post("/api/ask", json={"question": "karma?"}).status_code == 200  # 1.2 spent
    r = client.post("/api/ask", json={"question": "yoga?"})
    assert r.status_code == 429 and "daily budget" in r.json()["detail"]
    assert "daily budget" in client.post("/", data={"q": "moksha?", "mode": "ask"}).text
    # A question already answered is served from the cache, at no cost, even over budget.
    assert client.post("/api/ask", json={"question": "dharma?"}).json()["cached"] is True
    assert "Krishna" in client.post("/", data={"q": "Krishna", "mode": "search"}).text  # search still works


def test_non_ascii_token_is_rejected_not_crashed(client):
    r = client.post("/admin/login", data={"token": "pässword"})
    assert r.status_code == 200 and "Invalid token" in r.text


def test_ask_failure_returns_503(client, monkeypatch):
    from ikg import qa

    def broken(*args, **kwargs):
        raise RuntimeError("no API key")

    monkeypatch.setattr(qa, "ask", broken)
    build_sample(client)
    r = client.post("/api/ask", json={"question": "dharma?"})
    assert r.status_code == 503 and "no API key" in r.json()["detail"]


def test_oversized_upload_rejected(client, monkeypatch):
    monkeypatch.setattr(sources, "MAX_UPLOAD_BYTES", 1024)
    r = client.post("/contribute", data={"title": "big"}, files={"file": ("big.txt", b"a" * 5000, "text/plain")})
    assert "larger than" in r.text
    assert not config.UPLOADS_DIR.exists() or not any(config.UPLOADS_DIR.iterdir())


WIDGETS = b"Quarterly widget sales rose five percent across all regions this year. " * 10


def test_unrelated_upload_rejected_and_not_stored(client):
    r = client.post("/contribute", data={"title": "Sales report"},
                    files={"file": ("report.txt", WIDGETS, "text/plain")})
    assert "does not appear to be about Indic texts" in r.text and "widgets" in r.text
    assert not config.UPLOADS_DIR.exists() or not any(config.UPLOADS_DIR.iterdir())
    assert not config.USER_WORKS_FILE.exists()

    login(client)
    r = client.post("/admin/upload", data={"title": "Sales report"},
                    files={"file": ("report.txt", WIDGETS, "text/plain")})
    assert "does not appear to be about Indic texts" in r.text


def test_related_upload_records_verdict(client):
    client.post("/contribute", data={"title": "Gita notes"}, files={"file": ("notes.md", MATERIAL, "text/markdown")})
    work = sources.get_work("gita_notes")
    assert work.status == "pending" and "Krishna and dharma" in work.screening
    login(client)
    assert "Screened by" in client.get("/admin").text


def test_short_or_unscreenable_upload_rejected(client, monkeypatch):
    r = client.post("/contribute", data={"title": "tiny"}, files={"file": ("a.txt", b"Om.", "text/plain")})
    assert "too little readable text" in r.text

    def down(system, user):
        raise screen.ScreeningUnavailable("connection refused")

    monkeypatch.setattr(screen, "_call_model", down)
    r = client.post("/contribute", data={"title": "Gita notes"}, files={"file": ("notes.md", MATERIAL, "text/markdown")})
    assert "could not be checked" in r.text
    assert not config.USER_WORKS_FILE.exists()

    monkeypatch.setattr(config, "SCREEN_ENABLED", False)  # IKG_SCREEN=off
    r = client.post("/contribute", data={"title": "Gita notes"}, files={"file": ("notes.md", MATERIAL, "text/markdown")})
    assert "waiting for admin review" in r.text


def test_repeated_question_is_served_from_cache(client, monkeypatch):
    from ikg import qa
    fake_ask.calls = []
    monkeypatch.setattr(qa, "ask", fake_ask)
    build_sample(client)
    first = client.post("/api/ask", json={"question": "What is dharma?"}).json()
    again = client.post("/api/ask", json={"question": "  what IS   dharma? "}).json()
    assert first["cached"] is False and again["cached"] is True and again["answer"] == first["answer"]
    assert len(fake_ask.calls) == 1
    assert "served from cache" in client.post("/", data={"q": "What is dharma?", "mode": "ask"}).text
    # Different settings are a different question.
    client.post("/api/ask", json={"question": "What is dharma?", "primary_only": True})
    assert len(fake_ask.calls) == 2
    # A rebuild clears the cache: answers cite passages of the build they came from.
    build_sample(client)
    client.post("/api/ask", json={"question": "What is dharma?"})
    assert len(fake_ask.calls) == 3


def test_free_and_unbilled_answers_do_not_use_the_budget(client, monkeypatch):
    from ikg import qa
    build_sample(client)
    client.app_ref.state.budget.limit = 1.0
    # No passages match: the answer abstains without an API call and costs nothing.
    for i in range(10):
        assert client.post("/api/ask", json={"question": f"zzzz qqqq {i}"}).json()["abstained"]
    assert client.app_ref.state.budget.spent == 0.0

    def rejected(*args, **kwargs):
        raise ValueError("effort must be one of ...")

    monkeypatch.setattr(qa, "ask", rejected)
    assert client.post("/api/ask", json={"question": "dharma?"}).status_code == 503
    assert client.app_ref.state.budget.spent == 0.0


def test_cross_site_ask_is_refused(client, monkeypatch):
    from ikg import qa
    fake_ask.calls = []
    monkeypatch.setattr(qa, "ask", fake_ask)
    build_sample(client)
    r = client.post("/api/ask", json={"question": "dharma?"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 429 and "from this site" in r.json()["detail"]
    page = client.post("/", data={"q": "dharma?", "mode": "ask"}, headers={"Origin": "https://evil.example"})
    assert "from this site" in page.text
    assert fake_ask.calls == []
    assert client.post("/api/ask", json={"question": "dharma?"},
                       headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_refusals_and_answers_from_an_old_build_are_not_cached(client, monkeypatch):
    from ikg import qa
    calls = []

    def refusing(question, index, gazetteer, **kwargs):
        calls.append(question)
        return qa.Answer(question=question, text="", refused=True, cost_usd=0.001)

    monkeypatch.setattr(qa, "ask", refusing)
    build_sample(client)
    client.post("/api/ask", json={"question": "dharma?"})
    client.post("/api/ask", json={"question": "dharma?"})
    assert len(calls) == 2  # a refusal is retried, not pinned

    def slow(question, index, gazetteer, **kwargs):
        calls.append(question)
        build_sample(client)  # a rebuild finishes while this answer is being written
        return qa.Answer(question=question, text="Old answer.", cost_usd=0.001)

    monkeypatch.setattr(qa, "ask", slow)
    client.post("/api/ask", json={"question": "karma?"})
    assert client.app_ref.state.answer_cache.items == {}


def test_long_question_is_truncated(client, monkeypatch):
    from ikg import qa
    fake_ask.calls = []
    seen = []
    monkeypatch.setattr(qa, "ask", lambda q, *a, **kw: seen.append(q) or qa.Answer(question=q, text="A."))
    build_sample(client)
    client.post("/", data={"q": "dharma " * 1000, "mode": "ask"})
    assert len(seen[0]) == 2000


@pytest.mark.parametrize("text", [
    "works:\n  - {id: x, title: T, translator: X, year: '1', parser: generic, work: nowhere}\n",
    "works:\n  - {id: x, title: T, translator: X, year: '1', parser: generic, colour: red}\n",  # unknown field
    "works:\n  - {id: x, translator: X, year: '1', parser: generic}\n",                        # missing title
    "works:\n  - {id: x\n",                                                                    # bad YAML
    "works:\n  x: {}\n",                                                                       # not a list
])
def test_broken_registry_shows_a_notice_not_a_500(client, text):
    config.USER_WORKS_FILE.write_text(text, encoding="utf-8")
    for path in ("/", "/contribute"):
        r = client.get(path)
        assert r.status_code == 200 and "The work registry is invalid" in r.text
    login(client)
    assert "The work registry is invalid" in client.get("/admin").text
    assert client.post("/admin/gutenberg", data={"gutenberg_id": "1", "title": "T"}).status_code == 200


def test_failed_rebuild_leaves_the_previous_build_intact(client, monkeypatch):
    build_sample(client)
    before = config.PASSAGES_FILE.read_bytes()

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "save_graph", broken)  # fails after passages and mentions were written
    login(client)
    client.post("/admin/rebuild")
    job = client.app_ref.state.job
    deadline = time.time() + 30
    while job.state == "running" and time.time() < deadline:
        time.sleep(0.1)
    assert job.state == "error" and "disk full" in job.error
    assert config.PASSAGES_FILE.read_bytes() == before
    assert not list(config.PASSAGES_FILE.parent.glob("*.tmp"))
    pipeline.Runtime.load()  # the old build still verifies against its manifest
    assert not config.BUILD_LOCK_FILE.exists()
    assert "Arjuna" in client.get("/api/search", params={"q": "Arjuna"}).text


def _rebuild_and_wait(client):
    login(client)
    client.post("/admin/rebuild")
    job = client.app_ref.state.job
    deadline = time.time() + 30
    while job.state == "running" and time.time() < deadline:
        time.sleep(0.1)
    return job


def test_swap_failure_is_reported_and_the_site_keeps_serving(client, monkeypatch):
    build_sample(client)
    real, calls = pipeline._replace, []

    def flaky(src, dst, attempts=10):
        calls.append(dst)
        if len(calls) == 3:  # fails part-way through swapping the artefacts in
            raise PermissionError("file in use")
        real(src, dst, attempts)

    monkeypatch.setattr(pipeline, "_replace", flaky)
    # Change the input, so the new artefacts differ (an identical rebuild swaps in identical files).
    bundled = config.package_file("gazetteer.yaml").read_text(encoding="utf-8")
    config.USER_GAZETTEER_FILE.write_text(
        bundled + "  - {id: kurukshetra_field, type: Place, label: The field, aliases: [field]}\n", encoding="utf-8")
    job = _rebuild_and_wait(client)
    assert job.state == "error" and "run the build again" in job.error
    with pytest.raises(ValueError, match="does not match the build manifest"):
        pipeline.Runtime.verify()  # the mixed files on disk are refused, not served
    assert not list(config.PASSAGES_FILE.parent.glob("*.tmp"))
    assert "Arjuna" in client.get("/api/search", params={"q": "Arjuna"}).text  # the loaded build still serves
    monkeypatch.setattr(pipeline, "_replace", real)
    assert _rebuild_and_wait(client).state == "done"  # and a rebuild recovers
    pipeline.Runtime.verify()


def test_a_build_that_cannot_be_loaded_keeps_the_previous_one_serving(client, monkeypatch):
    build_sample(client)

    def refuse(cls):
        raise ValueError("cannot load")

    monkeypatch.setattr(pipeline.Runtime, "load", classmethod(refuse))
    job = _rebuild_and_wait(client)
    assert job.state == "error" and "still serving the previous one" in job.error
    assert "Arjuna" in client.get("/api/search", params={"q": "Arjuna"}).text


def test_uploads_are_rate_limited_per_client(client):
    client.app_ref.state.upload_limiter.per_hour = 2
    for i in range(2):
        r = client.post("/contribute", data={"title": f"Notes {i}"}, files={"file": ("n.md", MATERIAL, "text/markdown")})
        assert "waiting for admin review" in r.text
    r = client.post("/contribute", data={"title": "Notes 3"}, files={"file": ("n.md", MATERIAL, "text/markdown")})
    assert "Too many uploads" in r.text
    login(client)  # admins are not limited
    r = client.post("/contribute", data={"title": "Notes 4"}, files={"file": ("n.md", MATERIAL, "text/markdown")})
    assert "waiting for admin review" in r.text


def test_failed_registration_leaves_no_orphan_upload(client, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("registry is broken")

    monkeypatch.setattr(sources, "add_user_work", broken)
    r = client.post("/contribute", data={"title": "Notes"}, files={"file": ("n.md", MATERIAL, "text/markdown")})
    assert "registry is broken" in r.text
    assert not config.UPLOADS_DIR.exists() or not any(config.UPLOADS_DIR.iterdir())


def test_api_routes_answer_401_not_a_redirect(client):
    r = client.get("/api/build/status", follow_redirects=False)
    assert r.status_code == 401 and r.json()["detail"] == "Admin login required"


def test_upload_limits_apply_before_the_body_is_read(client):
    client.app_ref.state.upload_global.per_hour = 1
    ok = client.post("/contribute", data={"title": "A"}, files={"file": ("n.md", MATERIAL, "text/markdown")})
    assert "waiting for admin review" in ok.text
    r = client.post("/contribute", data={"title": "B"}, files={"file": ("n.md", MATERIAL, "text/markdown")})
    assert r.status_code == 429 and "Too many uploads" in r.text
    # A refused request doesn't burn the client's own quota.
    assert len(client.app_ref.state.upload_limiter.events.get("testclient", [])) == 1
    r = client.post("/contribute", content=b"x", headers={"content-length": str(50 << 20),
                                                          "content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413 and "larger than 20 MB" in r.text


def test_upload_disk_error_leaves_no_orphan(client, monkeypatch):
    def full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(sources, "add_user_work", full)
    r = client.post("/contribute", data={"title": "Notes"}, files={"file": ("n.md", MATERIAL, "text/markdown")})
    assert "could not be saved" in r.text
    assert not config.UPLOADS_DIR.exists() or not any(config.UPLOADS_DIR.iterdir())
