"""unrot's backend. A JSON API over the event log, plus the built frontend.

This is the *separate product surface* the PRD asks for: its own UI, its own
backend, reaching into Claude Code for nothing. Capture reads transcripts off
disk; nothing here talks to an agent, and no route writes into `~/.claude`.

Two things it is careful about:

* **Every write goes through `events.append` and is followed by a recompile.**
  Compiled state is a pure function of the log, so "write then re-fold" is the
  only sequence that cannot leave the two disagreeing. The fold is a full
  rebuild and takes milliseconds on a single user's graph.
* **A connection per request, opened inside the endpoint.** SQLite connections
  belong to the thread that made them, and FastAPI dispatches dependencies and
  path operations to the threadpool separately -- so the tidy-looking version of
  this, a `Depends`-injected connection, breaks under uvicorn while passing
  every test. See `stores()`.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..capture import paths
from ..material import TEXTUAL
from ..model import describe_failure
from ..store import compile_state
from ..store.__main__ import open_raw, open_store
from . import read
from .schemas import (
    CaptureOut,
    CheckOut,
    ConceptOut,
    CorrectedOut,
    EncounterOut,
    ExportFileOut,
    ExportPreviewOut,
    ExplanationOut,
    GradedOut,
    HealthOut,
    MadeOut,
    MaterialOut,
    JudgmentOut,
    MomentOut,
    MomentTurn,
    AnalysedOut,
    CapturedOut,
    CaptureResultOut,
    ConfigOut,
    DevWipeOut,
    DevWipePlanOut,
    EstimateOut,
    PerSessionOut,
    RegenPassOut,
    RegenPlanOut,
    PendingOut,
    QueueOut,
    SpendDayOut,
    SpendOut,
    SpendPartOut,
    SpendTotalOut,
    SubmittedOut,
    SurfaceOut,
)

def _ui_dist() -> Path:
    """Where `npm run build` puts the frontend.

    Present in a built checkout, absent in development -- where Vite serves the
    app instead and proxies here -- and absent again inside the frozen sidecar,
    which deliberately does not carry the web bundle: the Mac app is the surface
    there. Walking up from `__file__` lands somewhere arbitrary inside a
    PyInstaller bundle, so the frozen case is answered explicitly rather than
    left to produce a path that happens not to exist for the wrong reason.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "ui" / "dist"
    return Path(__file__).resolve().parents[3] / "ui" / "dist"


UI_DIST = _ui_dist()

#: Vite's dev server. Same-origin in production (the API serves the bundle), so
#: this only matters while developing.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


#: What each judgment route appends. `retract` is the undo behind quick accept,
#: and it is a third event rather than a delete of the first: the log keeps the
#: mis-key and the correction, and the fold reads whichever came last.
JUDGMENT_EVENTS = {
    "confirm": "encounter_confirmed",
    "dismiss": "encounter_dismissed",
    "retract": "encounter_judgment_retracted",
}


def _home() -> str | None:
    """Resolved per request so $UNROT_HOME can be changed without a restart."""
    return None


@contextmanager
def stores() -> Iterator[tuple[sqlite3.Connection, sqlite3.Connection | None]]:
    """Both stores, opened and closed entirely inside the calling thread.

    Deliberately NOT a FastAPI dependency, which is the obvious way to write
    this and is wrong. Sync dependencies and sync path operations are each
    dispatched to the threadpool separately, and anyio does not promise the same
    worker thread for both -- so a connection opened in a dependency and used in
    the endpoint trips SQLite's same-thread check under uvicorn. It does not trip
    under `TestClient`, which is why this cannot be left to the test suite to
    notice.

    The alternative fix is `check_same_thread=False`, which works by switching
    off the check that caught it. Keeping the whole life of a connection on one
    thread means there is nothing to switch off.
    """
    conn = open_store(_home())
    raw = open_raw(_home())
    try:
        yield conn, raw
    finally:
        conn.close()
        if raw is not None:
            raw.close()


def _encounter_out(encounter: read.Encounter) -> EncounterOut:
    return EncounterOut(**vars(encounter))


def _concept_out(concept: read.Concept) -> ConceptOut:
    data = vars(concept) | {
        "encounters": [_encounter_out(e) for e in concept.encounters],
        "explanations": [ExplanationOut(**vars(x)) for x in concept.explanations],
        "material": [MaterialOut(**vars(m)) for m in concept.material],
        "unjudged": concept.unjudged,
    }
    return ConceptOut(**data)


def _build_grader(meter=None):
    """The grading callable, or the offline fallback, and which one it is.

    Never raises. A missing key must not stop an explanation being stored --
    what the user wrote is the irreplaceable half, and grading is the part that
    can be redone later from it.
    """
    from ..grader import DEFAULT_JEV_MODEL, build_jev_grader, keyword_grader
    from ..model import ModelConfig

    config = ModelConfig.from_env()
    if not config.api_key:
        return keyword_grader, "keyword", False
    try:
        # A classifier rather than a reasoning model: this question is "pick one
        # of three and say how sure you are", which is the shape it is built
        # for. It also returns the distribution, which is what makes the
        # listed/causal threshold movable later without re-grading anything.
        return (
            build_jev_grader(config, meter=meter),
            DEFAULT_JEV_MODEL.replace("/", "-"),
            True,
        )
    except Exception:  # pragma: no cover - network/config problems at build time
        return keyword_grader, "keyword", False


