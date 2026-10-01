"""Recursos flotantes del guion (pista fx.media, estilo float_card; motion/media.py + render.py).

Geometria y animacion puras (tarjeta centrada y simetrica al pixel, arriba de la franja de
subtitulos, 4:5 o mas vertical si cabe), esquema de la pista "Recursos", y render real medido
por pixeles: la tarjeta aparece solo en su ventana, centrada (margenes iguales +-1 px), con
esquinas redondeadas, fondo desenfocado y oscurecido, subtitulos libres; clips cuadro exacto con
su velocidad (speed / trim / hold) y SIN su audio.

Todo con medios SINTETICOS (sin datos del cliente). Correr:
  cd spike && .venv/bin/python -m pytest tests/test_media_float.py -q
"""
import copy
import subprocess
from pathlib import Path

import numpy as np
import pytest

from motion import ctx as C
from motion import media as MF
from motion import render as R
from motion import schema as S
from motion import video_io as V

W, H = 216, 384
MAGENTA = (255, 0, 255)
# clip del usuario: un color plano por bloque de BLOCK cuadros (tiempo local comprobable)
COLS = [(230, 20, 20), (20, 200, 40), (30, 60, 230), (240, 220, 30), (20, 220, 220), (220, 30, 220),
        (250, 250, 250), (40, 40, 40), (250, 140, 20), (120, 40, 200), (120, 200, 120), (200, 120, 120),
        (20, 120, 160), (160, 160, 20), (90, 30, 30), (30, 90, 30)]
BLOCK = 6                                    # 0.2 s a 30 fps


# ------------------------------------------------------------------ medios sinteticos
def _src(path: Path, dur=6.0, size=f"{W}x{H}", tone=330) -> Path:
    """Fuente del plan: tablero gris de 8 px (mucho detalle: se nota el desenfoque) + tono."""
    if not path.exists():
        subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"nullsrc=s={size}:r=30:d={dur},format=yuv420p,"
                        "geq=lum='if(mod(floor(X/8)+floor(Y/8)\\,2)\\,200\\,50)':cb=128:cr=128",
                        "-f", "lavfi", "-i", f"sine=f={tone}:r=48000:d={dur}", "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                        str(path)], check=True)
    return path


def _png(path: Path, rgb=MAGENTA, size=(1080, 1350)) -> Path:
    from PIL import Image
    Image.new("RGB", size, rgb).save(path)
    return path


def _clip(path: Path, w=320, h=400, fps=30, tone=1000) -> Path:
    """Clip del usuario (COLS por bloque de BLOCK cuadros) CON audio fuerte a `tone` Hz: el
    recurso nunca debe sonar."""
    if not path.exists():
        tmp = path.with_name(path.stem + "_v.mp4")
        wr = V.ChunkWriter(tmp, w, h, fps, "inter")
        for c in COLS:
            fr = np.zeros((h, w, 4), np.uint8)
            fr[..., :3] = c
            fr[..., 3] = 255
            for _ in range(BLOCK):
                wr.write(fr)
        wr.close()
        dur = len(COLS) * BLOCK / fps
        subprocess.run([V.FFMPEG, "-y", "-v", "error", "-i", str(tmp), "-f", "lavfi", "-i",
                        f"sine=f={tone}:r=48000:d={dur}", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                        "-c:a", "aac", "-shortest", str(path)], check=True)
        tmp.unlink(missing_ok=True)
    return path


def _words(t0=0.3, n=16, step=0.33):
    txt = "uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis"
    return [{"text": w, "start": round(t0 + i * step, 3), "end": round(t0 + i * step + 0.25, 3)}
            for i, w in enumerate(txt.split()[:n])]


def _plan(video, media=(), captions=False, dur=6.0):
    ws = _words()
    return {"video": str(video), "duration": dur, "cuts": [], "version": 2,
            "phrases": [ws[i:i + 3] for i in range(0, len(ws), 3)] if captions else [],
            "fx": {"captions": {"enabled": bool(captions)}, "grade": {"enabled": False},
                   "music": {"enabled": False, "source": "none"}, "media": list(media)}}


def _item(sid, start=1.0, end=4.0, **kw):
    it = {"id": f"md_{sid}", "start": start, "end": end, "sid": sid, "path": None, "kind": None,
          "fit": "hold", "speed": 1.0, "src_in": 0.0, "style": "float_card", "enabled": True,
          "locked": True, "src": "script", "name": sid}
    it.update(kw)
    return it


def _opts(tmp_path, **kw):
    o = {"W": W, "H": H, "dither": 0.0, "cache_dir": str(tmp_path / "cache"),
         "media_root": str(tmp_path / "media"), "enhance_audio": False}
    o.update(kw)
    return o


