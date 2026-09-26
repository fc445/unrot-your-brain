# Handover: detector rethink and knowledge-map spike (2026-09-26)

Written at the end of a cloud session so the work can continue in a local one.
Nothing in `src/` was changed. This file is the only thing committed.

**Where it stopped:** about to build a spike that tests "given what a person
already knows, would they know this new concept?". The spike has not been
created yet. The plan, the data design and the environment findings are below.

---

## 1. How the detector works today

This is the baseline being replaced. Code is in `src/unrot/detector/`.

- **Input.** `build_windows` pairs each assistant turn (≥ 40 chars) with the
  next turn a human actually typed (`is_meta = 0`, `is_sidechain = 0`).
  `group_windows` merges consecutive assistant turns that share one reply.
  `chunk_windows` packs whole groups into chunks of ≤ 40,000 chars.
- **One LLM call per chunk, run one after another** (a `for` loop in `detect()`).
  The prompt (`prompt.py`, `p2`) asks one call to do five jobs: find
  load-bearing unexplained terms, classify the human's reply
  (`accepted | questioned | unclear`), rate `central | supporting`, write a
  standalone paraphrase, and give line numbers. It returns at most 2 per call.
- **Output** is a structured `Proposal`. `_coerce` drops `questioned`, invalid
  signals and unknown lines. Candidates are deduped by term, ranked, and only
  `accepted` ones are emitted, top 2 per session.
- Default model `inclusionai/ling-3.0-flash`, reasoning effort "low". Reasoning
  runaways are handled by a forced retry in `unrot/model.py`.

A diagram of the current pipeline:
https://claude.ai/artifact/2LpArkGeThonwzxZRnVe3X

## 2. Decisions made in this session

These have not been added to `docs/decisions.md` yet. Add them if they hold.

1. **Layer 0 does extraction only.** The model returns *concept + which
   message*, and nothing else. The signal, importance and paraphrase move to
   later layers.
2. **The pointer is the message** (`raw_turns.line_no` of the assistant turn),
   not a span or a character offset. Exact-string matching was considered and
   rejected because hyphens and paraphrasing break it.
3. **Human turns leave the extraction input.** Only the acceptance layer needs
   them, and the window pairing already exists in code.
4. **Switch the detector to a model with minimal or no reasoning.** Freddie
   will do this manually. Reasoning was the main cause of slowness and
   runaways.
5. **Later layers label concepts, they don't delete them.** Each concept
   carries a reason (known, too broad, re-encounter, …). That keeps the history
   re-runnable (S5), gives the PRD's "why these won" visibility, and turns
   dismissals into tuning data.
6. **Embedding "knowledge map": links between concepts are not needed.** The
   map is concepts embedded and coloured by state (`known` / `gap` /
   `referenced`). Domains (software engineering vs cooking) should emerge from
   clustering.
7. **Use Jev for the familiarity judgement.** Jev is the System One classifier
   already used in `src/unrot/grader/jev.py` (`typesafe/jev-1.13`, called at
   OpenRouter `/v1/systemone`, returns probabilities and a confidence). The
   question: "here's a new concept, here's what they know, do they already know
   it?". This is meant to fix the case that proximity alone gets wrong:
   "Postgres" sits right next to "MVCC" in embedding space, but knowing one
   doesn't imply the other.
8. **Bare terms are a known risk.** They're ambiguous, and a person can know a
   *name* without knowing what it is ("Postgres"). Embed the term plus a
   one-line gloss, and keep "knows the name" separate from "understands it".

## 3. The proposed funnel

These are hypotheses, and nothing is built. Cheap layers run first, and each
one labels.

| # | Layer | How | Cost |
|---|---|---|---|
| 0 | Extract concept + message | LLM, no reasoning, chunks run in parallel | 1 call per chunk |
| 1 | Graph match (known / existing gap / referenced) | Resolver's `exact` + alias match | Free |
| 2 | "Have you used this term yourself?" | Search your own human turns in `raw_turns` | Free |
| 3 | Term properties: too broad / too narrow / basic, commonness tier | Tiny LLM call on the term alone, cached per term forever | Trends to 0 |
| 4 | Familiarity for *this* person | Embedding map + Jev, per-domain threshold learned from dismissals | Cheap |
| 5 | Context: load-bearing? explained inline? accepted? | Today's judgement, run only on survivors | Few calls |
| 6 | Budget: top 1–2, paraphrase the winners only | Rank | 1–2 calls |

**Architecture catch:** the detector package must not read the store. Layers 1,
2 and 4 need the graph, so they belong in a new **triage** stage between the
detector and the resolver, not inside the detector.

**Still open:**
- Should an existing gap that is accepted again stay silent, or resurface after
  N days?
- Should layer 2 search this session only, or all history?
- Is one commonness scale enough, or does it need one per domain?

