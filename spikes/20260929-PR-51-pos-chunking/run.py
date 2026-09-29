"""POS chunking spike: how far can a deterministic pass shrink the detector's input?

Two corpora (the PR-42 real sessions live on another machine, see README):
  self   -- the assistant turns of a real Claude Code transcript, windowed by
            unrot's own build_windows (default: this session's .jsonl)
  docs   -- the assistant-authored prose in spikes/*/README.md, where the PR-42
            gold terms occur, so recall can be checked

For each pruning strategy it reports tokens kept, candidate counts, and whether
the gold terms' sentences survive. No text is written out, only counts.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

import snowballstemmer
import spacy
from wordfreq import zipf_frequency

SPIKE = Path(__file__).resolve().parent
REPO = SPIKE.parents[1]
OUT = SPIKE / "out"

# PR-42 gold terms (ticket's plausible gaps), flat.
GOLD = ["launchd", "LaunchAgent", "named volume", "copyleft", "MCP servers", "FSEvents",
        "Full Keyboard Access", "Liquid Glass", "merge commit", "VoiceOver", "TCC", "xattr",
        "hardened runtime", "notarisation", "actionlint", "appcast", "EdDSA", "ad-hoc signing",
        "Developer ID", "remote code execution", "reflog", "remote-tracking ref", "tree-sitter",
        "Stop hook", "idempotent", "outbox", "dual-write"]
# PR-42 local labels the detector wrongly proposed.
LOCAL = ["provenance gate", "format 1", "promotion PR", "comprehension check", "the funnel",
         "forced retry", "frozen sidecar", "source-link model"]

STEM = snowballstemmer.stemmer("english")
FENCE = re.compile(r"```.*?(```|$)", re.S)
TOK = re.compile(r"[a-z0-9]+")


def ntoks(s: str) -> int:
    # ESTIMATE (chars/4): tiktoken's vocab download is blocked in the sandbox.
    return round(len(s) / 4)


def norm(term: str) -> tuple[str, ...]:
    t = re.sub(r"iz(e|ed|es|ing|ation|ations)\b", r"is\1", term.casefold().replace("`", ""))
    return tuple(STEM.stemWords(TOK.findall(t)))


def contains(text: str, term: str) -> bool:
    """Stemmed contiguous-token containment, so idempotent/idempotency etc. fold."""
    hay, needle = norm(text), norm(term)
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


# ---- POS chunking ---------------------------------------------------------
def tag(tok) -> str:
    if tok.tag_ == "HYPH":
        return "H"
    if tok.pos_ == "ADJ":
        return "A"
    if tok.pos_ in ("NOUN", "PROPN"):
        return "N"
    if tok.pos_ == "ADP":
        return "P"
    return "X"


# Justeson & Katz: (A|N)+ N  |  (A|N)* N P (A|N)* N ; hyphens are glue.
JK = re.compile(r"(?=([AN](?:[ANH])*N|[AN](?:[ANH])*N P (?:[AN][ANH]*)?N))")
CODEY = re.compile(r"[_/\\]|\.\w|\w\.|^\W|\d{3,}|::|=|\(|\)|--")


def chunks(sent) -> list[tuple[str, bool]]:
    """Maximal J&K spans in a spaCy sentence -> (text, has_proper_or_hyphen)."""
    toks = [t for t in sent if not t.is_space]
    s = "".join(tag(t) if tag(t) != "P" else "P" for t in toks)
    # spaces between tags are needed for the ' P ' alternative; build spaced form
    spaced = " ".join(tag(t) for t in toks)
    out, i = [], 0
    for m in re.finditer(r"(?:[ANH] ?)*N(?: P (?:[ANH] ?)*N)?", spaced):
        a = spaced[:m.start()].count(" ")
        b = a + m.group(0).strip().count(" ") + 1
        span = toks[a:b]
        while span and tag(span[0]) in ("H", "P"):
            span = span[1:]
        while span and tag(span[-1]) != "N":
            span = span[:-1]
        if not span:
            continue
        text = "".join(t.text_with_ws for t in span).strip()
        special = any(t.pos_ == "PROPN" or t.text[:1].isupper() for t in span) or "-" in text
        out.append((text, special))
    return out


def _merge(spans):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def rare(text: str, thresh: float) -> bool:
    ws = [w for w in TOK.findall(text.casefold()) if len(w) > 2]
    return bool(ws) and min(zipf_frequency(w, "en") for w in ws) < thresh


# ---- corpora --------------------------------------------------------------
def load_self(jsonl: Path) -> list[str]:
    from unrot.capture.ingest import connect, ingest_file
    from unrot.detector.windows import build_windows, chunk_windows

    home = Path(tempfile.mkdtemp())
    conn = connect(home)
    ingest_file(jsonl, conn=conn, root=home)
    wins = build_windows(conn, jsonl.stem)
    return [w.assistant_text for w in wins], [w.human_text or "" for w in wins], len(chunk_windows(wins))


def load_docs() -> dict[str, str]:
    skip = ("PR-42", "PR-41", "PR-50", "PR-TBD")  # these list the gold terms in tables
    return {p.parent.name: p.read_text() for p in sorted((REPO / "spikes").glob("*/README.md"))
            if not any(k in p.parent.name for k in skip)}


# ---- strategies -----------------------------------------------------------
def analyse(nlp, turns: list[str], zipf: float) -> dict:
    """turns: assistant texts. Returns per-strategy kept text and candidate stats."""
    docs = []
    for t in turns:
        prose = FENCE.sub("\n", t)
        for para in re.split(r"\n{2,}", prose):
            if para.strip():
                docs.append(nlp(para))
    sents = [(s, chunks(s)) for d in docs for s in d.sents if len(s.text.strip()) > 1]

    def keep(pred):
        return [s.text.strip() for s, cs in sents if pred(cs)]

    def good(c):  # a candidate the LLM should see: not code-shaped, and rare or proper
        return not CODEY.search(c[0]) and (c[1] or rare(c[0], zipf))

    strategies = {
        "P0 raw": turns,
        "P1 no fences": [FENCE.sub("\n", t) for t in turns],
        "P2 sentences with any NP": keep(lambda cs: any(not CODEY.search(c[0]) for c in cs)),
        f"P3 sentences with rare/proper NP (zipf<{zipf})": keep(lambda cs: any(good(c) for c in cs)),
    }
    # P4: P3 plus one sentence either side (keeps an inline explanation in view)
    flags = [any(good(c) for c in cs) for _, cs in sents]
    idx = {i for i, f in enumerate(flags) if f}
    idx |= {i + d for i in list(idx) for d in (-1, 1)}
    strategies[f"P4 P3 +-1 sentence"] = [sents[i][0].text.strip() for i in sorted(idx) if 0 <= i < len(sents)]

    # P5: only a +-12-token window around each good candidate, merged
    snips = []
    for s_, cs in sents:
        gs = [c[0] for c in cs if good(c)]
        if not gs:
            continue
        txt = s_.text
        spans = []
        for g in gs:
            i = txt.find(g)
            if i >= 0:
                a = max(0, len(txt[:i].split()) - 12)
                b = len(txt[:i].split()) + len(g.split()) + 12
                spans.append((a, b))
        w = txt.split()
        for a, b in _merge(spans):
            snips.append(" ".join(w[a:b]))
    strategies["P5 +-12 words around good NP"] = snips
    # P6: deduplicated candidate list, each with its first sentence
    seen, first = set(), []
    for s_, cs in sents:
        new = [c[0] for c in cs if good(c) and norm(c[0]) not in seen]
        for c in new:
            seen.add(norm(c))
        if new:
            first.append(s_.text.strip())
    strategies["P6 first sentence per new good NP"] = first

    all_np = [c[0] for _, cs in sents for c in cs if not CODEY.search(c[0])]
    good_np = [c[0] for _, cs in sents for c in cs if good(c)]
    uniq = lambda xs: len({norm(x) for x in xs})
    def covered(pool, g):  # a candidate equals the gold term, or contains it with <=2 extra tokens
        gn = norm(g)
        for c in pool:
            cn = norm(c)
            n = len(gn)
            if n and len(cn) <= n + 2 and any(cn[i:i + n] == gn for i in range(len(cn) - n + 1)):
                return True
        return False
    covered_all = [g for g in GOLD + LOCAL if covered(all_np, g)]
    covered_good = [g for g in GOLD + LOCAL if covered(good_np, g)]
    cand = {"covered_by_any_np": covered_all, "covered_by_good_np": covered_good, "np_mentions": len(all_np), "np_unique_stemmed": uniq(all_np),
            "rare_or_proper_mentions": len(good_np), "rare_or_proper_unique_stemmed": uniq(good_np)}
    return {"strategies": strategies, "cand": cand, "n_sentences": len(sents)}


def report(name: str, res: dict, gold_pool: list[str]) -> dict:
    base = ntoks("\n".join(res["strategies"]["P0 raw"]))
    rows = []
    present = [g for g in gold_pool if contains("\n".join(res["strategies"]["P0 raw"]), g)]
    for k, parts in res["strategies"].items():
        text = "\n".join(parts)
        t = ntoks(text)
        rows.append({"strategy": k, "tokens": t, "pct_of_raw": round(100 * t / base, 1) if base else None,
                     "gold_kept": f"{sum(contains(text, g) for g in present)}/{len(present)}",
                     "gold_missing": [g for g in present if not contains(text, g)]})
    return {"corpus": name, "sentences": res["n_sentences"], "candidates": res["cand"],
            "gold_present_in_raw": present, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", type=Path, help="transcript for the 'self' corpus")
    ap.add_argument("--zipf", type=float, default=4.0)
    a = ap.parse_args()
    nlp = spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])
    nlp.max_length = 5_000_000
    results = []
    if a.jsonl:
        assist, human, nchunks = load_self(a.jsonl)
        r = report("self", analyse(nlp, assist, a.zipf), GOLD)
        r["windows"], r["detector_chunks"] = len(assist), nchunks
        r["human_tokens_kept_whole"] = ntoks("\n".join(human))
        results.append(r)
    docs = load_docs()
    r = report("docs", analyse(nlp, list(docs.values()), a.zipf), GOLD)
    r["docs"] = list(docs)
    results.append(r)
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    for r in results:
        c=r['candidates']; print(f"\n== {r['corpus']}  sentences={r['sentences']}  NP mentions={c['np_mentions']} unique={c['np_unique_stemmed']}  good mentions={c['rare_or_proper_mentions']} unique={c['rare_or_proper_unique_stemmed']}\n   gold/local covered by any NP: {c['covered_by_any_np']}\n   ... by good NP: {c['covered_by_good_np']}")
        print(f"   gold present in raw text: {len(r['gold_present_in_raw'])}")
        for row in r["rows"]:
            print(f"   {row['strategy']:<42} {row['tokens']:>8} tok {row['pct_of_raw']:>6}%  gold {row['gold_kept']}  miss={row['gold_missing']}")


if __name__ == "__main__":
    sys.exit(main())
