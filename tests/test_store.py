"""Tests for the event log and the compile step (PR-18).

Each test here corresponds to something a structural decision (S1-S7) claims is
true. They exist less to catch typos than to stop the guarantees quietly
eroding: if regeneration ever starts wiping user judgments, or the fold stops
being idempotent, the whole "the store is disposable" argument collapses and
every modelling mistake becomes permanent again.
"""

from __future__ import annotations

import json

import pytest

from unrot.store import (
    EventValidationError,
    append,
    compile_state,
    connect,
    derived_events,
    new_ulid,
    user_events,
)

DETECTOR = {"detector_version": "detector-0.1.0"}
GRADER = {"grader_version": "grader-0.1.0"}
PROMPT = "In one line, what is it?"


@pytest.fixture
def conn():
    connection = connect()
    yield connection
    connection.close()


def seed_one_gap(conn, *, concept="c-idempotency", name="idempotency"):
    """A concept encountered once in a transcript. No judgment yet."""
    append(conn, "concept_created", {"concept_id": concept, "canonical_name": name})
    encounter = "e-" + new_ulid()
    append(
        conn,
        "encounter_recorded",
        {
            "encounter_id": encounter,
            "concept_id": concept,
            "source": "transcript",
            "paraphrase": f"Claude used {name} to justify the retry design; user said 'ok go ahead'.",
            "pointer": {"session_id": "sess-1", "line_start": 40, "line_end": 58},
        },
        provenance=DETECTOR,
    )
    return concept, encounter


def dump(conn) -> dict:
    """Everything compiled, as comparable plain data."""
    tables = (
        "compiled_concepts",
        "compiled_encounters",
        "compiled_material",
        "compiled_material_concepts",
        "compiled_explanations",
    )
    return {
        table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in tables
    }


# --- the round trip --------------------------------------------------------


def test_round_trip_produces_expected_state(conn):
    concept, encounter = seed_one_gap(conn)
    append(conn, "encounter_confirmed", {"encounter_id": encounter})
    explanation = append(
        conn,
        "explanation_submitted",
        {
            "concept_id": concept,
            "encounter_id": encounter,
            "raw_text": "You can call it twice.",
            "prompt_text": PROMPT,
            "prompt_version": "v1",
        },
    )
    append(
        conn,
        "explanation_graded",
        {"explanation_id": explanation, "rubric": "solo-3", "level": "isolated"},
        provenance=GRADER,
    )

    result = compile_state(conn)
    assert result.event_count == 5

    row = conn.execute("SELECT * FROM compiled_concepts").fetchone()
    assert row["canonical_name"] == "idempotency"
    assert row["gap_type"] == "term"          # S3: term-shaped for v1, typed later
    assert row["state"] == "gap"              # confirmed + only isolated == still a gap
    assert row["encounter_count"] == 1
    assert row["latest_level"] == "isolated"

    enc = conn.execute("SELECT * FROM compiled_encounters").fetchone()
    assert enc["judgment"] == "confirmed"
    assert enc["session_id"] == "sess-1"      # S4 pointer survives the fold
    assert enc["line_start"] == 40
    assert enc["detector_version"] == "detector-0.1.0"   # S5


def test_causal_explanation_moves_concept_to_known(conn):
    concept, encounter = seed_one_gap(conn)
    append(conn, "encounter_confirmed", {"encounter_id": encounter})
    explanation = append(
        conn,
        "explanation_submitted",
        {
            "concept_id": concept,
            "raw_text": (
                "Calling it twice has the same effect as once, so a client can retry "
                "after a timeout without double-charging - hence the dedup key."
            ),
            "prompt_text": PROMPT,
            "prompt_version": "v1",
        },
    )
    append(
        conn,
        "explanation_graded",
        {"explanation_id": explanation, "rubric": "solo-3", "level": "causal"},
        provenance=GRADER,
    )
    compile_state(conn)

    state = conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"]
    assert state == "known"


def test_compiling_twice_is_idempotent(conn):
    concept, encounter = seed_one_gap(conn)
    append(conn, "encounter_confirmed", {"encounter_id": encounter})

    compile_state(conn)
    first = dump(conn)
    compile_state(conn)
    compile_state(conn)
    assert dump(conn) == first


# --- S1: a concept is not an encounter -------------------------------------


def test_concept_with_no_encounters_is_referenced_not_a_gap(conn):
    """S1's payoff. A concept named in material the user received, but never
    encountered by them, is valid state -- and must never be surfaced."""
    append(conn, "concept_created", {"concept_id": "c-quorum", "canonical_name": "quorum"})
    append(
        conn,
        "material_generated",
        {
            "material_id": "m-1",
            "format": "textual_with_sources",
            "covers_concept_ids": ["c-quorum"],
            "sources": ["https://example.invalid/paper"],
        },
    )
    append(conn, "material_delivered", {"material_id": "m-1"})
    compile_state(conn)

    row = conn.execute("SELECT * FROM compiled_concepts").fetchone()
    assert row["state"] == "referenced"
    assert row["encounter_count"] == 0


