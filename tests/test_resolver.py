"""Tests for the resolver (PR-21).

The resolver is the single serialisation point in front of the log, which means
two different kinds of guarantee live here. One set is about *what it decides* —
a repeat is not a new concept, a near-duplicate folds in, a wrong answer is
correctable. The other is about *what it cannot do* — it cannot bypass
`events.append`, cannot invent a link to a concept that does not exist, and
cannot corrupt the log when two of them run at once.

The second set matters more. The first is quality and will change as the model
improves; the second is the structure everything downstream assumes.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from unrot.detector.candidates import Candidate
from unrot.resolver import (
    Submission,
    correct,
    fingerprint,
    from_candidate,
    judgments,
    manual,
    match,
    merge,
    record_analysis,
    resolve,
    resolve_all,
    resolver_version,
    strict,
)
from unrot.store import compile_state, connect


@pytest.fixture
def conn():
    connection = connect()
    yield connection
    connection.close()


def decider(**answer):
    """A stub decider that always gives the same answer. Stands in for the model.

    Every rule in the resolver is testable this way because `resolve()` takes
    the decision as a callable -- the same seam the detector uses for `propose`.
    """

    def decide(submission, shortlist):
        del submission, shortlist
        return dict(answer)

    return decide


def a_term(text="idempotency", *, paraphrase="Retries were assumed harmless.", **kw):
    return Submission(
        text=text,
        paraphrase=paraphrase,
        source=kw.pop("source", "transcript"),
        version=kw.pop("version", "detector/test"),
        session_id=kw.pop("session_id", "s0"),
        line_start=kw.pop("line_start", 10),
        line_end=kw.pop("line_end", 12),
    )


# ---------------------------------------------------------------------------
# What it decides
# ---------------------------------------------------------------------------


def test_a_new_term_becomes_a_concept(conn):
    result = resolve(conn, a_term(), decide=strict)
    assert result.decision == "new"
    row = conn.execute("SELECT * FROM compiled_concepts").fetchone()
    assert row["canonical_name"] == "idempotency"
    assert row["state"] == "gap"


def test_meeting_the_same_term_again_is_one_concept_and_two_encounters(conn):
    """S1's whole reason for existing, exercised through the write path.

    A second row per encounter is what preserves the repetition signal; a second
    *concept* would destroy it, because nothing would then show that the same
    thing tripped the user up twice.
    """
    first = resolve(conn, a_term(), decide=strict)
    second = resolve(
        conn, a_term(text="Idempotency.", session_id="s1", line_start=4, line_end=6),
        decide=strict,
    )

    assert second.concept_id == first.concept_id
    assert second.decision == "existing"
    row = conn.execute("SELECT encounter_count FROM compiled_concepts").fetchone()
    assert row["encounter_count"] == 2


def test_an_exact_match_never_costs_a_model_call(conn):
    """Most re-encounters take this path, so it should not buy a judgment."""

    def explode(submission, shortlist):
        raise AssertionError("the model must not be asked about an exact match")

    resolve(conn, a_term(), decide=strict)
    result = resolve(conn, a_term(text="  IDEMPOTENCY  ", session_id="s2"), decide=explode)
    assert result.decided_without_model is True
    assert result.decision == "existing"


def test_a_near_duplicate_resolves_to_the_existing_concept(conn):
    """PR-21's named case: `K8s` when `Kubernetes` is already there.

    The string matcher cannot see this -- that is exactly why a model is asked.
    """
    first = resolve(conn, a_term(text="Kubernetes"), decide=strict)
    result = resolve(
        conn,
        a_term(text="K8s", session_id="s1"),
        decide=decider(
            decision="alias",
            concept_id=first.concept_id,
            canonical_name="Kubernetes",
            alias="K8s",
            reasoning="Standard abbreviation.",
        ),
    )

    assert result.decision == "alias"
    assert result.concept_id == first.concept_id
    rows = conn.execute("SELECT * FROM compiled_concepts").fetchall()
    assert len(rows) == 1, "a near-duplicate must not create a second concept"
    assert json.loads(rows[0]["aliases"]) == ["K8s"]


def test_the_alias_makes_the_old_name_resolve_without_a_model_next_time(conn):
    """An alias is not decoration: it is what stops the same call being bought twice."""
    first = resolve(conn, a_term(text="Kubernetes"), decide=strict)
    resolve(
        conn,
        a_term(text="K8s", session_id="s1"),
        decide=decider(decision="alias", concept_id=first.concept_id, alias="K8s",
                       canonical_name="Kubernetes", reasoning="."),
    )

    def explode(submission, shortlist):
        raise AssertionError("an alias should already match by string")

    again = resolve(conn, a_term(text="k8s", session_id="s2"), decide=explode)
    assert again.concept_id == first.concept_id


def test_punctuation_and_case_are_not_a_new_concept(conn):
    """`Quorum!` is `quorum`. Normalisation settles that without asking anyone."""
    first = resolve(conn, a_term(text="quorum"), decide=strict)
    second = resolve(conn, a_term(text="  Quorum!  ", session_id="s1"), decide=strict)
    assert second.concept_id == first.concept_id
    assert second.decided_without_model is True


def test_things_in_the_same_space_are_not_merged_by_default(conn):
    """Optimistic vs. pessimistic locking: near-identical wording, opposite meaning.

    The resolver cannot prevent a model getting this wrong, but it must not make
    the mistake itself -- string similarity alone never merges anything.
    """
    resolve(conn, a_term(text="optimistic locking"), decide=strict)
    resolve(conn, a_term(text="pessimistic locking", session_id="s1"), decide=strict)
    names = sorted(
        r["canonical_name"] for r in conn.execute("SELECT canonical_name FROM compiled_concepts")
    )
    assert names == ["optimistic locking", "pessimistic locking"]


def test_a_decider_pointing_at_a_concept_that_does_not_exist_falls_back_to_new(conn):
    """The model is an untrusted source, exactly as it is for the detector.

    Honouring a hallucinated id would file this encounter under a concept the
    user never met -- and the card would look identical to every real one, so
    they would have no way to catch it.
    """
    result = resolve(
        conn,
        a_term(),
        decide=decider(
            decision="existing", concept_id="c-does-not-exist", canonical_name="x",
            reasoning=".",
        ),
    )
    assert result.decision == "new"


def test_an_unknown_decision_degrades_to_new(conn):
    result = resolve(
        conn, a_term(), decide=decider(decision="probably?", canonical_name="idempotency",
                                       reasoning=".")
    )
    assert result.decision == "new"


def test_a_vague_manual_submission_is_resolved_to_the_concept_meant(conn):
    """Journey 10: misheard, misspelt, or barely a term at all."""
    first = resolve(conn, a_term(text="backpressure"), decide=strict)
    result = resolve(
        conn,
        manual("something about backpresure? pushing back on producers"),
        decide=decider(
            decision="alias",
            concept_id=first.concept_id,
            canonical_name="backpressure",
            alias="backpresure",
            paraphrase="A slow consumer pushing back on a fast producer.",
            reasoning="Misspelling of a concept already in the graph.",
        ),
    )
    assert result.concept_id == first.concept_id
    row = conn.execute(
        "SELECT paraphrase, source, session_id FROM compiled_encounters"
        " WHERE source = 'manual'"
    ).fetchone()
    # The resolver's paraphrase wins over the user's own words, which were not
    # standalone -- "no idea what that means" tells a phone reader nothing.
    assert row["paraphrase"] == "A slow consumer pushing back on a fast producer."
    assert row["session_id"] is None


# ---------------------------------------------------------------------------
# What it cannot do
# ---------------------------------------------------------------------------


def test_the_resolver_cannot_forge_a_user_judgment(conn):
    """`actor` comes from the event spec, so nothing here can write ground truth."""
    resolve(conn, a_term(), decide=strict)
    actors = {
        row["actor"]
        for row in conn.execute("SELECT DISTINCT actor FROM events")
    }
    assert actors == {"system"}


def test_every_judgment_records_its_reasoning_and_is_addressable(conn):
    """Storing only the outcome would leave journey 19 nothing to argue with."""
    result = resolve(
        conn, a_term(), decide=decider(decision="new", canonical_name="idempotency",
                                       reasoning="Nothing like it in the graph.")
    )
    row = conn.execute(
        "SELECT * FROM events WHERE event_id = ?", (result.judgment_event_id,)
    ).fetchone()
    payload = json.loads(row["payload"])
    assert row["event_type"] == "resolver_judgment"
    assert payload["reasoning"] == "Nothing like it in the graph."
    assert payload["input_text"] == "idempotency"


def test_a_judgment_can_be_corrected_and_the_correction_is_ground_truth(conn):
    """Journey 19, and the reason the reasoning is an event rather than a log line."""
    kubernetes = resolve(conn, a_term(text="Kubernetes"), decide=strict)
    mistake = resolve(conn, a_term(text="K8s", session_id="s1"), decide=strict)
    assert mistake.concept_id != kubernetes.concept_id  # the wrong answer

    correct(
        conn,
        mistake.judgment_event_id,
        reasoning="K8s is just Kubernetes.",
        merge_into=kubernetes.concept_id,
    )

    correction = conn.execute(
        "SELECT * FROM events WHERE event_type = 'resolver_judgment_corrected'"
    ).fetchone()
    # A user event: regeneration replays system events and must leave this alone.
    assert correction["actor"] == "user"
    assert correction["supersedes"] == mistake.judgment_event_id

    survivors = conn.execute(
        "SELECT * FROM compiled_concepts WHERE merged_into IS NULL"
    ).fetchall()
    assert len(survivors) == 1
    assert survivors[0]["concept_id"] == kubernetes.concept_id
    assert survivors[0]["encounter_count"] == 2


def test_correcting_something_that_is_not_a_judgment_is_refused(conn):
    with pytest.raises(ValueError, match="no resolver judgment"):
        correct(conn, "not-an-event", reasoning="nope")


def test_the_judgment_log_shows_decisions_with_their_corrections(conn):
    result = resolve(conn, a_term(), decide=strict)
    correct(conn, result.judgment_event_id, reasoning="Actually I knew that one.")
    rows = judgments(conn)
    assert len(rows) == 1
    assert rows[0]["correction"] == "Actually I knew that one."


def test_re_running_over_the_same_session_updates_rather_than_duplicates(conn):
    """What makes regeneration (PR-26) affordable.

    A re-run that duplicated every encounter would mean improving the detector
    cost the user a growing pile of repeat flags -- which would make S5's
    "improving the engine improves the whole history" actively unpleasant.
    """
    submission = a_term()
    resolve(conn, submission, decide=strict)
    resolve(conn, submission, decide=strict)

    assert conn.execute("SELECT count(*) FROM compiled_encounters").fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM events WHERE event_type = 'encounter_recorded'"
    ).fetchone()[0] == 2, "both attempts are still in the log; only the fold collapses them"


def test_a_repeated_manual_submission_is_a_second_encounter(conn):
    """The opposite rule, and deliberately so.

    Typing the same term in twice means you met it twice. Collapsing those by
    content would erase exactly the repetition signal S1 exists to keep.
    """
    resolve(conn, manual("backpressure"), decide=strict)
    resolve(conn, manual("backpressure"), decide=strict)
    assert conn.execute("SELECT count(*) FROM compiled_encounters").fetchone()[0] == 2


def test_a_user_judgment_survives_the_resolver_running_again(conn):
    """S5's rule at the point it is most likely to be broken."""
    from unrot.store import append

    result = resolve(conn, a_term(), decide=strict)
    append(conn, "encounter_confirmed", {"encounter_id": result.encounter_id})
    compile_state(conn)

    resolve(conn, a_term(paraphrase="A better paraphrase on the second pass."), decide=strict)

    row = conn.execute(
        "SELECT judgment, paraphrase FROM compiled_encounters WHERE encounter_id = ?",
        (result.encounter_id,),
    ).fetchone()
    assert row["judgment"] == "confirmed"


