"""Plan v2: defaults, migracion v1 -> v2, normalizacion, validacion y catalogo de kinds.

Contrato: docs/MOTION-ENGINE.md, seccion 3. `KINDS` es JSON-serializable y lo consume el
inspector del Studio (`/fx/schema`): por cada pista y kind/preset lista sus parametros con
`k` (clave), `type` (num|text|color|select|bool|seg|font|icon), `label` (espanol) y, segun
el tipo, `min`/`max`/`step`, `opts`, `default`. Extensiones:
  - `list: true`   el valor es una lista de ese tipo (p. ej. bg_colors, items, accent_words).
  - `len: n`       vector numerico de n componentes (path_to, pan).
  - `nullable`     admite null (p. ej. `box` sin caja).
  - `req: true`    campo obligatorio: normalize() lo completa con `default` si falta.
  - `unit`         unidad para la UI ("s", "px", "dB", "x").
  - claves con punto ("text.text") editan un objeto embebido (cutaways.text).

normalize() solo completa campos comunes y los `req`; los parametros opcionales ausentes se
dejan asi para que cada renderer aplique su propio default de preset (el `default` del
catalogo es el que muestra la UI). Con normalize(plan, fill=True) se completan todos.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

VERSION = 2

# ---------------------------------------------------------------- defaults (seccion 3)
DEFAULT_ENTITIES = {"name": "", "role": "", "price": "", "address": "", "landmark": "",
                    "cta": "", "link_label": "", "handle": ""}
DEFAULT_DIRECTOR = {"style": "dinamico", "intensity": 0.75, "seed": 7, "locked": [],
                    "llm": "qwen3.5:4b"}
DEFAULT_CAPTIONS = {"enabled": True, "preset": "clean_bold", "y": 0.72, "size": 0.052,
                    "font": "Poppins-ExtraBold", "color": "#FFFFFF", "accent": "#FFD23F",
                    "case": "none", "max_words": 4, "hide_during_titles": True}
DEFAULT_MUSIC = {"enabled": True, "source": "ai", "track": None, "file": None, "prompt": "",
                 "bpm": 110, "gain_db": -17, "duck_db": -9, "fade_in": 0.2, "fade_out": 1.5,
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

LIST_TRACKS = ("text", "graphics", "cutaways", "effects", "transitions", "sfx")
ID_PREFIX = {"text": "tx", "graphics": "gr", "cutaways": "cw", "effects": "fx",
             "transitions": "tr", "sfx": "sf"}
EVENT_TRACKS = ("transitions", "sfx")          # usan `at` en vez de start/end
MIN_ITEM = 0.05                                # s: duracion minima de un item

# ---------------------------------------------------------------- enumeraciones normativas
TEXT_PRESETS = ["title_pop", "stack", "cascade", "serif_elegant", "script_accent", "behind_giant",
                "highlight_marker", "typewriter", "counter", "kicker"]
TEXT_ANIM_IN = ["pop", "rise", "slide_left", "slide_right", "type", "mask", "fade", "scale_down",
                "word_stagger", "letter_stagger"]
TEXT_ANIM_OUT = ["fade", "pop_out", "slide_up", "none"]
CAPTION_PRESETS = ["clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps", "none"]
GRAPHIC_KINDS = ["doodle", "icon", "card", "lower_third", "cta", "progress", "sticker_text"]
DOODLES = ["sparkle", "star4", "star5", "burst", "rays", "arrow_curve", "arrow_straight",
           "arrow_down", "circle_scribble", "underline", "squiggle", "dotted_path", "heart",
           "check", "cross", "exclaim", "question", "swirl", "lightning", "loop"]
CARD_VARIANTS = ["price", "location", "schedule", "link", "phone", "rating", "checklist", "stat",
                 "generic"]
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
KINDS_TYPES = ("num", "text", "color", "select", "bool", "seg", "font", "icon", "lines")


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
    P("text", "text", "Texto (Enter = nueva linea)", "", multiline=True),
    num("x", "Posicion X", 0.5, 0.0, 1.0, 0.005),
    num("y", "Posicion Y", 0.3, 0.0, 1.0, 0.005),
    num("size", "Tamano", 0.09, 0.02, 0.45, 0.005),
    P("font", "font", "Fuente", "Poppins-Black", opts=FONTS),
    P("color", "color", "Color", "#FFFFFF"),
    P("accent", "color", "Color de acento", "#FFD23F"),
    P("accent_words", "text", "Palabras en acento", [], list=True),
    sel("align", "Alineacion", ["left", "center", "right"], "center", seg=True),
    num("stroke", "Contorno", 0.0, 0.0, 24.0, 0.5, unit="px"),
    num("shadow", "Sombra", 0.35, 0.0, 1.0, 0.05),
    sel("anim_in", "Animacion de entrada", TEXT_ANIM_IN, "pop"),
    sel("anim_out", "Animacion de salida", TEXT_ANIM_OUT, "fade"),
    P("behind", "bool", "Detras de la persona", False),
    P("box", "color", "Caja de fondo", None, nullable=True),
    # lineas con estilo propio (contrato 3.1): reemplazan a `text` si vienen
    P("lines", "lines", "Lineas con estilo propio", None, nullable=True, list=True,
      fields=[P("text", "text", "Texto", ""), num("scale", "Escala", 1.0, 0.2, 4.0, 0.05),
              P("font", "font", "Fuente", None, opts=FONTS, nullable=True),
              P("color", "color", "Color", None, nullable=True),
              P("italic", "bool", "Cursiva", False)]),
]
LINE_FIELDS = {"text", "scale", "font", "color", "italic"}
TEXT_PRESET_DEFAULTS = {
    "title_pop":        {"label": "Titulo pop", "font": "Poppins-Black", "size": 0.09, "y": 0.26,
                         "anim_in": "pop"},
    "stack":            {"label": "Apilado bold", "font": "Anton", "size": 0.11, "y": 0.28,
                         "anim_in": "word_stagger"},
    "cascade":          {"label": "Cascada", "font": "Poppins-ExtraBold", "size": 0.07, "y": 0.22,
                         "align": "left", "x": 0.1, "anim_in": "word_stagger"},
    "serif_elegant":    {"label": "Serif elegante", "font": "DMSerifDisplay", "size": 0.075,
                         "y": 0.24, "anim_in": "rise"},
    "script_accent":    {"label": "Manuscrita", "font": "Caveat-Bold", "size": 0.08, "y": 0.3,
                         "anim_in": "mask"},
    "behind_giant":     {"label": "Gigante detras", "font": "Anton", "size": 0.3, "y": 0.3,
                         "behind": True, "anim_in": "scale_down"},
    "highlight_marker": {"label": "Marcador", "font": "Poppins-ExtraBold", "size": 0.065,
                         "y": 0.3, "box": "#FFD23F", "color": "#111111", "anim_in": "mask"},
    "typewriter":       {"label": "Maquina de escribir", "font": "SpaceMono-Bold", "size": 0.055,
                         "y": 0.3, "anim_in": "type"},
    "counter":          {"label": "Contador", "font": "Poppins-Black", "size": 0.14, "y": 0.3,
                         "anim_in": "pop"},
    "kicker":           {"label": "Etiqueta (kicker)", "font": "Poppins-SemiBold", "size": 0.04,
                         "y": 0.2, "box": "#111111", "anim_in": "rise"},
}
TEXT_PRESET_EXTRA = {
    "counter": [num("from", "Desde", 0, -1e9, 1e9, 1), num("to", "Hasta", 1000, -1e9, 1e9, 1),
                P("prefix", "text", "Prefijo", "$"), P("suffix", "text", "Sufijo", ""),
                num("decimals", "Decimales", 0, 0, 3, 1)],
}

# ---------------------------------------------------------------- captions
CAPTIONS_PARAMS = [
    P("enabled", "bool", "Subtitulos", True),
    sel("preset", "Estilo", CAPTION_PRESETS, "clean_bold"),
    num("y", "Posicion Y", 0.72, 0.1, 0.9, 0.005),
    num("size", "Tamano", 0.052, 0.02, 0.12, 0.002),
    P("font", "font", "Fuente", "Poppins-ExtraBold", opts=FONTS),
    P("color", "color", "Color", "#FFFFFF"),
    P("accent", "color", "Color palabra activa", "#FFD23F"),
    sel("case", "Mayusculas", ["none", "upper", "lower", "title"], "none", seg=True),
    num("max_words", "Palabras por linea", 4, 1, 8, 1),
    P("hide_during_titles", "bool", "Ocultar durante titulos", True),
]

# ---------------------------------------------------------------- graphics
GRAPHIC_COMMON = [
    num("x", "Posicion X", 0.5, 0.0, 1.0, 0.005),
    num("y", "Posicion Y", 0.3, 0.0, 1.0, 0.005),
    num("size", "Tamano", 0.18, 0.02, 1.0, 0.005),
    P("color", "color", "Color", "#FFFFFF"),
    sel("anim_in", "Animacion de entrada", GRAPHIC_ANIM_IN, "pop"),
    sel("anim_out", "Animacion de salida", GRAPHIC_ANIM_OUT, "fade"),
    num("rotation", "Rotacion", 0.0, -180.0, 180.0, 1.0, unit="grados"),
]
GRAPHIC_KIND_PARAMS = {
    "doodle": {"label": "Garabato", "params": [
        sel("name", "Figura", DOODLES, "sparkle", req=True),
        num("stroke", "Grosor", 8.0, 1.0, 40.0, 0.5, unit="px"),
        P("draw", "bool", "Se dibuja solo", True),
        num("wiggle", "Temblor a mano", 0.3, 0.0, 1.0, 0.05),
        num("path_to", "Destino (flechas)", None, 0.0, 1.0, 0.005, len=2, nullable=True)]},
    "icon": {"label": "Icono", "params": [
        P("name", "icon", "Icono", "sparkles", req=True),
        sel("variant", "Variante", ["outline", "filled"], "outline", seg=True),
        sel("badge", "Insignia", ["none", "circle", "squircle"], "none", seg=True),
        P("badge_color", "color", "Color de insignia", "#FFD23F"),
        P("draw", "bool", "Se dibuja solo", True)]},
    "card": {"label": "Tarjeta", "params": [
        sel("variant", "Tipo", CARD_VARIANTS, "generic", req=True),
        P("title", "text", "Titulo", ""), P("subtitle", "text", "Subtitulo", ""),
        P("value", "text", "Valor", ""), P("items", "text", "Elementos", [], list=True),
        P("icon", "icon", "Icono", ""),
        sel("theme", "Tema", ["light", "dark", "brand"], "light", seg=True)]},
    "lower_third": {"label": "Rotulo (lower third)", "params": [
        P("title", "text", "Titulo", "", req=True), P("subtitle", "text", "Subtitulo", "")]},
    "cta": {"label": "Llamada a la accion", "params": [
        sel("variant", "Tipo", CTA_VARIANTS, "follow", req=True),
        P("label", "text", "Texto", ""), P("handle", "text", "Usuario", "")]},
    "progress": {"label": "Barra de progreso", "params": [
        num("thickness", "Grosor", 8.0, 2.0, 30.0, 0.5, unit="px")]},
    "sticker_text": {"label": "Etiqueta", "params": [
        P("text", "text", "Texto", "", req=True), P("bg", "color", "Fondo", "#FFD23F"),
        P("fg", "color", "Texto", "#111111")]},
}

# ---------------------------------------------------------------- cutaways
CUTAWAY_COMMON = [
    sel("background", "Fondo", BACKGROUNDS, "gradient"),
    P("bg_colors", "color", "Colores del fondo", [], list=True),
    P("text.text", "text", "Texto", "", multiline=True),
    sel("text.preset", "Estilo del texto", TEXT_PRESETS, "title_pop"),
]
CUTAWAY_KIND_PARAMS = {
    "title_card": {"label": "Tarjeta de titulo", "params": []},
    "info_card": {"label": "Tarjeta de informacion", "params": []},
    "photo": {"label": "Foto / cuadro congelado", "params": []},
    "self_broll": {"label": "Toma propia (B-roll)", "params": [
        num("src_t", "Tiempo del material", 0.0, 0.0, 36000.0, 0.01, unit="s", req=True),
        num("zoom_from", "Zoom inicial", 1.0, 1.0, 2.5, 0.01, unit="x"),
        num("zoom_to", "Zoom final", 1.15, 1.0, 2.5, 0.01, unit="x")]},
}

# ---------------------------------------------------------------- effects
EFFECT_KIND_PARAMS = {
    "zoom": {"label": "Zoom suave", "params": [
        num("from", "Desde", 1.0, 1.0, 2.5, 0.01, unit="x"),
        num("to", "Hasta", 1.15, 1.0, 2.5, 0.01, unit="x"),
        sel("easing", "Curva", EASINGS, "in_out_cubic")]},
    "punch": {"label": "Punch-in", "params": [
        num("amount", "Cantidad", 0.12, 0.0, 0.6, 0.01),
        num("hold", "Duracion del golpe", 0.6, 0.0, 3.0, 0.05, unit="s")]},
    "shake": {"label": "Sacudida", "params": [
        num("amp", "Amplitud", 10.0, 0.0, 60.0, 0.5, unit="px"),
        num("freq", "Frecuencia", 12.0, 1.0, 30.0, 0.5, unit="Hz"),
        num("decay", "Amortiguacion", 0.6, 0.0, 1.0, 0.05)]},
    "kenburns": {"label": "Ken Burns", "params": [
        num("from", "Desde", 1.0, 1.0, 2.5, 0.01, unit="x"),
        num("to", "Hasta", 1.12, 1.0, 2.5, 0.01, unit="x"),
        num("pan", "Desplazamiento", [0.02, -0.01], -0.5, 0.5, 0.005, len=2)]},
    "bw": {"label": "Blanco y negro", "params": [
        num("contrast", "Contraste", 1.2, 0.5, 2.0, 0.05),
        P("subject_color", "bool", "Persona a color", False)]},
    "color_pop": {"label": "Color pop (fondo gris)", "params": [
        num("bg_desat", "Desaturar fondo", 1.0, 0.0, 1.0, 0.05),
        num("bg_dark", "Oscurecer fondo", 0.25, 0.0, 1.0, 0.05)]},
    "bg_blur": {"label": "Desenfoque de fondo", "params": [
        num("radius", "Radio", 24.0, 0.0, 80.0, 1.0, unit="px")]},
    "bg_replace": {"label": "Reemplazo de fondo", "params": [
        sel("background", "Fondo", BACKGROUNDS, "gradient"),
        P("bg_colors", "color", "Colores del fondo", [], list=True)]},
    "subject_outline": {"label": "Contorno de la persona", "params": [
        P("color", "color", "Color", "#FFFFFF"),
        num("width", "Grosor", 8.0, 1.0, 40.0, 0.5, unit="px"),
        num("glow", "Brillo", 0.5, 0.0, 1.0, 0.05)]},
    "rgb_split": {"label": "Separacion RGB", "params": [
        num("amount", "Cantidad", 8.0, 0.0, 40.0, 0.5, unit="px")]},
    "flash_frame": {"label": "Destello", "params": [
        P("color", "color", "Color", "#FFFFFF")]},
}
MATTE_EFFECTS = ("color_pop", "bg_blur", "bg_replace", "subject_outline")

# ---------------------------------------------------------------- transitions / sfx
TRANSITION_PARAMS = [
    sel("kind", "Tipo", TRANSITION_KINDS, "flash", req=True),
    num("dur", "Duracion", 0.35, 0.05, 2.0, 0.01, unit="s", req=True),
    sel("dir", "Direccion", ["left", "right", "up", "down"], "left", seg=True),
    P("color", "color", "Color", "#FFFFFF"),
    sel("align", "Alineacion con la union", ["auto", "center", "start", "end"], "auto", seg=True),
]
TRANSITION_LABELS = {"cut": "Corte", "flash": "Destello", "light_leak": "Fuga de luz",
                     "whip": "Barrido (whip)", "zoom_blur": "Zoom con desenfoque",
                     "glitch": "Glitch", "film_burn": "Quemado de pelicula",
                     "torn_paper": "Papel rasgado", "circle": "Circulo", "slide": "Empuje"}
SFX_PARAMS = [
    sel("name", "Sonido", SFX_NAMES, "whoosh_soft", req=True),
    num("gain_db", "Volumen", -6.0, -40.0, 6.0, 0.5, unit="dB"),
    num("pan", "Paneo", 0.0, -1.0, 1.0, 0.05),
]
MUSIC_MOODS = ["upbeat", "chill", "inspiring", "corporate", "playful", "dramatic"]
MUSIC_PARAMS = [
    P("enabled", "bool", "Musica", True),
    sel("source", "Origen", ["ai", "procedural", "library", "none"], "ai", seg=True),
    P("track", "text", "Pista de biblioteca", None, nullable=True),
    P("prompt", "text", "Descripcion para la IA", ""),
    sel("mood", "Animo", MUSIC_MOODS, None, nullable=True),
    num("bpm", "BPM (0 o 110 = automatico)", 110, 0, 180, 1),
    P("keyscale", "text", "Tonalidad (p. ej. A major)", None, nullable=True),
    num("seed", "Variacion", None, 0, 999999, 1, nullable=True),
    sel("ending", "Final", ["auto", "natural", "hit", "fade"], "auto", seg=True),
    num("drop_at", "Golpe principal en", None, 0.0, 36000.0, 0.01, unit="s", nullable=True),
    num("gain_db", "Volumen", -17.0, -40.0, 0.0, 0.5, unit="dB"),
    num("duck_db", "Bajar bajo la voz", -9.0, -30.0, 0.0, 0.5, unit="dB"),
    num("fade_in", "Entrada", 0.2, 0.0, 5.0, 0.05, unit="s"),
    num("fade_out", "Salida", 1.5, 0.0, 8.0, 0.05, unit="s"),
]
GRADE_PARAMS = [
    P("enabled", "bool", "Color", True),
    sel("look", "Look", LOOKS, "warm_clean"),
    P("match_shots", "bool", "Igualar tomas", True),
    num("vignette", "Vinieta", 0.15, 0.0, 1.0, 0.01),
    num("grain", "Grano", 0.025, 0.0, 0.2, 0.005),
    num("sharpen", "Nitidez", 0.15, 0.0, 1.0, 0.01),
]
DIRECTOR_PARAMS = [
    sel("style", "Estilo", STYLES, "dinamico", seg=True),
    num("intensity", "Intensidad", 0.75, 0.0, 1.0, 0.05),
    num("seed", "Semilla", 7, 0, 999999, 1),
]


def _with_defaults(params: list[dict], overrides: dict) -> list[dict]:
    out = []
    for p in params:
        q = dict(p)
        if p["k"] in overrides:
            q["default"] = overrides[p["k"]]
        out.append(q)
    return out


def _build_kinds() -> dict:
    text_presets = {}
    for name in TEXT_PRESETS:
        d = dict(TEXT_PRESET_DEFAULTS[name])
        label = d.pop("label")
        text_presets[name] = {"label": label,
                              "params": _with_defaults(TEXT_COMMON, d) + TEXT_PRESET_EXTRA.get(name, [])}
    graphics = {k: {"label": v["label"], "params": GRAPHIC_COMMON + v["params"]}
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
            "graphics": {"label": "Graficos", "key": "kind", "items": graphics, "common": "range"},
            "cutaways": {"label": "Escenas", "key": "kind", "items": cutaways, "common": "range"},
            "effects": {"label": "Efectos", "key": "kind", "items": effects, "common": "range"},
            "transitions": {"label": "Transiciones", "key": "kind", "common": "event",
                            "items": {k: {"label": TRANSITION_LABELS[k],
                                          "params": TRANSITION_PARAMS[1:]}
                                      for k in TRANSITION_KINDS}},
            "sfx": {"label": "SFX", "key": "name", "common": "event",
                    "items": {k: {"label": k.replace("_", " "), "params": SFX_PARAMS[1:]}
                              for k in SFX_NAMES}},
        },
        "captions": {"label": "Subtitulos", "params": CAPTIONS_PARAMS},
        "music": {"label": "Musica", "params": MUSIC_PARAMS},
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


_LABELS = {"dur": "Duracion", "color": "Color", "intensity": "Intensidad", "dir": "Direccion",
           "gate": "Solo en la union", "x": "Centro X", "y": "Centro Y", "avoid": "Zona a evitar",
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
                if prm["k"] in ("x", "y", "align", "anim_in", "anim_out", "font", "behind",
                                "shadow", "color") and pd.get(prm["k"]) is not None:
                    _set_default(prm, pd[prm["k"]])
        return True

    def adopt_graphics(gr):
        ain = list(dict.fromkeys(GRAPHIC_ANIM_IN + list(getattr(gr, "ANIM_IN", ()))))
        aout = list(dict.fromkeys(GRAPHIC_ANIM_OUT + list(getattr(gr, "ANIM_OUT", ()))))
        defs = getattr(gr, "DEFAULTS", {}) or {}
        for kind, spec in kinds["tracks"]["graphics"]["items"].items():
            for prm in spec["params"]:
                if prm["k"] == "anim_in":
                    prm["opts"] = list(ain)
                elif prm["k"] == "anim_out":
                    prm["opts"] = list(aout)
                v = (defs.get(kind) or {}).get(prm["k"])
                if v is not None:
                    _set_default(prm, v)
        return True

    def adopt_cutaways(cu):
        defs = getattr(cu, "DEFAULTS", {}) or {}
        s_in, s_out = list(getattr(cu, "SCENE_IN", ())), list(getattr(cu, "SCENE_OUT", ()))
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
            params = [q for q in params if q["k"] != "align"]
            if not any(q["k"] == "dur" for q in params):
                params.insert(0, num("dur", "Duracion", 0.0, 0.0, 2.0, 0.05, unit="s"))
            spec["params"] = params + [dict(align)]
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
        KINDS["music"]["params"] = json.loads(json.dumps(MUSIC_PARAMS))
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
        if (p.get("req") or fill) and p.get("default") is not None:
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
    it["start"], it["end"] = round(s, 4), round(e, 4)
    return True


def _norm_event(it: dict, duration: float | None) -> bool:
    a = _num(it.get("at"))
    if a is None:
        return False
    a = max(0.0, a)
    if duration:
        a = min(a, duration)
    it["at"] = round(a, 4)
    return True


def _norm_common(it: dict, event: bool) -> None:
    it["enabled"] = bool(it.get("enabled", True))
    it["locked"] = bool(it.get("locked", False))
    it["src"] = it.get("src") if it.get("src") in ("ai", "user") else "ai"
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
    _norm_music(fx["music"], dur)

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
            kind = it.get(key)
            if kind not in KINDS["tracks"][track]["items"]:
                if not (track == "sfx" and kind in _sfx_extra()):
                    continue                              # kind invalido -> se descarta
            if event:
                if not _norm_event(it, dur):
                    continue
            elif not _norm_times(it, dur):
                continue
            _norm_common(it, event)
            for prm in _kind_params(track, kind):
                _fix_param(it, prm, fill)
            if track == "transitions":
                dv = _num(it.get("dur"))
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
