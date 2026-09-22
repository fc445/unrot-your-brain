#!/bin/sh
# Sign the frozen core for embedding in Unrot.app, inside out.
#
#   packaging/sign-core.sh <unrot-core dir> <identity> <entitlements.plist>
#
# `identity` is a Developer ID Application identity for a release, or `-` for an
# ad-hoc signature, which is how this is tested on a machine with no
# certificate. Called by the Xcode "Bundle the frozen core" phase and by
# packaging/release.sh, so there is one definition of how the core is signed.
#
# Order matters and `--deep` is not used, on purpose. Notarisation wants every
# piece of nested code signed individually with the hardened runtime and a
# timestamp, and a bundle's seal covers the signatures of what is inside it --
# so the loose libraries go first, then each framework as a bundle, then the
# executable that loads them all, with its entitlements. `--deep` would sign
# everything with the outermost options, entitlements included, which is both
# wrong for libraries and the thing Apple's own guidance says not to rely on.
set -eu

core="${1:?unrot-core directory}"
identity="${2:?signing identity, or - for ad hoc}"
entitlements="${3:?entitlements plist}"

if [ "$identity" = "-" ]; then
  stamp="--timestamp=none"   # a secure timestamp needs a real identity
else
  stamp="--timestamp"
fi

sign() {
  codesign --force $stamp --options runtime --sign "$identity" "$@"
}

is_macho() {
  file -b "$1" | grep -q '^Mach-O'
}

# 1. Every Mach-O file that is not inside a framework, except the executable.
find "$core/_internal" -type f -not -path '*.framework/*' | while IFS= read -r f; do
  if is_macho "$f"; then sign "$f"; fi
done

# 2. Each framework, as a bundle, at its versioned directory.
find "$core/_internal" -type d -name '*.framework' -prune | while IFS= read -r fw; do
  for version in "$fw"/Versions/*; do
    [ -L "$version" ] && continue          # Versions/Current is a symlink
    [ -d "$version" ] && sign "$version"
  done
done

# 3. The executable last, with entitlements. A frozen Python under the hardened
#    runtime needs them: see mac/Support/UnrotMac.entitlements for which, and why.
sign --entitlements "$entitlements" "$core/unrot-core"

codesign --verify --strict --verbose=1 "$core/unrot-core"
