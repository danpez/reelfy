"""Tests de motion/matte.py: descarga del modelo, post-proceso de mascaras, upsample guiado,
MatteProvider de punta a punta con video real y efectos de sujeto."""
import pickle
import time

import numpy as np
import pytest

from conftest import TEST_MEDIA
from motion import ctx as C, matte


# ---------------------------------------------------------------- modelo
def test_model_ready_and_local_copy(tmp_path, monkeypatch):
    import paths
    src = paths.SPIKE / "models" / matte.MODEL_NAME
    if not matte.model_ready(src):
        pytest.skip("sin copia local de MODNet")
    dst = tmp_path / "models" / matte.MODEL_NAME
    monkeypatch.setattr(matte, "model_path", lambda which="modnet": dst)
    assert not matte.model_ready(dst)
    p = matte.ensure_model()
    assert p == dst and matte.model_ready(dst)
    assert dst.with_name(dst.name + ".ok").exists()               # verificacion recordada


class _Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, n):
        for i in range(0, len(self.data), n):
            yield self.data[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_spec(monkeypatch, tmp_path, payload):
    import hashlib
    import paths
    spec = dict(matte.MODELS["modnet"], size=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                name="fake.onnx", sub="")
    monkeypatch.setitem(matte.MODELS, "modnet", spec)
    dst = tmp_path / "m" / "fake.onnx"
    monkeypatch.setattr(matte, "model_path", lambda which="modnet": dst)
    monkeypatch.setattr(paths, "SPIKE", tmp_path / "nada")          # sin copia local
    monkeypatch.setattr(matte.time, "sleep", lambda s: None)
    return dst


def test_ensure_model_download_retries_and_verifies_hash(tmp_path, monkeypatch):
    import requests
    payload = bytes(range(256)) * 400
    dst = _fake_spec(monkeypatch, tmp_path, payload)
    corrupt = bytearray(payload)
    corrupt[5000] ^= 0xFF
    calls = {"n": 0}

    def fake_get(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("red caida")
        if calls["n"] == 2:
            return _Resp(payload[:1000])                             # descarga truncada
        if calls["n"] == 3:
            return _Resp(payload[1000:], status=206)                 # reanuda con Range...
        return _Resp(payload)

    # la reanudacion junta un archivo del tamano exacto; si el hash no cuadra se vuelve a bajar
    monkeypatch.setattr(requests, "get", fake_get)
    pcts = []
    p = matte.ensure_model(on_pct=pcts.append, retries=5)
    assert p == dst and dst.read_bytes() == payload and pcts[-1] == 100.0
    assert not dst.with_name(dst.name + ".part").exists()

    # archivo del tamano exacto pero corrupto: no pasa la verificacion y se reemplaza
    dst.write_bytes(bytes(corrupt))
    dst.with_name(dst.name + ".ok").unlink()
    assert not matte.model_ready(dst)
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(payload))
    assert matte.ensure_model(retries=2) == dst and dst.read_bytes() == payload

    # un .part completo y valido se renombra sin bajar nada
    dst.unlink()
    dst.with_name(dst.name + ".part").write_bytes(payload)
    monkeypatch.setattr(requests, "get", lambda url, **kw: (_ for _ in ()).throw(AssertionError("no debe bajar")))
    assert matte.ensure_model(retries=1) == dst and dst.read_bytes() == payload

    dst.unlink()
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(b"", 404))
    with pytest.raises(RuntimeError):
        matte.ensure_model(retries=2)


def test_models_pinned_and_person_model_path():
    for w, spec in matte.MODELS.items():
        assert "/resolve/main/" not in spec["url"]                     # revision fija
        assert len(spec["sha256"]) == 64 and spec["license"] == "Apache-2.0"
    assert matte.model_path("person").parent.name == "matte"           # fuera del control de versiones


# ---------------------------------------------------------------- post-proceso
def test_body_gate_removes_far_blobs():
    m = np.zeros((200, 100), np.float32)
    m[40:200, 35:65] = 1.0          # cuerpo bajo la cara
    m[5:30, 0:12] = 0.9             # falso positivo lejos (arriba a la izquierda)
    face = [(0.5, 0.15, 0.1, 0.06, 0.9)]
    g = matte.body_gate(m, face)
    assert g[100, 50] > 0.99 and g[15, 5] < 0.01
    assert np.all(matte.body_gate(m, []) == 0)                       # sin cara: vacia


