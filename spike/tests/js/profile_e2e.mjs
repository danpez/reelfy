// E2E de «Quién sale en el video» (perfiles guardados) y de los dialogos propios en Chrome real
// (Playwright), en DOS modos: COMO en la app de escritorio (window.confirm / alert / prompt sustituidos
// por lo que hace el WKWebView de Tauri/wry: confirm -> false, alert -> nada, prompt -> null, sin mostrar
// nada, y contados; si la app vuelve a depender de ellos, el flujo falla) a 1440x900 y 1200x760, y con
// los dialogos NATIVOS de Chrome sin tocar a 1440x900. En los dos Chrome no debe abrir NINGUN dialogo
// nativo (page.on("dialog")).
// uso: REELFY_PLAYWRIGHT=<ruta a playwright-core> node tests/js/profile_e2e.mjs <url base> [capturas]
// El servidor es tests/profile_devserver.py <puerto> <datos> --demo (red ficticia + proyecto pf_demo).
// A 1440x900 y 1200x760:
//  1. El flujo compartido con WebKit (tests/webkit/profile_flow.js) en Inicio y en el Studio: crear,
//     guardar, reabrir, editar sin copias, cambiar el @usuario (foto de la cuenta nueva), aplicar al video
//     (rotulo, Seguir, marca de agua y foto re-generados), volver a buscar la foto, «Usar la versión
//     guardada», eliminar con la confirmacion propia, listas al dia, plantillas, salir sin guardar,
//     Montar clips sin clips y la clave de Pexels.
//  2. Teclado y raton DE VERDAD en el dialogo: el foco empieza en Cancelar al borrar, Tab / Mayus+Tab dan
//     la vuelta dentro, Escape cancela y el foco vuelve al boton, Enter activa el boton enfocado, Enter en
//     el campo acepta, y ⌘Z con el dialogo abierto NO deshace nada en el editor. Mismo nombre: Enter =
//     «Guardar otro aparte» (nunca pisa el guardado). Escape con cambios: pregunta (enfocado «Seguir
//     editando»). Doble clic en «Guardar perfil»: un solo perfil. Tras eliminar desde el Studio el foco
//     sigue en el panel: ⌘Z no deshace detras y Escape lo cierra.
//  3. Simetria medida del dialogo (caja centrada, mismo relleno, botones del mismo ancho y el grupo, el
//     icono y el titulo centrados) y lint de claridad (sin ingles ni jerga, sin emojis, letra >= 11 px).
// Sale con 1 al primer fallo.
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { HELPERS, LINT } from "./e2e_claridad.mjs";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("define REELFY_PLAYWRIGHT (ruta a playwright-core)"); process.exit(2); }
const { chromium } = require(PW);
const BASE = (process.argv[2] || "http://127.0.0.1:8961").replace(/\/$/, "");
const OUT = process.argv[3] || null;
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const HERE = path.dirname(fileURLToPath(import.meta.url));
const FLOW = fs.readFileSync(path.join(HERE, "..", "webkit", "profile_flow.js"), "utf8");
const log = (...a) => console.log("·", ...a);
const report = { viewports: {} };

// lo que hace el WKWebView de la app (wry no implementa los paneles de JavaScript), contado
const WK_DIALOGS = () => {
  window.__nativeDialogs = [];
  window.confirm = m => { window.__nativeDialogs.push("confirm: " + String(m).slice(0, 120)); return false; };
  window.alert = m => { window.__nativeDialogs.push("alert: " + String(m).slice(0, 120)); };
  window.prompt = m => { window.__nativeDialogs.push("prompt: " + String(m).slice(0, 120)); return null; };
};

