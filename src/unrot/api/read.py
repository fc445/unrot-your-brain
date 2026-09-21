"""The read model: compiled state turned into what the surface renders.

Everything here is a pure function of the two stores. Nothing in this module
writes, which is what lets the API open a fresh connection per request and hold
no state between them.

**All presentation policy lives here, not in the frontend.** The rule about
which bucket a gap belongs in is a product decision that has to stay next to
`derive_state`, not be re-derived in TypeScript where nobody will find it when
the rule changes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

#: Rendered as a section each. `open` is the only one that asks anything of the
#: user; the other two exist so the list is not purely a ledger of failures --
#: ideas.md is explicit that a daily list of your shortcomings is a product
#: people delete in week two.
BUCKETS = ("open", "learning", "closed")

#: How many parsed turns the moment view will return. A window can span forty
#: lines of tool traffic; the user wants what was said and what they said back,
#: not the machinery in between.
MOMENT_LIMIT = 40


@dataclass
class Encounter:
    encounter_id: str
    source: str
    paraphrase: str | None
    judgment: str | None
    judged_at: str | None
    occurred_at: str
    detector_version: str | None
    session_id: str | None
    line_start: int | None
    line_end: int | None
    #: Whether the raw transcript is still on this machine. False on the portable
    #: layer by construction -- S4 keeps raw local -- so the surface must read
    #: fine from the paraphrase alone.
    resolvable: bool = False


@dataclass
class Concept:
    concept_id: str
    name: str
    gap_type: str
    state: str
    bucket: str
    aliases: list[str]
    encounter_count: int
    first_seen_at: str | None
    last_seen_at: str | None
    latest_level: str | None
    encounters: list[Encounter] = field(default_factory=list)

    @property
    def unjudged(self) -> int:
        return sum(1 for e in self.encounters if e.judgment is None)


def bucket_for(state: str, encounters: list[Encounter]) -> str | None:
    """Which section a concept renders in, or None to keep it off the surface.

    `referenced` returns None and that is the whole point of the state: a concept
    named in material but never encountered by the user is scaffolding for
    clustering. Surfacing it would mean showing someone a gap they have never
    actually met, which is indistinguishable from the tool making things up.
    """
    if state == "referenced" or not encounters:
        return None
    if state == "known":
        return "closed"
    if any(e.judgment is None for e in encounters):
        return "open"
    # Judged, still a gap: the user said they did not know it. That is the start
    # of learning it, not the end of dealing with it.
    return "learning"


def _sessions_on_disk(root) -> set[str]:
    from ..capture import paths

    directory = paths.sessions_dir(paths.home(root))
    if not directory.is_dir():
        return set()
    return {p.stem for p in directory.glob("*.jsonl")}


def concepts(conn: sqlite3.Connection, root=None) -> list[Concept]:
    """Every concept that belongs on the surface, newest activity first."""
    retained = _sessions_on_disk(root)

    by_concept: dict[str, list[Encounter]] = {}
    for row in conn.execute(
        "SELECT * FROM compiled_encounters ORDER BY occurred_at DESC"
    ):
        by_concept.setdefault(row["concept_id"], []).append(
            Encounter(
                encounter_id=row["encounter_id"],
                source=row["source"],
                paraphrase=row["paraphrase"],
                judgment=row["judgment"],
                judged_at=row["judged_at"],
                occurred_at=row["occurred_at"],
                detector_version=row["detector_version"],
                session_id=row["session_id"],
                line_start=row["line_start"],
                line_end=row["line_end"],
                resolvable=bool(row["session_id"]) and row["session_id"] in retained,
            )
        )

    out: list[Concept] = []
    for row in conn.execute(
        "SELECT * FROM compiled_concepts WHERE merged_into IS NULL"
        " ORDER BY COALESCE(last_seen_at, '') DESC, canonical_name"
    ):
        found = by_concept.get(row["concept_id"], [])
        bucket = bucket_for(row["state"], found)
        if bucket is None:
            continue
        out.append(
            Concept(
                concept_id=row["concept_id"],
                name=row["canonical_name"],
                gap_type=row["gap_type"],
                state=row["state"],
                bucket=bucket,
                aliases=json.loads(row["aliases"] or "[]"),
                encounter_count=row["encounter_count"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
                latest_level=row["latest_level"],
                encounters=found,
            )
        )
    return out


@dataclass
class Capture:
    sessions: int
    human_turns: int
    last_activity: str | None
    #: Sessions that have produced at least one encounter. Deliberately NOT
    #: called "analysed": nothing records that the detector ran over a session
    #: and found nothing, so the difference between a clean session and one
    #: never looked at is currently invisible. PR-21 should append a
    #: `session_analysed` event and this becomes a real number.
    sessions_with_flags: int


@dataclass
class Surface:
    """What state the whole surface is in, and why."""

    state: str
    headline: str
    detail: str
    capture: Capture
    counts: dict[str, int]
    fixtures: int


def capture_stats(raw_conn: sqlite3.Connection | None, conn: sqlite3.Connection) -> Capture:
    flagged = conn.execute(
        "SELECT count(DISTINCT session_id) FROM compiled_encounters"
        " WHERE session_id IS NOT NULL"
    ).fetchone()[0]
    if raw_conn is None:
        return Capture(0, 0, None, flagged)
    row = raw_conn.execute("SELECT count(*) AS n FROM raw_sessions").fetchone()
    turns = raw_conn.execute(
        "SELECT count(*) AS n, max(occurred_at) AS last FROM raw_turns"
        " WHERE role = 'user' AND is_meta = 0 AND is_sidechain = 0"
    ).fetchone()
    return Capture(row["n"], turns["n"], turns["last"], flagged)


#: Below this, a quiet list is more likely to mean "we have barely looked" than
#: "you are doing well" -- journey 7's honest cold start.
COLD_START_SESSIONS = 3


def surface(
    conn: sqlite3.Connection,
    raw_conn: sqlite3.Connection | None,
    found: list[Concept],
) -> Surface:
    """Decide which of the states the surface is in.

    Journey 3 turns on this: "we looked and found nothing" has to be legible as a
    good result, and distinguishable from capture never running and from analysis
    breaking. A single empty list cannot say all three, so the states are named
    on the server and the frontend renders whichever it is told.

    `failed` is not produced here. A failure is a request that did not return, so
    the frontend derives it from the transport rather than from a field in a
    response that by definition did not arrive.
    """
    from ..store import fixtures as fixtures_module

    stats = capture_stats(raw_conn, conn)
    counts = {bucket: sum(1 for c in found if c.bucket == bucket) for bucket in BUCKETS}
    seeded = fixtures_module.count(conn)
    events = conn.execute("SELECT count(*) FROM events").fetchone()[0]

    if stats.sessions == 0:
        state, headline, detail = (
            "not_captured",
            "Nothing captured yet",
            "unrot has not read any Claude Code transcripts yet."
            " Nothing has been analysed because there is nothing to analyse.",
        )
    elif events == 0:
        state, headline, detail = (
            "not_analysed",
            f"{stats.sessions} sessions captured, none analysed",
            "Transcripts are on disk but nothing has looked at them yet."
            " This is not a clean bill of health -- it is an empty one.",
        )
    elif counts["open"]:
        state, headline, detail = (
            "gaps",
            f"{counts['open']} waiting on you",
            "Each of these was leaned on in a session and waved through."
            " Say whether you actually knew it.",
        )
    elif stats.sessions < COLD_START_SESSIONS:
        state, headline, detail = (
            "cold_start",
            "Not enough history yet",
            f"Only {stats.sessions} session(s) captured. Too little to say much"
            " either way -- come back after a few more.",
        )
    else:
        state, headline, detail = (
            "clean",
            "Nothing waiting on you",
            "Everything found so far has been dealt with."
            " Silence here means we looked, not that nothing ran.",
        )

    return Surface(
        state=state,
        headline=headline,
        detail=detail,
        capture=stats,
        counts=counts,
        fixtures=seeded,
    )


def moment(
    raw_conn: sqlite3.Connection | None,
    session_id: str,
    line_start: int,
    line_end: int,
) -> list[dict]:
    """The point of acceptance: what was said, and what the user said back.

    Reads the parsed turns rather than re-reading the `.jsonl`, because capture
    has already done the work of separating what a person typed from tool results
    and injected text -- which is the entire distinction this product rests on.
    Handing the raw records back would make the surface re-derive it badly.

    `is_meta` and `is_sidechain` are filtered out rather than merely flagged. A
    system-reminder arrives in a `user`-typed line without a person touching the
    keyboard, so showing it under a "you" label would put words in the user's
    mouth in the one view whose entire job is to show what they actually said --
    and it is the view they are meant to judge themselves against.
    """
    if raw_conn is None:
        return []
    rows = raw_conn.execute(
        "SELECT line_no, seq, role, text, tool_name, is_meta, is_sidechain"
        "  FROM raw_turns"
        " WHERE session_id = ? AND line_no BETWEEN ? AND ?"
        "   AND role IN ('assistant', 'user') AND text IS NOT NULL"
        "   AND is_meta = 0 AND is_sidechain = 0"
        " ORDER BY line_no, seq LIMIT ?",
        (session_id, line_start, line_end, MOMENT_LIMIT),
    ).fetchall()
    return [
        {
            "line_no": r["line_no"],
            "role": r["role"],
            "text": r["text"],
            "is_meta": bool(r["is_meta"]),
            "is_sidechain": bool(r["is_sidechain"]),
        }
        for r in rows
    ]
