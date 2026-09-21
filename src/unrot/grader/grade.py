"""Capturing an explanation, and grading it — as two separate things.

The split is the whole ticket. `explanation_submitted` carries what the person
wrote and what they were asked; it is a user event, ground truth, permanent, and
no regeneration may ever touch it. `explanation_graded` carries a level and the
version of whatever produced it; it is a system event, derived, and disposable
by design.

Keep the raw text and any future rubric can re-grade the entire history. Discard
it and the scale becomes permanent by accident, which is the precise failure S2
was resolved to avoid.

Two orderings in this module are load-bearing rather than incidental:

* **Store before grading, always.** The explanation is the thing that cannot be
  reconstructed -- the person typed it once and will not type it again. Grading
  is a network call that can fail, time out, or be unavailable because no key is
  configured. So the submission is appended and committed first, and grading is
  a separate step that may fail without costing anything. An ungraded
  explanation is a normal, recoverable state; a lost one is not.
* **Re-grading discards the old grades rather than layering over them.** They
  are system events, which S5 says a regeneration is free to replace, and the
  ticket's acceptance test is exactly this: delete every grade, re-grade from
  raw text, and arrive back at the same state. That only proves the raw layer is
  sufficient if the old grades are genuinely gone while it runs.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..store import SOLO_LEVELS, append, compile_state
from . import check as check_module
from . import prompt as prompt_module

GRADER_VERSION = "0.1.0"


def grader_version(model_label: str) -> str:
    """Which grader produced a level: version, model, and rubric.

    LLM grading drifts -- the same answer can land differently on a different
    day or a different model. Without this recorded, a silent model upgrade
    quietly changes the meaning of every historical grade, and nothing in the
    data would show it had happened.
    """
    return f"grader/{GRADER_VERSION}+{model_label}+{prompt_module.prompt_id()}"


@dataclass
class Explanation:
    explanation_id: str
    concept_id: str
    raw_text: str
    prompt_text: str
    prompt_version: str
    level: str | None = None
    reasoning: str | None = None
    probabilities: dict | None = None
    confidence: float | None = None
    grader_version: str | None = None


def question_for(conn: sqlite3.Connection, concept_id: str) -> str | None:
    """The exact words to put on screen, or None if there is no such concept."""
    row = conn.execute(
        "SELECT canonical_name FROM compiled_concepts WHERE concept_id = ?",
        (concept_id,),
    ).fetchone()
    return None if row is None else check_module.question(row["canonical_name"])


def submit(
    conn: sqlite3.Connection,
    concept_id: str,
    raw_text: str,
    *,
    encounter_id: str | None = None,
    prompt_text: str | None = None,
    origin: str = "local",
    recompile: bool = True,
) -> str:
    """Record what the person wrote, and what they were asked. Never overwritten.

    `prompt_text` is stored verbatim beside the answer rather than only as a
    version tag. A tag is opaque to a future re-grader, and the same answer
    means different things depending on the question that produced it -- it is a
    few dozen bytes, and it is the entire reason the re-grade path works at all.
    """
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("an empty explanation is not an answer")

    asked = prompt_text or question_for(conn, concept_id)
    if asked is None:
        raise ValueError(f"no concept {concept_id!r} to explain")

    payload = {
        "concept_id": concept_id,
        "raw_text": text,
        "prompt_text": asked,
        "prompt_version": check_module.check_version(),
    }
    if encounter_id:
        payload["encounter_id"] = encounter_id

    explanation_id = append(
        conn, "explanation_submitted", payload, subject_id=concept_id, origin=origin
    )
    conn.commit()
    if recompile:
        compile_state(conn)
    return explanation_id


def ungraded(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Explanations with no level yet. A normal state, not an error."""
    return list(
        conn.execute(
            "SELECT * FROM compiled_explanations WHERE level IS NULL"
            " ORDER BY submitted_at"
        )
    )


