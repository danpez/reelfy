"""app/exportkit.py: opciones del export (sin servidor ni motor). Resolucion por formato y cuando
se apaga (nunca escalar por encima de la fuente), cuando hay que recomponer, nombre del archivo
saneado, colisiones sin sobrescribir (clon APFS / enlace / copia), carpetas validas, selector nativo
por plataforma (falso en las pruebas), Mostrar en Finder, ajustes recordados y estimaciones.
Archivos inventados en tmp_path; nada toca ~/Movies ni abre ventanas."""
import errno
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import exportkit as EK  # noqa: E402

V916, V4K = (1080, 1920), (2160, 3840)


# ---------------------------------------------------------------- resoluciones
@pytest.mark.parametrize("fmt,res,wh", [
    ("9:16", "720p", (720, 1280)), ("9:16", "1080p", (1080, 1920)), ("9:16", "1440p", (1440, 2560)),
    ("9:16", "4k", (2160, 3840)), ("16:9", "720p", (1280, 720)), ("16:9", "4k", (3840, 2160)),
    ("1:1", "1440p", (1440, 1440)), ("1:1", "4k", (2160, 2160)), ("4:5", "720p", (720, 900)),
    ("4:5", "4k", (2160, 2700)), ("16:9", "1080p", (1920, 1080))])
def test_tamano_por_formato_y_resolucion(fmt, res, wh):
    assert EK.export_size(fmt, res) == wh


def test_original_respeta_el_aspecto_y_1080p_es_lo_de_siempre():
    # 1080p = render.output_size('original'): lado corto 1080, largo <= 3840
    assert EK.export_size("original", "1080p", (2160, 3840)) == (1080, 1920)
    assert EK.export_size("original", "1080p", (1080, 2340)) == (1080, 2340)
    assert EK.export_size("original", "4k", (2160, 3840)) == (2160, 3840)
    w, h = EK.export_size("original", "4k", (1170, 2532))          # 19.5:9: lado largo <= 4096
    assert h <= EK.LONG_MAX and w % 2 == 0 and h % 2 == 0 and abs(w / h - 1170 / 2532) < 0.01
    with pytest.raises(ValueError):
        EK.export_size("original", "1080p", None)
    with pytest.raises(ValueError):
        EK.export_size("2:1", "1080p")
    with pytest.raises(ValueError):
        EK.export_size("9:16", "8k")


def test_1080p_coincide_con_el_motor():
    from motion import render as R
    for fmt in R.FORMATS:
        assert EK.export_size(fmt, "1080p", formats=R.FORMATS) == R.FORMATS[fmt]
    for src in ((2160, 3840), (1920, 1080), (1080, 1350), (3000, 1000)):
        assert EK.export_size("original", "1080p", src, R.FORMATS) == \
            R.output_size("original", {"w": src[0], "h": src[1]})


def _rows(fmt, native, combined=None, can=True):
    return {r["id"]: r for r in EK.resolution_matrix(fmt, native, combined or native, can)}


def test_matriz_fuente_1080p_apaga_1440p_y_4k_con_el_motivo():
    rows = _rows("9:16", V916)
    assert [k for k, r in rows.items() if r["enabled"]] == ["720p", "1080p"]
    assert rows["4k"]["reason"] == "Tu video original es 1080p; 4K no agregaría calidad"
    assert rows["1440p"]["reason"] == "Tu video original es 1080p; 1440p no agregaría calidad"
    assert not any(r["recompose"] for r in rows.values())


def test_matriz_fuente_4k_vertical_en_cada_formato():
    # montaje: video combinado con tope 1620x2880, fuentes 4K
    rows = _rows("9:16", V4K, (1620, 2880))
    assert all(r["enabled"] for r in rows.values())
    assert rows["4k"]["recompose"] and rows["4k"]["recompose_size"] == [2160, 3840]
    assert not rows["1440p"]["recompose"]                # 1440x2560 cabe en 1620x2880
    assert not rows["1080p"]["recompose"] and not rows["720p"]["recompose"]
    sq = _rows("1:1", V4K, (1620, 2880))
    assert sq["4k"]["enabled"] and sq["4k"]["recompose_size"] == [2160, 3840]
    # 16:9 de un video vertical: el motor NO recorta (pillar: la toma completa a lo alto sobre un
    # fondo desenfocado) -> 4K saca 2160 px de alto de 3840: sin escalar hacia arriba
    hz = _rows("16:9", V4K, (1620, 2880))
    assert all(r["enabled"] for r in hz.values()) and not any(r["recompose"] for r in hz.values())
    # y el recorte de verdad (4K horizontal -> 9:16) si apaga 1440p/4K, con el nivel conocido
    v = _rows("9:16", (3840, 2160))
    assert not v["4k"]["enabled"] and "Al recortar tu video (4K horizontal)" in v["4k"]["reason"]
    assert "1080p+" in v["4k"]["reason"] and "1215p" not in v["4k"]["reason"]


