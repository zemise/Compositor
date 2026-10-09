#!/bin/bash
# Checks the packaged app's signature, native architecture and early launch failures.
set -euo pipefail
app=${1:?Usage: smoke-test-app.sh /path/to/Compositor.app arm64|x86_64}
expected=${2:?Pass the native architecture to test}
[[ $(uname -m) == "$expected" ]] || { echo "Expected a native $expected runner" >&2; exit 1; }
executable="$app/Contents/MacOS/Compositor"
lipo "$executable" -verify_arch arm64 x86_64
codesign --verify --deep --strict --verbose=2 "$app"
log=$(mktemp)
pid=''
cleanup() {
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        wait "$pid" || true
    fi
    rm -f "$log"
}
trap cleanup EXIT
/usr/bin/arch -"$expected" "$executable" > "$log" 2>&1 &
pid=$!
for ((second=0; second<10; second++)); do
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        cat "$log" >&2
        echo "Compositor exited during $expected launch" >&2
        exit 1
    fi
done
echo "Compositor stayed running for 10 seconds on $expected"
