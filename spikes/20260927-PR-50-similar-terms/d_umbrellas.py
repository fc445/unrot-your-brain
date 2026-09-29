"""D, step 2: how often would an umbrella be on offer? Then propose up to 12 parents.

An *umbrella opportunity* (PR-49 decision 3) is a unit -- a decision window
(`group_windows`), a chunk, or a whole session -- holding 3 or more candidates
that are distinct but related. Relatedness is the C transfer test (ling), per
pair: `related`, `a_extends_b` or `b_extends_a` is an edge; `identity` merges
two candidates into one; `unrelated` is no edge. A unit counts when some
connected set of 3+ distinct candidates remains ("connected"); the stricter
"clique" count needs three that are pairwise related.

Candidate sources (see `d_candidates.py`):

* `detector` -- main-pass detector returns. Every pair in a unit is classified.
* `detector+repeats` -- the same plus the 3 detection repeats run1 made on 6
  sessions (session level only; repeats are not tied to the main pass's chunks).
* `wide` -- PR-42 GLiNER + noun_chunks. Up to 255 candidates in one window and
  ~740k within-window pairs, so pairwise classification is out. Instead ling
  (reasoning off by default, `--propose-reasoning`) *proposes* groups of 3+ closely related technical concepts
  from each unit's term list, and the C classifier then *verifies* every pair
  inside each proposed group. Proposal recall is not measured (see README).

Only bare terms are sent to the model. Outputs:
`out/d_results.json` (counts, clusters, per-pair verdicts) and
`out/umbrellas.json` (the <=12 proposals for `umbrellas.html`).

    uv run python d_umbrellas.py --verify-setting ling-off
"""

from __future__ import annotations

import argparse
import itertools
import time
from concurrent.futures import ThreadPoolExecutor

from common import LING, OUT, load_env, ling_transfer, read_json, verdict, write_json, E2E_RESULTS
from unrot.grader.check import question as check_question

PROPOSE_PROMPT = """\
Below are terms that were all mentioned in one stretch of a developer's \
conversation with a coding assistant.

TERMS
{terms}

Find groups of THREE OR MORE terms that are distinct technical concepts and \
closely related to each other: the same narrow topic, so that one parent \
concept could sit above all of them (for example several kinds of code signing, \
or several git operations that rewrite history).

Rules:
- Ignore generic words and phrases (file, user, the change, next step, output), \
names coined inside one project, and people or product names that are not \
concepts to learn.
- Spelling variants, word forms and abbreviations of one concept count as ONE \
term: put only one of them in a group.
- Copy terms exactly as written in the list. Each term in at most one group.
- Each group has 3 to 8 terms: the most closely related ones, not everything \
loosely on the topic.
- Return at most 5 groups, best first. Return an empty list if there are none; \
that is a normal answer.
"""

#: p1 (first pass, kept in out/d_proposals.firstpass.json) had no group-size
#: limit: groups ran to 117 terms (69,825 pairs to verify) and 46 of 278 calls
#: hit the reasoning-off output ceiling while listing. p2 asks for 3-8 terms.
PROPOSE_VERSION = "p2"
MAX_GROUP = 8

PARENT_PROMPT = """\
These terms came up together in one stretch of a developer's work:

{terms}

1. parent: name the ONE concept they are all instances, parts or applications \
of -- the idea a person would need in order to see how these relate. Use an \
established name, not an invented one.
2. check_answer: answer this about the parent, in at most two short lines, as a \
person who understands it would: "{question}"
"""


def propose_schema():
    from pydantic import BaseModel, Field

    class Group(BaseModel):
        terms: list[str] = Field(description="3 or more terms copied exactly from the list")

    class Proposal(BaseModel):
        groups: list[Group] = Field(default_factory=list)

    return Proposal


def parent_schema():
    from pydantic import BaseModel, Field

    class Parent(BaseModel):
        parent: str = Field(description="Established name of the parent concept")
        check_answer: str = Field(description="At most two short lines")

    return Parent


def _bound_requests(timeout: float = 150.0) -> None:
    """Give every ChatOpenAI a request timeout (spike-only patch).

    `unrot.model.structured_client` builds `ChatOpenAI` without one, and the
    first full run here hung for 25+ minutes on a single proposal call. The
    client imports `ChatOpenAI` from `langchain_openai` at call time, so
    swapping the module attribute for a partial is enough; nothing in `unrot`
    is edited.
    """
    import functools

    import langchain_openai

    if not isinstance(langchain_openai.ChatOpenAI, functools.partial):
        langchain_openai.ChatOpenAI = functools.partial(
            langchain_openai.ChatOpenAI, timeout=timeout, max_retries=1
        )