#: The most a capture will accept. "Add to unrot" is for a term met somewhere,
#: and the Services menu hands over whatever is selected -- which can be a whole
#: page. Refusing past this keeps "unrot receives the selection and nothing
#: else" meaning something: a selection this long is a document, not a term.
MAX_CAPTURE = 600


class _ModelUnavailable(Exception):
    """The decider was asked and could not answer."""


def _build_decider(meter=None):
    """The resolver's decider and its label, or the string-only one.

    Mirrors `unrot.resolver`'s own choice, so the CLI and the app resolve a
    capture the same way. Never raises.
    """
    from ..model import ModelConfig
    from ..resolver import build_decider, strict

    config = ModelConfig.from_env()
    if not config.api_key:
        return strict, "none"
    try:
        return build_decider(config, meter=meter), config.label
    except Exception:  # pragma: no cover - config problems at build time
        return strict, "none"


class _NoModel(Exception):
    """Analysis was asked for and there is no model to do it."""


def _build_analyser(meter=None):
    """The detector's proposer and the resolver's decider, with their labels.

    Raises `_NoModel` rather than degrading: detection is one model call per
    window and has no string-only fallback, so without a key there is nothing
    honest to run. Capture still works -- it never needs a model.
    """
    from ..detector import build_proposer
    from ..model import ModelConfig
    from ..resolver import build_decider

    config = ModelConfig.from_env()
    if not config.api_key:
        raise _NoModel("no model is configured, so nothing can be analysed")
    try:
        return (
            build_proposer(config, meter=meter),
            build_decider(config, meter=meter),
            config.label,
            config.label,
        )
    except RuntimeError as exc:
        raise _NoModel(str(exc)) from exc


def _can_analyse() -> bool:
    from ..model import ModelConfig

    return bool(ModelConfig.from_env().api_key)


def _dev_mode() -> bool:
    """Whether this core was started with dev features on.

    Set only by the Mac app, only in a dev build (`UNROT_CHANNEL=dev`, the
    `DEV_FEATURES` Swift condition) -- see `UnrotMacApp.swift`'s
    `environmentProvider`. Not the same question as "is this build of the app
    a dev build": the app and the core are separate processes, `wipe` is a core
    operation reachable by anyone who can open this socket, and a CLI or bare
    `python -m unrot.api` run gets this off by default, which is the only safe
    default for an operation that can discard real data. Resolved from the
    environment on every call rather than cached at startup, matching `_home`
    and `ModelConfig.from_env` -- nothing here assumes the process was launched
    by the one supervisor that knows to set it.
    """
    return os.environ.get("UNROT_DEV_FEATURES") == "1"


#: What leaves this machine when the endpoint is not local, one line per kind of
#: call. Stated here, beside the code that makes the calls, rather than in a
#: settings screen that would have to be kept in step with it by hand.
LEAVES_THIS_MAC = [
    "Detection: the stretches of a session being analysed -- what you typed and"
    " what the assistant said back -- a window at a time. Never whole transcripts"
    " at once, and never tool output.",
    "Resolution: a candidate term and its one-line paraphrase, with the names of"
    " concepts it might match.",
    "The check: your answer and the question it was given to, for grading.",
    "Material: the concept's name and its paraphrases, to write from and to"
    " search for sources.",
]


def _total_out(total, *, local: bool = False) -> SpendTotalOut:
    """A spend total for the wire.

    With no calls at all, a local endpoint still says "local, no cost" rather
    than "$0.00": the empty week is not a price that rounded down.
    """
    data = total.as_dict()
    if not total.calls and local:
        data["text"] = "local, no cost"
    return SpendTotalOut(**data)


def _parts_out(by_purpose: dict) -> list[SpendPartOut]:
    from ..spend import PURPOSES

    return [
        SpendPartOut(purpose=purpose, label=PURPOSES.get(purpose, purpose), total=_total_out(t))
        for purpose, t in by_purpose.items()
    ]


#: Sessions being analysed right now, so the same one is never run twice at
#: once -- the watcher and a click on "Analyse now" can both ask. In-process is
#: enough: there is one core per socket, and `prepare_socket` makes sure of it.
_ANALYSING: set[str] = set()

#: uvicorn's own logger, so a failure lands in the same stream as the request
#: lines -- `run/core.log` under the Mac app -- rather than only in a response
#: body the user may never see.
_log = logging.getLogger("uvicorn.error")
_ANALYSING_LOCK = threading.Lock()


