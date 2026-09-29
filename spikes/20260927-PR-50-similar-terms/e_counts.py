"""E. Counting units: mentions vs sessions vs occasions, and duplicate-transcript inflation.

For every concept in the real graph (~/.unrot, read-only) and in run1's store:

* `encounters`            -- what the graph holds today (resolver-filed).
* `encounter_sessions`    -- distinct sessions among those encounters.
* `mentions`              -- windows (`build_windows`) across ALL captured sessions
                             whose assistant or human text names the concept, by
                             canonical name, alias, or parenthetical part
                             ("MVCC", "Multi-Version Concurrency Control").
                             Two matchers: `exact` (case-insensitive, word-bounded,
                             hyphen/space tolerant) and `stem` (token stems from
                             PR-38's matcher, so `idempotent` counts for `idempotency`).
* `mention_sessions`      -- distinct sessions among those windows.
* `occasions_{1h,1d,1w}`  -- mention timestamps (the transcript's own time for the
                             assistant turn) clustered so a gap larger than the
                             threshold starts a new occasion.
* all of the above again with windows whose text is byte-identical to a window
  in another session counted once (`dedup`), to see duplicate transcripts inflate.

The window corpus is run1's raw store (every session in ~/.claude/projects at
the time of the e2e run). Writes `out/e_counts.json`: concept names and numbers
only. Prints the aggregate findings.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta

from common import OUT, pr38_match, prose, raw_conn, real_conn, run1_conn, term_pattern, write_json

GAPS = {"1h": timedelta(hours=1), "1d": timedelta(days=1), "1w": timedelta(weeks=1)}
_PAREN = re.compile(r"\(([^)]*)\)")


def names_for(canonical: str, aliases: list[str]) -> list[str]:
    out = []
    for name in [canonical, *aliases]:
        forms = [name]
        inner = _PAREN.findall(name)
        if inner:
            forms.append(_PAREN.sub(" ", name).strip())
            forms.extend(i.strip() for i in inner)
        for f in forms:
            # snake_case scaffolding names ("merge_vs_linear") read as words
            f = re.sub(r"\s+", " ", f.replace("_", " ")).strip()
            if f and f not in out:
                out.append(f)
    return out


def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def corpus():
    """Every window in run1's raw store, with its time, session and a content hash."""
    from unrot.detector.windows import build_windows

    conn = raw_conn()
    sids = [r[0] for r in conn.execute("SELECT DISTINCT session_id FROM raw_turns ORDER BY session_id")]
    times = {}
    for sid, line, at in conn.execute("SELECT session_id, line_no, MIN(occurred_at) FROM raw_turns GROUP BY 1, 2"):
        times[(sid, line)] = at
    windows = []
    for sid in sids:
        for w in build_windows(conn, sid):
            text = prose(w.assistant_text) + "\n\n" + (w.human_text or "")
            windows.append({
                "session": sid,
                "line": w.assistant_line,
                "at": parse_time(times.get((sid, w.assistant_line))),
                "text": text,
                "hash": hashlib.sha256(w.assistant_text.encode()).hexdigest(),
            })
    # A window is a duplicate if the same assistant text occurs in an earlier-sorted session.
    first_session = {}
    for w in windows:
        first_session.setdefault(w["hash"], w["session"])
    for w in windows:
        w["dup"] = first_session[w["hash"]] != w["session"]
    return windows, times


def stem_seq(match, text: str) -> list[str]:
    return [match._stem(t) for t in match.normalise(text).split()]


def occasions(stamps: list[datetime], gap: timedelta) -> int:
    stamps = sorted(s for s in stamps if s)
    if not stamps:
        return 0
    n = 1
    for prev, cur in zip(stamps, stamps[1:]):
        if cur - prev > gap:
            n += 1
    return n


def concepts(conn, times) -> list[dict]:
    rows = []
    for c in conn.execute(
        "SELECT concept_id, canonical_name, aliases, state, encounter_count FROM compiled_concepts WHERE merged_into IS NULL"
    ):
        encs = conn.execute(
            "SELECT session_id, line_start, occurred_at, source FROM compiled_encounters WHERE concept_id = ?",
            (c["concept_id"],),
        ).fetchall()
        enc_times = []
        for e in encs:
            t = parse_time(times.get((e["session_id"], e["line_start"]))) if e["session_id"] else None
            enc_times.append(t or parse_time(e["occurred_at"]))  # manual entries: the event time
        rows.append({
            "concept_id": c["concept_id"],
            "name": c["canonical_name"],
            "names": names_for(c["canonical_name"], json.loads(c["aliases"] or "[]")),
            "state": c["state"],
            "encounters": len(encs),
            "encounter_sessions": len({e["session_id"] or f"manual:{i}" for i, e in enumerate(encs)}),
            "encounter_session_ids": sorted({(e["session_id"] or "manual")[:8] for e in encs}),
            "encounter_occasions": {k: occasions(enc_times, g) for k, g in GAPS.items()},
        })
    return rows


