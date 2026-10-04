"""compose.py con un MONTAJE CON IA ("Armar con IA"): los clips principales traen "montage".

- Nivel de la voz por segmento (speech_lufs / level_gains / vol "auto"): todas las tomas suenan
  igual de fuerte; los `vol` numericos se respetan; una toma que se sube pasa por un limitador.
- Micro-fundido SOLO de audio (10-20 ms) a cada lado de cada union; el video no se funde.
- Lienzo con la orientacion de la MAYORIA de los clips (nunca la del primero).
- info.shots / plan.shots con join, slot, cand, setup; plan.montage mapeado a plan.shots; cortes
  de tomas que cruzarian una union fuera; secciones de plan.takes desde el guion.
- Sin montaje (un video o ensamble manual): exactamente lo de siempre.

Material SINTETICO (lavfi: color + tono), sin datos de clientes.
"""
import copy
import subprocess

import numpy as np
import pytest

import compose
import paths

FPS = 30


def _clip(path, dur, w, h, color="white", freq=440, amp=1.0):
    """Clip de color con un tono (la 'voz'): `amp` escala el tono de lavfi (1/8 de fondo)."""
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={FPS}:d={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={dur}",
                    "-af", f"volume={amp}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)], check=True)
    return str(path)


def _item(path, t_in, t_out, start, name, montage=None, **kw):
    it = {"path": path, "type": "video", "in": t_in, "out": t_out, "start": start, "name": name,
          "layer": 0, "fadeIn": 0.0, "fadeOut": 0.0, "vol": 1.0, "mute": False}
    if montage is not None:
        it["montage"] = montage
        it.pop("vol")
    it.update(kw)
    return it


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("joins")
    return {
        "d": d,
        # vertical, voz fuerte y voz floja (-14 dB: la toma sin microfono)
        "va": _clip(d / "va.mp4", 2.0, 180, 320, "white", 440, 4.0),
        "vb": _clip(d / "vb.mp4", 2.0, 180, 320, "red", 550, 0.8),
        "vc": _clip(d / "vc.mp4", 2.0, 180, 320, "blue", 660, 4.0),
        # horizontal
        "h": _clip(d / "h.mp4", 2.0, 320, 180, "green", 330, 4.0),
    }


def _gray_at(path, t, w, h):
    raw = subprocess.run([str(paths.FFMPEG), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return float(np.frombuffer(raw, dtype=np.uint8).mean())


def _rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-12)


# ------------------------------------------------------------------ nivel de la voz
def test_speech_lufs_sigue_la_ganancia_y_solo_cuenta_las_palabras():
    sr = 48000
    t = np.arange(int(2.0 * sr)) / sr
    tone = 0.3 * np.sin(2 * np.pi * 440 * t)
    a = compose.speech_lufs(None, audio=tone)
    b = compose.speech_lufs(None, audio=tone * 0.1)                  # -20 dB
    assert a is not None and b is not None
    assert a - b == pytest.approx(20.0, abs=0.3)
    # voz en [0, 1) s y un golpe de ruido 12 dB mas fuerte en [1, 2): con las palabras solo cuenta
    # la voz
    x = tone.copy()
    x[sr:] = np.random.default_rng(1).normal(0, 1.2, sr)
    words = [{"start": 0.05, "end": 0.45}, {"start": 0.5, "end": 0.95}]
    only = compose.speech_lufs(None, words=words, audio=x)
    assert only == pytest.approx(a, abs=0.8)
    assert compose.speech_lufs(None, audio=x) > a + 3.0              # sin palabras: el ruido pesa
    # palabras en tiempos de la FUENTE: el tramo arranca en t_in
    shifted = [{"start": w["start"] + 10.0, "end": w["end"] + 10.0} for w in words]
    assert compose.speech_lufs(None, 10.0, 12.0, words=shifted, audio=x) == pytest.approx(only, abs=1e-6)
    assert compose.speech_lufs(None, audio=np.zeros(sr)) is None       # silencio: sin voz
    assert compose.speech_lufs(None, audio=np.zeros(0)) is None


def test_level_gains_mediana_topes_y_sin_medida():
    g = compose.level_gains([-20.0, -26.0, -14.0, None])
    assert g[0] == pytest.approx(1.0)
    assert 20 * np.log10(g[1]) == pytest.approx(6.0, abs=0.01)
    assert 20 * np.log10(g[2]) == pytest.approx(-6.0, abs=0.01)
    assert g[3] == 1.0
    up = compose.level_gains([-20.0, -60.0], target=-20.0)
    assert 20 * np.log10(up[1]) == pytest.approx(compose.LEVEL_UP_DB, abs=0.01)
    down = compose.level_gains([-20.0, 10.0], target=-20.0)
    assert 20 * np.log10(down[1]) == pytest.approx(-compose.LEVEL_DOWN_DB, abs=0.01)
    assert compose.level_gains([None, None]) == [1.0, 1.0]
    assert compose.level_gains([]) == []


def test_auto_levels_respeta_vol_numerico_y_mudo(media):
    items = [_item(media["va"], 0.2, 1.8, 0.0, "A", {"slot": "s1", "cand": "c1"}),
             _item(media["vb"], 0.2, 1.8, 1.6, "B", {"slot": "s2", "cand": "c1"}),
             _item(media["va"], 0.2, 1.8, 4.8, "D", {"slot": "s4", "cand": "c1"}, mute=True),
             _item(media["vc"], 0.2, 1.8, 6.4, "E")]                  # manual: vol 1.0 fijo
    res = compose.auto_levels(items)
    assert [k for k, _l, _g in res] == [0, 1]
    assert items[2]["vol"] == 1.0 and items[3]["vol"] == 1.0
    la, lb = res[0][1], res[1][1]
    assert la - lb == pytest.approx(14.0, abs=1.0)
    # sin tramos del montaje ya nivelados: la mediana de dos queda a la mitad (A baja ~7 dB, B sube ~7)
    assert 20 * np.log10(items[0]["vol"]) == pytest.approx(-(la - lb) / 2, abs=0.2)
    assert 20 * np.log10(items[1]["vol"]) == pytest.approx((la - lb) / 2, abs=0.2)
    assert items[0]["_lufs"] == la


def test_auto_levels_una_toma_cambiada_suena_como_sus_vecinas(media):
    """Regresion: "Usar esta toma" con un clip que no estaba en el video llega con vol "auto"; se
    nivelaba contra SI MISMA (quedaba a 1.0: la toma sin microfono ~14 dB bajo las demas). Ahora el
    objetivo es la sonoridad de los tramos del montaje ya nivelados (con su ganancia)."""
    items = [_item(media["va"], 0.2, 1.8, 0.0, "A", {"slot": "s1", "cand": "c1"}, vol=1.0),
             _item(media["vb"], 0.2, 1.8, 1.6, "X", {"slot": "s2", "cand": "c3"}, vol="auto"),
             _item(media["vc"], 0.2, 1.8, 3.2, "C", {"slot": "s3", "cand": "c1"}, vol=0.8)]
    res = compose.auto_levels(items)
    assert [k for k, _l, _g in res] == [1]
    assert items[0]["vol"] == 1.0 and items[2]["vol"] == 0.8          # los numericos se respetan
    lv = {k: compose._item_lufs(items[k]) for k in (0, 1, 2)}
    eff = [lv[0], lv[2] + 20 * np.log10(0.8)]
    x = lv[1] + 20 * np.log10(items[1]["vol"])
    assert x == pytest.approx(float(np.median(eff)), abs=0.3)
    assert 20 * np.log10(items[1]["vol"]) > 10.0                        # la toma floja sube
    # sin medida posible de las referencias (sin audio): se nivela como antes (queda a 1.0 sola)
    solo = [_item(media["vb"], 0.2, 1.8, 0.0, "X", {"slot": "s2", "cand": "c3"}, vol="auto")]
    compose.auto_levels(solo)
    assert solo[0]["vol"] == pytest.approx(1.0)


# ------------------------------------------------------------------ filtros de audio
def test_filtros_sin_montaje_identicos_a_los_de_siempre():
    assert compose._audio_filters(2.0, 1.0) == ["volume=1.000", "aresample=48000"]
    assert compose._audio_filters(2.0, 0.5, 0.3, 0.4) == [
        "volume=0.500", "aresample=48000", "afade=t=in:st=0:d=0.300", "afade=t=out:st=1.600:d=0.400"]
    mf = compose._audio_filters(2.0, 3.0, 0.0, 0.0, 0.015, 0.015, limit=True)
    assert mf == ["volume=3.000", "aresample=48000", f"alimiter=limit={compose.LIMIT}:level=0:latency=1",
                  "afade=t=in:st=0:d=0.015:curve=qsin", "afade=t=out:st=1.985:d=0.015:curve=qsin"]
    # un fundido del clip (fadeIn/fadeOut) tapa el micro-fundido de ese lado
    both = compose._audio_filters(2.0, 1.0, 0.5, 0.0, 0.015, 0.015)
    assert "afade=t=in:st=0:d=0.500" in both and "afade=t=in:st=0:d=0.015:curve=qsin" not in both
    assert 0.01 <= compose.MICRO_FADE <= 0.02


def _capture(monkeypatch):
    cmds = []
    real = compose._run

    def spy(cmd):
        cmds.append([str(c) for c in cmd])
        return real(cmd)
    monkeypatch.setattr(compose, "_run", spy)
    return cmds


def _af_of(cmds, src):
    """-af del segmento normalizado de `src` (en orden de los comandos)."""
    out = []
    for c in cmds:
        if "-af" in c and src in c:
            out.append(c[c.index("-af") + 1])
    return out


def test_micro_fundido_solo_de_audio_en_cada_union(media, monkeypatch):
    d = media["d"]
    cmds = _capture(monkeypatch)
    main = [_item(media["va"], 0.0, 1.5, 0.0, "A", {"slot": "s1", "cand": "c1", "join": None}, vol=1.0),
            _item(media["vc"], 0.0, 1.5, 1.5, "C", {"slot": "s2", "cand": "c1", "join": "line"}, vol=1.0)]
    info = {}
    out = compose.compose({"fps": FPS, "main": main}, str(d / "mf.mp4"), d / "w_mf", info=info)
    afa, afc = _af_of(cmds, media["va"])[0], _af_of(cmds, media["vc"])[0]
    # A: solo sale con micro-fundido (es el primero); C: solo entra (es el ultimo)
    assert "afade=t=out" in afa and "afade=t=in" not in afa
    assert "afade=t=in" in afc and "afade=t=out" not in afc
    vfs = [c[c.index("-vf") + 1] for c in cmds if "-vf" in c and (media["va"] in c or media["vc"] in c)]
    assert vfs and all("fade=" not in v for v in vfs)                 # el video NO se funde
    j = info["shots"][1]["start"]
    x = compose._pcm(out)
    sr = 48000
    steady = _rms_db(x[int(0.4 * sr):int(1.0 * sr)])
    win = int(0.004 * sr)
    near = [_rms_db(x[k:k + win]) for k in range(int((j - 0.05) * sr), int((j + 0.08) * sr), win // 2)]
    assert min(near) < steady - 12.0                                 # la union pasa por (casi) silencio
    # ...pero sin hueco: el relleno del AAC (21 ms de silencio digital en cada union con -c copy)
    # ya no aparece, y el tono vuelve completo en cuanto acaba el micro-fundido
    seg = x[int((j - 0.05) * sr):int((j + 0.1) * sr)]
    assert np.sum(np.abs(seg) < 1e-5) < 0.004 * sr
    dips = [k for k, v in enumerate(near) if v < steady - 3.0]
    assert (dips[-1] - dips[0] + 2) * (win // 2) / sr <= 2 * compose.MICRO_FADE + 0.01
    assert _rms_db(x[int((j + 0.08) * sr):int((j + 0.2) * sr)]) > steady - 1.5
    # cuadros a cada lado de la union: blanco y azul, nunca negro
    assert _gray_at(out, j - 0.05, 180, 320) > 200
    assert _gray_at(out, j + 0.05, 180, 320) > 15


def _tone_switches(x, sr=48000, f1=440, f2=660):
    """Instantes (s del audio decodificado) en que el tono dominante cambia entre f1 y f2."""
    hop, win = 120, 960
    t = np.arange(win) / sr
    c1, c2 = np.exp(-2j * np.pi * f1 * t), np.exp(-2j * np.pi * f2 * t)
    out, prev = [], None
    for k in range(0, len(x) - win, hop):
        s = x[k:k + win]
        a, b = abs(np.dot(s, c1)), abs(np.dot(s, c2))
        if max(a, b) < 1.0:
            continue
        cur = 1 if a > b else 2
        if prev is not None and cur != prev:
            out.append((k + win / 2) / sr)
        prev = cur
    return out


def _sync_offsets(media, tag, montage=True):
    """Desfase audio-video (ms) en cada union de 7 segmentos con duraciones que no son de cuadros
    enteros (como las de un montaje real), alternando dos tonos."""
    d = media["d"]
    durs = [0.517, 0.611, 0.733, 0.557, 0.649, 0.701, 0.523]
    main, t = [], 0.0
    for k, du in enumerate(durs):
        src = media["va"] if k % 2 == 0 else media["vc"]
        m = {"slot": f"s{k}", "cand": "c1", "join": None if k == 0 else "splice"} if montage else None
        main.append(_item(src, 0.1, 0.1 + du, t, f"S{k}", m, vol=1.0))
        t += du
    info = {}
    out = compose.compose({"fps": FPS, "main": main}, str(d / f"sync_{tag}.mp4"), d / f"w_sync_{tag}",
                          info=info)
    shots = info["shots"]
    assert len(shots) == len(durs)
    sw = np.array(_tone_switches(compose._pcm(out)))
    vj = np.array([s["start"] for s in shots[1:]])
    assert len(sw) == len(vj), (sw, vj)
    return (sw - vj) * 1000.0, shots


def test_montaje_en_sincronia_sin_deriva_en_muchas_uniones(media):
    """El audio de cada segmento cambia exactamente donde cambia su video, sin atrasarse union tras
    union (con `-c copy` cada union metia el relleno del AAC y la voz se iba atrasando)."""
    off, shots = _sync_offsets(media, "mont")
    for s in shots:                                     # cada limite en la reticula (a ms, sin deriva)
        for t in (s["start"], s["end"]):
            assert abs(t * FPS - round(t * FPS)) <= 0.5e-3 * FPS + 1e-9, s
    # sin desfase ni deriva: video y audio del combinado arrancan en 0 (antes el concat corria los
    # dos ~21 ms, el relleno inicial del AAC, y plan.shots quedaba 21 ms antes del contenido)
    assert off.max() - off.min() < 6.0, off
    assert -5.0 < float(np.median(off)) < 5.0, off


def test_uniones_exactas_tambien_en_el_ensamble_manual_si_se_pide(media, monkeypatch):
    monkeypatch.setattr(compose, "EXACT_JOINS", "all")
    off, _shots = _sync_offsets(media, "all", montage=False)
    assert off.max() - off.min() < 6.0, off


def _video_switches(path, w=180, h=320, fps=FPS):
    """Instantes (s) en que el video del archivo cambia entre blanco y azul (primer cuadro nuevo)."""
    raw = subprocess.run([str(paths.FFMPEG), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray",
                          "-"], capture_output=True, check=True).stdout
    g = np.frombuffer(raw, np.uint8).reshape(-1, h * w).mean(axis=1)
    white = g > 128
    return [k / fps for k in range(1, len(white)) if white[k] != white[k - 1]]


def test_ensamble_manual_de_45_cortes_sin_deriva_por_defecto(media):
    """Regresion (v0.6.1): el ensamble MANUAL (clips sin "montage", lo que arma "Montar clips" a mano)
    iba con `-c copy` por defecto y cada union atrasaba el audio ~27 ms: tras 45 cortes la voz quedaba
    1.2 s detras de la imagen. Ahora las uniones exactas son el default de todo ensamble: en el ULTIMO
    segmento de un ensamble de 46 tramos, el audio cambia a menos de un cuadro de donde cambia la
    imagen (medido en el archivo: tono vs color), y en ninguna union se acumula desfase."""
    assert compose.EXACT_JOINS == "all"
    d = media["d"]
    rng = np.random.default_rng(45)
    durs = [round(float(x), 3) for x in rng.uniform(0.37, 0.73, 46)]          # duraciones fuera de la reticula
    main, t = [], 0.0
    for k, du in enumerate(durs):
        src = media["va"] if k % 2 == 0 else media["vc"]
        main.append(_item(src, 0.1, 0.1 + du, t, f"S{k}", None, vol=1.0))
        t += du
    info = {}
    out = compose.compose({"fps": FPS, "main": main}, str(d / "manual45.mp4"), d / "w_manual45", info=info)
    assert info["exact"] is True
    rec = compose.spec_record({"fps": FPS, "main": main}, info)
    assert rec["spec"]["exact_joins"] is True                       # el 4K recompone con el mismo modo
    vs = np.array(_video_switches(out))
    asw = np.array(_tone_switches(compose._pcm(out)))
    assert len(vs) == len(durs) - 1 and len(asw) == len(vs), (len(vs), len(asw))
    off = (asw - vs) * 1000.0
    assert abs(off[-1]) < 1000.0 / FPS, off[-1]                     # el ultimo segmento: < 1 cuadro
    assert np.abs(off).max() < 1000.0 / FPS, off                    # ninguna union se desfasa
    assert abs(np.polyfit(np.arange(len(off)), off, 1)[0]) < 0.5    # y no hay deriva (ms por union)


def test_modo_de_uniones_del_spec_manda(monkeypatch):
    monkeypatch.setattr(compose, "EXACT_JOINS", "all")
    assert compose.exact_joins({"main": []}) is True
    assert compose.exact_joins({"main": [], "exact_joins": False}) is False
    monkeypatch.setattr(compose, "EXACT_JOINS", "montage")
    assert compose.exact_joins({"main": [{"montage": {}}]}) is True and compose.exact_joins({"main": [{}]}) is False
    monkeypatch.setattr(compose, "EXACT_JOINS", "off")
    assert compose.exact_joins({"main": [{"montage": {}}]}) is False
    assert compose.exact_joins({"main": [{}], "exact_joins": True}) is True
    # un registro de antes de v0.6.1 (sin el modo): solo "Armar con IA" iba exacto
    assert compose.legacy_exact_joins({"main": [{"montage": {"slot": "s1"}}]}) is True
    assert compose.legacy_exact_joins({"main": [{"path": "x"}]}) is False


def test_sin_montaje_no_hay_micro_fundidos_ni_limitador(media, monkeypatch):
    d = media["d"]
    cmds = _capture(monkeypatch)
    main = [_item(media["va"], 0.0, 1.5, 0.0, "A", vol=1.0),
            _item(media["vc"], 0.0, 1.5, 1.5, "C", vol=2.0)]
    spec = {"fps": FPS, "main": main}
    before = copy.deepcopy(spec)
    compose.compose(spec, str(d / "man.mp4"), d / "w_man", info={})
    afs = _af_of(cmds, media["va"]) + _af_of(cmds, media["vc"])
    assert afs == ["volume=1.000,aresample=48000", "volume=2.000,aresample=48000"]
    assert spec == before


def test_volumen_auto_iguala_la_voz_de_los_segmentos(media, monkeypatch):
    d = media["d"]
    cmds = _capture(monkeypatch)
    main = [_item(media["va"], 0.1, 1.6, 0.0, "A", {"slot": "s1", "cand": "c1", "join": None}),
            _item(media["vb"], 0.1, 1.6, 1.5, "B", {"slot": "s2", "cand": "c1", "join": "splice"},
                  vol="auto")]
    spec = {"fps": FPS, "main": main}
    before = copy.deepcopy(spec)
    info = {}
    out = compose.compose(spec, str(d / "lvl.mp4"), d / "w_lvl", info=info)
    assert spec == before                                              # el spec del job no cambia
    lv = {e["index"]: e for e in info["levels"]}
    assert lv[0]["vol"] < 1.0 < lv[1]["vol"]
    # la toma que se sube pasa por el limitador; la que se baja no
    assert "alimiter" in _af_of(cmds, media["vb"])[0]
    assert "alimiter" not in _af_of(cmds, media["va"])[0]
    s0, s1 = info["shots"]
    la = compose.speech_lufs(out, s0["start"] + 0.1, s0["end"] - 0.1)
    lb = compose.speech_lufs(out, s1["start"] + 0.1, s1["end"] - 0.1)
    assert abs(la - lb) < 1.0, (la, lb)
    assert np.max(np.abs(compose._pcm(out, ch=2))) < 1.0               # sin recorte digital


def test_limitador_solo_en_la_toma_que_se_sube_del_montaje(media):
    """vol 3 sobre una voz fuerte: en el montaje el limitador la deja bajo 0 dBFS; en un ensamble
    manual (sin limitador, como siempre) pasa de 0 dBFS."""
    d = media["d"]
    peaks = {}
    for mont in (True, False):
        m = {"slot": "s1", "cand": "c1", "join": None} if mont else None
        main = [_item(media["vc"], 0.2, 1.4, 0.0, "C", m, vol=3.0)]
        out = compose.compose({"fps": FPS, "main": main}, str(d / f"lim_{mont}.mp4"), d / f"w_lim_{mont}",
                              info={})
        peaks[mont] = float(np.max(np.abs(compose._pcm(out, ch=2))))
    assert peaks[True] < 1.0 < peaks[False]
    assert peaks[True] > 0.8                                           # limita, no aplasta


# ------------------------------------------------------------------ lienzo
def test_lienzo_por_mayoria_en_el_montaje(media):
    d = media["d"]
    # el primero es HORIZONTAL; la mayoria (2 de 3) es vertical
    items = [_item(media["h"], 0.0, 1.0, 0.0, "H"), _item(media["va"], 0.0, 1.0, 1.0, "A"),
             _item(media["vc"], 0.0, 1.0, 2.0, "C")]
    assert compose.majority_canvas(items) == (180, 320)
    # empate en clips: gana la orientacion con mas segundos
    tie = [_item(media["h"], 0.0, 1.8, 0.0, "H"), _item(media["va"], 0.0, 0.6, 1.8, "A")]
    assert compose.majority_canvas(tie) == (320, 180)
    assert compose.majority_canvas([{"path": str(d / "no_existe.mp4"), "type": "video"}]) is None
    info = {}
    mont = [dict(it, montage={"slot": f"s{k}", "cand": "c1", "join": None if k == 0 else "section"})
            for k, it in enumerate(items)]
    compose.compose({"fps": FPS, "main": mont}, str(d / "maj.mp4"), d / "w_maj", info=info)
    assert (info["W"], info["H"]) == (180, 320)
    # ensamble manual (sin montaje): el lienzo sigue siendo el del primer video, como siempre
    info2 = {}
    compose.compose({"fps": FPS, "main": items}, str(d / "first.mp4"), d / "w_first", info=info2)
    assert (info2["W"], info2["H"]) == (320, 180)


# ------------------------------------------------------------------ tomas, plan.montage
def _montage_plan_fixture():
    return {"version": 1,
            "order": [{"sid": "sa", "name": "A.MOV", "rec": 0, "created": None}],
            "script": {"source": "user", "text": "Uno dos tres.\nCuatro cinco.\n\nSeis siete ocho nueve.",
                       "sections": [{"id": 1, "lines": ["L01", "L02"]}, {"id": 2, "lines": ["L03"]}],
                       "lines": [{"id": "L01", "text": "Uno dos tres."}, {"id": "L02", "text": "Cuatro cinco."},
                                 {"id": "L03", "text": "Seis siete ocho nueve."}]},
            "slots": [{"id": "s01", "lines": ["L01"], "chosen": "c1",
                       "cands": [{"id": "c1", "sid": "sa", "setup": 0}, {"id": "c2", "sid": "sc", "setup": 1}]},
                      {"id": "s02", "lines": ["L02"], "chosen": "c1",
                       "cands": [{"id": "c1", "sid": "sa", "setup": 0}]},
                      {"id": "s03", "lines": ["L03"], "chosen": "c1",
                       "cands": [{"id": "c1", "sid": "sc", "setup": 1}]}],
            "timeline": [], "discarded": [], "uncovered": [], "extra": [], "stats": {}, "ms": 1}


def test_tomas_con_union_y_plan_montage(media):
    d = media["d"]
    mp = _montage_plan_fixture()
    # el usuario cambio la toma de s01 a c2 en el ensamble ("Usar esta toma")
    main = [_item(media["vc"], 0.0, 1.0, 0.0, "C", {"slot": "s01", "cand": "c2", "join": "section"}, sid="sc"),
            _item(media["va"], 0.0, 1.0, 1.0, "A", {"slot": "s02", "cand": "c1", "join": "splice"},
                  sid="sa", vol=1.0),
            _item(media["vc"], 0.5, 1.5, 2.0, "C", {"slot": "s03", "cand": "c1", "join": "section", "conf": 0.9},
                  sid="sc")]
    spec = {"fps": FPS, "main": main, "montage": mp}
    info = {}
    compose.compose(spec, str(d / "mp.mp4"), d / "w_mp", info=info)
    sh = info["shots"]
    assert [s.get("join") for s in sh] == [None, "splice", "section"]    # la primera nunca tiene
    assert [s.get("setup") for s in sh] == [1, 0, 1]                    # del plan del montaje
    assert [s.get("sid") for s in sh] == ["sc", "sa", "sc"] and sh[2]["conf"] == 0.9
    words = [{"text": t, "start": s, "end": s + 0.2} for t, s in
             (("Uno", 0.1), ("dos", 0.4), ("tres.", 0.7), ("Cuatro", 1.2), ("cinco.", 1.6), ("Seis", 2.2))]
    j1 = sh[1]["start"]
    plan = {"duration": sh[-1]["end"], "phrases": [words], "cuts": [
        {"id": "sil", "start": 0.9, "end": 1.1, "enabled": True},
        {"id": "tk_x", "start": j1 - 0.3, "end": j1 + 0.3, "enabled": True, "src": "takes", "kind": "retoma"},
        {"id": "tk_ok", "start": j1 + 0.05, "end": j1 + 0.15, "enabled": True, "src": "takes", "kind": "retoma"}],
        "takes": {"version": 1, "sections": [{"start": 0.0, "title": "Tema del LLM"}], "stats": {}}}
    compose.finish_plan(plan, info, spec, montage=mp)
    ps = plan["shots"]
    assert [s.get("join") for s in ps] == [None, "splice", "section"]
    assert [s["setup"] for s in ps] == [1, 0, 1]
    m = plan["montage"]
    assert [(e["slot"], e["cand"], e["shot"], e["join"]) for e in m["timeline"]] == [
        ("s01", "c2", 0, None), ("s02", "c1", 1, "splice"), ("s03", "c1", 2, "section")]
    assert m["timeline"][1]["t0"] == ps[1]["start"] and m["timeline"][2]["t1"] == ps[2]["end"]
    assert m["slots"][0]["chosen"] == "c2"                              # la toma que se uso
    assert m["composed"] and m["script"]["source"] == "user"
    assert mp["slots"][0]["chosen"] == "c1"                             # el plan de entrada no cambia
    assert [c["id"] for c in plan["cuts"]] == ["sil", "tk_ok"]          # la toma que cruza, fuera
    assert plan["takes"]["sections"] == [{"start": 0.0, "title": "Uno dos tres"},
                                         {"start": ps[2]["start"], "title": "Seis siete ocho nueve"}]


def test_finish_plan_sin_montaje_es_lo_de_siempre():
    info = {"shots": [{"start": 0.0, "end": 2.0, "src": "a.mp4", "index": 0, "kind": "clip"},
                      {"start": 2.0, "end": 2.03, "src": "b.mp4", "index": 1, "kind": "clip"},
                      {"start": 2.03, "end": 5.2, "src": "c.mp4", "index": 2, "kind": "clip", "sid": "x"}]}
    cuts = [{"id": "tk_a", "start": 1.9, "end": 2.2, "src": "takes", "enabled": True}]
    plan = {"duration": 5.0, "cuts": list(cuts), "takes": {"sections": [{"start": 0.0, "title": "T"}]}}
    compose.finish_plan(plan, info, {"main": [{"path": "a.mp4"}]})
    assert plan["shots"] == [{"start": 0.0, "end": 2.0, "src": "a.mp4"},
                             {"start": 2.03, "end": 5.0, "src": "c.mp4", "sid": "x"}]
    assert plan["cuts"] == cuts and "montage" not in plan
    assert plan["takes"]["sections"] == [{"start": 0.0, "title": "T"}]
    # plan_shots: la primera toma nunca trae union aunque la traiga el segmento
    ps = compose.plan_shots([{"start": 0.0, "end": 1.0, "join": "line"},
                             {"start": 1.0, "end": 2.0, "join": "splice", "setup": 3}], 2.0)
    assert ps[0]["join"] is None and ps[1] == {"start": 1.0, "end": 2.0, "src": "", "join": "splice", "setup": 3}
    assert compose.is_montage({"main": [{"path": "a", "montage": {}}]})
    assert not compose.is_montage({"main": [{"path": "a"}]}) and not compose.is_montage(None)


def test_montaje_de_punta_a_punta_compose_plan_director(media):
    """compose (montaje) -> finish_plan (plan.shots con union, plan.montage, tomas que no cruzan)
    -> director sobre el video compuesto REAL: corte seco en el 'splice', transicion permitida en
    la 'section', y join/setup hasta el plan final."""
    from motion import director as D
    from test_director import _phrase
    d = media["d"]
    mp = _montage_plan_fixture()
    main = [_item(media["va"], 0.0, 2.0, 0.0, "A", {"slot": "s01", "cand": "c1", "join": None}, sid="sa"),
            _item(media["va"], 0.0, 2.0, 2.0, "A", {"slot": "s02", "cand": "c1", "join": "splice"}, sid="sa"),
            _item(media["vc"], 0.0, 2.0, 4.0, "C", {"slot": "s03", "cand": "c1", "join": "section"}, sid="sc")]
    spec = {"fps": FPS, "main": main}
    info = {}
    out = compose.compose(spec, str(d / "e2e.mp4"), d / "w_e2e", info=info)
    sh = info["shots"]
    phrases = []
    for txt, s in (("Uno dos tres,", sh[0]), ("cuatro cinco.", sh[1]), ("Seis siete ocho nueve.", sh[2])):
        ws, _t = _phrase(txt, s["start"] + 0.3)
        phrases.append(ws)
    j1, j2 = sh[1]["start"], sh[2]["start"]
    plan = {"stem": "e2e_montaje", "video": out, "duration": compose.probe(out)["dur"],
            "dims": [info["W"], info["H"]], "phrases": phrases, "beats": [], "camera": [],
            "highlights": [],
            "cuts": [{"id": "tk_cruza", "start": j1 - 0.4, "end": j1 + 0.4, "enabled": True, "src": "takes",
                      "kind": "retoma", "level": "alta"}],
            "takes": {"version": 1, "sections": [], "stats": {}}}
    compose.finish_plan(plan, info, spec, montage=mp)
    assert plan["cuts"] == [] and plan["takes"]["stats"]["n"] == 0
    assert [(e["slot"], e["shot"]) for e in plan["montage"]["timeline"]] == [("s01", 0), ("s02", 1), ("s03", 2)]
    p = D.direct(plan, style="dinamico", intensity=0.75, seed=7, use_llm=False, trim=False, music_sync=False)
    assert [(s.get("join"), s.get("setup")) for s in p["shots"]] == [(None, 0), ("splice", 0), ("section", 1)]
    tl = D._timeline(p)
    trs = [tl.remap_clamped(float(it["at"])) for it in p["fx"]["transitions"]]
    assert not any(abs(t - tl.remap_clamped(j1)) < 0.3 for t in trs), trs
    assert any(abs(t - tl.remap_clamped(j2)) < 0.12 for t in trs), trs
    assert p["montage"]["composed"] is True


def test_export_4k_recompone_con_el_mismo_modo_de_uniones(tmp_path):
    """El export en 4K recompone la MISMA edicion: un registro nuevo trae su modo de uniones; uno de
    antes de v0.6.1 (sin el) usa el de entonces (solo "Armar con IA" iba exacto), asi un ensamble manual
    viejo sigue cayendo en los mismos cuadros que el video del proyecto."""
    from test_server_setup import _run
    _run("""
import server as S
S.SOURCES.mkdir(parents=True, exist_ok=True)
(S.SOURCES / "a1.mp4").write_bytes(b"x")
viejo = {"spec": {"fps": 30, "main": [{"sid": "a1", "in": 0, "out": 1}]}}
assert S._hires_spec(viejo)["exact_joins"] is False
mont = {"spec": {"fps": 30, "main": [{"sid": "a1", "in": 0, "out": 1, "montage": {"slot": "s1"}}]}}
assert S._hires_spec(mont)["exact_joins"] is True
nuevo = {"spec": {"fps": 30, "main": [{"sid": "a1", "in": 0, "out": 1}], "exact_joins": True}}
assert S._hires_spec(nuevo)["exact_joins"] is True
print("OK")
""", tmp_path)
