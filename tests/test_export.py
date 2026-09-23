"""Tests for recording the models' decisions, and exporting them (PR-32).

Two claims. First, the log now keeps what each model said, and keeps it
through a regeneration: every detector run in full, how long each call took,
and each time the material writer refused. Second, the export flattens that
without leaking what it promises to withhold, and agrees with what the app
already reports.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from unrot import export
from unrot.analyse import analyse_session
from unrot.api.app import create_app
from unrot.capture import paths
from unrot.capture.ingest import SCHEMA_PATH as RAW_SCHEMA
from unrot.grader import submit
from unrot.material import WouldRecurse, record_refusal
from unrot.metrics import compute
from unrot.model import ModelConfig
from unrot.regen import run
from unrot.resolver import resolve_reference, strict
from unrot.spend import Meter, tap
from unrot.store import append, compile_state, fixtures
from unrot.store.__main__ import main as store_main
from unrot.store.__main__ import open_store

SECRET_ANSWER = "my-private-answer-ZQX"
SECRET_BODY = "material-body-ZQX"
SECRET_EXCERPT = "excerpt-from-my-code-ZQX"


@pytest.fixture
def home(tmp_path):
    paths.ensure_layout(tmp_path)
    return tmp_path


@pytest.fixture
def raw(home):
    """Two captured sessions, each an exchange a person replied to, run from inside `home`."""
    conn = sqlite3.connect(paths.raw_db_path(home))
    conn.row_factory = sqlite3.Row
    conn.executescript(RAW_SCHEMA.read_text(encoding="utf-8"))
    for session in ("s0", "s1"):
        conn.execute(
            "INSERT INTO raw_sessions (session_id, source_path, copy_path, cwd,"
            " prefix_sha256, first_ingested_at, last_ingested_at)"
            " VALUES (?, '/x', '/y', ?, '', '2026-09-01', '2026-09-01')",
            (session, str(home / "code" / "payments")),
        )
        rows = [
            (1, "assistant", "We will make the handler idempotent before retrying." * 4),
            (2, "user", "ok go ahead with that"),
            (3, "assistant", "And the outbox makes the write and the publish atomic." * 4),
            (4, "user", "sure"),
        ]
        for line_no, role, text in rows:
            conn.execute(
                "INSERT INTO raw_turns (session_id, line_no, seq, role, text,"
                " is_meta, is_sidechain, occurred_at)"
                " VALUES (?, ?, 0, ?, ?, 0, 0, '2026-09-01')",
                (session, line_no, role, text),
            )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def conn(home):
    connection = open_store(home)
    yield connection
    connection.close()


def candidate(term, line, signal="accepted", importance="central"):
    return {
        "term": term,
        "paraphrase": f"{term} carried the design and went unquestioned.",
        "assistant_line": line,
        "acceptance_line": line + 1,
        "signal": signal,
        "importance": importance,
    }


def proposer(*items):
    return lambda prompt_text: [dict(i) for i in items]


THREE = proposer(
    candidate("idempotency", 1),
    candidate("outbox", 3, importance="supporting"),
    candidate("atomic publish", 3, signal="unclear"),
)


def analyse(conn, raw, session="s0", propose=THREE, label="model-a"):
    return analyse_session(
        conn, raw, session, propose=propose, decide=strict,
        detector_label=label, resolver_label=label, max_candidates=1,
    )


def runs(conn):
    return [
        json.loads(r["payload"]) | {"provenance": json.loads(r["provenance"])}
        for r in conn.execute(
            "SELECT payload, provenance FROM events WHERE event_type = 'detector_ran' ORDER BY event_id"
        )
    ]


# ---------------------------------------------------------------------------
# What is now stored
# ---------------------------------------------------------------------------


def test_a_detector_run_is_recorded_in_full_not_just_what_was_emitted(conn, raw):
    """Emitted candidates are what the user sees. Precision and stability also
    need what the detector found and the budget suppressed."""
    analyse(conn, raw)

    [ran] = runs(conn)
    assert ran["session_id"] == "s0"
    assert ran["max_candidates"] == 1
    assert ran["windows_examined"] >= 1 and ran["calls_made"] >= 1
    assert "model-a" in ran["provenance"]["detector_version"]

    by_term = {c["term"]: c for c in ran["candidates"]}
    assert set(by_term) == {"idempotency", "outbox", "atomic publish"}
    assert by_term["idempotency"]["emitted"] is True
    assert by_term["outbox"]["emitted"] is False, "suppressed by the budget of one"
    assert by_term["atomic publish"]["signal"] == "unclear"
    assert [c["rank"] for c in ran["candidates"]] == [1, 2, 3]


def test_an_emitted_candidate_carries_the_encounter_it_was_filed_under(conn, raw):
    analyse(conn, raw)
    [ran] = runs(conn)
    [emitted] = [c for c in ran["candidates"] if c["emitted"]]
    encounter = conn.execute("SELECT * FROM compiled_encounters").fetchone()
    assert emitted["encounter_id"] == encounter["encounter_id"]
    assert all("encounter_id" not in c for c in ran["candidates"] if not c["emitted"])


def test_a_clean_run_is_recorded_too(conn, raw):
    analyse(conn, raw, propose=proposer())
    [ran] = runs(conn)
    assert ran["candidates"] == []


def test_regeneration_keeps_every_earlier_run(conn, raw):
    """The reason `detector_ran` exists: a re-run deletes the old analysis and
    the unjudged encounters, and the old model's answer must not go with them."""
    run(conn, raw, propose=THREE, decide=strict, model_label="model-a", sessions=["s0"])
    run(conn, raw, propose=proposer(), decide=strict, model_label="model-b", sessions=["s0"])

    models = [export.model_of(r["provenance"]["detector_version"]) for r in runs(conn)]
    assert models == ["model-a", "model-b"]
    # The old encounter is gone from compiled state; its run is still in the log.
    assert conn.execute("SELECT count(*) FROM compiled_encounters").fetchone()[0] == 0
    assert any(c["emitted"] for c in runs(conn)[0]["candidates"])


