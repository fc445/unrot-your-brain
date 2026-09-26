"""PR-34 spike: can we predict whether a person knows a new concept from the ones
we already know they know (or don't)?

    uv run run.py                      # everything except the chat LLM
    uv run run.py --llm --llm-seeds 1  # add the chat-LLM comparison on one seed
    uv run run.py --no-jev             # offline apart from the embedding download

Writes results.md, results.json and map.png next to this file. Jev and LLM
answers are cached in .cache/, so a re-run only pays for what changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import threading
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from sklearn.cluster import AgglomerativeClustering
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import adjusted_rand_score, roc_auc_score
from wordfreq import zipf_frequency

from data import AREAS, CONCEPTS, PERSONAS, Concept, knows

HERE = Path(__file__).parent
CACHE = HERE / ".cache"
CACHE.mkdir(exist_ok=True)
load_dotenv(HERE.parents[1] / ".env")

BASE_URL = os.environ.get("UNROT_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
JEV_MODEL = "typesafe/jev-1.13"

K = 7
TAU = 0.05  # softmax temperature over cosine similarity in the kNN vote

N = len(CONCEPTS)
Y_BY_PERSONA = {p: np.array([knows(p, c) for c in CONCEPTS], dtype=int) for p in PERSONAS}
MAX_DEPTH = {a: max(c.depth for c in CONCEPTS if c.area == a) for a in AREAS}


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def embed(model: str, texts: list[str], tag: str) -> np.ndarray:
    path = CACHE / f"emb-{model.replace('/', '_')}-{tag}.npy"
    if path.exists():
        return np.load(path)
    from fastembed import TextEmbedding

    vecs = np.array(list(TextEmbedding(model).embed(texts)))
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    np.save(path, vecs)
    return vecs


def knn_scores(sims: np.ndarray, map_idx: np.ndarray, y: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """Similarity-weighted vote of the K nearest labelled concepts, per target."""
    out = []
    for t in targets:
        cand = map_idx[map_idx != t]
        s = sims[t, cand]
        top = np.argsort(-s)[:K]
        w = np.exp((s[top] - s[top].max()) / TAU)
        out.append(float((w * y[cand[top]]).sum() / w.sum()))
    return np.array(out)


# ---------------------------------------------------------------------------
# Remote calls (Jev and a chat LLM), cached on disk
# ---------------------------------------------------------------------------


class Cache:
    def __init__(self, name: str):
        self.path = CACHE / f"{name}.jsonl"
        self.lock = threading.Lock()
        self.data: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                row = json.loads(line)
                self.data[row["key"]] = row

    def get(self, key):
        return self.data.get(key)

    def put(self, key, row):
        row = {"key": key, **row}
        with self.lock:
            self.data[key] = row
            with self.path.open("a") as f:
                f.write(json.dumps(row) + "\n")


def post(url: str, body: dict, timeout: int = 60) -> tuple[dict, float]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    for attempt in range(4):
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r), time.perf_counter() - t0
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def listing(concepts: list[Concept]) -> str:
    return "\n".join(f"- {c.text}" for c in concepts) or "- (none)"


def map_state(map_idx, y, target: Concept | None) -> str:
    parts = []
    if map_idx is not None:
        known = [CONCEPTS[i] for i in map_idx if y[i]]
        unknown = [CONCEPTS[i] for i in map_idx if not y[i]]
        parts += [
            "Concepts this person KNOWS:", listing(known), "",
            "Concepts this person does NOT know:", listing(unknown), "",
        ]
    parts.append(f"NEW CONCEPT: {target.text}")
    return "\n".join(parts)


JEV_QUESTION = {
    "type": "choice",
    "instructions": "Would this person already understand the NEW CONCEPT, judging from what they are known to know and not know?",
    "criteria": {
        "knows": "They would already understand the new concept, given what they know.",
        "does_not_know": (
            "They would not yet understand the new concept. Knowing a related or"
            " neighbouring concept is not the same as knowing this one."
        ),
    },
}

#: The same question without the steer about neighbouring concepts -- to check
#: whether that sentence is what makes Jev reluctant to say "knows".
JEV_NEUTRAL_QUESTION = {
    "type": "choice",
    "instructions": JEV_QUESTION["instructions"],
    "criteria": {
        "knows": "They would already understand the new concept.",
        "does_not_know": "They would not yet understand the new concept.",
    },
}

JEV_PRIOR_QUESTION = {
    "type": "choice",
    "instructions": "Would a typical working software developer already understand the NEW CONCEPT?",
    "criteria": {
        "knows": "A typical software developer would already understand it.",
        "does_not_know": "A typical software developer would not yet understand it.",
    },
}


def jev(state: str, question: dict, cache: Cache) -> dict:
    body = {"model": JEV_MODEL, "state": state, "questions": {"familiar": question}}
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if hit := cache.get(key):
        return hit
    payload, secs = post(BASE_URL.removesuffix("/v1") + "/v1/systemone", body)
    got = payload["answers"]["familiar"]
    row = {
        "p_knows": float(got["probabilities"].get("knows") or 0.0),
        "confidence": float(got.get("confidence") or 0.0),
        "secs": secs,
        "cost": float((payload.get("usage") or {}).get("cost") or 0.0),
        "input_tokens": (payload.get("usage") or {}).get("input_tokens"),
    }
    cache.put(key, row)
    return row


LLM_SYSTEM = (
    "You judge whether a specific person already understands a concept, from lists of"
    " concepts they are known to know and not know. Knowing a related or neighbouring"
    " concept is not the same as knowing this one. Reply with JSON only:"
    ' {"p_knows": <probability from 0 to 1 that they already understand it>}'
)


def llm(state: str, model: str, cache: Cache, reasoning: str | None) -> dict:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": LLM_SYSTEM}, {"role": "user", "content": state}],
    }
    if reasoning:
        body["reasoning"] = {"effort": reasoning}
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if hit := cache.get(key):
        return hit
    payload, secs = post(BASE_URL + "/chat/completions", body, timeout=120)
    text = payload["choices"][0]["message"].get("content") or ""
    try:
        p = float(json.loads(text[text.index("{") : text.rindex("}") + 1])["p_knows"])
    except Exception:
        p = 0.5  # unparseable: count it as a shrug rather than dropping it
    usage = payload.get("usage") or {}
    row = {"p_knows": p, "secs": secs, "cost": float(usage.get("cost") or 0.0),
           "completion_tokens": usage.get("completion_tokens"), "raw": text[:200]}
    cache.put(key, row)
    return row


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def bucket(persona: str, c: Concept) -> str:
    level = PERSONAS[persona].get(c.area, 0)
    if level == 0:
        return "cold area (knows none of it)"
    if level >= MAX_DEPTH[c.area]:
        return "fully known area"
    if c.depth <= level:
        return "boundary area: known (at/below)"
    if c.depth == level + 1:
        return "boundary area: one step too deep"
    return "boundary area: 2+ steps too deep"


BUCKETS = [
    "cold area (knows none of it)",
    "fully known area",
    "boundary area: known (at/below)",
    "boundary area: one step too deep",
    "boundary area: 2+ steps too deep",
]


def auc(y, s):
    return float(roc_auc_score(y, s)) if len(set(y)) == 2 else float("nan")


def best_threshold(y, s) -> float:
    """The cut on a revealed map that gets most of it right -- for scores that
    are not probabilities (the Zipf prior)."""
    cands = np.unique(s)
    accs = [((s >= t).astype(int) == y).mean() for t in cands]
    return float(cands[int(np.argmax(accs))])


def fmt(xs):
    xs = [x for x in xs if not math.isnan(x)]
    if not xs:
        return "–"
    if len(xs) == 1:
        return f"{xs[0]:.2f}"
    return f"{statistics.mean(xs):.2f} ± {statistics.stdev(xs):.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--reveal", type=float, default=0.4, help="fraction of each persona's concepts shown as their map")
    ap.add_argument("--embed-models", default="BAAI/bge-base-en-v1.5,sentence-transformers/all-MiniLM-L6-v2")
    ap.add_argument("--no-jev", action="store_true")
    ap.add_argument("--llm", action="store_true", help="also run a chat LLM on the same prompt")
    ap.add_argument("--llm-model", default=os.environ.get("UNROT_MODEL", "inclusionai/ling-3.0-flash"))
    ap.add_argument("--llm-reasoning", default="low", help="OpenRouter reasoning effort; '' sends none")
    ap.add_argument("--llm-seeds", type=int, default=1)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    use_jev = not args.no_jev and bool(API_KEY)
    if not args.no_jev and not API_KEY:
        print("No OPENROUTER_API_KEY: skipping Jev and the LLM.")
    jev_cache, jevp_cache, llm_cache = Cache("jev"), Cache("jev_prior"), Cache("llm")

    # --- features -----------------------------------------------------------
    models = [m for m in args.embed_models.split(",") if m]
    t0 = time.perf_counter()
    sims = {}
    for m in models:
        short = m.split("/")[-1]
        for tag, texts in (("term", [c.term for c in CONCEPTS]), ("gloss", [c.text for c in CONCEPTS])):
            v = embed(m, texts, tag)
            sims[f"knn_{tag}[{short}]"] = v @ v.T
    embed_secs = time.perf_counter() - t0
    zipf = np.array([zipf_frequency(c.term, "en") for c in CONCEPTS])
    main_knn = f"knn_gloss[{models[0].split('/')[-1]}]"

    jev_prior = {}
    if use_jev:
        with ThreadPoolExecutor(args.workers) as ex:
            rows = list(ex.map(lambda c: jev(map_state(None, None, c), JEV_PRIOR_QUESTION, jevp_cache), CONCEPTS))
        jev_prior = {i: r for i, r in enumerate(rows)}

    # --- the split ------------------------------------------------------------
    # records[method] -> list of (seed, persona, idx, score, pred)
    records: dict[str, list] = defaultdict(list)
    calls: dict[str, list] = defaultdict(list)
    n_map = int(round(args.reveal * N))

    for seed in range(args.seeds):
        for persona in PERSONAS:
            y = Y_BY_PERSONA[persona]
            order = np.random.RandomState(seed * 101 + list(PERSONAS).index(persona)).permutation(N)
            map_idx, test_idx = np.sort(order[:n_map]), np.sort(order[n_map:])

            def add(method, scores, cut):
                for i, s in zip(test_idx, scores):
                    records[method].append((seed, persona, int(i), float(s), int(s >= cut)))

            # Commonness prior: no personalisation at all.
            add("prior_zipf", zipf[test_idx], best_threshold(y[map_idx], zipf[map_idx]))

            for name, S in sims.items():
                add(name, knn_scores(S, map_idx, y, test_idx), 0.5)

            # kNN + prior: a two-feature logistic regression fitted on the map itself,
            # with the kNN score for map items computed leave-one-out.
            S = sims[main_knn]
            Xm = np.c_[knn_scores(S, map_idx, y, map_idx), zipf[map_idx]]
            Xt = np.c_[knn_scores(S, map_idx, y, test_idx), zipf[test_idx]]
            if len(set(y[map_idx])) == 2:
                lr = LogisticRegression().fit(Xm, y[map_idx])
                add("knn+prior", lr.predict_proba(Xt)[:, 1], 0.5)

            if use_jev:
                add("jev_prior (no map)", [jev_prior[i]["p_knows"] for i in test_idx], 0.5)
                with ThreadPoolExecutor(args.workers) as ex:
                    rows = list(ex.map(
                        lambda i: jev(map_state(map_idx, y, CONCEPTS[i]), JEV_QUESTION, jev_cache), test_idx))
                add("jev", [r["p_knows"] for r in rows], 0.5)
                calls["jev"] += rows
                # Jev ranks on its own scale; learn where to cut it from this
                # person's map (each map item judged against the rest of the map),
                # as a threshold learned from their dismissals would in the product.
                with ThreadPoolExecutor(args.workers) as ex:
                    loo = list(ex.map(
                        lambda i: jev(map_state(map_idx[map_idx != i], y, CONCEPTS[i]), JEV_QUESTION, jev_cache),
                        map_idx))
                cut = best_threshold(y[map_idx], np.array([r["p_knows"] for r in loo]))
                add("jev, cut learned on map", [r["p_knows"] for r in rows], cut)
                with ThreadPoolExecutor(args.workers) as ex:
                    rows = list(ex.map(
                        lambda i: jev(map_state(map_idx, y, CONCEPTS[i]), JEV_NEUTRAL_QUESTION, jev_cache), test_idx))
                add("jev, neutral wording", [r["p_knows"] for r in rows], 0.5)

            if use_jev and args.llm and seed < args.llm_seeds:
                with ThreadPoolExecutor(args.workers) as ex:
                    rows = list(ex.map(
                        lambda i: llm(map_state(map_idx, y, CONCEPTS[i]), args.llm_model, llm_cache,
                                      args.llm_reasoning or None), test_idx))
                add(f"llm[{args.llm_model.split('/')[-1]}]", [r["p_knows"] for r in rows], 0.5)
                calls["llm"] += rows
        print(f"seed {seed} done")

    # --- metrics --------------------------------------------------------------
    results = {"n_concepts": N, "n_map": n_map, "seeds": args.seeds, "methods": {}}
    for method, rows in records.items():
        by_seed = defaultdict(list)
        for r in rows:
            by_seed[r[0]].append(r)
        overall, boundary, acc = [], [], []
        bucket_acc = defaultdict(list)
        for seed, rs in by_seed.items():
            yy = [Y_BY_PERSONA[p][i] for _, p, i, _, _ in rs]
            ss = [s for *_, s, _ in rs]
            overall.append(auc(yy, ss))
            acc.append(float(np.mean([pred == t for (_, _, _, _, pred), t in zip(rs, yy)])))
            bnd = [(t, s) for (_, p, i, s, _), t in zip(rs, yy) if bucket(p, CONCEPTS[i]).startswith("boundary")]
            boundary.append(auc([t for t, _ in bnd], [s for _, s in bnd]))
            per_b = defaultdict(list)
            for (_, p, i, _, pred), t in zip(rs, yy):
                per_b[bucket(p, CONCEPTS[i])].append(pred == t)
            for b, v in per_b.items():
                bucket_acc[b].append(float(np.mean(v)))
        per_persona = {}
        for persona in PERSONAS:
            rs = [r for r in rows if r[1] == persona]
            per_persona[persona] = auc([Y_BY_PERSONA[persona][i] for _, _, i, _, _ in rs], [r[3] for r in rs])
        results["methods"][method] = {
            "auc": overall, "auc_boundary": boundary, "accuracy": acc,
            "bucket_accuracy": dict(bucket_acc), "auc_by_persona": per_persona,
        }

    for name, rows in calls.items():
        results[f"{name}_calls"] = {
            "n": len(rows),
            "median_secs": statistics.median(r["secs"] for r in rows),
            "mean_cost": statistics.mean(r["cost"] for r in rows),
        }
    results["embed_secs_all_models"] = embed_secs

    # --- probes: leave-one-out with the whole rest of the map revealed --------
    probes = [("backend", "MVCC"), ("backend", "Postgres"), ("backend", "write skew"),
              ("backend", "Maillard reaction"), ("frontend", "hydration"), ("frontend", "Kubernetes"),
              ("ml_engineer", "KV cache"), ("ml_engineer", "React Fiber"), ("junior", "closure"),
              ("hobby_cook", "sourdough starter"), ("hobby_cook", "git")]
    idx_of = {c.term: i for i, c in enumerate(CONCEPTS)}
    probe_rows = []
    # Two maps per probe. "loo": everything else revealed, including the target's
    # same-depth siblings -- the easy case, since those siblings carry the answer.
    # "shallow": every concept in the target's area at its depth or deeper is
    # hidden too, so the map only shows what sits above it. That is the honest
    # Postgres -> MVCC question: nothing this deep in the area has been seen yet.
    for mode in ("loo", "shallow"):
        for persona, term in probes:
            t = idx_of[term]
            c = CONCEPTS[t]
            y = Y_BY_PERSONA[persona]
            if mode == "loo":
                map_idx = np.array([i for i in range(N) if i != t])
            else:
                map_idx = np.array([i for i, o in enumerate(CONCEPTS)
                                    if not (o.area == c.area and o.depth >= c.depth)])
            row = {"map": mode, "persona": persona, "term": term, "truth": "knows" if y[t] else "doesn't"}
            for name, S in sims.items():
                row[name] = float(knn_scores(S, map_idx, y, np.array([t]))[0])
            if use_jev:
                row["jev"] = jev(map_state(map_idx, y, c), JEV_QUESTION, jev_cache)["p_knows"]
            probe_rows.append(row)
    results["probes"] = probe_rows

    # --- does the map cluster into areas and domains? -------------------------
    clustering = {}
    for m in models:
        short = m.split("/")[-1]
        for tag, texts in (("term", [c.term for c in CONCEPTS]), ("gloss", [c.text for c in CONCEPTS])):
            v = embed(m, texts, tag)
            areas = [c.area for c in CONCEPTS]
            domains = ["cooking" if a == "cooking" else "software" for a in areas]
            lab_a = AgglomerativeClustering(n_clusters=len(AREAS), metric="cosine", linkage="average").fit_predict(v)
            lab_d = AgglomerativeClustering(n_clusters=2, metric="cosine", linkage="average").fit_predict(v)
            # Share of cooking concepts that land in a cluster that is mostly cooking.
            cook = [i for i, a in enumerate(areas) if a == "cooking"]
            cook_clusters = {l for l in lab_a[cook]}
            purity = np.mean([
                sum(areas[j] == "cooking" for j in range(N) if lab_a[j] == lab_a[i])
                / sum(lab_a[j] == lab_a[i] for j in range(N))
                for i in cook
            ])
            clustering[f"{tag}[{short}]"] = {
                "ARI vs 11 areas": float(adjusted_rand_score(areas, lab_a)),
                "ARI vs 2 domains": float(adjusted_rand_score(domains, lab_d)),
                "cooking cluster purity": float(purity),
                "clusters holding cooking": len(cook_clusters),
            }
    results["clustering"] = clustering

    (HERE / "results.json").write_text(json.dumps(results, indent=1))
    write_markdown(results, main_knn)
    plot(models[0])
    print((HERE / "results.md").read_text())


def write_markdown(results, main_knn):
    L = [f"# Results\n\n{results['n_concepts']} concepts, {results['n_map']} revealed per persona,"
         f" {results['seeds']} seeds. Mean ± sd over seeds; each seed pools all 5 personas.\n"]
    L.append("## Headline\n\n| Method | AUC (all) | AUC (boundary areas only) | Accuracy |\n|---|---|---|---|")
    for m, r in results["methods"].items():
        L.append(f"| {m} | {fmt(r['auc'])} | {fmt(r['auc_boundary'])} | {fmt(r['accuracy'])} |")
    L.append("\n*Boundary areas* are the ones where the persona knows some but not all of the area, so"
             " area alone cannot answer and depth has to.\n")
    L.append("## Accuracy by case\n\n| Method | " + " | ".join(BUCKETS) + " |\n|" + "---|" * (len(BUCKETS) + 1))
    for m, r in results["methods"].items():
        L.append(f"| {m} | " + " | ".join(fmt(r["bucket_accuracy"].get(b, [])) for b in BUCKETS) + " |")
    L.append("\n## AUC by persona (pooled over seeds)\n\n| Method | " + " | ".join(PERSONAS) + " |\n|" + "---|" * (len(PERSONAS) + 1))
    for m, r in results["methods"].items():
        L.append(f"| {m} | " + " | ".join(fmt([r["auc_by_persona"][p]]) for p in PERSONAS) + " |")
    L.append("\n## Probes: P(knows) for hand-picked cases\n\n*loo*: everything else revealed."
             " *shallow*: also hides every concept in the same area at the target's depth or deeper,"
             " so the map only shows what sits above it.\n")
    cols = [k for k in results["probes"][0] if k not in ("map", "persona", "term", "truth")]
    L.append("| Map | Persona | Concept | Truth | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 4))
    for p in results["probes"]:
        L.append(f"| {p['map']} | {p['persona']} | {p['term']} | {p['truth']} | " + " | ".join(f"{p[c]:.2f}" for c in cols) + " |")
    L.append("\n## Cost and latency\n")
    for k in ("jev_calls", "llm_calls"):
        if k in results:
            c = results[k]
            L.append(f"- **{k.removesuffix('_calls')}**: {c['n']} calls, median {c['median_secs']:.2f}s,"
                     f" mean ${c['mean_cost']:.6f} per prediction")
    L.append(f"- **embeddings**: {results['embed_secs_all_models']:.1f}s to load models and embed all"
             f" {results['n_concepts']} concepts twice per model (0s when cached); kNN itself is microseconds.")
    L.append("\n## Clustering: do areas and domains fall out of the embeddings?\n")
    L.append("| Embedding | ARI vs 11 areas | ARI vs 2 domains | cooking cluster purity | clusters holding cooking |\n|---|---|---|---|---|")
    for k, v in results["clustering"].items():
        L.append(f"| {k} | {v['ARI vs 11 areas']:.2f} | {v['ARI vs 2 domains']:.2f} |"
                 f" {v['cooking cluster purity']:.2f} | {v['clusters holding cooking']} |")
    (HERE / "results.md").write_text("\n".join(L) + "\n")


def plot(model):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    v = embed(model, [c.text for c in CONCEPTS], "gloss")
    xy = PCA(n_components=2, random_state=0).fit_transform(v)
    fig, axes = plt.subplots(2, 3, figsize=(18, 11))
    ax = axes[0, 0]
    cmap = plt.get_cmap("tab20")
    for k, a in enumerate(AREAS):
        pts = np.array([xy[i] for i, c in enumerate(CONCEPTS) if c.area == a])
        ax.scatter(pts[:, 0], pts[:, 1], s=18, color=cmap(k), label=a)
    ax.set_title(f"By area ({model.split('/')[-1]}, term + gloss, PCA)")
    ax.legend(fontsize=7, ncol=2)
    for ax, persona in zip(axes.flat[1:], PERSONAS):
        y = Y_BY_PERSONA[persona]
        for val, col, lab in ((1, "#2a9d8f", "knows"), (0, "#e76f51", "doesn't")):
            m = y == val
            ax.scatter(xy[m, 0], xy[m, 1], s=np.array([10 + 8 * c.depth for c in CONCEPTS])[m],
                       color=col, alpha=0.75, label=lab)
        for term in ("Postgres", "MVCC", "sourdough starter", "React", "KV cache"):
            i = next(j for j, c in enumerate(CONCEPTS) if c.term == term)
            ax.annotate(term, xy[i], fontsize=7)
        ax.set_title(f"{persona} (dot size = depth)")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(HERE / "map.png", dpi=110)


if __name__ == "__main__":
    main()
