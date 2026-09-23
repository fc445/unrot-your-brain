# unrot-your-brain

Notices the terms you let slide past in Claude Code sessions, asks whether you
actually knew them, and turns the ones you didn't into something to learn.

## Why

Working with a coding agent, it is easy to accept what it writes without
understanding it. An unfamiliar term, pattern or tradeoff goes past, the code
works, and nothing makes you stop and learn it. Over time that wears away the
skill and judgment that made you good at the work in the first place, and it
doesn't fix itself: nothing today points out what you skipped.

unrot finds those moments. It reads the session transcripts Claude Code already
writes to disk and looks for terms the agent relied on without explaining,
where your next message shows you went along with it rather than engaged with
it. It then asks you directly whether you knew each one.

A few principles follow from that:

- **Capture is silent.** No plugin, hook, slash command or injected text. Your
  workflow doesn't change; unrot only reads files, and all interaction happens
  in its own app.
- **Precision over recall.** One or two good flags a session, not twenty.
  A noisy list is one people stop opening within a week.
- **You are the judge.** A flag is a question, not a verdict. Your "I knew it"
  or "I didn't" is the record, and a check answer is graded against the
  question as it was shown to you.
- **It measures what you know, not how you use AI.** Tools that score your
  collaboration style already exist. This is about the specific concepts the
  agent assumed you had.
- **The page ends on progress.** What you've closed is shown last, so opening
  the app isn't only a list of what you don't know.

The full reasoning is in the [PRD](docs/unrot-your-brain-PRD.md), with
[decisions](docs/decisions.md) and [ideas and research](docs/unrot-your-brain-ideas.md)
alongside it.

## Getting it running

Python 3.11 or later and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run pytest
```

Model calls go through OpenRouter or a local OpenAI-compatible endpoint.
Copy `.env.example` to `.env` and set a key; capture works without one.

There are two surfaces over the same core, and neither decides anything the
core does not:

- **The Mac app**: `mac/`. Build it with Xcode, and see
  [`mac/README.md`](mac/README.md). A Debug build runs this checkout's
  `.venv`; a Release build bundles the frozen core from
  [`packaging/`](packaging/README.md).
- **The web page**: `ui/`, served by `python -m unrot.api`. See
  [`ui/README.md`](ui/README.md).

Exploratory work lives in [`spikes/`](spikes/README.md).