def test_a_model_call_records_how_long_it_took(conn):
    from langchain_core.outputs import LLMResult

    meter = Meter()
    [handler] = tap(meter, "detection", ModelConfig(model="m", base_url="http://localhost:1"))
    run_id = uuid.uuid4()
    handler.on_chat_model_start({}, [[]], run_id=run_id)
    handler.on_llm_end(LLMResult(generations=[[]], llm_output={"token_usage": {}}), run_id=run_id)

    [call] = meter.calls
    assert call.duration_ms is not None and call.duration_ms >= 0
    assert "duration_ms" in call.payload()


def test_an_untimed_call_says_unknown_rather_than_zero():
    from langchain_core.outputs import LLMResult

    meter = Meter()
    [handler] = tap(meter, "detection", ModelConfig(model="m", base_url="http://localhost:1"))
    handler.on_llm_end(LLMResult(generations=[[]], llm_output={}), run_id=uuid.uuid4())
    assert meter.calls[0].duration_ms is None
    assert "duration_ms" not in meter.calls[0].payload()


def test_a_material_refusal_is_recorded(conn):
    named = resolve_reference(conn, "quorum", decide=strict)
    record_refusal(conn, named.concept_id, "sources_only", WouldRecurse("never encountered"))
    row = conn.execute("SELECT * FROM events WHERE event_type = 'material_refused'").fetchone()
    payload = json.loads(row["payload"])
    assert payload["reason"] == "would_recurse"
    assert payload["concept_id"] == named.concept_id


def test_the_app_records_a_refusal_it_returns(home, monkeypatch):
    monkeypatch.setenv("UNROT_HOME", str(home))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with open_store(home) as setup:
        named = resolve_reference(setup, "quorum", decide=strict)
        setup.commit()

    response = TestClient(create_app()).post(
        f"/api/concepts/{named.concept_id}/material", params={"format": "sources_only"}
    )
    assert response.status_code == 409

    check = open_store(home)
    reasons = [
        json.loads(r["payload"])["reason"]
        for r in check.execute("SELECT payload FROM events WHERE event_type = 'material_refused'")
    ]
    assert reasons == ["would_recurse"]


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------


def test_flags_carry_the_verdict_and_what_the_detector_thought(conn, raw):
    analyse(conn, raw)
    encounter_id = conn.execute("SELECT encounter_id FROM compiled_encounters").fetchone()[0]
    append(conn, "encounter_confirmed", {"encounter_id": encounter_id})
    conn.commit()
    compile_state(conn)

    [flag] = export.build(conn, raw=raw)["flags"]
    assert flag["term"] == "idempotency"
    assert flag["verdict"] == "confirmed"
    assert flag["seconds_to_verdict"] is not None
    assert (flag["signal"], flag["importance"], flag["rank"]) == ("accepted", "central", 1)
    assert flag["model"] == "model-a"
    assert flag["fixture"] is False


