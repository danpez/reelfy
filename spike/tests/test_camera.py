"""Tests de motion/camera.py: matrices de encuadre, cobertura sin bordes, determinismo,
motion blur, efectos de post y seguimiento de rostro."""
import math
import pickle
import subprocess

import numpy as np
import pytest
import skia

from conftest import TEST_MEDIA
from motion import camera, ctx as C

FPS = 30.0
EFFECTS = {
    "zoom": {"kind": "zoom", "id": "z", "from": 1.0, "to": 1.25},
    "punch": {"kind": "punch", "id": "p", "amount": 0.2, "hold": 0.5},
    "shake": {"kind": "shake", "id": "s", "amp": 30, "freq": 12, "decay": 0.6, "rot": 1.2},
    "kenburns": {"kind": "kenburns", "id": "k", "from": 1.0, "to": 1.15, "pan": [0.06, -0.04]},
}


def _ctx(face=(0.5, 0.3, 0.1, 0.06), W=1080, H=1920, seed=7, n=0):
    return C.FrameCtx(W=W, H=H, fps=FPS, n=n, face=face, seed=seed)


def _covers(M, W, H, tol=0.75):
    inv = skia.Matrix()
    assert M.invert(inv)
    for x, y in [(0, 0), (W, 0), (0, H), (W, H), (W / 2, 0), (0, H / 2), (W, H / 2), (W / 2, H)]:
        p = inv.mapXY(x, y)
        if not (-tol <= p.x() <= W + tol and -tol <= p.y() <= H + tol):
            return False
    return True


def test_registry_and_signature():
    assert set(camera.MATRIX_EFFECTS) == {"zoom", "punch", "shake", "kenburns"}
    assert set(camera.POST_EFFECTS) == {"rgb_split", "flash_frame"}
    for k, it in EFFECTS.items():
        M, mv = camera.MATRIX_EFFECTS[k](it, 0.2, 1.0, _ctx())
        assert isinstance(M, skia.Matrix)
        mx, my = mv                       # contrato: tupla de 2
        assert len(mv) == 2 and all(math.isfinite(v) for v in (mx, my))
        assert mv.cur is not None and mv.prev is not None


@pytest.mark.parametrize("kind", list(EFFECTS))
@pytest.mark.parametrize("face", [None, (0.5, 0.3, 0.1, 0.06), (0.05, 0.04, 0.08, 0.05),
                                  (0.97, 0.95, 0.1, 0.06), (float("nan"),) * 4])
def test_always_covers_frame(kind, face):
    """Ningun efecto deja ver bordes: la imagen transformada cubre todo el cuadro."""
    it = dict(EFFECTS[kind])
    for dur in (0.4, 1.0, 3.0):
        for lt in np.linspace(0, dur, 31):
            M, _ = camera.MATRIX_EFFECTS[kind](it, float(lt), dur, _ctx(face))
            assert _covers(M, 1080, 1920), (kind, face, dur, lt, M.get9())


def test_zoom_endpoints_and_anchor():
    ctx = _ctx((0.4, 0.3, 0.1, 0.06))
    it = {"kind": "zoom", "from": 1.0, "to": 1.3, "easing": "linear"}
    M0, _ = camera.zoom(it, 0.0, 2.0, ctx)
    M1, _ = camera.zoom(it, 2.0, 2.0, ctx)
    assert M0.getScaleX() == pytest.approx(1.0, abs=1e-6)
    assert M1.getScaleX() == pytest.approx(1.3, abs=1e-6)
    # la cara es el punto fijo del zoom
    p = M1.mapXY(0.4 * 1080, 0.3 * 1920)
    assert p.x() == pytest.approx(0.4 * 1080, abs=0.5) and p.y() == pytest.approx(0.3 * 1920, abs=0.5)
    # item.center fuerza el ancla
    Mc, _ = camera.zoom(dict(it, center=[0.5, 0.5]), 2.0, 2.0, ctx)
    q = Mc.mapXY(540, 960)
    assert q.x() == pytest.approx(540, abs=0.5) and q.y() == pytest.approx(960, abs=0.5)


