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
it the script freezes the core, archives, verifies the signatures, notarises and
staples the app, then wraps it in a DMG that is itself signed, notarised and
stapled. **It has not been run end to end** — see its header.

Without a Developer ID, `dmg.sh` builds the same DMG signed ad hoc:

```bash
UNROT_CHANNEL=dev packaging/dmg.sh
```

Both scripts take `UNROT_CHANNEL=dev|prod` (default `prod`); a `dev` build
compiles in the code behind `#if DEV_FEATURES`. A `dev` build also takes
`UNROT_DEV_LANGSMITH_API_KEY` as the default key for the Developer tab's
LangSmith toggle. A `prod` build is always given an empty one, and both
scripts fail a `prod` build that still ends up with a key. Both call
`make-dmg.sh`, so the image is laid out one way. See "Dev and prod builds" in
[`mac/README.md`](../mac/README.md).

## Branches and CI

Everything reaches `develop` and `master` through a PR, and a PR merges only
once `ci.yml`'s **CI ok** check has passed. That check runs the Python tests,
builds the UI, and builds and tests the Mac app. So every commit a release can
be cut from has already passed CI.

| Branch | Takes PRs from | Merged by | Why |
|---|---|---|---|
| `develop` | feature branches | squash | one commit per change, linear history |
| `master` | `develop` only | merge commit | `master` stays a descendant of `develop`, so the next promotion merges cleanly |

To promote to production, open a PR from `develop` into `master` and merge it
with a merge commit. Squashing or rebasing would give `master` copies of
`develop`'s commits rather than the commits themselves, and every promotion
after that would conflict. A PR into `master` from any other branch fails CI's
**Promotion source** check. A fix that is needed in production goes to
`develop` first.

Neither branch can be force-pushed or deleted, and neither needs an approving
review, since there is one maintainer. A repo admin can still merge a PR whose
checks fail, for example when a runner is down, but cannot push to either
branch directly. Release tags (`v*`) cannot be moved or deleted. A release
that fails gets a new version, not the old tag reused, because installed apps
may already have seen the old one in the appcast.

These rules are GitHub rulesets, kept in [`.github/rulesets/`](../.github/rulesets).
After editing one, run this with admin on the repo:

```bash
.github/rulesets/apply.sh
```

## Releasing from GitHub

`develop` is the dev branch and `master` is production. Merging does not
release anything. Publishing a GitHub release does: it builds a DMG and
attaches it to the release.

| Branch | Release | Tag | DMG |
|---|---|---|---|
| `develop` | **Set as a pre-release** ticked | `vX.Y.Z-dev.N`, e.g. `v0.2.0-dev.1` | `Unrot-0.2.0-dev.1.dmg`, dev features in |
| `master` | a full release | `vX.Y.Z`, e.g. `v0.2.0` | `Unrot-0.2.0.dmg`, no dev features |

```bash
gh release create v0.2.0-dev.1 --target develop --prerelease --generate-notes
```

```bash
gh release create v0.2.0 --target master --generate-notes
```

`release-dev.yml` and `release-prod.yml` both call `build-dmg.yml`, which runs
`dmg.sh` on a `macos-26` runner. It adds install notes to the release,
including the `xattr` command macOS needs before it will open an ad-hoc build.
The workflow refuses a release that breaks the rules above:

- a dev release not tagged `-dev`;
- a prod release with a suffixed tag;
- a prod release whose commit is not on `master`. Merge `develop` into
  `master` first.

Promoting a `-dev` pre-release to a full release fails for the same reason.

A dev release bakes in the repository secret `LANGSMITH_API_KEY`, if it is
set, as the Developer tab's default LangSmith key. The repo is public and so
are the DMGs attached to its releases, so use a key you would be fine leaking.
The prod workflow never passes the secret.

**The version comes from the tag.** Nothing needs editing beforehand. The
workflow runs `set-version.sh`, which stamps the tag's version into the Xcode
project, `pyproject.toml` and `unrot.__version__`. The build number is the
commit count, so it only ever goes up, whichever channel. The stamp is not
committed back: the tag records what was built, and the checked-in `0.1.0` is
only what local builds call themselves.

```bash
packaging/set-version.sh v0.2.0-dev.1 54
```

That is the same stamp, run locally. It touches tracked files, so run it on a
throwaway tree or put the old version back afterwards.

GitHub runs a release workflow from the tagged commit, so a tag cut before
these files existed will not build.

## Updates

The app updates itself with [Sparkle](https://sparkle-project.org) (PR-36).
Once a build with it is installed, every later release reaches that copy
without anyone downloading a DMG.

After `build-dmg.yml` attaches the DMG, its `appcast` job signs the DMG and adds
it to `appcast.xml` on the repo's **`appcast` branch**. Every installed app reads
that file from `raw.githubusercontent.com` (`SUFeedURL`), once a day, or when
someone chooses **Check for Updates…** from the app menu. `packaging/appcast.py`
does the signing and the editing.

- **Channels stay apart.** A dev release's item is tagged `dev` and a prod
  release's `prod`. A build accepts only its own channel, so a dev tester is
  never moved onto a prod build, and a prod user never sees a dev one.
- **The build number decides what's newer.** It's the commit count, so it only
  goes up.
- **Nothing is signed with a Developer ID yet.** Sparkle accepts an update
  between two ad-hoc builds because of its own EdDSA signature. The PR-35 spike
  (`spikes/20260926-PR-35-sparkle-adhoc-updates/`) shows this working, and a
  tampered signature being refused.
- **A Keychain prompt after each update.** macOS ties the saved API key to the
  exact build that saved it, so it asks once after each update. Settings ›
  Advanced says so. A Developer ID would stop it.
- **Debug builds never update themselves.** They'd replace their own build in
  DerivedData.

### The signing key, once

The app carries the public half of an EdDSA key (`SPARKLE_PUBLIC_ED_KEY`, a
build setting on the UnrotMac target). The private half is the repo secret
`SPARKLE_ED_PRIVATE_KEY`.

```bash
uv run packaging/appcast.py keygen ~/unrot-sparkle-private-key
```

That prints the public key. Put it in `SPARKLE_PUBLIC_ED_KEY` for both the
Debug and Release configurations of the UnrotMac target. Then store the private
key as the secret:

```bash
gh secret set SPARKLE_ED_PRIVATE_KEY < ~/unrot-sparkle-private-key
```

Keep the key file somewhere safe, such as a password manager, and delete the
loose copy. **If the private key is lost, installed apps can never update
again.** Everyone would have to download a DMG by hand once to move to a new key.

The `appcast` job refuses to publish if the secret isn't the private half of
the project's public key. A mismatch would make every installed copy reject
every update as "improperly signed". Without the secret, releases are still
built and attached, but they're left out of the appcast, with a warning on the
run.

To check a file's signature the way Sparkle does:

```bash
SPARKLE_ED_PRIVATE_KEY="$(cat ~/unrot-sparkle-private-key)" uv run packaging/appcast.py sign build/dmg/Unrot-0.2.0.dmg
```
