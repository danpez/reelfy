"""IA director de Reelfy (docs/MOTION-ENGINE.md, seccion 5).

Decide TODA la edicion de un video corto a partir del plan v1 (frases con tiempos por palabra,
cuts, duracion, video) y emite el plan v2: `shots`, `entities`, `director` y `fx` con SOLO
kinds y campos del catalogo (seccion 3.1). 100% local.

Pasos:
  1. tomas     : deteccion de cortes (una pasada de ffmpeg: diferencia adaptativa de cuadros a
                 30 fps sobre una version diminuta), rostro por toma (YuNet) y descripcion con el
                 VLM local (qwen3.5:4b via ollama: persona, encuadre, lugar, textos visibles).
  2. guion     : heuristicas deterministas (secciones, enfasis, entidades por regex/numeros en
                 espanol) refinadas por el LLM local (formato JSON con esquema). Todo lo que
                 dice el LLM se valida contra la transcripcion antes de usarse.
  3. coreografo: reglas deterministas que convierten guion + tomas en items con tiempos
                 alineados a PALABRAS: hook, momento dramatico, titulos cineticos por bloques,
                 tarjetas de datos, escenas, transicion en cada cambio de toma, SFX por evento,
                 musica (stops/swells), color, subtitulos, CTA final; zonas sin encimar,
                 SAFE y cara respetadas; relleno de ritmo (evento cada 2-4 s segun intensidad).
  4. validacion: schema.normalize/validate (si existe) o validacion minima propia.

API:
  direct(plan, style="dinamico", intensity=0.75, seed=7, use_llm=True, on_step=None, trim=True)
      -> plan v2 (copia). plan.director lleva script, kit, notes, timings, rev.
  redirect(plan, style=None, intensity=None, seed=None, only_ids=None, use_llm=False) -> plan v2
  detect_shots(video, duration=None) -> list[dict]
  analyze_shots(plan, use_llm=True, on_step=None, scan=None, deadline=None, on_basic=None)
      -> list[dict] (+ face, motion, rows = perfil de color del fondo, speech)
  analyze_script(plan, shots, use_llm=True) -> dict
  propose_trims(plan, shots, words, keep) -> list[cut] (src "ai")
  choreograph(plan, shots, script, style, intensity, seed, fixed=None, windows=None) -> (fx, _Choreo)
  zone_of(track, item) -> str | None ; edit_table(plan) -> str ; stats(plan) ; STYLES ; TYPE_KIT
  contrast(a, b) / rel_lum(c) (WCAG) ; box_text_color(item) ; tone_icon(tag, role)

Convenciones de coordenadas que emite el director (para los renderers):
  - text: (x, y) = centro del bloque de texto (x = 0.5; cascade: borde izquierdo); `size` =
    tamano de fuente / W; `lines[].scale` multiplica `size`. Los bloques se ajustan (ancho
    medido con las fuentes reales, <= 0.76 W) para caber en SAFE y no tapar la cara (la camara
    hace zoom anclado en el rostro). Un bloque = una clausula hablada (2-4 lineas que se
    revelan al ritmo de la voz), nunca menos de ~0.9 s en pantalla.
  - graphics card/cta: (x, y) = centro; `size` = ancho / W. sticker_text/lower_third: `size` =
    tamano de fuente / W (lower_third: x = borde izquierdo).
  - doodle/icon: (x, y) = centro; `size` = lado de la caja / W.
  - cutaways info_card: la tarjeta va en `card` (item de graphics.card), no en graphics[].
Tipografia: un "kit" por estilo y video (display + sans en 2-3 pesos + un acento serif o
manuscrito): como maximo 4 fuentes y 3 familias. El color de tinta se elige contra el fondo
real de la franja (luminancia/tono medidos por toma) y todo texto sobre caja cumple WCAG 4.5.
Tiempos SIEMPRE originales (el render remapea). Aleatoriedad solo via FrameCtx.rand(key).
Opcional (`trim=True`): el director propone recortes `cuts` con src "ai" para b-roll mudo
demasiado largo (el Studio puede desactivarlos con `enabled: false`).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import assets
from .ctx import SAFE, FrameCtx

try:  # dentro del motor (scripts/ en sys.path)
    import paths as _paths
except Exception:  # pragma: no cover
    _paths = None

PROMPT_VERSION = "d5"
TRACKS = ("text", "graphics", "cutaways", "effects", "transitions", "sfx")
ID_PREFIX = {"text": "tx", "graphics": "gr", "cutaways": "cw", "effects": "fx",
             "transitions": "tr", "sfx": "sf"}

# ---------------------------------------------------------------- catalogo (seccion 3.1)
TEXT_PRESETS = ("title_pop", "stack", "cascade", "serif_elegant", "script_accent", "behind_giant",
                "highlight_marker", "typewriter", "counter", "kicker")
CAPTION_PRESETS = ("clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps", "none")
TEXT_ANIM_IN = ("pop", "rise", "slide_left", "slide_right", "type", "mask", "fade", "scale_down",
                "word_stagger", "letter_stagger")
ANIM_OUT = ("fade", "pop_out", "slide_up", "none")
GRAPHIC_ANIM_IN = ("pop", "draw", "fade", "rise", "scale_down", "slide_left", "slide_right", "none")
GRAPHIC_KINDS = ("doodle", "icon", "card", "lower_third", "cta", "progress", "sticker_text")
DOODLES = ("sparkle", "star4", "star5", "burst", "rays", "arrow_curve", "arrow_straight",
           "arrow_down", "circle_scribble", "underline", "squiggle", "dotted_path", "heart",
           "check", "cross", "exclaim", "question", "swirl", "lightning", "loop")
CARD_VARIANTS = ("price", "location", "schedule", "link", "phone", "rating", "checklist", "stat",
                 "generic")
CTA_VARIANTS = ("follow", "link_below", "comment", "save", "share")
CUTAWAY_KINDS = ("title_card", "info_card", "photo", "self_broll")
BACKGROUNDS = ("grid_warp", "gradient", "paper", "dark_stars", "rays", "blur_self", "color",
               "halftone")
EFFECT_KINDS = ("zoom", "punch", "shake", "kenburns", "bw", "color_pop", "bg_blur", "bg_replace",
                "subject_outline", "rgb_split", "flash_frame")
TRANSITION_KINDS = ("cut", "flash", "light_leak", "whip", "zoom_blur", "glitch", "film_burn",
                    "torn_paper", "circle", "slide")
SFX_NAMES = ("whoosh_soft", "whoosh_fast", "swoosh_up", "swoosh_down", "whip", "pop", "pop_soft",
             "bubble", "click", "tick", "typing", "ding", "bell", "chime", "sparkle", "cash", "coin",
             "riser_short", "riser_long", "impact_soft", "impact_boom", "sub_drop", "glitch",
             "shutter", "notification", "success", "swipe", "scratch", "paper_rip",
             "pen_scribble", "heartbeat", "record_stop")
LOOKS = ("natural", "warm_clean", "cinematic", "bright_pop", "moody", "bw_contrast")

_COMMON = {"id", "start", "end", "enabled", "src", "locked", "intensity", "z"}
_EVENT = {"id", "at", "enabled", "src", "locked", "intensity"}
FIELDS = {
    "text": _COMMON | {"preset", "text", "lines", "x", "y", "size", "font", "color", "accent",
                       "accent_words", "align", "stroke", "shadow", "anim_in", "anim_out",
                       "behind", "box", "from", "to", "prefix", "suffix", "decimals"},
    "graphics": _COMMON | {"kind", "x", "y", "size", "color", "anim_in", "anim_out", "rotation",
                           "name", "stroke", "draw", "wiggle", "path_to", "variant", "badge",
                           "badge_color", "title", "subtitle", "value", "items", "icon", "theme",
                           "label", "handle", "thickness", "text", "bg", "fg"},
    "cutaways": _COMMON | {"kind", "background", "bg_colors", "text", "graphics", "src_t",
                           "zoom_from", "zoom_to", "card", "anim_in", "anim_out"},
    "effects": _COMMON | {"kind", "from", "to", "easing", "amount", "hold", "amp", "freq", "decay",
                          "pan", "contrast", "subject_color", "bg_desat", "bg_dark", "radius",
                          "background", "bg_colors", "color", "width", "glow"},
    "transitions": _EVENT | {"kind", "dur", "dir", "color", "align"},
    "sfx": _EVENT | {"name", "gain_db", "pan"},
}
KIND_KEY = {"text": "preset", "graphics": "kind", "cutaways": "kind", "effects": "kind",
            "transitions": "kind", "sfx": "name"}
KIND_SET = {"text": set(TEXT_PRESETS), "graphics": set(GRAPHIC_KINDS),
            "cutaways": set(CUTAWAY_KINDS), "effects": set(EFFECT_KINDS),
            "transitions": set(TRANSITION_KINDS), "sfx": set(SFX_NAMES)}

# ---------------------------------------------------------------- estilos
# Todo lo que distingue un estilo vive aqui (el coreografo no tiene "ifs" por estilo).
#   presets     : preset principal de cada frase (peso). Una frase usa a lo mas 2 presets:
#                 el principal (bloques con enfasis) y su acompanante (resto).
#   companion   : preset de los bloques sin enfasis.
#   scenes      : presupuesto de "escenas" (fraccion del video con tratamiento de pantalla
#                 completa: cutaways, fondo reemplazado, blanco y negro...) y recursos.
#   broll_keep  : segundos de b-roll mudo que se conservan al proponer recortes (trim).
STYLES: dict[str, dict] = {
    "dinamico": {
        "captions": {"preset": "minimal_small", "y": 0.745, "size": 0.040,
                     "font": "Poppins-ExtraBold", "case": "none", "max_words": 4},
        "presets": [("stack", 4), ("cascade", 2), ("serif_elegant", 2), ("highlight_marker", 2),
                    ("title_pop", 1)],
        "companion": ["title_pop", "kicker"],
        "hook_presets": ["stack"],
        "humor_preset": "script_accent",
        "transitions": [("whip", 3), ("zoom_blur", 3), ("flash", 2), ("light_leak", 2),
                        ("glitch", 1), ("torn_paper", 1)],
        "scene_trans": [("zoom_blur", 3), ("flash", 3), ("glitch", 1), ("whip", 1)],
        "trans_dur": 1.0,
        "look": {"look": "warm_clean", "vignette": 0.15, "grain": 0.022, "sharpen": 0.15},
        "music": {"prompt": "upbeat modern pop instrumental, bright plucks, punchy drums, "
                            "positive and confident, social media ad",
                  "bpm": 112, "gain_db": -17.0, "duck_db": -9.0},
        "doodles": ["sparkle", "star4", "arrow_curve", "underline", "circle_scribble", "burst",
                    "squiggle", "star4"],
        "backgrounds": ["rays", "grid_warp", "dark_stars"],
        "drama": {"effect": "bw", "contrast": 1.35, "giant": True, "shake": False,
                  "rgb": False, "flash": True, "sfx": "impact_boom"},
        "scenes": {"budget": (0.12, 0.3), "bg_replace": ["paper", "grid_warp"], "bg_blur": True,
                   "outline": False},
        "gap": (4.0, 2.0), "coverage": (0.3, 0.85), "cutaways": 1.6, "punch": 1.0,
        "accent_p": (0.45, 0.95), "zoom_face": 0.125, "jump_zoom": 0.14, "sfx_db": 0.0,
        "progress": False, "color_pop": True, "broll_keep": 2.8,
    },
    "elegante": {
        "captions": {"preset": "minimal_small", "y": 0.745, "size": 0.038,
                     "font": "Poppins-SemiBold", "case": "none", "max_words": 4},
        "presets": [("serif_elegant", 4), ("stack", 2), ("cascade", 1)],
        "companion": ["kicker", "serif_elegant"],
        "hook_presets": ["serif_elegant"],
        "humor_preset": "script_accent",
        "transitions": [("light_leak", 3), ("film_burn", 2), ("slide", 1), ("circle", 1),
                        ("flash", 1)],
        "scene_trans": [("light_leak", 2), ("flash", 1), ("film_burn", 1)],
        "trans_dur": 1.25,
        "look": {"look": "cinematic", "vignette": 0.24, "grain": 0.03, "sharpen": 0.1},
        "music": {"prompt": "warm elegant instrumental, soft piano and strings, lo-fi texture, "
                            "calm premium mood",
                  "bpm": 88, "gain_db": -19.0, "duck_db": -8.0},
        "doodles": ["sparkle", "underline", "swirl", "star4", "loop"],
        "backgrounds": ["gradient", "paper"],
        "drama": {"effect": "bw", "contrast": 1.2, "giant": False, "shake": False,
                  "rgb": False, "flash": False, "sfx": "sub_drop"},
        "scenes": {"budget": (0.06, 0.18), "bg_replace": ["gradient"], "bg_blur": True,
                   "outline": False},
        "gap": (4.5, 2.6), "coverage": (0.25, 0.7), "cutaways": 0.7, "punch": 0.6,
        "accent_p": (0.3, 0.7), "zoom_face": 0.115, "jump_zoom": 0.1, "sfx_db": -2.0,
        "progress": False, "color_pop": False, "broll_keep": 3.4,
    },
    "minimal": {
        "captions": {"preset": "clean_bold", "y": 0.74, "size": 0.05,
                     "font": "Montserrat-Bold", "case": "none", "max_words": 4},
        "presets": [("title_pop", 3), ("highlight_marker", 2)],
        "companion": ["kicker"],
        "hook_presets": ["title_pop"],
        "humor_preset": "title_pop",
        "transitions": [("flash", 2), ("slide", 2), ("zoom_blur", 1)],
        "scene_trans": [("flash", 1)],
        "trans_dur": 0.8,
        "look": {"look": "natural", "vignette": 0.08, "grain": 0.0, "sharpen": 0.1},
        "music": {"prompt": "minimal ambient electronic instrumental, soft pulses, clean and airy",
                  "bpm": 100, "gain_db": -20.0, "duck_db": -8.0},
        "doodles": ["underline", "sparkle"],
        "backgrounds": ["color"],
        "drama": {"effect": None, "contrast": 1.2, "giant": False, "shake": False,
                  "rgb": False, "flash": False, "sfx": "impact_soft"},
        "scenes": {"budget": (0.0, 0.06), "bg_replace": [], "bg_blur": True, "outline": False},
        "gap": (5.5, 3.2), "coverage": (0.12, 0.4), "cutaways": 0.0, "punch": 0.5,
        "accent_p": (0.15, 0.4), "zoom_face": 0.11, "jump_zoom": 0.08, "sfx_db": -4.0,
        "progress": False, "color_pop": False, "broll_keep": 3.0,
    },
    "energetico": {
        "captions": {"preset": "bold_caps", "y": 0.74, "size": 0.056,
                     "font": "Poppins-Black", "case": "upper", "max_words": 3},
        "presets": [("stack", 4), ("title_pop", 2), ("highlight_marker", 2), ("cascade", 1)],
        "companion": ["title_pop"],
        "hook_presets": ["stack"],
        "humor_preset": "script_accent",
        "transitions": [("whip", 3), ("glitch", 2), ("zoom_blur", 3), ("flash", 2),
                        ("torn_paper", 1), ("circle", 1)],
        "scene_trans": [("glitch", 2), ("zoom_blur", 2), ("flash", 2)],
        "trans_dur": 0.85,
        "look": {"look": "bright_pop", "vignette": 0.12, "grain": 0.02, "sharpen": 0.2},
        "music": {"prompt": "high energy electronic trap instrumental, hype, heavy 808, big drops",
                  "bpm": 128, "gain_db": -15.0, "duck_db": -10.0},
        "doodles": ["burst", "lightning", "exclaim", "star5", "rays", "arrow_straight", "squiggle"],
        "backgrounds": ["rays", "halftone", "grid_warp", "dark_stars"],
        "drama": {"effect": "bw", "contrast": 1.45, "giant": True, "shake": True,
                  "rgb": True, "flash": True, "sfx": "impact_boom"},
        "scenes": {"budget": (0.16, 0.34), "bg_replace": ["rays", "halftone"], "bg_blur": False,
                   "outline": True},
        "gap": (3.0, 1.4), "coverage": (0.45, 0.95), "cutaways": 1.4, "punch": 1.3,
        "accent_p": (0.6, 1.0), "zoom_face": 0.135, "jump_zoom": 0.18, "sfx_db": 1.0,
        "progress": True, "color_pop": True, "broll_keep": 2.4,
    },
}

# Kit tipografico por estilo: display (palabra protagonista, gigantes, contador), bold (titulos),
# text (lineas chicas, cascada, marcador, kicker) y un acento: serif o manuscrita. El director
# resuelve UNA vez por video si usa serif, manuscrita o ambas, para no pasar de 3 familias y 4
# fuentes (sin contar subtitulos, tarjetas y CTA, que son de graphics/captions).
TYPE_KIT = {
    "dinamico": {"display": "Anton", "bold": "Poppins-Black", "text": "Poppins-ExtraBold",
                 "serif": "DMSerifDisplay", "script": "Caveat-Bold"},
    "elegante": {"display": "DMSerifDisplay", "bold": "DMSerifDisplay", "text": "Poppins-Medium",
                 "serif": "DMSerifDisplay", "script": "Caveat-Bold"},
    "minimal": {"display": "Montserrat-Black", "bold": "Montserrat-ExtraBold",
                "text": "Montserrat-ExtraBold", "serif": "InstrumentSerif-Italic",
                "script": "Caveat-Bold"},
    "energetico": {"display": "Anton", "bold": "Poppins-Black", "text": "Poppins-ExtraBold",
                   "serif": "DMSerifDisplay", "script": "PermanentMarker"},
}
PRESET_ROLE = {"title_pop": "bold", "stack": "display", "cascade": "text", "serif_elegant": "serif",
               "script_accent": "script", "highlight_marker": "text", "kicker": "text",
               "behind_giant": "display", "counter": "display", "typewriter": "text"}
# SFX de entrada de texto por preset (rotan; +-2 dB y paneo determinista)
TEXT_SFX = {"stack": ["pop", "bubble"], "cascade": ["pop_soft", "tick"], "title_pop": ["pop", "click"],
            "highlight_marker": ["swipe", "pop_soft"], "serif_elegant": ["whoosh_soft", "swoosh_up"],
            "script_accent": ["pen_scribble"], "kicker": ["swipe", "click"], "typewriter": ["typing"],
            "behind_giant": ["impact_soft"], "counter": ["pop"]}
NOMINAL_FACE = [0.5, 0.32, 0.12, 0.09]      # cara supuesta de una toma a camara sin deteccion


def font_family(name: str) -> str:
    return str(name or "").split("-")[0]

# SFX por tipo de transicion y ganancias base (dB relativos al nivel del catalogo)
TRANS_SFX = {"whip": "whip", "zoom_blur": "whoosh_fast", "flash": "whoosh_soft",
             "light_leak": "whoosh_soft", "glitch": "glitch", "torn_paper": "paper_rip",
             "film_burn": "whoosh_soft", "circle": "swoosh_up", "slide": "swipe", "cut": None}
SFX_CAT = {n: c for c, ns in {
    "air": ("whoosh_soft", "whoosh_fast", "swoosh_up", "swoosh_down", "whip", "swipe"),
    "ui": ("pop", "pop_soft", "bubble", "click", "tick", "typing", "shutter"),
    "tonal": ("ding", "bell", "chime", "sparkle", "cash", "coin", "notification", "success"),
    "hit": ("impact_soft", "impact_boom", "sub_drop", "heartbeat"),
    "rise": ("riser_short", "riser_long"),
    "tex": ("glitch", "scratch", "paper_rip", "pen_scribble", "record_stop")}.items() for n in ns}
LIGHT_BG = {"paper"}
TRANS_DUR = {"whip": 0.32, "zoom_blur": 0.36, "flash": 0.3, "light_leak": 0.75, "glitch": 0.3,
             "torn_paper": 0.55, "film_burn": 0.8, "circle": 0.5, "slide": 0.42, "cut": 0.05}
SFX_GAIN = {"whoosh_soft": -5, "whoosh_fast": -5, "whip": -4, "swoosh_up": -5, "swoosh_down": -6,
            "pop": -6, "pop_soft": -6, "bubble": -6, "click": -3, "tick": -6, "typing": -6,
            "ding": -4, "bell": -6, "chime": -5, "sparkle": -4, "cash": -3, "coin": -5,
            "riser_short": -6, "riser_long": -7, "impact_soft": -6, "impact_boom": -3,
            "sub_drop": -2, "glitch": -7, "shutter": -6, "notification": -3, "success": -4,
            "swipe": -5, "scratch": -6, "paper_rip": -5, "pen_scribble": -4, "heartbeat": -4,
            "record_stop": -4}

# ---------------------------------------------------------------- texto (espanol)
STOP = set("""a al ante con de del desde el en entre es esta este esto estos estas eso esa ese
hay ha han he la las le les lo los me mi mis muy mas nos o para pero por que se si sin su sus
te tu tus un una unos unas y ya yo como cuando donde cual cuales cuanto cuanta cuantos no ni
tan tambien aqui asi son ser fue era soy estoy estas esta estamos vamos va voy tiene tienen
todo toda todos todas otro otra cada mismo misma bien solo le lo""".split())

_UNITS = {"cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
          "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
          "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
          "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21,
          "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
          "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
          "veintinueve": 29}
_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
         "ochenta": 80, "noventa": 90}
_HUNDREDS = {"cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
             "trescientos": 300, "trescientas": 300, "cuatrocientos": 400, "cuatrocientas": 400,
             "quinientos": 500, "quinientas": 500, "seiscientos": 600, "seiscientas": 600,
             "setecientos": 700, "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
             "novecientos": 900, "novecientas": 900}
_CURRENCY = {"pesos": "MXN", "peso": "MXN", "mxn": "MXN", "varos": "MXN", "dolares": "USD",
             "dolar": "USD", "usd": "USD", "euros": "EUR", "euro": "EUR"}
_STREET = {"avenida": "Av.", "av": "Av.", "calle": "Calle", "calzada": "Calz.",
           "boulevard": "Blvd.", "bulevar": "Blvd.", "blvd": "Blvd.", "periferico": "Periferico",
           "carretera": "Carr.", "privada": "Priv.", "paseo": "Paseo", "circuito": "Circuito",
           "prolongacion": "Prol.", "plaza": "Plaza", "colonia": "Col."}
_LANDMARK_RX = re.compile(
    r"\b(a (?:\w+ )?(?:cuadras?|minutos?|pasos?|metros?) de(?: la| el| los| las)?|"
    r"frente a(?:l| la| el)?|enfrente de(?: la| el)?|junto a(?:l| la| el)?|cerca de(?: la| el)?|"
    r"a un lado de(?: la| el)?|esquina con)\s+(.+)$", re.I)
_CTA_W = {"link", "enlace", "sigueme", "siguenos", "comenta", "comentame", "guarda", "comparte",
          "agenda", "agendar", "agendame", "escribeme", "escribenos", "mandame", "whatsapp",
          "llama", "llamanos", "checa", "reserva", "reservar", "visita", "visitanos", "descarga",
          "suscribete", "click", "clic", "perfil", "bio", "dm", "inscribete", "registrate"}
_LINK_W = {"link", "enlace", "bio", "perfil", "descripcion"}
_SCHED_W = {"horario", "horarios", "lunes", "martes", "miercoles", "jueves", "viernes",
            "sabado", "sabados", "domingo", "domingos", "abrimos", "cerramos", "disponibles",
            "citas", "cita"}
_LOC_W = {"ubicada", "ubicado", "ubicados", "ubicadas", "ubicacion", "direccion", "avenida",
          "calle", "cuadras", "esquina", "colonia", "glorieta", "sucursal", "encuentras",
          "encontramos"}
_PRICE_W = {"costo", "cuesta", "cuestan", "precio", "vale", "valen", "pagas", "inversion",
            "gratis", "descuento", "promocion", "oferta"}
_BENEFIT_W = {"cambia", "mejora", "mejor", "ayuda", "resultado", "resultados", "beneficio",
              "logra", "logras", "rapido", "facil", "salud", "sonrisa", "brillo", "limpio",
              "limpieza", "bonito", "increible", "sano", "sana", "natural", "seguro", "segura"}
_PROBLEM_W = {"dolor", "problema", "miedo", "molestia", "sarro", "mancha", "manchas",
              "sensibilidad", "error", "errores", "peor", "caries", "sangrado", "cansado",
              "cansada", "falla", "fallas", "riesgo", "mal", "malo", "mala"}
_DRAMA_PH = ["necesitamos hablar", "tenemos que hablar", "escucha", "ojo", "atencion", "cuidado",
             "la verdad", "spoiler", "esto es importante", "nadie te dice", "no lo hagas",
             "alto", "para ya", "te voy a decir", "presta atencion"]
_HUMOR_PH = ["no te preocupes", "jaja", "broma", "no vamos a hablar", "ni modo", "sin juzgar",
             "no te juzgo", "te cache", "ups"]
_TOPIC_ROLES = [
    ({"dientes", "diente", "dental", "dentista", "muelas", "encias", "caries", "brackets",
      "ortodoncia", "limpieza dental", "sarro", "odontologia", "consultorio dental"}, "Dentista"),
    ({"nutricion", "dieta", "nutriologa", "nutriologo", "calorias"}, "Nutricion"),
    ({"abogado", "abogada", "demanda", "legal", "juicio"}, "Abogado"),
    ({"entrenamiento", "gym", "gimnasio", "rutina", "musculo"}, "Coach fitness"),
    ({"cabello", "corte", "tinte", "salon", "unas", "manicure"}, "Estilista"),
    ({"casa", "departamento", "inmueble", "renta", "credito", "hipoteca"}, "Bienes raices"),
    ({"piel", "facial", "acne", "dermatologa", "dermatologo"}, "Dermatologia"),
]
_NAME_TITLES = {"dra": "Dra.", "doctora": "Dra.", "dr": "Dr.", "doctor": "Dr.", "lic": "Lic.",
                "licenciada": "Lic.", "licenciado": "Lic.", "chef": "Chef", "coach": "Coach",
                "maestra": "Mtra.", "maestro": "Mtro.", "ing": "Ing.", "arquitecta": "Arq.",
                "arquitecto": "Arq."}

ICON_EN = {
    "tooth": "dental", "teeth": "dental", "dentist": "dental", "dental": "dental",
    "toothbrush": "dental", "smile": "mood-smile", "happy": "mood-happy", "money": "cash",
    "price": "cash", "cost": "cash", "cash": "cash", "pay": "credit-card", "coin": "coin",
    "map": "map-pin", "location": "map-pin", "address": "map-pin", "pin": "map-pin",
    "place": "map-pin", "street": "road", "walk": "walk", "clock": "clock", "time": "clock",
    "hour": "clock", "hours": "clock", "schedule": "calendar-event", "calendar": "calendar-event",
    "appointment": "calendar-event", "link": "link", "click": "hand-click", "talk": "message-circle",
    "chat": "message-circle", "speak": "message-circle", "message": "message-circle",
    "sparkle": "sparkles", "sparkles": "sparkles", "clean": "sparkles", "shine": "sparkles",
    "new": "sparkles", "alert": "alert-circle", "warning": "alert-triangle",
    "question": "help-circle", "check": "circle-check", "ok": "circle-check", "heart": "heart",
    "love": "heart", "star": "star", "phone": "phone", "call": "phone", "relief": "mood-smile",
    "shield": "shield-check", "safe": "shield-check", "home": "home", "store": "building-store",
    "shop": "building-store", "building": "building", "gift": "gift", "fire": "flame",
    "rocket": "rocket", "idea": "bulb", "eye": "eye", "look": "eye", "see": "eye",
    "hand": "hand-finger", "touch": "hand-finger", "feel": "hand-finger", "tongue": "mood-tongue",
    "hourglass": "hourglass", "wait": "hourglass", "follow": "user-plus", "user": "user",
    "trophy": "trophy", "best": "trophy", "lightning": "bolt", "fast": "bolt", "energy": "bolt",
    "health": "heartbeat", "doctor": "stethoscope", "medical": "stethoscope", "car": "car",
    "food": "tools-kitchen-2", "coffee": "coffee", "music": "music", "camera": "camera",
    "video": "video", "discount": "discount", "tag": "tag", "offer": "tag", "wink": "mood-wink",
    "laugh": "mood-happy", "funny": "mood-tongue-wink", "change": "refresh", "stop": "hand-stop",
    "lock": "lock", "secret": "lock-open", "calm": "mood-smile", "worry": "mood-confuzed",
}
# Conjugaciones y palabras generales del espanol (no de un guion en particular)
ICON_ES_EXTRA = {"preocupes": "mood-smile", "preocupar": "mood-smile", "tiempo": "hourglass",
                 "cambiar": "refresh", "hablar": "message-circle", "hablemos": "message-circle",
                 "costo": "cash", "cuesta": "cash", "disponible": "calendar-event",
                 "disponibles": "calendar-event", "caminar": "walk", "cerca": "map-pin"}
# lemas por prefijo (sentir -> sientes/sienten/siento, ver -> ves/ven...)
ICON_STEMS = (("sient", "hand-finger"), ("toca", "hand-finger"),
              ("mira", "eye"), ("luce", "sparkles"), ("brill", "sparkles"), ("limpi", "sparkles"),
              ("sonri", "mood-smile"), ("agend", "calendar-event"), ("ubica", "map-pin"),
              ("prote", "shield-check"), ("cuid", "shield-check"))
# iconos que se leen fuera de tono en un anuncio (emoji burlon, grafica bursatil, etc.)
ICON_BLOCK = {"mood-tongue", "mood-tongue-wink", "mood-tongue-wink-2", "trending-up",
              "trending-down", "x", "bluetooth", "mood-confuzed", "mood-sad", "skull", "poo"}
# sets de iconos por rubro: el director prefiere estos a una traduccion literal
TOPIC_ICONS = {
    "Dentista": ["dental", "sparkles", "shield-check", "mood-smile", "calendar-event", "map-pin",
                 "clock"],
    "Nutricion": ["salad", "apple", "scale", "heartbeat", "leaf", "calendar-event", "map-pin"],
    "Abogado": ["scale", "file-text", "shield-check", "briefcase", "calendar-event", "map-pin"],
    "Coach fitness": ["barbell", "flame", "heartbeat", "trophy", "calendar-event", "map-pin"],
    "Estilista": ["scissors", "sparkles", "brush", "heart", "calendar-event", "map-pin"],
    "Bienes raices": ["home", "key", "building", "map-pin", "calendar-event", "cash"],
    "Dermatologia": ["sparkles", "droplet", "sun", "shield-check", "calendar-event", "map-pin"],
    "": ["sparkles", "star", "heart", "bulb", "circle-check", "calendar-event", "map-pin"],
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str) -> str:
    """minusculas, sin acentos, solo alfanumericos (para comparar palabras)."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(str(s).lower()))


