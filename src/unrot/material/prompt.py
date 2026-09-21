"""The instruction for format 1, where every claim has to be answerable for.

`decisions.md` calls provenance a P0 gate and says this is the one place in the
skeleton where "deliberately crude" does not apply. So the prompt is built
around a single rule -- **say only what the sources say** -- and the check on it
lives in code: every citation the model emits is matched against the sources it
was given, and material citing anything else is rejected rather than cleaned up.

The template is hashed into the recorded version, so editing it is visible in
the provenance of everything written afterwards.
"""

from __future__ import annotations

import hashlib

PROMPT_VERSION = "m1"

TEMPLATE = """\
Explain {term} to a working software engineer who just met it in their own work \
and let it go past without asking. They are not a beginner; they simply have not \
had to pin this one down before.

USE ONLY THE SOURCES BELOW. Every substantive claim must come from one of them \
and must be followed by its tag, like [S2]. If the sources do not support \
something, leave it out -- a gap in the explanation is fine, an unsupported \
sentence is not. Do not add facts you happen to know.

{context}

SOURCES
{sources}

Write four to eight sentences. Aim for why it works the way it does and what \
goes wrong without it, not a definition -- they can already find a definition. \
Prefer the concrete over the general.

If a source is their own code or the session where this came up, build the \
explanation around that: what this means for the code they are actually \
working on is the part no general article can give them.

Also list the other concepts a reader needs in order to follow your explanation \
-- at most five, only ones you actually leaned on, named as an engineer would \
say them. If none, say none.
"""


def prompt_id() -> str:
    digest = hashlib.sha256(TEMPLATE.encode("utf-8")).hexdigest()
    return f"{PROMPT_VERSION}-{digest[:8]}"


def format_sources(sources) -> str:
    """Tag each source so citations can be checked against what was supplied."""
    lines = []
    for index, source in enumerate(sources, start=1):
        lines.append(f"[S{index}] ({source.kind}) {source.title}\n    {source.ref}")
        if source.excerpt:
            lines.append(f"    {source.excerpt}")
    return "\n".join(lines)


def render(term: str, sources, *, context: str = "") -> str:
    return TEMPLATE.format(
        term=term,
        sources=format_sources(sources),
        context=f"WHERE THEY MET IT\n{context}\n" if context else "",
    )
