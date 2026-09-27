"""Pruebas de motion/video_io.py: probe (rotacion/HDR), encoders, pipes exactos, voz y base pass.

Usa los MOV reales de la prueba (si existen) y videos sinteticos en spike/work/tests_video_io.
"""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import paths
from motion import video_io as V
from motion.timeline import Timeline
from conftest import TEST_MEDIA, load_plan_v1

WORK = paths.WORK / "tests_video_io"
MOVS = sorted(TEST_MEDIA.glob("IMG_*.MOV")) if TEST_MEDIA.exists() else []
CRUDO = TEST_MEDIA / "crudo_completo.mp4"
need_media = pytest.mark.skipif(not MOVS or not CRUDO.exists(), reason="sin media de prueba")


@pytest.fixture(scope="module")
def work():
    WORK.mkdir(parents=True, exist_ok=True)
    yield WORK


# ------------------------------------------------------------------ probe
@need_media
@pytest.mark.parametrize("mov", MOVS, ids=lambda p: p.name)
def test_probe_mov_iphone_rotado_hlg(mov):
    i = V.probe(mov)
    assert (i["coded_w"], i["coded_h"]) == (3840, 2160)
    assert i["rotation"] in (90, 270)
    assert (i["w"], i["h"]) == (2160, 3840)              # dims de DISPLAY: vertical
    assert i["hdr"] == "hlg" and i["bit_depth"] == 10
    assert 59 < i["fps"] <= 60.01 and i["fps_nominal"] == 60
    assert i["has_audio"] and i["audio_index"] == 0 and i["audio_rate"] == 48000
    assert i["duration"] > 3 and i["type"] == "video"


@need_media
def test_probe_crudo_sdr():
    i = V.probe(CRUDO)
    assert (i["w"], i["h"], i["rotation"]) == (2160, 3840, 0)
    assert i["hdr"] is None and i["fps"] == 60 and i["duration"] == pytest.approx(37.77, abs=0.05)
    assert V.tonemap_filters(i) == ["format=yuv420p", V.SETPARAMS_709]      # ya es bt709/tv
    tm = V.tonemap_filters({"hdr": "hlg"})
    assert tm[0].startswith("zscale=t=linear") and tm[-1] == V.SETPARAMS_709


def test_probe_inexistente(tmp_path):
    i = V.probe(tmp_path / "no.mp4")
    assert i["w"] == 0 and not i["has_audio"]


def test_rotation_normalizada():
    assert V._rotation({"side_data_list": [{"rotation": -90}]}) == 270
    assert V._rotation({"side_data_list": [{"rotation": 90}]}) == 90
    assert V._rotation({"tags": {"rotate": "180"}}) == 180
    assert V._rotation({}) == 0


# ------------------------------------------------------------------ encoders
def test_encoder_por_plataforma(monkeypatch):
    monkeypatch.setattr(V, "videotoolbox_ok", lambda: False)
    a = V.encoder_args(1080, 1920, 30, "inter")
    assert a[a.index("-c:v") + 1] == "libx264" and a[a.index("-g") + 1] == "15"
    assert "-colorspace" in a and "bt709" in a
    monkeypatch.setattr(V, "IS_MAC", False)
    assert V.hwaccel_args() == []
    monkeypatch.setattr(V, "videotoolbox_ok", lambda: True)
    a = V.encoder_args(1080, 1920, 60, "inter")
    assert a[a.index("-c:v") + 1] == "h264_videotoolbox" and a[a.index("-g") + 1] == "30"
    a = V.encoder_args(2160, 5000, 30, "final")          # lado > 4096: videotoolbox no puede
    assert a[a.index("-c:v") + 1] == "libx264"


@pytest.mark.skipif(not paths.IS_MAC, reason="solo Mac")
def test_videotoolbox_disponible_en_mac():
    assert V.videotoolbox_ok()
    assert V.hwaccel_args() == ["-hwaccel", "videotoolbox"]


