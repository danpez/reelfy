"""Pruebas de motion/render.py: compositor (orden 4.3, camara, sujeto, transiciones, errores)
con renderers stub, reparto en chunks y E2E real sobre crudo_completo.mp4.

Correr: cd spike && .venv/bin/python -m pytest tests/test_render.py -q
"""
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

import paths
from motion import render as R
from motion import schema as S
from motion import video_io as V
from motion.timeline import Timeline
from conftest import SCRIPTS, TEST_MEDIA
from _render_plan import STUBS, stub_plan

TESTS = str(Path(__file__).resolve().parent)
OUT = paths.WORK / "tests_render"
CRUDO = TEST_MEDIA / "crudo_completo.mp4"
need_media = pytest.mark.skipif(not CRUDO.exists(), reason="sin media de prueba")
W, H, FPS = 216, 384, 30


def _job(fx: dict, duration=4.0, cuts=(), shots=(), mods=None, **opts):
    plan = S.normalize({"video": "x.mp4", "duration": duration, "cuts": list(cuts),
                        "shots": list(shots),
                        "phrases": [[{"text": "hola", "start": 0.2, "end": 0.6},
                                     {"text": "mundo", "start": 0.6, "end": 1.2}],
                                    [{"text": "adios", "start": 2.0, "end": 2.6}]],
                        "fx": fx, "version": 2})
    tl = Timeline.from_plan(plan, fps=FPS)
    # sin grano final por defecto: las pruebas comparan pixeles exactos (hay una propia)
    o = R._resolve_opts(dict({"W": W, "H": H, "fps": FPS, "modules": STUBS, "dither": 0.0}, **opts))
    mods = mods if mods is not None else R.load_modules(STUBS)
    job, skipped = R.build_job(plan, tl, o, mods)
    return job, skipped, mods, tl


def _solid(rgb):
    a = np.zeros((H, W, 4), np.uint8)
    a[..., :3] = rgb
    a[..., 3] = 255
    return a


def _gradient():
    a = np.zeros((H, W, 4), np.uint8)
    a[..., 0] = np.linspace(0, 255, W)[None, :]
    a[..., 1] = np.linspace(0, 255, H)[:, None]
    a[..., 2] = 90
    a[..., 3] = 255
    return a


NOCAP = {"captions": {"enabled": False}, "grade": {"enabled": False}}


# ------------------------------------------------------------------ compositor
def test_sin_efectos_el_cuadro_es_el_base():
    job, skipped, mods, _ = _job(dict(NOCAP))
    comp = R.Compositor(job, mods)
    base = _gradient()
    out = comp.frame(10, base)
    assert out.shape == (H, W, 4) and np.array_equal(out, base)


def test_grade_se_aplica_y_modulo_ausente_se_omite():
    job, _, mods, _ = _job({"captions": {"enabled": False}})
    comp = R.Compositor(job, mods, grader=mods["grade"].Grader(None, [], W, H, FPS))
    out = comp.frame(0, _solid((120, 120, 120)))
    assert out[0, 0, 0] > out[0, 0, 2]                    # tinte calido del stub
    job2, skipped, mods2, _ = _job({"text": [{"preset": "title_pop", "text": "x", "start": 0, "end": 1}]},
                                   mods=R.load_modules({"text": None, "grade": None}, quiet=True))
    assert "text:title_pop" in skipped and job2["text"] == [] and not job2["grade_on"]


def test_camara_zoom_y_guardia_de_bordes():
    job, _, mods, _ = _job(dict(NOCAP, effects=[{"kind": "zoom", "start": 0, "end": 2, "from": 1.0,
                                                 "to": 1.3}]))
    comp = R.Compositor(job, mods)
    base = _gradient()
    out = comp.frame(59, base).copy()                     # casi al final: zoom ~1.3
    assert not np.array_equal(out, base)
    # el zoom amplia: el borde izquierdo ya no es el del base
    assert out[:, 0, 0].mean() > base[:, 0, 0].mean() + 10
    # una matriz que aleja (escala < 1) se corrige: nunca bordes negros
    import skia
    M = skia.Matrix()
    M.setScale(0.8, 0.8, W / 2, H / 2)
    G = comp._cover_guard(M)
    assert comp._covers(G) and G.getScaleX() >= 1.0 - 1e-3


