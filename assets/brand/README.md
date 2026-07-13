# LumiSync brand assets

This kit is generated from one continuous `LS` vector path. Run
`\.venv\Scripts\python.exe tools\generate_brand_assets.py` from the repository
root whenever the master geometry or palette changes.

## Recommended files

- `svg/lumisync-app.svg` — full-color app icon on the dark rounded tile.
- `svg/lumisync-mark-gradient.svg` — transparent full-color brand mark.
- `svg/lumisync-mark-white.svg` and `svg/lumisync-mark-dark.svg` — one-color marks.
- `svg/lumisync-tray-color.svg` — default cross-theme system-tray mark.
- `svg/lumisync-tray-light.svg` and `svg/lumisync-tray-dark.svg` — thicker,
  one-color tray alternatives for dark and light system surfaces.
- `png/app/` — application PNGs from 16 px through 1024 px.
- `png/mark/` — transparent gradient marks from 64 px through 1024 px.
- `png/tray/` — light and dark tray PNGs at 16, 20, 24, 32, and 48 px.
- `ico/lumisync.ico` — multi-resolution Windows application icon.
- `png/social/lumisync-github-banner-1280x640.png` — GitHub social preview.

## Usage

- Use the dark-tile app icon for executables, installers, shortcuts, GitHub
  avatars, and launchers.
- Use the transparent gradient mark on branded pages with generous whitespace.
- Use the transparent color tray mark when the OS theme is unknown. The
  monochrome alternatives are available when the surface color is known; do
  not shrink the full app tile into the tray.
- Preserve clear space around the mark equal to roughly one stroke width.
- Do not add LED chips, extra arrows, bloom, shadows, or additional colors to
  the small functional variants.

The source palette is `#14E8E8 → #2688FF → #7D45F6 → #ED2ACD` on graphite
surfaces.
