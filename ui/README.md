# unrot — the surface (PR-23)

The standalone product surface: React + Vite talking to a FastAPI backend over
JSON. Per the PRD this is *the* place the user interacts with unrot — Claude
Code integration is capture-only, and nothing here reaches back into an agent.

## Running it

```bash
uv sync                                    # backend deps
npm --prefix ui install                    # frontend deps
```

Two ways to run, depending on whether you are using it or changing it.

**Using it** — one process, one port:

```bash
npm --prefix ui run build && PYTHONPATH=src .venv/bin/python -m unrot.api
```

FastAPI serves the built bundle from `ui/dist`, so everything is on
<http://localhost:8000>.

**Changing it** — two processes, hot reload on the frontend:

```bash
PYTHONPATH=src .venv/bin/python -m unrot.api --reload   # :8000
npm --prefix ui run dev                                  # :5173
```

Vite proxies `/api` to `:8000`, so the frontend's fetch paths are identical in
both modes. Open <http://localhost:5173>.

> `PYTHONPATH=src` is not optional: hatchling writes its editable-install `.pth`
> without a trailing newline and Python 3.14 skips such lines, so `import unrot`
> silently fails otherwise. `pyproject.toml` already sets `pythonpath` for
> pytest for the same reason. `.claude/launch.json` sets it too.

## Getting data into it

**The resolver (PR-21) is not built**, which means nothing currently turns
detector output into events, which means the event log is empty. Until it
exists, seed development fixtures:

```bash
PYTHONPATH=src .venv/bin/python -m unrot.store seed
PYTHONPATH=src .venv/bin/python -m unrot.store seed --clear   # and back out again
```

Fixtures are stamped `origin='fixture'` in the log, so they are identifiable in
one query and removable in one command, and the surface says on screen when it
is showing them. Where real sessions have been captured they anchor to real
session ids and line numbers, so the moment view is exercised against real
transcripts rather than stubbed.

They go through `events.append` like every other write — the actor rules and
payload validation still apply — so seeding is not a second write path into the
graph, just a cheaper decision function in front of the same one.

## What the API is

| | |
| -- | -- |
| `GET /api/surface` | Which of the states we are in, plus the whole gap list. One request, so the list and the status can never disagree. |
| `POST /api/encounters/{id}/confirm` | "I genuinely didn't know this." Appends a user event and recompiles. |
| `POST /api/encounters/{id}/dismiss` | "I knew it fine." Same. |
| `GET /api/encounters/{id}/moment` | The point of acceptance: what was said, what you said back. Local only. |

Interactive docs at `/docs` while the server is running.

## Three things worth knowing before changing it

**Confirming does not close a gap.** `confirm` means *I did not know this*,
which is where learning it starts; `dismiss` means the flag was wrong and closes
it. This falls out of `derive_state` in the store and is reflected in the
buckets — it is not a UI choice to be undone in the frontend.

**Presentation policy lives in Python.** `api/read.py` decides which bucket a
concept renders in and which state the surface is in. The frontend renders what
it is told. Putting the bucket rule in TypeScript would separate it from
`derive_state`, which is the rule it has to agree with.

**Buckets are ordered so the page does not end on a deficit.** Waiting on you →
To learn → Closed. `ideas.md` §5 is blunt that a daily list of your failures is
a product people delete in week two, so the last thing on the page is progress.
Nothing in the palette is red except an actual backend failure.

## Known gaps

**"Clean" cannot yet mean "we looked at this session and found nothing."**
Nothing records that the detector ran over a session, so a session that produced
no flags is indistinguishable from one never analysed. The surface therefore
reports `not_analysed` until *some* encounter exists anywhere, and reports
`clean` only once everything found has been dealt with. Both are honest, but
neither is the per-session clean bill of health journey 3 eventually wants —
**PR-21 should append a `session_analysed` event**, and `Capture.sessions_with_flags`
becomes a real coverage number when it does.

Also out of scope here, per the ticket: candidate-density visibility (journey
5), the domain heatmap, graph visualisation, and the timeline.
