# unrot for Mac

A SwiftUI window over the Python core, which ships inside the app bundle and is
spawned, supervised and shut down by it. Phases 0–2 of
[PR-29](https://linear.app/freddie-cassidy/issue/PR-29/mac-app-a-swiftui-surface-over-the-python-core-and-the-watcher-the).

```
mac/
  UnrotKit/        models, the socket client, the view model — iOS-clean
  UnrotMac/        the window, the views, the supervisor
  UnrotKitTests/   the transport, against a stub socket server
  Support/         Info.plist and entitlements
```

## Building

```bash
xcodebuild -project mac/Unrot.xcodeproj -scheme UnrotMac -configuration Debug build
```

```bash
xcodebuild -project mac/Unrot.xcodeproj -scheme UnrotKit -configuration Debug test
```

The iOS criterion, which is the only thing this ticket owes the phone:

```bash
xcodebuild -project mac/Unrot.xcodeproj -scheme UnrotKit -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' build
```

**Always build through a scheme, never `-target`.** A `-target` build puts
products in `mac/build/`, inside the checkout — and if the checkout is under a
synced folder, every produced file picks up a file-provider extended attribute
and `codesign` refuses the bundle with *"resource fork, Finder information, or
similar detritus not allowed"*. Building through a scheme uses DerivedData,
which is outside all of that.

## Which core it runs

| Build | Core |
|---|---|
| Release | the bundled frozen sidecar, `Contents/Resources/unrot-core/` |
| Debug | the repo's `.venv/bin/python -m unrot.api`, so the Python loop stays fast |
| Either, with `UnrotUseFrozenCore` | the bundled one |

```bash
defaults write com.unrot.mac UnrotUseFrozenCore -bool YES
```

The frozen core is copied in by a build phase from `build/dist/unrot-core`.
Debug warns when it is absent; Release fails, and tells you the command.
See [`packaging/README.md`](../packaging/README.md).

### If the Debug core starts and then says nothing

**Give the app access to the folder your checkout is in, or use the frozen
core.** `~/Documents` and `~/Desktop` are protected by macOS: a GUI app reading
one blocks on a consent prompt, and a blocked interpreter produces no output at
all, which is indistinguishable from a hang. `run/core.log` will be empty and
the process will sit in state `S`. The app says so after a few failed polls.

This does not affect a shipped build — the frozen core lives inside the app
bundle and never reads the checkout.

## Where things are at runtime

Shared with the CLI, deliberately: the app is a surface over the same store.

```
~/.unrot/
  unrot.db          the event log
  run/              0700
    core.sock       the socket the app talks to
    core.log        the core's stdout and stderr, across restarts
    app.lock        flock'd, so a second copy knows it is one
  raw/              never leaves this machine
```

## What it does

**The window** — the three buckets in their fixed order, gap cards, the check,
both material formats, the moment view, and all five empty states plus the
derived failure. A bar under the masthead says when captured sessions are
waiting to be analysed, with the one button that spends money on them.

**The ring in the menu bar** — closed when nothing is waiting, broken when
something is, with the count beside it; dashed while analysing, crossed when
paused, dotted when the core is down. Never a red dot. Left click opens a
popover that answers the top gap; right click is *Analyse now · Pause watching
· Add a gap · Open unrot · Quit*. ⌘-drag it out of the menu bar to hide it;
View › Show in Menu Bar brings it back. Closing the window leaves the ring
running.

**Quick accept** — **D** for *I didn't know this*, **K** for *I knew this*, the
same in the popover, the window, a notification, and the **⌥⌘J** triage panel
that works from any app. Every answer has eight seconds of undo (⌘Z in the
window). It records confirm and dismiss only, and has no route to the check.

**Add to unrot** — select text in any app › right-click › **Services** › *Add
to unrot*, or **⌘N** / the ring's menu to type one. macOS puts third-party items
under Services, not at the top of the context menu. The keyboard shortcut is
set in System Settings › Keyboard › Keyboard Shortcuts › Services: a service
cannot ship an Option shortcut as its default, so ⌥⌘U is a suggestion.

**The watcher** — captures a finished session once it and its project have been
quiet for ten minutes. Capture is a copy and costs nothing. **Analysis is off
until you ask for it**: *Analyse now*, or Settings › Watching › *Analyse
finished sessions automatically* — which applies only to sessions that finish
after you switch it on.

**Notifications** — off by default. At most one a day in an hour you choose;
never on a clean day, never mid-session, never twice about the same gap.

**Settings** — Watching (open at login, capture, quiet period, automatic
analysis, the queue), Model (endpoint, model, the key in your login Keychain,
what is in effect, what leaves this Mac), Notifications, Capture (retained
copies, the Services shortcut), Regenerate (the plan, then a run that leaves
every judgment alone).

## Keys

| | |
|---|---|
| D / K | answer the top gap |
| ⌘Z | undo the last answer (window) |
| ⌥⌘J | triage, from any app |
| ⌘N | add a gap |
| ⌘0 | open the window |
| ⌘R / ⇧⌘R | reload / restart the core |

## Not done

- **Signing, notarisation, Sparkle.** `packaging/release.sh` is written and
  checks its prerequisites, but has never run: this machine has no Developer ID.
  Sparkle needs a decision about where updates are hosted.
- **Deleting retained copies.** Capture appends to them incrementally, so doing
  it safely needs a core operation that forgets them as well.
- **Universal build.** The frozen core is arm64; universal2 needs a universal2
  Python to freeze from.
- **The model key for the CLI.** It stays in env or `.env`; the CLI does not
  read the Keychain, which would prompt on every run.