def test_vertical_a_horizontal_es_pillar_no_recorte():
    """Regresion: useful_short suponia que todo formato es un recorte y apagaba 1440p/4K de un
    vertical en 16:9 con un motivo falso ("Al recortar tu video... queda en 1215p")."""
    assert EK.is_pillar("16:9", V4K) and EK.is_pillar("16:9", V916)
    assert not EK.is_pillar("9:16", (3840, 2160)) and not EK.is_pillar("1:1", V4K)
    assert not EK.is_pillar("4:5", V916) and not EK.is_pillar("original", V916)
    assert EK.useful_short("16:9", V4K) == 3840 and EK.useful_short("16:9", V916) == 1920
    r = _rows("16:9", V916)                         # vertical 1080p -> 16:9: 1440p si (1440 <= 1920)
    assert r["1440p"]["enabled"] and not r["4k"]["enabled"]
    assert r["4k"]["reason"] == ("Tu video original (1080p vertical) mide 1920 px de alto; en Horizontal "
                                 "16:9 4K no agregaría calidad")
    assert "recortar" not in r["4k"]["reason"]
    # 4K vertical en un montaje con tope 1620x2880: 16:9 4K NO necesita recomponer (2880 >= 2160)
    assert EK.needed_canvas("16:9", "4k", V4K, (1620, 2880)) is None
    assert EK.needed_canvas("16:9", "4k", V4K, (720, 1280)) == (1214, 2160)


def test_fuente_horizontal_4k():
    rows = _rows("16:9", (3840, 2160))
    assert all(r["enabled"] for r in rows.values()) and not any(r["recompose"] for r in rows.values())
    v = _rows("9:16", (3840, 2160))              # recorte vertical de un 4K horizontal: 1215x2160
    assert v["1080p"]["enabled"] and not v["1440p"]["enabled"] and not v["4k"]["enabled"]


def test_sin_fuentes_no_se_puede_recomponer():
    rows = _rows("9:16", V4K, (1620, 2880), can=False)
    assert rows["1440p"]["enabled"] and not rows["4k"]["enabled"]
    assert "máximo es 1440p" in rows["4k"]["reason"]


def test_lienzo_necesario_y_clamp():
    assert EK.needed_canvas("9:16", "4k", V4K, (1620, 2880)) == (2160, 3840)
    assert EK.needed_canvas("9:16", "1440p", V4K, (1620, 2880)) is None
    assert EK.needed_canvas("9:16", "1080p", V4K, (720, 1280)) is None     # <= 1080p: lo de siempre
    assert EK.needed_canvas("9:16", "4k", V4K, V4K) is None                # ya es nativo
    # fuente 5K: para 1440p basta el combinado; para 4K se recompone proporcional (no al nativo)
    assert EK.needed_canvas("9:16", "4k", (2880, 5120), (1620, 2880)) == (2160, 3840)
    rows = EK.resolution_matrix("9:16", V916, V916)
    assert EK.clamp_res("4k", rows) == "1080p" and EK.clamp_res("720p", rows) == "720p"
    assert EK.clamp_res("raro", rows) == "1080p"


def test_etiquetas():
    assert EK.res_label_of(2160) == "4K" and EK.res_label_of(1080) == "1080p"
    # entre dos niveles: el de abajo con '+' (nadie conoce "1620p"); debajo de 720, tal cual
    assert EK.res_label_of(1620) == "1440p+" and EK.res_label_of(1215) == "1080p+"
    assert EK.res_label_of(3000) == "4K" and EK.res_label_of(600) == "600p" and EK.res_label_of(1438) == "1440p"
    assert EK.orientation(1080, 1920) == "vertical"


