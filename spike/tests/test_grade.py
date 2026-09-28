"""Tests de motion/grade.py: LUTs, looks, igualacion de tomas, vinieta, grano, B/N."""
import pickle
import subprocess
import time

import numpy as np
import pytest

from conftest import TEST_MEDIA
from motion import ctx as C, grade

SHOTS = [(0.0, 8.0), (8.0, 13.867), (13.867, 17.267), (17.267, 24.267), (24.267, 28.867), (28.867, 37.7)]


def _img(W=540, H=960, seed=0):
    rng = np.random.default_rng(seed)
    a = np.empty((H, W, 4), np.uint8)
    base = np.linspace(0, 255, W, dtype=np.float32)[None, :, None]
    a[..., :3] = np.clip(base + rng.normal(0, 8, (H, W, 3)), 0, 255).astype(np.uint8)
    a[..., 3] = 255
    return a


def test_luts_monotonic_all_looks():
    corr = {"gains": [1.1, 1.0, 0.9], "black": 0.02, "white": 0.97}
    for name, L in grade.LOOKS.items():
        for c in (None, corr):
            lut = grade.build_lut(L, c, {})
            assert lut.shape == (256, 1, 4) and lut.dtype == np.uint8
            for ch in range(3):
                assert np.all(np.diff(lut[:, 0, ch].astype(int)) >= 0), (name, ch)
            assert np.array_equal(lut[:, 0, 3], np.arange(256))       # alfa intacto
            assert lut[255, 0, :3].max() >= 240                        # blancos no se apagan


def test_identity_when_strength_zero():
    lut = grade.build_lut(grade.LOOKS["natural"], None, {"strength": 0.0})
    assert np.abs(lut[:, 0, :3].astype(int) - np.arange(256)[:, None]).max() <= 1


def test_apply_disabled_and_shape():
    g = grade.Grader(None, [], 540, 960, 30)
    img = C.image_from_rgba(_img())
    ctx = C.demo_ctx(540, 960)
    assert g.apply(img, {"enabled": False}, 0, ctx) is img
    out = g.apply(img, {"look": "cinematic", "vignette": 0.2, "grain": 0.03, "sharpen": 0.2}, 0, ctx)
    arr = C.image_to_rgba(out)
    assert arr.shape == (960, 540, 4) and arr[..., 3].min() == 255


def test_looks_are_distinct_and_skin_safe():
    """Los looks cambian la imagen de forma distinta y el tono de piel sigue en el rango
    de piel (sin verde ni magenta)."""
    skin = np.zeros((64, 64, 4), np.uint8)
    skin[..., :3] = (200, 150, 125)
    skin[..., 3] = 255
    g = grade.Grader(None, [], 64, 64, 30)
    ctx = C.demo_ctx(64, 64)
    outs = {}
    for name in grade.LOOKS:
        o = C.image_to_rgba(g.apply(C.image_from_rgba(skin), {"look": name, "grain": 0, "vignette": 0,
                                                              "sharpen": 0}, None, ctx))
        outs[name] = o[32, 32, :3].astype(int)
        if name != "bw_contrast":
            r, gg, b = outs[name]
            assert r > gg > b, (name, outs[name])                      # sigue siendo piel
        else:
            assert outs[name][0] == outs[name][1] == outs[name][2]
    vals = [tuple(v) for v in outs.values()]
    assert len(set(vals)) == len(vals)


def _stats(skin_rgb, gw, px_mid):
    lin = grade.srgb_to_lin(np.array(skin_rgb) / 255.0)
    px = np.clip(np.random.default_rng(1).normal(px_mid, 0.15, (4000, 3)), 0, 1)
    return {"gw": np.array(gw), "skin": lin, "px": px}


