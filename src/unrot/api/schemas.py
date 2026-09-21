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


class CaptureOut(BaseModel):
    sessions: int
    human_turns: int
    last_activity: str | None = None
    sessions_with_flags: int


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
