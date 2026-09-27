"""Triage: between the detector finding a term and the resolver filing it.

The detector reads transcripts and nothing else -- it is not allowed to read the
store, which is what keeps it re-runnable over history without the graph it is
building leaking back into what it finds. But "is this a gap for *this* person"
needs the graph. So that question lives here, in its own stage, reading the
store and writing only its own record of what it decided.

**It labels; it does not delete.** Every candidate it judges is written to the
log as `familiarity_judged` -- the ones it held back included -- with the
probability, the cut it was measured against, and the map it was judged on. A
held-back candidate therefore remains a fact in the history rather than a thing
that silently never happened, and a better cut later is a re-reading of stored
numbers, not a re-run.

**Holding back is the expensive mistake.** A candidate passed through that the
person already knew costs one dismissal. A candidate held back that they did
not know is a real gap they never see, and nothing on the surface can tell them
it happened. So the cut is set by the precision of what is held back, not by
overall accuracy: in PR-34 the accuracy-best cut (0.1) held back a real gap
about one time in five; 0.5 held back fewer, and was right 97% of the time.

**It fails open.** If the classifier cannot be reached, the candidate passes
through unjudged and the session is analysed as it would have been before this
stage existed. Nothing is recorded for a judgment that was never made.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from ..detector.candidates import Candidate
from ..resolver import fingerprint, from_candidate, match
from ..store import append
from .familiarity import KnowledgeMap, knowledge_map

TRIAGE_VERSION = "0.2.0"

#: Fewer than this many of *each* side and there is no map to judge against --
#: PR-34 found the no-map question useless for holding anything back.
MIN_EACH = 3

#: With this many of each, the cut is learned from the map itself: every entry
#: is judged against the rest, and the cut is the lowest one whose held-back
#: entries are at least `TARGET_PRECISION` really known. PR-34's version of this
#: ("cut learned on map") took accuracy from 0.69 to 0.82.
CALIBRATE_EACH = 8

#: Of what is held back, how much must really be known.
TARGET_PRECISION = 0.9

#: Used when the map is too small to learn from. The precision-safe end of what
#: PR-34 measured: 97% of what it held back was really known.
DEFAULT_CUT = 0.5

#: One in this many hold-backs is surfaced anyway, as a spot check: "we think
#: you know this -- right?". A held-back candidate is otherwise never seen, so
#: without these nothing could ever show triage hiding a real gap. Chosen by the
#: encounter id rather than at random, so a re-run picks the same ones.
SPOT_CHECK_EVERY = 5

#: At most this many spot checks per session. They sit outside the flag budget,
#: so this is what stops them crowding it.
SPOT_CHECKS_PER_SESSION = 1

#: Once this many spot checks have been answered, their record overrides the
#: map: if fewer than `TARGET_PRECISION` of them were really known, triage is
#: wrong for this person more often than it was built to be, and holds nothing
#: back. The same bar the cut was chosen to meet, now measured in use.
SPOT_CHECK_EVIDENCE = 10

_log = logging.getLogger(__name__)

#: Learned cuts, by map and model. Calibrating costs one call per map entry, and
#: the map only changes when the person judges something -- so a watcher working
#: through a backlog pays for it once, not once per session.
_CUTS: dict[tuple[str, str], tuple[float | None, str]] = {}


def triage_version(model_label: str) -> str:
    return f"triage/{TRIAGE_VERSION}+{model_label}"


@dataclass(frozen=True)
class Verdict:
    candidate: Candidate
    held_back: bool
    #: Why it was or was not judged: 'judged', 'in the graph' (the resolver's
    #: exact match settles it), 'no map', or 'failed'.
    reason: str
    p_knows: float | None = None
    confidence: float | None = None
    #: Would have been held back; surfaced to ask instead. Never `held_back`.
    spot_check: bool = False


@dataclass
class Triage:
    #: What the budget allows through, in the detector's own rank order.
    emitted: list[Candidate]
    verdicts: list[Verdict] = field(default_factory=list)
    cut: float | None = None
    #: 'map' (learned), 'default', 'unreliable' (no cut reached the target, so
    #: nothing is held back), 'spot checks' (answered spot checks say triage is
    #: wrong too often, so nothing is held back), or 'no map'.
    cut_source: str = "no map"
    map_known: int = 0
    map_unknown: int = 0

    @property
    def held_back(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.held_back]

    @property
    def spot_checks(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.spot_check]


def is_spot_check(encounter_id: str) -> bool:
    """Whether this hold-back is one of the one-in-N asked about anyway."""
    import hashlib

    return int(hashlib.sha256(encounter_id.encode()).hexdigest()[:8], 16) % SPOT_CHECK_EVERY == 0


def spot_check_record(conn: sqlite3.Connection) -> tuple[int, int]:
    """(answered, right): spot checks the person has answered, and how many of
    those they said they knew -- which is triage having been right to hold back."""
    row = conn.execute(
        "SELECT count(*) AS answered,"
        "       coalesce(sum(judgment = 'dismissed'), 0) AS known"
        "  FROM compiled_encounters WHERE spot_check = 1 AND judgment IS NOT NULL"
    ).fetchone()
    return row["answered"], row["known"]


def calibrate(kmap: KnowledgeMap, judge) -> tuple[float | None, str]:
    """The cut for this map: learned from it when it is big enough.

    Each entry is judged against the map with itself removed -- the same
    question as a real candidate, asked about something whose answer we already
    have. Returns `(None, 'unreliable')` when no cut is precise enough, which
    means: for this person, do not hold anything back.
    """
    if len(kmap.known) < CALIBRATE_EACH or len(kmap.unknown) < CALIBRATE_EACH:
        return DEFAULT_CUT, "default"

    scored: list[tuple[float, bool]] = []
    for entries, truth in ((kmap.known, True), (kmap.unknown, False)):
        for entry in entries:
            answer = judge(entry.name, entry.gloss, kmap.without(entry))
            scored.append((answer["p_knows"], truth))

    for cut in sorted({p for p, _ in scored}):
        held = [truth for p, truth in scored if p >= cut]
        if held and sum(held) / len(held) >= TARGET_PRECISION:
            return cut, "map"
    return None, "unreliable"


def _cut_for(kmap: KnowledgeMap, judge, cache) -> tuple[float | None, str]:
    key = (kmap.fingerprint, getattr(judge, "model", repr(judge)))
    if cache is not None and key in cache:
        return cache[key]
    cut = calibrate(kmap, judge)
    if cache is not None:
        cache[key] = cut
    return cut


def triage(
    conn: sqlite3.Connection,
    candidates: list[Candidate],
    *,
    judge,
    max_candidates: int,
    model_label: str = "none",
    origin: str = "local",
    cache: dict | None = _CUTS,
) -> Triage:
    """Judge each gap candidate against the person's map, then apply the budget.

    `candidates` are the detector's gaps in rank order, *before* the budget: a
    candidate held back here must not have used up a place another could fill.
    """
    kmap = knowledge_map(conn)
    outcome = Triage(emitted=[], map_known=len(kmap.known), map_unknown=len(kmap.unknown))

    if len(kmap.known) < MIN_EACH or len(kmap.unknown) < MIN_EACH:
        outcome.verdicts = [Verdict(c, False, "no map") for c in candidates]
        outcome.emitted = candidates[:max_candidates]
        return outcome

    try:
        outcome.cut, outcome.cut_source = _cut_for(kmap, judge, cache)
    except Exception as exc:  # noqa: BLE001 - fail open, see module docstring
        _log.warning("familiarity calibration failed, passing everything through: %s", exc)
        outcome.verdicts = [Verdict(c, False, "failed") for c in candidates]
        outcome.emitted = candidates[:max_candidates]
        return outcome

    answered, right = spot_check_record(conn)
    if outcome.cut is not None and answered >= SPOT_CHECK_EVIDENCE and right / answered < TARGET_PRECISION:
        outcome.cut, outcome.cut_source = None, "spot checks"

    known = match.current(conn)
    for candidate in candidates:
        if match.exact(known, candidate.term) is not None:
            # Already a concept, so its state is already a fact about this
            # person. The resolver files the encounter under it; asking a
            # classifier to guess at what the graph already says would only
            # add a way to be wrong.
            outcome.verdicts.append(Verdict(candidate, False, "in the graph"))
            continue
        try:
            answer = judge(candidate.term, candidate.paraphrase, kmap)
        except Exception as exc:  # noqa: BLE001 - fail open
            _log.warning("familiarity check failed for %r: %s", candidate.term, exc)
            outcome.verdicts.append(Verdict(candidate, False, "failed"))
            continue

        p = answer["p_knows"]
        encounter_id = fingerprint(from_candidate(candidate))
        held = outcome.cut is not None and p >= outcome.cut
        spot = (
            held
            and is_spot_check(encounter_id)
            and len(outcome.spot_checks) < SPOT_CHECKS_PER_SESSION
        )
        outcome.verdicts.append(
            Verdict(
                candidate, held and not spot, "judged",
                p_knows=p, confidence=answer.get("confidence"), spot_check=spot,
            )
        )
        append(
            conn,
            "familiarity_judged",
            {
                "term": candidate.term,
                "session_id": candidate.session_id,
                "line_start": candidate.line_start,
                "line_end": candidate.line_end,
                # The id the encounter will carry if it is filed, so a later
                # dismissal or confirmation can be joined back to this number.
                "encounter_id": encounter_id,
                "p_knows": p,
                "confidence": answer.get("confidence"),
                "verdict": "spot_check" if spot else "held_back" if held else "passed",
                "cut": outcome.cut,
                "cut_source": outcome.cut_source,
                "map_known": outcome.map_known,
                "map_unknown": outcome.map_unknown,
                "map_fingerprint": kmap.fingerprint,
                "model": answer.get("model"),
            },
            subject_id=candidate.session_id,
            origin=origin,
            provenance={
                "triage_version": triage_version(model_label),
                "detector_version": candidate.detector_version,
            },
        )

    # The budget is for gaps. A spot check asks a different question -- "were
    # we right that you know this?" -- so it rides alongside rather than
    # taking a gap's place.
    passed = [v.candidate for v in outcome.verdicts if not v.held_back and not v.spot_check]
    outcome.emitted = passed[:max_candidates] + [v.candidate for v in outcome.spot_checks]
    return outcome