def test_a_batch_sees_concepts_created_earlier_in_the_same_batch(conn):
    """Without the recompile between submissions this silently makes duplicates."""
    resolve_all(
        conn,
        [a_term(text="idempotency"), a_term(text="idempotency", session_id="s1")],
        decide=strict,
    )
    assert conn.execute("SELECT count(*) FROM compiled_concepts").fetchone()[0] == 1


def test_two_resolvers_racing_produce_a_duplicate_not_a_corrupt_log(conn):
    """The accepted consequence of not locking, asserted rather than assumed.

    Both read the same state, both conclude "new", and the graph ends up with
    two concepts for one thing -- which a merge folds away, because merges are
    themselves correctable events. What must never happen is an unreadable log.
    """
    known = match.current(conn)  # both read state here, before either writes
    assert known == []

    first = resolve(conn, a_term(text="quorum"), decide=strict)
    # Deliberately NOT a string match -- otherwise the matcher settles it and no
    # race is possible. This is the second resolver reasoning against state it
    # read before the first one wrote, and concluding "new" for the same thing.
    second = resolve(
        conn,
        a_term(text="a quorum of replicas", session_id="s1"),
        decide=decider(decision="new", canonical_name="quorum", reasoning="Stale read."),
    )
    assert first.concept_id != second.concept_id

    compile_state(conn)
    compile_state(conn)  # the fold is still a pure function of the log
    assert conn.execute("SELECT count(*) FROM compiled_concepts").fetchone()[0] == 2

    merge(conn, second.concept_id, first.concept_id, reasoning="Same thing, raced.")
    survivors = conn.execute(
        "SELECT * FROM compiled_concepts WHERE merged_into IS NULL"
    ).fetchall()
    assert len(survivors) == 1
    assert survivors[0]["encounter_count"] == 2


