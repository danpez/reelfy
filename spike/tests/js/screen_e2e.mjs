// E2E de la grabacion de pantalla sincronizada en un navegador real (Playwright + Chrome) contra un
// servidor AISLADO con el motor REAL (sin simulaciones): Inicio -> "Armar con IA" -> panel Tomas (la
// pantalla reconocida "sincronizada con CAM_0002.MOV", el desfase exacto en segundos de los ARCHIVOS
// -la charla + el inicio tardio de su audio-, el marco del dispositivo emulado recortado, su bloque en
// la pista Recursos, la vista previa encima de su toma, quitar y deshacer, "No es grabación de pantalla"
// y volver) -> "Aplicar IA" (Minimal) -> Studio (elemento "Grabación de pantalla" en Recursos con su
// inspector editable; quitar/poner; movimiento) -> /frame: la ventana centrada (simetrica) muestra el
// cuadro de la pantalla que toca al inicio, en medio y al final (+-1 cuadro), sin nada del marco ->
// Exportar en 4K con la hoja (recompone desde las fuentes). Sin errores de consola.
// uso: REELFY_PLAYWRIGHT=<playwright-core> node tests/js/screen_e2e.mjs <url> <carpeta de clips> <capturas> '<info json>' <carpeta export>
// (tests/test_screen_e2e.py genera los medios sinteticos con `say`, la info, y revisa el 4K exportado).
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("define REELFY_PLAYWRIGHT (ruta a playwright-core)"); process.exit(2); }
const { chromium } = require(PW);
const BASE = (process.argv[2] || "http://127.0.0.1:8701").replace(/\/$/, "");
const CLIPDIR = process.argv[3];
const OUT = process.argv[4] || null;
const INFO = JSON.parse(process.argv[5] || "{}");
const XDIR = process.argv[6];
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const FILES = INFO.files.map(f => path.join(CLIPDIR, f));

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
const shot = async n => { if (OUT) { await page.waitForTimeout(400); await page.screenshot({ path: path.join(OUT, n + ".png") }); } };
const ev = (fn, a) => page.evaluate(fn, a);

