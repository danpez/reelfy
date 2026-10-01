"""Tarjeta flotante VIVA (motion/media.py): flotacion visible, vaiven 3D con perspectiva sobre el eje
horizontal, Ken Burns con paralaje y brillo de vidrio (franja de luz + irisado) en imagenes.

Se mide lo que pide la marca: la tarjeta es SIMETRICA en cada cuadro (margen izquierdo = derecho
por fila, al pixel), NUNCA entra a la franja de subtitulos mientras flota (con cualquier
`float`), la perspectiva por franjas no deja costuras, la luz es horizontal (simetrica) y no
toca los clips, todo es determinista y el costo extra por cuadro queda dentro del presupuesto.

Todo con medios SINTETICOS (sin datos del cliente). Correr:
  cd spike && .venv/bin/python -m pytest tests/test_media_float_vida.py -q
"""
import math
import time

import numpy as np
import pytest

from motion import ctx as C
from motion import media as MF
from motion import render as R
from test_media_float import _clip, _item, _mask, _opts, _plan, _png, _src

BA = 9 / 16
CAPS = [{"y": 0.72}, {"y": 0.73, "size": 0.064}, {"y": 0.72, "preset": "bold_caps", "size": 0.06},
        {"y": 0.6}, {"y": 0.25}]
SRCS = [(1080, 1350), (1920, 1080), (1080, 1920)]


def _steady(dur, n=240, in_dur=MF.IN_DUR, out_dur=MF.OUT_DUR):
    """Tiempos locales en reposo: entrada asentada (k = 1) hasta antes de la salida."""
    tin, tout = MF._tin_tout(dur, in_dur, out_dur)
    return np.linspace(2 * tin, dur - tout, n)


# ================================================================== puras: simetria y subtitulos
@pytest.mark.parametrize("fa", [0.0, 0.5, 1.0, 2.0])
def test_simetrica_en_cada_cuadro(fa):
    """Margen izquierdo = derecho en TODO el item (entrada, vaiven, salida): el cuadrilatero animado
    es espejo respecto al centro de la caja y la perspectiva no lo corre de lado."""
    for bw in (216, 540, 1080, 1081):
        bh = bw / BA
        card = MF.card_layout(1080, 1350, BA, MF.caption_top({"y": 0.72}, True, box_aspect=BA))
        for lt in np.linspace(0, 6.0, 181):
            st = MF.card_anim(lt, 6.0, float_amt=fa)
            (tlx, _a), (trx, _b), (brx, _c), (blx, _d) = MF.card_quad(card, bw, bh, st)
            assert abs(tlx - (round(bw) - trx)) < 1e-6 and abs(blx - (round(bw) - brx)) < 1e-6, (bw, lt)
            left, right = min(tlx, blx), round(bw) - max(trx, brx)
            assert abs(left - right) < 1e-6
            assert left >= (C.SAFE["left"] - 0.02) * bw                 # no se sale de la banda


@pytest.mark.parametrize("fa", [0.5, 1.0, 1.5, 2.0])
def test_nunca_entra_a_la_franja_de_subtitulos(fa):
    """En reposo (flotando y meciendose, con cualquier `float` del esquema) la tarjeta no entra a la
    franja de subtitulos: arriba de ella, su borde de abajo no pasa de caption_top (aun lo mas
    abajo posible, con `y` a mano); debajo (subtitulos subidos), su borde de arriba no sube de
    caption_bottom. Tampoco sale de la zona segura."""
    bw, bh = 1080, 1920
    for cap in CAPS:
        top = MF.caption_top(cap, True, box_aspect=BA)
        bot = MF.caption_bottom(cap, True, box_aspect=BA)
        for sw, sh in SRCS:
            for y in (None, 0.99):
                card = MF.card_layout(sw, sh, BA, top, None, None, y, bot)
                below = card["cy"] - card["h"] / 2 >= bot - 1e-9       # tarjeta debajo de subtitulos
                assert below or card["cy"] + card["h"] / 2 <= top + 1e-9
                for dur in (2.0, 6.5):
                    for lt in _steady(dur):
                        q = MF.card_quad(card, bw, bh, MF.card_anim(lt, dur, float_amt=fa))
                        ys = [p[1] for p in q]
                        if below:
                            assert min(ys) >= bot * bh - 1e-6, (cap, sw, y, lt, min(ys) / bh, bot)
                        else:
                            assert max(ys) <= top * bh + 1e-6, (cap, sw, y, lt, max(ys) / bh, top)
                        if not below:
                            assert min(ys) >= C.SAFE["top"] * bh - 1e-6, (cap, lt, min(ys) / bh)
                        else:
                            assert max(ys) <= C.SAFE["bottom"] * bh + 1e-6, (cap, lt, max(ys) / bh)


