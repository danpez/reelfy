"""Plan v2: defaults, migracion v1 -> v2, normalizacion, validacion y catalogo de kinds.

Contrato: docs/MOTION-ENGINE.md, seccion 3. `KINDS` es JSON-serializable y lo consume el
inspector del Studio (`/fx/schema`): por cada pista y kind/preset lista sus parametros con
`k` (clave), `type` (num|text|color|select|bool|seg|font|icon), `label` (espanol) y, segun
el tipo, `min`/`max`/`step`, `opts`, `default`. Extensiones:
  - `list: true`   el valor es una lista de ese tipo (p. ej. bg_colors, items, accent_words).
  - `len: n`       vector numerico de n componentes (path_to, pan).
  - `nullable`     admite null (p. ej. `box` sin caja).
  - `req: true`    campo obligatorio: normalize() lo completa con `default` si falta.
  - `unit`         unidad para la UI ("s", "px", "dB", "x", "grados", "st").
  - `hint`         texto de ayuda corto para el inspector (opcional).
  - claves con punto ("text.text") editan un objeto embebido (cutaways.text).
  - `type: lines`  lista de lineas de texto con estilo propio; `fields` describe cada una.
  - `type: rows`   lista de objetos (p. ej. music.stops); `fields` describe cada columna.
  - `roles`        (bg_colors) papel de cada indice de la lista por fondo (cutaways.BG_COLOR_ROLES).
  - num `nullable` con default null = "automatico" (el renderer decide; p. ej. punch.hold,
    graphics x/y con `anchor`, grade.clarity = la del look).
  - `nofill: true` ausente tiene significado propio (modo legacy): ni fill=True lo completa
    (p. ej. lower_third.align: sin clave = rotulo anclado por el borde izquierdo).

SIMETRIA (regla de la marca): todo overlay nace centrado en x = XC (0.5) y con ancho <= SYM_W
(0.76), el espejo de ctx.XC/ctx.SYM_W (este modulo es puro: sin skia). Los defaults de x y
alineacion los decide este esquema, no los modulos.

normalize() solo completa campos comunes y los `req`; los parametros opcionales ausentes se
dejan asi para que cada renderer aplique su propio default de preset (el `default` del
catalogo es el que muestra la UI). Con normalize(plan, fill=True) se completan todos.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from . import profile as _profile

VERSION = 2
XC = 0.5                  # espejo de ctx.XC: centro horizontal de todo overlay centrado
SYM_W = 0.76              # espejo de ctx.SYM_W: ancho maximo de un overlay centrado

# ---------------------------------------------------------------- defaults (seccion 3)
DEFAULT_ENTITIES = {"name": "", "role": "", "price": "", "address": "", "landmark": "",
                    "cta": "", "link_label": "", "handle": ""}
DEFAULT_DIRECTOR = {"style": "dinamico", "intensity": 0.75, "seed": 7, "locked": [],
                    "llm": "qwen3.5:4b"}
DEFAULT_CAPTIONS = {"enabled": True, "preset": "clean_bold", "y": 0.72, "size": 0.052,
                    "font": "Poppins-ExtraBold", "color": "#FFFFFF", "accent": "#FFD23F",
                    "case": "none", "max_words": 4, "hide_during_titles": True}
DEFAULT_MUSIC = {"enabled": True, "source": "ai", "track": None, "file": None, "prompt": "",
                 "bpm": 0, "gain_db": -17, "duck_db": -9, "intro_fade": 0.0, "fade_out": 1.5,
                 "stops": [], "swells": []}
DEFAULT_GRADE = {"enabled": True, "look": "warm_clean", "match_shots": True,
                 "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}

DEFAULTS = {
    "version": VERSION,
    "shots": [],
    "entities": DEFAULT_ENTITIES,
    "director": DEFAULT_DIRECTOR,
    "fx": {
        "captions": DEFAULT_CAPTIONS,
        "text": [], "graphics": [], "cutaways": [], "effects": [], "transitions": [], "sfx": [],
        "music": DEFAULT_MUSIC,
        "grade": DEFAULT_GRADE,
    },
}

LIST_TRACKS = ("text", "graphics", "cutaways", "effects", "transitions", "sfx", "media")
ID_PREFIX = {"text": "tx", "graphics": "gr", "cutaways": "cw", "effects": "fx",
             "transitions": "tr", "sfx": "sf", "media": "md"}
EVENT_TRACKS = ("transitions", "sfx")          # usan `at` en vez de start/end
MIN_ITEM = 0.05                                # s: duracion minima de un item

# ---------------------------------------------------------------- enumeraciones normativas
TEXT_PRESETS = ["title_pop", "stack", "cascade", "serif_elegant", "script_accent", "behind_giant",
                "highlight_marker", "typewriter", "counter", "kicker"]
TEXT_ANIM_IN = ["pop", "rise", "slide_left", "slide_right", "type", "mask", "fade", "scale_down",
                "word_stagger", "letter_stagger"]
TEXT_ANIM_OUT = ["fade", "pop_out", "slide_up", "none"]
CAPTION_PRESETS = ["clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps", "none"]
GRAPHIC_KINDS = ["doodle", "icon", "card", "lower_third", "cta", "progress", "sticker_text", "media",
                 "socials"]
DOODLES = ["sparkle", "star4", "star5", "burst", "rays", "arrow_curve", "arrow_straight",
           "arrow_down", "circle_scribble", "underline", "squiggle", "dotted_path", "heart",
           "check", "cross", "exclaim", "question", "swirl", "lightning", "loop"]
CARD_VARIANTS = ["price", "location", "schedule", "link", "phone", "rating", "checklist", "stat",
                 "generic", "booking"]
CTA_VARIANTS = ["follow", "link_below", "comment", "save", "share"]
GRAPHIC_ANIM_IN = ["pop", "draw", "fade", "rise", "scale_down", "slide_left", "slide_right", "none"]
GRAPHIC_ANIM_OUT = ["fade", "pop_out", "slide_up", "none"]
CUTAWAY_KINDS = ["title_card", "info_card", "photo", "self_broll"]
BACKGROUNDS = ["grid_warp", "gradient", "paper", "dark_stars", "rays", "blur_self", "color",
               "halftone"]
EFFECT_KINDS = ["zoom", "punch", "shake", "kenburns", "bw", "color_pop", "bg_blur", "bg_replace",
                "subject_outline", "rgb_split", "flash_frame"]
TRANSITION_KINDS = ["cut", "flash", "light_leak", "whip", "zoom_blur", "glitch", "film_burn",
                    "torn_paper", "circle", "slide"]
SFX_NAMES = ["whoosh_soft", "whoosh_fast", "swoosh_up", "swoosh_down", "whip", "pop", "pop_soft",
             "bubble", "click", "tick", "typing", "ding", "bell", "chime", "sparkle", "cash", "coin",
             "riser_short", "riser_long", "impact_soft", "impact_boom", "sub_drop", "glitch",
             "shutter", "notification", "success", "swipe", "scratch", "paper_rip",
             "pen_scribble", "heartbeat", "record_stop"]
LOOKS = ["natural", "warm_clean", "cinematic", "bright_pop", "moody", "bw_contrast"]
STYLES = ["dinamico", "elegante", "minimal", "energetico"]
# plataformas del perfil (motion/profile.py): icono de marca de chips, CTA y tarjetas
PLATFORM_IDS = list(_profile.PLATFORMS)
# transiciones que MEZCLAN A (congelado) con B: su ventana siempre empieza en la union
# (transitions.MIXERS); `align` solo aplica a las centradas
TRANSITION_MIXERS = ["film_burn", "torn_paper", "circle", "slide"]
FACE_ANCHORS = ["face", "face_left", "face_right", "face_top"]
BW_TONES = ["neutral", "warm", "cool", "sepia"]
# recursos del guion (pista fx.media): estilo de presentacion, ajuste del clip al hueco y origen
MEDIA_STYLES = ["float_card"]
MEDIA_FITS = ["hold", "speed", "trim"]
MEDIA_FILE_KINDS = ["image", "video"]
ITEM_SRCS = ("ai", "user", "script")          # script = lo puso el guion (montaje con recursos)
EASINGS = ["linear", "in_quad", "out_quad", "in_out_quad", "in_cubic", "out_cubic",
           "in_out_cubic", "out_quart", "in_expo", "out_expo", "in_out_expo", "out_back",
           "in_back", "out_elastic", "out_bounce", "smoothstep"]

try:  # nombres de fuentes registradas (solo para opts del inspector)
    from . import assets as _assets
    FONTS = _assets.list_fonts()
except Exception:  # pragma: no cover
    FONTS = ["Poppins-ExtraBold"]


# ---------------------------------------------------------------- helpers de esquema
# `lines`: lista de objetos {text, scale, font, color, italic} (sus campos van en `fields`)
# `media`: medio propio subido por POST /sources: {sid, type: image|video, name, w, h, dur}
#          (+ thumb, que el motor ignora). render lo resuelve por sid (render.media_path).
KINDS_TYPES = ("num", "text", "color", "select", "bool", "seg", "font", "icon", "lines", "rows",
               "media")

# ortografia de las etiquetas del catalogo (UTF-8). Red de seguridad para etiquetas que
# publican los modulos duenos en ASCII: accent_es() corrige solo palabras conocidas y respeta
# Mayuscula inicial y TODO MAYUSCULAS (idempotente: una palabra ya acentuada no cambia).
_ACCENTS = {
    "accion": "acción", "ademas": "además", "alineacion": "alineación",
    "amortiguacion": "amortiguación", "angulo": "ángulo", "animacion": "animación",
    "animo": "ánimo", "anticipacion": "anticipación", "aspero": "áspero",
    "automatica": "automática", "automatico": "automático", "boton": "botón", "caida": "caída",
    "calido": "cálido", "camara": "cámara", "cancion": "canción", "catalogo": "catálogo",
    "circulo": "círculo", "codigo": "código", "descripcion": "descripción", "detras": "detrás",
    "dinamico": "dinámico", "direccion": "dirección", "dramatica": "dramática",
    "dramatico": "dramático", "dramaticos": "dramáticos", "dueno": "dueño",
    "duracion": "duración", "energetico": "energético", "espanol": "español",
    "exposicion": "exposición", "frio": "frío", "grafico": "gráfico", "graficos": "gráficos",
    "inclinacion": "inclinación", "informacion": "información", "linea": "línea",
    "lineas": "líneas", "maquina": "máquina", "maxima": "máxima", "maximas": "máximas",
    "maximo": "máximo", "mayusculas": "mayúsculas", "minima": "mínima", "minimo": "mínimo",
    "modulo": "módulo", "musica": "música", "numero": "número", "opcion": "opción",
    "parametro": "parámetro", "parametros": "parámetros", "pelicula": "película",
    "posicion": "posición", "proporcion": "proporción", "rapido": "rápido",
    "reproduccion": "reproducción", "rotacion": "rotación", "rotulo": "rótulo",
    "saturacion": "saturación", "segun": "según", "separacion": "separación",
    "sobreexposicion": "sobreexposición", "subtitulo": "subtítulo", "subtitulos": "subtítulos",
    "tamano": "tamaño", "tambien": "también", "telefono": "teléfono", "titulo": "título",
    "titulos": "títulos", "transicion": "transición", "ubicacion": "ubicación",
    "ultima": "última", "ultimo": "último", "union": "unión", "vacia": "vacía", "vacio": "vacío",
    "vacios": "vacíos", "valido": "válido", "variacion": "variación", "vinieta": "viñeta",
}
# con _ y digitos: los identificadores no cambian; {variables} de una plantilla de la copy tampoco
# ({titulos} en el ejemplo de «Cantidad de efectos» lo llena el Studio con su nombre exacto)
_WORD = re.compile(r"\{[A-Za-z0-9_]+\}|[A-Za-zÀ-ÿ0-9_]+")


def accent_es(s: Any) -> Any:
    """Etiqueta con su ortografia en espanol (solo palabras del mapa; las {variables} no se tocan)."""
    if not isinstance(s, str):
        return s

    def fix(m):
        w = m.group(0)
        if w[0] == "{":
            return w
        a = _ACCENTS.get(w.lower()) if w.isalpha() else None
        if a is None:
            return w
        if len(w) > 1 and w.isupper():
            return a.upper()
        return a[0].upper() + a[1:] if w[0].isupper() else a
    return _WORD.sub(fix, s)


_ACCENT_TEXT = ("label", "hint", "desc", "help", "ex_ui", "ex_tpl")
_ACCENT_BY = ("label_by", "help_by", "note_by")


def _accent_catalog(o: Any) -> Any:
    """Aplica accent_es a los textos visibles de todo el catalogo (en sitio): label/hint/desc, la
    copy de la UI (help, ex_ui, ex_tpl) y los valores de label_by/help_by/note_by."""
    if isinstance(o, list):
        for x in o:
            _accent_catalog(x)
    elif isinstance(o, dict):
        for k, v in o.items():
            if k in _ACCENT_TEXT and isinstance(v, str):
                o[k] = accent_es(v)
            elif k in _ACCENT_BY and isinstance(v, dict):
                for kk, vv in v.items():
                    if isinstance(vv, str):
                        v[kk] = accent_es(vv)
            else:
                _accent_catalog(v)
    return o


# ---------------------------------------------------------------- copy de la interfaz
# ui_copy.json: etiqueta, que hace, ejemplo y nivel (Basico / Avanzado / oculto) de cada ajuste,
# nombres de las opciones, secciones, notas de reglas y textos fijos del Studio (es-MX). Es la
# fuente unica de la copy: /fx/schema la publica entera como `ui` y _apply_ui_copy la pone en
# cada parametro del catalogo.
try:
    UI_COPY: dict = json.loads((__import__("pathlib").Path(__file__).with_name("ui_copy.json")).read_text(encoding="utf-8"))
except Exception:  # pragma: no cover - sin copy el catalogo base sigue sirviendo
    UI_COPY = {"version": 0, "params": {}, "options": {}, "ui": {}, "sections": {}, "notes": {}, "strings": {},
               "aliases": {}, "deny": []}
# campos de la copy que se copian a cada parametro (los demas son notas del build: units, where...)
_COPY_FIELDS = ("help", "ex", "ex_ui", "ex_tpl", "basic_choices", "relative_choices", "adv", "adv_label",
                "label_by", "help_by", "note_by", "level_by")


def _copy_entry(ids: list) -> dict | None:
    P_ = UI_COPY.get("params") or {}
    for i in ids:
        if i in P_:
            return P_[i]
    return None


def _apply_copy(prm: dict, ids: list, keys: tuple = ()) -> None:
    """Pone la copy de `ids` (el primero que exista) en el parametro: label/level resueltos para
    el kind o la pista (`keys`), help, ejemplo y opciones humanas. `hint` se conserva."""
    e = _copy_entry(ids)
    if not e:
        return
    by = lambda m, d: next((m[k] for k in keys if isinstance(m, dict) and k in m), d)  # noqa: E731
    prm["label"] = by(e.get("label_by"), e.get("label") or prm.get("label"))
    prm["level"] = by(e.get("level_by"), e.get("level") or "basic")
    for f in _COPY_FIELDS:
        if f in e:
            prm[f] = copy.deepcopy(e[f])
    if prm.get("type") in ("rows", "lines") and prm.get("fields"):
        for fld in prm["fields"]:
            _apply_copy(fld, [f"{i}.{fld['k']}" for i in ids], keys)


def _apply_copy_params(params: list, group: str) -> None:
    for prm in params or []:
        _apply_copy(prm, [f"{group}.{prm['k']}"])


def _apply_ui_copy(kinds: dict) -> dict:
    """Copy de la UI en todo el catalogo (en sitio): grupos del proyecto, campos comunes, cada
    parametro de cada kind (orden <pista>.<kind>.<k>, <pista>.<k>, common.<k>) y el nombre de
    cada kind (options["<pista>.kind"], text.preset o sfx.name). Publica la copy entera en `ui`."""
    for g in ("captions", "music", "grade", "director"):
        if g in kinds:
            _apply_copy_params(kinds[g].get("params"), g)
    for grp in (kinds.get("common") or {}).values():
        for prm in grp or []:
            _apply_copy(prm, [f"common.{prm['k']}"])
    opts = UI_COPY.get("options") or {}
    for tk, T in (kinds.get("tracks") or {}).items():
        names = opts.get({"text": "text.preset", "sfx": "sfx.name"}.get(tk, f"{tk}.kind")) or {}
        for kind, spec in (T.get("items") or {}).items():
            if kind in names and names[kind].get("label"):
                spec["label"] = names[kind]["label"]
            for prm in spec.get("params") or []:
                k = prm["k"]
                _apply_copy(prm, [f"{tk}.{kind}.{k}", f"{tk}.{k}", f"common.{k}"], (kind, tk))
    kinds["ui"] = UI_COPY
    return kinds


def P(k: str, type: str, label: str, default: Any = None, **kw) -> dict:
    d = {"k": k, "type": type, "label": label, "default": default}
    d.update(kw)
    return d


def num(k, label, default, lo, hi, step=0.01, **kw):
    return P(k, "num", label, default, min=lo, max=hi, step=step, **kw)


def sel(k, label, opts, default, seg=False, **kw):
    return P(k, "seg" if seg else "select", label, default, opts=list(opts), **kw)


# campos comunes
COMMON_RANGE = [
    num("start", "Inicio", 0.0, 0.0, 36000.0, 0.01, unit="s", req=True),
    num("end", "Fin", 1.0, 0.0, 36000.0, 0.01, unit="s", req=True),
    P("enabled", "bool", "Activo", True, req=True),
    P("locked", "bool", "Bloqueado (la IA no lo toca)", False, req=True),
    num("intensity", "Intensidad", 1.0, 0.0, 1.0, 0.05, req=True),
    num("z", "Orden (capa)", 0, -50, 50, 1),
]
COMMON_EVENT = [
    num("at", "Momento", 0.0, 0.0, 36000.0, 0.01, unit="s", req=True),
    P("enabled", "bool", "Activo", True, req=True),
    P("locked", "bool", "Bloqueado (la IA no lo toca)", False, req=True),
]

# ---------------------------------------------------------------- text
TEXT_COMMON = [
    P("text", "text", "Texto (Enter = nueva línea)", "", multiline=True),
    num("x", "Posición X", 0.5, 0.0, 1.0, 0.005),
    num("y", "Posición Y", 0.3, 0.0, 1.0, 0.005),
    num("size", "Tamaño", 0.09, 0.02, 0.45, 0.005),
    P("font", "font", "Fuente", "Poppins-Black", opts=FONTS),
    P("color", "color", "Color", "#FFFFFF"),
    P("accent", "color", "Color de acento", "#FFD23F"),
    P("accent_words", "text", "Palabras en acento", [], list=True),
    # x es el CENTRO del bloque con cualquier alineacion (text._place, simetria)
    sel("align", "Alineación", ["left", "center", "right"], "center", seg=True,
        hint="Justifica las líneas dentro del bloque; el bloque va centrado en X"),
    num("stroke", "Contorno", 0.0, 0.0, 24.0, 0.5, unit="px"),
    num("shadow", "Sombra", 0.35, 0.0, 1.0, 0.05),
    sel("anim_in", "Animación de entrada", TEXT_ANIM_IN, "pop"),
    sel("anim_out", "Animación de salida", TEXT_ANIM_OUT, "fade"),
    P("behind", "bool", "Detrás de la persona", False),
    P("box", "color", "Caja de fondo", None, nullable=True),
    # lineas con estilo propio (contrato 3.1): reemplazan a `text` si vienen
    P("lines", "lines", "Líneas con estilo propio", None, nullable=True, list=True,
      fields=[P("text", "text", "Texto", ""), num("scale", "Escala", 1.0, 0.2, 4.0, 0.05),
              P("font", "font", "Fuente", None, opts=FONTS, nullable=True),
              P("color", "color", "Color", None, nullable=True),
              P("italic", "bool", "Cursiva", False)]),
]
LINE_FIELDS = {"text", "scale", "font", "color", "italic"}
TEXT_PRESET_DEFAULTS = {
    "title_pop":        {"label": "Título pop", "font": "Poppins-Black", "size": 0.09, "y": 0.26,
                         "anim_in": "pop"},
    "stack":            {"label": "Apilado bold", "font": "Anton", "size": 0.11, "y": 0.28,
                         "anim_in": "word_stagger"},
    # la escalera va DENTRO del bloque; el bloque entero se centra en x (simetria)
    "cascade":          {"label": "Cascada", "font": "Poppins-ExtraBold", "size": 0.07, "y": 0.22,
                         "align": "center", "x": XC, "anim_in": "word_stagger"},
    "serif_elegant":    {"label": "Serif elegante", "font": "DMSerifDisplay", "size": 0.075,
                         "y": 0.24, "anim_in": "rise"},
    "script_accent":    {"label": "Manuscrita", "font": "Caveat-Bold", "size": 0.08, "y": 0.3,
                         "anim_in": "mask"},
    "behind_giant":     {"label": "Gigante detrás", "font": "Anton", "size": 0.3, "y": 0.3,
                         "behind": True, "anim_in": "scale_down"},
    "highlight_marker": {"label": "Marcador", "font": "Poppins-ExtraBold", "size": 0.065,
                         "y": 0.3, "box": "#FFD23F", "color": "#111111", "anim_in": "mask"},
    "typewriter":       {"label": "Máquina de escribir", "font": "SpaceMono-Bold", "size": 0.055,
                         "y": 0.3, "anim_in": "type"},
    "counter":          {"label": "Contador", "font": "Poppins-Black", "size": 0.14, "y": 0.3,
                         "anim_in": "pop"},
    "kicker":           {"label": "Etiqueta (kicker)", "font": "Poppins-SemiBold", "size": 0.04,
                         "y": 0.2, "box": "#111111", "anim_in": "rise"},
}
TEXT_PRESET_EXTRA = {
    "counter": [num("from", "Desde", 0, -1e9, 1e9, 1), num("to", "Hasta", 1000, -1e9, 1e9, 1),
                P("prefix", "text", "Prefijo", "$"), P("suffix", "text", "Sufijo", ""),
                num("decimals", "Decimales", 0, 0, 3, 1),
                P("sep", "text", "Separador de miles", ","),
                # centro: el numero crece hacia ambos lados (simetrico en cada cuadro)
                sel("anchor", "Crece desde", ["right", "center", "left"], "center", seg=True),
                # text._counter_state: vacio = min(1.3, max(0.4, 0.55 * duracion del item))
                num("count_dur", "Duración del conteo", None, 0.1, 5.0, 0.05, unit="s",
                    nullable=True, hint="Vacío = automático (según la duración)")],
}

# ---------------------------------------------------------------- captions
CAPTIONS_PARAMS = [
    P("enabled", "bool", "Mostrar subtítulos", True),
    sel("preset", "Estilo", CAPTION_PRESETS, "clean_bold"),
    num("y", "Posición Y", 0.72, 0.1, 0.9, 0.005),
    num("size", "Tamaño", 0.052, 0.02, 0.12, 0.002),
    P("font", "font", "Fuente", "Poppins-ExtraBold", opts=FONTS),
    P("color", "color", "Color", "#FFFFFF"),
    P("accent", "color", "Color palabra activa", "#FFD23F"),
    sel("case", "Mayúsculas", ["none", "upper", "lower", "title"], "none", seg=True),
    num("max_words", "Palabras por línea", 4, 1, 8, 1),
    P("hide_during_titles", "bool", "Ocultar durante títulos", True),
    # finos (null = lo que dicta el estilo)
    num("x", "Posición X", 0.5, 0.1, 0.9, 0.005),
    num("max_lines", "Líneas máximas", None, 1, 4, 1, nullable=True),
    P("kw_accent", "bool", "Palabra clave en acento", None, nullable=True),
    P("recenter", "bool", "Recentrar al entrar palabras", None, nullable=True),
    num("stroke", "Contorno", None, 0.0, 12.0, 0.5, unit="px", nullable=True),
    num("shadow", "Sombra", None, 0.0, 1.0, 0.05, nullable=True),
    P("box", "color", "Caja de fondo", None, nullable=True),
    P("highlight", "color", "Color de resaltado", None, nullable=True),
    # altura por tramos (text._cap_dy): [{start, end, y}] en tiempo ORIGINAL; p. ej. subirlos
    # al pecho en tomas de cuerpo completo. Fuera de los tramos manda `y`.
    P("y_ranges", "rows", "Altura por tramos", [], hint="Fuera de los tramos se usa Posición Y",
      fields=[num("start", "Inicio", None, 0.0, 36000.0, 0.01, unit="s", req=True, nullable=True),
              num("end", "Fin", None, 0.0, 36000.0, 0.01, unit="s", req=True, nullable=True),
              num("y", "Posición Y", 0.6, 0.1, 0.9, 0.005)]),
]

# ---------------------------------------------------------------- graphics
GRAPHIC_COMMON = [
    # x/y vacios + `anchor` = junto a la cara (graficos que lo soportan)
    num("x", "Posición X", 0.5, 0.0, 1.0, 0.005, nullable=True),
    num("y", "Posición Y", 0.3, 0.0, 1.0, 0.005, nullable=True),
    num("size", "Tamaño", 0.18, 0.02, 1.0, 0.005),
    P("color", "color", "Color", "#FFFFFF"),
    sel("anim_in", "Animación de entrada", GRAPHIC_ANIM_IN, "pop"),
    sel("anim_out", "Animación de salida", GRAPHIC_ANIM_OUT, "fade"),
    num("rotation", "Rotación", 0.0, -180.0, 180.0, 1.0, unit="grados"),
    num("in_dur", "Duración de la entrada", None, 0.05, 3.0, 0.05, unit="s", nullable=True),
    num("out_dur", "Duración de la salida", None, 0.05, 3.0, 0.05, unit="s", nullable=True),
]
_ANCHOR = sel("anchor", "Junto a la cara (sin X/Y)", FACE_ANCHORS, None, nullable=True,
              hint="Deja X e Y vacíos para que siga a la cara")
GRAPHIC_KIND_PARAMS = {
    "doodle": {"label": "Garabato", "params": [
        sel("name", "Figura", DOODLES, "sparkle", req=True),
        num("stroke", "Grosor", 8.0, 1.0, 40.0, 0.5, unit="px"),
        P("draw", "bool", "Se dibuja solo", True),
        num("wiggle", "Temblor a mano", 0.3, 0.0, 1.0, 0.05),
        num("path_to", "Destino (flechas)", None, 0.0, 1.0, 0.005, len=2, nullable=True),
        sel("style", "Trazo", ["brush", "sketch"], "brush", seg=True,
            hint="sketch = contorno hueco aspero con rayado"),
        dict(_ANCHOR),
        num("bend", "Curvatura (flechas)", None, -1.0, 1.0, 0.02, nullable=True),
        num("glow", "Halo", 0.0, 0.0, 1.0, 0.05),
        num("shadow", "Sombra", 0.55, 0.0, 1.0, 0.05),
        num("draw_time", "Tiempo de trazo", None, 0.1, 3.0, 0.05, unit="s", nullable=True)]},
    "icon": {"label": "Icono", "params": [
        P("name", "icon", "Icono", "sparkles", req=True),
        sel("variant", "Variante", ["outline", "filled"], "outline", seg=True),
        sel("badge", "Insignia", ["none", "circle", "squircle"], "none", seg=True),
        P("badge_color", "color", "Color de insignia", "#FFD23F"),
        P("draw", "bool", "Se dibuja solo", True),
        sel("style", "Estilo", ["clean", "sketch"], "clean", seg=True),
        dict(_ANCHOR),
        num("shadow", "Sombra", 0.6, 0.0, 1.0, 0.05)]},
    "card": {"label": "Tarjeta", "params": [
        sel("variant", "Tipo", CARD_VARIANTS, "generic", req=True),
        P("title", "text", "Título", ""), P("subtitle", "text", "Subtítulo", ""),
        P("value", "text", "Valor", ""), P("items", "text", "Elementos", [], list=True),
        P("icon", "icon", "Icono", ""),
        sel("theme", "Tema", ["light", "dark", "brand"], "light", seg=True),
        P("accent", "color", "Color de acento", None, nullable=True),
        P("label", "text", "Texto del botón", ""),
        sel("platform", "Plataforma (icono)", PLATFORM_IDS, None, nullable=True,
            hint="Enlace o reservas: icono de la red o de la página"),
        P("display", "text", "Enlace corto", "", hint="p. ej. paseo.club/tu-nombre (subtítulo si no hay)"),
        # logo del enlace (linkmedia lo obtiene de la pagina o el usuario lo sube): reemplaza al
        # icono del encabezado. `aux`: no es el medio principal del kind (fxcore.mediaParam)
        P("logo", "media", "Logo", None, nullable=True, accept=["image"], aux=True,
          hint="Reemplaza al icono (p. ej. el logo de tu página de reservas)")]},
    "lower_third": {"label": "Rótulo (lower third)", "params": [
        P("title", "text", "Título", "", req=True), P("subtitle", "text", "Subtítulo", ""),
        sel("theme", "Tema", ["dark", "light"], "dark", seg=True),
        P("accent", "color", "Color de acento", None, nullable=True),
        # centrado (default) = x es el CENTRO del panel; izquierda = x es el borde izquierdo.
        # Sin la clave = rotulo de un plan viejo (izquierda): normalize no la rellena
        sel("align", "Alineación", ["center", "left"], "center", seg=True, nofill=True,
            hint="Centrado = simétrico (X es el centro); izquierda = X es el borde izquierdo")]},
    "cta": {"label": "Llamada a la acción", "params": [
        sel("variant", "Tipo", CTA_VARIANTS, "follow", req=True),
        P("title", "text", "Nombre", "", hint="Seguir: nombre arriba y la red + @usuario abajo"),
        P("label", "text", "Texto", ""), P("handle", "text", "Usuario", ""),
        sel("platform", "Red", PLATFORM_IDS, None, nullable=True,
            hint="Icono de la red junto al usuario (Seguir) o del enlace"),
        P("display", "text", "Enlace corto", "", hint="Link abajo: segunda línea (paseo.club/tu-nombre)"),
        P("subtitle", "text", "Subtítulo", ""),
        P("icon", "icon", "Icono", None, nullable=True),
        P("button", "text", "Botón", "Seguir"),
        P("button_done", "text", "Botón pulsado", "Siguiendo"),
        P("button_color", "color", "Color del botón", None, nullable=True),
        sel("avatar", "Avatar", ["initial", "face", "image"], "initial", seg=True,
            hint="Link abajo: imagen = la foto redonda en lugar del icono"),
        P("initials", "text", "Iniciales", "", hint="Vacío = del nombre"),
        P("media", "media", "Foto de perfil", None, nullable=True, accept=["image"],
          hint="Seguir: foto de perfil (si no, iniciales). Link abajo: foto del lugar"),
        P("logo", "media", "Logo del sitio", None, nullable=True, accept=["image"], aux=True,
          hint="Link abajo: insignia en la foto (o en lugar del icono). Seguir: insignia de la red"),
        num("width", "Ancho de la pastilla", None, 0.3, SYM_W, 0.01, nullable=True,
            hint="Seguir y Link abajo: ancho común de la tarjeta final (vacío = el natural)")]},
    "progress": {"label": "Barra de progreso", "params": [
        num("thickness", "Grosor", 8.0, 2.0, 30.0, 0.5, unit="px"),
        num("bar_y", "Posición vertical", 0.0, 0.0, 1.0, 0.005),
        num("margin", "Margen", 0.0, 0.0, 200.0, 1.0, unit="px")]},
    "sticker_text": {"label": "Etiqueta", "params": [
        P("text", "text", "Texto", "", req=True), P("bg", "color", "Fondo", "#FFD23F"),
        P("fg", "color", "Texto", "#111111"),
        P("font", "font", "Fuente", None, opts=FONTS, nullable=True),
        P("border", "bool", "Borde", True)]},
    # redes del perfil: chips con el icono de marca + @usuario (fila o columna, escalonados) o
    # marca de agua sutil
    "socials": {"label": "Redes sociales", "params": [
        P("links", "rows", "Redes", [], fields=[
            sel("platform", "Red", PLATFORM_IDS, "instagram"),
            P("text", "text", "Usuario o enlace", ""),
            P("logo", "media", "Logo", None, nullable=True, accept=["image"], aux=True,
              hint="Reservas o web: logo del sitio en la insignia")]),
        sel("layout", "Disposición", ["column", "row", "grid"], "column", seg=True,
            hint="grid = rejilla de chips del mismo ancho"),
        num("cols", "Columnas (rejilla)", 2, 1, 4, 1),
        num("width", "Ancho de la rejilla", None, 0.3, SYM_W, 0.01, nullable=True,
            hint="Vacío = el del chip más ancho"),
        sel("variant", "Tipo", ["chips", "watermark"], "chips", seg=True,
            hint="watermark = marca de agua sutil con el usuario"),
        sel("theme", "Tema", ["light", "dark"], "light", seg=True),
        sel("icon_style", "Iconos", ["brand", "mono"], "brand", seg=True,
            hint="brand = colores de cada red"),
        P("title", "text", "Encabezado", "", hint="p. ej. Encuéntrame en"),
        num("stagger", "Escalonado", 0.09, 0.0, 0.5, 0.01, unit="s"),
        num("opacity", "Opacidad (marca de agua)", 0.72, 0.15, 1.0, 0.05),
        num("backdrop", "Fondo translúcido (marca de agua)", 0.34, 0.0, 1.0, 0.02,
            hint="Pastilla oscura detrás del usuario: se lee sobre fondos claros")]},
    # imagen o clip del usuario flotando sobre el video (picture in picture). render resuelve
    # `media.sid`, decodifica el cuadro del tiempo local e inyecta item['_media_image']
    "media": {"label": "Imagen o video propio", "params": [
        P("media", "media", "Archivo", None, nullable=True, accept=["image", "video"],
          hint="Sube una imagen o un clip, o suéltalo sobre la pista"),
        num("aspect", "Proporción (ancho/alto)", None, 0.3, 3.0, 0.01, nullable=True,
            hint="Vacío = la del archivo")]},
}
# parametros de encuadre del medio (compartidos por graphics.media y las escenas con medio)
MEDIA_FIT = [
    sel("fit", "Ajuste", ["cover", "contain"], "cover", seg=True,
        hint="cover = llena el hueco recortando; contain = completo"),
    num("focus", "Punto de interés", None, 0.0, 1.0, 0.005, len=2, nullable=True,
        hint="Qué parte del archivo queda a la vista al recortar (vacío = el centro)"),
    num("zoom", "Acercamiento", 1.0, 1.0, 4.0, 0.01, unit="x"),
]
MEDIA_CLIP = [
    num("media_in", "Inicio del clip", 0.0, 0.0, 36000.0, 0.05, unit="s",
        hint="Desde dónde se reproduce el clip propio"),
    P("loop", "bool", "Repetir el clip", True, hint="Si es más corto que el elemento; si no, se congela"),
]
GRAPHIC_KIND_PARAMS["media"]["params"] += MEDIA_FIT + [
    P("frame", "bool", "Marco blanco", True),
    num("radius", "Esquinas redondeadas", 14.0, 0.0, 80.0, 0.5, unit="px"),
    num("shadow", "Sombra", 0.55, 0.0, 1.0, 0.05)] + MEDIA_CLIP
# defaults de los parametros comunes por kind. El PiP nace sin posicion (x/y null): render lo
# coloca solo lejos de la cara y de los subtitulos (render.media_auto_place, doc 4.2b) y lo
# achica si no cabe; con x/y el usuario manda. Los demas nacen CENTRADOS (x = XC, simetria):
# estas claves no se adoptan de graphics.DEFAULTS (red de seguridad si el modulo cambia).
GRAPHIC_KIND_DEFAULTS = {"media": {"x": None, "y": None, "size": 0.36},
                         **{k: {"x": XC} for k in ("doodle", "icon", "card", "lower_third", "cta",
                                                   "sticker_text", "socials")}}
GRAPHIC_KIND_HINTS = {"lower_third": {"x": "Centrado: centro del rótulo; izquierda: su borde izquierdo"},
                      "media": {
    "x": "Vacío = automático: lejos de la cara y de los subtítulos",
    "y": "Vacío = automático: lejos de la cara y de los subtítulos",
    "size": "Ancho máximo de la tarjeta (en automático puede achicarse para no tapar la cara)"}}
# la barra de progreso solo usa color/animaciones propias y su `bar_y` (x/y/size no aplican)
PROGRESS_COMMON = [
    P("color", "color", "Color", "#FFFFFF"),
    sel("anim_in", "Animación de entrada", ["fade", "none"], "fade", seg=True),
    sel("anim_out", "Animación de salida", ["none", "fade"], "none", seg=True),
]

# ---------------------------------------------------------------- cutaways
CUTAWAY_COMMON = [
    sel("background", "Fondo", BACKGROUNDS, "gradient"),
    P("bg_colors", "color", "Colores del fondo", [], list=True),
    P("text.text", "text", "Texto", "", multiline=True),
    sel("text.preset", "Estilo del texto", TEXT_PRESETS, "title_pop"),
    num("in_dur", "Duración de la entrada", None, 0.05, 2.0, 0.05, unit="s", nullable=True),
    num("out_dur", "Duración de la salida", None, 0.05, 2.0, 0.05, unit="s", nullable=True),
]
CUTAWAY_KIND_PARAMS = {
    "title_card": {"label": "Tarjeta de título", "params": []},
    "info_card": {"label": "Tarjeta de información", "params": [
        sel("variant", "Tarjeta propia", CARD_VARIANTS, None, nullable=True,
            hint="Vacío = la tarjeta va en gráficos"),
        P("title", "text", "Título", ""), P("subtitle", "text", "Subtítulo", ""),
        P("value", "text", "Valor", ""), P("items", "text", "Elementos", [], list=True)]},
    "photo": {"label": "Foto / cuadro congelado", "params": [
        P("media", "media", "Foto o clip propio", None, nullable=True, accept=["image", "video"],
          hint="Vacío = un cuadro congelado del video"),
        P("caption", "text", "Pie de foto", ""),
        num("tilt", "Inclinación", None, -20.0, 20.0, 0.5, unit="grados", nullable=True),
        num("size", "Tamaño", 0.72, 0.3, 0.8, 0.01)] + MEDIA_FIT + MEDIA_CLIP},
    "self_broll": {"label": "Toma propia (B-roll)", "params": [
        P("media", "media", "Clip o imagen propia", None, nullable=True, accept=["video", "image"],
          hint="Vacío = otro momento del mismo video (Tiempo del material)"),
        num("src_t", "Tiempo del material", 0.0, 0.0, 36000.0, 0.01, unit="s", req=True),
        num("zoom_from", "Zoom inicial", 1.0, 1.0, 4.0, 0.01, unit="x"),
        num("zoom_to", "Zoom final", 1.15, 1.0, 4.0, 0.01, unit="x"),
        num("pan", "Desplazamiento", [0.0, 0.0], -1.0, 1.0, 0.01, len=2)]
        + [q for q in MEDIA_FIT if q["k"] != "zoom"] + MEDIA_CLIP},
}

# ---------------------------------------------------------------- effects
_CENTER = num("center", "Centro fijo", None, 0.0, 1.0, 0.005, len=2, nullable=True,
              hint="Vacío = la cara")
_TARGET = num("target", "Llevar la cara a", None, 0.0, 1.0, 0.005, len=2, nullable=True,
              hint="Reencuadre: a donde llega la cara en el zoom máximo")


def _fades(tin: float, tout: float) -> list[dict]:
    return [num("fade_in", "Entrada", tin, 0.0, 5.0, 0.05, unit="s"),
            num("fade_out", "Salida", tout, 0.0, 5.0, 0.05, unit="s")]


EFFECT_KIND_PARAMS = {
    "zoom": {"label": "Zoom suave", "params": [
        num("from", "Desde", 1.0, 1.0, 4.0, 0.01, unit="x"),
        num("to", "Hasta", 1.15, 1.0, 4.0, 0.01, unit="x"),
        sel("easing", "Curva", EASINGS, "in_out_cubic"),
        dict(_TARGET), dict(_CENTER)]},
    "punch": {"label": "Punch-in", "params": [
        num("amount", "Cantidad", 0.12, 0.0, 1.5, 0.01),
        num("hold", "Duración del golpe", None, 0.0, 10.0, 0.05, unit="s", nullable=True,
            hint="Vacío = hasta el final del efecto"),
        sel("mode", "Entrada", ["spring", "cut"], "spring", seg=True,
            hint="spring = resorte con rebote; cut = golpe seco"),
        sel("release", "Salida", ["smooth", "cut"], "smooth", seg=True),
        num("attack", "Ataque", 0.0, 0.0, 2.0, 0.01, unit="s", hint="0 = resorte natural"),
        num("freq", "Frecuencia del resorte", 4.2, 0.5, 20.0, 0.1, unit="Hz"),
        num("damping", "Amortiguación del resorte", 0.5, 0.05, 0.99, 0.01),
        dict(_TARGET), dict(_CENTER)]},
    "shake": {"label": "Sacudida", "params": [
        num("amp", "Amplitud", 10.0, 0.0, 200.0, 0.5, unit="px"),
        num("freq", "Frecuencia", 12.0, 0.2, 60.0, 0.5, unit="Hz"),
        num("decay", "Amortiguación", 0.6, 0.0, 1.0, 0.05, hint="0 = constante; 1 = muere en ~0.3 s"),
        num("rot", "Rotación máxima", 0.8, 0.0, 10.0, 0.1, unit="grados"),
        num("attack", "Ataque", 0.0, 0.0, 2.0, 0.01, unit="s")]},
    "kenburns": {"label": "Ken Burns", "params": [
        num("from", "Desde", 1.0, 1.0, 4.0, 0.01, unit="x"),
        num("to", "Hasta", 1.12, 1.0, 4.0, 0.01, unit="x"),
        num("pan", "Desplazamiento", [0.02, -0.01], -1.0, 1.0, 0.005, len=2),
        sel("easing", "Curva", EASINGS, "in_out_quad"),
        dict(_CENTER)]},
    "bw": {"label": "Blanco y negro", "params": [
        num("contrast", "Contraste", 1.2, 0.5, 2.0, 0.05),
        P("subject_color", "bool", "Persona a color", False),
        sel("tone", "Tono", BW_TONES, "neutral", seg=True),
        num("vignette", "Viñeta", 0.55, 0.0, 1.0, 0.05),
        num("lift", "Levantar negros", 0.06, 0.0, 0.2, 0.01),
        num("fade", "Fundido de entrada/salida", 0.06, 0.0, 2.0, 0.01, unit="s")]},
    "color_pop": {"label": "Color pop (fondo gris)", "params": [
        num("bg_desat", "Desaturar fondo", 1.0, 0.0, 1.0, 0.05),
        num("bg_dark", "Oscurecer fondo", 0.25, 0.0, 1.0, 0.05)] + _fades(0.3, 0.2)},
    "bg_blur": {"label": "Desenfoque de fondo", "params": [
        num("radius", "Radio", 24.0, 0.0, 200.0, 1.0, unit="px")] + _fades(0.4, 0.25)},
    "bg_replace": {"label": "Reemplazo de fondo", "params": [
        sel("background", "Fondo", BACKGROUNDS, "gradient"),
        P("bg_colors", "color", "Colores del fondo", [], list=True),
        num("grain", "Grano del fondo", 0.025, 0.0, 0.3, 0.005),
        num("vignette", "Viñeta del fondo", 0.22, 0.0, 1.0, 0.01)] + _fades(0.45, 0.3)},
    "subject_outline": {"label": "Contorno de la persona", "params": [
        P("color", "color", "Color", "#FFFFFF"),
        num("width", "Grosor", 8.0, 0.0, 80.0, 0.5, unit="px"),
        num("glow", "Brillo", 0.5, 0.0, 1.0, 0.05)]},
    "rgb_split": {"label": "Separación RGB", "params": [
        num("amount", "Cantidad", 8.0, 0.0, 200.0, 0.5, unit="px"),
        num("angle", "Ángulo", 0.0, -180.0, 180.0, 1.0, unit="grados"),
        num("radial", "Radial", 0.35, 0.0, 1.0, 0.05),
        num("jitter", "Variación por cuadro", 0.0, 0.0, 1.0, 0.05),
        num("attack", "Ataque", 0.04, 0.0, 2.0, 0.01, unit="s"),
        P("hold", "bool", "Sostener (sin caída)", False)]},
    "flash_frame": {"label": "Destello", "params": [
        P("color", "color", "Color", "#FFFFFF"),
        num("peak", "Intensidad máxima", 0.95, 0.0, 1.0, 0.05),
        num("bloom", "Sobreexposición", 0.8, 0.0, 2.0, 0.05),
        num("attack", "Ataque", 0.035, 0.0, 2.0, 0.005, unit="s")]},
}
MATTE_EFFECTS = ("color_pop", "bg_blur", "bg_replace", "subject_outline")

# ---------------------------------------------------------------- transitions / sfx
TRANSITION_PARAMS = [
    sel("kind", "Tipo", TRANSITION_KINDS, "flash", req=True),
    num("dur", "Duración", 0.35, 0.05, 2.0, 0.01, unit="s", req=True),
    sel("dir", "Dirección", ["left", "right", "up", "down"], "left", seg=True),
    P("color", "color", "Color", "#FFFFFF"),
    sel("align", "Alineación con la unión", ["auto", "center", "start", "end"], "auto", seg=True),
]
TRANSITION_LABELS = {"cut": "Corte", "flash": "Destello", "light_leak": "Fuga de luz",
                     "whip": "Barrido (whip)", "zoom_blur": "Zoom con desenfoque",
                     "glitch": "Glitch", "film_burn": "Quemado de película",
                     "torn_paper": "Papel rasgado", "circle": "Círculo", "slide": "Empuje"}
SFX_PARAMS = [
    sel("name", "Sonido", SFX_NAMES, "whoosh_soft", req=True),
    num("gain_db", "Volumen", -6.0, -40.0, 12.0, 0.5, unit="dB", hint="0 = pico al nivel de la voz"),
    num("pan", "Paneo", 0.0, -1.0, 1.0, 0.05),
    num("variant", "Variante", None, 0, 9, 1, nullable=True, hint="Vacío = automática"),
    num("pitch", "Tono", None, -12.0, 12.0, 0.5, unit="st", nullable=True,
        hint="Vacío = afinado a la tonalidad de la música"),
    num("dur", "Duración", None, 0.05, 10.0, 0.05, unit="s", nullable=True,
        hint="Risers: anticipación; continuos: recorta o repite"),
]
SFX_LABELS = {
    "whoosh_soft": "Whoosh suave", "whoosh_fast": "Whoosh rápido", "swoosh_up": "Swoosh arriba",
    "swoosh_down": "Swoosh abajo", "whip": "Latigazo", "pop": "Pop", "pop_soft": "Pop suave",
    "bubble": "Burbuja", "click": "Clic", "tick": "Tic", "typing": "Tecleo", "ding": "Ding",
    "bell": "Campana", "chime": "Campanitas", "sparkle": "Destello", "cash": "Caja registradora",
    "coin": "Moneda", "riser_short": "Subida corta", "riser_long": "Subida larga",
    "impact_soft": "Impacto suave", "impact_boom": "Impacto fuerte", "sub_drop": "Caída grave",
    "glitch": "Glitch", "shutter": "Obturador", "notification": "Notificación",
    "success": "Éxito", "swipe": "Deslizar", "scratch": "Scratch de disco",
    "paper_rip": "Papel rasgado", "pen_scribble": "Trazo de pluma", "heartbeat": "Latido",
    "record_stop": "Disco que frena",
}
MUSIC_MOODS = ["upbeat", "chill", "inspiring", "corporate", "playful", "dramatic"]
STOP_FIELDS = [
    num("at", "Momento", None, 0.0, 36000.0, 0.01, unit="s", req=True, nullable=True),
    num("dur", "Duración del silencio", None, 0.05, 10.0, 0.05, unit="s", nullable=True),
    sel("mode", "Tipo", ["tape", "cut"], "tape", seg=True,
        hint="tape = la cancion se frena; cut = corte seco"),
    num("fade", "Frenado / fundido", None, 0.0, 2.0, 0.01, unit="s", nullable=True),
    num("fade_back", "Regreso", None, 0.0, 2.0, 0.01, unit="s", nullable=True),
]
SWELL_FIELDS = [
    num("start", "Inicio", None, 0.0, 36000.0, 0.01, unit="s", req=True, nullable=True),
    num("end", "Fin", None, 0.0, 36000.0, 0.01, unit="s", req=True, nullable=True),
    num("db", "Subida", 3.0, -24.0, 12.0, 0.5, unit="dB"),
    num("ramp", "Rampa", None, 0.0, 3.0, 0.05, unit="s", nullable=True),
]
MUSIC_PARAMS = [
    P("enabled", "bool", "Música activa", True),
    sel("source", "Origen", ["ai", "procedural", "library", "file", "none"], "ai", seg=True),
    P("track", "text", "Pista de biblioteca", None, nullable=True),
    P("file", "text", "Archivo propio (origen: file)", None, nullable=True),
    P("prompt", "text", "Descripción para la IA", ""),
    sel("mood", "Ánimo", MUSIC_MOODS, None, nullable=True),
    num("bpm", "BPM (0 = automático)", 0, 0, 180, 1),
    P("bpm_lock", "bool", "Forzar BPM", False, hint="Sin esto, 110 también cuenta como automático"),
    P("keyscale", "text", "Tonalidad (p. ej. A major)", None, nullable=True),
    num("seed", "Variación", None, 0, 999999, 1, nullable=True, hint="Otra música con el mismo pedido"),
    sel("ending", "Final", ["auto", "natural", "hit", "fade"], "auto", seg=True),
    num("drop_at", "Golpe principal en", None, 0.0, 36000.0, 0.01, unit="s", nullable=True),
    num("gain_db", "Volumen bajo la voz", -17.0, -40.0, 0.0, 0.5, unit="dB"),
    num("duck_db", "Subida en pausas", -9.0, -30.0, 0.0, 0.5, unit="dB",
        hint="Diferencia total entre la cama bajo la voz y los huecos"),
    num("voice_carve_db", "Hueco para la voz (EQ)", -3.0, -12.0, 0.0, 0.5, unit="dB"),
    num("intro_fade", "Entrada suave", 0.0, 0.0, 5.0, 0.05, unit="s",
        hint="0 = entra con su arranque (golpe incluido); > 0 = sube en ese tiempo"),
    num("fade_out", "Cola final", 1.5, 0.6, 3.0, 0.05, unit="s",
        hint="Donde cae el golpe/desvanecido final antes del último cuadro"),
    P("stops", "rows", "Silencios dramáticos", [], fields=STOP_FIELDS),
    P("swells", "rows", "Subidas de volumen", [], fields=SWELL_FIELDS),
]
GRADE_PARAMS = [
    P("enabled", "bool", "Corrección de color", True),
    sel("look", "Look", LOOKS, "warm_clean"),
    P("match_shots", "bool", "Igualar tomas", True),
    num("strength", "Fuerza del look", 1.0, 0.0, 1.5, 0.05),
    num("exposure", "Exposición", 0.0, -1.0, 1.0, 0.05, unit="EV"),
    num("temperature", "Temperatura", 0.0, -1.0, 1.0, 0.05, hint="+ calido / - frio"),
    num("tint", "Tinte", 0.0, -1.0, 1.0, 0.05, hint="+ magenta / - verde"),
    num("saturation", "Saturación", 1.0, 0.0, 2.0, 0.05),
    num("contrast", "Contraste", 0.0, -0.5, 0.5, 0.05),
    num("clarity", "Claridad", None, 0.0, 1.0, 0.05, nullable=True, hint="Vacío = la del look"),
    num("vignette", "Viñeta", 0.15, 0.0, 1.0, 0.01),
    num("grain", "Grano", 0.025, 0.0, 0.3, 0.005),
    num("sharpen", "Nitidez", 0.15, 0.0, 1.0, 0.01),
]
DIRECTOR_PARAMS = [
    sel("style", "Estilo", STYLES, "dinamico", seg=True),
    num("intensity", "Intensidad", 0.75, 0.0, 1.0, 0.05),
    num("seed", "Semilla", 7, 0, 999999, 1),
]

# ---------------------------------------------------------------- recursos del guion (fx.media)
# Imagen o clip que el guion pide al final de una linea ("- Agregar video_x"): el montaje lo
# coloca mientras se dice esa linea y compose lo deja aqui (tiempo ORIGINAL del video compuesto).
# Lo dibuja motion/media.py (render) y app/static/floatcard.js (vista en vivo). `internal`: dato
# del motor que el inspector no muestra como control (archivo, ruta, tipo, nombre en el guion).
# El director lo trata como contenido BLOQUEADO del usuario (nada de titulos/escenas encima).
MEDIA_COMMON = [
    P("sid", "text", "Archivo (fuente)", None, nullable=True, internal=True,
      hint="Id de la fuente subida (POST /sources); el motor resuelve el archivo por aqui"),
    P("path", "text", "Ruta del archivo", None, nullable=True, internal=True),
    P("name", "text", "Recurso del guion", "", internal=True),
    sel("kind", "Tipo de archivo", MEDIA_FILE_KINDS, None, nullable=True, internal=True),
    sel("fit", "Ajuste al tiempo", MEDIA_FITS, "hold", seg=True,
        hint="hold = imagen fija o clip a velocidad normal; speed = clip acelerado para caber; "
             "trim = clip recortado"),
    num("speed", "Velocidad del clip", 1.0, 0.25, 4.0, 0.05, unit="x", hint="Solo con ajuste speed"),
    num("src_in", "Inicio en el clip", 0.0, 0.0, 36000.0, 0.05, unit="s"),
]
# defaults espejo de motion/media.DEFAULTS y LIVE_DEFAULTS (tests/test_media_float.py los compara)
FLOAT_CARD_PARAMS = [
    num("width", "Ancho de la tarjeta", None, 0.4, SYM_W, 0.01, nullable=True,
        hint="Vacío = automático (banda segura, centrada)"),
    num("aspect", "Proporción (ancho/alto)", None, 0.5625, 1.78, 0.01, nullable=True,
        hint="Vacío = 4:5 (o más vertical si el archivo lo es y cabe)"),
    num("y", "Posición Y", None, 0.1, 0.9, 0.005, nullable=True,
        hint="Vacío = automático: arriba de los subtítulos"),
    num("blur", "Desenfoque del fondo", 0.55, 0.0, 1.0, 0.05),
    num("dim", "Oscurecer el fondo", 0.28, 0.0, 0.8, 0.02),
    num("radius", "Esquinas redondeadas", 0.055, 0.0, 0.2, 0.005, hint="Fracción del ancho de la tarjeta"),
    num("shadow", "Sombra", 0.6, 0.0, 1.0, 0.05),
    P("border", "bool", "Borde claro", True),
    num("kenburns", "Acercamiento lento (imágenes)", 0.08, 0.0, 0.3, 0.01),
    num("float", "Flotación", 1.0, 0.0, 2.0, 0.05),
    # brillo de vidrio (franja de luz que cruza) e irisado: espejo de media.LIVE_DEFAULTS
    num("sheen", "Brillo", None, 0.0, 1.0, 0.05, nullable=True,
        hint="Vacío = automático: imágenes sí, clips no"),
    num("holo", "Irisado", 0.6, 0.0, 1.0, 0.05, hint="Color tornasol en los bordes de la franja de luz"),
    num("in_dur", "Duración de la entrada", 0.35, 0.1, 1.5, 0.05, unit="s"),
    num("out_dur", "Duración de la salida", 0.25, 0.1, 1.5, 0.05, unit="s"),
]
MEDIA_STYLE_PARAMS = {"float_card": {"label": "Tarjeta flotante", "params": FLOAT_CARD_PARAMS}}


def _with_defaults(params: list[dict], overrides: dict, hints: dict | None = None) -> list[dict]:
    out = []
    for p in params:
        q = dict(p)
        if p["k"] in overrides:
            q["default"] = overrides[p["k"]]
        if hints and p["k"] in hints:
            q["hint"] = hints[p["k"]]
        out.append(q)
    return out


def _build_kinds() -> dict:
    text_presets = {}
    for name in TEXT_PRESETS:
        d = dict(TEXT_PRESET_DEFAULTS[name])
        label = d.pop("label")
        text_presets[name] = {"label": label,
                              "params": _with_defaults(TEXT_COMMON, d) + TEXT_PRESET_EXTRA.get(name, [])}
    graphics = {k: {"label": v["label"],
                    "params": (PROGRESS_COMMON if k == "progress" else
                               _with_defaults(GRAPHIC_COMMON, GRAPHIC_KIND_DEFAULTS.get(k, {}),
                                              GRAPHIC_KIND_HINTS.get(k)))
                    + v["params"]}
                for k, v in GRAPHIC_KIND_PARAMS.items()}
    cutaways = {k: {"label": v["label"], "params": CUTAWAY_COMMON + v["params"]}
                for k, v in CUTAWAY_KIND_PARAMS.items()}
    effects = {k: {"label": v["label"], "params": v["params"],
                   "needs_matte": k in MATTE_EFFECTS} for k, v in EFFECT_KIND_PARAMS.items()}
    effects["bw"]["needs_matte_if"] = {"subject_color": True}
    out = {
        "version": VERSION,
        "types": list(KINDS_TYPES),
        "common": {"range": COMMON_RANGE, "event": COMMON_EVENT},
        "tracks": {
            "text": {"label": "Texto", "key": "preset", "items": text_presets, "common": "range"},
            "graphics": {"label": "Gráficos", "key": "kind", "items": graphics, "common": "range"},
            "cutaways": {"label": "Escenas", "key": "kind", "items": cutaways, "common": "range"},
            "effects": {"label": "Efectos", "key": "kind", "items": effects, "common": "range"},
            "transitions": {"label": "Transiciones", "key": "kind", "common": "event",
                            "items": {k: {"label": TRANSITION_LABELS[k],
                                          "mixer": k in TRANSITION_MIXERS,
                                          "params": [q for q in TRANSITION_PARAMS[1:]
                                                     if not (q["k"] == "align"
                                                             and k in TRANSITION_MIXERS)]}
                                      for k in TRANSITION_KINDS}},
            "sfx": {"label": "SFX", "key": "name", "common": "event",
                    "items": {k: {"label": SFX_LABELS.get(k, k.replace("_", " ")),
                                  "params": SFX_PARAMS[1:]}
                              for k in SFX_NAMES}},
            "media": {"label": "Recursos", "key": "style", "common": "range",
                      "items": {k: {"label": v["label"], "params": MEDIA_COMMON + v["params"]}
                                for k, v in MEDIA_STYLE_PARAMS.items()}},
        },
        "captions": {"label": "Subtítulos", "params": CAPTIONS_PARAMS},
        "music": {"label": "Música", "params": MUSIC_PARAMS},
        "grade": {"label": "Color", "params": GRADE_PARAMS},
        "director": {"label": "Director", "params": DIRECTOR_PARAMS},
    }
    return json.loads(json.dumps(out))  # sin referencias compartidas: cada kind con sus dicts


_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
# colores con nombre que resuelven los renderers (paleta del estilo y tonos de transicion)
NAMED_COLORS = {"white", "black", "warm", "cool", "gold", "rose", "accent", "accent2", "primary",
                "dark", "light", "brand"}


def _named(v: Any) -> bool:
    return isinstance(v, str) and v.strip() in NAMED_COLORS


def _set_default(prm: dict, v: Any) -> None:
    """Adopta el default recomendado por el modulo dueno sin romper el esquema."""
    if prm["type"] in ("select", "seg"):
        if v not in prm["opts"]:
            prm["opts"] = list(prm["opts"]) + [v]
    elif prm["type"] == "num" and not prm.get("len"):
        f = _num(v)
        if f is None:
            return
        prm["min"], prm["max"] = min(prm["min"], f), max(prm["max"], f)
    prm["default"] = copy.deepcopy(v)


def _convert_param(k: str, spec: dict, label: str) -> dict | None:
    """Parametro en formato de un modulo (float/enum/color/bool/point) -> formato KINDS."""
    t = spec.get("type")
    d = spec.get("default")
    if t in ("float", "int", "num"):
        lo, hi = spec.get("min", 0.0), spec.get("max", 1.0)
        return P(k, "num", label, d, min=lo, max=hi, step=spec.get("step", 0.01),
                 **({"unit": spec["unit"]} if spec.get("unit") else {}),
                 **({"nullable": True} if d is None else {}))
    if t == "enum":
        return P(k, "seg" if len(spec.get("options", [])) <= 4 else "select", label, d,
                 opts=list(spec.get("options", [])))
    if t == "color":
        return P(k, "color", label, d, nullable=True,
                 **({"presets": list(spec["presets"])} if spec.get("presets") else {}))
    if t == "bool":
        return P(k, "bool", label, d, nullable=d is None)
    if t == "point":
        return P(k, "num", label, d, min=0.0, max=1.0, step=0.005, len=2, nullable=True)
    return None


_LABELS = {"dur": "Duración", "color": "Color", "intensity": "Intensidad", "dir": "Dirección",
           "gate": "Solo en la unión", "x": "Centro X", "y": "Centro Y", "avoid": "Zona a evitar",
           "mode": "Modo"}


def _adopt_modules(kinds: dict) -> list[str]:
    """Adopta defaults/opciones que publican los modulos duenos (text.PRESETS,
    graphics.DEFAULTS/ANIM_IN, cutaways.DEFAULTS/SCENE_IN, transitions.PARAMS) para que el
    inspector muestre exactamente lo que el renderer usa. Tolerante por modulo: si uno no
    existe, falla al importar o cambia de formato, se queda el catalogo base para ese modulo."""
    import importlib
    used: list[str] = []

    def mod(name):
        try:
            return importlib.import_module(f"{__package__}.{name}")
        except Exception:
            return None

    def adopt_text(tx):
        if not isinstance(getattr(tx, "PRESETS", None), dict):
            return False
        ain = list(getattr(tx, "ANIM_IN", TEXT_ANIM_IN))
        aout = list(getattr(tx, "ANIM_OUT", TEXT_ANIM_OUT))
        for name, spec in kinds["tracks"]["text"]["items"].items():
            pd = tx.PRESETS.get(name) or {}
            for prm in spec["params"]:
                if prm["k"] == "anim_in":
                    prm["opts"] = list(dict.fromkeys(prm["opts"] + ain))
                if prm["k"] == "anim_out":
                    prm["opts"] = list(dict.fromkeys(prm["opts"] + aout))
                # x/align no se adoptan: el bloque nace centrado en x = XC (simetria)
                if prm["k"] in ("y", "anim_in", "anim_out", "font", "behind",
                                "shadow", "color") and pd.get(prm["k"]) is not None:
                    _set_default(prm, pd[prm["k"]])
        return True

    def adopt_graphics(gr):
        ain = list(dict.fromkeys(GRAPHIC_ANIM_IN + list(getattr(gr, "ANIM_IN", ()))))
        aout = list(dict.fromkeys(GRAPHIC_ANIM_OUT + list(getattr(gr, "ANIM_OUT", ()))))
        defs = getattr(gr, "DEFAULTS", {}) or {}
        for kind, spec in kinds["tracks"]["graphics"]["items"].items():
            for prm in spec["params"]:
                if kind == "progress":
                    pass                        # la barra solo entiende fade|none
                elif prm["k"] == "anim_in":
                    prm["opts"] = list(ain)
                elif prm["k"] == "anim_out":
                    prm["opts"] = list(aout)
                if prm["k"] in GRAPHIC_KIND_DEFAULTS.get(kind, {}):
                    continue                    # colocacion del motor (PiP automatico)
                v = (defs.get(kind) or {}).get(prm["k"])
                if v is not None:
                    _set_default(prm, v)
        return True

    def adopt_cutaways(cu):
        defs = getattr(cu, "DEFAULTS", {}) or {}
        s_in, s_out = list(getattr(cu, "SCENE_IN", ())), list(getattr(cu, "SCENE_OUT", ()))
        roles = getattr(cu, "BG_COLOR_ROLES", None)
        if isinstance(roles, dict):
            roles = {str(k): [str(x) for x in v] for k, v in roles.items()}
            for trk in ("cutaways", "effects"):
                for spec in kinds["tracks"][trk]["items"].values():
                    for prm in spec["params"]:
                        if prm["k"] == "bg_colors":
                            prm["roles"] = roles
        for kind, spec in kinds["tracks"]["cutaways"]["items"].items():
            ks = {q["k"] for q in spec["params"]}
            if s_in and "anim_in" not in ks:
                spec["params"].append(sel("anim_in", "Entrada de la escena", s_in, s_in[0]))
            if s_out and "anim_out" not in ks:
                spec["params"].append(sel("anim_out", "Salida de la escena", s_out, s_out[0]))
            for prm in spec["params"]:
                v = (defs.get(kind) or {}).get(prm["k"])
                if v is not None:
                    _set_default(prm, v)
        return True

    def adopt_transitions(tr):
        if not isinstance(getattr(tr, "PARAMS", None), dict):
            return False
        NAMED_COLORS.update(str(k) for k in (getattr(tr, "_NAMED_COLORS", {}) or {}))
        NAMED_COLORS.update(str(k) for k in (getattr(tr, "LEAK_COLORS", ()) or ()))
        align = next(q for q in TRANSITION_PARAMS if q["k"] == "align")
        for kind, spec in kinds["tracks"]["transitions"]["items"].items():
            mp = tr.PARAMS.get(kind)
            if mp is None:
                continue
            if isinstance(mp, list):          # ya en formato KINDS ({k, type, label, ...})
                params = [json.loads(json.dumps(q)) for q in mp
                          if isinstance(q, dict) and q.get("k") and q.get("type") in KINDS_TYPES]
            else:                             # formato propio del modulo ({clave: spec})
                params = [c for c in (_convert_param(k, sp, _LABELS.get(k, k))
                                      for k, sp in mp.items()) if c is not None]
            mixers = set(getattr(tr, "MIXERS", TRANSITION_MIXERS) or TRANSITION_MIXERS)
            own_align = next((q for q in params if q["k"] == "align"), None)
            params = [q for q in params if q["k"] != "align"]
            if not any(q["k"] == "dur" for q in params):
                params.insert(0, num("dur", "Duración", 0.0, 0.0, 2.0, 0.05, unit="s"))
            spec["mixer"] = kind in mixers
            if kind not in mixers:              # en las mezclas A->B `align` no aplica
                params.append(own_align or dict(align))
            spec["params"] = params
        return True

    for name, fn in (("text", adopt_text), ("graphics", adopt_graphics),
                     ("cutaways", adopt_cutaways), ("transitions", adopt_transitions)):
        m = mod(name)
        if m is None:
            continue
        snapshot = json.dumps(kinds["tracks"][name])
        try:
            if fn(m):
                used.append(name)
        except Exception:  # formato inesperado: se restaura el catalogo base de esa pista
            kinds["tracks"][name] = json.loads(snapshot)
    return used


KINDS = _build_kinds()
_MUSIC_ADOPTED = [False]


def _adopt_music() -> None:
    """Adopta music.CFG_SCHEMA (opciones y rangos que usa el generador de musica).

    Perezoso (importar music cuesta ~0.6 s por scipy): se hace la primera vez que se llama
    normalize/validate/schema. Tolerante: si el modulo no esta o cambia de formato, se queda
    el catalogo base."""
    if _MUSIC_ADOPTED[0]:
        return
    _MUSIC_ADOPTED[0] = True
    try:
        import importlib
        mu = importlib.import_module(f"{__package__}.music")
        cs = getattr(mu, "CFG_SCHEMA", None)
        if not isinstance(cs, dict):
            return
        snap = json.dumps(MUSIC_PARAMS)
        try:
            by_k = {q["k"]: q for q in MUSIC_PARAMS}
            for k, spec in cs.items():
                q = by_k.get(k)
                if q is None or not isinstance(spec, dict):
                    continue
                t = spec.get("type")
                if q["type"] in ("select", "seg") and t == "enum" and isinstance(spec.get("options"), list):
                    q["opts"] = list(dict.fromkeys(list(q["opts"]) + list(spec["options"])))
                elif q["type"] == "num" and t in ("number", "float", "int") and not q.get("len"):
                    lo, hi = _num(spec.get("min")), _num(spec.get("max"))
                    if lo is not None:
                        q["min"] = min(q["min"], lo)
                    if hi is not None:
                        q["max"] = max(q["max"], hi)
        except Exception:  # formato inesperado: catalogo base
            MUSIC_PARAMS[:] = json.loads(snap)
        mp = json.loads(json.dumps(MUSIC_PARAMS))
        _apply_copy_params(mp, "music")
        KINDS["music"]["params"] = _accent_catalog(mp)
        KINDS.setdefault("adopted", []).append("music")
    except Exception:  # pragma: no cover
        return


def _sfx_extra() -> set:
    """Nombres de SFX del catalogo generado (sfx.py) ademas de los del contrato."""
    try:
        from . import assets
        return set(assets.sfx_catalog())
    except Exception:  # pragma: no cover
        return set()


def resolve_sfx_name(name) -> tuple[str | None, int | None]:
    """Nombre de SFX del plan -> (nombre del catalogo, variante 0-based del sufijo `_N`).
    Acepta alias en espanol/ingles, prefijos, tags y sufijo de variante (audio.parse_sfx_name):
    'riser' -> riser_short, 'riser_short_2' -> (riser_short, 1). (None, None) si no existe."""
    if not isinstance(name, str) or not name.strip():
        return None, None
    cat = {k: {} for k in SFX_NAMES}
    try:
        from . import assets
        cat.update(assets.sfx_catalog() or {})
    except Exception:  # pragma: no cover
        pass
    if name in cat:
        return name, None
    try:
        from . import audio
        return audio.parse_sfx_name(name, cat)
    except Exception:  # pragma: no cover - sin audio: solo nombres exactos
        return None, None


def _kind_params(track: str, kind: str) -> list[dict]:
    t = KINDS["tracks"][track]["items"]
    if kind in t:
        return t[kind]["params"]
    return SFX_PARAMS[1:] if track == "sfx" else []


def kind_key(track: str) -> str:
    return KINDS["tracks"][track]["key"]


def item_params(track: str, kind: str) -> list[dict]:
    """Lista de parametros (comunes + del kind) de un item de pista."""
    t = KINDS["tracks"].get(track)
    if not t or kind not in t["items"]:
        return []
    return KINDS["common"][t["common"]] + t["items"][kind]["params"]


# ---------------------------------------------------------------- utilidades de valores
_HEX = re.compile(r"^#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


def norm_color(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    m = _HEX.match(v.strip())
    if not m:
        return None
    s = m.group(1)
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return "#" + s.upper()


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _deep_merge(dst: dict, src: dict) -> dict:
    """Completa en dst las claves faltantes de src (recursivo en dicts). No pisa valores."""
    for k, v in src.items():
        if k not in dst:
            dst[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
    return dst


def _get(d: dict, k: str):
    if "." in k:
        a, b = k.split(".", 1)
        sub = d.get(a)
        return sub.get(b) if isinstance(sub, dict) else None
    return d.get(k)


def _set(d: dict, k: str, v: Any) -> None:
    if "." in k:
        a, b = k.split(".", 1)
        if not isinstance(d.get(a), dict):
            d[a] = {}
        d[a][b] = v
    else:
        d[k] = v


def _has(d: dict, k: str) -> bool:
    if "." in k:
        a, b = k.split(".", 1)
        return isinstance(d.get(a), dict) and b in d[a]
    return k in d


def _fix_param(item: dict, p: dict, fill: bool) -> None:
    """Clampa / normaliza un parametro segun su esquema (in-place)."""
    k, typ = p["k"], p["type"]
    present = _has(item, k)
    if not present:
        if (p.get("req") or fill) and p.get("default") is not None and not p.get("nofill"):
            _set(item, k, copy.deepcopy(p["default"]))
        return
    v = _get(item, k)
    if v is None:
        if not p.get("nullable") and p.get("req") and p.get("default") is not None:
            _set(item, k, copy.deepcopy(p["default"]))
        return
    if typ == "num":
        if p.get("len"):
            if isinstance(v, (list, tuple)) and len(v) == p["len"] and all(_num(x) is not None for x in v):
                _set(item, k, [min(max(float(x), p["min"]), p["max"]) for x in v])
            elif p.get("default") is not None:
                _set(item, k, copy.deepcopy(p["default"]))
            else:
                _set(item, k, None)
            return
        f = _num(v)
        if f is None:
            _set(item, k, copy.deepcopy(p.get("default")))
            return
        f = min(max(f, p["min"]), p["max"])
        if p.get("step") == 1 and float(p["min"]).is_integer():
            f = int(round(f))
        _set(item, k, f)
    elif typ == "bool":
        if not isinstance(v, bool):
            _set(item, k, bool(v) if isinstance(v, (int, float)) else bool(p.get("default")))
    elif typ == "color":
        if p.get("list"):
            vals = v if isinstance(v, list) else [v]
            _set(item, k, [c for c in (norm_color(x) for x in vals) if c])
        else:
            c = norm_color(v)
            if c is None and _named(v):
                c = v.strip()                      # color con nombre (white, warm, accent...)
            _set(item, k, c if c else (None if p.get("nullable") else p.get("default")))
    elif typ in ("select", "seg"):
        if v not in p["opts"]:
            _set(item, k, p.get("default"))
    elif typ == "text":
        if p.get("list"):
            if isinstance(v, str):
                v = [s.strip() for s in re.split(r"[,\n]", v) if s.strip()]
            _set(item, k, [str(s) for s in v] if isinstance(v, list) else [])
        elif not isinstance(v, str):
            _set(item, k, str(v))
    elif typ in ("font", "icon"):
        if not isinstance(v, str):
            _set(item, k, p.get("default"))
    elif typ == "lines":
        _set(item, k, _norm_lines(v))
    elif typ == "rows":
        _set(item, k, _norm_rows(v, p.get("fields") or []))
    elif typ == "media":
        _set(item, k, norm_media(v))


_SID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def norm_media(v: Any) -> dict | None:
    """Medio propio -> {sid, type?, name?, w?, h?, dur?, thumb?, crop?} saneado, o None.

    `sid` es obligatorio (id de POST /sources: letras, numeros, _ y -); nunca se acepta una
    ruta. `crop` = [x0, y0, x1, y1] normalizado (render lo valida). Claves desconocidas se
    descartan."""
    if not isinstance(v, dict):
        return None
    sid = v.get("sid")
    if not isinstance(sid, str) or not _SID.match(sid.strip()):
        return None
    d: dict[str, Any] = {"sid": sid.strip()}
    if v.get("type") in ("image", "video"):
        d["type"] = v["type"]
    if isinstance(v.get("name"), str):
        d["name"] = v["name"][:200]
    for k in ("w", "h"):
        f = _num(v.get(k))
        if f is not None and f >= 0:
            d[k] = int(round(f))
    f = _num(v.get("dur"))
    if f is not None and f >= 0:
        d["dur"] = round(f, 4)
    if isinstance(v.get("thumb"), str):
        d["thumb"] = v["thumb"][:300]
    c = v.get("crop")
    if isinstance(c, (list, tuple)) and len(c) == 4 and all(_num(x) is not None for x in c):
        d["crop"] = [min(1.0, max(0.0, float(x))) for x in c]
    return d


def _norm_media_item(it: dict) -> None:
    """Recurso del guion (fx.media): `sid` valido o None (nunca una ruta), `path`/`name` texto
    acotado, `kind` image|video|None. Un item sin archivo se conserva (el render lo omite con
    aviso 'media:<id>:no_encontrado' y el Studio pide subirlo)."""
    sid = it.get("sid")
    it["sid"] = sid.strip() if isinstance(sid, str) and _SID.match(sid.strip()) else None
    for k, n in (("path", 1024), ("name", 200)):
        v = it.get(k)
        if v is None and k == "path":
            it[k] = None
        else:
            it[k] = str(v)[:n] if v is not None else ""
    if it.get("kind") not in MEDIA_FILE_KINDS:
        it["kind"] = None


def _norm_y_ranges(cap: dict, duration: float | None) -> None:
    """captions.y_ranges: filas {start < end, y en [0.1, 0.9]} en tiempo ORIGINAL, ordenadas.
    Las invalidas se descartan (un dato malo nunca tumba el render)."""
    out = []
    for r in cap.get("y_ranges") if isinstance(cap.get("y_ranges"), list) else []:
        if not isinstance(r, dict):
            continue
        a, b, y = _num(r.get("start")), _num(r.get("end")), _num(r.get("y"))
        if a is None or b is None or y is None:
            continue
        if b < a:
            a, b = b, a
        a = max(0.0, a)
        if duration:
            b = min(b, duration)
        if b - a < MIN_ITEM:
            continue
        out.append(dict(r, start=round(a, 4), end=round(b, 4), y=round(min(0.9, max(0.1, y)), 4)))
    cap["y_ranges"] = sorted(out, key=lambda r: r["start"])


def _norm_rows(v: Any, fields: list[dict]) -> list[dict]:
    """Lista de objetos (`rows`): se descartan las entradas que no son objetos y las opciones
    invalidas (el modulo aplica su default). Los numeros los sanea el normalizador del bloque
    (p. ej. _norm_music descarta un stop sin `at` o con `dur` <= 0) y validate() reporta los
    que salen de rango. Conserva las claves extra."""
    if not isinstance(v, list):
        return []
    out = []
    for row in v:
        if not isinstance(row, dict):
            continue
        d = dict(row)
        for f in fields:
            k = f["k"]
            if k not in d or d[k] is None:
                continue
            if f["type"] == "media":
                d[k] = norm_media(d[k])            # {sid} saneado; una ruta o basura -> None
            elif f["type"] in ("select", "seg") and d[k] not in f["opts"]:
                d.pop(k)
            elif f["type"] == "bool" and not isinstance(d[k], bool):
                d[k] = bool(d[k]) if isinstance(d[k], (int, float)) else f.get("default")
        out.append(d)
    return out


def _norm_lines(v: Any) -> list[dict] | None:
    """`lines` de texto -> [{text, scale, font?, color?, italic}] (conserva claves extra del
    renderer, p. ej. case/tracking/align). Un str suelto es una linea; basura se descarta."""
    if isinstance(v, (str, dict)):
        v = [v]
    if not isinstance(v, list):
        return None
    out = []
    for ln in v:
        if isinstance(ln, str):
            ln = {"text": ln}
        if not isinstance(ln, dict):
            continue
        d = dict(ln)
        d["text"] = "" if d.get("text") is None else str(d.get("text"))
        sc = _num(d.get("scale"))
        d["scale"] = 1.0 if sc is None else min(4.0, max(0.2, sc))
        if d.get("font") is not None and not isinstance(d["font"], str):
            d.pop("font")
        if "color" in d:
            c = norm_color(d["color"]) or (d["color"].strip() if _named(d["color"]) else None)
            if c:
                d["color"] = c
            else:
                d.pop("color")
        d["italic"] = bool(d.get("italic", False))
        out.append(d)
    return out or None


def _norm_music(m: dict, duration: float | None) -> None:
    """stops [{at, dur>0}], swells [{start<end, db}] y drop_at (num|None), en tiempo ORIGINAL.
    Entradas invalidas se descartan (un dato malo nunca tumba el render)."""
    def t(v):
        f = _num(v)
        if f is None:
            return None
        f = max(0.0, f)
        return min(f, duration) if duration else f

    stops = []
    for s in m.get("stops") if isinstance(m.get("stops"), list) else []:
        if not isinstance(s, dict) or t(s.get("at")) is None:
            continue
        d = dict(s, at=round(t(s["at"]), 4))
        dur = _num(s.get("dur"))
        if dur is not None:
            if dur <= 0:
                continue
            d["dur"] = round(min(dur, 10.0), 4)
        if d.get("mode") not in (None, "tape", "cut"):
            d.pop("mode")
        stops.append(d)
    m["stops"] = sorted(stops, key=lambda s: s["at"])
    swells = []
    for s in m.get("swells") if isinstance(m.get("swells"), list) else []:
        if not isinstance(s, dict):
            continue
        a, b = t(s.get("start")), t(s.get("end"))
        if a is None or b is None:
            continue
        if b < a:
            a, b = b, a
        if b - a < MIN_ITEM:
            continue
        db = _num(s.get("db"))
        swells.append(dict(s, start=round(a, 4), end=round(b, 4),
                           db=3.0 if db is None else min(12.0, max(-24.0, db))))
    m["swells"] = sorted(swells, key=lambda s: s["start"])
    if "drop_at" in m:
        m["drop_at"] = None if t(m.get("drop_at")) is None else round(t(m["drop_at"]), 4)


# ---------------------------------------------------------------- migracion
def migrate_v1(plan: dict, custom: dict | None = None) -> dict:
    """Plan v1 (pipeline.analyze) -> v2. Conserva todas las claves v1 y agrega las nuevas.

    `custom` (opciones de subtitulos del render v1, opcional) se traduce a fx.captions.
    No inventa items de fx: eso es trabajo del director.
    """
    p = copy.deepcopy(plan or {})
    if p.get("version") == VERSION and isinstance(p.get("fx"), dict):
        return p
    p["version"] = VERSION
    p.setdefault("shots", [])
    p["entities"] = _deep_merge(dict(p.get("entities") or {}), DEFAULT_ENTITIES)
    p["director"] = _deep_merge(dict(p.get("director") or {}), DEFAULT_DIRECTOR)
    fx = p.get("fx") if isinstance(p.get("fx"), dict) else {}
    p["fx"] = _deep_merge(fx, DEFAULTS["fx"])
    c = custom or {}
    cap = p["fx"]["captions"]
    if c.get("cap_pos") is not None and _num(c["cap_pos"]) is not None:
        cap["y"] = min(0.9, max(0.1, float(c["cap_pos"])))
    if norm_color(c.get("cap_color")):
        cap["accent"] = norm_color(c["cap_color"])
    if norm_color(c.get("base_color")):
        cap["color"] = norm_color(c["base_color"])
    if c.get("cap_case") in ("none", "upper", "lower", "title"):
        cap["case"] = c["cap_case"]
    if c.get("cap_scale") and _num(c["cap_scale"]):
        cap["size"] = round(min(0.12, max(0.02, DEFAULT_CAPTIONS["size"] * float(c["cap_scale"]))), 4)
    return p


# ---------------------------------------------------------------- normalize
def _norm_ids(items: list[dict], prefix: str) -> None:
    used: set[str] = set()
    pat = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
    top = 0
    for it in items:
        m = pat.match(str(it.get("id", "")))
        if m:
            top = max(top, int(m.group(1)))
    for it in items:
        iid = it.get("id")
        if isinstance(iid, str) and iid.strip() and iid not in used:
            used.add(iid)
            continue
        top += 1
        while f"{prefix}_{top}" in used:
            top += 1
        it["id"] = f"{prefix}_{top}"
        used.add(it["id"])


def _norm_times(it: dict, duration: float | None) -> bool:
    s, e = _num(it.get("start")), _num(it.get("end"))
    if s is None or e is None:
        return False
    if e < s:
        s, e = e, s
    s = max(0.0, s)
    if duration:
        if s >= duration:
            return False
        e = min(e, duration)
    if e - s < MIN_ITEM:
        e = s + MIN_ITEM
        if duration and e > duration:
            e = duration
            s = max(0.0, e - MIN_ITEM)
    it["start"], it["end"] = round(s, 4), _round_in(e, duration)
    return True


def _round_in(t: float, duration: float | None) -> float:
    """t a 4 decimales sin pasarse del final del video: una duracion con mas decimales (la de un
    video compuesto, p. ej. 81.59069) redondeada HACIA ARRIBA (81.5907) dejaba el item "fuera del
    video" para validate (aviso falso en el Studio)."""
    r = round(t, 4)
    if duration and r > duration:
        r = int(duration * 1e4) / 1e4
    return r


