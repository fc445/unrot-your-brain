"""Building the grader's `grade_fn` callable.

Same seam as the detector's `propose` and the resolver's `decide`: the grading
logic takes a callable and this builds one, so every rule in `grade.py` -- the
untrusted-level coercion, the store-before-grade ordering, the regrade guard --
is testable without a network, a key, or langchain installed.
"""

from __future__ import annotations

from ..model import ModelConfig, structured_client
from ..store import SOLO_LEVELS
from . import prompt as prompt_module


def build_grader(config: ModelConfig):
    """Return `grade_fn(question, answer) -> dict`."""
    from pydantic import BaseModel, Field

    class Grade(BaseModel):
        level: str = Field(
            description=f"One of: {', '.join(SOLO_LEVELS)}. Grade the answer's structure."
        )
        reasoning: str = Field(
            description=(
                "One sentence, addressed to the person who wrote the answer."
                " They will read it, so write it for them."
            )
        )

    client = structured_client(config, Grade)

    def grade_fn(question: str, answer: str) -> dict:
        return client.invoke(prompt_module.render(question, answer)).model_dump()

    return grade_fn


def keyword_grader(question: str, answer: str) -> dict:
    """A no-model grader, so the check works offline and in tests.

    Deliberately crude and deliberately pessimistic: it looks for connectives
    doing explanatory work, and awards `causal` only when one is present. It
    will under-credit a genuinely causal answer that happens not to use them.

    That direction is the right one to be wrong in. Over-crediting comprehension
    is the exact failure this whole check replaces -- a user who is told they
    understood something they did not is worse off than before -- so when no
    model is available, the fallback marks down rather than up, and says so.
    """
    del question
    text = (answer or "").strip().lower()
    if not text:
        return {"level": "isolated", "reasoning": "Nothing was written."}

    connectives = (
        " so ", " because ", " which means ", " otherwise ", " hence ",
        " therefore ", " that way ", " in order to ", " as a result ",
    )
    padded = f" {text} "
    has_link = any(c in padded for c in connectives)
    facts = text.replace(";", ",").count(",") + 1

    if has_link:
        level, why = "causal", "You linked the facts rather than just listing them."
    elif facts > 1 or len(text.split()) > 18:
        level, why = "listed", "Correct-looking facts, but not connected to each other."
    else:
        level, why = "isolated", "One fact, without much structure around it."

    return {
        "level": level,
        "reasoning": f"{why} (Graded offline without a model, so treat it loosely.)",
    }
