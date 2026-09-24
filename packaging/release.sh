#!/bin/sh
# Build, sign, notarise and staple Unrot.app for distribution outside the App
# Store, and ship it as a notarised DMG.
#
#   DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)" \
#   NOTARY_PROFILE=unrot-notary \
#   [UNROT_CHANNEL=dev] \
#   packaging/release.sh [--check]
#
# UNROT_CHANNEL is prod unless set; dev compiles in the code behind
# `#if DEV_FEATURES`, and takes UNROT_DEV_LANGSMITH_API_KEY as the Developer
# tab's default LangSmith key. A prod build never gets one. For an ad-hoc DMG
# without a Developer ID, see dmg.sh.
#
# NOT YET RUN END TO END. It was written on a machine with no Developer ID
# certificate, so the signing and notarisation steps have never executed. What
# *has* been verified: the frozen core signed by packaging/sign-core.sh under
# the hardened runtime starts, serves, and loads every late import (tested with
# an ad-hoc identity). Treat the first real run as the test, read its output,
# and use --check first.
#
# Prerequisites, once per machine:
#   * a "Developer ID Application" certificate in the login keychain;
#   * notarisation credentials stored as a keychain profile -- the password
#     goes to Apple's tool directly, never into this script or its environment:
#       xcrun notarytool store-credentials unrot-notary
#
# Not the Mac App Store, at least not first: the sandbox plus a bundled
# interpreter spawning a subprocess is survivable but expensive, and nothing
# about the product needs the storefront.
set -eu

repo="$(cd "$(dirname "$0")/.." && pwd)"
out="$repo/build/release"
check_only=false
channel="${UNROT_CHANNEL:-prod}"
[ "${1:-}" = "--check" ] && check_only=true

fail() { echo "release: $*" >&2; exit 1; }

case "$channel" in dev|prod) ;; *) fail "UNROT_CHANNEL must be dev or prod, not '$channel'" ;; esac
: "${DEVELOPER_ID:?set DEVELOPER_ID to your Developer ID Application identity}"
: "${NOTARY_PROFILE:?set NOTARY_PROFILE to a profile made with xcrun notarytool store-credentials}"

team="$(printf '%s' "$DEVELOPER_ID" | sed -n 's/.*(\([A-Z0-9]*\))$/\1/p')"
[ -n "$team" ] || fail "could not read a Team ID from '$DEVELOPER_ID' -- expected '... (TEAMID)'"

# --- prerequisites: fail before building anything --------------------------------
security find-identity -v -p codesigning | grep -qF "$DEVELOPER_ID" \
  || fail "no signing identity '$DEVELOPER_ID' in the keychain"
xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 \
  || fail "no notarisation profile '$NOTARY_PROFILE' -- run: xcrun notarytool store-credentials $NOTARY_PROFILE"
command -v uv >/dev/null || fail "uv is not installed"

# xcodebuild reads build settings from the environment too, so prod passes an
# empty key explicitly.
if [ "$channel" = dev ]; then langsmith_key="${UNROT_DEV_LANGSMITH_API_KEY:-}"; else langsmith_key=""; fi

echo "channel:   $channel"
echo "identity:  $DEVELOPER_ID"
echo "team:      $team"
echo "notary:    $NOTARY_PROFILE"
echo "arch:      $(uname -m) only -- a universal build needs a universal2 Python to freeze from"
$check_only && { echo "prerequisites OK"; exit 0; }

rm -rf "$out"
mkdir -p "$out"

# --- 1. freeze the core from a clean tree ------------------------------------------
uv run --group packaging pyinstaller "$repo/packaging/unrot-core.spec" --noconfirm \
  --distpath "$repo/build/dist" --workpath "$repo/build/work"

# --- 2. archive, signed with the Developer ID ---------------------------------------
# The "Bundle the frozen core" phase sees this identity and signs the core inside
# out through packaging/sign-core.sh before Xcode seals the app around it.
xcodebuild -project "$repo/mac/Unrot.xcodeproj" -scheme UnrotMac -configuration Release \
  -archivePath "$out/Unrot.xcarchive" archive \
  UNROT_CHANNEL="$channel" UNROT_DEV_LANGSMITH_API_KEY="$langsmith_key" \
  CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY="$DEVELOPER_ID" DEVELOPMENT_TEAM="$team" \
  OTHER_CODE_SIGN_FLAGS="--timestamp"

app="$out/Unrot.xcarchive/Products/Applications/Unrot.app"
[ -d "$app" ] || fail "the archive has no Unrot.app"

# --- 3. verify before paying for a notarisation round trip ----------------------------
codesign --verify --deep --strict --verbose=2 "$app"
if [ "$channel" = prod ]; then
  [ -z "$(/usr/libexec/PlistBuddy -c 'Print :UnrotLangSmithKey' "$app/Contents/Info.plist" 2>/dev/null)" ] \
    || fail "a prod build carries a LangSmith key"
fi
codesign -dv "$app/Contents/Resources/unrot-core/unrot-core" 2>&1 | grep -q 'runtime' \
  || fail "the embedded core is not signed with the hardened runtime"

# --- 4. notarise, staple, and check the way Gatekeeper will -------------------------
ditto -c -k --keepParent "$app" "$out/Unrot-notarise.zip"
xcrun notarytool submit "$out/Unrot-notarise.zip" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$app"
spctl --assess --type execute --verbose=2 "$app"

# --- 5. the DMG: signed and notarised in its own right, so opening it is quiet too ----
# The app inside is already stapled, so it launches offline either way.
version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$app/Contents/Info.plist")"
if [ "$channel" = dev ]; then
  dmg="$out/Unrot-$version-dev.dmg"; volume="Unrot (dev)"
else
  dmg="$out/Unrot-$version.dmg"; volume="Unrot"
fi
"$repo/packaging/make-dmg.sh" "$app" "$dmg" "$volume"
codesign --sign "$DEVELOPER_ID" --timestamp "$dmg"
xcrun notarytool submit "$dmg" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$dmg"
spctl --assess --type open --context context:primary-signature --verbose=2 "$dmg"
echo "done: $dmg"
