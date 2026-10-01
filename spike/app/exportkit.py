"""Reelfy — opciones del export: resolucion por formato, calidad, nombre del archivo, carpeta de
destino (selector NATIVO de carpetas), copia sin sobrescribir, "Mostrar en Finder"/"Abrir" y la
estimacion de tiempo y tamano.

Modulo puro (sin FastAPI ni el motor): app/server.py lo conecta (seccion "export" y POST /render).

Resoluciones: por el LADO CORTO de la salida (720p = 720, 1080p = 1080, 1440p = 1440, 4K = 2160) en
cada formato (9:16 4K = 2160x3840, 16:9 4K = 3840x2160, 1:1 4K = 2160x2160, 4:5 4K = 2160x2700;
"original" = el aspecto de la fuente, lado largo <= 4096). 1080p es EXACTAMENTE la salida de siempre
(render.output_size). Nunca se escala por encima de la resolucion real de las fuentes: una
resolucion que pide mas pixeles de los que da el recorte del formato sobre la fuente se apaga con
el motivo ("Tu video original es 1080p; 4K no agregaria calidad").

Carpeta: solo se escribe en una carpeta que vino del selector nativo (POST /pick-folder), en la
recordada o en la de siempre (~/Movies/Reelfy; REELFY_EXPORT_DIR la cambia). El nombre se sanea (sin
separadores, sin '..', sin nombres reservados de Windows) y el archivo cae SIEMPRE dentro de esa
carpeta, sin sobrescribir: "Nombre (2).mp4", "Nombre (3).mp4"...
"""
from __future__ import annotations

import errno
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------- resoluciones
RESOLUTIONS = (("720p", 720, "720p"), ("1080p", 1080, "1080p"), ("1440p", 1440, "1440p"),
               ("4k", 2160, "4K"))
RES_IDS = tuple(r[0] for r in RESOLUTIONS)
RES_SHORT = {r[0]: r[1] for r in RESOLUTIONS}
RES_LABEL = {r[0]: r[2] for r in RESOLUTIONS}
DEFAULT_RES = "1080p"
# por encima de 1080p el video base se arma al tamano de la salida (y, en un montaje, las tomas se
# recomponen a la resolucion real si el video combinado se quedo corto); hasta 1080p todo es lo de
# siempre (720p solo compone mas chico sobre el mismo base)
HIRES = ("1440p", "4k")
# = render.FORMATS (el servidor pasa los del motor; esta copia es para las pruebas sin el motor)
FORMAT_SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}
FORMAT_LABELS = {"9:16": "Vertical 9:16", "16:9": "Horizontal 16:9", "1:1": "Cuadrado 1:1",
                 "4:5": "Retrato 4:5", "original": "Original"}
LONG_MAX = 4096          # lado largo maximo (H.264 nivel 5.2 / VideoToolbox)
PILLAR_RATIO = 2.0       # = video_io.PILLAR_RATIO: salida mucho mas ancha que la fuente -> sin recorte
MAJORITY = 0.5           # mezcla de resoluciones: una resolucion alta se ofrece si la da >= la mitad del video
UPSCALE_TOL = 0.05       # una resolucion hasta 5 % arriba de lo que da la fuente cuenta como nativa
NATIVE_SNAP = 0.9        # recomponer a >= 90 % del nativo = recomponer al nativo (sin tamanos raros)

# ---------------------------------------------------------------- calidad
# (id, etiqueta, bits por pixel (VideoToolbox), CRF (libx264), ayuda). "alta" = lo de siempre.
QUALITIES = (("estandar", "Estándar", 0.15, 21, "Archivo más ligero"),
             ("alta", "Alta", 0.22, 18, "La de siempre: ideal para redes"),
             ("maxima", "Máxima", 0.33, 15, "Para archivar o volver a editar"))
QUALITY_IDS = tuple(q[0] for q in QUALITIES)
QUALITY = {q[0]: {"id": q[0], "label": q[1], "bpp": q[2], "crf": q[3], "hint": q[4]} for q in QUALITIES}
DEFAULT_QUALITY = "alta"
AUDIO_BPS = 192_000


def _even(v):
    v = int(round(v))
    return max(2, v - v % 2)


def res_label_of(short):
    """Etiqueta de una resolucion por su lado corto: 2160+ -> 4K; un nivel conocido -> el nivel;
    entre dos niveles -> el de abajo con '+' (1620 -> '1440p+': nadie conoce "1620p"); debajo de
    720 -> '<lado>p'."""
    short = int(short or 0)
    if short >= 2160 - 2:
        return "4K"
    for _rid, s, lab in RESOLUTIONS:
        if abs(short - s) <= 2:
            return lab
    below = [lab for _rid, s, lab in RESOLUTIONS if s < short]
    return f"{below[-1]}+" if below else f"{short}p"


def orientation(w, h):
    w, h = int(w or 0), int(h or 0)
    return "vertical" if h > w else ("horizontal" if w > h else "cuadrado")


