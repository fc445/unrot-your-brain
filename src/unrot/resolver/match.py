"""Cheap, deterministic matching — what happens before any model is asked.

Two jobs, and the split between them matters:

* **Decide the easy cases without a model at all.** A term that normalises to an
  existing concept's name or one of its aliases is that concept. No judgment is
  required, so none is bought -- most re-encounters of a term take this path and
  cost nothing.
* **Shortlist for the hard ones.** When a model is asked, it should be asked
  about a handful of plausible neighbours rather than handed the whole graph.
  Today's graph fits in a prompt; the point is that it will not always, and a
  resolver that silently degrades once the graph grows is worse than one that
  was built to narrow from the start.

§11 puts near-duplicate detection at the resolver and calls it the highest-value
use of vector similarity in the product. This is the string-only version of that
shortlist. Embeddings would replace `_overlap`, and per S5 would have to record
their `model_version` -- the surrounding structure would not change.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

_PUNCT = re.compile(r"[^a-z0-9]+")

#: How many neighbours the model is shown. Enough to contain the right answer,
#: small enough that the prompt stays about a decision rather than a catalogue.
SHORTLIST = 12


def normalise(name: str) -> str:
    """Casefold and strip punctuation. `K8s` and `k8s.` collapse; `K8s` and
    `Kubernetes` do not -- that is a judgment, and judgments go to the model."""
    return _PUNCT.sub(" ", (name or "").casefold()).strip()


@dataclass(frozen=True)
class Known:
    """One concept as the resolver sees it: what it is called, and what else it is called."""

    concept_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    encounter_count: int = 0

    @property
    def names(self) -> tuple[str, ...]:
        return (self.canonical_name, *self.aliases)


def current(conn: sqlite3.Connection) -> list[Known]:
    """Every concept still standing, as of the last compile.

    Merged-away concepts are excluded: their names survive as aliases on the
    survivor, so they remain findable without being resolvable targets. Reading
    compiled state rather than folding the log here is deliberate -- the
    resolver is a client of the compile step like everything else.
    """
    import json

    return [
        Known(
            concept_id=row["concept_id"],
            canonical_name=row["canonical_name"],
            aliases=tuple(json.loads(row["aliases"] or "[]")),
            encounter_count=row["encounter_count"],
        )
        for row in conn.execute(
            "SELECT concept_id, canonical_name, aliases, encounter_count"
            " FROM compiled_concepts WHERE merged_into IS NULL"
            " ORDER BY canonical_name"
        )
    ]


def exact(known: list[Known], text: str) -> Known | None:
    """The concept this text already names, under any of its names. None if new."""
    target = normalise(text)
    if not target:
        return None
    for concept in known:
        if any(normalise(name) == target for name in concept.names):
            return concept
    return None


def _overlap(a: str, b: str) -> float:
    """Jaccard over word tokens, with a containment bonus.

    Crude on purpose. Its job is only to order a shortlist -- being wrong here
    costs the model a slightly worse menu, not the user a wrong answer.
    """
    left = set(normalise(a).split())
    right = set(normalise(b).split())
    if not left or not right:
        return 0.0
    score = len(left & right) / len(left | right)
    # "backpressure" against "backpressure handling": no token is shared with
    # the longer form under exact equality, but one clearly sits inside the other.
    if normalise(a) in normalise(b) or normalise(b) in normalise(a):
        score = max(score, 0.5)
    return score


def shortlist(known: list[Known], text: str, limit: int = SHORTLIST) -> list[Known]:
    """The concepts worth showing the model, best first.

    Falls back to the most-encountered concepts when nothing scores, so the
    model always sees *something* of the graph. A resolver shown an empty list
    can only ever answer "new", which would make every misspelling its own
    concept the moment the string matcher fails.
    """
    # Ties break toward concepts the user has actually met. Material naming ~5
    # concepts per piece means scaffolding outnumbers real concepts in the graph
    # very quickly, and a shortlist dominated by things that entered via someone
    # else's explanation is a worse menu than one led by what the user has hit.
    scored = sorted(
        ((max(_overlap(text, name) for name in c.names), c) for c in known),
        key=lambda pair: (-pair[0], -pair[1].encounter_count, pair[1].canonical_name),
    )
    hits = [c for score, c in scored if score > 0][:limit]
    if hits:
        return hits
    return sorted(known, key=lambda c: (-c.encounter_count, c.canonical_name))[:limit]
