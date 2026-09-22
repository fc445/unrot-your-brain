"""The week-of-self-use report (PR-30).

Most of these guard a way the numbers could mislead rather than a way they
could crash: fixtures counted as findings, a re-recorded flag counted twice, a
percentage over three flags presented as though it meant something.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from unrot.metrics import compute, render
from unrot.store import append, compile_state, fixtures
from unrot.store.__main__ import open_store

WEEK_START = datetime(2026, 9, 14, tzinfo=timezone.utc)
WEEK_END = datetime(2026, 9, 21, tzinfo=timezone.utc)
INSIDE = "2026-09-16T10:00:00.000+00:00"
BEFORE = "2026-09-01T10:00:00.000+00:00"


@pytest.fixture
def conn(tmp_path):
    connection = open_store(tmp_path)
    yield connection
    connection.close()


def flag(conn, eid, *, source="transcript", at=INSIDE, concept=None):
    concept = concept or f"c-{eid}"
    append(conn, "concept_created", {"concept_id": concept, "canonical_name": eid}, occurred_at=at)
    append(
        conn,
        "encounter_recorded",
        {"encounter_id": eid, "concept_id": concept, "source": source, "paraphrase": "p"},
        occurred_at=at,
        provenance={"detector_version": "detector/test"},
    )
    return concept


def report(conn):
    compile_state(conn)
    return compute(conn, since=WEEK_START, until=WEEK_END)


def test_engaged_means_confirmed_dismissed_or_explained(conn):
    flag(conn, "a")
    flag(conn, "b")
    explained = flag(conn, "c")
    flag(conn, "d")
    append(conn, "encounter_confirmed", {"encounter_id": "a"}, occurred_at=INSIDE)
    append(conn, "encounter_dismissed", {"encounter_id": "b"}, occurred_at=INSIDE)
    append(
        conn,
        "explanation_submitted",
        {"concept_id": explained, "raw_text": "x", "prompt_text": "q", "prompt_version": "c1"},
        occurred_at=INSIDE,
    )

    result = report(conn)

    assert (result.engaged.part, result.engaged.whole) == (3, 4)
    assert result.ignored == 1


def test_the_manual_share_answers_pr22s_question(conn):
    flag(conn, "a", source="manual")
    flag(conn, "b")
    flag(conn, "c")

    result = report(conn)

    assert (result.manual_share.part, result.manual_share.whole) == (1, 3)


def test_the_dismissal_rate_is_over_judged_detected_flags_only(conn):
    """A proxy for detector precision, so typed-in gaps say nothing about it."""
    flag(conn, "a")
    flag(conn, "b")
    flag(conn, "c")                    # detected, never judged: not in the denominator
    flag(conn, "m", source="manual")   # typed in: not the detector's doing
    append(conn, "encounter_dismissed", {"encounter_id": "a"}, occurred_at=INSIDE)
    append(conn, "encounter_confirmed", {"encounter_id": "b"}, occurred_at=INSIDE)
    append(conn, "encounter_dismissed", {"encounter_id": "m"}, occurred_at=INSIDE)

    result = report(conn)

    assert (result.dismissal_rate.part, result.dismissal_rate.whole) == (1, 2)


def test_an_undone_judgment_counts_as_an_undo_and_not_as_engagement(conn):
    flag(conn, "a")
    append(conn, "encounter_confirmed", {"encounter_id": "a"}, occurred_at=INSIDE)
    append(conn, "encounter_judgment_retracted", {"encounter_id": "a"}, occurred_at=INSIDE)

    result = report(conn)

    assert result.undone == 1
    assert result.engaged.part == 0


def test_a_flag_counts_in_the_week_it_first_appeared(conn):
    """Regeneration re-records under the same id; that is not a new flag."""
    flag(conn, "old", at=BEFORE)
    append(
        conn,
        "encounter_recorded",
        {"encounter_id": "old", "concept_id": "c-old", "source": "transcript", "paraphrase": "again"},
        occurred_at=INSIDE,
        provenance={"detector_version": "detector/test"},
    )

    assert report(conn).flags == 0


def test_fixtures_never_count_and_the_report_says_how_many_it_left_out(conn):
    fixtures.seed(conn)
    compile_state(conn)
    # A window wide enough to contain the seeded events, so the only thing
    # keeping them out of the figures is the exclusion itself.
    result = compute(
        conn,
        since=datetime(2000, 1, 1, tzinfo=timezone.utc),
        until=datetime(2100, 1, 1, tzinfo=timezone.utc),
    )

    assert result.flags == 0
    assert result.confirmed == 0
    assert result.fixtures_excluded > 0
    assert "fixture events left out" in render(result)


def test_sessions_are_counted_once_at_their_latest_look(conn):
    append(conn, "session_analysed", {"session_id": "s1", "candidates_found": 2},
           occurred_at=INSIDE, provenance={"detector_version": "d"})
    append(conn, "session_analysed", {"session_id": "s1", "candidates_found": 0},
           occurred_at=INSIDE, provenance={"detector_version": "d2"})
    append(conn, "session_analysed", {"session_id": "s2", "candidates_found": 1},
           occurred_at=INSIDE, provenance={"detector_version": "d"})

    result = report(conn)

    assert (result.sessions_analysed, result.sessions_clean) == (2, 1)


def test_a_small_week_reads_as_small(conn):
    flag(conn, "a")
    text = render(report(conn))

    assert "0 of 1 (0%)  -- too few to read much into" in text
    assert "none to count" in text  # nothing detected has been judged yet


def test_what_cannot_be_measured_is_said(conn):
    text = render(report(conn))

    assert "Inverted trust" in text and "Shame spiral" in text


def test_the_json_carries_numerators_and_denominators(conn):
    import json

    flag(conn, "a", source="manual")
    body = json.loads(report(conn).as_json())

    assert body["manual_share"] == {"part": 1, "whole": 1, "rate": 1.0}
