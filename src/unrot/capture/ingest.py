"""Copy transcripts into unrot's own store, and resolve pointers back out.

Read-only with respect to ~/.claude: this module opens source files for reading
and never writes, moves, locks or touches anything under Claude Code's
directory. There is no hook, no MCP server, no slash command and no injected
text -- ingestion is a thing you run, and a running session cannot tell.

Per PR-15 we copy rather than reference. Claude Code owns the lifecycle of the
originals: it can rotate them and the user can delete them. A pointer into
someone else's retention policy is not re-runnability.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .parser import parse_lines

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass
class IngestResult:
    session_id: str
    status: str          # 'new' | 'appended' | 'unchanged' | 'rewritten' | 'empty'
    lines_added: int
    turns_added: int
    lines_total: int
    bytes_total: int
    malformed_lines: int = 0
    skipped_blocks: int = 0

    @property
    def changed(self) -> bool:
        return self.status not in ("unchanged", "empty")


def connect(root: Path | str | None = None) -> sqlite3.Connection:
    """Open the raw store, creating the layout if needed."""
    home = paths.home(root)
    paths.ensure_layout(home)
    conn = sqlite3.connect(str(paths.raw_db_path(home)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _complete_prefix(data: bytes) -> bytes:
    """Everything up to and including the last newline.

    A session still being written ends in a half-line. Taking only whole lines
    means a partial write is simply not ingested yet rather than ingested wrong,
    and the next run picks it up once Claude Code has finished the line.
    """
    cut = data.rfind(b"\n")
    return b"" if cut == -1 else data[: cut + 1]


def ingest_file(
    source: Path | str,
    *,
    conn: sqlite3.Connection,
    root: Path | str | None = None,
) -> IngestResult:
    """Ingest one transcript. Idempotent, and incremental for growing sessions."""
    source = Path(source)
    home = paths.home(root)
    session_id = source.stem

    # Read-only, in one shot. We never hold the file open across the parse.
    with open(source, "rb") as handle:
        data = handle.read()
    complete = _complete_prefix(data)

    row = conn.execute(
        "SELECT * FROM raw_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()

    if not complete:
        if row is None:
            return IngestResult(session_id, "empty", 0, 0, 0, 0)
        return IngestResult(
            session_id, "unchanged", 0, 0, row["lines_ingested"], row["bytes_ingested"]
        )

    start_line = 1
    status = "new"
    rewrites = 0
    copy = paths.copy_path(home, session_id)

    if row is not None:
        already = row["bytes_ingested"]
        prefix_matches = (
            already <= len(complete)
            and hashlib.sha256(complete[:already]).hexdigest() == row["prefix_sha256"]
        )
        if not prefix_matches:
            # The file was rewritten or truncated under us. Anything we thought
            # we knew about its line numbers is now wrong, so throw our parse of
            # it away rather than stitching two different files together.
            status = "rewritten"
            rewrites = row["rewrites"] + 1
            conn.execute("DELETE FROM raw_turns WHERE session_id = ?", (session_id,))
        elif already == len(complete):
            return IngestResult(
                session_id, "unchanged", 0, 0, row["lines_ingested"], already
            )
        else:
            status = "appended"
            start_line = row["lines_ingested"] + 1

    new_bytes = complete if status in ("new", "rewritten") else complete[row["bytes_ingested"]:]
    mode = "wb" if status in ("new", "rewritten") else "ab"

    # The copy is byte-for-byte, and only ever appended to. That is what keeps
    # line N meaning line N forever, which is what makes a stored pointer stable.
    copy.parent.mkdir(parents=True, exist_ok=True)
    with open(copy, mode) as handle:
        handle.write(new_bytes)

    raw_lines = new_bytes.decode("utf-8", errors="replace").splitlines()
    turns, stats, meta = parse_lines(raw_lines, start_line_no=start_line)

    conn.executemany(
        "INSERT OR REPLACE INTO raw_turns (session_id, line_no, seq, role, text,"
        " is_meta, is_sidechain, tool_name, tool_use_id, is_error, occurred_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                session_id,
                t.line_no,
                t.seq,
                t.role,
                t.text,
                int(t.is_meta),
                int(t.is_sidechain),
                t.tool_name,
                t.tool_use_id,
                None if t.is_error is None else int(t.is_error),
                t.timestamp,
            )
            for t in turns
        ],
    )

    lines_total = start_line - 1 + stats.lines
    digest = hashlib.sha256(complete).hexdigest()
    now = _now()

    if row is None or status == "rewritten":
        unknown = stats.unknown_types
        malformed, skipped = stats.malformed_lines, stats.skipped_blocks
    else:
        unknown = json.loads(row["unknown_types"])
        for key, count in stats.unknown_types.items():
            unknown[key] = unknown.get(key, 0) + count
        malformed = row["malformed_lines"] + stats.malformed_lines
        skipped = row["skipped_blocks"] + stats.skipped_blocks

    conn.execute(
        "INSERT INTO raw_sessions (session_id, reported_session_id, source_path,"
        " copy_path, bytes_ingested, lines_ingested, prefix_sha256, entrypoint,"
        " cc_version, cwd, git_branch, first_ingested_at, last_ingested_at,"
        " rewrites, malformed_lines, skipped_blocks, unknown_types)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(session_id) DO UPDATE SET"
        "   reported_session_id = COALESCE(excluded.reported_session_id, reported_session_id),"
        "   source_path = excluded.source_path,"
        "   bytes_ingested = excluded.bytes_ingested,"
        "   lines_ingested = excluded.lines_ingested,"
        "   prefix_sha256 = excluded.prefix_sha256,"
        "   entrypoint = COALESCE(excluded.entrypoint, entrypoint),"
        "   cc_version = COALESCE(excluded.cc_version, cc_version),"
        "   cwd = COALESCE(excluded.cwd, cwd),"
        "   git_branch = COALESCE(excluded.git_branch, git_branch),"
        "   last_ingested_at = excluded.last_ingested_at,"
        "   rewrites = excluded.rewrites,"
        "   malformed_lines = excluded.malformed_lines,"
        "   skipped_blocks = excluded.skipped_blocks,"
        "   unknown_types = excluded.unknown_types",
        (
            session_id,
            meta.get("sessionId"),
            str(source),
            str(copy),
            len(complete),
            lines_total,
            digest,
            meta.get("entrypoint"),
            meta.get("version"),
            meta.get("cwd"),
            meta.get("gitBranch"),
            now,
            now,
            rewrites,
            malformed,
            skipped,
            json.dumps(unknown, sort_keys=True),
        ),
    )
    conn.commit()

    return IngestResult(
        session_id=session_id,
        status=status,
        lines_added=stats.lines,
        turns_added=len(turns),
        lines_total=lines_total,
        bytes_total=len(complete),
        malformed_lines=stats.malformed_lines,
        skipped_blocks=stats.skipped_blocks,
    )


def ingest_all(
    *,
    conn: sqlite3.Connection,
    root: Path | str | None = None,
    projects_dir: Path | None = None,
    limit: int | None = None,
) -> list[IngestResult]:
    """Ingest every transcript found on this machine, newest first."""
    found = paths.discover(projects_dir)
    if limit is not None:
        found = found[:limit]
    results = []
    for path in found:
        try:
            results.append(ingest_file(path, conn=conn, root=root))
        except OSError as exc:
            # One unreadable file must not abort a whole sweep.
            results.append(
                IngestResult(path.stem, f"error: {exc.__class__.__name__}", 0, 0, 0, 0)
            )
    return results


def resolve_pointer(
    session_id: str,
    line_start: int,
    line_end: int | None = None,
    *,
    root: Path | str | None = None,
) -> list[str]:
    """Resolve a stored pointer back to exact transcript lines. 1-based, inclusive.

    Reads unrot's copy, never `~/.claude/projects/`. This is the whole reason
    PR-15 chose copying: the original may be long gone.
    """
    home = paths.home(root)
    copy = paths.copy_path(home, session_id)
    if not copy.exists():
        raise FileNotFoundError(f"no retained copy for session {session_id!r}")
    last = line_start if line_end is None else line_end
    if line_start < 1 or last < line_start:
        raise ValueError(f"bad line range {line_start}..{last}")

    out: list[str] = []
    with open(copy, "r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw in enumerate(handle, start=1):
            if line_no > last:
                break
            if line_no >= line_start:
                out.append(raw.rstrip("\n"))
    return out


def human_turns(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    """Only what a person actually typed: no tool results, no injected text, no subagents.

    This is the query the detector is built on, which is why it is defined here
    once rather than re-derived by every caller.
    """
    return list(
        conn.execute(
            "SELECT * FROM raw_turns WHERE session_id = ? AND role = 'user'"
            " AND is_meta = 0 AND is_sidechain = 0 ORDER BY line_no, seq",
            (session_id,),
        )
    )
