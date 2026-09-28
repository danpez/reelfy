"""Invarianza de resolucion: un export 4K (2160x3840) debe verse como el de 1080 (mismo desenfoque,
grano, estela, realce y papel), solo mas nitido. Cada prueba dibuja lo mismo a 1080 y a 2160,
reduce el 4K a 1080 y compara una medida del efecto (con el codigo anterior fallaban)."""
import cv2
import numpy as np
import skia

from motion import ctx as C
from motion import cutaways as CU
from motion import grade as G
from motion import transitions as TR


def _synth(W=1080, H=1920, seed=3):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    a = np.zeros((H, W, 4), np.uint8)
    a[..., 0] = (127 + 90 * np.sin(xx / 37.0) * np.cos(yy / 53.0)).astype(np.uint8)
    a[..., 1] = (127 + 90 * np.sin(yy / 29.0)).astype(np.uint8)
    a[..., 2] = cv2.GaussianBlur(rng.integers(0, 255, (H, W)).astype(np.uint8), (0, 0), 1.5)
    a[..., 3] = 255
    return a


def _up(a, k=2):
    return cv2.resize(a, (a.shape[1] * k, a.shape[0] * k), interpolation=cv2.INTER_CUBIC)


def _down(a, k=2):
    return cv2.resize(a, (a.shape[1] // k, a.shape[0] // k), interpolation=cv2.INTER_AREA)


def _lap(a):
    g = a if a.ndim == 2 else cv2.cvtColor(np.ascontiguousarray(a[..., :3]), cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(g.astype(np.float32), cv2.CV_32F).var())


def _blur_self(W, H, base):
    ctx = C.demo_ctx(W, H, t=1.0)
    ctx._cache["base_image"] = C.image_from_rgba(base)
    arr, surf = C.new_frame(W, H)
    CU._bg_blur_self(surf.getCanvas(), None, 1.0, ctx)
    return arr.copy()


def test_blur_self_mismo_desenfoque_en_4k():
    base = _synth()
    a = _blur_self(1080, 1920, base)
    b = _down(_blur_self(2160, 3840, _up(base)))
    ratio = _lap(b) / _lap(a)
    assert 0.75 < ratio < 1.33, ratio          # antes: ~1.8 (medio desenfoque en 4K)
    assert np.abs(a.astype(int) - b.astype(int))[..., :3].mean() < 3.0


def test_papel_mismas_manchas_en_4k():
    a = CU._paper_array(1080, 1920, np.random.default_rng(11), 14)
    b = CU._paper_array(2160, 3840, np.random.default_rng(11), 28)
    b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    lo_a = cv2.GaussianBlur(a, (0, 0), 40)
    lo_b = cv2.GaussianBlur(b, (0, 0), 40)
    corr = float(np.corrcoef(lo_a.ravel(), lo_b.ravel())[0, 1])
    assert corr > 0.9, corr                     # antes: ~0.05 (otro papel)


def _grain(W, H):
    arr, surf = C.new_frame(W, H)
    surf.getCanvas().clear(skia.Color(128, 128, 128, 255))
    CU.grain(surf.getCanvas(), C.demo_ctx(W, H, t=0.5), 0.05)
    return arr[..., 0].astype(np.float32)


def test_grano_misma_fuerza_en_4k():
    a = _grain(1080, 1920)
    b = _down(_grain(2160, 3840))
    ratio = float(b.std() / a.std())
    assert ratio > 0.8, ratio                   # antes: ~0.5 (grano de medio px)


def _zoom(W, H, base, prev, p):
    it = {"kind": "zoom_blur", "dur": 0.4, "intensity": 1.0, "seed": 1}
    out = TR.zoom_blur(C.image_from_rgba(base), C.image_from_rgba(prev), it, p, C.demo_ctx(W, H, t=2.0))
    return C.image_to_rgba(out)


def test_zoom_blur_misma_estela_en_4k():
    base, prev = _synth(), _synth(seed=5)
    for p in (0.3, 0.4):
        a = _zoom(1080, 1920, base, prev, p).astype(int)
        b = _down(_zoom(2160, 3840, _up(base), _up(prev), p)).astype(int)
        mad = float(np.abs(a - b)[..., :3].mean())
        assert mad < 0.45, (p, mad)             # antes: ~0.7 (nucleo nitido de medio radio)


def test_scanlines_del_glitch_escalan():
    for W, rows in ((1080, 1), (2160, 2)):
        ctx = C.demo_ctx(W, W * 16 // 9)
        img = TR._scanline_shader(ctx)
        period = max(2, int(round(ctx.px(4))))
        surf = skia.Surface(4, period * 3)
        surf.getCanvas().drawPaint(skia.Paint(Shader=img))
        col = surf.toarray()[:, 0, 0]
        assert int((col[:period] < 200).sum()) == rows, (W, col[:period])


def test_nitidez_mismo_realce_en_4k():
    base = _synth()[..., :3].copy()
    a = G.detail_luma(base, 0.4, 0.0, 0.0, 1080, C.demo_ctx(1080, 1920)).astype(np.float32) - 128
    b = G.detail_luma(_up(base), 0.4, 0.0, 0.0, 2160, C.demo_ctx(2160, 3840)).astype(np.float32) - 128
    ratio = float(_down(b).std() / a.std())
    assert ratio > 0.6, ratio                   # antes: ~0.35 (realce de medio ancho)
