"""Videos de apoyo SINTETICOS para las pruebas (repo publico: nada de un cliente).

make_film(path, shots, fps, size) dibuja con PIL una "presentacion de producto" muda con tomas de tipos
conocidos, cortes secos en tiempos conocidos y, en cada cuadro, su numero en 12 bits (una rejilla de 4x3
cuadritos al centro abajo: queda dentro de un recorte vertical centrado y de la ventana):

  screen     interfaz de una app (fondo claro, barra de color, burbujas de chat con lineas de "texto")
  character  un personaje de color (mancha redonda con ojos) sobre un fondo claro liso
  title      fondo oscuro con un titulo grande claro al centro
  person     cuadros REALES de una persona (material de prueba local, nunca versionado) o, sin el, una
             silueta oscura grande que entra por el borde
  dark       casi negro (un fundido)

Devuelve [{kind, a, b}] con los tiempos de cada toma (s del archivo).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

BITS = 12
GRID = (4, 3)               # columnas x filas de los bits


def bit_cells(w: int, h: int) -> list[tuple[int, int, int, int]]:
    """Rect (x0, y0, x1, y1) de cada bit (orden: bit 0 primero) en un cuadro w x h."""
    cw, ch = max(4, int(w * 0.022)), max(4, int(h * 0.04))
    gx, gy = GRID
    x0 = w // 2 - gx * cw // 2
    y0 = int(h * 0.86)
    out = []
    for i in range(BITS):
        c, r = i % gx, i // gx
        out.append((x0 + c * cw, y0 + r * ch, x0 + (c + 1) * cw, y0 + (r + 1) * ch))
    return out


def read_bits(img: np.ndarray, cells=None) -> int | None:
    """Numero de cuadro leido de `img` (RGB) con las celdas dadas (None si algun bit es ambiguo)."""
    h, w = img.shape[:2]
    val = 0
    for i, (x0, y0, x1, y1) in enumerate(cells or bit_cells(w, h)):
        mx, my = max(1, (x1 - x0) // 4), max(1, (y1 - y0) // 4)
        v = float(img[y0 + my:y1 - my, x0 + mx:x1 - mx, :3].mean())
        if 80 < v < 175:
            return None
        val |= (1 << i) if v >= 175 else 0
    return val


def _person_frames(n: int, w: int, h: int):
    """Cuadros reales de una persona (crudo_completo.mp4 del material de prueba) o None."""
    try:
        from conftest import TEST_MEDIA
    except Exception:  # noqa
        return None
    src = Path(TEST_MEDIA) / "crudo_completo.mp4"
    if not src.exists():
        return None
    import paths
    r = subprocess.run([str(paths.FFMPEG), "-v", "error", "-ss", "3", "-i", str(src), "-frames:v", str(n),
                        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=rgb24",
                        "-f", "rawvideo", "-"], capture_output=True, timeout=120)
    if len(r.stdout) < w * h * 3:
        return None
    k = len(r.stdout) // (w * h * 3)
    return np.frombuffer(r.stdout[:k * w * h * 3], np.uint8).reshape(k, h, w, 3)


def _draw(kind: str, k: int, w: int, h: int, seed: int, person=None) -> np.ndarray:
    from PIL import Image, ImageDraw
    rng = np.random.default_rng(seed)
    if kind == "person" and person is not None:
        return np.array(person[k % len(person)], copy=True)
    if kind in ("screen", "wide"):
        bg = (246, 247, 250)
        im = Image.new("RGB", (w, h), (52, 46, 40) if kind == "screen" else (24, 64, 96))
        d = ImageDraw.Draw(im)
        # la pantalla (monitor) ocupa el centro; dentro, la app: en "screen" sus globos van en la columna
        # del centro (caben en un recorte 9:16), en "wide" de orilla a orilla del monitor (no caben: un
        # recorte 9:16 los rebanaria -> solo ventana)
        sx0, sy0, sx1, sy1 = int(w * 0.30), int(h * 0.06), int(w * 0.70), int(h * 0.82)
        d.rectangle([sx0, sy0, sx1, sy1], fill=bg)
        d.rectangle([sx0, sy0, sx1, sy0 + int(h * 0.07)], fill=tuple(int(c) for c in rng.integers(40, 200, 3)))
        ax0, ax1 = (int(w * 0.37), int(w * 0.63)) if kind == "screen" else (sx0, sx1)
        y = sy0 + int(h * 0.11)
        for j in range(4):
            bw = int((ax1 - ax0) * (0.55 + 0.2 * ((j + seed) % 2)))
            x = ax0 + int(w * 0.02) if j % 2 == 0 else ax1 - int(w * 0.02) - bw
            d.rounded_rectangle([x, y, x + bw, y + int(h * 0.11)], radius=int(h * 0.02),
                                fill=(226, 230, 238) if j % 2 == 0 else (60, 120, 220))
            for q in range(2):
                ly = y + int(h * 0.025) + q * int(h * 0.035)
                lx0 = x + int(w * 0.012)
                d.rectangle([lx0, ly, max(lx0 + 2, x + bw - int(w * 0.03) - (k * 3 + 17 * q) % 40), ly + int(h * 0.014)],
                            fill=(40, 40, 48) if j % 2 == 0 else (250, 250, 250))
            y += int(h * 0.15)
    elif kind == "character":
        im = Image.new("RGB", (w, h), (232, 233, 236))
        d = ImageDraw.Draw(im)
        import colorsys
        col = tuple(int(255 * c) for c in colorsys.hsv_to_rgb(float(rng.random()), 0.8, 0.85))    # color vivo
        cx, cy, r = w // 2 + int(0.02 * w * np.sin(k / 9)), int(h * 0.46), int(h * 0.26)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
        for ex in (-1, 1):
            d.ellipse([cx + ex * r // 3 - r // 9, cy - r // 4, cx + ex * r // 3 + r // 9, cy - r // 4 + r // 4],
                      fill=(20, 20, 24))
    elif kind == "title":
        im = Image.new("RGB", (w, h), (12, 12, 16))
        d = ImageDraw.Draw(im)
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", int(h * 0.22))
        except Exception:  # noqa
            font = None
        d.text((w // 2, int(h * 0.42)), "nubis", fill=(245, 245, 250), anchor="mm", font=font)
    elif kind == "person":
        # sin material real: una silueta oscura grande que entra por el borde (como alguien en primer plano)
        im = Image.new("RGB", (w, h), (150, 160, 170))
        d = ImageDraw.Draw(im)
        d.ellipse([int(w * 0.05), int(h * 0.1), int(w * 0.45), int(h * 0.75)], fill=(70, 50, 40))
        d.rectangle([0, int(h * 0.6), int(w * 0.6), h], fill=(30, 30, 34))
    else:   # dark
        im = Image.new("RGB", (w, h), (6, 6, 8))
    a = np.asarray(im, np.uint8).copy()
    return a


def make_film(path: Path, shots: list[tuple[str, float]], fps: int = 30, size=(640, 360), seed: int = 3) -> list[dict]:
    """Escribe el video (H.264 sin audio) y devuelve [{kind, a, b}]. `shots` = [(tipo, duracion s)]."""
    import paths
    w, h = size
    path = Path(path)
    total = sum(round(d * fps) for _k, d in shots)
    person = _person_frames(max(1, max([round(d * fps) for k, d in shots if k == "person"] + [1])), w, h) \
        if any(k == "person" for k, _d in shots) else None
    p = subprocess.Popen([str(paths.FFMPEG), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                          "-crf", "12", "-pix_fmt", "yuv420p", "-g", str(fps), str(path)], stdin=subprocess.PIPE)
    out, n, cells = [], 0, bit_cells(w, h)
    for j, (kind, d) in enumerate(shots):
        m = round(d * fps)
        out.append({"kind": kind, "a": round(n / fps, 4), "b": round((n + m) / fps, 4)})
        for k in range(m):
            fr = _draw(kind, k, w, h, seed + 17 * j, person)
            for i, (x0, y0, x1, y1) in enumerate(cells):
                fr[y0:y1, x0:x1] = 235 if ((n + k) >> i) & 1 else 20
            p.stdin.write(fr.tobytes())
        n += m
    p.stdin.close()
    assert p.wait(timeout=600) == 0
    assert n == total
    return out
