"""Analysing one session, as a LangGraph graph.

    windows ──▶ propose (one per chunk, in parallel) ──▶ rank ──▶ triage ──▶ resolve ──▶ record

Before this was a graph it was a loop in `analyse_session` and another in
`regenerate`, and every model call it made was its own unrelated trace. As a
graph, one session is one run: in LangSmith the detector's chunk calls, each
familiarity judgment and each resolution sit under the node that made them,
under a root that names the session. The graph shape is also where the rest of
the detector funnel (`docs/handover-20260926-detector-rethink.md`) goes: each
later layer is another node between `rank` and `resolve`, adding labels to the
state rather than deleting candidates from it.

**The one rule the shape has to keep: nodes that touch the store run alone.**
LangGraph runs a step with a single task on the calling thread and fans a
multi-task step out to worker threads. SQLite connections here are
deliberately single-thread (see `unrot.api.app.stores`), so every node that
reads or writes a connection is the only task in its step, and the parallel
`propose` nodes never touch one -- they only call the model. Breaking that is
not silent: SQLite raises the moment a connection crosses threads, and
`tests/test_pipeline.py` checks it.

Every model is still injected -- `propose`, `decide`, `judge` -- through the
graph's runtime context, not built here. That seam is what lets the whole graph
run in tests with no network, no key and no model.
"""

from __future__ import annotations

import operator
import sqlite3
from dataclasses import dataclass, field, replace
from functools import cache
from typing import Annotated, Any, TypedDict

from .detector import detector_version
from .detector.candidates import Candidate, DetectionResult
from .detector.detect import DEFAULT_MAX_CANDIDATES, assemble, plan, propose_chunk
from .resolver import (
    Resolution,
    fingerprint,
    from_candidate,
    record_analysis,
    record_detection,
    resolve,
)
from .triage import Triage, Verdict, triage

#: How many chunk calls may be in flight at once. Enough that a long session
#: stops being N round trips back to back; few enough not to trip a provider's
#: rate limit on the first long session someone analyses.
MAX_CONCURRENCY = 4


@dataclass
class Deps:
    """What the graph runs against. Passed as LangGraph's runtime context, so
    none of it is in the state and none of it is traced as data."""

    conn: sqlite3.Connection
    raw: sqlite3.Connection
    propose: Any
    decide: Any
    detector_label: str
    resolver_label: str
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    judge: Any = None
    triage_label: str = "none"
    #: Encounter ids a user has judged. Regeneration must not touch them.
    protected: frozenset = frozenset()
    #: Live analysis recompiles after each resolution so the next one sees it;
    #: regeneration compiles once per session instead.
    recompile: bool = True
    #: Characters per detector call. Smaller means more, parallel, calls.
    chunk_budget: int = 40_000


class State(TypedDict, total=False):
    session_id: str
    windows: list
    chunks: list
    #: (chunk index, what that chunk proposed). Parallel nodes append here; the
    #: index puts them back in order, since the order decides which mention of
    #: a term wins the dedupe.
    proposals: Annotated[list, operator.add]
    result: DetectionResult
    emitted: list[Candidate]
    held_back: list[Verdict]
    resolutions: list[Resolution]


class Chunk(TypedDict):
    index: int
    chunk: list


def _windows(state: State, runtime) -> dict:
    deps = runtime.context
    windows, chunks = plan(deps.raw, state["session_id"], chunk_budget=deps.chunk_budget)
    return {"windows": windows, "chunks": chunks}


def _fan_out(state: State):
    from langgraph.types import Send

    if not state["chunks"]:
        # No windows means nothing to propose. `rank` still runs, so an empty
        # session is recorded as examined -- journey 3's "we looked".
        return "rank"
    return [Send("propose", Chunk(index=i, chunk=c)) for i, c in enumerate(state["chunks"])]


def _propose(chunk: Chunk, runtime) -> dict:
    """One chunk, one model call. Runs in parallel: must never touch the store."""
    deps = runtime.context
    found = propose_chunk(deps.propose, chunk["chunk"], max_candidates=deps.max_candidates)
    return {"proposals": [(chunk["index"], found)]}


def _rank(state: State, runtime) -> dict:
    deps = runtime.context
    raw = [item for _, found in sorted(state.get("proposals", []), key=lambda p: p[0]) for item in found]
    result = assemble(
        raw,
        session_id=state["session_id"],
        version=detector_version(deps.detector_label),
        windows=state["windows"],
        calls_made=len(state["chunks"]),
        max_candidates=deps.max_candidates,
    )
    return {"result": result}


