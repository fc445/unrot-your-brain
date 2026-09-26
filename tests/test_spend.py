"""What analysis costs, recorded per call and read back off the log (PR-31).

The acceptance tests here run the real model clients -- langchain, the openai
SDK, the structured-output parsing -- against a local server that answers the
way OpenRouter does, `usage.cost` and all. Faking the callables instead would
test the arithmetic and miss the one thing that can actually go wrong: the cost
arriving in a place the meter is not listening, which is exactly what happens
on the call that hits the length limit.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from unrot import spend
from unrot.api.app import create_app
from unrot.capture import paths
from unrot.metrics import compute
from unrot.model import ModelConfig
from unrot.store import append, connect
from unrot.store.__main__ import open_store

from test_watch_api import a_session

app_module = sys.modules["unrot.api.app"]


# --- a server that answers like OpenRouter ------------------------------------


def _usage(cost: float, *, prompt=1200, completion=180, reasoning=0) -> dict:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost": cost,
        "is_byok": False,
        "cost_details": {
            "upstream_inference_cost": None,
            "upstream_inference_prompt_cost": round(cost * 0.25, 12),
            "upstream_inference_completions_cost": round(cost * 0.75, 12),
        },
        "completion_tokens_details": {"reasoning_tokens": reasoning},
    }


class FakeRouter:
    """Scripted answers per schema name, and a record of every cost it billed."""

    def __init__(self):
        self.script: dict[str, list[tuple[str, str, float]]] = {}
        self.billed: list[float] = []
        router = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                schema = request["response_format"]["json_schema"]["name"]
                content, finish, cost = router.script[schema].pop(0)
                router.billed.append(cost)
                body = json.dumps(
                    {
                        "id": "gen-test",
                        "object": "chat.completion",
                        "created": 1,
                        "model": request["model"],
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": finish,
                                "message": {"role": "assistant", "content": content},
                            }
                        ],
                        "usage": _usage(
                            cost,
                            completion=32768 if finish == "length" else 180,
                            reasoning=30000 if finish == "length" else 0,
                        ),
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def answer(self, schema: str, payload: dict, cost: float) -> None:
        self.script.setdefault(schema, []).append((json.dumps(payload), "stop", cost))

    def run_out_of_room(self, schema: str, cost: float) -> None:
        """The failed first run: a reasoning model spends the whole budget and stops mid-JSON."""
        self.script.setdefault(schema, []).append(('{"candidates": [{"term": "back', "length", cost))


@pytest.fixture
def router(monkeypatch):
    fake = FakeRouter()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("UNROT_BASE_URL", fake.url)
    monkeypatch.setenv("UNROT_MODEL", "test/model")
    # The server is on loopback, which would read as a local model. It stands
    # in for a hosted one, so it is priced like one.
    monkeypatch.setattr("unrot.model.is_local", lambda _url: False)
    yield fake
    fake.server.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("UNROT_HOME", str(tmp_path / "home"))
    paths.ensure_layout(tmp_path / "home")
    return tmp_path / "home"


@pytest.fixture
def projects(tmp_path, monkeypatch):
    projects = tmp_path / "claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(paths, "CLAUDE_PROJECTS", projects)
    return projects


@pytest.fixture
def client(home):
    return TestClient(create_app())


GAP = {
    "candidates": [
        {
            "term": "backpressure",
            "paraphrase": "Slowing producers so a queue cannot grow without bound.",
            "assistant_line": 2,
            "acceptance_line": 3,
            "signal": "accepted",
            "importance": "central",
        }
    ]
}
NEW = {
    "decision": "new",
    "canonical_name": "backpressure",
    "paraphrase": "Slowing producers so a queue cannot grow without bound.",
    "reasoning": "Nothing like it yet.",
}


# --- acceptance ---------------------------------------------------------------


def test_the_total_matches_every_billed_call_including_the_one_that_failed(
    client, projects, router
):
    """PR-31's first acceptance line, through the real clients.

    One session hits the length limit -- billed, produces nothing, and leaves
    the session waiting -- and one succeeds with a gap to file. The app's total
    is the sum of every `usage.cost` the endpoint returned.
    """
    for session in ("s-1", "s-2"):
        client.post("/api/capture", json={"paths": [str(a_session(projects, session))]})

    router.run_out_of_room("Proposal", 0.006585924)
    failed = client.post("/api/analyse", json={"session_id": "s-1"})
    assert failed.status_code == 502

    router.answer("Proposal", GAP, 0.0021)
    router.answer("Decision", NEW, 0.0004)
    ok = client.post("/api/analyse", json={"session_id": "s-2"})
    assert ok.status_code == 200, ok.text

    spent = client.get("/api/spend").json()
    assert spent["week"]["cost"] == pytest.approx(sum(router.billed))
    assert spent["week"]["calls"] == len(router.billed) == 3
    assert spent["week"]["failed"] == 1
    assert spent["week"]["text"] == spend.money(sum(router.billed))
    by_purpose = {p["purpose"]: p["total"]["cost"] for p in spent["week_by_purpose"]}
    assert by_purpose == pytest.approx({"detection": 0.008685924, "resolution": 0.0004})
    # The queue bar and the tray read the same total.
    assert client.get("/api/queue").json()["spent_this_week"]["cost"] == pytest.approx(
        sum(router.billed)
    )


def test_the_failed_call_keeps_its_tokens_and_why_it_failed(client, home, projects, router):
    client.post("/api/capture", json={"paths": [str(a_session(projects))]})
    router.run_out_of_room("Proposal", 0.006585924)

    client.post("/api/analyse", json={"session_id": "s-1"})

    conn = open_store(home)
    [call] = spend.calls(conn)
    assert call["ok"] is False
    assert call["error"] == "LengthFinishReasonError"
    assert call["finish_reason"] == "length"
    assert call["completion_tokens"] == 32768
    assert call["reasoning_tokens"] == 30000
    assert call["session_id"] == "s-1"
    assert call["purpose"] == "detection"
    assert call["model"] == "test/model"


def test_the_cli_reports_the_same_numbers_as_the_app(client, home, projects, router):
    """`store metrics` and /api/spend read one function over one log."""
    client.post("/api/capture", json={"paths": [str(a_session(projects))]})
    router.answer("Proposal", GAP, 0.0021)
    router.answer("Decision", NEW, 0.0004)
    client.post("/api/analyse", json={"session_id": "s-1"})

    app_week = client.get("/api/spend").json()["week"]
    conn = open_store(home)
    report = compute(conn, since=spend.week_ago())
    cli = json.loads(report.as_json())["spend"]["total"]

    assert cli["cost"] == pytest.approx(app_week["cost"])
    assert cli["calls"] == app_week["calls"]
    assert cli["text"] == app_week["text"]


def test_the_batch_total_is_spend_since_the_batch_began(client, home, projects, router):
    """What the watcher shows while it runs: `?since=` the moment it started."""
    conn = open_store(home)
    append(
        conn, "model_called",
        {"purpose": "detection", "model": "test/model", "ok": True, "cost": 1.0},
        occurred_at="2026-01-01T00:00:00.000+00:00",
    )
    conn.commit()
    started = datetime.now(timezone.utc).isoformat()
    client.post("/api/capture", json={"paths": [str(a_session(projects))]})
    router.answer("Proposal", {"candidates": []}, 0.003)
    client.post("/api/analyse", json={"session_id": "s-1"})

    window = client.get("/api/spend", params={"since": started}).json()["window"]
    assert window["cost"] == pytest.approx(0.003)
    assert client.get("/api/spend", params={"since": "yesterday"}).status_code == 400


# --- local and unpriced -----------------------------------------------------------


def test_a_local_model_is_no_cost_not_zero_dollars(client, home, monkeypatch):
    monkeypatch.setenv("UNROT_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder")
    # Nothing spent yet, on a local endpoint.
    assert client.get("/api/spend").json()["week"]["text"] == "local, no cost"

    conn = open_store(home)
    meter = spend.Meter()
    meter.record(spend.from_usage("detection", "qwen", _usage(0.5), local=True))
    meter.flush(conn)

    week = client.get("/api/spend").json()["week"]
    assert week["text"] == "local, no cost"
    assert week["local_only"] is True
    assert week["cost"] == 0
    assert "$" not in week["text"]


def test_a_call_with_no_price_is_unknown_not_free():
    total = spend.Total.of(
        [
            spend.from_usage("detection", "m", _usage(0.02), local=False),
            spend.from_usage("detection", "m", {"prompt_tokens": 10}, local=False),
            spend.from_usage("detection", "m", None, local=False, ok=False, error="Timeout"),
        ]
    )
    assert total.cost == pytest.approx(0.02)
    assert total.unpriced == 2
    assert total.failed == 1
    assert total.text == "$0.02 + 2 unpriced"
    assert spend.Total.of([spend.from_usage("grading", "m", None, local=False)]).text == (
        "not reported"
    )


# --- parsing usage ----------------------------------------------------------------


def test_openrouter_usage_is_read_in_full():
    call = spend.from_usage("detection", "m", _usage(0.006585924, reasoning=12), local=False)
    assert call.cost == pytest.approx(0.006585924)
    assert call.prompt_cost == pytest.approx(0.006585924 * 0.25)
    assert call.completion_cost == pytest.approx(0.006585924 * 0.75)
    assert (call.prompt_tokens, call.completion_tokens, call.reasoning_tokens) == (1200, 180, 12)


def test_bring_your_own_key_counts_what_the_provider_charged():
    usage = _usage(0.0001) | {
        "is_byok": True,
        "cost_details": {"upstream_inference_cost": 0.01},
    }
    assert spend.from_usage("detection", "m", usage, local=False).cost == pytest.approx(0.0101)


def test_money_keeps_the_fractions_a_session_costs():
    assert spend.money(0.14) == "$0.14"
    assert spend.money(0.0066) == "$0.0066"
    assert spend.money(0) == "$0.00"
    assert spend.money(0.00002) == "<$0.0001"
    assert spend.money(None) == "not reported"


# --- the log ----------------------------------------------------------------------


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def _spent(conn, session, cost, *, model="m", purpose="detection", at=None):
    append(
        conn, "model_called",
        {"purpose": purpose, "model": model, "ok": True, "cost": cost, "session_id": session},
        occurred_at=at,
    )


def test_cost_per_examined_session_is_per_model(conn):
    _spent(conn, "a", 0.01)
    _spent(conn, "a", 0.002, purpose="resolution")
    _spent(conn, "b", 0.004)
    _spent(conn, "c", 0.1, model="dear")
    # Grading is not part of examining a session.
    _spent(conn, "a", 5.0, purpose="grading")

    rows = {row.model: row for row in spend.summarise(conn).per_session}
    assert rows["m"].sessions == 2
    assert rows["m"].average == pytest.approx(0.008)
    assert rows["dear"].average == pytest.approx(0.1)


def test_an_estimate_is_recent_cost_per_session_with_the_same_model(conn):
    for index in range(30):
        _spent(conn, f"old-{index}", 1.0)
    for index in range(20):
        _spent(conn, f"new-{index}", 0.01)
    _spent(conn, "other", 9.0, model="other")

    guess = spend.estimate(conn, model="m", sessions=19, local=False)
    assert guess.based_on == 20
    assert guess.per_session == pytest.approx(0.01)
    assert guess.cost == pytest.approx(0.19)
    assert guess.text == "about $0.19"

    assert spend.estimate(conn, model="unseen", sessions=19, local=False).text is None
    assert spend.estimate(conn, model="m", sessions=19, local=True).text == "local, no cost"


def test_the_queue_carries_an_estimate_for_what_is_waiting(client, home, projects, router):
    conn = open_store(home)
    _spent(conn, "earlier", 0.01, model="test/model")
    conn.commit()
    for session in ("s-1", "s-2"):
        client.post("/api/capture", json={"paths": [str(a_session(projects, session))]})

    guess = client.get("/api/queue").json()["estimate"]
    assert guess["sessions"] == 2
    assert guess["cost"] == pytest.approx(0.02)
    assert guess["text"] == "about $0.02"


def test_spend_is_split_by_day_and_window(conn):
    now = datetime.now(timezone.utc)
    _spent(conn, "a", 0.01, at=(now - timedelta(days=10)).isoformat())
    _spent(conn, "b", 0.02, at=now.isoformat())

    week = spend.summarise(conn, since=spend.week_ago())
    ever = spend.summarise(conn)
    assert week.total.cost == pytest.approx(0.02)
    assert ever.total.cost == pytest.approx(0.03)
    assert len(ever.by_day) == 2


def test_fixtures_are_not_spend(conn):
    append(
        conn, "model_called",
        {"purpose": "detection", "model": "m", "ok": True, "cost": 1.0},
        origin="fixture",
    )
    assert spend.summarise(conn).total.calls == 0


def test_regeneration_never_deletes_what_was_spent(tmp_path, monkeypatch, projects):
    """A re-run makes new calls. The old ones were still paid for."""
    from unrot.capture import connect as connect_raw
    from unrot.capture.ingest import ingest_file
    from unrot.regen import regenerate
    from unrot.resolver import strict

    home = tmp_path / "regen-home"
    paths.ensure_layout(home)
    raw = connect_raw(home)
    ingest_file(a_session(projects), conn=raw, root=home)
    conn = open_store(home)
    _spent(conn, "s-1", 0.05)
    conn.commit()

    meter = spend.Meter()

    def propose(_prompt):
        meter.record(spend.from_usage("detection", "m", _usage(0.01), local=False))
        return []

    list(regenerate(conn, raw, propose=propose, decide=strict, model_label="m",
                    force=True, meter=meter))

    found = spend.calls(conn)
    assert [c["cost"] for c in found] == [0.05, 0.01]
    assert found[1]["session_id"] == "s-1"


def test_the_meter_attributes_calls_to_what_they_were_about():
    meter = spend.Meter()
    with meter.about(session_id="s-1"):
        meter.record(spend.Call("detection", "m"))
    meter.record(spend.Call("grading", "m"))
    assert meter.calls[0].payload()["session_id"] == "s-1"
    assert "session_id" not in meter.calls[1].payload()


def test_local_is_decided_by_the_endpoint():
    assert ModelConfig(base_url="http://localhost:11434/v1").local
    assert ModelConfig(base_url="http://studio.local:1234/v1").local
    assert not ModelConfig(base_url="https://openrouter.ai/api/v1").local


def test_the_grader_and_search_calls_are_metered_too(monkeypatch):
    """The calls that do not go through the chat client."""
    import httpx2

    from unrot.grader import jev

    def answer(request):
        del request
        return httpx2.Response(200, json={
            "model": "typesafe/jev-1.13-x",
            "answers": {"solo": {"type": "choice", "choice": "causal",
                                 "probabilities": {"causal": 0.9}, "confidence": 0.9}},
            "usage": {"prompt_tokens": 40, "completion_tokens": 1, "cost": 0.00002},
        })

    monkeypatch.setattr("unrot.model.is_local", lambda _url: False)
    meter = spend.Meter()
    jev.build_jev_grader(
        ModelConfig(api_key="k"), meter=meter, transport=httpx2.MockTransport(answer)
    )("Why?", "Because.")

    [call] = meter.calls
    assert (call.purpose, call.model, call.cost) == ("grading", jev.DEFAULT_JEV_MODEL, 0.00002)

    def broken(request):
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(Exception):
        jev.build_jev_grader(
            ModelConfig(api_key="k"), meter=meter, transport=httpx2.MockTransport(broken)
        )("Why?", "Because.")
    assert meter.calls[-1].ok is False and meter.calls[-1].cost is None
