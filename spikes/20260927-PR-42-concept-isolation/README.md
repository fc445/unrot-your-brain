# PR-42: can a deterministic step isolate candidate concepts before the LLM judges them?

**Ticket:** [PR-42](https://linear.app/freddie-cassidy/issue/PR-42)
**Date:** 2026-09-27
**Data:** the 36 real sessions and the PR-33 synthetic pair captured by the e2e run (`spikes/20260927-PR-41-e2e-pipeline-run`, run1). Read that README first.

## Goal

Today one LLM call per transcript chunk both **finds** candidate terms and **judges** them (load-bearing, unexplained, accepted). The e2e run found the finding half weak: it is unstable (mean pairwise Jaccard 0.11–0.50 over 4 runs), it lets project-local labels through (`provenance gate`, `format 1`, `forced retry` …, about 1 in 5), it is slow (p50 129 s, about 4k reasoning tokens a call), and it misses word-form variants (idempotent/idempotency).

The question: can a deterministic extractor, or a model built for keyphrase/term extraction, produce a **stable candidate list** that contains the real gaps and leaves out local labels, so the LLM only has to judge a fixed list?

## Answer, in short

- **Stable, yes. Selective, no.** Every extractor tried is bit-for-bit deterministic across runs and fast (0.35 s to 60 s for all 36 sessions). But to reach 20+/27 of the plausible gaps they need **250–490 candidates per session**. None of them rejects local labels: most keep 7–8 of the 8, because local labels are ordinary noun phrases and look exactly like `named volume`, `merge commit` or `Stop hook`.
- **No cheap signal separates "learnable concept" from "local label".** Rarity, Wikipedia title, Wikipedia phrase hits and "appears in the repo" each remove some local labels, but they remove real gaps at a similar rate (table below). A Wikipedia article is a strong *positive* signal: 0 of 8 local labels have one. But only 12 of 27 gaps do.
- **The hybrid (extractor proposes, LLM judges a fixed list) did not help** on 4 sessions × 3 runs. Latency and reasoning were the same or worse (88–257 s a call, 2.6k–8k reasoning tokens, and 3 of 15 primary calls hit the 8k ceiling and needed the forced retry). Stability was mixed: better on one session, worse on another. Local labels were swapped for other project terms (`resolver`, `Jev`, `store-before-grade`). And the fixed list cost a real gap: `ad-hoc signing` was not on the list, so the model returned `ad hoc`.
- **Recommendation:** keep a **single LLM call** as the finder. Add **deterministic post-processing**, not a pre-gate: anchor each returned term to the text, canonicalise it by stemming against the extracted noun phrases, and use a "has a Wikipedia article" boost and a repo-glossary hint as soft signals. Details are under [Recommendation](#recommendation).

## Headline results

Recall is over **27 (session, term) pairs** from the ticket's plausible-gap list (24 terms; TCC and xattr were found in two sessions each and notarisation in two). A pair is a hit if some candidate for **that session** equals the term after normalisation (casefold, `-ise`/`-ize`, Snowball stem), or contains it with at most 2 extra tokens. "Local kept" counts how many of the 8 local labels survive in their own session: **lower is better**. Filter `code` drops paths, file names, dotted/snake identifiers and flags. It did not remove a single gold term. Runtime is for all 36 sessions on an M-series Mac, CPU only.

| Extractor (code-filtered) | Gap recall | Recall in top 50 | Synthetic gaps (outbox, dual-write) | Synthetic not-gaps reach judge | Local kept (↓) | Mean candidates / session | Deterministic (2 runs) | Runtime, 36 sessions | Install weight |
|---|---|---|---|---|---|---|---|---|---|
| H1 RAKE (own impl., spaCy stop-words) | **23/27** | 7/27 | 2/2 | 1/2 | 8/8 | 465 | yes | 0.35 s | spaCy only |
| H2a spaCy `noun_chunks` | 22/27 | 12/27 | 2/2 | 2/2 | 7/8 | 255 | yes | 5.9 s | spaCy + en_core_web_sm ≈ 40 MB |
| H2b Justeson & Katz + C-value | 20/27 | 10/27 | 2/2 | 2/2 | 7/8 | 389 | yes | 6.8 s | same |
| H2b … cut to top 150 / session | 15/27 | 10/27 | 2/2 | 2/2 | 2/8 | 121 | yes | 6.8 s | same |
| H7 YAKE (n≤3, top 400) | 16/27 | 4/27 | 2/2 | 1/2 | **0/8** | 273 | yes | 1.8 s | < 1 MB (+ numpy, networkx) |
| H7 TextRank (pytextrank) | **23/27** | 6/27 | 2/2 | 2/2 | 7/8 | 320 | yes | 9.9 s | spaCy + < 1 MB |
| H7 PositionRank (pytextrank) | **23/27** | 6/27 | 2/2 | 2/2 | 7/8 | 320 | yes | 10.0 s | spaCy + < 1 MB |
| H7 GLiNER small v2.1, 7 zero-shot labels, t=0.3 | 18/27 | **15/27** | 1/2 | 2/2 | 3/8 | **66** | yes | 57 s | torch 573 MB + transformers 61 MB + weights 611 MB ≈ 1.25 GB |
| H7 KeyBERT (MiniLM) re-ranking J&K | 10/27 | 6/27 | 1/2 | 1/2 | 5/8 | 257 | yes | 10.3 s | torch + sentence-transformers + weights 91 MB |
| Union GLiNER + noun_chunks | 25/27 | – | – | – | 8/8 | 284 | yes | ~63 s | ≈ 1.3 GB |
| *Today's detector (LLM, e2e run)* | *27/27 by construction* | – | *outbox 1/4 runs, dual-write 0/4* | – | *8 local labels passed* | *0–4 per chunk* | *no (Jaccard 0.11–0.50)* | *p50 129 s per call* | *API* |

Filters layered on top. Rows come from `out/scores.json`. `rare` keeps a phrase whose rarest word has a Zipf frequency under 3.5 (H3). `wiki` keeps phrases with an exact Wikipedia title or disambiguation page (H4). `repo-local` drops phrases found in the session's repo that have no Wikipedia title (H5). Wikipedia lookups were run only on the GLiNER and J&K-top-150 lists, because of throttling (see Privacy).

| Extractor | Filter | Gap recall | Local kept (↓) | Mean cands |
|---|---|---|---|---|
| noun_chunks | code + rare<3.5 | 17/27 | 4/8 | 80 |
| J&K top-150 | code + repo-local | 13/27 | **0/8** | 103 |
| J&K top-150 | code + wiki | 5/27 | 0/8 | 65 |
| J&K top-150 | code + (rare \| wiki) − repo-local | 12/27 | 0/8 | 82 |
| GLiNER | code + rare<3.5 | 14/27 | 2/8 | 31 |
| GLiNER | code + repo-local | 15/27 | 1/8 | 53 |
| GLiNER | code + wiki | 8/27 | 0/8 | 38 |
| GLiNER | code + camelCase + backticks dropped (H5 "code" claim) | 13/27 | 3/8 | 55 |

Every filter that rejects local labels also costs **5–15 of the 27 gaps**. Of all the filters, `repo-local` on the J&K top 150 comes closest: 0/8 local labels kept and 13/27 gaps. But it gets there partly because the top-150 cut had already dropped 5 gaps, and the repo it checks is today's, not the one at session time (see caveats).

### The per-term signals, on the gold terms only (`out/gold_signals.md`)

| Signal | Gaps with it (of 27 pairs) | Local labels with it (of 8) | Reading |
|---|---|---|---|
| Wikipedia article or disambiguation page (exact title, redirects followed) | 12 | **0** | Precise, low recall. It misses Apple/Docker/git doc terms (`hardened runtime`, `named volume`, `Developer ID`, `remote-tracking ref`, `Stop hook`) and brittle titles (`tree-sitter` has no exact-title article). |
| Wikipedia phrase hits = 0 | 5 (`LaunchAgent`, `actionlint`, `appcast`, `ad-hoc signing`, `remote-tracking ref`) | 4 (`provenance gate`, `forced retry`, `frozen sidecar`, `source-link model`) | Overlapping. Wikipedia is too small a corpus for developer-docs jargon. |
| Rarest word Zipf < 3.5 | 19 | 4 | Kills compositional gaps built from common words (`named volume` 4.76, `Stop hook` 4.37, `remote code execution` 4.29). |
| Found in the session's repo (sessions with a resolvable repo: 21 gaps, 8 locals) | 12 of 21 | 6 of 8 | Gaps end up in the repo because the session *implemented* them (Sparkle, signing, notarisation). "In repo" does not mean "local". |
| In repo **and** no Wikipedia title | 7 of 21 | 6 of 8 | Better, still not a hard filter. |
| CamelCase | 4 (`LaunchAgent`, `FSEvents`, `VoiceOver`, `EdDSA`) | 0 | "CamelCase = code" is **wrong** for this domain: Apple API and product names are real gaps. |
| Backticked somewhere in the session | `actionlint`, `tree-sitter`, `appcast` | 0 | "Backticked = code" is also wrong: tools get backticked. |

### H8, the hybrid (`hybrid.py`, `out/hybrid-jk_cvalue.json`)

The chunks, prompt, schema and model are the same as the detector's (`inclusionai/ling-3.0-flash`, effort low, via `unrot.model`). One block is added: the J&K + C-value candidates (code-filtered, top 150 per session) that occur in the chunk, plus "return only terms from this list". 3 runs per session, 6 calls in flight. The baseline is the e2e run's main pass plus its 3 repeats. Stability is mean pairwise Jaccard of the *emitted* terms, as in the e2e report.

| Session | List size | Baseline Jaccard (4 runs) | Hybrid Jaccard (3 runs) | Baseline s/call | Hybrid s/call | Hybrid reasoning tokens | Hybrid emitted, per run |
|---|---|---|---|---|---|---|---|
| 33d84b15 (Sparkle) | 145 | 0.11 | **0.56** | 107–289 | 92–238 | 3.0k–7.7k (1 ceiling hit) | {ad hoc, Developer ID} ×2, {Developer ID, Sparkle} |
| 0805c0ae (signing CI) | 142 | 0.28 | **0.00** | 109–230 | 160–237 | 5.1k–8.0k (1 ceiling hit) | {Developer ID, pre-release}, {default branch}, {hardened runtime}; one off-list `notarisation` dropped |
| sess-messy (synthetic) | 25 | 0.50 | 0.33 | 57–64 | 88–95 | 2.6k–3.2k | {idempotency key}, {dual-write gap} ×2 |
| 2868a6fa (had `provenance gate`, `format 1`) | 143 + 78 | n/a (1 run) | 0.56 | 219–222 | 115–257 | 4.5k–7.9k (1 ceiling hit) | {resolver, store-before-grade, graph}, {Jev, Resolver, future resolution}, {Jev, resolver, graph} |

Total: 18 billed calls (15 primary + 3 forced retries), $0.021, 587 s wall-clock.

What this shows, with n = 3 runs on 4 sessions:
- **No latency win.** Reasoning still dominates: the model reasons over the transcript either way, and a 145-item list adds to what it weighs.
- **Stability is not fixed by fixing the list.** On 0805c0ae the three runs picked three disjoint answers from the *same* list. Many terms there are valid (hardened runtime, notarisation, actionlint, branch protection, Developer ID), and the budget of 2 turns choosing among them into a coin toss. Part of the measured instability is the budget, not the finder.
- **Local labels move rather than disappear.** `provenance gate`/`format 1` went, `resolver`, `Jev` and `store-before-grade` came. The list was noisy, and the judge still took project vocabulary.
- **A fixed list can make things worse.** POS patterns miss gerund heads (`signing` is tagged VERB), so `ad-hoc signing` was not offered and the model picked the fragment `ad hoc`. It also once tried to return `notarisation`, which was not in the top-150 list.
- On the synthetic session, `dual-write` (never found in the baseline's 4 runs) came back in 2 of 3. `outbox` (1 of 4 baseline runs) did not come back at all. That is too few runs to call either way.

## What was verified, and how

**Verified empirically (this data, these settings):**
- Every extractor is deterministic: two full runs over 36 sessions gave identical ranked lists (`out/candidates.json`, the `deterministic` key). For GLiNER and KeyBERT that holds on CPU only; GPU/MPS were not tried.
- All 27 gold (session, term) pairs and all 8 local labels occur verbatim (or as a word-form variant) in the assistant text of their session. None sits only inside fenced code, so stripping fences is safe for this gold set.
- Recall, candidate counts and local-label survival as tabled, via `score.py` and `quick_recall.py`.
- Stemming plus `-ise`/`-ize` folding (H6) unifies `idempotent`/`idempotency`/`idempotence`, `notarise(d)`/`notarisation`/`notarization`, `MCP server(s)` and `ad-hoc signed/signing`. Over-merging was not measured beyond eyeballing (for example, `Developer` → `develop`).
- Wikipedia redirects (`out/redirects_probe.txt`): K8s → Kubernetes, idempotent/Idempotency → Idempotence, xattr → Extended file attributes. But **notarization → Notary**, a wrong sense, and `notarisation` and `LaunchAgent` do not resolve.
- Specific tool failures seen here:
  - spaCy's small model tags `FSEvents`, `reflog` and `TCC-protected` as verbs, so POS-pattern extractors lose them.
  - spaCy's stop-word list contains `full`, so RAKE splits `Full Keyboard Access`.
  - KeyBERT's `candidates=` silently drops multi-word candidates unless `keyphrase_ngram_range` covers them, and even then it dropped about a third of J&K's list. Not chased further.
  - GLiNER needs `transformers<5` + `sentencepiece` + `protobuf`. With transformers 5.x the DeBERTa tokenizer fails to load.
- Wikipedia's public API throttles anonymous traffic hard. Batched title lookups returned HTTP 429 after about 100 requests at 10 req/s, and 1 req/s held. At candidate scale (about 8k distinct J&K terms over 36 sessions, or 16k title variants) that is unusable live.

**Assumed, or not established:**
- **The gold set is not a blind label set.** The 24 "plausible gaps" are the detector's own finds, judged plausible by a person, and the 8 local labels are its own mistakes. That biases recall towards terms an LLM surfaces, and says nothing about gaps the LLM never proposed. Precision of the candidate lists could not be measured: most of the 250+ candidates per session are unlabelled.
- Only the 2 PR-33 synthetic sessions have real (synthetic) labels. **Nothing here generalises beyond these 36 sessions**, one user and mostly one repo.
- The repo lookup reads **today's** repo (this worktree for unrot sessions, excluding `spikes/`, and the sibling repos on disk for `dashboard`/`managed harnesses`), not the repo as it stood when each session ran. Sessions whose cwd was `~/Documents/GitHub` or `~/Documents` have no repo, so 6 gold pairs are "n/a".
- Hybrid stability comes from 3 runs against the baseline's 4, at different concurrency (6 in flight against up to 24). Latency comparisons are rough.
- GLiNER labels and threshold (0.3) were picked once and not tuned. Tuning could move its row either way.

## Hypotheses, one line each

1. **RAKE:** highest recall with the least machinery (23/27), but 465 candidates a session, all 8 local labels kept, and stop-words split real terms.
2. **POS chunking / C-value:** 20–22/27 with cleaner phrases. C-value ranks by frequency, and gaps are often mentioned once or twice, so a top-K cut loses them (20 → 15 at top 150).
3. **Rarity (wordfreq):** cuts lists 3–4×, keeps 4 of 8 locals, and loses compositional gaps made of common words.
4. **Wikipedia linking:** a precise "is a learnable concept" signal (0/8 locals linkable) with 44% recall on gaps. Live API use is impractical at scale; an offline index is needed.
5. **Repo lookup / code shape:** the code-shape filter is free and safe (0 gold lost). "CamelCase/backticked = code" is wrong here. "In repo (and unlinkable) = local" catches 6/8 locals but also 7 gaps.
6. **Stemming:** works for the variants seen, and is the cheapest fix available. It belongs in dedupe and in the resolver.
7. **Purpose-built models:** GLiNER is the best single extractor on list size (66/session, 15/27 in the top 50, only 3/8 locals). But it weighs 1.25 GB and takes ~1.6 s per session on CPU. YAKE keeps 0/8 locals but finds only 16/27 gaps. KeyBERT is poor here: long documents get truncated to a 256-token embedding. `pke` and SciSpaCy were skipped (see below).
8. **Hybrid:** not better on stability or latency in this test, and it introduces list-coverage failures.

## Recommendation

**Detector shape: keep the single LLM call as finder and judge. Put the deterministic steps after it, and use them as signals rather than gates.**

A deterministic prefilter can't be the gate. At the recall a gate needs (≥ 80%) the lists are hundreds long, and every signal that removes local labels removes real gaps at a similar rate. Constraining the LLM to such a list bought no latency and no reliable stability, and cost a gap to coverage. What deterministic steps do well is cheap, verifiable and stable, so use them for:

1. **Anchoring and canonicalising the LLM's term** (feeds PR-38). Require the returned term to occur in the assistant text of the cited window. Then snap it to the longest extracted noun phrase that contains it, with the stem-normalised key as the identity. This fixes `idempotent`/`idempotency` duplicates and `ad hoc`-style fragments, and it is deterministic.
2. **A soft "learnable concept" prior** (feeds PR-44). A Wikipedia article or disambiguation page, from an **offline** titles+redirects index and not the live API, is strong evidence the term is not local. Absence is not evidence of anything.
3. **A repo glossary as a local-label hint** (feeds PR-44). Phrases that appear in the session's repo docs/code and have no Wikipedia title can be passed to the judge as "names coined in this project", or used to down-rank a candidate. Don't use them to drop one: they include `hardened runtime`, `appcast`, `ad-hoc signing` and `Developer ID`.
4. **Measure stability on something other than the top-2 budget.** On 0805c0ae all the disjoint picks were plausible. Record the ranked list with a larger budget, or recall against a stable reference set, before concluding the finder is unstable.

If a prefilter is still wanted later (for example to cut prompt size on a local model), **GLiNER + `noun_chunks` (union, 25/27, ~284/session)** is the candidate generator to start from. It should shortlist evidence, not constrain the answer.

### What feeds which ticket

- **PR-38 (resolver shortlist):** Snowball stemming plus `-ise`/`-ize` folding for `_overlap` (verified on the variants above). Wikipedia **redirects** from an offline index as an alias source (K8s → Kubernetes, xattr → Extended file attributes, idempotent → Idempotence), with the caveat that redirects can point to a wrong sense (notarization → Notary), so treat them as a hint for the model and never as an automatic merge.
- **PR-44 (filtering project-local labels):** the per-term table above is the evidence. Don't hard-filter on rarity, CamelCase, backticks or "in repo". The workable signals are: (a) has a Wikipedia article → not local; (b) in the repo and unlinkable → *possibly* local, shown to the judge as such; (c) not yet tried: ask a cheap per-term classifier (for example Jev) "is this a name coined in this project or a concept documented elsewhere?", with the bare term plus repo presence as input.

## How to run

From this folder:

```bash
uv sync                                        # light extractors (spaCy, wordfreq, YAKE, pytextrank)
uv run python extract.py                       # -> out/candidates.json (~75 s)
uv sync --group heavy                          # + torch, GLiNER, KeyBERT (~1.25 GB incl. model weights on first use)
uv run --group heavy python extract.py --heavy --only gliner keybert
uv run python quick_recall.py                  # recall + misses per extractor, unions; no network
uv run python score.py --wiki                  # full table -> out/scores.json (Wikipedia titles: ~10 min at 1 req/s)
uv run python gold_signals.py > out/gold_signals.md   # per-term signals for the gold terms
uv run python redirects_probe.py               # PR-38 redirect probe
uv run python hybrid.py --extractor jk_cvalue --cap 150 --sessions 33d84b15 0805c0ae sess-messy 2868a6fa --repeats 3
```

`hybrid.py` reads `OPENROUTER_API_KEY` from the repo-root `.env` and costs about $0.02 as run. `--dry-run` prints list and prompt sizes without calling the model. Everything reads the e2e run's store at `spikes/20260927-PR-41-e2e-pipeline-run/out/run1/home`, which is gitignored and has to exist on disk.

## Files

| File | What it is |
|---|---|
| `common.py` | Paths, the gold terms (from the ticket), session loading via `unrot.detector.windows.build_windows`, fence stripping, normalisation and matching. |
| `extract.py` | The 8 extractors (RAKE, noun_chunks, J&K+C-value, YAKE, TextRank, PositionRank, GLiNER, KeyBERT), run twice for determinism and timing. |
| `signals.py` | Code shape, CamelCase, Zipf rarity, Wikipedia title/phrase lookups (cached, throttled) and the repo n-gram vocabulary. |
| `score.py` | Extractor × filter scoring → `out/scores.json` and a markdown table. |
| `quick_recall.py` | Unfiltered recall with named misses, plus unions of extractor pairs. |
| `gold_signals.py` | The per-term signal table for the gold terms. |
| `redirects_probe.py` | The Wikipedia redirect probe for PR-38. |
| `hybrid.py` | H8: the extractor list fed into the real detector prompt, compared with the e2e baseline. |
| `pyproject.toml` | uv project; `heavy` dependency group for torch-based extractors. |
| `out/` (gitignored) | `candidates.json` (quotes transcript phrases), `scores.json`, `wiki_cache.json`, `gold_signals.md`, `hybrid-jk_cvalue.json` (model paraphrases of sessions), logs. |

## Privacy: what left the machine

- **Wikipedia (en.wikipedia.org public API):** only **bare candidate terms**, noun phrases of a few words, never a sentence or window. That was the 36 gold terms (title and exact-phrase lookups), 14 resolver-probe names, and 10.8k title variants in batched title lookups: about 3.5k distinct candidates from the GLiNER and J&K-top-150 lists, plus about 5k variants from a first attempt over the full RAKE/J&K/noun_chunks/GLiNER lists, which was abandoned when the API throttled it. Some of those candidates are project vocabulary, so a term like `provenance gate` did reach Wikipedia as a bare string. **This was done for this spike only.** The product would need an explicit decision on this, or, better, an **offline index**: the enwiki all-titles plus redirects dump is a single file of roughly 100 MB (size not verified here, and not downloaded). The live API is not viable anyway at about 16k title variants for 36 sessions.
- **OpenRouter:** the 18 hybrid calls sent transcript chunks, exactly as the product's detector already does.
- Model weights came from Hugging Face (GLiNER small v2.1, all-MiniLM-L6-v2, the DeBERTa-v3 tokenizer). No data was sent to Hugging Face.

## Open gaps

- **Real labels.** Recall is against the detector's own plausible finds. Without blind PR-27 labels, what the LLM never proposed is invisible, and candidate-list precision is unmeasured.
- **Skipped:**
  - `pke`: unmaintained, git-only install with NLTK corpus downloads, and its YAKE, TextRank and PositionRank are already covered above.
  - SciSpaCy: biomedical vocabulary, off-domain.
  - GLiNER medium/large (about 2× and 4× the weights) and `en_core_web_trf` (a transformer POS tagger, which might fix the `FSEvents`/`reflog` verb tags).
- **Not tried:** an offline Wikipedia/Wikidata index; per-term classification of the candidate list (Jev or a small LLM) as a replacement for the constrained single call; letting POS patterns take gerund heads (`ad-hoc signing`); a larger budget (> 2) when measuring stability.
- **Hybrid n is tiny:** 3 runs on 4 sessions. The 0.56 vs 0.11 and 0.00 vs 0.28 swings are within what 3 samples can do by chance.
- The repo lookup's time skew (today's repo, not the one at session time) is unquantified.