def test_montaje_con_clips_de_distinta_resolucion():
    """Regresion: el 'nativo' de un montaje era el primer clip; con clips 4K y 1080p se escalaban
    los 1080p a 4K sin avisar o se escondia 4K aunque casi todo fuera 4K."""
    many4k = [{"w": 2160, "h": 3840, "secs": 10}, {"w": 1080, "h": 1920, "secs": 3},
              {"w": 2160, "h": 3840, "secs": 5}]
    r = {x["id"]: x for x in EK.resolution_matrix("9:16", V4K, (1620, 2880), clips=many4k)}
    assert r["4k"]["enabled"] and r["4k"]["recompose_size"] == [2160, 3840]
    assert r["4k"]["warn"] == "1 de 3 clips es 1080p: se verá escalado en 4K"
    assert r["1080p"]["warn"] is None and r["720p"]["warn"] is None
    few4k = [{"w": 2160, "h": 3840, "secs": 2}, {"w": 1080, "h": 1920, "secs": 10},
             {"w": 720, "h": 1280, "secs": 4}]
    r = {x["id"]: x for x in EK.resolution_matrix("9:16", V4K, (1620, 2880), clips=few4k)}
    assert not r["4k"]["enabled"] and not r["1440p"]["enabled"]
    assert r["4k"]["reason"] == "La mayoría de tus clips son de 1080p o menos; 4K no agregaría calidad"
    assert r["1080p"]["enabled"]
    same = [{"w": 2160, "h": 3840, "secs": 3}] * 3
    r = {x["id"]: x for x in EK.resolution_matrix("9:16", V4K, (1620, 2880), clips=same)}
    assert r["4k"]["enabled"] and r["4k"]["warn"] is None


# ---------------------------------------------------------------- nombre
@pytest.mark.parametrize("raw,exp", [
    ("Mi video", "Mi video"), ("Mi video.mp4", "Mi video"), ("Mi video.MOV", "Mi video"),
    ("Tema: subtítulo", "Tema - subtítulo"), ("a/b\\c", "a-b-c"), ("a:b|c", "a-b-c"),
    ("¿Qué? <hola> \"x\" *", "¿Qué hola x"), ("  .oculto. ", "oculto"), ("../../etc/passwd", "-.-etc-passwd"),
    ("..", "Reelfy"), ("", "Reelfy"), (None, "Reelfy"), ("\x00\x1b", "Reelfy"), ("CON", "CON_"),
    ("lpt1.txt", "lpt1.txt_"), ("ñandú 🎬 – día 1", "ñandú 🎬 – día 1"), ("a   b\tc\nd", "a b c d")])
def test_nombre_saneado(raw, exp):
    assert EK.sanitize_name(raw) == exp


def test_nombre_largo_se_corta_por_bytes_sin_partir_caracteres():
    s = EK.sanitize_name("ñ" * 300)
    assert len(s.encode()) <= EK.NAME_MAX_BYTES and set(s) == {"ñ"}
    assert EK.sanitize_name("", default="Proyecto X") == "Proyecto X"


def test_nombre_por_defecto():
    t = 1790000000          # fecha fija
    day = __import__("time").strftime("%Y-%m-%d", __import__("time").localtime(t))
    assert EK.default_name("Cuida tu sonrisa", t) == f"Cuida tu sonrisa - {day}"
    assert EK.default_name(None, t) == f"Reelfy - {day}"
    long = EK.default_name("palabra " * 30, t)
    assert long.endswith(day) and len(long) <= EK.TOPIC_MAX + 3 + len(day)
    assert "/" not in EK.default_name("a/b: c", t)


# ---------------------------------------------------------------- colisiones y copia
def test_ruta_unica(tmp_path):
    assert EK.unique_path(tmp_path, "V").name == "V.mp4"
    (tmp_path / "V.mp4").write_bytes(b"x")
    (tmp_path / "V (2).mp4").write_bytes(b"x")
    assert EK.unique_path(tmp_path, "V").name == "V (3).mp4"