def test_solve_matches_skin_between_shots():
    g = grade.Grader(None, [(0, 1), (1, 2), (2, 3)], 540, 960, 30)
    warm = _stats((215, 140, 95), [1.15, 1.0, 0.82], 0.45)   # luz calida
    cool = _stats((190, 150, 140), [0.95, 1.0, 1.08], 0.45)  # luz de dia fria
    dark = _stats((150, 105, 85), [1.05, 1.0, 0.92], 0.30)   # subexpuesta
    corr = g._solve([warm, cool, dark])
    ratio = lambda st, c: (st["skin"] * np.array(c["gains"]))  # noqa: E731
    before = abs(np.log(warm["skin"][2] / warm["skin"][1]) - np.log(cool["skin"][2] / cool["skin"][1]))
    a, b = ratio(warm, corr[0]), ratio(cool, corr[1])
    after = abs(np.log(a[2] / a[1]) - np.log(b[2] / b[1]))
    assert after < 0.6 * before                                 # las pieles se acercan
    assert np.mean(corr[2]["gains"]) > np.mean(corr[0]["gains"])  # la toma oscura sube
    for c in corr:
        assert all(0.45 < v < 2.1 for v in c["gains"])            # wb [0.8,1.25] x exp 2^+-0.7


def test_solve_without_person_is_gentle():
    g = grade.Grader(None, [(0, 1), (1, 2)], 540, 960, 30)
    s1 = {"gw": np.array([1.1, 1.0, 0.85]), "skin": None, "px": np.full((500, 3), 0.5)}
    s2 = {"gw": np.array([0.9, 1.0, 1.1]), "skin": None, "px": np.full((500, 3), 0.5)}
    corr = g._solve([s1, s2, None][:2])
    for c in corr:
        assert all(0.8 <= v <= 1.25 for v in c["gains"])


def test_vignette_and_grain():
    vm = grade.vignette_map(540, 960, 0.3)
    assert vm[480, 270, 0] >= 250 and vm[0, 0, 0] < 200 and vm[..., 3].min() == 255
    g = grade.Grader(None, [], 540, 960, 30)
    flat = np.full((960, 540, 4), 128, np.uint8)
    cfg = {"look": "natural", "strength": 0, "grain": 0.03, "vignette": 0, "sharpen": 0}
    a = C.image_to_rgba(g.apply(C.image_from_rgba(flat), cfg, None, C.FrameCtx(W=540, H=960, n=3)))
    b = C.image_to_rgba(g.apply(C.image_from_rgba(flat), cfg, None, C.FrameCtx(W=540, H=960, n=3)))
    c = C.image_to_rgba(g.apply(C.image_from_rgba(flat), cfg, None, C.FrameCtx(W=540, H=960, n=4)))
    assert np.array_equal(a, b)                                  # determinista
    assert not np.array_equal(a, c)                              # animado por cuadro
    assert abs(a[..., 0].mean() - 128) < 1.5 and 2.0 < a[..., 0].std() < 8.0


def test_bw_renderer_and_subject_color():
    arr = np.zeros((200, 100, 4), np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3] = 200, 60, 40, 255
    ctx = C.demo_ctx(100, 200)
    out = C.image_to_rgba(grade.bw(C.image_from_rgba(arr), {"contrast": 1.3, "fade": 0}, 0.5, 1.0, ctx))
    assert np.all(out[..., 0] == out[..., 1]) and np.all(out[..., 1] == out[..., 2])
    m = np.zeros((200, 100), np.float32)
    m[50:150, 25:75] = 1.0
    ctx._matte_fn = lambda: m
    out2 = C.image_to_rgba(grade.bw(C.image_from_rgba(arr), {"subject_color": True, "fade": 0}, 0.5, 1.0, ctx))
    assert tuple(out2[100, 50, :3]) == (200, 60, 40)            # sujeto a color
    assert out2[10, 10, 0] == out2[10, 10, 1] == out2[10, 10, 2]  # fondo gris
    # fade de entrada: a lt=0 aun no hay B/N
    out3 = C.image_to_rgba(grade.bw(C.image_from_rgba(arr), {"fade": 0.1}, 0.0, 1.0, ctx))
    assert tuple(out3[10, 10, :3]) == (200, 60, 40)
    assert set(grade.RENDERERS) == {"bw"}


def test_decode_frames_many_indices(tmp_path):
    import paths
    v = tmp_path / "t.mp4"
    subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=96x160:r=30:d=6",
                    "-pix_fmt", "yuv420p", str(v)], check=True)
    idx = list(range(10, 150)) + [3, 160, 170, 171]
    got = grade.decode_frames(str(v), idx, 48, 80)
    assert sorted(got) == sorted(set(idx))
    assert got[10].shape == (80, 48, 3)


