"""The comprehension check: capture the explanation, grade it separately.

Asking someone to explain a term beats asking whether they know it, because
people confirm familiarity with things they do not know. The two halves are
deliberately separate entities: what they wrote is ground truth and permanent,
the level it was given is derived and disposable.
"""

from .check import CHECK_VERSION, TEMPLATE, check_version, question
from .grade import (
    GRADER_VERSION,
    Explanation,
    grade,
    grader_version,
    history,
    question_for,
    regrade,
    submit,
    ungraded,
)
from .jev import CRITERIA, DEFAULT_JEV_MODEL, build_jev_grader, threshold_level
from .model import build_grader, keyword_grader
from .prompt import RUBRIC, prompt_id

__all__ = [
    "CHECK_VERSION",
    "Explanation",
    "GRADER_VERSION",
    "RUBRIC",
    "TEMPLATE",
    "CRITERIA",
    "DEFAULT_JEV_MODEL",
    "build_grader",
    "build_jev_grader",
    "check_version",
    "grade",
    "grader_version",
    "history",
    "keyword_grader",
    "prompt_id",
    "question",
    "question_for",
    "regrade",
    "threshold_level",
    "submit",
    "ungraded",
]
