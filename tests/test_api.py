"""Tests for the surface (PR-23).

Like the store's tests, these are here to stop guarantees eroding rather than to
catch typos. Three of them correspond to named v1 acceptance criteria:

* an empty gap list is visually distinct from an error state -- which starts
  with the server being able to say *which* kind of empty it is,
* confirm and dismiss persist as events and survive a recompile,
* manually-submitted gaps appear in the same list as detected ones.

The fourth is the one the product rests on: the moment view must never attribute
injected text to the user.
"""

from __future__ import annotations

import json
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

from unrot.api.app import create_app
from unrot.capture import paths
from unrot.capture.ingest import SCHEMA_PATH as RAW_SCHEMA
from unrot.resolver import record_analysis
from unrot.store import append, compile_state, fixtures
from unrot.store.__main__ import open_store
from unrot.store.db import COMPILED_SCHEMA

#: `unrot.api.__init__` re-exports `app`, the FastAPI instance, which shadows
#: the submodule of the same name -- so `from unrot.api import app` hands back
#: the application, not the module, and monkeypatching anything inside it needs
#: the module object itself.
app_module = sys.modules["unrot.api.app"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated $UNROT_HOME. The app resolves it per request, so this is enough."""
    monkeypatch.setenv("UNROT_HOME", str(tmp_path))
    paths.ensure_layout(tmp_path)
    return tmp_path


@pytest.fixture
def client(home):
    return TestClient(create_app())


def make_raw(home, sessions: int, *, turns: list[tuple] = ()) -> sqlite3.Connection:
    """A raw capture store with `sessions` sessions in it.

    `turns` are (line_no, role, text, is_meta, is_sidechain) for session `s0`.
    """
    conn = sqlite3.connect(paths.raw_db_path(home))
    conn.executescript(RAW_SCHEMA.read_text(encoding="utf-8"))
    for index in range(sessions):
        conn.execute(
            "INSERT INTO raw_sessions (session_id, source_path, copy_path,"
            " prefix_sha256, first_ingested_at, last_ingested_at)"
            " VALUES (?, '/x', '/y', '', '2026-09-01', '2026-09-01')",
            (f"s{index}",),
        )
        paths.copy_path(home, f"s{index}").write_text("{}\n", encoding="utf-8")
    for line_no, role, text, is_meta, is_sidechain in turns:
        conn.execute(
            "INSERT INTO raw_turns (session_id, line_no, seq, role, text, is_meta,"
            " is_sidechain, occurred_at) VALUES ('s0', ?, 0, ?, ?, ?, ?, '2026-09-01')",
            (line_no, role, text, is_meta, is_sidechain),
        )
    conn.commit()
    conn.close()
    return conn


def seed_encounter(
    conn,
    *,
    concept="c-x",
    name="idempotency",
    source="transcript",
    pointer=("s0", 1, 2),
) -> str:
    append(conn, "concept_created", {"concept_id": concept, "canonical_name": name})
    encounter_id = f"e-{concept}"
    payload = {
        "encounter_id": encounter_id,
        "concept_id": concept,
        "source": source,
        "paraphrase": "Stands alone without the transcript beside it.",
    }
    if pointer:
        payload["pointer"] = dict(
            zip(("session_id", "line_start", "line_end"), pointer)
        )
    append(
        conn,
        "encounter_recorded",
        payload,
        provenance={"detector_version": "detector/test"},
    )
    compile_state(conn)
    return encounter_id


# ---------------------------------------------------------------------------
# The four states. "An empty gap list is visually distinct from an error state"
# is a v1 acceptance criterion, and it is only achievable if the server can name
# which kind of empty it is -- a single blank list cannot say four things.
# ---------------------------------------------------------------------------


def test_health_answers_without_re_folding_the_log(client, home, monkeypatch):
    """The supervisor polls this every second or two.

    A health check that recompiles would spend real work on every poll, and the
    load it generated would be indistinguishable from the load it exists to
    report. So this asserts the absence of a call rather than the presence of a
    field: the cheapness *is* the contract.
    """
    calls = []
    real = app_module.compile_state
    monkeypatch.setattr(
        app_module, "compile_state", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    )

    body = client.get("/api/health").json()

    assert calls == []
    assert body["ok"] is True
    assert body["compiled_schema"] == COMPILED_SCHEMA
    assert body["store_path"] == str(paths.store_db_path(home))


def test_health_reports_a_missing_raw_layer_without_calling_it_a_fault(client, home):
    """`raw_open: false` is the portable layer alone, which is a shipped state.

    It matters to the client -- the moment view cannot resolve anything without
    it -- so it is reported. It is not an error, so `ok` stays true and the
    client decides what to do about it.
    """
    before = client.get("/api/health").json()
    assert before["raw_open"] is False
    assert before["ok"] is True

    make_raw(home, 1)

    assert client.get("/api/health").json()["raw_open"] is True


def test_nothing_captured_is_its_own_state(client):
    body = client.get("/api/surface").json()
    assert body["state"] == "not_captured"
    assert body["capture"]["sessions"] == 0


def test_captured_but_never_analysed_is_not_a_clean_bill_of_health(client, home):
    make_raw(home, 5)
    body = client.get("/api/surface").json()
    assert body["state"] == "not_analysed"
    # The distinction that matters: this must not read as "we looked and found
    # nothing", because nothing looked.
    assert body["state"] != "clean"
    assert body["capture"]["sessions"] == 5


def test_too_little_history_is_admitted_rather_than_filled_in(client, home):
    """Journey 7: an honest cold start beats manufacturing flags to look useful."""
    make_raw(home, 1)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    append(conn, "encounter_dismissed", {"encounter_id": encounter})
    record_analysis(conn, "s0", candidates_found=1, detector_version="detector/test")
    conn.close()

    assert client.get("/api/surface").json()["state"] == "cold_start"


def test_clean_is_distinct_from_both_empty_and_broken(client, home):
    make_raw(home, 9)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    append(conn, "encounter_dismissed", {"encounter_id": encounter})
    for index in range(9):
        record_analysis(
            conn,
            f"s{index}",
            candidates_found=1 if index == 0 else 0,
            detector_version="detector/test",
            recompile=index == 8,
        )
    conn.close()

    body = client.get("/api/surface").json()
    assert body["state"] == "clean"
    assert body["counts"]["open"] == 0
    # Silence is a result, so it says what it is a result OF -- how many
    # sessions were examined, and how many of those were genuinely clean.
    assert body["capture"]["sessions_analysed"] == 9
    assert body["capture"]["sessions_clean"] == 8
    assert "examined" in body["detail"]


def test_analysed_and_clean_is_not_the_same_as_never_looked_at(client, home):
    """The distinction PR-21's `session_analysed` event exists to make.

    Both stores below contain zero encounters. Before coverage was recorded they
    were indistinguishable, so the surface could not tell the user whether its
    silence meant anything -- and journey 3 is entirely about silence meaning
    something.
    """
    make_raw(home, 9)

    never_looked = client.get("/api/surface").json()
    assert never_looked["state"] == "not_analysed"

    conn = open_store(home)
    for index in range(9):
        record_analysis(
            conn,
            f"s{index}",
            candidates_found=0,
            detector_version="detector/test",
            recompile=index == 8,
        )
    conn.close()

    looked = client.get("/api/surface").json()
    assert looked["state"] == "clean"
    assert looked["capture"]["sessions_clean"] == 9


def _typed_in(home, sessions: list[str]) -> None:
    """Give each session a turn a person typed, which is what makes it examinable."""
    conn = sqlite3.connect(paths.raw_db_path(home))
    for session in sessions:
        conn.execute(
            "INSERT INTO raw_turns (session_id, line_no, seq, role, text, is_meta,"
            " is_sidechain, occurred_at) VALUES (?, 1, 0, 'user', 'hi', 0, 0, '2026-09-01')",
            (session,),
        )
    conn.commit()
    conn.close()


def test_a_few_examined_with_many_waiting_is_not_clean(client, home):
    """First run, mid-analysis: 3 of 5 examinable sessions looked at, nothing found.

    "Silence here means we looked" would be false for the two nobody has looked
    at yet, so this is not `clean`. And the count that matters is sessions a
    person typed in -- all-tool sessions can never be examined, so counting them
    as waiting would keep the surface from ever reaching clean.
    """
    make_raw(home, 7)
    _typed_in(home, [f"s{index}" for index in range(5)])
    conn = open_store(home)
    for index in range(3):
        record_analysis(
            conn, f"s{index}", candidates_found=0, detector_version="detector/test",
            recompile=index == 2,
        )
    conn.close()

    body = client.get("/api/surface").json()
    assert body["state"] == "not_analysed"
    assert body["headline"] == "2 sessions waiting to be examined"
    assert "Silence here means we looked" not in body["detail"]
    assert body["capture"]["sessions"] == 7
    assert body["capture"]["sessions_analysable"] == 5
    assert body["capture"]["sessions_waiting"] == 2

    conn = open_store(home)
    for index in range(3, 5):
        record_analysis(
            conn, f"s{index}", candidates_found=0, detector_version="detector/test",
            recompile=index == 4,
        )
    conn.close()

    done = client.get("/api/surface").json()
    assert done["state"] == "clean"
    assert done["capture"]["sessions_waiting"] == 0
    # Nothing was ever found, so there is no "everything else" to have dealt with.
    assert "Everything else found" not in done["detail"]


def test_failure_is_never_reported_as_an_empty_list(client, home, monkeypatch):
    """A broken backend must not look like a clean session.

    The server never sends `state: failed` -- a response saying "I failed" is a
    response that arrived. It fails the request instead, and the frontend
    renders that as its own state.
    """
    make_raw(home, 5)
    monkeypatch.setattr(
        "unrot.api.read.concepts",
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("boom")),
    )
    with pytest.raises(sqlite3.OperationalError):
        client.get("/api/surface")


# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------


def test_referenced_concepts_never_reach_the_surface(client, home):
    """A concept named in material but never encountered is scaffolding.

    Showing one would mean presenting a gap the user has never actually met,
    which from their side is indistinguishable from the tool inventing things.
    """
    make_raw(home, 5)
    conn = open_store(home)
    append(conn, "concept_created", {"concept_id": "c-quorum", "canonical_name": "quorum"})
    append(
        conn,
        "material_generated",
        {
            "material_id": "m-1",
            "format": "sources_only",
            "covers_concept_ids": ["c-quorum"],
        },
    )
    compile_state(conn)
    conn.close()

    body = client.get("/api/surface").json()
    assert [c["name"] for c in body["concepts"]] == []


def test_confirming_does_not_close_the_gap(client, home):
    """Confirming means "I genuinely did not know this" -- the start, not the end."""
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    conn.close()

    before = client.get("/api/surface").json()
    assert before["concepts"][0]["bucket"] == "open"

    body = client.post(f"/api/encounters/{encounter}/confirm").json()
    assert body["concept"]["bucket"] == "learning"
    assert body["concept"]["state"] == "gap"
    assert body["counts"] == {"open": 0, "learning": 1, "closed": 0}


def test_one_answer_settles_a_concept_met_several_times(client, home):
    """A concept met in three sessions must not ask the same question three times.

    Caught by running the real chain: `idempotency` had two encounters, and
    confirming one left the card sitting in "Waiting on you" looking untouched --
    a button that appeared to do nothing. The question is about the concept, so
    one answer is enough.
    """
    make_raw(home, 5)
    conn = open_store(home)
    append(conn, "concept_created", {"concept_id": "c-x", "canonical_name": "idempotency"})
    for index, pointer in enumerate([("s0", 1, 2), ("s1", 8, 9)]):
        append(
            conn,
            "encounter_recorded",
            {
                "encounter_id": f"e-{index}",
                "concept_id": "c-x",
                "source": "transcript",
                "paraphrase": "Stands alone.",
                "pointer": dict(zip(("session_id", "line_start", "line_end"), pointer)),
            },
            provenance={"detector_version": "detector/test"},
        )
    compile_state(conn)
    conn.close()

    assert client.get("/api/surface").json()["counts"]["open"] == 1

    body = client.post("/api/encounters/e-0/confirm").json()
    assert body["counts"] == {"open": 0, "learning": 1, "closed": 0}
    # The other encounter is still honestly unjudged -- we never asked about it.
    moved = body["concept"]
    assert moved["encounter_count"] == 2
    assert moved["unjudged"] == 1


def test_dismissing_closes_it(client, home):
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    conn.close()

    body = client.post(f"/api/encounters/{encounter}/dismiss").json()
    assert body["concept"]["bucket"] == "closed"
    assert body["concept"]["state"] == "known"


def test_an_undo_puts_the_gap_back_and_keeps_the_mistake(client, home):
    """Quick accept's undo is a third event, not a deletion of the first.

    Deleting the confirmation would make a mis-key indistinguishable from never
    having answered. Keeping both means the log shows the moment the user
    changed their mind, and the surface shows the gap waiting again.
    """
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    conn.close()

    client.post(f"/api/encounters/{encounter}/confirm")
    body = client.post(f"/api/encounters/{encounter}/retract").json()

    assert body["concept"]["bucket"] == "open"
    assert body["concept"]["encounters"][0]["judgment"] is None

    conn = open_store(home)
    kinds = [
        row["event_type"]
        for row in conn.execute(
            "SELECT event_type FROM events WHERE subject_id = ? ORDER BY event_id",
            (encounter,),
        )
    ]
    conn.close()
    assert kinds[-2:] == ["encounter_confirmed", "encounter_judgment_retracted"]


def test_an_undo_is_ground_truth_like_any_other_judgment(home):
    """`user`, so regeneration never replays over it."""
    conn = open_store(home)
    encounter = seed_encounter(conn)
    append(conn, "encounter_dismissed", {"encounter_id": encounter})
    append(conn, "encounter_judgment_retracted", {"encounter_id": encounter})
    actor = conn.execute(
        "SELECT actor FROM events WHERE event_type = 'encounter_judgment_retracted'"
    ).fetchone()["actor"]
    compile_state(conn)
    judged = conn.execute(
        "SELECT judgment FROM compiled_encounters WHERE encounter_id = ?", (encounter,)
    ).fetchone()["judgment"]
    conn.close()

    assert actor == "user"
    assert judged is None


def test_the_latest_judgment_wins_across_an_undo(client, home):
    """Confirm, undo, dismiss: the answer is the last thing said."""
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    conn.close()

    client.post(f"/api/encounters/{encounter}/confirm")
    client.post(f"/api/encounters/{encounter}/retract")
    body = client.post(f"/api/encounters/{encounter}/dismiss").json()

    assert body["concept"]["encounters"][0]["judgment"] == "dismissed"
    assert body["concept"]["bucket"] == "closed"


def test_undoing_nothing_is_refused_rather_than_recorded(client, home):
    """A client sending this has lost track of what it just did."""
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    conn.close()

    response = client.post(f"/api/encounters/{encounter}/retract")

    assert response.status_code == 409
    conn = open_store(home)
    retractions = conn.execute(
        "SELECT count(*) AS n FROM events WHERE event_type = 'encounter_judgment_retracted'"
    ).fetchone()["n"]
    conn.close()
    assert retractions == 0


def test_judgments_survive_a_recompile(client, home):
    """S5 in the one place a user would notice it failing.

    Compiled state is thrown away and rebuilt on every write. If a judgment did
    not survive that, the user's own input would be the most fragile data in the
    system -- which inverts the whole point of separating user from system events.
    """
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    conn.close()

    client.post(f"/api/encounters/{encounter}/confirm")

    conn = open_store(home)
    compile_state(conn)
    compile_state(conn)
    row = conn.execute(
        "SELECT judgment FROM compiled_encounters WHERE encounter_id = ?", (encounter,)
    ).fetchone()
    conn.close()
    assert row["judgment"] == "confirmed"


def test_a_judgment_is_an_event_not_a_deletion(client, home):
    """Journey 2: a dismissal is signal for tuning the detector, not a discard."""
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    conn.close()

    client.post(f"/api/encounters/{encounter}/dismiss")

    conn = open_store(home)
    rows = list(
        conn.execute(
            "SELECT event_type, actor FROM events WHERE event_type = 'encounter_dismissed'"
        )
    )
    conn.close()
    assert len(rows) == 1
    # Ground truth. Actor comes from the event spec, never from the API caller.
    assert rows[0]["actor"] == "user"


def test_manual_and_detected_gaps_are_treated_identically(client, home):
    """PR-23's done-when. A gap you typed in yourself is as real as one we found."""
    make_raw(home, 5)
    conn = open_store(home)
    seed_encounter(conn, concept="c-auto", name="backpressure")
    seed_encounter(
        conn, concept="c-manual", name="cardinality", source="manual", pointer=None
    )
    conn.close()

    body = client.get("/api/surface").json()
    buckets = {c["name"]: c["bucket"] for c in body["concepts"]}
    assert buckets == {"backpressure": "open", "cardinality": "open"}

    # Provenance is visible, but it changes nothing about how the gap is handled.
    manual = next(c for c in body["concepts"] if c["name"] == "cardinality")
    assert manual["encounters"][0]["source"] == "manual"
    assert manual["encounters"][0]["resolvable"] is False
    assert client.post("/api/encounters/e-c-manual/confirm").json()["concept"][
        "bucket"
    ] == "learning"


# ---------------------------------------------------------------------------
# The moment view
# ---------------------------------------------------------------------------


def test_the_moment_never_attributes_injected_text_to_the_user(client, home):
    """The distinction the whole product rests on, in the view built to show it.

    A system-reminder arrives in a `user`-typed line without a person touching
    the keyboard. Rendering it under a "you" label would put words in the user's
    mouth in precisely the view they are meant to judge themselves against.
    """
    make_raw(
        home,
        1,
        turns=[
            (1, "assistant", "We'll make the handler idempotent.", 0, 0),
            (2, "user", "<system-reminder>injected</system-reminder>", 1, 0),
            (3, "user", "subagent chatter", 0, 1),
            (4, "user", "ok go ahead", 0, 0),
        ],
    )
    conn = open_store(home)
    encounter = seed_encounter(conn, pointer=("s0", 1, 4))
    conn.close()

    body = client.get(f"/api/encounters/{encounter}/moment").json()
    assert body["resolvable"] is True
    assert [t["text"] for t in body["turns"]] == [
        "We'll make the handler idempotent.",
        "ok go ahead",
    ]


def test_a_hand_submitted_gap_says_why_it_has_no_moment(client, home):
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn, source="manual", pointer=None)
    conn.close()

    body = client.get(f"/api/encounters/{encounter}/moment").json()
    assert body["resolvable"] is False
    assert "hand" in body["reason"]


