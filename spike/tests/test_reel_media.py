"""Videos de apoyo en fragmentos en el render (fx.media estilo "reel": motion/reel.py + render.py).

El video de apoyo SINTETICO (tests/_broll_media.py) lleva en cada cuadro su NUMERO (12 bits al centro
abajo): en el video final se lee que cuadro del archivo se ve en cada cuadro final y se compara con el
reparto de las tomas (reel.fit) en el tramo FINAL (con un recorte de silencio adentro): cortes secos al
cuadro, cada toma desde su segundo y nunca pasa de su `max`. Pantalla completa: el recorte vertical
centrado en `cx` cubre todo el cuadro (sin tarjeta ni fondo desenfocado); ventana: la tarjeta centrada
(simetrica al pixel) arriba de los subtitulos con la proporcion de la fuente. /frame sin cache = el mismo
cuadro. Los subtitulos miden su contraste en el cuadro YA compuesto (text.caption_contrast)."""
import subprocess
from pathlib import Path

import numpy as np
import pytest

from _broll_media import bit_cells, make_film
from motion import media as MF
from motion import reel as RL
from motion import render as R
from motion import text as TX
from motion import timeline as T
from motion import video_io as V

W, H = 216, 384
FW, FH, FPS = 640, 360, 30
SHOTS = [("screen", 3.0), ("character", 3.0), ("title", 3.0), ("screen", 3.0), ("character", 3.0),
         ("screen", 3.0), ("title", 3.0)]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("reel")
    make_film(d / "apoyo01.mp4", SHOTS, fps=FPS, size=(FW, FH))
    subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=30:d=20",
                    "-f", "lavfi", "-i", "sine=f=330:r=48000:d=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "cam.mp4")], check=True)
    # una toma oscura (una persona contra un fondo oscuro): bajo los subtitulos casi negro
    subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x141418:s={W}x{H}:r=30:d=20",
                    "-f", "lavfi", "-i", "sine=f=330:r=48000:d=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "cam_oscura.mp4")], check=True)
    return d


CUTS = [{"start": 4.0, "end": 4.6, "enabled": True}]       # silencio recortado DENTRO del primer fragmento


def _items():
    full = {"id": "br01", "start": 2.0, "end": 7.0, "sid": "apoyo01", "kind": "video", "style": "reel", "src": "broll",
            "layout": "full", "w": FW, "h": FH, "media_dur": 21.0, "enabled": True, "locked": True,
            "pieces": [{"shot": 0, "in": 0.1, "out": 1.9, "max": 2.9, "cx": 0.5},
                       {"shot": 3, "in": 9.1, "out": 10.7, "max": 11.9, "cx": 0.48},
                       {"shot": 5, "in": 15.1, "out": 16.7, "max": 17.9, "cx": 0.52}]}
    win = {"id": "br02", "start": 10.0, "end": 14.0, "sid": "apoyo01", "kind": "video", "style": "reel", "src": "broll",
           "layout": "window", "w": FW, "h": FH, "media_dur": 21.0, "enabled": True, "locked": True,
           "pieces": [{"shot": 1, "in": 3.1, "out": 5.1, "max": 5.9}, {"shot": 4, "in": 12.1, "out": 14.1, "max": 14.9}]}
    return [full, win]


def _plan(video, items, captions=True, style="minimal"):
    ws = [{"text": f"p{i}", "start": round(0.3 + i * 0.4, 3), "end": round(0.6 + i * 0.4, 3)} for i in range(45)]
    return {"video": str(video), "duration": 20.0, "cuts": [dict(c) for c in CUTS], "version": 2,
            "shots": [{"start": 0.0, "end": 20.0, "src": "cam.mp4", "face": [0.5, 0.35, 0.22, 0.14]}],
            "director": {"style": style},
            "phrases": [ws[i:i + 3] for i in range(0, len(ws), 3)] if captions else [],
            "fx": {"captions": {"enabled": bool(captions), "y": 0.72}, "grade": {"enabled": False},
                   "music": {"enabled": False, "source": "none"}, "media": list(items), "accent": "#08794D"}}


def _opts(tmp_path, media, **kw):
    o = {"W": W, "H": H, "dither": 0.0, "cache_dir": str(tmp_path / "cache"), "media_root": str(media),
         "enhance_audio": False}
    o.update(kw)
    return o