def test_encountered_concept_named_in_delivered_material_is_exposed(conn):
    concept, _ = seed_one_gap(conn, concept="c-ec", name="eventual consistency")
    append(
        conn,
        "material_generated",
        {"material_id": "m-1", "format": "sources_only", "covers_concept_ids": [concept]},
    )
    append(conn, "material_delivered", {"material_id": "m-1"})
    compile_state(conn)

    assert conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"] == "exposed"


def test_material_generated_but_not_delivered_does_not_expose(conn):
    concept, _ = seed_one_gap(conn)
    append(
        conn,
        "material_generated",
        {"material_id": "m-1", "format": "sources_only", "covers_concept_ids": [concept]},
    )
    compile_state(conn)
    assert conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"] == "gap"


# --- S5: user judgments are ground truth -----------------------------------


def test_user_and_derived_events_are_separable(conn):
    concept, encounter = seed_one_gap(conn)
    append(conn, "encounter_confirmed", {"encounter_id": encounter})
    append(
        conn,
        "explanation_submitted",
        {
            "concept_id": concept,
            "raw_text": "no idea really",
            "prompt_text": PROMPT,
            "prompt_version": "v1",
        },
    )

    assert {e["event_type"] for e in user_events(conn)} == {
        "encounter_confirmed",
        "explanation_submitted",
    }
    assert {e["event_type"] for e in derived_events(conn)} == {
        "concept_created",
        "encounter_recorded",
    }


def test_actor_is_a_property_of_the_event_not_the_caller(conn):
    """A detector must not be able to record a confirmation."""
    _, encounter = seed_one_gap(conn)
    append(conn, "encounter_confirmed", {"encounter_id": encounter})
    actor = conn.execute(
        "SELECT actor FROM events WHERE event_type = 'encounter_confirmed'"
    ).fetchone()["actor"]
    assert actor == "user"

    with pytest.raises(TypeError):
        append(conn, "encounter_confirmed", {"encounter_id": encounter}, actor="system")


def test_regeneration_replays_derived_events_and_keeps_judgments(conn):
    """S5's central claim, exercised end to end.

    Wipe every machine-produced event, replay them from a better detector, and
    the user's confirmation and explanation must still be attached afterwards.
    """
    concept, encounter = seed_one_gap(conn)
    append(conn, "encounter_confirmed", {"encounter_id": encounter})
    explanation = append(
        conn,
        "explanation_submitted",
        {
            "concept_id": concept,
            "raw_text": "something about retries",
            "prompt_text": PROMPT,
            "prompt_version": "v1",
        },
    )
    compile_state(conn)
    before = conn.execute("SELECT * FROM compiled_encounters").fetchone()
    assert before["judgment"] == "confirmed"

    # A re-run: drop machine output, replay with a newer detector version.
    conn.execute("DELETE FROM events WHERE actor = 'system'")
    append(conn, "concept_created", {"concept_id": concept, "canonical_name": "idempotency"})
    append(
        conn,
        "encounter_recorded",
        {
            "encounter_id": encounter,
            "concept_id": concept,
            "source": "transcript",
            "paraphrase": "A sharper paraphrase from a better detector.",
            "pointer": {"session_id": "sess-1", "line_start": 40, "line_end": 58},
        },
        provenance={"detector_version": "detector-0.2.0"},
    )
    compile_state(conn)

    after = conn.execute("SELECT * FROM compiled_encounters").fetchone()
    assert after["judgment"] == "confirmed"           # the human input survived
    assert after["judged_at"] == before["judged_at"]
    assert after["detector_version"] == "detector-0.2.0"   # the machine output improved
    assert conn.execute(
        "SELECT raw_text FROM compiled_explanations WHERE explanation_id = ?",
        (explanation,),
    ).fetchone()["raw_text"] == "something about retries"