def export_size(fmt, res, src_wh=None, formats=None):
    """(W, H) de la salida en `fmt` a la resolucion `res`. 1080p = render.output_size (lo de
    siempre: 'original' con el lado corto a 1080 y el largo <= 3840). `src_wh` = dims de display del
    video del proyecto (solo para 'original'). ValueError si no aplica."""
    formats = formats or FORMAT_SIZES
    if res not in RES_SHORT:
        raise ValueError(f"Resolución desconocida: {res}")
    short = RES_SHORT[res]
    if fmt in formats:
        w, h = formats[fmt]
    elif fmt == "original":
        w, h = (int(src_wh[0] or 0), int(src_wh[1] or 0)) if src_wh else (0, 0)
        if not w or not h:
            raise ValueError("El formato 'original' necesita las dimensiones de la fuente.")
    else:
        raise ValueError(f"Formato desconocido: {fmt}")
    k = short / float(min(w, h))
    if fmt == "original":
        long_cap = min(LONG_MAX, round(3840 * short / 1080.0))
        k = min(k, long_cap / float(max(w, h)))
    elif max(w, h) * k > LONG_MAX:
        k = LONG_MAX / float(max(w, h))
    return _even(w * k), _even(h * k)


def is_pillar(fmt, native_wh, formats=None):
    """El motor NO recorta: la salida es mucho mas ancha que la fuente (vertical -> 16:9, relacion
    >= PILLAR_RATIO) y la toma completa va a lo alto de la salida sobre un fondo desenfocado de si
    misma (= video_io.reframe_mode 'pillar')."""
    formats = formats or FORMAT_SIZES
    nw, nh = int(native_wh[0] or 0), int(native_wh[1] or 0)
    if fmt == "original" or not nw or not nh or fmt not in formats:
        return False
    w, h = formats[fmt]
    return (w / float(h)) / (nw / float(nh)) >= PILLAR_RATIO


def useful_short(fmt, native_wh, formats=None):
    """Lado corto MAXIMO de una salida `fmt` sacada de un video de `native_wh` sin escalar hacia
    arriba, con la geometria del reencuadre del motor (video_io.reframe_filters): el mayor
    rectangulo con el aspecto del formato dentro del video (el recorte) o, en modo pillar (vertical
    -> 16:9), el alto de la fuente (la toma completa va a lo alto de la salida, sin recorte)."""
    formats = formats or FORMAT_SIZES
    nw, nh = int(native_wh[0] or 0), int(native_wh[1] or 0)
    if not nw or not nh:
        return 0
    if fmt == "original":
        return min(nw, nh)
    w, h = formats.get(fmt, (1080, 1920))
    a = w / float(h)
    if is_pillar(fmt, (nw, nh), formats):
        # alto de la salida <= alto de la fuente; el lado corto es el alto (salida horizontal)
        return int(round(nh * min(1.0, a)))
    cw = min(float(nw), nh * a)
    ch = cw / a
    return int(round(min(cw, ch)))


def res_allowed(fmt, res, native_wh, formats=None):
    """720p y 1080p siempre (1080p = lo de siempre); 1440p/4K solo si la fuente los da."""
    if res in ("720p", "1080p"):
        return True
    return RES_SHORT[res] <= useful_short(fmt, native_wh, formats) * (1.0 + UPSCALE_TOL)


def needed_canvas(fmt, res, native_wh, combined_wh, formats=None):
    """Lienzo al que hay que RECOMPONER las tomas para exportar `fmt` a `res` sin perder detalle
    -> (w, h), o None si el video combinado ya alcanza (o la resolucion no es alta). Proporcional al
    lienzo nativo (las fuentes); >= 90 % del nativo = el nativo."""
    if res not in HIRES or not native_wh or not combined_wh:
        return None
    nw, nh = int(native_wh[0]), int(native_wh[1])
    cw, ch = int(combined_wh[0]), int(combined_wh[1])
    use = useful_short(fmt, (nw, nh), formats)
    if use <= 0:
        return None
    s = min(1.0, RES_SHORT[res] / float(use))
    need = (nw - nw % 2, nh - nh % 2) if s >= NATIVE_SNAP else (_even(nw * s), _even(nh * s))
    if need[0] <= cw + 2 and need[1] <= ch + 2:
        return None
    return need


def _reason(fmt, res, native_wh, formats=None):
    nw, nh = int(native_wh[0]), int(native_wh[1])
    src = res_label_of(min(nw, nh))
    use = useful_short(fmt, native_wh, formats)
    tier = RES_LABEL[res]
    if is_pillar(fmt, native_wh, formats):
        return (f"Tu video original ({src} {orientation(nw, nh)}) mide {nh} px de alto; en "
                f"{FORMAT_LABELS.get(fmt, fmt)} {tier} no agregaría calidad")
    if use >= min(nw, nh) - 2:
        return f"Tu video original es {src}; {tier} no agregaría calidad"
    return (f"Al recortar tu video ({src} {orientation(nw, nh)}) a {FORMAT_LABELS.get(fmt, fmt)} queda en "
            f"{res_label_of(use)}; {tier} no agregaría calidad")


def _clip_split(fmt, res, clips, formats=None):
    """Clips de un montaje que dan `res` en `fmt` sin escalar y los que no -> (segundos que la dan,
    segundos totales, [clips que no])."""
    ok = tot = 0.0
    low = []
    for c in clips:
        secs = max(0.0, float(c.get("secs") or 0.0)) or 1.0
        tot += secs
        if res_allowed(fmt, res, (c["w"], c["h"]), formats):
            ok += secs
        else:
            low.append(c)
    return ok, tot, low