def structured(schema, reasoning: str):
    from unrot.model import ModelConfig, structured_client
    from unrot.spend import Meter

    meter = Meter()
    config = ModelConfig.from_env(model=LING, reasoning_effort="low")
    client = structured_client(config, schema, meter=meter, purpose="pr50-umbrella")

    def invoke(prompt: str):
        from common import CALL_DEADLINE, with_deadline

        run = (lambda: client.forced.invoke(prompt)) if reasoning == "off" else (lambda: client.invoke(prompt))
        return with_deadline(run, CALL_DEADLINE)

    return invoke, meter


def cost_of(meter) -> float:
    return sum(c.cost or 0.0 for c in meter.calls)


# ---------------------------------------------------------------------------


class PairCache:
    """Pair verdicts, keyed order-free, reused across units and runs."""

    def __init__(self, path, setting: str):
        self.path = path
        self.setting = setting
        self.data = read_json(path) if path.exists() else {}
        self.spent = 0.0
        self.calls = 0

    @staticmethod
    def key(a: str, b: str) -> str:
        x, y = sorted((a.casefold(), b.casefold()))
        return f"{x}␟{y}"

    def get(self, a: str, b: str):
        """A cached row, or None. Rows whose call failed count as missing, so they are retried."""
        row = self.data.get(f"{self.setting}␞{self.key(a, b)}")
        return row if row and row.get("verdict") else None

    def fill(self, pairs: list[tuple[str, str]], workers: int) -> None:
        todo = sorted({tuple(sorted((a, b), key=str.casefold)) for a, b in pairs if self.get(a, b) is None})
        if not todo:
            return

        def work(p):
            a, b = p
            res = ling_transfer(a, b, reasoning="off" if self.setting == "ling-off" else "low")
            v = None
            if res.answer:
                v = verdict(res.answer["a_explains_b"], res.answer["b_explains_a"], res.answer["same_space"])
            return p, {"a": a, "b": b, "verdict": v, "answer": res.answer, "cost": res.cost,
                       "seconds": round(res.seconds, 2), "error": res.error}

        from concurrent.futures import as_completed

        from common import write_json as _w

        print(f"  verifying {len(todo)} pairs", flush=True)
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, p) for p in todo]
            for i, fut in enumerate(as_completed(futures), 1):
                (a, b), row = fut.result()
                self.data[f"{self.setting}␞{self.key(a, b)}"] = row
                self.spent += row["cost"] or 0.0
                self.calls += 1
                if i % 100 == 0 or i == len(todo):
                    _w(self.path, self.data)  # resumable: failed/unanswered pairs have verdict None
                    print(f"  verified {i}/{len(todo)}, {time.monotonic() - t0:.0f}s", flush=True)
        _w(self.path, self.data)


def verify(terms: list[str], cache: PairCache) -> dict:
    """Collapse identities, then find related structure among distinct concepts."""
    parent = {t: t for t in terms}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges = []
    for a, b in itertools.combinations(terms, 2):
        row = cache.get(a, b) or {}
        v = row.get("verdict")
        if v == "identity":
            parent[find(a)] = find(b)
        elif v in ("related", "a_extends_b", "b_extends_a"):
            edges.append((a, b))
    nodes = sorted({find(t) for t in terms})
    adj = {n: set() for n in nodes}
    for a, b in edges:
        x, y = find(a), find(b)
        if x != y:
            adj[x].add(y)
            adj[y].add(x)
    # Connected components of distinct concepts.
    seen, comps = set(), []
    for n in nodes:
        if n in seen:
            continue
        stack, comp = [n], []
        while stack:
            m = stack.pop()
            if m in seen:
                continue
            seen.add(m)
            comp.append(m)
            stack.extend(adj[m] - seen)
        comps.append(sorted(comp))
    big = [c for c in comps if len(c) >= 3]
    triangle = any(
        y in adj[x] and z in adj[x] and z in adj[y]
        for x in nodes for y, z in itertools.combinations(sorted(adj[x]), 2)
    )
    return {
        "distinct_concepts": len(nodes),
        "identity_merges": len(terms) - len(nodes),
        "components_3plus": big,
        "connected": bool(big),
        "clique": triangle,
        "members": {n: sorted(t for t in terms if find(t) == n) for n in nodes},
    }


