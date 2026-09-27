"""How the analysis pipeline performed, stage by stage, read off the event log.

The weekly report (`unrot.metrics`) asks PR-28's question: what surfaced, and
did you deal with it. This asks the pipeline's: of what the detector found, what
did each stage do with it, how long did it take, what did it cost, and was it
right. Every number comes from events the stages already write --
`detector_ran`, `familiarity_judged`, `model_called`, and your judgments -- so
it reads the log and writes nothing, and it can be recomputed for any window.

Two numbers here exist nowhere else, and they are the reason for it:

* **Hold-back precision, from spot checks.** A held-back candidate is never
  shown, so nothing about it can be judged -- except the one-in-five that triage
  surfaces anyway to ask. "I knew it" on one of those is triage having been
  right to hold it back; "I didn't know this" is a real gap it would have hidden.
* **Whether triage's probability means anything.** Among candidates it let
  through and you then answered, the dismissal rate should climb with p(knows).
  If it does not, the number is noise and the cut built on it is too.

Same rules as the weekly report: fixtures never count, and a rate always
carries its numerator and denominator.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .metrics import Ratio, _when
from .spend import PURPOSES, money
from .store.fixtures import FIXTURE_ORIGIN

#: The pipeline's stages in graph order, by the purpose their model calls are
#: metered under.
STAGES = ("detection", "familiarity", "resolution")

#: p(knows) bands for the calibration check, as (label, low, high).
BANDS = (("under 0.1", 0.0, 0.1), ("0.1 to 0.3", 0.1, 0.3), ("0.3 and over", 0.3, 1.01))


@dataclass
class StageCost:
    calls: int = 0
    failed: int = 0
    #: USD over the calls that reported a price; `unpriced` counts the rest.
    cost: float = 0.0
    unpriced: int = 0
    #: Median wall-clock per call, milliseconds. None when nothing was timed.
    median_ms: float | None = None
    #: `cost` as a person reads it, worded by `spend.money` like every other
    #: amount, so a stage that cost fractions of a cent never reads as free.
    cost_text: str = "$0.00"


@dataclass
class PipelineReport:
    since: str
    until: str
    fixtures_excluded: int = 0

    # -- the funnel, from each session's latest detector run in the window ---
    sessions: int = 0
    detector_calls: int = 0
    found: int = 0
    #: Candidates with an observed acceptance: the only ones that can be gaps.
    gaps: int = 0

    # -- triage, from its own record of each candidate it judged --------------
    judged: int = 0
    passed: int = 0
    held_back: int = 0
    spot_checks: int = 0
    #: Gaps triage did not judge: no map yet, already in the graph, or a failed call.
    not_judged: int = 0

    # -- what was filed, and what you said about it ----------------------------
    filed: int = 0
    confirmed: int = 0
    dismissed: int = 0
    unanswered: int = 0
    #: Of filed gaps you answered (spot checks aside): how many were real.
    flag_precision: Ratio = field(default_factory=lambda: Ratio(0, 0))

    # -- the two numbers that exist nowhere else --------------------------------
    #: Of answered spot checks: how many you already knew -- triage right.
    hold_back_precision: Ratio = field(default_factory=lambda: Ratio(0, 0))
    #: Among passed candidates you answered, the dismissal rate per p(knows) band.
    calibration: dict[str, Ratio] = field(default_factory=dict)

    stages: dict[str, StageCost] = field(default_factory=dict)

    def as_dict(self) -> dict:
        def plain(value):
            if isinstance(value, Ratio):
                return {"part": value.part, "whole": value.whole, "rate": value.rate}
            if isinstance(value, StageCost):
                return asdict(value)
            if isinstance(value, dict):
                return {k: plain(v) for k, v in value.items()}
            return value

        return {k: plain(getattr(self, k)) for k in self.__dataclass_fields__}


def compute(
    conn: sqlite3.Connection, *, since: datetime, until: datetime | None = None
) -> PipelineReport:
    until = until or datetime.now(timezone.utc)

    def inside(text: str | None) -> bool:
        moment = _when(text)
        return moment is not None and since <= moment < until

    rows = conn.execute(
        "SELECT event_type, origin, occurred_at, payload FROM events"
        " WHERE event_type IN ('detector_ran', 'familiarity_judged', 'model_called')"
        " ORDER BY event_id"
    ).fetchall()
    real = [r for r in rows if r["origin"] != FIXTURE_ORIGIN]
    report = PipelineReport(
        since=since.isoformat(timespec="seconds"),
        until=until.isoformat(timespec="seconds"),
        fixtures_excluded=len(rows) - len(real),
    )

    runs: dict[str, dict] = {}          # session -> its latest detector run
    triaged: dict[str, dict] = {}       # encounter id -> latest familiarity record
    calls: dict[str, list[dict]] = {}   # purpose -> calls
    for row in real:
        if not inside(row["occurred_at"]):
            continue
        payload = json.loads(row["payload"])
        kind = row["event_type"]
        if kind == "detector_ran":
            # A regeneration adds a run beside the last; the latest is the one
            # whose output is on the surface now.
            runs[payload["session_id"]] = payload
        elif kind == "familiarity_judged":
            key = payload.get("encounter_id") or f"{payload.get('session_id')}:{payload['term']}"
            triaged[key] = payload
        elif kind == "model_called":
            calls.setdefault(payload.get("purpose") or "other", []).append(payload)

    verdicts = {row["encounter_id"]: row for row in conn.execute(
        "SELECT encounter_id, judgment, spot_check FROM compiled_encounters"
    )}

    filed_ids: list[str] = []
    for run in runs.values():
        report.sessions += 1
        report.detector_calls += run.get("calls_made") or 0
        candidates = run.get("candidates") or []
        report.found += len(candidates)
        report.gaps += sum(1 for c in candidates if c.get("signal") == "accepted")
        filed_ids += [c["encounter_id"] for c in candidates if c.get("emitted") and c.get("encounter_id")]

    for record in triaged.values():
        report.judged += 1
        verdict = record.get("verdict")
        report.passed += verdict == "passed"
        report.held_back += verdict == "held_back"
        report.spot_checks += verdict == "spot_check"
    report.not_judged = max(report.gaps - report.judged, 0)

    report.filed = len(filed_ids)
    real_gaps = answered_gaps = 0
    known_spots = answered_spots = 0
    for eid in filed_ids:
        row = verdicts.get(eid)
        judgment = row["judgment"] if row else None
        if judgment is None:
            report.unanswered += 1
            continue
        report.confirmed += judgment == "confirmed"
        report.dismissed += judgment == "dismissed"
        if row["spot_check"]:
            answered_spots += 1
            known_spots += judgment == "dismissed"
        else:
            answered_gaps += 1
            real_gaps += judgment == "confirmed"
    report.flag_precision = Ratio(real_gaps, answered_gaps)
    report.hold_back_precision = Ratio(known_spots, answered_spots)

    bands = {label: [0, 0] for label, _, _ in BANDS}
    for eid, record in triaged.items():
        row = verdicts.get(eid)
        if record.get("verdict") != "passed" or row is None or row["judgment"] is None:
            continue
        p = float(record.get("p_knows") or 0.0)
        for label, low, high in BANDS:
            if low <= p < high:
                bands[label][0] += row["judgment"] == "dismissed"
                bands[label][1] += 1
    report.calibration = {label: Ratio(*counts) for label, counts in bands.items()}

    for purpose in (*STAGES, *sorted(set(calls) - set(STAGES))):
        made = calls.get(purpose, [])
        if not made and purpose not in STAGES:
            continue
        timed = [c["duration_ms"] for c in made if c.get("duration_ms") is not None]
        priced = [c["cost"] for c in made if c.get("cost") is not None]
        total = round(sum(priced), 6)
        report.stages[purpose] = StageCost(
            calls=len(made),
            failed=sum(1 for c in made if not c.get("ok", True)),
            cost=total,
            cost_text=money(total),
            unpriced=sum(1 for c in made if c.get("cost") is None and not c.get("local")),
            median_ms=round(statistics.median(timed), 1) if timed else None,
        )
    return report


def render(report: PipelineReport) -> str:
    def seconds(ms):
        return "–" if ms is None else f"{ms / 1000:.1f}s"

    lines = [
        f"pipeline, {report.since[:10]} to {report.until[:10]}",
        "",
        f"  sessions examined      {report.sessions}  ({report.detector_calls} detector calls)",
        f"  candidates found       {report.found}, {report.gaps} of them waved through",
        f"  triage                 judged {report.judged}: passed {report.passed},"
        f" held back {report.held_back}, spot-checked {report.spot_checks}"
        f"  ({report.not_judged} not judged)",
        f"  filed                  {report.filed}: confirmed {report.confirmed},"
        f" dismissed {report.dismissed}, unanswered {report.unanswered}",
        "",
        f"  flags that were real   {report.flag_precision}",
        f"  hold-backs that were right (spot checks)  {report.hold_back_precision}",
        "",
        "  dismissed, by triage's p(knows) -- should climb down the list",
        *[f"    {label:14s} {ratio}" for label, ratio in report.calibration.items()],
        "",
        "  per stage             calls  failed   median     cost",
        *[
            f"    {PURPOSES.get(p, p):20s} {s.calls:5d}  {s.failed:6d}  {seconds(s.median_ms):>7s}"
            f"  {s.cost_text:>9s}" + (f" + {s.unpriced} unpriced" if s.unpriced else "")
            for p, s in report.stages.items()
        ],
        "",
        "  How to read it",
        "    flags that were real        -- confirmed of answered flags: detector + triage precision.",
        "    hold-backs that were right  -- the only measure of what triage hides. Under 90% over",
        "                                   10 answered, triage stops holding anything back.",
        "    dismissed by p(knows)       -- if this does not climb, the probability is noise.",
    ]
    if report.fixtures_excluded:
        lines += ["", f"  ({report.fixtures_excluded} fixture events left out of every figure.)"]
    return "\n".join(lines)