def grade(
    conn: sqlite3.Connection,
    explanation_id: str,
    *,
    grade_fn,
    model_label: str = "none",
    origin: str = "local",
    recompile: bool = True,
) -> Explanation:
    """Grade one stored explanation, from its own stored question.

    Reads the question back out of the store rather than re-deriving it from
    today's wording. An answer written under an older question must be graded
    against that question, or changing the wording would retroactively corrupt
    every grade before it -- which is the one thing the verbatim storage exists
    to prevent.
    """
    row = conn.execute(
        "SELECT * FROM compiled_explanations WHERE explanation_id = ?",
        (explanation_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no explanation {explanation_id!r}")

    answer = grade_fn(row["prompt_text"], row["raw_text"]) or {}
    level = str(answer.get("level") or "").strip().lower()
    if level not in SOLO_LEVELS:
        # The model is untrusted here as everywhere else. An unrecognised level
        # becomes the lowest one rather than being invented: over-crediting
        # comprehension is the failure this check exists to prevent, so the
        # conservative direction is down.
        level = "isolated"

    reasoning = str(answer.get("reasoning") or "").strip()
    payload = {
        "explanation_id": explanation_id,
        "rubric": prompt_module.RUBRIC,
        "level": level,
        "reasoning": reasoning,
    }
    # Only a classifier supplies these. Recorded when present rather than
    # normalised into existence: an absent distribution is meaningfully
    # different from a flat one, and inventing it would make a reasoning
    # model's grade look like a calibrated one.
    if answer.get("probabilities"):
        payload["probabilities"] = answer["probabilities"]
        payload["confidence"] = answer.get("confidence")
    append(
        conn,
        "explanation_graded",
        payload,
        subject_id=row["concept_id"],
        origin=origin,
        provenance={"grader_version": grader_version(model_label)},
    )
    conn.commit()
    if recompile:
        compile_state(conn)

    return Explanation(
        explanation_id=explanation_id,
        concept_id=row["concept_id"],
        raw_text=row["raw_text"],
        prompt_text=row["prompt_text"],
        prompt_version=row["prompt_version"],
        level=level,
        reasoning=reasoning,
        probabilities=answer.get("probabilities"),
        confidence=answer.get("confidence"),
        grader_version=grader_version(model_label),
    )


def regrade(
    conn: sqlite3.Connection,
    *,
    grade_fn,
    model_label: str = "none",
    origin: str = "local",
) -> list[Explanation]:
    """Throw away every grade and rebuild them all from the raw answers.

    The ticket's proof that the raw layer is genuinely sufficient: if this
    reproduces the state, then nothing irreplaceable was ever living in a grade.
    It is also what PR-26's regeneration will do to this layer.

    Deleting `explanation_graded` rows is a deliberate exception to append-only,
    and a narrow one -- they are `system` events, which S5 defines as derived and
    replaceable. Nothing here touches an `explanation_submitted` event, and the
    guard below makes that a check rather than an intention.
    """
    before = conn.execute(
        "SELECT count(*) FROM events WHERE event_type = 'explanation_submitted'"
    ).fetchone()[0]

    conn.execute("DELETE FROM events WHERE event_type = 'explanation_graded'")
    conn.commit()
    compile_state(conn)

    out = [
        grade(
            conn,
            row["explanation_id"],
            grade_fn=grade_fn,
            model_label=model_label,
            origin=origin,
            recompile=False,
        )
        for row in conn.execute(
            "SELECT explanation_id FROM compiled_explanations ORDER BY submitted_at"
        ).fetchall()
    ]

    after = conn.execute(
        "SELECT count(*) FROM events WHERE event_type = 'explanation_submitted'"
    ).fetchone()[0]
    if after != before:
        raise RuntimeError(
            f"re-grading changed the number of submitted explanations ({before} -> {after});"
            " ground truth must survive a regrade untouched"
        )

    compile_state(conn)
    return out


def history(conn: sqlite3.Connection, concept_id: str) -> list[sqlite3.Row]:
    """Every explanation for a concept, oldest first.

    Kept as a list rather than collapsed to the latest: journey 8's spaced
    retrieval is about whether an answer given weeks apart got better, and that
    comparison needs both answers to still exist.
    """
    return list(
        conn.execute(
            "SELECT * FROM compiled_explanations WHERE concept_id = ?"
            " ORDER BY submitted_at",
            (concept_id,),
        )
    )
