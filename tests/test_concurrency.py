"""Analysis with calls in flight side by side.

A detection call takes about a minute, nearly all of it waiting on the provider,
and a session long enough to need several chunks used to send them one after
another. Now it sends them together, and the app analyses several sessions at
once. Two things must not change when it does, and these tests are about those:

* **The answer.** Chunks are gathered back in order, so a term flagged in two
  chunks is kept from the earlier one, exactly as it was when they ran in turn.
* **The graph.** Filing is serialised (`analyse.FILING`), so two sessions that
  met the same term at the same moment file it under one concept, not two.

No network and no model: the proposers here block on a barrier, which only
releases if the calls really are in flight at the same time.
"""

from __future__ import annotations

import re
import threading
import time

import pytest

from unrot.analyse import analyse_session
from unrot.capture import connect as connect_raw
from unrot.detector import build_windows, chunk_windows, detect
from unrot.model import DEFAULT_CONCURRENCY, ModelConfig
from unrot.resolver import strict
from unrot.store.__main__ import open_store

import test_detector
from test_detector import LONG, assistant_line, conn, proposal, seed, user_line  # noqa: F401 -- `conn` is a fixture

MODEL = "test-model"


def long_session(conn) -> str:
    lines = []
    for _ in range(12):
        lines.append(assistant_line(LONG * 20))
        lines.append(user_line("ok go ahead"))
    return seed(conn, lines)


def first_line(prompt: str) -> int:
    return int(re.search(r"--- assistant \(line (\d+)\) ---", prompt).group(1))


# --- chunks -------------------------------------------------------------------


def test_a_long_sessions_chunks_are_sent_at_once(conn):
    session = long_session(conn)
    chunks = chunk_windows(build_windows(conn, session), budget=8_000)
    assert len(chunks) >= 2

    # Every call waits for the others. Sent one at a time, the first would wait
    # alone until the barrier timed out.
    together = threading.Barrier(len(chunks), timeout=5)

    def propose(_prompt):
        together.wait()
        return []

    result = detect(
        conn, session, propose=propose, model_label=MODEL, chunk_budget=8_000,
        concurrency=len(chunks),
    )
    assert result.calls_made == len(chunks)


def test_chunks_answered_out_of_order_give_the_same_result(conn):
    """The first chunk is the slowest to answer, and still wins the tie."""
    session = long_session(conn)

    def propose(prompt):
        line = first_line(prompt)
        # Earlier chunks answer later: the reverse of the order they were sent.
        time.sleep(max(0.0, 0.3 - line / 1000))
        return [proposal(term="idempotent", line=line)]

    one_at_a_time = detect(conn, session, propose=propose, model_label=MODEL, chunk_budget=8_000)
    at_once = detect(
        conn, session, propose=propose, model_label=MODEL, chunk_budget=8_000, concurrency=4
    )

    assert [c.as_dict() for c in at_once.ranked] == [c.as_dict() for c in one_at_a_time.ranked]
    # Flagged once per chunk; kept once, from the first chunk.
    [kept] = at_once.ranked
    first_chunk = chunk_windows(build_windows(conn, session), budget=8_000)[0]
    assert kept.line_start == first_chunk[0].line_start


def test_a_failed_chunk_fails_the_session(conn):
    session = long_session(conn)

    def propose(prompt):
        if first_line(prompt) > 1:
            raise RuntimeError("the provider fell over")
        return []

    with pytest.raises(RuntimeError, match="fell over"):
        detect(conn, session, propose=propose, model_label=MODEL, chunk_budget=8_000, concurrency=4)


def test_one_at_a_time_is_still_the_default(conn):
    """Callers that say nothing get the old behaviour, calls in turn."""
    session = long_session(conn)
    in_flight = 0
    most = 0
    lock = threading.Lock()

    def propose(_prompt):
        nonlocal in_flight, most
        with lock:
            in_flight += 1
            most = max(most, in_flight)
        time.sleep(0.01)
        with lock:
            in_flight -= 1
        return []

    detect(conn, session, propose=propose, model_label=MODEL, chunk_budget=8_000)
    assert most == 1


# --- configuration --------------------------------------------------------------


def test_a_hosted_endpoint_sends_several_at_once(monkeypatch):
    monkeypatch.delenv("UNROT_CONCURRENCY", raising=False)
    monkeypatch.setenv("UNROT_BASE_URL", "https://openrouter.ai/api/v1")
    assert ModelConfig.from_env().concurrency == DEFAULT_CONCURRENCY


def test_a_local_server_is_sent_one_at_a_time(monkeypatch):
    """Most local servers answer one request at a time; more would only queue."""
    monkeypatch.delenv("UNROT_CONCURRENCY", raising=False)
    monkeypatch.setenv("UNROT_BASE_URL", "http://localhost:11434/v1")
    assert ModelConfig.from_env().concurrency == 1


def test_the_setting_overrides_either(monkeypatch):
    monkeypatch.setenv("UNROT_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("UNROT_CONCURRENCY", "3")
    assert ModelConfig.from_env().concurrency == 3
    monkeypatch.setenv("UNROT_CONCURRENCY", "0")
    assert ModelConfig.from_env().concurrency == 1


# --- sessions side by side ----------------------------------------------------------


def test_two_sessions_that_meet_the_same_term_at_once_file_one_concept(conn):
    """The race `FILING` exists for: both detections finish together, and both
    sessions go to file "idempotent". Serialised, the second finds the concept
    the first just made."""
    lines = [user_line("make the webhook safe to retry"), assistant_line(LONG), user_line("ok, do it")]
    seed(conn, lines, session_id="a")
    seed(conn, lines, session_id="b")
    home = test_detector._HOME
    # The store exists before any analysis runs, as it does under the core.
    # Creating it from two threads at once is a different race (switching a new
    # file to WAL), and not one the app can reach.
    open_store(home).close()

    both_detected = threading.Barrier(2, timeout=5)

    def propose(_prompt):
        both_detected.wait()
        return [proposal(term="idempotent", line=2)]

    def decide(submission, shortlist):
        # A resolver model call takes seconds. Unserialised, this is the gap in
        # which the other session also finds no concept and makes its own.
        time.sleep(0.3)
        return strict(submission, shortlist)

    failures: list[BaseException] = []

    def analyse(session_id):
        store, raw = open_store(home), connect_raw(home)
        try:
            analyse_session(
                store, raw, session_id,
                propose=propose, decide=decide,
                detector_label=MODEL, resolver_label=MODEL,
            )
        except BaseException as exc:  # noqa: BLE001 - reported below, on the test thread
            failures.append(exc)
        finally:
            store.close()
            raw.close()

    threads = [threading.Thread(target=analyse, args=(s,)) for s in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not failures, failures

    store = open_store(home)
    try:
        concepts = store.execute("SELECT canonical_name FROM compiled_concepts").fetchall()
        created = store.execute(
            "SELECT count(*) FROM events WHERE event_type = 'concept_created'"
        ).fetchone()[0]
        encounters = store.execute(
            "SELECT session_id FROM compiled_encounters ORDER BY session_id"
        ).fetchall()
        analysed = store.execute(
            "SELECT session_id FROM compiled_sessions ORDER BY session_id"
        ).fetchall()
    finally:
        store.close()

    assert [row[0] for row in concepts] == ["idempotent"]
    assert created == 1
    assert [row[0] for row in encounters] == ["a", "b"]
    assert [row[0] for row in analysed] == ["a", "b"]