def test_zoom_easing_is_smooth():
    ctx = _ctx()
    it = {"kind": "zoom", "from": 1.0, "to": 1.2}
    ss = [camera.zoom(it, lt, 1.5, ctx)[0].getScaleX() for lt in np.arange(0, 1.5 + 1e-9, 1 / FPS)]
    d = np.diff(ss)
    assert np.all(d >= -1e-9)                                # monotono
    assert d[0] < d[len(d) // 2] and d[-1] < d[len(d) // 2]  # arranca y llega suave


def test_punch_spring_hold_release():
    ctx = _ctx()
    it = {"kind": "punch", "amount": 0.15, "hold": 0.5}
    s = lambda lt, dur=1.2: camera.punch(it, lt, dur, ctx)[0].getScaleX()  # noqa: E731
    assert s(0.0) == pytest.approx(1.0, abs=1e-6)
    assert s(0.1) > 1.13                                  # llega rapido
    peak = max(s(t) for t in np.arange(0.05, 0.4, 0.005))
    assert peak > 1.15 + 1e-3                             # un rebote (sobrepasa)
    assert s(0.45) == pytest.approx(1.15, abs=0.01)       # asentado
    assert s(1.2) == pytest.approx(1.0, abs=1e-3)         # suelta suave al terminar el golpe
    # modo cut: escala instantanea
    it2 = {"kind": "punch", "amount": 0.1, "mode": "cut", "release": "cut"}
    assert camera.punch(it2, 0.0, 1.0, ctx)[0].getScaleX() == pytest.approx(1.1, abs=1e-6)


def test_shake_deterministic_smooth_and_decays():
    it = dict(EFFECTS["shake"])
    a = [camera.shake(it, lt, 1.0, _ctx(seed=3))[0].get9() for lt in np.arange(0, 1, 1 / FPS)]
    b = [camera.shake(it, lt, 1.0, _ctx(seed=3))[0].get9() for lt in np.arange(0, 1, 1 / FPS)]
    c = [camera.shake(it, lt, 1.0, _ctx(seed=4))[0].get9() for lt in np.arange(0, 1, 1 / FPS)]
    assert a == b
    assert a != c
    tx = np.array([m[2] for m in a])
    # ruido suave: el salto por cuadro esta acotado por amplitud * 2*pi*freq / fps
    bound = C.demo_ctx().px(30) * 2 * math.pi * 12 / FPS * 1.6
    assert np.max(np.abs(np.diff(tx))) < bound
    # decae: la energia del ultimo tercio es mucho menor que la del primero
    dev = np.abs(tx - np.median(tx))
    assert dev[-10:].mean() < 0.35 * dev[:10].mean() + 1.0
    # al final vuelve a identidad (sin salto al terminar el item)
    Mend, _ = camera.shake(it, 1.0, 1.0, _ctx())
    assert camera.is_identity(Mend)


def test_kenburns_continuous():
    ctx = _ctx((0.5, 0.45, 0.1, 0.06))
    it = dict(EFFECTS["kenburns"])
    mats = [camera.kenburns(it, lt, 2.0, ctx)[0].get9() for lt in np.arange(0, 2.0 + 1e-9, 1 / FPS)]
    tx = np.array([m[2] for m in mats])
    sx = np.array([m[0] for m in mats])
    assert np.max(np.abs(np.diff(tx))) < 12      # sin quiebres ni saltos
    assert np.all(np.diff(sx) >= -1e-9)
    assert abs(tx[-1] - tx[0]) > 20              # si hay paneo


def test_combine_and_motion_vector():
    ctx = _ctx()
    M, mv = camera.combine([], ctx)
    assert camera.is_identity(M) and mv.speed == 0
    r1 = camera.zoom({"from": 1.2, "to": 1.2}, 0.5, 1.0, ctx)      # estatico
    r2 = camera.shake(dict(EFFECTS["shake"]), 0.1, 1.0, ctx)
    M, mv = camera.combine([r1, r2], ctx)
    ref = skia.Matrix.Concat(r2[0], r1[0])                         # r1 primero, luego r2
    assert np.allclose(M.get9(), ref.get9())
    assert mv.speed > 0
    Ms, mvs = camera.combine([r1], ctx)
    assert mvs.speed == pytest.approx(0.0, abs=1e-6)
    # Motion es serializable (render multiproceso)
    assert tuple(pickle.loads(pickle.dumps(mv))) == tuple(mv)


def _edge_image(W=540, H=960):
    a = np.zeros((H, W, 4), np.uint8)
    a[..., 3] = 255
    a[:, W // 2:, :3] = 255
    return a


def test_warp_identity_and_scale():
    ctx = _ctx(W=540, H=960)
    img = C.image_from_rgba(_edge_image())
    assert camera.warp(img, skia.Matrix(), ctx) is img
    M = camera.scale_about(2.0, 270, 480)
    out = C.image_to_rgba(camera.warp(img, M, ctx))
    assert out.shape == (960, 540, 4)
    # el borde vertical sigue en el centro (punto fijo) y la imagen sigue opaca
    row = out[480, :, 0].astype(int)
    assert row[260] < 30 and row[280] > 225 and out[..., 3].min() == 255
    m = camera.warp_mask(np.ones((960, 540), np.float32), M, ctx)
    assert m.shape == (960, 540) and m.min() > 0.99


def test_motion_blur_translation_and_zoom():
    ctx = _ctx(W=540, H=960)
    arr = _edge_image()
    img = C.image_from_rgba(arr)
    assert camera.motion_blur(img, (0.0, 0.0), ctx) is img
    out = C.image_to_rgba(camera.motion_blur(img, (24.0, 0.0), ctx))
    row = out[480, :, 0].astype(int)
    ramp = np.count_nonzero((row > 20) & (row < 235))
    assert ramp >= 6                                   # el borde se barre en x
    col = out[:, 100, 0].astype(int)
    assert col.max() - col.min() <= 2                  # nada de blur en y
    # zoom real: el ancla del zoom queda nitida y las orillas se barren
    ctx2 = C.FrameCtx(W=540, H=960, fps=FPS, face=(0.5, 0.5, 0.1, 0.06))
    it = {"kind": "punch", "amount": 0.2}
    M, mv = camera.punch(it, 0.05, 1.0, ctx2)
    assert camera.needs_blur(mv, ctx2)
    checker = np.zeros((960, 540, 4), np.uint8)
    checker[..., 3] = 255
    checker[((np.indices((960, 540)).sum(0) // 8) % 2) == 1, :3] = 255
    w = camera.warp(C.image_from_rgba(checker), M, ctx2)
    b = C.image_to_rgba(camera.motion_blur(w, mv, ctx2))
    wa = C.image_to_rgba(w)
    center_diff = np.abs(b[470:490, 260:280, 0].astype(int) - wa[470:490, 260:280, 0]).mean()
    corner_diff = np.abs(b[:60, :60, 0].astype(int) - wa[:60, :60, 0]).mean()
    assert center_diff < 12 and corner_diff > 25


def test_rgb_split_and_flash():
    ctx = _ctx(W=540, H=960)
    arr = _edge_image()
    img = C.image_from_rgba(arr)
    o = C.image_to_rgba(camera.rgb_split(img, {"amount": 12, "radial": 0.0}, 0.0, 0.4, ctx))
    r, g, b = (o[480, :, i].astype(int) for i in range(3))
    xr, xg, xb = (int(np.argmax(ch > 127)) for ch in (r, g, b))
    assert xr > xg > xb                                  # rojo y azul separados en sentidos opuestos
    assert camera.rgb_split(img, {"amount": 12}, 0.4, 0.4, ctx) is img   # termina limpio
    f0 = C.image_to_rgba(camera.flash_frame(img, {}, 0.0, 0.4, ctx))
    assert f0[:, :270, :3].mean() > 150                  # el primer cuadro ya destella
    assert camera.flash_frame(img, {}, 0.4, 0.4, ctx) is img
    warm = C.image_to_rgba(camera.flash_frame(img, {"color": "#FF8800"}, 0.03, 0.4, ctx))
    assert warm[:, :270, 0].mean() > warm[:, :270, 2].mean() + 40


def test_track_postprocess_cuts_gaps_and_smoothing():
    rng = np.random.default_rng(0)
    n = 300
    sf = np.arange(0, n, 3)
    s = np.zeros((len(sf), 4))
    s[:, 0] = np.where(sf < 150, 0.3, 0.7) + rng.normal(0, 0.01, len(sf))   # corte en 150
    s[:, 1] = 0.3
    s[:, 2] = 0.1
    s[:, 3] = 0.06
    s[(sf >= 240) & (sf < 280)] = np.nan                                     # 1.3 s sin cara
    out = camera.track_postprocess(s, sf, n, FPS)
    assert out.shape == (n, 4)
    assert np.all(np.isfinite(out[:230]))
    assert np.all(np.isnan(out[255:265, 0]))                                 # hueco largo -> NaN
    assert np.all(out[:140, 0] < 0.35) and np.all(out[160:230, 0] > 0.65)     # no mezcla el corte
    assert np.std(np.diff(out[20:130, 0])) < np.std(np.diff(s[7:43, 0])) / 3  # suaviza
    # con cortes explicitos: un salto pequeno tampoco se suaviza a traves del corte
    s2 = s.copy()
    s2[:, 0] = np.where(sf < 150, 0.40, 0.44)
    s2[(sf >= 240) & (sf < 280)] = np.nan
    a = camera.track_postprocess(s2, sf, n, FPS)
    b = camera.track_postprocess(s2, sf, n, FPS, cuts=[150])
    assert abs(a[149, 0] - 0.40) > 0.002                 # sin cortes se mezcla
    assert b[149, 0] == pytest.approx(0.40, abs=1e-6) and b[150, 0] == pytest.approx(0.44, abs=1e-6)


def test_face_track_synthetic_no_face(tmp_path):
    import paths
    v = tmp_path / "noface.mp4"
    subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=270x480:r=30:d=1",
                    "-pix_fmt", "yuv420p", str(v)], check=True)
    ft = camera.face_track(v, 270, 480, 30, 30)
    assert ft.shape == (30, 4) and np.all(np.isnan(ft))


@pytest.mark.skipif(not (TEST_MEDIA / "crudo_completo.mp4").exists(), reason="sin video de prueba")
def test_face_track_real_clip(tmp_path):
    import paths
    v = tmp_path / "street.mp4"
    subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-ss", "19", "-t", "1.5",
                    "-i", str(TEST_MEDIA / "crudo_completo.mp4"), "-an",
                    "-vf", "fps=30,scale=540:960", "-pix_fmt", "yuv420p", str(v)], check=True)
    ft = camera.face_track(v, 540, 960, 30, 45, cache_dir=tmp_path)
    ok = np.isfinite(ft[:, 0])
    assert ok.mean() > 0.9
    assert 0.3 < np.nanmedian(ft[:, 0]) < 0.7 and 0.15 < np.nanmedian(ft[:, 1]) < 0.5
    assert np.nanmax(np.abs(np.diff(ft[:, 0]))) < 0.02   # pista suave
    assert list(tmp_path.glob("face_*.npy"))              # cache en disco
    assert np.allclose(camera.face_track(v, 540, 960, 30, 45, cache_dir=tmp_path), ft, equal_nan=True)


def test_perf_budget():
    """Camara completa (matrices + warp + blur) dentro de una fraccion del presupuesto."""
    import time
    arr = np.full((1920, 1080, 4), 128, np.uint8)
    img = C.image_from_rgba(arr)
    ctx = _ctx()
    best = 1e9
    for k in range(5):  # minimo: robusto a picos de carga de la maquina
        t = time.perf_counter()
        M, mv = camera.combine([camera.punch({"amount": 0.2}, 0.05, 1.0, ctx)], ctx)
        w = camera.warp(img, M, ctx)
        camera.motion_blur(w, mv, ctx)
        best = min(best, time.perf_counter() - t)
    assert best < 0.04


def test_punch_reframe_target_moves_face():
    ctx = _ctx((0.5, 0.2, 0.08, 0.05))
    it = {"kind": "punch", "amount": 0.3, "mode": "cut", "target": [0.5, 0.33]}
    M, _ = camera.punch(it, 0.2, 1.0, ctx)
    p = M.mapXY(0.5 * 1080, 0.2 * 1920)
    assert p.y() > 0.2 * 1920 + 50            # la cara baja hacia el tercio superior
    assert _covers(M, 1080, 1920)
    # target inalcanzable: se acota sin bordes
    M2, _ = camera.zoom({"from": 1.0, "to": 1.1, "target": [0.95, 0.95]}, 1.0, 1.0, ctx)
    assert _covers(M2, 1080, 1920)


# ---------------------------------------------------------------- ancla estable
def test_item_anchor_median_and_none():
    tr = np.full((100, 4), np.nan)
    tr[10:60, 0], tr[10:60, 1] = 0.42, 0.31
    tr[40:60, 0] = 0.46
    a = camera.item_anchor(tr, 0, 60)
    assert a is not None and a[0] == pytest.approx(0.42, abs=0.02) and a[1] == pytest.approx(0.31)
    assert camera.item_anchor(tr, 70, 100) is None                  # sin cara en el rango
    assert camera.item_anchor(None, 0, 10) is None
    assert camera.item_anchor(tr, -5, 1000) is not None              # rangos fuera se acotan


def test_anchor_prefers_item_anchor_over_face():
    ctx = _ctx((0.8, 0.8, 0.1, 0.06))
    it = {"kind": "zoom", "id": "za", "from": 1.3, "to": 1.3, "_anchor": [0.3, 0.3]}
    M, _ = camera.zoom(it, 0.5, 1.0, ctx)
    p = M.mapXY(0.3 * 1080, 0.3 * 1920)
    assert p.x() == pytest.approx(0.3 * 1080, abs=0.5) and p.y() == pytest.approx(0.3 * 1920, abs=0.5)


@pytest.mark.parametrize("kind", ["zoom", "punch", "kenburns"])
def test_face_lost_mid_item_does_not_jump(kind):
    """Pista con NaN en el 40% central del item: el encuadre no salta al centro (se mantiene la
    ultima cara valida): el mismo encuadre que con la cara siempre presente."""
    dur = 2.0
    n = int(dur * FPS)

    def run(tag, lost):
        it = dict(EFFECTS[kind], id=f"nan_{kind}_{tag}")
        if kind == "zoom":
            it.update({"from": 1.3, "to": 1.3})
        out = []
        for k in range(n):
            gone = lost and 0.3 * n <= k < 0.7 * n
            face = (float("nan"),) * 4 if gone else (0.25, 0.2, 0.1, 0.06)
            v = camera.MATRIX_EFFECTS[kind](it, k / FPS, dur, _ctx(face, n=k))[0].get9()
            out.append((v[2], v[5]))
        return np.array(out)

    a, b = run("ref", False), run("nan", True)
    assert np.abs(a - b).max() < 1e-6


# ---------------------------------------------------------------- sacudida
def test_shake_decay_monotonic_and_clamped():
    envs = [camera._shake_env({"decay": d}, 0.3, 2.0) for d in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 3.0)]
    assert all(a >= b - 1e-12 for a, b in zip(envs, envs[1:]))        # mas decay = menos energia
    assert envs[-1] == envs[-2] == envs[4]                            # > 1 se acota a 1


