#!/usr/bin/env python3
"""Composición de línea de tiempo (editor v1): arma varios videos/imágenes en la
PISTA PRINCIPAL (recortados y en orden) + capas OVERLAY encimadas, y produce UN
solo video. Ese video compuesto es la "fuente" que luego pasa por el pipeline de
IA (transcripción, subtítulos, cortes, etc.) manteniendo la línea de tiempo del
usuario + los cambios de la IA encima.

Enfoque robusto en 2 pasos (evita los problemas de un filter_complex gigante):
  1) Normalizar cada segmento de la pista principal a un archivo temporal con los
     MISMOS parámetros (resolución/fps/audio) -> concat demuxer los une sin re-encode.
     Los segmentos se normalizan de a COMPOSE_JOBS en paralelo (el Mac tiene 2 motores de
     codificacion; ffmpeg ya usa varios hilos para el tonemap).
  2) Componer las capas overlay sobre el video principal (pocas, manejable).

Lienzo: dims de display del primer video. Un lienzo VERTICAL se limita a 1.5x la salida
9:16 (1620x2880): el base del render es de 1080x1920 (la camara hace zoom sobre el base, no
sobre la fuente), asi que un 4K vertical (2160x3840) solo cuesta ~33 % mas CPU y disco sin
mejorar la salida. Horizontal/cuadrado no se limitan: el recorte 9:16 necesita su altura.

Disco: el intermedio NUNCA pesa mas que su fuente. El bitrate se limita a 1.2x el de la fuente
de mas bitrate del montaje (y a un maximo sano, REELFY_COMPOSE_MAX_MBPS): un 4K de iPhone a
25 Mbps se reescribia a ~75 Mbps (3x la fuente). Un UNICO video completo que ya cumple el
formato del intermedio (H.264/HEVC SDR bt709 8 bits, sin rotar, al tamano del lienzo, a los fps
del montaje, con audio, sin recortes ni fundidos ni volumen) no se re-codifica: el video se
copia tal cual (sin perdida, en segundos) y solo el audio se normaliza a AAC 48 kHz estereo.

MONTAJE CON IA ("Armar con IA": los clips principales traen "montage": {slot, cand, join, conf,
setup?}); sin ningun item de montaje todo queda EXACTAMENTE como antes:
  - Lienzo con la orientacion de la MAYORIA de los videos (nunca la del que quedo primero).
  - "vol" por segmento: numero (como siempre) o "auto"/None = nivel de la VOZ medido
    (speech_lufs, BS.1770 con compuertas) igualado a la mediana del montaje (level_gains); una
    ganancia > 1 pasa por un limitador (sin recortes digitales).
  - Micro-fundido SOLO de audio (MICRO_FADE, 10-20 ms) a cada lado de cada union: sin clic en el
    cambio de toma. fadeIn/fadeOut conservan su significado (fundido a/desde negro + audio).
  - info["shots"][i] lleva "join" (section|line|splice; None en la primera), slot, cand, conf,
    setup y sid; finish_plan() los pasa a plan.shots, arma plan.montage con el indice de toma de
    cada segmento, quita los cortes de tomas que cruzarian una union y siembra las secciones de
    plan.takes con las del guion.

RAMPAS DE VELOCIDAD en los cambios de camara (un item de video con "ramp_in"/"ramp_out", en el
item o en su "montage"; las decide montage.py): SOLO el VIDEO de un lado de la union corre a
`speed` x durante `dur` s de salida usando cuadros de mas de su fuente (la "manija"); el AUDIO es
exactamente [in, out] a 1x (con sus micro-fundidos) y la duracion no cambia, asi que la sincronia
sigue intacta fuera de la rampa (la rampa cae en la pausa entre frases).
  - "ramp_in"  {"dur": d, "speed": s}: el video empieza d*(s-1) s ANTES de "in" y corre a s x los
    primeros d s; luego 1x (ya alcanzo a su audio).
  - "ramp_out" {"dur": d, "speed": s}: el video sigue d*(s-1) s DESPUES de "out", comprimido en los
    ultimos d s del segmento.
  - Solo en una union entre dos clips sin crossfade y sin apagar ("ramp": false en el item que
    entra); una rampa por union (si llegan los dos lados gana la de salida). Sin manija suficiente
    se acorta a lo que hay (misma velocidad) y si queda < RAMP_MIN no se hace. info.shots[i] (el
    que ENTRA por la union) lleva "ramp": {"side": "in"|"out", "dur", "speed"} con lo que de
    verdad se hizo; finish_plan() lo pasa a plan.shots.
  - La rampa cae en la pausa de la union y el analisis corta esa pausa (silencio): finish_plan()
    recorta esos cortes de silencio para que no se coman la rampa (keep_ramps); lo demas de la pausa
    se sigue quitando.

RECURSOS DEL GUION (plan.montage.inserts, en s de la linea de tiempo del ensamble): finish_plan()
los lleva al video combinado por su ancla en la FUENTE (clip + segundo), asi un cambio de toma en
otra linea no los mueve, y escribe plan.fx.media (style "float_card", locked, src "script"); cada
insert gana t0/t1 (s del video combinado) y su start/end queda en la linea de tiempo compuesta.
Con spec["inserts_timeline"] == "main" los inserts YA estan en la linea de tiempo del ensamble que se
compone (los recoloco la UI sobre los clips actuales: FXCore.placeInserts) y se anclan en ella, no en
la linea de tiempo guardada del montaje (que puede ser la de antes de cambiar tomas).
"""
import copy
import math
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

import paths
from motion import video_io

FFMPEG = str(paths.FFMPEG)
FFPROBE = str(paths.FFPROBE)
FPS_DEFAULT = 30
# lienzo vertical maximo (1.5x la salida 9:16) y segmentos normalizados a la vez
MAX_VERTICAL = (int(os.environ.get("REELFY_COMPOSE_MAX_W", "1620")),
                int(os.environ.get("REELFY_COMPOSE_MAX_H", "2880")))
COMPOSE_JOBS = max(1, int(os.environ.get("REELFY_COMPOSE_JOBS", "2")))
# bitrate del intermedio: <= SRC_FACTOR x la fuente y <= MAX_BPS (4K30 "sin perdida" = ~75 Mbps)
SRC_FACTOR = 1.2
MAX_BPS = float(os.environ.get("REELFY_COMPOSE_MAX_MBPS", "60")) * 1e6
MIN_BPS = 500_000                # minimo tecnico del encoder
COPY_CODECS = ("h264", "hevc")   # se pueden copiar a mp4 sin re-codificar
# montaje con IA: tipos de union (contrato de /montage) y audio de las uniones
JOINS = ("section", "line", "splice")
MICRO_FADE = min(0.02, max(0.01, float(os.environ.get("REELFY_MICRO_FADE_MS", "15")) / 1000.0))
LEVEL_UP_DB = 15.0      # lo mas que se sube una toma (sin microfono: ~15 dB bajo las demas)
LEVEL_DOWN_DB = 12.0    # lo mas que se baja
LIMIT = 0.89            # techo (-1 dBFS: margen para el sobretiro del AAC) cuando una toma se sube
SHOT_KEYS = ("join", "slot", "cand", "conf", "setup", "sid", "ramp")   # de info.shots a plan.shots
# uniones exactas (segmentos de cuadros enteros + audio re-codificado al unir, ver compose()):
# "montage" (default: solo "Armar con IA"), "all" (tambien el ensamble manual) u "off"
EXACT_JOINS = os.environ.get("REELFY_COMPOSE_EXACT_JOINS", "montage").strip().lower()
# rampas de velocidad (solo video) en los cambios de camara: ver el docstring del modulo
RAMP_MIN = 0.10          # s de salida: una rampa mas corta (3 cuadros a 30 fps) no se nota
RAMP_MAX = 1.0           # s de salida
RAMP_SPEED_MAX = 4.0
RAMP_SHARE = 0.4         # cada rampa <= 40 % del segmento: las dos de un segmento nunca se enciman
# Sin desenfoque de movimiento en la rampa: medido con una barra en movimiento (obturador de 180
# grados, 30 y 60 fps), mezclar los cuadros que cubre cada cuadro de salida (tmix) deja la barra
# DOBLE (dos bordes, un fantasma) en vez de un barrido suave; el corte manual de referencia tampoco
# lo usa. Los cuadros de la rampa salen nitidos, como a 1x.
# recursos del guion en plan.fx.media
MEDIA_MIN = 0.5          # s: un recurso mas corto en el video combinado no se pone
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm")


def canvas_size(w, h):
    """Lienzo del montaje a partir de las dims de display del primer video (pares). Vertical
    (h > w): se escala para caber en MAX_VERTICAL conservando el aspecto; si no, tal cual."""
    w, h = int(w or 0), int(h or 0)
    if w > 0 and h > w:
        mw, mh = MAX_VERTICAL
        k = min(1.0, mw / w, mh / h)
        w, h = int(round(w * k)), int(round(h * k))
    return w - w % 2, h - h % 2


def _vbitrate(w, h, fps=FPS_DEFAULT, src_bps=None):
    """Bitrate del intermedio: visualmente sin perdida para cualquier resolucion (12M se quedaba
    corto en 4K: bloques en sombras y degradados), pero nunca mas de SRC_FACTOR x el de la fuente
    (`src_bps`; re-codificar a mas bitrate no agrega calidad, solo disco) ni de MAX_BPS."""
    br = min(float(video_io.hq_bitrate(w, h, fps)), MAX_BPS)
    if src_bps:
        br = min(br, SRC_FACTOR * float(src_bps))
    return str(int(max(MIN_BPS, br)))


@lru_cache(maxsize=64)
def _src_bitrate_cached(path, size, mtime):
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=bit_rate:format=bit_rate", "-of", "default=nw=1", path],
                       capture_output=True, text=True, timeout=30)
    vals = {}
    for ln in (r.stdout or "").splitlines():
        k, _, v = ln.partition("=")
        try:
            vals.setdefault(k.strip(), []).append(int(v))
        except ValueError:
            continue
    # ffprobe imprime primero el del stream y luego el del formato (ambos con la clave bit_rate):
    # el del stream es el del video; sin el (mkv/webm) el total del archivo, que lo incluye
    got = vals.get("bit_rate") or []
    return got[0] if got else None


def src_bitrate(path):
    """Bitrate del VIDEO de la fuente en bit/s (el total del archivo si el contenedor no lo
    dice), o None si no se puede saber (imagen, archivo ilegible)."""
    try:
        st = Path(path).stat()
        return _src_bitrate_cached(str(path), st.st_size, int(st.st_mtime_ns))
    except (OSError, subprocess.SubprocessError):
        return None