def _low_label(fmt, low, formats=None):
    """"1080p" (todos los clips de menos resolucion son del mismo nivel) o "de 1080p o menos"."""
    shorts = [useful_short(fmt, (c["w"], c["h"]), formats) for c in low]
    labs = {res_label_of(x) for x in shorts}
    top = res_label_of(max(shorts))
    return top if len(labs) == 1 else f"de {top} o menos"


def resolution_matrix(fmt, native_wh, combined_wh, can_recompose=True, formats=None,
                      missing_msg=None, clips=None):
    """[{id, label, W, H, enabled, reason, warn, recompose, recompose_size}] de un formato.
    `native_wh` = resolucion real de las fuentes (lienzo sin tope); `combined_wh` = el video del
    proyecto (lo que hoy lee el render). `can_recompose` False (fuentes borradas, proyecto sin
    registro) -> lo que pide recomponer se apaga con `missing_msg`. `clips` (montaje con clips de
    distinta resolucion): [{w, h, secs}] = el lienzo (con el aspecto del video) que cada clip llena
    sin escalar; una resolucion alta que solo da una MINORIA del video se apaga ("La mayoria de tus
    clips son 1080p...") y la que da la mayoria avisa en `warn` cuantos clips se veran escalados."""
    native_wh = native_wh or combined_wh
    clips = [c for c in clips or [] if c and c.get("w") and c.get("h")]
    out = []
    for rid in RES_IDS:
        W, H = export_size(fmt, rid, combined_wh, formats)
        row = {"id": rid, "label": RES_LABEL[rid], "W": W, "H": H, "enabled": True, "reason": None,
               "warn": None, "recompose": False, "recompose_size": None}
        split = _clip_split(fmt, rid, clips, formats) if (clips and rid in HIRES) else None
        if not res_allowed(fmt, rid, native_wh, formats):
            row.update(enabled=False, reason=_reason(fmt, rid, native_wh, formats))
        elif split and split[2] and split[0] < MAJORITY * split[1]:
            row.update(enabled=False, reason=(f"La mayoría de tus clips son {_low_label(fmt, split[2], formats)}; "
                                              f"{RES_LABEL[rid]} no agregaría calidad"))
        else:
            if split and split[2]:
                n, lab = len(split[2]), _low_label(fmt, split[2], formats)
                row["warn"] = (f"{n} de {len(clips)} clips {'es' if n == 1 else 'son'} {lab}: "
                               f"se {'verá' if n == 1 else 'verán'} escalado{'' if n == 1 else 's'} en {RES_LABEL[rid]}")
            need = needed_canvas(fmt, rid, native_wh, combined_wh, formats)
            if need:
                if can_recompose:
                    row.update(recompose=True, recompose_size=list(need))
                else:
                    best = max((r for r in RES_IDS if res_allowed(fmt, r, combined_wh, formats)),
                               key=lambda r: RES_SHORT[r])
                    row.update(enabled=False, reason=missing_msg or (
                        f"Los clips originales ya no están en este equipo: sin ellos el máximo es "
                        f"{RES_LABEL[best]}"))
        out.append(row)
    return out


def clamp_res(res, rows):
    """La resolucion pedida si esta habilitada; si no, la mas alta habilitada por debajo (1080p
    siempre lo esta)."""
    ok = [r["id"] for r in rows if r["enabled"]]
    if res in ok:
        return res
    below = [r for r in ok if RES_SHORT[r] <= RES_SHORT.get(res, 1080)]
    return max(below, key=lambda r: RES_SHORT[r]) if below else DEFAULT_RES


