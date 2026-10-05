"""«Quién sale en el video» (perfiles guardados) y los dialogos propios del Studio.

Reporte de Kevin (app de escritorio, macOS): «los perfiles de "Quién sale en el video" no los puedes
eliminar o actualizar/re-generar». Causa de «no se pueden eliminar»: la app es Tauri 2 + wry
(WKWebView) y wry NO implementa los paneles de JavaScript, asi que window.confirm() devuelve false al
instante sin mostrar nada (el boton de eliminar no hacia nada), alert() no se ve y prompt() devuelve
null (plantillas, clave de Pexels, salir sin guardar...). El Studio ahora usa sus propios dialogos
(app/static/dialog.js) y:
  - test_sin_dialogos_nativos_en_la_app: GUARDA. Falla si confirm( / alert( / prompt( vuelven a aparecer
    en el Studio (app/static: .js y los <script> de index.html) o en la cascara de escritorio.
  - test_dialogo_propio_cableado: dialog.js se carga antes que profile.js y el script en linea, con su
    ?v=<sha1> (el WKWebView de la app guarda en cache los estaticos), y cada sitio usa Dlg.
  - test_profile_e2e_en_navegador: E2E en Chrome real con confirm/alert/prompt sustituidos por lo que
    hace WebKit y, otra vez, con los dialogos nativos de Chrome sin tocar (tests/js/profile_e2e.mjs;
    REELFY_PLAYWRIGHT).
  - test_profile_e2e_en_webkit: el mismo flujo dentro de un WKWebView de verdad
    (tests/webkit/wkflow.swift + tests/webkit/profile_flow.js; REELFY_WEBKIT_E2E=1, macOS con swiftc).
Ambos E2E corren contra tests/profile_devserver.py (datos aislados, red ficticia, proyecto sintetico).
Solo datos ficticios.
"""
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1]
STATIC = SPIKE / "app" / "static"
DESKTOP = SPIKE.parent / "reelfy-desktop" / "src"
NODE = shutil.which("node")

# referencia a un dialogo nativo: confirm / alert / prompt sueltos o de window. self. globalThis. top.
# parent. frames. (con . o ?.), llamados o no (un alias `const ask = window.confirm`, confirm.call(...),
# confirm\n(...)); no Dlg.confirm, ni _ocr_confirm, ni una clave `prompt:` ni "prompt" como dato
_G = r"(?:window|self|globalThis|top|parent|frames)"
NATIVE = re.compile(r"(?<![\w$.])(?:" + _G + r"\s*(?:\?\.|\.)\s*)?(confirm|alert|prompt)\b(?!\s*:)")
NATIVE_IDX = re.compile(_G + r"""\s*(?:\?\.)?\s*\[\s*["'`](?:confirm|alert|prompt)["'`]\s*\]""")
# manejador en linea en HTML (en un .html o dentro de un texto JS que arma HTML): onclick="..." / '...'
HANDLER = re.compile(r"""(?<![\w$.-])on[a-z]+\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"'`]+))""", re.I)


_RX_BEFORE = re.compile(r"(?:^|[(,=:\[!&|?{};+\-*%<>~^]|\b(?:return|typeof|case|in|of|void|delete|new|else|do|instanceof))\s*$")


