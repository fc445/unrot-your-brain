"""Where things live on disk, and the one directory that never syncs.

The layout exists to make S4's rule visible rather than merely documented:

    $UNROT_HOME/                 (default ~/.unrot)
      unrot.db                   the event log and compiled state -- the layer
                                 that may one day sync
      run/                       0700. Runtime sockets, nothing durable.
        core.sock
      raw/                       NEVER leaves this machine. One rule, one
        raw.db                   directory, no per-file judgment calls.
        sessions/<id>.jsonl

Keeping raw material in its own directory (and its own database) means the
sync boundary is a path prefix, not a column you have to remember to check.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Where Claude Code writes its session transcripts. Read-only, always.
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

_NEVER_SYNC_NOTICE = """\
# Everything under this directory is raw captured transcript material.
#
# It never syncs, never uploads, and never leaves this machine. Claude Code
# already wrote these files to your disk; unrot keeping a copy adds no new
# exposure. Sending one somewhere would.
#
# This .gitignore is here so that an accidental `git init` in a parent
# directory cannot sweep it up.
*
"""


def home(override: str | Path | None = None) -> Path:
    """Resolve unrot's home directory: argument, then $UNROT_HOME, then default."""
    if override is not None:
        return Path(override).expanduser()
    env = os.environ.get("UNROT_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".unrot"


def store_db_path(root: Path) -> Path:
    """The event log. The layer that may one day sync -- note it is NOT under raw/.

    Lives here rather than in `unrot.store` because this module is where the
    layout is written down, and the sync boundary is only legible if both sides
    of it are described in the same place.
    """
    return root / "unrot.db"


#: Darwin caps `sockaddr_un.sun_path` at 104 bytes including the terminator.
#: Exceeding it fails inside bind() as a bare OSError with no mention of length,
#: which is a miserable thing to debug -- so the check happens here, where the
#: path is chosen, and says what is actually wrong.
SUN_PATH_MAX = 103


def run_dir(root: Path) -> Path:
    """Runtime state: sockets and nothing else. Safe to delete while not running."""
    return root / "run"


def socket_path(root: Path) -> Path:
    """The Unix socket the API binds when the Mac app supervises it.

    Not a TCP port: there is no port to collide with, nothing appears on the
    machine's network surface, and reaching the gap graph requires filesystem
    access to this path rather than the ability to connect to a local port.

    That last property is the whole point, and it rests on the *directory*
    being 0700 rather than on the socket's own mode -- uvicorn chmods the
    socket it creates to 0666, so anything relying on the file's permissions
    would be relying on something that is actively overwritten.
    """
    return run_dir(root) / "core.sock"


def ensure_private_dir(path: Path) -> Path:
    """Create `path` as 0700, and narrow it back if it already exists wider.

    This is the access control for the socket, and the only one there is. It is
    re-applied on every start rather than only at creation, because a directory
    made before this rule existed -- or loosened by hand -- would otherwise stay
    open forever with nothing ever noticing.

    It applies to whatever directory the socket is asked to live in, not just to
    the default `run/`: a caller who passes `--uds` elsewhere is owed the same
    property, not a weaker one for having been specific.
    """
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def raw_dir(root: Path) -> Path:
    return root / "raw"


def raw_db_path(root: Path) -> Path:
    return raw_dir(root) / "raw.db"


def sessions_dir(root: Path) -> Path:
    return raw_dir(root) / "sessions"


def copy_path(root: Path, session_id: str) -> Path:
    """unrot's own copy of a transcript. Pointers resolve against this, not the original."""
    return sessions_dir(root) / f"{session_id}.jsonl"


def ensure_layout(root: Path) -> Path:
    """Create the directory layout, including the never-sync marker."""
    sessions_dir(root).mkdir(parents=True, exist_ok=True)
    marker = raw_dir(root) / ".gitignore"
    if not marker.exists():
        marker.write_text(_NEVER_SYNC_NOTICE, encoding="utf-8")
    return root


def discover(projects_dir: Path | None = None) -> list[Path]:
    """Every Claude Code transcript on this machine, newest first.

    Read-only: this only ever lists and stats, and callers only ever open for
    reading. Nothing in unrot writes into ~/.claude.
    """
    base = projects_dir or CLAUDE_PROJECTS
    if not base.is_dir():
        return []
    files = [p for p in base.rglob("*.jsonl") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
