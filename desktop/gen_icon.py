#!/usr/bin/env python3
"""Generate Reelfy.icns from the real brand mark (brand/reelfy-mark.png):
navy rounded square + coral R-play monogram, macOS icon grid."""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
HERE = Path(__file__).resolve().parent
MARK = HERE.parent / "brand/reelfy-mark.png"
OUT = HERE / "build"
OUT.mkdir(exist_ok=True)
NAVY = (20, 27, 51, 255)


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # macOS icon grid: content inset ~10%, corner radius ~22.5% of the shape
    m = round(S * 0.10)
    r = round((S - 2 * m) * 0.225)
    d.rounded_rectangle([m, m, S - m, S - m], radius=r, fill=NAVY)

    mark = Image.open(MARK).convert("RGBA")
    box = S - 2 * m
    mh = round(box * 0.58)
    mw = round(mh * mark.width / mark.height)
    mark = mark.resize((mw, mh), Image.LANCZOS)
    # optical centering: alpha centroid at canvas center (the R is top/left-heavy)
    import numpy as np
    al = np.asarray(mark.split()[-1]).astype(float)
    ys, xs = np.indices(al.shape)
    cx = (al * xs).sum() / al.sum() / al.shape[1]
    cy = (al * ys).sum() / al.sum() / al.shape[0]
    img.alpha_composite(mark, (round(S / 2 - cx * mw), round(S / 2 - cy * mh)))

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
