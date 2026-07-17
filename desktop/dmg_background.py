#!/usr/bin/env python3
"""Fondo de marca para el DMG de Reelfy (ventana 660x460).
Genera bg.png (1x) y bg@2x.png (2x) — luego tiffutil los une para retina."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BRAND = HERE.parent / "brand"
OUT = HERE / "build"
OUT.mkdir(exist_ok=True)

W, H = 660, 460
NAVY = (15, 20, 38)
CORAL = (255, 90, 60)
FAINT = (58, 66, 92)
TEXT = (200, 206, 224)
MUTED = (120, 128, 156)
# posiciones (en puntos) donde Finder pondrá los iconos (coincide con dmg_settings.py)
APP_XY = (172, 232)
APPS_XY = (488, 232)
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def render(scale):
    w, h = W * scale, H * scale
    img = Image.new("RGB", (w, h), NAVY)
    d = ImageDraw.Draw(img, "RGBA")

    # glow coral suave arriba
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([w * 0.15, -h * 0.55, w * 0.85, h * 0.45], fill=(255, 90, 60, 30))
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), (0, 0))
    d = ImageDraw.Draw(img, "RGBA")

    def f(path, size):
        return ImageFont.truetype(path, size * scale)

    def ctext(cx, y, s, font, fill):
        b = d.textbbox((0, 0), s, font=font)
        d.text((cx * scale - (b[2] - b[0]) / 2, y * scale), s, font=font, fill=fill)

    # wordmark de marca arriba
    wm = Image.open(BRAND / "reelfy-wordmark-dark.png").convert("RGBA")
    ww = int(196 * scale); wh = int(ww * wm.height / wm.width)
    wm = wm.resize((ww, wh), Image.LANCZOS)
    img.paste(wm, (int(w / 2 - ww / 2), int(46 * scale)), wm)

    ctext(W / 2, 108, "Instalar es arrastrar y soltar", f(FONT_B, 17), TEXT)

    # aros guía donde irá cada icono (Finder pone el nombre debajo, no lo dibujo yo)
    for (cx, cy) in (APP_XY, APPS_XY):
        r = 66 * scale
        d.ellipse([cx * scale - r, cy * scale - r, cx * scale + r, cy * scale + r],
                  outline=(*FAINT, 255), width=max(1, scale))

    # flecha coral de la app a Aplicaciones
    y = APP_XY[1] * scale
    x0, x1 = 262 * scale, 398 * scale
    d.line([(x0, y), (x1, y)], fill=(*CORAL, 255), width=4 * scale)
    ah = 11 * scale
    d.polygon([(x1 + ah, y), (x1 - ah * 0.4, y - ah), (x1 - ah * 0.4, y + ah)],
              fill=(*CORAL, 255))

    ctext(W / 2, 400, "Arrastra el ícono de Reelfy sobre Aplicaciones", f(FONT, 13), MUTED)
    return img


render(1).save(OUT / "dmg-bg.png")
render(2).save(OUT / "dmg-bg@2x.png")
print("fondos:", OUT / "dmg-bg.png", OUT / "dmg-bg@2x.png")
