#!/usr/bin/env python3
"""Render the app icon into mac/UnrotMac/Assets.xcassets/AppIcon.appiconset.

The mark is "Unwind": a spiral that straightens into a line. It is drawn in
the same unit space as the menu-bar glyph (TrayGlyph.swift) and the SwiftUI
mark (UnwindMark.swift), so the three cannot drift apart:

    M50 50  a5 5 0 0 1 10 0      the innermost half turn
            a10 10 0 0 1 -20 0   the second, underneath
            a15 15 0 0 1 15 -15  a quarter turn up and over
            H92                  and out, straight

Small sizes drop turns rather than shrinking them to mush: 32 px keeps the
outer turn and the line, 16 px keeps only the hook and the line.

The shape follows Apple's macOS icon grid: an 824 px rounded square on a
1024 px canvas, with its drop shadow in the margin.

    uv run --with cairosvg --with pillow mac/Brand/make_icons.py

writes the ten PNGs and Contents.json, and app-icon.svg beside this script
(the 1024 master, for anything that wants a vector).
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import cairosvg
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "UnrotMac" / "Assets.xcassets" / "AppIcon.appiconset"

CANVAS = 1024
SHAPE = 824
MARGIN = (CANVAS - SHAPE) // 2

# Moss, top to bottom, and paper for the mark. Theme.swift's accent family.
TOP, BOTTOM, INK = "#3A6E53", "#264B38", "#F3EFE6"

PATHS = {
    "full": "M50 50 a5 5 0 0 1 10 0 a10 10 0 0 1 -20 0 a15 15 0 0 1 15 -15 H92",
    "medium": "M60 50 a10 10 0 0 1 -20 0 a15 15 0 0 1 15 -15 H92",
    "simple": "M40 50 a15 15 0 0 1 15 -15 H92",
}
# Stroke in mark units: heavier as turns are dropped, so the line holds up.
STROKES = {"full": 5.5, "medium": 7.5, "simple": 10.0}


def squircle(size: float, offset: float, n: float = 5.0, steps: int = 720) -> str:
    """A superellipse, close to Apple's continuous-corner icon shape."""
    r = size / 2
    c = offset + r
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        x = c + r * math.copysign(abs(ct) ** (2 / n), ct)
        y = c + r * math.copysign(abs(st) ** (2 / n), st)
        pts.append(f"{x:.2f},{y:.2f}")
    return "M" + " L".join(pts) + " Z"


def mark_transform() -> str:
    # The full mark spans x 40..92, y 35..60 in its units. Scale it to about
    # two thirds of the shape and centre it, nudged right a touch: the spiral
    # is the heavy end, so true centre reads as sitting left.
    s = 9.2
    tx = CANVAS / 2 - s * (40 + 92) / 2 + 6
    ty = CANVAS / 2 - s * (35 + 60) / 2
    return f"translate({tx:.2f} {ty:.2f}) scale({s})"


def svg(variant: str, shadow_space: bool = True) -> str:
    shape = squircle(SHAPE, MARGIN)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS}" height="{CANVAS}" viewBox="0 0 {CANVAS} {CANVAS}">
  <defs>
    <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{TOP}"/>
      <stop offset="1" stop-color="{BOTTOM}"/>
    </linearGradient>
    <linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.10"/>
      <stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <path d="{shape}" fill="url(#fill)"/>
  <path d="{shape}" fill="url(#sheen)"/>
  <g transform="{mark_transform()}">
    <path d="{PATHS[variant]}" fill="none" stroke="{INK}" stroke-width="{STROKES[variant]}"
          stroke-linecap="round" stroke-linejoin="round"/>
  </g>
</svg>
"""


def render(variant: str) -> Image.Image:
    """The 1024 master for a variant, with Apple's grid shadow under it."""
    png = cairosvg.svg2png(bytestring=svg(variant).encode(), output_width=CANVAS, output_height=CANVAS)
    icon = Image.open(io.BytesIO(png)).convert("RGBA")

    # Shadow: the shape's alpha, black at 30 %, 10 px down, 10 px blur.
    alpha = icon.getchannel("A")
    shadow = Image.new("RGBA", icon.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: int(a * 0.30)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    base = Image.new("RGBA", icon.size, (0, 0, 0, 0))
    base.alpha_composite(shadow, (0, 10))
    base.alpha_composite(icon)
    return base


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    masters = {v: render(v) for v in PATHS}

    images = []
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = points * scale
            variant = "simple" if px <= 16 else "medium" if px <= 32 else "full"
            name = f"icon_{points}x{points}{'' if scale == 1 else '@2x'}.png"
            masters[variant].resize((px, px), Image.LANCZOS).save(OUT / name, optimize=True)
            images.append({"filename": name, "idiom": "mac", "scale": f"{scale}x", "size": f"{points}x{points}"})

    (OUT / "Contents.json").write_text(
        json.dumps({"images": images, "info": {"author": "xcode", "version": 1}}, indent=2) + "\n"
    )
    (HERE / "app-icon.svg").write_text(svg("full"))
    print(f"wrote {len(images)} icons to {OUT.relative_to(HERE.parent.parent)}")


if __name__ == "__main__":
    main()