def test_temporal_smooth_no_lag():
    rng = np.random.default_rng(0)
    base = np.zeros((50, 50), np.float32)
    base[:, :25] = 1.0
    keys = [np.clip(base + rng.normal(0, 0.04, base.shape).astype(np.float32), 0, 1) for _ in range(6)]
    moved = np.zeros_like(base)
    moved[:, :35] = 1.0
    keys.append(moved)                                              # el borde salta 10 px
    sm = matte.temporal_smooth(keys)
    assert np.std(sm[3][:, :20]) < np.std(keys[3][:, :20]) * 0.9     # menos parpadeo
    assert sm[-1][:, 26:34].mean() > 0.9                             # sin arrastre del borde
    assert sm[-2][:, 26:34].mean() < 0.15                            # ni fantasma hacia atras


def test_guided_upsample_snaps_to_guide_edge():
    H, W = 256, 256
    guide = np.zeros((H, W, 3), np.uint8)
    guide[:, 130:] = 255                                            # borde real en x=130
    low = np.zeros((64, 64), np.float32)
    low[:, 34:] = 1.0                                               # mascara gruesa (x~136)
    low = np.clip(low + 0.0, 0, 1)
    import cv2
    low = cv2.GaussianBlur(low, (0, 0), 1.5)
    q = matte.refine(matte.guided_upsample(low, guide, r=3, eps=1e-3))
    assert q.shape == (H, W)
    row = q[128]
    edge = int(np.argmax(row > 0.5))
    assert abs(edge - 130) <= 2                                     # el borde sigue a la guia
    assert row[:127].max() < 0.05                                   # sin halo hacia afuera
    naive = cv2.resize(low, (W, H), interpolation=cv2.INTER_LINEAR)[128]
    assert abs(int(np.argmax(naive > 0.5)) - 130) >= 4              # (sin guia quedaria corrido)


def test_merge_ranges():
    assert matte._merge_ranges([(10, 20), (15, 30), (40, 41), (5, 5)]) == [(10, 30), (40, 41)]