def _close(px, rgb, tol=40):
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(px[:3], rgb))


def _mask(fr, rgb=MAGENTA, tol=40):
    return (np.abs(fr[..., :3].astype(int) - np.array(rgb)) <= tol).all(axis=2)


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return _src(tmp_path_factory.mktemp("fsrc") / "fuente.mp4")


@pytest.fixture(scope="module")
def media_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("fmedia")
    _png(d / "foto01.png")
    _png(d / "vertical01.png", (20, 200, 40), (720, 1280))
    _clip(d / "clip01.mp4")
    return d


def _o(tmp_path, media_dir, **kw):
    return _opts(tmp_path, media_root=str(media_dir), **kw)


# ================================================================== geometria y animacion (puras)
def test_tarjeta_centrada_y_arriba_de_los_subtitulos():
    ba = 9 / 16
    top = MF.caption_top({"y": 0.72, "preset": "clean_bold", "size": 0.052}, True, 0, 1, ba)
    assert abs(top - (0.72 - MF.CAPTION_HALF - MF.CAP_GAP)) < 1e-6
    for sw, sh in ((1080, 1350), (1920, 1080), (1080, 1080), (2160, 3840), (None, None)):
        c = MF.card_layout(sw, sh, ba, top)
        assert c["cx"] == 0.5 and c["w"] <= C.SYM_W + 1e-9
        assert c["cy"] - c["h"] / 2 >= C.SAFE["top"] + MF.MARGIN - 1e-6
        assert c["cy"] + c["h"] / 2 <= top - MF.MARGIN + 1e-6, (sw, sh, c)
        assert abs(c["w"] * ba / c["aspect"] - c["h"]) < 1e-4
    # 4:5 por defecto (fuentes 4:5 o mas anchas), sin subtitulos una fuente vertical va mas vertical
    assert MF.card_layout(1920, 1080, ba, top)["aspect"] == pytest.approx(0.8)
    tall = MF.card_layout(2160, 3840, ba, None)
    assert MF.ASPECT_TALL - 1e-6 <= tall["aspect"] < 0.8 and tall["w"] == pytest.approx(C.SYM_W)
    # subtitulos muy arriba: la tarjeta se vuelve menos vertical y luego mas angosta, nunca los tapa
    hi = MF.caption_top({"y": 0.5}, True, box_aspect=ba)
    c = MF.card_layout(1080, 1350, ba, hi)
    assert c["cy"] + c["h"] / 2 <= hi - MF.MARGIN + 1e-6 and c["cx"] == 0.5
    assert c["aspect"] > 0.8 or c["w"] < C.SYM_W
    # manual: ancho, proporcion y centro vertical acotados; x nunca se mueve
    m = MF.card_layout(1080, 1350, ba, top, width=0.6, aspect=1.0, y=0.3)
    assert m["w"] == pytest.approx(0.6) and m["aspect"] == pytest.approx(1.0) and m["cx"] == 0.5
    assert m["cy"] == pytest.approx(0.3)
    # tramos de altura de los subtitulos (y_ranges) durante el recurso cuentan; fuera, no
    cap = {"y": 0.72, "y_ranges": [{"start": 2, "end": 3, "y": 0.6}, {"start": 9, "end": 10, "y": 0.4},
                                   {"start": 0, "end": 99, "y": 0.2, "hide": True}]}
    assert MF.caption_top(cap, True, 1.0, 4.0, ba) == pytest.approx(0.6 - 0.06)
    assert MF.caption_top(cap, False) is None and MF.caption_top({"preset": "none"}) is None
    assert MF.caption_top({"enabled": False}) is None
    # bold_caps grande (2 lineas): la franja crece
    big = MF.caption_top({"y": 0.72, "preset": "bold_caps", "size": 0.1}, True, box_aspect=ba)
    assert big < 0.72 - MF.CAPTION_HALF - MF.CAP_GAP - 0.02


def test_presets_de_subtitulos_espejo_de_text():
    from motion import text as TX
    for name, (scale, lines) in MF.CAPTION_PRESET_LINES.items():
        P = TX.CAPTION_PRESETS[name]
        assert (P["scale"], P["max_lines"]) == (scale, lines), name
    assert set(MF.CAPTION_PRESET_LINES) == set(TX.CAPTION_PRESETS)


