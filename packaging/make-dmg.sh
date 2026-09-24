#!/bin/sh
# Wrap an .app in a compressed disk image with an Applications link beside it,
# so installing is a drag.
#
#   packaging/make-dmg.sh <path/to/Unrot.app> <out.dmg> <volume name>
#
# One definition, used by dmg.sh (ad hoc) and release.sh (Developer ID). Signing
# and notarising the image are the caller's business.
set -eu

app="$1"
dmg="$2"
volume="$3"
[ -d "$app" ] || { echo "make-dmg: no app at $app" >&2; exit 1; }

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
# ditto, not cp: it keeps the signature's extended attributes and symlinks intact.
ditto "$app" "$stage/$(basename "$app")"
ln -s /Applications "$stage/Applications"

mkdir -p "$(dirname "$dmg")"
rm -f "$dmg"
hdiutil create -volname "$volume" -srcfolder "$stage" -format UDZO -ov -quiet "$dmg"