def _js_code(src: str, keep_strings: bool = False, strings: list | None = None) -> str:
    """El codigo JS sin comentarios ni el CONTENIDO de los textos y expresiones regulares (un "image/*" o
    una url no abren un comentario); lo que va dentro de ${...} en una plantilla SI es codigo.
    keep_strings: los textos entre comillas se quedan (para window["confirm"]). `strings`: ahi se juntan
    los textos (las plantillas con ${...} como 0) para buscar manejadores en linea (onclick="...")."""
    out: list[str] = []
    n = len(src)

    def unq(t, q):
        return t.replace("\\" + q, q)

    def skip_str(i, q):
        i += 1
        while i < n and src[i] != q and src[i] != "\n":
            i += 2 if src[i] == "\\" else 1
        return i + 1

    def skip_regex(i):
        i += 1
        cls = False
        while i < n and src[i] != "\n":
            ch = src[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "[":
                cls = True
            elif ch == "]":
                cls = False
            elif ch == "/" and not cls:
                i += 1
                break
            i += 1
        while i < n and src[i].isalpha():
            i += 1
        return i

    def tpl(i):
        text: list[str] = []
        while i < n:
            if src[i] == "\\":
                text.append(src[i + 1:i + 2])
                i += 2
                continue
            if src[i] == "`":
                if strings is not None:
                    strings.append("".join(text))
                return i + 1
            if src[i:i + 2] == "${":
                out.append(" ")
                text.append("0")
                i = code(i + 2, True)
                out.append(" ")
                continue
            text.append(src[i])
            i += 1
        if strings is not None:
            strings.append("".join(text))
        return i

    def code(i, in_tpl=False):
        depth = 0
        while i < n:
            c, two = src[i], src[i:i + 2]
            if two == "//":
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            if two == "/*":
                j = src.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if c in "'\"":
                j = skip_str(i, c)
                out.append(src[i:j] if keep_strings else '""')
                if strings is not None:
                    strings.append(unq(src[i + 1:j - 1], c))
                i = j
                continue
            if c == "`":
                i = tpl(i + 1)
                out.append("``")
                continue
            if c == "/" and _RX_BEFORE.search("".join(out[-40:])):
                i = skip_regex(i)
                out.append("/r/")
                continue
            if in_tpl:
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        return i + 1
                    depth -= 1
            out.append(c)
            i += 1
        return i

    code(0)
    return "".join(out)


def _handlers(text: str) -> list[str]:
    """Cuerpos de los manejadores en linea (onclick="..." / '...' / sin comillas) de un texto HTML."""
    return [next(g for g in m.groups() if g is not None) for m in HANDLER.finditer(text)]


def _native_calls(code: str, depth: int = 0) -> list[str]:
    """Referencias a confirm / alert / prompt nativos en un codigo JS (y en los manejadores en linea de
    los textos que arman HTML) -> ['linea: fragmento']."""
    out = []
    strings: list[str] = []
    js = _js_code(code, strings=strings)
    for m in NATIVE.finditer(js):
        ln = js.count("\n", 0, m.start()) + 1
        a = js.rfind("\n", 0, m.start()) + 1
        b = js.find("\n", m.end())
        out.append(f"{ln}: {js[a:b if b >= 0 else len(js)].strip()[:140]}")
    kept = _js_code(code, keep_strings=True)
    for m in NATIVE_IDX.finditer(kept):
        out.append(f"{kept.count(chr(10), 0, m.start()) + 1}: {m.group(0)[:140]}")
    if depth < 3:
        for t in strings:
            for h in _handlers(t):
                out += [f"en linea {h[:60]!r} -> {x}" for x in _native_calls(h, depth + 1)]
    return out


def _html_sources(name: str, html: str) -> list[tuple[str, str]]:
    """(nombre, codigo JS) de un .html: sus <script> y sus manejadores en linea."""
    out = [(f"{name} <script {k}>", blk) for k, blk in
           enumerate(re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S | re.I))]
    rest = re.sub(r"<script(?:\s[^>]*)?>.*?</script>", " ", html, flags=re.S | re.I)
    out += [(f"{name} atributo {k}", h) for k, h in enumerate(_handlers(rest))]
    return out


def _app_sources():
    """(nombre, codigo JS) del Studio y de la cascara de escritorio."""
    out = []
    for f in sorted(STATIC.glob("*.js")):
        out.append((f"app/static/{f.name}", f.read_text()))
    for f in sorted(STATIC.glob("*.html")):
        out += _html_sources(f"app/static/{f.name}", f.read_text())
    if DESKTOP.exists():
        for f in sorted(DESKTOP.glob("*.js")):
            out.append((f"reelfy-desktop/src/{f.name}", f.read_text()))
        for f in sorted(DESKTOP.glob("*.html")):
            out += _html_sources(f"reelfy-desktop/src/{f.name}", f.read_text())
    return out


