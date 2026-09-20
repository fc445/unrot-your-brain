"""The judgment the detector is actually making, written down.

This file is the product. Everything else in the package is plumbing around one
question: did the assistant lean on something the human did not understand, and
did the human wave it through?

The template is hashed into `detector_version`, so editing this file changes the
recorded provenance of everything produced afterwards. That is deliberate --
S5's requirement is that a silent change to the machine's judgment must not be
indistinguishable from a change in the user's world.
"""

from __future__ import annotations

import hashlib

PROMPT_VERSION = "p2"

TEMPLATE = """\
You are reading excerpts from a real Claude Code session. Each excerpt is one \
thing the assistant said, followed by the next thing the human actually typed \
(tool output and system text have already been removed, so a "human turn" here \
really is a person).

Find terms or concepts the assistant used LOAD-BEARINGLY and did not explain.

Load-bearing means: the human needed to understand that term to judge whether \
what happened next was a good idea. If they could have followed the decision \
without knowing it, it is not load-bearing.

Then classify the human's next turn:
- "accepted": they moved on -- "ok", "go ahead", "do it", a new instruction, \
silence -- with no sign they understood the term.
- "questioned": they asked what it meant, pushed back on it, or used or \
explained it correctly themselves. This is evidence of understanding. Not a gap.
- "unclear": there is no next human turn to judge from.

And rate how much it mattered:
- "central": the human could not have properly evaluated the decision without \
understanding this term.
- "supporting": understanding it would have helped, but the decision was \
judgeable without it.

Rules:
- Skip anything the assistant explained inline, right there.
- Skip ordinary programming vocabulary the human clearly already uses \
("function", "variable", "commit", "test", "API").
- Skip project-specific names -- files, variables, branches, ticket ids. A gap \
is a concept someone could go and learn, not a local label.
- Do not flag a term twice. Pick its clearest appearance.

How to decide, per candidate: would you bet that this person could not give a \
correct one-sentence account of this term? If you would not bet on it, do not \
flag it. Apply that bar to each candidate on its own merits -- do not hold back \
a candidate that clears it because you have already found one, and do not add \
one that fails it to reach a number. An empty list is a correct and common \
answer. Return at most {max_candidates}, strongest first.

For each one, write a `paraphrase` that STANDS ALONE: one or two sentences that \
will be read on a phone, days later, by someone who cannot see this transcript \
or the code. Say what was being decided and why the term carried weight in it. \
Do not write "the transcript shows" or refer to line numbers. Name the term \
plainly, and do not explain what it means -- that comes later, and guessing here \
would teach them your guess.

Excerpts:

{excerpts}
"""


def prompt_id() -> str:
    """A short, stable hash of the prompt, so an edit is visible in provenance."""
    digest = hashlib.sha256(TEMPLATE.encode("utf-8")).hexdigest()
    return f"{PROMPT_VERSION}-{digest[:8]}"


def render(excerpts: str, *, max_candidates: int = 2) -> str:
    """Render the prompt.

    The budget is stated to the model rather than only enforced afterwards. It
    was previously implicit, which made volume depend on how many chunks a
    session happened to split into -- "be sparing" applies per call, so seven
    calls quietly permitted seven times as many flags as one.
    """
    return TEMPLATE.format(excerpts=excerpts, max_candidates=max_candidates)


def format_windows(windows) -> str:
    """Lay windows out for the model, keeping line numbers attached.

    Assistant turns that share a reply are listed together, with the human's
    words printed once underneath them -- which is how the exchange actually
    happened, and avoids repeating a long reply after every turn it answered.
    """
    from .windows import group_windows

    blocks = []
    for group in group_windows(list(windows)):
        parts = [
            f"--- assistant (line {w.assistant_line}) ---\n{w.assistant_text}\n"
            for w in group
        ]
        human = group[0]
        if human.human_text:
            parts.append(
                f"--- human, next turn (line {human.human_line}) ---\n"
                f"{human.human_text}\n"
            )
        else:
            parts.append("--- human, next turn ---\n(none -- session ended here)\n")
        blocks.append("".join(parts))
    return "\n".join(blocks)