# ---------------------------------------------------------------- nombre del archivo
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
# invisibles que no forman parte de ningun texto: control C1, espacio de ancho cero (U+200B), marcas
# de direccion (U+200E/F) y los controles bidi (U+202A-E, U+2066-9: "video\u202Ekcod.mp4" se ve
# como otro nombre) y el BOM. Se CONSERVAN el ZWNJ/ZWJ (U+200C/D: emoji compuestos como 👨‍💻 y
# escrituras como el persa) y los selectores de variante (U+FE0F: 🏳️‍🌈) (= FXCore.sanitizeFileName)
_INVISIBLE = re.compile("[\u0080-\u009f\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_COLON = re.compile(r"\s*[:|]+\s+|\s+[:|]+\s*")      # "Tema: subtitulo" -> "Tema - subtitulo"
_SEP = re.compile(r"[/\\:|]")
_DROP = re.compile(r"[*?\"<>]")
_EXT = re.compile(r"\.(mp4|mov|m4v)$", re.IGNORECASE)
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
NAME_MAX_BYTES = 180      # + " (99) - Short 12.mp4" sigue debajo de los 255 bytes del sistema
TOPIC_MAX = 60


def sanitize_name(raw, default="Reelfy"):
    """Nombre de archivo SEGURO (sin extension): sin control, sin separadores de ruta (/ \\ : |
    -> '-'), sin * ? " < >, sin espacios repetidos, sin punto ni espacio al inicio o al final (ni
    archivo oculto, ni '..'), sin nombres reservados de Windows, <= NAME_MAX_BYTES en UTF-8. Vacio
    -> `default`."""
    s = unicodedata.normalize("NFC", str(raw if raw is not None else ""))
    s = _INVISIBLE.sub("", s)
    s = _CTRL.sub(" ", s)
    s = _COLON.sub(" - ", s)
    s = _SEP.sub("-", s)
    s = _DROP.sub("", s)
    s = re.sub(r"\.{2,}", ".", s)                  # nunca '..' (ni dentro del nombre)
    s = re.sub(r"\s+", " ", s).strip()
    s = _EXT.sub("", s).strip()
    s = s.strip(" .")
    if not s or not re.search(r"[^\s.\-]", s):
        return sanitize_name(default, "Reelfy") if default != s and default else "Reelfy"
    if s.split(".")[0].strip().upper() in WIN_RESERVED:
        s += "_"
    b = s.encode("utf-8")
    if len(b) > NAME_MAX_BYTES:
        s = b[:NAME_MAX_BYTES].decode("utf-8", "ignore").rstrip(" .-\u200c\u200d")
    return s or "Reelfy"


def _short_topic(topic):
    t = re.sub(r"\s+", " ", str(topic or "")).strip().strip(".")
    if len(t) <= TOPIC_MAX:
        return t
    cut = t[:TOPIC_MAX]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp >= TOPIC_MAX // 2 else cut).rstrip(" ,;:-")


def default_name(topic=None, when=None):
    """"<proyecto> - <fecha>" (fecha ISO local: se ordena sola), saneado."""
    day = time.strftime("%Y-%m-%d", time.localtime(when or time.time()))
    proj = sanitize_name(_short_topic(topic), "Reelfy") if topic else "Reelfy"
    return sanitize_name(f"{proj} - {day}")


def candidate_names(stem, ext=".mp4", limit=999):
    yield f"{stem}{ext}"
    for k in range(2, limit + 1):
        yield f"{stem} ({k}){ext}"


def unique_path(folder, stem, ext=".mp4"):
    """Primera ruta libre dentro de `folder` ("Nombre.mp4", "Nombre (2).mp4", ...)."""
    folder = Path(folder)
    for n in candidate_names(stem, ext):
        p = folder / n
        if not os.path.lexists(p):
            return p
    raise OSError(errno.EEXIST, "Demasiados archivos con ese nombre", str(folder / stem))


# ---------------------------------------------------------------- carpetas
def home_label(p):
    """Ruta para mostrar: el home como '~'."""
    p = str(p)
    home = str(Path.home())
    if p == home:
        return "~"
    if p.startswith(home + os.sep):
        return "~" + p[len(home):]
    return p


def default_folder():
    """Carpeta de siempre: REELFY_EXPORT_DIR o ~/Movies/Reelfy (macOS) / ~/Videos/Reelfy."""
    env = os.environ.get("REELFY_EXPORT_DIR")
    if env:
        return Path(os.path.realpath(os.path.expanduser(env)))
    base = "Movies" if sys.platform == "darwin" else "Videos"
    return Path(os.path.realpath(Path.home() / base)) / "Reelfy"


def norm_folder(p):
    """Ruta absoluta canonica (sin '..', sin enlaces) o None."""
    if p is None:
        return None
    s = str(p).strip()
    if not s or "\x00" in s:
        return None
    s = os.path.expanduser(s)
    if not os.path.isabs(s):
        return None
    return str(Path(os.path.realpath(s)))


def _inside(p, root):
    try:
        p, root = Path(os.path.realpath(p)), Path(os.path.realpath(root))
    except (OSError, ValueError):
        return False
    return p == root or root in p.parents


def folder_status(path, data_dir=None, create=False):
    """{path, label, name, exists, writable, free_bytes, error} de una carpeta de destino.
    `create`: crea la carpeta si falta (solo la de siempre). Nunca una carpeta interna de Reelfy."""
    p = norm_folder(path)
    out = {"path": p, "label": home_label(p) if p else None, "name": Path(p).name if p else None,
           "exists": False, "writable": False, "free_bytes": None, "error": None}
    if not p:
        out["error"] = "Esa carpeta no es válida. Elige otra con «Cambiar…»."
        return out
    if data_dir and _inside(p, data_dir):
        out["error"] = "Esa carpeta es interna de Reelfy. Elige otra con «Cambiar…»."
        return out
    if create and not os.path.isdir(p):
        try:
            os.makedirs(p, exist_ok=True)
        except OSError:
            pass
    out["exists"] = os.path.isdir(p)
    nm = out["name"] or p
    if not out["exists"]:
        out["error"] = f"La carpeta «{nm}» ya no existe. Elige otra con «Cambiar…»."
        return out
    ok = os.access(p, os.W_OK | os.X_OK)
    if ok:
        try:
            with tempfile.NamedTemporaryFile(dir=p, prefix=".reelfy-", suffix=".tmp"):
                pass
        except OSError:
            ok = False
    out["writable"] = ok
    try:
        out["free_bytes"] = int(shutil.disk_usage(p).free)
    except OSError:
        pass
    if not ok:
        out["error"] = f"No se puede escribir en la carpeta «{nm}». Elige otra con «Cambiar…»."
    return out


# ---------------------------------------------------------------- copia al destino
_NO_LINK = {errno.EXDEV, errno.EPERM, errno.EACCES, errno.EMLINK, errno.ENOTSUP,
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP), errno.ENOSYS}
COPY_CHUNK = 8 << 20


