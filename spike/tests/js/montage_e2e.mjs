// E2E de "Armar con IA" en un navegador real (Playwright + Chrome) contra un servidor AISLADO.
// uso: REELFY_PLAYWRIGHT=<ruta a playwright-core> node tests/js/montage_e2e.mjs <url base> <carpeta de clips> [carpeta de capturas]
// La carpeta de clips trae los 8 clips SINTETICOS de tests/js/montage_fixture.js (PAN_*.MOV con su
// creation_time; ver tests/test_studio_ui.py). Las subidas y /sources son las reales del servidor; el
// trabajo de montaje (/montage, su /status y /plan) y /compose se simulan en el navegador con un
// plan.montage sintetico sobre los sids reales (el motor de montaje tiene sus propias pruebas).
// Revisa: guion opcional + borrador, "Armar con IA" (subida de a un archivo, etapas honestas),
// ensamblado desde plan.montage, panel "Tomas" (alternativas, "Usar esta toma", deshacer, escuchar,
// extra, descartadas, guion detectado + "Rearmar"), color por confianza, recargar (?job=), "Aplicar IA"
// (montage_job + guion + montage por item), vuelta a las tomas si la composicion falla, proyectos
// recientes, "Armar a mano" en orden de grabacion, cancelar, simetria, texto >= 11 px, contraste AA y
// consola sin errores. Sale con codigo 1 al primer fallo.
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("define REELFY_PLAYWRIGHT (ruta a playwright-core)"); process.exit(2); }
const { chromium } = require(PW);
const { CLIPS, UPLOAD_ORDER, SCRIPT, synthMontage } = require("./montage_fixture.js");
const BASE = (process.argv[2] || "http://127.0.0.1:8601").replace(/\/$/, "");
const CLIPDIR = process.argv[3];
const OUT = process.argv[4] || null;
if (!CLIPDIR) { console.error("falta la carpeta de clips"); process.exit(2); }
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const FILES = UPLOAD_ORDER.map(k => path.join(CLIPDIR, CLIPS.find(c => c.key === k).name));