try {
  // ---------------------------------------------------------------- Inicio -> Armar con IA
  await page.goto(BASE + "/");
  await page.waitForSelector("#drop");
  const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.click("#drop")]);
  await fc.setFiles(FILES);
  await page.waitForTimeout(500);
  await page.fill("#glossary", "Reelfy");
  await page.click("#goAI");
  await page.waitForSelector("#s-montage.on");
  const t0 = Date.now();
  await page.waitForSelector("#s-assembly.on", { timeout: 15 * 60 * 1000 });
  log("armado en", Math.round((Date.now() - t0) / 1000), "s");
  await page.waitForTimeout(1500);

  // ---------------------------------------------------------------- panel Tomas
  const m = await ev(() => ({ screens: ASM.montage.screens || [], order: (ASM.montage.order || []).map(o => o.name),
    clips: ASM.clips.map(c => c.name), discarded: (ASM.montage.discarded || []).map(d => d.sid) }));
  log("pantallas", JSON.stringify(m.screens.map(s => ({ name: s.name, cam: s.cam_name, offset: s.offset, corr: s.corr }))));
  assert.equal(m.screens.length, 1, "una grabacion de pantalla reconocida");
  const S = m.screens[0];
  assert.equal(S.name, INFO.screen);
  assert.equal(S.cam_name, INFO.cam);
  // desfase en segundos de los ARCHIVOS: la charla + el inicio tardio del audio de la pantalla
  assert.ok(Math.abs(S.offset - INFO.offset) < 0.03, `desfase ${S.offset} != ${INFO.offset}`);
  assert.ok(Math.abs(S.audio_offset - INFO.audio_offset) < 0.03, `desfase de los audios ${S.audio_offset}`);
  assert.ok(Math.abs(S.lead[1] - INFO.lead) < 1e-3 && S.lead[0] === 0, `inicio del audio ${S.lead}`);
  // el marco del dispositivo emulado se recorta (a <= 3 px de la app, nunca dentro del marco)
  const [fl, ft, fr, fb] = INFO.frame;
  assert.ok(Array.isArray(S.content), "recorte del marco");
  const [cx, cy, cw, ch] = S.content;
  assert.ok(cx >= fl && cx - fl <= 3 && cy >= ft && cy - ft <= 3, `marco ${S.content}`);
  assert.ok(cx + cw <= fl + INFO.sw && fl + INFO.sw - (cx + cw) <= 3 && cy + ch <= ft + INFO.sh && ft + INFO.sh - (cy + ch) <= 3, `marco ${S.content}`);
  assert.ok(!m.order.includes(INFO.screen) && !m.clips.includes(INFO.screen), "la pantalla nunca es toma");
  const row = await ev(() => { const r = document.querySelector("#asmTakes .mt-scr"); return r ? r.innerText.replace(/\s+/g, " ") : ""; });
  assert.match(row, new RegExp(`Grabación de pantalla sincronizada con ${INFO.cam.replace(".", "\\.")}`));
  assert.match(row, /sin su sonido/);
  const lane = await ev(() => [...document.querySelectorAll("#asmMedia .mdb.scr")].map(e => e.title));
  assert.equal(lane.length, 1, "bloque en la pista Recursos");
  await shot("01_tomas_pantalla");
  // vista previa del ensamble: la tarjeta encima de su toma, centrada
  const pv = await ev(async () => { const z = asmScreenSpans()[0]; asmGoto((z.a + z.b) / 2, true); await new Promise(r => setTimeout(r, 1800));
    const el = document.querySelector("#asmScard"), v = asmP(), er = el.getBoundingClientRect(), vr = v.getBoundingClientRect();
    return { hidden: el.hidden, l: er.left - vr.left, r: vr.right - er.right, w: er.width, vw: vr.width }; });
  log("vista previa", JSON.stringify(pv));
  assert.ok(!pv.hidden && pv.w > 0.3 * pv.vw, "la tarjeta se ve en la vista previa");
  // el <video> de la pantalla se corre para que el marco quede fuera de la ventana
  const vcrop = await ev(() => { const v = document.querySelector("#asmScard video"); return { l: parseFloat(v.style.left), t: parseFloat(v.style.top) }; });
  assert.ok(vcrop.l < -0.5 && vcrop.t < -0.5, `video recortado en la vista previa ${JSON.stringify(vcrop)}`);
  assert.ok(Math.abs(pv.l - pv.r) <= 1.5, `centrada: ${pv.l} vs ${pv.r}`);
  await shot("02_vista_previa");
  // quitar y deshacer
  await page.click('#asmTakes .mt-scr [data-act="soff"]');
  assert.ok(await ev(() => document.querySelector("#asmTakes .mt-scr").classList.contains("off")));
  await ev(() => asmUndo());
  assert.ok(await ev(() => !document.querySelector("#asmTakes .mt-scr").classList.contains("off")));
  // "No es grabación de pantalla": rearma el MISMO proyecto tratandola como toma; "Es grabación de
  // pantalla" lo deshace (otro rearmado)
  const rearm = async (sel, want) => {
    await page.click(sel);
    await page.waitForSelector(".modal.on .mtdlg");
    await page.click('.modal.on .mtdlg [data-a="ok"]');
    await page.waitForSelector("#s-montage.on");
    await page.waitForSelector("#s-assembly.on", { timeout: 10 * 60 * 1000 });
    await page.waitForFunction(n => ASM.montage && (ASM.montage.screens || []).length === n, want, { timeout: 60000 });
    await page.waitForTimeout(800);
  };
  await rearm('#asmTakes .mt-scr [data-act="snot"]', 0);
  const off = await ev(() => ({ off: ASM.montage.screens_off, order: (ASM.montage.order || []).map(o => o.name),
    row: (document.querySelector("#asmTakes [data-scrsid]") || {}).innerText || "" }));
  log("como toma", JSON.stringify(off));
  assert.equal(off.off.length, 1);
  assert.ok(off.order.includes(INFO.screen), "ahora es un clip de la sesion");
  assert.match(off.row.replace(/\s+/g, " "), /Lo tratas como una toma más/);
  await shot("01b_como_toma");
  await rearm('#asmTakes [data-scrsid] [data-act="sredo"]', 1);
  assert.deepEqual(await ev(() => ASM.montage.screens_off || []), []);

  // ---------------------------------------------------------------- Aplicar IA (Minimal) -> Studio
  await page.selectOption("#asmStyle", "minimal");
  await page.click("#asmApply");
  await page.waitForSelector("#s-editor.on", { timeout: 25 * 60 * 1000 });
  await page.waitForFunction(() => typeof PLAN !== "undefined" && PLAN && window.FX && !FX.busy(), null, { timeout: 10 * 60 * 1000 });
  await page.waitForTimeout(2500);
  const it = await ev(() => { const x = (PLAN.fx.media || []).find(q => q.style === "screen"); return x ? JSON.parse(JSON.stringify(x)) : null; });
  assert.ok(it, "elemento de pantalla en fx.media");
  log("elemento", JSON.stringify({ start: it.start, end: it.end, cam: it.cam_name, pieces: it.sync.pieces.length }));
  assert.equal(it.cam_name, INFO.cam);
  assert.ok(it.sync.pieces.length >= 1 && it.end - it.start > 3);
  // inspector en espanol y con la toma
  // como el usuario: clic en su bloque de la pista Recursos (la pestaña «Elemento» muestra su inspector)
  await page.click(`[data-fx="media:${it.id}"]`);
  await page.waitForTimeout(300);
  if (!(await ev(() => { const b = document.querySelector('[data-tab="elem"]'); return b && (b.classList.contains("sel") || b.classList.contains("on") || b.getAttribute("aria-selected") === "true"); })))
    await page.click('[data-tab="elem"]');
  await ev(id => { V.currentTime = (window.ScreenCard.coverage(PLAN.fx.media.find(q => q.id === id))[0]) + 2.5; }, it.id);
  await page.waitForTimeout(2500);
  const insp = await ev(() => document.querySelector("#inspBody").innerText.replace(/\s+/g, " "));
  assert.match(insp, /Grabación de pantalla/);
  assert.match(insp, new RegExp(`Sincronizada con ${INFO.cam.replace(".", "\\.")}`));
  assert.doesNotMatch(insp, /círculo/);
  assert.match(insp, new RegExp(`Reelfy quitó el marco de tu grabación \\(${INFO.sw - 4}×${INFO.sh - 4} px`));
  assert.match(insp, /Movimiento/);
  // editable: el movimiento (vacio = segun el estilo: con el corte en Minimal)
  const pick = async lbl => {
    const ok = await ev(l => { const row = [...document.querySelectorAll("#inspBody .hrow")].find(r => /Movimiento/.test(r.querySelector("label") ? r.querySelector("label").textContent : ""));
      const b = row && [...row.querySelectorAll("button")].find(x => x.textContent.trim() === l); if (b) b.setAttribute("data-e2e", "pick"); return !!b; }, lbl);
    assert.ok(ok, `opcion «${lbl}»`);
    await page.click('[data-e2e="pick"]');
    await page.waitForTimeout(300);
    await ev(() => { const b = document.querySelector('[data-e2e="pick"]'); if (b) b.removeAttribute("data-e2e"); });
  };
  await pick("Con vida");
  assert.equal(await ev(id => PLAN.fx.media.find(q => q.id === id).motion, it.id), "lively");
  await pick("Con el corte");
  assert.equal(await ev(id => PLAN.fx.media.find(q => q.id === id).motion, it.id), "cut");
  const hits = await ev(id => FX.hits().hits.filter(h => h.track === "media" && h.id === id), it.id);
  assert.equal(hits.length, 1, "la tarjeta se dibuja en la vista en vivo");
  const box = await ev(() => FX.hits().box);
  const hb = hits[0].bb;
  assert.ok(Math.abs((hb.x - box.x) - (box.x + box.w - (hb.x + hb.w))) <= 1.5, "tarjeta centrada en la vista en vivo");
  await shot("03_studio_pantalla");
  // quitar desde el inspector y volver a ponerla
  await page.click('#inspBody [data-a="scrtoggle"]');
  assert.equal(await ev(id => PLAN.fx.media.find(q => q.id === id).enabled, it.id), false);
  await page.waitForTimeout(500);
  await page.click('#inspBody [data-a="scrtoggle"]');
  assert.equal(await ev(id => PLAN.fx.media.find(q => q.id === id).enabled, it.id), true);

  // ---------------------------------------------------------------- /frame: el cuadro correcto de la pantalla
  const res = await ev(async ({ id, info }) => {
    const plan = PLAN, x = plan.fx.media.find(q => q.id === id), SC = window.ScreenCard, FC = window.FloatCard;
    const cov = SC.coverage(x), cap = plan.fx.captions, on = !!(cap && cap.enabled !== false), crop = SC.cropOf(x);
    const card = SC.layout(x.w, x.h, 9 / 16, FC.captionTop(cap, on, cov[0], cov[1], 9 / 16), FC.captionBottom(cap, on, cov[0], cov[1], 9 / 16), x.width, crop);
    const out = [];
    for (const f of [0.2, 0.5, 0.85]) {
      // un cuadro final DENTRO del tramo conservado (no en un corte)
      let t = cov[0] + f * (cov[1] - cov[0]);
      const T = FX.finalTime(t);
      const r = await fetch(`/frame/${JOB}`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan, t_final: T, quality: "final" }) });
      const bm = await createImageBitmap(await r.blob());
      const cv = new OffscreenCanvas(bm.width, bm.height), g = cv.getContext("2d"); g.drawImage(bm, 0, 0);
      const [x0, y0, w, h] = FC.cardPx(card, bm.width, bm.height);
      let n = 0, bad = false;
      for (let i = 0; i < info.bits; i++) {
        const yc = info.frame[1] + (info.band0 + (i + 0.5) * info.bandh) * info.sh;      // px de la grabacion completa
        const y = Math.round(y0 + (yc / info.fh - crop[1]) / (crop[3] - crop[1]) * h), xs = Math.round(x0 + w * 0.3);
        const d = g.getImageData(xs - 2, y - 1, 5, 3).data; let s = 0;
        for (let k = 0; k < d.length; k += 4) s += (d[k] + d[k + 1] + d[k + 2]) / 3;
        const v = s / (d.length / 4);
        if (v > 90 && v < 170) bad = true;
        if (v >= 170) n |= 1 << i;
      }
      // margenes de la ventana: lo blanco de la interfaz contra el fondo desenfocado (simetria medida)
      const row = g.getImageData(0, Math.round(y0 + h * 0.5), bm.width, 1).data;
      let L = -1, R = -1;
      for (let xx = 0; xx < bm.width; xx++) { const v = (row[xx * 4] + row[xx * 4 + 1] + row[xx * 4 + 2]) / 3; if (v > 200) { if (L < 0) L = xx; R = xx; } }
      // nada del marco lavanda dentro de la ventana (columna a 3 px de cada orilla, sin las esquinas)
      let lav = 0, tot = 0;
      for (const xx of [x0 + 3, x0 + w - 4]) {
        const col = g.getImageData(xx, Math.round(y0 + 0.1 * h), 1, Math.round(0.8 * h)).data;
        for (let k = 0; k < col.length; k += 4) { tot++; if (Math.abs(col[k] - info.lavanda[0]) + Math.abs(col[k + 1] - info.lavanda[1]) + Math.abs(col[k + 2] - info.lavanda[2]) < 24) lav++; }
      }
      // el cuadro final T muestra el original inverse(T): el que realmente se dibuja
      const tOrig = FX.tlInverse ? FX.tlInverse(T) : t;
      out.push({ f, t, T, tOrig, exp: SC.syncTime(x, tOrig), got: bad ? null : n / info.fps, L, R: bm.width - 1 - R, W: bm.width, lav: lav / tot });
    }
    return out;
  }, { id: it.id, info: INFO });
  log("cuadros", JSON.stringify(res));
  for (const q of res) {
    assert.ok(q.got != null, `franjas ilegibles en ${q.T}`);
    assert.ok(Math.abs(q.got - q.exp) <= 1 / 30 + 1 / 60 + 1e-6, `pantalla ${q.got} != ${q.exp} (t final ${q.T})`);
    assert.ok(Math.abs(q.L - q.R) <= 2, `margenes ${q.L} vs ${q.R}`);
    assert.ok(q.lav < 0.02, `marco dentro de la ventana: ${q.lav}`);
  }
  await shot("04_frame");

  // ---------------------------------------------------------------- Exportar en 4K (la hoja real): recompone
  const JOBID = await ev(() => JOB);
  const renders = [];
  page.on("response", async r => { if (r.request().method() === "POST" && new URL(r.url()).pathname.startsWith("/render/")) { try { renders.push({ body: JSON.parse(r.request().postData() || "{}"), res: await r.json() }); } catch (e) { /* nada */ } } });
  await page.click("#render");
  await page.waitForSelector("#xpGo", { state: "visible", timeout: 15000 });
  await page.waitForFunction(() => { const b = document.querySelector('#xpRes button[data-v="4k"]'); return b && !b.classList.contains("off"); }, null, { timeout: 30000 });
  await page.click('#xpRes button[data-v="4k"]');
  await page.waitForTimeout(500);
  assert.equal(await ev(() => document.querySelector('#xpRes button[data-v="4k"]').getAttribute("aria-checked")), "true", "4K elegido");
  await shot("05_hoja_4k");
  const before = new Set(fs.existsSync(XDIR) ? fs.readdirSync(XDIR) : []);
  await page.click("#xpGo");
  let files = [], stages = new Set();
  for (let k = 0; k < 2400 && !files.length; k++) {
    await page.waitForTimeout(1000);
    const st = await ev(async j => (await (await fetch(`/status/${j}?plan=0`)).json()), JOBID);
    if (st.stage) stages.add(st.stage);
    if (st.state === "error") throw new Error("export: " + (st.message || JSON.stringify(st)));
    const got = (fs.existsSync(XDIR) ? fs.readdirSync(XDIR) : []).filter(f => f.endsWith(".mp4") && !before.has(f));
    if (got.length && !["queued", "running"].includes(st.state)) files = got.map(f => path.join(XDIR, f));
  }
  assert.ok(files.length, "el export en 4K dejo su video en la carpeta");
  log("export", JSON.stringify({ files: files.map(f => path.basename(f)), stages: [...stages], render: renders.map(r => r.res && r.res.export) }));
  assert.ok(renders.length && renders[0].body.export && renders[0].body.export.res === "4k", "la hoja pidio 4K");
  assert.equal(renders[0].res.export.recompose, true, "4K recompone desde las fuentes (calidad completa)");
  await shot("06_exportado");
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
