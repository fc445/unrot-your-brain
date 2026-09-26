"""Would this person already know this concept? Asked of a System One classifier.

The question the detector cannot answer on its own. A term can be unexplained,
load-bearing and waved through, and still not be a gap -- because this person
already knew it. Only their history can say so, and the history is the graph:
concepts they told us they knew, and concepts they confirmed they did not.

**Why Jev and not embeddings.** PR-34 measured both on a constructed map of 170
concepts across five personas. Proximity captures *area* -- someone who knows
nothing of databases will not know MVCC -- and misses *depth*: Postgres sits
right beside MVCC, and knowing one says nothing about the other. Embedding kNN
scored 0.72 AUC where an area is partly known, which is barely better than a
guess; Jev, shown the same map as text, scored 0.89. A chat model on the same
prompt scored 0.81 at 25x the latency.

**It cannot run without a map.** Asked with no personal history ("would a
typical developer know this?") Jev's "knows" was right about half the time --
useless for holding anything back. So an empty map is not a weaker version of
this question; it is no question at all, and triage passes everything through.

Reached at OpenRouter's `/v1/systemone`, like the grader, as a plain HTTP call.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.request
from dataclasses import dataclass

from ..grader.jev import DEFAULT_JEV_MODEL
from ..model import NO_KEY_MESSAGE, ModelConfig
from ..spend import record_http

#: The same pin as the grader, for the same reason: the probability is stored,
#: and its provenance must name the model that produced it.
DEFAULT_FAMILIARITY_MODEL = DEFAULT_JEV_MODEL

#: How many of each side of the map are shown. The newest first, since recent
#: judgments describe the person as they are now. PR-34 measured maps of about
#: 70 concepts; nothing was measured beyond that, and every entry costs input
#: tokens on every call. Retrieving the relevant slice by embedding is the
#: likely next step once maps outgrow this -- untested, so not built.
MAP_LIMIT = 40

#: The criteria are what Jev discriminates on. The second sentence is the trap
#: the whole question exists for: a neighbouring concept is not the concept.
QUESTION = {
    "type": "choice",
    "instructions": (
        "Would this person already understand the NEW CONCEPT, judging from what"
        " they are known to know and not know?"
    ),
    "criteria": {
        "knows": "They would already understand the new concept, given what they know.",
        "does_not_know": (
            "They would not yet understand the new concept. Knowing a related or"
            " neighbouring concept is not the same as knowing this one."
        ),
    },
}


@dataclass(frozen=True)
class Entry:
    name: str
    gloss: str | None = None

    @property
    def text(self) -> str:
        return f"{self.name}: {self.gloss}" if self.gloss else self.name


@dataclass(frozen=True)
class KnowledgeMap:
    """What we have been told, and nothing we inferred.

    `known` is what compile calls `known`: dismissed as already understood, or
    explained to a causal level. `unknown` is only what the person *confirmed*
    as a gap. An unjudged gap is the detector's opinion, and feeding the
    detector's opinions back in as evidence would let it agree with itself.
    """

    known: tuple[Entry, ...] = ()
    unknown: tuple[Entry, ...] = ()

    def without(self, entry: Entry) -> KnowledgeMap:
        return KnowledgeMap(
            tuple(e for e in self.known if e != entry),
            tuple(e for e in self.unknown if e != entry),
        )

    @property
    def fingerprint(self) -> str:
        body = json.dumps(
            [[e.text for e in self.known], [e.text for e in self.unknown]]
        ).encode("utf-8")
        return hashlib.sha256(body).hexdigest()[:16]


def knowledge_map(conn: sqlite3.Connection, *, limit: int = MAP_LIMIT) -> KnowledgeMap:
    """The person's map, read from compiled state, newest activity first.

    The gloss is the concept's latest paraphrase. It describes the moment the
    term was met rather than defining it, but PR-34 found a term with *some*
    context far better than a bare name -- bare names did not even separate
    software from cooking.
    """
    rows = conn.execute(
        "SELECT c.canonical_name, c.state,"
        "       (SELECT e.paraphrase FROM compiled_encounters e"
        "         WHERE e.concept_id = c.concept_id AND e.paraphrase IS NOT NULL"
        "         ORDER BY e.occurred_at DESC LIMIT 1) AS gloss,"
        "       EXISTS (SELECT 1 FROM compiled_encounters e"
        "         WHERE e.concept_id = c.concept_id AND e.judgment = 'confirmed') AS confirmed"
        "  FROM compiled_concepts c"
        " WHERE c.merged_into IS NULL"
        " ORDER BY COALESCE(c.last_seen_at, '') DESC, c.canonical_name"
    ).fetchall()
    known, unknown = [], []
    for row in rows:
        entry = Entry(row["canonical_name"], row["gloss"])
        if row["state"] == "known":
            known.append(entry)
        elif row["confirmed"]:
            unknown.append(entry)
    return KnowledgeMap(tuple(known[:limit]), tuple(unknown[:limit]))


def render_state(kmap: KnowledgeMap, term: str, gloss: str | None) -> str:
    """The map and the question as Jev reads them. The layout PR-34 measured."""

    def listing(entries) -> str:
        return "\n".join(f"- {e.text}" for e in entries) or "- (none)"

    return "\n".join(
        [
            "Concepts this person KNOWS:",
            listing(kmap.known),
            "",
            "Concepts this person does NOT know:",
            listing(kmap.unknown),
            "",
            f"NEW CONCEPT: {Entry(term, gloss).text}",
        ]
    )


def _endpoint(base_url: str) -> str:
    return base_url.rstrip("/").removesuffix("/v1") + "/v1/systemone"


def build_familiarity(
    config: ModelConfig | None = None, *, model: str | None = None, meter=None
):
    """Return `judge(term, gloss, kmap) -> dict` backed by a System One model.

    The dict carries `p_knows`, `confidence` and `model`. It raises on any
    failure; deciding what a failure means is the caller's business.
    """
    config = config or ModelConfig.from_env()
    if not config.api_key:
        raise RuntimeError(NO_KEY_MESSAGE)

    url = _endpoint(config.base_url)
    chosen = model or DEFAULT_FAMILIARITY_MODEL

    def judge(term: str, gloss: str | None, kmap: KnowledgeMap) -> dict:
        body = json.dumps(
            {
                "model": chosen,
                "state": render_state(kmap, term, gloss),
                "questions": {"familiar": QUESTION},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except Exception as exc:
            record_http(meter, "familiarity", config, error=exc, model=chosen)
            raise
        record_http(meter, "familiarity", config, payload=payload, model=chosen)

        got = (payload.get("answers") or {}).get("familiar") or {}
        probabilities = got.get("probabilities") or {}
        if "knows" not in probabilities:
            raise ValueError(f"no familiarity answer in the response: {payload!r:.200}")
        return {
            "p_knows": float(probabilities.get("knows") or 0.0),
            "confidence": float(got.get("confidence") or 0.0),
            "model": payload.get("model") or chosen,
        }

    judge.model = chosen
    return judge


def familiarity_from_env(config: ModelConfig | None = None, *, meter=None):
    """The judge triage should use, or None when triage should not run.

    None when switched off, when there is no key, and whenever the endpoint is
    local: Jev is a hosted classifier, so running it would send your concept
    names off a machine you pointed at a local model precisely to keep them on
    it. Never raises -- triage is a filter in front of analysis, and a missing
    filter is today's behaviour, not a reason to stop analysing.
    """
    import os

    config = config or ModelConfig.from_env()
    switch = os.environ.get("UNROT_FAMILIARITY", "on").strip().lower()
    if switch in ("off", "0", "false", "no") or not config.api_key or config.local:
        return None
    try:
        return build_familiarity(config, meter=meter)
    except Exception:  # noqa: BLE001
        return None
