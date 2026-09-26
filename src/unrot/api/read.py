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
    #: The repo the session was working in, by its last path component. Read
    #: from the raw layer like `resolvable`, so absent anywhere that layer is.
    repo: str | None = None
    #: Triage thought you already knew this and asked anyway. The card says so,
    #: because "I knew it" here is an answer about triage, not the detector.
    spot_check: bool = False


@dataclass
class Explanation:
    """One attempt at the check, with whatever the grader made of it.

    Kept as a list on the concept rather than collapsed to the latest: journey 8
    is about whether an answer given weeks later got better, and that comparison
    needs both answers to still be there.
    """

    explanation_id: str
    raw_text: str
    prompt_text: str
    prompt_version: str
    submitted_at: str
    level: str | None = None
    reasoning: str | None = None
    #: Present only for a classifier grade. How close the answer came to the
    #: listed -> causal line, which is the only boundary the rubric turns on.
    probabilities: dict | None = None
    confidence: float | None = None
    grader_version: str | None = None


@dataclass
class MaterialOut:
    material_id: str
    format: str
    body: str | None
    sources: list[dict]
    generated_at: str
    delivered_at: str | None


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
    explanations: list[Explanation] = field(default_factory=list)
    material: list[MaterialOut] = field(default_factory=list)

    @property
    def unjudged(self) -> int:
        return sum(1 for e in self.encounters if e.judgment is None)


def bucket_for(
    state: str,
    encounters: list[Encounter],
    explanations: list[Explanation] = (),
) -> str | None:
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
    # One answer settles the concept, not one encounter. The question being
    # asked is "do you know idempotency?", and a concept met in three sessions
    # should not ask it three times -- which is also what stops a confirm on a
    # repeat concept looking like a button that did nothing.
    #
    # The other encounters stay unjudged in the data, honestly: we did not ask
    # about them. Re-asking at a distance is journey 8's spaced-retrieval loop,
    # which is deliberately not v1, and it needs to be driven by elapsed time
    # rather than by how many times a card happens to be on screen.
    if any(e.judgment is not None for e in encounters):
        return "learning"
    # Attempting the check is an answer to the question the card is asking, even
    # when the answer did not reach `causal`. A card that kept demanding a
    # verdict after you had just written an explanation for it would be asking
    # you to say twice what you already said once.
    if explanations:
        return "learning"
    return "open"


def _sessions_on_disk(root) -> set[str]:
    from ..capture import paths

    directory = paths.sessions_dir(paths.home(root))
    if not directory.is_dir():
        return set()
    return {p.stem for p in directory.glob("*.jsonl")}


def _repos(root) -> dict[str, str]:
    """session id -> the repo it ran in, from capture's own record of `cwd`."""
    from ..capture import paths

    path = paths.raw_db_path(paths.home(root))
    if not path.exists():
        return {}
    raw = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = raw.execute("SELECT session_id, cwd FROM raw_sessions WHERE cwd IS NOT NULL").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        raw.close()
    return {sid: cwd.rstrip("/").rsplit("/", 1)[-1] for sid, cwd in rows if cwd}


def concepts(conn: sqlite3.Connection, root=None) -> list[Concept]:
    """Every concept that belongs on the surface, newest activity first."""
    retained = _sessions_on_disk(root)
    repos = _repos(root)

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
                repo=repos.get(row["session_id"]) if row["session_id"] else None,
                spot_check=bool(row["spot_check"]),
            )
        )

    by_explained: dict[str, list[Explanation]] = {}
    for row in conn.execute(
        "SELECT * FROM compiled_explanations ORDER BY submitted_at"
    ):
        by_explained.setdefault(row["concept_id"], []).append(
            Explanation(
                explanation_id=row["explanation_id"],
                raw_text=row["raw_text"],
                prompt_text=row["prompt_text"],
                prompt_version=row["prompt_version"],
                submitted_at=row["submitted_at"],
                level=row["level"],
                reasoning=row["reasoning"],
                probabilities=json.loads(row["probabilities"])
                if row["probabilities"]
                else None,
                confidence=row["confidence"],
                grader_version=row["grader_version"],
            )
        )

    by_material: dict[str, list[MaterialOut]] = {}
    for row in conn.execute(
        "SELECT m.*, mc.concept_id FROM compiled_material m"
        " JOIN compiled_material_concepts mc ON mc.material_id = m.material_id"
        " ORDER BY m.generated_at DESC"
    ):
        by_material.setdefault(row["concept_id"], []).append(
            MaterialOut(
                material_id=row["material_id"],
                format=row["format"],
                body=row["body"],
                sources=json.loads(row["sources"] or "[]"),
                generated_at=row["generated_at"],
                delivered_at=row["delivered_at"],
            )
        )

    out: list[Concept] = []
    for row in conn.execute(
        "SELECT * FROM compiled_concepts WHERE merged_into IS NULL"
        " ORDER BY COALESCE(last_seen_at, '') DESC, canonical_name"
    ):
        found = by_concept.get(row["concept_id"], [])
        explained = by_explained.get(row["concept_id"], [])
        bucket = bucket_for(row["state"], found, explained)
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
                explanations=explained,
                # Only what was made FOR this concept. Material that merely
                # named it in passing is what made it `referenced`, and showing
                # that as its own material would present scaffolding as
                # something the user was taught.
                material=[
                    m
                    for m in by_material.get(row["concept_id"], [])
                    if m.sources or m.body
                ],
            )
        )
    return out


