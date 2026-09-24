"""Tests for wipe-and-rerun (dev builds only).

Three guarantees this operation makes, mirrored from `regen`'s own tests:

* **By default, user input survives.** A judged encounter, an explanation (and
  its grade), and a manual submission are all left alone -- the same promise
  `regen` makes, just enforced across the whole store in one pass instead of
  session by session.
* **Opting in to `discard_user_input` actually empties the store**, rather than
  leaving orphaned survivors (a judgment with nothing left to judge, an
  explanation naming a concept that no longer exists).
* **A wipe always leaves the store in a state `regen` can pick straight back
  up**: `already_done` is empty and every captured session shows up in
  `plan().to_run`, because that is how the app is meant to finish the job --
  through the existing regeneration endpoint, not a parallel pipeline.

Plus the two things nothing here is ever allowed to be careless about: a
backup is actually written and actually restorable, and the API route refuses
outright unless the core was started in dev mode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from unrot.api.app import create_app
from unrot.capture import paths
from unrot.capture.ingest import SCHEMA_PATH as RAW_SCHEMA
from unrot.regen import already_done, plan, wipe
from unrot.store import append, compile_state
from unrot.store.__main__ import open_store
from unrot.store.backup import backup_store


@pytest.fixture
def home(tmp_path):
    paths.ensure_layout(tmp_path)
    return tmp_path


@pytest.fixture
def conn(home):
    connection = open_store(home)
    yield connection
    connection.close()


def _seed_encounter(conn, *, encounter_id, concept_id, source, session_id="s0"):
    """One resolved encounter: concept, resolver judgment, and the encounter itself."""
    append(conn, "concept_created", {"concept_id": concept_id, "canonical_name": concept_id})
    append(
        conn,
        "resolver_judgment",
        {
            "input_text": concept_id,
            "decision": "new",
            "reasoning": "First time seeing this.",
            "concept_id": concept_id,
            "source": source,
        },
        provenance={"resolver_version": "resolver/test"},
    )
    payload = {
        "encounter_id": encounter_id,
        "concept_id": concept_id,
        "source": source,
        "paraphrase": f"Paraphrase for {encounter_id}.",
    }
    if source == "transcript":
        payload["pointer"] = {"session_id": session_id, "line_start": 1, "line_end": 2}
    append(
        conn, "encounter_recorded", payload,
        provenance={"detector_version": "detector/test" if source == "transcript" else "manual/0.1.0"},
    )


def _seed_session_analysed(conn, session_id):
    append(
        conn, "session_analysed",
        {"session_id": session_id, "candidates_found": 1},
        provenance={"detector_version": "detector/test"},
    )


def _seed_explanation(conn, concept_id, *, graded=True):
    event_id = append(
        conn, "explanation_submitted",
        {
            "concept_id": concept_id, "raw_text": "It means retries are safe.",
            "prompt_text": "What does it mean?", "prompt_version": "v1",
        },
    )
    if graded:
        append(
            conn, "explanation_graded",
            {"explanation_id": event_id, "rubric": "solo", "level": "causal"},
            provenance={"grader_version": "grader/test"},
        )
    return event_id


def _seed_material(conn, concept_id):
    material_id = "m-" + concept_id
    append(
        conn, "material_generated",
        {"material_id": material_id, "format": "textual_with_sources", "covers_concept_ids": [concept_id]},
    )
    append(conn, "material_delivered", {"material_id": material_id})
    return material_id


def _seed_ledger(conn, session_id="s0"):
    """`model_called` and `detector_ran`: never touched, by design."""
    append(conn, "model_called", {"purpose": "detect", "model": "m/test", "ok": True})
    append(
        conn, "detector_ran",
        {"session_id": session_id, "windows_examined": 1, "calls_made": 1, "candidates": []},
        provenance={"detector_version": "detector/test"},
    )


# ---------------------------------------------------------------------------
# Default wipe: user input survives
# ---------------------------------------------------------------------------


def test_a_judged_encounter_survives_a_default_wipe(conn):
    _seed_encounter(conn, encounter_id="e-judged", concept_id="c-judged", source="transcript")
    _seed_encounter(conn, encounter_id="e-unjudged", concept_id="c-unjudged", source="transcript")
    append(conn, "encounter_confirmed", {"encounter_id": "e-judged"})
    compile_state(conn)

    result = wipe(conn)

    remaining = {r["encounter_id"] for r in conn.execute("SELECT * FROM compiled_encounters")}
    assert remaining == {"e-judged"}
    assert conn.execute(
        "SELECT judgment FROM compiled_encounters WHERE encounter_id = 'e-judged'"
    ).fetchone()["judgment"] == "confirmed"
    assert result.user_input_discarded is False


def test_a_manual_submission_survives_a_default_wipe(conn):
    _seed_encounter(conn, encounter_id="e-manual", concept_id="c-manual", source="manual")
    _seed_encounter(conn, encounter_id="e-detected", concept_id="c-detected", source="transcript")
    compile_state(conn)

    wipe(conn)

    remaining = {r["encounter_id"] for r in conn.execute("SELECT * FROM compiled_encounters")}
    assert remaining == {"e-manual"}


def test_an_explanation_and_its_grade_survive_a_default_wipe(conn):
    _seed_encounter(conn, encounter_id="e-x", concept_id="c-x", source="transcript")
    _seed_explanation(conn, "c-x")
    compile_state(conn)

    wipe(conn)

    row = conn.execute("SELECT * FROM compiled_explanations").fetchone()
    assert row is not None
    assert row["raw_text"] == "It means retries are safe."
    assert row["level"] == "causal"
    # The concept the explanation names must still exist -- the whole reason
    # concept identity is not touched by default.
    assert conn.execute(
        "SELECT count(*) FROM compiled_concepts WHERE concept_id = 'c-x'"
    ).fetchone()[0] == 1


def test_session_coverage_and_material_are_discarded_by_default(conn):
    _seed_encounter(conn, encounter_id="e-x", concept_id="c-x", source="transcript")
    _seed_session_analysed(conn, "s0")
    _seed_material(conn, "c-x")
    compile_state(conn)
    assert conn.execute("SELECT count(*) FROM compiled_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM compiled_material").fetchone()[0] == 1

    wipe(conn)

    assert conn.execute("SELECT count(*) FROM compiled_sessions").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM compiled_material").fetchone()[0] == 0


def test_model_called_and_detector_ran_are_never_touched(conn):
    """The ledger `store/events.py` and `regen` both document as exempt forever."""
    _seed_ledger(conn)
    before = {
        row["event_id"]
        for row in conn.execute(
            "SELECT event_id FROM events WHERE event_type IN ('model_called', 'detector_ran')"
        )
    }
    assert len(before) == 2

    wipe(conn, discard_user_input=True)  # even the full wipe must not touch these

    after = {
        row["event_id"]
        for row in conn.execute(
            "SELECT event_id FROM events WHERE event_type IN ('model_called', 'detector_ran')"
        )
    }
    assert after == before


# ---------------------------------------------------------------------------
# Opt-in: a completely fresh store
# ---------------------------------------------------------------------------


def test_discard_user_input_empties_judgments_explanations_and_manual_submissions(conn):
    _seed_encounter(conn, encounter_id="e-judged", concept_id="c-judged", source="transcript")
    _seed_encounter(conn, encounter_id="e-manual", concept_id="c-manual", source="manual")
    append(conn, "encounter_confirmed", {"encounter_id": "e-judged"})
    _seed_explanation(conn, "c-judged")
    compile_state(conn)

    result = wipe(conn, discard_user_input=True)

    assert conn.execute("SELECT count(*) FROM compiled_encounters").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM compiled_concepts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM compiled_explanations").fetchone()[0] == 0
    assert result.user_input_discarded is True


def test_discard_user_input_leaves_no_orphaned_explanation(conn):
    """The reason concept identity is only ever wiped alongside explanations.

    An explanation names a `concept_id`; if that concept vanished while the
    explanation stayed, `compiled_explanations.concept_id` would point at
    nothing `compiled_concepts` knows about.
    """
    _seed_encounter(conn, encounter_id="e-x", concept_id="c-x", source="transcript")
    _seed_explanation(conn, "c-x")
    compile_state(conn)

    wipe(conn, discard_user_input=True)

    # Nothing survived to be orphaned in the first place.
    assert conn.execute("SELECT count(*) FROM compiled_explanations").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM compiled_concepts").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Handing off to regen
# ---------------------------------------------------------------------------


@pytest.fixture
def raw(home):
    conn = sqlite3.connect(paths.raw_db_path(home))
    conn.row_factory = sqlite3.Row
    conn.executescript(RAW_SCHEMA.read_text(encoding="utf-8"))
    for session_id in ("s0", "s1"):
        conn.execute(
            "INSERT INTO raw_sessions (session_id, source_path, copy_path,"
            " prefix_sha256, first_ingested_at, last_ingested_at)"
            " VALUES (?, '/x', '/y', '', '2026-09-01', '2026-09-01')",
            (session_id,),
        )
        conn.execute(
            "INSERT INTO raw_turns (session_id, line_no, seq, role, text,"
            " is_meta, is_sidechain, occurred_at)"
            " VALUES (?, 1, 0, 'user', 'ok go ahead', 0, 0, '2026-09-01')",
            (session_id,),
        )
    conn.commit()
    yield conn
    conn.close()


def test_a_wiped_store_makes_every_captured_session_due_a_rerun(conn, raw):
    _seed_encounter(conn, encounter_id="e-x", concept_id="c-x", source="transcript", session_id="s0")
    _seed_session_analysed(conn, "s0")
    _seed_session_analysed(conn, "s1")
    compile_state(conn)

    from unrot.detector import detector_version

    version = detector_version("v1")
    assert already_done(conn, version) == set()  # never actually run under "v1"

    wipe(conn)

    preview = plan(conn, raw, model_label="v1")
    assert preview.to_run == ["s0", "s1"]
    assert preview.already_done == 0


# ---------------------------------------------------------------------------
# The backup
# ---------------------------------------------------------------------------


def test_backup_store_writes_a_restorable_snapshot(conn, home):
    _seed_encounter(conn, encounter_id="e-x", concept_id="c-x", source="transcript")
    compile_state(conn)
    before = conn.execute("SELECT count(*) FROM events").fetchone()[0]

    dest = backup_store(conn, home)

    assert dest.exists()
    assert dest.parent == home / "backups"
    copy = sqlite3.connect(dest)
    try:
        after = copy.execute("SELECT count(*) FROM events").fetchone()[0]
    finally:
        copy.close()
    assert after == before == 3  # concept_created + resolver_judgment + encounter_recorded


def test_backup_survives_a_wipe_that_follows_it(conn, home):
    """The point of the backup: it is unaffected by whatever happens next."""
    _seed_encounter(conn, encounter_id="e-x", concept_id="c-x", source="transcript")
    compile_state(conn)

    dest = backup_store(conn, home)
    wipe(conn, discard_user_input=True)

    copy = sqlite3.connect(dest)
    try:
        n = copy.execute("SELECT count(*) FROM events").fetchone()[0]
    finally:
        copy.close()
    assert n == 3


# ---------------------------------------------------------------------------
# The API: dev mode only
# ---------------------------------------------------------------------------


def make_raw(home, sessions: int) -> None:
    conn = sqlite3.connect(paths.raw_db_path(home))
    conn.executescript(RAW_SCHEMA.read_text(encoding="utf-8"))
    for index in range(sessions):
        conn.execute(
            "INSERT INTO raw_sessions (session_id, source_path, copy_path,"
            " prefix_sha256, first_ingested_at, last_ingested_at)"
            " VALUES (?, '/x', '/y', '', '2026-09-01', '2026-09-01')",
            (f"s{index}",),
        )
        conn.execute(
            "INSERT INTO raw_turns (session_id, line_no, seq, role, text, is_meta,"
            " is_sidechain, occurred_at) VALUES (?, 1, 0, 'user', 'ok', 0, 0, '2026-09-01')",
            (f"s{index}",),
        )
        paths.copy_path(home, f"s{index}").write_text("{}\n", encoding="utf-8")
    conn.commit()
    conn.close()


@pytest.fixture
def api_home(tmp_path, monkeypatch):
    monkeypatch.setenv("UNROT_HOME", str(tmp_path))
    paths.ensure_layout(tmp_path)
    return tmp_path


@pytest.fixture
def client(api_home):
    return TestClient(create_app())


def test_dev_wipe_plan_refuses_without_dev_mode(client, api_home, monkeypatch):
    monkeypatch.delenv("UNROT_DEV_FEATURES", raising=False)
    make_raw(api_home, 1)
    response = client.get("/api/dev/wipe/plan")
    assert response.status_code == 403


def test_dev_wipe_refuses_without_dev_mode(client, api_home, monkeypatch):
    monkeypatch.delenv("UNROT_DEV_FEATURES", raising=False)
    make_raw(api_home, 1)
    response = client.post("/api/dev/wipe", json={})
    assert response.status_code == 403


def test_dev_wipe_plan_answers_once_dev_mode_is_on(client, api_home, monkeypatch):
    monkeypatch.setenv("UNROT_DEV_FEATURES", "1")
    make_raw(api_home, 2)
    response = client.get("/api/dev/wipe/plan")
    assert response.status_code == 200
    body = response.json()
    assert body["sessions_to_rerun"] == 2


def test_dev_wipe_backs_up_and_reports_removed_counts(client, api_home, monkeypatch):
    monkeypatch.setenv("UNROT_DEV_FEATURES", "1")
    make_raw(api_home, 1)
    connection = open_store(api_home)
    _seed_encounter(connection, encounter_id="e-x", concept_id="c-x", source="transcript", session_id="s0")
    _seed_session_analysed(connection, "s0")
    compile_state(connection)
    connection.close()

    response = client.post("/api/dev/wipe", json={"discard_user_input": False})
    assert response.status_code == 200
    body = response.json()

    assert body["encounters_removed"] == 1
    assert body["sessions_to_rerun"] == 1
    assert body["user_input_discarded"] is False
    backup_path = Path(body["backup_path"])
    assert backup_path.parent == api_home / "backups"
    assert backup_path.exists()
