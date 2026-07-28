#!/usr/bin/env python3
"""Composición de línea de tiempo (editor v1): arma varios videos/imágenes en la
PISTA PRINCIPAL (recortados y en orden) + capas OVERLAY encimadas, y produce UN
solo video. Ese video compuesto es la "fuente" que luego pasa por el pipeline de
IA (transcripción, subtítulos, cortes, etc.) manteniendo la línea de tiempo del
usuario + los cambios de la IA encima.

Enfoque robusto en 2 pasos (evita los problemas de un filter_complex gigante):
  1) Normalizar cada segmento de la pista principal a un archivo temporal con los
     MISMOS parámetros (resolución/fps/audio) -> concat demuxer los une sin re-encode.
  2) Componer las capas overlay sobre el video principal (pocas, manejable).
"""
import json
import subprocess
from pathlib import Path

import paths

FFMPEG = str(paths.FFMPEG)
FFPROBE = str(paths.FFPROBE)


def _run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def probe(path):
    """dims, duración y si tiene audio."""
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                        "stream=width,height,codec_type", "-show_entries", "format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except Exception:  # noqa
        return {"w": 0, "h": 0, "dur": 0.0, "audio": False, "type": "video"}
    w = h = 0
    audio = False
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and not w:
            w, h = s.get("width", 0), s.get("height", 0)
        if s.get("codec_type") == "audio":
            audio = True
    dur = float(d.get("format", {}).get("duration") or 0)
    return {"w": w, "h": h, "dur": round(dur, 3), "audio": audio,
            "type": "video" if dur > 0 else "image"}


def _has_audio(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def _norm_video(src, t_in, t_out, out, w, h, fps, vol=1.0, fin=0.0, fout=0.0):
    """Recorta [in,out] y normaliza a WxH/fps con audio 48k estéreo (silencio si
    no tiene). cover-crop para llenar el lienzo. `vol` volumen (0=mudo); `fin`/`fout`
    desvanecen VIDEO (a/desde negro) y AUDIO al inicio/fin del clip."""
    dur = max(0.1, t_out - t_in)
    fin = max(0.0, min(float(fin), dur / 2)); fout = max(0.0, min(float(fout), dur / 2))
    vf = [f"scale={w}:{h}:force_original_aspect_ratio=increase",
          f"crop={w}:{h}", f"fps={fps}", "setsar=1", "format=yuv420p"]
    af = [f"volume={max(0.0, float(vol)):.3f}", "aresample=48000"]
    if fin > 0.01:
        vf.append(f"fade=t=in:st=0:d={fin:.3f}"); af.append(f"afade=t=in:st=0:d={fin:.3f}")
    if fout > 0.01:
        vf.append(f"fade=t=out:st={dur - fout:.3f}:d={fout:.3f}")
        af.append(f"afade=t=out:st={dur - fout:.3f}:d={fout:.3f}")
    cmd = [FFMPEG, "-y", "-ss", f"{t_in:.3f}", "-i", str(src), "-t", f"{dur:.3f}"]
    if not _has_audio(src):
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-vf", ",".join(vf), "-af", ",".join(af), "-c:v", "h264_videotoolbox", "-b:v", "12M",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", str(out)]
    _run(cmd)


def _norm_image(src, dur, out, w, h, fps, fin=0.0, fout=0.0):
    """Imagen fija -> clip de `dur` s a WxH/fps con audio en silencio (+ fade opcional)."""
    fin = max(0.0, min(float(fin), dur / 2)); fout = max(0.0, min(float(fout), dur / 2))
    vf = [f"scale={w}:{h}:force_original_aspect_ratio=increase",
          f"crop={w}:{h}", f"fps={fps}", "setsar=1", "format=yuv420p"]
    if fin > 0.01:
        vf.append(f"fade=t=in:st=0:d={fin:.3f}")
    if fout > 0.01:
        vf.append(f"fade=t=out:st={dur - fout:.3f}:d={fout:.3f}")
    vf = ",".join(vf)
    _run([FFMPEG, "-y", "-loop", "1", "-t", f"{dur:.3f}", "-i", str(src),
          "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
          "-vf", vf, "-c:v", "h264_videotoolbox", "-b:v", "12M",
          "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", "-shortest", str(out)])