@pytest.mark.parametrize("bw", [216, 217, 540, 607, 608, 1080, 1081])
def test_card_px_simetrica_al_pixel(bw):
    bh = bw * 16 / 9
    c = MF.card_layout(1080, 1350, bw / bh, 0.66)
    x0, y0, w, h = MF.card_px(c, bw, bh)
    assert x0 == round(bw) - (x0 + w)                           # mismo margen a cada lado
    assert abs(w - c["w"] * bw) <= 1.0 and abs(h - w / c["aspect"]) <= 0.5
    assert y0 >= 0 and y0 + h <= bh * 0.66 + 1


def test_animacion_entrada_flotacion_y_salida():
    st0 = MF.card_anim(0.0, 3.0)
    assert st0["alpha"] == 0 and st0["scale"] == pytest.approx(MF.POP_FROM) and st0["tilt"] == pytest.approx(MF.TILT)
    assert st0["bg"] == 0 and st0["dy"] == pytest.approx(MF.RISE)
    mid = [MF.card_anim(t, 3.0) for t in np.linspace(0.8, 2.6, 40)]
    for st in mid:                       # en reposo: flota y se mece sin pasar de su amplitud
        assert st["alpha"] == pytest.approx(1.0) and st["bg"] == pytest.approx(1.0)
        assert abs(st["scale"] - 1) <= MF.BREATH + 1e-6 and abs(st["dy"]) <= MF.FLOAT_AMP + 1e-6
        assert abs(st["tilt"] - st["sway"]) < 0.6 and abs(st["sway"]) <= MF.SWAY + 1e-9
    assert max(st["dy"] for st in mid) - min(st["dy"] for st in mid) > MF.FLOAT_AMP    # si flota
    sw = [MF.card_anim(t, 8.0)["sway"] for t in np.linspace(0.8, 7.0, 120)]
    assert max(sw) > 0.9 * MF.SWAY and min(sw) < -0.9 * MF.SWAY                          # y se mece
    # el resorte rebasa un poco (pop) antes de asentarse
    assert max(MF.card_anim(t, 3.0)["scale"] for t in np.linspace(0.1, 0.6, 60)) > 1.0
    end = MF.card_anim(3.0, 3.0)
    assert end["alpha"] == 0 and end["bg"] == 0 and end["scale"] < 0.96
    # items cortos: entrada y salida caben
    s = MF.card_anim(0.5, 1.0)
    assert 0 < s["alpha"] <= 1
    # sin flotacion: quieto (ni flota, ni se mece, ni respira)
    q = [MF.card_anim(t, 3.0, float_amt=0.0) for t in (1.0, 1.7, 2.3)]
    assert all(abs(x["dy"]) < 1e-4 and abs(x["scale"] - 1) < 1e-4 and abs(x["sway"]) < 1e-9 for x in q)


def test_tiempo_del_clip_speed_trim_hold():
    it = {"fit": "speed", "speed": 1.4, "src_in": 0.5}
    assert MF.speed_of(it) == 1.4 and MF.src_time(it, 1.0) == pytest.approx(1.9)
    assert MF.speed_of({"fit": "trim", "speed": 1.4}) == 1.0
    assert MF.src_time({"fit": "trim", "src_in": 2.0}, 1.5) == pytest.approx(3.5)
    # hold: 1x y se congela en el ultimo cuadro del clip
    assert MF.src_time({"fit": "hold"}, 9.0, src_dur=3.0, fps=30) == pytest.approx(3.0 - 1 / 30)
    assert MF.speed_of({"fit": "speed", "speed": 99}) == 4.0 and MF.speed_of(None) == 1.0
    # Ken Burns: arranca un poco acercado (holgura del paralaje) y llega a 1 + kenburns; 0 = sin zoom
    assert MF.kenburns_zoom(0, 4) == pytest.approx(1 + MF.KB_LEAD * MF.KENBURNS)
    assert MF.kenburns_zoom(4, 4) == pytest.approx(1 + MF.KENBURNS) and MF.kenburns_zoom(2, 4, 0.0) == 1.0
    x, y, w, h = MF.cover_rect(1920, 1080, 800, 1000)
    assert h == 1080 and w == pytest.approx(864) and x == pytest.approx((1920 - 864) / 2) and y == 0
    q = MF.tilt_quad(100, 125, 14.0)
    assert q[0][0] == pytest.approx(-q[1][0]) and q[3][0] == pytest.approx(-q[2][0])     # simetrica
    assert q[1][0] - q[0][0] < q[2][0] - q[3][0]                 # arriba mas angosta (se aleja)