def test_flota_y_se_mece_a_la_vista():
    """A 1080x1920 la flotacion se ve (> 25 px de pico a pico), el vaiven es una perspectiva clara
    (arriba y abajo cambian de ancho > 2 % por turnos) y el centro horizontal no se mueve."""
    bw, bh = 1080, 1920
    card = MF.card_layout(1080, 1350, BA, MF.caption_top({"y": 0.72}, True, box_aspect=BA))
    qs = [MF.card_quad(card, bw, bh, MF.card_anim(lt, 12.0)) for lt in _steady(12.0, 600)]
    cy = [(q[0][1] + q[3][1]) / 2 for q in qs]
    assert max(cy) - min(cy) > 25, max(cy) - min(cy)
    ratio = [(q[1][0] - q[0][0]) / (q[2][0] - q[3][0]) for q in qs]     # ancho arriba / abajo
    assert min(ratio) < 0.98 and max(ratio) > 1.02, (min(ratio), max(ratio))
    for q in qs:
        assert abs((q[0][0] + q[1][0]) / 2 - bw / 2) < 1e-6 and abs((q[2][0] + q[3][0]) / 2 - bw / 2) < 1e-6
    # determinista: la misma lt da exactamente el mismo estado
    assert MF.card_anim(2.345, 6.0) == MF.card_anim(2.345, 6.0)
    # se mueve en cada cuadro (nunca dos cuadros seguidos iguales en reposo)
    sts = [MF.card_anim(k / 30, 6.0) for k in range(30, 150)]
    assert all(a["dy"] != b["dy"] or a["tilt"] != b["tilt"] for a, b in zip(sts, sts[1:]))


def test_franjas_de_la_perspectiva_exactas():
    """warp_strips cubre la tarjeta sin huecos, cae EXACTO en las esquinas de tilt_quad y el borde
    lateral no hace escalones: entre franjas vecinas salta <= WARP_STEP px y dentro de cada una se
    aparta < WARP_STEP de la perspectiva exacta (tarjeta de 1080 y de 4K, vaiven y entrada; solo
    la entrada a 4K toca el tope de franjas: < 0.35 px)."""
    assert MF.warp_strips(822, 1028, 0.0, 1.01) == [(-514, 514, -514 * 1.01, 514 * 1.01, 1.01)]
    for w, h in ((822, 1028), (1642, 2053), (304, 380)):
        for tilt in (-14.0, -4.5, -1.0, 0.5, 4.5, 9.0):
            for s in (0.9, 1.0, 1.006):
                st = MF.warp_strips(w, h, tilt, s)
                assert MF.WARP_MIN <= len(st) <= MF.WARP_MAX
                assert st[0][0] == pytest.approx(-h / 2) and st[-1][1] == pytest.approx(h / 2)
                lim = MF.WARP_STEP if len(st) < MF.WARP_MAX else 0.35   # tope: solo la entrada a 4K
                for a, b in zip(st, st[1:]):
                    assert a[1] == pytest.approx(b[0]) and a[3] == pytest.approx(b[2])     # contiguas
                    assert abs(a[4] - b[4]) * w / 2 <= lim + 1e-9                           # sin escalon
                q = MF.tilt_quad(w, h, tilt, s)
                assert st[0][2] == pytest.approx(q[0][1]) and st[-1][3] == pytest.approx(q[3][1])
                th, d = math.radians(tilt), MF.PERSP * h
                for v0, v1, _y0, _y1, kx in st:
                    for v in (v0, (v0 + v1) / 2, v1):
                        k = d / (d - v * math.sin(th)) * s
                        assert abs(kx - k) * w / 2 < lim, (w, tilt, v, kx, k)
    # la entrada (14 grados) a 4K llega al tope de franjas y aun asi el salto queda < 0.35 px; el
    # vaiven (<= SWAY) nunca llega al tope, ni a 4K
    st = MF.warp_strips(1642, 2053, 14.0, 1.0)
    assert len(st) == MF.WARP_MAX and max(abs(a[4] - b[4]) * 821 for a, b in zip(st, st[1:])) < 0.35
    assert len(MF.warp_strips(1642, 2053, MF.SWAY, 1.01)) < MF.WARP_MAX