def spec_bitrate(items, w, h, fps=FPS_DEFAULT):
    """Bitrate UNICO de todos los segmentos del montaje (el concat sin re-codificar junta
    segmentos con los mismos parametros): tope = SRC_FACTOR x la fuente de video de MAS bitrate.
    Sin videos (solo imagenes) o sin poder medir alguno: el de siempre, con el maximo sano."""
    rates = []
    for s in items or []:
        if s.get("type", "video") == "image" or not s.get("path"):
            continue
        b = src_bitrate(s["path"])
        if not b:
            return _vbitrate(w, h, fps)
        rates.append(b)
    return _vbitrate(w, h, fps, max(rates) if rates else None)


def _color_tags():
    return list(video_io.COLOR_TAGS)


# propiedades de color del cuadro (sin esto el mp4 sale con primaries/transfer 'unknown':
# el filtro scale fija las del cuadro y el encoder ignora -color_primaries/-color_trc)
SETPARAMS = video_io.SETPARAMS_709


def _run(cmd):
    # run_ff reintenta con libx264 si videotoolbox falla (p. ej. lado > 4096 px).
    paths.run_ff(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


_DATE_MIN = 366 * 86400      # 1904/1970 = "sin fecha" en algunas camaras y exportadores


def _parse_date(raw):
    """Fecha de un contenedor (ISO 8601: '2026-09-29T14:09:40-0600' de Apple, '...Z' de
    creation_time) -> datetime con zona, o None (vacia, ilegible o anterior a 1971). Sin zona se
    toma UTC (la convencion de creation_time en MOV/MP4)."""
    from datetime import datetime, timezone
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt if dt.timestamp() >= _DATE_MIN else None


@lru_cache(maxsize=256)
def _created_cached(path, size, mtime_ns):
    """(iso, fuente, epoch) de la fecha de GRABACION en los metadatos, o None. Prioridad:
    com.apple.quicktime.creationdate (hora local del iPhone con su zona; sobrevive a AirDrop y a la
    copia), creation_time del contenedor y el del primer stream de video."""
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                            "format_tags=com.apple.quicktime.creationdate,creation_time:"
                            "stream_tags=creation_time", "-select_streams", "v:0", "-of", "json",
                            path], capture_output=True, text=True, timeout=30)
        import json
        d = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    ft = (d.get("format") or {}).get("tags") or {}
    st = ((d.get("streams") or [{}])[0] or {}).get("tags") or {}
    for src, raw in (("apple", ft.get("com.apple.quicktime.creationdate")),
                     ("creation_time", ft.get("creation_time")),
                     ("creation_time", st.get("creation_time"))):
        dt = _parse_date(raw)
        if dt is not None:
            return dt.isoformat(timespec="seconds"), src, round(dt.timestamp(), 3)
    return None


def created(path):
    """Fecha de grabacion de un archivo -> {"created": ISO 8601, "created_src":
    "apple"|"creation_time"|"mtime", "created_ts": epoch}. Sin metadatos: la fecha de modificacion
    del archivo (hora local con zona). El orden de grabacion de una sesion sale de aqui."""
    from datetime import datetime, timezone
    p = Path(path)
    try:
        stt = p.stat()
    except OSError:
        return {"created": None, "created_src": None, "created_ts": None}
    got = _created_cached(str(p), stt.st_size, stt.st_mtime_ns)
    if got is None:
        dt = datetime.fromtimestamp(stt.st_mtime, tz=timezone.utc).astimezone()
        got = (dt.isoformat(timespec="seconds"), "mtime", round(stt.st_mtime, 3))
    return {"created": got[0], "created_src": got[1], "created_ts": got[2]}


def probe(path):
    """dims de DISPLAY (rotación aplicada: un MOV de iPhone 3840x2160 con rotation -90
    es 2160x3840), duración, si tiene audio, si es HDR (HLG/PQ: 'hlg'|'pq'|None), rotacion,
    fps (promedio; ~59.97 en un iPhone VFR) y fecha de grabacion (`created`, ver created())."""
    i = video_io.probe(path)
    if not i.get("w") and not i.get("duration"):
        return {"w": 0, "h": 0, "dur": 0.0, "audio": False, "type": "video",
                "rotation": 0, "hdr": None, "fps": 0.0, **created(path)}
    return {"w": i["w"], "h": i["h"], "dur": round(i["duration"], 3), "audio": i["has_audio"],
            "type": "video" if i["duration"] > 0 else "image", "rotation": i["rotation"],
            "hdr": i["hdr"], "fps": round(float(i.get("fps") or 0.0), 3), **created(path)}


def _has_audio(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def _audio_filters(dur, vol=1.0, fin=0.0, fout=0.0, afin=0.0, afout=0.0, limit=False):
    """Cadena de audio de un segmento de video: volumen, 48 kHz, limitador opcional (una toma
    que se SUBE no recorta), fundidos del clip (fin/fout, junto con el video) y micro-fundidos
    SOLO de audio en las uniones del montaje (afin/afout; los tapa un fundido mayor del clip)."""
    af = [f"volume={max(0.0, float(vol)):.3f}", "aresample=48000"]
    if limit:
        # sin auto-nivel (level=0: no cambia la sonoridad) y con la latencia compensada (el audio
        # no se corre `attack` ms respecto al video)
        af.append(f"alimiter=limit={LIMIT}:level=0:latency=1")
    if fin > 0.01:
        af.append(f"afade=t=in:st=0:d={fin:.3f}")
    elif afin > 0.001:
        af.append(f"afade=t=in:st=0:d={afin:.3f}:curve=qsin")
    if fout > 0.01:
        af.append(f"afade=t=out:st={dur - fout:.3f}:d={fout:.3f}")
    elif afout > 0.001:
        af.append(f"afade=t=out:st={max(0.0, dur - afout):.3f}:d={afout:.3f}:curve=qsin")
    return af


def _seg_dur(dur, fps, exact=False):
    """(duracion, texto de -t) de un segmento. `exact` (montaje con IA): un numero ENTERO de
    cuadros y el audio con exactamente esas muestras (48000/30 = 1600 por cuadro), para que el
    audio se una sin huecos ni deriva (ver compose: el concat re-codifica solo el audio)."""
    dur = float(dur)
    if not exact:
        return dur, f"{dur:.3f}"
    n = max(1, int(round(dur * float(fps))))
    d = n / float(fps)
    return d, f"{d:.6f}"


@lru_cache(maxsize=64)
def _video_end_cached(path, size, mtime_ns):
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=duration:format=duration", "-of", "default=nw=1", path],
                       capture_output=True, text=True, timeout=30)
    vals = []
    for ln in (r.stdout or "").splitlines():
        try:
            v = float(ln.partition("=")[2])
        except ValueError:
            continue
        if math.isfinite(v) and v > 0:
            vals.append(v)
    # el del stream de video (el contenedor puede durar mas por la cola del audio)
    return min(vals) if vals else None


def video_end(path):
    """Segundo en que termina el VIDEO de la fuente (el menor entre el stream y el contenedor), o
    None si no se puede saber: el limite de la manija de una rampa de salida."""
    try:
        st = Path(path).stat()
        return _video_end_cached(str(path), st.st_size, st.st_mtime_ns)
    except (OSError, subprocess.SubprocessError):
        return None


def ramp_fit(t_in, dur, fps, src_end=None, ramp_in=None, ramp_out=None):
    """Rampas EFECTIVAS de un segmento que toma [t_in, t_in + dur] de su fuente (`dur` s de salida)
    a partir de las pedidas ({"dur", "speed"} o None). La manija de entrada es lo que hay antes de
    t_in; la de salida, lo que hay entre t_in + dur y `src_end` (fin del video; None = no se sabe:
    sin rampa de salida). Sin manija suficiente se acorta la rampa (misma velocidad); cada una se
    limita a RAMP_SHARE del segmento, se redondea a cuadros de salida enteros y si queda < RAMP_MIN
    no se hace. -> {"in": {"dur", "speed", "pre", "req"} | None, "out": {"dur", "speed", "post",
    "req"} | None} (pre/post: s de fuente de mas antes de "in" / despues de "out")."""
    fps = float(fps)
    dur = float(dur)

    def fit(req, handle):
        if not req:
            return None
        sp = float(req["speed"])
        d = min(float(req["dur"]), RAMP_SHARE * dur, max(0.0, float(handle)) / (sp - 1.0))
        d = math.floor(d * fps + 1e-6) / fps
        if d < RAMP_MIN - 1e-9:
            return None
        return {"dur": d, "speed": sp, "req": float(req["dur"])}

    rin = fit(ramp_in, max(0.0, float(t_in)))
    if rin:
        rin["pre"] = rin["dur"] * (rin["speed"] - 1.0)
    rout = fit(ramp_out, (float(src_end) - (float(t_in) + dur)) if src_end else 0.0)
    if rout:
        rout["post"] = rout["dur"] * (rout["speed"] - 1.0)
    return {"in": rin, "out": rout}


def _ramp_expr(dur, rin=None, rout=None):
    """Expresion de setpts del remapeo de tiempo del VIDEO (T = s desde el inicio de la lectura, que
    arranca `pre` s antes de "in"): a `speed` x en las rampas, 1x en medio. El resultado se redondea
    al tick (+0.5: setpts trunca) para que un cuadro que cae justo a la mitad entre dos de salida
    siempre se resuelva igual."""
    pre = float(rin["pre"]) if rin else 0.0
    body = f"T-{pre:.9f}" if pre > 0 else "T"
    if rout:
        t1 = float(dur) - float(rout["dur"])            # s de salida donde empieza la rampa
        u1 = pre + t1                                   # ... y su s de lectura
        body = f"if(lt(T,{u1:.9f}),{body},{t1:.9f}+(T-{u1:.9f})/{float(rout['speed']):.9f})"
    if rin:
        a = float(rin["dur"]) * float(rin["speed"])     # s de lectura que cubre la rampa
        body = f"if(lt(T,{a:.9f}),T/{float(rin['speed']):.9f},{body})"
    return f"setpts='({body})/TB+0.5'"


