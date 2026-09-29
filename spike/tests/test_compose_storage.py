"""scripts/compose.py: el intermedio del montaje nunca pesa mas que su fuente.

- Bitrate con tope: <= 1.2x la fuente de mas bitrate del montaje y <= MAX_BPS (un 4K de iPhone a
  25 Mbps se reescribia a ~75 Mbps: 3x la fuente).
- Un UNICO video completo que ya es un intermedio valido se COPIA (sin re-codificar ni perder
  calidad); cualquier procesado (recorte, fundido, volumen, capas, otro lienzo, HDR, rotacion,
  otros fps, sin audio) re-codifica como siempre.

Material sintetico (lavfi), sin datos de clientes.
"""
import json
import subprocess

import pytest

import compose
import paths
from motion import video_io


def _clip(path, w=320, h=240, fps=30, dur=1.5, vbr="1500k", codec="libx264", audio=True, extra=()):
    cmd = [str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
           f"testsrc2=s={w}x{h}:r={fps}:d={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={dur}"]
    # etiquetas bt709 (sin ellas un video SD se trata como bt601 y se convierte: no se copia)
    cmd += ["-c:v", codec, "-b:v", vbr, "-pix_fmt", "yuv420p", "-colorspace", "bt709",
            "-color_primaries", "bt709", "-color_trc", "bt709", *extra]
    cmd += (["-c:a", "aac", "-ac", "1", "-shortest"] if audio else [])
    subprocess.run(cmd + [str(path)], check=True)
    return path


