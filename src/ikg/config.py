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
MANIFEST_FILE = PROCESSED_DIR / "manifest.json"  # what the build was made from (D11)

REPORTS_DIR = Path("reports")
EVAL_FILE = Path("eval") / "questions.jsonl"
EVAL_RESULTS_DIR = Path("eval") / "results"

# Claude model and effort for cited answers (Ask, `ikg ask`, `ikg eval --llm`). This is the only
# paid API call; search and upload screening never call it. Haiku 5.5 at low effort matched or
# beat Opus 5.5 at high effort on every answer metric of the eval set at 1/44 of the cost
# (docs/06 §1). Compare candidates with `ikg eval --llm --model ... --effort ...` before changing it.
MODEL = os.environ.get("IKG_ANSWER_MODEL", "claude-haiku-5-5")
EFFORT = os.environ.get("IKG_ANSWER_EFFORT", "low")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# USD per million (input, output) tokens, first-party API, for cost estimates in answers and eval
# results. Haiku 5.5 has a higher price for prompts over 100K tokens (PRICES_LONG_PROMPT).
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
PRICES_LONG_PROMPT = {"claude-haiku-5-5": (0.50, 2.50)}  # prompts over 100K tokens
# Models that accept the server-side refusal fallback (`fallbacks: "default"`). Haiku 5.5 has none.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

# Spend cap for Ask in the web app, across all visitors, per UTC day (admins included).
ASK_BUDGET_USD = float(os.environ.get("IKG_ASK_BUDGET_USD", "5"))
# Answers the web app keeps in memory for repeated questions (0 disables the cache).
ANSWER_CACHE_SIZE = int(os.environ.get("IKG_ANSWER_CACHE_SIZE", "1000"))

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
