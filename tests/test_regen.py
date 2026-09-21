"""Tests for regeneration (PR-26).

S5's claim is that the store is derived and re-runnable, and every modelling
decision in S1–S4 was made cheap on the strength of it. This is where that
claim is either true or has been quietly false the whole time.

One guarantee dominates: **a user judgment survives, and the encounter carrying
it is untouched.** Confirms, dismissals and explanations are the only ground
truth in the system. Most of these tests are the same assertion approached from
different directions, because there is no partial credit — an encounter is
either byte-identical afterwards or the guarantee is gone.
"""

from __future__ import annotations

import json

import pytest

from unrot.capture import paths
from unrot.capture.ingest import SCHEMA_PATH as RAW_SCHEMA
from unrot.grader import grade, submit
from unrot.regen import already_done, judged_in, regenerate, run
from unrot.resolver import strict
from unrot.store import append, compile_state
from unrot.store.__main__ import open_store

import sqlite3


@pytest.fixture
def home(tmp_path):
    paths.ensure_layout(tmp_path)
    return tmp_path


@pytest.fixture
def raw(home):
    """A capture store with two sessions, each an exchange a person replied to."""
    conn = sqlite3.connect(paths.raw_db_path(home))
    conn.row_factory = sqlite3.Row
    conn.executescript(RAW_SCHEMA.read_text(encoding="utf-8"))
    for session in ("s0", "s1"):
        conn.execute(
            "INSERT INTO raw_sessions (session_id, source_path, copy_path,"
            " prefix_sha256, first_ingested_at, last_ingested_at)"
            " VALUES (?, '/x', '/y', '', '2026-09-01', '2026-09-01')",
            (session,),
        )
        rows = [
            (1, "assistant", "We will make the handler idempotent before retrying." * 4, 0),
            (2, "user", "ok go ahead with that", 0),
        ]
        for line_no, role, text, meta in rows:
            conn.execute(
                "INSERT INTO raw_turns (session_id, line_no, seq, role, text,"
                " is_meta, is_sidechain, occurred_at)"
                " VALUES (?, ?, 0, ?, ?, ?, 0, '2026-09-01')",
                (session, line_no, role, text, meta),
            )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def conn(home):
    connection = open_store(home)
    yield connection
    connection.close()


def proposer(term="idempotency", paraphrase="Retries were assumed harmless."):
    """A detector that always flags the same term at line 1."""

    def propose(prompt_text):
        del prompt_text
        return [
            {
                "term": term,
                "paraphrase": paraphrase,
                "assistant_line": 1,
                "acceptance_line": 2,
                "signal": "accepted",
                "importance": "central",
            }
        ]

    return propose


def silent(prompt_text):
    """A detector that now believes the session was clean."""
    del prompt_text
    return []


def first_pass(conn, raw, **kwargs):
    return run(
        conn, raw, propose=proposer(), decide=strict, model_label="v1", **kwargs
    )


# ---------------------------------------------------------------------------
# The guarantee
# ---------------------------------------------------------------------------


def test_a_judged_encounter_is_untouched_by_a_re_run(conn, raw):
    """Byte-identical afterwards: paraphrase, pointer, judgment, all of it.

    The named acceptance criterion, and the one with no partial credit.
    """
    first_pass(conn, raw)
    target = conn.execute(
        "SELECT * FROM compiled_encounters WHERE session_id = 's0'"
    ).fetchone()
    append(conn, "encounter_confirmed", {"encounter_id": target["encounter_id"]})
    compile_state(conn)
    before = dict(target)

    # A different detector, with a different opinion and different wording.
    run(
        conn,
        raw,
        propose=proposer("idempotency", "A COMPLETELY DIFFERENT PARAPHRASE."),
        decide=strict,
        model_label="v2",
    )

    after = dict(
        conn.execute(
            "SELECT * FROM compiled_encounters WHERE encounter_id = ?",
            (before["encounter_id"],),
        ).fetchone()
    )
    assert after["paraphrase"] == before["paraphrase"]
    assert after["judgment"] == "confirmed"
    assert after["detector_version"] == before["detector_version"]
    assert (after["session_id"], after["line_start"], after["line_end"]) == (
        before["session_id"],
        before["line_start"],
        before["line_end"],
    )


