"""Tests for triage: is a detected gap a gap for *this* person? (PR-34)

Triage stands between the detector and the resolver and can do one thing the
detector cannot: hold a candidate back because the person's own history says
they already know it. That power is also its risk -- a held-back real gap is
invisible, and nothing on the surface can reveal it. So most of these tests are
about restraint: no map means no holding back, a failure means no holding back,
an unreliable classifier means no holding back, and whatever *is* held back is
written down rather than vanishing.
"""

from __future__ import annotations

import importlib
import json
import sqlite3

import pytest

from unrot.analyse import analyse_session
from unrot.capture import paths
from unrot.capture.ingest import SCHEMA_PATH as RAW_SCHEMA
from unrot.detector.candidates import Candidate
from unrot.model import ModelConfig
from unrot.regen import run
from unrot.resolver import manual, resolve, strict
from unrot.store import append, compile_state
from unrot.store.__main__ import open_store
from unrot.triage import (
    CALIBRATE_EACH,
    DEFAULT_CUT,
    KnowledgeMap,
    build_familiarity,
    calibrate,
    familiarity_from_env,
    knowledge_map,
    triage,
)


#: The module, not the function of the same name that the package exports.
triage_module = importlib.import_module("unrot.triage.triage")
#: Kept before any fixture replaces it.
REAL_IS_SPOT_CHECK = triage_module.is_spot_check


@pytest.fixture
def home(tmp_path):
    paths.ensure_layout(tmp_path)
    return tmp_path


@pytest.fixture
def conn(home):
    connection = open_store(home)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def no_spot_checks(monkeypatch):
    """Which hold-backs become spot checks depends on a hash of the encounter id,
    so it would decide these tests by accident. Off unless a test asks for it."""
    monkeypatch.setattr(triage_module, "is_spot_check", lambda encounter_id: False)


@pytest.fixture(autouse=True)
def fresh_cuts():
    """Learned cuts are cached per process; no test may see another's."""
    triage_module._CUTS.clear()
    yield
    triage_module._CUTS.clear()


