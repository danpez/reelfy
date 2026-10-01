"""Montaje "Armar con IA" exacto por cuadro, de punta a punta: compose -> Timeline -> base -> render.

- El video combinado (uniones exactas) empieza en pts 0 con TODO su video en la reticula k/fps y
  el audio de cada toma en sincronia con su video: antes el concat lo dejaba en 0.021354 s (el
  relleno del AAC) y el render mostraba en el cuadro k el cuadro k-1 del combinado.
- plan.shots sin deriva (las duraciones redondeadas a ms se sumaban: +4 ms en un montaje real).
- Primer cuadro de una toma = timeline.shot_frame (round(t*fps)): un keep que empieza JUSTO en un
  cambio de toma (t redondeado a ms hacia abajo) no muestra el ultimo cuadro de la toma anterior.
- Un efecto que termina en el inicio de un corte (o un cuadro antes) cubre el ultimo cuadro del
  keep; uno que empieza en un cambio de toma cubre su primer cuadro (timeline.snap_window).

Material SINTETICO (lavfi), sin datos de clientes. Cada clip pinta su identidad en la luma:
franja superior negra (Y=16: la tapa un zoom), mitad izquierda = numero de toma, mitad derecha =
numero de cuadro de SU fuente (40 + 10 * (n mod 20)); su audio es silencio con un pitido de
30 ms en un cuadro conocido de la fuente.
"""
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest

import compose
import paths
from motion import render as R
from motion import schema as S
from motion import timeline as T
from motion import video_io as V
from motion.timeline import Timeline
from _render_plan import STUBS

FPS = 30
W, H = 180, 320
TESTS = str(Path(__file__).resolve().parent)
# (toma, primer cuadro de su fuente, cuadros): uniones en 34, 69 y 107 -> plan.shots 1.133 y
# 3.567 son tiempos a ms por DEBAJO y por ENCIMA de la reticula
SEGS = [(0, 10, 34), (1, 5, 35), (2, 20, 38), (3, 0, 31)]
CUM = [0, 34, 69, 107]
N_TOTAL = 138
BEEP_AT = 14                          # cuadro (desde el "in") del pitido de cada toma
CID_Y = [50, 90, 130, 170]


def _frame_y(n):
    return 40 + 10 * (n % 20)


def _in(in_f):
    """"in" de un clip en ms (como lo guarda la UI), a lo mas 1 ms ANTES del cuadro in_f: el
    -ss de compose va a ms y un "in" redondeado hacia ARRIBA de un cuadro lo pierde (el segmento
    repite su segundo cuadro; es otro asunto, de _norm_video)."""
    return math.floor(in_f / FPS * 1000) / 1000


def _clip(path, cid, beep_t, dur=3.0):
    lum = f"if(lt(Y,H/8),16,if(lt(X,W/2),{CID_Y[cid]},40+10*mod(N,20)))"
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={dur}",
                    "-f", "lavfi", "-i", f"aevalsrc='0.5*sin(2*PI*1000*t)*between(t,{beep_t:.6f},"
                                         f"{beep_t + 0.03:.6f})':s=48000:d={dur}",
                    "-vf", f"format=yuv420p,geq=lum='{lum}':cb=128:cr=128",
                    "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)], check=True)
    return path


