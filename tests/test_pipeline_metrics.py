"""Tests for the pipeline report: each stage, from the log alone.

The report reads events other code writes, so the tests run the real pipeline
to write them -- a session where triage passes one candidate, holds one back
and spot-checks one -- then answer the cards and check that every number the
report prints traces back to that.
"""

from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from unrot import pipeline_metrics
from unrot.analyse import analyse_session
from unrot.api.app import create_app
from unrot.capture import paths
from unrot.capture.ingest import SCHEMA_PATH as RAW_SCHEMA
from unrot.resolver import manual, resolve, strict
from unrot.store import append, compile_state
from unrot.store.__main__ import open_store

triage_module = importlib.import_module("unrot.triage.triage")
WEEK = datetime.now(timezone.utc) - timedelta(days=7)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("UNROT_HOME", str(tmp_path))
    paths.ensure_layout(tmp_path)
    return tmp_path


@pytest.fixture
def conn(home):
    connection = open_store(home)
    yield connection
    connection.close()


@pytest.fixture
def raw(home):
    connection = sqlite3.connect(paths.raw_db_path(home))
    connection.row_factory = sqlite3.Row
    connection.executescript(RAW_SCHEMA.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO raw_sessions (session_id, source_path, copy_path,"
        " prefix_sha256, first_ingested_at, last_ingested_at)"
        " VALUES ('s0', '/x', '/y', '', '2026-09-01', '2026-09-01')"
    )
    for line_no, role, text in (
        (1, "assistant", "Postgres uses MVCC with a JOIN on an index, so readers never block." * 3),
        (2, "user", "ok ship it"),
    ):
        connection.execute(
            "INSERT INTO raw_turns (session_id, line_no, seq, role, text,"
            " is_meta, is_sidechain, occurred_at)"
            " VALUES ('s0', ?, 0, ?, ?, 0, 0, '2026-09-01')",
            (line_no, role, text),
        )
    connection.commit()
    yield connection
    connection.close()


def proposes(*terms):
    def propose(prompt_text):
        del prompt_text
        return [
            {"term": t, "paraphrase": f"{t}, leaned on.", "assistant_line": 1,
             "acceptance_line": 2, "signal": "accepted", "importance": "central"}
            for t in terms
        ]

    return propose


def judge(term, gloss, kmap):
    return {"p_knows": {"JOIN": 0.9, "database index": 0.8, "MVCC": 0.2}.get(term, 0.0),
            "confidence": 0.9, "model": "stub"}


judge.model = "stub"


@pytest.fixture
def analysed(conn, raw, monkeypatch):
    """One session: MVCC passed (p 0.2); JOIN and database index both over the cut.
    Both sit on the same line, so rank falls to the term: `database index` comes
    first and takes the session's one spot check, and JOIN is held back."""
    triage_module._CUTS.clear()
    monkeypatch.setattr(triage_module, "is_spot_check", lambda eid: True)
    for names, judgment in ((["Postgres", "SQL", "git"], "encounter_dismissed"),
                            (["write-ahead log", "Raft", "CRDT"], "encounter_confirmed")):
        for name in names:
            filed = resolve(conn, manual(name, f"what {name} is"), decide=strict)
            append(conn, judgment, {"encounter_id": filed.encounter_id})
    compile_state(conn)

    analysis = analyse_session(
        conn, raw, "s0", propose=proposes("JOIN", "database index", "MVCC"),
        decide=strict, detector_label="t", resolver_label="none",
        judge=judge, triage_label="stub",
    )
    yield {r.canonical_name: r.encounter_id for r in analysis.resolutions}
    triage_module._CUTS.clear()


def test_the_funnel_counts_what_each_stage_did(conn, analysed):
    report = pipeline_metrics.compute(conn, since=WEEK)

    assert (report.sessions, report.detector_calls, report.found, report.gaps) == (1, 1, 3, 3)
    assert (report.judged, report.passed, report.held_back, report.spot_checks) == (3, 1, 1, 1)
    assert report.not_judged == 0
    # MVCC and the database index spot check were filed; JOIN was held back.
    assert (report.filed, report.unanswered) == (2, 2)


def test_answers_become_precision_and_the_spot_check_is_kept_apart(conn, analysed):
    """A confirmed flag is the detector and triage right; a dismissed spot check
    is triage right to have held it back. They are different questions."""
    append(conn, "encounter_confirmed", {"encounter_id": analysed["MVCC"]})
    append(conn, "encounter_dismissed", {"encounter_id": analysed["database index"]})
    compile_state(conn)

    report = pipeline_metrics.compute(conn, since=WEEK)

    assert (report.flag_precision.part, report.flag_precision.whole) == (1, 1)
    assert (report.hold_back_precision.part, report.hold_back_precision.whole) == (1, 1)
    assert (report.confirmed, report.dismissed, report.unanswered) == (1, 1, 0)


def test_calibration_bands_passed_candidates_by_their_probability(conn, analysed):
    """MVCC passed at p 0.2 and was then dismissed: one dismissal in the 0.1-0.3 band."""
    append(conn, "encounter_dismissed", {"encounter_id": analysed["MVCC"]})
    compile_state(conn)

    bands = pipeline_metrics.compute(conn, since=WEEK).calibration

    assert (bands["0.1 to 0.3"].part, bands["0.1 to 0.3"].whole) == (1, 1)
    assert bands["under 0.1"].whole == 0 and bands["0.3 and over"].whole == 0


def test_each_stage_has_its_time_and_cost(conn):
    for purpose, ms, cost in (("detection", 40_000, 0.0006), ("detection", 20_000, 0.0004),
                              ("familiarity", 250, 0.00002), ("resolution", 9000, None)):
        append(conn, "model_called", {"purpose": purpose, "model": "m", "ok": True,
                                      "duration_ms": ms, **({"cost": cost} if cost else {})})

    stages = pipeline_metrics.compute(conn, since=WEEK).stages

    assert list(stages)[:3] == ["detection", "familiarity", "resolution"]
    assert (stages["detection"].calls, stages["detection"].median_ms) == (2, 30_000)
    assert stages["detection"].cost == pytest.approx(0.001)
    # Unknown is not zero: an unpriced call is counted as such.
    assert (stages["resolution"].cost, stages["resolution"].unpriced) == (0.0, 1)


def test_a_regenerated_session_counts_once(conn, raw, analysed):
    """Regeneration adds a detector run beside the last. The latest is what is
    on the surface, and the session is one session."""
    from unrot.regen import run

    run(conn, raw, propose=proposes("MVCC"), decide=strict, model_label="v2")

    report = pipeline_metrics.compute(conn, since=WEEK)
    assert (report.sessions, report.found) == (1, 1)


def test_the_app_serves_the_same_report(home, conn, analysed):
    body = TestClient(create_app()).get("/api/pipeline?days=7").json()

    assert body["spot_checks"] == 1 and body["held_back"] == 1
    assert body["flag_precision"] == {"part": 0, "whole": 0, "rate": None}
    assert set(body["stages"]) >= {"detection", "familiarity", "resolution"}
