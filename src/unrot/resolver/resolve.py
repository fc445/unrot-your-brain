"""The resolver: the only way anything reaches the graph.

Every write -- detector output and a person's own words alike -- passes through
here before a single event is appended. A term may be a misspelling, a
half-remembered version of something already in the graph, a second name for it,
or genuinely new. Deciding which is the whole job.

**Conflicts are prevented at ingestion, not reconciled at compile.** This is the
serialisation point, so the fold downstream stays dumb: by the time events reach
`compile_state` all the thinking has already happened. That division is what
makes the compiled tables safe to throw away and rebuild.

**Judgments are appended, not just their outcomes.** "Merged K8s into Kubernetes
because it looked like the same thing" can be wrong, and journey 19 requires the
user to be able to say so. An event carrying only the merge leaves nothing to
argue with, so the reasoning is a first-class event and its id is what a
correction points at.

**Staleness is accepted rather than locked against.** The resolver reads current
state, reasons, then appends, so a concurrent write can leave it reasoning
against state that has since moved. Worst case is a duplicate concept that a
later merge folds away -- and merges are themselves correctable events, so the
machinery to clean up after itself already exists. No locking; single user.

Like the detector, the model call is injected rather than built here: `resolve()`
takes a `decide` callable. That seam is what lets every rule in this module be
tested without a network, a key, or langchain installed -- and it is also how
the deterministic decider in `deciders.py` can drive the whole pipeline with no
model at all.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..store import append, compile_state, new_ulid
from . import match
from . import prompt as prompt_module
from .submissions import Submission, fingerprint

RESOLVER_VERSION = "0.1.0"

#: What a decider may answer. Anything else is treated as `new`, because the
#: failure mode of a duplicate concept is a later merge, while the failure mode
#: of a wrong match is an encounter filed under something the user never met.
DECISIONS = ("new", "existing", "alias")


def resolver_version(model_label: str) -> str:
    """Which resolver produced a judgment: version, model, and prompt.

    Encodes the prompt hash for the same reason the detector does -- editing the
    wording changes what the machine means by "the same concept", and S5 asks
    that such a change be visible in history rather than indistinguishable from
    the user's world having changed.
    """
    return f"resolver/{RESOLVER_VERSION}+{model_label}+{prompt_module.prompt_id()}"


@dataclass
class Resolution:
    """What the resolver did, and the event id that lets you argue with it."""

    submission: Submission
    decision: str
    concept_id: str
    canonical_name: str
    encounter_id: str
    reasoning: str

    #: The `resolver_judgment` event. This is the addressable thing -- a
    #: correction names it, which is only possible because the reasoning was
    #: appended rather than applied and forgotten.
    judgment_event_id: str

    #: True when a string match settled it and no model was asked. Worth
    #: knowing: it means the judgment carries no model's opinion, so a later
    #: model upgrade has nothing to revise here.
    decided_without_model: bool = False


def _concept_id(conn: sqlite3.Connection, name: str) -> str:
    """A readable id, falling back to a minted one on collision.

    Slugs make the log legible when reading it by hand, which matters for a
    store whose selling point is that its history can be inspected. They are not
    load-bearing: two different concepts that slug the same get a unique id
    rather than being quietly conflated.
    """
    slug = match.normalise(name).replace(" ", "-")[:48] or "concept"
    candidate = f"c-{slug}"
    taken = conn.execute(
        "SELECT 1 FROM compiled_concepts WHERE concept_id = ?", (candidate,)
    ).fetchone()
    return candidate if not taken else f"c-{slug}-{new_ulid()[-6:]}"


def resolve(
    conn: sqlite3.Connection,
    submission: Submission,
    *,
    decide,
    model_label: str = "none",
    origin: str = "local",
    recompile: bool = True,
) -> Resolution:
    """Resolve one submission and append everything it implies.

    Order matters: the judgment is appended first so that the events carrying
    out its decision can be read as its consequences, and so a correction has
    something to point at even if a later append fails.
    """
    known = match.current(conn)
    hit = match.exact(known, submission.text)

    if hit is not None:
        # No judgment to make, so none is bought. This is the common path: a
        # term you have met before, met again.
        decision, concept_id, canonical = "existing", hit.concept_id, hit.canonical_name
        alias = None
        paraphrase = submission.paraphrase
        reasoning = f"`{submission.text}` already names this concept."
        used_model = False
    else:
        answer = decide(submission, match.shortlist(known, submission.text)) or {}
        decision = str(answer.get("decision") or "").strip().lower()
        if decision not in DECISIONS:
            decision = "new"

        by_id = {c.concept_id: c for c in known}
        target = by_id.get(str(answer.get("concept_id") or ""))
        # A decider naming a concept that does not exist is treated as having
        # proposed a new one. Inventing the link instead would file an encounter
        # under something the user never met, which is the one error the surface
        # cannot help them notice.
        if decision in ("existing", "alias") and target is None:
            decision = "new"

        canonical = str(answer.get("canonical_name") or "").strip() or submission.text
        alias = None
        if decision == "new":
            concept_id = _concept_id(conn, canonical)
        else:
            concept_id, canonical = target.concept_id, target.canonical_name
            if decision == "alias":
                alias = str(answer.get("alias") or submission.text).strip()

        paraphrase = str(answer.get("paraphrase") or "").strip() or submission.paraphrase
        reasoning = str(answer.get("reasoning") or "").strip() or "No reasoning given."
        used_model = True

    provenance = {
        "detector_version": submission.version,
        "resolver_version": resolver_version(model_label if used_model else "none"),
    }

    judgment_event_id = append(
        conn,
        "resolver_judgment",
        {
            "input_text": submission.text,
            "decision": decision,
            "reasoning": reasoning,
            "concept_id": concept_id,
            "source": submission.source,
            "decided_without_model": not used_model,
        },
        subject_id=concept_id,
        origin=origin,
        provenance=provenance,
    )

    if decision == "new":
        append(
            conn,
            "concept_created",
            {"concept_id": concept_id, "canonical_name": canonical},
            origin=origin,
            provenance=provenance,
        )
    elif decision == "alias" and alias:
        append(
            conn,
            "alias_added",
            {"concept_id": concept_id, "alias": alias},
            origin=origin,
            provenance=provenance,
        )

    encounter_id = (
        fingerprint(submission) if submission.pointer else "e-" + new_ulid()
    )
    payload = {
        "encounter_id": encounter_id,
        "concept_id": concept_id,
        "source": submission.source,
        "paraphrase": paraphrase,
    }
    if submission.pointer:
        payload["pointer"] = submission.pointer
    append(
        conn,
        "encounter_recorded",
        payload,
        origin=origin,
        provenance={"detector_version": submission.version},
    )

    if recompile:
        compile_state(conn)
    return Resolution(
        submission=submission,
        decision=decision,
        concept_id=concept_id,
        canonical_name=canonical,
        encounter_id=encounter_id,
        reasoning=reasoning,
        judgment_event_id=judgment_event_id,
        decided_without_model=not used_model,
    )


def resolve_reference(
    conn: sqlite3.Connection,
    text: str,
    *,
    decide,
    model_label: str = "none",
    origin: str = "local",
    recompile: bool = True,
) -> Resolution:
    """Resolve a name to a concept WITHOUT recording an encounter.

    For concepts that learning material names in passing. They belong in the
    graph -- that is what makes clustering possible later -- but the user never
    met them, and writing an encounter would say they did. The whole point of
    the S1 split is that a concept with no encounters is a valid, different
    thing: `referenced`, which is never surfaced and never counts toward the
    flag budget.

    Still the same write path. Identity is decided here, by the same matcher and
    the same decider, and the judgment is appended like any other -- a second
    route into the graph that skipped that would be exactly what PR-21 forbids.
    """
    known = match.current(conn)
    hit = match.exact(known, text)

    if hit is not None:
        decision, concept_id, canonical = "existing", hit.concept_id, hit.canonical_name
        reasoning = f"`{text}` already names this concept."
        alias, used_model = None, False
    else:
        stub = Submission(
            text=text, paraphrase=f"Named in learning material: {text}.",
            source="material", version="material",
        )
        answer = decide(stub, match.shortlist(known, text)) or {}
        decision = str(answer.get("decision") or "").strip().lower()
        if decision not in DECISIONS:
            decision = "new"
        by_id = {c.concept_id: c for c in known}
        target = by_id.get(str(answer.get("concept_id") or ""))
        if decision in ("existing", "alias") and target is None:
            decision = "new"
        canonical = str(answer.get("canonical_name") or "").strip() or text
        alias = None
        if decision == "new":
            concept_id = _concept_id(conn, canonical)
        else:
            concept_id, canonical = target.concept_id, target.canonical_name
            if decision == "alias":
                alias = str(answer.get("alias") or text).strip()
        reasoning = str(answer.get("reasoning") or "").strip() or "No reasoning given."
        used_model = True

    provenance = {"resolver_version": resolver_version(model_label if used_model else "none")}
    judgment_event_id = append(
        conn,
        "resolver_judgment",
        {
            "input_text": text,
            "decision": decision,
            "reasoning": reasoning,
            "concept_id": concept_id,
            "source": "material",
            "decided_without_model": not used_model,
        },
        subject_id=concept_id,
        origin=origin,
        provenance=provenance,
    )
    if decision == "new":
        append(
            conn, "concept_created",
            {"concept_id": concept_id, "canonical_name": canonical},
            origin=origin, provenance=provenance,
        )
    elif decision == "alias" and alias:
        append(
            conn, "alias_added", {"concept_id": concept_id, "alias": alias},
            origin=origin, provenance=provenance,
        )

    if recompile:
        compile_state(conn)
    return Resolution(
        submission=Submission(text=text, paraphrase="", source="material", version="material"),
        decision=decision,
        concept_id=concept_id,
        canonical_name=canonical,
        encounter_id="",
        reasoning=reasoning,
        judgment_event_id=judgment_event_id,
        decided_without_model=not used_model,
    )


def resolve_all(
    conn: sqlite3.Connection,
    submissions,
    *,
    decide,
    model_label: str = "none",
    origin: str = "local",
) -> list[Resolution]:
    """Resolve a batch, recompiling between each.

    The recompile is not optional and not an optimisation left undone: the
    second submission in a batch has to be able to see the concept the first one
    created, or a session that mentions the same new term twice ends up with two
    concepts for it. Folding a single user's log costs milliseconds, so the
    correct-by-construction version is also the cheap one.
    """
    return [
        resolve(conn, s, decide=decide, model_label=model_label, origin=origin)
        for s in submissions
    ]


def record_analysis(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    candidates_found: int,
    detector_version: str,
    origin: str = "local",
    recompile: bool = True,
) -> str:
    """Record that a session was examined — **including when nothing was found**.

    The zero case is the one that matters. Without it a clean session and a
    session nobody looked at are the same absence of rows, and the surface
    cannot honestly tell the user that silence means anything. Journey 3 asks
    for silence to be trustworthy; this is what it costs.
    """
    event_id = append(
        conn,
        "session_analysed",
        {"session_id": session_id, "candidates_found": candidates_found},
        origin=origin,
        provenance={"detector_version": detector_version},
    )
    if recompile:
        compile_state(conn)
    return event_id


def merge(
    conn: sqlite3.Connection,
    from_concept_id: str,
    into_concept_id: str,
    *,
    reasoning: str,
    origin: str = "local",
    recompile: bool = True,
) -> str:
    """Fold one existing concept into another.

    Distinct from the `alias` decision, which handles a *new* name arriving for
    a concept already in the graph. This is the cleanup for when two concepts
    are already there and should never have been two -- the duplicate that the
    accept-staleness decision knowingly permits.

    Nothing is deleted. The merged-away concept keeps its row so old ids still
    resolve, and its names become aliases of the survivor, which is how `K8s`
    stays findable after folding into `Kubernetes`.
    """
    event_id = append(
        conn,
        "concept_merged",
        {
            "from_concept_id": from_concept_id,
            "into_concept_id": into_concept_id,
            "reasoning": reasoning,
        },
        origin=origin,
        provenance={"resolver_version": resolver_version("none")},
    )
    if recompile:
        compile_state(conn)
    return event_id


def correct(
    conn: sqlite3.Connection,
    target_event_id: str,
    *,
    reasoning: str,
    merge_into: str | None = None,
    origin: str = "local",
    recompile: bool = True,
) -> str:
    """Say that a resolver judgment was wrong, and optionally fix what it did.

    Journey 19. The correction is a **user** event, so it is ground truth and a
    regeneration will never replay over it -- the machine's opinion about a term
    is disposable, the user's correction of that opinion is not.

    Two appends rather than one, deliberately. The correction records that the
    judgment was wrong and why; `merge_into` repairs the state it produced. They
    are separable because a judgment can be wrong without the repair being
    obvious, and recording the objection should not wait on knowing the fix.
    """
    target = conn.execute(
        "SELECT payload FROM events WHERE event_id = ? AND event_type = 'resolver_judgment'",
        (target_event_id,),
    ).fetchone()
    if target is None:
        raise ValueError(f"no resolver judgment {target_event_id!r} to correct")

    event_id = append(
        conn,
        "resolver_judgment_corrected",
        {"target_event_id": target_event_id, "reasoning": reasoning},
        supersedes=target_event_id,
        origin=origin,
    )
    if merge_into:
        import json

        was = json.loads(target["payload"]).get("concept_id")
        if was and was != merge_into:
            merge(
                conn,
                was,
                merge_into,
                reasoning=f"Corrected by hand: {reasoning}",
                origin=origin,
                recompile=False,
            )
    if recompile:
        compile_state(conn)
    return event_id


def judgments(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """The resolver's decisions, newest first, with corrections attached.

    The reviewable surface the "judgments are events" decision exists to give.
    """
    return list(
        conn.execute(
            "SELECT j.*, c.event_id AS corrected_by,"
            "       json_extract(c.payload, '$.reasoning') AS correction"
            "  FROM events j"
            "  LEFT JOIN events c ON c.supersedes = j.event_id"
            "   AND c.event_type = 'resolver_judgment_corrected'"
            " WHERE j.event_type = 'resolver_judgment'"
            " ORDER BY j.event_id DESC LIMIT ?",
            (limit,),
        )
    )
