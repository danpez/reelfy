"""Muchos cortes (video largo): el select de cortes ya no revienta con cientos de keeps.

av_expr (el evaluador de expresiones de ffmpeg) no parsea una suma de mas de ~100 terminos: un
video real de 19 min con 211 cortes tiraba el export ("Error while parsing expression ... Cannot
allocate memory"). Ahora video_io.select_filters / select_chain reparten los keeps en varios select
ENCADENADOS por tramos de tiempo y sum_expr agrupa las sumas largas (zoom por beats, vfollow).

Todo con video SINTETICO donde cada cuadro lleva su propio indice en 16 franjas horizontales (bit
a bit, el mas significativo arriba): la salida debe tener EXACTAMENTE los cuadros esperados, en
orden. Cubre el base pass del motor (orden 'pre' y 'post' del reencuadre) y los caminos legados
(edit.tighten, pipeline.finish_short y pipeline.reframe_and_burn con select + aselect)."""
import re
import subprocess

import numpy as np
import pytest

import paths
from motion import video_io as V
from motion.timeline import Timeline

WORK = paths.WORK / "tests_many_cuts"
FPS = 30
BITS = 16


@pytest.fixture(scope="module")
def work():
    WORK.mkdir(parents=True, exist_ok=True)
    yield WORK