const browser = await chromium.launch({ headless: true, channel: process.env.REELFY_PW_CHANNEL || "chrome",
  args: ["--autoplay-policy=no-user-gesture-required"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", e => errs.push("pageerror " + e.message));
page.on("console", m => { if (m.type() === "error") errs.push("console " + m.text()); });
page.on("response", r => { if (r.status() >= 400) errs.push(`HTTP ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
page.on("dialog", d => d.dismiss());
const log = (...a) => console.log("·", ...a);
const shot = async n => { if (OUT) { await page.waitForTimeout(420); await page.screenshot({ path: path.join(OUT, n + ".png") }); } };   // sin animaciones a medias
const ev = (fn, arg) => page.evaluate(fn, arg);
const size = async (w, h) => { await page.setViewportSize({ width: w, height: h }); await page.waitForTimeout(350); };

// ---------------------------------------------------------------- servidor simulado del montaje
const JOB = "a1b2c3d4e5f6";
const sidByName = {};
// subidas: si pidieron la transcripcion por clip (transcribe=1) y una falla inyectada (disco lleno)
const uploads = [];
const FAIL = { name: null, injected: 0 };
page.on("request", req => {
  if (req.method() !== "POST" || new URL(req.url()).pathname !== "/sources") return;
  const body = req.postData() || "", m = /name="transcribe"\r\n\r\n([01])/.exec(body), f = /filename="([^"]+)"/.exec(body);
  uploads.push({ name: f ? f[1] : null, transcribe: m ? m[1] : null });
});
await page.route("**/sources", async route => {
  const req = route.request();
  if (req.method() === "POST" && FAIL.name && (req.postData() || "").includes(`filename="${FAIL.name}"`)) {
    FAIL.name = null; FAIL.injected++;
    return route.fulfill({ status: 500, json: { detail: "Disco lleno" } });
  }
  return route.continue();
});
const ST = { phase: "idle", polls: 0, montage: null, jobs: null };
const bodies = { montage: [], compose: [], cancel: 0 };
page.on("response", async r => {
  if (r.request().method() === "POST" && new URL(r.url()).pathname === "/sources" && r.ok()) {
    try { (await r.json()).sources.forEach(s => { sidByName[s.name] = s.id; }); } catch (e) { /* nada */ }
  }
});
const sidMap = () => Object.fromEntries(CLIPS.map(c => [c.key, sidByName[c.name]]));
function status(withPlan) {
  const base = { plan_rev: 1, warnings: [], ext: ".mp4", kind: "montage", pct: 0, eta: null, elapsed: 0, clips: null, error: null };
  if (ST.phase === "words") {
    const done = Math.min(8, 2 + ST.polls);
    return { ...base, phase: "assembly", state: "running", stage: "words", pct: 5 + done * 8, has_plan: false,
      message: `Transcribiendo tus clips (${done}/8)…`, words: { state: "running", done, total: 8, errors: 0 } };
  }
  if (ST.phase === "montage") return { ...base, phase: "assembly", state: "running", stage: "montage", pct: 80, has_plan: false, message: "Eligiendo las mejores tomas…" };
  if (ST.phase === "ready") return { ...base, phase: "assembly", state: "review", stage: "assembly", pct: 100, has_plan: true, message: "Tomas listas",
    ...(withPlan ? { plan: { montage: ST.montage } } : {}) };
  if (ST.phase === "compose") return { ...base, kind: "compose", phase: "analyzing", state: "running", stage: "compose", pct: 12, has_plan: true, message: "Montando tu línea de tiempo…" };
  if (ST.phase === "back") return { ...base, phase: "assembly", state: "review", stage: "assembly", pct: 100, has_plan: true,
    message: "La composición falló: prueba", error: "La composición falló: prueba", ...(withPlan ? { plan: { montage: ST.montage } } : {}) };
  return { ...base, phase: "error", state: "error", error: "sin trabajo" };
}
await page.route("**/montage", async route => {
  const b = JSON.parse(route.request().postData() || "{}");
  bodies.montage.push(b);
  // la respuesta de la ultima subida (sid de cada nombre) puede llegar un instante despues
  for (let i = 0; i < 60 && CLIPS.some(c => !sidByName[c.name]); i++) await new Promise(r => setTimeout(r, 50));
  // rearmar (mismo proyecto) devuelve ademas un extra aprobado (parte sin lineas, como el servidor)
  ST.montage = synthMontage(sidMap(), { detected: !b.script, keptExtra: !!(b.script && b.job_id) });
  if (b.script) {                       // con guion: el servidor lo parte en lineas (aqui, las mismas 6)
    ST.montage.script.text = b.script; ST.montage.ms = 777;                   // otro montaje (otra firma)
    const ls = b.script.split("\n").map(x => x.trim()).filter(Boolean);
    if (ls.length === ST.montage.script.lines.length) ST.montage.script.lines.forEach((L, i) => { L.text = ls[i]; });
  }
  ST.polls = 0; ST.phase = ST.hold || "words";
  await route.fulfill({ json: { job_id: JOB } });
});
await page.route(`**/status/${JOB}*`, async route => {
  const u = new URL(route.request().url());
  ST.polls++;
  await route.fulfill({ json: status(u.searchParams.get("plan") === "1") });
});
await page.route(`**/plan/${JOB}`, route => route.fulfill({ json: { plan: { montage: ST.montage }, plan_rev: 1 } }));
await page.route("**/compose", async route => {
  bodies.compose.push(JSON.parse(route.request().postData() || "{}"));
  ST.phase = "compose";
  await route.fulfill({ json: { job_id: JOB } });
});
await page.route(`**/cancel/${JOB}*`, async route => { bodies.cancel++; ST.phase = "error"; await route.fulfill({ json: { ok: true, side: 0 } }); });
await page.route("**/jobs?*", async route => { if (!ST.jobs) return route.continue(); await route.fulfill({ json: ST.jobs }); });

// ---------------------------------------------------------------- medidas
/** Simetria (px) de lo que se agrego: paneles, pares de botones y sin scroll horizontal. */
function measure(sel) {
  const R = e => e.getBoundingClientRect();
  const vis = s => [...document.querySelectorAll(s)].filter(e => e.offsetParent);
  const spread = a => (a.length ? Math.max(...a) - Math.min(...a) : 0);
  const out = { overflowX: document.documentElement.scrollWidth - innerWidth, pairs: 0, pairSpread: 0, inset: 0, rows: 0 };
  // pares/grupos de botones del mismo renglon: mismo ancho
  vis(sel.groups).forEach(g => { const w = [...g.children].filter(c => c.offsetParent).map(c => R(c).width); if (w.length > 1) { out.pairs++; out.pairSpread = Math.max(out.pairSpread, spread(w)); } });
  // tarjetas dentro de su panel: mismo margen a izquierda y derecha
  const box = document.querySelector(sel.box);
  if (box) { const b = R(box); vis(sel.cards).forEach(c => { const r = R(c); out.rows++; out.inset = Math.max(out.inset, Math.abs((r.left - b.left) - (b.right - r.right))); }); }
  return out;
}
/** Texto visible de la UI nueva: tamano minimo y contraste WCAG (fondo compuesto hacia arriba). */
function textAudit(scope) {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(",").map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const over = (a, b) => [0, 1, 2].map(i => a[i] * a[3] + b[i] * (1 - a[3])).concat([1]);
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const bgOf = el => {
    const stack = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e), b = parse(cs.backgroundColor);
      const g = /linear-gradient\([^,]*?,?\s*(rgba?\([^)]+\))/.exec(cs.backgroundImage || "");
      if (g) stack.push(parse(g[1]));
      if (b && b[3] > 0) { stack.push(b); if (b[3] >= 1) break; }
    }
    let c = [10, 12, 16, 1];
    for (let i = stack.length - 1; i >= 0; i--) c = over(stack[i], c);
    return c;
  };
  const out = { n: 0, small: [], low: [] };
  document.querySelectorAll(scope).forEach(el => {
    // el boton principal de la app (.btn: blanco sobre el degradado de marca) es el de siempre, no de esta UI
    if (!el.offsetParent || el.closest("[disabled]") || el.closest(".btn:not(.ghost)")) return;
    const txt = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join("");
    if (!txt) return;
    const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) return;
    const cs = getComputedStyle(el), fs = parseFloat(cs.fontSize);
    out.n++;
    if (fs < 11) out.small.push(`${fs}px «${txt.slice(0, 30)}»`);
    let fg = parse(cs.color); const bg = bgOf(el);
    if (fg[3] < 1) fg = over(fg, bg);
    let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity;
    if (op < 1) fg = over([fg[0], fg[1], fg[2], op], bg);
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const large = fs >= 24 || (fs >= 18.66 && +cs.fontWeight >= 700);
    if (ratio < (large ? 3 : 4.5)) out.low.push(`${ratio.toFixed(2)} «${txt.slice(0, 30)}» ${cs.color}`);
  });
  return out;
}
async function audit(tag, scope, sel) {
  const t = await ev(textAudit, scope);
  const m = await ev(measure, sel);
  log(tag, `texto ${t.n}`, JSON.stringify(m));
  assert.deepEqual(t.small, [], `${tag}: texto de menos de 11 px`);
  assert.deepEqual(t.low, [], `${tag}: contraste bajo (AA)`);
  assert.ok(t.n > 3, `${tag}: sin texto que revisar`);
  assert.ok(m.overflowX <= 0, `${tag}: scroll horizontal ${m.overflowX}px`);
  assert.ok(m.pairSpread <= 1, `${tag}: botones del mismo renglon desiguales (${m.pairSpread}px)`);
  assert.ok(m.inset <= 1, `${tag}: tarjetas no simetricas en su panel (${m.inset}px)`);
  return m;
}
const TAKES = { groups: "#asmTakes .mt-acts, #asmTabs", box: "#asmTakesWrap", cards: "#asmTakes .mt-slot, #asmTakes .mt-unc, #asmTakes .mt-extra, #asmTakes .mt-fold" };
async function pick() {
  const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.click("#drop")]);
  await fc.setFiles(FILES);
  await page.waitForTimeout(200);
}
const clips = () => ev(() => ASM.clips.map(c => ({ id: c._id, sid: c.sid, name: c.name, in: c.in, out: c.out, start: +(+c.start).toFixed(3), vol: c.vol, volAuto: !!c.volAuto,
  slot: c.montage && c.montage.slot, cand: c.montage && c.montage.cand, join: c.montage && c.montage.join, extra: !!c.extra })));
/** Simetria (px) del dialogo del armado: centrado en la ventana, titulo centrado, textarea con el mismo
    margen a cada lado y los dos botones del mismo ancho. */
const dlgSym = () => ev(() => {
  const R = e => e.getBoundingClientRect(), box = R(document.querySelector(".modal.on .mtdlg"));
  const rg = document.createRange(); rg.selectNodeContents(document.querySelector(".modal.on .mtdlg h3")); const t = rg.getBoundingClientRect();
  const ta = document.querySelector(".modal.on .mtdlg textarea"), a = ta ? R(ta) : null, b = [...document.querySelectorAll(".modal.on .rbtns .btn")].map(R);
  return { dlg: Math.abs(box.left - (innerWidth - box.right)), title: Math.abs((t.left - box.left) - (box.right - t.right)),
    ta: a ? Math.abs((a.left - box.left) - (box.right - a.right)) : 0, btns: Math.abs(b[0].width - b[1].width), overflowX: document.documentElement.scrollWidth - innerWidth };
});
const symOk = (tag, m) => { log(tag, JSON.stringify(m)); for (const [k, v] of Object.entries(m)) assert.ok(v <= 1, `${tag}: asimetrico ${k}=${v}px`); };
const contiguous = cs => { for (let i = 1; i < cs.length; i++) assert.ok(Math.abs(cs[i].start - (cs[i - 1].start + cs[i - 1].out - cs[i - 1].in)) < 0.01, `hueco/encimado en ${i}`); };

try {
  // ---------------------------------------------------------------- 1. inicio: guion opcional y borrador
  await page.goto(`${BASE}/`);
  await ev(() => { try { localStorage.clear(); } catch (e) { /* nada */ } });
  await page.reload();
  await page.waitForSelector("#drop");
  assert.equal(await ev(() => document.querySelector("#guionWrap").hidden), true, "sin archivos no hay guion");
  await pick();
  let st = await ev(() => ({ guion: !document.querySelector("#guionWrap").hidden, ai: !document.querySelector("#goAI").hidden && !document.querySelector("#goAI").disabled,
    hand: document.querySelector("#go").textContent.trim(), ghost: document.querySelector("#go").classList.contains("ghost"),
    ph: document.querySelector("#guion").placeholder, hint: document.querySelector("#setupHint").textContent }));
  log("inicio", JSON.stringify(st));
  assert.ok(st.guion && st.ai && st.ghost); assert.equal(st.hand, "Armar a mano (8)");
  assert.match(st.ph, /Pega el guion/); assert.match(st.ph, /Reelfy lo deduce/);
  await page.fill("#guion", SCRIPT);
  await page.waitForTimeout(450);
  await ev(() => document.querySelector("#guionWrap").scrollIntoView({ block: "center" }));
  await shot("01_inicio_varios_clips_1440x900");
  await audit("inicio", "#guionWrap *, #goRow *, #setupHint", { groups: "#goRow.two", box: "#s-setup .card", cards: "#guionWrap" });
  await size(1200, 760); await ev(() => document.querySelector("#guionWrap").scrollIntoView({ block: "center" }));
  await shot("02_inicio_varios_clips_1200x760"); await size(1440, 900);
  // el borrador sobrevive a recargar
  await page.reload(); await page.waitForSelector("#drop"); await pick();
  assert.equal(await ev(() => document.querySelector("#guion").value), SCRIPT, "borrador del guion restaurado");
  // sin guion: Reelfy lo deduce
  await page.fill("#guion", ""); await page.waitForTimeout(450);

  // ---------------------------------------------------------------- 2. Armar con IA: subida de a uno y etapas honestas
  ST.hold = "words";
  const nSources0 = Object.keys(sidByName).length;
  FAIL.name = CLIPS[4].name;                          // una subida falla (disco lleno): se decide antes de armar
  const nUploads0 = uploads.length;
  await page.click("#goAI");
  await page.waitForSelector("#s-montage.on");
  await page.waitForSelector(".modal.on .mtdlg", { timeout: 60000 });
  st = await ev(() => document.querySelector(".modal.on .mtdlg").innerText.replace(/\s+/g, " "));
  log("subida fallida", st);
  assert.match(st, /No se subió 1 clip/); assert.match(st, new RegExp(CLIPS[4].name.replace(".", "\\."))); assert.match(st, /Disco lleno/);
  assert.equal(bodies.montage.length, 0, "no se arma antes de decidir");
  await shot("02b_subida_fallida_1440x900");
  symOk("dialogo subida", await dlgSym());
  await audit("dialogo subida", ".modal.on .mtdlg *", { groups: ".modal.on .rbtns", box: ".modal.on .mtdlg", cards: ".modal.on .mtdlg p" });
  await page.click('.modal.on [data-a="alt"]');       // "Reintentar la subida"
  await page.waitForFunction(() => document.querySelector('.mtstep[data-k="words"]').classList.contains("cur") && MT.job, null, { timeout: 60000 });
  st = await ev(() => ({ steps: [...document.querySelectorAll(".mtstep")].map(e => e.className.replace("mtstep ", "") + ":" + e.innerText.replace(/\s+/g, " ")),
    files: document.querySelectorAll(".mtfile").length, url: location.search }));
  log("progreso", JSON.stringify(st));
  assert.equal(Object.keys(sidByName).length - nSources0 >= 0, true);
  assert.equal(bodies.montage.length, 1);
  const mb = bodies.montage[0];
  assert.equal(mb.sids.length, 8); assert.equal(mb.script, null, "sin guion -> null"); assert.equal(mb.intercut, true);
  assert.deepEqual(mb.sids, CLIPS.map(c => sidByName[c.name]), "sids en orden de grabacion");
  assert.match(st.steps[0], /^done:Subiendo 8 de 8/);
  assert.match(st.steps[1], /^cur:Escuchando cada clip \d\/8/);
  assert.match(st.steps[2], /^wait:Eligiendo las mejores tomas en espera/);
  assert.equal(st.files, 8); assert.equal(st.url, `?job=${JOB}`);
  assert.equal(FAIL.injected, 1);
  // "Armar con IA": cada clip pide su transcripcion (transcribe=1); el que fallo se subio otra vez
  const upAI = uploads.slice(nUploads0);
  assert.equal(upAI.length, 9); assert.ok(upAI.every(u => u.transcribe === "1"), JSON.stringify(upAI));
  await shot("03_armando_escuchando_1440x900");
  await audit("progreso", "#s-montage *", { groups: "#mtBtns", box: "#s-montage .card", cards: ".mtsteps" });
  await size(1200, 760); await shot("04_armando_escuchando_1200x760"); await size(1440, 900);
  ST.phase = "montage";
  await page.waitForFunction(() => document.querySelector('.mtstep[data-k="match"]').classList.contains("cur"), null, { timeout: 20000 });
  assert.match(await ev(() => document.querySelector('.mtstep[data-k="words"]').innerText), /8\/8/);
  await shot("05_armando_eligiendo_1440x900");
  ST.phase = "ready";
  await page.waitForSelector("#s-assembly.on", { timeout: 20000 });
  await page.waitForTimeout(500);

  // ---------------------------------------------------------------- 3. ensamblado desde plan.montage
  let cs = await clips();
  const S = sidMap();
  log("timeline", JSON.stringify(cs.map(c => `${c.name}@${c.start}`)));
  assert.equal(cs.length, 5);
  assert.deepEqual(cs.map(c => c.sid), ["C", "D", "E", "F", "G"].map(k => S[k]));
  assert.deepEqual(cs.map(c => c.start), [0, 2.4, 4.9, 8.4, 11.6]);
  st = await ev(() => ({ src: ASM.sources.map(s => s.name), tab: ASM.tab, tabs: !document.querySelector("#asmTabs").hidden,
    lanes: [...document.querySelectorAll("#asmLayers .clip2")].map(e => [e.className.replace("clip2 ", ""), e.querySelector(".lbl").textContent]),
    cnt: document.querySelector("#asmTakesCnt").textContent, rows: [...document.querySelectorAll("#asmTakes > *")].map(e => e.className.split(" ")[0]),
    sum: document.querySelector(".mt-sum").innerText, fold: document.querySelector('[data-fold="script"]').open,
    title: document.querySelector('[data-fold="script"] summary').innerText }));
  log("tomas", JSON.stringify(st));
  assert.deepEqual(st.src, CLIPS.map(c => c.name), "fuentes en orden de grabacion");
  assert.equal(await ev(() => document.querySelector("#asmAI").offsetParent), null, "ya armado: sin \"Armar con IA\" en la barra");
  assert.equal(st.tab, "takes"); assert.ok(st.tabs); assert.equal(st.cnt, "4");
  assert.deepEqual(st.lanes.map(l => l[0]), ["conf-alta", "conf-alta", "conf-alta", "conf-media", "conf-baja"]);
  assert.deepEqual(st.lanes.map(l => l[1].split(" · ")[0]), ["1", "1", "2", "3", "4"]);
  assert.deepEqual(st.rows, ["mt-head", "mt-sec", "mt-slot", "mt-sec", "mt-slot", "mt-extra", "mt-slot", "mt-sec", "mt-unc", "mt-slot", "mt-fold", "mt-fold"]);
  assert.match(st.sum, /4 partes · 1 sin toma/);
  assert.ok(st.fold, "sin guion: el guion detectado se muestra abierto"); assert.match(st.title, /Guion detectado/);
  const s1 = await ev(() => document.querySelector('[data-slot="s01"]').innerText);
  assert.match(s1, /PAN_0100\.MOV/); assert.match(s1, /2 tramos/); assert.match(s1, /Alta/);
  assert.match(s1, /93%/, "el puntaje de la toma en uso, la misma medida que las alternativas");
  assert.match(s1, /Última toma completa y fiel al guion/); assert.match(s1, /Intercalada con PAN_0101\.MOV/);
  assert.match(await ev(() => document.querySelector(".mt-unc").innerText), /Sin toma[\s\S]*calle Olmo/);
  await shot("06_ensamblado_tomas_1440x900");
  await audit("tomas", "#asmSide *", TAKES);
  const lay = await ev(() => { const R = s => document.querySelector(s).getBoundingClientRect(); const card = R("#s-assembly > .card"), pw = R(".asm-playerwrap"), side = R("#asmSide");
    return { l: pw.left - card.left, r: card.right - side.right }; });
  log("margenes del ensamblado", JSON.stringify(lay));
  assert.ok(Math.abs(lay.l - lay.r) <= 1, `margenes del ensamblado desiguales ${JSON.stringify(lay)}`);
  await size(1200, 760);
  await shot("07_ensamblado_tomas_1200x760");
  await audit("tomas 1200", "#asmSide *", TAKES);
  await size(1440, 900);

  // ---------------------------------------------------------------- 4. alternativas, "Usar esta toma", deshacer
  await page.click('[data-slot="s01"] [data-act="alts"]');
  st = await ev(() => [...document.querySelectorAll('[data-slot="s01"] .mt-alt')].map(e => e.innerText.replace(/\s+/g, " ")));
  log("alternativas", JSON.stringify(st));
  assert.equal(st.length, 3);
  assert.match(st[0], /PAN_0101\.MOV .*90%/); assert.match(st[1], /hace unos años/); assert.match(st[2], /sin micrófono/);
  await shot("08_otras_tomas_1440x900");
  await audit("alternativas", "#asmSide *", TAKES);
  await page.click('[data-slot="s01"] .mt-alt[data-cand="c13"] [data-act="use"]');
  await page.waitForTimeout(250);
  cs = await clips();
  assert.equal(cs.length, 4); assert.equal(cs[0].sid, S.A); assert.equal(cs[0].in, 1.2); assert.equal(cs[0].out, 7.4);
  assert.equal(cs[0].volAuto, true, "toma sin nivel medido: vol automatico");
  contiguous(cs);
  st = await ev(() => ({ toast: document.querySelector(".toast.on") && document.querySelector(".toast.on").innerText, sel: ASM.sel && ASM.sel.id,
    card: document.querySelector('[data-slot="s01"]').innerText, lbl: document.querySelector("#asmLayers .clip2 .vol").textContent }));
  assert.match(st.toast || "", /Parte 1: ahora usa PAN_0098\.MOV/); assert.match(st.toast, /Deshacer/);
  assert.equal(st.sel, cs[0].id); assert.match(st.card, /elegida por ti/); assert.match(st.card, /elegida por la IA/);
  assert.equal(st.lbl, "auto");
  await shot("09_toma_cambiada_1440x900");
  await page.keyboard.press("Meta+z"); await page.waitForTimeout(200);
  cs = await clips();
  assert.equal(cs.length, 5, "⌘Z deshace el cambio de toma"); assert.equal(cs[1].cand, "c12");
  await page.keyboard.press("Meta+Shift+z"); await page.waitForTimeout(200);
  assert.equal((await clips()).length, 4, "⇧⌘Z lo rehace");
  // volver a la elegida por la IA restaura los dos angulos
  await page.click('[data-slot="s01"] .mt-alt[data-cand="c11"] [data-act="use"]'); await page.waitForTimeout(200);
  cs = await clips();
  assert.deepEqual(cs.map(c => c.start), [0, 2.4, 4.9, 8.4, 11.6]); assert.equal(cs[1].join, "splice");
  // teclado: Espacio sobre un boton del panel lo activa (no reproduce) y el foco sigue en ese control
  const focusInfo = () => ev(() => { const a = document.activeElement, card = a && a.closest && a.closest("[data-slot]");
    return { playing: ASM.playing, act: a && a.dataset ? a.dataset.act || null : null, slot: card ? card.dataset.slot : null, tag: a ? a.tagName : null }; });
  await ev(() => document.querySelector('[data-slot="s02"] [data-act="alts"]').focus());
  await page.keyboard.press("Space"); await page.waitForTimeout(150);
  st = await focusInfo();
  assert.deepEqual(st, { playing: false, act: "alts", slot: "s02", tag: "BUTTON" }, "Espacio en «Otras tomas»");
  assert.equal(await ev(() => ASM.mtOpen.has("s02")), true);
  await ev(() => document.querySelector('[data-slot="s02"] .mt-alt[data-cand="c22"] [data-act="use"]').focus());
  await page.keyboard.press("Space"); await page.waitForTimeout(250);
  st = await focusInfo();
  assert.deepEqual(st, { playing: false, act: "sel", slot: "s02", tag: "BUTTON" }, "Espacio en «Usar esta toma»: el foco queda en la parte");
  assert.equal(await ev(() => ASM.clips.find(c => c.montage && c.montage.slot === "s02").montage.cand), "c22");
  await page.keyboard.press("Meta+z"); await page.waitForTimeout(150);
  assert.equal(await ev(() => ASM.clips.find(c => c.montage && c.montage.slot === "s02").montage.cand), "c21");
  await ev(() => { ASM.mtOpen.delete("s02"); mtRender(); document.querySelector('[data-fold="disc"] summary').focus(); });
  await page.keyboard.press("Space"); await page.waitForTimeout(150);
  assert.deepEqual(await ev(() => [document.querySelector('[data-fold="disc"]').open, ASM.playing]), [true, false], "Espacio en «Descartadas» la abre");
  await page.keyboard.press("Space"); await page.waitForTimeout(150);
  assert.equal(await ev(() => document.querySelector('[data-fold="disc"]').open), false);

  // ---------------------------------------------------------------- 5. escuchar (toma, parte, descartada)
  await page.click('[data-slot="s01"] .mt-alt[data-cand="c12"] [data-act="listen"]');
  await page.waitForTimeout(700);
  st = await ev(() => ({ pv: ASM.preview && ASM.preview.sid, badge: !document.querySelector("#asmPvb").hidden && document.querySelector("#asmPvbTxt").textContent,
    src: document.querySelector("#asmPlayer").src, paused: document.querySelector("#asmPlayer").paused,
    btn: document.querySelector('.mt-alt[data-cand="c12"] [data-act="listen"]').innerText }));
  log("escuchar toma", JSON.stringify(st));
  assert.equal(st.pv, S.D); assert.match(st.badge, /Escuchando PAN_0101\.MOV · 0:00\.9–0:06\.0/);
  assert.ok(st.src.endsWith("/tlsource/" + S.D)); assert.equal(st.btn.trim(), "Detener");
  await shot("10_escuchando_toma_1440x900");
  await page.click("#asmPvbStop"); await page.waitForTimeout(150);
  assert.equal(await ev(() => ASM.preview), null);
  assert.equal(await ev(() => document.querySelector("#asmPvb").hidden), true);
  await page.click('[data-slot="s02"] [data-act="listen"]'); await page.waitForTimeout(300);
  st = await ev(() => ({ playing: ASM.playing, stop: ASM.stopAt, t: ASM.t, key: ASM.listen }));
  assert.ok(st.playing); assert.equal(+st.stop.toFixed(2), 8.4); assert.equal(st.key, "slot:s02");
  await page.click('[data-slot="s02"] [data-act="listen"]'); await page.waitForTimeout(150);
  assert.equal(await ev(() => ASM.playing), false);
  // escuchar una parte y saltar a otra: el salto termina la escucha (antes el playhead regresaba al final
  // de la primera y se pausaba)
  await page.click('[data-slot="s01"] [data-act="listen"]'); await page.waitForTimeout(250);
  assert.ok(await ev(() => ASM.playing && ASM.stopAt != null));
  await page.click('[data-slot="s04"] .mt-top'); await page.waitForTimeout(400);
  st = await ev(() => ({ playing: ASM.playing, stop: ASM.stopAt, listen: ASM.listen, t: ASM.t }));
  log("saltar mientras se escucha", JSON.stringify(st));
  assert.equal(st.playing, false); assert.equal(st.stop, null); assert.equal(st.listen, null);
  assert.ok(Math.abs(st.t - 11.61) < 0.05, `el playhead se queda en la parte 4 (${st.t})`);
  // descartadas
  await page.click('[data-fold="disc"] summary'); await page.waitForTimeout(150);
  st = await ev(() => [...document.querySelectorAll(".mt-drow")].map(e => e.innerText.replace(/\s+/g, " ")));
  assert.equal(st.length, 5); assert.match(st[1], /Blooper/); assert.match(st[2], /Fuera del guion/); assert.match(st[4], /Sin audio/);
  await page.click('.mt-drow:nth-of-type(2) [data-act="listen"]'); await page.waitForTimeout(300);
  assert.equal(await ev(() => ASM.listen), "disc:1");
  await page.click('.mt-drow:nth-of-type(2) [data-act="listen"]'); await page.waitForTimeout(150);
  assert.equal(await ev(() => ASM.listen), null);

  // ---------------------------------------------------------------- 6. extra que nutre el guion: agregar y quitar
  await page.click('.mt-extra [data-act="extra"]'); await page.waitForTimeout(200);
  cs = await clips();
  assert.equal(cs.length, 6); assert.ok(cs[3].extra); assert.equal(cs[3].start, 8.4); assert.equal(cs[4].start, 9.4);
  assert.match(await ev(() => document.querySelector('.mt-extra [data-act="extra"]').innerText), /Quitar del video/);
  await page.click('.mt-extra [data-act="extra"]'); await page.waitForTimeout(200);
  cs = await clips();
  assert.equal(cs.length, 5); assert.deepEqual(cs.map(c => c.start), [0, 2.4, 4.9, 8.4, 11.6]);
  // una toma cambiada para comprobar que sobrevive a recargar y viaja a /compose
  await page.click('[data-slot="s04"] [data-act="alts"]');
  await page.click('[data-slot="s04"] .mt-alt[data-cand="c42"] [data-act="use"]'); await page.waitForTimeout(200);
  cs = await clips();
  assert.equal(cs[4].sid, S.H); assert.equal(cs[4].cand, "c42");
  // seleccionar una parte desde el panel la selecciona en el timeline; el inspector lleva a "Tomas"
  await page.click('[data-slot="s03"] .mt-top'); await page.waitForTimeout(200);
  assert.equal(await ev(() => ASM.sel.id), cs[3].id);
  await page.click('#asmTabs [data-t="insp"]');
  st = await ev(() => [...document.querySelectorAll("#asmTabs button")].map(b => [b.classList.contains("sel"), b.getAttribute("aria-selected")]));
  assert.deepEqual(st, [[false, "false"], [false, "false"], [true, "true"]]);
  assert.equal(await ev(() => [document.querySelector("#asmTakesWrap").hidden, document.querySelector("#asmSrcWrap").hidden, document.querySelector("#asmInspWrap").hidden].join()), "true,true,false");
  assert.match(await ev(() => document.querySelector("#asmInspector").innerText), /Parte 3 del guion · confianza media/);
  await shot("11_ajustes_de_una_parte_1440x900");
  await page.click("#ai-takes"); await page.waitForTimeout(150);
  assert.equal(await ev(() => ASM.tab), "takes");
  await page.click('[data-fold="disc"] summary');
  await page.evaluate(() => document.querySelector('[data-fold="script"]').scrollIntoView({ block: "end" }));
  await shot("12_guion_detectado_1440x900");

  // ---------------------------------------------------------------- 7. recargar (?job=): vuelve con tus cambios
  await page.waitForTimeout(700);                       // guardado local (debounce)
  await page.reload();
  await page.waitForSelector("#s-assembly.on", { timeout: 20000 });
  await page.waitForTimeout(400);
  cs = await clips();
  assert.equal(cs[4].cand, "c42", "el cambio de toma sobrevive a recargar");
  assert.match(await ev(() => document.querySelector(".toast.on") && document.querySelector(".toast.on").innerText) || "", /Recuperé tus cambios/);

  // ---------------------------------------------------------------- 8. guion detectado -> Rearmar (mismo proyecto)
  const edited = (await ev(() => document.querySelector("#asmScript").value)).replace("calle Olmo", "calle Olmo 12");
  assert.equal(await ev(() => document.querySelector("#asmRearm").disabled), true, "sin cambios no se rearma");
  const rearmLook = () => ev(() => { const b = document.querySelector("#asmRearm"), c = getComputedStyle(b);
    return { color: c.color, border: c.borderColor, hint: !document.querySelector("#asmRearmHint").hidden }; });
  st = await rearmLook();
  assert.notEqual(st.color, "rgb(255, 255, 255)", "deshabilitado no se ve como el boton principal");
  assert.notEqual(st.border, "rgb(255, 90, 60)"); assert.ok(st.hint, "dice que hay que editar el guion");
  await page.fill("#asmScript", edited);
  assert.equal(await ev(() => document.querySelector("#asmRearm").disabled), false);
  st = await rearmLook();
  assert.equal(st.color, "rgb(255, 255, 255)"); assert.equal(st.hint, false);
  ST.hold = "montage";
  await page.click("#asmRearm");
  await page.waitForSelector("#s-montage.on");
  await page.waitForFunction(() => document.querySelector('.mtstep[data-k="match"]').classList.contains("cur"), null, { timeout: 20000 });
  st = await ev(() => [...document.querySelectorAll(".mtstep")].map(e => e.className.replace("mtstep ", "") + ":" + e.innerText.replace(/\s+/g, " ")));
  assert.match(st[0], /^done:Subiendo ya subidos/); assert.match(st[1], /^done:/);
  const rb = bodies.montage[bodies.montage.length - 1];
  assert.equal(rb.job_id, JOB, "rearmar usa el mismo proyecto"); assert.equal(rb.script, edited.trim());
  assert.equal(await ev(() => document.querySelector("#mtHand").hidden), true);
  ST.hold = null; ST.phase = "ready";
  await page.waitForSelector("#s-assembly.on", { timeout: 20000 }); await page.waitForTimeout(300);
  st = await ev(() => ({ title: document.querySelector('[data-fold="script"] summary').innerText, clips: ASM.clips.length, cand5: ASM.clips[5].montage.cand, undo: ASM_UNDO.length,
    lane: [...document.querySelectorAll("#asmLayers .clip2")].map(e => [e.classList.contains("conf-extra"), e.querySelector(".lbl").textContent])[3],
    card: document.querySelector('[data-slot="sx1"]').innerText, sum: document.querySelector(".mt-sum").innerText }));
  log("rearmado", JSON.stringify(st));
  assert.match(st.title, /^Guion$/); assert.equal(st.clips, 6); assert.equal(st.cand5, "c41", "rearmado: tomas de la IA de nuevo"); assert.equal(st.undo, 0);
  assert.deepEqual(st.lane, [true, "Extra · PAN_0102.MOV"]);
  assert.match(st.card, /La receta es de mi abuela/); assert.match(st.card, /Extra/); assert.match(st.card, /Quitar del video/);
  assert.match(st.sum, /4 partes · 1 sin toma · 1 extra/);
  await ev(() => document.querySelector('[data-slot="sx1"]').scrollIntoView({ block: "center" }));
  await shot("15_extra_del_montaje_1440x900");
  await audit("extra", "#asmSide *", TAKES);
  await page.click('[data-slot="sx1"] [data-act="unslot"]'); await page.waitForTimeout(200);
  cs = await clips();
  assert.equal(cs.length, 5); assert.deepEqual(cs.map(c => c.start), [0, 2.4, 4.9, 8.4, 11.6]);
  assert.match(await ev(() => document.querySelector('[data-slot="sx1"]').innerText), /Volver a ponerlo/);
  await page.keyboard.press("Meta+z"); await page.waitForTimeout(200);
  assert.equal((await clips()).length, 6, "⌘Z devuelve el extra");

  // ---------------------------------------------------------------- 9. Aplicar IA: montage_job + guion + montage por item
  await page.click('[data-slot="s02"] [data-act="alts"]');
  await page.click('[data-slot="s02"] .mt-alt[data-cand="c22"] [data-act="use"]'); await page.waitForTimeout(150);
  await page.click("#asmApply");
  await page.waitForSelector("#s-analyzing.on", { timeout: 10000 });
  const cb = bodies.compose[0];
  log("compose", JSON.stringify({ job: cb.montage_job, script: (cb.script || "").slice(0, 40), main: cb.main.map(m => [m.sid.slice(0, 4), m.montage && m.montage.slot, m.montage && m.montage.cand, m.montage && m.montage.join, m.vol]) }));
  assert.equal(cb.montage_job, JOB); assert.equal(cb.script, edited.trim());
  assert.ok("profile" in cb && cb.profile === null, "«Sin perfil» viaja explicito (null): el servidor no usa el del armado");
  assert.ok(await ev(j => { try { return !!localStorage.getItem("reelfy.asm." + j); } catch (e) { return false; } }, JOB),
    "los cambios de tomas siguen guardados hasta que la composicion termine");
  assert.equal(cb.main.length, 6);
  assert.deepEqual(cb.main.map(m => m.montage.slot), ["s01", "s01", "s02", "sx1", "s03", "s04"]);
  assert.deepEqual(cb.main.map(m => m.montage.join), [null, "splice", "section", "line", "line", "section"]);
  assert.equal(cb.main[2].montage.cand, "c22"); assert.equal(cb.main[2].montage.conf, 0.55);
  assert.equal(cb.main[3].montage.conf, 0.5, "confianza del montaje por tramo");
  assert.deepEqual(cb.main.map(m => m.start), [0, 2.4, 4.9, 8.4, 9.4, 12.6]);
  assert.ok(cb.main.every(m => typeof m.vol === "number" || m.vol === "auto"));
  // la composicion falla: el servidor devuelve el trabajo a las tomas y la UI vuelve al ensamblado
  ST.phase = "back";
  await page.waitForSelector("#s-assembly.on", { timeout: 15000 });
  assert.match(await ev(() => document.querySelector(".toast.on").innerText), /La composición falló: prueba/);
  assert.equal(await ev(() => ASM.clips[2].montage.cand), "c22", "nada se pierde");

  // ---------------------------------------------------------------- 10. proyectos recientes con armados
  const now = Date.now() / 1000;
  ST.jobs = [
    { id: JOB, kind: "montage", state: "review", phase: "assembly", pct: 100, created: now - 60, updated: now - 30, has_plan: true, plan_rev: 1, topic: null, size_bytes: 3 << 20 },
    { id: "0f0f0f0f0f0f", kind: "montage", state: "running", phase: "assembly", pct: 40, created: now - 20, updated: now - 5, has_plan: false, plan_rev: 0, topic: null, size_bytes: 0 },
  ];
  await page.click("#asmBack");
  await page.waitForSelector("#recent .rrow", { timeout: 10000 });
  st = await ev(() => [...document.querySelectorAll("#recent .rrow")].map(r => ({ txt: r.innerText.replace(/\s+/g, " "), off: r.classList.contains("off") })));
  log("recientes", JSON.stringify(st));
  assert.equal(st.length, 2); assert.ok(st.every(r => !r.off), "los armados se pueden abrir");
  assert.ok(st.some(r => /Armado con IA a1b2c3.*tomas por revisar/.test(r.txt)));
  assert.ok(st.some(r => /armando con IA/.test(r.txt)));
  assert.equal(await ev(() => location.search), "", "al volver al inicio se suelta el ?job=");
  assert.equal(await ev(() => !!document.querySelector(".toast.on")), false, "el aviso fijo del ensamblado no sigue en el inicio");
  assert.equal(await ev(() => document.querySelector("#goAI").offsetParent), null, "sin archivos no hay \"Armar con IA\"");
  await shot("13_recientes_1440x900");
  ST.jobs = null;

  // ---------------------------------------------------------------- 11. Armar a mano: orden de grabacion
  await page.reload(); await page.waitForSelector("#drop");
  await pick();
  const nUp11 = uploads.length;
  await page.click("#go");
  await page.waitForSelector("#s-assembly.on");
  await page.waitForFunction(() => ASM.clips.length === 8, null, { timeout: 60000 });
  assert.ok(uploads.slice(nUp11).length === 8 && uploads.slice(nUp11).every(u => u.transcribe === "1"), "varios videos: se transcriben (se puede armar con IA)");
  st = await ev(() => ({ clips: ASM.clips.map(c => c.name), tabs: document.querySelector("#asmTabs").hidden, src: !document.querySelector("#asmSrcWrap").hidden,
    created: ASM.sources.map(s => s.created_src || null) }));
  log("a mano", JSON.stringify(st));
  assert.deepEqual(st.clips, CLIPS.map(c => c.name), "a mano: en orden de grabacion");
  assert.equal(st.tabs, true, "sin montaje no hay pestañas"); assert.ok(st.src);
  assert.ok(await ev(() => !!document.querySelector("#asmAI").offsetParent), "a mano con varios clips: \"Armar con IA\" en la barra");
  await shot("14_a_mano_orden_de_grabacion_1440x900");
  // desde el ensamblado a mano: Armar con IA con las fuentes ya subidas (sin volver a subir)
  const nUp = bodies.montage.length;
  ST.hold = "ready";
  await page.click("#asmAI");
  // el guion se ve y se confirma antes de armar (nunca uno escondido de la pantalla de inicio)
  await page.waitForSelector(".modal.on .mtdlg textarea", { timeout: 10000 });
  st = await ev(() => ({ txt: document.querySelector(".modal.on textarea").value, guion: document.querySelector("#guion").value.trim(),
    focus: document.activeElement && document.activeElement.tagName, title: document.querySelector(".modal.on h3").textContent }));
  log("dialogo armar", JSON.stringify(st));
  assert.equal(st.txt, st.guion); assert.equal(st.focus, "TEXTAREA"); assert.equal(st.title, "Armar con IA");
  assert.equal(bodies.montage.length, nUp, "no arma antes de confirmar");
  await shot("14b_armar_desde_el_ensamble_1440x900");
  symOk("dialogo armar", await dlgSym());
  await size(390, 800); symOk("dialogo armar 390", await dlgSym()); await size(1440, 900);
  await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  assert.equal(await ev(() => !!document.querySelector(".modal.on")), false); assert.equal(bodies.montage.length, nUp, "Escape cancela");
  await page.click("#asmAI");
  await page.waitForSelector(".modal.on .mtdlg textarea", { timeout: 10000 });
  await page.fill(".modal.on textarea", "Hace un año abrimos una panadería pequeña en el centro.");
  await page.click('.modal.on [data-a="ok"]');
  await page.waitForFunction(() => ASM.montage && document.querySelector("#s-assembly.on"), null, { timeout: 20000 });
  const b2 = bodies.montage[nUp];
  assert.equal(b2.sids.length, 8); assert.equal(b2.job_id, undefined);
  assert.equal(b2.script, "Hace un año abrimos una panadería pequeña en el centro.", "el guion confirmado en el dialogo");
  assert.equal(await ev(() => [!document.querySelector("#asmAI").offsetParent, !document.querySelector("#asmTabs").offsetParent].join()), "true,false");
  ST.hold = null;

  // ---------------------------------------------------------------- 12. Armar con IA con guion y cancelar
  await page.click("#asmBack");
  await page.fill("#guion", SCRIPT);
  ST.hold = "words";
  FAIL.name = CLIPS[7].name;
  await page.click("#goAI");
  await page.waitForSelector(".modal.on .mtdlg", { timeout: 60000 });
  await page.click('.modal.on [data-a="ok"]');         // "Armar sin ellos"
  await page.waitForFunction(() => MT.job, null, { timeout: 60000 });
  const b3 = bodies.montage[bodies.montage.length - 1];
  assert.equal(b3.script, SCRIPT.trim()); assert.equal(b3.job_id, undefined);
  assert.equal(b3.sids.length, 7, "sin el clip que no se subio"); assert.deepEqual(await ev(() => MT.failed), [CLIPS[7].name]);
  assert.equal(await ev(() => { try { return localStorage.getItem("reelfy.guion.draft"); } catch (e) { return "x"; } }), null, "el borrador se limpia al armar");
  await page.click("#mtCancel");
  await page.waitForSelector("#s-setup.on");
  assert.equal(bodies.cancel, 1); assert.equal(await ev(() => location.search), "");

  let injHttp = FAIL.injected, injCon = FAIL.injected;
  const bad = errs.filter(e => !/favicon/.test(e))
    .filter(e => !(e === "HTTP 500 POST /sources" && injHttp-- > 0))
    .filter(e => !(/^console .*status of 500/.test(e) && injCon-- > 0));
  assert.deepEqual(bad, [], "errores en la consola o en la red");
  console.log("OK montaje e2e");
} catch (e) {
  console.error("FALLO:", e && e.message ? e.message : e);
  console.error(errs.join("\n"));
  await shot("zz_fallo").catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
