"""Paths and constants. Override the data directory with the IKG_DATA environment variable."""

import os
from importlib import resources
from pathlib import Path

DATA_DIR = Path(os.environ.get("IKG_DATA", "data"))
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
UPLOADS_DIR = DATA_DIR / "uploads"
USER_WORKS_FILE = DATA_DIR / "works.yaml"          # works added through the web admin
USER_GAZETTEER_FILE = DATA_DIR / "gazetteer.yaml"  # admin-edited gazetteer (overrides the bundled one)

PASSAGES_FILE = PROCESSED_DIR / "passages.jsonl"
MENTIONS_FILE = PROCESSED_DIR / "mentions.jsonl"
INDEX_FILE = PROCESSED_DIR / "bm25.pkl"
GRAPH_FILE = PROCESSED_DIR / "cooccurrence.graphml"
RDF_FILE = PROCESSED_DIR / "ikg.ttl"

REPORTS_DIR = Path("reports")
EVAL_FILE = Path("eval") / "questions.jsonl"
EVAL_RESULTS_DIR = Path("eval") / "results"

MODEL = "claude-opus-5-5"

# Local model (Ollama) that screens uploads for relevance; see ikg.screen.
SCREEN_ENABLED = os.environ.get("IKG_SCREEN", "on").lower() not in ("off", "0", "false", "no")
SCREEN_MODEL = os.environ.get("IKG_SCREEN_MODEL", "qwen3.5:9B")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
SCREEN_TIMEOUT = float(os.environ.get("IKG_SCREEN_TIMEOUT", "180"))  # first call loads the model


def package_file(name: str) -> Path:
    """Path to a bundled data file (sources.yaml, gazetteer.yaml)."""
    return Path(str(resources.files("ikg") / "data" / name))


def gazetteer_file() -> Path:
    """The admin-edited gazetteer if one exists, else the bundled seed gazetteer."""
    return USER_GAZETTEER_FILE if USER_GAZETTEER_FILE.exists() else package_file("gazetteer.yaml")
