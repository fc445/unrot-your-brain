"""Run each candidate extractor over every session's assistant text, twice.

    uv run python extract.py                      # light extractors
    uv run --group heavy python extract.py --heavy  # + GLiNER, KeyBERT

Writes out/candidates.json: {extractor: {session_id: [[phrase, score], ...]}},
ranked best-first, plus timings and a determinism check (run 1 vs run 2).
Transcript-derived, so it lives under out/ (gitignored).
"""

from __future__ import annotations

import argparse
import math
import re
import time
from collections import Counter, defaultdict

from common import OUT, load_sessions, norm, prose, write_json

URL = re.compile(r"https?://\S+")
MD = re.compile(r"[*#|>_]{1,}")  # emphasis, headings, tables, quotes


def clean(text: str) -> str:
    """Prose the extractors see: no fenced code, URLs, markdown punctuation or backticks."""
    t = prose(text)
    t = URL.sub(" ", t)
    t = t.replace("`", "")
    # keep snake_case identifiers intact; strip markdown runs only when spaced
    t = re.sub(r"(?<!\w)[*#|>]+|[*#|>]+(?!\w)", " ", t)
    return t


def dedupe(ranked):
    """Keep the first (best) surface form for each normalised key."""
    seen, out = set(), []
    for phrase, score in ranked:
        phrase = phrase.strip(" -–—.,;:!?\"'()[]{}")
        if len(phrase) < 2 or not re.search(r"[A-Za-z]", phrase):
            continue
        key = norm(phrase)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append([phrase, round(float(score), 6)])
    return out


# --------------------------------------------------------------------------
# spaCy, loaded once
# --------------------------------------------------------------------------
_nlp = None


def nlp():
    global _nlp
    if _nlp is None:
        import spacy

        _nlp = spacy.load("en_core_web_sm", disable=["ner"])
        _nlp.max_length = 2_000_000
    return _nlp


def merge_hyphens(doc):
    """Glue `tree - sitter` / `ad - hoc` back into one token so POS patterns see one word."""
    spans = []
    i = 0
    while i < len(doc) - 2:
        a, h, b = doc[i], doc[i + 1], doc[i + 2]
        if h.text == "-" and not a.whitespace_ and not h.whitespace_ and a.is_alpha and b.is_alpha:
            j = i + 2
            # chains: at-least-once
            while j + 2 < len(doc) and doc[j + 1].text == "-" and not doc[j].whitespace_ and not doc[j + 1].whitespace_ and doc[j + 2].is_alpha:
                j += 2
            spans.append(doc[i : j + 1])
            i = j + 1
        else:
            i += 1
    with doc.retokenize() as rt:
        for s in spans:
            rt.merge(s, attrs={"POS": s[-1].pos_, "TAG": s[-1].tag_})
    return doc


_docs: dict[str, object] = {}


def parsed(session):
    if session.session_id not in _docs:
        _docs[session.session_id] = merge_hyphens(nlp()(clean(session.assistant_text)))
    return _docs[session.session_id]


# --------------------------------------------------------------------------
# H1: RAKE (own implementation -- the algorithm is ~20 lines; the PyPI ports
# need NLTK corpora downloads for the same stopword list spaCy ships)
# --------------------------------------------------------------------------
def rake(session, max_words: int = 4):
    from spacy.lang.en.stop_words import STOP_WORDS

    text = clean(session.assistant_text)
    phrases = []
    for sentence in re.split(r"[.!?,;:\t\n\"()\[\]{}]+|\s[-–—]\s", text):
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'+/._-]*", sentence)
        current = []
        for w in words:
            if w.casefold() in STOP_WORDS:
                if current:
                    phrases.append(current)
                current = []
            else:
                current.append(w)
        if current:
            phrases.append(current)
    phrases = [p for p in phrases if len(p) <= max_words]
    freq, degree = Counter(), Counter()
    for p in phrases:
        for w in p:
            k = w.casefold()
            freq[k] += 1
            degree[k] += len(p)
    scored = {}
    for p in phrases:
        s = sum(degree[w.casefold()] / freq[w.casefold()] for w in p)
        key = " ".join(p)
        scored[key] = max(scored.get(key, 0), s)
    return dedupe(sorted(scored.items(), key=lambda kv: (-kv[1], kv[0])))


# --------------------------------------------------------------------------
# H2a: spaCy noun_chunks, determiners/pronouns stripped, ranked by frequency
# --------------------------------------------------------------------------
def noun_chunks(session):
    doc = parsed(session)
    counts, first = Counter(), {}
    for nc in doc.noun_chunks:
        toks = [t for t in nc if t.pos_ not in ("DET", "PRON", "NUM", "PUNCT") and not t.is_stop]
        if not toks:
            continue
        phrase = " ".join(t.text for t in toks)
        counts[phrase] += 1
        first.setdefault(phrase, nc.start)
    ranked = sorted(counts, key=lambda p: (-counts[p], first[p]))
    return dedupe([(p, counts[p]) for p in ranked])


# --------------------------------------------------------------------------
# H2b: Justeson & Katz pattern (A|N)*N, all sub-spans up to 4 tokens, C-value
# --------------------------------------------------------------------------
JK_OK = {"ADJ", "NOUN", "PROPN"}


def jk_terms(doc, max_len: int = 4):
    runs, cur = [], []
    for t in doc:
        if t.pos_ in JK_OK and (t.is_alpha or "-" in t.text):
            cur.append(t)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    counts = Counter()
    for run in runs:
        for i in range(len(run)):
            for j in range(i + 1, min(len(run), i + max_len) + 1):
                span = run[i:j]
                if span[-1].pos_ not in ("NOUN", "PROPN") or all(t.is_stop for t in span):
                    continue
                counts[" ".join(t.text for t in span)] += 1
    return counts


