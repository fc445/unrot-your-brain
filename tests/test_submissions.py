"""Capture from anywhere, and arguing with what the resolver made of it (PR-29 4b).

A term met in a wiki or a thread comes in through `POST /api/submissions`, goes
through the same resolver as a detected one, and lands as a `manual` encounter.
The resolver's "this looks like X" is its own event, so it can be argued with --
and the argument has to *stick*, which is most of what these tests are about.
"""

from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from unrot.api.app import create_app
from unrot.capture import paths
from unrot.resolver import strict
from unrot.store import append, compile_state
from unrot.store.__main__ import open_store

app_module = sys.modules["unrot.api.app"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("UNROT_HOME", str(tmp_path))
    paths.ensure_layout(tmp_path)
    # No model in any test here unless one is injected. The repo `.env` or the
    # developer's shell must never decide whether these make a network call.
    monkeypatch.setattr(app_module, "_build_decider", lambda: (strict, "none"))
    return tmp_path


@pytest.fixture
def client(home):
    return TestClient(create_app())


def known(home, concept_id="c-backpressure", name="Backpressure"):
    conn = open_store(home)
    append(conn, "concept_created", {"concept_id": concept_id, "canonical_name": name})
    compile_state(conn)
    conn.close()
    return concept_id


def decider_says(monkeypatch, **answer):
    """Inject a decider with a fixed opinion, as though a model gave it."""
    monkeypatch.setattr(
        app_module, "_build_decider", lambda: ((lambda _s, _l: dict(answer)), "test-model")
    )


def events(home, kind):
    conn = open_store(home)
    rows = conn.execute(
        "SELECT actor, payload FROM events WHERE event_type = ? ORDER BY event_id", (kind,)
    ).fetchall()
    conn.close()
    return [(row["actor"], json.loads(row["payload"])) for row in rows]


def test_a_capture_is_a_manual_encounter_through_the_resolver(client, home):
    body = client.post(
        "/api/submissions", json={"text": "backpressure handling"}
    ).json()

    assert body["decision"] == "new"
    assert body["concept"]["bucket"] == "open"
    [encounter] = body["concept"]["encounters"]
    assert encounter["source"] == "manual"
    # A resolver judgment was recorded, so there is something to argue with.
    assert events(home, "resolver_judgment")


def test_a_capture_naming_a_known_concept_joins_it_without_a_model(client, home):
    known(home)

    body = client.post("/api/submissions", json={"text": "backpressure"}).json()

    assert body["decision"] == "existing"
    assert body["concept_id"] == "c-backpressure"
    assert body["decided_without_model"] is True


def test_only_the_selection_and_an_optional_title_come_in(client, home):
    """`seen_in` is folded into the paraphrase and goes nowhere else."""
    body = client.post(
        "/api/submissions",
        json={"text": "backpressure", "paraphrase": "", "seen_in": "Service design (Confluence)"},
    ).json()

    [encounter] = body["concept"]["encounters"]
    assert encounter["paraphrase"] == "backpressure (seen in Service design (Confluence))"
    [(_, recorded)] = events(home, "encounter_recorded")
    assert set(recorded) <= {"encounter_id", "concept_id", "source", "paraphrase"}


def test_a_passage_is_refused_rather_than_filed_as_a_term(client, home):
    response = client.post("/api/submissions", json={"text": "word " * 200})

    assert response.status_code == 400
    assert not events(home, "resolver_judgment")


def test_saying_this_is_new_gives_it_a_concept_of_its_own(client, home, monkeypatch):
    known(home)
    decider_says(
        monkeypatch,
        decision="existing",
        concept_id="c-backpressure",
        canonical_name="Backpressure",
        reasoning="Handling backpressure is backpressure.",
    )
    made = client.post("/api/submissions", json={"text": "flow control"}).json()
    assert made["concept_id"] == "c-backpressure"

    fixed = client.post(
        f"/api/judgments/{made['judgment_event_id']}/correct",
        json={"reasoning": "This is new", "split_encounter": made["encounter_id"]},
    ).json()

    assert fixed["concept"]["name"] == "flow control"
    assert [e["encounter_id"] for e in fixed["concept"]["encounters"]] == [made["encounter_id"]]
    # The correction is ground truth; the repair carries it out.
    [(actor, _)] = events(home, "resolver_judgment_corrected")
    assert actor == "user"
    # The concept it was wrongly filed under is untouched, and now empty of it.
    conn = open_store(home)
    left = conn.execute(
        "SELECT count(*) AS n FROM compiled_encounters WHERE concept_id = 'c-backpressure'"
    ).fetchone()["n"]
    conn.close()
    assert left == 0


def test_rejecting_an_alias_takes_the_alias_back(client, home, monkeypatch):
    """Otherwise the correction does not stick.

    The alias would stay on the concept, the same words would exact-match it
    next time with no model asked, and the mistake the user just argued with
    would be made again without anyone seeing it happen.
    """
    known(home)
    decider_says(
        monkeypatch,
        decision="alias",
        concept_id="c-backpressure",
        canonical_name="Backpressure",
        alias="flow control",
        reasoning="Another name for it.",
    )
    made = client.post("/api/submissions", json={"text": "flow control"}).json()
    client.post(
        f"/api/judgments/{made['judgment_event_id']}/correct",
        json={"split_encounter": made["encounter_id"]},
    )

    # Same words again, with the string matcher only: must not land on Backpressure.
    monkeypatch.setattr(app_module, "_build_decider", lambda: (strict, "none"))
    again = client.post("/api/submissions", json={"text": "flow control"}).json()

    assert again["concept_id"] != "c-backpressure"


def test_moving_an_encounter_keeps_what_the_user_said_about_it(client, home, monkeypatch):
    known(home)
    decider_says(
        monkeypatch, decision="existing", concept_id="c-backpressure",
        canonical_name="Backpressure", reasoning="Same thing.",
    )
    made = client.post("/api/submissions", json={"text": "flow control"}).json()
    client.post(f"/api/encounters/{made['encounter_id']}/confirm")

    fixed = client.post(
        f"/api/judgments/{made['judgment_event_id']}/correct",
        json={"split_encounter": made["encounter_id"]},
    ).json()

    [encounter] = fixed["concept"]["encounters"]
    assert encounter["judgment"] == "confirmed"


def test_a_split_can_only_move_what_that_judgment_placed(client, home, monkeypatch):
    known(home)
    other = client.post("/api/submissions", json={"text": "something else"}).json()
    decider_says(
        monkeypatch, decision="existing", concept_id="c-backpressure",
        canonical_name="Backpressure", reasoning="Same thing.",
    )
    made = client.post("/api/submissions", json={"text": "flow control"}).json()

    response = client.post(
        f"/api/judgments/{made['judgment_event_id']}/correct",
        json={"split_encounter": other["encounter_id"]},
    )

    assert response.status_code == 409


def test_a_model_that_cannot_be_reached_costs_the_check_not_the_capture(
    client, home, monkeypatch
):
    def unreachable(_submission, _shortlist):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(app_module, "_build_decider", lambda: (unreachable, "test-model"))

    body = client.post("/api/submissions", json={"text": "flow control"}).json()

    assert body["decision"] == "new"
    assert "could not be reached" in body["model_unavailable"]
    # Provenance must not claim a model decided what the string matcher did.
    conn = open_store(home)
    version = json.loads(
        conn.execute(
            "SELECT provenance FROM events WHERE event_type = 'resolver_judgment'"
        ).fetchone()["provenance"]
    )["resolver_version"]
    conn.close()
    assert "test-model" not in version


# --- PR-22: the three done-when criteria --------------------------------------------


def event_count(home) -> int:
    conn = open_store(home)
    n = conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"]
    conn.close()
    return n


def test_a_vague_submission_resolves_and_sits_beside_detected_gaps(client, home, monkeypatch):
    """PR-22's named v1 acceptance criterion."""
    decider_says(
        monkeypatch,
        decision="new",
        canonical_name="backpressure",
        paraphrase="Slowing producers down when a consumer can't keep up.",
        reasoning="They were reaching for backpressure.",
    )

    body = client.post(
        "/api/submissions", json={"text": "something about backpressure?"}
    ).json()

    assert body["canonical_name"] == "backpressure"
    surface = client.get("/api/surface").json()
    assert [c["name"] for c in surface["concepts"] if c["bucket"] == "open"] == ["backpressure"]


def test_a_submission_matching_a_known_concept_merges_rather_than_duplicating(client, home):
    known(home)
    client.post("/api/submissions", json={"text": "Backpressure"})

    names = [c["name"] for c in client.get("/api/surface").json()["concepts"]]
    assert names.count("Backpressure") == 1


def test_nonsense_fails_visibly_and_writes_nothing(client, home, monkeypatch):
    """Rather than silently creating a garbage concept."""
    decider_says(
        monkeypatch,
        decision="unclear",
        canonical_name="asdkjh",
        reasoning="That reads as keyboard noise rather than a term.",
    )
    before = event_count(home)

    response = client.post("/api/submissions", json={"text": "asdkjh"})

    assert response.status_code == 422
    assert "keyboard noise" in response.json()["detail"]
    assert event_count(home) == before


def test_input_with_no_word_in_it_is_refused_without_asking_a_model(client, home, monkeypatch):
    def must_not_be_called(_submission, _shortlist):
        raise AssertionError("the model was asked about something with no word in it")

    monkeypatch.setattr(app_module, "_build_decider", lambda: (must_not_be_called, "test-model"))
    before = event_count(home)

    response = client.post("/api/submissions", json={"text": "??? 123"})

    assert response.status_code == 422
    assert event_count(home) == before


def test_unclear_is_never_honoured_for_a_detected_term(home):
    """A transcript term cannot contain no term. It falls back to new, as before."""
    from unrot.resolver import Submission, resolve

    conn = open_store(home)
    resolution = resolve(
        conn,
        Submission(
            text="idempotency", paraphrase="p", source="transcript",
            version="detector/test", session_id="s", line_start=1, line_end=2,
        ),
        decide=lambda _s, _l: {"decision": "unclear", "reasoning": "?"},
    )
    conn.close()

    assert resolution.decision == "new"