# ---------------------------------------------------------------- efectos (sinteticos)
def _scene(W=270, H=480):
    a = np.zeros((H, W, 4), np.uint8)
    a[..., 3] = 255
    yy, xx = np.mgrid[0:H, 0:W]
    a[..., 0] = (xx * 255 // W).astype(np.uint8)
    a[..., 1] = ((yy // 4 + xx // 4) % 2 * 200).astype(np.uint8)     # fondo con detalle fino
    a[..., 2] = 90
    m = (((xx - W / 2) / (W * 0.22)) ** 2 + ((yy - H * 0.55) / (H * 0.3)) ** 2 < 1).astype(np.float32)
    a[m > 0.5, :3] = (210, 60, 50)                                   # sujeto rojo
    return a, m


def _ctx_with(m, W=270, H=480):
    c = C.FrameCtx(W=W, H=H, fps=30, n=5, face=(0.5, 0.35, 0.1, 0.06))
    c._matte_fn = lambda: m
    return c


def test_renderers_registry_and_no_matte_passthrough():
    assert set(matte.RENDERERS) == {"color_pop", "bg_blur", "bg_replace", "subject_outline"}
    a, _ = _scene()
    img = C.image_from_rgba(a)
    c = C.FrameCtx(W=270, H=480)
    for fn in matte.RENDERERS.values():
        assert fn(img, {}, 0.5, 1.0, c) is img                       # sin mascara: intacto


def test_color_pop_keeps_subject_color_and_grays_bg():
    a, m = _scene()
    o = C.image_to_rgba(matte.color_pop(C.image_from_rgba(a), {"bg_desat": 1.0, "bg_dark": 0.25}, 0.6, 1.2,
                                        _ctx_with(m)))
    s = o[int(480 * 0.55), 135, :3].astype(int)
    assert s[0] > 150 and s[0] - s[1] > 100                          # sujeto sigue rojo
    bg = o[20, 20, :3].astype(int)
    assert max(bg) - min(bg) <= 2                                    # fondo gris
    assert o[20, 250, 0] < a[20, 250, 0]                             # y mas oscuro


def test_bg_blur_blurs_only_background():
    a, m = _scene()
    o = C.image_to_rgba(matte.bg_blur(C.image_from_rgba(a), {"radius": 24}, 0.8, 1.6, _ctx_with(m)))
    assert np.std(o[:60, :, 1].astype(float)) < 0.5 * np.std(a[:60, :, 1].astype(float))
    y, x = int(480 * 0.55), 135
    assert np.abs(o[y - 5:y + 5, x - 5:x + 5, :3].astype(int) - a[y - 5:y + 5, x - 5:x + 5, :3]).max() <= 2
    # sin halo: el fondo junto al sujeto no se tine del rojo del sujeto
    ring_y = int(480 * 0.55)
    xs = int(135 - 270 * 0.22) - 6
    assert o[ring_y, xs, 0] < 150


def test_bg_replace_and_outline():
    a, m = _scene()
    c = _ctx_with(m)
    o = C.image_to_rgba(matte.bg_replace(C.image_from_rgba(a), {"background": "gradient",
                                                                 "bg_colors": ["#102030", "#304050"]},
                                         0.8, 1.6, c))
    assert np.abs(o[10:40, 10:40, :3].astype(int) - a[10:40, 10:40, :3]).mean() > 20   # fondo nuevo
    y, x = int(480 * 0.55), 135
    assert np.abs(o[y, x, :3].astype(int) - a[y, x, :3]).max() <= 16  # sujeto casi intacto (tinte leve)
    ol = C.image_to_rgba(matte.subject_outline(C.image_from_rgba(a), {"color": "#FFFFFF", "width": 40},
                                               0.8, 1.6, c))
    xe = int(135 + 270 * 0.22) + 4                                   # afuera del sujeto (10 px de borde)
    assert ol[y, xe, :3].min() > 200                                 # contorno blanco
    assert tuple(ol[y, x, :3]) == tuple(a[y, x, :3])
    # al entrar (lt=0) el contorno aun no existe; crece con resorte
    o0 = matte.subject_outline(C.image_from_rgba(a), {"width": 10}, 0.0, 1.6, c)
    assert np.array_equal(C.image_to_rgba(o0), a)


# ---------------------------------------------------------------- punta a punta (video real)
@pytest.mark.skipif(not (TEST_MEDIA / "crudo_completo.mp4").exists() or not matte.model_ready(),
                    reason="sin video de prueba o sin modelo")
def test_provider_real_video(tmp_path):
    from _real_media import small_base_video
    v = small_base_video(270, 480, 10)
    # calle (con persona) y fachada (sin persona: MODNet da falsos positivos ahi)
    street, facade = (195, 203), (258, 264)
    mp = matte.MatteProvider(v, 270, 480, 10, [street, facade], tmp_path, "draft", workers=2)
    pcts = []
    assert mp.prepare(pcts.append) and mp.ok
    assert pcts[-1] == 100.0
    ms = [mp.get(n) for n in range(*street)]
    assert all(m.shape == (480, 270) and m.dtype == np.float32 for m in ms)
    assert all(0.08 < m.mean() < 0.5 for m in ms)
    assert all(m.max() == 0.0 for m in (mp.get(n) for n in range(*facade)))
    # la persona esta al centro (y no en las esquinas)
    m = ms[3]
    assert m[200:300, 110:160].mean() > 0.9 and m[:40, :40].max() < 0.05
    assert mp.get(10_000).max() == 0.0                                # fuera de rango: vacia
    # cache: la segunda vez no recalcula
    t = time.time()
    mp2 = matte.MatteProvider(v, 270, 480, 10, [street, facade], tmp_path, "draft")
    assert mp2.missing_ranges() == [] and mp2.prepare() and time.time() - t < 1.0
    assert np.array_equal(mp2.get(street[0]), ms[0])
    mp3 = pickle.loads(pickle.dumps(mp2))
    assert np.array_equal(mp3.get(street[0] + 1), ms[1])


def test_provider_ranges_past_end_of_video(tmp_path):
    """Rangos que pasan del final del video: no fallan y no se recalculan cada vez."""
    import subprocess
    import paths
    if not matte.model_ready():
        pytest.skip("sin modelo")
    v = tmp_path / "t.mp4"
    subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=90x160:r=10:d=1",
                    "-pix_fmt", "yuv420p", str(v)], check=True)
    mp = matte.MatteProvider(v, 90, 160, 10, [(5, 15)], tmp_path, "draft", workers=1)
    assert mp.prepare()
    assert mp.missing_ranges() == []
    assert all(mp.get(n).max() == 0 for n in range(5, 15))      # sin persona: vacias


# ---------------------------------------------------------------- compuerta de persona
def _desk_scene(h=200, w=120):
    """Mascara MODNet sintetica: cuerpo + 'escritorio' pegado (mismo componente)."""
    m = np.zeros((h, w), np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    body = ((xx - 60) / 16.0) ** 2 + ((yy - 110) / 60.0) ** 2 < 1
    head = ((xx - 60) / 9.0) ** 2 + ((yy - 40) / 11.0) ** 2 < 1
    m[body | head] = 1.0
    m[120:170, 0:120] = 1.0                                          # escritorio que toca el cuerpo
    p = np.zeros_like(m)
    p[body | head] = 1.0                                             # la persona segun el segmentador
    return m, p, (body | head), [(0.5, 0.2, 0.15, 0.11, 0.9)]


def test_person_gate_removes_attached_furniture():
    m, p, person, faces = _desk_scene()
    g, rem = matte.gate_matte(m, faces, p, dil_px=4)
    assert g[110, 60] > 0.99 and g[40, 60] > 0.99                    # la persona queda
    assert g[150, 5] < 0.02 and g[150, 114] < 0.02                   # el escritorio se va
    assert 0.3 < rem < 0.8
    g0, rem0 = matte.gate_matte(m * person, faces, p, dil_px=4)
    assert rem0 < 0.02                                               # sin muebles no quita nada


def test_body_prior_fallback_trims_far_furniture():
    m, _, person, faces = _desk_scene()
    g, rem = matte.gate_matte(m, faces, None)
    assert g[110, 60] > 0.95                                         # torso
    assert g[150, 2] < 0.1 and g[150, 117] < 0.1                     # extremos del escritorio fuera
    assert rem > 0.2


def test_area_hysteresis_fades_instead_of_vanishing():
    k = np.zeros((20, 20), np.float32)
    k[5:15, 5:15] = 1.0
    keys = [k.copy() for _ in range(4)] + [np.zeros_like(k) for _ in range(6)]
    out = matte.area_hysteresis(keys, fps=30, step=2)
    areas = [float(o.mean()) for o in out]
    assert areas[4] > 0.5 * areas[3]                                 # no cae de golpe
    assert all(a >= b for a, b in zip(areas[3:], areas[4:]))         # se desvanece
    assert areas[-1] == 0.0                                          # y luego si desaparece


def test_range_gating_keeps_mask_when_face_is_lost(tmp_path, monkeypatch):
    """Rango con la cara ausente en las claves centrales (perfil, cabeza agachada): igual se
    infieren todas y la mascara NO se apaga a mitad del efecto."""
    import subprocess
    import paths
    v = tmp_path / "t.mp4"
    subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=90x160:r=10:d=3",
                    "-pix_fmt", "yuv420p", str(v)], check=True)
    monkeypatch.setattr(matte, "model_ready", lambda p=None, which="modnet": True)
    blob = np.zeros((160, 96), np.float32)
    blob[30:150, 25:70] = 1.0
    calls = {"faces": 0}

    def fake_faces(rgb):
        calls["faces"] += 1
        # solo hay cara en las primeras y ultimas claves
        return [(0.5, 0.25, 0.2, 0.1, 0.9)] if calls["faces"] <= 3 or calls["faces"] >= 14 else []

    monkeypatch.setattr(matte, "detect_faces", fake_faces)
    monkeypatch.setattr(matte, "infer", lambda x, threads=3: cv2_resize(blob, x.shape[1], x.shape[0]))
    monkeypatch.setattr(matte, "person_prob", lambda rgb, threads=1: np.ones((192, 192), np.float32))
    mp = matte.MatteProvider(v, 90, 160, 10, [(0, 30)], tmp_path, "final", workers=1)
    assert mp.prepare() and mp.ok
    areas = [float(mp.get(n).mean()) for n in range(30)]
    assert min(areas) > 0.1, areas                                    # nunca se apaga
    assert mp.quality_of(0, 30) is not None


