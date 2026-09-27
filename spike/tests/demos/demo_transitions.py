"""Demo visual de motion/transitions.py con cuadros reales del video de prueba.

Para cada transicion (y variantes) genera en spike/work/demos/transitions/:
  strip_<nombre>.png   tira de 10 cuadros de la ventana (p y tiempo relativo a la union)
  <nombre>.mp4         clip corto: A en vivo -> transicion -> B en vivo (540x960, 30 fps)
  overview.jpg         todas las tiras juntas (mitad de tamano)
  timings.json         ms por llamada (final 1080x1920 y draft 540x960), saltos y cuadros repetidos

Reproduce la ventana tal como la arma render.py: union en el cuadro J, L = round(dur*fps)
cuadros, F0 = J - round(L * effective_frac), p = (n - F0 + 0.5) / L, prev = cuadro J-1 congelado
(solo en las mezclas; en las centradas prev es el propio cuadro). El rostro por cuadro sale de
camera.face_track (YuNet) sobre la misma secuencia.

Variantes extra: los colores que hoy manda director.py ('#FF7A3D', '#E8C27A', '#FFFFFF') para
verificar que no degradan el look, y light_leak con barra de pelicula y pico parcial.

Uso (desde spike/):  .venv/bin/python tests/demos/demo_transitions.py [nombre ...]
A = consultorio (crudo ~11.6 s), B = calle (crudo ~19.6 s). Los renders viven en work/ (ignorado).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SPIKE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SPIKE / "scripts"))

import paths  # noqa: E402
from motion import assets, ctx as C, transitions as T  # noqa: E402

MEDIA = Path(__import__("os").environ.get("REELFY_TEST_MEDIA") or Path(__file__).resolve().parents[2] / "work" / "media")
SRC = MEDIA / "crudo_completo.mp4"
OUT = SPIKE / "work" / "demos" / "transitions"
FPS = 30
W, H = 1080, 1920
A_T, B_T = 11.6, 19.6
PRE, POST = 12, 12

VARIANTS: dict[str, dict] = {
    "flash": {}, "flash_warm": {"_kind": "flash", "color": "warm"},
    "light_leak": {}, "light_leak_gate": {"_kind": "light_leak", "gate": True},
    "light_leak_partial": {"_kind": "light_leak", "peak": "partial"},
    "light_leak_gold": {"_kind": "light_leak", "color": "gold"},
    "light_leak_dir_FF7A3D": {"_kind": "light_leak", "color": "#FF7A3D"},
    "light_leak_dir_E8C27A": {"_kind": "light_leak", "color": "#E8C27A"},
    "whip": {}, "whip_up": {"_kind": "whip", "dir": "up"},
    "zoom_blur": {}, "glitch": {}, "glitch_dir_FFFFFF": {"_kind": "glitch", "color": "#FFFFFF"},
    "film_burn": {}, "film_burn_dir_FFFFFF": {"_kind": "film_burn", "color": "#FFFFFF"},
    "torn_paper": {}, "torn_paper_up": {"_kind": "torn_paper", "dir": "up"},
    "torn_paper_sweep": {"_kind": "torn_paper", "mode": "sweep", "dir": "left"},
    "circle": {}, "circle_dir_FFFFFF": {"_kind": "circle", "color": "#FFFFFF"},
    "circle_ring": {"_kind": "circle", "ring": True, "color": "#FFE4BC"},
    "slide": {},
}


def decode(t0: float, n: int, w: int = W, h: int = H) -> list[np.ndarray]:
    cmd = [paths.FFMPEG, "-v", "error", "-ss", f"{t0:.3f}", "-i", str(SRC), "-frames:v", str(n),
           "-vf", f"fps={FPS},scale={w}:{h}:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 4)
    return [np.ascontiguousarray(f) for f in fr]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(assets.FONTS_DIR / "Poppins-SemiBold.ttf"), size)


def item_for(kind: str, **kw) -> dict:
    it = {"id": f"demo_{kind}", "kind": kind, "at": 0.0}
    it.update(T.DEFAULTS.get(kind, {}))
    it = {k: v for k, v in it.items() if v is not None}
    it.update(kw)
    return it


def faces_for(seq: list[np.ndarray], J: int) -> np.ndarray:
    """Rostro por cuadro (YuNet via camera.face_track) de la secuencia A|B; NaN si no hay."""
    try:
        from motion import camera, video_io as V
    except Exception as e:  # noqa
        print("sin camera.face_track:", e)
        return np.full((len(seq), 4), np.nan)
    tmp = OUT / "_faces_tmp.mp4"
    try:
        w = V.ChunkWriter(tmp, W, H, FPS, "inter")
        for f in seq:
            w.write(f)
        w.close()
        return np.asarray(camera.face_track(str(tmp), W, H, FPS, len(seq), cuts=[J]), np.float64)
    except Exception as e:  # noqa
        print("face_track fallo:", e)
        return np.full((len(seq), 4), np.nan)
    finally:
        tmp.unlink(missing_ok=True)


def run_kind(kind: str, A: list, B: list, Ad: list, Bd: list, faces: np.ndarray,
             item_kw: dict | None = None) -> dict:
    item = item_for(kind, **(item_kw or {}))
    L = max(1, int(round(float(item.get("dur", 0.4)) * FPS)))
    frac = T.effective_frac(item)
    J = PRE + int(round(L * frac))
    F0 = J - int(round(L * frac))
    F1 = F0 + L
    total = F1 + POST
    seq = [A[i] if i < J else B[i - J] for i in range(total)]
    seqd = [Ad[i] if i < J else Bd[i - J] for i in range(total)]
    mixer = kind in T.MIXERS
    prev_f = C.image_from_rgba(seq[J - 1])
    prev_d = C.image_from_rgba(seqd[J - 1])
    frames_out, ms_f, ms_d, ps = [], [], [], []
    for n in range(total):
        img = C.image_from_rgba(seq[n])
        if F0 <= n < F1:
            p = (n - F0 + 0.5) / L
            fc = faces[n] if n < len(faces) and not np.isnan(faces[n]).any() else None
            ctx_f = C.demo_ctx(W, H, fps=FPS, seed=7, quality="final", face=None if fc is None else tuple(fc))
            ctx_f.n = n
            ctx_d = C.demo_ctx(W // 2, H // 2, fps=FPS, seed=7, quality="draft",
                               face=None if fc is None else tuple(fc))
            ctx_d.n = n
            t0 = time.perf_counter()
            out = T.apply(img, prev_f if (mixer and n >= J) else img, item, p, ctx_f)
            ms_f.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            imgd = C.image_from_rgba(seqd[n])
            T.apply(imgd, prev_d if (mixer and n >= J) else imgd, item, p, ctx_d)
            ms_d.append((time.perf_counter() - t0) * 1000)
            ps.append((n, p))
        else:
            out = img
        frames_out.append(C.image_to_rgba(out))

    def mad(i):
        return float(np.abs(frames_out[i].astype(np.int16) - frames_out[i - 1].astype(np.int16))[..., :3].mean())
    def mad_src(i):
        return float(np.abs(seq[i].astype(np.int16) - seq[i - 1].astype(np.int16))[..., :3].mean())
    diffs = [mad(i) for i in range(F0, min(total, F1 + 1))]
    live = [mad(i) for i in range(1, PRE)]
    # cuadros practicamente repetidos dentro de la ventana: la salida no cambia aunque el video
    # en vivo si (A congelado sin movimiento visible)
    dups = int(sum(1 for i in range(F0, min(total, F1 + 1))
                   if mad(i) < 0.6 and mad_src(i) > 1.5))
    jumps = {"entry": diffs[0], "exit": diffs[-1], "live_typ": float(np.median(live)),
             "max_in_window": float(max(diffs)), "dup_frames": dups}
    # tira: 10 cuadros repartidos en la ventana
    idx = np.unique(np.round(np.linspace(0, len(ps) - 1, min(10, len(ps)))).astype(int))
    strip = [(ps[i][1], (ps[i][0] - J) / FPS, frames_out[ps[i][0]]) for i in idx]
    return {"item": item, "frames": frames_out, "strip": strip, "jumps": jumps,
            "ms_final": float(np.mean(ms_f)), "ms_final_max": float(np.max(ms_f)),
            "ms_draft": float(np.mean(ms_d)), "ms_draft_max": float(np.max(ms_d))}


def save_strip(name: str, res: dict) -> Path:
    tw, th = 216, 384
    head = 54
    n = len(res["strip"])
    im = Image.new("RGB", (tw * n + 8 * (n + 1), th + head + 8), (18, 18, 22))
    d = ImageDraw.Draw(im)
    it = res["item"]
    extra = "  ".join(f"{k} {v}" for k, v in it.items() if k not in ("id", "kind", "at", "dur"))
    title = f"{name}   dur {it.get('dur')}s   {extra}   final {res['ms_final']:.1f} ms " \
            f"(max {res['ms_final_max']:.1f})   draft {res['ms_draft']:.1f} ms   " \
            f"repetidos {res['jumps']['dup_frames']}"
    d.text((10, 10), title, fill=(240, 240, 240), font=font(18))
    f12 = font(13)
    for i, (p, t, arr) in enumerate(res["strip"]):
        th_im = Image.fromarray(arr[..., :3]).resize((tw, th), Image.LANCZOS)
        x = 8 + i * (tw + 8)
        im.paste(th_im, (x, head))
        d.rectangle([x, head, x + 118, head + 20], fill=(0, 0, 0))
        d.text((x + 4, head + 2), f"p={p:.2f}  {t:+.2f}s", fill=(255, 214, 64), font=f12)
    path = OUT / f"strip_{name}.png"
    im.save(path)
    return path


def save_mp4(name: str, frames: list) -> Path:
    path = OUT / f"{name}.mp4"
    cmd = [paths.FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-vf", "scale=540:960:flags=lanczos", "-c:v", "libx264",
           "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        proc.stdin.write(f.tobytes())
    proc.stdin.close()
    proc.wait()
    return path


def main(names: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    need = PRE + int(1.6 * FPS) + POST + 4
    A = decode(A_T, need)
    B = decode(B_T, need)
    Ad = [np.ascontiguousarray(np.array(Image.fromarray(a).resize((W // 2, H // 2), Image.BILINEAR))) for a in A]
    Bd = [np.ascontiguousarray(np.array(Image.fromarray(b).resize((W // 2, H // 2), Image.BILINEAR))) for b in B]
    # rostro por cuadro para las dos disposiciones de ventana (centrada y mezcla)
    face_cache: dict[int, np.ndarray] = {}
    if not names or names == ["all"]:
        names = list(VARIANTS)
    timings = {}
    tpath = OUT / "timings.json"
    if tpath.exists():
        try:
            timings = json.loads(tpath.read_text())
        except Exception:
            timings = {}
    for name in names:
        kw = dict(VARIANTS.get(name, {}))
        kind = kw.pop("_kind", name)
        if kind not in T.RENDERERS:
            print("kind desconocido:", name)
            continue
        item = item_for(kind, **kw)
        L = max(1, int(round(float(item.get("dur", 0.4)) * FPS)))
        J = PRE + int(round(L * T.effective_frac(item)))
        if J not in face_cache:
            seq = [A[i] if i < J else B[i - J] for i in range(min(len(A), J + len(B)))]
            face_cache[J] = faces_for(seq, J)
        res = run_kind(kind, A, B, Ad, Bd, face_cache[J], kw)
        sp = save_strip(name, res)
        mp = save_mp4(name, res["frames"])
        timings[name] = {k: round(res[k], 2) for k in ("ms_final", "ms_final_max", "ms_draft", "ms_draft_max")}
        timings[name]["jumps"] = {k: round(v, 2) for k, v in res["jumps"].items()}
        j = res["jumps"]
        print(f"{name:24s} final {res['ms_final']:6.1f} ms (max {res['ms_final_max']:6.1f})  "
              f"draft {res['ms_draft']:5.1f} ms  entrada {j['entry']:.1f} salida {j['exit']:.1f} "
              f"max {j['max_in_window']:.1f} (tipico {j['live_typ']:.1f}) repetidos {j['dup_frames']}"
              f"  -> {sp.name}, {mp.name}")
    tpath.write_text(json.dumps(timings, indent=2))
    all_strips = sorted(OUT.glob("strip_*.png"))
    ims = [Image.open(p) for p in all_strips]
    if ims:
        wmax = max(i.width for i in ims)
        ov = Image.new("RGB", (wmax, sum(i.height for i in ims)), (10, 10, 12))
        y = 0
        for i in ims:
            ov.paste(i, (0, y))
            y += i.height
        ov = ov.resize((ov.width // 2, ov.height // 2), Image.LANCZOS)
        ov.save(OUT / "overview.jpg", quality=88)


if __name__ == "__main__":
    main(sys.argv[1:] or ["all"])