def test_two_concepts_that_slug_the_same_do_not_collide(conn):
    """Ids are readable for debugging, but readability must not conflate concepts.

    Reached only when the string matcher did NOT match (so a new concept is
    genuinely being made) and the decider nonetheless named it something that
    slugs to an id already taken.
    """
    resolve(conn, a_term(text="write ahead logging"), decide=strict)
    second = resolve(
        conn,
        a_term(text="WAL", session_id="s1"),
        decide=decider(
            decision="new", canonical_name="write ahead logging",
            reasoning="Deliberately forced a slug collision.",
        ),
    )
    assert second.concept_id != "c-write-ahead-logging"
    assert conn.execute("SELECT count(*) FROM compiled_concepts").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# Coverage -- the event that makes silence mean something
# ---------------------------------------------------------------------------


def test_a_clean_session_is_recorded_as_examined(conn):
    """The zero case is the finding, not the absence of one."""
    record_analysis(conn, "s0", candidates_found=0, detector_version="detector/test")
    row = conn.execute("SELECT * FROM compiled_sessions").fetchone()
    assert row["session_id"] == "s0"
    assert row["candidates_found"] == 0


def test_re_analysing_a_session_replaces_rather_than_accumulates(conn):
    record_analysis(conn, "s0", candidates_found=0, detector_version="detector/0.1.0")
    record_analysis(conn, "s0", candidates_found=2, detector_version="detector/0.2.0")
    rows = conn.execute("SELECT * FROM compiled_sessions").fetchall()
    assert len(rows) == 1
    assert rows[0]["detector_version"] == "detector/0.2.0"
    assert rows[0]["candidates_found"] == 2


