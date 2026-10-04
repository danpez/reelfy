// E2E de los videos de apoyo en fragmentos en un navegador real (Playwright + Chrome) contra un servidor
// AISLADO con el motor REAL (sin simulaciones): Inicio -> "Armar con IA" (el video mudo nunca es toma: queda en
// "Videos de apoyo" con cuantas tomas sirven; quitar y deshacer) -> "Aplicar IA" (Minimal) -> Studio: los
// fragmentos en la pista Recursos (nunca una toma de persona, el titulo del producto cuando se nombra, nada
// cuando se habla de otra cosa, cortes secos y color de camara en Minimal), su inspector (por que, formato,
// tomas; cambiar una por otra que sirve) y la vista en vivo -> Exportar con la hoja. Sin errores de consola.
// uso: REELFY_PLAYWRIGHT=<playwright-core> node tests/js/broll_e2e.mjs <url> <carpeta de clips> <capturas> '<info json>' <carpeta export>
// (tests/test_broll_e2e.py genera los medios sinteticos, la info, y revisa el video exportado cuadro a cuadro).
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("define REELFY_PLAYWRIGHT (ruta a playwright-core)"); process.exit(2); }
const { chromium } = require(PW);
const BASE = (process.argv[2] || "http://127.0.0.1:8801").replace(/\/$/, "");
const CLIPDIR = process.argv[3];
const OUT = process.argv[4] || null;
const INFO = JSON.parse(process.argv[5] || "{}");
const XDIR = process.argv[6];
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const FILES = INFO.files.map(f => path.join(CLIPDIR, f));
const kindAt = t => (INFO.shots.find(s => s.a <= t && t < s.b) || {}).kind;