def test_every_judgment_made_before_a_re_run_is_still_attached_after(conn, raw):
    first_pass(conn, raw)
    rows = conn.execute("SELECT * FROM compiled_encounters ORDER BY session_id").fetchall()
    append(conn, "encounter_confirmed", {"encounter_id": rows[0]["encounter_id"]})
    append(conn, "encounter_dismissed", {"encounter_id": rows[1]["encounter_id"]})
    compile_state(conn)

    run(conn, raw, propose=proposer(), decide=strict, model_label="v2")

    judgments = {
        r["encounter_id"]: r["judgment"]
        for r in conn.execute("SELECT * FROM compiled_encounters")
    }
    assert judgments[rows[0]["encounter_id"]] == "confirmed"
    assert judgments[rows[1]["encounter_id"]] == "dismissed"


def test_an_explanation_and_its_grade_survive_a_re_run(conn, raw):
    """Explanations hang off concepts, so this checks the seam between them."""
    first_pass(conn, raw)
    concept = conn.execute("SELECT * FROM compiled_concepts").fetchone()
    explanation_id = submit(conn, concept["concept_id"], "It means retries are safe.")
    grade(
        conn,
        explanation_id,
        grade_fn=lambda q, a: {"level": "causal", "reasoning": "linked."},
        model_label="g",
    )

    run(conn, raw, propose=proposer(), decide=strict, model_label="v2")

    row = conn.execute(
        "SELECT * FROM compiled_explanations WHERE explanation_id = ?", (explanation_id,)
    ).fetchone()
    assert row["raw_text"] == "It means retries are safe."
    assert row["level"] == "causal"
    assert conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"] == "known"


def test_regeneration_deletes_only_what_s5_calls_replaceable(conn, raw):
    """A guard on the blast radius rather than a description of it.

    Widening what a re-run may delete is the kind of change that looks harmless
    and destroys the only irreplaceable data in the system.
    """
    first_pass(conn, raw)
    target = conn.execute("SELECT * FROM compiled_encounters").fetchone()
    append(conn, "encounter_confirmed", {"encounter_id": target["encounter_id"]})
    compile_state(conn)

    before = {
        row["event_id"]: row["event_type"]
        for row in conn.execute("SELECT event_id, event_type FROM events")
    }
    run(conn, raw, propose=silent, decide=strict, model_label="v2")
    after = set(row["event_id"] for row in conn.execute("SELECT event_id FROM events"))

    gone = {before[event_id] for event_id in before if event_id not in after}
    assert gone <= {"encounter_recorded", "session_analysed"}
    # And nothing a user wrote went with it.
    assert conn.execute(
        "SELECT count(*) FROM events WHERE actor = 'user'"
    ).fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Re-running actually changes things
# ---------------------------------------------------------------------------


def test_a_better_detector_retracts_a_gap_it_no_longer_believes_in(conn, raw):
    """Re-runnability has to be able to subtract, not only add.

    A regeneration that could only add would make every false positive
    permanent, which is the opposite of what improving the engine is for.
    """
    first_pass(conn, raw)
    assert conn.execute("SELECT count(*) FROM compiled_encounters").fetchone()[0] == 2

    run(conn, raw, propose=silent, decide=strict, model_label="v2")
    assert conn.execute("SELECT count(*) FROM compiled_encounters").fetchone()[0] == 0


def test_a_retraction_stops_at_anything_the_user_judged(conn, raw):
    """The two rules meeting. Protection wins."""
    first_pass(conn, raw)
    kept = conn.execute(
        "SELECT * FROM compiled_encounters WHERE session_id = 's0'"
    ).fetchone()
    append(conn, "encounter_dismissed", {"encounter_id": kept["encounter_id"]})
    compile_state(conn)

    run(conn, raw, propose=silent, decide=strict, model_label="v2")

    remaining = conn.execute("SELECT * FROM compiled_encounters").fetchall()
    assert [r["encounter_id"] for r in remaining] == [kept["encounter_id"]]
    assert remaining[0]["judgment"] == "dismissed"


def test_a_new_paraphrase_lands_on_an_unjudged_encounter(conn, raw):
    """Un-judged is where a re-run is allowed to improve things."""
    first_pass(conn, raw)
    run(
        conn,
        raw,
        propose=proposer("idempotency", "A much better paraphrase."),
        decide=strict,
        model_label="v2",
    )
    paraphrases = {
        r["paraphrase"] for r in conn.execute("SELECT * FROM compiled_encounters")
    }
    assert paraphrases == {"A much better paraphrase."}


