# POS chunking: can it cut the detector's candidates or input size?

**Ticket:** none yet (folder says `PR-TBD`; rename to the ticket ID once one exists, and fix the index row in `spikes/README.md`).
**Date:** 2026-09-29
**Follow-on to:** [PR-42](../20260927-PR-42-concept-isolation/) (deterministic extractors as a *candidate generator*). This one asks the other half: use POS chunks to **shrink what the LLM reads**, or to **shortlist what it judges**.

## Answer, in short

- **Weak evidence, leaning no.** On the only corpus I could build here (533 sentences of assistant-written prose), POS-guided pruning cut input by **20–32%** at best, and every gold term survived. But that corpus is dense technical prose where almost every sentence has a noun phrase. Real transcripts are chattier, so savings there could be larger. **That is untested.**
- **The free win is fenced code** (3.8% here; PR-42 found it safe on the real gold set). Everything beyond it costs context the judge needs.
- **Candidate cut is modest:** 1,101 unique noun phrases → 512–846 after code-shape + "rare or proper/hyphenated" filtering (zipf 3.0–4.5). Same story as PR-42: a filter strict enough to matter is strict enough to lose gaps. Nothing here separates concepts from local labels.
- **Input size is not the latency bottleneck.** PR-41/42 measured ~4k reasoning tokens per call (p50 129 s); the prompt is 40k chars a chunk at most. Cutting 25% of input will not change that much. Not re-measured here (no API key).

## What I ran

`run.py` (spaCy `en_core_web_sm`, wordfreq, Snowball) on the `docs` corpus: the 6 spike READMEs that do not themselves list the PR-42 gold terms (PR-9, 10, 6, 18, 33, 34, 35; PR-41/42/50 excluded — verify the list with `ls ..`). Noun-phrase chunks come from a Justeson-&-Katz-style pattern over UPOS tags (hyphens as glue). A "good" chunk is not code-shaped and is either proper/capitalised/hyphenated or has a word with Zipf frequency below the threshold.

Token counts are **chars/4 estimates** (tiktoken's vocab download is blocked here).

| Strategy | zipf 3.0 | 3.5 | 4.0 | 4.5 | Gold kept |
|---|---|---|---|---|---|
| P0 raw | 100% | 100% | 100% | 100% | 9/9 |
| P1 strip fenced code | 96.2% | 96.2% | 96.2% | 96.2% | 9/9 |
| P2 drop sentences with no NP | 93.5% | 93.5% | 93.5% | 93.5% | 9/9 |
| P3 keep sentences with a good NP | 76.1% | 79.4% | 85.6% | 89.7% | 9/9 |
| P4 P3 plus one sentence either side | 92.1% | 93.4% | 94.9% | 95.4% | 9/9 |
| P5 ±12 words around each good NP | 68.6% | 73.7% | 81.4% | 87.8% | 9/9 |
| P6 first sentence for each new good NP | 67.9% | 71.2% | 75.5% | 81.4% | 9/9 |

Candidates (533 sentences): 1,999 NP mentions / 1,101 unique (stemmed). Good NPs: 761/512 (zipf 3.0), 871/582 (3.5), 1,084/691 (4.0), 1,365/846 (4.5). All 9 gold terms present (xattr, hardened runtime, appcast, EdDSA, ad-hoc signing, Developer ID, idempotent, outbox, dual-write) were covered by a good NP at every threshold.

Reading it:
- **P4 shows the cost of keeping context.** Keeping the neighbouring sentence (so an inline explanation stays visible, which the prompt says to skip) hands back almost all the savings.
- **P5/P6 are the aggressive ones** and save the most, but they drop the surrounding reasoning. The judge decides "load-bearing" from that reasoning. Quality is unmeasured.
- **Gerund heads:** spaCy small tagged `ad-hoc signing` fine here, but PR-42 saw `signing` tagged VERB. Not re-checked on more text.

## Verified vs assumed

**Verified (this data):** the token/sentence reductions above; that all 9 gold terms present in these docs survive every strategy; the run is deterministic in structure (no randomness; not diffed across runs).

**Not verified, and matters:**
- **The corpus is not a transcript.** The 36 real sessions from PR-41/42 live on the ticket owner's Mac (`spikes/20260927-PR-41-e2e-pipeline-run/out/` is gitignored and absent here). The `self` corpus (this session's own transcript) held only 11 usable sentences and says nothing. Only 9 of 27 gold terms occur in the docs, and being in READMEs they are written to be understood, not waved through.
- **Recall here is trivial**: a term in a dense sentence is kept by any sentence-level rule that keeps NPs. It says nothing about terms in chatty turns.
- **No LLM was called** (no API key in the sandbox), so I have no effect on detection quality, stability or latency. "Fewer tokens" is not "same answers".
- **Local labels weren't measured**: none of the 8 occur in these docs.
- Zipf thresholds were swept, not tuned to anything.

## To finish this properly

On the machine with the run1 store:
1. Point `load_self` at the 36 sessions (PR-42's `common.py:load_sessions`) and run P0–P6, scoring the 27 gold pairs and 8 local labels.
2. Run the real detector prompt on P1/P3/P5 output for the four `hybrid.py` sessions × 3 runs, and compare Jaccard, reasoning tokens and latency with the baseline. That is the test that answers "does shrinking the input change anything".
3. Check the human turn: it is always kept whole here, but it is the acceptance evidence.

## How to run

```bash
uv sync
uv run python run.py --zipf 4.0                 # docs corpus, prints the table
uv run python run.py --jsonl <session>.jsonl    # adds the transcript corpus (needs no key)
```
`out/results.json` holds counts only, gitignored. Runtime is about 30 s per threshold.

## Files

`run.py` (everything), `pyproject.toml` / `uv.lock` (uv project; spaCy model from GitHub releases).