@pytest.fixture
def raw(home):
    """One captured session: an explanation, and a person waving it through."""
    connection = sqlite3.connect(paths.raw_db_path(home))
    connection.row_factory = sqlite3.Row
    connection.executescript(RAW_SCHEMA.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO raw_sessions (session_id, source_path, copy_path,"
        " prefix_sha256, first_ingested_at, last_ingested_at)"
        " VALUES ('s0', '/x', '/y', '', '2026-09-01', '2026-09-01')"
    )
    for line_no, role, text in (
        (1, "assistant", "Postgres uses MVCC, so the reader never blocks the writer here." * 3),
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


def person(conn, known=(), unknown=(), unjudged=()):
    """Build a history: concepts dismissed as known, confirmed as gaps, or left alone."""
    for names, judgment in ((known, "encounter_dismissed"), (unknown, "encounter_confirmed"), (unjudged, None)):
        for name in names:
            filed = resolve(conn, manual(name, f"what {name} is"), decide=strict)
            if judgment:
                append(conn, judgment, {"encounter_id": filed.encounter_id})
    compile_state(conn)


def candidate(term, *, line=1):
    return Candidate(
        term=term,
        paraphrase=f"{term}, leaned on without explanation.",
        session_id="s0",
        line_start=line,
        line_end=line + 1,
        signal="accepted",
        importance="central",
        detector_version="detector/test",
    )


def scripted(answers, *, calls=None):
    """A stand-in classifier: p(knows) by term, 0.0 for anything unlisted."""

    def judge(term, gloss, kmap):
        if calls is not None:
            calls.append((term, kmap))
        return {"p_knows": answers.get(term, 0.0), "confidence": 0.9, "model": "stub"}

    judge.model = "stub"
    return judge


def judged_events(conn):
    return [
        json.loads(row["payload"])
        for row in conn.execute(
            "SELECT payload FROM events WHERE event_type = 'familiarity_judged' ORDER BY event_id"
        )
    ]


A_MAP = dict(known=["Postgres", "SQL", "git"], unknown=["write-ahead log", "Raft", "CRDT"])


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------


def test_the_map_is_only_what_the_person_said(conn):
    """Dismissed is known, confirmed is not known, and an unjudged gap is
    neither -- it is the detector's opinion, and must not be fed back in as
    evidence for the detector's next opinion."""
    person(conn, known=["Postgres"], unknown=["MVCC"], unjudged=["idempotency"])

    kmap = knowledge_map(conn)

    assert [e.name for e in kmap.known] == ["Postgres"]
    assert [e.name for e in kmap.unknown] == ["MVCC"]
    assert kmap.known[0].gloss == "what Postgres is"


# ---------------------------------------------------------------------------
# Restraint
# ---------------------------------------------------------------------------


def test_with_no_map_nothing_is_asked_and_nothing_is_held_back(conn):
    """PR-34: without a person's history the classifier's "knows" is right
    about half the time. That is not a weak signal to use carefully; it is none."""
    person(conn, known=["Postgres"], unknown=["MVCC"])
    calls = []

    outcome = triage(
        conn, [candidate("a"), candidate("b"), candidate("c")],
        judge=scripted({"a": 1.0}, calls=calls), max_candidates=2, cache=None,
    )

    assert calls == []
    assert [c.term for c in outcome.emitted] == ["a", "b"]
    assert outcome.cut_source == "no map"
    assert judged_events(conn) == []


def test_a_failing_classifier_holds_nothing_back(conn):
    person(conn, **A_MAP)

    def broken(term, gloss, kmap):
        raise TimeoutError("jev is down")

    outcome = triage(conn, [candidate("MVCC")], judge=broken, max_candidates=2, cache=None)

    assert [c.term for c in outcome.emitted] == ["MVCC"]
    assert outcome.verdicts[0].reason == "failed"
    assert judged_events(conn) == []


def test_a_term_already_in_the_graph_is_not_second_guessed(conn):
    """The graph already knows what this person thinks of Postgres."""
    person(conn, **A_MAP)
    calls = []

    outcome = triage(
        conn, [candidate("postgres")],
        judge=scripted({}, calls=calls), max_candidates=2, cache=None,
    )

    assert calls == []
    assert outcome.verdicts[0].reason == "in the graph"
    assert [c.term for c in outcome.emitted] == ["postgres"]


# ---------------------------------------------------------------------------
# Holding back
# ---------------------------------------------------------------------------


def test_a_familiar_candidate_is_held_back_and_does_not_use_up_the_budget(conn):
    person(conn, **A_MAP)

    outcome = triage(
        conn, [candidate("database index"), candidate("MVCC"), candidate("write skew")],
        judge=scripted({"database index": 0.8, "MVCC": 0.1, "write skew": 0.05}),
        max_candidates=2, cache=None,
    )

    assert outcome.cut == DEFAULT_CUT and outcome.cut_source == "default"
    assert [v.candidate.term for v in outcome.held_back] == ["database index"]
    # The budget is spent on what survives, in the detector's own order.
    assert [c.term for c in outcome.emitted] == ["MVCC", "write skew"]


def test_what_is_held_back_is_written_down_not_dropped(conn):
    """Triage labels rather than deletes: the held-back candidate is a fact in
    the log, with the number and the cut, so a better cut later is a re-reading."""
    person(conn, **A_MAP)

    triage(
        conn, [candidate("database index"), candidate("MVCC")],
        judge=scripted({"database index": 0.8, "MVCC": 0.1}), max_candidates=2, cache=None,
    )

    events = {e["term"]: e for e in judged_events(conn)}
    assert events["database index"]["verdict"] == "held_back"
    assert events["MVCC"]["verdict"] == "passed"
    assert events["database index"]["p_knows"] == 0.8
    assert events["database index"]["cut"] == DEFAULT_CUT
    assert events["database index"]["map_known"] == 3
    assert events["MVCC"]["encounter_id"].startswith("e-")
    provenance = json.loads(conn.execute(
        "SELECT provenance FROM events WHERE event_type = 'familiarity_judged' LIMIT 1"
    ).fetchone()[0])
    assert provenance["triage_version"].startswith("triage/")


# ---------------------------------------------------------------------------
# Learning the cut from the map
# ---------------------------------------------------------------------------


def big_map(n=CALIBRATE_EACH):
    from unrot.triage import Entry

    return KnowledgeMap(
        tuple(Entry(f"known {i}") for i in range(n)),
        tuple(Entry(f"unknown {i}") for i in range(n)),
    )


def test_the_cut_is_learned_from_the_map_when_there_is_enough_of_it():
    """Each entry is judged against the rest, and the cut is the lowest one that
    holds back only what is really known."""
    kmap = big_map()
    answers = {**{f"known {i}": 0.3 for i in range(8)}, **{f"unknown {i}": 0.05 for i in range(8)}}

    cut, source = calibrate(kmap, scripted(answers))

    assert (cut, source) == (0.3, "map")


def test_a_map_the_classifier_cannot_read_holds_nothing_back(conn):
    """If no cut is precise enough for this person, the answer is not "use 0.5
    anyway" -- it is that this classifier cannot be trusted with them."""
    kmap = big_map()
    inverted = {**{f"known {i}": 0.1 for i in range(8)}, **{f"unknown {i}": 0.9 for i in range(8)}}

    assert calibrate(kmap, scripted(inverted)) == (None, "unreliable")


def test_a_small_map_uses_the_default_cut_without_calibrating():
    calls = []
    assert calibrate(big_map(3), scripted({}, calls=calls)) == (DEFAULT_CUT, "default")
    assert calls == []


def test_calibration_is_paid_for_once_per_map(conn):
    names = [f"k{i}" for i in range(CALIBRATE_EACH)], [f"u{i}" for i in range(CALIBRATE_EACH)]
    person(conn, known=names[0], unknown=names[1])
    calls = []
    judge = scripted({n: 0.9 for n in names[0]}, calls=calls)

    for term in ("a", "b"):
        triage(conn, [candidate(term)], judge=judge, max_candidates=2)

    calibrating = [c for c in calls if c[0] not in ("a", "b")]
    assert len(calibrating) == 2 * CALIBRATE_EACH


# ---------------------------------------------------------------------------
# In the pipeline
# ---------------------------------------------------------------------------


def proposes(*terms):
    def propose(prompt_text):
        del prompt_text
        return [
            {
                "term": term,
                "paraphrase": f"{term}, leaned on.",
                "assistant_line": 1,
                "acceptance_line": 2,
                "signal": "accepted",
                "importance": "central",
            }
            for term in terms
        ]

    return propose


def test_analysis_files_only_what_triage_lets_through(conn, raw):
    person(conn, **A_MAP)

    analysis = analyse_session(
        conn, raw, "s0",
        propose=proposes("database index", "MVCC"),
        decide=strict, detector_label="t", resolver_label="none",
        judge=scripted({"database index": 0.9}), triage_label="stub",
    )

    assert [r.canonical_name for r in analysis.resolutions] == ["MVCC"]
    assert [v.candidate.term for v in analysis.held_back] == ["database index"]
    held = conn.execute(
        "SELECT 1 FROM compiled_concepts WHERE canonical_name = 'database index'"
    ).fetchone()
    assert held is None
    found = conn.execute(
        "SELECT candidates_found FROM compiled_sessions WHERE session_id = 's0'"
    ).fetchone()[0]
    assert found == 1


def test_the_detection_record_marks_what_was_filed_not_what_the_budget_picked(conn, raw):
    """Export joins a user's verdict to `emitted`. A held-back candidate was
    never filed, so it must not be recorded as emitted."""
    person(conn, **A_MAP)

    analyse_session(
        conn, raw, "s0",
        propose=proposes("database index", "MVCC"),
        decide=strict, detector_label="t", resolver_label="none",
        judge=scripted({"database index": 0.9}), triage_label="stub",
    )

    [payload] = [
        json.loads(row["payload"])
        for row in conn.execute("SELECT payload FROM events WHERE event_type = 'detector_ran'")
    ]
    emitted = {c["term"]: c["emitted"] for c in payload["candidates"]}
    assert emitted == {"database index": False, "MVCC": True}


def test_analysis_without_a_judge_is_what_it_always_was(conn, raw):
    person(conn, **A_MAP)

    analysis = analyse_session(
        conn, raw, "s0", propose=proposes("database index"),
        decide=strict, detector_label="t", resolver_label="none",
    )

    assert [r.canonical_name for r in analysis.resolutions] == ["database index"]
    assert judged_events(conn) == []


def test_regeneration_replaces_a_sessions_familiarity_record(conn, raw):
    """Two runs, one answer per moment: the re-run's judgments replace the old."""
    person(conn, **A_MAP)
    judge = scripted({"database index": 0.9})

    for label in ("v1", "v2"):
        run(conn, raw, propose=proposes("database index", "MVCC"), decide=strict,
            model_label=label, judge=judge, triage_label="stub")

    # One record for the held-back term, not one per run. (MVCC is judged only
    # once: the first run filed it, so the re-run finds it in the graph.)
    assert [e["term"] for e in judged_events(conn)] == ["database index"]


# ---------------------------------------------------------------------------
# When it runs at all
# ---------------------------------------------------------------------------


def test_it_never_runs_against_a_local_endpoint(monkeypatch):
    """Jev is hosted. Someone who pointed unrot at a local model did it to keep
    their concept names on this machine."""
    monkeypatch.delenv("UNROT_FAMILIARITY", raising=False)
    local = ModelConfig(base_url="http://localhost:11434/v1", api_key="x")
    hosted = ModelConfig(api_key="x")

    assert familiarity_from_env(local) is None
    assert familiarity_from_env(hosted) is not None


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("UNROT_FAMILIARITY", "off")
    assert familiarity_from_env(ModelConfig(api_key="x")) is None


def test_it_needs_a_key(monkeypatch):
    monkeypatch.delenv("UNROT_FAMILIARITY", raising=False)
    assert familiarity_from_env(ModelConfig(api_key=None)) is None


def test_the_classifier_is_asked_the_measured_question():
    """The map goes in as text, known and not known, with the new concept last --
    the layout PR-34 measured -- and the answer comes back as a probability."""
    import httpx2

    from unrot.spend import Meter
    from unrot.triage import Entry

    sent = []

    def answer(request):
        sent.append(request)
        return httpx2.Response(200, json={
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"familiar": {"type": "choice", "choice": "does_not_know",
                                     "probabilities": {"knows": 0.12, "does_not_know": 0.88},
                                     "confidence": 0.8}},
            "usage": {"input_tokens": 300, "output_tokens": 30, "cost": 0.00002},
        })

    meter = Meter()
    judge = build_familiarity(
        ModelConfig(api_key="x"), meter=meter, transport=httpx2.MockTransport(answer)
    )
    kmap = KnowledgeMap((Entry("Postgres", "a relational database"),), (Entry("WAL"),))
    result = judge("MVCC", "readers don't block writers", kmap)

    [request] = sent
    assert str(request.url) == "https://openrouter.ai/api/v1/systemone"
    state = json.loads(request.content)["state"]
    assert "KNOWS:\n- Postgres: a relational database" in state
    assert "does NOT know:\n- WAL" in state
    assert state.endswith("NEW CONCEPT: MVCC: readers don't block writers")
    assert result == {"p_knows": 0.12, "confidence": 0.8, "model": "typesafe/jev-1.13-20260917"}
    [call] = meter.calls
    assert (call.purpose, call.cost) == ("familiarity", 0.00002)


# ---------------------------------------------------------------------------
# Spot checks: the only way to see what triage hides
# ---------------------------------------------------------------------------


@pytest.fixture
def spot_checks_on(monkeypatch):
    monkeypatch.setattr(triage_module, "is_spot_check", lambda encounter_id: True)


def test_a_spot_check_is_filed_and_does_not_take_a_gaps_place(conn, raw, spot_checks_on):
    """Triage would have held `database index` back. Asked instead, alongside the
    full budget of gaps -- it is a different question, not a gap."""
    person(conn, **A_MAP)

    analysis = analyse_session(
        conn, raw, "s0",
        propose=proposes("database index", "MVCC", "write skew"),
        decide=strict, detector_label="t", resolver_label="none",
        judge=scripted({"database index": 0.9}), triage_label="stub",
        max_candidates=2,
    )

    assert [r.canonical_name for r in analysis.resolutions] == ["MVCC", "write skew", "database index"]
    assert analysis.held_back == []
    assert {e["term"]: e["verdict"] for e in judged_events(conn)}["database index"] == "spot_check"
    flagged = conn.execute(
        "SELECT e.spot_check FROM compiled_encounters e JOIN compiled_concepts c USING (concept_id)"
        " WHERE c.canonical_name = 'database index'"
    ).fetchone()[0]
    assert flagged == 1


def test_the_surface_is_told_which_card_is_a_spot_check(conn, raw, home, spot_checks_on):
    from unrot.api import read

    person(conn, **A_MAP)
    analyse_session(
        conn, raw, "s0", propose=proposes("database index", "MVCC"),
        decide=strict, detector_label="t", resolver_label="none",
        judge=scripted({"database index": 0.9}), triage_label="stub",
    )

    cards = {c.name: c for c in read.concepts(conn, home)}
    assert cards["database index"].encounters[0].spot_check is True
    assert cards["MVCC"].encounters[0].spot_check is False


def test_at_most_one_spot_check_a_session(conn, spot_checks_on):
    person(conn, **A_MAP)

    outcome = triage(
        conn, [candidate("JOIN"), candidate("database index"), candidate("MVCC")],
        judge=scripted({"JOIN": 0.9, "database index": 0.9}), max_candidates=2, cache=None,
    )

    assert [v.candidate.term for v in outcome.spot_checks] == ["JOIN"]
    assert [v.candidate.term for v in outcome.held_back] == ["database index"]
    assert [c.term for c in outcome.emitted] == ["MVCC", "JOIN"]


def test_about_one_hold_back_in_five_is_a_spot_check_and_always_the_same_one():
    real = REAL_IS_SPOT_CHECK
    ids = [f"e-{i}" for i in range(2000)]

    chosen = [i for i in ids if real(i)]

    assert 300 < len(chosen) < 500
    assert chosen == [i for i in ids if real(i)]


def test_spot_checks_that_go_badly_switch_holding_back_off(conn):
    """Answered spot checks are triage measured in use. Below the precision the
    cut was chosen for, the map's cut no longer counts: nothing is held back."""
    person(conn, **A_MAP)
    for i in range(triage_module.SPOT_CHECK_EVIDENCE):
        filed = resolve(conn, manual(f"spot {i}", "asked anyway"), decide=strict)
        append(conn, "familiarity_judged", {
            "term": f"spot {i}", "session_id": "old", "p_knows": 0.9,
            "verdict": "spot_check", "encounter_id": filed.encounter_id,
        }, provenance={"triage_version": "t"})
        # Two in ten were real gaps: 80%, under the 90% bar.
        wrong = i < 2
        append(conn, "encounter_confirmed" if wrong else "encounter_dismissed",
               {"encounter_id": filed.encounter_id})
    compile_state(conn)

    outcome = triage(
        conn, [candidate("database index")],
        judge=scripted({"database index": 0.99}), max_candidates=2, cache=None,
    )

    assert triage_module.spot_check_record(conn) == (10, 8)
    assert outcome.cut is None and outcome.cut_source == "spot checks"
    assert outcome.held_back == []


def test_an_answered_spot_check_survives_regeneration(conn, raw, spot_checks_on):
    person(conn, **A_MAP)
    judge = scripted({"database index": 0.9})
    run(conn, raw, propose=proposes("database index"), decide=strict,
        model_label="v1", judge=judge, triage_label="stub")
    [spot] = [e for e in judged_events(conn) if e["verdict"] == "spot_check"]
    append(conn, "encounter_dismissed", {"encounter_id": spot["encounter_id"]})
    compile_state(conn)

    run(conn, raw, propose=proposes("database index"), decide=strict,
        model_label="v2", judge=judge, triage_label="stub")

    assert triage_module.spot_check_record(conn) == (1, 1)
