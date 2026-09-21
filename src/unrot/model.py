"""Talking to a model, without caring which one.

Shared by the detector and the resolver, and deliberately at the package root
rather than inside either. The resolver does not depend on detection -- it also
serves manual submissions, which never touch a transcript -- so importing this
from `unrot.detector` would assert a relationship that is not there.

Routed through an OpenAI-compatible endpoint -- OpenRouter by default, or any
local server -- rather than a provider SDK. That is the repo's standing rule,
and it is also what makes the PRD's open question (local-only analysis versus a
hosted call) a configuration change rather than a rewrite.

Callers never import this to *use* a model. Both `detect()` and `resolve()` take
a callable and this builds one, which is the seam that lets their logic be
tested without a network, a key, or langchain installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

#: Freddie's choice, 2026-09-21. Override per-project in `.env` (UNROT_MODEL) or
#: per-run with `--model`; this is only what applies when nothing says otherwise.
#: Changing it changes `detector_version` and `resolver_version`, which is S5
#: working as intended -- a model swap must be visible in history rather than
#: indistinguishable from the user's world having changed.
DEFAULT_MODEL = "inclusionai/ling-3.0-flash"


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
        """What goes into a version string. No key, no endpoint -- just the model."""
        return self.model.replace("/", "-")


NO_KEY_MESSAGE = (
    "No API key. Set $OPENROUTER_API_KEY, or pass --api-key, or point"
    " --base-url at a local server and pass any placeholder key."
)


def structured_client(config: ModelConfig, schema):
    """An OpenAI-compatible chat client pinned to one structured-output schema.

    Imports langchain lazily so that importing the detector or the resolver --
    or testing their logic -- does not require it.
    """
    from langchain_openai import ChatOpenAI

    if not config.api_key:
        raise RuntimeError(NO_KEY_MESSAGE)

    return ChatOpenAI(
        model=config.model,
        base_url=config.base_url,
        api_key=config.api_key,
        temperature=config.temperature,
    ).with_structured_output(schema)
