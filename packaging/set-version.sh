#!/bin/sh
# Stamp one version into every place the repo keeps one, so the app, the frozen
# core and the Python package agree about what they are.
#
#   packaging/set-version.sh <version> [build]
#
#   packaging/set-version.sh v0.2.0 57
#   packaging/set-version.sh 0.2.0-dev.3 58
#
# <version> is X.Y.Z with an optional leading v and an optional prerelease
# suffix. Where it goes:
#
#   the app's CFBundleShortVersionString  X.Y.Z only -- macOS wants integers
#   the app's CFBundleVersion             [build], when given
#   pyproject.toml                        X.Y.Z only -- it must stay PEP 440
#   unrot.__version__                     the whole thing, suffix included, so
#                                         /api/health says exactly which build
#
# The release workflows run this on a throwaway checkout and do not commit it:
# the release tag is the record of what was built.
set -eu

repo="$(cd "$(dirname "$0")/.." && pwd)"
fail() { echo "set-version: $*" >&2; exit 1; }

[ $# -ge 1 ] || fail "usage: set-version.sh <version> [build]"
full="${1#v}"
build="${2:-}"

base="$(printf '%s' "$full" | sed -n 's/^\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)\(-[0-9A-Za-z.-]*\)\{0,1\}$/\1/p')"
[ -n "$base" ] || fail "'$1' is not a version: expected X.Y.Z or X.Y.Z-suffix, e.g. v0.2.0 or v0.2.0-dev.1"
if [ -n "$build" ]; then
  case "$build" in *[!0-9]*) fail "build must be a whole number, not '$build'" ;; esac
fi

# perl -pi rather than sed -i: the two disagree about -i between macOS and Linux.
pbx="$repo/mac/Unrot.xcodeproj/project.pbxproj"
V="$base" perl -pi -e 's/(MARKETING_VERSION = )[^;]*;/$1$ENV{V};/' "$pbx"
[ -n "$build" ] && B="$build" perl -pi -e 's/(CURRENT_PROJECT_VERSION = )[^;]*;/$1$ENV{B};/' "$pbx"
V="$base" perl -pi -e 's/^version = "[^"]*"/version = "$ENV{V}"/' "$repo/pyproject.toml"
V="$full" perl -pi -e 's/^__version__ = "[^"]*"/__version__ = "$ENV{V}"/' "$repo/src/unrot/__init__.py"

echo "version:   $full (app $base${build:+, build $build})"
