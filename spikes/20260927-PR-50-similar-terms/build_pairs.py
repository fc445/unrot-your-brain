"""A. Build the pair set: ~60 pairs of names, stratified by the relation I *intend*.

Writes two files:

* `out/pairs.json` -- what the labelling page and the classifiers see: an opaque
  id, term A, term B, in shuffled order. Nothing else.
* `out/pairs_key.json` -- the answer key Freddie must not see: intended stratum,
  intended label (relative to the A/B orientation actually shown), where each
  term came from, and a note.

Sources are *derived*, not asserted: each term is looked up in run1's detector
output, run1's store, the real graph and PR-38's fixture, and whatever it is
found in is recorded. A term found nowhere is a hand-added one.

Terms only. No paraphrase, no window text.
"""

from __future__ import annotations

import json
import random
import re

from common import OUT, E2E_RESULTS, PR38_TESTS, pr38_fixture_names, read_json, real_conn, run1_conn, write_json

SEED = 50

# (A, B, stratum, intended label with this orientation, hard?, note)
# Labels: same | same_nothing_extra | b_extends_a | a_extends_b | related_different | unrelated
PAIRS = [
    # --- spelling / word form: one concept, another surface ---------------------------------
    ("idempotent", "idempotency", "spelling_wordform", "same", False, "live duplicate in run1 (filed as new)"),
    ("idempotence", "idempotency", "spelling_wordform", "same", False, ""),
    ("notarised", "macOS notarization", "spelling_wordform", "same", False, "word form + UK/US + qualifier"),
    ("ad-hoc signing", "ad hoc signing", "spelling_wordform", "same", False, "hyphenation"),
    ("backpresure", "Backpressure", "spelling_wordform", "same", False, "typo (PR-38 probe)"),
    ("Screen Capture Kit", "ScreenCaptureKit", "spelling_wordform", "same", False, "spacing (PR-38 probe)"),
    ("rebase", "Git Rebase", "spelling_wordform", "same", False, "bare vs qualified name"),
    ("system accent colour", "system accent color", "spelling_wordform", "same", False, "UK/US"),
    ("multiversion concurrency control", "MVCC (Multi-Version Concurrency Control)", "spelling_wordform", "same", False, "spelled-out vs parenthetical (PR-38 probe)"),
    # --- abbreviation -----------------------------------------------------------------------
    ("UDS", "Unix Domain Socket", "abbreviation", "same", False, "PR-38 probe"),
    ("K8s", "Kubernetes", "abbreviation", "same", False, "numeronym (PR-38 probe)"),
    ("2PC", "two-phase commit", "abbreviation", "same", False, "PR-38 alias"),
    ("TCC", "macOS TCC (Transparency, Consent, and Control)", "abbreviation", "same", False, "run1 detection vs real graph"),
    ("xattr", "extended file attributes", "abbreviation", "same", False, ""),
    ("RCE", "remote code execution", "abbreviation", "same", False, ""),
    ("WAL", "write-ahead logging", "abbreviation", "same", False, ""),
    ("MCP", "Model Context Protocol", "abbreviation", "same", False, ""),
    # --- barely different, nothing extra to learn -------------------------------------------
    ("Developer ID", "Developer ID signing", "no_extension", "same_nothing_extra", False, "ticket example"),
    ("TCC", "TCC-protected", "no_extension", "same_nothing_extra", False, "ticket example; both run1 detections"),
    ("appcast", "Sparkle appcast", "no_extension", "same_nothing_extra", False, ""),
    ("reflog", "git reflog", "no_extension", "same_nothing_extra", False, ""),
    ("Circuit Breaker pattern", "circuit breaker", "no_extension", "same_nothing_extra", False, ""),
    ("Outbox Pattern", "transactional outbox", "no_extension", "same_nothing_extra", False, "run1 store name vs PR-38 fixture name"),
    ("named volume", "Docker named volume", "no_extension", "same_nothing_extra", False, ""),
    ("at-least-once", "at-least-once delivery", "no_extension", "same_nothing_extra", False, ""),
    ("MCP servers", "Model Context Protocol", "no_extension", "same_nothing_extra", True, "hard: protocol vs its servers; could be read as extension"),
    # --- narrower / broader (A broader, B narrower) -----------------------------------------
    ("Git Rebase", "rebase --onto", "narrower_broader", "b_extends_a", False, "ticket example"),
    ("launchd", "LaunchAgent", "narrower_broader", "b_extends_a", False, "ticket example; both in run1"),
    ("code signing", "ad hoc signing", "narrower_broader", "b_extends_a", False, "PR-38 fixture"),
    ("load testing", "soak testing", "narrower_broader", "b_extends_a", False, ""),
    ("TCC", "Full Disk Access", "narrower_broader", "b_extends_a", False, ""),
    ("copyleft", "GPL", "narrower_broader", "b_extends_a", False, ""),
    ("EdDSA", "Ed25519", "narrower_broader", "b_extends_a", False, ""),
    ("PAT", "personal access token vs service key", "narrower_broader", "b_extends_a", True, "hard: PR-38 probe treats this as a shortlist match"),
    ("write-ahead logging", "SQLite WAL mode", "narrower_broader", "b_extends_a", True, "hard: PR-38 fixture stores write-ahead logging as an alias of SQLite WAL mode"),
    ("optimistic concurrency control", "optimistic locking", "narrower_broader", "b_extends_a", True, "hard: often used interchangeably"),
    # --- prerequisite (A needed first, B builds on it) --------------------------------------
    ("Kubernetes", "Helm chart", "prerequisite", "b_extends_a", False, ""),
    ("hardened runtime", "macOS notarization", "prerequisite", "b_extends_a", False, "co-occur in real graph session 0805c0ae"),
    ("GitHub Actions", "actionlint", "prerequisite", "b_extends_a", False, ""),
    ("idempotency", "idempotency key", "prerequisite", "b_extends_a", False, ""),
    ("public-key cryptography", "EdDSA", "prerequisite", "b_extends_a", False, ""),
    ("concrete syntax tree", "tree-sitter", "prerequisite", "b_extends_a", False, ""),
    ("code signing", "Developer ID", "prerequisite", "b_extends_a", False, ""),
    ("at-least-once delivery", "idempotent consumer", "prerequisite", "b_extends_a", False, ""),
    # --- contrasting in the same space -----------------------------------------------------
    ("optimistic locking", "pessimistic locking", "contrasting", "related_different", False, "the resolver prompt's own trap"),
    ("at-least-once delivery", "exactly-once delivery", "contrasting", "related_different", False, ""),
    ("Git Merge", "Git Rebase", "contrasting", "related_different", False, ""),
    ("ad hoc signing", "Developer ID", "contrasting", "related_different", False, "co-occur in real graph session 33d84b15"),
    ("named volume", "bind mount", "contrasting", "related_different", False, ""),
    ("LaunchAgent", "LaunchDaemon", "contrasting", "related_different", False, ""),
    ("merge commit", "fast-forward merge", "contrasting", "related_different", False, ""),
    ("copyleft", "permissive license", "contrasting", "related_different", False, ""),
    ("Stop hook", "PreToolUse hook", "contrasting", "related_different", False, ""),
    ("Saga pattern", "two-phase commit", "contrasting", "related_different", False, ""),
    # --- co-occurring in one real session, but unrelated -----------------------------------
    ("named volume", "LaunchAgent", "cooccurring_unrelated", "unrelated", False, "run1 session 02cbbad7"),
    ("MCP servers", "copyleft", "cooccurring_unrelated", "unrelated", False, "run1 session 65de8a45"),
    ("Unix Domain Socket", "xattr", "cooccurring_unrelated", "unrelated", False, "session d2930d59"),
    ("Liquid Glass", "Git Worktree", "cooccurring_unrelated", "unrelated", False, "real graph session 13d5c0d2"),
    ("Full Keyboard Access", "merge commit", "cooccurring_unrelated", "unrelated", False, "run1 session 13d5c0d2"),
    ("soak testing", "xattr", "cooccurring_unrelated", "unrelated", False, "session 3830fa9c"),
    ("hardened runtime", "actionlint", "cooccurring_unrelated", "unrelated", False, "session 0805c0ae"),
    ("FSEvents", "NSEvent", "cooccurring_unrelated", "unrelated", True, "hard: look-alike names, same session 75704410"),
    ("NSServices", "FSEvents", "cooccurring_unrelated", "unrelated", True, "hard: both macOS APIs, same session 75704410"),
    ("ScreenCaptureKit", "re-signing", "cooccurring_unrelated", "unrelated", True, "hard: causally linked in session 13d5c0d2 (re-signing resets the capture permission)"),
    # --- synonym: a different name, not a surface variant (extra, hand-added) ---------------
    ("Walking Skeleton", "tracer bullet", "synonym", "same", True, "hard: often treated as synonyms, sometimes distinguished"),
    ("consistent hashing", "hash ring", "synonym", "same", True, "hard: the ring is the usual implementation"),
]

