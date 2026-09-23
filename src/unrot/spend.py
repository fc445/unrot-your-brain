"""What analysis has cost, read off the event log (PR-31).

Analysis is the one thing unrot does that costs money. The spend rule says
*when* it is spent -- capture is free, and a model is only called on "Examine
them now" or automatic analysis the user switched on -- and this says *how
much*.

OpenRouter already returns the cost of every call in `usage`. This keeps it:
each model call becomes a `model_called` event, so the total survives a
relaunch and the CLI and the app read the same numbers from the same place.

Four rules shape it:

* **Failed calls count.** A call that runs to the length limit is billed and
  produces nothing. Leaving it out would under-report exactly the case that
  matters, so a call is recorded whether it returned or raised.
* **Local is not free, it is no cost.** A local endpoint has no bill, and
  "$0.00" would read as a price that happened to round down. The summary says
  `local` instead, and the surfaces say "local, no cost".
* **Unknown is not zero.** A hosted endpoint that reports no cost, or a call
  that died before any response, is counted as unpriced rather than as free.
* **Money spent is never regenerated away.** `model_called` is machine output,
  so its actor is `system`, but nothing that replays derived state deletes it:
  a re-run makes new calls and those are new money, not a replacement.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

#: What the money was spent on, as the event records it and as a person reads it.
PURPOSES: dict[str, str] = {
    "detection": "finding gaps",
    "resolution": "filing",
    "grading": "grading",
    "material": "material",
}

#: The two purposes a session's analysis spends on. Cost per examined session
#: is these, divided by the sessions they were spent on.
ANALYSIS = ("detection", "resolution")

#: How many recent sessions an estimate is averaged over.
RECENT = 20


# ---------------------------------------------------------------------------
# One call
# ---------------------------------------------------------------------------


@dataclass
class Call:
    """One model call, as it will be written to the log."""

    purpose: str
    model: str
    ok: bool = True
    local: bool = False
    #: USD. None when nothing reported a price -- which is not the same as zero.
    cost: float | None = None
    prompt_cost: float | None = None
    completion_cost: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    finish_reason: str | None = None
    error: str | None = None
    #: Which session or concept the call was about, filled from the meter.
    tags: dict = field(default_factory=dict)

    def payload(self) -> dict:
        data = {k: v for k, v in asdict(self).items() if k != "tags" and v is not None}
        data.update({k: v for k, v in self.tags.items() if v is not None})
        return data


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tokens(value) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def from_usage(
    purpose: str,
    model: str,
    usage,
    *,
    local: bool,
    ok: bool = True,
    error: str | None = None,
    finish_reason: str | None = None,
) -> Call:
    """A `Call` from whatever `usage` the endpoint sent, including none at all.

    Defensive by necessity: OpenRouter's shape, a plain OpenAI-compatible
    server's shape and a missing response all arrive here. A field that is not
    there is left as None rather than guessed at.
    """
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    usage = usage if isinstance(usage, dict) else {}

    details = usage.get("completion_tokens_details") or {}
    costs = usage.get("cost_details") or {}

    cost = _number(usage.get("cost"))
    # Bring-your-own-key: OpenRouter's `cost` is only its fee, and what the
    # provider charged the user's own key is reported beside it.
    if usage.get("is_byok") and _number(costs.get("upstream_inference_cost")) is not None:
        cost = (cost or 0.0) + _number(costs.get("upstream_inference_cost"))

    return Call(
        purpose=purpose,
        model=model,
        ok=ok,
        local=local,
        cost=None if local else cost,
        prompt_cost=None if local else _number(costs.get("upstream_inference_prompt_cost")),
        completion_cost=None
        if local
        else _number(costs.get("upstream_inference_completions_cost")),
        prompt_tokens=_tokens(usage.get("prompt_tokens", usage.get("input_tokens"))),
        completion_tokens=_tokens(
            usage.get("completion_tokens", usage.get("output_tokens"))
        ),
        reasoning_tokens=_tokens(
            details.get("reasoning_tokens") if isinstance(details, dict) else None
        ),
        finish_reason=finish_reason,
        error=error,
    )


# ---------------------------------------------------------------------------
# The meter: collects calls, writes them to the log
# ---------------------------------------------------------------------------


class Meter:
    """Collects the calls one piece of work makes, then writes them down.

    Built before the store is open and handed to the model builders, which is
    why it buffers: the calls it hears about are appended when `flush` is given
    a connection. Callers flush in a `finally`, so a run that fails still
    records what it spent -- that is the case this exists for.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._pending: list[Call] = []
        self._tags: dict = {}

    @contextmanager
    def about(self, **tags) -> Iterator[None]:
        """Attribute the calls made inside to a session or a concept."""
        before = self._tags
        self._tags = {**before, **tags}
        try:
            yield
        finally:
            self._tags = before

    def record(self, call: Call) -> None:
        call.tags = {**self._tags, **call.tags}
        self.calls.append(call)
        self._pending.append(call)

    def flush(self, conn: sqlite3.Connection) -> int:
        """Append every call not yet written, and commit. Returns how many."""
        from .store import append

        written = 0
        while self._pending:
            call = self._pending.pop(0)
            append(conn, "model_called", call.payload())
            written += 1
        if written:
            conn.commit()
        return written

    def total(self) -> "Total":
        return Total.of(self.calls)


