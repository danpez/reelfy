"""Graficas de audio para las demos de sfx/audio (skia + numpy; sin matplotlib).

- spectrogram_rgba: espectrograma en eje de frecuencia logaritmico con mapa de color tipo magma
- draw_wave: forma de onda min/max por columna (L arriba, R abajo)
- sfx_card: tarjeta de un SFX (onda + espectrograma + marca de hit + medidas)
"""
from __future__ import annotations

import numpy as np
import skia
from scipy import signal

from motion import assets
from motion import ctx as C

SR = 48000
BG = "#0F1115"
PANEL = "#171A21"
GRID = "#2A2F3A"
TEXT = "#E8EAF0"
MUTED = "#8A93A6"
WAVE_L = "#7CC4FF"
WAVE_R = "#FFB36B"
HIT = "#FF4D6D"
NOTE_NAMES = ["Do", "Do#", "Re", "Mib", "Mi", "Fa", "Fa#", "Sol", "Lab", "La", "Sib", "Si"]

_MAGMA = np.array([
    [0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122],
    [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191]], np.float64)


def colormap(v: np.ndarray) -> np.ndarray:
    """v en [0,1] -> RGB uint8 (magma aproximado)."""
    v = np.clip(v, 0, 1) * (len(_MAGMA) - 1)
    i = np.floor(v).astype(int)
    i2 = np.minimum(i + 1, len(_MAGMA) - 1)
    f = (v - i)[..., None]
    return (_MAGMA[i] * (1 - f) + _MAGMA[i2] * f).astype(np.uint8)


def spectrogram_rgba(x: np.ndarray, w: int, h: int, fmin: float = 30.0, fmax: float = 20000.0,
                     db_range: float = 80.0, nfft: int = 2048, ref_db: float | None = None) -> np.ndarray:
    m = x.mean(axis=1) if x.ndim == 2 else x
    if len(m) < nfft:
        m = np.concatenate([m, np.zeros(nfft - len(m))])
    hop = max(64, len(m) // max(1, w))
    hop = min(hop, nfft // 2)
    f, t, Z = signal.stft(m, fs=SR, nperseg=nfft, noverlap=nfft - hop, boundary=None, padded=False)
    S = 20 * np.log10(np.abs(Z) + 1e-9)
    top = ref_db if ref_db is not None else S.max()
    S = np.clip((S - (top - db_range)) / db_range, 0, 1)
    # filas en escala log
    fr = np.geomspace(fmin, fmax, h)[::-1]
    rows = np.empty((h, S.shape[1]))
    for j in range(S.shape[1]):
        rows[:, j] = np.interp(fr, f, S[:, j])
    # columnas a w
    xs = np.linspace(0, S.shape[1] - 1, w)
    img = np.empty((h, w))
    for i in range(h):
        img[i] = np.interp(xs, np.arange(S.shape[1]), rows[i])
    rgb = colormap(img ** 1.15)
    a = np.full((h, w, 1), 255, np.uint8)
    return np.ascontiguousarray(np.concatenate([rgb, a], axis=2))


def draw_image(canvas: skia.Canvas, rgba: np.ndarray, x: float, y: float) -> None:
    img = C.image_from_rgba(rgba)
    canvas.drawImage(img, x, y)


def text(canvas, s, x, y, size=18, color=TEXT, font="Inter-SemiBold", align="left"):
    f = assets.font(font, size)
    p = skia.Paint(AntiAlias=True, Color=C.color(color))
    if align != "left":
        w = f.measureText(s)
        x = x - (w if align == "right" else w / 2)
    canvas.drawString(s, x, y, f, p)


def draw_wave(canvas: skia.Canvas, x: np.ndarray, rect: tuple[float, float, float, float],
              dur: float | None = None, scale: float = 1.0) -> None:
    """Onda estereo: L en la mitad superior, R en la inferior (min/max por columna)."""
    X, Y, W, H = rect
    n = x.shape[0]
    total = int((dur or n / SR) * SR)
    cols = int(W)
    p = skia.Paint(AntiAlias=True, Color=C.color(GRID), StrokeWidth=1)
    canvas.drawLine(X, Y + H / 4, X + W, Y + H / 4, p)
    canvas.drawLine(X, Y + 3 * H / 4, X + W, Y + 3 * H / 4, p)
    for ch, col, cy in ((0, WAVE_L, Y + H / 4), (1, WAVE_R, Y + 3 * H / 4)):
        edges = np.linspace(0, total, cols + 1).astype(int)
        path = skia.Path()
        top, bot = [], []
        for i in range(cols):
            a, b = edges[i], min(n, max(edges[i] + 1, edges[i + 1]))
            if a >= n:
                top.append(0.0)
                bot.append(0.0)
                continue
            seg = x[a:b, ch]
            top.append(float(seg.max()))
            bot.append(float(seg.min()))
        amp = H / 4 * 0.95 * scale
        path.moveTo(X, cy - top[0] * amp)
        for i, v in enumerate(top):
            path.lineTo(X + i, cy - max(v, 0) * amp)
        for i in range(cols - 1, -1, -1):
            path.lineTo(X + i, cy - min(bot[i], 0) * amp)
        path.close()
        canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=C.color(col, 0.9)))