# ------------------------------------------------------------------ pipes exactos
def _code_frame(i, W=320, H=240):
    """Cuadro con el indice codificado en 10 barras binarias + nivel de gris."""
    a = np.zeros((H, W, 4), np.uint8)
    a[..., 3] = 255
    a[:, :, :3] = 40
    for b in range(10):
        if (i >> b) & 1:
            a[20:120, 10 + b * 30:10 + b * 30 + 24, :3] = 230
    a[150:230, 20:300, 0] = (i * 37) % 256
    a[150:230, 20:300, 1] = 128
    a[150:230, 20:300, 2] = 255 - (i * 11) % 256
    return a


def _decode_code(a):
    return sum(1 << b for b in range(10) if a[70, 10 + b * 30 + 12, :3].mean() > 128)


@pytest.mark.parametrize("sw", [False, True], ids=["plataforma", "libx264"])
def test_chunkwriter_framereader_busqueda_exacta(work, sw):
    out = work / f"code_{'sw' if sw else 'hw'}.mp4"
    n = 90
    w = V.ChunkWriter(out, 320, 240, 30, kind="inter" if not sw else "final", force_sw=sw)
    for i in range(n):
        w.write(_code_frame(i))
    w.close()
    assert V.count_frames(out) == n
    with V.FrameReader(out, 320, 240, 0, None, 30) as r:
        seq = list(r)
    assert [_decode_code(f) for f in seq] == list(range(n))
    for start in (1, 7, 14, 15, 16, 29, 30, 31, 44, 59, 88):
        with V.FrameReader(out, 320, 240, start, 2, 30) as r:
            got = [_decode_code(f) for f in r]
        assert got == [start, start + 1][: n - start], (start, got)


def test_color_ida_y_vuelta_bt709(work):
    """RGBA -> ChunkWriter -> FrameReader conserva colores planos (sin corrimiento 601/709)."""
    cols = [(230, 40, 40), (40, 200, 60), (30, 60, 220), (250, 210, 60), (128, 128, 128),
            (20, 20, 20), (240, 240, 240), (224, 170, 142)]
    out = work / "color.mp4"
    w = V.ChunkWriter(out, 256, 256, 30, kind="inter")
    for c in cols:
        a = np.zeros((256, 256, 4), np.uint8)
        a[..., :3] = c
        a[..., 3] = 255
        for _ in range(3):
            w.write(a)
    w.close()
    with V.FrameReader(out, 256, 256, 0, None, 30) as r:
        frames = list(r)
    for k, c in enumerate(cols):
        got = frames[k * 3 + 1][64:192, 64:192, :3].reshape(-1, 3).mean(0)
        assert np.abs(got - np.array(c)).max() < 4.0, (c, got)


def test_writer_rechaza_tamano_incorrecto(work):
    w = V.ChunkWriter(work / "bad.mp4", 64, 64, 30)
    with pytest.raises(ValueError):
        w.write(np.zeros((32, 64, 4), np.uint8))
    w.abort()


# ------------------------------------------------------------------ voz: cortes con microfundido
def test_cut_audio_longitud_y_contenido():
    sr, fps = V.SR, 30
    t = np.arange(int(10 * sr)) / sr
    src = np.stack([np.sin(2 * np.pi * 220 * t), np.sin(2 * np.pi * 330 * t)], 1).astype(np.float32)
    tl = Timeline([(1.0, 3.0), (4.5, 6.0), (8.0, 10.0)], 10.0, fps=fps)
    out = V.cut_audio(src, tl, fps)
    assert len(out) == tl.n_frames() / fps * sr
    # lejos de las uniones, las muestras son exactamente las de la fuente
    np.testing.assert_allclose(out[1000:2000], src[sr + 1000:sr + 2000], atol=1e-6)
    j = int(2.0 * sr)                                    # primera union en la salida
    np.testing.assert_allclose(out[j + 1000:j + 2000], src[int(4.5 * sr) + 1000:int(4.5 * sr) + 2000],
                               atol=1e-6)
    # sin clics: el salto maximo entre muestras en la union es del orden del de la senoide
    d = np.abs(np.diff(out[j - 400:j + 400, 0])).max()
    assert d < 2 * (2 * np.pi * 220 / sr) + 0.05


