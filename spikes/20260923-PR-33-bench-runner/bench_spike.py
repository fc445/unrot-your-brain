"""PR-33 spike: a benchmark runner that never touches ~/.unrot, and a scorer
that reads only the files the runner wrote.

    uv run python spikes/20260923-PR-33-bench-runner/bench_spike.py run \
        --models sim/steady,sim/flaky --effort low,medium --repeats 3 --yes
    uv run python spikes/20260923-PR-33-bench-runner/bench_spike.py score OUT_DIR

`sim/*` models are stubs (no network, no key). `--live` swaps in a real
OpenAI-compatible client with reasoning effort + max_tokens -- written, NOT run
(no key and openrouter.ai is blocked from the container this was written in).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REAL_HOME = Path.home() / ".unrot"

# Point unrot somewhere disposable BEFORE importing it, so nothing that falls
# back to $UNROT_HOME can resolve to the real store.
_TMP = Path(tempfile.mkdtemp(prefix="unrot-bench-"))
os.environ["UNROT_HOME"] = str(_TMP / "home")

sys.path.insert(0, str(HERE.parents[1] / "src"))
from unrot.capture import connect as connect_raw  # noqa: E402
from unrot.capture import ingest_file  # noqa: E402
from unrot.detector import build_windows, chunk_windows, detect  # noqa: E402
from unrot.detector import prompt as prompt_module  # noqa: E402
from unrot.spend import Call, Meter  # noqa: E402

#: Made-up $/token for the stubs. Real runs would read OpenRouter's /models pricing.
SIM_PRICES = {"sim/steady": (0.10e-6, 0.40e-6), "sim/flaky": (0.02e-6, 0.08e-6)}
MAX_TOKENS = 4000
SMALL = 5


def _snapshot(path: Path):
    if not path.exists():
        return None
    return sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in path.rglob("*"))


# ---------------------------------------------------------------------------
# Proposers
# ---------------------------------------------------------------------------

# What each sim "model" might say about sess-messy, keyed by assistant line.
_POOL = [
    dict(term="outbox", assistant_line=4, signal="accepted", importance="central"),
    dict(term="dual-write gap", assistant_line=4, signal="accepted", importance="central"),
    dict(term="idempotency key", assistant_line=2, signal="accepted", importance="supporting"),
    dict(term="at-least-once delivery", assistant_line=6, signal="questioned", importance="central"),
]


class Truncated(Exception):
    """Stands in for langchain's LengthFinishReasonError: billed, no answer."""