def test_the_version_that_produced_each_gap_is_visible(conn, raw):
    first_pass(conn, raw)
    versions = {
        r["detector_version"] for r in conn.execute("SELECT * FROM compiled_encounters")
    }
    assert len(versions) == 1
    assert "+v1+" in versions.pop()


# ---------------------------------------------------------------------------
# Idempotence and resumption
# ---------------------------------------------------------------------------


def test_re_running_twice_does_not_inflate_the_repetition_signal(conn, raw):
    """Repetition is the most valuable signal the product has.

    Manufacturing duplicate encounters would make a re-run look like the user
    met the same thing twice as often, which is worse than not re-running.
    """
    first_pass(conn, raw)
    before = conn.execute(
        "SELECT encounter_count FROM compiled_concepts"
    ).fetchone()["encounter_count"]

    run(conn, raw, propose=proposer(), decide=strict, model_label="v2", force=True)
    run(conn, raw, propose=proposer(), decide=strict, model_label="v2", force=True)

    after = conn.execute(
        "SELECT encounter_count FROM compiled_concepts"
    ).fetchone()["encounter_count"]
    assert after == before == 2


def test_a_second_pass_at_the_same_version_skips_the_work(conn, raw):
    first_pass(conn, raw)
    again = first_pass(conn, raw)
    assert all(p.skipped for p in again.passes)
    assert again.recorded == 0


def test_a_pass_that_dies_halfway_leaves_a_log_that_still_folds(conn, raw):
    """Append-only makes a partial pass a shorter log, not corruption.

    Relied on deliberately: work commits per session, so stopping between
    sessions is a supported way to stop rather than an accident to recover from.
    """
    first_pass(conn, raw)

    steps = regenerate(
        conn, raw, propose=silent, decide=strict, model_label="v2"
    )
    done = next(steps)          # one session re-run and committed
    steps.close()               # and then the process goes away

    compile_state(conn)
    compile_state(conn)

    encounters = conn.execute(
        "SELECT session_id FROM compiled_encounters"
    ).fetchall()
    # The finished session was retracted; the untouched one is exactly as it was.
    assert {r["session_id"] for r in encounters} == {"s1"}
    assert done.session_id == "s0"


def test_an_interrupted_pass_resumes_where_it_stopped(conn, raw):
    """`session_analysed` is what makes this possible, and why it records a version."""
    first_pass(conn, raw)

    steps = regenerate(conn, raw, propose=silent, decide=strict, model_label="v2")
    next(steps)
    steps.close()

    from unrot.detector import detector_version

    remaining = set(("s0", "s1")) - already_done(conn, detector_version("v2"))
    assert remaining == {"s1"}

    finished = run(conn, raw, propose=silent, decide=strict, model_label="v2")
    assert [p.session_id for p in finished.passes if not p.skipped] == ["s1"]


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_progress_is_observable_as_it_happens(conn, raw):
    """Nobody waits on a screen for this, so a run in flight must be visible."""
    first_pass(conn, raw)
    seen = []
    run(
        conn,
        raw,
        propose=proposer(),
        decide=strict,
        model_label="v2",
        on_progress=lambda p, result: seen.append((p.done, p.remaining, result.session_id)),
    )
    assert seen == [(1, 1, "s0"), (2, 0, "s1")]


def test_the_protected_set_is_reported_not_just_honoured(conn, raw):
    """So a run says what it declined to touch, rather than being trusted about it."""
    first_pass(conn, raw)
    target = conn.execute("SELECT * FROM compiled_encounters WHERE session_id = 's0'").fetchone()
    append(conn, "encounter_confirmed", {"encounter_id": target["encounter_id"]})
    compile_state(conn)

    assert judged_in(conn, "s0") == {target["encounter_id"]}

    progress = run(conn, raw, propose=proposer(), decide=strict, model_label="v2")
    by_session = {p.session_id: p for p in progress.passes}
    assert by_session["s0"].protected == 1
    assert by_session["s0"].recorded == 0   # the only candidate was protected
    assert by_session["s1"].protected == 0
    assert by_session["s1"].recorded == 1
