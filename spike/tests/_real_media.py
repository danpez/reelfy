"""Material real del caso de prueba para tests de camera/grade/matte (solo lectura).

Los videos derivados se cachean en spike/work/fixtures (ignorado por git): nada del cliente
se versiona.
"""
from pathlib import Path

from conftest import FIXTURES, TEST_MEDIA


def small_base_video(W: int = 270, H: int = 480, fps: int = 10):
    """Video base reducido del caso de prueba (crudo_completo.mp4), cacheado en
    spike/work/fixtures (ignorado por git). None si no hay material de prueba."""
    import subprocess
    src = TEST_MEDIA / "crudo_completo.mp4"
    if not src.exists():
        return None
    out = FIXTURES / f"_test_base_{W}x{H}_{fps}.mp4"
    if out.exists() and out.stat().st_size > 1000:
        return out
    import paths
    FIXTURES.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-i", str(src), "-an",
                    "-vf", f"fps={fps},scale={W}:{H}:flags=area", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", str(fps), str(tmp)],
                   check=True)
    tmp.replace(out)
    return out


def real_frames(ts, W: int = 540, H: int = 960) -> list:
    """Cuadros RGBA uint8 (alfa 255) de crudo_completo.mp4 en los tiempos ts (s), reducidos a
    W x H y cacheados como PNG en spike/work/fixtures (ignorado por git). None si no hay
    material de prueba."""
    import subprocess

    import cv2
    import numpy as np
    src = TEST_MEDIA / "crudo_completo.mp4"
    if not src.exists():
        return None
    import paths
    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = []
    for t in ts:
        p = FIXTURES / f"_test_frame_{W}x{H}_{t:.3f}.png"
        if not p.exists():
            tmp = p.with_suffix(".part.png")
            subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src),
                            "-frames:v", "1", "-vf", f"scale={W}:{H}:flags=area", str(tmp)], check=True)
            tmp.replace(p)
        bgr = cv2.imread(str(p), cv2.IMREAD_COLOR)
        a = np.empty((H, W, 4), np.uint8)
        a[..., :3] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        a[..., 3] = 255
        out.append(a)
    return out