def test_a_missing_transcript_copy_degrades_rather_than_errors(client, home):
    """S4's portable layer, tested from the local side.

    The paraphrase has to carry the card on its own, because on any machine
    without the retained copy that is all there is.
    """
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn, pointer=("gone", 1, 2))
    conn.close()

    body = client.get(f"/api/encounters/{encounter}/moment").json()
    assert body["resolvable"] is False
    assert "retained copy" in body["reason"]

    card = client.get("/api/surface").json()["concepts"][0]
    assert card["encounters"][0]["resolvable"] is False
    assert card["encounters"][0]["paraphrase"]


def test_unknown_things_are_refused_clearly(client, home):
    make_raw(home, 5)
    conn = open_store(home)
    encounter = seed_encounter(conn)
    conn.close()

    assert client.post("/api/encounters/nope/confirm").status_code == 404
    assert client.get("/api/encounters/nope/moment").status_code == 404
    assert client.post(f"/api/encounters/{encounter}/shrug").status_code == 400


# ---------------------------------------------------------------------------
# Fixtures. They exist only because the resolver (PR-21) is not built; the test
# that matters is that removing them leaves nothing behind.
# ---------------------------------------------------------------------------


def test_fixtures_are_removable_without_touching_anything_else(client, home):
    make_raw(home, 5)
    conn = open_store(home)
    real = seed_encounter(conn, concept="c-real", name="a real gap")
    append(conn, "encounter_confirmed", {"encounter_id": real})
    fixtures.seed(conn, raw_conn=None)
    compile_state(conn)

    assert fixtures.count(conn) > 0
    before = conn.execute(
        "SELECT count(*) FROM events WHERE origin != 'fixture'"
    ).fetchone()[0]

    fixtures.clear(conn)
    compile_state(conn)

    assert fixtures.count(conn) == 0
    assert (
        conn.execute("SELECT count(*) FROM events WHERE origin != 'fixture'").fetchone()[0]
        == before
    )
    names = [r["canonical_name"] for r in conn.execute("SELECT * FROM compiled_concepts")]
    conn.close()
    assert names == ["a real gap"]


