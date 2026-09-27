"""Stub de matte: elipse fija (la 'persona') en el centro-arriba."""
import numpy as np


class MatteProvider:
    def __init__(self, base_video, W, H, fps, frame_ranges, cache_dir, quality):
        self.W, self.H, self.ranges = W, H, frame_ranges
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = ((xx - W * 0.5) / (W * 0.28)) ** 2 + ((yy - H * 0.45) / (H * 0.36)) ** 2
        self.m = np.clip((1.0 - d) * 6, 0, 1).astype(np.float32)

    def prepare(self, on_pct=None):
        return self

    def get(self, n):
        return self.m


RENDERERS = {}
