"""Score C (and the baselines) against Freddie's blind labels. Run only once `out/labels.json` exists.

Also scores the umbrella ratings if `out/umbrella_ratings.json` exists.

    uv run python score.py

Classes. Freddie's six labels collapse to the transfer test's five outcomes:
`same` and `same_nothing_extra` -> identity; the two `extends` labels keep their
direction; `related_different` -> related; `unrelated` -> unrelated. A model's
answer per setting is the majority of its 3 identical-input repeats.

The number that matters most is the *wrong merge*: the model says identity and
Freddie says anything else. That is the resolver's unnoticeable error (a card
filed under something the user never met); a missed merge only costs a
duplicate that a later merge can fold.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from common import OUT, read_json

HUMAN = {
    "same": "identity", "same_nothing_extra": "identity",
    "a_extends_b": "a_extends_b", "b_extends_a": "b_extends_a",
    "related_different": "related", "unrelated": "unrelated",
}
KEY = HUMAN  # the key uses the same six labels


def coarse(v: str | None) -> str | None:
    """identity / extension (either way) / separate."""
    if v is None:
        return None
    return {"identity": "identity", "a_extends_b": "extension", "b_extends_a": "extension"}.get(v, "separate")


def majority(vs):
    vs = [v for v in vs if v]
    return Counter(vs).most_common(1)[0][0] if vs else None


def auc(pos, neg):
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def main() -> None:
    labels = read_json(OUT / "labels.json")
    key = {k["id"]: k for k in read_json(OUT / "pairs_key.json")["pairs"]}
    verdicts = read_json(OUT / "model_verdicts.json")
    human = {r["id"]: r for r in labels["ratings"] if r.get("label")}
    print(f"labelled {len(human)}/{len(key)}; unsure {sum(r['unsure'] for r in human.values())}")

    # Freddie vs my intended strata (how good was the key?)
    agree = sum(HUMAN[h["label"]] == KEY[key[i]["intended_label"]] for i, h in human.items())
    print(f"Freddie agrees with the intended label (5-way): {agree}/{len(human)}")
    by_stratum = defaultdict(Counter)
    for i, h in human.items():
        by_stratum[key[i]["stratum"]][h["label"]] += 1
    for s, c in sorted(by_stratum.items()):
        print(f"  {s:24s} {dict(c)}")

    per_setting = defaultdict(lambda: defaultdict(list))
    for r in verdicts["calls"]:
        # The swapped run is scored as its own "setting" (verdicts are already
        # mapped back to the displayed A/B orientation by run_c.py).
        name = r["setting"] + (":swapped" if r["swapped"] else "")
        per_setting[name][r["id"]].append(r["verdict"])
    results = {}
    for setting, rows in per_setting.items():
        five = coarse3 = n = wrong_merge = missed_merge = 0
        conf = Counter()
        strata = defaultdict(lambda: [0, 0])
        for i, h in human.items():
            m = majority(rows.get(i, []))
            t = HUMAN[h["label"]]
            n += 1
            five += m == t
            coarse3 += coarse(m) == coarse(t)
            wrong_merge += m == "identity" and t != "identity"
            missed_merge += t == "identity" and m != "identity"
            conf[(t, m)] += 1
            strata[key[i]["stratum"]][0] += m == t
            strata[key[i]["stratum"]][1] += 1
        results[setting] = {
            "five_way": f"{five}/{n}", "three_way": f"{coarse3}/{n}",
            "wrong_merges": wrong_merge, "missed_merges": missed_merge,
            "by_stratum": {s: f"{a}/{b}" for s, (a, b) in sorted(strata.items())},
            "confusion_true_to_model": {f"{t}->{m}": c for (t, m), c in sorted(conf.items(), key=str)},
        }

    # Baselines
    pr38 = verdicts["baselines"]["pr38"]
    tp = sum(pr38[i]["verdict"] == "same" and HUMAN[h["label"]] == "identity" for i, h in human.items())
    fp = sum(pr38[i]["verdict"] == "same" and HUMAN[h["label"]] != "identity" for i, h in human.items())
    fn = sum(pr38[i]["verdict"] != "same" and HUMAN[h["label"]] == "identity" for i, h in human.items())
    results["pr38_shortlist_top1"] = {"identity_tp": tp, "wrong_merges": fp, "missed_merges": fn}
    emb = verdicts["baselines"]["embed"]
    for model in ("BAAI/bge-base-en-v1.5", "sentence-transformers/all-MiniLM-L6-v2"):
        ident = [emb[i][model] for i, h in human.items() if HUMAN[h["label"]] == "identity"]
        other = [emb[i][model] for i, h in human.items() if HUMAN[h["label"]] != "identity"]
        related = [emb[i][model] for i, h in human.items() if HUMAN[h["label"]] != "unrelated"]
        unrel = [emb[i][model] for i, h in human.items() if HUMAN[h["label"]] == "unrelated"]
        contrast = [emb[i][model] for i, h in human.items() if HUMAN[h["label"]] == "related"]
        results[f"embed:{model}"] = {
            "auc_identity_vs_rest": auc(ident, other),
            "auc_identity_vs_related_different": auc(ident, contrast),
            "auc_related_vs_unrelated": auc(related, unrel),
        }
    print(json.dumps(results, indent=1))

    path = OUT / "umbrella_ratings.json"
    if path.exists():
        ratings = read_json(path)["ratings"]
        print("umbrella ratings:", Counter(r["rating"] for r in ratings), "unsure:", sum(r["unsure"] for r in ratings))


if __name__ == "__main__":
    main()
