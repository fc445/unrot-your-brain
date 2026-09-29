"""Run the real pipeline end to end, in a throwaway $UNROT_HOME, and record every stage.

    capture ─▶ windows/chunks ─▶ propose ─▶ rank ─▶ triage ─▶ resolve ─▶ record
                                                                   └▶ material ─▶ grade

Nothing here changes core code: every model is built the way the CLIs build it
and then wrapped, so each call's input size, output, duration and error land in
`results.json` next to what the meter billed. Your real `~/.unrot` is only ever
read (to report the historic log), never written.

Five probes run beside the main pass, because the main pass alone can't say
whether a stage is *right* -- only what it did:

* detection repeats -- the same session N times, for stability (Jaccard) and,
  on the labelled synthetic pair, precision/recall
* a seeded knowledge map -- so triage actually judges instead of passing all
* resolver probes -- near-duplicates with an expected decision
* material -- sources gathered and verified, prose written and citation-checked
* grader golden set -- answers written to be isolated/listed/causal

Usage (from the repo root):

    uv run python spikes/20260927-PR-41-e2e-pipeline-run/e2e_run.py --plan-only
    uv run python spikes/20260927-PR-41-e2e-pipeline-run/e2e_run.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LABELLED = REPO / "spikes" / "20260923-PR-33-bench-runner" / "labelled"

from unrot.env import load_env  # noqa: E402

load_env()

from unrot.analyse import pending  # noqa: E402
from unrot.capture import paths  # noqa: E402
from unrot.capture.ingest import connect as connect_raw, ingest_all, ingest_file  # noqa: E402
from unrot.detector import build_proposer, detect  # noqa: E402
from unrot.detector.detect import plan  # noqa: E402
from unrot.detector import prompt as detector_prompt  # noqa: E402
from unrot.grader.check import question  # noqa: E402
from unrot.grader.jev import build_jev_grader  # noqa: E402
from unrot.grader.model import build_grader  # noqa: E402
from unrot.material.generate import NotGrounded, WouldRecurse, deliver, textual  # noqa: E402
from unrot.material.search import build_search  # noqa: E402
from unrot.material.sources import gather  # noqa: E402
from unrot.material.writer import build_writer  # noqa: E402
from unrot.model import ModelConfig  # noqa: E402
from unrot.pipeline import FILING, Deps, run_session  # noqa: E402
from unrot.resolver import deciders, resolve, strict  # noqa: E402
from unrot.resolver.match import normalise  # noqa: E402
from unrot.resolver.submissions import manual  # noqa: E402
from unrot.spend import Meter  # noqa: E402
from unrot.store import append, compile_state  # noqa: E402
from unrot.store.__main__ import open_store  # noqa: E402
from unrot.triage import build_familiarity  # noqa: E402

LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(*parts) -> None:
    with LOCK:
        print(f"[{datetime.now():%H:%M:%S}]", *parts, flush=True)


# ---------------------------------------------------------------------------
# Instrumented model callables
# ---------------------------------------------------------------------------


class Recorder:
    """Wraps a model callable so every call leaves a row, success or not."""

    def __init__(self, stage: str, rows: list):
        self.stage, self.rows = stage, rows

    def wrap(self, fn, describe_in, describe_out, **tags):
        def wrapped(*args, **kwargs):
            started = time.monotonic()
            row = {"stage": self.stage, **tags, **describe_in(*args, **kwargs)}
            try:
                out = fn(*args, **kwargs)
            except Exception as exc:
                row.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}",
                           seconds=round(time.monotonic() - started, 2))
                with LOCK:
                    self.rows.append(row)
                raise
            row.update(ok=True, seconds=round(time.monotonic() - started, 2), **describe_out(out))
            with LOCK:
                self.rows.append(row)
            return out

        for attr in ("model",):
            if hasattr(fn, attr):
                setattr(wrapped, attr, getattr(fn, attr))
        return wrapped


def calls_of(meter: Meter) -> list[dict]:
    return [{**c.payload(), "purpose": c.purpose} for c in meter.calls]


# ---------------------------------------------------------------------------
# Stage 0/1: capture
# ---------------------------------------------------------------------------


def capture(home: Path, exclude: set[str]) -> list[dict]:
    raw = connect_raw(home)
    rows = []
    started = time.monotonic()
    for result in ingest_all(conn=raw, root=home):
        rows.append({**result.__dict__, "source": "claude-projects"})
    for path in sorted(LABELLED.glob("*.jsonl")):
        result = ingest_file(path, conn=raw, root=home)
        rows.append({**result.__dict__, "source": "synthetic-labelled"})
    for sid in exclude:
        raw.execute("DELETE FROM raw_turns WHERE session_id = ?", (sid,))
        raw.execute("DELETE FROM raw_sessions WHERE session_id = ?", (sid,))
    raw.commit()
    log(f"capture: {len(rows)} files in {time.monotonic() - started:.1f}s")
    return rows


def session_shapes(home: Path) -> dict[str, dict]:
    """Per session: turns, windows, chunks and the size of each rendered prompt."""
    raw = connect_raw(home)
    out = {}
    for row in raw.execute("SELECT session_id, cwd FROM raw_sessions"):
        sid = row["session_id"]
        counts = raw.execute(
            "SELECT sum(role='user' AND is_meta=0 AND is_sidechain=0 AND seq=0) human,"
            "       sum(role='assistant') assistant, sum(tool_name IS NOT NULL) tools,"
            "       sum(is_sidechain) sidechain, count(*) turns FROM raw_turns WHERE session_id=?",
            (sid,),
        ).fetchone()
        windows, chunks = plan(raw, sid)
        prompts = [
            len(detector_prompt.render(detector_prompt.format_windows(c), max_candidates=2))
            for c in chunks
        ]
        unpaired = sum(1 for w in windows if w.human_line is None)
        biggest_human = max((len(w.human_text or "") for w in windows), default=0)
        out[sid] = {
            "session_id": sid,
            "cwd": row["cwd"],
            "project": os.path.basename(row["cwd"] or "") or "(none)",
            **{k: counts[k] or 0 for k in counts.keys()},
            "windows": len(windows),
            "unpaired_windows": unpaired,
            "chunks": len(chunks),
            "prompt_chars": prompts,
            "biggest_human_turn_chars": biggest_human,
            "synthetic": sid.startswith("sess-"),
        }
    return out


# ---------------------------------------------------------------------------
# Stage 2: seed a knowledge map, so triage has something to judge against
# ---------------------------------------------------------------------------

#: A persona, not the user's real map. The three `known` entries marked (real)
#: are the only judgments in the real ~/.unrot; the rest are assumptions about
#: an experienced Python/backend developer new to shipping Mac apps.
PERSONA_KNOWN = [
    ("Git Rebase", "Replaying commits onto a new base when tidying a branch before merge. (real)"),
    ("Backpressure", "Slowing producers when a consumer queue fills, discussed for the watcher. (real)"),
    ("Walking Skeleton", "Shipping a thin end-to-end slice first before filling in stages. (real)"),
    ("idempotency", "Making a retried request safe so re-running it has no extra effect."),
    ("SQLite WAL mode", "Letting readers continue while a writer appends, chosen for the store."),
    ("pytest fixtures", "Sharing set-up such as a temp directory between tests."),
    ("LangGraph", "Building the analysis as a graph of nodes rather than a hand-rolled loop."),
    ("JSON Schema", "Describing the shape a model's structured output must take."),
]
PERSONA_UNKNOWN = [
    ("macOS notarization", "Apple's scan of a signed app before Gatekeeper lets it open on other Macs."),
    ("hardened runtime", "A signing option that restricts what the app process may do at run time."),
    ("MVCC (Multi-Version Concurrency Control)", "How a database lets readers see a snapshot while writers change rows."),
    ("Unix Domain Socket", "The local socket the Mac app and the Python core talk over."),
    ("ad hoc signing", "Signing the app without a Developer ID certificate for local builds."),
    ("ScreenCaptureKit", "The framework the app would use to read what is on screen."),
    ("macOS TCC (Transparency, Consent, and Control)", "The permission prompts that gate screen and file access."),
    ("Developer ID", "The certificate needed to distribute a Mac app outside the App Store."),
]


def seed_map(conn: sqlite3.Connection) -> None:
    for entries, verdict in ((PERSONA_KNOWN, "encounter_dismissed"), (PERSONA_UNKNOWN, "encounter_confirmed")):
        for name, gloss in entries:
            res = resolve(conn, manual(name, gloss), decide=strict, origin="seed", recompile=False)
            append(conn, verdict, {"encounter_id": res.encounter_id}, origin="seed")
    conn.commit()
    compile_state(conn)


# ---------------------------------------------------------------------------
# Stage 3: the pipeline, session by session, several at once like the app
# ---------------------------------------------------------------------------


def build_models(config: ModelConfig, meter: Meter, rows: list, sid: str, *, triage: bool):
    propose = Recorder("detection", rows).wrap(
        build_proposer(config, meter=meter),
        lambda prompt: {"prompt_chars": len(prompt)},
        lambda out: {"returned": out},
        session_id=sid,
    )
    decide = Recorder("resolution", rows).wrap(
        deciders.build_decider(config, meter=meter),
        lambda sub, shortlist: {"input": sub.text, "shortlist": [k.canonical_name for k in shortlist]},
        lambda out: {"answer": out},
        session_id=sid,
    )
    judge = None
    if triage:
        judge = Recorder("familiarity", rows).wrap(
            build_familiarity(config, meter=meter),
            lambda term, gloss, kmap: {"term": term},
            lambda out: {"p_knows": out.get("p_knows"), "confidence": out.get("confidence")},
            session_id=sid,
        )
    return propose, decide, judge


def cand(c) -> dict:
    return {k: getattr(c, k) for k in ("term", "paraphrase", "signal", "importance", "rank", "line_start", "line_end")}


def analyse_one(home: Path, sid: str, config: ModelConfig, rows: list, *, triage: bool) -> dict:
    conn, raw = open_store(home), connect_raw(home)
    meter = Meter()
    propose, decide, judge = build_models(config, meter, rows, sid, triage=triage)
    started = time.monotonic()
    out = {"session_id": sid, "started": now()}
    try:
        with meter.about(session_id=sid):
            run = run_session(
                sid,
                Deps(conn=conn, raw=raw, propose=propose, decide=decide,
                     detector_label=config.label, resolver_label=config.label,
                     judge=judge, triage_label=(judge.model.replace("/", "-") if judge else "none"),
                     concurrency=config.concurrency),
            )
        out.update(
            ok=True,
            windows=run.result.windows_examined,
            calls=run.result.calls_made,
            ranked=[cand(c) for c in run.result.ranked],
            detector_emitted=[c.term for c in run.result.emitted],
            filed=[c.term for c in run.emitted],
            held_back=[{"term": v.candidate.term, "p_knows": v.p_knows} for v in run.held_back],
            resolutions=[
                {"input": r.submission.text, "decision": r.decision, "concept_id": r.concept_id,
                 "canonical": r.canonical_name, "without_model": r.decided_without_model,
                 "reasoning": r.reasoning}
                for r in run.resolutions
            ],
        )
    except Exception as exc:  # noqa: BLE001 - a failed session is a result
        out.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:400]}", trace=traceback.format_exc()[-1500:])
    finally:
        out["seconds"] = round(time.monotonic() - started, 1)
        out["meter"] = calls_of(meter)
        with FILING:
            meter.flush(conn)
        conn.close()
        raw.close()
    log(f"pipeline {sid[:13]}: {'ok' if out.get('ok') else 'FAILED'} in {out['seconds']}s"
        f" ranked={len(out.get('ranked', []))} filed={out.get('filed')}")
    return out


def triage_verdicts(conn: sqlite3.Connection) -> list[dict]:
    return [
        json.loads(r["payload"])
        for r in conn.execute("SELECT payload FROM events WHERE event_type='familiarity_judged' ORDER BY event_id")
    ]


# ---------------------------------------------------------------------------
# Probe: detection repeats -- stability, and precision/recall on labels
# ---------------------------------------------------------------------------


def repeats(home: Path, sids: list[str], n: int, config: ModelConfig, rows: list) -> list[dict]:
    def one(sid: str, i: int) -> dict:
        raw = connect_raw(home)
        meter = Meter()
        propose = Recorder("detection-repeat", rows).wrap(
            build_proposer(config, meter=meter),
            lambda prompt: {"prompt_chars": len(prompt)},
            lambda out: {"returned": out},
            session_id=sid, repeat=i,
        )
        started = time.monotonic()
        try:
            result = detect(raw, sid, propose=propose, model_label=config.label)
            got = {"ok": True, "ranked": [cand(c) for c in result.ranked],
                   "emitted": [c.term for c in result.emitted]}
        except Exception as exc:  # noqa: BLE001
            got = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        raw.close()
        log(f"repeat {sid[:13]} #{i}: {got.get('emitted', got.get('error'))}")
        return {"session_id": sid, "repeat": i, "seconds": round(time.monotonic() - started, 1),
                "meter": calls_of(meter), **got}

    jobs = [(sid, i) for sid in sids for i in range(n)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(lambda j: one(*j), jobs))


# ---------------------------------------------------------------------------
# Probe: resolver near-duplicates, each against a fresh copy of the same graph
# ---------------------------------------------------------------------------

RESOLVER_SEED = [
    ("Kubernetes", "Container orchestration the deploy target runs on."),
    ("transactional outbox", "Writing an event to an outbox table in the same transaction as the change."),
    ("idempotency", "Making a retried request safe to repeat."),
    ("MVCC (Multi-Version Concurrency Control)", "Readers see a snapshot while writers change rows."),
    ("Unix Domain Socket", "The local socket between the Mac app and the core."),
    ("ad hoc signing", "Signing without a Developer ID certificate."),
    ("macOS notarization", "Apple's scan of a signed app before Gatekeeper opens it."),
    ("Git Rebase", "Replaying commits onto a new base."),
]

#: (text, paraphrase, acceptable decisions, expected target or None for new)
RESOLVER_PROBES = [
    ("K8s", "Deploying the worker to K8s.", {"existing", "alias"}, "Kubernetes"),
    ("outbox pattern", "Using the outbox pattern so events are not lost.", {"existing", "alias"}, "transactional outbox"),
    ("idempotent", "Making the webhook handler idempotent.", {"existing", "alias"}, "idempotency"),
    ("multiversion concurrency control", "How Postgres lets reads not block writes.", {"existing", "alias"}, "MVCC (Multi-Version Concurrency Control)"),
    ("UDS", "The core listens on a UDS in ~/.unrot/run.", {"existing", "alias"}, "Unix Domain Socket"),
    ("notarisation", "Notarisation before the DMG is shared.", {"existing", "alias"}, "macOS notarization"),
    ("git rebase", "Rebasing the branch onto develop.", {"existing"}, "Git Rebase"),
    ("Git Merge", "Merging develop into the feature branch.", {"new"}, None),
    ("optimistic locking", "Retrying a write if the row version changed underneath it.", {"new"}, None),
    ("Developer ID signing", "Signing with an Apple Developer ID certificate for distribution.", {"new"}, None),
    ("exactly-once delivery", "Whether the queue guarantees each message is handled once.", {"new"}, None),
]


def resolver_probes(work: Path, config: ModelConfig, rows: list, n: int) -> list[dict]:
    seed_home = work / "resolver-home"
    if seed_home.exists():
        shutil.rmtree(seed_home)
    seed = open_store(seed_home)
    for name, gloss in RESOLVER_SEED:
        resolve(seed, manual(name, gloss), decide=strict, recompile=False)
    seed.commit()
    compile_state(seed)
    target_ids = {r["canonical_name"]: r["concept_id"] for r in seed.execute("SELECT * FROM compiled_concepts")}
    seed.close()

    def one(probe, i):
        text, gloss, ok_decisions, target = probe
        # A fresh copy of the same small graph per probe, so probes can't see each other.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        src = sqlite3.connect(seed_home / "unrot.db")
        src.backup(conn)
        src.close()
        meter = Meter()
        decide = Recorder("resolver-probe", rows).wrap(
            deciders.build_decider(config, meter=meter),
            lambda sub, shortlist: {"input": sub.text, "shortlist": [k.canonical_name for k in shortlist]},
            lambda out: {"answer": out}, repeat=i,
        )
        started = time.monotonic()
        try:
            r = resolve(conn, manual(text, gloss), decide=decide, model_label=config.label)
            got_target = r.canonical_name if r.decision != "new" else None
            correct = r.decision in ok_decisions and (target is None or r.concept_id == target_ids.get(target))
            out = {"ok": True, "decision": r.decision, "target": got_target, "canonical": r.canonical_name,
                   "reasoning": r.reasoning, "without_model": r.decided_without_model, "correct": correct}
        except Exception as exc:  # noqa: BLE001
            out = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "correct": False}
        conn.close()
        return {"input": text, "expected": sorted(ok_decisions), "expected_target": target, "repeat": i,
                "seconds": round(time.monotonic() - started, 1), "meter": calls_of(meter), **out}

    jobs = [(p, i) for p in RESOLVER_PROBES for i in range(n)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        out = list(pool.map(lambda j: one(*j), jobs))
    log(f"resolver probes: {sum(o['correct'] for o in out)}/{len(out)} as expected")
    return out


# ---------------------------------------------------------------------------
# Probe: material for a few filed concepts
# ---------------------------------------------------------------------------


def material(home: Path, config: ModelConfig, rows: list, limit: int) -> list[dict]:
    conn, raw = open_store(home), connect_raw(home)
    concepts = conn.execute(
        "SELECT c.concept_id, c.canonical_name FROM compiled_concepts c"
        " JOIN compiled_encounters e ON e.concept_id = c.concept_id"
        " WHERE c.merged_into IS NULL AND e.source = 'transcript' AND e.session_id NOT LIKE 'sess-%'"
        " GROUP BY c.concept_id ORDER BY max(e.occurred_at) DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for row in concepts:
        meter = Meter()
        search = Recorder("material-search", rows).wrap(
            build_search(config, meter=meter), lambda term: {"term": term},
            lambda found: {"found": len(found)})
        write = Recorder("material-write", rows).wrap(
            build_writer(config, meter=meter), lambda prompt: {"prompt_chars": len(prompt)},
            lambda ans: {"chars": len(str((ans or {}).get("body") or ""))})
        started = time.monotonic()
        entry = {"concept_id": row["concept_id"], "term": row["canonical_name"]}
        try:
            with meter.about(concept_id=row["concept_id"]):
                t0 = time.monotonic()
                found = gather(conn, raw, row["concept_id"], search=search)
                entry["gather_seconds"] = round(time.monotonic() - t0, 1)
                entry["sources"] = [
                    {"kind": s.kind, "title": s.title[:90], "verified": s.verified, "note": s.note,
                     "ref": getattr(s, "ref", None)}
                    for s in found
                ]
                made = textual(conn, row["concept_id"], found, write=write,
                               resolve_named=lambda term: resolve(conn, manual(term), decide=strict, recompile=False),
                               model_label=config.label)
                deliver(conn, made.material_id)
                entry.update(outcome="written", body=made.body,
                             citations=sorted(set(re.findall(r"\[S(\d+)\]", made.body or ""))),
                             covered=made.covered)
        except (NotGrounded, WouldRecurse) as exc:
            entry.update(outcome=f"refused: {type(exc).__name__}", reason=str(exc))
        except Exception as exc:  # noqa: BLE001
            entry.update(outcome="error", reason=f"{type(exc).__name__}: {str(exc)[:300]}")
        entry["seconds"] = round(time.monotonic() - started, 1)
        entry["meter"] = calls_of(meter)
        meter.flush(conn)
        log(f"material {row['canonical_name']!r}: {entry['outcome']} in {entry['seconds']}s")
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Probe: grader golden set
# ---------------------------------------------------------------------------

GOLDEN = {
    "idempotency": {
        "isolated": "Something about being able to call it again.",
        "listed": "An operation you can repeat. Often uses an idempotency key. Common in payment APIs and retries. PUT is idempotent, POST isn't.",
        "causal": "Doing it twice leaves things the same as doing it once, so a client that times out can safely retry without charging twice -- that's why the server stores a key and returns the first result for a repeat.",
    },
    "backpressure": {
        "isolated": "No idea, something with queues?",
        "listed": "It's a flow-control thing. Used in streams and queues. Producers and consumers. Reactive libraries have it built in.",
        "causal": "When the consumer can't keep up, it signals the producer to slow down, because otherwise the buffer between them grows without bound until memory runs out or messages get dropped.",
    },
    "MVCC (Multi-Version Concurrency Control)": {
        "isolated": "It's a Postgres feature.",
        "listed": "Multi-version concurrency control. Keeps several versions of a row. Used by Postgres and MySQL InnoDB. Needs vacuuming.",
        "causal": "Each write makes a new row version instead of overwriting, so a reader can keep looking at the snapshot that existed when it started -- which is why reads don't have to wait for writers, and why old versions later need vacuuming away.",
    },
    "Unix Domain Socket": {
        "isolated": "A kind of socket.",
        "listed": "A socket that's a file on disk. Local only. Faster than TCP. Used by Docker and Postgres.",
        "causal": "It's addressed by a filesystem path rather than a port, so only processes on this machine can reach it and file permissions control who connects -- which is why it's a safe way for the app to talk to the core without opening a network port.",
    },
}


def grader_golden(config: ModelConfig, rows: list, n: int) -> list[dict]:
    meter = Meter()
    graders = {
        "jev": build_jev_grader(config, meter=meter),
        "llm": build_grader(config, meter=meter),
    }
    jobs = [(g, term, want, ans, i) for g in graders for term, levels in GOLDEN.items()
            for want, ans in levels.items() for i in range(n)]

    def one(job):
        g, term, want, ans, i = job
        started = time.monotonic()
        try:
            got = graders[g](question(term), ans)
            res = {"ok": True, "level": got.get("level"), "probabilities": got.get("probabilities"),
                   "reasoning": got.get("reasoning")}
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        return {"grader": g, "term": term, "expected": want, "answer": ans, "repeat": i,
                "seconds": round(time.monotonic() - started, 2), **res}

    with ThreadPoolExecutor(max_workers=6) as pool:
        out = list(pool.map(one, jobs))
    for g in graders:
        mine = [o for o in out if o["grader"] == g]
        log(f"grader {g}: {sum(o.get('level') == o['expected'] for o in mine)}/{len(mine)} as expected")
    return out, calls_of(meter)


# ---------------------------------------------------------------------------
# The historic log, read-only
# ---------------------------------------------------------------------------


def historic(real_home: Path) -> dict:
    path = real_home / "unrot.db"
    if not path.exists():
        return {}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    calls = [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM events WHERE event_type='model_called'")]
    concepts = [dict(r) for r in db.execute("SELECT canonical_name, state, encounter_count FROM compiled_concepts")]
    sessions = [dict(r) for r in db.execute("SELECT * FROM compiled_sessions")]
    db.close()
    return {"calls": calls, "concepts": concepts, "sessions": sessions}


# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=str(HERE / "out" / datetime.now().strftime("%Y%m%d-%H%M")))
    p.add_argument("--exclude", action="append", default=[], help="session id to leave out (e.g. the one running this)")
    p.add_argument("--plan-only", action="store_true", help="capture and plan, no model calls")
    p.add_argument("--sessions-parallel", type=int, default=6)
    p.add_argument("--limit", type=int, default=None, help="analyse at most this many real sessions")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--repeat-sessions", type=int, default=4, help="real sessions to repeat, besides the synthetic pair")
    p.add_argument("--material", type=int, default=4)
    p.add_argument("--skip", action="append", default=[], help="pipeline|repeats|resolver|material|grader")
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    home = out_dir / "home"
    if home.exists():
        shutil.rmtree(home)
    paths.ensure_layout(home)
    os.environ["UNROT_HOME"] = str(home)  # belt and braces: nothing below may reach ~/.unrot

    config = ModelConfig.from_env()
    results: dict = {
        "started": now(),
        "config": {"model": config.model, "label": config.label, "effort": config.reasoning_effort,
                   "max_tokens": config.max_tokens, "concurrency": config.concurrency,
                   "detector_prompt": detector_prompt.prompt_id()},
        "historic": historic(Path.home() / ".unrot"),
    }
    rows: list = []
    results["calls"] = rows

    def save():
        results["finished"] = now()
        (out_dir / "results.json").write_text(json.dumps(results, indent=1, default=str))

    results["capture"] = capture(home, set(args.exclude))
    results["shapes"] = session_shapes(home)
    conn = open_store(home)
    queue = [p.session_id for p in pending(conn, connect_raw(home))]
    results["pending"] = queue
    log(f"pending: {len(queue)} sessions, "
        f"{sum(results['shapes'][s]['chunks'] for s in queue)} chunk calls planned")
    save()
    if args.plan_only:
        return 0
    if not config.api_key:
        print("No API key; run with OPENROUTER_API_KEY set.", file=sys.stderr)
        return 2

    seed_map(conn)
    results["persona"] = {"known": PERSONA_KNOWN, "unknown": PERSONA_UNKNOWN}
    conn.close()

    real = [s for s in queue if not s.startswith("sess-")]
    if args.limit:
        real = real[: args.limit]
    targets = [s for s in queue if s.startswith("sess-")] + real

    if "pipeline" not in args.skip:
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=args.sessions_parallel) as pool:
            results["pipeline"] = list(pool.map(
                lambda s: analyse_one(home, s, config, rows, triage=True), targets))
        results["pipeline_seconds"] = round(time.monotonic() - t0, 1)
        conn = open_store(home)
        results["triage"] = triage_verdicts(conn)
        results["concepts"] = [dict(r) for r in conn.execute(
            "SELECT concept_id, canonical_name, state, encounter_count, aliases FROM compiled_concepts")]
        conn.close()
        save()

    if "repeats" not in args.skip:
        # Mid-sized real sessions: ones that found something are the interesting ones.
        found = [r["session_id"] for r in results.get("pipeline", []) if r.get("ok") and r.get("ranked")
                 and not r["session_id"].startswith("sess-")]
        picks = [s for s in queue if s.startswith("sess-")] + found[: args.repeat_sessions]
        results["repeats"] = repeats(home, picks, args.repeats, config, rows)
        save()

    if "resolver" not in args.skip:
        results["resolver_probes"] = resolver_probes(out_dir, config, rows, n=2)
        save()

    if "material" not in args.skip:
        results["material"] = material(home, config, rows, args.material)
        save()

    if "grader" not in args.skip:
        results["grader"], results["grader_meter"] = grader_golden(config, rows, n=2)
        save()

    save()
    log(f"done: {out_dir / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