def triage_gaps(conn, ranked, *, judge, max_candidates: int, model_label: str) -> Triage:
    """Triage the detector's gaps -- all of them, before the budget.

    The detector's own `emitted` is already cut to the budget, and a candidate
    triage holds back must not have used up a place another could fill. So this
    starts from `ranked`, which keeps everything in rank order.
    """
    return triage(
        conn,
        [c for c in ranked if c.is_gap],
        judge=judge,
        max_candidates=max_candidates,
        model_label=model_label,
    )


def _triage(state: State, runtime) -> dict:
    deps = runtime.context
    result = state["result"]
    if deps.judge is None:
        return {"emitted": result.emitted, "held_back": []}
    triaged = triage_gaps(
        deps.conn, result.ranked, judge=deps.judge,
        max_candidates=deps.max_candidates, model_label=deps.triage_label,
    )
    return {"emitted": triaged.emitted, "held_back": triaged.held_back}


def _resolve(state: State, runtime) -> dict:
    deps = runtime.context
    resolutions = []
    for candidate in state["emitted"]:
        submission = from_candidate(candidate)
        # Regeneration's conservative rule, at the one line where it is
        # enforced: the user has already said something about this encounter,
        # and a fresh `encounter_recorded` would supersede the paraphrase they
        # judged. Live analysis passes no protected ids.
        if fingerprint(submission) in deps.protected:
            continue
        # One at a time, and with live analysis recompiling after each: the
        # next candidate's resolution reads compiled state, and a term met twice
        # in a session must find the concept the first mention just created.
        resolutions.append(
            resolve(
                deps.conn, submission, decide=deps.decide,
                model_label=deps.resolver_label, recompile=deps.recompile,
            )
        )
    return {"resolutions": resolutions}


def _record(state: State, runtime) -> dict:
    """Written last, so a failure anywhere above leaves the session pending."""
    deps = runtime.context
    result, emitted = state["result"], state["emitted"]
    # What was filed, not what the detector's budget picked: with triage the two
    # differ, and export joins a user's verdict to the `emitted` flag.
    record_detection(deps.conn, replace(result, emitted=emitted), max_candidates=deps.max_candidates)
    record_analysis(
        deps.conn,
        state["session_id"],
        candidates_found=len(emitted),
        detector_version=result.detector_version,
        recompile=deps.recompile,
    )
    return {}


@cache
def graph():
    """The compiled graph. Built once; it holds no state between runs."""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(State, context_schema=Deps)
    builder.add_node("windows", _windows)
    builder.add_node("propose", _propose)
    builder.add_node("rank", _rank)
    builder.add_node("triage", _triage)
    builder.add_node("resolve", _resolve)
    builder.add_node("record", _record)
    builder.add_edge(START, "windows")
    builder.add_conditional_edges("windows", _fan_out, ["propose", "rank"])
    builder.add_edge("propose", "rank")
    builder.add_edge("rank", "triage")
    builder.add_edge("triage", "resolve")
    builder.add_edge("resolve", "record")
    builder.add_edge("record", END)
    return builder.compile()


@dataclass
class Run:
    session_id: str
    result: DetectionResult
    emitted: list[Candidate]
    held_back: list[Verdict] = field(default_factory=list)
    resolutions: list[Resolution] = field(default_factory=list)


def run_session(session_id: str, deps: Deps, *, run_name: str = "analyse session") -> Run:
    """Run the graph over one session. One run, one trace.

    The trace is named for what it is and carries the session and every version
    that shaped the answer, so a LangSmith search for a session finds all of it.
    """
    out = graph().invoke(
        {"session_id": session_id, "proposals": []},
        config={
            "run_name": run_name,
            "tags": ["unrot", run_name],
            "metadata": {
                "session_id": session_id,
                "detector_version": detector_version(deps.detector_label),
                "resolver": deps.resolver_label,
                "triage": deps.triage_label,
            },
            "max_concurrency": MAX_CONCURRENCY,
        },
        context=deps,
    )
    return Run(
        session_id=session_id,
        result=out["result"],
        emitted=out["emitted"],
        held_back=out.get("held_back", []),
        resolutions=out.get("resolutions", []),
    )
