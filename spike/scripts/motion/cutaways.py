"""Escenas a pantalla completa (cutaways) y fondos animados reutilizables.

Contrato (docs/MOTION-ENGINE.md 3.1 y 4.2b):
    RENDERERS[kind](canvas, item, lt, dur, ctx) -> None     kind en {title_card, info_card, photo, self_broll}
    draw_background(canvas, kind, colors, lt, ctx) -> None   (lo reutilizan bg_replace y title cards)

Fondos (`background`): grid_warp, gradient, paper, dark_stars, rays, blur_self, color, halftone.
`blur_self`, `photo` y `self_broll` usan ctx.base_image() (cuadro base ya procesado).

Semantica UNIFICADA de `bg_colors` (la misma en todos los fondos; ver BG_COLOR_ROLES):
  indice 0 = color dominante del fondo; indice 1 = acento (tinte secundario);
  indice 2 (opcional) = tinta de lineas/puntos/estrellas. Si no se da, la tinta se deriva del
  fondo por contraste (casi blanco sobre oscuro, casi negro sobre claro). Asi el director puede
  mandar siempre [oscuro, acento] y cada fondo se ve como en el video objetivo (reticula blanca
  sobre negro, estrellas blancas con nebulosa del acento, papel oscuro con relieve, etc.).
  gradient acepta hasta 4 colores (base + 3 manchas).

Campos que entiende este modulo (ademas de los comunes):
  - `background`, `bg_colors` [hex], `text` (str o item de text: preset + campos), `graphics` [items].
  - `anim_in` en {cut, fade, zoom, slide_up, slide_left, circle, wipe}; `anim_out` en
    {cut, fade, slide_down, slide_up, circle, zoom}; `in_dur`/`out_dur` (s).
  - Items embebidos (`text` y cada elemento de `graphics`) usan `offset` (s desde el inicio del
    cutaway, default 0 para graphics y 0.12 para text) y `length` (s, default hasta el final).
  - info_card: `card` (item de graphics.card) o los campos de card en el nivel superior
    (variant, title, subtitle, value, items). Si no hay ninguno, NO se dibuja tarjeta propia:
    la tarjeta viene en `graphics` (asi la arma el director).
  - photo: `caption`, `tilt` (grados), `_still` (skia.Image del cuadro al inicio del cutaway).
  - self_broll: `src_t`, `zoom_from`, `zoom_to` (>= 1), `pan` [dx,dy] (fraccion de W/H, acotado para
    cubrir siempre el cuadro), `_src_image` (skia.Image del cuadro de src_t).
  - NEEDS_FRAME declara que cuadro debe inyectar render.py por kind (ver frame_request()).
"""
from __future__ import annotations

import math

import numpy as np
import skia

from . import ease, graphics
from . import ctx as C

BACKGROUNDS = ("grid_warp", "gradient", "paper", "dark_stars", "rays", "blur_self", "color", "halftone")
KINDS = ("title_card", "info_card", "photo", "self_broll")
SCENE_IN = ("cut", "fade", "zoom", "slide_up", "slide_left", "circle", "wipe")
SCENE_OUT = ("cut", "fade", "slide_down", "slide_up", "circle", "zoom")

# Papel de cada indice de bg_colors por fondo (documentacion para schema/UI y el director).
BG_COLOR_ROLES = {
    "grid_warp": ("fondo", "acento (luz de la lente)", "lineas"),
    "gradient": ("base", "mancha 1", "mancha 2", "mancha 3"),
    "paper": ("papel", "acento (tinte de luz)"),
    "dark_stars": ("fondo", "acento (nebulosa)", "estrellas"),
    "rays": ("fondo", "rayos"),
    "blur_self": ("tinte de oscurecido",),
    "color": ("fondo", "acento (luz)"),
    "halftone": ("fondo", "puntos"),
}

# Cuadro que render.py debe inyectar como skia.Image antes de llamar al renderer:
#   photo:      item["_still"]     = cuadro compuesto base al INICIO del cutaway (congelado)
#   self_broll: item["_src_image"] = cuadro base de remap(src_t)
NEEDS_FRAME = {"photo": ("_still", "start"), "self_broll": ("_src_image", "src_t")}

# Valores recomendados por tipo (para schema.py / director).
DEFAULTS = {
    "title_card": {"background": "grid_warp", "anim_in": "cut", "anim_out": "cut"},
    "info_card": {"background": "gradient", "anim_in": "cut", "anim_out": "cut"},
    # photo: foto instantanea (polaroid) con cinta; `media` = imagen/video propio ({sid, type, w, h,
    # dur, crop?}: render inyecta item['_media_image']); sin media usa el cuadro del video al inicio
    "photo": {"background": "paper", "anim_in": "cut", "anim_out": "cut", "size": 0.72, "media": None,
              "fit": "cover", "focus": None, "zoom": 1.0, "caption": "", "media_in": 0.0, "loop": True},
    "self_broll": {"zoom_from": 1.0, "zoom_to": 1.18, "anim_in": "cut", "anim_out": "cut"},
}
TAU = math.tau
XC = graphics.XC

_clamp = graphics._clamp
_num = graphics._num


class _ByteLRU(graphics._LRU):
    """LRU acotada por bytes (capas de cuadro completo: 8.3 MB cada una a 1080x1920)."""

    def __init__(self, cap_bytes: int):
        super().__init__(cap=10_000)
        self.cap_bytes = cap_bytes
        self.nbytes = 0
        self._sz: dict = {}

    @staticmethod
    def _size(v) -> int:
        if isinstance(v, skia.Image):
            return v.width() * v.height() * 4
        if isinstance(v, np.ndarray):
            return int(v.nbytes)
        if isinstance(v, (tuple, list)):
            return sum(_ByteLRU._size(x) for x in v)
        if isinstance(v, dict):
            return sum(_ByteLRU._size(x) for x in v.values())
        return 64

    def get_or(self, key, fn):
        if key in self:
            self.move_to_end(key)
            return self[key]
        v = fn()
        n = self._size(v)
        self[key] = v
        self._sz[key] = n
        self.nbytes += n
        while self.nbytes > self.cap_bytes and len(self) > 1:
            k, _ = self.popitem(last=False)
            self.nbytes -= self._sz.pop(k, 0)
        return v


_TEX = _ByteLRU(80 * 1024 * 1024)


def _cols(colors, n: int, defaults: list) -> list:
    if isinstance(colors, str):
        colors = [colors]
    elif not isinstance(colors, (list, tuple)):
        colors = []
    out = [c for c in colors if graphics._color_ok(c)]
    for i in range(len(out), n):
        out.append(defaults[i] if i < len(defaults) else defaults[-1])
    return out


def ink_for(bg) -> str:
    """Tinta por contraste: casi blanco sobre fondo oscuro, casi negro sobre claro."""
    return "#F4F3EF" if C.luminance(bg) < 0.5 else "#15151B"


def default_colors(kind: str, ctx) -> list:
    pal = ctx.palette
    return {
        "grid_warp": [pal["dark"], pal["accent2"]],
        "gradient": [pal["dark"], pal["accent2"], pal["warm"], pal["accent"]],
        "paper": ["#EEEAE2", pal["warm"]],
        "dark_stars": ["#04050B", pal["accent2"]],
        "rays": [pal["accent"], pal["warm"]],
        "blur_self": ["#000000"],
        "color": [pal["dark"], pal["accent2"]],
        "halftone": [pal["light"], pal["dark"]],
    }.get(kind, [pal["dark"], pal["accent"]])


