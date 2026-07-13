r"""Generate the LumiSync brand asset kit from one vector master.

Run from the repository root with::

    .venv\Scripts\python.exe tools\generate_brand_assets.py

The script intentionally keeps the functional icons flat. Small tray and app
icons should not depend on glow or raster-only detail for recognition.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
)
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
BRAND_ROOT = ROOT / "assets" / "brand"
SVG_DIR = BRAND_ROOT / "svg"
PNG_DIR = BRAND_ROOT / "png"
ICO_DIR = BRAND_ROOT / "ico"

APP_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
MARK_SIZES = (64, 128, 256, 512, 1024)
TRAY_SIZES = (16, 20, 24, 32, 48)
FONT_FAMILY = "Segoe UI"

# One continuous centerline. The larger gap in the inner S is deliberate: it
# survives antialiasing at 16 px while preserving the approved LS gesture.
MARK_PATH = (
    "M 230 190 V 650 "
    "Q 230 805 385 805 "
    "H 650 "
    "Q 805 805 805 650 "
    "Q 805 545 700 545 "
    "H 600 "
    "Q 505 545 505 450 "
    "Q 505 355 600 355 "
    "H 710"
)


def _gradient_defs() -> str:
    return """
    <linearGradient id="brand" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#14E8E8"/>
      <stop offset="42%" stop-color="#2688FF"/>
      <stop offset="72%" stop-color="#7D45F6"/>
      <stop offset="100%" stop-color="#ED2ACD"/>
    </linearGradient>
    """


def _mark(stroke: str = "url(#brand)", width: int = 118) -> str:
    return (
        f'<path d="{MARK_PATH}" fill="none" stroke="{stroke}" '
        f'stroke-width="{width}" stroke-linecap="round" '
        'stroke-linejoin="round"/>'
    )


def _path_data(path: QPainterPath) -> str:
    """Serialize a Qt painter path as SVG path data."""
    commands: list[str] = []
    index = 0
    while index < path.elementCount():
        element = path.elementAt(index)
        if element.type == QPainterPath.ElementType.MoveToElement:
            commands.append(f"M {element.x:.3f} {element.y:.3f}")
        elif element.type == QPainterPath.ElementType.LineToElement:
            commands.append(f"L {element.x:.3f} {element.y:.3f}")
        elif element.type == QPainterPath.ElementType.CurveToElement:
            control_2 = path.elementAt(index + 1)
            end = path.elementAt(index + 2)
            commands.append(
                "C "
                f"{element.x:.3f} {element.y:.3f} "
                f"{control_2.x:.3f} {control_2.y:.3f} "
                f"{end.x:.3f} {end.y:.3f}"
            )
            index += 2
        index += 1
    return " ".join(commands)


def _outlined_text(
    text: str,
    x: float,
    baseline: float,
    size: int,
    fill: str,
    *,
    bold: bool = False,
    letter_spacing: float = 0,
) -> str:
    """Return font-independent SVG outlines for a short text string."""
    font = QFont(FONT_FAMILY)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    if letter_spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    path = QPainterPath()
    path.addText(QPointF(x, baseline), font, text)
    return f'<path d="{_path_data(path)}" fill="{fill}"/>'


def _app_svg() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    {_gradient_defs()}
    <radialGradient id="tile" cx="38%" cy="28%" r="90%">
      <stop offset="0%" stop-color="#202632"/>
      <stop offset="58%" stop-color="#141821"/>
      <stop offset="100%" stop-color="#0C0F15"/>
    </radialGradient>
  </defs>
  <rect x="32" y="32" width="960" height="960" rx="220" fill="url(#tile)"
        stroke="#343B4A" stroke-width="4"/>
  {_mark()}
</svg>
"""


def _transparent_mark_svg(stroke: str = "url(#brand)", width: int = 118) -> str:
    defs = _gradient_defs() if stroke == "url(#brand)" else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>{defs}</defs>
  {_mark(stroke, width)}
</svg>
"""


def _banner_svg() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 640">
  <defs>
    {_gradient_defs()}
    <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0A0D13"/>
      <stop offset="58%" stop-color="#151A24"/>
      <stop offset="100%" stop-color="#0B0E14"/>
    </linearGradient>
    <radialGradient id="aura" cx="25%" cy="50%" r="55%">
      <stop offset="0%" stop-color="#2688FF" stop-opacity="0.16"/>
      <stop offset="100%" stop-color="#2688FF" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1280" height="640" fill="url(#background)"/>
  <rect width="1280" height="640" fill="url(#aura)"/>
  <g transform="translate(82 92) scale(.445)">
    <rect x="32" y="32" width="960" height="960" rx="220" fill="#111620"
          stroke="#313949" stroke-width="4"/>
    {_mark()}
  </g>
  {_outlined_text("LumiSync", 580, 286, 108, "#F4F7FB", bold=True)}
  {_outlined_text("Screen. Sound. Light. In sync.", 587, 358, 31, "#AEB8CA", letter_spacing=1.2)}
  <rect x="587" y="402" width="390" height="7" rx="3.5" fill="url(#brand)"/>
</svg>
"""


