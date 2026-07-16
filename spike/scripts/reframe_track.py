#!/usr/bin/env python3
"""
Subject-tracking reframe for Reelfy spike.

Detects the primary face across the video (YuNet, Apache-2.0 — NOT YOLO/AGPL),
builds a heavily-smoothed horizontal trajectory, and emits an ffmpeg `sendcmd`
script that pans a 9:16 crop window to follow the subject (a virtual camera
operator). Replaces the fixed center-crop that clipped the subject when he moved.

Design notes:
- Detect on low-res sampled frames (fast) via an ffmpeg rawvideo pipe.
- Smooth with a moving average + max-velocity clamp so the pan is calm, not jittery.
- Map face-center-x (fraction) to a full-res crop x, clamped in-frame.
"""
import subprocess
from pathlib import Path

import cv2
import numpy as np

FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
SPIKE = Path(__file__).resolve().parent.parent
YUNET = SPIKE / "models/face_detection_yunet_2023mar.onnx"

SAMPLE_FPS = 4          # face detections per second
DET_W, DET_H = 960, 540  # low-res detection frame size
SMOOTH_WIN = 9          # moving-average window (in samples ~= 2.25s)
MAX_VEL_FRAC = 0.010    # max crop-center move per sample (fraction of width) -> calm pan


def crop_dims(src_w, src_h, target_w=1080, target_h=1920):
    """9:16 cover-crop dims at source resolution."""
    ar = target_w / target_h
    if src_w / src_h > ar:          # source wider than 9:16 -> crop width, track X
        cw = round(src_h * ar); ch = src_h
    else:                            # taller -> crop height (X tracking is a no-op)
        cw = src_w; ch = round(src_w / ar)
    return cw, ch


def detect_face_track(video, src_w, src_h):
    """Return list of face-center-x fractions [0..1], one per sampled frame,
    with gaps (no detection) filled by carrying the last value."""
    det = cv2.FaceDetectorYN.create(str(YUNET), "", (DET_W, DET_H), 0.6, 0.3, 5000)
    det.setInputSize((DET_W, DET_H))
    proc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-hwaccel", "videotoolbox", "-i", str(video),
         "-vf", f"fps={SAMPLE_FPS},scale={DET_W}:{DET_H}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE)
    frame_bytes = DET_W * DET_H * 3
    xs, last = [], 0.5
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        img = np.frombuffer(buf, np.uint8).reshape(DET_H, DET_W, 3)
        _, faces = det.detect(img)
        if faces is not None and len(faces):
            f = max(faces, key=lambda f: f[2] * f[3])  # largest face
            last = (f[0] + f[2] / 2) / DET_W
        xs.append(last)
    proc.stdout.close(); proc.wait()
    return xs


def smooth(xs):
    """Moving average + max-velocity clamp for a calm pan."""
    if not xs:
        return xs
    a = np.array(xs, float)
    k = np.ones(SMOOTH_WIN) / SMOOTH_WIN
    a = np.convolve(np.pad(a, SMOOTH_WIN // 2, mode="edge"), k, "valid")[:len(xs)]
    out = [a[0]]
    for v in a[1:]:
        d = np.clip(v - out[-1], -MAX_VEL_FRAC, MAX_VEL_FRAC)
        out.append(out[-1] + d)
    return out


def write_sendcmd(xs, src_w, cw, cmds_path):
    """Map smoothed face-x fractions -> full-res crop x, write a sendcmd script."""
    xmax = src_w - cw
    lines = []
    for i, fx in enumerate(xs):
        t = i / SAMPLE_FPS
        cx = fx * src_w - cw / 2          # crop left so face is centered
        x = int(min(max(cx, 0), xmax))
        lines.append(f"{t:.3f} crop x {x};")
    Path(cmds_path).write_text("\n".join(lines) + "\n")
    return cmds_path


def build(video, src_w, src_h, cmds_path):
    """Full trajectory build. Returns (crop_w, crop_h, cmds_path) or (cw,ch,None)
    if no horizontal tracking is needed (portrait source)."""
    cw, ch = crop_dims(src_w, src_h)
    if cw >= src_w:                        # no horizontal room to pan
        return cw, ch, None
    xs = smooth(detect_face_track(video, src_w, src_h))
    write_sendcmd(xs, src_w, cw, cmds_path)
    rng = (min(xs), max(xs)) if xs else (0, 0)
    print(f"   tracked {len(xs)} samples, face-x range {rng[0]*100:.0f}%..{rng[1]*100:.0f}%")
    return cw, ch, cmds_path