def cv2_resize(a, w, h):
    import cv2
    return cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)


# ---------------------------------------------------------------- cache robusta
def test_cache_atomic_out_of_range_corrupt_and_prune(tmp_path):
    import os
    mp = matte.MatteProvider(tmp_path / "nada.mp4", 40, 60, 10, [(0, 5)], tmp_path, "draft")
    mp.dir.mkdir(parents=True, exist_ok=True)
    one = np.full((60, 40), 255, np.uint8)
    for n in range(0, 8):
        mp._write(n, one)
    assert not list(mp.dir.glob("*.tmp*"))                             # escritura atomica
    assert mp.get(3).mean() == pytest.approx(1.0)
    assert mp.get(6).max() == 0.0                                      # PNG viejo fuera de rango: ignorado
    mp._png(2).write_bytes(b"basura")                                  # PNG truncado
    mp._lru.clear()
    assert mp.get(2).max() == 0.0 and not mp._png(2).exists()          # se descarta y se recalculara
    assert mp.missing_ranges() == [(0, 5)]
    # prune: conserva los mas recientes y baja del limite
    for i in range(4):
        d = tmp_path / f"matte_{i:016d}"
        d.mkdir()
        (d / "000000.png").write_bytes(b"x" * 400_000)
        os.utime(d / "000000.png", (1000 + i, 1000 + i))
    (tmp_path / "matte_0000000000000003" / "a.png.tmp1.png").write_bytes(b"x")
    r = matte.prune_cache(tmp_path, max_mb=0.9, keep=1)
    left = sorted(d.name for d in tmp_path.glob("matte_*"))
    assert "matte_0000000000000000" not in left and mp.dir.name in left
    assert not list(tmp_path.glob("matte_*/*.tmp*")) and r["mb"] <= 0.9 + 0.5


