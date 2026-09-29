"""D, step 1: put candidates into decision windows and chunks. No model calls.

Two candidate sources per run1 session:

* `detector` -- every term the detector's main pass returned (pre-budget, so a
  superset of `ranked`), placed by its `assistant_line`.
* `wide`     -- PR-42's GLiNER + spaCy noun_chunks lists (union), code-shaped
  phrases dropped, leading determiners stripped, then placed in every window
  group whose assistant prose (fenced code removed) mentions it.

Units: a *group* is `group_windows()` output (assistant turns sharing one human
reply: the decision window); a *chunk* is `chunk_windows()` output (what one
detector call sees).

Writes `out/d_candidates.json`: per session, per unit, the candidate terms.
Terms only -- no window text.
"""

from __future__ import annotations

import re
from collections import Counter

from common import E2E_RESULTS, OUT, PR42_CANDIDATES, prose, raw_conn, read_json, term_pattern, write_json

# PR-42's H5a code filter (paths, file names, dotted/snake identifiers, flags).
_PATHY = re.compile(r"[/\\~$@=<>(){}\[\]]|\w\.\w|_|^-|\.\w{1,5}$|::")
_LEADING = {"the", "a", "an", "this", "that", "these", "those", "its", "their", "our", "your", "my", "his", "her",
            "any", "each", "every", "some", "no", "all", "both", "which", "what", "one", "another", "other"}
_PRONOUNS = {"it", "i", "you", "we", "they", "he", "she", "them", "us", "me", "him", "this", "that", "these",
             "those", "what", "which", "who", "something", "anything", "nothing", "everything", "one", "there",
             "here", "itself", "yourself", "yes", "no", "ok", "okay", "thing", "things", "way", "lot", "bit"}


def clean(term: str) -> str | None:
    t = term.strip().strip("`'\".,:;!?")
    if not t or _PATHY.search(t):
        return None
    words = t.split()
    while words and words[0].casefold() in _LEADING:
        words = words[1:]
    t = " ".join(words)
    if len(t) < 3 or not re.search(r"[A-Za-z]", t) or t.casefold() in _PRONOUNS:
        return None
    if len(words) > 5:
        return None
    return t


def load_sessions():
    from unrot.detector.windows import build_windows, chunk_windows, group_windows

    conn = raw_conn()
    results = read_json(E2E_RESULTS)
    sessions = [p["session_id"] for p in results["pipeline"]]
    out = {}
    for sid in sessions:
        windows = build_windows(conn, sid)
        groups = group_windows(windows)
        chunks = chunk_windows(windows)
        out[sid] = (windows, groups, chunks)
    return out


def unit_of(line: int, units) -> int | None:
    for i, unit in enumerate(units):
        if any(w.assistant_line == line for w in unit):
            return i
    # Fallback: the unit whose line span covers it.
    for i, unit in enumerate(units):
        if unit[0].line_start <= line <= unit[-1].line_end:
            return i
    return None


def detector_terms(results) -> dict[str, list[tuple[str, int]]]:
    """(term, assistant_line) per session, main detection pass only."""
    found: dict[str, list] = {}
    for call in results["calls"]:
        if call["stage"] != "detection" or not isinstance(call.get("returned"), list):
            continue
        for t in call["returned"]:
            if isinstance(t, dict) and t.get("term") and t.get("assistant_line") is not None:
                found.setdefault(call["session_id"], []).append((t["term"], int(t["assistant_line"])))
    return found


def wide_terms() -> dict[str, list[str]]:
    data = read_json(PR42_CANDIDATES)["candidates"]
    out: dict[str, list[str]] = {}
    for extractor in ("gliner", "noun_chunks"):
        for sid, rows in data[extractor].items():
            for row in rows:
                term = row[0] if isinstance(row, list) else row
                t = clean(term)
                if t:
                    out.setdefault(sid, []).append(t)
    # Dedupe case-insensitively, keeping the first spelling.
    for sid, terms in out.items():
        seen, keep = set(), []
        for t in terms:
            k = t.casefold()
            if k not in seen:
                seen.add(k)
                keep.append(t)
        out[sid] = keep
    return out


def main() -> None:
    results = read_json(E2E_RESULTS)
    sessions = load_sessions()
    det = detector_terms(results)
    wide = wide_terms()
    data = {}
    stats = Counter()
    for sid, (windows, groups, chunks) in sessions.items():
        row = {"windows": len(windows), "groups": len(groups), "chunks": len(chunks), "detector": {}, "wide": {}}
        # detector
        for unit_name, units in (("group", groups), ("chunk", chunks)):
            per: dict[int, list[str]] = {}
            for term, line in det.get(sid, []):
                i = unit_of(line, units)
                if i is not None and term not in per.setdefault(i, []):
                    per[i].append(term)
            row["detector"][unit_name] = {str(k): v for k, v in per.items()}
        row["detector"]["session"] = sorted({t for t, _ in det.get(sid, [])})
        # wide: locate by mention in assistant prose
        terms = wide.get(sid, [])
        pats = [(t, term_pattern(t)) for t in terms]
        group_text = [prose("\n\n".join(w.assistant_text for w in g)) for g in groups]
        per_group: dict[int, list[str]] = {}
        for i, text in enumerate(group_text):
            hits = [t for t, p in pats if p.search(text)]
            if hits:
                per_group[i] = hits
        # chunk = union of its groups' hits
        per_chunk: dict[int, list[str]] = {}
        gi = 0
        for ci, chunk in enumerate(chunks):
            lines = {w.assistant_line for w in chunk}
            acc = []
            for i, g in enumerate(groups):
                if g[0].assistant_line in lines:
                    for t in per_group.get(i, []):
                        if t not in acc:
                            acc.append(t)
            if acc:
                per_chunk[ci] = acc
        row["wide"]["group"] = {str(k): v for k, v in per_group.items()}
        row["wide"]["chunk"] = {str(k): v for k, v in per_chunk.items()}
        row["wide"]["session"] = sorted({t for v in per_group.values() for t in v})
        data[sid] = row
        stats["windows"] += len(windows)
        stats["groups"] += len(groups)
        stats["chunks"] += len(chunks)
    write_json(OUT / "d_candidates.json", data)

    # Size report (no terms printed).
    for source in ("detector", "wide"):
        for unit in ("group", "chunk"):
            sizes = [len(v) for row in data.values() for v in row[source][unit].values()]
            ge3 = sum(1 for s in sizes if s >= 3)
            pairs = sum(s * (s - 1) // 2 for s in sizes)
            print(f"{source:8s} {unit:5s}: units with any={len(sizes)}, >=3 candidates={ge3}, "
                  f"max={max(sizes) if sizes else 0}, within-unit pairs={pairs}")
        sess = [len(row[source]["session"]) for row in data.values()]
        print(f"{source:8s} session: sessions with >=3 candidates={sum(1 for s in sess if s >= 3)} of {len(sess)}")
    print(dict(stats))
    distinct = {t for row in data.values() for v in row["wide"]["group"].values() for t in v}
    print("wide distinct terms placed:", len(distinct))


if __name__ == "__main__":
    main()
