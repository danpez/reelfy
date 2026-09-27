"""Demo del mezclador (motion/audio.py): "reel sonoro" de 20 s con la voz real del caso de prueba.

- voz: crudo_completo.mp4 de 0 a 20 s por la cadena de estudio (highpass, RNNoise, de-esser,
  compresor; SIN loudnorm: audio.mix normaliza la voz y el bus final) = "voz ya procesada"
- musica: pista generada por IA local (ACE-Step) de prueba, tonalidad declarada (keyscale)
- SFX colocados como lo haria el director (estilo dinamico): ganancias de director.SFX_GAIN,
  tape stop real de la musica antes del titulo principal (el record_stop sintetico queda
  reemplazado), impacto al volver, riser con `dur`, tecleo de la direccion y garabato con `dur`
  sincronizados al texto, SFX tonales transpuestos a la tonalidad de la musica
- los tiempos por palabra salen del fixture del plan v1 en spike/work/fixtures (ignorado por git):
  en este archivo no hay texto ni datos del cliente

Entradas (variables de entorno opcionales):
  REELFY_MEDIA       carpeta con crudo_completo.mp4 (default: TEST_MEDIA de tests/conftest.py)
  REELFY_DEMO_MUSIC  pista de musica (default: spike/work/fixtures/demo_music_ace.mp3)
  REELFY_DEMO_PLAN   plan v1 con tiempos por palabra (default: spike/work/fixtures/*_plan_v1.json)
  REELFY_DEMO_KEY    tonalidad de la pista (default: "A major", la que se pidio a ACE-Step)

Escribe en spike/work/demos/audio/:
  reel_sonoro.wav (+ stems/), reel_sonoro.png (espectrograma + niveles por pista + curva de la
  musica + eventos), reel_sonoro_detalle.png (tape stop, cama, SFX frente a la voz en bocina
  completa y en telefono, dur del tecleo), reel_sonoro.mp4 (cursor + audio), report.json

Uso: cd spike/scripts && ../.venv/bin/python ../tests/demos/demo_audio.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))

import numpy as np  # noqa: E402
import skia  # noqa: E402
from scipy import signal  # noqa: E402

import paths  # noqa: E402
from motion import audio as A  # noqa: E402
from motion import assets  # noqa: E402
from motion import ctx as C  # noqa: E402
from motion.director import SFX_GAIN  # noqa: E402  (solo lectura: ganancias reales del director)
import _audioviz as V  # noqa: E402

OUT = paths.SPIKE / "work" / "demos" / "audio"
FIXTURES = paths.SPIKE / "work" / "fixtures"
DUR = 20.0
SR = A.SR

STUDIO = (f"highpass=f=80,arnndn=m={paths.RNNOISE}:mix=0.85,deesser,"
          "acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=2,aresample=48000")

COLS = {"voice": "#7CC4FF", "music": "#FFB36B", "sfx": "#FF6FA5", "gain": "#FFE08A", "phone": "#9BE3B0"}


def _inputs() -> tuple[Path, Path, Path, str]:
    media = os.environ.get("REELFY_MEDIA")
    if not media:
        try:
            from conftest import TEST_MEDIA
            media = str(TEST_MEDIA)
        except Exception:
            media = ""
    crudo = Path(media) / "crudo_completo.mp4"
    music = Path(os.environ.get("REELFY_DEMO_MUSIC") or FIXTURES / "demo_music_ace.mp3")
    plan = os.environ.get("REELFY_DEMO_PLAN")
    plan = Path(plan) if plan else next(iter(sorted(FIXTURES.glob("*_plan_v1.json"))), FIXTURES / "plan_v1.json")
    missing = [str(p) for p in (crudo, music, plan) if not p.exists()]
    if missing:
        sys.exit("demo_audio: faltan entradas (ver docstring, variables REELFY_*): " + ", ".join(missing))
    return crudo, music, plan, os.environ.get("REELFY_DEMO_KEY", "A major")


def _g(name: str, extra: float = 0.0) -> float:
    return float(SFX_GAIN.get(name, -6)) + extra     # estilo dinamico: sfx_db 0


def plan_events(plan: Path, music: Path, key: str) -> tuple[list[dict], dict]:
    """Eventos y musica como los coloca el director, a partir de los tiempos por palabra."""
    ph = json.loads(plan.read_text())["phrases"]
    w = lambda i, j: ph[i][j]["start"]  # noqa: E731
    end = lambda i: ph[i][-1]["end"]  # noqa: E731
    title = w(4, 0)                         # titulo principal (drama)
    stop_at = title - 0.12
    back = end(4) + 0.6
    evs = [
        {"at": max(0.0, w(0, 0) - 0.05), "name": "whoosh_fast", "why": "entra el titulo del gancho"},
        {"at": w(1, 1) + 0.02, "name": "pop", "pan": -0.2, "why": "sticker sobre la palabra clave"},
        {"at": w(2, 1) + 0.03, "name": "sparkle", "why": "icono con destellos"},
        {"at": w(3, 1), "name": "swoosh_up", "why": "la pregunta sube a pantalla"},
        {"at": title - 0.1, "name": "record_stop", "why": "el director lo pide en el stop (lo reemplaza el tape stop)"},
        {"at": title, "name": "riser_short", "dur": 1.0, "why": "tension de 1 s hacia el golpe"},
        {"at": title, "name": "impact_boom", "why": "golpe del titulo principal"},
        {"at": w(5, 0), "name": "whoosh_soft", "why": "transicion al beneficio"},
        {"at": w(6, 1) + 0.05, "name": "chime", "why": "destello del beneficio"},
        {"at": w(9, 0) + 0.03, "name": "pen_scribble", "dur": 0.55, "why": "subrayado que se dibuja en 0.55 s"},
        {"at": w(9, 1), "name": "ding", "why": "antes/despues"},
        {"at": w(10, 0) - 0.05, "name": "whip", "why": "whip pan a la tarjeta de precio"},
        {"at": w(11, 1) + 0.12, "name": "cash", "why": "precio"},
        {"at": w(12, 2) + 0.02, "name": "pop", "pan": 0.2, "why": "pin de ubicacion"},
        {"at": w(12, 0) + 0.05, "name": "typing", "dur": round(DUR - 0.25 - (w(12, 0) + 0.05), 3),
         "why": "la direccion se escribe (dur = tiempo de revelado, mas largo que el archivo: loop)"},
    ]
    for i, e in enumerate(evs):
        e["id"] = f"sf{i}"
        e["gain_db"] = _g(e["name"])
    evs.sort(key=lambda e: e["at"])
    mcfg = {"enabled": True, "wav": str(music), "keyscale": key, "gain_db": -17, "duck_db": -9,
            "fade_in": 0.2, "fade_out": 1.5,
            "stops": [{"at": round(stop_at, 3), "dur": round(back - title, 3)}],
            "swells": [{"start": end(9) + 0.1, "end": w(10, 0) - 0.15, "db": 3}]}
    return evs, mcfg


def extract_voice(crudo: Path, dst: Path) -> None:
    if dst.exists():
        return
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-ss", "0", "-t", str(DUR), "-i", str(crudo),
                    "-vn", "-af", STUDIO, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s24le", str(dst)], check=True)


# ---------------------------------------------------------------- medidas
def level_db(x: np.ndarray, win: float = 0.05) -> np.ndarray:
    m = (x ** 2).mean(axis=1)
    w = int(win * SR)
    k = np.convolve(m, np.ones(w) / w, mode="same")[::480]  # 10 ms
    return 10 * np.log10(np.maximum(k, 1e-12))


def momentary(x: np.ndarray) -> np.ndarray:
    """Loudness momentanea (400 ms) cada 100 ms."""
    sub = A._kw_sub(x)
    return A._lk(A._win_power(sub, 4))


_PHONE = signal.butter(4, 300, "highpass", fs=SR, output="sos")


def l100_at(x: np.ndarray, a: float, b: float) -> float:
    s = x[max(0, int(a * SR)):max(int(a * SR) + 1, int(b * SR))]
    return A.peak_window_lufs(s, 0.1) if s.shape[0] > 480 else -99.0


def measure(stems: dict, info: dict, mcfg: dict) -> dict:
    voice, music, sfx = stems["voice"], stems["music"], stems["sfx"]
    n = voice.shape[0]
    # cama: loudness momentanea de la musica con voz y en huecos (actividad de la musica)
    am = A.music_activity(voice)
    mm, vm = momentary(music), momentary(voice)
    tb = np.arange(mm.size) * 0.1 + 0.2                # centro de cada bloque
    a_b = np.interp(tb, np.arange(am.size) / A.CTRL_HZ, am)
    ok = (tb > 0.6) & (tb < DUR - 1.8)
    for st in mcfg["stops"]:
        ok &= ~((tb > st["at"] - 0.7) & (tb < st["at"] + st["dur"] + 0.3))
    for sw in mcfg["swells"]:
        ok &= ~((tb > sw["start"] - 0.4) & (tb < sw["end"] + 0.4))
    on, off = ok & (a_b > 0.9), ok & (a_b < 0.1)
    bed = {"music_momentary_with_voice": round(float(np.median(mm[on])), 1),
           "voice_momentary": round(float(np.median(vm[on])), 1),
           "music_momentary_in_gaps": round(float(np.median(mm[off])), 1) if off.any() else None,
           "bed_below_voice_lu": round(float(np.median(vm[on]) - np.median(mm[on])), 1)}
    # aperturas de la cama (curva de ganancia): cuantas veces sube > 4 dB
    g, _ = A.music_gain_curve(DUR, mcfg, am, 0.0, 0.0)
    gdb = 20 * np.log10(np.maximum(g, 1e-9))
    t = np.arange(gdb.size) / A.CTRL_HZ
    sel = (t > 0.5) & (t < DUR - 1.8)
    for st in mcfg["stops"]:
        sel &= ~((t > st["at"] - 0.1) & (t < st["at"] + st["dur"] + 0.2))
    for sw in mcfg["swells"]:
        sel &= ~((t > sw["start"] - 0.4) & (t < sw["end"] + 0.4))
    base = np.percentile(gdb[sel], 10)
    up = (gdb > base + 4.0) & sel
    bed["openings_over_4db"] = int(np.sum(np.diff(up.astype(int)) == 1))
    # SFX frente a la voz: L100 del SFX alrededor de su hit contra el pico L100 de la voz (+-0.5 s)
    vph = signal.sosfilt(_PHONE, voice, axis=0)
    sph = signal.sosfilt(_PHONE, sfx, axis=0)
    rows = []
    for e in info["sfx"]:
        at = e["at"]
        a, b = (at - 0.15, at + 0.35) if e["name"] not in ("riser_short", "riser_long") else (at - 0.5, at + 0.05)
        ls = l100_at(sfx, a, b)
        lv = l100_at(voice, at - 0.5, at + 0.5)
        rows.append({"name": e["name"], "at": at, "gain_db": e["gain_db"], "sfx_l100": round(ls, 1),
                     "voice_l100": round(lv, 1), "lu_vs_voice": round(ls - lv, 1),
                     "phone_lu_vs_voice": round(l100_at(sph, a, b) - l100_at(vph, at - 0.5, at + 0.5), 1),
                     "transpose": e.get("transpose", 0), "dur": e.get("dur")})
    # tape stop: centroide de la musica antes y durante el frenado
    st = mcfg["stops"][0]
    c = lambda a, b: V_centroid(music[int(a * SR):int(b * SR)])  # noqa: E731
    tape = {"at": st["at"], "centroid_before_hz": round(c(st["at"] - 0.75, st["at"] - 0.5)),
            "centroid_braking_hz": round(c(st["at"] - 0.2, st["at"] - 0.05)),
            "silence_db": round(10 * np.log10((music[int((st["at"] + 0.05) * SR):int((st["at"] + st["dur"] - 0.05) * SR)] ** 2).mean() + 1e-20), 1)}
    return {"bed": bed, "sfx_levels": rows, "tape_stop": tape, "n": n}


def V_centroid(x: np.ndarray) -> float:
    m = x.mean(axis=1)
    S = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return float((S * f).sum() / (S.sum() + 1e-20))


# ---------------------------------------------------------------- dibujo
def _line(cv, xs, ys, col, width=2.0, alpha=0.95):
    path = skia.Path()
    for i, (x, y) in enumerate(zip(xs, ys)):
        (path.moveTo if i == 0 else path.lineTo)(x, y)
    cv.drawPath(path, skia.Paint(AntiAlias=True, Color=C.color(col, alpha), Style=skia.Paint.kStroke_Style,
                                 StrokeWidth=width, StrokeJoin=skia.Paint.kRound_Join))


def _panel(cv, x, y, w, h, title=None):
    cv.drawRoundRect(skia.Rect.MakeXYWH(x, y, w, h), 12, 12, skia.Paint(AntiAlias=True, Color=C.color(V.PANEL)))
    if title:
        V.text(cv, title, x + 18, y + 30, 19, V.TEXT, "Inter-Bold")


def draw_report(mix, stems, info, path, mcfg, meas):
    W, H = 2000, 1220
    arr, surf = C.new_frame(W, H)
    cv = surf.getCanvas()
    cv.clear(C.color(V.BG))
    pad = 60
    V.text(cv, f"Reel sonoro de prueba: voz real + musica IA + {len(info['sfx'])} SFX (ganancias del director)",
           pad, 62, 32, V.TEXT, "Inter-Bold")
    sub = (f"{info['lufs']:.2f} LUFS   TP {info['true_peak_dbtp']:.2f} dBTP   LRA {info['lra']:.1f} LU   "
           f"limitador {info['limiter_max_gr_db']:.1f} dB   bus SFX {info['sfx_bus_gr_db']:.1f} dB   "
           f"cama {meas['bed']['bed_below_voice_lu']:.1f} LU bajo la voz   mezcla {info['ms'] / 1000:.2f} s")
    V.text(cv, sub, pad, 100, 19, V.MUTED, "SpaceMono-Bold")
    x0, y0, w, h = pad, 160, W - 2 * pad, 400
    V.draw_image(cv, V.spectrogram_rgba(mix, w, h, db_range=85, nfft=2048), x0, y0)
    for fq, lab in ((100, "100 Hz"), (1000, "1 kHz"), (10000, "10 kHz")):
        yy = y0 + h * (1 - (np.log(fq / 30.0) / np.log(20000 / 30.0)))
        V.text(cv, lab, x0 + 10, yy + 5, 14, "#FFFFFF", "SpaceMono-Bold")
    ly, lh = y0 + h + 80, 380
    _panel(cv, x0, ly, w, lh)
    lo, hi = -60.0, 0.0
    for d in range(-60, 1, 10):
        yy = ly + lh * (1 - (d - lo) / (hi - lo))
        cv.drawLine(x0, yy, x0 + w, yy, skia.Paint(Color=C.color(V.GRID), StrokeWidth=1))
        V.text(cv, f"{d} dB", x0 - 8, yy + 5, 13, V.MUTED, "SpaceMono-Bold", align="right")
    ymap = lambda v: ly + lh * (1 - (np.clip(v, lo, hi) - lo) / (hi - lo))  # noqa: E731
    for k in ("music", "sfx", "voice"):
        L = level_db(stems[k])
        xs = x0 + w * np.arange(len(L)) / (len(L) - 1)
        _line(cv, xs, ymap(L), COLS[k], 1.6 if k == "voice" else 2.2)
    am = A.music_activity(stems["voice"])
    g, _ = A.music_gain_curve(DUR, mcfg, am, 0.0, voice_ref=0.0)
    gdb = 20 * np.log10(np.maximum(g, 1e-6)) + 1.0
    idx = np.arange(0, len(gdb), 5)
    _line(cv, x0 + w * idx / (len(gdb) - 1), ymap(gdb[idx]), COLS["gain"], 2.6)
    xx = x0
    for k, lab in (("voice", "voz"), ("music", "musica"), ("sfx", "sfx"),
                   ("gain", "ganancia de banda ancha de la musica (ducking con hold 0.6 s, tape stop, swell)")):
        yy = ly + lh + 66
        cv.drawRect(skia.Rect.MakeXYWH(xx, yy - 6, 22, 5), skia.Paint(Color=C.color(COLS[k])))
        V.text(cv, lab, xx + 32, yy, 18, V.TEXT, "Inter-SemiBold")
        xx += 60 + assets.font("Inter-SemiBold", 18).measureText(lab)
    for i, e in enumerate(info["sfx"]):
        xe = x0 + w * e["at"] / DUR
        p = skia.Paint(AntiAlias=True, Color=C.color("#FFFFFF", 0.5), StrokeWidth=1.5)
        p.setPathEffect(skia.DashPathEffect.Make([5, 5], 0))
        cv.drawLine(xe, y0, xe, ly + lh, p)
        V.text(cv, e["name"], xe + 4, y0 - 12 - 20 * (i % 2), 14, "#FFD23F", "Inter-SemiBold")
    for s in mcfg["stops"]:
        a = x0 + w * (s["at"] - 0.5) / DUR
        b = x0 + w * (s["at"] + s["dur"]) / DUR
        cv.drawRect(skia.Rect.MakeLTRB(a, ly, b, ly + lh), skia.Paint(Color=C.color("#FF4D6D", 0.12)))
        V.text(cv, "tape stop", a + 4, ly + lh - 10, 14, "#FF4D6D", "Inter-Bold")
    t = 0.0
    while t <= DUR + 1e-6:
        V.text(cv, f"{t:.0f}s", x0 + w * t / DUR, ly + lh + 28, 14, V.MUTED, "SpaceMono-Bold", align="center")
        t += 1.0
    V.save_png(arr.copy(), path)


def draw_detail(stems, info, mcfg, meas, path):
    """Cuatro paneles para confirmar cada arreglo: tape stop, cama, SFX vs voz (bocina y
    telefono) y dur del tecleo/garabato."""
    W, H = 2000, 1300
    arr, surf = C.new_frame(W, H)
    cv = surf.getCanvas()
    cv.clear(C.color(V.BG))
    V.text(cv, "Detalle de la mezcla: tape stop, cama, SFX frente a la voz y duracion sincronizada",
           60, 60, 30, V.TEXT, "Inter-Bold")
    music, sfx, voice = stems["music"], stems["sfx"], stems["voice"]
    # 1) tape stop: espectrograma de la musica sola
    st = mcfg["stops"][0]
    a, b = st["at"] - 1.2, st["at"] + st["dur"] + 0.6
    px, py, pw, ph = 60, 100, 920, 540
    _panel(cv, px, py, pw, ph, "1. Tape stop de la propia pista (musica sola)")
    seg = music[int(a * SR):int(b * SR)]
    sp = V.spectrogram_rgba(seg, pw - 40, ph - 110, fmin=40, db_range=80, nfft=4096)
    V.draw_image(cv, sp, px + 20, py + 50)
    for tt, lab, col in ((st["at"] - 0.5, "frena", "#FFE08A"), (st["at"], "silencio", "#FF4D6D"),
                         (st["at"] + st["dur"], "regresa", "#9BE3B0")):
        xx = px + 20 + (pw - 40) * (tt - a) / (b - a)
        p = skia.Paint(AntiAlias=True, Color=C.color(col, 0.9), StrokeWidth=2)
        p.setPathEffect(skia.DashPathEffect.Make([6, 5], 0))
        cv.drawLine(xx, py + 50, xx, py + ph - 60, p)
        V.text(cv, lab, xx + 5, py + 70, 15, col, "Inter-Bold")
    tp = meas["tape_stop"]
    V.text(cv, f"centroide {tp['centroid_before_hz']} Hz -> {tp['centroid_braking_hz']} Hz al frenar   "
               f"silencio {tp['silence_db']} dB", px + 20, py + ph - 22, 15, V.MUTED, "SpaceMono-Bold")
    # 2) cama: nivel de musica y voz en los primeros 14 s
    px2 = 1020
    _panel(cv, px2, py, pw, ph, "2. Cama musical: baja con la voz, solo sube en pausas reales")
    T = 14.0
    lo, hi = -60.0, -5.0
    gx, gy, gw, gh = px2 + 60, py + 60, pw - 90, ph - 175
    ymap = lambda v: gy + gh * (1 - (np.clip(v, lo, hi) - lo) / (hi - lo))  # noqa: E731
    for d in range(-60, -4, 10):
        cv.drawLine(gx, ymap(d), gx + gw, ymap(d), skia.Paint(Color=C.color(V.GRID), StrokeWidth=1))
        V.text(cv, f"{d}", gx - 8, ymap(d) + 5, 13, V.MUTED, "SpaceMono-Bold", align="right")
    for k, win in (("voice", 0.05), ("music", 0.2)):
        L = level_db(stems[k][:int(T * SR)], win)
        _line(cv, gx + gw * np.arange(len(L)) / (len(L) - 1), ymap(L), COLS[k], 1.4 if k == "voice" else 2.6)
    am = A.music_activity(voice)[:int(T * A.CTRL_HZ)]
    act_y = gy + gh + 48 - 26 * am
    _line(cv, gx + gw * np.arange(len(am)) / (len(am) - 1), act_y, COLS["gain"], 2.0)
    V.text(cv, "actividad de la musica (hold 0.6 s)", gx - 50, gy + gh + 20, 13, COLS["gain"], "Inter-SemiBold")
    bed = meas["bed"]
    V.text(cv, f"cama con voz {bed['music_momentary_with_voice']} LUFS (voz {bed['voice_momentary']})   "
               f"en pausas {bed['music_momentary_in_gaps']}   aperturas > 4 dB: {bed['openings_over_4db']}",
           px2 + 20, py + ph - 22, 15, V.MUTED, "SpaceMono-Bold")
    # 3) SFX vs voz: barras (bocina completa y telefono)
    py3 = py + ph + 30
    ph3 = H - py3 - 30
    _panel(cv, 60, py3, pw, ph3, "3. Cada SFX frente a la voz (LU, L100): bocina y telefono (HPF 300 Hz)")
    rows = [r for r in meas["sfx_levels"]]
    bx, by, bw = 60 + 240, py3 + 60, pw - 300
    rh = (ph3 - 110) / max(1, len(rows))
    lo, hi = -24.0, 6.0
    xm = lambda v: bx + bw * (np.clip(v, lo, hi) - lo) / (hi - lo)  # noqa: E731
    for d in range(-24, 7, 6):
        cv.drawLine(xm(d), by - 6, xm(d), by + rh * len(rows), skia.Paint(Color=C.color(V.GRID, 0.9), StrokeWidth=1))
        V.text(cv, f"{d:+d}", xm(d), by + rh * len(rows) + 22, 13, V.MUTED, "SpaceMono-Bold", align="center")
    cv.drawLine(xm(0), by - 6, xm(0), by + rh * len(rows), skia.Paint(Color=C.color("#FFFFFF", 0.6), StrokeWidth=1.5))
    for i, r in enumerate(rows):
        yy = by + i * rh
        V.text(cv, f"{r['name']}  {r['gain_db']:+.0f} dB", bx - 14, yy + rh * 0.62, 14, V.TEXT, "Inter-SemiBold",
               align="right")
        for k, (val, col) in enumerate(((r["lu_vs_voice"], COLS["sfx"]), (r["phone_lu_vs_voice"], COLS["phone"]))):
            y1 = yy + 4 + k * (rh - 8) / 2
            cv.drawRoundRect(skia.Rect.MakeLTRB(xm(lo), y1, xm(val), y1 + (rh - 8) / 2 - 2), 3, 3,
                             skia.Paint(AntiAlias=True, Color=C.color(col, 0.85)))
    V.text(cv, "rosa: bocina completa   verde: telefono   0 = pico de la voz", 60 + 20, py3 + ph3 - 14, 14,
           V.MUTED, "Inter-SemiBold")
    # 4) dur del tecleo y del garabato
    _panel(cv, px2, py3, pw, ph3, "4. dur: el sonido dura lo que dura el revelado en pantalla")
    evs = [e for e in info["sfx"] if e.get("dur_mode")]
    k = 0
    slot = (ph3 - 70) / max(1, len(evs))
    for e in evs:
        a = e["at"] - 0.3 if e["name"] != "riser_short" else e["at"] - e["dur"] - 0.3
        b = e["at"] + e["dur"] + 0.4 if e["name"] != "riser_short" else e["at"] + 0.6
        seg = sfx[int(a * SR):int(b * SR)]
        rx, ry, rw, rh2 = px2 + 20, py3 + 50 + k * slot, pw - 40, slot - 34
        V.draw_wave(cv, seg, (rx, ry, rw, rh2), b - a, scale=0.95 / (np.abs(seg).max() + 1e-9))
        s0 = e["at"] if e["name"] != "riser_short" else e["at"] - e["dur"]
        s1 = e["at"] + e["dur"] if e["name"] != "riser_short" else e["at"]
        for tt, col in ((s0, "#9BE3B0"), (s1, "#FF4D6D")):
            xx = rx + rw * (tt - a) / (b - a)
            p = skia.Paint(AntiAlias=True, Color=C.color(col, 0.95), StrokeWidth=2)
            p.setPathEffect(skia.DashPathEffect.Make([6, 5], 0))
            cv.drawLine(xx, ry, xx, ry + rh2, p)
        V.text(cv, f"{e['name']}  dur {e['dur']:.2f} s ({e['dur_mode']})", rx, ry + rh2 + 22, 15, V.TEXT,
               "Inter-SemiBold")
        k += 1
    V.save_png(arr.copy(), path)


def main() -> dict:
    crudo, music, plan, key = _inputs()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in ("voice_studio20.wav",):          # voz con loudnorm de una version anterior
        (OUT / old).unlink(missing_ok=True)
    voice = OUT / "voice_studio20_v2.wav"
    extract_voice(crudo, voice)
    out = OUT / "reel_sonoro.wav"
    events, mcfg = plan_events(plan, music, key)
    t0 = time.perf_counter()
    info = A.mix(voice, DUR, events, mcfg, out, seed=7, stems_dir=OUT / "stems")
    wall = time.perf_counter() - t0
    mix = A.load_audio(out)
    stems = {k: A.load_audio(v) for k, v in info["stems"].items()}
    ff = A.ffmpeg_ebur128(out)
    meas = measure(stems, info, mcfg)
    png = OUT / "reel_sonoro.png"
    draw_report(mix, stems, info, png, mcfg, meas)
    png2 = OUT / "reel_sonoro_detalle.png"
    draw_detail(stems, info, mcfg, meas, png2)
    mp4 = OUT / "reel_sonoro.mp4"
    x0, w = 60, 2000 - 120
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(png),
                    "-i", str(out), "-filter_complex",
                    f"[0:v]scale=1280:-2,drawbox=x='({x0}+{w}*t/{DUR})*1280/2000':y=100:w=3:h=ih-170:color=white@0.9:t=fill,format=yuv420p[v]",
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "26", "-tune", "stillimage",
                    "-c:a", "aac", "-b:a", "192k", "-t", str(DUR), str(mp4)], check=True)
    rep = {k: info.get(k) for k in ("lufs", "true_peak_dbtp", "lra", "momentary_max", "gain_db", "limiter_max_gr_db",
                                    "sfx_bus_gr_db", "lufs_short_by", "voice_lufs_in", "samples", "ms")}
    rep.update({"ffmpeg_ebur128": ff, "music": info.get("music"), "wall_s": round(wall, 2),
                "bed": meas["bed"], "tape_stop": meas["tape_stop"], "sfx_levels": meas["sfx_levels"],
                "sfx": info["sfx"], "skipped": info["sfx_skipped"],
                "png": str(png), "png_detail": str(png2), "mp4": str(mp4), "wav": str(out)})
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in rep.items() if k not in ("sfx", "sfx_levels")}, indent=1, ensure_ascii=False))
    for r in meas["sfx_levels"]:
        print(f"  {r['name']:13s} {r['at']:6.2f}s gain {r['gain_db']:+4.0f}  {r['lu_vs_voice']:+5.1f} LU vs voz"
              f"  (telefono {r['phone_lu_vs_voice']:+5.1f})  tr {r['transpose']:+d}")
    return rep


if __name__ == "__main__":
    main()
