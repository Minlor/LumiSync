# LumiSync Flatpak (scaffold)

> **Status: unfinished scaffold, not yet built or tested.** The supported
> Linux build today is the **AppImage** (`tools/build_linux.sh`). This directory
> is the starting point for a proper Flatpak; the steps below are what remain.

Flatpak is the better long-term Linux channel — it's sandboxed, integrates with
GNOME/KDE software centers, and (unlike the AppImage) can capture the Wayland
screen through the desktop portal without extra work. Finishing it needs a Linux
machine with `flatpak` and `flatpak-builder`.

## Remaining work

1. **Vendor the Python dependencies.** Flatpak builds are offline, so the pip
   deps (PySide6, numpy, pillow, bleak, tinytuya, soundcard, mss, …) must be
   pre-declared. Use the official helper:

   ```sh
   # from https://github.com/flatpak/flatpak-builder-tools
   pip install requirements-parser  # helper dep
   python flatpak-pip-generator \
     --runtime org.freedesktop.Sdk//24.08 \
     bleak tinytuya numpy pillow soundcard mss webcolors colorama \
     packaging PySide6 \
     --output packaging/flatpak/python3-deps
   ```

   That produces `python3-deps.json`, which the manifest already references.
   (PySide6 is large and may need `--only-binary`; confirm wheels resolve for
   the runtime's Python version.)

2. **Build and test:**

   ```sh
   flatpak install flathub org.freedesktop.Platform//24.08 org.freedesktop.Sdk//24.08
   flatpak-builder --user --install --force-clean build-dir \
     packaging/flatpak/io.github.Minlor.LumiSync.yaml
   flatpak run io.github.Minlor.LumiSync
   ```

3. **Verify the sandbox permissions** in `finish-args` against real hardware:
   - LAN discovery/control (Govee, Tuya) — needs `--share=network`.
   - BLE (iDotMatrix) — needs `--system-talk-name=org.bluez`; confirm `bleak`
     can scan/connect inside the sandbox.
   - Music sync — needs `--socket=pulseaudio`; confirm the monitor source is
     reachable (PipeWire's pulse shim included).
   - Screen sync — currently X11-only in the app; the ScreenCast portal arg is
     staged for a future Wayland capture backend.

4. **App metadata for Flathub** (if publishing): add an AppStream
   `io.github.Minlor.LumiSync.metainfo.xml` and the `.desktop`/icon install
   commands to the `lumisync` module.

## Why it's not wired into CI yet

Unlike the AppImage (validated by the `Build Linux Release` workflow on a GitHub
Ubuntu runner), the Flatpak needs the dependency vendoring above and a
real-hardware permissions pass before it's trustworthy. Once `python3-deps.json`
exists and a local build runs, add a `flatpak-builder` CI job mirroring
`linux-release.yaml`.
