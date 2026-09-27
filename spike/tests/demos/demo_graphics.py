"""Demo visual de motion/graphics.py y motion/cutaways.py.

Genera en spike/work/demos/graphics/:
  - doodles_*.png, icons.png, cards_*.png, lower_cta.png, cutaways_*.png: hojas de contacto
    (varios cuadros a lo largo de cada animacion, con el tiempo anotado) sobre cuadros reales
    del video de prueba.
  - composite.png: cuadros completos con varios graficos combinados (comparar con el objetivo).
  - montage.mp4: ~12 s de video real con graficos animados (opcional: --mp4).

Uso: cd spike/scripts && ../.venv/bin/python ../tests/demos/demo_graphics.py [--only doodles,cards] [--mp4]
Los cuadros reales se extraen de TEST_MEDIA/crudo_completo.mp4 (no se versionan).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve()
SPIKE = HERE.parents[2]
sys.path.insert(0, str(SPIKE / "scripts"))

import paths  # noqa: E402
import skia  # noqa: E402

from motion import assets, graphics  # noqa: E402
from motion import ctx as C  # noqa: E402

OUT = SPIKE / "work" / "demos" / "graphics"
BG_DIR = OUT / "_bg"


def _media_dir() -> Path:
    """Carpeta de medios de prueba: $REELFY_TEST_MEDIA o la config comun de tests (conftest)."""
    env = os.environ.get("REELFY_TEST_MEDIA")
    if env:
        return Path(env)
    sys.path.insert(0, str(SPIKE / "tests"))
    from conftest import TEST_MEDIA  # noqa: E402
    return Path(TEST_MEDIA)


MEDIA = _media_dir()
CRUDO = MEDIA / "crudo_completo.mp4"
W, H = 1080, 1920

_FACES: dict = {}


def face_of(t: str):
    """Cara (cx, cy, w, h normalizado) del cuadro de fondo, detectada con YuNet (como camera.py)."""
    if t in _FACES:
        return _FACES[t]
    import cv2
    f = BG_DIR / f"f_{t}.jpg"
    bg(t)
    im = cv2.resize(cv2.imread(str(f)), (540, 960))
    det = cv2.FaceDetectorYN.create(str(paths.YUNET), "", (540, 960), 0.6)
    _, faces = det.detect(im)
    r = None
    if faces is not None and len(faces):
        x, y, w, h = [float(v) for v in faces[0][:4]]
        r = ((x + w / 2) / 540, (y + h / 2) / 960, w / 540, h / 960)
    _FACES[t] = r
    return r

# Entidades del caso de prueba: se leen de spike/work/fixtures/demo_entities.json (ignorado por
# git; datos del cliente) y si no existe se usan placeholders neutros.
ENT = {"name": "Dra. Ana Ruiz", "role": "Dentista", "price": "$1,000 MXN",
       "address": "Av. Central 123", "landmark": "a 2 cuadras de la Glorieta Norte",
       "cta": "Revisa mis horarios en el link", "link_label": "Agenda tu cita", "handle": ""}
_EF = SPIKE / "work" / "fixtures" / "demo_entities.json"
if _EF.exists():
    try:
        ENT.update({k: v for k, v in json.loads(_EF.read_text()).items() if isinstance(v, str)})
    except Exception:
        pass
NAME, ROLE, ADDR, LAND = ENT["name"], ENT["role"], ENT["address"], ENT["landmark"]
_m = re.search(r"(\d+)\s*$", ADDR)
NUM = _m.group(1) if _m else ADDR

_BG: dict = {}


def bg(t: str) -> skia.Image:
    if t in _BG:
        return _BG[t]
    BG_DIR.mkdir(parents=True, exist_ok=True)
    f = BG_DIR / f"f_{t}.jpg"
    if not f.exists():
        subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-ss", t, "-i", str(CRUDO), "-frames:v", "1",
                        "-vf", f"scale={W}:{H}:flags=lanczos", "-q:v", "3", str(f)], check=True)
    arr = np.asarray(Image.open(f).convert("RGBA").resize((W, H)))
    _BG[t] = C.image_from_rgba(arr)
    return _BG[t]


def make_ctx(t: float, bgk: str | None, quality="final", style="dinamico", w=W, h=H):
    c = C.demo_ctx(W=w, H=h, t=t, quality=quality, style=style, entities=dict(ENT))
    if bgk is not None:
        c.face = face_of(bgk)
        img = bg(bgk)
        if (w, h) != (W, H):
            s = C.offscreen(w, h)
            s.getCanvas().drawImageRect(img, skia.Rect.MakeWH(w, h), C.SAMPLING)
            img = s.makeImageSnapshot()
        c._cache["base_image"] = img
    return c


def render(layers, t: float, bgk: str | None = "2.5", quality="final", style="dinamico", w=W, h=H,
           bg_color="#202024") -> np.ndarray:
    """layers: [(fn, item, start, dur)] -> RGBA np (h, w, 4)."""
    arr, surf = C.new_frame(w, h)
    cv = surf.getCanvas()
    ctx = make_ctx(t, bgk, quality, style, w, h)
    if bgk is not None:
        cv.drawImage(ctx.base_image(), 0, 0)
    else:
        cv.clear(C.color(bg_color))
    for fn, item, st, du in layers:
        lt = t - st
        if 0 <= lt < du:
            fn(cv, item, lt, du, ctx)
    return arr.copy()


_FONT = None


def _lab_font(size=22):
    global _FONT
    return ImageFont.truetype(str(assets.FONTS_DIR / "Poppins-SemiBold.ttf"), size)


def sheet(rows, out: Path, thumb: int = 240, title: str = ""):
    """rows: [(label, [(t, np_img_crop)])] -> PNG con etiquetas."""
    ncol = max(len(r[1]) for r in rows)
    lab_w = 150
    th0 = rows[0][1][0][1]
    ratio = th0.shape[0] / th0.shape[1]
    tw, thh = thumb, int(thumb * ratio)
    head = 40 if title else 0
    img = Image.new("RGB", (lab_w + ncol * (tw + 6) + 6, head + len(rows) * (thh + 6) + 6), (18, 18, 22))
    d = ImageDraw.Draw(img)
    if title:
        d.text((10, 6), title, fill=(240, 240, 240), font=_lab_font(24))
    for r, (label, frames) in enumerate(rows):
        y = head + 6 + r * (thh + 6)
        d.text((8, y + thh // 2 - 14), label, fill=(230, 230, 230), font=_lab_font(19))
        for c, (t, fr) in enumerate(frames):
            x = lab_w + 6 + c * (tw + 6)
            im = Image.fromarray(fr[..., :3]).resize((tw, thh), Image.LANCZOS)
            img.paste(im, (x, y))
            d.rectangle([x, y, x + 62, y + 22], fill=(0, 0, 0))
            d.text((x + 4, y + 1), f"{t:.2f}s", fill=(255, 215, 64), font=_lab_font(16))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print("->", out)


def crop(arr, cx, cy, side):
    x0 = int(np.clip(cx - side / 2, 0, arr.shape[1] - side))
    y0 = int(np.clip(cy - side / 2, 0, arr.shape[0] - side))
    return arr[y0:y0 + side, x0:x0 + side]


# ---------------------------------------------------------------- casos
def doodle_cases():
    D = graphics.draw_doodle
    base = {"sparkle": dict(x=0.5, y=0.45), "star4": dict(), "star5": dict(), "burst": dict(),
            "rays": dict(), "arrow_curve": dict(), "arrow_straight": dict(), "arrow_down": dict(),
            "circle_scribble": dict(size=0.45), "underline": dict(double=True), "squiggle": dict(),
            "dotted_path": dict(), "heart": dict(), "check": dict(), "cross": dict(), "exclaim": dict(),
            "question": dict(), "swirl": dict(), "lightning": dict(), "loop": dict()}
    out = []
    for name, extra in base.items():
        it = {"id": f"d_{name}", "kind": "doodle", "name": name, "x": 0.5, "y": 0.45}
        it.update(extra)
        out.append((name, it))
    return D, out


def run_doodles():
    D, cases = doodle_cases()
    times = [0.08, 0.18, 0.3, 0.45, 0.8, 1.3, 1.9]
    dur = 2.0
    bgs = ["2.5", "15", "31", "19.5"]
    for part in range(4):
        rows = []
        for i, (name, it) in enumerate(cases[part * 5:(part + 1) * 5]):
            bgk = bgs[(part * 5 + i) % len(bgs)]
            frames = []
            for t in times:
                a = render([(D, it, 0.0, dur)], t, bgk)
                frames.append((t, crop(a, it["x"] * W, it["y"] * H, 560)))
            rows.append((name, frames))
        sheet(rows, OUT / f"doodles_{part + 1}.png", thumb=210, title=f"doodles {part + 1}/4 (dur {dur}s)")


def run_doodle_paths():
    """Flechas y punteados con path_to sobre un cuadro completo (uso real: senalar algo)."""
    D = graphics.draw_doodle
    items = [
        {"id": "p1", "name": "arrow_curve", "x": 0.12, "y": 0.6, "path_to": [0.3, 0.38], "stroke": 12},
        {"id": "p2", "name": "dotted_path", "x": 0.85, "y": 0.68, "path_to": [0.62, 0.42]},
        {"id": "p3", "name": "rays", "anchor": "face_right", "size": 0.2, "rotation": 60},
        {"id": "p4", "name": "sparkle", "x": 0.22, "y": 0.2, "size": 0.12},
        {"id": "p5", "name": "circle_scribble", "anchor": "face", "size": 0.24, "aspect": 0.95},
    ]
    times = [0.15, 0.35, 0.6, 1.0, 1.6]
    frames = []
    for t in times:
        a = render([(D, it, 0.0, 2.2) for it in items], t, "2.5")
        frames.append((t, a))
    sheet([("paths", frames)], OUT / "doodles_paths.png", thumb=300, title="flechas path_to / anchor cara")


def run_sketch():
    """Estilo sketch (contorno hueco + rayado) vs brush para garabatos grandes."""
    D = graphics.draw_doodle
    rows = []
    for name, extra in (("arrow_curve", {}), ("arrow_down", {"size": 0.3}), ("star5", {"size": 0.3}),
                        ("sparkle", {"size": 0.22}), ("heart", {"size": 0.24}), ("lightning", {"size": 0.24})):
        frames = []
        for style, bgk in (("brush", "7"), ("sketch", "7"), ("sketch", None)):
            it = dict({"id": f"sk_{name}", "name": name, "x": 0.5, "y": 0.45, "style": style}, **extra)
            for t in (0.25, 1.2):
                a = render([(D, it, 0.0, 2.0)], t, bgk, bg_color="#0B0B0E")
                frames.append((t, crop(a, 0.5 * W, 0.45 * H, 520)))
        rows.append((name, frames))
    sheet(rows, OUT / "doodles_sketch.png", thumb=170,
          title="brush (2 cuadros) | sketch sobre video (2) | sketch sobre oscuro (2)")


def run_icons():
    I = graphics.draw_icon
    cases = [
        ("dental sq", {"id": "i1", "name": "dental", "badge": "squircle", "size": 0.2}, "2.5"),
        ("map-pin circ", {"id": "i2", "name": "map-pin", "badge": "circle", "size": 0.2,
                          "badge_color": "#FF4D5E"}, "19.5"),
        ("clock clean", {"id": "i3", "name": "clock", "size": 0.22}, "9"),
        ("thumb sketch", {"id": "i4", "name": "thumb-up", "style": "sketch", "size": 0.26}, None),
        ("cash filled", {"id": "i5", "name": "cash", "variant": "filled", "badge": "circle",
                         "badge_color": "#2EC4B6", "size": 0.2}, "15"),
        ("calendar dark", {"id": "i6", "name": "calendar-event", "badge": "squircle",
                           "badge_color": "#16161C", "color": "#FFD23F", "size": 0.2}, "31"),
    ]
    times = [0.06, 0.14, 0.26, 0.45, 0.8, 1.6]
    rows = []
    for label, it, bgk in cases:
        it = dict(it, x=0.5, y=0.45)
        frames = [(t, crop(render([(I, it, 0.0, 2.0)], t, bgk, bg_color="#0B0B0E"), 540, 0.45 * H, 420))
                  for t in times]
        rows.append((label, frames))
    sheet(rows, OUT / "icons.png", thumb=200, title="icons (Tabler): badge / clean / sketch / filled")


CARDS = [
    ("price", {"variant": "price", "title": "Limpieza dental", "subtitle": "Costo por sesion",
               "value": "$1,000 MXN"}, "15"),
    ("location", {"variant": "location", "title": ADDR,
                  "subtitle": LAND}, "19.5"),
    ("schedule", {"variant": "schedule", "title": "Horarios disponibles", "subtitle": "Checa el link aqui abajo",
                  "label": "Agenda tu cita"}, "31"),
    ("link", {"variant": "link", "title": "Agenda tu cita", "subtitle": "El link esta aqui abajo",
              "label": "Agenda aqui"}, "35"),
    ("checklist", {"variant": "checklist", "title": "Una buena limpieza", "items":
                   ["Se sienten lisos", "Se ven mas blancos", "Sin sarro"]}, "9"),
    ("generic", {"variant": "generic", "icon": "dental", "title": NAME, "subtitle": ROLE,
                 "value": "Consultorio dental"}, "2.5"),
    ("price dark", {"variant": "price", "theme": "dark", "title": "Limpieza dental", "value": "$1,000 MXN",
                    "anim_in": "flip"}, "15"),
    ("location dark", {"variant": "location", "theme": "dark", "title": ADDR,
                       "subtitle": LAND, "anim_in": "pop"}, "22.5"),
    ("price brand", {"variant": "price", "theme": "brand", "title": "Limpieza dental", "value": "$1,000 MXN",
                     "anim_in": "pop"}, "9"),
    ("phone", {"variant": "phone", "title": "Escribeme por WhatsApp", "value": "33 1234 5678"}, "2.5"),
    ("rating", {"variant": "rating", "value": "4.9", "subtitle": "Opiniones de pacientes"}, "15"),
    ("stat", {"variant": "stat", "value": "1,000", "title": "pesos la limpieza", "progress": 0.7,
              "theme": "dark"}, "19.5"),
]


def run_cards():
    Cd = graphics.draw_card
    times = [0.1, 0.25, 0.45, 0.8, 1.4, 2.6]
    for part in range(3):
        rows = []
        for label, extra, bgk in CARDS[part * 4:(part + 1) * 4]:
            it = {"id": f"c_{label}", "kind": "card", "x": 0.5, "y": 0.52, "size": 0.8}
            it.update(extra)
            frames = [(t, crop(render([(Cd, it, 0.0, 3.0)], t, bgk), 540, 0.52 * H, 1080)) for t in times]
            rows.append((label, frames))
        sheet(rows, OUT / f"cards_{part + 1}.png", thumb=250, title=f"cards {part + 1}/3 (dur 3.0s)")


def run_lower_cta():
    G = graphics.RENDERERS
    cases = [
        ("lower third", G["lower_third"], {"id": "lt1", "title": NAME, "subtitle": ROLE},
         "2.5", 3.0, (0.35, 0.70), 700),
        ("lower light", G["lower_third"], {"id": "lt2", "title": NAME, "subtitle": ROLE,
                                           "theme": "light", "y": 0.66}, "19.5", 3.0, (0.35, 0.66), 700),
        ("cta follow", G["cta"], {"id": "cf", "variant": "follow", "label": NAME,
                                  "subtitle": ROLE, "y": 0.62}, "35", 3.0, (0.47, 0.62), 760),
        ("cta link_below", G["cta"], {"id": "cl", "variant": "link_below", "label": "Agenda aqui",
                                      "y": 0.66}, "31", 3.0, (0.47, 0.71), 760),
        ("cta comment", G["cta"], {"id": "cc", "variant": "comment", "label": "Comenta LIMPIEZA",
                                   "y": 0.6}, "9", 3.0, (0.47, 0.6), 700),
        ("cta save", G["cta"], {"id": "cs", "variant": "save", "y": 0.6}, "15", 3.0, (0.47, 0.6), 700),
        ("cta share", G["cta"], {"id": "csh", "variant": "share", "y": 0.6}, "22.5", 3.0, (0.47, 0.6), 700),
        ("sticker", G["sticker_text"], {"id": "st", "text": "$1,000", "y": 0.3}, "15", 2.0, (0.47, 0.3), 560),
        ("sticker dark", G["sticker_text"], {"id": "st2", "text": "SIN DOLOR", "bg": "#16161C", "y": 0.3},
         "9", 2.0, (0.47, 0.3), 560),
    ]
    times = [0.1, 0.3, 0.6, 1.0, 1.3, 2.0, 2.85]
    rows = []
    for label, fn, it, bgk, du, (cx, cy), side in cases:
        frames = [(t, crop(render([(fn, it, 0.0, du)], min(t, du - 0.01), bgk), cx * W, cy * H, side))
                  for t in times if t < du + 0.5]
        rows.append((label, frames[:7]))
    sheet(rows[:5], OUT / "lower_cta_1.png", thumb=210, title="lower third + CTA")
    sheet(rows[5:], OUT / "lower_cta_2.png", thumb=210, title="CTA + sticker")
    # progreso: cuadro completo
    frames = [(t, render([(G["progress"], {"id": "pg"}, 0.0, 10.0)], t, "2.5")[:220]) for t in (0.2, 2.5, 6.0, 9.5)]
    sheet([("progress", frames)], OUT / "progress.png", thumb=420, title="progress (franja superior)")


def run_cutaways():
    from motion import cutaways as CU
    pal = assets.palette("dinamico")
    director_cols = [pal["dark"], pal["accent2"]]  # lo que manda el director a TODOS los fondos
    times = [0.05, 0.4, 1.0, 1.8, 2.8]
    rows = []
    for kind in CU.BACKGROUNDS:
        for label, cols in ((kind, None), (f"{kind} [dir]", director_cols)):
            frames = []
            for t in times:
                arr, surf = C.new_frame(540, 960)
                ctx = make_ctx(t, "9", w=540, h=960)
                cv = surf.getCanvas()
                cv.drawImage(ctx.base_image(), 0, 0)
                CU.draw_background(cv, kind, cols, t, ctx)
                frames.append((t, arr.copy()))
            rows.append((label, frames))
    sheet(rows[:8], OUT / "cutaways_bg_1.png", thumb=170,
          title="fondos 1/2: default y con bg_colors del director [oscuro, acento] (draft 540p)")
    sheet(rows[8:], OUT / "cutaways_bg_2.png", thumb=170,
          title="fondos 2/2: default y con bg_colors del director [oscuro, acento] (draft 540p)")
    # grid_warp por defecto (oscuro con lineas blancas) a resolucion completa
    R = CU.RENDERERS
    scenes = [
        ("title grid", R["title_card"], {"id": "tc1", "background": "grid_warp",
                                         "text": {"preset": "title_pop", "text": "Necesitamos\nhablar.",
                                                  "accent_words": ["hablar."], "size": 0.13}}, "7", 3.0),
        ("title stars", R["title_card"], {"id": "tc2", "background": "dark_stars", "anim_in": "circle",
                                          "text": {"preset": "title_pop", "text": "Una buena limpieza",
                                                   "size": 0.1, "y": 0.5},
                                          "graphics": [{"kind": "doodle", "name": "sparkle", "x": 0.78, "y": 0.36,
                                                        "offset": 0.4},
                                                       {"kind": "doodle", "name": "underline", "x": 0.5,
                                                        "y": 0.585, "size": 0.62, "offset": 0.7,
                                                        "color": "#FFD23F"}]}, "9", 3.0),
        ("info price", R["info_card"], {"id": "ic1", "background": "gradient", "variant": "price",
                                        "title": "Limpieza dental", "subtitle": "Costo por sesion",
                                        "value": "$1,000 MXN", "text": "Tiene un costo de"}, "15", 3.0),
        ("info location", R["info_card"], {"id": "ic2", "background": "blur_self", "variant": "location",
                                           "title": ADDR,
                                           "subtitle": LAND,
                                           "anim_in": "slide_up"}, "19.5", 3.0),
        ("photo", R["photo"], {"id": "ph", "background": "paper", "caption": ADDR}, "25", 3.0),
        ("self_broll", R["self_broll"], {"id": "sb", "zoom_from": 1.0, "zoom_to": 1.35,
                                         "text": {"preset": "title_pop", "text": NUM, "size": 0.2,
                                                  "y": 0.72}}, "25", 3.0),
        ("title halftone", R["title_card"], {"id": "tc3", "background": "halftone", "anim_in": "slide_up",
                                             "anim_out": "slide_down",
                                             "text": {"preset": "title_pop", "text": "Revisa mis horarios",
                                                      "size": 0.1}}, "31", 3.0),
        ("title rays", R["title_card"], {"id": "tc4", "background": "rays", "anim_in": "zoom",
                                         "text": {"preset": "title_pop", "text": "$1,000", "size": 0.2,
                                                  "color": "#15151B"}}, "15", 3.0),
    ]
    times = [0.05, 0.2, 0.45, 0.9, 1.6, 2.85]
    rows = []
    for label, fn, it, bgk, du in scenes:
        frames = [(t, render([(fn, it, 0.0, du)], t, bgk)) for t in times]
        rows.append((label, frames))
    sheet(rows[:4], OUT / "cutaways_1.png", thumb=190, title="cutaways 1/2 (sobre cuadro real; anim_in)")
    sheet(rows[4:], OUT / "cutaways_2.png", thumb=190, title="cutaways 2/2")


def run_fixes():
    """Evidencia de las correcciones con items tal cual los arma el director."""
    from motion import cutaways as CU
    G, R = graphics.RENDERERS, CU.RENDERERS
    pal = assets.palette("dinamico")
    dcols = [pal["dark"], pal["accent2"]]
    card = {"kind": "card", "variant": "location", "title": ADDR, "subtitle": LAND, "value": "", "icon": "map-pin",
            "theme": "light", "color": "#FFFFFF", "anim_in": "rise", "anim_out": "fade", "rotation": 0.0,
            "items": [], "x": 0.5, "y": 0.47, "size": 0.8}
    rows = []
    info = {"id": "fx_info", "kind": "info_card", "background": "blur_self", "bg_colors": dcols, "graphics": [card]}
    rows.append(("info_card dir", [(t, render([(R["info_card"], info, 0.0, 2.6)], t, "19.5"))
                                   for t in (0.05, 0.3, 0.8, 1.6, 2.5)]))
    fol = {"id": "fx_fol", "kind": "cta", "variant": "follow", "label": "Sigueme", "handle": "", "x": 0.47,
           "y": 0.6, "size": 0.62, "color": pal["accent"], "anim_in": "pop", "anim_out": "fade", "rotation": 0.0}
    rows.append(("follow dir", [(t, render([(G["cta"], fol, 0.0, 2.8)], t, "35")) for t in (0.2, 0.6, 1.2, 1.9, 2.75)]))
    lb = {"id": "fx_lb", "kind": "cta", "variant": "link_below", "label": "Agenda tu cita", "x": 0.47, "y": 0.6,
          "size": 0.46, "color": pal["accent"], "anim_in": "pop", "anim_out": "fade", "rotation": 0.0}
    rows.append(("link fade out", [(t, render([(G["cta"], lb, 0.0, 3.0)], t, "31"))
                                   for t in (1.0, 2.72, 2.82, 2.9, 2.97)]))
    lt3 = {"id": "fx_lt", "kind": "lower_third", "title": NAME,
           "subtitle": "Cirujana dentista especialista en ortodoncia y estetica dental", "x": 0.07, "y": 0.66,
           "size": 0.056, "color": pal["accent"], "anim_in": "slide_left", "anim_out": "fade"}
    rows.append(("lower long", [(t, render([(G["lower_third"], lt3, 0.0, 3.0)], t, "2.5"))
                                for t in (0.1, 0.3, 0.8, 2.75, 2.95)]))
    over = [(G["card"], {"id": "fx_ch", "variant": "schedule", "title": "Horarios disponibles",
                         "subtitle": "https://beacons.ai/consultorio-dental-agenda-en-linea",
                         "items": ["Lunes a viernes de 9:00 a 19:00 horas, sabados de 9:00 a 14:00", "Domingo"],
                         "y": 0.4, "size": 0.74}, 0.0, 3.0),
            (G["sticker_text"], {"id": "fx_st", "text": "PROMOCION DE TEMPORADA LIMITADA", "y": 0.72}, 0.0, 3.0)]
    rows.append(("desbordes", [(t, render(over, t, "31")) for t in (0.2, 0.5, 1.0, 1.6, 2.5)]))
    sheet(rows, OUT / "fixes_director.png", thumb=200,
          title="correcciones con items del director (info_card sin tarjeta fantasma, follow, fade, desbordes)")
    # escenas de titulo con la forma exacta del director (_scene): bg_colors [oscuro, acento], texto blanco
    rows = []
    for bgk in ("grid_warp", "dark_stars", "rays", "paper", "halftone", "gradient"):
        it = {"id": f"fx_sc_{bgk}", "kind": "title_card", "background": bgk, "bg_colors": dcols,
              "text": {"preset": "stack", "lines": [{"text": "NECESITAMOS HABLAR", "scale": 1.0, "font": "Anton"}],
                       "x": 0.5, "y": 0.46, "size": 0.12, "font": "Anton", "color": "#FFFFFF",
                       "accent": pal["accent"], "anim_in": "word_stagger", "align": "center"},
              "graphics": [{"kind": "doodle", "name": "sparkle", "x": 0.78, "y": 0.38, "size": 0.1,
                            "color": pal["accent"], "draw": True, "anim_in": "draw"}]}
        rows.append((bgk, [(t, render([(R["title_card"], it, 0.0, 2.0)], t, "7")) for t in (0.1, 0.5, 1.0, 1.9)]))
    sheet(rows[:3], OUT / "fixes_scenes_1.png", thumb=230, title="escenas del director 1/2 (bg_colors [oscuro, acento])")
    sheet(rows[3:], OUT / "fixes_scenes_2.png", thumb=230, title="escenas del director 2/2 (bg_colors [oscuro, acento])")
    # sketch: contorno unico (Simplify) a 1:1
    rows = []
    for name, extra in (("star5", {"size": 0.34}), ("arrow_curve", {"size": 0.4}), ("arrow_down", {"size": 0.3}),
                        ("heart", {"size": 0.28})):
        it = dict({"id": f"fx_sk_{name}", "name": name, "x": 0.5, "y": 0.45, "style": "sketch"}, **extra)
        frames = []
        for bgk in (None, "7"):
            for t in (0.3, 1.2):
                a = render([(G["doodle"], it, 0.0, 2.0)], t, bgk, bg_color="#0B0B0E")
                frames.append((t, crop(a, 0.5 * W, 0.45 * H, 480)))
        rows.append((name, frames))
    sheet(rows, OUT / "fixes_sketch.png", thumb=240, title="sketch con contorno unico (oscuro | video), crop 1:1")


def run_perf():
    """ms por llamada (promedio a lo largo de la animacion) en final 1080x1920 y draft 540x960."""
    from motion import cutaways as CU
    G = graphics.RENDERERS
    cases = [("doodle sparkle", G["doodle"], {"id": "a", "name": "sparkle"}),
             ("doodle arrow_curve", G["doodle"], {"id": "b", "name": "arrow_curve"}),
             ("doodle circle", G["doodle"], {"id": "c", "name": "circle_scribble", "size": 0.5}),
             ("doodle dotted", G["doodle"], {"id": "d", "name": "dotted_path"}),
             ("doodle burst", G["doodle"], {"id": "e", "name": "burst"}),
             ("icon badge", G["icon"], {"id": "f", "name": "dental", "badge": "squircle"}),
             ("icon sketch", G["icon"], {"id": "g", "name": "thumb-up", "style": "sketch"}),
             ("card price", G["card"], {"id": "h", "variant": "price", "title": "Limpieza dental",
                                        "value": "$1,000 MXN"}),
             ("card location", G["card"], {"id": "i", "variant": "location", "title": ADDR,
                                           "subtitle": LAND}),
             ("card schedule", G["card"], {"id": "j", "variant": "schedule", "label": "Agenda tu cita"}),
             ("lower_third", G["lower_third"], {"id": "k", "title": NAME, "subtitle": ROLE}),
             ("cta follow", G["cta"], {"id": "l", "variant": "follow", "label": NAME}),
             ("cta link_below", G["cta"], {"id": "m", "variant": "link_below"}),
             ("progress", G["progress"], {"id": "n"}),
             ("sticker", G["sticker_text"], {"id": "o", "text": "$1,000"})]
    for kind in CU.BACKGROUNDS:
        cases.append((f"bg {kind}", lambda cv, it, lt, du, ctx: CU.draw_background(cv, it["bg"], None, lt, ctx),
                      {"id": "bg", "bg": kind}))
    for kind in CU.KINDS:
        cases.append((f"cut {kind}", CU.RENDERERS[kind], {"id": "cut", "text": "Necesitamos hablar.",
                                                          "variant": "price", "value": "$1,000 MXN"}))
    print(f"{'renderer':24s} {'final ms':>9s} {'draft ms':>9s}")
    res = {}
    for label, fn, it in cases:
        row = []
        for (w, h, q) in ((W, H, "final"), (540, 960, "draft")):
            arr, surf = C.new_frame(w, h)
            cv = surf.getCanvas()
            ctx = make_ctx(0.0, "9", q, w=w, h=h)
            base = ctx.base_image()
            fn(cv, it, 0.5, 3.0, ctx)  # calentar caches
            ts = []
            for i in range(24):
                t = 0.05 + i * 0.12
                ctx.t, ctx.n = t, int(t * 30)
                cv.drawImage(base, 0, 0)
                t0 = time.perf_counter()
                fn(cv, it, t, 3.0, ctx)
                ts.append((time.perf_counter() - t0) * 1000)
            row.append(float(np.mean(ts)))
        res[label] = row
        print(f"{label:24s} {row[0]:9.2f} {row[1]:9.2f}")
    return res


def run_composite():
    """Cuadros completos con varios graficos combinados (el 'despues' de cada momento del guion)."""
    from motion import cutaways as CU
    G = graphics.RENDERERS
    scenes = [
        ("hook", "2.5", 1.4, [
            (G["doodle"], {"id": "k1", "name": "circle_scribble", "anchor": "face", "size": 0.25, "aspect": 0.95}, 0.0),
            (G["doodle"], {"id": "k2", "name": "rays", "anchor": "face_right", "size": 0.16, "rotation": 70}, 0.25),
            (G["doodle"], {"id": "k3", "name": "question", "x": 0.2, "y": 0.2, "size": 0.12}, 0.4),
            (G["lower_third"], {"id": "k4", "title": NAME, "subtitle": ROLE, "y": 0.66}, 0.0),
        ]),
        ("limpieza", "9", 1.5, [
            (G["doodle"], {"id": "l1", "name": "sparkle", "x": 0.62, "y": 0.27, "size": 0.12}, 0.0),
            (G["doodle"], {"id": "l2", "name": "sparkle", "x": 0.2, "y": 0.4, "size": 0.08}, 0.2),
            (G["icon"], {"id": "l3", "name": "dental", "badge": "squircle", "x": 0.78, "y": 0.5, "size": 0.16}, 0.1),
            (G["doodle"], {"id": "l4", "name": "arrow_curve", "x": 0.74, "y": 0.62, "path_to": [0.5, 0.42]}, 0.3),
        ]),
        ("precio", "15", 1.6, [
            (G["card"], {"id": "p1", "variant": "price", "title": "Limpieza dental", "subtitle": "Costo por sesion",
                         "value": "$1,000 MXN", "y": 0.62}, 0.0),
            (G["doodle"], {"id": "p2", "name": "rays", "x": 0.9, "y": 0.535, "size": 0.12, "rotation": 45}, 0.9),
        ]),
        ("ubicacion", "19.5", 1.8, [
            (G["card"], {"id": "u1", "variant": "location", "title": ADDR,
                         "subtitle": LAND, "y": 0.64}, 0.0),
            (G["doodle"], {"id": "u2", "name": "sparkle", "anchor": "face_left", "size": 0.1}, 0.3),
        ]),
        ("recursos", None, 1.3, [
            (lambda cv, it, lt, du, ctx: CU.draw_background(cv, "color", ["#0B0B0E"], lt, ctx), {}, 0.0),
            (G["icon"], {"id": "r1", "name": "thumb-up", "style": "sketch", "x": 0.28, "y": 0.22, "size": 0.2}, 0.0),
            (G["icon"], {"id": "r2", "name": "crop", "style": "sketch", "x": 0.68, "y": 0.16, "size": 0.14}, 0.15),
            (G["doodle"], {"id": "r3", "name": "circle_scribble", "x": 0.66, "y": 0.33, "size": 0.26,
                           "aspect": 0.95}, 0.2),
            (G["doodle"], {"id": "r4", "name": "cross", "x": 0.66, "y": 0.33, "size": 0.1}, 0.45),
            (G["doodle"], {"id": "r5", "name": "dotted_path", "x": 0.92, "y": 0.55, "path_to": [0.08, 0.5],
                           "bend": 0.35, "shadow": 0}, 0.1),
        ]),
        ("horarios", "31", 2.6, [
            (G["card"], {"id": "h1", "variant": "schedule", "title": "Horarios disponibles",
                         "subtitle": "Checa el link aqui abajo", "y": 0.5}, 0.0),
            (G["cta"], {"id": "h2", "variant": "link_below", "label": "Agenda aqui", "y": 0.68}, 0.4),
        ]),
        ("seguir", "35", 1.6, [
            (G["cta"], {"id": "s1", "variant": "follow", "label": NAME, "subtitle": ROLE,
                        "y": 0.62}, 0.0),
            (G["sticker_text"], {"id": "s2", "text": "SIGUEME", "y": 0.72}, 0.9),
        ]),
    ]
    frames = []
    for label, bgk, t, layers in scenes:
        a = render([(fn, it, st, 5.0) for fn, it, st in layers], t, bgk, bg_color="#0B0B0E")
        frames.append((t, a))
    sheet([("despues", frames)], OUT / "composite.png", thumb=300,
          title="composicion por momento del guion (hook, limpieza, precio, ubicacion, recursos, horarios, seguir)")


def _montage_spec():
    """Segmentos reales del crudo (inicio, duracion) con los graficos del guion de la dentista."""
    G = graphics.RENDERERS
    from motion import cutaways as CU
    R = CU.RENDERERS
    return [
        # gancho con pregunta
        (1.0, 3.4, [
            (G["lower_third"], {"id": "m_lt", "title": NAME, "subtitle": ROLE, "y": 0.66},
             0.15, 3.1),
            (G["doodle"], {"id": "m_q", "name": "question", "x": 0.72, "y": 0.16, "size": 0.13}, 1.7, 1.6),
            (G["doodle"], {"id": "m_r", "name": "rays", "x": 0.33, "y": 0.17, "size": 0.16, "rotation": -40},
             1.9, 1.4),
        ]),
        # "Necesitamos hablar." (escena a pantalla completa)
        (6.4, 1.2, [
            (R["title_card"], {"id": "m_tc", "background": "grid_warp", "bg_colors": ["#0E0E12", "#2EC4B6"],
                               "text": {"preset": "title_pop", "text": "Necesitamos\nhablar.",
                                        "accent_words": ["hablar."], "size": 0.13, "color": "#FFFFFF",
                                        "accent": "#FFD23F"},
                               "graphics": [{"kind": "doodle", "name": "exclaim", "x": 0.83, "y": 0.33,
                                             "offset": 0.35, "color": "#FFD23F"}]}, 0.0, 1.2),
        ]),
        # beneficio
        (8.3, 2.6, [
            (G["doodle"], {"id": "m_s1", "name": "sparkle", "x": 0.8, "y": 0.2, "size": 0.14}, 0.2, 2.3),
            (G["doodle"], {"id": "m_s2", "name": "sparkle", "x": 0.26, "y": 0.3, "size": 0.09}, 0.5, 2.0),
            (G["icon"], {"id": "m_ic", "name": "dental", "badge": "squircle", "x": 0.2, "y": 0.62, "size": 0.17},
             0.6, 1.9),
            (G["doodle"], {"id": "m_sk", "name": "arrow_curve", "style": "sketch", "x": 0.3, "y": 0.5,
                           "path_to": [0.2, 0.56], "bend": -0.5}, 0.9, 1.6),
        ]),
        # precio
        (14.4, 3.0, [
            (G["card"], {"id": "m_price", "variant": "price", "title": "Limpieza dental",
                         "subtitle": "Costo por sesion", "value": "$1,000 MXN", "y": 0.6}, 0.2, 2.7),
            (G["doodle"], {"id": "m_ar", "name": "arrow_curve", "x": 0.2, "y": 0.4, "path_to": [0.33, 0.5],
                           "size": 0.2}, 1.2, 1.5),
        ]),
        # frase de ubicacion (direccion + punto de referencia)
        (18.2, 4.6, [
            # escena de tarjeta tal cual la arma el director (tarjeta dentro de graphics)
            (R["info_card"], {"id": "m_ic", "background": "blur_self", "bg_colors": ["#0E0E12", "#2EC4B6"],
                              "anim_in": "zoom", "anim_out": "fade",
                              "graphics": [{"kind": "card", "variant": "location", "title": ADDR,
                                            "subtitle": LAND, "icon": "map-pin", "theme": "light",
                                            "color": "#FFFFFF", "anim_in": "rise", "anim_out": "fade",
                                            "x": 0.5, "y": 0.47, "size": 0.8}]}, 0.2, 2.8),
            (G["doodle"], {"id": "m_ar2", "name": "dotted_path", "x": 0.2, "y": 0.72, "path_to": [0.62, 0.6],
                           "bend": 0.3}, 3.0, 1.6),
        ]),
        # fachada: numero de la direccion
        (24.6, 1.8, [
            (R["photo"], {"id": "m_ph", "background": "paper", "caption": ADDR}, 0.0, 1.8),
        ]),
        # "Revisa mis horarios disponibles en el link de mi perfil"
        (29.9, 3.4, [
            (G["card"], {"id": "m_sch", "variant": "schedule", "title": "Horarios disponibles",
                         "subtitle": "Checa el link aqui abajo", "y": 0.5}, 0.1, 1.9),
            (G["cta"], {"id": "m_lb", "variant": "link_below", "label": "Agenda aqui", "y": 0.66}, 1.8, 1.6),
        ]),
        # cierre: seguir
        (34.6, 3.0, [
            (G["cta"], {"id": "m_fw", "variant": "follow", "label": NAME, "subtitle": ROLE,
                        "y": 0.64}, 0.3, 2.7),
            (G["progress"], {"id": "m_pg"}, 0.0, 3.0),
        ]),
    ]


def run_mp4():
    import subprocess as sp
    out = OUT / "montage.mp4"
    fps = 30
    enc = sp.Popen([paths.FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
                    "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], stdin=sp.PIPE)
    n_total = 0
    t_draw = []
    for seg_i, (start, dur, layers) in enumerate(_montage_spec()):
        dec = sp.Popen([paths.FFMPEG, "-v", "error", "-ss", str(start), "-t", str(dur), "-i", str(CRUDO),
                        "-vf", f"scale={W}:{H}:flags=bicubic,fps={fps}", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                       stdout=sp.PIPE)
        fsize = W * H * 4
        i = 0
        while True:
            buf = dec.stdout.read(fsize)
            if len(buf) < fsize:
                break
            t = i / fps
            arr = np.frombuffer(buf, np.uint8).reshape(H, W, 4).copy()
            img = C.image_from_rgba(arr)
            ctx = C.demo_ctx(W=W, H=H, t=t, entities=dict(ENT))
            ctx.n = n_total
            ctx._cache["base_image"] = img
            surf = skia.Surface(arr, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
            cv = surf.getCanvas()
            t0 = time.perf_counter()
            for fn, it, st, du in layers:
                lt = t - st
                if 0 <= lt < du:
                    fn(cv, it, lt, du, ctx)
            t_draw.append((time.perf_counter() - t0) * 1000)
            enc.stdin.write(arr.tobytes())
            i += 1
            n_total += 1
        dec.wait()
    enc.stdin.close()
    enc.wait()
    print(f"-> {out}  ({n_total} cuadros, overlays {np.mean(t_draw):.2f} ms/cuadro prom, "
          f"p95 {np.percentile(t_draw, 95):.2f} ms)")
    # hoja de contacto del montaje (2 cuadros por segundo)
    cs = OUT / "montage_sheet.png"
    sp.run([paths.FFMPEG, "-v", "error", "-y", "-i", str(out), "-vf",
            "fps=2,scale=216:384,drawtext=text='%{pts\\:hms}':x=4:y=4:fontcolor=yellow:fontsize=14:"
            "box=1:boxcolor=black@0.6,tile=12x4", "-frames:v", "1", str(cs)], check=False)
    print("->", cs)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--mp4", action="store_true")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    jobs = {"doodles": run_doodles, "paths": run_doodle_paths, "sketch": run_sketch, "icons": run_icons, "cards": run_cards,
            "lower_cta": run_lower_cta, "cutaways": run_cutaways, "perf": run_perf,
            "composite": run_composite, "fixes": run_fixes}
    if a.mp4:
        jobs["mp4"] = run_mp4
    for k, fn in jobs.items():
        if not only or k in only or (k == "mp4" and a.mp4):
            t0 = time.time()
            fn()
            print(f"{k}: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