## 4. Next step: the knowledge-map spike

**Question:** can we predict whether a person knows a new concept from the
concepts we already know they know or don't know? How do embedding proximity
and Jev compare, especially on the Postgres-vs-MVCC case?

**Ticket:** none exists yet. The closest is
[PR-13](https://linear.app/freddie-cassidy/issue/PR-13) (zero-shot vs
semantic-similarity gap detection), but PR-13 is about explanation coverage,
not familiarity. Probably create a new ticket, relate it to PR-13, and name the
folder `spikes/<date>-PR-<n>-knowledge-map-familiarity/` per `CLAUDE.md`.

### Data (constructed; no real labels exist yet)

- **Concepts:** about 150 software-engineering concepts plus about 20 cooking
  concepts, each with:
  - `term` and a one-line `gloss`
  - `area` (databases, networking, frontend, infra, ML, …)
  - `depth` 1–4 within that area (1 = "Postgres", 3 = "MVCC", 4 = "SSI")
- **Personas:** about 5, each a map of `area → depth known`, plus universal
  basics. For example: backend engineer, frontend engineer, bootcamp junior,
  ML engineer, and a hobbyist cook with light software exposure (the
  cross-domain case).
- **Labels are derived by rule:** known if `depth ≤ persona[area]`. State
  plainly in the README that these labels are synthetic, and that the rule
  bakes in exactly the area-vs-depth structure being tested.
- **Split:** for each persona, the model sees a random 30–50% of concepts with
  labels (their "map") and predicts the rest. Repeat over several seeds.

### Methods to compare

1. **Commonness prior:** the `wordfreq` Zipf score of the term, with no
   personalisation.
2. **Embedding kNN:** similarity-weighted vote of the k nearest labelled
   concepts, once with the bare term and once with term + gloss.
3. **Jev:** `state` = known list + unknown list + the new concept with its
   gloss; one `choice` question with options `knows | does not know`. Store
   the probabilities.
4. *(Optional)* a no-reasoning chat LLM given the same prompt, for comparison.
5. *(Optional)* kNN + prior combined.

### What to measure

- ROC AUC and accuracy for each method.
- **Broken down by case:** a different area (should be easy), the same area but
  deeper (the Postgres→MVCC case, hard), and the same area but shallower.
- Latency and cost per prediction.
- A 2D plot (UMAP or PCA) of the map, coloured known/unknown per persona, to
  eyeball whether the regions separate.

**Expected result:** embeddings will capture *area* well and *depth* poorly.
The spike exists to measure how poorly, and whether Jev closes that gap.

## 5. Environment findings from the cloud session

- **Blocked in the cloud container:** `openrouter.ai` and `huggingface.co`. The
  environment's network policy refused the connection (proxy 403 on CONNECT).
  There was also no `OPENROUTER_API_KEY` in the environment. Neither problem
  applies locally, where `.env` provides the key via `ModelConfig.from_env()`.
- **Embeddings that ran with no HuggingFace:** `fastembed` models whose ONNX
  tarballs are on Google Cloud Storage. Download
  `https://storage.googleapis.com/qdrant-fastembed/<name>.tar.gz`, extract it
  into a cache dir, then use
  `TextEmbedding(model, cache_dir=..., local_files_only=True)`. Locally the
  normal HuggingFace download works, so none of this is needed.

  | Model | Dim | Load + 4 embeddings | cos(Postgres, ·): WAL / MVCC / sourdough |
  |---|---|---|---|
  | all-MiniLM-L6-v2 | 384 | 0.2 s | 0.13 / 0.12 / 0.15 |
  | bge-base-en-v1.5 | 768 | 1.0 s | 0.54 / 0.48 / 0.45 |
  | multilingual-e5-large | 1024 | 8.3 s | 0.83 / 0.77 / 0.79 |

  From one sample of 4 terms: **bare-term similarities barely separate
  "sourdough starter" from database concepts** for any of the three models (e5
  squeezes everything into 0.77–0.83). This is early evidence for the
  term + gloss decision. Re-check it on the real dataset, and prefer ranking
  over absolute thresholds.
- Libraries used in the throwaway venv: `fastembed`, `wordfreq`,
  `scikit-learn`, `numpy`.

## 6. Related work

- Knowledge Space Theory / ALEKS, the "outer fringe" (what a learner is ready
  to learn next):
  https://www.aleks.com/about_aleks/knowledge_space_theory
- Cognitive diagnosis on untested knowledge concepts:
  https://arxiv.org/html/2405.16003v2
- Personalised word complexity (personal models beat general ones):
  https://aclanthology.org/2022.findings-naacl.27/
- Knowledge tracing with concept embeddings or graphs:
  https://arxiv.org/pdf/2211.12881
- Open learner models (Bull & Kay): cited from memory and not checked.
