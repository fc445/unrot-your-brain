#!/bin/sh
# The whole PR-35 experiment, unattended. Builds ad-hoc-signed SparkleProbe
# apps, serves a signed appcast from 127.0.0.1, installs the old build into
# /Applications and lets Sparkle update it. Every result comes from the probe's
# own log (results/<scenario>.log) and from inspecting the installed app.
#
#   ./run.sh
#
# Scenarios:
#   control   build 1 launched twice, empty appcast: can it read its own Keychain item?
#   prod      build 1 (prod) -> build 2 (prod); a build 3 on the dev channel must stay hidden
#   dev       build 1 (dev)  -> build 3 (dev)
#   tampered  build 1 (prod) with an appcast whose EdDSA signature is wrong: must refuse
#
# SCENARIOS="prod dev" ./run.sh runs just those (default: all four).
#
# Leaves nothing behind: /Applications/SparkleProbe.app and the probe's
# Keychain item are removed at the end. Keys and builds live in a temp dir.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
results="$here/results"
port=8765
feed="http://127.0.0.1:$port/appcast.xml"
installed=/Applications/SparkleProbe.app
sparkle_version=2.10.0

work="${WORK:-$(mktemp -d)}"
echo "work dir: $work"
mkdir -p "$results" "$work/www" "$work/builds"
log="$work/probe.log"

# --- Sparkle --------------------------------------------------------------------------
SPARKLE_DIR="${SPARKLE_DIR:-$work/sparkle}"
export SPARKLE_DIR
if [ ! -d "$SPARKLE_DIR/Sparkle.framework" ]; then
  mkdir -p "$SPARKLE_DIR"
  curl -fsSL "https://github.com/sparkle-project/Sparkle/releases/download/$sparkle_version/Sparkle-$sparkle_version.tar.xz" \
    | tar xJ -C "$SPARKLE_DIR"
fi

# --- keys -----------------------------------------------------------------------------
swift "$here/probe/keygen.swift" "$work/ed_private" "$work/ed_public"
pubkey="$(cat "$work/ed_public")"

# --- server ---------------------------------------------------------------------------
python3 -m http.server "$port" --bind 127.0.0.1 --directory "$work/www" >"$work/http.log" 2>&1 &
server=$!