def test_detections_hold_every_candidate_and_join_to_the_verdict(conn, raw):
    analyse(conn, raw)
    encounter_id = conn.execute("SELECT encounter_id FROM compiled_encounters").fetchone()[0]
    append(conn, "encounter_dismissed", {"encounter_id": encounter_id})
    conn.commit()
    compile_state(conn)

    rows = export.build(conn, raw=raw)["detections"]
    assert len(rows) == 3
    [emitted] = [r for r in rows if r["emitted"]]
    assert emitted["verdict"] == "dismissed"
    assert {r["run_id"] for r in rows} == {rows[0]["run_id"]}


def test_sessions_agree_with_the_metrics_report(conn, raw):
    analyse(conn, raw, "s0")
    analyse(conn, raw, "s1", propose=proposer())
    report = compute(conn, since=export_window())

    rows = export.build(conn, raw=raw)["sessions"]
    assert len(rows) == report.sessions_analysed == 2
    assert sum(r["clean"] for r in rows) == report.sessions_clean == 1
    s0 = next(r for r in rows if r["session_id"] == "s0")
    assert s0["repo"] == "payments"
    assert s0["human_turns"] == 2
    assert s0["runs"] == 1
    assert s0["latest_run_candidates_found"] == 3
    assert s0["latest_run_candidates_emitted"] == 1


def export_window():
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) - timedelta(days=1)


def test_resolutions_record_later_corrections(conn, raw):
    analyse(conn, raw)
    judgment = conn.execute(
        "SELECT event_id FROM events WHERE event_type = 'resolver_judgment'"
    ).fetchone()[0]
    append(
        conn, "resolver_judgment_corrected",
        {"target_event_id": judgment, "reasoning": "wrong concept"}, supersedes=judgment,
    )
    conn.commit()

    [row] = export.build(conn)["resolutions"]
    assert row["corrected"] is True
    assert row["input_text"] == "idempotency"


def test_calls_and_materials_come_through(conn):
    append(conn, "model_called", {"purpose": "detection", "model": "m", "ok": False,
                                  "finish_reason": "length", "duration_ms": 812.5})
    named = resolve_reference(conn, "quorum", decide=strict)
    record_refusal(conn, named.concept_id, "textual_with_sources", WouldRecurse("no"))

    rows = export.build(conn)
    [call] = rows["calls"]
    assert (call["ok"], call["finish_reason"], call["duration_ms"]) == (False, "length", 812.5)
    [material] = rows["materials"]
    assert (material["outcome"], material["reason"]) == ("refused", "would_recurse")


def _private_store(conn, raw, home):
    """A store holding everything the export promises not to leak by default."""
    analyse(conn, raw)
    concept_id = conn.execute("SELECT concept_id FROM compiled_concepts").fetchone()[0]
    submit(conn, concept_id, SECRET_ANSWER)
    append(
        conn,
        "material_generated",
        {
            "material_id": "m-1",
            "format": "textual_with_sources",
            "covers_concept_ids": [concept_id],
            "sources": [
                {"kind": "code", "ref": str(home / "code" / "payments" / "handler.py"),
                 "title": "Your own code: handler.py", "excerpt": SECRET_EXCERPT, "verified": True}
            ],
            "body": SECRET_BODY,
        },
        provenance={"material_version": "material/0.1.0+model-a+p"},
    )
    conn.commit()
    compile_state(conn)


def _everything(folder) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(folder.iterdir()))