FLIP = {"a_extends_b": "b_extends_a", "b_extends_a": "a_extends_b"}


def vocabularies() -> dict[str, set[str]]:
    results = read_json(E2E_RESULTS)
    ranked = {r["term"] for p in results["pipeline"] for r in p.get("ranked") or []}
    returned = set()
    for call in results["calls"]:
        if call["stage"] in ("detection", "detection-repeat") and isinstance(call.get("returned"), list):
            returned |= {t["term"] for t in call["returned"] if isinstance(t, dict) and t.get("term")}
    run1_store = {r[0] for r in run1_conn().execute("SELECT canonical_name FROM compiled_concepts")}
    real = set()
    for name, aliases in real_conn().execute("SELECT canonical_name, aliases FROM compiled_concepts"):
        real.add(name)
        real |= set(json.loads(aliases or "[]"))
    fixture = set(pr38_fixture_names())
    text = PR38_TESTS.read_text()
    # Aliases in the fixture: ("SQLite WAL mode", ("write-ahead logging",), 3)
    fixture |= {alias for _, alias in re.findall(r'\("([^"]+)",\s*\("([^"]+)",\)', text)}
    probes = text.split("_PROBES = [", 1)[1].split("\n]\n", 1)[0]
    fixture_probes = {a for a, _ in re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', probes)}
    return {
        "run1_detector": ranked | returned,
        "run1_store": run1_store,
        "real_graph": real,
        "pr38_fixture": fixture,
        "pr38_probe": fixture_probes,
    }


def sources(term: str, vocab: dict[str, set[str]]) -> list[str]:
    found = [name for name, terms in vocab.items() if term in terms]
    return found or ["hand"]


def main() -> None:
    vocab = vocabularies()
    rng = random.Random(SEED)
    rows = list(PAIRS)
    rng.shuffle(rows)
    pairs, key = [], []
    seen = set()
    for i, (a, b, stratum, label, hard, note) in enumerate(rows, 1):
        assert frozenset((a, b)) not in seen, (a, b)
        seen.add(frozenset((a, b)))
        flipped = rng.random() < 0.5
        if flipped:
            a, b = b, a
            label = FLIP.get(label, label)
        pid = f"p{i:02d}"
        pairs.append({"id": pid, "a": a, "b": b})
        key.append(
            {
                "id": pid,
                "a": a,
                "b": b,
                "stratum": stratum,
                "intended_label": label,
                "hard": hard,
                "flipped_from_authoring_order": flipped,
                "source_a": sources(a, vocab),
                "source_b": sources(b, vocab),
                "note": note,
            }
        )
    write_json(OUT / "pairs.json", pairs)
    write_json(
        OUT / "pairs_key.json",
        {
            "_warning": "Answer key. Do not show to the labeller before labels.json exists.",
            "seed": SEED,
            "pairs": key,
        },
    )
    from collections import Counter

    print(len(pairs), "pairs")
    print(Counter(k["stratum"] for k in key))
    print("hard:", sum(k["hard"] for k in key))
    print(Counter(s for k in key for s in k["source_a"] + k["source_b"]))


if __name__ == "__main__":
    main()
