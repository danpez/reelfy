#!/usr/bin/env python3
"""
Auto-thumbnail: pick a strong frame (clear, large, well-placed face via YuNet) from
the rendered short and overlay a bold hook title. Local, no cloud.
"""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SPIKE = Path(__file__).resolve().parent.parent
YUNET = SPIKE / "models/face_detection_yunet_2023mar.onnx"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _best_frame(video):
    """Scan the clip and return the frame (BGR) with the strongest, best-placed face."""
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    det = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.6, 0.3, 5000)
    best, best_score = None, -1.0
    for fr in np.linspace(n * 0.12, n * 0.85, 8):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fr))
        ok, img = cap.read()
        if not ok:
            continue
        h, w = img.shape[:2]
        det.setInputSize((w, h))
        _, faces = det.detect(img)
        if faces is None or not len(faces):
            continue
        f = max(faces, key=lambda x: x[2] * x[3])
        score = float(f[-1]) * f[2] * f[3]
        if (f[1] + f[3] / 2) / h < 0.6:      # prefer face in the upper 60% (headroom below)
            score *= 1.25
        if score > best_score:
            best_score, best = score, img.copy()
    if best is None:                          # no face found -> a frame at ~30%
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * 0.3))
        _, best = cap.read()
    cap.release()
    return best


def make_thumbnail(video, out_jpg, title):
    bgr = _best_frame(video)
    if bgr is None:
        return None
    img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    # darken the bottom for text legibility (also hides the running caption)
    gh = int(H * 0.44)
    for i in range(gh):
        draw.line([(0, H - gh + i), (W, H - gh + i)],
                  fill=(8, 10, 16, int(210 * (i / gh) ** 1.35)))
    # wrapped uppercase hook
    size = max(28, int(W * 0.088))
    font = ImageFont.truetype(FONT, size)
    lines, cur = [], ""
    for word in (title or "").upper().split():
        t = (cur + " " + word).strip()
        if cur and draw.textlength(t, font=font) > W * 0.88:
            lines.append(cur); cur = word
        else:
            cur = t
    if cur:
        lines.append(cur)
    lines = lines[:3] or [""]
    lh = int(size * 1.15)
    y = H - int(H * 0.055) - len(lines) * lh
    for ln in lines:
        x = (W - draw.textlength(ln, font=font)) / 2
        draw.text((x + 3, y + 3), ln, font=font, fill=(0, 0, 0, 190))       # shadow
        draw.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        y += lh
    img.save(out_jpg, "JPEG", quality=88)
    return out_jpg


if __name__ == "__main__":
    import sys
    print(make_thumbnail(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "Reelfy"))
