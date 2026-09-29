#!/bin/sh
# Render every screen of the Mac app to PNGs, light and dark, with no display.
#
#   mac/snapshots/run.sh [out-dir]        (default: build/snapshots)
#
# Builds throwaway stores in every surface state (make_homes.py), starts a core
# on each, runs the Debug app in its snapshot mode against them, and stops the
# cores. Nothing here touches ~/.unrot. Compare the output with the design
# canvas; that is what these are for.
#
# Offscreen drawing leaves out system materials -- the sidebar, a selected
# row's highlight. To see those, UNROT_SNAPSHOT_HOLD=<seconds> mac/snapshots/run.sh
# opens the real main window over the `full` fixtures instead, behind every
# other window and without taking focus, for a tool that captures one app's
# windows (the window server draws materials; `cacheDisplay` does not). It runs
# a copy with its own bundle id, com.unrot.mac.snapshots: an installed unrot
# then keeps its own defaults, and is not the app such a tool finds first.
set -eu
repo="$(cd "$(dirname "$0")/../.." && pwd)"
out="${1:-$repo/build/snapshots}"
homes="/tmp/unrot-snap-homes"   # short: socket paths are capped at 104 bytes
socks="/tmp/unrot-snap"

cd "$repo"
PYTHONPATH=src .venv/bin/python mac/snapshots/make_homes.py "$homes"
mkdir -p "$socks"
pids=""
spec=""
for home in full clean coldstart unanalysed empty; do
  rm -f "$socks/$home.sock"
  UNROT_HOME="$homes/$home" PYTHONPATH=src .venv/bin/python -m unrot.api --uds "$socks/$home.sock" \
    >"$socks/$home.log" 2>&1 &
  pids="$pids $!"
  spec="$spec;$home=$socks/$home.sock"
done
trap 'kill $pids 2>/dev/null || true' EXIT
for home in full clean coldstart unanalysed empty; do
  until [ -S "$socks/$home.sock" ]; do sleep 0.3; done
done

xcodebuild -project mac/Unrot.xcodeproj -scheme UnrotMac -configuration Debug build -quiet
app="$(xcodebuild -project mac/Unrot.xcodeproj -scheme UnrotMac -configuration Debug -showBuildSettings 2>/dev/null \
  | awk -F' = ' '/ BUILT_PRODUCTS_DIR /{print $2; exit}')/Unrot.app"

if [ -n "${UNROT_SNAPSHOT_HOLD:-}" ]; then
  held="$socks/Unrot Snapshots.app"
  rm -rf "$held"
  cp -R "$app" "$held"
  plist="$held/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.unrot.mac.snapshots" "$plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleName unrot snapshots" "$plist"
  codesign --force --deep --sign - --preserve-metadata=entitlements "$held" 2>/dev/null
  app="$held"
fi

rm -rf "$out"
if [ -n "${UNROT_SNAPSHOT_HOLD:-}" ]; then
  # Through Launch Services, so a window-capturing tool can find it as an app;
  # -g so launching it does not bring it forward. -W holds the cores up for it.
  open -g -n -F -W --env UNROT_SNAPSHOTS="$out" --env UNROT_SNAPSHOT_HOMES="${spec#;}" \
    --env UNROT_SNAPSHOT_HOLD="$UNROT_SNAPSHOT_HOLD" "$app"
  exit 0
fi
UNROT_SNAPSHOTS="$out" UNROT_SNAPSHOT_HOMES="${spec#;}" "$app/Contents/MacOS/Unrot"
echo "$(ls "$out" | wc -l | tr -d ' ') screenshots in $out"
