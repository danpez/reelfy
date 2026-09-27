"""Utilidades comunes de las demos de camera/grade/matte: cuadros reales del caso de prueba.

Genera (una vez) el video base 1080x1920 30 fps a partir de crudo_completo.mp4 en
spike/work/demos/_frames/ (ignorado por git) y extrae cuadros RGBA por tiempo.
Nada de esto se versiona: el material del cliente vive solo en spike/work/.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

SPIKE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SPIKE / "scripts"))

import paths  # noqa: E402

MEDIA = Path(__import__("os").environ.get("REELFY_TEST_MEDIA") or Path(__file__).resolve().parents[2] / "work" / "media")
RAW = MEDIA / "crudo_completo.mp4"
FRAMES = SPIKE / "work" / "demos" / "_frames"
# limites de toma medidos en crudo_completo.mp4 (deteccion de escena), tiempo final
SHOTS = [(0.0, 8.0), (8.0, 13.867), (13.867, 17.267), (17.267, 24.267), (24.267, 28.867), (28.867, 37.7)]
SHOT_NAMES = ["consultorio de pie", "consultorio caminando", "escritorio sentada", "calle",
              "fachada (sin persona)", "sillon dental"]
# un tiempo representativo por toma
SHOT_T = [3.0, 10.5, 15.2, 20.0, 26.0, 31.5]


def base_video(W: int = 1080, H: int = 1920, fps: int = 30) -> Path:
    FRAMES.mkdir(parents=True, exist_ok=True)
    out = FRAMES / f"base_{W}.mp4"
    if out.exists() and out.stat().st_size > 1000:
        return out
    if not RAW.exists():
        raise FileNotFoundError(RAW)
    subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-i", str(RAW), "-an",
                    "-vf", f"fps={fps},scale={W}:{H}:flags=lanczos,format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", "15", str(out)],
                   check=True)
    return out


def frame_at(t: float, W: int = 1080, H: int = 1920, fps: int = 30) -> np.ndarray:
    """Cuadro RGBA uint8 (alfa 255) del video base en el tiempo t (indice round(t*fps))."""
    return frames_at([t], W, H, fps)[0]


def frames_at(ts, W: int = 1080, H: int = 1920, fps: int = 30) -> list[np.ndarray]:
    from motion.grade import decode_frames
    src = base_video(W, H, fps)
    idx = [int(round(t * fps)) for t in ts]
    got = decode_frames(str(src), idx, W, H)
    out = []
    for i in idx:
        rgb = got[i]
        a = np.empty((H, W, 4), np.uint8)
        a[..., :3] = rgb
        a[..., 3] = 255
        out.append(a)
    return out


def sheet(tiles, labels, cols: int, tile_w: int = 360, title: str | None = None, pad: int = 8):
    """Hoja de contacto PIL con etiqueta sobre cada cuadro."""
    from PIL import Image, ImageDraw, ImageFont
    ims = []
    for t in tiles:
        if not isinstance(t, Image.Image):
            arr = np.asarray(t)
            t = Image.fromarray(arr[..., :3] if arr.ndim == 3 else arr)
        h = int(round(t.height * tile_w / t.width))
        ims.append(t.convert("RGB").resize((tile_w, h), Image.LANCZOS))
    th = max(i.height for i in ims)
    rows = (len(ims) + cols - 1) // cols
    top = 44 if title else 0
    lab_h = 30
    W = cols * (tile_w + pad) + pad
    H = top + rows * (th + lab_h + pad) + pad
    out = Image.new("RGB", (W, H), (18, 18, 20))
    d = ImageDraw.Draw(out)
    try:
        fnt = ImageFont.truetype(str(SPIKE / "assets/motion/fonts/Poppins-SemiBold.ttf"), 17)
        fntt = ImageFont.truetype(str(SPIKE / "assets/motion/fonts/Poppins-Bold.ttf"), 24)
    except Exception:
        fnt = fntt = ImageFont.load_default()
    if title:
        d.text((pad + 4, 8), title, fill=(240, 240, 240), font=fntt)
    for k, (im, lab) in enumerate(zip(ims, labels)):
        r, c = divmod(k, cols)
        x = pad + c * (tile_w + pad)
        y = top + pad + r * (th + lab_h + pad)
        d.text((x + 2, y + 4), str(lab), fill=(230, 230, 230), font=fnt)
        out.paste(im, (x, y + lab_h))
    return out
