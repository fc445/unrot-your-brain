"""Unfiltered recall per extractor, with the misses named. No network.

    uv run python quick_recall.py
"""

import json

from common import GAPS, LOCAL, OUT, SYNTH_GAPS, SYNTH_NOT_GAPS, covered, matches

C = json.loads((OUT / "candidates.json").read_text())["candidates"]
ids = list(next(iter(C.values())).keys())
sid = lambda p: next(i for i in ids if i.startswith(p))  # noqa: E731

for name, cs in C.items():
    miss, ranks = [], []
    for p, gs in GAPS.items():
        lst = [c for c, _ in cs[sid(p)]]
        for g in gs:
            r = next((i for i, c in enumerate(lst) if matches(c, g)), None)
            (miss.append(g) if r is None else ranks.append(r))
    loc = [g for p, gs in LOCAL.items() for g in gs if covered([c for c, _ in cs[sid(p)]], g)]
    syn = [g for g in SYNTH_GAPS["sess-messy"] + SYNTH_NOT_GAPS["sess-messy"] if covered([c for c, _ in cs["sess-messy"]], g)]
    ranks.sort()
    print(f"{name}: gaps {len(ranks)}/{len(ranks) + len(miss)}, median rank {ranks[len(ranks) // 2] if ranks else None}, missed {miss}")
    print(f"   local labels kept {len(loc)}/8 {loc}")
    print(f"   synthetic found {syn}")

# Unions: does a second extractor cover what the first misses?
from statistics import mean  # noqa: E402

from signals import code_shape  # noqa: E402

for a, b in [("gliner", "noun_chunks"), ("gliner", "jk_cvalue"), ("noun_chunks", "rake")]:
    u = {s: list(dict.fromkeys([c for c, _ in C[a][s]] + [c for c, _ in C[b][s]])) for s in ids}
    u = {s: [c for c in v if not code_shape(c)] for s, v in u.items()}
    hits = sum(covered(u[sid(p)], g) for p, gs in GAPS.items() for g in gs)
    loc = sum(covered(u[sid(p)], g) for p, gs in LOCAL.items() for g in gs)
    n = mean(len(u[s]) for s in ids if not s.startswith("sess-"))
    print(f"union {a}+{b} (code-filtered): gaps {hits}/27, local kept {loc}/8, mean cands {n:.0f}")