const launch = { headless: true, args: ["--autoplay-policy=no-user-gesture-required"] };
if (process.env.REELFY_PW_EXE) launch.executablePath = process.env.REELFY_PW_EXE; else launch.channel = process.env.REELFY_PW_CHANNEL || "chrome";
const browser = await chromium.launch(launch);
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const errs = [];
page.on("pageerror", e => errs.push("pageerror " + e.message));
page.on("console", m => { if (m.type() === "error") errs.push("console " + m.text()); });
page.on("response", r => { if (r.status() >= 400 && !/\/broll\/.*\/shot\//.test(r.url())) errs.push(`HTTP ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
page.on("dialog", d => d.dismiss());
const log = (...a) => console.log("·", ...a);
const shot = async n => { if (OUT) { await page.waitForTimeout(400); await page.screenshot({ path: path.join(OUT, n + ".png") }); } };
const ev = (fn, a) => page.evaluate(fn, a);

try {
  // ---------------------------------------------------------------- Inicio -> Armar con IA
  await page.goto(BASE + "/");
  await page.waitForSelector("#drop");
  const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.click("#drop")]);
  await fc.setFiles(FILES);
  await page.waitForTimeout(500);
  await page.fill("#glossary", INFO.glossary);
  await page.click("#goAI");
  await page.waitForSelector("#s-montage.on");
  const t0 = Date.now();
  await page.waitForSelector("#s-assembly.on", { timeout: 20 * 60 * 1000 });
  log("armado en", Math.round((Date.now() - t0) / 1000), "s");
  await page.waitForTimeout(1500);

  // ---------------------------------------------------------------- panel Tomas: "Videos de apoyo"
  const m = await ev(() => ({ broll: (ASM.montage.broll || {}).sources || [], src: ASM.montage.broll_src || [],
    order: (ASM.montage.order || []).map(o => o.name), clips: ASM.clips.map(c => c.name) }));
  log("apoyo", JSON.stringify(m.broll.map(s => ({ name: s.name, summary: s.summary, vlm: s.vlm }))));
  assert.equal(m.broll.length, 1, "un video de apoyo");
  assert.equal(m.broll[0].name, INFO.film);
  assert.ok(!m.order.includes(INFO.film) && !m.clips.includes(INFO.film), "el video mudo nunca es toma");
  const sm = m.broll[0].summary;
  assert.ok(sm.usable >= 4 && sm.people >= 2, `tomas que sirven ${JSON.stringify(sm)}`);
  // el analisis vio a las personas: ninguna toma de persona sirve
  for (const s of m.broll[0].shots) {
    const k = kindAt((s.a + s.b) / 2);
    if (k === "person") assert.ok(!s.ok_window && !s.ok_full, `toma de persona utilizable: ${JSON.stringify(s)}`);
  }
  const row = await ev(() => { const r = document.querySelector("#asmTakes .mt-broll"); return r ? r.innerText.replace(/\s+/g, " ") : ""; });
  assert.match(row, /Videos de apoyo|Video sin voz: no es toma/);
  assert.match(row, new RegExp(`${sm.usable} de ${sm.shots} tomas sirven`));
  await shot("01_tomas_apoyo");
  await page.click('#asmTakes .mt-broll [data-act="boff"]');
  assert.ok(await ev(() => document.querySelector("#asmTakes .mt-broll").classList.contains("off")));
  await ev(() => asmUndo());
  assert.ok(await ev(() => !document.querySelector("#asmTakes .mt-broll").classList.contains("off")));

  // ---------------------------------------------------------------- Aplicar IA (Minimal) -> Studio
  await page.selectOption("#asmStyle", "minimal");
  await page.click("#asmApply");
  await page.waitForSelector("#s-editor.on", { timeout: 25 * 60 * 1000 });
  await page.waitForFunction(() => typeof PLAN !== "undefined" && PLAN && window.FX && !FX.busy(), null, { timeout: 10 * 60 * 1000 });
  await page.waitForTimeout(2500);
  const st = await ev(() => {
    const words = (PLAN.phrases || []).flat();
    const at = re => { const w = words.find(x => re.test(String(x.text))); return w ? +w.start : null; };
    return { reels: (PLAN.fx.media || []).filter(q => q.style === "reel").map(q => JSON.parse(JSON.stringify(q))),
      trans: (PLAN.fx.transitions || []).filter(q => q.src === "ai").length, grade: PLAN.fx.grade, effects: (PLAN.fx.effects || []).filter(q => q.src === "ai").length,
      abuela: at(/abuela/i), nubis: at(/nubis/i), dur: PLAN.duration };
  });
  log("fragmentos", JSON.stringify(st.reels.map(r => ({ id: r.id, s: r.start, e: r.end, lay: r.layout, shots: r.pieces.map(p => [p.shot, p.in, p.out]) }))));
  assert.ok(st.reels.length >= 1, "fragmentos de apoyo en fx.media");
  for (const r of st.reels) {
    assert.ok(r.src === "broll" && r.locked && r.why && r.why.startsWith("Mientras dices"), JSON.stringify(r));
    assert.ok(r.pieces.length >= 2 && r.pieces.length <= 5, "de 2 a 5 tomas");
    for (const p of r.pieces) {
      const k = kindAt((p.in + p.out) / 2), k2 = kindAt(p.out - 0.01);
      assert.ok(k !== "person" && k2 !== "person", `una toma de persona en ${r.id}: ${JSON.stringify(p)}`);
      assert.equal(k, kindAt(p.in + 0.01), `la toma se pasa a la siguiente: ${JSON.stringify(p)}`);
    }
    // nunca mientras se habla de otra cosa (la abuela)
    if (st.abuela != null) assert.ok(!(r.start <= st.abuela && st.abuela <= r.end), `apoyo fuera del tema en ${r.id}`);
  }
  // el titulo del producto cuando se nombra ("Se llama Nubis")
  const named = st.nubis != null ? st.reels.find(r => r.start - 0.3 <= st.nubis && st.nubis <= r.end) : null;
  if (named) assert.equal(kindAt((named.pieces[0].in + named.pieces[0].out) / 2), "title", "el titulo primero al nombrar el producto");
  // Minimal en un montaje: cortes secos, color de camara
  assert.equal(st.trans, 0, "sin transiciones");
  assert.equal(st.effects, 0, "sin efectos de camara");
  assert.equal(st.grade.enabled, false, "color de camara");

  // ---------------------------------------------------------------- inspector del fragmento
  const it = st.reels[0];
  await page.click(`[data-fx="media:${it.id}"]`);
  await page.waitForTimeout(300);
  if (!(await ev(() => { const b = document.querySelector('[data-tab="elem"]'); return b && (b.classList.contains("sel") || b.classList.contains("on") || b.getAttribute("aria-selected") === "true"); })))
    await page.click('[data-tab="elem"]');
  await ev(id => { const x = PLAN.fx.media.find(q => q.id === id); V.currentTime = (x.start + x.end) / 2; }, it.id);
  await page.waitForTimeout(2500);
  const insp = await ev(() => document.querySelector("#inspBody").innerText.replace(/\s+/g, " "));
  assert.match(insp, /Video de apoyo/);
  assert.match(insp, /Mientras dices/);
  assert.match(insp, /Formato/);
  assert.match(insp, /Ventana flotante/);
  assert.match(insp, /Pantalla completa/);
  assert.match(insp, /Toma 1 ·/);
  assert.match(insp, /nunca personas/);
  await shot("02_inspector_apoyo");
  // la vista en vivo dibuja el fragmento (pantalla completa: toda la caja; ventana: centrada)
  const hit = await ev(id => FX.hits().hits.find(h => h.track === "media" && h.id === id), it.id);
  const box = await ev(() => FX.hits().box);
  assert.ok(hit, "el fragmento se dibuja en la vista en vivo");
  if (it.layout === "full") assert.ok(Math.abs(hit.bb.w - box.w) <= 1.5 && Math.abs(hit.bb.h - box.h) <= 1.5, "pantalla completa");
  else assert.ok(Math.abs((hit.bb.x - box.x) - (box.x + box.w - (hit.bb.x + hit.bb.w))) <= 1.5, "ventana centrada");
  // cambiar una toma: la rejilla solo ofrece tomas que sirven (nunca personas) y la elegida queda en el plan
  await page.click('#inspBody .reel-shot[data-j="0"] [data-a="swap"]');
  await page.waitForTimeout(600);
  const opts = await ev(() => [...document.querySelectorAll("#inspBody .rp-opt")].map(b => +b.dataset.i));
  log("opciones", JSON.stringify(opts));
  const lib = m.broll[0].shots;
  for (const i of opts) {
    const s = lib.find(x => x.i === i);
    assert.ok(s && kindAt((s.a + s.b) / 2) !== "person", `opcion de persona: ${i}`);
    assert.ok(it.layout === "full" ? s.ok_full : s.ok_window, `opcion que no sirve en ${it.layout}: ${i}`);
  }
  if (opts.length) {
    await page.click(`#inspBody .rp-opt[data-i="${opts[0]}"]`);
    await page.waitForTimeout(500);
    const p0 = await ev(id => PLAN.fx.media.find(q => q.id === id).pieces[0], it.id);
    assert.equal(p0.shot, opts[0]);
    assert.equal(p0.why, "La elegiste tú.");
    await shot("03_toma_cambiada");
  }
  // el formato: si alcanzan las tomas, se cambia y las piezas siguen sirviendo
  const alt = it.layout === "full" ? "window" : "full";
  const can = await ev(() => { const b = [...document.querySelectorAll("#inspBody button[role=radio]")].find(x => /Pantalla completa|Ventana flotante/.test(x.textContent) && !x.getAttribute("aria-checked").includes("true"));
    if (b && !b.disabled) { b.setAttribute("data-e2e", "lay"); return true; } return false; });
  if (can) {
    await page.click('[data-e2e="lay"]');
    await page.waitForTimeout(500);
    const x = await ev(id => JSON.parse(JSON.stringify(PLAN.fx.media.find(q => q.id === id))), it.id);
    assert.equal(x.layout, alt);
    for (const p of x.pieces) { const s = lib.find(q => q.i === p.shot); assert.ok(alt === "full" ? s.ok_full : s.ok_window, "pieza que no sirve en el formato nuevo"); }
    await shot("04_otro_formato");
  }

  // ---------------------------------------------------------------- Exportar con la hoja
  const JOBID = await ev(() => JOB);
  await page.click("#render");
  await page.waitForSelector("#xpGo", { state: "visible", timeout: 15000 });
  await page.waitForTimeout(500);
  const before = new Set(fs.existsSync(XDIR) ? fs.readdirSync(XDIR) : []);
  await page.click("#xpGo");
  let files = [];
  for (let k = 0; k < 2400 && !files.length; k++) {
    await page.waitForTimeout(1000);
    const s2 = await ev(async j => (await (await fetch(`/status/${j}?plan=0`)).json()), JOBID);
    if (s2.state === "error") throw new Error("export: " + (s2.message || JSON.stringify(s2)));
    const got = (fs.existsSync(XDIR) ? fs.readdirSync(XDIR) : []).filter(f => f.endsWith(".mp4") && !before.has(f));
    if (got.length && !["queued", "running"].includes(s2.state)) files = got.map(f => path.join(XDIR, f));
  }
  assert.ok(files.length, "el export dejo su video en la carpeta");
  await shot("05_exportado");
  const finalPlan = await ev(async j => (await (await fetch(`/plan/${j}`)).json()).plan, JOBID);
  assert.deepEqual(errs, [], "sin errores de consola ni HTTP");
  log("OK");
  console.log("RESULT " + JSON.stringify({ files, plan: finalPlan }));
} catch (e) {
  console.error("FALLO:", e && e.stack || e);
  await shot("99_fallo").catch(() => {});
  console.error("errores:", JSON.stringify(errs));
  await browser.close();
  process.exit(1);
}
await browser.close();
