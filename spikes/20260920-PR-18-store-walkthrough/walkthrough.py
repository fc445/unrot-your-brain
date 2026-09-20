"""A narrated run through the store: watch a concept move gap -> exposed -> known.

The unit tests assert that the guarantees hold. This does the opposite job --
it shows you the thing working, one scene at a time, and leaves a real SQLite
file behind so you can go poking at it with `sqlite3` afterwards.

Run it:

    uv run python spikes/20260920-PR-18-store-walkthrough/walkthrough.py

Nothing here is library code. It is a demo, and it cheats accordingly: concept
ids are hand-written strings rather than resolver output, and the "detector"
is me typing paraphrases. Everything it calls, though, is the real store.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The editable install does not resolve on this machine (see README -- uv marks
# everything in .venv hidden, and Python 3.13+ skips hidden .pth files), so put
# src on the path directly rather than depending on it.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from unrot.store import (  # noqa: E402
    EventValidationError,
    append,
    compile_state,
    connect,
    derived_events,
    new_ulid,
    user_events,
)

DETECTOR_V1 = {"detector_version": "detector-0.1.0"}
DETECTOR_V2 = {"detector_version": "detector-0.2.0"}
GRADER = {"grader_version": "grader-0.1.0"}
PROMPT = "In your own words, what is it and why did it matter here?"


def scene(n: int, title: str) -> None:
    print(f"\n\033[1m{'-' * 72}\n {n}. {title}\n{'-' * 72}\033[0m")


def show(conn) -> None:
    """Print the compiled view: what the product would actually act on."""
    result = compile_state(conn)
    rows = list(
        conn.execute(
            "SELECT canonical_name, state, gap_type, encounter_count, latest_level,"
            " aliases, merged_into FROM compiled_concepts ORDER BY canonical_name"
        )
    )
    print(f"    {len(rows)} concepts, folded from {result.event_count} events\n")
    print(f"    {'concept':<16} {'state':<11} {'seen':<5} {'grade':<9} notes")
    for row in rows:
        aliases = json.loads(row["aliases"])
        notes = []
        if aliases:
            notes.append("aka " + ", ".join(aliases))
        if row["merged_into"]:
            notes.append(f"merged into {row['merged_into']}")
        print(
            f"    {row['canonical_name']:<16} {row['state']:<11}"
            f" {row['encounter_count']:<5} {row['latest_level'] or '-':<9}"
            f" {'; '.join(notes)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=str(Path(__file__).with_name("walkthrough.db")),
        help="where to write the store (deleted first, so re-runs are clean)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    conn = connect(db_path)

    idem, k8s, kube, crdt = "c-idem", "c-k8s", "c-kube", "c-crdt"

    scene(1, "The detector reads a transcript and finds two unexplained terms")
    print("    Claude justified a retry design with 'idempotency' and 'K8s'.")
    print("    The user said 'ok go ahead'. Neither term was ever explained.\n")
    for cid, name in ((idem, "idempotency"), (k8s, "K8s")):
        append(conn, "concept_created", {"concept_id": cid, "canonical_name": name})
    enc_idem = "e-" + new_ulid()
    enc_k8s = "e-" + new_ulid()
    append(
        conn,
        "encounter_recorded",
        {
            "encounter_id": enc_idem,
            "concept_id": idem,
            "source": "transcript",
            "paraphrase": "Claude called the handler idempotent to justify retrying on timeout.",
            "pointer": {"session_id": "sess-1", "line_start": 40, "line_end": 58},
        },
        provenance=DETECTOR_V1,
    )
    append(
        conn,
        "encounter_recorded",
        {
            "encounter_id": enc_k8s,
            "concept_id": k8s,
            "source": "transcript",
            "paraphrase": "Claude suggested the retry belongs in a K8s CronJob rather than the app.",
            "pointer": {"session_id": "sess-1", "line_start": 61, "line_end": 70},
        },
        provenance=DETECTOR_V1,
    )
    show(conn)
    print("\n    Both are gaps. Note the raw transcript is NOT in the log --")
    print("    only a paraphrase plus a pointer back to sess-1, lines 40-58.")

    scene(2, "The user judges them")
    print("    Confirms idempotency ('yeah, I nodded along'), dismisses K8s")
    print("    ('I've run clusters for years').\n")
    append(conn, "encounter_confirmed", {"encounter_id": enc_idem})
    append(conn, "encounter_dismissed", {"encounter_id": enc_k8s})
    show(conn)
    print("\n    Confirming does NOT clear the gap -- it is still a gap, now with")
    print("    ground truth behind it. Dismissing moves K8s straight to known.")

    scene(3, "The resolver notices K8s and Kubernetes are the same thing")
    append(conn, "concept_created", {"concept_id": kube, "canonical_name": "Kubernetes"})
    append(
        conn,
        "resolver_judgment",
        {
            "input_text": "K8s",
            "decision": f"merge into {kube}",
            "reasoning": "Numeronym for Kubernetes; same referent, not a distinct concept.",
        },
    )
    append(
        conn,
        "concept_merged",
        {
            "from_concept_id": k8s,
            "into_concept_id": kube,
            "reasoning": "Numeronym for Kubernetes.",
        },
    )
    show(conn)
    print("\n    The old id still resolves and the old name survives as an alias,")
    print("    and the reasoning is stored as an event you could later correct.")

    scene(4, "Material is generated -- and names a concept never encountered")
    material = "m-" + new_ulid()
    append(
        conn,
        "material_generated",
        {
            "material_id": material,
            "format": "textual_with_sources",
            "covers_concept_ids": [idem, crdt],
            "sources": ["https://example.invalid/idempotency-keys"],
            "body": "An idempotency key lets the server recognise a retry ...",
        },
    )
    append(conn, "concept_created", {"concept_id": crdt, "canonical_name": "CRDT"})
    show(conn)
    print("\n    CRDT is `referenced`, not a gap: the explanation mentioned it in")
    print("    passing, the user never met it. Scaffolding, never surfaced. This")
    print("    is the depth-1 guard -- adding nodes is fine, recursing is not.")

    scene(5, "The material is delivered")
    append(conn, "material_delivered", {"material_id": material})
    show(conn)
    print("\n    idempotency is now `exposed`: taught at, but not yet shown to be")
    print("    understood. Delivery alone never counts as learning.")

    scene(6, "The user explains it -- badly")
    weak = append(
        conn,
        "explanation_submitted",
        {
            "concept_id": idem,
            "encounter_id": enc_idem,
            "raw_text": "It means you can call it more than once.",
            "prompt_text": PROMPT,
            "prompt_version": "v1",
        },
    )
    append(
        conn,
        "explanation_graded",
        {"explanation_id": weak, "rubric": "solo-3", "level": "isolated"},
        provenance=GRADER,
    )
    show(conn)
    print("\n    Graded `isolated` -- one fact, no structure. Still exposed.")

    scene(7, "The user explains it properly")
    strong = append(
        conn,
        "explanation_submitted",
        {
            "concept_id": idem,
            "encounter_id": enc_idem,
            "raw_text": (
                "The client sends a key with the write. The server stores the key with"
                " the result, so a retry after a timeout returns the first result"
                " instead of charging twice. That is why retrying on timeout is safe"
                " here and would not be without it."
            ),
            "prompt_text": PROMPT,
            "prompt_version": "v1",
        },
    )
    append(
        conn,
        "explanation_graded",
        {"explanation_id": strong, "rubric": "solo-3", "level": "causal"},
        provenance=GRADER,
    )
    show(conn)
    print("\n    `causal` -- they can say why it holds. That is the listed -> causal")
    print("    boundary, and the only transition that closes a gap.")

    scene(8, "Regeneration: better detector and grader, replayed over the same history")
    print(f"    Before: {len(user_events(conn))} user events,"
          f" {len(derived_events(conn))} derived.\n")
    conn.execute("DELETE FROM events WHERE actor = 'system'")
    print(f"    Wiped every derived event -> {len(derived_events(conn))} left."
          f" User events untouched: {len(user_events(conn))}.\n")

    # Replay the machine's whole contribution: concepts, encounters, the merge,
    # the material. A partial replay would leave the graph looking like state
    # had regressed, which is a property of the replay, not of the store.
    for cid, name in (
        (idem, "idempotency"), (k8s, "K8s"), (kube, "Kubernetes"), (crdt, "CRDT")
    ):
        append(conn, "concept_created", {"concept_id": cid, "canonical_name": name})
    append(
        conn,
        "encounter_recorded",
        {
            "encounter_id": enc_idem,
            "concept_id": idem,
            "source": "transcript",
            "paraphrase": "A sharper paraphrase, from a detector that reads tool results properly.",
            "pointer": {"session_id": "sess-1", "line_start": 40, "line_end": 58},
        },
        provenance=DETECTOR_V2,
    )
    append(
        conn,
        "encounter_recorded",
        {
            "encounter_id": enc_k8s,
            "concept_id": k8s,
            "source": "transcript",
            "paraphrase": "Claude put the retry in a K8s CronJob instead of the app.",
            "pointer": {"session_id": "sess-1", "line_start": 61, "line_end": 70},
        },
        provenance=DETECTOR_V2,
    )
    append(
        conn,
        "concept_merged",
        {
            "from_concept_id": k8s,
            "into_concept_id": kube,
            "reasoning": "Numeronym for Kubernetes.",
        },
    )
    append(
        conn,
        "material_generated",
        {
            "material_id": material,
            "format": "textual_with_sources",
            "covers_concept_ids": [idem, crdt],
            "sources": ["https://example.invalid/idempotency-keys"],
            "body": "An idempotency key lets the server recognise a retry ...",
        },
    )
    append(conn, "material_delivered", {"material_id": material})

    # Re-grade from the raw answers, which are user events and therefore still
    # in the log. This is the whole reason raw_text is stored rather than just
    # the grade: a new rubric can re-run over the entire history.
    print("    Re-grading from the raw answers still in the log:\n")
    for row in conn.execute(
        "SELECT event_id, payload FROM events"
        " WHERE event_type = 'explanation_submitted' ORDER BY event_id"
    ).fetchall():
        payload = json.loads(row["payload"])
        text = payload["raw_text"]
        # A stand-in for a real grader: long and causal-sounding scores higher.
        level = "causal" if "that is why" in text.lower() else "isolated"
        print(f"      {text[:58] + ('...' if len(text) > 58 else ''):<62} -> {level}")
        append(
            conn,
            "explanation_graded",
            {"explanation_id": row["event_id"], "rubric": "solo-3", "level": level},
            provenance={"grader_version": "grader-0.2.0"},
        )

    print()
    show(conn)
    enc = conn.execute(
        "SELECT judgment, detector_version FROM compiled_encounters WHERE encounter_id = ?",
        (enc_idem,),
    ).fetchone()
    print(f"\n    Same graph as scene 7, rebuilt from scratch. Judgment survived:"
          f" {enc['judgment']!r},")
    print(f"    now sitting on {enc['detector_version']} instead of detector-0.1.0.")
    print("    The machine's output improved; the human's answers were never touched.")
    print("\n    This is the part that needed a bug fix. Replayed events get LATER")
    print("    ulids than the confirmation that refers to them, so folding")
    print("    judgments in event order would silently drop every confirm.")

    scene(9, "What the store refuses to do")
    print("    First, the one that does NOT raise. A detector calling this:\n")
    append(conn, "encounter_confirmed", {"encounter_id": enc_idem})
    row = conn.execute(
        "SELECT actor FROM events WHERE event_id = (SELECT MAX(event_id) FROM events)"
    ).fetchone()
    print(f"        append(conn, 'encounter_confirmed', ...)   -> wrote actor={row['actor']!r}")
    print("\n    ...just wrote a genuine user confirmation. There is no way to even")
    print("    ask for a different actor: it comes from the event's own spec, not")
    print("    from the caller. A detector cannot forge ground truth because it")
    print("    cannot address that field at all. Not a rule -- a missing parameter.")
    conn.execute("DELETE FROM events WHERE event_id = (SELECT MAX(event_id) FROM events)")
    conn.commit()

    print("\n    And the ones that do raise:\n")
    for label, call in (
        (
            "an unknown event type",
            lambda: append(conn, "user_got_bored", {"concept_id": idem}),
        ),
        (
            "a grade outside the rubric",
            lambda: append(
                conn,
                "explanation_graded",
                {"explanation_id": strong, "rubric": "solo-3", "level": "excellent"},
                provenance=GRADER,
            ),
        ),
        (
            "derived state with no version recorded",
            lambda: append(
                conn,
                "encounter_recorded",
                {
                    "encounter_id": "e-x",
                    "concept_id": idem,
                    "source": "transcript",
                    "paraphrase": "...",
                },
            ),
        ),
        (
            "an event missing a required field",
            lambda: append(conn, "concept_created", {"concept_id": "c-nameless"}),
        ),
    ):
        try:
            call()
        except EventValidationError as exc:
            print(f"    REFUSED  {label}\n             {exc}\n")
        else:
            print(f"    ALLOWED  {label}  <-- unexpected, the guard is gone\n")

    print(f"\n\033[1mStore written to {db_path}\033[0m")
    print("Poke at it:\n")
    print(f"    sqlite3 -box {db_path} 'SELECT canonical_name, state FROM compiled_concepts'")
    print(f"    sqlite3 -box {db_path} 'SELECT event_type, actor FROM events ORDER BY event_id'")
    print(f"    sqlite3 -box {db_path} 'SELECT raw_text, level FROM compiled_explanations'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
