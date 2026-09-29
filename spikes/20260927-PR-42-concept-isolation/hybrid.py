"""H8: a deterministic extractor proposes, the LLM only judges that fixed list.

    uv run python hybrid.py --extractor gliner --sessions 33d84b15 0805c0ae sess-messy --repeats 3

Same chunks, same prompt and same schema as today's detector
(`unrot.detector`), with one block added: the candidate terms that occur in
the chunk, and an instruction to return only terms from it. Anything returned
that is not on the list is dropped and counted. Results go to out/hybrid.json;
the baseline comparison is recomputed from the e2e run's results.json.

Model: inclusionai/ling-3.0-flash through OpenRouter, via unrot's own
ModelConfig / build_proposer, reasoning effort "low" (the detector's default).
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations

from dotenv import load_dotenv

from common import OUT, REPO_MAIN, RESULTS, load_sessions, norm, write_json
from signals import code_shape

MODEL = "inclusionai/ling-3.0-flash"

LIST_BLOCK = """\
Candidate terms. A mechanical extractor pulled these noun phrases out of the \
assistant's text below. It does not understand the session, so the list is \
noisy: ordinary words, code identifiers, and names coined inside this project \
are all in it, and most entries should be rejected. You may ONLY return terms \
from this list, copied exactly as written here. If the right term is not on the \
list, return nothing for it.

{terms}

