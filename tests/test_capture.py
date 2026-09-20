"""Tests for capture (PR-19).

The ticket's "done when" list is the spine of this file: one test per claim,
plus the parser distinctions that the rest of the product is built on. The most
important of those is that a `tool_result` is not a human turn -- if that ever
stops holding, the detector starts treating tool output as a person accepting
something, and the whole signal is noise.
"""

from __future__ import annotations

import hashlib
import json
import pytest

from unrot.capture import (
    connect,
    human_turns,
    ingest_all,
    ingest_file,
    parse_line,
    resolve_pointer,
)
from unrot.capture import paths


# --- building fake transcripts ---------------------------------------------


def user_line(text: str, **extra) -> str:
    record = {
        "type": "user",
        "sessionId": "sess-fake",
        "timestamp": "2026-09-20T10:00:00.000Z",
        "entrypoint": "cli",
        "version": "2.1.271",
        "cwd": "/repo",
        "gitBranch": "main",
        "message": {"role": "user", "content": text},
    }
    record.update(extra)
    return json.dumps(record)


def tool_result_line(text: str, **extra) -> str:
    """A `user`-typed line that no human typed a word of."""
    record = {
        "type": "user",
        "sessionId": "sess-fake",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu-1", "content": text}
            ],
        },
    }
    record.update(extra)
    return json.dumps(record)


def assistant_line(text: str, *, thinking: str | None = None, tool: str | None = None) -> str:
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "id": "tu-1", "name": tool, "input": {"a": 1}})
    return json.dumps(
        {"type": "assistant", "sessionId": "sess-fake", "message": {"content": content}}
    )


def write_transcript(directory, session_id: str, lines: list[str], *, complete=True):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    body = "\n".join(lines)
    path.write_text(body + ("\n" if complete else ""), encoding="utf-8")
    return path


@pytest.fixture
def home(tmp_path):
    return tmp_path / "unrot-home"


@pytest.fixture
def projects(tmp_path):
    return tmp_path / "claude" / "projects"


@pytest.fixture
def conn(home):
    connection = connect(home)
    yield connection
    connection.close()


SAMPLE = [
    user_line("add retries to the webhook handler"),
    assistant_line(
        "Making the handler idempotent so retries are safe.",
        thinking="They have not asked what idempotent means.",
        tool="Edit",
    ),
    tool_result_line("Edit applied to handler.py"),
    user_line("ok go ahead"),
]


# --- done when: a real session can be ingested and durably retained --------


def test_ingest_copies_the_transcript_and_indexes_it(conn, home, projects):
    source = write_transcript(projects / "repo", "sess-1", SAMPLE)
    result = ingest_file(source, conn=conn, root=home)

    assert result.status == "new"
    assert result.lines_total == 4

    copy = paths.copy_path(home, "sess-1")
    assert copy.exists()
    # Byte-for-byte. Anything else and stored line numbers stop meaning anything.
    assert copy.read_bytes() == source.read_bytes()

    row = conn.execute("SELECT * FROM raw_sessions").fetchone()
    assert row["session_id"] == "sess-1"
    assert row["entrypoint"] == "cli"
    assert row["cc_version"] == "2.1.271"
    assert row["cwd"] == "/repo"


def test_the_raw_directory_carries_its_own_never_sync_marker(conn, home):
    marker = paths.raw_dir(home) / ".gitignore"
    assert marker.exists()
    assert marker.read_text(encoding="utf-8").strip().endswith("*")


# --- done when: pointers resolve back to exact transcript locations --------


def test_pointer_resolves_to_the_exact_lines(conn, home, projects):
    source = write_transcript(projects / "repo", "sess-1", SAMPLE)
    ingest_file(source, conn=conn, root=home)

    lines = resolve_pointer("sess-1", 2, 3, root=home)
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "assistant"
    assert json.loads(lines[1])["message"]["content"][0]["type"] == "tool_result"

    single = resolve_pointer("sess-1", 4, root=home)
    assert json.loads(single[0])["message"]["content"] == "ok go ahead"


