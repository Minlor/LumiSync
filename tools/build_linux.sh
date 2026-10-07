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
# Install Qt's xcb runtime dependencies on that machine before building; see
# .github/workflows/linux-release.yaml for the Ubuntu package list.
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

step "Checking bundled X11 dependencies"
# These are easy to miss on a headless build host. PyInstaller warns about
# unresolved libraries but still produces a bundle, and offscreen tests pass.
INTERNAL="$DIST/LumiSync/_internal"
for library in \
    PySide6/Qt/plugins/platforms/libqxcb.so \
    libxkbcommon-x11.so.0 \
    libxcb-cursor.so.0; do
    if [ ! -f "$INTERNAL/$library" ]; then
        echo "Missing bundled X11 dependency: $library" >&2
        echo "Install the Qt X11 runtime packages listed in .github/workflows/linux-release.yaml and rebuild." >&2
        exit 1
    fi
done

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
