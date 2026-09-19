"""The event vocabulary, and the only function that writes to the log.

Nothing else in unrot inserts into `events`. Every write lands here, gets its
payload shape checked, and gets an `actor` assigned from the event's own
definition rather than from the caller.

That last part is the point. `actor` is what makes regeneration safe: a re-run
replays `system` events and leaves `user` events alone. If callers could pass
their own actor, a detector could one day write a confirmation and the only
ground truth in the system would be quietly contaminated. Here, the actor is a
property of the event type and callers cannot override it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .ids import new_ulid

USER = "user"
SYSTEM = "system"


@dataclass(frozen=True)
class EventSpec:
    """What an event type is: who may emit it, what it is about, what it carries."""

    name: str
    actor: str
    subject_type: str
    required: tuple[str, ...] = ()
    #: provenance keys that MUST be supplied (S5 — derived state carries its version)
    requires_provenance: tuple[str, ...] = ()


#: The event catalogue. Adding a row here is how the vocabulary grows; the
#: schema itself never changes, because the payload is JSON.
SPECS: dict[str, EventSpec] = {
    # -- concept lifecycle (derived; the resolver owns all of these) ----------
    "concept_created": EventSpec(
        "concept_created", SYSTEM, "concept", ("concept_id", "canonical_name")
    ),
    "alias_added": EventSpec("alias_added", SYSTEM, "concept", ("concept_id", "alias")),
    "concept_merged": EventSpec(
        "concept_merged", SYSTEM, "concept",
        ("from_concept_id", "into_concept_id", "reasoning"),
    ),
    # The resolver's reasoning is appended as a first-class event, not just its
    # outcome. "Merged X into Y because it looked like a misspelling" is a
    # judgment that can be wrong; storing only the merge leaves nothing to
    # correct against.
    "resolver_judgment": EventSpec(
        "resolver_judgment", SYSTEM, "concept", ("input_text", "decision", "reasoning")
    ),
    # -- encounters (derived) ------------------------------------------------
    "encounter_recorded": EventSpec(
        "encounter_recorded", SYSTEM, "encounter",
        ("encounter_id", "concept_id", "source", "paraphrase"),
        requires_provenance=("detector_version",),
    ),
    # -- user judgments. Ground truth. Never replayed, never overwritten. -----
    "encounter_confirmed": EventSpec(
        "encounter_confirmed", USER, "encounter", ("encounter_id",)
    ),
    "encounter_dismissed": EventSpec(
        "encounter_dismissed", USER, "encounter", ("encounter_id",)
    ),
    "explanation_submitted": EventSpec(
        "explanation_submitted", USER, "concept",
        # prompt_text is stored verbatim, not just prompt_version. A version tag
        # alone does not tell a future re-grader what was actually asked, and the
        # same answer means different things under different questions.
        ("concept_id", "raw_text", "prompt_text", "prompt_version"),
    ),
    "resolver_judgment_corrected": EventSpec(
        "resolver_judgment_corrected", USER, "concept", ("target_event_id", "reasoning")
    ),
    # -- grading (derived, disposable) ---------------------------------------
    "explanation_graded": EventSpec(
        "explanation_graded", SYSTEM, "concept",
        ("explanation_id", "rubric", "level"),
        requires_provenance=("grader_version",),
    ),
    # -- material (derived) --------------------------------------------------
    "material_generated": EventSpec(
        "material_generated", SYSTEM, "material",
        ("material_id", "format", "covers_concept_ids"),
    ),
    "material_delivered": EventSpec(
        "material_delivered", SYSTEM, "material", ("material_id",)
    ),
}

#: SOLO, collapsed to three levels. Changeable — the grade is derived, and the
#: raw explanation text is retained precisely so the whole history can be
#: re-graded under a different rubric.
SOLO_LEVELS = ("isolated", "listed", "causal")

#: Compiled concept states. `referenced` has no encounter; the others do.
CONCEPT_STATES = ("gap", "known", "referenced", "exposed")


class EventValidationError(ValueError):
    """Raised when an event would not round-trip through the compile step."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def append(
    conn: sqlite3.Connection,
    event_type: str,
    payload: dict,
    *,
    subject_id: str | None = None,
    occurred_at: str | None = None,
    origin: str = "local",
    provenance: dict | None = None,
    supersedes: str | None = None,
    event_id: str | None = None,
) -> str:
    """Append one event to the log. The only write path into `events`.

    `actor` is taken from the event's spec, never from the caller — see the
    module docstring for why that is not a convenience.
    """
    spec = SPECS.get(event_type)
    if spec is None:
        raise EventValidationError(
            f"unknown event type {event_type!r}; add it to SPECS if it is real"
        )

    missing = [key for key in spec.required if key not in payload]
    if missing:
        raise EventValidationError(
            f"{event_type} is missing required payload field(s): {', '.join(missing)}"
        )

    provenance = provenance or {}
    missing_prov = [key for key in spec.requires_provenance if key not in provenance]
    if missing_prov:
        raise EventValidationError(
            f"{event_type} is derived state and must record {', '.join(missing_prov)}; "
            "without it a model upgrade silently changes the meaning of history"
        )

    if event_type == "explanation_graded" and payload["level"] not in SOLO_LEVELS:
        raise EventValidationError(
            f"level {payload['level']!r} is not one of {SOLO_LEVELS}"
        )

    eid = event_id or new_ulid()
    when = occurred_at or _now()
    conn.execute(
        "INSERT INTO events (event_id, event_type, actor, occurred_at, recorded_at,"
        " origin, subject_type, subject_id, supersedes, provenance, payload)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            eid,
            event_type,
            spec.actor,
            when,
            _now(),
            origin,
            spec.subject_type,
            subject_id or _default_subject(spec, payload),
            supersedes,
            json.dumps(provenance, sort_keys=True) if provenance else None,
            json.dumps(payload, sort_keys=True),
        ),
    )
    return eid


def _default_subject(spec: EventSpec, payload: dict) -> str | None:
    for key in ("concept_id", "encounter_id", "material_id", "from_concept_id"):
        if key in payload:
            return payload[key]
    return None


def read_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every event, in total order. ULIDs sort chronologically, so this is the fold order."""
    return list(conn.execute("SELECT * FROM events ORDER BY event_id"))


def user_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Ground truth only. What a regeneration must leave untouched."""
    return list(
        conn.execute("SELECT * FROM events WHERE actor = 'user' ORDER BY event_id")
    )


def derived_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Machine output only. What a regeneration is free to replace."""
    return list(
        conn.execute("SELECT * FROM events WHERE actor = 'system' ORDER BY event_id")
    )
