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
CURATORS_FILE = DATA_DIR / "curators.yaml"          # named curators: name and token SHA-256 (D12)
PROVENANCE_FILE = DATA_DIR / "provenance.jsonl"     # append-only log of who did what (D12)

PASSAGES_FILE = PROCESSED_DIR / "passages.jsonl"
MENTIONS_FILE = PROCESSED_DIR / "mentions.jsonl"
INDEX_FILE = PROCESSED_DIR / "bm25.pkl"
GRAPH_FILE = PROCESSED_DIR / "cooccurrence.graphml"
RDF_FILE = PROCESSED_DIR / "ikg.ttl"
MANIFEST_FILE = PROCESSED_DIR / "manifest.json"  # what the build was made from (D11), with checksums
GAZETTEER_SNAPSHOT_FILE = PROCESSED_DIR / "gazetteer.yaml"  # the gazetteer the build used
BUILD_LOCK_FILE = PROCESSED_DIR / ".build.lock"  # one build at a time, CLI and web alike
EMBEDDINGS_FILE = PROCESSED_DIR / "embeddings.npy"        # one normalised row per passage (ikg.embed)
EMBEDDINGS_META_FILE = PROCESSED_DIR / "embeddings.json"  # model, dimension and the passage ID of each row

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
# A local model through Ollama answers instead when the model is "ollama:<name>" (e.g.
# IKG_ANSWER_MODEL=ollama:qwen3.5:9B). No API cost; citations are checked in code (ikg.localqa).
LOCAL_ANSWER_TIMEOUT = float(os.environ.get("IKG_LOCAL_ANSWER_TIMEOUT", "180"))  # seconds per answer
LOCAL_ANSWER_CONTEXT = int(os.environ.get("IKG_LOCAL_ANSWER_CONTEXT", "16384"))  # tokens; 8 passages need ~4K

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

# Judge for the fidelity benchmark (ikg eval --fidelity). Its verdicts are checked against expert
# labels before rates are reported (eval/fidelity/README.md).
JUDGE_MODEL = os.environ.get("IKG_JUDGE_MODEL", "claude-opus-5-5")
JUDGE_EFFORT = os.environ.get("IKG_JUDGE_EFFORT", "medium")
FIDELITY_FILE = Path("eval") / "fidelity" / "items.jsonl"

# PDFs are parsed in a child process with these limits (ikg.pdftext).
PDF_MAX_PAGES = int(os.environ.get("IKG_PDF_MAX_PAGES", "2000"))
PDF_TIMEOUT = float(os.environ.get("IKG_PDF_TIMEOUT", "120"))
PDF_MAX_CHARS = int(os.environ.get("IKG_PDF_MAX_CHARS", "50000000"))  # a 1,000-page book is about 6 million
PDF_MAX_MEMORY_MB = int(os.environ.get("IKG_PDF_MAX_MEMORY_MB", "2048"))  # POSIX only
PDF_MAX_PARALLEL = int(os.environ.get("IKG_PDF_MAX_PARALLEL", "2"))
# Contributor uploads per hour, per client address and across all clients (admins excluded).
UPLOAD_LIMIT = int(os.environ.get("IKG_UPLOAD_LIMIT", "10"))
UPLOAD_GLOBAL_LIMIT = int(os.environ.get("IKG_UPLOAD_GLOBAL_LIMIT", "100"))
LOGIN_FAILURE_LIMIT = int(os.environ.get("IKG_LOGIN_FAILURE_LIMIT", "10"))  # failed admin logins per client per hour

# Spend cap for Ask in the web app, across all visitors, per UTC day (admins included).
ASK_BUDGET_USD = float(os.environ.get("IKG_ASK_BUDGET_USD", "5"))
# Answers the web app keeps in memory for repeated questions (0 disables the cache).
ANSWER_CACHE_SIZE = int(os.environ.get("IKG_ANSWER_CACHE_SIZE", "1000"))

# Dense passage embeddings at build time (ikg.embed; docs/08 A1). Off until hybrid retrieval is shown
# to beat BM25 on the eval (docs/08 A3). `ikg build --embed` / `--no-embed` overrides IKG_EMBED.
EMBED = os.environ.get("IKG_EMBED", "off").lower() in ("on", "1", "true", "yes")
EMBED_MODEL = os.environ.get("IKG_EMBED_MODEL", "BAAI/bge-small-en-v1.5")  # or "ollama:<model>"
# Where nearest-passage search runs (ikg.vectorstore; docs/08 A2): "file" (exact, in memory) or a
# postgresql:// URL with pgvector (`docker compose up db`), which is synced from the build on first use.
VECTOR_STORE = os.environ.get("IKG_VECTOR_STORE", "file")
# Retrieval mode for search, Ask and eval (ikg.retrieve; docs/08 A3): bm25, dense or hybrid. Hybrid is
# the default because it beat BM25 on hit@8 and MRR (docs/06 §1). Dense and hybrid need a build with
# embeddings (`ikg build --embed`); without one the default falls back to bm25.
RETRIEVER = os.environ.get("IKG_RETRIEVER", "hybrid")
if RETRIEVER not in ("bm25", "dense", "hybrid"):  # retrieve.MODES
    raise ValueError(f"IKG_RETRIEVER must be bm25, dense or hybrid, not {RETRIEVER!r}")

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
