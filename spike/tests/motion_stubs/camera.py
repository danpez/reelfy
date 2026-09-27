"""Stub de camara: zoom/punch por matriz, sin helpers (ejercita el camino propio de render)."""
import numpy as np
import skia

from motion import ease


def _scale(s, ctx):
    m = skia.Matrix()
    cx, cy = (ctx.face[0] * ctx.W, ctx.face[1] * ctx.H) if ctx.face else (ctx.W / 2, ctx.H / 2)
    m.setScale(s, s, cx, cy)
    return m


def zoom(item, lt, dur, ctx):
    p = ease.in_out_cubic(lt / max(1e-6, dur))
    s = float(item.get("from", 1.0)) + (float(item.get("to", 1.15)) - float(item.get("from", 1.0))) * p
    return _scale(s, ctx), (0.0, 0.0)


def punch(item, lt, dur, ctx):
    a = float(item.get("amount", 0.12))
    s = 1 + a * ease.out_back(min(1.0, lt / 0.12))
    return _scale(s, ctx), (0.0, 0.0)


def whip(item, lt, dur, ctx):
    """Traslacion rapida (para probar el motion blur del nucleo)."""
    v = 60.0 * ctx.s
    m = skia.Matrix()
    m.setTranslate(v * np.sin(lt * 3.0), 0)
    return m, (v, 0.0)


MATRIX_EFFECTS = {"zoom": zoom, "punch": punch, "shake": whip}
POST_EFFECTS = {}


def face_track(base_video, W, H, fps, n_frames):
    f = np.tile(np.array([0.5, 0.26, 0.2, 0.11], np.float32), (n_frames, 1))
    f[40:46] = np.nan          # hueco corto: se interpola
    return f
