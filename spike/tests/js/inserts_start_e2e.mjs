// E2E: un recurso del guion ("- Agregar demo") con «Desde qué segundo del clip» puesto por el usuario
// en el Studio EMPIEZA ahi, en la vista en vivo y en el video exportado (la hoja Exportar real).
// Regresion v0.6.1: en v0.5.5 el usuario ponia otro inicio y el recurso salia siempre desde el inicio
// del archivo: el recurso del guion no traia la duracion de su clip (media_dur) y el inspector,
// sin ella, devolvia el inicio a 0 (mediaRefit acotaba a max(0, 0 - tramo)).
// uso: REELFY_PLAYWRIGHT=<playwright-core> node tests/js/inserts_start_e2e.mjs <url> <job> <carpeta export> [capturas]
// (tests/test_inserts_start_e2e.py siembra el proyecto SINTETICO y revisa los cuadros del archivo).
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("define REELFY_PLAYWRIGHT (ruta a playwright-core)"); process.exit(2); }
const { chromium } = require(PW);
const BASE = (process.argv[2] || "http://127.0.0.1:8941").replace(/\/$/, "");
const JOB = process.argv[3] || "ins_demo";
const XDIR = process.argv[4];
const OUT = process.argv[5] || null;
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const START = 9.0;

const launch = { headless: true, args: ["--autoplay-policy=no-user-gesture-required"] };
if (process.env.REELFY_PW_EXE) launch.executablePath = process.env.REELFY_PW_EXE; else launch.channel = process.env.REELFY_PW_CHANNEL || "chrome";
const browser = await chromium.launch(launch);
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const errs = [];
page.on("pageerror", e => errs.push("pageerror " + e.message));
page.on("console", m => { if (m.type() === "error") errs.push("console " + m.text()); });
page.on("response", r => { if (r.status() >= 400) errs.push(`HTTP ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
page.on("dialog", d => d.dismiss());
const log = (...a) => console.log("·", ...a);
const shot = async n => { if (OUT) { await page.waitForTimeout(300); await page.screenshot({ path: path.join(OUT, n + ".png") }); } };
const ev = (fn, a) => page.evaluate(fn, a);

try {
  await page.goto(`${BASE}/?job=${JOB}`);
  await page.waitForSelector("#s-editor.on", { timeout: 60000 });
  await page.waitForFunction(() => typeof PLAN !== "undefined" && PLAN && window.FX && !FX.busy() && (PLAN.fx.media || []).length, null, { timeout: 60000 });
  await page.waitForTimeout(800);
  // el recurso del guion llega con la duracion de su clip (el servidor la completa en uno viejo)
  const it0 = await ev(() => JSON.parse(JSON.stringify(PLAN.fx.media[0])));
  log("recurso", JSON.stringify({ id: it0.id, fit: it0.fit, src_in: it0.src_in, media_dur: it0.media_dur }));
  assert.equal(it0.src, "script");
  assert.ok(Math.abs(it0.media_dur - 20) < 0.1, `media_dur ${it0.media_dur}`);
  // sin la duracion (el clip aun no carga) el ajuste ya no tira el inicio a 0
  const kept = await ev(() => { const x = { kind: "video", fit: "trim", src_in: 9, start: 3, end: 7, sid: "nada" }; FX.mediaRefit(x); return x.src_in; });
  assert.equal(kept, 9, "sin duracion conocida se respeta el inicio");
  const clamped = await ev(() => { const x = { kind: "video", fit: "trim", src_in: 19, start: 3, end: 7, media_dur: 20 }; FX.mediaRefit(x); return x.src_in; });
  assert.equal(clamped, 16, "con la duracion: dentro del clip (que no se acabe antes del tramo)");

  // inspector (Avanzado): «Desde qué segundo del clip»
  await ev(() => { Estilo.setLevel("advanced"); V.currentTime = 0.5; });
  await page.waitForTimeout(300);
  // como el usuario: clic en su bloque de la pista Recursos (y la pestaña «Elemento»)
  await page.click(`[data-fx="media:${it0.id}"]`);
  await page.waitForTimeout(300);
  if (!(await ev(() => { const b = document.querySelector('[data-tab="elem"]'); return b && (b.classList.contains("sel") || b.classList.contains("on") || b.getAttribute("aria-selected") === "true"); })))
    await page.click('[data-tab="elem"]');
  await page.waitForFunction(() => /Recurso|demo/i.test((document.querySelector("#inspBody") || {}).innerText || ""), null, { timeout: 10000 });
  const mark = await ev(() => {
    const row = [...document.querySelectorAll("#inspBody .hrow")].find(r => /Desde qué segundo/.test(r.innerText));
    const inp = row && row.querySelector("input");
    if (inp) inp.setAttribute("data-e2e", "srcin");
    return !!inp;
  });
  assert.ok(mark, "el campo «Desde qué segundo del clip» esta en el inspector (Avanzado, Recortar)");
  await shot("01_inspector");
  await page.fill('[data-e2e="srcin"]', String(START));
  await page.keyboard.press("Tab");
  await page.waitForTimeout(400);
  assert.equal(await ev(() => PLAN.fx.media[0].src_in), START, "el inicio que puso el usuario se queda");
  // se guarda en el servidor (autoguardado)
  let saved = null;
  for (let k = 0; k < 60 && saved !== START; k++) {
    await page.waitForTimeout(250);
    saved = await ev(async j => (await (await fetch(`/plan/${j}`)).json()).plan.fx.media[0].src_in, JOB);
  }
  assert.equal(saved, START, `guardado: ${saved}`);
  // moverlo en el timeline no lo regresa a 0
  await ev(() => { const x = PLAN.fx.media[0]; x.start += 0; FX.mediaRefit(x); });
  assert.equal(await ev(() => PLAN.fx.media[0].src_in), START);
  // vista en vivo: el clip del recurso va en su segundo START + lo que lleva el recurso
  const live = await ev(async () => {
    const x = PLAN.fx.media[0]; V.currentTime = x.start + 1.5;
    await new Promise(r => setTimeout(r, 1500));
    return window.FloatCard.srcTime(x, 1.5, +x.media_dur, 30);
  });
  assert.ok(Math.abs(live - (START + 1.5)) < 1e-6, `vista en vivo ${live}`);
  await shot("02_vista");

  // ---------------------------------------------------------------- Exportar (la hoja real)
  await page.click("#render");
  await page.waitForSelector("#xpGo", { state: "visible", timeout: 15000 });
  await page.waitForTimeout(600);
  await shot("03_hoja");
  const before = new Set(fs.existsSync(XDIR) ? fs.readdirSync(XDIR) : []);
  await page.click("#xpGo");
  let files = [];
  for (let k = 0; k < 600 && !files.length; k++) {
    await page.waitForTimeout(1000);
    const st = await ev(async j => (await (await fetch(`/status/${j}?plan=0`)).json()), JOB);
    if (st.state === "error") throw new Error("export: " + (st.message || JSON.stringify(st)));
    const got = (fs.existsSync(XDIR) ? fs.readdirSync(XDIR) : []).filter(f => f.endsWith(".mp4") && !before.has(f));
    if (got.length && !["queued", "running"].includes(st.state)) files = got.map(f => path.join(XDIR, f));
  }
  assert.ok(files.length, "el export dejo su video en la carpeta");
  await shot("04_exportado");
  const plan = await ev(async j => (await (await fetch(`/plan/${j}`)).json()).plan, JOB);
  assert.deepEqual(errs, [], "sin errores de consola ni HTTP");
  console.log("RESULT " + JSON.stringify({ files, plan }));
} catch (e) {
  console.error("FALLO:", e && e.stack || e);
  await shot("99_fallo").catch(() => {});
  console.error("errores:", JSON.stringify(errs));
  await browser.close();
  process.exit(1);
}
await browser.close();
