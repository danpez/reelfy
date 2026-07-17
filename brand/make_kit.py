#!/usr/bin/env python3
"""Reelfy brand kit — regenerate every asset from brand/_source.png.

Extracts the mark via linear unmixing between the source's navy background and
coral foreground (soft alpha matte, watermark rejected by residual), then emits
flat recolored variants, icons and favicons. Run with the spike venv:
    ../spike/.venv/bin/python make_kit.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
SRC = HERE / "_source.png"

CORAL = (255, 90, 60)      # #FF5A3C — normalized to the product UI brand color
NAVY = (20, 27, 51)        # #141B33 — brand background
WHITE = (255, 255, 255)
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def extract_matte():
    a = np.asarray(Image.open(SRC).convert("RGB")).astype(float)
    bg = np.array([25.7, 32.7, 60.8])     # measured source navy
    fg = np.array([253.0, 93.8, 65.5])    # measured source coral
    axis = fg - bg
    alpha = ((a - bg) @ axis) / (axis @ axis)
    # residual off the bg->fg color line kills the grey watermark sparkle
    resid = np.linalg.norm(a - (bg + alpha[..., None] * axis), axis=-1)
    alpha = np.clip(alpha, 0, 1)
    alpha[resid > 60] = 0.0
    # flatten sensor noise: hard 0/1 outside the true antialiased edge band
    alpha[alpha < 0.12] = 0.0
    alpha[alpha > 0.92] = 1.0
    m = Image.fromarray((alpha * 255).astype(np.uint8), "L")
    m = m.filter(ImageFilter.MedianFilter(3))
    bbox = m.getbbox()
    return m.crop(bbox)


def solid(matte, rgb):
    out = Image.new("RGBA", matte.size, rgb + (255,))
    out.putalpha(matte)
    return out


def on_bg(matte, fg_rgb, bg_rgb, size=1024, mark_frac=0.62, radius_frac=0.0):
    img = Image.new("RGBA", (size, size), bg_rgb + (255,))
    mw = round(size * mark_frac)
    mh = round(mw * matte.height / matte.width)
    if mh > size * mark_frac:
        mh = round(size * mark_frac)
        mw = round(mh * matte.width / matte.height)
    mark = solid(matte.resize((mw, mh), Image.LANCZOS), fg_rgb)
    img.alpha_composite(mark, ((size - mw) // 2, (size - mh) // 2))
    if radius_frac > 0:
        r = round(size * radius_frac)
        m = Image.new("L", (size, size), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, size, size], radius=r, fill=255)
        img.putalpha(m)
    return img


def wordmark(matte, fg_rgb, text_rgb, height=512):
    mh = height
    mw = round(mh * matte.width / matte.height)
    mark = solid(matte.resize((mw, mh), Image.LANCZOS), fg_rgb)
    font = ImageFont.truetype(FONT, round(height * 0.72))
    tb = ImageDraw.Draw(mark).textbbox((0, 0), "Reelfy", font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    gap = round(height * 0.22)
    img = Image.new("RGBA", (mw + gap + tw + 8, height), (0, 0, 0, 0))
    img.alpha_composite(mark, (0, 0))
    d = ImageDraw.Draw(img)
    d.text((mw + gap - tb[0], (height - th) // 2 - tb[1]), "Reelfy",
           font=font, fill=text_rgb + (255,))
    return img


def main():
    matte = extract_matte()
    print(f"matte {matte.size}")

    # marks (transparent background)
    solid(matte, CORAL).save(HERE / "reelfy-mark.png")
    solid(matte, WHITE).save(HERE / "reelfy-mark-white.png")
    solid(matte, NAVY).save(HERE / "reelfy-mark-navy.png")

    # square icons
    on_bg(matte, CORAL, NAVY).save(HERE / "reelfy-icon.png")
    on_bg(matte, CORAL, WHITE).save(HERE / "reelfy-icon-light.png")
    on_bg(matte, WHITE, CORAL).save(HERE / "reelfy-icon-coral.png")
    on_bg(matte, CORAL, NAVY, radius_frac=0.225).save(HERE / "reelfy-icon-rounded.png")

    # wordmarks
    wordmark(matte, CORAL, WHITE).save(HERE / "reelfy-wordmark-dark.png")
    wordmark(matte, CORAL, NAVY).save(HERE / "reelfy-wordmark-light.png")

    # favicons / web icons
    icon = on_bg(matte, CORAL, NAVY)
    for s in (16, 32, 48, 180, 192, 512):
        name = "apple-touch-icon.png" if s == 180 else f"favicon-{s}.png" if s <= 48 else f"icon-{s}.png"
        icon.resize((s, s), Image.LANCZOS).save(HERE / name)
    icon.resize((48, 48), Image.LANCZOS).save(
        HERE / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("kit listo:", sorted(p.name for p in HERE.glob('*.png')))


if __name__ == "__main__":
    main()