def test_the_surface_admits_when_it_is_showing_fake_data(client, home):
    """Seeded data must never read as a finding about the user."""
    make_raw(home, 5)
    conn = open_store(home)
    fixtures.seed(conn, raw_conn=None)
    compile_state(conn)
    conn.close()

    assert client.get("/api/surface").json()["fixtures"] > 0


def test_fixtures_go_through_the_same_write_path_as_everything_else(home):
    """No second write path: `actor` still comes from the event spec, not the caller."""
    conn = open_store(home)
    fixtures.seed(conn, raw_conn=None)
    actors = {
        (r["event_type"], r["actor"])
        for r in conn.execute("SELECT DISTINCT event_type, actor FROM events")
    }
    conn.close()
    assert ("encounter_confirmed", "user") in actors
    assert ("encounter_recorded", "system") in actors
    # And provenance is still required of derived state.
    assert all(
        json.loads(p)["detector_version"]
        for (p,) in open_store(home).execute(
            "SELECT provenance FROM events WHERE event_type = 'encounter_recorded'"
        )
    )


def test_a_reasoning_model_is_asked_to_think_briefly(monkeypatch):
    """Left to its default, ling-3.0-flash reasoned for 32,768 tokens over one
    real transcript chunk and never answered. The config caps both."""
    from unrot.model import ModelConfig

    monkeypatch.delenv("UNROT_REASONING_EFFORT", raising=False)
    config = ModelConfig.from_env(api_key="k")
    assert config.reasoning_effort == "low"
    assert config.sends_reasoning
    assert config.max_tokens <= 16_000
    # The default leaves version strings as they were; a change shows in them.
    assert config.label == config.model.replace("/", "-")
    monkeypatch.setenv("UNROT_REASONING_EFFORT", "high")
    assert ModelConfig.from_env(api_key="k").label.endswith("+reasoning-high")
    # A local server is not sent OpenRouter's parameter.
    assert not ModelConfig.from_env(api_key="k", base_url="http://localhost:11434/v1").sends_reasoning


