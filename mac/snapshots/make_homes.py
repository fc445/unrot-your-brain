"""Build one throwaway $UNROT_HOME per surface state, for the snapshot run.

    uv run python mac/snapshots/make_homes.py <dir>

Everything goes through the core's own write paths -- capture's ingest, the
fixture seeder, the grader -- over synthetic transcripts, so a snapshot shows
what the real code produces from plausible data and never touches a real store.

Homes:
  empty         nothing captured                         -> not_captured
  unanalysed    sessions captured, none examined         -> not_analysed
  coldstart     one session examined, nothing found      -> cold_start
  clean         several examined, all dealt with         -> clean
  full          every bucket populated, a graded answer,
                both material formats, replayable moments -> gaps
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from unrot.capture import connect as connect_raw, ingest_file, paths  # noqa: E402
from unrot.grader import grade, submit  # noqa: E402
from unrot.material.sources import Source  # noqa: E402
from unrot.resolver import record_analysis  # noqa: E402
from unrot.store import append, compile_state, fixtures  # noqa: E402
from unrot.store.__main__ import open_store  # noqa: E402

ASSISTANT = (
    "I'll make the write handler retry-safe: it takes an idempotency key, so a replay"
    " with the same key returns the stored response instead of charging twice. I've"
    " also switched the balance update to optimistic locking with a version column,"
    " so two concurrent writers can't silently overwrite each other -- the second one"
    " gets a conflict and retries. If the consumer falls behind we'll apply"
    " backpressure rather than buffering without bound. Shall I go ahead?"
)


def transcript(directory: Path, session_id: str, replies: list[str], *, repo: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, reply in enumerate(replies):
        stamp = f"2026-09-{18 + index % 3:02d}T1{index % 9}:04:00.000Z"
        lines.append(json.dumps({
            "type": "user", "sessionId": session_id, "timestamp": stamp,
            "cwd": f"/Users/dev/code/{repo}", "gitBranch": "main", "entrypoint": "cli",
            "message": {"role": "user", "content": "Make the payments write path safe to retry." if index == 0 else reply},
        }))
        lines.append(json.dumps({
            "type": "assistant", "sessionId": session_id, "timestamp": stamp,
            "message": {"content": [{"type": "text", "text": ASSISTANT}]},
        }))
    lines.append(json.dumps({
        "type": "user", "sessionId": session_id, "timestamp": "2026-09-20T19:42:00.000Z",
        "cwd": f"/Users/dev/code/{repo}", "message": {"role": "user", "content": "ok go ahead"},
    }))
    path = directory / f"{session_id}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def capture(home: Path, count: int, *, repo: str = "payments-api") -> list[str]:
    projects = home.parent / f"{home.name}-projects" / repo
    raw = connect_raw(home)
    ids = []
    for index in range(count):
        path = transcript(projects, f"sess-{home.name}-{index}", ["ok", "sure", "sounds right"], repo=repo)
        ids.append(ingest_file(path, conn=raw, root=home).session_id)
    raw.close()
    return ids


def analysed(conn, sessions: list[str], found: int = 0) -> None:
    for session in sessions:
        record_analysis(conn, session, candidates_found=found,
                        detector_version="detector/0.1.0+snapshot", recompile=False)


def build(base: Path) -> None:
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)

    # empty -- nothing at all.
    paths.ensure_layout(base / "empty")
    open_store(base / "empty").close()

    # unanalysed -- captured, never examined.
    home = base / "unanalysed"
    capture(home, 4)
    open_store(home).close()

    # coldstart -- one session looked at, nothing in it.
    home = base / "coldstart"
    sessions = capture(home, 2)
    conn = open_store(home)
    analysed(conn, sessions[:1])
    compile_state(conn)
    conn.close()

    # clean -- plenty examined, everything found already dealt with.
    home = base / "clean"
    sessions = capture(home, 6)
    conn = open_store(home)
    analysed(conn, sessions[:4])
    analysed(conn, sessions[4:], found=1)
    append(conn, "concept_created", {"concept_id": "c-wal", "canonical_name": "WAL mode"})
    append(conn, "encounter_recorded",
           {"encounter_id": "e-wal", "concept_id": "c-wal", "source": "transcript",
            "paraphrase": "Write-ahead logging was switched on for concurrent readers."},
           provenance={"detector_version": "detector/0.1.0+snapshot"})
    append(conn, "encounter_dismissed", {"encounter_id": "e-wal"})
    compile_state(conn)
    conn.close()

    # full -- every bucket, a graded answer, both material formats.
    home = base / "full"
    sessions = capture(home, 5)
    conn = open_store(home)
    raw = connect_raw(home)
    fixtures.seed(conn, raw_conn=raw)
    raw.close()
    # Fixtures are stamped so the surface can admit to them. Here they stand in
    # for real findings, so the stamp comes off -- this is a throwaway store.
    conn.execute("UPDATE events SET origin = 'local' WHERE origin = ?", (fixtures.FIXTURE_ORIGIN,))
    conn.commit()
    analysed(conn, sessions, found=1)
    compile_state(conn)

    # The bucket is decided in api/read.py, not stored; a confirmed encounter is
    # what puts a concept in "to learn".
    learning = conn.execute(
        "SELECT concept_id FROM compiled_encounters WHERE judgment = 'confirmed' LIMIT 1"
    ).fetchone()
    if learning:
        explanation = submit(conn, learning["concept_id"],
                             "It's where you slow the producer down when the consumer can't keep up,"
                             " instead of letting a queue grow until something falls over.")
        grade(conn, explanation, model_label="snapshot",
              grade_fn=lambda _q, _a: {
                  "level": "listed",
                  "reasoning": "Names what backpressure does, but not why slowing the producer"
                               " is better than buffering -- the causal step is missing.",
                  "probabilities": {"isolated": 0.12, "listed": 0.63, "causal": 0.25},
                  "confidence": 0.63,
              })
        append(conn, "material_generated", {
            "material_id": "m-snapshot-textual",
            "format": "textual_with_sources",
            "covers_concept_ids": [learning["concept_id"]],
            "body": "Backpressure is a consumer telling its producer to slow down [S1]. In your"
                    " session the queue between the webhook handler and the ledger writer had no"
                    " bound, so a slow ledger would have meant unbounded memory [S2].",
            "sources": [
                Source("transcript", "sess-full-0:12-14", "The session where this came up",
                       verified=True).as_dict(),
                Source("web", "https://en.wikipedia.org/wiki/Backpressure_routing",
                       "Backpressure routing", verified=True).as_dict(),
            ],
        }, provenance={"material_version": "snapshot"})
        append(conn, "material_delivered", {"material_id": "m-snapshot-textual"})

    # A spot check: triage thought this was already known and asked anyway.
    # Triage records it before the resolver files it, as in a real run.
    append(conn, "familiarity_judged", {
        "term": "connection pooling", "session_id": sessions[0], "p_knows": 0.82,
        "verdict": "spot_check", "encounter_id": "e-spot", "cut": 0.5, "cut_source": "default",
    }, provenance={"triage_version": "triage/snapshot"})
    append(conn, "concept_created", {"concept_id": "c-pool", "canonical_name": "connection pooling"})
    append(conn, "encounter_recorded", {
        "encounter_id": "e-spot", "concept_id": "c-pool", "source": "transcript",
        "paraphrase": "The worker was changed to reuse database connections from a pool instead of"
                      " opening one per job.",
        "pointer": {"session_id": sessions[0], "line_start": 3, "line_end": 4},
    }, provenance={"detector_version": "detector/0.1.0+snapshot"})

    # Enough pipeline activity for the Developer tab to have something to say.
    for purpose, ms, cost in (("detection", 38_000, 0.0006), ("detection", 22_000, 0.0004),
                              ("familiarity", 260, 0.00002), ("familiarity", 240, 0.00002),
                              ("resolution", 9_400, 0.00014)):
        append(conn, "model_called", {"purpose": purpose, "model": "snapshot", "ok": True,
                                      "duration_ms": ms, "cost": cost, "session_id": sessions[0]})
    append(conn, "familiarity_judged", {
        "term": "retry budget", "session_id": sessions[0], "p_knows": 0.12, "verdict": "passed",
        "encounter_id": "e-snap-passed", "cut": 0.5, "cut_source": "default",
    }, provenance={"triage_version": "triage/snapshot"})
    append(conn, "detector_ran", {
        "session_id": sessions[0], "windows_examined": 6, "calls_made": 2, "max_candidates": 2,
        "candidates": [
            {"term": t, "signal": "accepted", "importance": "central", "rank": i + 1,
             "line_start": 3, "line_end": 4, "emitted": eid is not None,
             **({"encounter_id": eid} if eid else {})}
            for i, (t, eid) in enumerate((("retry budget", "e-snap-passed"),
                                          ("connection pooling", "e-spot"),
                                          ("HTTP status code", None)))
        ],
    }, provenance={"detector_version": "detector/0.1.0+snapshot"})
    append(conn, "familiarity_judged", {
        "term": "HTTP status code", "session_id": sessions[0], "p_knows": 0.91, "verdict": "held_back",
        "encounter_id": "e-snap-held", "cut": 0.5, "cut_source": "default",
    }, provenance={"triage_version": "triage/snapshot"})
    compile_state(conn)
    conn.close()


if __name__ == "__main__":
    build(Path(sys.argv[1]))
    print("built", ", ".join(sorted(p.name for p in Path(sys.argv[1]).iterdir() if not p.name.endswith("-projects"))))
