# PR-6: Where do candidate concepts come from?

**Date:** 20260915
**Ticket:** [PR-6](https://linear.app/freddie-cassidy/issue/PR-6/spike-where-do-candidate-concepts-come-from) — "Spike: where do candidate concepts come from?" (project: unrot-your-brain, label: `human`)

**Goal:** answer whether manual-only capture gets to a useful graph, or a thin proposer is mandatory — by running an actual (unpolished) proposer over real transcripts rather than hand-labeling 10 sessions in the abstract.

This replaces the original plan in the ticket (hand-label 10 sessions, count precision) with: build the thin thing, run it for real, look at the output. The judgment call is unchanged and still needs a human — an agent can run the pass, it can't decide whether the candidates are worth a node.

## Files

- `jsonl_transcript_parser.py` — copy of PR-9's parser (`spikes/20260914-PR-9-jsonl-transcript-parser/`), unmodified. Duplicated rather than imported cross-folder so this spike stands alone; if this graduates out of `spikes/`, de-dupe into a shared module.
- `candidate_proposer.py` — two-node LangGraph pipeline. `load_transcript` parses the session and pairs each assistant turn with the human's next turn; `propose_candidates` makes one structured-output call per session, flagging terms used load-bearingly and without explanation, classified against the human's next turn as `accepted` / `questioned` / `unclear`. Talks to the model through an OpenAI-compatible endpoint (`langchain_openai.ChatOpenAI`) rather than a provider-specific SDK, since Freddie routes model calls through OpenRouter or a local model rather than holding a direct Anthropic/OpenAI key.
- `pyproject.toml` — `langgraph`, `langchain`, `langchain-openai`, `pydantic`, `python-dotenv`.
- `.env.example` — template for `OPENROUTER_API_KEY` and LangSmith tracing vars; copy to `.env` (gitignored) and fill in.

## Run it

```
# via OpenRouter (default)
export OPENROUTER_API_KEY=...
uv run candidate_proposer.py --latest
uv run candidate_proposer.py ~/.claude/projects/<project>/<session-id>.jsonl

# via a local model server (e.g. Ollama, LM Studio) instead
uv run candidate_proposer.py --latest \
    --base-url http://localhost:11434/v1 --api-key ollama --model llama3.1
```

`uv run` resolves and syncs this folder's `pyproject.toml` automatically — no separate install step.

`--model` and `--base-url` default to OpenRouter and `anthropic/claude-sonnet-4.5`; override either to point at a different OpenRouter model or a local endpoint. `--api-key` falls back to `$OPENROUTER_API_KEY`, then `$OPENAI_API_KEY`.

### LangSmith tracing

Copy `.env.example` to `.env` and fill in `LANGSMITH_API_KEY` (plus `LANGSMITH_ENDPOINT` if your LangSmith workspace is outside the default US region — Freddie's is EU, `https://eu.api.smith.langchain.com`). The script loads `.env` via `python-dotenv` at import time; LangGraph/LangChain pick up `LANGSMITH_TRACING`/`LANGSMITH_API_KEY`/`LANGSMITH_PROJECT` from the environment automatically, no code-level opt-in. Traces land in the `pr-6-langgraph-candidate-proposer` LangSmith project by default.

## What was verified empirically

- `build_windows` correctly pairs assistant turns with the following human turn, checked against a synthetic transcript matching PR-9's documented schema (plain-string user turns, block-array assistant turns, a mid-session clarifying question).
- The graph compiles and the full `load_transcript → propose_candidates` pipeline runs end to end, checked with a stubbed model standing in for the real call (no API key was available in the environment that built this).
- The OpenAI-compatible wiring has since been run against a real local vLLM server (`qwen3.8-flash-next`, an OpenAI-compatible endpoint) — it worked, but only against this session's own transcript (`--latest` resolved to the conversation that produced this spike), which surfaced only meta-noise about the spike itself rather than genuine load-bearing jargon. Not yet run against a real *working* session.
- LangSmith tracing wiring has not yet been run/confirmed for this script specifically (added after the vLLM run above) — the mechanism (env-var-driven auto-instrumentation) is the same one confirmed working for the `deep-agent/` quickstart in this repo, but hasn't been separately verified here.

## Known gaps / next steps

- **Not yet run against a real, substantive session.** That's the actual PR-6 judgment call — needs Freddie's own `~/.claude` transcripts from real coding sessions (not this spike's own meta-conversation), and his read on which candidates are genuine vs. noise.
- One LLM call per whole session, no chunking — a long session may exceed context or attention budget before the model reaches the end.
- No precision/volume budgeting (the ~1–2/session target from `decisions.md`). Deliberately prints everything flagged, so the raw signal is visible before deciding whether to constrain it.
- The "human's next turn" lookup only filters out `isMeta` turns — it doesn't check whether that turn is itself just a tool result being echoed back in, which could misclassify a signal.
- Structured output support and quality vary by OpenRouter model/provider and by local model — not all of them handle `with_structured_output` as reliably as a first-party API.