def test_grades_can_be_thrown_away_and_recomputed_from_raw_text(conn):
    """S2's irreversible half: keep the raw answer and any future rubric can
    re-grade the entire history."""
    concept, encounter = seed_one_gap(conn)
    explanation = append(
        conn,
        "explanation_submitted",
        {
            "concept_id": concept,
            "raw_text": "You can call it twice, uses a key, it's for retries.",
            "prompt_text": PROMPT,
            "prompt_version": "v1",
        },
    )
    append(
        conn,
        "explanation_graded",
        {"explanation_id": explanation, "rubric": "solo-3", "level": "listed"},
        provenance=GRADER,
    )
    compile_state(conn)

    conn.execute("DELETE FROM events WHERE event_type = 'explanation_graded'")
    compile_state(conn)
    stripped = conn.execute("SELECT * FROM compiled_explanations").fetchone()
    assert stripped["level"] is None
    assert stripped["raw_text"] == "You can call it twice, uses a key, it's for retries."
    assert stripped["prompt_text"] == PROMPT   # the question survived with the answer

    append(
        conn,
        "explanation_graded",
        {"explanation_id": explanation, "rubric": "solo-3", "level": "listed"},
        provenance={"grader_version": "grader-0.2.0"},
    )
    compile_state(conn)
    regraded = conn.execute("SELECT * FROM compiled_explanations").fetchone()
    assert regraded["level"] == "listed"
    assert regraded["grader_version"] == "grader-0.2.0"


# --- S16: supersession is expressible, even though the policy is deferred ---


def test_superseding_a_paraphrase_destroys_nothing_and_orphans_nothing(conn):
    concept, encounter = seed_one_gap(conn)
    original = conn.execute(
        "SELECT event_id FROM events WHERE event_type = 'encounter_recorded'"
    ).fetchone()["event_id"]
    append(conn, "encounter_confirmed", {"encounter_id": encounter})

    better = append(
        conn,
        "encounter_recorded",
        {
            "encounter_id": encounter,
            "concept_id": concept,
            "source": "transcript",
            "paraphrase": "A clearer paraphrase produced by a later detector.",
            "pointer": {"session_id": "sess-1", "line_start": 40, "line_end": 58},
        },
        provenance={"detector_version": "detector-0.2.0"},
        supersedes=original,
    )
    compile_state(conn)

    row = conn.execute("SELECT * FROM compiled_encounters").fetchone()
    assert row["paraphrase"] == "A clearer paraphrase produced by a later detector."
    assert row["paraphrase_event_id"] == better
    assert row["paraphrase_superseded"] == 1
    # nothing destroyed: the original event is still in the log
    assert conn.execute(
        "SELECT COUNT(*) c FROM events WHERE event_id = ?", (original,)
    ).fetchone()["c"] == 1
    # nothing orphaned: the judgment is held against encounter_id, not the event
    assert row["judgment"] == "confirmed"


# --- resolver behaviour ----------------------------------------------------


def test_merge_folds_names_into_an_alias_and_keeps_old_ids_resolvable(conn):
    append(conn, "concept_created", {"concept_id": "c-k8s", "canonical_name": "K8s"})
    append(
        conn, "concept_created", {"concept_id": "c-kube", "canonical_name": "Kubernetes"}
    )
    append(
        conn,
        "encounter_recorded",
        {
            "encounter_id": "e-1",
            "concept_id": "c-k8s",
            "source": "transcript",
            "paraphrase": "used K8s without explaining it",
        },
        provenance=DETECTOR,
    )
    append(
        conn,
        "concept_merged",
        {
            "from_concept_id": "c-k8s",
            "into_concept_id": "c-kube",
            "reasoning": "K8s is the standard numeronym for Kubernetes.",
        },
    )
    compile_state(conn)

    survivor = conn.execute(
        "SELECT * FROM compiled_concepts WHERE concept_id = 'c-kube'"
    ).fetchone()
    assert "K8s" in json.loads(survivor["aliases"])
    assert survivor["encounter_count"] == 1     # the encounter followed the merge

    gone = conn.execute(
        "SELECT * FROM compiled_concepts WHERE concept_id = 'c-k8s'"
    ).fetchone()
    assert gone["merged_into"] == "c-kube"      # old id still resolves


def test_resolver_judgment_is_recorded_with_its_reasoning(conn):
    append(
        conn,
        "resolver_judgment",
        {
            "input_text": "something about backpressure?",
            "decision": "created_new_concept",
            "reasoning": "No existing concept was close; nearest was 'rate limiting'.",
        },
    )
    row = conn.execute(
        "SELECT payload FROM events WHERE event_type = 'resolver_judgment'"
    ).fetchone()
    assert "nearest was 'rate limiting'" in json.loads(row["payload"])["reasoning"]


# --- validation ------------------------------------------------------------


def test_derived_state_must_record_the_version_that_produced_it(conn):
    with pytest.raises(EventValidationError, match="detector_version"):
        append(
            conn,
            "encounter_recorded",
            {
                "encounter_id": "e-1",
                "concept_id": "c-1",
                "source": "transcript",
                "paraphrase": "x",
            },
        )