# ================================================================== puras: Ken Burns, paralaje y luz
def test_kenburns_y_paralaje_dentro_de_la_imagen():
    """El paralaje mueve la imagen DENTRO de la tarjeta (profundidad) pero la ventana nunca sale de
    la imagen ni recorta mas de lo que deja la holgura del zoom (texto pegado al borde a salvo)."""
    w, h = 820, 1026
    moved = 0.0
    for kb in (0.0, 0.03, 0.08, 0.3):
        pw, ph = math.ceil(w * (1 + kb)), math.ceil(h * (1 + kb))
        for fa in (0.0, 1.0, 2.0):
            for lt in np.linspace(0, 6.0, 121):
                st = MF.card_anim(lt, 6.0, float_amt=fa)
                z = MF.kenburns_zoom(lt, 6.0, kb)
                sx, sy, sw, sh = MF.cover_rect(pw, ph, w, h, z)
                p = MF.parallax_px(st["fdy"], 1920, h, z)
                slack = (1 - 1 / z) * h / 2
                assert abs(p) <= MF.PARALLAX_MAX * slack + 1e-9
                sy2 = sy + p * sh / h
                assert sy2 >= -1e-6 and sy2 + sh <= ph + 1e-6
                # recorte maximo por lado <= (1 + PARALLAX_MAX) de la holgura
                assert min(sy2, ph - sy2 - sh) >= sy * (1 - MF.PARALLAX_MAX) - 1e-6
                if kb > 0 and fa > 0:
                    moved = max(moved, abs(p))
                if kb == 0:
                    assert p == 0 and z == 1.0
    assert moved > 3.0, moved                      # se nota (px de una tarjeta de 1026 de alto)


def test_horario_de_la_franja_de_luz():
    dur = 9.0
    tin, tout = MF._tin_tout(dur, MF.IN_DUR, MF.OUT_DUR)
    a0 = tin + MF.SHEEN_DELAY
    assert MF.sheen_state(a0 - 0.01, dur) is None and MF.sheen_state(a0, dur) is not None
    # cruza de arriba abajo, entrando y saliendo por fuera de la tarjeta
    us = [MF.sheen_state(a0 + p * MF.SHEEN_DUR, dur)[0] for p in np.linspace(0, 0.999, 60)]
    assert all(b > a for a, b in zip(us, us[1:])) and us[0] < -0.2 and us[-1] > 1.2
    # periodica y nunca durante la salida
    assert MF.sheen_state(a0 + MF.SHEEN_EVERY + 0.3, dur) is not None
    assert MF.sheen_state(a0 + MF.SHEEN_DUR + 0.2, dur) is None
    for lt in np.linspace(dur - tout, dur, 20):
        assert MF.sheen_state(lt, dur) is None
    # item corto: una pasada si cabe (>= SHEEN_MIN), si no ninguna
    short = MF.IN_DUR + MF.SHEEN_DELAY + MF.SHEEN_MIN + MF.OUT_DUR + 0.05
    assert any(MF.sheen_state(lt, short) for lt in np.linspace(0, short, 400))
    assert not any(MF.sheen_state(lt, short - 0.2) for lt in np.linspace(0, short - 0.2, 400))
    # envolvente suave en las puntas
    assert MF.sheen_state(a0, dur)[1] == 0.0 and MF.sheen_state(a0 + MF.SHEEN_DUR / 2, dur)[1] == 1.0


def _fwhm(keys, i):
    """Ancho a media altura del pico keys[i] de una capa lineal por tramos ((u, f), ...)."""
    half = keys[i][1] / 2

    def cross(j, step):
        while keys[j + step][1] > half:
            j += step
        (ua, fa), (ub, fb) = keys[j], keys[j + step]
        return ua + (half - fa) * (ub - ua) / (fb - fa)
    return cross(i, 1) - cross(i, -1)