@pytest.mark.skipif(not (TEST_MEDIA / "crudo_completo.mp4").exists(), reason="sin video de prueba")
def test_prepare_real_video(tmp_path):
    from _real_media import small_base_video
    v = small_base_video(270, 480, 10)
    g = grade.Grader(v, SHOTS, 270, 480, 10, cache_dir=tmp_path)
    corr = g.prepare()
    assert len(corr) == 6
    assert [c["face"] for c in corr] == [True, True, True, True, False, True]   # la fachada no tiene cara
    for c in corr:
        assert all(0.6 < x < 1.6 for x in c["gains"])
    # cache en disco y serializable para los procesos de render
    t = time.time()
    g2 = grade.Grader(v, SHOTS, 270, 480, 10, cache_dir=tmp_path)
    assert g2.prepare() == corr and time.time() - t < 0.5
    g3 = pickle.loads(pickle.dumps(g))
    assert g3.corr == corr
    img = C.image_from_rgba(_img(270, 480))
    assert g3.apply(img, {"look": "warm_clean"}, 2, C.demo_ctx(270, 480)).width() == 270


def test_perf_final_frame():
    g = grade.Grader(None, [], 1080, 1920, 30)
    img = C.image_from_rgba(_img(1080, 1920))
    cfg = {"look": "warm_clean", "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}
    ctx = C.demo_ctx(1080, 1920)
    g.apply(img, cfg, None, ctx)
    best = 1e9
    for k in range(7):  # minimo: robusto a picos de carga de la maquina
        ctx.n = k
        t = time.perf_counter()
        g.apply(img, cfg, None, ctx)
        best = min(best, time.perf_counter() - t)
    assert best < 0.03 * _load_factor()


# ---------------------------------------------------------------- look con impacto (cuadros reales)
SHOT_T = [3.0, 10.5, 15.2, 20.0, 26.0, 31.5]
_REAL = TEST_MEDIA / "crudo_completo.mp4"


def _luma_sat(rgb):
    x = rgb[..., :3].astype(np.float32) / 255.0
    y = x @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    mx, mn = x.max(-1), x.min(-1)
    return y, ((mx - mn) / np.maximum(mx, 1e-3))


def _prepared(tmp_path):
    from _real_media import small_base_video
    v = small_base_video(540, 960, 10)
    g = grade.Grader(v, SHOTS, 540, 960, 10, cache_dir=tmp_path)
    g.prepare()
    return g


@pytest.mark.skipif(not _REAL.exists(), reason="sin video de prueba")
def test_default_look_has_reference_impact(tmp_path):
    """El look por defecto (warm_clean) se acerca a la referencia: negros profundos, mas
    saturacion (+25% o mas en promedio) y la piel sigue en el rango de tono de piel."""
    import cv2
    import paths
    from _real_media import real_frames
    g = _prepared(tmp_path)
    frames = real_frames(SHOT_T)
    ctx = C.demo_ctx(540, 960)
    cfg = {"look": "warm_clean", "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}
    p10s, gains = [], []
    det = cv2.FaceDetectorYN.create(str(paths.YUNET), "", (540, 960), 0.7, 0.3, 5000)
    for i, fr in enumerate(frames):
        out = C.image_to_rgba(g.apply(C.image_from_rgba(fr), cfg, i, ctx))
        y0, s0 = _luma_sat(fr)
        y1, s1 = _luma_sat(out)
        p10s.append(float(np.percentile(y1, 10)))
        gains.append(float(s1.mean() / s0.mean()) - 1.0)
        assert np.percentile(y1, 50) <= np.percentile(y0, 50) + 0.02       # los medios no suben
        skin = grade._skin_pixels(np.ascontiguousarray(out[..., :3]), det)
        if i != 4 and skin is not None:
            hsv = cv2.cvtColor((np.median(skin, axis=0)[None, None] * 255).astype(np.uint8), cv2.COLOR_RGB2HSV)
            assert 3 <= int(hsv[0, 0, 0]) * 2 <= 42, (i, hsv)                  # tono de piel natural
    assert np.median(p10s) <= 0.06 and max(p10s) <= 0.11, p10s
    assert np.mean(gains) >= 0.25 and min(gains) >= 0.15, gains


@pytest.mark.skipif(not _REAL.exists(), reason="sin video de prueba")
def test_shot_matching_real_skin_dispersion(tmp_path):
    """Tras igualar, la dispersion entre tomas de log(R/G), log(B/G) y la luma de la piel cae a
    la mitad o menos de la original."""
    import cv2
    import paths
    from _real_media import real_frames
    g = _prepared(tmp_path)
    det = cv2.FaceDetectorYN.create(str(paths.YUNET), "", (540, 960), 0.7, 0.3, 5000)
    ctx = C.demo_ctx(540, 960)
    cfg = {"look": "natural", "strength": 0.0, "vignette": 0, "grain": 0, "sharpen": 0, "clarity": 0}

    def skin(frames):
        px = [grade._skin_pixels(np.ascontiguousarray(f[..., :3]), det) for f in frames]
        px = [p for p in px if p is not None]
        s = np.median(grade.srgb_to_lin(np.concatenate(px)), axis=0)
        return np.log(s[0] / s[1]), np.log(s[2] / s[1]), float(grade.luma(grade.lin_to_srgb(s)))

    before, after = [], []
    for si, (a, b) in enumerate(SHOTS):
        if si == 4:
            continue                                                    # fachada: sin persona
        frs = real_frames([a + (b - a) * (k + 0.5) / 8 for k in range(8)])
        before.append(skin(frs))
        after.append(skin([C.image_to_rgba(g.apply(C.image_from_rgba(f), cfg, si, ctx)) for f in frs]))
    B, A = np.array(before), np.array(after)
    ratio = A.std(axis=0) / np.maximum(B.std(axis=0), 1e-9)
    assert np.all(ratio <= 0.5), ratio


@pytest.mark.skipif(not _REAL.exists(), reason="sin video de prueba")
def test_bw_filmic_street(tmp_path):
    """B/N filmico en la calle: altas luces suaves (no fotocopia), negros levantados, la cara
    mucho mas luminosa que las esquinas (vinieta en la cara) y `contrast` perceptible."""
    from _real_media import real_frames
    from motion import matte
    g = _prepared(tmp_path)
    fr = real_frames([20.0])[0]
    f = max(matte.detect_faces(np.ascontiguousarray(fr[..., :3])), key=lambda f: f[2] * f[3])
    ctx = C.FrameCtx(W=540, H=960, fps=30, n=5, face=tuple(f[:4]))
    base = g.apply(C.image_from_rgba(fr), {"look": "warm_clean", "vignette": 0.15, "grain": 0.025,
                                            "sharpen": 0.15}, 3, ctx)
    stds = {}
    for c in (0.8, 1.2, 2.0):
        o = C.image_to_rgba(grade.bw(base, {"contrast": c, "fade": 0}, 1.0, 2.0, ctx))
        y = o[..., 0].astype(np.float32) / 255.0
        stds[c] = float(y.std())
        if c == 1.2:
            p2, p98 = np.percentile(y, [2, 98])
            assert p98 <= 0.93 and p2 >= 0.03, (p2, p98)
            cx, cy, fw, fh = f[:4]
            face = y[int((cy - 0.1 * fh) * 960):int((cy + 0.3 * fh) * 960), int((cx - 0.25 * fw) * 540):int((cx + 0.25 * fw) * 540)]
            k = 100
            corners = np.concatenate([y[:k, :k].ravel(), y[:k, -k:].ravel(), y[-k:, :k].ravel(), y[-k:, -k:].ravel()])
            assert float(np.median(face)) - float(corners.mean()) >= 0.25
            assert len(np.unique(o[..., 0])) > 200                          # sin posterizar
    assert stds[2.0] - stds[0.8] >= 0.04, stds


def test_grain_never_repeats_or_freezes():
    W, H = 96, 160
    prev = None
    seen = []
    for n in range(0, 62):
        g = grade.grain_frame(W, H, n, seed=7).copy()
        if prev is not None:
            assert not np.array_equal(g, prev), n
        prev = g
        seen.append(g)
    for n in range(0, 50):
        for k in (4, 6, 8, 12):
            assert not np.array_equal(seen[n], seen[n + k]), (n, k)      # sin periodo corto
    assert np.array_equal(grade.grain_frame(W, H, 9, 7), grade.grain_frame(W, H, 9, 7))   # determinista


def test_sat_matrix_protects_skin_and_luma():
    M = grade.sat_matrix(1.3, 1.08)
    gray = np.full((4, 4, 3), 120, np.uint8)
    out = cv2_transform(gray, M)
    assert np.abs(out.astype(int) - 120).max() <= 1                     # gris sigue gris
    skin = np.array([[[200, 150, 125]]], np.uint8)
    teal = np.array([[[90, 165, 70]]], np.uint8)   # verde: perpendicular al eje de piel

    def chroma_gain(px):
        o = cv2_transform(px, M).astype(float)[0, 0]
        p = px.astype(float)[0, 0]
        return np.ptp(o) / np.ptp(p)

    assert chroma_gain(skin) < chroma_gain(teal) - 0.1                  # la piel se satura menos
    assert grade.sat_matrix(1.0, 1.0) is None


def cv2_transform(px, M):
    import cv2
    return cv2.transform(px, M[:3, :3])


def test_strength_above_one_is_stronger_and_monotonic():
    L = grade.LOOKS["warm_clean"]
    a = grade.build_lut(L, None, {"strength": 1.0})
    b = grade.build_lut(L, None, {"strength": 1.5})
    for ch in range(3):
        assert np.all(np.diff(b[:, 0, ch].astype(int)) >= 0)
    assert b[40, 0, 1] < a[40, 0, 1] and b[215, 0, 1] >= a[215, 0, 1]   # mas contraste


@pytest.mark.parametrize("bad", [None, "x", float("nan"), [1, 2], -9, 1e6])
def test_garbage_cfg_never_raises(bad):
    g = grade.Grader(None, [], 64, 96, 30)
    img = C.image_from_rgba(_img(64, 96))
    ctx = C.demo_ctx(64, 96)
    for k in ("look", "strength", "exposure", "temperature", "tint", "saturation", "contrast",
              "vignette", "grain", "sharpen", "clarity"):
        assert g.apply(img, {k: bad}, 0, ctx).width() == 64
    for k in ("contrast", "fade", "tone", "vignette", "lift", "intensity", "subject_color"):
        assert grade.bw(img, {k: bad}, 0.5, 1.0, ctx).width() == 64


def test_perf_single_thread_worker():
    """Grade completo en un worker (cv2 a 1 hilo) en final: debe dejar margen al resto del
    cuadro (camara + grade <= ~18 ms)."""
    import cv2
    prev_threads = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        g = grade.Grader(None, [], 1080, 1920, 30)
        img = C.image_from_rgba(_img(1080, 1920))
        cfg = {"look": "warm_clean", "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}
        ctx = C.demo_ctx(1080, 1920)
        g.apply(img, cfg, None, ctx)
        arr = _img(1080, 1920)
        lut = g.lut(cfg, None)
        best = best_arr = 1e9
        for k in range(8):
            ctx.n = k
            t = time.perf_counter()
            g.apply(img, cfg, None, ctx)
            best = min(best, time.perf_counter() - t)
            t = time.perf_counter()
            grade.grade_array(arr, cfg, lut, ctx, g._vig)
            best_arr = min(best_arr, time.perf_counter() - t)
        # meta de la ola 2: el grade por cuadro <= 8 ms a 1 hilo (antes ~12.6-13.5 ms); con
        # las conversiones skia <-> numpy de apply, <= 9 ms
        lf = _load_factor()
        assert best_arr < 0.008 * lf, best_arr
        assert best < 0.009 * lf, best
    finally:
        cv2.setNumThreads(prev_threads)


def _load_factor() -> float:
    """Holgura por carga comun a la suite (conftest.load_factor)."""
    from conftest import load_factor
    return load_factor()