"""


def render_with_list(prompt_module, chunk, terms, max_candidates):
    base = prompt_module.render(prompt_module.format_windows(chunk), max_candidates=max_candidates)
    block = LIST_BLOCK.format(terms="\n".join(f"- {t}" for t in terms))
    return base.replace("Excerpts:\n", block + "Excerpts:\n", 1)


def chunk_terms(session_cands, chunk):
    text = "\n".join(w.assistant_text for w in chunk).casefold()
    return [t for t in session_cands if t.casefold() in text]


def jaccard(a, b):
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def mean_pairwise(sets):
    pairs = list(combinations(sets, 2))
    return round(statistics.mean(jaccard(a, b) for a, b in pairs), 2) if pairs else None


def baseline(prefixes):
    """Today's detector on the same sessions, from the e2e run: main pass + repeats."""
    r = json.loads(RESULTS.read_text())
    out = {}
    for p in prefixes:
        runs = [x for x in r["repeats"] if x["session_id"].startswith(p) and x.get("ok")]
        main = [s for s in r["pipeline"] if s["session_id"].startswith(p) and s.get("ok")]
        runs += [{"ranked": s["ranked"], "emitted": s.get("detector_emitted", [])} for s in main]
        calls = [c["seconds"] for c in r["calls"] if c["stage"] == "detection" and c["session_id"].startswith(p)]
        rep_secs = [x["seconds"] for x in r["repeats"] if x["session_id"].startswith(p)]
        reasoning = [m.get("reasoning_tokens") for x in r["repeats"] if x["session_id"].startswith(p) for m in x.get("meter", []) if m.get("reasoning_tokens")]
        emitted = [frozenset(" ".join(norm(t if isinstance(t, str) else t["term"])) for t in x.get("emitted", [])) for x in runs]
        ranked = [frozenset(" ".join(norm(c["term"])) for c in x.get("ranked", [])) for x in runs]
        out[p] = {
            "runs": len(runs),
            "ranked_terms_per_run": [sorted(s) for s in ranked],
            "jaccard_emitted": mean_pairwise(emitted),
            "jaccard_ranked": mean_pairwise(ranked),
            "seconds_main_calls": calls,
            "seconds_repeats": rep_secs,
            "reasoning_tokens_repeats": reasoning,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractor", default="gliner")
    ap.add_argument("--sessions", nargs="+", default=["33d84b15", "0805c0ae", "sess-messy"])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--cap", type=int, default=150, help="max candidates per session list")
    ap.add_argument("--dry-run", action="store_true", help="print list sizes and prompt sizes, no calls")
    args = ap.parse_args()

    load_dotenv(REPO_MAIN / ".env")
    from unrot.detector import prompt as prompt_module
    from unrot.detector.detect import DEFAULT_MAX_CANDIDATES, assemble, plan
    from unrot.detector.model import build_proposer
    from unrot.capture.ingest import connect as connect_raw
    from unrot.model import ModelConfig
    from unrot.spend import Meter

    from common import HOME

    cands = json.loads((OUT / "candidates.json").read_text())["candidates"][args.extractor]
    sessions = {s.session_id: s for s in load_sessions()}
    conn = connect_raw(HOME)
    config = ModelConfig.from_env(model=MODEL)

    jobs = []
    for p in args.sessions:
        sid = next(i for i in sessions if i.startswith(p))
        windows, chunks = plan(conn, sid)
        session_list = [c for c, _ in cands[sid] if not code_shape(c)][: args.cap]
        prompts = []
        for chunk in chunks:
            terms = chunk_terms(session_list, chunk)
            prompts.append((render_with_list(prompt_module, chunk, terms, DEFAULT_MAX_CANDIDATES), terms))
        print(f"{p}: {len(chunks)} chunk(s), list sizes {[len(t) for _, t in prompts]}, prompt chars {[len(x) for x, _ in prompts]}")
        for rep in range(args.repeats):
            jobs.append((p, sid, rep, windows, prompts))
    if args.dry_run:
        return

    def run(job):
        p, sid, rep, windows, prompts = job
        meter = Meter()
        propose = build_proposer(config, meter=meter)
        raw, off_list, secs = [], [], []
        for text, terms in prompts:
            allowed = {norm(t) for t in terms}
            t0 = time.perf_counter()
            items = propose(text)
            secs.append(round(time.perf_counter() - t0, 1))
            for it in items:
                (raw if norm(it.get("term", "")) in allowed else off_list).append(it)
        result = assemble(raw, session_id=sid, version="hybrid-spike", windows=windows, calls_made=len(prompts))
        return {
            "session": p,
            "repeat": rep,
            "seconds_per_call": secs,
            "ranked": [c.term for c in result.ranked],
            "ranked_signals": [(c.term, c.signal, c.importance) for c in result.ranked],
            "emitted": [c.term for c in result.emitted],
            "off_list_returned": [it.get("term") for it in off_list],
            "meter": [c.payload() for c in meter.calls],
        }

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run, jobs))
    wall = time.perf_counter() - started

    summary = {}
    for p in args.sessions:
        rs = [r for r in results if r["session"] == p]
        summary[p] = {
            "jaccard_emitted": mean_pairwise([frozenset(" ".join(norm(t)) for t in r["emitted"]) for r in rs]),
            "jaccard_ranked": mean_pairwise([frozenset(" ".join(norm(t)) for t in r["ranked"]) for r in rs]),
            "seconds_per_call": [s for r in rs for s in r["seconds_per_call"]],
            "reasoning_tokens": [m.get("reasoning_tokens") for r in rs for m in r["meter"]],
            "cost": round(sum(m.get("cost") or 0 for r in rs for m in r["meter"]), 5),
            "ranked_per_run": [r["ranked"] for r in rs],
            "emitted_per_run": [r["emitted"] for r in rs],
            "off_list_per_run": [r["off_list_returned"] for r in rs],
        }
    out = {
        "extractor": args.extractor,
        "cap": args.cap,
        "model": MODEL,
        "wall_seconds": round(wall, 1),
        "summary": summary,
        "baseline": baseline(args.sessions),
        "runs": results,
    }
    write_json(OUT / f"hybrid-{args.extractor}.json", out)
    for p in args.sessions:
        s, b = summary[p], out["baseline"][p]
        print(f"\n== {p}")
        print(f"  hybrid   J_emitted={s['jaccard_emitted']} J_ranked={s['jaccard_ranked']} secs={s['seconds_per_call']} reasoning={s['reasoning_tokens']} cost=${s['cost']}")
        print(f"           ranked={s['ranked_per_run']} off_list={s['off_list_per_run']}")
        print(f"  baseline J_emitted={b['jaccard_emitted']} J_ranked={b['jaccard_ranked']} runs={b['runs']} secs(main)={b['seconds_main_calls']} secs(repeats)={b['seconds_repeats']} reasoning={b['reasoning_tokens_repeats']}")
        print(f"           ranked={b['ranked_terms_per_run']}")


if __name__ == "__main__":
    main()
