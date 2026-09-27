"""Camara virtual del motor: encuadre (zoom, punch, sacudida, ken burns), motion blur,
efectos de post (rgb split, flash) y seguimiento de rostro sobre el video base.

Contrato (docs/MOTION-ENGINE.md, 4.2b):
  MATRIX_EFFECTS[kind](item, lt, dur, ctx) -> (skia.Matrix, Motion)
      kind en {zoom, punch, shake, kenburns}. La matriz lleva coordenadas del cuadro base
      (px, W x H) a coordenadas de salida. `Motion` es una tupla (mx, my) = desplazamiento
      en px por cuadro del punto que mas se mueve (render agrega motion blur si es grande);
      ademas trae `.cur` y `.prev` (matrices de este cuadro y del anterior) para que
      `motion_blur` haga un obturador real (sirve tambien para zoom, no solo traslacion).
  POST_EFFECTS[kind](img, item, lt, dur, ctx) -> skia.Image, kind en {rgb_split, flash_frame}.
  face_track(base_video, W, H, fps, n_frames) -> np.ndarray (n, 4) (cx, cy, w, h) normalizado,
      NaN donde no hay cara. Detecta solo los cortes de toma (no suaviza a traves de ellos).

Uso tipico en render (por cuadro):
    res = [camera.MATRIX_EFFECTS[it["kind"]](it, lt, dur, ctx) for it in activos]
    M, mv = camera.combine(res, ctx)
    img = camera.apply_camera(img, M, mv, ctx)      # warp + motion blur (muestrea del base)
    # equivalente: img = camera.warp(img, M, ctx); img = camera.motion_blur(img, mv, ctx)
    # (motion_blur reconoce la salida del ultimo warp y muestrea del cuadro base)

Todas las transformaciones cubren siempre el cuadro completo (escala >= 1 y traslacion
acotada): nunca aparecen bordes negros. El ruido de la sacudida es suave y determinista
(semilla por item), no aleatorio por cuadro.

Ancla del encuadre (en px del cuadro BASE, antes de la camara), por prioridad:
  item["center"] = [x, y] (fijo) | item["_anchor"] = [x, y] (lo fija render por item con
  item_anchor(): mediana de la cara en el rango, determinista) | ctx.face (la cara del cuadro
  base, suavizada por face_track) | la ultima cara valida de ese item en este proceso (nunca
  salta al centro a mitad del item) | el centro un poco arriba.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import cv2
import numpy as np
import skia

from . import ctx as C
from . import ease


# ---------------------------------------------------------------- parametros tolerantes
def _num(d, k: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    """Numero del item tolerante a None/strings/NaN (cae al default) y acotado."""
    v = d.get(k, default) if isinstance(d, dict) else default
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = float(default)
    if not math.isfinite(v):
        v = float(default)
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _vec2(v, default=None):
    """[x, y] valido (finito) o default."""
    if isinstance(v, (list, tuple, np.ndarray)) and len(v) >= 2:
        try:
            x, y = float(v[0]), float(v[1])
        except (TypeError, ValueError):
            return default
        if math.isfinite(x) and math.isfinite(y):
            return x, y
    return default


def _rgb(c, default=(255, 255, 255)) -> tuple[int, int, int]:
    try:
        return C.rgb(c)
    except Exception:
        return default


def _face_ok(face) -> bool:
    try:
        return face is not None and len(face) >= 2 and math.isfinite(float(face[0])) \
            and math.isfinite(float(face[1]))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- utilidades de matriz
class Motion(tuple):
    """Vector de movimiento (mx, my) en px por cuadro, con las matrices que lo producen.

    Se comporta como una tupla de 2 elementos (contrato), y expone:
      .cur  skia.Matrix del cuadro actual (o None)
      .prev skia.Matrix del cuadro anterior (o None)
      .speed magnitud en px por cuadro
    """

    def __new__(cls, mx: float = 0.0, my: float = 0.0, cur: skia.Matrix | None = None,
                prev: skia.Matrix | None = None):
        o = super().__new__(cls, (float(mx), float(my)))
        o.cur = cur
        o.prev = prev
        return o

    @property
    def speed(self) -> float:
        return math.hypot(self[0], self[1])

    def __reduce__(self):  # pickle sin matrices (skia.Matrix no es serializable)
        return (Motion, (self[0], self[1]))


def _m(a: float, b: float, c: float, d: float, e: float, f: float) -> skia.Matrix:
    """Matriz afin [[a, b, c], [d, e, f], [0, 0, 1]]."""
    return skia.Matrix.MakeAll(a, b, c, d, e, f, 0.0, 0.0, 1.0)


def to_affine(M: skia.Matrix) -> np.ndarray:
    """skia.Matrix -> np.float64 (2, 3) para cv2.warpAffine."""
    v = M.get9()
    return np.array([[v[0], v[1], v[2]], [v[3], v[4], v[5]]], np.float64)


def from_affine(A: np.ndarray) -> skia.Matrix:
    A = np.asarray(A, np.float64)
    return _m(A[0, 0], A[0, 1], A[0, 2], A[1, 0], A[1, 1], A[1, 2])


def concat(*ms: skia.Matrix) -> skia.Matrix:
    """Composicion en orden de aplicacion: concat(A, B) aplica A y luego B (B o A)."""
    out = skia.Matrix()
    for m in ms:
        if m is not None:
            out = skia.Matrix.Concat(m, out)
    return out


def is_identity(M: skia.Matrix | None, tol: float = 1e-4) -> bool:
    if M is None:
        return True
    v = M.get9()
    ref = (1, 0, 0, 0, 1, 0, 0, 0, 1)
    return all(abs(v[i] - ref[i]) < (tol if i not in (2, 5) else 1e-2) for i in range(9))


def matrix_scale(M: skia.Matrix | None) -> float:
    """Escala media (px de salida por px del base) de una afin."""
    if M is None:
        return 1.0
    v = M.get9()
    return math.sqrt(abs(v[0] * v[4] - v[1] * v[3]))


def scale_about(s: float, cx: float, cy: float) -> skia.Matrix:
    """Escala s alrededor del punto (cx, cy) en px (ese punto queda fijo)."""
    return _m(s, 0.0, cx * (1 - s), 0.0, s, cy * (1 - s))


def cover_clamp(M: skia.Matrix, W: float, H: float) -> skia.Matrix:
    """Acota la traslacion de una matriz escala+traslacion para que la imagen W x H
    transformada siga cubriendo todo el cuadro de salida (sin bordes)."""
    v = list(M.get9())
    sx, sy = max(1.0, v[0]), max(1.0, v[4])
    tx = min(0.0, max(W - sx * W, v[2]))
    ty = min(0.0, max(H - sy * H, v[5]))
    return _m(sx, v[1], tx, v[3], sy, ty)


def _grid_points(W: float, H: float, extra: list | None = None) -> np.ndarray:
    xs = np.array([0.08, 0.5, 0.92]) * W
    ys = np.array([0.08, 0.5, 0.92]) * H
    pts = [(x, y) for y in ys for x in xs]
    if extra:
        pts += extra
    return np.array(pts, np.float64)


def motion_between(cur: skia.Matrix, prev: skia.Matrix, W: float, H: float,
                   extra_pts: list | None = None) -> Motion:
    """Vector de movimiento (px/cuadro) entre dos matrices: desplazamiento en la salida
    del punto del cuadro (rejilla 3x3 + extra) que mas se mueve."""
    pts = _grid_points(W, H, extra_pts)
    a, b = to_affine(cur), to_affine(prev)
    hom = np.c_[pts, np.ones(len(pts))]
    d = hom @ a.T - hom @ b.T
    k = int(np.argmax(np.hypot(d[:, 0], d[:, 1])))
    return Motion(d[k, 0], d[k, 1], cur, prev)


# ---------------------------------------------------------------- ancla del encuadre
_ANCHORS: dict = {}   # (seed, item) -> (n, x, y): ultima cara valida vista en este proceso


def item_anchor(track, f0: int, f1: int, min_valid: float = 0.15) -> tuple[float, float] | None:
    """Ancla fija de un item: mediana (cx, cy) de la pista de rostro en los cuadros [f0, f1).
    Determinista (la calcula render una vez por item y la guarda en item['_anchor']).
    None si la cara aparece en menos de `min_valid` del rango."""
    if track is None:
        return None
    tr = np.asarray(track, np.float64)
    if tr.ndim != 2 or tr.shape[1] < 2 or len(tr) == 0:
        return None
    f0 = int(max(0, min(len(tr) - 1, f0)))
    f1 = int(max(f0 + 1, min(len(tr), f1)))
    seg = tr[f0:f1, :2]
    ok = np.all(np.isfinite(seg), axis=1)
    if not ok.any() or ok.mean() < min_valid:
        return None
    return float(np.median(seg[ok, 0])), float(np.median(seg[ok, 1]))


def _clamp_anchor(x: float, y: float) -> tuple[float, float]:
    return min(0.9, max(0.1, x)), min(0.9, max(0.08, y))


def _anchor_norm(item: dict, ctx) -> tuple[float, float]:
    """Ancla normalizada (ver docstring del modulo)."""
    c = item.get("center")
    v = _vec2(c)
    if v is not None:
        return v
    if c == "frame":
        return 0.5, 0.45
    a = _vec2(item.get("_anchor"))
    if a is not None:
        return _clamp_anchor(*a)
    key = (int(getattr(ctx, "seed", 0) or 0), _item_key(item))
    face = getattr(ctx, "face", None)
    if _face_ok(face):
        x, y = _clamp_anchor(float(face[0]), float(face[1]))
        if len(_ANCHORS) > 2048:
            _ANCHORS.clear()
        _ANCHORS[key] = (int(getattr(ctx, "n", 0) or 0), x, y)
        return x, y
    held = _ANCHORS.get(key)
    if held is not None:
        return held[1], held[2]
    return 0.5, 0.45


def _anchor(item: dict, ctx) -> tuple[float, float]:
    """Ancla en px del cuadro base."""
    x, y = _anchor_norm(item, ctx)
    return x * ctx.W, y * ctx.H


def _framed(s: float, s_max: float, item: dict, ctx, anchor=None) -> skia.Matrix:
    """Escala s alrededor del ancla (cara). Con item.target = [x, y] (normalizado) el ancla
    viaja hacia ese punto de la salida a medida que crece el zoom (reencuadre: p. ej. llevar
    la cara al tercio superior en un punch). Siempre cubre el cuadro."""
    ax, ay = anchor if anchor is not None else _anchor(item, ctx)
    tgt = _vec2(item.get("target"))
    if tgt is not None and s_max > 1.0 + 1e-6:
        k = max(0.0, min(1.0, (s - 1.0) / (s_max - 1.0)))
        ox = ax + (tgt[0] * ctx.W - ax) * k
        oy = ay + (tgt[1] * ctx.H - ay) * k
        M = _m(s, 0.0, ox - s * ax, 0.0, s, oy - s * ay)
    else:
        M = scale_about(s, ax, ay)
    return cover_clamp(M, ctx.W, ctx.H)


def _item_key(item: dict) -> str:
    return str(item.get("id") or f"{item.get('kind', 'fx')}:{item.get('start', 0)}")


def _face_px(ctx):
    f = getattr(ctx, "face", None)
    if _face_ok(f):
        return [(float(f[0]) * ctx.W, float(f[1]) * ctx.H)]
    return None


def _with_motion(fn, item: dict, lt: float, dur: float, ctx, prev_identity: bool = False):
    """(M, Motion) del cuadro lt con la MISMA ancla para el cuadro previo (el vector solo
    refleja el movimiento de camara). prev_identity: antes del inicio el efecto no existia
    (la sacudida entra de golpe: el primer cuadro lleva estela, desde medio camino)."""
    anchor = _anchor(item, ctx)
    cur = fn(item, lt, dur, ctx, anchor)
    dt = 1.0 / max(1.0, float(ctx.fps))
    if lt - dt >= 0:
        prev = fn(item, lt - dt, dur, ctx, anchor)
    elif prev_identity:
        # a medio camino desde la camara quieta: el golpe lleva estela sin embarrar el cuadro
        # (el salto de escala de cobertura no debe verse como un zoom borroso)
        c = to_affine(cur)
        prev = from_affine(0.5 * c + 0.5 * np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]))
    else:
        prev = cur
    return cur, motion_between(cur, prev, ctx.W, ctx.H, _face_px(ctx))


def _intensity(item: dict) -> float:
    return _num(item, "intensity", 1.0, 0.0, 1.5)


# ---------------------------------------------------------------- zoom
def _zoom_m(item: dict, lt: float, dur: float, ctx, anchor=None) -> skia.Matrix:
    k = _intensity(item)
    z0 = _num(item, "from", 1.0, 1.0, 4.0)
    z1 = _num(item, "to", 1.15, 1.0, 4.0)
    z0, z1 = 1.0 + (z0 - 1.0) * k, 1.0 + (z1 - 1.0) * k
    ez = item.get("easing", "in_out_cubic")
    p = ease.ease(ez if isinstance(ez, str) else "in_out_cubic", lt / dur if dur > 0 else 1.0)
    s = max(1.0, ease.lerp(z0, z1, p))
    return _framed(s, max(z0, z1), item, ctx, anchor)


def zoom(item: dict, lt: float, dur: float, ctx):
    """Zoom suave de `from` a `to` (escala) con easing (default in_out_cubic), fijo en la cara
    (o llevandola a `target` [x, y] si se indica)."""
    return _with_motion(_zoom_m, item, lt, dur, ctx)


# ---------------------------------------------------------------- punch
def _punch_scale(item: dict, lt: float, dur: float) -> float:
    if lt < 0:
        return 1.0
    k = _intensity(item)
    amount = _num(item, "amount", 0.12, 0.0, 1.5) * k
    attack = _num(item, "attack", 0.0, 0.0, 2.0)       # 0 = resorte natural
    mode = item.get("mode", "spring")                  # spring | cut
    release = item.get("release", "smooth")            # smooth | cut
    hold = item.get("hold")
    rel_t = min(0.28, max(0.12, dur * 0.3))
    rel_start = math.inf
    if release != "cut":
        rel_start = dur - rel_t
        if hold is not None:
            h = _num(item, "hold", -1.0)
            if h >= 0:
                rel_start = min(rel_start, max(0.15, h))
    if mode == "cut":
        up = 1.0
    elif attack > 0:
        up = ease.out_back(min(1.0, lt / attack), 1.2)
    else:
        # resorte rapido: llega en ~0.09 s, pasa ~10% y se asienta en ~0.3 s
        up = ease.spring(lt, freq=_num(item, "freq", 4.2, 0.5, 20.0),
                         damping=_num(item, "damping", 0.5, 0.05, 0.99))
    s = 1.0 + amount * up
    if lt > rel_start:
        s = ease.lerp(s, 1.0, ease.in_out_cubic((lt - rel_start) / rel_t))
    return max(1.0, s)


def _punch_m(item: dict, lt: float, dur: float, ctx, anchor=None) -> skia.Matrix:
    s = _punch_scale(item, lt, dur)
    amount = _num(item, "amount", 0.12, 0.0, 1.5) * _intensity(item)
    return _framed(s, 1.0 + amount, item, ctx, anchor)


def punch(item: dict, lt: float, dur: float, ctx):
    """Golpe de zoom: entra rapido con resorte (un rebote) o de corte, sostiene y suelta suave.

    Campos: amount (escala extra, 0.12), hold (s que dura el golpe antes de soltar; null =
    hasta el final del item), mode (spring|cut), release (smooth|cut), attack (s; 0 =
    resorte), freq/damping del resorte, target [x, y] opcional (a donde lleva la cara al
    llegar al zoom maximo).
    """
    # en modo cut el golpe es un corte: sin estela en el primer cuadro (prev = cur)
    return _with_motion(_punch_m, item, lt, dur, ctx)


# ---------------------------------------------------------------- shake
_KNOTS: dict = {}


def _smooth_noise(key: str, t: float, freq: float, ctx) -> float:
    """Ruido de valor suave en [-1, 1]: nodos aleatorios (deterministas por seed+key) cada
    1/freq s interpolados con Catmull-Rom. Mismo t -> mismo valor, sin importar el cuadro."""
    x = max(0.0, t) * max(0.1, freq)
    i = int(math.floor(x))
    f = x - i
    ck = (int(getattr(ctx, "seed", 0) or 0), key)
    knots = _KNOTS.get(ck)
    if knots is None or len(knots) < i + 4:
        n = max(i + 4, 96)
        knots = ctx.rng(f"shake:{key}").uniform(-1.0, 1.0, n)
        # los dos primeros nodos fuertes y opuestos: el golpe inicial se lee claro
        knots[0] = math.copysign(max(0.7, abs(knots[0])), knots[0])
        knots[1] = -math.copysign(max(0.8, abs(knots[1])), knots[0])
        if len(_KNOTS) > 512:
            _KNOTS.clear()
        _KNOTS[ck] = knots
    p0, p1, p2, p3 = (knots[max(0, i - 1)], knots[i], knots[i + 1], knots[i + 2])
    v = 0.5 * ((2 * p1) + (-p0 + p2) * f + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f * f
               + (-p0 + 3 * p1 - 3 * p2 + p3) * f * f * f)
    return max(-1.0, min(1.0, v))


def _shake_rate(item: dict) -> float:
    """`decay` del plan en [0, 1] (0 = constante, 1 = muere en ~0.3 s): tasa 6*decay por
    segundo. Valores fuera de rango se acotan (una sola semantica, monotona)."""
    return 6.0 * _num(item, "decay", 0.6, 0.0, 1.0)


def _shake_env(item: dict, lt: float, dur: float) -> float:
    if lt < 0:
        return 0.0
    rate = _shake_rate(item)
    tin = _num(item, "attack", 0.0, 0.0, 2.0)
    env = math.exp(-rate * lt) if rate > 0 else 1.0
    if tin > 0:
        env *= ease.out_quad(lt / tin)
    tout = min(0.12, dur * 0.3)
    if dur > 0 and lt > dur - tout:
        env *= 1.0 - ease.smoothstep((lt - (dur - tout)) / tout)
    return max(0.0, env)


def _shake_m(item: dict, lt: float, dur: float, ctx, anchor=None) -> skia.Matrix:
    k = _intensity(item)
    amp = ctx.px(_num(item, "amp", 10.0, 0.0, 200.0)) * k   # px de referencia
    freq = _num(item, "freq", 12.0, 0.2, 60.0)
    rot = _num(item, "rot", 0.8, 0.0, 10.0) * k             # grados maximos
    env = _shake_env(item, lt, dur)
    if env <= 1e-4 or amp <= 0:
        return skia.Matrix()
    key = _item_key(item)
    t = max(0.0, lt)
    dx = _smooth_noise(key + ":x", t, freq, ctx) * amp * env
    dy = _smooth_noise(key + ":y", t + 0.37, freq * 0.9, ctx) * amp * env * 0.8
    th = math.radians(_smooth_noise(key + ":r", t + 0.71, freq * 0.7, ctx) * rot * env)
    W, H = float(ctx.W), float(ctx.H)
    # escala minima para cubrir: rotacion (rectangulo 9:16) + traslacion maxima de la envolvente
    a, b = W / 2, H / 2
    th_max = math.radians(rot * env)
    s_rot = math.cos(th_max) + max(b / a, a / b) * math.sin(th_max)
    s = s_rot + max(amp * env / a, 0.8 * amp * env / b) + 0.002 * env
    cx, cy = W / 2, H / 2  # rota alrededor del centro (la cobertura se calcula asi)
    cos, sin = math.cos(th) * s, math.sin(th) * s
    # rota+escala alrededor de (cx, cy) y desplaza (dx, dy)
    return _m(cos, -sin, cx - cos * cx + sin * cy + dx, sin, cos, cy - sin * cx - cos * cy + dy)


def shake(item: dict, lt: float, dur: float, ctx):
    """Sacudida de camara: amp (px ref), freq (Hz), decay (0..1; 0 = constante), rot (grados),
    attack (s). Ruido suave determinista; la escala de cobertura sigue a la envolvente (sin
    bordes). El primer cuadro lleva estela (antes del golpe la camara estaba quieta)."""
    return _with_motion(_shake_m, item, lt, dur, ctx, prev_identity=True)


# ---------------------------------------------------------------- ken burns
def window_matrix(s: float, wx: float, wy: float, W: float, H: float) -> skia.Matrix:
    """Matriz que muestra en todo el cuadro la ventana del base de tamano (W/s, H/s) centrada
    en (wx, wy) px. El centro se acota para que la ventana no salga del cuadro base."""
    s = max(1.0, s)
    hw, hh = W / (2 * s), H / (2 * s)
    wx = min(W - hw, max(hw, wx))
    wy = min(H - hh, max(hh, wy))
    return _m(s, 0.0, W / 2 - s * wx, 0.0, s, H / 2 - s * wy)


def _kenburns_m(item: dict, lt: float, dur: float, ctx, anchor=None) -> skia.Matrix:
    """Ventana que hace zoom `from`->`to` y recorre `pan` alrededor del ancla.

    Sin inflar la escala: el margen disponible en cada instante es m(s) = (1 - 1/s)/2 (fraccion
    del cuadro que la ventana puede separarse del centro). La trayectoria deseada d(p) (ancla +
    paneo) se acota UNA vez contra el margen del zoom maximo y se escala por m(s(p)) / m_max:
    nunca choca con el borde (sin quiebres de velocidad) y con from=1 empieza en identidad."""
    k = _intensity(item)
    z0 = 1.0 + (_num(item, "from", 1.0, 1.0, 4.0) - 1.0) * k
    z1 = 1.0 + (_num(item, "to", 1.12, 1.0, 4.0) - 1.0) * k
    pan = _vec2(item.get("pan"), (0.02, -0.01))
    px_, py_ = float(np.clip(pan[0], -1, 1)) * k, float(np.clip(pan[1], -1, 1)) * k
    ez = item.get("easing", "in_out_quad")
    p = ease.ease(ez if isinstance(ez, str) else "in_out_quad", lt / dur if dur > 0 else 1.0)
    s = max(1.0, ease.lerp(z0, z1, p))
    W, H = float(ctx.W), float(ctx.H)
    m_max = (1.0 - 1.0 / max(1.0, z0, z1)) / 2.0
    if m_max <= 1e-6:
        return skia.Matrix()
    ax, ay = anchor if anchor is not None else _anchor(item, ctx)
    out = []
    for a, pn in ((ax / W, px_), (ay / H, py_)):
        pe = float(np.clip(pn, -2 * m_max, 2 * m_max))           # recorrido posible
        lim = m_max - abs(pe) / 2
        a0 = float(np.clip(a - 0.5, -lim, lim))                   # ancla acotada una vez
        u = (a0 + (p - 0.5) * pe) / m_max                         # en [-1, 1]
        out.append(0.5 + (1.0 - 1.0 / s) / 2.0 * u)
    return window_matrix(s, out[0] * W, out[1] * H, W, H)


def kenburns(item: dict, lt: float, dur: float, ctx):
    """Ken Burns: zoom lento `from`->`to` + paneo `pan` [dx, dy] (fraccion del cuadro que
    recorre la ventana), centrado en la cara. Sin zoom (from = to = 1) no hay margen para
    panear y la matriz es identidad."""
    return _with_motion(_kenburns_m, item, lt, dur, ctx)


def whip_matrix(p: float, direction: str, ctx, strength: float = 1.0,
                dur: float = 0.35) -> tuple[skia.Matrix, Motion]:
    """Ayuda para transiciones tipo whip pan: desplazamiento de `p` en [0,1] (in_out_expo)
    en la direccion dada, con el vector de movimiento para el motion blur (dur = duracion de
    la transicion en s, para escalar bien el vector). El contenido se repite con espejo fuera
    de cuadro (usar border='reflect' en warp)."""
    dxy = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}.get(direction, (1, 0))
    dt = 1.0 / max(1.0, float(getattr(ctx, "fps", 30.0))) / max(0.02, float(dur))

    def off(q):
        e = ease.in_out_expo(q)
        return dxy[0] * e * ctx.W * strength, dxy[1] * e * ctx.H * strength

    x1, y1 = off(p)
    x0, y0 = off(max(0.0, p - dt))
    cur, prev = _m(1, 0, x1, 0, 1, y1), _m(1, 0, x0, 0, 1, y0)
    return cur, Motion(x1 - x0, y1 - y0, cur, prev)


MATRIX_EFFECTS = {"zoom": zoom, "punch": punch, "shake": shake, "kenburns": kenburns}


# ---------------------------------------------------------------- composicion y aplicacion
def combine(results, ctx=None) -> tuple[skia.Matrix, Motion]:
    """Compone varios (M, Motion) en orden de aplicacion (el primero se aplica primero al
    cuadro base) y recalcula el vector de movimiento total."""
    results = [r for r in (results or []) if r is not None and r[0] is not None]
    if not results:
        return skia.Matrix(), Motion(0.0, 0.0, skia.Matrix(), skia.Matrix())
    cur = concat(*[r[0] for r in results])
    prevs = []
    for M, mv in results:
        pm = getattr(mv, "prev", None)
        if pm is None:
            # vector simple (sin matrices): el cuadro anterior es M desplazado -v
            try:
                vx, vy = (float(mv[0]), float(mv[1])) if mv is not None else (0.0, 0.0)
            except (TypeError, ValueError, IndexError):
                vx = vy = 0.0
            pm = concat(M, _m(1, 0, -vx, 0, 1, -vy))
        prevs.append(pm)
    prev = concat(*prevs)
    if ctx is None:
        # sin ctx: suma simple de vectores
        mx = sum(float(r[1][0]) for r in results if r[1] is not None)
        my = sum(float(r[1][1]) for r in results if r[1] is not None)
        return cur, Motion(mx, my, cur, prev)
    return cur, motion_between(cur, prev, ctx.W, ctx.H, _face_px(ctx))


def _as_rgba(img) -> np.ndarray:
    if isinstance(img, np.ndarray):
        return img
    return C.image_to_rgba(img)


_BORDERS = {"replicate": cv2.BORDER_REPLICATE, "reflect": cv2.BORDER_REFLECT_101,
            "constant": cv2.BORDER_CONSTANT}
CUBIC_FROM = 1.2          # escala desde la que el warp final usa bicubico (antes, bilineal)
# ultimo warp de este proceso: motion_blur lo reconoce y muestrea del cuadro BASE (contenido
# real fuera del encuadre, un solo remuestreo) en lugar de la imagen ya deformada
_STASH: dict = {"out": None, "src": None, "A": None, "border": None}


def warp(img, M: skia.Matrix | None, ctx, border: str = "replicate") -> skia.Image:
    """Aplica la matriz de camara al cuadro (skia.Image o np RGBA) -> skia.Image W x H.

    cv2.warpAffine: bicubico en final cuando el zoom amplia >= CUBIC_FROM (conserva nitidez),
    bilineal en zooms chicos, sacudidas y draft (la diferencia no se ve y cuesta la mitad).
    """
    if is_identity(M):
        return img if not isinstance(img, np.ndarray) else C.image_from_rgba(img)
    arr = _as_rgba(img)
    s = matrix_scale(M)
    cubic = not getattr(ctx, "draft", False) and s >= CUBIC_FROM
    A = to_affine(M)
    out = cv2.warpAffine(arr, A, (int(ctx.W), int(ctx.H)),
                         flags=cv2.INTER_CUBIC if cubic else cv2.INTER_LINEAR,
                         borderMode=_BORDERS.get(border, cv2.BORDER_REPLICATE))
    res = C.image_from_rgba(out)
    _STASH.update(out=res, src=arr, A=A, border=border)
    return res


def warp_mask(mask: np.ndarray | None, M: skia.Matrix | None, ctx) -> np.ndarray | None:
    """Misma transformacion para la mascara del sujeto (float32 HxW)."""
    if mask is None or is_identity(M):
        return mask
    return cv2.warpAffine(mask, to_affine(M), (int(ctx.W), int(ctx.H)), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def warp_point(M: skia.Matrix, x: float, y: float) -> tuple[float, float]:
    p = M.mapXY(float(x), float(y))
    return p.x(), p.y()


def map_face(face, M: skia.Matrix, W: float, H: float):
    """Cara normalizada del cuadro base -> cuadro de salida tras la camara."""
    try:
        if face is None or M is None or len(face) < 4 or not all(math.isfinite(float(v)) for v in face[:4]):
            return face
    except (TypeError, ValueError):
        return face
    v = M.get9()
    sx = math.hypot(v[0], v[3])
    sy = math.hypot(v[1], v[4])
    x, y = warp_point(M, face[0] * W, face[1] * H)
    return (x / W, y / H, face[2] * sx, face[3] * sy)


SHUTTER = 0.5  # 180 grados


def needs_blur(v, ctx, shutter: float = SHUTTER, threshold_px: float | None = None) -> bool:
    """True si el movimiento por cuadro deja una estela visible (>= ~4 px de referencia)."""
    if v is None:
        return False
    thr = ctx.px(4.0) if threshold_px is None else threshold_px
    return math.hypot(v[0], v[1]) * shutter >= thr


def _h3(A: np.ndarray) -> np.ndarray:
    return np.vstack([A[:2], [0.0, 0.0, 1.0]])


def _blur_plan(speed: float, ctx, max_samples: int | None) -> tuple[int, int]:
    """(ds, n): factor de reduccion y numero de muestras del obturador. Estelas cortas a media
    resolucion (hasta 8 muestras); largas a un cuarto (hasta 12): siempre sin escalones
    visibles y con costo acotado en un hilo."""
    s = max(0.25, ctx.W / C.REF_W)
    long_ = speed / s >= 24.0
    ds = max(2, int(round((4 if long_ else 2) * s)))
    nmax = max_samples or (12 if long_ else 8)
    n = int(max(3, min(nmax, math.ceil(speed / ds / 1.5) + 1)))
    return ds, n


def _motion_weight(A_cur3: np.ndarray, A_prev3: np.ndarray | None, speed: float, W: int, H: int,
                   ctx, shutter: float) -> np.ndarray:
    """Peso por pixel (rejilla 1/16) de la version barrida: estela local = |x - M_prev M_cur^-1 x|.
    En un punch el ancla del zoom queda nitida y las orillas se barren."""
    gh, gw = max(8, H // 16), max(8, W // 16)
    if A_prev3 is not None:
        R1 = A_prev3 @ np.linalg.inv(A_cur3)
        ys, xs = np.mgrid[0:gh, 0:gw].astype(np.float64)
        X = (xs + 0.5) * (W / gw)
        Y = (ys + 0.5) * (H / gh)
        dx = X - (R1[0, 0] * X + R1[0, 1] * Y + R1[0, 2])
        dy = Y - (R1[1, 0] * X + R1[1, 1] * Y + R1[1, 2])
        mag = np.hypot(dx, dy) * shutter
    else:
        mag = np.full((gh, gw), speed)
    sp = max(0.25, ctx.W / C.REF_W)
    wmap = np.clip((mag - 2.0 * sp) / (6.0 * sp), 0.0, 1.0).astype(np.float32)
    return wmap * wmap * (3 - 2 * wmap)


def motion_blur(img, v, ctx, shutter: float = SHUTTER, max_samples: int | None = None,
                base=None, M: skia.Matrix | None = None) -> skia.Image:
    """Motion blur de obturador: promedia el cuadro re-muestreado a lo largo del movimiento.

    - Si `v` trae matrices (.cur/.prev, como las de MATRIX_EFFECTS o combine), integra la
      transformacion real entre cuadros (traslacion, zoom radial y rotacion correctos).
      Muestrea del cuadro BASE (sin camara) cuando lo tiene: `base` explicito (con `M` = la
      matriz del warp, default v.cur) o, si `img` es la salida del ultimo warp(), el base de
      ese warp. Asi hay contenido real fuera del encuadre (sin estelas estiradas en las
      orillas) y un solo remuestreo. Sin base, re-muestrea `img`.
    - Si `v` es una tupla simple (mx, my), hace blur direccional por traslacion.
    Obturador centrado (sin retraso de la posicion nitida). Integra a 1/2 o 1/4 de resolucion
    y mezcla con el cuadro nitido segun la estela por pixel (la cara en el ancla de un punch
    queda nitida y las orillas se barren).
    """
    if v is None:
        return img
    try:
        speed = math.hypot(float(v[0]), float(v[1])) * shutter
    except (TypeError, ValueError, IndexError):
        return img
    if speed < 0.75:
        return img
    W, H = int(ctx.W), int(ctx.H)
    cur = getattr(v, "cur", None)
    prev = getattr(v, "prev", None)
    exact = cur is not None and prev is not None
    src, from_base, border = None, False, cv2.BORDER_REPLICATE
    A_cur3 = A_prev3 = None
    if exact:
        A_cur3, A_prev3 = _h3(to_affine(cur)), _h3(to_affine(prev))
        A_real = None
        if base is not None:
            src = _as_rgba(base)
            A_real = _h3(to_affine(M)) if M is not None else A_cur3
        elif _STASH["out"] is not None and _STASH["out"] is img:
            src = _STASH["src"]
            A_real = _h3(_STASH["A"])
            border = _BORDERS.get(_STASH["border"], cv2.BORDER_REPLICATE)
        if src is not None and src.shape[:2] == (H, W):
            # correccion del warp real (p. ej. un guard de cobertura) aplicada tambien al previo
            G = A_real @ np.linalg.inv(A_cur3)
            A_cur3, A_prev3 = A_real, G @ A_prev3
            from_base = True
        else:
            src = None
    wmap = _motion_weight(A_cur3, A_prev3, speed, W, H, ctx, shutter) if exact else \
        _motion_weight(np.eye(3), None, speed, W, H, ctx, shutter)
    if float(wmap.max()) < 0.005:
        return img
    ds, n = _blur_plan(speed, ctx, max_samples)
    if src is None:
        src = _as_rgba(img)
    sw, sh = max(8, W // ds), max(8, H // ds)
    small = src if ds == 1 else cv2.resize(src, (sw, sh), interpolation=cv2.INTER_AREA)
    S = np.diag([1.0 / ds, 1.0 / ds, 1.0])
    Si = np.diag([float(ds), float(ds), 1.0])
    acc = np.zeros((sh, sw, src.shape[2]), np.float32)
    for i in range(n):
        s = (i / (n - 1) - 0.5) * shutter  # [-shutter/2, +shutter/2] en fracciones de cuadro
        if exact:
            # s < 0 interpola hacia el cuadro anterior; s > 0 extrapola (obturador centrado)
            A_s = A_cur3 + (A_cur3 - A_prev3) * s
            if from_base:
                R = (S @ A_s @ Si)[:2]
                w = cv2.warpAffine(small, R, (sw, sh), flags=cv2.INTER_LINEAR, borderMode=border)
            else:
                # de la salida en el instante s a la imagen ya deformada
                R = (S @ (A_cur3 @ np.linalg.inv(A_s)) @ Si)[:2]
                w = cv2.warpAffine(small, R, (sw, sh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                   borderMode=border)
        else:
            R = np.array([[1, 0, -float(v[0]) * s / ds], [0, 1, -float(v[1]) * s / ds]], np.float64)
            w = cv2.warpAffine(small, R, (sw, sh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=border)
        cv2.accumulate(w, acc)
    out = cv2.convertScaleAbs(acc, alpha=1.0 / n)
    up = out if ds == 1 else cv2.resize(out, (W, H), interpolation=cv2.INTER_LINEAR)
    if float(wmap.min()) > 0.995:
        return C.image_from_rgba(up)
    sharp = _as_rgba(img)
    wfull = cv2.resize(wmap, (W, H), interpolation=cv2.INTER_LINEAR)
    return C.image_from_rgba(cv2.blendLinear(up, sharp, wfull, 1.0 - wfull))


def apply_camera(img, M: skia.Matrix | None, mv, ctx, border: str = "replicate") -> skia.Image:
    """warp + motion blur en una llamada: si todo el cuadro se barre, no calcula el warp
    nitido; si no, muestrea el blur del base y lo mezcla con el warp nitido."""
    if not needs_blur(mv, ctx):
        return warp(img, M, ctx, border)
    cur = getattr(mv, "cur", None)
    prev = getattr(mv, "prev", None)
    if cur is not None and prev is not None and M is not None:
        A_real = _h3(to_affine(M))
        G = A_real @ np.linalg.inv(_h3(to_affine(cur)))
        speed = math.hypot(mv[0], mv[1]) * SHUTTER
        wmap = _motion_weight(A_real, G @ _h3(to_affine(prev)), speed, int(ctx.W), int(ctx.H), ctx, SHUTTER)
        if float(wmap.min()) > 0.995:
            return motion_blur(img, mv, ctx, base=img, M=M)
    base = _as_rgba(img)
    sharp = warp(base, M, ctx, border)
    return motion_blur(sharp, mv, ctx, base=base, M=M if M is not None else cur)


# ---------------------------------------------------------------- efectos de post
def _env_pulse(lt: float, dur: float, attack: float = 0.0, fps: float = 30.0) -> float:
    """1 al inicio y cae suave hasta 0 al final (con ataque opcional; el primer cuadro ya
    tiene efecto)."""
    if dur <= 0:
        return 0.0
    if attack > 0 and lt < attack:
        dt = 1.0 / max(1.0, fps)
        return ease.out_quad((lt + dt) / (attack + dt))
    p = (lt - attack) / max(1e-3, dur - attack)
    return max(0.0, 1.0 - ease.out_cubic(p)) if p < 1 else 0.0


def rgb_split(img, item: dict, lt: float, dur: float, ctx) -> skia.Image:
    """Aberracion cromatica: rojo y azul se separan (lineal en `angle` + radial) y vuelven.

    Campos: amount (px ref, 8), angle (grados, 0 = horizontal), radial (0..1, 0.35),
    jitter (0..1: variacion por cuadro tipo glitch, 0), hold (bool: sin decaimiento).
    """
    item = item if isinstance(item, dict) else {}
    k = _intensity(item)
    amt = ctx.px(_num(item, "amount", 8.0, 0.0, 200.0)) * k
    env = 1.0 if item.get("hold") is True else _env_pulse(lt, dur, _num(item, "attack", 0.04, 0.0, 2.0),
                                                          float(getattr(ctx, "fps", 30.0)))
    jit = _num(item, "jitter", 0.0, 0.0, 1.0)
    if jit > 0:
        r = ctx.rng(f"{_item_key(item)}:{ctx.n}")
        env *= 1.0 - jit + jit * float(r.uniform(0.2, 1.6))
    d = amt * env
    if d < 0.35:
        return img
    arr = _as_rgba(img)
    Hh, Ww = arr.shape[:2]
    ang = math.radians(_num(item, "angle", 0.0))
    ux, uy = math.cos(ang), math.sin(ang)
    radial = _num(item, "radial", 0.35, 0.0, 1.0)
    kr = radial * d / (0.5 * max(Ww, Hh))  # escala relativa en el borde
    cx, cy = Ww / 2.0, Hh / 2.0
    lin = d * (1.0 - radial * 0.5)
    ch = cv2.split(arr)

    def shifted(c, sgn):
        s = 1.0 + sgn * kr
        A = np.array([[s, 0, cx * (1 - s) + sgn * ux * lin], [0, s, cy * (1 - s) + sgn * uy * lin]], np.float64)
        return cv2.warpAffine(c, A, (Ww, Hh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    out = cv2.merge([shifted(ch[0], 1.0), ch[1], shifted(ch[2], -1.0), ch[3]])
    return C.image_from_rgba(out)


def flash_frame(img, item: dict, lt: float, dur: float, ctx) -> skia.Image:
    """Destello: sobreexpone y funde a `color` (screen) con ataque de ~1-2 cuadros y caida
    rapida con cola suave. Campos: color (#FFFFFF), peak (0..1, 0.95), attack (s, 0.035),
    bloom (0..1: sobreexposicion extra, 0.8)."""
    item = item if isinstance(item, dict) else {}
    k = _intensity(item)
    peak = _num(item, "peak", 0.95, 0.0, 1.0) * min(1.0, k)
    attack = _num(item, "attack", 0.035, 0.0, 2.0)
    dt = 1.0 / max(1.0, float(getattr(ctx, "fps", 30.0)))
    if lt < attack:
        a = ease.out_quad((lt + dt) / (attack + dt))  # el primer cuadro ya destella
    else:
        p = (lt - attack) / max(1e-3, dur - attack)
        a = (1.0 - min(1.0, max(0.0, p))) ** 2.4
    a *= peak
    if a < 0.004:
        return img
    arr = _as_rgba(img)
    bloom = _num(item, "bloom", 0.8, 0.0, 2.0)
    lut = _flash_lut(round(a, 3), round(bloom, 3), _rgb(item.get("color", "#FFFFFF")))
    return C.image_from_rgba(cv2.LUT(arr, lut))


def _flash_lut(a: float, bloom: float, rgb: tuple) -> np.ndarray:
    """LUT por canal del destello: sobreexposicion (bloom) y screen hacia el color."""
    x = np.arange(256, dtype=np.float64) / 255.0
    gain = 1.0 + bloom * 1.6 * a
    lut = np.empty((256, 1, 4), np.uint8)
    for c in range(3):
        v = np.clip(x * gain, 0.0, 1.0)
        v = 1.0 - (1.0 - v) * (1.0 - a * rgb[c] / 255.0)
        lut[:, 0, c] = np.clip(np.round(v * 255.0), 0, 255)
    lut[:, 0, 3] = np.arange(256)
    return lut


POST_EFFECTS = {"rgb_split": rgb_split, "flash_frame": flash_frame}


# ---------------------------------------------------------------- seguimiento de rostro
def _yunet(w: int, h: int, score: float = 0.6):
    import paths
    det = cv2.FaceDetectorYN.create(str(paths.YUNET), "", (w, h), score, 0.3, 5000)
    return det


def _pick_face(faces: np.ndarray | None, prev: np.ndarray | None, w: int, h: int):
    """Elige la cara principal: grande y con buen score, con bonus por continuidad."""
    if faces is None or len(faces) == 0:
        return None
    best, best_s = None, -1e9
    for f in faces:
        x, y, fw, fh, sc = f[0], f[1], f[2], f[3], f[14]
        cx, cy = (x + fw / 2) / w, (y + fh / 2) / h
        area = (fw / w) * (fh / h)
        s = math.sqrt(max(area, 1e-6)) * 3.0 + sc
        if prev is not None and np.all(np.isfinite(prev)):
            s -= 2.0 * math.hypot(cx - prev[0], cy - prev[1])
        if s > best_s:
            best_s, best = s, np.array([cx, cy, fw / w, fh / h], np.float64)
    return best


def _gauss_smooth(x: np.ndarray, sigma: float) -> np.ndarray:
    """Suavizado gaussiano de fase cero (sin retraso) de una serie 1D con bordes replicados."""
    if sigma <= 0 or len(x) < 3:
        return x
    r = int(math.ceil(sigma * 3))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    xp = np.pad(x, r, mode="edge")
    return np.convolve(xp, k, mode="valid")


def track_postprocess(samples: np.ndarray, sample_frames: np.ndarray, n_frames: int, fps: float,
                      max_gap_s: float = 0.6, sigma_s: float = 0.15, cuts=None) -> np.ndarray:
    """Muestras (k, 4) con NaN -> pista por cuadro (n_frames, 4) suavizada.

    - Parte en segmentos donde hay salto brusco (corte de toma) o hueco largo sin cara.
    - Dentro de un segmento: mediana de 3 contra picos, huecos cortos interpolados,
      suavizado gaussiano de fase cero e interpolacion lineal a todos los cuadros.
    - Entre segmentos: cada cuadro toma el segmento mas cercano (no interpola a traves
      de un corte). Sin cara por mas de max_gap_s: NaN.
    - cuts: cuadros donde empieza una toma nueva (opcional): nunca se suaviza ni se
      extiende una pista a traves de ellos.
    """
    cuts = sorted(int(c) for c in (cuts or []) if 0 < int(c) < n_frames)
    out = np.full((n_frames, 4), np.nan, np.float64)
    if len(samples) == 0:
        return out
    valid = np.all(np.isfinite(samples), axis=1)
    idx = np.nonzero(valid)[0]
    if len(idx) == 0:
        return out
    step = float(np.median(np.diff(sample_frames))) if len(sample_frames) > 1 else 1.0
    max_gap = max(1, int(round(max_gap_s * fps / max(1.0, step))))
    segs, cur = [], [idx[0]]
    for a, b in zip(idx[:-1], idx[1:]):
        pa, pb = samples[a], samples[b]
        jump = math.hypot(pb[0] - pa[0], pb[1] - pa[1]) > max(0.1, 1.2 * pa[2])
        size = max(pb[2], pa[2]) / max(1e-6, min(pb[2], pa[2])) > 1.6
        fa, fb = sample_frames[a], sample_frames[b]
        crosses = any(fa < c <= fb for c in cuts)
        if (b - a) > max_gap or jump or size or crosses:
            segs.append(cur)
            cur = [b]
        else:
            cur.append(b)
    segs.append(cur)
    sig = sigma_s * fps / max(1.0, step)
    spans = []
    for sg in segs:
        sg = np.array(sg)
        k0, k1 = sg[0], sg[-1]
        ks = np.arange(k0, k1 + 1)
        vals = np.empty((len(ks), 4))
        for c in range(4):
            v = np.interp(ks, sg, samples[sg, c])
            if len(v) >= 3:
                med = np.median(np.stack([np.r_[v[0], v[:-1]], v, np.r_[v[1:], v[-1]]]), axis=0)
                v = med
            vals[:, c] = _gauss_smooth(v, sig)
        fr = sample_frames[ks].astype(np.float64)
        spans.append((fr, vals))
    # asignar cuadros: dentro de cada span interpolado; fuera, el span mas cercano si esta
    # a menos de medio paso (hold corto), y entre spans el mas cercano hasta max_gap
    frames = np.arange(n_frames, dtype=np.float64)
    best_d = np.full(n_frames, np.inf)
    hold = step * 0.5 + 0.5
    reach_gap = max_gap * step * 0.5
    for i, (fr, vals) in enumerate(spans):
        lo, hi = fr[0], fr[-1]
        inside = (frames >= lo) & (frames <= hi)
        d = np.where(inside, 0.0, np.minimum(np.abs(frames - lo), np.abs(frames - hi)))
        # limite de alcance: hacia un hueco entre segmentos llega hasta la mitad del hueco, o
        # hasta el corte de toma si hay uno en medio (sin pasar de reach_gap)
        prev_hi = spans[i - 1][0][-1] if i > 0 else None
        next_lo = spans[i + 1][0][0] if i + 1 < len(spans) else None
        cut_l = max((c for c in cuts if c <= lo), default=None)
        cut_r = min((c for c in cuts if c > hi), default=None)
        if prev_hi is None:
            left_lim = lo - hold
        elif cut_l is not None and cut_l > prev_hi:
            left_lim = lo - min(reach_gap, lo - cut_l)
        else:
            left_lim = lo - min(reach_gap, (lo - prev_hi) / 2.0)
        if next_lo is None:
            right_lim = hi + hold
        elif cut_r is not None and cut_r <= next_lo:
            right_lim = hi + min(reach_gap, cut_r - 1 - hi)
        else:
            right_lim = hi + min(reach_gap, (next_lo - hi) / 2.0)
        if cut_l is not None:
            left_lim = max(left_lim, cut_l)
        if cut_r is not None:
            right_lim = min(right_lim, cut_r - 1)
        ok = (frames >= left_lim) & (frames <= right_lim) & (d < best_d)
        if not ok.any():
            continue
        for c in range(4):
            out[ok, c] = np.interp(frames[ok], fr, vals[:, c])
        best_d[ok] = d[ok]
    return out


def detect_cuts(thumbs: np.ndarray, min_gap: int = 6) -> list[int]:
    """Cortes de toma a partir de miniaturas por cuadro (n, h, w[, 3]) uint8.

    Diferencia media absoluta entre cuadros seguidos (luma + color). Un corte es un pico
    que supera un umbral absoluto y es varias veces mayor que la actividad local (camara en
    mano, persona caminando). Devuelve los cuadros donde EMPIEZA cada toma nueva."""
    n = len(thumbs)
    if n < 3:
        return []
    t = thumbs.astype(np.float32)
    d = np.zeros(n, np.float32)
    d[1:] = np.abs(t[1:] - t[:-1]).reshape(n - 1, -1).mean(axis=1)
    # histograma de color grueso: robusto a movimiento, sensible al cambio de escena
    if t.ndim == 4:
        q = (thumbs // 64).astype(np.int32)
        codes = q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]
        hist = np.stack([np.bincount(c.ravel(), minlength=64) for c in codes]).astype(np.float32)
        hist /= np.maximum(1.0, hist.sum(axis=1, keepdims=True))
        hd = np.zeros(n, np.float32)
        hd[1:] = 0.5 * np.abs(hist[1:] - hist[:-1]).sum(axis=1)       # 0..1
    else:
        hd = np.zeros(n, np.float32)
    cuts = []
    r = 8
    for i in range(1, n):
        lo, hi = max(1, i - r), min(n, i + r + 1)
        loc = np.concatenate([d[lo:i], d[i + 1:hi]])
        base = float(np.median(loc)) if len(loc) else 0.0
        thr = max(18.0, 4.0 * base + 8.0)
        big = d[i] >= thr and d[i] >= float(d[lo:hi].max()) - 1e-6
        # misma escena con otro encuadre (mismo cuarto): el histograma cambia poco, pero el
        # salto de pixeles es enorme frente a la actividad local
        if big and (hd[i] >= 0.05 or d[i] >= 2.5 * thr):
            if cuts and i - cuts[-1] < min_gap:
                if d[i] > d[cuts[-1]]:
                    cuts[-1] = i
                continue
            cuts.append(i)
    return cuts


def face_track(base_video, W: int, H: int, fps: float, n_frames: int, sample_fps: float = 10.0,
               cache_dir=None, det_width: int = 480, cuts=None, return_cuts: bool = False,
               on_pct=None, cancel=None):
    """Pista de rostro en el video base (ya reencuadrado a W x H).

    YuNet a ~sample_fps sobre cuadros reducidos (det_width px de ancho), interpolacion y
    suavizado de fase cero. Devuelve (n_frames, 4) = (cx, cy, w, h) normalizados; NaN sin cara.
    cuts: None = detectarlos (cada cuadro se compara con el anterior en miniatura: la pista
    nunca se suaviza ni se extiende a traves de un corte de toma); [] = sin cortes; o la
    lista de cuadros de inicio de cada union. return_cuts: devuelve (pista, cortes).
    on_pct(p 0..100): avance (cada ~16 cuadros leidos; si lanza, se propaga: cancelacion).
    cancel() -> True: corta la lectura y lanza InterruptedError (no se escribe cache).
    """
    import hashlib

    import paths

    base_video = str(base_video)
    n_frames = int(n_frames)
    cache_file = None
    cut_spec = "auto" if cuts is None else sorted(int(c) for c in cuts)
    if cache_dir:
        st = Path(base_video).stat()
        key = (f"{Path(base_video).name}_{st.st_size}_{int(st.st_mtime)}_{W}x{H}_{n_frames}_{sample_fps}"
               f"_{cut_spec}_v2")
        cache_file = Path(cache_dir) / f"face_{hashlib.sha1(key.encode()).hexdigest()[:16]}.npy"
        if cache_file.exists():
            try:
                arr = np.load(cache_file)
                cf = cache_file.with_suffix(".cuts.npy")
                if arr.shape == (n_frames, 4) and (not return_cuts or cf.exists()):
                    if return_cuts:
                        return arr, [int(c) for c in np.load(cf)]
                    return arr
            except Exception:
                pass
    step = max(1, int(round(float(fps) / sample_fps)))
    dw = int(min(W, det_width))
    dw -= dw % 2
    dh = int(round(dw * H / W / 2) * 2)
    auto = cuts is None
    vf = f"scale={dw}:{dh}:flags=area" if auto else f"select=not(mod(n\\,{step})),scale={dw}:{dh}:flags=area"
    cmd = [paths.FFMPEG, "-v", "error", "-nostdin", "-i", base_video, "-an", "-vf", vf,
           "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    det = _yunet(dw, dh)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=dw * dh * 6)
    fsz = dw * dh * 3
    samples, sframes, thumbs = [], [], []
    prev = None
    k = 0
    try:
        while True:
            buf = proc.stdout.read(fsz)
            if not buf or len(buf) < fsz:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)
            fn = k if auto else k * step
            if auto:
                thumbs.append(cv2.resize(frame, (24, 42), interpolation=cv2.INTER_AREA))
            if fn % step == 0:
                _, faces = det.detect(frame)
                f = _pick_face(faces, prev, dw, dh)
                if f is not None:
                    prev = f
                    samples.append(f)
                else:
                    samples.append(np.full(4, np.nan))
                sframes.append(fn)
            k += 1
            if (k if auto else k * step) >= n_frames:
                break
            if k % 16 == 0:
                if cancel is not None and cancel():
                    raise InterruptedError("face_track cancelado")
                if on_pct is not None:
                    on_pct(min(99.0, 100.0 * (k if auto else k * step) / max(1, n_frames)))
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()
    found = detect_cuts(np.stack(thumbs)) if auto and len(thumbs) > 2 else list(cuts or [])
    arr = track_postprocess(np.array(samples).reshape(-1, 4), np.array(sframes), n_frames, float(fps),
                            cuts=found)
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_file, arr)
        np.save(cache_file.with_suffix(".cuts.npy"), np.array(found, np.int64))
    if return_cuts:
        return arr, [int(c) for c in found]
    return arr