def _norm_event(it: dict, duration: float | None) -> bool:
    a = _num(it.get("at"))
    if a is None:
        return False
    a = max(0.0, a)
    if duration:
        a = min(a, duration)
    it["at"] = _round_in(a, duration)
    return True


def _norm_common(it: dict, event: bool) -> None:
    it["enabled"] = bool(it.get("enabled", True))
    it["locked"] = bool(it.get("locked", False))
    it["src"] = it.get("src") if it.get("src") in ITEM_SRCS else "ai"
    if not event:
        f = _num(it.get("intensity"))
        it["intensity"] = 1.0 if f is None else min(1.0, max(0.0, f))
        if "z" in it:
            z = _num(it.get("z"))
            if z is None:
                del it["z"]
            else:
                it["z"] = int(round(z))


def normalize(plan: dict, fill: bool = False) -> dict:
    """Plan (v1 o v2) -> plan v2 normalizado (copia). Nunca lanza por datos malos:
    descarta items con kind invalido o sin tiempos y clampa valores fuera de rango."""
    _adopt_music()
    p = migrate_v1(plan) if (plan or {}).get("version") != VERSION else copy.deepcopy(plan)
    _deep_merge(p, DEFAULTS)
    if not isinstance(p.get("shots"), list):
        p["shots"] = []
    dur = _num(p.get("duration"))
    dur = dur if dur and dur > 0 else None
    fx = p["fx"]

    # bloques de configuracion
    for key, params in (("captions", CAPTIONS_PARAMS), ("music", MUSIC_PARAMS),
                        ("grade", GRADE_PARAMS)):
        if not isinstance(fx.get(key), dict):
            fx[key] = copy.deepcopy(DEFAULTS["fx"][key])
        for prm in params:
            _fix_param(fx[key], prm, fill=True)
    d = p["director"]
    for prm in DIRECTOR_PARAMS:
        _fix_param(d, prm, fill=True)
    if not isinstance(d.get("locked"), list):
        d["locked"] = []
    for k in DEFAULT_ENTITIES:
        v = p["entities"].get(k)
        p["entities"][k] = "" if v is None else str(v)
    # perfil de la persona (3.2): saneado; vacio o invalido se quita del plan
    if "profile" in p:
        prof = _profile.normalize_profile(p.get("profile"))
        if prof:
            p["profile"] = prof
        else:
            p.pop("profile", None)
    _norm_music(fx["music"], dur)
    _norm_y_ranges(fx["captions"], dur)

    # pistas de items
    for track in LIST_TRACKS:
        raw = fx.get(track)
        items = []
        key = kind_key(track)
        event = track in EVENT_TRACKS
        for it in raw if isinstance(raw, list) else []:
            if not isinstance(it, dict):
                continue
            it = copy.deepcopy(it)
            if track == "text" and not it.get("preset"):
                it["preset"] = "title_pop"
            if track == "media":
                if not it.get("style"):
                    it["style"] = "float_card"
                _norm_media_item(it)
            kind = it.get(key)
            if track == "sfx" and kind not in KINDS["tracks"][track]["items"] and \
                    kind not in _sfx_extra():
                # alias ('riser', 'campana', 'pop_2'...): se resuelve al nombre del catalogo
                # (con la variante del sufijo) en vez de descartar el SFX sin avisar
                real, var = resolve_sfx_name(kind)
                if real is not None:
                    it[key] = kind = real
                    if var is not None and it.get("variant") is None:
                        it["variant"] = var
            if kind not in KINDS["tracks"][track]["items"]:
                if not (track == "sfx" and kind in _sfx_extra()):
                    continue                              # kind invalido -> se descarta
            if event:
                if not _norm_event(it, dur):
                    continue
            elif not _norm_times(it, dur):
                continue
            _norm_common(it, event)
            # rotulo de un plan viejo (sin `align`): su x es el borde izquierdo; fill no le pone
            # el x centrado del catalogo (el renderer usa su default legacy)
            legacy_lt = track == "graphics" and kind == "lower_third" and "align" not in it
            for prm in _kind_params(track, kind):
                _fix_param(it, prm, fill and not (legacy_lt and prm["k"] == "x"))
            if track == "transitions":
                dv = _num(it.get("dur"))
                if dv is None:
                    dv = _num(next((q.get("default") for q in _kind_params(track, kind)
                                    if q["k"] == "dur"), None))
                it["dur"] = 0.35 if dv is None else min(2.0, max(0.05, dv))
            if track == "cutaways" and "text" in it and not isinstance(it["text"], dict):
                it["text"] = {"preset": "title_pop", "text": str(it["text"])}
            items.append(it)
        _norm_ids(items, ID_PREFIX[track])
        fx[track] = items
    p["version"] = VERSION
    return p


