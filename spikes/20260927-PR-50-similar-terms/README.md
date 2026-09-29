# PR-50: how do similarly named terms relate, and what should be counted?

**Ticket:** [PR-50](https://linear.app/freddie-cassidy/issue/PR-50) (research spike). It blocks the design ticket PR-49.
**Date:** 2026-09-27
**Status:** done. A, C, D and E ran, both sets were labelled blind by Claude (see [Who labelled the data](#who-labelled-the-data)), and C was scored. Results and recommendations for PR-49 are at the end.
**Data:** the e2e run's store (`spikes/20260927-PR-41-e2e-pipeline-run/out/run1`, gitignored, on this machine only), PR-42's candidate lists, the real graph at `~/.unrot/unrot.db` (opened read-only), and PR-38's resolver fixture. Read those spikes' READMEs for context.

## The question

Similarly named terms relate in different ways. They can be:

- identical;
- the same concept under another surface: spelling, word form or abbreviation;
- barely different, with nothing extra to learn (`Developer ID` / `Developer ID signing`);
- related but extending knowledge (`Git Rebase` / `rebase --onto`);
- contrasting in the same space (optimistic / pessimistic locking);
- unrelated.

PR-49 proposes four things:

1. **A directional transfer test.** "Could someone who can correctly explain A in a line or two also explain B?", asked both ways. Both yes means identity (one concept, alias). One way only means extension (separate concepts). Neither means related or unrelated (separate).
2. **Count independent occasions, not mentions.** The same concept across sessions is the strongest signal.
3. **Surface an umbrella** only when 3 or more related candidates co-occur in one decision window.
4. **A size limit.** A card must be answerable by the check "In a line or two: what is X, and why does it work the way it does?". Anything bigger is a topic.

This spike measures whether a model can apply (1), how often (3) would fire, and what (2)'s counting units do on real data. It follows the ticket's five-part method, A to E.

## What's done

| Part | What | Where |
|---|---|---|
| A | 66 pairs, stratified by *intended* relation, sources derived automatically | `build_pairs.py` → `out/pairs.json` (blind) and `out/pairs_key.json` (**answer key: Freddie must not open it**) |
| B prep | A blind labelling page | `make_pages.py` → `out/label.html` |
| C | The transfer test on every pair: ling low, ling off and Jev, 3 identical repeats plus 1 A/B-swapped run each. Baselines: PR-38 shortlist top-1, and embedding cosine | `run_c.py` → `out/model_verdicts.json` (**contains verdicts: don't show before labelling**) |
| D | Candidates placed into decision windows and chunks, related clusters found, and ≤12 umbrella parents proposed | `d_candidates.py`, `d_umbrellas.py` → `out/d_results.json`, `out/umbrellas.json`; `make_pages.py` → `out/umbrellas.html` |
| E | Mentions vs sessions vs occasions (1h/1d/1w), and duplicate-transcript inflation, for every concept in the real graph and in run1's store | `e_counts.py` → `out/e_counts.json` |
| Scoring | Ready to run once the labels exist | `score.py` |

## Who labelled the data

Freddie asked Claude to label both sets, since it's a stronger model than the one the product runs. So `out/labels.json` and `out/umbrella_ratings.json` were written by **Claude Opus 5.5**, not by a person.

**It was done blind.** The pairs were labelled from `out/pairs.json` alone, before `pairs_key.json`, `model_verdicts.json`, the D caches or the rest of this README were opened. The 7 pairs Claude was unsure of are flagged `unsure`, with a note on each.

**The caveat that matters: a model labeller may share biases with the models it scores.** The strata key was also written by a Claude instance, so the 63/66 agreement between labels and intended strata is weak evidence. A handful of human labels on the borderline pairs (the `unsure` ones, and the "same, nothing extra to learn" class) would be the cheapest way to check the result.

The two pages still work if Freddie wants to add labels of their own:
- `out/label.html` exports `labels.json`.
- `out/umbrellas.html` exports `umbrella_ratings.json`.

Save human exports under different names, so both sets can be scored.

## How to run

From this folder, with `OPENROUTER_API_KEY` in the repo-root `.env`:

```bash
uv sync
uv run python build_pairs.py      # A: out/pairs.json, out/pairs_key.json (no model)
uv run python run_c.py            # C: 792 calls, ~9.5 min, ~$0.06 → out/model_verdicts.json
uv run python run_c.py --summary  # cost/latency/stability only
uv run python d_candidates.py     # D1: place candidates into windows/chunks (no model)
uv run python d_umbrellas.py --verify-setting ling-off --workers 16   # D2: ~$0.25, ~21 min; resumable from out/d_proposals.json + out/d_pair_cache.json
uv run python make_pages.py       # out/label.html, out/umbrellas.html
uv run python e_counts.py         # E (no model)
uv run python score.py            # after out/labels.json exists
```

The embedding baseline uses fastembed models that PR-34 already cached. Set `HF_HUB_OFFLINE=1` to be sure nothing is downloaded.

**`out/` is gitignored.** It holds session-derived terms, the answer key and model verdicts. No file here, committed or not, quotes transcript text: only terms, counts and ids.

## A. The pair set

There are 66 pairs, built from:

- run1's detector terms against the real graph's concept names;
- PR-38's 51-concept fixture and its probes;
- hand-added hard cases, including the ticket's own examples.

Each term's source is looked up, not asserted. Across the 132 term slots, the matches were: PR-38 fixture 55, run1 detector 46, run1 store 44, real graph 30, PR-38 probe 7, and hand-only 30. A term can count under more than one source.

| Intended stratum | Pairs | Of which hard |
|---|---|---|
| spelling / word form | 9 | 0 |
| abbreviation | 8 | 0 |
| no-extension variant | 9 | 1 |
| narrower / broader | 10 | 3 |
| prerequisite | 8 | 0 |
| contrasting, same space | 10 | 0 |
| co-occurring in one real session, unrelated | 10 | 3 |
| synonym (a different name, not a surface variant), extra | 2 | 2 |

How the set was put together:

- The order is shuffled with seed 50.
- A and B are swapped at random within each pair. The key stores the intended label for the orientation actually shown.
- Pair ids follow display order, so they reveal nothing.
- "Hard" marks pairs I think could reasonably be labelled two ways. Two of them are cases where PR-38's fixture or probes already treat a pair as a match that I think is really an extension. Which pairs these are is in the key, and is left out here so this README can be read before labelling.

**The strata are my intention, not ground truth.** The point of B is to find out where I'm wrong.

## C. Model classification: cost, latency, stability

One prompt per pair asks for three booleans:

- `a_explains_b`: could someone who can answer the check question about A also answer it about B, without learning anything new?
- `b_explains_a`: the same, the other way round;
- `same_space`: would A and B be taught in the same chapter?

The verdict follows the ticket's mapping:

- both directions yes → identity;
- one direction yes → extension;
- neither, and `same_space` yes → related;
- otherwise → unrelated.

The prompt is `TRANSFER_PROMPT` in `common.py`. Its one example, strong vs eventual consistency, is deliberately not in the pair set. Jev gets the same three questions as `Choice`s and returns probabilities. A probability of 0.5 or more counts as yes, and the raw probabilities are kept.

Each setting was run over all 66 pairs, 3 times on identical input and once with A and B swapped. **No verdicts are reported here**, so the labelling stays blind.

| Setting | Calls | Cost | p50 / p90 / max latency | All 3 repeats agree on the verdict | Per field (a→b, b→a, same space) | Swapped order agrees with majority |
|---|---|---|---|---|---|---|
| ling-3.0-flash, reasoning **low** | 264 | $0.046 | 17.6 s / 41.8 s / 95 s | 54/66 | 60, 59, 64 | 52/66 |
| ling-3.0-flash, reasoning **off** | 264 | $0.005 | 1.7 s / 4.1 s / 7.8 s | **63/66** | 66, 65, 63 | **46/66** |
| Jev (`typesafe/jev-1.13`) | 264 | $0.007 | 0.29 s / 0.37 s / 0.8 s | **63/66** | 65, 64, 66 | **59/66** |

- **Total: 792 calls, $0.058, 9.4 minutes wall-clock at 12 in flight. There were no errors or unparsed answers.**
- ling-low reasoned for 716 tokens at p50 (5,079 max) on a question about two bare terms. None of its calls needed the forced retry.
- **Two kinds of stability disagree.**
  - ling-off is the most repeatable (63/66) but the most order-sensitive: 20 of 66 pairs change verdict when A and B swap places.
  - ling-low is less repeatable (54/66) and less order-sensitive (52/66).
  - Jev is both repeatable (63/66; the mean spread of p(yes) across repeats was 0.014, max 0.10) and the least order-sensitive (59/66).
  - For the product, order sensitivity matters more: the resolver always puts the new term first. Score both orders against the labels before choosing.
- Baselines were computed and saved but not reported:
  - PR-38's shortlist, both directions, over a background of the fixture plus the real graph. It counts as a match when the partner ranks first with a non-zero score.
  - bge-base and MiniLM cosine on bare terms. Their thresholds get chosen against the labels.

## D. How often would an umbrella be on offer?

An **umbrella opportunity** is a unit that holds 3 or more candidates which are distinct but related. The unit is one of:

- a decision window: a `group_windows()` group, meaning assistant turns that share one human reply;
- a chunk: `chunk_windows()`, what one detector call sees;
- a whole session.

Relatedness comes from the C classifier, run with ling reasoning **off**, which had the most repeatable verdicts. Each pair is sorted alphabetically before it is asked, so the same pair always gets the same order.

- A pair judged related, or extension in either direction, is an edge.
- Identity merges the two candidates into one.
- Unrelated gives no edge.

A unit counts ("connected") if 3 or more distinct concepts remain connected. It counts as a "clique" if three of them are pairwise related.

Run1 has 36 sessions, 1,260 windows, **240 decision windows** and 39 chunks. In agentic sessions, one human reply often follows many assistant turns.

| Candidate source | Unit | Units with 3+ candidates | Umbrella opportunity (connected) | Clique | Sessions with an opportunity |
|---|---|---|---|---|---|
| **Detector, main pass** (what PR-49 would actually see) | decision window | **0 of 240** | **0** | 0 | **0 of 36** |
| | chunk | 0 of 39 | 0 | 0 | 0 |
| | session | 2 of 36 | 0 | 0 | 0 |
| Detector + the 3 repeats run1 made on 6 sessions | session | 5 of 36 | 3 | 2 | 3 (one is the synthetic `sess-messy`) |
| **Wide net** (PR-42 GLiNER ∪ noun_chunks, code-filtered) | decision window | 239 of 240 (median 46 candidates) | **220 of 240 (92%)** | 216 | **31 of 36** |
| | chunk (derived from its windows; a lower bound) | – | 36 of 39 | – | – |

Findings:

1. **With today's detector the umbrella rule can never fire in a decision window.**
   - The detector returns at most 2 terms per chunk, so no window or chunk ever holds 3.
   - Even pooled over a whole session, the main pass gives 2 sessions with 3+ terms, and neither has 3 related ones. For example, `13d5c0d2` has an accessibility setting, a git term, a design-language name and a local label.
   - Only after adding the repeat runs do 3 sessions show a related triple. One is an accessibility trio, one is signing (`ad-hoc signing`, `Developer ID`, `EdDSA`), and the third is the synthetic messaging trio.
   - **Decision 3, as written, is a no-op unless the per-chunk budget grows or candidates are pooled beyond a window.**
2. **With a wide net it fires almost everywhere, which makes it meaningless as a trigger.**
   - 92% of decision windows, and 31 of 36 sessions, have a "verified" cluster.
   - The clusters are mostly generic or project vocabulary, not learnable umbrellas. From the proposals shown on `umbrellas.html`: `folder / root / place / Structure` → "tree", `good tags / bad tags / suffix` → "versioning", `grade / grader / regrade guard` → "grading".
   - "3+ related candidates" is only a useful gate if the candidates are already gap-quality. The count carries no signal on its own.
3. **The transfer test is a weak relatedness filter.**
   - The ling-off verifier kept 765 of 871 proposed groups (88%).
   - Across the 8,441 pairs it judged, the split was: related 5,137; unrelated 5,015; extension 2,144; identity 419; unanswered 2. `same_space` is loose: "would they be taught in the same chapter" says yes to most things from one conversation.
   - For umbrellas, the test tells distinct from same, but it doesn't tell "worth grouping" from "happened together".
4. **Parents for the size-limit check (decision 4).**
   - 12 parents were proposed:
     - 3 from the detector+repeats clusters;
     - 9 from the wide net, one per session, in session-id order.
   - The model's check answers run to about 230–360 characters, 2–4 sentences. That is longer than the "at most two lines" asked for.
   - The ratings from `umbrellas.html` are the evidence for decision 4 (see Results).

**The proposal step had failures, and this is how they were handled:**

- **The first pass (prompt `p1`, reasoning off, kept in `out/d_proposals.firstpass.json`) failed on 59 of 278 units (21%).**
  - 46 hit the 4,000-token output ceiling of the reasoning-off client. The model looped while listing groups, spending about 83 s each; it put up to 117 terms in one "group".
  - 13 were connection errors.
  - Failures didn't depend on list size: the median list was 40 terms for failed calls against 44 for successful ones.
  - p1 also made verification explode to 69,825 pairs. **That is what the earlier ~15-hour "hang" was:** tens of thousands of pair calls, with no progress output and no per-call deadline.
- **The fix, prompt `p2`:**
  - groups are limited to 3–8 terms, and anything longer is cut to 8;
  - a failed call is retried once with reasoning low;
  - every call has a 240 s wall-clock deadline;
  - progress is cached so a run resumes;
  - chunks are no longer proposed over separately, and are derived from their windows.
- **Result:** 23 of 239 window units needed the fallback, and **3 of 239 still failed**. Those 3 count as "no opportunity", which can lower the 220/240 rate by at most 3 windows (1.3 points). **That doesn't change the conclusion.**
- **Costs:** proposals $0.055, pair verification $0.188 (8,441 calls), parent naming $0.003. The final pass took 21 minutes.
- **All of D, including the abandoned p1 passes, cost about $0.41.** The pair cache holds 12,717 judged pairs worth $0.28. The spike's total, with C, is about $0.47.

## E. Counting units

This covers every concept in the real graph (37 now, all read-only) and in run1's store (51). **Mentions** are windows across all 36 captured sessions (1,292 windows, 2026-08-20 to 2026-09-27) whose assistant prose or human reply names the concept. A concept's names are its canonical name, its aliases and the parts of any parenthetical, for example `MVCC` and `Multi-Version Concurrency Control`. There are two matchers:

- **exact**: case-insensitive and word-bounded;
- **stem**: PR-38's stemmer applied to both sides.

**Occasions** cluster mention times (the transcript's own timestamps). A gap bigger than 1h, 1d or 1w starts a new occasion.

| | Real graph (37) | run1 store (51) |
|---|---|---|
| Persistent by **encounters** (encounters in 2 or more sessions) | **2** | 4 |
| Persistent by **mentions**, exact (2 or more sessions) | 13 | 17 |
| …the same after removing duplicated windows | 10 | 16 |
| …stem matcher | 14 | 20 |
| Never mentioned at all (exact / stem) | 13 / 11 | 19 / 18 |
| Stem matching finds more windows than exact | 4 | 9 |
| 5 or more mentions but only 1 session | 2 | 2 |
| 2 or more sessions but 1 occasion at 1h / 1d / 1w | 2 / 3 / **13 of 13** | 1 / 2 / **12 of 17** |
| More 1h-occasions than sessions (one long session counts as several occasions) | 2 | 9 |
| Encounter sessions fewer than mention sessions | 14 | 17 |

Findings:

1. **Encounters barely persist yet.**
   - Only 2 of the real graph's 37 concepts have encounters in 2 or more sessions. `Git Rebase` gets there only via a manual entry. `macOS notarization` gets there via two detector encounters. It also carries the alias `notarisation`.
   - Everything else has one encounter. On today's data, "persistence is the strongest signal" can almost never fire from encounters alone.
2. **Mentions disagree with encounters, and mostly in the wrong direction for a gap signal.**
   - The concepts mentioned in the most sessions are project vocabulary, not gaps: `comprehension check` in 8 sessions, `Walking Skeleton` in 6, `frozen core` in 6 and `Backpressure` in 5. Those words recur because the project uses them.
   - Mention-persistence therefore rewards local labels, the same failure PR-42 found in the detector.
   - Mentions also over-count within a session. `Git Rebase` has 11 mentions, all in one session.
3. **Names hide mentions.**
   - 13 of 37 real concepts are never mentioned verbatim anywhere:
     - 4 are the `referenced` scaffolding with snake_case names (`merge_vs_linear`);
     - 3 come from the current session, which run1 captured only in part;
     - 2 are resolver-made names (`personal access token vs service key`, `triage hold-back`);
     - 2 carry qualifiers the text never uses: `macOS TCC (…)` where the text says `TCC`, `Git Worktree` where it says `worktree`;
     - 2 (`soak testing`, `auto-instrumentation`) are not found in their own encounter's window by either matcher. I didn't chase why; the likely causes are a different word form, or the term sitting in fenced code, which is stripped.
   - Word forms matter as much. `macOS notarization` is found in **1 window by exact match and 20 windows in 6 sessions by stem**. `idempotency` is 9 windows in 3 sessions exact, and 47 in 8 by stem.
   - **Any count keyed on the canonical string undercounts badly.** Counting has to go through identity resolution (aliases, stems), which is exactly what the transfer test is meant to decide.
4. **Duplicated transcripts inflate every count.**
   - **138 of 1,292 windows (10.7%) are byte-identical to a window in another session.** The pairs, with their duplicated windows:

     | Session pair | Duplicated windows |
     |---|---|
     | `3830fa9c`~`f409f1f5` | 53 |
     | `3830fa9c`~`d2930d59` | 42 |
     | `e1e8432a`~`f9aa8fa8` | 29 |
     | `75704410`~`d2930d59` | 13 |
     | `3830fa9c`~`75704410` | 1 |

   - Each pair starts at the same minute. They look like forked or resumed sessions that copy their parent's history into a new file.
   - Deduplication changes the mention-session count for 9 of 37 real concepts:
     - `NSEvent` drops from 4 sessions to 1;
     - `Unix Domain Socket` from 2 to 1;
     - `comprehension check` from 8 to 4;
     - `Backpressure` from 5 to 3.
   - Of the 13 mention-persistent real concepts, 3 are persistent **only** because of duplicates. The counting unit must dedupe on content, not session id. The detector has the same problem (PR-41 finding 7).
5. **The time thresholds.**
   - **1h** is finer than a session. Long sessions yield several "occasions" (9 run1 concepts have more 1h-occasions than sessions).
   - **1d** is close to sessions: of the concepts in 2 or more sessions, 3 real and 2 run1 collapse to one occasion.
   - **1w** collapses nearly everything. 13 of 13 real mention-persistent concepts and 12 of 17 run1 ones are one occasion.
   - The corpus covers 5.5 weeks, but most multi-session concepts fall inside one burst of work. **"Spread across months" cannot be measured on this data at all.**

## What was verified, and what was assumed

**Verified (on this data, these settings):**
- Every number in the C, D and E tables above, from the scripts and files named. C's stability figures are exact counts over 66 pairs × 3 repeats.
- The real graph and run1's stores were only opened with `mode=ro` URIs.
- `label.html` was exercised in the browser pane:
  - keys 1–6 select and advance;
  - U toggles unsure;
  - progress and the grid update;
  - the export payload has all 66 ids.

  The pane serves a `data:` URL, where localStorage is blocked, and the page correctly showed its "can't save" notice. **Saving across reloads in a real browser on `file://` was not verified.** It is standard behaviour in Safari and Chrome, but if it fails, export before closing.
- `score.py` runs end to end against randomly generated fake labels in a scratch folder (not in `out/`).

**Assumed, or not established:**
- **The pair strata are my judgement.** B exists to check them.
- **C asks both directions in one prompt.** The ticket says "asked both ways". Separate calls per direction were not tried. They might reduce the order sensitivity, or might not.
- **Terms are shown bare**, with no gloss. The product would have paraphrases, and PR-34 found bare terms weak for Jev on area. The results here are a lower bound on what context adds, in either direction.
- **D's wide-net numbers depend on the proposal step, whose recall is unmeasured.** A window with no proposed group counts as having no opportunity, so 92% is a lower bound for "what a model would call related". Verification only prunes. Groups cut to 8 terms, and chunks derived from windows, are lower bounds too. The detector numbers need no proposal step: every pair was classified.
- The detector units come from run1's single main pass, plus the repeats that exist for only 6 sessions. The 4 sessions that failed in run1 with `database is locked` still have their detection returns and are included.
- D's verifier is ling with reasoning off, chosen because it was the most repeatable in C. It is also the most order-sensitive, so D asks every pair in a fixed alphabetical order. Whether its verdicts are *right* waits on the labels.
- E's mention matcher is a string matcher. It finds names, not concepts, so "never mentioned" partly means "named differently". The window corpus is run1's capture of `~/.claude/projects` as of 2026-09-27, including 2 synthetic sessions and 2 `agent-*` subagent transcripts. The current session is captured only up to the e2e run.
- Duplicate detection is exact assistant-text equality. Near-duplicates are not counted.

## Results vs labels

Scores are against the Claude labels above (`uv run python score.py`). There are 66 pairs, 28 of them identity: "same" (22) or "same, nothing extra to learn" (6).

### Per setting

Each setting's answer is the majority of 3 repeats.

| Setting | 5-way | 3-way (identity / extension / separate) | Wrong merges | Missed merges |
|---|---|---|---|---|
| ling, reasoning low | 49/66 | 52/66 | 0 | 6 |
| ling, reasoning low, pair swapped | 51/66 | 55/66 | **2** | 4 |
| ling, reasoning off | 48/66 | 54/66 | 1 | 4 |
| ling, reasoning off, pair swapped | 49/66 | 54/66 | **3** | 3 |
| Jev | 44/66 | 44/66 | 0 | 8 |
| Jev, pair swapped | 45/66 | 45/66 | 0 | 8 |
| PR-38 matcher's top match as "same" | – | – | **10** | 4 |

A **wrong merge** means the model says identity and the label doesn't: an encounter gets filed under a concept the person never met, and nothing on the surface shows it. That is the resolver's unnoticeable error. A missed merge only costs a duplicate that can be merged later.

### Where each kind of error falls

**Every wrong merge was a general term paired with a narrower one.** Examples: copyleft / GPL, Model Context Protocol / MCP servers, idempotency / idempotency key, write-ahead logging / SQLite WAL mode, and the PAT-vs-service-key comparison / PAT. The swapped order produced most of them: ling-low had 0 wrong merges in the original order and 2 swapped.

**Missed merges cluster in the "same, nothing extra to learn" class.** Examples:
- Developer ID / Developer ID signing (missed by every setting);
- TCC-protected / TCC;
- Docker named volume / named volume;
- Sparkle appcast / appcast;
- Walking Skeleton / tracer bullet.

Models read "more specific-sounding" as "extends". One oddity: `TCC` / `macOS TCC (Transparency, Consent, and Control)` came back **unrelated** from ling-low and Jev. The canonical name's parenthetical expansion confuses them.

**Direction of extension is unreliable.**

| Setting | narrower/broader | prerequisite |
|---|---|---|
| ling | 2–5/10 | 2–6/8 |
| Jev | 3–4/10 | 2/8 |

Models often answer "related" instead, or pick the wrong direction.

### Merge rules that require agreement

| Rule | Right merges | Wrong merges |
|---|---|---|
| ling-low, identity in both orders | 21/28 | 0 |
| ling-off, identity in both orders | 23/28 | 0 |
| Jev, identity in either order | 22/28 | 0 |
| **ling-off in both orders, OR Jev in either order** | **24/28** | **0** |

### Baselines

**String similarity can't decide identity.** The PR-38 matcher's top match would wrongly merge 10 pairs, including optimistic / pessimistic locking, Git Merge / Git Rebase, and at-least-once / exactly-once delivery. That's fine for PR-38, which only builds a shortlist for the model, but it could never be the decider.

**Embeddings can't separate same from contrasting.**

| Model | identity vs rest | identity vs related-but-different | related vs unrelated |
|---|---|---|---|
| bge-base | 0.78 | 0.68 | 0.83 |
| MiniLM | 0.77 | 0.66 | 0.89 |

The weak middle column confirms the 2026-09-16 decision.

### Umbrella ratings (12 proposals)

| Rating | Count | Umbrellas |
|---|---|---|
| Right-sized | 2 | reliable messaging (outbox, at-least-once, idempotent), from a detector cluster; merge (merge commit, squash-merge), from the wide net |
| Too broad | 2 | Apple Accessibility; version control |
| Not a unit | 8 | The rest |

The "not a unit" umbrellas:
- **Digital signatures** mixes an algorithm (EdDSA) with Apple signing identities.
- **The others** group generic or project words: container, tree, versioning, GCD, repository, process management, grading.

By source, 1 of 3 detector-cluster umbrellas was right-sized, and 1 of 9 from the wide net.

## Recommendations for PR-49 decisions 1–5

### 1. Use the transfer test for identity, not for edges

- **The test separates identity from everything else reliably enough to act on, if agreement is required.** Recommended resolver rule: after PR-38's shortlist, file as the same concept only when **ling (reasoning off) says identity in both pair orders, or Jev says identity**. That gives 24/28 right merges and 0 wrong ones, for about 2 s and well under a cent per pair.
- **Otherwise file as new.** This keeps the existing "prefer new when unsure" rule; the missed merges are exactly the fuzzy ones.
- **Don't store directional edges from it yet.** Getting 2–6 of 8 prerequisites right isn't good enough, and a wrong prerequisite edge misroutes learning (2026-09-16). At most, record "related (unverified)".
- **Strip parenthetical expansions from canonical names** before asking. The `TCC` / `macOS TCC (…)` failure shows why.

### 2. Count occasions, not mentions

E's findings stand, and nothing in the labels argues against them:
- dedupe byte-identical windows before counting (10.7% of windows are duplicated; this raises PR-46's priority);
- count through identity resolution, not canonical strings;
- use sessions, or 1-day occasions, not 1-hour ones;
- don't treat mention persistence as a gap signal, because the most persistent mentions are project vocabulary.

Real persistence is currently rare: 2 of 37 real concepts appear in more than one session. The spread-across-months signal can't be measured on 5.5 weeks of data.

### 3. Umbrellas: don't build yet

- **With today's detector the rule never fires.** It emits at most 2 terms per chunk, so no window ever holds 3.
- **With a wide candidate net it fires almost everywhere** (92% of windows), and 8 of 12 proposals weren't a unit.
- **The good cases were real but rare.** They were learnable trios the detector *had* found, such as reliable messaging.

Revisit if the detector budget changes (PR-45) or when a real session produces a clear cluster. When that happens, the trigger should be "3 or more **detected gaps** judged related", never a wide candidate net.

### 4. "A line or two" works as the size test

- It rejected the too-broad parents (Accessibility, version control) and the generic ones.
- The two right-sized umbrellas had two-line causal check answers.
- Generated check answers ran 2–4 sentences. Enforcing the limit when generating is itself a useful filter.

### 5. The relation set

- **"Same, nothing extra to learn" is where models and labeller disagree.** That makes it the class to keep as an explicit label, and the one to show the person ("we treated these as one: OK?") rather than decide silently.
- **The unsure pairs** (appcast, hash ring / consistent hashing, soak vs load testing, Walking Skeleton / tracer bullet, OCC / optimistic locking, PAT comparison, hardened runtime vs notarization) are genuinely borderline. These should also be asked, not auto-merged.
- **Alias kinds are worth recording.** Spelling and abbreviation merges were near-perfect for every model; the no-extension class was not.

## Files

| File | What it is |
|---|---|
| `common.py` | Paths, read-only store connections, PR-38 matcher import by path, the transfer prompt, and the ling and Jev callers (via `unrot.model.structured_client` and `decision_client`) |
| `build_pairs.py` | A: the pair list, source lookup, shuffling and orientation, key |
| `make_pages.py` | Both rating pages from one template |
| `run_c.py` | C: model runs, PR-38 and embedding baselines, the stability summary |
| `d_candidates.py` | D1: detector and PR-42 candidates placed into windows and chunks |
| `d_umbrellas.py` | D2: pairwise verification, wide-net proposals, rates, parent naming |
| `e_counts.py` | E: counting units and duplicate inflation |
| `score.py` | Scoring against labels and umbrella ratings |
| `pyproject.toml`, `uv.lock` | The uv project. The repo is a non-editable path dependency. |
| `out/` (gitignored) | The main outputs: `pairs.json`, `pairs_key.json`, `model_verdicts.json`, `label.html`, `umbrellas.json`, `umbrellas.html` and `e_counts.json`. D's working files: `d_candidates.json`, `d_proposals.json` (p2 cache), `d_proposals.firstpass.json` (the failed p1 pass, kept as evidence), `d_pair_cache.json` and `d_results.json`. Also the logs. `labels.json` and `umbrella_ratings.json` come later. |

## Privacy: what left the machine

Everything sent went to OpenRouter:

- C sent the 66 pairs of bare terms.
- D sent bare-term lists per decision window and chunk (from PR-42's extractor output), bare pairs of those terms, and bare child lists for parent naming.

No sentence, paraphrase or window text was sent. Some of these terms are project vocabulary, just as in PR-42's Wikipedia lookups. Nothing was sent anywhere else. The embedding models were already cached.