def test_cut_audio_fuente_corta_rellena_con_silencio():
    fps = 30
    src = np.ones((V.SR * 2, 2), np.float32) * 0.1
    tl = Timeline([(0.0, 3.0)], 3.0, fps=fps)
    out = V.cut_audio(src, tl, fps)
    assert len(out) == 3 * V.SR and np.all(out[int(2.5 * V.SR):] == 0)


def test_cut_audio_final_recortado_termina_en_silencio():
    """El ultimo keep termina en medio de un tono (la fuente sigue): la voz se apaga con un
    fundido (antes terminaba a media onda: clic/pop al final del export). Si el keep llega al
    final de la fuente no se toca."""
    sr, fps = V.SR, 30
    t = np.arange(int(6 * sr)) / sr
    src = (0.8 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)[:, None].repeat(2, 1)
    tl = Timeline([(0.0, 2.0), (3.0, 4.51)], 6.0, fps=fps)
    out = V.cut_audio(src, tl, fps)
    assert np.abs(out[-1]).max() < 1e-3 and np.abs(out[-3:]).max() < 5e-3
    k = int(V.END_FADE_MS / 1000 * sr)
    assert np.abs(out[-k - 2000:-k - 200]).max() > 0.7          # antes del fundido: intacto
    full = V.cut_audio(src, Timeline([(0.0, 6.0)], 6.0, fps=fps), fps)
    np.testing.assert_allclose(full[-200:], src[len(src) - 200:], atol=1e-6)
    assert V.VOICE_VERSION != "v3"                                # la voz cacheada se rehace


# ------------------------------------------------------------------ base pass
def _synthetic_source(path: Path, dur=6.0):
    if path.exists():
        return path
    subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc2=s=720x1280:r=60:d={dur}", "-f", "lavfi", "-i",
                    f"sine=f=440:r=44100:d={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def test_base_pass_sintetico_y_cache(work):
    src = _synthetic_source(work / "synthetic_src.mp4")
    plan = {"video": str(src), "duration": 6.0,
            "cuts": [{"start": 0, "end": 0.5, "enabled": True},
                     {"start": 2.0, "end": 3.1, "enabled": True},
                     {"start": 5.5, "end": 6.0, "enabled": True}]}
    tl = Timeline.from_plan(plan, fps=30)
    cache = work / "cache"
    shutil.rmtree(cache, ignore_errors=True)
    bv, bw = V.base_paths(cache, plan, 360, 640, 30, tl, enhance_audio=False)
    pcts = []
    r = V.base_pass(plan, bv, bw, 360, 640, 30, tl, enhance_audio=False, on_pct=pcts.append)
    assert not r["cached_video"] and r["n_frames"] == tl.n_frames() == 117
    si = V.stream_info(bv)["video"]
    assert (si["width"], si["height"], si["r_frame_rate"]) == (360, 640, "30/1")
    assert int(si["nb_frames"]) == 117 and si["color_space"] == "bt709"
    import soundfile as sf
    a, sr = sf.read(bw)
    assert sr == 48000 and a.shape == (117 * 1600, 2)
    assert pcts and max(pcts) <= 100.0
    r2 = V.base_pass(plan, bv, bw, 360, 640, 30, tl, enhance_audio=False)
    assert r2["cached_video"] and r2["cached_voice"]
    # la clave cambia con los cortes y con el formato
    plan2 = dict(plan, cuts=plan["cuts"][:2])
    tl2 = Timeline.from_plan(plan2, fps=30)
    assert V.base_paths(cache, plan2, 360, 640, 30, tl2)[0] != bv
    assert V.base_paths(cache, plan, 540, 960, 30, tl)[0] != bv


