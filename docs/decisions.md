# unrot-your-brain — decisions.md
(append-only; reconstructed from the existing design doc as of 2026-09-12)

- **2026-09-01 — Rejected network-proxy/gateway capture.** Claude Desktop's third-party gateway mode reroutes traffic rather than tapping it, forcing subscription users onto pay-as-you-go API billing. Kills the "attach without changing your workflow" premise.
- **2026-09-01 — Chose local JSONL transcript parsing as the capture layer.** Claude Code already writes full session transcripts to `~/.claude/projects/<project>/<session-id>.jsonl`, working identically across CLI/VS Code/JetBrains/Desktop and under both subscription and API billing.
- **2026-09-01 — Chose hook-triggered analysis over filesystem polling.** Claude Code's hook system fires on session events and passes the transcript path directly.
- **2026-09-01 — Chose behavioral signal as the primary detection method.** Unexplained term + immediate blind acceptance = flagged gap; a clarifying question = not a gap. Preferred over self-report, which is unreliable (people confirm familiarity with things they don't know).
- **2026-09-01 — Chose "what is it in one line?" as the secondary confirmation check**, not yes/no self-assessment — functions as a real check rather than a survey.
- **2026-09-01 — Set flag budget to ~1–2 surfaced gaps per session.** Precision over recall; this is what determines whether people keep using it past day three.
- **2026-09-01 — Deferred the prerequisite graph and curriculum engine.** Needs real gap data to design against; not needed for v1.
- **2026-09-01 — Chose MCP server + `/unrot` slash command as the delivery surface.** Pull-based, no injected text, no interruption — over a dashboard or digest.

---

## 2026-09-16 — PRD drafting session

### Positioning and scope

- **2026-09-16 — Positioned against knowledge gaps, not AI fluency.** Existing OSS tools (claude-insight, skill-tree, claude-session-analyzer) and Anthropic's own scorecard all measure *collaboration style* — how well someone delegates, describes, iterates. unrot targets knowledge gaps in daily workflow that AI silently assumes the user already has. Different axis; describe it that way to avoid reading as a weaker clone of the fluency category.
- **2026-09-16 — REVERSED the MCP + `/unrot` delivery decision (2026-09-01).** unrot is a **silent, capture-only** integration to Claude Code with no in-agent interaction of any kind. All interaction happens in a separate product surface with its own UI and backend. The agent is a data source, not an interface.
- **2026-09-16 — Chose v1 = walking skeleton over detector-first.** A thin slice through every layer (capture → detect → store → manual input → surface → learn), deliberately crude but end-to-end runnable. "Is this loop worth having" is as risky an unknown as "does the detector work," and only an end-to-end version tests both. **Supersedes `brief.md`'s "smallest version = analyzer plus validation only."**
- **2026-09-16 — Reduced hand-labelling from ~10 sessions to 2–3.** Kept as a sanity check specifically so a disappointing week of self-use can be diagnosed as bad detection vs. bad concept.
- **2026-09-16 — Chose zero-shot detection for v1.** Semantic-similarity is spike-worthy but non-blocking (PR-13).
- **2026-09-16 — Chose not to over-formalise module interfaces.** Four modules (parser / detector / store / delivery), one person; a shared dataclass shape is sufficient.

### Structural decisions (S1–S7)

- **2026-09-16 — Established S1–S7 as blocking structural decisions.** No schema and no code until resolved. Each is cheap now and expensive-to-impossible to retrofit once real data accumulates.
- **2026-09-16 — S1: split `concept` from `encounter`.** Concepts are compiled state; encounters are events. One row per concept loses history; two independent rows lose the repetition signal — and repetition across months is plausibly the most valuable signal in the product.
- **2026-09-16 — S5: the store is derived and re-runnable, not a write-once source of truth.** Improving the detector should retroactively improve the whole history, not apply only from that point forward. Requires retaining transcripts, recording which detector version produced each gap, and treating regeneration as a first-class operation. **User judgments (confirms, dismissals, explanations) must survive regeneration** — they are human input, not derived data.
- **2026-09-16 — S4: two-layer storage.** Raw transcripts retained **locally and never synced**; graph events carry an LLM-generated paraphrase plus a pointer (session id + line range) back to the raw. Resolves the S4/S5 tension: re-runnable *and* private. Rationale: Claude Code already writes these transcripts to disk, so retaining them adds no new exposure — **the exposure is the sync, not the storage.** Privacy boundary is the sync line.
- **2026-09-16 — S6 and S7 resolved by the event-log model.** An append-only log is versioned by construction (S6); logs merge without locking (S7).
- **2026-09-16 — S2: collapsed SOLO taxonomy, 3 levels** (isolated / listed / causal), explicitly flagged as changeable later. The boundary that matters is listed → causal: that is where recall separates from comprehension. **The irreversible part is storing the raw text of the user's explanation** as a separate event from its grade, so any future rubric can re-grade the entire history.
- **2026-09-16 — S3 downgraded from blocking to a spike.** Whether the atom is a `term` or a typed `gap` (term / decision / tool-call) needs empirical work and possibly user guidance. Does not block v1 for internal use — build with a term-shaped atom and keep the spike open.

### Storage and ingestion

- **2026-09-16 — Chose an append-only event log compiled to current state.** Gives S5, S6 and S7 largely for free, and gives an LLM the chronological reasoning trail rather than only the end state.
- **2026-09-16 — Chose Postgres over ClickHouse.** ClickHouse targets analytical scans over very large volumes; this workload is small writes and current-state lookups for a single user's graph. Append-only is a convention kept, not a database feature bought.
- **2026-09-16 — Nothing writes to the graph directly.** Every write — automatic ingestion and manual submission alike — passes through an LLM resolution step. Conflicts are therefore prevented at ingestion rather than reconciled at compile; the resolver is the serialisation point and compile stays a dumb fold.
- **2026-09-16 — Resolver judgments are appended as first-class events.** "Merged X into Y because it looked like a misspelling" is a judgment that can be wrong and must be correctable; storing only the outcome leaves nothing to correct against.
- **2026-09-16 — Accepted staleness rather than locking.** The resolver reads latest state, reasons, appends. Worst case is a duplicate concept a later pass merges — and merges are themselves correctable events. Not worth building locking for a single user.
- **2026-09-16 — Parked the general versioned-decision system as a separate later project.** Append-only markdown with explicit `supersedes:` references, compiled to current state. Same mechanism as the event log; building the general version inside unrot would roughly double the project. Plausibly a skill. Hone on unrot's single use case first.

### Graph semantics

- **2026-09-16 — Material is a first-class entity with a many-to-many join to concepts.** An explanation of eventual consistency necessarily covers partitions and quorum. Creates a third node state, **exposed**: named in material the user received, but not confirmed and not unseen.
- **2026-09-16 — Added a `referenced` node state.** A concept named in material but never encountered by the user. Scaffolding for clustering and future edges; never surfaced, never counts toward the flag budget. Validates the S1 split — a concept with zero encounters is valid, a gap requires an encounter.
- **2026-09-16 — Capped graph expansion at depth 1.** A concept enters the graph if named in material the user actually received; it does not then generate its own material, and its neighbours are not pulled in. Expansion is driven by encounters, never by the graph's own contents. Explosion comes from recursion, not from adding.
- **2026-09-16 — Deferred typed edges.** Similarity and clustering stand alone without them. When edges arrive, model them as events with a **free-text `type`, no enum** — structure without an unearned ontology.
- **2026-09-16 — Scoped vector similarity to resolution and clustering, not relationships.** Similarity cannot distinguish *same as* from *opposite of in the same space* (optimistic vs. pessimistic locking embed near-identically), and is symmetric where prerequisites are directional. Use it for ingestion near-duplicate detection and the domain heatmap.
- **2026-09-16 — Chose an LLM-proposes / similarity-verifies ensemble** for future edge work. Both agree → store; LLM proposes but similarity distant → likely hallucinated, drop; similarity close but LLM finds no relationship → clustering only. Precision-over-recall applies to edges more tightly than to flags, since a wrong prerequisite edge actively misroutes learning.
- **2026-09-16 — Store model/grader/detector versions alongside all derived state.** Embeddings, SOLO grades and gap detections are all derived; a silent model upgrade otherwise changes the meaning of history.

### Learning material

- **2026-09-16 — Sourcing/provenance is a P0 quality gate.** The product generates explanations of precisely the things the user cannot evaluate — a hallucinated definition lands on someone who by definition can't catch it, and is then wrong in their head *and* in the graph. Worse than not flagging at all. Grounding and provenance over free generation.
- **2026-09-16 — The user controls material generation at three levels:** whether it happens at all (pure-detector mode must be fully useful), what style within a format, and links-only as a first-class output. Material generation is a pluggable output stage; the gap graph is the product.
- **2026-09-16 — Chose to teach from the user's own code where possible.** Explain the queue in their ingest service, not backpressure in the abstract. A generic explainer is a commodity; an explanation grounded in the user's codebase is the moat.

### Product surfaces

- **2026-09-16 — Chose a domain heatmap over graph visualisation.** A sparse early graph looks pathetic and teaches nothing; gap density by domain over time is honest at low data volume and useful at high.
- **2026-09-16 — Chose to surface knowledge evolving over time.** The event log is already a time series, so per-concept trajectories cost nothing extra. Directly counters the shame-spiral risk: the same data, progress-shaped rather than deficit-shaped. **This is the strongest argument for the event log over a mutable store** — an overwritten row has no trajectory to show. Requires recording non-interaction events (vocabulary production, silent resolution, re-encounters) as densely as explicit ones.
- **2026-09-16 — Named inverted trust as an accepted, monitored risk.** "unrot will catch it" could license more blind acceptance. Mitigating factor: unrot has no write path and changes nothing about what ships, so it cannot be leaned on mechanically. Measure it — a falling clarifying-question rate after onboarding is the signal.

### The inversion

- **2026-09-16 — Adopted agent calibration as the direction of the integration.** unrot tells the agent *how to pitch explanations* — more for concepts the user doesn't know, less for those they do. Not a gap report, not an interface. The agent surfaces nothing about unrot, asks nothing on its behalf, interrupts nothing.
- **2026-09-16 — Ruled out "second brain" positioning.** Saturated and faddish category. Frame as the agent adapting to what you know, not as externalising your mind.
- **2026-09-16 — Reopened the MCP decision, narrowly, for resources only.** MCP exposes tools, prompts and resources; the 2026-09-01 reversal was about not building an *interaction surface*, which still holds for tools and prompts. Resources are a read path. Testable first as a plain generated context file with no MCP at all.

### Commercial

- **2026-09-16 — Parked enterprise/commercial work, with two items flagged as time-sensitive:** the Apache 2.0 licence choice (permits a cloud vendor to run the aggregation layer as a service — revisit before there is anything worth taking), and S4 becoming a procurement requirement rather than a design preference.
- **2026-09-16 — Ruled out individual comprehension scoring as an enterprise product.** A tool that reports individual developer comprehension to managers destroys its own signal: developers game blind acceptance, which is the exact behaviour the detector depends on. Must be architecturally impossible, not a policy promise. Aggregate views (codebase risk map, documentation gaps, onboarding, agent calibration) are both safer and more valuable.
