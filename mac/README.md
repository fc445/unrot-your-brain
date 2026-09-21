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

## What is not here yet

The watcher (phase 3), the menu-bar item, notifications and quick accept
(phase 4), capture from anywhere (phase 4b), settings and Keychain (phase 5),
signing and Sparkle (phase 6). `python -m unrot.api` still works with none of
this installed, and `ui/` is still the headless surface.
