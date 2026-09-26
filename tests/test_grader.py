"""Tests for the comprehension check (PR-24).

The point of this ticket is a split: what the person wrote is ground truth and
permanent, the level it was given is derived and disposable. Most of these tests
are about that line holding under pressure — a re-grade, a rubric change, a
change to the question itself — because if it ever stops holding, the scale
becomes permanent by accident, which is the failure S2 was resolved to avoid.
"""

from __future__ import annotations

import json

import pytest

from unrot.grader import (
    check,
    grade,
    grader_version,
    history,
    keyword_grader,
    prompt,
    question,
    regrade,
    submit,
    ungraded,
)
from unrot.model import ModelConfig
from unrot.resolver import manual, resolve, strict
from unrot.store import SOLO_LEVELS as SOLO
from unrot.store import compile_state, connect


def _config():
    return ModelConfig(api_key="test-key")


@pytest.fixture
def conn():
    connection = connect()
    yield connection
    connection.close()


@pytest.fixture
def concept(conn):
    return resolve(conn, manual("idempotency", "Retries were assumed harmless."), decide=strict)


def fixed(level, reasoning="because."):
    """A grader that always answers the same. Stands in for the model."""

    def grade_fn(question_text, answer):
        del question_text, answer
        return {"level": level, "reasoning": reasoning}

    return grade_fn


CAUSAL = (
    "Calling it twice has the same effect as once, so a client can retry after a"
    " timeout without double-charging."
)


# ---------------------------------------------------------------------------
# The two-event split
# ---------------------------------------------------------------------------


def test_the_answer_and_its_grade_are_separate_events(conn, concept):
    explanation_id = submit(conn, concept.concept_id, CAUSAL)
    grade(conn, explanation_id, grade_fn=fixed("causal"), model_label="test")

    kinds = [
        row["event_type"]
        for row in conn.execute(
            "SELECT event_type FROM events WHERE event_type LIKE 'explanation%'"
            " ORDER BY event_id"
        )
    ]
    assert kinds == ["explanation_submitted", "explanation_graded"]


def test_the_answer_is_a_user_event_and_the_grade_is_not(conn, concept):
    """S5's line, at the point it decides what a regeneration may destroy."""
    explanation_id = submit(conn, concept.concept_id, CAUSAL)
    grade(conn, explanation_id, grade_fn=fixed("causal"), model_label="test")

    actors = {
        row["event_type"]: row["actor"]
        for row in conn.execute(
            "SELECT event_type, actor FROM events WHERE event_type LIKE 'explanation%'"
        )
    }
    assert actors == {"explanation_submitted": "user", "explanation_graded": "system"}


def test_the_question_is_stored_verbatim_beside_the_answer(conn, concept):
    """A version tag alone is opaque to a future re-grader.

    The same answer means different things under different questions, so the
    words that were actually on screen are what get kept.
    """
    explanation_id = submit(conn, concept.concept_id, CAUSAL)
    row = conn.execute(
        "SELECT * FROM compiled_explanations WHERE explanation_id = ?", (explanation_id,)
    ).fetchone()

    assert row["prompt_text"] == question("idempotency")
    assert "why does it work" in row["prompt_text"]
    assert row["prompt_version"] == check.check_version()


def test_the_grade_records_which_grader_produced_it(conn, concept):
    """LLM grading drifts; without this a model upgrade silently rewrites meaning."""
    explanation_id = submit(conn, concept.concept_id, CAUSAL)
    grade(conn, explanation_id, grade_fn=fixed("causal"), model_label="some-model")

    row = conn.execute(
        "SELECT * FROM compiled_explanations WHERE explanation_id = ?", (explanation_id,)
    ).fetchone()
    assert row["rubric"] == "solo-3"
    assert "some-model" in row["grader_version"]
    assert prompt.prompt_id() in row["grader_version"]


# ---------------------------------------------------------------------------
# The acceptance criterion: the raw layer must be sufficient on its own
# ---------------------------------------------------------------------------