def test_pointer_resolves_against_our_copy_not_the_original(conn, home, projects):
    """PR-15's whole point: the original may be gone."""
    source = write_transcript(projects / "repo", "sess-1", SAMPLE)
    ingest_file(source, conn=conn, root=home)
    source.unlink()

    lines = resolve_pointer("sess-1", 1, root=home)
    assert json.loads(lines[0])["message"]["content"] == "add retries to the webhook handler"


def test_unknown_session_and_bad_range_are_refused_clearly(conn, home, projects):
    ingest_file(write_transcript(projects / "r", "sess-1", SAMPLE), conn=conn, root=home)
    with pytest.raises(FileNotFoundError):
        resolve_pointer("sess-nope", 1, root=home)
    with pytest.raises(ValueError):
        resolve_pointer("sess-1", 3, 2, root=home)


# --- done when: re-ingesting is idempotent ---------------------------------


def test_re_ingesting_an_unchanged_session_changes_nothing(conn, home, projects):
    source = write_transcript(projects / "repo", "sess-1", SAMPLE)
    first = ingest_file(source, conn=conn, root=home)
    before = paths.copy_path(home, "sess-1").read_bytes()
    turns_before = conn.execute("SELECT count(*) c FROM raw_turns").fetchone()["c"]

    second = ingest_file(source, conn=conn, root=home)

    assert second.status == "unchanged"
    assert second.lines_added == 0
    assert paths.copy_path(home, "sess-1").read_bytes() == before
    assert conn.execute("SELECT count(*) c FROM raw_turns").fetchone()["c"] == turns_before
    assert conn.execute("SELECT count(*) c FROM raw_sessions").fetchone()["c"] == 1
    assert first.lines_total == second.lines_total


def test_a_growing_session_appends_and_line_numbers_stay_stable(conn, home, projects):
    """The live-session case: ingest mid-flight, then again after more turns."""
    source = write_transcript(projects / "repo", "sess-1", SAMPLE)
    ingest_file(source, conn=conn, root=home)
    line4_before = resolve_pointer("sess-1", 4, root=home)[0]

    with open(source, "a", encoding="utf-8") as handle:
        handle.write(assistant_line("Done.") + "\n")
        handle.write(user_line("what does idempotent mean?") + "\n")

    result = ingest_file(source, conn=conn, root=home)

    assert result.status == "appended"
    assert result.lines_added == 2
    assert result.lines_total == 6
    # An existing pointer still resolves to the same thing it did before.
    assert resolve_pointer("sess-1", 4, root=home)[0] == line4_before
    assert paths.copy_path(home, "sess-1").read_bytes() == source.read_bytes()
    assert conn.execute("SELECT count(*) c FROM raw_sessions").fetchone()["c"] == 1


def test_a_rewritten_transcript_is_re_ingested_rather_than_stitched(conn, home, projects):
    """If the prefix hash stops matching, our line numbers are lies. Start over."""
    source = write_transcript(projects / "repo", "sess-1", SAMPLE)
    ingest_file(source, conn=conn, root=home)

    replacement = [user_line("entirely different session"), assistant_line("ok")]
    write_transcript(projects / "repo", "sess-1", replacement)
    result = ingest_file(source, conn=conn, root=home)

    assert result.status == "rewritten"
    assert result.lines_total == 2
    assert paths.copy_path(home, "sess-1").read_bytes() == source.read_bytes()
    row = conn.execute("SELECT rewrites FROM raw_sessions").fetchone()
    assert row["rewrites"] == 1
    # No stale rows left pointing at lines that no longer exist.
    assert conn.execute(
        "SELECT max(line_no) m FROM raw_turns WHERE session_id = 'sess-1'"
    ).fetchone()["m"] == 2


# --- done when: a partial write does not corrupt or crash ------------------


