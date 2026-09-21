"""Opening and initialising the store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: Bump whenever any `compiled_*` table changes shape.
#:
#: `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so
#: without this a newly added column never appears in a store that was created
#: before it. Every test passes -- they build fresh databases -- and every real
#: store fails at runtime on the first query that names the column. That is the
#: worst shape a bug can have: invisible exactly where it is being checked.
#:
#: The migration is not `ALTER TABLE`. Compiled state is derived and disposable
#: by construction, so the honest repair is to throw the whole lot away and
#: re-fold the log, which is the same operation the design already relies on
#: being safe. `events` is never touched.
COMPILED_SCHEMA = 3


def _drop_compiled(conn: sqlite3.Connection) -> None:
    """Drop every compiled table, found by name rather than from a list.

    Reading `sqlite_master` rather than a constant means a table that was
    removed from the schema also gets cleaned up, instead of lingering as a
    stale copy of something nothing writes to any more.
    """
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
        " AND name LIKE 'compiled_%'"
    ).fetchall():
        conn.execute(f"DROP TABLE {name}")


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open the store, creating it if needed, and rebuild compiled state on drift.

    `PRAGMA user_version` holds the compiled-schema version. It is the one
    SQLite-specific thing in this layer, and it earns that: the alternative is a
    version column inside a table that itself may need to change shape, which
    cannot bootstrap. The schema's portability rule is about column *types*
    surviving the move to Postgres, and this is four lines of connect logic
    rather than anything in the data.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")

    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found != COMPILED_SCHEMA:
        _drop_compiled(conn)

    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    if found != COMPILED_SCHEMA:
        conn.execute(f"PRAGMA user_version = {COMPILED_SCHEMA}")
        # Re-fold immediately rather than leaving the tables empty: a reader
        # that opened the store would otherwise see a graph with nothing in it
        # and have no way to tell that from a graph that is genuinely empty.
        from .compile import compile_state

        compile_state(conn)
    return conn
