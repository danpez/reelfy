"""compose.compose(info=...) devuelve los limites REALES de cada clip (plan.shots).

Material sintetico (lavfi, sin datos de clientes): clips de color de duraciones distintas,
sin transiciones (concat) y con crossfade (xfade: el limite queda a la mitad del fundido).
"""
import subprocess

import pytest

import compose
import paths

W, H, FPS = 360, 640, 30


def _clip(path, dur, color, tone):
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:r={FPS}:d={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("shots")
    specs = [(1.5, "red", 440), (1.0, "green", 660), (2.0, "blue", 550)]
    return d, [_clip(d / f"c{i}.mp4", du, c, t) for i, (du, c, t) in enumerate(specs)], \
        [s[0] for s in specs]


def _main(paths_, durs, names=True):
    out, t = [], 0.0
    for i, (p, du) in enumerate(zip(paths_, durs)):
        it = {"path": str(p), "type": "video", "in": 0.0, "out": du, "start": t}
        if names:
            it["name"] = f"CLIP_{i}.MOV"
        out.append(it)
        t += du
    return out


def test_limites_sin_transicion_coinciden_con_los_clips(clips):
    d, ps, durs = clips
    info = {}
    out = compose.compose({"fps": FPS, "main": _main(ps, durs)}, str(d / "out.mp4"), d / "w1",
                          info=info)
    shots = info["shots"]
    assert [s["src"] for s in shots] == ["CLIP_0.MOV", "CLIP_1.MOV", "CLIP_2.MOV"]
    assert [s["kind"] for s in shots] == ["clip"] * 3
    starts = [s["start"] for s in shots]
    assert starts[0] == 0.0
    assert abs(starts[1] - 1.5) < 0.05 and abs(starts[2] - 2.5) < 0.05
    for a, b in zip(shots, shots[1:]):
        assert a["end"] == b["start"]                     # contiguos, sin huecos
    total = compose.probe(out)["dur"]
    assert abs(shots[-1]["end"] - total) < 0.06


def test_limites_con_crossfade_quedan_a_la_mitad_del_fundido(clips):
    d, ps, durs = clips
    main = _main(ps, durs)
    main[1]["trans"] = {"type": "crossfade", "dur": 0.4}
    info = {}
    out = compose.compose({"fps": FPS, "main": main}, str(d / "out_x.mp4"), d / "w2", info=info)
    s = info["shots"]
    # xfade superpone 0.4 s en la union 1 y 0.04 s en la 2: el total se acorta
    assert abs(s[1]["start"] - (1.5 - 0.2)) < 0.06
    assert abs(s[2]["start"] - (1.5 + 1.0 - 0.4 - 0.02)) < 0.06
    total = compose.probe(out)["dur"]
    assert abs(s[-1]["end"] - total) < 0.08


def test_hueco_en_la_pista_principal_es_una_toma_negra(clips):
    d, ps, durs = clips
    main = _main(ps[:2], durs[:2])
    main[1]["start"] = 2.5                                 # 1 s de hueco tras el primer clip
    info = {}
    compose.compose({"fps": FPS, "main": main}, str(d / "out_gap.mp4"), d / "w3", info=info)
    kinds = [s["kind"] for s in info["shots"]]
    assert kinds == ["clip", "black", "clip"]
    assert info["shots"][1]["src"] == ""
    assert abs(info["shots"][2]["start"] - 2.5) < 0.06


def test_sin_info_devuelve_la_ruta_como_antes(clips):
    d, ps, durs = clips
    out = compose.compose({"fps": FPS, "main": _main(ps[:1], durs[:1], names=False)},
                          str(d / "solo.mp4"), d / "w4")
    assert out == str(d / "solo.mp4")


def test_lienzo_vertical_limitado_y_horizontal_intacto():
    """Un 4K vertical se compone a 1620x2880 (1.5x la salida 9:16, mismo aspecto); lo
    horizontal y lo que ya es chico no se tocan (el recorte 9:16 necesita su altura)."""
    assert compose.canvas_size(2160, 3840) == (1620, 2880)
    assert compose.canvas_size(1080, 1920) == (1080, 1920)
    assert compose.canvas_size(3840, 2160) == (3840, 2160)
    assert compose.canvas_size(1440, 1440) == (1440, 1440)
    w, h = compose.canvas_size(2208, 4000)                 # otro aspecto vertical: cabe y par
    assert w <= 1620 and h <= 2880 and w % 2 == 0 and h % 2 == 0
    assert abs(w / h - 2208 / 4000) < 0.01


def test_compose_en_paralelo_conserva_orden_y_progreso(clips, monkeypatch):
    """Con 2 normalizaciones a la vez los segmentos se unen en el orden de la linea de
    tiempo y el progreso reporta k/n clips listos (el servidor lo mapea a 2-32 %)."""
    d, paths_, durs = clips
    monkeypatch.setattr(compose, "COMPOSE_JOBS", 2)
    msgs, info = [], {}
    out = d / "par.mp4"
    compose.compose({"fps": FPS, "main": _main(paths_, durs)}, str(out), d / "wpar",
                    on_step=msgs.append, info=info)
    prep = [m for m in msgs if m.startswith("Preparando")]
    assert prep[0] == "Preparando 0/3…" and prep[-1] == "Preparando 3/3…" and len(prep) == 4
    assert (info["W"], info["H"]) == (W, H)
    starts = [s["start"] for s in info["shots"]]
    assert starts == pytest.approx([0.0, 1.5, 2.5], abs=0.05)
    assert [s["src"] for s in info["shots"]] == ["CLIP_0.MOV", "CLIP_1.MOV", "CLIP_2.MOV"]
    # el color de cada tramo es el del clip en ese orden (rojo, verde, azul)
    import numpy as np
    for t, ch in ((0.7, 0), (2.0, 1), (3.5, 2)):
        raw = subprocess.run([str(paths.FFMPEG), "-v", "error", "-ss", str(t), "-i", str(out),
                              "-frames:v", "1", "-vf", "scale=16:16,format=rgb24", "-f", "rawvideo",
                              "pipe:1"], capture_output=True, check=True).stdout
        px = np.frombuffer(raw, np.uint8).reshape(16, 16, 3).mean(axis=(0, 1))
        assert int(np.argmax(px)) == ch, (t, px)


def test_compose_cancelado_a_media_normalizacion(clips, monkeypatch):
    """Si on_step lanza (cancelacion del trabajo) la excepcion sale de compose."""
    d, paths_, durs = clips
    monkeypatch.setattr(compose, "COMPOSE_JOBS", 2)

    class Cancel(Exception):
        pass

    def boom(m):
        if m.startswith("Preparando 1/"):
            raise Cancel()
    with pytest.raises(Cancel):
        compose.compose({"fps": FPS, "main": _main(paths_, durs)}, str(d / "c.mp4"), d / "wc",
                        on_step=boom)
