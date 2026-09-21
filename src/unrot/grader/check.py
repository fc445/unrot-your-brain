"""The question put to the user. The one part of this that cannot be undone.

Everything else here is recoverable. The rubric is derived, so it can be swapped
and the whole history re-graded. The grade is disposable. The raw answer is kept
precisely so that both remain true.

The question is not recoverable, and that asymmetry is the reason this is its
own module rather than a string constant next to the grader. `ideas.md`:

    "One line, what is it?" elicits listed answers. Measuring causal
    understanding requires asking for causation -- "why does it work that way?".
    Old answers can be re-graded; a question the user has moved on from cannot
    be re-asked.

PR-24 offers the 2026-09-01 "what is it in one line?" as a casual default, and
that default is declined here on the docs' own reasoning. The rubric's only
load-bearing boundary is listed -> causal; a question that asks solely for a
definition elicits listed-shaped answers, so the grader would spend the whole
corpus unable to see the distinction it exists to draw. Storing `prompt_text`
verbatim makes changing the wording cheap, but it does not recover signal that
was never elicited in the first place.

Two other things follow from the same argument:

* **A line or two, not one.** A causal explanation rarely fits in one, and
  asking for one actively suppresses the structure being measured.
* **Ask for causation without describing a good answer.** "What would go wrong
  without it?" would elicit better-shaped answers, but a question that spells
  out the expected shape rewards following instructions rather than
  understanding -- which is the self-report failure this whole check replaces.
"""

from __future__ import annotations

import hashlib

#: Bump when the wording changes. Answers under the old and new wording remain
#: interpretable side by side, because each one carries its own question.
CHECK_VERSION = "c1"

TEMPLATE = (
    "In a line or two: what is {term}, and why does it work the way it does?"
)


def check_version() -> str:
    """Version plus a hash of the wording, so an edit is visible in history."""
    digest = hashlib.sha256(TEMPLATE.encode("utf-8")).hexdigest()
    return f"{CHECK_VERSION}-{digest[:8]}"


def question(term: str) -> str:
    """The exact words shown to the user, which is what gets stored verbatim.

    Rendered with the term already in it rather than stored as a template plus
    arguments: a future re-grader needs to read what was actually on screen, and
    reconstructing that from a template and a substitution is one more thing
    that can quietly stop being true.
    """
    return TEMPLATE.format(term=term)
