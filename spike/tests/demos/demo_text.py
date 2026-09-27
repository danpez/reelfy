"""Demo visual de motion/text.py: hojas de contacto por preset y subtitulos + MP4 corto.

Uso (desde spike/):
  .venv/bin/python tests/demos/demo_text.py            # todo
  .venv/bin/python tests/demos/demo_text.py titles     # solo presets de titulo
  .venv/bin/python tests/demos/demo_text.py captions   # solo subtitulos
  .venv/bin/python tests/demos/demo_text.py video      # MP4 con video real
  .venv/bin/python tests/demos/demo_text.py bench      # ms por llamada

Salida: spike/work/demos/text/ (ignorado por git). Los fondos son cuadros del caso real
(crudo_completo.mp4) extraidos con ffmpeg; la transcripcion sale de work/fixtures.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import skia
from PIL import Image, ImageDraw, ImageFont

SPIKE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SPIKE / "scripts"))

import paths  # noqa: E402
from motion import ctx as C  # noqa: E402
from motion import text as T  # noqa: E402

OUT = SPIKE / "work" / "demos" / "text"
OUT.mkdir(parents=True, exist_ok=True)
MEDIA = Path(__import__("os").environ.get("REELFY_TEST_MEDIA") or Path(__file__).resolve().parents[2] / "work" / "media")
CRUDO = MEDIA / "crudo_completo.mp4"
PLAN = SPIKE / "work" / "fixtures" / "dentista_plan_v1.json"
W, H = 1080, 1920
THUMB = (360, 640)


def bg(t: float) -> np.ndarray:
    """Cuadro RGBA 1080x1920 del crudo (cacheado en OUT/bg_<t>.jpg)."""
    f = OUT / f"bg_{t:.1f}.jpg"
    if not f.exists():
        subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-ss", str(t), "-i", str(CRUDO), "-frames:v", "1",
                        "-vf", f"scale={W}:{H}:flags=lanczos", "-q:v", "2", str(f)], check=True)
    im = Image.open(f).convert("RGBA")
    return np.array(im)


def words_rel(plan: dict, t0: float, t1: float) -> list:
    out = []
    for ph in plan["phrases"]:
        for w in ph:
            if w["start"] >= t0 - 0.01 and w["start"] < t1:
                out.append({"text": w["text"], "start": round(w["start"] - t0, 3), "end": round(w["end"] - t0, 3)})
    return out


def render(bg_arr: np.ndarray, fn, t: float, quality: str = "final", **ctx_kw) -> np.ndarray:
    w, h = (W, H) if quality == "final" else (W // 2, H // 2)
    arr, surf = C.new_frame(w, h)
    cv = surf.getCanvas()
    img = C.image_from_rgba(bg_arr)
    if quality != "final":
        cv.save()
        cv.scale(0.5, 0.5)
        cv.drawImage(img, 0, 0)
        cv.restore()
        base = surf.makeImageSnapshot()
    else:
        cv.drawImage(img, 0, 0)
        base = img
    ctx = C.demo_ctx(W=w, H=h, t=t, quality=quality, **ctx_kw)
    ctx._cache["base_image"] = base
    fn(cv, ctx)
    return arr.copy()


def label(im: Image.Image, txt: str) -> Image.Image:
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype(str(SPIKE / "assets/motion/fonts/SpaceMono-Bold.ttf"), 20)
    except Exception:
        f = None
    d.rectangle((0, 0, 8 + 13 * len(txt), 30), fill=(0, 0, 0))
    d.text((6, 3), txt, fill=(255, 220, 60), font=f)
    return im


def sheet(frames: list, name: str, title: str, cols: int = 8) -> Path:
    tw, th = THUMB
    rows = (len(frames) + cols - 1) // cols
    head = 44
    S = Image.new("RGB", (cols * tw, rows * th + head), (18, 18, 20))
    d = ImageDraw.Draw(S)
    try:
        f = ImageFont.truetype(str(SPIKE / "assets/motion/fonts/Poppins-SemiBold.ttf"), 26)
    except Exception:
        f = None
    d.text((12, 6), title, fill=(240, 240, 240), font=f)
    for i, (arr, lab) in enumerate(frames):
        im = Image.fromarray(arr[..., :3]).resize((tw, th), Image.LANCZOS)
        label(im, lab)
        S.paste(im, ((i % cols) * tw, head + (i // cols) * th))
    p = OUT / f"{name}.jpg"
    S.save(p, quality=88)
    return p


# ---------------------------------------------------------------- items de ejemplo (guion dentista)
def title_items(plan: dict) -> list:
    return [
        ("title_pop", {"id": "t1", "preset": "title_pop", "text": "¿Ya sientes sarro?",
                       "accent_words": ["sarro"], "y": 0.2, "_words": words_rel(plan, 4.35, 5.6)},
         2.4, 9.5, [0.0, 0.08, 0.16, 0.3, 0.5, 0.75, 1.0, 1.4, 2.0, 2.2, 2.3, 2.38]),
        ("stack", {"id": "t2", "preset": "stack", "text": "Necesitamos\nHABLAR",
                   "y": 0.62, "_words": words_rel(plan, 6.4, 7.8)},
         2.2, 9.5, [0.0, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.4, 1.9, 2.0, 2.1, 2.18]),
        ("stack_3", {"id": "t2b", "preset": "stack", "text": "Una buena\nLIMPIEZA\nlo cambia todo",
                     "y": 0.62, "accent_words": ["limpieza"]},
         2.4, 31.0, [0.0, 0.1, 0.25, 0.4, 0.55, 0.7, 0.9, 1.2, 2.0, 2.15, 2.3, 2.38]),
        ("cascade", {"id": "t3", "preset": "cascade", "text": "cómo se sienten\ny cómo se ven\ntus dientes",
                     "y": 0.19, "accent_words": ["dientes"], "_words": words_rel(plan, 10.8, 13.2)},
         3.0, 9.5, [0.0, 0.2, 0.5, 0.8, 1.1, 1.4, 1.7, 2.0, 2.4, 2.8, 2.9, 2.98]),
        ("serif_elegant", {"id": "t4", "preset": "serif_elegant", "text": "Una buena limpieza",
                           "accent_words": ["limpieza"], "y": 0.15, "_words": words_rel(plan, 8.3, 9.4)},
         2.2, 2.0, [0.0, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9, 1.2, 1.6, 1.95, 2.05, 2.18]),
        ("serif_lines", {"id": "t4b", "preset": "serif_elegant",
                         "lines": [{"text": "cómo se ven"}, {"text": "tus dientes", "scale": 0.36,
                                                            "font": "Poppins-ExtraBold", "align": "right"}],
                         "y": 0.15}, 2.0, 9.5, [0.0, 0.15, 0.3, 0.45, 0.6, 0.8, 1.0, 1.3, 1.6, 1.8, 1.9, 1.98]),
        ("script_accent", {"id": "t5", "preset": "script_accent", "text": "limpieza", "y": 0.2},
         2.4, 2.0, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.85, 1.0, 1.3, 2.3]),
        ("behind_giant", {"id": "t6", "preset": "behind_giant", "text": "DIENTES", "y": 0.25},
         2.4, 9.5, [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.45, 0.7, 1.2, 2.0, 2.2, 2.35]),
        ("highlight_marker", {"id": "t7", "preset": "highlight_marker", "text": "Tiene un costo de mil pesos",
                              "accent_words": ["mil", "pesos"], "y": 0.66, "_words": words_rel(plan, 14.55, 16.4)},
         2.4, 31.0, [0.0, 0.15, 0.3, 0.5, 0.7, 0.9, 1.1, 1.3, 1.6, 2.0, 2.25, 2.38]),
        ("typewriter", {"id": "t8", "preset": "typewriter", "text": "Av. Central 123", "y": 0.66},
         2.6, 20.0, [0.0, 0.1, 0.25, 0.4, 0.55, 0.7, 0.85, 1.0, 1.3, 1.6, 2.0, 2.55]),
        ("counter", {"id": "t9", "preset": "counter", "from": 0, "to": 1000, "prefix": "$", "suffix": "MXN",
                     "text": "Limpieza dental", "y": 0.62},
         2.4, 31.0, [0.0, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9, 1.2, 1.5, 1.8, 2.2, 2.38]),
        ("kicker", {"id": "t10", "preset": "kicker", "kicker": "Ubicación",
                    "text": "A 2 cuadras de la\nGlorieta Norte", "y": 0.2, "accent_words": ["norte"]},
         2.4, 20.0, [0.0, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0, 1.4, 2.0, 2.2, 2.38]),
        ("title_box", {"id": "t11", "preset": "title_pop", "text": "Agenda tu cita", "box": "#FFFFFF",
                       "y": 0.7, "anim_in": "rise"},
         2.0, 31.0, [0.0, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.3, 1.6, 1.8, 1.9, 1.98]),
        ("title_mask", {"id": "t12", "preset": "title_pop", "text": "Checa mis horarios\ndisponibles",
                        "y": 0.2, "anim_in": "mask", "anim_out": "slide_up", "accent_words": ["horarios"]},
         2.0, 31.0, [0.0, 0.1, 0.2, 0.3, 0.45, 0.6, 1.0, 1.5, 1.75, 1.85, 1.92, 1.98]),
        # items tal como los generan director.py y schema.py (regresiones de la revision)
        ("marker_director", {"id": "t13", "preset": "highlight_marker", "text": "Tiene un costo\nde mil pesos",
                             "size": 0.072, "font": "Poppins-SemiBold", "color": "#FFFFFF", "accent": "#0E0E12",
                             "shadow": 0.3, "anim_in": "pop", "accent_words": ["mil", "pesos."], "box": "#FFD23F",
                             "y": 0.66, "_words": words_rel(plan, 14.55, 16.4)},
         2.4, 31.0, [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0, 1.1, 1.3, 1.8, 2.2, 2.38]),
        ("marker_schema", {"id": "t14", "preset": "highlight_marker", "text": "Tiene un costo de mil pesos",
                           "size": 0.065, "font": "Poppins-ExtraBold", "y": 0.3, "box": "#FFD23F",
                           "color": "#111111", "anim_in": "mask"},
         2.4, 31.0, [0.0, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0, 1.3, 2.0, 2.25, 2.38]),
        ("kicker_director", {"id": "t15", "preset": "kicker", "text": "a dos cuadras de la Glorieta Norte",
                             "size": 0.05, "font": "Poppins-Medium", "color": "#FFFFFF", "shadow": 0.25,
                             "anim_in": "slide_left", "accent_words": ["Norte"], "box": "#0E0E12", "y": 0.25,
                             "_words": words_rel(plan, 21.3, 23.6)},
         2.6, 20.0, [0.0, 0.1, 0.25, 0.5, 0.8, 1.0, 1.2, 1.4, 1.7, 2.2, 2.45, 2.58]),
        ("kicker_label", {"id": "t16", "preset": "kicker", "text": "Limpieza dental", "size": 0.05,
                          "font": "Poppins-SemiBold", "box": "#0E0E12", "y": 0.25},
         2.0, 20.0, [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.45, 0.7, 1.2, 1.8, 1.9, 1.98]),
        ("behind_no_matte", {"id": "t17", "preset": "behind_giant", "text": "DIENTES", "y": 0.3},
         2.4, 2.0, [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.45, 0.7, 1.2, 2.0, 2.2, 2.35],
         {"face": (0.384, 0.33, 0.14, 0.10)}),
        ("counter_odometer", {"id": "t18", "preset": "counter", "from": 0, "to": 1000, "prefix": "$",
                              "suffix": "MXN", "y": 0.3},
         2.0, 2.0, [0.0, 0.1, 0.15, 0.2, 0.3, 0.4, 0.55, 0.7, 0.9, 1.2, 1.5, 1.9]),
    ]


def run_titles(only: str | None = None) -> list:
    plan = json.loads(PLAN.read_text())
    out = []
    for name, item, dur, bgt, ts, *extra in title_items(plan):
        if only and only != name:
            continue
        ckw = extra[0] if extra else {}
        base = bg(bgt)
        fn = T.RENDERERS[item["preset"]]
        frames = []
        for t in ts:
            arr = render(base, lambda cv, ctx: fn(cv, item, t, dur, ctx), t, **ckw)
            frames.append((arr, f"{t:.2f}s"))
        p = sheet(frames, f"sheet_{name}", f"{name}  (dur {dur}s)  preset={item['preset']}", cols=6)
        out.append(p)
        print("ok", p)
    return out


def run_captions(presets=None) -> list:
    plan = json.loads(PLAN.read_text())
    phrases = plan["phrases"]
    out = []
    for preset in presets or ["clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps"]:
        cfg = {"enabled": True, "preset": preset, "y": 0.72, "size": 0.052, "font": "Poppins-ExtraBold",
               "color": "#FFFFFF", "accent": "#FFD23F", "case": "none", "max_words": 4,
               "_hide": [[16.8, 17.6]]}
        ts = [8.3, 8.42, 8.6, 8.8, 9.0, 9.2, 9.45, 9.7, 10.0, 10.3, 10.9, 11.3]
        frames = []
        for t in ts:
            base = bg(9.5)
            arr = render(base, lambda cv, ctx: T.draw_captions(cv, phrases, cfg, t, ctx), t)
            frames.append((arr, f"{t:.2f}s"))
        p = sheet(frames, f"captions_{preset}", f"captions preset={preset}", cols=6)
        out.append(p)
        print("ok", p)
    return out


def bench() -> str:
    plan = json.loads(PLAN.read_text())
    base = bg(9.5)
    lines = []
    phrases = plan["phrases"]
    for quality in ("final", "draft"):
        w, h = (W, H) if quality == "final" else (W // 2, H // 2)
        arr, surf = C.new_frame(w, h)
        cv = surf.getCanvas()
        bimg = C.image_from_rgba(base)
        res = []
        for name, item, dur, _bgt, _ts, *_x in title_items(plan):
            fn = T.RENDERERS[item["preset"]]
            n = 60
            ts = [dur * i / n for i in range(n)]
            ctx0 = C.demo_ctx(W=w, H=h, quality=quality)
            ctx0._cache["base_image"] = bimg
            fn(cv, item, 0.5, dur, ctx0)  # calentar cache de layout
            t0 = time.perf_counter()
            for t in ts:
                ctx = C.demo_ctx(W=w, H=h, t=t, quality=quality)
                ctx._cache["base_image"] = bimg
                fn(cv, item, t, dur, ctx)
            res.append((name, (time.perf_counter() - t0) / n * 1000))
        cfg = {"enabled": True, "preset": "clean_bold", "y": 0.72, "size": 0.052, "max_words": 4}
        for preset in ["clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps"]:
            cfg = dict(cfg, preset=preset)
            ctx0 = C.demo_ctx(W=w, H=h, quality=quality)
            T.draw_captions(cv, phrases, cfg, 9.0, ctx0)
            n = 120
            t0 = time.perf_counter()
            for i in range(n):
                t = 8.3 + i / 30
                ctx = C.demo_ctx(W=w, H=h, t=t, quality=quality)
                ctx._cache["base_image"] = bimg
                T.draw_captions(cv, phrases, cfg, t, ctx)
            res.append(("captions_" + preset, (time.perf_counter() - t0) / n * 1000))
        lines.append(f"[{quality} {w}x{h}] " + ", ".join(f"{k}={v:.2f}ms" for k, v in res))
    txt = "\n".join(lines)
    (OUT / "bench.txt").write_text(txt + "\n")
    print(txt)
    return txt


def run_video(seconds: float = 8.0, t0: float = 4.2) -> Path:
    """MP4 con video real del crudo: subtitulos clean_bold + titulos sincronizados."""
    plan = json.loads(PLAN.read_text())
    phrases = plan["phrases"]
    fps = 30
    n = int(seconds * fps)
    dec = subprocess.Popen([paths.FFMPEG, "-v", "error", "-ss", str(t0), "-t", str(seconds), "-i", str(CRUDO),
                            "-vf", f"scale={W}:{H}:flags=bicubic,fps={fps}", "-pix_fmt", "rgba", "-f", "rawvideo",
                            "-"], stdout=subprocess.PIPE)
    out = OUT / "demo_text.mp4"
    enc = subprocess.Popen([paths.FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                            "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-ss", str(t0), "-t", str(seconds),
                            "-i", str(CRUDO), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-preset",
                            "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)],
                           stdin=subprocess.PIPE)
    items = [
        ({"id": "v1", "preset": "title_pop", "text": "¿Ya sientes sarro?", "accent_words": ["sarro"],
          "y": 0.2, "_words": words_rel(plan, 4.35, 5.6)}, 4.35, 5.9),
        ({"id": "v2", "preset": "stack", "text": "Necesitamos\nHABLAR", "y": 0.2,
          "_words": words_rel(plan, 6.4, 7.8)}, 6.4, 8.1),
        ({"id": "v3", "preset": "serif_elegant", "text": "Una buena limpieza", "accent_words": ["limpieza"],
          "y": 0.15, "_words": words_rel(plan, 8.3, 9.4)}, 8.3, 10.0),
    ]
    hide = [[a, b] for _, a, b in items]  # como render: sin subtitulos durante titulos
    cfg = {"enabled": True, "preset": "clean_bold", "y": 0.72, "size": 0.052, "max_words": 4, "_hide": hide}
    fsz = W * H * 4
    times = []
    for i in range(n):
        buf = dec.stdout.read(fsz)
        if len(buf) < fsz:
            break
        t = t0 + i / fps
        arr = np.frombuffer(buf, np.uint8).reshape(H, W, 4).copy()
        surf = skia.Surface(arr, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        cv = surf.getCanvas()
        ctx = C.demo_ctx(W=W, H=H, t=t)
        ctx._cache["base_image"] = C.image_from_rgba(arr.copy())
        s = time.perf_counter()
        for item, a, b in items:
            if a <= t < b:
                T.RENDERERS[item["preset"]](cv, item, t - a, b - a, ctx)
        T.draw_captions(cv, phrases, cfg, t, ctx)
        times.append((time.perf_counter() - s) * 1000)
        enc.stdin.write(arr.tobytes())
    enc.stdin.close()
    enc.wait()
    dec.wait()
    print(f"video {out}  overlay ms/cuadro: media {np.mean(times):.2f}  p95 {np.percentile(times, 95):.2f}")
    return out


OBJ = MEDIA / "ejemplo_objetivo.mp4"


def reference_items() -> list:
    """Momentos tipograficos del video objetivo replicados con nuestros presets.
    (t_objetivo, item, dur, lt, fondo)"""
    return [
        (0.6, {"id": "r1", "preset": "stack", "text": "Te has\npreguntado", "y": 0.13}, 2.0, 1.4, 2.0),
        (2.6, {"id": "r2", "preset": "serif_elegant", "font": "InstrumentSerif", "y": 0.1,
               "lines": [{"text": "los creadores"},
                         {"text": "de contenido", "scale": 0.34, "font": "Poppins-ExtraBold", "align": "right",
                          "color": "#FFFFFF"}]}, 2.0, 1.4, 9.5),
        (8.7, {"id": "r3", "preset": "stack", "text": "HOY\nte revelo\nel secreto", "y": 0.6}, 2.0, 1.4, 2.0),
        (14.2, {"id": "r4", "preset": "stack", "y": 0.6,
                "lines": [{"text": "AQUI", "font": "DMSerifDisplay"},
                          {"text": "ENCONTRARÁS", "scale": 0.27, "font": "Poppins-Bold", "tracking": 0.06}]},
         2.0, 1.4, 31.0),
        (18.6, {"id": "r5", "preset": "stack", "y": 0.14, "color": "#141414", "font": "ArchivoBlack",
                "lines": [{"text": "BACKGROUNDS"}, {"text": "efectos", "scale": 0.5, "font": "Poppins-Bold"},
                          {"text": "de sonido", "scale": 0.72, "font": "Poppins-ExtraBold", "color": "#1F8A74"}]},
         2.0, 1.4, 31.0),
        (19.6, {"id": "r6", "preset": "script_accent", "text": "y música", "y": 0.45}, 2.4, 1.6, 20.0),
        (22.2, {"id": "r7", "preset": "cascade", "text": "para llevar\nla edición\nde video", "y": 0.16},
         2.4, 1.8, 9.5),
        (23.2, {"id": "r8", "preset": "stack", "text": "al siguiente\nNIVEL", "y": 0.2}, 2.0, 1.4, 20.0),
        (32.0, {"id": "r9", "preset": "serif_elegant", "font": "DMSerifDisplay", "text": "recursos", "y": 0.6,
                "size": 0.12, "color": "#FFFFFF"}, 2.0, 1.4, 2.0),
        (36.6, {"id": "r10", "preset": "title_pop", "text": "sígueme", "y": 0.66, "size": 0.085}, 2.0, 1.4, 2.0),
    ]


def run_reference() -> Path:
    """Hoja comparativa: panel 'despues' del objetivo (izq) vs nuestro render (der)."""
    tw, th = 300, 534
    pairs = []
    for tobj, item, dur, lt, bgt in reference_items():
        f = OUT / f"obj_{tobj:.1f}.png"
        if not f.exists():
            subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-ss", str(tobj), "-i", str(OBJ), "-frames:v", "1",
                            "-vf", "crop=275:490:281:88", str(f)], check=True)
        ref = Image.open(f).convert("RGB").resize((tw, th), Image.LANCZOS)
        base = bg(bgt)
        arr = render(base, lambda cv, ctx: T.RENDERERS[item["preset"]](cv, item, lt, dur, ctx), lt)
        ours = Image.fromarray(arr[..., :3]).resize((tw, th), Image.LANCZOS)
        pairs.append((ref, ours, f"obj {tobj}s | {item['preset']}"))
    cols = 5
    rows = (len(pairs) + cols - 1) // cols
    S = Image.new("RGB", (cols * (2 * tw + 12), rows * (th + 30)), (18, 18, 20))
    d = ImageDraw.Draw(S)
    for i, (a, b, lab) in enumerate(pairs):
        x = (i % cols) * (2 * tw + 12)
        y = (i // cols) * (th + 30)
        d.text((x + 4, y + 6), lab, fill=(240, 220, 90))
        S.paste(a, (x, y + 28))
        S.paste(b, (x + tw + 2, y + 28))
    p = OUT / "sheet_reference_vs_ours.jpg"
    S.save(p, quality=88)
    print("ok", p)
    return p


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if what in ("all", "titles"):
        run_titles(arg)
    if what in ("all", "captions"):
        run_captions([arg] if arg else None)
    if what in ("all", "reference"):
        run_reference()
    if what in ("all", "bench"):
        bench()
    if what in ("all", "video"):
        run_video()
