"""Fixture events, so the surface has something to render before PR-21 exists.

**This is not the resolver.** PR-21 is the single write path into the graph, and
it is not built yet -- which means nothing currently turns detector output into
events, which means the event log is empty and a UI over it renders nothing.
This module fills that hole for development only.

Two rules keep it from quietly becoming a second write path:

* Every event goes through `events.append` like everything else. Nothing here
  reaches into `INSERT INTO events` directly, so the actor rules, the payload
  validation and the provenance requirements all still apply.
* Every event is stamped `origin='fixture'`, which makes the fake data
  identifiable in one query and removable in one command. `seed --clear` deletes
  exactly those rows. That is the one place we knowingly break append-only, and
  it is defensible because removing something that was never history is not the
  same as rewriting history.

Where real captured sessions exist, fixtures **anchor to them**: real session
ids, real line numbers. That matters more than it sounds -- it means the S4
pointer path (paraphrase on the card, raw transcript behind it) is exercised
against real data rather than stubbed, so the moment view is being tested rather
than mocked. With no capture, it falls back to synthetic pointers that resolve
to nothing, which the surface must also handle.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .events import append
from .ids import new_ulid

#: Stamped on every fixture event. The whole cleanup story hangs off this.
FIXTURE_ORIGIN = "fixture"

FIXTURE_DETECTOR = {"detector_version": "detector/0.1.0+fixture+fixture"}
FIXTURE_PROMPT = "In one line, what is it?"


@dataclass(frozen=True)
class _Anchor:
    """A real place in a real transcript, or a synthetic stand-in for one."""

    session_id: str
    line_start: int
    line_end: int
    real: bool


def _anchors(raw_conn: sqlite3.Connection | None, wanted: int) -> list[_Anchor]:
    """Find real (assistant said X, human replied) pairs to point the fixtures at.

    The same pairing the detector's windows use, but expressed as a query here
    rather than imported, so that the store package stays free of any dependency
    on the detector. A fixture that needed the detector installed to run would be
    a worse development tool.
    """
    if raw_conn is None:
        return [_Anchor(f"fixture-session-{i}", 1, 2, False) for i in range(wanted)]

    rows = list(
        raw_conn.execute(
            "SELECT a.session_id, a.line_no AS assistant_line,"
            "       MIN(h.line_no) AS human_line"
            "  FROM raw_turns a"
            "  JOIN raw_turns h ON h.session_id = a.session_id"
            "   AND h.line_no > a.line_no AND h.role = 'user'"
            "   AND h.is_meta = 0 AND h.is_sidechain = 0 AND h.text IS NOT NULL"
            " WHERE a.role = 'assistant' AND a.text IS NOT NULL"
            "   AND length(a.text) > 400"
            " GROUP BY a.session_id, a.line_no"
            " ORDER BY a.session_id, a.line_no"
        )
    )
    # One per session before a second from any session, so the fixture list looks
    # like several days of use rather than one very confusing afternoon.
    by_session: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_session.setdefault(row["session_id"], []).append(row)

    spread: list[sqlite3.Row] = []
    depth = 0
    while len(spread) < wanted and any(len(v) > depth for v in by_session.values()):
        for candidates in by_session.values():
            if len(candidates) > depth:
                spread.append(candidates[depth])
            if len(spread) >= wanted:
                break
        depth += 1

    anchors = [
        _Anchor(r["session_id"], r["assistant_line"], r["human_line"], True)
        for r in spread[:wanted]
    ]
    while len(anchors) < wanted:
        anchors.append(_Anchor(f"fixture-session-{len(anchors)}", 1, 2, False))
    return anchors


#: (canonical name, paraphrase). Paraphrases are written to the S4 rule: they
#: must stand alone on a phone with no code in front of you, because on the
#: portable layer the pointer cannot resolve and a paraphrase that only works
#: beside the transcript makes the remote experience empty.
_CONCEPTS = [
    (
        "idempotency",
        "The retry logic was written assuming a repeated request is harmless."
        " Whether that holds decides if a retry storm is a recovery or an outage.",
    ),
    (
        "backpressure",
        "The queue was given a bounded size, so a slow consumer now pushes back"
        " on the producer instead of growing memory until something dies.",
    ),
    (
        "optimistic locking",
        "Concurrent edits were handled by letting both proceed and rejecting the"
        " loser at write time, rather than by making the second one wait.",
    ),
    (
        "write-ahead logging",
        "Durability was taken to mean the change is recorded before it is applied,"
        " so a crash mid-write leaves something to replay rather than a torn file.",
    ),
    (
        "cardinality",
        "A metric was labelled per-user, which multiplies the number of distinct"
        " time series stored -- the thing that usually makes a metrics bill explode.",
    ),
]


def seed(
    conn: sqlite3.Connection,
    *,
    raw_conn: sqlite3.Connection | None = None,
) -> dict[str, int]:
    """Append a spread of fixture events covering every state the surface renders.

    Deliberately not five identical unjudged gaps. The surface has to distinguish
    unjudged from confirmed from dismissed, has to treat a manually-submitted gap
    identically to a detected one, and has to not surface a `referenced` concept
    at all -- none of which is visible in a fixture set that is all one shape.
    """
    anchors = _anchors(raw_conn, len(_CONCEPTS) + 1)
    counts = {"concepts": 0, "encounters": 0, "judgments": 0}

    def _append(event_type: str, payload: dict, **kwargs) -> str:
        return append(conn, event_type, payload, origin=FIXTURE_ORIGIN, **kwargs)

    def _encounter(concept_id: str, paraphrase: str, anchor: _Anchor, source: str) -> str:
        encounter_id = "e-" + new_ulid()
        payload = {
            "encounter_id": encounter_id,
            "concept_id": concept_id,
            "source": source,
            "paraphrase": paraphrase,
        }
        # A manual submission has no transcript behind it by definition -- the
        # user heard the term in a meeting. Giving one a pointer would be a
        # fixture that lies, and would hide the branch where the paraphrase has
        # to carry the whole card on its own.
        if source != "manual":
            payload["pointer"] = {
                "session_id": anchor.session_id,
                "line_start": anchor.line_start,
                "line_end": anchor.line_end,
            }
        _append("encounter_recorded", payload, provenance=FIXTURE_DETECTOR)
        counts["encounters"] += 1
        return encounter_id

    ids = []
    for index, (name, paraphrase) in enumerate(_CONCEPTS):
        concept_id = "c-" + name.replace(" ", "-")
        _append("concept_created", {"concept_id": concept_id, "canonical_name": name})
        counts["concepts"] += 1
        # Manual submission (journey 10) must land in the same list, treated the
        # same way. One fixture is manual so that "indistinguishable in treatment"
        # is something the surface is tested against rather than assumed.
        source = "manual" if name == "cardinality" else "transcript"
        ids.append((concept_id, _encounter(concept_id, paraphrase, anchors[index], source)))

    # Repetition is the signal S1 exists to preserve: the same concept tripping
    # you up twice in different sessions is categorically different from once.
    _encounter(
        ids[0][0],
        "Came up again in a different service -- the same assumption about repeated"
        " delivery being safe, this time in the webhook handler.",
        anchors[-1],
        "transcript",
    )

    # Journey 1: a flag you accept. Stays a gap -- confirming means "I genuinely
    # do not know this", which is the start of learning it, not the end.
    _append("encounter_confirmed", {"encounter_id": ids[1][1]})
    counts["judgments"] += 1

    # Journey 2: a false positive. Dismissal compiles the concept to `known`.
    _append("encounter_dismissed", {"encounter_id": ids[3][1]})
    counts["judgments"] += 1

    # `referenced`: named in material, never encountered. Scaffolding -- it must
    # never appear in the gap list, and seeding one is how we find out if it does.
    _append(
        "concept_created",
        {"concept_id": "c-quorum", "canonical_name": "quorum"},
    )
    counts["concepts"] += 1
    material_id = "m-" + new_ulid()
    _append(
        "material_generated",
        {
            "material_id": material_id,
            "format": "sources_only",
            "covers_concept_ids": ["c-quorum"],
            "sources": ["https://jepsen.io/consistency"],
        },
    )

    conn.commit()
    return counts


def clear(conn: sqlite3.Connection) -> int:
    """Delete every fixture event. Returns how many went.

    The caller recompiles afterwards -- compiled state is a pure function of the
    log, so removing events and re-folding is all that "undo the fixtures" means.
    """
    cursor = conn.execute("DELETE FROM events WHERE origin = ?", (FIXTURE_ORIGIN,))
    conn.commit()
    return cursor.rowcount


def count(conn: sqlite3.Connection) -> int:
    """How many fixture events are in the log. Zero means the store is honest."""
    return conn.execute(
        "SELECT count(*) FROM events WHERE origin = ?", (FIXTURE_ORIGIN,)
    ).fetchone()[0]