# ---------------------------------------------------------------- reemplazo de fondo integrado
def test_bg_replace_reveal_wrap_and_floor_shadow():
    W, H = 270, 480
    a = np.zeros((H, W, 4), np.uint8)
    a[..., 3] = 255
    a[..., :3] = (200, 200, 200)
    m = np.zeros((H, W), np.float32)
    m[120:420, 110:160] = 1.0                                        # cuerpo completo, pies en y=420
    m[80:120, 120:150] = 1.0
    a[m > 0.5, :3] = (60, 60, 60)
    c = C.FrameCtx(W=W, H=H, fps=30, n=3, face=(0.5, 0.21, 0.1, 0.035))
    c._matte_fn = lambda: m
    it = {"background": "color", "bg_colors": ["#2040C0", "#2040C0"], "vignette": 0.0, "grain": 0.0}
    o = C.image_to_rgba(matte.bg_replace(C.image_from_rgba(a), it, 0.08, 2.0, c))
    assert np.abs(o[470, 5, :3].astype(int) - 200).max() <= 3         # lejos de la cara: aun no revela
    assert o[100, 90, 2] > o[100, 90, 0] + 40                           # junto a la cara: fondo nuevo
    full = C.image_to_rgba(matte.bg_replace(C.image_from_rgba(a), it, 1.0, 2.0, c))
    assert full[5, 5, 2] > full[5, 5, 0] + 40                           # fondo reemplazado
    edge, center = full[250, 111, :3].astype(int), full[250, 135, :3].astype(int)
    assert edge[2] - edge[0] > center[2] - center[0] + 6                # light wrap en el borde
    below, side = full[424, 135, :3].astype(int), full[424, 30, :3].astype(int)
    assert below.sum() < side.sum() - 30                                # sombra de piso bajo los pies


# ---------------------------------------------------------------- parametros basura y costo
BAD = [None, "abc", float("nan"), [1], -3, 1e9, "red"]


@pytest.mark.parametrize("name", ["color_pop", "bg_blur", "bg_replace", "subject_outline"])
def test_renderers_garbage_params(name):
    a, m = _scene(90, 160)
    c = _ctx_with(m, 90, 160)
    for k in ("bg_desat", "bg_dark", "radius", "background", "bg_colors", "color", "width", "glow",
              "intensity", "fade_in", "fade_out", "grain", "vignette"):
        for bad in BAD:
            out = matte.RENDERERS[name](C.image_from_rgba(a), {k: bad}, 0.5, 1.2, c)
            assert out.width() == 90


