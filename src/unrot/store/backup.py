"""A safety copy of the store, taken before a destructive operation touches it.

Only `wipe_and_rerun` (dev builds only, see `unrot.regen.wipe`) calls this today,
because it is the one operation in the whole codebase that can genuinely lose
data a user cares about -- a dev build points at the same `~/.unrot` a prod
build would use, there is no undo event for "I deleted the wrong things", and
the store this backs up is the ONLY copy of every judgment and explanation ever
made. A file next to it, made before anything is touched, is cheap insurance
against a wipe that turns out to have been a mistake.

`sqlite3.Connection.backup()` rather than a filesystem copy: the store runs in
WAL mode (see `db.connect`), so a plain `shutil.copy2` of `unrot.db` can miss
committed pages still sitting in the `-wal` file and silently produce a backup
that is missing recent writes -- the worst possible failure mode for a safety
copy, because it looks like it worked. The backup API reads through SQLite's
own consistent snapshot instead, the same mechanism the `sqlite3` CLI's
`.backup` command uses.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

#: Where backups live: inside the unrot home, beside `unrot.db` and `raw/`,
#: rather than off in a temp directory a person would never think to look in or
#: that a "clear temp files" tool might sweep up before anyone reads the path
#: back off the confirmation dialog.
BACKUPS_DIRNAME = "backups"


def backups_dir(root: Path) -> Path:
    return root / BACKUPS_DIRNAME


def backup_store(conn: sqlite3.Connection, root: Path, *, label: str = "unrot") -> Path:
    """Copy the store's current, consistent state to a timestamped file.

    Returns the path written, so a caller can hand it back to whoever asked for
    the destructive operation this is guarding. The timestamp is UTC and
    filesystem-safe (no colons), so backups sort in the order they were taken
    just by listing the directory.
    """
    directory = backups_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = directory / f"{label}-{stamp}.db"
    dest = sqlite3.connect(dest_path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return dest_path
