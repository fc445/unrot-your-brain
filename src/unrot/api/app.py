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

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..capture import paths
from ..store import compile_state
from ..store.__main__ import open_raw, open_store
from . import read
from .schemas import (
    CaptureOut,
    ConceptOut,
    EncounterOut,
    JudgmentOut,
    MomentOut,
    MomentTurn,
    SurfaceOut,
)

#: Where `npm run build` puts the frontend. Present in a built checkout, absent
#: in development -- where Vite serves the app instead and proxies here.
UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"

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
        "unjudged": concept.unjudged,
    }
    return ConceptOut(**data)


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