@dataclass
class Capture:
    sessions: int
    human_turns: int
    last_activity: str | None
    #: Sessions the detector has actually examined, and how many of those it
    #: examined and found nothing in. Real numbers since PR-21: the resolver
    #: appends `session_analysed` even when the detector emitted nothing, which
    #: is what lets silence here mean "we looked" rather than "no rows".
    sessions_analysed: int
    sessions_clean: int
    #: Sessions with something a person typed -- the only ones the detector can
    #: examine -- and how many of those are still waiting to be. The waiting
    #: count is `analyse.pending`, the watcher's own queue, so the surface and
    #: the queue cannot disagree about what has been looked at.
    sessions_analysable: int = 0
    sessions_waiting: int = 0
    #: Of the waiting, how many were examined once and have carried on since.
    sessions_grown: int = 0


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
    coverage = conn.execute(
        "SELECT count(*) AS analysed,"
        "       sum(CASE WHEN candidates_found = 0 THEN 1 ELSE 0 END) AS clean"
        "  FROM compiled_sessions"
    ).fetchone()
    analysed = coverage["analysed"] or 0
    clean = coverage["clean"] or 0
    if raw_conn is None:
        return Capture(0, 0, None, analysed, clean)
    row = raw_conn.execute("SELECT count(*) AS n FROM raw_sessions").fetchone()
    turns = raw_conn.execute(
        "SELECT count(*) AS n, count(DISTINCT session_id) AS sessions,"
        "       max(occurred_at) AS last FROM raw_turns"
        " WHERE role = 'user' AND is_meta = 0 AND is_sidechain = 0"
    ).fetchone()
    from ..analyse import pending

    waiting = pending(conn, raw_conn)
    grown = sum(1 for p in waiting if p.reason == "grown")
    return Capture(
        row["n"], turns["n"], turns["last"], analysed, clean, turns["sessions"], len(waiting), grown
    )


def _count(n: int, noun: str) -> str:
    """"1 session", "3 sessions". This is copy both surfaces show as written."""
    return f"{n} {noun}" + ("" if n == 1 else "s")


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
    # Fixtures stand in for a detector run that never happened, so they count as
    # coverage for the purpose of not telling a seeded store it has analysed
    # nothing. Real coverage is `sessions_analysed`.
    events = stats.sessions_analysed or seeded

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
            f"{_count(stats.sessions, 'session')} captured, none analysed",
            "Transcripts are on disk but nothing has looked at them yet."
            " This is not a clean bill of health \u2014 it is an empty one.",
            # No "run the resolver" here: the next step is each surface's to
            # give. The web page shows a command; the Mac app has a button, and
            # telling someone in a window to go and type a command is wrong.
        )
    elif counts["open"]:
        state, headline, detail = (
            "gaps",
            f"{counts['open']} waiting on you",
            "Each of these was leaned on in a session and waved through."
            " Say whether you actually knew it.",
        )
    elif stats.sessions_waiting:
        # Some examined, more not. Neither "clean" nor "cold start" is true yet:
        # a quiet list here is mostly a list nothing has looked at.
        found_nothing = stats.sessions_clean == stats.sessions_analysed
        state, headline, detail = (
            "not_analysed",
            f"{_count(stats.sessions_waiting, 'session')} waiting to be examined",
            f"{_count(stats.sessions_analysed, 'session')} examined so far"
            + (", nothing worth flagging in any of them." if found_nothing else ".")
            + (
                f" {stats.sessions_grown} of the waiting "
                + ("was examined before and has" if stats.sessions_grown == 1
                   else "were examined before and have")
                + " carried on since."
                if stats.sessions_grown else ""
            )
            + " Until the rest have been looked at, an empty list here says nothing"
            " either way.",
        )
    elif stats.sessions_analysed < COLD_START_SESSIONS:
        state, headline, detail = (
            "cold_start",
            "Not enough history yet",
            f"Only {_count(stats.sessions_analysed, 'session')} analysed so far. Too little"
            " to say much either way \u2014 come back after a few more.",
        )
    else:
        # Now sayable, and only because `session_analysed` is recorded for the
        # zero case too. Before that this could only ever claim the weaker
        # "nothing is waiting", which is also true of a detector that never ran.
        state, headline, detail = (
            "clean",
            "Nothing waiting on you",
            f"{_count(stats.sessions_analysed, 'session')} examined, {stats.sessions_clean}"
            " of them with nothing worth flagging."
            + (" Everything else found has been dealt with."
               if stats.sessions_clean < stats.sessions_analysed else "")
            + " Silence here means we looked.",
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
