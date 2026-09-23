"""Re-running the detector over history, so improving it improves the past.

S5 resolved that the store is derived and re-runnable, and that regeneration is
*"a first-class operation rather than a one-off migration"*. This is that
operation. Everything else in the codebase has been built assuming it works;
this is where that assumption gets tested rather than asserted.

**The hard constraint: user judgments must survive.** Confirms, dismissals and
explanations are human input, not derived data, and a re-run that wipes them
destroys the only ground truth in the system.

v1 ships the conservative case, deliberately. An encounter the user has judged
is not re-run, not re-paraphrased, not superseded -- it is skipped entirely,
even when the new detector flags the same term at the same lines. Nothing is
rewritten, so no rule is needed about what rewriting would mean, and PR-16 stays
deferred without blocking anything.

Four properties this leans on, all of them built earlier for this moment:

* **Encounter ids are content-derived** (`resolver.fingerprint`), so a term
  re-flagged at the same place keeps its id and the fold collapses the two
  rather than manufacturing a duplicate. Idempotence comes from the id scheme,
  not from a check somebody has to remember to write.
* **The compile step applies judgments by id, after the fold.** A replayed
  `encounter_recorded` gets a freshly minted ULID and therefore sorts *after*
  the confirmation that refers to it -- applying judgments inline would silently
  drop them, which is exactly this rule failing.
* **`session_analysed` records which detector version examined each session**,
  which is what makes a half-finished pass resumable: the sessions still to do
  are the ones not yet stamped with the version being run.
* **Append-only makes a partial pass a shorter log, not corruption.** Work is
  committed per session, so a kill mid-pass leaves a log the compile step still
  folds cleanly. That is relied on deliberately rather than hoped for.

Written as a generator rather than a loop that runs to completion: the caller
drives it, sees progress as it happens, and stops whenever it likes. That is
what "asynchronous, interruptible, observable" reduces to when the alternative
would be a thread and a progress table.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field

from ..detector import detect
from ..resolver import fingerprint, from_candidate, record_analysis, record_detection, resolve

#: Events regeneration is allowed to delete. Both are `system` events, which S5
#: defines as derived and replaceable, and the list is stated here so that
#: widening it is a deliberate edit rather than a side effect of a refactor.
REPLACEABLE = ("encounter_recorded", "session_analysed")


@dataclass
class SessionPass:
    """What one session's re-run did. Yielded as it completes."""

    session_id: str
    #: Encounters left strictly alone because the user had judged them.
    protected: int
    #: Stale derived encounters removed -- including gaps the improved detector
    #: no longer believes in, which is half the point of re-running at all.
    removed: int
    #: Encounters the new pass produced.
    recorded: int
    detector_version: str
    skipped: bool = False   # already at this version; nothing to do


@dataclass
class Progress:
    total: int
    done: int = 0
    protected: int = 0
    removed: int = 0
    recorded: int = 0
    passes: list[SessionPass] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return self.total - self.done


def _sessions_with_turns(raw: sqlite3.Connection) -> list[str]:
    return [
        row["session_id"]
        for row in raw.execute(
            "SELECT s.session_id FROM raw_sessions s"
            " WHERE EXISTS (SELECT 1 FROM raw_turns t WHERE t.session_id = s.session_id"
            "   AND t.role = 'user' AND t.is_meta = 0 AND t.is_sidechain = 0)"
            " ORDER BY s.session_id"
        )
    ]


def already_done(conn: sqlite3.Connection, version: str) -> set[str]:
    """Sessions already examined by this exact detector version.

    Resumption, and also the reason a re-run does not re-pay for work it has
    already done. A different version is a different judgment, so it does not
    count as done.
    """
    return {
        row["session_id"]
        for row in conn.execute(
            "SELECT session_id FROM compiled_sessions WHERE detector_version = ?",
            (version,),
        )
    }


@dataclass
class Plan:
    """What a run would do, computed without calling a model."""

    detector_version: str
    captured: int
    already_done: int
    #: Sessions a run would re-examine, in the order it would take them.
    to_run: list[str]
    #: Encounters carrying a user judgment. None of them will be touched.
    protected: int


def plan(conn: sqlite3.Connection, raw: sqlite3.Connection, *, model_label: str) -> Plan:
    """The preview: the CLI's `plan` and the app's regeneration pane both read this.

    One function, so the preview a user approves and the run that follows it
    count the same things.
    """
    from ..detector import detector_version as version_of

    version = version_of(model_label)
    sessions = _sessions_with_turns(raw)
    done = already_done(conn, version)
    protected = conn.execute(
        "SELECT count(*) FROM compiled_encounters WHERE judgment IS NOT NULL"
    ).fetchone()[0]
    return Plan(
        detector_version=version,
        captured=len(sessions),
        already_done=len(done & set(sessions)),
        to_run=[s for s in sessions if s not in done],
        protected=protected,
    )


