#!/usr/bin/env python3
"""
Reelfy spike pipeline — single-pass, local, Apple Silicon.

video_in -> [ffmpeg audio] -> [whisper.cpp word-timestamps] -> [ASS word-highlight captions]
         -> [ffmpeg 9:16 reframe + burn captions] -> video_out

This is a validation spike, NOT the product. Goal: prove we can render a 9:16 short
with Spanish captions whose word-level timing/sync is tight and "listo para publicar".

Reframe here is a simple center-crop cover (no subject tracking yet). Real subject
tracking must use a PERMISSIVE-licensed detector, NOT YOLOv11 (AGPL-3.0). See docs.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reframe_track
import highlights as hl
import align as align_mod
import edit as edit_mod
import thumbnail as thumb_mod
import translate as translate_mod
import broll as broll_mod

from paths import SPIKE, DATA, WORK, WCLI, FFMPEG, FFPROBE  # noqa: E402
import paths as paths_mod  # noqa: E402

# ---- caption styling (the wedge: word-by-word "viral" look) ----
W, H = 1080, 1920            # 9:16
FONT = "Arial"
FONT_SIZE = 78
BASE_COLOR = "&H00FFFFFF"    # white (ASS is &HAABBGGRR)
ACTIVE_COLOR = "&H0000E5FF"  # amber highlight for the spoken word
OUTLINE = 5
MARGIN_H = 140              # side margins so text never overflows
MARGIN_V = 430               # captions in lower third (chest), clear of face & bottom UI
MAX_WORDS_PER_LINE = 3       # short phrases, like Submagic/Opus
GAP_SPLIT_MS = 700           # start a new phrase after a pause this long

FONT_TTF = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
_FONT_CACHE = {}


def _text_w(text, size):
    """Measure text width in px with the SAME TTF libass renders (Arial Bold),
    so the auto-fit below matches what actually gets burned."""
    from PIL import ImageFont
    f = _FONT_CACHE.get(size)
    if f is None:
        f = ImageFont.truetype(FONT_TTF, size)
        _FONT_CACHE[size] = f
    return f.getlength(text)


def _fit_size(text, base, max_w, floor=28):
    """Largest font size <= base that fits text within max_w."""
    tw = _text_w(text, base)
    if tw <= max_w:
        return base
    return max(floor, int(base * max_w / tw * 0.98))


def _hex_ass(hex_color):
    """'#rrggbb' -> ASS '&H00BBGGRR' (BGR order)."""
    c = hex_color.lstrip("#")
    if len(c) != 6:
        return None
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H00{b}{g}{r}".upper()


# caption style presets (ASS colours are &HAABBGGRR). base text is always white/bold.
STYLES = {
    # kw = color persistente de las palabras clave (resaltado inteligente).
    # border/boxcolor -> BorderStyle 3 (caja opaca). fill -> barrido karaoke.
    "clasico":  dict(size=78, active="&H0000E5FF", outline=5, upper=False,   # white + amber
                     kw="&H0032FF7C"),                                       # kw lime
    "amarillo": dict(size=84, active="&H0000F0FF", outline=6, upper=True,    # CAPS + yellow
                     kw="&H00FF9036"),                                       # kw azul eléctrico
    "neon":     dict(size=80, active="&H00F050FF", outline=5, upper=False,   # white + magenta
                     kw="&H00FFF04A"),                                       # kw cian
    "minimal":  dict(size=70, active="&H00FFFFFF", outline=4, upper=False,   # clean
                     kw="&H000FBDF2"),                                       # kw dorado suave
    "karaoke":  dict(size=80, active="&H0000E5FF", outline=5, upper=True,    # barrido de color
                     kw="&H0032FF7C", fill=True),                            # dentro de la palabra
    "caja":     dict(size=72, active="&H0000E5FF", outline=9, upper=False,   # caja oscura detrás
                     kw="&H0032FF7C", border=3, boxcolor="&H66000000"),
    "impacto":  dict(size=92, active="&H00FFD24A", outline=7, upper=True,    # XL + cian
                     kw="&H000FBDF2"),
}

# output format presets (label -> (w, h))
FORMATS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
           "16:9": (1920, 1080)}

# ---- presets de PLATAFORMA (D2): safe-zones reales de cada UI + duración ----
# cap_pos: los captions suben para no chocar con la descripción/botonera de la
# app; max_short recorta los shorts al límite de la plataforma; suffix va al
# nombre de archivo para que el usuario sepa qué subir a dónde.
PLATFORMS = {
    "none":   dict(suffix="",        cap_pos=None,  max_short=None),
    "tiktok": dict(suffix="_tiktok", cap_pos=0.72,  max_short=600),   # desc + rail derecho
    "reels":  dict(suffix="_reels",  cap_pos=0.68,  max_short=90),    # overlay inferior alto
    "shorts": dict(suffix="_shorts", cap_pos=0.74,  max_short=60),    # título inferior + rail
}

# ---- framing: FIXED blurred background + dynamic foreground video on top ----
# The bg is a static cover-crop of the scene, blurred, NEVER panned/zoomed (fixed).
# ALL motion (tracking pan, zoom punches) lives in the foreground layer only, so the
# frame/margins stay rock-steady and professional.
FG_SCALE = 0.84              # foreground video fills this fraction; rest = fixed blurred margin
BG_BLUR = 42                 # background blur strength
BG_DARKEN = -0.08            # slightly darken bg so fg pops
ZOOM_IN = 0.10               # emphasis punch-in on the FG content (display size stays fixed)
ZOOM_HOLD = 1.10             # punch-in hold (s)

# ---- studio audio chain (all local): rumble cut -> RNNoise denoise -> de-ess ->
# gentle compression -> EBU R128 loudness to the social standard (-14 LUFS) ----
_RNNOISE = paths_mod.RNNOISE
AUDIO_STUDIO = (
    f"highpass=f=80,arnndn=m={_RNNOISE}:mix=0.85,deesser,"   # mix<1: keep some natural
    "acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=2,"  # signal -> less
    "loudnorm=I=-14:TP=-1.5:LRA=11,"                         # metallic denoise artifact
    "aresample=48000"   # loudnorm upsamples internally; pin back to 48 kHz
)


def run(cmd, **kw):
    print(f"$ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def run_ffmpeg_progress(cmd, total_dur, on_pct):
    """Run ffmpeg emitting real progress (out_time) -> on_pct(0..100).
    Si on_pct lanza (p.ej. cancelación del job), el ffmpeg en curso se MATA
    antes de propagar — sin procesos zombie ni archivos a medio escribir."""
    full = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]
    p = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        for line in p.stdout:
            if line.startswith("out_time_us=") and total_dur and on_pct:
                try:
                    pct = min(100.0, int(line.split("=")[1]) / 1e6 / total_dur * 100)
                except ValueError:
                    continue                      # línea de progreso ilegible
                on_pct(pct)                       # puede lanzar JobCanceled
        p.wait()
    except BaseException:
        p.kill()
        p.wait()
        raise
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed")


def probe_duration(video):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=nk=1:nw=1", str(video)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def has_audio(video):
    """True si el video trae al menos una pista de audio."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    return "audio" in (r.stdout or "")