def test_deleting_every_grade_and_regrading_reproduces_the_state(conn, concept):
    """PR-24's proof that nothing irreplaceable lives in a grade.

    If this holds, the rubric is genuinely swappable and the scale never became
    permanent by accident.
    """
    second = resolve(conn, manual("backpressure", "A bounded queue."), decide=strict)
    first_id = submit(conn, concept.concept_id, CAUSAL)
    second_id = submit(conn, second.concept_id, "it pushes back")
    grade(conn, first_id, grade_fn=fixed("causal"), model_label="m")
    grade(conn, second_id, grade_fn=fixed("isolated"), model_label="m")

    before = {
        row["concept_id"]: (row["state"], row["latest_level"])
        for row in conn.execute("SELECT * FROM compiled_concepts")
    }
    assert before[concept.concept_id] == ("known", "causal")

    def by_answer(question_text, answer):
        del question_text
        return {"level": "causal" if "so a client" in answer else "isolated", "reasoning": "."}

    regrade(conn, grade_fn=by_answer, model_label="m")

    after = {
        row["concept_id"]: (row["state"], row["latest_level"])
        for row in conn.execute("SELECT * FROM compiled_concepts")
    }
    assert after == before


def test_regrading_never_touches_what_the_user_wrote(conn, concept):
    explanation_id = submit(conn, concept.concept_id, CAUSAL)
    grade(conn, explanation_id, grade_fn=fixed("causal"), model_label="m")

    regrade(conn, grade_fn=fixed("isolated"), model_label="m")

    rows = list(
        conn.execute("SELECT * FROM events WHERE event_type = 'explanation_submitted'")
    )
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"])["raw_text"] == CAUSAL
    # And the new, different grade did land.
    assert conn.execute(
        "SELECT level FROM compiled_explanations WHERE explanation_id = ?",
        (explanation_id,),
    ).fetchone()["level"] == "isolated"


def test_a_regrade_that_would_lose_ground_truth_is_refused(conn, concept):
    """A guard rather than an intention.

    `regrade` deletes system events by design. If a change ever made it delete a
    user event too, the store would silently lose the only thing it cannot
    rebuild, and every test above would still pass.
    """
    submit(conn, concept.concept_id, CAUSAL)

    def destructive(question_text, answer):
        del question_text, answer
        conn.execute("DELETE FROM events WHERE event_type = 'explanation_submitted'")
        return {"level": "causal", "reasoning": "."}

    with pytest.raises(RuntimeError, match="ground truth must survive"):
        regrade(conn, grade_fn=destructive, model_label="m")


def test_a_new_rubric_can_regrade_history_written_under_the_old_one(conn, concept):
    """Swapping the rubric is a code change plus a re-run, not a migration."""
    explanation_id = submit(conn, concept.concept_id, CAUSAL)
    grade(conn, explanation_id, grade_fn=fixed("listed"), model_label="old")
    assert conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"] == "gap"

    regrade(conn, grade_fn=fixed("causal"), model_label="new")

    row = conn.execute("SELECT * FROM compiled_concepts").fetchone()
    assert row["state"] == "known"
    assert row["latest_level"] == "causal"


# ---------------------------------------------------------------------------
# Changing the question mid-corpus
# ---------------------------------------------------------------------------


def test_answers_under_two_different_questions_stay_interpretable(conn, concept, monkeypatch):
    """The wording is expected to change. Nothing before it may be corrupted.

    Each answer carries the question it was given to, so a re-grade can grade
    each one against what was actually asked rather than against today's wording.
    """
    old_id = submit(conn, concept.concept_id, "it means you can retry")
    old_question = conn.execute(
        "SELECT prompt_text FROM compiled_explanations WHERE explanation_id = ?",
        (old_id,),
    ).fetchone()["prompt_text"]

    monkeypatch.setattr(check, "TEMPLATE", "Totally different question about {term}?")
    new_id = submit(conn, concept.concept_id, CAUSAL)

    rows = {
        r["explanation_id"]: r
        for r in conn.execute("SELECT * FROM compiled_explanations")
    }
    assert rows[old_id]["prompt_text"] == old_question
    assert rows[new_id]["prompt_text"] == "Totally different question about idempotency?"
    assert rows[old_id]["prompt_version"] != rows[new_id]["prompt_version"]

    # A re-grade sees each answer's own question, not whichever is current.
    seen = []

    def recording(question_text, answer):
        del answer
        seen.append(question_text)
        return {"level": "listed", "reasoning": "."}

    regrade(conn, grade_fn=recording, model_label="m")
    assert old_question in seen
    assert "Totally different question" in " ".join(seen)


def test_the_question_asks_for_causation(conn):
    """The rubric's only load-bearing boundary is listed -> causal.

    A question that asks solely for a definition elicits listed-shaped answers,
    so the grader would spend the whole corpus unable to see the distinction it
    exists to draw -- and re-grading cannot recover signal never elicited.
    """
    asked = question("backpressure").lower()
    assert "why" in asked
    assert "backpressure" in asked