def test_base_pass_cancelable(work):
    src = _synthetic_source(work / "synthetic_src.mp4")
    plan = {"video": str(src), "duration": 6.0, "cuts": []}
    tl = Timeline.from_plan(plan, fps=30)
    out = work / "cancel"
    shutil.rmtree(out, ignore_errors=True)
    with pytest.raises(V.Canceled):
        V.base_pass(plan, out / "b.mp4", out / "v.wav", 360, 640, 30, tl, enhance_audio=False,
                    cancel=lambda: True)
    assert not (out / "b.mp4").exists()


@need_media
def test_base_pass_crudo_real_cortes_exactos(work):
    plan = load_plan_v1()
    plan["video"] = str(CRUDO)
    tl = Timeline.from_plan(plan, fps=30)
    cache = paths.WORK / "motion_cache"
    bv, bw = V.base_paths(cache, plan, 1080, 1920, 30, tl)
    r = V.base_pass(plan, bv, bw, 1080, 1920, 30, tl)
    N = tl.n_frames()
    assert r["n_frames"] == N and V.count_frames(bv) == N
    si = V.stream_info(bv)["video"]
    assert si["color_primaries"] == si["color_transfer"] == si["color_space"] == "bt709"
    import soundfile as sf
    assert sf.info(str(bw)).frames == N * 1600
    # el cuadro final n corresponde a la fuente en inverse(n/fps): primer cuadro tras un corte
    for n in (0, 67, 106):
        with V.FrameReader(bv, 1080, 1920, n, 1, 30) as rd:
            got = rd.read()[..., :3].astype(int)
        t_orig = tl.inverse(n / 30)
        errs = []
        for dt in (0.0, -1 / 30, 1 / 30):
            cmd = [V.FFMPEG, "-v", "error", "-ss", f"{t_orig + dt - 0.5 / 60:.6f}", "-i", str(CRUDO),
                   "-frames:v", "1", "-vf", "scale=1080:1920:flags=lanczos,"
                   "scale=in_color_matrix=bt709:in_range=tv,format=rgb24", "-f", "rawvideo", "pipe:1"]
            ref = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.uint8)
            errs.append(np.abs(ref.reshape(1920, 1080, 3).astype(int) - got).mean())
        assert errs[0] < 3.0 and errs[0] <= min(errs[1:]), (n, errs)


@need_media
def test_base_pass_mov_hlg_vertical_tonemap(work):
    """IMG_0246 (HEVC HLG rotado): el base sale vertical, SDR y coincide con el crudo."""
    mov = next(m for m in MOVS if m.name == "IMG_0246.MOV")
    plan = {"video": str(mov), "duration": V.probe(mov)["duration"],
            "cuts": [{"start": 0, "end": 5.0, "enabled": True}, {"start": 6.0, "end": 8.0, "enabled": True}]}
    tl = Timeline.from_plan(plan, fps=30)
    cache = work / "cache_mov"
    bv, bw = V.base_paths(cache, plan, 540, 960, 30, tl, enhance_audio=False)
    V.base_pass(plan, bv, bw, 540, 960, 30, tl, enhance_audio=False)
    si = V.stream_info(bv)["video"]
    assert (si["width"], si["height"]) == (540, 960) and si["color_transfer"] == "bt709"
    with V.FrameReader(bv, 540, 960, 15, 1, 30) as rd:
        got = rd.read()[..., :3].astype(int)                 # t_orig = 5.5 s
    cmd = [V.FFMPEG, "-v", "error", "-ss", f"{5.5 - 0.5 / 60:.6f}", "-i", str(CRUDO), "-frames:v", "1",
           "-vf", "scale=540:960,scale=in_color_matrix=bt709:in_range=tv,format=rgb24",
           "-f", "rawvideo", "pipe:1"]
    ref = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.uint8).reshape(960, 540, 3)
    diff = np.abs(ref.astype(int) - got).mean()
    # mismo encuadre (sin recorte de cabeza/pies) y brillo parecido pese a otro tonemap
    assert diff < 25, diff
    assert abs(ref.mean() - got.mean()) < 25
    rows_ref, rows_got = ref.mean(axis=(1, 2)), got.mean(axis=(1, 2))
    assert np.corrcoef(rows_ref, rows_got)[0, 1] > 0.9