def _clonefile(src, dst):
    """Clon APFS (copia instantanea que no ocupa mas disco ni comparte el archivo): EEXIST si el
    destino existe (no sobrescribe), EXDEV/ENOTSUP fuera del mismo volumen APFS."""
    if sys.platform != "darwin":
        raise OSError(errno.ENOTSUP, "clonefile solo en macOS")
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    fn = getattr(libc, "clonefile", None)
    if fn is None:
        raise OSError(errno.ENOTSUP, "clonefile no disponible")
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    fn.restype = ctypes.c_int
    if fn(os.fsencode(str(src)), os.fsencode(str(dst)), 0) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), str(dst))


def _copy_bytes(src, tmp, on_bytes=None, cancel=None):
    total = os.path.getsize(src)
    done = 0
    with open(src, "rb") as fi, open(tmp, "xb") as fo:
        while True:
            buf = fi.read(COPY_CHUNK)
            if not buf:
                break
            fo.write(buf)
            done += len(buf)
            if on_bytes:
                on_bytes(done, total)
            if cancel and cancel():
                raise InterruptedError("cancelado")
        fo.flush()
        os.fsync(fo.fileno())
    try:
        shutil.copystat(src, tmp)
    except OSError:
        pass


def place_file(src, folder, stem, ext=".mp4", on_bytes=None, cancel=None, allow_link=True):
    """Pone `src` en `folder` como "<stem><ext>" (o "<stem> (2)<ext>"...) SIN sobrescribir nada y
    sin salir de la carpeta -> (ruta, como): 'clone' (APFS), 'link' (enlace duro, mismo volumen) o
    'copy' (copia con progreso a un temporal oculto que se publica de forma atomica). Lanza OSError
    (ENOSPC, EACCES...) dejando la carpeta como estaba."""
    folder = Path(folder)
    src = Path(src)
    same_dev = False
    try:
        same_dev = os.stat(src).st_dev == os.stat(folder).st_dev
    except OSError:
        pass
    modes = (["clone", "link"] if (allow_link and same_dev) else []) + ["copy"]
    for mode in modes:
        if mode == "copy":
            break
        for n in candidate_names(stem, ext):
            dst = folder / n
            if dst.parent != folder:
                raise OSError(errno.EINVAL, "nombre fuera de la carpeta", n)
            if os.path.lexists(dst):
                continue
            try:
                if mode == "clone":
                    _clonefile(src, dst)
                else:
                    os.link(src, dst)
                return dst, mode
            except FileExistsError:
                continue
            except OSError as e:
                if e.errno == errno.EEXIST:
                    continue
                if e.errno in _NO_LINK:
                    break                      # este modo no aplica aqui: el siguiente
                raise
    tmp = folder / f".{stem[:60]}.{os.getpid()}.{threading.get_ident()}{PART_SUFFIX}"
    try:
        _copy_bytes(src, tmp, on_bytes, cancel)
        for n in candidate_names(stem, ext):
            dst = folder / n
            if os.path.lexists(dst):
                continue
            try:
                os.link(tmp, dst)              # publicar sin sobrescribir (atomico)
                return dst, "copy"
            except FileExistsError:
                continue
            except OSError as e:
                if e.errno == errno.EEXIST:
                    continue
                if e.errno not in _NO_LINK:
                    raise
            # sin enlaces duros (exFAT/FAT): reservar el nombre y reemplazar la reserva
            try:
                fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                continue
            os.close(fd)
            try:
                os.replace(tmp, dst)
            except BaseException:
                try:
                    os.unlink(dst)
                except OSError:
                    pass
                raise
            return dst, "copy"
        raise OSError(errno.EEXIST, "Demasiados archivos con ese nombre", str(folder / stem))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def save_error_text(e, folder_name):
    """Por que no se pudo guardar en la carpeta, en espanol y sin el texto del sistema (que va al
    registro): sin espacio, sin permiso, solo lectura, disco desconectado (EIO), nombre largo..."""
    nm = folder_name or "la carpeta"
    en = getattr(e, "errno", None)
    if is_no_space(e):
        return f"No hay espacio en «{nm}» para guardar el video"
    if en in (errno.EACCES, errno.EPERM):
        return f"Reelfy no tiene permiso para escribir en «{nm}»"
    if en == errno.EROFS:
        return f"«{nm}» es de solo lectura"
    if en == errno.EIO:
        return f"Se perdió la conexión con «{nm}» (¿se desconectó el disco?)"
    if en in (errno.ENOENT, errno.ENOTDIR, getattr(errno, "ENODEV", -1), getattr(errno, "ENXIO", -1),
              getattr(errno, "ESTALE", -1)):
        return f"La carpeta «{nm}» ya no está disponible (¿se desconectó el disco?)"
    if en == errno.ENAMETOOLONG:
        return f"El nombre del archivo es demasiado largo para «{nm}»: usa uno más corto"
    if en == errno.EFBIG:
        return (f"El video es demasiado grande para el disco de «{nm}» (en FAT32 cada archivo llega "
                "hasta 4 GB)")
    if en == errno.EEXIST:
        return f"Ya hay demasiados archivos con ese nombre en «{nm}»"
    return f"No se pudo guardar en «{nm}»"