def _preview_svg() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 1000">
  <defs>
    {_gradient_defs()}
    <linearGradient id="sheet" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0B0E14"/>
      <stop offset="100%" stop-color="#181E29"/>
    </linearGradient>
  </defs>
  <rect width="1600" height="1000" fill="url(#sheet)"/>
  {_outlined_text("LumiSync brand kit", 80, 96, 54, "#F4F7FB", bold=True)}
  {_outlined_text("One continuous mark, optimized for every surface.", 82, 144, 23, "#8995A8")}

  <rect x="80" y="205" width="500" height="500" rx="110" fill="#111620"
        stroke="#343B4A" stroke-width="3"/>
  <g transform="translate(80 205) scale(.488)">{_mark()}</g>
  {_outlined_text("App icon", 80, 756, 25, "#DCE3EE", bold=True)}
  {_outlined_text("Dark tile - full color", 80, 792, 20, "#8995A8")}

  <rect x="650" y="205" width="390" height="230" rx="36" fill="#F3F6FA"/>
  <g transform="translate(676 205) scale(.22)">{_mark()}</g>
  {_outlined_text("Transparent mark", 675, 470, 25, "#DCE3EE", bold=True)}

  <rect x="1090" y="205" width="430" height="230" rx="36" fill="#0A0D12"
        stroke="#303746" stroke-width="2"/>
  <g transform="translate(1124 218) scale(.20)">{_mark("#FFFFFF", 132)}</g>
  {_outlined_text("Tray - light", 1115, 470, 25, "#DCE3EE", bold=True)}

  <rect x="650" y="535" width="870" height="260" rx="42" fill="#EEF2F7"/>
  <g transform="translate(688 555) scale(.18)">{_mark("#111722", 132)}</g>
  <g transform="translate(980 582) scale(.13)">{_mark("#111722", 138)}</g>
  <g transform="translate(1215 612) scale(.095)">{_mark("#111722", 144)}</g>
  {_outlined_text("Small-scale silhouettes", 680, 760, 24, "#202735", bold=True)}

  {_outlined_text("Gradient  #14E8E8  >  #2688FF  >  #7D45F6  >  #ED2ACD", 80, 914, 21, "#8995A8")}
</svg>
"""


def _write_svg(name: str, source: str) -> Path:
    destination = SVG_DIR / name
    normalized = "\n".join(line.rstrip() for line in source.splitlines()) + "\n"
    destination.write_text(normalized, encoding="utf-8")
    return destination


def _render_svg(source: Path, destination: Path, width: int, height: int) -> None:
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG: {source}")

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()

    destination.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(destination), "PNG"):
        raise RuntimeError(f"Could not save {destination}")


def _build_ico(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.convert("RGBA").save(
            destination,
            format="ICO",
            sizes=[(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)],
        )


def main() -> None:
    SVG_DIR.mkdir(parents=True, exist_ok=True)
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication(["lumisync-brand-assets"])
    # The bundled Qt runtime starts with an empty font database on some Windows
    # machines. Load the project banner fonts explicitly before outlining text.
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for filename in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf"):
        font_path = fonts_dir / filename
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))

    app_svg = _write_svg("lumisync-app.svg", _app_svg())
    mark_svg = _write_svg("lumisync-mark-gradient.svg", _transparent_mark_svg())
    _write_svg("lumisync-mark-white.svg", _transparent_mark_svg("#FFFFFF", 126))
    _write_svg("lumisync-mark-dark.svg", _transparent_mark_svg("#111722", 126))
    tray_color_svg = _write_svg(
        "lumisync-tray-color.svg", _transparent_mark_svg("url(#brand)", 138)
    )
    tray_light_svg = _write_svg(
        "lumisync-tray-light.svg", _transparent_mark_svg("#FFFFFF", 138)
    )
    tray_dark_svg = _write_svg(
        "lumisync-tray-dark.svg", _transparent_mark_svg("#111722", 138)
    )
    banner_svg = _write_svg("lumisync-github-banner.svg", _banner_svg())
    preview_svg = _write_svg("lumisync-brand-sheet.svg", _preview_svg())

    app_dir = PNG_DIR / "app"
    for size in APP_SIZES:
        _render_svg(app_svg, app_dir / f"lumisync-app-{size}.png", size, size)

    mark_dir = PNG_DIR / "mark"
    for size in MARK_SIZES:
        _render_svg(mark_svg, mark_dir / f"lumisync-mark-{size}.png", size, size)

    for variant, source in (
        ("color", tray_color_svg),
        ("light", tray_light_svg),
        ("dark", tray_dark_svg),
    ):
        tray_dir = PNG_DIR / "tray" / variant
        for size in TRAY_SIZES:
            _render_svg(
                source,
                tray_dir / f"lumisync-tray-{variant}-{size}.png",
                size,
                size,
            )

    _render_svg(
        banner_svg,
        PNG_DIR / "social" / "lumisync-github-banner-1280x640.png",
        1280,
        640,
    )
    _render_svg(preview_svg, PNG_DIR / "lumisync-brand-sheet.png", 1600, 1000)
    _build_ico(app_dir / "lumisync-app-256.png", ICO_DIR / "lumisync.ico")

    print(f"Generated LumiSync brand assets in {BRAND_ROOT}")
    del app


if __name__ == "__main__":
    main()