def sim_proposer(model: str, effort: str, seed: str, meter: Meter):
    rng = random.Random(seed)
    inp, out = SIM_PRICES[model]

    def propose(prompt_text: str) -> list[dict]:
        started = time.perf_counter()
        ptoks = len(prompt_text) // 4
        is_messy = "outbox" in prompt_text
        if model == "sim/steady":
            picks = [_POOL[0], _POOL[2]] if is_messy else []
            fail = False
        else:
            picks = [p for p in _POOL if rng.random() < 0.5] if is_messy else []
            fail = rng.random() < (0.4 if effort == "low" else 0.15)
        ctoks = MAX_TOKENS if fail else 60 + 80 * len(picks)
        time.sleep(rng.uniform(0.001, 0.01))
        meter.record(
            Call(
                purpose="detection",
                model=model,
                ok=not fail,
                cost=ptoks * inp + ctoks * out,
                prompt_tokens=ptoks,
                completion_tokens=ctoks,
                finish_reason="length" if fail else "stop",
                error="Truncated" if fail else None,
                tags={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
        )
        if fail:
            raise Truncated(model)
        return [
            {**p, "paraphrase": f"{p['term']} carried the fix.", "acceptance_line": p["assistant_line"] + 1}
            for p in picks
        ]

    return propose


def live_proposer(model: str, effort: str, meter: Meter):
    """UNVERIFIED. Mirrors unrot.detector.model.build_proposer, plus the two
    knobs ModelConfig does not have yet: reasoning effort and max_tokens."""
    from pydantic import BaseModel

    from unrot.model import ModelConfig
    from unrot.spend import tap

    from langchain_openai import ChatOpenAI

    class P(BaseModel):
        term: str
        paraphrase: str
        assistant_line: int
        acceptance_line: int | None = None
        signal: str
        importance: str

    class Proposal(BaseModel):
        candidates: list[P] = []

    config = ModelConfig.from_env(model=model)
    client = ChatOpenAI(
        model=config.model,
        base_url=config.base_url,
        api_key=config.api_key,
        temperature=0,
        max_tokens=MAX_TOKENS,
        # OpenRouter's unified knob; `reasoning_effort=` is the OpenAI spelling.
        extra_body={"reasoning": {"effort": effort}},
        callbacks=tap(meter, "detection", config),
    ).with_structured_output(Proposal)
    return lambda text: [c.model_dump() for c in client.invoke(text).candidates]


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def cmd_run(args) -> int:
    before = _snapshot(REAL_HOME)
    labels = json.loads((args.set / "labels.json").read_text())
    models = args.models.split(",")
    efforts = args.effort.split(",")

    raw = connect_raw()  # resolves to the temp $UNROT_HOME
    sessions = []
    for sid in labels["sessions"]:
        ingest_file(args.set / f"{sid}.jsonl", conn=raw)
        sessions.append(sid)

    # Estimate before spending: calls are knowable offline (chunk count), prompt
    # tokens roughly (chars/4), completion is bounded by max_tokens -> a ceiling.
    ceiling = 0.0
    calls_planned = 0
    for sid in sessions:
        chunks = chunk_windows(build_windows(raw, sid))
        for chunk in chunks:
            ptoks = len(prompt_module.render(prompt_module.format_windows(chunk))) // 4
            for m in models:
                inp, out = SIM_PRICES.get(m, (None, None))
                if inp is None:
                    continue  # live: would look up OpenRouter pricing here
                ceiling += (ptoks * inp + MAX_TOKENS * out) * len(efforts) * args.repeats
        calls_planned += len(chunks) * len(models) * len(efforts) * args.repeats
    print(f"{len(sessions)} sessions x {len(models)} models x {len(efforts)} efforts x {args.repeats} repeats"
          f" = {calls_planned} detector calls, at most ${ceiling:.4f}")
    if not args.yes and input("Spend it? [y/N] ").strip().lower() != "y":
        return 1

    out = args.out or (HERE / "out" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    meter = Meter()
    runs = []
    for model, effort, rep, sid in itertools.product(models, efforts, range(1, args.repeats + 1), sessions):
        seed = f"{model}|{effort}|{rep}|{sid}"
        propose = (live_proposer(model, effort, meter) if args.live
                   else sim_proposer(model, effort, seed, meter))
        run_id = f"{model}:{effort}:r{rep}:{sid}"
        started = time.perf_counter()
        with meter.about(session_id=sid, run_id=run_id, effort=effort):
            try:
                result = detect(raw, sid, propose=propose, model_label=model.replace("/", "-"))
                row = {"ok": True, "detector_version": result.detector_version,
                       "calls": result.calls_made,
                       "flags": [c.as_dict() for c in result.emitted]}
            except Exception as exc:  # a failed run is a result, not a crash
                row = {"ok": False, "error": type(exc).__name__, "flags": []}
        runs.append({"run_id": run_id, "model": model, "effort": effort, "repeat": rep,
                     "session_id": sid, "max_tokens": MAX_TOKENS,
                     "duration_ms": round((time.perf_counter() - started) * 1000, 2), **row})

    manifest = {
        "kind": "unrot-bench/0", "created_at": datetime.now(timezone.utc).isoformat(),
        "set": args.set.name, "labels_format": labels["format"],
        "prompt_id": prompt_module.prompt_id(), "models": models, "efforts": efforts,
        "repeats": args.repeats, "max_tokens": MAX_TOKENS, "simulated": not args.live,
        "estimate_ceiling_usd": ceiling,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "labels.json").write_text(json.dumps(labels, indent=2))  # the ground truth travels with it
    (out / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs))
    (out / "calls.jsonl").write_text("".join(json.dumps(c.payload()) + "\n" for c in meter.calls))

    after = _snapshot(REAL_HOME)
    assert before == after, "~/.unrot changed during a bench run"
    print(f"wrote {out}  (spent ${meter.total().cost or 0:.4f}; ~/.unrot untouched: "
          f"{'absent before and after' if before is None else 'unchanged'})")
    return 0


# ---------------------------------------------------------------------------
# score -- reads ONLY the output folder
# ---------------------------------------------------------------------------


def _norm(t: str) -> str:
    return " ".join(t.casefold().replace("-", " ").split())


def classify(flag: dict, label: dict) -> str:
    """hit | known (labelled not-a-gap) | unlabelled. Term/alias match only --
    see README 'matching' for why this is the open question."""
    term = _norm(flag["term"])
    for g in label["gaps"]:
        if term in {_norm(x) for x in [g["term"], *g.get("aliases", [])]}:
            return "hit:" + g["term"]
    for g in label["not_gaps"]:
        if term in {_norm(x) for x in [g["term"], *g.get("aliases", [])]}:
            return "known"
    return "unlabelled"


def _n(part, whole):
    if not whole:
        return "  --     (n=0)"
    s = f"{part / whole:5.0%} ({part}/{whole})"
    return s + (" ~" if whole < SMALL else "")


def cmd_score(args) -> int:
    d = args.dir
    labels = json.loads((d / "labels.json").read_text())["sessions"]
    runs = [json.loads(l) for l in (d / "runs.jsonl").read_text().splitlines()]
    calls = [json.loads(l) for l in (d / "calls.jsonl").read_text().splitlines()]

    print("model × effort        precision        recall           flags/sess  stability  fail       p50/max ms   $/correct")
    for (model, effort), group in itertools.groupby(
        sorted(runs, key=lambda r: (r["model"], r["effort"])), key=lambda r: (r["model"], r["effort"])
    ):
        group = list(group)
        ok = [r for r in group if r["ok"]]
        verdicts = [classify(f, labels[r["session_id"]]) for r in ok for f in r["flags"]]
        hits = sum(v.startswith("hit") for v in verdicts)
        found = sum(len({v for v in (classify(f, labels[r["session_id"]]) for f in r["flags"]) if v.startswith("hit")})
                    for r in ok)
        possible = sum(len(labels[r["session_id"]]["gaps"]) for r in ok)
        # Stability: mean pairwise Jaccard of emitted term sets, per session, over ok repeats.
        jac = []
        for sid in labels:
            sets = [{_norm(f["term"]) for f in r["flags"]} for r in ok if r["session_id"] == sid]
            for a, b in itertools.combinations(sets, 2):
                jac.append(1.0 if not (a | b) else len(a & b) / len(a | b))
        mine = [c for c in calls if c["model"] == model and c.get("effort") == effort]
        cost = sum(c.get("cost") or 0 for c in mine)
        durs = sorted(r["duration_ms"] for r in group)
        print(f"{model + ' ' + effort:<20}  {_n(hits, len(verdicts)):<15}  {_n(found, possible):<15}  "
              f"{(len(verdicts) / len(ok)) if ok else 0:>5.2f} (n={len(ok)})  "
              f"{(statistics.mean(jac) if jac else float('nan')):.2f} (p={len(jac)})  "
              f"{_n(len(group) - len(ok), len(group)):<9}  {statistics.median(durs):5.1f}/{durs[-1]:5.1f}  "
              f"{('$%.6f' % (cost / hits)) if hits else 'n/a'}")
    print("~ = fewer than %d in the denominator; don't read much into it" % SMALL)

    sid = args.session or next(s for s, l in labels.items() if l["gaps"])
    print(f"\nside by side: {sid}   labelled gaps: {', '.join(g['term'] for g in labels[sid]['gaps']) or '(none)'}")
    for r in (r for r in runs if r["session_id"] == sid):
        if not r["ok"]:
            cells = f"FAILED ({r['error']})"
        else:
            cells = "  ".join(
                ("✓ " if v.startswith("hit") else "✗ " if v == "known" else "? ") + f["term"]
                for f in r["flags"] for v in [classify(f, labels[sid])]
            ) or "(nothing flagged)"
            missed = {g["term"] for g in labels[sid]["gaps"]} - {
                v[4:] for f in r["flags"] for v in [classify(f, labels[sid])] if v.startswith("hit")}
            cells += "".join(f"  · missed {m}" for m in sorted(missed))
        print(f"  {r['model']:<11} {r['effort']:<6} r{r['repeat']}  {cells}")
    print("  ✓ hit  ✗ flagged a labelled not-gap  ? flagged something the labels don't mention  · missed")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", default="sim/steady,sim/flaky")
    r.add_argument("--effort", default="low,medium")
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--set", type=Path, default=HERE / "labelled")
    r.add_argument("--out", type=Path)
    r.add_argument("--live", action="store_true")
    r.add_argument("--yes", action="store_true")
    s = sub.add_parser("score")
    s.add_argument("dir", type=Path)
    s.add_argument("--session")
    args = p.parse_args(argv)
    return cmd_run(args) if args.cmd == "run" else cmd_score(args)


if __name__ == "__main__":
    sys.exit(main())