# ---------------------------------------------------------------------------


def detector_units(cands: dict, results: dict) -> list[dict]:
    units = []
    for sid, row in cands.items():
        for unit in ("group", "chunk"):
            for k, terms in row["detector"][unit].items():
                if len(terms) >= 3:
                    units.append({"source": "detector", "unit": unit, "session": sid, "index": k, "terms": terms})
        if len(row["detector"]["session"]) >= 3:
            units.append({"source": "detector", "unit": "session", "session": sid, "index": "all",
                          "terms": row["detector"]["session"]})
    # + repeats, session level
    extra: dict[str, set] = {}
    for call in results["calls"]:
        if call["stage"] == "detection-repeat" and isinstance(call.get("returned"), list):
            extra.setdefault(call["session_id"], set()).update(
                t["term"] for t in call["returned"] if isinstance(t, dict) and t.get("term"))
    for sid, row in cands.items():
        terms = sorted(set(row["detector"]["session"]) | extra.get(sid, set()), key=str.casefold)
        # case-insensitive dedupe
        uniq = list({t.casefold(): t for t in terms}.values())
        if len(uniq) >= 3:
            units.append({"source": "detector+repeats", "unit": "session", "session": sid, "index": "all",
                          "terms": uniq})
    return units


def propose(units: list[dict], workers: int, reasoning: str = "off") -> float:
    Proposal = propose_schema()

    def work(u):
        """Try `reasoning` first; on any failure retry once with reasoning low.

        Measured on the first full pass (reasoning off, 278 units): 46 calls hit
        the 4,000-token ceiling of the reasoning-off client -- the model looped
        while writing the answer, ~83 s each -- and 13 died with a connection
        error. A unit that failed before goes straight to the fallback.
        """
        listing = "\n".join(f"- {t}" for t in u["terms"])
        prompt = PROPOSE_PROMPT.format(terms=listing)
        started = time.monotonic()
        order = ["low"] if u.get("_failed_before") else [reasoning, "low"]
        groups, err, attempts, meters = [], None, [], []
        for setting in dict.fromkeys(order):
            invoke, meter = structured(Proposal, setting)
            meters.append(meter)
            try:
                out = invoke(prompt)
                groups, err = [g.terms for g in out.groups], None
                attempts.append({"reasoning": setting, "error": None})
                break
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"[:200]
                attempts.append({"reasoning": setting, "error": err})
        u["_attempts"] = attempts
        meter = type("M", (), {"calls": [c for m in meters for c in m.calls]})()
        by_fold = {t.casefold(): t for t in u["terms"]}
        valid = []
        for g in groups:
            kept = []
            for t in g:
                real = by_fold.get(t.strip().casefold())
                if real and real not in kept:
                    kept.append(real)
            if len(kept) >= 3:
                # The prompt asks for 3-8; anything longer is cut to the first
                # MAX_GROUP so verification stays bounded (it is pairwise).
                valid.append(kept[:MAX_GROUP])
        return u, valid, len(groups), cost_of(meter), time.monotonic() - started, err

    # Cached per unit so an interrupted run resumes; errors are retried next run.
    path = OUT / "d_proposals.json"
    cached = read_json(path) if path.exists() else {}

    def ukey(u):
        return f"{u['source']}|{u['unit']}|{u['session']}|{u['index']}|{reasoning}|{PROPOSE_VERSION}"

    def apply(u, row):
        u["proposed"], u["proposed_raw_count"] = row["proposed"], row["raw_n"]
        u["propose_cost"], u["propose_seconds"], u["propose_error"] = row["cost"], row["seconds"], row["error"]
        u["propose_attempts"] = row.get("attempts") or [{"reasoning": reasoning, "error": row["error"]}]

    todo = []
    for u in units:
        row = cached.get(ukey(u))
        if row and not row["error"]:
            apply(u, row)
        else:
            if row:
                u["_failed_before"] = True
                u["_earlier_attempts"] = row.get("attempts") or [{"reasoning": reasoning, "error": row["error"]}]
                u["_earlier_cost"] = row["cost"]
            todo.append(u)

    from concurrent.futures import as_completed

    spent, t0 = 0.0, time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, u) for u in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            u, valid, raw_n, cost, secs, err = fut.result()
            attempts = u.pop("_earlier_attempts", []) + u.pop("_attempts", [])
            cost += u.pop("_earlier_cost", 0.0) or 0.0
            u.pop("_failed_before", None)
            row = {"proposed": valid, "raw_n": raw_n, "cost": cost, "seconds": round(secs, 1), "error": err,
                   "attempts": attempts}
            apply(u, row)
            cached[ukey(u)] = row
            spent += cost
            if i % 10 == 0 or i == len(todo):
                write_json(path, cached)
                print(f"  proposed {i}/{len(todo)} (+{len(units) - len(todo)} cached), "
                      f"{time.monotonic() - t0:.0f}s, errors so far "
                      f"{sum(1 for x in todo if x.get('propose_error'))}", flush=True)
    return spent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-setting", default="ling-off", choices=("ling-off", "ling-low"))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--max-umbrellas", type=int, default=12)
    # "low" was tried first and measured at ~5 min per 25 units at 12 in flight
    # (reasoning over 100-700-term lists, ~55 min for all 278), so it was stopped.
    ap.add_argument("--propose-reasoning", default="off", choices=("off", "low"))
    args = ap.parse_args()
    load_env()
    _bound_requests()
    started = time.monotonic()

    cands = read_json(OUT / "d_candidates.json")
    results = read_json(E2E_RESULTS)
    cache = PairCache(OUT / "d_pair_cache.json", args.verify_setting)

    # 1. Detector units: every pair.
    det_units = detector_units(cands, results)
    cache.fill([p for u in det_units for p in itertools.combinations(u["terms"], 2)], args.workers)
    for u in det_units:
        u["verified"] = verify(u["terms"], cache)

    # 2. Wide units: propose, then verify inside proposals.
    # Decision windows only. Chunks (up to 702 terms) are not proposed over
    # separately: a chunk is counted as having an opportunity when one of its
    # windows does, which is a lower bound (cross-window clusters are missed).
    wide_units = []
    for sid, row in cands.items():
        for unit in ("group",):
            for k, terms in row["wide"][unit].items():
                if len(terms) >= 3:
                    wide_units.append({"source": "wide", "unit": unit, "session": sid, "index": k, "terms": terms})
    print(f"proposing over {len(wide_units)} wide units", flush=True)
    propose_cost = propose(wide_units, args.workers, args.propose_reasoning)
    cache.fill([p for u in wide_units for g in u["proposed"] for p in itertools.combinations(g, 2)], args.workers)
    for u in wide_units:
        u["clusters"] = [verify(g, cache) for g in u["proposed"]]
        u["connected"] = any(c["connected"] for c in u["clusters"])
        u["clique"] = any(c["clique"] for c in u["clusters"])

    # 3. Rates.
    sessions = list(cands)
    rates = {}
    for source, units in (("detector", [u for u in det_units if u["source"] == "detector"]),
                          ("detector+repeats", [u for u in det_units if u["source"] == "detector+repeats"])):
        for unit in ("group", "chunk", "session"):
            denom = (len(sessions) if unit == "session"
                     else sum(cands[s]["groups" if unit == "group" else "chunks"] for s in sessions))
            mine = [u for u in units if u["unit"] == unit]
            rates[f"{source}/{unit}"] = {
                "units_total": denom,
                "units_with_3plus_candidates": len(mine),
                "connected": sum(u["verified"]["connected"] for u in mine),
                "clique": sum(u["verified"]["clique"] for u in mine),
                "sessions_with_connected": len({u["session"] for u in mine if u["verified"]["connected"]}),
            }
    # Wide chunks, derived: a chunk has an opportunity if any of its windows does.
    from d_candidates import load_sessions

    windows_by_session = load_sessions()
    hit_groups = {(u["session"], int(u["index"])) for u in wide_units if u["connected"]}
    chunk_hits = chunk_total = 0
    for sid, (_, groups, chunks) in windows_by_session.items():
        first_line = {g[0].assistant_line: i for i, g in enumerate(groups)}
        for chunk in chunks:
            chunk_total += 1
            idx = {first_line[w.assistant_line] for w in chunk if w.assistant_line in first_line}
            chunk_hits += any((sid, i) in hit_groups for i in idx)
    rates["wide/chunk (derived from windows, lower bound)"] = {
        "units_total": chunk_total, "connected": chunk_hits, "sessions_total": len(sessions),
    }
    for unit in ("group",):
        denom = sum(cands[s]["groups" if unit == "group" else "chunks"] for s in sessions)
        mine = [u for u in wide_units if u["unit"] == unit]
        rates[f"wide/{unit}"] = {
            "units_total": denom,
            "units_with_3plus_candidates": len(mine),
            "units_with_a_proposal": sum(1 for u in mine if u["proposed"]),
            "units_propose_failed": sum(1 for u in mine if u.get("propose_error")),
            "units_needed_fallback": sum(1 for u in mine if len(u.get("propose_attempts") or []) > 1
                                         or (u.get("propose_attempts") or [{}])[0].get("reasoning") == "low"),
            "connected": sum(u["connected"] for u in mine),
            "clique": sum(u["clique"] for u in mine),
            "sessions_with_connected": len({u["session"] for u in mine if u["connected"]}),
        }
    # Sessions with an opportunity in at least one decision window (wide).
    for key in list(rates):
        rates[key]["sessions_total"] = len(sessions)

    # 4. Umbrella proposals: up to N verified clusters, one per session first, detector sources first.
    pool_clusters = []
    for u in det_units:
        for comp in u["verified"]["components_3plus"]:
            kids = sorted({m for n in comp for m in u["verified"]["members"][n][:1]}, key=str.casefold)
            pool_clusters.append((0 if u["source"] == "detector" else 1, u, kids))
    for u in wide_units:
        for c in u["clusters"]:
            for comp in c["components_3plus"]:
                kids = sorted({c["members"][n][0] for n in comp}, key=str.casefold)
                pool_clusters.append((2 if u["unit"] == "group" else 3, u, kids))
    pool_clusters.sort(key=lambda x: (x[0], x[1]["session"], -len(x[2])))
    chosen, seen_sessions, seen_sets = [], set(), set()
    for pass_ in (0, 1):
        for prio, u, kids in pool_clusters:
            if len(chosen) >= args.max_umbrellas:
                break
            key = frozenset(k.casefold() for k in kids)
            if key in seen_sets or (pass_ == 0 and u["session"] in seen_sessions):
                continue
            seen_sets.add(key)
            seen_sessions.add(u["session"])
            chosen.append((u, kids))

    Parent = parent_schema()
    q = check_question("{X}")

    def name(item):
        u, kids = item
        invoke, meter = structured(Parent, "low")
        try:
            out = invoke(PARENT_PROMPT.format(terms="\n".join(f"- {k}" for k in kids),
                                              question=q.replace("{X}", "the parent")))
            return u, kids, out.parent.strip(), out.check_answer.strip(), cost_of(meter), None
        except Exception as exc:  # noqa: BLE001
            return u, kids, None, None, cost_of(meter), f"{type(exc).__name__}: {exc}"[:200]

    print(f"naming {len(chosen)} parents from {len(pool_clusters)} verified clusters", flush=True)
    proposals, parent_cost = [], 0.0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (u, kids, parent, answer, cost, err) in enumerate(pool.map(name, chosen), 1):
            parent_cost += cost
            if not parent:
                continue
            proposals.append({
                "id": f"u{i:02d}", "parent": parent, "children": kids,
                "question": check_question(parent), "check_answer": answer,
                "source": u["source"], "unit": u["unit"], "session": u["session"][:8],
            })

    wall = time.monotonic() - started
    write_json(OUT / "d_results.json", {
        "verify_setting": args.verify_setting,
        "propose_reasoning": args.propose_reasoning,
        "rates": rates,
        "detector_units": det_units,
        "wide_units": wide_units,
        "cost": {"verify_pairs": round(cache.spent, 5), "verify_calls": cache.calls,
                 "propose": round(propose_cost, 5), "parents": round(parent_cost, 5)},
        "wall_seconds": round(wall, 1),
    })
    write_json(OUT / "umbrellas.json", {"proposals": proposals})
    import json

    print(json.dumps(rates, indent=1))
    print("cost:", round(cache.spent + propose_cost + parent_cost, 4), "calls verify:", cache.calls,
          "wall:", round(wall))
    print("umbrella proposals:", len(proposals))


if __name__ == "__main__":
    main()
