# Spikes

Throwaway/exploratory work, one folder per spike. Each spike exists to answer a question for a specific Linear ticket before real implementation work starts.

## Structure

```
spikes/
  <YYYYMMDD>-<ticket-id>-<short-slug>/
    README.md      # what was being tested, what was found, how to run it, open gaps
    <artifacts>     # the script(s), sample output, or other evidence produced
```

Folder naming: date the spike was done, the ticket it belongs to, then a short slug — e.g. `20260914-PR-9-jsonl-transcript-parser`. Every spike folder must have its own `README.md`; that file is the entrypoint for anyone (human or agent) picking the spike up cold, and it must stand on its own without needing the original conversation that produced it.

## Index

| Date | Ticket | Spike | Summary |
|---|---|---|---|
| 20260914 | [PR-9](https://linear.app) | [jsonl-transcript-parser](20260914-PR-9-jsonl-transcript-parser/) | Defensive parser for Claude Code `.jsonl` session transcripts — extracts user/assistant turns and tool calls/results, tolerates schema drift and malformed lines. |
| 20260914 | [PR-10](https://linear.app/freddie-cassidy/issue/PR-10/spike-explore-claude-code-hooks-for-capturing-full-transcripts) | [hooks-transcript-capture](20260914-PR-10-hooks-transcript-capture/) | Claude Code hooks for capturing full session transcripts — event/config/payload reference, empirical verification against a live transcript, and prior art from entire.io/cli's shipped implementation. |
| 20260915 | [PR-6](https://linear.app/freddie-cassidy/issue/PR-6/spike-where-do-candidate-concepts-come-from) | [langgraph-candidate-proposer](20260915-PR-6-langgraph-candidate-proposer/) | Thin LangGraph proposer (2 nodes, structured output) that flags unexplained load-bearing terms per transcript window and classifies the human's next turn — built directly instead of the original hand-labeling plan, to settle whether a thin proposer is needed before the graph builder. |
| 20260920 | [PR-18](https://linear.app/freddie-cassidy/issue/PR-18) | [store-walkthrough](20260920-PR-18-store-walkthrough/) | Narrated nine-scene run through the event log and compile step — watch a concept move `gap` → `exposed` → `known`, see a full regeneration preserve user judgments, and leave a real SQLite file behind to inspect. Also pins down why `uv run python` cannot import `unrot` (macOS hidden flag on `.venv`, skipped by CPython 3.13+). |
| 20260923 | [PR-33](https://linear.app/freddie-cassidy/issue/PR-33) | [bench-runner](20260923-PR-33-bench-runner/) | Offline benchmark runner + scorer over a (synthetic) labelled set: detector × model × effort × repeats into a temp `$UNROT_HOME`, cost ceiling before spending, every headline metric recomputed from output files alone. Proposes the PR-27 label format; flag↔label matching left as the key open question. |
| 20260926 | [PR-34](https://linear.app/freddie-cassidy/issue/PR-34/spike-knowledge-map-familiarity-embeddings-vs-jev) | [knowledge-map-familiarity](20260926-PR-34-knowledge-map-familiarity/) | Predicting whether a person knows a new concept from their known/unknown map, on 170 synthetic concepts × 5 personas. Embeddings (term + gloss) capture area but not depth (boundary AUC 0.72); Jev ranks far better (AUC 0.91, boundary 0.89) but is biased towards "does not know" until its cut is learned from the person's map (accuracy 0.69 → 0.82). Bare-term embeddings don't even separate software from cooking. |
