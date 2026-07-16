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

SAMPLE_FPS = 6           # face detections per second
DET_W, DET_H = 960, 540  # low-res detection frame size
OUT_FPS = 30             # output frame rate (sendcmd emitted per output frame -> fluid)
PRESMOOTH_SEC = 0.7      # zero-phase moving-average on raw detections (denoise)
CAM_FREQ_HZ = 0.9        # virtual-camera natural frequency (lower = calmer/lazier follow)
DEADZONE_FRAC = 0.020    # ignore target moves smaller than this (fraction of width)


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


def _zero_phase_ma(a, win):
    """Zero-phase (forward+backward) moving average -> smoothing without lag."""
    win = max(1, int(win) | 1)                     # odd
    k = np.ones(win) / win
    pad = win // 2
    f = np.convolve(np.pad(a, pad, mode="edge"), k, "valid")
    b = np.convolve(np.pad(f[::-1], pad, mode="edge"), k, "valid")[::-1]
    return b[:len(a)]


def camera_track(xs):
    """Face-x detections -> per-OUTPUT-frame virtual-camera CENTER as a fraction of
    the source width (0..1). Format-INDEPENDENT: the crop-left for any aspect is
    derived later from this + the crop width. Spring + hysteresis dead-zone = smooth,
    no overshoot, no steps, no micro-chasing.
    """
    if not xs:
        return []
    a = _zero_phase_ma(np.array(xs, float), PRESMOOTH_SEC * SAMPLE_FPS)
    dur = (len(a) - 1) / SAMPLE_FPS
    n = max(1, int(round(dur * OUT_FPS)) + 1)
    face = np.interp(np.arange(n) / OUT_FPS, np.arange(len(a)) / SAMPLE_FPS, a)
    dead, dt, omega = DEADZONE_FRAC, 1.0 / OUT_FPS, 2 * np.pi * CAM_FREQ_HZ
    target, x, v, out = face[0], float(face[0]), 0.0, []
    for f in face:
        if abs(f - target) > dead:        # hysteresis: re-anchor only on real movement
            target = f - np.sign(f - target) * dead
        v += (omega * omega * (target - x) - 2 * omega * v) * dt   # critically damped
        x += v * dt
        out.append(float(min(max(x, 0), 1)))
    return out


def write_cmds(centers, src_w, cw, cmds_path):
    """Camera centers (fractions) + a crop width -> per-frame sendcmd crop x.
    Regenerable for ANY output aspect from the same centers."""
    xmax = src_w - cw
    lines = [f"{i / OUT_FPS:.3f} crop@fgc x {int(round(min(max(c * src_w - cw / 2, 0), xmax)))};"
             for i, c in enumerate(centers)]
    Path(cmds_path).write_text("\n".join(lines) + "\n")
    return cmds_path


def build(video, src_w, src_h, cmds_path):
    """Detect + smooth the camera trajectory, write cmds for the default 9:16 crop.
    Returns (crop_w, crop_h, cmds_path, centers). centers is format-independent so the
    plan can re-emit cmds for 1:1 / 4:5 without re-detecting."""
    cw, ch = crop_dims(src_w, src_h)
    if cw >= src_w:                        # portrait: no horizontal pan
        return cw, ch, None, []
    xs = detect_face_track(video, src_w, src_h)
    centers = camera_track(xs)
    write_cmds(centers, src_w, cw, cmds_path)
    if xs:
        print(f"   tracked {len(xs)} samples @ {SAMPLE_FPS}fps -> {len(centers)} cmds, "
              f"face-x {min(xs)*100:.0f}%..{max(xs)*100:.0f}%")
    return cw, ch, cmds_path, centers
