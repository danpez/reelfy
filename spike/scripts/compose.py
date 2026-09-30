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
SHOT_KEYS = ("join", "slot", "cand", "conf", "setup", "sid")   # de info.shots a plan.shots
# uniones exactas (segmentos de cuadros enteros + audio re-codificado al unir, ver compose()):
# "montage" (default: solo "Armar con IA"), "all" (tambien el ensamble manual) u "off"
EXACT_JOINS = os.environ.get("REELFY_COMPOSE_EXACT_JOINS", "montage").strip().lower()


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


def _norm_video(src, t_in, t_out, out, w, h, fps, vol=1.0, fin=0.0, fout=0.0, br=None,
                afin=0.0, afout=0.0, limit=False, exact=False):
    """Recorta [in,out] y normaliza a WxH/fps con audio 48k estéreo (silencio si
    no tiene). cover-crop para llenar el lienzo. `vol` volumen (0=mudo); `fin`/`fout`
    desvanecen VIDEO (a/desde negro) y AUDIO al inicio/fin del clip. `br` bitrate del
    montaje (spec_bitrate); sin el, el tope de esta fuente. `afin`/`afout`: micro-fundido
    SOLO de audio (uniones del montaje); `limit`: limitador tras el volumen (vol > 1);
    `exact`: duracion de cuadros enteros (_seg_dur)."""
    dur, tdur = _seg_dur(max(0.1, t_out - t_in), fps, exact)
    fin = max(0.0, min(float(fin), dur / 2)); fout = max(0.0, min(float(fout), dur / 2))
    afin = max(0.0, min(float(afin), dur / 4)); afout = max(0.0, min(float(afout), dur / 4))
    info = video_io.probe(src)
    # ffmpeg autorrota al decodificar (los cuadros ya llegan en orientación de display);
    # la escala conserva 10 bits y el tonemap HLG/PQ -> SDR bt709 va ANTES de bajar a 8 bits.
    vf = [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos",
          f"crop={w}:{h}", f"fps={fps}", "setsar=1"] + video_io.tonemap_filters(info)
    af = _audio_filters(dur, vol, fin, fout, afin, afout, limit)
    if fin > 0.01:
        vf.append(f"fade=t=in:st=0:d={fin:.3f}")
    if fout > 0.01:
        vf.append(f"fade=t=out:st={dur - fout:.3f}:d={fout:.3f}")
    cmd = [FFMPEG, "-y"] + video_io.hwaccel_args() + ["-ss", f"{t_in:.3f}", "-i", str(src),
                                                    "-t", tdur]
    if not info.get("has_audio"):
        cmd += ["-f", "lavfi", "-t", tdur, "-i", "anullsrc=r=48000:cl=stereo",
                "-map", "0:v:0", "-map", "1:a:0"]
    else:
        # 1a pista de audio explícita (los iPhone traen una 2a pista APAC espacial)
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
    cmd += ["-vf", ",".join(vf), "-af", ",".join(af), "-c:v", "h264_videotoolbox",
            "-b:v", br or _vbitrate(w, h, fps, src_bitrate(src))] + _color_tags() + [
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", str(out)]
    _run(cmd)


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


def finish_plan(plan, info, spec=None, montage=None):
    """Tras pipeline.analyze del video compuesto: plan.shots desde los limites reales (con join/
    setup del montaje) y, en un montaje, plan.montage mapeado a plan.shots, sin cortes de tomas que
    crucen una union y con las secciones de plan.takes sembradas desde el guion. `montage`: el plan
    del montaje (job de /montage); sin el se usa spec["montage"]. Sin montaje solo pone plan.shots
    (como siempre). Modifica y devuelve `plan`."""
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
    import takes
    joins = takes.plan_joins(shots)
    if joins and plan.get("cuts"):
        plan["cuts"] = takes.respect_joins(plan["cuts"], joins)
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
    cursor = 0.0
    for s in ordered:
        st = float(s.get("start", 0) or 0)
        if st > cursor + 0.05:                       # hueco -> negro
            seq.append(("black", st - cursor, None))
        seq.append(("clip", s, s.get("trans")))
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
            _norm_video(s["path"], t_in, t_out, seg, W, H, fps, vol, fin, fout, br,
                        afin=mf if i > 0 else 0.0, afout=mf if i < len(seq) - 1 else 0.0,
                        limit=bool(isinstance(s.get("montage"), dict) and vol > 1.0 + 1e-3),
                        exact=exact)

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
