"""Named curators and the provenance log (decision D12).

Each curator has a name and a token; only the token's SHA-256 is stored, in `data/curators.yaml`.
`IKG_ADMIN_TOKEN` keeps working and logs in as the curator "admin". Every admin action, and every
public submission, is appended to `data/provenance.jsonl` with who did it, when, and to what, so
curation records ("who proposed, who approved") are correct from the first entry. The log is
append-only: nothing in the application rewrites or deletes lines.

OIDC and roles replace the tokens in Phase 4; the log format carries over.
"""

import getpass
import hashlib
import json
from collections import deque
import re
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import yaml

from ikg import config

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
RESERVED = {"admin", "public", "system"}  # used by the log for the env token, submissions and builds

_lock = threading.Lock()


@dataclass
class Curator:
    name: str
    token_sha256: str
    added: str


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_curators() -> list[Curator]:
    path = config.CURATORS_FILE
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return [Curator(**c) for c in data.get("curators", [])]
    except (yaml.YAMLError, TypeError, AttributeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def _save(curators: list[Curator]) -> None:
    config.CURATORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.CURATORS_FILE.write_text(yaml.safe_dump({"curators": [asdict(c) for c in curators]}, sort_keys=False),
                                    encoding="utf-8")


def add_curator(name: str) -> str:
    """Create a curator and return their token. The token is shown once and never stored."""
    if not NAME_RE.fullmatch(name) or name in RESERVED:
        raise ValueError("a curator name is 1-40 lowercase letters, digits, '.', '_' or '-', "
                         f"and not one of {', '.join(sorted(RESERVED))}")
    with _lock:
        curators = load_curators()
        if any(c.name == name for c in curators):
            raise ValueError(f"curator {name!r} already exists")
        token = secrets.token_urlsafe(24)
        _save(curators + [Curator(name, _digest(token), _now())])
    record("system", "curator_added", name, f"by OS user {_os_user()}")
    return token


def remove_curator(name: str) -> None:
    with _lock:
        curators = load_curators()
        if not any(c.name == name for c in curators):
            raise ValueError(f"no curator named {name!r}")
        _save([c for c in curators if c.name != name])
    record("system", "curator_removed", name, f"by OS user {_os_user()}")


def _os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no user name available (some containers)
        return "unknown"


def is_current(name: str) -> bool:
    """Whether `name` may still act: the env admin always, a named curator only while registered,
    so removing a curator also ends their live sessions."""
    if name == "admin":
        return True
    try:
        return any(c.name == name for c in load_curators())
    except ValueError:
        return False


def identify(token: str, admin_token: str | None) -> str | None:
    """The curator a login token belongs to, or None. Comparisons are constant-time."""
    if admin_token and secrets.compare_digest(token.encode(), admin_token.encode()):
        return "admin"
    digest = _digest(token).encode()
    for c in load_curators():
        if secrets.compare_digest(digest, c.token_sha256.encode()):
            return c.name
    return None


def record(curator: str, action: str, target: str = "", detail: str = "") -> None:
    """Append one entry to the provenance log."""
    entry = {"time": _now(), "curator": curator, "action": action, "target": target, "detail": detail}
    with _lock:
        config.PROVENANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with config.PROVENANCE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def recent(n: int = 50) -> list[dict]:
    """The last `n` log entries, newest first. Lines that aren't valid JSON are skipped."""
    path = config.PROVENANCE_FILE
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=n)  # streams the file; keeps only the last n lines
    except OSError:
        return []
    entries = []
    for line in tail:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append({k: str(entry.get(k, "")) for k in ("time", "curator", "action", "target", "detail")})
    return entries[::-1]