def extract_audio(video, wav):
    if not has_audio(video):
        raise RuntimeError("El video no tiene pista de audio. Reelfy necesita audio con "
                           "voz para transcribir y generar los subtítulos sincronizados.")
    r = subprocess.run([FFMPEG, "-y", "-i", str(video), "-ar", "16000", "-ac", "1",
                        "-c:a", "pcm_s16le", str(wav)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        tail = " ".join((r.stderr or "").strip().splitlines()[-2:]) or f"código {r.returncode}"
        raise RuntimeError(f"No se pudo extraer el audio del video: {tail}")


def transcribe(wav, out_prefix, glossary=""):
    """whisper.cpp with word-level timestamps -> JSON.

    `glossary` is a custom-vocabulary initial prompt (brand/proper-noun names)
    biasing the model — a real product feature. Carried through the whole audio.
    Fixed the visible 'Keruvin'->'Kerobin' error in testing.
    """
    # --dtw: token-level timestamps via cross-attention alignment (DTW).
    # Much tighter caption sync than the default heuristic timing.
    base = [str(WCLI), "-m", str(paths_mod.model_path()), "-f", str(wav),
            "-l", "es", "-ml", "1", "-sow", "--dtw", "large.v3.turbo",
            "-oj", "-of", str(out_prefix)]
    if glossary:
        base += ["--prompt", glossary, "--carry-initial-prompt"]
    # Primer intento con GPU (Metal). En algunas GPUs recientes (p.ej. Apple M4)
    # el backend Metal aborta (SIGABRT); se reintenta en CPU (-ng) para que
    # funcione en CUALQUIER Mac. La CPU de Apple Silicon lo corre bien, más lento.
    last = ""
    for extra in ([], ["-ng"]):
        print(f"$ whisper ({'CPU' if extra else 'GPU'})")
        r = subprocess.run(base + extra, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True)
        if r.returncode == 0 and Path(f"{out_prefix}.json").exists():
            if extra:
                print("   whisper: Metal (GPU) falló en este equipo, se usó CPU")
            return Path(f"{out_prefix}.json")
        last = " ".join((r.stderr or "").strip().splitlines()[-4:]) or f"señal/código {r.returncode}"
        print(f"   whisper {'CPU' if extra else 'GPU'} rc={r.returncode}: {last}")
    raise RuntimeError(f"La transcripción falló: {last}")


def load_words(json_path):
    """Return [{'text','start','end'}] in seconds, cleaned."""
    data = json.load(open(json_path))
    words = []
    for seg in data["transcription"]:
        txt = seg["text"].strip()
        if not txt:
            continue
        words.append({
            "text": txt,
            "start": seg["offsets"]["from"] / 1000.0,
            "end": seg["offsets"]["to"] / 1000.0,
        })
    return words


def group_phrases(words):
    """Chunk words into short caption lines by count and pauses."""
    phrases, cur = [], []
    for i, w in enumerate(words):
        if cur:
            gap = w["start"] - cur[-1]["end"]
            ends_sentence = cur[-1]["text"][-1:] in ".!?"
            if len(cur) >= MAX_WORDS_PER_LINE or gap * 1000 > GAP_SPLIT_MS or ends_sentence:
                phrases.append(cur)
                cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    return phrases


def ass_time(t):
    h = int(t // 3600); m = int((t % 3600) // 60)
    s = int(t % 60); cs = int((t - int(t)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(phrases, ass_path, w=W, h=H, style="clasico", anim="none",
              cap_color=None, cap_font=None, cap_scale=1.0, cap_pos=0.776,
              smart=True, base_color=None, kw_color=None, cap_outline=None,
              cap_shadow=None, cap_case=None, cap_margin=None, effect=None):
    """Word-by-word highlight captions, ROCK-STABLE position.

    Highlight is COLOR-ONLY (glyph metrics never change) so the phrase stays pinned;
    events globally de-overlapped + min-duration'd + capped on long pauses so captions
    never duplicate or vanish. Personalization: `cap_color` (hex) overrides the
    highlight color, `cap_font` the family, `cap_scale` the size, `cap_pos` the
    vertical baseline position (fraction of height from the top).

    Resaltado inteligente (`smart`): las palabras marcadas kw=True por
    captionsmart se pintan del color acento del estilo de forma PERSISTENTE
    (solo color -> cero reflow). Devuelve las ventanas de emoji por frase
    [(emoji, start, end)] para overlay (libass no renderiza emoji: tofu).
    """
    # El "estilo" es solo un PRESET de arranque; cada atributo se puede sobreescribir
    # individualmente (personalización total). Un preset guardado manda todos juntos.
    st = dict(STYLES.get(style, STYLES["clasico"]))
    size = max(24, int(st["size"] * float(cap_scale or 1.0)))
    active_color = (_hex_ass(cap_color) if cap_color else None) or st["active"]
    base = (_hex_ass(base_color) if base_color else None) or BASE_COLOR
    kwc = (_hex_ass(kw_color) if kw_color else None) or st.get("kw", active_color)
    font = cap_font or FONT
    cap_pos = min(0.93, max(0.15, float(cap_pos or 0.776)))
    margin_h = int(cap_margin) if cap_margin is not None else MARGIN_H
    margin_v = round(h * (1 - cap_pos))   # ASS MarginV = distance from the bottom
    outline = float(cap_outline) if cap_outline is not None else st["outline"]
    shadow = float(cap_shadow) if cap_shadow is not None else 2
    # efecto: explícito, o inferido del preset (karaoke = barrido, caja = fondo)
    eff = effect or ("karaoke" if st.get("fill") else
                     ("box" if st.get("border", 1) == 3 else "none"))
    fill = eff == "karaoke"
    border = 3 if eff == "box" else 1
    outcolor = (st.get("boxcolor") or "&H66000000") if eff == "box" else "&H00000000"
    # mayúsculas / minúsculas / Capitalizar / normal
    case = cap_case or ("upper" if st.get("upper") else "none")

    def _case(t):
        return {"upper": t.upper(), "lower": t.lower(),
                "title": t.title()}.get(case, t)

    MIN_DUR = 0.06
    MAX_HOLD = 1.2   # after this much silence the caption clears (natural)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, BorderStyle
Style: Base,{font},{size},{base},{outcolor},&H80000000,1,{outline},{shadow},2,{margin_h},{margin_h},{margin_v},{border}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # entrance animation applied ONLY to the first event of each phrase (not per word,
    # so it never re-triggers / never causes reflow). Active word resets color to white
    # (not \r) so a line-level scale animation survives across words.
    ANIM = {
        "none": "",
        "fade": r"{\fad(150,0)}",
        "pop":  r"{\fad(70,0)\t(0,150,\fscx112\fscy112)\t(150,270,\fscx100\fscy100)}",
        "slide": r"{\fad(120,0)\t(0,220,\frz0)}",  # subtle settle
    }
    max_line_w = (w - 2 * margin_h) * 0.97
    events = []   # [start, end, text, is_first_of_phrase]
    for ph in phrases:
        # AUTO-FIT: if this phrase is wider than the safe area at the base size,
        # shrink its font (per-phrase \fs tag) so it NEVER clips off-screen.
        plain = " ".join(_case(t["text"]) for t in ph)
        # shrink at most to 60% of the base size; beyond that ASS wraps to 2 lines
        # (stable within a phrase because the text never changes between events)
        fs = _fit_size(plain, size, max_line_w, floor=int(size * 0.6))
        fs_tag = f"{{\\fs{fs}}}" if fs != size else ""
        for i, active in enumerate(ph):
            start = active["start"]
            end = ph[i + 1]["start"] if i + 1 < len(ph) else active["end"]
            parts = []
            for w_ in ph:
                token = _case(w_["text"].replace("{", "(").replace("}", ")"))
                is_kw = smart and w_.get("kw")
                # La palabra clave SOLO cambia de color cuando se pronuncia (es la
                # activa) -> resalta con el color keyword; las demás palabras
                # siempre en base (sin color persistente que rompa el estilo).
                hi = kwc if is_kw else active_color
                if w_ is active:
                    if fill:
                        # karaoke: la palabra activa BARRE del color base al acento
                        # durante su duración (transición de color: métricas intactas)
                        ms = max(60, int((end - start) * 1000))
                        parts.append(f"{{\\c{base}\\t(0,{ms},\\c{hi})}}"
                                     f"{token}{{\\c{base}}}")
                    else:
                        parts.append(f"{{\\c{hi}}}{token}{{\\c{base}}}")
                else:
                    parts.append(token)
            events.append([start, end, fs_tag + " ".join(parts), i == 0])

    events.sort(key=lambda e: e[0])
    anim_tag = ANIM.get(anim, "")
    # Emitir SIN HUECOS: un evento ultra-corto no se descarta (dejaría un frame en
    # blanco = parpadeo), se funde extendiendo el anterior; los micro-huecos por
    # redondeo se cierran. Así el bloque de subtítulos nunca desaparece dentro de
    # una frase (solo se limpia tras una pausa real > MAX_HOLD).
    emitted = []   # [start, end, text, is_first_of_phrase]
    for j, ev in enumerate(events):
        s, e0, txt, first = ev
        nxt = events[j + 1][0] if j + 1 < len(events) else None
        end = e0
        if nxt is not None:
            end = min(end, nxt)                      # never overlap next -> no duplicates
            if nxt - s > MAX_HOLD:                   # long pause -> let it clear
                end = min(end, s + MAX_HOLD)
        if end - s < MIN_DUR:                        # ultra-corto: fundir en el anterior
            if emitted:
                emitted[-1][1] = max(emitted[-1][1], end)
            continue
        if emitted and 0 < s - emitted[-1][1] < 0.05:  # cerrar micro-hueco (sin parpadeo)
            emitted[-1][1] = s
        emitted.append([s, end, txt, first])
    lines = []
    for s, end, txt, first in emitted:
        pre = anim_tag if (first and anim_tag) else ""
        lines.append(f"Dialogue: 0,{ass_time(s)},{ass_time(end)},Base,,0,0,0,,{pre}{txt}")
    Path(ass_path).write_text(header + "\n".join(lines) + "\n")

    # ventanas de emoji por frase (overlay PNG flotante). Duración MÍNIMA visible
    # de 1.4 s (aunque la frase sea corta) para que el emoji se note de verdad, y
    # máximo 2.6 s. Antes duraba lo que la frase (a veces 0.3 s -> invisible).
    emojis = []
    if smart:
        for ph in phrases:
            em = ph[0].get("emoji") if ph else None
            if em:
                s0, e0 = ph[0]["start"], ph[-1]["end"]
                emojis.append((em, s0, max(s0 + 1.4, min(e0, s0 + 2.6))))
    return emojis


def is_hdr(video):
    """Detect HLG/PQ HDR by color_transfer so we can tonemap to SDR."""
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=color_transfer", "-of", "default=nk=1:nw=1", str(video)],
        capture_output=True, text=True)
    return r.stdout.strip() in ("arib-std-b67", "smpte2084")


def probe_dims(video):
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video)],
        capture_output=True, text=True)
    nums = [int(n) for n in r.stdout.strip().split("x") if n.strip()]
    return nums[0], nums[1]


def _tonemap(video):
    if is_hdr(video):
        return ["zscale=transfer=linear:npl=100", "tonemap=tonemap=hable:desat=0",
                "zscale=transfer=bt709:matrix=bt709:primaries=bt709:range=tv"]
    return []


BRAND_DIR = paths_mod.BRAND

# fuentes de emoji A COLOR por SO (libass no las renderiza: se hacen overlay PNG)
_EMOJI_FONTS = [
    "/System/Library/Fonts/Apple Color Emoji.ttc",          # macOS (sbix, strike 160)
    "C:/Windows/Fonts/seguiemj.ttf",                         # Windows (COLR)
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",     # Linux (CBDT)
]


def render_emoji_png(emoji, out_path):
    """PNG transparente con el emoji A COLOR (Pillow embedded_color). None si el
    SO no tiene fuente de emoji a color -> los emojis simplemente se omiten."""
    from PIL import Image, ImageDraw, ImageFont
    for fp in _EMOJI_FONTS:
        if not Path(fp).exists():
            continue
        try:
            size = 160 if "Apple" in fp else 128   # Apple: strike bitmap fijo
            f = ImageFont.truetype(fp, size)
            img = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.text((size // 2, size // 2), emoji, font=f, embedded_color=True)
            box = img.getbbox()
            if not box:
                return None
            img.crop(box).save(out_path)
            return str(out_path)
        except Exception:  # noqa
            continue
    return None


def reframe_and_burn(video, ass_path, out, fps=30, track=True, cmds_path=None, beats=None,
                     on_pct=None, preview_secs=None, preview_start=0.0, enhance_audio=False,
                     out_w=W, out_h=H, camera=None, keeps=None,
                     zoom_amt=ZOOM_IN, air=None, logo=None, emojis=None, emoji_y=None,
                     broll=None, low_res=False):
    """Composite a FIXED blurred background + a dynamic foreground video, burn captions.

    Layers (frame/margins never move; only the video moves): static blurred BG cover +
    tracked/zoomed FG inset + captions. `out_w/out_h` set the aspect (9:16/1:1/4:5);
    `camera` (fraction trajectory) re-emits the pan crop for that aspect.
    Apple Silicon fast path: VideoToolbox decode/encode; tonemap + blur at low res.
    """
    ow, oh = out_w, out_h
    src_w, src_h = probe_dims(video)
    tm = _tonemap(video)
    if tm:
        print("   HDR -> tonemap HLG/PQ to SDR (both layers)")
    # DYNAMIC margins: air only where the aspect mismatch actually crops content.
    #  - source aspect ~= output aspect  -> full-bleed, NO margins
    #  - source WIDER than output        -> air on the SIDES only (full height)
    #  - source TALLER than output       -> air TOP/BOTTOM only (full width)
    inset = 1 - min(0.35, max(0.0, air if air is not None else (1 - FG_SCALE)))
    src_ar, out_ar = src_w / src_h, ow / oh
    if abs(src_ar / out_ar - 1) < 0.05 or inset >= 0.995:
        fg_w, fg_h = ow, oh
    elif src_ar > out_ar:
        fg_w, fg_h = (round(ow * inset) // 2) * 2, oh
    else:
        fg_w, fg_h = ow, (round(oh * inset) // 2) * 2

    # --- foreground: tracked (or static) crop AT THE FG ASPECT scaled to its box ---
    cmds = None
    if track:
        cw, ch = reframe_track.crop_dims(src_w, src_h, fg_w, fg_h)
        if cw >= src_w:                                   # no horizontal room to pan
            cmds = None
        elif camera:                                      # regen pan for THIS fg aspect
            cmds = str(Path(ass_path).with_suffix(f".{fg_w}x{fg_h}.crop.txt"))
            reframe_track.write_cmds(camera, src_w, cw, cmds)
        else:                                             # CLI/no-analyze: detect now
            cw, ch, cmds, _ = reframe_track.build(video, src_w, src_h, cmds_path,
                                                  fg_w, fg_h)
    if cmds:
        fg = [f"sendcmd=f={cmds}", f"crop@fgc={cw}:{ch}:x=0:y=(ih-{ch})/2", f"scale={fg_w}:{fg_h}"]
    else:
        fg = [f"scale={fg_w}:{fg_h}:force_original_aspect_ratio=increase", f"crop={fg_w}:{fg_h}"]
    fg = fg + [f"fps={fps}"] + tm   # sendcmd MUST precede fps or its crop-x cmds don't land
    if beats and zoom_amt > 0.004:   # hard-cut punch-in on the fg CONTENT (display size fixed)
        win = "+".join(f"between(t,{b:.3f},{b + ZOOM_HOLD:.3f})" for b in beats)
        z = f"(1+{zoom_amt}*({win}))"
        fg += [f"scale=w='ceil({fg_w}*{z}/2)*2':h='ceil({fg_h}*{z}/2)*2':eval=frame",
               f"crop={fg_w}:{fg_h}"]
    fg += ["format=yuv420p"]

    # --- background: static blurred cover-fill (blur at low res -> ~15x cheaper) ---
    bw = 270; bh = (round(bw * oh / ow) // 2) * 2
    bg = ([f"scale={bw}:{bh}:force_original_aspect_ratio=increase", f"crop={bw}:{bh}", f"fps={fps}"]
          + tm + [f"gblur=sigma={BG_BLUR/3.6:.0f}", f"scale={ow}:{oh}",
                  f"eq=brightness={BG_DARKEN}:saturation=0.9", "format=yuv420p"])

    vf = (f"split=2[bgsrc][fgsrc];"
          f"[bgsrc]{','.join(bg)}[bg];"
          f"[fgsrc]{','.join(fg)}[fg];"
          f"[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2[c]")
    # B-roll: cutaways de stock a PANTALLA COMPLETA, DEBAJO de los captions y en
    # timeline original (como subs/emojis). setpts desplaza el clip a su ventana;
    # eof_action=pass devuelve al video base si el clip es más corto.
    last = "c"
    for j, (bp, bs, be) in enumerate((broll or [])[:6]):
        vf += (f";movie='{bp}',scale={ow}:{oh}:force_original_aspect_ratio=increase,"
               f"crop={ow}:{oh},fps={fps},format=yuv420p,setpts=PTS+{bs:.3f}/TB[bk{j}];"
               f"[{last}][bk{j}]overlay=eof_action=pass:"
               f"enable='between(t,{bs:.3f},{be:.3f})'[r{j}]")
        last = f"r{j}"
    vf += f";[{last}]subtitles={ass_path}" if broll else f";[c]subtitles={ass_path}"
    # emojis del resaltado inteligente: overlay PNG flotante centrado ARRIBA del
    # bloque de captions, visible en la ventana de su frase (timeline original,
    # ANTES del recorte de silencios para que siga a sus frames como los subs).
    if emojis:
        ew = (round(ow * 0.115) // 2) * 2                  # ~11.5% del ancho
        ey = emoji_y if emoji_y is not None else round(oh * 0.60)
        for k, (png, es, ee) in enumerate(emojis[:14]):    # tope sano de overlays
            vf += (f"[e{k}a];movie='{png}',scale={ew}:-1,format=rgba[em{k}];"
                   f"[e{k}a][em{k}]overlay=x=(W-w)/2:y={ey}:"
                   f"enable='between(t,{es:.3f},{ee:.3f})'")
    if logo:
        lf = BRAND_DIR / "logo.png"
        if lf.exists():
            lw = (round(ow * float(logo.get("size", 0.14))) // 2) * 2
            op = float(logo.get("opacity", 0.9))
            mx, my = round(ow * 0.03), round(oh * 0.03)
            pos = logo.get("pos", "br")
            ox = f"{mx}" if pos in ("tl", "bl") else f"W-w-{mx}"
            oy = f"{my}" if pos in ("tl", "tr") else f"H-h-{my}"
            vf += (f"[cs];movie='{lf}',scale={lw}:-1,format=rgba,"
                   f"colorchannelmixer=aa={op}[lg];[cs][lg]overlay={ox}:{oy}")
    # ZERO-CASCADE silence trim: select/aselect fused into THIS render (after captions,
    # so burned subs follow their frames). One total encode instead of render+tighten,
    # which was re-compressing AAC and bringing the robotic artifact back. adeclick
    # repairs any impulse at the jump-cut junctions.
    af_parts = [AUDIO_STUDIO] if enhance_audio else []
    total = preview_secs or probe_duration(video)
    if keeps:
        sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)
        vf += f",select='{sel}',setpts=N/FRAME_RATE/TB"
        af_parts.append(f"aselect='{sel}',asetpts=N/SR/TB,adeclick")
        if not preview_secs:
            total = sum(b - a for a, b in keeps)
    bitrate = "12M"
    seek = []
    windowed = bool(preview_secs and preview_start and preview_start > 0.05)
    if low_res and not preview_secs:      # PROXY: mismo pipeline (=export), solo 540p
        pv_h = (round(540 * oh / ow) // 2) * 2
        vf += f",scale=540:{pv_h}"
        bitrate = "6M"
    if preview_secs:                      # fast low-res sample (ventana del preview)
        pv_h = (round(540 * oh / ow) // 2) * 2
        vf += f",scale=540:{pv_h}"
        bitrate = "6M"
        if windowed:
            # VENTANA ARBITRARIA alrededor del playhead: input-seek (-ss ANTES de
            # -i, rápido: salta al keyframe) + setpts=PTS+start/TB para RESTAURAR
            # el tiempo absoluto, de modo que subtítulos/tracking/beats/emojis/
            # B-roll (todos en tiempo absoluto) sigan alineados. Verificado: la
            # palabra correcta se resalta y cuesta ~1.3s aun a t=90 de un video 3min.
            # Solo-video: es una vista del LOOK; el audio se oye en 'Muestra exacta'.
            seek = ["-ss", f"{preview_start:.3f}"]
            vf = f"setpts=PTS+{preview_start:.3f}/TB," + vf
    cmd = [FFMPEG, "-y", "-hwaccel", "videotoolbox"] + seek + ["-i", str(video),
           "-vf", vf, "-c:v", "h264_videotoolbox", "-b:v", bitrate]
    if windowed:
        cmd += ["-an"]                    # preview de ventana: sin audio (rápido y sin desync)
    else:
        if af_parts:
            cmd += ["-af", ",".join(af_parts)]
        # 192k base: shorts derive from this render, so give downstream steps headroom
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    if preview_secs:
        # windowed: setpts dejó los PTS en tiempo absoluto -> acotar con -to (fin
        # absoluto). Con -t, ffmpeg ve PTS ya > secs y no codifica nada.
        cmd += (["-to", f"{preview_start + preview_secs:.3f}"] if windowed
                else ["-t", str(preview_secs)])
    cmd += [str(out)]
    if on_pct:
        run_ffmpeg_progress(cmd, total, on_pct)
    else:
        run(cmd)


MUSIC_DIR = paths_mod.MUSIC_DIR


def add_music(video_in, video_out, track="ambient", volume=0.26):
    """Mix a royalty-free bed under the voice with sidechain DUCKING (music drops
    while you speak). Video is stream-copied -> only audio re-encodes -> fast."""
    m = MUSIC_DIR / f"{track}.m4a"
    if not m.exists():
        Path(video_in).replace(video_out); return
    filt = (f"[1:a]volume={volume}[bg];"
            f"[bg][0:a]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=250[duck];"
            f"[0:a][duck]amix=inputs=2:duration=first:dropout_transition=0[a]")
    run([FFMPEG, "-y", "-i", str(video_in), "-stream_loop", "-1", "-i", str(m),
         "-filter_complex", filt, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(video_out)])


def make_hook_ass(title, w, h, out, dur=3.4):
    """Viral-style animated hook for the first `dur`s.

    - UPPERCASE, heavy outline + shadow (no flat black box)
    - key words (the longer ones) highlighted in brand amber
    - amber GLOW layer behind the text (blurred copy)
    - pop-in with overshoot (30% -> 116% -> 100%) + fade out
    """
    size = max(34, int(w * 0.072))
    AMBER = r"\c&H20B0FF&"      # #FFB020 in ASS BGR
    WHITE = r"\c&HFFFFFF&"
    words = (title or "").upper().split()
    if words:  # AUTO-FIT: libass wraps lines but cannot break a WORD -> make sure
        size = _fit_size(max(words, key=len), size, w * 0.84, floor=26)  # the longest fits
    # highlight the informative words: alternate among words of >=5 chars
    longs = [i for i, t in enumerate(words) if len(t) >= 5]
    hot = set(longs[::2] if longs else [])
    styled = " ".join(f"{{{AMBER}}}{t}{{{WHITE}}}" if i in hot else t
                      for i, t in enumerate(words))
    pop = (r"{\fad(120,420)\fscx30\fscy30"
           r"\t(0,190,\fscx116\fscy116)\t(190,330,\fscx100\fscy100)}")
    glow = (r"{\fad(120,420)\blur14\bord6\fscx30\fscy30"
            r"\t(0,190,\fscx116\fscy116)\t(190,330,\fscx100\fscy100)}")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Hook,{FONT},{size},&H00FFFFFF,&H00000000,&H96000000,1,1,{max(4, size // 11)},{max(2, size // 20)},8,{int(w*0.07)},{int(w*0.07)},{int(h*0.10)}
Style: HookGlow,{FONT},{size},&H0020B0FF,&H0020B0FF,&H00000000,1,1,{max(4, size // 11)},0,8,{int(w*0.07)},{int(w*0.07)},{int(h*0.10)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,{ass_time(dur)},HookGlow,,0,0,0,,{glow}{(title or '').upper()}
Dialogue: 1,0:00:00.00,{ass_time(dur)},Hook,,0,0,0,,{pop}{styled}
"""
    Path(out).write_text(header)
    return out


def cut_clip(full_out, start, end, clip_out, on_pct=None):
    """Cut a highlight [start,end] from the fully-rendered vertical (captions and
    tracking already baked). Video re-encodes for frame accuracy; audio STREAM-COPIES
    (zero generation loss — cascaded AAC re-encodes were making shorts sound robotic)."""
    cmd = [FFMPEG, "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(full_out),
           "-c:v", "h264_videotoolbox", "-b:v", "12M", "-c:a", "copy", str(clip_out)]
    if on_pct:
        run_ffmpeg_progress(cmd, max(0.1, end - start), on_pct)
    else:
        run(cmd)


def finish_short(clip_in, clip_out, dynamic, hook_title, w, h):
    """ONE finishing pass per short: silence-trim (jump cuts) and/or hook overlay.
    Merging them (instead of tighten->hook as separate re-encodes) keeps the audio
    at a single extra AAC generation max — and none at all if only the hook runs."""
    vf, af = [], None
    if dynamic:
        keeps, dur = edit_mod.keep_segments(clip_in)
        if keeps and sum(b - a for a, b in keeps) < dur - 0.2:
            sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)
            vf += [f"select='{sel}'", "setpts=N/FRAME_RATE/TB"]
            af = f"aselect='{sel}',asetpts=N/SR/TB"
    if hook_title:
        ass = Path(clip_out).with_suffix(".hook.ass")
        make_hook_ass(hook_title, w, h, ass)
        vf += [f"subtitles={ass}"]   # after select -> hook rides the FINAL timeline
    if not vf:
        Path(clip_in).replace(clip_out); return
    cmd = [FFMPEG, "-y", "-i", str(clip_in), "-vf", ",".join(vf),
           "-c:v", "h264_videotoolbox", "-b:v", "12M"]
    cmd += (["-af", af, "-c:a", "aac", "-b:a", "192k"] if af else ["-c:a", "copy"])
    cmd += [str(clip_out)]
    run(cmd)
    Path(clip_out).with_suffix(".hook.ass").unlink(missing_ok=True)


def analyze(video, glossary="", n=2, align=True, on_step=None):
    """Light phase (no heavy render): audio -> transcribe -> align -> phrases +
    highlights + emphasis beats + tracking trajectory. Returns an EDITABLE plan dict
    (JSON-serializable) the UI can review/tweak before rendering."""
    def step(p, m):
        print(f"   [{p}%] {m}")
        if on_step:
            on_step(p, m)

    video = Path(video).resolve()
    work = WORK
    stem = video.stem
    wav = work / f"{stem}.wav"; prefix = work / stem; cmds = work / f"{stem}.crop.txt"

    step(6, "Extrayendo audio…"); extract_audio(video, wav)
    gloss = f"Nombres propios y términos: {glossary}." if glossary.strip() else ""
    step(18, "Transcribiendo (español)…"); jp = transcribe(wav, prefix, gloss)
    words = load_words(jp)
    if align:
        step(42, "Afinando la sincronía de subtítulos…")
        try:
            words = align_mod.align_words(wav, words)
        except Exception as e:  # noqa
            print(f"   alignment failed ({e}); whisper timings")
    phrases = group_phrases(words)
    step(60, "Marcando palabras clave y emojis (IA)…")
    try:
        import captionsmart
        captionsmart.annotate(phrases)     # marca word['kw'] y emoji por frase
    except Exception as e:  # noqa: sin LLM el video sale igual, solo sin resaltado
        print(f"   captionsmart falló ({e}); sin resaltado inteligente")
    step(66, "Eligiendo los mejores momentos (IA)…")
    highlights = hl.select_highlights(hl.build_sentences(words), n=n)
    for h in highlights:
        h["enabled"] = True
    step(78, "Detectando momentos de énfasis…")
    beats, _ = edit_mod.emphasis_beats(wav)
    broll_sugg = broll_mod.suggest(hl.build_sentences(words), probe_duration(video),
                                   topic=glossary)   # ancla el stock al tema/producto real
    if broll_sugg:
        print(f"   broll: {[b['query'] for b in broll_sugg]}")
    step(84, "Detectando silencios y muletillas…")
    keeps, src_len = edit_mod.keep_segments(str(wav))
    cuts, t_prev = [], 0.0                 # complement of keeps = editable cut track
    for a, b in keeps:
        if a - t_prev > 0.05:
            cuts.append({"start": round(t_prev, 2), "end": round(a, 2), "enabled": True})
        t_prev = b
    if src_len - t_prev > 0.05:
        cuts.append({"start": round(t_prev, 2), "end": round(src_len, 2), "enabled": True})
    step(90, "Calculando el seguimiento de cámara…")
    src_w, src_h = probe_dims(video)
    _, _, _, camera = reframe_track.build(video, src_w, src_h, str(cmds))
    step(100, "Análisis listo")
    return {
        "stem": stem, "video": str(video), "wav": str(wav),
        "dims": [src_w, src_h], "duration": probe_duration(video),
        "cmds_path": str(cmds), "camera": camera,
        "phrases": [[{"text": w["text"], "start": w["start"], "end": w["end"],
                      # resaltado inteligente: conservar keywords y emoji de frase
                      **({"kw": True} if w.get("kw") else {}),
                      **({"emoji": w["emoji"]} if w.get("emoji") else {})} for w in ph]
                    for ph in phrases],
        "highlights": highlights,
        "beats": beats,
        "cuts": cuts,
        "broll": broll_sugg,
    }


def _emoji_overlays(emoji_windows, stem):
    """[(emoji, s, e)] -> [(png, s, e)] renderizando cada emoji único una vez."""
    out, cache = [], {}
    for em, s, e in emoji_windows:
        if em not in cache:
            png = WORK / f"{stem}_em_{len(cache)}.png"
            cache[em] = render_emoji_png(em, png)
        if cache[em]:
            out.append((cache[em], s, e))
    return out or None


def render_from_plan(plan, out_dir, dynamic=True, enhance_audio=False, style="clasico",
                     anim="none", fmt="9:16", music=False, music_track="ambient",
                     music_volume=0.26, hook=False, lang="es", custom=None,
                     smart=True, platform="none", broll=False, proxy=False,
                     proxy_out=None, on_step=None, on_pct=None):
    """Heavy phase: apply the (possibly edited) plan -> full video + enabled shorts.
    on_pct(stage, percent) reports real ffmpeg progress per stage."""
    def step(m):
        print(f"   {m}")
        if on_step:
            on_step(m)

    ow, oh = FORMATS.get(fmt, FORMATS["9:16"])
    stem = plan["stem"]; video = Path(plan["video"]); camera = plan.get("camera")
    out_dir = Path(out_dir); out_dir.mkdir(exist_ok=True)
    phrases, titles = plan["phrases"], {}
    if lang != "es":
        hl_titles = [h.get("title", "") for h in plan["highlights"]]
        if lang == "en" and plan.get("phrases_en"):    # pre-translated (and possibly
            phrases = plan["phrases_en"]               # user-EDITED) in the studio
            ten = plan.get("titles_en") or []
            titles = dict(zip(hl_titles, ten)) if len(ten) == len(hl_titles) else {}
            if not titles:
                titles = dict(zip(hl_titles, translate_mod.translate_texts(hl_titles)))
        else:
            name = translate_mod.LANGS.get(lang, lang)
            step(f"Traduciendo subtítulos al {name}…")
            phrases = translate_mod.translate_phrases(phrases, lang)
            titles = dict(zip(hl_titles, translate_mod.translate_texts(hl_titles, lang)))
    c = custom or {}
    pf = PLATFORMS.get(platform or "none", PLATFORMS["none"])
    # safe-zone de la plataforma: sube los captions salvo que el usuario los
    # haya colocado explícitamente (su ajuste manda)
    cap_pos = c.get("cap_pos") or pf["cap_pos"] or 0.776
    ass = WORK / f"{stem}.ass"
    emoji_windows = build_ass(phrases, ass, ow, oh, style, anim,   # rebuild from edited captions
                              c.get("cap_color"), c.get("cap_font"),
                              c.get("cap_scale", 1.0), cap_pos, smart=smart,
                              base_color=c.get("base_color"), kw_color=c.get("kw_color"),
                              cap_outline=c.get("cap_outline"), cap_shadow=c.get("cap_shadow"),
                              cap_case=c.get("cap_case"), cap_margin=c.get("cap_margin"),
                              effect=c.get("effect"))
    emojis = _emoji_overlays(emoji_windows, stem)
    broll_clips = None
    if broll and plan.get("broll"):
        step("Buscando B-roll de stock…")
        broll_clips = broll_mod.resolve(plan["broll"])
    beats = plan.get("beats") if dynamic else None

    # ZERO-CASCADE: silence-trim happens INSIDE the main render (one encode total).
    # keeps = complement of the ENABLED cuts from the plan (the user can restore any
    # cut in the editor). Highlight timestamps get remapped onto the trimmed timeline.
    keeps = None
    if dynamic:
        src_dur = plan.get("duration") or probe_duration(video)
        cuts = sorted((c for c in plan.get("cuts", []) if c.get("enabled", True)),
                      key=lambda c: c["start"])
        if cuts:
            keeps, t = [], 0.0
            for c in cuts:
                if c["start"] - t > 0.05:
                    keeps.append((t, c["start"]))
                t = max(t, c["end"])
            if src_dur - t > 0.05:
                keeps.append((t, src_dur))
        elif "cuts" not in plan:                            # older plans: detect now
            keeps, src_dur = edit_mod.keep_segments(plan["wav"])
        removed = src_dur - sum(b - a for a, b in keeps) if keeps else 0.0
        if removed < 0.2:
            keeps = None                                    # nothing worth trimming
        else:
            print(f"   trim fusionado al render: -{removed:.1f}s de silencios/muletillas")

    def remap(t):
        if not keeps:
            return t
        acc = 0.0
        for a, b in keeps:
            if t <= a:
                return acc
            if t <= b:
                return acc + (t - a)
            acc += b - a
        return acc

    step("Montando el video…" if not proxy else "Generando vista real…")
    full = Path(proxy_out) if (proxy and proxy_out) else out_dir / f"{stem}_reelfy{pf['suffix']}.mp4"
    reframe_and_burn(video, ass, full, cmds_path=plan["cmds_path"], beats=beats,
                     enhance_audio=enhance_audio, out_w=ow, out_h=oh, camera=camera,
                     keeps=keeps, zoom_amt=float(c.get("zoom_amt", ZOOM_IN)),
                     air=c.get("air"), logo=c.get("logo"), emojis=emojis,
                     emoji_y=round(oh * (cap_pos - 0.18)), broll=broll_clips, low_res=proxy,
                     on_pct=(lambda p: on_pct("full", p)) if on_pct else None)
    if music:
        step("Añadiendo música de fondo…")
        tmp = out_dir / f"{plan['stem']}_mus.mp4"
        add_music(full, tmp, music_track, music_volume); tmp.replace(full)
    # PROXY (vista real completa): mismo pipeline+cortes que el export, solo 540p.
    # No genera shorts ni miniaturas: es solo para reproducir el resultado exacto.
    if proxy:
        return [{"name": "Vista real", "file": full.name}]
    clips = [{"name": "Video completo", "file": full.name}]

    enabled = [h for h in plan["highlights"] if h.get("enabled", True)]
    for k, h in enumerate(enabled, 1):
        step(f"Cortando short {k}…")
        clip = out_dir / f"{stem}_short{k}{pf['suffix']}.mp4"
        s0, e0 = remap(h["start"]), remap(h["end"])
        if pf["max_short"] and e0 - s0 > pf["max_short"]:   # límite de la plataforma
            e0 = s0 + pf["max_short"]
        cut_clip(full, s0, e0, clip,
                 on_pct=(lambda p, k=k: on_pct(f"short{k}", p)) if on_pct else None)
        title = titles.get(h.get("title", ""), h.get("title", ""))
        if hook and title:
            # hook only: video re-encodes, audio STREAM-COPIES (silences already
            # trimmed in the main render -> shorts never re-encode audio again)
            tmp = out_dir / f"{stem}_short{k}_fin.mp4"
            finish_short(clip, tmp, False, title, ow, oh)
            tmp.replace(clip)
        thumb = out_dir / f"{stem}_short{k}_thumb.jpg"
        try:
            thumb_mod.make_thumbnail(clip, thumb, title)
        except Exception as e:  # noqa
            print(f"   thumbnail failed: {e}"); thumb = None
        clips.append({"name": f"Short {k}: {h.get('title', '')}".strip(" :"),
                      "file": clip.name,
                      "thumb": thumb.name if thumb and thumb.exists() else None})
    return clips


def render_preview(plan, out, secs=7, enhance_audio=False, style="clasico", anim="none",
                   fmt="9:16", music=False, music_track="ambient", music_volume=0.26,
                   lang="es", custom=None, smart=True, start=0.0, platform="none", broll=False):
    """Fast, low-res sample (the real look: captions, tracking, blurred bg, zoom,
    studio audio, music, chosen style/format/lang). `start`>0 rinde una VENTANA
    alrededor de ese segundo (auto-preview del playhead); start=0 los primeros `secs`."""
    ow, oh = FORMATS.get(fmt, FORMATS["9:16"])
    stem = plan["stem"]
    if lang == "en":
        phrases = plan.get("phrases_en") or translate_mod.translate_phrases(plan["phrases"])
    elif lang != "es":
        phrases = translate_mod.translate_phrases(plan["phrases"], lang)
    else:
        phrases = plan["phrases"]
    c = custom or {}
    pf = PLATFORMS.get(platform or "none", PLATFORMS["none"])
    cap_pos = c.get("cap_pos") or pf["cap_pos"] or 0.776
    ass = WORK / f"{stem}.ass"
    emoji_windows = build_ass(phrases, ass, ow, oh, style, anim,
                              c.get("cap_color"), c.get("cap_font"),
                              c.get("cap_scale", 1.0), cap_pos, smart=smart,
                              base_color=c.get("base_color"), kw_color=c.get("kw_color"),
                              cap_outline=c.get("cap_outline"), cap_shadow=c.get("cap_shadow"),
                              cap_case=c.get("cap_case"), cap_margin=c.get("cap_margin"),
                              effect=c.get("effect"))
    # emojis dentro de la ventana del preview [start, start+secs]
    lo, hi = start, start + secs
    emojis = _emoji_overlays([w for w in emoji_windows if lo <= w[1] <= hi or lo <= w[2] <= hi], stem)
    # B-roll: mostrar el clip real en su ventana (si cae en el preview)
    broll_clips = None
    if broll and plan.get("broll"):
        try:
            broll_clips = broll_mod.resolve([b for b in plan["broll"]
                                             if b.get("enabled", True) and b["end"] >= lo and b["start"] <= hi])
        except Exception as e:  # noqa
            print(f"   preview broll: {e}")
    out = Path(out)
    tmp = out.with_name(out.stem + "_raw.mp4") if music else out
    reframe_and_burn(Path(plan["video"]), ass, tmp, cmds_path=plan["cmds_path"],
                     beats=plan.get("beats"), preview_secs=secs, preview_start=start,
                     broll=broll_clips, enhance_audio=enhance_audio,
                     out_w=ow, out_h=oh, camera=plan.get("camera"),
                     zoom_amt=float(c.get("zoom_amt", ZOOM_IN)),
                     air=c.get("air"), logo=c.get("logo"), emojis=emojis,
                     emoji_y=round(oh * (cap_pos - 0.18)))
    if music and not (start and start > 0.05):    # ventana solo-video: sin música
        add_music(tmp, out, music_track, music_volume); tmp.unlink(missing_ok=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("-g", "--glossary", default="")
    ap.add_argument("--highlights", type=int, default=2)
    ap.add_argument("--no-align", action="store_true")
    ap.add_argument("--dynamic", action="store_true")
    args = ap.parse_args()
    plan = analyze(args.video, args.glossary, args.highlights, align=not args.no_align)
    out_dir = Path(args.out).parent if args.out else paths_mod.OUTPUT
    for c in render_from_plan(plan, out_dir, dynamic=args.dynamic):
        print(f"✅ {c['name']} -> {c['file']}")


if __name__ == "__main__":
    main()