PART_SUFFIX = ".reelfy-part"
PART_MAX_AGE = 15 * 60       # s sin escribir: una copia que sigue viva escribe cada pocos ms
_PART_RE = re.compile(r"^\..*\.(\d+)\.(\d+)" + re.escape(PART_SUFFIX) + r"$")
_PROBE_RE = re.compile(r"^\.reelfy-.*\.tmp$")          # prueba de escritura de folder_status


def _pid_alive(pid):
    if sys.platform.startswith("win"):
        return True                     # os.kill(pid, 0) en Windows TERMINA el proceso: no se usa
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, ValueError, OverflowError):
        return True
    return True


def sweep_parts(folder, max_age=PART_MAX_AGE, now=None):
    """Borra de `folder` (sin bajar a subcarpetas) las copias a medias que dejo un Reelfy cerrado
    o caido a mitad de "Guardando en «USB»…" (".<nombre>.<pid>.<hilo>.reelfy-part", ocultas: el
    usuario no las ve y pueden pesar GB) y las pruebas de escritura huerfanas. Una copia sigue
    viva mientras su proceso existe y escribe: se borra si su proceso ya no existe o si lleva
    `max_age` s sin cambiar. -> bytes liberados."""
    now = time.time() if now is None else now
    freed = 0
    try:
        it = list(os.scandir(folder))
    except OSError:
        return 0
    for e in it:
        m = _PART_RE.match(e.name)
        if not m and not _PROBE_RE.match(e.name):
            continue
        try:
            if not e.is_file(follow_symlinks=False):
                continue
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        stale = now - st.st_mtime > max_age
        if m and not stale:
            pid = int(m.group(1))
            stale = pid != os.getpid() and not _pid_alive(pid)
        if not stale:
            continue
        try:
            os.unlink(e.path)
            freed += st.st_size
        except OSError:
            pass
    return freed


def is_no_space(e):
    """El error es "sin espacio en el disco" (OSError ENOSPC o el texto de ffmpeg)."""
    if isinstance(e, OSError) and e.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return True
    t = str(e).lower()
    return "no space left" in t or "disk full" in t or "enospc" in t


# ---------------------------------------------------------------- selector nativo de carpetas
PICK_PROMPT = "Elige la carpeta donde Reelfy guardará tus videos"
_OSA_PICK = """on run argv
set p to item 1 of argv
set d to missing value
if (count of argv) > 1 then
try
set d to (POSIX file (item 2 of argv)) as alias
end try
end if
activate
if d is missing value then
set f to choose folder with prompt p
else
set f to choose folder with prompt p default location d
end if
return POSIX path of f
end run"""
_PS_PICK = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
    "$d.Description = $env:REELFY_PICK_PROMPT; $d.ShowNewFolderButton = $true;"
    "if ($env:REELFY_PICK_START) { $d.SelectedPath = $env:REELFY_PICK_START };"
    "$o = New-Object System.Windows.Forms.Form; $o.TopMost = $true;"
    "if ($d.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK) {"
    " [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::Out.Write($d.SelectedPath)"
    "} else { exit 1 }")


PICK_FAIL = "No se pudo abrir el selector de carpetas. Vuelve a intentarlo."
PICK_NONE = "No hay un selector de carpetas disponible en este equipo."


def picker_cmd(start=None, prompt=PICK_PROMPT, platform=None, which=shutil.which, osascript=None):
    """(cmd, env_extra, cancel_codes, cancel_marker) del selector nativo de carpetas de esta
    plataforma, o None. Cancelar = salir con un codigo de `cancel_codes` y, si hay `cancel_marker`,
    con ese texto en stderr: osascript sale con 1 tambien en sus errores (cancelar es el -128);
    zenity, kdialog y el FolderBrowserDialog salen con 1 SOLO al cancelar (y zenity/kdialog
    escriben avisos de GTK/Qt en stderr aunque todo vaya bien). REELFY_PICK_FOLDER_CMD = un
    ejecutable propio (pruebas): recibe [prompt, start] y escribe la carpeta (codigo 1 = cancelado).
    Los textos viajan como argumentos o variables (nunca dentro del script: sin inyeccion)."""
    over = os.environ.get("REELFY_PICK_FOLDER_CMD")
    if over:
        return [over, prompt, start or ""], {}, (1,), None
    platform = platform or sys.platform
    if platform == "darwin":
        osa = osascript or which("osascript") or "/usr/bin/osascript"
        cmd = [osa]
        for ln in _OSA_PICK.splitlines():
            cmd += ["-e", ln]
        return cmd + [prompt] + ([start] if start else []), {}, (1,), "-128"
    if platform.startswith("win"):
        ps = which("powershell") or which("pwsh")
        if not ps:
            return None
        return ([ps, "-NoProfile", "-STA", "-Command", _PS_PICK],
                {"REELFY_PICK_PROMPT": prompt, "REELFY_PICK_START": start or ""}, (1,), None)
    z = which("zenity")
    if z:
        cmd = [z, "--file-selection", "--directory", "--title", prompt]
        if start:
            cmd += ["--filename", str(start).rstrip("/") + "/"]
        return cmd, {}, (1,), None
    k = which("kdialog")
    if k:
        return [k, "--getexistingdirectory", start or str(Path.home()), "--title", prompt], {}, (1,), None
    return None


