"""Tests for the detector (PR-20).

No network and no model. `detect()` takes the proposer as an argument precisely
so the parts worth testing -- what gets dropped, what gets ranked, what gets
emitted, and what never gets emitted -- can be tested against a stub.

The claims here are mostly about restraint. A detector that finds something in
every session is easy; one that stays quiet when there is nothing, and refuses a
candidate it cannot anchor to a real line, is the thing worth guarding.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from unrot.capture import connect as connect_raw
from unrot.capture import ingest_file, resolve_pointer
from unrot.detector import (
    Candidate,
    build_windows,
    chunk_windows,
    detect,
    detector_version,
    group_windows,
)
from unrot.detector import prompt as prompt_module

MODEL = "test-model"


def user_line(text: str, **extra) -> str:
    record = {
        "type": "user",
        "sessionId": "s",
        "timestamp": "2026-09-20T10:00:00.000Z",
        "message": {"role": "user", "content": text},
    }
    record.update(extra)
    return json.dumps(record)


def tool_result_line(text: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": text}]
            },
        }
    )


def assistant_line(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    )


LONG = (
    "Making the webhook handler idempotent so the retry is safe: the second "
    "delivery recognises the key and returns the first result."
)


#: The store root for the test currently running. sqlite3.Connection does not
#: take attributes, and threading `home` through every call site would bury the
#: tests in plumbing that has nothing to do with what they assert.
_HOME: pathlib.Path | None = None


@pytest.fixture
def conn(tmp_path):
    global _HOME
    _HOME = tmp_path / "home"
    connection = connect_raw(_HOME)
    yield connection
    connection.close()


def seed(conn, lines, session_id="sess-1"):
    home = _HOME
    source = pathlib.Path(home).parent / "src-transcripts"
    source.mkdir(parents=True, exist_ok=True)
    path = source / f"{session_id}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ingest_file(path, conn=conn, root=home)
    return session_id


def stub(*batches):
    """A proposer that returns a canned batch per call."""
    calls = list(batches)

    def propose(_prompt):
        return calls.pop(0) if calls else []

    return propose


def proposal(term="idempotent", line=2, signal="accepted", importance="central", **extra):
    base = {
        "term": term,
        "paraphrase": f"A retry design leaned on {term}, and it was waved through.",
        "assistant_line": line,
        "acceptance_line": 3,
        "signal": signal,
        "importance": importance,
    }
    base.update(extra)
    return base


SESSION = [user_line("add retries"), assistant_line(LONG), user_line("ok go ahead")]


# --- done when: candidates carry paraphrase, pointer, detector_version -----


def test_a_candidate_carries_everything_the_resolver_needs(conn):
    session = seed(conn, SESSION)
    result = detect(conn, session, propose=stub([proposal()]), model_label=MODEL)

    assert len(result.emitted) == 1
    c = result.emitted[0]
    assert c.term == "idempotent"
    assert c.paraphrase
    assert c.session_id == session
    assert (c.line_start, c.line_end) == (2, 3)
    assert c.detector_version == detector_version(MODEL)
    assert c.rank == 1


def test_the_pointer_a_candidate_carries_actually_resolves(conn):
    """A pointer nobody checked is a pointer that resolves to the wrong thing."""
    session = seed(conn, SESSION)
    result = detect(conn, session, propose=stub([proposal()]), model_label=MODEL)
    c = result.emitted[0]

    lines = resolve_pointer(c.session_id, c.line_start, c.line_end, root=_HOME)
    assert json.loads(lines[0])["type"] == "assistant"
    assert json.loads(lines[-1])["message"]["content"] == "ok go ahead"


def test_detector_version_records_model_and_prompt(conn):
    version = detector_version("some-model")
    assert "some-model" in version
    assert prompt_module.prompt_id() in version
    # A different model is a different version, so history says the machine moved.
    assert detector_version("other-model") != version


# --- done when: nothing is manufactured when the session is clean ----------


def test_a_clean_session_emits_nothing(conn):
    session = seed(conn, SESSION)
    result = detect(conn, session, propose=stub([]), model_label=MODEL)

    assert result.emitted == []
    assert result.ranked == []


def test_there_is_no_floor_that_forces_a_flag(conn):
    """A rich session the model found nothing in still emits nothing.

    No "well, surface the best window anyway" fallback: the empty answer has to
    survive all the way out, or journey 3's empty state is a lie.
    """
    lines = []
    for _ in range(8):
        lines.append(assistant_line(LONG))
        lines.append(user_line("ok go ahead"))
    session = seed(conn, lines)

    result = detect(conn, session, propose=stub([]), model_label=MODEL)

    assert len(build_windows(conn, session)) == 8
    assert result.windows_examined == 8
    assert result.calls_made == 1
    assert result.emitted == []
    assert result.ranked == []


# --- the behavioural signal ------------------------------------------------


def test_a_clarifying_question_is_not_a_gap(conn):
    """The 2026-09-01 decision: asking what it means is evidence of understanding."""
    session = seed(conn, SESSION)
    result = detect(
        conn, session, propose=stub([proposal(signal="questioned")]), model_label=MODEL
    )

    assert result.emitted == []
    # Dropped entirely, not merely demoted -- it is not a candidate at all.
    assert result.ranked == []


def test_unclear_is_ranked_but_never_emitted(conn):
    """No next turn means nothing was accepted, so there is no observed gap."""
    session = seed(conn, SESSION)
    result = detect(
        conn, session, propose=stub([proposal(signal="unclear")]), model_label=MODEL
    )

    assert result.emitted == []
    assert [c.term for c in result.ranked] == ["idempotent"]
    assert result.suppressed == 1


# --- done when: volume lands in the 1-2 range ------------------------------


def test_the_budget_caps_what_reaches_the_resolver(conn):
    session = seed(conn, SESSION)
    many = [proposal(term=f"term-{i}") for i in range(6)]
    result = detect(conn, session, propose=stub(many), model_label=MODEL, max_candidates=2)

    assert len(result.emitted) == 2
    assert len(result.ranked) == 6
    assert result.suppressed == 4


def test_central_outranks_supporting(conn):
    session = seed(conn, SESSION)
    result = detect(
        conn,
        session,
        propose=stub([
            proposal(term="minor", importance="supporting"),
            proposal(term="major", importance="central"),
        ]),
        model_label=MODEL,
        max_candidates=1,
    )

    assert [c.term for c in result.emitted] == ["major"]
    assert [c.rank for c in result.ranked] == [1, 2]


# --- done when: re-running gives the same answer ---------------------------


def test_the_same_input_gives_the_same_answer(conn):
    session = seed(conn, SESSION)
    batch = [
        proposal(term="beta", importance="central"),
        proposal(term="alpha", importance="central"),
        proposal(term="gamma", importance="supporting"),
    ]
    first = detect(conn, session, propose=stub(list(batch)), model_label=MODEL)
    # Same candidates, different order out of the model.
    second = detect(conn, session, propose=stub(list(reversed(batch))), model_label=MODEL)

    assert [c.term for c in first.ranked] == [c.term for c in second.ranked]
    assert [c.as_dict() for c in first.emitted] == [c.as_dict() for c in second.emitted]


def test_the_same_term_twice_is_one_gap(conn):
    session = seed(conn, SESSION)
    result = detect(
        conn,
        session,
        propose=stub([proposal(term="Idempotent"), proposal(term="idempotent")]),
        model_label=MODEL,
    )
    assert len(result.ranked) == 1


# --- the model is an untrusted source --------------------------------------


def test_a_candidate_anchored_to_a_line_that_does_not_exist_is_dropped(conn):
    """A wrong pointer is worse than a missing candidate: it teaches the wrong thing."""
    session = seed(conn, SESSION)
    result = detect(conn, session, propose=stub([proposal(line=9999)]), model_label=MODEL)
    assert result.ranked == []


def test_garbage_from_the_model_does_not_crash_the_run(conn):
    session = seed(conn, SESSION)
    junk = [
        "not even a dict",
        None,
        {},
        {"term": "", "paraphrase": "x", "assistant_line": 2, "signal": "accepted"},
        {"term": "x", "paraphrase": "", "assistant_line": 2, "signal": "accepted"},
        {"term": "x", "paraphrase": "y", "assistant_line": 2, "signal": "enthusiastic"},
        {"term": "x", "paraphrase": "y", "assistant_line": "two", "signal": "accepted"},
        proposal(term="survivor"),
    ]
    result = detect(conn, session, propose=stub(junk), model_label=MODEL)
    assert [c.term for c in result.ranked] == ["survivor"]


def test_an_unknown_importance_degrades_rather_than_drops(conn):
    session = seed(conn, SESSION)
    result = detect(
        conn, session, propose=stub([proposal(importance="vital")]), model_label=MODEL
    )
    assert result.emitted[0].importance == "supporting"


def test_a_model_invented_acceptance_line_loses_to_the_real_one(conn):
    session = seed(conn, SESSION)
    result = detect(
        conn, session, propose=stub([proposal(acceptance_line=77)]), model_label=MODEL
    )
    assert result.emitted[0].line_end == 3


# --- windows: the spike's bug, fixed by capture ----------------------------


def test_a_tool_result_is_not_the_humans_next_turn(conn):
    """The PR-6 spike filtered only isMeta, so tool output could read as assent."""
    session = seed(
        conn,
        [
            user_line("add retries"),
            assistant_line(LONG),
            tool_result_line("Edit applied to handler.py"),
            user_line("actually what does that mean?"),
        ],
    )
    windows = build_windows(conn, session)

    assert len(windows) == 1
    assert windows[0].human_line == 4
    assert windows[0].human_text == "actually what does that mean?"


def test_injected_and_sidechain_turns_are_not_the_humans_next_turn(conn):
    session = seed(
        conn,
        [
            assistant_line(LONG),
            user_line("<system-reminder>noise", isMeta=True),
            user_line("subagent chatter", isSidechain=True),
            user_line("ok go ahead"),
        ],
    )
    windows = build_windows(conn, session)
    assert windows[0].human_text == "ok go ahead"


def test_a_short_acknowledgement_is_not_worth_a_window(conn):
    session = seed(conn, [user_line("hi"), assistant_line("Done."), user_line("ok")])
    assert build_windows(conn, session) == []


def test_a_session_that_ends_on_the_assistant_has_no_acceptance(conn):
    session = seed(conn, [user_line("go"), assistant_line(LONG)])
    windows = build_windows(conn, session)
    assert windows[0].human_line is None
    assert windows[0].line_end == windows[0].assistant_line


# --- chunking --------------------------------------------------------------


def test_a_normal_session_is_one_call(conn):
    session = seed(conn, SESSION)
    result = detect(conn, session, propose=stub([proposal()]), model_label=MODEL)
    assert result.calls_made == 1


def test_a_long_session_splits_rather_than_being_truncated(conn):
    lines = []
    for _ in range(12):
        lines.append(assistant_line(LONG * 20))
        lines.append(user_line("ok go ahead"))
    session = seed(conn, lines)

    windows = build_windows(conn, session)
    chunks = chunk_windows(windows, budget=8_000)
    assert len(chunks) > 1
    assert sum(len(c) for c in chunks) == len(windows)

    result = detect(conn, session, propose=stub([], []), model_label=MODEL, chunk_budget=8_000)
    assert result.calls_made == len(chunks)


def test_one_reply_to_several_turns_is_sent_once_not_once_per_turn(conn):
    """The reply is repeated per-window otherwise, which multiplied real prompts 9x.

    Several assistant turns followed by a single human reply is one exchange:
    the person answered all of it at once. Rendering it as N windows, each
    carrying a full copy of a reply that can run to thousands of characters, is
    both a worse account of what happened and enormously more expensive.
    """
    reply = "ok go ahead, that all sounds right to me"
    session = seed(
        conn,
        [assistant_line(LONG), assistant_line(LONG + " more"), assistant_line(LONG + " and more"), user_line(reply)],
    )
    windows = build_windows(conn, session)
    assert len(windows) == 3
    assert [len(g) for g in group_windows(windows)] == [3]

    rendered = prompt_module.format_windows(windows)
    assert rendered.count(reply) == 1
    # ...and every assistant turn is still there to be judged.
    assert rendered.count("--- assistant (line") == 3


def test_chunking_never_separates_a_reply_from_what_it_answered(conn):
    """Splitting mid-exchange would ask the model to judge an acceptance it cannot see."""
    lines = []
    for _ in range(6):
        lines.extend([assistant_line(LONG * 30), assistant_line(LONG * 30)])
        lines.append(user_line("ok go ahead"))
    session = seed(conn, lines)

    windows = build_windows(conn, session)
    chunks = chunk_windows(windows, budget=10_000)
    assert len(chunks) > 1
    for chunk in chunks:
        for group in group_windows(chunk):
            # A group that survived chunking still knows what reply it got.
            assert len({w.human_line for w in group}) == 1
    assert sum(len(c) for c in chunks) == len(windows)


def test_an_oversized_exchange_still_gets_sent(conn):
    """One giant turn must not silently vanish for exceeding the budget."""
    session = seed(conn, [assistant_line(LONG * 500), user_line("ok go ahead")])
    chunks = chunk_windows(build_windows(conn, session), budget=1_000)
    assert len(chunks) == 1 and len(chunks[0]) == 1


def test_chunking_an_empty_session_asks_nothing(conn):
    assert chunk_windows([]) == []
    session = seed(conn, [user_line("hello")])
    calls = []

    def counting(prompt):
        calls.append(prompt)
        return []

    result = detect(conn, session, propose=counting, model_label=MODEL)
    assert calls == []
    assert result.calls_made == 0


# --- structural ------------------------------------------------------------


def test_the_detector_cannot_write_to_the_graph():
    """PR-21's resolver is the single write path. Enforced by absence, not by care.

    Checked against the parsed import graph rather than the file text, so a
    docstring explaining the rule cannot be mistaken for breaking it.
    """
    package = pathlib.Path(__file__).resolve().parents[1] / "src" / "unrot" / "detector"
    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level 2 from inside unrot.detector means `unrot.<x>`
                prefix = "." * node.level
                names = [f"{prefix}{node.module or ''}"]
            else:
                continue
            for name in names:
                assert "store" not in name, f"{module.name} imports {name}"


def test_editing_the_prompt_changes_the_recorded_version(monkeypatch):
    before = prompt_module.prompt_id()
    monkeypatch.setattr(prompt_module, "TEMPLATE", prompt_module.TEMPLATE + " extra")
    assert prompt_module.prompt_id() != before


def test_the_prompt_tells_the_model_that_empty_is_allowed():
    """The one instruction that journey 3 depends on surviving a prompt edit."""
    assert "empty list is a correct" in prompt_module.TEMPLATE
    assert "stands alone" in prompt_module.TEMPLATE.lower()
