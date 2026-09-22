"""The resolver: the single write path into the graph.

Nothing else appends concept or encounter events. The detector proposes and
cannot write; the surface records user judgments about encounters that already
exist; everything that puts a *concept* in the graph comes through here.
"""

from .deciders import build_decider, strict
from .match import Known, current, exact, normalise, shortlist
from .prompt import prompt_id
from .resolve import (
    DECISIONS,
    RESOLVER_VERSION,
    UNRESOLVABLE,
    Resolution,
    Unresolvable,
    correct,
    judgments,
    merge,
    record_analysis,
    resolve,
    resolve_all,
    resolve_reference,
    resolver_version,
)
from .submissions import MANUAL_VERSION, Submission, fingerprint, from_candidate, manual

__all__ = [
    "DECISIONS",
    "Known",
    "MANUAL_VERSION",
    "RESOLVER_VERSION",
    "Resolution",
    "Submission",
    "UNRESOLVABLE",
    "Unresolvable",
    "build_decider",
    "correct",
    "current",
    "exact",
    "fingerprint",
    "from_candidate",
    "judgments",
    "manual",
    "merge",
    "normalise",
    "prompt_id",
    "record_analysis",
    "resolve",
    "resolve_all",
    "resolve_reference",
    "resolver_version",
    "shortlist",
    "strict",
]
