#!/bin/sh
# Applies the rulesets in this folder to the GitHub repo: each one replaces the
# ruleset of the same name, or is created if there is none. Needs admin on the
# repo. See "Branches and CI" in packaging/README.md.
#
#   .github/rulesets/apply.sh [owner/repo]
set -eu

repo="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
dir="$(dirname "$0")"

for f in "$dir"/*.json; do
  name="$(sed -n 's/^  "name": "\(.*\)",$/\1/p' "$f")"
  [ -n "$name" ] || { echo "No name in $f" >&2; exit 1; }
  id="$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id")"
  if [ -n "$id" ]; then
    gh api -X PUT "repos/$repo/rulesets/$id" --input "$f" >/dev/null
    echo "updated: $name"
  else
    gh api -X POST "repos/$repo/rulesets" --input "$f" >/dev/null
    echo "created: $name"
  fi
done
