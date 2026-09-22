"""The core half of the watcher: capture, the queue, and analysis (PR-29 phase 3).

The split these endpoints encode is the spend decision. Capture copies a
transcript Claude Code already wrote -- no model, no cost -- and the watcher may
do it whenever a session goes quiet. Analysis is the call that costs, and runs
only when asked. The queue is the difference between the two, derived from the
stores rather than kept anywhere, so it survives a relaunch with nothing saved.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from unrot.api.app import create_app
from unrot.capture import paths
from unrot.resolver import record_analysis, strict
from unrot.store.__main__ import open_store

from test_capture import assistant_line, user_line, write_transcript

app_module = sys.modules["unrot.api.app"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("UNROT_HOME", str(tmp_path / "home"))
    paths.ensure_layout(tmp_path / "home")
    return tmp_path / "home"


@pytest.fixture
def projects(tmp_path, monkeypatch):
    """Claude Code's projects directory, somewhere harmless."""
    projects = tmp_path / "claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(paths, "CLAUDE_PROJECTS", projects)
    return projects


@pytest.fixture
def client(home):
    return TestClient(create_app())


def a_session(projects, session_id="s-1", *, turns=2):
    lines = []
    for index in range(turns):
        lines.append(user_line(f"question {index}", timestamp=f"2026-09-20T10:0{index}:00.000Z"))
        # Long enough to be a window the detector will examine; short assistant
        # turns are skipped, and a session of them reads as clean.
        lines.append(assistant_line(f"answer {index}: " + "use backpressure on the queue so producers slow down. " * 4))
    return write_transcript(projects / "repo", session_id, lines)


def with_analyser(monkeypatch, propose):
    monkeypatch.setattr(
        app_module, "_build_analyser", lambda: (propose, strict, "test-model", "none")
    )


# --- capture ----------------------------------------------------------------


def test_capturing_a_quiet_session_puts_it_in_the_queue(client, projects):
    source = a_session(projects)

    body = client.post("/api/capture", json={"paths": [str(source)]}).json()

    assert body["captured"][0]["status"] == "new"
    assert body["pending"] == 1
    queue = client.get("/api/queue").json()
    assert [p["session_id"] for p in queue["pending"]] == ["s-1"]
    assert queue["pending"][0]["reason"] == "never"
    assert queue["pending"][0]["human_turns"] == 2


def test_capture_reads_nothing_outside_claude_codes_projects(client, projects, tmp_path):
    """A socket that could be pointed at any file on the machine would be a way to read it."""
    outside = tmp_path / "elsewhere" / "secrets.jsonl"
    outside.parent.mkdir()
    outside.write_text(user_line("nope") + "\n", encoding="utf-8")

    assert client.post("/api/capture", json={"paths": [str(outside)]}).status_code == 400
    # ...including through a symlink that lives inside but points out.
    (projects / "repo").mkdir(parents=True, exist_ok=True)
    link = projects / "repo" / "sneaky.jsonl"
    link.symlink_to(outside)
    assert client.post("/api/capture", json={"paths": [str(link)]}).status_code == 400
    # And only transcripts.
    other = projects / "repo" / "notes.txt"
    other.write_text("x", encoding="utf-8")
    assert client.post("/api/capture", json={"paths": [str(other)]}).status_code == 400


def test_capturing_twice_is_harmless(client, projects):
    source = a_session(projects)
    client.post("/api/capture", json={"paths": [str(source)]})

    again = client.post("/api/capture", json={"paths": [str(source)]}).json()

    assert again["captured"][0]["status"] == "unchanged"
    assert again["pending"] == 1


# --- the queue ----------------------------------------------------------------


def test_an_analysed_session_leaves_the_queue_and_a_grown_one_returns(client, home, projects):
    source = a_session(projects, turns=1)
    client.post("/api/capture", json={"paths": [str(source)]})
    conn = open_store(home)
    record_analysis(conn, "s-1", candidates_found=0, detector_version="detector/test")
    conn.close()
    assert client.get("/api/queue").json()["pending"] == []

    # The session carried on after it was analysed.
    a_session(projects, turns=3)
    client.post("/api/capture", json={"paths": [str(source)]})

    [waiting] = client.get("/api/queue").json()["pending"]
    assert waiting["reason"] == "grown"


def test_a_session_nobody_typed_in_is_never_queued(client, projects):
    """All tool traffic: there is no human turn to judge, so nothing to pay for."""
    source = write_transcript(projects / "repo", "s-robot", [assistant_line("working...")])
    client.post("/api/capture", json={"paths": [str(source)]})

    assert client.get("/api/queue").json()["pending"] == []


def test_the_queue_says_whether_analysis_is_possible(client, monkeypatch, projects):
    monkeypatch.setattr(app_module, "_can_analyse", lambda: False)
    assert client.get("/api/queue").json()["can_analyse"] is False


# --- analysis ----------------------------------------------------------------


def test_analysis_without_a_model_is_refused_not_faked(client, projects, monkeypatch):
    """Detection has no string-only fallback, so there is nothing honest to run."""
    source = a_session(projects)
    client.post("/api/capture", json={"paths": [str(source)]})

    def no_model():
        raise app_module._NoModel("no model is configured")

    monkeypatch.setattr(app_module, "_build_analyser", no_model)

    response = client.post("/api/analyse", json={"session_id": "s-1"})
    assert response.status_code == 409
    assert client.get("/api/queue").json()["pending"][0]["session_id"] == "s-1"


def test_a_clean_analysis_is_recorded_so_silence_means_we_looked(client, projects, monkeypatch):
    source = a_session(projects)
    client.post("/api/capture", json={"paths": [str(source)]})
    with_analyser(monkeypatch, lambda _prompt: [])

    body = client.post("/api/analyse", json={"session_id": "s-1"}).json()

    assert body["clean"] is True
    assert client.get("/api/queue").json()["pending"] == []
    assert client.get("/api/surface").json()["capture"]["sessions_analysed"] == 1


def test_a_failed_model_call_leaves_the_session_waiting(client, projects, monkeypatch):
    """Recorded last, so a failure partway costs nothing but the attempt."""
    source = a_session(projects)
    client.post("/api/capture", json={"paths": [str(source)]})

    def down(_prompt):
        raise ConnectionError("no route to host")

    with_analyser(monkeypatch, down)

    response = client.post("/api/analyse", json={"session_id": "s-1"})

    assert response.status_code == 502
    assert "still waiting" in response.json()["detail"]
    assert [p["session_id"] for p in client.get("/api/queue").json()["pending"]] == ["s-1"]


def test_an_uncaptured_session_cannot_be_analysed(client, projects, monkeypatch):
    with_analyser(monkeypatch, lambda _prompt: [])
    assert client.post("/api/analyse", json={"session_id": "nope"}).status_code == 404


def test_the_same_session_is_never_analysed_twice_at_once(client, projects, monkeypatch):
    """The watcher and a click on "Analyse now" can both ask."""
    source = a_session(projects)
    client.post("/api/capture", json={"paths": [str(source)]})
    with_analyser(monkeypatch, lambda _prompt: [])
    app_module._ANALYSING.add("s-1")
    try:
        response = client.post("/api/analyse", json={"session_id": "s-1"})
    finally:
        app_module._ANALYSING.discard("s-1")

    assert response.status_code == 409