def test_shake_first_frame_has_motion():
    it = dict(EFFECTS["shake"], id="s0")
    M, mv = camera.shake(it, 0.0, 1.0, _ctx())
    assert not camera.is_identity(M)
    assert mv.speed > 5.0                                            # el golpe inicial lleva estela
    assert not camera.is_identity(mv.prev) and mv.prev.getScaleX() < M.getScaleX()   # medio camino
    # el zoom con from > 1 NO inventa un vector en el primer cuadro
    Mz, mvz = camera.zoom({"from": 1.3, "to": 1.3}, 0.0, 1.0, _ctx())
    assert mvz.speed == pytest.approx(0.0, abs=1e-6)
    # punch de corte: sin estela en el golpe
    _, mvp = camera.punch({"amount": 0.2, "mode": "cut"}, 0.0, 1.0, _ctx())
    assert mvp.speed == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------- ken burns
def test_kenburns_identity_at_start_and_no_inflation():
    ctx = _ctx((0.3, 0.25, 0.1, 0.06))
    it = {"kind": "kenburns", "id": "kb", "from": 1.0, "to": 1.15, "pan": [0.5, -0.5]}
    M0, _ = camera.kenburns(it, 0.0, 2.0, ctx)
    assert camera.is_identity(M0)                                    # sin salto al entrar
    M1, _ = camera.kenburns(it, 2.0, 2.0, ctx)
    assert M1.getScaleX() == pytest.approx(1.15, abs=1e-6)           # respeta `to` (no infla)
    # sin zoom no hay margen: identidad (no un zoom que nadie pidio)
    Mz, _ = camera.kenburns({"from": 1.0, "to": 1.0, "pan": [0.3, 0.0]}, 1.0, 2.0, ctx)
    assert camera.is_identity(Mz)