def test_a_half_written_final_line_is_left_for_next_time(conn, home, projects):
    """Claude Code is mid-write. Take whole lines only; the rest is not ours yet."""
    directory = projects / "repo"
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "sess-1.jsonl"
    source.write_text("\n".join(SAMPLE) + "\n" + '{"type": "user", "mess', encoding="utf-8")

    result = ingest_file(source, conn=conn, root=home)

    assert result.status == "new"
    assert result.lines_total == 4               # the torn line is not ingested
    assert result.malformed_lines == 0           # and is not counted as damage
    copy = paths.copy_path(home, "sess-1")
    assert copy.read_bytes() == "\n".join(SAMPLE).encode() + b"\n"

    # Claude Code finishes the line; the next run picks it up, once.
    with open(source, "a", encoding="utf-8") as handle:
        handle.write('age": {"content": "carry on"}}\n')
    second = ingest_file(source, conn=conn, root=home)
    assert second.status == "appended"
    assert second.lines_total == 5
    assert json.loads(resolve_pointer("sess-1", 5, root=home)[0])["type"] == "user"


def test_a_file_with_no_complete_line_at_all_is_simply_not_ingested(conn, home, projects):
    directory = projects / "repo"
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "sess-empty.jsonl"
    source.write_text('{"type": "user"', encoding="utf-8")

    result = ingest_file(source, conn=conn, root=home)

    assert result.status == "empty"
    assert conn.execute("SELECT count(*) c FROM raw_sessions").fetchone()["c"] == 0
    assert not paths.copy_path(home, "sess-empty").exists()


def test_malformed_and_unknown_lines_are_counted_not_fatal(conn, home, projects):
    source = write_transcript(
        projects / "repo",
        "sess-1",
        [
            user_line("hello"),
            "this is not json at all",
            json.dumps({"type": "a-type-from-the-future", "payload": 1}),
            json.dumps({"type": "user", "message": "not a dict"}),
            assistant_line("still here"),
        ],
    )
    result = ingest_file(source, conn=conn, root=home)

    assert result.lines_total == 5
    assert result.malformed_lines == 1
    row = conn.execute("SELECT * FROM raw_sessions").fetchone()
    assert json.loads(row["unknown_types"]) == {"a-type-from-the-future": 1}
    assert row["skipped_blocks"] == 1
    # The good lines on either side still made it in.
    roles = [r["role"] for r in conn.execute("SELECT role FROM raw_turns ORDER BY line_no")]
    assert roles == ["user", "assistant"]


# --- done when: invisible to the running Claude Code session ---------------