def _norm_black(dur, out, w, h, fps):
    """Segmento NEGRO con silencio (para rellenar huecos de posicionamiento libre)."""
    _run([FFMPEG, "-y", "-f", "lavfi", "-t", f"{dur:.3f}", "-i",
          f"color=c=black:s={w}x{h}:r={fps}",
          "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
          "-c:v", "h264_videotoolbox", "-b:v", "12M", "-c:a", "aac", "-ar", "48000",
          "-ac", "2", "-b:a", "192k", "-shortest", str(out)])


def _overlay_xy(pos, W, H, mx, my):
    return {
        "tl": (f"{mx}", f"{my}"), "tr": (f"W-w-{mx}", f"{my}"),
        "bl": (f"{mx}", f"H-h-{my}"), "br": (f"W-w-{mx}", f"H-h-{my}"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }.get(pos, ("(W-w)/2", "(H-h)/2"))


def _concat_xfade(parts, trans, out):
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
    _run([FFMPEG, "-y", *inputs, "-filter_complex", ";".join(fc),
          "-map", vprev, "-map", aprev, "-c:v", "h264_videotoolbox", "-b:v", "12M",
          "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", str(out)])


def compose(spec, out, work, on_step=None):
    """spec: ver módulo. Devuelve la ruta del video compuesto (out)."""
    def step(m):
        if on_step:
            on_step(m)

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    main = [s for s in spec.get("main", []) if s.get("path")]
    if not main:
        raise ValueError("La composición no tiene clips en la pista principal.")
    fps = int(spec.get("fps", 30))

    # lienzo: dimensiones del primer VIDEO (o del primer clip); pipeline reencuadra luego
    W = H = 0
    for s in main:
        if s.get("type") == "video":
            p = probe(s["path"]); W, H = p["w"], p["h"]; break
    if not W:
        p = probe(main[0]["path"]); W, H = (p["w"] or 1080), (p["h"] or 1920)
    W -= W % 2; H -= H % 2

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

    # 1) normalizar cada segmento
    parts, trans = [], []
    for i, (kind, data, tr) in enumerate(seq):
        step(f"Preparando {i + 1}/{len(seq)}…")
        seg = work / f"seg_{i:03d}.mp4"
        if kind == "black":
            _norm_black(float(data), seg, W, H, fps); trans.append(None)
        else:
            s = data
            fin = float(s.get("fadeIn", 0) or 0); fout = float(s.get("fadeOut", 0) or 0)
            if s.get("type") == "image":
                _norm_image(s["path"], float(s.get("dur", 3.0)), seg, W, H, fps, fin, fout)
            else:
                t_in = float(s.get("in", 0)); t_out = float(s.get("out", t_in + 5))
                vol = 0.0 if s.get("mute") else float(s.get("vol", 1.0))
                _norm_video(s["path"], t_in, t_out, seg, W, H, fps, vol, fin, fout)
            trans.append(tr)
        parts.append(seg)

    # 2) unir: si hay TRANSICIONES (crossfade) usa xfade; si no, concat demuxer (rápido)
    step("Uniendo la línea de tiempo…")
    main_mp4 = work / "main.mp4"
    has_trans = any(t and t.get("type") == "crossfade" and float(t.get("dur", 0)) > 0.05
                    for t in trans[1:])
    if has_trans and len(parts) > 1:
        _concat_xfade(parts, trans, main_mp4)
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
            _norm_image(s["path"], float(s.get("dur", 3.0)), seg, W, H, fps, fin, fout)
        else:
            _norm_video(s["path"], float(s.get("in", 0)), float(s.get("out", 5)), seg, W, H, fps,
                        0.0 if s.get("mute") else float(s.get("vol", 1.0)), fin, fout)
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
    _run([FFMPEG, "-y", *inputs, "-filter_complex", ";".join(fc),
          "-map", f"[{last}]", "-map", "0:a?", "-c:v", "h264_videotoolbox", "-b:v", "12M",
          "-c:a", "aac", "-b:a", "192k", str(out)])
    return str(out)