def test_franja_lenta_a_30_fps():
    """La franja de luz no salta: a 30 fps su centro avanza <= 1/2 del ancho a media altura de su
    nucleo por cuadro, en la pasada normal y en la mas corta (items cortos). Antes cruzaba en ~0.4 s
    y saltaba mas que su nucleo cada cuadro (se veia a saltos, el reflejo fino parpadeaba). El
    reflejo de detras ya no es una linea fina: >= 2x lo de antes (0.025 del alto)."""
    band = list(MF._BAND)
    core = _fwhm(band, max(range(len(band)), key=lambda i: band[i][1]))
    refl = _fwhm(band, 1)
    assert 0.07 <= core <= 0.12 and refl >= 2 * 0.025, (core, refl)
    tin, _tout = MF._tin_tout(20.0, MF.IN_DUR, MF.OUT_DUR)
    a0 = tin + MF.SHEEN_DELAY
    for dur in (20.0, a0 + MF.SHEEN_MIN + MF.OUT_DUR + 1e-3):      # pasada normal y la mas corta
        us = [MF.sheen_state(a0 + k / 30, dur) for k in range(int(MF.SHEEN_DUR * 30) + 2)]
        us = [u[0] for u in us if u]
        assert len(us) >= int(MF.SHEEN_MIN * 30) - 1, (dur, len(us))
        step = max(b - a for a, b in zip(us, us[1:]))
        assert 0 < step <= 0.5 * core, (dur, step, core)
    assert MF.SHEEN_EVERY >= MF.SHEEN_DUR + 2.5                    # descanso entre pasadas


def test_luz_con_desenfoque_de_movimiento():
    """light_at (lo que se dibuja): la franja se promedia en el obturador, asi a 30 fps cada fila
    ve pasar la luz en >= 2 cuadros seguidos (nunca un destello de un cuadro) y la luz se mueve en
    cada cuadro de la pasada; fuera de la pasada es exactamente light_stops sin franja; a media
    pasada el pico sigue siendo ~SHEEN_A (el desenfoque no la apaga)."""
    dur = 8.0
    tin, _tout = MF._tin_tout(dur, MF.IN_DUR, MF.OUT_DUR)
    a0 = tin + MF.SHEEN_DELAY
    assert MF.light_at(a0 - 0.2, dur, 1.3, 1.0, 0.6) == MF.light_stops(None, 1.3, 1.0, 0.6)
    assert MF.light_at(a0 + MF.SHEEN_DUR / 2, dur, 0.0, 0.0) == []
    mid = MF.light_at(a0 + MF.SHEEN_DUR / 2, dur, 0.0, 1.0, 0.0)
    assert 0.85 * MF.SHEEN_A <= max(s[4] for s in mid) <= MF.SHEEN_A + 1e-9
    n = 400
    frames = [MF.light_column(MF.light_at(a0 + k / 30, dur, 0.0, 1.0, 0.0), n)[:, 0, 3].astype(float)
              for k in range(-2, int(MF.SHEEN_DUR * 30) + 3)]
    A = np.stack(frames)                                       # cuadros x filas
    k0, k1 = int(0.2 * len(A)), int(0.8 * len(A))                # la franja dentro de la tarjeta
    assert all(not np.array_equal(a, b) for a, b in zip(A[k0:k1], A[k0 + 1:k1 + 1]))
    rows = range(int(0.45 * n), int(0.97 * n))                 # debajo del brillo fijo de arriba
    for r in rows:
        col = A[:, r]
        on = col > 0.5 * col.max()
        runs, run = [], 0
        for v in on:
            if v:
                run += 1
            elif run:
                runs.append(run)
                run = 0
        if run:
            runs.append(run)
        assert runs and min(runs) >= 2, (r / n, runs)