def test_ingestion_does_not_touch_the_source_tree(conn, home, projects):
    """Read-only, and checkable: same files, same bytes, same mtimes afterwards."""
    write_transcript(projects / "repo-a", "sess-1", SAMPLE)
    write_transcript(projects / "repo-b", "sess-2", SAMPLE)

    def snapshot():
        return {
            str(p.relative_to(projects)): (
                p.stat().st_mtime_ns,
                p.stat().st_size,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
            for p in sorted(projects.rglob("*"))
            if p.is_file()
        }

    before = snapshot()
    results = ingest_all(conn=conn, root=home, projects_dir=projects)
    after = snapshot()

    assert len(results) == 2
    assert all(r.status == "new" for r in results)
    assert before == after          # no writes, no touches, no new files


def test_an_unreadable_file_does_not_abort_the_sweep(conn, home, projects):
    write_transcript(projects / "repo-a", "sess-1", SAMPLE)
    bad = write_transcript(projects / "repo-b", "sess-2", SAMPLE)
    bad.chmod(0o000)
    try:
        results = ingest_all(conn=conn, root=home, projects_dir=projects)
    finally:
        bad.chmod(0o644)

    statuses = sorted(r.status for r in results)
    assert statuses[0] == "error: PermissionError"
    assert statuses[1] == "new"


# --- the distinction the product rests on ----------------------------------


def test_a_tool_result_is_not_a_human_turn(conn, home, projects):
    """The PR-12 reason for our own parser, as an assertion.

    Tool output arrives inside a `user`-typed line. Counting it as a human turn
    would make every tool call look like a person accepting something.
    """
    source = write_transcript(projects / "repo", "sess-1", SAMPLE)
    ingest_file(source, conn=conn, root=home)

    rows = human_turns(conn, "sess-1")
    assert [r["text"] for r in rows] == [
        "add retries to the webhook handler",
        "ok go ahead",
    ]
    assert conn.execute(
        "SELECT count(*) c FROM raw_turns WHERE role = 'tool_result'"
    ).fetchone()["c"] == 1


def test_injected_meta_text_is_marked_and_excluded(conn, home, projects):
    """System reminders and skill text arrive as `user`. No human typed them."""
    source = write_transcript(
        projects / "repo",
        "sess-1",
        [user_line("real question"), user_line("<system-reminder>...", isMeta=True)],
    )
    ingest_file(source, conn=conn, root=home)

    assert [r["text"] for r in human_turns(conn, "sess-1")] == ["real question"]
    assert conn.execute(
        "SELECT count(*) c FROM raw_turns WHERE is_meta = 1"
    ).fetchone()["c"] == 1


def test_sidechain_turns_are_marked_and_excluded(conn, home, projects):
    """Subagent traffic. The user never saw it, so they cannot have accepted it."""
    source = write_transcript(
        projects / "repo",
        "sess-1",
        [user_line("real question"), user_line("subagent prompt", isSidechain=True)],
    )
    ingest_file(source, conn=conn, root=home)

    assert [r["text"] for r in human_turns(conn, "sess-1")] == ["real question"]


def test_thinking_is_retained_but_kept_distinct_from_what_was_shown(conn, home, projects):
    source = write_transcript(projects / "repo", "sess-1", SAMPLE)
    ingest_file(source, conn=conn, root=home)

    thinking = conn.execute(
        "SELECT text FROM raw_turns WHERE role = 'thinking'"
    ).fetchone()
    assert thinking["text"] == "They have not asked what idempotent means."
    assistant = conn.execute(
        "SELECT text FROM raw_turns WHERE role = 'assistant'"
    ).fetchone()
    assert "idempotent" in assistant["text"]
    assert "have not asked" not in assistant["text"]


def test_a_line_mixing_human_text_and_tool_results_splits_correctly(conn, home, projects):
    """Both arrive in one `user` line. Only one of them is a person talking."""
    mixed = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu-9", "content": "output"},
                    {"type": "text", "text": "and here is what I think"},
                ]
            },
        }
    )
    source = write_transcript(projects / "repo", "sess-1", [mixed])
    ingest_file(source, conn=conn, root=home)

    rows = list(conn.execute("SELECT seq, role FROM raw_turns ORDER BY seq"))
    assert [(r["seq"], r["role"]) for r in rows] == [(0, "user"), (1, "tool_result")]
    assert [r["text"] for r in human_turns(conn, "sess-1")] == ["and here is what I think"]


def test_parse_line_never_raises_on_anything():
    """Whatever the next Claude Code release does, this must degrade, not crash."""
    for junk in (
        "",
        "   ",
        "not json",
        "[]",
        "null",
        "42",
        '{"type": "user"}',
        '{"type": "user", "message": {"content": [1, 2, 3]}}',
        '{"type": "user", "message": {"content": [{"type": "brand_new"}]}}',
        '{"type": "assistant", "message": {"content": "unexpectedly a string"}}',
    ):
        result = parse_line(junk, 1)
        assert isinstance(result.turns, list)


def test_reopening_the_store_sees_everything(home, projects):
    source = write_transcript(projects / "repo", "sess-1", SAMPLE)
    first = connect(home)
    ingest_file(source, conn=first, root=home)
    first.close()

    second = connect(home)
    try:
        assert second.execute("SELECT count(*) c FROM raw_sessions").fetchone()["c"] == 1
        assert len(human_turns(second, "sess-1")) == 2
    finally:
        second.close()
