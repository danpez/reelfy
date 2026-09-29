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
"""
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


def probe(path):
    """dims de DISPLAY (rotación aplicada: un MOV de iPhone 3840x2160 con rotation -90
    es 2160x3840), duración, si tiene audio y si es HDR (HLG/PQ)."""
    i = video_io.probe(path)
    if not i.get("w") and not i.get("duration"):
        return {"w": 0, "h": 0, "dur": 0.0, "audio": False, "type": "video",
                "rotation": 0, "hdr": None}
    return {"w": i["w"], "h": i["h"], "dur": round(i["duration"], 3), "audio": i["has_audio"],
            "type": "video" if i["duration"] > 0 else "image", "rotation": i["rotation"],
            "hdr": i["hdr"]}


def _has_audio(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def _norm_video(src, t_in, t_out, out, w, h, fps, vol=1.0, fin=0.0, fout=0.0, br=None):
    """Recorta [in,out] y normaliza a WxH/fps con audio 48k estéreo (silencio si
    no tiene). cover-crop para llenar el lienzo. `vol` volumen (0=mudo); `fin`/`fout`
    desvanecen VIDEO (a/desde negro) y AUDIO al inicio/fin del clip. `br` bitrate del
    montaje (spec_bitrate); sin el, el tope de esta fuente."""
    dur = max(0.1, t_out - t_in)
    fin = max(0.0, min(float(fin), dur / 2)); fout = max(0.0, min(float(fout), dur / 2))
    info = video_io.probe(src)
    # ffmpeg autorrota al decodificar (los cuadros ya llegan en orientación de display);
    # la escala conserva 10 bits y el tonemap HLG/PQ -> SDR bt709 va ANTES de bajar a 8 bits.
    vf = [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos",
          f"crop={w}:{h}", f"fps={fps}", "setsar=1"] + video_io.tonemap_filters(info)
    af = [f"volume={max(0.0, float(vol)):.3f}", "aresample=48000"]
    if fin > 0.01:
        vf.append(f"fade=t=in:st=0:d={fin:.3f}"); af.append(f"afade=t=in:st=0:d={fin:.3f}")
    if fout > 0.01:
        vf.append(f"fade=t=out:st={dur - fout:.3f}:d={fout:.3f}")
        af.append(f"afade=t=out:st={dur - fout:.3f}:d={fout:.3f}")
    cmd = [FFMPEG, "-y"] + video_io.hwaccel_args() + ["-ss", f"{t_in:.3f}", "-i", str(src),
                                                    "-t", f"{dur:.3f}"]
    if not info.get("has_audio"):
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-map", "0:v:0", "-map", "1:a:0"]
    else:
        # 1a pista de audio explícita (los iPhone traen una 2a pista APAC espacial)
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
    cmd += ["-vf", ",".join(vf), "-af", ",".join(af), "-c:v", "h264_videotoolbox",
            "-b:v", br or _vbitrate(w, h, fps, src_bitrate(src))] + _color_tags() + [
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", str(out)]
    _run(cmd)


def _norm_image(src, dur, out, w, h, fps, fin=0.0, fout=0.0, br=None):
    """Imagen fija -> clip de `dur` s a WxH/fps con audio en silencio (+ fade opcional)."""
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
    _run([FFMPEG, "-y", "-loop", "1", "-t", f"{dur:.3f}", "-i", str(src),
          "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
          "-vf", vf, "-c:v", "h264_videotoolbox", "-b:v", br or _vbitrate(w, h, fps)] + _color_tags() + [
          "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", "-shortest", str(out)])


def _norm_black(dur, out, w, h, fps, br=None):
    """Segmento NEGRO con silencio (para rellenar huecos de posicionamiento libre)."""
    _run([FFMPEG, "-y", "-f", "lavfi", "-t", f"{dur:.3f}", "-i",
          f"color=c=black:s={w}x{h}:r={fps}",
          "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
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
    main = [s for s in spec.get("main", []) if s.get("path")]
    if not main:
        raise ValueError("La composición no tiene clips en la pista principal.")
    fps = int(spec.get("fps", 30))

    # lienzo: dimensiones de DISPLAY del primer VIDEO (o del primer clip); pipeline
    # reencuadra luego. probe ya aplica la rotación (iPhone vertical = 2160x3840).
    W = H = 0
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
            return str(out)

    # bitrate UNICO del montaje: <= 1.2x la fuente de mas bitrate (y <= MAX_BPS)
    br = spec_bitrate(main, W, H, fps)
    if info is not None:
        info["bitrate"] = int(br)

    # 1) normalizar cada segmento (COMPOSE_JOBS a la vez; el orden de `parts` es el de seq)
    parts = [work / f"seg_{i:03d}.mp4" for i in range(len(seq))]
    trans = [None if kind == "black" else tr for kind, _d, tr in seq]

    def norm(i):
        kind, data, _tr = seq[i]
        seg = parts[i]
        if kind == "black":
            _norm_black(float(data), seg, W, H, fps, br)
            return
        s = data
        fin = float(s.get("fadeIn", 0) or 0); fout = float(s.get("fadeOut", 0) or 0)
        if s.get("type") == "image":
            _norm_image(s["path"], float(s.get("dur", 3.0)), seg, W, H, fps, fin, fout, br)
        else:
            t_in = float(s.get("in", 0)); t_out = float(s.get("out", t_in + 5))
            vol = 0.0 if s.get("mute") else float(s.get("vol", 1.0))
            _norm_video(s["path"], t_in, t_out, seg, W, H, fps, vol, fin, fout, br)

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

    # 2) unir: si hay TRANSICIONES (crossfade) usa xfade; si no, concat demuxer (rápido)
    step("Uniendo la línea de tiempo…")
    main_mp4 = work / "main.mp4"
    has_trans = any(t and t.get("type") == "crossfade" and float(t.get("dur", 0)) > 0.05
                    for t in trans[1:])
    if has_trans and len(parts) > 1:
        _concat_xfade(parts, trans, main_mp4, W, H, fps, br)
    else:
        listf = work / "concat.txt"
        listf.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
              "-c", "copy", str(main_mp4)])

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
