"""What the detector hands to the resolver.

This is the whole output contract. The detector does not write to the graph --
PR-21's resolver is the single serialisation point, and keeping the detector
incapable of writing is what makes that true rather than merely intended. There
is deliberately no import of `unrot.store.events` anywhere in this package.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

#: How the human's next turn read. Only `accepted` is a gap: the 2026-09-01
#: decision is behavioural -- a clarifying question is evidence of understanding,
#: not of a gap, and is therefore a reason to stay quiet rather than to flag.
SIGNALS = ("accepted", "questioned", "unclear")

#: Whether understanding the term was required to judge what happened, or merely
#: useful. The ranking signal, and the reason a 1-2 flag budget can be spent well
#: rather than spent on whatever came first.
IMPORTANCE = ("central", "supporting")


@dataclass(frozen=True)
class Candidate:
    """One flagged term, with everything the resolver needs and nothing more."""

    term: str

    #: S4: this must make sense to someone reading it on a phone with no code in
    #: front of them. A paraphrase that only works beside the transcript makes
    #: the portable layer empty, which is most of the product.
    paraphrase: str

    #: The pointer. Resolves against unrot's own copy of the transcript.
    session_id: str
    line_start: int
    line_end: int

    signal: str
    importance: str

    #: S5: which detector produced this. Encodes model and prompt, so a silent
    #: model or prompt change is visible in history rather than indistinguishable
    #: from the user's world having changed.
    detector_version: str

    #: Position in the internal ranked list (1 = best). Retained even for
    #: candidates the budget excludes, so candidate-density can be surfaced later
    #: without re-architecting the detector.
    rank: int = 0

    @property
    def is_gap(self) -> bool:
        return self.signal == "accepted"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DetectionResult:
    session_id: str
    detector_version: str

    #: What the budget allows through -- what the resolver actually sees.
    emitted: list[Candidate]

    #: Everything found, ranked, including what the budget excluded.
    ranked: list[Candidate]

    windows_examined: int
    calls_made: int

    @property
    def suppressed(self) -> int:
        return len(self.ranked) - len(self.emitted)