def test_motion_blur_por_traslacion():
    job, _, mods, _ = _job(dict(NOCAP, effects=[{"kind": "shake", "start": 0, "end": 2}]))
    comp = R.Compositor(job, mods)
    base = np.zeros((H, W, 4), np.uint8)
    base[..., 3] = 255
    base[:, ::8, :3] = 255                                # rayas verticales finas
    out = comp.frame(0, base)
    col = out[H // 2, :, 0].astype(float)
    # el blur horizontal suaviza las rayas: aparecen grises intermedios
    assert ((col > 20) & (col < 235)).mean() > 0.3


def test_texto_detras_del_sujeto():
    fx = dict(NOCAP, text=[{"preset": "title_pop", "text": "MMMMMM", "start": 0, "end": 3,
                            "behind": True, "y": 0.45, "size": 0.2, "color": "#FF0000"}])
    job, _, mods, _ = _job(fx)
    assert job["matte_ranges"]
    matte = mods["matte"].MatteProvider(None, W, H, FPS, job["matte_ranges"], None, "final")
    comp = R.Compositor(job, mods, matte=matte)
    base = _solid((0, 0, 255))
    out = comp.frame(15, base)
    m = matte.m
    inside = m > 0.99
    # dentro del sujeto se ve el base (azul); el titulo solo asoma fuera
    assert np.abs(out[inside][:, :3].astype(int) - [0, 0, 255]).max() < 3
    assert (out[~inside][:, 3] == 255).all()
    # sin matte el texto se dibuja encima (fallback)
    comp2 = R.Compositor(job, mods, matte=None)
    out2 = comp2.frame(15, base)
    assert (np.abs(out2[inside][:, :3].astype(int) - [0, 0, 255]).max(axis=1) > 30).any()


def test_orden_escena_graficos_y_subtitulos_ocultos():
    fx = {"captions": {"enabled": True, "preset": "clean_bold", "hide_during_titles": True},
          "grade": {"enabled": False},
          "cutaways": [{"kind": "title_card", "start": 2.0, "end": 3.0, "text": {"text": "X"}}],
          "graphics": [{"kind": "doodle", "start": 2.0, "end": 3.0, "x": 0.5, "y": 0.2}],
          "text": [{"preset": "title_pop", "text": "HOLA", "start": 0.1, "end": 1.5}]}
    job, _, mods, _ = _job(fx)
    assert job["captions"]["_hide"][0][0] == pytest.approx(0.1)
    comp = R.Compositor(job, mods)
    base = _solid((0, 200, 0))
    out = comp.frame(75, base).copy()                    # t = 2.5: escena encima del video
    assert not (np.abs(out[..., :3].astype(int) - [0, 200, 0]).sum(-1) < 5).any()
    assert out[5, 5, :3].tolist() == [0x1B, 0x1B, 0x22]
    assert (out[..., :3] == 255).all(-1).any()           # el circulo (blanco) va sobre la escena
    # t=0.5: titulo activo -> sin subtitulos; t=2.2 sin titulo (escena) -> tampoco (tiene texto)
    with_cap = R.Compositor(_job(dict(fx, text=[]))[0], mods).frame(15, base).copy()
    no_cap = comp.frame(15, base).copy()
    y = int(0.72 * H)
    band = slice(y - 20, y + 5)
    assert (with_cap[band, :, :3] == 255).all(-1).sum() > 20
    assert (no_cap[band, :, :3] == 255).all(-1).sum() == 0


def test_transicion_slide_usa_prev_congelado():
    fx = dict(NOCAP, transitions=[{"kind": "slide", "at": 2.0, "dur": 0.4}])
    job, _, mods, _ = _job(fx)
    tr = job["transitions"][0]
    assert (tr["_J"], tr["_F0"], tr["_F1"], tr["_P"]) == (60, 60, 72, 59)
    comp = R.Compositor(job, mods)
    for n in range(55, 60):
        comp.frame(n, _solid((255, 0, 0)))               # A = rojo
    mid = comp.frame(66, _solid((0, 0, 255))).copy()     # B = azul, a mitad de la ventana
    assert mid[H // 2, 5, :3].tolist() == [255, 0, 0]    # izquierda: A congelado saliendo
    assert mid[H // 2, W - 5, :3].tolist() == [0, 0, 255]
    after = comp.frame(72, _solid((0, 0, 255)))          # fuera de la ventana: solo B
    assert after[H // 2, 5, :3].tolist() == [0, 0, 255]
    assert 59 not in comp.prev_store                     # se libera al terminar


def test_transicion_flash_centrada_y_en_corte():
    fx = dict(NOCAP, transitions=[{"kind": "flash", "at": 1.5, "dur": 0.3}])
    job, _, mods, tl = _job(fx, cuts=[{"start": 1.0, "end": 2.0, "enabled": True}])
    tr = job["transitions"][0]
    assert tr["_J"] == 30                                # la union esta en t_final = 1.0
    assert tr["_F0"] == 30 - 4 and tr["_F1"] == 30 - 4 + 9
    comp = R.Compositor(job, mods)
    base = _solid((0, 0, 0))
    vals = [comp.frame(n, base)[0, 0, 0] for n in range(tr["_F0"], tr["_F1"])]
    assert max(vals) > 200 and vals[0] < max(vals) and vals[-1] < max(vals)


def test_error_de_renderer_no_tumba_el_cuadro():
    job, _, mods, _ = _job(dict(NOCAP, graphics=[{"kind": "doodle", "start": 0, "end": 2}]))
    comp = R.Compositor(job, mods)

    def boom(*a):
        raise RuntimeError("fallo a proposito")
    comp.reg = dict(comp.reg, graphics={"doodle": boom})
    out = comp.frame(3, _solid((10, 20, 30)))
    assert out[0, 0, :3].tolist() == [10, 20, 30] and comp.errors == {"graphics:doodle": 1}
    job["strict"] = True
    comp2 = R.Compositor(job, mods)
    comp2.reg = dict(comp2.reg, graphics={"doodle": boom})
    with pytest.raises(RuntimeError):
        comp2.frame(3, _solid((10, 20, 30)))


def test_items_mapeados_a_tiempo_final_y_palabras():
    fx = dict(NOCAP, text=[{"preset": "title_pop", "text": "a", "start": 0.1, "end": 1.5},
                           {"preset": "title_pop", "text": "cortado", "start": 1.1, "end": 1.9}])
    job, _, _, _ = _job(fx, cuts=[{"start": 1.0, "end": 2.0, "enabled": True}])
    assert len(job["text"]) == 1                         # el segundo cae entero en el corte
    t = job["text"][0]
    assert t["_fs"] == pytest.approx(0.1) and t["_fe"] == pytest.approx(1.0)
    assert [w["text"] for w in t["_words"]] == ["hola", "mundo"]
    assert t["_words"][0]["start"] == pytest.approx(0.1)
    assert [w["text"] for w in job["words"]] == ["hola", "mundo", "adios"]
    assert job["words"][2]["start"] == pytest.approx(1.0)


def test_plan_chunks_respeta_transiciones():
    trs = [{"_F0": 95, "_F1": 110, "_P": 99}, {"_F0": 300, "_F1": 312, "_P": 299}]
    ch = R.plan_chunks(600, 6, trs)
    assert ch[0][0] == 0 and ch[-1][1] == 600
    assert all(a < b for a, b in ch)
    assert all(ch[i][1] == ch[i + 1][0] for i in range(len(ch) - 1))
    for a, _ in ch[1:]:
        assert not (95 < a < 110) and not (299 < a < 312)
    assert R.plan_chunks(10, 8, []) == [(0, 10)]


def test_face_rows_huecos():
    f = np.tile([0.5, 0.3, 0.2, 0.1], (100, 1)).astype(float)
    f[10:14] = np.nan                                    # corto -> interpolado
    f[40:80] = np.nan                                    # largo -> sin cara
    g = R._face_rows(f, 100)
    assert not np.isnan(g[10:14]).any() and np.isnan(g[40:80]).all()
    g2 = R._face_rows(f[::2], 100)                       # muestreado -> por cuadro
    assert g2.shape == (100, 4)


# ------------------------------------------------------------------ E2E real
@pytest.fixture(scope="module")
def e2e():
    OUT.mkdir(parents=True, exist_ok=True)
    plan = stub_plan(CRUDO)
    out = OUT / "e2e_stub.mp4"
    pcts, steps = [], []
    t0 = time.time()
    st = R.render_pro(plan, out, {"modules": STUBS, "sys_path": [TESTS], "workers": 4,
                                  "strict": True, "on_pct": lambda s, p: pcts.append((s, p)),
                                  "on_step": steps.append})
    st["_wall"] = time.time() - t0
    (OUT / "e2e_stats.json").write_text(json.dumps(st, indent=1, default=str))
    return plan, out, st, pcts, steps


@need_media
def test_e2e_render_pro_crudo(e2e):
    plan, out, st, pcts, steps = e2e
    tl = Timeline.from_plan(plan, fps=30)
    si = V.stream_info(out)
    v, a = si["video"], si["audio"]
    assert (v["width"], v["height"]) == (1080, 1920)
    assert v["r_frame_rate"] == "30/1"
    assert int(v["nb_frames"]) == tl.n_frames() == st["n_frames"]
    assert float(v["duration"]) == pytest.approx(tl.final_duration, abs=1 / 30)
    assert float(a["duration"]) == pytest.approx(tl.final_duration, abs=1 / 30)
    assert abs(float(a["start_time"]) - float(v["start_time"])) < 0.025
    # color completo en el VUI (sin 'unknown': ColorSync/iPhone aplican la gamma correcta)
    assert v["color_space"] == v["color_transfer"] == v["color_primaries"] == "bt709"
    assert v["color_range"] == "tv" and v["profile"] == "High" and int(v["level"]) == 41
    # entrega ~13.7 Mbps a 1080p30 (TikTok/Reels re-codifican: mas no se ve)
    mbps = out.stat().st_size * 8 / float(v["duration"]) / 1e6
    assert 8 < mbps < 20, mbps
    # progreso monotono hasta 100 y sin errores de renderers
    vals = [p for _, p in pcts]
    assert vals == sorted(vals) and vals[-1] == 100
    assert st["errors"] == {} and set(st["skipped"]) <= {"audio:modulo"}, st["skipped"]
    assert st["workers"] == 4 and st["chunks"] >= 4 and st["threads_per_worker"] >= 1
    print(f"\nE2E: total {st['t_total']}s, base {st['t_base']}s (cache={st['cached_base']}), "
          f"compositor {st['t_compose']}s, {st['ms_per_frame_proc']} ms/cuadro/proceso, "
          f"{st['ms_per_frame_compose']} ms/cuadro de composicion")


@need_media
def test_e2e_audio_en_sincronia_con_la_voz(e2e):
    """La voz del mp4 final coincide con la voz cortada (lag 0 +-1 cuadro)."""
    plan, out, st, _, _ = e2e
    import soundfile as sf
    ref, sr = sf.read(st["voice"], always_2d=True)
    got = V.read_audio(out, 48000, 1)[:, 0]
    ref = ref.mean(1)
    n = min(len(ref), len(got)) // 480 * 480
    env = lambda x: np.abs(x[:n]).reshape(-1, 480).mean(1)   # noqa: E731  envolvente 10 ms
    er, eg = env(ref), env(got)
    lags = range(-10, 11)
    cc = [np.corrcoef(er[max(0, -l):len(er) - max(0, l)], eg[max(0, l):len(eg) - max(0, -l)])[0, 1]
          for l in lags]
    best = list(lags)[int(np.argmax(cc))]
    assert abs(best) <= 3 and max(cc) > 0.95, (best, max(cc))


@need_media
def test_e2e_render_frame_igual_al_video(e2e):
    """render_frame (vista previa exacta) coincide con el cuadro del mp4 final."""
    plan, out, st, _, _ = e2e
    opts = {"modules": STUBS, "sys_path": [TESTS]}
    for n in (40, 250, 460, 700):                         # titulo, texto detras, zoom, bw
        fr = R.render_frame(plan, n / 30, opts)
        with V.FrameReader(out, 1080, 1920, n, 1, 30) as r:
            ref = r.read()
        diff = np.abs(fr[..., :3].astype(int) - ref[..., :3].astype(int)).mean()
        assert diff < 2.5, (n, diff)
        R.save_png(fr, OUT / f"frame_{n}.png")


@need_media
def test_render_frame_sin_base_cacheado(tmp_path):
    """Sin base en cache decodifica la fuente en el tiempo mapeado (mismo encuadre)."""
    plan = stub_plan(CRUDO)
    opts = {"modules": STUBS, "sys_path": [TESTS], "cache_dir": str(tmp_path)}
    fr = R.render_frame(plan, 5.0, opts)
    ref = R.render_frame(plan, 5.0, {"modules": STUBS, "sys_path": [TESTS]})
    assert fr.shape == (1920, 1080, 4)
    assert np.abs(fr[..., :3].astype(int) - ref[..., :3].astype(int)).mean() < 4.0


@need_media
def test_render_cancelable_sin_salida_parcial(tmp_path):
    plan = stub_plan(CRUDO)
    out = tmp_path / "cancel.mp4"
    state = {"n": 0}

    def cancel():
        state["n"] += 1
        return state["n"] > 12
    with pytest.raises(V.Canceled):
        R.render_pro(plan, out, {"modules": STUBS, "sys_path": [TESTS], "workers": 3,
                                 "cancel": cancel})
    assert not out.exists()
    assert not list(tmp_path.glob("*.part*"))              # ni temporales en la carpeta
    time.sleep(0.5)
    left = subprocess.run(["pgrep", "-f", "concurrent.futures.process"], capture_output=True,
                          text=True).stdout.split()
    assert not [p for p in left if p and int(p) != 0 and _child_of_me(int(p))]


def _child_of_me(pid):
    import os
    r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip() == str(os.getpid())


@need_media
def test_render_draft_540(tmp_path):
    plan = stub_plan(CRUDO)
    plan["cuts"] = plan["cuts"] + [{"start": 12.0, "end": plan["duration"], "enabled": True}]
    out = tmp_path / "draft.mp4"
    st = R.render_draft(plan, out, {"modules": STUBS, "sys_path": [TESTS]})
    si = V.stream_info(out)["video"]
    assert (si["width"], si["height"]) == (540, 960) and st["quality"] == "draft"
    assert int(si["nb_frames"]) == st["n_frames"]


def test_opciones_progreso_y_modulos_desactivados():
    job, _, mods, _ = _job(dict(NOCAP), progress=True)
    prog = [g for g in job["graphics"] if g["kind"] == "progress"]
    assert len(prog) == 1 and prog[0]["_fs"] == 0 and prog[0]["_fe"] == pytest.approx(job["final_duration"])
    m = R.load_modules({"text": None, "camera": "motion_stubs.camera"}, quiet=True)
    assert m["text"] is None and m["camera"].__name__ == "motion_stubs.camera"
    assert R.load_modules({"text": "no_existe_modulo_x"}, names=("text",), quiet=True)["text"] is None


def test_safe_main_oculta_main_y_limita_hilos():
    import os as _os
    import sys as _s
    main = _s.modules["__main__"]
    had = hasattr(main, "__file__")
    old, spec = getattr(main, "__file__", None), getattr(main, "__spec__", None)
    main.__file__ = __file__                              # archivo REAL: tambien se oculta
    prev_env = _os.environ.get("OMP_NUM_THREADS")
    try:
        with R._SafeMain({"OMP_NUM_THREADS": 3}):
            assert not hasattr(main, "__file__") and main.__spec__ is None
            assert _os.environ["OMP_NUM_THREADS"] == "3"
        assert main.__file__ == __file__ and main.__spec__ is spec
        assert _os.environ.get("OMP_NUM_THREADS") == prev_env
    finally:
        if had:
            main.__file__ = old
        else:
            del main.__file__


# ------------------------------------------------------------------ arreglos del nucleo
_MAIN_SCRIPT = '''
import sys
sys.path.insert(0, {scripts!r})
sys.path.insert(0, {tests!r})
import os
with open({marker!r}, "a") as f:          # efecto lateral a nivel de modulo (como app/server.py)
    f.write(str(os.getpid()) + "\\n")
from motion import render as R
from _render_plan import STUBS

if __name__ == "__main__":
    plan = {{"video": {video!r}, "duration": 3.0, "cuts": [], "phrases": [], "version": 2,
             "fx": {{"captions": {{"enabled": False}}, "grade": {{"enabled": False}},
                    "music": {{"enabled": False, "source": "none"}}}}}}
    st = R.render_pro(plan, {out!r}, {{"W": 216, "H": 384, "workers": 3, "modules": STUBS,
                                     "sys_path": [{tests!r}], "enhance_audio": False,
                                     "cache_dir": {cache!r}}})
    print("CHUNKS", st["chunks"])
'''


def _tiny_source(path: Path, dur=3.0):
    if not path.exists():
        subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"testsrc2=s=360x640:r=30:d={dur}", "-f", "lavfi", "-i",
                        f"sine=f=330:r=48000:d={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def test_workers_no_reejecutan_el_script_principal(tmp_path):
    """spawn reimporta __main__ en cada worker: app/server.py arrancaria ollama, licencia y
    FastAPI N veces. Con _SafeMain el efecto lateral de nivel de modulo corre UNA vez."""
    import sys as _s
    src = _tiny_source(tmp_path / "src.mp4")
    marker = tmp_path / "marker.txt"
    script = tmp_path / "main_con_efectos.py"
    script.write_text(_MAIN_SCRIPT.format(scripts=str(SCRIPTS), tests=TESTS,
                                          marker=str(marker), video=str(src),
                                          out=str(tmp_path / "o.mp4"), cache=str(tmp_path / "c")))
    r = subprocess.run([_s.executable, str(script)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "CHUNKS" in r.stdout and int(r.stdout.split("CHUNKS")[1].split()[0]) >= 3
    assert len(marker.read_text().split()) == 1, marker.read_text()
    # y tambien como `python -m` (spawn usaria __spec__.name para reimportar)
    marker.unlink()
    r = subprocess.run([_s.executable, "-m", "main_con_efectos"], capture_output=True, text=True,
                       timeout=300, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr[-2000:]
    assert len(marker.read_text().split()) == 1, marker.read_text()


def test_formatos_original_y_desconocido():
    o = R._resolve_opts({"fmt": "original"}, {"w": 2160, "h": 3840})
    assert (o["W"], o["H"]) == (1080, 1920)
    o = R._resolve_opts({"fmt": "original", "quality": "draft"}, {"w": 3840, "h": 2160})
    assert (o["W"], o["H"], o["base_W"], o["base_H"]) == (960, 540, 1920, 1080)
    o = R._resolve_opts({"fmt": "16:9", "quality": "draft"})
    assert (o["W"], o["H"], o["base_W"]) == (960, 540, 1920)
    with pytest.raises(ValueError, match="Formato desconocido"):
        R._resolve_opts({"fmt": "3:2"})
    with pytest.raises(ValueError):
        R._resolve_opts({"fmt": "original"})                 # sin fuente
    assert R.layout_box(1080, 1920) == (0, 0, 1080, 1920)
    assert R.layout_box(1920, 1080) == (656, 0, 608, 1080)
    assert R.layout_box(1080, 1080) == (236, 0, 608, 1080)


def test_caja_de_layout_fuera_de_9_16():
    """En 16:9 los overlays se dibujan en la caja 9:16 central con tamanos de la caja: el
    mismo titulo mide lo mismo (en px) que en un 9:16 de la misma altura."""
    fx = dict(NOCAP, text=[{"preset": "title_pop", "text": "HOLA", "start": 0, "end": 3,
                            "x": 0.5, "y": 0.3, "size": 0.12}])
    WW, HH = 384, 216
    job, _, mods, _ = _job(fx, W=WW, H=HH)
    assert job["layout"] == [131, 0, 122, 216]
    comp = R.Compositor(job, mods)
    base = np.zeros((HH, WW, 4), np.uint8)
    base[..., 3] = 255
    out = comp.frame(20, base).copy()
    bx, by, bw, bh = comp.box
    ink = out[..., :3].max(-1) > 40
    cols = np.where(ink.any(0))[0]
    assert cols.min() >= bx - 2 and cols.max() <= bx + bw + 2       # nada fuera de la caja
    # referencia: 9:16 de la misma altura (122x216) -> mismo ancho de tinta
    job2, _, mods2, _ = _job(fx, W=bw, H=bh)
    ref = R.Compositor(job2, mods2).frame(20, base[:, :bw].copy()).copy()
    cols2 = np.where((ref[..., :3].max(-1) > 40).any(0))[0]
    assert abs((cols.max() - cols.min()) - (cols2.max() - cols2.min())) <= 2


def test_escena_fuera_de_9_16_con_relleno_desenfocado():
    fx = dict(NOCAP, cutaways=[{"kind": "title_card", "start": 0, "end": 3, "text": {"text": "X"}}])
    WW, HH = 384, 216
    job, _, mods, _ = _job(fx, W=WW, H=HH)
    comp = R.Compositor(job, mods)
    out = comp.frame(10, _solid_wh((0, 220, 0), WW, HH)).copy()
    # nada del video verde queda visible: el costado es la escena desenfocada (oscura)
    assert out[HH // 2, 5, 1] < 120 and out[HH // 2, WW - 5, 1] < 120
    bx, _, bw, _ = comp.box
    assert out[5, bx + bw // 2, :3].tolist() == [0x1B, 0x1B, 0x22]     # escena nitida al centro
    # borde fundido: sin salto brusco entre la escena y el relleno
    row = out[HH // 2, bx - 3:bx + 8, :3].astype(int)
    assert np.abs(np.diff(row, axis=0)).max() < 12


def _solid_wh(rgb, w, h):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., :3] = rgb
    a[..., 3] = 255
    return a


def test_transicion_se_pega_a_la_union_real():
    shots = [{"start": 0.0, "end": 2.5}, {"start": 2.5, "end": 4.0}]
    fx = dict(NOCAP, transitions=[
        {"id": "tr_1", "kind": "slide", "at": 1.1, "dur": 0.3},     # union en t_final=1.0
        {"id": "tr_2", "kind": "flash", "at": 2.45, "dur": 0.3},    # cambio de toma 2.5
        {"id": "tr_3", "kind": "slide", "at": 3.2, "dur": 0.3},     # sin union cerca
        {"id": "tr_4", "kind": "flash", "at": 0.0, "dur": 0.3}])    # antes del primer cuadro
    job, skipped, _, tl = _job(fx, cuts=[{"start": 1.0, "end": 1.4, "enabled": True}],
                               shots=shots)
    trs = {t["id"]: t for t in job["transitions"]}
    assert trs["tr_1"]["_J"] == 30 and trs["tr_1"]["_snapped"]
    assert trs["tr_2"]["_J"] == tl.shot_change_frames(shots)[0] == 63
    assert not trs["tr_3"]["_snapped"] and "transitions:tr_3:sin_union" in job["warnings"]
    assert "transitions:tr_4:fuera_del_video" in skipped and "tr_4" not in trs


def test_base_image_disponible_en_efectos_de_base():
    job, _, mods, _ = _job(dict(NOCAP, effects=[{"kind": "bw", "start": 0, "end": 2}]))
    comp = R.Compositor(job, mods)
    seen = []

    def spy(img, item, lt, dur, ctx):
        seen.append((ctx.base_image() is img, ctx.subject_image()))
        return img
    comp.reg = dict(comp.reg, grade_fx={"bw": spy})
    comp.frame(3, _gradient())
    assert seen == [(True, None)]                        # base = entrada; sin matte: sin sujeto


def test_grano_final_determinista_y_sutil():
    fx = dict(NOCAP, cutaways=[{"kind": "title_card", "start": 0, "end": 3, "text": {"text": "X"}}])
    job, _, mods, _ = _job(fx, dither=1.0)
    job0 = dict(job, dither=0.0)
    a = R.Compositor(job, mods).frame(12, _gradient()).copy()
    b = R.Compositor(job, mods).frame(12, _gradient()).copy()
    c = R.Compositor(job0, mods).frame(12, _gradient()).copy()
    assert np.array_equal(a, b)                          # mismo cuadro en cualquier proceso
    d = a[..., :3].astype(int) - c[..., :3].astype(int)
    assert np.abs(d).max() <= 2 and (d != 0).mean() > 0.4 and abs(d.mean()) < 0.1
    assert (a[..., 3] == 255).all()
    nxt = R.Compositor(job, mods).frame(13, _gradient()).copy()
    assert not np.array_equal(nxt, a)                     # el grano cambia por cuadro
    draft = R.Compositor(dict(job, quality="draft"), mods).frame(12, _gradient()).copy()
    assert np.array_equal(draft, c)                       # sin grano en draft


def test_frame_request_inyecta_cuadro_congelado(monkeypatch):
    """Las escenas que lo piden (photo/self_broll) reciben el cuadro base una sola vez."""
    import types
    calls = []
    fake = types.SimpleNamespace(
        RENDERERS={"photo": lambda c, it, lt, d, ctx: calls.append(it.get("_still"))},
        frame_request=lambda it: ("_still", "start", it.get("start")))
    mods = R.load_modules(STUBS)
    mods["cutaways"] = fake
    fx = dict(NOCAP, cutaways=[{"kind": "photo", "start": 1.0, "end": 2.0}])
    job, _, _, _ = _job(fx, mods=mods)
    assert job["cutaways"][0]["_frame_req"] == ["_still", 30]
    got = []
    comp = R.Compositor(job, mods, frame_source=lambda k: (got.append(k), _gradient())[1])
    for n in (30, 31, 40):
        comp.frame(n, _solid((0, 0, 0)))
    assert got == [30] and all(isinstance(x, __import__("skia").Image) for x in calls)


def test_audio_con_datos_malos_no_tumba(tmp_path):
    plan = S.normalize({"video": "x.mp4", "duration": 4.0, "fx": {}, "version": 2})
    plan["fx"]["music"]["stops"] = [{"at": "x"}, {"at": 1.0}]
    plan["fx"]["sfx"] = [{"id": "sf_1", "name": "pop", "at": "nope"}, {"id": "sf_2", "name": "pop", "at": 1}]
    tl = Timeline.from_plan(plan, fps=FPS)
    job = {"final_duration": 4.0, "style": "dinamico", "seed": 7}
    st = {"skipped": []}
    prog = R._Progress({})
    import soundfile as sf
    voice = tmp_path / "v.wav"
    sf.write(str(voice), np.zeros((4 * 48000, 2), np.float32), 48000)
    res = R._audio(plan, job, tl, {"music": None, "audio": None}, str(voice), tmp_path / "m.wav",
                   prog, st)
    assert res == str(voice) and st["sfx_events"] == 1
    assert "sfx:sf_1:dato_invalido" in st["skipped"] and "music:stops:dato_invalido" in st["skipped"]


def test_stage_plan_monotono():
    sp = R.stage_plan({"timeline": 1, "base": 0, "analysis": 5, "audio": 1, "render": 50, "mux": 3})
    vals = [sp[s] for s in R.STAGE_ORDER]
    assert vals[0][0] == 0 and vals[-1][1] == 100
    assert all(a[1] == pytest.approx(b[0]) for a, b in zip(vals, vals[1:]))


@need_media
def test_render_frame_solo_con_base_en_cache(e2e, tmp_path):
    """Flujo del Studio: ensure_base en segundo plano y luego /frame. Sin pista de rostro ni
    mattes en cache, render_frame los resuelve (rostro completo, matte solo de los cuadros
    necesarios) y coincide con el export."""
    import os
    plan, out, st, _, _ = e2e
    cache = tmp_path / "cache"
    cache.mkdir()
    b = Path(st["base"])
    os.symlink(b, cache / b.name)
    (cache / (b.name + ".json")).write_text(Path(str(b) + ".json").read_text())
    opts = {"modules": STUBS, "sys_path": [TESTS], "cache_dir": str(cache)}
    for n in (250, 460):                                  # texto detras del sujeto, zoom
        t0 = time.time()
        fr = R.render_frame(plan, (n + 0.5) / 30, opts)   # a media duracion del cuadro: floor
        dt = time.time() - t0
        with V.FrameReader(out, 1080, 1920, n, 1, 30) as r:
            ref = r.read()
        diff = np.abs(fr[..., :3].astype(int) - ref[..., :3].astype(int)).mean()
        assert diff < 2.5, (n, diff)
        assert dt < 4.0, dt
    assert list(cache.glob("face_*.npy"))                 # la pista quedo en cache
    assert not list(cache.glob("matte_*"))                # sin matte de TODOS los rangos


@need_media
def test_draft_reutiliza_el_base_del_formato(e2e, tmp_path):
    plan, out, st, _, _ = e2e
    d = R.render_draft(plan, tmp_path / "d.mp4", {"modules": STUBS, "sys_path": [TESTS]})
    assert d["cached_base"] and d["base"] == st["base"]   # no repite el base pass
    si = V.stream_info(tmp_path / "d.mp4")["video"]
    assert (si["width"], si["height"]) == (540, 960) and int(si["nb_frames"]) == st["n_frames"]


@need_media
def test_shorts_remapeados(e2e, tmp_path):
    plan, out, st, _, _ = e2e
    p = dict(plan, highlights=[{"start": 7.0, "end": 12.0, "title": "uno"},
                               {"start": 13.2, "end": 14.4, "title": "en corte"},
                               {"start": 20.0, "end": 26.0, "title": "dos", "enabled": False}])
    res = R.render_shorts(p, out, {"shorts_dir": str(tmp_path)})
    assert [r["title"] for r in res] == ["uno"]          # < 1 s tras el corte / deshabilitado
    tl = Timeline.from_plan(S.normalize(p), fps=30)
    fs, fe = tl.map_range(7.0, 12.0)
    r = res[0]
    assert r["start"] == pytest.approx(fs, abs=1 / 30) and r["end"] == pytest.approx(fe, abs=1 / 30)
    si = V.stream_info(r["path"])
    assert float(si["video"]["duration"]) == pytest.approx(fe - fs, abs=2 / 30)
    assert float(si["audio"]["duration"]) == pytest.approx(fe - fs, abs=0.05)
    assert si["video"]["color_primaries"] == "bt709"
    # el primer cuadro del short es el cuadro final fs del video
    k = int(round(r["start"] * 30))
    with V.FrameReader(r["path"], 270, 480, 0, 1, 30) as a, V.FrameReader(out, 270, 480, k, 1, 30) as b:
        assert np.abs(a.read()[..., :3].astype(int) - b.read()[..., :3].astype(int)).mean() < 4


def test_matte_del_frente_en_inset(tmp_path):
    """Vertical -> 16:9 (inset): el matte se calcula solo en el frente y se ubica en el cuadro."""
    src = _tiny_source(tmp_path / "src.mp4")
    pr = R.prepare({"video": str(src), "duration": 3.0, "version": 2, "fx": {}},
                   {"fmt": "16:9", "quality": "draft", "modules": STUBS, "sys_path": [TESTS]})
    job = pr["job"]
    assert job["matte_box"] == [328, 0, 304, 540] and job["matte_box_base"] == [656, 0, 608, 1080]
    job["matte_ranges"] = [(0, 10)]

    class BoxMatte:
        def get(self, n):
            return np.ones((540, 304), np.float32)
    comp = R.Compositor(job, pr["mods"], matte=BoxMatte())
    m = comp.matte_out(3, None)
    assert m.shape == (540, 960) and m[:, 328:632].min() == 1.0
    assert m[:, :328].max() == 0.0 and m[:, 632:].max() == 0.0