def test_luz_degradado_vertical():
    """light_stops: paradas ordenadas en [0, 1] (con 0 y 1), alfa 0..1, color 0..255; sin `sheen`
    no hay luz; la franja es lo mas brillante de la columna; el irisado pinta color solo con holo."""
    assert MF.light_stops((0.5, 1.0), 0.0, 0.0) == []
    for sheen in (None, (-0.25, 0.4), (0.5, 1.0), (1.2, 0.7)):
        for holo in (0.0, 0.6, 1.0):
            for sway in (-MF.SWAY, 0.0, MF.SWAY):
                st = MF.light_stops(sheen, sway, 1.0, holo)
                us = [s[0] for s in st]
                assert us[0] == 0.0 and us[-1] == 1.0 and all(b > a for a, b in zip(us, us[1:]))
                for _u, r, g, b, a in st:
                    assert 0 <= a <= 1 and all(0 <= c <= 255 for c in (r, g, b))
    st = MF.light_stops((0.5, 1.0), 0.0, 1.0, 0.0)
    best = max(st, key=lambda s: s[4])
    assert best[0] == pytest.approx(0.5) and best[4] == pytest.approx(MF.SHEEN_A, rel=0.05)
    assert all(min(s[1:4]) > 254.9 for s in st)                         # sin holo: luz blanca
    col = MF.light_stops((0.5, 1.0), 0.0, 1.0, 1.0)
    assert any(min(s[1:4]) < 230 and s[4] > 0.05 for s in col)          # con holo: color
    # el brillo de vidrio de arriba sube cuando la parte de arriba se acerca (vaiven negativo)
    g_fwd = MF.light_stops(None, -MF.SWAY, 1.0)[0][4]
    g_back = MF.light_stops(None, MF.SWAY, 1.0)[0][4]
    assert g_fwd > g_back > 0
    # columna premultiplicada: igual a interpolar las paradas
    colpx = MF.light_column(st, 100)
    assert colpx.shape == (100, 1, 4)
    a = np.interp((np.arange(100) + 0.5) / 100, [s[0] for s in st], [s[4] for s in st])
    assert np.abs(colpx[:, 0, 3].astype(float) - a * 255).max() <= 0.51
    assert (colpx[:, 0, :3] <= colpx[:, 0, 3:4]).all()                  # premultiplicado


def test_brillo_solo_en_imagenes():
    assert MF.sheen_amount({"kind": "image"}) == 1.0 and MF.sheen_amount({}) == 1.0
    assert MF.sheen_amount({"kind": "video"}) == 0.0 and MF.sheen_amount({"kind": "image"}, baked=True) == 0.0
    assert MF.sheen_amount({"kind": "video", "sheen": 0.4}) == pytest.approx(0.4)
    assert MF.sheen_amount({"kind": "image", "sheen": 7}) == 1.0 and MF.sheen_amount({"sheen": -1}) == 0.0
    assert MF.param({}, "holo") == MF.LIVE_DEFAULTS["holo"] and MF.param({"holo": 0.2}, "holo") == 0.2


# ================================================================== render por pixeles
RW, RH = 432, 768


@pytest.fixture(scope="module")
def vsrc(tmp_path_factory):
    return _src(tmp_path_factory.mktemp("vsrc") / "fuente.mp4", dur=7.0, size=f"{RW}x{RH}")


@pytest.fixture(scope="module")
def vmedia(tmp_path_factory):
    d = tmp_path_factory.mktemp("vmedia")
    _png(d / "foto01.png")
    _png(d / "gris01.png", (110, 110, 110))
    _clip(d / "clip01.mp4")
    return d


def _vo(tmp_path, vmedia, **kw):
    return _opts(tmp_path, W=RW, H=RH, media_root=str(vmedia), **kw)


def _rows_margins(m):
    """(fila, margen izq, margen der) de cada fila con tarjeta."""
    out = []
    for y in np.where(m.any(axis=1))[0]:
        xs = np.where(m[y])[0]
        out.append((int(y), int(xs.min()), RW - 1 - int(xs.max())))
    return out