# ------------------------------------------------------------------ concat / mux
def test_concat_y_mux_sincronia(work):
    chunks = []
    for k in range(3):
        p = work / f"cc_{k}.mp4"
        w = V.ChunkWriter(p, 128, 128, 30)
        for i in range(30):
            w.write(np.full((128, 128, 4), (k * 60 + 40, 90, 120, 255), np.uint8))
        w.close()
        chunks.append(p)
    vid = work / "cc_all.mp4"
    V.concat(chunks, vid)
    assert V.count_frames(vid) == 90
    wav = work / "cc.wav"
    V.write_wav(wav, np.zeros((3 * V.SR, 2), np.float32))
    out = work / "cc_mux.mp4"
    V.mux(vid, wav, out)
    si = V.stream_info(out)
    assert float(si["video"]["duration"]) == pytest.approx(3.0, abs=1 / 30)
    assert float(si["audio"]["duration"]) == pytest.approx(3.0, abs=0.03)
    assert abs(float(si["audio"]["start_time"]) - float(si["video"]["start_time"])) < 0.03


def test_prune_cache(work):
    d = work / "prune"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir()
    import os
    import time
    now = time.time()

    def old(p, age):
        os.utime(p, (now - age, now - age))

    for i in range(5):
        f = d / f"base_k{i}.mp4"
        f.write_bytes(b"x")
        (d / f"base_k{i}.mp4.json").write_text("{}")
        old(d / f"base_k{i}.mp4.json", 4000 - i)           # ultimo uso = su .json (touch)
        fc = d / f"face_k{i}_abcd1234.npy"                   # pista de rostro de ese base
        fc.write_bytes(b"x")
        old(f, 4000 - i)
        old(fc, 4000 - i)
    recent = d / "base_r.mp4"                               # usado hace 1 min: nunca se poda
    recent.write_bytes(b"x")
    old(recent, 60)
    part = d / "base_k9.123.abc.part.mp4"                    # render en curso: no se toca
    part.write_bytes(b"x")
    stale = d / "voice_z.1.a.part.wav"                        # huerfano de hace 7 h: se borra
    stale.write_bytes(b"x")
    old(stale, 7 * 3600)
    for i in range(4):
        m = d / f"matte_{i}"
        m.mkdir()
        (m / "000001.png").write_bytes(b"x")
        old(m, 4000 - i)
    # un base viejo pero USADO hace poco (touch de su .json) tampoco se poda
    os.utime(d / "base_k0.mp4.json", None)
    V.prune_cache(d, keep=2)
    assert sorted(p.name for p in d.glob("base_*.mp4")) == ["base_k0.mp4", part.name,
                                                            "base_r.mp4"]
    assert sorted(p.name for p in d.glob("face_*.npy")) == ["face_k0_abcd1234.npy"]
    assert sorted(p.name for p in d.glob("matte_*")) == ["matte_2", "matte_3"]
    assert part.exists() and not stale.exists()


def _horizontal_source(path: Path):
    """Fuente horizontal 1280x720 con gradiente en X (R = x/W): el color medio del
    recorte vertical delata hacia donde apunta la camara."""
    if path.exists():
        return path
    w = V.ChunkWriter(path, 1280, 720, 30, kind="inter")
    a = np.zeros((720, 1280, 4), np.uint8)
    a[..., 0] = np.linspace(0, 255, 1280)[None, :].astype(np.uint8)
    a[..., 1] = 80
    a[..., 3] = 255
    for _ in range(90):
        w.write(a)
    w.close()
    return path


