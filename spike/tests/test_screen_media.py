"""Grabacion de pantalla sincronizada en el render (fx.media estilo "screen": motion/screen.py +
render.py).

La pantalla SINTETICA lleva en cada cuadro su NUMERO (12 franjas blanco/negro = 12 bits, 60 fps): en
el video final se lee que cuadro de la pantalla se ve en cada cuadro final y se compara con la
sincronia esperada A TRAVES de un salto del montaje dentro de la toma y de dos recortes de silencio
del render (inicio, medio y final, cuadro exacto, sin deriva). Ademas: ventana centrada simetrica
arriba de los subtitulos con el look de la tarjeta flotante, el MARCO de un dispositivo emulado
recortado (nada de su color dentro de la ventana), muda (su audio nunca suena), /frame sin cache = el
mismo cuadro, y la geometria/animacion puras."""
import subprocess
from pathlib import Path

import numpy as np
import pytest

from motion import media as MF
from motion import render as R
from motion import screen as SC
from motion import timeline as T
from motion import video_io as V

W, H = 216, 384
SW, SH, SFPS, SDUR = 120, 258, 60, 40.0
BITS = 12
ACCENT = "#0B7A52"
# grabacion con MARCO (modo dispositivo de un navegador): la pantalla de SW x SH dentro de un marco
# lavanda fijo de FL/FR/FT/FB px
FL, FR, FT, FB = 10, 10, 14, 22
FW, FH = SW + FL + FR, SH + FT + FB
LAVANDA = (216, 220, 232)


def _bands():
    y0, step = 9, 20
    return [(y0 + i * step, y0 + i * step + step) for i in range(BITS)]


