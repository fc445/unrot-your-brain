"""The wire format.

Separate from `read.py`'s dataclasses on purpose: those are the read model and
may change freely; these are a contract with a frontend that ships separately
and may be older than the backend. Keeping them distinct means a rename inside
the read model is a rename, not a breaking API change nobody noticed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    """Is the core up, and is it the core this client expects?

    Polled every couple of seconds by the Mac app's supervisor, so it must stay
    cheap and side-effect-free -- in particular it must never recompile. A
    health check that re-folds the log on every poll is a health check that
    causes the load it is there to detect.

    It reports rather than judges. `raw_open` being false is a normal, shippable
    state (the portable layer alone), not a fault, and the client decides what
    either fact means.
    """

    ok: bool = True
    version: str
    #: `store.db.COMPILED_SCHEMA`. A client built against a different number is
    #: talking to a core whose compiled tables have a different shape.
    compiled_schema: int
    #: Whether `raw/raw.db` opened. False means the moment view cannot resolve
    #: anything on this machine -- worth knowing before a user clicks one.
    raw_open: bool
    store_path: str


class EncounterOut(BaseModel):
    encounter_id: str
    #: 'transcript' | 'manual'. The surface must treat these identically -- a gap
    #: you typed in yourself is as real as one we found. The field exists for
    #: provenance, not for ranking.
    source: str
    paraphrase: str | None = None
    judgment: str | None = None
    judged_at: str | None = None
    occurred_at: str
    detector_version: str | None = None
    session_id: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    resolvable: bool = False
    #: The repo the session ran in -- provenance for the card, read locally.
    repo: str | None = None


class ExplanationOut(BaseModel):
    explanation_id: str
    raw_text: str
    #: The question this answer was given to, stored verbatim. Shown back with
    #: the answer because the same words mean different things under different
    #: questions -- and the wording is expected to change over time.
    prompt_text: str
    prompt_version: str
    submitted_at: str
    #: 'isolated' | 'listed' | 'causal', or None while ungraded. Ungraded is a
    #: normal state: the answer is stored before any grading is attempted, so a
    #: failed or unavailable grader never costs the user what they wrote.
    level: str | None = None
    reasoning: str | None = None
    #: Only a classifier supplies these, and an absent distribution means "not
    #: measured" rather than "flat" -- so it stays absent rather than being
    #: filled in with zeroes.
    probabilities: dict[str, float] | None = None
    confidence: float | None = None
    grader_version: str | None = None


class MaterialOut(BaseModel):
    material_id: str
    #: 'textual_with_sources' | 'sources_only'. The second is a shipped format,
    #: not a degraded one -- it generates nothing and so can invent nothing.
    format: str
    body: str | None = None
    sources: list[dict] = Field(default_factory=list)
    generated_at: str
    delivered_at: str | None = None


class ConceptOut(BaseModel):
    concept_id: str
    name: str
    gap_type: str
    state: str
    bucket: str
    aliases: list[str] = Field(default_factory=list)
    encounter_count: int
    unjudged: int
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    latest_level: str | None = None
    encounters: list[EncounterOut] = Field(default_factory=list)
    explanations: list[ExplanationOut] = Field(default_factory=list)
    material: list[MaterialOut] = Field(default_factory=list)


class CaptureOut(BaseModel):
    sessions: int
    human_turns: int
    last_activity: str | None = None
    sessions_analysed: int
    sessions_clean: int
    sessions_analysable: int = 0
    sessions_waiting: int = 0
    sessions_grown: int = 0


class SurfaceOut(BaseModel):
    #: 'gaps' | 'clean' | 'cold_start' | 'not_captured' | 'not_analysed'.
    #: 'failed' is never sent -- the frontend derives it from a request that did
    #: not return, because a response saying "I failed" is a response.
    state: str
    headline: str
    detail: str
    capture: CaptureOut
    counts: dict[str, int]
    #: How many events in the log are development fixtures. Non-zero means some
    #: of what is on screen is fake, and the surface says so rather than letting
    #: seeded data quietly read as a finding about the user.
    fixtures: int
    concepts: list[ConceptOut] = Field(default_factory=list)


class JudgmentOut(BaseModel):
    encounter_id: str
    #: The concept after recompiling, so the frontend re-renders from the store's
    #: answer rather than from its own guess at what the judgment did.
    concept: ConceptOut | None = None
    surface: str
    counts: dict[str, int]


class MomentTurn(BaseModel):
    line_no: int
    role: str
    text: str
    is_meta: bool = False
    is_sidechain: bool = False


class MomentOut(BaseModel):
    encounter_id: str
    resolvable: bool
    reason: str | None = None
    session_id: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    turns: list[MomentTurn] = Field(default_factory=list)


class CheckOut(BaseModel):
    """The question to put on screen."""

    concept_id: str
    name: str
    prompt_text: str
    prompt_version: str


class GradedOut(BaseModel):
    explanation: ExplanationOut
    #: The concept after recompiling. A `causal` grade compiles the concept to
    #: `known`, so this can arrive in a different bucket than it left.
    concept: ConceptOut | None = None
    counts: dict[str, int]
    #: False when the answer was stored but no grader was available. The
    #: distinction matters: the user must not be told they failed a check that
    #: was never actually run.
    graded: bool = True


class MadeOut(BaseModel):
    material: MaterialOut
    concept: ConceptOut | None = None
    counts: dict[str, int]


class SubmittedOut(BaseModel):
    """What the resolver did with a capture, and the handle for arguing with it.

    `judgment_event_id` is the point. The resolver's "this looks like X" is
    stored as its own event precisely so it can be corrected, so the client is
    handed the address of that event rather than just its conclusion.
    """

    encounter_id: str
    concept_id: str
    canonical_name: str
    #: 'new' | 'existing' | 'alias'
    decision: str
    reasoning: str
    judgment_event_id: str
    #: True when a string match settled it, or no model was configured.
    decided_without_model: bool
    #: Set only when a model should have been asked and could not be. The
    #: capture is kept regardless -- filed as new -- and this says so, rather
    #: than letting a near-duplicate nobody checked for look like a considered
    #: "this is new".
    model_unavailable: str | None = None
    concept: ConceptOut | None = None
    counts: dict[str, int]


class CorrectedOut(BaseModel):
    correction_event_id: str
    #: Where the encounter lives now, when the correction moved it.
    concept: ConceptOut | None = None
    counts: dict[str, int]


class PendingOut(BaseModel):
    session_id: str
    last_activity: str | None = None
    human_turns: int
    #: 'never' | 'grown'
    reason: str
    analysed_at: str | None = None
    #: The repo the session was working in, as the transcript records it.
    cwd: str | None = None


class SpendTotalOut(BaseModel):
    """A sum of model calls. `text` is how every surface says it (PR-31)."""

    calls: int = 0
    failed: int = 0
    #: USD, summed over the calls that reported a price.
    cost: float = 0.0
    priced: int = 0
    #: Calls to a local endpoint. No bill, and never shown as $0.00.
    local: int = 0
    #: Hosted calls that reported no price: unknown, not free.
    unpriced: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    local_only: bool = False
    #: "$0.14", "local, no cost", "$0.14 + 2 unpriced".
    text: str


class SpendPartOut(BaseModel):
    #: 'detection' | 'resolution' | 'grading' | 'material'
    purpose: str
    #: "finding gaps", "filing", "grading", "material".
    label: str
    total: SpendTotalOut


class SpendDayOut(BaseModel):
    #: A UTC day, YYYY-MM-DD.
    day: str
    total: SpendTotalOut


class PerSessionOut(BaseModel):
    """What examining one session has cost with one model."""

    model: str
    sessions: int
    cost: float
    local: bool
    average: float | None = None
    text: str


class EstimateOut(BaseModel):
    """Roughly what analysing `sessions` would cost, from recent sessions on `model`."""

    model: str
    sessions: int
    #: How many recent sessions the average came from. Zero: no estimate.
    based_on: int
    per_session: float | None = None
    local: bool
    cost: float | None = None
    #: "about $0.12", "local, no cost", or null when there is nothing to go on.
    text: str | None = None


class SpendOut(BaseModel):
    """What analysis has cost, read off the log. The same numbers as `store metrics`."""

    #: The model and endpoint in effect now, which the estimate is for.
    model: str
    local: bool
    week: SpendTotalOut
    week_by_purpose: list[SpendPartOut] = Field(default_factory=list)
    all_time: SpendTotalOut
    all_time_by_purpose: list[SpendPartOut] = Field(default_factory=list)
    #: The last fourteen UTC days that had any calls.
    by_day: list[SpendDayOut] = Field(default_factory=list)
    #: Cost per examined session, per model, since install.
    per_session: list[PerSessionOut] = Field(default_factory=list)
    #: Set when `?since=` was given: spend from then until now. The running
    #: total for a batch of analysis, failed calls included.
    window: SpendTotalOut | None = None


class QueueOut(BaseModel):
    """Captured sessions waiting to be analysed. Derived, so it survives anything."""

    pending: list[PendingOut] = Field(default_factory=list)
    #: Whether a model is configured. Analysis needs one; capture never does.
    can_analyse: bool
    #: Sessions being analysed right now.
    analysing: list[str] = Field(default_factory=list)
    #: Roughly what analysing everything pending would cost. Null when nothing
    #: is pending or no model is configured.
    estimate: EstimateOut | None = None
    #: The last seven days' spend, for the queue bar and the tray.
    spent_this_week: SpendTotalOut | None = None


class CapturedOut(BaseModel):
    session_id: str
    #: 'new' | 'appended' | 'unchanged' | 'rewritten' | 'empty' | 'error: ...'
    status: str
    turns_added: int = 0


class CaptureResultOut(BaseModel):
    captured: list[CapturedOut] = Field(default_factory=list)
    pending: int


class AnalysedOut(BaseModel):
    session_id: str
    clean: bool
    #: What each candidate was filed under, in order.
    filed: list[str] = Field(default_factory=list)
    windows_examined: int
    detector_version: str
    counts: dict[str, int]


class ExportFileOut(BaseModel):
    name: str
    rows: int


class ExportPreviewOut(BaseModel):
    """What an export would contain, before anything is written (PR-32)."""

    include_text: bool
    #: Suggested file name for the `.zip`; it unpacks to a folder of the same stem.
    filename: str
    files: list[ExportFileOut] = Field(default_factory=list)
    fixture_events: int = 0
    first_event_at: str | None = None
    last_event_at: str | None = None
    #: The core's own wording, so the app cannot describe the bundle differently
    #: from what the code puts in it.
    included: list[str] = Field(default_factory=list)
    withheld: list[str] = Field(default_factory=list)
    never: list[str] = Field(default_factory=list)


class ConfigOut(BaseModel):
    """What the core will actually use, as it sees it. The key is never echoed."""

    model: str
    base_url: str
    key_set: bool
    #: True when every model call goes to this machine.
    local: bool
    #: 'classifier' | 'keyword'
    grader: str
    detector_version: str
    #: OpenRouter `reasoning.effort` sent with each call; None when nothing is
    #: sent (a local server, or the model left to decide).
    reasoning_effort: str | None = None
    #: What each kind of model call sends off this machine, stated by the code
    #: that sends it. Empty when the endpoint is local.
    leaves_this_mac: list[str] = Field(default_factory=list)


class RegenPlanOut(BaseModel):
    detector_version: str
    captured: int
    already_done: int
    to_run: list[str] = Field(default_factory=list)
    protected: int
    can_run: bool


class RegenPassOut(BaseModel):
    session_id: str
    #: Encounters left strictly alone because the user had judged them.
    protected: int
    removed: int
    recorded: int
    skipped: bool
    detector_version: str


class DevWipePlanOut(BaseModel):
    """What a wipe would do, shown before the confirmation dialog. Calls no model."""

    #: Every captured session -- after a wipe, `already_done` is empty, so this
    #: many sessions is exactly what `regenerate()` will re-run.
    sessions_to_rerun: int
    #: Encounters that carry a judgment. Kept unless `discard_user_input` is set.
    protected_encounters: int
    #: Encounters submitted by hand. Kept unless `discard_user_input` is set.
    manual_encounters: int
    #: Explanations on file. Kept, and their grades with them, unless
    #: `discard_user_input` is set.
    explanations: int
    can_run: bool


class DevWipeOut(BaseModel):
    """What one wipe actually did, and where the safety copy went."""

    backup_path: str
    encounters_removed: int
    other_events_removed: int
    user_input_discarded: bool
    #: Sessions the caller should now hand to `POST /api/regen`, one at a time
    #: (or drive through the existing `Regenerator`) to finish the rerun.
    sessions_to_rerun: int