def _display(word: str) -> str:
    """Palabra para titulo: sin comas/puntos finales, conserva signos de pregunta/exclamacion."""
    w = str(word).strip()
    w = re.sub(r"[,.;:…]+$", "", w)
    w = re.sub(r"^[,.;:…]+", "", w)
    return w


def _cap_first(s: str) -> str:
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i + 1:]
    return s


def parse_number(tokens: list[str], i: int) -> tuple[float | None, int]:
    """Numero en espanol (palabras o digitos) que empieza en tokens[i] (normalizados).

    Devuelve (valor, n_tokens). "mil" -> 1000, "dos mil quinientos" -> 2500, "2150" -> 2150,
    "1,500" -> 1500, "5 mil" -> 5000, "treinta y cinco" -> 35.
    """
    if i >= len(tokens):
        return None, 0
    t = tokens[i]
    if re.fullmatch(r"\d+", t):
        v = float(t)
        n = 1
        if i + 1 < len(tokens) and tokens[i + 1] == "mil":
            v *= 1000
            n = 2
        return v, n
    total, cur, j, seen = 0, 0, i, False
    while j < len(tokens):
        w = tokens[j]
        if w in _UNITS:
            cur += _UNITS[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w in _HUNDREDS:
            cur += _HUNDREDS[w]
        elif w == "y" and seen and j + 1 < len(tokens) and tokens[j + 1] in _UNITS:
            j += 1
            continue
        elif w == "mil":
            total += (cur or 1) * 1000
            cur = 0
        elif w in ("millon", "millones"):
            total += (cur or 1) * 1_000_000
            cur = 0
        else:
            break
        seen = True
        j += 1
    if not seen:
        return None, 0
    return float(total + cur), j - i


def fmt_money(v: float, cur: str = "MXN") -> str:
    s = f"{v:,.2f}" if abs(v - round(v)) > 1e-6 else f"{v:,.0f}"
    if cur == "EUR":
        return f"{s} EUR"
    return f"${s} {cur}".strip()


# ---------------------------------------------------------------- utilidades
def _now() -> float:
    return time.perf_counter()


def _step(on_step, name: str, pct: float, info: str = "") -> None:
    if not on_step:
        return
    try:
        on_step(name, pct, info)
    except TypeError:
        try:
            on_step(name, pct)
        except Exception:
            pass
    except Exception:
        pass


def _ffmpeg() -> str:
    return (getattr(_paths, "FFMPEG", None) if _paths else None) or shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe() -> str:
    return (getattr(_paths, "FFPROBE", None) if _paths else None) or shutil.which("ffprobe") or "ffprobe"


def _work_dir() -> Path:
    base = getattr(_paths, "WORK", None) if _paths else None
    return Path(base) if base else Path(tempfile.gettempdir())


def _cache_path(kind: str, key: str) -> Path:
    return _work_dir() / "director_cache" / f"{kind}_{key}.json"


def _cache_get(kind: str, key: str):
    if os.environ.get("REELFY_DIRECTOR_NOCACHE"):
        return None
    p = _cache_path(kind, key)
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except Exception:
        return None


def _cache_put(kind: str, key: str, val) -> None:
    try:
        p = _cache_path(kind, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(val, ensure_ascii=False))
        tmp.replace(p)
    except Exception:
        pass


def _hkey(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode())
    return h.hexdigest()[:16]


def _r(v: float, n: int = 3) -> float:
    return float(round(float(v), n))


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


# ---------------------------------------------------------------- ollama (LLM / VLM locales)
def _ollama_url() -> str:
    return (os.environ.get("REELFY_OLLAMA_URL") or "http://127.0.0.1:11498").rstrip("/")


def _llm_model() -> str:
    return os.environ.get("REELFY_LLM_MODEL") or "qwen3.5:4b"


def _vlm_model() -> str:
    return os.environ.get("REELFY_VLM_MODEL") or _llm_model()


_AVAIL: dict[str, tuple[float, bool]] = {}


def llm_available(model: str | None = None, timeout: float = 2.0) -> bool:
    """True si ollama responde y tiene el modelo (cache de 30 s)."""
    model = model or _llm_model()
    key = _ollama_url() + "|" + model
    hit = _AVAIL.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    ok = False
    try:
        with urllib.request.urlopen(_ollama_url() + "/api/tags", timeout=timeout) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        base = model.split(":")[0]
        ok = model in names or any(n.split(":")[0] == base and model.endswith(":latest") for n in names)
    except Exception:
        ok = False
    _AVAIL[key] = (time.time(), ok)
    return ok


def _ollama_chat(messages: list[dict], fmt: Any = "json", model: str | None = None,
                 num_ctx: int = 4096, temperature: float = 0.1, num_predict: int = 900,
                 timeout: float = 60.0) -> str:
    body = {"model": model or _llm_model(), "stream": False, "format": fmt, "think": False,
            "keep_alive": "10m",
            "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict,
                        "seed": 7},
            "messages": messages}
    req = urllib.request.Request(_ollama_url() + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["message"]["content"]


def _json_loose(s: str) -> dict | None:
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", s or "", re.S)
        if m:
            try:
                v = json.loads(m.group(0))
                return v if isinstance(v, dict) else None
            except Exception:
                return None
    return None


# ---------------------------------------------------------------- linea de tiempo
class _MiniTimeline:
    """Respaldo minimo de motion.timeline.Timeline (misma semantica de remap_clamped)."""

    def __init__(self, plan: dict):
        dur = float(plan.get("duration") or 0.0)
        act = sorted((float(c["start"]), float(c["end"])) for c in plan.get("cuts") or []
                     if c.get("enabled", True))
        keeps, t = [], 0.0
        for s, e in act:
            if s - t > 0.05:
                keeps.append((t, s))
            t = max(t, e)
        if dur - t > 0.05:
            keeps.append((t, dur))
        self.keeps = keeps
        self.offsets, acc = [], 0.0
        for a, b in keeps:
            self.offsets.append(acc)
            acc += b - a
        self.final_duration = acc
        self.duration = dur

    def remap(self, t):
        for (a, b), o in zip(self.keeps, self.offsets):
            if a - 1e-9 <= t <= b + 1e-9:
                return o + max(0.0, t - a)
        return None

    def remap_clamped(self, t):
        prev = 0.0
        for (a, b), o in zip(self.keeps, self.offsets):
            if t < a:
                return o
            if t <= b:
                return o + (t - a)
            prev = o + (b - a)
        return prev

    def inverse(self, tf):
        if not self.keeps:
            return 0.0
        for (a, b), o in zip(self.keeps, self.offsets):
            if tf <= o + (b - a) + 1e-9:
                return a + max(0.0, tf - o)
        return self.keeps[-1][1]

    @property
    def joins(self):
        return [{"index": i, "t": self.offsets[i], "before": self.keeps[i - 1][1],
                 "after": self.keeps[i][0]} for i in range(1, len(self.keeps))]


def _timeline(plan: dict):
    try:
        from . import timeline as T
        return T.Timeline.from_plan(plan)
    except Exception:
        return _MiniTimeline(plan)


def _plan_duration(plan: dict) -> float:
    d = float(plan.get("duration") or 0.0)
    if d <= 0:
        ws = [w for ph in plan.get("phrases") or [] for w in ph]
        d = max([float(w.get("end", 0)) for w in ws] + [0.0]) + 0.5
    return d


# ---------------------------------------------------------------- paso 1: tomas
def _probe_duration(video: str) -> float:
    try:
        out = subprocess.run([_ffprobe(), "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(video)], capture_output=True, text=True,
                             timeout=20).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def scan_video(video: str, duration: float | None = None) -> dict:
    """UNA pasada de decodificacion: cuadros diminutos a 30 fps (cortes, movimiento) y
    cuadros medianos a 2 fps (rostro y VLM). Usa VideoToolbox si esta disponible."""
    import cv2
    t0 = _now()
    video = str(video)
    dur = float(duration or 0.0) or _probe_duration(video)
    tmpd = Path(tempfile.mkdtemp(prefix="reelfy_dir_", dir=str(_work_dir())))
    small = None
    try:
        for hw in (["-hwaccel", "videotoolbox"] if (_paths and getattr(_paths, "IS_MAC", False)) else [], []):
            cmd = [_ffmpeg(), "-v", "error", "-y", *hw, "-i", video, "-filter_complex",
                   "[0:v]split=2[a][b];[a]fps=30,scale=48:48:flags=area,format=gray[s];"
                   "[b]fps=2,scale='if(gt(iw,ih),640,-2)':'if(gt(iw,ih),-2,640)',format=yuvj420p[m]",
                   "-map", "[s]", "-f", "rawvideo", "pipe:1",
                   "-map", "[m]", "-q:v", "3", str(tmpd / "m_%04d.jpg")]
            res = subprocess.run(cmd, capture_output=True, timeout=600)
            if res.returncode == 0 and res.stdout:
                small = np.frombuffer(res.stdout, np.uint8).reshape(-1, 48, 48).astype(np.float32)
                break
            for f in tmpd.glob("m_*.jpg"):
                f.unlink()
        if small is None:
            raise RuntimeError("no se pudo decodificar el video")
        files = sorted(tmpd.glob("m_*.jpg"))
        med = [cv2.cvtColor(cv2.imread(str(f)), cv2.COLOR_BGR2RGB) for f in files]
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    diffs = np.abs(np.diff(small, axis=0)).mean(axis=(1, 2)) if len(small) > 1 else np.zeros(0)
    if dur <= 0:
        dur = len(small) / 30.0
    return {"diff": diffs, "fps": 30.0, "med": med, "med_t": [j * 0.5 for j in range(len(med))],
            "duration": dur, "ms": (_now() - t0) * 1000}


def _cuts_from_diff(d: np.ndarray, fps: float, dur: float, min_len: float = 0.6) -> list[float]:
    """Cortes duros: pico de diferencia >> mediana de su vecindad (robusto a camara en mano)."""
    n = len(d)
    if n < 3:
        return []
    k = max(3, int(fps * 0.5))
    cand = []
    for i in range(n):
        lo, hi = max(0, i - k), min(n, i + k + 1)
        nb = np.concatenate([d[lo:i], d[i + 1:hi]])
        base = float(np.median(nb)) if nb.size else 0.0
        if d[i] > 12.0 and d[i] / (base + 2.0) > 4.0:
            cand.append(((i + 1) / fps, float(d[i])))
    cand.sort(key=lambda c: -c[1])
    out: list[float] = []
    for t, _ in cand:
        if t < min_len or t > dur - min_len:
            continue
        if all(abs(t - u) >= min_len for u in out):
            out.append(t)
    return sorted(out)


def detect_shots(video: str, duration: float | None = None, scan: dict | None = None) -> list[dict]:
    """Tomas [{start, end}] del video (tiempo original)."""
    sc = scan or scan_video(video, duration)
    dur = float(duration or sc["duration"])
    cuts = _cuts_from_diff(sc["diff"], sc["fps"], dur)
    bounds = [0.0] + cuts + [dur]
    return [{"start": _r(a), "end": _r(b)} for a, b in zip(bounds[:-1], bounds[1:]) if b - a > 0.05]


def _yunet(w: int, h: int):
    import cv2
    model = (getattr(_paths, "YUNET", None) if _paths else None)
    if not model or not Path(model).exists():
        return None
    try:
        return cv2.FaceDetectorYN.create(str(model), "", (w, h), 0.6, 0.3, 50)
    except Exception:
        return None


def _shot_faces(sc: dict, shots: list[dict]) -> list[dict]:
    import cv2
    out = []
    det_cache: dict = {}
    for s in shots:
        idx = [j for j, t in enumerate(sc["med_t"]) if s["start"] + 0.12 <= t <= s["end"] - 0.12]
        if not idx:
            idx = [min(range(len(sc["med_t"])), key=lambda j: abs(sc["med_t"][j] - (s["start"] + s["end"]) / 2))] if sc["med_t"] else []
        boxes = []
        for j in idx:
            img = sc["med"][j]
            h, w = img.shape[:2]
            det = det_cache.get((w, h))
            if det is None:
                det = det_cache[(w, h)] = _yunet(w, h)
            if det is None:
                break
            det.setInputSize((w, h))
            _, faces = det.detect(cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
            if faces is None or len(faces) == 0:
                continue
            fb = max(faces, key=lambda r: r[2] * r[3])
            x, y, fw, fh = (float(v) for v in fb[:4])
            if fw * fh < 0.0008 * w * h:
                continue
            boxes.append(((x + fw / 2) / w, (y + fh / 2) / h, fw / w, fh / h))
        ratio = len(boxes) / max(1, len(idx))
        if boxes:
            a = np.array(boxes)
            face = [float(v) for v in np.median(a, axis=0)]
            spread = float(np.std(a[:, 0]))
        else:
            face, spread = None, 0.0
        out.append({"face": face, "face_ratio": ratio, "face_spread": spread})
    return out


_VLM_SCHEMA = {
    "type": "object",
    "properties": {
        "person": {"type": "boolean"},
        "framing": {"type": "string", "enum": ["primer_plano", "medio", "general", "sin_persona"]},
        "place": {"type": "string"},
        "text_seen": {"type": "array", "items": {"type": "string"}},
        "free_space": {"type": "string", "enum": ["arriba", "abajo", "izquierda", "derecha", "ninguno"]},
        "action": {"type": "string"},
    },
    "required": ["person", "framing", "place", "text_seen", "free_space", "action"],
}
_VLM_PROMPT = ("Cuadro de un video vertical corto. Describe la toma en espanol y responde SOLO JSON: "
               "person (hay una persona visible), framing (primer_plano|medio|general|sin_persona), "
               "place (lugar en 2-5 palabras), text_seen (hasta 5 textos legibles MAS GRANDES: "
               "letreros, numeros, nombres; copia exacta; lista vacia si no hay), free_space "
               "(zona libre para poner texto), action (que se ve, breve).")


def _vlm_describe(img_rgb: np.ndarray, timeout: float = 30.0) -> dict | None:
    """Descripcion de un cuadro con el VLM local (JSON con esquema). None si falla."""
    import cv2
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        return None
    b64 = base64.b64encode(buf.tobytes()).decode()
    try:
        raw = _ollama_chat([{"role": "user", "content": _VLM_PROMPT, "images": [b64]}],
                           fmt=_VLM_SCHEMA, model=_vlm_model(), num_ctx=4096, temperature=0.1,
                           num_predict=300, timeout=timeout)
    except Exception:
        return None
    d = _json_loose(raw)
    if not d:
        return None
    ts = d.get("text_seen") or []
    if not isinstance(ts, list):
        ts = [str(ts)]
    seen, clean = set(), []
    for t in ts:
        t = str(t).strip()
        if t and norm(t) not in seen and len(t) <= 60:
            seen.add(norm(t))
            clean.append(t)
    d["text_seen"] = clean[:6]
    return d


def _grab_frame(video: str, t: float, long_side: int = 960) -> np.ndarray | None:
    """Un cuadro RGB (lado largo = long_side) en el tiempo t (busqueda rapida)."""
    import cv2
    try:
        r = subprocess.run([_ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", video,
                            "-frames:v", "1", "-vf",
                            f"scale='if(gt(iw,ih),{long_side},-2)':'if(gt(iw,ih),-2,{long_side})'",
                            "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "pipe:1"],
                           capture_output=True, timeout=30)
        if r.returncode != 0 or not r.stdout:
            return None
        img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if img is not None else None
    except Exception:
        return None


def _framing_from_face(face) -> str:
    if not face:
        return "sin_persona"
    h = face[3]
    if h >= 0.17:
        return "primer_plano"
    if h >= 0.10:
        return "medio"
    return "general"


def _free_space(face) -> str:
    if not face:
        return "arriba"
    cx, cy, w, h = face
    head_top = cy - h * 0.95
    if head_top >= 0.24:
        return "arriba"
    if cy + h * 0.6 <= 0.45:
        return "abajo"
    if cx >= 0.62:
        return "izquierda"
    if cx <= 0.38:
        return "derecha"
    return "ninguno"


ROWS = 16      # franjas horizontales del perfil de fondo por toma


def _shot_rows(sc: dict, s: dict) -> list[list[int]] | None:
    """Perfil de color del fondo de una toma: mediana (en el tiempo) del color medio de ROWS
    franjas horizontales, en el 70% central del ancho (donde van los titulos). sRGB 0-255."""
    idx = [j for j, t in enumerate(sc.get("med_t") or []) if s["start"] + 0.1 <= t <= s["end"] - 0.1]
    if not idx and sc.get("med_t"):
        mid = (s["start"] + s["end"]) / 2
        idx = [min(range(len(sc["med_t"])), key=lambda j: abs(sc["med_t"][j] - mid))]
    prof = []
    for j in idx[:24]:
        img = sc["med"][j]
        h, w = img.shape[:2]
        x0, x1 = int(w * 0.15), int(w * 0.85)
        band = img[:, x0:x1].astype(np.float32)
        rows = [band[int(h * k / ROWS):max(int(h * k / ROWS) + 1, int(h * (k + 1) / ROWS))].mean(axis=(0, 1))
                for k in range(ROWS)]
        prof.append(rows)
    if not prof:
        return None
    med = np.median(np.array(prof), axis=0)
    return [[int(round(c)) for c in r] for r in med]


def _speech_ratio(plan: dict, a: float, b: float) -> float:
    ws = [w for ph in plan.get("phrases") or [] for w in ph or []]
    tot = 0.0
    for w in ws:
        try:
            lo, hi = max(a, float(w["start"])), min(b, float(w["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        tot += max(0.0, hi - lo)
    return tot / max(1e-3, b - a)


def analyze_shots(plan: dict, use_llm: bool = True, on_step=None, scan: dict | None = None,
                  deadline: float | None = None, on_basic: Callable | None = None) -> list[dict]:
    """Tomas con campos del contrato (+ face/motion/rows). Nunca lanza: sin video -> una toma.
    `deadline` (time.perf_counter) limita el VLM: las tomas que no alcancen quedan con
    los campos heuristicos (rostro/encuadre), para que el director nunca pase de ~60 s.
    `on_basic(shots)` se llama en cuanto se conocen limites + rostro (antes del VLM): el
    director lanza ahi el LLM del guion con las tomas, en paralelo al VLM."""
    dur = _plan_duration(plan)
    video = plan.get("video")

    def basic(shots_):
        if on_basic:
            try:
                on_basic(shots_)
            except Exception:
                pass

    shots_in = [dict(s) for s in plan.get("shots") or [] if isinstance(s, dict) and "start" in s]
    complete = shots_in and all(k in s for s in shots_in for k in ("person", "use", "framing"))
    if complete and all("face" in s for s in shots_in):
        basic(shots_in)
        return shots_in
    if not video or not Path(str(video)).exists():
        if shots_in:
            for s in shots_in:
                s.setdefault("person", True)
                s.setdefault("framing", "medio")
                s.setdefault("place", "")
                s.setdefault("text_seen", [])
                s.setdefault("use", "a_camara" if s["person"] else "broll")
                s.setdefault("free_space", "arriba")
                s.setdefault("src", "")
            basic(shots_in)
            return shots_in
        out = [{"start": 0.0, "end": _r(dur), "src": "", "person": True, "framing": "medio",
                "place": "", "text_seen": [], "use": "a_camara", "free_space": "arriba",
                "face": None, "motion": 0.0}]
        basic(out)
        return out
    try:
        st = Path(str(video)).stat()
        vkey = _hkey(str(video), st.st_size, int(st.st_mtime), [(s["start"], s["end"]) for s in shots_in],
                     PROMPT_VERSION, _vlm_model() if use_llm else "none")
    except Exception:
        vkey = None
    if vkey:
        hit = _cache_get("shots", vkey)
        if hit:
            basic(hit)
            return hit
    _step(on_step, "tomas", 0.05, "analizando cortes")
    sc = scan or scan_video(str(video), dur)
    shots = shots_in or detect_shots(str(video), dur, sc)
    faces = _shot_faces(sc, shots)
    yunet_ok = bool(sc.get("med")) and _yunet(*sc["med"][0].shape[1::-1]) is not None
    fps = sc["fps"]
    out = []
    for i, s in enumerate(shots):
        fa = faces[i]
        a, b = int(s["start"] * fps), max(int(s["start"] * fps) + 1, int(s["end"] * fps) - 1)
        seg = sc["diff"][a:b]
        out.append({"start": _r(s["start"]), "end": _r(s["end"]), "src": Path(str(video)).name,
                    "person": fa["face_ratio"] >= 0.3,
                    "face": [_r(x, 4) for x in fa["face"]] if fa["face"] and fa["face_ratio"] >= 0.3 else None,
                    "motion": _r(float(np.median(seg)) if len(seg) else 0.0, 2),
                    "rows": _shot_rows(sc, s), "speech": _r(_speech_ratio(plan, s["start"], s["end"]), 2)})
    basic([dict(o, framing=_framing_from_face(o["face"]), use="a_camara" if o["face"] else "broll",
                place="", text_seen=[]) for o in out])
    use_vlm = use_llm and llm_available(_vlm_model())
    vlm: list[dict | None] = [None] * len(shots)
    if use_vlm and sc["med"]:
        _step(on_step, "tomas", 0.2, "describiendo tomas (VLM)")
        for i, s in enumerate(shots):
            if deadline is not None and _now() > deadline:
                break
            mid = (s["start"] + s["end"]) / 2
            j = min(range(len(sc["med_t"])), key=lambda k: abs(sc["med_t"][k] - mid))
            img = sc["med"][j]
            if faces[i]["face_ratio"] < 0.3:      # b-roll: letreros -> mas resolucion para leer
                hi = _grab_frame(str(video), s["start"] + (s["end"] - s["start"]) * 0.35, 960)
                img = hi if hi is not None else img
            vlm[i] = _vlm_describe(img, timeout=max(5.0, min(30.0, (deadline - _now()) if deadline else 30.0)))
            _step(on_step, "tomas", 0.2 + 0.7 * (i + 1) / len(shots), f"toma {i + 1}/{len(shots)}")
    for i, o in enumerate(out):
        fa = faces[i]
        v = vlm[i] or {}
        face = o["face"]
        person = bool(face or (v.get("person") and fa["face_ratio"] > 0.0))
        if face:
            use = "a_camara"
        elif not yunet_ok and (o["speech"] > 0.5 or v.get("person")):
            # sin detector de rostros no se degrada la edicion: si hay voz o persona, es a camara
            use, person = "a_camara", True
        else:
            use = "broll"
        framing = _framing_from_face(face) if face else ("general" if person else "sin_persona")
        o.update({"person": person, "framing": framing,
                  "place": str(v.get("place") or "").strip()[:60],
                  "text_seen": list(v.get("text_seen") or []), "use": use,
                  "free_space": _free_space(face) if face else str(v.get("free_space") or "arriba"),
                  "vlm": bool(vlm[i])})
    keys = ("start", "end", "src", "person", "framing", "place", "text_seen", "use", "free_space",
            "face", "motion", "rows", "speech", "vlm")
    out = [{k: o[k] for k in keys} for o in out]
    if vkey and (not use_vlm or all(v is not None for v in vlm)):
        _cache_put("shots", vkey, out)
    return out


# ---------------------------------------------------------------- paso 2: guion
def _flatten_words(plan: dict) -> list[dict]:
    ws = []
    for pi, ph in enumerate(plan.get("phrases") or []):
        for w in ph or []:
            try:
                t0, t1 = float(w["start"]), float(w["end"])
            except (KeyError, TypeError, ValueError):
                continue
            txt = str(w.get("text", "")).strip()
            if not txt:
                continue
            ws.append({"text": txt, "n": norm(txt), "t0": t0, "t1": max(t1, t0 + 0.02),
                       "kw": bool(w.get("kw")), "ph": pi})
    ws.sort(key=lambda w: (w["t0"], w["t1"]))
    # tiempos monotonos (el alineador a veces encima una calle y su numero)
    for k in range(1, len(ws)):
        if ws[k]["t0"] < ws[k - 1]["t1"] - 1e-3:
            ws[k]["t0"] = min(ws[k - 1]["t1"], ws[k]["t1"] - 0.02)
    for i, w in enumerate(ws):
        w["i"] = i
    return ws


def _sentences(words: list[dict]) -> list[dict]:
    sents, cur = [], []
    for k, w in enumerate(words):
        if cur and (w["t0"] - cur[-1]["t1"] > 0.8) and not (
                cur[-1]["text"].endswith(",") and w["t0"] - cur[-1]["t1"] < 2.5):
            sents.append(cur)
            cur = []
        cur.append(w)
        end_p = bool(re.search(r"[.!?…]$", w["text"]))
        nxt_gap = (words[k + 1]["t0"] - w["t1"]) if k + 1 < len(words) else 9
        comma = w["text"].endswith(",") and nxt_gap < 2.5
        if end_p or (nxt_gap > 0.42 and len(cur) >= 5 and not comma):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    out = []
    for si, s in enumerate(sents):
        for w in s:
            w["s"] = si
        out.append({"id": si, "w0": s[0]["i"], "w1": s[-1]["i"],
                    "text": " ".join(w["text"] for w in s), "t0": s[0]["t0"], "t1": s[-1]["t1"]})
    return out


def _find_phrase(ns: list[str], phrase: str) -> int:
    p = phrase.split()
    for i in range(len(ns) - len(p) + 1):
        if ns[i:i + len(p)] == p:
            return i
    return -1


def extract_entities(words: list[dict], sents: list[dict], shots: list[dict] | None = None) -> tuple[dict, dict]:
    """Entidades por reglas (sin LLM) + anclas (indices de palabra) para el coreografo."""
    ent = {"name": "", "role": "", "price": "", "address": "", "landmark": "", "cta": "",
           "link_label": "", "handle": "", "schedule": "", "phone": ""}
    anc: dict[str, Any] = {}
    ns = [w["n"] for w in words]
    # precio: numero + moneda (o "$")
    for i in range(len(words)):
        v, n = parse_number(ns, i)
        if v is None or v <= 0:
            continue
        j = i + n
        cur = _CURRENCY.get(ns[j]) if j < len(ns) else None
        dollar = words[i]["text"].startswith("$")
        if cur or dollar:
            ctx_w = set(ns[max(0, i - 6):i])
            if cur or ctx_w & _PRICE_W:
                ent["price"] = fmt_money(v, cur or "MXN")
                anc["price"] = [i, j if cur else i + n - 1]
                anc["price_value"] = v
                break
    # direccion: palabra de calle + nombre + numero
    for i, n in enumerate(ns):
        if n in _STREET and i + 1 < len(ns):
            parts, j, got_num = [_STREET[n]], i + 1, False
            while j < len(words) and j <= i + 6:
                raw = _display(words[j]["text"])
                if re.fullmatch(r"\d{1,6}[a-zA-Z]?", raw):
                    parts.append(raw)
                    got_num = True
                    j += 1
                    break
                if raw[:1].isupper() or ns[j] in ("de", "del", "la", "las", "los"):
                    parts.append(raw)
                    j += 1
                    if re.search(r"[,.]$", words[j - 1]["text"]):
                        break
                    continue
                break
            if len(parts) >= 2 and (got_num or n in ("avenida", "av", "calle", "plaza")):
                ent["address"] = " ".join(parts).replace(" ,", ",")
                anc["address"] = [i, j - 1]
                break
    # referencia (landmark): "a dos cuadras del Parque Rojo", "frente a la Plaza Mayor"
    for s in sents:
        txt = " ".join(w["text"] for w in words[s["w0"]:s["w1"] + 1])
        tn = strip_accents(txt.lower())
        m = _LANDMARK_RX.search(re.sub(r"[,.;]", "", tn))
        if not m:
            continue
        lead = m.group(1)
        lead_tokens = lead.split()
        swords = words[s["w0"]:s["w1"] + 1]
        sn = [w["n"] for w in swords]
        k0 = _find_phrase(sn, " ".join(norm(t) for t in lead_tokens))
        if k0 < 0:
            continue
        tail = swords[k0 + len(lead_tokens):]
        name_ws = []
        for w in tail:
            raw = _display(w["text"])
            if raw[:1].isupper() or (name_ws and w["n"] in ("de", "del", "la")):
                name_ws.append(raw)
                if re.search(r"[.,]$", w["text"]):
                    break
            else:
                break
        if not name_ws:
            continue
        lead_disp = " ".join(_display(w["text"]) for w in swords[k0:k0 + len(lead_tokens)])
        # "dos" -> "2" en la referencia (mas legible en una tarjeta)
        lead_disp = re.sub(r"\b(" + "|".join(k for k in _UNITS if len(k) > 2) + r")\b",
                           lambda mm: str(_UNITS[mm.group(1).lower()]), lead_disp, flags=re.I)
        ent["landmark"] = f"{lead_disp} {' '.join(name_ws)}".strip()
        anc["landmark"] = [swords[k0]["i"], swords[k0 + len(lead_tokens) + len(name_ws) - 1]["i"]]
        break
    # CTA / link / horario
    for s in sents:
        sn = [w["n"] for w in words[s["w0"]:s["w1"] + 1]]
        hit = set(sn) & _CTA_W
        if not hit:
            continue
        if not ent["cta"]:
            ws_ = [w for w in words[s["w0"]:s["w1"] + 1]]
            keep = []
            for w in ws_:
                if w["n"] in ("aqui", "abajo", "aca") and keep and keep[-1]["n"] in ("link", "enlace", "aqui"):
                    continue
                keep.append(w)
            ent["cta"] = _cap_first(" ".join(_display(w["text"]) for w in keep[:8]))
            anc["cta_sent"] = s["id"]
        for k, n in enumerate(sn):
            if n in _LINK_W and "link" not in anc:
                anc["link"] = s["w0"] + k
        sset = set(sn)
        if "link" in anc and not ent["link_label"]:
            if sset & {"horarios", "horario", "cita", "citas", "agenda", "agendar", "reserva"}:
                ent["link_label"] = "Agenda tu cita"
            elif sset & {"compra", "compralo", "tienda", "pedido", "ordena"}:
                ent["link_label"] = "Compra aqui"
            elif sset & {"descarga", "descargalo"}:
                ent["link_label"] = "Descargalo gratis"
            elif sset & {"inscribete", "registrate"}:
                ent["link_label"] = "Inscribete"
            else:
                ent["link_label"] = "Link abajo"
    for s in sents:
        sn = [w["n"] for w in words[s["w0"]:s["w1"] + 1]]
        for k, n in enumerate(sn):
            if n in ("horario", "horarios") and "schedule" not in anc:
                anc["schedule"] = s["w0"] + k
    days = [w for w in words if w["n"] in {"lunes", "martes", "miercoles", "jueves", "viernes",
                                             "sabado", "sabados", "domingo", "domingos"}]
    if len(days) >= 2:
        ent["schedule"] = f"{_cap_first(_display(days[0]['text']))} a {_display(days[1]['text'])}"
    # telefono y usuario
    alltxt = " ".join(w["text"] for w in words)
    m = re.search(r"(\d[\d\s-]{8,}\d)", alltxt)
    if m and len(re.sub(r"\D", "", m.group(1))) >= 10:
        ent["phone"] = m.group(1).strip()
    m = re.search(r"@([A-Za-z0-9_.]{3,30})", alltxt)
    if m:
        ent["handle"] = "@" + m.group(1)
    else:
        for k, n in enumerate(ns[:-1]):
            if n == "arroba":
                ent["handle"] = "@" + re.sub(r"[^A-Za-z0-9_.]", "", words[k + 1]["text"])
                break
    # nombre: "soy la Dra. X", "me llamo X", "mi nombre es X"
    for k in range(len(ns)):
        start = None
        if ns[k] == "soy":
            start = k + 1
        elif ns[k] == "llamo" and k > 0 and ns[k - 1] == "me":
            start = k + 1
        elif ns[k] == "es" and k > 1 and ns[k - 1] == "nombre":
            start = k + 1
        if start is None:
            continue
        j = start
        if j < len(ns) and ns[j] in ("la", "el"):
            j += 1
        title = ""
        if j < len(ns) and ns[j] in _NAME_TITLES:
            title = _NAME_TITLES[ns[j]]
            j += 1
        nm = []
        while j < len(words) and len(nm) < 3 and _display(words[j]["text"])[:1].isupper():
            nm.append(_display(words[j]["text"]))
            j += 1
        if nm:
            ent["name"] = (title + " " + " ".join(nm)).strip()
            anc["name"] = [start, j - 1]
            if title in ("Dra.", "Dr."):
                ent["role"] = ent["role"] or ("Doctora" if title == "Dra." else "Doctor")
            break
    # rol por tema (transcripcion + lugares de las tomas)
    bag = Counter(ns)
    places = " ".join(norm(s.get("place", "")) + " " for s in shots or [])
    best, best_n = "", 0
    for keys, role in _TOPIC_ROLES:
        c = sum(bag[k] for k in keys if " " not in k) + sum(2 for k in keys if k.replace(" ", "") in places.replace(" ", ""))
        if c > best_n:
            best, best_n = role, c
    if best_n >= 2 and not ent["role"]:
        ent["role"] = best
    return ent, anc


_SPECIALTIES = ("ginecolog", "oftalmolog", "urolog", "cardiolog", "pediatr", "dermatolog",
                "oncolog", "gastro", "geriatr", "traumatolog", "neurolog", "psicolog", "nutri",
                "cirugia", "cosmet", "obstetric", "otorrino", "ortoped", "abogad", "notari")


_PROFESSIONS = ("dentista", "odontolog", "especialista", "cirujan", "medic", "doctor", "abogad",
                "nutriolog", "psicolog", "consultorio", "clinica")


def _ocr_confirm(ent: dict, shots: list[dict]) -> dict:
    """Cruza entidades con los textos vistos por el VLM.

    Un nombre leido en un letrero es un DATO DELICADO (va en un rotulo del cliente): solo se
    usa si aparece en el MISMO texto que la profesion del video y el letrero no es un
    directorio (otras especialidades u otros nombres visibles). Si no, queda como candidato
    en director.script.ocr para que el usuario lo elija en el Studio."""
    seen = [t for s in shots or [] for t in s.get("text_seen") or []]
    info = {"address_seen": False, "name_candidates": []}
    num = re.findall(r"\d{2,6}", ent.get("address", ""))
    if num and any(num[-1] in re.sub(r"\D", "", t) for t in seen):
        info["address_seen"] = True
    rx = re.compile(r"\b(?:dra?|dr|doctora|doctor|lic)\.?\s+([A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]+){0,3})", re.I)
    cands = []
    for t in seen:
        for m in rx.finditer(t):
            cands.append((m.group(0).strip(), t))
    info["name_candidates"] = [c for c, _ in cands][:6]
    alln = norm(" ".join(seen))
    directory = any(sp in alln for sp in _SPECIALTIES) or len({norm(c) for c, _ in cands}) > 1
    role = norm(ent.get("role", ""))[:6]
    if not ent.get("name") and cands and not directory and role:
        c, src = cands[0]
        if role in norm(src):
            toks = c.split()
            ttl = norm(toks[0])
            nm = []
            for w in toks[1:]:
                nw = norm(w)
                if nw.startswith(_PROFESSIONS) or nw.startswith(_SPECIALTIES) or (role and nw.startswith(role)):
                    break
                nm.append(w.capitalize())
            ent["name"] = (_NAME_TITLES.get(ttl, "") + " " + " ".join(nm)).strip()
            info["name_from_ocr"] = True
    info["directory_sign"] = bool(directory and cands)
    return info


def _classify(sent: dict, words: list[dict], idx: int, n_sents: int, anc: dict) -> dict:
    ws = words[sent["w0"]:sent["w1"] + 1]
    sn = [w["n"] for w in ws]
    sset = set(sn)
    txt = strip_accents(sent["text"].lower())
    wi = {w["i"] for w in ws}
    dur = sent["t1"] - sent["t0"]
    sec, fx = "otro", "titulo"
    if "price" in anc and anc["price"][0] in wi:
        sec, fx = "precio", "tarjeta"
    elif ("address" in anc and anc["address"][0] in wi) or ("landmark" in anc and anc["landmark"][0] in wi) \
            or len(sset & _LOC_W) >= 2:
        sec, fx = "ubicacion", "tarjeta"
    elif sset & _CTA_W or ("schedule" in anc and anc["schedule"] in wi):
        sec, fx = "cta", "tarjeta"
    elif idx == 0:
        sec, fx = "hook", "titulo"
    elif any(p in txt for p in _HUMOR_PH):
        sec, fx = "cierre", "titulo"
    elif any(p in txt for p in _DRAMA_PH) or (len(ws) <= 4 and dur <= 2.0 and idx <= max(2, n_sents // 3)
                                             and re.search(r"[.!]$", ws[-1]["text"])):
        sec, fx = "problema", "dramatico"
    elif sset & _BENEFIT_W:
        sec, fx = "beneficio", "titulo"
    elif sset & _PROBLEM_W or "?" in sent["text"]:
        sec, fx = "problema", "titulo"
    elif idx == n_sents - 1:
        sec = "cierre"
    # enfasis: kw del analisis v1, numeros, palabras largas de contenido
    scored = []
    for w in ws:
        if w["n"] in STOP or len(w["n"]) < 3:
            if not re.fullmatch(r"\d+", w["n"]):
                continue
        sc = (3 if w["kw"] else 0) + min(len(w["n"]), 10) * 0.25
        if re.fullmatch(r"\d+", w["n"]):
            sc += 3
        if w["n"] in _BENEFIT_W | _PROBLEM_W | _PRICE_W:
            sc += 1.5
        if re.search(r"[?!]$", w["text"]):
            sc += 1.2
        scored.append((sc, w["i"]))
    scored.sort(key=lambda x: -x[0])
    emph = sorted(i for _, i in scored[:2 if len(ws) > 5 else 1])
    rate = len(ws) / max(0.3, dur)
    energy = "alta" if (rate > 3.3 or "!" in sent["text"] or "?" in sent["text"]) else ("baja" if rate < 2.2 else "media")
    tone = ("humor" if any(p in txt for p in _HUMOR_PH) else "intriga" if "?" in sent["text"]
            else "informativo" if sec in ("precio", "ubicacion") else "urgente" if sec == "cta"
            else "serio" if fx == "dramatico" else "alegre" if sec == "beneficio" else "informativo")
    icons = []
    for i in emph:
        ic = resolve_icon(words[i]["n"])
        if ic and ic not in icons and ic not in ICON_BLOCK:
            icons.append(ic)
    return {"id": sent["id"], "section": sec, "emph": emph, "fx": fx, "icons": icons,
            "energy": energy, "tone": tone, "title": None}


def resolve_icon(tag: str) -> str | None:
    """Palabra (ES/EN) -> icono Tabler existente, evitando coincidencias parciales absurdas."""
    t = norm(tag)
    if not t:
        return None
    for table in (ICON_ES_EXTRA, assets.ICON_ES, ICON_EN):
        n = table.get(t)
        if n and assets.icon_exists(n):
            return n
    if len(t) >= 4 and t.endswith("s") and ICON_EN.get(t[:-1]):
        n = ICON_EN[t[:-1]]
        if assets.icon_exists(n):
            return n
    if len(t) >= 4:
        for stem, n in ICON_STEMS:
            if t.startswith(stem) and assets.icon_exists(n):
                return n
    try:
        # palabra completa dentro del nombre del icono ("tooth" no debe dar "bluetooth")
        for n in assets.icon_search(t, 12):
            if t in n.split("-") and assets.icon_exists(n):
                return n
    except Exception:
        pass
    return None


def topic_icons(role: str) -> list[str]:
    return [n for n in TOPIC_ICONS.get(role or "", TOPIC_ICONS[""]) if assets.icon_exists(n)]


def tone_icon(tag: str, role: str = "", k: int = 0) -> str | None:
    """Icono con tono de anuncio: la traduccion literal si es neutra o del rubro; si se lee
    fuera de tono (emoji burlon, grafica bursatil...) un icono del set del rubro."""
    n = resolve_icon(tag)
    if n and n not in ICON_BLOCK:
        return n
    if n is None:
        return None
    pool = topic_icons(role) or TOPIC_ICONS[""]
    return pool[k % len(pool)]


_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "ent": {"type": "object", "properties": {k: {"type": "string"} for k in
                ("name", "role", "price", "address", "landmark", "schedule", "cta", "link_label", "handle")},
                "required": ["name", "role", "price", "address", "landmark", "schedule", "cta",
                             "link_label", "handle"]},
        "f": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"},
            "sec": {"type": "string", "enum": ["hook", "problema", "beneficio", "oferta", "precio",
                                               "ubicacion", "cta", "cierre", "otro"]},
            "title": {"type": "string"},
            "emph": {"type": "array", "items": {"type": "string"}},
            "fx": {"type": "string", "enum": ["titulo", "tarjeta", "icono", "garabato", "detras",
                                              "dramatico", "color_pop", "zoom", "escena", "ninguno"]},
            "icon": {"type": "array", "items": {"type": "string"}},
            "nrg": {"type": "string", "enum": ["baja", "media", "alta"]},
            "tone": {"type": "string", "enum": ["intriga", "serio", "alegre", "informativo",
                                                "urgente", "humor"]}},
            "required": ["id", "sec", "title", "emph", "fx", "icon", "nrg", "tone"]}},
    },
    "required": ["topic", "ent", "f"],
}
_LLM_SYS = ("Eres director creativo de videos verticales cortos (TikTok/Reels) en espanol. "
            "Analizas el guion hablado y decides la edicion. Responde SOLO JSON.")


def _llm_prompt(sents: list[dict], shots: list[dict] | None) -> str:
    lines = [f"{s['id']} | {s['t0']:.1f}-{s['t1']:.1f} | {s['text']}" for s in sents]
    sh = ""
    if shots:
        desc = []
        for k, s in enumerate(shots):
            d = f"{chr(65 + k % 26)} {s['start']:.1f}-{s['end']:.1f}: " + (
                ("persona a camara" if s.get("use", "a_camara") == "a_camara" else "persona")
                if s.get("person") else "sin persona (b-roll)")
            if s.get("framing") and s.get("person"):
                d += f", {s['framing'].replace('_', ' ')}"
            if s.get("place"):
                d += f", {s['place']}"
            if s.get("text_seen"):
                d += "; textos: " + ", ".join(s["text_seen"][:4])
            desc.append(d)
        sh = "\nTomas:\n" + "\n".join(desc) + "\n"
    return (
        "Frases del video (id | segundos | texto):\n" + "\n".join(lines) + "\n" + sh +
        "\nDevuelve:\n"
        "\"topic\": tema (2-4 palabras en espanol);\n"
        "\"ent\": {\"name\": nombre propio de quien habla SOLO si se dice en el audio o \"\", "
        "\"role\": profesion o negocio (1-2 palabras) o \"\", \"price\": precio dicho (\"$500 MXN\") o \"\", "
        "\"address\": direccion dicha (corta) o \"\", \"landmark\": referencia cercana dicha o \"\", "
        "\"schedule\": horario dicho o \"\", \"cta\": accion que pide (3-6 palabras), "
        "\"link_label\": texto de boton (2-3 palabras), \"handle\": usuario de redes dicho o \"\"};\n"
        "\"f\": una entrada por frase: {\"id\", \"sec\": seccion, \"title\": 1-4 palabras COPIADAS TAL CUAL "
        "de la frase para titulo grande, \"emph\": 1-2 palabras COPIADAS de la frase, \"fx\": tratamiento "
        "visual, \"icon\": 1-2 palabras en ingles para buscar icono, \"nrg\": energia, \"tone\": tono}.\n"
        "Reglas: solo UNA frase corta y punzante -> fx \"dramatico\". Precio, direccion, horario o link -> "
        "fx \"tarjeta\". La primera frase es \"hook\". Frase con chiste -> tone \"humor\".")


def _llm_script(sents: list[dict], shots: list[dict] | None, timeout: float = 55.0) -> dict | None:
    if not sents:
        return None
    sh_key = [(round(float(x["start"]), 1), round(float(x["end"]), 1), bool(x.get("person")),
               x.get("use", "")) for x in shots or []]
    key = _hkey([s["text"] for s in sents], sh_key, PROMPT_VERSION, _llm_model())
    hit = _cache_get("script", key)
    if hit:
        return hit
    try:
        raw = _ollama_chat([{"role": "system", "content": _LLM_SYS},
                            {"role": "user", "content": _llm_prompt(sents, shots)}],
                           fmt=_LLM_SCHEMA, num_ctx=8192, temperature=0.2,
                           num_predict=160 + 90 * len(sents), timeout=timeout)
    except Exception:
        return None
    d = _json_loose(raw)
    if not d or not isinstance(d.get("f"), list):
        return None
    _cache_put("script", key, d)
    return d


def _match_span(words: list[dict], w0: int, w1: int, phrase: str) -> tuple[int, int] | None:
    toks = [norm(t) for t in str(phrase).split() if norm(t)]
    if not toks:
        return None
    sn = [words[i]["n"] for i in range(w0, w1 + 1)]
    k = _find_phrase(sn, " ".join(toks))
    if k < 0:
        return None
    return w0 + k, w0 + k + len(toks) - 1


_TOPIC_BLOCK = {"barata", "barato", "baratas", "baratos", "economica", "economico", "cara", "caro",
                "mala", "malo", "peor", "gratis"}


def _merge_llm(script: dict, llm: dict, words: list[dict]) -> None:
    """Integra la respuesta del LLM validandola contra la transcripcion (in-place)."""
    by_id = {s["id"]: s for s in script["sents"]}
    ns_all = " ".join(w["n"] for w in words)
    strong = {"precio", "ubicacion", "cta"}
    dramas = []
    for f in llm.get("f") or []:
        try:
            s = by_id.get(int(f.get("id")))
        except (TypeError, ValueError):
            continue
        if not s:
            continue
        info = s["info"]
        sec = f.get("sec")
        if info["section"] not in strong and info["section"] != "hook" and sec in (
                "problema", "beneficio", "oferta", "cierre", "otro"):
            info["section"] = sec
        em = []
        for e in f.get("emph") or []:
            for tok in str(e).split():
                sp = _match_span(words, s["w0"], s["w1"], tok)
                if sp and words[sp[0]]["n"] not in STOP and sp[0] not in em:
                    em.append(sp[0])
        if em:
            heur = [i for i in info["emph"] if i not in em]
            info["emph"] = sorted(set(em[:2] + heur[:max(0, 2 - len(em[:2]))]))
        sp = _match_span(words, s["w0"], s["w1"], f.get("title") or "")
        if sp and sp[1] - sp[0] <= 5:
            info["title"] = list(sp)
        fx = f.get("fx")
        if fx == "dramatico":
            dramas.append(s)
        elif info["section"] not in strong and fx in ("titulo", "icono", "garabato", "detras",
                                                       "color_pop", "zoom", "escena"):
            info["fx"] = fx
        if f.get("nrg") in ("baja", "media", "alta"):
            info["energy"] = f["nrg"]
        if f.get("tone") in ("intriga", "serio", "alegre", "informativo", "urgente", "humor"):
            # el humor y la intriga de una pregunta (heuristicas seguras) no se sobrescriben
            if info["tone"] != "humor" and not (info["tone"] == "intriga" and "?" in s["text"]):
                info["tone"] = f["tone"]
        ics = list(info["icons"])
        for tag in f.get("icon") or []:
            ic = resolve_icon(str(tag))
            if ic and ic not in ics and ic not in ICON_BLOCK:
                ics.append(ic)
        info["icons"] = ics[:3]
    # a lo mas UN momento dramatico: el mas corto de los propuestos (si es corto de verdad)
    if dramas:
        cand = [s for s in dramas if s["info"]["section"] not in strong
                and (s["w1"] - s["w0"] + 1) <= 6 and s["id"] > 0]
        if cand:
            best = min(cand, key=lambda s: (s["w1"] - s["w0"], s["id"]))
            for s in script["sents"]:
                if s["info"]["fx"] == "dramatico" and s is not best:
                    s["info"]["fx"] = "titulo"
            best["info"]["fx"] = "dramatico"
    # entidades: solo lo verificable en la transcripcion
    le = llm.get("ent") or {}
    ent = script["entities"]
    nm = str(le.get("name") or "").strip()
    if nm and not ent.get("name"):
        toks = [norm(t) for t in nm.split() if norm(t) and norm(t) not in _NAME_TITLES]
        if toks and all(t in ns_all.split() for t in toks):
            ent["name"] = nm
    role = str(le.get("role") or "").strip()
    if role and not ent.get("role") and len(role.split()) <= 3:
        ent["role"] = _cap_first(role)
    if not ent.get("price") and le.get("price"):
        digits = re.sub(r"[^\d.]", "", str(le["price"]).replace(",", ""))
        try:
            val = float(digits)
            nums = []
            ns_list = [w["n"] for w in words]
            for i in range(len(ns_list)):
                v, n = parse_number(ns_list, i)
                if v:
                    nums.append(v)
            if val in nums:
                ent["price"] = fmt_money(val, "MXN")
        except ValueError:
            pass
    for k in ("cta", "link_label"):
        v = str(le.get(k) or "").strip()
        lim = 7 if k == "cta" else 3
        if v and ent.get(k) and 1 <= len(v.split()) <= lim and k == "link_label" and ent[k] == "Link abajo":
            ent[k] = _cap_first(v)
    h = str(le.get("handle") or "").strip()
    if h and not ent.get("handle") and norm(h) and norm(h) in ns_all.replace(" ", ""):
        ent["handle"] = h if h.startswith("@") else "@" + h
    t = str(llm.get("topic") or "").strip()
    # sin juicios de valor que no dijo la persona ("barata", "cara"...): van a la musica y a titulos
    t = " ".join(w for w in t.split() if norm(w) not in _TOPIC_BLOCK)
    if t and len(t.split()) <= 5:
        script["topic"] = t


def analyze_script(plan: dict, shots: list[dict] | None = None, use_llm: bool = True,
                   llm_result: dict | None = None) -> dict:
    """Guion estructurado: palabras, frases con seccion/enfasis/tratamiento/iconos, entidades."""
    words = _flatten_words(plan)
    sents = _sentences(words)
    ent, anc = extract_entities(words, sents, shots)
    script = {"words": words, "sents": sents, "entities": ent, "anchors": anc, "topic": "",
              "llm": False}
    for k, s in enumerate(sents):
        s["info"] = _classify(s, words, k, len(sents), anc)
    # un solo dramatico heuristico: el mas corto
    dr = [s for s in sents if s["info"]["fx"] == "dramatico"]
    for s in dr[1:]:
        s["info"]["fx"] = "titulo"
    llm = llm_result
    if llm is None and use_llm and sents and llm_available():
        llm = _llm_script(sents, shots)
    if llm:
        _merge_llm(script, llm, words)
        script["llm"] = True
    info = _ocr_confirm(ent, shots or [])
    script["ocr"] = info
    return script


# ---------------------------------------------------------------- zonas
def zone_of(track: str, item: dict) -> str | None:
    """Zona de pantalla que ocupa un item (para la regla 'nunca dos en la misma zona')."""
    if track == "cutaways":
        return "full"
    if track == "text":
        if item.get("preset") == "behind_giant":
            return "top"
        return _zone_y(float(item.get("y", 0.3)))
    if track == "graphics":
        k = item.get("kind")
        if k == "progress":
            return "edge"
        if k in ("doodle", "icon"):
            return "accent"
        return _zone_y(float(item.get("y", 0.3)))
    return None


ZONE_Y = {"top": 0.38, "center": 0.585, "low": 0.665}


def _zone_y(y: float) -> str:
    """Franja por la y del centro: top (sobre la cabeza) / center (pecho) / low (arriba de los
    subtitulos: CTA, rotulos) / bottom (subtitulos)."""
    return "top" if y < ZONE_Y["top"] else "center" if y < ZONE_Y["center"] else \
        "low" if y < ZONE_Y["low"] else "bottom"


ACCENT_SEP = 0.2


_MCTX: dict = {}


def _measure_ctx(style: str):
    c = _MCTX.get(style)
    if c is None:
        c = _MCTX[style] = FrameCtx(W=1080, H=1920, style=style)
    return c


def measure_text(item: dict, style: str = "dinamico") -> tuple[float, float, float, float] | None:
    """Tinta real del texto en reposo (x0, y0, x1, y1 normalizados) segun motion/text.py, o None
    si el modulo no esta. El director coloca con esto para no tapar la cara ni salir de SAFE."""
    try:
        from . import text as TX
        clean = {k: v for k, v in item.items() if not k.startswith("_")}
        r = TX.measure(clean, _measure_ctx(style))
        if r is None or r.isEmpty():
            return None
        return r.left() / 1080.0, r.top() / 1920.0, r.right() / 1080.0, r.bottom() / 1920.0
    except Exception:
        return None


_CARD_H_EST = {"location": 0.43, "price": 0.27, "schedule": 0.17, "link": 0.2, "phone": 0.2,
               "rating": 0.2, "checklist": 0.25, "stat": 0.22, "generic": 0.16}


def card_height(item: dict, style: str = "dinamico") -> float:
    """Alto real de una tarjeta (fraccion de H) segun motion/graphics.py (o estimado)."""
    try:
        from . import graphics as G
        r = G.card_layout(dict(item), _measure_ctx(style))
        return float(r[8]) / 1920.0
    except Exception:
        return _CARD_H_EST.get(item.get("variant", "generic"), 0.18) * float(item.get("size", 0.74)) * 1080 / 1920


def _text_w(font: str, size: float, text: str) -> float:
    """Ancho del texto como fraccion de W (fuente real, W=1080)."""
    try:
        f = assets.font(font, max(4.0, size * 1080.0))
        return f.measureText(text) / 1080.0
    except Exception:
        return len(text) * size * 0.55


# ---------------------------------------------------------------- color (WCAG)
def _rgb(c) -> tuple[float, float, float]:
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return float(c[0]), float(c[1]), float(c[2])
    s = str(c or "#000000").lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    try:
        return float(int(s[0:2], 16)), float(int(s[2:4], 16)), float(int(s[4:6], 16))
    except ValueError:
        return 0.0, 0.0, 0.0


def _hexc(rgb) -> str:
    return "#" + "".join(f"{int(round(_clamp(v, 0, 255))):02X}" for v in rgb[:3])


def mix_hex(a, b, p: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hexc([ra[i] + (rb[i] - ra[i]) * p for i in range(3)])


def rel_lum(c) -> float:
    """Luminancia relativa WCAG 2.x (sRGB linealizado)."""
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(c)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b) -> float:
    la, lb = rel_lum(a), rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hsv(c) -> tuple[float, float, float]:
    r, g, b = (v / 255.0 for v in _rgb(c))
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d < 1e-6:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / d) + 360) % 360
    elif mx == g:
        h = 60 * ((b - r) / d) + 120
    else:
        h = 60 * ((r - g) / d) + 240
    return h, (d / mx if mx > 0 else 0.0), mx


def hue_dist(a, b) -> float:
    ha, hb = _hsv(a)[0], _hsv(b)[0]
    d = abs(ha - hb) % 360
    return min(d, 360 - d)


def box_text_color(item: dict) -> str | None:
    """Color del texto que queda ENCIMA de la caja de un item de texto (como lo pinta text.py):
    highlight_marker -> `accent` si contrasta con el marcador; kicker -> automatico por
    luminancia de la pastilla; resto -> `color`."""
    box = item.get("box")
    if not box:
        return None
    p = item.get("preset")
    if p == "highlight_marker":
        for c in (item.get("accent"), item.get("color")):
            if c and abs(_luma(c) - _luma(box)) >= 0.4:
                return c
        return "#141414" if _luma(box) > 0.55 else "#FFFFFF"
    if p == "kicker":
        return "#141414" if _luma(box) > 0.55 else "#FFFFFF"
    return item.get("color") or ("#111111" if _luma(box) > 0.6 else "#FFFFFF")


def _luma(c) -> float:
    """Luma no lineal (misma formula que ctx.luminance, la que usa text.py para decidir)."""
    r, g, b = (v / 255 for v in _rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


CLAUSE_W = {"y", "e", "o", "u", "pero", "que", "como", "cuando", "donde", "porque", "si", "para",
            "aunque", "pues", "entonces", "ni", "mientras", "sino"}
_NOUN_SUFFIX = ("eza", "cion", "sion", "miento", "aje", "ura", "encia", "ancia", "ismo", "dad",
                "tura", "ado", "ada", "ia")
_VERB_END = ("ar", "er", "ir", "ando", "iendo", "amos", "emos", "imos", "an", "en")
STROKE_PRESETS = ("stack", "title_pop", "cascade", "highlight_marker", "kicker")


# ---------------------------------------------------------------- paso 3: coreografo
class _Choreo:
    """Coreografo determinista. Trabaja en tiempo original para emitir y en tiempo final para
    las reglas de ritmo y de no encimar."""

    MAXW = 0.76                     # ancho maximo de un bloque de texto (fraccion de W)
    XC = 0.5                        # centro horizontal de los bloques (0.12..0.88 con MAXW)

    def __init__(self, plan: dict, shots: list[dict], script: dict, style: str, intensity: float,
                 seed: int, fixed: dict | None = None, windows: list | None = None, rev: int = 0):
        self.plan = plan
        self.style = style if style in STYLES else "dinamico"
        self.S = STYLES[self.style]
        self.I = _clamp(float(intensity), 0.0, 1.0)
        self.seed = int(seed)
        self.rev = int(rev)
        self.rc = FrameCtx(seed=self.seed * 1000 + self.rev, style=self.style)
        self.pal = assets.palette(self.style)
        self.tl = _timeline(plan)
        self.D = float(self.tl.final_duration)
        self.aspect = 16 / 9
        dims = plan.get("dims") or [1080, 1920]
        try:
            self.aspect = float(dims[1]) / float(dims[0]) if dims[0] else 16 / 9
        except Exception:
            pass
        if self.aspect < 1.2:           # salida 9:16 siempre (el base pass reencuadra)
            self.aspect = 16 / 9
        self.words = script["words"]
        self.sents = script["sents"]
        self.ent = script["entities"]
        self.anc = script["anchors"]
        self.script = script
        self.role = self.ent.get("role", "")
        for w in self.words:
            w["f0"], w["f1"] = self.f(w["t0"]), self.f(w["t1"])
        self.shots = self._prep_shots(shots)
        self.fx = {k: [] for k in TRACKS}
        self.occ: dict[str, list] = {z: [] for z in ("top", "center", "low", "bottom", "accent",
                                                      "full", "edge")}
        self.acc_pos: dict[str, tuple[float, float]] = {}
        self.cam: list[tuple[float, float, float]] = []
        self.events: list[tuple[float, str]] = []
        self.sfx_req: list[dict] = []
        self.used: Counter = Counter()
        self.last: dict[str, Any] = {}
        self.ids: set[str] = set()
        self.windows = windows
        self.fixed = fixed or {k: [] for k in TRACKS}
        self.titled_words: set[int] = set()
        self.punches: list[float] = []
        self.drama_range = None
        self.music_stops: list[dict] = []
        self.music_swells: list[dict] = []
        self.drop_at = None
        self.K = self._type_kit()
        self.acc_light = self._accent_on_light()
        self.ink: list[tuple] = []          # (fa, fb, x0, y0, x1, y1, id) tinta de textos
        self.shown: set[str] = set()        # entidades ya mostradas (no repetir)
        self.trans_req: list[dict] = []
        self.reserved: list[tuple[float, float]] = []
        self.bg_fx: list[tuple[float, float, str]] = []
        self.broll_card: dict[int, dict] = {}
        self.sent_main = None
        self.n_cut_left = 0
        for tr, items in self.fixed.items():
            for it in items:
                self.ids.add(str(it.get("id")))
                if tr == "effects" and it.get("kind") == "zoom":
                    try:
                        self.cam.append((float(it["start"]), float(it["end"]),
                                         max(float(it.get("from", 1.0)), float(it.get("to", 1.0)))))
                    except (KeyError, TypeError, ValueError):
                        pass
                z = zone_of(tr, it)
                if tr in ("transitions", "sfx"):
                    fa = self.f(float(it.get("at", 0)))
                    if tr == "transitions":
                        self.events.append((fa, str(it.get("id"))))
                        self.trans_req.append({"at": float(it.get("at", 0)), "f": fa, "ctx": "fixed",
                                               "kind": it.get("kind")})
                    continue
                a, b = self.f(float(it["start"])), self.f(float(it["end"]))
                if z:
                    self.occ[z].append((a, b, str(it.get("id"))))
                    if z == "accent":
                        self.acc_pos[str(it.get("id"))] = (float(it.get("x", 0.5)), float(it.get("y", 0.5)))
                if tr == "text":
                    r = measure_text(it, self.style)
                    if r is not None:
                        self.ink.append((a, b, *self._pad_rect(it, r), str(it.get("id"))))
                self.events.append((a, str(it.get("id"))))

    # ---------------------------------------------------------- tiempo
    def f(self, t: float) -> float:
        return float(self.tl.remap_clamped(float(t)))

    def o(self, tf: float) -> float:
        return float(self.tl.inverse(float(tf)))

    def kept(self, t: float) -> bool:
        return self.tl.remap(float(t)) is not None

    def snap_start(self, t: float) -> float:
        """Si t cae en un corte, lo mueve al inicio del siguiente tramo conservado."""
        if self.kept(t):
            return t
        return self.o(self.f(t))

    def snap_end(self, t: float) -> float:
        if self.kept(t):
            return t
        tf = self.f(t)
        for a, b in self.tl.keeps:
            if abs(self.f(b) - tf) < 1e-6 and b <= t:
                return b
        return self.o(tf)

    # ---------------------------------------------------------- kit tipografico
    def _type_kit(self) -> dict:
        kit = dict(TYPE_KIT[self.style])
        base = {font_family(kit[k]) for k in ("display", "bold", "text")}
        extra = [k for k in ("serif", "script") if font_family(kit[k]) not in base]
        if len(base) + len({font_family(kit[k]) for k in extra}) <= 3:
            kit["accent"] = "both"
        else:
            humor = any(s["info"].get("tone") == "humor" for s in self.sents)
            want = humor and self.S["humor_preset"] == "script_accent" and self.rc.rand("kit:script") < 0.7
            kit["accent"] = "script" if want else "serif"
        return kit

    def preset_ok(self, p: str) -> bool:
        a = self.K["accent"]
        if p == "serif_elegant":
            return a in ("serif", "both")
        if p == "script_accent":
            return a in ("script", "both")
        return p in TEXT_PRESETS

    def sub_preset(self, p: str) -> str:
        """Preset permitido por el kit del video (sin pasar de 3 familias tipograficas)."""
        if self.preset_ok(p):
            return p
        if p == "script_accent":
            return "serif_elegant" if self.preset_ok("serif_elegant") else "title_pop"
        if p == "serif_elegant":
            return "cascade"
        return "title_pop"

    def font_for(self, preset: str) -> str:
        return self.K[PRESET_ROLE.get(preset, "bold")]

    def _accent_on_light(self) -> str:
        """Acento legible sobre fondos claros (>= 3:1): el acento secundario oscurecido."""
        pal = self.pal
        for c in (pal["accent2"], pal["warm"], pal["accent"]):
            for k in (0.0, 0.2, 0.35, 0.5, 0.65):
                cc = mix_hex(c, pal["dark"], k)
                if contrast(cc, "#EDEDED") >= 3.2:
                    return cc
        return pal["dark"]

    # ---------------------------------------------------------- tomas y huecos de pantalla
    def _prep_shots(self, shots: list[dict]) -> list[dict]:
        out = []
        for k, s in enumerate(shots):
            d = dict(s)
            d["k"] = k
            d["f0"], d["f1"] = self.f(s["start"]), self.f(s["end"])
            talk = s.get("use", "a_camara") == "a_camara"
            face = s.get("face") or (list(NOMINAL_FACE) if talk else None)
            d["face"] = face
            d["face_nominal"] = bool(talk and not s.get("face"))
            d["talk"] = talk
            z0 = 1.0
            if talk and not d["face_nominal"]:
                z0 = _clamp(self.S["zoom_face"] / max(0.03, face[3]), 1.0, 1.45)
            d["z0"] = z0
            zmax = z0 * (1.0 + self.S["jump_zoom"] * (0.6 + 0.4 * self.I)) * 1.03
            d["zmax"] = zmax
            d["face_x"] = face[0] if face else None
            d.update(self._geom(d, zmax))
            out.append(d)
        return out

    def _geom(self, shot: dict, z: float) -> dict:
        """Huecos libres (top/center/low) para un nivel de zoom anclado en la cara."""
        face = shot.get("face")
        if not face:
            return {"top": (SAFE["top"] + 0.02, 0.36), "center": (0.38, ZONE_Y["center"]),
                    "low": (ZONE_Y["center"], ZONE_Y["low"]), "head_top": None, "chin": None}
        cx, cy, fw, fh = face
        z = z * 1.08                          # margen para los punch-in
        head_top = cy - fh * z * 0.98
        chin = cy + fh * z * 0.62
        return {"top": (SAFE["top"] + 0.012, head_top - 0.02),
                "center": (chin + 0.03, ZONE_Y["center"]),
                "low": (max(ZONE_Y["center"], chin + 0.03), ZONE_Y["low"]),
                "head_top": head_top, "chin": chin}

    def zoom_level(self, shot: dict, t0: float, t1: float) -> float:
        lv = [z for a, b, z in self.cam if a < t1 and t0 < b]
        return max(lv) if lv else shot.get("z0", 1.0)

    def geom(self, shot: dict, t0: float, t1: float) -> dict:
        return self._geom(shot, self.zoom_level(shot, t0, t1))

    def face_rect(self, shot: dict, t0: float, t1: float) -> tuple | None:
        """Caja de la cabeza en el cuadro de salida (zoom anclado en la cara)."""
        face = shot.get("face")
        if not face or not shot.get("talk"):
            return None
        cx, cy, fw, fh = face
        z = self.zoom_level(shot, t0, t1) * 1.08
        return (cx - fw * z * 0.7, cy - fh * z * 1.05, cx + fw * z * 0.7, cy + fh * z * 0.8)

    def shot_at(self, t: float) -> dict:
        for s in self.shots:
            if s["start"] - 1e-6 <= t < s["end"]:
                return s
        return self.shots[-1] if self.shots else {"k": 0, "start": 0, "end": 1e9, "talk": True,
                                                   "top": (0.1, 0.3), "center": (0.4, 0.62),
                                                   "low": (0.585, 0.665),
                                                   "face_x": None, "face": None, "z0": 1.0,
                                                   "zmax": 1.0, "f0": 0, "f1": self.D,
                                                   "head_top": None, "chin": None}

    # ---------------------------------------------------------- fondo bajo el texto
    def _bg_at(self, shot: dict, y0: float, y1: float, t0: float, t1: float) -> dict | None:
        """Color medio del fondo en la franja [y0, y1] del cuadro de salida (None = desconocido).
        Usa el perfil por toma (shots[].rows) y el zoom anclado en la cara; si hay un
        reemplazo de fondo activo, manda el fondo sintetico."""
        fa, fb = self.f(t0), self.f(t1)
        for a, b, kind in self.bg_fx:
            if a < fb and fa < b and (min(b, fb) - max(a, fa)) >= 0.4 * max(1e-3, fb - fa):
                ht = self.geom(shot, t0, t1)["head_top"] if shot.get("talk") else None
                if ht is None or y1 <= ht + 0.01:
                    rgb = (236, 231, 222) if kind == "light" else (16, 16, 20)
                    return {"rgb": rgb, "L": rel_lum(rgb), "sat": 0.05, "src": "fx"}
                break       # el texto cae sobre la persona (centrada): manda su color real
        rows = shot.get("rows")
        if not rows:
            return None
        n = len(rows)
        z = self.zoom_level(shot, t0, t1) if shot.get("talk") else 1.08
        cy = shot["face"][1] if shot.get("face") and shot.get("talk") else 0.5
        sy0 = _clamp(cy + (y0 - cy) / z, 0.0, 1.0)
        sy1 = _clamp(cy + (y1 - cy) / z, 0.0, 1.0)
        i0, i1 = int(sy0 * n), max(int(sy0 * n) + 1, int(math.ceil(sy1 * n)))
        sel = rows[max(0, i0):min(n, i1)] or rows[min(n - 1, max(0, i0)):min(n, i0 + 1)]
        rgb = tuple(float(np.mean([r[c] for r in sel])) for c in range(3))
        h, sat, v = _hsv(rgb)
        return {"rgb": rgb, "L": rel_lum(rgb), "sat": sat, "src": "rows"}

    def _apply_ink(self, spec: dict, bg: dict | None) -> str:
        """Colores de tinta con contraste contra el fondo real. Devuelve el modo (light/dark)."""
        pal = self.pal
        preset = spec.get("preset")
        if bg is None:
            mode = "dark"
            ink, acc = "#FFFFFF", pal["accent"]
        elif contrast("#FFFFFF", bg["rgb"]) < 2.4:
            # fondo claro (marmol, pared, papel): tinta oscura y acento oscurecido
            mode = "light"
            ink, acc = pal["dark"], self.acc_light
            spec["shadow"] = 0.12
        else:
            mode = "dark"
            ink = "#FFFFFF"
            acc = "#FFFFFF"
            for c in (pal["accent"], pal["accent2"]):
                # un acento acromatico (blanco) no "compite" en tono con el fondo
                if contrast(c, bg["rgb"]) >= 3.0 and (bg["sat"] < 0.2 or _hsv(c)[1] < 0.15
                                                      or hue_dist(c, bg["rgb"]) >= 40):
                    acc = c
                    break
            if contrast("#FFFFFF", bg["rgb"]) < 4.5 or acc == "#FFFFFF":
                # fondo medio o con el mismo tono del acento: sombra fuerte (+ contorno en bold)
                spec["shadow"] = max(float(spec.get("shadow", 0.5)), 0.7)
                if preset in STROKE_PRESETS and not spec.get("box"):
                    spec["stroke"] = 6.0
        old_acc = spec.get("accent")
        if preset == "highlight_marker":
            box = pal["accent"] if _luma(pal["accent"]) < 0.9 else pal["dark"]
            on = pal["dark"] if contrast(pal["dark"], box) >= contrast("#FFFFFF", box) else "#FFFFFF"
            spec.update({"box": box, "accent": on, "color": ink})
        elif preset == "kicker":
            pill = pal["accent"] if (mode == "dark" and _luma(pal["accent"]) < 0.9) else pal["dark"]
            spec.update({"box": pill, "color": ink, "accent": acc})
        else:
            spec["color"] = ink
            spec["accent"] = acc
        for ln in spec.get("lines") or []:
            if ln.get("color") and ln["color"] == old_acc:
                ln["color"] = acc if preset != "highlight_marker" else ink
            elif ln.get("color"):
                ln["color"] = ink
        spec["_mode"] = mode
        return mode

    def doodle_color(self, t0: float, t1: float, x: float, y: float) -> str:
        shot = self.shot_at(t0)
        bg = self._bg_at(shot, y - 0.03, y + 0.03, t0, t1)
        if bg is not None and contrast("#FFFFFF", bg["rgb"]) < 2.4:
            return self.acc_light
        if bg is not None and contrast(self.pal["accent"], bg["rgb"]) < 2.2:
            return "#FFFFFF"
        return self.pal["accent"]

    # ---------------------------------------------------------- ocupacion
    def free(self, zone: str, fa: float, fb: float, pad: float = 0.0) -> bool:
        if zone == "center_low":
            return self.free("center", fa, fb, pad) and self.free("low", fa, fb, pad)
        zs = [zone] if zone != "accent" else []
        if zone in ("top", "center", "low", "accent"):
            zs.append("full")
        if zone == "full":
            zs = ["full", "top", "center", "low", "accent"]
        for z in zs:
            for a, b, _ in self.occ[z]:
                if fa < b + pad - 1e-6 and a < fb + pad - 1e-6:
                    return False
        if self.windows is not None:
            if not any(wa - 0.3 <= fa and fb <= wb + 0.6 for wa, wb in self.windows):
                return False
        return True

    def free_accent(self, x: float, y: float, fa: float, fb: float) -> bool:
        """Acentos (garabatos/iconos pequenos): no se enciman en el tiempo si estan a menos de
        ACCENT_SEP (distancia en unidades de W) ni caen sobre una escena o el CTA final."""
        if not self.free("accent", fa, fb):
            return False
        if any(a < fb and fa < b for a, b in self.reserved):
            return False
        for a, b, iid in self.occ["accent"]:
            if fa < b - 1e-6 and a < fb - 1e-6:
                px, py = self.acc_pos.get(iid, (0.5, 0.5))
                if math.hypot(px - x, (py - y) * self.aspect) < ACCENT_SEP:
                    return False
        return True

    def _pad_rect(self, it: dict, r: tuple) -> tuple:
        """Tinta + relleno de la caja (marcador/kicker) en coordenadas normalizadas."""
        x0, y0, x1, y1 = r
        if it.get("box") or it.get("preset") in ("highlight_marker", "kicker"):
            s = float(it.get("size", 0.06))
            x0, x1 = x0 - 0.35 * s, x1 + 0.35 * s
            y0, y1 = y0 - 0.25 * s / self.aspect, y1 + 0.25 * s / self.aspect
        return x0, y0, x1, y1

    def clear_of_ink(self, rect: tuple, fa: float, fb: float, skip: str | None = None,
                     margin: float = 0.008) -> bool:
        x0, y0, x1, y1 = rect
        for a, b, rx0, ry0, rx1, ry1, iid in self.ink:
            if iid == skip or not (a < fb - 1e-6 and fa < b - 1e-6):
                continue
            if x0 < rx1 + margin and rx0 - margin < x1 and y0 < ry1 + margin and ry0 - margin < y1:
                return False
        return True

    def accent_ok(self, x: float, y: float, sz: float, fa: float, fb: float, shot: dict,
                  t0: float, t1: float, skip: str | None = None, extra: float = 1.0) -> bool:
        hw, hh = sz / 2 * extra, sz / 2 / self.aspect * extra
        rect = (x - hw, y - hh, x + hw, y + hh)
        if rect[0] < SAFE["left"] - 0.01 or rect[2] > SAFE["right"] + 0.01 or \
                rect[1] < SAFE["top"] - 0.005 or rect[3] > SAFE["bottom"]:
            return False
        fr = self.face_rect(shot, t0, t1)
        if fr is not None and rect[0] < fr[2] and fr[0] < rect[2] and rect[1] < fr[3] and fr[1] < rect[3]:
            return False
        if not self.clear_of_ink(rect, fa, fb, skip):
            return False
        return self.free_accent(x, y, fa, fb)

    # ---------------------------------------------------------- variedad determinista
    def rand(self, key: str) -> float:
        return self.rc.rand(f"{key}:{self.used[key]}")

    def pick(self, key: str, options: list, avoid_last: bool = True, weights: list | None = None):
        opts = list(options)
        if not opts:
            return None
        last = self.last.get(key)
        if avoid_last and len(opts) > 1 and last in opts:
            idx = [i for i, o in enumerate(opts) if o != last]
            if idx:
                opts = [opts[i] for i in idx]
                if weights:
                    weights = [weights[i] for i in idx]
        r = self.rand(key)
        self.used[key] += 1
        if weights:
            tot = sum(weights)
            acc = 0.0
            for o, w in zip(opts, weights):
                acc += w / tot
                if r < acc:
                    self.last[key] = o
                    return o
            self.last[key] = opts[-1]
            return opts[-1]
        o = opts[int(r * len(opts)) % len(opts)]
        self.last[key] = o
        return o

    # ---------------------------------------------------------- emision
    def _id(self, track: str, role: str) -> str:
        base = f"{ID_PREFIX[track]}_{role}"
        n = 1
        while f"{base}_{n}" in self.ids:
            n += 1
        iid = f"{base}_{n}"
        self.ids.add(iid)
        return iid

    def add(self, track: str, role: str, fields: dict, zone: str | None = None,
            event: bool = True) -> dict | None:
        it = {"id": self._id(track, role)}
        if track in ("transitions", "sfx"):
            it.update({"enabled": True, "src": "ai", "locked": False})
            it.update(fields)
            it["at"] = _r(it["at"])
            fa = self.f(it["at"])
            if track == "transitions":
                self.events.append((fa, it["id"]))
        else:
            s, e = float(fields["start"]), float(fields["end"])
            s = self.snap_start(max(0.0, s))
            e = self.snap_end(min(e, self.tl.duration))
            fa, fb = self.f(s), self.f(e)
            if fb - fa < 0.08:
                self.ids.discard(it["id"])
                return None
            if self.windows is not None and zone is None:
                if not any(wa - 0.3 <= fa and fb <= wb + 0.6 for wa, wb in self.windows):
                    self.ids.discard(it["id"])
                    return None
            it.update({"start": _r(s), "end": _r(e), "enabled": True, "src": "ai", "locked": False,
                       "intensity": 1.0})
            it.update({k: v for k, v in fields.items() if k not in ("start", "end")})
            if zone is None:
                zone = zone_of(track, it)
            if zone == "accent":
                ok = self.free_accent(float(it.get("x", 0.5)), float(it.get("y", 0.5)), fa, fb)
            else:
                ok = not zone or zone in ("bottom", "edge") or self.free(zone, fa, fb)
            if not ok:
                self.ids.discard(it["id"])
                return None
            for zz in (["center", "low"] if zone == "center_low" else [zone] if zone else []):
                self.occ[zz].append((fa, fb, it["id"]))
            if zone == "accent":
                self.acc_pos[it["id"]] = (float(it.get("x", 0.5)), float(it.get("y", 0.5)))
            if event:
                self.events.append((fa, it["id"]))
        self.fx[track].append(it)
        return it

    def sfx(self, at: float, name: str, prio: int = 5, gain: float | None = None, pan: float = 0.0,
            src_id: str = "", vary: bool = False) -> None:
        if name not in SFX_NAMES or at is None:
            return
        g = SFX_GAIN.get(name, -6) if gain is None else gain
        if vary:
            key = f"sfxv:{src_id}:{name}"
            g += (self.rc.rand(key + "g") - 0.5) * 4.0
            pan = pan + (self.rc.rand(key + "p") - 0.5) * 0.5
        self.sfx_req.append({"at": float(at), "name": name, "prio": prio,
                             "gain": g + self.S["sfx_db"], "pan": _clamp(pan, -0.6, 0.6),
                             "src": src_id})

    def text_sfx(self, preset: str, t0: float, it: dict, prio: int, n_lines: int = 1,
                 line_ts: list | None = None) -> None:
        """SFX de entrada de un bloque: rota entre variantes del preset (+-2 dB, paneo) y marca
        cada linea nueva con un tic suave (ritmo sin saturar)."""
        opts = TEXT_SFX.get(preset, ["pop"])
        name = opts[self.used["tsfx:" + preset] % len(opts)]
        self.used["tsfx:" + preset] += 1
        self.sfx(t0 + 0.05, name, prio=prio, src_id=it["id"], vary=True)
        for k, t in enumerate(line_ts or []):
            soft = "tick" if k % 2 == 0 else "pop_soft"
            self.sfx(t, soft, prio=2, gain=SFX_GAIN[soft] - 5.0, src_id=it["id"] + f"l{k}", vary=True)

    # ---------------------------------------------------------- 1. camara base
    def camera(self) -> None:
        for s in self.shots:
            if s["f1"] - s["f0"] < 0.15:
                continue
            segs = []
            for a, b in self.tl.keeps:
                lo, hi = max(a, s["start"]), min(b, s["end"])
                if hi - lo > 0.05:
                    segs.append([lo, hi])
            merged: list[list[float]] = []
            for seg in segs:            # tramos diminutos se funden con el vecino
                if merged and (seg[1] - seg[0] < 0.35 or merged[-1][1] - merged[-1][0] < 0.35):
                    merged[-1][1] = seg[1]
                else:
                    merged.append(seg)
            fixed_cam = [(float(it["start"]), float(it["end"])) for it in self.fixed["effects"]
                         if it.get("kind") in ("zoom", "kenburns")]
            if fixed_cam:
                merged = [sg for sg in merged
                          if not any(min(sg[1], b) - max(sg[0], a) > 0.5 * (sg[1] - sg[0]) for a, b in fixed_cam)]
                if not merged:
                    continue
            if s["talk"]:
                jz = 1.0 + self.S["jump_zoom"] * (0.6 + 0.4 * self.I)
                push = 0.025 + 0.035 * self.I
                start_tight = self.rand(f"cam{s['k']}") < 0.35 and len(merged) > 1
                for k, (a, b) in enumerate(merged):
                    tight = (k % 2 == 1) != start_tight
                    lv = s["z0"] * (jz if tight else 1.0)
                    dur = self.f(b) - self.f(a)
                    to = lv * (1.0 + push * min(1.0, dur / 3.0))
                    self.add("effects", "cam", {"start": a, "end": b, "kind": "zoom",
                                                "from": _r(lv, 3), "to": _r(to, 3),
                                                "easing": "in_out_quad"}, event=k > 0)
                    self.cam.append((a, b, to))
            elif not fixed_cam or len(merged) == len(segs):
                d = 1 if self.rand(f"kb{s['k']}") < 0.5 else -1
                self.add("effects", "kb", {"start": s["start"], "end": s["end"], "kind": "kenburns",
                                           "from": 1.0, "to": _r(1.08 + 0.08 * self.I, 3),
                                           "pan": [_r(0.03 * d, 3), -0.015]}, event=False)

    # ---------------------------------------------------------- 2. transiciones
    def shot_changes(self) -> list[dict]:
        out = []
        for k in range(1, len(self.shots)):
            b = self.shots[k]["start"]
            at = self.snap_start(b)
            fa = self.f(at)
            if fa < 0.25 or fa > self.D - 0.25:
                continue
            if out and abs(fa - out[-1]["f"]) < 0.12:
                continue
            out.append({"at": at, "f": fa, "prev": self.shots[k - 1], "next": self.shots[k]})
        return out

    def request_transition(self, at: float, ctx: str = "scene", prefer: str | None = None,
                           dirn: str | None = None) -> bool:
        """Pide una transicion en `at` (tiempo original). El tipo se asigna al final en orden
        temporal, sin repetir ninguna de las dos anteriores."""
        fa = self.f(at)
        if fa < 0.25 or fa > self.D - 0.25:
            return False
        if any(abs(r["f"] - fa) < 0.3 for r in self.trans_req):
            return False
        if self.windows is not None and not any(wa - 0.3 <= fa <= wb + 0.3 for wa, wb in self.windows):
            return False
        self.trans_req.append({"at": at, "f": fa, "ctx": ctx, "prefer": prefer, "dir": dirn})
        self.events.append((fa, "trans"))
        return True

    def transitions(self) -> None:
        for ch in self.shot_changes():
            nxt, prv = ch["next"], ch["prev"]
            kinds = [k for k, _ in self.S["transitions"]]
            pref = None
            if not nxt["talk"] and "light_leak" in kinds:
                pref = "light_leak"
            elif not nxt["talk"] and "film_burn" in kinds:
                pref = "film_burn"
            elif not prv["talk"] and "flash" in kinds:
                pref = "flash"
            elif norm(prv.get("place", ""))[:6] != norm(nxt.get("place", ""))[:6] and "whip" in kinds \
                    and prv.get("place") and nxt.get("place"):
                pref = "whip"
            self.request_transition(ch["at"], "shot", pref)

    def assign_transitions(self) -> None:
        recent: list[str] = []
        for i, r in enumerate(sorted(self.trans_req, key=lambda r: r["f"])):
            if r["ctx"] == "fixed":
                recent = (recent + [r.get("kind")])[-2:]
                continue
            pool = self.S["transitions"] if r["ctx"] == "shot" else self.S["scene_trans"]
            pref = r.get("prefer")
            if pref and pref not in recent and pref in TRANS_DUR:
                kind = pref
            else:
                ok = [(k, w) for k, w in pool if k not in recent] or \
                     [(k, w) for k, w in self.S["transitions"] if k not in recent] or list(pool)
                kind = self.pick("trans", [k for k, _ in ok], weights=[w for _, w in ok], avoid_last=False)
            recent = (recent + [kind])[-2:]
            dur = TRANS_DUR[kind] * self.S["trans_dur"] * (0.85 if r["ctx"] == "scene" else 1.0)
            dirn = r.get("dir") or ("left" if i % 2 == 0 else "right")
            fields = {"at": r["at"], "kind": kind, "dur": _r(dur, 3), "dir": dirn,
                      "color": "#FFFFFF" if kind != "light_leak" else self.pal.get("warm", "#FF7A3D")}
            it = self.add("transitions", "cut" if r["ctx"] == "shot" else "scene", fields)
            if it and TRANS_SFX.get(kind):
                self.sfx(r["at"], TRANS_SFX[kind], prio=8, src_id=it["id"],
                         pan=-0.25 if dirn == "left" else 0.25)

    # ---------------------------------------------------------- bloques de texto
    def _chunks(self, w0: int, w1: int, emph: set, secondary: set | None = None) -> list[list[dict]]:
        """Lineas de 1-4 palabras: corta en puntuacion, pausas y cambios de toma, y prefiere
        cerrar una linea justo despues de la palabra de enfasis."""
        secondary = set(secondary or ()) - set(emph)
        ws = [w for w in self.words[w0:w1 + 1]]
        chunks, cur = [], []

        def best_break(c):
            best, bi = -99.0, len(c)
            for k in range(1, len(c) + 1):
                w = c[k - 1]
                sc = 0.0
                if re.search(r"[,;:.?!]$", w["text"]):
                    sc += 3
                if w["i"] in emph:
                    sc += 2
                elif w["i"] in secondary:
                    sc += 1
                if w["n"] in STOP:
                    sc -= 3
                if k < len(c) and c[k]["n"] in STOP:
                    sc += 1
                sc -= abs(k - 3) * 0.4
                if sc > best:
                    best, bi = sc, k
            return bi

        for w in ws:
            if cur:
                hard = (w["f0"] - cur[-1]["f1"] > 0.45
                        or self.shot_at(w["t0"])["k"] != self.shot_at(cur[0]["t0"])["k"]
                        or re.search(r"[.?!]$", cur[-1]["text"]) or re.match(r"^[¿¡]", w["text"]))
                if hard:
                    chunks.append(cur)
                    cur = []
            cur.append(w)
            if cur and (len(cur) >= 4 or cur[-1]["f1"] - cur[0]["f0"] > 1.5):
                k = best_break(cur)
                chunks.append(cur[:k])
                cur = cur[k:]
            elif ((len(cur) >= 2 and w["i"] in emph) or (len(cur) >= 3 and w["i"] in secondary)) \
                    and re.search(r"[,.?!]$", w["text"]) is None:
                nxt = self.words[w["i"] + 1] if w["i"] + 1 < len(self.words) else None
                if not (nxt and nxt["i"] <= w1 and nxt["n"] in STOP and len(cur) < 3):
                    chunks.append(cur)
                    cur = []
            elif len(cur) >= 2 and re.search(r"[,;:]$", w["text"]):
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        out: list[list[dict]] = []
        for c in chunks:
            if out and len(out[-1]) == 1 and out[-1][0]["n"] in STOP and \
                    self.shot_at(out[-1][0]["t0"])["k"] == self.shot_at(c[0]["t0"])["k"] and \
                    c[0]["f0"] - out[-1][0]["f1"] < 0.4:
                out[-1] = out[-1] + c
            else:
                out.append(c)
        return [c for c in out if c]

    def _can_join(self, a: list, b: list) -> bool:
        wa, wb = a[-1][-1], b[0][0]
        return (self.shot_at(wa["t0"])["k"] == self.shot_at(wb["t0"])["k"]
                and not re.search(r"[.?!]$", wa["text"]) and not re.match(r"^[¿¡]", wb["text"])
                and len(a) + len(b) <= 4 and sum(map(len, a)) + sum(map(len, b)) <= 10
                and wb["f0"] - wa["f1"] < 0.8)

    def _blocks(self, w0: int, w1: int, emph: set, secondary: set | None = None) -> list[list[list[dict]]]:
        """Bloques = clausulas habladas (como un editor): 1-3 lineas que se acumulan en pantalla
        al ritmo de la voz y se sostienen >= 0.9 s. Corta en fin de oracion, pregunta, pausa
        larga, cambio de toma o inicio de clausula (y/que/cuando...) tras >= 1 s de bloque."""
        chunks = self._chunks(w0, w1, emph, secondary)
        # una linea no termina en palabra funcional ("tu diente y"): pasa a la siguiente
        for k in range(len(chunks) - 1):
            c, nx = chunks[k], chunks[k + 1]
            while len(c) > 1 and c[-1]["n"] in STOP and not re.search(r"[,.;:?!]$", c[-1]["text"]) \
                    and nx[0]["f0"] - c[-1]["f1"] < 0.4 and \
                    self.shot_at(nx[0]["t0"])["k"] == self.shot_at(c[-1]["t0"])["k"]:
                nx.insert(0, c.pop())
        chunks = [c for c in chunks if c]
        blocks: list[list[list[dict]]] = []
        cur: list[list[dict]] = []
        for ch in chunks:
            if cur:
                last, first = cur[-1][-1], ch[0]
                gap = first["f0"] - last["f1"]
                dur_cur = last["f1"] - cur[0][0]["f0"]
                nw = sum(len(c) for c in cur)
                hard = (self.shot_at(first["t0"])["k"] != self.shot_at(cur[0][0]["t0"])["k"]
                        or re.search(r"[.?!…]$", last["text"]) or gap > 0.6
                        or re.match(r"^[¿¡]", first["text"]))
                full = len(cur) >= 3 or nw + len(ch) > 9 or (ch[-1]["f1"] - cur[0][0]["f0"]) > 3.4
                soft = (len(cur) >= 2 or dur_cur >= 1.2) and dur_cur >= 0.9 and (
                    first["n"] in CLAUSE_W or bool(re.search(r"[,;:]$", last["text"])))
                if hard or full or soft:
                    blocks.append(cur)
                    cur = []
            cur.append(ch)
        if cur:
            blocks.append(cur)
        out: list[list[list[dict]]] = []
        i = 0
        while i < len(blocks):
            b = blocks[i]
            span = b[-1][-1]["f1"] - b[0][0]["f0"] + 0.4
            if span < 0.9 and i + 1 < len(blocks) and self._can_join(b, blocks[i + 1]):
                blocks[i + 1] = b + blocks[i + 1]
                i += 1
                continue
            if span < 0.9 and out and self._can_join(out[-1], b):
                out[-1] = out[-1] + b
                i += 1
                continue
            out.append(b)
            i += 1
        return out

    def _fit_lines(self, lines: list[dict], size: float, max_w: float) -> float:
        """Reduce `size` hasta que la linea mas ancha quepa en max_w."""
        widest = 0.0
        for ln in lines:
            widest = max(widest, _text_w(ln["font"], size * ln.get("scale", 1.0), ln["text"]))
        if widest > max_w:
            size *= max_w / widest
        return size

    def _block_h(self, lines: list[dict], size: float) -> float:
        """Alto del bloque en fraccion de H."""
        return sum(size * ln.get("scale", 1.0) * 1.12 for ln in lines) / self.aspect

    def _rewrap(self, words: list[str], font: str, size: float, max_lines: int) -> list[str]:
        """Parte palabras en <= max_lines lineas balanceadas por ancho medido (sin terminar una
        linea en palabra funcional si se puede)."""
        n = len(words)
        if n <= 1 or max_lines <= 1:
            return [" ".join(words)]
        if _text_w(font, size, " ".join(words)) <= self.MAXW and n <= 3:
            return [" ".join(words)]
        import itertools
        best, cost_b = [" ".join(words)], 9e9
        for k in range(2, min(max_lines, n) + 1):
            for cuts in itertools.combinations(range(1, n), k - 1):
                parts = [words[a:b] for a, b in zip((0,) + cuts, cuts + (n,))]
                ws_ = [_text_w(font, size, " ".join(p)) for p in parts]
                cost = max(ws_) + (max(ws_) - min(ws_)) * 0.3 + 0.08 * k
                cost += sum(0.3 for p in parts[:-1] if norm(p[-1]) in STOP)
                if max(ws_) > self.MAXW:
                    cost += 5
                if cost < cost_b:
                    best, cost_b = [" ".join(p) for p in parts], cost
        return best

    def _hero_of(self, words: list[dict], emph: set) -> dict | None:
        em = [w for w in words if w["i"] in emph and w["n"] not in STOP]
        if not em:
            return None
        return max(em, key=lambda w: (len(w["n"]) <= 12, len(w["n"]), -w["i"]))

    def _text_spec(self, preset: str, blk: list[list[dict]], emph: set, humor: bool = False,
                   acc_words: set | None = None, compact: bool = False) -> dict:
        """Campos del item de texto (sin posicion) + lineas medidas para colocar. `emph` = palabra
        protagonista (stack); `acc_words` = palabras que ademas van en color de acento."""
        pal = self.pal
        words = [w for ln in blk for w in ln]
        acc_ids = set(emph) | set(acc_words or ())
        emw = [_display(w["text"]) for w in words if w["i"] in acc_ids]
        font = self.font_for(preset)
        small = self.K["text"]
        acc = pal["accent"]
        spec: dict[str, Any] = {"preset": preset, "align": "center", "color": "#FFFFFF",
                                "accent": acc, "shadow": 0.5, "anim_out": "fade", "font": font}
        lines: list[dict]
        if preset == "stack":
            hero = self._hero_of(words, emph) or max(words, key=lambda w: len(w["n"]))
            lines = []
            for ln in blk:
                if hero in ln:
                    k = ln.index(hero)
                    if k > 0:
                        lines.append({"text": " ".join(_display(w["text"]) for w in ln[:k]),
                                      "scale": 0.5, "font": small, "_small": True})
                    lines.append({"text": _display(hero["text"]).upper(), "scale": 1.0, "font": font,
                                  "color": acc})
                    if k + 1 < len(ln):
                        lines.append({"text": " ".join(_display(w["text"]) for w in ln[k + 1:]),
                                      "scale": 0.5, "font": small, "_small": True})
                else:
                    lines.append({"text": " ".join(_display(w["text"]) for w in ln), "scale": 0.5,
                                  "font": small, "_small": True})
            while len(lines) > 3:
                pairs = [(len(lines[j]["text"]) + len(lines[j + 1]["text"]), j) for j in range(len(lines) - 1)
                         if lines[j].get("_small") and lines[j + 1].get("_small")]
                if not pairs or (len(lines) == 4 and min(pairs)[0] > 17 and not compact):
                    break           # 4 lineas cortas se leen mejor que una linea chica larguisima
                _, j = min(pairs)
                lines[j] = dict(lines[j], text=lines[j]["text"] + " " + lines[j + 1]["text"])
                del lines[j + 1]
            size = self._fit_lines(lines, 0.19, self.MAXW)
            for ln in lines:
                ln.pop("_small", None)
            spec.update({"lines": lines, "size": size, "anim_in": "word_stagger",
                         "accent_words": [e for e in emw if norm(e) != hero["n"]]})
        elif preset == "cascade":
            lines = [{"text": " ".join(_display(w["text"]) for w in ln), "scale": 1.0, "font": font}
                     for ln in blk]
            while len(lines) > 3:
                j = min(range(len(lines) - 1), key=lambda j: len(lines[j]["text"]) + len(lines[j + 1]["text"]))
                lines[j] = dict(lines[j], text=lines[j]["text"] + " " + lines[j + 1]["text"])
                del lines[j + 1]
            if len(lines) > 1:
                lines[-1]["scale"] = 1.15
            size = self._fit_lines(lines, 0.082, 0.62)
            spec.update({"lines": lines, "size": size, "anim_in": "word_stagger", "align": "left",
                         "accent_words": emw})
        else:
            base = {"title_pop": 0.085, "serif_elegant": 0.1, "script_accent": 0.11,
                    "highlight_marker": 0.07, "kicker": 0.062, "typewriter": 0.058}.get(preset, 0.08)
            max_lines = 2 if preset == "script_accent" else 3
            # se respetan los cortes de la voz: una linea demasiado ancha se parte en dos
            # (balanceada) y solo si sobran lineas se reenvuelve todo el bloque
            texts = []
            for ln in blk:
                t_ = [_display(w["text"]) for w in ln]
                if _text_w(font, base, " ".join(t_)) > self.MAXW / 0.8 and len(t_) > 1:
                    texts += self._rewrap(t_, font, base, 2)
                else:
                    texts.append(" ".join(t_))
            if len(texts) > max_lines:
                texts = self._rewrap([_display(w["text"]) for w in words], font, base, max_lines)
            lines = [{"text": t, "scale": 1.0, "font": font} for t in texts]
            size = self._fit_lines(lines, base, self.MAXW)
            anim = {"title_pop": "word_stagger", "serif_elegant": "rise", "script_accent": "mask",
                    "highlight_marker": "pop", "kicker": "slide_left", "typewriter": "type"}[preset]
            spec.update({"text": "\n".join(texts), "size": size, "anim_in": anim, "accent_words": emw})
            if preset == "highlight_marker":
                spec.update({"accent_words": emw or [_display(words[-1]["text"])], "shadow": 0.3})
            elif preset == "kicker":
                spec.update({"shadow": 0.3})
            elif preset == "script_accent":
                spec["color"] = acc if humor else "#FFFFFF"
        spec["_lines"] = lines
        return spec

    def _place(self, spec: dict, g: dict, zones: list[str], fa: float, fb: float,
               min_size: float, first_bonus: float = 1.0) -> tuple[str, float, float] | None:
        """Elige zona libre y calcula (zona, y, size) para que el bloque quepa sin tapar la cara.
        Entre las zonas que caben gana la que deja el texto mas grande (la primera de la lista
        lleva un 15% de preferencia: continuidad con el bloque anterior)."""
        best = None
        for k, z in enumerate(zones):
            r = self._place_in(spec, g, z, fa, fb, min_size)
            if r is None:
                continue
            score = r[2] * ((1.15 * first_bonus) if k == 0 else 1.0)
            if best is None or score > best[0]:
                best = (score, r)
        return best[1] if best else None

    def _place_in(self, spec: dict, g: dict, z: str, fa: float, fb: float,
                  min_size: float) -> tuple[str, float, float] | None:
        lines = spec["_lines"]
        for z in (z,):
            if not self.free(z, fa, fb):
                continue
            lo, hi = (g["center"][0], g["low"][1]) if z == "center_low" else g[z]
            room = hi - lo
            if room < 0.035:
                continue
            size = float(spec["size"])
            h = self._block_h(lines, size)
            if h > room:
                size *= room / h
                h = room
            if size < min_size:
                continue
            if z == "top":
                y = (lo + hi) / 2 if room < 0.2 else hi - h / 2 - 0.01
            else:
                y = lo + h / 2 + 0.005
            ok = True
            for _ in range(5):          # ajuste fino con la tinta real del renderer de texto
                probe = {k: v for k, v in spec.items() if not k.startswith("_")}
                probe.update({"x": self._x_for(spec), "y": y, "size": size})
                r = measure_text(probe, self.style)
                if r is None:
                    break
                top, bot = r[1], r[3]
                hh = bot - top
                if hh > room + 1e-4:
                    size *= room / hh * 0.98
                    if size < min_size:
                        ok = False
                        break
                    continue
                if top < lo:
                    y += lo - top
                elif bot > hi:
                    y -= bot - hi
                else:
                    break
            if not ok:
                continue
            return z, y, size
        return None

    def _x_for(self, spec: dict) -> float:
        return SAFE["left"] + 0.04 if spec.get("align") == "left" else self.XC

    MIN_SIZE = {"kicker": 0.036, "stack": 0.07, "title_pop": 0.05, "serif_elegant": 0.055,
                "script_accent": 0.06, "highlight_marker": 0.045, "cascade": 0.045,
                "typewriter": 0.04}

    def title_block(self, blk: list[list[dict]], preset: str, emph: set, zones: list[str], t0: float,
                    t1: float, role: str, humor: bool = False, fallback: bool = True,
                    acc_words: set | None = None, first_bonus: float = 1.0) -> dict | None:
        ws = [w for ln in blk for w in ln]
        shot = self.shot_at(ws[0]["t0"])
        g = self.geom(shot, t0, t1)
        preset = self.sub_preset(preset)
        compact = first_bonus > 1.0
        if compact and g.get("head_top"):
            # sobre el fondo reemplazado el hueco llega casi a la cabeza (no hay nada detras)
            g = dict(g, top=(g["top"][0], g["top"][1] + 0.014))
        spec = self._text_spec(preset, blk, emph, humor, acc_words, compact)
        fa, fb = self.f(t0), self.f(t1)
        pl = self._place(spec, g, zones, fa, fb, self.MIN_SIZE.get(preset, 0.05), first_bonus)
        if pl is None and fallback and preset not in ("title_pop", "kicker"):
            spec = self._text_spec("title_pop", blk, emph, humor, acc_words)
            pl = self._place(spec, g, zones, fa, fb, 0.05, first_bonus)
        if pl is None:
            return None
        zone, y, size = pl
        lines = spec.pop("_lines")
        spec["size"] = _r(size, 4)
        x = self._x_for(spec)
        spec.update({"start": t0, "end": t1, "x": _r(x, 3), "y": _r(y, 4)})
        probe = {k: v for k, v in spec.items() if not k.startswith("_")}
        r = measure_text(probe, self.style)
        if r is None:
            bw = max(_text_w(ln["font"], size * ln.get("scale", 1.0), ln["text"]) for ln in lines)
            bh = self._block_h(lines, size)
            x0 = x if spec.get("align") == "left" else x - bw / 2
            r = (x0, y - bh / 2, x0 + bw, y + bh / 2)
        self._apply_ink(spec, self._bg_at(shot, r[1], r[3], t0, t1))
        mode = spec.pop("_mode", "dark")
        it = self.add("text", role, spec, zone=zone)
        if not it:
            return None
        for w in ws:
            self.titled_words.add(w["i"])
        pr = self._pad_rect(it, r)
        self.ink.append((fa, fb, *pr, it["id"]))
        it["_geom"] = (pr, zone, lines, mode)
        return it

    # ---------------------------------------------------------- acentos (garabatos / iconos)
    def _icon_for(self, ws: list[dict], emph: set, icons: list[str] | None) -> str | None:
        k = self.used["topic_icon"]
        for w in ws:
            if w["i"] in emph:
                ic = tone_icon(w["n"], self.role, k)
                if ic:
                    return ic
        for ic in icons or []:
            if ic not in ICON_BLOCK:
                return ic
        return None

    def _doodle_sfx(self, name: str, t: float, x: float, src: str) -> None:
        if name in ("sparkle", "star4", "star5", "burst", "rays"):
            self.sfx(t, "sparkle", prio=3, pan=(x - 0.5) * 0.8, src_id=src, vary=True)
        elif name in ("question", "exclaim", "heart", "check"):
            self.sfx(t, "pop_soft", prio=3, pan=(x - 0.5) * 0.8, src_id=src, vary=True)
        elif name == "lightning":
            self.sfx(t, "impact_soft", prio=3, pan=(x - 0.5) * 0.8, src_id=src, vary=True)
        else:        # trazos largos: subrayado, circulo, flechas, garabatos
            self.sfx(t, "pen_scribble", prio=3, pan=(x - 0.5) * 0.8, src_id=src, vary=True)

    def accent_for(self, it: dict, ws: list[dict], emph: set, key: str, t0: float, t1: float,
                   icons: list[str] | None = None, force: str | None = None,
                   icon: str | None = None) -> dict | None:
        """Garabato o icono junto a un titulo (zona 'accent'): dura >= 0.8 s, nunca toca la
        tinta de otro texto (con el relleno de su caja) ni la cabeza."""
        (x0, y0, x1, y1), zone, lines, mode = it["_geom"]
        fa, fb = self.f(t0), self.f(t1)
        if fb - fa < 0.8:
            return None
        shot = self.shot_at(ws[0]["t0"])
        icon = icon or self._icon_for(ws, emph, icons)
        choice = force
        if choice is None:
            q = "?" in " ".join(w["text"] for w in ws)
            if q and "question" in DOODLES and self.last.get("accent_kind") != "question":
                choice = "question"
            elif icon and self.last.get("accent_kind") != "icon" and self.rand(key + "ic") < 0.7:
                choice = "icon"
            else:
                # las flechas se reservan para senalar algo (CTA); junto a un titulo apuntarian
                # a la persona sin decir nada
                dd = [d_ for d_ in self.S["doodles"] if not d_.startswith("arrow")] or ["sparkle"]
                if it.get("box") or it.get("preset") in ("highlight_marker", "kicker") or len(lines) > 2:
                    dd = [d_ for d_ in dd if d_ not in ("underline", "circle_scribble")] or ["sparkle"]
                choice = self.pick("doodle", dd)
        face_x = shot.get("face_x")
        away = 1 if (face_x is None or face_x < 0.5) else -1       # lado contrario a la cara
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        acc_col = self.acc_light if mode == "light" else self.pal["accent"]
        if choice == "icon" and icon:
            if fb - fa < 0.9:
                return None
            sz = 0.12
            hh = sz / 2 / self.aspect
            cands = [(x1 + sz / 2 + 0.025, cy), (x0 - sz / 2 - 0.025, cy),
                     (x1 - sz * 0.15, y0 - hh - 0.016), (x0 + sz * 0.15, y0 - hh - 0.016),
                     (x1 - sz * 0.15, y1 + hh + 0.016)]
            if away < 0:
                cands = [cands[1], cands[0], cands[3], cands[2], cands[4]]
            for ix, iy in cands:
                if self.accent_ok(ix, iy, sz, fa, fb, shot, t0, t1):
                    bcol = self.pal["accent"] if mode == "dark" else self.pal["dark"]
                    fields = {"start": t0, "end": t1, "kind": "icon", "name": icon, "variant": "outline",
                              "badge": "circle", "badge_color": bcol,
                              "color": self.pal["dark"] if _luma(bcol) > 0.55 else "#FFFFFF",
                              "draw": True, "x": _r(ix, 3), "y": _r(iy, 4), "size": sz, "anim_in": "pop",
                              "anim_out": "pop_out", "rotation": _r((self.rand(key + "rot") - 0.5) * 14, 1)}
                    g = self.add("graphics", "icon", fields, zone="accent")
                    if g:
                        self.last["accent_kind"] = "icon"
                        self.used["topic_icon"] += 1
                        self.sfx(t0 + 0.02, "bubble" if self.used["icon_sfx"] % 2 else "pop", prio=4,
                                 pan=(ix - 0.5) * 0.8, src_id=g["id"], vary=True)
                        self.used["icon_sfx"] += 1
                    return g
            choice = self.pick("doodle", [d for d in self.S["doodles"]
                                          if d not in ("underline", "circle_scribble")] or ["sparkle"])
        name = choice if choice in DOODLES else "sparkle"
        sz = {"sparkle": 0.1, "star4": 0.09, "burst": 0.15, "question": 0.11, "cross": 0.1,
              "exclaim": 0.1, "lightning": 0.1, "star5": 0.09, "rays": 0.16, "swirl": 0.12,
              "loop": 0.13, "heart": 0.1, "check": 0.1, "squiggle": 0.14}.get(name, 0.12)
        fields = {"start": t0, "end": t1, "kind": "doodle", "name": name, "draw": True,
                  "wiggle": 0.35, "color": acc_col, "stroke": 7.0, "anim_in": "draw",
                  "anim_out": "fade", "rotation": 0.0}
        own = it["id"]
        if name == "underline":
            last = lines[-1]
            lw = min(x1 - x0, _text_w(last["font"], float(it["size"]) * last.get("scale", 1.0), last["text"]) * 1.05)
            uy = min(y1 + 0.01, SAFE["bottom"] - 0.02)
            ux = (x0 + x1) / 2 if it.get("align") != "left" else x0 + lw / 2
            rect = (ux - lw / 2, uy - 0.012, ux + lw / 2, uy + 0.012)
            if not (self.clear_of_ink(rect, fa, fb, skip=own) and self.free_accent(ux, uy, fa, fb)):
                return None
            fields.update({"x": _r(ux, 3), "y": _r(uy, 4), "size": _r(min(0.76, lw), 3)})
        elif name in ("arrow_curve", "arrow_straight"):
            ax = _clamp(x1 + 0.06 if away > 0 else x0 - 0.06, SAFE["left"] + 0.07, SAFE["right"] - 0.07)
            ay = _clamp(y1 + 0.045, SAFE["top"] + 0.04, SAFE["bottom"] - 0.08)
            if not self.accent_ok(ax, ay, 0.13, fa, fb, shot, t0, t1):
                return None
            tx = face_x if face_x is not None else 0.5
            ty = min(SAFE["bottom"] - 0.05, (self.geom(shot, t0, t1)["chin"] or 0.5) + 0.08)
            fields.update({"x": _r(ax, 3), "y": _r(ay, 4), "size": 0.13,
                           "path_to": [_r(_clamp(tx, 0.1, 0.85), 3), _r(ty, 3)]})
        else:
            if name == "circle_scribble":
                name = "sparkle"
                sz = 0.1
                fields["name"] = name
            hh = sz / 2 / self.aspect
            cands = [(x1 + sz * 0.3, y0 - hh * 0.35), (x0 - sz * 0.3, y0 - hh * 0.35),
                     (x1 + sz / 2 + 0.015, cy), (x0 - sz / 2 - 0.015, cy),
                     (x1 + sz * 0.2, y1 + hh * 0.5)]
            if away < 0:
                cands = [cands[1], cands[0], cands[3], cands[2], cands[4]]
            pos = next(((px, py) for px, py in cands if self.accent_ok(px, py, sz, fa, fb, shot, t0, t1)), None)
            if pos is None:
                return None
            fields.update({"x": _r(pos[0], 3), "y": _r(pos[1], 4), "size": sz,
                           "rotation": _r((self.rand(key + "r") - 0.5) * 24, 1)})
        g = self.add("graphics", "doodle", fields, zone="accent")
        if g:
            self.last["accent_kind"] = "question" if name == "question" else "doodle"
            self._doodle_sfx(name, t0 + 0.03, fields["x"], g["id"])
        return g

    def punch_at(self, t: float, amount: float, hold: float, role: str = "punch",
                 min_gap: float = 1.6) -> dict | None:
        ft = self.f(t)
        if any(abs(ft - p) < min_gap for p in self.punches):
            return None
        if any(a <= ft <= b for a, b, _ in self.occ["full"]):
            return None
        shot = self.shot_at(t)
        if not shot["talk"]:
            return None
        end = min(t + hold + 0.35, shot["end"] - 0.02)
        if end - t < 0.3:
            return None
        amount *= self.S["punch"]
        it = self.add("effects", role, {"start": t, "end": end, "kind": "punch",
                                        "amount": _r(_clamp(amount, 0.03, 0.4), 3),
                                        "hold": _r(hold, 2)})
        if it:
            self.punches.append(ft)
        return it

    # ---------------------------------------------------------- secuencia de titulos
    def _sentence_preset(self, humor: bool) -> str:
        if humor:
            return self.sub_preset(self.S["humor_preset"])
        agg: dict[str, float] = {}
        for p, w in self.S["presets"]:
            q = self.sub_preset(p)
            agg[q] = agg.get(q, 0.0) + w
        kinds = [k for k in agg if k != self.sent_main] or list(agg)
        p = self.pick("sent", kinds, weights=[agg[k] for k in kinds], avoid_last=False)
        return p

    def _companion(self, main: str) -> str:
        opts = [q for q in (self.sub_preset(p) for p in self.S["companion"]) if q != main] or \
               (["title_pop"] if main != "title_pop" else ["kicker"])
        c = opts[(self.used["comp"] + self.rev) % len(opts)]
        self.used["comp"] += 1
        return c

    def title_sequence(self, s: dict, w0: int, w1: int, role: str, zones_lead: list[str],
                       zones_hero: list[str], humor: bool = False, max_blocks: int = 99,
                       accents: bool = True, punches: bool = True, end_limit: float | None = None,
                       main: str | None = None, companion: str | None = None) -> int:
        """Titulos cineticos por CLAUSULA: cada bloque acumula 1-3 lineas que aparecen al
        ritmo de la voz y se sostiene >= 0.9 s. Una frase usa a lo mas 2 presets (principal
        en los bloques con enfasis, acompanante en el resto)."""
        info = s["info"]
        emph = set(info["emph"])
        if info.get("title"):
            a, b = info["title"]
            emph |= {a, b} if b - a <= 1 else {b}
        emph = {i for i in emph if w0 <= i <= w1}
        kw = {w["i"] for w in self.words[w0:w1 + 1] if w["kw"] and w["n"] not in STOP and len(w["n"]) >= 5}
        blocks = self._blocks(w0, w1, emph, kw)[:max_blocks]
        if not blocks:
            return 0
        flat = [w for b in blocks for ln in b for w in ln]
        if not any(w["i"] in emph for w in flat):
            cand = [w for w in flat if w["n"] not in STOP] or flat
            emph = emph | {max(cand, key=lambda w: len(w["n"]))["i"]}
        primary = set(emph)
        accent_set = emph | {w["i"] for w in flat if w["kw"] and w["n"] not in STOP and len(w["n"]) >= 5}
        main = self.sub_preset(main or self._sentence_preset(humor))
        comp = self.sub_preset(companion or self._companion(main))
        self.sent_main = main
        placed = 0
        home = None
        for bi, blk in enumerate(blocks):
            ws = [w for ln in blk for w in ln]
            hero = any(w["i"] in primary for w in ws)
            preset = main if (hero or len(blocks) == 1) else comp
            fa_, fb_ = ws[0]["f0"], ws[-1]["f1"]
            if any(min(b, fb_) - max(a, fa_) >= 0.5 * max(0.1, fb_ - fa_) for a, b, _ in self.bg_fx):
                # bloque sobre fondo reemplazado: es el momento protagonista (pila grande)
                preset = "stack" if main != "script_accent" else main
                if not hero:
                    cand = [w for w in ws if w["i"] in accent_set] or \
                           [w for w in ws if w["n"] not in STOP] or ws
                    primary.add(max(cand, key=lambda w: (w["kw"], len(w["n"])))["i"])
                    hero = True
            recent = self.last.get("tp_recent", [])
            if len(recent) >= 2 and recent[-1] == recent[-2] == preset:
                preset = comp if preset == main else main
            shot = self.shot_at(ws[0]["t0"])
            t0 = max(ws[0]["t0"] - 0.08, shot["start"] + 0.02)
            nxt = blocks[bi + 1][0][0]["t0"] if bi + 1 < len(blocks) else None
            lim = shot["end"] - 0.04
            if s["id"] + 1 < len(self.sents) and self.sents[s["id"] + 1]["t0"] > ws[-1]["t1"]:
                lim = min(lim, self.sents[s["id"] + 1]["t0"] - 0.04)
            if nxt is not None:
                lim = min(lim, nxt - 0.1)          # el siguiente bloque entra en nxt - 0.08
            if end_limit is not None:
                lim = min(lim, end_limit)
            t1 = min(ws[-1]["t1"] + (0.5 if nxt is None else 0.4), lim)
            if self.f(t1) - self.f(t0) < 0.9:
                t1 = min(lim, self.o(self.f(t0) + 0.95))
            if self.f(t1) - self.f(t0) < 0.8:
                continue
            big = preset in ("stack", "serif_elegant", "script_accent", "cascade") or len(blk) >= 3
            zones = list(zones_hero if big else zones_lead)
            if home and home in zones:
                zones.remove(home)
                zones.insert(0, home)
            top_bonus = 1.0
            if any(min(b, fb_) - max(a, fa_) >= 0.5 * max(0.1, fb_ - fa_) for a, b, _ in self.bg_fx):
                # sobre el fondo nuevo (arriba de la cabeza) el titulo luce el reemplazo
                zones = ["top"] + [z_ for z_ in zones if z_ != "top"]
                top_bonus = 1.9
            hero_set = {w["i"] for w in ws if w["i"] in primary} or {w["i"] for w in ws if w["i"] in accent_set}
            it = self.title_block(blk, preset, hero_set, zones, t0, t1, role, humor=humor,
                                  acc_words={w["i"] for w in ws if w["i"] in accent_set},
                                  first_bonus=top_bonus)
            if not it:
                continue
            self.last["tp_recent"] = (recent + [it["preset"]])[-2:]
            home = it["_geom"][1]
            placed += 1
            line_ts = []
            for ln in blk[1:]:
                line_ts.append(ln[0]["t0"])
            self.text_sfx(it["preset"], t0, it, prio=5 if hero else 3, line_ts=line_ts)
            if hero:
                hw = next((w for w in ws if w["i"] in primary), ws[-1])
                p_acc = _lerp(*self.S["accent_p"], self.I)
                if accents and self.rand(f"acc{hw['i']}") < p_acc:
                    self.accent_for(it, ws, accent_set, f"a{hw['i']}", max(t0, hw["t0"] - 0.05), t1,
                                    info.get("icons"))
                if punches and (info["energy"] != "baja" or self.I > 0.8):
                    self.punch_at(hw["t0"], 0.07 + 0.07 * self.I, 0.35)
        return placed

    def riser(self) -> None:
        """Riser que culmina en el cambio mas grande: la entrada a un b-roll mudo (revelacion) o,
        si no hay, la primera escena a pantalla completa."""
        target = None
        for ch in self.shot_changes():
            if not ch["next"]["talk"] and ch["f"] > 2.0:
                target = ch["at"]
                break
        if target is None and self.fx["cutaways"]:
            c0 = min(self.fx["cutaways"], key=lambda c: c["start"])
            if self.f(c0["start"]) > 2.0:
                target = c0["start"]
        if target is not None and self.I >= 0.4:
            self.sfx(target, "riser_short", prio=7)

    # ---------------------------------------------------------- 3. hook
    def hook(self) -> None:
        if not self.sents:
            return
        s = self.sents[0]
        w1 = s["w1"]
        # hook visual en los primeros segundos: como mucho ~4.5 s de titulos
        while w1 > s["w0"] and self.words[w1]["f1"] - self.words[s["w0"]]["f0"] > 4.5:
            w1 -= 1
        first = self.words[s["w0"]]
        self.punch_at(first["t0"] + 0.02, 0.09 + 0.05 * self.I, 0.45, role="hook_punch", min_gap=0.8)
        main = self.sub_preset(self.pick("hook", self.S["hook_presets"]))
        n = self.title_sequence(s, s["w0"], w1, "hook", ["top", "center"], ["center", "center_low", "top"],
                                main=main)
        if n == 0:
            ws = self.words[s["w0"]:min(w1, s["w0"] + 3) + 1]
            self.title_block([ws], "title_pop", set(s["info"]["emph"]), ["center", "top"],
                             first["t0"] - 0.05, ws[-1]["t1"] + 0.5, "hook")
        # whoosh de entrada
        self.sfx(max(0.0, first["t0"] - 0.05), "whoosh_fast", prio=9)
        if self.S["scenes"]["outline"]:
            sh = self.shot_at(first["t0"])
            if sh["talk"] and not sh.get("face_nominal"):
                it = self.add("effects", "hook_outline", {
                    "start": first["t0"], "end": min(first["t0"] + 1.4, sh["end"] - 0.05),
                    "kind": "subject_outline", "color": "#FFFFFF", "width": 7.0, "glow": 0.6})
                if it:
                    self.sfx(first["t0"] + 0.05, "shutter", prio=4, src_id=it["id"])
        s["info"]["_done"] = True

    # ---------------------------------------------------------- 4. momento dramatico
    def drama(self) -> None:
        cand = [s for s in self.sents if s["info"]["fx"] == "dramatico" and not s["info"].get("_done")]
        if not cand:
            return
        s = cand[0]
        ws = self.words[s["w0"]:s["w1"] + 1]
        shot = self.shot_at(ws[0]["t0"])
        D = self.S["drama"]
        t0 = ws[0]["t0"] - 0.06
        nxt = self.sents[s["id"] + 1]["t0"] if s["id"] + 1 < len(self.sents) else self.tl.duration
        end = min(ws[-1]["t1"] + 0.45, shot["end"] - 0.02, nxt - 0.05)
        if self.f(end) - self.f(t0) < 0.5:
            return
        # el golpe se sostiene >= 0.95 s aunque la siguiente frase empiece enseguida
        end = max(end, min(shot["end"] - 0.02, self.o(self.f(t0) + 0.95)))
        self.drama_range = (t0, end)
        if D["effect"] and shot["talk"]:
            self.add("effects", "drama", {"start": t0, "end": end, "kind": "bw",
                                          "contrast": D["contrast"], "subject_color": False})
        if D.get("flash"):
            fa0 = self.snap_start(max(t0, ws[0]["t0"] - 0.02))
            self.add("effects", "drama_flash", {"start": fa0, "end": self.o(self.f(fa0) + 0.28),
                                                "kind": "flash_frame", "color": "#FFFFFF"}, event=False)
        if shot["talk"]:
            self.punch_at(ws[0]["t0"], 0.16 + 0.06 * self.I, max(0.4, ws[-1]["t1"] - ws[0]["t0"]),
                          role="drama_punch", min_gap=0.5)
        if D["shake"] and shot["talk"]:
            self.add("effects", "drama_shake", {"start": ws[0]["t0"], "end": ws[0]["t0"] + 0.45,
                                                "kind": "shake", "amp": 9.0, "freq": 11.0, "decay": 0.7})
        if D["rgb"]:
            self.add("effects", "drama_rgb", {"start": ws[0]["t0"], "end": ws[0]["t0"] + 0.3,
                                              "kind": "rgb_split", "amount": 10.0})
        # texto: gigante detras del sujeto si la toma lo permite; si no, serif centrado
        emph = set(s["info"]["emph"]) or {max(ws, key=lambda w: len(w["n"]))["i"]}
        g = self._geom(shot, self.zoom_level(shot, t0, end))
        giant_ok = (D["giant"] and shot["talk"] and not shot.get("face_nominal")
                    and shot["face_x"] is not None
                    and 0.25 <= shot["face_x"] <= 0.75 and (g["head_top"] or 0) >= 0.17)
        placed = None
        if giant_ok and self.free("top", self.f(t0), self.f(end)):
            content = [w for w in ws if w["n"] not in STOP and len(w["n"]) >= 3] or ws
            short = [w for w in content if len(w["n"]) <= 8]
            hero = (short[-1:] or [min(content, key=lambda w: len(w["n"]))])
            h = hero[0]
            before = [w for w in ws if w["i"] < h["i"]]
            after = [w for w in ws if w["i"] > h["i"]]
            font = self.K["display"]
            lines = []
            if before:
                lines.append({"text": " ".join(_display(w["text"]) for w in before).upper(),
                              "scale": 0.3, "font": font})
            lines.append({"text": _display(h["text"]).upper(), "scale": 1.0, "font": font})
            if after:
                lines.append({"text": " ".join(_display(w["text"]) for w in after).upper(),
                              "scale": 0.3, "font": font})
            size = self._fit_lines(lines, 0.34, self.MAXW)
            # la cabeza tapa ~35% inferior de la palabra gigante: se lee y se siente "detras"
            for _ in range(12):
                sh = sum(size * ln["scale"] * 1.12 for ln in lines[:1] if before) / self.aspect
                gh = size * 1.12 / self.aspect
                ah = sum(size * ln["scale"] * 1.12 for ln in lines[-1:] if after) / self.aspect
                g_center = g["head_top"] - 0.15 * gh
                top_ = g_center - gh / 2 - sh
                if top_ >= SAFE["top"] - 0.005 or size < 0.2:
                    break
                size *= 0.95
            top_ = max(SAFE["top"], top_)
            bh = sh + gh + ah
            y = top_ + bh / 2
            placed = self.add("text", "drama", {
                "start": t0, "end": end, "preset": "behind_giant", "lines": lines,
                "x": 0.5, "y": _r(y, 4), "size": _r(size, 4), "font": font,
                "color": "#FFFFFF", "accent": self.pal["accent"], "align": "center",
                "behind": True, "anim_in": self.pick("drama_anim", ["scale_down", "letter_stagger"]),
                "anim_out": "fade", "shadow": 0.2, "accent_words": []}, zone="top")
            if placed:
                r = measure_text(placed, self.style)
                if r is not None:
                    self.ink.append((self.f(t0), self.f(end), *r, placed["id"]))
        if not placed:
            self.title_block([ws], "serif_elegant", emph, ["center", "top"], t0, end, "drama")
        for w in ws:
            self.titled_words.add(w["i"])
        # audio: la musica se corta en seco, golpe grave y regresa con la siguiente toma
        self.sfx(t0 - 0.1, "record_stop", prio=7)
        self.sfx(ws[0]["t0"], D["sfx"], prio=10)
        back = min(shot["end"], ws[-1]["t1"] + 0.6)
        self.music_stops.append({"at": _r(t0 - 0.12), "dur": _r(max(0.4, min(2.2, back - t0)), 3)})
        self.drop_at = _r(back)
        s["info"]["_done"] = True

    # ---------------------------------------------------------- 5. datos (tarjetas / escenas)
    def _sent_of(self, wi: int) -> dict:
        return self.sents[self.words[wi]["s"]]

    def _service(self, s: dict) -> str:
        """Servicio/producto del que se habla antes del precio (palabra de enfasis sustantiva)."""
        for k in range(s["id"], max(-1, s["id"] - 3), -1):
            sk = self.sents[k]
            cands = [self.words[i] for i in sorted(set(sk["info"]["emph"]) | {
                w["i"] for w in self.words[sk["w0"]:sk["w1"] + 1] if w["kw"]})]
            for w in cands:
                n = w["n"]
                if len(n) >= 5 and n not in STOP and not re.fullmatch(r"\d+", n) and \
                        n.endswith(_NOUN_SUFFIX) and not n.endswith(_VERB_END[:3]) and n not in _PRICE_W:
                    return _display(w["text"]).strip("¿?¡!").upper()
        t = str(self.script.get("topic") or "").strip()
        if t and len(t.split()) <= 2:
            return t.upper()
        return ""

    def _shot_change_before(self, t: float, within: float) -> float | None:
        ft = self.f(t)
        for ch in self.shot_changes():
            if 0.0 <= ft - ch["f"] <= within:
                return ch["at"]
        return None

    def scene_transitions(self, t0: float, t1: float) -> None:
        """Transicion de entrada y salida de una escena (sin repetir las dos anteriores)."""
        self.request_transition(t0, "scene")
        self.request_transition(t1, "scene")

    def _broll_after(self, s: dict) -> dict | None:
        """B-roll mudo que empieza a < 6 s del final de la frase `s` (para mostrar ahi el dato)."""
        end_f = self.f(s["t1"])
        for sh in self.shots:
            if sh["talk"] or sh["f1"] - sh["f0"] < 1.8:
                continue
            if not (-1.0 <= sh["f0"] - end_f <= 6.0):
                continue
            spoken = sum(w["t1"] - w["t0"] for w in self.words if sh["start"] <= w["t0"] < sh["end"])
            if spoken <= 0.35 * (sh["end"] - sh["start"]):
                return sh
        return None

    def data(self) -> None:
        a = self.anc
        n_cut = int(round(self.S["cutaways"] * self.I * max(1.0, self.D / 25.0)))
        self.n_cut_left = n_cut
        # precio
        if "price" in a:
            i0, i1 = a["price"]
            s = self._sent_of(i0)
            shot = self.shot_at(self.words[i0]["t0"])
            nxt = self.sents[s["id"] + 1]["t0"] if s["id"] + 1 < len(self.sents) else self.tl.duration
            vis0 = self.snap_start(max(s["t0"] - 0.05, shot["start"] + 0.05))
            t0 = max(self.o(self.f(vis0) + 0.55), self.words[i0]["t0"] - 0.5)
            t0 = min(t0, self.words[i0]["t0"] - 0.15)
            snapped = self._shot_change_before(t0, 0.7)
            if snapped is not None:
                t0 = snapped            # la escena entra con la transicion del cambio de toma
            end = min(self.words[i1]["t1"] + 0.7, shot["end"] - 0.03, nxt - 0.05)
            val = float(a.get("price_value") or 0)
            cur = (self.ent.get("price", "").split() or ["", "MXN"])[-1]
            service = self._service(s)
            done = False
            if n_cut > 0 and val > 0 and self.f(end) - self.f(t0) >= 1.1 and self.free("full", self.f(t0), self.f(end)):
                bg = self.pick("bg", [b for b in self.S["backgrounds"] if b not in ("blur_self",) and b not in LIGHT_BG]
                               or ["gradient"])
                amount = f"{int(val):,}" if val == int(val) else f"{val:,.2f}"
                font = self.K["display"]
                size = min(0.24, 0.72 / max(1.0, _text_w(font, 1.0, "$" + amount)))
                ny = 0.47
                gr = [{"kind": "doodle", "name": "sparkle", "x": 0.8, "y": _r(ny - size * 0.62 / self.aspect, 4),
                       "size": 0.1, "color": self.pal["accent"], "draw": True, "anim_in": "draw"},
                      {"kind": "doodle", "name": "star4", "x": 0.19, "y": _r(ny + size * 0.35 / self.aspect, 4),
                       "size": 0.07, "color": "#FFFFFF", "draw": True, "anim_in": "draw"}]
                if service:
                    gr.insert(0, {"kind": "sticker_text", "text": service, "x": 0.5,
                                  "y": _r(ny - size * 0.5 / self.aspect - 0.06, 4), "size": 0.055,
                                  "bg": self.pal["accent"] if _luma(self.pal["accent"]) < 0.9 else "#FFFFFF",
                                  "fg": self.pal["dark"], "anim_in": "pop", "rotation": -3.0})
                cw = self.add("cutaways", "price", {
                    "start": t0, "end": end, "kind": "title_card", "background": bg,
                    "bg_colors": [self.pal["dark"], self.pal["accent2"]],
                    # `text` del contador = etiqueta bajo el numero (render oculta subtitulos)
                    "text": {"preset": "counter", "from": 0, "to": int(val) if val == int(val) else val,
                             "prefix": "$", "suffix": "", "decimals": 0 if val == int(val) else 2,
                             "text": cur, "x": 0.5, "y": ny, "size": _r(size, 4), "font": font,
                             "color": "#FFFFFF", "accent": self.pal["accent"], "align": "center",
                             "anim_in": "pop", "shadow": 0.4},
                    "graphics": gr}, zone="full")
                if cw:
                    done = True
                    self.n_cut_left -= 1
                    if snapped is None:
                        self.request_transition(t0, "scene")
                    self.request_transition(end, "scene")
                    self.sfx(self.words[i0]["t0"] + 0.12, "cash", prio=9, src_id=cw["id"])
                    if service:
                        self.sfx(t0 + 0.1, "pop", prio=5, src_id=cw["id"] + "s", vary=True)
                    self.music_swells.append({"start": _r(t0), "end": _r(end), "db": 2.5})
                    for w in self.words[s["w0"]:s["w1"] + 1]:
                        self.titled_words.add(w["i"])
            if not done:
                # servicio como titulo, "Precio" como subtitulo y la moneda junto al valor
                self.card("price", i0, max(t0, self.words[i0]["t0"] - 0.35), max(end, self.words[i0]["t0"] + 2.2),
                          title=_cap_first(service.lower()) if service else "Precio",
                          value=self.ent.get("price", "") or "", subtitle="Precio" if service else "",
                          icon="cash", theme="brand", sfx_name="cash")
            self.shown.add("price")
            s["info"]["_done"] = True
        # ubicacion
        if "address" in a or "landmark" in a:
            i0 = a["address"][0] if "address" in a else a["landmark"][0]
            s = self._sent_of(i0)
            shot = self.shot_at(self.words[i0]["t0"])
            title = self.ent.get("address") or self.ent.get("landmark")
            sub = self.ent.get("landmark") if self.ent.get("address") else ""
            br = self._broll_after(s)
            if br is not None:
                # el dato completo (tarjeta con mapa) va sobre el b-roll de la fachada; mientras
                # habla, un solo titulo protagonista con pin y la referencia como titulo
                self.broll_card[br["k"]] = {"variant": "location", "title": title, "subtitle": sub,
                                            "icon": "map-pin"}
                if "address" in a:
                    self._address_hero(s)
                if "landmark" in a:
                    la, lb = a["landmark"]
                    ws_ = [w for w in self.words[la:lb + 1] if w["i"] not in self.titled_words]
                    if ws_:
                        self.title_sequence(self._sent_of(la), ws_[0]["i"], lb, "place",
                                            ["center", "top"], ["center", "center_low", "top"],
                                            main="stack", companion="title_pop")
                self.shown |= {"address", "landmark"}
            else:
                t0 = self.words[i0]["t0"] - 0.12
                last_i = max(a.get("address", [i0, i0])[1], a.get("landmark", [i0, i0])[1])
                end = min(shot["end"] - 0.06, max(self.words[last_i]["t1"] + 0.8, t0 + 3.0))
                cut_end = None
                if "landmark" in a and "address" in a and a["landmark"][0] > a["address"][1]:
                    cut_end = self.words[a["landmark"][0]]["t0"] - 0.08
                card = self.card("location", i0, t0, end, title=title, subtitle=sub, icon="map-pin",
                                 theme="light", sfx_name="notification", cut_end=cut_end)
                if card is not None:
                    self.shown |= {"address", "landmark"} if sub or not self.ent.get("address") else {"address"}
                covered = card is not None and self.f(card["end"]) >= self.words[last_i]["f1"] - 0.2
                if covered and "landmark" in a:
                    for i in range(a["landmark"][0], a["landmark"][1] + 1):
                        self.titled_words.add(i)
                elif card is not None and "landmark" in a:
                    la, lb = a["landmark"]
                    ws_ = [w for w in self.words[la:lb + 1] if w["f0"] >= self.f(card["end"]) - 0.1]
                    if ws_:
                        self.title_sequence(self._sent_of(la), ws_[0]["i"], lb, "place",
                                            ["center", "top"], ["center", "top"], main="stack",
                                            companion="title_pop")
            s["info"]["_done"] = True
            if "landmark" in a:
                self._sent_of(a["landmark"][0])["info"]["_done"] = True
        # horario + link (CTA)
        link_i = a.get("link")
        sched_i = a.get("schedule")
        if sched_i is not None:
            s = self._sent_of(sched_i)
            shot = self.shot_at(self.words[sched_i]["t0"])
            t0 = self.words[sched_i]["t0"] - 0.1
            end = min(shot["end"] - 0.05, s["t1"] + 0.4)
            if link_i is not None and self._sent_of(link_i)["id"] == s["id"]:
                end = min(end, self.words[link_i]["t0"] - 0.12)
            end = max(end, t0 + 1.3)
            hw = [w for w in self.words[sched_i:min(s["w1"], sched_i + 1) + 1]]
            title = _cap_first(" ".join(_display(w["text"]) for w in hw))
            self.card("schedule", sched_i, t0, end, title=title,
                      subtitle=self.ent.get("schedule") or ("Agenda en el link" if link_i is not None else ""),
                      icon="calendar-event", theme="light", sfx_name="ding")
            s["info"]["_done"] = True
        if link_i is not None:
            s = self._sent_of(link_i)
            shot = self.shot_at(self.words[link_i]["t0"])
            t0 = self.words[link_i]["t0"] - 0.06
            end = min(shot["end"] - 0.03, s["t1"] + 1.2)
            if self.reserved:
                end = min(end, self.o(self.reserved[0][0] - 0.3))
            end = max(end, min(t0 + 1.6, shot["end"] - 0.03))
            self.cta("link_below", t0, end, label=self.ent.get("link_label") or "Link abajo")
            s["info"]["_done"] = True

    def _address_hero(self, s: dict) -> dict | None:
        """Titulo protagonista de la direccion: calle chica + numero gigante, con pin y fondo
        desenfocado (el numero se lee de un vistazo)."""
        ai0, ai1 = self.anc["address"]
        ws = self.words[ai0:ai1 + 1]
        num = [w for w in ws if re.fullmatch(r"\d{1,6}[A-Za-z]?", _display(w["text"]))]
        street = [w for w in ws if w not in num]
        blk = [street, num] if num and street else [ws]
        hero = {num[0]["i"]} if num else {ws[-1]["i"]}
        shot = self.shot_at(ws[0]["t0"])
        t0 = max(ws[0]["t0"] - 0.1, shot["start"] + 0.02)
        lim = shot["end"] - 0.05
        if "landmark" in self.anc and self.anc["landmark"][0] > ai1:
            lim = min(lim, self.words[self.anc["landmark"][0]]["t0"] - 0.08)
        t1 = min(lim, max(ws[-1]["t1"] + 0.6, self.o(self.f(t0) + 1.2)))
        if self.f(t1) - self.f(t0) < 0.8:
            return None
        it = self.title_block(blk, "stack", hero, ["center", "center_low", "top"], t0, t1, "place")
        if not it:
            return None
        self.text_sfx("stack", t0, it, prio=6, line_ts=[blk[-1][0]["t0"]] if len(blk) > 1 else None)
        self.sfx((num[0] if num else ws[-1])["t0"] + 0.05, "notification", prio=8, src_id=it["id"])
        self.accent_for(it, ws, hero, "addr", max(t0, (num or ws)[0]["t0"] - 0.1), t1, force="icon",
                        icon="map-pin")
        if self.S["scenes"]["bg_blur"] and shot["talk"] and not shot.get("face_nominal"):
            self.add("effects", "addr_blur", {"start": t0, "end": t1, "kind": "bg_blur", "radius": 18.0},
                     event=False)
        self.punch_at((num[0] if num else ws[0])["t0"], 0.08, 0.35, min_gap=1.0)
        return it

    def card(self, variant: str, wi: int, t0: float, t1: float, title: str = "", subtitle: str = "",
             value: str = "", icon: str = "", theme: str = "light", sfx_name: str = "pop",
             cut_end: float | None = None) -> dict | None:
        shot = self.shot_at(self.words[wi]["t0"])
        g = self.geom(shot, t0, t1)
        fa, fb = self.f(t0), self.f(t1)
        base = {"kind": "card", "variant": variant, "title": title, "subtitle": subtitle,
                "value": value, "icon": icon, "theme": theme, "color": "#FFFFFF",
                "anim_in": "rise", "anim_out": "fade", "rotation": 0.0, "items": []}
        # zona libre (arriba de la cabeza, o pecho + franja baja) donde quepa a >= 0.58 de ancho
        spans = [("top", g["top"][0], g["top"][1]), ("center", g["center"][0], g["low"][1])]
        for z, lo, hi in spans:
            if not self.free(z, fa, fb) or (z == "center" and not self.free("low", fa, fb)):
                continue
            for size in (0.74, 0.66, 0.58):
                h = card_height(dict(base, size=size), self.style)
                if h + 0.01 <= hi - lo:
                    y = (lo + hi) / 2 if z == "top" else lo + h / 2 + 0.005
                    it = self.add("graphics", variant, dict(base, start=t0, end=t1, x=_r(self.XC, 3),
                                                            y=_r(y, 4), size=size), zone=_zone_y(y))
                    if it:
                        if y + h / 2 > ZONE_Y["center"]:
                            self.occ["low"].append((fa, fb, it["id"]))
                        self.ink.append((fa, fb, self.XC - size / 2, y - h / 2, self.XC + size / 2, y + h / 2,
                                         it["id"]))
                        self.sfx(t0 + 0.05, sfx_name, prio=8, src_id=it["id"])
                        self.sfx(t0 + 0.02, "swipe", prio=2, src_id=it["id"])
                        return it
        # no cabe sobre la toma: escena a pantalla completa con la tarjeta grande (en `card`)
        if getattr(self, "n_cut_left", 0) > 0 and self.S["cutaways"] > 0 and fb - fa >= 1.2 \
                and self.free("full", fa, fb):
            end = self.o(min(fb, fa + 2.6))
            if cut_end is not None and self.f(cut_end) - fa >= 1.3:
                end = min(end, cut_end)
            label = {"location": "Ubicacion", "schedule": "Horarios", "price": "Precio",
                     "link": "Agenda"}.get(variant, "")
            fields = {"start": t0, "end": end, "kind": "info_card", "background": "blur_self",
                      "bg_colors": [self.pal["dark"], self.pal["accent2"]],
                      "card": dict(base, x=0.5, y=0.5, size=0.74, anim_in="rise")}
            if label:
                ch = card_height(dict(base, size=0.74), self.style)
                fields["text"] = {"preset": "kicker", "text": label.upper(), "x": 0.5,
                                  "y": _r(max(SAFE["top"] + 0.04, 0.5 - ch / 2 - 0.06), 4), "size": 0.05,
                                  "font": self.K["text"], "box": self.pal["accent"], "color": "#FFFFFF",
                                  "anim_in": "slide_left"}
            cw = self.add("cutaways", variant, fields, zone="full")
            if cw:
                self.n_cut_left -= 1
                self.request_transition(t0, "scene")
                self.request_transition(end, "scene")
                self.sfx(t0 + 0.12, sfx_name, prio=9, src_id=cw["id"])
                self.music_swells.append({"start": _r(t0), "end": _r(end), "db": 2.0})
                return cw
        # tarjeta compacta (encabezado con icono, sin mapa/boton) si la completa no cupo
        if variant in ("location", "link", "schedule"):
            compact = dict(base, variant="generic")
            for z, lo, hi in spans:
                if not self.free(z, fa, fb) or (z == "center" and not self.free("low", fa, fb)):
                    continue
                for size in (0.74, 0.62):
                    h = card_height(dict(compact, size=size), self.style)
                    if h + 0.01 <= hi - lo:
                        y = (lo + hi) / 2 if z == "top" else lo + h / 2 + 0.005
                        it = self.add("graphics", variant, dict(compact, start=t0, end=t1, x=_r(self.XC, 3),
                                                                y=_r(y, 4), size=size), zone=_zone_y(y))
                        if it:
                            if y + h / 2 > ZONE_Y["center"]:
                                self.occ["low"].append((fa, fb, it["id"]))
                            self.ink.append((fa, fb, self.XC - size / 2, y - h / 2, self.XC + size / 2,
                                             y + h / 2, it["id"]))
                            self.sfx(t0 + 0.05, sfx_name, prio=8, src_id=it["id"])
                            self.sfx(t0 + 0.02, "swipe", prio=2, src_id=it["id"])
                            return it
        # ultimo recurso: etiqueta compacta con el dato
        txt = title if not value else f"{title} {value}".strip()
        for z in ("top", "center"):
            lo, hi = g[z]
            if hi - lo < 0.05 or not self.free(z, fa, fb):
                continue
            it = self.add("graphics", variant + "_tag", {
                "start": t0, "end": t1, "kind": "sticker_text", "text": txt, "bg": self.pal["light"],
                "fg": self.pal["dark"], "x": _r(self.XC, 3), "y": _r((lo + hi) / 2, 4), "size": 0.05,
                "anim_in": "pop", "anim_out": "pop_out", "rotation": -2.0, "color": "#FFFFFF"}, zone=z)
            if it:
                self.sfx(t0 + 0.05, sfx_name, prio=8, src_id=it["id"])
                return it
        return None

    def cta(self, variant: str, t0: float, t1: float, label: str = "", handle: str = "",
            zones: tuple = ("low", "center", "top")) -> dict | None:
        shot = self.shot_at(t0)
        g = self.geom(shot, t0, t1)
        fa, fb = self.f(t0), self.f(t1)
        size = 0.46 if variant == "link_below" else 0.62
        for z in zones:
            if not self.free(z, fa, fb):
                continue
            lo, hi = g[z]
            if hi - lo < 0.06:
                continue
            yy = 0.6 if z == "low" else ((lo + hi) / 2 if z == "top" else hi - 0.04)
            it = self.add("graphics", "cta", {
                "start": t0, "end": t1, "kind": "cta", "variant": variant, "label": label,
                "handle": handle, "x": _r(self.XC, 3), "y": _r(yy, 4), "size": size,
                "color": self.pal["accent"], "anim_in": "pop", "anim_out": "fade", "rotation": 0.0},
                zone=z)
            if it:
                self.ink.append((fa, fb, self.XC - size / 2, yy - 0.035, self.XC + size / 2, yy + 0.1, it["id"]))
                self.sfx(t0 + 0.04, "click", prio=8, src_id=it["id"])
                if variant == "link_below":
                    self.sfx(t0 + 0.3, "swoosh_down", prio=3, src_id=it["id"])
                else:
                    self.sfx(t0 + 0.35, "success", prio=4, src_id=it["id"])
                return it
        return None

    # ---------------------------------------------------------- 6. escenas (tratamientos)
    def scene_time(self) -> float:
        t = 0.0
        for c in self.fx["cutaways"]:
            t += self.f(c["end"]) - self.f(c["start"])
        for e in self.fx["effects"]:
            if e["kind"] in ("bw", "bg_replace", "color_pop"):
                t += self.f(e["end"]) - self.f(e["start"])
        return t

    def scenes(self) -> None:
        """Presupuesto de escenas del estilo: fondo reemplazado (papel/reticula) detras de la
        persona en la frase de beneficio (o de humor) con su titulo en tinta de contraste."""
        cfg = self.S["scenes"]
        budget = _lerp(*cfg["budget"], self.I) * self.D
        if not cfg["bg_replace"] or self.scene_time() >= budget:
            return
        order = {"beneficio": 0, "oferta": 1, "cierre": 2, "problema": 3, "otro": 4}
        cands = sorted([s for s in self.sents if not s["info"].get("_done")
                        and s["info"]["section"] in order], key=lambda s: (order[s["info"]["section"]], s["id"]))
        for s in cands:
            if self._bg_replace(s, budget - self.scene_time()):
                return

    def _bg_replace(self, s: dict, room: float) -> bool:
        info = s["info"]
        emph = set(info["emph"])
        kw = {w["i"] for w in self.words[s["w0"]:s["w1"] + 1] if w["kw"] and w["n"] not in STOP}
        blocks = self._blocks(s["w0"], s["w1"], emph, kw)
        best = None
        for bi, blk in enumerate(blocks):
            ws = [w for ln in blk for w in ln]
            shot = self.shot_at(ws[0]["t0"])
            face = shot.get("face")
            if not shot["talk"] or shot.get("face_nominal") or not face:
                continue
            if not (0.3 <= face[0] <= 0.7 and face[3] >= 0.095):
                continue
            a = max(ws[0]["t0"] - 0.12, shot["start"] + 0.05)
            b = min(ws[-1]["t1"] + 0.45, shot["end"] - 0.05)
            dur = self.f(b) - self.f(a)
            if dur < 1.3 or dur > max(3.2, room + 0.5):
                continue
            if self.drama_range and a < self.drama_range[1] and self.drama_range[0] < b:
                continue
            if not self.free("full", self.f(a), self.f(b)):
                continue
            score = len(blk) + (1 if any(w["n"] in CLAUSE_W for w in ws[1:]) else 0) + dur * 0.3
            if best is None or score > best[0]:
                best = (score, bi, a, b, shot)
        if best is None:
            return False
        _, bi, a, b, shot = best
        bgk = self.pick("bgr", self.S["scenes"]["bg_replace"])
        light = bgk in LIGHT_BG
        cols = ["#F3EFE6", "#E4DDCF"] if bgk == "paper" else [self.pal["dark"], self.pal["accent2"]]
        it = self.add("effects", "bgswap", {"start": a, "end": b, "kind": "bg_replace", "background": bgk,
                                            "bg_colors": cols})
        if not it:
            return False
        self.bg_fx.append((self.f(a), self.f(b), "light" if light else "dark"))
        self.sfx(a + 0.02, "swoosh_up", prio=6, src_id=it["id"])
        self.sfx(b - 0.05, "whoosh_soft", prio=3, src_id=it["id"])
        self.music_swells.append({"start": _r(a), "end": _r(b), "db": 1.5})
        # titulos de la frase (el bloque sobre el fondo nuevo sale con tinta de contraste)
        n = self.title_sequence(s, s["w0"], s["w1"], "title", ["top", "center"],
                                ["center", "center_low", "top"], humor=info["tone"] == "humor")
        if self.S["color_pop"] and self.I >= 0.55:
            self._color_pop(s)
        info["_done"] = n > 0
        return True

    # ---------------------------------------------------------- 7. tomas sin voz (b-roll)
    def broll(self) -> None:
        for s in self.shots:
            if s["talk"] or s["f1"] - s["f0"] < 1.2:
                continue
            speech = [w for w in self.words if s["start"] <= w["t0"] < s["end"]]
            spoken = sum(w["t1"] - w["t0"] for w in speech)
            if spoken > 0.35 * (s["end"] - s["start"]):
                continue
            t0 = s["start"] + 0.25
            t1 = s["end"] - 0.15
            fa, fb = self.f(t0), self.f(t1)
            card = self.broll_card.get(s["k"])
            if card:
                size = 0.74
                base = {"kind": "card", "variant": card["variant"], "title": card["title"],
                        "subtitle": card["subtitle"], "value": "", "icon": card["icon"], "theme": "light",
                        "color": "#FFFFFF", "anim_in": "rise", "anim_out": "fade", "rotation": 0.0,
                        "items": []}
                h = card_height(dict(base, size=size), self.style)
                y = _clamp(0.5, SAFE["top"] + h / 2 + 0.02, SAFE["bottom"] - h / 2 - 0.02)
                it = self.add("graphics", "broll_card", dict(base, start=t0, end=t1, x=_r(self.XC, 3),
                                                             y=_r(y, 4), size=size), zone="center")
                if it:
                    for z in ("top", "low"):
                        self.occ[z].append((fa, fb, it["id"]))
                    self.ink.append((fa, fb, self.XC - size / 2, y - h / 2, self.XC + size / 2, y + h / 2,
                                     it["id"]))
                    self.sfx(t0 + 0.05, "notification", prio=8, src_id=it["id"])
                    self.sfx(t0 + 0.02, "swipe", prio=3, src_id=it["id"])
                    self.sfx(t0 + 0.9, "pop_soft", prio=2, gain=-9.0, src_id=it["id"] + "pin")
            elif "address" not in self.shown and re.match(r"^(.*?)(\d{1,6}[A-Za-z]?)$", self.ent.get("address", "").strip()):
                m = re.match(r"^(.*?)(\d{1,6}[A-Za-z]?)$", self.ent.get("address", "").strip())
                lines = [{"text": m.group(1).strip().rstrip(",").upper(), "scale": 0.4, "font": self.K["text"]},
                         {"text": m.group(2), "scale": 1.0, "font": self.K["display"], "color": self.pal["accent"]}]
                size = self._fit_lines(lines, 0.24, self.MAXW)
                if self.free("center", fa, fb):
                    it = self.add("text", "broll", {
                        "start": t0, "end": t1, "preset": "stack", "lines": lines, "x": _r(self.XC, 3),
                        "y": 0.46, "size": _r(size, 4), "font": self.K["display"], "color": "#FFFFFF",
                        "accent": self.pal["accent"], "align": "center", "anim_in": "word_stagger",
                        "anim_out": "fade", "shadow": 0.6, "accent_words": [lines[-1]["text"]]}, zone="center")
                    if it:
                        self.shown.add("address")
                        self.text_sfx("stack", t0, it, prio=6)
            elif s.get("place") and not self.shown & {"address", "landmark"} and self.free("center", fa, fb):
                font = self.font_for(self.sub_preset("serif_elegant"))
                txt = _cap_first(s.get("place", ""))
                lines = [{"text": t, "scale": 1.0, "font": font}
                         for t in self._rewrap(txt.split(), font, 0.09, 2)]
                size = self._fit_lines(lines, 0.09, self.MAXW)
                pr = self.sub_preset("serif_elegant")
                it = self.add("text", "broll", {
                    "start": t0, "end": t1, "preset": pr, "text": "\n".join(ln["text"] for ln in lines),
                    "x": _r(self.XC, 3), "y": 0.46, "size": _r(size, 4), "font": font, "color": "#FFFFFF",
                    "accent": self.pal["accent"], "align": "center", "anim_in": "rise", "anim_out": "fade",
                    "shadow": 0.6, "accent_words": []}, zone="center")
                if it:
                    self.text_sfx(pr, t0, it, prio=5)
            if spoken < 0.2:
                self.music_swells.append({"start": _r(s["start"]), "end": _r(s["end"]), "db": 4.0})

    # ---------------------------------------------------------- 8. rotulo de nombre
    def lower_third(self) -> None:
        name = self.ent.get("name")
        if not name:
            return
        start_f = 2.0
        if self.sents:
            start_f = max(start_f, self.f(self.sents[0]["t1"]) + 0.3)
        if self.drama_range:
            start_f = max(start_f, self.f(self.drama_range[1]) + 0.2) if self.f(self.drama_range[0]) < start_f + 3 else start_f
        for s in self.shots:
            if not s["talk"]:
                continue
            fa = max(s["f0"] + 0.4, start_f)
            fb = min(s["f1"] - 0.3, fa + 2.8)
            if fb - fa < 1.8:
                continue
            if self.free("low", fa, fb):
                t0, t1 = self.o(fa), self.o(fb)
                lo, hi = self.geom(s, t0, t1)["low"]
                if hi - lo < 0.06:
                    continue
                it = self.add("graphics", "name", {
                    "start": t0, "end": t1, "kind": "lower_third", "title": name,
                    "subtitle": self.ent.get("role", ""), "x": 0.07, "y": _r((lo + hi) / 2, 4),
                    "size": 0.056, "color": self.pal["accent"], "anim_in": "slide_left", "anim_out": "fade",
                    "rotation": 0.0}, zone="low")
                if it:
                    self.sfx(t0 + 0.02, "swipe", prio=6, src_id=it["id"])
                    return

    # ---------------------------------------------------------- 9. CTA final
    def _final_window(self) -> tuple[float, float] | None:
        if self.D < 6:
            return None
        last_f = max([w["f1"] for w in self.words] + [0.0])
        fa = max(self.D - 2.3, self.D * 0.8, min(self.D - 1.6, last_f - 1.5))
        fb = self.D - 0.1
        return (fa, fb) if fb - fa >= 1.2 else None

    def reserve_final_cta(self) -> None:
        """Reserva el cierre (~2 s) para el CTA: sin titulos ni acentos encima (cierre limpio)."""
        win = self._final_window()
        if win is None or self.windows is not None:
            return
        self.reserved.append(win)
        for z in ("top", "center", "low"):
            self.occ[z].append((win[0], win[1], "reserve:cta"))

    def final_cta(self) -> None:
        if not self.reserved:
            if self.windows is None and self.D >= 6 and not any(
                    it.get("kind") == "cta" for it in self.fixed["graphics"]):
                win = self._final_window()
                if win:
                    self.cta("follow", self.o(win[0]), self.o(win[1]), label="Sigueme")
            return
        fa, fb = self.reserved[0]
        for z in ("top", "center", "low"):
            self.occ[z] = [e for e in self.occ[z] if e[2] != "reserve:cta"]
        if any(it.get("kind") == "cta" and self.f(float(it["end"])) > fa for it in self.fixed["graphics"]):
            return
        link = self.anc.get("link") is not None
        variant = "link_below" if link else "follow"
        label = (self.ent.get("link_label") or "Link abajo") if link else "Sigueme"
        t0, t1 = self.o(fa), self.o(fb)
        it = self.cta(variant, t0, t1, label=label, handle=self.ent.get("handle", ""),
                      zones=("low", "center"))
        if it is None:
            return
        shot = self.shot_at(t0)
        if self.S["scenes"]["bg_blur"] and shot["talk"] and not shot.get("face_nominal"):
            self.add("effects", "cta_blur", {"start": t0, "end": t1, "kind": "bg_blur", "radius": 16.0},
                     event=False)
        elif self.S["scenes"]["outline"] and shot["talk"] and not shot.get("face_nominal"):
            self.add("effects", "cta_outline", {"start": t0, "end": t1, "kind": "subject_outline",
                                                "color": self.pal["accent"], "width": 6.0, "glow": 0.5},
                     event=False)
        self.sfx(t0 + 0.5, "success", prio=4, src_id=it["id"] + "ok")

    # ---------------------------------------------------------- 10. resto del guion
    def sentences(self) -> None:
        total = sum(self.f(s["t1"]) - self.f(s["t0"]) for s in self.sents) or 1.0
        target = _lerp(*self.S["coverage"], self.I)
        order = sorted(self.sents, key=lambda s: ({"hook": 0, "problema": 1, "beneficio": 1,
                                                    "oferta": 2, "cierre": 2, "otro": 3}.get(s["info"]["section"], 3),
                                                   s["id"]))
        color_pop_used = any(e["kind"] == "color_pop" for e in self.fx["effects"])
        for s in order:
            if s["info"].get("_done"):
                continue
            covered = sum(self.f(w["t1"]) - self.f(w["t0"]) for w in self.words if w["i"] in self.titled_words)
            if covered / total >= target and s["info"]["section"] not in ("beneficio", "problema"):
                break
            info = s["info"]
            humor = info["tone"] == "humor"
            fxk = info["fx"]
            ws = self.words[s["w0"]:s["w1"] + 1]
            shot = self.shot_at(ws[0]["t0"])
            if fxk == "escena" and self.n_cut_left > 0 and self.S["cutaways"] > 0:
                if self._scene(s):
                    self.n_cut_left -= 1
                    info["_done"] = True
                    continue
            if fxk == "detras" and shot["talk"]:
                if self._behind(s):
                    info["_done"] = True
                    continue
            n = self.title_sequence(s, s["w0"], s["w1"], "title", ["top", "center"],
                                    ["center", "center_low", "top"], humor=humor)
            if not color_pop_used and (fxk == "color_pop" or (self.S["color_pop"] and info["section"] == "beneficio"
                                                              and self.I >= 0.55)):
                color_pop_used = self._color_pop(s)
            if n == 0 and fxk in ("zoom", "icono", "garabato"):
                em = [self.words[i] for i in info["emph"]] or ws[-1:]
                self.punch_at(em[0]["t0"], 0.1, 0.4)
            info["_done"] = n > 0

    def _color_pop(self, s: dict) -> bool:
        """Sujeto a color y fondo gris en la palabra de enfasis (una vez por video)."""
        if any(e["kind"] == "color_pop" for e in self.fx["effects"]):
            return True
        info = s["info"]
        em = [self.words[i] for i in info["emph"]] or self.words[s["w1"]:s["w1"] + 1]
        for hw in ([em[-1], em[0]] if len(em) > 1 else em):
            shot = self.shot_at(hw["t0"])
            if not shot["talk"] or shot.get("face_nominal"):
                continue
            a_, b_ = hw["t0"] - 0.1, min(hw["t1"] + 0.9, shot["end"] - 0.03)
            fa_, fb_ = self.f(a_), self.f(b_)
            if any(a < fb_ and fa_ < b for a, b, _ in self.bg_fx):
                continue
            if self.drama_range is not None and not (b_ < self.drama_range[0] or a_ > self.drama_range[1]):
                continue
            it = self.add("effects", "colorpop", {"start": a_, "end": b_, "kind": "color_pop",
                                                  "bg_desat": 1.0, "bg_dark": 0.22})
            if it:
                self.sfx(a_ + 0.08, "impact_soft", prio=5, src_id=it["id"])
                return True
        return False

    def _scene(self, s: dict) -> bool:
        ws = self.words[s["w0"]:s["w1"] + 1]
        emph = set(s["info"]["emph"])
        hero = [w for w in ws if w["i"] in emph] or ws[-2:]
        a, b = hero[0]["t0"] - 0.25, min(hero[-1]["t1"] + 0.8, self.shot_at(hero[0]["t0"])["end"] - 0.03)
        if self.f(b) - self.f(a) < 1.0 or not self.free("full", self.f(a), self.f(b)):
            return False
        font = self.K["display"]
        txt = " ".join(_display(w["text"]) for w in hero).upper()
        lines = [{"text": txt, "scale": 1.0, "font": font}]
        size = self._fit_lines(lines, 0.16, 0.74)
        bg = self.pick("bg", self.S["backgrounds"])
        cw = self.add("cutaways", "scene", {
            "start": a, "end": b, "kind": "title_card", "background": bg,
            "bg_colors": [self.pal["dark"], self.pal["accent2"]],
            "text": {"preset": "stack", "lines": lines, "x": 0.5, "y": 0.46, "size": _r(size, 4),
                     "font": font, "color": "#FFFFFF", "accent": self.pal["accent"],
                     "anim_in": "word_stagger", "align": "center"},
            "graphics": [{"kind": "doodle", "name": "sparkle", "x": 0.78, "y": 0.38, "size": 0.1,
                          "color": self.pal["accent"], "draw": True, "anim_in": "draw"}]}, zone="full")
        if cw:
            self.scene_transitions(a, b)
            self.music_swells.append({"start": _r(a), "end": _r(b), "db": 2.5})
            return True
        return False

    def _behind(self, s: dict) -> bool:
        ws = self.words[s["w0"]:s["w1"] + 1]
        shot = self.shot_at(ws[0]["t0"])
        if shot.get("face_nominal"):
            return False
        hero = [w for w in ws if w["i"] in set(s["info"]["emph"])][:1] or [max(ws, key=lambda w: len(w["n"]))]
        a, b = hero[0]["t0"] - 0.1, min(hero[0]["t1"] + 1.2, shot["end"] - 0.03)
        g = self.geom(shot, a, b)
        if not (shot["face_x"] and 0.25 <= shot["face_x"] <= 0.75 and (g["head_top"] or 0) >= 0.17):
            return False
        if not self.free("top", self.f(a), self.f(b)):
            return False
        font = self.K["display"]
        lines = [{"text": _display(hero[0]["text"]).upper(), "scale": 1.0, "font": font}]
        size = self._fit_lines(lines, 0.32, self.MAXW)
        y = _clamp(g["head_top"] + 0.03, SAFE["top"] + 0.08, 0.42)
        it = self.add("text", "behind", {"start": a, "end": b, "preset": "behind_giant", "lines": lines,
                                         "x": 0.5, "y": _r(y, 4), "size": _r(size, 4), "font": font,
                                         "color": "#FFFFFF", "accent": self.pal["accent"],
                                         "behind": True, "anim_in": "scale_down", "anim_out": "fade",
                                         "align": "center", "shadow": 0.2}, zone="top")
        if it:
            self.sfx(a + 0.05, "impact_soft", prio=6, src_id=it["id"])
            self.titled_words.add(hero[0]["i"])
            return True
        return False

    # ---------------------------------------------------------- 11. ritmo
    def rhythm(self) -> None:
        gap_max = _lerp(*self.S["gap"], self.I)
        for _ in range(40):
            ev = sorted({round(t, 3) for t, _ in self.events if 0 <= t <= self.D})
            pts = [0.0] + ev + [self.D]
            worst, wa, wb = 0.0, None, None
            for a, b in zip(pts[:-1], pts[1:]):
                if b - a > gap_max and b - a > worst:
                    worst, wa, wb = b - a, a, b
            if wa is None:
                return
            if not self._fill(wa, wb, gap_max):
                # hueco imposible de llenar (sin palabras ni zonas): se marca para no ciclar
                self.events.append(((wa + wb) / 2, "skip"))

    def _fill(self, fa: float, fb: float, gap_max: float) -> bool:
        mid = (fa + fb) / 2
        emph_all = {i for s in self.sents for i in s["info"]["emph"]}
        cands = [w for w in self.words if fa + 0.5 <= w["f0"] <= fb - 0.4]
        cands.sort(key=lambda w: (-(3 * (w["kw"] or w["i"] in emph_all)
                                    + min(len(w["n"]), 9) * 0.3 - (3 if w["n"] in STOP else 0)
                                    - abs(w["f0"] - mid) * 0.8)))
        kinds = ["punch", "accent", "punch", "icon", "sticker"]
        k0 = self.used["fill"]
        for w in cands[:6]:
            shot = self.shot_at(w["t0"])
            for j in range(len(kinds)):
                kind = kinds[(k0 + j) % len(kinds)]
                ok = None
                if kind == "punch" and shot["talk"]:
                    ok = self.punch_at(w["t0"], 0.07 + 0.05 * self.I, 0.35, role="beat", min_gap=1.2)
                elif kind in ("accent", "icon"):
                    ok = self._float_accent(w, shot, icon_only=(kind == "icon"))
                elif kind == "sticker" and w["n"] not in STOP and len(w["n"]) >= 4:
                    ok = self._sticker(w, shot)
                if ok:
                    self.used["fill"] += 1
                    return True
        # sin palabras (b-roll mudo): un destello decorativo a mitad del hueco
        t = self.o(mid)
        shot = self.shot_at(t)
        x = 0.78 if (shot.get("face_x") or 0.4) < 0.5 else 0.2
        if self.accent_ok(x, 0.2, 0.1, mid, mid + 1.2, shot, t, self.o(mid + 1.2)):
            name = self.pick("doodle", ["sparkle", "star4"])
            g = self.add("graphics", "beat", {"start": t, "end": self.o(min(self.D, mid + 1.3)),
                                               "kind": "doodle", "name": name, "x": x, "y": 0.2, "size": 0.1,
                                               "color": self.doodle_color(t, self.o(mid + 1.2), x, 0.2),
                                               "draw": True, "wiggle": 0.3, "stroke": 7.0,
                                               "anim_in": "draw", "anim_out": "fade", "rotation": 0.0},
                         zone="accent")
            if g:
                self._doodle_sfx(name, t + 0.03, x, g["id"])
                return True
        return False

    def _anchor_words(self) -> set:
        out = set()
        for k in ("price", "address", "landmark", "name"):
            if k in self.anc:
                a, b = self.anc[k]
                out |= set(range(a, b + 1))
        return out

    def _float_accent(self, w: dict, shot: dict, icon_only: bool = False) -> dict | None:
        t0 = w["t0"] - 0.05
        t1 = min(w["t1"] + 1.2, shot["end"] - 0.03)
        fa, fb = self.f(t0), self.f(t1)
        if fb - fa < 0.9:
            return None
        ht = self.geom(shot, t0, t1)["head_top"]
        base_y = _clamp((ht - 0.02) if ht else 0.3, SAFE["top"] + 0.06, 0.45)
        side = 0.78 if (shot.get("face_x") or 0.4) < 0.5 else 0.2
        pos = None
        for x, y in ((side, base_y), (side, base_y + 0.08), (1.0 - side, base_y), (side, base_y - 0.06)):
            if self.accent_ok(x, y, 0.12, fa, fb, shot, t0, t1):
                pos = (x, y)
                break
        if pos is None:
            return None
        x, y = pos
        icon = tone_icon(w["n"], self.role, self.used["topic_icon"])
        if icon:
            dark_bg = self._bg_at(shot, y - 0.03, y + 0.03, t0, t1)
            light = dark_bg is not None and contrast("#FFFFFF", dark_bg["rgb"]) < 2.4
            bcol = self.pal["dark"] if light else self.pal["accent"]
            g = self.add("graphics", "beat_icon", {
                "start": t0, "end": t1, "kind": "icon", "name": icon, "variant": "outline",
                "badge": "circle", "badge_color": bcol,
                "color": self.pal["dark"] if _luma(bcol) > 0.55 else "#FFFFFF", "draw": True,
                "x": x, "y": _r(y, 4), "size": 0.12, "anim_in": "pop", "anim_out": "pop_out",
                "rotation": _r((self.rand("bi" + str(w["i"])) - 0.5) * 14, 1)}, zone="accent")
            if g:
                self.used["topic_icon"] += 1
                self.sfx(t0 + 0.02, "bubble", prio=4, pan=(x - 0.5), src_id=g["id"], vary=True)
            return g
        if icon_only:
            return None
        name = self.pick("doodle", [d for d in self.S["doodles"] if d not in ("underline", "circle_scribble", "arrow_curve")] or ["sparkle"])
        g = self.add("graphics", "beat_doodle", {
            "start": t0, "end": t1, "kind": "doodle", "name": name, "x": x, "y": _r(y, 4),
            "size": 0.11, "color": self.doodle_color(t0, t1, x, y), "draw": True, "wiggle": 0.35,
            "stroke": 7.0, "anim_in": "draw", "anim_out": "fade",
            "rotation": _r((self.rand("bd" + str(w["i"])) - 0.5) * 20, 1)}, zone="accent")
        if g:
            self._doodle_sfx(name, t0 + 0.03, x, g["id"])
        return g

    def _sticker(self, w: dict, shot: dict) -> dict | None:
        if w["i"] in self.titled_words or w["i"] in self._anchor_words():
            return None
        t0 = w["t0"] - 0.05
        t1 = min(w["t1"] + 0.9, shot["end"] - 0.03)
        fa, fb = self.f(t0), self.f(t1)
        if fb - fa < 0.8:
            return None
        g = self.geom(shot, t0, t1)
        for z in ("center", "top"):
            if not self.free(z, fa, fb):
                continue
            lo, hi = g[z]
            if hi - lo < 0.05:
                continue
            y = lo + 0.03 if z == "center" else (lo + hi) / 2
            txt = _display(w["text"]).strip("¿?¡!").upper()
            bgc = self.pal["accent"] if _luma(self.pal["accent"]) < 0.9 else self.pal["dark"]
            g = self.add("graphics", "beat_tag", {
                "start": t0, "end": t1, "kind": "sticker_text", "text": txt,
                "bg": bgc, "fg": self.pal["dark"] if _luma(bgc) > 0.55 else "#FFFFFF",
                "x": _r(self.XC, 3), "y": _r(y, 4), "size": 0.058,
                "anim_in": "pop", "anim_out": "pop_out", "rotation": _r((self.rand("st" + str(w["i"])) - 0.5) * 8, 1),
                "color": "#FFFFFF"}, zone=z)
            if g:
                self.titled_words.add(w["i"])
                self.ink.append((fa, fb, 0.2, y - 0.03, 0.8, y + 0.03, g["id"]))
                self.sfx(t0 + 0.02, "pop_soft", prio=4, src_id=g["id"], vary=True)
                return g
        return None

    # ---------------------------------------------------------- 12. SFX
    def sfx_pass(self) -> None:
        acc: list[tuple[float, str]] = [(self.f(float(s.get("at", 0))), SFX_CAT.get(s.get("name"), "x"))
                                        for s in self.fixed["sfx"]]
        same_gap = 0.16 if self.style == "energetico" else 0.22
        reqs = sorted(self.sfx_req, key=lambda r: (-r["prio"], r["at"]))
        chosen = []
        for r in reqs:
            if self.I < 0.35 and r["prio"] <= 3:
                continue
            ft = self.f(r["at"])
            if ft < 0 or ft > self.D:
                continue
            if self.windows is not None and not any(wa - 0.3 <= ft <= wb + 0.6 for wa, wb in self.windows):
                continue
            cat = SFX_CAT.get(r["name"], "x")
            if any(c == cat and abs(t - ft) < same_gap for t, c in acc):   # capas de distinta familia: si
                continue
            # densidad: no mas de 4 SFX en una ventana de 1.2 s (3 si son de baja prioridad)
            near = sum(1 for t, _ in acc if abs(t - ft) < 0.6)
            if near >= 4 or (near >= 3 and r["prio"] <= 4):
                continue
            acc.append((ft, cat))
            chosen.append(r)
        for r in sorted(chosen, key=lambda r: r["at"]):
            at = r["at"]
            if not self.kept(at):
                at = self.snap_start(at)
            self.add("sfx", r["name"], {"at": max(0.0, at), "name": r["name"],
                                        "gain_db": _r(_clamp(r["gain"], -30, 3), 1),
                                        "pan": _r(r["pan"], 2)})

    # ---------------------------------------------------------- orquestacion
    def run(self) -> dict:
        self.camera()
        self.transitions()
        self.reserve_final_cta()
        self.hook()
        self.drama()
        self.data()
        self.scenes()
        self.broll()
        self.lower_third()
        self.sentences()
        self.final_cta()
        self.riser()
        self.assign_transitions()
        self.rhythm()
        if self.S["progress"] and self.windows is None:
            self.add("graphics", "progress", {"start": 0.0, "end": self.tl.duration, "kind": "progress",
                                              "x": 0.5, "y": 0.0, "size": 1.0, "color": self.pal["accent"],
                                              "thickness": 8.0, "anim_in": "fade", "anim_out": "none",
                                              "rotation": 0.0}, zone="edge", event=False)
        self.sfx_pass()
        for tr in ("text", "graphics"):
            for it in self.fx[tr]:
                it.pop("_geom", None)
        for tr in TRACKS:
            key = "at" if tr in ("transitions", "sfx") else "start"
            self.fx[tr].sort(key=lambda it: (float(it[key]), it["id"]))
        return self.fx


# ---------------------------------------------------------------- configuracion global
def _captions_cfg(style: str, pal: dict) -> dict:
    c = STYLES[style]["captions"]
    return {"enabled": True, "preset": c["preset"], "y": c["y"], "size": c["size"], "font": c["font"],
            "color": "#FFFFFF", "accent": pal["accent"], "case": c["case"],
            "max_words": c["max_words"], "hide_during_titles": True}


def _grade_cfg(style: str) -> dict:
    g = STYLES[style]["look"]
    return {"enabled": True, "look": g["look"], "match_shots": True, "vignette": g["vignette"],
            "grain": g["grain"], "sharpen": g["sharpen"]}


def _music_cfg(style: str, script: dict, ch: _Choreo) -> dict:
    m = STYLES[style]["music"]
    topic = script.get("topic") or script["entities"].get("role") or ""
    prompt = m["prompt"] + (f", for a video about {strip_accents(topic).lower()}" if topic else "")
    stops = sorted(ch.music_stops, key=lambda s: s["at"])
    swells = []
    for sw in sorted(ch.music_swells, key=lambda s: s["start"]):
        if swells and sw["start"] <= swells[-1]["end"]:
            swells[-1]["end"] = max(swells[-1]["end"], sw["end"])
            swells[-1]["db"] = max(swells[-1]["db"], sw["db"])
        else:
            swells.append(dict(sw))
    cfg = {"enabled": True, "source": "ai", "track": None, "file": None, "prompt": prompt,
           "bpm": m["bpm"], "gain_db": m["gain_db"], "duck_db": m["duck_db"], "fade_in": 0.2,
           "fade_out": 1.5, "stops": stops, "swells": swells}
    if ch.drop_at is not None:
        cfg["drop_at"] = ch.drop_at
    return cfg


# ---------------------------------------------------------------- validacion
def validate_min(plan: dict) -> list[str]:
    """Validacion minima propia (si schema.py no esta): kinds y campos del catalogo."""
    errs = []
    fx = plan.get("fx") or {}
    dur = float(plan.get("duration") or 0) or None
    for tr in TRACKS:
        ids = set()
        for it in fx.get(tr, []):
            iid = it.get("id")
            if not iid or iid in ids:
                errs.append(f"{tr}: id invalido o duplicado {iid!r}")
            ids.add(iid)
            k = it.get(KIND_KEY[tr])
            if k not in KIND_SET[tr]:
                errs.append(f"{tr}[{iid}]: {KIND_KEY[tr]} fuera del catalogo ({k!r})")
            extra = set(it) - FIELDS[tr]
            if extra:
                errs.append(f"{tr}[{iid}]: campos fuera del catalogo {sorted(extra)}")
            if tr in ("transitions", "sfx"):
                if not isinstance(it.get("at"), (int, float)):
                    errs.append(f"{tr}[{iid}]: falta at")
            else:
                s, e = it.get("start"), it.get("end")
                if not isinstance(s, (int, float)) or not isinstance(e, (int, float)) or e <= s:
                    errs.append(f"{tr}[{iid}]: tiempos invalidos")
                elif dur and (s < 0 or e > dur + 1e-6):
                    errs.append(f"{tr}[{iid}]: fuera del video")
            if tr == "graphics":
                if it.get("kind") == "doodle" and it.get("name") not in DOODLES:
                    errs.append(f"graphics[{iid}]: doodle desconocido {it.get('name')!r}")
                if it.get("kind") == "icon" and not assets.icon_exists(str(it.get("name"))):
                    errs.append(f"graphics[{iid}]: icono inexistente {it.get('name')!r}")
                if it.get("kind") == "card" and it.get("variant") not in CARD_VARIANTS:
                    errs.append(f"graphics[{iid}]: tarjeta desconocida")
                if it.get("kind") == "cta" and it.get("variant") not in CTA_VARIANTS:
                    errs.append(f"graphics[{iid}]: cta desconocido")
            if tr == "cutaways" and it.get("background") not in BACKGROUNDS:
                errs.append(f"cutaways[{iid}]: fondo desconocido")
    return errs


def _finalize(p: dict) -> dict:
    """schema.normalize + validate si existe (import perezoso); si no, validacion propia."""
    try:
        from . import schema as S
    except Exception:
        S = None
    errs: list[str] = []
    if S is not None and hasattr(S, "normalize"):
        try:
            p = S.normalize(p)
            if hasattr(S, "validate"):
                errs = [e for e in S.validate(p) if "falta 'video'" not in e]
        except Exception as ex:  # pragma: no cover
            errs = [f"schema.normalize fallo: {ex}"] + validate_min(p)
    else:
        errs = validate_min(p)
    errs += [e for e in validate_min(p) if "campos fuera" in e or "catalogo" in e or "icono" in e]
    p.setdefault("director", {})["warnings"] = sorted(set(errs))[:50]
    return p


# ---------------------------------------------------------------- API
def _base_v2(plan: dict) -> dict:
    try:
        from . import schema as S
        return S.migrate_v1(plan)
    except Exception:
        p = copy.deepcopy(plan)
        p["version"] = 2
        p.setdefault("shots", [])
        p.setdefault("entities", {})
        p.setdefault("director", {})
        p.setdefault("fx", {})
        return p


def _script_public(script: dict) -> dict:
    """Guion serializable para plan.director.script (permite re-dirigir sin LLM)."""
    return {"topic": script.get("topic", ""), "llm": script.get("llm", False),
            "anchors": script.get("anchors", {}), "ocr": script.get("ocr", {}),
            "sentences": [{"id": s["id"], "w0": s["w0"], "w1": s["w1"], "text": s["text"],
                           "t0": _r(s["t0"]), "t1": _r(s["t1"]),
                           **{k: v for k, v in s["info"].items() if not k.startswith("_")}}
                          for s in script["sents"]]}


def _script_from_public(plan: dict, pub: dict, shots: list[dict]) -> dict:
    words = _flatten_words(plan)
    sents = _sentences(words)
    ent, anc = extract_entities(words, sents, shots)
    by = {s["id"]: s for s in pub.get("sentences") or []}
    ok = len(by) == len(sents) and all(by[s["id"]]["w0"] == s["w0"] and by[s["id"]]["w1"] == s["w1"]
                                       for s in sents if s["id"] in by)
    for k, s in enumerate(sents):
        s["info"] = _classify(s, words, k, len(sents), anc)
        if ok:
            src = by[s["id"]]
            for key in ("section", "emph", "fx", "icons", "energy", "tone", "title"):
                if key in src:
                    s["info"][key] = src[key]
    e2 = dict(plan.get("entities") or {})
    for k, v in e2.items():
        if isinstance(v, str):
            ent[k] = v
    return {"words": words, "sents": sents, "entities": ent, "anchors": anc,
            "topic": pub.get("topic", ""), "llm": pub.get("llm", False), "ocr": pub.get("ocr", {})}


def choreograph(plan: dict, shots: list[dict], script: dict, style: str = "dinamico",
                intensity: float = 0.75, seed: int = 7, fixed: dict | None = None,
                windows: list | None = None, rev: int = 0) -> tuple[dict, _Choreo]:
    ch = _Choreo(plan, shots, script, style, intensity, seed, fixed=fixed, windows=windows, rev=rev)
    fx = ch.run()
    return fx, ch


def propose_trims(plan: dict, shots: list[dict], words: list[dict], keep: float = 2.8) -> list[dict]:
    """Recortes sugeridos (cuts con src "ai") para que ninguna toma muda se alargue: de un
    b-roll sin voz se conservan `keep` s (el inicio: la revelacion) y se quita el silencio que
    lo rodea (deja 0.35 s tras la ultima palabra y 0.3 s antes de la siguiente). No propone
    nada si el plan ya trae recortes de la IA (el usuario pudo haberlos desactivado)."""
    if any(isinstance(c, dict) and c.get("src") == "ai" for c in plan.get("cuts") or []):
        return []
    tl = _timeline(plan)
    out: list[dict] = []

    def add(a, b, reason):
        if b - a < 0.25:
            return
        if tl.remap_clamped(b) - tl.remap_clamped(a) < 0.25:        # ya cortado
            return
        out.append({"start": _r(a), "end": _r(b), "enabled": True, "src": "ai",
                    "id": f"cut_ai_{len(out) + 1}", "reason": reason})

    for s in shots:
        if s.get("use", "a_camara") == "a_camara":
            continue
        a, b = float(s["start"]), float(s["end"])
        if any(w["t1"] > a + 0.05 and w["t0"] < b - 0.05 for w in words):
            continue
        fa, fb = tl.remap_clamped(a), tl.remap_clamped(b)
        if fb - fa > keep + 0.6:
            add(tl.inverse(fa + keep), b, "b-roll mudo largo")
        prev = [w for w in words if w["t1"] <= a + 0.05]
        if prev and fa - tl.remap_clamped(prev[-1]["t1"]) > 0.7:
            add(prev[-1]["t1"] + 0.35, a, "silencio antes del b-roll")
        nxt = [w for w in words if w["t0"] >= b - 0.05]
        if nxt and tl.remap_clamped(nxt[0]["t0"]) - fb > 0.8:
            add(b, nxt[0]["t0"] - 0.3, "silencio despues del b-roll")
    return out


def _silence_notes(plan: dict, words: list[dict], limit: float = 2.5) -> list[str]:
    tl = _timeline(plan)
    pts = [0.0] + [x for w in words for x in (tl.remap_clamped(w["t0"]), tl.remap_clamped(w["t1"]))] + \
        [tl.final_duration]
    notes = []
    for k in range(0, len(pts) - 1, 2):
        a, b = pts[k], pts[k + 1]
        if b - a > limit and a > 0.5:
            notes.append(f"silencio de {b - a:.1f} s en {a:.1f} s (tiempo final)")
    return notes


def direct(plan: dict, style: str = "dinamico", intensity: float = 0.75, seed: int = 7,
           use_llm: bool = True, on_step: Callable | None = None, trim: bool = True) -> dict:
    """Plan v1 (o v2) -> plan v2 con TODA la edicion decidida (copia; no modifica `plan`).
    `trim`: propone recortes (cuts src "ai") para b-roll mudo demasiado largo."""
    t_start = _now()
    style = style if style in STYLES else "dinamico"
    p = _base_v2(plan)
    p["duration"] = _plan_duration(p)
    timings = {}
    # guion con LLM: arranca en cuanto se conocen las tomas (limites + rostro), en paralelo al VLM
    llm_box: dict = {}
    words = _flatten_words(p)
    sents = _sentences(words)
    can_llm = use_llm and bool(sents) and llm_available()

    def on_basic(shots_basic):
        if not can_llm or "th" in llm_box:
            return
        sb = [dict(x) for x in shots_basic]

        def _run():
            t = _now()
            llm_box["r"] = _llm_script(sents, sb)
            llm_box["ms"] = (_now() - t) * 1000
        th = threading.Thread(target=_run, daemon=True)
        llm_box["th"] = th
        th.start()

    _step(on_step, "tomas", 0.0, "detectando tomas")
    t = _now()
    budget = float(os.environ.get("REELFY_DIRECTOR_BUDGET", "50"))
    shots = analyze_shots(p, use_llm=use_llm, on_step=on_step, deadline=t_start + budget - 8.0,
                          on_basic=on_basic)
    on_basic(shots)
    timings["shots_ms"] = round((_now() - t) * 1000)
    _step(on_step, "guion", 0.0, "entendiendo el guion")
    if llm_box.get("th") is not None:
        llm_box["th"].join(timeout=max(1.0, t_start + budget - _now()))
        timings["llm_ms"] = round(llm_box.get("ms", 0))
    t = _now()
    script = analyze_script(p, shots, use_llm=False, llm_result=llm_box.get("r"))
    notes: list[str] = []
    if trim:
        trims = propose_trims(p, shots, script["words"], STYLES[style]["broll_keep"])
        if trims:
            p["cuts"] = list(p.get("cuts") or []) + trims
            removed = sum(c["end"] - c["start"] for c in trims)
            notes.append(f"recortes IA propuestos: {len(trims)} ({removed:.1f} s de b-roll/silencio)")
    _step(on_step, "coreografia", 0.0, "coreografiando la edicion")
    old = (plan.get("fx") or {}) if isinstance(plan.get("fx"), dict) else {}
    locked_ids = set((plan.get("director") or {}).get("locked") or [])
    fixed = {tr: [copy.deepcopy(it) for it in old.get(tr) or [] if isinstance(it, dict) and (
        it.get("locked") or it.get("src") == "user" or it.get("id") in locked_ids)] for tr in TRACKS}
    fx, ch = choreograph(p, shots, script, style, intensity, seed, fixed=fixed)
    for tr in TRACKS:
        if fixed[tr]:
            fx[tr] = sorted(fixed[tr] + fx[tr], key=lambda it: (float(it.get("at", it.get("start", 0))), str(it["id"])))
    timings["choreo_ms"] = round((_now() - t) * 1000)
    pal = assets.palette(style)
    fx["captions"] = _captions_cfg(style, pal)
    fx["music"] = _music_cfg(style, script, ch)
    fx["grade"] = _grade_cfg(style)
    p["fx"] = fx
    p["shots"] = [{k: v for k, v in s.items() if k != "vlm"} for s in shots]
    ent = {k: script["entities"].get(k, "") for k in ("name", "role", "price", "address", "landmark",
                                                       "cta", "link_label", "handle")}
    for k in ("schedule", "phone"):
        if script["entities"].get(k):
            ent[k] = script["entities"][k]
    p["entities"] = ent
    prev = plan.get("director") or {}
    notes += _silence_notes(p, script["words"])
    if (script.get("ocr") or {}).get("name_candidates") and not ent.get("name"):
        notes.append("nombre leido en letreros (no usado; confirmar en el Studio): "
                     + ", ".join(script["ocr"]["name_candidates"][:3]))
    p["director"] = {"style": style, "intensity": float(intensity), "seed": int(seed),
                     "locked": list(prev.get("locked") or []),
                     "llm": _llm_model() if script.get("llm") else "heuristico",
                     "vlm": _vlm_model() if any(s.get("vlm") for s in shots) else "heuristico",
                     "rev": 0, "script": _script_public(script), "timings": timings,
                     "kit": {k: v for k, v in ch.K.items()}, "notes": notes}
    p = _finalize(p)
    p["director"]["timings"]["total_ms"] = round((_now() - t_start) * 1000)
    _step(on_step, "listo", 1.0, "")
    return p


def redirect(plan: dict, style: str | None = None, intensity: float | None = None,
             seed: int | None = None, only_ids: list[str] | None = None,
             use_llm: bool = False) -> dict:
    """Re-dirige un plan v2 respetando items `locked` y `src:"user"` (y los ids en
    director.locked). Con `only_ids`, solo re-hace esos items (mismo momento, otra propuesta).
    Tambien acepta en only_ids/locked las claves "captions", "music" y "grade"."""
    p = _base_v2(plan)
    p["duration"] = _plan_duration(p)
    d = dict(p.get("director") or {})
    style = style if style in STYLES else d.get("style", "dinamico")
    style = style if style in STYLES else "dinamico"
    intensity = float(d.get("intensity", 0.75) if intensity is None else intensity)
    seed = int(d.get("seed", 7) if seed is None else seed)
    rev = int(d.get("rev", 0)) + 1
    locked_ids = set(d.get("locked") or [])
    shots = p.get("shots") or []
    if not shots or not all("use" in s for s in shots):
        shots = analyze_shots(p, use_llm=use_llm)
    pub = d.get("script")
    if pub:
        script = _script_from_public(p, pub, shots)
    else:
        script = analyze_script(p, shots, use_llm=use_llm)
    old = p.get("fx") or {}
    only = set(only_ids or [])
    fixed = {k: [] for k in TRACKS}
    windows = None
    tl = _timeline(p)
    removed_windows = []
    for tr in TRACKS:
        for it in old.get(tr) or []:
            keep = bool(it.get("locked")) or it.get("src") == "user" or it.get("id") in locked_ids
            if only:
                keep = keep or it.get("id") not in only
            if keep:
                fixed[tr].append(copy.deepcopy(it))
            elif only:
                if tr in ("transitions", "sfx"):
                    a = tl.remap_clamped(float(it.get("at", 0)))
                    removed_windows.append((a - 0.2, a + 0.2))
                else:
                    removed_windows.append((tl.remap_clamped(float(it["start"])),
                                            tl.remap_clamped(float(it["end"]))))
    if only:
        windows = removed_windows
    removed = [{k: v for k, v in it.items() if k != "id"} for tr in TRACKS for it in old.get(tr) or []
               if only and it.get("id") in only]
    for k in range(4):
        fx, ch = choreograph(copy.deepcopy(p), shots, script, style, intensity,
                             seed if not only else seed + rev + 97 * k,
                             fixed=copy.deepcopy(fixed), windows=windows, rev=rev + k)
        if not only:
            break
        new_items = [{kk: v for kk, v in it.items() if kk != "id"} for tr in TRACKS for it in fx[tr]]
        # "otra propuesta": si todo lo rehecho quedo identico, se intenta con otra semilla
        if not removed or any(it not in removed for it in new_items) or not new_items:
            break
    for tr in TRACKS:
        fx[tr] = sorted(fixed[tr] + fx[tr], key=lambda it: (float(it.get("at", it.get("start", 0))), str(it["id"])))
    pal = assets.palette(style)
    for key, fn in (("captions", lambda: _captions_cfg(style, pal)),
                    ("music", lambda: _music_cfg(style, script, ch)),
                    ("grade", lambda: _grade_cfg(style))):
        keep = key in locked_ids or (only and key not in only)
        fx[key] = copy.deepcopy(old[key]) if keep and isinstance(old.get(key), dict) else fn()
    p["fx"] = fx
    p["shots"] = shots
    d.update({"style": style, "intensity": intensity, "seed": seed, "rev": rev,
              "script": _script_public(script), "kit": dict(ch.K),
              "notes": _silence_notes(p, script["words"])})
    p["director"] = d
    return _finalize(p)


# ---------------------------------------------------------------- lectura humana
def edit_table(plan: dict) -> str:
    """Tabla legible de la edicion (orden por tiempo final)."""
    tl = _timeline(plan)
    fx = plan.get("fx") or {}
    rows = []
    for tr in TRACKS:
        for it in fx.get(tr) or []:
            if tr in ("transitions", "sfx"):
                a = float(it["at"])
                b = a + float(it.get("dur", 0) or 0)
            else:
                a, b = float(it["start"]), float(it["end"])
            kind = it.get(KIND_KEY[tr])
            extra = ""
            if tr == "text":
                extra = it.get("text") or " / ".join(ln.get("text", "") for ln in it.get("lines") or [])
                extra = extra.replace("\n", " / ") + f"  [{it.get('font')}, y={it.get('y')}, s={it.get('size')}]"
            elif tr == "graphics":
                k = it.get("kind")
                if k == "doodle":
                    extra = f"{it.get('name')} @({it.get('x')},{it.get('y')})"
                elif k == "icon":
                    extra = f"{it.get('name')} @({it.get('x')},{it.get('y')})"
                elif k == "card":
                    extra = f"{it.get('variant')}: {it.get('title')} | {it.get('subtitle')} {it.get('value')}"
                elif k == "cta":
                    extra = f"{it.get('variant')}: {it.get('label')}"
                else:
                    extra = str(it.get("title") or it.get("text") or "")
            elif tr == "cutaways":
                t = it.get("text") or {}
                extra = f"{it.get('background')}: {t.get('preset')} {t.get('to') or ''} {t.get('text') or ''}"
                if isinstance(it.get("card"), dict):
                    extra += f" | card {it['card'].get('variant')}: {it['card'].get('title')}"
                gs = [g_.get("text") or g_.get("name") for g_ in it.get("graphics") or []]
                if gs:
                    extra += " | " + ", ".join(str(x) for x in gs)
            elif tr == "effects":
                extra = ", ".join(f"{k}={it[k]}" for k in ("from", "to", "amount", "hold", "contrast", "pan")
                                  if k in it)
            elif tr == "transitions":
                extra = f"dur={it.get('dur')} {it.get('dir', '')}"
            elif tr == "sfx":
                extra = f"{it.get('gain_db')} dB"
            rows.append((tl.remap_clamped(a), tl.remap_clamped(b), a, tr, str(kind), extra, it.get("id")))
    rows.sort(key=lambda r: (r[0], TRACKS.index(r[3])))
    out = [f"{'final':>6} {'fin':>6} {'orig':>6}  {'pista':<11} {'kind':<16} detalle"]
    for fa, fb, a, tr, kind, extra, iid in rows:
        out.append(f"{fa:6.2f} {fb:6.2f} {a:6.2f}  {tr:<11} {kind:<16} {extra}")
    return "\n".join(out)


def stats(plan: dict) -> dict:
    """Metricas de la edicion para pruebas/demos (tiempo final)."""
    tl = _timeline(plan)
    fx = plan.get("fx") or {}
    D = float(tl.final_duration)
    ev = []
    for tr in ("text", "graphics", "cutaways"):
        ev += [tl.remap_clamped(float(it["start"])) for it in fx.get(tr) or []]
    ev += [tl.remap_clamped(float(it["start"])) for it in fx.get("effects") or []
           if it.get("kind") in ("punch", "bw", "color_pop", "shake", "zoom", "bg_replace", "bg_blur",
                                 "subject_outline", "flash_frame", "rgb_split")]
    ev += [tl.remap_clamped(float(it["at"])) for it in fx.get("transitions") or []]
    ev = sorted(set(round(e, 3) for e in ev))
    pts = [0.0] + ev + [D]
    gaps = [b - a for a, b in zip(pts[:-1], pts[1:])]
    return {"final_duration": round(D, 3), "events": len(ev), "max_gap": round(max(gaps) if gaps else D, 3),
            "mean_gap": round(D / max(1, len(ev)), 3),
            "counts": {tr: len(fx.get(tr) or []) for tr in TRACKS}}
