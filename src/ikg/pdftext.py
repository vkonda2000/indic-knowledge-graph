"""PDF text extraction in a separate process, with limits.

PDFs come from the public contribute form, and a crafted one (deeply nested objects, a huge page
tree, a compression bomb) can pin the CPU or blow up memory while pypdf parses it. Parsing in a
child process bounds what a bad file can cost (review finding A5):

- CPU time: the child is killed after `PDF_TIMEOUT` seconds.
- Size: more than `PDF_MAX_PAGES` pages, or more than `PDF_MAX_CHARS` characters of text, is
  refused, so the parent never receives an unbounded amount of output.
- Memory: on POSIX the child's address space is capped at `PDF_MAX_MEMORY_MB`. Windows has no
  simple equivalent, so there memory is bounded only by pypdf's own decompression limits (per
  stream, pypdf 6+) and the text cap.
- Concurrency: at most `PDF_MAX_PARALLEL` extractions run at once.
- Secrets: the child gets a minimal environment, never the API key or admin token.
"""

import json
import os
import subprocess
import sys
import threading

from ikg import config

_EXTRACT = """
import io, sys
max_pages, max_chars, max_memory_mb = (int(a) for a in sys.argv[1:4])
try:
    import resource  # POSIX only
    resource.setrlimit(resource.RLIMIT_AS, (max_memory_mb << 20, max_memory_mb << 20))
except (ImportError, ValueError, OSError):
    pass
from pypdf import PdfReader
reader = PdfReader(io.BytesIO(sys.stdin.buffer.read()))
if len(reader.pages) > max_pages:
    sys.stderr.write(f"the PDF has {len(reader.pages)} pages; the limit is {max_pages}")
    sys.exit(3)
parts, size = [], 0
for page in reader.pages:
    # Malformed font maps can yield lone surrogates, which can't be stored as UTF-8: replace them.
    text = (page.extract_text() or "").encode("utf-8", "replace").decode("utf-8")
    size += len(text) + 2
    if size > max_chars:
        sys.stderr.write(f"the PDF's text is longer than the limit of {max_chars:,} characters")
        sys.exit(3)
    parts.append(text)
try:
    labels = list(reader.page_labels)
except Exception:  # no or broken /PageLabels: fall back to page indexes
    labels = []
import json
sys.stdout.buffer.write(json.dumps({"pages": parts, "labels": labels}).encode("utf-8"))
"""

_slots = threading.BoundedSemaphore(config.PDF_MAX_PARALLEL)


def _child_env() -> dict:
    """Just enough environment for Python to start: no API keys or tokens."""
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL")
    return {k: os.environ[k] for k in keep if k in os.environ}


def pdf_text(data: bytes, max_pages: int | None = None, timeout: float | None = None,
             max_chars: int | None = None) -> str:
    """The text layer of a PDF, pages separated by a blank line; raises ValueError for an
    unreadable, oversized or too slow file."""
    return "\n\n".join(pdf_document(data, max_pages, timeout, max_chars)[0])


def pdf_document(data: bytes, max_pages: int | None = None, timeout: float | None = None,
                 max_chars: int | None = None) -> tuple[list[str], list[str]]:
    """Each page's text, and each page's printed label (from /PageLabels; empty if the PDF has
    none). Sent as JSON, so nothing in the text can be mistaken for a page boundary."""
    max_pages = max_pages or config.PDF_MAX_PAGES
    timeout = timeout or config.PDF_TIMEOUT
    max_chars = max_chars or config.PDF_MAX_CHARS
    args = [str(max_pages), str(max_chars), str(config.PDF_MAX_MEMORY_MB)]
    with _slots:
        try:
            done = subprocess.run([sys.executable, "-I", "-c", _EXTRACT, *args], input=data, capture_output=True,
                                  timeout=timeout, check=False, env=_child_env())
        except subprocess.TimeoutExpired:
            raise ValueError(f"reading the PDF took longer than {timeout:g} s") from None
    if done.returncode == 3:
        raise ValueError(done.stderr.decode("utf-8", errors="replace").strip())
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise ValueError(f"could not read the PDF{': ' + detail[-1] if detail else ''}")
    out = json.loads(done.stdout.decode("utf-8", errors="replace"))
    pages, labels = out["pages"], out["labels"]
    return pages, labels if len(labels) == len(pages) else []