def test_by_default_no_text_no_key_and_no_absolute_path(conn, raw, home, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-SECRET-KEY-ZQX")
    _private_store(conn, raw, home)
    out = tmp_path / "bundle"
    export.write(conn, out, raw=raw, config=ModelConfig.from_env())

    written = _everything(out)
    for secret in (SECRET_ANSWER, SECRET_BODY, SECRET_EXCERPT, "sk-or-SECRET-KEY-ZQX", str(home)):
        assert secret not in written, secret
    # Withheld, and saying so, rather than silently missing.
    withheld = [json.loads(l) for l in (out / "events.jsonl").read_text().splitlines()]
    assert any("raw_text" in e.get("withheld", []) for e in withheld)
    # What is needed to judge a flag is still there.
    assert "idempotency" in written and "carried the design" in written


def test_text_is_included_only_when_asked(conn, raw, home, tmp_path):
    _private_store(conn, raw, home)
    out = tmp_path / "bundle"
    meta = export.write(conn, out, raw=raw, include_text=True)
    written = _everything(out)
    assert SECRET_ANSWER in written and SECRET_BODY in written and SECRET_EXCERPT in written
    assert meta["include_text"] is True


def test_fixtures_are_marked_not_mixed_in(conn, tmp_path):
    fixtures.seed(conn)
    compile_state(conn)
    meta = export.write(conn, tmp_path / "bundle")
    assert meta["fixture_events"] > 0
    flags = [json.loads(l) for l in (tmp_path / "bundle" / "flags.jsonl").read_text().splitlines()]
    assert flags and all(f["fixture"] for f in flags)


def test_the_bundle_describes_itself(conn, tmp_path):
    meta = export.write(conn, tmp_path / "bundle", config=ModelConfig(model="x/y", api_key="k"))
    folder = tmp_path / "bundle"
    assert {p.name for p in folder.iterdir()} == {"README.md", "manifest.json"} | {
        f"{name}.jsonl" for name in export.FILES
    }
    assert meta["format"] == export.FORMAT
    assert meta["config"] == {"model": "x/y", "endpoint_host": "openrouter.ai", "local": False,
                              "temperature": 0.0}
    readme = (folder / "README.md").read_text()
    for name in export.FILES:
        assert f"{name}.jsonl" in readme, f"{name}.jsonl is not in the data dictionary"


def test_the_cli_writes_once_and_refuses_to_overwrite(home, tmp_path, capsys):
    out = tmp_path / "bundle"
    assert store_main(["--home", str(home), "export", str(out)]) == 0
    assert "nothing was uploaded" in capsys.readouterr().out
    assert store_main(["--home", str(home), "export", str(out)]) == 1


def test_since_leaves_out_what_came_before(conn, raw):
    analyse(conn, raw)
    rows = export.build(conn, raw=raw, since="2999-01-01")
    assert all(not rows[name] for name in export.FILES)


# ---------------------------------------------------------------------------
# From the app
# ---------------------------------------------------------------------------


@pytest.fixture
def app_home(conn, raw, home, monkeypatch):
    monkeypatch.setenv("UNROT_HOME", str(home))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-SECRET-KEY-ZQX")
    _private_store(conn, raw, home)
    return home


def test_the_preview_says_what_is_in_and_out_before_anything_is_written(app_home):
    body = TestClient(create_app()).get("/api/export/preview").json()
    assert body["filename"].startswith("unrot-export-") and body["filename"].endswith(".zip")
    rows = {f["name"]: f["rows"] for f in body["files"]}
    assert rows["flags.jsonl"] == 1 and rows["detections.jsonl"] == 3
    assert body["withheld"] and body["never"] and body["included"]
    assert not [p for p in app_home.rglob("*") if p.name.startswith("unrot-export")]

    with_text = TestClient(create_app()).get("/api/export/preview", params={"include_text": True}).json()
    assert with_text["withheld"] == []


def test_the_app_gets_a_zip_it_can_save(app_home):
    import io
    import zipfile

    response = TestClient(create_app()).post("/api/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "unrot-export-" in response.headers["content-disposition"]

    bundle = zipfile.ZipFile(io.BytesIO(response.content))
    names = bundle.namelist()
    [root] = {n.split("/", 1)[0] for n in names}
    assert root.startswith("unrot-export-")
    assert {n.split("/", 1)[1] for n in names} == {"README.md", "manifest.json"} | {
        f"{name}.jsonl" for name in export.FILES
    }
    everything = "\n".join(bundle.read(n).decode("utf-8") for n in names)
    for secret in (SECRET_ANSWER, SECRET_BODY, SECRET_EXCERPT, "sk-or-SECRET-KEY-ZQX", str(app_home)):
        assert secret not in everything, secret


def test_the_zip_includes_text_only_when_asked(app_home):
    import io
    import zipfile

    response = TestClient(create_app()).post("/api/export", params={"include_text": True})
    bundle = zipfile.ZipFile(io.BytesIO(response.content))
    everything = "\n".join(bundle.read(n).decode("utf-8") for n in bundle.namelist())
    assert SECRET_ANSWER in everything
    assert "sk-or-SECRET-KEY-ZQX" not in everything
