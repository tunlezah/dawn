#!/usr/bin/env bash
# Build the web UI beside the one in use and swap it in only when it is complete.
#
#   build-web.sh <web dir> [npm command...]      (default npm; install.sh passes `sudo -u dawn env ... npm`)
#
# Vite empties its output directory before it writes, so a build that failed (or was killed by the OOM killer, likely
# on a Zero 2 W) used to leave web/dist empty: at the kiosk's next restart there was no face at all, and so no face
# backup alarm either. Here the build goes to dist.new; only when it finished and both pages are in it is the old
# bundle moved to dist.old and the new one put in place. On failure web/dist is untouched and the exit is non-zero.
set -uo pipefail
WEB="${1:?usage: build-web.sh <web dir> [npm command...]}"
shift
if [ "$#" -gt 0 ]; then NPM=("$@"); else NPM=(npm); fi
cd "$WEB" || exit 1
rm -rf dist.new
fail() { echo "web build failed: $*; web/dist left as it was"; rm -rf dist.new; exit 1; }
"${NPM[@]}" ci --no-audit --no-fund || fail "npm ci"
"${NPM[@]}" run typecheck || fail "type check"
"${NPM[@]}" exec -- vite build --outDir dist.new --emptyOutDir || fail "vite build"
[ -f dist.new/index.html ] && [ -f dist.new/face/index.html ] || fail "dist.new is missing index.html or face/index.html"
rm -rf dist.old
if [ -d dist ]; then mv dist dist.old || fail "could not move the old bundle aside"; fi
mv dist.new dist || { [ -d dist.old ] && mv dist.old dist; fail "could not put the new bundle in place"; }
echo "web UI built (the previous one is in web/dist.old)"