def run_picker(start=None, prompt=PICK_PROMPT, timeout=1800, log=print, **kw):
    """Abre el selector nativo y espera -> {"path": str|None, "canceled": bool, "error": str|None}.
    `error` es SIEMPRE una frase en espanol: el texto tecnico (stderr del selector) va a `log`."""
    spec = picker_cmd(start, prompt, **kw)
    if spec is None:
        return {"path": None, "canceled": False, "error": PICK_NONE}
    cmd, extra, cancel_codes, marker = spec
    env = dict(os.environ, **extra)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"path": None, "canceled": True, "error": None}
    except OSError as e:
        if log:
            log(f"   selector de carpetas: no se pudo lanzar {Path(str(cmd[0])).name}: {e}")
        return {"path": None, "canceled": False, "error": PICK_FAIL}
    out = (r.stdout or "").strip().strip("\r\n")
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if r.returncode in cancel_codes and (marker is None or marker in err):
            return {"path": None, "canceled": True, "error": None}
        if log:
            log(f"   selector de carpetas: salio con {r.returncode}: {err[-300:]}")
        return {"path": None, "canceled": False, "error": PICK_FAIL}
    if not out:
        return {"path": None, "canceled": True, "error": None}
    line = out.splitlines()[-1].strip()
    p = norm_folder(line)
    if not p:
        if log:
            log(f"   selector de carpetas: ruta invalida {line[-200:]!r}")
        return {"path": None, "canceled": False, "error": "El selector devolvió una carpeta inválida."}
    return {"path": p, "canceled": False, "error": None}


# ---------------------------------------------------------------- Mostrar en Finder / Abrir
def reveal_cmd(path, action="reveal", platform=None, which=shutil.which):
    """Comando para mostrar el archivo en su carpeta ('reveal') o abrirlo ('open'), o None (Windows
    'open' usa os.startfile). REELFY_OPEN_CMD = un ejecutable propio (pruebas): [action, path]."""
    over = os.environ.get("REELFY_OPEN_CMD")
    if over:
        return [over, action, str(path)]
    platform = platform or sys.platform
    path = str(path)
    if platform == "darwin":
        o = which("open") or "/usr/bin/open"
        return [o, "-R", path] if action == "reveal" else [o, path]
    if platform.startswith("win"):
        # "/select," y la ruta como argumentos SEPARADOS: list2cmdline deja `/select, "C:\a b\x.mp4"`
        # (lo que el Explorador entiende); "/select,<ruta>" junto se citaba entero si la ruta tenia
        # espacios ("/select,C:\...") y el Explorador abria Documentos en vez de marcar el archivo
        return ["explorer", "/select,", path] if action == "reveal" else None
    xo = which("xdg-open")
    if not xo:
        return None
    return [xo, os.path.dirname(path) if action == "reveal" else path]


def reveal_label(platform=None):
    """Texto del boton "Mostrar en Finder" de esta plataforma."""
    platform = platform or sys.platform
    if platform == "darwin":
        return "Mostrar en Finder"
    if platform.startswith("win"):
        return "Mostrar en el Explorador"
    return "Abrir carpeta"


def reveal_fail_msg(action="reveal", platform=None):
    """Frase (en espanol, con el nombre de la app de ESTA plataforma) cuando no se pudo mostrar o
    abrir el video."""
    platform = platform or sys.platform
    if action == "open":
        return "No se pudo abrir el video en este equipo."
    app = "el Finder" if platform == "darwin" else (
        "el Explorador de archivos" if platform.startswith("win") else "la carpeta del video")
    return f"No se pudo abrir {app} en este equipo."


def run_reveal(path, action="reveal"):
    """Lanza el Finder/Explorador (sin esperar) -> True si se lanzo."""
    cmd = reveal_cmd(path, action)
    try:
        if cmd is None:
            if sys.platform.startswith("win") and action == "open":
                os.startfile(str(path))       # noqa: solo Windows
                return True
            return False
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=not sys.platform.startswith("win"))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- ajustes recordados
class Store:
    """JSON de ajustes del export (DATA/export.json): ultimas elecciones, carpetas que dio el
    selector, archivos exportados (lo unico que /reveal acepta) y rendimiento medido. Escritura
    atomica; lectura-modificacion-escritura bajo un candado."""
    PICKED_MAX = 20
    EXPORTED_MAX = 500

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def load(self):
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, d):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        os.replace(tmp, self.path)

    def update(self, fn):
        with self.lock:
            d = self.load()
            fn(d)
            self.save(d)
            return d

    # -- carpetas permitidas: la de siempre, la recordada y las que dio el selector
    def allowed_folders(self):
        d = self.load()
        out = {str(default_folder())}
        last = (d.get("last") or {}).get("folder")
        if last:
            out.add(str(last))
        out.update(str(x) for x in d.get("picked") or [] if x)
        return {p for p in (norm_folder(x) for x in out) if p}

    def add_picked(self, folder):
        def f(d):
            lst = [x for x in d.get("picked") or [] if x != folder]
            d["picked"] = ([folder] + lst)[:self.PICKED_MAX]
            d.setdefault("last", {})["folder"] = folder
        return self.update(f)

    def remember(self, **kw):
        def f(d):
            last = d.setdefault("last", {})
            for k, v in kw.items():
                if v is not None:
                    last[k] = v
        return self.update(f)

    def add_exported(self, paths_, job_id):
        now = round(time.time(), 1)

        def f(d):
            new = [{"path": str(p), "job": job_id, "t": now} for p in paths_ if p]
            keep = {e["path"] for e in new}
            old = [e for e in d.get("exported") or [] if isinstance(e, dict) and e.get("path") not in keep]
            d["exported"] = (new + old)[:self.EXPORTED_MAX]
        return self.update(f)

    def is_exported(self, path):
        p = str(path)
        return any(isinstance(e, dict) and e.get("path") == p for e in self.load().get("exported") or [])

    def add_perf(self, kind, value, keep=12):
        if value is None or not math.isfinite(value) or value <= 0:
            return None

        def f(d):
            perf = d.setdefault("perf", {})
            perf[kind] = ([round(float(value), 6)] + list(perf.get(kind) or []))[:keep]
        return self.update(f)


