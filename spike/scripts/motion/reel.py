"""Videos de apoyo en fragmentos (pista fx.media, estilo "reel").

compose.finish_plan coloca los fragmentos que decide scripts/brollplan.py (un video de apoyo mudo, varios
momentos distintos, cada uno a la medida de una frase) y quedan en plan.fx.media:

  {id, start, end (tiempo ORIGINAL del video compuesto), sid, path, kind: "video", style: "reel",
   src: "broll", layout: "window" | "full", name, w, h (de la fuente), media_dur, enabled, locked,
   motion (vacio = segun el estilo), vignette, line, lines, text, why,
   pieces: [{shot, in, out, max, cx, win, why}]}      (in/out/max en s del ARCHIVO de apoyo)

TIEMPO: el fragmento dura lo que dura su tramo FINAL (el render recorta silencios: puede quedar mas corto)
y fit() (= brollplan.fit) reparte ese tiempo entre sus tomas: si sobran segundos cada toma se acorta en
proporcion desde su inicio; si faltan, las ultimas se alargan hasta su `max` (el final de su toma: nunca
se pasa al corte siguiente) y despues el ultimo cuadro se queda. Cada fila lleva ese `max` (src_max): el
render (_reel_cmd), el cuadro suelto y el Studio (ReelCard.srcTime) NUNCA leen la fuente en o despues de
el (un pedazo quitado o cambiado en el Studio, o un Fin alargado, congela el ultimo cuadro de SU toma en
vez de mostrar la siguiente toma del video, que puede ser de personas). Cortes secos entre tomas.

PRESENTACION (la edicion aprobada; 9:16 y simetria):
  - "full" (pantalla completa): cada toma recortada a la proporcion de la caja (alto completo) centrada en
    `cx` (el centro de su sujeto: brollscan.full_crop, sin personas ni textos cortados), cubriendo la caja
    entera, sin tarjeta; los subtitulos van encima (con contraste: text.caption_contrast). `vignette`
    opcional (0 por defecto).
  - "window" (ventana flotante): el cuadro completo (o el recorte `win` de su sujeto, p. ej. un celular
    sobre un fondo liso) en una tarjeta con la proporcion de la fuente (media.card_layout, ancho
    `width` o SYM_W, arriba de los subtitulos, centrada, esquinas redondeadas, sombra suave y borde claro)
    sobre la toma desenfocada y oscurecida (blur 0.55, dim 0.28: el look de la tarjeta flotante).
  - Movimiento (`motion`; vacio = el del estilo, como la grabacion de pantalla: minimal "cut",
    elegante "calm", el resto "lively"): screen.anim (sin inclinacion; nunca translucida).
    En pantalla completa siempre entra y sale con el corte.

Funciones PURAS (sin skia) con port identico en app/static/reelcard.js (tests/test_reel_card.py compara
ambos); el dibujo es el de la tarjeta flotante (media.draw_card con look(), media.draw_background).
"""
from __future__ import annotations

import math
from typing import Any

from . import ctx as C
from . import media as MF
from . import screen as SC

STYLE = "reel"
LAYOUTS = ("window", "full")
ASPECT_DEFAULT = 16 / 9
DEFAULTS = {"blur": MF.DEFAULTS["blur"], "dim": MF.DEFAULTS["dim"], "radius": MF.RADIUS,
            "shadow": MF.DEFAULTS["shadow"], "border": True, "motion": None, "width": None,
            "vignette": 0.0, "layout": "window"}


def _num(v: Any, default: float) -> float:
    if isinstance(v, bool) or v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _r5(v: float) -> float:
    return math.floor(v * 1e5 + 0.5) / 1e5


def param(it: dict, k: str) -> Any:
    v = it.get(k) if isinstance(it, dict) else None
    return DEFAULTS.get(k) if v is None else v


def layout_of(it: dict) -> str:
    v = it.get("layout") if isinstance(it, dict) else None
    return v if v in LAYOUTS else "window"


def motion_of(it: dict, style: str | None = None) -> str:
    """Pantalla completa: siempre "cut". Ventana: el del item o el del estilo (= screen.motion_of)."""
    if layout_of(it) == "full":
        return "cut"
    return SC.motion_of(it, style)


def look(it: dict) -> dict:
    return {"radius": param(it, "radius"), "shadow": param(it, "shadow"), "border": param(it, "border"),
            "sheen": 0.0, "kenburns": 0.0, "kind": "video"}