def judged_in(conn: sqlite3.Connection, session_id: str) -> set[str]:
    """Encounter ids from this session that carry a user judgment.

    The protected set. Everything here is left byte-identical: same paraphrase,
    same pointer, same grade, same everything.
    """
    return {
        row["encounter_id"]
        for row in conn.execute(
            "SELECT encounter_id FROM compiled_encounters"
            " WHERE session_id = ? AND judgment IS NOT NULL",
            (session_id,),
        )
    }


def _drop_stale(conn: sqlite3.Connection, session_id: str, protected: set[str]) -> int:
    """Remove derived encounters for this session that no user judgment defends.

    Deleting rather than superseding is what lets a re-run *retract* a gap the
    improved detector no longer believes in. A regeneration that could only ever
    add would make the detector's false positives permanent, which is the
    opposite of what re-runnability is for.
    """
    rows = conn.execute(
        "SELECT event_id, json_extract(payload, '$.encounter_id') AS encounter_id"
        "  FROM events"
        " WHERE event_type = 'encounter_recorded'"
        "   AND json_extract(payload, '$.pointer.session_id') = ?",
        (session_id,),
    ).fetchall()

    doomed = [r["event_id"] for r in rows if r["encounter_id"] not in protected]
    for event_id in doomed:
        conn.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
    return len(doomed)


def regenerate(
    conn: sqlite3.Connection,
    raw: sqlite3.Connection,
    *,
    propose,
    decide,
    model_label: str,
    sessions: list[str] | None = None,
    max_candidates: int = 2,
    force: bool = False,
    meter=None,
) -> Iterator[SessionPass]:
    """Re-run the detector over history, one session at a time.

    Yields after each session, with that session's work already committed. The
    caller can stop at any yield and the log is left foldable -- which is the
    whole of "interruptible and resumable" and costs nothing beyond committing
    in the right place.

    With a `meter`, each session's model calls are attributed to it and written
    down with the session's commit. A session that fails leaves its calls in
    the meter; the caller flushes them.
    """
    from ..detector import detector_version as version_of
    from ..store import compile_state

    version = version_of(model_label)
    targets = sessions if sessions is not None else _sessions_with_turns(raw)
    done = set() if force else already_done(conn, version)

    for session_id in targets:
        if session_id in done:
            yield SessionPass(session_id, 0, 0, 0, version, skipped=True)
            continue

        protected = judged_in(conn, session_id)
        removed = _drop_stale(conn, session_id, protected)
        conn.execute(
            "DELETE FROM events WHERE event_type = 'session_analysed'"
            "   AND json_extract(payload, '$.session_id') = ?",
            (session_id,),
        )
        conn.commit()
        compile_state(conn)

        with meter.about(session_id=session_id) if meter else nullcontext():
            result = detect(
                raw,
                session_id,
                propose=propose,
                model_label=model_label,
                max_candidates=max_candidates,
            )

            recorded = 0
            for candidate in result.emitted:
                submission = from_candidate(candidate)
                # The conservative rule, at the one line where it is enforced. The
                # new detector may well flag this same term at these same lines --
                # and it is still not allowed to touch it, because the user has
                # already said something about it and a fresh `encounter_recorded`
                # would supersede the paraphrase they judged.
                if fingerprint(submission) in protected:
                    continue
                resolve(
                    conn,
                    submission,
                    decide=decide,
                    model_label=model_label,
                    recompile=False,
                )
                recorded += 1

        # Not in REPLACEABLE: the next pass adds its own run beside this one,
        # so the log keeps what every detector version said about the session.
        record_detection(conn, result, max_candidates=max_candidates)
        record_analysis(
            conn,
            session_id,
            candidates_found=len(result.emitted),
            detector_version=result.detector_version,
            recompile=False,
        )
        conn.commit()
        if meter is not None:
            meter.flush(conn)
        compile_state(conn)

        yield SessionPass(
            session_id=session_id,
            protected=len(protected),
            removed=removed,
            recorded=recorded,
            detector_version=version,
        )


def run(
    conn: sqlite3.Connection,
    raw: sqlite3.Connection,
    *,
    on_progress=None,
    **kwargs,
) -> Progress:
    """Drive a regeneration to completion, reporting as it goes.

    The convenience wrapper. Anything that wants to stop early, or to stay
    responsive while this runs, should iterate `regenerate` itself rather than
    calling this.
    """
    targets = kwargs.get("sessions")
    total = len(targets) if targets is not None else len(_sessions_with_turns(raw))
    progress = Progress(total=total)

    for result in regenerate(conn, raw, **kwargs):
        progress.done += 1
        progress.protected += result.protected
        progress.removed += result.removed
        progress.recorded += result.recorded
        progress.passes.append(result)
        if on_progress is not None:
            on_progress(progress, result)
    return progress
