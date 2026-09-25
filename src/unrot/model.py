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
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_MAX_TOKENS = 8_000
#: How many model calls one piece of work may have in flight: a session's
#: transcript chunks, or -- in the app -- sessions side by side. Each call is
#: mostly waiting on the provider (a detection call takes about a minute, most of
#: it reasoning), so four at once is close to four times the throughput. A local
#: server usually answers one request at a time, so there it is one.
DEFAULT_CONCURRENCY = 4


@dataclass(frozen=True)
class ModelConfig:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    #: Zero, because the same session must not produce a different answer on
    #: Tuesday. Not a guarantee -- providers are not bit-deterministic -- but it
    #: is the part we control.
    temperature: float = 0.0
    #: How hard a reasoning model may think before answering, as OpenRouter's
    #: `reasoning.effort`. "low", because left to its own default
    #: inclusionai/ling-3.0-flash reasoned through a 12k-token transcript chunk
    #: line by line for 32,768 tokens and four minutes, never reached the
    #: answer, and failed; at "low" the same chunk answers in about 30 seconds.
    #: Ignored by models that do not reason. Empty sends nothing.
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT
    #: A ceiling, not a target, reasoning included: every answer here is a
    #: small JSON object or a page of material, and at low effort a real
    #: transcript chunk took 1-2.5k tokens. Hitting it means the model would not
    #: stop, and it should find out in about two minutes rather than ten, at a
    #: quarter of the cost. `UNROT_MAX_TOKENS` raises it for a higher effort.
    max_tokens: int = DEFAULT_MAX_TOKENS
    #: See `DEFAULT_CONCURRENCY`. `UNROT_CONCURRENCY` sets it; `from_env` makes
    #: it 1 for a local endpoint unless that says otherwise.
    concurrency: int = DEFAULT_CONCURRENCY

    @classmethod
    def from_env(cls, **overrides) -> "ModelConfig":
        key = (
            overrides.pop("api_key", None)
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        base_url = (
            overrides.pop("base_url", None)
            or os.environ.get("UNROT_BASE_URL")
            or DEFAULT_BASE_URL
        )
        concurrency = max(
            1,
            int(
                overrides.pop("concurrency", None)
                or os.environ.get("UNROT_CONCURRENCY")
                or (1 if is_local(base_url) else DEFAULT_CONCURRENCY)
            ),
        )
        return cls(
            model=overrides.pop("model", None) or os.environ.get("UNROT_MODEL") or DEFAULT_MODEL,
            base_url=base_url,
            api_key=key,
            max_tokens=int(
                overrides.pop("max_tokens", None)
                or os.environ.get("UNROT_MAX_TOKENS")
                or DEFAULT_MAX_TOKENS
            ),
            reasoning_effort=overrides.pop(
                "reasoning_effort", os.environ.get("UNROT_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)
            )
            or None,
            concurrency=concurrency,
            **overrides,
        )

    @property
    def label(self) -> str:
        """What goes into a version string. No key, no endpoint -- the model, and
        the reasoning effort when it is not the default, since that changes the
        answers as surely as a model swap does."""
        label = self.model.replace("/", "-")
        if self.reasoning_effort != DEFAULT_REASONING_EFFORT:
            label += f"+reasoning-{self.reasoning_effort or 'default'}"
        return label

    @property
    def sends_reasoning(self) -> bool:
        """`reasoning` is OpenRouter's parameter; a local server is not sent it."""
        return bool(self.reasoning_effort) and "openrouter.ai" in self.base_url

    @property
    def local(self) -> bool:
        """Whether calls stay on this machine -- and so leave nothing, and cost nothing."""
        return is_local(self.base_url)


def is_local(base_url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


NO_KEY_MESSAGE = (
    "No API key. Set $OPENROUTER_API_KEY, or pass --api-key, or point"
    " --base-url at a local server and pass any placeholder key."
)


def structured_client(
    config: ModelConfig, schema, *, meter=None, purpose: str = "other"
) -> "Structured":
    """An OpenAI-compatible chat client pinned to one structured-output schema.

    Imports langchain lazily so that importing the detector or the resolver --
    or testing their logic -- does not require it.

    With a `meter`, every call the client makes -- including one that fails --
    is reported to it under `purpose`. See `unrot.spend`.
    """
    from langchain_openai import ChatOpenAI

    from .spend import tap

    if not config.api_key:
        raise RuntimeError(NO_KEY_MESSAGE)

    def client(*, max_tokens: int, reasoning: dict | None):
        return ChatOpenAI(
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            temperature=config.temperature,
            max_tokens=max_tokens,
            extra_body={"reasoning": reasoning} if reasoning else None,
            # Both clients are metered: the forced retry is billed too, and so
            # is the call before it that ran out of room.
            callbacks=tap(meter, purpose, config) or None,
        ).with_structured_output(schema)

    on_openrouter = "openrouter.ai" in config.base_url
    return Structured(
        primary=client(
            max_tokens=config.max_tokens,
            reasoning={"effort": config.reasoning_effort} if config.sends_reasoning else None,
        ),
        forced=client(
            max_tokens=FORCED_MAX_TOKENS,
            reasoning={"enabled": False} if on_openrouter else None,
        ),
    )


#: The second call only has to write down an answer it has notes for. Measured:
#: about 200 tokens and five seconds. The cap is room for a long answer, and a
#: bound on one that does not stop.
FORCED_MAX_TOKENS = 4_000

#: How much of the model's own reasoning goes back to it. The tail, because that
#: is where a loop that has already concluded states its conclusion.
NOTES_CHARS = 6_000


class Structured:
    """A structured call that survives a model which will not stop thinking.

    Reasoning models sometimes never leave the reasoning phase. Measured on one
    real 12k-token transcript chunk, inclusionai/ling-3.0-flash did this in 2 of
    5 identical calls: all 32,768 output tokens went on reasoning, the answer was
    empty, and each took nearly ten minutes. One run had concluded early and
    then restated "Empty list. Done." 112 times; the other enumerated every term
    in the transcript. Lowering effort makes it rarer, not impossible.

    So: every call is capped, and a call that runs out of room with no answer is
    asked once more with reasoning off and its own notes attached, to write the
    answer down. On the same chunk that answered 4 of 4, in about five seconds.
    A call that ran out of room *while answering* is not retried here -- its
    partial answer is in the exception, and only the caller knows whether a
    prefix of it is usable (see `truncated_output`).
    """

    def __init__(self, primary, forced) -> None:
        self.primary = primary
        self.forced = forced

    def invoke(self, prompt: str):
        try:
            return self.primary.invoke(prompt)
        except Exception as exc:
            if not ran_out_of_room(exc) or truncated_output(exc):
                raise
            return self.forced.invoke(forced_prompt(prompt, _reasoning_of(exc)))


def ran_out_of_room(exc: BaseException) -> bool:
    """The provider stopped at the output ceiling (`finish_reason == "length"`)."""
    return type(exc).__name__ == "LengthFinishReasonError"


def truncated_output(exc: BaseException) -> str:
    """The partial answer a length-limited call wrote, or empty if it wrote none.

    The OpenAI client raises before parsing but keeps the response on the
    exception, and every token of it was billed.
    """
    try:
        return exc.completion.choices[0].message.content or ""  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        return ""


def _reasoning_of(exc: BaseException) -> str:
    try:
        message = exc.completion.choices[0].message  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        return ""
    return str((message.model_extra or {}).get("reasoning") or "")


def forced_prompt(prompt: str, notes: str) -> str:
    notes = notes[-NOTES_CHARS:].strip()
    return (
        prompt
        + "\n\nYou already analysed this and ran out of room before answering."
        + (f" Your notes, as far as they got:\n<notes>\n{notes}\n</notes>" if notes else "")
        + "\n\nDo not analyse further. Write the answer now."
    )


def describe_failure(exc: BaseException) -> str:
    """A model failure in words a person can act on, instead of a usage dump."""
    if ran_out_of_room(exc):
        return (
            "the model ran out of room before it finished an answer, even when"
            " asked again to answer directly. Try a lower reasoning effort, or a"
            " different model."
        )
    return str(exc)
