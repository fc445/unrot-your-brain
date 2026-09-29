"""Cheap, deterministic matching — what happens before any model is asked.

Two jobs, and the split between them matters:

* **Decide the easy cases without a model at all.** A term that normalises to an
  existing concept's name or one of its aliases is that concept. No judgment is
  required, so none is bought -- most re-encounters of a term take this path and
  cost nothing.
* **Shortlist for the hard ones.** When a model is asked, it should be asked
  about a handful of plausible neighbours rather than handed the whole graph.
  Today's graph fits in a prompt; the point is that it will not always, and a
  resolver that silently degrades once the graph grows is worse than one that
  was built to narrow from the start.

§11 puts near-duplicate detection at the resolver and calls it the highest-value
use of vector similarity in the product. This is the string-only version of that
shortlist. Embeddings would replace `_overlap`, and per S5 would have to record
their `model_version` -- the surrounding structure would not change.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

_PUNCT = re.compile(r"[^a-z0-9]+")

#: How many neighbours the model is shown. Enough to contain the right answer,
#: small enough that the prompt stays about a decision rather than a catalogue.
SHORTLIST = 12


def normalise(name: str) -> str:
    """Casefold and strip punctuation. `K8s` and `k8s.` collapse; `K8s` and
    `Kubernetes` do not -- that is a judgment, and judgments go to the model."""
    return _PUNCT.sub(" ", (name or "").casefold()).strip()


@dataclass(frozen=True)
class Known:
    """One concept as the resolver sees it: what it is called, and what else it is called."""

    concept_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    encounter_count: int = 0

    @property
    def names(self) -> tuple[str, ...]:
        return (self.canonical_name, *self.aliases)


def current(conn: sqlite3.Connection) -> list[Known]:
    """Every concept still standing, as of the last compile.

    Merged-away concepts are excluded: their names survive as aliases on the
    survivor, so they remain findable without being resolvable targets. Reading
    compiled state rather than folding the log here is deliberate -- the
    resolver is a client of the compile step like everything else.
    """
    import json

    return [
        Known(
            concept_id=row["concept_id"],
            canonical_name=row["canonical_name"],
            aliases=tuple(json.loads(row["aliases"] or "[]")),
            encounter_count=row["encounter_count"],
        )
        for row in conn.execute(
            "SELECT concept_id, canonical_name, aliases, encounter_count"
            " FROM compiled_concepts WHERE merged_into IS NULL"
            " ORDER BY canonical_name"
        )
    ]


def exact(known: list[Known], text: str) -> Known | None:
    """The concept this text already names, under any of its names. None if new."""
    target = normalise(text)
    if not target:
        return None
    for concept in known:
        if any(normalise(name) == target for name in concept.names):
            return concept
    return None


# ---------------------------------------------------------------------------
# Shortlist scoring
#
# Everything below only *orders a menu*. Being wrong here costs the model a
# slightly worse set of options, not the user a wrong answer -- nothing in this
# section ever decides that two names are the same concept. What it must avoid
# is the failure that motivated it (PR-38): a word form, abbreviation or
# spelling variant scoring zero against its own concept, so that once the graph
# outgrows the shortlist the right answer is never shown and the model, seeing
# only strangers, files a duplicate. `idempotent` beside `idempotency` was the
# live case.
#
# Stdlib only, on purpose: this runs on every unresolved term and has to stay
# cheap and deterministic. It is the string half of §11's near-duplicate search,
# not a substitute for the embedding half.
# ---------------------------------------------------------------------------

#: Words that carry no identity. "Separation of Concerns" and "Bill of
#: Materials" are not neighbours because both contain "of".
_STOPWORDS = frozenset(
    {"a", "an", "and", "as", "for", "in", "of", "on", "or", "the", "to", "vs", "with"}
)

#: British -> American, applied to both sides before stemming. The two spellings
#: are the same word; nothing about the concept changes with the reader's
#: locale. Word-final only, longest first.
_SPELLING = (
    ("isations", "izations"), ("isation", "ization"), ("ising", "izing"),
    ("ised", "ized"), ("ises", "izes"), ("ise", "ize"),
    ("ysing", "yzing"), ("ysed", "yzed"), ("yse", "yze"),
    ("ours", "ors"), ("our", "or"), ("tre", "ter"), ("ogue", "og"),
)

#: Derivational and inflectional endings, longest first. Deliberately a short,
#: blunt list rather than Porter: over-stemming here only adds a plausible-ish
#: option to a menu, while under-stemming is exactly the bug being fixed.
_SUFFIXES = (
    "izations", "ization", "ations", "ation", "ically", "ional", "ical",
    "encies", "ancies", "ences", "ances", "ities", "ments", "ness",
    "izing", "ings", "ency", "ancy", "ence", "ance", "ment", "ions", "ents",
    "ants", "ized", "izes", "able", "ible", "ity", "ize", "ing", "ion", "ent",
    "ant", "ers", "ive", "er", "ic", "ed", "ly",
)
#: Shortest stem a derivational suffix may leave. Four keeps `client` from
#: collapsing to `cli` and colliding with the command-line kind.
_MIN_STEM = 4

#: Two stems that differ only by a typo still count as the same word, at a
#: discount. Measured on stems, not words, because the endings are shared
#: noise: `locking`/`blocking` is 0.80 on words but 0.67 on `lock`/`block`, and
#: `backpresur`/`backpressur` is 0.86. Stems shorter than five characters are
#: never fuzzed -- three-letter grams on a four-letter word are mostly edges.
_FUZZY_FLOOR = 0.75
_FUZZY_MIN_LEN = 5

#: Scores for the non-token signals. A spacing variant (`multiversion` /
#: `Multi-Version`) is as good as the same words; an abbreviation is strong but
#: a step below, because `UDS` has more possible expansions than the graph shows.
_COMPACT_EQUAL = 1.0
_ABBREVIATION = 0.9
#: The original containment bonus: one name sitting inside the other. The
#: contained side must be at least this long, or `git` sits inside `digital`.
_CONTAINED = 0.5
_CONTAINED_MIN = 4

_PAREN = re.compile(r"\(([^)]*)\)")
_VERSUS = re.compile(r"\b(?:vs|versus)\b")
_NUMERONYM = re.compile(r"^([a-z])(\d{1,2})([a-z])$")
#: Two letters is too little to call an initialism: `CI` would name "cache
#: invalidation" as readily as continuous integration.
_INITIALISM_MIN = 3


def _variants(name: str) -> tuple[str, ...]:
    """A name and each name folded inside it, normalised.

    "MVCC (Multi-Version Concurrency Control)" is two names for one thing in one
    string; "personal access token vs service key" is a concept about two
    things. Scored whole, `MVCC` or `PAT` matches only part of the string and is
    diluted by the rest, so each part is also scored on its own.
    """
    forms = [name]
    inner = _PAREN.findall(name or "")
    if inner:
        forms.append(_PAREN.sub(" ", name))
        forms.extend(inner)
    out: list[str] = []
    for form in forms:
        norm = normalise(form)
        for part in (norm, *_VERSUS.split(norm)):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return tuple(out)


@lru_cache(maxsize=4096)
def _stem(word: str) -> str:
    """Collapse a word to a crude stem: `idempotent`, `idempotency` -> `idempot`.

    Two passes, so `transactional` -> `transaction` -> `transact` meets
    `transaction` -> `transact`. Tokens with digits (`k8s`, `502`) are left
    alone: they are identifiers, not English.
    """
    if any(ch.isdigit() for ch in word):
        return word
    for british, american in _SPELLING:
        if word.endswith(british) and len(word) - len(british) >= 2:
            word = word[: -len(british)] + american
            break
    # Plurals first, so `caches` and `cache` both reach the suffix pass as
    # `cach`/`cache`. `-es` only where English adds it (`boxes`, `processes`);
    # `types` is `type` + s.
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("sses", "xes", "zzes", "ches", "shes")):
        word = word[:-2]
    elif word.endswith("s") and len(word) > 3 and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for _ in range(2):
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
                word = word[: -len(suffix)]
                break
        else:
            break
    # `rebase`/`rebasing` and `commit`/`committed` differ only in the join.
    if word.endswith("e") and len(word) > _MIN_STEM:
        word = word[:-1]
    last = word[-1]
    if len(word) > _MIN_STEM and last == word[-2] and last.isalpha() and last not in "lsz":
        word = word[:-1]
    return word


def _tokens(norm: str) -> frozenset[str]:
    words = [w for w in norm.split() if w not in _STOPWORDS] or norm.split()
    return frozenset(_stem(w) for w in words)


@lru_cache(maxsize=4096)
def _grams(stem: str) -> frozenset[str]:
    padded = f" {stem} "
    return frozenset(padded[i : i + 3] for i in range(len(padded) - 2))


def _likeness(a: str, b: str) -> float:
    """How far two stems count as one word: 1 if equal, a discount if a typo apart."""
    if a == b:
        return 1.0
    if min(len(a), len(b)) < _FUZZY_MIN_LEN or not (a.isalpha() and b.isalpha()):
        return 0.0
    ga, gb = _grams(a), _grams(b)
    dice = 2 * len(ga & gb) / (len(ga) + len(gb))
    return dice if dice >= _FUZZY_FLOOR else 0.0


def _abbreviates(short: str, long: str) -> bool:
    """Whether one normalised name is a standard abbreviation of the other.

    Two conventions, both mechanical rather than a matter of judgment:

    * an initialism -- `uds` for "unix domain socket", `mvcc` for "multi version
      concurrency control" (normalising has already made the hyphen a word
      break), with or without the stopwords, and with a plural `s` allowed;
    * a numeronym -- `k8s` is k, eight letters, s: "kubernetes". Same rule as
      `i18n` and `a11y`, checked against each word and the name run together.
    """
    if " " in short or not 2 <= len(short) <= 8:
        return False
    words = long.split()
    numeronym = _NUMERONYM.match(short)
    if numeronym:
        first, count, last = numeronym.groups()
        return any(
            len(w) == int(count) + 2 and w[0] == first and w[-1] == last
            for w in (*words, "".join(words))
        )
    if len(words) < 2 or len(short) < _INITIALISM_MIN or not short.isalpha():
        return False
    content = [w for w in words if w not in _STOPWORDS]
    initials = {"".join(w[0] for w in words), "".join(w[0] for w in content)}
    return short in initials or (short.endswith("s") and short[:-1] in initials)


def _overlap(a: str, b: str, weight: Callable[[str], float] | None = None) -> float:
    """How much two names look like the same thing. 0 means "no evidence".

    The original PR-21 core survives: Jaccard over word tokens, with a bonus
    when one name sits inside the other. What changed is what counts as the
    same token -- stems rather than exact words, so `idempotent` meets
    `idempotency` and `notarisation` meets `notarization`, with a discounted
    match for a typo -- and that tokens can be weighted by rarity, so sharing
    `pattern` with half the graph counts for less than sharing `outbox`.
    Abbreviations and spacing variants are separate signals on top.
    """
    weight = weight or (lambda _token: 1.0)
    best = 0.0
    for left in _variants(a):
        for right in _variants(b):
            best = max(best, _pair(left, right, weight))
    return best


def _pair(left: str, right: str, weight: Callable[[str], float]) -> float:
    compact_left, compact_right = left.replace(" ", ""), right.replace(" ", "")
    if compact_left == compact_right:
        return _COMPACT_EQUAL
    if _abbreviates(left, right) or _abbreviates(right, left):
        return _ABBREVIATION

    left_tokens, right_tokens = _tokens(left), _tokens(right)
    matched = 0.0
    for token in left_tokens:
        strength, partner = max(
            ((_likeness(token, other), other) for other in right_tokens), default=(0.0, "")
        )
        if strength:
            # A fuzzy pair is two tokens standing for one word; average their
            # weights so it does not matter which side the typo was on.
            matched += strength * (weight(token) + weight(partner)) / 2
    total = sum(map(weight, left_tokens)) + sum(map(weight, right_tokens)) - matched
    score = matched / total if total > 0 else 0.0

    # "backpressure" against "backpressure handling", or "multiversion
    # concurrency" against "multi-version concurrency control": the tokens only
    # partly line up, but one name clearly sits inside the other.
    shorter, longer = sorted((compact_left, compact_right), key=len)
    if len(shorter) >= _CONTAINED_MIN and shorter in longer:
        score = max(score, _CONTAINED)
    return score


def _rarity(known: list[Known]) -> Callable[[str], float]:
    """Inverse document frequency of each stem across the graph's names.

    A graph with four `... pattern` concepts and one `transactional outbox`
    should put the outbox first for `outbox pattern`; unweighted Jaccard scores
    all five the same and leaves it to the tie-break. Smoothed so every token
    weighs at least 1 and one the graph has never seen weighs the most.
    """
    df: Counter[str] = Counter()
    for concept in known:
        df.update(set().union(*(_tokens(v) for name in concept.names for v in _variants(name))))
    n = len(known)
    return lambda token: 1.0 + math.log((n + 1) / (df[token] + 1))


def shortlist(known: list[Known], text: str, limit: int = SHORTLIST) -> list[Known]:
    """The concepts worth showing the model, best first.

    Falls back to the most-encountered concepts when nothing scores, so the
    model always sees *something* of the graph. A resolver shown an empty list
    can only ever answer "new", which would make every misspelling its own
    concept the moment the string matcher fails.
    """
    weight = _rarity(known)
    # Ties break toward concepts the user has actually met. Material naming ~5
    # concepts per piece means scaffolding outnumbers real concepts in the graph
    # very quickly, and a shortlist dominated by things that entered via someone
    # else's explanation is a worse menu than one led by what the user has hit.
    scored = sorted(
        ((max(_overlap(text, name, weight) for name in c.names), c) for c in known),
        key=lambda pair: (-pair[0], -pair[1].encounter_count, pair[1].canonical_name),
    )
    hits = [c for score, c in scored if score > 0][:limit]
    if hits:
        return hits
    return sorted(known, key=lambda c: (-c.encounter_count, c.canonical_name))[:limit]
