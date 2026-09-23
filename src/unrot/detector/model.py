"""Building the detector's `propose` callable.

`ModelConfig` moved up to `unrot.model` when the resolver arrived and needed the
same configuration: both talk to an OpenAI-compatible endpoint, and neither
depends on the other. It is re-exported here so `from unrot.detector import
ModelConfig` keeps working -- the seam this module exists for is `build_proposer`,
not the config dataclass.
"""

from __future__ import annotations

import json

from ..model import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ModelConfig,
    ran_out_of_room,
    structured_client,
    truncated_output,
)

__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "ModelConfig", "build_proposer"]


def build_proposer(config: ModelConfig, *, meter=None):
    """Return `propose(prompt_text) -> list[dict]`.

    Imports pydantic lazily alongside the client so that importing the detector
    -- or testing its ranking and budgeting -- does not require langchain.
    """
    from pydantic import BaseModel, Field

    class ProposedCandidate(BaseModel):
        term: str = Field(description="The term or concept, named as it was in the session")
        paraphrase: str = Field(
            description=(
                "One or two sentences that stand alone: what was being decided and"
                " why this term carried weight. Readable without the transcript."
            )
        )
        assistant_line: int = Field(description="Line number of the assistant turn where it appeared")
        acceptance_line: int | None = Field(
            default=None, description="Line number of the human's next turn, if there was one"
        )
        signal: str = Field(description="accepted | questioned | unclear")
        importance: str = Field(description="central | supporting")

    class Proposal(BaseModel):
        candidates: list[ProposedCandidate] = Field(
            default_factory=list,
            description="Empty is a correct and common answer for a clean session.",
        )

    client = structured_client(config, Proposal, meter=meter, purpose="detection")

    def propose(prompt_text: str) -> list[dict]:
        try:
            result = client.invoke(prompt_text)
        except Exception as exc:
            # Out of room *while answering*: with reasoning off this model can
            # list candidates without stopping, well past the "at most 2" it was
            # asked for. The entries before the cut are complete and valid, and
            # the detector ranks and keeps only the strongest anyway.
            kept = []
            if ran_out_of_room(exc):
                for item in complete_candidates(truncated_output(exc)):
                    try:
                        kept.append(ProposedCandidate.model_validate(item).model_dump())
                    except ValueError:
                        continue
            if not kept:
                raise
            return kept
        return [c.model_dump() for c in result.candidates]

    return propose


def complete_candidates(text: str) -> list[dict]:
    """The whole entries at the front of a `{"candidates": [...` cut off mid-list."""
    start = text.find("[", text.find('"candidates"'))
    if '"candidates"' not in text or start < 0:
        return []
    decoder = json.JSONDecoder()
    kept: list[dict] = []
    at = start + 1
    while True:
        while at < len(text) and text[at] in " \t\r\n,":
            at += 1
        if at >= len(text) or text[at] != "{":
            return kept
        try:
            item, at = decoder.raw_decode(text, at)
        except json.JSONDecodeError:
            return kept  # the one the ceiling cut through
        if isinstance(item, dict):
            kept.append(item)