def _norm_video(src, t_in, t_out, out, w, h, fps, vol=1.0, fin=0.0, fout=0.0, br=None,
                afin=0.0, afout=0.0, limit=False, exact=False, ramp_in=None, ramp_out=None):
    """Recorta [in,out] y normaliza a WxH/fps con audio 48k estéreo (silencio si
    no tiene). cover-crop para llenar el lienzo. `vol` volumen (0=mudo); `fin`/`fout`
    desvanecen VIDEO (a/desde negro) y AUDIO al inicio/fin del clip. `br` bitrate del
    montaje (spec_bitrate); sin el, el tope de esta fuente. `afin`/`afout`: micro-fundido
    SOLO de audio (uniones del montaje); `limit`: limitador tras el volumen (vol > 1);
    `exact`: duracion de cuadros enteros (_seg_dur). `ramp_in`/`ramp_out` ({"dur", "speed"}):
    rampas de velocidad SOLO del video (ver ramp_fit); el audio sale identico al de sin rampa.
    Devuelve las rampas efectivas (ramp_fit) o None si no se hizo ninguna."""
    dur, tdur = _seg_dur(max(0.1, t_out - t_in), fps, exact)
    fin = max(0.0, min(float(fin), dur / 2)); fout = max(0.0, min(float(fout), dur / 2))
    afin = max(0.0, min(float(afin), dur / 4)); afout = max(0.0, min(float(afout), dur / 4))
    info = video_io.probe(src)
    ramps = None
    if ramp_in or ramp_out:
        t0 = float(f"{float(t_in):.3f}")                # el mismo "in" que lee el audio
        ramps = ramp_fit(t0, dur, fps, video_end(src) if ramp_out else None, ramp_in, ramp_out)
        if not (ramps["in"] or ramps["out"]):
            ramps = None
    # ffmpeg autorrota al decodificar (los cuadros ya llegan en orientación de display);
    # la escala conserva 10 bits y el tonemap HLG/PQ -> SDR bt709 va ANTES de bajar a 8 bits.
    # Uniones exactas (montaje) y rampas: fps arranca en 0 (start_time=0). Con un "in" fuera de la
    # rejilla de cuadros el primer cuadro tras el -ss cae a mas de medio cuadro y fps empezaba en el
    # cuadro 1: el video del segmento empezaba un cuadro tarde y en el video combinado ese corte se
    # veia 33 ms despues que su audio (en ~la mitad de las uniones de un montaje real).
    fps_f = f"fps={fps}:start_time=0" if (exact or ramps) else f"fps={fps}"
    vf = [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos",
          f"crop={w}:{h}", fps_f, "setsar=1"] + video_io.tonemap_filters(info)
    af = _audio_filters(dur, vol, fin, fout, afin, afout, limit)
    if fin > 0.01:
        vf.append(f"fade=t=in:st=0:d={fin:.3f}")
    if fout > 0.01:
        vf.append(f"fade=t=out:st={dur - fout:.3f}:d={fout:.3f}")
    if ramps:
        # el video se lee desde `pre` s antes (otra entrada del MISMO archivo) y se remapea antes de
        # escalar; fps= luego toma el cuadro mas cercano a cada tiempo de salida. El audio es la
        # entrada 1 con el mismo -ss de siempre.
        pre = ramps["in"]["pre"] if ramps["in"] else 0.0
        v0 = max(0.0, t0 - pre)
        vf = [_ramp_expr(dur, ramps["in"], ramps["out"])] + vf
        cmd = [FFMPEG, "-y"] + video_io.hwaccel_args() + ["-ss", f"{v0:.6f}", "-i", str(src)]
        if not info.get("has_audio"):
            cmd += ["-f", "lavfi", "-t", tdur, "-i", "anullsrc=r=48000:cl=stereo"]
        else:
            cmd += ["-ss", f"{t_in:.3f}", "-i", str(src)]
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-t", tdur]
    else:
        cmd = [FFMPEG, "-y"] + video_io.hwaccel_args() + ["-ss", f"{t_in:.3f}", "-i", str(src)]
        if not info.get("has_audio"):
            cmd += ["-f", "lavfi", "-t", tdur, "-i", "anullsrc=r=48000:cl=stereo",
                    "-map", "0:v:0", "-map", "1:a:0"]
        else:
            # 1a pista de audio explícita (los iPhone traen una 2a pista APAC espacial)
            cmd += ["-map", "0:v:0", "-map", "0:a:0"]
        # -t de SALIDA (despues de las entradas): antes quedaba como opcion de la entrada de
        # silencio y un video sin audio salia desde "in" hasta el final de su fuente
        cmd += ["-t", tdur]
    cmd += ["-vf", ",".join(vf), "-af", ",".join(af), "-c:v", "h264_videotoolbox",
            "-b:v", br or _vbitrate(w, h, fps, src_bitrate(src))] + _color_tags() + [
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", str(out)]
    _run(cmd)
    return ramps


def _norm_image(src, dur, out, w, h, fps, fin=0.0, fout=0.0, br=None, exact=False):
    """Imagen fija -> clip de `dur` s a WxH/fps con audio en silencio (+ fade opcional)."""
    dur, tdur = _seg_dur(dur, fps, exact)
    fin = max(0.0, min(float(fin), dur / 2)); fout = max(0.0, min(float(fout), dur / 2))
    # RGB -> YUV con matriz bt709 explícita (coincide con la etiqueta de color del intermedio)
    vf = [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos:"
          "out_color_matrix=bt709:out_range=tv",
          f"crop={w}:{h}", f"fps={fps}", "setsar=1", "format=yuv420p"]
    if fin > 0.01:
        vf.append(f"fade=t=in:st=0:d={fin:.3f}")
    if fout > 0.01:
        vf.append(f"fade=t=out:st={dur - fout:.3f}:d={fout:.3f}")
    vf = ",".join(vf + [SETPARAMS])
    _run([FFMPEG, "-y", "-loop", "1", "-t", tdur, "-i", str(src),
          "-f", "lavfi", "-t", tdur, "-i", "anullsrc=r=48000:cl=stereo",
          "-vf", vf, "-c:v", "h264_videotoolbox", "-b:v", br or _vbitrate(w, h, fps)] + _color_tags() + [
          "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", "-shortest", str(out)])


def _norm_black(dur, out, w, h, fps, br=None, exact=False):
    """Segmento NEGRO con silencio (para rellenar huecos de posicionamiento libre)."""
    _dur, tdur = _seg_dur(dur, fps, exact)
    _run([FFMPEG, "-y", "-f", "lavfi", "-t", tdur, "-i",
          f"color=c=black:s={w}x{h}:r={fps}",
          "-f", "lavfi", "-t", tdur, "-i", "anullsrc=r=48000:cl=stereo",
          "-vf", f"format=yuv420p,{SETPARAMS}", "-c:v", "h264_videotoolbox", "-b:v", br or _vbitrate(w, h, fps)] + _color_tags() + [
          "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", "-shortest", str(out)])


def _overlay_xy(pos, W, H, mx, my):
    return {
        "tl": (f"{mx}", f"{my}"), "tr": (f"W-w-{mx}", f"{my}"),
        "bl": (f"{mx}", f"H-h-{my}"), "br": (f"W-w-{mx}", f"H-h-{my}"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }.get(pos, ("(W-w)/2", "(H-h)/2"))


def _concat_xfade(parts, trans, out, w=1080, h=1920, fps=FPS_DEFAULT, br=None):
    """Une los segmentos con TRANSICIONES: crossfade (xfade+acrossfade) en los
    bordes que lo pidan; los demás bordes usan un xfade de ~1 frame (corte limpio),
    para mantener un solo grafo uniforme. Re-encoda (necesario para mezclar)."""
    durs = [probe(p)["dur"] for p in parts]
    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]
    fc = []
    vprev, aprev = "[0:v]", "[0:a]"
    acc = durs[0]
    for i in range(1, len(parts)):
        t = trans[i] if i < len(trans) else None
        cross = bool(t and t.get("type") == "crossfade" and float(t.get("dur", 0)) > 0.05)
        T = float(t.get("dur")) if cross else 0.04
        T = max(0.03, min(T, durs[i] - 0.05, acc - 0.05))
        off = acc - T
        vo, ao = f"[vx{i}]", f"[ax{i}]"
        fc.append(f"{vprev}[{i}:v]xfade=transition=fade:duration={T:.3f}:offset={off:.3f}{vo}")
        fc.append(f"{aprev}[{i}:a]acrossfade=d={T:.3f}{ao}")
        vprev, aprev = vo, ao
        acc = acc + durs[i] - T
    fc.append(f"{vprev}format=yuv420p,{SETPARAMS}[vout]")
    _run([FFMPEG, "-y", *inputs, "-filter_complex", ";".join(fc),
          "-map", "[vout]", "-map", aprev, "-c:v", "h264_videotoolbox",
          "-b:v", br or _vbitrate(w, h, fps)] + _color_tags() + [
          "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", str(out)])


def _shots(seq, parts, trans):
    """Limites reales de cada segmento de la pista principal en el video compuesto.

    Replica la aritmetica del montaje: con concat demuxer (sin transiciones) cada segmento
    empieza donde acaba el anterior (duracion del contenedor); con xfade (hay algun
    crossfade) CADA union se superpone T s (el crossfade pedido o 0.04 s) y el limite queda
    a la MITAD de la superposicion. Devuelve [{start, end, src, index, kind}] (kind "clip" o
    "black"), en segundos del video compuesto."""
    durs = [float(probe(p)["dur"] or 0.0) for p in parts]
    has_trans = len(parts) > 1 and any(
        t and t.get("type") == "crossfade" and float(t.get("dur", 0)) > 0.05 for t in trans[1:])
    out, acc = [], 0.0
    for i, (kind, data, _tr) in enumerate(seq):
        d = durs[i]
        if i == 0:
            start, acc = 0.0, d
        elif has_trans:
            t = trans[i] if i < len(trans) else None
            cross = bool(t and t.get("type") == "crossfade" and float(t.get("dur", 0)) > 0.05)
            T = float(t.get("dur")) if cross else 0.04
            T = max(0.03, min(T, d - 0.05, acc - 0.05))
            start = acc - T / 2
            acc = acc + d - T
        else:
            start, acc = acc, acc + d
        if out:
            out[-1]["end"] = round(start, 3)
        src = "" if kind == "black" else Path(str(data.get("name") or data.get("path") or "")).name
        out.append({"start": round(start, 3), "end": round(acc, 3), "src": src, "index": i,
                    "kind": kind})
    return out


def copy_source(seq, spec, W, H, fps):
    """Si el montaje es UN solo video completo que ya cumple el formato del intermedio, su probe
    (se copia el video sin re-codificar); si no, None. Conservador: cualquier procesado (recorte,
    fundido, volumen, capas, otro lienzo, HDR, rotacion, otros fps, sin audio) re-codifica."""
    if len(seq) != 1 or seq[0][0] != "clip" or spec.get("overlays"):
        return None
    s = seq[0][1]
    if s.get("type", "video") != "video" or not s.get("path") or s.get("trans"):
        return None
    if float(s.get("start", 0) or 0) > 0.05 or s.get("mute") or \
            abs(float(s.get("vol", 1.0) if s.get("vol") is not None else 1.0) - 1.0) > 1e-3:
        return None
    if float(s.get("fadeIn", 0) or 0) > 0.01 or float(s.get("fadeOut", 0) or 0) > 0.01:
        return None
    i = video_io.probe(s["path"])
    dur = float(i.get("duration") or 0)
    t_in, t_out = float(s.get("in", 0) or 0), float(s.get("out", 0) or 0)
    if dur <= 0 or t_in > 0.05 or t_out < dur - 0.05:
        return None
    if i.get("codec") not in COPY_CODECS or i.get("pix_fmt") != "yuv420p" or i.get("hdr") or \
            i.get("dovi") or int(i.get("rotation") or 0) != 0 or not i.get("has_audio"):
        return None
    if (int(i.get("w") or 0), int(i.get("h") or 0)) != (int(W), int(H)):
        return None
    if abs(float(i.get("fps") or 0) - float(fps)) > 0.05:
        return None
    if video_io.source_matrix(i) != ("bt709", "tv"):
        return None
    return i


def _copy_video(src, out, info):
    """Video tal cual (sin perdida, sin re-codificar) + primera pista de audio normalizada a
    AAC 48 kHz estereo (como los segmentos normalizados). Las pistas de datos del iPhone (mebx) y
    la 2a pista de audio espacial no se copian."""
    tag = ["-tag:v", "hvc1"] if info.get("codec") == "hevc" else []
    _run([FFMPEG, "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", *tag,
          "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", str(out)])


# ---------------------------------------------------------------- montaje con IA
def is_montage(spec):
    """El montaje viene de "Armar con IA": algun clip principal trae su "montage" (o el spec trae
    el plan del montaje). Sin esto compose() se comporta exactamente como siempre."""
    spec = spec or {}
    if isinstance(spec.get("montage"), dict):
        return True
    return any(isinstance(s, dict) and isinstance(s.get("montage"), dict)
               for s in spec.get("main") or [])


def _orientation(w, h):
    w, h = int(w or 0), int(h or 0)
    return "vertical" if h > w else ("horizontal" if w > h else "cuadrado")


def majority_canvas(items):
    """Dims de DISPLAY (rotacion aplicada) del lienzo segun la orientacion de la MAYORIA de los
    videos: gana la orientacion con mas clips (empate: mas segundos; empate: la del primero) y el
    lienzo toma las dims del primer video con esa orientacion. None sin videos legibles."""
    vids = []
    for s in items or []:
        if not s.get("path") or s.get("type", "video") == "image":
            continue
        p = probe(s["path"])
        if p.get("type") != "video" or not p.get("w") or not p.get("h"):
            continue
        ln = float(s.get("out", 0) or 0) - float(s.get("in", 0) or 0)
        vids.append((_orientation(p["w"], p["h"]), (p["w"], p["h"]), ln if ln > 0 else p["dur"]))
    if not vids:
        return None
    tally = {}
    for k, (o, _wh, ln) in enumerate(vids):
        c, secs, first = tally.get(o, (0, 0.0, k))
        tally[o] = (c + 1, secs + float(ln or 0.0), first)
    best = max(tally, key=lambda o: (tally[o][0], round(tally[o][1], 3), -tally[o][2]))
    return next(wh for o, wh, _ln in vids if o == best)


def _pcm(src, t_in=0.0, t_out=None, sr=48000, ch=1):
    """Audio float (mono, o (n, ch)) a `sr` del tramo [t_in, t_out] de `src` (primera pista de
    audio: los iPhone traen una 2a pista APAC espacial). None si no hay audio o no se lee."""
    import numpy as np
    cmd = [FFMPEG, "-v", "error", "-nostdin"]
    if t_in and float(t_in) > 0:
        cmd += ["-ss", f"{float(t_in):.3f}"]
    cmd += ["-i", str(src)]
    if t_out is not None:
        cmd += ["-t", f"{max(0.01, float(t_out) - float(t_in or 0.0)):.3f}"]
    cmd += ["-map", "0:a:0", "-vn", "-ac", str(int(ch)), "-ar", str(sr), "-f", "f32le", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    x = np.frombuffer(r.stdout, dtype=np.float32).astype(np.float64)
    return x if int(ch) == 1 else x[:len(x) - len(x) % int(ch)].reshape(-1, int(ch))


def speech_lufs(src, t_in=0.0, t_out=None, words=None, audio=None):
    """Sonoridad de la VOZ del tramo [t_in, t_out] (s de la fuente) en LUFS: BS.1770 (filtro K,
    bloques de 400 ms con compuerta absoluta -70 y relativa -10 LU) sobre la primera pista de
    audio. Con `words` ([{start, end}] en s de la fuente) solo cuentan los sub-bloques de 100 ms
    que caen en palabras: las pausas y el ruido de fondo no mueven la medida. `audio`: mono 48 kHz
    (o (n, 2)) ya leido del tramo, sin leer la fuente. None sin audio, sin voz o tramo < 0.1 s."""
    import math
    import numpy as np
    from motion import audio as A
    # estereo: BS.1770 suma la potencia de cada canal (una mezcla a mono la cambiaria segun la
    # correlacion entre canales)
    x = audio if audio is not None else _pcm(src, t_in, t_out, A.SR, ch=2)
    if x is None:
        return None
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 2:
        x = x.reshape(-1)
    sub = A._kw_sub(x)                              # energia K por sub-bloque de 100 ms
    if sub.size == 0:
        return None
    t0 = float(t_in or 0.0)
    keep = np.ones(sub.size, dtype=bool)
    spans = []
    for w in words or []:
        try:
            a, b = float(w["start"]) - t0, float(w["end"]) - t0
        except (KeyError, TypeError, ValueError):
            continue
        if b > 0 and a < sub.size * 0.1:
            spans.append((max(0.0, a), b))
    if spans:
        keep[:] = False
        for a, b in spans:
            keep[int(a / 0.1):max(int(a / 0.1) + 1, int(math.ceil(b / 0.1)))] = True
    if spans and keep.any():
        # solo los sub-bloques de 100 ms con palabras (un bloque de 400 ms mezclaria la pausa o el
        # ruido de al lado), con las mismas compuertas
        v = A._gated(sub[keep] / A._SUB)
    elif sub.size >= 4:
        v = A._gated(A._win_power(sub, 4))          # BS.1770: 400 ms, paso 100 ms
    else:
        v = A._gated(np.array([sub.sum() / (sub.size * A._SUB)]))
    return float(v) if math.isfinite(v) else None


def level_gains(levels, target=None, up_db=LEVEL_UP_DB, down_db=LEVEL_DOWN_DB):
    """`vol` lineal por segmento para que TODOS suenen igual de fuerte: cada nivel (LUFS de la voz,
    None = sin medir) se lleva a `target` (default: la mediana de los medidos, asi el montaje
    conserva su sonoridad y la toma tipica queda igual). Tope de subida/bajada en dB; sin medida
    -> 1.0."""
    import numpy as np
    vals = [float(v) for v in levels if v is not None]
    if not vals:
        return [1.0 for _ in levels]
    tgt = float(np.median(vals)) if target is None else float(target)
    out = []
    for v in levels:
        if v is None:
            out.append(1.0)
            continue
        g = max(-float(down_db), min(float(up_db), tgt - float(v)))
        out.append(round(10.0 ** (g / 20.0), 4))
    return out


def _is_auto_vol(s):
    v = s.get("vol")
    return v == "auto" or (v is None and isinstance(s.get("montage"), dict))


def _item_lufs(s):
    """LUFS de la voz del tramo de un item (None si no se puede medir)."""
    t_in = float(s.get("in", 0) or 0)
    t_out = float(s.get("out", t_in + 5) or t_in + 5)
    try:
        return speech_lufs(s["path"], t_in, t_out, words=s.get("words"))
    except Exception as e:  # noqa: medir es una mejora; sin medida el clip queda a 1.0
        print(f"   compose: nivel de voz no medido ({e})")
        return None


def _numeric_vol(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) and v > 0 else None


def auto_levels(items):
    """Resuelve los `vol` "auto" (o ausentes en un clip del montaje) midiendo la voz de cada tramo.
    Objetivo: si hay tramos del montaje con `vol` numerico (el montaje ya los nivelo), la mediana de
    su sonoridad YA con su ganancia (una toma cambiada en el ensamble suena igual que sus vecinas);
    si no, los "auto" se nivelan entre si (level_gains). Modifica los items y devuelve
    [(indice, lufs, vol)]."""
    todo = []
    for k, s in enumerate(items):
        if not _is_auto_vol(s):
            continue
        if s.get("type", "video") == "image" or s.get("mute") or not s.get("path"):
            s["vol"] = 1.0
            continue
        todo.append((k, _item_lufs(s)))
    target = None
    if any(lv is not None for _k, lv in todo):
        auto_ix = {k for k, _lv in todo}
        refs = []
        for k, s in enumerate(items):
            vol = _numeric_vol(s.get("vol"))
            if k in auto_ix or vol is None or not isinstance(s.get("montage"), dict) or s.get("mute") or \
                    s.get("type", "video") == "image" or not s.get("path"):
                continue
            lv = _item_lufs(s)
            if lv is not None:
                refs.append(lv + 20.0 * math.log10(vol))
        if refs:
            import numpy as np
            target = float(np.median(refs))
    gains = level_gains([lv for _k, lv in todo], target=target)
    out = []
    for (k, lv), g in zip(todo, gains):
        items[k]["vol"] = g
        if lv is not None:
            items[k]["_lufs"] = round(lv, 2)
        out.append((k, None if lv is None else round(lv, 2), g))
    return out


def _cand_index(montage):
    """{(slot, cand): candidato} del plan del montaje (para su setup/camara)."""
    idx = {}
    for sl in (montage or {}).get("slots") or []:
        if not isinstance(sl, dict):
            continue
        for c in sl.get("cands") or []:
            if isinstance(c, dict):
                idx[(sl.get("id"), c.get("id"))] = c
    return idx


def _annotate_shots(shots, seq, montage=None):
    """Pasa a cada toma de info.shots lo que el montaje sabe de su segmento: join (la union con el
    segmento ANTERIOR; la primera nunca tiene), slot, cand, conf, setup y sid."""
    cands = _cand_index(montage)
    for sh in shots or []:
        i = int(sh.get("index", -1))
        if not (0 <= i < len(seq)) or seq[i][0] != "clip":
            continue
        data = seq[i][1]
        m = data.get("montage")
        if not isinstance(m, dict):
            continue
        if data.get("sid"):
            sh["sid"] = data["sid"]
        j = m.get("join")
        sh["join"] = j if (j in JOINS and i > 0) else None
        for k in ("slot", "cand", "conf", "setup"):
            if m.get(k) is not None:
                sh[k] = m[k]
        if sh.get("setup") is None:
            c = cands.get((m.get("slot"), m.get("cand")))
            if c and c.get("setup") is not None:
                sh["setup"] = c["setup"]


def seg_rows(shots, seq, at, seg_len):
    """Lo que el mapeo de recursos del guion necesita de cada segmento del video combinado (va en
    info["segs"], aparte de info.shots): start/end (s del video combinado), kind, at/len (donde
    estaba en la linea de tiempo del ensamble), sid, in (s de su fuente; solo videos), slot, cand."""
    out = []
    for sh in shots or []:
        i = int(sh.get("index", -1))
        if not (0 <= i < len(seq)) or i >= len(at):
            continue
        kind, data, _tr = seq[i]
        r = {"index": i, "start": sh["start"], "end": sh["end"], "kind": kind,
             "at": round(float(at[i]), 6), "sid": None, "in": None, "slot": None, "cand": None}
        if kind == "black":
            r["len"] = round(float(data), 6)
        else:
            r["len"] = round(float(seg_len(data)), 6)
            r["sid"] = data.get("sid")
            if data.get("type", "video") != "image":
                r["in"] = float(data.get("in", 0) or 0)
            m = data.get("montage")
            if isinstance(m, dict):
                r["slot"], r["cand"] = m.get("slot"), m.get("cand")
        out.append(r)
    return out


# ---------------------------------------------------------------- rampas de velocidad
def _ramp_req(s, side):
    """Rampa pedida de un item ("ramp_in"/"ramp_out" en el item o en su "montage") -> {"dur",
    "speed"} saneada o None (sin rampa, dur <= 0 o speed <= 1)."""
    key = f"ramp_{side}"
    raw = s.get(key)
    m = s.get("montage")
    if raw is None and isinstance(m, dict):
        raw = m.get(key)
    if not isinstance(raw, dict):
        return None
    try:
        d = float(raw.get("dur"))
        sp = float(raw["speed"]) if raw.get("speed") is not None else 2.0
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(d) and math.isfinite(sp)) or d <= 0 or sp <= 1.0 + 1e-3:
        return None
    return {"dur": min(d, RAMP_MAX), "speed": min(sp, RAMP_SPEED_MAX)}


def _ramp_off(s):
    """La union de este item (con el ANTERIOR) tiene la rampa apagada por el usuario."""
    m = s.get("montage") if isinstance(s.get("montage"), dict) else {}
    return s.get("ramp") is False or m.get("ramp") is False


def seq_ramps(seq):
    """Rampas pedidas por elemento de `seq` -> [{"in": {dur, speed} | None, "out": ...}]. Solo en
    una union entre dos clips seguidos (sin negro en medio), sin crossfade y sin apagar; la rampa
    va en un VIDEO. Una sola por union: si llegan los dos lados gana la de salida."""
    n = len(seq)
    out = [{"in": None, "out": None} for _ in range(n)]

    def clip(k):
        return 0 <= k < n and seq[k][0] == "clip"

    def join_ok(k):                     # union entre k-1 y k
        if not (clip(k - 1) and clip(k)):
            return False
        tr = seq[k][2]
        if isinstance(tr, dict) and tr.get("type") == "crossfade" and \
                float(tr.get("dur", 0) or 0) > 0.05:
            return False
        return not _ramp_off(seq[k][1])

    for k in range(n):
        if not clip(k) or seq[k][1].get("type", "video") == "image":
            continue
        s = seq[k][1]
        ro = _ramp_req(s, "out")
        if ro and join_ok(k + 1):
            out[k]["out"] = ro
        ri = _ramp_req(s, "in")
        if ri and join_ok(k):
            out[k]["in"] = ri
    for k in range(1, n):
        if out[k]["in"] and out[k - 1]["out"]:
            out[k]["in"] = None
    return out


RAMP_CUT_MIN = 0.05      # s: lo que queda de un silencio recortado por una rampa (menos no se corta)
# s: ninguna parte del video (keep) empieza tan cerca de un cambio de camara con rampa: el render
# mostraria 1-2 cuadros sueltos de la OTRA camara (limite del keep en la reticula + el desfase de
# ~21 ms del video combinado); medido en un montaje real: un cuadro de la toma que sale tras el salto
RAMP_LEAK = 0.10
RAMP_IN_EAT = 0.08       # s: un silencio que llega al cambio sigue esto dentro de la rampa de entrada


def _ramp_joins(shots):
    """[(t, side, dur)] de las rampas hechas (plan.shots[i].ramp del que entra por la union; t = el
    cambio de camara en s del video combinado)."""
    out = []
    for sh in shots or []:
        r = sh.get("ramp") if isinstance(sh, dict) else None
        if not isinstance(r, dict):
            continue
        try:
            t, d = float(sh["start"]), float(r.get("dur") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if d > 0 and math.isfinite(t):
            out.append((t, "out" if r.get("side") == "out" else "in", d))
    return out


def ramp_windows(shots):
    """[(a, b)] (s del video combinado) donde se VE una rampa de velocidad hecha: los ultimos `dur` s
    de la toma que sale (side "out") o los primeros de la que entra (side "in")."""
    return [(t - d, t) if side == "out" else (t, t + d) for t, side, d in _ramp_joins(shots)]


def _ramp_trim(pieces, t, side, d):
    """Tramos de un corte de silencio [(x, y)] respetando la rampa del cambio de camara en `t`."""
    out = []
    for x, y in pieces:
        if side == "out":
            # rampa [t - d, t] en la que sale: lo de antes se corta igual; lo de despues empieza justo
            # en el cambio (sin cuadros sueltos de la que entra) y solo si reanuda >= RAMP_LEAK adentro
            if y <= t - d or x >= t + RAMP_LEAK:
                out.append((x, y))
                continue
            if x < t - d:
                out.append((x, t - d))
            if y - t >= RAMP_LEAK:
                out.append((t, y))
        else:
            # rampa [t, t + d] en la que entra: un silencio que llega al cambio (termina a menos de
            # RAMP_LEAK antes o adentro de la rampa) sigue RAMP_IN_EAT dentro de ella; el resto de la
            # rampa no se corta
            w0, w1 = t + RAMP_IN_EAT, t + d
            if y <= t - RAMP_LEAK or x >= w1:
                out.append((x, y))
                continue
            if x < t:
                out.append((x, w0))
            if y > w1:
                out.append((w1, y))
    return [(x, y) for x, y in out if y - x >= RAMP_CUT_MIN - 1e-9]


def keep_ramps(cuts, shots):
    """Cortes de SILENCIO (los del analisis: sin src ni marca de usuario/manual) recortados para no
    comerse una rampa de velocidad: la rampa cae en la pausa entre frases de una union y el corte
    de esa pausa se la llevaba (quedaba ~0.1 s de ella en el video final). Lo que el corte quitaba
    fuera de la rampa se sigue quitando (piezas >= RAMP_CUT_MIN) y ningun corte deja cuadros sueltos
    de la otra camara junto al cambio (RAMP_LEAK, RAMP_IN_EAT). Tomas, cortes manuales y los del
    usuario no se tocan. Copia; sin rampas devuelve los mismos cortes."""
    joins = _ramp_joins(shots)
    if not joins or not cuts:
        return cuts
    out = []
    for c in cuts:
        silence = isinstance(c, dict) and not c.get("src") and not c.get("manual") and not c.get("user")
        try:
            a, b = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            silence = False
        near = silence and any((t - d - RAMP_LEAK < b and a < t + RAMP_LEAK) if side == "out" else
                               (t - RAMP_LEAK < b and a < t + d) for t, side, d in joins)
        if not near:
            out.append(c)
            continue
        pieces = [(a, b)]
        for t, side, d in joins:
            pieces = _ramp_trim(pieces, t, side, d)
        out += [dict(c, start=round(x, 3), end=round(y, 3)) for x, y in pieces]
    return out


def _annotate_ramps(shots, req, eff):
    """info.shots[i] (el que ENTRA por la union) gana "ramp": {"side", "dur", "speed"} con la rampa
    que de verdad se hizo en esa union. Devuelve el resumen [{index, side, dur, speed, req}]
    (tambien las pedidas que no se pudieron hacer, con dur 0)."""
    by_index = {int(sh.get("index", -1)): sh for sh in shots or []}
    summary = []
    for i in range(len(req)):
        for side, j in (("in", i), ("out", i + 1)):
            r = req[i][side]
            if not r:
                continue
            e = (eff[i] or {}).get(side) if i < len(eff) else None
            summary.append({"index": j, "side": side, "dur": e["dur"] if e else 0.0,
                            "speed": r["speed"], "req": r["dur"]})
            if e and j in by_index:
                by_index[j]["ramp"] = {"side": side, "dur": round(e["dur"], 3),
                                       "speed": round(e["speed"], 3)}
    return summary


def plan_shots(info_shots, dur=None):
    """plan.shots a partir de info["shots"] del montaje: limites acotados a la duracion analizada
    (sin tomas < 0.05 s), la ultima hasta el final, y los campos del montaje (SHOT_KEYS) cuando
    los hay. Sin montaje: exactamente {start, end, src} como siempre."""
    out = []
    for s in info_shots or []:
        a, b = float(s["start"]), float(s["end"])
        if dur:
            a, b = min(a, dur), min(b, dur)
        if b - a >= 0.05:
            d = {"start": round(a, 3), "end": round(b, 3), "src": s.get("src", "")}
            for k in SHOT_KEYS:
                if k in s:
                    d[k] = s[k]
            out.append(d)
    if out:
        out[-1]["end"] = round(dur or out[-1]["end"], 3)
        if out[0].get("join") is not None:
            out[0]["join"] = None
    return out


def montage_plan(montage, main, shots):
    """plan.montage tras componer: el plan del montaje (guion, candidatos, descartes) con la linea
    de tiempo que REALMENTE se armo (el usuario pudo cambiar tomas en el ensamble): cada segmento
    con su indice en plan.shots ("shot") y sus limites en el video compuesto (t0, t1); el
    "chosen" de cada slot es la toma usada. None si no hay montaje."""
    items = [s for s in main or [] if isinstance(s, dict) and isinstance(s.get("montage"), dict)
             and int(s.get("layer", 0) or 0) == 0]
    if not items and not isinstance(montage, dict):
        return None
    out = copy.deepcopy(montage) if isinstance(montage, dict) else {"version": 1}
    pool = {}
    for k, sh in enumerate(shots or []):
        if sh.get("slot") is not None:
            pool.setdefault((sh.get("slot"), sh.get("cand")), []).append(k)
        elif sh.get("sid"):
            # un extra agregado en el ensamble (montage sin parte): por su clip, en orden de tiempo
            pool.setdefault(("sid", sh.get("sid")), []).append(k)
    if items:
        tl = []
        for s in sorted(items, key=lambda x: float(x.get("start", 0) or 0)):
            m = s["montage"]
            key = (m.get("slot"), m.get("cand")) if m.get("slot") is not None else ("sid", s.get("sid"))
            ks = pool.get(key) or []
            k = ks.pop(0) if ks else None
            vol = s.get("vol", 1.0)
            e = {"slot": m.get("slot"), "cand": m.get("cand"), "sid": s.get("sid"),
                 "in": s.get("in"), "out": s.get("out"), "start": s.get("start"),
                 "vol": round(float(vol), 4) if isinstance(vol, (int, float)) else 1.0,
                 "join": m.get("join") if m.get("join") in JOINS else None, "shot": k}
            if k is not None:
                e["t0"], e["t1"] = shots[k]["start"], shots[k]["end"]
            if s.get("_lufs") is not None:
                e["lufs"] = s["_lufs"]
            # rampas pedidas y el interruptor de la union (el ensamble los conserva al volver)
            for rk in ("ramp_in", "ramp_out"):
                rv = s.get(rk) if s.get(rk) is not None else m.get(rk)
                if isinstance(rv, dict):
                    e[rk] = copy.deepcopy(rv)
            for rk in ("ramp", "ramp_auto", "ramp_user"):
                rv = s.get(rk) if s.get(rk) is not None else m.get(rk)
                if isinstance(rv, (bool, dict)):
                    e[rk] = copy.deepcopy(rv)
            tl.append(e)
        if tl:
            tl[0]["join"] = None
        out["timeline"] = tl
        used = {}
        for e in tl:
            if e["slot"] is not None and e["cand"] is not None:
                used.setdefault(e["slot"], e["cand"])
        for sl in out.get("slots") or []:
            if isinstance(sl, dict) and sl.get("id") in used:
                sl["chosen"] = used[sl["id"]]
    out["composed"] = True
    return out


# ---------------------------------------------------------------- recursos del guion -> fx.media
def _tl_entries(timeline):
    """Items de una linea de tiempo del montaje -> [{a, b, in, sid, slot, cand}] (a/b: s de la
    linea de tiempo; in: s de su fuente), en orden."""
    out = []
    for e in timeline or []:
        if not isinstance(e, dict):
            continue
        try:
            a = float(e.get("start") or 0.0)
            i0, i1 = float(e.get("in") or 0.0), float(e.get("out") or 0.0)
        except (TypeError, ValueError):
            continue
        if i1 - i0 <= 0 or not math.isfinite(a):
            continue
        out.append({"a": a, "b": a + (i1 - i0), "in": i0, "sid": e.get("sid"),
                    "slot": e.get("slot"), "cand": e.get("cand")})
    return sorted(out, key=lambda x: x["a"])


def _anchor(t, entries, end=False):
    """s de la linea de tiempo -> (item que lo contiene, s de SU fuente). Un inicio pertenece al
    item que empieza ahi y un final al que termina ahi; fuera de todos, el mas cercano (acotado).
    None sin items."""
    if not entries:
        return None
    t = float(t)
    hit = next((e for e in entries if ((e["a"] < t <= e["b"]) if end else (e["a"] <= t < e["b"]))),
               None)
    if hit is None:
        hit = min(entries, key=lambda e: 0.0 if e["a"] <= t <= e["b"]
                  else min(abs(t - e["a"]), abs(t - e["b"])))
    tt = min(max(t, hit["a"]), hit["b"])
    return hit, hit["in"] + (tt - hit["a"])


def _segs(info):
    """Segmentos del video combinado (info["segs"], ver seg_rows) listos para mapear."""
    out = []
    for r in (info or {}).get("segs") or []:
        try:
            out.append({"start": float(r["start"]), "end": float(r["end"]), "kind": r.get("kind"),
                        "sid": r.get("sid"), "in": r.get("in"), "at": r.get("at"),
                        "len": r.get("len"), "slot": r.get("slot"), "cand": r.get("cand")})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _src_to_composed(entry, src_t, segs):
    """(item de la linea de tiempo, s de su fuente) -> s del video combinado: el segmento del MISMO
    clip cuyo tramo contiene ese segundo (mismo slot/cand primero, luego el mas cercano en la linea
    de tiempo). None si ese tramo ya no esta en el video."""
    best = None
    for s in segs:
        if s["kind"] != "clip" or not s["sid"] or s["sid"] != entry["sid"] or s["in"] is None:
            continue
        span = s["end"] - s["start"]
        if not (s["in"] - 0.02 <= src_t <= s["in"] + span + 0.02):
            continue
        same = entry.get("slot") is not None and (s["slot"], s["cand"]) == (entry["slot"], entry["cand"])
        key = (0 if same else 1, abs(float(s["at"] or 0.0) - entry["a"]))
        if best is None or key < best[0]:
            best = (key, s)
    if best is None:
        return None
    s = best[1]
    return min(max(s["start"] + (src_t - s["in"]), s["start"]), s["end"])


def _tl_to_composed(t, segs):
    """s de la linea de tiempo del ensamble -> s del video combinado por posicion (respaldo cuando
    el ancla en la fuente ya no esta): el segmento que ocupaba ese lugar."""
    rows = sorted((s for s in segs if s["at"] is not None and s["len"] is not None),
                  key=lambda s: s["at"])
    if not rows:
        return None
    t = float(t)
    if t <= rows[0]["at"]:
        return rows[0]["start"]
    for k, s in enumerate(rows):
        nxt = rows[k + 1]["at"] if k + 1 < len(rows) else None
        if nxt is None or t < nxt:
            return min(s["start"] + (t - s["at"]), s["end"])
    return rows[-1]["end"]


MEDIA_SPEED_MAX = 1.4    # = montage P["ins_speed_max"], server.INSERT_SPEED_MAX, fxcore INS.speedMax
MEDIA_SLOW_MIN = 0.8     # = montage P["ins_slow_min"], server.INSERT_SLOW_MIN, fxcore INS.slowMin


def _media_fit(kind, src_len, slot, ins):
    """(fit, speed, src_in) de un recurso para un hueco de `slot` s (la MISMA regla que
    montage.plan_inserts / fxcore.insertFit): imagen -> hold; video mas largo que el hueco ->
    "speed" (hasta 1.4x) o "trim" (desde src_in); un poco mas corto (desde 0.8x) -> "speed" < 1 (se
    alenta para llenar el hueco); mas corto -> "hold" (dura lo suyo y se queda en su ultimo cuadro)."""
    fit = ins.get("fit") if ins.get("fit") in ("hold", "speed", "trim") else "hold"
    try:
        sp = float(ins.get("speed") or 1.0)
        src_in = max(0.0, float(ins.get("src_in") or 0.0))
    except (TypeError, ValueError):
        sp, src_in = 1.0, 0.0
    if kind != "video":
        return "hold", 1.0, 0.0
    if not src_len or slot <= 0:
        return fit, round(sp, 4), round(src_in, 3)
    avail = float(src_len)
    if avail > slot + 1e-3:
        if avail <= MEDIA_SPEED_MAX * slot:
            return "speed", round(avail / slot, 4), 0.0
        return "trim", 1.0, round(src_in if src_in + slot <= avail else 0.0, 3)
    if MEDIA_SLOW_MIN * slot - 1e-3 <= avail < slot - 1e-3:
        return "speed", round(avail / slot, 4), 0.0
    return ("hold" if fit in ("speed", "trim") else fit), 1.0, 0.0


def _asset_resolver(spec, resolve=None):
    """sid de un recurso -> ruta: `resolve(sid)` (el servidor), spec["assets"] ([{sid, path}]), la
    ruta que traiga el insert o, al final, input/sources/<sid>.* como el servidor."""
    table = {}
    for a in (spec or {}).get("assets") or []:
        if isinstance(a, dict) and a.get("sid") and a.get("path"):
            table[str(a["sid"])] = str(a["path"])

    def res(sid, ins):
        if not sid:
            return None
        if callable(resolve):
            try:
                p = resolve(sid)
            except Exception:  # noqa: sin ruta el recurso se marca como faltante
                p = None
            if p:
                return str(p)
        if str(sid) in table:
            return table[str(sid)]
        if ins.get("path") and Path(str(ins["path"])).exists():
            return str(ins["path"])
        stem = Path(str(sid)).stem
        try:
            for p in sorted((paths.INPUT / "sources").glob(f"{stem}.*")):
                if p.suffix.lower() in IMAGE_EXT + VIDEO_EXT:
                    return str(p)
        except OSError:
            pass
        return None
    return res


def _media_kind(ins, path):
    k = ins.get("kind")
    if k in ("image", "video"):
        return k
    ext = Path(str(path or "")).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    try:
        return "image" if probe(path).get("type") == "image" else "video"
    except Exception:  # noqa
        return None


def script_media(montage, info, dur=None, spec=None, resolve=None, timeline=None):
    """plan.montage.inserts (s de la linea de tiempo del montaje `montage`) + info de compose()
    (info["segs"]) -> (items de plan.fx.media, {id: {"t0", "t1", "anchors", "status"}}). Cada
    extremo se ancla en la FUENTE (clip + segundo) del item de la linea de tiempo que lo contiene y
    se busca ese segundo en el video combinado; si ese tramo ya no esta, por su posicion en la
    linea de tiempo. Sin solaparse (cada uno termina donde empieza el siguiente que se queda),
    dentro del video y de al menos MEDIA_MIN s; los faltantes ("missing", sin sid o sin archivo)
    entran sin sid (el render los omite con aviso; el Studio pide subirlos) y los quitados en el
    ensamble ("enabled": false) entran ocultos sin recortar a los demas. `timeline`: la linea de
    tiempo en que se calcularon los inserts (default: la del montaje)."""
    ins = (montage or {}).get("inserts") if isinstance(montage, dict) else None
    if not isinstance(ins, list):
        return [], {}
    entries = _tl_entries(timeline if timeline is not None else (montage or {}).get("timeline"))
    segs = _segs(info)
    res = _asset_resolver(spec, resolve)
    total = float(dur) if dur else (max((s["end"] for s in segs), default=0.0) or None)
    rows, meta = [], {}
    for k, it in enumerate(ins):
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id") or f"ins{k + 1}")
        try:
            a, b = float(it["start"]), float(it["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(a) and math.isfinite(b)) or b <= a:
            continue
        anchors, ts = [], []
        for t, end in ((a, False), (b, True)):
            an = _anchor(t, entries, end=end)
            c = _src_to_composed(an[0], an[1], segs) if an else None
            if c is None:
                c = _tl_to_composed(t, segs)
            anchors.append({"sid": an[0]["sid"], "src": round(an[1], 3), "slot": an[0]["slot"],
                            "cand": an[0]["cand"]} if an else None)
            ts.append(c)
        if ts[0] is None:
            continue
        t0 = ts[0]
        t1 = ts[1] if ts[1] is not None and ts[1] > t0 + 1e-3 else t0 + (b - a)
        if total:
            t0, t1 = max(0.0, min(t0, total)), max(0.0, min(t1, total))
        path = res(it.get("sid"), it) if it.get("status", "ok") != "missing" else None
        status = "ok" if path else "missing"
        meta[iid] = {"t0": round(t0, 3), "t1": round(t1, 3), "anchors": anchors, "status": status}
        rows.append((t0, t1, iid, it, path))
    rows.sort(key=lambda r: (r[0], r[2]))
    # de atras hacia adelante: cada uno termina donde empieza el siguiente QUE SE QUEDA (uno
    # diminuto que se descarta no recorta al anterior). Uno QUITADO en el ensamble (enabled false)
    # no ocupa lugar: no recorta ni descarta a nadie (llega oculto con su propio tramo)
    kept, nxt = [], None
    for t0, t1, iid, it, path in reversed(rows):
        off = it.get("enabled", True) is False
        if nxt is not None and not off:
            t1 = min(t1, nxt)
        if t1 - t0 < MEDIA_MIN:
            meta[iid]["status"] = "too_short"
            continue
        kept.append((t0, t1, iid, it, path))
        if not off:
            nxt = t0
    items = []
    for t0, t1, iid, it, path in reversed(kept):
        meta[iid]["t1"] = round(t1, 3)
        kind = _media_kind(it, path) if path else (it.get("kind") if it.get("kind") in ("image", "video") else None)
        src_len = None
        if kind == "video" and path:
            try:
                src_len = float(probe(path).get("dur") or 0.0) or None
            except Exception:  # noqa
                src_len = None
        fit, sp, src_in = _media_fit(kind, src_len, t1 - t0, it)
        # sin archivo (falta): se queda en su lugar sin sid (el render lo omite con aviso y el Studio
        # pide subirlo en la pista Recursos)
        items.append({"id": iid, "start": round(t0, 3), "end": round(t1, 3),
                      "sid": it.get("sid") if path else None, "path": path, "kind": kind, "fit": fit,
                      "speed": sp, "src_in": src_in, "style": "float_card",
                      "enabled": it.get("enabled", True) is not False, "locked": True,
                      "src": "script", "name": it.get("name")})
    return items, meta


def _rebase_inserts(inserts, meta, timeline):
    """Los inserts de plan.montage quedan en la linea de tiempo COMPUESTA (la que el ensamble
    muestra al volver): start/end desde su ancla en la fuente; t0/t1 = s del video combinado y el
    status que se vio al componer. Sin las dos anclas en la nueva linea de tiempo, t0/t1."""
    entries = _tl_entries(timeline)
    for k, it in enumerate(inserts or []):
        if not isinstance(it, dict):
            continue
        mt = meta.get(str(it.get("id") or f"ins{k + 1}"))
        if not mt:
            continue
        it["t0"], it["t1"] = mt["t0"], mt["t1"]
        if mt["status"] == "missing" and it.get("status") != "missing":
            it["status"] = "missing"
        new = []
        for an in mt["anchors"]:
            hit = None
            if an:
                for e in entries:
                    if e["sid"] == an["sid"] and e["in"] - 1e-3 <= an["src"] <= e["in"] + (e["b"] - e["a"]) + 1e-3:
                        same = an.get("slot") is not None and (e["slot"], e["cand"]) == (an["slot"], an["cand"])
                        if hit is None or same:
                            hit = e
                            if same:
                                break
            new.append(round(hit["a"] + (an["src"] - hit["in"]), 3) if hit else None)
        if new[0] is not None and new[1] is not None and new[1] > new[0]:
            it["start"], it["end"] = new[0], new[1]
        elif mt["t1"] > mt["t0"]:
            # un extremo perdio su ancla (cambio la toma de su linea): donde quedo en el video
            # combinado (= la linea de tiempo compuesta); asi recomponer no lo mueve
            it["start"], it["end"] = mt["t0"], mt["t1"]


def finish_plan(plan, info, spec=None, montage=None, resolve=None):
    """Tras pipeline.analyze del video compuesto: plan.shots desde los limites reales (con join/
    setup/rampa del montaje) y, en un montaje, plan.montage mapeado a plan.shots, sin cortes de
    tomas que crucen una union y con las secciones de plan.takes sembradas desde el guion. Los
    recursos del guion (plan.montage.inserts) quedan en plan.fx.media (script_media; `resolve(sid)`
    -> ruta del archivo, opcional). `montage`: el plan del montaje (job de /montage); sin el se usa
    spec["montage"]. Sin montaje solo pone plan.shots (como siempre). Modifica y devuelve `plan`."""
    spec = spec or {}
    try:
        dur = float(plan.get("duration") or 0) or None
    except (TypeError, ValueError):
        dur = None
    shots = plan_shots((info or {}).get("shots"), dur)
    if shots:
        plan["shots"] = shots
    mont = montage if isinstance(montage, dict) else spec.get("montage")
    main = (info or {}).get("main") or spec.get("main")
    if not (isinstance(mont, dict) or is_montage({"main": main})):
        return plan
    shots = plan.get("shots") or []
    cands = _cand_index(mont)
    for sh in shots:
        if sh.get("setup") is None and sh.get("slot") is not None:
            c = cands.get((sh.get("slot"), sh.get("cand")))
            if c and c.get("setup") is not None:
                sh["setup"] = c["setup"]
    m = montage_plan(mont, main, shots)
    if m is not None:
        plan["montage"] = m
    if isinstance(mont, dict) and isinstance(mont.get("inserts"), list):
        # recursos del guion: del ensamble (timeline del montaje) al video combinado
        try:
            # inserts recolocados por el ensamble (spec["inserts_timeline"] == "main"): sus tiempos son
            # los de la linea de tiempo que se compuso (m), no los de la guardada del montaje
            tl_ins = m.get("timeline") if (m is not None and spec.get("inserts_timeline") == "main") else None
            items, meta = script_media(mont, info, dur, spec=spec, resolve=resolve, timeline=tl_ins)
        except Exception as e:  # noqa: los recursos son un extra, nunca tumban el montaje
            print(f"   compose: recursos del guion no mapeados ({e})")
            items, meta = None, {}
        if items is not None and (items or isinstance(plan.get("fx"), dict)):
            fx = plan.get("fx") if isinstance(plan.get("fx"), dict) else {}
            keep = [it for it in fx.get("media") or []
                    if isinstance(it, dict) and it.get("src") != "script"]
            fx["media"] = sorted(keep + items, key=lambda it: float(it.get("start") or 0.0))
            plan["fx"] = fx
        if items is not None and m is not None:
            _rebase_inserts(m.get("inserts"), meta, m.get("timeline"))
    import takes
    joins = takes.plan_joins(shots)
    if joins and plan.get("cuts"):
        # ... y ningun silencio se come una rampa de velocidad de un cambio de camara
        plan["cuts"] = keep_ramps(takes.respect_joins(plan["cuts"], joins), shots)
        if isinstance(plan.get("takes"), dict):
            plan["takes"]["stats"] = takes.stats(plan["cuts"])
    if m is not None:
        secs = takes.script_sections(m, shots)
        if secs:
            tk = plan.get("takes") if isinstance(plan.get("takes"), dict) else None
            if tk is not None:
                tk["sections"] = secs
                tk["sections_src"] = "guion"
    return plan


def compose(spec, out, work, on_step=None, info=None):
    """spec: ver módulo. Devuelve la ruta del video compuesto (out).

    Si `info` es un dict, se llena con info["shots"] = limites REALES de cada clip de la
    pista principal en el video compuesto ([{start, end, src, index, kind}], segundos):
    el servidor los pone en plan.shots para el director y el render (uniones exactas)."""
    def step(m):
        if on_step:
            on_step(m)

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    montage = is_montage(spec)
    # copias: resolver el volumen "auto" del montaje no toca el spec del llamador (params del job)
    main = [dict(s) for s in spec.get("main", []) if s.get("path")]
    if not main:
        raise ValueError("La composición no tiene clips en la pista principal.")
    fps = int(spec.get("fps", 30))
    if montage or any(s.get("vol") == "auto" for s in main):
        # nivel de la VOZ por segmento: todas las tomas suenan igual de fuerte (una sin microfono
        # sube, una muy cerca baja); los `vol` numericos se respetan
        lv = auto_levels(main)
        if info is not None and lv:
            info["levels"] = [{"index": k, "lufs": l, "vol": g} for k, l, g in lv]
    if info is not None:
        info["main"] = main

    # lienzo: dimensiones de DISPLAY del primer VIDEO (o del primer clip); pipeline
    # reencuadra luego. probe ya aplica la rotación (iPhone vertical = 2160x3840). En un
    # montaje con IA: la orientacion de la MAYORIA de los clips (el orden lo decide el guion).
    W = H = 0
    if montage or spec.get("canvas") == "majority":
        mc = majority_canvas(main)
        if mc:
            W, H = mc
    if not W:
        for s in main:
            if s.get("type") == "video":
                p = probe(s["path"]); W, H = p["w"], p["h"]; break
    if not W:
        p = probe(main[0]["path"]); W, H = (p["w"] or 1080), (p["h"] or 1920)
    W, H = canvas_size(W, H)
    if info is not None:
        info["W"], info["H"] = W, H

    def _len(s):
        return float(s.get("dur", 3.0)) if s.get("type") == "image" else \
            float(s.get("out", 5)) - float(s.get("in", 0))
    # CAPAS: la capa 0 es la base; las superiores se encimarán full-frame.
    base = [s for s in main if int(s.get("layer", 0) or 0) == 0]
    upper = [s for s in main if int(s.get("layer", 0) or 0) > 0]
    if not base:
        base = main; upper = []      # todo en una capa si no hay base explícita

    # POSICIÓN LIBRE: ordenar por start, rellenar HUECOS con negro, colapsar solapes.
    ordered = sorted(base, key=lambda s: float(s.get("start", 0) or 0))
    seq = []            # lista de (tipo, dato, trans) en orden de reproducción
    at = []             # s de la LINEA DE TIEMPO del ensamble donde empieza cada elemento de seq
    cursor = 0.0
    for s in ordered:
        st = float(s.get("start", 0) or 0)
        if st > cursor + 0.05:                       # hueco -> negro
            seq.append(("black", st - cursor, None))
            at.append(cursor)
        seq.append(("clip", s, s.get("trans")))
        at.append(st)
        cursor = max(cursor, st) + _len(s)

    # 0) un solo video completo que ya es un intermedio valido: se copia (sin re-codificar)
    if not upper:
        src_info = copy_source(seq, spec, W, H, fps)
        if src_info is not None:
            s0 = seq[0][1]
            step("Preparando 0/1…")
            tmp = work / "copy.mp4"                 # temporal + renombrado: nunca queda a medias
            _copy_video(s0["path"], tmp, src_info)
            Path(tmp).replace(out)
            step("Preparando 1/1…")
            if info is not None:
                d = round(float(probe(out)["dur"] or 0.0), 3)
                info["copied"] = True
                info["shots"] = [{"start": 0.0, "end": d, "index": 0, "kind": "clip",
                                  "src": Path(str(s0.get("name") or s0.get("path") or "")).name}]
                _annotate_shots(info["shots"], seq, spec.get("montage"))
                info["segs"] = seg_rows(info["shots"], seq, at, _len)
            return str(out)

    # bitrate UNICO del montaje: <= 1.2x la fuente de mas bitrate (y <= MAX_BPS)
    br = spec_bitrate(main, W, H, fps)
    if info is not None:
        info["bitrate"] = int(br)

    # 1) normalizar cada segmento (COMPOSE_JOBS a la vez; el orden de `parts` es el de seq)
    parts = [work / f"seg_{i:03d}.mp4" for i in range(len(seq))]
    trans = [None if kind == "black" else tr for kind, _d, tr in seq]
    has_trans = any(t and t.get("type") == "crossfade" and float(t.get("dur", 0)) > 0.05
                    for t in trans[1:])
    # micro-fundido SOLO de audio a cada lado de las uniones de un montaje con IA (con crossfades
    # el acrossfade ya mezcla cada union)
    mfade = MICRO_FADE if (montage and not has_trans) else 0.0
    # montaje (sin crossfades): segmentos de cuadros ENTEROS y el audio se re-codifica al unir. Con
    # `-c copy` el concat deja en CADA union el relleno inicial del AAC (1024 muestras = 21 ms de
    # silencio digital) y el audio de cada segmento se atrasa ~21-28 ms mas que el anterior: en un
    # montaje de 15 uniones la voz terminaba ~0.4 s detras de la boca
    exact = bool((EXACT_JOINS == "all" or (montage and EXACT_JOINS != "off")) and not has_trans)
    # rampas de velocidad pedidas (solo en uniones validas) y las que de verdad se hicieron
    ramp_req = seq_ramps(seq)
    ramp_eff = [None] * len(seq)

    def norm(i):
        kind, data, _tr = seq[i]
        seg = parts[i]
        if kind == "black":
            _norm_black(float(data), seg, W, H, fps, br, exact=exact)
            return
        s = data
        fin = float(s.get("fadeIn", 0) or 0); fout = float(s.get("fadeOut", 0) or 0)
        if s.get("type") == "image":
            _norm_image(s["path"], float(s.get("dur", 3.0)), seg, W, H, fps, fin, fout, br, exact=exact)
        else:
            t_in = float(s.get("in", 0)); t_out = float(s.get("out", t_in + 5))
            vol = 0.0 if s.get("mute") else float(s.get("vol", 1.0))
            mf = mfade if isinstance(s.get("montage"), dict) else 0.0
            ramp_eff[i] = _norm_video(
                s["path"], t_in, t_out, seg, W, H, fps, vol, fin, fout, br,
                afin=mf if i > 0 else 0.0, afout=mf if i < len(seq) - 1 else 0.0,
                limit=bool(isinstance(s.get("montage"), dict) and vol > 1.0 + 1e-3),
                exact=exact, ramp_in=ramp_req[i]["in"], ramp_out=ramp_req[i]["out"])

    n = len(seq)
    step(f"Preparando 0/{n}…")
    if COMPOSE_JOBS <= 1 or n <= 1:
        for i in range(n):
            norm(i)
            step(f"Preparando {i + 1}/{n}…")
    else:
        ex = ThreadPoolExecutor(max_workers=min(COMPOSE_JOBS, n), thread_name_prefix="compose")
        try:
            futs = [ex.submit(norm, i) for i in range(n)]
            for k, f in enumerate(as_completed(futs), 1):
                f.result()                       # propaga el error del ffmpeg
                step(f"Preparando {k}/{n}…")     # on_step puede lanzar (cancelacion)
        except BaseException:
            for f in futs:
                f.cancel()                       # los que no empezaron ya no corren
            raise
        finally:
            ex.shutdown(wait=True)

    if info is not None:
        try:
            info["shots"] = _shots(seq, parts, trans)
        except Exception as e:  # noqa: los limites son una ayuda, nunca tumban el montaje
            print(f"   compose: limites de tomas no disponibles ({e})")
            info["shots"] = []
        _annotate_shots(info["shots"], seq, spec.get("montage"))
        info["segs"] = seg_rows(info["shots"], seq, at, _len)
        info["ramps"] = _annotate_ramps(info["shots"], ramp_req, ramp_eff)
    nreq = sum(1 for r in ramp_req if r["in"] or r["out"])
    if nreq:
        done = sum(1 for r in ramp_eff if r)
        short = sum(1 for r in ramp_eff if r for side in ("in", "out")
                    if r[side] and r[side]["dur"] < r[side]["req"] - 0.5 / fps)
        print(f"   compose: {done}/{nreq} rampas de velocidad"
              f"{f' ({short} acortadas por falta de manija)' if short else ''}")

    # 2) unir: si hay TRANSICIONES (crossfade) usa xfade; si no, concat demuxer (rápido)
    step("Uniendo la línea de tiempo…")
    main_mp4 = work / "main.mp4"
    if has_trans and len(parts) > 1:
        _concat_xfade(parts, trans, main_mp4, W, H, fps, br)
    else:
        listf = work / "concat.txt"
        listf.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        # montaje: el video se copia y el audio se decodifica (sin el relleno de cada AAC) y se
        # re-codifica una vez: uniones sin hueco y en sincronia (segmentos de cuadros enteros)
        acodec = ["-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k"] \
            if exact and len(parts) > 1 else ["-c", "copy"]
        _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
              *acodec, str(main_mp4)])

    # 3) encimar CAPAS superiores (full-frame, en su posición temporal) + STICKERS (PiP)
    overlays = [o for o in spec.get("overlays", []) if o.get("path")]
    if not overlays and not upper:
        Path(main_mp4).replace(out)
        return str(out)

    step("Encimando capas…")
    # normalizar cada clip de capa superior a un segmento (recorte + fade)
    upper_segs = []   # (seg_path, start, len)
    for j, s in enumerate(sorted(upper, key=lambda x: (int(x.get("layer", 1)), float(x.get("start", 0) or 0)))):
        seg = work / f"up_{j:03d}.mp4"
        fin = float(s.get("fadeIn", 0) or 0); fout = float(s.get("fadeOut", 0) or 0)
        if s.get("type") == "image":
            _norm_image(s["path"], float(s.get("dur", 3.0)), seg, W, H, fps, fin, fout, br)
        else:
            _norm_video(s["path"], float(s.get("in", 0)), float(s.get("out", 5)), seg, W, H, fps,
                        0.0 if s.get("mute") else float(s.get("vol", 1.0)), fin, fout, br)
        upper_segs.append((seg, float(s.get("start", 0) or 0), _len(s)))

    inputs = ["-i", str(main_mp4)]
    fc = []
    last = "0:v"
    idx = 1
    mx, my = round(W * 0.04), round(H * 0.04)
    # capas superiores: full-frame, aparecen en [start, start+len] (setpts las posiciona)
    for seg, st, ln in upper_segs:
        inputs += ["-i", str(seg)]
        fc.append(f"[{idx}:v]setpts=PTS+{st:.3f}/TB,format=yuva420p[u{idx}]")
        fc.append(f"[{last}][u{idx}]overlay=0:0:enable='between(t,{st:.3f},{st + ln:.3f})'[l{idx}]")
        last = f"l{idx}"; idx += 1
    # stickers/overlays PiP: escalados y posicionados
    for o in overlays[:8]:
        typ = o.get("type", "image")
        st = float(o.get("start", 0)); dur = float(o.get("dur", 2.5))
        if typ == "image":
            inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(o["path"])]
        else:
            inputs += ["-i", str(o["path"])]
        ow = max(2, round(W * float(o.get("size", 0.35)))); ow -= ow % 2
        if o.get("x") is not None and o.get("y") is not None:
            ox = f"{round(float(o['x']) * W)}-overlay_w/2"
            oy = f"{round(float(o['y']) * H)}-overlay_h/2"
        else:
            ox, oy = _overlay_xy(o.get("pos", "center"), W, H, mx, my)
        fc.append(f"[{idx}:v]scale={ow}:-2,setsar=1,format=yuva420p[s{idx}]")
        fc.append(f"[{last}][s{idx}]overlay={ox}:{oy}:enable='between(t,{st:.3f},{st + dur:.3f})'[l{idx}]")
        last = f"l{idx}"; idx += 1
    fc.append(f"[{last}]format=yuv420p,{SETPARAMS}[vout]")
    _run([FFMPEG, "-y", *inputs, "-filter_complex", ";".join(fc),
          "-map", "[vout]", "-map", "0:a?", "-c:v", "h264_videotoolbox",
          "-b:v", br] + _color_tags() + [
          "-c:a", "aac", "-b:a", "192k", str(out)])
    return str(out)
