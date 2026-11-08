"""The Claude plugin (docs/08 B2) stays consistent with the MCP server it packages."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "integrations" / "claude" / "ikg"
SKILL = PLUGIN / "skills" / "ikg-scholar"


def skill_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(SKILL.rglob("*.md")))


def test_skill_frontmatter_names_and_describes_it():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    front = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert front, "SKILL.md must start with YAML frontmatter"
    fields = dict(line.split(": ", 1) for line in front.group(1).splitlines())
    assert fields["name"] == "ikg-scholar"
    assert "Upanishads" in fields["description"] and "ikg" in fields["description"]
    assert len(fields["description"]) <= 1024


def test_skill_links_resolve():
    for md in SKILL.rglob("*.md"):
        for target in re.findall(r"\]\(([^)#:]+)\)", md.read_text(encoding="utf-8")):
            assert (md.parent / target).exists(), f"{md.name} links to missing {target}"


def test_plugin_config_uses_only_declared_options():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    mcp = (PLUGIN / ".mcp.json").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{user_config\.(\w+)\}", mcp))
    assert used and used <= manifest["userConfig"].keys()
    assert manifest["userConfig"]["mcp_token"]["sensitive"] is True  # stored in the OS keychain, not settings
    assert json.loads(mcp)["mcpServers"]["ikg"]["type"] == "http"
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    entry = marketplace["plugins"][0]
    assert entry["name"] == manifest["name"] and (ROOT / entry["source"]).resolve() == PLUGIN.resolve()


def test_every_tool_the_skill_names_exists():
    pytest.importorskip("mcp")
    from ikg.mcp_server import READ_ONLY  # noqa: F401  (the module imports only with the SDK)
    source = (ROOT / "src" / "ikg" / "mcp_server.py").read_text(encoding="utf-8")
    served = set(re.findall(r'name="(ikg_\w+)"', source))
    named = set(re.findall(r"\bikg_[a-z_]+\b", skill_text())) - {"ikg_mcp"}
    assert named and named <= served, f"the skill names tools the server doesn't have: {named - served}"
    assert {"ikg_search_passages", "ikg_get_passage", "ikg_get_entity", "ikg_sparql_query", "ikg_ask"} <= named


def _built_passages():
    path = ROOT / "data" / "processed" / "passages.jsonl"
    if not path.exists():
        pytest.skip("needs a built corpus (ikg build); CI's eval-gate job runs this after building")
    return {json.loads(line)["id"]: json.loads(line)["text"] for line in path.open(encoding="utf-8")}


def test_every_passage_id_the_skill_and_guide_cite_exists():
    passages = _built_passages()
    docs = skill_text() + (ROOT / "integrations" / "README.md").read_text(encoding="utf-8")
    cited = set(re.findall(r"`((?:upa|mbh|ram|vp)\.[a-z0-9.]+)`", docs))
    assert cited
    assert not cited - passages.keys(), f"cited passage ids that don't exist: {sorted(cited - passages.keys())}"


def test_quotations_in_the_model_answer_are_verbatim():
    passages = _built_passages()
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    block = "\n".join(line[1:].strip() for line in text.splitlines() if line.startswith(">"))
    ids = re.findall(r"`((?:upa|mbh|ram|vp)\.[a-z0-9.]+)`", block)
    quotes = re.findall(r'"([^"]{8,})"', " ".join(block.split()))
    assert ids and quotes

    def norm(s):
        return " ".join(s.lower().split())
    for q in quotes:
        assert any(norm(q) in norm(passages[i]) for i in ids if i in passages), f"not verbatim in a cited passage: {q!r}"