# ================================================================== esquema: pista "Recursos"
def test_pista_recursos_en_el_esquema():
    tr = S.schema()["tracks"]["media"]
    assert tr["label"] == "Recursos" and tr["key"] == "style" and tr["common"] == "range"
    assert set(tr["items"]) == set(MF.STYLES)
    ps = {p["k"]: p for p in tr["items"]["float_card"]["params"]}
    assert {"sid", "path", "kind", "name", "fit", "speed", "src_in"} <= set(ps)
    assert all(ps[k].get("internal") for k in ("sid", "path", "kind", "name"))
    assert ps["fit"]["opts"] == list(MF.FITS)
    for k, v in MF.DEFAULTS.items():                             # defaults espejo del renderer
        assert ps[k]["default"] == pytest.approx(v) if not isinstance(v, bool) else ps[k]["default"] is v, k
    assert ps["width"]["max"] == pytest.approx(C.SYM_W)          # nunca mas ancha que la banda segura
    # brillo e irisado con control en el Studio: 'Brillo' vacio = automatico (imagenes 1, clips 0)
    assert set(MF.LIVE_DEFAULTS) <= set(ps)
    assert ps["sheen"]["default"] is None and ps["sheen"]["nullable"] and (ps["sheen"]["min"], ps["sheen"]["max"]) == (0, 1)
    assert ps["holo"]["default"] == pytest.approx(MF.LIVE_DEFAULTS["holo"]) and (ps["holo"]["min"], ps["holo"]["max"]) == (0, 1)
    assert MF.sheen_amount({"kind": "image", "sheen": None}) == MF.LIVE_DEFAULTS["sheen"] == 1.0


def test_brillo_e_irisado_se_validan_en_el_plan():
    """normalize acota 'sheen' y 'holo' a 0..1 (lo que el Studio escribe llega sano al render), deja
    'sheen' vacio como automatico y no agrega campos a un recurso que no los trae."""
    p = {"video": "x.mp4", "duration": 10.0, "fx": {"media": [
        _item("foto01", 1, 3, sheen=0, holo=0.2), _item("foto02", 3, 5, sheen=7, holo=-3),
        _item("clip01", 5, 7, kind="video", sheen=None), _item("foto03", 7, 9),
        _item("foto04", 9, 10, sheen="x", holo="y")]}}
    m = S.normalize(p)["fx"]["media"]
    assert (m[0]["sheen"], m[0]["holo"]) == (0.0, 0.2)
    assert (m[1]["sheen"], m[1]["holo"]) == (1.0, 0.0)
    assert m[2]["sheen"] is None and MF.sheen_amount(m[2]) == 0.0          # clip: automatico = sin luz
    assert "sheen" not in m[3] and "holo" not in m[3]
    assert m[4]["sheen"] is None and m[4]["holo"] == pytest.approx(MF.LIVE_DEFAULTS["holo"])
    assert S.validate(S.normalize(p)) == []
    bad = copy.deepcopy(S.normalize(p))
    bad["fx"]["media"][0]["holo"] = 3
    assert S.validate(bad)                                                    # fuera de rango: se avisa


def test_normalize_y_validate_de_recursos():
    p = {"video": "x.mp4", "duration": 10.0, "fx": {"media": [
        _item("clip01", 1, 3, kind="video", fit="speed", speed=9.0, path="/tmp/x.mp4"),
        {"start": 4, "end": 5, "sid": "../../etc/passwd", "kind": "otro", "src": "raro"},
        {"start": 6, "end": 7, "sid": "a", "style": "no_existe"},
        {"start": "x", "end": 7, "sid": "a"}]}}
    n = S.normalize(p)
    m = n["fx"]["media"]
    assert len(m) == 2
    assert m[0]["src"] == "script" and m[0]["speed"] == 4.0 and m[0]["style"] == "float_card"
    assert m[1]["sid"] is None and m[1]["kind"] is None and m[1]["src"] == "ai" and m[1]["id"].startswith("md_")
    assert S.validate(n) == [] and S.normalize(n) == n            # valido e idempotente
    # plan sin recursos: la pista queda vacia y lo demas igual
    q = S.normalize({"video": "x.mp4", "duration": 5.0, "fx": {"text": [
        {"preset": "title_pop", "text": "Hola", "start": 1, "end": 2}]}})
    assert q["fx"]["media"] == [] and len(q["fx"]["text"]) == 1
    assert "media" not in S.DEFAULTS["fx"]                         # el contrato base no cambia