def _streams(path):
    r = subprocess.run([str(paths.FFPROBE), "-v", "error", "-show_streams", "-show_format", "-of",
                        "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def _video_md5(path):
    """md5 de los PAQUETES de video (sin decodificar): igual <=> el video se copio tal cual."""
    r = subprocess.run([str(paths.FFMPEG), "-v", "error", "-i", str(path), "-map", "0:v:0", "-c",
                        "copy", "-f", "md5", "-"], capture_output=True, text=True, check=True)
    return r.stdout.strip()


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("compose_storage")
    return {"d": d,
            "a": _clip(d / "a.mp4", vbr="1500k"),
            "b": _clip(d / "b.mp4", vbr="600k", dur=1.0),
            "fps25": _clip(d / "fps25.mp4", fps=25),
            "mute": _clip(d / "mute.mp4", audio=False)}


def test_tope_de_bitrate_nunca_supera_la_fuente():
    hq = float(video_io.hq_bitrate(3840, 2160, 30))
    assert hq > 70e6                                             # el de antes: ~75 Mbps en 4K30
    assert compose._vbitrate(3840, 2160, 30, 25e6) == str(int(1.2 * 25e6))   # iPhone 4K a 25 Mbps
    assert int(compose._vbitrate(3840, 2160, 30)) == int(compose.MAX_BPS) < hq   # fuente desconocida
    assert int(compose._vbitrate(1080, 1920, 30, 300e6)) == int(video_io.hq_bitrate(1080, 1920, 30))
    assert compose._vbitrate(640, 360, 30, 1e6) == str(int(1.2e6))
    assert int(compose._vbitrate(640, 360, 30, 1000)) == compose.MIN_BPS        # minimo tecnico


def test_bitrate_del_montaje_es_el_de_la_fuente_mas_pesada(media):
    ra, rb = compose.src_bitrate(media["a"]), compose.src_bitrate(media["b"])
    assert ra and rb and ra > rb
    items = [{"path": str(media["a"]), "type": "video"}, {"path": str(media["b"]), "type": "video"},
             {"path": str(media["d"] / "no_es_video.png"), "type": "image"}]
    assert int(compose.spec_bitrate(items, 320, 240)) == int(1.2 * ra)
    # una fuente que no se puede medir: el de siempre (con el maximo sano)
    items.append({"path": str(media["d"] / "no_existe.mp4"), "type": "video"})
    assert compose.spec_bitrate(items, 320, 240) == compose._vbitrate(320, 240)


def test_montaje_recodificado_con_tope(media, monkeypatch):
    """Dos clips (hay que re-codificar): TODOS los ffmpeg del montaje usan el mismo bitrate con
    tope, y el video resultante no pesa mas que 1.2x la fuente mas pesada (+ margen del encoder)."""
    cmds = []
    real = compose._run
    monkeypatch.setattr(compose, "_run", lambda cmd: (cmds.append(cmd), real(cmd))[1])
    ra = compose.src_bitrate(media["a"])
    spec = {"fps": 30, "main": [
        {"path": str(media["a"]), "type": "video", "in": 0, "out": 1.5, "start": 0},
        {"path": str(media["b"]), "type": "video", "in": 0, "out": 1.0, "start": 1.5}]}
    info = {}
    out = media["d"] / "mont.mp4"
    compose.compose(spec, str(out), media["d"] / "w_mont", info=info)
    assert not info.get("copied") and info["bitrate"] == int(1.2 * ra)
    brs = [c[c.index("-b:v") + 1] for c in cmds if "-b:v" in c]
    assert brs and set(brs) == {str(int(1.2 * ra))}, brs
    v = next(s for s in _streams(out)["streams"] if s["codec_type"] == "video")
    assert int(v["bit_rate"]) <= 1.2 * ra * 1.25, (v["bit_rate"], ra)


def test_un_video_completo_se_copia_sin_recodificar(media, monkeypatch):
    cmds = []
    real = compose._run
    monkeypatch.setattr(compose, "_run", lambda cmd: (cmds.append(cmd), real(cmd))[1])
    src = media["a"]
    dur = compose.probe(src)["dur"]
    info, steps = {}, []
    out = media["d"] / "copia.mp4"
    r = compose.compose({"fps": 30, "main": [{"path": str(src), "type": "video", "in": 0,
                                              "out": dur, "start": 0, "name": "IMG_0001.MOV"}]},
                        str(out), media["d"] / "w_copia", on_step=steps.append, info=info)
    assert r == str(out) and info["copied"] is True
    assert len(cmds) == 1 and "-c:v" in cmds[0] and cmds[0][cmds[0].index("-c:v") + 1] == "copy"
    assert "-b:v" not in cmds[0]
    assert _video_md5(out) == _video_md5(src)                     # mismos paquetes: sin perdida
    st = _streams(out)["streams"]
    a = next(s for s in st if s["codec_type"] == "audio")
    assert (a["codec_name"], int(a["sample_rate"]), a["channels"]) == ("aac", 48000, 2)
    assert [s["codec_type"] for s in st] == ["video", "audio"]
    assert info["shots"] == [{"start": 0.0, "end": pytest.approx(dur, abs=0.06), "index": 0,
                              "kind": "clip", "src": "IMG_0001.MOV"}]
    assert steps[0].startswith("Preparando 0/1") and steps[-1].startswith("Preparando 1/1")
    assert not (media["d"] / "w_copia" / "copy.mp4").exists()     # temporal renombrado


def test_hevc_se_copia_con_la_etiqueta_de_apple(media, monkeypatch):
    info = {"codec": "hevc"}
    cmds = []
    monkeypatch.setattr(compose, "_run", lambda cmd: cmds.append(cmd))
    compose._copy_video(media["a"], media["d"] / "x.mp4", info)
    c = cmds[0]
    assert c[c.index("-tag:v") + 1] == "hvc1" and c[c.index("-c:v") + 1] == "copy"
    assert c[c.index("-map") + 1] == "0:v:0" and "0:a:0" in c


def _seq(item):
    return [("clip", item, item.get("trans"))]


def test_solo_se_copia_si_no_hay_ningun_procesado(media, monkeypatch):
    a = str(media["a"])
    dur = compose.probe(a)["dur"]
    base = {"path": a, "type": "video", "in": 0, "out": dur, "start": 0}
    W, H = 320, 240
    assert compose.copy_source(_seq(dict(base)), {}, W, H, 30) is not None
    assert compose.copy_source(_seq(dict(base, vol=1.0, mute=False, fadeIn=0, fadeOut=0)), {},
                               W, H, 30) is not None
    no = [dict(base, **{"in": 0.5}), dict(base, out=dur - 0.4), dict(base, start=1.0),
          dict(base, fadeIn=0.3), dict(base, fadeOut=0.3), dict(base, vol=0.5), dict(base, mute=True),
          dict(base, trans={"type": "crossfade", "dur": 0.4}), dict(base, type="image")]
    for it in no:
        assert compose.copy_source(_seq(it), {}, W, H, 30) is None, it
    assert compose.copy_source(_seq(dict(base)), {"overlays": [{"path": a}]}, W, H, 30) is None
    assert compose.copy_source(_seq(dict(base)), {}, 640, 480, 30) is None       # otro lienzo
    assert compose.copy_source(_seq(dict(base)), {}, W, H, 60) is None           # otros fps
    assert compose.copy_source(_seq(dict(base)) * 2, {}, W, H, 30) is None       # dos clips
    assert compose.copy_source([("black", 1.0, None)], {}, W, H, 30) is None
    f25 = str(media["fps25"])
    assert compose.copy_source(_seq(dict(base, path=f25, out=compose.probe(f25)["dur"])), {},
                               W, H, 30) is None
    m = str(media["mute"])
    assert compose.copy_source(_seq(dict(base, path=m, out=compose.probe(m)["dur"])), {},
                               W, H, 30) is None                                 # sin audio
    # propiedades de la fuente que obligan a re-codificar (probe simulado)
    real = video_io.probe(a)
    for k, v in (("hdr", "hlg"), ("dovi", True), ("rotation", 90), ("codec", "prores"),
                 ("codec", "vp9"), ("pix_fmt", "yuv420p10le"), ("pix_fmt", "yuvj420p"),
                 ("color_space", "smpte170m"), ("color_range", "pc")):
        fake = dict(real, **{k: v})
        monkeypatch.setattr(video_io, "probe", lambda p, _f=fake: dict(_f))
        assert compose.copy_source(_seq(dict(base)), {}, W, H, 30) is None, (k, v)
    monkeypatch.setattr(video_io, "probe", lambda p: dict(real, codec="hevc"))
    assert compose.copy_source(_seq(dict(base)), {}, W, H, 30) is not None       # HEVC SDR si


def test_capa_superior_obliga_a_recodificar(media):
    a = str(media["a"])
    dur = compose.probe(a)["dur"]
    info = {}
    out = media["d"] / "capa.mp4"
    compose.compose({"fps": 30, "main": [
        {"path": a, "type": "video", "in": 0, "out": dur, "start": 0},
        {"path": str(media["b"]), "type": "video", "in": 0, "out": 0.5, "start": 0.2, "layer": 1}]},
        str(out), media["d"] / "w_capa", info=info)
    assert not info.get("copied") and info["bitrate"] <= 1.2 * compose.src_bitrate(a) + 1