cleanup() {
  kill "$server" 2>/dev/null || true
  pkill -x SparkleProbe 2>/dev/null || true
  rm -rf "$installed"
  security delete-generic-password -s com.unrot.sparkleprobe -a probe-api-key >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- helpers --------------------------------------------------------------------------
build() {  # build <name> <build-number> <channels>
  mkdir -p "$work/builds/$1"
  "$here/build-probe.sh" "$work/builds/$1" "$2" "$3" "$feed" "$pubkey" "$log" >/dev/null
}

dmg() {  # dmg <name> -> $work/www/<name>.dmg, the way make-dmg.sh wraps Unrot.app
  hdiutil create -quiet -volname SparkleProbe -srcfolder "$work/builds/$1" -ov -format UDZO "$work/www/$1.dmg"
}

sig() {  # sig <file> -> sparkle:edSignature="..." length="..."
  "$SPARKLE_DIR/bin/sign_update" --ed-key-file "$work/ed_private" "$1"
}

item() {  # item <build> <channel|""> <dmg-name> <sig-attrs>
  channel=""
  [ -n "$2" ] && channel="<sparkle:channel>$2</sparkle:channel>"
  cat <<ITEM
    <item>
      <title>Build $1</title>
      <sparkle:version>$1</sparkle:version>
      <sparkle:shortVersionString>0.$1.0</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>14.0</sparkle:minimumSystemVersion>
      $channel
      <enclosure url="http://127.0.0.1:$port/$3.dmg" type="application/octet-stream" $4/>
    </item>
ITEM
}

appcast() {  # appcast <item>... -> $work/www/appcast.xml
  {
    echo '<?xml version="1.0" encoding="utf-8"?>'
    echo '<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
    echo '  <channel><title>SparkleProbe</title>'
    for i in "$@"; do printf '%s\n' "$i"; done
    echo '  </channel>'
    echo '</rss>'
  } > "$work/www/appcast.xml"
}

install() {  # install <name>: straight copy, no quarantine -- the state after `xattr -dr`
  pkill -x SparkleProbe 2>/dev/null || true
  rm -rf "$installed"
  ditto "$work/builds/$1/SparkleProbe.app" "$installed"
}

run_until() {  # run_until <build that should finish> <timeout seconds> [DONE lines to wait for]
  open "$installed"
  i=0
  while [ "$i" -lt "$2" ]; do
    [ "$(grep -Ec "build=$1 pid=[0-9]+ DONE" "$log" 2>/dev/null)" -ge "${3:-1}" ] && return 0
    sleep 1; i=$((i + 1))
  done
  echo "  !! timed out waiting for build $1 to finish"
  return 1
}

installed_build() { /usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$installed/Contents/Info.plist"; }

want() { case " ${SCENARIOS:-control prod dev tampered} " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

scenario() {  # scenario <name>: start a fresh log
  echo "== $1"
  : > "$log"
  current="$1"
}

record() {  # keep the probe's log, plus what is on disk afterwards
  {
    cat "$log"
    echo "--- after: installed build $(installed_build)"
    codesign -dv "$installed" 2>&1 | grep -E '^(Signature|TeamIdentifier)='
    echo "--- after: xattrs on the installed app: $(xattr "$installed" | tr '\n' ' ')"
  } > "$results/$current.log"
  sed 's/^/  /' "$results/$current.log"
}

# --- builds ---------------------------------------------------------------------------
build prod1 1 ""
build prod2 2 ""
build dev1  1 "dev"
build dev3  3 "dev"
dmg prod2
dmg dev3
sig_prod2="$(sig "$work/www/prod2.dmg")"
sig_dev3="$(sig "$work/www/dev3.dmg")"
real_appcast() { appcast "$(item 2 "" prod2 "$sig_prod2")" "$(item 3 dev dev3 "$sig_dev3")"; }

security delete-generic-password -s com.unrot.sparkleprobe -a probe-api-key >/dev/null 2>&1 || true

# --- control: same build twice, nothing to update to ----------------------------------
if want control; then
scenario control
appcast
install prod1
run_until 1 30 1 || true
run_until 1 30 2 || true
record
fi

# --- prod: 1 -> 2, dev item hidden ----------------------------------------------------
security delete-generic-password -s com.unrot.sparkleprobe -a probe-api-key >/dev/null 2>&1 || true
if want prod; then
scenario prod
real_appcast
install prod1
run_until 2 120 || true
record
cp "$work/www/appcast.xml" "$results/appcast.xml"
fi

# --- dev: 1 -> 3 on the dev channel ---------------------------------------------------
security delete-generic-password -s com.unrot.sparkleprobe -a probe-api-key >/dev/null 2>&1 || true
if want dev; then
scenario dev
real_appcast
install dev1
run_until 3 120 || true
record
fi

# --- tampered: the prod2 DMG with dev3's signature ------------------------------------
if want tampered; then
scenario tampered
length_prod2="$(printf '%s' "$sig_prod2" | sed -E 's/.*(length="[0-9]+").*/\1/')"
wrong_sig="$(printf '%s' "$sig_dev3" | sed -E 's/(sparkle:edSignature="[^"]+").*/\1/') $length_prod2"
appcast "$(item 2 "" prod2 "$wrong_sig")"
install prod1
run_until 1 120 || true
record
fi

# --- Sparkle's own log, for anything the probe did not catch --------------------------
/usr/bin/log show --last 15m --style compact \
  --predicate 'subsystem BEGINSWITH "org.sparkle-project"' \
  > "$results/sparkle-system.log" 2>/dev/null || true
echo "results in $results"