def tap(meter: Meter | None, purpose: str, config) -> list:
    """Callbacks for a langchain client that report each call to `meter`.

    A callback rather than a wrapper round `invoke`, because the one call this
    most needs to see -- the one that hit the length limit -- never returns: it
    raises from inside the client, and the usage is on the exception.
    """
    if meter is None:
        return []
    from langchain_core.callbacks import BaseCallbackHandler

    local = config.local

    class _Tap(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs) -> None:
            output = response.llm_output or {}
            finish = None
            if response.generations and response.generations[0]:
                finish = (response.generations[0][0].generation_info or {}).get(
                    "finish_reason"
                )
            meter.record(
                from_usage(
                    purpose,
                    config.model,
                    output.get("token_usage"),
                    local=local,
                    finish_reason=finish,
                )
            )

        def on_llm_error(self, error, **kwargs) -> None:
            # `LengthFinishReasonError` carries the completion it could not
            # parse, and the completion carries what it cost.
            completion = getattr(error, "completion", None)
            finish = None
            choices = getattr(completion, "choices", None) or []
            if choices:
                finish = getattr(choices[0], "finish_reason", None)
            meter.record(
                from_usage(
                    purpose,
                    config.model,
                    getattr(completion, "usage", None),
                    local=local,
                    ok=False,
                    error=type(error).__name__,
                    finish_reason=finish,
                )
            )

    return [_Tap()]


def record_http(
    meter: Meter | None,
    purpose: str,
    config,
    *,
    payload: dict | None = None,
    error: BaseException | None = None,
    model: str | None = None,
) -> None:
    """The same, for the calls that are plain HTTP rather than langchain."""
    if meter is None:
        return
    payload = payload or {}
    choice = (payload.get("choices") or [{}])[0]
    meter.record(
        from_usage(
            purpose,
            model or config.model,
            payload.get("usage"),
            local=config.local,
            ok=error is None,
            error=type(error).__name__ if error is not None else None,
            finish_reason=choice.get("finish_reason") if isinstance(choice, dict) else None,
        )
    )


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


