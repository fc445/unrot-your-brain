"""Where things live on disk, and the one directory that never syncs.

The layout exists to make S4's rule visible rather than merely documented:

    $UNROT_HOME/                 (default ~/.unrot)
      unrot.db                   the event log and compiled state -- the layer
                                 that may one day sync
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
