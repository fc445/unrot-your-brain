#!/bin/sh
# Build one SparkleProbe.app, signed ad hoc the way packaging/dmg.sh signs
# Unrot.app: no Developer ID, no hardened runtime.
#
#   build-probe.sh <out-dir> <build-number> <channels> <feed-url> <public-key> <log-path>
#
# <channels> is what the build opts into, comma separated ("" for prod, "dev"
# for a dev build). Needs SPARKLE_DIR pointing at an unpacked Sparkle release.
set -eu

out="$1"; build="$2"; channels="$3"; feed="$4"; pubkey="$5"; logpath="$6"
here="$(cd "$(dirname "$0")" && pwd)"
: "${SPARKLE_DIR:?set SPARKLE_DIR to an unpacked Sparkle release}"

app="$out/SparkleProbe.app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Frameworks"

swiftc -O -target arm64-apple-macos14.0 \
  -F "$SPARKLE_DIR" -framework Sparkle \
  -Xlinker -rpath -Xlinker @executable_path/../Frameworks \
  "$here/probe/main.swift" -o "$app/Contents/MacOS/SparkleProbe"

# ditto keeps the framework's symlinks intact; cp -R would too, but ditto is
# what Apple documents for bundles.
ditto "$SPARKLE_DIR/Sparkle.framework" "$app/Contents/Frameworks/Sparkle.framework"

cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.unrot.sparkleprobe</string>
  <key>CFBundleName</key><string>SparkleProbe</string>
  <key>CFBundleExecutable</key><string>SparkleProbe</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.$build.0</string>
  <key>CFBundleVersion</key><string>$build</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>SUFeedURL</key><string>$feed</string>
  <key>SUPublicEDKey</key><string>$pubkey</string>
  <key>SUEnableAutomaticChecks</key><false/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>ProbeChannels</key><string>$channels</string>
  <key>ProbeLogPath</key><string>$logpath</string>
</dict>
</plist>
PLIST

# Inside out, as Sparkle's docs say for non-sandboxed apps: the helpers, then
# the framework, then the app. No --deep, no --options runtime.
fw="$app/Contents/Frameworks/Sparkle.framework/Versions/B"
codesign --force --sign - "$fw/XPCServices/Installer.xpc"
codesign --force --sign - "$fw/XPCServices/Downloader.xpc"
codesign --force --sign - "$fw/Autoupdate"
codesign --force --sign - "$fw/Updater.app"
codesign --force --sign - "$app/Contents/Frameworks/Sparkle.framework"
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"
echo "built $app (build $build, channels '${channels}')"
