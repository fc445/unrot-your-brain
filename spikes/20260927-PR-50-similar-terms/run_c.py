"""C. Classify every pair with the transfer test, plus two cheap baselines.

Settings, each run 3 times on identical input plus once with A and B swapped
(the swap measures order sensitivity, not stability):

* `ling-low`  -- inclusionai/ling-3.0-flash, reasoning effort low (the product's setting)
* `ling-off`  -- the same model with reasoning disabled (`Structured.forced`)
* `jev`       -- typesafe/jev-1.13, three Choice questions, probabilities kept

Baselines, no model:

* `pr38`      -- PR-38's matcher (imported by path). For A: does `shortlist()` over a
                 background graph plus B put B first with a non-zero score? Both ways.
* `embed`     -- cosine of bare-term embeddings (fastembed: bge-base-en-v1.5 and
                 all-MiniLM-L6-v2, both already cached by PR-34). Threshold chosen
                 later, against Freddie's labels.

Everything goes to `out/model_verdicts.json`. This script prints only cost,
latency and stability -- never a verdict -- because labelling is blind.

    uv run python run_c.py            # ~330 ling calls + ~260 Jev calls
    uv run python run_c.py --summary  # re-print the summary from the saved file
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from common import OUT, jev_transfer, ling_transfer, load_env, read_json, real_conn, verdict, write_json, pr38_match, PR38_TESTS

REPEATS = 3
SETTINGS = ("ling-low", "ling-off", "jev")


def call(setting: str, a: str, b: str):
    if setting == "jev":
        return jev_transfer(a, b)
    return ling_transfer(a, b, reasoning="low" if setting == "ling-low" else "off")


def unswap(v: str) -> str:
    return {"a_extends_b": "b_extends_a", "b_extends_a": "a_extends_b"}.get(v, v)


def run_models(pairs: list[dict], workers: int) -> list[dict]:
    jobs = []
    for p in pairs:
        for setting in SETTINGS:
            for r in range(REPEATS):
                jobs.append((p["id"], setting, r, False, p["a"], p["b"]))
            jobs.append((p["id"], setting, REPEATS, True, p["b"], p["a"]))

    def work(job):
        pid, setting, rep, swapped, a, b = job
        res = call(setting, a, b)
        v = None
        if res.answer and None not in (res.answer.get(k) for k in ("a_explains_b", "b_explains_a", "same_space")):
            v = verdict(res.answer["a_explains_b"], res.answer["b_explains_a"], res.answer["same_space"])
            if swapped:
                v = unswap(v)  # back to the displayed A/B orientation
        return {
            "id": pid,
            "setting": setting,
            "repeat": rep,
            "swapped": swapped,
            "asked_a": a,
            "asked_b": b,
            "answer": res.answer,
            "verdict": v,
            "seconds": round(res.seconds, 3),
            "cost": res.cost,
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "reasoning_tokens": res.reasoning_tokens,
            "error": res.error,
        }

    out = []
    started = time.monotonic()
    # Jev answers in ~0.3 s; ling-low in 5-20 s. One pool, interleaved.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, row in enumerate(pool.map(work, jobs), 1):
            out.append(row)
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)} calls, {time.monotonic() - started:.0f}s", flush=True)
    return out


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def background_graph(match):
    """PR-38's 51-concept fixture plus the real graph's concepts, as `Known`s."""
    import json
    import re

    text = PR38_TESTS.read_text()
    block = text.split("_GRAPH = [", 1)[1].split("\n]\n", 1)[0]
    known = {}
    for name, aliases, count in re.findall(r'^\s*\("([^"]+)",\s*\(([^)]*)\),\s*(\d+)\)', block, flags=re.M):
        al = tuple(re.findall(r'"([^"]+)"', aliases))
        known[match.normalise(name)] = match.Known(f"fx-{match.normalise(name)}", name, al, int(count))
    for row in real_conn().execute("SELECT concept_id, canonical_name, aliases, encounter_count FROM compiled_concepts"):
        n = match.normalise(row["canonical_name"])
        known.setdefault(
            n, match.Known(row["concept_id"], row["canonical_name"], tuple(json.loads(row["aliases"] or "[]")), row["encounter_count"])
        )
    return list(known.values())


def pr38_baseline(pairs: list[dict]) -> dict:
    match = pr38_match()
    bg = background_graph(match)
    out = {}
    for p in pairs:
        row = {}
        for src, dst, key in ((p["a"], p["b"], "a_finds_b"), (p["b"], p["a"], "b_finds_a")):
            ns, nd = match.normalise(src), match.normalise(dst)
            graph = [
                k for k in bg
                if not any(match.normalise(n) in (ns, nd) for n in k.names)
            ] + [match.Known("target", dst, (), 1)]
            exact = match.exact(graph, src)
            if exact is not None:
                row[key] = {"top1": exact.canonical_name, "hit": exact.concept_id == "target", "exact": True, "score": 1.0}
                continue
            weight = match._rarity(graph)
            listed = match.shortlist(graph, src)
            top = listed[0]
            score = max(match._overlap(src, n, weight) for n in top.names)
            rank = next((i for i, k in enumerate(listed, 1) if k.concept_id == "target"), None)
            row[key] = {"top1": top.canonical_name, "hit": top.concept_id == "target" and score > 0, "score": round(score, 3), "rank": rank}
        row["overlap_unweighted"] = round(match._overlap(p["a"], p["b"]), 3)
        row["verdict"] = "same" if row["a_finds_b"]["hit"] or row["b_finds_a"]["hit"] else "not_same"
        out[p["id"]] = row
    return out


def embed_baseline(pairs: list[dict]) -> dict:
    import numpy as np
    from fastembed import TextEmbedding

    terms = sorted({t for p in pairs for t in (p["a"], p["b"])})
    out = {p["id"]: {} for p in pairs}
    for model in ("BAAI/bge-base-en-v1.5", "sentence-transformers/all-MiniLM-L6-v2"):
        vecs = np.array(list(TextEmbedding(model).embed(terms)))
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        idx = {t: i for i, t in enumerate(terms)}
        for p in pairs:
            out[p["id"]][model] = round(float(vecs[idx[p["a"]]] @ vecs[idx[p["b"]]]), 4)
    return out


# ---------------------------------------------------------------------------
# Summary: cost, latency, stability. No verdicts.
# ---------------------------------------------------------------------------


def pct(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def summarise(rows: list[dict]) -> dict:
    summary = {}
    for setting in SETTINGS:
        mine = [r for r in rows if r["setting"] == setting]
        secs = [r["seconds"] for r in mine]
        costs = [r["cost"] for r in mine if r["cost"] is not None]
        errors = [r for r in mine if r["error"] or r["verdict"] is None]
        by_pair: dict[str, list] = {}
        swap: dict[str, str | None] = {}
        fields: dict[str, list] = {}
        for r in mine:
            if r["swapped"]:
                swap[r["id"]] = r["verdict"]
            else:
                by_pair.setdefault(r["id"], []).append(r["verdict"])
                fields.setdefault(r["id"], []).append(r["answer"])
        unanimous = sum(1 for v in by_pair.values() if len(v) == REPEATS and None not in v and len(set(v)) == 1)
        # Per-field agreement across the 3 repeats.
        field_agree = {}
        for f in ("a_explains_b", "b_explains_a", "same_space"):
            ok = sum(
                1 for answers in fields.values()
                if all(a for a in answers) and len({a.get(f) for a in answers}) == 1
            )
            field_agree[f] = ok
        # Order sensitivity: swapped-run verdict (mapped back) vs the repeats' majority.
        order_same = 0
        order_n = 0
        for pid, v in by_pair.items():
            maj = Counter(x for x in v if x).most_common(1)
            if maj and swap.get(pid):
                order_n += 1
                order_same += swap[pid] == maj[0][0]
        s = {
            "calls": len(mine),
            "errors_or_unparsed": len(errors),
            "cost_usd": round(sum(costs), 5),
            "cost_priced_calls": len(costs),
            "seconds_p50": round(pct(secs, 0.5), 2),
            "seconds_p90": round(pct(secs, 0.9), 2),
            "seconds_max": round(max(secs), 2),
            "pairs": len(by_pair),
            "unanimous_over_3_repeats": unanimous,
            "field_unanimous": field_agree,
            "order_swap_agrees_with_majority": f"{order_same}/{order_n}",
        }
        if setting == "ling-low":
            rt = [r["reasoning_tokens"] for r in mine if r["reasoning_tokens"] is not None]
            s["reasoning_tokens_p50"] = pct(rt, 0.5)
            s["reasoning_tokens_max"] = max(rt) if rt else None
        if setting == "jev":
            # Probability spread across repeats, averaged over pairs and fields.
            spreads = []
            for answers in fields.values():
                for f in ("a_explains_b", "b_explains_a", "same_space"):
                    ps = [a.get(f"p_{f}") for a in answers if a and a.get(f"p_{f}") is not None]
                    if len(ps) == REPEATS:
                        spreads.append(max(ps) - min(ps))
            s["prob_spread_mean"] = round(statistics.mean(spreads), 4) if spreads else None
            s["prob_spread_max"] = round(max(spreads), 4) if spreads else None
        summary[setting] = s
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    path = OUT / "model_verdicts.json"
    if args.summary:
        data = read_json(path)
        import json

        print(json.dumps(data["summary"], indent=1))
        return

    load_env()
    pairs = read_json(OUT / "pairs.json")
    started = time.monotonic()
    rows = run_models(pairs, args.workers)
    wall = time.monotonic() - started
    data = {
        "_warning": "Model verdicts. Do not show to the labeller before labels.json exists.",
        "settings": {
            "ling-low": "inclusionai/ling-3.0-flash, reasoning effort low, max_tokens 8000, temperature 0",
            "ling-off": "inclusionai/ling-3.0-flash, reasoning disabled (Structured.forced), max_tokens 4000, temperature 0",
            "jev": "typesafe/jev-1.13 via unrot.model.decision_client, 3 Choice questions; bool = p(yes) >= 0.5",
        },
        "repeats": REPEATS,
        "wall_seconds": round(wall, 1),
        "calls": rows,
        "baselines": {"pr38": pr38_baseline(pairs), "embed": embed_baseline(pairs)},
    }
    data["summary"] = summarise(rows)
    write_json(path, data)
    import json

    print(json.dumps(data["summary"], indent=1))
    print("wall seconds:", round(wall, 1))


if __name__ == "__main__":
    main()