# ------------------------------------------------------------------ utilidades
def _index_video(path, n, w, h, audio: str | None = None):
    """n cuadros a 30 fps; el cuadro k lleva k en 16 franjas (blanco = 1). Las franjas sobreviven
    recortes horizontales y escalas. `audio`: expresion de aevalsrc (pcm en .mov)."""
    if path.exists():
        return path
    assert h % BITS == 0
    tmp = path.with_name(f"{path.stem}.part{path.suffix}")         # nunca dejar una fuente a medias
    cmd = [V.FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", f"{w}x{h}", "-r", str(FPS), "-i", "pipe:0"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"aevalsrc='{audio}':s=48000:d={n / FPS:.6f}",
                "-c:a", "pcm_s16le"]
    cmd += ["-map", "0:v", *(["-map", "1:a"] if audio else []), "-frames:v", str(n),
            "-c:v", "libx264", "-preset", "ultrafast", "-qp", "2", "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-g", str(FPS), str(tmp)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    weights = 1 << np.arange(BITS - 1, -1, -1)
    for k0 in range(0, n, 256):
        ks = np.arange(k0, min(n, k0 + 256))
        bits = ((ks[:, None] // weights[None, :]) % 2).astype(np.uint8) * 255   # (m, 16)
        rows = np.repeat(bits, h // BITS, axis=1)                               # (m, h)
        p.stdin.write(np.ascontiguousarray(np.repeat(rows[:, :, None], w, axis=2)).tobytes())
    p.stdin.close()
    assert p.wait() == 0
    tmp.replace(path)
    return path


def _decode(path_or_cmd) -> np.ndarray:
    """Indices de cada cuadro de un video (o de la salida rawvideo de un comando ffmpeg que ya
    termina en el mismo filtro de lectura)."""
    if isinstance(path_or_cmd, list):
        raw = subprocess.run(path_or_cmd, capture_output=True, check=True).stdout
    else:
        raw = subprocess.run([V.FFMPEG, "-v", "error", "-i", str(path_or_cmd), "-vf", _READ,
                              "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"],
                             capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, BITS, 8).mean(axis=2)
    return ((a > 128).astype(np.int64) * (1 << np.arange(BITS - 1, -1, -1))).sum(axis=1)


# columna central (el reencuadre puede dejar fondo desenfocado a los lados) -> 8x16 promediado
_READ = "crop=iw/3:ih:iw/3:0,scale=8:16:flags=area,format=gray"


def _ranges(n_keeps: int, seed: int, adjacent: bool = True, tail: int = 0):
    """Rangos [a, b) de cuadros: el primero en el cuadro 0, keeps de 1 cuadro, algunos pegados
    al anterior (si `adjacent`) y el ultimo termina en el ultimo cuadro de la fuente (o `tail`
    cuadros despues: cola clonada). Devuelve (rangos, cuadros de la fuente)."""
    rng = np.random.default_rng(seed)
    out, k = [], 0
    for i in range(n_keeps):
        gap = 0 if i == 0 else (0 if adjacent and i % 7 == 3 else int(rng.integers(1, 5)))
        a = k + gap
        ln = 1 if i % 5 == 0 else int(rng.integers(1, 6))
        out.append((a, a + ln))
        k = a + ln
    return out, k - tail


def _expected(ranges, n_src):
    return np.array([min(k, n_src - 1) for a, b in ranges for k in range(a, b)])


def _simulate(filters: list[str]):
    """Evalua en Python la cadena de select (between/gte/lt/if) para un t: el cuadro sobrevive
    si todos los filtros dan != 0. Sirve para comprobar la particion en tramos sin ffmpeg."""
    progs = []
    for f in filters:
        body = f.split("=", 1)[1].strip("'")
        m = re.fullmatch(r"if\((.*?),(between.*),1\)", body)
        cond, terms = (m.group(1), m.group(2)) if m else ("", body)
        conds = [(op, float(x)) for op, x in re.findall(r"(gte|lt)\(t,([-\d.]+)\)", cond)]
        ivs = [(float(a), float(b)) for a, b in re.findall(r"between\(t,([-\d.]+),([-\d.]+)\)", terms)]
        progs.append((conds, ivs))

    def keep(t):
        for conds, ivs in progs:
            inside = all(t >= x if op == "gte" else t < x for op, x in conds)
            if inside and not any(a <= t <= b for a, b in ivs):
                return False
        return True
    return keep


def _cut_cmd(src, filters):
    """Mismo arranque del base pass (reticula t0=0 + cola clonada) + cortes + lectura rawvideo."""
    graph = ",".join([f"fps=fps={FPS}:start_time=0:round=near",
                      "tpad=stop_mode=clone:stop_duration=1.000", *filters,
                      f"setpts=N/({FPS}*TB)", _READ])
    return [V.FFMPEG, "-v", "error", "-i", str(src), "-vf", graph,
            "-fps_mode", "passthrough", "-an", "-f", "rawvideo", "pipe:1"]


def _ff_ok(vf: str) -> bool:
    r = subprocess.run([V.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=64x64:r=30:d=1", "-vf", vf, "-f", "null", "-"],
                       capture_output=True, text=True)
    return r.returncode == 0


# ------------------------------------------------------------------ helper puro
def test_pocos_keeps_un_solo_select_como_antes():
    fr = [(0, 3), (10, 11), (20, 40), (41, 42), (100, 130)]
    fl = V.select_filters(fr, FPS)
    assert len(fl) == 1 and "if(" not in fl[0] and fl[0].count("between(t,") == 5
    # legado (segundos, 3 decimales): identico a la expresion de siempre
    keeps = [(0.0, 1.234), (2.5, 3.0004), (7.12345, 9.9)]
    old = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)
    assert V.select_chain(keeps, "aselect", digits=3) == [f"aselect='{old}'"]
    assert V.select_chain([], "select") == ["select=0"]


@pytest.mark.parametrize("n_keeps", [150, 400])
def test_tramos_parten_toda_la_linea(n_keeps):
    fr, n_src = _ranges(n_keeps, seed=n_keeps)
    fl = V.select_filters(fr, FPS)
    merged = len(V._merge_closed([((a - .5) / FPS, (b - .5) / FPS) for a, b in fr], 6))
    assert len(fl) == -(-merged // V.SELECT_MAX_TERMS) > 1
    assert all(f.count("between(t,") <= V.SELECT_MAX_TERMS for f in fl)
    assert "gte(" not in fl[0] and "lt(" not in fl[-1]              # -inf ... +inf
    # la cadena conserva EXACTAMENTE los cuadros de los keeps (incluida la cola clonada: fuera)
    keep = _simulate(fl)
    want = {k for a, b in fr for k in range(a, b)}
    assert {k for k in range(-5, n_src + 40) if keep(k / FPS)} == want


def test_sum_expr_plano_si_es_corto_y_arbol_si_es_largo():
    t = [f"between(t,{i},{i + 0.5})" for i in range(200)]
    assert V.sum_expr(t[:V.SUM_LEAF]) == "+".join(t[:V.SUM_LEAF])
    assert V.sum_expr(["7"]) == "7"
    big = V.sum_expr(t * 25)                                           # 5000 terminos
    assert big.count("between(") == 5000
    assert not _ff_ok(f"scale=w='ceil(64*(1+0.1*({'+'.join(t[:101])}))/2)*2':h=64:eval=frame")
    assert _ff_ok(f"scale=w='ceil(64*(1+0.1*({big}))/2)*2':h=64:eval=frame")


def test_vfollow_cientos_de_keeps_expresion_valida(monkeypatch):
    """Vertical -> 1:1 sigue la cara con un encuadre por keep: con 300 keeps (cara alternando
    arriba/abajo) la y del crop es una suma de 300 terminos: ffmpeg la acepta (sum_expr) y en el
    primer cuadro de cada keep vale el encuadre de ese keep."""
    keeps = [(float(i), i + 0.2) for i in range(300)]
    tl = Timeline(keeps, 300.0, fps=FPS)
    samples = [[a + 0.1, 0.3 if i % 2 else 0.6, 0.1] for i, (a, b) in enumerate(keeps)]
    monkeypatch.setattr(V, "face_samples", lambda *a, **k: samples)
    f, meta = V.reframe_filters({"w": 1080, "h": 1920}, 360, 360, {"video": "x.mp4"}, timeline=tl)
    assert meta["mode"] == "vfollow" and len(set(meta["y"])) == 2
    yexpr = re.fullmatch(r"crop=360:360:0:'(.*)'", f[1]).group(1)
    assert yexpr.count("gte(n,") == 299
    starts = np.cumsum([0] + [b - a for a, b in tl.frame_ranges(FPS)])[:-1]
    for i, st in enumerate(starts):
        assert eval(yexpr, {"gte": lambda n, x: float(n >= x), "n": int(st)}) == meta["y"][i]
    flat = "+".join(f"(2)*gte(n,{i})" for i in range(101))           # como antes: plana
    assert not _ff_ok(f"{f[0]},crop=360:360:0:'{flat}'")
    assert _ff_ok(f"{f[0]},{f[1]}")


# ------------------------------------------------------------------ ffmpeg real, cuadro exacto
@pytest.mark.parametrize("n_keeps", [5, 150, 400])
def test_select_encadenado_cuadro_exacto(work, n_keeps):
    """Keeps de 1 cuadro, pegados, al inicio y al final (400: el ultimo entra a la cola clonada).
    Sin -frames:v: la propia cadena descarta lo previo al primer keep y toda la cola."""
    fr, n_src = _ranges(n_keeps, seed=7 + n_keeps, tail=1 if n_keeps == 400 else 0)
    src = _index_video(work / f"idx_{n_keeps}_{n_src}.mp4", n_src, 176, 96)
    fl = V.select_filters(fr, FPS)
    assert (len(fl) == 1) == (n_keeps <= V.SELECT_MAX_TERMS)
    got = _decode(_cut_cmd(src, fl))
    want = _expected(fr, n_src)
    assert len(got) == len(want)
    np.testing.assert_array_equal(got, want)
    if n_keeps > 100:                         # la expresion unica de antes ni siquiera parsea
        old = "+".join(f"between(n,{a},{b - 1})" for a, b in fr)
        assert subprocess.run(_cut_cmd(src, [f"select='{old}'"]), capture_output=True).returncode != 0


def test_sum_expr_en_select_cuadro_exacto(work):
    fr, n_src = _ranges(300, seed=3)
    src = _index_video(work / f"idx_sum_{n_src}.mp4", n_src, 176, 96)
    expr = V.sum_expr(f"between(t,{(a - .5) / FPS:.6f},{(b - .5) / FPS:.6f})" for a, b in fr)
    np.testing.assert_array_equal(_decode(_cut_cmd(src, [f"select='{expr}'"])),
                                  _expected(fr, n_src))


def test_mismo_resultado_que_el_select_por_indice(work):
    """Con pocos keeps el select por t da EXACTAMENTE los cuadros del select por n de antes,
    tambien con un video que arranca 0.1 s despues del audio (el filtro fps rellena la
    reticula desde t=0, como en un MOV de iPhone con desfase)."""
    fr, n_src = _ranges(50, seed=11)
    src = work / f"idx_offset_{n_src}.mp4"
    if not src.exists():
        base = _index_video(work / f"idx_50_{n_src}.mp4", n_src, 176, 96)
        subprocess.run([V.FFMPEG, "-y", "-v", "error", "-itsoffset", "0.1", "-i", str(base),
                        "-f", "lavfi", "-t", f"{n_src / FPS + 0.1:.3f}", "-i",
                        "anullsrc=r=48000:cl=mono", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                        "-c:a", "aac", str(src)], check=True)
    assert abs(float(V.stream_info(src)["video"].get("start_time") or 0) - 0.1) < 0.01
    old = "+".join(f"between(n,{a},{b - 1})" for a, b in fr)
    a = _decode(_cut_cmd(src, [f"select='{old}'"]))
    b = _decode(_cut_cmd(src, V.select_filters(fr, FPS)))
    assert len(a) == sum(y - x for x, y in fr)
    np.testing.assert_array_equal(a, b)


# ------------------------------------------------------------------ base pass del motor
@pytest.mark.parametrize("orient", ["horizontal", "vertical"])
def test_base_pass_400_keeps_cuadro_exacto(work, orient):
    """Base pass con 400 keeps: horizontal -> 9:16 (reencuadre ANTES de los cortes, como el
    video real de 4K) y vertical -> 9:16 (despues). Cuadros exactos, voz de la misma duracion."""
    fr, n_src = _ranges(400, seed=21, adjacent=False)       # Timeline funde keeps pegados
    w, h = (320, 176) if orient == "horizontal" else (176, 320)
    src = _index_video(work / f"idx_{orient}_{n_src}.mp4", n_src, w, h)
    tl = Timeline([(a / FPS, b / FPS) for a, b in fr], n_src / FPS, fps=FPS)
    assert tl.frame_ranges(FPS) == fr
    plan = {"video": str(src), "duration": n_src / FPS}
    cache = work / f"cache_{orient}"
    bv, bw = V.base_paths(cache, plan, 176, 320, FPS, tl, enhance_audio=False)
    for p in (bv, bw):
        p.unlink(missing_ok=True)
    r = V.base_pass(plan, bv, bw, 176, 320, FPS, tl, enhance_audio=False)
    assert r["mode"] == ("center" if orient == "horizontal" else "cover")
    want = _expected(fr, n_src)
    assert r["n_frames"] == len(want) == V.count_frames(bv)
    np.testing.assert_array_equal(_decode(bv), want)
    import soundfile as sf
    assert sf.info(str(bw)).frames == int(round(len(want) / FPS * V.SR))


# ------------------------------------------------------------------ caminos legados (select + aselect)
BURST, PERIOD, N_BURSTS = 0.35, 0.85, 160


@pytest.fixture(scope="module")
def bursts(work):
    """160 rafagas de tono separadas por 0.5 s de silencio -> ~160 keeps de silencedetect.
    Video de indices 320x176 + audio pcm (marcas de tiempo deterministas)."""
    dur = N_BURSTS * PERIOD - (PERIOD - BURST)
    n = int(round(dur * FPS))
    src = _index_video(work / f"bursts_{n}.mov", n, 320, 176,
                       audio=f"0.4*sin(2*PI*440*t)*lt(mod(t,{PERIOD}),{BURST})")
    return src


def _pts(src, sel: str, entries: str):
    """(time_base, filas) de ffprobe: t = pts * time_base en doble precision, como ffmpeg."""
    r = subprocess.run([V.FFPROBE, "-v", "error", "-select_streams", sel, "-show_entries",
                        f"stream=time_base:frame={entries}", "-of", "csv=p=0", str(src)],
                       capture_output=True, text=True, check=True).stdout.split()
    num, den = (int(x) for x in r[0].split("/")) if "/" in r[0] else (int(x) for x in r[-1].split("/"))
    rows = [[int(v) for v in line.split(",") if v] for line in r if "/" not in line]
    return num / den, rows


def _legacy_expect(src, keeps, video_t=None):
    """Cuadros de video y muestras de audio que conserva between(t,a,b) (3 decimales) sobre los
    tiempos reales de la fuente. video_t: tiempos propios del video (p. ej. tras un fps=30)."""
    kk = [(float(f"{a:.3f}"), float(f"{b:.3f}")) for a, b in keeps]
    inside = lambda t: any(a <= t <= b for a, b in kk)              # noqa: E731
    if video_t is None:
        tb, rows = _pts(src, "v:0", "pts")
        video_t = [float(r[0]) * tb for r in rows]
    tb_a, arows = _pts(src, "a:0", "pts,nb_samples")
    frames = [k for k, t in enumerate(video_t) if inside(t)]
    samples = sum(r[1] for r in arows if inside(float(r[0]) * tb_a))
    return np.array(frames), samples


def _audio_samples(path) -> int:
    raw = subprocess.run([V.FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
                          "-ar", "48000", "-f", "s16le", "pipe:1"], capture_output=True,
                         check=True).stdout
    return len(raw) // 2


def _check_legacy(out, src, keeps, video_t=None, identity=True):
    frames, samples = _legacy_expect(src, keeps, video_t)
    assert V.count_frames(out) == len(frames)
    if identity:
        np.testing.assert_array_equal(_decode(out), frames)
    # aselect decide por BLOQUE de audio (t = inicio del bloque de 1024 muestras): la duracion
    # correcta es la de los bloques que caen dentro de los keeps, como con un solo aselect
    got = _audio_samples(out)
    assert abs(got - samples) <= 1024, (got, samples)             # AAC: a lo mas un paquete


def test_legado_tighten_160_keeps(work, bursts):
    import edit
    keeps, _ = edit.keep_segments(bursts)
    assert len(keeps) >= 150
    out = work / "tighten.mp4"
    edit.tighten(bursts, out)
    _check_legacy(out, bursts, keeps)


def test_legado_finish_short_160_keeps(work, bursts):
    import edit
    import pipeline
    keeps, _ = edit.keep_segments(bursts)
    out = work / "finish.mp4"
    pipeline.finish_short(bursts, out, dynamic=True, hook_title=None, w=320, h=176)
    _check_legacy(out, bursts, keeps)


def test_legado_reframe_and_burn_160_keeps_y_beats(work, bursts):
    """Export legado completo: 160 keeps (select + aselect) y 160 beats de zoom (sum_expr)."""
    import edit
    import pipeline
    keeps, _ = edit.keep_segments(bursts)
    beats = [i * PERIOD + 0.1 for i in range(N_BURSTS)]
    ass = work / "vacio.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 176\nPlayResY: 320\n\n"
                   "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Arial,20\n\n"
                   "[Events]\nFormat: Layer, Start, End, Style, Text\n")
    out = work / "reframe.mp4"
    pipeline.reframe_and_burn(bursts, ass, out, fps=FPS, track=False, beats=beats,
                              enhance_audio=False, out_w=176, out_h=320, keeps=keeps)
    n_src = V.count_frames(bursts)
    # tras fps=30 la reticula es t = k * (1/30); el zoom de los beats mueve las franjas: solo
    # se comprueba el numero de cuadros y el audio
    _check_legacy(out, bursts, keeps, video_t=[k * (1 / FPS) for k in range(n_src)], identity=False)