/** Medidas de simetria del dialogo abierto (px). */
const SYM = () => {
  const w = document.querySelector(".rdlgw"); if (!w) return null;
  const box = w.querySelector(".rdlg"), b = box.getBoundingClientRect(), cs = getComputedStyle(box);
  const mid = r => (r.left + r.right) / 2, bm = mid(b);
  const rg = document.createRange(); rg.selectNodeContents(box.querySelector("h3")); const t = rg.getBoundingClientRect();
  const btns = [...box.querySelectorAll(".rbtns .btn")].map(x => x.getBoundingClientRect());
  const grp = box.querySelector(".rbtns").getBoundingClientRect();
  const icon = box.querySelector(".rdi").getBoundingClientRect();
  const rows = {}; btns.forEach(r => { (rows[Math.round(r.top)] = rows[Math.round(r.top)] || []).push(r); });
  let rowOff = 0; Object.values(rows).forEach(r => { r.sort((a, c) => a.left - c.left); rowOff = Math.max(rowOff, Math.abs((r[0].left - grp.left) - (grp.right - r[r.length - 1].right))); });
  return {
    page: Math.abs(b.left - (innerWidth - b.right)), pageV: Math.abs(b.top - (innerHeight - b.bottom)),
    pad: Math.abs(parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)),
    title: Math.abs(mid(t) - bm), icon: Math.abs(mid(icon) - bm), group: Math.abs(mid(grp) - bm), rows: rowOff,
    widths: btns.length > 1 && Object.values(rows).every(r => r.length > 1) ? Math.max(...btns.map(r => r.width)) - Math.min(...btns.map(r => r.width)) : 0,
    over: document.documentElement.scrollWidth - document.documentElement.clientWidth, n: btns.length,
  };
};
const symOk = s => s && s.page <= 1 && s.pad <= 0.5 && s.title <= 1.5 && s.icon <= 1 && s.group <= 1 && s.rows <= 1 && s.widths <= 1 && s.over <= 0;

const browser = await chromium.launch({ headless: true, channel: process.env.REELFY_PW_CHANNEL || "chrome",
  ...(process.env.REELFY_PW_EXE ? { executablePath: process.env.REELFY_PW_EXE, channel: undefined } : {}) });