def test_base_pass_horizontal_sigue_la_camara(work):
    src = _horizontal_source(work / "horizontal_src.mp4")
    centers = list(np.linspace(0.2, 0.8, 91))           # 3 s de trayectoria a 30 fps
    plan = {"video": str(src), "duration": 3.0, "cuts": [], "camera": centers}
    tl = Timeline.from_plan(plan, fps=30)
    cache = work / "cache_h"
    shutil.rmtree(cache, ignore_errors=True)
    bv, bw = V.base_paths(cache, plan, 270, 480, 30, tl, enhance_audio=False)
    V.base_pass(plan, bv, bw, 270, 480, 30, tl, enhance_audio=False)
    with V.FrameReader(bv, 270, 480, 0, None, 30) as r:
        frames = list(r)
    assert len(frames) == 90
    reds = [f[..., 0].mean() for f in frames]
    # el recorte 9:16 (405 px de 1280) se desplaza de izquierda a derecha siguiendo el centro
    assert reds[0] < 90 and reds[-1] > 165 and all(b >= a - 1 for a, b in zip(reds, reds[1:]))
    meta = json.loads(Path(str(bv) + ".json").read_text())
    assert meta["mode"] == "track"
    # con margen desenfocado (air) el video queda inset y la clave de cache cambia
    bv2, bw2 = V.base_paths(cache, plan, 270, 480, 30, tl, enhance_audio=False, air=0.16)
    assert bv2 != bv
    V.base_pass(plan, bv2, bw2, 270, 480, 30, tl, enhance_audio=False, air=0.16)
    assert json.loads(Path(str(bv2) + ".json").read_text())["mode"] == "track+air"
    assert V.count_frames(bv2) == 90


def test_cuadro_suelto_horizontal_igual_al_base(work):
    """render._source_frame (preview sin base) usa el mismo x de camara que el base pass."""
    from motion import render as R
    src = _horizontal_source(work / "horizontal_src.mp4")
    plan = {"video": str(src), "duration": 3.0, "cuts": [{"start": 0.0, "end": 0.5, "enabled": True}],
            "camera": list(np.linspace(0.2, 0.8, 91))}
    tl = Timeline.from_plan(plan, fps=30)
    cache = work / "cache_h2"
    bv, bw = V.base_paths(cache, plan, 270, 480, 30, tl, enhance_audio=False)
    V.base_pass(plan, bv, bw, 270, 480, 30, tl, enhance_audio=False)
    for n in (0, 30, 70):
        with V.FrameReader(bv, 270, 480, n, 1, 30) as r:
            b = r.read()[..., :3].astype(int)
        s = R._source_frame(plan, tl, n, 270, 480, 30)[..., :3].astype(int)
        assert np.abs(b - s).mean() < 3.0, n


# ------------------------------------------------------------------ color, niveles, locks, formatos
def test_encoder_nivel_y_bitrate_de_entrega():
    a = V.encoder_args(1080, 1920, 30, "final")
    assert a[a.index("-level:v") + 1] == "4.1"
    if a[a.index("-c:v") + 1] == "h264_videotoolbox":
        assert 12e6 < int(a[a.index("-b:v") + 1]) < 15e6         # ~13.7 Mbps a 1080p30
    assert V.encoder_args(1080, 1920, 60, "final")[V.encoder_args(1080, 1920, 60, "final")
                                                    .index("-level:v") + 1] == "4.2"
    assert "-level:v" not in V.encoder_args(1080, 1920, 30, "inter")
    assert V.h264_level(3840, 2160, 30) == "5.1"


def test_chunkwriter_etiqueta_color_completa(work):
    out = work / "tags.mp4"
    w = V.ChunkWriter(out, 64, 64, 30, "final")
    for _ in range(3):
        w.write(np.full((64, 64, 4), 128, np.uint8))
    w.close()
    si = V.stream_info(out)["video"]
    assert (si["color_primaries"], si["color_transfer"], si["color_space"], si["color_range"]) == \
        ("bt709", "bt709", "bt709", "tv")