# ---------------------------------------------------------------------------
# Behaviour under failure
# ---------------------------------------------------------------------------


def test_an_answer_is_stored_even_when_grading_never_happens(conn, concept):
    """What the user wrote is irreplaceable; the level can be produced later."""
    explanation_id = submit(conn, concept.concept_id, CAUSAL)

    pending = ungraded(conn)
    assert [row["explanation_id"] for row in pending] == [explanation_id]
    assert pending[0]["raw_text"] == CAUSAL

    grade(conn, explanation_id, grade_fn=fixed("causal"), model_label="later")
    assert ungraded(conn) == []


def test_an_unrecognised_level_is_marked_down_not_invented(conn, concept):
    """The conservative direction is down.

    Over-crediting comprehension is the exact failure this check replaces: a
    person told they understood something they did not is worse off than before.
    """
    explanation_id = submit(conn, concept.concept_id, CAUSAL)
    result = grade(
        conn, explanation_id, grade_fn=fixed("brilliant"), model_label="m"
    )
    assert result.level == "isolated"


def test_an_empty_answer_is_refused_rather_than_stored(conn, concept):
    with pytest.raises(ValueError, match="not an answer"):
        submit(conn, concept.concept_id, "   ")


def test_explaining_a_concept_that_does_not_exist_is_refused(conn):
    with pytest.raises(ValueError, match="no concept"):
        submit(conn, "c-nope", CAUSAL)


def test_every_attempt_is_kept_not_just_the_latest(conn, concept):
    """Journey 8 compares an answer weeks later against the first one."""
    submit(conn, concept.concept_id, "no idea")
    submit(conn, concept.concept_id, CAUSAL)

    attempts = history(conn, concept.concept_id)
    assert [row["raw_text"] for row in attempts] == ["no idea", CAUSAL]


def test_the_outcome_survives_a_recompile(conn, concept):
    """Named v1 acceptance criterion."""
    explanation_id = submit(conn, concept.concept_id, CAUSAL)
    grade(conn, explanation_id, grade_fn=fixed("causal"), model_label="m")

    compile_state(conn)
    compile_state(conn)

    row = conn.execute("SELECT * FROM compiled_concepts").fetchone()
    assert (row["state"], row["latest_level"]) == ("known", "causal")


# ---------------------------------------------------------------------------
# The offline grader
# ---------------------------------------------------------------------------


def test_the_offline_grader_separates_the_rubric_levels(conn):
    """Crude, but it must at least draw the boundary the rubric is about."""
    assert keyword_grader("q", "You can call it twice.")["level"] == "isolated"
    assert (
        keyword_grader("q", "You can call it twice, it uses a key, it's for retries.")[
            "level"
        ]
        == "listed"
    )
    assert keyword_grader("q", CAUSAL)["level"] == "causal"


def test_the_offline_grader_says_it_was_offline(conn):
    """The user must be able to tell a real judgment from a keyword count."""
    assert "without a model" in keyword_grader("q", CAUSAL)["reasoning"]


def test_grader_version_changes_when_the_rubric_changes(monkeypatch):
    before = grader_version("m")
    monkeypatch.setattr(prompt, "TEMPLATE", prompt.TEMPLATE + "\nAn extra rule.")
    assert grader_version("m") != before


# ---------------------------------------------------------------------------
# The classifier grader
# ---------------------------------------------------------------------------


def jev_response(probabilities, confidence=0.9, model="typesafe/jev-1.13-x"):
    """A transport standing in for the network, answering as the real endpoint does.

    Every request it receives is kept on `.seen`, so a test can read what was sent.
    """
    import httpx2

    body = {
        "model": model,
        "answers": {
            "solo": {
                "type": "choice",
                "choice": max(probabilities, key=probabilities.get),
                "confidence": confidence,
                "probabilities": probabilities,
            }
        },
        "usage": {"input_tokens": 400, "output_tokens": 40, "cost": 0.000018},
    }
    seen = []

    def answer(request):
        seen.append(request)
        return httpx2.Response(200, json=body)

    transport = httpx2.MockTransport(answer)
    transport.seen = seen
    return transport


