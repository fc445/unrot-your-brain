"""One session, from captured to analysed -- the path `resolver run` and the watcher share.

Before the watcher there was one caller, the CLI, and the loop lived inside its
command. A second caller that copied that loop would be two places that decide
what "analysing a session" means, and they would drift: one would forget to
record the clean case, and silence would stop meaning anything. So the loop is
here, and both call it.

Nothing in this module decides *when* to analyse. That is the watcher's
question, and behind it the user's: every call here spends model calls, and
automatic spending is a thing they switch on, not a default.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .detector import detect
from .detector.detect import DEFAULT_MAX_CANDIDATES
from .resolver import Resolution, from_candidate, record_analysis, resolve


@dataclass
class Analysis:
    session_id: str
    detector_version: str
    windows_examined: int
    resolutions: list[Resolution] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.resolutions


def analyse_session(
    conn: sqlite3.Connection,
    raw: sqlite3.Connection,
    session_id: str,
    *,
    propose,
    decide,
    detector_label: str,
    resolver_label: str,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> Analysis:
    """Detect, resolve every candidate, and record that the session was examined.

    The record is written whether or not anything was found -- the zero case is
    the one that lets the surface say "we looked". It is written *last*, so a
    model call that fails partway leaves the session unrecorded and therefore
    still pending; the next run re-proposes the same candidates, and their
    fingerprinted encounter ids mean they update rather than duplicate.
    """
    result = detect(
        raw,
        session_id,
        propose=propose,
        model_label=detector_label,
        max_candidates=max_candidates,
    )
    resolutions = [
        # Recompiling after each one is deliberate: the next candidate's
        # resolution reads compiled state, and a term met twice in a session
        # must find the concept the first mention just created.
        resolve(conn, from_candidate(candidate), decide=decide, model_label=resolver_label)
        for candidate in result.emitted
    ]
    record_analysis(
        conn,
        session_id,
        candidates_found=len(result.emitted),
        detector_version=result.detector_version,
    )
    return Analysis(
        session_id=session_id,
        detector_version=result.detector_version,
        windows_examined=result.windows_examined,
        resolutions=resolutions,
    )


@dataclass
class Pending:
    session_id: str
    #: When the latest turn happened, as the transcript tells it.
    last_activity: str | None
    human_turns: int
    #: 'never' -- captured and not yet examined; 'grown' -- examined, then the
    #: session carried on and more of it was captured.
    reason: str
    analysed_at: str | None = None
    cwd: str | None = None


def pending(conn: sqlite3.Connection, raw: sqlite3.Connection | None) -> list[Pending]:
    """Captured sessions waiting to be analysed, most recent first.

    The watcher's queue, and derived rather than stored: it is the difference
    between what capture holds and what the log says was examined. So it
    survives a relaunch, a crash and a reinstall without anything to persist,
    and it cannot disagree with the surface about what has been looked at.

    Only sessions with something a person actually typed. A session that is all
    tool traffic has no human turn to judge, and would be analysed for nothing.
    """
    if raw is None:
        return []

    analysed = {
        row["session_id"]: row["analysed_at"]
        for row in conn.execute("SELECT session_id, analysed_at FROM compiled_sessions")
    }
    rows = raw.execute(
        "SELECT s.session_id, s.last_ingested_at, s.cwd,"
        "       count(t.line_no) AS human_turns, max(t.occurred_at) AS last_activity"
        "  FROM raw_sessions s"
        "  JOIN raw_turns t ON t.session_id = s.session_id"
        " WHERE t.role = 'user' AND t.is_meta = 0 AND t.is_sidechain = 0"
        " GROUP BY s.session_id"
        " ORDER BY last_activity DESC"
    ).fetchall()

    waiting = []
    for row in rows:
        when = analysed.get(row["session_id"])
        if when is None:
            reason = "never"
        elif _later(row["last_ingested_at"], when):
            reason = "grown"
        else:
            continue
        waiting.append(
            Pending(
                session_id=row["session_id"],
                last_activity=row["last_activity"],
                human_turns=row["human_turns"],
                reason=reason,
                analysed_at=when,
                cwd=row["cwd"],
            )
        )
    return waiting


def _later(a: str | None, b: str | None) -> bool:
    """`a` after `b`, comparing instants rather than strings."""
    if not a or not b:
        return False
    try:
        return _instant(a) > _instant(b)
    except ValueError:
        return a > b


def _instant(text: str) -> datetime:
    """An aware datetime. A stamp with no offset is read as UTC rather than
    left naive, since comparing naive with aware raises instead of answering."""
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