def test_effects_perf_single_thread():
    import time
    import cv2
    prev = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        W, H = 1080, 1920
        a = np.random.default_rng(0).integers(0, 255, (H, W, 4), dtype=np.uint8)
        a[..., 3] = 255
        m = np.zeros((H, W), np.float32)
        m[500:1800, 350:750] = 1.0
        m = cv2.GaussianBlur(m, (0, 0), 3)
        c = C.FrameCtx(W=W, H=H, fps=30, n=4, face=(0.5, 0.3, 0.1, 0.05))
        c._matte_fn = lambda: m
        img = C.image_from_rgba(a)
        res = {}
        for name, it in (("color_pop", {}), ("bg_blur", {}), ("subject_outline", {"width": 10}),
                         ("bg_replace", {"background": "gradient"})):
            matte.RENDERERS[name](img, it, 1.0, 2.0, c)
            best = 1e9
            for _ in range(4):
                t = time.perf_counter()
                matte.RENDERERS[name](img, it, 1.0, 2.0, c)
                best = min(best, time.perf_counter() - t)
            res[name] = best
        assert res["color_pop"] < 0.012 and res["subject_outline"] < 0.016, res
        assert res["bg_blur"] < 0.02 and res["bg_replace"] < 0.045, res
    finally:
        cv2.setNumThreads(prev)


@pytest.mark.skipif(not (TEST_MEDIA / "crudo_completo.mp4").exists() or not matte.model_ready()
                    or not matte.model_ready(which="person"), reason="sin video de prueba o sin modelos")
def test_provider_real_desk_excluded(tmp_path):
    """Toma real del escritorio: MODNet solo mete el escritorio al sujeto; con la compuerta de
    persona el frente del escritorio queda fuera y la calidad del rango lo refleja."""
    from _real_media import small_base_video
    v = small_base_video(540, 960, 10)     # MODNet necesita ~400 px de sujeto (a 270 px falla)
    desk, street = (147, 157), (195, 203)
    mp = matte.MatteProvider(v, 540, 960, 10, [desk, street], tmp_path, "draft", workers=2)
    assert mp.prepare() and mp.ok and mp.warning is None
    for n in range(*desk):
        m = mp.get(n)
        assert m[int(0.75 * 960):int(0.95 * 960), int(0.3 * 540):int(0.9 * 540)].mean() < 0.05
        assert m[int(0.45 * 960):int(0.58 * 960), int(0.25 * 540):int(0.5 * 540)].mean() > 0.8  # torso
    qd, qs = mp.quality_of(*desk), mp.quality_of(*street)
    assert qd is not None and qs is not None and qd < qs and qs > 0.95


def test_without_person_model_falls_back_to_body_prior(tmp_path, monkeypatch):
    """Sin la compuerta de persona (offline, descarga fallida) el provider sigue: usa el prior
    geometrico, deja un aviso y no reintenta la descarga en cada render."""
    import subprocess
    import paths
    v = tmp_path / "t.mp4"
    subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=90x160:r=10:d=1",
                    "-pix_fmt", "yuv420p", str(v)], check=True)
    monkeypatch.setattr(matte, "model_ready", lambda p=None, which="modnet": which == "modnet")
    tries = {"n": 0}

    def no_net(*a, **kw):
        tries["n"] += 1
        raise RuntimeError("sin red")

    monkeypatch.setattr(matte, "ensure_model", no_net)
    monkeypatch.setattr(matte, "_PERSON_FAILED", [])
    blob = np.zeros((160, 96), np.float32)
    blob[40:150, 30:66] = 1.0
    monkeypatch.setattr(matte, "detect_faces", lambda rgb: [(0.5, 0.3, 0.2, 0.1, 0.9)])
    monkeypatch.setattr(matte, "infer", lambda x, threads=3: cv2_resize(blob, x.shape[1], x.shape[0]))
    for k in range(2):
        mp = matte.MatteProvider(v, 90, 160, 10, [(0, 10)], tmp_path / f"c{k}", "final", workers=1)
        assert mp.prepare() and mp.ok and mp.warning
        assert all(mp.get(n).max() > 0.5 for n in range(10))
    assert tries["n"] == 1                                              # un solo intento por proceso
