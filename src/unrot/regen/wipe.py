"""Wipe every model-generated thing and start detection over. Dev builds only.

`unrot.regen` re-examines what has already been captured, conservatively: a new
detector version updates encounters and their coverage record, but nothing else
in the graph moves, and an encounter the user has judged is not touched at all.
That is the right default for "the detector got better." It is the wrong tool
for "I changed the prompt six times this afternoon and the store is full of
half-finished experiments I don't trust" -- which is a dev-only problem, and
this module is its answer: discard everything the models produced and rebuild
it from the same raw transcripts, from nothing.

**Which mechanism, and why.** The task this exists for names two respectable
options: a reset/epoch event the compile step honours, or physically deleting
rows. This picks the second, and reuses the idiom `regen._drop_stale` already
established rather than inventing a new one -- an epoch marker would need
`store/compile.py`'s fold to understand a second kind of history boundary
forever after, for a feature that exists in dev builds only. Physical deletion
needs nothing new: `store/events.py` already partitions the log into `user`
events (ground truth, S5's word) and `system` events ("What a regeneration is
free to replace"), `regen.REPLACEABLE` already names a subset of `system`
events safe to delete, and `_drop_stale` already deletes by
`DELETE FROM events WHERE event_id = ?` inside a transaction immediately
followed by `compile_state`. `_protected_encounter_ids` below is the same
"read `compiled_encounters` for `judgment IS NOT NULL`" query `regen.judged_in`
already runs, just across the whole store instead of one session at a time.
This is that same operation, with a wider, explicitly justified guest list --
not a different, riskier kind of write.

**What survives by default, and why.** Judgments, explanations and manual
submissions are the product's ground truth and are kept, matching `regen`'s own
rule for judgments. Concretely:

* An `encounter_recorded` event survives if it carries a judgment (confirmed or
  dismissed -- exactly `regen.judged_in`'s definition of protected) or if it is
  a manual submission (`payload.source == 'manual'`; see
  `resolver/submissions.py` -- the resolver records a manual encounter through
  the same write path as a detected one, so `source` is what tells them apart,
  not `actor`). Losing either would silently drop a verdict the user gave, or
  a term they typed in by hand, which is exactly what "user input survives"
  promises against.
* Concept identity -- `concept_created`, `alias_added`, `alias_removed`,
  `concept_merged`, `resolver_judgment` -- is *also* kept by default, and not
  because it is user input: an `explanation_submitted` event (protected, kept
  by default) names a `concept_id`, and a `resolver_judgment_corrected` event
  (protected, kept by default) names a `resolver_judgment` event as
  `target_event_id`. Deleting concept identity while keeping those would leave
  a user's own explanation pointing at a concept that no longer exists in
  `compiled_concepts` -- an orphan, which is a worse failure than "the wipe did
  less than it could have."

Concept identity, judgments and manual encounters are only discarded when
`discard_user_input=True` retires the events that could be left dangling by
their absence, at which point deleting them too is what makes the result an
actually *empty* store rather than an empty store with a ghost taxonomy in it.

**What is never discarded, not even by `discard_user_input`.** `model_called`
and `detector_ran` are both explicitly documented in `store/events.py` as
exempt from ever being regenerated away, so that spend history and "did the
new model do better than the old one" both stay answerable across every re-run
that has ever happened. A wipe-and-rerun is a bigger regeneration, not a
different kind of one, so the same exemption applies -- wiping the ledger that
exists specifically to survive regenerations would be the one way to make this
operation actively worse than the `regen` it is built on top of.

**Why `explanation_graded` is not wiped by default.** Nothing in the app can
currently re-grade an existing explanation without the user re-submitting it --
there is no "re-grade" route, only "submit", which is graded once on the way
in. `encounter_recorded` and `session_analysed` are safe to discard by default
*because* the `regenerate()` pass this operation triggers refills them
automatically, immediately after. Discarding a grade with no automatic refill
would silently regress a concept from `known` back to `gap` (see
`store/compile.py:derive_state`) with no route back except the user redoing
work that, as far as they can tell, they already did -- exactly the kind of
loss "user input survives by default" exists to prevent, even though a grade
is technically the model's output rather than the user's. So it travels with
`explanation_submitted` into `WIPED_WITH_USER_INPUT` instead of `ALWAYS_WIPED`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..store import compile_state

#: Always discarded, including when nothing else is. Every one of these is
#: refilled automatically by the `regenerate()` pass this operation triggers
#: (`session_analysed`), or costs nothing to lose because the surface already
#: regenerates it on demand with no data loss along the way (material:
#: `POST /api/concepts/{id}/material` is exactly "make this again").
ALWAYS_WIPED = (
    "session_analysed",
    "material_generated",
    "material_delivered",
    "material_refused",
)

#: Discarded only with the explicit opt-in, because each of these is either
#: literal ground truth (a judgment, an explanation, a manual submission) or
#: structural scaffolding that a protected event can still be pointing at
#: (concept identity -- see the module docstring). `explanation_graded` travels
#: with `explanation_submitted` for the reason given above: there is no route
#: back to a grade once it is gone.
WIPED_WITH_USER_INPUT = (
    "encounter_confirmed",
    "encounter_dismissed",
    "encounter_judgment_retracted",
    "explanation_submitted",
    "explanation_graded",
    "resolver_judgment_corrected",
    "concept_created",
    "alias_added",
    "alias_removed",
    "concept_merged",
    "resolver_judgment",
)

# `model_called` and `detector_ran` are deliberately absent from both lists --
# see "What is never discarded" above. Widening either list to include them is
# the kind of change that looks like a more thorough wipe and is actually a
# regression; if that is ever genuinely wanted, it deserves its own explicit
# flag rather than falling out of `discard_user_input`.


@dataclass
class WipeResult:
    """What one wipe actually removed, for the confirmation the app shows after."""

    encounters_removed: int
    other_events_removed: int
    user_input_discarded: bool

    @property
    def total_removed(self) -> int:
        return self.encounters_removed + self.other_events_removed


def wipe(conn: sqlite3.Connection, *, discard_user_input: bool = False) -> WipeResult:
    """Delete every event this operation is willing to delete, then recompile.

    Deliberately a single pass over the whole log rather than session-by-session
    like `regen._drop_stale`: there is no per-session judgment to make here, the
    whole point is "all of it, not just the stale parts", and a full rebuild of
    `compiled_*` is a few milliseconds on a single user's graph (see
    `store/compile.py`). Callers back up the store *before* calling this --
    this function only deletes, it does not protect anything on disk. See
    `store.backup.backup_store`.
    """
    encounters_removed = _wipe_encounters(conn, discard_user_input=discard_user_input)

    types = list(ALWAYS_WIPED)
    if discard_user_input:
        types += list(WIPED_WITH_USER_INPUT)

    other_removed = 0
    for event_type in types:
        cursor = conn.execute("DELETE FROM events WHERE event_type = ?", (event_type,))
        other_removed += cursor.rowcount

    conn.commit()
    compile_state(conn)
    return WipeResult(
        encounters_removed=encounters_removed,
        other_events_removed=other_removed,
        user_input_discarded=discard_user_input,
    )


def _protected_encounter_ids(conn: sqlite3.Connection) -> set[str]:
    """Every encounter carrying a user verdict. `regen.judged_in`, store-wide.

    Reads `compiled_encounters` rather than the raw judgment events so that a
    retraction (`encounter_judgment_retracted`) is honoured automatically: the
    fold already resolves "confirmed, then retracted" to `judgment IS NULL`,
    and re-deriving that here by hand would be a second place to get it wrong.
    """
    return {
        row["encounter_id"]
        for row in conn.execute(
            "SELECT encounter_id FROM compiled_encounters WHERE judgment IS NOT NULL"
        )
    }


def _wipe_encounters(conn: sqlite3.Connection, *, discard_user_input: bool) -> int:
    """Delete `encounter_recorded` events, by default keeping judged and manual ones.

    With `discard_user_input=True` there is nothing left that could dangle --
    the judgments and the manual-submission marker are being discarded in the
    same pass -- so every encounter goes, which is what makes the result an
    actually empty store rather than one with orphaned survivors in it.
    """
    if discard_user_input:
        cursor = conn.execute("DELETE FROM events WHERE event_type = 'encounter_recorded'")
        return cursor.rowcount

    protected = _protected_encounter_ids(conn)
    rows = conn.execute(
        "SELECT event_id, json_extract(payload, '$.encounter_id') AS encounter_id,"
        "       json_extract(payload, '$.source') AS source"
        "  FROM events WHERE event_type = 'encounter_recorded'"
    ).fetchall()
    doomed = [
        row["event_id"]
        for row in rows
        if row["source"] != "manual" and row["encounter_id"] not in protected
    ]
    for event_id in doomed:
        conn.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
    return len(doomed)
