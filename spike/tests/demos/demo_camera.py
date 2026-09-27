"""Demo visual de camera.py: seguimiento de rostro, zoom/punch/shake/kenburns con motion blur,
rgb_split y flash sobre cuadros reales del caso de prueba.

Uso: cd spike && .venv/bin/python tests/demos/demo_camera.py [--mp4]
Salida: spike/work/demos/camera/*.png (+ camera_demo.mp4 con --mp4)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _real_frames as RF  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import paths  # noqa: E402
from motion import camera, ctx as C, grade  # noqa: E402

OUT = RF.SPIKE / "work" / "demos" / "camera"
FPS = 30
N_FRAMES = int(37.7 * FPS)


def face_of(ft, n):
    f = ft[min(n, len(ft) - 1)]
    return tuple(float(v) for v in f) if np.all(np.isfinite(f)) else None


def plot_track(ft, path, cuts=()):
    """Grafica cx, cy, w de la pista de rostro a lo largo del video: limites de toma medidos
    (gris) y cortes detectados por face_track (amarillo)."""
    Wp, Hp = 1400, 420
    im = Image.new("RGB", (Wp, Hp), (18, 18, 20))
    d = ImageDraw.Draw(im)
    fnt = ImageFont.truetype(str(RF.SPIKE / "assets/motion/fonts/Poppins-SemiBold.ttf"), 16)
    x0, x1, y0, y1 = 50, Wp - 20, 30, Hp - 40
    n = len(ft)
    for a, b in RF.SHOTS:
        xa = x0 + (x1 - x0) * a * FPS / n
        d.line([(xa, y0), (xa, y1)], fill=(60, 60, 70), width=5)
    for c in cuts:
        xa = x0 + (x1 - x0) * c / n
        d.line([(xa, y0), (xa, y1)], fill=(255, 210, 0), width=1)
    for v in (0.0, 0.25, 0.5, 0.75, 1.0):
        yy = y1 - (y1 - y0) * v
        d.line([(x0, yy), (x1, yy)], fill=(40, 40, 46))
        d.text((8, yy - 9), f"{v:.2f}", fill=(150, 150, 150), font=fnt)
    cols = {0: (255, 190, 60), 1: (80, 200, 255), 2: (140, 240, 140)}
    names = {0: "cx", 1: "cy", 2: "w"}
    for c in (0, 1, 2):
        pts = []
        for i in range(n):
            v = ft[i, c]
            if not np.isfinite(v):
                if len(pts) > 1:
                    d.line(pts, fill=cols[c], width=2)
                pts = []
                continue
            pts.append((x0 + (x1 - x0) * i / n, y1 - (y1 - y0) * v))
        if len(pts) > 1:
            d.line(pts, fill=cols[c], width=2)
        d.text((x0 + 10 + c * 70, 6), names[c], fill=cols[c], font=fnt)
    d.text((x0 + 240, 6), "pista de rostro (YuNet ~10 fps, fase cero, sin cruzar cortes); amarillo = corte detectado",
           fill=(200, 200, 200), font=fnt)
    im.save(path)


def render_frame(fr, items, lt_of, ctx, with_blur=True, grader=None, gcfg=None, shot=0):
    res = [camera.MATRIX_EFFECTS[it["kind"]](it, lt_of(it), it["_dur"], ctx)
           for it in items if it["kind"] in camera.MATRIX_EFFECTS and 0 <= lt_of(it) <= it["_dur"]]
    M, mv = camera.combine(res, ctx)
    blurred = with_blur and camera.needs_blur(mv, ctx)
    if with_blur:
        img = camera.apply_camera(C.image_from_rgba(fr), M, mv, ctx)
    else:
        img = camera.warp(C.image_from_rgba(fr), M, ctx)
    if grader is not None:
        img = grader.apply(img, gcfg, shot, ctx)
    for it in items:
        if it["kind"] in camera.POST_EFFECTS and 0 <= lt_of(it) <= it["_dur"]:
            img = camera.POST_EFFECTS[it["kind"]](img, it, lt_of(it), it["_dur"], ctx)
    return img, mv, blurred


def draw_face_box(arr, face):
    out = arr[..., :3].copy()
    if face is None:
        return out
    H, W = out.shape[:2]
    cx, cy, w, h = face
    cv2.rectangle(out, (int((cx - w / 2) * W), int((cy - h / 2) * H)), (int((cx + w / 2) * W), int((cy + h / 2) * H)),
                  (255, 210, 0), 4)
    return out


def main(mp4: bool = False):
    OUT.mkdir(parents=True, exist_ok=True)
    base = RF.base_video()
    t = time.time()
    ft, cuts = camera.face_track(base, 1080, 1920, FPS, N_FRAMES, return_cuts=True)
    print(f"face_track {N_FRAMES} cuadros: {time.time() - t:.2f} s, con cara: {np.isfinite(ft[:, 0]).mean():.0%}, "
          f"cortes {cuts} (esperados {[int(round(a * FPS)) for a, _ in RF.SHOTS[1:]]})")
    fac = int(RF.SHOTS[4][0] * FPS), int(RF.SHOTS[4][1] * FPS)
    print(f"  fachada con 'cara': {np.isfinite(ft[fac[0]:fac[1], 0]).mean():.0%}")
    plot_track(ft, OUT / "01_face_track.png", cuts)
    ts = [1.0, 5.0, 9.0, 12.5, 15.0, 19.0, 22.0, 26.0, 30.0, 35.0]
    frs = RF.frames_at(ts)
    RF.sheet([draw_face_box(f, face_of(ft, int(tt * FPS))) for f, tt in zip(frs, ts)],
             [f"t={tt}s" for tt in ts], cols=10, tile_w=180, title="face_track: caja suavizada").save(
        OUT / "02_face_boxes.png")

    # efectos de encuadre: 8 cuadros a lo largo de cada item (video real en movimiento)
    specs = [
        ("punch", {"kind": "punch", "id": "p1", "amount": 0.18, "hold": 0.6}, 17.6, 1.0),
        ("zoom", {"kind": "zoom", "id": "z1", "from": 1.0, "to": 1.3, "easing": "in_out_cubic"}, 18.8, 1.6),
        ("shake", {"kind": "shake", "id": "s1", "amp": 18, "freq": 12, "decay": 0.6, "rot": 0.8}, 20.5, 0.8),
        ("kenburns", {"kind": "kenburns", "id": "k1", "from": 1.0, "to": 1.2, "pan": [0.06, -0.02]}, 1.0, 3.0),
    ]
    for name, it, t0, dur in specs:
        it["_dur"] = dur
        lts = [dur * k / 7 for k in range(8)]
        frs = RF.frames_at([t0 + lt for lt in lts])
        tiles, labels = [], []
        for lt, fr in zip(lts, frs):
            n = int(round((t0 + lt) * FPS))
            ctx = C.FrameCtx(W=1080, H=1920, fps=FPS, n=n, t=t0 + lt, face=face_of(ft, n))
            img, mv, bl = render_frame(fr, [it], lambda _it: lt, ctx)
            tiles.append(C.image_to_rgba(img))
            labels.append(f"t={lt:.2f}s |v|={mv.speed:.0f}px{' blur' if bl else ''}")
        RF.sheet(tiles, labels, cols=8, tile_w=220, title=f"camera.{name} (item {dur}s)").save(OUT / f"03_{name}.png")

    # motion blur: sin / con, en el cuadro mas rapido del punch y de la sacudida
    tiles, labels = [], []
    for name, it, t0, dur in specs[:3]:
        best = max(((lt, camera.MATRIX_EFFECTS[it["kind"]](it, lt, dur, C.FrameCtx(fps=FPS, face=face_of(ft, int((t0 + lt) * FPS))))[1].speed)
                    for lt in np.arange(0, dur, 1 / FPS)), key=lambda x: x[1])
        lt = best[0]
        fr = RF.frame_at(t0 + lt)
        n = int(round((t0 + lt) * FPS))
        ctx = C.FrameCtx(W=1080, H=1920, fps=FPS, n=n, face=face_of(ft, n))
        a, mv, _ = render_frame(fr, [it], lambda _it: lt, ctx, with_blur=False)
        b, _, _ = render_frame(fr, [it], lambda _it: lt, ctx, with_blur=True)
        x0, y0 = 200, 300
        tiles += [C.image_to_rgba(a)[y0:y0 + 700, x0:x0 + 600], C.image_to_rgba(b)[y0:y0 + 700, x0:x0 + 600]]
        labels += [f"{name} sin blur |v|={mv.speed:.0f}", f"{name} con motion blur"]
    RF.sheet(tiles, labels, cols=6, tile_w=300, title="motion blur de obturador (recorte)").save(OUT / "04_motion_blur.png")

    # post: rgb_split y flash
    fr = RF.frame_at(20.0)
    tiles, labels = [], []
    for name, it, dur in (("rgb_split", {"kind": "rgb_split", "id": "r1", "amount": 14, "jitter": 0.3}, 0.4),
                          ("flash_frame", {"kind": "flash_frame", "id": "f1", "color": "#FFFFFF"}, 0.4),
                          ("flash_frame", {"kind": "flash_frame", "id": "f2", "color": "#FFB36B"}, 0.4)):
        for k in range(6):
            lt = dur * k / 6
            ctx = C.FrameCtx(W=1080, H=1920, fps=FPS, n=600 + k)
            o = camera.POST_EFFECTS[name](C.image_from_rgba(fr), it, lt, dur, ctx)
            tiles.append(C.image_to_rgba(o))
            labels.append(f"{name} {it.get('color', '')} t={lt:.2f}")
    RF.sheet(tiles, labels, cols=6, tile_w=240, title="post: rgb_split / flash").save(OUT / "05_post.png")

    timings()
    if mp4:
        make_mp4(ft)


def timings():
    """ms por llamada con cv2 a 1 hilo (como un worker de render que comparte la CPU)."""
    cv2.setNumThreads(1)
    fr = RF.frame_at(20.0)
    for q, W, H in (("final", 1080, 1920), ("draft", 540, 960)):
        a = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA) if q == "draft" else fr
        img = C.image_from_rgba(a)
        ctx = C.FrameCtx(W=W, H=H, fps=FPS, n=10, quality=q, face=(0.5, 0.3, 0.1, 0.06))

        def best(fn, k=8):
            fn()
            b = 1e9
            for _ in range(k):
                t = time.perf_counter()
                fn()
                b = min(b, time.perf_counter() - t)
            return b * 1000

        it = {"kind": "punch", "id": "p", "amount": 0.18}
        print(f"{q} punch matriz: {best(lambda: camera.punch(it, 0.05, 1.0, ctx), 200):.3f} ms")
        for name, eff, lt in (("zoom 1.1", {"kind": "zoom", "from": 1.1, "to": 1.1}, 0.5),
                              ("zoom 1.25", {"kind": "zoom", "from": 1.25, "to": 1.25}, 0.5),
                              ("punch golpe", {"kind": "punch", "id": "p2", "amount": 0.18}, 0.05),
                              ("shake", {"kind": "shake", "id": "s", "amp": 16, "freq": 12, "decay": 0.6}, 0.1)):
            M, mv = camera.combine([camera.MATRIX_EFFECTS[eff["kind"]](eff, lt, 1.0, ctx)], ctx)
            print(f"{q} camara {name} (|v|={mv.speed:.0f}px, blur={camera.needs_blur(mv, ctx)}): "
                  f"{best(lambda: camera.apply_camera(img, M, mv, ctx)):.2f} ms")
        for name, pit in (("rgb_split", {"amount": 12}), ("flash_frame", {})):
            print(f"{q} {name}: {best(lambda: camera.POST_EFFECTS[name](img, pit, 0.05, 0.4, ctx)):.2f} ms")
    cv2.setNumThreads(0)


def make_mp4(ft):
    """4.5 s de la toma de calle: punch, sacudida + rgb split, zoom lento y flash, con grade."""
    t0, secs = 17.4, 4.5
    n0, nn = int(t0 * FPS), int(secs * FPS)
    items = [
        {"kind": "punch", "id": "p", "amount": 0.16, "hold": 0.7, "_s": 0.25, "_dur": 1.0},
        {"kind": "shake", "id": "s", "amp": 16, "freq": 12, "decay": 0.6, "_s": 1.45, "_dur": 0.6},
        {"kind": "rgb_split", "id": "r", "amount": 12, "_s": 1.45, "_dur": 0.3},
        {"kind": "zoom", "id": "z", "from": 1.0, "to": 1.22, "_s": 2.1, "_dur": 1.9},
        {"kind": "flash_frame", "id": "f", "_s": 4.0, "_dur": 0.35},
    ]
    g = grade.Grader(RF.base_video(), RF.SHOTS, 1080, 1920, FPS, cache_dir=RF.FRAMES)
    g.prepare()
    gcfg = {"look": "warm_clean", "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}
    out = OUT / "camera_demo.mp4"
    cmd = [paths.FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "540x960",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frs = RF.frames_at([(n0 + k) / FPS for k in range(nn)])
    total = 0.0
    for k, fr in enumerate(frs):
        n = n0 + k
        lt0 = k / FPS
        ctx = C.FrameCtx(W=1080, H=1920, fps=FPS, n=n, t=lt0, face=face_of(ft, n))
        t = time.perf_counter()
        img, _, _ = render_frame(fr, items, lambda it: lt0 - it["_s"], ctx, grader=g, gcfg=gcfg, shot=3)
        total += time.perf_counter() - t
        a = cv2.resize(C.image_to_rgba(img), (540, 960), interpolation=cv2.INTER_AREA)
        p.stdin.write(a.tobytes())
    p.stdin.close()
    p.wait()
    print(f"mp4: {out}  (camara+blur+grade+post promedio {total / nn * 1000:.1f} ms/cuadro a 1080x1920)")


if __name__ == "__main__":
    main("--mp4" in sys.argv)
