"""The rubric, as the grader sees it.

Collapsed SOLO, three levels, and the only boundary that carries weight is
listed -> causal. That is where "I can recite what the model told me" separates
from "I understood it" -- someone who has just read an explanation can produce a
listed answer straight from recall, which is exactly the false pass this check
exists to catch.

Unlike `check.py`, everything here is recoverable: the rubric is derived, the
grade is disposable, and the raw answers are retained precisely so the whole
history can be re-graded under a different rubric later. Edit this file freely,
then re-run the grader over history.

The template is hashed into `grader_version`, so an edit is visible in the
provenance of every grade produced afterwards rather than silently changing what
past grades meant.
"""

from __future__ import annotations

import hashlib

RUBRIC = "solo-3"
PROMPT_VERSION = "g1"

TEMPLATE = """\
Someone is being checked on whether they actually understand a term that came \
up in their work, rather than having merely nodded along to it.

They were asked:
  {question}

They answered:
  {answer}

Grade the ANSWER on this scale. Grade what they wrote, not what you know about \
the topic, and not how well written it is.

- "isolated": one fact, or a vague gesture. No structure.
    e.g. for idempotency -- "You can call it twice."

- "listed": several correct facts, sitting next to each other, not connected.
    Recall without linkage. This is what reciting a good explanation produces.
    e.g. -- "You can call it twice, it uses a key, it's for retries."

- "causal": the facts are linked -- something is true BECAUSE of something \
else, or one thing enables or prevents another. They explain why it works the \
way it does, not just what it does.
    e.g. -- "Calling it twice has the same effect as once, so a client can \
retry after a timeout without double-charging -- hence the dedup key."

Rules that matter more than they look:

- **The listed/causal line is the whole point.** Do not award causal for a \
fluent or confident answer that is still just a list. Look for actual \
connective work -- "so", "because", "which means", "otherwise" -- doing real \
explanatory work rather than decorating a list.
- **An answer can be short and still causal.** Brevity is not a reason to mark \
down; they were asked for a line or two.
- **An answer can be long, polished and still listed.** Length is not evidence.
- **Wrong is not the same as shallow.** If what they wrote is incorrect, say so \
in your reasoning and grade the structure they actually showed -- a confidently \
wrong causal story is still causal in shape, and the reasoning is where that \
gets recorded.
- If they say they do not know, that is "isolated". It is an honest answer and \
costs them nothing; do not strain to find structure in it.

Give the level, and one sentence of reasoning aimed at the person who wrote the \
answer -- they will read it.
"""


def prompt_id() -> str:
    """A short, stable hash of the rubric, so an edit shows up in provenance."""
    digest = hashlib.sha256(TEMPLATE.encode("utf-8")).hexdigest()
    return f"{PROMPT_VERSION}-{digest[:8]}"


def render(question: str, answer: str) -> str:
    """Render the grading prompt.

    The question is included, not just the answer. The same answer means
    different things under different questions, which is the entire reason
    `prompt_text` is stored verbatim on the submission -- a re-grade that did
    not pass it back would throw away the thing it was kept for.
    """
    return TEMPLATE.format(question=question, answer=answer)
