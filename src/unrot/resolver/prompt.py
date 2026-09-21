"""The judgment the resolver is actually making, written down.

One question: is this thing already in the graph under some other name, or is it
genuinely new?

Getting it wrong in one direction costs a duplicate concept, which a later merge
folds away. Getting it wrong in the other files an encounter under something the
user never met -- and the surface gives them no way to notice that, because the
card will look exactly like every other card. So the prompt leans toward `new`,
and says so explicitly rather than hoping the model infers the asymmetry.

The template is hashed into `resolver_version`, so editing this file changes the
recorded provenance of everything judged afterwards -- S5's rule that a silent
change in the machine's judgment must not look like a change in the user's world.
"""

from __future__ import annotations

import hashlib

PROMPT_VERSION = "r1"

TEMPLATE = """\
You maintain a personal knowledge graph of concepts someone has encountered but \
may not understand. A new item has arrived. Decide whether it belongs to a \
concept already in the graph, or is a new one.

ITEM ({source})
  as encountered: {text}
  context: {paraphrase}

CONCEPTS ALREADY IN THE GRAPH
{candidates}

Answer with one of:
- "existing" -- this IS one of the concepts listed, under a name it already has.
- "alias" -- this is one of the concepts listed, under a DIFFERENT name worth \
recording ("K8s" for "Kubernetes", a misspelling, an abbreviation, a plural).
- "new" -- none of them. **Prefer this whenever you are genuinely unsure.**

The asymmetry is deliberate. A duplicate concept is tidied up later by a merge. \
A wrong match files this encounter under something the person never actually \
met, and nothing downstream will ever show them that it happened.

THE TRAP: things that sit in the same space are not the same thing. "Optimistic \
locking" and "pessimistic locking" share a domain, a vocabulary and almost every \
word -- they are opposites, and they are two concepts, not one. Same for \
"eventual consistency" and "strong consistency", "queue" and "stream", "authn" \
and "authz". Near-identical wording is not evidence of sameness; being the same \
idea is.

{manual_note}
Also give:
- canonical_name: what this concept should be called. Use the established name \
for it, not the mangled form it arrived as -- if the item reads "backpresure" or \
"that thing about pushing back on producers", the canonical name is \
"backpressure". For "existing" and "alias", repeat the listed concept's name.
- paraphrase: one or two sentences that stand ALONE. Someone will read this on a \
phone with no code and no transcript in front of them, so "the queue in the \
handler" means nothing to them. Say what the concept was doing and why it \
mattered here.
- reasoning: why you decided that. The person can see this and disagree with it, \
so write it for them, not for a log.
"""

_MANUAL_NOTE = """\
This item was typed by the person from memory, so it may be misheard, misspelt, \
or barely a term at all ("something about backpressure?"). Work out what they \
were reaching for. If you genuinely cannot tell what concept is meant, say "new" \
and use their own words as the canonical name -- a wrong guess is worse than a \
rough one they can correct.

"""


def prompt_id() -> str:
    """A short, stable hash of the prompt, so an edit is visible in provenance."""
    digest = hashlib.sha256((TEMPLATE + _MANUAL_NOTE).encode("utf-8")).hexdigest()
    return f"{PROMPT_VERSION}-{digest[:8]}"


def format_candidates(shortlist) -> str:
    """The graph as a menu, with ids the model must quote back verbatim.

    Encounter counts are shown because they carry real information: a concept
    met eight times is far more likely to be the one a mangled term is reaching
    for than one met once.
    """
    if not shortlist:
        return "  (the graph is empty -- this can only be new)"
    lines = []
    for concept in shortlist:
        names = f" (also: {', '.join(concept.aliases)})" if concept.aliases else ""
        seen = f" [met {concept.encounter_count}x]" if concept.encounter_count else ""
        lines.append(f"  {concept.concept_id}: {concept.canonical_name}{names}{seen}")
    return "\n".join(lines)


def render(submission, shortlist) -> str:
    """Render the prompt for one submission."""
    return TEMPLATE.format(
        source=submission.source,
        text=submission.text,
        paraphrase=submission.paraphrase,
        candidates=format_candidates(shortlist),
        manual_note=_MANUAL_NOTE if submission.source == "manual" else "",
    )
