# PR-6: Where do candidate concepts come from?

**Date:** 20260915
**Ticket:** [PR-6](https://linear.app/freddie-cassidy/issue/PR-6/spike-where-do-candidate-concepts-come-from) — "Spike: where do candidate concepts come from?" (project: unrot-your-brain, label: `human`)

**Goal:** answer whether manual-only capture gets to a useful graph, or a thin proposer is mandatory — by running an actual (unpolished) proposer over real transcripts rather than hand-labeling 10 sessions in the abstract.

This replaces the original plan in the ticket (hand-label 10 sessions, count precision) with: build the thin thing, run it for real, look at the output. The judgment call is unchanged and still needs a human — an agent can run the pass, it can't decide whether the candidates are worth a node.

## Files

- `jsonl_transcript_parser.py` — copy of PR-9's parser (`spikes/20260914-PR-9-jsonl-transcript-parser/`), unmodified. Duplicated rather than imported cross-folder so this spike stands alone; if this graduates out of `spikes/`, de-dupe into a shared module.
- `candidate_proposer.py` — two-node LangGraph pipeline. `load_transcript` parses the session and pairs each assistant turn with the human's next turn; `propose_candidates` makes one structured-output call to `claude-sonnet-5` per session, flagging terms used load-bearingly and without explanation, classified against the human's next turn as `accepted` / `questioned` / `unclear`.
- `requirements.txt` — `langgraph`, `langchain`, `langchain-anthropic`, `pydantic`.

## Run it

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python3 candidate_proposer.py --latest
python3 candidate_proposer.py ~/.claude/projects/<project>/<session-id>.jsonl
```

## What was verified empirically

- `build_windows` correctly pairs assistant turns with the following human turn, checked against a synthetic transcript matching PR-9's documented schema (plain-string user turns, block-array assistant turns, a mid-session clarifying question).
- The graph compiles and the full `load_transcript → propose_candidates` pipeline runs end to end, checked with a stubbed model standing in for the real Anthropic call (no API key was available in the environment that built this).

## Known gaps / next steps

- **Not yet run against a real session or a real model call.** That's the actual PR-6 judgment call — needs Freddie's `ANTHROPIC_API_KEY` and his own `~/.claude` transcripts, and his read on which candidates are genuine vs. noise.
- One LLM call per whole session, no chunking — a long session may exceed context or attention budget before the model reaches the end.
- No precision/volume budgeting (the ~1–2/session target from `decisions.md`). Deliberately prints everything flagged, so the raw signal is visible before deciding whether to constrain it.
- The "human's next turn" lookup only filters out `isMeta` turns — it doesn't check whether that turn is itself just a tool result being echoed back in, which could misclassify a signal.