@pytest.mark.parametrize("allow_link", [True, False])
def test_colocar_sin_sobrescribir(tmp_path, allow_link):
    src = tmp_path / "src.mp4"
    src.write_bytes(os.urandom(3000))
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "Video.mp4").write_bytes(b"NO TOCAR")
    got = []
    for _ in range(3):
        p, how = EK.place_file(src, dest, "Video", allow_link=allow_link)
        got.append(p.name)
        assert p.parent == dest and p.read_bytes() == src.read_bytes()
        assert how in (("clone", "link") if allow_link else ("copy",))
    assert got == ["Video (2).mp4", "Video (3).mp4", "Video (4).mp4"]
    assert (dest / "Video.mp4").read_bytes() == b"NO TOCAR"
    assert not [q for q in dest.iterdir() if q.name.startswith(".")]      # sin temporales


def test_clon_apfs_no_comparte_el_archivo(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"abc" * 1000)
    p, how = EK.place_file(src, tmp_path, "Clon")
    if sys.platform == "darwin" and how == "clone":
        assert os.stat(p).st_ino != os.stat(src).st_ino and os.stat(src).st_nlink == 1
    elif how == "link":
        assert os.stat(p).st_ino == os.stat(src).st_ino


def test_copia_con_progreso_y_sin_enlaces(tmp_path, monkeypatch):
    """Otro volumen o exFAT (sin enlaces duros): copia a un temporal oculto + reserva del nombre."""
    src = tmp_path / "src.mp4"
    src.write_bytes(os.urandom(EK.COPY_CHUNK + 123))
    dest = tmp_path / "usb"
    dest.mkdir()
    real_link = os.link

    def no_link(a, b, **kw):
        raise OSError(errno.EPERM, "sin enlaces")
    monkeypatch.setattr(os, "link", no_link)
    seen = []
    p, how = EK.place_file(src, dest, "Clip", allow_link=False, on_bytes=lambda d, t: seen.append((d, t)))
    assert how == "copy" and p.read_bytes() == src.read_bytes() and seen[-1][0] == seen[-1][1]
    p2, _ = EK.place_file(src, dest, "Clip", allow_link=False)
    assert p2.name == "Clip (2).mp4" and sorted(q.name for q in dest.iterdir()) == ["Clip (2).mp4", "Clip.mp4"]
    monkeypatch.setattr(os, "link", real_link)


