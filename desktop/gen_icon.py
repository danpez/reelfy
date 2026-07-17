#!/usr/bin/env python3
"""Generate Reelfy.icns: dark rounded square, brand-gradient play glyph."""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
OUT = Path(__file__).parent / "build"
OUT.mkdir(exist_ok=True)


def gradient(w, h, c1=(255, 90, 60), c2=(255, 176, 32)):
    """Diagonal linear gradient c1 -> c2."""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            t = (x + y) / (w + h - 2)
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))
    return im


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # macOS icon grid: content inset ~10%, corner radius ~22.5% of the shape
    m = round(S * 0.10)
    r = round((S - 2 * m) * 0.225)
    d.rounded_rectangle([m, m, S - m, S - m], radius=r, fill=(13, 16, 22, 255))

    # play triangle, brand gradient, slightly right of center (optical centering)
    tri = Image.new("L", (S, S), 0)
    td = ImageDraw.Draw(tri)
    cx, cy, w, h = S * 0.535, S * 0.5, S * 0.34, S * 0.40
    td.polygon([(cx - w / 2, cy - h / 2), (cx - w / 2, cy + h / 2), (cx + w / 2, cy)],
               fill=255)
    grad = gradient(S, S).convert("RGBA")
    img.paste(grad, (0, 0), tri)

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Reelfy.iconset"
        iconset.mkdir()
        for size in (16, 32, 64, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
                img.resize((px, px), Image.LANCZOS).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset),
                        "-o", str(OUT / "Reelfy.icns")], check=True)
    print(f"icns -> {OUT/'Reelfy.icns'}")


if __name__ == "__main__":
    sys.exit(main())
