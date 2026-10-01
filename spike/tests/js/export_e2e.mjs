// E2E de la hoja "Exportar" del Studio en un navegador real (Playwright + Chrome).
// uso: REELFY_PLAYWRIGHT=<ruta a playwright-core> node tests/js/export_e2e.mjs <url base> <job> [capturas] [server|contract]
// El servidor (tests/devserver.sh, datos SINTETICOS: tests/test_studio_ui.py::_plan_export) sirve el
// editor real; el render y los endpoints del export se SIMULAN aqui (page.route) en una de sus dos
// formas (la hoja habla con las dos):
//  - "server" (la de app/server.py): GET /export/options (resoluciones con motivo, calidades
//    estandar|alta|maxima, carpeta, nombre), GET /export/estimate (space_ok...), POST /render con
//    `export: {res, quality, name, folder, shorts}`, /status con export.code y fallback_res, clips con
//    saved/path/folder_label (o save_error -> POST /export/save), POST /reveal {path, action}.
//  - "contract" (minima): GET /settings/export, estimate con max_res/source_res, POST /render con
//    {resolution, quality, name, folder, shorts}, POST /reveal y POST /open {path}.
// Revisa: abrir con "Exportar", valores por defecto, resolucion por formato y por fuente (aviso
// "no agregaria calidad"), calidad, nombre saneado, carpeta SOLO con el selector nativo (elegir,
// cancelar, carpeta sin permiso), estimacion y "Video en 4K", error de espacio (la hoja sigue abierta),
// export -> etapas -> resultados (ruta, tamano, duracion, resolucion, Mostrar/Abrir), lo recordado,
// falla al recomponer -> "Exportar en 1080p", teclado (Esc, Tab, flechas; Espacio no reproduce),
// simetria medida, texto >= 11 px, contraste AA y consola sin errores. Sale con 1 al primer fallo.
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("define REELFY_PLAYWRIGHT (ruta a playwright-core)"); process.exit(2); }
const { chromium } = require(PW);
const BASE = (process.argv[2] || "http://127.0.0.1:8801").replace(/\/$/, "");
const JOB = process.argv[3] || "xp_demo";
const OUT0 = process.argv[4] || null;
// "live": SIN simular /export/* (el servidor de verdad); solo abre la hoja y la revisa (no exporta)
const MODE = ["contract", "live"].includes(process.argv[5]) ? process.argv[5] : "server";
const SRV = MODE !== "contract", LIVE = MODE === "live";
const OUT = OUT0 ? (MODE === "server" ? OUT0 : path.join(OUT0, MODE === "live" ? "servidor_real" : "contrato")) : null;
if (OUT) fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true, channel: process.env.REELFY_PW_CHANNEL || "chrome" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", e => errs.push("pageerror " + e.message));
page.on("console", m => { if (m.type() === "error" && !/status of 50[0-9]|status of 4\d\d/.test(m.text())) errs.push("console " + m.text()); });
page.on("dialog", d => { errs.push("dialog " + d.message()); d.dismiss(); });
const log = (...a) => console.log("·", ...a);
const shot = async n => { if (OUT) await page.screenshot({ path: path.join(OUT, n + ".png") }); };
const ev = (fn, arg) => page.evaluate(fn, arg);
const txt = s => ev(q => { const e = document.querySelector(q); return e ? e.textContent.replace(/\s+/g, " ").trim() : null; }, s);
const today = (() => { const d = new Date(), z = n => String(n).padStart(2, "0"); return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`; })();

// ------------------------------------------------------------------ servidor simulado
const HOME = "/Users/demo";
const DEFAULT_DIR = `${HOME}/Movies/Reelfy`;
const PICKED = `${HOME}/Documents/Clientes/Entregas de septiembre/Videos finales/Reelfy`;
const label = p => p.startsWith(HOME) ? "~" + p.slice(HOME.length) : p;
const M = {
  settings: { folder: DEFAULT_DIR, resolution: "1080p", quality: "high", name_pattern: "{proyecto} - {fecha}" },
  last: { res: "1080p", quality: "alta", shorts: true, folder: DEFAULT_DIR },        // lo que recuerda el servidor
  allowed: new Set([DEFAULT_DIR]),
  maxRes: "1080p", sourceRes: "1080x1920", combined: 1080,
  pick: [], picks: 0, renders: [], renderReply: [], estimates: [], reveal: [], open: [], saves: [], sim: null,
};
const SHORT = { "720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160 };
const LBL = { "720p": "720p", "1080p": "1080p", "1440p": "1440p", "4k": "4K" };
const SQ = { estandar: "standard", alta: "high", maxima: "max" };
const QUALS = [{ id: "estandar", label: "Estándar", hint: "Archivo más ligero" }, { id: "alta", label: "Alta", hint: "La calidad de siempre" },
  { id: "maxima", label: "Máxima", hint: "Máximo detalle, archivo más pesado" }];
const FMTS = { "9:16": [9, 16], "1:1": [1, 1], "4:5": [4, 5], "16:9": [16, 9], original: [9, 16] };
function rows(fmt) {
  const [a, b] = FMTS[fmt], mi = Object.keys(SHORT).indexOf(M.maxRes);
  return Object.keys(SHORT).map((id, i) => {
    const s = SHORT[id], W = a <= b ? s : Math.round(s * a / b / 2) * 2, H = a <= b ? Math.round(s * b / a / 2) * 2 : s;
    const on = i <= Math.max(1, mi);
    // montaje con clips de distinta resolucion: la resolucion alta que da la mayoria avisa cuantos se escalan
    const warn = M.mixed && on && s > 1080 ? `2 de 9 clips son 1080p: se verán escalados en ${LBL[id]}` : null;
    return { id, label: LBL[id], W, H, enabled: on, reason: on ? null : `Tu video original es ${LBL[M.maxRes]}; ${LBL[id]} no agregaría calidad`, recompose: on && s > M.combined, warn };
  });
}
function estimate(q) {
  const res = q.get("res"), k = SHORT[res] / 1080;
  const qq = SRV ? SQ[q.get("quality")] : q.get("quality");
  const qf = { standard: 0.6, high: 1, max: 1.7 }[qq] || 1;
  const sh = q.get("shorts") === "1" ? 1.4 : 1;
  const rec = SHORT[res] > M.combined;
  const e = { seconds: Math.round(70 * k * k * sh + (rec ? 120 : 0)), bytes: Math.round(150e6 * k * k * qf * sh), recompose: rec };
  if (!SRV) return Object.assign(e, { max_res: M.maxRes, source_res: M.sourceRes });
  return Object.assign(e, { text: "(texto del servidor)", recompose_cached: false, enabled: true, reason: null,
    space_ok: !M.lowSpace, need_bytes: e.bytes * 2, free_bytes: M.lowSpace ? 1.2e9 : 120e9, res, label: LBL[res] });
}
const httpErrs = [];
page.on("response", r => { const u = new URL(r.url()); if (r.status() >= 400 && /^\/(export|pick-folder|reveal|open|settings)/.test(u.pathname)) httpErrs.push(`HTTP ${r.status()} ${r.request().method()} ${u.pathname}`); });
await page.route("**/*", async route => {
  const req = route.request(), u = new URL(req.url()), p = u.pathname, meth = req.method();
  const json = (body, status) => route.fulfill({ status: status || 200, contentType: "application/json", body: JSON.stringify(body) });
  const nf = () => json({ detail: "Not Found" }, 404);
  if (LIVE) {      // el servidor real; el selector nativo no se abre en una prueba
    if (p === "/pick-folder") { M.picks++; return json({ ok: false, canceled: true, path: null }); }
    if (p.startsWith("/export/estimate")) M.estimates.push(Object.fromEntries(u.searchParams));
    if (p.startsWith("/render/")) return json({ detail: "La prueba en vivo no exporta." }, 409);
    return route.continue();
  }
  if (p === "/settings/export") return SRV ? nf() : json(M.settings);
  if (p === "/export/settings") return SRV ? json({ last: M.last, folder: { path: M.last.folder, label: label(M.last.folder) } }) : nf();
  if (p === "/export/options") {
    if (!SRV) return nf();
    const fmt = u.searchParams.get("fmt") || "9:16";
    const formats = {}; Object.keys(FMTS).forEach(f => { formats[f] = { label: f, resolutions: rows(f) }; });
    const ok = formats[fmt].resolutions.filter(r => r.enabled).map(r => r.id);
    return json({ job: JOB, name: `Reseña de una bici urbana - ${today}`, ext: ".mp4", fmt, formats,
      source: { w: 1080, h: 1920, label: LBL[M.maxRes], kind: "single" }, qualities: QUALS.map(q => Object.assign({ default: q.id === "alta" }, q)),
      defaults: { res: ok.includes(M.last.res) ? M.last.res : "1080p", quality: M.last.quality, shorts: M.last.shorts, fmt },
      folder: M.folderGone   // la recordada esta en un disco desconectado: la de siempre, con aviso
        ? { path: DEFAULT_DIR, label: label(DEFAULT_DIR), name: "Reelfy", writable: true, error: null, default: true,
          notice: `Tu carpeta «${path.basename(M.folderGone)}» no está disponible (¿disco desconectado?); se usará ${label(DEFAULT_DIR)}.`,
          fallback_from: { path: M.folderGone, label: label(M.folderGone), name: path.basename(M.folderGone) } }
        : { path: M.last.folder, label: label(M.last.folder), name: path.basename(M.last.folder), writable: true, error: null, default: M.last.folder === DEFAULT_DIR },
      shorts_available: 2 });
  }
  if (p === "/export/estimate") {
    M.estimates.push(Object.fromEntries(u.searchParams));
    if (SRV && !SQ[u.searchParams.get("quality")]) return json({ detail: "Calidad desconocida: usa Estándar, Alta o Máxima." }, 400);
    return json(estimate(u.searchParams));
  }
  if (p === "/pick-folder" && meth === "POST") {
    M.picks++; const r = M.pick.shift() || { body: { path: null } };
    if (SRV && r.body && r.body.path) { M.allowed.add(r.body.path); return json({ ok: true, canceled: false, path: r.body.path, label: label(r.body.path), writable: true }, r.status); }
    if (SRV && r.body && r.body.path === null && !r.status) return json({ ok: false, canceled: true, path: null });
    return json(r.body, r.status);
  }
  if (p === "/reveal" && meth === "POST") { M.reveal.push(req.postDataJSON()); return json({ ok: true }); }
  if (p === "/open" && meth === "POST") { if (SRV) return nf(); M.open.push(req.postDataJSON()); return json({ ok: true }); }
  if (p === "/export/save" && meth === "POST") {
    if (!SRV) return nf();
    const b = req.postDataJSON(); M.saves.push(b);
    const name = M.lastName || "Reelfy";
    return json({ saved: true, path: `${b.folder}/${name}.mp4`, file_name: `${name}.mp4`, bytes: 498.2e6, folder: b.folder, folder_label: label(b.folder),
      how: "copy", shorts: [`${b.folder}/${name} - Short 1.mp4`, `${b.folder}/${name} - Short 2.mp4`] });
  }
  if (p === `/render/${JOB}` && meth === "POST") {
    const b = req.postDataJSON(); M.renders.push(b);
    const r = M.renderReply.shift();
    if (r) return json(r.body, r.status);
    const x = SRV ? b.export : { res: b.resolution, quality: b.quality, name: b.name, folder: b.folder, shorts: b.shorts };
    if (SRV) {
      if (!x || !SHORT[x.res] || !SQ[x.quality]) return json({ detail: "Resolución o calidad desconocida" }, 400);
      if (x.folder && !M.allowed.has(x.folder)) return json({ detail: "Elige la carpeta con «Cambiar…»: Reelfy solo guarda en carpetas que elegiste." }, 400);
      M.last = { res: x.res, quality: x.quality, shorts: !!x.shorts, folder: x.folder || M.last.folder };
    }
    M.lastName = x.name;
    M.sim = { i: 0, steps: simSteps(x) };
    const folder = x.folder || (SRV ? M.last.folder : DEFAULT_DIR);
    return json(Object.assign({ ok: true, pro: true }, SRV ? { export: { res: x.res, label: LBL[x.res], quality: x.quality, name: x.name,
      file_name: `${x.name}.mp4`, folder, folder_label: label(folder), shorts: !!x.shorts, recompose: SHORT[x.res] > M.combined } } : {}));
  }
  if (p === `/status/${JOB}` && M.sim) {
    const s = M.sim.steps[Math.min(M.sim.i++, M.sim.steps.length - 1)];
    if (M.sim.i >= M.sim.steps.length) M.sim = null;
    return json(s);
  }
  if (p.startsWith("/video/xp_out")) return route.continue({ url: `${BASE}/source/${JOB}` });   // el resultado simulado: el video del proyecto
  return route.continue();
});
/** Pasos de /status del export simulado `x` = {res, quality, name, folder, shorts}. */
function simSteps(x) {
  const base = { state: "running", phase: "rendering", disk_free_gb: 120, low_space: false };
  const hi = SHORT[x.res] > M.combined, W = SHORT[x.res], H = Math.round(W * 16 / 9);
  const steps = [];
  if (M.failRecompose) {
    steps.push({ ...base, pct: 4, stage: "recompose", message: "", elapsed: 2, eta: null });
    const error = "No se pudieron recomponer los clips en 4K: ffmpeg terminó con un error";
    steps.push(Object.assign({ state: "error", phase: "error", pct: 4, error },
      SRV ? { export: { state: "error", code: "recompose_failed", message: error, fallback_res: "1080p" } } : {}));
    return steps;
  }
  if (hi) for (let k = 0; k < 4; k++) steps.push({ ...base, pct: 3 + 3 * k, stage: "recompose", message: "", elapsed: 3 + 3 * k, eta: k ? 160 : null });
  steps.push({ ...base, pct: 40, stage: "render", message: "Componiendo efectos, textos y gráficos…", elapsed: 30, eta: 60 });
  const dir = x.folder || (SRV ? M.last.folder : DEFAULT_DIR);
  if (SRV) steps.push({ ...base, pct: 99.5, stage: "save", message: `Guardando en «${path.basename(dir)}»… 40 %`, elapsed: 60, eta: 1 });
  const bytes = Math.round(498.2e6 * (W / 2160) ** 2);
  const main = { name: "Video completo", file: "xp_out_main.mp4", duration: 74.4, W, H };
  if (SRV && M.saveFails) Object.assign(main, { saved: false, folder: dir, folder_label: label(dir), res: x.res, quality: x.quality, label: LBL[x.res], bytes,
    save_error: "No hay espacio en «Reelfy» para guardar el video. Tu video quedó en Reelfy: libera espacio y usa «Guardar de nuevo»." });
  else if (SRV) Object.assign(main, { saved: true, path: `${dir}/${x.name}.mp4`, file_name: `${x.name}.mp4`, folder: dir, folder_label: label(dir), bytes, res: x.res, quality: x.quality, label: LBL[x.res] });
  else Object.assign(main, { path: `${dir}/${x.name}.mp4`, bytes });
  const clips = [main];
  if (x.shorts) {
    const sh = [["Short 1: Frenos con lluvia", 1, 9, 62.4e6], ["Short 2: La cadena", 11, 19, 58.1e6]];
    sh.forEach(([name, start, end, by], i) => {
      const c = { name, file: `xp_out_s${i + 1}.mp4`, start, end, thumb: null };
      const sp = `${dir}/${x.name} - Short ${i + 1}.mp4`;
      if (SRV) { if (!M.saveFails) Object.assign(c, { path: sp, file_name: path.basename(sp) }); }
      else Object.assign(c, { path: sp, bytes: by, W: 1080, H: 1920, duration: end - start + 0.2 });
      clips.push(c);
    });
  }
  steps.push({ state: "done", phase: "done", pct: 100, stage: "done", message: "¡Listo!", clips, warnings: [] });
  return steps;
}

// ------------------------------------------------------------------ medidas
/** Simetria (px) de la hoja: hoja centrada, mismos margenes internos, botones de igual ancho. */
function measureSheet() {
  const R = e => (typeof e === "string" ? document.querySelector(e) : e).getBoundingClientRect();
  const box = R("#xpSheet .xp-box"), mid = r => r.left + r.width / 2;
  const flds = [...document.querySelectorAll("#xpSheet .xp-fld")].filter(e => e.offsetParent).map(R);
  const segs = [...document.querySelectorAll("#xpSheet .xp-seg")].map(R);
  const foot = [...document.querySelectorAll("#xpSheet .xp-foot .btn")].map(R);
  const h = R("#xpTitle"), hs = document.querySelector("#xpTitle");
  const range = document.createRange(); range.selectNodeContents(hs); const hc = range.getBoundingClientRect();
  return {
    sheetL: box.left, sheetR: innerWidth - box.right, sheetT: box.top, sheetB: innerHeight - box.bottom,
    fldL: Math.max(...flds.map(r => Math.abs(r.left - box.left))), fldR: Math.max(...flds.map(r => Math.abs(box.right - r.right))),
    fldLmin: Math.min(...flds.map(r => r.left - box.left)), fldRmin: Math.min(...flds.map(r => box.right - r.right)),
    segW: Math.max(...segs.map(r => r.width)) - Math.min(...segs.map(r => r.width)),
    estL: R("#xpEst").left - box.left, estR: box.right - R("#xpEst").right,
    footL: foot[0].left - box.left, footR: box.right - foot[1].right, footW: Math.abs(foot[0].width - foot[1].width),
    titleCenter: mid(hc) - mid(box), titleBox: mid(h) - mid(box),
    btnSpread: Math.max(...[...document.querySelectorAll("#xpSheet .xp-seg")].map(s => {
      const w = [...s.querySelectorAll("button")].map(b => b.getBoundingClientRect().width); return Math.max(...w) - Math.min(...w);
    })),
    overflowX: document.documentElement.scrollWidth - innerWidth, boxH: box.height, vh: innerHeight,
  };
}
/** Texto visible bajo `root`: tamano minimo y contraste WCAG (fondo compuesto, degradados incluidos). */
function textAudit(root) {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(",").map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const over = (a, b) => [0, 1, 2].map(i => a[i] * a[3] + b[i] * (1 - a[3])).concat([1]);
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const bgsOf = el => {      // fondos candidatos: con un degradado, cada color de sus paradas
    const stack = []; let grads = null;
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e), b = parse(cs.backgroundColor);
      const g = cs.backgroundImage && cs.backgroundImage !== "none" ? [...cs.backgroundImage.matchAll(/rgba?\([^)]+\)/g)].map(m => parse(m[0])) : [];
      if (g.length && !grads) { grads = { at: stack.length, cols: g }; }
      if (b && b[3] > 0) { stack.push(b); if (b[3] >= 1) break; }
    }
    const comp = extra => { let c = [10, 12, 16, 1]; const st = stack.slice(); if (extra) st.splice(grads.at, 0, extra);
      for (let i = st.length - 1; i >= 0; i--) c = over(st[i], c); return c; };
    return grads ? grads.cols.map(comp) : [comp(null)];
  };
  const out = { n: 0, small: [], low: [] };
  document.querySelectorAll(root + " *").forEach(el => {
    if (!el.offsetParent || el.closest("[disabled],[aria-disabled=true]")) return;
    const t = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join("");
    if (!t) return;
    const r = el.getBoundingClientRect(); if (r.bottom < 0 || r.top > innerHeight || r.width === 0) return;
    const cs = getComputedStyle(el), fs = parseFloat(cs.fontSize);
    out.n++;
    if (fs < 11) out.small.push(`${fs}px «${t.slice(0, 30)}»`);
    for (const bg of bgsOf(el)) {
      let fg = parse(cs.color);
      if (fg[3] < 1) fg = over(fg, bg);
      let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity;
      if (op < 1) fg = over([fg[0], fg[1], fg[2], op], bg);
      const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      const large = fs >= 24 || (fs >= 18.66 && +cs.fontWeight >= 700);
      if (ratio < (large ? 3 : 4.5)) { out.low.push(`${ratio.toFixed(2)} «${t.slice(0, 30)}» ${cs.color}`); break; }
    }
  });
  return out;
}
/** Espera a que terminen las animaciones de entrada (opacidad 1 en `sel` y sus ancestros): con la
    maquina ocupada el reloj de las animaciones avanza lento. */
async function settled(sel) {
  await page.waitForFunction(q => {
    const el = document.querySelector(q); if (!el) return false;
    for (let e = el; e; e = e.parentElement) { const cs = getComputedStyle(e); if (+cs.opacity < 1 || (cs.transform !== "none" && cs.transform !== "matrix(1, 0, 0, 1, 0, 0)")) return false; }
    return true;
  }, sel, { timeout: 15000 });
}
async function auditSheet(tag) {
  await settled("#xpSheet .xp-box");        // la animacion de entrada (pop) termina
  const m = await ev(measureSheet), ta = await ev(textAudit, "#xpSheet");
  log(tag, JSON.stringify(m), `texto: ${ta.n} (chicos ${ta.small.length}, bajo contraste ${ta.low.length})`);
  assert.ok(Math.abs(m.sheetL - m.sheetR) <= 1, `hoja centrada ${m.sheetL} vs ${m.sheetR}`);
  assert.ok(Math.abs(m.fldL - m.fldR) <= 1 && Math.abs(m.fldLmin - m.fldRmin) <= 1, `margenes de los campos ${m.fldL}/${m.fldR}`);
  assert.ok(Math.abs(m.estL - m.estR) <= 1, `estimacion ${m.estL}/${m.estR}`);
  assert.ok(Math.abs(m.footL - m.footR) <= 1 && m.footW <= 1, `pie ${m.footL}/${m.footR} ancho ${m.footW}`);
  assert.ok(Math.abs(m.titleCenter) <= 1.5, `titulo centrado ${m.titleCenter}`);
  assert.ok(m.btnSpread <= 1, `botones de cada selector del mismo ancho (${m.btnSpread})`);
  assert.ok(m.segW <= 1, "selectores del mismo ancho");
  assert.ok(m.overflowX <= 0, "sin scroll horizontal");
  assert.ok(m.sheetT >= 15 && m.sheetB >= 15, `la hoja cabe en la ventana (${m.sheetT}/${m.sheetB})`);
  assert.deepEqual(ta.small, [], "texto de menos de 11 px");
  assert.deepEqual(ta.low, [], "texto sin contraste AA");
  return m;
}
/** Espera (en node) a que fn() sea verdadero. */
async function until(fn, what, ms) {
  const t0 = Date.now();
  while (!fn()) { if (Date.now() - t0 > (ms || 8000)) throw new Error("tiempo agotado: " + what); await new Promise(r => setTimeout(r, 50)); }
}
const sel = s => ev(q => { const b = document.querySelector(q + " button.sel"); return b ? b.dataset.v : null; }, s);
const offs = s => ev(q => [...document.querySelectorAll(q + " button.off")].map(b => b.dataset.v), s);
const waitEst = () => page.waitForFunction(() => /Tardará/.test(document.querySelector("#xpEst").textContent), null, { timeout: 8000 });
async function openSheet() {
  await page.click("#render");
  await page.waitForSelector("#xpSheet.on");
  await waitEst();
}

try {
  // ---------------------------------------------------------------- 0. editor del proyecto sintetico
  await page.goto(`${BASE}/?job=${JOB}`);
  await ev(() => { try { localStorage.clear(); } catch (e) { /* nada */ } });
  await page.goto(`${BASE}/?job=${JOB}`);
  await page.waitForSelector("#s-editor.on", { timeout: 30000 });
  await page.waitForFunction(() => typeof PLAN !== "undefined" && PLAN && FX.isPro() && V.videoWidth > 0, null, { timeout: 30000 });
  await page.waitForTimeout(500);

  if (LIVE) {
    // ---------------------------------------------------------------- servidor real: la hoja se llena con sus datos
    await openSheet();
    assert.equal(await ev(() => document.querySelector("#xpName").value), `Reseña de una bici urbana - ${today}`);
    assert.equal(await sel("#xpFmt"), "9:16");
    assert.equal(await sel("#xpRes"), "1080p");
    assert.deepEqual(await offs("#xpRes"), ["1440p", "4k"]);
    assert.match(await txt("#xpResNote"), /^Tu video original es \d+p; 1440p y 4K no agregarían calidad\.$/);
    assert.deepEqual(await ev(() => [...document.querySelectorAll("#xpQual button")].map(b => b.textContent)), ["Estándar", "Alta", "Máxima"]);
    assert.match(await txt("#xpEst .xp-eline"), /^Tardará (menos de 1 min|~\d+ min) · ~[\d.]+ (MB|GB)$/);
    assert.equal(M.estimates.at(-1).quality, "alta");
    assert.ok((await ev(() => document.querySelector("#xpDir").title)).length > 1, "carpeta del servidor");
    await auditSheet("en vivo");
    await shot("01_hoja_servidor_real");
    await page.click('#xpQual button[data-v="max"]');
    await until(() => M.estimates.at(-1).quality === "maxima", "estimacion real con calidad maxima");
    await page.click('#xpFmt button[data-v="16:9"]');
    await page.waitForFunction(() => [...document.querySelectorAll("#xpRes button span")].map(s => s.textContent).join() === "1280 × 720,1920 × 1080,2560 × 1440,3840 × 2160");
    await page.click('#xpFmt button[data-v="9:16"]');
    await page.click("#xpPick");
    await page.waitForFunction(() => !document.querySelector("#xpPick").disabled);
    assert.equal(M.picks, 1);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    assert.deepEqual(httpErrs, [], "el servidor real respondio a todo");
    assert.deepEqual(errs, [], "errores en la consola");
    log("OK: hoja Exportar (servidor real)", `${M.estimates.length} estimaciones`);
    await browser.close();
    process.exit(0);
  }

  // ---------------------------------------------------------------- 1. abre la hoja con los valores por defecto
  await openSheet();
  assert.equal(await ev(() => document.querySelector("#s-editor").classList.contains("on")), true, "la hoja no exporta directo");
  assert.equal(M.renders.length, 0);
  assert.equal(await ev(() => document.querySelector("#xpName").value), `Reseña de una bici urbana - ${today}`);
  assert.equal(await txt("#xpDir"), "~/Movies/Reelfy");
  assert.equal(await ev(() => document.querySelector("#xpDir").title), `${HOME}/Movies/Reelfy`);
  assert.equal(await sel("#xpFmt"), "9:16");
  assert.equal(await sel("#xpRes"), "1080p");
  assert.equal(await sel("#xpQual"), "high");
  assert.deepEqual(await offs("#xpRes"), ["1440p", "4k"]);
  assert.equal(await txt("#xpResNote"), "Tu video original es 1080p; 1440p y 4K no agregarían calidad.");
  assert.equal(await txt("#xpEst"), "Tardará ~2 min · ~210 MB");
  assert.equal(await ev(() => document.querySelector("#xpShorts").checked), true);
  assert.equal(await txt("#xpShortsN"), "(2)");
  const e0 = M.estimates[M.estimates.length - 1];
  assert.deepEqual([e0.job, e0.res, e0.fmt, e0.shorts, e0.quality], [JOB, "1080p", "9:16", "1", "alta"]);   // la calidad en el idioma del servidor
  assert.equal(await ev(() => document.activeElement.id), "xpGo", "el foco empieza en Exportar");
  assert.deepEqual(await ev(() => [...document.querySelectorAll("#xpRes button")].map(b => b.querySelector("span").textContent)),
    ["720 × 1280", "1080 × 1920", "1440 × 2560", "2160 × 3840"]);
  await auditSheet("1440x900");
  await shot("01_hoja_1440x900");
  // una resolucion no disponible no se elige; el aviso parpadea
  await page.click('#xpRes button[data-v="4k"]', { force: true });     // aria-disabled: se puede pulsar, no se elige
  assert.equal(await sel("#xpRes"), "1080p");
  assert.equal(await ev(() => document.querySelector("#xpResNote").classList.contains("xp-flash")), true, "el aviso parpadea");
  await page.setViewportSize({ width: 1200, height: 760 });
  await auditSheet("1200x760");
  await shot("02_hoja_1200x760");
  await page.setViewportSize({ width: 1440, height: 900 });

  // ---------------------------------------------------------------- 2. teclado: Espacio no reproduce, flechas, Tab atrapado, Esc
  await page.focus('#xpQual button[data-v="high"]');
  await page.keyboard.press("Space");
  await page.waitForTimeout(250);
  assert.equal(await ev(() => V.paused), true, "Espacio en la hoja no reproduce el video");
  await page.keyboard.press("ArrowRight");
  assert.equal(await sel("#xpQual"), "max");
  assert.equal(await ev(() => document.activeElement.dataset.v), "max", "el foco sigue en el selector");
  assert.equal(await txt("#xpQualNote"), SRV ? "Máximo detalle, archivo más pesado." : "Máximo detalle para editar o archivar; el archivo pesa más.");
  await until(() => M.estimates.at(-1).quality === "maxima", "estimacion con calidad maxima");
  await page.keyboard.press("ArrowLeft");
  assert.equal(await sel("#xpQual"), "high");
  for (let i = 0; i < 30; i++) await page.keyboard.press("Tab");
  assert.equal(await ev(() => !!document.activeElement.closest("#xpSheet")), true, "Tab no sale de la hoja");
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => !document.querySelector("#xpSheet").classList.contains("on"));
  assert.equal(await ev(() => document.activeElement.id), "render", "el foco vuelve a Exportar");
  assert.equal(M.renders.length, 0);

  // ---------------------------------------------------------------- 3. fuente 4K: todas las resoluciones; 4K recompone
  M.maxRes = "4k"; M.sourceRes = "2160x3840";
  await openSheet();
  await page.waitForFunction(() => !document.querySelector('#xpRes button[data-v="4k"]').classList.contains("off"));
  assert.deepEqual(await offs("#xpRes"), []);
  assert.equal(await txt("#xpResNote"), "");
  await page.click('#xpRes button[data-v="4k"]');
  await waitEst();
  await page.waitForFunction(() => /en 4K/.test(document.querySelector("#xpEst").textContent));
  assert.equal(M.estimates[M.estimates.length - 1].res, "4k");
  assert.equal(await txt("#xpEst .xp-eline"), "Tardará ~9 min · ~840 MB");
  assert.equal(await txt("#xpEst .xp-esub"), "Primero se prepara el video en 4K desde tu video original.");
  await auditSheet("4k");
  await shot("03_hoja_4k");

  // ---------------------------------------------------------------- 4. formato desde la hoja = formato del proyecto
  await page.click('#xpFmt button[data-v="16:9"]');
  await page.waitForFunction(() => FMT === "16:9");
  assert.equal(await sel("#segFmt"), "16:9", "el selector del panel sigue a la hoja");
  assert.equal(await ev(() => PLAN.studio.fmt), "16:9");
  assert.deepEqual(await ev(() => [...document.querySelectorAll("#xpRes button")].map(b => b.querySelector("span").textContent)),
    ["1280 × 720", "1920 × 1080", "2560 × 1440", "3840 × 2160"]);
  assert.equal(await ev(() => document.querySelector("#xpShorts").disabled), true);
  assert.equal(await txt("#xpShortsNote"), "Los shorts son verticales: con el formato 16:9 no se generan.");
  await until(() => M.estimates.at(-1).fmt === "16:9", "estimacion en 16:9");
  assert.equal(M.estimates.at(-1).shorts, "0");
  await waitEst();
  await shot("04_hoja_16x9");
  await page.click('#xpFmt button[data-v="9:16"]');
  await page.waitForFunction(() => FMT === "9:16");
  assert.equal(await ev(() => document.querySelector("#xpShorts").checked), true, "vuelven los shorts en vertical");

  // ---------------------------------------------------------------- 5. nombre saneado
  await page.fill("#xpName", "Mi video: final/v2.mp4");
  assert.equal(await txt("#xpNameNote"), "Se guardará como «Mi video final v2.mp4» (sin / \\ : * ? \" < > |).");
  await page.click("#xpTitle");          // blur
  assert.equal(await ev(() => document.querySelector("#xpName").value), "Mi video final v2");
  await page.fill("#xpName", "   ");
  assert.equal(await txt("#xpNameNote"), `Se usará «Reseña de una bici urbana - ${today}.mp4».`);
  await page.fill("#xpName", "Entrega cliente");

  // ---------------------------------------------------------------- 6. carpeta: selector nativo (elegir, cancelar, sin permiso)
  assert.equal(await ev(() => document.querySelectorAll("#xpSheet input").length), 2, "la ruta no se escribe a mano");
  M.pick.push({ body: { path: PICKED } });
  await page.click("#xpPick");
  await page.waitForFunction(p => document.querySelector("#xpDir").title === p, PICKED);
  const shown = await txt("#xpDir");
  assert.ok(shown.startsWith("~/…/") && shown.endsWith("/Videos finales/Reelfy") && shown.length <= 46, shown);
  M.pick.push({ body: { path: null } });              // cancelo el dialogo
  await page.click("#xpPick");
  await page.waitForFunction(() => !document.querySelector("#xpPick").disabled);
  assert.equal(await ev(() => document.querySelector("#xpDir").title), PICKED, "cancelar no cambia la carpeta");
  const noPerm = SRV ? "No se puede escribir en «System»: elige otra carpeta." : "PermissionError: [Errno 13] Permission denied: '/System'";
  M.pick.push({ status: 400, body: { detail: noPerm } });
  await page.click("#xpPick");
  await page.waitForFunction(() => document.querySelector("#xpDirNote").textContent.length > 0);
  // un mensaje del servidor en espanol se muestra tal cual; uno tecnico, con el texto de la hoja
  assert.equal(await txt("#xpDirNote"), SRV ? noPerm : "No se puede guardar en la carpeta elegida. Elige otra carpeta con «Cambiar…».");
  assert.equal(await ev(() => document.querySelector("#xpDir").title), PICKED);
  assert.equal(M.picks, 3);
  await shot("05_hoja_carpeta_sin_permiso");

  // ---------------------------------------------------------------- 7. sin espacio al iniciar: la hoja sigue abierta con el error
  const noSpace = SRV ? "No hay espacio suficiente para exportar en 4K: hacen falta ~3.1 GB y quedan 1.2 GB. Libera espacio o elige una resolución menor."
    : "OSError: [Errno 28] No space left on device";
  M.renderReply.push({ status: 507, body: { detail: noSpace } });
  await page.click("#xpGo");
  await page.waitForFunction(() => !document.querySelector("#xpErr").hidden);
  assert.equal(await txt("#xpErr"), SRV ? noSpace : "No hay espacio suficiente en el disco para exportar. Libera espacio o elige una carpeta en otro disco.");
  assert.equal(await ev(() => document.querySelector("#xpSheet").classList.contains("on")), true);
  assert.equal(await ev(() => document.querySelector("#s-editor").classList.contains("on")), true);
  await auditSheet("error");
  await shot("06_hoja_error_espacio");

  // ---------------------------------------------------------------- 8. exporta en 4K: etapas -> resultados
  await page.click("#xpGo");
  await page.waitForSelector("#s-rendering.on");
  const body = M.renders.at(-1);
  // las dos formas viajan: la plana del contrato y `export` del servidor (calidad estandar|alta|maxima)
  assert.deepEqual({ r: body.resolution, q: body.quality, n: body.name, f: body.folder, s: body.shorts, fmt: body.fmt },
    { r: "4k", q: "high", n: "Entrega cliente", f: PICKED, s: true, fmt: "9:16" });
  assert.deepEqual(body.export, { res: "4k", quality: "alta", name: "Entrega cliente", folder: PICKED, shorts: true });
  assert.ok(body.plan && body.plan.version === 2, "el plan viaja como siempre");
  assert.equal(await ev(() => document.querySelector("#xpSheet").classList.contains("on")), false);
  await page.waitForFunction(() => /Video en 4K/.test(document.querySelector("#r-stages").textContent));
  assert.match(await txt("#r-dest"), /^Entrega cliente\.mp4 en ~\/…\/.*Reelfy · 4K · Calidad alta$/);
  await page.waitForFunction(() => document.querySelector("#r-msg").textContent === "Preparando el video en 4K…");
  await page.waitForTimeout(420);         // la pantalla termina de entrar
  assert.equal(await ev(() => document.querySelector("#r-stages .stg.cur").textContent), "Video en 4K");
  await shot("07_exportando_4k");
  await page.waitForSelector("#s-results.on", { timeout: 20000 });
  await page.waitForTimeout(500);
  assert.equal(await txt("#clips .xp-hero .xp-fname"), "Entrega cliente.mp4");
  assert.equal(await ev(() => document.querySelector("#clips .xp-hero .xp-fname").title), `${PICKED}/Entrega cliente.mp4`);
  assert.equal(await ev(() => document.querySelector("#clips .xp-hero .xp-fdir").title), PICKED);
  assert.deepEqual(await ev(() => [...document.querySelectorAll("#clips .xp-hero .xp-chip")].map(c => c.textContent)), ["2160 × 3840 · 4K", "1:14", "498 MB"]);
  assert.deepEqual(await ev(() => [...document.querySelectorAll("#clips .xp-hero .xp-acts .btn")].map(b => b.textContent.trim())),
    ["Mostrar en Finder", "Abrir"]);              // Chrome en macOS
  assert.equal(await ev(() => document.querySelectorAll("#clips .clip:not(.xp-hero)").length), 2);
  assert.match(await txt("#clips .gtitle"), /^Shorts \(2\) · en la misma carpeta$/);
  assert.equal(await txt("#clips .clip:not(.xp-hero) .xp-meta"), SRV ? "Entrega cliente - Short 1.mp40:08" : "Entrega cliente - Short 1.mp41080 × 1920 · 0:08 · 62 MB");
  const rs = await ev(() => {     // simetria de la tarjeta principal y de la cuadricula
    const R = q => document.querySelector(q).getBoundingClientRect();
    const card = R("#s-results > .card"), hero = R("#clips .xp-hero");
    const rs = [...document.querySelectorAll("#clips .clip:not(.xp-hero)")].map(e => e.getBoundingClientRect());
    const cards = rs.map(r => r.width);
    const acts = [...document.querySelectorAll("#clips .clip:not(.xp-hero) .xp-acts .btn")].map(e => e.getBoundingClientRect().width);
    return { heroL: hero.left - card.left, heroR: card.right - hero.right,
      gridL: Math.min(...rs.map(r => r.left)) - card.left, gridR: card.right - Math.max(...rs.map(r => r.right)),
      cardSpread: Math.max(...cards) - Math.min(...cards), actSpread: Math.max(...acts) - Math.min(...acts), overflowX: document.documentElement.scrollWidth - innerWidth,
      footL: (() => { const f = [...document.querySelectorAll(".xp-resfoot .btn")].filter(b => b.offsetParent).map(b => b.getBoundingClientRect());
        return Math.min(...f.map(r => r.left)) - card.left - (card.right - Math.max(...f.map(r => r.right))); })() };
  });
  log("resultados", JSON.stringify(rs));
  assert.ok(Math.abs(rs.heroL - rs.heroR) <= 1 && Math.abs(rs.gridL - rs.gridR) <= 1, "resultados simetricos");
  assert.ok(rs.cardSpread <= 1 && rs.actSpread <= 1 && rs.overflowX <= 0);
  assert.ok(Math.abs(rs.footL) <= 1, `botones del pie centrados (${rs.footL})`);
  await settled("#clips .xp-hero");
  const ra = await ev(textAudit, "#s-results");
  assert.deepEqual(ra.small, [], "resultados: texto de menos de 11 px");
  assert.deepEqual(ra.low, [], "resultados: contraste AA");
  await shot("08_resultados_1440x900");
  if (OUT) await page.screenshot({ path: path.join(OUT, "08b_resultados_pagina_completa.png"), fullPage: true });
  await page.setViewportSize({ width: 1200, height: 760 });
  await page.waitForTimeout(300);
  assert.equal(await ev(() => document.documentElement.scrollWidth - innerWidth), 0);
  await shot("09_resultados_1200x760");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.click("#clips .xp-hero [data-xa=reveal]");
  await page.click("#clips .xp-hero [data-xa=open]");
  await page.click("#clips .clip:not(.xp-hero) [data-xa=reveal] >> nth=1");
  await page.waitForTimeout(300);
  if (SRV) assert.deepEqual(M.reveal.map(x => [x.path, x.action]), [[`${PICKED}/Entrega cliente.mp4`, "reveal"], [`${PICKED}/Entrega cliente.mp4`, "open"],
    [`${PICKED}/Entrega cliente - Short 2.mp4`, "reveal"]]);
  else {
    assert.deepEqual(M.reveal.map(x => x.path), [`${PICKED}/Entrega cliente.mp4`, `${PICKED}/Entrega cliente - Short 2.mp4`]);
    assert.deepEqual(M.open.map(x => x.path), [`${PICKED}/Entrega cliente.mp4`]);
  }

  // ---------------------------------------------------------------- 9. lo elegido se recuerda
  await page.click("#backEditor");
  await page.waitForSelector("#s-editor.on");
  await openSheet();
  assert.equal(await sel("#xpRes"), "4k");
  assert.equal(await ev(() => document.querySelector("#xpName").value), "Entrega cliente");
  assert.equal(await ev(() => document.querySelector("#xpDir").title), PICKED);
  await page.click('#xpQual button[data-v="standard"]');
  await page.click("#xpShorts");
  assert.equal(await txt("#xpShortsNote"), "Solo se exporta el video completo.");
  await page.keyboard.press("Escape");
  await page.reload();
  await page.waitForSelector("#s-editor.on", { timeout: 30000 });
  await page.waitForFunction(() => typeof PLAN !== "undefined" && PLAN && V.videoWidth > 0, null, { timeout: 30000 });
  await openSheet();
  await page.waitForFunction(() => document.querySelector("#xpRes button.sel").dataset.v === "4k", null, { timeout: 8000 });
  assert.equal(await sel("#xpRes"), "4k", "la resolucion se recuerda tras recargar");
  assert.equal(await sel("#xpQual"), "high", "cerrar sin exportar no cambia lo recordado");
  assert.equal(await ev(() => document.querySelector("#xpName").value), "Entrega cliente", "el nombre se recuerda por proyecto");
  // la carpeta viene del servidor: el de Reelfy recuerda la del ultimo export; el minimo, la suya
  assert.equal(await ev(() => document.querySelector("#xpDir").title), SRV ? PICKED : DEFAULT_DIR, "carpeta recordada por el servidor");

  // ---------------------------------------------------------------- 10. falla al recomponer -> "Exportar en 1080p"
  M.failRecompose = true;
  await page.click("#xpGo");
  await page.waitForSelector("#s-error.on", { timeout: 20000 });
  assert.equal(await txt("#err-msg"), "No se pudo preparar el video en alta resolución desde tus clips originales. Puedes exportarlo en 1080p.");
  assert.equal(await ev(() => document.querySelector("#retry1080").hidden), false);
  await settled("#err-msg");
  const ea = await ev(textAudit, "#s-error");
  assert.deepEqual(ea.low, [], "error: contraste AA");
  await shot("10_error_recomponer");
  M.failRecompose = false;
  await page.click("#retry1080");
  await page.waitForSelector("#s-results.on", { timeout: 20000 });
  const b2 = M.renders.at(-1);
  assert.deepEqual([b2.resolution, b2.name, b2.folder, b2.export.res], ["1080p", "Entrega cliente", SRV ? PICKED : DEFAULT_DIR, "1080p"]);
  assert.deepEqual(await ev(() => [...document.querySelectorAll("#clips .xp-hero .xp-chip")].map(c => c.textContent)), ["1080 × 1920 · 1080p", "1:14", "125 MB"]);
  await shot("11_resultados_1080p");

  if (SRV) {
    // -------------------------------------------------------------- 11. poco espacio (estimacion) y "Guardar de nuevo"
    await page.click("#backEditor");
    await page.waitForSelector("#s-editor.on");
    M.lowSpace = true;
    await openSheet();
    await page.waitForFunction(() => /No hay espacio suficiente/.test(document.querySelector("#xpEst").textContent));
    assert.match(await txt("#xpEst .xp-warn"), /^No hay espacio suficiente: hacen falta ~[\d.]+ [MG]B y quedan 1\.2 GB\. Libera espacio o elige una resolución menor\.$/);
    await shot("12_hoja_poco_espacio");
    M.lowSpace = false; M.saveFails = true;
    await page.click('#xpRes button[data-v="1080p"]');
    await page.click("#xpGo");
    await page.waitForSelector("#s-results.on", { timeout: 20000 });
    await page.waitForTimeout(400);
    assert.match(await txt("#clips .xp-hero .xp-saveerr"), /^No hay espacio en «Reelfy» para guardar el video/);
    assert.equal(await txt("#clips .xp-hero .xp-fname"), "Entrega cliente.mp4", "el nombre elegido, no el interno");
    assert.equal(await txt("#clips .gtitle"), "Shorts (2)");
    assert.equal(await ev(() => document.querySelectorAll("#clips .xp-hero [data-xa=reveal]").length), 0, "sin archivo guardado no hay Mostrar");
    await settled("#clips .xp-hero");
    const sa = await ev(textAudit, "#s-results");
    assert.deepEqual(sa.low, [], "guardar de nuevo: contraste AA");
    await shot("13_resultado_sin_guardar");
    const OTHER = "/Volumes/Disco externo/Reelfy";
    M.pick.push({ body: { path: OTHER } });
    await page.click("#clips .xp-hero [data-xa=resave]");
    await page.waitForFunction(() => !!document.querySelector("#clips .xp-hero [data-xa=reveal]"), null, { timeout: 8000 });
    assert.deepEqual(M.saves.at(-1), { job: JOB, file: "xp_out_main.mp4", folder: OTHER });
    assert.equal(await txt("#clips .xp-hero .xp-fname"), "Entrega cliente.mp4");
    assert.equal(await txt("#clips .xp-hero .xp-fdir"), OTHER);
    assert.equal(await ev(() => document.querySelector("#clips .clip:not(.xp-hero)").dataset.path), `${OTHER}/Entrega cliente - Short 1.mp4`);
    await shot("14_guardado_de_nuevo");
    M.saveFails = false;
  }

  if (SRV) {
    // -------------------------------------------------------------- 12. revision: carpeta desconectada, clips de distinta resolucion, emoji
    // la carpeta recordada esta en un disco desconectado: la de siempre CON aviso (no en silencio)
    const GONE = "/Volumes/USB/Entregas";
    M.folderGone = GONE; M.mixed = true; M.last.folder = GONE; M.allowed.add(GONE);
    await page.reload();
    await page.waitForSelector("#s-editor.on", { timeout: 30000 });
    await page.waitForFunction(() => typeof PLAN !== "undefined" && PLAN && V.videoWidth > 0, null, { timeout: 30000 });
    await openSheet();
    assert.equal(await ev(() => document.querySelector("#xpDir").title), DEFAULT_DIR, "la de siempre mientras no esta el disco");
    assert.equal(await txt("#xpDirNote"), "Tu carpeta «Entregas» no está disponible (¿disco desconectado?); se usará ~/Movies/Reelfy.");
    assert.deepEqual(await ev(() => [...document.querySelector("#xpDirNote").classList].filter(c => /^xp-(bad|notice)$/.test(c))), ["xp-notice"],
      "un aviso, no un error");
    // 4K con 2 de 9 clips en 1080p: se ofrece y avisa cuantos se escalan
    await page.click('#xpRes button[data-v="4k"]');
    await waitEst();
    await page.waitForFunction(() => /escalados en 4K/.test(document.querySelector("#xpEst").textContent));
    assert.equal(await txt("#xpEst .xp-warn"), "2 de 9 clips son 1080p: se verán escalados en 4K.");
    await auditSheet("aviso 1440x900");
    await shot("16_aviso_carpeta_y_clips_1440x900");
    await page.setViewportSize({ width: 1200, height: 760 });
    await auditSheet("aviso 1200x760");
    await shot("17_aviso_carpeta_y_clips_1200x760");
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.click('#xpRes button[data-v="1080p"]');
    await waitEst();
    assert.equal(await ev(() => document.querySelectorAll("#xpEst .xp-warn").length), 0, "1080p no escala nada");
    // nombre con emoji compuestos (ZWJ) y ZWNJ: se conservan al salir del campo y al exportar
    const emo = "👨\u200d💻 Demo 🏳\ufe0f\u200d🌈 می\u200cخواهم";
    await page.fill("#xpName", emo);
    await page.click("#xpTitle");          // blur
    assert.equal(await ev(() => document.querySelector("#xpName").value), emo);
    assert.equal(await txt("#xpNameNote"), "");
    await page.click("#xpGo");
    await page.waitForSelector("#s-results.on", { timeout: 20000 });
    const b3 = M.renders.at(-1);
    assert.deepEqual([b3.export.name, b3.export.folder, b3.export.res], [emo, DEFAULT_DIR, "1080p"]);
    assert.equal(await txt("#clips .xp-hero .xp-fname"), `${emo}.mp4`);
    await shot("18_resultado_nombre_emoji");
    M.folderGone = null; M.mixed = false;
  }

  // ---------------------------------------------------------------- 13. consola
  assert.deepEqual(errs, [], "errores en la consola");
  log(`OK: hoja Exportar (${MODE})`, `${M.estimates.length} estimaciones, ${M.renders.length} exports, ${M.picks} selectores`);
} catch (e) {
  console.error("FALLO:", e.message);
  await shot("zz_fallo").catch(() => {});
  console.error(errs.join("\n"));
  await browser.close();
  process.exit(1);
}
await browser.close();
