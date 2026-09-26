"""Grading with a System One classifier instead of a reasoning model.

A chat model asked for a SOLO level has to be talked into a schema, then trusted
to stay inside it. Jev is built for the shape this question actually has: pick
one of a fixed set, and say how sure you are. It returns a probability for every
level and a confidence, not prose.

That distribution is the reason to prefer it here, more than the speed or the
price. **The rubric's only load-bearing boundary is listed -> causal**, and a
stored probability says how close an answer came to that line, where a stored
label throws that away. Moving the threshold later then costs nothing at all --
no re-grading, no model calls, just a different comparison over numbers already
in the log. That is the same argument the whole store is built on, one level
further in: keep what was actually observed, derive the verdict.

Measured on the rubric's own three examples plus five real answers, it agreed
with the reasoning model on every one, including the two cases the rubric warns
about: a fluent, well-written answer that is still just a list, and an honest
"no idea". ~0.3s and ~$0.00002 a grade, against ~12s and ~$0.002.

**It cannot explain itself.** System One models return typed decisions and no
prose, so the one sentence of feedback the check gives back has to come from
somewhere else, or not at all. `grade.py` treats reasoning as optional for
exactly this reason.

Reached through OpenRouter like everything else here, but at `/v1/systemone`
rather than `/chat/completions`, through `langchain_typesafe` -- so each grade is
a traced run rather than an HTTP call LangSmith never sees.
"""

from __future__ import annotations

from ..model import ModelConfig, decision_client
from ..store import SOLO_LEVELS

#: The default. Pinned to a minor version rather than an alias: the grade is
#: derived state and its provenance names a model, so silently sliding onto a
#: different one would make old grades and new grades incomparable without
#: anything in the data showing it happened.
DEFAULT_JEV_MODEL = "typesafe/jev-1.13"

#: Written to separate the levels from each other rather than to describe them
#: in isolation -- the docs are explicit that the option descriptions are what
#: the model discriminates on, so the contrast has to be in the text.
CRITERIA: dict[str, str] = {
    "isolated": "One fact, or a vague gesture, with no structure around it. Also use this when the answer says they do not know.",
    "listed": (
        "Several correct facts sitting next to each other without being connected."
        " Recall without linkage. This is what reciting a good explanation"
        " produces, and a long, fluent, well-written answer can still be this."
    ),
    "causal": (
        "The facts are linked: something is true BECAUSE of something else, or"
        " one thing enables or prevents another. Explains why it works the way it"
        " does, not merely what it does. A short answer can be this."
    ),
}

INSTRUCTIONS = (
    "Grade the structure of the ANSWER: how much of it is connected reasoning"
    " rather than recall. Judge the structure the answer actually shows, not"
    " whether it is correct and not how well written it is."
)


def build_jev_grader(
    config: ModelConfig | None = None, *, model: str | None = None, meter=None, transport=None
):
    """Return `grade_fn(question, answer) -> dict` backed by a System One model.

    The returned dict carries `probabilities` and `confidence` alongside
    `level`, and no `reasoning` -- the caller decides whether to source that
    separately.
    """
    from langchain_typesafe import Choice

    config = config or ModelConfig.from_env()
    chosen = model or DEFAULT_JEV_MODEL
    classify = decision_client(
        config, model=chosen, meter=meter, purpose="grading", transport=transport
    )
    solo = Choice(instructions=INSTRUCTIONS, criteria=CRITERIA)

    def grade_fn(question: str, answer: str) -> dict:
        # Both halves, because the same answer means different things under
        # different questions -- which is the whole reason the question is
        # stored verbatim beside it.
        response = classify(f"Question: {question}\nAnswer: {answer}", {"solo": solo})
        got = response.answers.get("solo")
        given = getattr(got, "probabilities", None) or {}
        probabilities = {level: float(given.get(level) or 0.0) for level in SOLO_LEVELS}
        return {
            # Taken from the distribution rather than from `choice`, so the
            # level and the probabilities stored beside it can never disagree.
            "level": max(probabilities, key=probabilities.get),
            "probabilities": probabilities,
            "confidence": float(getattr(got, "confidence", 0.0) or 0.0),
            "model": response.model,
        }

    return grade_fn


def threshold_level(probabilities: dict, *, causal_at: float = 0.5) -> str:
    """Re-derive a level from stored probabilities, at any threshold.

    Nothing calls this yet, and that is the point: because the distribution is
    kept, moving the bar for `causal` later is a pure re-reading of the log --
    no model calls, no re-grading, no new events. It is here so the next person
    can see that the option exists rather than assuming the label is all there is.
    """
    causal = float(probabilities.get("causal") or 0.0)
    if causal >= causal_at:
        return "causal"
    listed = float(probabilities.get("listed") or 0.0)
    isolated = float(probabilities.get("isolated") or 0.0)
    return "listed" if listed >= isolated else "isolated"