def test_la_guarda_detecta_dialogos_nativos():
    """La guarda encuentra las formas reales (las que habia en el Studio y las que se le escapaban: un
    manejador en linea dentro de un texto que arma HTML, partida en dos lineas, ?.(), .call, un alias,
    top./parent., atributos con comillas simples) y no confunde las propias."""
    bad = ['if (!confirm(`¿Eliminar el perfil guardado "x"?`)) return;', 'alert("No se pudo subir el logo");',
           'const k = prompt("Pega tu clave");', "if(!ok&&!window.confirm('¿Salir?')) return;", "self.alert(1)",
           "globalThis.prompt('x')", "window['confirm']('x')", "const f = window[\"alert\"];",
           # tras un "image/*" (no es un comentario) y dentro de ${...} de una plantilla
           'fi = Object.assign(el, {accept:"image/*"});\ncatch(e){ alert("No se pudo subir el logo"); }',
           "box.innerHTML = `<b>${confirm('x') ? 1 : 0}</b>`;",
           # manejadores en linea dentro de textos que arman HTML (comillas dobles, simples, con ${...})
           "box.innerHTML = `<button onclick=\"return confirm('x')\">Borrar</button>`;",
           "box.innerHTML = '<button onclick=\"return confirm(\\'x\\')\">Borrar</button>';",
           "el.innerHTML = `<a onclick='alert(1)'>x</a>`;",
           "row.innerHTML = `<i onclick=\"del(${i}); prompt('n')\"></i>`;",
           # partida en dos lineas, ?.(), .call, alias, top./parent., window?.
           'confirm\n("¿Seguro?")', "window.confirm?.('x')", "confirm.call(window, 'x')",
           "const ask = window.confirm; ask('x')", "top.confirm('x')", "parent.alert('x')", "window?.prompt('x')",
           "const o = { confirm };", "top['prompt']('x')"]
    good = ["Dlg.confirm({title: 'x'})", "await Dlg.prompt({label: 'Nombre'})", "D._ocr_confirm(ent, shots)",
            "music.prompt = t;", '"music.prompt"', "// confirm( en un comentario", "/* alert( */ x()",
            "p.prompt ? 1 : 0", "build_prompt(style)", "inp.placeholder = T('mus.prompt.ph')",
            "const url = 'https://x.test/alert('", "x.replace(/confirm\\(/g, '')", "a = b / c; alert2()",
            "const m = { prompt: m.prompt, gain: 1 };", "if (kind === 'alert') x();", "n.innerHTML = `${ICO(\"alert\")}<b>x</b>`;",
            "box.innerHTML = `<button onclick=\"Dlg.confirm({title:'x'})\">x</button>`;",
            "el.innerHTML = '<p role=\"alert\">confirm alert prompt</p>';", "D.confirm = o => D.open(o);"]
    for b in bad:
        assert _native_calls(b), b
    for g in good:
        assert not _native_calls(g), (g, _native_calls(g))
    # atributos de un .html: comillas dobles, simples y sin comillas; y el codigo de sus <script>
    for html in ('<button onclick="confirm(1)">x</button>', "<button onclick='alert(\"x\")'>x</button>",
                 "<body onload=prompt(1)>", "<script>\nwindow.confirm?.('x')\n</script>"):
        srcs = _html_sources("x.html", html)
        assert any(_native_calls(c) for _n, c in srcs), html
    for html in ('<button onclick="Dlg.confirm({})">x</button>', '<div data-onclick="confirm(1)" role="alert"></div>'):
        assert not any(_native_calls(c) for _n, c in _html_sources("x.html", html)), html


def test_sin_dialogos_nativos_en_la_app():
    """GUARDA: en la app de escritorio (Tauri/wry, WKWebView) confirm/alert/prompt no muestran nada
    (confirm -> false: «Eliminar perfil» no hacia nada). El Studio usa Dlg (app/static/dialog.js)."""
    srcs = _app_sources()
    assert any(n == "app/static/profile.js" for n, _ in srcs) and any("index.html <script" in n for n, _ in srcs)
    hits = {n: _native_calls(c) for n, c in srcs}
    hits = {n: h for n, h in hits.items() if h}
    assert not hits, "dialogos nativos (no funcionan en la app de escritorio; usa Dlg.confirm / Dlg.prompt / " \
                     "Dlg.alert de dialog.js): " + json.dumps(hits, ensure_ascii=False, indent=1)