@dataclass
class Total:
    """A sum of calls, honest about what it could not price."""

    calls: int = 0
    failed: int = 0
    #: Sum of the calls that reported a price.
    cost: float = 0.0
    priced: int = 0
    #: Calls to a local endpoint: no bill, and never shown as $0.00.
    local: int = 0
    #: Hosted calls with no price reported. Not free -- unknown.
    unpriced: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    @classmethod
    def of(cls, calls) -> "Total":
        total = cls()
        for call in calls:
            total.add(call if isinstance(call, dict) else call.payload())
        return total

    def add(self, payload: dict) -> None:
        self.calls += 1
        self.failed += 0 if payload.get("ok", True) else 1
        self.prompt_tokens += payload.get("prompt_tokens") or 0
        self.completion_tokens += payload.get("completion_tokens") or 0
        self.reasoning_tokens += payload.get("reasoning_tokens") or 0
        cost = _number(payload.get("cost"))
        if payload.get("local"):
            self.local += 1
        elif cost is None:
            self.unpriced += 1
        else:
            self.priced += 1
            self.cost += cost

    @property
    def local_only(self) -> bool:
        return self.calls > 0 and self.local == self.calls

    @property
    def text(self) -> str:
        """How a surface says it. Decided here so every surface says it the same way."""
        if self.local_only:
            return "local, no cost"
        if not self.priced and self.unpriced:
            return "not reported"
        text = money(self.cost)
        if self.unpriced:
            text += f" + {self.unpriced} unpriced"
        return text

    def as_dict(self) -> dict:
        return asdict(self) | {"local_only": self.local_only, "text": self.text}


def money(amount: float | None) -> str:
    """Dollars, to the precision the amount needs.

    Two places for anything a person would call money; four significant places
    for the fractions of a cent a single session costs, where "$0.00" would
    throw away the one number that separates a cheap model from a dear one.
    """
    if amount is None:
        return "not reported"
    if amount == 0:
        return "$0.00"
    if amount < 0.0001:
        # A classifier grade costs about this. "$0.0000" would read as free.
        return "<$0.0001"
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def calls(
    conn: sqlite3.Connection,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    include_fixtures: bool = False,
) -> list[dict]:
    """Every recorded call in [since, until), oldest first, with `occurred_at` added."""
    from .store.fixtures import FIXTURE_ORIGIN

    out = []
    for row in conn.execute(
        "SELECT occurred_at, origin, payload FROM events"
        " WHERE event_type = 'model_called' ORDER BY event_id"
    ):
        if row["origin"] == FIXTURE_ORIGIN and not include_fixtures:
            continue
        moment = _when(row["occurred_at"])
        if since is not None and (moment is None or moment < since):
            continue
        if until is not None and (moment is None or moment >= until):
            continue
        out.append(json.loads(row["payload"]) | {"occurred_at": row["occurred_at"]})
    return out


@dataclass
class PerSession:
    """What examining one session costs, for one model. The number for choosing one."""

    model: str
    sessions: int
    cost: float
    local: bool

    @property
    def average(self) -> float | None:
        return None if self.local or not self.sessions else self.cost / self.sessions

    @property
    def text(self) -> str:
        return "local, no cost" if self.local else money(self.average)

    def as_dict(self) -> dict:
        return asdict(self) | {"average": self.average, "text": self.text}


def per_session(found: list[dict]) -> list[PerSession]:
    """Analysis spend divided by the sessions it was spent on, per model.

    Only priced calls with a session go into the sum; a session all of whose
    calls were unpriced would drag the average towards zero, so it is left out.
    """
    by_model: dict[str, dict[str, float]] = {}
    local_models: set[str] = set()
    for call in found:
        session = call.get("session_id")
        if call.get("purpose") not in ANALYSIS or not session:
            continue
        model = call.get("model") or "unknown"
        if call.get("local"):
            local_models.add(model)
            by_model.setdefault(model, {}).setdefault(session, 0.0)
            continue
        cost = _number(call.get("cost"))
        if cost is None:
            continue
        sessions = by_model.setdefault(model, {})
        sessions[session] = sessions.get(session, 0.0) + cost

    return [
        PerSession(
            model=model,
            sessions=len(sessions),
            cost=sum(sessions.values()),
            local=model in local_models,
        )
        for model, sessions in sorted(by_model.items())
    ]