def create_app() -> FastAPI:
    app = FastAPI(
        title="unrot",
        summary="The gap list, and the judgments you make about it.",
        version=__version__,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthOut)
    def health() -> HealthOut:
        """Cheap liveness, polled by the Mac app's supervisor.

        Deliberately does everything `surface` does not: it opens the stores and
        closes them, and touches nothing else. No `compile_state`, no grader
        construction, no model config. Those are what make `/api/surface` cost
        something, and a supervisor polling this every second or two must not be
        paying that -- a health check that re-folds the log is a health check
        that manufactures the load it exists to report.
        """
        from ..store.db import COMPILED_SCHEMA

        root = paths.home(_home())
        with stores() as (_conn, raw):
            raw_open = raw is not None
        return HealthOut(
            version=__version__,
            compiled_schema=COMPILED_SCHEMA,
            raw_open=raw_open,
            store_path=str(paths.store_db_path(root)),
        )

    @app.get("/api/surface", response_model=SurfaceOut)
    def surface() -> SurfaceOut:
        """Everything the first paint needs: which state we are in, and the list.

        One request rather than two, so the frontend never has to render a list
        and a status that disagree about whether anything was found.
        """
        with stores() as (conn, raw):
            found = read.concepts(conn, _home())
            state = read.surface(conn, raw, found)
        return SurfaceOut(
            **vars(state)
            | {
                "capture": CaptureOut(**vars(state.capture)),
                "concepts": [_concept_out(c) for c in found],
            }
        )

    @app.post("/api/encounters/{encounter_id}/{judgment}", response_model=JudgmentOut)
    def judge(encounter_id: str, judgment: str) -> JudgmentOut:
        """Record what the user said about a flag. Ground truth; never replayed.

        `confirmed` means "I genuinely did not know this", and deliberately does
        NOT close the gap -- it is the beginning of learning it. `dismissed`
        means the flag was wrong, and per journey 2 that is signal for tuning the
        detector rather than something to throw away, which is why it is an event
        in the same log rather than a deletion.
        """
        event = JUDGMENT_EVENTS.get(judgment)
        if event is None:
            raise HTTPException(400, f"unknown judgment {judgment!r}")

        from ..store import append

        with stores() as (conn, raw):
            current = conn.execute(
                "SELECT judgment FROM compiled_encounters WHERE encounter_id = ?",
                (encounter_id,),
            ).fetchone()
            if not current:
                raise HTTPException(404, f"no encounter {encounter_id!r}")
            # An undo with nothing to undo is refused rather than recorded. It
            # would be harmless to the fold and noise in the log, and a client
            # sending one has lost track of what it just did.
            if judgment == "retract" and current["judgment"] is None:
                raise HTTPException(409, "nothing to undo: this has no judgment")

            append(conn, event, {"encounter_id": encounter_id})
            compile_state(conn)

            found = read.concepts(conn, _home())
            state = read.surface(conn, raw, found).state

        moved = next(
            (c for c in found if any(e.encounter_id == encounter_id for e in c.encounters)),
            None,
        )
        return JudgmentOut(
            encounter_id=encounter_id,
            concept=_concept_out(moved) if moved else None,
            surface=state,
            counts={b: sum(1 for c in found if c.bucket == b) for b in read.BUCKETS},
        )

    @app.get("/api/concepts/{concept_id}/check", response_model=CheckOut)
    def check(concept_id: str) -> CheckOut:
        """The question to ask, worded as it will be stored.

        Handed to the frontend rather than composed there, so the words on
        screen and the words recorded against the answer cannot drift apart --
        which would silently break the one thing that makes re-grading valid.
        """
        from ..grader import check_version, question_for

        with stores() as (conn, _raw):
            row = conn.execute(
                "SELECT canonical_name FROM compiled_concepts WHERE concept_id = ?",
                (concept_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, f"no concept {concept_id!r}")
            asked = question_for(conn, concept_id)
        return CheckOut(
            concept_id=concept_id,
            name=row["canonical_name"],
            prompt_text=asked,
            prompt_version=check_version(),
        )

    @app.post("/api/concepts/{concept_id}/explanation", response_model=GradedOut)
    def explain(concept_id: str, body: dict) -> GradedOut:
        """Store what the user wrote, then grade it. In that order, always.

        The explanation is the half that cannot be reconstructed -- they typed it
        once and will not type it again. Grading is a network call that can fail.
        So the submission is committed first and grading is attempted after; if
        it fails, the answer survives ungraded and can be graded later from
        exactly the same raw text.
        """
        from ..grader import grade as grade_one
        from ..grader import submit

        text = str((body or {}).get("text") or "").strip()
        if not text:
            raise HTTPException(400, "an empty explanation is not an answer")

        from ..spend import Meter

        meter = Meter()
        grade_fn, model_label, live = _build_grader(meter)

        with stores() as (conn, _raw):
            try:
                explanation_id = submit(conn, concept_id, text)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc

            graded = True
            try:
                with meter.about(concept_id=concept_id):
                    grade_one(conn, explanation_id, grade_fn=grade_fn, model_label=model_label)
            except Exception:
                # The answer is safely stored; the level is not. Reported as
                # ungraded rather than as a failure, because the user has not
                # lost anything and must not be told they failed a check that
                # never ran.
                graded = False
                compile_state(conn)
            finally:
                meter.flush(conn)

            found = read.concepts(conn, _home())
            row = conn.execute(
                "SELECT * FROM compiled_explanations WHERE explanation_id = ?",
                (explanation_id,),
            ).fetchone()

        concept = next((c for c in found if c.concept_id == concept_id), None)
        return GradedOut(
            explanation=ExplanationOut(
                explanation_id=row["explanation_id"],
                raw_text=row["raw_text"],
                prompt_text=row["prompt_text"],
                prompt_version=row["prompt_version"],
                submitted_at=row["submitted_at"],
                level=row["level"],
                reasoning=row["reasoning"],
                probabilities=json.loads(row["probabilities"])
                if row["probabilities"]
                else None,
                confidence=row["confidence"],
                grader_version=row["grader_version"],
            ),
            concept=_concept_out(concept) if concept else None,
            counts={b: sum(1 for c in found if c.bucket == b) for b in read.BUCKETS},
            graded=graded and live,
        )

    @app.post("/api/concepts/{concept_id}/material", response_model=MadeOut)
    def make(concept_id: str, format: str = TEXTUAL) -> MadeOut:
        """Generate material for a gap, in the requested format, and deliver it.

        Never automatic. The gap graph is the product and this is a pluggable
        output stage on top of it, so material exists only when it is asked for
        -- which is also what makes "turn it off" a real option rather than a
        setting that has to be honoured in ten places.
        """
        from ..grader import build_jev_grader  # noqa: F401  (key presence check)
        from ..material import (
            NotGrounded,
            SOURCES_ONLY,
            TEXTUAL as TEXTUAL_FORMAT,
            WouldRecurse,
            build_search,
            build_writer,
            deliver,
            gather,
            record_refusal,
            sources_only,
            textual,
        )
        from ..model import ModelConfig
        from ..resolver import resolve_reference, strict
        from ..spend import Meter

        if format not in (TEXTUAL_FORMAT, SOURCES_ONLY):
            raise HTTPException(400, f"unknown format {format!r}")

        config = ModelConfig.from_env()
        meter = Meter()
        search = build_search(config, meter=meter) if config.api_key else None

        with stores() as (conn, raw), meter.about(concept_id=concept_id):
            try:
                found = gather(conn, raw, concept_id, search=search)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
            finally:
                meter.flush(conn)

            if format != SOURCES_ONLY and not config.api_key:
                # Configuration, not the writer's judgment, so not recorded
                # as a refusal: it would count against a model that never ran.
                raise HTTPException(
                    422,
                    "no model configured, so nothing can be written."
                    " The sources-only format still works.",
                )
            try:
                if format == SOURCES_ONLY:
                    made = sources_only(conn, concept_id, found)
                else:
                    decide = strict
                    made = textual(
                        conn,
                        concept_id,
                        found,
                        write=build_writer(config, meter=meter),
                        resolve_named=lambda term: resolve_reference(
                            conn, term, decide=decide, recompile=False
                        ),
                        model_label=config.label,
                    )
            except WouldRecurse as exc:
                record_refusal(conn, concept_id, format, exc, model_label=config.label)
                raise HTTPException(409, str(exc)) from exc
            except NotGrounded as exc:
                record_refusal(conn, concept_id, format, exc, model_label=config.label)
                # 422 rather than 500: refusing to write something unsupported
                # is the gate working, not the server failing.
                raise HTTPException(422, str(exc)) from exc
            finally:
                # A refused or failed write was still a billed call.
                meter.flush(conn)

            deliver(conn, made.material_id)
            concepts = read.concepts(conn, _home())
            row = conn.execute(
                "SELECT * FROM compiled_material WHERE material_id = ?",
                (made.material_id,),
            ).fetchone()

        concept = next((c for c in concepts if c.concept_id == concept_id), None)
        return MadeOut(
            material=MaterialOut(
                material_id=row["material_id"],
                format=row["format"],
                body=row["body"],
                sources=json.loads(row["sources"] or "[]"),
                generated_at=row["generated_at"],
                delivered_at=row["delivered_at"],
            ),
            concept=_concept_out(concept) if concept else None,
            counts={b: sum(1 for c in concepts if c.bucket == b) for b in read.BUCKETS},
        )

    @app.get("/api/encounters/{encounter_id}/moment", response_model=MomentOut)
    def moment(encounter_id: str) -> MomentOut:
        """Replay the point of acceptance rather than abstracting it into a flag.

        S4's local layer. The paraphrase is what travels; this is what is only
        available at the machine that produced it, and it resolves against
        unrot's own retained copy rather than `~/.claude`, which may have rotated
        the original away.
        """
        with stores() as (conn, raw):
            row = conn.execute(
                "SELECT session_id, line_start, line_end FROM compiled_encounters"
                " WHERE encounter_id = ?",
                (encounter_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"no encounter {encounter_id!r}")
            if not row["session_id"]:
                return MomentOut(
                    encounter_id=encounter_id,
                    resolvable=False,
                    reason="This gap has no transcript pointer -- it was submitted by hand.",
                    turns=[],
                )

            copy = paths.copy_path(paths.home(_home()), row["session_id"])
            if not copy.exists():
                return MomentOut(
                    encounter_id=encounter_id,
                    resolvable=False,
                    reason="No retained copy of this session on this machine.",
                    turns=[],
                )

            turns = read.moment(
                raw, row["session_id"], row["line_start"] or 1, row["line_end"] or 1
            )
            line_start, line_end = row["line_start"], row["line_end"]
            session_id = row["session_id"]

        return MomentOut(
            encounter_id=encounter_id,
            resolvable=bool(turns),
            reason=None
            if turns
            else "Nothing a person actually typed in that range -- it was all"
            " tool traffic and injected text.",
            session_id=session_id,
            line_start=line_start,
            line_end=line_end,
            turns=[MomentTurn(**t) for t in turns],
        )

    @app.post("/api/submissions", response_model=SubmittedOut)
    def submit_gap(body: dict) -> SubmittedOut:
        """A term met outside a session, through the same resolver as everything else.

        Journey 10's other half: the wiki, the thread, the PDF, which capture
        will never see. It lands as a `manual` encounter, identical in the graph
        to a detected one -- `source` is provenance, not ranking.

        Only the selection comes in. `seen_in` is an app or document title the
        user can switch off before sending; it is folded into the paraphrase,
        which is the layer that has to stand alone, and nowhere else.
        """
        from ..resolver import Unresolvable, manual, resolve, strict

        body = body or {}
        text = " ".join(str(body.get("text") or "").split())
        if not text:
            raise HTTPException(400, "nothing was selected")
        if len(text) > MAX_CAPTURE:
            raise HTTPException(
                400,
                f"that selection is {len(text)} characters -- select the term,"
                " not the passage around it",
            )
        own_words = " ".join(str(body.get("paraphrase") or "").split())
        seen_in = " ".join(str(body.get("seen_in") or "").split())
        paraphrase = own_words or text
        if seen_in:
            paraphrase = f"{paraphrase} (seen in {seen_in})"

        from ..spend import Meter

        meter = Meter()
        real, label = _build_decider(meter)

        def guarded(submission, shortlist):
            # The decider runs before `resolve` appends anything, so a failure
            # here leaves the log untouched and the capture can be re-resolved
            # without a model -- with provenance that says so, rather than a
            # string-only decision recorded under a model's name.
            try:
                return real(submission, shortlist)
            except Exception as exc:
                raise _ModelUnavailable(f"{type(exc).__name__}: {exc}") from exc

        submission = manual(text, paraphrase)
        unavailable = None
        with stores() as (conn, _raw):
            try:
                resolution = resolve(conn, submission, decide=guarded, model_label=label)
            except Unresolvable as exc:
                # 422 rather than 400: the request was well formed, and the
                # resolver read it and found nothing to file. Nothing was written.
                raise HTTPException(422, str(exc)) from exc
            except _ModelUnavailable as exc:
                resolution = resolve(conn, submission, decide=strict, model_label="none")
                unavailable = (
                    "The model could not be reached, so this was filed as new"
                    f" without checking for near-duplicates. ({exc})"
                )
            finally:
                meter.flush(conn)
            found = read.concepts(conn, _home())

        concept = next((c for c in found if c.concept_id == resolution.concept_id), None)
        return SubmittedOut(
            encounter_id=resolution.encounter_id,
            concept_id=resolution.concept_id,
            canonical_name=resolution.canonical_name,
            decision=resolution.decision,
            reasoning=resolution.reasoning,
            judgment_event_id=resolution.judgment_event_id,
            decided_without_model=resolution.decided_without_model or label == "none",
            model_unavailable=unavailable,
            concept=_concept_out(concept) if concept else None,
            counts={b: sum(1 for c in found if c.bucket == b) for b in read.BUCKETS},
        )

    @app.post("/api/judgments/{event_id}/correct", response_model=CorrectedOut)
    def correct_judgment(event_id: str, body: dict) -> CorrectedOut:
        """Argue with the resolver, and optionally repair what it did.

        Journey 19. The correction is a `user` event -- ground truth, never
        replayed over. `split_encounter` is "No, this is new": the encounter
        gets a concept of its own. `merge_into` is the opposite repair.
        """
        from ..resolver import correct

        body = body or {}
        reasoning = " ".join(str(body.get("reasoning") or "").split()) or "Corrected by hand."
        split = body.get("split_encounter") or None
        merge_into = body.get("merge_into") or None

        with stores() as (conn, _raw):
            try:
                correction = correct(
                    conn,
                    event_id,
                    reasoning=reasoning,
                    split_out=split,
                    merge_into=merge_into,
                )
            except ValueError as exc:
                raise HTTPException(
                    404 if str(exc).startswith("no ") else 409, str(exc)
                ) from exc

            moved = None
            if split:
                row = conn.execute(
                    "SELECT concept_id FROM compiled_encounters WHERE encounter_id = ?",
                    (split,),
                ).fetchone()
                moved = row["concept_id"] if row else None
            elif merge_into:
                moved = merge_into
            found = read.concepts(conn, _home())

        concept = next((c for c in found if c.concept_id == moved), None)
        return CorrectedOut(
            correction_event_id=correction,
            concept=_concept_out(concept) if concept else None,
            counts={b: sum(1 for c in found if c.bucket == b) for b in read.BUCKETS},
        )

    @app.get("/api/queue", response_model=QueueOut)
    def queue() -> QueueOut:
        """Captured sessions not yet analysed, or grown since they were.

        Derived from the two stores rather than kept anywhere, which is what lets
        it survive a relaunch with nothing to persist.
        """
        from ..analyse import pending
        from ..model import ModelConfig
        from ..spend import Total, calls, estimate, week_ago

        config = ModelConfig.from_env()
        can = _can_analyse()
        with stores() as (conn, raw):
            waiting = pending(conn, raw)
            guess = (
                estimate(conn, model=config.model, sessions=len(waiting), local=config.local)
                if waiting and can
                else None
            )
            week = Total.of(calls(conn, since=week_ago()))
        with _ANALYSING_LOCK:
            running = sorted(_ANALYSING)
        return QueueOut(
            pending=[PendingOut(**vars(p)) for p in waiting],
            can_analyse=can,
            analysing=running,
            estimate=EstimateOut(**guess.as_dict()) if guess else None,
            spent_this_week=_total_out(week, local=config.local),
        )

    @app.get("/api/spend", response_model=SpendOut)
    def spend(since: str | None = None) -> SpendOut:
        """What model calls have cost: this week, since install, and per session.

        Read straight off the log, with the same functions `store metrics`
        uses, so the app and the CLI cannot disagree. `since` adds a window
        from that instant until now -- the running total of a batch.
        """
        from datetime import datetime, timedelta, timezone

        from ..model import ModelConfig
        from ..spend import Total, calls, summarise, week_ago

        start = None
        if since:
            try:
                start = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(400, f"could not read {since!r} as a time") from exc
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)

        config = ModelConfig.from_env()
        with stores() as (conn, _raw):
            week = summarise(conn, since=week_ago())
            ever = summarise(conn)
            window = Total.of(calls(conn, since=start)) if start else None

        fortnight = (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat()
        return SpendOut(
            model=config.model,
            local=config.local,
            week=_total_out(week.total, local=config.local),
            week_by_purpose=_parts_out(week.by_purpose),
            all_time=_total_out(ever.total, local=config.local),
            all_time_by_purpose=_parts_out(ever.by_purpose),
            by_day=[
                SpendDayOut(day=day, total=_total_out(total))
                for day, total in ever.by_day.items()
                if day >= fortnight
            ],
            per_session=[PerSessionOut(**row.as_dict()) for row in ever.per_session],
            window=_total_out(window, local=config.local) if window is not None else None,
        )

    @app.post("/api/capture", response_model=CaptureResultOut)
    def capture(body: dict) -> CaptureResultOut:
        """Copy named transcripts into the raw store. No model, no cost.

        The watcher calls this once a session has gone quiet. It reads only from
        Claude Code's projects directory: a path anywhere else is refused before
        anything is opened, symlinks resolved first, because a socket that could
        be pointed at any file on the machine would be a way to read it.
        """
        from ..analyse import pending
        from ..capture import connect as connect_raw
        from ..capture.ingest import IngestResult, ingest_file

        requested = (body or {}).get("paths") or []
        if not isinstance(requested, list) or not requested:
            raise HTTPException(400, "name the transcripts to capture")

        allowed = paths.CLAUDE_PROJECTS.resolve()
        sources = []
        for item in requested:
            source = Path(str(item)).expanduser().resolve()
            if source.suffix != ".jsonl" or not source.is_relative_to(allowed):
                raise HTTPException(400, f"not a Claude Code transcript: {item}")
            sources.append(source)

        home = paths.home(_home())
        raw = connect_raw(home)
        try:
            results = []
            for source in sources:
                try:
                    results.append(ingest_file(source, conn=raw, root=home))
                except OSError as exc:
                    # One unreadable file must not abort the rest.
                    results.append(IngestResult(source.stem, f"error: {type(exc).__name__}", 0, 0, 0, 0))
        finally:
            raw.close()

        with stores() as (conn, raw_ro):
            waiting = len(pending(conn, raw_ro))
        return CaptureResultOut(
            captured=[
                CapturedOut(session_id=r.session_id, status=r.status, turns_added=r.turns_added)
                for r in results
            ],
            pending=waiting,
        )

    @app.post("/api/analyse", response_model=AnalysedOut)
    def analyse(body: dict) -> AnalysedOut:
        """Detect and resolve one captured session. This is the call that costs.

        One session per request, so the caller can show progress between them,
        pause between them, and never has a single request outlive its patience.
        Nothing here decides whether to run -- the watcher asks only when the user
        has switched automatic analysis on, or clicked "Analyse now".
        """
        from ..analyse import analyse_session
        from ..spend import Meter

        session_id = str((body or {}).get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(400, "name a session to analyse")

        meter = Meter()
        try:
            propose, decide, detector_label, resolver_label = _build_analyser(meter)
        except _NoModel as exc:
            raise HTTPException(409, str(exc)) from exc

        with _ANALYSING_LOCK:
            if session_id in _ANALYSING:
                raise HTTPException(409, f"{session_id} is already being analysed")
            _ANALYSING.add(session_id)
        try:
            with stores() as (conn, raw):
                if raw is None or not raw.execute(
                    "SELECT 1 FROM raw_sessions WHERE session_id = ?", (session_id,)
                ).fetchone():
                    raise HTTPException(404, f"{session_id} has not been captured")
                started = time.monotonic()
                _log.info("analysing %s with %s", session_id, detector_label)
                try:
                    with meter.about(session_id=session_id):
                        result = analyse_session(
                            conn,
                            raw,
                            session_id,
                            propose=propose,
                            decide=decide,
                            detector_label=detector_label,
                            resolver_label=resolver_label,
                        )
                except HTTPException:
                    raise
                except Exception as exc:
                    # The session is recorded last, so a failure here leaves it
                    # pending and a retry is safe.
                    _log.warning(
                        "analysing %s failed after %.0fs: %s",
                        session_id, time.monotonic() - started, exc,
                    )
                    raise HTTPException(
                        502,
                        f"the model call failed, so {session_id} is still waiting:"
                        f" {describe_failure(exc)}",
                    ) from exc
                finally:
                    # Failed or not, the calls were made and billed.
                    meter.flush(conn)
                _log.info(
                    "analysed %s in %.0fs: %d windows, %d flagged",
                    session_id, time.monotonic() - started,
                    result.windows_examined, len(result.resolutions),
                )
                found = read.concepts(conn, _home())
        finally:
            with _ANALYSING_LOCK:
                _ANALYSING.discard(session_id)

        return AnalysedOut(
            session_id=session_id,
            clean=result.clean,
            filed=[r.canonical_name for r in result.resolutions],
            windows_examined=result.windows_examined,
            detector_version=result.detector_version,
            counts={b: sum(1 for c in found if c.bucket == b) for b in read.BUCKETS},
        )

    @app.get("/api/config", response_model=ConfigOut)
    def config() -> ConfigOut:
        """The model configuration in effect, as the core resolved it.

        Read from the core rather than from the app's own settings on purpose: a
        real environment variable beats whatever the app passed, so the only
        honest answer to "what will be used" is the one the core gives.
        """
        from ..detector import detector_version
        from ..model import ModelConfig

        current = ModelConfig.from_env()
        local = current.local
        _, grader, graded_by_model = _build_grader()
        return ConfigOut(
            model=current.model,
            base_url=current.base_url,
            key_set=bool(current.api_key),
            local=local,
            grader="classifier" if graded_by_model else "keyword",
            detector_version=detector_version(current.label),
            reasoning_effort=current.reasoning_effort if current.sends_reasoning else None,
            leaves_this_mac=[] if local else LEAVES_THIS_MAC,
        )

    @app.get("/api/export/preview", response_model=ExportPreviewOut)
    def export_preview(include_text: bool = False) -> ExportPreviewOut:
        """What "Export for analysis" would write, shown before anything is. Reads only."""
        from .. import export
        from ..model import ModelConfig

        with stores() as (conn, raw):
            files, meta = export.contents(
                conn, raw=raw, include_text=include_text, config=ModelConfig.from_env()
            )
        return ExportPreviewOut(
            include_text=include_text,
            filename=f"{export.folder_name()}.zip",
            files=[ExportFileOut(name=name, rows=n) for name, n in meta["files"].items()],
            fixture_events=meta["fixture_events"],
            first_event_at=meta["first_event_at"],
            last_event_at=meta["last_event_at"],
            included=export.INCLUDED,
            withheld=[] if include_text else export.WITHHELD,
            never=export.NEVER,
        )

    @app.post("/api/export")
    def export_bundle(include_text: bool = False) -> Response:
        """The bundle as a `.zip`, returned rather than written.

        The app saves it where the person chose. The core never writes it
        anywhere itself, so this endpoint cannot be pointed at a path.
        """
        from .. import export
        from ..model import ModelConfig

        with stores() as (conn, raw):
            data, _meta = export.archive(
                conn, raw=raw, include_text=include_text, config=ModelConfig.from_env()
            )
        return Response(
            content=data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{export.folder_name()}.zip"'
            },
        )

    @app.get("/api/regen/plan", response_model=RegenPlanOut)
    def regen_plan() -> RegenPlanOut:
        """What regenerating would do. Calls no model."""
        from ..model import ModelConfig
        from ..regen import plan

        with stores() as (conn, raw):
            if raw is None:
                raise HTTPException(409, "nothing has been captured, so there is nothing to regenerate")
            preview = plan(conn, raw, model_label=ModelConfig.from_env().label)
        return RegenPlanOut(**vars(preview), can_run=_can_analyse())

    @app.post("/api/regen", response_model=RegenPassOut)
    def regen_one(body: dict) -> RegenPassOut:
        """Re-examine one session under the current detector, protecting every judgment.

        One per request, for the same reasons as analysis: real progress, a stop
        that lands between sessions, and no request that outlives its patience.
        `regenerate` commits before it yields, so stopping leaves the log whole.
        """
        from ..regen import regenerate
        from ..spend import Meter

        session_id = str((body or {}).get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(400, "name a session to regenerate")
        meter = Meter()
        try:
            propose, decide, detector_label, _ = _build_analyser(meter)
        except _NoModel as exc:
            raise HTTPException(409, str(exc)) from exc

        with _ANALYSING_LOCK:
            if session_id in _ANALYSING:
                raise HTTPException(409, f"{session_id} is already being analysed")
            _ANALYSING.add(session_id)
        try:
            with stores() as (conn, raw):
                if raw is None or not raw.execute(
                    "SELECT 1 FROM raw_sessions WHERE session_id = ?", (session_id,)
                ).fetchone():
                    raise HTTPException(404, f"{session_id} has not been captured")
                try:
                    [result] = list(
                        regenerate(
                            conn, raw,
                            propose=propose, decide=decide, model_label=detector_label,
                            sessions=[session_id], meter=meter,
                        )
                    )
                except Exception as exc:
                    _log.warning("regenerating %s failed: %s", session_id, exc)
                    raise HTTPException(
                        502, f"regenerating {session_id} failed: {describe_failure(exc)}"
                    ) from exc
                finally:
                    meter.flush(conn)
        finally:
            with _ANALYSING_LOCK:
                _ANALYSING.discard(session_id)
        return RegenPassOut(
            session_id=result.session_id,
            protected=result.protected,
            removed=result.removed,
            recorded=result.recorded,
            skipped=result.skipped,
            detector_version=result.detector_version,
        )

    @app.get("/api/dev/wipe/plan", response_model=DevWipePlanOut)
    def dev_wipe_plan() -> DevWipePlanOut:
        """What a wipe would discard and rebuild, shown before the confirmation
        dialog. Calls no model, changes nothing. Dev builds only -- see `_dev_mode`.
        """
        from ..model import ModelConfig
        from ..regen import plan

        if not _dev_mode():
            raise HTTPException(403, "dev features are off on this core")
        with stores() as (conn, raw):
            if raw is None:
                raise HTTPException(409, "nothing has been captured, so there is nothing to wipe")
            preview = plan(conn, raw, model_label=ModelConfig.from_env().label)
            manual = conn.execute(
                "SELECT count(*) FROM compiled_encounters WHERE source = 'manual'"
            ).fetchone()[0]
            explanations = conn.execute(
                "SELECT count(*) FROM compiled_explanations"
            ).fetchone()[0]
        return DevWipePlanOut(
            # Every captured session, not just `to_run`: a wipe clears
            # `session_analysed` for all of them, so `already_done` is empty
            # immediately afterwards and every one of them is due a re-run.
            sessions_to_rerun=preview.captured,
            protected_encounters=preview.protected,
            manual_encounters=manual,
            explanations=explanations,
            can_run=_can_analyse(),
        )

    @app.post("/api/dev/wipe", response_model=DevWipeOut)
    def dev_wipe(body: dict) -> DevWipeOut:
        """Back up the store, discard model-generated data, and report what's left
        to re-run. The rerun itself is not started here -- the caller drives it
        through the existing `POST /api/regen`, one session at a time, the same
        way any other regeneration does (see `Regenerator.swift`). Splitting the
        two means a wipe that took the backup and cannot afford the rerun's model
        calls is still a completed, honest operation rather than a half a wipe
        stuck mid-request.

        Dev builds only -- see `_dev_mode`. Refuses with 403 on any other core,
        including one started for the CLI or from a shipped, prod-channel app,
        because this can discard the only copy of real judgments and
        explanations that exists.
        """
        from ..model import ModelConfig
        from ..regen import plan, wipe
        from ..store.backup import backup_store

        if not _dev_mode():
            raise HTTPException(403, "dev features are off on this core")
        discard_user_input = bool((body or {}).get("discard_user_input"))
        with stores() as (conn, raw):
            if raw is None:
                raise HTTPException(409, "nothing has been captured, so there is nothing to wipe")
            backup_path = backup_store(conn, paths.home(_home()))
            result = wipe(conn, discard_user_input=discard_user_input)
            sessions_to_rerun = plan(
                conn, raw, model_label=ModelConfig.from_env().label
            ).captured
        _log.warning(
            "dev wipe: removed %d encounter(s) and %d other event(s)"
            " (discard_user_input=%s), backed up to %s",
            result.encounters_removed, result.other_events_removed,
            discard_user_input, backup_path,
        )
        return DevWipeOut(
            backup_path=str(backup_path),
            encounters_removed=result.encounters_removed,
            other_events_removed=result.other_events_removed,
            user_input_discarded=result.user_input_discarded,
            sessions_to_rerun=sessions_to_rerun,
        )

    # The built frontend, when there is one. Mounted last so it cannot shadow
    # /api. In development this is absent and Vite serves the app instead.
    if UI_DIST.is_dir():
        app.mount(
            "/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets"
        )

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            """Any non-API path is the single-page app; routing happens client-side.

            Served with no-store. The assets beside it are content-hashed and
            safe to cache forever, but `index.html` is the thing that names
            which hash is current -- so a cached copy silently pins the browser
            to a bundle that no longer exists on disk. That fails as "my change
            did not apply", which costs far more time than re-fetching 400 bytes.
            """
            return FileResponse(
                UI_DIST / "index.html",
                headers={"Cache-Control": "no-store, must-revalidate"},
            )

    return app


app = create_app()