def test_render_simetrico_por_fila_sin_costuras_y_sobre_los_subtitulos(vsrc, tmp_path, vmedia):
    """Render real a 432x768 en los extremos del vaiven: en CADA fila de la tarjeta el margen
    izquierdo y el derecho son iguales (+-1 px); arriba y abajo cambian de ancho (perspectiva);
    las franjas no dejan filas de otro color (sin costuras); la tarjeta termina arriba de la franja
    de subtitulos."""
    o = _vo(tmp_path, vmedia)
    it = _item("foto01", 0.5, 6.5, sheen=0, border=False)
    p = _plan(vsrc, [it], captions=True, dur=7.0)
    job = R.prepare(p, o)["job"]
    top = MF.caption_top(job["captions"], True, 0.5, 6.5, RW / RH)
    widths = []
    for lt in (MF.SWAY_T / 4, 3 * MF.SWAY_T / 4, 2.0):          # vaiven arriba, abajo y en medio
        fr = R.render_frame(p, 0.5 + lt, o)
        m = _mask(fr)
        rows = _rows_margins(m)
        assert len(rows) > 200
        inner = rows[12:-12]                                     # fuera de las esquinas redondeadas
        for y, left, right in inner:
            assert abs(left - right) <= 1, (lt, y, left, right)
        # sin costuras: cada fila interior es tarjeta de borde a borde (sin pixeles de otro color)
        for y, left, right in inner:
            assert m[y, left:RW - right].all(), (lt, y)
        widths.append((RW - inner[0][1] - inner[0][2], RW - inner[-1][1] - inner[-1][2]))
        assert rows[-1][0] <= top * RH + 1, (lt, rows[-1][0], top * RH)
    (t0, b0), (t1, b1) = widths[0], widths[1]
    assert t0 < b0 - 2 and t1 > b1 + 2, widths                  # perspectiva por turnos


def test_render_franja_de_luz_horizontal(vsrc, tmp_path, vmedia):
    """Imagen gris: a media pasada la franja aclara una banda de filas (mas en el centro de la
    franja que lejos), igual a la izquierda y a la derecha (luz horizontal: simetrica); con
    sheen 0 no hay luz; el mismo cuadro sale identico dos veces (determinista)."""
    o = _vo(tmp_path, vmedia)
    dur = 6.0
    tin, _tout = MF._tin_tout(dur, MF.IN_DUR, MF.OUT_DUR)
    t = round((0.5 + tin + MF.SHEEN_DELAY + MF.SHEEN_DUR / 2) * 30) / 30     # cuadro exacto
    lt = t - 0.5                                                 # franja a ~media tarjeta
    u, env = MF.sheen_state(lt, dur)
    assert abs(u - 0.5) < 0.05 and env == 1.0
    lit = R.render_frame(_plan(vsrc, [_item("gris01", 0.5, 0.5 + dur, holo=0)], dur=7.0), t, o)
    off = R.render_frame(_plan(vsrc, [_item("gris01", 0.5, 0.5 + dur, sheen=0)], dur=7.0), t, o)
    again = R.render_frame(_plan(vsrc, [_item("gris01", 0.5, 0.5 + dur, holo=0)], dur=7.0), t, o)
    assert np.array_equal(lit, again)
    job = R.prepare(_plan(vsrc, [_item("gris01", 0.5, 0.5 + dur)], dur=7.0), o)["job"]
    card = job["media"][0]["_card"]
    st = MF.card_anim(lt, dur)
    q = MF.card_quad(card, RW, RH, st)
    yt, yb = q[0][1], q[3][1]
    # fila de la franja: v local = (u - 1/2) h llevado por las franjas de la perspectiva
    _x0, cy0, cw, h = MF.card_px(card, RW, RH)
    v = (u - 0.5) * h
    v0, v1, ya, yb_, _k = next(sx for sx in MF.warp_strips(cw, h, st["tilt"], st["scale"]) if sx[0] <= v <= sx[1])
    yband = cy0 + h / 2 + st["dy"] * RH + ya + (v - v0) * (yb_ - ya) / (v1 - v0)
    diff = lit[..., :3].astype(int).mean(axis=2) - off[..., :3].astype(int).mean(axis=2)
    xc = RW // 2
    ymid = int(round(yband))
    yfar = int(round(yt + 0.92 * (yb - yt)))
    band = max(diff[y, xc - 60:xc + 60].mean() for y in range(ymid - 3, ymid + 4))
    far = diff[yfar - 2:yfar + 3, xc - 60:xc + 60].mean()
    peak = MF.SHEEN_A * (255 - 110)                              # luz blanca encima del gris
    assert band > 0.8 * peak and far < 0.2 * band, (band, far, peak)
    # horizontal: cada fila aclara igual en columnas espejo
    for y in range(int(yt) + 20, int(yb) - 20, 7):
        l_, r_ = diff[y, xc - 120:xc - 20], diff[y, xc + 21:xc + 121][::-1]
        assert abs(l_.mean() - r_.mean()) <= 2.0, (y, l_.mean(), r_.mean())