def count(rows: list[dict], windows: list[dict], match) -> None:
    stems = [(w, stem_seq(match, w["text"])) for w in windows]
    for r in rows:
        pats = [term_pattern(n) for n in r["names"]]
        seqs = [s for s in (stem_seq(match, n) for n in r["names"]) if s]
        hit_exact, hit_stem = [], []
        for w, toks in stems:
            if any(p.search(w["text"]) for p in pats):
                hit_exact.append(w)
            if any(
                toks[i:i + len(s)] == s for s in seqs for i in range(len(toks) - len(s) + 1)
            ):
                hit_stem.append(w)
        for label, hits in (("exact", hit_exact), ("stem", hit_stem)):
            for dedup in (False, True):
                use = [w for w in hits if not (dedup and w["dup"])]
                key = f"{label}{'_dedup' if dedup else ''}"
                r[f"mentions_{key}"] = len(use)
                r[f"mention_sessions_{key}"] = len({w["session"] for w in use})
                for g, gap in GAPS.items():
                    r[f"occasions_{g}_{key}"] = occasions([w["at"] for w in use], gap)
        r["mention_session_ids_exact"] = sorted({w["session"][:8] for w in hit_exact})


def summarise(rows: list[dict], label: str) -> dict:
    n = len(rows)
    s = {"concepts": n}
    s["encounter_persistent_2plus_sessions"] = sum(r["encounter_sessions"] >= 2 for r in rows)
    s["encounters_2plus"] = sum(r["encounters"] >= 2 for r in rows)
    for key in ("exact", "stem", "exact_dedup", "stem_dedup"):
        s[f"mention_persistent_{key}"] = sum(r[f"mention_sessions_{key}"] >= 2 for r in rows)
        s[f"never_mentioned_{key}"] = sum(r[f"mentions_{key}"] == 0 for r in rows)
    s["stem_finds_more_windows"] = sum(r["mentions_stem"] > r["mentions_exact"] for r in rows)
    s["dedup_changes_mentions"] = sum(r["mentions_exact_dedup"] != r["mentions_exact"] for r in rows)
    s["dedup_changes_sessions"] = sum(r["mention_sessions_exact_dedup"] != r["mention_sessions_exact"] for r in rows)
    s["dedup_changes_occasions_1h"] = sum(r["occasions_1h_exact_dedup"] != r["occasions_1h_exact"] for r in rows)
    # Where units disagree: many mentions but one session; many sessions but one occasion at 1d; etc.
    s["mentions_ge5_but_1_session"] = sum(r["mentions_exact"] >= 5 and r["mention_sessions_exact"] == 1 for r in rows)
    s["sessions_ge2_but_1_occasion_1h"] = sum(r["mention_sessions_exact"] >= 2 and r["occasions_1h_exact"] == 1 for r in rows)
    s["sessions_ge2_but_1_occasion_1d"] = sum(r["mention_sessions_exact"] >= 2 and r["occasions_1d_exact"] == 1 for r in rows)
    s["sessions_ge2_but_1_occasion_1w"] = sum(r["mention_sessions_exact"] >= 2 and r["occasions_1w_exact"] == 1 for r in rows)
    s["occasions_1h_gt_sessions"] = sum(r["occasions_1h_exact"] > r["mention_sessions_exact"] for r in rows)
    s["encounter_sessions_lt_mention_sessions"] = sum(r["encounter_sessions"] < r["mention_sessions_exact"] for r in rows)
    return s


def main() -> None:
    match = pr38_match()
    windows, times = corpus()
    span = [w["at"] for w in windows if w["at"]]
    dup_windows = [w for w in windows if w["dup"]]
    dup_pairs = defaultdict(int)
    first = {}
    for w in windows:
        first.setdefault(w["hash"], w["session"])
    for w in dup_windows:
        dup_pairs[(first[w["hash"]][:8], w["session"][:8])] += 1
    out = {"corpus": {
        "windows": len(windows),
        "sessions": len({w["session"] for w in windows}),
        "first": min(span).isoformat() if span else None,
        "last": max(span).isoformat() if span else None,
        "duplicate_windows": len(dup_windows),
        "duplicate_session_pairs": {f"{a}~{b}": n for (a, b), n in dup_pairs.items()},
    }}
    for label, conn in (("real_graph", real_conn()), ("run1_store", run1_conn())):
        rows = concepts(conn, times)
        count(rows, windows, match)
        out[label] = {"summary": summarise(rows, label), "concepts": rows}
    write_json(OUT / "e_counts.json", out)
    print(json.dumps(out["corpus"], indent=1))
    for label in ("real_graph", "run1_store"):
        print(label, json.dumps(out[label]["summary"], indent=1))


if __name__ == "__main__":
    main()
