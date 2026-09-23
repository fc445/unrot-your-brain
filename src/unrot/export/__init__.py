"""Everything the models decided, flattened for analysis (PR-32).

unrot runs four model-backed agents -- detector, resolver, grader, material
writer -- and the log already holds what each of them said and what the user
said back. This writes that out as a folder another agent can read without this
repo: the raw log, one flat file per agent, a manifest and a data dictionary.

It reads the log and writes files. It never writes to the store.

Three rules shape it:

* **Derived from the log, by the same folds the app uses.** Flags come from
  `compiled_encounters` and sessions from `compiled_sessions`, so the counts here
  agree with the app and with `store metrics`. `events.jsonl` goes in as well,
  so anything derived can be checked against its source.
* **No text by default.** Transcripts hold code, paths and sometimes secrets.
  Explanation text, the question it answered, the grader's reasoning about it,
  material bodies and source excerpts are withheld unless `include_text` is
  set. Each row says which fields it withheld rather than silently lacking them.
  Terms, paraphrases and line numbers stay: without them nothing can be judged.
* **Fixtures are marked, not dropped.** Every row carries `fixture`, so seeded
  data can be excluded by whoever reads it and is never mistaken for a finding.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePath
from urllib.parse import urlparse

from .. import __version__
from ..store.fixtures import FIXTURE_ORIGIN
from .readme import README

FORMAT = "unrot-export/1"

#: Payload fields that hold text a person wrote, a question put to them, or
#: something lifted from their files. Withheld unless asked for.
TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "explanation_submitted": ("raw_text", "prompt_text"),
    # The grader's reasoning quotes and paraphrases the answer it graded.
    "explanation_graded": ("reasoning",),
    "material_generated": ("body",),
}

FILES = (
    "events",
    "flags",
    "detections",
    "sessions",
    "resolutions",
    "grades",
    "calls",
    "materials",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def model_of(version: str | None) -> str | None:
    """The model label inside a version string like `detector/0.1.0+MODEL+PROMPT`.

    It is the label (`/` replaced by `-`), not the exact model id, because that is
    all the version records.
    """
    if not version or "+" not in version:
        return None
    middle = version.split("+", 1)[1]
    return middle.rsplit("+", 1)[0] if "+" in middle else middle


def _seconds_between(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    try:
        start = datetime.fromisoformat(a.replace("Z", "+00:00"))
        end = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((end - start).total_seconds(), 3)


def _scrub_source(source):
    """A material source without its excerpt, and a code path cut to its file name.

    Older material (and the fixtures) stored a source as a bare URL; that passes
    through as it is.
    """
    if not isinstance(source, dict):
        return source
    out = {k: v for k, v in source.items() if k != "excerpt"}
    if out.get("kind") == "code" and out.get("ref"):
        out["ref"] = PurePath(out["ref"]).name
    return out


class _Log:
    """The event log, read once and indexed the ways the flat files need it."""

    def __init__(self, conn: sqlite3.Connection, since: str | None) -> None:
        self.rows = []
        for row in conn.execute("SELECT * FROM events ORDER BY event_id"):
            self.rows.append(
                {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "actor": row["actor"],
                    "occurred_at": row["occurred_at"],
                    "recorded_at": row["recorded_at"],
                    "origin": row["origin"],
                    "subject_type": row["subject_type"],
                    "subject_id": row["subject_id"],
                    "supersedes": row["supersedes"],
                    "provenance": json.loads(row["provenance"]) if row["provenance"] else {},
                    "payload": json.loads(row["payload"]),
                }
            )
        self.since = since
        self.by_type: dict[str, list[dict]] = defaultdict(list)
        for row in self.rows:
            self.by_type[row["event_type"]].append(row)

    def inside(self, when: str | None) -> bool:
        return self.since is None or (when is not None and when >= self.since)

    def of(self, event_type: str) -> list[dict]:
        return [r for r in self.by_type.get(event_type, []) if self.inside(r["occurred_at"])]


def _fixture(origin: str | None) -> bool:
    return origin == FIXTURE_ORIGIN


# ---------------------------------------------------------------------------
# The flat files. Each returns a list of rows; `write` puts them on disk.
# ---------------------------------------------------------------------------


def events(log: _Log, *, include_text: bool) -> list[dict]:
    out = []
    for row in log.rows:
        if not log.inside(row["occurred_at"]):
            continue
        payload = dict(row["payload"])
        withheld = []
        if not include_text:
            for key in TEXT_FIELDS.get(row["event_type"], ()):
                if key in payload:
                    payload.pop(key)
                    withheld.append(key)
            if row["event_type"] == "material_generated" and payload.get("sources"):
                payload["sources"] = [_scrub_source(s) for s in payload["sources"]]
                withheld.append("sources.excerpt")
        item = {**row, "payload": payload, "fixture": _fixture(row["origin"])}
        if withheld:
            item["withheld"] = withheld
        out.append(item)
    return out


def _latest_emission(log: _Log) -> dict[str, dict]:
    """encounter id -> the candidate row from the latest detector run that emitted it."""
    found: dict[str, dict] = {}
    for run in log.by_type.get("detector_ran", []):
        for candidate in run["payload"].get("candidates", []):
            if candidate.get("emitted") and candidate.get("encounter_id"):
                found[candidate["encounter_id"]] = candidate
    return found


def flags(conn: sqlite3.Connection, log: _Log) -> list[dict]:
    """One row per detector flag as the app shows it now, with the user's verdict."""
    origins = {
        r["payload"]["encounter_id"]: r["origin"]
        for r in log.by_type.get("encounter_recorded", [])
    }
    emitted = _latest_emission(log)
    out = []
    for row in conn.execute(
        "SELECT e.*, c.canonical_name FROM compiled_encounters e"
        " LEFT JOIN compiled_concepts c ON c.concept_id = e.concept_id"
        " WHERE e.source = 'transcript' ORDER BY e.occurred_at, e.encounter_id"
    ):
        if not log.inside(row["occurred_at"]):
            continue
        candidate = emitted.get(row["encounter_id"], {})
        out.append(
            {
                "encounter_id": row["encounter_id"],
                "session_id": row["session_id"],
                "term": candidate.get("term") or row["canonical_name"],
                "concept_id": row["concept_id"],
                "concept": row["canonical_name"],
                "paraphrase": row["paraphrase"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "signal": candidate.get("signal"),
                "importance": candidate.get("importance"),
                "rank": candidate.get("rank"),
                "detector_version": row["detector_version"],
                "model": model_of(row["detector_version"]),
                "flagged_at": row["occurred_at"],
                "verdict": row["judgment"],
                "judged_at": row["judged_at"],
                "seconds_to_verdict": _seconds_between(row["occurred_at"], row["judged_at"]),
                "fixture": _fixture(origins.get(row["encounter_id"])),
            }
        )
    return out


def detections(conn: sqlite3.Connection, log: _Log) -> list[dict]:
    """One row per candidate per detector run, emitted or not, across every run."""
    verdicts = {
        r["encounter_id"]: r["judgment"]
        for r in conn.execute("SELECT encounter_id, judgment FROM compiled_encounters")
    }
    out = []
    for run in log.of("detector_ran"):
        payload = run["payload"]
        version = run["provenance"].get("detector_version")
        for candidate in payload.get("candidates", []):
            encounter_id = candidate.get("encounter_id")
            out.append(
                {
                    "run_id": run["event_id"],
                    "ran_at": run["occurred_at"],
                    "session_id": payload["session_id"],
                    "detector_version": version,
                    "model": model_of(version),
                    "term": candidate.get("term"),
                    "paraphrase": candidate.get("paraphrase"),
                    "signal": candidate.get("signal"),
                    "importance": candidate.get("importance"),
                    "rank": candidate.get("rank"),
                    "line_start": candidate.get("line_start"),
                    "line_end": candidate.get("line_end"),
                    "emitted": bool(candidate.get("emitted")),
                    "encounter_id": encounter_id,
                    "verdict": verdicts.get(encounter_id) if encounter_id else None,
                    "fixture": _fixture(run["origin"]),
                }
            )
    return out


def _repos(raw: sqlite3.Connection | None) -> dict[str, str]:
    if raw is None:
        return {}
    rows = raw.execute("SELECT session_id, cwd FROM raw_sessions WHERE cwd IS NOT NULL")
    return {r["session_id"]: r["cwd"].rstrip("/").rsplit("/", 1)[-1] for r in rows if r["cwd"]}


def _human_turns(raw: sqlite3.Connection | None) -> dict[str, int]:
    if raw is None:
        return {}
    rows = raw.execute(
        "SELECT session_id, count(*) AS n FROM raw_turns"
        " WHERE role = 'user' AND is_meta = 0 AND is_sidechain = 0 GROUP BY session_id"
    )
    return {r["session_id"]: r["n"] for r in rows}


def sessions(conn: sqlite3.Connection, log: _Log, raw: sqlite3.Connection | None) -> list[dict]:
    """One row per analysed session: its latest analysis, latest run, and what it cost."""
    analysed = {r["session_id"]: dict(r) for r in conn.execute("SELECT * FROM compiled_sessions")}
    analysed_origin = {
        r["payload"]["session_id"]: r["origin"] for r in log.by_type.get("session_analysed", [])
    }
    runs: dict[str, list[dict]] = defaultdict(list)
    for run in log.by_type.get("detector_ran", []):
        runs[run["payload"]["session_id"]].append(run)

    spent: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "failed": 0, "cost": 0.0, "priced": 0, "duration_ms": 0.0, "timed": 0}
    )
    for call in log.by_type.get("model_called", []):
        payload = call["payload"]
        session = payload.get("session_id")
        if not session or payload.get("purpose") not in ("detection", "resolution"):
            continue
        s = spent[session]
        s["calls"] += 1
        s["failed"] += not payload.get("ok", True)
        if isinstance(payload.get("cost"), int | float):
            s["cost"] += payload["cost"]
            s["priced"] += 1
        if isinstance(payload.get("duration_ms"), int | float):
            s["duration_ms"] += payload["duration_ms"]
            s["timed"] += 1

    repos, turns = _repos(raw), _human_turns(raw)
    out = []
    for session_id in sorted(set(analysed) | set(runs)):
        state = analysed.get(session_id, {})
        when = state.get("analysed_at") or (runs[session_id][-1]["occurred_at"] if runs[session_id] else None)
        if not log.inside(when):
            continue
        latest = runs[session_id][-1]["payload"] if runs[session_id] else {}
        candidates = latest.get("candidates", [])
        s = spent.get(session_id)
        origin = analysed_origin.get(session_id) or (
            runs[session_id][-1]["origin"] if runs[session_id] else None
        )
        out.append(
            {
                "session_id": session_id,
                "repo": repos.get(session_id),
                "human_turns": turns.get(session_id),
                "analysed_at": state.get("analysed_at"),
                "detector_version": state.get("detector_version"),
                "model": model_of(state.get("detector_version")),
                "candidates_emitted": state.get("candidates_found"),
                "clean": state.get("candidates_found") == 0 if state else None,
                "runs": len(runs[session_id]),
                "latest_run_windows": latest.get("windows_examined"),
                "latest_run_calls": latest.get("calls_made"),
                "latest_run_candidates_found": len(candidates) if latest else None,
                "latest_run_candidates_emitted": sum(1 for c in candidates if c.get("emitted"))
                if latest
                else None,
                "model_calls": s["calls"] if s else 0,
                "failed_calls": s["failed"] if s else 0,
                "cost_usd": round(s["cost"], 8) if s and s["priced"] else None,
                "priced_calls": s["priced"] if s else 0,
                "model_ms": round(s["duration_ms"], 1) if s and s["timed"] else None,
                "fixture": _fixture(origin),
            }
        )
    return out