# ================================================================ tiempo (PURA, = brollplan.fit)
def pieces_of(it: dict) -> list[dict]:
    out = []
    for p in (it.get("pieces") if isinstance(it, dict) else None) or []:
        if not isinstance(p, dict):
            continue
        a, b = _num(p.get("in"), float("nan")), _num(p.get("out"), float("nan"))
        if a != a or b != b or b - a <= 1e-6 or a < 0:
            continue
        out.append(p)
    return out


def fit(pieces, L: float, fps: float = 30.0) -> list[list[float]]:
    """Pedazos -> [[t0, t1, src_in, j, src_max]] en s LOCALES de un tramo de L s, en cuadros enteros (j:
    indice del pedazo entre los validos de pieces_of; uno que queda sin cuadros se omite; src_max: el fin de
    su toma, el tope de lectura). Ver el docstring del modulo. Identico a ReelCard.fit."""
    ps = []
    for p in pieces or []:
        a, b = _num(p.get("in"), float("nan")), _num(p.get("out"), float("nan"))
        if a != a or b != b or b - a <= 1e-6:
            continue
        mx = _num(p.get("max"), b)
        ps.append([a, b, max(b, mx), len(ps)])
    L = max(0.0, _num(L, 0.0))
    if not ps or L <= 0:
        return []
    d = [q[1] - q[0] for q in ps]
    S = sum(d)
    if S >= L:
        d = [x * L / S for x in d]
    else:
        extra = L - S
        for j in range(len(ps) - 1, -1, -1):
            add = min(ps[j][2] - ps[j][1], extra)
            d[j] += add
            extra -= add
            if extra <= 1e-9:
                break
        if extra > 1e-9:
            d[-1] += extra
    out, t, F0 = [], 0.0, 0
    for j, (p, dj) in enumerate(zip(ps, d)):
        t += dj
        F1 = int(math.floor(t * fps + 0.5)) if j < len(ps) - 1 else int(math.floor(L * fps + 0.5))
        if F1 <= F0:
            continue
        out.append([_r6(F0 / fps), _r6(F1 / fps), _r6(p[0]), p[3], _r6(p[2])])
        F0 = F1
    return out


def _row_max(row: list) -> float | None:
    """src_max de una fila de fit() [t0, t1, src_in, j, src_max] o del render [t0, t1, src_in, crop, j,
    src_max] (None en filas viejas sin tope)."""
    v = row[5] if len(row) >= 6 else row[4] if len(row) == 5 else None
    return None if v is None or isinstance(v, bool) else float(v)


def hold_frames(row: list, fps: float = 30.0) -> int:
    """Cuadros que una fila de fit() puede leer de su toma antes de congelar el ultimo: los de
    src_in + i/fps con al menos un cuadro de margen antes de src_max (el primer cuadro de la toma
    siguiente). >= 1."""
    mx = _row_max(row)
    if mx is None:
        return 1 << 30
    return max(1, int(math.floor((mx - float(row[2])) * fps + 1e-6)))


def src_cap(row: list, t: float, fps: float = 30.0) -> float:
    """t (s del archivo) acotado al ultimo cuadro que la fila puede mostrar (ver hold_frames)."""
    if _row_max(row) is None:
        return t
    return min(t, float(row[2]) + (hold_frames(row, fps) - 1) / fps)


def _r6(v: float) -> float:
    return math.floor(v * 1e6 + 0.5) / 1e6


def piece_at(rows: list, lt: float) -> int:
    """Indice de la fila de fit() que contiene el tiempo local lt (en una union gana la siguiente)."""
    if not rows:
        return -1
    for j, r in enumerate(rows):
        if r[0] - 1e-9 <= lt < r[1] - 1e-9:
            return j
    return 0 if lt < rows[0][0] else len(rows) - 1


def src_time(rows: list, lt: float, max_t: float | None = None, fps: float = 30.0) -> float | None:
    """s del archivo de apoyo que se ve en el tiempo local lt (nunca pasa del fin de la toma: src_cap)."""
    j = piece_at(rows, lt)
    if j < 0:
        return None
    r = rows[j]
    t = src_cap(r, r[2] + min(max(lt, r[0]), r[1]) - r[0], fps)
    return t if max_t is None else min(t, max_t)


