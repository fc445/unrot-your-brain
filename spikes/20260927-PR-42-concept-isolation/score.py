"""Score every extractor x filter chain against the gold terms.

    uv run python score.py            # rarity / code / repo filters only
    uv run python score.py --wiki     # + Wikipedia-title filters (bare terms to a public API)

Reads out/candidates.json, writes out/scores.json and prints a markdown table.
The table itself contains only counts and the ticket's own gold terms, so it is
safe to paste into the README; out/scores.json also lists per-session misses.
"""

from __future__ import annotations

import argparse
import json
from statistics import mean

from common import GAPS, LOCAL, OUT, SPIKE, SYNTH_GAPS, SYNTH_NOT_GAPS, backticked, covered, load_sessions, matches, write_json
from signals import camel, code_shape, in_repo, repo_for, wiki_titles, zipf_min

K = 50
#: Wikipedia lookups are limited to these: the public API throttles, and the
#: full J&K list is ~8k distinct terms over 36 sessions (~16k title variants).
#: `jk_cvalue@150` is the J&K list cut to its top 150 per session after the
#: code filter, as a prefilter would ship it.
WIKI_EXTRACTORS = ("gliner", "jk_cvalue@150")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", action="store_true")
    ap.add_argument("--rare", type=float, default=3.5)
    args = ap.parse_args()

    data = json.loads((OUT / "candidates.json").read_text())
    cands, timing, det = data["candidates"], data["timing"], data["deterministic"]
    sessions = {s.session_id: s for s in load_sessions()}
    ids = list(sessions)
    sid = lambda p: next(i for i in ids if i.startswith(p))  # noqa: E731
    worktree = SPIKE.parents[1]
    roots = {i: repo_for(s.project_dir, worktree) for i, s in sessions.items()}
    ticks = {i: {b.casefold() for b in backticked(s.assistant_text)} for i, s in sessions.items()}

    cands["jk_cvalue@150"] = {s: [x for x in v if not code_shape(x[0])][:150] for s, v in cands["jk_cvalue"].items()}
    timing["jk_cvalue@150"], det["jk_cvalue@150"] = timing["jk_cvalue"], det["jk_cvalue"]

    wiki = {}
    if args.wiki:
        terms = sorted({c for n in WIKI_EXTRACTORS if n in cands for v in cands[n].values() for c, _ in v if not code_shape(c)})
        print(f"wikipedia title lookup: {len(terms)} bare terms")
        wiki = wiki_titles(terms)
    linkable = lambda t: wiki.get(t) in ("article", "disambiguation")  # noqa: E731

    T = args.rare
    chains = {
        "raw": lambda t, s: True,
        "code": lambda t, s: not code_shape(t),
        "code+camel+ticks": lambda t, s: not code_shape(t) and not camel(t) and t.casefold() not in ticks[s],
        f"code+rare<{T}": lambda t, s: not code_shape(t) and zipf_min(t) < T,
        "code+repo-local-drop": lambda t, s: not code_shape(t) and not (in_repo(t, roots[s]) and not linkable(t)),
    }
    if args.wiki:
        chains["code+wiki"] = lambda t, s: not code_shape(t) and linkable(t)
        chains[f"code+(rare<{T}|wiki)"] = lambda t, s: not code_shape(t) and (zipf_min(t) < T or linkable(t))
        chains[f"code+(rare<{T}|wiki)-repo-local"] = lambda t, s: (
            not code_shape(t) and (zipf_min(t) < T or linkable(t)) and not (in_repo(t, roots[s]) and not linkable(t))
        )

    rows, detail = [], {}
    for name, per_session in cands.items():
        for cname, keep in chains.items():
            if "wiki" in cname or "repo" in cname:
                if name not in WIKI_EXTRACTORS:
                    continue
            kept = {s: [c for c, _ in v if keep(c, s)] for s, v in per_session.items()}
            gap_hits, gap_hits_k, misses = 0, 0, []
            total = 0
            for p, gs in GAPS.items():
                lst = kept[sid(p)]
                for g in gs:
                    total += 1
                    idx = next((i for i, c in enumerate(lst) if matches(c, g)), None)
                    if idx is None:
                        misses.append(g)
                    else:
                        gap_hits += 1
                        gap_hits_k += idx < K
            local_kept = [g for p, gs in LOCAL.items() for g in gs if covered(kept[sid(p)], g)]
            sg = [g for g in SYNTH_GAPS["sess-messy"] if covered(kept["sess-messy"], g)]
            sn = [g for g in SYNTH_NOT_GAPS["sess-messy"] if covered(kept["sess-messy"], g)]
            real = [s for s in kept if not s.startswith("sess-")]
            row = {
                "extractor": name,
                "filter": cname,
                "gap_recall": f"{gap_hits}/{total}",
                f"gap_recall@{K}": f"{gap_hits_k}/{total}",
                "synth_gaps": f"{len(sg)}/2",
                "synth_not_gaps": f"{len(sn)}/2",
                "local_kept": f"{len(local_kept)}/8",
                "mean_cands": round(mean(len(kept[s]) for s in real), 1),
                "deterministic": det.get(name),
                "seconds_36_sessions": min(timing[name]["seconds_per_run"]),
            }
            rows.append(row)
            detail[f"{name}|{cname}"] = {"missed_gaps": misses, "local_kept": local_kept}

    write_json(OUT / "scores.json", {"rows": rows, "detail": detail, "K": K, "rare_threshold": T})
    cols = list(rows[0].keys())
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(str(r[c]) for c in cols) + " |")


if __name__ == "__main__":
    main()