def resolutions(conn: sqlite3.Connection, log: _Log) -> list[dict]:
    """One row per resolver judgment, and whether the user later argued with it."""
    corrections = {}
    for row in log.by_type.get("resolver_judgment_corrected", []):
        target = row["supersedes"] or row["payload"].get("target_event_id")
        corrections[target] = row["occurred_at"]
    merged = {
        r["concept_id"]
        for r in conn.execute("SELECT concept_id FROM compiled_concepts WHERE merged_into IS NOT NULL")
    }
    out = []
    for row in log.of("resolver_judgment"):
        payload, provenance = row["payload"], row["provenance"]
        version = provenance.get("resolver_version")
        out.append(
            {
                "judgment_id": row["event_id"],
                "decided_at": row["occurred_at"],
                "input_text": payload.get("input_text"),
                "source": payload.get("source"),
                "decision": payload.get("decision"),
                "reasoning": payload.get("reasoning"),
                "concept_id": payload.get("concept_id"),
                "alias": payload.get("alias"),
                "decided_without_model": bool(payload.get("decided_without_model")),
                "resolver_version": version,
                "model": None if payload.get("decided_without_model") else model_of(version),
                "detector_version": provenance.get("detector_version"),
                "corrected": row["event_id"] in corrections,
                "corrected_at": corrections.get(row["event_id"]),
                "merged_later": payload.get("concept_id") in merged,
                "fixture": _fixture(row["origin"]),
            }
        )
    return out


