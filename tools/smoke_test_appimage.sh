#!/usr/bin/env bash
# Test the actual packaged GUI on X11, e.g. xvfb-run -a bash tools/smoke_test_appimage.sh <AppImage>.
# Run in a clean runtime so build-host libraries cannot hide missing dependencies.
set -euo pipefail

APPIMAGE="$(readlink -f "${1:?Usage: smoke_test_appimage.sh <AppImage>}")"
WORKDIR="$(mktemp -d)"
APP_PID=""

cleanup() {
    if [ -n "$APP_PID" ]; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

fail() {
    echo "$1" >&2
    cat "$WORKDIR/startup.log" >&2
    exit 1
}

cd "$WORKDIR"
# Extraction exercises the packaged AppRun without requiring FUSE privileges.
"$APPIMAGE" --appimage-extract > /dev/null
export XDG_CONFIG_HOME="$WORKDIR/config"
export XDG_DATA_HOME="$WORKDIR/data"
export XDG_CACHE_HOME="$WORKDIR/cache"
export XDG_RUNTIME_DIR="$WORKDIR/runtime"
export TMPDIR="$WORKDIR"
mkdir -m 0700 "$XDG_RUNTIME_DIR"
export QT_QPA_PLATFORM=xcb
export QT_DEBUG_PLUGINS=1
timeout --kill-after=5s 60s "$WORKDIR/squashfs-root/AppRun" > "$WORKDIR/startup.log" 2>&1 &
APP_PID=$!

WINDOW=""
for attempt in $(seq 1 30); do
    kill -0 "$APP_PID" 2>/dev/null || fail "AppImage exited before showing a window."
    WINDOW="$(xdotool search --onlyvisible --name '^LumiSync$' 2>/dev/null || true)"
    [ -n "$WINDOW" ] && break
    sleep 1
done
[ -n "$WINDOW" ] || fail "AppImage did not show its main window within 30 seconds."

# The catalog waits at least ten seconds. Catch delayed startup crashes too.
for attempt in $(seq 1 12); do
    sleep 1
    kill -0 "$APP_PID" 2>/dev/null || fail "AppImage exited after showing its window."
done
xdotool search --onlyvisible --name '^LumiSync$' > /dev/null 2>&1 \
    || fail "AppImage main window disappeared."
echo "AppImage opened its X11 window and stayed running."