def test_missing_required_payload_is_rejected(conn):
    with pytest.raises(EventValidationError, match="prompt_text"):
        append(
            conn,
            "explanation_submitted",
            {"concept_id": "c-1", "raw_text": "x", "prompt_version": "v1"},
        )


def test_unknown_event_type_is_rejected(conn):
    with pytest.raises(EventValidationError, match="unknown event type"):
        append(conn, "user_vibed_at_it", {})


def test_grade_outside_the_rubric_is_rejected(conn):
    with pytest.raises(EventValidationError, match="not one of"):
        append(
            conn,
            "explanation_graded",
            {"explanation_id": "x", "rubric": "solo-3", "level": "enlightened"},
            provenance=GRADER,
        )


def test_empty_log_compiles_to_empty_state(conn):
    """Journey 3 depends on this: 'we looked and found nothing' must be a real,
    reachable state rather than an error."""
    result = compile_state(conn)
    assert result.event_count == 0
    assert result.compiled_through is None
    assert dump(conn) == {
        "compiled_concepts": [],
        "compiled_encounters": [],
        "compiled_material": [],
        "compiled_material_concepts": [],
        "compiled_explanations": [],
    }


def test_store_survives_reopening_and_reinitialising(tmp_path):
    """The initialisation path is re-entrant: opening an existing store must not
    clobber it, because `connect` runs the schema script every time."""
    path = tmp_path / "unrot.sqlite3"

    first = connect(path)
    concept, encounter = seed_one_gap(first)
    append(first, "encounter_confirmed", {"encounter_id": encounter})
    first.commit()
    first.close()

    second = connect(path)          # same schema script runs again
    result = compile_state(second)
    assert result.event_count == 3
    row = second.execute("SELECT * FROM compiled_encounters").fetchone()
    assert row["judgment"] == "confirmed"
    assert row["encounter_id"] == encounter
    second.close()


# ---------------------------------------------------------------------------
# Compiled-schema drift
# ---------------------------------------------------------------------------


def test_an_added_compiled_column_reaches_a_store_that_already_existed(tmp_path):
    """The bug class this guards against is invisible to every other test here.

    `CREATE TABLE IF NOT EXISTS` skips a table that exists, so a column added to
    a `compiled_*` table never appears in a store created before it. Tests build
    fresh databases and pass; real stores fail at runtime on the first query
    naming the column. Caught in the browser rather than the suite, which is
    exactly the wrong order.
    """
    import sqlite3

    from unrot.store import db

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    # A store from before `compiled_explanations.reasoning` existed, carrying an
    # event that the fold has to be able to write into the new shape.
    old.executescript(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,"
        " actor TEXT NOT NULL, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL,"
        " origin TEXT NOT NULL, subject_type TEXT, subject_id TEXT, supersedes TEXT,"
        " provenance TEXT, payload TEXT NOT NULL);"
        "CREATE TABLE compiled_explanations (explanation_id TEXT PRIMARY KEY,"
        " concept_id TEXT NOT NULL, encounter_id TEXT, raw_text TEXT NOT NULL,"
        " prompt_text TEXT NOT NULL, prompt_version TEXT NOT NULL,"
        " submitted_at TEXT NOT NULL, rubric TEXT, level TEXT, grader_version TEXT,"
        " graded_at TEXT);"
    )
    old.execute(
        "INSERT INTO events VALUES ('01OLD', 'concept_created', 'system',"
        " '2026-09-01', '2026-09-01', 'local', 'concept', 'c-x', NULL, NULL,"
        " '{\"concept_id\": \"c-x\", \"canonical_name\": \"idempotency\"}')"
    )
    old.commit()
    old.close()

    conn = db.connect(path)
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(compiled_explanations)")
    }
    assert "reasoning" in columns

    # And the rebuild refilled from the log rather than leaving it empty, so a
    # reader cannot mistake a migrated store for an empty one.
    names = [r["canonical_name"] for r in conn.execute("SELECT * FROM compiled_concepts")]
    assert names == ["idempotency"]
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    conn.close()


def test_reopening_an_up_to_date_store_does_not_rebuild_it(tmp_path):
    """The rebuild must be drift-triggered, not per-connection.

    `connect()` runs on every API request; re-folding the whole log each time
    would be silent, steadily growing waste.
    """
    from unrot.store import db

    path = tmp_path / "current.db"
    first = db.connect(path)
    append(first, "concept_created", {"concept_id": "c-x", "canonical_name": "x"})
    compile_state(first)
    first.close()

    second = db.connect(path)
    assert second.execute("PRAGMA user_version").fetchone()[0] == db.COMPILED_SCHEMA
    # Still there: nothing was dropped on the way in.
    assert second.execute("SELECT count(*) FROM compiled_concepts").fetchone()[0] == 1
    second.close()
