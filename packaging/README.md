# The frozen core

The Mac app does not ask the user to install Python. It ships one, bundled as a
co-signed resource inside `Unrot.app`, spawns it on launch and talks to it over
a Unix socket. This directory builds that thing.

```bash
uv run --group packaging pyinstaller packaging/unrot-core.spec --noconfirm \
  --distpath build/dist --workpath build/work
```

Output is `build/dist/unrot-core/`, about **51 MB**, containing `unrot-core`
and an `_internal/` directory beside it. Both move together: the binary is not
relocatable on its own.

## Checking it actually works

The failure mode this guards against is a bundle that runs perfectly on the
machine that built it, because that machine has a Python, a `.venv` and a
checkout. So test it without any of them:

```bash
env -i HOME=/tmp/frozen UNROT_HOME=/tmp/frozen/h PATH=/nonexistent \
  ./build/dist/unrot-core/unrot-core --uds
```

```bash
curl -s --unix-socket /tmp/frozen/h/run/core.sock http://localhost/api/health
```

Then hit `POST /api/concepts/nope/material`. A **404** is the answer you want:
that endpoint imports LangChain, the resolver, the grader and the material
writer *before* it looks anything up, so reaching a database-level 404 proves
the whole late-import graph resolved. A 500 there means a missing hidden
import, which is the one class of bug a normal smoke test will not find.

## What is deliberate in the spec

**`--onedir`, not `--onefile`.** One-file unpacks to a temp directory on every
launch — a second or two of startup the user watches, and an interpreter
outside the app bundle where the code signature does not reach.

**`schema.sql` is listed in `datas`.** `store/db.py` and `capture/ingest.py`
both read a sibling `schema.sql` via `Path(__file__).with_name(...)`.
PyInstaller collects modules, not data, so without those two lines the core
starts cleanly and dies on its first `connect()`.

**LangChain is collected whole** rather than trusted to the import graph — it
resolves providers and tokenisers dynamically. It is also most of the 51 MB. If
that becomes worth attacking, the honest fix is upstream: the detector makes
one call, not a graph, so a plain OpenAI-compatible client would do.

**No UPX** — compressed binaries cannot be notarised. **No `codesign_identity`**
— the Xcode build co-signs this as part of the app.

**`target_arch=None`** follows the building interpreter, so this produces an
arm64 bundle on Apple silicon. A universal2 app needs a universal2 Python to
freeze from; that is a Phase 6 problem, not a Phase 0 one.

## Signing

`sign-core.sh` signs the frozen core inside out: loose libraries, then each
framework as a bundle, then `unrot-core` with `core.entitlements`. No `--deep`.
The Xcode build phase and `release.sh` both call it, so there is one definition.

The entitlements were settled by trying without them, signed ad hoc under the
hardened runtime:

| entitlements | result |
|---|---|
| none | does not start: dyld refuses `Python.framework`, "different Team IDs" |
| `disable-library-validation` only | starts, serves, every late import loads |

`allow-jit` and `allow-unsigned-executable-memory` are not needed, so the core
does not have them. Under one Developer ID team, library validation should pass
without the exception too; it is kept so a development build and a release load
libraries the same way. The Swift app itself carries no code-signing exceptions.

## Releasing

```bash
DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)" NOTARY_PROFILE=unrot-notary packaging/release.sh --check
```

`--check` verifies the identity and the notarisation profile and stops. Without
it the script freezes the core, archives, verifies the signatures, notarises,
staples and zips. **It has not been run end to end** — see its header.
