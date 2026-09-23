# unrot-your-brain

Notices the terms you let slide past in Claude Code sessions, asks whether you
actually knew them, and turns the ones you didn't into something to learn.

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