def _cells_out(jit, row_crop):
    """Celdas de los bits en el cuadro FINAL para la toma de fila `row_crop`."""
    lay = jit["_layout"]
    if lay == "full":
        x0, y0, bw, bh = 0, 0, W, H
        eff = R._reel_crop(row_crop, FW, FH, W, H) or [0.0, 0.0, 1.0, 1.0]
    else:
        x0, y0, bw, bh = MF.card_px(jit["_card"], W, H)
        eff = R._reel_crop(row_crop, FW, FH, bw, bh) or [0.0, 0.0, 1.0, 1.0]
    out = []
    for a, b, c, d in bit_cells(FW, FH):
        u0, u1 = (a / FW - eff[0]) / (eff[2] - eff[0]), (c / FW - eff[0]) / (eff[2] - eff[0])
        v0, v1 = (b / FH - eff[1]) / (eff[3] - eff[1]), (d / FH - eff[1]) / (eff[3] - eff[1])
        out.append((int(round(x0 + u0 * bw)), int(round(y0 + v0 * bh)), int(round(x0 + u1 * bw)), int(round(y0 + v1 * bh))))
    return out


def _read(fr, cells):
    val = 0
    for i, (x0, y0, x1, y1) in enumerate(cells):
        mx, my = max(1, (x1 - x0) // 4), max(1, (y1 - y0) // 4)
        v = float(fr[y0 + my:y1 - my, x0 + mx:x1 - mx, :3].mean())
        if 80 < v < 175:
            return None
        val |= (1 << i) if v >= 175 else 0
    return val


def test_cada_toma_a_su_tiempo_con_cortes_secos(media, tmp_path):
    p = _plan(media / "cam.mp4", _items(), captions=False)
    o = _opts(tmp_path, media, workers=2)
    out = tmp_path / "reel.mp4"
    st = R.render_pro(p, out, o)
    assert st["errors"] == {} and not [s for s in st["skipped"] if s.startswith("media:")], st["skipped"]
    job = R.prepare(p, o)["job"]
    with V.FrameReader(out, W, H, 0, None, 30) as r:
        frames = [f for f in r]
    for jit in job["media"]:
        rows = jit["_media"]["reel"]
        L = jit["_fe"] - jit["_fs"]
        assert rows[-1][1] == pytest.approx(L, abs=1 / 30 + 1e-6)
        k0, k1 = int(round(jit["_fs"] * 30)), int(round(jit["_fe"] * 30))
        checked = 0
        for k in range(k0 + 1, k1 - 1):
            lt = k / 30 - jit["_fs"]
            j = RL.piece_at(rows, lt)
            got = _read(frames[k], _cells_out(jit, rows[j][3]))
            exp = RL.src_time(rows, lt)
            assert got is not None, (jit["id"], k, lt)
            assert abs(got / FPS - exp) <= 1 / 30 + 1e-6, (jit["id"], k, lt, got / FPS, exp)
            # nunca pasa del final de su toma (`max`) a la siguiente
            pc = RL.pieces_of(jit)[rows[j][4]]
            assert got / FPS <= float(pc["max"]) + 1 / 30 + 1e-6
            checked += 1
        assert checked > 0.8 * (k1 - k0)
    # el primer fragmento perdio 0.6 s de silencio: sus tomas se acortaron en proporcion (cada una desde su
    # inicio: la primera sigue empezando en su segundo 0.1)
    full = next(j for j in job["media"] if j["id"] == "br01")
    assert full["_fe"] - full["_fs"] == pytest.approx(4.4, abs=1 / 30)
    assert [r[2] for r in full["_media"]["reel"]] == [0.1, 9.1, 15.1]
    # cortes secos: el cuadro final antes y despues de cada union muestra tomas distintas
    rows = full["_media"]["reel"]
    for r0, r1 in zip(rows, rows[1:]):
        kb = int(round((full["_fs"] + r1[0]) * 30))
        a = _read(frames[kb - 1], _cells_out(full, r0[3]))
        b = _read(frames[kb], _cells_out(full, r1[3]))
        assert a is not None and b is not None and abs(b / FPS - r1[2]) <= 1 / 30 + 1e-6 and abs(a / FPS - b / FPS) > 1.0


def test_pantalla_completa_cubre_y_ventana_simetrica_arriba_de_los_subtitulos(media, tmp_path):
    p = _plan(media / "cam.mp4", _items(), captions=True)
    o = _opts(tmp_path, media, media_bg=False)
    job = R.prepare(p, o)["job"]
    full = next(j for j in job["media"] if j["id"] == "br01")
    win = next(j for j in job["media"] if j["id"] == "br02")
    assert full["_card"] is None and full["_motion"] == "cut"
    # recorte vertical centrado en cx (alto completo)
    c = full["_media"]["reel"][1][3]
    assert c[1] == 0.0 and c[3] == 1.0 and (c[0] + c[2]) / 2 == pytest.approx(0.48, abs=1e-4)
    assert (c[2] - c[0]) * FW / FH == pytest.approx(W / H, abs=1e-3)
    # ventana: proporcion de la fuente, centrada al pixel y arriba de los subtitulos
    x0, y0, w, h = MF.card_px(win["_card"], W, H)
    assert x0 == W - (x0 + w) and win["_card"]["aspect"] == pytest.approx(16 / 9, abs=1e-3)
    cap_top = MF.caption_top(p["fx"]["captions"], True, 10.0, 14.0, W / H)
    assert (y0 + h) / H <= cap_top + 1e-3
    # en pantalla completa no hay tarjeta ni fondo desenfocado: el cuadro entero es la toma
    tl = T.Timeline.from_plan(p, fps=30)
    fr = R.render_frame(p, round(tl.remap(2.5) * 30) / 30, _opts(tmp_path, media, media_bg=False))
    eff = R._reel_crop(full["_media"]["reel"][0][3], FW, FH, W, H)
    assert eff is not None
    # el cuadro final (sin la franja de los subtitulos) ES el recorte de la toma escalado a todo el cuadro:
    # sin barras, sin tarjeta, sin fondo desenfocado
    lt = round(tl.remap(2.5) * 30) / 30 - full["_fs"]
    ts = RL.src_time(full["_media"]["reel"], lt)
    raw = subprocess.run([V.FFMPEG, "-v", "error", "-ss", f"{ts:.4f}", "-i", str(media / "apoyo01.mp4"), "-frames:v", "1",
                          "-vf", f"crop={int(round((eff[2] - eff[0]) * FW))}:{int(round((eff[3] - eff[1]) * FH))}:"
                                 f"{int(round(eff[0] * FW))}:{int(round(eff[1] * FH))},scale={W}:{H},format=rgb24",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    exp = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
    top = slice(0, int(0.6 * H))
    assert np.abs(fr[top, :, :3].astype(int) - exp[top].astype(int)).mean() < 14
    # /frame sin cache = el mismo cuadro que el export
    for t_orig, jid in ((3.0, "br01"), (5.5, "br01"), (11.0, "br02"), (13.5, "br02")):
        jit = full if jid == "br01" else win
        tf = round(tl.remap(t_orig) * 30) / 30
        fr = R.render_frame(p, tf, _opts(tmp_path / "x", media, media_bg=False))
        lt = tf - jit["_fs"]
        rows = jit["_media"]["reel"]
        got = _read(fr, _cells_out(jit, rows[RL.piece_at(rows, lt)][3]))
        assert got is not None and abs(got / FPS - RL.src_time(rows, lt)) <= 1 / 30 + 1e-6, (t_orig, got)


def test_subtitulos_miden_el_contraste_en_el_cuadro_compuesto(media, tmp_path, monkeypatch):
    seen = []
    real = TX.caption_contrast

    def spy(color, lum, stroke, shadow):
        seen.append(lum)
        return real(color, lum, stroke, shadow)
    monkeypatch.setattr(TX, "caption_contrast", spy)
    p = _plan(media / "cam_oscura.mp4", _items(), captions=True)
    tl = T.Timeline.from_plan(p, fps=30)
    o = _opts(tmp_path, media, media_bg=False)
    # dentro del fragmento a pantalla completa se mide lo que queda detras (la app clara), no la toma de camara
    tf = round(tl.remap(2.6) * 30) / 30
    seen.clear()
    R.render_frame(p, tf, o)
    with_reel = max(x for x in seen if x is not None)
    p2 = _plan(media / "cam_oscura.mp4", [dict(it, enabled=False) for it in _items()], captions=True)
    seen.clear()
    R.render_frame(p2, tf, _opts(tmp_path / "b", media, media_bg=False))
    without = max(x for x in seen if x is not None)
    assert with_reel - without > 0.3, (with_reel, without)
    assert real("#FFFFFF", 0.8, 0.0, 0.95) == (TX.CONTRAST_STROKE, 1.0)
    # sin fragmento: se mide la toma (no el cuadro compuesto) y la regla no cambia lo del estilo sobre oscuro
    assert real("#FFFFFF", 0.2, 0.0, 0.95) == (0.0, 0.95)
    assert real("#111111", 0.9, 0.0, 0.95) == (0.0, 0.95)          # tinta oscura: no aplica
    assert real("#FFFFFF", None, 1.0, 0.5) == (1.0, 0.5)
