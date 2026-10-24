"""Source registry and downloading of texts.

Two registries are merged:
  - the bundled ``ikg/data/sources.yaml`` (curated public-domain editions), and
  - ``data/works.yaml``, written by the web admin when people add Gutenberg texts or
    upload their own material. User-added works start as ``pending`` and only enter the
    corpus once an admin approves them.
"""

import hashlib
from collections import Counter
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests
import yaml

from ikg import config

GUTENBERG_URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"
START_MARK = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARK = "*** END OF THE PROJECT GUTENBERG EBOOK"

STATUSES = ("pending", "approved", "rejected")
UPLOAD_EXTENSIONS = (".txt", ".md", ".pdf")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

_registry_lock = threading.Lock()


@dataclass
class Work:
    id: str
    title: str
    translator: str
    year: str
    parser: str
    files: list[dict] = field(default_factory=list)
    notes: str = ""
    status: str = "approved"
    kind: str = "narrative"  # default passage kind for the generic parser
    submitted_by: str = ""
    submitted_at: str = ""
    screening: str = ""  # local model's relevance verdict for uploads
    bundled: bool = False
    # A registry entry is one edition (a translation or a scholarly book) of a work (the text).
    # Both default to `id`; set them when a second edition of the same text is added (D10).
    work: str = ""
    edition: str = ""
    # Passage ID prefix. Bundled parsers default to their own (mbh, ram, upa, vp) and the generic
    # parser to `id`; a second edition of a bundled text must set its own (e.g. mbhd), or the
    # build fails on duplicate passage IDs (D11).
    id_prefix: str = ""

    @property
    def work_id(self) -> str:
        return self.work or self.id

    @property
    def edition_id(self) -> str:
        return self.edition or self.id

    def raw_paths(self, raw_dir: Path | None = None) -> list[Path]:
        raw_dir = raw_dir or config.RAW_DIR
        paths = []
        for f in self.files:
            if "gutenberg" in f:
                paths.append(raw_dir / f"pg{f['gutenberg']}.txt")
            else:
                paths.append(config.UPLOADS_DIR / f["upload"])
        return paths

    def source_urls(self) -> list[str]:
        return [f"https://www.gutenberg.org/ebooks/{f['gutenberg']}" if "gutenberg" in f
                else f"upload:{f.get('original_name', f['upload'])}" for f in self.files]


def _load_yaml(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("works", [])


def _to_work(entry: dict, bundled: bool) -> Work:
    entry = {**entry, "year": str(entry.get("year", ""))}
    entry.pop("bundled", None)
    return Work(**entry, bundled=bundled)


def load_works(include_unapproved: bool = False) -> list[Work]:
    works = [_to_work(w, True) for w in _load_yaml(config.package_file("sources.yaml"))]
    works += [_to_work(w, False) for w in _load_yaml(config.USER_WORKS_FILE)]
    editions = Counter(w.edition_id for w in works)
    if dupes := sorted(e for e, n in editions.items() if n > 1):
        raise ValueError(f"edition ids must be unique across the registry: {', '.join(dupes)}")
    if not include_unapproved:
        works = [w for w in works if w.status == "approved"]
    return works


def get_work(work_id: str) -> Work:
    for work in load_works(include_unapproved=True):
        if work.id == work_id:
            return work
    raise KeyError(f"unknown work: {work_id}")


def _save_user_works(works: list[Work]) -> None:
    config.USER_WORKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for w in works:
        d = asdict(w)
        d.pop("bundled")
        entries.append(d)
    config.USER_WORKS_FILE.write_text(yaml.safe_dump({"works": entries}, sort_keys=False, allow_unicode=True),
                                      encoding="utf-8")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "work"


def _unique_id(base: str) -> str:
    works = load_works(include_unapproved=True)
    existing = {w.id for w in works} | {w.edition_id for w in works}  # a new entry's edition defaults to its id
    candidate, n = base, 2
    while candidate in existing:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def add_user_work(title: str, translator: str, year: str, files: list[dict], parser: str = "generic",
                  kind: str = "narrative", submitted_by: str = "", status: str = "pending",
                  notes: str = "", screening: str = "") -> Work:
    with _registry_lock:
        user = [_to_work(w, False) for w in _load_yaml(config.USER_WORKS_FILE)]
        work = Work(id=_unique_id(_slug(title)), title=title, translator=translator, year=str(year),
                    parser=parser, files=files, notes=notes, status=status, kind=kind,
                    submitted_by=submitted_by, submitted_at=time.strftime("%Y-%m-%d %H:%M"),
                    screening=screening)
        _save_user_works(user + [work])
    return work


def set_status(work_id: str, status: str) -> Work:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with _registry_lock:
        user = [_to_work(w, False) for w in _load_yaml(config.USER_WORKS_FILE)]
        for w in user:
            if w.id == work_id:
                w.status = status
                _save_user_works(user)
                return w
    raise KeyError(f"{work_id} is not a user-added work")


def save_upload(filename: str, content: bytes) -> dict:
    """Store an uploaded file under a random name; returns the registry file entry."""
    suffix = Path(filename).suffix.lower()
    if suffix not in UPLOAD_EXTENSIONS:
        raise ValueError(f"unsupported file type {suffix!r}; allowed: {', '.join(UPLOAD_EXTENSIONS)}")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not content.strip():
        raise ValueError("file is empty")
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{suffix}"
    (config.UPLOADS_DIR / stored).write_bytes(content)
    return {"upload": stored, "original_name": Path(filename).name}


def _download(url: str, attempts: int = 3) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException:
            if attempt == attempts:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def fetch(works: list[Work], raw_dir: Path | None = None, force: bool = False) -> list[Path]:
    """Download each work's Gutenberg files into the raw directory (skips existing files).

    Files pinned with ``sha256`` in the registry are verified, whether just downloaded or
    already present, so the build only ever segments the exact texts the eval was written for.
    """
    raw_dir = raw_dir or config.RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for work in works:
        for f, path in zip(work.files, work.raw_paths(raw_dir)):
            if "gutenberg" not in f:
                continue
            if force or not path.exists():
                content = _download(GUTENBERG_URL.format(id=int(f["gutenberg"])))
                _verify(f, content, f"downloaded pg{f['gutenberg']}")
                path.write_bytes(content)
                written.append(path)
            else:
                _verify(f, path.read_bytes(), str(path))
    return written


def _verify(entry: dict, content: bytes, what: str) -> None:
    expected = entry.get("sha256")
    if expected and hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f"{what} does not match the sha256 pinned in the source registry; the upstream "
                         f"text may have changed. Check it, then update the pin and the eval baseline.")


def strip_gutenberg(text: str) -> str:
    """Return only the ebook body, without the Project Gutenberg header and license."""
    start = text.find(START_MARK)
    if start != -1:
        text = text[text.index("\n", start) + 1:]
    end = text.find(END_MARK)
    if end != -1:
        text = text[:end]
    return text.replace("\r\n", "\n")
