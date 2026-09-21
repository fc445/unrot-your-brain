"""What gets handed to the resolver.

The resolver serves two callers that have nothing else in common: the detector,
which hands it a term it extracted from a transcript with a line range attached,
and a person typing "someone said our service needs backpressure handling, no
idea what that means" into a box. Both must land in the same graph through the
same write path, because a second write path is exactly what PR-21 exists to
prevent.

`Submission` is the shape both collapse into, and the reason `resolve()` never
imports the detector. `from_candidate` is the only place the two packages meet,
and it converts in one direction only.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

#: Provenance for a submission nobody's detector produced. It still has to say
#: what made it, because S5 asks every derived row to name its version -- and
#: "a human typed it" is a meaningfully different origin from any model.
MANUAL_VERSION = "manual/0.1.0"


@dataclass(frozen=True)
class Submission:
    """One thing to resolve into the graph.

    `text` is what was encountered: a term for the detector, raw natural
    language for a person. The resolver is responsible for turning the second
    into a concept name -- this dataclass deliberately does not, because doing
    it here would mean two places that decide what a concept is called.
    """

    text: str

    #: S4's portable layer. Must stand alone: on a phone the pointer cannot
    #: resolve, so a paraphrase that only works beside the code makes the remote
    #: experience empty.
    paraphrase: str

    source: str            # 'transcript' | 'manual'
    version: str           # detector_version, or MANUAL_VERSION

    #: The pointer, when there is one. A manual submission has none by
    #: definition -- the user heard it in a meeting.
    session_id: str | None = None
    line_start: int | None = None
    line_end: int | None = None

    @property
    def pointer(self) -> dict | None:
        if not self.session_id:
            return None
        return {
            "session_id": self.session_id,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


def from_candidate(candidate) -> Submission:
    """Adapt one detector `Candidate`. The only coupling between the packages."""
    return Submission(
        text=candidate.term,
        paraphrase=candidate.paraphrase,
        source="transcript",
        version=candidate.detector_version,
        session_id=candidate.session_id,
        line_start=candidate.line_start,
        line_end=candidate.line_end,
    )


def manual(text: str, paraphrase: str | None = None) -> Submission:
    """Adapt a person's own words (journey 10).

    The paraphrase defaults to what they typed. That is a deliberately poor
    paraphrase -- "no idea what that means" is not standalone context -- and the
    resolver is expected to improve it, which is why it may return one.
    """
    return Submission(
        text=text.strip(),
        paraphrase=(paraphrase or text).strip(),
        source="manual",
        version=MANUAL_VERSION,
    )


_PUNCT = re.compile(r"[^a-z0-9]+")


def fingerprint(submission: Submission) -> str:
    """A stable encounter id for a submission that has a place in a transcript.

    Derived from where it happened rather than minted fresh, so re-running the
    detector over a session it has already seen UPDATES that encounter instead
    of creating a second one beside it. Regeneration (PR-26) depends on this: a
    re-run that duplicated every encounter would make improving the detector
    cost the user a growing pile of repeat flags.

    Manual submissions get a fresh id instead -- if you type the same term twice
    you genuinely did encounter it twice, and collapsing those would erase the
    repetition signal S1 exists to preserve.
    """
    key = "|".join(
        [
            submission.session_id or "",
            str(submission.line_start or ""),
            str(submission.line_end or ""),
            _PUNCT.sub(" ", submission.text.casefold()).strip(),
        ]
    )
    return "e-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:26].upper()