def _ys(path):
    """Plano Y de TODOS los cuadros (orden de decodificacion), sin conversion de rango."""
    raw = subprocess.run([str(paths.FFMPEG), "-v", "error", "-i", str(path), "-map", "0:v:0",
                          "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                         capture_output=True, check=True).stdout
    fs = W * H * 3 // 2
    a = np.frombuffer(raw, np.uint8)[: len(raw) // fs * fs].reshape(-1, fs)
    return a[:, : W * H].reshape(-1, H, W).astype(np.float32)


def _ident(y):
    """(toma, valor del cuadro de su fuente) de un cuadro, por sus parches de luma."""
    left = y[int(0.4 * H):int(0.6 * H), int(0.1 * W):int(0.35 * W)].mean()
    right = y[int(0.4 * H):int(0.6 * H), int(0.65 * W):int(0.9 * W)].mean()
    cid = int(np.argmin([abs(left - v) for v in CID_Y]))
    return cid, float(right)


def _band(y):
    """Luma de la franja superior (Y=16 sin zoom; un zoom de 1.3 la saca del cuadro)."""
    return float(y[int(0.09 * H):int(0.115 * H), int(0.3 * W):int(0.7 * W)].mean())


def _expected(k):
    """(toma, valor) del cuadro k del video combinado."""
    j = max(i for i, c in enumerate(CUM) if c <= k)
    cid, in_f, _n = SEGS[j]
    return cid, _frame_y(in_f + k - CUM[j])


def _beeps(path, cands):
    """Inicio (s) del pitido de cada toma: primera muestra fuerte cerca de cada candidato."""
    a = V.read_audio(path, 48000, 1)[:, 0]
    out = []
    for t in cands:
        i0, i1 = int((t - 0.05) * 48000), int((t + 0.08) * 48000)
        hit = np.where(np.abs(a[i0:i1]) > 0.1)[0]
        out.append((i0 + int(hit[0])) / 48000 if len(hit) else None)
    return out


@pytest.fixture(scope="module")
def montage(tmp_path_factory):
    d = tmp_path_factory.mktemp("frame_exact")
    main = []
    for j, (cid, in_f, n) in enumerate(SEGS):
        p = _clip(d / f"c{cid}.mp4", cid, (in_f + BEEP_AT) / FPS)
        t_in = _in(in_f)
        main.append({"path": str(p), "type": "video", "in": t_in, "out": t_in + n / FPS,
                     "start": CUM[j] / FPS, "name": f"C{cid}.MOV", "layer": 0, "fadeIn": 0.0,
                     "fadeOut": 0.0, "mute": False, "vol": 1.0,
                     "montage": {"slot": f"s{j}", "cand": f"s{j}c1"}})
    info = {}
    out = Path(compose.compose({"fps": FPS, "main": main}, d / "montage.mp4", d / "w", info=info))
    return d, out, info


def _plan(out, info, cuts=(), fx=None):
    dur = V.probe(out)["duration"]
    return {"video": str(out), "duration": dur, "version": 2,
            "cuts": [{"start": a, "end": b, "enabled": True} for a, b in cuts],
            "shots": compose.plan_shots(info["shots"], dur), "phrases": [],
            "fx": fx or {"captions": {"enabled": False}, "grade": {"enabled": False},
                         "music": {"enabled": False, "source": "none"}}}


# ------------------------------------------------------------------ video combinado
def test_combinado_empieza_en_cero_y_cada_cuadro_esta_en_su_lugar(montage):
    d, out, info = montage
    si = V.stream_info(out)
    assert float(si["video"]["start_time"]) == 0.0 and float(si["audio"]["start_time"]) == 0.0
    pts = subprocess.run([str(paths.FFPROBE), "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True, check=True).stdout.split()
    pts = sorted(float(x) for x in pts)
    assert len(pts) == N_TOTAL
    assert max(abs(p - k / FPS) for k, p in enumerate(pts)) < 1e-4       # antes: +0.021354
    assert float(si["audio"]["duration"]) == pytest.approx(N_TOTAL / FPS, abs=1e-3)
    ys = _ys(out)
    assert len(ys) == N_TOTAL
    for k, y in enumerate(ys):
        cid, v = _ident(y)
        ecid, ev = _expected(k)
        assert cid == ecid and abs(v - ev) <= 4, (k, cid, v, ecid, ev)


def test_plan_shots_sin_deriva(montage):
    _d, _out, info = montage
    starts = [s["start"] for s in info["shots"]]
    assert starts == [round(c / FPS, 3) for c in CUM]          # 1.133, 2.3, 3.567: sin acumular
    ends = [s["end"] for s in info["shots"]]
    assert ends == [round(c / FPS, 3) for c in CUM[1:] + [N_TOTAL]]
    # el primer cuadro de cada toma es shot_frame de su inicio
    assert [T.shot_frame(t, FPS) for t in starts] == CUM


def test_audio_en_sincronia_con_el_video_de_cada_toma(montage):
    """El pitido de cada toma cae en el cuadro de su fuente donde se grabo (+-3 ms): antes todo
    el audio del combinado llegaba 21.3 ms tarde (relleno del AAC)."""
    _d, out, _info = montage
    exp = [c / FPS + (in_f + BEEP_AT) / FPS - _in(in_f) for c, (_cid, in_f, _n) in zip(CUM, SEGS)]
    got = _beeps(out, exp)
    for e, g in zip(exp, got):
        assert g is not None and abs(g - e) <= 0.003, (e, g)


def test_un_solo_segmento_tambien_empieza_en_cero(tmp_path):
    p = _clip(tmp_path / "solo.mp4", 2, 0.5)
    spec = {"fps": FPS, "main": [{"path": str(p), "type": "video", "in": 10 / FPS, "out": 40 / FPS,
                                  "start": 0.0, "name": "S.MOV", "layer": 0, "fadeIn": 0.0,
                                  "fadeOut": 0.0, "mute": False, "vol": 1.0,
                                  "montage": {"slot": "s0", "cand": "s0c1"}}]}
    out = compose.compose(spec, tmp_path / "solo_out.mp4", tmp_path / "w")
    si = V.stream_info(out)
    assert float(si["video"]["start_time"]) == 0.0 and float(si["audio"]["start_time"]) == 0.0
    ys = _ys(out)
    assert len(ys) == 30 and _ident(ys[0])[1] == pytest.approx(_frame_y(10), abs=4)
    assert _beeps(out, [(15 - 10) / FPS])[0] == pytest.approx(5 / FPS, abs=0.003)


# ------------------------------------------------------------------ keeps en cambios de toma
CUTS = [(0.75, 1.133),       # A: el keep siguiente empieza JUSTO en el cambio 1 (1.133 < 34/30)
        (2.3, 2.6),          # C: el keep anterior termina justo en el cambio 2
        (3.217, 3.507),      # B: el keep siguiente empezaria 2 cuadros antes del cambio 3 -> se pega
        (4.21, 4.4)]         # D: corte fuera de la reticula (el keep llega a 127/30)


def test_keeps_en_cambios_de_toma_son_exactos(montage):
    _d, out, info = montage
    tl = Timeline.from_plan(_plan(out, info, CUTS), fps=FPS)
    assert tl.frame_ranges() == [(0, 23), (34, 69), (78, 97), (107, 127), (132, 138)]
    assert tl.shot_change_frames(compose.plan_shots(info["shots"], 4.6)) == [23, 58, 77]


def test_base_pass_cuadro_a_cuadro_sin_cuadros_sueltos(montage, tmp_path):
    """El video base (lo que ve el render) muestra en cada cuadro final EXACTAMENTE el cuadro del
    combinado que dice el Timeline: sin corrimiento de un cuadro y sin cuadros de otra toma en
    las uniones."""
    _d, out, info = montage
    plan = _plan(out, info, CUTS)
    tl = Timeline.from_plan(plan, fps=FPS)
    res = V.base_pass(plan, tmp_path / "base.mp4", tmp_path / "voice.wav", W, H, FPS, tl,
                      enhance_audio=False)
    want = [k for a, b in tl.frame_ranges() for k in range(a, b)]
    ys = _ys(res["video"])
    assert len(ys) == len(want) == tl.n_frames()
    for j, (y, k) in enumerate(zip(ys, want)):
        cid, v = _ident(y)
        ecid, ev = _expected(k)
        assert cid == ecid and abs(v - ev) <= 4, (j, k, cid, v, ecid, ev)


# ------------------------------------------------------------------ efectos en uniones (render)
def _gradient():
    a = np.zeros((384, 216, 4), np.uint8)
    a[..., 0] = np.linspace(0, 255, 216)[None, :]
    a[..., 1] = np.linspace(0, 255, 384)[:, None]
    a[..., 2] = 90
    a[..., 3] = 255
    return a


def _comp(effects, cuts, shots=()):
    plan = S.normalize({"video": "x.mp4", "duration": 6.0, "cuts": list(cuts), "shots": list(shots),
                        "phrases": [], "version": 2,
                        "fx": {"captions": {"enabled": False}, "grade": {"enabled": False},
                               "effects": effects}})
    tl = Timeline.from_plan(plan, fps=FPS)
    o = R._resolve_opts({"W": 216, "H": 384, "fps": FPS, "modules": STUBS, "dither": 0.0})
    mods = R.load_modules(STUBS)
    job, _ = R.build_job(plan, tl, o, mods)
    comp = R.Compositor(job, mods)
    base = _gradient()
    return (lambda n: not np.array_equal(comp.frame(n, base)[..., :3], base[..., :3])), job, tl


def _zoom(start, end, fid="z"):
    return {"id": fid, "kind": "zoom", "start": start, "end": end, "from": 1.3, "to": 1.3}


CUT = [{"start": 2.93, "end": 3.5}]      # keep 0 llega a 88/30 (ultimo cuadro 87); el 2 empieza en 105


def test_zoom_que_termina_en_el_corte_cubre_el_ultimo_cuadro():
    for end in (2.93, 2.9, 2.91):        # en el corte, un cuadro antes, dentro del ultimo cuadro
        zoomed, job, tl = _comp([_zoom(1.0, end)], CUT)
        assert tl.frame_ranges()[0] == (0, 88)
        assert zoomed(86) and zoomed(87), end          # antes (2.9): el 87 salia sin zoom
        assert not zoomed(88), end                     # el primer cuadro tras el salto, sin zoom
        assert job["effects"][0]["_fe"] == pytest.approx(88 / FPS)


def test_zoom_que_empieza_en_el_keep_cubre_su_primer_cuadro():
    # empieza 10 ms despues del inicio del keep (3.5 -> cuadro final 88): cubre el 88
    zoomed, job, _ = _comp([_zoom(3.51, 4.5)], CUT)
    assert zoomed(88) and not zoomed(87)
    # y uno que se sale 20 ms al keep siguiente no deja un cuadro con zoom tras el salto
    # (3.52 = 0.6 cuadros dentro del keep 2: antes el cuadro 88 salia con zoom)
    zoomed, _, _ = _comp([_zoom(1.0, 3.52)], CUT)
    assert zoomed(87) and not zoomed(88)
    zoomed, _, _ = _comp([_zoom(1.0, 2.95)], CUT)               # 2.95 cae en el corte
    assert zoomed(87) and not zoomed(88)


def test_zoom_en_cambio_de_toma_con_tiempo_corrido():
    """Toma nueva en el cuadro original 120 (4.0 s) con su inicio corrido +4 ms (4.004): el zoom
    de la toma nueva cubre su primer cuadro y el de la anterior no se cuela en el."""
    shots = [{"start": 0.0, "end": 4.004}, {"start": 4.004, "end": 6.0}]
    f120 = 120 - 105 + 88                                        # cuadro final del original 120
    zoomed, _, tl = _comp([_zoom(4.004, 5.0)], CUT, shots)
    assert tl.shot_change_frames(shots) == [f120]
    assert zoomed(f120) and not zoomed(f120 - 1)
    zoomed, _, _ = _comp([_zoom(3.6, 4.004)], CUT, shots)
    assert zoomed(f120 - 1) and not zoomed(f120)


def test_limites_lejanos_no_se_mueven():
    # termina 3 cuadros antes del final del keep: no es un error de reticula, se respeta
    zoomed, job, _ = _comp([_zoom(1.0, 2.83)], CUT)
    assert zoomed(84) and not zoomed(85)
    assert job["effects"][0]["_fe"] == pytest.approx(2.83)


# ------------------------------------------------------------------ de punta a punta
def test_render_pro_montaje_exacto_con_zoom_hasta_el_salto(montage, tmp_path):
    """compose -> render_pro (modulos reales): cada cuadro del mp4 final es el cuadro del
    combinado que dice el Timeline, el zoom cubre el ultimo cuadro antes de cada salto y el
    pitido de cada toma suena en el cuadro donde se grabo."""
    _d, out, info = montage
    fx = {"captions": {"enabled": False}, "grade": {"enabled": False},
          "music": {"enabled": False, "source": "none"},
          "effects": [_zoom(0.2, 22 / FPS, "z0"),        # termina 1 cuadro antes del fin del keep 0
                      _zoom(3.9, 4.21, "z1")]}          # termina en el inicio (fuera de reticula) de D
    plan = _plan(out, info, CUTS, fx)
    tl = Timeline.from_plan(S.normalize(plan), fps=FPS)
    o = {"W": W, "H": H, "fps": FPS, "dither": 0.0, "cache_dir": str(tmp_path / "cache"),
         "enhance_audio": False, "workers": 1, "strict": True}
    final = tmp_path / "final.mp4"
    st = R.render_pro(plan, final, o)
    assert st["errors"] == {} and st["n_frames"] == tl.n_frames()
    want = [k for a, b in tl.frame_ranges() for k in range(a, b)]
    ys = _ys(final)
    assert len(ys) == len(want)
    for j, (y, k) in enumerate(zip(ys, want)):
        cid, v = _ident(y)
        ecid, ev = _expected(k)
        assert cid == ecid and abs(v - ev) <= 5, (j, k, cid, v, ecid, ev)
    # zoom: tapa la franja negra de arriba hasta el ULTIMO cuadro de su keep, y nada despues
    z0_last = 22                                         # keep 0 = cuadros finales 0..22
    z1_last = next(j for j, k in enumerate(want) if k == 126)
    for j in (z0_last - 1, z0_last, z1_last - 1, z1_last):
        assert _band(ys[j]) > 32, (j, _band(ys[j]))
    for j in (z0_last + 1, z1_last + 1, 3):
        assert _band(ys[j]) < 24, (j, _band(ys[j]))
    # A/V: el pitido de cada toma cae en el cuadro final que muestra su cuadro de la fuente
    exp = [want.index(c + BEEP_AT) / FPS for c in CUM]
    got = _beeps(final, exp)
    for e, g in zip(exp, got):
        assert g is not None and abs(g - e) <= 0.005, (e, g)