# ---------------------------------------------------------------------------
# The seam to the detector
# ---------------------------------------------------------------------------


def test_a_detector_candidate_converts_without_losing_the_pointer(conn):
    candidate = Candidate(
        term="idempotency",
        paraphrase="Retries were assumed harmless.",
        session_id="abc",
        line_start=10,
        line_end=14,
        signal="accepted",
        importance="central",
        detector_version="detector/0.1.0+m+p",
    )
    result = resolve(conn, from_candidate(candidate), decide=strict)
    row = conn.execute(
        "SELECT * FROM compiled_encounters WHERE encounter_id = ?", (result.encounter_id,)
    ).fetchone()
    assert (row["session_id"], row["line_start"], row["line_end"]) == ("abc", 10, 14)
    assert row["detector_version"] == "detector/0.1.0+m+p"
    assert row["source"] == "transcript"


def test_the_fingerprint_is_stable_across_paraphrase_changes(conn):
    """The identity of an encounter is where it happened, not what we said about it."""
    first = a_term(paraphrase="One wording.")
    second = a_term(paraphrase="A completely different wording.")
    assert fingerprint(first) == fingerprint(second)


def test_resolver_version_records_model_and_prompt(conn):
    version = resolver_version("anthropic-claude-sonnet-5")
    assert version.startswith("resolver/0.1.0+anthropic-claude-sonnet-5+")
    # Editing the prompt must change this, or a change in what the machine means
    # by "the same concept" becomes indistinguishable from the user's world moving.
    assert version != resolver_version("other-model")