# ---------------------------------------------------------------- estimacion
# segundos por cuadro por megapixel (medidos en una Mac M-series con 4 procesos del compositor):
# render con el base en cache ~0.0083 (1080x1920 y 2160x3840 por igual), base frio ~+0.006,
# recomponer las tomas ~0.0035 (4K: 70 s para 82 s de video), shorts (re-codificar) ~0.002
SPFM_DEFAULTS = {"render": 0.0085, "base": 0.006, "recompose": 0.0035, "shorts": 0.002}
COPY_BPS = 150e6           # copia a otro volumen (bytes/s) si no se pudo medir


def _median(xs):
    xs = sorted(float(x) for x in xs if isinstance(x, (int, float)) and math.isfinite(x) and x > 0)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def perf_value(perf, kind):
    """Mediana de lo medido (al menos 1 muestra) o el valor por defecto -> (valor, medido?)."""
    m = _median((perf or {}).get(kind) or [])
    if m is None:
        return SPFM_DEFAULTS.get(kind), False
    return m, True


def video_bps(W, H, fps, quality=DEFAULT_QUALITY):
    """Bitrate del video entregado (= video_io.encoder_args kind 'final' con la calidad elegida)."""
    bpp = QUALITY.get(quality, QUALITY[DEFAULT_QUALITY])["bpp"]
    return int(min(160e6, max(4e6, W * H * float(fps) * bpp)))


def fmt_minutes(seconds):
    s = max(0.0, float(seconds or 0))
    if s < 45:
        return "menos de 1 min"
    m = int(math.ceil(s / 60.0))
    if m < 60:
        return f"~{m} min"
    h, mm = divmod(m, 60)
    return f"~{h} h {mm} min" if mm else f"~{h} h"


def fmt_bytes(b):
    b = max(0.0, float(b or 0))
    if b >= 1e9:
        return f"~{b / 1e9:.1f} GB"
    mb = b / 1e6
    if mb >= 100:
        return f"~{int(round(mb, -1))} MB"
    return f"~{max(1, int(round(mb)))} MB"


def estimate(*, W, H, fps, frames, quality=DEFAULT_QUALITY, base_cold=False, base_wh=None,
             rec_frames=0, rec_wh=None, shorts_frames=0, perf=None, copy=False):
    """Estimacion del export -> {seconds, bytes, bytes_main, bytes_shorts, text, parts, basis}.
    `frames` = cuadros de la salida; `base_cold`: el video base no esta en cache (se arma a
    `base_wh`); `rec_frames`/`rec_wh`: cuadros y lienzo de la recomposicion (0 = no hay);
    `shorts_frames`: cuadros de los shorts; `copy`: el destino esta en otro volumen."""
    mpx = W * H / 1e6
    measured = []
    k_r, m_r = perf_value(perf, "render")
    measured.append(m_r)
    parts = {"render": frames * mpx * k_r}
    parts["base"] = 0.0
    if base_cold:
        bw, bh = base_wh or (W, H)
        k_b, m_b = perf_value(perf, "base")
        parts["base"] = frames * (bw * bh / 1e6) * k_b
        measured.append(m_b)
    parts["recompose"] = 0.0
    if rec_frames and rec_wh:
        k_c, m_c = perf_value(perf, "recompose")
        parts["recompose"] = rec_frames * (rec_wh[0] * rec_wh[1] / 1e6) * k_c
        measured.append(m_c)
    k_s, _ = perf_value(perf, "shorts")
    parts["shorts"] = shorts_frames * mpx * k_s
    bps = video_bps(W, H, fps, quality) + AUDIO_BPS
    ratio = _median((perf or {}).get("size_ratio") or []) or 1.0
    ratio = min(1.6, max(0.5, ratio))
    dur = frames / float(fps or 30)
    b_main = bps / 8.0 * dur * ratio
    b_sh = bps / 8.0 * (shorts_frames / float(fps or 30)) * ratio
    parts["save"] = (b_main + b_sh) / COPY_BPS if copy else 0.0
    secs = sum(parts.values()) + 3.0
    total = b_main + b_sh
    return {"seconds": int(round(secs)), "bytes": int(total), "bytes_main": int(b_main),
            "bytes_shorts": int(b_sh), "parts": {k: round(v, 1) for k, v in parts.items()},
            "basis": "medido" if all(measured) else "estimado",
            "text": f"Tardará {fmt_minutes(secs)} · {fmt_bytes(total)}"}
