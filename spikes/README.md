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