def _paper_color(col) -> tuple:
    """(color efectivo del papel, es_oscuro). Un papel casi negro se trata como cartulina oscura
    (se aclara un poco y el relieve se pinta con luces aditivas para que la textura se vea)."""
    if C.luminance(col) < 0.3:
        return C.mix(col, "#34343C", 0.45), True
    return col, False


def is_dark(kind: str, colors, ctx) -> bool:
    """Si el fondo EFECTIVO es oscuro (para elegir color de texto encima)."""
    if kind in ("dark_stars", "blur_self"):
        return True
    cols = _cols(colors, 1, default_colors(kind, ctx))
    if kind == "paper":
        return _paper_color(cols[0])[1]
    return C.luminance(cols[0]) < 0.5


# ============================================================== fondos


def _vignette_paint(ctx, strength: float, cx: float = 0.5, cy: float = 0.45, col="#000000") -> skia.Paint:
    W, H = ctx.W, ctx.H
    p = skia.Paint()
    p.setDither(True)  # degradados sin anillos (8 bits)
    p.setShader(skia.GradientShader.MakeRadial(
        skia.Point(W * cx, H * cy), H * 0.78,
        [C.color(col, 0.0), C.color(col, 0.0), C.color(col, strength)], [0.0, 0.45, 1.0]))
    return p


def _light_paint(ctx, col, alpha: float, cx=0.5, cy=0.4, r=0.6) -> skia.Paint:
    W, H = ctx.W, ctx.H
    p = skia.Paint()
    p.setDither(True)  # degradados sin anillos (8 bits)
    p.setShader(skia.GradientShader.MakeRadial(skia.Point(W * cx, H * cy), H * r,
                                               [C.color(col, alpha), C.color(col, 0.0)]))
    return p


def _cached_layer(ctx, key: tuple, paint_fn) -> skia.Image:
    """Capa de pantalla completa pre-renderizada (luz/vineta/fondos estaticos): dibujarla cuesta
    ~1 ms contra 4-8 ms de un degradado radial por cuadro."""
    def build():
        surf = C.offscreen(ctx.W, ctx.H)
        c = surf.getCanvas()
        c.clear(skia.ColorTRANSPARENT)
        paint_fn(c)
        return surf.makeImageSnapshot()
    return _TEX.get_or(("layer", ctx.W, ctx.H) + key, build)


def _vignette(canvas, ctx, strength: float = 0.45, cx: float = 0.5, cy: float = 0.45, col="#000000"):
    img = _cached_layer(ctx, ("vig", round(strength, 3), cx, cy, str(col)),
                        lambda c: c.drawPaint(_vignette_paint(ctx, strength, cx, cy, col)))
    canvas.drawImage(img, 0, 0)


def _backdrop(canvas, ctx, key: tuple, paint_fn):
    """Fondo estatico opaco cacheado (color + luces + vineta)."""
    canvas.drawImage(_cached_layer(ctx, ("bd",) + key, paint_fn), 0, 0)


def _grain_image(ctx, size: int = 256):
    """Ruido neutro: pixeles blancos o negros con alpha ~|ruido| (src-over no agrisa)."""
    def build():
        rng = ctx.rng("bg:grain_tex")
        g = rng.normal(0.0, 1.0, (size, size))
        a = np.clip(np.abs(g) * 0.5, 0, 1)
        v = (g > 0).astype(np.float64)
        arr = np.zeros((size, size, 4), np.uint8)
        pm = (v * a * 255).astype(np.uint8)  # premultiplicado
        arr[..., 0] = pm
        arr[..., 1] = pm
        arr[..., 2] = pm
        arr[..., 3] = (a * 255).astype(np.uint8)
        return C.image_from_rgba(arr)
    return _TEX.get_or(("grain", size, ctx.seed), build)


def grain(canvas, ctx, amount: float = 0.05):
    """Grano fino (rompe el banding de degradados). Desplazado por cuadro, determinista."""
    if amount <= 0 or ctx.draft:
        return
    img = _grain_image(ctx)
    rng = ctx.rng(f"bg:grain:{ctx.n}")
    ox, oy = rng.integers(0, 256, 2)
    m = skia.Matrix.Translate(float(ox), float(oy))
    sh = img.makeShader(skia.TileMode.kRepeat, skia.TileMode.kRepeat, C.SAMPLING_FAST, m)
    p = skia.Paint(Shader=sh)
    p.setAlphaf(min(1.0, amount * 2.5))
    canvas.drawPaint(p)


# ---------------------------------------------------------------- grid_warp
def _glow_sprite(ctx, col, alpha: float):
    """Luz radial suave pre-renderizada (1/4 de W) que se dibuja escalada donde haga falta."""
    def build():
        n = max(16, int(ctx.W / 4))
        surf = C.offscreen(n, n)
        c = surf.getCanvas()
        c.clear(skia.ColorTRANSPARENT)
        p = skia.Paint()
        p.setDither(True)  # degradados sin anillos (8 bits)
        p.setShader(skia.GradientShader.MakeRadial(skia.Point(n / 2, n / 2), n / 2,
                                                   [C.color(col, alpha), C.color(col, alpha * 0.35),
                                                    C.color(col, 0.0)], [0.0, 0.45, 1.0]))
        c.drawPaint(p)
        return surf.makeImageSnapshot()
    return _TEX.get_or(("glow", ctx.W, str(col), round(alpha, 3)), build)