@pytest.mark.parametrize("face", [(0.5, 0.45, 0.1, 0.06), (0.05, 0.05, 0.1, 0.06), (0.95, 0.9, 0.1, 0.06)])
def test_kenburns_smooth_near_edges(face):
    """Con la cara en un borde el paneo no choca: velocidad sin quiebres (segunda diferencia
    acotada) y siempre cubre."""
    it = {"kind": "kenburns", "id": "kbe", "from": 1.1, "to": 1.2, "pan": [0.08, 0.05], "easing": "linear"}
    mats = [camera.kenburns(it, lt, 3.0, _ctx(face))[0] for lt in np.arange(0, 3.0 + 1e-9, 1 / FPS)]
    tx = np.array([m.get9()[2] for m in mats])
    assert all(_covers(m, 1080, 1920) for m in mats)
    assert np.max(np.abs(np.diff(tx, 2))) < 0.6                      # sin quiebres de velocidad


def test_whip_matrix_scales_with_duration():
    ctx = _ctx()
    _, a = camera.whip_matrix(0.5, "left", ctx, dur=0.35)
    _, b = camera.whip_matrix(0.5, "left", ctx, dur=0.7)
    assert abs(a[0]) > 1.15 * abs(b[0])                              # transicion mas larga = mas lenta