let exitCode = 0;
try {
  for (const [MODE, VW, VH] of [["wk", 1440, 900], ["wk", 1200, 760], ["nativo", 1440, 900]]) {
    const tag = MODE === "wk" ? `${VW}x${VH}` : `${MODE}_${VW}x${VH}`, rep = report.viewports[tag] = { mode: MODE, flows: {}, sym: {}, lint: {} };
    const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, deviceScaleFactor: 1 });
    if (MODE === "wk") await ctx.addInitScript(WK_DIALOGS);
    const page = await ctx.newPage();
    const errs = [], natives = [];
    page.on("pageerror", e => errs.push("pageerror " + e.message));
    page.on("console", m => { if (m.type() === "error" && !/status of (40\d|50\d)/.test(m.text())) errs.push("console " + m.text()); });
    page.on("dialog", d => { natives.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
    // (la entrada de un dialogo ya termino: la captura lo muestra completo)
    await page.exposeFunction("__snap", async n => { if (OUT) { await page.waitForTimeout(300); await page.screenshot({ path: path.join(OUT, `${n}_${tag}.png`) }); } return true; });
    const ev = (fn, arg) => page.evaluate(fn, arg);
    const wait = ms => page.waitForTimeout(ms);
    const shot = async n => { if (OUT) await page.screenshot({ path: path.join(OUT, `${n}_${tag}.png`) }); };
    const flow = async (url, name) => {
      await page.goto(url);
      const r = JSON.parse(await page.evaluate(`(async () => {\n${FLOW}\n})()`));
      rep.flows[name] = { ok: r.ok, steps: r.steps.length, error: r.error || null };
      r.steps.forEach(s => log(tag, name, s.ok ? "ok " : "MAL", s.name));
      assert.ok(r.ok, `${name}: ${r.error}`);
    };
    const dialogChecks = async name => {
      await page.waitForSelector(".rdlgw .rdlg", { timeout: 5000 });
      await wait(350);                                   // la entrada (pop) ya termino
      const s = await ev(SYM);
      rep.sym[name] = s;
      assert.ok(symOk(s), `simetría del diálogo «${name}»: ${JSON.stringify(s)}`);
      await ev(HELPERS);
      const probs = await ev(LINT, { root: ".rdlgw", level: "basic", chrome: ".rdlgw" });
      rep.lint[name] = probs.length;
      assert.deepEqual(probs, [], `claridad del diálogo «${name}»: ${probs.join(" | ")}`);
      const a = await ev(() => { const w = document.querySelector(".rdlgw"); return { role: w.getAttribute("role"), modal: w.getAttribute("aria-modal"), lab: !!document.getElementById(w.getAttribute("aria-labelledby")), desc: !w.getAttribute("aria-describedby") || !!document.getElementById(w.getAttribute("aria-describedby")), inside: w.contains(document.activeElement) }; });
      assert.ok(/dialog/.test(a.role) && a.modal === "true" && a.lab && a.desc && a.inside, `accesibilidad del diálogo «${name}»: ${JSON.stringify(a)}`);
    };
    const focused = () => ev(() => { const a = document.activeElement; return a ? (a.dataset.a || a.id || a.tagName) : null; });

    // ---------------------------------------------------------------- 1. el flujo compartido con WebKit
    await flow(`${BASE}/`, "inicio");
    await flow(`${BASE}/?job=pf_demo`, "studio");

    // ---------------------------------------------------------------- 2a. teclado de verdad: eliminar (Inicio)
    await ev(async () => {
      for (const p of (await (await fetch("/profiles")).json()).profiles || []) await fetch("/profiles/" + p.id, { method: "DELETE" });
      const parsed = await (await fetch("/profile/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "Soy la Dra. Ana Demo, odontóloga. Instagram https://instagram.com/dra.ana.demo", use_llm: false }) })).json();
      await fetch("/profiles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: parsed.profile }) });
    });
    await page.goto(`${BASE}/`);
    await page.waitForFunction(() => window.PF && document.querySelector("#homeProfSel") && [...document.querySelectorAll("#homeProfSel option")].some(o => o.value === "dra-ana-demo"), null, { timeout: 30000 });
    await page.selectOption("#homeProfSel", "dra-ana-demo");
    await page.click("#homeProfile [data-e]");
    await page.waitForSelector('#pfm.on [data-a="del"]');
    await page.click('#pfm [data-a="del"]');
    await dialogChecks("eliminar perfil");
    assert.equal(await focused(), "cancel", "al borrar, el foco empieza en Cancelar");
    await shot("K_confirmar_eliminar");
    await page.keyboard.press("Tab"); assert.equal(await focused(), "ok", "Tab: «Eliminar perfil»");
    await page.keyboard.press("Tab"); assert.equal(await focused(), "cancel", "Tab da la vuelta dentro del diálogo");
    await page.keyboard.press("Shift+Tab"); assert.equal(await focused(), "ok", "Mayús+Tab hacia atrás");
    await page.keyboard.press("Escape");
    await page.waitForSelector(".rdlgw", { state: "detached" });
    assert.equal(await ev(() => document.activeElement && document.activeElement.dataset.a), "del", "Escape: el foco vuelve al botón de eliminar");
    assert.equal(await ev(async () => ((await (await fetch("/profiles")).json()).profiles || []).length), 1, "Escape no elimina");
    assert.ok(await ev(() => PF.isOpen()), "Escape cierra solo el diálogo, no el panel del perfil");
    await page.keyboard.press("Enter");                       // Enter sobre el boton de eliminar: abre el dialogo
    await page.waitForSelector(".rdlgw .rdlg");
    await page.keyboard.press("Enter");                       // Enter sobre Cancelar (el foco inicial): cancela
    await page.waitForSelector(".rdlgw", { state: "detached" });
    assert.equal(await ev(async () => ((await (await fetch("/profiles")).json()).profiles || []).length), 1, "Enter en Cancelar no elimina");
    await page.click('#pfm [data-a="del"]');
    await page.waitForSelector(".rdlgw .rdlg");
    await page.keyboard.press("Tab"); await page.keyboard.press("Enter");
    await page.waitForFunction(async () => ((await (await fetch("/profiles")).json()).profiles || []).length === 0, null, { timeout: 5000 });
    assert.ok(await ev(() => ![...document.querySelectorAll("#homeProfSel option, #asmProfile select option")].some(o => o.value === "dra-ana-demo") && document.querySelector("#homeProfSel").value === ""), "eliminado con el teclado: fuera de las listas y sin perfil para el próximo video");
    await shot("K_eliminado");
    log(tag, "teclado: eliminar perfil (Cancelar enfocado, Tab atrapado, Escape, Enter)");

    // ---------------------------------------------------------------- 2a'. mismo nombre, salir con cambios y doble clic
    const nsaved = () => ev(async () => ((await (await fetch("/profiles")).json()).profiles || []));
    await ev(async () => {
      const parsed = await (await fetch("/profile/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "Soy la Dra. Ana Demo, odontóloga. Instagram https://instagram.com/dra.ana.demo · Agenda: https://paseo.club/business/dra-ana-demo", use_llm: false }) })).json();
      await fetch("/profiles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: parsed.profile }) });
    });
    await page.goto(`${BASE}/`);
    await page.waitForFunction(() => window.PF && [...document.querySelectorAll("#homeProfSel option")].some(o => o.value === "dra-ana-demo"), null, { timeout: 30000 });
    await page.selectOption("#homeProfSel", "__new");
    await page.waitForSelector('#pfm.on [data-f="name"]');
    await page.fill('#pfm [data-f="name"]', "Dra. Ana Demo");
    await page.fill('#pfm [data-f="title"]', "Ortodoncista");
    await page.click('#pfm [data-a="save"]');
    await dialogChecks("mismo nombre");
    assert.equal(await focused(), "new", "mismo nombre: enfocado «Guardar otro aparte»");
    await shot("K_mismo_nombre");
    await page.keyboard.press("Enter");
    await page.waitForFunction(async () => ((await (await fetch("/profiles")).json()).profiles || []).length === 2, null, { timeout: 5000 });
    const orig = (await nsaved()).find(x => x.id === "dra-ana-demo");
    assert.ok(orig.title === "Odontóloga" && orig.links.length === 2, `Enter no pisó el perfil guardado: ${JSON.stringify(orig)}`);
    // Escape con cambios: pregunta; Enter en «Seguir editando» sigue; Mayus+Tab a «Descartar cambios»
    await page.fill('#pfm [data-f="title"]', "Cardióloga");
    await page.keyboard.press("Escape");
    await dialogChecks("salir con cambios");
    assert.equal(await focused(), "cancel", "salir con cambios: enfocado «Seguir editando»");
    await shot("K_salir_con_cambios");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".rdlgw", { state: "detached" });
    assert.ok(await ev(() => PF.isOpen() && document.querySelector('#pfm [data-f="title"]').value === "Cardióloga"), "«Seguir editando» no pierde lo escrito");
    await page.keyboard.press("Escape");
    await page.waitForSelector(".rdlgw .rdlg");
    await page.keyboard.press("Shift+Tab");
    assert.equal(await focused(), "discard");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !PF.isOpen() && !document.querySelector(".rdlgw"), null, { timeout: 3000 });
    assert.ok((await nsaved()).every(x => x.title !== "Cardióloga"), "«Descartar cambios» no guarda");
    // doble clic de verdad en «Guardar perfil»: un solo perfil
    await page.selectOption("#homeProfSel", "__new");
    await page.waitForSelector('#pfm.on [data-f="name"]');
    await page.fill('#pfm [data-f="name"]', "Dr. Doble Demo");
    await page.dblclick('#pfm [data-a="save"]');
    await page.waitForFunction(async () => ((await (await fetch("/profiles")).json()).profiles || []).some(p => p.name === "Dr. Doble Demo"), null, { timeout: 5000 });
    await wait(800);
    assert.equal((await nsaved()).filter(x => x.name === "Dr. Doble Demo").length, 1, "doble clic: un solo perfil");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !PF.isOpen(), null, { timeout: 3000 });
    log(tag, "teclado: mismo nombre (Enter no pisa), salir con cambios (Seguir editando / Descartar) y doble clic");

    // ---------------------------------------------------------------- 2b. teclado de verdad: plantilla y ⌘Z (Studio)
    await ev(async () => {
      for (const p of (await (await fetch("/profiles")).json()).profiles || []) await fetch("/profiles/" + p.id, { method: "DELETE" });
      const parsed = await (await fetch("/profile/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "Soy la Dra. Ana Demo, odontóloga. Instagram https://instagram.com/dra.ana.demo", use_llm: false }) })).json();
      await fetch("/profiles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: parsed.profile }) });
    });
    await page.goto(`${BASE}/?job=pf_demo`);
    await page.waitForFunction(() => window.FX && FX.isPro && FX.isPro() && document.querySelector("#s-editor.on") && window.Estilo && window.PF, null, { timeout: 60000 });
    await wait(800);
    await ev(async () => { await PF.load(true); PF.syncAll(); Estilo.open("stWho"); });
    await page.waitForSelector("#profUse");
    await page.selectOption("#profUse", "dra-ana-demo");
    await page.waitForFunction(() => PLAN.profile && PLAN.profile.id === "dra-ana-demo", null, { timeout: 10000 });
    await page.click("#profEdit");
    await page.waitForSelector('#pfm.on [data-f="title"]');
    await page.fill('#pfm [data-f="title"]', "Dentista");
    await wait(200);
    await ev(() => document.querySelector("#pfm .pfbtns").scrollIntoView({ block: "end" }));
    await wait(150);
    await shot("K_panel_cambios_sin_guardar");
    // el pie del panel: secundarias del mismo ancho en un renglon, la principal a todo lo ancho, todo en
    // una linea y simetrico
    const foot = await ev(() => {
      const row = document.querySelector("#pfm .pfbtns").getBoundingClientRect(), bs = [...document.querySelectorAll("#pfm .pfbtns .btn")];
      const rs = bs.map(b => b.getBoundingClientRect()), go = rs[rs.length - 1], sec = rs.slice(0, -1);
      return { go: Math.abs(go.left - row.left) + Math.abs(row.right - go.right), widths: sec.length > 1 ? Math.max(...sec.map(r => r.width)) - Math.min(...sec.map(r => r.width)) : 0,
        edges: sec.length ? Math.abs((sec[0].left - row.left) - (row.right - sec[sec.length - 1].right)) : 0,
        cut: bs.filter(b => b.scrollWidth > b.clientWidth + 1).map(b => b.textContent.trim()), label: bs[bs.length - 1].textContent.trim(),
        state: document.querySelector("#pfm .pfstate").textContent };
    });
    rep.foot = foot;
    assert.ok(foot.go <= 1 && foot.widths <= 1 && foot.edges <= 1 && !foot.cut.length && foot.label === "Guardar y aplicar al video" && foot.state === "Cambios sin guardar", `pie del panel: ${JSON.stringify(foot)}`);
    await page.click('#pfm [data-a="apply"]');
    await page.waitForFunction(() => !PF.isOpen() && (PLAN.fx.graphics || []).some(g => g.kind === "lower_third" && g.subtitle === "Dentista"), null, { timeout: 10000 });
    const before = await ev(() => JSON.stringify(PLAN.profile));
    await ev(() => document.querySelector("#presetSave").scrollIntoView({ block: "center" }));
    await page.click("#presetSave");
    await dialogChecks("guardar plantilla");
    assert.equal(await ev(() => document.activeElement === document.querySelector(".rdlgw input")), true, "el campo del nombre empieza enfocado");
    await page.keyboard.press(process.platform === "darwin" ? "Meta+z" : "Control+z");
    await wait(400);
    assert.equal(await ev(() => JSON.stringify(PLAN.profile)), before, "⌘Z con el diálogo abierto no deshace nada en el editor");
    assert.ok(await ev(() => !!document.querySelector(".rdlgw")), "el diálogo sigue abierto");
    await page.keyboard.press("Enter");
    assert.ok(await ev(() => /Escribe un nombre/.test(document.querySelector(".rdlgw .rdl-err").textContent)), "sin nombre: lo pide");
    await page.keyboard.type("Mi marca demo");
    await shot("K_plantilla_nombre");
    await page.keyboard.press("Enter");
    await page.waitForFunction(async () => !document.querySelector(".rdlgw") && Object.keys(await (await fetch("/presets")).json()).includes("Mi marca demo"), null, { timeout: 5000 });
    await page.click("#presetDel");
    await dialogChecks("eliminar plantilla");
    await page.keyboard.press("Enter");                       // foco en Cancelar: no borra
    await page.waitForSelector(".rdlgw", { state: "detached" });
    assert.ok(await ev(async () => Object.keys(await (await fetch("/presets")).json()).includes("Mi marca demo")), "Enter en Cancelar no borra la plantilla");
    await page.click("#presetDel"); await page.waitForSelector(".rdlgw .rdlg");
    await page.click('.rdlgw [data-a="ok"]');
    await page.waitForFunction(async () => !Object.keys(await (await fetch("/presets")).json()).includes("Mi marca demo"), null, { timeout: 5000 });
    // un aviso: Enter lo cierra
    await ev(() => { window.__clips0 = ASM.clips; ASM.clips = []; asmApply(); });
    await dialogChecks("aviso sin clips");
    assert.equal(await focused(), "ok");
    await shot("K_aviso");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".rdlgw", { state: "detached" });
    await ev(() => { ASM.clips = window.__clips0; });
    // eliminar desde el Studio con el teclado: el foco sigue en el panel, ⌘Z no deshace detras y Escape cierra
    await page.click("#profEdit");
    await page.waitForSelector('#pfm.on [data-a="del"]');
    await page.click('#pfm [data-a="del"]');
    await dialogChecks("eliminar perfil (Studio)");
    await page.keyboard.press("Tab"); await page.keyboard.press("Enter");
    await page.waitForFunction(async () => ((await (await fetch("/profiles")).json()).profiles || []).length === 0, null, { timeout: 5000 });
    await page.waitForFunction(() => document.querySelector("#pfm").contains(document.activeElement), null, { timeout: 3000 });
    assert.ok(await ev(() => /Este video conserva sus datos/.test(document.querySelector('#pfm [data-a="msg"]').textContent)));
    const kept = await ev(() => JSON.stringify(PLAN.profile));
    await page.keyboard.press(process.platform === "darwin" ? "Meta+z" : "Control+z");
    await wait(400);
    assert.equal(await ev(() => JSON.stringify(PLAN.profile)), kept, "tras eliminar, ⌘Z no deshace nada detrás del panel");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !PF.isOpen(), null, { timeout: 3000 });
    log(tag, "teclado: plantilla (Enter acepta, ⌘Z no deshace detrás), aviso, eliminar desde el Studio (foco, ⌘Z, Escape)");

    assert.deepEqual(natives, [], "Chrome no abrió ningún diálogo nativo");
    assert.deepEqual(await ev(() => window.__nativeDialogs || []), [], "la app no llamó confirm/alert/prompt");
    assert.deepEqual(errs, [], "consola sin errores");
    await ctx.close();
  }
  if (OUT) fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 1));
  log("OK", JSON.stringify(report));
} catch (e) {
  console.error("FALLA:", e && e.stack || e);
  if (OUT) fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 1));
  exitCode = 1;
} finally {
  await browser.close();
}
process.exit(exitCode);
