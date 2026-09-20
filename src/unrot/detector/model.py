"""Talking to a model, without caring which one.

Routed through an OpenAI-compatible endpoint -- OpenRouter by default, or any
local server -- rather than a provider SDK. That is the repo's standing rule,
and it is also what makes the PRD's open question (local-only analysis versus a
hosted call) a configuration change rather than a rewrite.

The rest of the detector never imports this module directly: `detect()` takes a
`propose` callable, and this builds one. That seam is what lets the ranking and
budgeting logic be tested without a network, a key, or langchain installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-5"


@dataclass(frozen=True)
class ModelConfig:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    #: Zero, because the same session must not produce a different answer on
    #: Tuesday. Not a guarantee -- providers are not bit-deterministic -- but it
    #: is the part we control.
    temperature: float = 0.0

    @classmethod
    def from_env(cls, **overrides) -> "ModelConfig":
        key = (
            overrides.pop("api_key", None)
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        return cls(
            model=overrides.pop("model", None) or os.environ.get("UNROT_MODEL") or DEFAULT_MODEL,
            base_url=overrides.pop("base_url", None)
            or os.environ.get("UNROT_BASE_URL")
            or DEFAULT_BASE_URL,
            api_key=key,
            **overrides,
        )

    @property
    def label(self) -> str:
        """What goes into detector_version. No key, no endpoint -- just the model."""
        return self.model.replace("/", "-")


def build_proposer(config: ModelConfig):
    """Return `propose(prompt_text) -> list[dict]`.

    Imports langchain lazily so that importing the detector -- or testing its
    logic -- does not require it.
    """
    from langchain_openai import ChatOpenAI
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

    if not config.api_key:
        raise RuntimeError(
            "No API key. Set $OPENROUTER_API_KEY, or pass --api-key, or point"
            " --base-url at a local server and pass any placeholder key."
        )

    client = ChatOpenAI(
        model=config.model,
        base_url=config.base_url,
        api_key=config.api_key,
        temperature=config.temperature,
    ).with_structured_output(Proposal)

    def propose(prompt_text: str) -> list[dict]:
        result = client.invoke(prompt_text)
        return [c.model_dump() for c in result.candidates]

    return propose
