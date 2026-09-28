"""Pruebas de la ola 3 del motor (render.py): clave publica del analisis, candado de _SafeMain
con hilos concurrentes, opciones de marca (logo/air/enhance_audio) en render_pro, render_draft
y render_frame, y medios propios (imagen/clip del usuario en graphics.media, cutaways.photo y
cutaways.self_broll).

Todo con medios SINTETICOS (sin datos del cliente). Correr:
  cd spike && .venv/bin/python -m pytest tests/test_render_media.py -q
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import paths
from motion import render as R
from motion import video_io as V
from conftest import SCRIPTS

TESTS = str(Path(__file__).resolve().parent)
W, H = 216, 384
MAGENTA = (255, 0, 255)
RGB = [(230, 20, 20), (20, 200, 40), (30, 60, 230)]          # rojo, verde, azul por segundo


# ------------------------------------------------------------------ medios sinteticos
def _src(path: Path, dur=4.0, size="360x640") -> Path:
    """Fuente del plan (testsrc2 + tono) cacheada por ruta."""
    if not path.exists():
        subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"testsrc2=s={size}:r=30:d={dur}", "-f", "lavfi", "-i",
                        f"sine=f=330:r=48000:d={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def _png(path: Path, rgb=MAGENTA, size=(300, 200), exif_rot=None) -> Path:
    from PIL import Image
    im = Image.new("RGB", size, rgb)
    if exif_rot:                             # EXIF Orientation (6 = girar 90 en horario)
        ex = Image.Exif()
        ex[0x0112] = exif_rot
        im.save(path, "JPEG", quality=95, exif=ex.tobytes())
    else:
        im.save(path)
    return path


def _clip(path: Path, colors=RGB, w=320, h=180, fps=30, secs=1.0) -> Path:
    """Clip del usuario: un color plano por segundo (para comprobar el tiempo local)."""
    if not path.exists():
        wr = V.ChunkWriter(path, w, h, fps, "inter")
        for c in colors:
            fr = np.zeros((h, w, 4), np.uint8)
            fr[..., :3] = c
            fr[..., 3] = 255
            for _ in range(int(fps * secs)):
                wr.write(fr)
        wr.close()
    return path


def _plan(video, fx=None, dur=4.0):
    base = {"captions": {"enabled": False}, "grade": {"enabled": False},
            "music": {"enabled": False, "source": "none"}}
    base.update(fx or {})
    return {"video": str(video), "duration": dur, "cuts": [], "phrases": [], "version": 2,
            "fx": base}


def _opts(tmp_path, **kw):
    o = {"W": W, "H": H, "dither": 0.0, "cache_dir": str(tmp_path / "cache"),
         "media_root": str(tmp_path / "media"), "enhance_audio": False}
    o.update(kw)
    return o


def _close(px, rgb, tol=40):
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(px[:3], rgb))


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return _src(tmp_path_factory.mktemp("src") / "fuente.mp4")


@pytest.fixture
def media(tmp_path):
    d = tmp_path / "media"
    d.mkdir()
    _png(d / "foto01.png")
    _png(d / "girada01.jpg", (20, 200, 40), (300, 200), exif_rot=6)
    _clip(d / "clip01.mp4")
    return d


def _pip(mid, **kw):
    it = {"id": "gr_m", "kind": "media", "start": 0.0, "end": 3.5, "x": 0.5, "y": 0.5,
          "size": 0.5, "frame": False, "shadow": 0.0, "anim_in": "none", "anim_out": "none",
          "media": {"sid": mid}}
    it.update(kw)
    return it


# ------------------------------------------------------------------ (1) clave publica
def test_analysis_key_publica_solo_cambia_con_el_analisis(src, tmp_path):
    p = _plan(src)
    o = _opts(tmp_path)
    k0 = R.analysis_key(p, o)
    assert set(k0) == {"base", "full", "media"} and len(k0["base"]) == 16 and len(k0["full"]) == 16
    assert k0["media"] is None                                  # sin medios propios
    t0 = time.perf_counter()
    assert R.analysis_key(p, o) == k0
    assert time.perf_counter() - t0 < 0.5                      # barata: no toca video
    # textos, graficos, SFX, musica y el look NO la cambian
    p2 = _plan(src, {"text": [{"preset": "title_pop", "text": "Hola", "start": 1, "end": 2}],
                     "sfx": [{"name": "pop", "at": 1.0}],
                     "grade": {"enabled": False, "look": "moody"}})
    assert R.analysis_key(p2, o) == k0
    # un efecto que pide matte cambia solo la completa
    p3 = _plan(src, {"effects": [{"kind": "color_pop", "start": 1, "end": 2}]})
    k3 = R.analysis_key(p3, o)
    assert k3["base"] == k0["base"] and k3["full"] != k0["full"]
    # cortes, formato, air y audio de estudio cambian la base
    p4 = dict(_plan(src), cuts=[{"start": 1.0, "end": 1.5, "enabled": True}])
    assert R.analysis_key(p4, o)["base"] != k0["base"]
    assert R.analysis_key(p, _opts(tmp_path, W=None, H=None, fmt="1:1"))["base"] != k0["base"]
    assert R.analysis_key(p, dict(o, enhance_audio=True))["base"] != k0["base"]
    # grade activo: las tomas cuentan en la completa
    g = _plan(src, {"grade": {"enabled": True}})
    kg = R.analysis_key(g, o)
    kg2 = R.analysis_key(dict(g, shots=[{"start": 0, "end": 2}, {"start": 2, "end": 4}]), o)
    assert kg["full"] != kg2["full"] and kg["base"] == kg2["base"]


def test_air_cambia_la_base_solo_en_fuentes_horizontales(tmp_path):
    horiz = _src(tmp_path / "h.mp4", size="640x360")
    o = _opts(tmp_path)
    k0 = R.analysis_key(_plan(horiz), o)
    k1 = R.analysis_key(_plan(horiz), dict(o, air=0.16))
    assert k0["base"] != k1["base"]
    assert R.prepare(_plan(horiz), dict(o, air=9))["opts"]["air"] == 0.35      # acotado
    assert R.prepare(_plan(horiz), dict(o, air=None))["opts"]["air"] is None
    # fuente vertical en 9:16: el aire no cambia la imagen -> se descarta (misma base en cache)
    vert = _src(tmp_path / "v.mp4")
    assert R.prepare(_plan(vert), dict(o, air=0.16))["opts"]["air"] is None
    assert R.analysis_key(_plan(vert), dict(o, air=0.16)) == R.analysis_key(_plan(vert), o)


# ------------------------------------------------------------------ (2) _SafeMain
def test_safe_main_serializa_hilos_y_anida():
    """Dos hilos creando procesos a la vez: mientras uno esta dentro, __main__ sigue oculto
    aunque el otro salga (antes el primero en salir restauraba __file__ y el hijo reimportaba el
    script principal). Anidar en el mismo hilo solo restaura en la salida mas externa."""
    main = sys.modules["__main__"]
    had = hasattr(main, "__file__")
    old, spec = getattr(main, "__file__", None), getattr(main, "__spec__", None)
    main.__file__ = __file__
    seen: list = []
    a_in, a_out, b_in = threading.Event(), threading.Event(), threading.Event()

    def a():
        with R._SafeMain({"OMP_NUM_THREADS": 2}):
            a_in.set()
            a_out.wait(5)
            seen.append(("a", hasattr(main, "__file__")))

    def b():
        a_in.wait(5)
        with R._SafeMain({"OMP_NUM_THREADS": 5}):
            b_in.set()
            time.sleep(0.05)
            seen.append(("b", hasattr(main, "__file__")))
    try:
        ta, tb = threading.Thread(target=a), threading.Thread(target=b)
        ta.start()
        tb.start()
        a_in.wait(5)
        time.sleep(0.1)
        assert not b_in.is_set()                 # b espera el candado mientras a esta dentro
        a_out.set()
        ta.join(5)
        tb.join(5)
        assert b_in.is_set() and seen == [("a", False), ("b", False)]
        assert main.__file__ == __file__ and main.__spec__ is spec
        with R._SafeMain():
            with R._SafeMain():
                assert not hasattr(main, "__file__")
            assert not hasattr(main, "__file__")      # la interna no restaura
        assert main.__file__ == __file__
    finally:
        if had:
            main.__file__ = old
        else:
            del main.__file__


_THREADS_SCRIPT = '''
import sys, threading
sys.path.insert(0, {scripts!r})
sys.path.insert(0, {tests!r})
import os
with open({marker!r}, "a") as f:          # efecto lateral a nivel de modulo (como app/server.py)
    f.write(str(os.getpid()) + "\\n")
from motion import render as R
from _render_plan import STUBS

def job(i, out):
    plan = {{"video": {video!r}, "duration": 3.0, "cuts": [], "phrases": [], "version": 2,
             "fx": {{"captions": {{"enabled": False}}, "grade": {{"enabled": False}},
                    "music": {{"enabled": False, "source": "none"}}}}}}
    R.render_pro(plan, out, {{"W": 216, "H": 384, "workers": 3, "modules": STUBS,
                             "sys_path": [{tests!r}], "enhance_audio": False,
                             "cache_dir": {cache!r}}})
    out_ok.append(i)

if __name__ == "__main__":
    out_ok = []
    ths = [threading.Thread(target=job, args=(i, {out!r} + f"_{{i}}.mp4")) for i in range(3)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    print("OK", len(out_ok))
'''


def test_renders_en_hilos_concurrentes_no_reejecutan_el_script(tmp_path):
    """Regresion real del servidor (export + vista previa a la vez): tres render_pro en hilos
    del mismo proceso lanzan 9 workers spawn y ninguno reimporta el script principal."""
    src_v = _src(tmp_path / "src.mp4", dur=3.0)
    marker = tmp_path / "marker.txt"
    script = tmp_path / "hilos_con_efectos.py"
    script.write_text(_THREADS_SCRIPT.format(scripts=str(SCRIPTS), tests=TESTS, marker=str(marker),
                                             video=str(src_v), out=str(tmp_path / "o"),
                                             cache=str(tmp_path / "c")))
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "OK 3" in r.stdout, r.stdout[-500:]
    assert len(marker.read_text().split()) == 1, marker.read_text()


# ------------------------------------------------------------------ (7) marca
def test_logo_cfg_saneado():
    assert R.logo_cfg(None) is None and R.logo_cfg(False) is None
    assert R.logo_cfg({"enabled": False, "pos": "tl"}) is None
    d = R.logo_cfg(True)
    assert d["pos"] == "br" and d["size"] == 0.14 and d["opacity"] == 0.9
    assert d["path"] == str(paths.BRAND / "logo.png")
    d = R.logo_cfg({"pos": "top-left", "size": 7, "opacity": -1, "path": "/x/l.png"})
    assert (d["pos"], d["size"], d["opacity"], d["path"]) == ("tl", 0.5, 0.0, "/x/l.png")
    assert R.logo_cfg({"pos": "raro", "size": "nan"})["pos"] == "br"
    assert set(R.PRO_OPTS) == {"logo", "air", "enhance_audio"}


def _logo_file(tmp_path):
    from PIL import Image
    p = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), LOGO_RGB + (255,)).save(p)
    return p


LOGO_RGB = (255, 128, 0)


def _corner_has_logo(fr, pos, rgb=LOGO_RGB):
    h, w = fr.shape[:2]
    m = int(round(w * 0.03))
    lw = int(round(w * 0.2))
    lh = lw // 2
    x = m + lw // 2 if pos in ("tl", "bl") else w - m - lw // 2
    y = m + lh // 2 if pos in ("tl", "tr") else h - m - lh // 2
    return _close(fr[y, x], rgb, 30)


def test_logo_en_render_frame_por_esquina(src, tmp_path):
    logo = _logo_file(tmp_path)
    p = _plan(src)
    sin = R.render_frame(p, 1.0, _opts(tmp_path))
    for pos in ("tl", "tr", "bl", "br"):
        fr = R.render_frame(p, 1.0, _opts(tmp_path, logo={"pos": pos, "size": 0.2, "opacity": 1.0,
                                                           "path": str(logo)}))
        assert _corner_has_logo(fr, pos), pos
        assert not _corner_has_logo(sin, pos), pos
    # opacidad: mezcla con el cuadro
    fr = R.render_frame(p, 1.0, _opts(tmp_path, logo={"pos": "br", "size": 0.2, "opacity": 0.5,
                                                       "path": str(logo)}))
    assert not _corner_has_logo(fr, "br")
    # sin archivo: se omite con aviso (no truena)
    pr = R.prepare(p, _opts(tmp_path, logo={"path": str(tmp_path / "no.png")}))
    assert "logo:archivo" in pr["skipped"] and pr["job"]["logo"] is None


def test_marca_en_render_pro_y_draft(src, tmp_path):
    """El logo sale en el export y en la vista previa real (antes el servidor no lo pasaba y el
    Studio lo deshabilitaba en la edicion IA)."""
    logo = _logo_file(tmp_path)
    p = _plan(src)
    lg = {"pos": "tr", "size": 0.2, "opacity": 1.0, "path": str(logo)}
    out = tmp_path / "final.mp4"
    st = R.render_pro(p, out, _opts(tmp_path, logo=lg, workers=2, enhance_audio=True))
    with V.FrameReader(out, W, H, 30, 1, 30) as r:
        fr = r.read()
    assert _corner_has_logo(fr, "tr") and "logo:archivo" not in st["skipped"]
    # enhance_audio llega a la voz del base (otra clave de voz)
    st2 = R.render_pro(p, tmp_path / "f2.mp4", _opts(tmp_path, logo=lg, workers=0,
                                                       enhance_audio=False))
    assert st2["voice"] != st["voice"] and st2["base"] == st["base"]
    d = tmp_path / "draft.mp4"
    std = R.render_draft(p, d, dict(_opts(tmp_path, logo=lg), W=None, H=None, fmt="9:16"))
    with V.FrameReader(d, std["W"], std["H"], 30, 1, 30) as r:
        frd = r.read()
    assert (std["W"], std["H"]) == (540, 960) and _corner_has_logo(frd, "tr")


# ------------------------------------------------------------------ (4) medios propios
def test_media_path_por_sid_y_resolver(tmp_path, media):
    o = {"media_root": str(media)}
    assert R.media_path({"sid": "foto01"}, o) == media / "foto01.png"
    assert R.media_path({"sid": "clip01", "type": "video"}, o) == media / "clip01.mp4"
    for bad in ({"sid": "../media/foto01"}, {"sid": ""}, {"sid": "no_existe"}, "foto01", None,
                {"path": str(media / "foto01.png")}):
        assert R.media_path(bad, o) is None, bad
    # resolver inyectable (p. ej. el servidor con su propia tabla de fuentes)
    o2 = {"media_resolver": lambda m: media / "clip01.mp4" if m.get("sid") == "zz" else None}
    assert R.media_path({"sid": "zz"}, o2) == media / "clip01.mp4"
    assert R.media_path({"sid": "foto01"}, o2) is None
    # default: la carpeta de POST /sources del servidor
    assert R._media_roots({}) == [paths.INPUT / "sources"]


def test_pip_de_imagen_en_render_frame(src, tmp_path, media):
    p = _plan(src, {"graphics": [_pip("foto01")]})
    fr = R.render_frame(p, 1.0, _opts(tmp_path))
    assert _close(fr[H // 2, W // 2], MAGENTA, 12)                    # centro del PiP
    # la tarjeta toma la proporcion del medio (300x200): ancho 0.5*W, alto 0.5*W/1.5
    col = fr[:, W // 2, :3].astype(int)
    rows = np.where((np.abs(col - MAGENTA) < 25).all(1))[0]
    assert abs((rows.max() - rows.min() + 1) - 0.5 * W / 1.5) <= 3
    # EXIF: la foto 300x200 con Orientation=6 se ve vertical (200x300)
    p2 = _plan(src, {"graphics": [_pip("girada01")]})
    fr2 = R.render_frame(p2, 1.0, _opts(tmp_path))
    col2 = fr2[:, W // 2, :3].astype(int)
    rows2 = np.where((np.abs(col2 - (20, 200, 40)) < 30).all(1))[0]
    assert abs((rows2.max() - rows2.min() + 1) - 0.5 * W * 1.5) <= 4
    # fuera de su rango no se dibuja; y con aspect + cover llena la tarjeta
    fr3 = R.render_frame(p, 3.8, _opts(tmp_path))
    assert not _close(fr3[H // 2, W // 2], MAGENTA, 12)


def test_pip_de_clip_sigue_el_tiempo_local(src, tmp_path, media):
    o = _opts(tmp_path)
    for loop, last in ((True, RGB[0]), (False, RGB[2])):          # 3.2 s de un clip de 3 s
        p = _plan(src, {"graphics": [_pip("clip01", start=0.5, end=3.9, loop=loop)]})
        for t, rgb in ((0.9, RGB[0]), (1.9, RGB[1]), (2.9, RGB[2]), (3.7, last)):
            fr = R.render_frame(p, t, o)                              # 1 cuadro de la fuente
            assert _close(fr[H // 2, W // 2], rgb, 45), (loop, t, fr[H // 2, W // 2])


def test_medio_faltante_se_omite_con_aviso(src, tmp_path, media):
    p = _plan(src, {"graphics": [_pip("no_esta"), dict(_pip("foto01"), id="gr_sin", media=None)],
                    "cutaways": [{"id": "cw_f", "kind": "photo", "start": 1, "end": 2,
                                  "media": {"sid": "no_esta"}}]})
    pr = R.prepare(p, _opts(tmp_path))
    assert {"media:gr_m:no_encontrado", "media:gr_sin:sin_medio",
            "media:cw_f:no_encontrado"} <= set(pr["skipped"])
    assert pr["job"]["graphics"] == [] and pr["job"]["cutaways"] == []


def test_escena_foto_y_broll_con_medio_propio(src, tmp_path, media):
    """cutaways.photo con la foto del usuario (en vez del cuadro congelado) y self_broll con
    su clip a pantalla completa siguiendo el tiempo local."""
    p = _plan(src, {"cutaways": [
        {"id": "cw_p", "kind": "photo", "start": 0.2, "end": 2.2, "background": "color",
         "bg_colors": ["#202020"], "tilt": 0, "media": {"sid": "foto01"}, "anim_in": "cut",
         "anim_out": "cut"},
        {"id": "cw_b", "kind": "self_broll", "start": 2.3, "end": 3.9, "src_t": 0.0,
         "zoom_from": 1.0, "zoom_to": 1.0, "media": {"sid": "clip01", "type": "video"},
         "anim_in": "cut", "anim_out": "cut"}]})
    o = _opts(tmp_path)
    pr = R.prepare(p, o)
    assert all("_frame_req" not in c for c in pr["job"]["cutaways"])   # el medio reemplaza
    fr = R.render_frame(p, 1.8, o)
    assert _close(fr[int(H * 0.44), W // 2], MAGENTA, 30), fr[int(H * 0.44), W // 2]
    fb = R.render_frame(p, 2.5, o)                                    # local 0.2 s -> rojo
    assert _close(fb[H // 2, W // 2], RGB[0], 45)
    fb2 = R.render_frame(p, 3.5, o)                                   # local 1.2 s -> verde
    assert _close(fb2[H // 2, W // 2], RGB[1], 45)


def test_render_pro_con_medios_en_paralelo_y_draft(src, tmp_path, media):
    """Export con 3 workers: el clip del usuario se normaliza UNA vez en la cache y cada
    worker lo lee en orden (los chunks parten el item); el draft usa su propio tamano."""
    p = _plan(src, {"graphics": [_pip("clip01", start=0.0, end=2.95)],
                    "cutaways": [{"id": "cw_p", "kind": "photo", "start": 3.0, "end": 3.9,
                                  "background": "color", "bg_colors": ["#202020"], "tilt": 0,
                                  "media": {"sid": "foto01"}, "anim_in": "cut", "anim_out": "cut"}]})
    out = tmp_path / "m.mp4"
    st = R.render_pro(p, out, _opts(tmp_path, workers=3))
    assert st["chunks"] >= 3 and st["media"]["items"] == 2 and st["errors"] == {}
    assert not [s for s in st["skipped"] if s.startswith("media:")]
    clips = list((tmp_path / "cache").glob("media_*.mp4"))
    assert len(clips) == 1 and len(list((tmp_path / "cache").glob("media_*.png"))) == 1
    with V.FrameReader(out, W, H, 0, None, 30) as r:
        frames = [f for f in r]
    for k in (10, 40, 70, 85):                   # 0.33 s rojo, 1.33 verde, 2.33 azul, 2.83 azul
        exp = RGB[min(2, int(k / 30))]
        assert _close(frames[k][H // 2, W // 2], exp, 45), (k, frames[k][H // 2, W // 2])
    assert _close(frames[112][int(H * 0.44), W // 2], MAGENTA, 35)  # la foto de la escena
    # render_frame reutiliza el clip normalizado del export (mismo resultado)
    fr = R.render_frame(p, 40 / 30, _opts(tmp_path))
    assert np.abs(fr[..., :3].astype(int) - frames[40][..., :3].astype(int)).mean() < 3.0
    # y sin el clip en cache (cuadro suelto de la fuente) el PiP se ve igual: mismo cuadro del
    # clip y misma conversion de color
    fr1 = R.render_frame(p, 40 / 30, _opts(tmp_path, cache_dir=str(tmp_path / "otra_cache")))
    pip = (slice(H // 2 - 20, H // 2 + 20), slice(W // 2 - 40, W // 2 + 40))
    assert np.abs(fr1[pip][..., :3].astype(int) - frames[40][pip][..., :3].astype(int)).mean() < 4.0
    d = tmp_path / "d.mp4"
    sd = R.render_draft(p, d, dict(_opts(tmp_path), W=None, H=None))
    assert sd["media"]["items"] == 2 and len(list((tmp_path / "cache").glob("media_*.mp4"))) == 2


def test_ensure_analysis_deja_los_medios_normalizados(src, tmp_path, media):
    """El analisis de fondo (al abrir el proyecto en el Studio) tambien normaliza los medios
    propios: el primer export/draft y /frame no pagan la decodificacion del clip."""
    p = _plan(src, {"graphics": [_pip("clip01", start=0.5, end=2.5)]})
    r = R.ensure_analysis(p, _opts(tmp_path, prefetch_music=False))
    assert not [s for s in r["skipped"] if s.startswith("media:")]
    assert len(list((tmp_path / "cache").glob("media_*.mp4"))) == 1


# ================================================================== ola 3b: arreglos de la critica
from conftest import load_factor  # noqa: E402
from motion import ctx as C  # noqa: E402

P3_ICC = Path("/System/Library/ColorSync/Profiles/Display P3.icc")


def _sips():
    import shutil
    return shutil.which("sips")


def _heic(path: Path, size=(2000, 1500), exif_rot=6, icc=None) -> Path:
    """HEIC sintetico con sips (macOS). > 512 px -> rejilla de teselas como los del iPhone;
    Orientation 6 -> rotacion (irot). Rojo a la izquierda, azul a la derecha, verde arriba."""
    if not _sips():
        pytest.skip("sin sips (macOS) para generar un HEIC")
    from PIL import Image
    w, h = size
    a = np.zeros((h, w, 3), np.uint8)
    a[:, :w // 2] = (230, 20, 20)
    a[:, w // 2:] = (30, 60, 230)
    a[:h // 8] = (20, 200, 40)
    ex = Image.Exif()
    if exif_rot:
        ex[0x0112] = exif_rot
    jpg = path.with_name(path.stem + "_src.jpg")
    Image.fromarray(a).save(jpg, "JPEG", quality=95, exif=ex.tobytes(),
                            **({"icc_profile": icc} if icc else {}))
    r = subprocess.run(["sips", "-s", "format", "heic", str(jpg), "--out", str(path)],
                       capture_output=True)
    jpg.unlink(missing_ok=True)
    if r.returncode != 0 or not path.exists():
        pytest.skip("sips no genero el HEIC")
    return path


# ------------------------------------------------------------------ HEIC del iPhone
def test_heic_en_rejilla_con_rotacion(src, tmp_path, media):
    """Critico de la critica: un HEIC real del iPhone es una rejilla de teselas (ffprobe da
    512x512 de UNA tesela y un -vf choca con el filtergraph de la rejilla). Se decodifica por
    ffmpeg sin filtros: dims completas, rotacion aplicada y sin `ilegible`."""
    h = _heic(media / "fotoheic.heic")
    assert V.probe(h)["w"] == 512                         # lo que veia el motor antes
    assert R.image_size(h) == (1500, 2000)                # rejilla completa y girada
    arr = R.decode_image(h, 4000, 4000)
    assert arr.shape == (2000, 1500, 4)
    # Orientation 6 (90 en horario): la izquierda (rojo) queda arriba, la franja verde a la derecha
    assert _close(arr[300, 600], (230, 20, 20), 30) and _close(arr[1900, 600], (30, 60, 230), 30)
    assert _close(arr[1000, 1480], (20, 200, 40), 35)
    # miniatura para POST /sources (el servidor la usaba con -vf y fallaba)
    w0, h0 = R.image_thumb(h, tmp_path / "t.jpg", 320)
    from PIL import Image
    assert (w0, h0) == (1500, 2000) and Image.open(tmp_path / "t.jpg").size == (320, 427)
    # en el render: PiP y escena de foto sin avisos de medio ilegible
    p = _plan(src, {"graphics": [_pip("fotoheic", size=0.5)],
                    "cutaways": [{"id": "cw_h", "kind": "photo", "start": 3.6, "end": 3.95,
                                  "media": {"sid": "fotoheic"}}]})
    o = _opts(tmp_path)
    pr = R.prepare(p, o)
    assert R.prepare_media(pr["job"], o) == []
    fr = R.render_frame(p, 1.0, o)
    card_h = 0.5 * W * 2000 / 1500
    assert _close(fr[int(H / 2 - card_h / 4), W // 2 - 10], (230, 20, 20), 35)


# ------------------------------------------------------------------ color (Display P3)
def test_color_display_p3_a_srgb(tmp_path):
    """Fotos del iPhone en Display P3: el perfil ICC (JPEG/PNG/HEIC) o el nclx P3 del HEIC se
    convierten a sRGB (skia asume sRGB); antes salian desaturadas."""
    g = np.full((2, 2, 4), 128, np.uint8)
    assert np.array_equal(R._p3_to_srgb(g), g)                       # el gris no cambia
    assert tuple(R._p3_to_srgb(np.array([[[255, 0, 0, 255]]], np.uint8))[0, 0]) == (255, 0, 0, 255)
    col = (200, 120, 80)
    out = R._p3_to_srgb(np.array([[[*col, 200]]], np.uint8))[0, 0]
    assert out[0] > col[0] and out[1] < col[1] and out[3] == 200     # mas saturado, alfa intacto
    if not P3_ICC.exists():
        pytest.skip("sin el perfil Display P3 del sistema (macOS)")
    import io
    from PIL import Image, ImageCms
    icc = P3_ICC.read_bytes()
    ref = ImageCms.profileToProfile(Image.new("RGB", (1, 1), col), ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                    ImageCms.createProfile("sRGB")).getpixel((0, 0))
    assert _close(out, ref, 3)                                       # la matriz nclx = el perfil
    Image.new("RGB", (64, 64), col).save(tmp_path / "p3.png", icc_profile=icc)
    Image.new("RGB", (64, 64), col).save(tmp_path / "p3.jpg", quality=100, subsampling=0, icc_profile=icc)
    for f in ("p3.png", "p3.jpg"):
        a = R.decode_image(tmp_path / f)
        assert _close(a[32, 32], ref, 3) and not _close(a[32, 32], col, 4), (f, a[32, 32], ref)
    if _sips():                                                      # HEIC con el mismo perfil
        r = subprocess.run(["sips", "-s", "format", "heic", str(tmp_path / "p3.png"), "--out",
                            str(tmp_path / "p3.heic")], capture_output=True)
        if r.returncode == 0 and R._heif_color(tmp_path / "p3.heic"):
            a = R.decode_image(tmp_path / "p3.heic")
            assert _close(a[32, 32], ref, 6), (a[32, 32], ref)


# ------------------------------------------------------------------ medio ilegible
def test_medio_ilegible_fuera_del_export_marcado_en_frame(src, tmp_path, media, monkeypatch):
    """Un medio que no se puede leer: en el export su item NO sale (ni el marcador gris del PiP
    ni una escena de foto con la persona y el pie del usuario); en /frame se ve un marcador; y
    el fallo se recuerda (no se reintenta en cada cuadro ni en cada export)."""
    (media / "rota01.jpg").write_bytes(b"esto no es una imagen")
    (media / "roto01.mp4").write_bytes(b"\x00" * 4096)
    fx = {"graphics": [_pip("rota01", start=0.2, end=1.8)],
          "cutaways": [{"id": "cw_r", "kind": "photo", "start": 2.0, "end": 3.0, "caption": "Mi foto",
                        "background": "color", "bg_colors": ["#202020"], "tilt": 0,
                        "media": {"sid": "roto01", "type": "video"}, "anim_in": "cut", "anim_out": "cut"}]}
    p = _plan(src, fx)
    o = _opts(tmp_path)
    st = R.render_pro(p, tmp_path / "x.mp4", _opts(tmp_path, workers=0))
    assert {"media:gr_m:ilegible", "media:cw_r:ilegible"} <= set(st["skipped"])
    assert sorted(st["media"]["dropped"]) == ["cw_r", "gr_m"]
    assert st["items"]["graphics"] == 0 and st["items"]["cutaways"] == 0
    clean = tmp_path / "limpio.mp4"
    R.render_pro(_plan(src), clean, _opts(tmp_path, workers=0))
    for k in (30, 75):                                               # PiP y escena: nada
        with V.FrameReader(tmp_path / "x.mp4", W, H, k, 1, 30) as r:
            a = r.read()
        with V.FrameReader(clean, W, H, k, 1, 30) as r:
            b = r.read()
        assert np.abs(a[..., :3].astype(int) - b[..., :3].astype(int)).mean() < 2.0, k
    # /frame: marcador gris (#2A2A33) en la tarjeta del PiP (la edicion muestra que falta)
    fr = R.render_frame(p, 1.0, o)
    assert _close(fr[H // 2 - 40, W // 2 - 40], (42, 42, 51), 14), fr[H // 2 - 40, W // 2 - 40]
    fp = R.render_frame(p, 2.6, o)                                   # hueco de la foto: marcador
    assert _close(fp[int(H * 0.40), W // 2 - 50], (42, 42, 51), 20), fp[int(H * 0.40), W // 2 - 50]
    # recordado: ni en memoria ni desde la marca en disco se vuelve a decodificar/probar
    calls = []
    pr = R.prepare(p, o)
    monkeypatch.setattr(R, "_decode_image_rgba", lambda *a, **k: calls.append(a) or 1 / 0)
    monkeypatch.setattr(R.V, "probe", lambda *a, **k: calls.append(a) or 1 / 0)
    for clear in (False, True):
        if clear:
            R._MEDIA_FAILS.clear()                                  # otro proceso: la marca en disco
        job = __import__("copy").deepcopy(pr["job"])
        w = R.prepare_media(job, pr["opts"])
        assert calls == [] and set(w) == {"media:gr_m:ilegible", "media:cw_r:ilegible"}
    assert len(list((tmp_path / "cache").glob("media_*.failed"))) == 2


# ------------------------------------------------------------------ PiP automatico
def _zone(f):
    return (f[0] - f[2] / 2 - 0.03, f[1] - f[3] / 2 - 0.05, f[0] + f[2] / 2 + 0.03, f[1] + f[3] / 2 + 0.03)


def test_pip_automatico_no_tapa_la_cara():
    S = C.SAFE
    for face in ([0.5, 0.33, 0.3, 0.17], [0.62, 0.3, 0.25, 0.14], [0.35, 0.4, 0.2, 0.12],
                 [0.47, 0.28, 0.36, 0.2]):
        for w, h in ((0.36, 0.36), (0.36, 0.114), (0.36, 0.152), (0.3, 0.45)):
            x, y, s = R.media_auto_place(face, w, h, 0.66)
            r = (x - w * s / 2, y - h * s / 2, x + w * s / 2, y + h * s / 2)
            assert r[0] >= S["left"] - 1e-6 and r[2] <= S["right"] + 1e-6, (face, w, h, r)
            assert r[1] >= S["top"] - 1e-6 and r[3] <= 0.66 + 1e-6, (face, w, h, r)
            assert R._overlap(r, _zone(face)) == 0.0, (face, w, h, r, s)
    # lado contrario a la cara; sin cara, arriba a la derecha
    assert R.media_auto_place([0.66, 0.3, 0.2, 0.12], 0.25, 0.2, 0.66)[0] < 0.4
    assert R.media_auto_place([0.3, 0.3, 0.2, 0.12], 0.25, 0.2, 0.66)[0] > 0.55
    x, y, s = R.media_auto_place(None, 0.3, 0.2, 0.66)
    assert x > 0.6 and y < 0.3 and s == 1.0
    # otros overlays activos (un titulo en el pecho): se evitan si hay lugar, sin tapar la cara
    face = [0.5, 0.33, 0.3, 0.17]
    x, y, s = R.media_auto_place(face, 0.36, 0.114, 0.66)
    assert y > 0.45                                                   # el pecho estaba libre
    avoid = [(0.06, 0.45, 0.88, 0.62)]
    x, y, s = R.media_auto_place(face, 0.36, 0.114, 0.66, avoid)
    r = (x - 0.18 * s, y - 0.057 * s, x + 0.18 * s, y + 0.057 * s)
    assert R._overlap(r, avoid[0]) == 0.0 and R._overlap(r, _zone(face)) == 0.0


def test_pip_sin_posicion_se_coloca_en_el_job_y_en_frame(src, tmp_path, media):
    """El PiP que suelta el usuario (x/y null por schema) se coloca lejos de la cara con la
    pista de rostro; `size` es el maximo (puede achicarse) y `_size0` guarda el pedido."""
    from motion import schema as S
    ps = {q["k"]: q for q in S.KINDS["tracks"]["graphics"]["items"]["media"]["params"]}
    assert ps["x"]["default"] is None and ps["y"]["default"] is None and ps["size"]["default"] == 0.36
    it = {"id": "gr_a", "kind": "media", "start": 0.0, "end": 3.5, "frame": False, "shadow": 0.0,
          "anim_in": "none", "anim_out": "none", "media": {"sid": "foto01"}}
    p = _plan(src, {"graphics": [it], "captions": {"enabled": True}})
    o = _opts(tmp_path)
    pr = R.prepare(p, o)
    job = pr["job"]
    face = np.tile([0.4, 0.3, 0.34, 0.2], (job["n_frames"], 1))
    R.set_anchors(job, face, pr["mods"])
    g = job["graphics"][0]
    assert g["_auto_xy"] and g["_size0"] == 0.36

    def card_ok(g, f):
        w = g["size"] * W
        h = w / 1.5                                                  # foto 300x200
        r = (g["x"] - w / W / 2, g["y"] - h / H / 2, g["x"] + w / W / 2, g["y"] + h / H / 2)
        return R._overlap(r, _zone(f)) == 0.0 and r[3] <= 0.72 - 0.05
    assert card_ok(g, [0.4, 0.3, 0.34, 0.2])
    # otra pasada con otra cara (primer plano: la cara grande) recalcula desde lo pedido
    f2 = [0.55, 0.36, 0.5, 0.3]
    R.set_anchors(job, np.tile(f2, (job["n_frames"], 1)), pr["mods"])
    g = job["graphics"][0]
    assert g["_size0"] == 0.36 and g["size"] < 0.36 and card_ok(g, f2)
    # sin cara (testsrc): arriba a la derecha en el cuadro real
    fr = R.render_frame(p, 1.0, o)
    x, y, _ = R.media_auto_place(None, 0.36 * 1.06, 0.36 * W / 1.5 * 1.06 / H, 0.72 - 0.06)
    assert _close(fr[int(y * H), int(x * W)], MAGENTA, 14), (x, y, fr[int(y * H), int(x * W)])


# ------------------------------------------------------------------ loop y draft
def test_loop_repite_desde_media_in(src, tmp_path, media):
    """`loop` repite [media_in, fin] (antes volvia a 0) y sin loop se congela el ultimo cuadro;
    igual en el cuadro suelto de /frame y en el clip normalizado del export."""
    G, B = RGB[1], RGB[2]
    lts = (0.5, 1.5, 2.3, 3.3)                              # clip 3 s, media_in 1 -> tramo de 2 s
    for loop, exp in ((True, (G, B, G, B)), (False, (G, B, B, B))):
        p = _plan(src, {"graphics": [_pip("clip01", start=0.2, end=3.9, media_in=1.0, loop=loop)]})
        o = _opts(tmp_path, media_bg=False, cache_dir=str(tmp_path / f"c{loop}"))
        for lt, rgb in zip(lts, exp):
            fr = R.render_frame(p, 0.2 + lt, o)
            assert _close(fr[H // 2, W // 2], rgb, 45), ("frame", loop, lt, fr[H // 2, W // 2])
        out = tmp_path / f"loop_{loop}.mp4"
        R.render_pro(p, out, dict(o, workers=2))
        for lt, rgb in zip(lts, exp):
            with V.FrameReader(out, W, H, int((0.2 + lt) * 30), 1, 30) as r:
                a = r.read()
            assert _close(a[H // 2, W // 2], rgb, 45), ("export", loop, lt, a[H // 2, W // 2])


def test_draft_lee_el_clip_normalizado_del_export(src, tmp_path, media):
    """ensure_media/ensure_analysis normalizan al tamano del EXPORT y el draft (540x960) lee
    ese mismo clip escalado: no lo vuelve a normalizar."""
    p = _plan(src, {"graphics": [_pip("clip01", start=0.5, end=2.5)]})
    o = dict(_opts(tmp_path), W=None, H=None, fmt="9:16", prefetch_music=False)
    r = R.ensure_media(p, o)
    assert r["items"] == 1 and r["skipped"] == []
    before = sorted((tmp_path / "cache").glob("media_*.mp4"))
    assert len(before) == 1
    sd = R.render_draft(p, tmp_path / "d.mp4", o)
    assert sorted((tmp_path / "cache").glob("media_*.mp4")) == before and sd["media"]["items"] == 1
    with V.FrameReader(tmp_path / "d.mp4", sd["W"], sd["H"], 30, 1, 30) as rd:
        a = rd.read()
    assert _close(a[sd["H"] // 2, sd["W"] // 2], RGB[0], 45)


def test_clave_media_y_ensure_media(src, tmp_path, media):
    """analysis_key()['media']: cambia al agregar/cambiar un medio (el servidor llama
    ensure_media); base y full no cambian; mover el item tampoco."""
    o = _opts(tmp_path)
    p = _plan(src, {"graphics": [_pip("clip01", start=0.5, end=2.5)]})
    k = R.analysis_key(p, o)
    k0 = R.analysis_key(_plan(src), o)
    assert len(k["media"]) == 16 and (k["base"], k["full"]) == (k0["base"], k0["full"])
    moved = _plan(src, {"graphics": [_pip("clip01", start=1.0, end=3.0)]})
    assert R.analysis_key(moved, o)["media"] == k["media"]
    for other in (_pip("foto01", start=0.5, end=2.5), _pip("clip01", start=0.5, end=2.5, media_in=0.5),
                  _pip("clip01", start=0.5, end=3.5), _pip("clip01", start=0.5, end=2.5, crop=[0, 0, 0.5, 1])):
        assert R.analysis_key(_plan(src, {"graphics": [other]}), o)["media"] != k["media"], other
    r = R.ensure_media(p, o)
    assert r["items"] == 1 and len(list((tmp_path / "cache").glob("media_*.mp4.json"))) == 1
    job = R.prepare(p, o)["job"]
    R.prepare_media(job, o, only_images=True)                       # /frame lo encuentra
    assert job["graphics"][0]["_media"].get("file")


def test_frame_normaliza_el_clip_en_segundo_plano(src, tmp_path, media):
    """El primer /frame de un clip sin normalizar decodifica ese cuadro suelto y deja la
    normalizacion en un hilo de fondo: los siguientes leen el clip en cache."""
    p = _plan(src, {"graphics": [_pip("clip01", start=0.5, end=2.5)]})
    o = _opts(tmp_path)
    fr = R.render_frame(p, 1.0, o)
    assert _close(fr[H // 2, W // 2], RGB[0], 45)
    t0 = time.time()
    while time.time() - t0 < 60 * load_factor() and not list((tmp_path / "cache").glob("media_*.mp4.json")):
        time.sleep(0.2)
    assert len(list((tmp_path / "cache").glob("media_*.mp4.json"))) == 1
    job = R.prepare(p, o)["job"]
    R.prepare_media(job, o, only_images=True)
    assert job["graphics"][0]["_media"].get("file")
    fr2 = R.render_frame(p, 1.0, o)
    assert np.abs(fr2[..., :3].astype(int) - fr[..., :3].astype(int)).mean() < 3.0


# ------------------------------------------------------------------ costo por cuadro
def test_medios_por_cuadro_sin_ajuste_caro(src, tmp_path, media):
    """self_broll cover: el clip se normaliza horneado a la caja (focus aplicado) y va tal cual
    (antes fit_media bicubico por cuadro: ~110 ms a 1080x1920); photo: solo _media_image (el
    renderer lo ajusta; antes se calculaba un _still que nadie usaba); fit_media 1:1 y contain
    baratos."""
    m = {"kind": "self_broll", "fit": "cover", "focus": [0.2, 0.5], "crop": None, "zoom": 1.0}
    w, h, crop, baked = R._clip_target({}, m, 1920, 1080, (1080, 1920))
    assert (w, h, baked) == (1080, 1920, True) and abs(crop[0] - 80.25 / 1920) < 1e-3
    w, h, _, baked = R._clip_target({"size": 0.72}, {"kind": "photo", "fit": "cover", "zoom": 1.0},
                                    1080, 1920, (1080, 1920))
    assert (w, h) == (708, 1258) and not baked                     # el hueco, no la caja
    rng = np.random.default_rng(1)
    a = rng.integers(0, 255, (1920, 1080, 4), dtype=np.uint8)
    a[..., 3] = 255
    img = C.image_from_rgba(a)

    def best(fn, k=5):
        fn()
        ts = []
        for _ in range(k):
            t = time.perf_counter()
            fn()
            ts.append(time.perf_counter() - t)
        return min(ts) * 1000
    lf = load_factor()
    assert best(lambda: R.fit_media(img, 1080, 1920, "cover")) < 8.0 * lf
    assert best(lambda: R.fit_media(img, 1080, 1920, "contain")) < 20.0 * lf
    # compositor a 1080x1920: self_broll con clip e imagen de foto, costo con el medio ACTIVO
    fx = {"cutaways": [
        {"id": "cw_b", "kind": "self_broll", "start": 0.0, "end": 2.0, "src_t": 0.0,
         "media": {"sid": "clip01", "type": "video"}, "anim_in": "cut", "anim_out": "cut"},
        {"id": "cw_p", "kind": "photo", "start": 2.1, "end": 3.9, "media": {"sid": "clip01", "type": "video"},
         "anim_in": "cut", "anim_out": "cut"}]}
    o = _opts(tmp_path, W=1080, H=1920)
    pr = R.prepare(_plan(src, fx), o)
    job = pr["job"]
    assert R.prepare_media(job, o) == []
    base = np.zeros((1920, 1080, 4), np.uint8)
    comp = R.Compositor(job, pr["mods"], frame_source=lambda k: base)
    try:
        ts = []
        for n in range(3, 33):
            t = time.perf_counter()
            comp._inject_media(n / 30)
            ts.append(time.perf_counter() - t)
        b, ph = job["cutaways"]
        assert b["_src_image"] is b["_media_image"] and b["_media"]["baked"]
        comp._inject_media(2.5)
        assert "_still" not in ph and ph["_media_image"].width() <= 708
        ms = float(np.median(ts)) * 1000
        assert ms < 25.0 * lf, ms                                   # leer + subir el cuadro
    finally:
        comp.media.close()


# ------------------------------------------------------------------ foto con pie y subtitulos
def test_foto_con_pie_mueve_los_subtitulos(src, tmp_path, media):
    """El pie escrito de la foto choca con la franja de subtitulos (0.72): en vez de ocultarlos
    (se perdia la voz en esos segundos) se MUEVEN (y_ranges internos, `_auto`) sobre la parte
    baja de la foto. Sin choque (foto mas arriba) o con keep_captions no se tocan; el pie como
    cadena en `text` tambien cuenta; si la foto se omite (medio ilegible) la fila se va."""
    fx = {"captions": {"enabled": True, "preset": "clean_bold",
                       "y_ranges": [{"start": 3.5, "end": 3.9, "y": 0.6}]},
          "cutaways": [
              {"id": "cw_1", "kind": "photo", "start": 1.0, "end": 2.0, "caption": "Mi local",
               "media": {"sid": "foto01"}},
              {"id": "cw_2", "kind": "photo", "start": 2.1, "end": 2.3, "caption": "  "},
              {"id": "cw_3", "kind": "photo", "start": 2.4, "end": 2.6, "caption": "Arriba", "y": 0.3},
              {"id": "cw_4", "kind": "photo", "start": 2.7, "end": 2.9, "caption": "Fijo", "keep_captions": True},
              {"id": "cw_5", "kind": "photo", "start": 3.0, "end": 3.3, "text": "Pie como texto"}]}
    job = R.prepare(_plan(src, fx), _opts(tmp_path))["job"]
    cap = job["captions"]
    auto = [r for r in cap["y_ranges"] if r.get("_auto")]
    assert [r["_id"] for r in auto] == ["cw_1"]
    assert 0.55 < auto[0]["y"] < 0.63, auto
    assert cap["y_ranges"][-1] == {"start": 3.5, "end": 3.9, "y": 0.6}   # la del usuario sigue
    assert not any(a < 2.0 and b > 1.0 for a, b in cap["_hide"])        # ya no se ocultan
    # `text` (normalize lo vuelve titulo de la escena) sigue ocultandolos, como toda escena con texto
    assert any(abs(a - 3.0) < 0.05 for a, b in cap["_hide"])
    assert R.photo_caption_y({"y": 0.3}, 0.72) is None
    assert R._photo_caption_text({"text": "pie sin normalizar"}) == "pie sin normalizar"
    job["cutaways"][0]["_media"]["failed"] = "x"
    R.drop_failed_media(job)
    assert not [r for r in job["captions"]["y_ranges"] if r.get("_auto")]
    # en el cuadro: los subtitulos salen a la altura nueva (diferencia con/sin subtitulos)
    words = [[{"text": "HOLA", "start": 1.05, "end": 1.9}]]

    def rows_diff(keep):
        f = dict(fx, cutaways=[dict(fx["cutaways"][0], keep_captions=keep, background="color",
                                    bg_colors=["#303030"], tilt=0)])
        a = R.render_frame(dict(_plan(src, f), phrases=words), 1.6, _opts(tmp_path))
        f2 = dict(f, captions={"enabled": False})
        b = R.render_frame(dict(_plan(src, f2), phrases=words), 1.6, _opts(tmp_path))
        d = np.abs(a[..., :3].astype(int) - b[..., :3].astype(int)).sum(axis=(1, 2))
        ys = np.where(d > 0)[0]
        assert len(ys), keep
        return (ys.min() + ys.max()) / 2 / H
    assert abs(rows_diff(False) - auto[0]["y"]) < 0.05
    assert abs(rows_diff(True) - 0.72) < 0.05
