"""Metadatos de las fuentes para "Armar con IA": compose.probe (fecha de grabacion `created`, fps,
hdr, rotacion) y la voz de cada clip (pipeline.extract_audio con la 1a pista de audio,
pipeline.transcribe_clip por clip y el semaforo de whisper compartido con el analisis).

Clips SINTETICOS (lavfi) con metadatos inventados; whisper falso salvo en la prueba que usa el modelo
real (se salta sin el modelo descargado)."""
import json
import os
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

import compose
import paths
import pipeline

FF = str(paths.FFMPEG)


def clip(path, dur=1.0, size="320x568", audio=True, freq=320, apple=None, ctime=None, comment=None,
         hdr=False, rotation=None, fps=30):
    """Clip sintetico con metadatos opcionales (fecha de Apple, creation_time, comentario)."""
    path = Path(path)
    tmp = path.with_name(f"_rot_{path.name}") if rotation else path
    cmd = [FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}:d={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={dur}"]
    if hdr:
        cmd += ["-vf", "setparams=color_trc=arib-std-b67:color_primaries=bt2020:colorspace=bt2020nc",
                "-c:v", "libx264", "-pix_fmt", "yuv420p10le"]
    else:
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    if apple:
        cmd += ["-movflags", "use_metadata_tags", "-metadata", f"com.apple.quicktime.creationdate={apple}"]
    if ctime:
        cmd += ["-metadata", f"creation_time={ctime}"]
    if comment:
        cmd += ["-metadata", f"comment={comment}"]
    subprocess.run(cmd + [str(tmp)], check=True)
    if rotation:
        subprocess.run([FF, "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(tmp),
                        "-c", "copy", "-map_metadata", "0", str(path)], check=True)
        tmp.unlink()
    return path


# ---------------------------------------------------------------- compose.probe
def test_probe_fecha_de_grabacion_apple_creation_time_y_archivo(tmp_path):
    a = clip(tmp_path / "A.MOV", apple="2026-03-14T10:02:05-0600")
    b = clip(tmp_path / "B.mp4", ctime="2026-03-14T16:05:00Z")
    # Apple y creation_time a la vez: gana la de Apple (hora local con su zona)
    ab = clip(tmp_path / "AB.MOV", apple="2026-03-14T10:00:00-0600", ctime="2026-03-14T18:00:00Z")
    c = clip(tmp_path / "C.mp4")
    # "sin fecha" de algunas camaras/exportadores (1970): se ignora -> la del archivo
    z = clip(tmp_path / "Z.mp4", ctime="1970-01-01T00:00:00Z")
    t_file = 1773504000.0                                  # 2026-03-14T16:00:00Z
    for p in (c, z):
        os.utime(p, (t_file, t_file))
    pa, pb, pab, pc, pz = (compose.probe(p) for p in (a, b, ab, c, z))
    assert pa["created"] == "2026-03-14T10:02:05-06:00" and pa["created_src"] == "apple"
    assert pb["created"] == "2026-03-14T16:05:00+00:00" and pb["created_src"] == "creation_time"
    assert pab["created_src"] == "apple" and pab["created"].startswith("2026-03-14T10:00:00")
    # los instantes son comparables entre zonas: A (16:02:05Z) antes que B (16:05:00Z)
    assert pa["created_ts"] < pb["created_ts"]
    assert pa["created_ts"] == pytest.approx(1773504125.0)
    for p in (pc, pz):
        assert p["created_src"] == "mtime" and p["created_ts"] == pytest.approx(t_file)
        # ISO 8601 con zona (hora local de la maquina)
        from datetime import datetime
        assert datetime.fromisoformat(p["created"]).timestamp() == pytest.approx(t_file)
    # lo de siempre sigue igual
    assert (pa["w"], pa["h"], pa["type"], pa["audio"]) == (320, 568, "video", True)


def test_probe_fps_hdr_y_rotacion(tmp_path):
    h = clip(tmp_path / "hlg.mp4", hdr=True, audio=False)
    r = clip(tmp_path / "rot.mp4", rotation=90, fps=60)
    ph, pr = compose.probe(h), compose.probe(r)
    assert ph["hdr"] == "hlg" and ph["fps"] == pytest.approx(30.0) and ph["rotation"] == 0
    assert not ph["audio"]
    # rotacion de display: 320x568 codificado, 568x320 en pantalla
    assert pr["rotation"] in (90, 270) and (pr["w"], pr["h"]) == (568, 320)
    assert pr["fps"] == pytest.approx(60.0) and pr["hdr"] is None
    # un archivo ilegible no revienta: dims 0 y la fecha del archivo
    bad = tmp_path / "roto.mov"
    bad.write_bytes(b"no es un video")
    pb = compose.probe(bad)
    assert pb["w"] == 0 and pb["fps"] == 0.0 and pb["created_src"] == "mtime"


def test_probe_fecha_en_cache_por_archivo(tmp_path, monkeypatch):
    """La fecha se lee UNA vez por archivo (compose sondea cada segmento varias veces)."""
    a = clip(tmp_path / "A.MOV", apple="2026-03-14T10:02:05-0600")
    compose._created_cached.cache_clear()
    calls = []
    real = subprocess.run

    def spy(cmd, *a_, **k):
        if "format_tags=com.apple.quicktime.creationdate,creation_time:stream_tags=creation_time" in cmd:
            calls.append(cmd)
        return real(cmd, *a_, **k)
    monkeypatch.setattr(compose.subprocess, "run", spy)
    for _ in range(3):
        assert compose.created(a)["created_src"] == "apple"
    assert len(calls) == 1
    os.utime(a, (time.time() + 5, time.time() + 5))       # el archivo cambio: se vuelve a leer
    compose.created(a)
    assert len(calls) == 2


# ---------------------------------------------------------------- voz de cada clip
def _dominant_hz(wav):
    with wave.open(str(wav)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
    spec = np.abs(np.fft.rfft(x))
    return float(np.fft.rfftfreq(len(x), 1 / 16000)[int(np.argmax(spec))])


def test_extract_audio_usa_la_primera_pista(tmp_path):
    """Un iPhone trae una 2a pista de audio (APAC 4.0): ffmpeg elegia la de MAS canales. La voz es
    la 1a pista: -map 0:a:0 (aqui 1a pista mono a 440 Hz, 2a de 4 canales a 1000 Hz)."""
    src = tmp_path / "dos_pistas.mov"
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x284:r=30:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
                    "-filter_complex", "[2:a]pan=4c|c0=c0|c1=c0|c2=c0|c3=c0[q]",
                    "-map", "0:v", "-map", "1:a", "-map", "[q]", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    wav = tmp_path / "voz.wav"
    pipeline.extract_audio(src, wav)
    assert abs(_dominant_hz(wav) - 440) < 15
    with pytest.raises(RuntimeError, match="no tiene pista de audio"):
        pipeline.extract_audio(clip(tmp_path / "mudo.mp4", audio=False), tmp_path / "x.wav")


def _fake_whisper(record, texts, hold=0.0):
    """subprocess.run falso para whisper-cli: escribe el JSON (-of) con `texts` y anota si el
    semaforo de la GPU estaba tomado y cuantos corrian a la vez."""
    real = subprocess.run
    state = {"now": 0}
    lock = threading.Lock()

    def run(cmd, *a, **k):
        if not (cmd and str(cmd[0]).endswith("whisper-cli")):
            return real(cmd, *a, **k)
        with lock:
            state["now"] += 1
            record.append({"gpu_taken": pipeline.WHISPER_GPU._value == 0, "at_once": state["now"],
                           "prompt": cmd[cmd.index("--prompt") + 1] if "--prompt" in cmd else None})
        time.sleep(hold)
        out = cmd[cmd.index("-of") + 1]
        segs, t, pause = [], 0.3, None
        for tx in texts:                  # "|0.9|" = pausa antes de la palabra siguiente
            if tx.startswith("|"):
                pause = float(tx.strip("|"))
                continue
            t += pause if pause is not None else (0.04 + (0.3 if segs and segs[-1]["text"][-1] in ".," else 0))
            d = 0.10 + 0.06 * len(tx)
            segs.append({"text": " " + tx, "offsets": {"from": int(t * 1000), "to": int((t + d) * 1000)},
                         "tokens": []})
            t, pause = t + d, None
        Path(f"{out}.json").write_text(json.dumps({"transcription": segs}))
        with lock:
            state["now"] -= 1
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def test_transcribe_clip_un_clip_con_sus_tomas(tmp_path, monkeypatch):
    rec = []
    said = ("Hoy reparo bicicletas de, |1.0| hoy reparo bicicletas de montaña en el centro. |0.6| "
            "Te espero.").split()
    words = [w for w in said if not w.startswith("|")]
    monkeypatch.setattr(pipeline.subprocess, "run", _fake_whisper(rec, said))
    monkeypatch.setattr(pipeline, "WORK", tmp_path)
    src = clip(tmp_path / "BICI.MOV", dur=6.0)
    wav = tmp_path / "BICI_16k.wav"
    res = pipeline.transcribe_clip(src, wav, align=False, dtw=False)
    assert res["audio"] and wav.exists() and res["dur"] == pytest.approx(6.0, abs=0.1)
    assert [w["text"] for w in res["words"]] == words
    assert all(set(w) == {"text", "start", "end"} for w in res["words"])
    assert rec and rec[0]["gpu_taken"] and rec[0]["prompt"] is None      # sin glosario, sin prompt
    # la retoma DENTRO del clip (arranca dos veces) sale como corte de tomas del clip
    assert res["takes"] and all(c.get("src") == "takes" for c in res["takes"])
    assert res["takes"][0]["start"] < res["words"][4]["start"] and res["takes"][0]["w0"] == 0
    assert not list(tmp_path.glob("clipwords_*"))                       # temporales borrados
    # glosario: va como prompt corto
    res2 = pipeline.transcribe_clip(src, wav, glossary="Marta, Rueda Libre", align=False, dtw=False,
                                    detect_takes=False)
    assert rec[-1]["prompt"] == "Nombres propios y términos: Marta, Rueda Libre." and res2["takes"] == []


def test_transcribe_clip_sin_audio_no_es_error(tmp_path, monkeypatch):
    rec = []
    monkeypatch.setattr(pipeline.subprocess, "run", _fake_whisper(rec, ["nada"]))
    wav = tmp_path / "m.wav"
    res = pipeline.transcribe_clip(clip(tmp_path / "MUDO.mp4", audio=False, dur=1.5), wav)
    assert res["audio"] is False and res["words"] == [] and res["takes"] == []
    assert not wav.exists() and rec == []                               # whisper ni se llamo


def test_whisper_de_a_uno_entre_hilos(tmp_path, monkeypatch):
    """El carril de cada clip y el analisis del video combinado corren en hilos distintos: nunca
    dos whisper a la vez (pipeline.WHISPER_GPU)."""
    rec = []
    monkeypatch.setattr(pipeline.subprocess, "run", _fake_whisper(rec, ["hola"], hold=0.15))
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"")
    ths = [threading.Thread(target=pipeline.transcribe, args=(wav, tmp_path / f"o{i}", "", False))
           for i in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert len(rec) == 4 and max(r["at_once"] for r in rec) == 1 and all(r["gpu_taken"] for r in rec)
    assert pipeline.WHISPER_GPU._value == 1


WHISPER_MODEL = next((c / "ggml-large-v3-turbo.bin" for c in (
    Path.home() / "Library/Application Support/online.mixiuh.reelfy/models",
    Path.home() / "Library/Application Support/Reelfy/models")
    if (c / "ggml-large-v3-turbo.bin").exists()), None)


@pytest.mark.skipif(WHISPER_MODEL is None or not Path("/usr/bin/say").exists(),
                    reason="sin el modelo de voz descargado o sin la voz del sistema (say)")
def test_transcribe_clip_real_con_voz_sintetica(tmp_path):
    """whisper REAL sobre un clip con voz sintetica en espanol (macOS say): palabras con tiempos
    dentro del clip. Corre en un proceso aparte con DATA temporal (el modelo enlazado)."""
    aiff = tmp_path / "voz.aiff"
    subprocess.run(["/usr/bin/say", "-v", "Paulina", "-o", str(aiff),
                    "Hola, soy Marta y reparo bicicletas en el centro."], check=True)
    src = tmp_path / "VOZ.MOV"
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x284:r=30:d=4",
                    "-i", str(aiff), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    data = tmp_path / "data"
    (data / "models").mkdir(parents=True)
    (data / "models" / WHISPER_MODEL.name).symlink_to(WHISPER_MODEL)
    code = f'''
import json, sys
sys.path.insert(0, "scripts")
import pipeline
res = pipeline.transcribe_clip({str(src)!r}, {str(tmp_path / "v16k.wav")!r})
print("RES" + json.dumps(res))
'''
    env = dict(os.environ, REELFY_DATA=str(data))
    r = subprocess.run([pipeline.sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]),
                       env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    res = json.loads(r.stdout.split("RES", 1)[1])
    text = " ".join(w["text"] for w in res["words"]).lower()
    assert "bicicletas" in text and "marta" in text, text
    assert res["audio"] and 0 <= res["words"][0]["start"] < res["words"][-1]["end"] <= 4.2