def grades(conn: sqlite3.Connection, log: _Log, *, include_text: bool) -> list[dict]:
    """One row per explanation, graded or not, with its latest grade."""
    origins = {r["event_id"]: r["origin"] for r in log.by_type.get("explanation_submitted", [])}
    out = []
    for row in conn.execute("SELECT * FROM compiled_explanations ORDER BY submitted_at"):
        if not log.inside(row["submitted_at"]):
            continue
        item = {
            "explanation_id": row["explanation_id"],
            "concept_id": row["concept_id"],
            "encounter_id": row["encounter_id"],
            "submitted_at": row["submitted_at"],
            "prompt_version": row["prompt_version"],
            "answer_chars": len(row["raw_text"] or ""),
            "graded": row["level"] is not None,
            "level": row["level"],
            "rubric": row["rubric"],
            "probabilities": json.loads(row["probabilities"]) if row["probabilities"] else None,
            "confidence": row["confidence"],
            "grader_version": row["grader_version"],
            "model": model_of(row["grader_version"]),
            "graded_at": row["graded_at"],
            "fixture": _fixture(origins.get(row["explanation_id"])),
        }
        if include_text:
            item.update(
                answer=row["raw_text"], question=row["prompt_text"], grader_reasoning=row["reasoning"]
            )
        out.append(item)
    return out