def test_dialogo_propio_cableado():
    html = (STATIC / "index.html").read_text()
    # dialog.js una vez, con la version de su contenido (el WKWebView de la app guarda en cache los
    # estaticos), antes de profile.js y del script en linea (profile.js y compania:
    # test_studio_ui.test_version_de_assets_es_el_hash_del_contenido)
    h = hashlib.sha1((STATIC / "dialog.js").read_bytes()).hexdigest()[:8]
    vs = re.findall(r"/static/dialog\.js\?v=([0-9a-f]+)", html)
    assert vs == [h], f"dialog.js: version {vs} != {h} (el WKWebView de la app guardaria la vieja)"
    i_dlg, i_pf = html.index("/static/dialog.js"), html.index("/static/profile.js")
    assert i_dlg < i_pf < html.index("<script>")
    dj = (STATIC / "dialog.js").read_text()
    for k in ("D.confirm =", "D.prompt =", "D.alert =", "D.choose =", 'aria-modal", "true"', '"alertdialog"',
              'e.key === "Escape"', 'e.key === "Tab"', 'e.key === "Enter"', "prev.focus(", "z-index:95"):
        assert k in dj, k
    pf = (STATIC / "profile.js").read_text()
    assert "await Dlg.confirm({" in pf and 'ok: "Eliminar perfil", danger: true' in pf
    assert "Dlg.choose({" in pf and "/profile/normalize" in pf and "function syncAll()" in pf
    # cada dialogo que habia en el Studio usa ahora el propio
    for k in ('Dlg.alert({title:"Falta un clip"', 'Dlg.prompt({title:"Clave de Pexels"', 'Dlg.alert({title:"No se pudo subir el logo"',
              'Dlg.prompt({title:"Guardar plantilla de tu marca"', 'Dlg.confirm({title:`¿Eliminar la plantilla', 'Dlg.confirm({title:"Tus últimos cambios no se guardaron"'):
        assert k in html, k
    if NODE:
        r = subprocess.run([NODE, "--check", str(STATIC / "dialog.js")], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr


def _free_port():
    for p in range(8900, 9000):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise AssertionError("sin puerto libre en 8900-8999")


class _Server:
    """tests/profile_devserver.py con datos propios (red ficticia + proyecto pf_demo)."""

    def __init__(self, data):
        self.port = _free_port()
        self.proc = subprocess.Popen([sys.executable, str(SPIKE / "tests" / "profile_devserver.py"), str(self.port), str(data), "--demo"],
                                     cwd=str(SPIKE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(240):
            try:
                if urllib.request.urlopen(self.base + "/setup", timeout=2).status == 200:
                    return
            except Exception:  # noqa: aun arrancando
                time.sleep(0.5)
        self.stop()
        raise AssertionError("el servidor de prueba no arranco")

    def stop(self):
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)


def test_profile_e2e_en_navegador(tmp_path):
    """Chrome real con confirm/alert/prompt como en WebKit (false / nada / null) a 1440x900 y 1200x760,
    y con los dialogos nativos de Chrome a 1440x900: crear, guardar, reabrir, editar sin copias, cambiar
    el @usuario y el WhatsApp (foto y numero nuevos, nunca los viejos), aplicar al video (re-genera
    rotulo, Seguir, marca de agua, cierre y foto), volver a buscar la foto, otra version del guardado
    (nunca se pisa sin preguntar), mismo nombre («Guardar otro aparte» con Enter; «Completar ese perfil»
    conserva todo), doble clic, salir con cambios, eliminar con la confirmacion propia (teclado: Cancelar
    enfocado, Tab atrapado, Escape, Enter; despues ⌘Z no deshace detras), listas al dia sin recargar,
    plantillas, salir sin guardar, Montar clips sin clips y la clave de Pexels; dialogos simetricos y
    claros."""
    if not (os.environ.get("REELFY_PLAYWRIGHT") and NODE):
        pytest.skip("E2E del perfil desactivado (define REELFY_PLAYWRIGHT)")
    srv = _Server(tmp_path / "data")
    try:
        out = os.environ.get("REELFY_E2E_SHOTS") or str(tmp_path / "shots")
        r = subprocess.run([NODE, str(SPIKE / "tests" / "js" / "profile_e2e.mjs"), srv.base, out],
                           capture_output=True, text=True, timeout=1500, env={**os.environ})
        assert r.returncode == 0, r.stdout[-8000:] + r.stderr[-3000:]
    finally:
        srv.stop()


def test_profile_e2e_en_webkit(tmp_path):
    """El mismo flujo DENTRO de un WKWebView (el motor de la app de escritorio), sin delegado para los
    paneles de JavaScript (como wry): ningun dialogo nativo y todo funciona. Inicio y Studio, a 1440x900
    y 1200x760. Se activa con REELFY_WEBKIT_E2E=1 (macOS con swiftc; abre una ventana casi invisible)."""
    if os.environ.get("REELFY_WEBKIT_E2E") != "1" or sys.platform != "darwin" or not shutil.which("swiftc"):
        pytest.skip("E2E en WebKit desactivado (REELFY_WEBKIT_E2E=1 en macOS con swiftc)")
    exe = tmp_path / "wkflow"
    subprocess.run(["swiftc", "-O", str(SPIKE / "tests" / "webkit" / "wkflow.swift"), "-o", str(exe)], check=True,
                   capture_output=True, timeout=600)
    srv = _Server(tmp_path / "data")
    try:
        out = Path(os.environ.get("REELFY_E2E_SHOTS") or tmp_path / "shots")
        for w, h in ((1440, 900), (1200, 760)):
            for url in ("/", "/?job=pf_demo"):
                r = subprocess.run([str(exe), srv.base + url, str(SPIKE / "tests" / "webkit" / "profile_flow.js"),
                                    str(out / f"webkit_{w}x{h}"), str(w), str(h), "300"],
                                   capture_output=True, text=True, timeout=400)
                line = (r.stdout.strip().splitlines() or ["{}"])[-1]
                rep = json.loads(line)
                assert rep.get("ok"), f"{url} {w}x{h}: {rep.get('error')} " + json.dumps(rep.get("steps", [])[-3:], ensure_ascii=False)
                assert len(rep["steps"]) >= 30 and all(s["ok"] for s in rep["steps"])
    finally:
        srv.stop()