def test_render_clip_sin_luz_pero_flota(vsrc, tmp_path, vmedia):
    """Un clip flota y se mece (cambia de posicion entre cuadros) pero no lleva luz: el cuadro con
    `sheen` sin definir es identico al de sheen 0."""
    o = _vo(tmp_path, vmedia)
    base = _item("clip01", 0.5, 4.0, kind="video", fit="hold")
    t = 0.5 + MF.IN_DUR + MF.SHEEN_DELAY + MF.SHEEN_DUR / 2
    a = R.render_frame(_plan(vsrc, [base], dur=7.0), t, o)
    b = R.render_frame(_plan(vsrc, [dict(base, sheen=0)], dur=7.0), t, o)
    assert np.array_equal(a, b)
    job = R.prepare(_plan(vsrc, [base], dur=7.0), o)["job"]
    card = job["media"][0]["_card"]
    q1 = MF.card_quad(card, RW, RH, MF.card_anim(1.0, 3.5))
    q2 = MF.card_quad(card, RW, RH, MF.card_anim(2.4, 3.5))
    assert abs(q1[0][1] - q2[0][1]) > 2 or abs((q1[1][0] - q1[0][0]) - (q2[1][0] - q2[0][0])) > 2


# ================================================================== presupuesto de tiempo
def test_presupuesto_de_tiempo_1080x1920():
    """Lo nuevo (perspectiva por franjas, paralaje, luz) cuesta poco: a 1080x1920 el fondo + la
    tarjeta VIVA (vaiven al maximo y la franja de luz a media tarjeta) no pasan de 1.3x la misma
    tarjeta quieta (sin inclinacion ni luz = el dibujo de antes). La parte de la tarjeta es < la
    mitad del cuadro, asi el cuadro con tarjeta sube < 15 %. Medido intercalado (la carga de la Mac
    afecta a los dos igual)."""
    from PIL import Image
    W, H = 1080, 1920
    yy, xx = np.mgrid[0:1350, 0:1080]
    arr = np.zeros((1350, 1080, 4), np.uint8)
    arr[..., 0] = (40 + 120 * yy / 1350).astype(np.uint8)
    arr[..., 1] = (30 + 60 * xx / 1080).astype(np.uint8)
    arr[..., 2] = 150
    arr[..., 3] = 255
    img = C.image_from_rgba(np.asarray(Image.fromarray(arr)))
    surf = C.offscreen(W, H)
    cv = surf.getCanvas()
    rng = np.random.default_rng(3)
    bg = np.zeros((H, W, 4), np.uint8)
    bg[..., :3] = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
    bg[..., 3] = 255
    base = C.image_from_rgba(bg)
    card = MF.card_layout(1080, 1350, BA, MF.caption_top({"y": 0.72}, True, box_aspect=BA))
    dur = 8.0
    lt_live = MF.IN_DUR + MF.SHEEN_DELAY + MF.SHEEN_DUR / 2       # franja a media tarjeta
    st_live = MF.card_anim(lt_live, dur)
    st_live["tilt"] = st_live["sway"] = MF.SWAY                   # vaiven al maximo
    st_still = dict(st_live, tilt=0.0, sway=0.0, fdy=0.0)

    def run(st, it, n):
        ts = []
        for k in range(n):
            cv.drawImage(base, 0, 0)
            t0 = time.perf_counter()
            MF.draw_background(cv, surf.makeImageSnapshot(), W, H, 1.0)
            MF.draw_card(cv, img, W, H, card, st, it, lt_live + k * 1e-4, dur)
            ts.append(time.perf_counter() - t0)
        return ts

    run(st_live, {}, 3)
    live, still = [], []
    for _ in range(6):
        live += run(st_live, {}, 6)
        still += run(st_still, {"sheen": 0, "kenburns": 0.0}, 6)
    ratio = float(np.median(live) / np.median(still))
    assert ratio <= 1.3, (ratio, np.median(live), np.median(still))
