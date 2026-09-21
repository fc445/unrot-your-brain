# UnrotKit

The models, the transport and the view model. No AppKit, no SwiftUI, no
platform assumptions — it builds for iOS unchanged, which is the only thing
PR-29 owes the phone.

## The rule

**Presentation policy lives in Python.** Which bucket a concept renders in,
which state the surface is in, what the headline and the next step say — all of
it arrives from `/api/surface` already decided, by `src/unrot/api/read.py`.

`ui/README.md` states the same rule for the web surface, for the same reason:
the bucket rule has to stay next to `derive_state`, or changing it becomes two
edits in two languages that can drift apart without anything looking wrong.

A Swift view model that works out a bucket is the failure this port is most
likely to produce, so two things are set up to make it awkward:

- `Bucket`, `SurfaceState`, `SoloLevel` and `MaterialFormat` are **structs
  wrapping a String**, not enums. They survive a value the server adds later —
  and, more to the point, they cannot be `switch`ed exhaustively, so the
  natural thing to do with one is render it rather than reason about it.
- Nothing in `SurfaceStore` computes a count, a bucket or a section membership.
  It filters what it was sent.

## The one thing the client does derive

`SurfaceState.failed`.

The server never sends it, by construction: a response saying "I failed" is a
response that arrived. So it is assembled here, from a request that did not
return. `UnrotClient` makes that possible by guaranteeing every call either
returns data or throws — nothing returns an empty result on error, because
journey 3 turns on "we looked and found nothing" being distinguishable from
"the core is down", and one `try?` in the middle loses it.

## The transport

`UnixSocketHTTP` is a hand-written HTTP/1.1 client over `NWConnection` with
`NWEndpoint.unix`. `URLSession` cannot address a Unix socket.

Writing an HTTP client is usually a mistake. It is defensible here because of
what this one never does: every request is a GET or a JSON POST, every response
is small JSON, both ends ship together, and `Connection: close` removes
keep-alive and connection reuse entirely. Chunked decoding is implemented
anyway — no `/api` endpoint chunks today, and the twenty lines mean the day one
does, the app does not start parsing framing bytes as JSON.

The alternative is `async-http-client`, which speaks `http+unix://` properly.
That is the fallback if streaming or keep-alive ever becomes necessary; until
then it is a large dependency tree for a socket on the same machine, and
keeping this target dependency-free is most of what iOS compatibility costs.
