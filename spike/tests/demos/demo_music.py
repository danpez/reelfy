"""Demo visual/auditiva de motion/music.py (no podemos escuchar: validamos con espectrogramas,
tempo, loudness del arranque, drop, costuras, silencios y picos; Kevin escucha los MP4).

Casos principales = la cfg EXACTA que emite el director para la dentista (prompt, bpm, stop y
drop_at remapeados a tiempo final con la misma Timeline que usa render.py), por estilo, con ACE y
con el generador procedural (fallback sin modelos).

Salida en spike/work/demos/music/:
  sheet_<caso>.png     espectrograma + forma de onda con beats, downbeats, secciones, stops (gris),
                       costuras (cian), fin del hook, drop y final; loudness del arranque vs total
  overview.png         todas las hojas apiladas
  <caso>.mp4           musica con playhead sobre la hoja (para escuchar)
  preview_voz_<caso>.mp4  voz real de la prueba (con los cortes del plan) + musica con los stops del
                       director y ducking simple de ffmpeg (solo referencia; el mezclador real es audio.py)
  metrics.json         metricas por caso

Uso:  cd spike/scripts && ../.venv/bin/python ../tests/demos/demo_music.py [--no-ai] [--no-mp4] [--only a,b]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
SPIKE = HERE.parents[2]
sys.path.insert(0, str(SPIKE / "scripts"))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from motion import assets, music as M, timeline as TL  # noqa: E402

OUT = SPIKE / "work" / "demos" / "music"
PLANS = SPIKE / "work" / "demos" / "director"
PLAN_RENDER = SPIKE / "work" / "demos" / "render" / "dentista_plan_v2.json"
MEDIA = Path(__import__("os").environ.get("REELFY_TEST_MEDIA") or Path(__file__).resolve().parents[2] / "work" / "media")
W = 1800
LABEL_COL = {"intro": (120, 170, 255), "pickup": (150, 150, 200), "verse": (90, 200, 160), "build": (255, 190, 80),
             "drop": (255, 95, 110), "main": (255, 130, 90), "break": (170, 120, 230), "outro": (150, 150, 150),
             "ending": (230, 230, 230), "hook": (255, 150, 200)}


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    name = "Poppins-SemiBold.ttf" if bold else "Poppins-Medium.ttf"
    try:
        return ImageFont.truetype(str(assets.FONTS_DIR / name), size)
    except Exception:
        return ImageFont.load_default()


def director_case(style: str) -> tuple[dict, float, list[tuple[float, float]]] | None:
    """cfg de musica del plan dirigido (como la ve get_music dentro de render: stops y drop_at en
    tiempo final), duracion final y keeps de la Timeline."""
    f = PLAN_RENDER if style == "dinamico" and PLAN_RENDER.exists() else PLANS / f"plan_v2_{style}.json"
    if not f.exists():
        return None
    plan = json.loads(f.read_text())
    tl = TL.Timeline.from_plan(plan)
    m = dict(plan["fx"]["music"])
    m["stops"] = [dict(s, at=tl.remap_clamped(float(s["at"]))) for s in m.get("stops") or [] if "at" in s]
    if m.get("drop_at") is not None:
        m["drop_at"] = tl.remap_clamped(float(m["drop_at"]))
    m.pop("swells", None)
    return m, float(tl.final_duration), list(tl.keeps)


def spectrogram(wav: str, path: Path, w: int, h: int) -> Image.Image:
    subprocess.run([M.FFMPEG, "-v", "error", "-y", "-i", wav, "-lavfi",
                    f"showspectrumpic=s={w}x{h}:legend=0:scale=log:fscale=log:color=magma", str(path)], check=True)
    return Image.open(path).convert("RGB").resize((w, h))


def sheet(case: str, res: dict, metrics: dict, cfg: dict, note: str = "") -> Path:
    D = res["duration"]
    y = M.load_audio(res["wav"])
    mono = y.mean(axis=1)
    top, spec_h, wave_h, sec_h, pad = 140, 300, 190, 34, 30
    H = top + spec_h + 10 + sec_h + 6 + wave_h + 64
    img = Image.new("RGB", (W, H), (14, 14, 18))
    dr = ImageDraw.Draw(img, "RGBA")
    x0, x1 = pad, W - pad
    pw = x1 - x0

    def X(t: float) -> float:
        return x0 + pw * t / D

    dr.text((pad, 16), case, font=_font(34), fill=(245, 245, 245))
    sub = (f"{res.get('source')} | {res.get('engine', '')} | {res.get('bpm', 0):.1f} BPM | {res.get('keyscale') or ''} | "
           f"final {res['ending']['mode']} @ {res['ending']['hit']:.2f}s | loops {res.get('loops', 0)} | "
           f"drop {res.get('drop') if res.get('drop') is None else round(res['drop'], 2)} | "
           f"pulso {res.get('beat_confidence')}")
    dr.text((pad, 60), sub, font=_font(20, False), fill=(190, 190, 200))
    met = (f"LUFS {metrics['lufs']:.1f}  arranque(0-2s) {metrics['lu_first2'] - metrics['lufs']:+.1f} LU  "
           f"TP {metrics['true_peak_db']:.1f}  clip {metrics['clipped']}  tempo {metrics['tempo']:.1f} "
           f"(err {100 * (metrics['tempo_err'] or 0):.1f}%)  silencios {len(metrics['silences'])}  "
           f"gen {res.get('gen_s', 0):.1f}s")
    dr.text((pad, 92), met, font=_font(20, False), fill=(160, 220, 170))
    sp = spectrogram(res["wav"], OUT / f"_spec_{case}.png", pw, spec_h)
    img.paste(sp, (x0, top))
    ys = top + spec_h + 10
    yw = ys + sec_h + 6
    # stops del director (audio.py silencia ahi): sombreado
    for s in cfg.get("stops") or []:
        a, b = float(s["at"]), float(s["at"]) + float(s.get("dur", 0.6))
        dr.rectangle([X(a), top, X(b), yw + wave_h], fill=(200, 200, 200, 70))
        dr.text((X(a) + 4, top + spec_h - 26), "stop", font=_font(16), fill=(240, 240, 240))
    for s in res["sections"]:
        c = LABEL_COL.get(s["label"], (200, 200, 200))
        dr.rectangle([X(s["start"]), ys, X(s["end"]) - 2, ys + sec_h], fill=tuple(int(v * 0.55) for v in c))
        dr.text((X(s["start"]) + 6, ys + 5), s["label"], font=_font(18), fill=(250, 250, 250))
    mid = yw + wave_h / 2
    cols = np.array_split(np.abs(mono), pw)
    pk = np.array([c.max() if len(c) else 0 for c in cols])
    rms = np.array([np.sqrt(np.mean(c ** 2)) if len(c) else 0 for c in cols])
    for i in range(pw):
        h1, h2 = pk[i] * wave_h / 2, rms[i] * wave_h / 2
        dr.line([(x0 + i, mid - h1), (x0 + i, mid + h1)], fill=(70, 90, 140))
        dr.line([(x0 + i, mid - h2), (x0 + i, mid + h2)], fill=(140, 180, 255))
    for s in res.get("seams") or []:   # costuras (debajo de los downbeats: una costura en downbeat se ve amarilla)
        dr.line([(X(s), top), (X(s), yw + wave_h)], fill=(80, 230, 255), width=2)
    for b in res["beats"]:
        dr.line([(X(b), yw), (X(b), yw + 12)], fill=(255, 255, 255), width=1)
    for b in res["downbeats"]:
        dr.line([(X(b), yw), (X(b), yw + wave_h)], fill=(255, 210, 63), width=2)
    if res.get("hook_end") is not None:
        dr.text((X(res["hook_end"]) - 70, top + 30), "hook", font=_font(18), fill=(255, 150, 200))
    if res.get("drop") is not None:
        dr.line([(X(res["drop"]), top), (X(res["drop"]), yw + wave_h)], fill=(255, 80, 100), width=3)
        dr.text((X(res["drop"]) + 5, top + 6), "drop", font=_font(18), fill=(255, 120, 140))
    eh = res["ending"]["hit"]
    dr.line([(X(eh), top), (X(eh), yw + wave_h)], fill=(120, 255, 170), width=3)
    dr.text((X(eh) - 60, top + 6), "final", font=_font(18), fill=(120, 255, 170))
    ya = yw + wave_h + 8
    for t in np.arange(0, D + 1e-6, 2.0 if D <= 40 else 5.0):
        dr.line([(X(t), ya), (X(t), ya + 8)], fill=(150, 150, 150))
        dr.text((X(t) - 10, ya + 12), f"{t:.0f}s", font=_font(16, False), fill=(150, 150, 160))
    if note:
        dr.text((pad, H - 24), note, font=_font(16, False), fill=(150, 150, 160))
    p = OUT / f"sheet_{case}.png"
    img.save(p)
    return p


def mp4(case: str, res: dict, sheet_png: Path) -> Path:
    """Hoja + playhead + audio (para que Kevin escuche)."""
    D = res["duration"]
    out = OUT / f"{case}.mp4"
    x0, pw = 30, W - 60
    vf = (f"scale={W}:-2,drawbox=x='{x0}+{pw}*t/{D:.3f}':y=130:w=3:h=ih-190:color=white@0.9:t=fill")
    subprocess.run([M.FFMPEG, "-v", "error", "-y", "-loop", "1", "-framerate", "15", "-i", str(sheet_png),
                    "-i", res["wav"], "-vf", vf, "-t", f"{D:.3f}", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)],
                   check=True)
    return out


def voice_preview(case: str, res: dict, keeps: list[tuple[float, float]], cfg: dict) -> Path | None:
    """Voz del crudo con los cortes del plan + musica a gain_db con los stops del director y ducking
    (sidechaincompress). Solo referencia auditiva: el mezclador real es audio.py."""
    src = MEDIA / "crudo_completo.mp4"
    if not src.exists() or not keeps:
        return None
    parts = "".join(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[k{i}];" for i, (a, b) in enumerate(keeps))
    cat = "".join(f"[k{i}]" for i in range(len(keeps))) + f"concat=n={len(keeps)}:v=0:a=1,aresample=48000[v];"
    mute = "".join(f",volume=0:enable='between(t,{float(s['at']):.3f},{float(s['at']) + float(s['dur']):.3f})'"
                   for s in cfg.get("stops") or [])
    g = float(cfg.get("gain_db", -17.0))
    fc = (parts + cat + f"[v]asplit=2[vm][vs];[1:a]volume={g:.1f}dB{mute}[m];"
          "[m][vs]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[md];"
          "[vm][md]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    out = OUT / f"preview_voz_{case}.mp4"
    subprocess.run([M.FFMPEG, "-v", "error", "-y", "-i", str(src), "-i", res["wav"], "-loop", "1", "-framerate", "5",
                    "-i", str(OUT / f"sheet_{case}.png"), "-filter_complex", fc, "-map", "2:v", "-map", "[a]",
                    "-vf", f"scale={W // 2}:-2", "-t", f"{res['duration']:.3f}", "-c:v", "libx264", "-preset",
                    "veryfast", "-crf", "32", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)],
                   check=True)
    return out


def user_file_128(path: Path) -> Path:
    if not path.exists():
        y, _ = M.synth_procedural(40.0, "energetico", None, None, None, 3)
        y, _ = M.normalize(y)
        M.write_audio(path, y)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    no_ai = "--no-ai" in sys.argv
    no_mp4 = "--no-mp4" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    ai = not no_ai and M.music_ready()
    cases: list[tuple[str, dict, float, str, str, list]] = []
    for style in ("dinamico", "elegante", "minimal", "energetico"):
        dc = director_case(style)
        if not dc:
            continue
        cfg, D, keeps = dc
        if ai:
            cases.append((f"director_{style}_ai", cfg, D, style, "cfg exacta del director (ACE-Step)", keeps))
        cases.append((f"director_{style}_proc", dict(cfg, source="procedural"), D, style,
                      "misma cfg con el generador procedural (fallback sin modelos)", keeps))
    D0 = cases[0][2] if cases else 31.5
    keeps0 = cases[0][5] if cases else []
    if ai:
        cases += [("ai_dinamico_sin_stops", {"source": "ai"}, D0, "dinamico", "sin drop_at: drop en el primer segundo", keeps0),
                  ("ai_largo_60s", {"source": "ai"}, 60.0, "dinamico", "ACE-Step, video de 60 s", []),
                  ("ai_corto_12s", {"source": "ai"}, 12.0, "dinamico", "ACE-Step, video de 12 s", [])]
    cases += [("proc_elegante_final_fade", {"source": "procedural", "ending": "fade", "fade_out": 2.5}, D0, "elegante",
               "procedural elegante con final fade 2.5 s", []),
              ("biblioteca_loop_60s", {"source": "library", "track": "uplift"}, 60.0, "dinamico",
               "pista de biblioteca (drone): sin rejilla inventada, loop con crossfade", []),
              ("archivo_128bpm_cfg110", {"source": "file", "file": str(user_file_128(OUT / "_user128.wav")), "bpm": 110},
               25.0, "dinamico", "archivo del usuario a 128 BPM con el bpm 110 del plan: tempo correcto", [])]
    if only:
        cases = [c for c in cases if c[0] in only]
    metrics, sheets = {}, []
    for case, cfg, dur, style, note, keeps in cases:
        t0 = time.time()
        res = M.get_music({"enabled": True, **cfg}, dur, style, 7)
        el = time.time() - t0
        if not res["wav"]:
            print(f"{case}: sin musica ({res.get('error')})")
            continue
        v = M.validate_music(res["wav"], bpm=res["bpm"] or None)
        v["elapsed_s"] = round(el, 2)
        v.update(source=res["source"], engine=res.get("engine"), ending=res["ending"], cached=res["cached"],
                 drop=res.get("drop"), drop_at=cfg.get("drop_at"), hook_end=res.get("hook_end"),
                 seams=res.get("seams"), bpm=res["bpm"], duration=dur, prompt=res.get("prompt"),
                 labels=[(s["label"], s["start"]) for s in res["sections"]])
        metrics[case] = v
        sp = sheet(case, res, v, cfg, note)
        sheets.append(sp)
        print(f"{case}: {res['source']}/{res.get('engine')} {dur:.2f}s LUFS {v['lufs']:.1f} arranque "
              f"{v['lu_first2'] - v['lufs']:+.1f} LU TP {v['true_peak_db']:.1f} bpm {res['bpm']:.1f} "
              f"drop {res.get('drop')} (pedido {cfg.get('drop_at')}) final {res['ending']['mode']} "
              f"({el:.1f}s{' cache' if res['cached'] else ''})")
        if not no_mp4 and (case.startswith("director_") or case in ("ai_dinamico_sin_stops", "biblioteca_loop_60s")):
            mp4(case, res, sp)
            if keeps:
                voice_preview(case, res, keeps, cfg)
    ims = [Image.open(p) for p in sheets]
    if ims:
        ov = Image.new("RGB", (W, sum(i.height for i in ims)), (0, 0, 0))
        yy = 0
        for i in ims:
            ov.paste(i, (0, yy))
            yy += i.height
        ov.save(OUT / "overview.png")
    for p in OUT.glob("_spec_*.png"):
        p.unlink()
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=1, default=float))
    print("listo:", OUT)


if __name__ == "__main__":
    main()
