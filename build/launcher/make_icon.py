"""Render the VAbk Studio app icon (an SVG, below) into a multi-resolution
Windows .ico -- no extra deps, uses the PyQt6 already in the .venv.

    .venv\\Scripts\\python.exe build\\launcher\\make_icon.py

Writes build\\VAbkStudio.ico with 16/24/32/48/64/128/256 px frames (each frame
is PNG-compressed, which Windows 10/11 read fine). Edit SVG below to restyle.
"""
import os
import struct
import sys

# "Visual Audiobook": a play glyph over karaoke caption bars (one highlighted),
# on a violet -> indigo rounded square.
SVG = """<svg width="1024" height="1024" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#8B5CF6"/>
      <stop offset="0.55" stop-color="#6366F1"/>
      <stop offset="1" stop-color="#4338CA"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="1024" height="1024" rx="230" ry="230" fill="url(#bg)"/>
  <!-- play triangle (rounded via a fat round-join stroke) -->
  <path d="M 398 300 L 398 600 L 678 450 Z" fill="#FFFFFF" stroke="#FFFFFF"
        stroke-width="74" stroke-linejoin="round"/>
  <!-- karaoke caption bars: middle word "highlighted" in amber -->
  <rect x="300" y="706" width="150" height="62" rx="31" fill="#FFFFFF" opacity="0.92"/>
  <rect x="480" y="706" width="224" height="62" rx="31" fill="#FBBF24"/>
  <rect x="734" y="706" width="86" height="62" rx="31" fill="#FFFFFF" opacity="0.55"/>
</svg>"""

SIZES = [16, 24, 32, 48, 64, 128, 256]


def render_png(svg_bytes, size):
    from PyQt6.QtCore import QByteArray, QBuffer
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(svg_bytes))
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(0)  # transparent
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(p)
    p.end()

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def build_ico(frames):
    """frames: list of (size, png_bytes) -> .ico bytes (PNG-compressed entries)."""
    count = len(frames)
    out = struct.pack("<HHH", 0, 1, count)  # ICONDIR: reserved, type=icon, count
    offset = 6 + count * 16
    blob = b""
    for size, png in frames:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO spec
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blob += png
    return out + blob


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication

    _app = QGuiApplication(sys.argv)  # needed for the imaging stack

    svg_bytes = SVG.encode("utf-8")
    frames = [(s, render_png(svg_bytes, s)) for s in SIZES]

    here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.normpath(os.path.join(here, "..", "VAbkStudio.ico"))
    with open(out_path, "wb") as f:
        f.write(build_ico(frames))

    print("wrote", out_path)
    print("frames:", ", ".join("%dpx=%dB" % (s, len(p)) for s, p in frames))


if __name__ == "__main__":
    main()