def test_source_matrix_heuristica():
    assert V.source_matrix({"color_space": "smpte170m", "color_range": "pc"}) == ("bt601", "pc")
    assert V.source_matrix({"color_space": None, "w": 640, "h": 480}) == ("bt601", "tv")
    assert V.source_matrix({"color_space": None, "w": 720, "h": 1280}) == ("bt709", "tv")
    assert V.source_matrix({"color_space": "bt709", "pix_fmt": "yuvj420p"}) == ("bt709", "pc")


def test_base_pass_convierte_bt601_rango_completo(work):
    """Una fuente SDR bt601 de rango completo (Android/grabacion de pantalla) se CONVIERTE a
    bt709/tv: antes solo se re-etiquetaba (corrimiento de color y negros aplastados)."""
    src = work / "bt601_pc.mp4"
    cols = [(230, 40, 40), (40, 200, 60), (30, 60, 220), (16, 16, 16), (245, 245, 245)]
    if not src.exists():
        inputs = []
        for c in cols:
            inputs += ["-f", "lavfi", "-i", "color=c=0x%02X%02X%02X:s=320x568:r=30:d=0.5" % c]
        fc = "".join(f"[{i}:v]" for i in range(len(cols))) + f"concat=n={len(cols)}:v=1:a=0," \
             "scale=out_color_matrix=bt601:out_range=pc,format=yuvj420p[v]"
        subprocess.run([V.FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", fc,
                        "-map", "[v]", "-c:v", "libx264", "-qp", "0", "-color_range", "pc",
                        "-colorspace", "smpte170m", "-color_primaries", "smpte170m",
                        "-color_trc", "smpte170m", str(src)], check=True)
    info = V.probe(src)
    assert V.source_matrix(info) == ("bt601", "pc")
    plan = {"video": str(src), "duration": info["duration"], "cuts": []}
    tl = Timeline.from_plan(plan, fps=30)
    cache = work / "cache_601"
    shutil.rmtree(cache, ignore_errors=True)
    bv, bw = V.base_paths(cache, plan, 320, 568, 30, tl, enhance_audio=False)
    V.base_pass(plan, bv, bw, 320, 568, 30, tl, enhance_audio=False)
    with V.FrameReader(bv, 320, 568, 0, None, 30) as r:
        frames = list(r)
    for k, c in enumerate(cols):
        got = frames[k * 15 + 7][100:400, 60:260, :3].reshape(-1, 3).mean(0)
        assert np.abs(got - np.array(c)).max() < 5.0, (c, got)


