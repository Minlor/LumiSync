#!/usr/bin/env bash
# Build a Linux AppImage of LumiSync.
#
# Steps: create a locked build venv with uv, build the onedir bundle from the
# shared spec, assemble an AppDir, and pack it with appimagetool into
# dist/LumiSync-x86_64.AppImage, plus dist/LumiSync-x86_64.AppImage.tar.gz,
# which keeps the executable bit through a download.
#
# Env vars:
#   PYTHON=3.12         interpreter or version for uv to build with (default:
#                       $UV_PYTHON, else 3.12; uv downloads it if missing).
#                       Not plain python3: Ubuntu 22.04 ships 3.10, and the
#                       project needs 3.11+.
#   RUN_TESTS=1         run the unit suite (offscreen Qt) before packaging
#
# Build on the oldest Ubuntu you want to support — the resulting AppImage is
# tied to the build machine's glibc.
# Install Qt's xcb runtime dependencies on that machine before building; see
# .github/workflows/linux-release.yaml for the Ubuntu package list.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-${UV_PYTHON:-3.12}}"
DIST="$ROOT/dist"
BUILD="$ROOT/build/linux"
VENV="$BUILD/venv"
APPDIR="$BUILD/LumiSync.AppDir"

step() { printf '\n==> %s\n' "$1"; }

step "Installing the locked build environment"
if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required for reproducible builds. See https://docs.astral.sh/uv/" >&2
    exit 1
fi
rm -rf "$VENV"
UV_PROJECT_ENVIRONMENT="$VENV" uv sync \
    --frozen --no-dev --project "$ROOT" --group build --python "$PYTHON"
PY="$VENV/bin/python"

if [ "${RUN_TESTS:-0}" = "1" ]; then
    step "Running unit tests"
    QT_QPA_PLATFORM=offscreen "$PY" -m unittest discover -s "$ROOT/tests"
fi

step "Building PyInstaller onedir bundle"
"$PY" -m PyInstaller \
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
chmod 0755 "$DIST/LumiSync-x86_64.AppImage"

step "Packing the download archive"
# A browser download or a GitHub artifact zip never keeps the executable bit,
# so the raw AppImage always needs chmod +x. A tar archive keeps the mode:
# extracting it gives a file that runs straight away.
tar -C "$DIST" --owner=0 --group=0 --numeric-owner \
    -czf "$DIST/LumiSync-x86_64.AppImage.tar.gz" LumiSync-x86_64.AppImage

step "Done"
echo "Built: $DIST/LumiSync-x86_64.AppImage"
echo "       $DIST/LumiSync-x86_64.AppImage.tar.gz (keeps the executable bit)"
