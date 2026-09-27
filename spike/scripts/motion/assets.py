"""Registro de assets del motor: fuentes, iconos (Tabler, MIT), paletas y catalogos.

Todo vive en `SPIKE/assets/motion/` (solo lectura en la app empaquetada):
  fonts/     TTF con licencia OFL/Apache (ver fonts/OFL.txt y LICENSE.txt)
  icons/     tabler-nodes-{outline,filled}.json + icons.json (tags) + LICENSE (MIT)
  sfx/       catalogo sintetizado por `motion/sfx.py` (propio, sin licencias de terceros)
  music/     pistas generadas/propias
  textures/  texturas procedurales pre-generadas (papel, grano, leaks)

Las funciones cachean: cargar un icono o una fuente cuesta una sola vez por proceso.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

import skia

try:  # dentro del motor (scripts/ en sys.path)
    import paths as _paths
    SPIKE = _paths.SPIKE
except Exception:  # pragma: no cover - uso aislado (tests/demos)
    SPIKE = Path(__file__).resolve().parents[2]

MOTION_DIR = SPIKE / "assets" / "motion"
FONTS_DIR = MOTION_DIR / "fonts"
ICONS_DIR = MOTION_DIR / "icons"
SFX_DIR = MOTION_DIR / "sfx"
MUSIC_DIR = MOTION_DIR / "music"
TEXTURES_DIR = MOTION_DIR / "textures"

# ---------------------------------------------------------------- fuentes
# nombre logico -> (archivo, peso variable o None)
_WGHT = 0x77676874  # 'wght'
FONT_FILES: dict[str, tuple[str, float | None]] = {
    "Poppins-Black": ("Poppins-Black.ttf", None),
    "Poppins-BlackItalic": ("Poppins-BlackItalic.ttf", None),
    "Poppins-ExtraBold": ("Poppins-ExtraBold.ttf", None),
    "Poppins-Bold": ("Poppins-Bold.ttf", None),
    "Poppins-SemiBold": ("Poppins-SemiBold.ttf", None),
    "Poppins-Medium": ("Poppins-Medium.ttf", None),
    "Anton": ("Anton-Regular.ttf", None),
    "BebasNeue": ("BebasNeue-Regular.ttf", None),
    "ArchivoBlack": ("ArchivoBlack-Regular.ttf", None),
    "DMSerifDisplay": ("DMSerifDisplay-Regular.ttf", None),
    "DMSerifDisplay-Italic": ("DMSerifDisplay-Italic.ttf", None),
    "InstrumentSerif": ("InstrumentSerif-Regular.ttf", None),
    "InstrumentSerif-Italic": ("InstrumentSerif-Italic.ttf", None),
    "Kalam-Bold": ("Kalam-Bold.ttf", None),
    "Pacifico": ("Pacifico-Regular.ttf", None),
    "PermanentMarker": ("PermanentMarker-Regular.ttf", None),
    "LuckiestGuy": ("LuckiestGuy-Regular.ttf", None),
    "Bangers": ("Bangers-Regular.ttf", None),
    "SpaceMono-Bold": ("SpaceMono-Bold.ttf", None),
    "Caveat-Bold": ("Caveat[wght].ttf", 700.0),
    "Caveat": ("Caveat[wght].ttf", 500.0),
    "Montserrat-Black": ("Montserrat[wght].ttf", 900.0),
    "Montserrat-ExtraBold": ("Montserrat[wght].ttf", 800.0),
    "Montserrat-Bold": ("Montserrat[wght].ttf", 700.0),
    "Montserrat-SemiBold": ("Montserrat[wght].ttf", 600.0),
    "Montserrat-Medium": ("Montserrat[wght].ttf", 500.0),
    "Inter-Black": ("Inter[opsz,wght].ttf", 900.0),
    "Inter-Bold": ("Inter[opsz,wght].ttf", 700.0),
    "Inter-SemiBold": ("Inter[opsz,wght].ttf", 600.0),
    "Inter-Medium": ("Inter[opsz,wght].ttf", 500.0),
}
DEFAULT_FONT = "Poppins-ExtraBold"

# Familias por rol (los estilos del director eligen de aqui)
FONT_ROLES = {
    "bold": ["Poppins-ExtraBold", "Montserrat-ExtraBold", "Poppins-Black"],
    "impact": ["Anton", "BebasNeue", "ArchivoBlack"],
    "serif": ["DMSerifDisplay", "InstrumentSerif"],
    "serif_italic": ["DMSerifDisplay-Italic", "InstrumentSerif-Italic"],
    "script": ["Caveat-Bold", "Kalam-Bold", "Pacifico", "PermanentMarker"],
    "fun": ["LuckiestGuy", "Bangers"],
    "body": ["Poppins-SemiBold", "Inter-SemiBold", "Montserrat-SemiBold"],
    "mono": ["SpaceMono-Bold"],
}


@lru_cache(maxsize=64)
def typeface(name: str | None) -> skia.Typeface:
    """Typeface por nombre logico (o nombre de archivo). Cae a DEFAULT_FONT si no existe."""
    name = name or DEFAULT_FONT
    spec = FONT_FILES.get(name)
    if spec is None:
        # permitir nombre de archivo directo o alias sin guion ("Poppins ExtraBold")
        alias = name.replace(" ", "-")
        spec = FONT_FILES.get(alias)
        if spec is None:
            p = FONTS_DIR / name
            if p.exists():
                spec = (name, None)
            else:
                spec = FONT_FILES[DEFAULT_FONT]
    fname, wght = spec
    tf = skia.Typeface.MakeFromFile(str(FONTS_DIR / fname))
    if tf is None:
        tf = skia.Typeface.MakeFromFile(str(FONTS_DIR / FONT_FILES[DEFAULT_FONT][0]))
    if wght is not None:
        VP = skia.FontArguments.VariationPosition
        coords = VP.Coordinates([VP.Coordinate(axis=_WGHT, value=float(wght))])
        args = skia.FontArguments()
        args.setVariationDesignPosition(VP(coords))
        tf = tf.makeClone(args)
    return tf


def font(name: str | None, size_px: float) -> skia.Font:
    f = skia.Font(typeface(name), float(size_px))
    f.setSubpixel(True)
    f.setEdging(skia.Font.Edging.kAntiAlias)
    f.setHinting(skia.FontHinting.kNone)
    return f


def list_fonts() -> list[str]:
    return sorted(FONT_FILES)


# ---------------------------------------------------------------- SVG path -> skia.Path
_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def svg_path(d: str) -> skia.Path:
    """Parser SVG path completo (M L H V C S Q T A Z, absolutos y relativos)."""
    toks = _TOKEN.findall(d)
    p = skia.Path()
    i = 0
    cmd = None
    cx = cy = 0.0  # punto actual
    sx = sy = 0.0  # inicio de subpath
    lcx = lcy = None  # ultimo control (para S/T)
    lcmd = None

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        t = toks[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                p.close()
                cx, cy = sx, sy
                lcmd = cmd
                continue
        elif cmd is None:
            break
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = num(), num()
            if rel:
                x, y = cx + x, cy + y
            p.moveTo(x, y)
            cx, cy, sx, sy = x, y, x, y
            cmd = "l" if rel else "L"  # coordenadas extra = lineTo
            lcx = lcy = None
        elif c == "L":
            x, y = num(), num()
            if rel:
                x, y = cx + x, cy + y
            p.lineTo(x, y)
            cx, cy = x, y
            lcx = lcy = None
        elif c == "H":
            x = num()
            x = cx + x if rel else x
            p.lineTo(x, cy)
            cx = x
            lcx = lcy = None
        elif c == "V":
            y = num()
            y = cy + y if rel else y
            p.lineTo(cx, y)
            cy = y
            lcx = lcy = None
        elif c == "C":
            x1, y1, x2, y2, x, y = (num() for _ in range(6))
            if rel:
                x1, y1, x2, y2, x, y = cx + x1, cy + y1, cx + x2, cy + y2, cx + x, cy + y
            p.cubicTo(x1, y1, x2, y2, x, y)
            lcx, lcy, cx, cy = x2, y2, x, y
        elif c == "S":
            x2, y2, x, y = (num() for _ in range(4))
            if rel:
                x2, y2, x, y = cx + x2, cy + y2, cx + x, cy + y
            if lcmd and lcmd.upper() in "CS" and lcx is not None:
                x1, y1 = 2 * cx - lcx, 2 * cy - lcy
            else:
                x1, y1 = cx, cy
            p.cubicTo(x1, y1, x2, y2, x, y)
            lcx, lcy, cx, cy = x2, y2, x, y
        elif c == "Q":
            x1, y1, x, y = (num() for _ in range(4))
            if rel:
                x1, y1, x, y = cx + x1, cy + y1, cx + x, cy + y
            p.quadTo(x1, y1, x, y)
            lcx, lcy, cx, cy = x1, y1, x, y
        elif c == "T":
            x, y = num(), num()
            if rel:
                x, y = cx + x, cy + y
            if lcmd and lcmd.upper() in "QT" and lcx is not None:
                x1, y1 = 2 * cx - lcx, 2 * cy - lcy
            else:
                x1, y1 = cx, cy
            p.quadTo(x1, y1, x, y)
            lcx, lcy, cx, cy = x1, y1, x, y
        elif c == "A":
            rx, ry, rot, large, sweep, x, y = (num() for _ in range(7))
            if rel:
                x, y = cx + x, cy + y
            if rx == 0 or ry == 0:
                p.lineTo(x, y)
            else:
                size = skia.Path.ArcSize.kLarge_ArcSize if large else skia.Path.ArcSize.kSmall_ArcSize
                direction = skia.PathDirection.kCW if sweep else skia.PathDirection.kCCW
                p.arcTo(abs(rx), abs(ry), rot, size, direction, x, y)
            cx, cy = x, y
            lcx = lcy = None
        else:  # comando desconocido: abortar con lo que haya
            break
        lcmd = cmd
    return p


def _shape_to_path(tag: str, a: dict) -> skia.Path | None:
    f = lambda k, d=0.0: float(a.get(k, d))  # noqa: E731
    p = skia.Path()
    if tag == "path":
        d = a.get("d", "")
        if not d or d.strip() in ("M0 0h24v24H0z",):
            return None
        return svg_path(d)
    if tag == "circle":
        p.addCircle(f("cx"), f("cy"), f("r"))
    elif tag == "ellipse":
        cx, cy, rx, ry = f("cx"), f("cy"), f("rx"), f("ry")
        p.addOval(skia.Rect.MakeLTRB(cx - rx, cy - ry, cx + rx, cy + ry))
    elif tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        rx = f("rx", 0.0)
        r = skia.Rect.MakeXYWH(x, y, w, h)
        if rx > 0:
            p.addRRect(skia.RRect.MakeRectXY(r, rx, f("ry", rx)))
        else:
            p.addRect(r)
    elif tag == "line":
        p.moveTo(f("x1"), f("y1"))
        p.lineTo(f("x2"), f("y2"))
    elif tag in ("polyline", "polygon"):
        pts = [float(v) for v in re.findall(r"[-+]?\d*\.?\d+", a.get("points", ""))]
        if len(pts) >= 2:
            p.moveTo(pts[0], pts[1])
            for k in range(2, len(pts) - 1, 2):
                p.lineTo(pts[k], pts[k + 1])
            if tag == "polygon":
                p.close()
    else:
        return None
    return p


@lru_cache(maxsize=2)
def _tabler_nodes(variant: str) -> dict:
    f = ICONS_DIR / f"tabler-nodes-{variant}.json"
    return json.loads(f.read_text()) if f.exists() else {}


@lru_cache(maxsize=1)
def _tabler_meta() -> dict:
    f = ICONS_DIR / "icons.json"
    return json.loads(f.read_text()) if f.exists() else {}


@lru_cache(maxsize=512)
def icon_paths(name: str, variant: str = "outline") -> tuple[skia.Path, ...]:
    """Paths de un icono Tabler en su espacio 24x24 (stroke 2, round caps en outline).

    Devuelve tupla vacia si no existe. Para dibujar: escalar por size/24.
    """
    nodes = _tabler_nodes(variant).get(name)
    if nodes is None and variant == "filled":
        nodes = _tabler_nodes("outline").get(name)
    if not nodes:
        return tuple()
    out = []
    for tag, attrs in nodes:
        pth = _shape_to_path(tag, attrs)
        if pth is not None and not pth.isEmpty():
            out.append(pth)
    return tuple(out)


def icon_exists(name: str) -> bool:
    return bool(_tabler_nodes("outline").get(name))


# Diccionario ES -> iconos Tabler para el director y la busqueda (ampliable).
ICON_ES = {
    "diente": "dental", "dientes": "dental", "dental": "dental", "dentista": "dental",
    "limpieza": "sparkles", "brillo": "sparkles", "limpio": "sparkles",
    "precio": "cash", "costo": "cash", "pesos": "cash", "dinero": "cash", "pago": "credit-card",
    "ubicacion": "map-pin", "ubicada": "map-pin", "direccion": "map-pin", "avenida": "map-2",
    "calle": "road", "mapa": "map", "cerca": "map-pin", "glorieta": "map-pin",
    "horario": "clock", "horarios": "clock", "hora": "clock", "tiempo": "hourglass",
    "cita": "calendar-event", "agenda": "calendar-event", "citas": "calendar-event",
    "link": "link", "enlace": "link", "abajo": "arrow-down", "click": "click",
    "telefono": "phone", "llamar": "phone", "whatsapp": "brand-whatsapp",
    "sonrisa": "mood-smile", "feliz": "mood-happy", "hablar": "message-circle",
    "necesitamos": "alert-circle", "atencion": "alert-triangle", "importante": "alert-circle",
    "si": "check", "correcto": "circle-check", "no": "x", "error": "circle-x",
    "seguir": "user-plus", "sigueme": "user-plus", "comparte": "share", "guarda": "bookmark",
    "corazon": "heart", "amor": "heart", "estrella": "star", "mejor": "trophy",
    "rapido": "bolt", "energia": "bolt", "idea": "bulb", "secreto": "lock-open",
    "salud": "heartbeat", "doctor": "stethoscope", "doctora": "stethoscope",
    "cepillo": "brush", "sentir": "hand-finger", "ver": "eye",
    "casa": "home", "negocio": "building-store", "tienda": "building-store",
    "video": "video", "musica": "music", "foto": "camera", "regalo": "gift",
    "descuento": "discount", "oferta": "tag", "nuevo": "sparkles", "gratis": "gift",
    "pregunta": "help-circle", "porque": "help-circle", "como": "help-circle",
}


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(ch for ch in s if ch.isalnum() or ch in "- ")


def icon_search(query: str, limit: int = 5) -> list[str]:
    """Busca iconos por palabra (ES via ICON_ES, EN via nombre/tags de Tabler)."""
    q = _norm(query).strip()
    if not q:
        return []
    res: list[str] = []
    for w in q.split():
        if w in ICON_ES and icon_exists(ICON_ES[w]):
            res.append(ICON_ES[w])
    meta = _tabler_meta()
    words = [w for w in q.split() if len(w) > 2]
    scored = []
    for name, m in meta.items():
        tags = {str(t).lower() for t in m.get("tags", [])}
        parts = name.split("-")
        s = 0
        for w in words:
            if name == w:
                s += 10
            elif w in parts:
                s += 6
            elif any(p.startswith(w) for p in parts):
                s += 2  # prefijo de palabra completa ("tooth" no casa con "bluetooth")
            if w in tags:
                s += 3
        if s and "outline" in m.get("styles", {}):
            scored.append((s, -len(name), name))
    for _, _, name in sorted(scored, reverse=True):
        if name not in res:
            res.append(name)
        if len(res) >= limit:
            break
    return res[:limit]


# ---------------------------------------------------------------- paletas por estilo
PALETTES = {
    "dinamico": {"primary": "#FFFFFF", "accent": "#FFD23F", "accent2": "#2EC4B6",
                 "dark": "#0E0E12", "light": "#F7F4EE", "warm": "#FF7A3D",
                 "fonts": {"title": "Poppins-Black", "caption": "Poppins-ExtraBold",
                           "serif": "DMSerifDisplay", "script": "Caveat-Bold", "impact": "Anton"}},
    "elegante": {"primary": "#FFFFFF", "accent": "#E8C27A", "accent2": "#A7C4BC",
                 "dark": "#101012", "light": "#F4EFE6", "warm": "#E8C27A",
                 "fonts": {"title": "DMSerifDisplay", "caption": "Poppins-SemiBold",
                           "serif": "InstrumentSerif", "script": "Caveat-Bold", "impact": "BebasNeue"}},
    "minimal": {"primary": "#FFFFFF", "accent": "#FFFFFF", "accent2": "#9AD1FF",
                "dark": "#111111", "light": "#FAFAFA", "warm": "#FFB36B",
                "fonts": {"title": "Montserrat-ExtraBold", "caption": "Montserrat-Bold",
                          "serif": "InstrumentSerif", "script": "Caveat", "impact": "BebasNeue"}},
    "energetico": {"primary": "#FFFFFF", "accent": "#FFE600", "accent2": "#FF3D7F",
                   "dark": "#0A0A0A", "light": "#FFFFFF", "warm": "#FF5A1F",
                   "fonts": {"title": "Anton", "caption": "Poppins-Black",
                             "serif": "DMSerifDisplay", "script": "PermanentMarker", "impact": "Bangers"}},
}


def palette(style: str | None) -> dict:
    return PALETTES.get(style or "dinamico", PALETTES["dinamico"])


# ---------------------------------------------------------------- catalogos
def sfx_catalog() -> dict:
    f = SFX_DIR / "catalog.json"
    return json.loads(f.read_text()) if f.exists() else {}


def sfx_file(name: str) -> Path | None:
    for ext in (".flac", ".ogg", ".m4a", ".wav"):
        p = SFX_DIR / f"{name}{ext}"
        if p.exists():
            return p
    return None
