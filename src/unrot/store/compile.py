"""The compile step: fold the event log into current state.

This is deliberately dumb. Every judgment call in the system -- is this term the
same as that one, is this a misspelling, does this deserve to be a concept at
all -- belongs to the resolver, which is the single serialisation point in front
of the log. By the time events reach here, all the thinking has happened.

Keeping it dumb is what makes it trustworthy: the fold is a pure function of the
log, so `compile_state` can be run any number of times and always produces the
same answer. That in turn is what makes the store disposable, and the store
being disposable is what makes a schema mistake cheap.

The one piece of policy that does live here is `derive_state`, and it is here on
purpose: status is compiled rather than stored, so changing the rule is a code
change plus a re-run, not a migration.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

_COMPILED_TABLES = (
    "compiled_concepts",
    "compiled_encounters",
    "compiled_material",
    "compiled_material_concepts",
    "compiled_explanations",
    "compiled_sessions",
)


@dataclass
class CompileResult:
    event_count: int
    compiled_through: str | None
    concepts: int
    encounters: int
    sessions_analysed: int = 0


@dataclass
class _Concept:
    concept_id: str
    canonical_name: str
    gap_type: str = "term"
    aliases: list[str] = field(default_factory=list)
    merged_into: str | None = None


def _resolve(concept_id: str, merges: dict[str, str]) -> str:
    """Follow a merge chain to the surviving concept, with a cycle guard."""
    seen = set()
    current = concept_id
    while current in merges and current not in seen:
        seen.add(current)
        current = merges[current]
    return current


def derive_state(
    *,
    has_encounters: bool,
    latest_judgment: str | None,
    latest_level: str | None,
    named_in_delivered_material: bool,
) -> str:
    """Decide a concept's compiled state.

    The only policy in this module, and the most likely thing to change. It is
    cheap to change precisely because nothing here was written down at ingest.

    * `referenced` -- named in material but never encountered by the user. Pure
      scaffolding: never surfaced, never counts toward the flag budget.
    * `known` -- the user reached a causal explanation, or told us they already
      knew it. Not a gap.
    * `exposed` -- encountered, and named in material they actually received,
      but never confirmed. Neither taught nor untouched.
    * `gap` -- encountered and unresolved. The thing the product is about.
    """
    if not has_encounters:
        return "referenced"
    if latest_level == "causal":
        return "known"
    if latest_judgment == "dismissed":
        return "known"
    if named_in_delivered_material:
        return "exposed"
    return "gap"


def compile_state(conn: sqlite3.Connection) -> CompileResult:
    """Rebuild every `compiled_*` table from the log. Idempotent by construction.

    A full rebuild rather than an incremental update: for a single user's graph
    the whole log folds in milliseconds, and "throw it away and redo it" is the
    only version of this that cannot drift from the log it claims to summarise.
    """
    rows = list(conn.execute("SELECT * FROM events ORDER BY event_id"))

    for table in _COMPILED_TABLES:
        conn.execute(f"DELETE FROM {table}")

    # Pass 1 -- collect merges, so events recorded before a merge still resolve
    # to the surviving concept.
    merges: dict[str, str] = {}
    for row in rows:
        if row["event_type"] == "concept_merged":
            payload = json.loads(row["payload"])
            merges[payload["from_concept_id"]] = payload["into_concept_id"]

    concepts: dict[str, _Concept] = {}
    encounters: dict[str, dict] = {}
    explanations: dict[str, dict] = {}
    material: dict[str, dict] = {}
    material_concepts: set[tuple[str, str]] = set()
    sessions: dict[str, dict] = {}

    # Events that REFER to something rather than create it are collected here
    # and applied after the pass, keyed by the id they name.
    #
    # This is not tidiness, it is the regeneration guarantee. A re-run deletes
    # derived events and appends replacements, which get freshly minted ULIDs --
    # so a replayed `encounter_recorded` sorts AFTER the confirmation that
    # refers to it. Applying judgments inline would silently drop them, which is
    # exactly the "user judgments must survive regeneration" rule failing.
    # Keying by id instead of by position makes the fold order-independent where
    # it has to be, and order-dependent only where order is the answer (which of
    # two judgments is the latest).
    judgments: dict[str, tuple[str | None, str | None]] = {}
    # Triage's spot checks, by encounter id. Deferred like judgments: triage
    # writes its event before the resolver records the encounter it names.
    # Sticky -- once asked as a spot check, always one -- so a regeneration that
    # re-judges the same moment cannot quietly un-ask a question already answered.
    spot_checks: set[str] = set()
    grades: dict[str, dict] = {}
    deliveries: dict[str, str] = {}

    # Pass 2 -- the fold.
    for row in rows:
        etype = row["event_type"]
        payload = json.loads(row["payload"])
        provenance = json.loads(row["provenance"]) if row["provenance"] else {}

        if etype == "concept_created":
            cid = payload["concept_id"]
            concepts[cid] = _Concept(
                concept_id=cid,
                canonical_name=payload["canonical_name"],
                gap_type=payload.get("gap_type", "term"),
            )

        elif etype == "alias_added":
            target = _resolve(payload["concept_id"], merges)
            concept = concepts.get(target)
            if concept and payload["alias"] not in concept.aliases:
                concept.aliases.append(payload["alias"])

        elif etype == "alias_removed":
            target = _resolve(payload["concept_id"], merges)
            concept = concepts.get(target)
            if concept and payload["alias"] in concept.aliases:
                concept.aliases.remove(payload["alias"])

        elif etype == "concept_merged":
            gone, survivor = payload["from_concept_id"], payload["into_concept_id"]
            if gone in concepts:
                concepts[gone].merged_into = survivor
                # The merged-away name becomes an alias of the survivor: that is
                # how "K8s" stays findable after folding into "Kubernetes".
                target = concepts.get(_resolve(survivor, merges))
                if target:
                    for name in [concepts[gone].canonical_name, *concepts[gone].aliases]:
                        if name not in target.aliases and name != target.canonical_name:
                            target.aliases.append(name)

        elif etype == "encounter_recorded":
            eid = payload["encounter_id"]
            existing = encounters.get(eid)
            encounters[eid] = {
                "encounter_id": eid,
                "concept_id": _resolve(payload["concept_id"], merges),
                "source": payload["source"],
                "paraphrase": payload["paraphrase"],
                "session_id": (payload.get("pointer") or {}).get("session_id"),
                "line_start": (payload.get("pointer") or {}).get("line_start"),
                "line_end": (payload.get("pointer") or {}).get("line_end"),
                "detector_version": provenance.get("detector_version"),
                "occurred_at": row["occurred_at"],
                "paraphrase_event_id": row["event_id"],
                # Supersession never touches the judgment: that is held against
                # encounter_id, not against whichever event supplied the text.
                "paraphrase_superseded": 1 if (existing and row["supersedes"]) else 0,
                "judgment": None,
                "judged_at": None,
                "spot_check": 0,
            }

        elif etype == "familiarity_judged":
            if payload.get("verdict") == "spot_check" and payload.get("encounter_id"):
                spot_checks.add(payload["encounter_id"])

        elif etype in ("encounter_confirmed", "encounter_dismissed"):
            judgments[payload["encounter_id"]] = (
                "confirmed" if etype == "encounter_confirmed" else "dismissed",
                row["occurred_at"],
            )

        elif etype == "encounter_judgment_retracted":
            # Latest wins, and the latest is "no judgment". Held in the same
            # deferred map rather than applied inline, for the same reason the
            # judgments are: a replayed encounter sorts after the events that
            # refer to it.
            judgments[payload["encounter_id"]] = (None, None)

        elif etype == "explanation_submitted":
            explanations[row["event_id"]] = {
                "explanation_id": row["event_id"],
                "concept_id": _resolve(payload["concept_id"], merges),
                "encounter_id": payload.get("encounter_id"),
                "raw_text": payload["raw_text"],
                "prompt_text": payload["prompt_text"],
                "prompt_version": payload["prompt_version"],
                "submitted_at": row["occurred_at"],
                "rubric": None,
                "level": None,
                "reasoning": None,
                "probabilities": None,
                "confidence": None,
                "grader_version": None,
                "graded_at": None,
            }

        elif etype == "explanation_graded":
            # Last grade wins: re-grading under a new rubric supersedes the
            # previous answer about the same text rather than accumulating.
            grades[payload["explanation_id"]] = {
                "rubric": payload["rubric"],
                "level": payload["level"],
                "reasoning": payload.get("reasoning"),
                "probabilities": json.dumps(payload["probabilities"])
                if payload.get("probabilities")
                else None,
                "confidence": payload.get("confidence"),
                "grader_version": provenance.get("grader_version"),
                "graded_at": row["occurred_at"],
            }

        elif etype == "session_analysed":
            # Last run wins: re-analysing a session with a better detector
            # replaces what the previous one concluded about it, rather than
            # accumulating one row per pass.
            sessions[payload["session_id"]] = {
                "session_id": payload["session_id"],
                "analysed_at": row["occurred_at"],
                "detector_version": provenance.get("detector_version"),
                "candidates_found": int(payload.get("candidates_found") or 0),
            }

        elif etype == "material_generated":
            mid = payload["material_id"]
            material[mid] = {
                "material_id": mid,
                "format": payload["format"],
                "sources": json.dumps(payload.get("sources", [])),
                "body": payload.get("body"),
                "generated_at": row["occurred_at"],
                "delivered_at": None,
            }
            for cid in payload["covers_concept_ids"]:
                material_concepts.add((mid, _resolve(cid, merges)))

        elif etype == "material_delivered":
            deliveries[payload["material_id"]] = row["occurred_at"]

    # Apply the deferred references now that everything they name exists.
    for encounter_id, (judgment, judged_at) in judgments.items():
        record = encounters.get(encounter_id)
        if record is not None:
            record["judgment"] = judgment
            record["judged_at"] = judged_at

    for encounter_id in spot_checks:
        record = encounters.get(encounter_id)
        if record is not None:
            record["spot_check"] = 1

    for explanation_id, grade in grades.items():
        target = explanations.get(explanation_id)
        if target is not None:
            target.update(grade)

    for material_id, delivered_at in deliveries.items():
        target = material.get(material_id)
        if target is not None:
            target["delivered_at"] = delivered_at

    _write(
        conn, concepts, encounters, explanations, material, material_concepts,
        sessions, merges,
    )

    through = rows[-1]["event_id"] if rows else None
    conn.execute(
        "INSERT INTO compile_meta (id, compiled_through, compiled_at, event_count)"
        " VALUES (1, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET compiled_through = excluded.compiled_through,"
        " compiled_at = excluded.compiled_at, event_count = excluded.event_count",
        (through, datetime.now(timezone.utc).isoformat(timespec="milliseconds"), len(rows)),
    )
    conn.commit()
    return CompileResult(
        len(rows), through, len(concepts), len(encounters), len(sessions)
    )


def _write(
    conn, concepts, encounters, explanations, material, material_concepts,
    sessions, merges,
):
    for record in sessions.values():
        conn.execute(
            "INSERT INTO compiled_sessions (session_id, analysed_at, detector_version,"
            " candidates_found)"
            " VALUES (:session_id, :analysed_at, :detector_version, :candidates_found)",
            record,
        )

    for record in encounters.values():
        conn.execute(
            "INSERT INTO compiled_encounters (encounter_id, concept_id, source,"
            " paraphrase, session_id, line_start, line_end, detector_version,"
            " judgment, judged_at, occurred_at, paraphrase_event_id,"
            " paraphrase_superseded, spot_check)"
            " VALUES (:encounter_id, :concept_id, :source, :paraphrase, :session_id,"
            " :line_start, :line_end, :detector_version, :judgment, :judged_at,"
            " :occurred_at, :paraphrase_event_id, :paraphrase_superseded, :spot_check)",
            record,
        )

    for record in explanations.values():
        conn.execute(
            "INSERT INTO compiled_explanations (explanation_id, concept_id,"
            " encounter_id, raw_text, prompt_text, prompt_version, submitted_at,"
            " rubric, level, reasoning, probabilities, confidence,"
            " grader_version, graded_at)"
            " VALUES (:explanation_id, :concept_id, :encounter_id, :raw_text,"
            " :prompt_text, :prompt_version, :submitted_at, :rubric, :level,"
            " :reasoning, :probabilities, :confidence, :grader_version, :graded_at)",
            record,
        )

    for record in material.values():
        conn.execute(
            "INSERT INTO compiled_material (material_id, format, sources, body,"
            " generated_at, delivered_at)"
            " VALUES (:material_id, :format, :sources, :body, :generated_at,"
            " :delivered_at)",
            record,
        )

    delivered = {m["material_id"] for m in material.values() if m["delivered_at"]}
    for mid, cid in sorted(material_concepts):
        conn.execute(
            "INSERT OR IGNORE INTO compiled_material_concepts (material_id, concept_id)"
            " VALUES (?, ?)",
            (mid, cid),
        )

    exposed_concepts = {cid for mid, cid in material_concepts if mid in delivered}

    states: dict[str, str] = {}
    for cid, concept in concepts.items():
        own = [e for e in encounters.values() if e["concept_id"] == cid]
        judged = sorted(
            (e for e in own if e["judgment"]), key=lambda e: e["judged_at"] or ""
        )
        graded = sorted(
            (x for x in explanations.values() if x["concept_id"] == cid and x["level"]),
            key=lambda x: x["graded_at"] or "",
        )
        states[cid] = derive_state(
            has_encounters=bool(own),
            latest_judgment=judged[-1]["judgment"] if judged else None,
            latest_level=graded[-1]["level"] if graded else None,
            named_in_delivered_material=cid in exposed_concepts,
        )

    for cid, concept in concepts.items():
        own = [e for e in encounters.values() if e["concept_id"] == cid]
        seen = sorted(e["occurred_at"] for e in own)
        graded = sorted(
            (x for x in explanations.values() if x["concept_id"] == cid and x["level"]),
            key=lambda x: x["graded_at"] or "",
        )
        # A concept that was merged away keeps its row so old ids still resolve,
        # and reports the surviving concept's state rather than a stale one.
        survivor = _resolve(cid, merges)
        conn.execute(
            "INSERT INTO compiled_concepts (concept_id, canonical_name, gap_type,"
            " state, aliases, encounter_count, first_seen_at, last_seen_at,"
            " merged_into, latest_level, latest_level_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid,
                concept.canonical_name,
                concept.gap_type,
                states.get(survivor, states.get(cid, "referenced")),
                json.dumps(concept.aliases),
                len(own),
                seen[0] if seen else None,
                seen[-1] if seen else None,
                concept.merged_into,
                graded[-1]["level"] if graded else None,
                graded[-1]["graded_at"] if graded else None,
            ),
        )
