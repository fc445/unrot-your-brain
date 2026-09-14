# CLAUDE.md

## Spikes

When doing exploratory/throwaway work to answer a question for a Linear ticket before real implementation, use the `spikes/` folder — never drop a spike script loose at the repo root.

Every spike gets its own dated, ticket-tagged folder:

```
spikes/
  <YYYYMMDD>-<ticket-id>-<short-slug>/
    README.md      # entrypoint: goal, findings, how to run, open gaps
    <artifacts>     # the script(s), sample output, or other evidence
```

Rules for traceability:

- **Folder name** carries the date the spike was done, the ticket ID it belongs to, and a short slug — e.g. `20260914-PR-9-jsonl-transcript-parser`.
- **Every spike folder needs its own `README.md`.** Write it so a cold reader (human or agent, with no memory of the session that produced it) can pick it up and continue: state the ticket and its goal, what was verified and how, how to run the artifacts, and what's still open or unverified. Don't rely on the ticket description alone — capture what was actually *learned*, since that's the part that isn't in Linear.
- **After adding or finishing a spike, update `spikes/README.md`'s index** with a new row: date, ticket link, spike folder link, one-line summary. That index is the map of every spike in the repo — keep it current rather than letting people discover spikes by browsing folders.
- Spikes are exploratory by nature — don't hold them to the code-quality bar of the rest of the repo. But do keep them honest: state what was verified empirically versus assumed, and don't claim something works if it was only tested against one narrow case.
