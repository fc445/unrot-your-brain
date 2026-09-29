"""Per-term signals the filters use: code shape, rarity, Wikipedia, the repo.

PRIVACY: the Wikipedia functions send a bare candidate term (a noun phrase of
at most a few words) to en.wikipedia.org's public API. No sentence, window or
other transcript context is ever sent. This was done for this spike only; the
product would need an explicit decision, or an offline index (see README).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests
from wordfreq import zipf_frequency

from common import OUT, REPO_MAIN, norm, tokens

# --------------------------------------------------------------------------
# H5a: code shape
# --------------------------------------------------------------------------
_PATHY = re.compile(r"[/\\~$@=<>(){}\[\]]|\w\.\w|_|^-|\.\w{1,5}$|::")
_CAMEL = re.compile(r"[a-z][A-Z]|[A-Z]{2,}[a-z]")


def code_shape(term: str) -> bool:
    """Paths, file names, dotted/snake identifiers, flags. Not CamelCase."""
    return bool(_PATHY.search(term))


def camel(term: str) -> bool:
    return bool(_CAMEL.search(term))


# --------------------------------------------------------------------------
# H3: rarity in general English
# --------------------------------------------------------------------------
def zipf_min(term: str) -> float:
    toks = [t for t in re.split(r"[\s\-]+", term.casefold()) if re.search(r"[a-z]", t)]
    if not toks:
        return 8.0
    return min(zipf_frequency(t, "en") for t in toks)


# --------------------------------------------------------------------------
# H4: Wikipedia linking (public API, bare terms only)
# --------------------------------------------------------------------------
API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "unrot-spike-PR-42/0.1 (research spike; bare-term title lookups)"}
CACHE = OUT / "wiki_cache.json"


def _get(params):
    """GET with backoff: the search endpoint answers 429 to ~10 req/s."""
    for attempt in range(6):
        r = requests.get(API, params=params, headers=UA, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After") or 2 ** attempt))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def _load():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {"titles": {}, "phrase": {}}


def _save(c):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False))


def title_variants(term: str) -> list[str]:
    t = re.sub(r"\s+", " ", term.strip())
    out = [t]
    words = t.split(" ")
    last = words[-1]
    if len(last) > 3 and last.endswith("s") and not last.endswith("ss"):
        out.append(" ".join(words[:-1] + [last[:-1]]))
    # "Full keyboard access" vs "Full Keyboard Access": try title case too.
    out.append(" ".join(w[:1].upper() + w[1:] for w in words))
    return list(dict.fromkeys(out))


def wiki_titles(terms, *, batch: int = 50) -> dict[str, str]:
    """term -> 'article' | 'disambiguation' | 'missing'. Exact title (after the
    API's own normalisation and redirects), trying the variants above."""
    cache = _load()
    known = cache["titles"]
    todo = sorted({v for t in terms for v in title_variants(t)} - set(known))
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        data = _get(
            {
                "action": "query",
                "titles": "|".join(chunk),
                "redirects": 1,
                "prop": "pageprops",
                "ppprop": "disambiguation",
                "format": "json",
                "formatversion": 2,
            }
        )
        q = data.get("query", {})
        mapping = {t: t for t in chunk}
        for n in q.get("normalized", []):
            for k, v in mapping.items():
                if v == n["from"]:
                    mapping[k] = n["to"]
        for rd in q.get("redirects", []):
            for k, v in mapping.items():
                if v == rd["from"]:
                    mapping[k] = rd["to"]
        pages = {p["title"]: p for p in q.get("pages", [])}
        for t in chunk:
            p = pages.get(mapping[t])
            if not p or p.get("missing") or p.get("invalid"):
                known[t] = "missing"
            elif "disambiguation" in (p.get("pageprops") or {}):
                known[t] = "disambiguation"
            else:
                known[t] = "article"
        if i // batch % 5 == 4:
            _save(cache)
        # Anonymous API traffic is throttled hard: at 10 req/s this spike got
        # 429s after ~100 batched requests. One a second holds.
        time.sleep(1.0)
    _save(cache)
    rank = {"article": 2, "disambiguation": 1, "missing": 0}
    out = {}
    for t in terms:
        states = [known[v] for v in title_variants(t)]
        out[t] = max(states, key=rank.get)
    return out


def wiki_phrase_hits(terms) -> dict[str, int]:
    """How many Wikipedia articles contain the exact phrase. One request per term."""
    cache = _load()
    known = cache["phrase"]
    for i, t in enumerate(sorted(set(terms) - set(known))):
        data = _get(
            {
                "action": "query",
                "list": "search",
                "srsearch": f'"{t}"',
                "srlimit": 1,
                "srinfo": "totalhits",
                "srprop": "",
                "format": "json",
            }
        )
        known[t] = data.get("query", {}).get("searchinfo", {}).get("totalhits", 0)
        if i % 25 == 24:
            _save(cache)
        time.sleep(0.5)
    _save(cache)
    return {t: known[t] for t in terms}


# --------------------------------------------------------------------------
# H5b: the session's repo
# --------------------------------------------------------------------------
SKIP_DIRS = {".git", ".venv", "node_modules", "spikes", "dist", "build", ".next", "__pycache__", "DerivedData", ".build", "target", "vendor", ".claude"}
TEXT_EXT = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".swift", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".sh", ".txt", ".html", ".css", ".plist", ".xml", ".sql", ".cfg", ".ini", ""}


def repo_for(cwd: str | None, worktree: Path) -> Path | None:
    """The repo a session ran in, or None when its cwd is not one repo."""
    if not cwd:
        return None
    p = Path(cwd)
    if str(p).startswith(str(REPO_MAIN)):
        # Read the worktree this spike runs in: same history, and it is ours to read.
        return worktree
    if (p / ".git").exists():
        return p
    return None


_vocab: dict[Path, set] = {}


def repo_vocab(root: Path, max_n: int = 4) -> set:
    """Every stemmed 1..4-gram in the repo's text files (spikes/ excluded, since
    the e2e spike's README now lists the very labels under test)."""
    if root in _vocab:
        return _vocab[root]
    grams: set = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            fp = Path(dirpath) / f
            if fp.suffix.lower() not in TEXT_EXT or fp.stat().st_size > 1_000_000:
                continue
            try:
                text = fp.read_text(errors="ignore")
            except OSError:
                continue
            # split camel/snake so identifiers contribute their words too
            text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).replace("_", " ")
            for line in text.splitlines():
                toks = norm(line)
                for n in range(1, max_n + 1):
                    for i in range(len(toks) - n + 1):
                        grams.add(toks[i : i + n])
    _vocab[root] = grams
    return grams


def in_repo(term: str, root: Path | None) -> bool | None:
    if root is None:
        return None
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", term).replace("_", " ")
    return norm(t) in repo_vocab(root)
