"""Ciclo de vida del motor en la app de escritorio: ollama embebido huerfano de una sesion anterior
(se termina al arrancar, nunca el ollama del sistema) y vigilante del proceso padre (si la app muere,
el motor se apaga solo). Procesos reales e inofensivos (sleep) y puertos locales."""
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import server as S  # noqa: E402

posix = pytest.mark.skipif(os.name == "nt", reason="lsof/ps")


def _alive(p):
    return p.poll() is None


@posix
def test_mata_ollama_embebido_de_la_pidfile(tmp_path, monkeypatch):
    # un proceso cuyo comando contiene 'ollama-runtime' (como el embebido de la app)
    d = tmp_path / "engine" / "bin" / "ollama-runtime"
    d.mkdir(parents=True)
    fake = d / "ollama"
    fake.write_text("#!/bin/sh\nexec sleep 30\n")
    fake.chmod(0o755)
    p = subprocess.Popen([str(fake)])
    pidf = tmp_path / "ollama.pid"
    pidf.write_text(str(p.pid))
    monkeypatch.setattr(S, "OLLAMA_PID_F", pidf)
    monkeypatch.setattr(S, "OLLAMA_PORT", 1)                     # nadie escucha: solo la pidfile
    time.sleep(0.3)
    try:
        # `exec sleep` reemplaza el comando del proceso: ps lo reportaria como 'sleep 30'; se fija el
        # comando que reportaria un ollama embebido real
        monkeypatch.setattr(S, "_proc_cmd", lambda pid: f"{fake} serve" if pid == p.pid else "")
        done = S._kill_stale_ollama()
        assert done == [p.pid]
        p.wait(timeout=5)
        assert not _alive(p)
    finally:
        if _alive(p):
            p.kill()


@posix
def test_no_toca_procesos_ajenos_aunque_esten_en_la_pidfile(tmp_path, monkeypatch):
    p = subprocess.Popen(["sleep", "30"])
    pidf = tmp_path / "ollama.pid"
    pidf.write_text(str(p.pid))
    monkeypatch.setattr(S, "OLLAMA_PID_F", pidf)
    monkeypatch.setattr(S, "OLLAMA_PORT", 1)
    try:
        assert S._kill_stale_ollama() == []                       # 'sleep' no es ollama-runtime
        assert _alive(p)
    finally:
        p.kill()


@posix
def test_detecta_quien_escucha_en_el_puerto():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert os.getpid() in S._pids_listening(port)
    finally:
        srv.close()


def test_es_nuestro_ollama():
    assert S._is_our_ollama("/Applications/Reelfy.app/Contents/Resources/engine/bin/ollama-runtime/ollama serve")
    assert not S._is_our_ollama("/opt/homebrew/bin/ollama serve")           # el del sistema: nunca
    assert not S._is_our_ollama("")


@posix
def test_vigilante_apaga_cuando_muere_el_padre():
    """El 'padre' es un proceso aparte; al morir, el vigilante llama on_orphan. Se simula el
    re-emparentado con getppid (en la app real el padre es la app de escritorio)."""
    parent = subprocess.Popen(["sleep", "30"])
    fired = threading.Event()
    real_ppid = os.getppid
    try:
        S.os.getppid = lambda: parent.pid if parent.poll() is None else 1
        S._watch_parent(parent.pid, fired.set, every=0.05)
        time.sleep(0.3)
        assert not fired.is_set()                                 # padre vivo: sigue
        parent.kill()
        parent.wait()
        assert fired.wait(3)                                      # padre muerto: se apaga
    finally:
        S.os.getppid = real_ppid
        if parent.poll() is None:
            parent.kill()


def test_texto_de_primer_arranque_con_tamanos_reales():
    html = open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "index.html")).read()
    assert "~4 GB" not in html and "~5.5 GB" in html and "~3.7 GB" in html


def test_el_apagado_del_servidor_apaga_el_ollama(monkeypatch):
    """uvicorn re-lanza SIGTERM tras su apagado ordenado (los atexit no corren): el ollama se
    apaga en el lifespan de la app, que si corre."""
    import asyncio
    calls = []
    monkeypatch.setattr(S, "_stop_ollama", lambda: calls.append(1))
    assert S.app.router.lifespan_context is S._lifespan

    async def run():
        async with S.app.router.lifespan_context(S.app):
            assert calls == []
    asyncio.run(run())
    assert calls == [1]


@posix
def test_al_apagar_termina_los_procesos_que_lanzo(monkeypatch):
    # un "motor" (bash) con dos hijos que siguen vivos (como ace-synth y el ffmpeg de un proxy) y
    # un nieto: al apagar se terminan todos; nada queda huerfano
    motor = subprocess.Popen(["bash", "-c", "sleep 30 & (sleep 31 & wait) & wait"])
    try:
        time.sleep(0.4)
        hijos = S._descendants(motor.pid)
        assert len(hijos) >= 3
        monkeypatch.setattr(S.os, "getpid", lambda: motor.pid)
        S._kill_children(wait=1.0)
        monkeypatch.undo()
        time.sleep(0.3)
        vivos = [k for k in hijos if S._proc_cmd(k) and "defunct" not in S._proc_cmd(k)]
        assert vivos == []
        motor.wait(timeout=5)                       # sin hijos, el wait del motor termina
    finally:
        if motor.poll() is None:
            motor.kill()


def test_el_apagado_de_pruebas_no_mata_procesos_del_proceso_de_pruebas(monkeypatch):
    # importado en pruebas (TestClient) el lifespan NO barre hijos: solo el servidor real
    llamado = []
    monkeypatch.setattr(S, "_kill_children", lambda *a, **k: llamado.append(1))
    monkeypatch.setattr(S, "_stop_ollama", lambda: None)
    import asyncio

    async def run():
        async with S.app.router.lifespan_context(S.app):
            pass
    asyncio.run(run())
    assert S._SERVING is False and llamado == []
