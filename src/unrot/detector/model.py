"""Building the detector's `propose` callable.

`ModelConfig` moved up to `unrot.model` when the resolver arrived and needed the
same configuration: both talk to an OpenAI-compatible endpoint, and neither
depends on the other. It is re-exported here so `from unrot.detector import
ModelConfig` keeps working -- the seam this module exists for is `build_proposer`,
not the config dataclass.
"""

from __future__ import annotations

from ..model import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ModelConfig,
    structured_client,
)

__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "ModelConfig", "build_proposer"]


def build_proposer(config: ModelConfig):
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

    client = structured_client(config, Proposal)

    def propose(prompt_text: str) -> list[dict]:
        result = client.invoke(prompt_text)
        return [c.model_dump() for c in result.candidates]

    return propose
