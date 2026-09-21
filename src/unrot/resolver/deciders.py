"""The judgment the resolver injects: is this new, or something we already have?

Two implementations, and both are real.

`strict` decides nothing. It is reached only when the string matcher has already
failed, so it always answers `new` -- which means no API key is required to run
the whole pipeline end to end, at the cost of `K8s` becoming its own concept
beside `Kubernetes`. That is the correct degradation: a duplicate is a later
merge, whereas a resolver that guessed at matches without a model would file
encounters under concepts the user never met, and the surface gives them no way
to notice.

`build_decider` is the real one, and is deliberately the only place in the
product where a model is asked to say that two things are the same. §11 is
explicit that near-duplicate detection belongs at the resolver rather than in
the graph, for a reason worth restating: similarity cannot tell *same as* from
*opposite of in the same space*. Optimistic and pessimistic locking embed almost
identically -- same domain, same vocabulary -- and any model or metric asked to
judge them needs to be told, in the prompt, that this is the trap.
"""

from __future__ import annotations

from ..model import ModelConfig, structured_client
from . import prompt as prompt_module
from .match import Known
from .submissions import Submission


def strict(submission: Submission, shortlist: list[Known]) -> dict:
    """The no-model decider. Everything the string matcher missed is new.

    Not a stub: this is what runs when no key is configured, and it keeps the
    product usable offline as a pure detector-plus-list, which the PRD requires
    material generation never to be load-bearing against.
    """
    del shortlist
    return {
        "decision": "new",
        "canonical_name": submission.text,
        "reasoning": (
            "No exact match, and no model was available to judge near-duplicates."
            " Recorded as new; merge it later if it turns out to be one we have."
        ),
    }


def build_decider(config: ModelConfig):
    """Return `decide(submission, shortlist) -> dict`.

    Imports pydantic lazily alongside the client, so importing the resolver --
    or testing every rule in it -- does not require langchain.
    """
    from pydantic import BaseModel, Field

    class Decision(BaseModel):
        decision: str = Field(
            description="new | existing | alias. Prefer `new` when genuinely unsure."
        )
        concept_id: str | None = Field(
            default=None,
            description="Required for existing/alias: the id from the candidate list.",
        )
        canonical_name: str = Field(
            description=(
                "What this concept should be called. For a vague or misspelled"
                " submission, the term the person was actually reaching for."
            )
        )
        alias: str | None = Field(
            default=None,
            description="For `alias`: the other name to record against the concept.",
        )
        paraphrase: str | None = Field(
            default=None,
            description=(
                "A standalone one or two sentences. Must make sense to someone"
                " reading it on a phone with no code and no transcript in front"
                " of them."
            ),
        )
        reasoning: str = Field(
            description="Why. This is shown to the user and can be argued with."
        )

    client = structured_client(config, Decision)

    def decide(submission: Submission, shortlist: list[Known]) -> dict:
        return client.invoke(
            prompt_module.render(submission, shortlist)
        ).model_dump()

    return decide
