"""Tests for the analysis graph.

Two things are new when analysis becomes a graph, and these tests are about
exactly those two. **One run per session**: every model call nests under the
node that made it, under a root naming the session -- which is the whole reason
for the change. **Chunks in parallel**: faster, but only safe if parallelism
changes nothing about the answer and never lets a SQLite connection cross a
thread. Everything else the graph does is already covered by the analysis,
triage and regeneration tests, which now run through it.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from langchain_core.runnables import RunnableLambda
from langchain_core.tracers.base import BaseTracer

from unrot.capture import connect as connect_raw
from unrot.capture import paths
from unrot.capture.ingest import SCHEMA_PATH as RAW_SCHEMA
from unrot.detector import detect
from unrot.pipeline import Deps, run_session
from unrot.resolver import manual, resolve, strict
from unrot.store import append, compile_state
from unrot.store.__main__ import open_store


@pytest.fixture
def home(tmp_path):
    paths.ensure_layout(tmp_path)
    return tmp_path


@pytest.fixture
def conn(home):
    connection = open_store(home)
    yield connection
    connection.close()


def captured(home, session_id: str, exchanges: int) -> sqlite3.Connection:
    """A session of `exchanges` explanations, each waved through."""
    raw = sqlite3.connect(paths.raw_db_path(home))
    raw.row_factory = sqlite3.Row
    raw.executescript(RAW_SCHEMA.read_text(encoding="utf-8"))
    raw.execute(
        "INSERT INTO raw_sessions (session_id, source_path, copy_path,"
        " prefix_sha256, first_ingested_at, last_ingested_at)"
        " VALUES (?, '/x', '/y', '', '2026-09-01', '2026-09-01')",
        (session_id,),
    )
    for i in range(exchanges):
        for line_no, role, text in (
            (2 * i + 1, "assistant", f"Exchange {i}: we lean on term{i} here without saying why. " * 8),
            (2 * i + 2, "user", "ok go"),
        ):
            raw.execute(
                "INSERT INTO raw_turns (session_id, line_no, seq, role, text,"
                " is_meta, is_sidechain, occurred_at)"
                " VALUES (?, ?, 0, ?, ?, 0, 0, '2026-09-01')",
                (session_id, line_no, role, text),
            )
    raw.commit()
    return raw


def flags_every_window(prompt_text: str) -> list[dict]:
    """A detector that flags `termN` for every window it is shown."""
    import re

    return [
        {
            "term": f"term{int(n)}",
            "paraphrase": f"term{n}, leaned on.",
            "assistant_line": 2 * int(n) + 1,
            "acceptance_line": 2 * int(n) + 2,
            "signal": "accepted",
            "importance": "central",
        }
        for n in sorted(set(re.findall(r"Exchange (\d+):", prompt_text)), key=int)
    ]


class Recorder(BaseTracer):
    """Every run the graph starts, with its parent -- a LangSmith trace, locally."""

    def __init__(self):
        super().__init__()
        self.runs = []

    def _persist_run(self, run):
        pass

    def _start_trace(self, run):
        super()._start_trace(run)
        self.runs.append(run)


def deps(conn, raw, **overrides):
    settings = dict(
        conn=conn, raw=raw, propose=flags_every_window, decide=strict,
        detector_label="t", resolver_label="none",
    )
    return Deps(**{**settings, **overrides})


# ---------------------------------------------------------------------------
# One run per session
# ---------------------------------------------------------------------------


def test_a_session_is_one_trace_with_every_call_under_its_node(conn, home, monkeypatch):
    """The point of the graph. A model call made inside a node is a child of that
    node, and every node is a child of one root that names the session."""
    raw = captured(home, "s0", 2)

    def detection(prompt_text):
        # Stands in for the real client: a LangChain runnable, traced the same way.
        return RunnableLambda(flags_every_window, name="detection").invoke(prompt_text)

    recorder = Recorder()
    from unrot import pipeline

    real_invoke = pipeline.graph().invoke
    monkeypatch.setattr(
        pipeline.graph(), "invoke",
        lambda state, config, **kw: real_invoke(state, {**config, "callbacks": [recorder]}, **kw),
    )
    run_session("s0", deps(conn, raw, propose=detection))

    by_id = {r.id: r for r in recorder.runs}
    [root] = [r for r in recorder.runs if r.parent_run_id is None]
    assert root.name == "analyse session"
    assert root.extra["metadata"]["session_id"] == "s0"
    assert root.extra["metadata"]["detector_version"].startswith("detector/")

    parents = {r.name: by_id[r.parent_run_id].name for r in recorder.runs if r.parent_run_id}
    for node in ("windows", "propose", "rank", "triage", "resolve", "record"):
        assert parents[node] == "analyse session", node
    assert parents["detection"] == "propose"


# ---------------------------------------------------------------------------
# Chunks in parallel
# ---------------------------------------------------------------------------


def test_parallel_chunks_give_the_same_answer_as_one_after_another(conn, home):
    """Order decides which mention of a term wins the dedupe, and parallel calls
    finish in any order. The graph must rank exactly as the sequential detector."""
    raw = captured(home, "s0", 6)
    threads = set()

    def slow_in_reverse(prompt_text):
        threads.add(threading.get_ident())
        found = flags_every_window(prompt_text)
        # Earlier chunks finish last, so arrival order is the reverse of chunk order.
        time.sleep(0.02 * (6 - int(found[0]["term"][4:])) if found else 0)
        return found

    sequential = detect(raw, "s0", propose=flags_every_window, model_label="t", max_candidates=2)
    run = run_session("s0", deps(conn, raw, propose=slow_in_reverse, chunk_budget=500))

    assert run.result.calls_made == 6
    assert len(threads) > 1, "the chunks should have run in parallel"
    assert [c.term for c in run.result.ranked] == [c.term for c in sequential.ranked]
    assert [r.canonical_name for r in run.resolutions] == ["term0", "term1"]


def test_the_store_is_only_touched_on_the_calling_thread(conn, home):
    """SQLite raises the moment a connection crosses threads, so a clean run with
    several parallel chunks, then triage, resolution and recording, is the proof."""
    raw = captured(home, "s0", 4)

    run = run_session("s0", deps(conn, raw, chunk_budget=500))

    assert run.result.calls_made == 4
    assert conn.execute(
        "SELECT 1 FROM compiled_sessions WHERE session_id = 's0'"
    ).fetchone()


def test_a_session_with_nothing_to_read_is_still_recorded_as_examined(conn, home):
    """No windows, no chunks, no calls -- and still "we looked"."""
    raw = captured(home, "s0", 0)
    calls = []

    run = run_session("s0", deps(conn, raw, propose=lambda p: calls.append(p) or []))

    assert calls == []
    assert run.resolutions == []
    assert conn.execute(
        "SELECT candidates_found FROM compiled_sessions WHERE session_id = 's0'"
    ).fetchone()[0] == 0


def test_a_failing_model_call_leaves_the_session_pending(conn, home):
    """Recording is the last node, so a failure anywhere above it means the
    session is not marked examined and the next run tries again."""
    raw = captured(home, "s0", 1)

    def broken(prompt_text):
        raise TimeoutError("the model is down")

    with pytest.raises(TimeoutError):
        run_session("s0", deps(conn, raw, propose=broken))

    assert conn.execute(
        "SELECT 1 FROM compiled_sessions WHERE session_id = 's0'"
    ).fetchone() is None


# ---------------------------------------------------------------------------
# Filing, one session at a time
# ---------------------------------------------------------------------------
#
# `FILING` takes the writing nodes one session at a time, but SQLite has a lock
# of its own: a connection that has written holds the database's write lock
# until it commits. A node that let go of `FILING` with its writes uncommitted
# left the next session to take `FILING`, write, and wait on SQLite -- while the
# first waited on `FILING` to get back to its own writes. Neither moved until
# the second gave up with "database is locked". In a live run of 36 sessions,
# 6 at a time, four failed that way (PR-37).


def knows(conn, known=("git", "grep", "json"), unknown=("mvcc", "raft", "crdt")):
    """A map to judge against: concepts dismissed as known, confirmed as gaps.
    Without one, triage judges nothing and so writes nothing."""
    for names, judgment in ((known, "encounter_dismissed"), (unknown, "encounter_confirmed")):
        for name in names:
            filed = resolve(conn, manual(name, f"what {name} is"), decide=strict, recompile=False)
            append(conn, judgment, {"encounter_id": filed.encounter_id})
    conn.commit()
    compile_state(conn)


def unfamiliar(term, gloss, kmap):
    """A classifier sure every term is new to the person, so triage records a
    judgment and passes the candidate on to be filed."""
    return {"p_knows": 0.1, "confidence": 0.9, "model": "stub"}


unfamiliar.model = "stub"


class Watched:
    """`FILING`, noting each time it is let go whether the store still had
    writes open."""

    def __init__(self, conn):
        self._lock = threading.Lock()
        self._conn = conn
        self.released_open: list[bool] = []

    def __enter__(self):
        self._lock.acquire()

    def __exit__(self, *exc):
        self.released_open.append(self._conn.in_transaction)
        self._lock.release()


class SteppingBack:
    """`FILING`, where a session that lets it go steps back before asking again,
    so a session already waiting takes the next turn. The OS promises neither
    order; this makes the one that failed live the one that always happens."""

    def __init__(self):
        self._lock = threading.Lock()

    def __enter__(self):
        self._lock.acquire()

    def __exit__(self, *exc):
        self._lock.release()
        time.sleep(0.2)


@pytest.mark.parametrize("recompile", [True, False], ids=["analysis", "regeneration"])
def test_no_writing_node_lets_go_of_filing_with_its_writes_open(conn, home, monkeypatch, recompile):
    """Triage, resolve and record each write, and each commits before another
    session can take its turn. Regeneration is the sharper case: it does not
    recompile after each resolution, so nothing was committing for it."""
    from unrot import pipeline

    knows(conn)
    raw = captured(home, "s0", 1)
    filing = Watched(conn)
    monkeypatch.setattr(pipeline, "FILING", filing)

    run = run_session("s0", deps(conn, raw, judge=unfamiliar, recompile=recompile))

    assert [r.canonical_name for r in run.resolutions] == ["term0"]
    assert conn.execute(
        "SELECT count(*) FROM events WHERE event_type = 'familiarity_judged'"
    ).fetchone()[0] == 1
    # One release each for triage, resolve and record.
    assert filing.released_open == [False, False, False]


def test_two_sessions_filing_side_by_side_both_finish(home, monkeypatch):
    """The live failure, offline: two sessions triaged against a map, the second
    taking `FILING` the moment the first lets it go. Both must be filed."""
    from unrot import pipeline

    seed = open_store(home)
    knows(seed)
    seed.close()
    for session_id in ("a", "b"):
        captured(home, session_id, 1).close()
    monkeypatch.setattr(pipeline, "FILING", SteppingBack())

    both_proposed = threading.Barrier(2, timeout=5)

    def propose(prompt_text):
        # Both reach triage together, as they do when the app sends several.
        both_proposed.wait()
        return flags_every_window(prompt_text)

    failures: list[BaseException] = []

    def analyse(session_id):
        store, raw = open_store(home), connect_raw(home)
        try:
            run_session(session_id, deps(store, raw, propose=propose, judge=unfamiliar))
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
        analysed = store.execute(
            "SELECT session_id FROM compiled_sessions ORDER BY session_id"
        ).fetchall()
        filed = store.execute(
            "SELECT session_id FROM compiled_encounters"
            " WHERE session_id IS NOT NULL ORDER BY session_id"
        ).fetchall()
    finally:
        store.close()
    assert [row[0] for row in analysed] == ["a", "b"]
    assert [row[0] for row in filed] == ["a", "b"]