@dataclass
class Estimate:
    """Roughly what analysing `sessions` more would cost, and what that is based on."""

    model: str
    sessions: int
    #: How many recent sessions the average came from. Zero means no estimate.
    based_on: int
    per_session: float | None
    local: bool

    @property
    def cost(self) -> float | None:
        if self.local or self.per_session is None:
            return None
        return self.per_session * self.sessions

    @property
    def text(self) -> str | None:
        if self.local:
            return "local, no cost"
        if self.cost is None:
            return None
        return f"about {money(self.cost)}"

    def as_dict(self) -> dict:
        return asdict(self) | {"cost": self.cost, "text": self.text}


def estimate(
    conn: sqlite3.Connection, *, model: str, sessions: int, local: bool, recent: int = RECENT
) -> Estimate:
    """What `sessions` more would cost, from the last `recent` sessions on `model`.

    An average per session, not per window: rough on purpose, because the
    question it answers is "is this pennies or pounds", and a session's length
    is not known until it has been read. Nothing to go on gives no estimate
    rather than a guess.
    """
    if local:
        return Estimate(model, sessions, 0, None, local=True)

    by_session: dict[str, float] = {}
    order: list[str] = []
    for call in calls(conn):
        session = call.get("session_id")
        cost = _number(call.get("cost"))
        if (
            call.get("model") != model
            or call.get("purpose") not in ANALYSIS
            or not session
            or cost is None
            or call.get("local")
        ):
            continue
        if session in by_session:
            order.remove(session)
        order.append(session)
        by_session[session] = by_session.get(session, 0.0) + cost

    chosen = order[-recent:]
    if not chosen:
        return Estimate(model, sessions, 0, None, local=False)
    average = sum(by_session[s] for s in chosen) / len(chosen)
    return Estimate(model, sessions, len(chosen), average, local=False)


@dataclass
class Summary:
    """Spend over a window: the total, what it was spent on, and by day."""

    since: str | None
    total: Total
    by_purpose: dict[str, Total]
    by_day: dict[str, Total]
    per_session: list[PerSession]

    def as_dict(self) -> dict:
        return {
            "since": self.since,
            "total": self.total.as_dict(),
            "by_purpose": {k: v.as_dict() for k, v in self.by_purpose.items()},
            "by_day": {k: v.as_dict() for k, v in self.by_day.items()},
            "per_session": [p.as_dict() for p in self.per_session],
        }


def summarise(
    conn: sqlite3.Connection,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Summary:
    """Spend in [since, until), or since install when `since` is None.

    Days are UTC days, the same clock every event is stamped with.
    """
    found = calls(conn, since=since, until=until)
    by_purpose: dict[str, Total] = {}
    by_day: dict[str, Total] = {}
    for call in found:
        by_purpose.setdefault(call.get("purpose") or "other", Total()).add(call)
        by_day.setdefault(call["occurred_at"][:10], Total()).add(call)
    return Summary(
        since=since.isoformat(timespec="seconds") if since else None,
        total=Total.of(found),
        by_purpose={p: by_purpose[p] for p in [*PURPOSES, *by_purpose] if p in by_purpose},
        by_day=dict(sorted(by_day.items())),
        per_session=per_session(found),
    )


def week_ago(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=7)


def render(summary: Summary, *, heading: str = "analysis cost") -> list[str]:
    """Lines for the CLI, in the same words the app uses."""
    total = summary.total
    if not total.calls:
        return [f"  {heading:<22} nothing spent -- no model calls in this window"]
    lines = [
        f"  {heading:<22} {total.text} over {total.calls} call(s)"
        + (f", {total.failed} of them failed" if total.failed else "")
    ]
    parts = [
        f"{PURPOSES.get(purpose, purpose)} {part.text}"
        for purpose, part in summary.by_purpose.items()
    ]
    if parts:
        lines.append(f"  {'':<22} " + " · ".join(parts))
    for row in summary.per_session:
        lines.append(
            f"  {'per examined session':<22} {row.text} with {row.model}"
            f" (over {row.sessions} session{'s' if row.sessions != 1 else ''})"
        )
    if total.unpriced:
        lines.append(
            f"  {'':<22} {total.unpriced} call(s) reported no price and are not in the total"
        )
    return lines