def c_value(counts: Counter):
    """Frantzi et al. C-value, with log2(len+1) so single words are not zeroed."""
    by_key = defaultdict(int)
    surface = {}
    for p, n in counts.items():
        k = norm(p)
        by_key[k] += n
        surface.setdefault(k, p)
    keys = list(by_key)
    nested_in = defaultdict(list)
    for a in keys:
        for b in keys:
            if len(b) > len(a) and any(b[i : i + len(a)] == a for i in range(len(b) - len(a) + 1)):
                nested_in[a].append(b)
    scores = {}
    for a in keys:
        f = by_key[a]
        longer = nested_in[a]
        if longer:
            f = f - sum(by_key[b] for b in longer) / len(longer)
        scores[a] = math.log2(len(a) + 1) * f
    ranked = sorted(keys, key=lambda k: (-scores[k], surface[k]))
    return [(surface[k], scores[k]) for k in ranked]


def jk_cvalue(session):
    return dedupe(c_value(jk_terms(parsed(session))))


# --------------------------------------------------------------------------
# H7: YAKE, TextRank / PositionRank
# --------------------------------------------------------------------------
def yake_(session):
    import yake

    kw = yake.KeywordExtractor(lan="en", n=3, dedupLim=0.9, top=400)
    ranked = kw.extract_keywords(clean(session.assistant_text))  # lower = better
    return dedupe([(p, -s) for p, s in ranked])


_tr = {}


def _pytextrank(name):
    import pytextrank  # noqa: F401  registers the factories
    import spacy

    if name not in _tr:
        m = spacy.load("en_core_web_sm")
        m.max_length = 2_000_000
        m.add_pipe(name)
        _tr[name] = m
    return _tr[name]


def textrank(session):
    doc = _pytextrank("textrank")(clean(session.assistant_text))
    return dedupe([(p.text, p.rank) for p in doc._.phrases])


def positionrank(session):
    doc = _pytextrank("positionrank")(clean(session.assistant_text))
    return dedupe([(p.text, p.rank) for p in doc._.phrases])


# --------------------------------------------------------------------------
# H7 heavy: GLiNER zero-shot NER, KeyBERT
# --------------------------------------------------------------------------
GLINER_MODEL = "urchade/gliner_small-v2.1"
GLINER_LABELS = [
    "technical concept",
    "software tool",
    "protocol",
    "operating system feature",
    "programming technique",
    "security mechanism",
    "file format",
]
_gliner = None


def gliner(session, threshold: float = 0.3):
    global _gliner
    if _gliner is None:
        from gliner import GLiNER

        _gliner = GLiNER.from_pretrained(GLINER_MODEL)
    best = {}
    for w in session.windows:
        text = clean(w.assistant_text)
        # GLiNER's context is ~384 words; feed paragraph-sized pieces.
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if len(para) < 20:
                continue
            for piece in _chunks(para, 1200):
                for ent in _gliner.predict_entities(piece, GLINER_LABELS, threshold=threshold):
                    p = ent["text"]
                    best[p] = max(best.get(p, 0), ent["score"])
    return dedupe(sorted(best.items(), key=lambda kv: (-kv[1], kv[0])))


def _chunks(text, size):
    words, cur, n = text.split(), [], 0
    for w in words:
        cur.append(w)
        n += len(w) + 1
        if n >= size:
            yield " ".join(cur)
            cur, n = [], 0
    if cur:
        yield " ".join(cur)


_kb = None


def keybert(session):
    """KeyBERT ranking the J&K candidates by similarity to the session.

    The document embedding is of the whole session, which MiniLM truncates to
    its first 256 word-pieces -- measured here as a limitation, not fixed."""
    global _kb
    if _kb is None:
        from keybert import KeyBERT

        _kb = KeyBERT("sentence-transformers/all-MiniLM-L6-v2")
    cands = [p for p, _ in jk_cvalue(session)]
    if not cands:
        return []
    # keyphrase_ngram_range must cover the candidates, or multi-word ones are
    # silently dropped (default (1, 1) -- seen here on the first run).
    ranked = _kb.extract_keywords(
        clean(session.assistant_text), candidates=cands, keyphrase_ngram_range=(1, 4), top_n=len(cands)
    )
    return dedupe(ranked)


LIGHT = {
    "rake": rake,
    "noun_chunks": noun_chunks,
    "jk_cvalue": jk_cvalue,
    "yake": yake_,
    "textrank": textrank,
    "positionrank": positionrank,
}
HEAVY = {"gliner": gliner, "keybert": keybert}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--heavy", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    sessions = load_sessions()
    extractors = dict(LIGHT)
    if args.heavy:
        extractors.update(HEAVY)
    if args.only:
        extractors = {k: v for k, v in extractors.items() if k in args.only}

    path = OUT / "candidates.json"
    import json

    existing = json.loads(path.read_text()) if path.exists() else {"candidates": {}, "timing": {}, "deterministic": {}}
    for name, fn in extractors.items():
        runs = []
        for r in range(args.runs):
            _docs.clear()  # parse time counts against each run
            started = time.perf_counter()
            result = {s.session_id: fn(s) for s in sessions}
            runs.append((time.perf_counter() - started, result))
        existing["candidates"][name] = runs[0][1]
        existing["timing"][name] = {"seconds_per_run": [round(t, 2) for t, _ in runs], "sessions": len(sessions)}
        existing["deterministic"][name] = all(r[1] == runs[0][1] for r in runs[1:])
        n = sum(len(v) for v in runs[0][1].values()) / len(sessions)
        print(f"{name:14s} {runs[0][0]:7.1f}s  mean cands/session {n:6.1f}  deterministic={existing['deterministic'][name]}")
        write_json(path, existing)


if __name__ == "__main__":
    main()