# ---------------------------------------------------------------- motion blur desde el base
def test_motion_blur_samples_real_content_from_base():
    """En un zoom rapido las muestras hacia el cuadro anterior ven contenido fuera del encuadre:
    con el base (automatico tras warp) ese contenido es real, no el borde replicado."""
    W, H = 540, 960
    ctx = C.FrameCtx(W=W, H=H, fps=FPS, face=(0.5, 0.5, 0.1, 0.06))
    base = np.zeros((H, W, 4), np.uint8)
    base[..., 3] = 255
    base[..., :3] = 128
    base[:50, :, 0], base[:50, :, 1], base[:50, :, 2] = 255, 0, 0      # banda roja arriba (fuera del zoom)
    it = {"kind": "punch", "amount": 0.25, "center": [0.5, 0.5]}
    M, mv = camera.punch(it, 0.05, 1.0, ctx)
    w = camera.warp(C.image_from_rgba(base), M, ctx)
    top_sharp = C.image_to_rgba(w)[:6, W // 2, 0].astype(int)
    assert top_sharp.max() < 140                                     # el encuadre nitido no ve rojo
    auto = C.image_to_rgba(camera.motion_blur(w, mv, ctx))           # reconoce la salida del warp
    expl = C.image_to_rgba(camera.motion_blur(w, mv, ctx, base=base, M=M))
    assert np.array_equal(auto, expl)
    top = auto[:6, W // 2, :3].astype(int)
    assert (top[:, 0] - top[:, 2]).max() > 8                         # la estela trae rojo real
    one = C.image_to_rgba(camera.apply_camera(C.image_from_rgba(base), M, mv, ctx))
    assert np.abs(one.astype(int) - auto.astype(int)).max() <= 2


def test_apply_camera_without_blur_is_warp():
    ctx = _ctx(W=540, H=960)
    img = C.image_from_rgba(_edge_image())
    M = camera.scale_about(1.3, 270, 480)
    a = C.image_to_rgba(camera.apply_camera(img, M, (0.0, 0.0), ctx))
    b = C.image_to_rgba(camera.warp(img, M, ctx))
    assert np.array_equal(a, b)


# ---------------------------------------------------------------- cortes de toma
def test_detect_cuts_synthetic():
    rng = np.random.default_rng(0)
    n = 60
    a = np.zeros((n, 42, 24, 3), np.uint8)
    for i in range(n):                                       # paneo lento: sin cortes
        a[i] = np.roll(np.tile(np.linspace(0, 255, 24, dtype=np.uint8)[None, :, None], (42, 1, 3)), i // 3, axis=1)
    b = a.copy()
    b[30:] = rng.integers(0, 255, (1, 42, 24, 3), dtype=np.uint8)   # corte en 30
    assert camera.detect_cuts(a) == []
    assert camera.detect_cuts(b) == [30]


def test_face_track_detects_cut_in_synthetic_video(tmp_path):
    import paths
    v = tmp_path / "cut.mp4"
    subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=1",
                    "-f", "lavfi", "-i", "smptebars=s=180x320:r=30:d=1",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-pix_fmt", "yuv420p",
                    str(v)], check=True)
    ft, cuts = camera.face_track(v, 180, 320, 30, 60, return_cuts=True)
    assert ft.shape == (60, 4) and cuts == [30]
    ft2, cuts2 = camera.face_track(v, 180, 320, 30, 60, cuts=[], return_cuts=True)
    assert cuts2 == []


# ---------------------------------------------------------------- parametros basura
GARBAGE = [None, "abc", float("nan"), [1], {"x": 1}, -5, 1e9]


@pytest.mark.parametrize("kind", list(EFFECTS))
@pytest.mark.parametrize("bad", GARBAGE, ids=[repr(g)[:12] for g in GARBAGE])
def test_garbage_params_never_raise(kind, bad):
    keys = {"zoom": ["from", "to", "easing", "center", "target", "intensity"],
            "punch": ["amount", "hold", "attack", "freq", "damping", "target", "mode"],
            "shake": ["amp", "freq", "decay", "rot", "attack"],
            "kenburns": ["from", "to", "pan", "easing", "_anchor"]}[kind]
    for k in keys:
        it = dict(EFFECTS[kind], **{k: bad})
        for lt in (0.0, 0.3, 1.0):
            M, mv = camera.MATRIX_EFFECTS[kind](it, lt, 1.0, _ctx())
            assert _covers(M, 1080, 1920) and all(math.isfinite(v) for v in mv)


@pytest.mark.parametrize("bad", GARBAGE + ["red", "#12"], ids=[repr(g)[:12] for g in GARBAGE + ["red", "#12"]])
def test_post_garbage_params(bad):
    ctx = _ctx(W=270, H=480)
    img = C.image_from_rgba(_edge_image(270, 480))
    for k in ("amount", "angle", "radial", "jitter", "attack", "hold"):
        camera.rgb_split(img, {k: bad, "amount": 10} if k != "amount" else {k: bad}, 0.05, 0.4, ctx)
    for k in ("color", "peak", "attack", "bloom", "intensity"):
        out = camera.flash_frame(img, {k: bad}, 0.02, 0.4, ctx)
        assert out.width() == 270


def test_perf_single_thread_worker():
    """Como en un worker de render que comparte CPU (cv2 a 1 hilo): punch con motion blur y
    sacudida, en final 1080x1920 (min de varias corridas)."""
    import time
    import cv2
    prev_threads = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        arr = np.random.default_rng(0).integers(0, 255, (1920, 1080, 4), dtype=np.uint8)
        arr[..., 3] = 255
        img = C.image_from_rgba(arr)
        ctx = _ctx()
        res = {}
        for name, it, lt in (("punch", {"kind": "punch", "id": "p", "amount": 0.2}, 0.05),
                             ("shake", dict(EFFECTS["shake"], amp=12), 0.1),
                             ("zoom", {"kind": "zoom", "from": 1.1, "to": 1.1}, 0.5)):
            best = 1e9
            for _ in range(5):
                t = time.perf_counter()
                M, mv = camera.combine([camera.MATRIX_EFFECTS[name](it, lt, 1.0, ctx)], ctx)
                camera.apply_camera(img, M, mv, ctx)
                best = min(best, time.perf_counter() - t)
            res[name] = best
        assert res["zoom"] < 0.015, res                     # holgura: la maquina se comparte
        assert res["punch"] < 0.04 and res["shake"] < 0.04, res
    finally:
        cv2.setNumThreads(prev_threads)