# ================================================================ geometria (PURA)
def full_crop(cx: Any, src_w: Any, src_h: Any, box_aspect: float = 9 / 16) -> list[float] | None:
    """Recorte [x0, y0, x1, y1] (fracciones) de una toma en pantalla completa: alto completo y el ancho de
    la proporcion de la caja centrado en cx (acotado); una fuente mas angosta que la caja se recorta de
    alto (centrada). None = la fuente entera."""
    w, h = _num(src_w, 0.0), _num(src_h, 0.0)
    if w <= 0 or h <= 0:
        return None
    ba = _clamp(_num(box_aspect, 9 / 16), 0.2, 5.0)
    sa = w / h
    if sa > ba + 1e-6:
        cw = ba / sa
        c = _clamp(_num(cx, 0.5), cw / 2, 1 - cw / 2)
        return [_r5(c - cw / 2), 0.0, _r5(c + cw / 2), 1.0]
    if sa < ba - 1e-6:
        ch = sa / ba
        return [0.0, _r5(0.5 - ch / 2), 1.0, _r5(0.5 + ch / 2)]
    return None


def win_crop(p: dict) -> list[float] | None:
    """Recorte `win` de una toma en ventana ([x0, y0, x1, y1] saneado) o None (el cuadro completo)."""
    c = p.get("win") if isinstance(p, dict) else None
    if not isinstance(c, (list, tuple)) or len(c) != 4:
        return None
    try:
        x0, y0, x1, y1 = (_clamp(float(v), 0.0, 1.0) for v in c)
    except (TypeError, ValueError):
        return None
    if x1 - x0 < 0.1 or y1 - y0 < 0.1:
        return None
    return [x0, y0, x1, y1]


def win_view(p: dict, src_w: Any, src_h: Any, aspect: Any) -> list[float] | None:
    """Lo que se ve de una toma en una tarjeta de proporcion `aspect` (la de la primera toma del
    fragmento): su recorte `win` AGRANDADO a esa proporcion alrededor de su centro (corrido para no salir
    del cuadro), asi el sujeto cabe ENTERO aunque su recorte tenga otra forma (un logo cuadrado en una
    tarjeta 16:9 no pierde la parte de arriba y la de abajo). Si ni el cuadro completo alcanza, lo que
    sobra lo recorta la normalizacion al cubrir. None = el cuadro completo (sin recorte)."""
    c = win_crop(p)
    w, h, a = _num(src_w, 0.0), _num(src_h, 0.0), _num(aspect, 0.0)
    if c is None or w <= 0 or h <= 0 or a <= 0:
        return c
    x0, y0, x1, y1 = c
    cw, ch = (x1 - x0) * w, (y1 - y0) * h
    if cw / ch < a:
        cw = min(w, ch * a)
    else:
        ch = min(h, cw / a)
    fw, fh = cw / w, ch / h
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    nx0 = _clamp(cx - fw / 2, 0.0, 1.0 - fw)
    ny0 = _clamp(cy - fh / 2, 0.0, 1.0 - fh)
    out = [_r5(nx0), _r5(ny0), _r5(nx0 + fw), _r5(ny0 + fh)]
    return None if out == [0.0, 0.0, 1.0, 1.0] else out


def window_aspect(it: dict, src_w: Any, src_h: Any) -> float:
    """Proporcion de la tarjeta: la de lo que se ve de la primera toma (su recorte `win` o el cuadro
    completo), entre media.ASPECT_TALL y media.ASPECT_MAX."""
    w, h = _num(src_w, 0.0), _num(src_h, 0.0)
    ps = pieces_of(it)
    c = win_crop(ps[0]) if ps else None
    if w > 0 and h > 0 and c:
        w, h = w * (c[2] - c[0]), h * (c[3] - c[1])
    a = w / h if w > 0 and h > 0 else ASPECT_DEFAULT
    return _clamp(a, MF.ASPECT_TALL, MF.ASPECT_MAX)


def window_layout(it: dict, src_w: Any, src_h: Any, box_aspect: float = 9 / 16,
                  cap_top: float | None = None, cap_bottom: float | None = None) -> dict:
    """Tarjeta de la ventana: media.card_layout con la proporcion de la fuente y el ancho `width` (o SYM_W)."""
    asp = window_aspect(it, src_w, src_h)
    return MF.card_layout(src_w, src_h, box_aspect, cap_top, param(it, "width") or C.SYM_W, asp, None, cap_bottom)


def anim(lt: float, dur: float, motion: str = "cut") -> dict:
    """= screen.anim; en pantalla completa "cut" y sin fondo desenfocado (bg 0: la toma cubre todo)."""
    return SC.anim(lt, dur, motion)