def _screen_clip(path: Path, frame: bool = False) -> Path:
    """Pantalla 120x258 a 60 fps: el cuadro n lleva n en binario (franjas de 20 px) + tono de 1 kHz
    (no debe sonar nunca). Sin perdida (qp 0): se lee exacto. `frame`: dentro de un marco lavanda fijo
    (FW x FH)."""
    if path.exists():
        return path
    n = int(SDUR * SFPS)
    w, h = (FW, FH) if frame else (SW, SH)
    frames = np.empty((n, h, w, 3), np.uint8)
    frames[:] = LAVANDA if frame else (128, 128, 128)
    ox, oy = (FL, FT) if frame else (0, 0)
    # el fondo de la pantalla cambia con el tiempo (como una app que se desplaza): sus orillas no son marco
    frames[:, oy:oy + SH, ox:ox + SW] = (100 + (np.arange(n) * 7) % 60)[:, None, None, None].astype(np.uint8)
    for k in range(n):
        for i, (a, b) in enumerate(_bands()):
            frames[k, oy + a:oy + b, ox + 8:ox + SW - 8] = 235 if (k >> i) & 1 else 20
    tmp = path.with_name("v_" + path.name)
    p = subprocess.Popen([V.FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                          "-r", str(SFPS), "-i", "-", "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast",
                          "-pix_fmt", "yuv444p", str(tmp)], stdin=subprocess.PIPE)
    p.stdin.write(frames.tobytes())
    p.stdin.close()
    assert p.wait() == 0
    subprocess.run([V.FFMPEG, "-y", "-v", "error", "-i", str(tmp), "-f", "lavfi", "-i",
                    f"sine=f=1000:r=48000:d={SDUR}", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-shortest", str(path)], check=True)
    tmp.unlink(missing_ok=True)
    return path


def _cam(path: Path, dur=20.0) -> Path:
    if not path.exists():
        subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"testsrc2=s={W}x{H}:r=30:d={dur}", "-f", "lavfi", "-i", f"sine=f=330:r=48000:d={dur}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                        str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("scr")
    _screen_clip(d / "pant01.mp4")
    _screen_clip(d / "pant02.mp4", frame=True)
    _cam(d / "cam.mp4")
    return d


# camara compuesta 20 s; la pantalla va 5 s adelantada a la toma; el montaje SALTA dentro de la toma
# en el s 10 del video compuesto (camara 9.0 -> 15.0); el render recorta [6.0, 7.5] y [12.0, 12.6]
PIECES = [[2.0, 10.0, 1.0], [10.0, 18.0, 15.0]]
CUTS = [{"start": 6.0, "end": 7.5, "enabled": True}, {"start": 12.0, "end": 12.6, "enabled": True}]


def _item(**kw):
    it = {"id": "sc01", "start": 2.0, "end": 18.0, "sid": "pant01", "path": None, "kind": "video",
          "style": "screen", "enabled": True, "locked": True, "src": "screen", "name": "pantalla.mov",
          "cam": "cam", "cam_name": "IMG_0002.MOV", "w": SW, "h": SH, "media_dur": SDUR,
          "sync": {"offset": 5.0, "rate": 1.0, "pieces": [list(p) for p in PIECES]}}
    it.update(kw)
    return it


def _plan(video, items, captions=True, style="minimal"):
    ws = [{"text": f"p{i}", "start": round(0.3 + i * 0.4, 3), "end": round(0.6 + i * 0.4, 3)} for i in range(45)]
    shots = [{"start": 0.0, "end": 20.0, "src": "cam.mp4", "face": [0.5, 0.35, 0.22, 0.14]}]
    return {"video": str(video), "duration": 20.0, "cuts": [dict(c) for c in CUTS], "version": 2, "shots": shots,
            "director": {"style": style},
            "phrases": [ws[i:i + 3] for i in range(0, len(ws), 3)] if captions else [],
            "fx": {"captions": {"enabled": bool(captions), "y": 0.72}, "grade": {"enabled": False},
                   "music": {"enabled": False, "source": "none"}, "media": list(items), "accent": ACCENT}}


def _opts(tmp_path, media, **kw):
    o = {"W": W, "H": H, "dither": 0.0, "cache_dir": str(tmp_path / "cache"), "media_root": str(media),
         "enhance_audio": False}
    o.update(kw)
    return o


def _decode(fr, card):
    """Numero del cuadro de pantalla que se ve en el cuadro final `fr` (o None si no se lee)."""
    x0, y0, w, h = MF.card_px(card, W, H)
    xs = x0 + w // 2
    val = 0
    for i, (a, b) in enumerate(_bands()):
        y = y0 + int(round((a + b) / 2 * h / SH))
        v = int(fr[y, xs - 2:xs + 3, :3].mean())
        if 70 < v < 170:
            return None
        val |= (1 << i) if v >= 170 else 0
    return val


def _expected(plan, it, k, fps=30):
    tl = T.Timeline.from_plan(plan, fps=fps)
    t = tl.inverse(k / fps)
    return SC.sync_time(it, t), t


def test_sincronia_cuadro_exacto_a_traves_de_cortes_y_saltos(media, tmp_path):
    it = _item()
    p = _plan(media / "cam.mp4", [it], captions=True)
    o = _opts(tmp_path, media, workers=2)
    out = tmp_path / "pant.mp4"
    st = R.render_pro(p, out, o)
    assert st["errors"] == {} and not [s for s in st["skipped"] if s.startswith("media:")], st["skipped"]
    job = R.prepare(p, o)["job"]
    jit = job["media"][0]
    card = jit["_card"]
    with V.FrameReader(out, W, H, 0, None, 30) as r:
        frames = [f for f in r]
    tl = T.Timeline.from_plan(p, fps=30)
    k0, k1 = int(round(jit["_fs"] * 30)), int(round(jit["_fe"] * 30))
    worst, checked, zones = 0.0, 0, set()
    for k in range(k0 + 12, k1 - 10):
        got = _decode(frames[k], card)
        exp, t = _expected(p, it, k)
        assert got is not None, (k, t)
        err = abs(got / SFPS - exp)
        worst = max(worst, err)
        # un cuadro de salida (1/30) + el de la pantalla (1/60): nunca mas
        assert err <= 1 / 30 + 1 / 60 + 1e-6, (k, t, got / SFPS, exp)
        checked += 1
        zones.add("antes" if t < 6.0 else "tras_corte1" if t < 10.0 else "tras_salto" if t < 12.0 else "final")
    assert zones == {"antes", "tras_corte1", "tras_salto", "final"} and checked > 250
    # el primer cuadro despues de cada corte y del salto muestra la pantalla del otro lado
    for t_orig in (7.5, 10.0, 12.6):
        kf = int(round(tl.remap(t_orig) * 30))
        got = _decode(frames[kf], card)
        assert abs(got / SFPS - SC.sync_time(it, t_orig)) <= 1 / 30 + 1 / 60, (t_orig, got)
    # muda: el tono de la camara (330 Hz) si, el de la pantalla (1 kHz) no
    a = V.read_audio(out, 48000, 1)[:, 0]
    seg = a[int(3.0 * 48000):int(9.0 * 48000)]
    spec = np.abs(np.fft.rfft(seg))
    f = np.fft.rfftfreq(len(seg), 1 / 48000)
    assert spec[(f > 990) & (f < 1010)].max() < 0.01 * spec[(f > 320) & (f < 340)].max()


def test_frame_sin_cache_mismo_cuadro(media, tmp_path):
    it = _item()
    p = _plan(media / "cam.mp4", [it])
    o = _opts(tmp_path, media, media_bg=False)
    card = R.prepare(p, o)["job"]["media"][0]["_card"]
    tl = T.Timeline.from_plan(p, fps=30)
    for t_orig in (3.0, 8.0, 10.2, 14.0, 17.0):
        tf = round(tl.remap(t_orig) * 30) / 30
        fr = R.render_frame(p, tf, o)
        got = _decode(fr, card)
        exp, _t = _expected(p, it, int(round(tf * 30)))
        assert got is not None and abs(got / SFPS - exp) <= 1 / 30 + 1 / 60, (t_orig, got, exp)


def test_ventana_simetrica_arriba_de_los_subtitulos_con_el_look_aprobado(media, tmp_path):
    it = _item()
    p = _plan(media / "cam.mp4", [it], captions=True)
    o = _opts(tmp_path, media, media_bg=False)
    job = R.prepare(p, o)["job"]
    jit = job["media"][0]
    card = jit["_card"]
    x0, y0, w, h = MF.card_px(card, W, H)
    assert x0 == W - (x0 + w)                                   # margenes identicos al pixel
    cap_top = MF.caption_top(p["fx"]["captions"], True, 2.0, 18.0, W / H)
    assert (y0 + h) / H <= cap_top + 1e-6                       # los subtitulos no tapan la pantalla
    assert y0 / H == pytest.approx(SC.TOP, abs=1.5 / H)         # lo mas grande: desde TOP
    assert card["aspect"] == pytest.approx(SW / SH, abs=1e-4)   # la grabacion completa, sin recortar
    assert "bubble" not in card
    # Minimal: aparece con el corte (fondo calmado y ventana en su lugar desde el primer cuadro)
    assert jit["_motion"] == "cut"
    fs = jit["_fs"]
    fr = R.render_frame(p, fs, o).astype(float)
    plain = R.render_frame(_plan(media / "cam.mp4", [], captions=True), fs, o).astype(float)
    # la ventana es la pantalla (gris uniforme a la orilla de las franjas), no la toma
    assert fr[y0 + h // 2, x0 + 3:x0 + 6, :3].std() < 12
    # el fondo es la toma desenfocada y oscurecida
    band_ = (slice(int(0.75 * H), int(0.95 * H)), slice(2, max(4, x0 - 2)))
    assert fr[band_][..., :3].std() < plain[band_][..., :3].std()
    assert fr[band_][..., :3].mean() < plain[band_][..., :3].mean()


def test_marco_de_dispositivo_emulado_recortado(media, tmp_path):
    """Una grabacion con el marco de un dispositivo emulado: compose trae su `crop` (screensync.
    content_rect) y la ventana muestra SOLO la pantalla: su proporcion es la de adentro, ningun pixel
    lavanda del marco cerca de la orilla de la ventana y el numero de cuadro se sigue leyendo exacto."""
    import screensync as SS
    rect = SS.content_rect(media / "pant02.mp4")
    assert rect is not None
    # el marco se detecta con su linea fina de orilla (se recorta de mas, nunca de menos)
    assert (rect["x"], rect["y"], rect["w"], rect["h"]) == (FL + SS.FRAME_INSET, FT + SS.FRAME_INSET,
                                                            SW - 2 * SS.FRAME_INSET, SH - 2 * SS.FRAME_INSET)
    crop = SS.crop_frac(rect)
    it = _item(sid="pant02", w=FW, h=FH, crop=crop)
    p = _plan(media / "cam.mp4", [it], captions=True)
    o = _opts(tmp_path, media, media_bg=False)
    jit = R.prepare(p, o)["job"]["media"][0]
    card = jit["_card"]
    assert card["aspect"] == pytest.approx(rect["w"] / rect["h"], abs=2e-3)
    x0, y0, w, h = MF.card_px(card, W, H)
    tl = T.Timeline.from_plan(p, fps=30)
    for t_orig in (3.0, 14.0):
        tf = round(tl.remap(t_orig) * 30) / 30
        fr = R.render_frame(p, tf, o).astype(int)
        # anillo interior de la ventana (sin las esquinas redondeadas ni el borde claro): nada lavanda
        r = max(3, int(0.06 * w))
        ring = np.concatenate([fr[y0 + r:y0 + h - r, x0 + 3, :3], fr[y0 + r:y0 + h - r, x0 + w - 4, :3],
                               fr[y0 + 3, x0 + r:x0 + w - r, :3], fr[y0 + h - 4, x0 + r:x0 + w - r, :3]])
        lav = np.abs(ring - np.array(LAVANDA)).sum(axis=1) < 40
        assert lav.mean() < 0.02, lav.mean()
        # la misma lectura de franjas, relativa a la pantalla recortada
        x_s = x0 + w // 2
        val = 0
        for i, (a, b) in enumerate(_bands()):
            yy = y0 + int(round(((a + b) / 2 + FT - rect["y"]) * h / rect["h"]))
            v = int(fr[yy, x_s - 2:x_s + 3, :3].mean())
            val |= (1 << i) if v >= 170 else 0
        exp, _t = _expected(p, it, int(round(tf * 30)))
        assert abs(val / SFPS - exp) <= 1 / 30 + 1 / 60, (t_orig, val, exp)


def test_ventana_acotada_a_la_sincronia(media, tmp_path):
    # el usuario estiro el elemento fuera de los pedazos: solo se ve donde hay sincronia
    p = _plan(media / "cam.mp4", [_item(start=0.0, end=20.0)])
    o = _opts(tmp_path, media, media_bg=False)
    j = R.prepare(p, o)["job"]["media"][0]
    tl = T.Timeline.from_plan(p, fps=30)
    assert j["_fs"] == pytest.approx(tl.remap(2.0), abs=1 / 30) and j["_fe"] == pytest.approx(tl.remap(18.0), abs=1 / 30)
    # sin pedazos: no se dibuja (aviso)
    p2 = _plan(media / "cam.mp4", [_item(sync={"offset": 1.0, "pieces": []})])
    pr = R.prepare(p2, o)
    assert pr["job"]["media"] == [] and any("sin_sincronia" in s for s in pr["skipped"])


# ================================================================== puras
def test_layout_simetrico_y_proporcion():
    for sw, sh in ((670, 1436), (1920, 1080), (1080, 1080), (None, None)):
        for crop in (None, [0.03, 0.02, 0.97, 0.97]):
            c = SC.layout(sw, sh, 9 / 16, 0.66, 0.79, None, crop)
            assert c["cx"] == 0.5 and c["w"] <= 0.76 + 1e-9 and "bubble" not in c
            for bw in (216, 540, 1080, 1081, 2160):
                x0, y0, w, h = MF.card_px(c, bw, bw * 16 / 9)
                assert x0 == round(bw) - (x0 + w)
            assert c["cy"] + c["h"] / 2 <= 0.66 - 0.015 + 1e-4      # arriba de los subtitulos (redondeo a 5 decimales)
            assert c["cy"] - c["h"] / 2 >= SC.TOP - 1e-4
    phone = SC.layout(670, 1436, 9 / 16, 0.66, 0.79)
    assert phone["aspect"] == pytest.approx(670 / 1436, abs=1e-4)
    # con el marco recortado, la proporcion es la de adentro
    cut = SC.layout(670, 1436, 9 / 16, 0.66, 0.79, None, [19 / 670, 26 / 1436, 651 / 670, 1398 / 1436])
    assert cut["aspect"] == pytest.approx(632 / 1372, abs=1e-4)
    # ancho manual acotado
    assert SC.layout(1920, 1080, 9 / 16, 0.66, 0.79, width=0.5)["w"] == pytest.approx(0.5)
    # subtitulos subidos (la banda de ABAJO): la ventana no sube a TOP, se queda bajo los subtitulos
    low = SC.layout(670, 1436, 9 / 16, 0.2, 0.3)
    assert low["cy"] - low["h"] / 2 >= 0.3 - 1e-4
    assert SC.crop_of({"crop": [0, 0, 1, 1]}) is None and SC.crop_of({"crop": "x"}) is None
    assert SC.crop_of({"crop": [-0.1, 0.1, 1.2, 0.9]}) == [0.0, 0.1, 1.0, 0.9]


def test_animacion_cut_calm_y_lively():
    c0, c5 = SC.anim(0.0, 10.0, "cut"), SC.anim(5.0, 10.0, "cut")
    assert (c0["alpha"], c0["scale"], c0["dy"], c0["bg"]) == (1.0, 1.0, 0.0, 1.0) and c0 == c5
    a0 = SC.anim(0.0, 10.0, "calm")
    a1 = SC.anim(1.0, 10.0, "calm")
    # opaca desde el primer cuadro (nada de la toma "fantasma" a traves de la pantalla): entra con escala
    assert a0["alpha"] == 1 and a0["scale"] == pytest.approx(SC.CALM_FROM) and a0["dy"] == pytest.approx(SC.CALM_RISE)
    assert a0["bg"] == 0 and SC.anim(0.25, 10.0, "calm")["bg"] == pytest.approx(1.0)
    assert a1["alpha"] == 1 and a1["scale"] == pytest.approx(1.0)
    assert a1["tilt"] == 0 and a1["dy"] == 0 and a1["bg"] == pytest.approx(1.0)
    assert all(SC.anim(t, 10.0, m)["alpha"] == 1 for t in np.arange(0, 10.01, 0.05) for m in SC.MOTIONS)
    # quieta en reposo (texto nitido): nada cambia entre 3 s y 7 s
    s3, s7 = SC.anim(3.0, 10.0, "calm"), SC.anim(7.0, 10.0, "calm")
    assert all(s3[k] == pytest.approx(s7[k]) for k in ("alpha", "scale", "dy"))
    # sin fundido de salida (corte seco con su toma): el fondo sigue desenfocado hasta el final
    for m in SC.MOTIONS:
        assert SC.anim(10.0, 10.0, m)["bg"] == pytest.approx(1.0) and SC.anim(9.99, 10.0, m)["scale"] > 0.98
    lv = [SC.anim(t, 10.0, "lively") for t in np.arange(0.5, 9.0, 0.25)]
    assert all(x["tilt"] == 0 for x in lv) and len({round(x["dy"], 6) for x in lv}) > 3    # flota, sin inclinarse
    assert SC.motion_of({}, "minimal") == "cut" and SC.motion_of({}, "elegante") == "calm"
    assert SC.motion_of({}, "dinamico") == "lively" and SC.motion_of({"motion": "lively"}, "minimal") == "lively"


def test_sync_time_y_coverage():
    it = _item()
    assert SC.sync_time(it, 2.0) == pytest.approx(6.0)
    assert SC.sync_time(it, 9.999) == pytest.approx(5.0 + 1.0 + 7.999)
    assert SC.sync_time(it, 10.0) == pytest.approx(20.0)          # en la union gana el pedazo siguiente
    assert SC.sync_time(it, 25.0) == pytest.approx(5.0 + 15.0 + 8.0)
    assert SC.coverage(it) == (2.0, 18.0)
    assert SC.coverage(_item(start=5.0, end=30.0)) == (5.0, 18.0)
    assert SC.sync_time({"sync": {}}, 1.0) is None


def test_director_conserva_la_pantalla_y_quita_la_camara_debajo():
    """La grabacion de pantalla es contenido bloqueado: el director la conserva tal cual (con su
    sincronia), no pone titulos/graficos/escenas/transiciones encima y tampoco movimientos de camara
    (la toma va desenfocada debajo: no se verian)."""
    import copy
    from motion import director as D
    from test_director_joins import montage_plan
    import test_director_media_inserts as TI
    base = montage_plan()
    a, _b = TI._line_span(base, 1)
    _a, b = TI._line_span(base, 5)
    scr = _item(start=round(a, 3), end=round(b, 3), sync={"offset": 3.0, "rate": 1.0, "pieces": [[round(a, 3), round(b, 3), 0.5]]})
    p = copy.deepcopy(base)
    p["fx"] = {"media": [scr]}
    out = D.direct(p, style="dinamico", intensity=0.95, seed=7, use_llm=False, trim=False, music_sync=False)
    got = out["fx"]["media"]
    assert len(got) == 1 and got[0]["sync"] == scr["sync"] and got[0]["style"] == "screen"
    fa, fb = TI._final(out, scr["start"]), TI._final(out, scr["end"])
    cam = [e for e in out["fx"]["effects"] if e.get("kind") in D.SCREEN_CAMERA_FX
           and TI._final(out, e["start"]) < fb - 1e-6 and fa < TI._final(out, e["end"]) - 1e-6]
    assert cam == [], cam
    for tr in ("text", "cutaways"):
        assert not [it for it in out["fx"][tr] if TI._final(out, it["start"]) < fb - 1e-6 and fa < TI._final(out, it["end"]) - 1e-6], tr
    assert not [t for t in out["fx"]["transitions"] if fa - 0.3 < TI._final(out, t["at"]) < fb + 0.3]
    # sin la pantalla el director si mueve la camara en ese tramo (la regla es por la pantalla)
    free = D.direct(copy.deepcopy(base), style="dinamico", intensity=0.95, seed=7, use_llm=False, trim=False, music_sync=False)
    assert [e for e in free["fx"]["effects"] if e.get("kind") in D.SCREEN_CAMERA_FX
            and TI._final(free, e["start"]) < fb and fa < TI._final(free, e["end"])]
