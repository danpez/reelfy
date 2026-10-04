"""Videos de apoyo (b-roll MUDO): sus tomas, de que trata cada una y si se puede usar.

Un video de la sesion sin voz (un video de presentacion de un producto, una demo grabada sin audio)
nunca es toma: es material de apoyo. Para usarlo en FRAGMENTOS de momentos distintos (scripts/
brollplan.py) hace falta saber, toma por toma:

  1. CORTES (detect_cuts): el video se lee completo a baja resolucion (96 px, hasta 60 cps) y cada
     cuadro se compara con el anterior: diferencia media + histograma HSV + correlacion de la forma.
     Un corte es un pico local (+-6 cuadros) que destaca sobre su vecindad (mediana + 8 MAD y 2.5x la
     mediana; los cuadros repetidos de un video a 24 cps dentro de 60 no cuentan) y pasa un piso
     absoluto. Tomas de menos de MIN_SHOT s (un destello, un fundido) se unen a la siguiente. Una toma
     larga (> LONG_SHOT s) se parte en MOMENTOS de 2-3 s donde mas cambia: en una demo de una sola toma
     cada momento es un fragmento distinto. Una ANIMACION de la interfaz dentro de una toma (un panel que
     se desliza: rachas cortas de cambio entre tramos quietos, bursts) la parte en dos y la animacion no se
     usa: un pedazo nunca empieza a media animacion ni con el contenido de la toma anterior.
  2. CUADROS de cada toma (20 %, 50 % y 80 %, a 640 px; HDR -> SDR como el render) y, por cuadro:
       - personas: PP-HumanSeg (motion/matte.person_prob) en cuadrados a lo largo del lado largo
         (cubren todo el cuadro sin deformar) + caras YuNet. `people` = fraccion maxima del cuadro con
         persona; `face` = la cara mas grande (fraccion del area).
       - textos: lineas de texto por gradiente morfologico (sin OCR): cajas anchas y bajas.
       - sujeto: caja de lo mas llamativo (saliencia de residuo espectral) como respaldo del VLM.
  3. ETIQUETA (VLM local, qwen3.5 via ollama, un cuadro por toma; las tomas que ya se ven como de
     persona por las reglas no se preguntan): subject (screen|phone|ui|character|logo|title|product|
     person|people|place|object), si una persona es el sujeto, textos legibles, que se ve (espanol) y
     palabras clave (espanol), y la caja del sujeto. Sin VLM (no instalado, sin respuesta) la etiqueta
     sale de las reglas (src "reglas").
  4. USO: una toma sirve solo si su sujeto es una pantalla, interfaz, personaje, logo, titulo o
     producto (Kevin: "el chiste es mostrar cosas del producto, no personas random"):
       - nunca si una persona es el sujeto o tapa mucho (cara > FACE_MAX del cuadro, persona >
         PERSON_MAIN), ni si es casi negra o muy corta;
       - "window" (ventana flotante: se ve el cuadro COMPLETO): personas <= WINDOW_PEOPLE (y dentro de su
         acercamiento, si lleva uno: si no, va el cuadro completo);
       - "full" (pantalla completa 9:16: un recorte por toma): existe un recorte (full_crop) que
         contiene al sujeto (entero si es un personaje, logo, titulo o producto) y la mitad de lo que
         se lee o se ve en el, no corta ninguna letra
         de una linea de texto ni una cosa de color (personaje, globo de chat, tarjeta: object_boxes) y
         deja dentro <= FULL_PEOPLE de persona.
     `why` dice en espanol por que no (para el Studio).

Todo queda en cache por archivo (ruta, tamano, fecha, version, modelo del VLM) en WORK/broll/<firma>/
(analysis.json + una miniatura por toma s<i>.jpg); las respuestas del VLM, aparte (WORK/broll/vlm/:
cambiar las reglas no se las vuelve a preguntar). Un analisis en el que el VLM fallo en alguna toma
(`degraded` > 0: ollama cargando, la GPU ocupada con whisper) se guarda pero NO es definitivo: el
siguiente analyze() lo vuelve a ver (las respuestas que si llegaron no se vuelven a preguntar). analyze() es lo unico que llama ffmpeg/onnx/ollama;
lo demas son funciones PURAS con pruebas (tests/test_brollscan.py).

API
  analyze(path, *, use_vlm=True, on_step=None, cancel=None, cache_dir=None) -> catalogo
  cached(path, cache_dir=None) -> catalogo | None      (sin calcular nada)
  thumb_path(path, i, cache_dir=None) -> Path | None    miniatura de la toma i
  detect_cuts(path, info=None) -> (cortes [s], duracion, cps)
  shots_from_cuts(cuts, dur, min_len=MIN_SHOT) -> [(a, b)]
  cut_scores(frames) / pick_cuts(scores, fps) / moments(a, b, change, fps) / full_crop(...) /
  usable(shot) / text_boxes(rgb) / object_boxes(rgb) / saliency_box(rgb) / window_crop(...)   (puras)

Catalogo: {"version", "sig", "name", "dur", "w", "h", "fps", "vlm": modelo | None, "ms",
           "shots": [{"i", "a", "b", "cut_a", "cut_b", "subject", "what", "tags", "text", "people",
                      "face", "person_main", "dark", "luma", "box", "texts", "objs", "full": {"cx", "people",
                      "cut_text", "cut_obj", "inside", "whole", "content", "w"} | None, "win_crop", "ok_window", "ok_full", "why",
                      "src"}]}
  (a/b en s del ARCHIVO; cajas en fracciones [x0, y0, x1, y1] del cuadro).
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

VERSION = 5                # 5: animaciones de interfaz, personas de espaldas, aparato sobre fondo liso, titulos
VLM_VERSION = "p1"        # prompt del VLM (sus respuestas se guardan aparte: cambiar reglas no lo repite)
SCAN_LONG = 96            # px del lado largo en la lectura de cortes
SCAN_FPS_MAX = 60.0
CUT_FLOOR = 0.35          # puntaje minimo de un corte
CUT_MAD = 8.0             # ... sobre la mediana + CUT_MAD * MAD de su vecindad
CUT_RATIO = 2.5           # ... y CUT_RATIO veces la mediana
CUT_WIN = 30              # cuadros de vecindad a cada lado
CUT_PEAK = 6              # pico local en +-CUT_PEAK cuadros
DUP_EPS = 0.004           # puntaje de un cuadro repetido (24 cps dentro de 60)
MIN_SHOT = 0.5            # s: una toma mas corta se une a la siguiente
LONG_SHOT = 4.5           # s: una toma mas larga se parte en momentos
MOMENT = (2.0, 3.0)       # s: largo de un momento
FRAME_W = 640             # px del lado largo de los cuadros de analisis
SAMPLE_AT = (0.12, 0.3, 0.5, 0.7, 0.88)   # (los pedazos empiezan al inicio de su toma: se mira cerca de el)
THUMB_W = 240
# uso
FACE_MAX = 0.03           # cara mas grande (area) desde la que una persona es el sujeto
PERSON_MAIN = 0.35        # fraccion del cuadro con persona desde la que es el sujeto
WINDOW_PEOPLE = 0.10      # persona maxima en el cuadro completo (ventana flotante)
EDGE_PERSON = 0.07        # una mancha de persona que entra por el borde y mide mas (primer plano) ...
TALL_PERSON = 0.45        # ... o una persona de mas de este alto: "se ve una persona" (sin ventana)
WINDOW_FACE = 0.006       # cara maxima (area) en el cuadro completo (ventana flotante)
FULL_PEOPLE = 0.04        # persona maxima dentro del recorte 9:16
FULL_INSIDE = 0.6         # fraccion minima del ancho del sujeto dentro del recorte
FULL_WHOLE = 0.92         # ... de un personaje, logo, titulo o producto: entero (casi) adentro
FULL_CONTENT = 0.5        # fraccion minima de lo que se lee o se ve del sujeto (textos y cosas) dentro del recorte
TEXT_PAD = 0.5            # un borde corta una linea de texto si la cruza o pasa a menos de esto x su alto
OBJ_SIDE = 0.12           # ... y una cosa de color si deja de cada lado >= esto de su ancho
OBJ_S, OBJ_V = 0.45, 0.5  # cosas de color (personajes, globos de chat, tarjetas): saturacion y brillo minimos
OBJ_AREA = (0.002, 0.15)  # ... area (fraccion del cuadro)
OBJ_FILL = 0.45           # ... llenan su caja al menos esto (compactas)
OBJ_RING = 40.0           # ... y se distinguen de lo que las rodea (una tabla de madera iluminada, no)
OBJ_LONG = 6.0            # ... sin ser tiras (una barra de titulo de lado a lado o una tabla: mas de 6:1)
DARK = 0.07               # luminancia media de un cuadro "negro" ...
DARK_PEAK = 0.25          # ... sin nada claro (un titulo claro sobre negro no es "negro")
USE_MIN = 0.6             # s: una toma (o momento) mas corta no se usa
OUT_ASPECT = 9 / 16       # proporcion del recorte de pantalla completa
GOOD = ("screen", "phone", "ui", "character", "logo", "title", "product")
PEOPLE = ("person", "people")
SUBJECTS = GOOD + PEOPLE + ("place", "object")
SUBJECT_ES = {"screen": "pantalla", "phone": "teléfono", "ui": "interfaz", "character": "personaje",
              "logo": "logo", "title": "título", "product": "producto", "person": "persona",
              "people": "personas", "place": "lugar", "object": "objeto"}
# sujetos que la segmentacion de personas confunde (personajes y logos con ojos, juguetes): dentro de su
# caja la mascara no cuenta como persona (las caras si)
DISCOUNT = ("character", "logo", "title", "product")
SAT_S, SAT_V = 0.5, 0.2   # color saturado (personaje, logo) que la segmentacion confunde con persona
SAT_BLOB = 0.5            # fraccion de pixeles saturados desde la que una mancha sin cara es una figura de color
FLAT_STD = 4.0            # desviacion local (7x7, niveles) bajo la que un pixel del color del fondo liso es fondo
PERSON_WORDS = re.compile(r"^\s*(?:(?:un|una|unos|unas|dos|tres|cuatro|varias|varios|algunas|algunos|a|an|the|two|"
                          r"three|several|some)\s+)?(?:(?:young|old|smiling|joven|sonriente)\s+)?"
                          r"(?:mujer|mujeres|hombre|hombres|persona|personas|ni[nñ]a|ni[nñ]o|ni[nñ]os|chica|chico|"
                          r"se[nñ]ora|se[nñ]or|gente|grupo|pareja|joven|j[oó]venes|woman|women|man|men|person|people|girl|boy|guy|couple|group)\b", re.I)
VLM_W = 640
VLM_TIMEOUT = 60.0
VLM_FAILS = 3             # fallos seguidos del VLM tras los que el resto va con reglas
VLM_MAX = 90              # tomas que ve el VLM como maximo (un video de apoyo de 10 min: el resto con reglas)

_VLM_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "enum": list(SUBJECTS)},
        "person_is_main": {"type": "boolean"},
        "text_seen": {"type": "array", "items": {"type": "string"}},
        "what": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "subject_box": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["subject", "person_is_main", "text_seen", "what", "tags", "subject_box"],
}
_VLM_PROMPT = (
    "Frame from a promo or demo video that will be shown as B-roll. Answer ONLY JSON. "
    "subject = the MAIN thing shown: screen (a monitor, TV or tablet showing an app), phone (a phone "
    "showing an app), ui (an app interface filling the frame), character (an animated mascot or cartoon "
    "character), logo, title (big title words), product (a physical product), person (one person is the "
    "main subject), people (several people), place (a room or landscape), object. "
    "person_is_main = a person is the main subject. text_seen = up to 4 biggest legible texts, exact. "
    "what = 5-10 words in Mexican Spanish describing what is seen (no people names). "
    "tags = 3-6 Spanish keywords (nouns) of what is seen. "
    "subject_box = [x0,y0,x1,y1] of the main subject in 0-1000 coordinates.")


# ================================================================ utilidades
def _num(v, d: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return d
    return f if math.isfinite(f) else d


def _r(v: float, k: int = 3) -> float:
    return round(float(v), k)


def _box(b) -> list[float] | None:
    """[x0, y0, x1, y1] saneado (fracciones, x1 > x0, y1 > y0) o None."""
    try:
        x0, y0, x1, y1 = (float(v) for v in b)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    x0, x1 = sorted((min(1.0, max(0.0, x0)), min(1.0, max(0.0, x1))))
    y0, y1 = sorted((min(1.0, max(0.0, y0)), min(1.0, max(0.0, y1))))
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return [_r(x0, 4), _r(y0, 4), _r(x1, 4), _r(y1, 4)]


def _iou(a, b) -> float:
    a, b = _box(a), _box(b)
    if not a or not b:
        return 0.0
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _union(boxes) -> list[float] | None:
    bs = [b for b in (_box(x) for x in boxes or []) if b]
    if not bs:
        return None
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


# ================================================================ cortes (PURAS)
def frame_features(rgb: np.ndarray) -> tuple:
    """Rasgos de un cuadro chico RGB uint8 para comparar con el siguiente: (float32, hist HSV
    normalizado, gris centrado y normalizado)."""
    import cv2
    f = rgb.astype(np.float32)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256]).ravel()
    h /= h.sum() + 1e-9
    g = f.mean(axis=2)
    g = g - g.mean()
    g /= math.sqrt(float((g * g).sum())) + 1e-6
    return f, h, g


def pair_score(fa: tuple, fb: tuple) -> float:
    """Puntaje de cambio entre dos cuadros (0 = iguales): diferencia media (x4 / 255) + distancia
    de histograma (0..1) + (1 - correlacion de la forma)."""
    d = float(np.abs(fb[0] - fa[0]).mean()) / 255.0 * 4.0
    hd = 0.5 * float(np.abs(fb[1] - fa[1]).sum())
    c = 1.0 - float((fb[2] * fa[2]).sum())
    return d + hd + c


def cut_scores(frames) -> np.ndarray:
    """Cuadros (iterable de RGB uint8 chicos) -> puntajes s[k] del cambio del cuadro k-1 al k
    (s[0] = 0)."""
    out, prev = [], None
    for fr in frames:
        f = frame_features(fr)
        out.append(0.0 if prev is None else pair_score(prev, f))
        prev = f
    return np.asarray(out, np.float32)


def pick_cuts(s: np.ndarray, fps: float) -> list[int]:
    """Indices de los cuadros que EMPIEZAN una toma nueva: picos locales (+-CUT_PEAK) sobre el piso
    CUT_FLOOR que destacan sobre su vecindad (+-CUT_WIN cuadros, sin el mismo ni los repetidos):
    > mediana + CUT_MAD * MAD y > CUT_RATIO * mediana."""
    s = np.asarray(s, np.float32)
    n = len(s)
    cuts = []
    for k in range(1, n):
        v = float(s[k])
        if v < CUT_FLOOR:
            continue
        lo, hi = max(1, k - CUT_PEAK), min(n, k + CUT_PEAK + 1)
        if v < float(s[lo:hi].max()) - 1e-9:
            continue
        if cuts and k - cuts[-1] <= CUT_PEAK:
            continue                      # dos picos iguales pegados: uno
        win = np.concatenate([s[max(1, k - CUT_WIN):k], s[k + 1:min(n, k + CUT_WIN + 1)]])
        win = win[win > DUP_EPS]
        if len(win) == 0:
            cuts.append(k)
            continue
        med = float(np.median(win))
        mad = float(np.median(np.abs(win - med))) + 1e-3
        if v > med + CUT_MAD * mad and v > CUT_RATIO * med:
            cuts.append(k)
    return cuts


def shots_from_cuts(cuts, dur: float, min_len: float = MIN_SHOT) -> list[tuple[float, float]]:
    """Tiempos de corte (s) -> tomas [(a, b)] que cubren [0, dur]; una toma de menos de `min_len`
    se une a la siguiente (la ultima, a la anterior)."""
    dur = max(0.0, _num(dur))
    pts = sorted({round(float(c), 4) for c in cuts or [] if 0.0 < _num(c, -1) < dur})
    edges = [0.0] + pts + [dur]
    shots = [[edges[i], edges[i + 1]] for i in range(len(edges) - 1) if edges[i + 1] > edges[i]]
    k = 0
    while k < len(shots) and len(shots) > 1:
        a, b = shots[k]
        if b - a >= min_len:
            k += 1
            continue
        if k + 1 < len(shots):
            shots[k + 1][0] = a
        else:
            shots[k - 1][1] = b
        shots.pop(k)
    return [(round(a, 4), round(b, 4)) for a, b in shots]


BURST_MIN = 0.1           # puntaje por cuadro de una animacion de la interfaz (deslizar un panel)...
BURST_LEN = (0.05, 0.8)   # ...que dura esto (s)...
BURST_QUIET = 0.25        # ...con esto de calma antes y despues (s; una camara que se mueve no tiene calma)
BURST_QUIET_MAX = 0.06    # puntaje medio de la calma
BURST_PEAK = 0.8          # un salto mas fuerte es un cambio de contenido de golpe (un fundido que termina), no un deslizar


def bursts(change, fps: float, a: float, b: float) -> list[tuple[float, float]]:
    """Animaciones de la interfaz DENTRO de una toma (un panel que se desliza y deja ver otro: un menu que da
    paso a un personaje): rachas cortas (BURST_LEN) de cambio >= BURST_MIN por cuadro
    entre tramos quietos, por debajo de un corte. -> [(inicio, fin)] en s. `change`: puntajes de la lectura
    (detect_cuts, cuadro k = k / fps; los cuadros repetidos de un video de 30 cps leido a 60 se ignoran con
    el maximo de cada par)."""
    if change is None or b - a <= 2 * BURST_QUIET:
        return []
    s = np.asarray(change, np.float32)
    w = max(1, int(round(fps / 30.0)))
    k0, k1 = max(1, int(math.ceil(a * fps)) + 1), min(len(s), int(math.floor(b * fps)))
    if k1 - k0 < 4:
        return []
    seg = s[k0:k1]
    if w > 1:
        seg = np.array([seg[max(0, i - w + 1):i + 1].max() for i in range(len(seg))], np.float32)
    hot = seg >= BURST_MIN
    out, i, n = [], 0, len(seg)
    q = int(round(BURST_QUIET * fps))
    while i < n:
        if not hot[i]:
            i += 1
            continue
        j = i
        gap = 2 * w + 1                    # (una pausa de 1-2 cuadros dentro de la animacion no la corta)
        while j < n and (hot[j] or any(hot[j + 1:j + 1 + gap])):
            j += 1
        t0, t1 = (k0 + i) / fps, (k0 + j) / fps
        before = seg[max(0, i - q):i]
        after = seg[j:min(n, j + q)]
        calm = lambda x: len(x) == 0 or float(x.mean()) <= BURST_QUIET_MAX  # noqa: E731
        if BURST_LEN[0] <= t1 - t0 <= BURST_LEN[1] and calm(before) and calm(after) \
                and float(seg[i:j + 1].max()) < BURST_PEAK:
            out.append((round(t0, 4), round(t1, 4)))
        i = j + 1
    return out


def split_bursts(shots, change, fps: float) -> list[tuple[float, float, bool, bool]]:
    """Tomas [(a, b)] -> sub-tomas [(a, b, corte_a, corte_b)] partidas en sus animaciones de interfaz (sin la
    animacion: lo de antes y lo de despues son contenidos distintos). Una sub-toma de menos de MIN_SHOT se
    descarta (el pedazo de menu antes de que se deslice el personaje)."""
    out = []
    for a, b in shots:
        cur = a
        for x, y in bursts(change, fps, a, b):
            if x - cur >= MIN_SHOT:
                out.append((cur, x, True, True))
            cur = y
        if b - cur >= MIN_SHOT or cur == a:
            out.append((cur, b, True, True))
    return out


def moments(a: float, b: float, change=None, fps: float = 30.0) -> list[tuple[float, float]]:
    """Una toma larga (> LONG_SHOT) -> momentos de MOMENT s: cada frontera en el cuadro de MAS cambio
    (`change[k]` = puntaje del cuadro k de la lectura, tiempo k / fps) dentro de su rango permitido;
    sin `change`, partes iguales. Una toma corta queda igual."""
    L = b - a
    if L <= LONG_SHOT:
        return [(a, b)]
    lo_m, hi_m = MOMENT
    n = max(2, int(round(L / ((lo_m + hi_m) / 2))))
    while n > 2 and L / n < lo_m:
        n -= 1
    out, t = [], a
    for j in range(1, n):
        target = a + L * j / n
        lo, hi = max(t + lo_m, target - 0.5), min(b - lo_m * (n - j), target + 0.5)
        cut = target
        if change is not None and hi > lo:
            k0, k1 = int(math.ceil(lo * fps)), int(math.floor(hi * fps))
            seg = np.asarray(change[max(0, k0):max(0, k1 + 1)], np.float32) if k1 >= k0 else np.zeros(0)
            if len(seg) and float(seg.max()) > DUP_EPS * 4:
                cut = (k0 + int(np.argmax(seg))) / fps
        cut = min(max(cut, t + 0.5), b - 0.5)
        out.append((round(t, 4), round(cut, 4)))
        t = cut
    out.append((round(t, 4), round(b, 4)))
    return out


# ================================================================ cuadros (PURAS sobre arreglos)
def text_boxes(rgb: np.ndarray, max_n: int = 24) -> list[list[float]]:
    """Lineas de texto probables (sin OCR) de un cuadro RGB -> cajas [x0, y0, x1, y1] (fracciones):
    gradiente morfologico, umbral, cierre horizontal (une las letras) y componentes anchas y bajas
    con trazo denso."""
    import cv2
    h, w = rgb.shape[:2]
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    grad = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    thr = max(40.0, float(cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0]))
    bw = (grad >= thr).astype(np.uint8) * 255
    kx = max(3, int(round(w / 90)))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1)))
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(bw, 8)
    out = []
    hmin, hmax = max(4, h * 0.012), h * 0.09
    for i in range(1, n):
        x, y, ww, hh, area = (int(v) for v in stats[i])
        if not (hmin <= hh <= hmax) or ww < max(12, w * 0.025) or ww < 1.8 * hh:
            continue
        if area < 0.3 * ww * hh or ww > 0.95 * w:
            continue
        out.append([x / w, y / h, (x + ww) / w, (y + hh) / h])
    out.sort(key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    return [[_r(v, 4) for v in b] for b in out[:max_n]]


def object_boxes(rgb: np.ndarray, max_n: int = 12) -> list[list[float]]:
    """Cosas compactas de color de un cuadro RGB (personajes, globos de chat, tarjetas de una interfaz,
    iconos grandes) -> cajas [x0, y0, x1, y1] (fracciones): manchas saturadas y claras (OBJ_S, OBJ_V) de
    OBJ_AREA del cuadro, que no tocan el borde (una pared), llenan su caja (OBJ_FILL), no son tiras
    (OBJ_LONG: la barra de titulo de una app o una tabla) y se distinguen de lo que las rodea (OBJ_RING:
    una tabla de madera iluminada junto a otra, no)."""
    import cv2
    h, w = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = ((hsv[..., 1] > OBJ_S * 255) & (hsv[..., 2] > OBJ_V * 255)).astype(np.uint8)
    sat = cv2.morphologyEx(sat, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    sat = cv2.morphologyEx(sat, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    n, lab, stats, _c = cv2.connectedComponentsWithStats(sat, 8)
    rgbf = rgb.astype(np.float32)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    out = []
    for i in range(1, n):
        x, y, ww, hh, area = (int(v) for v in stats[i])
        if not OBJ_AREA[0] <= area / float(w * h) <= OBJ_AREA[1] or area < OBJ_FILL * ww * hh:
            continue
        if x <= 1 or y <= 1 or x + ww >= w - 1 or y + hh >= h - 1 or max(ww / hh, hh / ww) > OBJ_LONG:
            continue
        comp = lab == i
        ring = (cv2.dilate(comp.astype(np.uint8), ker) > 0) & ~comp
        if not ring.any() or float(np.abs(rgbf[comp].mean(0) - rgbf[ring].mean(0)).mean()) < OBJ_RING:
            continue
        out.append((area, [x / w, y / h, (x + ww) / w, (y + hh) / h]))
    out.sort(key=lambda r: -r[0])
    return [[_r(v, 4) for v in b] for _a, b in out[:max_n]]


def saliency_box(rgb: np.ndarray) -> list[float] | None:
    """Caja de lo mas llamativo (residuo espectral en gris + contraste de color con el promedio),
    en fracciones; None en un cuadro plano."""
    import cv2
    h, w = rgb.shape[:2]
    sw = 128
    sh = max(8, int(round(sw * h / max(1, w))))
    small = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
    g = small.mean(axis=2)
    F = np.fft.fft2(g)
    la = np.log(np.abs(F) + 1e-6)
    resid = la - cv2.blur(la, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(resid + 1j * np.angle(F)))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), 2.5)
    lab = cv2.cvtColor(small.astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    col = np.sqrt(((lab - lab.reshape(-1, 3).mean(0)) ** 2).sum(axis=2))
    sal = sal / (sal.max() + 1e-9) + col / (col.max() + 1e-9)
    if float(sal.max() - sal.min()) < 1e-6:
        return None
    m = (sal >= np.percentile(sal, 85)).astype(np.uint8)
    n, lab_, stats, _c = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    # componentes grandes (>= 25 % de la mayor): la caja que las une
    areas = stats[1:, cv2.CC_STAT_AREA]
    big = [i + 1 for i, a in enumerate(areas) if a >= 0.25 * areas.max()]
    x0 = min(stats[i, 0] for i in big)
    y0 = min(stats[i, 1] for i in big)
    x1 = max(stats[i, 0] + stats[i, 2] for i in big)
    y1 = max(stats[i, 1] + stats[i, 3] for i in big)
    return _box([x0 / sw, y0 / sh, x1 / sw, y1 / sh])


def person_frac(mask: np.ndarray, x0: float = 0.0, x1: float = 1.0, y0: float = 0.0, y1: float = 1.0) -> float:
    """Fraccion con persona (mask bool HxW) dentro de la ventana [x0, x1] x [y0, y1] (fracciones)."""
    if mask is None or mask.size == 0:
        return 0.0
    h, w = mask.shape[:2]
    a, b = int(round(x0 * w)), max(int(round(x0 * w)) + 1, int(round(x1 * w)))
    c, d = int(round(y0 * h)), max(int(round(y0 * h)) + 1, int(round(y1 * h)))
    sub = mask[c:d, a:b]
    return float(sub.mean()) if sub.size else 0.0


def discount(mask: np.ndarray | None, box, faces=(), rgb: np.ndarray | None = None,
             pad: float = 0.05) -> np.ndarray | None:
    """La mascara de persona sin lo que la segmentacion confunde con personas en una toma del producto
    (personajes, logos y juguetes de colores): una mancha SIN cara que no toca el borde del cuadro (las
    personas reales de primer plano entran por un borde) y es sobre todo de color saturado (SAT_BLOB), o
    cae sobre todo (>= 60 %) en la caja del sujeto SIN proporciones de persona, se quita; de las demas
    manchas sin cara en la caja (o saturadas en promedio) solo se quitan sus pixeles saturados (S > SAT_S,
    V > SAT_V): una persona de espaldas, de cuerpo entero frente a la pantalla, se queda aunque la caja
    holgada del VLM la cubra. Lo de las caras siempre se queda. Sin `rgb`, sin cambios. Sin caja (sin
    VLM) solo cuenta el color saturado de una mancha que no toca el borde."""
    if mask is None or rgb is None or not mask.any():
        return mask
    import cv2
    b = _box(box)
    h, w = mask.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    S = hsv[..., 1].astype(np.float32) / 255.0
    sat = (S > SAT_S) & (hsv[..., 2] > SAT_V * 255)
    inside = np.zeros((h, w), bool)
    if b:
        x0, y0 = int(max(0.0, b[0] - pad) * w), int(max(0.0, b[1] - pad) * h)
        x1, y1 = int(math.ceil(min(1.0, b[2] + pad) * w)), int(math.ceil(min(1.0, b[3] + pad) * h))
        inside[y0:y1, x0:x1] = True
    fc = [(cx * w, cy * h) for cx, cy, _fw, _fh, _s in faces or ()]
    rgbf = rgb.astype(np.float32)
    out = mask.copy()
    # un fondo LISO (pared, fondo de una presentacion): lo que tiene su mismo color no es persona (la
    # segmentacion inventa un cuerpo debajo de una figura redonda)
    bw = max(2, int(0.05 * min(w, h)))
    border = np.concatenate([rgbf[:bw].reshape(-1, 3), rgbf[-bw:].reshape(-1, 3), rgbf[:, :bw].reshape(-1, 3),
                             rgbf[:, -bw:].reshape(-1, 3)])
    bgc = np.median(border, axis=0)
    if float((np.abs(border - bgc).mean(axis=1) < 14.0).mean()) >= 0.8:
        # (y LISO de verdad: la ropa oscura de alguien frente a una pared oscura tiene textura -pliegues,
        # pelo- y se queda; el "cuerpo" inventado sobre el fondo de una presentacion es plano)
        g = rgbf.mean(axis=2)
        k7 = (7, 7)
        loc = np.sqrt(np.maximum(0.0, cv2.blur(g * g, k7) - cv2.blur(g, k7) ** 2))
        flat = (np.abs(rgbf - bgc).mean(axis=2) < 14.0) & (loc < FLAT_STD)
        keep_faces = np.zeros((h, w), bool)
        for cx, cy, fw, fh, _s in faces or ():
            keep_faces[int(max(0, (cy - fh) * h)):int(min(h, (cy + fh) * h)),
                       int(max(0, (cx - fw) * w)):int(min(w, (cx + fw) * w))] = True
        out &= ~(flat & ~keep_faces)
    n, lab, stats, _c = cv2.connectedComponentsWithStats(out.astype(np.uint8), 8)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    for i in range(1, n):
        x, y, ww, hh, area = (int(v) for v in stats[i])
        if any(x <= px <= x + ww and y <= py <= y + hh for px, py in fc):
            continue                                  # con cara: persona
        comp = lab == i
        if area >= 30:
            # una "persona" lisa del MISMO color que lo que la rodea es la pared o el fondo (la segmentacion
            # inventa un cuerpo debajo de una figura redonda); una persona real se distingue de su fondo
            ring = (cv2.dilate(comp.astype(np.uint8), ker) > 0) & ~comp
            if ring.any():
                pc, rc = rgbf[comp], rgbf[ring]
                if float(np.abs(pc.mean(0) - rc.mean(0)).mean()) < 10.0 and float(pc.std(0).mean()) < 12.0:
                    out[comp] = False
                    continue
        touches = x <= 1 or y <= 1 or x + ww >= w - 1 or y + hh >= h - 1
        frac_in = float((comp & inside).sum()) / max(1, area)
        sat_frac = float((comp & sat).sum()) / max(1, area)
        # una figura con proporciones de PERSONA (alta y angosta, o mas alta que TALL_PERSON del cuadro) sin
        # cara visible es alguien de espaldas parado frente a la pantalla: la caja del VLM (holgada) no la
        # borra; solo se quitan sus pixeles de color saturado (un personaje de color si se va entero)
        person_shaped = hh > TALL_PERSON * h or (hh >= 1.5 * ww and hh > 0.2 * h)
        if not touches and sat_frac >= SAT_BLOB:
            out[comp] = False
        elif not touches and frac_in >= 0.6 and not person_shaped:
            out[comp] = False
        elif frac_in >= 0.6 or (not touches and float(S[comp].mean()) > 0.35):
            out[comp & sat] = False
    return out


def intruder(mask: np.ndarray | None) -> bool:
    """Se VE una persona en el cuadro completo: una mancha que entra por un borde y mide mas de
    EDGE_PERSON del cuadro (alguien de espaldas o desenfocado en primer plano) o una dentro del cuadro
    de mas de TALL_PERSON de alto (alguien de cuerpo entero caminando por la escena)."""
    if mask is None or not mask.any():
        return False
    import cv2
    h, w = mask.shape[:2]
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    for i in range(1, n):
        x, y, ww, hh, area = (int(v) for v in stats[i])
        if area < 0.004 * w * h:
            continue
        touches = x <= 1 or y <= 1 or x + ww >= w - 1 or y + hh >= h - 1
        if (area > EDGE_PERSON * w * h) if touches else (hh > TALL_PERSON * h):
            return True
    return False


def _edge_cuts(x0: float, x1: float, boxes, asp: float, kind: str) -> int:
    """Cuantas cajas corta el recorte [x0, x1]: una linea de texto ("text") si un borde la atraviesa o pasa
    a menos de TEXT_PAD x su alto (en pixeles) de ella: ni media letra en la orilla ni un globo de chat
    rebanado junto a su texto; una cosa de color ("obj") si un borde la atraviesa dejando de cada lado
    OBJ_SIDE de su ancho."""
    n = 0
    for tb in boxes or []:
        if kind == "text":
            pad = TEXT_PAD * (tb[3] - tb[1]) / asp
            n += sum(1 for e in (x0, x1) if tb[0] - pad < e < tb[2] + pad)
            continue
        side = OBJ_SIDE * (tb[2] - tb[0])
        n += sum(1 for e in (x0, x1) if tb[0] + side <= e <= tb[2] - side)
    return n


def full_crop(src_aspect: float, subject: list | None, masks=(), texts=(), out_aspect: float = OUT_ASPECT,
              steps: int = 61, objs=(), whole: bool = False, people_ymax: float | None = None) -> dict | None:
    """Mejor recorte de pantalla completa (alto completo, ancho = out_aspect / src_aspect del cuadro) de
    una toma: el centro cx (fraccion del ancho) que deja menos persona dentro (`masks`: mascaras bool de
    los cuadros de la toma), no corta ni roza una linea de texto (`texts`: cajas de los cuadros; _edge_cuts)
    ni una cosa de color (`objs`: personajes,
    globos, tarjetas; object_boxes) y contiene al sujeto (cerca de su centro; ENTERO si `whole` -un
    personaje, logo, titulo o producto- y su caja cabe en el recorte: la caja del VLM es holgada y una mas
    ancha que el recorte no se puede exigir entera) y al menos FULL_CONTENT de lo que se lee o se ve en
    el (textos y cosas dentro de su caja: un recorte que no corta nada porque se fue a la pared tampoco
    sirve). Entre los recortes que cumplen todo gana el de menor costo; si ninguno cumple, el menos malo
    (y usable() lo descarta). `people_ymax` (titulo o logo de un evento): solo cuenta la persona por
    ENCIMA de esa altura (fraccion): las siluetas lejanas del publico abajo del titulo no lo descartan (v6
    uso el titulo de cierre a pantalla completa con ellas).
    -> {"cx", "people", "cut_text", "cut_obj", "inside", "whole", "content", "w"} o None si la fuente ya
    es tan angosta como la salida (cx 0.5, sin recorte horizontal)."""
    ymax = 1.0 if people_ymax is None else min(1.0, max(0.05, float(people_ymax)))
    asp = max(1e-3, _num(src_aspect, 16 / 9))
    cw = out_aspect / asp
    if cw >= 0.999:
        ppl = max([person_frac(m, 0.0, 1.0, 0.0, ymax) * ymax for m in masks if m is not None] + [0.0])
        return {"cx": 0.5, "people": _r(ppl), "cut_text": 0, "cut_obj": 0, "inside": 1.0, "whole": False,
                "content": 1.0, "w": 1.0}
    sb = _box(subject) or [0.3, 0.2, 0.7, 0.8]
    scx, sw_ = (sb[0] + sb[2]) / 2, sb[2] - sb[0]
    whole = bool(whole) and sw_ <= cw
    need = FULL_WHOLE if whole else FULL_INSIDE
    # lo que se lee o se ve del sujeto: textos y cosas con el centro en su caja
    pad = 0.03
    content = [b for b in list(texts or []) + list(objs or [])
               if sb[0] - pad <= (b[0] + b[2]) / 2 <= sb[2] + pad and sb[1] - pad <= (b[1] + b[3]) / 2 <= sb[3] + pad]
    c_area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in content)
    lo, hi = cw / 2, 1 - cw / 2
    rows = []
    for j in range(steps):
        cx = lo + (hi - lo) * j / max(1, steps - 1)
        x0, x1 = cx - cw / 2, cx + cw / 2
        ppl = max([person_frac(m, x0, x1, 0.0, ymax) * ymax for m in masks if m is not None] + [0.0])
        cut = _edge_cuts(x0, x1, texts, asp, "text")
        cob = _edge_cuts(x0, x1, objs, asp, "obj")
        over = max(0.0, min(x1, sb[2]) - max(x0, sb[0]))
        inside = over / max(1e-6, sw_ if whole else min(sw_, cw))
        cov = (sum(max(0.0, min(x1, b[2]) - max(x0, b[0])) * (b[3] - b[1]) for b in content) / c_area
               if c_area > 0 else 1.0)
        cost = 12.0 * ppl + 1.5 * cut + 1.5 * cob + 2.0 * (1.0 - min(1.0, inside)) + 0.6 * abs(cx - scx) \
            + 1.0 * (1.0 - cov)
        ok = ppl <= FULL_PEOPLE and cut == 0 and cob == 0 and inside >= need and cov >= FULL_CONTENT
        rows.append((not ok, inside < need, cost, cx, ppl, cut, cob, inside, cov))
    # lo que cumple todo primero; despues, el sujeto adentro (un recorte vacio no sirve aunque no tenga
    # personas); despues, el costo
    best = min(rows, key=lambda r: r[:3])
    _bad, _out, _c, cx, ppl, cut, cob, inside, cov = best
    return {"cx": _r(cx, 4), "people": _r(ppl), "cut_text": int(cut), "cut_obj": int(cob),
            "inside": _r(min(1.0, inside)), "whole": whole, "content": _r(cov), "w": _r(cw, 4)}


BG_FLAT = 0.75            # fraccion del cuadro FUERA del sujeto con el color del fondo: fondo liso
BG_DIST = 18.0            # niveles (promedio RGB) del color mediano del fondo que aun son fondo
DEVICE_DIST = 25.0        # niveles: lo que se separa asi del fondo es el aparato (celular, tarjeta; el blanco de
#                           un chat sobre un fondo lima esta a ~39)


def _outside(rgb: np.ndarray, box, pad: float = 0.03) -> np.ndarray:
    h, w = rgb.shape[:2]
    m = np.ones((h, w), bool)
    b = _box(box)
    if b:
        m[int(max(0.0, b[1] - pad) * h):int(math.ceil(min(1.0, b[3] + pad) * h)),
          int(max(0.0, b[0] - pad) * w):int(math.ceil(min(1.0, b[2] + pad) * w))] = False
    return m


def bg_flat(rgb: np.ndarray, box) -> tuple[float, np.ndarray]:
    """(fraccion del cuadro FUERA de la caja del sujeto que tiene el color de su fondo, color mediano del
    fondo): una demo de celular sobre un fondo de color liso (con algun adorno en las esquinas) da > 0.8; un
    cuarto o una oficina, < 0.7."""
    out = _outside(rgb, box)
    px = rgb[out].reshape(-1, 3).astype(np.float32) if out.any() else rgb.reshape(-1, 3).astype(np.float32)
    med = np.median(px, axis=0)
    return float((np.abs(px - med).mean(axis=1) < BG_DIST).mean()), med


def device_box(rgb: np.ndarray, bg: np.ndarray) -> list[float] | None:
    """Caja del aparato sobre un fondo liso de color `bg`: la mancha MAS GRANDE que se separa del fondo
    (DEVICE_DIST), sin los adornos chicos de las esquinas; None si no hay una clara (5-70 % del cuadro)."""
    import cv2
    h, w = rgb.shape[:2]
    m = (np.abs(rgb.astype(np.float32) - bg).mean(axis=2) > DEVICE_DIST).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    n, _lab, st, _c = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    x, y, ww, hh, _a = (int(v) for v in st[i])
    if not 0.05 <= ww * hh / float(w * h) <= 0.7:
        return None
    return _box([x / w, y / h, (x + ww) / w, (y + hh) / h])


def window_crop(src_aspect: float, subject: list | None, bg_flat: bool, area: float, masks=()) -> list[float] | None:
    """Recorte de la ventana flotante: None (el cuadro completo) salvo cuando el sujeto es chico o ANGOSTO
    sobre un fondo liso (una demo de celular sobre un fondo de color: el celular ocupa todo el alto pero un
    tercio del ancho; mostrado completo, el chat no se lee): su caja + 12 % de aire, con una proporcion entre
    9:16 y 16:9 (fracciones; un celular queda vertical). Con `masks` (personas de los cuadros de la toma) el
    recorte se corre, sin dejar fuera al sujeto, a donde salga menos gente (las siluetas bajo un logo)."""
    sb = _box(subject)
    if not sb or not bg_flat or (area >= 0.35 and sb[2] - sb[0] > 0.6):
        return None
    asp = max(1e-3, _num(src_aspect, 16 / 9))
    x0, y0, x1, y1 = sb
    mx, my = 0.12 * (x1 - x0), 0.12 * (y1 - y0)
    x0, x1, y0, y1 = x0 - mx, x1 + mx, y0 - my, y1 + my
    a = (x1 - x0) * asp / max(1e-6, y1 - y0)            # proporcion en pixeles
    if a < 9 / 16:                                       # muy alto: se ensancha
        need = (9 / 16) * (y1 - y0) / asp
        c = (x0 + x1) / 2
        x0, x1 = c - need / 2, c + need / 2
    elif a > 16 / 9:
        need = (x1 - x0) * asp / (16 / 9)
        c = (y0 + y1) / 2
        y0, y1 = c - need / 2, c + need / 2
    # dentro del cuadro (se corre, no se encoge)
    if x0 < 0:
        x1, x0 = x1 - x0, 0.0
    if x1 > 1:
        x0, x1 = max(0.0, x0 - (x1 - 1)), 1.0
    if y0 < 0:
        y1, y0 = y1 - y0, 0.0
    if y1 > 1:
        y0, y1 = max(0.0, y0 - (y1 - 1)), 1.0
    ms = [m for m in masks or () if m is not None and m.any()]
    if ms:
        bw, bh = x1 - x0, y1 - y0
        xa, xb = max(0.0, sb[2] - bw), min(sb[0], 1.0 - bw)
        ya, yb = max(0.0, sb[3] - bh), min(sb[1], 1.0 - bh)
        if xa <= xb and ya <= yb:
            best = None
            for jx in range(9):
                for jy in range(9):
                    nx = xa + (xb - xa) * jx / 8
                    ny = ya + (yb - ya) * jy / 8
                    ppl = max(person_frac(m, nx, nx + bw, ny, ny + bh) for m in ms)
                    key = (round(ppl, 3), abs(nx - x0) + abs(ny - y0))
                    if best is None or key < best[0]:
                        best = (key, nx, ny)
            _k, x0, y0 = best
            x1, y1 = x0 + bw, y0 + bh
    return _box([x0, y0, x1, y1])


def usable(sh: dict) -> tuple[bool, bool, str]:
    """(sirve en ventana, sirve en pantalla completa, por que no) de una toma del catalogo."""
    subj = sh.get("subject")
    L = _num(sh.get("b")) - _num(sh.get("a"))
    if L < USE_MIN:
        return False, False, "Toma demasiado corta"
    if sh.get("dark"):
        return False, False, "Toma casi negra"
    if sh.get("person_main") or subj in PEOPLE or _num(sh.get("face")) > FACE_MAX \
            or _num(sh.get("people")) > PERSON_MAIN or sh.get("person_what"):
        # (person_what: la descripcion del VLM empieza con una persona -«Un hombre mira una pantalla…»-:
        # aunque un recorte la evite, la toma es de ella y la camara se mueve con ella)
        return False, False, "Una persona es lo principal"
    if subj not in GOOD:
        return False, False, f"Muestra {SUBJECT_ES.get(subj, 'otra cosa')}, no el producto"
    win = _num(sh.get("people")) <= WINDOW_PEOPLE and _num(sh.get("face")) <= WINDOW_FACE \
        and not sh.get("person_seen") and not sh.get("intruder")
    fc = sh.get("full") or None
    need = FULL_WHOLE if fc and fc.get("whole") else FULL_INSIDE
    full_why = ""
    if not fc:
        full_why = "no cabe lo importante"
    elif int(fc.get("cut_text") or 0):
        full_why = "cortaría un texto"
    elif int(fc.get("cut_obj") or 0):
        full_why = "cortaría un personaje o un globo de texto"
    elif _num(fc.get("people"), 1.0) > FULL_PEOPLE:
        full_why = "saldría una persona"
    elif _num(fc.get("inside")) < need or _num(fc.get("content"), 1.0) < FULL_CONTENT:
        full_why = "no cabe lo importante"
    elif sh.get("device") and sh.get("win_crop"):
        # un celular (o una pantalla) sobre un fondo liso: el recorte 9:16 de alto completo le corta los lados
        # a la pantalla del aparato (los renglones del chat quedaban mochos en la orilla); va en ventana,
        # recortado al aparato
        full_why = "cortaría la pantalla del aparato"
    full = not full_why
    why = ""
    if not win and not full:
        why = ("Sale una persona en el cuadro" if full_why == "saldría una persona"
               else f"Sale una persona, y recortada a pantalla completa {full_why}")
    elif not win:
        why = "Sale una persona: solo en pantalla completa (recortada)"
    elif not full:
        why = f"Recortada a pantalla completa {full_why}"
    return win, full, why


def rule_label(feat: dict) -> dict:
    """Etiqueta sin VLM (reglas): persona si la cara o la persona dominan; titulo/logo si es un cuadro
    oscuro con un elemento claro chico al centro; pantalla si hay varias lineas de texto; si no, objeto."""
    face, ppl = _num(feat.get("face")), _num(feat.get("people"))
    if face > FACE_MAX or ppl > PERSON_MAIN:
        subj = "person"
    elif feat.get("dark_bg") and feat.get("center_bright"):
        subj = "logo"
    elif len(feat.get("texts") or []) >= 3:
        subj = "screen"
    elif ppl > WINDOW_PEOPLE:
        subj = "people"
    else:
        subj = "object"
    return {"subject": subj, "person_main": subj in PEOPLE and (face > FACE_MAX or ppl > PERSON_MAIN),
            "text": [], "what": "", "tags": [], "box": feat.get("sal_box"), "person_seen": subj in PEOPLE}


_EN_WORDS = frozenset("a an the of with and showing shows screen displaying display on in at is are man woman "
                      "person people from about for its this that".split())


def _english(txt: str) -> bool:
    """La descripcion esta en ingles (>= 30 % de palabras funcionales del ingles y ninguna del espanol)."""
    ws = re.findall(r"[a-záéíóúñü]+", str(txt or "").lower())
    if len(ws) < 3:
        return False
    es = {"un", "una", "de", "la", "el", "con", "en", "que", "los", "las", "del", "mostrando", "muestra"}
    return sum(w in _EN_WORDS for w in ws) / len(ws) >= 0.3 and not any(w in es for w in ws)


def merge_label(feat: dict, vlm: dict | None) -> dict:
    """Etiqueta de la toma: la del VLM (saneada) o la de las reglas; las reglas de personas MANDAN
    (una cara grande o mucha persona = persona aunque el VLM diga pantalla)."""
    base = rule_label(feat)
    if not vlm:
        return dict(base, src="reglas")
    subj = vlm.get("subject") if vlm.get("subject") in SUBJECTS else base["subject"]
    pm = bool(vlm.get("person_is_main")) or base["person_main"]
    if base["subject"] == "person":
        subj, pm = "person", True
    box = None
    sb = vlm.get("subject_box")
    if isinstance(sb, list) and len(sb) == 4:
        box = _box([_num(v) / 1000.0 for v in sb])
    texts = [str(t).strip()[:80] for t in (vlm.get("text_seen") or []) if str(t).strip()][:4]
    tags = [str(t).strip().lower()[:40] for t in (vlm.get("tags") or []) if str(t).strip()][:6]
    what = str(vlm.get("what") or "").strip()[:140]
    # "Un hombre mira una pantalla...": hay una persona a la vista (en la ventana se veria completa)
    seen = bool(PERSON_WORDS.match(what)) or any(PERSON_WORDS.match(t) for t in tags)
    person_what = bool(PERSON_WORDS.match(what))
    if _english(what):
        # el VLM a veces contesta en ingles ("A screen showing a chat message…"): el Studio muestra esto al
        # usuario, en espanol siempre (el sujeto y lo que se lee); la persona ya se vio arriba
        txt = next((t for t in texts if len(t) <= 40), "")
        what = SUBJECT_ES.get(subj, "toma").capitalize() + (f" con «{txt}»" if txt else "")
    return {"subject": subj, "person_main": pm, "text": texts, "what": what, "tags": tags,
            "box": box or base.get("box"), "src": "vlm", "person_seen": seen or pm,
            "person_what": person_what}


# ================================================================ lectura (ffmpeg)
def _ff():
    from motion import video_io as V
    return V


def _scan_size(w: int, h: int) -> tuple[int, int]:
    if w >= h:
        sw = SCAN_LONG
        sh = max(8, int(round(SCAN_LONG * h / max(1, w) / 2)) * 2)
    else:
        sh = SCAN_LONG
        sw = max(8, int(round(SCAN_LONG * w / max(1, h) / 2)) * 2)
    return sw, sh


def detect_cuts(path, info: dict | None = None, cancel=None) -> tuple[list[float], float, float, np.ndarray]:
    """Lectura completa a baja resolucion -> (cortes en s, duracion, cps de la lectura, puntajes)."""
    V = _ff()
    info = info or V.probe(path)
    w, h = int(info.get("w") or 0), int(info.get("h") or 0)
    if not w or not h:
        raise RuntimeError("video ilegible")
    sw, sh = _scan_size(w, h)
    fps = min(SCAN_FPS_MAX, _num(info.get("fps_nominal")) or _num(info.get("fps")) or 30.0)
    if fps <= 0:
        fps = 30.0
    cmd = [V.FFMPEG, "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0", "-an", "-sn",
           "-vf", f"fps={fps:.6f},scale={sw}:{sh}:flags=area,format=rgb24", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = sw * sh * 3
    scores, prev = [], None
    try:
        while True:
            if cancel is not None and cancel():
                p.kill()
                raise RuntimeError("cancelado")
            buf = p.stdout.read(n)
            if not buf or len(buf) < n:
                break
            f = frame_features(np.frombuffer(buf, np.uint8).reshape(sh, sw, 3))
            scores.append(0.0 if prev is None else pair_score(prev, f))
            prev = f
    finally:
        try:
            p.stdout.close()
        except Exception:  # noqa
            pass
        p.wait()
    s = np.asarray(scores, np.float32)
    dur = _num(info.get("duration")) or len(s) / fps
    cuts = [round(k / fps, 4) for k in pick_cuts(s, fps)]
    return cuts, float(dur), float(fps), s


def _frame(path, t: float, info: dict, long_side: int = FRAME_W) -> np.ndarray | None:
    """Un cuadro RGB (lado largo `long_side`, SDR bt709 como el render) en t (s del archivo)."""
    V = _ff()
    w, h = int(info.get("w") or 0), int(info.get("h") or 0)
    if w >= h:
        fw, fh = long_side, max(2, int(round(long_side * h / max(1, w) / 2)) * 2)
    else:
        fh, fw = long_side, max(2, int(round(long_side * w / max(1, h) / 2)) * 2)
    chain = [f"scale={fw}:{fh}:flags=area"] + V.tonemap_filters(info) + [
        f"scale={fw}:{fh}:in_color_matrix=bt709:in_range=tv:flags=bicubic", "format=rgb24"]
    r = subprocess.run([V.FFMPEG, "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.4f}", "-i", str(path),
                        "-map", "0:v:0", "-frames:v", "1", "-vf", ",".join(chain), "-f", "rawvideo", "-"],
                       capture_output=True, timeout=120)
    need = fw * fh * 3
    if len(r.stdout) < need:
        return None
    return np.frombuffer(r.stdout[:need], np.uint8).reshape(fh, fw, 3).copy()


def _person_mask(rgb: np.ndarray) -> np.ndarray | None:
    """Mascara bool de persona (PP-HumanSeg) del cuadro completo: cuadrados a lo largo del lado largo
    (sin deformar), el maximo de cada pixel. None sin el modelo."""
    import cv2
    try:
        from motion import matte as M
        if not M.model_path("person").exists():
            return None
    except Exception:  # noqa
        return None
    h, w = rgb.shape[:2]
    s = min(w, h)
    acc = np.zeros((h, w), np.float32)
    n = 1 if abs(w - h) < 0.1 * s else int(math.ceil(max(w, h) / s)) + 1
    for j in range(n):
        off = 0 if n == 1 else int(round((max(w, h) - s) * j / (n - 1)))
        tile = rgb[:, off:off + s] if w >= h else rgb[off:off + s, :]
        try:
            p = M.person_prob(np.ascontiguousarray(tile))
        except Exception:  # noqa
            return None
        p = cv2.resize(p, (tile.shape[1], tile.shape[0]))
        if w >= h:
            acc[:, off:off + s] = np.maximum(acc[:, off:off + s], p)
        else:
            acc[off:off + s, :] = np.maximum(acc[off:off + s, :], p)
    return acc > 0.5


def _faces(rgb: np.ndarray) -> list[tuple]:
    try:
        from motion import matte as M
        return [f for f in M.detect_faces(rgb) if f[4] >= 0.6]
    except Exception:  # noqa
        return []


def frame_feat(rgb: np.ndarray) -> dict:
    """Rasgos de un cuadro: mascara de persona, caras, textos, caja llamativa, luminancia, fondo."""
    import cv2
    mask = _person_mask(rgb)
    faces = _faces(rgb)
    if mask is not None and faces:
        # una cara tambien es persona (la segmentacion falla en caras desenfocadas)
        h, w = mask.shape
        for cx, cy, fw, fh, _s in faces:
            x0, x1 = int(max(0, (cx - fw * 0.6) * w)), int(min(w, (cx + fw * 0.6) * w))
            y0, y1 = int(max(0, (cy - fh * 0.6) * h)), int(min(h, (cy + fh * 0.7) * h))
            mask[y0:y1, x0:x1] = True
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    luma = float(g.mean())
    bright = float(np.percentile(g, 99.5))
    h, w = g.shape
    border = np.concatenate([g[: h // 8].ravel(), g[-h // 8:].ravel(), g[:, : w // 8].ravel(), g[:, -w // 8:].ravel()])
    center = g[h // 3: 2 * h // 3, w // 3: 2 * w // 3]
    return {"mask": mask, "people": float(mask.mean()) if mask is not None else 0.0, "faces": faces,
            "face": max([f[2] * f[3] for f in faces] + [0.0]), "texts": text_boxes(rgb), "objs": object_boxes(rgb),
            "sal_box": saliency_box(rgb), "luma": luma, "bright": bright, "bg_std": float(border.std()),
            "dark_bg": float(np.median(border)) < 0.12,
            "center_bright": float(np.percentile(center, 98)) > 0.6 and float(np.median(center)) < 0.3}


def _vlm_label(rgb: np.ndarray, timeout: float = VLM_TIMEOUT) -> dict | None:
    import cv2
    try:
        from motion import director as D
    except Exception:  # noqa
        return None
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        return None
    try:
        raw = D._ollama_chat([{"role": "user", "content": _VLM_PROMPT,
                               "images": [base64.b64encode(buf.tobytes()).decode()]}],
                             fmt=_VLM_SCHEMA, model=D._vlm_model(), num_ctx=4096, temperature=0.1,
                             num_predict=350, timeout=timeout)
    except Exception:  # noqa
        return None
    return D._json_loose(raw)


def vlm_model() -> str | None:
    """Modelo del VLM local si ollama responde y lo tiene; None si no."""
    try:
        from motion import director as D
        m = D._vlm_model()
        return m if D.llm_available(m) else None
    except Exception:  # noqa
        return None


# ================================================================ cache
def _cache_root(cache_dir=None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    import paths
    return Path(paths.WORK) / "broll"


def signature(path, vlm: str | None) -> str:
    p = Path(path)
    st = p.stat()
    key = [VERSION, str(p.resolve()), st.st_size, int(st.st_mtime_ns), vlm or "reglas"]
    return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:16]


def _file_sig(path, versioned: bool = True) -> str:
    """Firma del archivo (ruta, tamano, fecha) y, si `versioned`, de la version de las reglas."""
    p = Path(path)
    st = p.stat()
    key = [str(p.resolve()), st.st_size, int(st.st_mtime_ns)]
    return hashlib.sha1(json.dumps(([VERSION] if versioned else []) + key).encode()).hexdigest()[:12]


def _vlm_dir(path, model: str, cache_dir=None) -> Path:
    """Respuestas del VLM por toma: dependen del archivo, del modelo y del prompt (VLM_VERSION), NO de las
    reglas (VERSION): cambiar las reglas vuelve a medir los cuadros sin volver a preguntarle al VLM."""
    return _cache_root(cache_dir) / "vlm" / f"{_file_sig(path, versioned=False)}_{_safe(model)}_{VLM_VERSION}"


def _dir(path, cache_dir=None) -> Path:
    return _cache_root(cache_dir) / _file_sig(path)


def cached(path, cache_dir=None, vlm: str | None | bool = True) -> dict | None:
    """Catalogo guardado del archivo (el del VLM si `vlm` es True y existe; si no, el de reglas) o None."""
    try:
        d = _dir(path, cache_dir)
    except OSError:
        return None
    names = []
    if vlm is True:
        names = sorted(d.glob("analysis_*.json"), key=lambda p: (p.name == "analysis_reglas.json", p.name))
    elif isinstance(vlm, str):
        names = [d / f"analysis_{_safe(vlm)}.json"]
    else:
        names = [d / "analysis_reglas.json"]
    best = None
    for f in names:
        try:
            doc = json.loads(f.read_text())
            if doc.get("version") == VERSION:
                if f.name == "analysis_reglas.json" and best is not None:
                    return best             # uno del VLM degradado vale mas que solo reglas
                if not doc.get("degraded"):
                    return doc
                best = best or doc          # degradado: si no hay otro mejor (analyze lo vuelve a ver)
        except (OSError, json.JSONDecodeError):
            continue
    return best


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(s))[:60]


def thumb_path(path, i: int, cache_dir=None) -> Path | None:
    try:
        f = _dir(path, cache_dir) / f"s{int(i)}.jpg"
    except (OSError, ValueError):
        return None
    return f if f.exists() else None


# ================================================================ analisis completo
def analyze(path, *, use_vlm: bool = True, on_step=None, cancel=None, cache_dir=None,
            vlm_fn=None) -> dict:
    """Catalogo de tomas del video de apoyo `path` (ver el docstring del modulo), con cache. `vlm_fn`
    (pruebas): callable(rgb) -> dict | None en lugar del VLM local. `on_step(done, total, msg)`."""
    import cv2
    t_start = time.time()
    path = Path(path)
    model = None
    if vlm_fn is not None:
        model = "prueba"
    elif use_vlm:
        model = vlm_model()
    root = _dir(path, cache_dir)
    out_f = root / f"analysis_{_safe(model) if model else 'reglas'}.json"
    if vlm_fn is None:
        try:
            doc = json.loads(out_f.read_text())
            # un catalogo DEGRADADO (el VLM fallo en alguna toma: ollama cargando, la GPU ocupada con
            # whisper) no es definitivo: se vuelve a ver (las respuestas que si llegaron estan en vlm/)
            if doc.get("version") == VERSION and not doc.get("degraded"):
                return doc
        except (OSError, json.JSONDecodeError):
            pass
    V = _ff()
    info = V.probe(path)
    w, h = int(info.get("w") or 0), int(info.get("h") or 0)
    step = (lambda d, n, m: on_step(d, n, m)) if callable(on_step) else (lambda d, n, m: None)
    step(0, 1, "Buscando los cortes del video…")
    cuts, dur, fps, scores = detect_cuts(path, info, cancel)
    base = split_bursts(shots_from_cuts(cuts, dur), scores, fps)
    parts = []
    for a, b, _ca, _cb in base:
        ms = moments(a, b, scores, fps)
        for j, (x, y) in enumerate(ms):
            parts.append((x, y, j == 0, j == len(ms) - 1))
    root.mkdir(parents=True, exist_ok=True)
    src_aspect = w / max(1, h)
    times = [(k, a + (b - a) * u) for k, (a, b, _ca, _cb) in enumerate(parts) for u in SAMPLE_AT]
    frames: dict[tuple[int, int], np.ndarray | None] = {}

    def grab(kt):
        return kt, _frame(path, kt[1], info)
    with ThreadPoolExecutor(max_workers=4) as ex:
        for (k, t), fr in ex.map(grab, times):
            frames[(k, round(t, 4))] = fr
    shots = []
    fails = 0
    asked = 0
    degraded = 0              # tomas que debia ver el VLM y fueron con reglas porque fallo
    n = len(parts)
    # respuestas del VLM por toma (sobreviven a cambios de las reglas; otro prompt = otra carpeta)
    vdir = _vlm_dir(path, model, cache_dir) if (model and vlm_fn is None) else None
    if vdir is not None:
        vdir.mkdir(parents=True, exist_ok=True)
    for k, (a, b, ca, cb) in enumerate(parts):
        if cancel is not None and cancel():
            raise RuntimeError("cancelado")
        step(k, n, f"Viendo las tomas de tu video de apoyo ({k + 1}/{n})…")
        frs = [frames.get((k, round(a + (b - a) * u, 4))) for u in SAMPLE_AT]
        frs = [f for f in frs if f is not None]
        feats = [frame_feat(f) for f in frs]
        if not feats:
            continue
        mid = frs[len(frs) // 2]
        F = {"people": max(f["people"] for f in feats), "face": max(f["face"] for f in feats),
             "texts": [tb for f in feats for tb in f["texts"]], "objs": [ob for f in feats for ob in f["objs"]],
             "sal_box": feats[len(feats) // 2]["sal_box"],
             "luma": float(np.mean([f["luma"] for f in feats])), "bg_std": float(np.mean([f["bg_std"] for f in feats])),
             "bright": max(f["bright"] for f in feats),
             "dark_bg": all(f["dark_bg"] for f in feats), "center_bright": any(f["center_bright"] for f in feats)}
        vlm = None
        obvious_person = F["face"] > FACE_MAX * 1.5 or F["people"] > 0.5
        want_vlm = (vlm_fn is not None or model) and not obvious_person and b - a >= USE_MIN and asked < VLM_MAX
        if want_vlm and fails >= VLM_FAILS:
            degraded += 1
        if want_vlm and fails < VLM_FAILS:
            asked += 1
            vkey = vdir / f"{a:.4f}_{b:.4f}.json" if vdir is not None else None
            if vkey is not None and vkey.exists():
                try:
                    vlm = json.loads(vkey.read_text())
                except (OSError, json.JSONDecodeError):
                    vlm = None
            if vlm is None:
                vf = vlm_fn or _vlm_label
                small = mid if mid.shape[1] <= VLM_W else cv2.resize(mid, (VLM_W, int(round(VLM_W * mid.shape[0] / mid.shape[1]))))
                vlm = vf(small)
                fails = 0 if vlm else fails + 1
                degraded += 0 if vlm else 1
                if vlm and vkey is not None:
                    try:
                        vkey.write_text(json.dumps(vlm, ensure_ascii=False))
                    except OSError:
                        pass
        lab = merge_label(F, vlm)
        box = _box(lab.get("box")) or F["sal_box"]
        masks = [f["mask"] for f in feats]
        raw = list(masks)
        # la segmentacion de personas confunde personajes, logos y juguetes con personas: si el VLM dice que
        # el sujeto es del producto y no menciona a nadie, lo que cae en la caja del sujeto no es persona
        if vlm and box and not vlm.get("person_is_main") and vlm.get("subject") in GOOD:
            masks = [discount(m, box, f["faces"], fr) for m, f, fr in zip(masks, feats, frs)]
            F["people"] = max([float(m.mean()) for m in masks if m is not None] + [0.0])
            lab = merge_label(F, vlm)
        elif not vlm:
            # sin VLM: las manchas de color saturado sin cara que no tocan el borde (personajes, juguetes)
            masks = [discount(m, None, f["faces"], fr) for m, f, fr in zip(masks, feats, frs)]
            F["people"] = max([float(m.mean()) for m in masks if m is not None] + [0.0])
            lab = merge_label(F, None)
        # fondo liso alrededor del sujeto (un celular sobre un fondo de color): la caja del APARATO (mas
        # justa que la del VLM) para recortar la ventana a el
        flat, bgc = bg_flat(mid, box)
        dev = device_box(mid, bgc) if flat >= BG_FLAT else None
        wbox = dev if (dev and box and _iou(dev, box) > 0.3) or (dev and not box) else box
        # un CELULAR de pie sobre el fondo liso (alto y angosto): su pantalla no cabe en un recorte 9:16
        device = bool(dev is not None and wbox is dev and lab["subject"] in ("phone", "screen")
                      and dev[3] - dev[1] >= 0.6 and dev[2] - dev[0] <= 0.6)
        area = ((wbox[2] - wbox[0]) * (wbox[3] - wbox[1])) if wbox else 1.0
        brand = lab["subject"] in ("title", "logo") and box is not None and _num(F["face"]) <= WINDOW_FACE
        fc = full_crop(src_aspect, box, masks, F["texts"], objs=F["objs"], whole=lab["subject"] in DISCOUNT,
                       people_ymax=box[3] if brand else None)
        wc = window_crop(src_aspect, wbox, flat >= BG_FLAT, area, raw)
        if wc and max([person_frac(m, wc[0], wc[2], wc[1], wc[3]) for m in raw if m is not None] + [0.0]) \
                > WINDOW_PEOPLE:
            # con el acercamiento la gente del fondo se ve grande (y aqui cuenta TODO lo que parece persona,
            # sin el descuento de personajes: alguien que pasa junto al logo cae en su caja): el cuadro completo
            wc = None
        intr = any(intruder(m) for m in masks)
        sh = {"i": len(shots), "a": _r(a, 4), "b": _r(b, 4), "cut_a": bool(ca), "cut_b": bool(cb),
              "subject": lab["subject"], "what": lab["what"], "tags": lab["tags"], "text": lab["text"],
              "people": _r(F["people"]), "face": _r(F["face"], 4), "person_main": bool(lab["person_main"]),
              "dark": F["luma"] < DARK and F["bright"] < DARK_PEAK, "luma": _r(F["luma"]), "box": box,
              "texts": F["texts"][:16], "full": fc,
              "objs": F["objs"][:12],
              "win_crop": wc, "src": lab["src"], "bg_flat": _r(flat), "area": _r(area), "device": device,
              "person_seen": bool(lab.get("person_seen")), "person_what": bool(lab.get("person_what")),
              "intruder": bool(intr)}
        sh["ok_window"], sh["ok_full"], sh["why"] = usable(sh)
        shots.append(sh)
        try:
            th = cv2.resize(mid, (THUMB_W, max(2, int(round(THUMB_W * mid.shape[0] / mid.shape[1])))),
                            interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(root / f"s{sh['i']}.jpg"), cv2.cvtColor(th, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, 82])
        except Exception:  # noqa: sin miniatura el Studio muestra un icono
            traceback.print_exc()
    doc = {"version": VERSION, "sig": _file_sig(path), "name": path.name, "dur": _r(dur), "w": w, "h": h,
           "fps": _r(fps), "vlm": model, "cuts": [_r(c, 4) for c in cuts], "shots": shots,
           "ms": int(round((time.time() - t_start) * 1000)), "degraded": int(degraded)}
    if vlm_fn is None:
        tmp = out_f.with_name(out_f.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False))
        os.replace(tmp, out_f)
    step(n, n, "Tomas listas")
    return doc


def summary(doc: dict | None) -> dict:
    """Resumen para la UI: {"shots", "window", "full", "usable", "people"}."""
    shots = (doc or {}).get("shots") or []
    return {"shots": len(shots), "window": sum(1 for s in shots if s.get("ok_window")),
            "full": sum(1 for s in shots if s.get("ok_full")),
            "usable": sum(1 for s in shots if s.get("ok_window") or s.get("ok_full")),
            "people": sum(1 for s in shots if s.get("why") == "Una persona es lo principal")}


SHOT_KEYS = ("i", "a", "b", "cut_a", "cut_b", "subject", "what", "text", "tags", "people", "person_seen", "person_what",
             "intruder", "ok_window", "ok_full", "why", "win_crop", "src")


def compact(doc: dict | None) -> dict | None:
    """Catalogo -> lo que viaja en el plan (plan.montage.broll.sources[i]): sin mascaras ni cajas de texto,
    con el centro del recorte de pantalla completa (`cx`) de cada toma."""
    if not isinstance(doc, dict):
        return None
    shots = []
    for sh in doc.get("shots") or []:
        if not isinstance(sh, dict):
            continue
        x = {k: sh.get(k) for k in SHOT_KEYS if k in sh}
        fc = sh.get("full") or {}
        x["cx"] = fc.get("cx")
        x["text"] = list(x.get("text") or [])[:4]
        x["tags"] = list(x.get("tags") or [])[:6]
        shots.append(x)
    return {"sig": doc.get("sig"), "name": doc.get("name"), "dur": doc.get("dur"), "w": doc.get("w"),
            "h": doc.get("h"), "fps": doc.get("fps"), "vlm": doc.get("vlm"), "shots": shots,
            "summary": summary(doc)}
