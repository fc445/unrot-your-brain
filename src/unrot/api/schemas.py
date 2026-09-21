"""The wire format.

Separate from `read.py`'s dataclasses on purpose: those are the read model and
may change freely; these are a contract with a frontend that ships separately
and may be older than the backend. Keeping them distinct means a rename inside
the read model is a rename, not a breaking API change nobody noticed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