def test_base_pass_lock_un_solo_ffmpeg(work, monkeypatch):
    """ensure_base en segundo plano + export a la vez: un solo base pass, sin temporales
    compartidos y ambos terminan con el mismo archivo valido."""
    import threading
    src = _synthetic_source(work / "synthetic_src.mp4")
    plan = {"video": str(src), "duration": 6.0, "cuts": [{"start": 2, "end": 3, "enabled": True}]}
    tl = Timeline.from_plan(plan, fps=30)
    cache = work / "cache_lock"
    shutil.rmtree(cache, ignore_errors=True)
    bv, bw = V.base_paths(cache, plan, 180, 320, 30, tl, enhance_audio=False)
    calls = {"v": 0, "a": 0}
    real_v, real_a = V._video_pass, V._voice_pass

    def spy_v(*a, **k):
        calls["v"] += 1
        return real_v(*a, **k)

    def spy_a(*a, **k):
        calls["a"] += 1
        return real_a(*a, **k)
    monkeypatch.setattr(V, "_video_pass", spy_v)
    monkeypatch.setattr(V, "_voice_pass", spy_a)
    res, errs = [], []

    def go():
        try:
            res.append(V.base_pass(plan, bv, bw, 180, 320, 30, tl, enhance_audio=False))
        except BaseException as e:  # noqa
            errs.append(e)
    ths = [threading.Thread(target=go) for _ in range(3)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert not errs and len(res) == 3
    assert calls == {"v": 1, "a": 1}
    assert sum(r["cached_video"] for r in res) == 2
    assert V.count_frames(bv) == tl.n_frames()
    assert not list(cache.glob("*.part*"))


def test_reencuadre_vertical_a_16_9_inset_con_fondo(work):
    """Fuente vertical a 16:9: la toma completa centrada (nada recortado) sobre su propio
    fondo desenfocado (antes: cover centrado perdia el 68% del cuadro)."""
    src = _synthetic_source(work / "synthetic_src.mp4")
    plan = {"video": str(src), "duration": 6.0, "cuts": []}
    tl = Timeline.from_plan(plan, fps=30)
    cache = work / "cache_pillar"
    shutil.rmtree(cache, ignore_errors=True)
    bv, bw = V.base_paths(cache, plan, 384, 216, 30, tl, enhance_audio=False)
    r = V.base_pass(plan, bv, bw, 384, 216, 30, tl, enhance_audio=False)
    assert r["mode"] == "pillar"
    with V.FrameReader(bv, 384, 216, 45, 1, 30) as rd:
        got = rd.read()[..., :3].astype(int)
    fg_w = 122
    x0 = (384 - fg_w) // 2
    ref = subprocess.run([V.FFMPEG, "-v", "error", "-ss", f"{1.5 - 0.5 / 60:.4f}", "-i", str(src),
                          "-frames:v", "1", "-vf", f"scale={fg_w}:216:flags=lanczos,"
                          "scale=in_color_matrix=bt709:in_range=tv,format=rgb24",
                          "-f", "rawvideo", "pipe:1"], capture_output=True).stdout
    ref = np.frombuffer(ref, np.uint8).reshape(216, fg_w, 3).astype(int)
    assert np.abs(got[:, x0:x0 + fg_w] - ref).mean() < 12            # toma completa al centro
    side = got[:, :x0 - 4]
    assert np.abs(np.diff(side, axis=1)).mean() < np.abs(np.diff(ref, axis=1)).mean() / 2


@need_media
def test_reencuadre_vertical_a_1_1_sigue_la_cara(work):
    """Vertical -> 1:1: ancho completo y recorte vertical por keep que deja la cara con aire
    arriba (antes: recorte centrado que podia cortar la cabeza)."""
    import cv2
    plan = {"video": str(CRUDO), "duration": V.probe(CRUDO)["duration"],
            "cuts": [{"start": 0.0, "end": 2.0, "enabled": True},
                     {"start": 4.0, "end": 9.0, "enabled": True},
                     {"start": 11.0, "end": 99.0, "enabled": True}]}
    tl = Timeline.from_plan(plan, fps=30)
    cache = work / "cache_vfollow"
    bv, bw = V.base_paths(cache, plan, 360, 360, 30, tl, enhance_audio=False)
    r = V.base_pass(plan, bv, bw, 360, 360, 30, tl, enhance_audio=False)
    assert r["mode"] == "vfollow"
    meta = json.loads(Path(str(bv) + ".json").read_text())
    assert len(meta["y"]) == 2 and all(0 <= y <= 640 - 360 for y in meta["y"])
    det = cv2.FaceDetectorYN.create(str(paths.YUNET), "", (360, 360), 0.6, 0.3, 5000)
    with V.FrameReader(bv, 360, 360, 0, None, 30) as rd:
        frames = list(rd)
    cys = []
    for f in frames[::10]:
        _, faces = det.detect(np.ascontiguousarray(f[..., 2::-1]))
        if faces is not None and len(faces):
            fc = max(faces, key=lambda q: q[2] * q[3])
            cys.append((fc[1] + fc[3] / 2) / 360)
            assert fc[1] > 2                              # la cabeza no queda cortada arriba
    assert len(cys) >= len(frames[::10]) // 2
    assert 0.25 < float(np.median(cys)) < 0.6, cys