def test_sin_espacio_deja_la_carpeta_como_estaba(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(os.urandom(5000))
    dest = tmp_path / "lleno"
    dest.mkdir()

    def full(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(EK, "_copy_bytes", full)
    with pytest.raises(OSError) as ei:
        EK.place_file(src, dest, "X", allow_link=False)
    assert EK.is_no_space(ei.value) and list(dest.iterdir()) == []
    assert EK.is_no_space(RuntimeError("ffmpeg: No space left on device")) and not EK.is_no_space(ValueError("x"))


# ---------------------------------------------------------------- carpetas
def test_estado_de_carpeta(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    st = EK.folder_status(ok)
    assert st["writable"] and st["error"] is None and st["free_bytes"] > 0 and st["name"] == "ok"
    assert EK.folder_status(tmp_path / "no").get("error", "").startswith("La carpeta «no» ya no existe")
    assert EK.folder_status("relativa/x")["error"]
    data = tmp_path / "data"
    (data / "output").mkdir(parents=True)
    assert "interna de Reelfy" in EK.folder_status(data / "output", data_dir=data)["error"]
    nuevo = tmp_path / "Movies" / "Reelfy"
    assert EK.folder_status(nuevo, create=True)["writable"] and nuevo.is_dir()
    if os.geteuid() != 0:
        ro = tmp_path / "solo_lectura"
        ro.mkdir()
        ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            st = EK.folder_status(ro)
            assert not st["writable"] and "No se puede escribir en la carpeta «solo_lectura»" in st["error"]
        finally:
            ro.chmod(0o755)


def test_carpeta_por_defecto_y_etiqueta(monkeypatch, tmp_path):
    monkeypatch.delenv("REELFY_EXPORT_DIR", raising=False)
    d = EK.default_folder()
    assert d.name == "Reelfy" and d.parent.name == ("Movies" if sys.platform == "darwin" else "Videos")
    monkeypatch.setenv("REELFY_EXPORT_DIR", str(tmp_path / "x"))
    assert EK.default_folder() == Path(os.path.realpath(tmp_path)) / "x"
    assert EK.home_label(Path.home() / "Movies" / "Reelfy") == "~/Movies/Reelfy"
    assert EK.norm_folder("rel") is None and EK.norm_folder("") is None and EK.norm_folder("/a\x00b") is None
    assert EK.norm_folder(str(tmp_path / "a" / ".." / "b")) == str(Path(os.path.realpath(tmp_path)) / "b")


# ---------------------------------------------------------------- selector nativo
def test_selector_por_plataforma(monkeypatch):
    monkeypatch.delenv("REELFY_PICK_FOLDER_CMD", raising=False)
    which = {"osascript": "/usr/bin/osascript", "powershell": "C:/ps.exe", "zenity": "/usr/bin/zenity",
             "kdialog": "/usr/bin/kdialog"}
    cmd, env, codes, marker = EK.picker_cmd("/x/y", platform="darwin", which=which.get)
    assert cmd[0] == "/usr/bin/osascript" and "choose folder with prompt p default location d" in " ".join(cmd)
    assert cmd[-2:] == [EK.PICK_PROMPT, "/x/y"] and codes == (1,)     # textos como argumentos
    assert marker == "-128"              # osascript sale con 1 tambien en sus errores: cancelar es -128
    cmd, env, codes, marker = EK.picker_cmd("C:/v", platform="win32", which=which.get)
    assert cmd[:2] == ["C:/ps.exe", "-NoProfile"] and "-STA" in cmd and "FolderBrowserDialog" in cmd[-1]
    assert env == {"REELFY_PICK_PROMPT": EK.PICK_PROMPT, "REELFY_PICK_START": "C:/v"}
    assert codes == (1,) and marker is None
    cmd, _, codes, marker = EK.picker_cmd("/home/u", platform="linux", which=which.get)
    assert cmd[:3] == ["/usr/bin/zenity", "--file-selection", "--directory"] and cmd[-1] == "/home/u/"
    assert codes == (1,) and marker is None
    cmd, _, codes, marker = EK.picker_cmd(None, platform="linux", which={"kdialog": "/usr/bin/kdialog"}.get)
    assert cmd[:2] == ["/usr/bin/kdialog", "--getexistingdirectory"] and codes == (1,) and marker is None
    assert EK.picker_cmd(None, platform="linux", which=lambda n: None) is None
    assert EK.run_picker(None, platform="linux", which=lambda n: None)["error"] == EK.PICK_NONE


def _fake(tmp_path, body, name="picker.sh"):
    f = tmp_path / name
    f.write_text("#!/bin/bash\n" + body + "\n")
    f.chmod(0o755)
    return str(f)


def test_selector_falso_elige_cancela_y_falla(monkeypatch, tmp_path):
    target = tmp_path / "Elegida"
    target.mkdir()
    monkeypatch.setenv("REELFY_PICK_FOLDER_CMD", _fake(tmp_path, f'echo "{target}/"'))
    r = EK.run_picker("/inicio")
    assert r == {"path": str(Path(os.path.realpath(target))), "canceled": False, "error": None}
    monkeypatch.setenv("REELFY_PICK_FOLDER_CMD", _fake(tmp_path, 'echo "execution error: User canceled. (-128)" >&2; exit 1'))
    assert EK.run_picker()["canceled"] is True
    logs = []
    monkeypatch.setenv("REELFY_PICK_FOLDER_CMD", _fake(tmp_path, 'echo "boom" >&2; exit 3'))
    r = EK.run_picker(log=logs.append)
    # el texto tecnico va al registro, al usuario una frase en espanol
    assert r == {"path": None, "canceled": False, "error": EK.PICK_FAIL} and "boom" in logs[-1]
    monkeypatch.setenv("REELFY_PICK_FOLDER_CMD", _fake(tmp_path, 'echo "relativa"'))
    assert EK.run_picker(log=None)["error"]


GTK_WARN = ('echo "Gtk-Message: 10:22:01.123: GtkDialog mapped without a transient parent. '
            'This is discouraged." >&2; ')


@pytest.mark.parametrize("tool", ["zenity", "kdialog"])
def test_cancelar_zenity_kdialog_con_avisos_de_gtk(monkeypatch, tmp_path, tool):
    """Regresion: zenity/kdialog escriben avisos de GTK/Qt en stderr al cancelar (salen con 1): no
    es un error 501 con el texto en ingles, es cancelar."""
    monkeypatch.delenv("REELFY_PICK_FOLDER_CMD", raising=False)
    fake = _fake(tmp_path, GTK_WARN + "exit 1", tool)
    logs = []
    r = EK.run_picker("/home/u", platform="linux", which={tool: fake}.get, log=logs.append)
    assert r == {"path": None, "canceled": True, "error": None} and not logs
    # elegir con el mismo aviso en stderr: la carpeta
    target = tmp_path / "Videos"
    target.mkdir()
    fake = _fake(tmp_path, GTK_WARN + f'echo "{target}"', tool)
    r = EK.run_picker(None, platform="linux", which={tool: fake}.get)
    assert r["path"] == str(Path(os.path.realpath(target))) and not r["canceled"]
    # un fallo de verdad (codigo 2...): frase en espanol, sin el texto de GTK
    fake = _fake(tmp_path, GTK_WARN + "exit 255", tool)
    r = EK.run_picker(None, platform="linux", which={tool: fake}.get, log=logs.append)
    assert r["error"] == EK.PICK_FAIL and "Gtk" not in r["error"] and "Gtk-Message" in logs[-1]


def test_osascript_error_no_es_cancelar_ni_va_en_ingles(monkeypatch, tmp_path):
    monkeypatch.delenv("REELFY_PICK_FOLDER_CMD", raising=False)
    osa = _fake(tmp_path, 'echo "execution error: Not authorized to send Apple events to System Events. (-1743)" >&2; exit 1',
                "osascript")
    logs = []
    r = EK.run_picker(None, platform="darwin", osascript=osa, log=logs.append)
    assert r == {"path": None, "canceled": False, "error": EK.PICK_FAIL} and "-1743" in logs[-1]
    osa = _fake(tmp_path, 'echo "execution error: User canceled. (-128)" >&2; exit 1', "osascript")
    assert EK.run_picker(None, platform="darwin", osascript=osa)["canceled"] is True


def test_mostrar_en_finder_por_plataforma(monkeypatch):
    import subprocess
    monkeypatch.delenv("REELFY_OPEN_CMD", raising=False)
    w = {"open": "/usr/bin/open", "xdg-open": "/usr/bin/xdg-open"}.get
    assert EK.reveal_cmd("/a/b.mp4", "reveal", "darwin", w) == ["/usr/bin/open", "-R", "/a/b.mp4"]
    assert EK.reveal_cmd("/a/b.mp4", "open", "darwin", w) == ["/usr/bin/open", "/a/b.mp4"]
    # Windows: "/select," y la ruta SEPARADOS. Juntos, list2cmdline citaba "/select,C:\a b\c.mp4"
    # entero (la ruta tiene espacios) y el Explorador abria Documentos sin marcar el archivo
    win = EK.reveal_cmd("C:\\Videos\\Reelfy\\Promo - 2026-10-01.mp4", "reveal", "win32", w)
    assert win == ["explorer", "/select,", "C:\\Videos\\Reelfy\\Promo - 2026-10-01.mp4"]
    assert subprocess.list2cmdline(win) == 'explorer /select, "C:\\Videos\\Reelfy\\Promo - 2026-10-01.mp4"'
    assert EK.reveal_cmd("C:\\c.mp4", "open", "win32", w) is None          # os.startfile
    assert EK.reveal_cmd("/a/b.mp4", "reveal", "linux", w) == ["/usr/bin/xdg-open", "/a"]
    assert EK.reveal_label("darwin") == "Mostrar en Finder" and EK.reveal_label("win32") == "Mostrar en el Explorador"
    assert EK.reveal_label("linux") == "Abrir carpeta"
    # el mensaje de error nombra la app de ESTA plataforma (antes decia "Finder" en Windows y Linux)
    assert EK.reveal_fail_msg("reveal", "darwin") == "No se pudo abrir el Finder en este equipo."
    assert EK.reveal_fail_msg("reveal", "win32") == "No se pudo abrir el Explorador de archivos en este equipo."
    assert "Finder" not in EK.reveal_fail_msg("reveal", "linux")
    assert EK.reveal_fail_msg("open", "linux") == "No se pudo abrir el video en este equipo."
    monkeypatch.setenv("REELFY_OPEN_CMD", "/bin/echo")
    assert EK.reveal_cmd("/a/b.mp4", "open") == ["/bin/echo", "open", "/a/b.mp4"]


@pytest.mark.parametrize("en,want", [
    (errno.ENOSPC, "No hay espacio en «USB» para guardar el video"),
    (errno.EIO, "Se perdió la conexión con «USB» (¿se desconectó el disco?)"),
    (errno.EROFS, "«USB» es de solo lectura"),
    (errno.EACCES, "Reelfy no tiene permiso para escribir en «USB»"),
    (errno.EPERM, "Reelfy no tiene permiso para escribir en «USB»"),
    (errno.ENAMETOOLONG, "El nombre del archivo es demasiado largo para «USB»: usa uno más corto"),
    (errno.ENOENT, "La carpeta «USB» ya no está disponible (¿se desconectó el disco?)"),
    (errno.EFBIG, "El video es demasiado grande para el disco de «USB» (en FAT32 cada archivo llega hasta 4 GB)"),
    (errno.EINVAL, "No se pudo guardar en «USB»")])
def test_errores_al_guardar_en_espanol(en, want):
    """Regresion: el error del sistema ("Input/output error") iba dentro del mensaje en espanol."""
    e = OSError(en, os.strerror(en), "/Volumes/USB/x.mp4")
    t = EK.save_error_text(e, "USB")
    assert t == want and os.strerror(en) not in t


def test_barrer_copias_a_medias(tmp_path):
    """Regresion: una copia a otro volumen interrumpida (Reelfy cerrado a mitad de "Guardando en
    «USB»…") dejaba un ".<nombre>.<pid>.<hilo>.reelfy-part" oculto de varios GB en la carpeta."""
    import time as _t
    now = _t.time()
    dead = tmp_path / f".Promo - 2026-10-01.999999.123{EK.PART_SUFFIX}"         # proceso que ya no existe
    dead.write_bytes(b"x" * 1000)
    old = tmp_path / f".Viejo.{os.getpid()}.1{EK.PART_SUFFIX}"                # vivo pero sin escribir
    old.write_bytes(b"x" * 10)
    os.utime(old, (now - EK.PART_MAX_AGE - 60,) * 2)
    live = tmp_path / f".Copiando.{os.getpid()}.2{EK.PART_SUFFIX}"            # la copia en curso
    live.write_bytes(b"x" * 10)
    probe_old = tmp_path / ".reelfy-abc.tmp"
    probe_old.write_bytes(b"")
    os.utime(probe_old, (now - EK.PART_MAX_AGE - 60,) * 2)
    probe_new = tmp_path / ".reelfy-def.tmp"
    probe_new.write_bytes(b"")
    keep = [tmp_path / "Promo.mp4", tmp_path / ".oculto.reelfy-part", tmp_path / ".otro.tmp"]
    for k in keep:
        k.write_bytes(b"y")
    os.utime(keep[1], (now - 10 * EK.PART_MAX_AGE,) * 2)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / dead.name).write_bytes(b"z")                                        # sin bajar a subcarpetas
    if os.getuid() != 0:
        assert not EK._pid_alive(999999)
    freed = EK.sweep_parts(tmp_path, now=now)
    assert not dead.exists() and not old.exists() and not probe_old.exists()
    assert live.exists() and probe_new.exists() and all(k.exists() for k in keep) and (sub / dead.name).exists()
    assert freed == 1010
    assert EK.sweep_parts(tmp_path / "no_existe") == 0


# ---------------------------------------------------------------- ajustes recordados
def test_ajustes_carpetas_permitidas_y_exportados(tmp_path, monkeypatch):
    monkeypatch.setenv("REELFY_EXPORT_DIR", str(tmp_path / "Movies" / "Reelfy"))
    st = EK.Store(tmp_path / "export.json")
    assert st.load() == {} and st.allowed_folders() == {str(EK.default_folder())}
    st.add_picked("/Volumes/USB/Videos")
    assert "/Volumes/USB/Videos" in st.allowed_folders()
    assert st.load()["last"]["folder"] == "/Volumes/USB/Videos"
    st.remember(res="4k", quality="maxima", shorts=False, fmt="1:1", folder=None)
    last = st.load()["last"]
    assert last == {"folder": "/Volumes/USB/Videos", "res": "4k", "quality": "maxima", "shorts": False, "fmt": "1:1"}
    for k in range(25):
        st.add_picked(f"/p{k}")
    assert len(st.load()["picked"]) == EK.Store.PICKED_MAX
    st.add_exported(["/x/a.mp4", None, "/x/a - Short 1.mp4"], "job1")
    assert st.is_exported("/x/a.mp4") and st.is_exported("/x/a - Short 1.mp4") and not st.is_exported("/x/b.mp4")
    st.add_perf("render", 0.01)
    st.add_perf("render", float("nan"))
    st.add_perf("render", -1)
    assert st.load()["perf"]["render"] == [0.01]
    (tmp_path / "export.json").write_text("{roto")
    assert st.load() == {}                                    # un archivo roto no tumba nada


# ---------------------------------------------------------------- estimacion
def test_estimacion_por_defecto_y_medida():
    e = EK.estimate(W=1080, H=1920, fps=30, frames=2220)
    assert e["basis"] == "estimado" and e["parts"]["base"] == 0 and e["parts"]["recompose"] == 0
    assert abs(e["parts"]["render"] - 2220 * 2.0736 * EK.SPFM_DEFAULTS["render"]) < 0.5
    br = EK.video_bps(1080, 1920, 30) + EK.AUDIO_BPS
    assert abs(e["bytes_main"] - br / 8 * 74.0) < 1000
    assert e["text"].startswith("Tardará ") and " · ~" in e["text"] and e["text"].endswith("MB")
    k4 = EK.estimate(W=2160, H=3840, fps=30, frames=2220, base_cold=True, rec_frames=2450,
                     rec_wh=(2160, 3840), shorts_frames=600)
    assert k4["seconds"] > 4 * e["seconds"] and k4["parts"]["recompose"] > 0 and k4["bytes_shorts"] > 0
    assert 400e6 < k4["bytes_main"] < 600e6                    # ~500 MB para 74 s en 4K (medido)
    perf = {"render": [0.004, 0.005, 0.006], "size_ratio": [0.9]}
    m = EK.estimate(W=1080, H=1920, fps=30, frames=2220, perf=perf)
    assert m["basis"] == "medido" and m["parts"]["render"] < e["parts"]["render"]
    assert m["bytes_main"] < e["bytes_main"]
    assert EK.estimate(W=1080, H=1920, fps=30, frames=2220, quality="maxima")["bytes"] > e["bytes"] > \
        EK.estimate(W=1080, H=1920, fps=30, frames=2220, quality="estandar")["bytes"]
    assert EK.estimate(W=1080, H=1920, fps=30, frames=2220, copy=True)["parts"]["save"] > 0


def test_textos_de_tiempo_y_tamano():
    assert EK.fmt_minutes(20) == "menos de 1 min" and EK.fmt_minutes(61) == "~2 min"
    assert EK.fmt_minutes(3600) == "~1 h" and EK.fmt_minutes(4000) == "~1 h 7 min"
    assert EK.fmt_bytes(5.4e6) == "~5 MB" and EK.fmt_bytes(523e6) == "~520 MB" and EK.fmt_bytes(1.26e9) == "~1.3 GB"


def test_calidad_alta_es_la_de_siempre_en_el_encoder(monkeypatch):
    from motion import video_io as VIO
    monkeypatch.delenv("REELFY_FINAL_BPP", raising=False)
    monkeypatch.delenv("REELFY_FINAL_CRF", raising=False)
    assert VIO.final_bpp() == EK.QUALITY["alta"]["bpp"] == 0.22 and VIO.final_crf() == EK.QUALITY["alta"]["crf"]
    base = VIO.encoder_args(1080, 1920, 30, "final", force_sw=True)
    assert base[base.index("-crf") + 1] == "18"
    monkeypatch.setenv("REELFY_FINAL_BPP", str(EK.QUALITY["maxima"]["bpp"]))
    monkeypatch.setenv("REELFY_FINAL_CRF", str(EK.QUALITY["maxima"]["crf"]))
    a = VIO.encoder_args(1080, 1920, 30, "final", force_sw=True)
    assert a[a.index("-crf") + 1] == "15"
    inter = VIO.encoder_args(1080, 1920, 30, "inter", force_sw=True)       # intermedios: sin cambio
    assert inter[inter.index("-crf") + 1] == "12"
    if VIO.videotoolbox_ok():
        hw = VIO.encoder_args(1080, 1920, 30, "final")
        assert int(hw[hw.index("-b:v") + 1]) == EK.video_bps(1080, 1920, 30, "maxima")
    monkeypatch.setenv("REELFY_FINAL_BPP", "basura")
    assert VIO.final_bpp() == 0.22