def _bg_grid_warp(canvas, colors, lt, ctx):
    cols = _cols(colors, 2, default_colors("grid_warp", ctx))
    bg, acc = cols[0], cols[1]
    ln = cols[2] if len(cols) > 2 else ink_for(bg)
    W, H = ctx.W, ctx.H

    def bd(c):
        c.drawColor(C.color(bg))
        c.drawPaint(_light_paint(ctx, C.mix(bg, "#FFFFFF", 0.5), 0.16, 0.5, 0.42, 0.62))
    _backdrop(canvas, ctx, ("grid", str(bg)), bd)
    cell = W / 5.2
    t = lt
    k = ease.hold_steps(lt, 12)
    n_pts = 22 if ctx.draft else 38
    lx = W * (0.5 + 0.28 * math.sin(t * 0.7))
    ly = H * (0.45 + 0.2 * math.sin(t * 0.53 + 1.0))
    # la lente que deforma la reticula lleva una luz suave del acento (profundidad, no chillon)
    gs = W * 0.95
    canvas.drawImageRect(_glow_sprite(ctx, acc, 0.30), skia.Rect.MakeXYWH(lx - gs / 2, ly - gs / 2, gs, gs),
                         C.SAMPLING_FAST)
    R = W * 0.55
    amp = cell * 0.22

    def warp(X, Y):
        dx = amp * np.sin(Y / H * TAU * 0.9 + t * 1.7) + amp * 0.5 * np.sin((X + Y) / W * 3.1 - t * 1.2)
        dy = amp * np.sin(X / W * TAU * 0.8 - t * 1.4) + amp * 0.5 * np.cos((X - Y) / W * 2.7 + t * 0.9)
        rx, ry = X - lx, Y - ly
        f = 0.35 * np.exp(-(rx * rx + ry * ry) / (R * R))
        X2 = X + dx + rx * f
        Y2 = Y + dy + ry * f
        # temblor de trazo a mano (boil)
        X2 = X2 + ctx.px(2.2) * graphics._vn(Y2 / cell * 1.7 + k * 3.1)
        Y2 = Y2 + ctx.px(2.2) * graphics._vn(X2 / cell * 1.9 + k * 2.3 + 4.0)
        return X2, Y2

    path = skia.Path()
    ys = np.linspace(-cell, H + cell, n_pts)
    xs = np.linspace(-cell, W + cell, max(12, n_pts // 2))
    ox = (t * cell * 0.12) % cell
    for i in range(-1, int(W / cell) + 3):
        X = np.full_like(ys, i * cell - ox)
        X2, Y2 = warp(X, ys)
        pts = graphics._catmull(np.stack([X2, Y2], 1), 3)
        path.addPoly([skia.Point(float(a), float(b)) for a, b in pts.tolist()], False)
    for j in range(-1, int(H / cell) + 3):
        Y = np.full_like(xs, j * cell - ox)
        X2, Y2 = warp(xs, Y)
        pts = graphics._catmull(np.stack([X2, Y2], 1), 3)
        path.addPoly([skia.Point(float(a), float(b)) for a, b in pts.tolist()], False)
    p = C.stroke(ln, ctx.px(4.2), 0.9)
    canvas.drawPath(path, p)
    _vignette(canvas, ctx, 0.5 if C.luminance(bg) < 0.5 else 0.22)


# ---------------------------------------------------------------- gradient (malla que respira)
_MESH_SKSL = """
uniform float2 res; uniform float t;
uniform half4 c0; uniform half4 c1; uniform half4 c2; uniform half4 c3;
half4 main(float2 p) {
  float2 uv = p / res;
  uv.y *= res.y / res.x * 0.62;
  float2 a = float2(0.25 + 0.18 * sin(t * 0.61), 0.20 + 0.12 * cos(t * 0.43));
  float2 b = float2(0.78 + 0.14 * cos(t * 0.52), 0.55 + 0.16 * sin(t * 0.37 + 1.0));
  float2 c = float2(0.32 + 0.20 * cos(t * 0.33 + 2.0), 0.92 + 0.10 * sin(t * 0.71));
  float wa = exp(-dot(uv - a, uv - a) * 5.5);
  float wb = exp(-dot(uv - b, uv - b) * 4.5);
  float wc = exp(-dot(uv - c, uv - c) * 5.0);
  half4 col = c0;
  col = mix(col, c1, half(wa));
  col = mix(col, c2, half(wb * 0.9));
  col = mix(col, c3, half(wc * 0.85));
  return col;
}
"""
_MESH_EFF = None


def _mesh_effect():
    global _MESH_EFF
    if _MESH_EFF is None:
        _MESH_EFF = skia.RuntimeEffect.MakeForShader(_MESH_SKSL)
    return _MESH_EFF


def _rgba_f(c) -> list:
    r, g, b = C.rgb(c)
    return [r / 255.0, g / 255.0, b / 255.0, 1.0]


def _bg_gradient(canvas, colors, lt, ctx):
    cols = _cols(colors, 4, default_colors("gradient", ctx))
    given = len(_cols(colors, 0, [])) if colors is not None else 0
    if given == 2:  # [base, acento]: domina la base, el acento en manchas de distinta fuerza
        cols = [cols[0], cols[1], C.mix(cols[0], cols[1], 0.35), C.mix(cols[0], cols[1], 0.7)]
    elif given == 1:
        cols = [cols[0], C.mix(cols[0], "#FFFFFF", 0.18), C.mix(cols[0], "#000000", 0.3), cols[0]]
    W, H = ctx.W, ctx.H
    div = 6
    w, h = max(8, W // div), max(8, H // div)
    eff = _mesh_effect()
    small = C.offscreen(w, h)
    uni = [float(w), float(h), float(lt)] + sum((_rgba_f(c) for c in cols[:4]), [])
    data = skia.Data.MakeWithCopy(np.array(uni, np.float32).tobytes())
    small.getCanvas().drawPaint(skia.Paint(Shader=eff.makeShader(data), Dither=True))
    img = small.makeImageSnapshot()
    # la ampliacion x6 no lleva dither (duplicaria el costo a 1080x1920): el grano de encima ya
    # rompe los escalones de 8 bits
    canvas.drawImageRect(img, skia.Rect.MakeWH(W, H), C.SAMPLING_FAST)
    grain(canvas, ctx, 0.035)


# ---------------------------------------------------------------- paper (papel arrugado procedural)
def _paper_array(w: int, h: int, rng: np.random.Generator, pad: int) -> np.ndarray:
    """Papel arrugado y alisado (referencia: papel del video objetivo), como campo de alturas:
      - arrugas: crestas de ruido ANISOTROPO (estirado en una direccion, 5 orientaciones al azar,
        2 escalas) con leve distorsion de dominio: pliegues largos, casi rectos, que se cruzan;
      - pliegues marcados: aristas de un Voronoi de pocas celdas (lineas rectas largas) y facetas
        con inclinacion muy suave y difuminada;
      - luz rasante (gradiente del campo), manchas de baja frecuencia y fibras finas.
    Todo a media resolucion. Devuelve luminancia relativa ~1.0 del tamano (h+2pad, w+2pad)."""
    import cv2
    from scipy.spatial import cKDTree
    hw, hh = (w + 2 * pad) // 2, (h + 2 * pad) // 2
    s_ref = hw / 540.0  # medidas en px de la mitad de 1080

    def field(cell):
        gh, gw = max(2, int(hh / (cell * s_ref)) + 2), max(2, int(hw / (cell * s_ref)) + 2)
        f = cv2.resize(rng.normal(0, 1, (gh, gw)).astype(np.float32), (hw, hh), interpolation=cv2.INTER_CUBIC)
        return f / (f.std() + 1e-6)

    yy, xx = np.mgrid[0:hh, 0:hw].astype(np.float32)
    L = int(math.hypot(hw, hh)) + 4

    def aniso(across, along, ang):
        ga, gl = max(2, int(L / (across * s_ref)) + 2), max(2, int(L / (along * s_ref)) + 2)
        f = cv2.resize(rng.normal(0, 1, (ga, gl)).astype(np.float32), (L, L), interpolation=cv2.INTER_CUBIC)
        M = cv2.getRotationMatrix2D((L / 2, L / 2), math.degrees(ang), 1.0)
        M[:, 2] += [(hw - L) / 2, (hh - L) / 2]
        f = cv2.warpAffine(f, M, (hw, hh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        return f / (f.std() + 1e-6)

    height = np.zeros((hh, hw), np.float32)
    n_dir = 5
    wx, wy = field(180) * 14 * s_ref, field(180) * 14 * s_ref
    for _ in range(n_dir):
        ang = rng.uniform(0, math.pi)
        for across, along, amp in ((95, 320, 1.0), (40, 130, 0.5)):
            f = cv2.remap(aniso(across, along, ang), xx + wx, yy + wy, cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REFLECT)
            ridge = (1.0 - np.abs(np.tanh(f * 1.3))) ** 3
            sgn = np.tanh(aniso(across * 3, along, ang + 0.3) * 1.5)  # cresta o valle, sin saltos
            height += amp * ridge * (0.15 + 0.85 * sgn) / n_dir * 2.2
    grid = np.stack([xx.ravel(), yy.ravel()], 1)
    n_cells = 10
    pts = rng.uniform([-hw * 0.1, -hh * 0.1], [hw * 1.1, hh * 1.1], (n_cells, 2))
    d, idx = cKDTree(pts).query(grid, k=2, workers=2)
    rid = np.clip(1.0 - (d[:, 1] - d[:, 0]) / (4.0 * s_ref), 0, 1) ** 2
    height += 0.2 * rid.reshape(hh, hw).astype(np.float32)
    lvec = np.array([-0.5, -0.6, 0.62], np.float32)
    lvec /= np.linalg.norm(lvec)
    nrm = rng.normal(0, 0.08, (n_cells, 3)).astype(np.float32)
    nrm[:, 2] = 1.0
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    facet = cv2.GaussianBlur((nrm[idx[:, 0]] @ lvec - lvec[2]).reshape(hh, hw).astype(np.float32) * 3.0,
                             (0, 0), 7.0 * s_ref)
    height = cv2.GaussianBlur(height, (0, 0), 0.7)
    gxs = cv2.Sobel(height, cv2.CV_32F, 1, 0, ksize=3)
    gys = cv2.Sobel(height, cv2.CV_32F, 0, 1, ksize=3)
    wr = -(gxs * lvec[0] + gys * lvec[1])
    wr = np.tanh(wr / (np.percentile(np.abs(wr), 99.0) + 1e-6) * 1.1)
    fib = cv2.GaussianBlur(rng.normal(0, 1, (hh, hw)).astype(np.float32), (0, 0), 0.5)
    v = 0.965 + 0.085 * wr + 0.035 * np.tanh(facet) + 0.03 * field(260) + 0.012 * field(70) + 0.008 * fib
    return cv2.resize(v, (w + 2 * pad, h + 2 * pad), interpolation=cv2.INTER_LINEAR)


def _paper_image(ctx, col, pad, acc=None):
    W, H = ctx.W, ctx.H
    eff, dark = _paper_color(col)
    key = ("paper", W, H, str(col), str(acc), pad, ctx.seed)

    def build():
        v = _paper_array(W, H, ctx.rng("bg:paper"), pad)
        r, g, b = (float(x) for x in C.rgb(eff))
        a = np.empty(v.shape + (4,), np.uint8)
        if dark:
            # cartulina oscura: el relieve se suma como luz (multiplicar un color casi negro no se ve)
            d = (v - 0.965) * 300.0
            a[..., 0] = np.clip(r + d, 0, 255)
            a[..., 1] = np.clip(g + d, 0, 255)
            a[..., 2] = np.clip(b + d * 1.02, 0, 255)
        else:
            a[..., 0] = np.clip(v * r, 0, 255)
            a[..., 1] = np.clip(v * g, 0, 255)
            a[..., 2] = np.clip(v * (b * 0.985), 0, 255)
        a[..., 3] = 255
        img = C.image_from_rgba(a)
        surf = C.offscreen(img.width(), img.height())
        c = surf.getCanvas()
        c.drawImage(img, 0, 0)
        c.translate(pad, pad)
        if acc is not None:  # tinte calido de luz en una esquina (como el papel del objetivo)
            c.drawPaint(_light_paint(ctx, acc, 0.07 if not dark else 0.1, 0.85, 0.72, 0.55))
        c.drawPaint(_vignette_paint(ctx, 0.22 if not dark else 0.5, col="#3A2A18" if not dark else "#000000"))
        return surf.makeImageSnapshot()

    return _TEX.get_or(key, build)


def _bg_paper(canvas, colors, lt, ctx):
    col, acc = _cols(colors, 2, default_colors("paper", ctx))[:2]
    pad = int(ctx.px(14))
    img = _paper_image(ctx, col, pad, acc)
    # stop-motion: el papel "salta" 4 veces por segundo (desplazamientos enteros: copia rapida)
    k = int(math.floor(lt * 4))
    ox, oy = ctx.rng(f"bg:paper:{k}").integers(0, 2 * pad + 1, 2)
    canvas.drawImage(img, -float(ox), -float(oy))


# ---------------------------------------------------------------- dark_stars
def _stars(ctx):
    def build():
        rng = ctx.rng("bg:stars")
        layers = []
        for n, rmin, rmax, speed in ((420, 1.0, 2.0, 6.0), (110, 2.0, 3.2, 14.0), (22, 3.2, 4.4, 24.0)):
            layers.append({"x": rng.uniform(0, 1, n), "y": rng.uniform(0, 1, n), "r": rng.uniform(rmin, rmax, n),
                           "b": rng.uniform(0.35, 1.0, n), "f": rng.uniform(0.6, 2.2, n),
                           "ph": rng.uniform(0, TAU, n), "v": speed})
        glints = {"x": rng.uniform(0.08, 0.92, 7), "y": rng.uniform(0.06, 0.94, 7), "s": rng.uniform(0.6, 1.0, 7),
                  "ph": rng.uniform(0, TAU, 7)}
        return layers, glints
    return _TEX.get_or(("stars", ctx.seed), build)


def _bg_dark_stars(canvas, colors, lt, ctx):
    cols = _cols(colors, 2, default_colors("dark_stars", ctx))
    bg, neb = cols[0], cols[1]
    st = cols[2] if len(cols) > 2 else "#FFFFFF"
    if C.luminance(bg) > 0.35:  # un fondo claro no es 'cielo': se oscurece conservando el tono
        bg = C.mix(bg, "#000000", 0.82)
    W, H = ctx.W, ctx.H

    def bd(c):
        p = skia.Paint()
        p.setDither(True)  # degradados sin anillos (8 bits)
        p.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(0, H)],
                                                   [C.color(C.mix(bg, "#000000", 0.2)), C.color(C.mix(bg, neb, 0.12))]))
        c.drawPaint(p)
        c.drawPaint(_light_paint(ctx, neb, 0.16, 0.3, 0.3, 0.5))
        c.drawPaint(_light_paint(ctx, C.mix(neb, "#7A4DFF", 0.5), 0.10, 0.8, 0.75, 0.45))
        c.drawPaint(_vignette_paint(ctx, 0.5))
    _backdrop(canvas, ctx, ("stars", str(bg), str(neb)), bd)
    layers, gl = _stars(ctx)
    s = ctx.s
    for L in layers:
        x = (L["x"] * W - lt * L["v"] * s * 0.6) % W
        y = (L["y"] * H + lt * L["v"] * s) % H
        tw = L["b"] * (0.55 + 0.45 * np.sin(lt * L["f"] * TAU * 0.5 + L["ph"]))
        q = np.clip((tw * 4).astype(int), 0, 4)
        for lvl in range(5):
            m = q == lvl
            if not m.any():
                continue
            pts = [skia.Point(float(a), float(b)) for a, b in zip(x[m], y[m])]
            rr = float(np.mean(L["r"][m])) * s
            pp = C.stroke(st, rr * 2, 0.25 + 0.1875 * lvl, cap="round")
            canvas.drawPoints(skia.Canvas.kPoints_PointMode, pts, pp)
    # destellos de 4 puntas que titilan
    path = skia.Path()
    for i in range(len(gl["x"])):
        tw = 0.5 + 0.5 * math.sin(lt * 1.7 + gl["ph"][i])
        r = ctx.px(16) * gl["s"][i] * (0.4 + 0.6 * tw)
        graphics.quad_star(path, float(gl["x"][i] * W), float(gl["y"][i] * H), r * 0.7, r, 0.12)
    g = C.fill(st, 0.9)
    canvas.drawPath(path, g)
    gp = C.fill(st, 0.35)
    gp.setMaskFilter(graphics.blur_mask(ctx.px(6)))
    canvas.drawPath(path, gp)
    # estrella fugaz cada ~2.4 s (determinista)
    per = 2.4
    k = int(lt // per)
    ph = (lt % per) / 0.6
    if ph < 1.0:
        rng = ctx.rng(f"bg:shoot:{k}")
        x0, y0 = rng.uniform(0.2, 1.0) * W, rng.uniform(0.0, 0.45) * H
        d = np.array([-0.82, 0.57])
        L = W * 0.55
        e = ease.out_cubic(ph)
        hx, hy = x0 + d[0] * L * e, y0 + d[1] * L * e
        tl = L * 0.35 * (1 - abs(2 * ph - 1) * 0.6)
        tp = skia.Paint(AntiAlias=True, StrokeWidth=ctx.px(3.2), Style=skia.Paint.kStroke_Style)
        tp.setStrokeCap(skia.Paint.kRound_Cap)
        tp.setDither(True)  # degradados sin anillos (8 bits)
        tp.setShader(skia.GradientShader.MakeLinear([skia.Point(hx, hy), skia.Point(hx - d[0] * tl, hy - d[1] * tl)],
                                                    [C.color(st, 0.95 * (1 - ph)), C.color(st, 0.0)]))
        canvas.drawLine(hx, hy, hx - d[0] * tl, hy - d[1] * tl, tp)


# ---------------------------------------------------------------- rays (sunburst suave)
def _bg_rays(canvas, colors, lt, ctx):
    """Rayos: 11 cunas anchas de bajo contraste (mezcladas 35% hacia el fondo) que se desvanecen
    hacia el centro, bordes suaves (se dibujan a 1/4 y se escalan) y giro lento (3.5 grados/s).
    Un parche radial suave en y 0.40-0.52 despeja el titulo (oscuro o claro segun el fondo)."""
    cols = _cols(colors, 2, default_colors("rays", ctx))
    base, ray = cols[0], cols[1]
    W, H = ctx.W, ctx.H
    dark = C.luminance(base) < 0.5
    # bajo contraste: el rayo es un tinte del fondo, no una franja dura (sobre casi negro un 30%
    # del acento ya se lee; sobre claro hace falta un poco mas)
    wedge = C.mix(base, ray, 0.3 if dark else 0.42)
    div = 4
    w, h = max(8, W // div), max(8, H // div)
    cx, cy = w * 0.5, h * 0.44
    small = C.offscreen(w, h)
    sc = small.getCanvas()
    sc.drawColor(C.color(base))
    n = 11
    R = math.hypot(w, h)
    rot = math.radians(lt * 3.5)
    path = skia.Path()
    for i in range(n):
        a0 = rot + i * TAU / n
        a1 = a0 + TAU / n * 0.52
        path.moveTo(cx, cy)
        path.lineTo(cx + math.cos(a0) * R, cy + math.sin(a0) * R)
        path.lineTo(cx + math.cos(a1) * R, cy + math.sin(a1) * R)
        path.close()
    rp = skia.Paint(AntiAlias=True)
    rp.setMaskFilter(graphics.blur_mask(max(0.6, ctx.px(7) / div)))  # bordes suaves (7 px ref)
    rp.setDither(True)  # degradados sin anillos (8 bits)
    rp.setShader(skia.GradientShader.MakeRadial(
        skia.Point(cx, cy), h * 0.75,
        [C.color(wedge, 0.0), C.color(wedge, 0.55), C.color(wedge, 1.0)], [0.0, 0.3, 1.0]))
    sc.drawPath(path, rp)
    canvas.drawImageRect(small.makeImageSnapshot(), skia.Rect.MakeWH(W, H), C.SAMPLING_FAST)

    def ov(c):
        # parche que despeja el titulo + luz central + vineta
        patch = C.mix(base, "#000000", 0.45) if dark else C.mix(base, "#FFFFFF", 0.55)
        c.drawPaint(_light_paint(ctx, patch, 0.55, 0.5, 0.46, 0.34))
        c.drawPaint(_light_paint(ctx, "#FFFFFF", 0.10 if dark else 0.22, 0.5, 0.44, 0.5))
        c.drawPaint(_vignette_paint(ctx, 0.45 if dark else 0.3, 0.5, 0.44))
    canvas.drawImage(_cached_layer(ctx, ("rays_ov", str(base)), ov), 0, 0)
    grain(canvas, ctx, 0.03)


# ---------------------------------------------------------------- blur_self
downsample = graphics.downsample


def _bg_blur_self(canvas, colors, lt, ctx):
    img = ctx.base_image()
    W, H = ctx.W, ctx.H
    if img is None:
        _bg_gradient(canvas, None, lt, ctx)
        return
    div = 8
    w, h = max(8, W // div), max(8, H // div)
    small = C.offscreen(w, h)
    sc = small.getCanvas()
    p = skia.Paint(ImageFilter=skia.ImageFilters.Blur(3.2, 3.2, skia.TileMode.kClamp))
    # leve zoom lento para que no se sienta congelado
    z = 1.08 + 0.03 * ease.in_out_quad(_clamp(lt / 4.0))
    sc.save()
    sc.translate(w / 2, h / 2)
    sc.scale(z, z)
    sc.translate(-w / 2, -h / 2)
    sc.drawImageRect(downsample(img, 2), skia.Rect.MakeWH(w, h), C.SAMPLING_FAST, p)
    sc.restore()
    col = _cols(colors, 1, default_colors("blur_self", ctx))[0]
    sc.drawColor(C.color(col, 0.38))
    canvas.drawImageRect(small.makeImageSnapshot(), skia.Rect.MakeWH(W, H), C.SAMPLING_FAST)
    _vignette(canvas, ctx, 0.35)


# ---------------------------------------------------------------- color
def _bg_color(canvas, colors, lt, ctx):
    given = _cols(colors, 0, []) if colors is not None else []
    col, acc = _cols(colors, 2, default_colors("color", ctx))[:2]
    dark = C.luminance(col) < 0.5

    def bd(c):
        c.drawColor(C.color(col))
        if len(given) >= 2:  # acento explicito: luz baja muy suave (profundidad, no mancha)
            c.drawPaint(_light_paint(ctx, acc, 0.14, 0.78, 0.92, 0.55))
        c.drawPaint(_light_paint(ctx, "#FFFFFF", 0.10 if dark else 0.18, 0.5, 0.3, 0.6))
        c.drawPaint(_vignette_paint(ctx, 0.35 if dark else 0.14))
    _backdrop(canvas, ctx, ("color", str(col), str(acc) if len(given) >= 2 else ""), bd)
    grain(canvas, ctx, 0.03)


# ---------------------------------------------------------------- halftone
def _halftone_grid(ctx):
    def build():
        sp = ctx.px(30)
        xs = np.arange(-sp, ctx.W + 2 * sp, sp)
        ys = np.arange(-sp, ctx.H + 2 * sp, sp)
        X, Y = np.meshgrid(xs, ys)
        X[1::2] += sp / 2  # reticula escalonada
        return X.ravel(), Y.ravel(), sp
    return _TEX.get_or(("ht", ctx.W, ctx.H), build)


def _bg_halftone(canvas, colors, lt, ctx):
    bg, dot = _cols(colors, 2, default_colors("halftone", ctx))[:2]
    if abs(C.luminance(bg) - C.luminance(dot)) < 0.12:  # puntos invisibles: tinta por contraste
        dot = ink_for(bg)
    W, H = ctx.W, ctx.H
    canvas.drawColor(C.color(bg))
    X, Y, sp = _halftone_grid(ctx)
    g = 0.5 + 0.5 * np.sin((X * 0.55 + Y) / H * TAU * 0.9 - lt * 1.6)
    g = 0.35 + 0.65 * g  # densidad minima: la composicion nunca se vacia
    diag = 0.3 + 0.7 * np.clip(1.15 - (X / W * 0.35 + Y / H * 0.85), 0, 1)
    r = sp * 0.5 * np.clip(0.12 + 0.88 * g * diag, 0, 1)
    clear = np.clip((np.abs(Y - H * 0.45) - H * 0.04) / (H * 0.2), 0, 1)  # respiro para el titulo
    r = r * (0.15 + 0.85 * clear * clear * (3 - 2 * clear))
    q = np.clip((r / (sp * 0.5) * 6).astype(int), 0, 6)
    for lvl in range(1, 7):
        m = q == lvl
        if not m.any():
            continue
        pts = [skia.Point(float(a), float(b)) for a, b in zip(X[m], Y[m])]
        canvas.drawPoints(skia.Canvas.kPoints_PointMode, pts, C.stroke(dot, sp * lvl / 6.0, 0.9, cap="round"))
    _vignette(canvas, ctx, 0.2)


_BG_FN = {"grid_warp": _bg_grid_warp, "gradient": _bg_gradient, "paper": _bg_paper,
          "dark_stars": _bg_dark_stars, "rays": _bg_rays, "blur_self": _bg_blur_self, "color": _bg_color,
          "halftone": _bg_halftone}


def draw_background(canvas: skia.Canvas, kind: str, colors, lt: float, ctx) -> None:
    """Pinta un fondo animado a pantalla completa (tiempo local lt)."""
    fn = _BG_FN.get(kind or "gradient", _bg_gradient)
    fn(canvas, colors, lt, ctx)


# ============================================================== entrada / salida del cutaway


def _clean(item: dict) -> dict:
    """Copia tolerante: colores de fondo validos, numeros numericos, anim conocidas."""
    it = dict(item or {})
    bc = it.get("bg_colors")
    if bc is not None:
        it["bg_colors"] = _cols(bc, 0, [])
    for k in ("in_dur", "out_dur", "zoom_from", "zoom_to", "size", "tilt", "y", "intensity", "src_t"):
        if k in it and it[k] is not None:
            try:
                v = float(it[k])
                it[k] = v if math.isfinite(v) else None
            except (TypeError, ValueError):
                it[k] = None
    if it.get("background") not in BACKGROUNDS:
        it.pop("background", None)
    if it.get("anim_in") not in SCENE_IN:
        it.pop("anim_in", None)
    if it.get("anim_out") not in SCENE_OUT:
        it.pop("anim_out", None)
    pan = it.get("pan")
    if pan is not None:
        try:
            it["pan"] = [float(pan[0]), float(pan[1])]
        except Exception:
            it["pan"] = None
    t = it.get("text")
    if t is not None and not isinstance(t, (str, dict)):
        it.pop("text")
    elif isinstance(t, dict):
        t = dict(t)
        if not isinstance(t.get("text", ""), str):
            t["text"] = str(t.get("text"))
        it["text"] = t
    if it.get("graphics") is not None and not isinstance(it["graphics"], (list, tuple)):
        it.pop("graphics")
    if it.get("card") is not None and not isinstance(it["card"], dict):
        it.pop("card")
    cap = it.get("caption")
    if cap is not None and not isinstance(cap, str):
        it["caption"] = None if isinstance(cap, (dict, list, bool)) else str(cap)
    for k in ("_still", "_src_image"):
        if it.get(k) is not None and not isinstance(it[k], skia.Image):
            it.pop(k)
    for k in ("zoom_from", "zoom_to"):  # un zoom < 1 dejaria ver el cuadro base debajo
        if it.get(k) is not None:
            it[k] = _clamp(it[k], 1.0, 4.0)
    return it


def frame_request(item: dict) -> tuple[str, str, float | None] | None:
    """Que cuadro necesita el cutaway (para render.py): (clave del item, referencia, t_orig).
    photo -> ('_still', 'start', item.start); self_broll -> ('_src_image', 'src_t', item.src_t).
    render debe decodificar ese cuadro UNA vez por item y fijarlo como skia.Image."""
    need = NEEDS_FRAME.get(str(item.get("kind")))
    if need is None:
        return None
    key, ref = need
    v = item.get("start") if ref == "start" else item.get("src_t", item.get("start"))
    try:
        t = float(v) if v is not None else None
    except (TypeError, ValueError):
        t = None
    return key, ref, t


def _scene(canvas, item, lt, dur, ctx, paint_fn):
    """Aplica la entrada/salida del cutaway sobre el cuadro base ya dibujado en canvas."""
    W, H = ctx.W, ctx.H
    ain = item.get("anim_in") or "cut"
    aout = item.get("anim_out") or "cut"
    tin = _num(item, "in_dur", 0.35)
    tout = _num(item, "out_dur", 0.3)
    pin, pout = ease.envelope(lt, dur, tin, tout)
    if ain == "cut":
        pin = 1.0
    if aout == "cut":
        pout = 0.0
    e_in = ease.out_cubic(pin)
    e_out = ease.in_cubic(pout)
    canvas.save()
    alpha = 1.0
    if pin < 1.0:
        if ain == "fade":
            alpha *= e_in
        elif ain == "zoom":
            alpha *= e_in
            s = 1.0 + 0.18 * (1 - e_in)
            canvas.translate(W / 2, H / 2)
            canvas.scale(s, s)
            canvas.translate(-W / 2, -H / 2)
        elif ain == "slide_up":
            canvas.translate(0, H * (1 - ease.out_expo(pin)))
        elif ain == "slide_left":
            canvas.translate(W * (1 - ease.out_expo(pin)), 0)
        elif ain == "circle":
            cx, cy = (ctx.face[0] * W, ctx.face[1] * H) if ctx.face else (W / 2, H / 2)
            pth = skia.Path()
            pth.addCircle(cx, cy, math.hypot(W, H) * ease.in_out_cubic(pin))
            canvas.clipPath(pth, skia.ClipOp.kIntersect, True)
        elif ain == "wipe":
            canvas.clipRect(skia.Rect.MakeLTRB(0, 0, W * ease.in_out_cubic(pin), H))
    if pout > 0.0:
        if aout == "fade":
            alpha *= 1 - e_out
        elif aout == "zoom":
            alpha *= 1 - e_out
            s = 1.0 + 0.2 * e_out
            canvas.translate(W / 2, H / 2)
            canvas.scale(s, s)
            canvas.translate(-W / 2, -H / 2)
        elif aout == "slide_down":
            canvas.translate(0, H * ease.in_expo(pout))
        elif aout == "slide_up":
            canvas.translate(0, -H * ease.in_expo(pout))
        elif aout == "circle":
            cx, cy = W / 2, H / 2
            pth = skia.Path()
            pth.addCircle(cx, cy, math.hypot(W, H) * (1 - ease.in_out_cubic(pout)))
            canvas.clipPath(pth, skia.ClipOp.kIntersect, True)
    if alpha <= 0.003:
        canvas.restore()
        return
    layer = alpha < 0.999
    if layer:
        canvas.saveLayerAlpha(skia.Rect.MakeWH(W, H), int(alpha * 255))
    paint_fn(canvas)
    if layer:
        canvas.restore()
    canvas.restore()


def _bg_settle(canvas, item, lt, ctx, kind, colors):
    """Fondo con un leve 'asentamiento' de escala al entrar (energia sin transicion)."""
    W, H = ctx.W, ctx.H
    z = 1.0 + 0.06 * (1.0 - ease.out_cubic(_clamp(lt / 0.5))) * _num(item, "intensity", 1.0)
    if z > 1.0005:
        canvas.save()
        canvas.translate(W / 2, H / 2)
        canvas.scale(z, z)
        canvas.translate(-W / 2, -H / 2)
        draw_background(canvas, kind, colors, lt, ctx)
        canvas.restore()
    else:
        draw_background(canvas, kind, colors, lt, ctx)


def _embedded(canvas, item, lt, dur, ctx, dark: bool):
    """Dibuja graphics[] embebidos (offset/length relativos al cutaway)."""
    for i, g in enumerate(item.get("graphics") or []):
        if not isinstance(g, dict) or g.get("enabled") is False:
            continue
        fn = graphics.RENDERERS.get(g.get("kind", "doodle"))
        if fn is None:
            continue
        off = _num(g, "offset", 0.0)
        ln = _num(g, "length", max(0.05, dur - off))
        lg = lt - off
        if 0.0 <= lg < ln:
            gi = dict(g)
            gi.setdefault("id", f"{item.get('id', 'cut')}:g{i}")
            fn(canvas, gi, lg, ln, ctx)


# ============================================================== texto (via text.py con fallback)


def _text_item(item, dark: bool, ctx) -> dict | None:
    t = item.get("text")
    if not t:
        return None
    if isinstance(t, str):
        t = {"preset": "title_pop", "text": t}
    ti = dict(t)
    if not str(ti.get("text") or "").strip() and not ti.get("lines") and ti.get("preset") != "counter":
        return None
    ti.setdefault("id", f"{item.get('id', 'cut')}:text")
    ti.setdefault("x", XC)
    ti.setdefault("y", 0.45)
    ti.setdefault("color", "#FFFFFF" if dark else "#15151B")
    ti.setdefault("accent", ctx.palette["accent"] if dark else C.mix(ctx.palette["accent"], "#000000", 0.25))
    # el texto mide el fondo de la ESCENA (no el video tapado): refuerzo de sombra / placa correctos
    ti.setdefault("_bg_lum", 0.1 if dark else 0.9)
    return ti


def draw_text_item(canvas, ti: dict, lt: float, dur: float, ctx) -> None:
    """Usa text.RENDERERS[preset] si existe (import perezoso); si no, fallback tipografico propio."""
    fn = None
    try:
        from . import text as _text  # noqa: WPS433 (perezoso: text.py puede no existir aun)
        fn = getattr(_text, "RENDERERS", {}).get(ti.get("preset") or "title_pop")
    except Exception:
        fn = None
    if fn is not None:
        try:
            fn(canvas, ti, lt, dur, ctx)
            return
        except Exception:
            pass
    fallback_title(canvas, ti, lt, dur, ctx)


def fallback_title(canvas, ti: dict, lt: float, dur: float, ctx) -> None:
    """Titulo cinetico propio: palabras que suben con resorte, palabras acento en color."""
    pal = ctx.palette
    W, H = ctx.W, ctx.H
    size = _num(ti, "size", 0.1) * W
    fname = ti.get("font") or pal.get("fonts", {}).get("title", "Poppins-Black")
    col = ti.get("color") or "#FFFFFF"
    acc = ti.get("accent") or pal["accent"]
    accent_words = {str(w).lower().strip(".,!?") for w in (ti.get("accent_words") or [])}
    maxw = W * (graphics.SAFE_W - 0.04)  # ancho de SAFE menos margen
    raw = ti.get("lines")
    if raw:
        lines = [(str(ln.get("text", "")), _num(ln, "scale", 1.0), ln.get("font") or fname,
                  ln.get("color") if graphics._color_ok(ln.get("color")) else None)
                 for ln in raw if isinstance(ln, dict)] if isinstance(raw, (list, tuple)) else []
    else:
        txt = str(ti.get("text") or "")
        parts = txt.split("\n")
        lines = []
        for part in parts:
            f0 = graphics.font(fname, size)
            for ln in graphics.wrap(f0, part, maxw, 3):
                lines.append((ln, 1.0, fname, None))
    if not lines:
        return
    fonts = []
    for text_, sc, fn_, _ in lines:
        f = graphics.fit_font(fn_, text_, size * sc, maxw)
        fonts.append(f)
    lh = [f.getSize() * 1.08 for f in fonts]
    total = sum(lh)
    cx, cy = _num(ti, "x", XC) * W, _num(ti, "y", 0.45) * H
    cx = graphics.safe_cx(cx, maxw / 2, ctx)
    y = cy - total / 2
    tout = 0.22
    pout = _clamp((lt - (dur - tout)) / tout) if dur > 0 else 0.0
    wi = 0
    for li, (text_, sc, fn_, lcol) in enumerate(lines):
        f = fonts[li]
        base = y + lh[li] * 0.82
        words = text_.split(" ")
        space = f.measureText(" ")
        widths = [f.measureText(w) for w in words]
        lw = sum(widths) + space * (len(words) - 1)
        x = cx - lw / 2
        for w_, ww in zip(words, widths):
            tt = lt - 0.06 * wi
            sp = graphics.spring(tt, freq=2.6, damping=0.55)
            a = _clamp(tt / 0.12) * (1 - ease.in_quad(pout))
            if a > 0.003:
                c = lcol or (acc if w_.lower().strip(".,!?") in accent_words else col)
                dy = (1 - sp) * f.getSize() * 0.45
                sh = C.fill("#000000", 0.28 * a)
                sh.setMaskFilter(graphics.blur_mask(f.getSize() * 0.06))
                canvas.drawString(w_, x, base + dy + f.getSize() * 0.04, f, sh)
                canvas.drawString(w_, x, base + dy, f, C.fill(c, a))
            x += ww + space
            wi += 1
        y += lh[li]


# ============================================================== renderers


def draw_title_card(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    item = _clean(item)
    kind = item.get("background") or "grid_warp"
    colors = item.get("bg_colors")
    dark = is_dark(kind, colors, ctx)
    ti = _text_item(item, dark, ctx)

    def paint(cv):
        _bg_settle(cv, item, lt, ctx, kind, colors)
        if ti is not None:
            off = _num(ti, "offset", 0.12)
            ln = _num(ti, "length", max(0.1, dur - off))
            if 0.0 <= lt - off < ln:
                draw_text_item(cv, ti, lt - off, ln, ctx)
        _embedded(cv, item, lt, dur, ctx, dark)

    _scene(canvas, item, lt, dur, ctx, paint)


CARD_FIELDS = ("variant", "title", "subtitle", "value", "items")


def has_own_card(item: dict) -> bool:
    """True si el info_card trae su propia tarjeta (`card` dict o campos de card en el nivel
    superior). El director manda la tarjeta dentro de `graphics`: ahi no hay tarjeta propia."""
    if isinstance(item.get("card"), dict):
        return True
    return any(item.get(k) not in (None, "", []) for k in CARD_FIELDS)


def _card_from(item: dict) -> dict:
    if isinstance(item.get("card"), dict):
        c = dict(item["card"])
    else:
        c = {k: item[k] for k in ("variant", "title", "subtitle", "value", "items", "icon", "theme", "label",
                                  "accent", "map_label", "days", "cursor") if k in item}
    c.setdefault("id", f"{item.get('id', 'cut')}:card")
    c.setdefault("kind", "card")
    c.setdefault("size", 0.8)
    c.setdefault("x", XC)
    c.setdefault("y", 0.52)
    c.setdefault("anim_in", "pop")
    c.setdefault("anim_out", "none")
    return c


def draw_info_card(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    item = _clean(item)
    kind = item.get("background") or "gradient"
    colors = item.get("bg_colors")
    dark = is_dark(kind, colors, ctx)
    card = _card_from(item) if has_own_card(item) else None
    ti = _text_item(item, dark, ctx)
    if ti is not None:
        if not (isinstance(item.get("text"), dict) and "y" in item["text"]):
            ti["y"] = 0.22
        ti.setdefault("size", 0.085)

    def paint(cv):
        _bg_settle(cv, item, lt, ctx, kind, colors)
        if ti is not None:
            off = _num(ti, "offset", 0.05)
            if lt - off >= 0:
                draw_text_item(cv, ti, lt - off, max(0.1, dur - off), ctx)
        if card is not None:
            off = _num(card, "offset", 0.12)
            if lt - off >= 0:
                graphics.draw_card(cv, card, lt - off, max(0.1, dur - off), ctx)
        _embedded(cv, item, lt, dur, ctx, dark)

    _scene(canvas, item, lt, dur, ctx, paint)


def _cover_src(img: skia.Image, dst_w: float, dst_h: float, focus: tuple | None) -> skia.Rect:
    """Rect fuente que cubre dst (aspecto) centrado en `focus` (x,y normalizado) si existe."""
    iw, ih = img.width(), img.height()
    ar = dst_w / dst_h
    if iw / ih > ar:
        sw, sh = ih * ar, ih
    else:
        sw, sh = iw, iw / ar
    fx, fy = focus if focus else (0.5, 0.45)
    x = _clamp(fx * iw - sw / 2, 0, iw - sw)
    y = _clamp(fy * ih - sh / 2, 0, ih - sh)
    return skia.Rect.MakeXYWH(x, y, sw, sh)


def draw_photo(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    item = _clean(item)
    kind = item.get("background") or "paper"
    colors = item.get("bg_colors")
    dark = is_dark(kind, colors, ctx)
    own = graphics.media_image(item)            # imagen/video propio (motor: _media_image)
    img = own or item.get("_still") or ctx.base_image()
    W, H = ctx.W, ctx.H
    rnd = ctx.rand(f"photo:{item.get('id', '')}")
    tilt = _num(item, "tilt", -4.0 + (rnd - 0.5) * 4.0)
    caption = item.get("caption")
    if caption is None and isinstance(item.get("text"), str):
        caption = item["text"]

    def paint(cv):
        _bg_settle(cv, item, lt, ctx, kind, colors)
        pw = W * _clamp(_num(item, "size", DEFAULTS["photo"]["size"]), 0.3, 0.8)
        ph_img = pw * 0.92 * 1.2              # hueco fijo 0.91 x 1.104 pw (render.PHOTO_ASPECT)
        border = pw * 0.045
        bottom = pw * 0.2
        fw, fh = pw, ph_img + border + bottom
        # entrada: lanzada desde abajo con giro que se asienta
        sp = graphics.spring(lt, freq=1.8, damping=0.6)
        rot = tilt + (1 - sp) * 14.0
        dy = (1 - sp) * H * 0.55
        s = (0.92 + 0.08 * sp) * (1.0 + 0.025 * ease.in_out_quad(_clamp(lt / max(0.5, dur))))
        cx, cy = W * XC, H * _num(item, "y", 0.46)
        cv.save()
        cv.translate(cx, cy + dy)
        cv.rotate(rot)
        cv.scale(s, s)
        r = skia.Rect.MakeXYWH(-fw / 2, -fh / 2, fw, fh)
        rr = skia.RRect.MakeRectXY(r, ctx.px(6), ctx.px(6))
        graphics.soft_shadow(cv, rr, ctx, 1.0, key=0.3, amb=0.25, spread=1.4)
        cv.drawRRect(rr, C.fill("#FBFAF6"))
        ir = skia.Rect.MakeXYWH(-fw / 2 + border, -fh / 2 + border, fw - 2 * border, ph_img)
        if img is not None:
            if own is not None:
                fit = "contain" if item.get("fit") == "contain" else "cover"
                src, dst = graphics.fit_src(img, ir.width(), ir.height(), fit, item.get("focus"),
                                            _num(item, "zoom", 1.0))
                if fit == "contain":
                    cv.drawRect(ir, C.fill("#1A1A20"))
                cv.drawImageRect(img, src, dst.makeOffset(ir.centerX(), ir.centerY()),
                                 C.SAMPLING_CUBIC if not ctx.draft else C.SAMPLING_FAST, None)
            else:
                focus = (ctx.face[0], ctx.face[1] + 0.08) if ctx.face else None
                src = _cover_src(img, ir.width(), ir.height(), focus)
                cv.drawImageRect(img, src, ir, C.SAMPLING_FAST, None)
            # "revelado" de foto instantanea: velo que se aclara
            dev = 1.0 - ease.out_cubic(_clamp((lt - 0.1) / 0.9))
            if dev > 0.01:
                cv.drawRect(ir, C.fill("#F4F1EA", 0.85 * dev))
        else:
            cv.drawRect(ir, C.fill("#D8D4CC"))
        cv.drawRect(ir, C.stroke("#000000", 1.0, 0.08))
        if caption:
            f = graphics.fit_font(ctx.palette.get("fonts", {}).get("script", "Caveat-Bold"), str(caption),
                                  bottom * 0.62, fw - 2 * border)
            ce = ease.out_cubic(_clamp((lt - 0.45) / 0.5))
            n = int(len(str(caption)) * ce + 0.999)
            graphics.draw_text(cv, str(caption)[:n], 0, fh / 2 - bottom * 0.36, f, "#2A2A33", 1.0, align="center")
        # cinta adhesiva
        tp = skia.Rect.MakeXYWH(-pw * 0.14, -fh / 2 - pw * 0.035, pw * 0.28, pw * 0.08)
        cv.save()
        cv.rotate(-tilt * 0.8 + 3)
        cv.drawRect(tp, C.fill("#F2E6C8", 0.78))
        cv.restore()
        cv.restore()
        _embedded(cv, item, lt, dur, ctx, dark)

    _scene(canvas, item, lt, dur, ctx, paint)


def _broll_offset(z: float, pan: float, focus_px: float, extent: float) -> float:
    """Desplazamiento (px) acotado para que la imagen escalada z alrededor de focus_px siga
    cubriendo [0, extent] (sin asomar el cuadro base debajo)."""
    lo = -(extent - focus_px) * (z - 1.0)
    hi = focus_px * (z - 1.0)
    return _clamp(pan * extent, lo, hi)


def draw_self_broll(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    item = _clean(item)
    img = item.get("_src_image") or ctx.base_image()
    W, H = ctx.W, ctx.H
    z0, z1 = _num(item, "zoom_from", 1.0), _num(item, "zoom_to", 1.18)
    pan = item.get("pan") or [0.0, 0.0]
    p = ease.in_out_quad(_clamp(lt / max(1e-3, dur)))
    z = max(1.0, z0 + (z1 - z0) * p)
    dark = True
    ti = _text_item(item, dark, ctx)

    def paint(cv):
        if img is None:
            draw_background(cv, "gradient", None, lt, ctx)
        else:
            fx, fy = (_clamp(ctx.face[0]) * W, _clamp(ctx.face[1]) * H) if ctx.face else (W / 2, H / 2)
            dx = _broll_offset(z, float(pan[0]) * p, fx, W)
            dy = _broll_offset(z, float(pan[1]) * p, fy, H)
            cv.save()
            cv.translate(fx + dx, fy + dy)
            cv.scale(z, z)
            cv.translate(-fx, -fy)
            cv.drawImageRect(img, skia.Rect.MakeWH(W, H), C.SAMPLING_FAST)
            cv.restore()
            _vignette(cv, ctx, 0.3)
        if ti is not None:
            off = _num(ti, "offset", 0.12)
            if lt - off >= 0:
                draw_text_item(cv, ti, lt - off, max(0.1, dur - off), ctx)
        _embedded(cv, item, lt, dur, ctx, dark)

    _scene(canvas, item, lt, dur, ctx, paint)


RENDERERS = {
    "title_card": draw_title_card,
    "info_card": draw_info_card,
    "photo": draw_photo,
    "self_broll": draw_self_broll,
}
