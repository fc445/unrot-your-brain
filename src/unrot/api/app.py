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
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..capture import paths
from ..material import TEXTUAL
from ..store import compile_state
from ..store.__main__ import open_raw, open_store
from . import read
from .schemas import (
    CaptureOut,
    CheckOut,
    ConceptOut,
    EncounterOut,
    ExplanationOut,
    GradedOut,
    HealthOut,
    MadeOut,
    MaterialOut,
    JudgmentOut,
    MomentOut,
    MomentTurn,
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


def _build_grader():
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
        return build_jev_grader(config), DEFAULT_JEV_MODEL.replace("/", "-"), True
    except Exception:  # pragma: no cover - network/config problems at build time
        return keyword_grader, "keyword", False


def create_app() -> FastAPI:
    app = FastAPI(
        title="unrot",
        summary="The gap list, and the judgments you make about it.",
        version="0.1.0",
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
        if judgment not in ("confirm", "dismiss"):
            raise HTTPException(400, f"unknown judgment {judgment!r}")

        from ..store import append

        with stores() as (conn, raw):
            exists = conn.execute(
                "SELECT 1 FROM compiled_encounters WHERE encounter_id = ?",
                (encounter_id,),
            ).fetchone()
            if not exists:
                raise HTTPException(404, f"no encounter {encounter_id!r}")

            append(
                conn,
                "encounter_confirmed" if judgment == "confirm" else "encounter_dismissed",
                {"encounter_id": encounter_id},
            )
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

        grade_fn, model_label, live = _build_grader()

        with stores() as (conn, _raw):
            try:
                explanation_id = submit(conn, concept_id, text)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc

            graded = True
            try:
                grade_one(conn, explanation_id, grade_fn=grade_fn, model_label=model_label)
            except Exception:
                # The answer is safely stored; the level is not. Reported as
                # ungraded rather than as a failure, because the user has not
                # lost anything and must not be told they failed a check that
                # never ran.
                graded = False
                compile_state(conn)

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
            sources_only,
            textual,
        )
        from ..model import ModelConfig
        from ..resolver import resolve_reference, strict

        if format not in (TEXTUAL_FORMAT, SOURCES_ONLY):
            raise HTTPException(400, f"unknown format {format!r}")

        config = ModelConfig.from_env()
        search = build_search(config) if config.api_key else None

        with stores() as (conn, raw):
            try:
                found = gather(conn, raw, concept_id, search=search)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc

            try:
                if format == SOURCES_ONLY:
                    made = sources_only(conn, concept_id, found)
                else:
                    if not config.api_key:
                        raise NotGrounded(
                            "no model configured, so nothing can be written."
                            " The sources-only format still works."
                        )
                    decide = strict
                    made = textual(
                        conn,
                        concept_id,
                        found,
                        write=build_writer(config),
                        resolve_named=lambda term: resolve_reference(
                            conn, term, decide=decide, recompile=False
                        ),
                        model_label=config.label,
                    )
            except WouldRecurse as exc:
                raise HTTPException(409, str(exc)) from exc
            except NotGrounded as exc:
                # 422 rather than 500: refusing to write something unsupported
                # is the gate working, not the server failing.
                raise HTTPException(422, str(exc)) from exc

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