# ================================================================== render por pixeles
def test_tarjeta_de_imagen_ventana_centrado_esquinas_y_fondo(src, tmp_path, media_dir):
    o = _o(tmp_path, media_dir)
    # sin la franja de luz (a media pasada en este cuadro; la luz se mide en test_media_float_vida)
    p = _plan(src, [_item("foto01", 1.0, 4.0, sheen=0)])
    job = R.prepare(p, o)["job"]
    assert len(job["media"]) == 1 and job["media"][0]["_card"]["cx"] == 0.5
    x0, y0, w, h = MF.card_px(job["media"][0]["_card"], W, H)
    fr = R.render_frame(p, 2.5, o)
    m = _mask(fr)
    rows, cols = np.where(m)
    assert len(rows) > 0.8 * w * h                               # la tarjeta se ve (casi) completa
    mid = (y0 + h // 2)
    xs = np.where(m[mid])[0]
    left, right = int(xs.min()), W - 1 - int(xs.max())
    assert abs(left - right) <= 1, (left, right)                 # simetrica al pixel
    assert abs(left - x0) <= 2
    # esquinas redondeadas: el vertice de la caja NO es tarjeta; a un radio hacia adentro, si
    top, bot = int(rows.min()), int(rows.max())
    r = MF.RADIUS * w
    assert not m[top + 1, left + 1] and not m[bot - 1, W - 1 - right - 1]
    assert m[top + int(r) + 2, left + 2] and m[top + 2, left + int(r) + 2]
    # fondo: desenfocado y oscurecido (a los lados de la tarjeta) vs el mismo cuadro sin recurso
    plain = R.render_frame(_plan(src), 2.5, o)
    side = (slice(y0 + 10, y0 + h - 10), slice(2, max(4, x0 - 8)))

    def lapvar(a):
        g = a[..., :3].astype(np.float32).mean(axis=2)
        lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
        return float(lap.var())
    assert lapvar(fr[side]) < 0.2 * lapvar(plain[side])
    assert fr[side][..., :3].mean() < plain[side][..., :3].mean() * 0.9
    # solo en su ventana (y el fondo vuelve a ser nitido)
    for t in (0.5, 4.5):
        f2 = R.render_frame(p, t, o)
        assert _mask(f2).sum() == 0, t
        assert lapvar(f2[side]) > 0.8 * lapvar(plain[side])
    # a media entrada: parcialmente visible (se aclara)
    fe = R.render_frame(p, 1.0 + 2 / 30, o)
    assert _mask(fe).sum() < 0.5 * len(rows)


def test_subtitulos_libres_y_encima(src, tmp_path, media_dir):
    o = _o(tmp_path, media_dir)
    p = _plan(src, [_item("foto01", 0.5, 5.0)], captions=True)
    job = R.prepare(p, o)["job"]
    card = job["media"][0]["_card"]
    top = MF.caption_top(job["captions"], True, 0.5, 5.0, 9 / 16)
    x0, y0, w, h = MF.card_px(card, W, H)
    assert y0 + h <= top * H                                    # la tarjeta termina arriba
    fr = R.render_frame(p, 2.0, o)
    m = _mask(fr)
    assert np.where(m.any(axis=1))[0].max() <= top * H + 1
    # los subtitulos se ven (pixeles casi blancos o del acento en su franja)
    cy = int(0.72 * H)
    band = fr[cy - 14:cy + 14, :, :3].astype(int)
    bright = ((band > 215).all(axis=2)) | ((band[..., 0] > 220) & (band[..., 1] > 180) & (band[..., 2] < 110))
    assert bright.sum() > 30
    # con una tarjeta a mano (y bajo) igual acotada arriba de la franja
    p2 = _plan(src, [_item("foto01", 0.5, 5.0, y=0.85)], captions=True)
    c2 = R.prepare(p2, o)["job"]["media"][0]["_card"]
    assert c2["cy"] + c2["h"] / 2 <= top - MF.MARGIN + 1e-6


def test_imagen_vertical_sin_subtitulos_es_mas_vertical(src, tmp_path, media_dir):
    o = _o(tmp_path, media_dir)
    c = R.prepare(_plan(src, [_item("vertical01")]), o)["job"]["media"][0]["_card"]
    assert c["aspect"] < 0.8 and c["cx"] == 0.5
    fr = R.render_frame(_plan(src, [_item("vertical01")]), 2.5, o)
    m = _mask(fr, (20, 200, 40), 45)
    ys, xs = np.where(m)
    assert (ys.max() - ys.min()) / (xs.max() - xs.min()) > 1.3


def _speed_color(lt, it, fps=30):
    """Color esperado del clip en el tiempo local lt (cuadro de salida k = floor(lt * fps))."""
    k = int(np.floor(lt * fps + 1e-6))
    j = int(round(float(it.get("src_in", 0.0)) * fps + k * MF.speed_of(it)))
    return COLS[min(len(COLS) - 1, j // BLOCK)], j


def _center(fr, card):
    x0, y0, w, h = MF.card_px(card, W, H)
    return fr[y0 + h // 2, x0 + w // 2]


def test_clip_cuadro_exacto_con_velocidad_y_sin_audio(src, tmp_path, media_dir):
    """Export: clip acelerado (speed 1.4) y otro recortado desde src_in (trim) siguen el tiempo
    local cuadro a cuadro; el audio del recurso (1 kHz) no aparece en la mezcla."""
    items = [_item("clip01", 0.5, 2.5, kind="video", fit="speed", speed=1.4),
             dict(_item("clip01", 3.0, 5.5, kind="video", fit="trim", src_in=0.8), id="md_trim")]
    p = _plan(src, items)
    o = _o(tmp_path, media_dir, workers=2)
    out = tmp_path / "rec.mp4"
    st = R.render_pro(p, out, o)
    assert st["errors"] == {} and not [s for s in st["skipped"] if s.startswith("media:")]
    assert st["items"]["media"] == 2 and st["media"]["items"] == 2
    job = R.prepare(p, o)["job"]
    cards = {it["id"]: it["_card"] for it in job["media"]}
    with V.FrameReader(out, W, H, 0, None, 30) as r:
        frames = [f for f in r]
    checked = 0
    for it in items:
        for k in range(int(it["start"] * 30) + 12, int(it["end"] * 30) - 9):
            lt = k / 30 - it["start"]
            rgb, j = _speed_color(lt, it)
            if j % BLOCK in (0, BLOCK - 1):
                continue                               # borde de bloque: +-1 cuadro de redondeo
            px = _center(frames[k], cards[it["id"]])
            assert _close(px, rgb, 45), (it["id"], k, lt, j, px, rgb)
            checked += 1
    assert checked > 60
    # fuera de las ventanas no hay tarjeta
    assert not _mask(frames[int(2.75 * 30)], COLS[0], 30).any()
    # audio: el tono de la fuente (330 Hz) si, el del recurso (1 kHz) no
    a = V.read_audio(out, 48000, 1)[:, 0]
    spec = np.abs(np.fft.rfft(a[int(0.6 * 48000):int(5.4 * 48000)]))
    f = np.fft.rfftfreq(int(5.4 * 48000) - int(0.6 * 48000), 1 / 48000)
    e330 = spec[(f > 320) & (f < 340)].max()
    e1k = spec[(f > 990) & (f < 1010)].max()
    assert e330 > 0 and e1k < 0.01 * e330, (e330, e1k)


def test_clip_en_frame_sin_cache_y_congelado(src, tmp_path, media_dir):
    """/frame sin el clip normalizado (cuadro suelto de la fuente) = mismo cuadro exacto; un clip
    mas corto que su hueco (hold) se congela en su ultimo cuadro."""
    n_src = len(COLS) * BLOCK / 30                                # 3.2 s
    it = _item("clip01", 0.5, 5.9, kind="video", fit="hold")
    p = _plan(src, [it])
    o = _o(tmp_path, media_dir, media_bg=False)
    card = R.prepare(p, o)["job"]["media"][0]["_card"]
    for lt in (0.35, 1.55, 2.75):
        rgb, _j = _speed_color(lt, it)
        assert _close(_center(R.render_frame(p, it["start"] + lt, o), card), rgb, 45), lt
    fr = R.render_frame(p, it["start"] + n_src + 1.0, o)            # pasado el final del clip
    assert _close(_center(fr, card), COLS[-1], 45)
    sp = _item("clip01", 0.5, 3.0, kind="video", fit="speed", speed=1.25, src_in=0.4)
    p2 = _plan(src, [sp])
    c2 = R.prepare(p2, o)["job"]["media"][0]["_card"]
    for lt in (0.45, 1.25, 1.95):
        rgb, _j = _speed_color(lt, sp)
        assert _close(_center(R.render_frame(p2, sp["start"] + lt, o), c2), rgb, 45), lt


def test_archivo_faltante_ruta_y_formatos(src, tmp_path, media_dir):
    o = _o(tmp_path, media_dir)
    pr = R.prepare(_plan(src, [_item("no_esta"), dict(_item(None), id="md_sin")]), o)
    assert {"media:md_no_esta:no_encontrado", "media:md_sin:no_encontrado"} <= set(pr["skipped"])
    assert pr["job"]["media"] == []
    # `path` solo dentro de un media_root (nunca una ruta arbitraria del plan)
    inside = dict(_item(None), id="md_p", path=str(media_dir / "foto01.png"))
    assert len(R.prepare(_plan(src, [inside]), o)["job"]["media"]) == 1
    outside = tmp_path / "fuera.png"
    _png(outside)
    far = dict(_item(None), id="md_f", path=str(outside))
    assert R.prepare(_plan(src, [far]), o)["job"]["media"] == []
    # deshabilitado: nada
    assert R.prepare(_plan(src, [_item("foto01", enabled=False)]), o)["job"]["media"] == []
    # 16:9: la tarjeta va en la caja de layout centrada (margenes iguales en el cuadro completo)
    o2 = _o(tmp_path, media_dir, W=384, H=216)
    fr = R.render_frame(_plan(src, [_item("foto01")]), 2.5, o2)
    ys, xs = np.where(_mask(fr))
    assert len(xs) and abs(int(xs.min()) - (384 - 1 - int(xs.max()))) <= 1


def test_sin_recursos_el_cuadro_no_cambia(src, tmp_path, media_dir):
    o = _o(tmp_path, media_dir)
    a = R.render_frame(_plan(src), 2.0, o)
    p = _plan(src)
    p["fx"].pop("media")
    b = R.render_frame(p, 2.0, o)
    assert np.array_equal(a, b)
    pr = R.prepare(p, o)
    assert pr["job"]["media"] == [] and R.media_signature(pr["job"]) is None
    sig = R.media_signature(R.prepare(_plan(src, [_item("foto01")]), o)["job"])
    assert sig is not None
    assert R.media_signature(R.prepare(_plan(src, [_item("foto01", width=0.6)]), o)["job"]) != sig


def test_archivo_ilegible_marcador_en_frame_y_fuera_del_export(src, tmp_path, media_dir):
    """Un recurso que no se puede leer: /frame dibuja la tarjeta con un marcador (se ve en la
    edicion) y el export lo omite con aviso, sin tumbar el render ni desenfocar el fondo."""
    d = tmp_path / "rotos"
    d.mkdir()
    (d / "roto01.png").write_bytes(b"no es una imagen")
    o = _opts(tmp_path, media_root=str(d))
    p = _plan(src, [_item("roto01", 1.0, 3.0)])
    fr = R.render_frame(p, 2.0, o)
    card = R.prepare(p, o)["job"]["media"][0]["_card"]
    x0, y0, w, h = MF.card_px(card, W, H)
    px = fr[y0 + h // 4, x0 + w // 4, :3].astype(int)
    assert _close(px, (0x2A, 0x2A, 0x33), 12), px                # marcador gris oscuro
    st = R.render_pro(p, tmp_path / "roto.mp4", o)
    assert "media:md_roto01:ilegible" in st["skipped"] and st["media"]["dropped"] == ["md_roto01"]
    with V.FrameReader(tmp_path / "roto.mp4", W, H, 60, 1, 30) as r:
        f2 = r.read()
    plain = R.render_frame(_plan(src), 2.0, o)
    assert np.abs(f2[..., :3].astype(int) - plain[..., :3].astype(int)).mean() < 6


def test_vista_previa_real_draft(src, tmp_path, media_dir):
    """La vista previa real (/preview = render_draft, 540x960) dibuja la misma tarjeta: el clip
    normalizado del export se lee escalado y la imagen con su Ken Burns."""
    items = [_item("foto01", 0.5, 2.5), _item("clip01", 3.0, 5.0, kind="video", fit="speed", speed=1.2)]
    p = _plan(src, items)
    o = _o(tmp_path, media_dir, workers=1)
    o.pop("W")
    o.pop("H")
    st = R.render_draft(p, tmp_path / "d.mp4", o)
    assert st["errors"] == {} and st["items"]["media"] == 2 and (st["W"], st["H"]) == (540, 960)
    job = R.prepare(p, dict(o, quality="draft"))["job"]
    cards = [it["_card"] for it in job["media"]]
    with V.FrameReader(tmp_path / "d.mp4", 540, 960, 0, None, 30) as r:
        frames = [f for f in r]
    x0, y0, w, h = MF.card_px(cards[0], 540, 960)
    assert _close(frames[45][y0 + h // 2, x0 + w // 2], MAGENTA, 40)
    lt = 0.9                                   # j = 32: lejos del borde de bloque
    rgb, _j = _speed_color(lt, items[1])
    x0, y0, w, h = MF.card_px(cards[1], 540, 960)
    assert _close(frames[int((3.0 + lt) * 30)][y0 + h // 2, x0 + w // 2], rgb, 45)


def test_clip_girado_se_ve_derecho(src, tmp_path, media_dir):
    """Clip con rotacion de display (iPhone en vertical: codificado 320x180 + 90 grados): la
    tarjeta lo trata como VERTICAL (mas alta que 4:5 sin subtitulos) y el contenido sale girado
    (lo que en el archivo esta a izquierda/derecha queda arriba/abajo)."""
    d = tmp_path / "rot"
    d.mkdir()
    raw = d / "raw.mp4"
    wr = V.ChunkWriter(raw, 320, 180, 30, "inter")
    fr = np.zeros((180, 320, 4), np.uint8)
    fr[:, :160, :3] = (230, 20, 20)
    fr[:, 160:, :3] = (30, 60, 230)
    fr[..., 3] = 255
    for _ in range(60):
        wr.write(fr)
    wr.close()
    subprocess.run([V.FFMPEG, "-y", "-v", "error", "-display_rotation:v:0", "90", "-i", str(raw), "-c", "copy",
                    str(d / "girado01.mp4")], check=True)
    o = _opts(tmp_path, media_root=str(d))
    p = _plan(src, [_item("girado01", 0.5, 2.5, kind="video")])
    card = R.prepare(p, o)["job"]["media"][0]["_card"]
    assert card["aspect"] < 0.8
    x0, y0, w, h = MF.card_px(card, W, H)
    for f in (R.render_frame(p, 1.5, o), ):
        top, bot = f[y0 + h // 4, x0 + w // 2, :3], f[y0 + 3 * h // 4, x0 + w // 2, :3]
        left, right = f[y0 + h // 4, x0 + w // 4, :3], f[y0 + h // 4, x0 + 3 * w // 4, :3]
        assert {_close(top, (230, 20, 20), 50), _close(bot, (230, 20, 20), 50)} == {True, False}, (top, bot)
        assert _close(left, right, 30)


def test_imagen_con_transparencia_sobre_la_tarjeta_oscura(src, tmp_path, media_dir):
    """Un PNG con transparencia (un logo) se ve sobre el fondo OPACO de la tarjeta (#1C1C22, igual que
    la vista del Studio), no sobre la sombra ni el video desenfocado."""
    from PIL import Image
    im = Image.new("RGBA", (1080, 1350), (0, 0, 0, 0))
    im.paste((255, 0, 255, 255), (390, 525, 690, 825))            # logo magenta al centro
    im.save(media_dir / "logo_transparente.png")
    o = _o(tmp_path, media_dir)
    p = _plan(src, [_item("logo_transparente", 1.0, 4.0, kenburns=0.0, sheen=0)])     # sin franja de luz
    card = R.prepare(p, o)["job"]["media"][0]["_card"]
    x0, y0, w, h = MF.card_px(card, W, H)
    fr = R.render_frame(p, 2.5, o)
    bg = np.array([int(MF.CARD_BG[i:i + 2], 16) for i in (1, 3, 5)])
    # dentro de la tarjeta, lejos del logo y de las esquinas redondeadas: el fondo de la tarjeta
    for yy, xx in ((y0 + h // 6, x0 + w // 2), (y0 + h // 2, x0 + w // 8), (y0 + 5 * h // 6, x0 + 7 * w // 8)):
        px = fr[yy, xx, :3].astype(int)
        assert (np.abs(px - bg) <= 14).all(), (yy, xx, px)
    assert _close(fr[y0 + h // 2, x0 + w // 2], MAGENTA)                   # el logo se ve


def test_tarjeta_debajo_de_los_subtitulos_subidos(src, tmp_path, media_dir):
    """El usuario subio los subtitulos (y = 0.25): la tarjeta va DEBAJO, ancha (>= CARD_W_MIN) y
    simetrica, y los subtitulos siguen arriba sin taparse."""
    o = _o(tmp_path, media_dir)
    p = _plan(src, [_item("foto01", 0.5, 5.0)], captions=True)
    p["fx"]["captions"]["y"] = 0.25
    card = R.prepare(p, o)["job"]["media"][0]["_card"]
    b = MF.caption_bottom({"y": 0.25}, True, 0.5, 5.0, 9 / 16)
    assert card["w"] >= MF.CARD_W_MIN and card["cy"] - card["h"] / 2 >= b + MF.MARGIN - 1e-6
    fr = R.render_frame(p, 2.0, o)
    m = _mask(fr)
    rows, cols = np.where(m)
    assert rows.min() >= b * H - 1 and rows.max() <= C.SAFE["bottom"] * H + 1
    left, right = int(cols.min()), W - 1 - int(cols.max())
    assert abs(left - right) <= 1 and (cols.max() - cols.min() + 1) >= 0.4 * W - 2