def test_the_classifier_grade_keeps_the_whole_distribution(conn, concept, monkeypatch):
    """A label says which side of the line you fell; a distribution says how far.

    The rubric's only load-bearing boundary is listed -> causal, so how close an
    answer came to it is the most informative thing about the grade.
    """
    from unrot.grader import jev

    transport = jev_response({"isolated": 0.0, "listed": 0.72, "causal": 0.28})

    explanation_id = submit(conn, concept.concept_id, "Facts, unconnected.")
    result = grade(
        conn,
        explanation_id,
        grade_fn=jev.build_jev_grader(_config(), transport=transport),
        model_label="jev",
    )

    assert result.level == "listed"
    assert result.probabilities == {"isolated": 0.0, "listed": 0.72, "causal": 0.28}
    assert result.confidence == 0.9

    row = conn.execute(
        "SELECT * FROM compiled_explanations WHERE explanation_id = ?", (explanation_id,)
    ).fetchone()
    assert json.loads(row["probabilities"])["causal"] == 0.28
    assert row["confidence"] == 0.9


def test_the_level_is_taken_from_the_distribution_not_the_label(conn, concept, monkeypatch):
    """So the stored level and the stored probabilities can never disagree.

    Trusting `choice` separately would allow a row saying `causal` beside numbers
    that peak on `listed`, and nothing downstream could tell which was right.
    """
    from unrot.grader import jev

    disagreeing = jev_response({"isolated": 0.0, "listed": 0.9, "causal": 0.1})

    grade_fn = jev.build_jev_grader(_config(), transport=disagreeing)
    answer = grade_fn("q", "a")
    assert answer["level"] == "listed"
    assert answer["level"] == max(answer["probabilities"], key=answer["probabilities"].get)


def test_a_reasoning_grade_stores_no_distribution(conn, concept):
    """Absent must mean "not measured", never "flat".

    Filling it in with zeroes would make a reasoning model's grade look like a
    calibrated one, which is exactly the confusion `grader_version` exists to
    prevent elsewhere.
    """
    explanation_id = submit(conn, concept.concept_id, CAUSAL)
    grade(conn, explanation_id, grade_fn=fixed("causal"), model_label="m")

    row = conn.execute(
        "SELECT * FROM compiled_explanations WHERE explanation_id = ?", (explanation_id,)
    ).fetchone()
    assert row["probabilities"] is None
    assert row["confidence"] is None


def test_the_threshold_can_move_without_regrading(conn, monkeypatch):
    """The payoff for keeping the distribution.

    Changing where `causal` starts is then a re-reading of numbers already in
    the log -- no model calls, no new events, nothing to re-grade.
    """
    from unrot.grader import threshold_level

    near_miss = {"isolated": 0.0, "listed": 0.6, "causal": 0.4}
    assert threshold_level(near_miss) == "listed"
    assert threshold_level(near_miss, causal_at=0.35) == "causal"


def test_the_classifier_is_sent_both_the_question_and_the_answer(conn, monkeypatch):
    """The same answer means different things under different questions."""
    from unrot.grader import jev

    fake = jev_response({"isolated": 0.0, "listed": 1.0, "causal": 0.0})

    jev.build_jev_grader(_config(), transport=fake)("What is idempotency?", "You can call it twice.")
    sent = json.loads(fake.seen[-1].content)
    assert "What is idempotency?" in sent["state"]
    assert "You can call it twice." in sent["state"]
    assert set(sent["questions"]["solo"]["criteria"]) == set(SOLO)


def test_the_classifier_endpoint_sits_beside_the_chat_one(conn):
    from unrot.grader import jev
    from unrot.model import ModelConfig

    for base, expected in (
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/systemone"),
        ("http://localhost:11434/v1/", "http://localhost:11434/v1/systemone"),
    ):
        fake = jev_response({"isolated": 1.0, "listed": 0.0, "causal": 0.0})
        jev.build_jev_grader(ModelConfig(base_url=base, api_key="k"), transport=fake)("q", "a")
        assert str(fake.seen[-1].url) == expected


def test_a_classifier_grade_is_priced(conn):
    """The classifier's own response type keeps only token counts. OpenRouter's
    price is read off the raw response on its way past, so Jev spend is priced
    rather than quietly counted as unknown."""
    from unrot.grader import jev
    from unrot.spend import Meter

    meter = Meter()
    fake = jev_response({"isolated": 0.0, "listed": 1.0, "causal": 0.0})
    jev.build_jev_grader(_config(), meter=meter, transport=fake)("q", "a")

    [call] = meter.calls
    assert call.purpose == "grading"
    assert call.cost == 0.000018
    assert call.ok


def test_the_classifier_needs_a_key_like_everything_else(conn):
    from unrot.model import ModelConfig
    from unrot.grader import jev

    with pytest.raises(RuntimeError, match="No API key"):
        jev.build_jev_grader(ModelConfig(api_key=None))
