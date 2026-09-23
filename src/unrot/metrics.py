"""The leading metrics for a week of self-use, read straight off the event log (PR-30).

PR-28's go/no-go asks for a handful of numbers recorded "even roughly". Every
one of them that can be counted is already in the log, because judgments,
explanations and analyses are all events. This reads them; it writes nothing.

Three rules shape it:

* **Fixtures never count.** Every seeded event is stamped `origin='fixture'`,
  and the report says how many it left out rather than quietly mixing them in.
* **A rate always shows its numerator and denominator.** A week of self-use is
  small. "3 of 4" and "75%" are the same fact, and only one of them reads as
  small.
* **What it cannot measure, it says.** Inverted trust and the shame spiral are
  in PR-28's list and are not countable from the log; the report names them
  rather than implying it covered everything.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import spend
from .store.fixtures import FIXTURE_ORIGIN

#: Below this many in a denominator, a percentage is noise dressed as a finding.
SMALL = 5

NOT_MEASURED = [
    "Inverted trust -- whether 'unrot will catch it' is licensing more blind"
    " acceptance. Look for yourself: are you asking the agent fewer clarifying"
    " questions than before the week started?",
    "Shame spiral -- whether the list reads as a ledger of failures. Only you"
    " can say; note it alongside these numbers.",
]


@dataclass
class Ratio:
    part: int
    whole: int

    @property
    def rate(self) -> float | None:
        return self.part / self.whole if self.whole else None

    def __str__(self) -> str:
        if not self.whole:
            return "none to count"
        text = f"{self.part} of {self.whole} ({self.rate:.0%})"
        return text + ("  -- too few to read much into" if self.whole < SMALL else "")


@dataclass
class Report:
    since: str
    until: str
    #: Events left out because they are development fixtures.
    fixtures_excluded: int

    flags: int = 0
    detected: int = 0
    manual: int = 0
    engaged: Ratio = field(default_factory=lambda: Ratio(0, 0))
    ignored: int = 0
    manual_share: Ratio = field(default_factory=lambda: Ratio(0, 0))
    #: Among detected flags that have a judgment: how many were dismissed.
    dismissal_rate: Ratio = field(default_factory=lambda: Ratio(0, 0))

    confirmed: int = 0
    dismissed: int = 0
    undone: int = 0
    checks_answered: int = 0
    check_levels: dict[str, int] = field(default_factory=dict)
    material_made: int = 0
    sessions_analysed: int = 0
    sessions_clean: int = 0
    #: What model calls cost in the window (PR-31). The same function the app's
    #: /api/spend reads, so the two cannot disagree.
    spend: spend.Summary | None = None

    not_measured: list[str] = field(default_factory=lambda: list(NOT_MEASURED))

    def as_json(self) -> str:
        """For pasting into decisions.md, or comparing one week with the next."""

        def plain(value):
            if isinstance(value, Ratio):
                return {"part": value.part, "whole": value.whole, "rate": value.rate}
            if isinstance(value, spend.Summary):
                return value.as_dict()
            return value

        return json.dumps({k: plain(getattr(self, k)) for k in self.__dataclass_fields__}, indent=2)


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def compute(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    until: datetime | None = None,
) -> Report:
    """The report for flags *first raised* in [since, until).

    A flag counts in the window it first appeared in, and its engagement is read
    as it stands now -- so a flag raised on day six and answered on day nine
    counts as engaged when the report is run on day nine. That is the question
    PR-28 asks: of what surfaced this week, how much did you deal with.
    """
    until = until or datetime.now(timezone.utc)

    def inside(text: str | None) -> bool:
        moment = _when(text)
        return moment is not None and since <= moment < until

    rows = conn.execute(
        "SELECT event_type, origin, occurred_at, payload FROM events ORDER BY event_id"
    ).fetchall()
    real = [r for r in rows if r["origin"] != FIXTURE_ORIGIN]
    report = Report(
        since=since.isoformat(timespec="seconds"),
        until=until.isoformat(timespec="seconds"),
        fixtures_excluded=len(rows) - len(real),
    )

    # When each encounter was first recorded. Regeneration and splits re-record
    # an encounter under the same id; its first appearance is when it surfaced.
    first_seen: dict[str, str] = {}
    explained_concepts: set[str] = set()
    for row in real:
        payload = json.loads(row["payload"])
        kind = row["event_type"]
        if kind == "encounter_recorded":
            first_seen.setdefault(payload["encounter_id"], row["occurred_at"])
        elif kind == "explanation_submitted":
            explained_concepts.add(payload["concept_id"])

        if not inside(row["occurred_at"]):
            continue
        if kind == "encounter_confirmed":
            report.confirmed += 1
        elif kind == "encounter_dismissed":
            report.dismissed += 1
        elif kind == "encounter_judgment_retracted":
            report.undone += 1
        elif kind == "explanation_submitted":
            report.checks_answered += 1
        elif kind == "material_generated":
            report.material_made += 1

    # Levels, as graded: the latest grade for each explanation made in the window.
    in_window_explanations = {
        r["event_id"]
        for r in conn.execute(
            "SELECT event_id, occurred_at, origin FROM events WHERE event_type = 'explanation_submitted'"
        )
        if r["origin"] != FIXTURE_ORIGIN and inside(r["occurred_at"])
    }
    for row in conn.execute(
        "SELECT explanation_id, level FROM compiled_explanations WHERE level IS NOT NULL"
    ):
        if row["explanation_id"] in in_window_explanations:
            report.check_levels[row["level"]] = report.check_levels.get(row["level"], 0) + 1

    # Sessions examined in the window, and how many were clean at their latest look.
    latest: dict[str, int] = {}
    for row in real:
        if row["event_type"] == "session_analysed" and inside(row["occurred_at"]):
            payload = json.loads(row["payload"])
            latest[payload["session_id"]] = payload["candidates_found"]
    report.sessions_analysed = len(latest)
    report.sessions_clean = sum(1 for found in latest.values() if found == 0)

    # The flags themselves, as they stand now.
    surfaced = {eid for eid, at in first_seen.items() if inside(at)}
    engaged = judged_detected = dismissed_detected = 0
    for row in conn.execute(
        "SELECT encounter_id, concept_id, source, judgment FROM compiled_encounters"
    ):
        if row["encounter_id"] not in surfaced:
            continue
        report.flags += 1
        if row["source"] == "manual":
            report.manual += 1
        else:
            report.detected += 1
            if row["judgment"]:
                judged_detected += 1
                dismissed_detected += row["judgment"] == "dismissed"
        if row["judgment"] or row["concept_id"] in explained_concepts:
            engaged += 1

    report.spend = spend.summarise(conn, since=since, until=until)

    report.engaged = Ratio(engaged, report.flags)
    report.ignored = report.flags - engaged
    report.manual_share = Ratio(report.manual, report.flags)
    report.dismissal_rate = Ratio(dismissed_detected, judged_detected)
    return report


def window(days: int | None = None, since: str | None = None) -> datetime:
    if since:
        moment = _when(since)
        if moment is None:
            raise ValueError(f"could not read {since!r} as a date")
        return moment
    return datetime.now(timezone.utc) - timedelta(days=days or 7)


def render(report: Report) -> str:
    lines = [
        f"unrot, {report.since[:10]} to {report.until[:10]}",
        "",
        f"  flags raised           {report.flags}  ({report.detected} detected, {report.manual} typed in)",
        f"  engaged with           {report.engaged}",
        f"  ignored so far         {report.ignored}",
        f"  typed in, not detected {report.manual_share}",
        f"  detected, dismissed    {report.dismissal_rate}",
        "",
        f"  confirmed {report.confirmed} · dismissed {report.dismissed} · undone {report.undone}",
        f"  checks answered {report.checks_answered}"
        + (
            " · " + ", ".join(f"{level} {n}" for level, n in sorted(report.check_levels.items()))
            if report.check_levels
            else ""
        ),
        f"  material made {report.material_made}",
        f"  sessions analysed {report.sessions_analysed}, {report.sessions_clean} of them clean",
        "",
        *(spend.render(report.spend) if report.spend else []),
        "",
        "  How to read it",
        "    engaged with            -- confirmed, dismissed, or explained. PR-28's first metric.",
        "    typed in, not detected  -- is journey 10 a nice-to-have, or the main input?",
        "    detected, dismissed     -- the in-the-wild proxy for detector precision.",
        "",
        "  Not measured here",
        *[f"    - {line}" for line in report.not_measured],
    ]
    if report.fixtures_excluded:
        lines += ["", f"  ({report.fixtures_excluded} fixture events left out of every figure.)"]
    return "\n".join(lines)