# ---------------------------------------------------------------- validate
def _check_param(errs: list, where: str, item: dict, prm: dict) -> None:
    k = prm["k"]
    if not _has(item, k):
        if prm.get("req"):
            errs.append(f"{where}: falta el campo '{k}'")
        return
    v = _get(item, k)
    if v is None:
        if prm.get("req") and not prm.get("nullable"):
            errs.append(f"{where}: '{k}' no puede ser nulo")
        return
    t = prm["type"]
    if t == "num":
        if prm.get("len"):
            if not (isinstance(v, (list, tuple)) and len(v) == prm["len"]
                    and all(_num(x) is not None for x in v)):
                errs.append(f"{where}: '{k}' debe ser una lista de {prm['len']} numeros")
            return
        f = _num(v)
        if f is None:
            errs.append(f"{where}: '{k}' debe ser numerico")
        elif f < prm["min"] - 1e-9 or f > prm["max"] + 1e-9:
            errs.append(f"{where}: '{k}'={f} fuera de rango [{prm['min']}, {prm['max']}]")
    elif t == "bool" and not isinstance(v, bool):
        errs.append(f"{where}: '{k}' debe ser verdadero/falso")
    elif t == "color":
        vals = v if prm.get("list") and isinstance(v, list) else [v]
        for c in vals:
            if norm_color(c) is None and not (_named(c) and not prm.get("list")):
                errs.append(f"{where}: '{k}' color invalido ({c!r})")
    elif t in ("select", "seg") and v not in prm["opts"]:
        errs.append(f"{where}: '{k}'={v!r} no es una opcion valida")
    elif t == "text" and not prm.get("list") and not isinstance(v, str):
        errs.append(f"{where}: '{k}' debe ser texto")
    elif t == "rows":
        if not isinstance(v, list) or not all(isinstance(r, dict) for r in v):
            errs.append(f"{where}: '{k}' debe ser una lista de objetos")
            return
        for i, r in enumerate(v):
            for f in prm.get("fields") or []:
                fv = r.get(f["k"])
                if fv is None:
                    continue
                if f["type"] == "num" and not f.get("len"):
                    x = _num(fv)
                    if x is None or x < f["min"] - 1e-9 or x > f["max"] + 1e-9:
                        errs.append(f"{where}: '{k}[{i}].{f['k']}' fuera de rango "
                                    f"[{f['min']}, {f['max']}]")
                elif f["type"] in ("select", "seg") and fv not in f["opts"]:
                    errs.append(f"{where}: '{k}[{i}].{f['k']}'={fv!r} no es una opcion valida")
                elif f["type"] == "media" and norm_media(fv) is None:
                    errs.append(f"{where}: '{k}[{i}].{f['k']}' debe ser un medio con 'sid' valido")
    elif t == "media":
        if norm_media(v) is None:
            errs.append(f"{where}: '{k}' debe ser un medio con 'sid' valido")
    elif t == "lines":
        if not isinstance(v, list) or not all(isinstance(ln, (dict, str)) for ln in v):
            errs.append(f"{where}: '{k}' debe ser una lista de lineas")
            return
        for i, ln in enumerate(v):
            if isinstance(ln, str):
                continue
            if not isinstance(ln.get("text", ""), str):
                errs.append(f"{where}: '{k}[{i}].text' debe ser texto")
            sc = ln.get("scale")
            if sc is not None and (_num(sc) is None or not 0.2 <= _num(sc) <= 4.0):
                errs.append(f"{where}: '{k}[{i}].scale' fuera de rango [0.2, 4]")
            c = ln.get("color")
            if c is not None and norm_color(c) is None and not _named(c):
                errs.append(f"{where}: '{k}[{i}].color' color invalido ({c!r})")