def calls(log: _Log) -> list[dict]:
    """One row per model call, failed calls included."""
    return [
        {
            "event_id": row["event_id"],
            "occurred_at": row["occurred_at"],
            **row["payload"],
            "fixture": _fixture(row["origin"]),
        }
        for row in log.of("model_called")
    ]


def materials(log: _Log) -> list[dict]:
    """One row per attempt to make material: generated (and delivered?) or refused."""
    delivered = {r["payload"]["material_id"] for r in log.by_type.get("material_delivered", [])}
    out = []
    for row in log.of("material_generated") + log.of("material_refused"):
        payload = row["payload"]
        version = row["provenance"].get("material_version")
        generated = row["event_type"] == "material_generated"
        sources = payload.get("sources") or []
        covers = payload.get("covers_concept_ids") or []
        out.append(
            {
                "event_id": row["event_id"],
                "at": row["occurred_at"],
                "outcome": "generated" if generated else "refused",
                "material_id": payload.get("material_id"),
                "concept_id": covers[0] if covers else payload.get("concept_id"),
                "format": payload.get("format"),
                "reason": payload.get("reason"),
                "detail": payload.get("detail"),
                "delivered": payload.get("material_id") in delivered if generated else None,
                "sources": len(sources) if generated else None,
                "verified_sources": sum(1 for s in sources if isinstance(s, dict) and s.get("verified")) if generated else None,
                "also_covers": len(covers) - 1 if covers else None,
                "material_version": version,
                "model": model_of(version),
                "fixture": _fixture(row["origin"]),
            }
        )
    return sorted(out, key=lambda r: r["event_id"])


# ---------------------------------------------------------------------------
# Writing it
# ---------------------------------------------------------------------------


def build(
    conn: sqlite3.Connection,
    *,
    raw: sqlite3.Connection | None = None,
    since: str | None = None,
    include_text: bool = False,
) -> dict[str, list[dict]]:
    """Every flat file's rows, keyed by file name (without `.jsonl`)."""
    log = _Log(conn, since)
    return {
        "events": events(log, include_text=include_text),
        "flags": flags(conn, log),
        "detections": detections(conn, log),
        "sessions": sessions(conn, log, raw),
        "resolutions": resolutions(conn, log),
        "grades": grades(conn, log, include_text=include_text),
        "calls": calls(log),
        "materials": materials(log),
    }


def manifest(
    rows: dict[str, list[dict]], *, since: str | None, include_text: bool, config=None
) -> dict:
    """What was exported, from when, under which configuration. Never the key."""
    times = [e["occurred_at"] for e in rows["events"]]
    settings = None
    if config is not None:
        settings = {
            "model": config.model,
            "endpoint_host": urlparse(config.base_url).hostname,
            "local": config.local,
            "temperature": config.temperature,
        }
    return {
        "format": FORMAT,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unrot_version": __version__,
        "since": since,
        "include_text": include_text,
        "first_event_at": min(times) if times else None,
        "last_event_at": max(times) if times else None,
        "fixture_events": sum(1 for e in rows["events"] if e["fixture"]),
        "config": settings,
        "files": {f"{name}.jsonl": len(rows[name]) for name in FILES},
    }


def write(
    conn: sqlite3.Connection,
    out: Path | str,
    *,
    raw: sqlite3.Connection | None = None,
    since: str | None = None,
    include_text: bool = False,
    config=None,
) -> dict:
    """Write the bundle into `out`, which must not exist or be empty. Returns the manifest."""
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty; export into a new folder")
    out.mkdir(parents=True, exist_ok=True)

    rows = build(conn, raw=raw, since=since, include_text=include_text)
    for name in FILES:
        with open(out / f"{name}.jsonl", "w", encoding="utf-8") as handle:
            for row in rows[name]:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    meta = manifest(rows, since=since, include_text=include_text, config=config)
    (out / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (out / "README.md").write_text(README, encoding="utf-8")
    return meta