def test_running_out_of_output_is_said_in_words():
    from unrot.model import describe_failure

    message = describe_failure(_ran_out(content="", reasoning="..."))
    assert "CompletionUsage" not in message
    assert "reasoning effort" in message


# -- A model that will not stop -------------------------------------------------
#
# Shaped like the real failures: the OpenAI client raises LengthFinishReasonError
# with the billed response attached as `.completion`.


class LengthFinishReasonError(Exception):
    pass


def _ran_out(*, content: str, reasoning: str) -> Exception:
    from types import SimpleNamespace

    message = SimpleNamespace(content=content, model_extra={"reasoning": reasoning})
    error = LengthFinishReasonError("CompletionUsage(completion_tokens=32768 ...)")
    error.completion = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    return error


class _Scripted:
    """A client that answers from a script and remembers what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_model_that_never_stops_thinking_is_asked_to_answer_with_its_notes():
    """2 of 5 real calls reasoned for 32k tokens and wrote nothing. The retry
    answers with reasoning off, given the tail of its own notes."""
    from unrot.model import Structured

    primary = _Scripted(_ran_out(content="", reasoning="...Empty list. Done. I'm submitting this."))
    forced = _Scripted("the answer")
    assert Structured(primary, forced).invoke("PROMPT") == "the answer"
    assert forced.prompts[0].startswith("PROMPT")
    assert "Empty list. Done." in forced.prompts[0]
    assert "Write the answer now" in forced.prompts[0]


def test_other_failures_are_not_retried():
    from unrot.model import Structured

    primary = _Scripted(RuntimeError("401 unauthorised"))
    forced = _Scripted("never used")
    with pytest.raises(RuntimeError, match="401"):
        Structured(primary, forced).invoke("PROMPT")
    assert forced.prompts == []


def test_a_list_cut_off_by_the_ceiling_keeps_its_complete_entries():
    """With reasoning off the model listed candidates until the ceiling cut one
    in half. Everything before the cut is whole and usable."""
    from unrot.detector.model import complete_candidates

    entry = '{"term":"undo","paraphrase":"p","assistant_line":14,"signal":"accepted","importance":"central"}'
    cut = '{"candidates":[' + entry + ", " + entry.replace("undo", "NSEvent") + ',{"term":"PR-13","parap'
    assert [c["term"] for c in complete_candidates(cut)] == ["undo", "NSEvent"]
    assert complete_candidates('{"candidates":[{"term":"hal') == []
    assert complete_candidates("") == []


def test_the_detector_uses_a_cut_off_answer_rather_than_failing_the_session(monkeypatch):
    from unrot.detector import model as detector_model

    entry = '{"term":"undo","paraphrase":"p","assistant_line":14,"signal":"accepted","importance":"central"}'
    cut = '{"candidates":[' + entry + ',{"term":"PR-13","parap'
    failing = _Scripted(_ran_out(content=cut, reasoning=""))
    monkeypatch.setattr(detector_model, "structured_client", lambda config, schema, **_: failing)
    propose = detector_model.build_proposer(detector_model.ModelConfig(api_key="k"))
    assert [c["term"] for c in propose("PROMPT")] == ["undo"]

    # Nothing complete to keep: the failure stands, and the session stays pending.
    empty = _Scripted(_ran_out(content='{"candidates":[{"te', reasoning=""))
    monkeypatch.setattr(detector_model, "structured_client", lambda config, schema, **_: empty)
    propose = detector_model.build_proposer(detector_model.ModelConfig(api_key="k"))
    with pytest.raises(LengthFinishReasonError):
        propose("PROMPT")