def validate(plan: dict) -> list[str]:
    """Lista de problemas (en espanol). Vacia = plan valido. No modifica el plan."""
    errs: list[str] = []
    if not isinstance(plan, dict):
        return ["el plan no es un objeto"]
    _adopt_music()
    if plan.get("version") != VERSION:
        errs.append(f"version debe ser {VERSION} (usa migrate_v1/normalize)")
    fx = plan.get("fx")
    if not isinstance(fx, dict):
        return errs + ["falta 'fx'"]
    dur = _num(plan.get("duration"))
    if not plan.get("video"):
        errs.append("falta 'video' (ruta de la fuente)")
    if plan.get("profile") is not None and not isinstance(plan.get("profile"), dict):
        errs.append("profile: debe ser un objeto (perfil de la persona) o no venir")
    for key, params in (("captions", CAPTIONS_PARAMS), ("music", MUSIC_PARAMS),
                        ("grade", GRADE_PARAMS)):
        blk = fx.get(key)
        if not isinstance(blk, dict):
            errs.append(f"fx.{key}: falta o no es un objeto")
            continue
        for prm in params:
            _check_param(errs, f"fx.{key}", blk, prm)
    mus = fx.get("music") if isinstance(fx.get("music"), dict) else {}
    for i, st in enumerate(mus.get("stops") or []):
        if not isinstance(st, dict) or _num(st.get("at")) is None:
            errs.append(f"fx.music.stops[{i}]: falta 'at' numerico")
        elif st.get("dur") is not None and (_num(st.get("dur")) or 0) <= 0:
            errs.append(f"fx.music.stops[{i}]: 'dur' debe ser mayor que 0")
    for i, sw in enumerate(mus.get("swells") or []):
        if not isinstance(sw, dict) or _num(sw.get("start")) is None or _num(sw.get("end")) is None:
            errs.append(f"fx.music.swells[{i}]: faltan 'start'/'end' numericos")
        elif _num(sw["end"]) <= _num(sw["start"]):
            errs.append(f"fx.music.swells[{i}]: 'end' debe ser mayor que 'start'")
    if mus.get("drop_at") is not None and _num(mus.get("drop_at")) is None:
        errs.append("fx.music: 'drop_at' debe ser numerico o nulo")
    for track in LIST_TRACKS:
        items = fx.get(track, [])
        if not isinstance(items, list):
            errs.append(f"fx.{track}: debe ser una lista")
            continue
        ids = set()
        key = kind_key(track)
        event = track in EVENT_TRACKS
        for i, it in enumerate(items):
            where = f"fx.{track}[{i}]"
            if not isinstance(it, dict):
                errs.append(f"{where}: no es un objeto")
                continue
            where = f"fx.{track}[{it.get('id', i)}]"
            iid = it.get("id")
            if not isinstance(iid, str) or not iid:
                errs.append(f"{where}: falta 'id'")
            elif iid in ids:
                errs.append(f"{where}: id duplicado")
            ids.add(iid)
            kind = it.get(key)
            if kind not in KINDS["tracks"][track]["items"] and not (
                    track == "sfx" and kind in _sfx_extra()):
                errs.append(f"{where}: {key} desconocido ({kind!r})")
                continue
            if event:
                a = _num(it.get("at"))
                if a is None:
                    errs.append(f"{where}: falta 'at'")
                elif a < 0 or (dur and a > dur + 1e-6):
                    errs.append(f"{where}: 'at' fuera del video")
            else:
                s, e = _num(it.get("start")), _num(it.get("end"))
                if s is None or e is None:
                    errs.append(f"{where}: faltan 'start'/'end'")
                else:
                    if e <= s:
                        errs.append(f"{where}: 'end' debe ser mayor que 'start'")
                    if s < 0 or (dur and e > dur + 1e-6):
                        errs.append(f"{where}: rango fuera del video")
                inten = _num(it.get("intensity", 1.0))
                if inten is None or not 0 <= inten <= 1:
                    errs.append(f"{where}: 'intensity' debe estar en [0,1]")
            for prm in _kind_params(track, kind):
                _check_param(errs, where, it, prm)
            if track == "text" and not (str(it.get("text") or "").strip() or it.get("lines")) \
                    and kind != "counter":
                errs.append(f"{where}: texto vacio")
    return errs


def schema() -> dict:
    """Catalogo para `/fx/schema` (copia profunda, JSON-serializable)."""
    _adopt_music()
    return copy.deepcopy(KINDS)


# adopcion de defaults de los modulos duenos (al final: usa helpers definidos arriba)
try:
    KINDS["adopted"] = _adopt_modules(KINDS)
except Exception:  # pragma: no cover - nunca romper el catalogo base
    KINDS["adopted"] = []
# copy de la interfaz (ui_copy.json) y ortografia final (tambien de lo que adoptaron los modulos)
# y marca de mezcla en TODAS las transiciones (el Studio no duplica la lista MIXERS)
_apply_ui_copy(KINDS)
_accent_catalog(KINDS)
for _k, _spec in KINDS["tracks"]["transitions"]["items"].items():
    _spec["mixer"] = bool(_spec.get("mixer", _k in TRANSITION_MIXERS))
