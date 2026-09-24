#!/bin/sh
# Build Unrot.app for a channel and wrap it in a DMG, signed ad hoc: no
# Developer ID, no notarisation. For putting a build on your own Mac or a
# tester's.
#
#   UNROT_CHANNEL=dev  packaging/dmg.sh    # dev features compiled in
#   UNROT_CHANNEL=prod packaging/dmg.sh    # what ships; the default
#
# A dev build takes UNROT_DEV_LANGSMITH_API_KEY, if set, as the default key for
# the Developer tab's LangSmith toggle. It is readable by anyone with the DMG.
# A prod build never gets it.
#
# Both channels are optimised Release builds running the bundled frozen core.
# The channel decides one thing: whether code behind `#if DEV_FEATURES` is
# compiled in. A prod DMG does not contain it at all.
#
# A downloaded ad-hoc app is quarantined and Gatekeeper will refuse it. Once,
# after the first refusal: System Settings > Privacy & Security > Open Anyway.
# Or skip the dance:  xattr -dr com.apple.quarantine /Applications/Unrot.app
#
# A DMG for strangers wants release.sh, which signs and notarises.
set -eu

repo="$(cd "$(dirname "$0")/.." && pwd)"
channel="${UNROT_CHANNEL:-prod}"

fail() { echo "dmg: $*" >&2; exit 1; }

case "$channel" in dev|prod) ;; *) fail "UNROT_CHANNEL must be dev or prod, not '$channel'" ;; esac
command -v uv >/dev/null || fail "uv is not installed"

# Archived outside the checkout: a checkout under a synced folder tags every
# file it writes with an extended attribute that codesign refuses (mac/README.md).
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# xcodebuild reads build settings from the environment as well as its command
# line, so prod passes an empty key explicitly rather than just not passing one.
if [ "$channel" = dev ]; then langsmith_key="${UNROT_DEV_LANGSMITH_API_KEY:-}"; else langsmith_key=""; fi

echo "channel:   $channel"
if [ "$channel" = dev ]; then
  if [ -n "$langsmith_key" ]; then echo "langsmith: key included"; else echo "langsmith: no key"; fi
fi

# --- 1. freeze the core, every time: a DMG with a stale core is worse than a slow one
uv run --group packaging pyinstaller "$repo/packaging/unrot-core.spec" --noconfirm \
  --distpath "$repo/build/dist" --workpath "$repo/build/work"

# --- 2. archive, signed ad hoc ("Sign to Run Locally") --------------------------------
# No hardened runtime. It exists for notarisation, which an ad-hoc build cannot
# have, and under it the app refuses its own UnrotKit.framework: library
# validation wants a Team ID and an ad-hoc signature has none. A Debug build
# out of Xcode is signed the same way, for the same reason.
xcodebuild -project "$repo/mac/Unrot.xcodeproj" -scheme UnrotMac -configuration Release \
  -destination "generic/platform=macOS" -archivePath "$work/Unrot.xcarchive" archive -quiet \
  UNROT_CHANNEL="$channel" UNROT_DEV_LANGSMITH_API_KEY="$langsmith_key" \
  CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY=- DEVELOPMENT_TEAM= ENABLE_HARDENED_RUNTIME=NO

app="$work/Unrot.xcarchive/Products/Applications/Unrot.app"
[ -d "$app" ] || fail "the archive has no Unrot.app"

# --- 3. check the build is the channel that was asked for -----------------------------
plist="$app/Contents/Info.plist"
built="$(/usr/libexec/PlistBuddy -c 'Print :UnrotChannel' "$plist")"
[ "$built" = "$channel" ] || fail "asked for a $channel build, got '$built'"
if [ "$channel" = prod ]; then
  [ -z "$(/usr/libexec/PlistBuddy -c 'Print :UnrotLangSmithKey' "$plist" 2>/dev/null)" ] \
    || fail "a prod build carries a LangSmith key"
fi
codesign --verify --deep --strict "$app"

# --- 4. wrap -------------------------------------------------------------------------
version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")"
if [ "$channel" = dev ]; then
  dmg="$repo/build/dmg/Unrot-$version-dev.dmg"; volume="Unrot (dev)"
else
  dmg="$repo/build/dmg/Unrot-$version.dmg"; volume="Unrot"
fi
"$repo/packaging/make-dmg.sh" "$app" "$dmg" "$volume"
echo "done: $dmg"
