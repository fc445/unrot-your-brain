"""Turning a confirmed gap into material, in the two formats v1 ships.

| | Format | What it does |
| -- | -- | -- |
| 1 | `textual_with_sources` | Generated prose, grounded in and citing real sources |
| 2 | `sources_only` | No prose at all. Curated pointers, handed over |

Format 2 is not a degraded mode. It is "links-only as a first-class output"
made concrete, and it sidesteps hallucination entirely by generating nothing.
Format 1's citation requirement is the check on the other path.

Three rules here are load-bearing:

* **No verified sources, no prose.** Format 1 refuses rather than writing
  something ungrounded. The P0 gate and "pure-detector mode must be fully
  useful" turn out to be the same mechanism: when there is nothing to stand on,
  the product goes quiet instead of making something up.
* **Citations are checked against what was supplied.** A model citing `[S9]`
  when it was given four sources has invented a reference, and the material is
  rejected rather than tidied. Rejecting is cheap; a plausible-looking citation
  that supports nothing is the exact failure this is guarding.
* **Depth 1.** Material names about five concepts. If each of those generated
  its own, that is 125 by depth 3, none of it originating with the user.
  Generation therefore refuses for any concept with no encounters -- which is
  precisely `referenced`. Expansion is driven by encounters, never by the
  graph's own contents; explosion comes from recursion, not from adding.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from ..store import append, compile_state, new_ulid
from . import prompt as prompt_module
from .sources import Source

MATERIAL_VERSION = "0.1.0"

TEXTUAL = "textual_with_sources"
SOURCES_ONLY = "sources_only"
FORMATS = (TEXTUAL, SOURCES_ONLY)

#: Material names at most this many other concepts. A cap on the *width* of one
#: step; the depth-1 rule is what stops it ever becoming a tree.
MAX_COVERED = 5

_CITATION = re.compile(r"\[S(\d+)\]")


class NotGrounded(RuntimeError):
    """Raised when material would have to be written without support."""


class WouldRecurse(RuntimeError):
    """Raised when generation is asked for a concept the user never met."""


def material_version(model_label: str) -> str:
    return f"material/{MATERIAL_VERSION}+{model_label}+{prompt_module.prompt_id()}"


@dataclass
class Material:
    material_id: str
    format: str
    concept_id: str
    body: str | None
    sources: list[Source]
    covered: list[str]


def _guard(conn: sqlite3.Connection, concept_id: str) -> sqlite3.Row:
    """Refuse to generate for a concept the user has never actually met.

    The explosion guard, expressed as the thing it actually means. A concept
    with no encounters is one that only exists because some earlier material
    mentioned it -- generating for it is the recursive step, and there is no
    natural place to stop once it is allowed once.
    """
    row = conn.execute(
        "SELECT * FROM compiled_concepts WHERE concept_id = ?", (concept_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no concept {concept_id!r}")
    if not row["encounter_count"]:
        raise WouldRecurse(
            f"{row['canonical_name']!r} was named in material but never encountered"
            " -- generating for it is the recursive step depth-1 exists to stop"
        )
    return row


def _context(conn: sqlite3.Connection, concept_id: str) -> str:
    row = conn.execute(
        "SELECT paraphrase FROM compiled_encounters WHERE concept_id = ?"
        " ORDER BY occurred_at DESC LIMIT 1",
        (concept_id,),
    ).fetchone()
    return (row["paraphrase"] or "") if row else ""


def _record(
    conn: sqlite3.Connection,
    *,
    concept_id: str,
    fmt: str,
    body: str | None,
    sources: list[Source],
    covered_ids: list[str],
    model_label: str,
    origin: str,
) -> str:
    material_id = "m-" + new_ulid()
    append(
        conn,
        "material_generated",
        {
            "material_id": material_id,
            "format": fmt,
            # The concept it was made for comes first; anything else it leaned
            # on follows. Both are covered, which is what makes them `exposed`
            # or `referenced` once it is delivered.
            "covers_concept_ids": [concept_id, *covered_ids],
            "sources": [s.as_dict() for s in sources],
            "body": body,
        },
        origin=origin,
        provenance={"material_version": material_version(model_label)},
    )
    return material_id


def sources_only(
    conn: sqlite3.Connection,
    concept_id: str,
    sources: list[Source],
    *,
    origin: str = "local",
    recompile: bool = True,
) -> Material:
    """Format 2. Nothing is written, so nothing can be made up.

    Unverified links are kept and labelled rather than dropped. The user clicks
    through and sees the page for themselves, which is the one check they cannot
    perform on a generated sentence -- so the bar for showing a link is lower
    than the bar for asserting something, on purpose.
    """
    row = _guard(conn, concept_id)
    if not sources:
        raise NotGrounded(f"found nothing to point at for {row['canonical_name']!r}")

    material_id = _record(
        conn,
        concept_id=concept_id,
        fmt=SOURCES_ONLY,
        body=None,
        sources=sources,
        covered_ids=[],
        model_label="none",
        origin=origin,
    )
    if recompile:
        compile_state(conn)
    return Material(material_id, SOURCES_ONLY, concept_id, None, sources, [])


def textual(
    conn: sqlite3.Connection,
    concept_id: str,
    sources: list[Source],
    *,
    write,
    resolve_named=None,
    model_label: str = "none",
    origin: str = "local",
    recompile: bool = True,
) -> Material:
    """Format 1. Prose, from verified sources only, with every citation checked.

    `write` and `resolve_named` are injected for the same reason every other
    model call in this codebase is: the grounding rules, the citation check and
    the depth-1 guard are then testable without a network.
    """
    row = _guard(conn, concept_id)
    grounded = [s for s in sources if s.verified]
    if not grounded:
        # Deliberately not "write something general instead". An explanation of
        # the thing the user cannot evaluate, with nothing behind it, is the
        # failure mode the whole gate exists for.
        raise NotGrounded(
            f"nothing verifiable to ground an explanation of {row['canonical_name']!r};"
            " the sources-only format can still hand over what was found"
        )

    answer = write(
        prompt_module.render(
            row["canonical_name"], grounded, context=_context(conn, concept_id)
        )
    ) or {}
    body = str(answer.get("body") or "").strip()
    if not body:
        raise NotGrounded("the model returned nothing to show")

    cited = {int(n) for n in _CITATION.findall(body)}
    invented = {n for n in cited if n < 1 or n > len(grounded)}
    if invented:
        raise NotGrounded(
            f"cited {sorted(f'[S{n}]' for n in invented)} but was given"
            f" {len(grounded)} source(s) -- an invented citation is the defect"
            " this format exists to prevent, so the material is discarded"
        )
    if not cited:
        raise NotGrounded("wrote an explanation but cited none of the sources")

    covered_ids: list[str] = []
    named = [str(n).strip() for n in (answer.get("covers") or []) if str(n).strip()]
    if resolve_named is not None:
        for term in named[:MAX_COVERED]:
            resolution = resolve_named(term)
            if resolution and resolution.concept_id != concept_id:
                covered_ids.append(resolution.concept_id)

    material_id = _record(
        conn,
        concept_id=concept_id,
        fmt=TEXTUAL,
        body=body,
        sources=grounded,
        covered_ids=covered_ids,
        model_label=model_label,
        origin=origin,
    )
    if recompile:
        compile_state(conn)
    return Material(material_id, TEXTUAL, concept_id, body, grounded, covered_ids)


def deliver(
    conn: sqlite3.Connection,
    material_id: str,
    *,
    origin: str = "local",
    recompile: bool = True,
) -> str:
    """Record that the user actually received it.

    Separate from generating it, because the two mean different things to the
    graph: a concept named in material the user *received* becomes `exposed`,
    while one merely generated and never shown should leave no trace on what
    they have been taught.
    """
    event_id = append(
        conn, "material_delivered", {"material_id": material_id}, origin=origin
    )
    if recompile:
        compile_state(conn)
    return event_id


def for_concept(conn: sqlite3.Connection, concept_id: str) -> list[sqlite3.Row]:
    """Everything made for a concept, newest first."""
    return list(
        conn.execute(
            "SELECT m.* FROM compiled_material m"
            " JOIN compiled_material_concepts mc ON mc.material_id = m.material_id"
            " WHERE mc.concept_id = ? ORDER BY m.generated_at DESC",
            (concept_id,),
        )
    )