def db_ticks(v: float) -> str:
    return f"{v:+.1f}"


def sfx_card(name: str, x: np.ndarray, hit: float, meta: dict, w: int = 1200, h: int = 520) -> np.ndarray:
    """Tarjeta PNG de un SFX (onda normalizada a su pico + espectrograma). Devuelve RGBA uint8."""
    arr, surf = C.new_frame(w, h)
    cv = surf.getCanvas()
    cv.clear(C.color(BG))
    k = w / 1200.0
    dur = x.shape[0] / SR
    pad = 28 * k
    text(cv, name, pad, 40 * k + 4, 30 * k, TEXT, "Inter-Bold")
    info = (f"dur {dur:.2f} s   hit {hit * 1000:.1f} ms   L100 {meta.get('lufs_100ms', 0):.1f} LUFS"
            f"   pico {meta.get('peak_db', 0):.1f} dBTP")
    if meta.get("phone_db") is not None:
        info += f"   tel {meta['phone_db']:+.1f} dB"
    if meta.get("root") is not None:
        info += f"   tonica {NOTE_NAMES[int(meta['root']) % 12]}"
    text(cv, meta.get("desc", ""), pad, 40 * k + 32 * k + 2, 16 * k, MUTED, "Inter-Medium")
    text(cv, info, w - pad, 40 * k + 32 * k + 2, 14 * k, MUTED, "SpaceMono-Bold", align="right")
    top = int(0.19 * h)
    wr = (pad, top, w - 2 * pad, int(0.36 * h))
    cv.drawRoundRect(skia.Rect.MakeXYWH(wr[0] - 6, wr[1] - 6, wr[2] + 12, wr[3] + 12), 10, 10,
                     skia.Paint(AntiAlias=True, Color=C.color(PANEL)))
    pk = float(np.abs(x).max()) or 1.0
    if meta.get("loop"):
        la, lb = meta["loop"]
        cv.drawRect(skia.Rect.MakeLTRB(wr[0] + wr[2] * la / dur, wr[1] - 6, wr[0] + wr[2] * lb / dur, wr[1] + wr[3] + 6),
                    skia.Paint(Color=C.color("#9BE3B0", 0.10)))
        text(cv, "zona sostenida (loop con dur)", wr[0] + wr[2] * la / dur + 6, wr[1] + wr[3] - 2, 12 * k + 1,
             "#9BE3B0", "Inter-SemiBold")
    draw_wave(cv, x, wr, dur, scale=0.98 / pk)
    text(cv, f"x{1 / pk:.1f}", wr[0] + wr[2] - 4, wr[1] + 14 * k + 4, 12 * k, MUTED, "SpaceMono-Bold", align="right")
    sy = wr[1] + wr[3] + int(14 * k)
    sh = int(h - sy - 30 * k)
    sp = spectrogram_rgba(x, int(wr[2]), int(sh), db_range=75)
    draw_image(cv, sp, wr[0], sy)
    for fq, lab in ((100, "100"), (1000, "1k"), (10000, "10k")):
        yy = sy + sh * (1 - (np.log(fq / 30.0) / np.log(20000 / 30.0)))
        cv.drawLine(wr[0], yy, wr[0] + 8, yy, skia.Paint(Color=C.color("#FFFFFF", 0.6)))
        text(cv, lab, wr[0] + 12, yy + 4, 11 * k + 2, "#FFFFFF", "SpaceMono-Bold")
    step = 0.1 if dur <= 1.2 else (0.25 if dur <= 3 else 0.5)
    tt = 0.0
    while tt <= dur + 1e-6:
        xx = wr[0] + wr[2] * tt / dur
        text(cv, f"{tt:.2f}", xx, h - 10 * k, 11 * k + 1, MUTED, "SpaceMono-Bold", align="center")
        tt += step
    hx = wr[0] + wr[2] * hit / dur
    hp = skia.Paint(AntiAlias=True, Color=C.color(HIT), StrokeWidth=2)
    hp.setPathEffect(skia.DashPathEffect.Make([6, 5], 0))
    cv.drawLine(hx, wr[1] - 4, hx, sy + sh, hp)
    text(cv, "hit", hx + 6, wr[1] + 12, 14 * k + 1, HIT, "Inter-Bold")
    return arr.copy()


def save_png(rgba: np.ndarray, path) -> None:
    img = C.image_from_rgba(rgba)
    img.save(str(path), skia.kPNG)


def grid(cards: list[np.ndarray], cols: int, gap: int = 12, bg: str = BG) -> np.ndarray:
    ch, cw = cards[0].shape[:2]
    rows = (len(cards) + cols - 1) // cols
    H = rows * ch + (rows + 1) * gap
    W = cols * cw + (cols + 1) * gap
    out = np.zeros((H, W, 4), np.uint8)
    out[..., :3] = np.array(C.rgb(bg), np.uint8)
    out[..., 3] = 255
    for i, c in enumerate(cards):
        r, k = divmod(i, cols)
        y, x = gap + r * (ch + gap), gap + k * (cw + gap)
        out[y:y + ch, x:x + cw] = c
    return out
