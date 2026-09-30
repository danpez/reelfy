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
  direct(plan, style="dinamico", intensity=0.75, seed=7, use_llm=True, on_step=None, trim=True,
         music_sync=None, music=None) -> plan v2 (copia). plan.director lleva script, kit, notes,
      timings, rev y music_grid ({bpm, beats, downbeats, drop, key}: rejilla de la pista IA).
      music_sync (default = use_llm y musica IA lista): genera la musica real antes de cerrar la
      coreografia (music.get_music con la cfg en tiempo final, como la pide render) y alinea
      punches, el golpe del drop y los pops de SFX a sus beats; el render reusa el cache.
      music: la musica que eligio el usuario (se mezcla en fx.music antes de la rejilla; con otra
      fuente que no sea la IA no hay sincronia).
  sign_name(video, shots, role) -> {name, line} | None : nombre en un letrero-directorio: el
      letrero debe tener UNA sola persona de la profesion y dos lecturas del VLM coincidir. Queda
      como candidato (entities.name_candidate, name_src "letrero", name_confirmed false): no se
      publica en rotulo/CTA hasta que el usuario lo confirme (name_usable(entities)).
  find_enums(words, w0, w1) -> enumeraciones (clausulas paralelas / listas con comas).
  music_grid(plan, style, seed, music_cfg) / music_cfg_final(plan, music_cfg).
  redirect(plan, style=None, intensity=None, seed=None, only_ids=None, use_llm=False,
           music_sync=None, music_timeout=12) -> plan v2 (reusa director.music_grid si la pista no
      cambia; conserva las notas del nombre y de la musica)
  apply_profile(plan, profile) -> plan v2 : pone/quita el perfil de la persona (plan.profile, doc
      3.2) y re-dirige al instante sin LLM (redirect keep_rev: el resto de la edicion igual; lo
      bloqueado recibe los datos nuevos). apply_profile_entities(ent, anc, words, profile) fija
      las entidades del perfil (name_src 'perfil') y ancla el momento en que dice su nombre.
  detect_shots(video, duration=None) -> list[dict]
  analyze_shots(plan, use_llm=True, on_step=None, scan=None, deadline=None, on_basic=None)
      -> list[dict] (+ face, motion, rows = perfil de color del fondo, speech)
  analyze_script(plan, shots, use_llm=True) -> dict
  propose_trims(plan, shots, words, keep) -> list[cut] (src "ai")
  choreograph(plan, shots, script, style, intensity, seed, fixed=None, windows=None) -> (fx, _Choreo)
  symmetrize(plan) -> plan v2 (copia) : regla de SIMETRIA sobre un plan existente (sin LLM,
      determinista; mismos ids, tiempos y contenido): todo lo de la IA centrado en x = 0.5 con
      ancho <= SYM_W, glifos simetricos en par espejo ('<id>' y '<id>_m'), glifos con mano e
      iconos solo centrados, flechas verticales centradas; lo que no cabe simetrico sin tapar la
      cara u otra tinta se quita. Nunca mueve lo del usuario ni lo bloqueado.
  zone_of(track, item) -> str | None ; edit_table(plan) -> str ; stats(plan) ; STYLES ; TYPE_KIT
  contrast(a, b) / rel_lum(c) (WCAG) ; box_text_color(item) ; tone_icon(tag, role)

Convenciones de coordenadas que emite el director (para los renderers):
  - SIMETRIA (regla de la marca): todo overlay deja el MISMO aire a izquierda y derecha. Lo
    centrado va en x = 0.5 con ancho <= SYM_W (0.76 W, banda 0.12..0.88); nada se recorre de
    lado (si no cabe, se achica). Los acentos van centrados o en PAR ESPEJO (x y 1-x).
  - text: (x, y) = centro del bloque de texto (x = 0.5, align center; cascade tambien: la
    escalera va dentro de su caja centrada); `size` = tamano de fuente / W; `lines[].scale`
    multiplica `size`. Los bloques se ajustan (ancho medido con las fuentes reales, <= 0.76 W)
    para caber en SAFE y no tapar la cara (la camara hace zoom anclado en el rostro). Un bloque =
    una clausula hablada (2-4 lineas que se revelan al ritmo de la voz), nunca menos de ~0.9 s.
  - graphics card/cta: (x, y) = centro; `size` = ancho / W. sticker_text/lower_third: `size` =
    tamano de fuente / W (lower_third: align 'center', x = CENTRO del panel).
  - doodle/icon: (x, y) = centro; `size` = lado de la caja / W. Par espejo: ids '<id>' y
    '<id>_m' (mismo nombre, tamano, y, tiempos y animacion; rotacion invertida; un solo SFX).
    Flechas que senalan: verticales y centradas (x = path_to x = 0.5, bend 0).
  - cutaways info_card: la tarjeta va en `card` (item de graphics.card), no en graphics[].
Tipografia: un "kit" por estilo y video (display + sans en 2-3 pesos + un acento serif o
manuscrito): como maximo 4 fuentes y 3 familias. El color de tinta se elige contra el fondo
real de la franja (luminancia/tono medidos por toma) y todo texto sobre caja cumple WCAG 4.5.
Tiempos SIEMPRE originales (el render remapea). Aleatoriedad solo via FrameCtx.rand(key).
Direccion de arte (ola 2): subtitulos con presencia iguales en todo el video; nunca texto sobre
ojos/nariz/boca (pista de rostro a 2 fps por toma); texto detras del sujeto solo si se lee
(oclusion estimada <= BEHIND_MAX, si no sube o pasa delante); precio como contador en la toma
(la persona sigue visible, fondo tratado detras) salvo en energetico (escena); enumeraciones con
iconos que se dibujan (escena de lista para 3+); flechas hacia tarjetas largas; subrayado o
circulo a mano en la linea protagonista; remate con rotulo nombre + rol y CTA seguir + link
sostenidos hasta el ultimo cuadro; efectos de sujeto (reemplazo de fondo, contorno) solo con
recorte limpio (shots[].matte_q >= MATTE_OK).
Ola 3 (revision del critico, flujo real del servidor con los clips en orden de subida):
b-roll que llega al FINAL = tarjeta de cierre (se conserva END_KEEP s; seguir + link + ubicacion
hasta el ultimo cuadro); el CTA final entra al terminar la ultima palabra del remate (el chiste se
ve limpio; el desenfoque solo despues); el link de la frase del CTA se sostiene hasta el final en
vez de repetirse; rotulo al inicio (primera toma a camara tras el hook, sin titulos encima); sin
nombre confirmado, rotulo y seguir solo con el rol; escenas relampago (~1 s) y jump-cuts por
clausula en tomas largas; el precio aterriza en 0.45 s y queda quieto >= 0.8 s (transicion seca
despues o cruza el corte); marcas (subrayado/circulo) en la linea de SU palabra; flechas tambien
en tarjetas sobre b-roll.
Ola 3 (frente director, calidad visual): 'Regenerar con IA' propone OTRA version real del mismo id
(`_variants`: otro preset/kind/posicion/parametros por semilla, sin tocar nada mas); lo bloqueado
ocupa su frase y su rango; ids estables por frase (s<N>) o union de tomas (j<K>); titulos con
presencia (MIN_LINE 0.052 W, sin titulos de muletillas); hook cerrado y sobre el fondo del estilo;
momento protagonista con palabra gigante + frase legible (_bg_hero); primeros planos extremos y
fuentes horizontales (rostro al cuadro 9:16, franja 'under'); aire muerto inicial recortado;
behind_giant siempre detras y en SAFE; boton Seguir legible; musica con intro_fade por estilo.
Ola 3b (revision del critico): oraciones partidas por la mayuscula de Whisper (STARTERS) y nunca
en palabra colgante (DANGLING); muletillas generales (FILLER) recortadas del borde y LIGHT sin
contar como contenido; hook sin preambulo de relleno (y 'muletilla al inicio' recortada); piso de
titulos 1.15x el subtitulo y kicker solo con etiqueta explicita; primer plano: titulos, rotulo y
Seguir BAJO la barbilla (pista de rostro del rango) con subtitulos ocultos (y_ranges hide); piso
de plano medio en tomas abiertas (zcap, _fit_zoom si camina; _cap_cam para composiciones
arriba); variantes por geometria y contraste, acentos ligados que se mueven con su titulo,
composiciones que se bloquean juntas; fondo local por celdas (shots[].cells) y contorno oscuro en
trazos blancos; drama solo con senal lexica; VLM con tope y corte tras 2 fallos.
Perfil de la persona (plan.profile): sus datos mandan (nunca el nombre de un letrero): rotulo
nombre + profesion (+ negocio si cabe) temprano (o cuando dice su nombre: dos palabras o
presentacion), Seguir con red principal + @usuario + foto o iniciales (el negocio si la red es de
la marca), pastilla de reservas con enlace corto cuando la voz invita de verdad ('link aqui
abajo', 'agenda', 'pide tu cita'; nunca 'los dientes de abajo') y >= 1.5 s, tarjeta final >= 2.2 s
con pila alineada (ubicacion, Seguir, chips de las demas redes, reservas) o, si a camara no cabe,
escena corta con el video desenfocado; notas de lo que no cupo; marca de agua opcional que se
aparta de los titulos. Foto y logo de cada enlace (profile.link_media, descargados solos por el
servidor o subidos): Seguir con la foto de SU red (la foto subida del perfil manda), pastilla de
reservas con la foto de la pagina + insignia del logo (o solo el logo), tarjetas que nombran la
plataforma (horarios 'En PaseoClub') con `platform` + `logo` y chips con el logo de las paginas sin
icono propio. apply_profile_media(plan, perfil) las pone sin mover nada (misma geometria).
Efectos de recorte (bg_replace, color_pop, subject_outline): el director NO los propone solo (AUTO_MATTE_FX: el borde
del recorte deja halos); siguen disponibles a mano en el Studio y un plan que ya los traia los
conserva al aplicar el perfil (redirect keep_rev).
Videos LARGOS (> LONG_S; caso real: 19 min 4K H.264, 2541 palabras, antes 8 min y todo
'heuristico'): presupuesto sub-lineal (director_budget: raiz de la duracion, tope 4.5x); escaneo
con el decodificador mas rapido por codec (H.264 por software, HEVC con VideoToolbox), cadencia de
cuadros medianos acotada (MED_MAX) y avance real en la barra; guion por TRAMOS de oraciones
completas (llm_windows, ~LLM_WIN_WORDS palabras) desde el segundo 0 en paralelo al escaneo, cada
uno con prompt/num_predict/timeout acotados y cache propia, unidos de forma determinista
(merge_windows; un tramo que falla deja solo SUS frases con heuristicas y los ya hechos se usan
aunque el resto no llegue); VLM por GRUPOS de tomas casi iguales (shot_groups: b-roll primero, una
descripcion por grupo reusada en sus tomas, tope vlm_max_calls y piso VLM_LONG_MIN tras un escaneo
largo); turno FIFO del modelo (_ModelTurn) entre tramos y VLM. director.timings dice lo hecho
(budget_s, scan_ms/decoder, faces_ms, vlm_groups/calls/reused/missing, llm_windows/ok/cached).
Opcional (`trim=True`): el director propone recortes `cuts` con src "ai" (b-roll mudo largo,
silencios > SILENCE_MAX, aire muerto inicial > LEAD_MAX, cola `tail`); el Studio puede
desactivarlos con `enabled: false`.
"""
from __future__ import annotations

import base64
import bisect
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
from . import linkmedia as LM
from . import profile as PR
from .ctx import SAFE, FrameCtx

try:  # regla de simetria (ctx.XC / ctx.SYM_W)
    from .ctx import SYM_W, XC as SYM_X
except ImportError:  # pragma: no cover
    SYM_X, SYM_W = 0.5, 2 * (SAFE["right"] - 0.5)

try:  # dentro del motor (scripts/ en sys.path)
    import paths as _paths
except Exception:  # pragma: no cover
    _paths = None

PROMPT_VERSION = "d6"
SHOTS_VERSION = "s9"          # analisis de tomas (rostro por tiempo, calidad del recorte, fondo por celdas)
TRACKS = ("text", "graphics", "cutaways", "effects", "transitions", "sfx")
ID_PREFIX = {"text": "tx", "graphics": "gr", "cutaways": "cw", "effects": "fx",
             "transitions": "tr", "sfx": "sf"}
# efectos que dependen del RECORTE de la persona (fondo reemplazado, sujeto a color): el borde del
# matte deja halos y fugas, asi que el director ya no los propone solo (quedan a mano en el Studio).
# Un plan que ya los traia de la IA los conserva al re-aplicar el perfil (redirect keep_rev).
AUTO_MATTE_FX = False
MATTE_FX = ("bg_replace", "color_pop")

# ---------------------------------------------------------------- catalogo (seccion 3.1)
TEXT_PRESETS = ("title_pop", "stack", "cascade", "serif_elegant", "script_accent", "behind_giant",
                "highlight_marker", "typewriter", "counter", "kicker")
CAPTION_PRESETS = ("clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps", "none")
TEXT_ANIM_IN = ("pop", "rise", "slide_left", "slide_right", "type", "mask", "fade", "scale_down",
                "word_stagger", "letter_stagger")
ANIM_OUT = ("fade", "pop_out", "slide_up", "none")
GRAPHIC_ANIM_IN = ("pop", "draw", "fade", "rise", "scale_down", "slide_left", "slide_right", "none")
GRAPHIC_KINDS = ("doodle", "icon", "card", "lower_third", "cta", "progress", "sticker_text", "media",
                 "socials")
DOODLES = ("sparkle", "star4", "star5", "burst", "rays", "arrow_curve", "arrow_straight",
           "arrow_down", "circle_scribble", "underline", "squiggle", "dotted_path", "heart",
           "check", "cross", "exclaim", "question", "swirl", "lightning", "loop")
CARD_VARIANTS = ("price", "location", "schedule", "link", "phone", "rating", "checklist", "stat",
                 "generic", "booking")
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
# campos del catalogo 3.1 + extensiones que los modulos ya implementan (reportes de la ola 1):
# text kicker/rotation/case; graphics map (tarjeta de ubicacion sin mapa), button/subtitle;
# effects punch mode/release/target, bg_replace grain/vignette; transitions x/y (rostro), ring,
# mode, peak, gate, intensity; sfx dur (revelado / trazo / anticipacion del riser), variant, pitch.
FIELDS = {
    "text": _COMMON | {"preset", "text", "lines", "x", "y", "size", "font", "color", "accent",
                       "accent_words", "align", "stroke", "shadow", "anim_in", "anim_out",
                       "behind", "box", "from", "to", "prefix", "suffix", "decimals", "kicker",
                       "rotation", "case", "anchor", "count_dur", "kicker_scale", "word_sync"},
    "graphics": _COMMON | {"kind", "x", "y", "size", "color", "anim_in", "anim_out", "rotation",
                           "name", "stroke", "draw", "wiggle", "path_to", "variant", "badge",
                           "badge_color", "title", "subtitle", "value", "items", "icon", "theme",
                           "label", "handle", "thickness", "text", "bg", "fg", "map", "bend",
                           "shadow", "button", "arrow_color", "aspect", "double", "arrow_scale",
                           "button_color", "avatar", "style", "media", "radius", "border",
                           "border_color", "fit", "caption", "platform", "display", "initials",
                           "links", "layout", "icon_style", "stagger", "opacity", "width", "cols",
                           "backdrop", "align", "logo"},
    "cutaways": _COMMON | {"kind", "background", "bg_colors", "text", "graphics", "src_t",
                           "zoom_from", "zoom_to", "card", "anim_in", "anim_out", "media", "caption",
                           "tilt", "size", "pan", "in_dur", "out_dur"},
    "effects": _COMMON | {"kind", "from", "to", "easing", "amount", "hold", "amp", "freq", "decay",
                          "pan", "contrast", "subject_color", "bg_desat", "bg_dark", "radius",
                          "background", "bg_colors", "color", "width", "glow", "mode", "release",
                          "target", "fade_in", "fade_out", "grain", "vignette", "tone", "lift"},
    "transitions": _EVENT | {"kind", "dur", "dir", "color", "align", "x", "y", "ring", "mode",
                             "peak", "gate"},
    "sfx": _EVENT | {"name", "gain_db", "pan", "dur", "variant", "pitch"},
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
#   broll_keep  : segundos de b-roll mudo que se conservan al proponer recortes (trim); el que
#                 lleva la tarjeta de ubicacion conserva CARD_KEEP y el del final END_KEEP.
#   tail        : segundos que quedan despues de la ultima palabra (remate del CTA final).
#   flash       : escenas relampago (title_card de ~1 s con icono que se dibuja) por video.
STYLES: dict[str, dict] = {
    "dinamico": {
        # subtitulos con presencia (bold blanco, sombra suave, palabra activa en acento),
        # iguales en todo el video: 0.064 W = 69 px en 1080
        "captions": {"preset": "clean_bold", "y": 0.73, "size": 0.064,
                     "font": "Poppins-ExtraBold", "case": "none", "max_words": 3},
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
                  "bpm": 112, "gain_db": -14.0, "duck_db": -9.0, "mood": "upbeat"},
        "doodles": ["sparkle", "star4", "arrow_curve", "underline", "circle_scribble", "burst",
                    "squiggle", "star4"],
        "backgrounds": ["rays", "grid_warp", "dark_stars"],
        "drama": {"effect": "bw", "contrast": 1.35, "giant": True, "shake": False,
                  "rgb": False, "flash": True, "sfx": "impact_boom"},
        "hook_bg": "grid_warp",
        "scenes": {"budget": (0.12, 0.3), "bg_replace": ["paper", "grid_warp"], "bg_blur": True,
                   "outline": False},
        "gap": (4.0, 2.0), "coverage": (0.3, 0.85), "cutaways": 1.6, "punch": 1.0,
        "accent_p": (0.45, 0.95), "zoom_face": 0.125, "jump_zoom": 0.14, "sfx_db": 0.0,
        "progress": False, "color_pop": True, "broll_keep": 1.6, "punch_mode": "cut",
        "price": "overlay", "marks": 0.75, "flash": 1, "tail": 1.9,
    },
    "elegante": {
        "captions": {"preset": "clean_bold", "y": 0.73, "size": 0.058,
                     "font": "Poppins-Bold", "case": "none", "max_words": 3},
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
                  "bpm": 88, "gain_db": -17.0, "duck_db": -8.0, "mood": "inspiring", "intro_fade": 0.6},
        "doodles": ["sparkle", "underline", "swirl", "star4", "loop"],
        "backgrounds": ["gradient", "paper"],
        "drama": {"effect": "bw", "contrast": 1.2, "giant": False, "shake": False,
                  "rgb": False, "flash": False, "sfx": "sub_drop"},
        "hook_bg": None,
        "scenes": {"budget": (0.06, 0.18), "bg_replace": ["gradient"], "bg_blur": True,
                   "outline": False},
        "gap": (4.5, 2.6), "coverage": (0.25, 0.7), "cutaways": 0.7, "punch": 0.6,
        "accent_p": (0.3, 0.7), "zoom_face": 0.115, "jump_zoom": 0.1, "sfx_db": -2.0,
        "progress": False, "color_pop": False, "broll_keep": 2.2, "price": "overlay", "marks": 0.35,
        "flash": 0, "tail": 2.0,
    },
    "minimal": {
        "captions": {"preset": "clean_bold", "y": 0.73, "size": 0.06,
                     "font": "Montserrat-ExtraBold", "case": "none", "max_words": 3},
        "presets": [("title_pop", 3), ("highlight_marker", 2)],
        "companion": ["kicker"],
        "hook_presets": ["title_pop"],
        "humor_preset": "title_pop",
        "transitions": [("flash", 2), ("slide", 2), ("zoom_blur", 1)],
        "scene_trans": [("flash", 1)],
        "trans_dur": 0.8,
        "look": {"look": "natural", "vignette": 0.08, "grain": 0.0, "sharpen": 0.1},
        "music": {"prompt": "minimal ambient electronic instrumental, soft pulses, clean and airy",
                  "bpm": 100, "gain_db": -17.0, "duck_db": -8.0, "mood": "chill", "intro_fade": 0.5},
        "doodles": ["underline", "sparkle"],
        "backgrounds": ["color"],
        "drama": {"effect": None, "contrast": 1.2, "giant": False, "shake": False,
                  "rgb": False, "flash": False, "sfx": "impact_soft"},
        "hook_bg": None,
        "scenes": {"budget": (0.0, 0.06), "bg_replace": [], "bg_blur": True, "outline": False},
        "gap": (5.5, 3.2), "coverage": (0.12, 0.4), "cutaways": 0.0, "punch": 0.5,
        "accent_p": (0.15, 0.4), "zoom_face": 0.11, "jump_zoom": 0.08, "sfx_db": -4.0,
        "progress": False, "color_pop": False, "broll_keep": 2.0, "price": "card", "marks": 0.3,
        "flash": 0, "tail": 1.8,
    },
    "energetico": {
        "captions": {"preset": "bold_caps", "y": 0.725, "size": 0.056,
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
                  "bpm": 128, "gain_db": -14.0, "duck_db": -10.0, "mood": "upbeat"},
        "doodles": ["burst", "lightning", "exclaim", "star5", "rays", "arrow_straight", "squiggle"],
        "backgrounds": ["rays", "halftone", "grid_warp", "dark_stars"],
        "drama": {"effect": "bw", "contrast": 1.45, "giant": True, "shake": True,
                  "rgb": True, "flash": True, "sfx": "impact_boom"},
        "hook_bg": "rays",
        "scenes": {"budget": (0.16, 0.34), "bg_replace": ["rays", "halftone"], "bg_blur": False,
                   "outline": True},
        "gap": (3.0, 1.4), "coverage": (0.45, 0.95), "cutaways": 1.4, "punch": 1.3,
        "accent_p": (0.6, 1.0), "zoom_face": 0.135, "jump_zoom": 0.18, "sfx_db": 1.0,
        "progress": True, "color_pop": True, "broll_keep": 1.4, "punch_mode": "cut",
        "price": "scene", "marks": 0.8, "flash": 2, "tail": 1.7,
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
    r"\b(a (?:\w+ )?(?:cuadras?|minutos?|pasos?|metros?) de(?:l| la| el| los| las)?|"
    r"frente a(?:l| la| el)?|enfrente de(?:l| la| el)?|junto a(?:l| la| el)?|cerca de(?:l| la| el)?|"
    r"a un lado de(?:l| la| el)?|esquina con)\s+(.+)$", re.I)
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
    ({"nutricion", "dieta", "nutriologa", "nutriologo", "calorias"}, "Nutrición"),
    ({"abogado", "abogada", "demanda", "legal", "juicio"}, "Abogado"),
    ({"entrenamiento", "gym", "gimnasio", "rutina", "musculo"}, "Coach fitness"),
    ({"barberia", "barbero", "barba", "fade", "navaja", "degradado"}, "Barbero"),
    ({"cabello", "corte", "tinte", "salon", "unas", "manicure"}, "Estilista"),
    ({"casa", "departamento", "inmueble", "renta", "credito", "hipoteca"}, "Bienes raíces"),
    ({"piel", "facial", "acne", "dermatologa", "dermatologo"}, "Dermatología"),
    ({"perfume", "perfumes", "fragancia", "fragancias", "colonia", "aroma", "notas", "perfumeria",
      "decants", "decant"}, "Perfumería"),
    ({"cafe", "restaurante", "platillo", "menu", "comida", "tacos", "postre"}, "Restaurante"),
    ({"tienda", "envio", "envios", "pedido", "pedidos", "producto", "productos", "catalogo"}, "Tienda"),
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
    "hand": "hand-finger", "touch": "hand-finger", "feel": "mood-smile", "tongue": "mood-tongue",
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
                 "disponibles": "calendar-event", "caminar": "walk", "cerca": "map-pin",
                 # vocabulario general de servicios (cualquier negocio, no un guion en particular)
                 "ven": "eye", "ver": "eye", "vean": "eye", "vista": "eye", "ojos": "eye",
                 "corte": "scissors", "cortes": "scissors", "cortar": "scissors", "tijeras": "scissors",
                 "barba": "moustache", "bigote": "moustache", "navaja": "razor", "afeitado": "razor",
                 "rasurado": "razor", "cejas": "eye", "cabello": "brush", "peinado": "brush",
                 "dieta": "salad", "ensalada": "salad", "verduras": "carrot", "fruta": "apple",
                 "frutas": "apple", "agua": "droplet", "hidratacion": "droplet", "sueno": "moon",
                 "dormir": "moon", "descanso": "zzz", "ejercicio": "run", "entrenar": "barbell",
                 "pesas": "barbell", "gimnasio": "barbell", "peso": "scale", "kilos": "scale",
                 "comida": "tools-kitchen-2", "cocina": "chef-hat", "cafe": "coffee", "pan": "bread",
                 "huevo": "egg", "carne": "meat", "pescado": "fish", "medicina": "pill",
                 "pastillas": "pill", "vacuna": "vaccine", "masaje": "massage", "yoga": "yoga",
                 "blanqueamiento": "sparkles", "ortodoncia": "dental", "brackets": "dental",
                 "resina": "dental", "carilla": "dental", "carillas": "dental", "implante": "dental",
                 "consulta": "stethoscope", "revision": "stethoscope",
                 "muela": "dental", "muelas": "dental", "caries": "dental", "sonrisa": "mood-smile",
                 "envio": "truck", "envios": "truck", "regalo": "gift", "descuento": "discount",
                 "promocion": "discount", "oferta": "tag", "garantia": "shield-check",
                 "seguro": "shield-check", "calidad": "award", "experiencia": "award",
                 "certificado": "certificate", "curso": "school", "clases": "school",
                 "libro": "book", "musica": "music", "fotos": "camera", "video": "video",
                 "casa": "home", "hogar": "home", "auto": "car", "carro": "car",
                 "whatsapp": "brand-whatsapp", "mensaje": "message-circle", "llamada": "phone",
                 "rapido": "bolt", "energia": "bolt", "salud": "heartbeat", "corazon": "heart",
                 "familia": "users", "equipo": "users", "ninos": "friends", "mascota": "paw",
                 "perro": "dog", "gato": "cat", "sol": "sun", "idea": "bulb", "meta": "target",
                 "resultados": "chart-bar", "crecer": "trending-up", "lista": "list-check"}
# lemas por prefijo (sentir -> sientes/sienten/siento, ver -> ves/ven...)
ICON_STEMS = (("sient", "mood-smile"), ("senti", "mood-smile"), ("toca", "hand-finger"),
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
    "Nutrición": ["salad", "apple", "scale", "heartbeat", "leaf", "calendar-event", "map-pin"],
    "Abogado": ["scale", "file-text", "shield-check", "briefcase", "calendar-event", "map-pin"],
    "Coach fitness": ["barbell", "flame", "heartbeat", "trophy", "calendar-event", "map-pin"],
    "Estilista": ["scissors", "sparkles", "brush", "heart", "calendar-event", "map-pin"],
    "Bienes raíces": ["home", "key", "building", "map-pin", "calendar-event", "cash"],
    "Dermatología": ["sparkles", "droplet", "sun", "shield-check", "calendar-event", "map-pin"],
    "Perfumería": ["perfume", "sparkles", "flower", "droplet", "gift", "diamond", "shopping-bag"],
    "Restaurante": ["tools-kitchen-2", "coffee", "flame", "star", "map-pin", "clock"],
    "Tienda": ["building-store", "shopping-bag", "package", "truck-delivery", "tag", "gift"],
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


# ---------------------------------------------------------------- videos largos
# Un video corto (<= LONG_S) se dirige EXACTAMENTE como siempre. En uno largo (19 min, 4K) el
# presupuesto crece sub-lineal con la duracion, el guion va por tramos desde el segundo 0 (en
# paralelo al escaneo) y el VLM describe tomas representativas (agrupa las casi iguales).
LONG_S = 150.0            # s de video desde los que el director entra en modo largo
# Montaje de varios clips (Armar con IA): ollama atiende UNA peticion a la vez y el guion (una sola
# llamada de ~35 s) llegaba antes que las descripciones del VLM, que agotaban su plazo en la cola (0 de
# 12 camaras descritas en el caso real). En montaje el VLM va primero (una descripcion por camara) y
# el guion despues, con este tiempo extra de presupuesto.
MONTAGE_EXTRA_S = 40.0
BUDGET_MAX_X = 4.5        # tope del presupuesto largo (x el presupuesto base)


def director_budget(dur: float) -> float:
    """Presupuesto (s) del director: REELFY_DIRECTOR_BUDGET (50 s) en videos cortos; en largos
    crece con la RAIZ de la duracion (5 min ~91 s, 10 min ~129 s, 19 min ~178 s, tope 4.5x)."""
    base = float(os.environ.get("REELFY_DIRECTOR_BUDGET", "50"))
    if dur <= LONG_S:
        return base
    return min(base * BUDGET_MAX_X, base * math.sqrt(dur / 90.0))



# ---------------------------------------------------------------- ollama (LLM / VLM locales)
def _ollama_url() -> str:
    return (os.environ.get("REELFY_OLLAMA_URL") or "http://127.0.0.1:11498").rstrip("/")


def _llm_model() -> str:
    return os.environ.get("REELFY_LLM_MODEL") or "qwen3.5:4b"


def _vlm_model() -> str:
    return os.environ.get("REELFY_VLM_MODEL") or _llm_model()


_AVAIL: dict[str, tuple[float, bool]] = {}


def llm_available(model: str | None = None, timeout: float = 2.0) -> bool:
    """True si ollama responde y tiene el modelo (cache de 30 s si responde; un fallo solo se
    recuerda 6 s). Con la maquina cargada /api/tags puede tardar: se reintenta una vez con mas
    tiempo antes de caer a heuristicas (un video entero sin LLM ni VLM por un timeout de 2 s)."""
    model = model or _llm_model()
    key = _ollama_url() + "|" + model
    hit = _AVAIL.get(key)
    if hit and time.time() - hit[0] < (30 if hit[1] else 6):
        return hit[1]
    ok = False
    for k, to in enumerate((timeout, max(6.0, timeout * 3))):
        try:
            with urllib.request.urlopen(_ollama_url() + "/api/tags", timeout=to) as r:
                names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
            base = model.split(":")[0]
            ok = model in names or any(n.split(":")[0] == base and model.endswith(":latest") for n in names)
            break
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


def _probe_codec(video: str) -> str:
    """Codec del primer stream de video ('h264', 'hevc'...) o '' si no se pudo leer."""
    try:
        return subprocess.run([_ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=codec_name", "-of", "csv=p=0", str(video)], capture_output=True,
                              text=True, timeout=20).stdout.strip().split("\n")[0].split(",")[0].strip().lower()
    except Exception:
        return ""


# cuadros medianos (rostro, fondo, VLM) que guarda el escaneo como maximo: 2 fps hasta ~12 min; en
# videos mas largos baja la cadencia (19 min a 2 fps serian ~1.6 GB de cuadros en memoria)
MED_MAX = 1500
# codecs que el decodificador por software de ffmpeg (multinucleo) lee MAS rapido que VideoToolbox:
# H.264 4K 30 fps (74 Mb/s) a ~10x tiempo real contra ~2.4x con VT (salida identica bit a bit). HEVC
# (iPhone, 10 bits) sigue con VT: por software es ~2.5x mas lento que VT
SW_FIRST = ("h264",)


def _scan_decoders(video: str) -> list[list[str]]:
    vt = ["-hwaccel", "videotoolbox"] if (_paths and getattr(_paths, "IS_MAC", False)) else []
    if not vt:
        return [[]]
    return [[], vt] if _probe_codec(video) in SW_FIRST else [vt, []]


def _frame_diffs(small: np.ndarray, chunk: int = 4096) -> np.ndarray:
    """Diferencia media entre cuadros consecutivos (uint8 -> float32 por bloques: mismo
    resultado que sobre todo el arreglo, sin tener el video entero en float32)."""
    n = len(small)
    if n < 2:
        return np.zeros(0, np.float32)
    out = []
    for a in range(0, n - 1, chunk):
        blk = small[a:min(n, a + chunk + 1)].astype(np.float32)
        out.append(np.abs(np.diff(blk, axis=0)).mean(axis=(1, 2)))
    return np.concatenate(out)


def _run_scan(cmd: list[str], timeout: float, on_progress: Callable | None = None,
              dur: float = 0.0) -> tuple[int, bytes]:
    """Corre el ffmpeg del escaneo -> (codigo, stdout). Con `on_progress(frac)` (video largo: el
    escaneo tarda minutos y la barra no debe parecer congelada) lee el avance de `-progress` en
    stderr en otro hilo mientras drena stdout por bloques; un temporizador mata ffmpeg al pasar
    `timeout`."""
    if on_progress is None:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return res.returncode, res.stdout
    proc = subprocess.Popen(cmd[:1] + ["-progress", "pipe:2", "-nostats"] + cmd[1:],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    state = {"t": 0.0}

    def _err():
        for line in proc.stderr:
            if line.startswith((b"out_time_us=", b"out_time_ms=")):
                try:
                    state["t"] = int(line.split(b"=", 1)[1]) / 1e6
                except ValueError:
                    pass
    th = threading.Thread(target=_err, name="director-scan-progress", daemon=True)
    th.start()
    killer = threading.Timer(timeout, proc.kill)
    killer.start()
    chunks, last = [], -1.0
    try:
        while True:
            b = proc.stdout.read(1 << 20)
            if not b:
                break
            chunks.append(b)
            frac = min(1.0, state["t"] / dur) if dur > 0 else 0.0
            if frac - last >= 0.02:
                last = frac
                try:
                    on_progress(frac)
                except Exception:
                    pass
        proc.wait()
    finally:
        killer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        th.join(2.0)
    return proc.returncode, b"".join(chunks)


def scan_video(video: str, duration: float | None = None, on_progress: Callable | None = None) -> dict:
    """UNA pasada de decodificacion: cuadros diminutos a 30 fps (cortes, movimiento) y
    cuadros medianos a 2 fps (rostro y VLM; menos en videos de mas de MED_MAX / 2 s).
    Decodifica con VideoToolbox o por software segun el codec (_scan_decoders).
    `on_progress(frac)`: avance 0..1 de la decodificacion (opcional)."""
    import cv2
    t0 = _now()
    video = str(video)
    dur = float(duration or 0.0) or _probe_duration(video)
    med_fps = 2.0 if dur <= MED_MAX / 2.0 else MED_MAX / dur
    med_f = "2" if med_fps == 2.0 else f"{med_fps:.6f}"
    tmpd = Path(tempfile.mkdtemp(prefix="reelfy_dir_", dir=str(_work_dir())))
    small = None
    used = ""
    try:
        for hw in _scan_decoders(video):
            cmd = [_ffmpeg(), "-v", "error", "-y", *hw, "-i", video, "-filter_complex",
                   "[0:v]split=2[a][b];[a]fps=30,scale=48:48:flags=area,format=gray[s];"
                   f"[b]fps={med_f},scale='if(gt(iw,ih),640,-2)':'if(gt(iw,ih),-2,640)',format=yuvj420p[m]",
                   "-map", "[s]", "-f", "rawvideo", "pipe:1",
                   "-map", "[m]", "-q:v", "3", str(tmpd / "m_%04d.jpg")]
            code, raw = _run_scan(cmd, max(600.0, dur * 1.5), on_progress, dur)
            if code == 0 and raw:
                small = np.frombuffer(raw, np.uint8).reshape(-1, 48, 48)
                used = "videotoolbox" if hw else "software"
                break
            for f in tmpd.glob("m_*.jpg"):
                f.unlink()
        if small is None:
            raise RuntimeError("no se pudo decodificar el video")
        files = sorted(tmpd.glob("m_*.jpg"))
        med = [cv2.cvtColor(cv2.imread(str(f)), cv2.COLOR_BGR2RGB) for f in files]
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    diffs = _frame_diffs(small)
    if dur <= 0:
        dur = len(small) / 30.0
    return {"diff": diffs, "fps": 30.0, "med": med, "med_t": [j / med_fps for j in range(len(med))],
            "duration": dur, "ms": (_now() - t0) * 1000, "decoder": used, "med_fps": _r(med_fps, 4)}


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
        track: list[list[float]] = []
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
            track.append([round(float(sc["med_t"][j]), 2)] + [round(v, 4) for v in boxes[-1]])
        ratio = len(boxes) / max(1, len(idx))
        if boxes:
            a = np.array(boxes)
            face = [float(v) for v in np.median(a, axis=0)]
            spread = float(np.std(a[:, 0]))
        else:
            face, spread = None, 0.0
        out.append({"face": face, "face_ratio": ratio, "face_spread": spread, "track": track})
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


COLS = 4       # columnas del perfil de fondo por celdas (ROWS x COLS)


def _shot_cells(sc: dict, s: dict) -> list[list[list[int]]] | None:
    """Perfil de fondo por CELDAS (ROWS filas x COLS columnas, todo el ancho): el color medio
    local bajo un garabato o una flecha (un gabinete blanco junto a una silla oscura no se
    promedia con ella). [fila][columna] = [r, g, b] sRGB 0-255, mediana en el tiempo."""
    idx = [j for j, t in enumerate(sc.get("med_t") or []) if s["start"] + 0.1 <= t <= s["end"] - 0.1]
    if not idx and sc.get("med_t"):
        mid = (s["start"] + s["end"]) / 2
        idx = [min(range(len(sc["med_t"])), key=lambda j: abs(sc["med_t"][j] - mid))]
    prof = []
    for j in idx[:24]:
        img = sc["med"][j].astype(np.float32)
        h, w = img.shape[:2]
        grid = []
        for r in range(ROWS):
            y0, y1 = int(h * r / ROWS), max(int(h * r / ROWS) + 1, int(h * (r + 1) / ROWS))
            grid.append([img[y0:y1, int(w * c / COLS):max(int(w * c / COLS) + 1, int(w * (c + 1) / COLS))]
                         .mean(axis=(0, 1)) for c in range(COLS)])
        prof.append(grid)
    if not prof:
        return None
    med = np.median(np.array(prof), axis=0)
    return [[[int(round(v)) for v in cell] for cell in row] for row in med]


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


MATTE_OK = 0.94     # calidad minima de la mascara (1 = limpia) para contorno / reemplazo de fondo


def _matte_quality(video: str, s: dict) -> float | None:
    """Calidad estimada de la mascara del sujeto en una toma (misma medida que
    matte.MatteProvider.quality_of: 1 - fraccion de la mascara MODNet que la compuerta de persona
    tuvo que quitar; baja cuando la persona toca muebles grandes). Dos cuadros de la toma a
    960 px. None si no hay modelos o no hay cara."""
    try:
        import cv2
        from . import matte as M
        if not (M.model_ready() and M.model_ready(which="person")):
            return None
    except Exception:
        return None
    qs = []
    a, b = float(s["start"]), float(s["end"])
    for frac in (0.3, 0.7):
        rgb = _grab_frame(video, a + (b - a) * frac, 960)
        if rgb is None:
            continue
        try:
            H, W = rgb.shape[:2]
            faces = sorted([f for f in M.detect_faces(rgb) if f[2] >= 0.025], key=lambda f: -f[2] * f[3])[:1]
            if not faces:
                continue
            cx, cy, fw, fh, _sc = faces[0]
            x0, x1 = max(0.0, (cx - 3.3 * fw) * W), min(float(W), (cx + 3.3 * fw) * W)
            y0 = max(0.0, (cy - 2.2 * fh) * H)
            if x1 - x0 < 0.5 * W:
                c = (x0 + x1) / 2
                x0, x1 = max(0.0, c - W / 4), min(float(W), c + W / 4)
            x0, y0, x1 = int(x0), int(y0), int(math.ceil(x1))
            cw, ch = x1 - x0, H - y0
            k = min(1.0, math.sqrt(384 * 672 / max(1, cw * ch)))
            iw, ih = max(32, int(round(cw * k / 32)) * 32), max(32, int(round(ch * k / 32)) * 32)
            x = cv2.resize(rgb[y0:H, x0:x1], (iw, ih), interpolation=cv2.INTER_AREA)
            m = M.infer(x, 2)
            pp = M.person_prob(rgb)
            pf = cv2.resize(pp, (W, H), interpolation=cv2.INTER_LINEAR)[y0:H, x0:x1]
            pc = cv2.resize(pf, (iw, ih), interpolation=cv2.INTER_LINEAR)
            fc = [((cx * W - x0) / cw, (cy * H - y0) / ch, fw * W / cw, fh * H / ch, 1.0)]
            _g, rem = M.gate_matte(m, fc, pc, 16.0 * (W / 1080.0) * (iw / cw))
            qs.append(1.0 - float(rem))
        except Exception:
            continue
    return _r(float(np.mean(qs)), 3) if qs else None


_VLM_STATUS: dict[str, str] = {}     # video -> causa por la que el VLM no describio todas las tomas

# ---- tomas de un video largo: el VLM describe UNA toma por grupo de tomas casi iguales
SIM_ROWS = 14.0      # diferencia media de color (0-255) del perfil de fondo bajo la que dos tomas son "la misma"
MATTE_LONG_S = 20.0  # s maximos midiendo la calidad del recorte en un video largo (una medicion por grupo)
VLM_LONG_MIN = 25.0  # s de VLM garantizados tras el escaneo de un video largo (aunque se pase del presupuesto)


def _similar(a: dict, b: dict) -> bool:
    """Dos tomas casi iguales (mismo lugar y encuadre): mismo si/no de rostro, rostro en el mismo
    sitio y de tamano parecido, y perfil de color del fondo (rows) a menos de SIM_ROWS."""
    fa, fb = a.get("face"), b.get("face")
    if bool(fa) != bool(fb):
        return False
    if fa and fb and (abs(fa[0] - fb[0]) > 0.08 or abs(fa[1] - fb[1]) > 0.08
                      or abs(fa[3] - fb[3]) > 0.25 * max(fa[3], fb[3])):
        return False
    ra, rb = a.get("rows"), b.get("rows")
    if not ra or not rb or len(ra) != len(rb):
        return False
    return float(np.abs(np.asarray(ra, np.float32) - np.asarray(rb, np.float32)).mean()) < SIM_ROWS


def shot_groups(out: list[dict]) -> list[list[int]]:
    """Grupos de tomas casi iguales (_similar) en orden de prioridad para el VLM: primero el b-roll
    (sus letreros y lugar alimentan el guion), luego lo mas largo. El primero de cada grupo es su
    representante (la toma que se describe)."""
    order = sorted(range(len(out)), key=lambda i: (bool(out[i].get("face")),
                                                   -(float(out[i]["end"]) - float(out[i]["start"])), i))
    groups: list[list[int]] = []
    for i in order:
        for g in groups:
            if _similar(out[g[0]], out[i]):
                g.append(i)
                break
        else:
            groups.append([i])
    return groups


# campos del montaje con IA que viajan con cada toma (plan.shots[i]): la union con la toma anterior
# (section|line|splice), el setup de camara (mismo lugar/encuadre) y su slot/candidato del guion.
# 'section' admite transicion; 'line' y 'splice' son corte seco (sin transicion ni whoosh)
HARD_JOINS = ("line", "splice")
# recursos del guion (fx.media): ninguna transicion a menos de esto (s, tiempo final) de su tarjeta
MEDIA_TRANS_PAD = 0.45
MEDIA_SFX = "swoosh_up"          # entrada de la tarjeta flotante: aire suave (la tarjeta sube)
SHOT_CARRY = ("join", "setup", "slot", "cand", "sid", "ramp", "ramp_in", "ramp_out")


def _ramp_on(v: Any) -> bool:
    """Rampa activa: True o un dict ({dur, speed, side...}) que no la apaga (enabled/on False o
    dur <= 0). False / None / ausente = sin rampa (el interruptor del usuario manda)."""
    if isinstance(v, dict):
        if v.get("enabled") is False or v.get("on") is False or v.get("ramp") is False:
            return False
        try:
            return float(v.get("dur", 0.3) or 0.0) > 0.0
        except (TypeError, ValueError):
            return True
    return v is True


def ramp_join(shots: list[dict], k: int) -> bool:
    """La union que ENTRA a shots[k] es un cambio de camara con RAMPA de velocidad (solo video,
    audio a 1x): shots[k].ramp o .ramp_in, o shots[k-1].ramp_out. La rampa ES la transicion: ahi
    no va otra transicion ni whoosh. Un `ramp: false` explicito en shots[k] la apaga."""
    if not (0 <= k < len(shots)) or not isinstance(shots[k], dict):
        return False
    s = shots[k]
    if s.get("ramp") is False:
        return False
    if _ramp_on(s.get("ramp")) or _ramp_on(s.get("ramp_in")):
        return True
    return k > 0 and isinstance(shots[k - 1], dict) and _ramp_on(shots[k - 1].get("ramp_out"))


def media_items(plan: dict) -> list[dict]:
    """Recursos del guion activos (plan.fx.media, tiempo ORIGINAL): contenido BLOQUEADO del
    usuario. El director no pone titulos, tarjetas, escenas, garabatos ni transiciones encima
    (los subtitulos se quedan)."""
    fx = plan.get("fx") if isinstance(plan.get("fx"), dict) else {}
    out = []
    for it in fx.get("media") or []:
        if not isinstance(it, dict) or it.get("enabled", True) is False:
            continue
        try:
            a, b = float(it["start"]), float(it["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            out.append(it)
    return out


def _keep_media(fx: dict, old: dict) -> dict:
    """fx.media del plan anterior pasa tal cual al plan nuevo (el director nunca lo toca)."""
    if isinstance(old, dict) and isinstance(old.get("media"), list):
        fx["media"] = copy.deepcopy(old["media"])
    return fx


def _keep_accent(fx: dict, old: dict, style: str | None = None) -> dict:
    """El color de acento del PROYECTO (fx.accent, lo elige el usuario; sin el, el amarillo de siempre)
    pasa al plan nuevo al dirigir, re-dirigir o aplicar el perfil: los subtitulos nuevos lo usan
    (accent y el resaltado que venia del estilo). El render lo usa como acento de la paleta y el
    servidor (_accent_apply) pasa a el lo que la IA pinto con el acento de su estilo."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str((old or {}).get("accent") or "").strip()) \
        if isinstance(old, dict) else None
    if not m:
        return fx
    acc = "#" + m.group(1).upper()
    fx["accent"] = acc
    cap = fx.get("captions")
    if isinstance(cap, dict):
        pal = str(assets.palette(style).get("accent") or "").upper()
        if str(cap.get("highlight") or "").upper() in {pal, "#FFD23F"} - {""}:
            cap["highlight"] = acc
        cap["accent"] = acc
    return fx


def _carry_montage(out: list[dict], shots_in: list[dict]) -> list[dict]:
    """Copia a cada toma analizada los campos del montaje de la toma de entrada (mismo indice) y
    quita los que ya no trae: una cache de otra direccion nunca deja joins viejos."""
    if len(out) != len(shots_in):
        return out
    for o, s in zip(out, shots_in):
        for k in SHOT_CARRY:
            if s.get(k) is not None:
                o[k] = s[k]
            else:
                o.pop(k, None)
    return out


def setup_groups(out: list[dict]) -> list[list[int]]:
    """Grupos por setup de camara del montaje (una descripcion del VLM por setup, que se reusa en
    sus tomas). El representante es la toma MAS LARGA del grupo (su cuadro medio es el mas
    estable); b-roll primero y luego lo mas largo, como shot_groups. Tomas sin setup: solas."""
    by: dict = {}
    order: list = []
    for i, o in enumerate(out):
        key = ("s", o["setup"]) if o.get("setup") is not None else ("i", i)
        if key not in by:
            by[key] = []
            order.append(key)
        by[key].append(i)

    def length(i):
        return float(out[i]["end"]) - float(out[i]["start"])
    groups = []
    for key in order:
        g = sorted(by[key], key=lambda i: (-length(i), i))
        groups.append(g)
    return sorted(groups, key=lambda g: (bool(out[g[0]].get("face")), -sum(length(i) for i in g), g[0]))


def vlm_max_calls(dur: float) -> int:
    """Tope de descripciones del VLM en un video largo: crece con la raiz de la duracion
    (5 min ~25, 19 min ~38)."""
    return int(12 + 6 * math.sqrt(max(0.0, dur) / 60.0))


def _vlm_groups(video: str, sc: dict, shots: list[dict], faces: list[dict], groups: list[list[int]],
                deadline: float | None, turn: "_ModelTurn | None", on_step, dur: float,
                st: dict) -> tuple[list, str]:
    """VLM de un video largo: describe el representante de cada grupo (b-roll primero, luego lo
    mas largo) y copia la descripcion a sus tomas casi iguales. Tope de llamadas (vlm_max_calls),
    de tiempo (12 s por grupo, raiz de la duracion, `deadline`) y corte tras 2 fallos seguidos.
    Lo que no alcanza queda con heuristicas (parcial > nada)."""
    vlm: list[dict | None] = [None] * len(shots)
    reps = groups[:vlm_max_calls(dur)]
    # 12 s por grupo + la espera del turno (un tramo del guion en curso), tope por la raiz de la
    # duracion; el `deadline` del director manda, pero si el escaneo ya se lo comio queda un piso
    # (VLM_LONG_MIN) para los grupos mas importantes: parcial > todo heuristico
    vdead = _now() + min(45.0 * math.sqrt(dur / 90.0), 12.0 * max(1, len(reps)) + 30.0)
    if deadline is not None:
        vdead = min(vdead, max(deadline, _now() + VLM_LONG_MIN))
    fails, described, cause = 0, 0, ""
    calls = [0]

    def _describe(img_, to):
        calls[0] += 1
        return _vlm_describe(img_, timeout=to)
    for gi, g in enumerate(reps):
        if vdead - _now() < 5.0:
            cause = f"tiempo agotado en el grupo {gi + 1}/{len(reps)} de tomas"
            break
        i = g[0]
        s = shots[i]
        mid = (s["start"] + s["end"]) / 2
        j = min(range(len(sc["med_t"])), key=lambda k: abs(sc["med_t"][k] - mid))
        img = sc["med"][j]
        if faces[i]["face_ratio"] < 0.3:      # b-roll: letreros -> mas resolucion para leer
            hi = _grab_frame(video, s["start"] + (s["end"] - s["start"]) * 0.35, 960)
            img = hi if hi is not None else img
        res = _turn_call(turn, vdead, max(5.0, min(30.0, vdead - _now())),
                         lambda to, im=img: _describe(im, to), min_s=5.0)
        _step(on_step, "tomas", 0.2 + 0.7 * (gi + 1) / len(reps), f"grupo {gi + 1}/{len(reps)}")
        if res is not None:
            described += 1
            for k in g:
                vlm[k] = res if k == i else dict(res, text_seen=list(res.get("text_seen") or []))
        fails = 0 if res is not None else fails + 1
        if fails >= 2 and gi + 1 < len(reps):
            cause = f"{fails} fallos seguidos (servidor ocupado) en el grupo {gi + 1}/{len(reps)} de tomas"
            break
    missing = sum(1 for v in vlm if v is None)
    if not cause and missing:
        cause = f"sin descripcion en {missing}/{len(shots)} tomas"
    st.update({"vlm_groups": len(groups), "vlm_calls": calls[0], "vlm_described": described,
               "vlm_reused": sum(1 for v in vlm if v is not None) - described, "vlm_missing": missing})
    return vlm, cause


def analyze_shots(plan: dict, use_llm: bool = True, on_step=None, scan: dict | None = None,
                  deadline: float | None = None, on_basic: Callable | None = None,
                  turn: "_ModelTurn | None" = None, stats: dict | None = None) -> list[dict]:
    """Tomas con campos del contrato (+ face/motion/rows). Nunca lanza: sin video -> una toma.
    `deadline` (time.perf_counter) limita el VLM: las tomas que no alcancen quedan con
    los campos heuristicos (rostro/encuadre), para que el director nunca pase de ~60 s.
    `on_basic(shots)` se llama en cuanto se conocen limites + rostro (antes del VLM): el
    director lanza ahi el LLM del guion con las tomas, en paralelo al VLM.
    Video largo (> LONG_S): el VLM describe una toma por GRUPO de tomas casi iguales (shot_groups;
    el resto reusa la descripcion), con tope de llamadas y turno del modelo (`turn`) compartido con
    el guion; la calidad del recorte se mide por grupo con tope de tiempo. `stats` recibe lo hecho
    (escaneo, rostros, VLM, recorte) para director.timings."""
    dur = _plan_duration(plan)
    stx = stats if stats is not None else {}
    video = plan.get("video")

    def basic(shots_):
        if on_basic:
            try:
                on_basic(shots_)
            except Exception:
                pass

    shots_in = [dict(s) for s in plan.get("shots") or [] if isinstance(s, dict) and "start" in s]
    # montaje con IA: tomas del mismo setup de camara = una sola descripcion del VLM
    by_setup = any(s.get("setup") is not None for s in shots_in)
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
                     PROMPT_VERSION, SHOTS_VERSION, _vlm_model() if use_llm else "none")
    except Exception:
        vkey = None
    if vkey:
        hit = _cache_get("shots", vkey)
        if hit:
            hit = _carry_montage(hit, shots_in)
            basic(hit)
            return hit
    _step(on_step, "tomas", 0.05, "analizando cortes")
    t = _now()
    # video largo: la decodificacion tarda minutos -> avance real en la barra (0.05 .. 0.18)
    prog = (lambda f: _step(on_step, "tomas", 0.05 + 0.13 * f, f"analizando el video {int(f * 100)}%")) \
        if (on_step and dur > LONG_S) else None
    sc = scan or scan_video(str(video), dur, on_progress=prog)
    stx["scan_ms"] = round((_now() - t) * 1000)
    if sc.get("decoder"):
        stx["scan_decoder"] = sc["decoder"]
    shots = shots_in or detect_shots(str(video), dur, sc)
    t = _now()
    faces = _shot_faces(sc, shots)
    stx["faces_ms"] = round((_now() - t) * 1000)
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
                    # rostro a 2 fps (t, cx, cy, w, h): para no tapar la cara cuando la persona camina
                    "face_track": (fa.get("track") or []) if fa["face"] and fa["face_ratio"] >= 0.3 else [],
                    "rows": _shot_rows(sc, s), "cells": _shot_cells(sc, s),
                    "speech": _r(_speech_ratio(plan, s["start"], s["end"]), 2)})
    if shots_in:
        _carry_montage(out, shots_in)
    basic([dict(o, framing=_framing_from_face(o["face"]), use="a_camara" if o["face"] else "broll",
                place="", text_seen=[]) for o in out])
    use_vlm = use_llm and llm_available(_vlm_model())
    vlm: list[dict | None] = [None] * len(shots)
    cause = ""
    long_mode = dur > LONG_S
    by_setup = by_setup and len(out) == len(shots_in)
    groups = setup_groups(out) if by_setup else \
        (shot_groups(out) if long_mode else [[i] for i in range(len(out))])
    t = _now()
    if use_vlm and sc["med"] and (long_mode or by_setup):
        _step(on_step, "tomas", 0.2, "describiendo tomas representativas (VLM)")
        vlm, cause = _vlm_groups(str(video), sc, shots, faces, groups, deadline, turn, on_step, dur, stx)
    elif use_vlm and sc["med"]:
        _step(on_step, "tomas", 0.2, "describiendo tomas (VLM)")
        # tope propio del VLM: 12 s por toma (maximo 45 s) y se corta tras 2 fallos seguidos (un
        # servidor saturado no gasta 2 minutos para acabar en heuristicas igual)
        vdead = _now() + min(45.0, 12.0 * max(1, len(shots)))
        if deadline is not None:
            vdead = min(vdead, deadline)
        fails = 0
        for i, s in enumerate(shots):
            if _now() > vdead:
                cause = f"tiempo agotado en la toma {i + 1}/{len(shots)}"
                break
            mid = (s["start"] + s["end"]) / 2
            j = min(range(len(sc["med_t"])), key=lambda k: abs(sc["med_t"][k] - mid))
            img = sc["med"][j]
            if faces[i]["face_ratio"] < 0.3:      # b-roll: letreros -> mas resolucion para leer
                hi = _grab_frame(str(video), s["start"] + (s["end"] - s["start"]) * 0.35, 960)
                img = hi if hi is not None else img
            vlm[i] = _vlm_describe(img, timeout=max(5.0, min(30.0, vdead - _now())))
            _step(on_step, "tomas", 0.2 + 0.7 * (i + 1) / len(shots), f"toma {i + 1}/{len(shots)}")
            fails = 0 if vlm[i] is not None else fails + 1
            if fails >= 2 and i + 1 < len(shots):
                cause = f"{fails} fallos seguidos (servidor ocupado) en la toma {i + 1}/{len(shots)}"
                break
        if not cause and any(v is None for v in vlm):
            cause = f"sin respuesta en {sum(1 for v in vlm if v is None)}/{len(shots)} tomas"
    elif use_llm and not use_vlm:
        cause = "modelo no disponible"
    if cause and video:
        if len(_VLM_STATUS) > 64:
            _VLM_STATUS.clear()
        _VLM_STATUS[str(video)] = cause
    if use_vlm and sc["med"]:
        stx["vlm_ms"] = round((_now() - t) * 1000)
    _step(on_step, "tomas", 0.93, "midiendo el recorte del sujeto")
    t = _now()
    if long_mode or by_setup:
        # una medicion por grupo (tomas casi iguales = mismo recorte) y tope de tiempo: sin medir =
        # None (como sin modelos: se permite)
        mdead = _now() + MATTE_LONG_S
        measured = 0
        for g in groups:
            o0 = out[g[0]]
            q = _matte_quality(str(video), o0) if o0["face"] and _now() < mdead else None
            measured += bool(o0["face"] and q is not None)
            for i in g:
                out[i]["matte_q"] = q if out[i]["face"] else None
        stx["matte_measured"] = measured
    else:
        for o in out:
            o["matte_q"] = _matte_quality(str(video), o) if o["face"] else None
    stx["matte_ms"] = round((_now() - t) * 1000)
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
            "face", "motion", "rows", "cells", "speech", "vlm", "matte_q", "face_track")
    out = [{**{k: o[k] for k in keys}, **{k: o[k] for k in SHOT_CARRY if o.get(k) is not None}}
           for o in out]
    # la cache con clave de VLM solo guarda descripciones REALES: si el VLM no respondio (ollama
    # ocupado) el resultado heuristico no se queda pegado para las siguientes direcciones
    if vkey and ((use_vlm and all(v is not None for v in vlm)) or not use_llm):
        _cache_put("shots", vkey, out)
    return out


# ---------------------------------------------------------------- paso 2: guion
def _word_cut_spans(plan: dict) -> list[tuple[float, float]]:
    """Tramos (fundidos y ordenados) de los cortes habilitados que quitan PALABRAS a proposito:
    tomas repetidas (src "takes"), manuales y del usuario. Los silencios no cuentan (con el timing
    crudo de Whisper una palabra dicha se estira sobre la pausa vecina y su punto medio cae en el
    silencio sin que la palabra se corte) ni los recortes de la IA (src "ai": el director los
    propone DESPUES de leer el guion y re-dirigir debe ver las mismas palabras)."""
    iv = []
    for c in plan.get("cuts") or []:
        if not isinstance(c, dict) or c.get("enabled", True) is False:
            continue
        if not (c.get("src") in ("takes", "user", "manual") or c.get("manual")):
            continue
        try:
            a, b = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            iv.append((a, b))
    out: list[list[float]] = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _mid_in_spans(spans: list[tuple[float, float]], a: float, b: float) -> bool:
    """El punto medio de [a, b] cae (estricto) dentro de un tramo de `spans` (fundidos y ordenados)."""
    if not spans:
        return False
    m = (a + b) / 2
    k = bisect.bisect_right(spans, (m, float("inf"))) - 1
    return k >= 0 and spans[k][0] < m < spans[k][1]


def _flatten_words(plan: dict) -> list[dict]:
    """Palabras del guion: las que quedan en el video. Fuera las de una toma repetida o un corte
    manual habilitado (su punto medio cae dentro): ni titulos ni enfasis sobre lo que se corto."""
    word_cuts = _word_cut_spans(plan)
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
            if _mid_in_spans(word_cuts, t0, max(t1, t0)):
                continue
            ws.append({"text": txt, "n": norm(txt), "t0": t0, "t1": max(t1, t0 + 0.02),
                       "kw": bool(w.get("kw")), "ph": pi})
    ws.sort(key=lambda w: (w["t0"], w["t1"]))
    # tiempos monotonos (el alineador a veces encima una calle y su numero)
    for k in range(1, len(ws)):
        if ws[k]["t0"] < ws[k - 1]["t1"] - 1e-3:
            ws[k]["t0"] = min(ws[k - 1]["t1"], ws[k]["t1"] - 0.02)
    # tiempo cortado a proposito entre dos palabras que quedan juntas en el video final: la pausa
    # que ve el guion es la FINAL ('durante [40, no,] 50 minutos' no parte la oracion)
    if word_cuts:
        for k in range(1, len(ws)):
            a, b = ws[k - 1]["t1"], ws[k]["t0"]
            cut = sum(max(0.0, min(b, e) - max(a, s_)) for s_, e in word_cuts if s_ < b and e > a)
            if cut > 0:
                ws[k]["cut_before"] = cut
    for i, w in enumerate(ws):
        w["i"] = i
    return ws


def _gap(words: list[dict], k: int) -> float:
    """Pausa (tiempo final) entre la palabra k-1 y la k: sin lo que se corto a proposito."""
    return words[k]["t0"] - words[k - 1]["t1"] - words[k].get("cut_before", 0.0)


# palabras que abren oracion: con mayuscula a media transcripcion (Whisper sin puntos) marcan
# el inicio de una oracion nueva ('... nombres en ingles No puedo', '... de demo Yo estoy')
STARTERS = set("""yo tu el ella ellos ellas nosotros nosotras ustedes usted no si claro asi bueno pues
entonces ahora ok okay oye oigan mira miren bien ademas tambien luego despues primero finalmente
igual osea basicamente obviamente literalmente digamos total vamos vale listo hoy aqui alli ahi ya
pero porque para por que como cuando donde y o e u ni sin con en de a al del la las los lo un una
unos unas este esta esto estos estas ese esa eso esos esas mi mis tus su sus nuestro nuestra me te
se le les nos hay es son fue era tengo tiene tenemos quiero queremos puedo podemos voy vas va
estoy estamos dejame dejenme permitanme imaginate imagina recuerda ojo hola gracias sigueme
siguenos checa chequen visita ven vengan""".split())
# palabras que no cierran una idea (determinantes, posesivos, cuantificadores, preposiciones):
# una oracion o un bloque de titulo no termina en ellas ('que no tiene ningun', 'aqui en nuestra')
DANGLING = STOP | set("""ningun ninguna ningunos ningunas nuestro nuestra nuestros nuestras algun
alguna algunos algunas varios varias cierto cierta porque muy mas tan sin sobre hacia hasta segun
cuyo cuya tus mis sus""".split())


def _is_cap(w: dict) -> bool:
    t = str(w.get("text", "")).lstrip("¿¡\"'(")
    return bool(t[:1]) and t[:1].isupper()


def _cap_start(words: list[dict], k: int) -> bool:
    """La palabra k abre una oracion nueva por su mayuscula: es de las que abren oracion
    (pronombre, negacion, muletilla, conector, adverbio en -mente, imperativo con enclitico) y
    no es el inicio de un nombre de varias palabras ('de Al Jaramain', 'Glorieta Norte')."""
    if k <= 0 or k >= len(words):
        return False
    w = words[k]
    if not _is_cap(w) or str(w["text"]).isupper() and len(w["n"]) > 1:
        return False                        # sigla ('UV', 'MXN')
    n = w["n"]
    if not (n in STARTERS or n.endswith("mente") or re.search(r"(anme|enme)$", n)):
        return False
    nx = words[k + 1] if k + 1 < len(words) else None
    if nx is not None and _is_cap(nx) and nx["n"] not in STARTERS and nx["t0"] - w["t1"] < 0.35:
        return False                        # 'Al Jaramain': nombre propio
    return True


def _sentences(words: list[dict]) -> list[dict]:
    sents, cur = [], []
    # transcripcion sin puntos (una toma larga): la mayuscula de Whisper manda y una pausa corta
    # sola no parte la oracion
    punct = sum(1 for w in words if re.search(r"[.!?…]$", w["text"])) >= max(1, len(words) // 60)
    pause = 0.42 if punct else 0.62
    for k, w in enumerate(words):
        g = _gap(words, k) if cur else 0.0
        if cur and (g > 0.8) and not (
                cur[-1]["text"].endswith(",") and g < 2.5) and not (
                cur[-1]["n"] in DANGLING and g < 1.3 and not _cap_start(words, k)):
            sents.append(cur)
            cur = []
        elif cur and _cap_start(words, k):
            sents.append(cur)
            cur = []
        cur.append(w)
        end_p = bool(re.search(r"[.!?…]$", w["text"]))
        nxt_gap = _gap(words, k + 1) if k + 1 < len(words) else 9
        comma = w["text"].endswith(",") and nxt_gap < 2.5
        dangling = w["n"] in DANGLING and not end_p and nxt_gap < 1.3
        if end_p or (nxt_gap > pause and len(cur) >= 5 and not comma and not dangling
                     and not (k + 1 < len(words) and _cap_start(words, k + 1))):
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
        while name_ws and norm(name_ws[-1]) in ("de", "del", "la", "las", "los", "el"):
            name_ws.pop()                  # "Parque Azul del (centro)": sin conector colgando
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
        spoken = sum(bag[k] for k in keys if " " not in k)
        # el lugar de la toma solo refuerza lo que se DICE ("interior de casa" no vuelve a nadie
        # agente inmobiliario): sin ninguna palabra del tema en la voz no hay rol por tema
        c = spoken + (sum(2 for k in keys if k.replace(" ", "") in places.replace(" ", "")) if spoken else 0)
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


# raices de la profesion que deben aparecer en la MISMA entrada del letrero que el nombre
_ROLE_SIGN = {"Dentista": ("dentist", "odontolog", "dental"), "Nutrición": ("nutri",),
              "Abogado": ("abogad", "legal", "juridic"), "Dermatología": ("dermat",),
              "Barbero": ("barber",), "Estilista": ("estilist", "salon", "belleza"),
              "Coach fitness": ("entrenador", "coach", "fitness"), "Doctora": ("medic", "doctor"),
              "Doctor": ("medic", "doctor")}
_SIGN_SCHEMA = {"type": "object", "properties": {"entries": {"type": "array", "items": {
    "type": "object", "properties": {"name": {"type": "string"}, "line": {"type": "string"}},
    "required": ["name", "line"]}}}, "required": ["entries"]}
_NAME_RX = re.compile(r"^(dra?|dr|doctora|doctor|lic|ing|mtra?|psic)\.?\s+([A-Za-zÁÉÍÓÚÑÜáéíóúñü]+\.?"
                      r"(?:\s+[A-Za-zÁÉÍÓÚÑÜáéíóúñü]+\.?){1,4})$", re.I)
_NAME_SMALL = {"de", "del", "la", "las", "los", "y"}


def _pretty_name(raw: str) -> str | None:
    """'DRA. ANA RUIZ DE LA O' -> 'Dra. Ana Ruiz de la O' (None si no parece nombre con titulo)."""
    m = _NAME_RX.match(re.sub(r"\s+", " ", str(raw or "").strip().strip(".,;:")))
    if not m:
        return None
    ttl = _NAME_TITLES.get(norm(m.group(1)), m.group(1).capitalize() + ".")
    parts = []
    for k, w in enumerate(m.group(2).split()):
        lw = w.lower()
        parts.append(lw if (k > 0 and lw in _NAME_SMALL) else lw[:1].upper() + lw[1:])
    return f"{ttl} {' '.join(parts)}"


# como se escribe la profesion en un letrero (para la pregunta dirigida al VLM)
_ROLE_ASK = {"Dentista": "DENTISTA (u odontologia/dental)", "Nutrición": "NUTRIOLOGA o NUTRICION",
             "Abogado": "ABOGADO o ABOGADA", "Dermatología": "DERMATOLOGIA", "Barbero": "BARBERIA o BARBERO",
             "Estilista": "ESTILISTA o SALON DE BELLEZA", "Coach fitness": "ENTRENADOR o COACH"}


def _vlm_sign_read(img: np.ndarray, ask: str, timeout: float = 30.0) -> dict | None:
    """Lectura dirigida del letrero: TODAS las entradas cuya descripcion dice la profesion
    ({entries: [{name, line}]})."""
    import cv2
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return None
    prompt = ("Hay un letrero con nombres de profesionales. Lista TODAS las personas cuya descripcion "
              f"dice {ask}. Para cada una copia EXACTO su nombre completo con titulo (Dra./Dr.) y la "
              "linea de descripcion que lo acompana. Si no hay ninguna, entries vacio.")
    try:
        raw = _ollama_chat([{"role": "user", "content": prompt,
                             "images": [base64.b64encode(buf.tobytes()).decode()]}],
                           fmt=_SIGN_SCHEMA, model=_vlm_model(), num_ctx=4096, temperature=0.0,
                           num_predict=260, timeout=timeout)
    except Exception:
        return None
    return _json_loose(raw)


def _sign_entries(r: dict | None, stems: tuple) -> list[tuple[str, str]] | None:
    """Entradas validas de una lectura (nombre con titulo + linea con la profesion), sin
    duplicados. None si la lectura no sirve (respuesta rota)."""
    if not isinstance(r, dict):
        return None
    ents = r.get("entries")
    if ents is None and r.get("found") is not None:          # formato viejo {found, name, line}
        ents = [r] if r.get("found") else []
    if not isinstance(ents, list):
        return None
    out: dict[str, tuple[str, str]] = {}
    for e in ents:
        if not isinstance(e, dict):
            continue
        nm = _pretty_name(e.get("name", ""))
        line = str(e.get("line", ""))
        if nm and any(st in norm(line) for st in stems):
            out.setdefault(norm(nm), (nm, line[:80]))
    return list(out.values())


def sign_name(video: str, shots: list[dict], role: str, deadline: float | None = None) -> dict | None:
    """Nombre de quien habla leido en un letrero-directorio (fachada con varios consultorios).

    Pregunta dirigida al VLM: TODAS las entradas cuya descripcion dice <profesion>. Solo se
    acepta si el letrero tiene EXACTAMENTE una persona de esa profesion y DOS lecturas
    independientes (dos resoluciones / cuadros) coinciden en el nombre. Si alguna lectura ve
    dos o mas personas de la profesion, no hay nombre (no se puede saber quien habla).
    Devuelve {name, line, reads} o None. Es un dato del cliente: queda como candidato
    (entities.name_candidate) hasta que el usuario lo confirme en el Studio."""
    stems = _ROLE_SIGN.get(role or "") or ((norm(role)[:6],) if role and len(norm(role)) >= 5 else ())
    if not stems or not video or not Path(str(video)).exists() or not llm_available(_vlm_model()):
        return None
    rx = re.compile(r"\b(?:dra?|dr|doctora|doctor|lic)\.?\s+[A-Za-z]", re.I)
    cands = [s for s in shots or [] if s.get("use") != "a_camara"
             and any(rx.search(str(t)) for t in s.get("text_seen") or [])]
    if not cands:
        return None
    ask = _ROLE_ASK.get(role or "") or str(role).upper()
    try:
        stv = Path(str(video)).stat()
        vid = (str(video), stv.st_size, int(stv.st_mtime))
    except OSError:
        vid = (str(video),)
    for s in cands:
        key = _hkey(vid, s["start"], s["end"], role, PROMPT_VERSION, "sign2", _vlm_model())
        hit = _cache_get("sign", key)
        if hit is not None:
            if hit.get("name"):
                return hit
            continue
        L = float(s["end"]) - float(s["start"])
        reads: list[tuple[str, str]] = []
        agreed = None
        complete = True
        many = False
        # letrero cercano al inicio del b-roll (la camara suele alejarse): 4 cuadros x 2 tamanos,
        # hasta que DOS lecturas coincidan
        for frac in (0.1, 0.18, 0.26, 0.34):
            for side in (1280, 1600):
                if deadline is not None and _now() > deadline:
                    complete = False
                    break
                img = _grab_frame(str(video), float(s["start"]) + L * frac, side)
                if img is None:
                    continue
                ents = _sign_entries(_vlm_sign_read(img, ask), stems)
                if not ents:
                    continue
                if len(ents) > 1:
                    many = True             # varias personas de la profesion: no se sabe quien es
                    break
                reads.append(ents[0])
                same = [x for x in reads if norm(x[0]) == norm(ents[0][0])]
                if len(same) >= 2:
                    agreed = same[0]
                    break
            if agreed or many or not complete:
                break
        if agreed and not many:
            res = {"name": agreed[0], "line": agreed[1], "reads": len(reads)}
        else:
            res = {"name": "", "reads": len(reads), "many": many}
        if complete or many:
            _cache_put("sign", key, res)
        if res.get("name"):
            return res
    return None


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


_ICON_MEMO: dict[str, str | None] = {}


def resolve_icon(tag: str) -> str | None:
    """Palabra (ES/EN) -> icono Tabler existente, evitando coincidencias parciales absurdas.
    Memorizado: la busqueda por nombre en el catalogo cuesta ~0.2 s por palabra desconocida."""
    t = norm(tag)
    if not t:
        return None
    if t in _ICON_MEMO:
        return _ICON_MEMO[t]
    _ICON_MEMO[t] = r = _resolve_icon(t)
    return r


def _resolve_icon(t: str) -> str | None:
    for table in (ICON_ES_EXTRA, assets.ICON_ES, ICON_EN):
        n = table.get(t)
        if n and assets.icon_exists(n):
            return n
    # plurales del espanol: 'resinas' -> 'resina', 'blanqueamientos' -> 'blanqueamiento'
    for cut in (1, 2):
        if len(t) > 4 + cut and t.endswith("s" if cut == 1 else "es"):
            for table in (ICON_ES_EXTRA, assets.ICON_ES):
                n = table.get(t[:-cut])
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


def _llm_prompt(sents: list[dict], shots: list[dict] | None, part: tuple[int, int] | None = None) -> str:
    """Prompt del guion. `part` = (k, n): tramo k de n de un guion largo (_llm_script_windows);
    sin `part` es el prompt de siempre (videos cortos, una sola llamada)."""
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
    head, tail = "Frases del video (id | segundos | texto):\n", ""
    if part is not None:
        k, n = part
        head = (f"Tramo {k + 1} de {n} de un video largo (ids globales; 'ent' y 'topic' solo con lo "
                "que se dice en ESTE tramo). Frases del tramo (id | segundos | texto):\n")
        tail = ("\nUna entrada en \"f\" por CADA frase del tramo, con su id tal cual."
                + ("" if k == 0 else " Este tramo continua el video: su primera frase NO es el hook.")
                + " Escribe el JSON compacto en UNA sola linea (sin sangrias ni saltos de linea).")
    return (
        head + "\n".join(lines) + "\n" + sh +
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
        "fx \"tarjeta\". " + ("La primera frase es \"hook\". " if part is None or part[0] == 0 else "") +
        "Frase con chiste -> tone \"humor\"." + tail)


def _sh_key(shots: list[dict] | None) -> list:
    return [(round(float(x["start"]), 1), round(float(x["end"]), 1), bool(x.get("person")),
             x.get("use", "")) for x in shots or []]


# ---------------------------------------------------------------- guion largo (por tramos)
# Un guion largo (19 min, 2500 palabras) no cabe en UNA llamada: ~80 frases x ~95 tokens de salida
# no caben en el contexto de 8192 junto al prompt (JSON cortado = todo a heuristicas) y a ~50
# tokens/s no terminan en el presupuesto. Se parte en TRAMOS de oraciones completas, cada uno con
# su prompt acotado, su num_predict proporcional y su timeout segun los tokens esperados; se unen
# de forma determinista y un tramo que falla solo deja SUS frases con heuristicas.
LLM_SINGLE_MAX = 400      # palabras: hasta aqui el guion va en UNA llamada (prompt de siempre)
LLM_WIN_WORDS = 300       # palabras por tramo de un guion largo (~800-1000 tokens de prompt)
LLM_TOK_S = 40.0          # tokens/s de salida del LLM local con la Mac ocupada (libre ~70)
LLM_TOK_SENT = 95         # tokens de salida por frase (JSON con sangria; compacto ~50)
LLM_ENT_KEYS = ("name", "role", "price", "address", "landmark", "schedule", "cta", "link_label", "handle")


def _n_words(sents: list[dict]) -> int:
    return sum(int(s["w1"]) - int(s["w0"]) + 1 for s in sents)


def llm_timeout(prompt: str, n_sents: int) -> float:
    """Timeout (s) de una llamada del guion segun los tokens esperados (prompt ~3.2 caracteres por
    token a ~300 tok/s; salida LLM_TOK_SENT por frase a LLM_TOK_S con 30 % de holgura)."""
    return 8.0 + len(prompt) / 3.2 / 300.0 + 1.3 * (60 + LLM_TOK_SENT * max(1, n_sents)) / LLM_TOK_S


def llm_windows(sents: list[dict], max_words: int = LLM_WIN_WORDS) -> list[list[dict]]:
    """Tramos de ORACIONES COMPLETAS de ~max_words palabras, equilibrados (mismo tamano objetivo) y
    deterministas. Una oracion mas larga que el tramo va sola."""
    if not sents:
        return []
    total = _n_words(sents)
    k = max(1, math.ceil(total / max(1, max_words)))
    target = total / k
    out, cur, n = [], [], 0
    for s in sents:
        m = int(s["w1"]) - int(s["w0"]) + 1
        over = n + m - target
        # cierra el tramo si agregar la oracion lo aleja MAS del objetivo que dejarla (o lo pasa
        # de 1.3x el maximo): tramos parejos cerca del objetivo
        if cur and over > 0 and (over > target - n or n + m > 1.3 * max_words):
            out.append(cur)
            cur, n = [], 0
        cur.append(s)
        n += m
    if cur:
        out.append(cur)
    return out


def _shots_between(shots: list[dict] | None, t0: float, t1: float) -> list[dict] | None:
    """Tomas que se traslapan con [t0, t1] (las del tramo)."""
    if not shots:
        return None
    return [x for x in shots if float(x["end"]) > t0 and float(x["start"]) < t1] or None


class _ModelTurn:
    """Turno FIFO para el modelo local. ollama (OLLAMA_NUM_PARALLEL=1) atiende UNA peticion a la vez:
    sin turno, el tiempo en la cola del servidor se come el timeout de cada llamada (los tramos del
    guion y el VLM se cortan entre si). Con turno, cada llamada mide su timeout desde que empieza de
    verdad y el VLM no espera a que acaben TODOS los tramos (FIFO: se alternan)."""

    def __init__(self):
        self._cv = threading.Condition()
        self._busy = False
        self._q: list = []

    def acquire(self, timeout: float) -> bool:
        with self._cv:
            tk = object()
            self._q.append(tk)
            end = _now() + max(0.0, timeout)
            while self._busy or self._q[0] is not tk:
                rem = end - _now()
                if rem <= 0:
                    self._q.remove(tk)
                    self._cv.notify_all()
                    return False
                self._cv.wait(rem)
            self._q.pop(0)
            self._busy = True
            return True

    def release(self) -> None:
        with self._cv:
            self._busy = False
            self._cv.notify_all()


def _turn_call(turn: "_ModelTurn | None", deadline: float | None, need: float, fn: Callable,
               min_s: float = 4.0):
    """fn(timeout) con turno del modelo. El timeout es `need` recortado al `deadline`; si al tocar
    el turno ya no queda ni `min_s`, no se llama (None)."""
    wait = (deadline - _now()) if deadline is not None else need * 4
    if turn is not None and not turn.acquire(max(0.0, wait)):
        return None
    try:
        to = need if deadline is None else min(need, deadline - _now())
        if to < min_s:
            return None
        return fn(to)
    finally:
        if turn is not None:
            turn.release()


def _llm_script(sents: list[dict], shots: list[dict] | None, timeout: float | None = None) -> dict | None:
    if not sents:
        return None
    key = _hkey([s["text"] for s in sents], _sh_key(shots), PROMPT_VERSION, _llm_model())
    hit = _cache_get("script", key)
    if hit:
        return hit
    prompt = _llm_prompt(sents, shots)
    if timeout is None:
        # 55 s de siempre; mas solo si los tokens esperados lo piden (un guion de ~400 palabras)
        timeout = max(55.0, llm_timeout(prompt, len(sents)))
    try:
        raw = _ollama_chat([{"role": "system", "content": _LLM_SYS},
                            {"role": "user", "content": prompt}],
                           fmt=_LLM_SCHEMA, num_ctx=8192, temperature=0.2,
                           num_predict=160 + 90 * len(sents), timeout=timeout)
    except Exception:
        return None
    d = _json_loose(raw)
    if not d or not isinstance(d.get("f"), list):
        return None
    _cache_put("script", key, d)
    return d


def _window_result(d: dict | None, win: list[dict]) -> dict | None:
    """Respuesta de un tramo saneada: solo entradas con ids del tramo (si el modelo numero el
    tramo desde 0, se mapea por posicion). None si no trae ninguna frase del tramo."""
    if not d or not isinstance(d.get("f"), list):
        return None
    ids = [s["id"] for s in win]
    f = [e for e in d["f"] if isinstance(e, dict)]
    got = []
    for e in f:
        try:
            got.append(int(e.get("id")))
        except (TypeError, ValueError):
            got.append(None)
    if f and not any(g in ids for g in got) and all(g is not None and 0 <= g < len(win) for g in got):
        for e, g in zip(f, got):
            e["id"] = ids[g]
        got = [e["id"] for e in f]
    keep = [e for e, g in zip(f, got) if g in ids]
    if not keep:
        return None
    return {"topic": str(d.get("topic") or ""), "ent": d.get("ent") if isinstance(d.get("ent"), dict) else {},
            "f": keep}


def merge_windows(parts: list[dict | None]) -> dict | None:
    """Une los tramos (orden del video, determinista): frases de todos los tramos que respondieron,
    tema del primero con tema, entidades = primer valor no vacio (cta/link_label: el ULTIMO, el
    llamado a la accion va al final). None si ningun tramo respondio."""
    ok = [x for x in parts if x]
    if not ok:
        return None
    f, seen = [], set()
    for x in ok:
        for e in x.get("f") or []:
            try:
                i = int(e.get("id"))
            except (TypeError, ValueError):
                continue
            if i not in seen:
                seen.add(i)
                f.append(e)
    ent = {}
    for k in LLM_ENT_KEYS:
        vals = [str((x.get("ent") or {}).get(k) or "").strip() for x in ok]
        vals = [v for v in vals if v]
        ent[k] = (vals[-1] if k in ("cta", "link_label") else vals[0]) if vals else ""
    topic = next((str(x.get("topic")).strip() for x in ok if str(x.get("topic") or "").strip()), "")
    return {"topic": topic, "ent": ent, "f": f}


def _llm_script_windows(sents: list[dict], shots: list[dict] | None, deadline: float | None = None,
                        box: dict | None = None, turn: "_ModelTurn | None" = None) -> dict | None:
    """Guion largo por tramos (llm_windows) en orden: el del hook primero. Cada tramo con su
    prompt (`part`), num_predict proporcional y timeout por tokens esperados (llm_timeout), con
    cache propia. `deadline`: los tramos que no alcanzan quedan con heuristicas. `box` recibe el
    avance EN VIVO (parts/stat): si el director deja de esperar, usa los tramos ya hechos."""
    wins = llm_windows(sents)
    n = len(wins)
    parts: list[dict | None] = [None] * n
    stat = {"n": n, "ok": 0, "cached": 0, "failed": 0, "skipped": 0, "ms": 0}
    if box is not None:
        box["parts"], box["wins"], box["stat"] = parts, wins, stat
    t_all = _now()
    for k, w in enumerate(wins):
        wsh = _shots_between(shots, float(w[0]["t0"]), float(w[-1]["t1"]))
        key = _hkey([s["text"] for s in w], _sh_key(wsh), PROMPT_VERSION, _llm_model(), "tramo", k, n)
        hit = _cache_get("script", key)
        if hit:
            parts[k] = _window_result(hit, w)
            stat["cached"] += 1
            stat["ok"] += parts[k] is not None
            continue
        prompt = _llm_prompt(w, wsh, part=(k, n))
        need = llm_timeout(prompt, len(w))
        if deadline is not None and deadline - _now() < min(need, 12.0):
            stat["skipped"] += n - k       # no alcanza ni un tramo corto: el resto va con heuristicas
            break
        msgs = [{"role": "system", "content": _LLM_SYS}, {"role": "user", "content": prompt}]
        try:
            raw = _turn_call(turn, deadline, need, lambda to: _ollama_chat(
                msgs, fmt=_LLM_SCHEMA, num_ctx=8192, temperature=0.2,
                num_predict=200 + 130 * len(w), timeout=to), min_s=min(need, 12.0))
        except Exception:
            raw = None
        d = _json_loose(raw) if raw else None
        res = _window_result(d, w)
        if res is None:
            stat["failed"] += 1
            continue
        _cache_put("script", key, d)
        parts[k] = res
        stat["ok"] += 1
    stat["ms"] = round((_now() - t_all) * 1000)
    return merge_windows(parts)


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


def _takes_topic(plan: dict) -> str:
    """Tema que entendio la limpieza de tomas (plan.takes.topic), sin juicios de valor y corto."""
    tk = plan.get("takes") if isinstance(plan.get("takes"), dict) else {}
    t = str(tk.get("topic") or "").strip().strip(".")
    t = " ".join(w for w in t.split() if norm(w) not in _TOPIC_BLOCK)
    return t if t and len(t.split()) <= 6 else ""


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
        if _n_words(sents) > LLM_SINGLE_MAX:
            llm = _llm_script_windows(sents, shots, deadline=_now() + director_budget(_plan_duration(plan)))
        else:
            llm = _llm_script(sents, shots)
    if llm:
        _merge_llm(script, llm, words)
        script["llm"] = True
    tt = _takes_topic(plan)
    if tt:
        # el tema de TODO el video (limpieza de tomas: una llamada con el inicio de cada oracion)
        # manda sobre el del primer tramo del guion (en un video largo suele ser el saludo o el
        # nombre del canal)
        script["topic"] = tt
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
        if k == "socials" and item.get("variant") == "watermark":
            return "edge"                    # marca de agua: no ocupa ninguna franja
        return _zone_y(float(item.get("y", 0.3)))
    return None


ZONE_Y = {"top": 0.38, "center": 0.585, "low": 0.665}


def _zone_y(y: float) -> str:
    """Franja por la y del centro: top (sobre la cabeza) / center (pecho) / low (arriba de los
    subtitulos: CTA, rotulos) / bottom (subtitulos)."""
    return "top" if y < ZONE_Y["top"] else "center" if y < ZONE_Y["center"] else \
        "low" if y < ZONE_Y["low"] else "bottom"


ACCENT_SEP = 0.2

# ---------------------------------------------------------------- simetria (regla de la marca)
# todo overlay deja el MISMO aire a la izquierda y a la derecha de su tinta: lo centrado va en
# x = 0.5 con ancho <= SYM_W; los glifos simetricos pueden ir en PAR ESPEJO (x y 1-x); los glifos
# con mano (?, !, rayo, espiral...) y los iconos solo centrados; lo que no cabe simetrico se quita
SYM_L, SYM_R = SYM_X - SYM_W / 2, SYM_X + SYM_W / 2      # banda simetrica (0.12..0.88)
SYM_TOL = 0.003                   # |x - 0.5| que ya cuenta como centrado
SYM_GLYPHS = ("sparkle", "star4", "star5", "burst", "rays", "heart")    # admiten par espejo
MARK_GLYPHS = ("underline", "circle_scribble", "squiggle")               # marcas sobre una linea
TWIN = "_m"                       # sufijo del gemelo espejo de un par ('<id>' y '<id>_m')


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


def measure_lines(item: dict, style: str = "dinamico", with_text: bool = False) -> list:
    """Tinta de cada linea (normalizada) segun motion/text.py; [] si el modulo no esta. Con
    `with_text`, [(caja, texto de la linea)] tal como la partio el layout."""
    try:
        from . import text as TX
        clean = {k: v for k, v in item.items() if not k.startswith("_")}
        out = []
        for row in TX.measure_lines(clean, _measure_ctx(style), with_text=True):
            (a, b, c, d), txt = row
            box = (a / 1080.0, b / 1920.0, c / 1080.0, d / 1920.0)
            out.append((box, txt) if with_text else box)
        return out
    except Exception:
        return []


_CARD_H_EST = {"location": 0.43, "price": 0.27, "schedule": 0.17, "link": 0.2, "phone": 0.2,
               "rating": 0.2, "checklist": 0.25, "stat": 0.22, "generic": 0.16, "booking": 0.3}


def card_height(item: dict, style: str = "dinamico") -> float:
    """Alto real de una tarjeta (fraccion de H) segun motion/graphics.py (o estimado)."""
    try:
        from . import graphics as G
        r = G.card_layout(dict(item), _measure_ctx(style))
        return float(r[8]) / 1920.0
    except Exception:
        return _CARD_H_EST.get(item.get("variant", "generic"), 0.18) * float(item.get("size", 0.74)) * 1080 / 1920


def lt_sub_fits(sub: str, size: float, style: str = "dinamico", x: float | None = None,
                min_k: float = 0.8) -> bool:
    """El subtitulo del rotulo (versalitas con tracking, graphics.draw_lower_third) cabe a >=
    `min_k` de su tamano nominal en el ancho del rotulo CENTRADO (SYM_W - 2*padx). Con `x`, el
    rotulo legado anclado a la izquierda en x (hasta SAFE.right)."""
    try:
        from . import graphics as G
        c = _measure_ctx(style)
        if x is None:
            avail = SYM_W * c.W - 2 * c.px(28)
        else:
            avail = (SAFE["right"] - x) * c.W - c.px(10) - 2 * c.px(28)
        fs = G.font(G._fonts(c)["label"], size * c.W * 0.5 * min_k)
        return G.text_w(fs, str(sub).upper(), fs.getSize() * 0.14) <= avail
    except Exception:
        return len(str(sub)) <= 30


def lt_width(title: str, sub: str, size: float = 0.064, style: str = "dinamico") -> float:
    """Ancho (fraccion de W) del panel del rotulo CENTRADO (graphics.draw_lower_third, align
    'center'): titulo con fit_font y rol en versalitas con tracking hasta SYM_W - 2*padx, mas el
    padding. El panel va centrado en su x y nunca pasa de SYM_W."""
    try:
        from . import graphics as G
        c = _measure_ctx(style)
        F = G._fonts(c)
        padx = c.px(28)
        avail = max(c.px(120), SYM_W * c.W - 2 * padx)
        title = str(title or "")
        ft = G.fit_font(F["title"], title, size * c.W, avail, min_px=size * c.W * 0.55)
        title = G.ellipsize(ft, title, avail)
        sub_u = str(sub or "").upper()
        fs_px = size * c.W * 0.5
        fs = G.font(F["label"], fs_px)
        trk = fs.getSize() * 0.14
        while sub_u and G.text_w(fs, sub_u, trk) > avail and fs.getSize() > fs_px * 0.7:
            fs = G.font(F["label"], fs.getSize() * 0.94)
            trk = fs.getSize() * 0.14
        sub_u = G.ellipsize(fs, sub_u, avail, trk)
        w = max(ft.measureText(title), G.text_w(fs, sub_u, trk) if sub_u else 0.0) + 2 * padx
        return min(SYM_W, w / c.W)
    except Exception:
        return SYM_W


def cta_natural_width(item: dict, style: str = "dinamico") -> float:
    """Ancho natural (fraccion de W) de un CTA segun graphics (sin `width`)."""
    try:
        from . import graphics as G
        it = {k: v for k, v in item.items() if k != "width"}
        return 2.0 * G.cta_extent(it, _measure_ctx(style)) / 1080.0
    except Exception:
        return float(item.get("size", 0.7))


def socials_size(item: dict, style: str = "dinamico") -> tuple[float, float]:
    """(ancho, alto) normalizados del grupo de chips de redes segun motion/graphics.py (o estimado)."""
    try:
        from . import graphics as G
        w, h = G.socials_extent(dict(item), _measure_ctx(style))
        return w / 1080.0, h / 1920.0
    except Exception:
        n = len(item.get("links") or []) or 1
        ch = float(item.get("size", 0.074)) * 1080 / 1920
        return 0.6, n * ch + (n - 1) * 0.22 * ch


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


# muletillas y marcadores de discurso GENERALES del espanol hablado: nunca son el protagonista
# de un titulo y se recortan del borde de un titulo ('Basicamente el perfume...' -> 'el perfume...')
FILLER = {"pues", "nada", "basicamente", "bueno", "este", "eh", "ehh", "mmm", "ok", "okay", "entonces",
          "obviamente", "literalmente", "tipo", "digamos", "osea", "asi", "igual", "verdad", "creo",
          "permitanme", "permiteme", "dejame", "dejenme", "mira", "miren", "oye", "oigan", "sabes"}
# palabras sin contenido propio (auxiliares y verbos ligeros, deicticos, cuantificadores, sustantivos
# vagos): no se recortan de un titulo pero no cuentan como contenido. Categorias generales del
# idioma, no vocabulario de un rubro: 'Tengo 20% de descuento' tiene contenido (numero + sustantivo)
LIGHT = {"ser", "es", "son", "era", "fue", "soy", "somos", "estar", "estoy", "estas", "esta", "estamos",
         "estan", "haber", "hay", "ha", "han", "he", "hemos", "tener", "tengo", "tiene", "tienes",
         "tenemos", "tienen", "hacer", "hago", "hace", "hacemos", "hacen", "haciendo", "hecho", "ir",
         "voy", "vas", "va", "vamos", "van", "poder", "puedo", "puede", "puedes", "podemos", "pueden",
         "poderles", "querer", "quiero", "quieres", "quiere", "queremos", "quieren", "decir", "digo",
         "dice", "dicho", "dije", "saber", "se", "sabe", "hablando", "hablar", "aqui", "ahi", "alla",
         "alli", "ahora", "hoy", "mismo", "misma", "todo", "toda", "todos", "todas", "bien", "algo",
         "cosa", "cosas", "caso", "manera", "forma", "estilo", "parte", "alguna", "alguno", "mencione",
         "decirlo", "gente", "ningun", "ninguna", "tan", "tanto", "tanta", "muy", "mas", "menos"}
# frases de relleno completas (se descuentan antes de medir el contenido de un bloque)
FILLER_PH = ["o algo por el estilo", "por decirlo de alguna manera", "como ya mencione", "en ninguna parte",
             "asi que nada", "de alguna manera", "o sea", "la verdad", "por asi decirlo", "que te digo",
             "ok gente", "hola gente", "que onda gente", "asi que", "en este caso", "por decirlo asi",
             "ya que"]
# muletillas que se recortan del INICIO o del FINAL de un titulo (el subtitulo las conserva)
EDGE_FILLER = FILLER | {"que", "y", "o", "pero"}
# tamano efectivo minimo de una linea de titulo (fraccion de W; las etiquetas de tarjetas aparte)
MIN_LINE = 0.052

CLAUSE_W = {"y", "e", "o", "u", "pero", "que", "como", "cuando", "donde", "porque", "si", "para",
            "aunque", "pues", "entonces", "ni", "mientras", "sino"}
_NOUN_SUFFIX = ("eza", "cion", "sion", "miento", "aje", "ura", "encia", "ancia", "ismo", "dad",
                "tura", "ado", "ada", "ia")
_VERB_END = ("ar", "er", "ir", "ando", "iendo", "amos", "emos", "imos", "an", "en")
STROKE_PRESETS = ("stack", "title_pop", "cascade", "highlight_marker", "kicker")
# verbos frecuentes que terminan como sustantivo (-ia, -ado...): no son "el servicio"
_VERBISH = {"cambia", "cambian", "limpia", "limpian", "copia", "envia", "anuncia", "estudia", "varia",
            "confia", "cuida", "ayuda", "cuidado", "pasado", "quedado", "dejado", "logrado", "cansado"}


# ---------------------------------------------------------------- enumeraciones
_ENUM_JOIN = {"y", "e", "o", "u", "ni"}


def find_enums(words: list[dict], w0: int, w1: int) -> list[list[list[dict]]]:
    """Enumeraciones dentro de las palabras [w0, w1] de una frase (2-5 elementos):
      - clausulas paralelas: 'como comes y como duermes' (tras 'y' se repite el arranque de
        un tramo anterior de 1-5 palabras: los dos tramos son los elementos);
      - lista con comas: 'cortes, barba y cejas' (elementos de 1-3 palabras separados por coma
        y cerrados con y/e/o).
    Devuelve [[palabras del elemento 1], [elemento 2], ...] por enumeracion encontrada."""
    ws = words[w0:w1 + 1]
    ns = [w["n"] for w in ws]
    out = []
    # 1) paralelas
    for k in range(1, len(ws) - 1):
        if ns[k] not in _ENUM_JOIN:
            continue
        for j in range(max(0, k - 5), k):
            L = k - j
            if ns[j] == ns[k + 1] and len(ns[j]) >= 2 and k + 1 + L <= len(ws):
                a, b = ws[j:k], ws[k + 1:k + 1 + L]
                if any(w["n"] not in STOP for w in a) and any(w["n"] not in STOP for w in b):
                    out.append([a, b])
                break
    # 2) lista con comas
    segs, cur = [], []
    closed = False
    for k, w in enumerate(ws):
        if not closed and ns[k] in _ENUM_JOIN and cur and segs:
            segs.append(cur)            # 'y' tras las comas: empieza el ultimo elemento
            cur = []
            closed = True
            continue
        cur.append(w)
        if not closed and w["text"].endswith(","):
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    if len(segs) >= 3:
        items = []
        for i, sg in enumerate(segs):
            content = [w for w in sg if w["n"] not in STOP and not re.fullmatch(r"\d+", w["n"])]
            if not content:
                items = []
                break
            if i == 0:
                it_ = content[-1:]
            elif i == len(segs) - 1:
                it_ = []
                for w in sg:
                    if w["n"] in STOP and it_:
                        break
                    if w["n"] not in STOP:
                        it_.append(w)
                    if len(it_) >= 2:
                        break
            else:
                if len(sg) > 3:
                    items = []
                    break
                it_ = content
            items.append(it_)
        if len(items) >= 3 and not any(len(x) > 3 for x in items):
            out.append(items)
    return out


# ---------------------------------------------------------------- paso 3: coreografo
def _draw_time(name: str) -> float:
    """Tiempo de trazo de un garabato (graphics.DRAW_TIME) para el SFX de lapiz."""
    try:
        from . import graphics as G
        return float(G.DRAW_TIME.get(name, 0.5))
    except Exception:
        return 0.5


def _transitions_mod():
    """motion.transitions si esta disponible (DEFAULTS, SFX, hit_time); None si no."""
    try:
        from . import transitions as TM
        return TM
    except Exception:
        return None


class _Choreo:
    """Coreografo determinista. Trabaja en tiempo original para emitir y en tiempo final para
    las reglas de ritmo y de no encimar."""

    MAXW = SYM_W                    # ancho maximo de un bloque centrado (fraccion de W)
    XC = SYM_X                      # centro horizontal de TODO overlay (0.12..0.88 con MAXW)

    def __init__(self, plan: dict, shots: list[dict], script: dict, style: str, intensity: float,
                 seed: int, fixed: dict | None = None, windows: list | None = None, rev: int = 0):
        self.plan = plan
        self.style = style if style in STYLES else "dinamico"
        self.S = STYLES[self.style]
        self.auto_matte = AUTO_MATTE_FX         # bg_replace / color_pop propuestos por la IA
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
        # fuente horizontal (o cuadrada): la salida 9:16 es un recorte que SIGUE la cara
        # (reframe_track): ancho del recorte en fraccion del ancho de la fuente
        self.crop_w = 1.0
        # zoom maximo de encuadre: sin pasar de ~1.15x de ampliacion sobre la resolucion de la
        # fuente (4K vertical 2880 px -> 1.72; 1080p -> 1.3)
        try:
            src_h = float(dims[1])
        except Exception:
            src_h = 1920.0
        self.zcap = _clamp(src_h / 1920.0 * 1.15, 1.3, 1.8)
        if self.aspect < 1.2:           # salida 9:16 siempre (el base pass reencuadra)
            self.crop_w = _clamp(self.aspect * 9.0 / 16.0, 0.05, 1.0)
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
        cap = self.S["captions"]
        cap_sz = float(cap["size"]) * (1.28 if cap["preset"] == "bold_caps" else 1.0)
        # borde superior de la linea de subtitulos: nada con flecha/tarjeta debe bajar de aqui
        self.cap_top = float(cap["y"]) - cap_sz * (1080.0 / 1920.0) * 0.8
        # jerarquia: un titulo nunca es mas chico que 1.15x el subtitulo que oculta (si no cabe
        # con ese tamano, ese bloque se queda como subtitulo)
        self.title_floor = float(cap["size"]) * (1.15 if cap["preset"] == "bold_caps" else 1.0) * 1.15
        self.kicker_labels: Counter = Counter()      # etiquetas de kicker ya usadas
        self.cap_hide: list[dict] = []               # captions.y_ranges con hide (CTA en primer plano)
        self.prof_notes: list[str] = []              # notas del perfil (lo que no cupo en el cierre)
        self.grid = None                     # rejilla de la musica (beats en tiempo final)
        self.snapped: list[tuple[float, float]] = []
        self.ink: list[tuple] = []          # (fa, fb, x0, y0, x1, y1, id) tinta de textos
        self.shown: set[str] = set()        # entidades ya mostradas (no repetir)
        self.trans_req: list[dict] = []
        self.reserved: list[tuple[float, float]] = []
        self.bg_fx: list[tuple[float, float, str]] = []
        self.broll_card: dict[int, dict] = {}
        self.sent_main = None
        # presupuesto de escenas a pantalla completa (listas, precio en escena, tarjetas grandes)
        self.n_cut_left = int(round(self.S["cutaways"] * self.I * max(1.0, self.D / 25.0)))
        self.name = name_usable(self.ent)       # nombre publicable (voz, confirmado o perfil)
        # perfil de la persona (3.2): rotulo, Seguir, reservas y cierre salen de SUS datos
        self.prof = _plan_profile(plan)
        self.psocial = PR.primary_social(self.prof)
        self.book = PR.booking_link(self.prof)
        # iconos del rubro y musica: el rubro de la voz (la profesion escrita no los cambia)
        self.role = self.ent["topic_role"] if "topic_role" in self.ent else self.ent.get("role", "")
        if self.book is not None and PR.uses(self.prof, "booking_cta") and self.anc.get("link") is None:
            li = self._booking_anchor()
            if li is not None:
                self.anc["link"] = li
        self.link_item = None                   # CTA link_below de la frase del link
        self.link_ext = False                   # ese link se sostiene hasta el ultimo cuadro
        self.protect: list[tuple[float, float]] = []   # (desde, hasta) tiempo final: dato que se lee
        self.cap_ranges = None                  # captions.y_ranges (tomas de cuerpo completo)
        self.last_word = max(self.words, key=lambda w: w["f1"]) if self.words else None
        self.fixed_titles: list[tuple[float, float]] = []      # (inicio, fin) originales
        self._sym_old: dict = {}                # cajas previas al pase de simetria (por id)
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
                if tr == "effects" and it.get("kind") == "bg_replace" and it.get("enabled", True) is not False:
                    # fondo reemplazado que se queda (bloqueado): la tinta de encima se mide contra el
                    self.bg_fx.append((a, b, "light" if it.get("background") in LIGHT_BG else "dark"))
                if z:
                    self.occ[z].append((a, b, str(it.get("id"))))
                    if z == "accent":
                        # anclado a la cara (face_*: x/y null): se cuenta en el centro
                        self.acc_pos[str(it.get("id"))] = (float(it["x"]) if it.get("x") is not None else 0.5,
                                                           float(it["y"]) if it.get("y") is not None else 0.5)
                if tr == "text":
                    r = measure_text(it, self.style)
                    if r is not None:
                        self.ink.append((a, b, *self._pad_rect(it, r), str(it.get("id"))))
                if tr in ("text", "cutaways") and it.get("enabled", True) is not False:
                    # lo bloqueado ocupa SU frase y su rango: ningun otro titulo en ninguna franja
                    # (no se regenera el mismo texto encima ni en otra zona)
                    self.fixed_titles.append((float(it["start"]), float(it["end"])))
                    # las OTRAS franjas van marcadas 'lockzone:': bloquean titulos, pero no la
                    # pastilla del enlace ni los CTA (free(..., cta=True)). Sin la marca, bloquear un
                    # titulo junto a 'link aqui abajo' borraba 'Agenda en PaseoClub' al re-dirigir
                    for zz in ("top", "center", "low"):
                        if zz != z:
                            self.occ[zz].append((a, b, "lockzone:" + str(it.get("id"))))
                if tr == "graphics" and it.get("kind") == "card":
                    v = it.get("variant")
                    self.shown |= {"price"} if v == "price" else {"address", "landmark"} if v == "location" else set()
                self.events.append((a, str(it.get("id"))))
        # palabras que ya cubre un titulo/escena bloqueado: su frase no se vuelve a titular
        for a0, b0 in self.fixed_titles:
            for w in self.words:
                if a0 - 0.05 <= w["t0"] and w["t1"] <= b0 + 0.08:
                    self.titled_words.add(w["i"])
        for s_ in self.sents:
            ws_ = self.words[s_["w0"]:s_["w1"] + 1]
            if ws_ and sum(1 for w in ws_ if w["i"] in self.titled_words) >= 0.6 * len(ws_):
                s_["info"]["_done"] = True
        # recursos del guion (fx.media): contenido BLOQUEADO que ocupa la pantalla (tarjeta flotante
        # sobre el video desenfocado). Ocupan todas las franjas (nada de titulos, tarjetas, escenas
        # ni garabatos encima; los subtitulos se quedan) y cuentan como eventos para el ritmo
        self.media_win: list[tuple[float, float, str]] = []
        for it in media_items(plan):
            a, b = self.f(float(it["start"])), self.f(float(it["end"]))
            if b - a <= 0.05:
                continue
            iid = "media:" + str(it.get("id"))
            self.media_win.append((a, b, iid))
            for z in ("top", "center", "low", "full", "accent"):
                self.occ[z].append((a, b, iid))
            t = a
            while t < b:
                self.events.append((t, iid))
                t += 1.0
            self.events.append((b, iid))

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
    def out_face(self, face: list | None) -> list | None:
        """Rostro (cx, cy, w, h) de la FUENTE -> cuadro de salida 9:16. En una fuente vertical es
        el mismo; en una horizontal el recorte sigue la cara (centrado en ella salvo en los
        bordes del cuadro) y todo lo horizontal se escala por 1/ancho del recorte."""
        if not face or self.crop_w >= 0.999:
            return face
        cw = self.crop_w
        cx, cy, fw, fh = [float(v) for v in face[:4]]
        c0 = _clamp(cx, cw / 2, 1 - cw / 2)
        return [_r(_clamp(0.5 + (cx - c0) / cw, 0.0, 1.0), 4), cy, _r(min(1.0, fw / cw), 4), fh]

    def _prep_shots(self, shots: list[dict]) -> list[dict]:
        out = []
        for k, s in enumerate(shots):
            d = dict(s)
            d["k"] = k
            d["ramp_join"] = ramp_join(shots, k)
            d["f0"], d["f1"] = self.f(s["start"]), self.f(s["end"])
            talk = s.get("use", "a_camara") == "a_camara"
            if self.crop_w < 0.999:
                d["face"] = self.out_face(s.get("face"))
                d["face_track"] = [[tr[0], *self.out_face(tr[1:5])] for tr in s.get("face_track") or []
                                   if len(tr) >= 5]
                s = d
            face = s.get("face") or (list(NOMINAL_FACE) if talk else None)
            d["face"] = face
            d["face_nominal"] = bool(talk and not s.get("face"))
            d["talk"] = talk
            z0 = 1.0
            wide = bool(talk and not d["face_nominal"] and face[3] < 0.14)
            if talk and not d["face_nominal"]:
                z0 = _clamp(self.S["zoom_face"] / max(0.03, face[3]), 1.0, 1.45)
                if wide:
                    # plano abierto a camara (cuerpo entero): piso de PLANO MEDIO (cara ~0.14-0.16
                    # del alto, de la cintura arriba) como el editor de referencia; la camara
                    # limita el zoom por toma si la persona camina (pista de rostro). Deja sitio
                    # bajo el tope para el cerrado del jump-cut
                    jn = 1.0 + self.S["jump_zoom"] * (0.6 + 0.4 * self.I)
                    z0 = max(z0, min(self.zcap / jn, (self.S["zoom_face"] + 0.045) / max(0.03, face[3])))
            d["z0"] = z0
            d["wide"] = wide
            mq = s.get("matte_q")
            # recorte del sujeto confiable (contorno / reemplazo de fondo); sin medicion = se permite
            d["matte_ok"] = bool(talk and not d["face_nominal"] and (mq is None or float(mq) >= MATTE_OK))
            # en un primer plano (cara > ~1/5 del alto) el jump-zoom es corto: cerrar mas corta la
            # frente y empuja la barbilla sobre los titulos; en primer plano extremo casi nada
            close = _clamp((0.34 - face[3]) / 0.2, 0.25, 1.0) if face and talk else 1.0
            if face and talk and face[3] >= 0.3:
                close = 0.15
            d["jz"] = 1.0 + self.S["jump_zoom"] * (0.6 + 0.4 * self.I) * close
            zmax = z0 * d["jz"] * 1.03
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
                    "low": (ZONE_Y["center"], ZONE_Y["low"]), "under": None, "head_top": None,
                    "chin": None}
        cx, cy, fw, fh = face[:4]
        z = z * 1.08                          # margen para los punch-in
        head_top = cy - fh * z * 0.98
        # la caja del detector llega a la barbilla (h/2 bajo el centro): margen fijo para el
        # cuello (un margen proporcional a h se come media pantalla en un primer plano)
        chin = cy + fh * z * 0.52 + 0.012
        # primer plano (la barbilla cae bajo el pecho: no hay franja de pecho ni arriba de la
        # cabeza): el titulo va BAJO la barbilla y puede ocupar la franja de los subtitulos
        # (se ocultan mientras hay titulo)
        under = None
        if fh >= 0.25:
            # primer plano extremo: el titulo va bajo la BARBILLA (cuello / pecho y la franja de
            # los subtitulos, que se ocultan) anclado al fondo; nunca sobre boca ni barbilla
            if SAFE["bottom"] - 0.012 - (chin + 0.004) >= 0.035:
                under = (chin + 0.004, SAFE["bottom"] - 0.012)
        elif chin > ZONE_Y["center"] - 0.07:
            under = (min(max(chin - 0.01, 0.5), SAFE["bottom"] - 0.08), SAFE["bottom"] - 0.012)
        return {"top": (SAFE["top"] + 0.012, head_top - 0.02),
                "center": (chin + 0.03, ZONE_Y["center"]),
                "low": (max(ZONE_Y["center"], chin + 0.03), ZONE_Y["low"]),
                "under": under, "head_top": head_top, "chin": chin}

    def zoom_level(self, shot: dict, t0: float, t1: float) -> float:
        lv = [z for a, b, z in self.cam if a < t1 and t0 < b]
        return max(lv) if lv else shot.get("z0", 1.0)

    def geom(self, shot: dict, t0: float, t1: float, z: float | None = None) -> dict:
        """Huecos libres del rango [t0, t1]: con la pista de rostro la cabeza y la barbilla son
        las de TODO el rango (quien se inclina hacia la camara no queda bajo un titulo).
        `z` = zoom supuesto (por defecto el de la camara en ese rango)."""
        z = self.zoom_level(shot, t0, t1) if z is None else z
        g = self._geom(shot, z)
        if not shot.get("talk") or not shot.get("face") or shot.get("face_nominal") or g.get("chin") is None:
            return g
        pts = self._track_out(shot, t0, t1, z)
        if len(pts) <= 1:
            return g
        ht = min(oy - fh * 0.98 for _ox, oy, _fw, fh in pts)
        chin = max(oy + fh * 0.52 + 0.012 for _ox, oy, _fw, fh in pts)
        g = dict(g)
        g["head_top"], g["chin"] = min(g["head_top"], ht), max(g["chin"], chin)
        g["top"] = (g["top"][0], min(g["top"][1], g["head_top"] - 0.02))
        g["center"] = (max(g["center"][0], g["chin"] + 0.03), g["center"][1])
        g["low"] = (max(g["low"][0], g["chin"] + 0.03), g["low"][1])
        if g.get("under"):
            lo = max(g["under"][0], g["chin"] + 0.004)
            g["under"] = (lo, g["under"][1]) if g["under"][1] - lo >= 0.035 else None
        return g

    def _track_out(self, shot: dict, t0: float, t1: float, z: float | None = None) -> list[tuple]:
        """Rostro de la pista (2 fps) en [t0, t1] en el cuadro de salida, con el zoom anclado en la
        cara mediana: [(cx, cy, w, h)] con w/h ya escalados (zoom * 1.08 de margen para los
        punch-in). Se descartan detecciones sueltas (otra cara, un reflejo)."""
        face = shot.get("face")
        if not face:
            return []
        ax, ay, fw0, fh0 = [float(v) for v in face[:4]]
        z = self.zoom_level(shot, t0, t1) if z is None else z
        zz = z * 1.08
        pts = [tr for tr in shot.get("face_track") or [] if len(tr) >= 5 and t0 - 0.3 <= tr[0] <= t1 + 0.3]
        pts = [tr for tr in pts if 0.6 * fh0 <= float(tr[4]) <= 1.6 * fh0 and abs(float(tr[1]) - ax) < 0.35] or \
            [[0.0, ax, ay, fw0, fh0]]
        return [(ax + (float(cx) - ax) * z, ay + (float(cy) - ay) * z, float(fw) * zz, float(fh) * zz)
                for _t, cx, cy, fw, fh in pts]

    def face_rect(self, shot: dict, t0: float, t1: float, zl: float | None = None) -> tuple | None:
        """Caja de la cabeza en el cuadro de salida (zoom anclado en la cara). Si la toma trae
        pista de rostro (2 fps), es la union de las posiciones en [t0, t1]: la persona que
        camina no queda debajo de un icono."""
        face = shot.get("face")
        if not face or not shot.get("talk"):
            return None
        ax, ay, fw0, fh0 = face
        z = (self.zoom_level(shot, t0, t1) if zl is None else zl) * 1.08
        pts = [tr for tr in shot.get("face_track") or [] if t0 - 0.3 <= tr[0] <= t1 + 0.3]
        if not pts:
            pts = [[0.0, ax, ay, fw0, fh0]]
        x0 = y0 = 9.0
        x1 = y1 = -9.0
        for _t, cx, cy, fw, fh in pts:
            ox, oy = ax + (cx - ax) * z / 1.08, ay + (cy - ay) * z / 1.08     # zoom anclado en la mediana
            x0, x1 = min(x0, ox - fw * z * 0.7), max(x1, ox + fw * z * 0.7)
            y0, y1 = min(y0, oy - fh * z * 1.05), max(y1, oy + fh * z * 0.55 + 0.03)
        return (x0, y0, x1, y1)

    def face_core(self, shot: dict, t0: float, t1: float, zl: float | None = None) -> tuple | None:
        """Zona intocable de la cara (de media frente a la barbilla; la caja del detector sube
        hasta media frente): ningun texto delante la toca. El cabello y la coronilla si pueden
        quedar bajo un titulo (como en las ediciones de referencia)."""
        fr = self.face_rect(shot, t0, t1, zl)
        if fr is None:
            return None
        ax, ay, fw0, fh0 = shot["face"][:4]
        z = (self.zoom_level(shot, t0, t1) if zl is None else zl) * 1.08
        pts = [tr for tr in shot.get("face_track") or [] if t0 - 0.3 <= tr[0] <= t1 + 0.3] or \
            [[0.0, ax, ay, fw0, fh0]]
        top = min(ay + (cy - ay) * z / 1.08 - fh * z * 0.5 for _t, cx, cy, fw, fh in pts)
        bottom = fr[3]
        if fh0 >= 0.25:
            # primer plano extremo: se protege hasta la BARBILLA (con la pista del rango); el
            # cuello y el pecho si pueden quedar bajo el titulo
            pts_o = self._track_out(shot, t0, t1, zl)
            bottom = min(fr[3], max(oy + fh * 0.52 + 0.004 for _ox, oy, _fw, fh in pts_o))
        w = fr[2] - fr[0]
        return (fr[0] + 0.12 * w, max(fr[1], top), fr[2] - 0.12 * w, bottom)

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
    def _bg_at(self, shot: dict, y0: float, y1: float, t0: float, t1: float,
               x0: float | None = None, x1: float | None = None) -> dict | None:
        """Color medio del fondo en la franja [y0, y1] del cuadro de salida (None = desconocido).
        Usa el perfil por toma (shots[].rows; con [x0, x1] y shots[].cells, el de las celdas de
        esa zona) y el zoom anclado en la cara; si hay un reemplazo de fondo activo, manda el
        fondo sintetico."""
        fa, fb = self.f(t0), self.f(t1)
        for a, b, kind in self.bg_fx:
            if a < fb and fa < b and (min(b, fb) - max(a, fa)) >= 0.4 * max(1e-3, fb - fa):
                ht = self.geom(shot, t0, t1)["head_top"] if shot.get("talk") else None
                side = False
                if x0 is not None and x1 is not None and ht is not None:
                    # a un lado de la persona (fuera de su silueta: ~3 anchos de cara) tambien
                    # manda el fondo sintetico
                    pts_ = self._track_out(shot, t0, t1)
                    if pts_:
                        # el centro del objeto fuera del torso (~1 ancho de cara a cada lado)
                        xm = (x0 + x1) / 2
                        side = all(abs(xm - cx_) > 1.0 * w_ for cx_, _cy, w_, _h in pts_)
                if ht is None or y1 <= ht + 0.01 or side:
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
        cells = shot.get("cells")
        if x0 is not None and x1 is not None and cells and len(cells) == n and self.crop_w >= 0.999:
            # color LOCAL: solo las columnas bajo [x0, x1] (zoom anclado en la cara)
            m = len(cells[0])
            cx = shot["face"][0] if shot.get("face") and shot.get("talk") else 0.5
            sx0 = _clamp(cx + (x0 - cx) / z, 0.0, 1.0)
            sx1 = _clamp(cx + (x1 - cx) / z, 0.0, 1.0)
            j0, j1 = int(sx0 * m), max(int(sx0 * m) + 1, int(math.ceil(sx1 * m)))
            j0, j1 = min(m - 1, max(0, j0)), min(m, max(1, j1))
            ii0, ii1 = min(n - 1, max(0, i0)), min(n, max(i0 + 1, i1))
            sel = [cells[r][c] for r in range(ii0, ii1) for c in range(j0, j1)] or sel
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
        bg = self._bg_at(shot, y - 0.03, y + 0.03, t0, t1, x - 0.06, x + 0.06)
        if bg is not None and contrast("#FFFFFF", bg["rgb"]) < 2.4:
            return self.acc_light
        if bg is not None and contrast(self.pal["accent"], bg["rgb"]) < 2.2:
            return "#FFFFFF"
        return self.pal["accent"]

    # ---------------------------------------------------------- ocupacion
    def on_media(self, fa: float, fb: float, pad: float = 0.0) -> bool:
        """[fa, fb] (tiempo final) toca un recurso del guion (fx.media)."""
        return any(fa < b + pad - 1e-6 and a < fb + pad - 1e-6 for a, b, _ in self.media_win)

    def near_ramp(self, ft: float, before: float, after: float | None = None) -> bool:
        """ft (tiempo final) cae a menos de `before` antes / `after` despues de una union con
        rampa de velocidad (cambio de camara del montaje)."""
        after = before if after is None else after
        return any(sh.get("ramp_join") and -before <= ft - sh["f0"] <= after for sh in self.shots)

    def free(self, zone: str, fa: float, fb: float, pad: float = 0.0, cta: bool = False) -> bool:
        """`cta`: para un CTA (Seguir, enlace) las franjas que un titulo bloqueado reserva de mas
        ('lockzone:') no cuentan; su propia franja si."""
        if zone in ("center_low", "under"):
            return self.free("center", fa, fb, pad, cta) and self.free("low", fa, fb, pad, cta)
        zs = [zone] if zone != "accent" else []
        if zone in ("top", "center", "low", "accent"):
            zs.append("full")
        if zone == "full":
            zs = ["full", "top", "center", "low", "accent"]
        for z in zs:
            for a, b, iid in self.occ[z]:
                if cta and str(iid).startswith("lockzone:"):
                    continue
                if fa < b + pad - 1e-6 and a < fb + pad - 1e-6:
                    return False
        if self.windows is not None:
            if not any(wa - 0.3 <= fa and fb <= wb + 0.6 for wa, wb in self.windows):
                return False
        return True

    def free_accent(self, x: float, y: float, fa: float, fb: float, skip=()) -> bool:
        """Acentos (garabatos/iconos pequenos): no se enciman en el tiempo si estan a menos de
        ACCENT_SEP (distancia en unidades de W) ni caen sobre una escena o el CTA final. `skip`:
        ids que no cuentan (el propio acento y su gemelo al re-colocarlos)."""
        if not self.free("accent", fa, fb):
            return False
        if any(a < fb and fa < b for a, b in self.reserved):
            return False
        for a, b, iid in self.occ["accent"]:
            if iid in skip:
                continue
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

    def clear_of_ink(self, rect: tuple, fa: float, fb: float, skip=None,
                     margin: float = 0.008) -> bool:
        """`skip`: id (o ids) cuya tinta no cuenta (el titulo dueno del acento)."""
        x0, y0, x1, y1 = rect
        skips = {skip} if isinstance(skip, str) else set(skip or ())
        for a, b, rx0, ry0, rx1, ry1, iid in self.ink:
            if iid in skips or not (a < fb - 1e-6 and fa < b - 1e-6):
                continue
            if x0 < rx1 + margin and rx0 - margin < x1 and y0 < ry1 + margin and ry0 - margin < y1:
                return False
        return True

    def accent_ok(self, x: float, y: float, sz: float, fa: float, fb: float, shot: dict,
                  t0: float, t1: float, skip=None, extra: float = 1.0, own=()) -> bool:
        """Un acento de lado ~sz en (x, y) cabe: dentro de la banda SIMETRICA (un lugar y su espejo
        pasan o fallan igual), sin tocar la cabeza ni otra tinta, separado de los otros acentos.
        `own`: ids propios (el acento y su gemelo) que no cuentan al re-colocarlo."""
        hw, hh = sz / 2 * extra, sz / 2 / self.aspect * extra
        rect = (x - hw, y - hh, x + hw, y + hh)
        if rect[0] < SYM_L - 0.01 or rect[2] > SYM_R + 0.01 or \
                rect[1] < SAFE["top"] - 0.005 or rect[3] > SAFE["bottom"]:
            return False
        fr = self.face_rect(shot, t0, t1)
        if fr is not None and rect[0] < fr[2] and fr[0] < rect[2] and rect[1] < fr[3] and fr[1] < rect[3]:
            return False
        skips = ({skip} if isinstance(skip, str) else set(skip or ())) | set(own or ())
        if not self.clear_of_ink(rect, fa, fb, skips):
            return False
        return self.free_accent(x, y, fa, fb, skip=set(own or ()))

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
    def _sid_at(self, t: float) -> int | None:
        """Frase (id) que contiene t (tiempo original, +-0.35 s) o la siguiente."""
        nxt = None
        for s_ in self.sents:
            if s_["t0"] - 0.35 <= t <= s_["t1"] + 0.35:
                return s_["id"]
            if nxt is None and s_["t0"] > t:
                nxt = s_["id"]
        if nxt is not None:
            return nxt
        return self.sents[-1]["id"] if self.sents else None

    def _anchor(self, track: str, fields: dict) -> str:
        """Ancla ESTABLE del id: la frase del guion (s3) o, en transiciones de corte, la toma que
        empieza ahi (j2). Asi un re-dirigir o un cambio de estilo conserva el id del elemento de
        esa frase (la seleccion y el bloqueo del Studio lo siguen)."""
        if track == "transitions":
            at = float(fields.get("at", 0.0))
            for sh in self.shots[1:]:
                if abs(float(sh["start"]) - at) < 0.35:
                    return f"j{sh['k']}"
        t = float(fields.get("at", fields.get("start", 0.0)) or 0.0)
        sid = self._sid_at(t)
        return f"s{sid}" if sid is not None else f"t{int(round(t * 10))}"

    def _id(self, track: str, role: str, anchor: str | None = None) -> str:
        base = f"{ID_PREFIX[track]}_{role}_{anchor or 'x'}"
        iid = base
        k = 0
        while iid in self.ids:
            k += 1
            iid = base + (chr(ord("a") + k) if k < 26 else str(k))
        self.ids.add(iid)
        return iid

    def add(self, track: str, role: str, fields: dict, zone: str | None = None,
            event: bool = True) -> dict | None:
        it = {"id": self._id(track, role, self._anchor(track, fields))}
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
            # un item que arranca justo en una union (inicio de un tramo tras un corte) arranca un
            # poco DENTRO del corte: el render cuantiza los tramos a su reticula de cuadros (el
            # inicio del tramo baja hasta 1 cuadro) y sin esto el primer cuadro tras la union
            # queda sin su zoom/titulo (un cuadro abierto que parpadea en cada jump-cut)
            for ka, (kpa, kpb) in enumerate(self.tl.keeps):
                if abs(s - kpa) < 1e-4 and kpa > 1e-4:
                    prev_end = self.tl.keeps[ka - 1][1] if ka > 0 else 0.0
                    s = max(prev_end + 1e-3, s - min(0.045, (kpa - prev_end) / 2))
                    break
            it.update({"start": _r(s), "end": _r(e), "enabled": True, "src": "ai", "locked": False,
                       "intensity": 1.0})
            it.update({k: v for k, v in fields.items() if k not in ("start", "end")})
            if track == "text" and it.get("lines") and it.get("preset") != "counter":
                self._line_floor(it)
            if track == "text" and it.get("preset") == "behind_giant":
                self._sane_behind(it)
            if track == "graphics" and it.get("kind") == "doodle" and it.get("color") and \
                    _luma(it["color"]) > 0.85 and not it.get("border_color"):
                # trazo blanco: contorno oscuro si el fondo local no le da contraste de sobra
                x_, y_ = float(it.get("x", 0.5)), float(it.get("y", 0.5))
                bg_ = self._bg_at(self.shot_at(s + 0.02), y_ - 0.03, y_ + 0.03, s, e, x_ - 0.06, x_ + 0.06)
                if bg_ is None or contrast("#FFFFFF", bg_["rgb"]) < 4.5:
                    it["border_color"] = "#161616"
            if zone is None:
                zone = zone_of(track, it)
            if track in ("text", "graphics", "cutaways") and zone != "edge" and self.on_media(fa, fb):
                self.ids.discard(it["id"])           # un recurso del guion ocupa la pantalla
                return None
            if zone == "accent":
                ok = self.free_accent(float(it.get("x", 0.5)), float(it.get("y", 0.5)), fa, fb)
            else:
                ok = not zone or zone in ("bottom", "edge") or self.free(
                    zone, fa, fb, cta=track == "graphics" and it.get("kind") == "cta")
            if not ok:
                self.ids.discard(it["id"])
                return None
            for zz in (["center", "low"] if zone in ("center_low", "under") else [zone] if zone else []):
                self.occ[zz].append((fa, fb, it["id"]))
            if zone == "accent":
                self.acc_pos[it["id"]] = (float(it.get("x", 0.5)), float(it.get("y", 0.5)))
            if event:
                self.events.append((fa, it["id"]))
        self.fx[track].append(it)
        return it

    def _unplace(self, it: dict, pair: bool = True) -> None:
        """Quita un item recien colocado (pista, franjas, tinta, eventos y sus SFX pedidos). Un
        miembro de un par espejo se lleva a su gemelo (el par entra completo o no entra)."""
        iid = str(it.get("id"))
        gone = {iid}
        if pair and it.get("kind") == "doodle":
            base = iid[:-len(TWIN)] if iid.endswith(TWIN) else iid
            gone = {base, base + TWIN}
        for tr in TRACKS:
            self.fx[tr][:] = [x for x in self.fx[tr] if x is not it and str(x.get("id")) not in gone]
        for z in list(self.occ):
            self.occ[z] = [e for e in self.occ[z] if e[2] not in gone]
        self.ink = [r for r in self.ink if r[6] not in gone]
        self.events = [e for e in self.events if e[1] not in gone]
        self.sfx_req = [r for r in self.sfx_req if not any(str(r.get("src", "")).startswith(g) for g in gone)]
        for g in gone:
            self.ids.discard(g)
            self.acc_pos.pop(g, None)

    def add_pair(self, role: str, fields: dict, xr: float, rot: float = 0.0, event: bool = True) -> dict | None:
        """PAR ESPEJO de un glifo simetrico (SYM_GLYPHS): el mismo garabato en x_r y 1-x_r (mismo
        nombre, tamano, y, tiempos, color y animacion; rotacion invertida). Ids '<id>' y
        '<id>_m': graphics dibuja el gemelo reflejado (espejo exacto). Entra completo o no entra;
        el que llama ya comprobo ambos lugares con accent_ok. -> el item base (o None)."""
        xr = max(float(xr), 2 * self.XC - float(xr))
        if xr - self.XC < ACCENT_SEP / 2 - 1e-6:
            return None
        y = float(fields.get("y", 0.5))
        col = fields.get("color")
        if col is None:
            col = self.pair_color(float(fields["start"]), float(fields["end"]), xr, y)
        f = dict(fields, x=_r(xr, 3), rotation=_r(rot, 1), color=col)
        a = self.add("graphics", role, f, zone="accent", event=event)
        if not a:
            return None
        xl = 2 * self.XC - xr
        tw = dict(a, id=a["id"] + TWIN, x=_r(xl, 3), rotation=_r(-float(a.get("rotation", 0.0)), 1) + 0.0)
        if tw["id"] in self.ids:
            self._unplace(a, pair=False)
            return None
        if a.get("kind") == "doodle" and _luma(a.get("color") or "#000000") > 0.85 and not a.get("border_color"):
            # trazo blanco: si el lado espejo necesita contorno, lo llevan los dos (espejo exacto)
            bg_ = self._bg_at(self.shot_at(float(a["start"]) + 0.02), y - 0.03, y + 0.03, float(a["start"]),
                              float(a["end"]), xl - 0.06, xl + 0.06)
            if bg_ is None or contrast("#FFFFFF", bg_["rgb"]) < 4.5:
                a["border_color"] = tw["border_color"] = "#161616"
        fa, fb = self.f(float(a["start"])), self.f(float(a["end"]))
        self.ids.add(tw["id"])
        self.occ["accent"].append((fa, fb, tw["id"]))
        self.acc_pos[tw["id"]] = (xl, y)
        self.fx["graphics"].append(tw)
        return a

    def _scene_pair(self, g: dict) -> list[dict]:
        """Par espejo EXACTO de un garabato decorativo dentro de una escena (mismo glifo, tamano,
        y, color, entrada y offset; x y 1-x; giro invertido). Ids '<id>' y '<id>_m': graphics
        dibuja el gemelo reflejado (misma forma)."""
        k = self.used["scene_pair"]
        self.used["scene_pair"] += 1
        x = float(g.get("x", 0.78))
        x = max(x, 2 * self.XC - x)
        base = dict(g, id=f"pair{k}", x=_r(x, 3), rotation=_r(float(g.get("rotation", 0.0) or 0.0), 1))
        twin = dict(base, id=base["id"] + TWIN, x=_r(2 * self.XC - x, 3), rotation=_r(-base["rotation"], 1) + 0.0)
        return [base, twin]

    def pair_color(self, t0: float, t1: float, xr: float, y: float) -> str:
        """Color comun del par espejo: el que se lee de los DOS lados (si cada lado pide otro, el
        blanco, que add() contornea donde haga falta)."""
        c1 = self.doodle_color(t0, t1, xr, y)
        c2 = self.doodle_color(t0, t1, 2 * self.XC - xr, y)
        return c1 if c1 == c2 else "#FFFFFF"

    def _line_floor(self, it: dict) -> None:
        """Ninguna linea de un titulo por debajo de MIN_LINE (fraccion de W): sube la escala de
        las lineas chicas y, si alguna ya no cabe en MAXW, achica el bloque y repite."""
        for _ in range(4):
            size = float(it.get("size", 0.08))
            lines = it["lines"]
            low = [ln for ln in lines if size * float(ln.get("scale", 1.0)) < MIN_LINE - 1e-4]
            if not low:
                return
            for ln in low:
                ln["scale"] = _r(min(1.0, MIN_LINE / max(1e-3, size) + 0.002), 3)
            widest = max(_text_w(ln.get("font") or it.get("font"), size * float(ln.get("scale", 1.0)),
                                 str(ln.get("text", ""))) for ln in lines)
            if widest > self.MAXW:
                # centrado en x = 0.5 nada pasa de SYM_W (el renderer lo empujaria de lado)
                it["size"] = _r(size * self.MAXW / widest, 4)

    BEHIND_SIZE = (0.12, 0.46)        # rango de tamano del preset behind_giant (text.PRESETS)

    def _sane_behind(self, it: dict) -> None:
        """behind_giant siempre detras, con el tamano del preset y la tinta dentro de SAFE."""
        it["behind"] = True
        lo, hi = self.BEHIND_SIZE
        it["size"] = _r(_clamp(float(it.get("size", 0.3)), lo, hi), 4)
        for _ in range(4):
            r = measure_text(it, self.style)
            if r is None:
                break
            if r[1] < SAFE["top"] - 0.004:
                it["y"] = _r(float(it["y"]) + (SAFE["top"] - r[1]), 4)
            elif r[3] > SAFE["bottom"]:
                it["y"] = _r(float(it["y"]) - (r[3] - SAFE["bottom"]), 4)
            else:
                break

    def sfx(self, at: float, name: str, prio: int = 5, gain: float | None = None, pan: float = 0.0,
            src_id: str = "", vary: bool = False, dur: float | None = None, fixed_pan: bool = False) -> None:
        """Pide un SFX. `dur` (s): tiempo de revelado (typing), de trazo (pen_scribble) o
        anticipacion (riser_*): audio.py estira/recorta el sonido a esa duracion. `fixed_pan`: el
        paneo no varia (un par espejo suena al centro)."""
        if name not in SFX_NAMES or at is None:
            return
        g = SFX_GAIN.get(name, -6) if gain is None else gain
        if vary:
            key = f"sfxv:{src_id}:{name}"
            g += (self.rc.rand(key + "g") - 0.5) * 4.0
            if not fixed_pan:
                pan = pan + (self.rc.rand(key + "p") - 0.5) * 0.5
        g += self.S["sfx_db"]
        if name == "pen_scribble":
            g = min(g, -2.0)            # el lapiz acompana, nunca tapa la voz
        req = {"at": float(at), "name": name, "prio": prio, "gain": g,
               "pan": _clamp(pan, -0.6, 0.6), "src": src_id}
        if dur is not None and name in ("typing", "pen_scribble", "riser_short", "riser_long"):
            req["dur"] = _r(_clamp(float(dur), 0.15, 4.0), 2)
        self.sfx_req.append(req)

    def text_sfx(self, preset: str, t0: float, it: dict, prio: int, n_lines: int = 1,
                 line_ts: list | None = None, reveal: float | None = None) -> None:
        """SFX de entrada de un bloque: rota entre variantes del preset (+-2 dB, paneo) y marca
        cada linea nueva con un tic suave (ritmo sin saturar). `reveal` = segundos que tarda en
        escribirse (typing / pen_scribble duran eso)."""
        opts = TEXT_SFX.get(preset, ["pop"])
        name = opts[self.used["tsfx:" + preset] % len(opts)]
        self.used["tsfx:" + preset] += 1
        self.sfx(t0 + 0.05, name, prio=prio, src_id=it["id"], vary=True,
                 dur=reveal if name in ("typing", "pen_scribble") else None)
        for k, t in enumerate(line_ts or []):
            soft = "tick" if k % 2 == 0 else "pop_soft"
            self.sfx(t, soft, prio=2, gain=SFX_GAIN[soft] - 5.0, src_id=it["id"] + f"l{k}", vary=True)

    # ---------------------------------------------------------- 1. camara base
    def _jump_start(self, s: dict, prev: dict | None) -> bool | None:
        """Jump-zoom en una union 'splice' del montaje (a mitad de frase) dentro del MISMO setup de
        camara (o sin setup conocido): la toma arranca con el encuadre CONTRARIO al que cerro la
        anterior (cerrado <-> abierto), asi el salto se lee como un punch-in y no como un brinco.
        None = sin regla (el encuadre de siempre)."""
        if s.get("join") != "splice" or prev is None or prev["k"] != s["k"] - 1:
            return None
        a, b = prev.get("setup"), s.get("setup")
        if a is not None and b is not None and a != b:
            return None                  # cambio de angulo: el corte ya es un cambio de plano
        return not prev["tight"]

    def camera(self) -> None:
        prev_cam: dict | None = None     # ultimo encuadre de la toma a camara anterior (jump-zoom)
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
                jz = s.get("jz") or (1.0 + self.S["jump_zoom"] * (0.6 + 0.4 * self.I))
                push = 0.025 + 0.035 * self.I
                if s.get("face") and float(s["face"][3]) >= 0.3:
                    push = 0.012          # primer plano extremo: casi quieto (la barbilla no baja)
                # tomas largas a camara: reencuadre tipo jump-cut (cerrado/abierto) por clausula
                merged = [pc for sg in merged for pc in self._jump_pieces(sg[0], sg[1])]
                start_tight = self.rand(f"cam{s['k']}") < 0.35 and len(merged) > 1
                if self.words and s["start"] - 1e-6 <= self.words[0]["t0"] < s["end"] and \
                        s.get("face") and float(s["face"][3]) < 0.14:
                    # hook en plano abierto: arranca CERRADO (la cara llena el cuadro) y abre en
                    # la siguiente clausula, como el punch-in de un editor en el primer segundo
                    start_tight = True
                hook_shot = bool(self.words) and s["start"] - 1e-6 <= self.words[0]["t0"] < s["end"]
                js = self._jump_start(s, prev_cam)
                if js is not None:
                    start_tight = js
                prev_cam = {"k": s["k"], "setup": s.get("setup"),
                            "tight": ((len(merged) - 1) % 2 == 1) != start_tight}
                for k, (a, b) in enumerate(merged):
                    tight = (k % 2 == 1) != start_tight
                    lv = s["z0"] * (jz if tight else 1.0)
                    if k == 0 and tight and hook_shot and s.get("face") and float(s["face"][3]) < 0.14:
                        # primer cuadro del video en plano abierto: plano medio corto (cara ~18% del
                        # alto) para que el gancho se sienta cerca
                        lv = max(lv, min(self.zcap, 0.18 / max(0.04, float(s["face"][3]))))
                    dur = self.f(b) - self.f(a)
                    # quien camina no se sale del cuadro: el zoom se limita a lo que deja ver
                    # todas sus posiciones del tramo
                    zfit = self._fit_zoom(s, a, b)
                    if zfit < self.zcap - 1e-6:
                        lv = max(1.0, min(lv, zfit / (1.0 + push * min(1.0, dur / 3.0))))
                    to = lv * (1.0 + push * min(1.0, dur / 3.0))
                    self.add("effects", "cam", {"start": a, "end": b, "kind": "zoom",
                                                "from": _r(lv, 3), "to": _r(to, 3),
                                                "easing": "in_out_quad"}, event=k > 0)
                    self.cam.append((a, b, to))
            elif not fixed_cam or len(merged) == len(segs):
                d = 1 if self.rand(f"kb{s['k']}") < 0.5 else -1
                self.add("effects", "kb", {"start": s["start"], "end": s["end"], "kind": "kenburns",
                                           "from": 1.04, "to": _r(1.1 + 0.08 * self.I, 3),
                                           "pan": [_r(0.03 * d, 3), -0.015]}, event=False)

    def _fit_zoom(self, s: dict, a: float, b: float) -> float:
        """Zoom maximo (anclado en la cara mediana del tramo) que deja ver TODAS las posiciones
        del rostro en [a, b] (pista a 2 fps) con margen: la persona que camina no se sale."""
        face = s.get("face")
        pts = [tr for tr in s.get("face_track") or [] if len(tr) >= 5 and a - 0.25 <= tr[0] <= b + 0.25]
        if not face or len(pts) < 2:
            return self.zcap
        fh0 = float(face[3])
        pts = [tr for tr in pts if 0.6 * fh0 <= float(tr[4]) <= 1.6 * fh0] or pts
        x0 = min(float(tr[1]) - 0.8 * float(tr[3]) for tr in pts)
        x1 = max(float(tr[1]) + 0.8 * float(tr[3]) for tr in pts)
        ay = float(np.median([float(tr[2]) for tr in pts]))
        top = min(float(tr[2]) - 1.1 * float(tr[4]) for tr in pts)
        z = self.zcap
        if x1 - x0 > 1e-3:
            z = min(z, 0.86 / (x1 - x0))
        if ay - top > 1e-3:
            z = min(z, (ay - 0.02) / (ay - top))
        return max(1.0, z)

    JUMP_MAX = 4.2          # s (tiempo final): un encuadre fijo mas largo se parte en jump-cuts
    JUMP_MIN = 1.8

    def _jump_pieces(self, a: float, b: float) -> list[list[float]]:
        """Parte [a, b] (tiempo original, un tramo continuo a camara) en encuadres de 1.8-3.6 s
        cortando justo antes de una palabra que abre clausula/frase (pausa, puntuacion)."""
        out = []
        pos = a
        ws = [w for w in self.words if a < w["t0"] < b]
        while self.f(b) - self.f(pos) > self.JUMP_MAX:
            best = None
            for w, nx in zip(ws, ws[1:]):
                t = nx["t0"] - 0.04
                if t <= w["t1"] - 0.02:
                    t = (w["t1"] + nx["t0"]) / 2
                d0, d1 = self.f(t) - self.f(pos), self.f(b) - self.f(t)
                if d0 < self.JUMP_MIN or d0 > 3.6 or d1 < self.JUMP_MIN or not self.kept(t):
                    continue
                sc = min(0.6, nx["t0"] - w["t1"]) * 3
                sc += 1.5 if re.search(r"[.?!]$", w["text"]) else (0.8 if re.search(r"[,;:]$", w["text"]) else 0.0)
                sc += 0.6 if nx["n"] in CLAUSE_W else 0.0
                sc -= abs(d0 - 2.6) * 0.3
                if best is None or sc > best[0]:
                    best = (sc, t)
            if best is None:
                break
            out.append([pos, best[1]])
            pos = best[1]
        out.append([pos, b])
        return out

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
        if self.on_media(fa - MEDIA_TRANS_PAD, fa + MEDIA_TRANS_PAD):
            return False                         # la tarjeta del recurso no se lava con una transicion
        if self.near_ramp(fa, 0.3):
            return False                         # la rampa de velocidad ES la transicion
        if self.windows is not None and not any(wa - 0.3 <= fa <= wb + 0.3 for wa, wb in self.windows):
            return False
        self.trans_req.append({"at": at, "f": fa, "ctx": ctx, "prefer": prefer, "dir": dirn})
        self.events.append((fa, "trans"))
        return True

    @staticmethod
    def hard_join(shot: dict) -> bool:
        """La toma entra con una union de montaje de corte seco (misma seccion del guion: 'line'
        entre lineas, 'splice' a mitad de frase) o con una RAMPA de velocidad en el cambio de
        camara (la rampa ES la transicion): sin transicion ni whoosh. 'section' (o sin montaje)
        admite transicion."""
        return shot.get("join") in HARD_JOINS or bool(shot.get("ramp_join")) \
            or (shot.get("ramp") is not False and (_ramp_on(shot.get("ramp")) or _ramp_on(shot.get("ramp_in"))))

    def transitions(self) -> None:
        for ch in self.shot_changes():
            nxt, prv = ch["next"], ch["prev"]
            if self.hard_join(nxt):
                continue
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
            # un dato que se esta leyendo (precio, palabra protagonista) no se lava con una
            # transicion larga: justo despues de el solo entran transiciones secas y cortas
            guard = any(pa - 0.05 <= r["f"] <= pb + 0.45 for pa, pb in self.protect)
            if guard:
                short = [(k, w) for k, w in list(pool) + list(self.S["transitions"])
                         if k in ("whip", "zoom_blur", "glitch", "flash", "slide") and k not in recent]
                pool = short or [("whip", 1)]
                pref = None
            if pref and pref not in recent and pref in TRANS_DUR:
                kind = pref
            else:
                ok = [(k, w) for k, w in pool if k not in recent] or \
                     [(k, w) for k, w in self.S["transitions"] if k not in recent] or list(pool)
                kind = self.pick("trans", [k for k, _ in ok], weights=[w for _, w in ok], avoid_last=False)
            recent = (recent + [kind])[-2:]
            TM = _transitions_mod()
            base_dur = TRANS_DUR[kind]
            if TM is not None:
                base_dur = float((getattr(TM, "DEFAULTS", {}).get(kind) or {}).get("dur") or base_dur)
            dur = base_dur * self.S["trans_dur"] * (0.85 if r["ctx"] == "scene" else 1.0)
            if guard:
                dur = min(dur, 0.34)
            if dur < 0.06 and kind != "cut":
                kind, dur = "cut", TRANS_DUR["cut"]      # el corte seco es el kind 'cut', nunca dur 0
            dirn = r.get("dir") or ("left" if i % 2 == 0 else "right")
            fields = {"at": r["at"], "kind": kind, "dur": _r(dur, 3)}
            if kind in ("whip", "slide", "torn_paper"):
                fields["dir"] = dirn
            if kind == "flash":
                fields["color"] = "warm" if self.style == "elegante" else "white"
            elif kind == "light_leak":
                fields["color"] = "gold" if self.style == "elegante" else "warm"
            elif kind == "glitch":
                fields["color"] = self.pal["accent"]
            # punto protegido / centro: rostro de la toma A (quema, papel, zoom) o de B (circulo)
            fa_ = self.f(r["at"])
            sa = self.shot_at(self.o(max(0.0, fa_ - 0.1)))
            sb = self.shot_at(r["at"] + 0.02)
            src_shot = sb if kind == "circle" else sa
            if kind in ("film_burn", "torn_paper", "circle", "zoom_blur") and src_shot.get("talk") \
                    and src_shot.get("face") and not src_shot.get("face_nominal"):
                fields["x"] = _r(_clamp(src_shot["face"][0], 0.1, 0.9), 3)
                fields["y"] = _r(_clamp(src_shot["face"][1], 0.1, 0.8), 3)
            it = self.add("transitions", "cut" if r["ctx"] == "shot" else "scene", fields)
            if it:
                hit = r["at"]
                if TM is not None and hasattr(TM, "hit_time"):
                    try:
                        hit = float(TM.hit_time(dict(it)))
                    except Exception:
                        hit = r["at"]
                pan = -0.25 if dirn == "left" else 0.25
                # aire (whoosh) en el golpe visual + el acento sugerido por el modulo, mas bajo
                air = TRANS_SFX.get(kind)
                if air:
                    self.sfx(hit, air, prio=8, src_id=it["id"], pan=pan)
                names = [n_ for n_ in ((getattr(TM, "SFX", {}) or {}).get(kind) or []) if n_ != air] \
                    if TM is not None else []
                if names:
                    nm = names[self.used["trs:" + kind] % len(names)]
                    self.used["trs:" + kind] += 1
                    self.sfx(hit, nm, prio=4, gain=SFX_GAIN.get(nm, -6) - 3.0, src_id=it["id"] + "a", pan=pan,
                             dur=0.8 if nm.startswith("riser") else None)

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
                        or re.match(r"^[¿¡]", first["text"]) or _cap_start(self.words, first["i"]))
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
        # un bloque no termina en determinante, posesivo, cuantificador o preposicion ('aqui en
        # nuestra', 'que no tiene ningun'): esas palabras pasan al bloque siguiente (con su sustantivo)
        for k in range(len(out) - 1):
            b, nb = out[k], out[k + 1]
            while b and b[-1] and b[-1][-1]["n"] in DANGLING and not re.search(r"[,.;:?!]$", b[-1][-1]["text"]) \
                    and sum(map(len, b)) > 1 and nb[0][0]["f0"] - b[-1][-1]["f1"] < 0.7 and \
                    self.shot_at(nb[0][0]["t0"])["k"] == self.shot_at(b[-1][-1]["t0"])["k"]:
                nb[0].insert(0, b[-1].pop())
                if not b[-1]:
                    b.pop()
        return [b for b in out if b and any(b)]

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
        w1 = _text_w(font, size, " ".join(words))
        if w1 <= self.MAXW and n <= 5:
            best, cost_b = [" ".join(words)], w1 + 0.08
        for k in range(2, min(max_lines, n) + 1):
            for cuts in itertools.combinations(range(1, n), k - 1):
                parts = [words[a:b] for a, b in zip((0,) + cuts, cuts + (n,))]
                ws_ = [_text_w(font, size, " ".join(p)) for p in parts]
                cost = max(ws_) + (max(ws_) - min(ws_)) * 0.3 + 0.08 * k
                cost += sum(0.3 for p in parts[:-1] if norm(p[-1]) in STOP)
                # una linea de una sola palabra corta ('tus') queda huerfana
                cost += sum(0.35 for p in parts if len(p) == 1 and len(norm(p[0])) <= 4)
                if max(ws_) > self.MAXW:
                    cost += 5
                if cost < cost_b:
                    best, cost_b = [" ".join(p) for p in parts], cost
        return best

    def _hero_of(self, words: list[dict], emph: set) -> dict | None:
        """Palabra protagonista: de enfasis y con contenido (nunca 'BASICAMENTE', 'PERMITANME'
        ni un verbo ligero como 'HACIENDO')."""
        em = [w for w in words if w["i"] in emph and self.content_word(w)] or \
            [w for w in words if w["i"] in emph and (self._entity_word(w) or re.search(r"\d", w["text"]))]
        if not em:
            return None
        return max(em, key=lambda w: (len(w["n"]) <= 12, len(w["n"]), -w["i"]))

    def _hero_fallback(self, words: list[dict]) -> dict:
        cont = [w for w in words if self.content_word(w)] or \
            [w for w in words if w["n"] not in STOP and w["n"] not in FILLER] or words
        return max(cont, key=lambda w: (len(w["n"]) <= 12, len(w["n"])))

    def _text_spec(self, preset: str, blk: list[list[dict]], emph: set, humor: bool = False,
                   acc_words: set | None = None, compact: bool = False, label: str | None = None) -> dict:
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
            hero = self._hero_of(words, emph) or self._hero_fallback(words)
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
            j = 0
            while j < len(lines):
                # una palabra funcional sola en su linea ('que') se une a la linea chica vecina
                if lines[j].get("_small") and len(lines[j]["text"].split()) == 1 and norm(lines[j]["text"]) in STOP:
                    nb = j + 1 if j + 1 < len(lines) and lines[j + 1].get("_small") else \
                        (j - 1 if j > 0 and lines[j - 1].get("_small") else None)
                    if nb is not None:
                        a_, b_ = min(j, nb), max(j, nb)
                        lines[a_] = dict(lines[a_], text=lines[a_]["text"] + " " + lines[b_]["text"])
                        del lines[b_]
                        continue
                j += 1
            while len(lines) > 3:
                pairs = [(len(lines[j]["text"]) + len(lines[j + 1]["text"]), j) for j in range(len(lines) - 1)
                         if lines[j].get("_small") and lines[j + 1].get("_small")]
                if not pairs or (len(lines) == 4 and min(pairs)[0] > 17 and not compact):
                    break           # 4 lineas cortas se leen mejor que una linea chica larguisima
                _, j = min(pairs)
                lines[j] = dict(lines[j], text=lines[j]["text"] + " " + lines[j + 1]["text"])
                del lines[j + 1]
            size = self._fit_lines(lines, 0.19, self.MAXW)
            for _ in range(3):
                # las lineas chicas nunca bajan de MIN_LINE (0.5 de un bloque achicado = ilegible)
                k_min = MIN_LINE / max(1e-3, size)
                if all(ln.get("scale", 1.0) >= k_min - 1e-4 for ln in lines):
                    break
                for ln in lines:
                    if ln.get("_small"):
                        ln["scale"] = _r(max(ln["scale"], min(0.8, k_min)), 3)
                size = self._fit_lines(lines, size, self.MAXW)
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
            size = self._fit_lines(lines, max(0.082, self.title_floor * 1.04), 0.62)
            # la escalera va DENTRO de su caja y la caja se centra en x = 0.5 (simetria): text.py
            # arma las lineas a la izquierda con sus pasos y ancla el bloque por su centro
            spec.update({"lines": lines, "size": size, "anim_in": "word_stagger", "align": "center",
                         "accent_words": emw})
        else:
            base = {"title_pop": 0.085, "serif_elegant": 0.1, "script_accent": 0.11,
                    "highlight_marker": 0.07, "kicker": 0.062, "typewriter": 0.058}.get(preset, 0.08)
            base = max(base, self.title_floor * 1.04)
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
            # una linea de una sola palabra corta ("no", "y") se une a la vecina que quepa
            k = 0
            while len(texts) > 1 and k < len(texts):
                if len(texts[k].split()) == 1 and len(texts[k]) <= 3:
                    j = k + 1 if k + 1 < len(texts) else k - 1
                    merged = (texts[k] + " " + texts[j]) if j > k else (texts[j] + " " + texts[k])
                    if _text_w(font, base, merged) <= self.MAXW / 0.8:
                        texts[min(k, j)] = merged
                        del texts[max(k, j)]
                        continue
                k += 1
            lines = [{"text": t, "scale": 1.0, "font": font} for t in texts]
            size = self._fit_lines(lines, base, self.MAXW)
            anim = {"title_pop": "word_stagger", "serif_elegant": "rise", "script_accent": "mask",
                    "highlight_marker": "pop", "kicker": "slide_left", "typewriter": "type"}[preset]
            spec.update({"text": "\n".join(texts), "size": size, "anim_in": anim, "accent_words": emw})
            if preset == "highlight_marker":
                spec.update({"accent_words": emw or [_display(words[-1]["text"])], "shadow": 0.3})
            elif preset == "kicker":
                # etiqueta EXPLICITA (rol / seccion / dato) y el bloque completo como cuerpo; la
                # etiqueta no baja de MIN_LINE (el cuerpo nunca baja del piso de titulos)
                spec.update({"shadow": 0.3, "kicker": (label or self.ent.get("role") or "Tip").upper(),
                             "kicker_scale": _r(min(0.8, MIN_LINE / max(1e-3, self.title_floor) + 0.006), 3)})
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
        # la linea mas chica del bloque tambien respeta el piso de lectura (MIN_LINE)
        k_min = min([float(ln.get("scale", 1.0)) for ln in lines] or [1.0])
        min_size = max(min_size, MIN_LINE / max(0.05, k_min))
        for z in (z,):
            if not self.free(z, fa, fb) or (z == "under" and not g.get("under")):
                continue
            lo, hi = (g["center"][0], g["low"][1]) if z == "center_low" else g[z]
            if z in ("center", "center_low", "low", "under"):
                # una pastilla o tarjeta que asoma desde abajo en la franja (p. ej. la pastilla de
                # reservas de dos lineas sostenida hasta el final): el titulo termina arriba de ella
                for a_, b_, _x0, y0_, _x1, _y1, iid_ in self.ink:
                    if str(iid_).startswith("gr_") and a_ < fb - 1e-6 and fa < b_ - 1e-6 and lo < y0_ < hi:
                        hi = y0_ - 0.012
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
            elif z == "under":
                y = hi - h / 2 - 0.01          # primer plano: al fondo de la franja, lejos de la barbilla
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
        """x de un bloque de texto: SIEMPRE el centro (regla de simetria; cascade incluida)."""
        return self.XC

    COUNT_DUR = 0.45                 # s de conteo del precio (text.counter count_dur)

    # presencia: ningun titulo por debajo de ~0.05 W (antes kicker 0.036 / cascada 0.045)
    MIN_SIZE = {"kicker": 0.052, "stack": 0.09, "title_pop": 0.058, "serif_elegant": 0.066,
                "script_accent": 0.075, "highlight_marker": 0.054, "cascade": 0.056,
                "typewriter": 0.052}

    def min_size(self, preset: str) -> float:
        """Tamano minimo de un titulo: el del preset y nunca menos que 1.15x el subtitulo."""
        return max(self.MIN_SIZE.get(preset, 0.05), self.title_floor)

    # etiqueta del kicker por seccion del guion (nunca una linea de la voz hecha pastilla)
    KICKER_SECTION = {"problema": "Ojo", "beneficio": "Beneficio", "oferta": "Promo", "cierre": "Recuerda"}

    def kicker_label(self, ws: list[dict], use: bool = True) -> str | None:
        """Etiqueta natural para un kicker: el rol del video (una vez, 'PERFUMERIA'), la seccion
        del guion ('OJO', 'PROMO') o 'DATO' si el bloque trae un numero. None = este bloque no
        lleva kicker (se usa otro preset)."""
        cands = []
        role = str(self.ent.get("role") or "").strip()
        if role and len(role) <= 16:
            cands.append((role, 1))
        s = ws[0].get("s", -1) if ws else -1
        sec = (self.sents[s]["info"].get("section") if isinstance(s, int) and 0 <= s < len(self.sents) else None)
        if sec in self.KICKER_SECTION:
            cands.append((self.KICKER_SECTION[sec], 2))
        if any(re.search(r"\d", w["text"]) for w in ws):
            cands.append(("Dato", 2))
        for lab, cap in cands:
            if self.kicker_labels[lab] < cap and self.last.get("kicker_label") != lab:
                if use:
                    self.kicker_labels[lab] += 1
                    self.last["kicker_label"] = lab
                return lab
        return None

    def title_block(self, blk: list[list[dict]], preset: str, emph: set, zones: list[str], t0: float,
                    t1: float, role: str, humor: bool = False, fallback: bool = True,
                    acc_words: set | None = None, first_bonus: float = 1.0) -> dict | None:
        ws = [w for ln in blk for w in ln]
        shot = self.shot_at(ws[0]["t0"])
        g = self.geom(shot, t0, t1)
        preset = self.sub_preset(preset)
        label = None
        if preset == "kicker":
            label = self.kicker_label(ws, use=False)
            if label is None:
                preset = "title_pop"          # sin etiqueta natural no hay kicker
        compact = first_bonus > 1.0
        if compact and g.get("head_top"):
            # sobre el fondo reemplazado el hueco llega casi a la cabeza (no hay nada detras)
            g = dict(g, top=(g["top"][0], g["top"][1] + 0.014))
        spec0 = self._text_spec(preset, blk, emph, humor, acc_words, compact, label=label)
        fa, fb = self.f(t0), self.f(t1)
        fr = self.face_rect(shot, t0, t1)
        zones_try = list(zones)
        if g.get("under") and "under" not in zones_try:
            zones_try.append("under")          # primer plano: bajo la barbilla
        r = None
        spec = None
        while zones_try:
            spec = copy.deepcopy(spec0)
            pl = self._place(spec, g, zones_try, fa, fb, self.min_size(preset), first_bonus)
            if pl is None and fallback and preset not in ("title_pop", "kicker"):
                spec = self._text_spec("title_pop", blk, emph, humor, acc_words)
                pl = self._place(spec, g, zones_try, fa, fb, self.min_size("title_pop"), first_bonus)
            if pl is None:
                return None
            zone, y, size = pl
            spec["size"] = _r(size, 4)
            x = self._x_for(spec)
            spec.update({"start": t0, "end": t1, "x": _r(x, 3), "y": _r(y, 4)})
            probe = {k: v for k, v in spec.items() if not k.startswith("_")}
            r = measure_text(probe, self.style)
            # nunca texto sobre la cara (ojos, nariz, boca): si la tinta real la toca, se prueba
            # otra zona. El cabello de la coronilla si puede rozarse (descendentes de la ultima linea)
            core = None if fr is None else self.face_core(shot, t0, t1)

            def hits(rr):
                return rr is not None and core is not None and rr[0] < core[2] and core[0] < rr[2] \
                    and rr[1] < core[3] and core[1] < rr[3]
            if hits(r) and zone in ("center", "center_low", "low", "under"):
                # bajo la cara: se baja o se achica dentro de su franja antes de descartarla
                hi_z = g["low"][1] if zone == "center_low" else g[zone][1]
                k_min = min([float(ln.get("scale", 1.0)) for ln in spec.get("_lines") or []] or [1.0])
                for _ in range(6):
                    dy = core[3] + 0.006 - r[1]
                    if r[3] + dy <= hi_z + 1e-4:
                        spec["y"] = _r(float(spec["y"]) + dy, 4)
                    else:
                        spec["size"] = _r(float(spec["size"]) * 0.9, 4)
                        if spec["size"] < self.min_size(spec["preset"]) * 0.97 or \
                                spec["size"] * k_min < MIN_LINE * 0.97:
                            r = (0.0, 0.0, 1.0, 1.0)      # no cabe con presencia: otra zona
                            break
                    probe = {k: v for k, v in spec.items() if not k.startswith("_")}
                    r = measure_text(probe, self.style)
                    if not hits(r) and r is not None and r[3] <= hi_z + 1e-4:
                        break
                size, y = float(spec["size"]), float(spec["y"])
            if hits(r):
                zones_try = [z_ for z_ in zones_try if z_ != zone and not (zone == "center_low" and z_ in ("center", "low"))]
                r = None
                continue
            break
        else:
            return None
        lines = spec.pop("_lines")
        if r is None:
            bw = max(_text_w(ln["font"], size * ln.get("scale", 1.0), ln["text"]) for ln in lines)
            bh = self._block_h(lines, size)
            r = (x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2)
        self._apply_ink(spec, self._bg_at(shot, r[1], r[3], t0, t1))
        mode = spec.pop("_mode", "dark")
        it = self.add("text", role, spec, zone=zone)
        if not it:
            return None
        if it.get("preset") == "kicker" and label:
            self.kicker_labels[label] += 1
            self.last["kicker_label"] = label
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

    def _doodle_sfx(self, name: str, t: float, x: float, src: str, pair: bool = False) -> None:
        """SFX de un garabato (paneo segun su x). Un par espejo lleva UN solo SFX, al centro."""
        pan = 0.0 if pair else (x - 0.5) * 0.8
        kw = {"prio": 3, "pan": pan, "src_id": src, "vary": True, "fixed_pan": pair}
        if name in ("sparkle", "star4", "star5", "burst", "rays"):
            self.sfx(t, "sparkle", **kw)
        elif name in ("question", "exclaim", "heart", "check"):
            self.sfx(t, "pop_soft", **kw)
        elif name == "lightning":
            self.sfx(t, "impact_soft", **kw)
        else:        # trazos largos: subrayado, circulo, flechas, garabatos
            self.sfx(t, "pen_scribble", dur=_draw_time(name), **kw)

    def accent_for(self, it: dict, ws: list[dict], emph: set, key: str, t0: float, t1: float,
                   icons: list[str] | None = None, force: str | None = None,
                   icon: str | None = None, only: bool = False) -> dict | None:
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
                # la escalera (cascade) no tiene su ultima linea centrada: sin subrayado ni circulo
                if it.get("box") or it.get("preset") in ("highlight_marker", "kicker", "cascade") or len(lines) > 2:
                    dd = [d_ for d_ in dd if d_ not in ("underline", "circle_scribble")] or ["sparkle"]
                choice = self.pick("doodle", dd)
        rect = (x0, y0, x1, y1)
        acc_col = self.acc_light if mode == "light" else self.pal["accent"]
        if choice == "icon" and icon:
            if fb - fa < 0.9:
                return None
            # un icono tiene mano (y duplicado se lee mal): solo centrado, arriba / abajo del titulo
            # o sobre la cabeza
            spot = self.accent_spot(rect, 0.12, fa, fb, shot, t0, t1)
            if spot is not None:
                ix, iy, sz = spot[0]
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
            if only:
                return None
            choice = self.pick("doodle", [d for d in self.S["doodles"]
                                          if d not in ("underline", "circle_scribble") and not d.startswith("arrow")]
                               or ["sparkle"])
        name = choice if choice in DOODLES else "sparkle"
        if name.startswith("arrow") or name == "circle_scribble":
            # las flechas senalan algo (arrow_to); el circulo va en la linea protagonista (hero_mark)
            name = "sparkle"
        sz = {"sparkle": 0.1, "star4": 0.09, "burst": 0.15, "question": 0.11, "cross": 0.1,
              "exclaim": 0.1, "lightning": 0.1, "star5": 0.09, "rays": 0.16, "swirl": 0.12,
              "loop": 0.13, "heart": 0.1, "check": 0.1, "squiggle": 0.14}.get(name, 0.12)
        fields = {"start": t0, "end": t1, "kind": "doodle", "name": name, "draw": True,
                  "wiggle": 0.35, "color": acc_col, "anim_in": "draw",
                  "anim_out": "fade", "rotation": 0.0}
        own = it["id"]
        if name == "underline":
            last = lines[-1]
            lw = min(x1 - x0, _text_w(last["font"], float(it["size"]) * last.get("scale", 1.0), last["text"]) * 1.05)
            uy = min(y1 + 0.01, SAFE["bottom"] - 0.012)
            if uy < y1 - 0.004:
                return None               # no cabe bajo el texto: nunca un subrayado que lo tacha
            ux = self.XC                  # el bloque (y su ultima linea) va centrado en x = 0.5
            rect = (ux - lw / 2, uy - 0.012, ux + lw / 2, uy + 0.012)
            if not (self.clear_of_ink(rect, fa, fb, skip=own) and self.free_accent(ux, uy, fa, fb)):
                return None
            fields.update({"x": _r(ux, 3), "y": _r(uy, 4), "size": _r(min(self.MAXW, lw), 3)})
            g = self.add("graphics", "doodle", fields, zone="accent")
            if g:
                self.last["accent_kind"] = "doodle"
                self._doodle_sfx(name, t0 + 0.03, fields["x"], g["id"])
            return g
        # glifo simetrico: par espejo a los lados del titulo (o de la cabeza); si no, uno centrado.
        # Glifo con mano (?, !, rayo, espiral...): solo centrado. Si nada cabe, sin acento
        spots = self.accent_spot(rect, sz, fa, fb, shot, t0, t1, pair=name in SYM_GLYPHS)
        if spots is None:
            return None
        px, py, sz = spots[0]
        fields.update({"y": _r(py, 4), "size": sz})
        if len(spots) == 2:
            rot = _r((self.rand(key + "r") - 0.5) * 24, 1)
            fields["color"] = acc_col
            g = self.add_pair("doodle", fields, px, rot)
            if g:
                self.last["accent_kind"] = "doodle"
                self._doodle_sfx(name, t0 + 0.03, self.XC, g["id"], pair=True)
            return g
        # centrado: un glifo simetrico conserva su giro; uno con mano va derecho (girado, su tinta
        # se cargaria a un lado)
        rot = _r((self.rand(key + "r") - 0.5) * 24, 1) if name in SYM_GLYPHS else 0.0
        fields.update({"x": _r(px, 3), "rotation": rot})
        g = self.add("graphics", "doodle", fields, zone="accent")
        if g:
            self.last["accent_kind"] = "question" if name == "question" else "doodle"
            self._doodle_sfx(name, t0 + 0.03, fields["x"], g["id"])
        return g

    def beat_snap(self, t: float, tol: float = 0.1, down: bool = False) -> float:
        """t (original) -> el beat de la musica mas cercano (si esta a <= tol s en tiempo final y
        no cruza un corte). Sin rejilla (musica no sincronizada) devuelve t."""
        g = self.grid
        if not g:
            return t
        arr = (g.get("downbeats") if down else None) or g.get("beats") or []
        if not arr:
            return t
        ft = self.f(t)
        b = min(arr, key=lambda x: abs(x - ft))
        if abs(b - ft) > tol or abs(b - ft) < 1e-3:
            return t
        t2 = self.o(b)
        if not self.kept(t2) or abs((t2 - t) - (b - ft)) > 0.02:
            return t
        self.snapped.append((ft, b))
        return t2

    def drop_hit(self) -> None:
        """Golpe de la musica (drop): punch en seco + impacto, salvo que ahi ya haya una
        transicion (el cambio de toma ya es el golpe) o una escena."""
        g = self.grid
        if not g or g.get("drop") is None:
            return
        fd = float(g["drop"])
        if fd < 0.4 or fd > self.D - 1.0:
            return
        if any(abs(r["f"] - fd) < 0.25 for r in self.trans_req) or \
                any(abs(fd - ch["f"]) < 0.25 for ch in self.shot_changes()):
            return
        t = self.o(fd)
        it = self.punch_at(t, 0.12 + 0.06 * self.I, 0.6, role="drop", min_gap=0.8)
        if it:
            self.sfx(t, "impact_soft", prio=7, src_id=it["id"])

    def occlusion(self, item: dict, shot: dict, t0: float, t1: float) -> float:
        """Fraccion (ponderada) de la linea MAS tapada que quedaria detras de la persona si el
        texto va DETRAS: cabeza (con cabello) + hombros/torso estimados desde el rostro. Se mide
        cada linea (una linea chica tapada tambien vuelve ilegible el bloque)."""
        fr = self.face_rect(shot, t0, t1)
        if fr is None:
            return 0.0
        lines = measure_lines(item, self.style)
        if not lines:
            r = measure_text(item, self.style)
            lines = [r] if r else []
        if not lines:
            return 0.0
        hx0, hy0, hx1, hy1 = fr
        cx = (hx0 + hx1) / 2
        hw = (hx1 - hx0) / 2
        body = [(hx0 - hw * 0.1, hy0 - 0.01, hx1 + hw * 0.1, hy1),                 # cabeza + cabello
                (cx - hw * 0.9, hy1 - 0.01, cx + hw * 0.9, hy1 + 0.04),               # cuello
                (cx - hw * 2.6, hy1 + 0.035, cx + hw * 2.6, 1.0)]                     # hombros y torso
        worst = 0.0
        for x0, y0, x1, y1 in lines:
            area = max(1e-6, (x1 - x0) * (y1 - y0))
            cov = 0.0
            mid = y0 + (y1 - y0) * 0.55
            for k, (bx0, by0, bx1, by1) in enumerate(body):
                ix = max(0.0, min(x1, bx1) - max(x0, bx0))
                # la mitad de arriba de las letras pesa mas para leer que los pies
                top = max(0.0, min(mid, by1) - max(y0, by0))
                bot = max(0.0, min(y1, by1) - max(mid, by0))
                c = ix * (1.3 * top + 0.7 * bot)
                cov += c * (0.8 if k == 0 else 1.0)          # la cabeza es una elipse, no una caja
            worst = max(worst, cov / area)
        return min(1.0, worst)

    BEHIND_MAX = 0.3           # oclusion (ponderada) maxima para que un texto detras se lea

    def legible_behind(self, item: dict, shot: dict, t0: float, t1: float,
                       max_occ: float | None = None, top_min: float | None = None) -> dict:
        """Texto detras del sujeto SOLO si se lee (oclusion <= BEHIND_MAX; una palabra gigante
        aguanta un poco mas, `max_occ`). Si no, sube hasta que solo lo roce la coronilla; si aun
        asi no se lee, va delante (pila) sobre el cabello, nunca de las cejas para abajo."""
        if not item.get("behind"):
            return item
        lim = self.BEHIND_MAX if max_occ is None else max_occ
        if self.occlusion(item, shot, t0, t1) <= lim:
            return item
        g = self.geom(shot, t0, t1)
        ht = g.get("head_top")
        r = measure_text(item, self.style)
        if ht is None or r is None:
            out = dict(item, behind=False)
            if out.get("preset") == "behind_giant":
                out["preset"] = "stack"
            return out
        top_min = SAFE["top"] if top_min is None else max(SAFE["top"], top_min)
        for _ in range(10):
            dy = min(0.02, max(0.0, r[3] - (ht + 0.02)))
            if dy <= 0 or r[1] - dy < top_min:
                break
            item = dict(item, y=_r(float(item["y"]) - dy, 4))
            r = measure_text(item, self.style) or r
            if self.occlusion(item, shot, t0, t1) <= lim:
                return item
        # delante y por encima de la cabeza, dentro de SAFE (achica hasta caber en la franja
        # [SAFE.top, cabeza]); nunca sobre la cara. Delante ya no es 'gigante detras': pasa a
        # pila (behind_giant siempre va con behind:true)
        item = dict(item, behind=False)
        if item.get("preset") == "behind_giant":
            item["preset"] = "stack"
        # delante puede cubrir el cabello (como un editor) pero no la cara (face_core)
        brow = ht - 0.012
        core = self.face_core(shot, t0, t1) if shot.get("talk") else None
        if core is not None:
            brow = max(brow, core[1] - 0.006)
        lo_, hi_ = top_min + 0.005, brow
        for _ in range(16):
            r = measure_text(item, self.style)
            if r is None:
                break
            h_ = r[3] - r[1]
            if h_ > hi_ - lo_:
                item["size"] = _r(float(item["size"]) * max(0.7, (hi_ - lo_) / h_ * 0.98), 4)
                continue
            if r[3] > hi_:
                item["y"] = _r(float(item["y"]) - (r[3] - hi_), 4)
            elif r[1] < lo_:
                item["y"] = _r(float(item["y"]) + (lo_ - r[1]), 4)
            else:
                break
        return item

    def hero_mark(self, it: dict, ws: list[dict], key: str, t_hero: float, t1: float,
                  hero: dict | None = None) -> dict | None:
        """Garabato de enfasis SOBRE la palabra protagonista (como un editor a mano): subrayado
        bajo SU linea o circulo alrededor de un numero / palabra corta. Se dibuja cuando se
        pronuncia (nunca antes de que aparezca su linea), con SFX de lapiz."""
        # solo bloques CENTRADOS (cada linea centrada en x = 0.5): en la escalera (cascade) la
        # linea no esta centrada y la marca romperia la simetria
        if it.get("preset") not in ("stack", "title_pop", "serif_elegant") or it.get("box"):
            return None
        rows = measure_lines(it, self.style, with_text=True)
        spec_lines = it.get("lines") or [{"text": t_} for t_ in str(it.get("text", "")).split("\n")]
        # solo si el layout respeto las lineas del director (si re-envolvio, la caja no es la linea)
        if not rows or len(rows) != len(spec_lines) or any(
                norm(txt) != norm(str(sl.get("text", ""))) for (_, txt), sl in zip(rows, spec_lines)):
            return None
        lines = [bx for bx, _ in rows]
        # linea protagonista: la que CONTIENE la palabra protagonista (la marca entra cuando se
        # dice); sin palabra, la de mayor escala (stack) o la que trae el acento
        k = None
        if hero is not None:
            hn = norm(_display(hero["text"]))
            for i, sl in enumerate(spec_lines):
                if hn and hn in [norm(x) for x in str(sl.get("text", "")).split()]:
                    k = i
                    break
            if k is None:
                return None
            t_hero = max(t_hero, hero["t0"])
        if k is None:
            acc = {norm(w) for w in it.get("accent_words") or []}
            k = max(range(len(lines)), key=lambda i: (float(spec_lines[i].get("scale", 1.0)),
                                                      any(norm(w) in acc for w in str(spec_lines[i].get("text", "")).split()),
                                                      lines[i][2] - lines[i][0]))
        x0, y0, x1, y1 = lines[k]
        txt = str(spec_lines[k].get("text", ""))
        fa, fb = self.f(t_hero), self.f(t1)
        if fb - fa < 0.7:
            return None
        shot = self.shot_at(ws[0]["t0"])
        mode = (self.last.get("hero_mark"))
        short = len(norm(txt)) <= 7 or bool(re.fullmatch(r"[\d$.,]+", txt.strip()))
        name = "circle_scribble" if (short and mode != "circle_scribble") else "underline"
        col = self.acc_light if it.get("color") == self.pal["dark"] else self.pal["accent"]
        if norm(str(it.get("accent", ""))) and it.get("accent") and _luma(it["accent"]) > 0.35 \
                and it.get("color") != self.pal["dark"]:
            col = "#FFFFFF" if hue_dist(it["accent"], self.pal["accent"]) < 30 else self.pal["accent"]
        # huecos hasta las lineas vecinas del mismo bloque (el trazo no las cruza)
        above = max([ln[3] for i, ln in enumerate(lines) if i != k and ln[3] <= y0 + 0.002] + [-1.0])
        below = min([ln[1] for i, ln in enumerate(lines) if i != k and ln[1] >= y1 - 0.002] + [2.0])
        if name == "circle_scribble":
            w_, h_ = (x1 - x0) * 1.28 + 0.03, (y1 - y0) * 1.7 + 0.02
            cx, cy = self.XC, (y0 + y1) / 2            # la linea va centrada: la marca tambien
            room = min(cy - above, below - cy) - 0.006
            h_ = min(h_, 2 * room)
            if h_ < (y1 - y0) * 1.18:
                name = "underline"            # no cabe el circulo entre lineas: subrayado
            else:
                rect = (cx - w_ / 2, cy - h_ / 2, cx + w_ / 2, cy + h_ / 2)
                fields = {"x": _r(cx, 3), "y": _r(cy, 4), "size": _r(w_, 3),
                          "aspect": _r(_clamp(h_ * self.aspect / w_, 0.3, 1.0), 3)}
        if name == "underline":
            w_ = min(self.MAXW, (x1 - x0) * 1.06)
            # sobre los subtitulos no baja; un titulo que YA ocupa su franja (primer plano, los
            # subtitulos se ocultan) solo respeta SAFE. Nunca tacha la linea
            lim = self.cap_top - 0.02 if y1 < self.cap_top - 0.01 else SAFE["bottom"] - 0.012
            cx, cy = self.XC, min(y1 + 0.012, lim)
            if cy + 0.014 > below - 0.004 or cy < y1 - 0.004:
                return None
            rect = (cx - w_ / 2, cy - 0.012, cx + w_ / 2, cy + 0.014)
            fields = {"x": _r(cx, 3), "y": _r(cy, 4), "size": _r(w_, 3)}
        if rect[0] < SYM_L - 0.02 or rect[2] > SYM_R + 0.02 or rect[1] < SAFE["top"] - 0.01:
            return None
        fr = self.face_rect(shot, t_hero, t1)
        if fr is not None and rect[0] < fr[2] and fr[0] < rect[2] and rect[1] < fr[3] and fr[1] < rect[3]:
            return None
        if not self.clear_of_ink(rect if name == "underline" else (rect[0], rect[1], rect[2], rect[3]),
                                 fa, fb, skip=it["id"]):
            return None
        if not self.free_accent(cx, cy, fa, fb):
            return None
        fields.update({"start": t_hero, "end": t1, "kind": "doodle", "name": name, "draw": True,
                       "wiggle": 0.3, "color": col, "anim_in": "draw", "anim_out": "fade", "rotation": 0.0})
        g = self.add("graphics", "mark", fields, zone="accent")
        if g:
            self.last["hero_mark"] = name
            self._doodle_sfx(name, t_hero + 0.02, cx, g["id"])
        return g

    def arrow_to(self, rect: tuple, t0: float, t1: float, shot: dict, own: str) -> dict | None:
        """Flecha dibujada a mano que senala una tarjeta/CTA: UNA flecha VERTICAL centrada (x =
        path_to x = 0.5, bend 0) que llega por arriba o por abajo desde un hueco libre (nunca
        cruza la cara ni otra tinta). Una flecha de lado rompe la simetria: si no cabe centrada,
        no hay flecha."""
        fa, fb = self.f(t0), self.f(t1)
        if fb - fa < 1.2:              # la flecha tarda ~0.5 s en dibujarse
            return None
        x0, y0, x1, y1 = rect
        cx = self.XC
        cands = [(y0 - 0.1, y0 - 0.012),        # llega por arriba
                 (y1 + 0.09, y1 + 0.012)]       # llega por abajo
        fr = self.face_rect(shot, t0, t1)
        for sy, ey in cands:
            box = (cx - 0.03, min(sy, ey) - 0.015, cx + 0.03, max(sy, ey) + 0.015)
            if box[1] < SAFE["top"] or box[3] > min(SAFE["bottom"], self.cap_top - 0.005):
                continue
            if fr is not None and box[0] < fr[2] and fr[0] < box[2] and box[1] < fr[3] and fr[1] < box[3]:
                continue
            if not self.clear_of_ink(box, fa, fb, skip=own, margin=0.0):
                continue
            if not self.free_accent(cx, sy, fa, fb):
                continue
            ta = self.o(fa + (0.3 if fb - fa < 1.8 else 0.4))
            g = self.add("graphics", "arrow", {
                "start": ta, "end": t1, "kind": "doodle", "name": "arrow_straight", "x": _r(cx, 3),
                "y": _r(sy, 4), "size": 0.14, "path_to": [_r(cx, 3), _r(ey, 4)], "bend": 0.0,
                "color": self.doodle_color(ta, t1, cx, (sy + ey) / 2), "draw": True, "wiggle": 0.2,
                "anim_in": "draw", "anim_out": "fade", "rotation": 0.0}, zone="accent")
            if g:
                self.ink.append((self.f(ta), fb, *box, g["id"]))
                self._doodle_sfx("arrow_straight", ta + 0.02, cx, g["id"])
            return g
        return None

    def accent_spot(self, rect: tuple, sz: float, fa: float, fb: float, shot: dict, t0: float, t1: float,
                    pair: bool = False, own=()) -> list[tuple[float, float, float]] | None:
        """Lugar SIMETRICO para un acento de tamano ~sz junto a un titulo (centrado en x = 0.5):
        [(x, y, s)] (uno, centrado: arriba o abajo del bloque, o sobre la cabeza) o, con `pair`
        (solo glifos simetricos), [(x_r, y, s), (1-x_r, y, s)] (par espejo a los lados o en las
        esquinas del bloque, o a los lados de la cabeza), achicando hasta 70% si no cabe. Cada
        miembro pasa accent_ok (banda simetrica, cara, tinta, separacion); None = no cabe
        simetrico (sin acento). `own`: ids propios que no cuentan (re-colocar uno existente)."""
        x0, y0, x1, y1 = rect
        cy = (y0 + y1) / 2
        xc = self.XC
        fr = self.face_rect(shot, t0, t1)

        def ok(px, py, s_):
            return self.accent_ok(px, py, s_, fa, fb, shot, t0, t1, own=own)
        if pair:
            half = max(xc - x0, x1 - xc)        # medio ancho del bloque medido desde el centro
            for k in (1.0, 0.85, 0.7):
                s_ = sz * k
                hw, hh = s_ / 2, s_ / 2 / self.aspect
                c = [(xc + half + hw + 0.03, cy),                    # a los lados del bloque
                     (xc + half - hw * 0.4, y0 - hh * 0.9),           # esquinas de arriba
                     (xc + half - hw * 0.4, y1 + hh * 0.9)]           # esquinas de abajo
                if fr is not None:
                    fx0, fy0, fx1, fy1 = fr
                    fh = max(xc - fx0, fx1 - xc)
                    c += [(xc + fh + hw + 0.02, (fy0 + fy1) / 2 - 0.02),   # a los lados de la cabeza
                          (xc + fh + hw + 0.01, fy0 - hh * 0.6)]
                for px, py in c:
                    if px - xc < ACCENT_SEP / 2 + 1e-3:
                        continue            # tan juntos se leen como uno (y se enciman)
                    if ok(px, py, s_) and ok(2 * xc - px, py, s_):
                        return [(px, py, s_), (2 * xc - px, py, s_)]
        for k in (1.0, 0.85, 0.7):
            s_ = sz * k
            hh = s_ / 2 / self.aspect
            c = [(xc, y0 - hh - 0.012), (xc, y1 + hh + 0.012)]
            if fr is not None:
                c.append((xc, fr[1] - hh - 0.01))                     # sobre la cabeza
            for px, py in c:
                if ok(px, py, s_):
                    return [(px, py, s_)]
        return None

    def punch_at(self, t: float, amount: float, hold: float, role: str = "punch",
                 min_gap: float = 1.6) -> dict | None:
        t = self.beat_snap(t, 0.1)
        ft = self.f(t)
        if any(abs(ft - p) < min_gap for p in self.punches):
            return None
        if any(a <= ft <= b for a, b, _ in self.occ["full"]):
            return None
        shot = self.shot_at(t)
        if not shot["talk"]:
            return None
        cut = self.S.get("punch_mode") == "cut"
        # modo corte (dinamico/energetico, como la referencia): entra en seco en la palabra y se
        # sostiene hasta el final del item (hold null), luego regresa en seco: un jump-zoom
        end = min(t + (max(hold, 0.55) + 0.45 if cut else hold + 0.35), shot["end"] - 0.02)
        if end - t < 0.3:
            return None
        amount *= self.S["punch"]
        fields = {"start": t, "end": end, "kind": "punch", "amount": _r(_clamp(amount, 0.03, 0.4), 3),
                  "hold": None if cut else _r(hold, 2)}
        if cut:
            fields.update({"mode": "cut", "release": "cut"})
        it = self.add("effects", role, fields)
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

    @staticmethod
    def _filler_ids(ws: list[dict]) -> set:
        """Indices de las palabras que forman una frase de relleno completa (FILLER_PH)."""
        ns = [w["n"] for w in ws]
        drop: set = set()
        for ph in FILLER_PH:
            p_ = ph.split()
            for k in range(len(ns) - len(p_) + 1):
                if ns[k:k + len(p_)] == p_:
                    drop |= {ws[j]["i"] for j in range(k, k + len(p_))}
        return drop

    def _entity_word(self, w: dict) -> bool:
        """Nombre propio / marca: mayuscula que no abre oracion ('Aurelia', 'Norte')."""
        if not _is_cap(w) or w["n"] in STARTERS or w["n"] in FILLER or len(w["n"]) < 2:
            return False
        s = w.get("s", -1)
        if isinstance(s, int) and 0 <= s < len(self.sents) and self.sents[s]["w0"] == w["i"]:
            return False                      # primera palabra de la oracion
        return True

    @staticmethod
    def content_word(w: dict) -> bool:
        """Palabra con contenido propio (sustantivo, adjetivo, verbo pleno): ni funcional, ni
        muletilla, ni verbo ligero/deictico, ni adverbio en -mente."""
        n = w["n"]
        return n not in STOP and n not in FILLER and n not in LIGHT and len(n) >= 3 \
            and not n.endswith("mente") and not re.fullmatch(r"\d+", n)

    def weak_block(self, ws: list[dict], emph: set | None = None) -> bool:
        """Bloque sin contenido que mostrar (muletillas: 'asi que nada, este video basicamente
        pues'; 'creo que no tiene ningun'): el subtitulo basta; un titulo ahi es ruido.
        Titulable = un numero o un nombre propio, o >= 2 palabras de contenido, o 1 si el bloque
        no es mayormente muletilla ('Hay promocion', 'como se sienten'). Una palabra clave del
        LLM ya no basta por si sola."""
        drop = self._filler_ids(ws)
        kept = [w for w in ws if w["i"] not in drop]
        if not kept:
            return True
        if any(re.search(r"\d", w["text"]) or self._entity_word(w) for w in kept):
            return False
        content = [w for w in kept if self.content_word(w)]
        if len(content) >= 2:
            return False
        if not content:
            return True
        filler = len(drop) + sum(1 for w in kept if w["n"] in FILLER)
        return filler / len(ws) >= 0.34

    def trim_edges(self, blk: list[list[dict]]) -> list[list[dict]]:
        """Quita del borde de un titulo las muletillas ('Basicamente el perfume' -> 'el perfume',
        'con ruido de fondo creo' -> 'con ruido de fondo') y lo que queda colgando al final
        ('que no tiene ningun'). El subtitulo conserva todo lo dicho."""
        ws = [w for ln in blk for w in ln]
        if not ws:
            return []
        drop = self._filler_ids(ws)

        def filler(k: int) -> bool:
            w = ws[k]
            if w["i"] in drop:
                return True
            if w["n"] == "este":
                # demostrativo ('este video') salvo que lo siga otra muletilla o una pausa
                nx = ws[k + 1] if k + 1 < len(ws) else None
                return nx is None or nx["n"] in FILLER or nx["i"] in drop or nx["t0"] - w["t1"] > 0.3
            return w["n"] in EDGE_FILLER

        lead = 0
        while lead < len(ws) - 1 and filler(lead):
            lead += 1
        tail = len(ws)
        while tail - 1 > lead and (filler(tail - 1) or (ws[tail - 1]["n"] in DANGLING
                                                        and not re.search(r"[?!]$", ws[tail - 1]["text"]))):
            tail -= 1
        if filler(lead) and tail - lead <= 1:
            return []
        keep = {w["i"] for w in ws[lead:tail]}
        out = [[w for w in ln if w["i"] in keep] for ln in blk]
        return [ln for ln in out if ln]

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
        # el enfasis nunca cae en una muletilla ni en un verbo ligero ('BASICAMENTE', 'HACIENDO')
        emph = {i for i in emph if w0 <= i <= w1 and (self.content_word(self.words[i])
                                                       or self._entity_word(self.words[i])
                                                       or re.search(r"\d", self.words[i]["text"]))}
        kw = {w["i"] for w in self.words[w0:w1 + 1] if w["kw"] and self.content_word(w) and len(w["n"]) >= 5}
        blocks = self._blocks(w0, w1, emph, kw)[:max_blocks]
        if not blocks:
            return 0
        flat = [w for b in blocks for ln in b for w in ln]
        if not any(w["i"] in emph for w in flat):
            emph = emph | {self._hero_fallback(flat)["i"]}
        primary = set(emph)
        accent_set = emph | {w["i"] for w in flat if w["kw"] and self.content_word(w) and len(w["n"]) >= 5}
        main = self.sub_preset(main or self._sentence_preset(humor))
        comp = self.sub_preset(companion or self._companion(main))
        if role != "hook" and any(
                any(min(b, ln[-1]["f1"]) - max(a, ln[0]["f0"]) > 0.3 for a, b, _k in self.bg_fx)
                for blk in blocks for ln in blk):
            comp = main       # la composicion del fondo nuevo ya es la segunda pieza de la frase
        self.sent_main = main
        placed = 0
        home = None
        tried: set = set()             # bloques ya partidos (no se vuelven a unir ni a partir)
        for bi, blk in enumerate(blocks):
            raw = [w for ln in blk for w in ln]
            blk = self.trim_edges(blk)       # sin muletillas en el borde ni palabra colgando
            if not blk:
                continue
            ws = [w for ln in blk for w in ln]
            hero = any(w["i"] in primary for w in ws)
            if all(w["i"] in self.titled_words for w in ws):
                continue                     # ya lo muestra otro titulo/escena (o uno bloqueado)
            # el contenido se mide en la clausula HABLADA completa ('asi que nada, este video
            # basicamente pues' es relleno aunque recortado quede 'este video')
            if self.weak_block(raw, primary) or self.weak_block(ws, primary):
                nb = blocks[bi + 1] if bi + 1 < len(blocks) else None
                if role == "hook" and nb and self.shot_at(nb[0][0]["t0"])["k"] == self.shot_at(ws[0]["t0"])["k"] \
                        and nb[0][0]["f0"] - ws[-1]["f1"] < 0.6 and \
                        tuple(w["i"] for ln in blk + nb for w in ln) not in tried:
                    # hook: la clausula sin contenido ('aqui estamos haciendo') se suma a la
                    # siguiente (el texto esta desde el primer cuadro y remata en la idea)
                    blocks[bi + 1] = blk + nb
                continue                     # muletillas: basta el subtitulo
            preset = main if (hero or len(blocks) == 1) else comp
            fa_, fb_ = ws[0]["f0"], ws[-1]["f1"]
            on_bg = any(min(b, fb_) - max(a, fa_) >= 0.5 * max(0.1, fb_ - fa_) for a, b, _ in self.bg_fx)
            if on_bg and role != "hook":
                # momento protagonista sobre el fondo nuevo: palabra GIGANTE detras de la persona
                # con la frase encima (presencia; no una linea chica apretada sobre la cabeza).
                # Las clausulas siguientes sobre el mismo fondo son la misma composicion
                merged = list(blk)
                j = bi + 1
                while j < len(blocks) and sum(map(len, merged)) + sum(map(len, blocks[j])) <= 12:
                    nws = [w for ln in blocks[j] for w in ln]
                    n0, n1 = nws[0]["f0"], nws[-1]["f1"]
                    if not any(min(b, n1) - max(a, n0) >= 0.5 * max(0.1, n1 - n0) for a, b, _ in self.bg_fx):
                        break
                    merged = merged + [list(ln) for ln in blocks[j]]
                    j += 1
                self._bghero_left = None
                it = self._bg_hero(s, merged, emph | accent_set, role, end_limit)
                left = getattr(self, "_bghero_left", None)
                if it is not None and left:
                    # la clausula que no entro en la composicion: su propio titulo, antes
                    lw, tc = left
                    lw = [w for ln in self.trim_edges([lw]) for w in ln]
                    if lw:
                        lt0 = max(lw[0]["t0"] - 0.08, self.shot_at(lw[0]["t0"])["start"] + 0.02)
                        lt1 = tc - 0.04
                        if self.f(lt1) - self.f(lt0) >= 0.8 and not self.weak_block(lw):
                            # arriba, donde sigue la composicion (el fondo nuevo luce)
                            li_ = self.title_block([lw], main, {w["i"] for w in lw if w["i"] in primary},
                                                   ["top"] + [z_ for z_ in zones_hero if z_ != "top"], lt0, lt1,
                                                   role, humor=humor,
                                                   acc_words={w["i"] for w in lw if w["i"] in accent_set},
                                                   first_bonus=1.9)
                            if li_ is not None:
                                li_["anim_out"] = "none"      # le pasa la franja a la composicion
                if it is not None:
                    placed += 1
                    home = "top"
                    self.last["tp_recent"] = (self.last.get("tp_recent", []) + ["behind_giant"])[-2:]
                    continue
            if on_bg:
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
            if on_bg and role != "hook":
                # sobre el fondo nuevo (arriba de la cabeza) el titulo luce el reemplazo
                zones = ["top"] + [z_ for z_ in zones if z_ != "top"]
                top_bonus = 1.9
            hero_set = {w["i"] for w in ws if w["i"] in primary} or {w["i"] for w in ws if w["i"] in accent_set}
            it = self.title_block(blk, preset, hero_set, zones, t0, t1, role, humor=humor,
                                  acc_words={w["i"] for w in ws if w["i"] in accent_set},
                                  first_bonus=top_bonus)
            if not it:
                key_ = tuple(w["i"] for w in ws)
                if len(blk) >= 2 and key_ not in tried:
                    # no cabe con presencia (primer plano, franja corta): se reorganiza en dos
                    # bloques mas cortos que entran uno tras otro con la voz
                    tried.add(key_)
                    h_ = (len(blk) + 1) // 2
                    blocks[bi + 1:bi + 1] = [blk[:h_], blk[h_:]]
                continue
            self.last["tp_recent"] = (recent + [it["preset"]])[-2:]
            home = it["_geom"][1]
            placed += 1
            line_ts = []
            for ln in blk[1:]:
                line_ts.append(ln[0]["t0"])
            self.text_sfx(it["preset"], t0, it, prio=5 if hero else 3, line_ts=line_ts,
                          reveal=max(0.3, self.f(ws[-1]["t1"]) - self.f(ws[0]["t0"]) + 0.2))
            if hero:
                hw = next((w for w in ws if w["i"] in primary), ws[-1])
                p_acc = _lerp(*self.S["accent_p"], self.I)
                # el acento entra con la palabra protagonista, o antes si esta llega al final del
                # bloque (debe verse >= 0.9 s para alcanzar a dibujarse)
                t_acc = max(t0, min(hw["t0"] - 0.05, self.o(self.f(t1) - 0.95)))
                if accents and self.rand(f"acc{hw['i']}") < p_acc:
                    self.accent_for(it, ws, accent_set, f"a{hw['i']}", t_acc, t1, info.get("icons"))
                if accents and self.S.get("marks", 0) > 0 and self.rand(f"mk{hw['i']}") < self.S["marks"] * (0.5 + 0.5 * self.I):
                    self.hero_mark(it, ws, f"m{hw['i']}", max(t0 + 0.15, hw["t0"]), t1, hero=hw)
                if punches and (info["energy"] != "baja" or self.I > 0.8):
                    self.punch_at(hw["t0"], 0.07 + 0.07 * self.I, 0.35)
        return placed

    def _bg_hero(self, s: dict, blk: list[list[dict]], emph: set, role: str,
                 end_limit: float | None) -> dict | None:
        """Composicion del momento protagonista sobre el fondo reemplazado (dos piezas de la
        misma composicion, editables por separado; ids tx_bghero_s<N> y tx_bghero_s<N>b, que se
        bloquean juntas con su fx_bgswap_s<N>):
          - la frase ARRIBA en 1-2 lineas con presencia (0.075-0.09 W) que se escribe al ritmo de
            la voz desde el inicio del bloque (si no cabe, solo su ultima clausula);
          - la palabra protagonista GIGANTE (>= 0.13 W) debajo, detras de la cabeza o delante sobre
            el cabello (nunca de las cejas para abajo), que ATERRIZA cuando se dice (golpe suave);
            su hueco queda reservado desde el inicio.
        Si el encuadre no deja sitio, la camara de ese tramo abre un poco (el fondo nuevo luce)."""
        ws = [w for ln in blk for w in ln]
        shot = self.shot_at(ws[0]["t0"])
        if not shot["talk"] or shot.get("face_nominal") or not shot.get("face_x") or \
                not (0.3 <= shot["face_x"] <= 0.7):
            return None
        cand = [w for w in ws if self.content_word(w) and len(w["n"]) >= 4]
        if not cand:
            return None
        pref = [w for w in cand if w["i"] in emph or w["kw"]] or cand
        hero = pref[-1]                       # el remate de la clausula ('... tus DIENTES')
        if len(hero["n"]) > 10:
            return None
        before = [w for w in ws if w["i"] < hero["i"]]
        after = [w for w in ws if w["i"] > hero["i"]]
        if len(after) > 2 or sum(len(w["n"]) for w in after) > 8:
            return None
        t0 = max(ws[0]["t0"] - 0.08, shot["start"] + 0.02)
        lim = shot["end"] - 0.04
        if s["id"] + 1 < len(self.sents) and self.sents[s["id"] + 1]["t0"] > ws[-1]["t1"]:
            lim = min(lim, self.sents[s["id"] + 1]["t0"] - 0.04)
        if end_limit is not None:
            lim = min(lim, end_limit)
        t1 = min(max(ws[-1]["t1"] + 0.55, self.o(self.f(hero["t0"]) + 1.1)), lim)
        fa, fb = self.f(t0), self.f(t1)
        if fb - fa < 1.0 or not self.free("top", fa, fb):
            return None
        # la gigante aterriza con la voz (entera, con golpe: word_sync false), pero se ve >= 1.1 s
        # aunque la palabra se diga al final de la toma
        tg = max(t0, min(hero["t0"] - 0.05, self.o(fb - 1.1)))
        light = any(k == "light" and a < fb and fa < b for a, b, k in self.bg_fx)
        lead_font = self.K["serif"] if self.preset_ok("serif_elegant") else self.K["text"]
        lead_preset = "serif_elegant" if self.preset_ok("serif_elegant") else "title_pop"
        disp = self.K["display"]
        giant = " ".join([_display(hero["text"]).strip("¿?¡!.,").upper()] +
                         [_display(w["text"]).strip("¿?¡!.,").upper() for w in after])
        ink = self.pal["dark"] if light else "#FFFFFF"
        acc = self.acc_light if light else self.pal["accent"]
        # frase: completa o solo su ultima clausula ('y como se ven tus' -> 'como se ven tus')
        leads = [before] if before else [[]]
        cut = max((k for k, w in enumerate(before) if k > 0 and w["n"] in CLAUSE_W), default=None)
        if cut is not None:
            tail_ = before[cut:]
            if tail_ and tail_[0]["n"] in ("y", "e", "o", "u", "pero"):
                tail_ = tail_[1:]
            if tail_:
                leads.append(tail_)
        opts = []                 # (lineas, tamano, palabras) de la mas grande/completa a la menor
        for li_, lw in enumerate(leads):
            words_ = [_display(w["text"]) for w in lw]
            if not words_:
                opts.append(([], 0.0, lw))
                continue
            for fs in (0.09, 0.085, 0.08, 0.075):
                lines = self._rewrap(words_, lead_font, fs, 2)
                if len(lines) <= 2 and max(_text_w(lead_font, fs, ln) for ln in lines) <= self.MAXW:
                    opts.append((lines, fs, lw))
        if not opts:
            return None
        gsize = self._fit_lines([{"text": giant, "scale": 1.0, "font": disp}], 0.3, self.MAXW)
        GMIN = 0.13

        def layout(z=None, only=None):
            g_ = self.geom(shot, t0, t1, z)
            ht_ = g_.get("head_top")
            if ht_ is None:
                return None
            core_ = self.face_core(shot, t0, t1, z)
            brow_ = (core_[1] - 0.006) if core_ is not None else ht_
            for lines_, fs_, lw_ in (only or opts):
                lh_ = sum(fs_ * 1.12 for _ in lines_) / self.aspect
                top_ = SAFE["top"] + (lh_ + 0.012 if lines_ else 0.0)
                sz_ = min(gsize, (brow_ - top_) * self.aspect / 1.12)
                if sz_ >= GMIN:
                    return ht_, brow_, lines_, fs_, lw_, top_, sz_
            return None

        lay = layout()
        full = [o for o in opts if o[2] is leads[0]]
        if full and (lay is None or lay[4] is not leads[0]):
            # no cabe la frase COMPLETA: la camara de este tramo abre lo justo (sin bajar de
            # ~0.85 del plano medio de la toma), primero con letra >= 0.08 W y si no con 0.075
            zc = self.zoom_level(shot, t0, t1)
            zfloor = max(1.0, 0.85 * float(shot.get("z0", 1.0)))
            best = None
            for fsmin in (0.08, 0.075):
                cand_ = [o for o in full if o[1] >= fsmin - 1e-6]
                z_ = zc
                while z_ >= zfloor - 1e-6 and cand_:
                    if layout(z_, cand_) is not None:
                        best = z_
                        break
                    z_ -= 0.02
                if best is not None:
                    break
            if best is not None:
                if best < zc - 1e-3:
                    self._cap_cam(t0, t1, best)
                lay = layout() or lay
        if lay is None:
            return None
        ht, brow, lead_txt, lead_fs, lead_ws, top_lim, size = lay
        if ht < 0.12:
            return None
        # frase recortada a su ultima clausula: la composicion empieza con ESA clausula y lo
        # anterior ('como se sienten') se titula aparte antes (title_sequence), no se pierde
        first_i = (lead_ws[0]["i"] if lead_ws else hero["i"])
        left = [w for w in ws if w["i"] < first_i]
        if left:
            t0 = max(t0, self.words[first_i]["t0"] - 0.08)
            fa = self.f(t0)
            if fb - fa < 1.0:
                return None
            tg = max(t0, tg)
            ws = [w for w in ws if w["i"] >= first_i]
        gh = size * 1.12 / self.aspect
        gy = top_lim + gh / 2
        gfields = {"start": tg, "end": t1, "preset": "behind_giant",
                   "lines": [{"text": giant, "scale": 1.0, "font": disp, "color": acc}],
                   "x": 0.5, "y": _r(gy, 4), "size": _r(size, 4), "font": disp, "color": acc,
                   "accent": acc, "align": "center", "behind": True, "case": "none",
                   "anim_in": "scale_down", "anim_out": "fade", "shadow": 0.12 if light else 0.35,
                   "accent_words": [], "word_sync": False}
        gfields = self.legible_behind(gfields, shot, tg, t1, max_occ=0.4, top_min=top_lim - 0.004)
        if not gfields.get("behind"):
            gfields["shadow"] = 0.2 if light else 0.6
        gi = self.add("text", "bghero", gfields, zone="top")
        if not gi:
            return None
        # el hueco de la gigante queda reservado desde el inicio del bloque (la frase ya esta)
        self.occ["top"].append((fa, fb, gi["id"]))
        rg = measure_text(gi, self.style)
        if rg is not None:
            self.ink.append((fa, fb, *rg, gi["id"]))
        # la frase encima (se escribe con la voz); si no cabe con presencia, solo la gigante
        if lead_txt and rg is not None:
            fs = lead_fs
            for _ in range(6):
                lh = sum(fs * 1.12 for _ in lead_txt) / self.aspect
                if rg[1] - 0.008 - lh >= SAFE["top"] or fs <= 0.075:
                    break
                fs *= 0.96
            ly = rg[1] - 0.008 - lh / 2
            if ly - lh / 2 >= SAFE["top"] - 0.004:
                lt0 = max(t0, lead_ws[0]["t0"] - 0.08) if lead_ws else t0
                lf = {"start": lt0, "end": t1, "preset": lead_preset, "text": "\n".join(lead_txt),
                      "x": 0.5, "y": _r(ly, 4), "size": _r(fs, 4), "font": lead_font, "color": ink,
                      "accent": acc, "align": "center", "anim_in": "word_stagger", "anim_out": "fade",
                      "shadow": 0.12 if light else 0.5, "accent_words": []}
                li = self.add("text", "bghero", lf, zone="edge")
                if li:
                    self.occ["top"].append((fa, fb, li["id"]))
                    r = measure_text(li, self.style)
                    if r is not None:
                        self.ink.append((fa, fb, *r, li["id"]))
        for w in ws:
            self.titled_words.add(w["i"])
        self.sfx(tg + 0.04, "impact_soft", prio=6, src_id=gi["id"])
        self.protect.append((self.f(tg) + 0.25, self.f(tg) + 0.9))
        self._bghero_left = (left, t0) if left else None
        # destellos junto a la palabra gigante (par espejo o uno centrado)
        if rg is not None:
            gi["_geom"] = ((rg[0], rg[1], rg[2], rg[3]), "top", gfields["lines"], "light" if light else "dark")
            self.accent_for(gi, ws, emph, f"bh{hero['i']}", max(tg, hero["t0"] - 0.05), t1, force="sparkle")
        return gi

    def _cap_cam(self, t0: float, t1: float, zmax: float) -> bool:
        """Baja el zoom de camara de los tramos que tocan [t0, t1] a <= zmax (abre el encuadre
        para una composicion de texto arriba). Todos los tramos tocados se escalan con el MISMO
        factor: el jump-cut sigue alternando cerrado / abierto. True si cambio algo."""
        zmax = max(1.0, float(zmax))
        hit = []
        for it in self.fx["effects"]:
            if it.get("kind") != "zoom" or not str(it.get("id", "")).startswith("fx_cam"):
                continue
            a, b = float(it["start"]), float(it["end"])
            if min(b, t1) - max(a, t0) < min(0.3, 0.5 * (t1 - t0)):
                continue                  # un roce en el borde no abre el tramo vecino
            hit.append(it)
        tops = [max(float(it["from"]), float(it["to"])) for it in hit]
        if not hit or max(tops) <= zmax + 1e-3:
            return False
        k = zmax / max(tops)
        for it in hit:
            a, b = float(it["start"]), float(it["end"])
            it["from"] = _r(max(1.0, float(it["from"]) * k), 3)
            it["to"] = _r(max(1.0, float(it["to"]) * k), 3)
            # el tramo de camara se reconoce por su FIN (el inicio pudo entrar un poco en el corte)
            self.cam = [(ca, cb, (max(float(it["from"]), float(it["to"])) if abs(cb - b) < 0.003 and ca < b
                                  and a - 0.05 <= ca else cz)) for ca, cb, cz in self.cam]
        return True

    def riser(self) -> None:
        """Riser que culmina en el cambio mas grande: la entrada a un b-roll mudo (revelacion) o,
        si no hay, la primera escena a pantalla completa."""
        target = None
        for ch in self.shot_changes():
            if not ch["next"]["talk"] and ch["f"] > 2.0 and not self.hard_join(ch["next"]):
                target = ch["at"]
                break
        if target is None and self.fx["cutaways"]:
            c0 = min(self.fx["cutaways"], key=lambda c: c["start"])
            if self.f(c0["start"]) > 2.0:
                target = c0["start"]
        if target is not None and self.I >= 0.4:
            # anticipacion: el riser culmina en `target` (no empieza antes del inicio del video)
            self.sfx(target, "riser_short", prio=7, dur=min(1.4, max(0.4, self.f(target) - 0.2)))

    # ---------------------------------------------------------- 3. hook
    def hook(self) -> None:
        if not self.sents:
            return
        s = self.sents[0]
        # el preambulo de relleno ('Ok gente,') o ya recortado no se titula: el hook arranca en
        # la primera palabra que queda en el video y no es muletilla
        w0 = s["w0"]
        drop = self._filler_ids(self.words[s["w0"]:s["w1"] + 1])
        while w0 < s["w1"] and (not self.kept(self.words[w0]["t0"] + 0.01) or self.words[w0]["i"] in drop
                                or self.words[w0]["n"] in FILLER):
            w0 += 1
        w1 = s["w1"]
        # hook visual en los primeros segundos: como mucho ~4.5 s de titulos
        while w1 > w0 and self.words[w1]["f1"] - self.words[w0]["f0"] > 4.5:
            w1 -= 1
        first = self.words[w0]
        self._hook_bg(s, w1, w0)
        self.punch_at(first["t0"] + 0.02, 0.09 + 0.05 * self.I, 0.45, role="hook_punch", min_gap=0.8)
        main = self.sub_preset(self.pick("hook", self.S["hook_presets"]))
        n = self.title_sequence(s, w0, w1, "hook", ["top", "center"], ["center", "center_low", "top"],
                                main=main)
        if n == 0:
            ws = self.words[w0:min(w1, w0 + 3) + 1]
            if not self.weak_block(ws):
                self.title_block([ws], "title_pop", set(s["info"]["emph"]), ["center", "top"],
                                 first["t0"] - 0.05, ws[-1]["t1"] + 0.5, "hook")
        # whoosh de entrada
        self.sfx(max(0.0, first["t0"] - 0.05), "whoosh_fast", prio=9)
        if self.S["scenes"]["outline"] and self.auto_matte:      # contorno = borde del recorte
            sh = self.shot_at(first["t0"])
            if sh["talk"] and not sh.get("face_nominal") and sh.get("matte_ok", True):
                it = self.add("effects", "hook_outline", {
                    "start": first["t0"], "end": min(first["t0"] + 1.4, sh["end"] - 0.05),
                    "kind": "subject_outline", "color": "#FFFFFF", "width": 7.0, "glow": 0.6})
                if it:
                    self.sfx(first["t0"] + 0.05, "shutter", prio=4, src_id=it["id"])
        s["info"]["_done"] = True

    def _hook_bg(self, s: dict, w1: int, w0: int | None = None) -> dict | None:
        """Impacto del hook (como el editor de referencia): el sujeto recortado sobre un fondo
        del estilo desde el primer cuadro hasta el final del primer bloque (~1.4-2.6 s). Solo
        con recorte limpio, persona centrada y plano medio/abierto (en un primer plano casi no
        hay fondo que cambiar)."""
        kind = self.S.get("hook_bg")
        if not kind or self.I < 0.5 or self.windows is not None:
            return None
        w0 = s["w0"] if w0 is None else w0
        first = self.words[w0]
        shot = self.shot_at(first["t0"])
        face = shot.get("face")
        if not (shot["talk"] and not shot.get("face_nominal") and face and shot.get("matte_ok")
                and 0.28 <= face[0] <= 0.72 and 0.06 <= face[3] <= 0.2):
            return None
        blocks = self._blocks(w0, w1, set(s["info"]["emph"]))
        if not blocks:
            return None
        fb = blocks[0][-1][-1]["f1"] + 0.25
        if len(blocks) > 1 and fb - self.f(first["t0"]) < 1.4:
            fb = blocks[1][-1][-1]["f1"] + 0.25
        fa = max(0.0, self.f(first["t0"]) - 0.06)
        fb = min(fb, fa + 2.8, shot["f1"] - 0.04)
        # termina con el encuadre cerrado del hook: abierto, la persona queda diminuta sobre el
        # fondo sintetico
        for a_, b_, _z in self.cam:
            if a_ - 1e-6 <= first["t0"] < b_:
                fb = min(fb, self.f(b_) - 0.02)
                break
        if fb - fa < 1.2:
            return None
        a, b = self.o(fa) if fa > 0 else max(0.0, shot["start"]), self.o(fb)
        # un fondo que ya se queda (bloqueado junto con el titulo del hook por _lock_groups) ES el
        # fondo del hook: no se emite una copia identica (duplicaba recorte y compositor en el export)
        for fx0 in self.fixed.get("effects") or []:
            if fx0.get("kind") == "bg_replace" and fx0.get("enabled", True) is not False:
                oa, ob = self.f(float(fx0["start"])), self.f(float(fx0["end"]))
                if min(fb, ob) - max(fa, oa) >= 0.5 * (fb - fa):
                    self.sfx(max(0.0, a) + 0.02, "swoosh_up", prio=6, src_id=str(fx0.get("id", "")))
                    self.music_swells.append({"start": _r(a), "end": _r(b), "db": 1.5})
                    return None
        if not self.auto_matte:
            return None                         # fondo reemplazado: solo a mano (AUTO_MATTE_FX)
        cols = [self.pal["dark"], self.pal["accent2"]]
        it = self.add("effects", "hookbg", {"start": a, "end": b, "kind": "bg_replace", "background": kind,
                                            "bg_colors": cols, "fade_in": 0.0, "fade_out": 0.12},
                      event=True)
        if it:
            self.bg_fx.append((fa, fb, "dark"))
            self.sfx(max(0.0, a) + 0.02, "swoosh_up", prio=6, src_id=it["id"])
            self.music_swells.append({"start": _r(a), "end": _r(b), "db": 1.5})
        return it

    # ---------------------------------------------------------- 4. momento dramatico
    # negaciones y avisos fuertes: una frase 'dramatica' necesita alguna senal en lo que se dice
    _DRAMA_W = {"nunca", "jamas", "nadie", "prohibido", "error", "errores", "peligro", "peligroso", "grave",
                "cuidado", "ojo", "urgente", "atencion", "importante", "alerta", "necesitamos", "tenemos",
                "escucha", "escuchame", "alto", "secreto", "increible"}

    def drama_signal(self, s: dict) -> bool:
        """El tratamiento dramatico (B/N, destello, golpe grave) solo con una senal lexica: una
        frase hecha ('necesitamos hablar'), una pregunta o una palabra de aviso/negacion fuerte.
        Una frase neutra ('nombres en ingles, no puedo') no se vuelve drama porque el LLM lo diga."""
        ws = self.words[s["w0"]:s["w1"] + 1]
        txt = " ".join(w["n"] for w in ws)
        if any(p in txt for p in _DRAMA_PH) or "?" in s.get("text", ""):
            return True
        return any(w["n"] in self._DRAMA_W for w in ws)

    def drama(self) -> None:
        cand = [s for s in self.sents if s["info"]["fx"] == "dramatico" and not s["info"].get("_done")
                and self.drama_signal(s)]
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
        if D["giant"] and shot["talk"] and not shot.get("face_nominal") and shot.get("face_x") is not None \
                and 0.25 <= shot["face_x"] <= 0.75 and g.get("head_top") is not None and g["head_top"] < 0.17:
            # plano medio cerrado: para la palabra gigante sobre la cabeza la camara abre lo justo
            ax, ay, _fw, fh = shot["face"][:4]
            if self._cap_cam(t0, end, (ay - 0.175) / max(1e-3, fh * 1.08 * 0.98)):
                g = self._geom(shot, self.zoom_level(shot, t0, end))
        giant_ok = (D["giant"] and shot["talk"] and not shot.get("face_nominal")
                    and shot["face_x"] is not None
                    and 0.25 <= shot["face_x"] <= 0.75 and (g["head_top"] or 0) >= 0.17)
        placed = None
        content = [w for w in ws if w["n"] not in STOP and len(w["n"]) >= 3] or ws
        short = [w for w in content if len(w["n"]) <= 8]
        hero = (short[-1:] or [min(content, key=lambda w: len(w["n"]))])
        h = hero[0]
        # la palabra protagonista queda QUIETA y nitida >= 0.8 s (el texto puede cruzar un poco
        # hacia la frase siguiente; la transicion que sigue es corta y seca)
        settle = 0.3
        end_txt = min(max(end, self.o(self.f(h["t0"]) + settle + 0.85)), shot["end"] - 0.02)
        static = self.f(end_txt) - (self.f(h["t0"]) + settle)
        self.protect.append((self.f(h["t0"]) + settle, self.f(h["t0"]) + settle + 0.8))
        if giant_ok and self.free("top", self.f(t0), self.f(end_txt)):
            end = end_txt
            before = [w for w in ws if w["i"] < h["i"]]
            after = [w for w in ws if w["i"] > h["i"]]
            giant = [h]
            if after and all(w["n"] in STOP or len(w["n"]) <= 4 for w in after) and len(after) <= 2:
                giant, after = giant + after, []          # 'ESCUCHA ESTO': todo gigante, arriba
            font = self.K["display"]
            lines = []
            if before:
                lines.append({"text": " ".join(_display(w["text"]) for w in before).upper(),
                              "scale": 0.3, "font": font})
            lines.append({"text": " ".join(_display(w["text"]) for w in giant).upper(), "scale": 1.0,
                          "font": font})
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
            fields = self.legible_behind({
                "start": t0, "end": end, "preset": "behind_giant", "lines": lines,
                "x": 0.5, "y": _r(y, 4), "size": _r(size, 4), "font": font,
                "color": "#FFFFFF", "accent": self.pal["accent"], "align": "center",
                "behind": True,
                "anim_in": self.pick("drama_anim", ["scale_down", "letter_stagger"]) if static >= 0.8 else "pop",
                "anim_out": "fade", "shadow": 0.2, "accent_words": []}, shot, t0, end, max_occ=0.42)
            if not fields.get("behind"):
                # delante (pila sobre el cabello): tinta contra el fondo real de su franja
                fields["shadow"] = 0.6
                r0 = measure_text(fields, self.style)
                if r0 is not None:
                    self._apply_ink(fields, self._bg_at(shot, r0[1], r0[3], t0, end))
                    fields.pop("_mode", None)
            placed = self.add("text", "drama", fields, zone="top")
            if placed:
                r = measure_text(placed, self.style)
                if r is not None:
                    self.ink.append((self.f(t0), self.f(end), *r, placed["id"]))
        if not placed:
            self.title_block([ws], "serif_elegant", emph, ["center", "top"], t0, end_txt, "drama")
        for w in ws:
            self.titled_words.add(w["i"])
        # audio: la musica se frena (tape stop de la propia pista, sin SFX record_stop encima),
        # golpe grave y regresa con la siguiente toma
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
                        n.endswith(_NOUN_SUFFIX) and not n.endswith(_VERB_END[:3]) and n not in _PRICE_W \
                        and n not in _VERBISH:
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
        n_cut = self.n_cut_left
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
            mode = self.S.get("price", "scene")
            if mode == "overlay" and val > 0:
                done = self._price_overlay(i0, i1, s, shot, end, val, cur, service)
            if not done and mode != "card" and n_cut > 0 and val > 0 and self.f(end) - self.f(t0) >= 1.1 \
                    and self.free("full", self.f(t0), self.f(end)):
                bg = self.pick("bg", [b for b in self.S["backgrounds"] if b not in ("blur_self",) and b not in LIGHT_BG]
                               or ["gradient"])
                amount = f"{int(val):,}" if val == int(val) else f"{val:,.2f}"
                font = self.K["display"]
                size = min(0.24, 0.72 / max(1.0, _text_w(font, 1.0, "$" + amount)))
                ny = 0.47
                # destellos en PAR ESPEJO exacto (mismo glifo, tamano, y, color; x y 1-x)
                gr = self._scene_pair({"kind": "doodle", "name": "sparkle", "x": 0.8,
                                       "y": _r(ny - size * 0.62 / self.aspect, 4), "size": 0.1,
                                       "color": self.pal["accent"], "draw": True, "anim_in": "draw"})
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
                             "anchor": "center", "anim_in": "pop", "shadow": 0.4,
                             "count_dur": self.COUNT_DUR},
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
                self.card("price", i0, max(t0, self.words[i0]["t0"] - 0.35),
                          min(max(end, self.words[i0]["t0"] + 2.2), shot["end"] - 0.03),
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
                        # la referencia no se titula (va en la tarjeta): un icono que la acompana
                        la, lb = a["landmark"]
                        cw_ = [w for w in self.words[la:lb + 1] if w["n"] not in STOP and len(w["n"]) >= 4]
                        if cw_:
                            ic = "walk" if assets.icon_exists("walk") else "map-pin"
                            self._float_accent(cw_[0], self.shot_at(cw_[0]["t0"]), icon_only=True, icon=ic)
                elif "landmark" in a:
                    # sin direccion: la referencia es el dato (en la tarjeta va como titulo); con
                    # direccion, la referencia solo va en la tarjeta (sin repetirla tres veces)
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
                    # la tarjeta ya mostro la referencia: sin repetirla como titulo, un icono
                    # (caminar) que acompana la frase
                    la, lb = a["landmark"]
                    ws_ = [w for w in self.words[la:lb + 1] if w["f0"] >= self.f(card["end"]) - 0.1
                           and w["n"] not in STOP and len(w["n"]) >= 4]
                    if ws_:
                        ic = "walk" if assets.icon_exists("walk") else "map-pin"
                        self._float_accent(ws_[0], self.shot_at(ws_[0]["t0"]), icon_only=True, icon=ic)
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
            # sin repetir "Agenda": el link (con su etiqueta) llega enseguida
            bf = self._book_fields()
            via = ""
            if bf:
                via = "En mi página" if self.book["kind"] == "web" else \
                    "En " + PR.PLATFORMS.get(bf["platform"], {}).get("label", "el link")
            sub = self.ent.get("schedule") or via or ("En el link de abajo" if link_i is not None else "")
            self.card("schedule", sched_i, t0, end, title=title, subtitle=sub, icon="calendar-event",
                      theme="light", sfx_name="ding",
                      extra=card_media(self.prof, bf["platform"]) if bf and via and sub == via else None)
            s["info"]["_done"] = True
        if link_i is not None:
            s = self._sent_of(link_i)
            shot = self.shot_at(self.words[link_i]["t0"])
            t0 = self.words[link_i]["t0"] - 0.06
            end = min(shot["end"] - 0.03, s["t1"] + 0.7)
            if self.reserved:
                end = min(end, self.o(self.reserved[0][0] - 0.3))
            end = max(end, min(t0 + 1.6, shot["end"] - 0.03))
            if s["id"] + 1 < len(self.sents):
                # la siguiente frase manda (su titulo entra en su primera palabra)
                end = min(end, max(t0 + 1.0, self.sents[s["id"] + 1]["t0"] - 0.12))
            bf = self._book_fields()
            end0 = end
            # la pastilla se lee >= 1.5 s en tiempo FINAL (un recorte puede comerse el tramo): si no
            # alcanza se alarga dentro de la toma; si aun asi no, no se pone (la tarjeta final la
            # lleva) en vez de un parpadeo que parece falla
            if self.f(end) - self.f(t0) < self.LINK_MIN:
                lim = self.f(shot["end"] - 0.03)
                if self.reserved:
                    lim = min(lim, self.reserved[0][0] - 0.3)
                end = max(end, self.o(min(lim, self.f(t0) + self.LINK_MIN + 0.1)))
            lk = dict(label=bf.get("label") or self.ent.get("link_label") or "Link abajo",
                      extra={k: v for k, v in bf.items() if k != "label"})
            it = self.cta("link_below", t0, end, **lk)
            if it is None and end > end0 + 1e-6:
                # algo fijo empieza justo despues (un titulo que el usuario bloqueo): la pastilla
                # corta entra igual y extend_link la sostiene hasta el final en la franja baja; si no
                # se puede sostener se quita abajo (< LINK_MIN)
                it = self.cta("link_below", t0, end0, **lk)
            if it is not None:
                self.link_item = it
                if not self.extend_link(it) and \
                        self.f(float(it["end"])) - self.f(float(it["start"])) < self.LINK_MIN - 1e-3:
                    self._unplace(it)
                    self.link_item = None
            s["info"]["_done"] = True

    def _price_overlay(self, i0: int, i1: int, s: dict, shot: dict, end: float, val: float, cur: str,
                       service: str) -> bool:
        """Precio SIN tapar a la persona: contador grande en el hueco libre (arriba de la cabeza si
        cabe; si no, al pecho, delante), etiqueta del servicio, destellos y fondo tratado detras
        de ella (reemplazo de fondo oscuro si el recorte es limpio; si no, desenfoque)."""
        if not shot["talk"] or shot.get("face_nominal") or not shot.get("face"):
            return False
        w0 = self.words[i0]
        # el contador arranca ~0.3 s antes del numero y aterriza (0.45 s de conteo) mientras se
        # dice: el valor final queda quieto y legible >= 0.8 s antes de la transicion
        lead = 0.3
        # toma corta: el conteo arranca antes (hasta 0.8 s antes del numero) para que el valor
        # final quede quieto ~1 s antes del corte
        room = self.f(shot["end"] - 0.03) - (self.f(w0["t0"]) - lead + 0.12 + self.COUNT_DUR)
        if room < 1.0:
            lead = min(0.8, lead + (1.0 - room))
        t0 = self.snap_start(max(shot["start"] + 0.03, w0["t0"] - lead))
        land = self.o(self.f(t0) + 0.12 + self.COUNT_DUR)
        end = min(max(end, self.words[i1]["t1"] + 0.6, self.o(self.f(land) + 1.15)), shot["end"] - 0.03)
        # si aun asi la toma se acaba antes de que el precio se lea quieto ~1 s, el numero cruza el
        # corte hasta 0.6 s sobre la toma siguiente: se queda un instante, como en un editor
        cross = None
        nxt_shot = self.shot_at(shot["end"] + 0.02) if shot["end"] < self.tl.duration - 0.1 else None
        if self.f(end) - self.f(land) < 0.95 and nxt_shot is not None and nxt_shot["k"] != shot["k"]:
            cross = self.o(min(self.f(land) + 1.05, self.f(shot["end"]) + 0.6))
        fa, fb = self.f(t0), self.f(cross or end)
        if fb - fa < 1.0 or not self.free("full", fa, fb):
            return False
        g = self.geom(shot, t0, end)
        font = self.K["display"]
        amount = f"{int(val):,}" if val == int(val) else f"{val:,.2f}"
        base = {"preset": "counter", "from": 0, "to": int(val) if val == int(val) else val, "prefix": "$",
                "suffix": "", "decimals": 0 if val == int(val) else 2, "text": cur, "x": 0.5,
                "font": font, "color": "#FFFFFF", "accent": self.pal["accent"], "align": "center",
                "anim_in": "pop", "anim_out": "pop_out", "shadow": 0.85, "anchor": "center",
                "count_dur": self.COUNT_DUR}
        size0 = min(0.24, 0.7 / max(1.0, _text_w(font, 1.0, "$" + amount)))
        sticker_h = 0.05 if service else 0.0
        best = None
        for z in ("top", "center_low"):
            if not self.free(z, fa, fb) or (z == "under" and not g.get("under")):
                continue
            lo, hi = (g["center"][0], g["low"][1]) if z == "center_low" else g[z]
            lo += sticker_h
            size = size0
            for _ in range(8):
                r = measure_text(dict(base, y=0.5, size=size), self.style)
                h = (r[3] - r[1]) if r else size * 0.8 / self.aspect
                if h <= hi - lo or size < 0.12:
                    break
                size *= 0.9
            if h > hi - lo or size < 0.12:
                continue
            y = (lo + hi) / 2 if z == "top" else lo + h / 2 + 0.01
            if best is None or size > best[0] * 1.15:
                best = (size, z, y, h)
        if best is None:
            return False
        size, zone, y, h = best
        if cross is not None:
            r_ = measure_text(dict(base, y=y, size=size), self.style)
            fr2 = self.face_rect(nxt_shot, shot["end"], cross)
            if r_ is not None and fr2 is not None and r_[0] < fr2[2] and fr2[0] < r_[2] and r_[1] < fr2[3] and fr2[1] < r_[3]:
                cross = None                    # en la toma siguiente taparia la cara
        t_txt_end = cross or end
        spec = dict(base, start=t0, end=t_txt_end, y=_r(y, 4), size=_r(size, 4))
        dark_bg = self.auto_matte and shot.get("matte_ok") and bool(self.S["scenes"]["bg_replace"])
        if dark_bg and zone == "top":
            spec["color"] = "#FFFFFF"          # sobre el fondo oscuro nuevo, arriba de la cabeza
        else:
            self._apply_ink(spec, self._bg_at(shot, y - h / 2, y + h / 2, t0, end))
            spec.pop("_mode", None)
        spec["shadow"] = max(float(spec.get("shadow", 0.5)), 0.85 if spec["color"] == "#FFFFFF" else 0.12)
        it = self.add("text", "price", spec, zone=zone)
        if not it:
            return False
        r = measure_text(it, self.style) or (0.2, y - h / 2, 0.8, y + h / 2)
        self.ink.append((fa, fb, *r, it["id"]))
        for w in self.words[s["w0"]:s["w1"] + 1]:
            self.titled_words.add(w["i"])
        # fondo detras de ella: escena oscura si el recorte es limpio, si no desenfoque
        if dark_bg:
            bgk = self.pick("bg", [b for b in self.S["backgrounds"] if b not in LIGHT_BG] or ["dark_stars"])
            fx_ = self.add("effects", "price_bg", {"start": t0, "end": end, "kind": "bg_replace",
                                                  "background": bgk,
                                                  "bg_colors": [self.pal["dark"], self.pal["accent2"]]},
                           event=False)
            if fx_:
                self.bg_fx.append((fa, fb, "dark"))
        else:
            self.add("effects", "price_blur", {"start": t0, "end": end, "kind": "bg_blur", "radius": 20.0},
                     event=False)
        # etiqueta del servicio encima del numero + destellos a los lados
        tag_top = None
        if service:
            sy = max(SAFE["top"] + 0.03, r[1] - 0.035)
            bgc = self.pal["accent"] if _luma(self.pal["accent"]) < 0.9 else "#FFFFFF"
            st = self.add("graphics", "price_tag", {
                "start": t0 + 0.1, "end": end, "kind": "sticker_text", "text": service, "x": 0.5,
                "y": _r(sy, 4), "size": 0.052, "bg": bgc, "fg": self.pal["dark"] if _luma(bgc) > 0.55 else "#FFFFFF",
                "anim_in": "pop", "anim_out": "pop_out", "rotation": -3.0, "color": "#FFFFFF"}, zone="accent",
                event=False)
            if st:
                self.acc_pos[st["id"]] = (0.5, sy)
                self.sfx(t0 + 0.14, "pop", prio=5, src_id=st["id"], vary=True)
                # borde superior de la pastilla (alto 1.45x la fuente + borde, inclinada ~4 grados)
                tag_top = sy - PRICE_TAG_HALF
        self.protect.append((self.f(land), self.f(land) + 0.8))
        # circulo a mano alrededor del precio cuando aterriza (como un editor que lo remarca)
        t_c = self.o(self.f(land) + 0.05)
        if self.f(end) - self.f(t_c) >= 0.8 and self.S.get("marks", 0) > 0:
            cw_, ch_ = (r[2] - r[0]) * 1.16 + 0.04, (r[3] - r[1]) * 1.55 + 0.02
            cx_, cy_ = self.XC, (r[1] + r[3]) / 2        # el contador va centrado (anchor center)
            if tag_top is not None and cy_ - ch_ / 2 > tag_top - 0.012:
                # el circulo abraza TAMBIEN la etiqueta: al alto del numero su trazo cruzaba la
                # pastilla del servicio y parecia tacharla ('LIMPIEZA' tachada)
                top_, bot_ = tag_top - 0.012, cy_ + ch_ / 2
                cy_, ch_ = (top_ + bot_) / 2, bot_ - top_
            rect = (cx_ - cw_ / 2, cy_ - ch_ / 2, cx_ + cw_ / 2, cy_ + ch_ / 2)
            fr = self.face_rect(shot, t_c, end)
            # nunca sobre ojos/nariz/boca (puede rozar barbilla o cabello, como los titulos)
            core = None if fr is None else (fr[0] + 0.12 * (fr[2] - fr[0]), fr[1] + 0.2 * (fr[3] - fr[1]),
                                            fr[2] - 0.12 * (fr[2] - fr[0]), fr[3] - 0.15 * (fr[3] - fr[1]))
            inside = rect[0] >= SYM_L - 0.02 and rect[2] <= SYM_R + 0.02 and rect[1] >= SAFE["top"] - 0.01
            if inside and (core is None or not (rect[0] < core[2] and core[0] < rect[2] and rect[1] < core[3] and core[1] < rect[3])):
                mk = self.add("graphics", "price_mark", {
                    "start": t_c, "end": end, "kind": "doodle", "name": "circle_scribble",
                    "x": _r(cx_, 3), "y": _r(cy_, 4), "size": _r(min(0.86, cw_), 3),
                    "aspect": _r(_clamp(ch_ * self.aspect / cw_, 0.3, 1.0), 3),
                    "color": self.pal["accent"] if spec["color"] == "#FFFFFF" else self.acc_light,
                    "draw": True, "wiggle": 0.3, "anim_in": "draw", "anim_out": "fade", "rotation": -2.0},
                    zone="edge", event=False)          # abraza al contador y su etiqueta: sin regla de separacion
                if mk:
                    self.occ["accent"].append((self.f(t_c), self.f(end), mk["id"]))
                    self.acc_pos[mk["id"]] = (cx_, cy_)
                    self.ink.append((self.f(t_c), fb, *rect, mk["id"]))
                    self._doodle_sfx("circle_scribble", t_c + 0.02, cx_, mk["id"])
        ta = max(t0 + 0.2, min(w0["t0"], self.o(self.f(end) - 1.05)))
        # destellos en PAR ESPEJO a los lados del numero (mismo glifo, tamano, y y tiempos): entran
        # los dos o ninguno
        if self.f(end) - self.f(ta) >= 0.9:
            for sz in (0.1, 0.085):
                px = max(min(r[2] + sz / 2 + 0.012, SYM_R - sz / 2), self.XC + ACCENT_SEP / 2 + 1e-3)
                done_ = False
                for dy in (-0.2, 0.35):
                    py = _clamp(y + dy * h, SAFE["top"] + 0.04, SAFE["bottom"] - 0.05)
                    if not (self.accent_ok(px, py, sz, fa, fb, shot, t0, end)
                            and self.accent_ok(2 * self.XC - px, py, sz, fa, fb, shot, t0, end)):
                        continue
                    d = self.add_pair("price_spark", {
                        "start": ta, "end": end, "kind": "doodle", "name": "sparkle", "y": _r(py, 4),
                        "size": sz, "color": self.pal["accent"], "draw": True, "wiggle": 0.3,
                        "anim_in": "draw", "anim_out": "fade"}, px, (self.rand("ps0") - 0.5) * 20)
                    if d:
                        self._doodle_sfx("sparkle", ta + 0.03, self.XC, d["id"], pair=True)
                        done_ = True
                        break
                if done_:
                    break
        self.sfx(w0["t0"] + 0.12, "cash", prio=9, src_id=it["id"])
        self.sfx(t0 + 0.03, "whoosh_fast", prio=6, src_id=it["id"] + "in")
        self.punch_at(w0["t0"], 0.1 + 0.05 * self.I, 0.5, role="price_punch", min_gap=0.8)
        self.music_swells.append({"start": _r(t0), "end": _r(end), "db": 2.0})
        return True

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
             cut_end: float | None = None, extra: dict | None = None) -> dict | None:
        """Tarjeta en la primera franja libre. `extra`: campos del perfil (platform + logo de la
        plataforma que nombra: el logo va en el hueco del icono, mismo alto)."""
        shot = self.shot_at(self.words[wi]["t0"])
        t1 = max(min(t1, shot["end"] - 0.03), min(t0 + 0.9, shot["end"] - 0.03))   # no cruza el corte
        g = self.geom(shot, t0, t1)
        fa, fb = self.f(t0), self.f(t1)
        base = {"kind": "card", "variant": variant, "title": title, "subtitle": subtitle,
                "value": value, "icon": icon, "theme": theme, "color": "#FFFFFF",
                "anim_in": "rise", "anim_out": "fade", "rotation": 0.0, "items": []}
        base.update(extra or {})
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
                        if self.I >= 0.5:
                            self.arrow_to((self.XC - size / 2, y - h / 2, self.XC + size / 2, y + h / 2),
                                          t0, t1, shot, it["id"])
                        return it
        # no cabe sobre la toma: escena a pantalla completa con la tarjeta grande (en `card`)
        if getattr(self, "n_cut_left", 0) > 0 and self.S["cutaways"] > 0 and fb - fa >= 1.2 \
                and self.free("full", fa, fb):
            end = self.o(min(fb, fa + 2.6))
            if cut_end is not None and self.f(cut_end) - fa >= 1.3:
                end = min(end, cut_end)
            label = {"location": "Ubicación", "schedule": "Horarios", "price": "Precio",
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
            compact = dict(base, map=False) if variant == "location" else dict(base, variant="generic")
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
            zones: tuple = ("low", "center", "top"), extra: dict | None = None) -> dict | None:
        """CTA sobre la toma en la primera franja libre. `extra`: campos del perfil (link_below
        con `display` = pastilla de dos lineas; follow con title/platform/avatar)."""
        shot = self.shot_at(t0)
        g = self.geom(shot, t0, t1)
        fa, fb = self.f(t0), self.f(t1)
        extra = dict(extra or {})
        two = bool(extra.get("display")) and variant == "link_below"
        size = 0.66 if variant == "link_below" else 0.68
        for z in zones:
            if not self.free(z, fa, fb, cta=True):
                continue
            lo, hi = g[z]
            if hi - lo < 0.06:
                continue
            yy0 = 0.6 if z == "low" else ((lo + hi) / 2 if z == "top" else hi - 0.04)
            yy, arrow = yy0, 1.0
            if variant == "link_below":
                # la flecha rebota hacia abajo: el centro de la pastilla queda arriba de subtitulos
                # (la pastilla de dos lineas prueba una flecha mas corta o ninguna)
                fit = None
                for ar in ((1.0, 0.6, 0.0) if two else (1.0,)):
                    y_ = min(yy0, self._link_y_max(size, ar, two))
                    if y_ - self._link_h(size, two) / 2 >= max(lo, SAFE["top"]) - 0.01:
                        fit = (y_, ar)
                        break
                if fit is None:
                    continue
                yy, arrow = fit
            fields = {"start": t0, "end": t1, "kind": "cta", "variant": variant, "label": label,
                      "handle": handle, "x": _r(self.XC, 3), "y": _r(yy, 4), "size": size,
                      "color": self.pal["accent"], "anim_in": "pop", "anim_out": "fade", "rotation": 0.0}
            if arrow < 1.0:
                fields["arrow_scale"] = arrow
            fields.update(extra)
            if variant == "follow":
                fields["button_color"] = self.button_color()
            it = self.add("graphics", "cta", fields, zone=z)
            if it:
                hh = self._link_h(size, two) if variant == "link_below" else size * 0.17 * 1080 / 1920
                self.ink.append((fa, fb, self.XC - size / 2, yy - hh / 2, self.XC + size / 2,
                                 yy + ((0.8 + 1.66 * arrow) * hh if variant == "link_below" else hh / 2), it["id"]))
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
            # el fondo del hook no gasta el presupuesto de escenas (es el gancho, no una escena)
            if e["kind"] in ("bw", "bg_replace", "color_pop") and not str(e["id"]).startswith("fx_hookbg"):
                t += self.f(e["end"]) - self.f(e["start"])
        return t

    def scenes(self) -> None:
        """Presupuesto de escenas del estilo: fondo reemplazado (papel/reticula) detras de la
        persona en la frase de beneficio (o de humor) con su titulo en tinta de contraste."""
        cfg = self.S["scenes"]
        budget = _lerp(*cfg["budget"], self.I) * self.D
        if not self.auto_matte or not cfg["bg_replace"] or self.scene_time() >= budget:
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
            if not shot["talk"] or shot.get("face_nominal") or not face or not shot.get("matte_ok", True):
                continue                         # sin recorte limpio no hay fondo reemplazado
            if not (0.3 <= face[0] <= 0.7 and 0.095 <= face[3] <= 0.3):
                continue                         # en un primer plano extremo casi no hay fondo que cambiar
            a = max(ws[0]["t0"] - 0.12, shot["start"] + 0.05)
            b = min(ws[-1]["t1"] + 0.45, shot["end"] - 0.05)
            dur = self.f(b) - self.f(a)
            if dur < 1.3 or dur > max(3.2, room + 0.5):
                continue
            # el rotulo nunca queda a medias entre el fondo nuevo y el real: el fondo lo cubre
            # completo (hasta 1.6 s mas, dentro de la toma) o el rotulo termina con el fondo (o
            # antes de que entre) si asi se lee >= 2 s; si nada de eso cabe, no se usa este bloque
            lts = [lt for lt in self.fx["graphics"] if lt.get("kind") == "lower_third"
                   and float(lt["start"]) < b and a < float(lt["end"])
                   and not (a <= float(lt["start"]) and float(lt["end"]) <= b)]
            opts = []
            if lts:
                a2 = min([a] + [float(lt["start"]) - 0.1 for lt in lts])
                b2 = max([b] + [float(lt["end"]) + 0.1 for lt in lts])
                if a2 >= shot["start"] + 0.05 and b2 <= shot["end"] - 0.05 and (a - a2) + (b2 - b) <= 1.6:
                    opts.append((a2, b2, []))
                fix = []
                for lt in lts:
                    la = float(lt["start"])
                    cut = b - 0.1 if la >= a - 0.05 else a - 0.1
                    if self.f(cut) - self.f(la) < 2.0:
                        fix = None
                        break
                    fix.append((lt, cut))
                if fix is not None:
                    opts.append((a, b, fix))
            else:
                opts.append((a, b, []))
            ok_opt = None
            for a_, b_, fix_ in opts:
                if self.drama_range and a_ < self.drama_range[1] and self.drama_range[0] < b_:
                    continue
                # la escena cambia el fondo detras de la persona: basta con que no haya otra escena
                # ni titulos (un rotulo en la franja baja puede convivir)
                if not (self.free("top", self.f(a_), self.f(b_))
                        and self._zone_free_but("center", self.f(a_), self.f(b_),
                                                {e[2] for e in self.occ["center"] if str(e[2]).startswith("gr_name")})):
                    continue
                ok_opt = (a_, b_, fix_)
                break
            if ok_opt is None:
                continue
            a, b, lt_fix = ok_opt
            dur = self.f(b) - self.f(a)
            score = len(blk) + (1 if any(w["n"] in CLAUSE_W for w in ws[1:]) else 0) + dur * 0.3
            if best is None or score > best[0]:
                best = (score, bi, a, b, shot, lt_fix)
        if best is None:
            return False
        _, bi, a, b, shot, lt_fix = best
        bgk = self.pick("bgr", self.S["scenes"]["bg_replace"])
        light = bgk in LIGHT_BG
        cols = ["#F3EFE6", "#E4DDCF"] if bgk == "paper" else [self.pal["dark"], self.pal["accent2"]]
        it = self.add("effects", "bgswap", {"start": a, "end": b, "kind": "bg_replace", "background": bgk,
                                            "bg_colors": cols})
        if not it:
            return False
        for lt, cut in lt_fix:
            # el rotulo termina junto con el fondo (sin resto sobre el fondo real)
            lt["end"] = _r(cut)
            nf = self.f(cut)
            for z in self.occ:
                self.occ[z] = [(x, nf, iid) if iid == lt["id"] else (x, y, iid) for x, y, iid in self.occ[z]]
            self.ink = [tuple([r[0], min(r[1], nf)] + list(r[2:])) if r[6] == lt["id"] else r for r in self.ink]
        self.bg_fx.append((self.f(a), self.f(b), "light" if light else "dark"))
        self.sfx(a + 0.02, "swoosh_up", prio=6, src_id=it["id"])
        self.sfx(b - 0.05, "whoosh_soft", prio=3, src_id=it["id"])
        self.music_swells.append({"start": _r(a), "end": _r(b), "db": 1.5})
        # titulos de la frase (el bloque sobre el fondo nuevo sale con tinta de contraste)
        n = self.title_sequence(s, s["w0"], s["w1"], "title", ["top", "center"],
                                ["center", "center_low", "top"], humor=info["tone"] == "humor",
                                end_limit=self.title_limit())
        if self.auto_matte and self.S["color_pop"] and self.I >= 0.55:
            self._color_pop(s)
        info["_done"] = n > 0
        return True

    # ---------------------------------------------------------- 6b. escenas relampago
    def flash_scenes(self) -> None:
        """Insertos de ~1 s a pantalla completa en la palabra protagonista de una frase de
        beneficio/problema (fondo del estilo + palabra gigante + icono que se dibuja), como los
        cortes a grafico del editor de referencia. Entran con transicion y salen en seco."""
        n = int(self.S.get("flash", 0) or 0)
        if n <= 0 or self.I < 0.45 or self.windows is not None:
            return
        order = {"beneficio": 0, "problema": 1, "oferta": 1, "otro": 2}
        cands = []
        for s in self.sents:
            sec = s["info"]["section"]
            if sec not in order:
                continue
            ws = self.words[s["w0"]:s["w1"] + 1]
            emph = set(s["info"]["emph"]) | {w["i"] for w in ws if w["kw"] and w["n"] not in STOP}
            if s["info"].get("title"):
                emph |= set(range(s["info"]["title"][0], s["info"]["title"][1] + 1))
            # palabra del inserto: un sustantivo con icono ('LIMPIEZA'), no un intensificador
            # (>= 5 letras: 'auto' de 'auto captions' no es un coche)
            pool_w = [w for w in ws if w["i"] in emph and w["n"] not in STOP and len(w["n"]) >= 5
                      and w["n"] not in FILLER and w["n"] not in LIGHT
                      and not w["n"].endswith(("isimo", "isima", "mente"))]
            if not pool_w or s["info"].get("_done"):
                continue
            if any(len(e) >= 3 for e in find_enums(self.words, s["w0"], s["w1"])):
                continue                     # la lista tiene su propia escena de iconos
            pool_w.sort(key=lambda w: (2 * bool(tone_icon(w["n"], self.role)) + w["n"].endswith(_NOUN_SUFFIX)
                                       + 0.5 * w["kw"] + len(w["n"]) / 20), reverse=True)
            for rank, hero in enumerate(pool_w[:2]):
                if hero["i"] in self.titled_words:
                    continue
                icon = tone_icon(hero["n"], self.role, self.used["topic_icon"])
                if not icon:
                    # los iconos que el LLM propuso para ESTA frase antes que los del rubro
                    own = [ic for ic in s["info"].get("icons") or [] if ic not in ICON_BLOCK
                           and assets.icon_exists(str(ic))]
                    pool = own or topic_icons(self.role)
                    icon = pool[0] if pool else None
                if icon:
                    cands.append((order[sec], rank, s["id"], s, hero, icon))
        cands.sort(key=lambda c: c[:3])
        placed = 0
        done_s: set = set()
        for *_k, s, hero, icon in cands:
            if placed >= n:
                break
            if s["id"] in done_s:
                continue
            if self._flash(s, hero, icon):
                placed += 1
                done_s.add(s["id"])

    def _flash(self, s: dict, hero: dict, icon: str) -> bool:
        shot = self.shot_at(hero["t0"])
        a = self.snap_start(max(hero["t0"] - 0.12, shot["start"] + 0.03))
        fa = self.f(a)
        fb = min(self.f(hero["t1"]) + 0.75, fa + 1.2, shot["f1"] - 0.03)
        hook_end = self.f(self.sents[0]["t1"]) if self.sents else 0.0
        # se acomoda entre lo que ya hay: empieza al terminar un titulo que acaba enseguida y
        # termina antes del siguiente (siempre con la palabra dentro)
        busy = [(a_, b_) for z in ("top", "center", "low") for a_, b_, iid in self.occ[z]
                if not str(iid).startswith("reserve:") and a_ < fb and fa < b_]
        for a_, b_ in busy:
            if a_ <= fa and b_ - fa <= 0.3:
                fa = b_ + 0.02
            elif a_ > fa + 0.5:
                fb = min(fb, a_ - 0.05)
        if fa > self.f(hero["t0"]) + 0.15:
            return False
        a = self.o(fa)
        if fb - fa < 0.75 or fa < max(3.0, hook_end):
            return False                     # el hook muestra la cara (confianza)
        if any(a_ < fb + 0.6 and fa - 0.6 < b_ for a_, b_, _ in self.occ["full"]) \
                or any(a_ < fb and fa < b_ for a_, b_, _ in self.bg_fx) \
                or any(a_ < fb and fa < b_ for a_, b_ in self.reserved) \
                or any(self.f(float(t["start"])) < fb and fa < self.f(float(t["end"])) for t in self.fx["text"]):
            return False
        if self.drama_range and self.f(self.drama_range[0]) - 0.3 < fb and fa < self.f(self.drama_range[1]) + 0.3:
            return False
        b = self.o(fb)
        # palabra gigante (con 1-2 palabras previas chicas) + icono del rubro que se dibuja
        prev = [w for w in self.words[max(s["w0"], hero["i"] - 2):hero["i"]] if w["i"] >= s["w0"]]
        while prev and prev[0]["n"] in STOP and len(prev) > 1 and prev[-1]["n"] not in STOP:
            prev.pop(0)
        font = self.K["display"]
        lines = []
        if prev and self.f(prev[0]["t0"]) >= fa - 0.05:
            lines.append({"text": " ".join(_display(w["text"]) for w in prev), "scale": 0.36,
                          "font": self.K["text"]})
        lines.append({"text": _display(hero["text"]).strip("¿?¡!").upper(), "scale": 1.0, "font": font,
                      "color": self.pal["accent"]})
        size = self._fit_lines(lines, 0.2, 0.74)
        bg = self.pick("bg", [b_ for b_ in self.S["backgrounds"] if b_ not in LIGHT_BG and b_ != "blur_self"]
                       or ["dark_stars"])
        bcol = self.pal["accent"] if _luma(self.pal["accent"]) < 0.9 else "#FFFFFF"
        ty = 0.5
        gr = [{"kind": "icon", "name": icon, "variant": "outline", "badge": "circle", "badge_color": bcol,
               "color": self.pal["dark"] if _luma(bcol) > 0.55 else "#FFFFFF", "draw": True,
               "x": 0.5, "y": _r(ty - size * 0.95 / self.aspect - 0.1, 4), "size": 0.2, "anim_in": "pop",
               "offset": 0.04},
              *self._scene_pair({"kind": "doodle", "name": "sparkle", "x": 0.78,
                                 "y": _r(ty - size * 0.55 / self.aspect, 4), "size": 0.1,
                                 "color": self.pal["accent"], "draw": True, "anim_in": "draw", "offset": 0.12})]
        cw = self.add("cutaways", "flash", {
            "start": a, "end": b, "kind": "title_card", "background": bg,
            "bg_colors": [self.pal["dark"], self.pal["accent2"]],
            # `text` (ademas de las lineas) marca la escena con texto: el render oculta los subtitulos
            "text": {"preset": "stack", "lines": lines, "text": " ".join(ln["text"] for ln in lines),
                     "x": 0.5, "y": ty, "size": _r(size, 4), "font": font,
                     "color": "#FFFFFF", "accent": self.pal["accent"], "anim_in": "pop", "align": "center",
                     "accent_words": [lines[-1]["text"]]},
            "graphics": gr}, zone="full")
        if not cw:
            return False
        self.request_transition(a, "scene")
        self.sfx(a + 0.02, "pop", prio=7, src_id=cw["id"], vary=True)
        self.sfx(b - 0.02, "whoosh_soft", prio=8, src_id=cw["id"] + "o")
        self.music_swells.append({"start": _r(a), "end": _r(b), "db": 2.0})
        self.used["topic_icon"] += 1
        for w in self.words[s["w0"]:s["w1"] + 1]:
            if fa - 0.05 <= w["f0"] and w["f1"] <= fb + 0.1:
                self.titled_words.add(w["i"])
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
                    if self.I >= 0.5:
                        self.arrow_to((self.XC - size / 2, y - h / 2, self.XC + size / 2, y + h / 2),
                                      t0, t1, s, it["id"])
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
    def _lt_texts(self) -> tuple[str, str] | None:
        """(titulo, subtitulo) del rotulo. Con perfil: su nombre + profesion ('Dra. Ana Ruiz' /
        'Odontóloga'; sin nombre, el negocio). Sin perfil: nombre publicable + rol; sin nombre
        confirmado, el rol + el tema (nunca un nombre leido en un letrero sin confirmar)."""
        if self.prof is not None:
            nm, ttl = self.prof.get("name") or "", self.prof.get("title") or ""
            biz = self.prof.get("business") or ""
            if nm:
                # la marca se ve: 'PERFUMISTA · OLFATO' si cabe sin achicar de mas el rol
                if ttl and biz and norm(biz) not in norm(ttl) and norm(biz) not in norm(nm):
                    both = f"{ttl} · {biz}"
                    if lt_sub_fits(both, 0.064, self.style):
                        return nm, both
                return nm, ttl or biz
            if biz:
                return biz, ttl or self.prof.get("tagline") or ""
        role = str(self.ent.get("role") or "").strip()
        if self.name:
            return self.name, role
        if not role:
            return None
        sub = str(self.script.get("topic") or "").strip()
        if not sub or norm(sub) == norm(role) or len(sub.split()) > 3:
            sv = ""
            if "price" in self.anc:
                sv = self._service(self._sent_of(self.anc["price"][0]))
            sub = sv.capitalize() if sv else ""
        if not sub or norm(sub) == norm(role):
            return None
        return role, _cap_first(sub.lower())

    def _try_lower_third(self, sh: dict, fa: float, L: float, title: str, sub: str,
                         end_lim: float) -> bool:
        """Coloca el rotulo en [fa, fa+L] (tiempo final) de la toma `sh` si ahi no hay titulos
        (franjas baja y centro libres), ni escena, ni fondo reemplazado, ni el cierre, y no toca
        la cara (en primer plano va en la franja de los subtitulos, que se ocultan)."""
        fb = fa + L
        if fa < sh["f0"] + 0.02 or fb > min(sh["f1"] - 0.1, end_lim) + 1e-6:
            return False
        if sh["k"] != self.shot_at(self.o(fb - 0.05))["k"] or not self.kept(self.o(fa + 0.05)) \
                or not (self.free("low", fa, fb) and self.free("center", fa, fb)) \
                or any(a < fb and fa < b for a, b in self.reserved) \
                or any(a < fb and fa < b for a, b, _ in self.bg_fx):
            return False
        t0, t1 = self.o(fa), self.o(fb)
        g = self.geom(sh, t0, t1)
        # el panel (~0.085 H) deja >= 0.04 H de aire sobre los subtitulos: no se leen
        # como un solo bloque
        y = _clamp(max(g["low"][0] + 0.02, 0.6), 0.5, self.cap_top - 0.07)
        size = 0.064                 # un nombre largo lo ajusta el renderer (fit_font) a SYM_W
        # rotulo CENTRADO (align 'center'): el panel mide lo que su texto y va centrado en x = 0.5
        hw = lt_width(title, sub, size, self.style) / 2
        rect = (self.XC - hw, y - 0.045, self.XC + hw, y + 0.045)
        fr = self.face_rect(sh, t0, t1)
        hide = False
        if fr is not None and rect[1] < fr[3] and g.get("under") and g.get("chin") is not None:
            # primer plano: el rotulo va en la franja de los subtitulos (se ocultan)
            y = SAFE["bottom"] - 0.012 - 0.045
            rect = (self.XC - hw, y - 0.045, self.XC + hw, y + 0.045)
            hide = rect[1] >= g["chin"] + 0.004
        if (fr is not None and rect[1] < fr[3] and not hide) or not self.clear_of_ink(rect, fa, fb):
            return False
        it = self.add("graphics", "name", {
            "start": t0, "end": t1, "kind": "lower_third", "title": title, "subtitle": sub,
            "align": "center", "x": _r(self.XC, 3), "y": _r(y, 4), "size": size,
            "color": self.pal["accent"], "theme": "dark", "anim_in": "draw", "anim_out": "undraw",
            "rotation": 0.0}, zone="low")
        if not it:
            return False
        self.occ["center"].append((fa, fb, it["id"]))
        self.ink.append((fa, fb, *rect, it["id"]))
        if hide:
            self.cap_hide.append({"start": _r(t0), "end": _r(t1),
                                  "y": float(self.S["captions"]["y"]), "hide": True})
        self.sfx(t0 + 0.02, "swipe", prio=7, src_id=it["id"])
        self.sfx(t0 + 0.3, "pop_soft", prio=3, src_id=it["id"] + "b", vary=True)
        return True

    def lower_third(self) -> None:
        """Rotulo nombre + rol que PRESENTA a quien habla: en la primera toma a camara despues
        del hook (como un editor), donde no haya ningun titulo encima (franja baja Y centro
        libres). El remate final queda solo para seguir + link. Con perfil corre ANTES que los
        titulos (temprano, 2.8 s para leer nombre y profesion) y, si la persona dice su nombre,
        entra cuando lo dice."""
        if self.prof is not None and not PR.uses(self.prof, "lower_third"):
            return
        txt = self._lt_texts()
        if txt is None:
            return
        title, sub = txt
        profiled = self.prof is not None
        Ls = (2.8, 2.4, 2.0) if profiled else (2.4, 2.0)
        end_lim = (self.reserved[0][0] - 2.0) if self.reserved else self.D - 2.5
        na = self.anc.get("name") if profiled else None
        if na is not None and 0 <= na[0] < len(self.words):
            w = self.words[na[0]]
            sh = self.shot_at(w["t0"])
            cands = [(sh, w["f0"] + d) for d in (-0.12, 0.12, 0.36, 0.6, 0.84)]
            nxt = next((x for x in self.shots if x["k"] == sh["k"] + 1), None)
            if nxt is not None and nxt["f0"] - w["f0"] < 1.2:
                # lo dice justo antes de un corte: entra con la toma siguiente
                cands += [(nxt, nxt["f0"] + 0.05 + d) for d in (0.0, 0.25)]
            for L in Ls:
                for sh_, fa in cands:
                    if not sh_["talk"] or sh_.get("face_nominal"):
                        continue
                    if self._try_lower_third(sh_, max(sh_["f0"] + 0.05, fa), L, title, sub, end_lim):
                        # el rotulo ya presenta el nombre dicho: esas palabras no se titulan
                        for i in range(na[0], min(na[1], len(self.words) - 1) + 1):
                            self.titled_words.add(self.words[i]["i"])
                        return
        start_f = 2.0
        if self.sents:
            start_f = max(start_f, self.f(self.sents[0]["t1"]) + 0.3)
        if self.drama_range and self.f(self.drama_range[0]) < start_f + 3:
            start_f = max(start_f, self.f(self.drama_range[1]) + 0.2)
        for sh, L in [(sh, L) for L in Ls for sh in self.shots]:
            if not sh["talk"] or sh.get("face_nominal"):
                continue
            lo, hi = max(sh["f0"] + 0.3, start_f), min(sh["f1"] - 0.2, end_lim)
            fa = lo
            while fa + L <= hi + 1e-6:
                if self._try_lower_third(sh, fa, L, title, sub, end_lim):
                    return
                fa += 0.25

    # ---------------------------------------------------------- 9. CTA final
    def _closing_broll(self) -> dict | None:
        """Toma sin voz que cierra el video (el usuario subio la fachada al final): es la
        tarjeta de cierre."""
        if not self.last_word:
            return None
        for sh in self.shots:
            if not sh["talk"] and sh["f1"] >= self.D - 0.05 and sh["f0"] >= self.last_word["f1"] - 0.15 \
                    and sh["f1"] - sh["f0"] >= 1.2:
                return sh
        return None

    def _final_window(self) -> tuple[float, float] | None:
        """Cierre hasta el ULTIMO cuadro (el CTA no se desvanece antes del final):
          - si el video termina en un b-roll mudo, el cierre es esa toma completa;
          - si la ultima frase es el remate (humor / cierre), el CTA entra al TERMINAR la ultima
            palabra: el chiste se ve limpio y la cola (~2 s) es el CTA;
          - si no, los ultimos ~2-2.6 s."""
        if self.D < 6:
            return None
        lw = self.last_word
        last_f = lw["f1"] if lw else 0.0
        fa = None
        cb = self._closing_broll()
        if cb is not None and self.D - cb["f0"] >= 1.5:
            fa = cb["f0"] + 0.02
        elif lw is not None:
            info = self._sent_of(lw["i"])["info"]
            if info.get("tone") == "humor" or info.get("section") == "cierre":
                fa = max(last_f - 0.05, self.D - 3.2)
                if self.D - fa < 1.6:
                    # el crudo termina casi con la voz: el CTA entra EN la ultima palabra (el
                    # titulo del remate termina ahi y su subtitulo la muestra)
                    fa = max(lw["f0"] - 0.1, self.D - 2.0)
        if fa is None:
            fa = max(self.D - 2.6, self.D * 0.8, min(self.D - 1.8, last_f - 1.6))
        fa = min(fa, self.D - 1.2)
        if getattr(self, "prof", None) is not None and self.D >= 10:
            # con perfil la tarjeta final se lee y se alcanza a tocar: >= END_MIN s (entra sobre las
            # ultimas palabras si hace falta; sus subtitulos se ocultan o quedan debajo)
            fa = min(fa, self.D - self.END_MIN)
        fb = self.D
        return (fa, fb) if fb - fa >= 1.2 - 1e-6 else None

    def reserve_final_cta(self) -> None:
        """Reserva el cierre para el CTA: sin titulos ni acentos encima (cierre limpio)."""
        win = self._final_window()
        if win is None or self.windows is not None:
            return
        self.reserved.append(win)
        for z in ("top", "center", "low"):
            self.occ[z].append((win[0], win[1], "reserve:cta"))

    def title_limit(self) -> float | None:
        """Fin maximo (tiempo original) de un titulo: justo antes del cierre reservado."""
        return self.o(self.reserved[0][0]) - 0.02 if self.reserved else None

    @staticmethod
    def _link_h(size: float, two: bool = False) -> float:
        """Alto (fraccion de H) de la pastilla link_below de ancho `size` (graphics: h = 0.2 W';
        con `display`, dos lineas: 0.25 W')."""
        return size * (0.25 if two else 0.2) * 1080.0 / 1920.0

    def _link_y_max(self, size: float, arrow: float = 1.0, two: bool = False) -> float:
        """y maxima del centro de link_below para que su flecha (escala `arrow`) no pise los
        subtitulos (graphics: la flecha llega a h/2 + 0.3h + 1.66h*escala bajo el centro)."""
        h = self._link_h(size, two)
        return self.cap_top - 0.012 - (0.8 + 1.66 * arrow) * h

    def _zone_free_but(self, zone: str, fa: float, fb: float, skip: set, cta: bool = False) -> bool:
        for z in ([zone, "full"] if zone != "full" else ["full"]):
            for a, b, iid in self.occ[z]:
                if iid in skip or str(iid).startswith("reserve:") or (cta and str(iid).startswith("lockzone:")):
                    continue
                if fa < b - 1e-6 and a < fb - 1e-6:
                    return False
        return True

    def extend_link(self, it: dict) -> bool:
        """El link de la frase del CTA se sostiene hasta el ULTIMO cuadro si el cierre empieza a
        menos de 6 s (en vez de repetir un link identico): mas tiempo en pantalla y el remate
        suma el 'seguir' encima."""
        if not self.reserved or it is None:
            return False
        fs_, fe = self.f(float(it["start"])), self.f(float(it["end"]))
        fa_r = self.reserved[0][0]
        if fa_r - fe > 6.0:
            return False
        zone = zone_of("graphics", it)
        if zone != "low":
            # en la franja del pecho bloquearia los titulos del remate: se baja a la franja baja,
            # mas grande y sin flecha (un pase largo en pantalla; la voz ya dijo 'aqui abajo')
            size = 0.7
            two = bool(it.get("display"))
            y = min(0.625, self._link_y_max(size, 0.0, two))
            if _zone_y(y) != "low" or not self._zone_free_but("low", fs_, self.D, {it["id"]}, cta=True):
                return False
            for z in ("top", "center", "low"):
                self.occ[z] = [e for e in self.occ[z] if e[2] != it["id"]]
            self.ink = [r for r in self.ink if r[6] != it["id"]]
            hh = self._link_h(size, two)
            self.ink.append((fs_, fe, self.XC - size / 2, y - hh / 2, self.XC + size / 2, y + hh / 2, it["id"]))
            it.update({"y": _r(y, 4), "size": size, "arrow_scale": 0.0})
            self.occ["low"].append((fs_, fe, it["id"]))
        elif not self._zone_free_but("low", fe, self.D, {it["id"]}, cta=True):
            return False
        it["end"] = _r(float(self.tl.duration))
        it["anim_out"] = "none"
        self.occ["low"].append((fe, self.D, it["id"]))
        rect = next((r[2:6] for r in self.ink if r[6] == it["id"]), None)
        if rect is not None:
            self.ink.append((fe, self.D, *rect, it["id"]))
        self.link_ext = True
        return True

    LINK_MIN = 1.5            # s (tiempo final) minimos de la pastilla del enlace en pantalla
    # senales de que la voz manda al enlace (con perfil): 'link', 'enlace', 'bio', 'descripcion'
    _BOOK_LINK = {"link", "links", "enlace", "enlaces", "bio", "descripcion"}
    # verbos de reservar (imperativo / infinitivo): bastan solos
    _BOOK_VERB = {"agenda", "agendar", "agendes", "agendame", "agendalo", "agendala", "agendate", "reserva",
                  "reservar", "reserves", "reservame", "aparta", "apartar", "apartes"}
    # sustantivos: solo en la frase del CTA o junto a un verbo de accion ('pide tu cita')
    _BOOK_NOUN = {"cita", "citas", "horario", "horarios", "reservacion", "reservaciones", "agendas"}
    _BOOK_ACT = {"pide", "pidas", "saca", "haz", "hagas", "escribeme", "escribenos", "mandame", "llamame",
                 "visita", "visitame", "entra", "checa", "revisa", "consulta", "busca", "dale", "click", "clic",
                 "toca", "picale", "encuentra", "encuentras"}
    _OWN = {"mi", "mis", "nuestra", "nuestro", "nuestras", "nuestros", "su"}
    _BODY = {"dientes", "diente", "muelas", "muela", "encias", "encia", "boca", "labio", "labios", "mandibula",
             "parpado", "parpados", "ojos", "cabello", "pelo", "espalda", "pierna", "piernas", "brazo", "brazos"}

    def _book_signals(self, s_: dict) -> tuple[int, int | None]:
        """(puntaje, palabra ancla) de una frase como invitacion a reservar / ir al enlace. 'abajo'
        solo cuenta en una frase de enlace ('el link de abajo', 'aqui abajo', 'abajo en la
        descripcion'), nunca 'los dientes de abajo' ni 'de arriba hacia abajo'; 'pagina', 'web' o
        'sitio' solo con 'mi/nuestra' o junto a otra senal; 'cita' u 'horarios' solo en la frase
        del CTA o con un verbo de accion ('pide tu cita')."""
        ws = self.words[s_["w0"]:s_["w1"] + 1]
        ns = [w["n"] for w in ws]
        cta = s_["info"].get("section") == "cta"
        score, anchor = 0, None
        link = [k for k, n in enumerate(ns) if n in self._BOOK_LINK]
        verb = [k for k, n in enumerate(ns) if n in self._BOOK_VERB]
        act = any(n in self._BOOK_ACT for n in ns)
        abajo = []
        for k, n in enumerate(ns):
            if n != "abajo":
                continue
            prev = ns[max(0, k - 4):k]
            nxt = ns[k + 1:k + 5]
            if "arriba" in prev or "arriba" in nxt or any(p in self._BODY for p in prev):
                continue
            if (k > 0 and ns[k - 1] in ("aqui", "ahi", "aca", "alla")) or any(p in self._BOOK_LINK for p in prev) \
                    or any(p in ("descripcion", "comentarios", "link", "enlace", "bio") for p in nxt):
                abajo.append(k)
        noun = [k for k, n in enumerate(ns) if n in self._BOOK_NOUN]
        if noun and not (cta or act or verb or link or abajo):
            noun = []
        page = []
        for k, n in enumerate(ns):
            if n in ("pagina", "web", "sitio", "perfil") and (
                    (k > 0 and ns[k - 1] in self._OWN) or (k > 1 and ns[k - 2] in self._OWN) or link or verb or noun):
                page.append(k)
        strong = sorted(set(link) | set(abajo))
        score = 2 * len(strong) + len(verb) + len(noun) + len(page) + (1 if cta and (strong or verb or noun or page)
                                                                        else 0)
        first = sorted(strong or verb or noun or page)
        if first:
            anchor = ws[first[0]]["i"]
        return score, anchor

    def _booking_anchor(self) -> int | None:
        """Palabra donde la voz manda a reservar o al enlace (ver _book_signals): la frase con mas
        senales (a igualdad, la ultima). Solo con perfil que tiene pagina de reservas o web."""
        best = None
        for s_ in self.sents:
            sc, anchor = self._book_signals(s_)
            if sc <= 0 or anchor is None:
                continue
            key = (sc, s_["id"])
            if best is None or key >= best[0]:
                best = (key, anchor)
        return best[1] if best else None

    def _book_fields(self, force: bool = False) -> dict:
        """Campos de la CTA/tarjeta de reservas del perfil: etiqueta ('Agenda en PaseoClub'),
        enlace corto (`display`), plataforma e icono. {} sin perfil o con la CTA apagada."""
        b = self.book
        if b is None or not (force or PR.uses(self.prof, "booking_cta")):
            return {}
        # una web con la etiqueta automatica ('Sitio web') lleva una accion segun el rubro
        ctx = " ".join(str(x or "") for x in (self.role, self.prof.get("business"), self.prof.get("title"),
                                                self.script.get("topic")))
        return dict({"label": PR.link_cta(b, ctx), "display": b["display"], "platform": b["platform"],
                     "icon": b["icon"]}, **book_media(b))

    def button_color(self) -> str:
        """Color del boton Seguir: el acento del estilo si se lee como boton; si el acento es
        blanco (minimal) el oscuro de la paleta (nunca el rojo generico del renderer)."""
        acc = self.pal["accent"]
        return acc if _luma(acc) <= 0.85 else self.pal["dark"]

    def _follow_fields(self) -> dict | None:
        """Textos del CTA seguir: @usuario, o el nombre publicable (+ rol), o SOLO el rol si el
        nombre no esta confirmado (nunca el nombre de un letrero sin confirmar). Con perfil:
        nombre + icono de la red principal + @usuario, avatar con su foto (o iniciales); None si
        el perfil apago el Seguir o no tiene ni nombre ni red."""
        if self.prof is not None:
            if not PR.uses(self.prof, "follow_cta"):
                return None
            nm = _follow_title(self.prof)
            ps = self.psocial
            if not nm and ps is None:
                return None
            f = {"label": "Sígueme", "title": nm, "handle": ps["handle"] if ps else "",
                 "initials": PR.initials(nm or (ps or {}).get("handle", ""))}
            if ps is not None:
                f["platform"] = ps["platform"]
            else:
                f["subtitle"] = self.prof.get("title") or "Sígueme para más"
            # foto: la que subio el usuario; si no, la de SU red (descargada sola); si no, iniciales
            f.update(follow_media(self.prof, (ps or {}).get("platform"), (ps or {}).get("handle")))
            return f
        handle = str(self.ent.get("handle") or "").strip()
        if handle:
            return {"label": "Sígueme", "handle": handle}
        if self.name:
            return {"label": "Sígueme", "handle": ""}
        role = str(self.ent.get("role") or "").strip()
        if role:
            return {"label": role, "handle": role, "subtitle": "Sígueme para más"}
        return {"label": "Sígueme", "handle": ""}

    END_MIN = 2.2            # s minimos de tarjeta final con perfil (se alcanza a leer y a tocar)
    STACK_GAP = 0.02         # separacion vertical de la pila del cierre (fraccion de H)
    SOC_MAX = 4              # chips de redes en la tarjeta final

    def _end_socials(self, follow: dict | None, link: bool, place: bool = False) -> tuple[list[dict], list[str]]:
        """(chips, no mostrados) de redes del cierre (perfil): las redes y enlaces que NO se ven ya
        en el cierre (la red del Seguir y la pagina de la pastilla de reservas no se repiten). La
        ubicacion va en su tarjeta; con `place` (cierre sin tarjeta de ubicacion) va como chip."""
        if self.prof is None or not PR.uses(self.prof, "end_card"):
            return [], []
        shown = set()
        if follow and follow.get("platform") and follow.get("handle"):
            shown.add((follow["platform"], str(follow["handle"]).lower()))
        out = []
        for l in PR.all_links(self.prof):
            if l["kind"] == "maps" or (l["platform"], str(l.get("handle") or "").lower()) in shown:
                continue
            if link and self.book is not None and l.get("url") == self.book.get("url"):
                continue
            out.append(dict({"platform": l["platform"], "text": l["display"]}, **chip_media(l)))
        if place:
            addr = str(self.prof.get("address") or "")
            mp = next((l for l in PR.all_links(self.prof) if l["kind"] == "maps"), None)
            if addr or mp:
                txt = addr.split(",")[0].strip()[:30] if addr else mp["display"]
                out.append({"platform": "maps", "text": txt})
        extra = [f"{PR.PLATFORMS.get(c['platform'], {}).get('label', c['platform'])} {c['text']}"
                 for c in out[self.SOC_MAX:]]
        return out[:self.SOC_MAX], extra

    def _stack_width(self, ff: dict | None, bf: dict, fs: float = 0.76, ls: float = 0.66) -> float:
        """Ancho COMUN (fraccion de W) de la pila del cierre: el mayor ancho natural del Seguir y
        de la pastilla de reservas (>= 0.72), acotado a SYM_W. Todo se centra en XC (0.5) con ese
        ancho: los bordes quedan alineados (tarjeta de ubicacion, Seguir, chips y reservas) y el
        aire a los lados es el mismo."""
        ws = [0.72]
        if ff is not None:
            ws.append(cta_natural_width(dict(ff, kind="cta", variant="follow", size=fs), self.style))
        if bf:
            ws.append(cta_natural_width(dict(bf, kind="cta", variant="link_below", size=ls), self.style))
        return _r(min(SYM_W, max(ws)), 3)

    def _soc_opts(self, socials: list[dict], width: float) -> list[tuple[str, float, float]]:
        """Opciones de maquetacion de los chips con el ancho comun -> [(layout, size, alto)], de
        mayor a menor: una fila si caben en una linea, si no una rejilla de 2 columnas, si no
        una columna."""
        if not socials:
            return []
        out = []
        n = len(socials)
        for ss in (0.066, 0.058, 0.05):
            row = {"kind": "socials", "links": socials, "layout": "row", "size": ss, "width": width}
            rw, rh = socials_size(row, self.style)
            opts = []
            if rh <= ss * 1080 / 1920 * 1.05 and rw <= width + 1e-3:
                opts.append(("row", ss, rh))
            if n >= 2:
                # rejilla justificada al ancho comun (2 o 4 chips: filas parejas, alineadas con la pila)
                g = ("grid", ss, socials_size(dict(row, layout="grid", cols=2), self.style)[1])
                opts = [g] + opts if n % 2 == 0 else opts + [g]
            opts.append(("column", ss, socials_size(dict(row, layout="column"), self.style)[1]))
            out += opts
        return out

    def _loc_card(self, width: float) -> dict | None:
        """Tarjeta de ubicacion del cierre (direccion / referencia) con el ancho comun, o None."""
        title = self.ent.get("address") or self.ent.get("landmark")
        if not title:
            return None
        sub = self.ent.get("landmark") if self.ent.get("address") and self.ent.get("landmark") != title else ""
        return {"kind": "card", "variant": "location", "title": title, "subtitle": sub or "", "value": "",
                "icon": "map-pin", "theme": "light", "color": "#FFFFFF", "anim_in": "rise", "anim_out": "none",
                "rotation": 0.0, "items": [], "map": False, "size": width}

    def _layout_stack(self, elems: list[str], lo: float, hi: float, width: float, socials: list[dict],
                      two: bool, loc: dict | None, anchor: str = "center", arrow_ok: bool = True
                      ) -> list[tuple] | None:
        """Pila vertical del cierre en [lo, hi] (fraccion de H) con el ancho comun: prueba tamanos
        de mayor a menor y devuelve [(variante, centro y, size, alto, extra)] o None si no cabe.
        `anchor`: 'center' (centrada en el hueco), 'top' o 'bottom' (pegada a ese borde)."""
        gap = self.STACK_GAP
        soc = self._soc_opts(socials, width) if "socials" in elems else [("column", 0.0, 0.0)]
        loc_sizes = (1.0, 0.9) if loc is not None and "location" in elems else (1.0,)
        for fs, ls, ar in ((0.76, 0.66, 1.0), (0.74, 0.64, 0.6), (0.76, 0.66, 0.0), (0.72, 0.6, 0.0),
                           (0.68, 0.56, 0.0)):
            if not arrow_ok and ar > 0:
                continue
            for lay, ss, sh_ in soc:
                for lk in loc_sizes:
                    parts = []
                    for v in elems:
                        if v == "follow":
                            parts.append((v, fs * 0.17 * 1080 / 1920, fs, None))
                        elif v == "socials":
                            parts.append((v, sh_, ss, lay))
                        elif v == "link_below":
                            hl = self._link_h(ls, two)
                            # la flecha cuelga bajo la pastilla: cuenta en el alto de la pila
                            parts.append((v, hl / 2 + (0.8 + 1.66 * ar) * hl if ar > 0 else hl, ls, ar))
                        elif v == "location" and loc is not None:
                            ch = card_height(dict(loc, size=_r(width * lk, 3)), self.style)
                            parts.append((v, ch, _r(width * lk, 3), None))
                    tot = sum(p[1] for p in parts) + gap * (len(parts) - 1)
                    if tot > hi - lo + 1e-6:
                        continue
                    y = lo if anchor == "top" else hi - tot if anchor == "bottom" else lo + (hi - lo - tot) / 2
                    out = []
                    for v, hh, sz, ex in parts:
                        if v == "link_below" and ex:
                            hl = self._link_h(sz, two)
                            out.append((v, y + hl / 2, sz, hl, ex))
                        else:
                            out.append((v, y + hh / 2, sz, hh, ex))
                        y += hh + gap
                    return out
        return None

    def final_cta(self) -> None:
        """Remate hasta el ultimo cuadro: seguir (nombre/rol) + link grande con flecha (o el link
        de la frase del CTA sostenido); sobre el b-roll de cierre suma la ubicacion. Fondo
        desenfocado detras de la persona solo DESPUES de la ultima palabra (el gag se ve).
        Con perfil (tarjeta final): Seguir con su red y @usuario, chips con las demas redes (fila,
        rejilla 2x2 o columna) y la pastilla de reservas con su enlace corto aunque la voz no la
        mencione, todo con un ANCHO COMUN (bordes alineados). En un primer plano donde no cabe
        bajo la barbilla, la tarjeta final es una escena corta (video desenfocado detras). Lo que
        no cabe queda en una nota del director."""
        ff = self._follow_fields()
        if not self.reserved:
            if self.windows is None and self.D >= 6 and ff is not None and not any(
                    it.get("kind") == "cta" for it in self.fixed["graphics"]):
                win = self._final_window()
                if win:
                    extra = {k: v for k, v in ff.items() if k not in ("label", "handle")} if self.prof else None
                    self.cta("follow", self.o(win[0]), self.o(win[1]),
                             label=ff.get("label", "Sígueme") if self.prof else "Sígueme",
                             handle=ff.get("handle", "") if self.prof else "", extra=extra)
            return
        fa, fb = self.reserved[0]
        for z in ("top", "center", "low"):
            self.occ[z] = [e for e in self.occ[z] if e[2] != "reserve:cta"]
        if any(it.get("kind") == "cta" and self.f(float(it["end"])) > fa for it in self.fixed["graphics"]):
            return
        t0, t1 = self.o(fa), float(self.tl.duration)
        shot = self.shot_at(t0 + 0.02)
        g = self.geom(shot, t0, t1)
        ext = self.link_item if getattr(self, "link_ext", False) else None
        book_end = self.prof is not None and self.book is not None and PR.uses(self.prof, "end_card")
        link = (self.anc.get("link") is not None or book_end) and ext is None
        bf = self._book_fields(force=book_end)
        two = bool(bf.get("display"))
        talk = bool(shot["talk"])
        loc = self._loc_card(SYM_W) if not talk else None
        socials, extra_soc = self._end_socials(ff, link or ext is not None, place=talk)
        lo = max(g["center"][0], SAFE["top"] + 0.05) if talk else SAFE["top"] + 0.012
        # palabras dentro del cierre: los subtitulos siguen visibles y el CTA no los pisa
        spoken = any(w["f1"] > fa + 0.05 for w in self.words)
        cap_lim = self.cap_top - 0.014
        closeup = bool(talk and shot.get("face") and not shot.get("face_nominal")
                       and g["center"][0] > cap_lim - 0.1)
        width = self._stack_width(ff, bf) if self.prof is not None else 0.0
        placed: list[tuple] = []          # (variante, centro y, size, alto, extra)
        missing: list[str] = []
        hide_caps = False
        if closeup and ext is None and ff is not None:
            # primer plano: la barbilla cae sobre los subtitulos. Seguir va en la franja de los
            # subtitulos (anclado al fondo de SAFE, nunca sobre la barbilla) y los subtitulos se
            # ocultan desde que entra
            chin = float(g.get("chin") or 0.0)
            for fs in (0.76, 0.72, 0.66, 0.6):
                hf = fs * 0.17 * 1080 / 1920
                yy = SAFE["bottom"] - 0.012 - hf / 2
                if yy - hf / 2 >= chin + 0.004:
                    break
            placed = [("follow", yy, fs, hf, None)]
            hide_caps = True
        elif ext is not None:
            # Seguir (y chips, y la ubicacion sobre el b-roll) ENCIMA del link sostenido, pegados a
            # el: una sola tarjeta final con el ancho comun
            ly = float(ext["y"])
            top_link = ly - self._link_h(float(ext["size"]), bool(ext.get("display"))) / 2
            if self.prof is not None:
                ext["width"] = width
                ext["x"] = _r(self.XC, 3)
            elems = (["location"] if loc is not None else []) + (["follow"] if ff is not None else []) + \
                (["socials"] if socials else [])
            hi = top_link - self.STACK_GAP
            res = None
            for el in (elems, [e for e in elems if e != "location"], [e for e in elems if e != "socials"],
                       [e for e in elems if e == "follow"]):
                if not el:
                    continue
                res = self._layout_stack(el, lo, hi, width or SYM_W, socials, two, loc, anchor="bottom")
                if res is not None:
                    break
            placed = res or []
            if not any(v == "follow" for v, *_x in placed) and ff is not None and g.get("head_top"):
                top0, top1 = g["top"]
                hf = 0.76 * 0.17 * 1080 / 1920
                if top1 - top0 >= hf + 0.02:
                    placed.insert(0, ("follow", (top0 + top1) / 2, 0.76, hf, None))
        else:
            elems = (["location"] if loc is not None else []) + (["follow"] if ff is not None else []) + \
                (["socials"] if socials else []) + (["link_below"] if link else [])
            if talk:
                hi = cap_lim if spoken else SAFE["bottom"] - 0.012
                anchor = "top"
            else:
                hi = SAFE["bottom"] - 0.012
                anchor = "center"
            res = None
            if elems:
                cands = [elems, [e for e in elems if e != "location"], [e for e in elems if e != "socials"],
                         [e for e in elems if e not in ("socials", "location")]]
                for el in cands:
                    if not el:
                        continue
                    res = self._layout_stack(el, lo, hi, width or SYM_W, socials, two, loc, anchor=anchor,
                                             arrow_ok=not (talk and spoken))
                    if res is not None:
                        break
            if res is None and elems:
                # no cabe la pila: solo el link (o solo seguir) en el hueco, sin flecha
                stack = [e for e in elems if e in ("follow", "link_below")] or ["socials"]
                v = stack[-1]
                ls = 0.6
                hf = 0.74 * 0.17 * 1080 / 1920
                if v == "link_below":
                    yy = min(self._link_y_max(ls, 0.0, two), max(lo + 0.05, 0.5))
                    res = [(v, yy, ls, self._link_h(ls, two), 0.0)]
                elif v == "follow":
                    yy = max(lo + hf / 2 + 0.01, 0.52)
                    if spoken:
                        yy = min(yy, cap_lim - hf / 2)
                    res = [(v, yy, 0.74, hf, None)]
                else:
                    sh_ = socials_size({"kind": "socials", "links": socials, "layout": "column", "size": 0.058},
                                       self.style)[1]
                    res = [(v, min(max(lo + sh_ / 2 + 0.01, 0.5), cap_lim - sh_ / 2), 0.058, sh_, "column")]
            placed = res or []
        if socials and not any(v == "socials" for v, *_x in placed) and talk and g.get("head_top"):
            # no cupieron en la pila: arriba de la cabeza (fila, rejilla o columna)
            top0, top1 = max(g["top"][0], SAFE["top"] + 0.01), g["top"][1] - 0.012
            for lay, ss, sh_ in self._soc_opts(socials, width or SYM_W):
                if sh_ <= top1 - top0:
                    placed.append(("socials", (top0 + top1) / 2, ss, sh_, lay))
                    break
        lost_soc = bool(socials) and not any(v == "socials" for v, *_x in placed)
        lost_link = bool(link and bf) and not any(v == "link_below" for v, *_x in placed)
        if self.prof is not None and ff is not None and talk and (lost_soc or lost_link):
            # a camara no caben las redes o la pastilla de reservas (primer plano: bajo la barbilla
            # solo cabe el Seguir): la tarjeta final es una escena corta con el video desenfocado
            # detras y la pila completa alineada
            if self._end_scene(t0, t1, ff, bf, two, socials, extra_soc, ext):
                return
        if lost_soc:
            missing += [f"{PR.PLATFORMS.get(c['platform'], {}).get('label', '')} {c['text']}".strip()
                        for c in socials]
        if lost_link:
            missing.append(bf.get("label") or "reservas")
        if loc is not None and not any(v == "location" for v, *_x in placed):
            missing.append("ubicación")
        missing += extra_soc
        if missing and self.prof is not None:
            self.prof_notes.append("perfil: sin espacio para " + ", ".join(dict.fromkeys(missing))[:200]
                                   + " en el cierre")
        if hide_caps:
            self.cap_hide.append({"start": _r(t0), "end": _r(t1), "y": float(self.S["captions"]["y"]),
                                  "hide": True})
        first = self._place_stack(placed, t0, t1, fa, fb, ff, bf, socials, width, ext)
        if first is None and ext is None:
            return
        if not talk:
            return
        lw = self.last_word
        b0 = max(t0, (lw["t1"] + 0.12) if lw else t0)
        if self.f(t1) - self.f(b0) < 0.6:
            return
        if self.S["scenes"]["bg_blur"] and not shot.get("face_nominal"):
            self.add("effects", "cta_blur", {"start": b0, "end": t1, "kind": "bg_blur", "radius": 16.0,
                                             "fade_in": 0.25}, event=False)
        elif self.S["scenes"]["outline"] and self.auto_matte and shot.get("matte_ok"):
            self.add("effects", "cta_outline", {"start": b0, "end": t1, "kind": "subject_outline",
                                                "color": self.pal["accent"], "width": 6.0, "glow": 0.5},
                     event=False)

    def _stack_fields(self, v: str, sz: float, ex, ff: dict | None, bf: dict, socials: list[dict],
                      width: float) -> dict:
        """Campos (sin tiempos ni posicion) de un elemento de la pila del cierre."""
        if v == "socials":
            f = {"kind": "socials", "links": socials, "layout": ex or "column", "variant": "chips",
                 "theme": "light", "icon_style": "brand", "size": sz, "color": self.pal["accent"],
                 "anim_in": "pop", "anim_out": "none", "stagger": 0.1, "rotation": 0.0}
            if ex == "grid":
                f["cols"] = 2
            if width:
                f["width"] = width
            return f
        if v == "location":
            loc = self._loc_card(sz)
            return dict(loc) if loc else {}
        f = {"kind": "cta", "variant": v,
             "label": (bf.get("label") or self.ent.get("link_label") or "Link abajo") if v == "link_below"
             else (ff or {}).get("label", "Sígueme"),
             "handle": (ff or {}).get("handle", "") if v == "follow" else self.ent.get("handle", ""),
             "size": sz, "color": self.pal["accent"], "anim_in": "pop", "anim_out": "none", "rotation": 0.0}
        if v == "follow":
            f["button_color"] = self.button_color()
            f.update({k_: v_ for k_, v_ in (ff or {}).items() if k_ not in ("label", "handle")})
        else:
            f.update({k_: v_ for k_, v_ in bf.items() if k_ != "label"})
            f["arrow_scale"] = float(ex) if isinstance(ex, (int, float)) else 0.0
            if f["arrow_scale"] >= 1.0:
                f.pop("arrow_scale")
        if width and self.prof is not None:
            f["width"] = width
        return f

    def _place_stack(self, placed: list[tuple], t0: float, t1: float, fa: float, fb: float,
                     ff: dict | None, bf: dict, socials: list[dict], width: float, ext) -> dict | None:
        """Coloca la pila del cierre (entradas escalonadas 0.3 s de arriba hacia abajo)."""
        first = None
        order = sorted(placed, key=lambda p: p[1])
        for k, (v, yy, sz, hh, ex) in enumerate(order):
            st = t0 + (0.15 if v == "location" else 0.0) + 0.3 * k
            fields = self._stack_fields(v, sz, ex, ff, bf, socials, width)
            if not fields:
                continue
            fields.update({"start": st, "end": t1, "x": _r(self.XC, 3), "y": _r(yy, 4)})
            role = {"socials": "socials", "location": "end_place"}.get(v, "cta")
            # la pila ya se midio contra si misma y contra el link sostenido: entra como 'edge' y
            # ocupa su franja a mano (lo que se coloque despues la respeta)
            it = self.add("graphics", role, fields, zone="edge", event=True)
            if not it:
                continue
            zn = _zone_y(yy)
            if zn in self.occ:
                self.occ[zn].append((self.f(st), self.D, it["id"]))
            first = first or it
            h = hh * 2.6 if v == "link_below" and fields.get("arrow_scale", 1.0) > 0 else hh
            ww = width or (sz if v not in ("socials", "location") else SYM_W)
            xc = self.XC
            self.ink.append((fa, fb, xc - ww / 2, yy - h / 2, xc + ww / 2, yy + h / 2, it["id"]))
            if v == "location":
                self.occ["top"].append((self.f(st), self.D, it["id"]))
                self.sfx(st + 0.05, "notification", prio=6, src_id=it["id"])
                continue
            self.sfx(st + 0.04, "click" if v != "socials" else "pop", prio=8 if v != "socials" else 6,
                     src_id=it["id"])
            if v == "follow":
                self.sfx(st + 1.1, "success", prio=5, src_id=it["id"] + "ok")
            elif v == "link_below":
                self.sfx(st + 0.3, "swoosh_down", prio=4, src_id=it["id"])
        return first

    def _end_scene(self, t0: float, t1: float, ff: dict, bf: dict, two: bool, socials: list[dict],
                   extra_soc: list[str], ext) -> bool:
        """Tarjeta final como escena corta (>= END_MIN s) cuando la persona esta en primer plano y
        bajo la barbilla solo cabe el Seguir: el video sigue DESENFOCADO detras (fondo blur_self,
        la voz continua) y encima la pila alineada: Seguir + 'Encuéntrame en' con los chips +
        reservas. Los subtitulos se ocultan en ese tramo."""
        fa, fb = self.f(t0), self.f(t1)
        ext_id = ext.get("id") if ext is not None else None
        # la escena tapa todo: nada mas en pantalla en ese tramo (el link sostenido pasa adentro)
        for z in ("full", "top", "center", "low", "accent"):
            for a, b, iid in self.occ[z]:
                if iid != ext_id and not str(iid).startswith("reserve:") and fa < b - 1e-6 and a < fb - 1e-6:
                    return False
        if fb - fa < 1.5 or (self.windows is not None):
            return False
        link = bool(bf) and (ext is not None or self.book is not None)
        if ext is not None:
            socials2, extra2 = self._end_socials(ff, True, place=True)
            socials, extra_soc = socials2, extra2
        width = self._stack_width(ff, bf)
        elems = ["follow"] + (["socials"] if socials else []) + (["link_below"] if link else [])
        res = None
        for el in (elems, [e for e in elems if e != "socials"]):
            res = self._layout_stack(el, SAFE["top"] + 0.05, SAFE["bottom"] - 0.03, width, socials, two, None,
                                     anchor="center", arrow_ok=False)
            if res is not None:
                break
        if res is None:
            return False
        gr = []
        missing = list(extra_soc)
        for k, (v, yy, sz, _hh, ex) in enumerate(sorted(res, key=lambda p: p[1])):
            f = self._stack_fields(v, sz, ex, ff, bf, socials, width)
            if v == "socials":
                f["title"] = "Encuéntrame en"
                f["y"] = _r(yy + sz * 1080 / 1920 * 0.26, 4)     # la cabecera sube el grupo
            else:
                f["y"] = _r(yy, 4)
            f.update({"x": _r(self.XC, 3), "offset": _r(0.2 + 0.28 * k, 2)})
            gr.append(f)
        if not any(v == "socials" for v, *_x in res) and socials:
            missing += [f"{PR.PLATFORMS.get(c['platform'], {}).get('label', '')} {c['text']}".strip()
                        for c in socials]
        saved = ({z: list(v) for z, v in self.occ.items()}, list(self.ink))
        if ext is not None:
            # el link sostenido termina donde entra la tarjeta (va dentro de ella)
            ef = self.f(max(float(ext["start"]) + 0.3, t0 + 0.2))
            for z in self.occ:
                self.occ[z] = [(a, min(b, fa), iid) if iid == ext_id else (a, b, iid) for a, b, iid in self.occ[z]]
            self.ink = [tuple([r[0], min(r[1], ef)] + list(r[2:])) if r[6] == ext_id else r for r in self.ink]
        cw = self.add("cutaways", "endcard", {
            "start": t0, "end": t1, "kind": "title_card", "background": "blur_self",
            "bg_colors": ["#0B0B12"], "anim_in": "fade", "in_dur": 0.35, "anim_out": "cut",
            "graphics": gr}, zone="full")
        if not cw:
            self.occ, self.ink = saved
            return False
        if ext is not None:
            ext["end"] = _r(max(float(ext["start"]) + 0.3, t0 + 0.2))
            ext["anim_out"] = "fade"
            self.link_ext = False
        self.cap_hide.append({"start": _r(t0), "end": _r(t1), "y": float(self.S["captions"]["y"]), "hide": True})
        self.sfx(t0 + 0.02, "whoosh_soft", prio=7, src_id=cw["id"])
        self.sfx(t0 + 0.24, "click", prio=8, src_id=cw["id"] + "f")
        self.sfx(t0 + 0.2 + 1.1, "success", prio=5, src_id=cw["id"] + "ok")
        if socials:
            self.sfx(t0 + 0.5, "pop", prio=6, src_id=cw["id"] + "s")
        if missing:
            self.prof_notes.append("perfil: sin espacio para " + ", ".join(dict.fromkeys(missing))[:200]
                                   + " en el cierre")
        self.prof_notes.append("perfil: tarjeta final como escena (a camara no cabian las redes o las reservas)")
        return True

    def _fix_lt_bg(self) -> None:
        """El rotulo nunca queda a medias entre el fondo reemplazado y el real: si un fondo
        (escena) empieza o termina mientras el rotulo esta en pantalla, el fondo se extiende hasta
        cubrirlo (+0.1 s, dentro de su toma y <= 1 s mas) o, si no se puede, el rotulo se recorta
        al fondo (-0.1 s) mientras siga >= 2 s en pantalla."""
        lts = [it for it in self.fx["graphics"] if it.get("kind") == "lower_third"]
        bgs = [e for e in self.fx["effects"] if e.get("kind") == "bg_replace"]
        for lt in lts:
            la, lb = float(lt["start"]), float(lt["end"])
            for e in bgs:
                ea, eb = float(e["start"]), float(e["end"])
                if not (ea < lb and la < eb) or (ea <= la and lb <= eb):
                    continue
                sh = self.shot_at(ea + 0.02)
                na, nb = min(ea, la - 0.1), max(eb, lb + 0.1)
                fna, fnb = self.f(na), self.f(nb)
                clash = any(iid != e["id"] and fna < b_ - 1e-6 and a_ < fnb - 1e-6 for a_, b_, iid in self.occ["full"]) \
                    or any(c is not e and float(c["start"]) < nb and na < float(c["end"])
                           for c in self.fx["cutaways"] + [x for x in bgs if x is not e]) \
                    or any((float(t_["start"]) < ea and na < float(t_["end"])) or
                           (float(t_["start"]) < nb and eb < float(t_["end"]))
                           for t_ in self.fx["text"])     # su tinta se midio contra el fondo real
                if na >= sh["start"] + 0.02 and nb <= sh["end"] - 0.03 and (ea - na) + (nb - eb) <= 1.6 \
                        and not clash:
                    old_f = (self.f(ea), self.f(eb))
                    e["start"], e["end"] = _r(na), _r(nb)
                    self.bg_fx = [(self.f(na), self.f(nb), k) if abs(a - old_f[0]) < 1e-3 and abs(b - old_f[1]) < 1e-3
                                  else (a, b, k) for a, b, k in self.bg_fx]
                    continue
                # recorte del rotulo al tramo del fondo o al de fuera (>= 1.8 s en pantalla)
                if la < ea < lb:
                    cand = (la, ea - 0.1) if self.f(ea - 0.1) - self.f(la) >= 1.8 else None
                else:
                    cand = (la, eb - 0.1) if eb < lb and self.f(eb - 0.1) - self.f(la) >= 1.8 else None
                if cand is not None:
                    lt["end"] = _r(cand[1])
                    lb = cand[1]
                    continue
                # ultimo recurso: el fondo termina antes de que entre el rotulo (si queda >= 1.2 s)
                if ea < la < eb <= lb + 1e-6 and self.f(la - 0.1) - self.f(ea) >= 1.2:
                    e["end"] = _r(la - 0.1)

    def watermark(self) -> None:
        """Marca de agua sutil con el @usuario de la red principal del perfil (apagada por
        defecto: `profile.use.handle_watermark`), CENTRADA arriba de SAFE (simetria), hasta el
        cierre. Con pastilla translucida (se lee sobre fondos claros) y SOLO en los tramos donde
        ese lugar esta libre: se aparta cuando un titulo, una escena o la cabeza lo ocupan."""
        if self.prof is None or not PR.uses(self.prof, "handle_watermark") or self.windows is not None:
            return
        ps = self.psocial or PR.primary_social(self.prof)
        if ps is None:
            return
        end_f = self.reserved[0][0] if self.reserved else self.D
        item = {"kind": "socials", "variant": "watermark",
                "links": [dict({"platform": ps["platform"], "text": ps["handle"]}, **chip_media(ps))], "size": 0.03,
                "layout": "row", "anim_in": "fade", "anim_out": "fade", "opacity": 0.8, "backdrop": 0.34,
                "rotation": 0.0, "color": "#FFFFFF"}
        w, h = socials_size(item, self.style)
        x, y = self.XC, SAFE["top"] + h / 2 + 0.008
        box = (x - w / 2 - 0.02, y - h / 2 - 0.012, x + w / 2 + 0.02, y + h / 2 + 0.012)
        busy = [(a, b) for a, b, x0, y0, x1, y1, _i in self.ink
                if x0 < box[2] and box[0] < x1 and y0 < box[3] and box[1] < y1]
        busy += [(self.f(c["start"]), self.f(c["end"])) for c in self.fx["cutaways"]]
        for sh in self.shots:
            # la cabeza (primer plano) tambien ocupa el centro de arriba
            fr = self.face_rect(sh, sh["start"], sh["end"])
            if fr is not None and fr[0] < box[2] and box[0] < fr[2] and fr[1] < box[3] and box[1] < fr[3]:
                busy.append((sh["f0"], sh["f1"]))
        busy.sort()
        free, cur = [], 0.0
        for a, b in busy:
            if a - 0.3 > cur:
                free.append((cur, a - 0.3))
            cur = max(cur, b + 0.3)
        if cur < end_f:
            free.append((cur, end_f))
        for a, b in free:
            a, b = max(0.0, a), min(end_f, b)
            if b - a < 2.0:
                continue
            it = self.add("graphics", "watermark", dict(item, start=self.o(a) if a > 0 else 0.0, end=self.o(b),
                                                        x=_r(x, 4), y=_r(y, 4)), zone="edge", event=False)
            if it:
                self.ink.append((a, b, *box, it["id"]))

    # ---------------------------------------------------------- 10. resto del guion
    def sentences(self) -> None:
        total = sum(self.f(s["t1"]) - self.f(s["t0"]) for s in self.sents) or 1.0
        # en un video largo y conversado un editor titula las ideas, no cada frase (el resto lo
        # llevan los subtitulos): la cobertura objetivo baja con la duracion
        target = _lerp(*self.S["coverage"], self.I) * _clamp(40.0 / max(1.0, self.D), 0.55, 1.0)
        rank = {"hook": 0, "problema": 1, "beneficio": 1, "oferta": 2, "cierre": 2, "otro": 3}
        long_ = self.D > 35.0
        # en un video largo, dentro de cada seccion primero las frases con contenido (nombres
        # propios, palabras clave, numeros) y no las de relleno
        order = sorted(self.sents, key=lambda s: (rank.get(s["info"]["section"], 3),
                                                  -self._sent_score(s) if long_ else 0.0, s["id"]))
        color_pop_used = any(e["kind"] == "color_pop" for e in self.fx["effects"])
        for s in order:
            if s["info"].get("_done"):
                continue
            covered = sum(self.f(w["t1"]) - self.f(w["t0"]) for w in self.words if w["i"] in self.titled_words)
            # el remate con humor siempre lleva titulo (es el chiste: se lee, no solo se oye)
            if covered / total >= target and s["info"]["section"] not in ("beneficio", "problema") \
                    and s["info"].get("tone") != "humor":
                continue
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
                                    ["center", "center_low", "top"], humor=humor,
                                    end_limit=self.title_limit())
            if self.auto_matte and not color_pop_used and (
                    fxk == "color_pop" or (self.S["color_pop"] and info["section"] == "beneficio" and self.I >= 0.55)):
                color_pop_used = self._color_pop(s)
            if n == 0 and fxk in ("zoom", "icono", "garabato", "color_pop"):
                em = [self.words[i] for i in info["emph"]] or ws[-1:]
                self.punch_at(em[0]["t0"], 0.1, 0.4)
            info["_done"] = n > 0
        self._title_gaps()

    def _sent_score(self, s: dict) -> float:
        """Cuanto contenido 'titulable' trae una frase (por palabra, para no premiar lo largo)."""
        ws = self.words[s["w0"]:s["w1"] + 1]
        if not ws:
            return 0.0
        emph = set(s["info"].get("emph") or [])
        sc = 0.0
        for k, w in enumerate(ws):
            if w["n"] in STOP or w["n"] in FILLER or w["n"] in LIGHT or len(w["n"]) < 3:
                sc -= 0.15
                continue
            sc += 0.4
            if w["i"] in emph or w["kw"]:
                sc += 1.2
            if k > 0 and _display(w["text"])[:1].isupper():
                sc += 1.0                        # nombre propio / marca ('Casa Nube', la tienda)
            if re.search(r"\d", w["text"]):
                sc += 1.0
        return sc / math.sqrt(len(ws))

    def _title_gaps(self, gap: float = 7.0) -> None:
        """Ningun tramo largo sin titulo en un video largo: si entre dos titulos pasan mas de
        `gap` s, la mejor frase de ese hueco lleva su titulo aunque se haya alcanzado la
        cobertura (la variedad visual no se acaba en la primera mitad)."""
        if self.D < 20.0 or self.windows is not None:
            return
        for _ in range(8):
            spans = sorted((self.f(float(t["start"])), self.f(float(t["end"]))) for t in self.fx["text"])
            spans += [(a, b) for a, b in self.reserved]
            pts = [(0.0, 0.0)] + spans + [(self.D, self.D)]
            best = None
            for (a0, b0), (a1, b1) in zip(pts[:-1], pts[1:]):
                if a1 - b0 < gap:
                    continue
                cands = [s_ for s_ in self.sents if not s_["info"].get("_done")
                         and b0 + 0.5 <= self.f(s_["t0"]) and self.f(s_["t1"]) <= a1 - 0.3]
                for s_ in cands:
                    sc = self._sent_score(s_)
                    if best is None or sc > best[0]:
                        best = (sc, s_)
            if best is None:
                return
            s_ = best[1]
            n = self.title_sequence(s_, s_["w0"], s_["w1"], "title", ["top", "center"],
                                    ["center", "center_low", "top"], humor=s_["info"]["tone"] == "humor",
                                    max_blocks=3, end_limit=self.title_limit())
            s_["info"]["_done"] = True
            if n == 0:
                continue

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
            "text": {"preset": "stack", "lines": lines, "text": txt, "x": 0.5, "y": 0.46, "size": _r(size, 4),
                     "font": font, "color": "#FFFFFF", "accent": self.pal["accent"],
                     "anim_in": "word_stagger", "align": "center"},
            "graphics": self._scene_pair({"kind": "doodle", "name": "sparkle", "x": 0.78, "y": 0.38, "size": 0.1,
                                          "color": self.pal["accent"], "draw": True, "anim_in": "draw"})},
            zone="full")
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
        # la coronilla tapa ~15% inferior de la palabra (se siente detras y se lee)
        gh = size * 1.12 / self.aspect
        y = _clamp(g["head_top"] - 0.15 * gh, SAFE["top"] + gh / 2, 0.42)
        fields = self.legible_behind({"start": a, "end": b, "preset": "behind_giant", "lines": lines,
                                      "x": 0.5, "y": _r(y, 4), "size": _r(size, 4), "font": font,
                                      "color": "#FFFFFF", "accent": self.pal["accent"],
                                      "behind": True, "anim_in": "scale_down", "anim_out": "fade",
                                      "align": "center", "shadow": 0.2}, shot, a, b)
        it = self.add("text", "behind", fields, zone="top")
        if it:
            self.sfx(a + 0.05, "impact_soft", prio=6, src_id=it["id"])
            self.titled_words.add(hero[0]["i"])
            return True
        return False

    # ---------------------------------------------------------- 10b. enumeraciones
    def _enum_icons_for(self, items: list[list[dict]]) -> list[str] | None:
        """Un icono distinto por elemento; los que no tienen traduccion toman uno del rubro (o
        uno generico) para que la lista se vea completa. None si ninguno se reconoce."""
        icons: list[str | None] = []
        for k, it_ in enumerate(items):
            ic = None
            for w in reversed(it_):
                if w["n"] in STOP:
                    continue
                ic = tone_icon(w["n"], self.role, k)
                if ic and ic not in icons:
                    break
                ic = None
            icons.append(ic)
        if sum(1 for ic in icons if ic) < max(1, len(items) - 1):
            return None
        spare = [n for n in topic_icons(self.role) + ["circle-check", "star", "sparkles", "heart", "award"]
                 if n not in icons and assets.icon_exists(n)]
        return [ic or spare.pop(0) for ic in icons]

    def enumerations(self) -> None:
        """Listas de 3+ elementos ('cortes, barba y cejas'): escena de iconos a pantalla completa
        que se dibujan al pronunciar cada uno, con su etiqueta y un punteado que los une (como
        el editor de referencia). Las de 2 (clausulas paralelas) se marcan con iconos junto al
        titulo en enum_icons()."""
        self.enum_pending: list[tuple[dict, list, list[str]]] = []
        for s in self.sents:
            if s["info"].get("_done"):
                continue
            for items in find_enums(self.words, s["w0"], s["w1"]):
                icons = self._enum_icons_for(items)
                if not icons:
                    continue
                if len(items) >= 3 and self.n_cut_left > 0 and self.S["cutaways"] > 0 \
                        and self._enum_scene(s, items, icons):
                    self.n_cut_left -= 1
                    break
                self.enum_pending.append((s, items, icons))

    def _enum_scene(self, s: dict, items: list[list[dict]], icons: list[str]) -> bool:
        first, last = items[0][0], items[-1][-1]
        shot = self.shot_at(first["t0"])
        a = max(first["t0"] - 0.25, s["t0"] - 0.05, shot["start"] + 0.03)
        nxt = self.sents[s["id"] + 1]["t0"] if s["id"] + 1 < len(self.sents) else self.tl.duration
        b = min(last["t1"] + 0.6, nxt - 0.05, self.shot_at(last["t0"])["end"] - 0.03)
        fa, fb = self.f(a), self.f(b)
        # lo que termina apenas empezada la escena (el golpe del drama que se sostiene) la retrasa
        busy = [e for z in ("full", "top", "center", "low", "accent") for e in self.occ[z]
                if fa < e[1] - 1e-6 and e[0] < fb - 1e-6 and not str(e[2]).startswith("reserve:")]
        if busy and all(e[1] <= fa + 0.45 for e in busy):
            fa = max(e[1] for e in busy) + 0.02
            a = self.o(fa)
        if fb - fa < 1.2 or fb - fa > 4.5 or not self.free("full", fa, fb):
            return False
        n = len(items)
        cols = n if n <= 3 else 2
        rows = (n + cols - 1) // cols
        sz = {1: 0.28, 2: 0.26, 3: 0.22}.get(cols, 0.22)
        # columnas SIMETRICAS respecto a x = 0.5 (3: 0.23 / 0.5 / 0.77 -> caja 0.12..0.88)
        xs = [self.XC + (c - (cols - 1) / 2) * (0.27 if cols == 3 else 0.34) for c in range(cols)]
        dy_row = 0.25
        y0 = 0.45 - (rows - 1) * dy_row / 2
        gr = []
        bcol = self.pal["accent"] if _luma(self.pal["accent"]) < 0.9 else "#FFFFFF"
        for k, (it_, ic) in enumerate(zip(items, icons)):
            r_, c_ = divmod(k, cols)
            x, y = xs[c_], y0 + r_ * dy_row
            off = max(0.05, self.f(it_[0]["t0"]) - fa - 0.08)
            label = " ".join(_display(w["text"]) for w in it_).strip(",.;:").upper()
            gr.append({"kind": "icon", "name": ic, "variant": "outline", "badge": "circle", "badge_color": bcol,
                       "color": self.pal["dark"] if _luma(bcol) > 0.55 else "#FFFFFF", "draw": True,
                       "x": _r(x, 3), "y": _r(y, 4), "size": sz, "anim_in": "pop", "offset": _r(off, 3)})
            gr.append({"kind": "sticker_text", "text": label, "x": _r(x, 3), "y": _r(y + sz * 0.5 / self.aspect * 1.2 + 0.035, 4),
                       "size": 0.05 if cols < 3 else 0.044, "bg": "#FFFFFF", "fg": self.pal["dark"], "anim_in": "pop",
                       "rotation": _r((self.rand(f"el{k}") - 0.5) * 6, 1), "offset": _r(off + 0.12, 3)})
            if k > 0 and c_ > 0 and r_ == 0:
                # punteado SOLO en la primera fila (arco por encima de los iconos): entre filas o
                # en la segunda fila rozaria iconos y etiquetas
                px, py = xs[c_ - 1], y
                gr.append({"kind": "doodle", "name": "dotted_path", "x": _r(px + 0.09, 3),
                           "y": _r(py - sz * 0.62 / self.aspect, 4),
                           "path_to": [_r(x - 0.09, 3), _r(y - sz * 0.62 / self.aspect, 4)],
                           "size": 0.2, "color": "#FFFFFF", "draw": True, "anim_in": "draw",
                           "offset": _r(max(0.0, off - 0.2), 3), "bend": 0.5})
        self._center_group(gr)
        label = self._enum_label(s, first)
        text = None
        if label:
            box = self.pal["accent"]
            text = {"preset": "kicker", "text": label,
                    "x": 0.5, "y": _r(max(SAFE["top"] + 0.05, y0 - sz * 0.5 / self.aspect - 0.09), 4), "size": 0.058,
                    "font": self.K["text"], "box": box, "color": "#141414" if _luma(box) > 0.55 else "#FFFFFF",
                    "anim_in": "slide_left"}
        bg = self.pick("bg", [b_ for b_ in self.S["backgrounds"] if b_ in ("grid_warp", "dark_stars")] or ["dark_stars"])
        fields = {"start": a, "end": b, "kind": "title_card", "background": bg,
                  "bg_colors": [self.pal["dark"], self.pal["accent2"]], "graphics": gr}
        if text:
            fields["text"] = text
        cw = self.add("cutaways", "list", fields, zone="full")
        if not cw:
            return False
        self.scene_transitions(a, b)
        for k, it_ in enumerate(items):
            self.sfx(it_[0]["t0"] - 0.05, "pop" if k % 2 == 0 else "bubble", prio=6, src_id=cw["id"] + f"i{k}", vary=True)
        self.music_swells.append({"start": _r(a), "end": _r(b), "db": 2.5})
        for w in self.words[s["w0"]:s["w1"] + 1]:
            if a <= w["t0"] <= b:
                self.titled_words.add(w["i"])
        return True

    @staticmethod
    def _nested_span(g: dict) -> tuple[float, float] | None:
        """Extension horizontal (x0, x1) estimada de un grafico dentro de una escena: icono y
        garabato por su caja, etiqueta por su pastilla (texto + relleno + borde, inclinada),
        punteado de x a path_to."""
        k = g.get("kind")
        x = float(g.get("x", 0.5))
        sz = float(g.get("size", 0.1))
        if k == "sticker_text":
            txt = str(g.get("text") or "")
            ph = sz * 1.45
            pw = _text_w(g.get("font") or "Poppins-Black", sz, txt) + sz
            ang = math.radians(abs(float(g.get("rotation", 0.0) or 0.0)) + 1.4)
            half = (pw / 2 + 8 / 1080) * math.cos(ang) + (ph / 2 + 8 / 1080) * math.sin(ang)
            return x - half, x + half
        pt = g.get("path_to")
        if pt:
            return min(x, float(pt[0])) - 0.02, max(x, float(pt[0])) + 0.02
        if k in ("icon", "doodle"):
            return x - sz / 2, x + sz / 2
        return None

    def _center_group(self, gr: list[dict]) -> None:
        """Centra en x = 0.5 la caja comun de los graficos de una escena de lista (las etiquetas
        de distinto ancho la cargaban a un lado): mismo aire a la izquierda y a la derecha."""
        spans = [sp for sp in (self._nested_span(g) for g in gr) if sp is not None]
        if not spans:
            return
        lo, hi = min(a for a, _ in spans), max(b for _, b in spans)
        dx = self.XC - (lo + hi) / 2
        if abs(dx) < 0.001:
            return
        for g in gr:
            g["x"] = _r(float(g.get("x", 0.5)) + dx, 3)
            if g.get("path_to"):
                g["path_to"] = [_r(float(g["path_to"][0]) + dx, 3), g["path_to"][1]]

    def _enum_label(self, s: dict, first: dict) -> str:
        """Etiqueta de la escena de lista: el sintagma que la introduce, completo y corto
        ('MEJORA TU ENERGIA', 'CAMBIA TU ESTILO', 'HACEMOS'): <= 3 palabras antes de la lista,
        sin adverbios en -mente ni palabras funcionales colgando."""
        lead = [w for w in self.words[s["w0"]:first["i"]] if not w["n"].endswith("mente")]
        while lead and lead[-1]["n"] in STOP:
            lead.pop()
        lead = lead[-3:]
        while lead and lead[0]["n"] in STOP:
            lead.pop(0)
        if not lead:
            t = str(self.script.get("topic") or "").strip()
            return t.upper() if t and len(t.split()) <= 3 else ""
        return " ".join(_display(w["text"]) for w in lead).strip(",.¿?¡!").upper()

    def enum_icons(self) -> None:
        """Enumeraciones cortas: un icono que se dibuja junto al titulo al decir cada elemento."""
        for s, items, icons in getattr(self, "enum_pending", []):
            for k, (it_, ic) in enumerate(zip(items, icons)):
                t_i = it_[0]["t0"] - 0.05
                tit = [x for x in self.fx["text"] if x.get("_geom") and float(x["start"]) <= t_i + 0.3
                       and float(x["end"]) >= t_i + 0.6]
                if not tit:
                    continue
                self.accent_for(tit[0], it_, set(), f"en{s['id']}_{k}", t_i, float(tit[0]["end"]),
                                force="icon", icon=ic, only=True)

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
        # sin palabras (b-roll mudo): un destello decorativo a mitad del hueco, centrado o en par
        # espejo (0.22 / 0.78)
        t = self.o(mid)
        shot = self.shot_at(t)
        t1 = self.o(mid + 1.2)
        for x in (self.XC, 0.78):
            ok = self.accent_ok(x, 0.2, 0.1, mid, mid + 1.2, shot, t, t1)
            if ok and x != self.XC:
                ok = self.accent_ok(2 * self.XC - x, 0.2, 0.1, mid, mid + 1.2, shot, t, t1)
            if not ok:
                continue
            name = self.pick("doodle", ["sparkle", "star4"])
            fields = {"start": t, "end": self.o(min(self.D, mid + 1.3)), "kind": "doodle", "name": name,
                      "y": 0.2, "size": 0.1, "draw": True, "wiggle": 0.3, "anim_in": "draw",
                      "anim_out": "fade"}
            if x == self.XC:
                g = self.add("graphics", "beat", dict(fields, x=x, rotation=0.0,
                                                      color=self.doodle_color(t, t1, x, 0.2)), zone="accent")
            else:
                g = self.add_pair("beat", dict(fields, color=self.pair_color(t, t1, x, 0.2)), x)
            if g:
                self._doodle_sfx(name, t + 0.03, x, g["id"], pair=x != self.XC)
                return True
        return False

    def _anchor_words(self) -> set:
        out = set()
        for k in ("price", "address", "landmark", "name"):
            if k in self.anc:
                a, b = self.anc[k]
                out |= set(range(a, b + 1))
        return out

    def _float_accent(self, w: dict, shot: dict, icon_only: bool = False, icon: str | None = None) -> dict | None:
        """Acento de ritmo sobre la cabeza: un icono (siempre centrado: tiene mano) o un garabato
        (glifo simetrico en par espejo a los lados de la cabeza, o uno centrado). Si nada cabe
        simetrico (la cabeza ocupa el centro), no hay acento."""
        t0 = w["t0"] - 0.05
        t1 = min(w["t1"] + 1.2, shot["end"] - 0.03)
        fa, fb = self.f(t0), self.f(t1)
        if fb - fa < 0.9:
            return None
        ht = self.geom(shot, t0, t1)["head_top"]
        base_y = _clamp((ht - 0.02) if ht else 0.3, SAFE["top"] + 0.06, 0.45)
        single = next(((self.XC, y) for y in (base_y, base_y - 0.06)
                       if self.accent_ok(self.XC, y, 0.12, fa, fb, shot, t0, t1)), None)
        icon = icon or tone_icon(w["n"], self.role, self.used["topic_icon"])
        if icon:
            if single is None:
                return None
            x, y = single
            dark_bg = self._bg_at(shot, y - 0.03, y + 0.03, t0, t1)
            light = dark_bg is not None and contrast("#FFFFFF", dark_bg["rgb"]) < 2.4
            bcol = self.pal["dark"] if light else self.pal["accent"]
            g = self.add("graphics", "beat_icon", {
                "start": t0, "end": t1, "kind": "icon", "name": icon, "variant": "outline",
                "badge": "circle", "badge_color": bcol,
                "color": self.pal["dark"] if _luma(bcol) > 0.55 else "#FFFFFF", "draw": True,
                "x": _r(x, 3), "y": _r(y, 4), "size": 0.12, "anim_in": "pop", "anim_out": "pop_out",
                "rotation": _r((self.rand("bi" + str(w["i"])) - 0.5) * 14, 1)}, zone="accent")
            if g:
                self.used["topic_icon"] += 1
                self.sfx(t0 + 0.02, "bubble", prio=4, pan=(x - 0.5), src_id=g["id"], vary=True)
            return g
        if icon_only:
            return None
        pair = next(((self.XC + d, base_y) for d in (0.28, 0.24)
                     if self.accent_ok(self.XC + d, base_y, 0.12, fa, fb, shot, t0, t1)
                     and self.accent_ok(self.XC - d, base_y, 0.12, fa, fb, shot, t0, t1)), None)
        if single is None and pair is None:
            return None
        pool = [d for d in self.S["doodles"] if d not in ("underline", "circle_scribble", "arrow_curve")
                and not d.startswith("arrow")] or ["sparkle"]
        if single is None:
            pool = [d for d in pool if d in SYM_GLYPHS] or ["sparkle"]     # solo cabe el par
        name = self.pick("doodle", pool)
        rot = _r((self.rand("bd" + str(w["i"])) - 0.5) * 20, 1)
        fields = {"start": t0, "end": t1, "kind": "doodle", "name": name, "size": 0.11, "draw": True,
                  "wiggle": 0.35, "anim_in": "draw", "anim_out": "fade"}
        if name in SYM_GLYPHS and pair is not None:
            x, y = pair
            g = self.add_pair("beat_doodle", dict(fields, y=_r(y, 4), color=self.pair_color(t0, t1, x, y)), x, rot)
            if g:
                self._doodle_sfx(name, t0 + 0.03, self.XC, g["id"], pair=True)
            return g
        x, y = single
        g = self.add("graphics", "beat_doodle", dict(
            fields, x=_r(x, 3), y=_r(y, 4), color=self.doodle_color(t0, t1, x, y),
            rotation=rot if name in SYM_GLYPHS else 0.0), zone="accent")
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
            if cat == "air" and self.near_ramp(ft, 0.15, 0.4):
                continue                          # la rampa de velocidad es la transicion: sin whoosh
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
            if SFX_CAT.get(r["name"]) in ("ui", "tonal") and r["name"] not in ("typing",):
                at = self.beat_snap(at, 0.07)      # pops/destellos al pulso de la musica
            fields = {"at": max(0.0, at), "name": r["name"], "gain_db": _r(_clamp(r["gain"], -30, 3), 1),
                      "pan": _r(r["pan"], 2)}
            if r.get("dur") is not None:
                fields["dur"] = r["dur"]
            self.add("sfx", r["name"], fields)

    # ---------------------------------------------------------- 13. simetria (regla de la marca)
    # red de seguridad al final de run() y nucleo de symmetrize(plan): lo centrado a x = 0.5 (ancho
    # <= SYM_W), glifos simetricos en par espejo exacto, glifos con mano e iconos centrados,
    # flechas verticales centradas; lo que no cabe simetrico (cara, tinta, separacion) se quita
    _TWIN_KEYS = ("kind", "name", "size", "y", "start", "end", "anim_in", "anim_out", "color", "draw", "wiggle",
                  "border_color", "aspect", "double", "stroke", "enabled", "intensity", "z", "offset", "length",
                  "shadow", "glow", "style")

    def _sym_rect(self, tr: str, it: dict) -> tuple | None:
        """Caja (x0, y0, x1, y1) normalizada de un item en reposo: texto medido con text.py (con el
        relleno de su caja); graficos estimados con las medidas de graphics."""
        if tr == "text":
            r = measure_text(it, self.style)
            return None if r is None else self._pad_rect(it, r)
        k = it.get("kind")
        if it.get("x") is None:
            return None
        x, y = float(it.get("x")), float(it.get("y", 0.5) if it.get("y") is not None else 0.5)
        sz = float(it.get("size", 0.1) or 0.1)
        if k in ("doodle", "icon"):
            pt = it.get("path_to")
            if pt:
                return (min(x, float(pt[0])) - 0.03, min(y, float(pt[1])) - 0.015,
                        max(x, float(pt[0])) + 0.03, max(y, float(pt[1])) + 0.015)
            return _accent_rect(self, it)
        if k == "card":
            w, h = sz, card_height(it, self.style)
        elif k == "cta":
            w = float(it.get("width") or 0.0) or min(SYM_W, cta_natural_width(it, self.style))
            if it.get("variant") == "link_below":
                h = self._link_h(sz, bool(it.get("display")))
                return (x - w / 2, y - h / 2, x + w / 2, y + (0.8 + 1.66 * float(it.get("arrow_scale", 1.0))) * h)
            h = sz * 0.17 * 1080 / 1920
        elif k == "sticker_text":
            w = min(SYM_W, _text_w(it.get("font") or "Poppins-Black", sz, str(it.get("text") or "")) + sz * 1.3)
            h = sz * 1.6 * 1080 / 1920
        elif k == "lower_third":
            if it.get("align") != "center":
                return (x, y - 0.045, SAFE["right"], y + 0.045)
            w, h = lt_width(it.get("title", ""), it.get("subtitle", ""), sz, self.style), 0.09
        elif k == "socials":
            w, h = socials_size(it, self.style)
        elif k == "media":
            w, h = sz, sz * 1080 / 1920
        else:
            return None
        return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)

    def _sym_set_ink(self, it: dict, rect: tuple | None) -> None:
        """La tinta registrada de un item pasa a `rect` (mismos tramos de tiempo)."""
        iid = str(it.get("id"))
        spans = sorted({(r[0], r[1]) for r in self.ink if r[6] == iid})
        self.ink = [r for r in self.ink if r[6] != iid]
        if rect is None:
            return
        if not spans:
            spans = [(self.f(float(it["start"])), self.f(float(it["end"])))]
        for a, b in spans:
            self.ink.append((a, b, *rect, iid))

    def _sym_seat(self, g: dict, x: float, y: float | None = None) -> None:
        """Mueve un acento (x, y) y su registro (franja 'accent' y posicion)."""
        g["x"] = _r(x, 3)
        if y is not None:
            g["y"] = _r(y, 4)
        iid = str(g["id"])
        self.acc_pos[iid] = (float(g["x"]), float(g.get("y", 0.5)))
        if not any(e[2] == iid for e in self.occ["accent"]):
            self.occ["accent"].append((self.f(float(g["start"])), self.f(float(g["end"])), iid))

    def _sym_sync(self, g: dict, tw: dict) -> bool:
        """El gemelo '<id>_m' queda como espejo EXACTO de su base. -> cambio algo."""
        before = json.dumps(tw, sort_keys=True, default=str)
        for k in self._TWIN_KEYS:
            if k in g:
                tw[k] = copy.deepcopy(g[k])
            else:
                tw.pop(k, None)
        tw.pop("path_to", None)
        tw["x"] = _r(2 * self.XC - float(g["x"]), 3)
        tw["rotation"] = _r(-float(g.get("rotation", 0.0) or 0.0), 1) + 0.0
        if json.dumps(tw, sort_keys=True, default=str) == before:
            return False
        self._sym_seat(tw, float(tw["x"]), float(tw["y"]))
        return True

    def _sym_twin(self, g: dict, fx: dict) -> dict:
        """Crea el gemelo espejo exacto '<id>_m' de un glifo simetrico (en fx y en el registro)."""
        tid = str(g["id"]) + TWIN
        tw = {k: copy.deepcopy(v) for k, v in g.items() if not str(k).startswith("_")}
        tw["id"] = tid
        lst = fx.setdefault("graphics", [])
        idx = next((i for i, x in enumerate(lst) if x is g), None)
        if idx is None:
            lst.append(tw)
        else:
            lst.insert(idx + 1, tw)
        self.ids.add(tid)
        self._sym_sync(g, tw)
        self._sym_seat(tw, float(tw["x"]), float(tw["y"]))
        return tw

    def _sym_legible(self, g: dict, tw: dict | None) -> None:
        """Un garabato movido (o su par) se sigue leyendo contra el fondo de CADA lado (el que se
        conoce): un trazo blanco lleva contorno oscuro donde no le sobra contraste; un color que no
        se lee pasa a blanco con contorno (los dos miembros igual: espejo exacto)."""
        col = g.get("color")
        if g.get("kind") != "doodle" or not col or g.get("border_color"):
            return
        white = _luma(col) > 0.85
        t0, t1 = float(g["start"]), float(g["end"])
        shot = self.shot_at(t0 + 0.02)
        swap = border = False
        for m in [g] + ([tw] if tw is not None else []):
            mx, my = float(m["x"]), float(m.get("y", 0.5))
            bg = self._bg_at(shot, my - 0.03, my + 0.03, t0, t1, mx - 0.06, mx + 0.06)
            if bg is None:
                border = border or white
            elif contrast(col, bg["rgb"]) < (4.5 if white else 3.0):
                border = True
                swap = swap or not white
        for m in [g] + ([tw] if tw is not None else []):
            if swap:
                m["color"] = "#FFFFFF"
            if border:
                m["border_color"] = "#161616"

    def _sym_sfx_of(self, g: dict, fx: dict, mv) -> dict | None:
        """SFX (del plan) que acompana la entrada de un acento: mismo sonido a <= 0.12 s."""
        names = _accent_sfx_names(g)
        t = float(g.get("start", 0.0)) + 0.03
        best = None
        for sx in fx.get("sfx") or []:
            if not isinstance(sx, dict) or sx.get("name") not in names or not mv(sx):
                continue
            d = abs(float(sx.get("at", -9.0)) - t)
            if d <= 0.12 and (best is None or d < best[0]):
                best = (d, sx)
        return best[1] if best else None

    def _sym_pan0(self, g: dict, fx: dict, mv, plan_mode: bool) -> None:
        """El SFX de un par espejo suena al centro (uno solo, pan 0)."""
        iid = str(g["id"])
        for r in self.sfx_req:
            if str(r.get("src", "")) == iid:
                r["pan"] = 0.0
        if plan_mode:
            sx = self._sym_sfx_of(g, fx, mv)
            if sx is not None:
                sx["pan"] = 0.0

    def _sym_drop(self, g: dict, fx: dict, mv, plan_mode: bool, pair: bool = True) -> list[str]:
        """Quita un acento que no cabe simetrico (y a su gemelo, si es de la IA) con su SFX."""
        iid = str(g.get("id"))
        gone = {iid}
        if pair and g.get("kind") == "doodle":
            base = iid[:-len(TWIN)] if iid.endswith(TWIN) else iid
            gone |= {x_ for x_ in (base, base + TWIN)
                     if any(str(o.get("id")) == x_ and mv(o) for o in fx.get("graphics") or [])}
        if not plan_mode:
            for o in [o for o in fx.get("graphics") or [] if str(o.get("id")) in gone]:
                self._unplace(o, pair=False)
            return sorted(gone)
        # el SFX es de la base (un par lleva uno solo): se va si se va la base
        sx = self._sym_sfx_of(g, fx, mv) if g.get("kind") in ("doodle", "icon") and \
            any(not x_.endswith(TWIN) for x_ in gone) else None
        fx["graphics"][:] = [o for o in fx.get("graphics") or [] if str(o.get("id")) not in gone]
        if sx is not None and sx in (fx.get("sfx") or []):
            fx["sfx"].remove(sx)
        for z in list(self.occ):
            self.occ[z] = [e for e in self.occ[z] if e[2] not in gone]
        self.ink = [r for r in self.ink if r[6] not in gone]
        for x_ in gone:
            self.acc_pos.pop(x_, None)
        return sorted(gone)

    def _sym_owner(self, g: dict, fx: dict, tracks: tuple = ("text",)) -> tuple[dict, tuple] | None:
        """(item, caja NUEVA) del titulo (o grafico) al que acompana un acento: se cruza en el tiempo
        y su caja (la de antes del pase) queda a <= 0.1 W / 0.08 H del acento; a igualdad, el de
        la misma frase y el que mas tiempo comparte."""
        if g.get("path_to"):
            px, py = float(g["path_to"][0]), float(g["path_to"][1])
            gr = (px - 0.06, py - 0.04, px + 0.06, py + 0.04)
        else:
            gr = _accent_rect(self, g)
        a, b = float(g["start"]), float(g["end"])
        anc = _anchor_of(str(g.get("id", "")))
        best = None
        for tr in tracks:
            for it in fx.get(tr) or []:
                if it is g or not isinstance(it, dict) or it.get("enabled") is False:
                    continue
                if tr == "graphics" and it.get("kind") in ("doodle", "icon", "progress", "media"):
                    continue
                if not (float(it.get("start", 0)) < b and a < float(it.get("end", 0))):
                    continue
                r0 = self._sym_old.get(str(it.get("id"))) or self._sym_rect(tr, it)
                if r0 is None:
                    continue
                if not (gr[0] < r0[2] + 0.1 and r0[0] - 0.1 < gr[2] and gr[1] < r0[3] + 0.08 and r0[1] - 0.08 < gr[3]):
                    continue
                key = (_anchor_of(str(it.get("id", ""))) == anc,
                       min(b, float(it["end"])) - max(a, float(it["start"])))
                if best is None or key > best[0]:
                    best = (key, it, tr)
        if best is None:
            return None
        r1 = self._sym_rect(best[2], best[1])
        return (best[1], r1) if r1 is not None else None

    def _sym_arrow(self, g: dict, fx: dict, mv, plan_mode: bool, rep: dict) -> None:
        """Flecha que senala: vertical y centrada (x = path_to x = 0.5, bend 0), por arriba o por
        abajo de lo que senala; si no cabe asi, se quita."""
        iid = str(g["id"])
        xc = self.XC
        pt = [float(g["path_to"][0]), float(g["path_to"][1])]
        x = float(g["x"])
        arrow = str(g.get("name", "")).startswith("arrow")
        if abs(x - xc) <= SYM_TOL and abs(pt[0] - xc) <= SYM_TOL:
            ch_ = abs(x - xc) > 1e-6 or abs(pt[0] - xc) > 1e-6 or float(g.get("rotation", 0.0) or 0.0) != 0.0 \
                or (arrow and float(g.get("bend", 1.0) or 0.0) != 0.0)
            if ch_:
                g["path_to"] = [_r(xc, 3), g["path_to"][1]]
                g["rotation"] = 0.0
                if arrow:
                    g["bend"] = 0.0
                self._sym_seat(g, xc)
                self._sym_set_ink(g, self._sym_rect("graphics", g))
                rep["moved"].append(iid)
            return
        t0, t1 = float(g["start"]), float(g["end"])
        fa, fb = self.f(t0), self.f(t1)
        shot = self.shot_at(t0 + 0.02)
        own = {iid, iid + TWIN}
        tgt = self._sym_owner(g, fx, ("graphics", "text"))
        if tgt is not None:
            _it, rect = tgt
            fr = self.face_rect(shot, t0, t1)
            for sy, ey in ((rect[1] - 0.1, rect[1] - 0.012), (rect[3] + 0.09, rect[3] + 0.012)):
                box = (xc - 0.03, min(sy, ey) - 0.015, xc + 0.03, max(sy, ey) + 0.015)
                if box[1] < SAFE["top"] or box[3] > min(SAFE["bottom"], self.cap_top - 0.005):
                    continue
                if fr is not None and box[0] < fr[2] and fr[0] < box[2] and box[1] < fr[3] and fr[1] < box[3]:
                    continue
                if not self.clear_of_ink(box, fa, fb, skip=own | {str(_it.get("id"))}, margin=0.0) or \
                        not self.free_accent(xc, sy, fa, fb, skip=own):
                    continue
                if arrow:
                    g["name"] = "arrow_straight"
                    g["bend"] = 0.0
                g["path_to"] = [_r(xc, 3), _r(ey, 4)]
                g["rotation"] = 0.0
                self._sym_seat(g, xc, sy)
                self._sym_set_ink(g, box)
                # la flecha cae sobre otro fondo (p. ej. la ropa, al centro): el color elegido para
                # su lugar viejo puede no leerse -> contraste contra el fondo de su tramo nuevo
                probe = dict(g, y=(sy + ey) / 2)
                self._sym_legible(probe, None)
                g.update({k_: probe[k_] for k_ in ("color", "border_color") if k_ in probe})
                rep["moved"].append(iid)
                return
        rep["dropped"] += self._sym_drop(g, fx, mv, plan_mode)

    def _sym_accent(self, g: dict, fx: dict, mv, plan_mode: bool, rep: dict) -> None:
        """Un acento (garabato / icono) de la IA queda simetrico: centrado, en par espejo exacto
        (glifos simetricos) o fuera."""
        iid = str(g.get("id"))
        byid = {str(o.get("id")): o for o in fx.get("graphics") or []}
        if iid.endswith(TWIN) and iid[:-len(TWIN)] in byid:
            base = byid[iid[:-len(TWIN)]]
            if mv(base) and base.get("enabled", True) is not False:
                return                              # el gemelo va con su base
            # base fijada por el usuario (o apagada): el gemelo la sigue en espejo, o se va
            if base.get("kind") == "doodle" and base.get("name") in SYM_GLYPHS and base.get("x") is not None \
                    and abs(float(base["x"]) - self.XC) >= ACCENT_SEP / 2 - 1e-6:
                if self._sym_sync(base, g):
                    rep["moved"].append(iid)
            else:
                rep["dropped"] += self._sym_drop(g, fx, mv, plan_mode, pair=False)
            return
        if g.get("x") is None:
            return                                  # anclado a la cara por el usuario (face_*)
        if g.get("path_to"):
            self._sym_arrow(g, fx, mv, plan_mode, rep)
            return
        xc = self.XC
        doodle = g.get("kind") == "doodle"
        name = g.get("name") if doodle else None
        tw = byid.get(iid + TWIN) if doodle else None
        x, y = float(g["x"]), float(g.get("y", 0.5))
        sz = float(g.get("size") or _default_size(g))
        if name in MARK_GLYPHS:
            # marca sobre una linea (subrayado, circulo): centrada en su linea, que va centrada
            if abs(x - xc) <= 0.03:
                if abs(x - xc) > 1e-6:
                    self._sym_seat(g, xc)
                    rep["moved"].append(iid)
                if tw is not None and mv(tw):
                    rep["dropped"] += self._sym_drop(tw, fx, mv, plan_mode, pair=False)
            else:
                rep["dropped"] += self._sym_drop(g, fx, mv, plan_mode)
            return
        pairable = name in SYM_GLYPHS
        if tw is not None:
            if not mv(tw):
                return                              # el usuario fijo el gemelo: el par se queda
            if pairable and abs(x - xc) >= ACCENT_SEP / 2 - 1e-6:
                if self._sym_sync(g, tw):
                    self._sym_legible(g, tw)
                    tw.update({k_: g[k_] for k_ in ("color", "border_color") if k_ in g})
                    rep["moved"].append(tw["id"])
                return
            rep["dropped"] += self._sym_drop(tw, fx, mv, plan_mode, pair=False)
            tw = None
        if abs(x - xc) <= SYM_TOL:
            if abs(x - xc) > 1e-6:
                self._sym_seat(g, xc)
                rep["moved"].append(iid)
            return
        t0, t1 = float(g["start"]), float(g["end"])
        fa, fb = self.f(t0), self.f(t1)
        shot = self.shot_at(t0 + 0.02)
        own = {iid, iid + TWIN}
        # a) glifo simetrico: el gemelo en el lugar espejo (el acento se queda donde estaba)
        if pairable and abs(x - xc) >= ACCENT_SEP / 2 and \
                self.accent_ok(x, y, sz, fa, fb, shot, t0, t1, own=own) and \
                self.accent_ok(2 * xc - x, y, sz, fa, fb, shot, t0, t1, own=own):
            tw = self._sym_twin(g, fx)
            self._sym_legible(g, tw)
            self._sym_pan0(g, fx, mv, plan_mode)
            rep["twins"].append(tw["id"])
            return
        # b) junto a su titulo: par espejo o centrado arriba / abajo (o sobre la cabeza); sin
        #    titulo (acento de ritmo): a los lados de la cabeza, centrado cerca de su altura o
        #    sobre la cabeza
        own_it = self._sym_owner(g, fx)
        rect = own_it[1] if own_it is not None else (xc, y, xc, y)
        spots = self.accent_spot(rect, sz, fa, fb, shot, t0, t1, pair=pairable, own=own)
        if spots:
            px, py, s_ = spots[0]
            if "size" in g or abs(s_ - sz) > 1e-6:
                g["size"] = _r(s_, 3)
            if len(spots) == 2:
                self._sym_seat(g, px, py)
                tw = self._sym_twin(g, fx)
                self._sym_legible(g, tw)
                self._sym_pan0(g, fx, mv, plan_mode)
                rep["twins"].append(tw["id"])
            else:
                self._sym_seat(g, xc, py)
                if doodle and not pairable:
                    g["rotation"] = 0.0
                self._sym_legible(g, None)
            rep["moved"].append(iid)
            return
        # c) centrado a la misma altura
        if self.accent_ok(xc, y, sz, fa, fb, shot, t0, t1, own=own):
            self._sym_seat(g, xc)
            if doodle and not pairable:
                g["rotation"] = 0.0
            self._sym_legible(g, None)
            rep["moved"].append(iid)
            return
        # d) no cabe simetrico sin tapar la cara u otra tinta: fuera
        rep["dropped"] += self._sym_drop(g, fx, mv, plan_mode)

    def _sym_center_graphic(self, g: dict) -> bool:
        """Grafico estructural (tarjeta, CTA, etiqueta, chips, rotulo): centrado en x = 0.5 y con
        ancho <= SYM_W; un PiP con x propia pasa al colocador automatico. -> cambio algo."""
        k = g.get("kind")
        ch_ = False
        if k == "media":
            # PiP: al colocador automatico (render.media_auto_place, simetrico y sin tapar la cara)
            if g.get("x") is None or abs(float(g["x"]) - self.XC) <= 1e-6:
                return False
            g["x"] = g["y"] = None
            return True
        if k == "lower_third" and g.get("align") != "center":
            g["align"] = "center"
            ch_ = True
        if g.get("x") is None or abs(float(g["x"]) - self.XC) > 1e-6:
            g["x"] = _r(self.XC, 3)
            ch_ = True
        if g.get("width") and float(g["width"]) > SYM_W + 1e-6:
            g["width"] = _r(SYM_W, 3)
            ch_ = True
        if k in ("card", "cta") and float(g.get("size", 0.0) or 0.0) > SYM_W + 1e-6:
            g["size"] = _r(SYM_W, 3)
            ch_ = True
        return ch_

    def _sym_watermark(self, g: dict, fx: dict, mv, plan_mode: bool, rep: dict) -> None:
        """Marca de agua: centrada arriba y solo en el tramo libre mas largo (>= 2 s) de ese lugar;
        si no hay, fuera."""
        iid = str(g["id"])
        moved = self._sym_center_graphic(g)
        if not moved:
            return
        w, h = socials_size(g, self.style)
        x, y = self.XC, float(g.get("y", SAFE["top"] + h / 2 + 0.008))
        box = (x - w / 2 - 0.02, y - h / 2 - 0.012, x + w / 2 + 0.02, y + h / 2 + 0.012)
        fa, fb = self.f(float(g["start"])), self.f(float(g["end"]))
        busy = [(a, b) for a, b, x0, y0, x1, y1, i_ in self.ink
                if i_ != iid and x0 < box[2] and box[0] < x1 and y0 < box[3] and box[1] < y1]
        busy += [(a, b) for a, b, _i in self.occ["full"]]
        for sh in self.shots:
            fr = self.face_rect(sh, sh["start"], sh["end"])
            if fr is not None and fr[0] < box[2] and box[0] < fr[2] and fr[1] < box[3] and box[1] < fr[3]:
                busy.append((sh["f0"], sh["f1"]))
        free, cur = [], fa
        for a, b in sorted(busy):
            if b <= fa or a >= fb:
                continue
            if a - 0.3 > cur:
                free.append((cur, a - 0.3))
            cur = max(cur, b + 0.3)
        if cur < fb:
            free.append((cur, fb))
        free = [(a, b) for a, b in free if b - a >= 2.0]
        if not free:
            rep["dropped"] += self._sym_drop(g, fx, mv, plan_mode, pair=False)
            return
        a, b = max(free, key=lambda ab: ab[1] - ab[0])
        if a > fa + 1e-6:
            g["start"] = _r(self.o(a))
        if b < fb - 1e-6:
            g["end"] = _r(self.o(b))
        self._sym_set_ink(g, box)
        rep["moved"].append(iid)

    def _sym_cutaway(self, c: dict) -> bool:
        """Escena: texto, tarjeta y graficos embebidos centrados; decorados en par espejo EXACTO
        (mismo glifo y tamano, x y 1-x); la lista (cw_list) centra la caja comun. -> cambio algo."""
        xc = self.XC
        ch_ = False
        t = c.get("text")
        if isinstance(t, dict):
            if t.get("x") is not None and abs(float(t["x"]) - xc) > 1e-6:
                t["x"] = _r(xc, 3)
                ch_ = True
            if t.get("align") in ("left", "right"):
                t["align"] = "center"
                ch_ = True
            if t.get("preset") == "counter" and t.get("anchor") not in (None, "center"):
                t["anchor"] = "center"
                ch_ = True
        cd = c.get("card")
        if isinstance(cd, dict):
            if cd.get("x") is not None and abs(float(cd["x"]) - xc) > 1e-6:
                cd["x"] = _r(xc, 3)
                ch_ = True
            if float(cd.get("size", 0.0) or 0.0) > SYM_W + 1e-6:
                cd["size"] = _r(SYM_W, 3)
                ch_ = True
        gr = c.get("graphics")
        if not isinstance(gr, list) or not gr:
            return ch_
        gr = [g for g in gr if isinstance(g, dict)]
        if str(c.get("id", "")).startswith("cw_list"):
            before = [(g.get("x"), g.get("path_to")) for g in gr]
            self._center_group(gr)
            c["graphics"] = gr
            return ch_ or before != [(g.get("x"), g.get("path_to")) for g in gr]
        out: list[dict] = []
        used: set = set()
        cid = str(c.get("id", "cut"))
        for i, g in enumerate(gr):
            if i in used:
                continue
            k = g.get("kind")
            if g.get("x") is None:
                out.append(g)
                continue
            x = float(g["x"])
            if k not in ("doodle", "icon"):
                ch_ = self._sym_center_graphic(g) or ch_
                out.append(g)
                continue
            if g.get("path_to"):
                if abs(x - xc) > SYM_TOL or abs(float(g["path_to"][0]) - xc) > SYM_TOL:
                    g["x"], g["path_to"] = _r(xc, 3), [_r(xc, 3), g["path_to"][1]]
                    g["bend"] = 0.0
                    ch_ = True
                out.append(g)
                continue
            if abs(x - xc) <= SYM_TOL:
                if abs(x - xc) > 1e-6:
                    g["x"] = _r(xc, 3)
                    ch_ = True
                out.append(g)
                continue
            gid = str(g.get("id") or "")
            j = next((j for j, h in enumerate(gr) if j != i and j not in used and gid and
                      str(h.get("id") or "") == gid + TWIN), None)
            if j is None:
                j = next((j for j, h in enumerate(gr) if j != i and j not in used and h.get("kind") == k
                          and not h.get("path_to") and h.get("x") is not None
                          and abs(float(h["x"]) + x - 2 * xc) <= 0.05 and abs(float(h["x"]) - xc) > SYM_TOL), None)
            pairable = k == "doodle" and (g.get("name") in SYM_GLYPHS or (
                j is not None and gr[j].get("name") in SYM_GLYPHS))
            if not pairable:
                g["x"] = _r(xc, 3)                  # icono / glifo con mano: centrado
                ch_ = True
                out.append(g)
                continue
            base = g if (j is None or x > xc) else gr[j]
            if base.get("name") not in SYM_GLYPHS:
                base["name"] = "sparkle"
            if not base.get("id") or str(base.get("id")).endswith(TWIN):
                base["id"] = f"{cid}:p{i}"
            tw = gr[j] if j is not None and base is g else (g if j is not None else None)
            used |= {i} | ({j} if j is not None else set())
            before = json.dumps(tw, sort_keys=True, default=str) if tw is not None else None
            if tw is None:
                tw = {}
            for kk in self._TWIN_KEYS:
                if kk in base:
                    tw[kk] = copy.deepcopy(base[kk])
                else:
                    tw.pop(kk, None)
            tw.update(id=str(base["id"]) + TWIN, x=_r(2 * xc - float(base["x"]), 3),
                      rotation=_r(-float(base.get("rotation", 0.0) or 0.0), 1) + 0.0)
            if before is None or json.dumps(tw, sort_keys=True, default=str) != before:
                ch_ = True
            out += [base, tw]
        c["graphics"] = out
        return ch_

    def symmetry(self, fx: dict | None = None, mv=None, plan_mode: bool = False) -> dict:
        """Regla de SIMETRIA sobre `fx` (por defecto lo que coloco esta coreografia): texto y
        graficos centrados en x = 0.5 (ancho <= SYM_W), rotulo centrado, escenas con decorados en
        par espejo exacto, acentos centrados o en par espejo (glifos simetricos) y flechas
        verticales centradas; lo que no cabe simetrico sin tapar la cara u otra tinta se quita.
        `mv(item)`: lo que se puede mover (lo del usuario y lo bloqueado nunca). -> resumen
        {moved, twins, dropped} (ids)."""
        fx = self.fx if fx is None else fx
        mv = mv or (lambda it: True)
        rep: dict = {"moved": [], "twins": [], "dropped": []}
        self._sym_old = {}
        for it in fx.get("text") or []:
            if not isinstance(it, dict) or not mv(it):
                continue
            ch_ = False
            if it.get("align") in ("left", "right"):
                it["align"] = "center"
                ch_ = True
            if it.get("preset") == "counter" and it.get("anchor") not in (None, "center"):
                it["anchor"] = "center"
                ch_ = True
            if it.get("x") is None or abs(float(it["x"]) - self.XC) > 1e-6:
                ch_ = True
            if ch_:
                old = next((r[2:6] for r in self.ink if r[6] == it.get("id")), None)
                if old is not None:
                    self._sym_old[str(it["id"])] = tuple(old)
                it["x"] = _r(self.XC, 3)
                self._sym_set_ink(it, self._sym_rect("text", it))
                rep["moved"].append(str(it.get("id")))
        for g in list(fx.get("graphics") or []):
            if not isinstance(g, dict) or not mv(g) or g.get("kind") in ("doodle", "icon", "progress"):
                continue
            if g.get("kind") == "socials" and g.get("variant") == "watermark":
                self._sym_watermark(g, fx, mv, plan_mode, rep)
                continue
            if not self._sym_center_graphic(copy.deepcopy(g)):
                continue
            old = self._sym_rect("graphics", g)
            self._sym_center_graphic(g)
            if old is not None:
                self._sym_old[str(g["id"])] = old
            self._sym_set_ink(g, self._sym_rect("graphics", g))
            rep["moved"].append(str(g.get("id")))
        for c in fx.get("cutaways") or []:
            if isinstance(c, dict) and mv(c) and self._sym_cutaway(c):
                rep["moved"].append(str(c.get("id")))
        acc = [g for g in fx.get("graphics") or [] if isinstance(g, dict) and mv(g)
               and g.get("kind") in ("doodle", "icon") and g.get("enabled", True) is not False]
        for g in sorted(acc, key=lambda g_: (float(g_.get("start", 0)), str(g_.get("id")))):
            if not any(o is g for o in fx.get("graphics") or []):
                continue                            # se fue con su par
            self._sym_accent(g, fx, mv, plan_mode, rep)
        self._sym_old = {}
        return rep

    # ---------------------------------------------------------- recursos del guion (fx.media)
    def media_sfx(self) -> None:
        """Aire suave cuando entra la tarjeta de un recurso del guion (una vez por recurso)."""
        for a, _b, iid in self.media_win:
            if a < 0.05 or a > self.D - 0.2:
                continue
            self.sfx(self.o(a + 0.03), MEDIA_SFX, prio=6, gain=SFX_GAIN.get(MEDIA_SFX, -5) - 2.0, src_id=iid)

    def media_guard(self) -> None:
        """Red de seguridad: lo de la IA que termino encima de un recurso del guion (un CTA o un
        rotulo que se alargo despues de colocarse, una transicion pegada) se quita con sus SFX, y
        ninguna transicion queda en una union con rampa. La barra de progreso y la marca de agua
        (franja 'edge') se quedan."""
        if not self.media_win and not any(sh.get("ramp_join") for sh in self.shots):
            return
        for tr in ("text", "graphics", "cutaways"):
            for it in list(self.fx[tr]):
                if tr == "graphics" and zone_of(tr, it) == "edge":
                    continue
                if not any(x is it for x in self.fx[tr]):
                    continue                      # se fue con su gemelo espejo
                if self.on_media(self.f(float(it["start"])), self.f(float(it["end"]))):
                    self._unplace(it)
        for it in list(self.fx["transitions"]):
            fa = self.f(float(it["at"]))
            if self.on_media(fa - MEDIA_TRANS_PAD, fa + MEDIA_TRANS_PAD) or self.near_ramp(fa, 0.3):
                self._unplace(it)

    # ---------------------------------------------------------- orquestacion
    def run(self) -> dict:
        self.media_sfx()
        self.camera()
        self.transitions()
        self.reserve_final_cta()
        self.hook()
        self.drama()
        if self.prof is not None:
            self.lower_third()           # con perfil: presenta a la persona temprano (antes que el resto)
        self.drop_hit()
        self.enumerations()
        self.data()
        self.flash_scenes()
        if self.prof is None:
            self.lower_third()
        self.scenes()
        self.broll()
        self.sentences()
        self.enum_icons()
        self.final_cta()
        self._fix_lt_bg()
        self.riser()
        self.assign_transitions()
        self.rhythm()
        self.watermark()
        # red de seguridad: todo lo que coloco esta coreografia queda simetrico (lo del usuario y
        # lo bloqueado viven en self.fixed y no se tocan)
        self.symmetry()
        self.media_guard()
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
def _captions_cfg(style: str, pal: dict, ch: "_Choreo | None" = None) -> dict:
    c = STYLES[style]["captions"]
    cfg = {"enabled": True, "preset": c["preset"], "y": c["y"], "size": c["size"], "font": c["font"],
           "color": "#FFFFFF", "accent": pal["accent"], "case": c["case"],
           "max_words": c["max_words"], "hide_during_titles": True}
    yr = list(getattr(ch, "cap_ranges", None) or []) if ch is not None else []
    # tramos sin subtitulos (hide: el CTA final de un primer plano ocupa su franja)
    yr += list(getattr(ch, "cap_hide", None) or []) if ch is not None else []
    if yr:
        cfg["y_ranges"] = sorted(yr, key=lambda r: r["start"])
    return cfg


def _grade_cfg(style: str) -> dict:
    g = STYLES[style]["look"]
    return {"enabled": True, "look": g["look"], "match_shots": True, "vignette": g["vignette"],
            "grain": g["grain"], "sharpen": g["sharpen"]}


def _music_cfg(style: str, script: dict, ch: _Choreo) -> dict:
    m = STYLES[style]["music"]
    # el rubro de la voz (la profesion escrita en el perfil no cambia la pista: misma musica)
    ent_ = script["entities"]
    topic = script.get("topic") or (ent_.get("topic_role") if "topic_role" in ent_ else ent_.get("role")) or ""
    prompt = m["prompt"] + (f", for a video about {strip_accents(topic).lower()}" if topic else "")
    stops = sorted(ch.music_stops, key=lambda s: s["at"])
    swells = []
    for sw in sorted(ch.music_swells, key=lambda s: s["start"]):
        if swells and sw["start"] <= swells[-1]["end"]:
            swells[-1]["end"] = max(swells[-1]["end"], sw["end"])
            swells[-1]["db"] = max(swells[-1]["db"], sw["db"])
        else:
            swells.append(dict(sw))
    mood = m.get("mood")
    humor = sum(1 for s in script.get("sents") or [] if (s.get("info") or {}).get("tone") == "humor")
    if mood == "upbeat" and humor >= 2:
        mood = "playful"          # guion con varios chistes: cama juguetona
    # entrada: 'intro_fade' (fade_in es legado y ya no suaviza nada con el arranque horneado);
    # dinamico/energetico entran con el golpe, elegante/minimal con una entrada suave
    cfg = {"enabled": True, "source": "ai", "track": None, "file": None, "prompt": prompt,
           "mood": mood, "bpm": m["bpm"], "gain_db": m["gain_db"], "duck_db": m["duck_db"],
           "intro_fade": float(m.get("intro_fade", 0.0)), "fade_out": 1.5, "stops": stops,
           "swells": swells, "ending": "hit"}
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
    # las palabras cambiaron (se restauro o se quito una toma repetida): cada frase que sigue
    # IGUAL (mismos tiempos y texto) conserva lo que decidio la IA; solo las tocadas van con
    # heuristicas. Enfasis y titulo son indices de palabra: se trasladan con el corrimiento
    by_t = {} if ok else {(round(float(p_["t0"]), 2), round(float(p_["t1"]), 2), p_.get("text")): p_
                          for p_ in pub.get("sentences") or [] if "t0" in p_ and "t1" in p_}
    for k, s in enumerate(sents):
        s["info"] = _classify(s, words, k, len(sents), anc)
        src = by[s["id"]] if ok else by_t.get((round(s["t0"], 2), round(s["t1"], 2), s["text"]))
        if src is None:
            continue
        shift = 0 if ok else s["w0"] - int(src.get("w0", s["w0"]))
        for key in ("section", "emph", "fx", "icons", "energy", "tone", "title"):
            if key not in src:
                continue
            v = src[key]
            if shift and key in ("emph", "title") and isinstance(v, list):
                v = [int(i) + shift for i in v if isinstance(i, (int, float))]
            s["info"][key] = v
    e2 = dict(plan.get("entities") or {})
    # lo que fijo un perfil (anterior o vigente) no cuenta como dato del usuario: se recalcula
    pk = set(e2.get("profile_keys") or []) if isinstance(e2.get("profile_keys"), list) else set()
    from_profile = e2.get("name_src") == "perfil"
    for k, v in e2.items():
        if k in pk or (from_profile and k in ("name", "name_src", "name_confirmed")):
            continue
        if isinstance(v, (str, bool)):
            ent[k] = v
    if "role" in pk and "topic_role" in e2:
        ent["role"] = str(e2["topic_role"] or "")   # el rubro que venia de la voz
    apply_profile_entities(ent, anc, words, _plan_profile(plan), pub.get("ocr"))
    return {"words": words, "sents": sents, "entities": ent, "anchors": anc,
            "topic": _takes_topic(plan) or pub.get("topic", ""), "llm": pub.get("llm", False),
            "ocr": pub.get("ocr", {})}


def choreograph(plan: dict, shots: list[dict], script: dict, style: str = "dinamico",
                intensity: float = 0.75, seed: int = 7, fixed: dict | None = None,
                windows: list | None = None, rev: int = 0, grid: dict | None = None,
                auto_matte: bool | None = None) -> tuple[dict, _Choreo]:
    """`auto_matte`: la IA propone fondos reemplazados / sujeto a color (default AUTO_MATTE_FX;
    True solo para reproducir un plan que ya los traia)."""
    # el coreografo marca frases como hechas (info._done): cada pasada parte de una copia limpia
    script = dict(script)
    script["sents"] = [dict(s_, info={k: v for k, v in s_["info"].items() if not k.startswith("_")})
                       for s_ in script["sents"]]
    ch = _Choreo(plan, shots, script, style, intensity, seed, fixed=fixed, windows=windows, rev=rev)
    if auto_matte is not None:
        ch.auto_matte = bool(auto_matte)
    ch.grid = grid
    fx = ch.run()
    return fx, ch


def _music_ready() -> bool:
    try:
        from . import music as MU
        return bool(MU.music_ready())
    except Exception:
        return False


CARD_KEEP = 2.0      # s de b-roll que se conservan cuando lleva la tarjeta de ubicacion
PRICE_TAG_HALF = 0.034   # medio alto (fraccion de H) de la etiqueta del servicio sobre el precio
END_KEEP = 2.8       # s del b-roll que queda al FINAL (tarjeta de cierre con el CTA). Las tarjetas
#                      entran tras la transicion de escena (~0.2-0.4 s): con 2.2 s el cierre del
#                      dentista (ubicacion + Seguir + reservas) se veia solo 1.7 s (< END_MIN)
SILENCE_MAX = 2.5    # s: un silencio mayor (tiempo final) se recorta aunque no sea b-roll
LEAD_MAX = 0.4       # s: mas aire que esto antes de la primera palabra se recorta (el hook)
LEAD_KEEP = 0.12     # s que se dejan antes de la primera palabra
# interjecciones de arranque que se recortan al inicio del video (hasta 3 palabras seguidas)
LEAD_INTERJ = {"ok", "okay", "bueno", "eh", "ehh", "mmm", "pues", "gente", "entonces", "listo"}


def propose_trims(plan: dict, shots: list[dict], words: list[dict], keep: float = 1.6,
                  tail: float = 1.7, hold: dict | None = None, end_keep: float = END_KEEP) -> list[dict]:
    """Recortes sugeridos (cuts con src "ai") para que no haya aire muerto:
      - de un b-roll sin voz se conservan `keep` s (el inicio: la revelacion; `hold[k]` para la
        toma k, p. ej. la que lleva la tarjeta de ubicacion) y se quita el silencio que lo rodea
        (deja 0.3 s tras la ultima palabra y 0.25 s antes de la siguiente);
      - un b-roll DESPUES de la ultima palabra (el usuario lo subio al final) es la tarjeta de
        cierre: conserva >= `end_keep` s y la cola nunca se lo come;
      - silencios de mas de SILENCE_MAX s entre palabras (tiempo final) se acortan;
      - cola: quedan `tail` s despues de la ultima palabra (el remate del CTA) o el b-roll final.
    Nunca borra mas que (duracion - keep) de un clip sin voz. No propone nada si el plan ya
    trae recortes de la IA (el usuario pudo haberlos desactivado)."""
    if any(isinstance(c, dict) and c.get("src") == "ai" for c in plan.get("cuts") or []):
        return []
    tl = _timeline(plan)
    out: list[dict] = []
    hold = hold or {}

    def add(a, b, reason):
        if b - a < 0.25:
            return
        tl_now = _timeline(dict(plan, cuts=list(plan.get("cuts") or []) + out)) if out else tl
        if tl_now.remap_clamped(b) - tl_now.remap_clamped(a) < 0.25:        # ya cortado
            return
        out.append({"start": _r(a), "end": _r(b), "enabled": True, "src": "ai",
                    "id": f"cut_ai_{len(out) + 1}", "reason": reason})

    last = max((w["t1"] for w in words), default=None)
    # inicio: el hook arranca con la voz (aire muerto al principio = el scroll se va). Solo si
    # el video abre a camara (una toma de producto/fachada al inicio es intencional)
    first = min(words, key=lambda w: w["t0"]) if words else None
    if first is not None:
        fk = tl.remap_clamped(first["t0"])
        s0 = next((s for s in shots if float(s["start"]) - 1e-6 <= first["t0"] < float(s["end"])), None)
        opens_talking = s0 is not None and s0.get("use", "a_camara") == "a_camara" and \
            tl.remap_clamped(float(s0["start"])) <= 0.05
        # muletilla de arranque ('Ok gente, aqui estamos...', 'Bueno, ...'): un editor la corta y
        # el video abre con la primera idea (solo si hay un respiro limpio para cortar)
        ws0 = sorted(words, key=lambda w: w["t0"])
        j = 0
        while j < min(3, len(ws0) - 1) and ws0[j]["n"] in LEAD_INTERJ:
            j += 1
        if opens_talking and 0 < j < len(ws0) and ws0[j]["t0"] - ws0[j - 1]["t1"] >= 0.15 and \
                tl.remap_clamped(ws0[j]["t0"]) < 4.0:
            add(tl.inverse(0.0), ws0[j]["t0"] - 0.1, "muletilla al inicio")
        elif opens_talking and fk > LEAD_MAX:
            add(tl.inverse(0.0), first["t0"] - LEAD_KEEP, "silencio al inicio")
    broll_keep: dict[int, float] = {}
    for k, s in enumerate(shots):
        if s.get("use", "a_camara") == "a_camara":
            continue
        a, b = float(s["start"]), float(s["end"])
        if any(w["t1"] > a + 0.05 and w["t0"] < b - 0.05 for w in words):
            continue
        kb = float(hold.get(k, keep))
        if last is None or a >= last - 0.05:
            kb = max(kb, end_keep)             # cierre sobre el b-roll final
        broll_keep[k] = kb
        fa, fb = tl.remap_clamped(a), tl.remap_clamped(b)
        if fb - fa > kb + 0.4:
            add(tl.inverse(fa + kb), b, "b-roll mudo largo")
        prev = [w for w in words if w["t1"] <= a + 0.05]
        if prev and fa - tl.remap_clamped(prev[-1]["t1"]) > 0.55:
            add(prev[-1]["t1"] + 0.3, a, "silencio antes del b-roll")
        nxt = [w for w in words if w["t0"] >= b - 0.05]
        if nxt and tl.remap_clamped(nxt[0]["t0"]) - fb > 0.6:
            add(b, nxt[0]["t0"] - 0.25, "silencio despues del b-roll")
    # silencios largos entre palabras que no son b-roll (ya con los recortes de arriba)
    ws = sorted(words, key=lambda w: w["t0"])
    tl2 = _timeline(dict(plan, cuts=list(plan.get("cuts") or []) + out))
    for w, nx in zip(ws, ws[1:]):
        if tl2.remap_clamped(nx["t0"]) - tl2.remap_clamped(w["t1"]) <= SILENCE_MAX:
            continue
        if any(k in broll_keep and float(s["start"]) < nx["t0"] and w["t1"] < float(s["end"])
               for k, s in enumerate(shots)):
            continue
        add(w["t1"] + 0.4, nx["t0"] - 0.3, "silencio largo")
    # cola: `tail` s despues de la ultima palabra, pero NUNCA dentro de lo que se conserva de un
    # b-roll que viene despues de la ultima palabra (esa toma es la tarjeta de cierre)
    if last is not None:
        dur = float(tl.duration)
        start = last + tail
        for k, s in enumerate(shots):
            if k in broll_keep and float(s["start"]) >= last - 0.05 and float(s["end"]) > start:
                start = max(start, tl.inverse(tl.remap_clamped(float(s["start"])) + broll_keep[k]))
        if tl.remap_clamped(dur) - tl.remap_clamped(start) > 0.4:
            add(start, dur, "silencio al final")
    return out


def _silence_notes(plan: dict, words: list[dict], limit: float = 2.5) -> list[str]:
    tl = _timeline(plan)
    pts = [0.0] + [x for w in words for x in (tl.remap_clamped(w["t0"]), tl.remap_clamped(w["t1"]))] + \
        [tl.final_duration]
    notes = []
    for k in range(0, len(pts) - 1, 2):
        a, b = pts[k], pts[k + 1]
        # la cola tras la ultima palabra es la tarjeta de cierre (b-roll final de END_KEEP s con
        # musica y CTA), no aire muerto
        lim = max(limit, END_KEEP + 0.5) if k == len(pts) - 2 else limit
        if b - a > lim and a > 0.5:
            notes.append(f"silencio de {b - a:.1f} s en {a:.1f} s (tiempo final)")
    return notes


def music_cfg_final(plan: dict, mcfg: dict) -> tuple[dict, float]:
    """(cfg, duracion) de la musica en TIEMPO FINAL, igual que render._audio la pide a
    music.get_music (stops/swells/drop_at remapeados): asi la llamada del director y la del
    render comparten el cache de la pista."""
    tl = _timeline(plan)
    c = copy.deepcopy(mcfg)
    c["stops"] = [dict(st, at=tl.remap_clamped(float(st["at"]))) for st in c.get("stops") or []
                  if isinstance(st, dict) and st.get("at") is not None]
    sw = []
    for x in c.get("swells") or []:
        try:
            r = tl.map_range(float(x["start"]), float(x["end"]))
        except Exception:
            r = None
        if r:
            sw.append(dict(x, start=r[0], end=r[1]))
    c["swells"] = sw
    if c.get("drop_at") is not None:
        c["drop_at"] = tl.remap_clamped(float(c["drop_at"]))
    return c, float(tl.final_duration)


def music_grid(plan: dict, style: str, seed: int, mcfg: dict, timeout: float = 40.0) -> dict | None:
    """Genera (o toma del cache) la musica real del plan y devuelve su rejilla en tiempo final:
    {bpm, beats, downbeats, drop, source}. None si no hay musica o no termina a tiempo (el hilo
    sigue y deja el cache caliente para el render)."""
    try:
        from . import music as MU
    except Exception:
        return None
    c, dur = music_cfg_final(plan, mcfg)
    box: dict = {}

    def run():
        try:
            box["r"] = MU.get_music(c, dur, style, int(seed))
        except Exception as ex:  # pragma: no cover - la musica nunca tumba al director
            box["err"] = str(ex)
    th = threading.Thread(target=run, name="director-music", daemon=True)
    th.start()
    th.join(max(0.5, timeout))
    r = box.get("r")
    if not r or not r.get("wav") or not r.get("beats"):
        return None
    return {"bpm": float(r.get("bpm") or 0.0), "beats": [float(b) for b in r.get("beats") or []],
            "downbeats": [float(b) for b in r.get("downbeats") or []],
            "drop": (float(r["drop"]) if r.get("drop") is not None else None),
            "source": r.get("source"), "engine": r.get("engine")}


def _music_key(plan: dict, mcfg: dict, style: str, seed: int) -> str:
    """Huella de la PISTA de musica que pedira el render (cfg en tiempo final sin las claves de
    mezcla): si no cambia, la rejilla de beats guardada sigue valiendo (redirect)."""
    c, dur = music_cfg_final(plan, mcfg)
    c = {k: v for k, v in c.items() if k not in ("swells", "gain_db", "duck_db", "fade_in", "intro_fade",
                                                  "locked")}
    return _hkey(c, round(dur, 3), style, int(seed))


def _music_syncable(mcfg: dict | None) -> bool:
    """La rejilla solo sirve si la musica que va a sonar es la pista IA (activa)."""
    m = mcfg or {}
    return m.get("enabled", True) is not False and m.get("source", "ai") == "ai"


def _merge_music(mcfg: dict, music: dict | None) -> dict:
    """cfg de musica del director + lo que eligio el usuario (fuente, prompt, bpm, semilla...);
    los stops/swells/drop los decide la coreografia."""
    if not music:
        return mcfg
    out = dict(mcfg)
    out.update({k: v for k, v in music.items() if k not in ("stops", "swells", "drop_at")})
    if out.get("source") == "none":
        out["enabled"] = False
    return out


def _grid_public(grid: dict, key: str) -> dict:
    return {"bpm": _r(grid.get("bpm") or 0.0, 2), "beats": [_r(b) for b in grid.get("beats") or []],
            "downbeats": [_r(b) for b in grid.get("downbeats") or []],
            "drop": (_r(grid["drop"]) if grid.get("drop") is not None else None), "key": key}


# ---------------------------------------------------------------- perfil de la persona (3.2)
def _plan_profile(plan: dict | None) -> dict | None:
    """Perfil saneado del plan (motion/profile.py) o None."""
    return PR.normalize_profile((plan or {}).get("profile")) if isinstance(plan, dict) else None


def _brand_handle(business: str, handle: str) -> bool:
    """El @usuario es de la marca ('Olfato' / '@olfato.demo', 'Sonrisa Plena' / '@sonrisaplena')."""
    b = norm(business)
    h = norm(handle)
    if len(b) < 3 or not h:
        return False
    if b in h:
        return True
    ws = [norm(w) for w in str(business).split() if len(norm(w)) >= 4]
    return bool(ws) and all(w in h for w in ws[:2])


def _follow_title(prof: dict | None) -> str:
    """Nombre del CTA Seguir con perfil: el de la persona; el del NEGOCIO si la red principal es
    la de la marca (@olfato.demo -> 'Olfato'); sin nombre, el negocio."""
    if not prof:
        return ""
    nm = prof.get("name") or prof.get("business") or ""
    biz = prof.get("business") or ""
    ps = PR.primary_social(prof)
    if biz and ps is not None and _brand_handle(biz, ps.get("handle", "")):
        return biz
    return nm


def _ai_matte_fx(fx: dict | None) -> bool:
    """El plan trae efectos de recorte (bg_replace / color_pop) que propuso la IA."""
    return any(isinstance(e, dict) and e.get("kind") in MATTE_FX and e.get("src", "ai") != "user"
               for e in (fx or {}).get("effects") or [])


# ---- foto y logo de los enlaces del perfil en los items (profile.link_media). La foto y el logo
# ocupan el hueco del icono / las iniciales: la geometria (ancho natural, alto, simetria) no cambia
def _media_item(m: dict | None) -> dict | None:
    """Medio del perfil para un item (sin `src`: el renderer resuelve el sid en media_root)."""
    if not isinstance(m, dict) or not m.get("sid"):
        return None
    out = {k: copy.deepcopy(m[k]) for k in ("sid", "type", "name", "w", "h", "crop", "thumb") if k in m}
    out["type"] = "image"
    return out


def own_icon(platform: str | None) -> bool:
    """La red tiene icono de marca propio en Reelfy (Instagram, TikTok...): su logo no se usa."""
    return str(platform or "") in LM.OWN_ICON


def _link_logo(link: dict | None, platform: str | None = None) -> dict | None:
    plat = platform or (link or {}).get("platform")
    return None if link is None or own_icon(plat) else _media_item(PR.link_media(link, "logo"))


def follow_media(prof: dict | None, platform: str | None = None, handle: str | None = None) -> dict:
    """Foto del CTA Seguir: la que SUBIO el usuario (profile.avatar) manda; si no, la foto del
    enlace de ESA red (Instagram -> su foto de perfil publica); sin foto {} (iniciales). Si la red no
    tiene icono propio y su pagina tiene logo, `logo` (la insignia de la red)."""
    if not prof:
        return {}
    link = PR.link_for(prof, platform, handle) if platform else None
    out: dict = {}
    m = _media_item(prof.get("avatar")) or _media_item(PR.link_media(link, "photo"))
    if m:
        out.update(avatar="image", media=m)
    lg = _link_logo(link, platform)
    if lg:
        out["logo"] = lg
    return out


def book_media(link: dict | None) -> dict:
    """Pastilla de reservas / web: con foto de la pagina -> foto redonda en el hueco del icono y el
    logo como insignia; solo logo -> el logo en su insignia redondeada; nada -> {} (icono Tabler)."""
    out: dict = {}
    ph = _media_item(PR.link_media(link, "photo"))
    if ph:
        out.update(avatar="image", media=ph)
    lg = _link_logo(link)
    if lg:
        out["logo"] = lg
    return out


def card_media(prof: dict | None, platform: str | None) -> dict:
    """Tarjeta que nombra una plataforma del perfil ('En PaseoClub'): `platform` + `logo` (el logo
    reemplaza al icono en su insignia) si la pagina lo tiene. {} si el perfil no tiene ese enlace."""
    link = PR.link_for(prof, platform) if prof else None
    if link is None:
        return {}
    out = {"platform": platform}
    lg = _link_logo(link)
    if lg:
        out["logo"] = lg
    return out


def chip_media(link: dict | None) -> dict:
    """Chip de redes: el logo de la pagina si su plataforma no tiene icono propio."""
    lg = _link_logo(link)
    return {"logo": lg} if lg else {}


def _find_link(prof: dict | None, platform: str | None, display: str | None = None,
               handle: str | None = None) -> dict | None:
    """Enlace del perfil de un item: misma red y mismo @usuario / texto corto si los trae."""
    if not prof or not platform:
        return None
    links = [l for l in PR.all_links(prof) if l.get("platform") == platform]
    if handle:
        return next((l for l in links if str(l.get("handle") or "").lower() == str(handle).lower()), None)
    if display:
        hit = next((l for l in links if l.get("display") == display), None)
        if hit is not None:
            return hit
    return links[0] if links else None


def _patch_item_media(it: dict, prof: dict | None, ours: set[str]) -> bool:
    """Foto/logo del perfil en UN item ya colocado (Seguir, pastilla link_below, tarjeta con
    `platform`, chips; tambien los de una escena). Solo se tocan medios del perfil (`ours`: sids del
    perfil vigente y del anterior) o su ausencia: una imagen elegida a mano y el avatar 'face' se
    respetan; 'Regenerar' que eligio iniciales con la foto disponible sigue con iniciales.
    -> True si cambio."""
    if not isinstance(it, dict):
        return False
    before = json.dumps(it, sort_keys=True, ensure_ascii=False)

    def mine(m):
        return not isinstance(m, dict) or not m.get("sid") or str(m["sid"]) in ours

    def put_photo(want):
        cur = it.get("media")
        if not mine(cur):
            return
        m = want.get("media")
        if m:
            had = isinstance(cur, dict) and bool(cur.get("sid"))
            it["media"] = m
            if not had and it.get("avatar") in (None, "", "initial", "image"):
                it["avatar"] = "image"
        elif isinstance(cur, dict):
            it.pop("media", None)
            if it.get("avatar") == "image":
                it["avatar"] = "initial"

    def put_logo(obj, lg):
        if not mine(obj.get("logo")):
            return
        if lg:
            obj["logo"] = lg
        else:
            obj.pop("logo", None)

    k, v = it.get("kind"), it.get("variant")
    if k == "cta" and v == "follow":
        plat, h = it.get("platform"), it.get("handle")
        if plat and h and _find_link(prof, plat, handle=h) is None and prof is not None \
                and any(l.get("handle") for l in PR.all_links(prof) if l.get("platform") == plat):
            return False                      # Seguir de OTRA cuenta (hecho a mano): no es del perfil
        want = follow_media(prof, plat, h) if prof else {}
        put_photo(want)
        put_logo(it, want.get("logo"))
    elif k == "cta" and v == "link_below":
        if not it.get("platform"):
            return False                      # 'Link abajo' generico (sin enlace del perfil)
        want = book_media(_find_link(prof, it.get("platform"), it.get("display")))
        put_photo(want)
        put_logo(it, want.get("logo"))
    elif k == "card":
        if not it.get("platform") and it.get("src", "ai") == "ai" and prof:
            # tarjeta de un plan anterior que NOMBRA una pagina del perfil ('En PaseoClub'):
            # se etiqueta con su plataforma para que lleve el logo (como las nuevas del director)
            ws = [norm(w) for w in " ".join(str(it.get(f) or "") for f in
                                            ("title", "subtitle", "value")).split()]
            seen = set(ws) | {a + b for a, b in zip(ws, ws[1:])}      # 'Paseo Club' = 'PaseoClub'
            for l in PR.all_links(prof):
                lab = norm(str((PR.PLATFORMS.get(l.get("platform")) or {}).get("label") or ""))
                if len(lab) >= 4 and lab in seen:
                    it["platform"] = l["platform"]
                    break
        if it.get("platform"):
            put_logo(it, _link_logo(_find_link(prof, it.get("platform"), it.get("display"))))
    elif k == "socials":
        for c in it.get("links") or []:
            if isinstance(c, dict) and c.get("platform") and not own_icon(c.get("platform")):
                put_logo(c, _link_logo(_find_link(prof, c["platform"], c.get("text"))))
    return json.dumps(it, sort_keys=True, ensure_ascii=False) != before


def apply_profile_media(plan: dict, profile: dict | None = None, old: dict | None = None
                        ) -> tuple[dict, list[str]]:
    """Pone / actualiza / quita la foto y el logo de los enlaces del perfil en lo que YA esta en el
    plan (Seguir, pastilla de reservas, tarjetas con `platform`, chips, y los de las escenas) sin
    re-dirigir ni mover nada: mismos ids, tiempos, posiciones y tamanos (la foto y el logo ocupan el
    hueco del icono / las iniciales). Lo usa el servidor cuando termina la descarga automatica.
    `profile` (None = el del plan) queda en plan.profile; `old` (default: el del plan) = de donde
    venian los medios a reemplazar. -> (plan copia, ids cambiados)."""
    p = copy.deepcopy(plan)
    prev = PR.normalize_profile(old) if old is not None else _plan_profile(plan)
    prof = PR.normalize_profile(profile) if profile is not None else _plan_profile(plan)
    if profile is not None:
        if prof:
            p["profile"] = prof
        else:
            p.pop("profile", None)
    if prof is None and prev is None:
        return p, []
    ours = PR.media_sids(prof) | PR.media_sids(prev)
    fx = p.get("fx") if isinstance(p.get("fx"), dict) else {}
    changed = []
    for it in fx.get("graphics") or []:
        if _patch_item_media(it, prof, ours):
            changed.append(str(it.get("id")))
    for c in fx.get("cutaways") or []:
        if not isinstance(c, dict):
            continue
        hit = False
        for g in c.get("graphics") or []:
            hit = _patch_item_media(g, prof, ours) or hit
        if isinstance(c.get("card"), dict):
            hit = _patch_item_media(c["card"], prof, ours) or hit
        if hit:
            changed.append(str(c.get("id")))
    return p, changed


def _name_anchor(words: list[dict], name: str) -> list[int] | None:
    """[i0, i1] (indices `i` de las palabras) donde la persona DICE el nombre del perfil:
    profile.name_span (dos palabras del nombre seguidas, o una con presentacion antes: 'soy la
    doctora Lucia'; tolera una letra de la transcripcion en palabras largas). i0 incluye el
    honorifico dicho. None si no lo dice ('sonrisa blanca' no es la 'Dra. Blanca Flores')."""
    sp = PR.name_span([str(w.get("text") or w.get("n") or "") for w in words], name)
    if sp is None:
        return None
    return [int(words[sp[0]]["i"]), int(words[sp[1]]["i"])]


def apply_profile_entities(ent: dict, anc: dict, words: list[dict], prof: dict | None,
                           ocr: dict | None = None) -> list[str]:
    """Fija en `ent` lo que dice el perfil (manda sobre lo que se infiere del audio o de un
    letrero) y ancla `anc['name']` donde la persona dice su nombre. El nombre leido en un letrero
    NUNCA se usa si hay perfil. El rubro que venia de la voz queda en `topic_role` (iconos y
    musica no cambian por escribir la profesion). -> claves que fijo el perfil."""
    if not prof:
        return []
    fixed, keys = PR.entities_from(prof)
    if ent.get("name_src") == "letrero" or (ocr or {}).get("name_from_ocr"):
        ent["name"] = ""                      # leido en un letrero: nunca con perfil
        ent.pop("name_src", None)
        ent.pop("name_confirmed", None)
    ent.pop("name_candidate", None)
    if "role" in fixed and "topic_role" not in ent:
        ent["topic_role"] = str(ent.get("role") or "")
    ent.update(fixed)
    if prof.get("name"):
        na = _name_anchor(words, prof["name"])
        if na is not None:
            anc["name"] = na
    ent["profile_keys"] = keys
    return keys


def profile_note(prof: dict | None) -> str | None:
    """Nota del director con lo que se integro del perfil."""
    if not prof:
        return None
    bits = [b for b in (prof.get("name") or prof.get("business"), prof.get("title")) if b]
    ps = PR.primary_social(prof)
    if ps:
        bits.append(f"{ps['label']} {ps['handle']}")
    bk = PR.booking_link(prof)
    if bk:
        lab = PR.link_cta(bk, " ".join(str(prof.get(k) or "") for k in ("business", "title")))
        bits.append(f"{lab} ({bk['display']})")
    return "perfil: " + ", ".join(bits)[:220] if bits else None


def name_usable(ent: dict) -> str:
    """Nombre que se puede publicar en rotulo / CTA: el dicho en el audio o el que el usuario
    confirmo. El leido en un letrero (name_src 'letrero') espera confirmacion en el Studio."""
    nm = str((ent or {}).get("name") or "").strip()
    if not nm:
        return ""
    if (ent or {}).get("name_src") == "letrero" and not (ent or {}).get("name_confirmed"):
        return ""
    return nm


def _broll_hold(shots: list[dict], script: dict, plan: dict, keep: float) -> dict:
    """Tomas sin voz que llevaran la tarjeta de ubicacion (empiezan <= 6 s despues de la frase
    de la direccion): conservan CARD_KEEP s para que la tarjeta se alcance a leer."""
    anc = script.get("anchors") or {}
    i0 = (anc.get("address") or anc.get("landmark") or [None])[0]
    if i0 is None:
        return {}
    words = script["words"]
    sid = words[i0].get("s")
    s_end = next((s["t1"] for s in script["sents"] if s["id"] == sid), words[i0]["t1"])
    tl = _timeline(plan)
    out = {}
    for k, sh in enumerate(shots):
        if sh.get("use", "a_camara") == "a_camara":
            continue
        d = tl.remap_clamped(float(sh["start"])) - tl.remap_clamped(float(s_end))
        if -1.0 <= d <= 6.0:
            out[k] = max(keep, CARD_KEEP)
    return out


def direct(plan: dict, style: str = "dinamico", intensity: float = 0.75, seed: int = 7,
           use_llm: bool = True, on_step: Callable | None = None, trim: bool = True,
           music_sync: bool | None = None, music: dict | None = None) -> dict:
    """Plan v1 (o v2) -> plan v2 con TODA la edicion decidida (copia; no modifica `plan`).
    `trim`: propone recortes (cuts src "ai") de b-roll mudo largo, silencios y cola.
    `music_sync` (default = use_llm con musica IA lista): genera la musica ANTES de terminar la
    coreografia y alinea punches, golpe del drop y SFX a sus beats (el render reusa el cache).
    `music`: musica que eligio el usuario (se mezcla en fx.music ANTES de la rejilla; con otra
    fuente que no sea la IA no se sincroniza)."""
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
    # video/guion largo: presupuesto sub-lineal, guion por tramos desde el segundo 0 y turno FIFO del
    # modelo entre tramos y VLM. Corto: todo como siempre (budget 50 s, una llamada, sin turno)
    budget = director_budget(p["duration"])
    long_script = _n_words(sents) > LLM_SINGLE_MAX
    turn = _ModelTurn() if (long_script or p["duration"] > LONG_S) else None
    vlm_first = (can_llm and turn is None and bool((plan.get("montage") or {}).get("timeline"))
                 and len(plan.get("shots") or []) > 1)
    if vlm_first:
        budget += MONTAGE_EXTRA_S
    # tomas que ya describio el VLM en una direccion anterior (vienen completas en el plan): al
    # re-dirigir se conserva la etiqueta del VLM (no se vuelve 'heuristico' por no repetirlo)
    sh_in = [x for x in plan.get("shots") or [] if isinstance(x, dict)]
    shots_known = bool(sh_in) and all(k in x for x in sh_in for k in ("person", "use", "framing", "face"))

    def _start_llm(fn):
        th = threading.Thread(target=fn, name="director-llm", daemon=True)
        llm_box["th"] = th
        th.start()

    def on_basic(shots_basic, final=False):
        if not can_llm or "th" in llm_box or (vlm_first and not final):
            return
        sb = [dict(x) for x in shots_basic]

        def _run():
            t = _now()
            llm_box["r"] = _llm_script(sents, sb)
            llm_box["ms"] = (_now() - t) * 1000
        _start_llm(_run)

    if can_llm and long_script:
        # el escaneo de un video largo tarda minutos (decodificar 4K) y el guion no lo necesita: los
        # tramos arrancan YA, con las tomas solo si el plan ya las trae descritas
        sk = [dict(x) for x in sh_in] if shots_known else None

        def _run_long():
            t = _now()
            llm_box["r"] = _llm_script_windows(sents, sk, deadline=t_start + budget, box=llm_box, turn=turn)
            llm_box["ms"] = (_now() - t) * 1000
        _start_llm(_run_long)

    _step(on_step, "tomas", 0.0, "detectando tomas")
    t = _now()
    _VLM_STATUS.pop(str(p.get("video")), None)
    shot_st: dict = {}
    shots = analyze_shots(p, use_llm=use_llm, on_step=on_step, deadline=t_start + budget - 8.0,
                          on_basic=on_basic, turn=turn, stats=shot_st)
    vlm_cause = _VLM_STATUS.pop(str(p.get("video")), "")
    on_basic(shots, final=True)
    timings["shots_ms"] = round((_now() - t) * 1000)
    _step(on_step, "guion", 0.0, "entendiendo el guion")
    llm_r = None
    if llm_box.get("th") is not None:
        llm_box["th"].join(timeout=max(1.0, t_start + budget - _now()))
        timings["llm_ms"] = round(llm_box.get("ms", 0))
        llm_r = llm_box.get("r")
        if llm_r is None and llm_box.get("parts") is not None:
            # el guion por tramos no termino a tiempo: se usan los tramos que ya respondieron
            llm_r = merge_windows(list(llm_box["parts"]))
            timings["llm_ms"] = round((_now() - t_start) * 1000)
    if p["duration"] > LONG_S or long_script:
        timings["budget_s"] = round(budget, 1)
        timings.update(shot_st)
        wst = llm_box.get("stat")
        if wst:
            timings.update({"llm_windows": wst["n"], "llm_windows_ok": sum(1 for x in list(llm_box["parts"]) if x),
                            "llm_windows_cached": wst["cached"]})
    t = _now()
    script = analyze_script(p, shots, use_llm=False, llm_result=llm_r)
    notes: list[str] = []
    wst = llm_box.get("stat")
    if wst and script.get("llm"):
        n_ok = sum(1 for x in list(llm_box["parts"]) if x)
        if n_ok < wst["n"]:
            miss = sum(1 for x, w in zip(list(llm_box["parts"]), llm_box["wins"]) if not x for _ in w)
            notes.append(f"guion largo: {n_ok}/{wst['n']} tramos con IA; {miss} frases con heuristicas")
    ent0 = script["entities"]
    if ent0.get("name"):
        ent0["name_src"] = "voz"
    prev_ent = plan.get("entities") if isinstance(plan.get("entities"), dict) else {}
    if prev_ent.get("name") and prev_ent.get("name_src") != "perfil" and (
            prev_ent.get("name_confirmed") or prev_ent.get("name_src") == "usuario"):
        # nombre que el usuario confirmo o escribio en el Studio: manda sobre el analisis
        ent0.update({k: prev_ent[k] for k in ("name", "name_src", "name_confirmed", "name_candidate")
                     if k in prev_ent})
    # perfil de la persona (nombre, profesion, redes): manda sobre todo lo anterior
    prof = _plan_profile(p)
    apply_profile_entities(ent0, script["anchors"], script["words"], prof, script.get("ocr"))
    if prof is None and use_llm and not ent0.get("name") and ent0.get("role") and \
            (script.get("ocr") or {}).get("name_candidates"):
        # letrero-directorio: pregunta dirigida al VLM por LA persona de la misma profesion. Es un
        # candidato: no se publica (rotulo/CTA) hasta que el usuario lo confirme en el Studio
        t_s = _now()
        sn = sign_name(p.get("video"), shots, ent0["role"], deadline=t_start + budget + 12.0)
        timings["sign_ms"] = round((_now() - t_s) * 1000)
        if sn and sn.get("name"):
            ent0.update({"name_candidate": sn["name"], "name_src": "letrero", "name_confirmed": False})
            script["ocr"]["sign_name"] = sn
            notes.append(f"nombre leido en el letrero junto a la profesion ('{sn.get('line', '')}'): "
                         f"{sn['name']} - no se muestra hasta confirmarlo en el Studio")
    keep = STYLES[style]["broll_keep"]
    if trim:
        trims = propose_trims(p, shots, script["words"], keep, tail=STYLES[style].get("tail", 1.7),
                              hold=_broll_hold(shots, script, p, keep))
        if trims:
            p["cuts"] = list(p.get("cuts") or []) + trims
            removed = sum(c["end"] - c["start"] for c in trims)
            notes.append(f"recortes IA propuestos: {len(trims)} ({removed:.1f} s de b-roll/silencio)")
    _step(on_step, "coreografia", 0.0, "coreografiando la edicion")
    old = (plan.get("fx") or {}) if isinstance(plan.get("fx"), dict) else {}
    locked_ids = set((plan.get("director") or {}).get("locked") or [])
    group_ids = _lock_groups(old, locked_ids)      # composiciones que se bloquean juntas
    fixed = {tr: [copy.deepcopy(it) for it in old.get(tr) or [] if isinstance(it, dict) and (
        it.get("locked") or it.get("src") == "user" or it.get("id") in locked_ids or it.get("id") in group_ids)]
        for tr in TRACKS}
    fx, ch = choreograph(p, shots, script, style, intensity, seed, fixed=copy.deepcopy(fixed))
    timings["choreo_ms"] = round((_now() - t) * 1000)
    mcfg = _merge_music(_music_cfg(style, script, ch), music)
    if music_sync is None:
        music_sync = bool(use_llm) and _music_ready()
    grid_pub = None
    if music_sync and not _music_syncable(mcfg):
        music_sync = False
        notes.append("musica elegida por el usuario (no IA): sin sincronia de beats")
    if music_sync:
        _step(on_step, "musica", 0.0, "componiendo la musica")
        t_m = _now()
        grid = music_grid(p, style, seed, mcfg, timeout=max(8.0, t_start + budget + 40.0 - _now()))
        timings["music_ms"] = round((_now() - t_m) * 1000)
        if grid:
            fx, ch = choreograph(p, shots, script, style, intensity, seed, fixed=copy.deepcopy(fixed),
                                 grid=grid)
            mcfg = _merge_music(_music_cfg(style, script, ch), music)
            grid_pub = _grid_public(grid, _music_key(p, mcfg, style, seed))
            notes.append(f"musica sincronizada: {grid['bpm']:.0f} BPM, drop en {grid['drop'] or 0:.2f} s "
                         f"(tiempo final), {len(ch.snapped)} eventos al beat")
    reinked = _reink_orphans(ch, fixed)
    if reinked:
        notes.append(f"tinta ajustada al fondo real (su fondo ya no esta): {', '.join(reinked)[:200]}")
    for tr in TRACKS:
        if fixed[tr]:
            fx[tr] = sorted(fixed[tr] + fx[tr], key=lambda it: (float(it.get("at", it.get("start", 0))), str(it["id"])))
    pal = assets.palette(style)
    fx["captions"] = _captions_cfg(style, pal, ch)
    fx["music"] = mcfg
    fx["grade"] = _grade_cfg(style)
    _keep_media(fx, old)                 # recursos del guion: contenido bloqueado, tal cual
    _keep_accent(fx, old, style)         # el color de acento que eligio el usuario
    p["fx"] = fx
    p["shots"] = [{k: v for k, v in s.items() if k != "vlm"} for s in shots]
    p["entities"] = _entities_public(script["entities"])
    prev = plan.get("director") or {}
    notes += _silence_notes(p, script["words"])
    if profile_note(prof):
        notes.append(profile_note(prof))
    notes += [n for n in dict.fromkeys(ch.prof_notes)]
    if (script.get("ocr") or {}).get("name_candidates") and not p["entities"].get("name_candidate") \
            and not p["entities"].get("name") and prof is None:
        notes.append("nombre leido en letreros (no usado; confirmar en el Studio): "
                     + ", ".join(script["ocr"]["name_candidates"][:3]))
    p["director"] = {"style": style, "intensity": float(intensity), "seed": int(seed),
                     "locked": list(prev.get("locked") or []),
                     "llm": _llm_model() if script.get("llm") else "heuristico",
                     "vlm": _vlm_model() if any(s.get("vlm") for s in shots) else (
                         prev.get("vlm") if shots_known and prev.get("vlm") else "heuristico"),
                     "rev": 0, "script": _script_public(script), "timings": timings,
                     "kit": {k: v for k, v in ch.K.items()}, "notes": notes}
    if grid_pub:
        p["director"]["music_grid"] = grid_pub
    p = _finalize(p)
    if vlm_cause and not shots_known:
        p["director"]["warnings"] = sorted(set(p["director"].get("warnings") or []) |
                                           {f"VLM: {vlm_cause}; esas tomas van con heuristicas"})
    p["director"]["timings"]["total_ms"] = round((_now() - t_start) * 1000)
    _step(on_step, "listo", 1.0, "")
    return p


def _entities_public(e: dict) -> dict:
    ent = {k: e.get(k, "") for k in ("name", "role", "price", "address", "landmark", "cta", "link_label",
                                     "handle")}
    for k in ("schedule", "phone", "name_candidate", "name_src", "business", "handle_platform", "link_display"):
        if e.get(k):
            ent[k] = e[k]
    if "topic_role" in e:
        ent["topic_role"] = str(e["topic_role"] or "")        # rubro de la voz (puede ser vacio)
    if "name_confirmed" in e:
        ent["name_confirmed"] = bool(e["name_confirmed"])
    if e.get("profile_keys"):
        ent["profile_keys"] = [str(k) for k in e["profile_keys"]]
    return ent


# ---------------------------------------------------------------- variantes (Regenerar con IA)
# familias para proponer OTRA version de un item sin cambiar lo que dice ni cuando pasa
# familias por GEOMETRIA: un circulo alrededor de un precio sigue rodeandolo (nunca un
# subrayado que lo tacha); subrayado/ola solo donde ya habia algo bajo el texto
_DOODLE_FAM = [("sparkle", "star4", "star5", "burst"), ("underline", "squiggle"), ("circle_scribble",),
               ("arrow_curve", "arrow_straight"), ("question", "exclaim"), ("heart", "check"),
               ("lightning", "rays", "burst"), ("swirl", "loop")]
_TITLE_VARIANTS = ("stack", "title_pop", "cascade", "serif_elegant", "highlight_marker", "kicker",
                   "script_accent")
# SFX por USO (la variante de un SFX de trazo sigue siendo de trazo; la de un pop, de texto)
SFX_USE = [("draw", ("pen_scribble", "scratch")), ("text", ("pop", "pop_soft", "bubble", "tick", "click")),
           ("hit", ("impact_soft", "impact_boom", "sub_drop", "heartbeat")),
           ("air", ("whoosh_soft", "whoosh_fast", "swoosh_up", "swoosh_down", "whip", "swipe")),
           ("tonal", ("ding", "bell", "chime", "sparkle", "notification", "success")),
           ("money", ("cash", "coin")), ("rise", ("riser_short", "riser_long")),
           ("glitch", ("glitch", "record_stop")), ("paper", ("paper_rip", "scratch")),
           ("camera", ("shutter", "click")), ("typing", ("typing",))]


def _vr(seed: int, rev: int, iid: str, key: str) -> float:
    """Azar de la variante: depende de (semilla del pedido, director.rev, id). rev sube en cada
    re-direccion, asi que 'Regenerar' otra vez con la misma semilla da OTRA propuesta; la misma
    (semilla, rev) repite la misma."""
    return FrameCtx(seed=int(seed) * 1000 + int(rev), style="dinamico").rand(f"var:{iid}:{key}")


def _pick_other(options: list, current, seed: int, rev: int, iid: str, key: str):
    uniq: list = []
    for o in options:
        if o not in uniq:
            uniq.append(o)
    opts = [o for o in uniq if o != current]
    if not opts:
        return current
    return opts[int(_vr(seed, rev, iid, key) * len(opts)) % len(opts)]


def _item_words(it: dict) -> list[str]:
    if it.get("lines"):
        return [str(ln.get("text", "")) for ln in it["lines"] if str(ln.get("text", "")).strip()]
    return [t_ for t_ in str(it.get("text", "")).split("\n") if t_.strip()]


def _item_bg(ch: "_Choreo", it: dict, rect: tuple | None = None) -> dict | None:
    """Fondo real (o sintetico) bajo un item: la franja de su tinta y, si se conoce, su x."""
    t0, t1 = float(it.get("start", 0.0)), float(it.get("end", 0.0))
    shot = ch.shot_at(t0 + 0.02)
    if "_bghero_" in str(it.get("id", "")):
        # pieza de la composicion: vive sobre el fondo sintetico (aunque roce el cabello)
        fa, fb = ch.f(t0), ch.f(t1)
        for a, b, kind in ch.bg_fx:
            if a < fb and fa < b and (min(b, fb) - max(a, fa)) >= 0.4 * max(1e-3, fb - fa):
                rgb = (236, 231, 222) if kind == "light" else (16, 16, 20)
                return {"rgb": rgb, "L": rel_lum(rgb), "sat": 0.05, "src": "fx"}
    if rect is None:
        x, y = float(it.get("x", 0.5)), float(it.get("y", 0.5))
        sz = float(it.get("size", 0.1))
        rect = (x - sz / 2, y - sz / 2 / ch.aspect, x + sz / 2, y + sz / 2 / ch.aspect)
    return ch._bg_at(shot, rect[1], rect[3], t0, t1, rect[0], rect[2])


def _legible(colors: list[str], bg: dict | None, need: float = 3.0) -> list[str]:
    """Colores con contraste >= need contra el fondo (sin fondo conocido: todos)."""
    if bg is None:
        return list(colors)
    return [c for c in colors if contrast(c, bg["rgb"]) >= need]


def _text_emph(fake_lines: list[list[dict]], it: dict) -> set:
    """Palabra protagonista de un titulo existente: la de la linea de mayor escala o la que va
    en mayusculas (no solo accent_words: un stack guarda su protagonista como linea grande)."""
    lines = it.get("lines") or []
    words = [w for row in fake_lines for w in row]
    if lines and len(lines) == len(fake_lines):
        k = max(range(len(lines)), key=lambda i: float(lines[i].get("scale", 1.0)))
        if len(lines) > 1 and float(lines[k].get("scale", 1.0)) > min(float(ln.get("scale", 1.0)) for ln in lines):
            big = [w for w in fake_lines[k] if w["n"] not in STOP]
            if big:
                return {max(big, key=lambda w: len(w["n"]))["i"]}
    up = [w for w in words if len(w["text"]) > 2 and w["text"].isupper() and w["n"] not in STOP]
    if up and len(up) < len(words):
        return {w["i"] for w in up}
    acc = {norm(x) for x in it.get("accent_words") or []}
    em = {w["i"] for w in words if w["n"] in acc}
    if em:
        return em
    cont = [w for w in words if w["n"] not in STOP] or words
    return {max(cont, key=lambda w: len(w["n"]))["i"]}


def _visual_fallback(ch: "_Choreo", it: dict, seed: int, rev: int) -> dict | None:
    """Mismo lugar y preset pero algo que SE VE distinto: color de la tinta (legible contra su
    fondo), fuente del kit o detras/delante. Nunca solo la entrada (queda igual tras entrar)."""
    iid = str(it["id"])
    out = copy.deepcopy(it)
    r = measure_text(it, ch.style)
    bg = _item_bg(ch, it, r) if r is not None else None
    cur = str(it.get("color") or "#FFFFFF").upper()
    pal = ch.pal
    light_bg = bg is not None and contrast("#FFFFFF", bg["rgb"]) < 2.4
    pool = ([pal["dark"], ch.acc_light, mix_hex(pal["accent2"], pal["dark"], 0.35), mix_hex(pal["warm"], pal["dark"], 0.45)]
            if light_bg else ["#FFFFFF", pal["accent"], pal["accent2"], pal["warm"]])
    pool = _legible(pool, bg) or pool
    col = _pick_other([c.upper() for c in pool], cur, seed, rev, iid, "fbcol")
    changed = False
    if col != cur:
        out["color"] = col
        for ln in out.get("lines") or []:
            if ln.get("color"):
                ln["color"] = col
        changed = True
    # fuente del kit (display <-> bold, serif <-> bold): otra voz tipografica, mismo texto
    fonts = [ch.K["display"], ch.K["bold"]] if it.get("font") in (ch.K["display"], ch.K["bold"]) else \
        [ch.K["serif"], ch.K["bold"]] if it.get("font") in (ch.K["serif"],) else []
    if fonts and _vr(seed, rev, iid, "fbfont") < 0.5:
        nf = _pick_other(fonts, it.get("font"), seed, rev, iid, "fbf")
        if nf != it.get("font"):
            out["font"] = nf
            for ln in out.get("lines") or []:
                if ln.get("font") == it.get("font"):
                    ln["font"] = nf
            # la otra fuente puede ser mas ancha: el bloque se ajusta a MAXW (sin bajar del piso)
            size = float(out.get("size", 0.08))
            lines = out.get("lines") or [{"text": t_, "scale": 1.0} for t_ in str(out.get("text", "")).split("\n")]
            widest = max(_text_w(ln.get("font") or nf, size * float(ln.get("scale", 1.0)), str(ln.get("text", "")))
                         for ln in lines)
            if widest > ch.MAXW:
                out["size"] = _r(size * ch.MAXW / widest, 4)
            probe = {k: v for k, v in out.items() if not k.startswith("_")}
            r2 = measure_text(probe, ch.style)
            if float(out["size"]) < ch.min_size(str(out.get("preset"))) * 0.97 or (r2 is not None and (
                    r2[0] < SYM_L - 0.01 or r2[2] > SYM_R + 0.01 or r2[3] > SAFE["bottom"] + 0.01)):
                out["font"], out["size"] = it.get("font"), it.get("size")
                for ln, ln0 in zip(out.get("lines") or [], it.get("lines") or []):
                    ln["font"] = ln0.get("font")
            else:
                changed = True
    out["anim_in"] = _pick_other(["pop", "rise", "word_stagger", "scale_down", "slide_left", "mask"],
                                 it.get("anim_in"), seed, rev, iid, "anim")
    return out if changed else None


def _text_variant(ch: "_Choreo", it: dict, seed: int, rev: int) -> dict | None:
    """Mismo texto y mismo momento con OTRO preset (y zona si se puede): se re-mide y coloca con
    las reglas del coreografo (sin tapar la cara, dentro de SAFE, sin encimar lo demas). La
    palabra protagonista se conserva (la linea grande del stack / la que va en mayusculas)."""
    iid = str(it["id"])
    preset = it.get("preset")
    if preset == "counter":
        # el precio no cambia de lugar (esta compuesto con su etiqueta): otra entrada y otro color
        out = dict(it)
        out["anim_in"] = _pick_other(["pop", "scale_down", "rise"], it.get("anim_in", "pop"), seed, rev, iid, "anim")
        r = measure_text(it, ch.style)
        bg = _item_bg(ch, it, r) if r is not None else None
        cols = _legible(["#FFFFFF", ch.pal["accent"], ch.pal["accent2"], ch.pal["dark"]], bg) or ["#FFFFFF"]
        out["color"] = _pick_other(cols, it.get("color"), seed, rev, iid, "col")
        return out
    if "_bghero_" in iid:
        # pieza de la composicion del fondo reemplazado: se queda en su sitio (la otra pieza se
        # mide contra ella); cambia tinta / fuente
        return _visual_fallback(ch, it, seed, rev)
    lines_txt = _item_words(it)
    if not lines_txt:
        return None
    t0, t1 = float(it["start"]), float(it["end"])
    fa, fb = ch.f(t0), ch.f(t1)
    shot = ch.shot_at(t0 + 0.02)
    fake, blk, k = [], [], 0
    sid = ch._sid_at(t0 + 0.05)
    for ln in lines_txt:
        row = []
        for tok in ln.split():
            w = {"text": tok, "i": -1000 - k, "n": norm(tok), "kw": False, "t0": t0, "t1": t1,
                 "f0": fa, "f1": fb, "s": sid if sid is not None else -1}
            row.append(w)
            fake.append(w)
            k += 1
        if row:
            blk.append(row)
    emph = _text_emph(blk, it)
    for w in fake:
        w["kw"] = w["i"] in emph
    label = ch.kicker_label(fake, use=False) if preset != "kicker" else None
    cands = [p_ for p_ in _TITLE_VARIANTS if ch.preset_ok(p_) and p_ != preset
             and not (p_ == "kicker" and label is None)]
    order = sorted(cands, key=lambda p_: _vr(seed, rev, iid, "p:" + p_))
    cur_zone = zone_of("text", it)
    zones = ["top", "center", "center_low", "low", "under"]
    zones = sorted(zones, key=lambda z_: (z_ == cur_zone, _vr(seed, rev, iid, "z:" + z_)))
    g = ch.geom(shot, t0, t1)
    for p_ in order:
        spec = ch._text_spec(p_, blk, emph, False, emph, label=label if p_ == "kicker" else None)
        pl = ch._place(spec, g, zones, fa, fb, ch.min_size(p_))
        if pl is None:
            continue
        zone, y, size = pl
        spec["size"] = _r(size, 4)
        spec.update({"x": _r(ch._x_for(spec), 3), "y": _r(y, 4)})
        probe = {kk: v for kk, v in spec.items() if not kk.startswith("_")}
        r = measure_text(probe, ch.style)
        core = ch.face_core(shot, t0, t1)
        if r is not None and core is not None and r[0] < core[2] and core[0] < r[2] and r[1] < core[3] \
                and core[1] < r[3]:
            continue
        if r is not None and (r[1] < SAFE["top"] - 0.01 or r[3] > SAFE["bottom"] + 0.01):
            continue
        spec.pop("_lines", None)
        ch._apply_ink(spec, ch._bg_at(shot, r[1], r[3], t0, t1) if r is not None else None)
        spec.pop("_mode", None)
        out = {kk: v for kk, v in it.items() if kk not in ("text", "lines", "box", "stroke", "behind",
                                                           "case", "kicker", "kicker_scale", "rotation",
                                                           "align")}
        out.update({kk: v for kk, v in spec.items() if not kk.startswith("_")})
        if out.get("lines"):
            ch._line_floor(out)
        return out
    # sin otra colocacion posible: mismo lugar con otra tinta / fuente (algo que se vea)
    return _visual_fallback(ch, it, seed, rev)


def _graphic_variant(ch: "_Choreo", it: dict, seed: int, rev: int) -> dict:
    iid = str(it["id"])
    k = it.get("kind")
    out = dict(it)
    pal = ch.pal
    bg = _item_bg(ch, it) if k in ("doodle", "icon", "sticker_text") else None
    if k == "doodle":
        fam = next((f for f in _DOODLE_FAM if it.get("name") in f), ("sparkle", "star4", "burst"))
        x_ = float(it["x"]) if it.get("x") is not None else 0.5
        if abs(x_ - 0.5) > SYM_TOL and it.get("name") in SYM_GLYPHS:
            # miembro de un par espejo: sigue siendo un glifo simetrico (su gemelo lo refleja)
            fam = tuple(n for n in fam if n in SYM_GLYPHS) or ("sparkle", "star4", "star5", "burst")
        out["name"] = _pick_other(list(fam), it.get("name"), seed, rev, iid, "name")
        cols = _legible([pal["accent"], "#FFFFFF", pal["accent2"], ch.acc_light, pal["dark"]], bg) or \
            [ch.doodle_color(float(it["start"]), float(it["end"]), float(it.get("x", 0.5)), float(it.get("y", 0.5)))]
        out["color"] = _pick_other(cols, it.get("color"), seed, rev, iid, "col")
        if out["name"] in ("circle_scribble", "underline", "squiggle"):
            # el trazo sigue en su sitio: cambia su caracter (doble trazo / marcador hueco)
            out["double"] = not bool(it.get("double"))
            out["rotation"] = it.get("rotation", 0.0)
        elif out["name"] in SYM_GLYPHS:
            out["rotation"] = _r((_vr(seed, rev, iid, "rot") - 0.5) * 30, 1)
        else:
            # flecha vertical / glifo con mano centrado: derecho (girado cargaria su tinta a un lado)
            out["rotation"] = 0.0
        if out["name"] not in ("underline", "arrow_curve", "arrow_straight"):
            out.pop("path_to", None)
        if _luma(out["color"]) > 0.85 and (bg is None or contrast("#FFFFFF", bg["rgb"]) < 4.5):
            out["border_color"] = "#161616"
        else:
            out.pop("border_color", None)
    elif k == "icon":
        out["badge"] = _pick_other(["circle", "squircle", "none"], it.get("badge", "circle"), seed, rev, iid, "badge")
        out["style"] = _pick_other(["clean", "sketch"], it.get("style", "clean"), seed, rev, iid, "style")
        out["anim_in"] = _pick_other(["pop", "draw", "scale_down", "rise"], it.get("anim_in"), seed, rev, iid, "ain")
        if out["badge"] != "none":
            # el disco se ve contra su fondo; el icono, contra el disco
            bcs = _legible([pal["accent"], pal["dark"], pal["accent2"], "#FFFFFF"], bg) or [pal["dark"], "#FFFFFF"]
            bc = _pick_other(bcs, it.get("badge_color"), seed, rev, iid, "bc")
            out["badge_color"] = bc
            out["color"] = pal["dark"] if contrast(pal["dark"], bc) >= contrast("#FFFFFF", bc) else "#FFFFFF"
        else:
            # icono sin disco: su propio color debe leerse contra el fondo (>= 3:1)
            cs = _legible([pal["accent"], "#FFFFFF", pal["dark"], ch.acc_light], bg)
            if not cs:
                out["badge"] = "circle"
                out["badge_color"] = pal["dark"] if bg is None or _luma(_hexc(bg["rgb"])) > 0.5 else "#FFFFFF"
                out["color"] = "#FFFFFF" if out["badge_color"] == pal["dark"] else pal["dark"]
            else:
                out["color"] = _pick_other(cs, it.get("color"), seed, rev, iid, "c") if len(cs) > 1 or \
                    cs[0] != it.get("color") else cs[0]
    elif k == "card":
        out["theme"] = _pick_other(["light", "dark", "brand"], it.get("theme", "light"), seed, rev, iid, "theme")
        out["anim_in"] = _pick_other(["rise", "pop", "slide_left", "scale_down"], it.get("anim_in"), seed, rev, iid, "ain")
    elif k == "cta":
        if it.get("variant") == "follow":
            cur = it.get("button_color") or it.get("color")
            out["button_color"] = _pick_other([ch.button_color(), pal["dark"], pal["accent2"]], cur, seed, rev, iid, "bc")
            # con foto de perfil (medio propio) se alterna foto / iniciales; sin ella, iniciales / cara
            avs = ["image", "initial"] if isinstance(it.get("media"), dict) else ["initial", "face"]
            out["avatar"] = _pick_other(avs, it.get("avatar", avs[0]), seed, rev, iid, "av")
        else:
            out["color"] = _pick_other([pal["accent"], pal["accent2"], "#FFFFFF"], it.get("color"), seed, rev, iid, "c")
            out["anim_in"] = _pick_other(["pop", "rise", "scale_down"], it.get("anim_in"), seed, rev, iid, "ain")
    elif k == "socials":
        out["theme"] = _pick_other(["light", "dark"], it.get("theme", "light"), seed, rev, iid, "theme")
        out["icon_style"] = _pick_other(["brand", "mono"], it.get("icon_style", "brand"), seed, rev, iid, "ic")
    elif k == "lower_third":
        out["theme"] = _pick_other(["dark", "light"], it.get("theme", "dark"), seed, rev, iid, "theme")
        out["color"] = _pick_other([pal["accent"], pal["accent2"], pal["warm"]], it.get("color"), seed, rev, iid, "c")
    elif k == "sticker_text":
        bgc = _pick_other(_legible([pal["accent"], pal["dark"], "#FFFFFF", pal["accent2"]], bg, 1.6)
                          or [pal["accent"], pal["dark"]], it.get("bg"), seed, rev, iid, "bg")
        out.update({"bg": bgc, "fg": pal["dark"] if _luma(bgc) > 0.55 else "#FFFFFF",
                    "rotation": _r((_vr(seed, rev, iid, "rot") - 0.5) * 10, 1)})
    else:
        out["color"] = _pick_other([pal["accent"], pal["accent2"], "#FFFFFF"], it.get("color"), seed, rev, iid, "c")
    return out


def _cutaway_variant(ch: "_Choreo", it: dict, seed: int, rev: int) -> dict:
    iid = str(it["id"])
    out = copy.deepcopy(it)
    pool = [b for b in list(ch.S["backgrounds"]) + ["grid_warp", "dark_stars", "rays", "gradient", "halftone"]
            if b in BACKGROUNDS and b not in ("blur_self",)]
    out["background"] = _pick_other(pool, it.get("background"), seed, rev, iid, "bg")
    out["anim_in"] = _pick_other(["cut", "zoom", "circle", "wipe", "fade"], it.get("anim_in", "cut"), seed, rev, iid, "ain")
    t = out.get("text")
    if isinstance(t, dict) and t.get("preset") in ("stack", "title_pop"):
        t["anim_in"] = _pick_other(["pop", "word_stagger", "scale_down"], t.get("anim_in"), seed, rev, iid, "tain")
    return out


def _effect_variant(ch: "_Choreo", it: dict, seed: int, rev: int) -> dict:
    iid = str(it["id"])
    k = it.get("kind")
    out = dict(it)
    r = _vr(seed, rev, iid, "amt")
    if k == "zoom":
        a, b = float(it.get("from", 1.0)), float(it.get("to", 1.05))
        out.update({"from": _r(b, 3), "to": _r(a, 3)} if abs(a - b) > 0.01 else {"to": _r(a * 1.06, 3)})
        out["easing"] = _pick_other(["in_out_quad", "out_cubic", "linear"], it.get("easing"), seed, rev, iid, "e")
    elif k == "punch":
        out["amount"] = _r(_clamp(float(it.get("amount", 0.12)) * (1.35 if r < 0.5 else 0.7), 0.04, 0.35), 3)
        out["mode"] = _pick_other(["cut", "spring"], it.get("mode", "cut"), seed, rev, iid, "mode")
        if out["mode"] == "spring":
            out.pop("release", None)
            out["hold"] = 0.35
        else:
            out.update({"release": "cut", "hold": None})
    elif k == "kenburns":
        pan = list(it.get("pan") or [0.03, -0.015])
        out["pan"] = [_r(-float(pan[0]), 3), _r(-float(pan[1]), 3)]
    elif k == "shake":
        out["amp"] = _r(float(it.get("amp", 8.0)) * (1.4 if r < 0.5 else 0.7), 1)
    elif k == "bw":
        out["tone"] = _pick_other(["neutral", "warm", "cool", "sepia"], it.get("tone", "neutral"), seed, rev, iid, "tone")
        out["contrast"] = _r(_clamp(float(it.get("contrast", 1.3)) + (0.15 if r < 0.5 else -0.15), 0.8, 1.8), 2)
    elif k == "bg_replace":
        # mismo tono (claro <-> claro, oscuro <-> oscuro): los titulos encima ya tienen su tinta
        light = it.get("background") in LIGHT_BG
        pool = [b for b in list(ch.S["scenes"].get("bg_replace") or []) + ["grid_warp", "gradient", "rays",
                                                                              "halftone", "dark_stars"]
                if b in BACKGROUNDS and (b in LIGHT_BG) == light]
        out["background"] = _pick_other(pool, it.get("background"), seed, rev, iid, "bg")
        if out["background"] == "paper":
            papers = [["#F3EFE6", "#E4DDCF"], ["#EEF1F4", "#DCE2E9"], ["#F6EEE9", "#E8D9CF"]]
            cur = it.get("bg_colors") if it.get("background") == "paper" else None
            out["bg_colors"] = _pick_other(papers, cur, seed, rev, iid, "paper")
        else:
            out["bg_colors"] = _pick_other([[ch.pal["dark"], ch.pal["accent2"]], [ch.pal["dark"], ch.pal["accent"]],
                                            [ch.pal["dark"], ch.pal["warm"]]], it.get("bg_colors"), seed, rev, iid, "cols")
    elif k == "bg_blur":
        out["radius"] = _r(float(it.get("radius", 16.0)) * (1.5 if r < 0.5 else 0.6), 1)
    elif k == "color_pop":
        out["bg_dark"] = _r(_clamp(float(it.get("bg_dark", 0.22)) + (0.15 if r < 0.5 else -0.15), 0.0, 0.6), 2)
    elif k == "subject_outline":
        out["color"] = _pick_other([ch.pal["accent"], "#FFFFFF", ch.pal["accent2"]], it.get("color"), seed, rev, iid, "c")
    elif k == "rgb_split":
        out["amount"] = _r(float(it.get("amount", 10.0)) * (1.5 if r < 0.5 else 0.6), 1)
    elif k == "flash_frame":
        out["color"] = _pick_other(["#FFFFFF", ch.pal["accent"], ch.pal["warm"]], it.get("color"), seed, rev, iid, "c")
    return out


def _transition_variant(ch: "_Choreo", it: dict, seed: int, rev: int) -> dict:
    """Otro kind de transicion con el MISMO sonido de aire (whip <-> whip, flash <-> light_leak
    <-> film_burn...) para que el SFX emparejado siga siendo verdad; si no hay, otro kind y
    _variants actualiza el SFX de ese golpe."""
    iid = str(it["id"])
    kinds = [k_ for k_, _w in ch.S["transitions"]] + [k_ for k_, _w in ch.S["scene_trans"]] + \
        ["flash", "whip", "zoom_blur", "light_leak", "film_burn", "slide", "glitch"]
    kinds = [k_ for k_ in dict.fromkeys(kinds) if k_ in TRANS_DUR and k_ != "cut"]
    air = TRANS_SFX.get(it.get("kind"))
    same = [k_ for k_ in kinds if TRANS_SFX.get(k_) == air and k_ != it.get("kind")]
    kind = _pick_other(same or kinds, it.get("kind"), seed, rev, iid, "kind")
    out = {kk: v for kk, v in it.items() if kk in ("id", "at", "enabled", "src", "locked", "intensity", "x", "y")}
    TM = _transitions_mod()
    base_dur = TRANS_DUR.get(kind, 0.35)
    if TM is not None:
        base_dur = float((getattr(TM, "DEFAULTS", {}).get(kind) or {}).get("dur") or base_dur)
    out.update({"kind": kind, "dur": _r(max(0.12, base_dur * ch.S["trans_dur"]), 3)})
    if kind in ("whip", "slide", "torn_paper"):
        out["dir"] = _pick_other(["left", "right", "up"], it.get("dir"), seed, rev, iid, "dir")
    if kind == "flash":
        out["color"] = "white"
    elif kind == "light_leak":
        out["color"] = "warm"
    elif kind == "glitch":
        out["color"] = ch.pal["accent"]
    if kind not in ("film_burn", "torn_paper", "circle", "zoom_blur"):
        out.pop("x", None)
        out.pop("y", None)
    return out


def _sfx_family(name: str) -> tuple:
    for _use, names in SFX_USE:
        if name in names:
            return names
    return (name,)


def _sfx_variant(ch: "_Choreo", it: dict, seed: int, rev: int) -> dict:
    """Otro SFX del mismo USO (trazo -> trazo, pop -> texto, golpe -> golpe); si el uso solo tiene
    uno (typing), otra ganancia y otro paneo."""
    iid = str(it["id"])
    fam = [n for n in _sfx_family(str(it.get("name"))) if n in SFX_NAMES]
    out = dict(it)
    out["name"] = _pick_other(fam, it.get("name"), seed, rev, iid, "name")
    g0 = float(it.get("gain_db", SFX_GAIN.get(str(it.get("name")), -6)))
    if out["name"] == it.get("name"):
        out["gain_db"] = _r(_clamp(g0 + (3.0 if _vr(seed, rev, iid, "g") < 0.5 else -3.0), -30, 3), 1)
        out["pan"] = _r(_clamp(-float(it.get("pan", 0.0) or 0.0) + (_vr(seed, rev, iid, "p") - 0.5) * 0.6, -0.6, 0.6), 2)
    else:
        out["gain_db"] = _r(_clamp(g0 - SFX_GAIN.get(str(it.get("name")), -6) + SFX_GAIN.get(out["name"], -6),
                                   -30, 3), 1)
    out.pop("variant", None)
    return out


_ANCHOR_RX = re.compile(r"_([sjt]\d+)[a-z]?\d*(?:_m)?$")     # '_m': gemelo espejo de un par


def _anchor_of(iid: str) -> str | None:
    m = _ANCHOR_RX.search(str(iid))
    return m.group(1) if m else None


def _default_size(g: dict) -> float:
    """`size` con que el renderer dibuja un grafico que no lo trae (graphics.DEFAULTS)."""
    try:
        from . import graphics as G
        return float((G.DEFAULTS.get(g.get("kind")) or {}).get("size") or 0.1)
    except Exception:
        return 0.1


def _accent_sfx_names(g: dict) -> tuple:
    """Nombres del SFX con que entra un acento (_doodle_sfx / icono de accent_for)."""
    if g.get("kind") == "icon":
        return ("bubble", "pop")
    n = g.get("name")
    if n in ("sparkle", "star4", "star5", "burst", "rays"):
        return ("sparkle",)
    if n in ("question", "exclaim", "heart", "check"):
        return ("pop_soft",)
    if n == "lightning":
        return ("impact_soft",)
    return ("pen_scribble",)


def _accent_rect(ch: "_Choreo", g: dict) -> tuple:
    x = float(g["x"]) if g.get("x") is not None else 0.5
    y = float(g["y"]) if g.get("y") is not None else 0.5
    sz = float(g.get("size") or _default_size(g))
    asp = float(g.get("aspect", 1.0) or 1.0) if g.get("name") == "circle_scribble" else 1.0
    hh = (0.014 if g.get("name") in ("underline", "squiggle") else sz * asp / 2 / ch.aspect)
    return (x - sz / 2, y - hh, x + sz / 2, y + hh)


def _reanchor_accents(ch: "_Choreo", fx: dict, old: dict, new: dict, only: set) -> list[str]:
    """Acentos LIGADOS a un titulo que cambio de lugar (garabato / icono de la misma frase cuyo
    rectangulo tocaba la tinta vieja): se mueven con el titulo, SIMETRICOS (subrayado bajo la
    tinta nueva y circulo alrededor, centrados; el resto en un lugar simetrico junto a la tinta
    nueva: par espejo o centrado, con el gemelo '<id>_m' si queda en par). Si ya no caben (banda
    simetrica, cara, otra tinta), se apagan. -> ids tocados."""
    r0, r1 = measure_text(old, ch.style), measure_text(new, ch.style)
    if r0 is None or r1 is None:
        return []
    anc = _anchor_of(old.get("id", ""))
    a, b = float(old["start"]), float(old["end"])
    touched = []
    gl = fx.get("graphics") or []
    for g in list(gl):
        if g.get("id") in only or g.get("kind") not in ("doodle", "icon") or g.get("locked"):
            continue
        gid = str(g.get("id", ""))
        if gid.endswith(TWIN) and any(str(o.get("id")) == gid[:-len(TWIN)] for o in gl):
            continue                                # el gemelo se mueve con su base
        if _anchor_of(gid) != anc or not (float(g["start"]) < b and a < float(g["end"])):
            continue
        gr = _accent_rect(ch, g)
        if not (gr[0] < r0[2] + 0.08 and r0[0] - 0.08 < gr[2] and gr[1] < r0[3] + 0.06 and r0[1] - 0.06 < gr[3]):
            continue
        name = g.get("name")
        w1, h1 = r1[2] - r1[0], r1[3] - r1[1]
        twin = next((o for o in gl if str(o.get("id")) == gid + TWIN), None)
        if name in ("underline", "squiggle"):
            g.update({"x": _r(ch.XC, 3), "y": _r(min(r1[3] + 0.012, SAFE["bottom"] - 0.012), 4),
                      "size": _r(min(ch.MAXW, w1 * 1.02), 3)})
        elif name == "circle_scribble":
            g.update({"x": _r(ch.XC, 3), "y": _r((r1[1] + r1[3]) / 2, 4),
                      "size": _r(min(0.9, w1 * 1.2 + 0.03), 3),
                      "aspect": _r(_clamp((h1 * 1.6 + 0.02) * ch.aspect / max(1e-3, w1 * 1.2 + 0.03), 0.3, 1.0), 3)})
        else:
            # lugar SIMETRICO junto a la tinta nueva (la tinta nueva cuenta como ocupada)
            t0, t1 = float(g["start"]), float(g["end"])
            fa, fb = ch.f(t0), ch.f(t1)
            tmp = (ch.f(float(new["start"])), ch.f(float(new["end"])), *ch._pad_rect(new, r1), "__reanchor__")
            ch.ink.append(tmp)
            spots = ch.accent_spot(ch._pad_rect(new, r1), float(g.get("size", 0.1) or 0.1), fa, fb,
                                   ch.shot_at(t0 + 0.02), t0, t1,
                                   pair=g.get("kind") == "doodle" and name in SYM_GLYPHS,
                                   own={gid, gid + TWIN})
            ch.ink.remove(tmp)
            if not spots:
                g["enabled"] = False
                if twin is not None:
                    twin["enabled"] = False
                touched.append(gid)
                continue
            px, py, s_ = spots[0]
            g.update({"x": _r(px, 3), "y": _r(py, 4), "size": _r(s_, 3)})
            if len(spots) == 2:
                if twin is None:
                    twin = {k: copy.deepcopy(v) for k, v in g.items()}
                    gl.insert(gl.index(g) + 1, twin)
                twin.update({k: copy.deepcopy(g[k]) for k in ch._TWIN_KEYS if k in g})
                twin.update({"id": gid + TWIN, "x": _r(2 * ch.XC - px, 3),
                             "rotation": _r(-float(g.get("rotation", 0.0) or 0.0), 1)})
                touched.append(gid + TWIN)
            else:
                if twin is not None:
                    gl.remove(twin)
                    touched.append(gid + TWIN)
                if g.get("kind") == "doodle" and name not in SYM_GLYPHS:
                    g["rotation"] = 0.0
            touched.append(gid)
            continue
        nr = _accent_rect(ch, g)
        shot = ch.shot_at(float(g["start"]) + 0.02)
        core = ch.face_core(shot, float(g["start"]), float(g["end"]))
        bad = nr[0] < SYM_L - 0.02 or nr[2] > SYM_R + 0.02 or nr[1] < SAFE["top"] - 0.01 \
            or nr[3] > SAFE["bottom"] + 0.005 or (core is not None and nr[0] < core[2] and core[0] < nr[2]
                                                  and nr[1] < core[3] and core[1] < nr[3])
        if bad:
            g["enabled"] = False
        touched.append(gid)
    return touched


def _variants(plan: dict, shots: list[dict], script: dict, style: str, intensity: float, seed: int,
              rev: int, only: set, locked_ids: set) -> tuple[dict, list[str], list[str]]:
    """'Regenerar con IA' de verdad: por cada id pedido, OTRA version con el MISMO id, el mismo
    momento y el mismo contenido (otro preset / kind / posicion / parametros), decidida por
    (semilla, rev). No agrega items; solo mueve los acentos ligados al titulo que cambio de
    lugar y el SFX emparejado de una transicion. Un item bloqueado (locked o director.locked)
    NO se regenera aunque se pida. Un par espejo se regenera junto (pedir el gemelo '<id>_m'
    regenera su base y el gemelo la sigue en espejo; symmetrize al final). -> (fx,
    ids_sin_variante, ids_bloqueados)."""
    old = plan.get("fx") or {}
    fx = {tr: [copy.deepcopy(it) for it in old.get(tr) or [] if isinstance(it, dict)] for tr in TRACKS}
    gids = {str(o.get("id")) for o in fx["graphics"]}
    twins = {str(i) for i in only if str(i).endswith(TWIN) and str(i)[:-len(TWIN)] in gids}
    only = set(only) | {i[:-len(TWIN)] for i in twins}
    others = {tr: [it for it in fx[tr] if it.get("id") not in only] for tr in TRACKS}
    sc = dict(script)
    sc["sents"] = [dict(s_, info={k: v for k, v in s_["info"].items() if not k.startswith("_")})
                   for s_ in script["sents"]]
    ch = _Choreo(plan, shots, sc, style, intensity, seed, fixed=copy.deepcopy(others), rev=rev)
    missing, locked = [], []
    tm = _transitions_mod()
    for tr in TRACKS:
        for k, it in enumerate(fx[tr]):
            iid = it.get("id")
            if iid not in only:
                continue
            if it.get("locked") or iid in locked_ids:
                locked.append(str(iid))
                continue
            if tr == "graphics" and str(iid).endswith(TWIN) and str(iid)[:-len(TWIN)] in gids:
                continue                    # el gemelo sigue a su base (espejo exacto)
            fn = {"text": _text_variant, "graphics": _graphic_variant, "cutaways": _cutaway_variant,
                  "effects": _effect_variant, "transitions": _transition_variant, "sfx": _sfx_variant}[tr]
            try:
                nv = fn(ch, it, seed, rev)
            except Exception:           # pragma: no cover - una variante nunca tumba el plan
                nv = None
            if not nv or {kk: v for kk, v in nv.items()} == it:
                missing.append(str(iid))
                continue
            for kk in ("id", "start", "end", "at", "enabled", "src", "intensity", "z"):
                if kk in it:
                    nv[kk] = it[kk]
            nv["locked"] = False
            fx[tr][k] = nv
            if tr == "text":
                _reanchor_accents(ch, fx, it, nv, only)
            elif tr == "transitions" and TRANS_SFX.get(nv.get("kind")) != TRANS_SFX.get(it.get("kind")):
                # el aire del golpe es parte del mismo evento: sigue al nuevo kind
                hit = float(it.get("at", 0.0))
                if tm is not None and hasattr(tm, "hit_time"):
                    try:
                        hit = float(tm.hit_time(dict(it)))
                    except Exception:
                        pass
                a0, a1 = TRANS_SFX.get(it.get("kind")), TRANS_SFX.get(nv.get("kind"))
                for sx in fx["sfx"]:
                    if a0 and sx.get("name") == a0 and abs(float(sx.get("at", -9)) - hit) < 0.2 \
                            and sx.get("id") not in only and not sx.get("locked"):
                        if a1:
                            sx["name"] = a1
                            sx["gain_db"] = _r(float(SFX_GAIN.get(a1, -6)) + ch.S["sfx_db"], 1)
                        else:
                            sx["enabled"] = False
    return fx, missing, locked


_KEEP_NOTES = ("nombre leido", "musica", "recortes IA")


def _lock_groups(old: dict, locked_ids: set) -> set:
    """Ids que se bloquean JUNTOS con uno bloqueado: las piezas de una composicion (la frase y la
    palabra gigante del fondo reemplazado, tx_bghero_s<N>*, con su fx_bgswap_s<N>) y el fondo
    reemplazado que dio la tinta a un texto bloqueado (sin el, esa tinta oscura pensada para
    papel quedaria sobre el consultorio real)."""
    items = [(tr, it) for tr in TRACKS for it in old.get(tr) or [] if isinstance(it, dict)]
    kept = {str(it.get("id")) for _tr, it in items
            if it.get("locked") or it.get("src") == "user" or it.get("id") in locked_ids}
    out: set = set()
    for _ in range(3):
        grow = set()
        for tr, it in items:
            iid = str(it.get("id"))
            if iid not in kept | out:
                continue
            anc = _anchor_of(iid)
            if iid.startswith(("tx_bghero_", "fx_bgswap_")) and anc:
                grow |= {str(o.get("id")) for _t, o in items
                         if str(o.get("id", "")).startswith(("tx_bghero_", "fx_bgswap_")) and _anchor_of(o.get("id", "")) == anc}
            if tr == "text":
                a, b = float(it.get("start", 0)), float(it.get("end", 0))
                for t2, o in items:
                    if t2 == "effects" and o.get("kind") == "bg_replace":
                        oa, ob = float(o.get("start", 0)), float(o.get("end", 0))
                        if min(b, ob) - max(a, oa) >= 0.5 * max(0.05, b - a):
                            grow.add(str(o.get("id")))
        grow -= kept | out
        if not grow:
            break
        out |= grow
    return out


def _reink_orphans(ch: "_Choreo", fixed: dict) -> list[str]:
    """Un texto que se queda (bloqueado) cuyo fondo sintetico ya no existe y cuya tinta no se lee
    (< 3:1) contra el fondo real: se re-entinta contra el fondo real. -> ids ajustados."""
    out = []
    for it in fixed.get("text") or []:
        if it.get("enabled", True) is False or it.get("src") == "user" or it.get("preset") == "counter":
            continue
        a, b = ch.f(float(it["start"])), ch.f(float(it["end"]))
        if any(min(b, fb) - max(a, fa) >= 0.5 * max(0.05, b - a) for fa, fb, _k in ch.bg_fx):
            continue
        r = measure_text(it, ch.style)
        if r is None:
            continue
        bg = ch._bg_at(ch.shot_at(float(it["start"]) + 0.02), r[1], r[3], float(it["start"]), float(it["end"]))
        col = it.get("color") or "#FFFFFF"
        if bg is None or contrast(col, bg["rgb"]) >= 3.0:
            continue
        spec = copy.deepcopy(it)
        ch._apply_ink(spec, bg)
        spec.pop("_mode", None)
        for k in ("color", "accent", "shadow", "stroke", "box", "lines"):
            if k in spec:
                it[k] = spec[k]
        for ln in it.get("lines") or []:
            if ln.get("color") and contrast(ln["color"], bg["rgb"]) < 3.0:
                ln["color"] = it.get("color")
        out.append(str(it.get("id")))
    return out


def _sym_choreo(p: dict) -> "_Choreo":
    """Coreografo con el plan COMPLETO ya colocado (todo como fijo: ocupacion, acentos y tinta de
    textos) mas la tinta estimada de los graficos, para medir contra lo que hay."""
    fx = p.get("fx") or {}
    d = p.get("director") or {}
    style = d.get("style") if d.get("style") in STYLES else "dinamico"
    shots = [s_ for s_ in p.get("shots") or [] if isinstance(s_, dict) and "start" in s_ and "end" in s_]
    fixed = {tr: [copy.deepcopy(it) for it in fx.get(tr) or [] if isinstance(it, dict)] for tr in TRACKS}
    try:
        pub = d.get("script")
        script = _script_from_public(p, pub, shots) if pub else analyze_script(p, shots, use_llm=False)
        ch = _Choreo(p, shots, script, style, float(d.get("intensity", 0.75)), int(d.get("seed", 7)),
                     fixed=fixed, rev=int(d.get("rev", 0)))
    except Exception:
        # plan sin palabras o sin guion guardado: sin frases (la geometria de tomas y cara basta)
        ch = _Choreo(p, shots, {"words": [], "sents": [], "entities": {}, "anchors": {}}, style,
                     float(d.get("intensity", 0.75)), int(d.get("seed", 7)), fixed=fixed, rev=int(d.get("rev", 0)))
    for g in fx.get("graphics") or []:
        if not isinstance(g, dict) or g.get("enabled") is False or g.get("kind") in ("progress",):
            continue
        if g.get("kind") in ("doodle", "icon") and not g.get("path_to"):
            continue                    # los acentos se miden por su posicion (franja 'accent')
        try:
            r = ch._sym_rect("graphics", g)
        except Exception:
            r = None
        if r is not None:
            ch.ink.append((ch.f(float(g["start"])), ch.f(float(g["end"])), *r, str(g.get("id"))))
    return ch


def symmetrize(plan: dict) -> dict:
    """Regla de SIMETRIA sobre un plan EXISTENTE (sin LLM, determinista; mismos ids, tiempos y
    contenido) -> plan (copia). Solo lo de la IA (src 'ai', sin `locked`, fuera de
    director.locked y de sus composiciones bloqueadas): textos y graficos centrados en x = 0.5 con
    ancho <= SYM_W (cascade centrada, rotulo align 'center', contador anchor 'center'), escenas
    con decorados en par espejo exacto, acentos centrados o en par espejo ('<id>' y '<id>_m')
    junto a su titulo, flechas verticales centradas; lo que no cabe simetrico sin tapar la cara
    (rostro de shots) u otra tinta se quita (con su SFX). Lo del usuario nunca se mueve."""
    p = copy.deepcopy(plan)
    fx = p.get("fx")
    if not isinstance(fx, dict):
        return p
    d = p.get("director") if isinstance(p.get("director"), dict) else {}
    locked_ids = set(d.get("locked") or [])
    frozen = locked_ids | _lock_groups(fx, locked_ids)

    def mv(it) -> bool:
        return isinstance(it, dict) and it.get("src", "ai") == "ai" and not it.get("locked") \
            and str(it.get("id")) not in frozen
    ch = _sym_choreo(p)
    rep = ch.symmetry(fx, mv=mv, plan_mode=True)
    cap = fx.get("captions")
    if isinstance(cap, dict) and "captions" not in locked_ids and cap.get("x") is not None \
            and abs(float(cap["x"]) - 0.5) > 1e-6:
        cap["x"] = 0.5
    if rep["twins"] or rep["dropped"]:
        notes = [n for n in d.get("notes") or [] if not (isinstance(n, str) and n.startswith("simetria:"))]
        parts = []
        if rep["twins"]:
            parts.append(f"{len(rep['twins'])} acento(s) en par espejo")
        if rep["dropped"]:
            parts.append(f"quitado (sin lugar simetrico): {', '.join(rep['dropped'])[:160]}")
        d["notes"] = notes + ["simetria: " + "; ".join(parts)]
        p["director"] = d
    return p


def redirect(plan: dict, style: str | None = None, intensity: float | None = None,
             seed: int | None = None, only_ids: list[str] | None = None,
             use_llm: bool = False, music_sync: bool | None = None, music_timeout: float = 12.0,
             keep_rev: bool = False) -> dict:
    """Re-dirige un plan v2 respetando items `locked` y `src:"user"` (y los ids en
    director.locked). Con `only_ids`, solo re-hace esos items (mismo momento, otra propuesta).
    Tambien acepta en only_ids/locked las claves "captions", "music" y "grade".
    Sincronia con la musica: si la pista no cambia (misma huella que la rejilla guardada en
    director.music_grid) se reusa la rejilla; si cambia y `music_sync` (default: re-direccion
    completa con musica IA lista) se pide la rejilla nueva con `music_timeout` (el render reusa
    la pista). Las notas del nombre del letrero y de la musica se conservan.
    El perfil del plan (`plan.profile`, 3.2) se re-aplica siempre (sin LLM): cambiarlo actualiza
    rotulo, Seguir, reservas y cierre al instante. `keep_rev` (apply_profile): misma revision de
    variantes, asi el resto de la edicion queda igual que la propuesta vigente."""
    p = _base_v2(plan)
    p["duration"] = _plan_duration(p)
    d = dict(p.get("director") or {})
    style = style if style in STYLES else d.get("style", "dinamico")
    style = style if style in STYLES else "dinamico"
    intensity = float(d.get("intensity", 0.75) if intensity is None else intensity)
    seed = int(d.get("seed", 7) if seed is None else seed)
    rev = int(d.get("rev", 0)) + (0 if keep_rev and not only_ids else 1)
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
    if only:
        return _redirect_only(p, d, shots, script, style, intensity, seed, rev, only, locked_ids)
    # aplicar el perfil (keep_rev) no cambia el resto de la edicion: un plan que ya traia fondos
    # reemplazados / sujeto a color de la IA (director anterior a AUTO_MATTE_FX) los conserva;
    # re-dirigir de verdad (otro estilo, regenerar) ya no los propone
    legacy_matte = True if keep_rev and _ai_matte_fx(old) else None
    fixed = {k: [] for k in TRACKS}
    windows = None
    tl = _timeline(p)
    removed_windows = []
    group_ids = _lock_groups(old, locked_ids)
    for tr in TRACKS:
        for it in old.get(tr) or []:
            keep = bool(it.get("locked")) or it.get("src") == "user" or it.get("id") in locked_ids \
                or it.get("id") in group_ids
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
    stored = d.get("music_grid") if isinstance(d.get("music_grid"), dict) else None
    pal = assets.palette(style)

    def music_for(ch_):
        keep_m = "music" in locked_ids or (only and "music" not in only)
        if keep_m and isinstance(old.get("music"), dict):
            return copy.deepcopy(old["music"])
        m = _music_cfg(style, script, ch_)
        um = old.get("music") if isinstance(old.get("music"), dict) else {}
        m.pop("fade_in", None)
        if um and (um.get("source", "ai") != "ai" or um.get("enabled") is False):
            m = _merge_music(m, {k: um[k] for k in ("source", "enabled", "track", "file", "gain_db") if k in um})
        return m

    def run(grid_):
        fx_, ch_ = None, None
        for k in range(4):
            fx_, ch_ = choreograph(copy.deepcopy(p), shots, script, style, intensity,
                                   seed if not only else seed + rev + 97 * k,
                                   fixed=copy.deepcopy(fixed), windows=windows, rev=rev + k, grid=grid_,
                                   auto_matte=legacy_matte)
            if not only:
                break
            new_items = [{kk: v for kk, v in it.items() if kk != "id"} for tr in TRACKS for it in fx_[tr]]
            # "otra propuesta": si todo lo rehecho quedo identico, se intenta con otra semilla
            if not removed or any(it not in removed for it in new_items) or not new_items:
                break
        return fx_, ch_

    fx, ch = run(None)
    mcfg = music_for(ch)
    grid = None
    grid_note = None
    if _music_syncable(mcfg):
        key = _music_key(p, mcfg, style, seed)
        if stored and stored.get("key") == key and stored.get("beats"):
            grid = stored
        else:
            if music_sync is None:
                music_sync = not only and _music_ready()
            if music_sync:
                g2 = music_grid(p, style, seed, mcfg, timeout=music_timeout)
                if g2:
                    grid = _grid_public(g2, key)
                else:
                    grid_note = "musica nueva generandose: sin sincronia de beats en esta propuesta"
        if grid:
            fx, ch = run(grid)
            m2 = music_for(ch)
            if _music_key(p, m2, style, seed) == grid.get("key"):
                mcfg = m2
    reinked = _reink_orphans(ch, fixed)
    for tr in TRACKS:
        fx[tr] = sorted(fixed[tr] + fx[tr], key=lambda it: (float(it.get("at", it.get("start", 0))), str(it["id"])))
    for key_, fn in (("captions", lambda: _captions_cfg(style, pal, ch)),
                     ("grade", lambda: _grade_cfg(style))):
        keep = key_ in locked_ids or (only and key_ not in only)
        fx[key_] = copy.deepcopy(old[key_]) if keep and isinstance(old.get(key_), dict) else fn()
    fx["music"] = mcfg
    _keep_media(fx, old)                 # recursos del guion: contenido bloqueado, tal cual
    _keep_accent(fx, old, style)         # el color de acento que eligio el usuario
    p["fx"] = fx
    p["shots"] = shots
    prev_notes = [n for n in d.get("notes") or [] if isinstance(n, str) and n.startswith(_KEEP_NOTES)
                  and not n.startswith("musica")]
    notes = prev_notes + _silence_notes(p, script["words"])
    pn = profile_note(_plan_profile(p))
    if pn:
        notes.append(pn)
    notes += [n for n in dict.fromkeys(getattr(ch, "prof_notes", []))]
    if reinked:
        notes.append(f"tinta ajustada al fondo real (su fondo ya no esta): {', '.join(reinked)[:200]}")
    if grid:
        notes.append(f"musica sincronizada: {float(grid.get('bpm') or 0):.0f} BPM, drop en "
                     f"{float(grid.get('drop') or 0):.2f} s (tiempo final), {len(ch.snapped)} eventos al beat")
    elif grid_note:
        notes.append(grid_note)
    d.update({"style": style, "intensity": intensity, "seed": seed, "rev": rev,
              "script": _script_public(script), "kit": dict(ch.K), "notes": notes})
    if grid:
        d["music_grid"] = grid
    else:
        d.pop("music_grid", None)
    # entidades: las del plan ya estan en el guion (_script_from_public), salvo lo que fijo un
    # perfil anterior, que se recalcula con el perfil vigente
    p["entities"] = _entities_public(script["entities"])
    p["director"] = d
    return _finalize(p)


def _retext_locked(plan: dict, old: dict | None, new: dict | None) -> list[str]:
    """Items BLOQUEADOS (o del usuario) que muestran datos del perfil anterior: se quedan donde
    estan pero con los datos nuevos (nombre, profesion, red y @usuario, reservas). -> ids."""
    fx = plan.get("fx") or {}
    locked_ids = set((plan.get("director") or {}).get("locked") or [])
    o_name = (old or {}).get("name") or (old or {}).get("business") or ""
    n_name = (new or {}).get("name") or (new or {}).get("business") or ""
    o_fol, n_fol = _follow_title(old), _follow_title(new)
    o_ps, n_ps = PR.primary_social(old), PR.primary_social(new)
    o_bk, n_bk = PR.booking_link(old), PR.booking_link(new)
    o_cta = PR.link_cta(o_bk, " ".join(str((old or {}).get(k) or "") for k in ("business", "title")))
    n_ctx = " ".join(str((new or {}).get(k) or "") for k in ("business", "title"))
    ours = PR.media_sids(old) | PR.media_sids(new)
    out = []
    for it in fx.get("graphics") or []:
        if not isinstance(it, dict) or not (it.get("locked") or it.get("src") == "user" or it.get("id") in locked_ids):
            continue
        k, v = it.get("kind"), it.get("variant")
        before = json.dumps(it, sort_keys=True, ensure_ascii=False)
        if k == "lower_third" and o_name and it.get("title") == o_name and n_name:
            it["title"] = n_name
            o_t, o_b = (old or {}).get("title") or "", (old or {}).get("business") or ""
            n_t, n_b = (new or {}).get("title") or "", (new or {}).get("business") or ""
            if o_t and it.get("subtitle") == f"{o_t} · {o_b}" and n_t and n_b:
                it["subtitle"] = f"{n_t} · {n_b}"
            elif it.get("subtitle") in (o_t, f"{o_t} · {o_b}"):
                it["subtitle"] = n_t or it.get("subtitle")
        elif k == "cta" and v == "follow" and ((o_fol and it.get("title") in (o_fol, o_name)) or
                                              (o_ps and it.get("handle") == o_ps["handle"])):
            if n_fol:
                it["title"] = n_fol
                it["initials"] = PR.initials(n_fol)
            if n_ps:
                it.update(handle=n_ps["handle"], platform=n_ps["platform"])
        elif (k == "cta" and v == "link_below" or k == "card" and v in ("booking", "link")) and o_bk and n_bk \
                and (it.get("label") in (o_bk["label"], o_cta) or it.get("display") == o_bk["display"]):
            lab = n_bk["label"] if k == "card" else PR.link_cta(n_bk, n_ctx)
            it.update(label=lab, display=n_bk["display"], platform=n_bk["platform"])
            if k == "cta":
                it["icon"] = n_bk["icon"]
        # foto y logo del perfil nuevo (foto subida, de su red o de la pagina; sin ellos, iniciales)
        _patch_item_media(it, new, ours)
        if json.dumps(it, sort_keys=True, ensure_ascii=False) != before:
            out.append(str(it.get("id")))
    return out


retext_locked = _retext_locked          # API publica (el servidor la usa en /direct con `profile`)


def apply_profile(plan: dict, profile: dict | None) -> dict:
    """Pone (o quita, con None) el perfil de la persona en el plan y re-dirige al instante SIN
    LLM ni re-analisis (misma revision: el resto de la edicion queda igual) -> plan v2 nuevo.
    Lo usa PUT /profile del servidor cuando el Studio edita el perfil."""
    p = copy.deepcopy(plan)
    old = _plan_profile(p)
    prof = PR.normalize_profile(profile)
    if prof:
        p["profile"] = prof
    else:
        p.pop("profile", None)
    if p.get("version") != 2 or not isinstance(p.get("fx"), dict):
        return direct(p, use_llm=False)
    fixed = _retext_locked(p, old, prof)
    out = redirect(p, music_sync=False, keep_rev=True)
    # foto/logo tambien en lo bloqueado dentro de escenas (la pila de la tarjeta final)
    out, _more = apply_profile_media(out, None, old)
    if fixed:
        out["director"]["notes"] = list(out["director"].get("notes") or []) + [
            f"perfil aplicado tambien a lo bloqueado: {', '.join(fixed)[:200]}"]
    return out


def _redirect_only(p: dict, d: dict, shots: list[dict], script: dict, style: str, intensity: float,
                   seed: int, rev: int, only: set, locked_ids: set) -> dict:
    """redirect(only_ids): otra version REAL de cada item pedido (mismo id y momento) sin tocar
    nada mas. La semilla (la manda el Studio en cada 'Regenerar') decide la variante; la semilla
    del plan (director.seed, la del render) no cambia. captions / music / grade: otro preset de
    subtitulos, otra variacion de la musica (seed) u otro look."""
    old = p.get("fx") or {}
    plan_seed = int(d.get("seed", 7))
    fx, missing, locked = _variants(p, shots, script, style, intensity, seed, rev, only, locked_ids)
    music_new = False
    for key_ in ("captions", "music", "grade"):
        cur = copy.deepcopy(old.get(key_)) if isinstance(old.get(key_), dict) else None
        if cur is None:
            continue
        if key_ in only and key_ in locked_ids:
            locked.append(key_)
        elif key_ in only:
            music_new = music_new or key_ == "music"
            if key_ == "captions":
                cur["preset"] = _pick_other(["clean_bold", "karaoke", "boxed", "bold_caps"], cur.get("preset"),
                                            seed, rev, "captions", "preset")
                cur["case"] = "upper" if cur["preset"] == "bold_caps" else "none"
            elif key_ == "music":
                cur["seed"] = int(_vr(seed, rev, "music", "seed") * 100000)
                cur.pop("fade_in", None)
            else:
                cur["look"] = _pick_other(list(LOOKS[:-1]), cur.get("look"), seed, rev, "grade", "look")
        fx[key_] = cur
    for k_, v_ in old.items():
        if k_ not in fx:
            fx[k_] = copy.deepcopy(v_)
    p["fx"] = fx
    p["shots"] = shots
    notes = [n for n in d.get("notes") or [] if isinstance(n, str)
             and not n.startswith(("sin variante", "bloqueado"))]
    if missing:
        notes.append(f"sin variante para: {', '.join(sorted(missing))[:200]}")
    if locked:
        notes.append(f"bloqueado (no se regenera): {', '.join(sorted(locked))[:200]}")
    if music_new:
        # otra pista: la rejilla de beats guardada ya no es la suya (cortes y SFX al beat viejo)
        d.pop("music_grid", None)
        notes = [n for n in notes if not n.startswith(("musica sincronizada", "musica nueva"))] + \
            ["musica nueva: sin sincronia de beats hasta re-dirigir"]
    d.update({"style": style, "intensity": intensity, "seed": plan_seed, "rev": rev, "notes": notes})
    p["director"] = d
    # lo regenerado (y lo que se movio con ello) tambien cumple la regla de simetria
    return _finalize(symmetrize(p))


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
                    extra = f"{it.get('variant')}: {it.get('title') or it.get('label')}"
                    shown = it.get("display") if it.get("variant") == "link_below" else it.get("handle")
                    if shown:
                        extra += f" | {it.get('platform') or ''} {shown}"
                elif k == "socials":
                    extra = f"{it.get('variant') or 'chips'} {it.get('layout') or ''}: " + ", ".join(
                        f"{l.get('platform')} {l.get('text')}" for l in it.get("links") or [])
                elif k == "lower_third":
                    extra = f"{it.get('title')} | {it.get('subtitle')}"
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
