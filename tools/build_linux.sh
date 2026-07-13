#!/usr/bin/env bash
# Build a Linux AppImage of LumiSync.
#
# Steps: create a build venv, install the app + PyInstaller, build the onedir
# bundle from the shared spec, assemble an AppDir, and pack it with
# appimagetool into dist/LumiSync-x86_64.AppImage.
#
# Env vars:
#   PYTHON=python3.12   interpreter to build with (default: python3)
#   RUN_TESTS=1         run the unit suite (offscreen Qt) before packaging
#
# Build on the oldest Ubuntu you want to support — the resulting AppImage is
# tied to the build machine's glibc.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
DIST="$ROOT/dist"
BUILD="$ROOT/build/linux"
VENV="$BUILD/venv"
APPDIR="$BUILD/LumiSync.AppDir"

step() { printf '\n==> %s\n' "$1"; }

step "Creating build virtualenv"
rm -rf "$VENV"
"$PYTHON" -m venv "$VENV"
# shellcheck disable=SC1091
. "$VENV/bin/activate"
python -m pip install --upgrade pip wheel
python -m pip install -e "$ROOT" pyinstaller

if [ "${RUN_TESTS:-0}" = "1" ]; then
    step "Running unit tests"
    QT_QPA_PLATFORM=offscreen python -m unittest discover -s "$ROOT/tests"
fi

step "Building PyInstaller onedir bundle"
python -m PyInstaller \
    --noconfirm --clean \
    --distpath "$DIST" \
    --workpath "$BUILD/pyinstaller" \
    "$ROOT/packaging/pyinstaller/lumisync_onedir.spec"

step "Assembling AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -r "$DIST/LumiSync/." "$APPDIR/usr/bin/"
install -m 0755 "$ROOT/packaging/linux/AppRun" "$APPDIR/AppRun"
cp "$ROOT/packaging/linux/lumisync.desktop" "$APPDIR/lumisync.desktop"
cp "$ROOT/lumisync/gui/resources/icons/app.svg" "$APPDIR/lumisync.svg"

step "Fetching appimagetool"
TOOL="$BUILD/appimagetool"
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$TOOL"
fi

step "Packing AppImage"
mkdir -p "$DIST"
# --appimage-extract-and-run avoids needing FUSE on the build host (e.g. CI).
ARCH=x86_64 "$TOOL" --appimage-extract-and-run \
    "$APPDIR" "$DIST/LumiSync-x86_64.AppImage"

step "Done"
echo "Built: $DIST/LumiSync-x86_64.AppImage"
