// E2E de la "Limpieza de tomas" del Studio en un navegador real (Playwright + Chrome).
// uso: REELFY_PLAYWRIGHT=<ruta a playwright-core> node tests/js/limpieza_e2e.mjs <url base> [carpeta de capturas]
// El servidor debe tener sembrados (datos SINTETICOS, ver tests/test_studio_ui.py):
//   takesui_long: video horizontal largo con plan.takes (tomas + silencios)
//   takesui_old : el mismo video SIN plan.takes (estado vacio "Buscar tomas repetidas")
// Revisa: seleccionar sin cambiar, popover, R / deshacer, flechas, escuchar, filtros, silencios,
// "Restaurar todo…", nivel de limpieza (POST /takes), categorias, guardado (PUT /plan con
// base_rev), timeline (clic = seleccionar), formato 16:9 por defecto, simetria, texto >= 11 px,
// contraste AA y consola sin errores. Sale con codigo 1 al primer fallo.
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("define REELFY_PLAYWRIGHT (ruta a playwright-core)"); process.exit(2); }
const { chromium } = require(PW);
const BASE = (process.argv[2] || "http://127.0.0.1:8547").replace(/\/$/, "");
const OUT = process.argv[3] || null;
if (OUT) fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true, channel: process.env.REELFY_PW_CHANNEL || "chrome" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", e => errs.push("pageerror " + e.message));
page.on("console", m => { if (m.type() === "error") errs.push("console " + m.text()); });
page.on("response", r => { if (r.status() >= 400 && !/\/frame\//.test(r.url())) errs.push(`HTTP ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
page.on("dialog", d => d.dismiss());
const anaPosts = [];                    // POST /analysis (re-analisis de fondo del video base)
page.on("request", r => { if (r.method() === "POST" && /\/analysis\//.test(r.url())) anaPosts.push(Date.now()); });
const log = (...a) => console.log("·", ...a);
const shot = async n => { if (OUT) await page.screenshot({ path: path.join(OUT, n + ".png") }); };
const ev = (fn, arg) => page.evaluate(fn, arg);
const saved = () => page.waitForFunction(() => !FX.isDirty(), null, { timeout: 20000 });
const serverPlan = job => ev(async j => (await (await fetch(`/plan/${j}`)).json()).plan, job);
const key = async k => { await page.keyboard.press(k); await page.waitForTimeout(120); };

async function open(job, w, h) {
  await page.setViewportSize({ width: w || 1440, height: h || 900 });
  await ev(() => { try { localStorage.clear(); } catch (e) { /* nada */ } });
  await page.goto(`${BASE}/?job=${job}`);
  await page.waitForSelector("#s-editor.on", { timeout: 30000 });
  await page.waitForFunction(() => typeof PLAN !== "undefined" && PLAN && FX.clean && document.querySelector("#finLbl").textContent.length > 0);
  await page.waitForTimeout(600);
}

/** Simetria (px): margenes externos de las dos columnas, anchos y centros de las barras de 3 columnas. */
function measure() {
  const R = s => document.querySelector(s).getBoundingClientRect();
  const card = R("#s-editor > .card"), pane = R("#cleanPane"), side = R("#cleanSide"), stage = R(".studio > .stage");
  const mid = r => r.left + r.width / 2;
  const widths = s => [...document.querySelectorAll(s)].filter(e => e.offsetParent).map(e => e.getBoundingClientRect().width);
  const spread = a => (a.length ? Math.max(...a) - Math.min(...a) : 0);
  return {
    marginL: pane.left - card.left, marginR: card.right - side.right, stageR: card.right - stage.right,
    colL: pane.width, colR: side.width, stageW: stage.width,
    modeCenter: mid(R("#segMode")) - mid(R("#modeBar")),
    footCenter: mid(R("#cleanPane .gf-c")) - mid(R("#cleanPane .gfoot")),
    levelSpread: spread(widths("#cSum .cs-lv button")), catSpread: spread(widths("#cSum .cs-cat:not(.full)")),
    popPairSpread: spread(widths("#gPop .pair .btn")), overflowX: document.documentElement.scrollWidth - innerWidth,
  };
}
/** Texto visible de la UI nueva: tamano minimo y contraste WCAG (fondo compuesto hacia arriba). */
function textAudit() {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(",").map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const over = (a, b) => [0, 1, 2].map(i => a[i] * a[3] + b[i] * (1 - a[3])).concat([1]);
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const bgOf = el => {
    const stack = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e), b = parse(cs.backgroundColor);
      // capa de color en un degradado (p. ej. el rotulo de un corte) sobre su fondo solido
      const g = /linear-gradient\((rgba?\([^)]+\))/.exec(cs.backgroundImage || "");
      if (g) stack.push(parse(g[1]));
      if (b && b[3] > 0) { stack.push(b); if (b[3] >= 1) break; }
    }
    let c = [10, 12, 16, 1];
    for (let i = stack.length - 1; i >= 0; i--) c = over(stack[i], c);
    return c;
  };
  const out = { n: 0, small: [], low: [] };
  document.querySelectorAll("#modeBar *, #cleanPane *, #cleanSide *").forEach(el => {
    if (!el.offsetParent || el.closest("[disabled]") || el.closest(".gchip:disabled")) return;
    const txt = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join("");
    if (!txt) return;
    const r = el.getBoundingClientRect(); if (r.bottom < 0 || r.top > innerHeight || r.width === 0) return;
    const cs = getComputedStyle(el), fs = parseFloat(cs.fontSize);
    out.n++;
    if (fs < 11) out.small.push(`${fs}px «${txt.slice(0, 30)}»`);
    let fg = parse(cs.color); const bg = bgOf(el);
    if (fg[3] < 1) fg = over(fg, bg);
    const op = +cs.opacity < 1 ? +cs.opacity : 1;
    if (op < 1) fg = over([fg[0], fg[1], fg[2], op], bg);
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const large = fs >= 24 || (fs >= 18.66 && +cs.fontWeight >= 700);
    if (ratio < (large ? 3 : 4.5)) out.low.push(`${ratio.toFixed(2)} «${txt.slice(0, 30)}» ${cs.color}`);
  });
  return out;
}

try {
  // ---------------------------------------------------------------- 1. abre en Limpieza, 16:9
  await open("takesui_long");
  let st = await ev(() => ({ mode: FX.clean.mode, fmt: FMT, words: document.querySelectorAll("#gTr .gw").length,
    takes: PLAN.cuts.filter(c => c.src === "takes").length, badge: document.querySelector("#cleanCnt").textContent,
    tl: getComputedStyle(document.querySelector("#tlwrap")).display }));
  log("abre", JSON.stringify(st));
  assert.equal(st.mode, "clean", "con tomas abre en Limpieza de tomas");
  assert.equal(st.fmt, "16:9", "un solo video horizontal largo -> 16:9 por defecto");
  assert.ok(st.words > 500, "el guion completo esta en pantalla");
  assert.equal(st.badge, String(st.takes));
  assert.equal(st.tl, "none", "el timeline no se muestra en Limpieza");
  // rendimiento: guion completo y cambio suelto
  const perf = await ev(() => { let t = performance.now(); FX.clean.render(); const full = performance.now() - t; return { full }; });
  log("render completo", perf.full.toFixed(1) + " ms");
  assert.ok(perf.full < 400, `render del guion lento: ${perf.full} ms`);
  await shot("01_limpieza");

  // ---------------------------------------------------------------- 2. clic = seleccionar + popover (no cambia)
  const first = await ev(() => { const el = document.querySelector("#gTr .gcut.on"); el.scrollIntoView({ block: "center" }); return +el.dataset.c; });
  const before = await ev(ci => JSON.stringify(PLAN.cuts[ci]), first);
  await page.click(`#gTr .gcut[data-c="${first}"] .gw`);
  await page.waitForSelector("#gPop");
  st = await ev(ci => ({ sel: FX.clean.sel, same: JSON.stringify(PLAN.cuts[ci]), pop: document.querySelector("#gPop").innerText, pos: document.querySelector("#gPos").textContent }), first);
  assert.equal(st.sel, first); assert.equal(st.same, before, "un clic no cambia el corte");
  assert.match(st.pop, /Seguridad (alta|media|baja)/); assert.match(st.pop, /Se quitan/); assert.match(st.pop, /Escuchar cómo queda/); assert.match(st.pop, /Restaurar/);
  assert.match(st.pos, /^Corte \d+ de \d+$/);
  const symPop = await ev(measure);
  assert.ok(symPop.popPairSpread <= 1, `botones del popover desiguales: ${symPop.popPairSpread}`);
  await shot("02_popover");

  // ---------------------------------------------------------------- 3. R restaura (user:true), se guarda, ⌘Z deshace
  const rev0 = await ev(() => FX.serverRev);
  await key("r");
  st = await ev(ci => ({ en: PLAN.cuts[ci].enabled, user: PLAN.cuts[ci].user, cls: document.querySelector(`#gTr [data-c="${ci}"]`).className,
    toast: document.querySelector(".toast.on") && document.querySelector(".toast.on").innerText }), first);
  assert.equal(st.en, false); assert.equal(st.user, true); assert.match(st.cls, /\bkept\b/);
  assert.match(st.toast || "", /Restauraste/); assert.match(st.toast || "", /Deshacer/);
  await saved();
  let sp = await serverPlan("takesui_long");
  const firstId = await ev(ci => PLAN.cuts[ci].id, first);
  const srv = sp.cuts.find(c => c.id === firstId);
  assert.equal(srv.enabled, false, "PUT /plan guardo la toma restaurada"); assert.equal(srv.user, true);
  assert.ok((await ev(() => FX.serverRev)) > rev0, "la revision del plan subio");
  await shot("03_restaurado");
  await page.keyboard.press("Meta+z"); await page.waitForTimeout(150);
  st = await ev(ci => ({ en: PLAN.cuts[ci].enabled, user: !!PLAN.cuts[ci].user }), first);
  assert.equal(st.en, true, "⌘Z vuelve a cortar la toma"); assert.equal(st.user, false);
  await saved();

  // ---------------------------------------------------------------- 4. flechas, escuchar
  await ev(ci => FX.clean.select(ci), first);
  await key("ArrowDown");
  st = await ev(() => ({ sel: FX.clean.sel, pos: document.querySelector("#gPos").textContent, pop: !!document.querySelector("#gPop") }));
  assert.notEqual(st.sel, first, "flecha abajo = corte siguiente"); assert.ok(st.pop);
  const n1 = +st.pos.match(/Corte (\d+)/)[1];
  await key("ArrowUp");
  assert.equal(await ev(() => FX.clean.sel), first);
  assert.equal(+(await ev(() => document.querySelector("#gPos").textContent)).match(/Corte (\d+)/)[1], n1 - 1);
  const lr = await ev(ci => { const c = PLAN.cuts[ci]; return FXCore.listenRange(FX.timeline(), c, 2); }, first);
  await key("Space");
  st = await ev(() => ({ playing: !srcVid.paused, lr: FX.clean.listenRange }));
  assert.ok(st.lr, "Espacio con un corte = escuchar cómo queda");
  await page.waitForFunction(() => srcVid.paused, null, { timeout: 12000 });
  st = await ev(() => srcVid.currentTime);
  assert.ok(st >= lr[1] - 0.25 && st <= lr[1] + 0.6, `escuchar termina 2 s despues del corte (${st} vs ${lr[1]})`);

  // ---------------------------------------------------------------- 5. filtros y silencios
  const chips = await ev(() => [...document.querySelectorAll("#gChips .gchip")].map(b => b.innerText.replace(/\s+/g, " ").trim()));
  log("chips", chips.join(" | "));
  for (const lbl of ["Todos", "Tomas repetidas", "Arranques", "Correcciones", "Tartamudeos", "Errores"]) assert.ok(chips.some(c => c.startsWith(lbl)), `chip ${lbl}`);
  await page.click('#gChips [data-f="tartamudeo"]');
  st = await ev(() => ({ n: FXCore.reviewItems(PLAN.cuts, FX.clean.filter, FX.clean.sil).map(i => FXCore.cutKind(PLAN.cuts[i])), dim: document.querySelectorAll("#gTr .gcut.dim").length }));
  assert.ok(st.n.length > 0 && st.n.every(k => k === "tartamudeo")); assert.ok(st.dim > 0, "lo demas queda atenuado");
  await key("ArrowDown");
  assert.equal(await ev(() => FXCore.cutKind(PLAN.cuts[FX.clean.sel])), "tartamudeo");
  await page.click('#gChips [data-f="todos"]');
  const silBefore = await ev(() => document.querySelectorAll("#gTr .gsil.k-silencio").length);
  await page.click("#gSil");
  const silAfter = await ev(() => ({ n: document.querySelectorAll("#gTr .gsil.k-silencio").length, aria: document.querySelector("#gSil").getAttribute("aria-checked") }));
  assert.equal(silBefore, 0); assert.ok(silAfter.n > 50, "Mostrar silencios muestra las pausas"); assert.equal(silAfter.aria, "true");
  await shot("04_silencios");
  await page.click("#gSil");

  // ---------------------------------------------------------------- 6. Restaurar todo… (confirmar + deshacer)
  await page.click('#gChips [data-f="retoma"]');
  await page.click("#gRestAll");
  await page.waitForSelector("#gConfirm.on");
  assert.match(await ev(() => document.querySelector("#gConfirm").innerText), /Restaurar \d+/);
  await shot("05_confirmar");
  await page.click('#gConfirm [data-a="ok"]');
  st = await ev(() => { const r = PLAN.cuts.filter(c => FXCore.cutKind(c) === "retoma"); return { off: r.every(c => c.enabled === false), user: r.filter(c => c.user).length }; });
  assert.ok(st.off && st.user > 0, "todas las tomas repetidas restauradas (las que cambiaron quedan user:true)");
  await page.keyboard.press("Meta+z"); await page.waitForTimeout(150);
  assert.ok(await ev(() => PLAN.cuts.filter(c => FXCore.cutKind(c) === "retoma").some(c => c.enabled !== false)), "⌘Z deshace Restaurar todo");
  await page.click('#gChips [data-f="todos"]');
  await saved();

  // ---------------------------------------------------------------- 7. nivel de limpieza (POST /takes) y categorias
  const fin0 = await ev(() => FX.timeline().finalDuration);
  const quedaSuave = await ev(() => FXCore.levelFinal(PLAN, "suave", 30));
  const req = page.waitForResponse(r => /\/takes\//.test(r.url()) && r.request().method() === "POST", { timeout: 120000 });
  await page.click('#cSum [data-lv="suave"]');
  st = await ev(() => ({ lv: PLAN.takes.sensitivity, fin: FX.timeline().finalDuration }));
  assert.equal(st.lv, "suave"); assert.ok(Math.abs(st.fin - quedaSuave) < 0.05, "Suave se aplica al instante");
  assert.ok(st.fin >= fin0);
  const resp = await req;
  const body = JSON.parse(resp.request().postData());
  assert.equal(body.sensitivity, "suave"); assert.ok(Number.isInteger(body.base_rev), "POST /takes lleva base_rev");
  assert.equal(resp.status(), 200, "POST /takes");
  await page.waitForFunction(() => !FX.clean.busy, null, { timeout: 120000 });
  await saved();
  sp = await serverPlan("takesui_long");
  assert.equal(sp.takes.sensitivity, "suave", "el servidor guardo el nivel");
  st = await ev(() => ({ on: document.querySelector('#cSum [data-lv="suave"]').getAttribute("aria-checked"), txt: document.querySelector("#cSum .cs-lv").innerText }));
  assert.equal(st.on, "true"); assert.match(st.txt, /queda \d+:\d\d/);
  await shot("06_nivel_suave");
  await page.click('#cSum [data-kind="retoma"]');
  assert.ok(await ev(() => PLAN.cuts.filter(c => FXCore.cutKind(c) === "retoma").every(c => c.enabled === false)), "apagar la categoria restaura sus tomas");
  await page.click('#cSum [data-kind="retoma"]');
  assert.ok(await ev(() => FXCore.kindOn(PLAN.cuts, "retoma")));
  await page.click('#cSum [data-lv="normal"]');
  await page.waitForFunction(() => !FX.clean.busy, null, { timeout: 120000 });
  await saved();

  // ---------------------------------------------------------------- 8. simetria, tamanos y contraste (1440x900 y 1200x760)
  for (const [w, h] of [[1440, 900], [1200, 760]]) {
    await page.setViewportSize({ width: w, height: h }); await page.waitForTimeout(400);
    const ci = await ev(() => { const el = document.querySelector("#gTr .gcut.on"); return +el.dataset.c; });
    await ev(c => FX.clean.select(c, { scroll: true }), ci); await page.waitForTimeout(150);
    const m = await ev(measure), ta = await ev(textAudit);
    log(`${w}x${h}`, JSON.stringify(m), `texto: ${ta.n} (chicos ${ta.small.length}, bajo contraste ${ta.low.length})`);
    assert.ok(Math.abs(m.marginL - m.marginR) <= 1, `margenes izq/der distintos: ${m.marginL} vs ${m.marginR}`);
    assert.ok(Math.abs(m.colL - m.colR) <= 1, `columnas distintas: ${m.colL} vs ${m.colR}`);
    assert.ok(Math.abs(m.stageR - m.marginR) <= 1 && Math.abs(m.stageW - m.colR) <= 1, "la vista previa alinea con su columna");
    assert.ok(Math.abs(m.modeCenter) <= 1, `selector de modo descentrado: ${m.modeCenter}`);
    assert.ok(Math.abs(m.footCenter) <= 1, `navegacion del pie descentrada: ${m.footCenter}`);
    assert.ok(m.levelSpread <= 1 && m.catSpread <= 1, "segmentos y tarjetas del mismo ancho");
    assert.ok(m.overflowX <= 0, "sin scroll horizontal");
    assert.deepEqual(ta.small, [], "texto de menos de 11 px");
    assert.deepEqual(ta.low, [], "texto sin contraste AA");
    await shot(`07_limpieza_${w}x${h}`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await ev(() => FX.clean.select(-1));

  // ---------------------------------------------------------------- 9. Edicion: un clic en el timeline solo selecciona
  await page.click('#segMode [data-v="edit"]');
  await page.waitForTimeout(300);
  assert.notEqual(await ev(() => getComputedStyle(document.querySelector("#tlwrap")).display), "none", "Edición muestra el timeline");
  const lane = await ev(() => { const i = PLAN.cuts.findIndex(c => c.src === "takes" && c.enabled !== false && c.end - c.start > 0.8);
    tlSetZoom(40); const sc = document.querySelector("#tlscroll"); sc.scrollLeft = Math.max(0, LABW + PLAN.cuts[i].start * TL.px - 300); return i; });
  await page.waitForTimeout(200);
  const box = await page.locator(`#trkCuts .cutb.tk`).evaluateAll((els, i) => { const c = PLAN.cuts[i]; const el = els.find(e => Math.abs(parseFloat(e.style.left) - c.start * TL.px) < 1); const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }, lane);
  await page.mouse.click(box.x, box.y);
  st = await ev(i => ({ en: PLAN.cuts[i].enabled, sel: SEL && SEL.type === "cut" && SEL.i === i }), lane);
  assert.equal(st.en, true, "clic en un corte del timeline NO lo restaura"); assert.ok(st.sel, "solo lo selecciona");
  await page.mouse.click(box.x, box.y);
  assert.equal(await ev(i => PLAN.cuts[i].enabled, lane), true, "ni con un segundo clic");
  await key("r");
  st = await ev(i => ({ en: PLAN.cuts[i].enabled, user: PLAN.cuts[i].user }), lane);
  assert.equal(st.en, false, "R restaura el corte seleccionado"); assert.equal(st.user, true);
  await page.keyboard.press("Meta+z"); await page.waitForTimeout(150);
  const hb = await ev(() => { const el = document.querySelector("#trkShorts .hlb"); el.scrollIntoView({ block: "center", inline: "center" }); const r = el.getBoundingClientRect(); return { x: r.left + Math.min(40, r.width / 2), y: r.top + r.height / 2, en: PLAN.highlights[0].enabled }; });
  await page.mouse.click(hb.x, hb.y); await page.mouse.click(hb.x, hb.y);
  assert.equal(await ev(() => PLAN.highlights[0].enabled), hb.en, "clic en un short seleccionado no lo apaga");
  await shot("08_edicion");
  await page.click('#segMode [data-v="clean"]');
  await saved();

  // ---------------------------------------------------------------- 10. proyecto sin tomas: "Buscar tomas repetidas"
  const ana0 = anaPosts.length;
  await open("takesui_old");
  st = await ev(() => ({ mode: FX.clean.mode, takes: !!PLAN.takes, fmt: FMT, studio: PLAN.studio && PLAN.studio.fmt }));
  assert.equal(st.mode, "edit", "sin tomas abre en Edición"); assert.equal(st.takes, false);
  // proyecto de antes (sin plan.studio.fmt ni plan.takes): se edito en 9:16 y se queda asi, anotado
  assert.equal(st.fmt, "9:16", "un proyecto viejo no cambia a 16:9 al abrirlo"); assert.equal(st.studio, "9:16");
  await saved();
  assert.equal((await serverPlan("takesui_old")).studio.fmt, "9:16", "el formato quedo anotado en el plan");
  await page.waitForTimeout(5600);
  assert.equal(anaPosts.length, ana0, "abrir un proyecto viejo no relanza el analisis de fondo");
  await page.click('#segMode [data-v="clean"]');
  await page.waitForSelector("#gTr .gempty [data-a=find]");
  await shot("09_sin_tomas");
  const req2 = page.waitForResponse(r => /\/takes\//.test(r.url()) && r.request().method() === "POST", { timeout: 180000 });
  await page.click("#gTr .gempty [data-a=find]");
  assert.ok(await ev(() => !!FX.clean.busy));
  const r2 = await req2;
  assert.equal(r2.status(), 200);
  await page.waitForFunction(() => !FX.clean.busy, null, { timeout: 180000 });
  st = await ev(() => ({ takes: !!PLAN.takes, n: PLAN.cuts.filter(c => c.src === "takes").length, empty: !!document.querySelector("#gTr .gempty") }));
  log("buscar tomas", JSON.stringify(st));
  assert.ok(st.takes, "plan.takes llego del motor"); assert.equal(st.empty, false);
  await saved();
  await shot("10_tomas_encontradas");

  // ---------------------------------------------------------------- 11. arreglos de la revision
  await open("takesui_long");
  // el motivo cita la toma que se queda en tiempo FINAL (el motor la escribe en tiempo de la fuente)
  st = await ev(() => {
    const tl = FX.timeline();
    const i = PLAN.cuts.map((c, k) => k).reverse().find(k => { const c = PLAN.cuts[k]; return c.src === "takes" && c.kind === "retoma" && c.keep_ref && tl.remapClamped(c.keep_ref.start) < c.keep_ref.start - 60; });
    const c = PLAN.cuts[i];
    c.reason = `Lo repites mejor en ${fmtT(c.keep_ref.start)}`;
    FX.clean.select(i, { scroll: true });
    return { src: fmtT(c.keep_ref.start), fin: fmtT(tl.remapClamped(c.keep_ref.start)), pop: document.querySelector("#gPop").innerText };
  });
  log("motivo", JSON.stringify(st));
  assert.ok(st.pop.includes(`Lo repites mejor en ${st.fin}`), "el popover da la hora FINAL de la toma que se queda");
  assert.ok(!st.pop.includes(`en ${st.src}`), "sin la hora de la fuente");
  // un parrafo cortado entero no repite la hora del siguiente: lleva una marca
  st = await ev(() => [...document.querySelectorAll("#gTr .gpara")].map(p => ({ ts: p.querySelector(".gts").textContent,
    cut: [...p.querySelectorAll(".gw")].every(w => w.closest(".gcut.on") || w.classList.contains("rm")) })));
  assert.ok(st.every(p => (p.ts === "—") === p.cut), "marca solo en los parrafos cortados enteros");
  const stamps = st.filter(p => p.ts !== "—").map(p => p.ts);
  assert.ok(stamps.every((t, k) => !k || t >= stamps[k - 1] || t.length > stamps[k - 1].length), "horas en orden");
  // aviso de un corte sin palabras: sustantivo con su articulo
  const man = await ev(() => {
    const w = FXCore.planWords(PLAN.phrases); let k = 1; while (w[k].start - w[k - 1].end < 0.6) k++;
    PLAN.cuts.push({ start: +(w[k - 1].end + 0.15).toFixed(3), end: +(w[k].start - 0.15).toFixed(3), enabled: true, manual: true });
    afterCutsChange(); return PLAN.cuts.length - 1;
  });
  await ev(i => FX.clean.toggle(i), man);
  assert.match(await ev(() => document.querySelector(".toast.on").innerText), /Restauraste el corte manual de /);
  await ev(i => FX.clean.toggle(i), man);
  // "Todos" = cortes de limpieza: cuenta lo mismo que los chips y "Restaurar todo" no toca el manual
  st = await ev(() => ({ todos: +document.querySelector('#gChips [data-f="todos"] b').textContent,
    chips: [...document.querySelectorAll('#gChips .gchip:not([data-f="todos"]) b')].reduce((n, b) => n + +b.textContent, 0),
    on: PLAN.cuts.filter(c => c.src === "takes" && c.enabled !== false && c.end > c.start).length }));
  assert.equal(st.todos, st.chips, "Todos suma lo mismo que los chips");
  await page.click("#gRestAll"); await page.waitForSelector("#gConfirm.on");
  assert.match(await ev(() => document.querySelector("#gConfirm").innerText), new RegExp(`Restaurar ${st.on} cortes de limpieza`));
  await page.click('#gConfirm [data-a="ok"]');
  assert.equal(await ev(i => PLAN.cuts[i].enabled, man), true, "Restaurar todo no restaura un corte manual");
  await page.keyboard.press("Meta+z"); await page.waitForTimeout(150);
  // Edicion -> Limpieza con un silencio sin palabras seleccionado y los silencios ocultos: no se lleva
  await page.click('#segMode [data-v="edit"]'); await page.waitForTimeout(250);
  const sil = await ev(() => { const i = PLAN.cuts.findIndex(c => !c.src && !c.manual && c.end - c.start > 0.3); inspect({ type: "cut", i }); return i; });
  await page.click('#segMode [data-v="clean"]'); await page.waitForTimeout(250);
  assert.equal(await ev(() => FX.clean.sel), -1, "un silencio oculto no queda seleccionado en Limpieza");
  await key("r");
  assert.equal(await ev(i => PLAN.cuts[i].enabled, sil), true, "R no toca un silencio que no se ve");
  // carril de cortes: una toma angosta lleva tijeras, no "IA" (esa etiqueta es del recorte del director)
  await page.click('#segMode [data-v="edit"]'); await page.waitForTimeout(250);
  st = await ev(() => { tlSetZoom(8); return [...document.querySelectorAll("#trkCuts .cutb.tk .clb")].map(e => e.textContent.trim() || (e.querySelector("svg") ? "svg" : "")); });
  assert.ok(st.length && !st.includes("IA") && st.includes("svg"), `etiquetas de tomas en el carril: ${[...new Set(st)].join(",")}`);
  // arrastrar el borde de una toma: sus bordes sobreviven a cambiar el nivel (POST /takes)
  // una toma cuya asa derecha se puede tomar (antes la tapaba la del silencio que termina en el mismo punto)
  const drag = await ev(() => {
    tlSetZoom(40);
    const sc = document.querySelector("#tlscroll");
    for (let i = 0; i < PLAN.cuts.length; i++) {
      const c = PLAN.cuts[i];
      if (c.src !== "takes" || c.enabled === false || c.end - c.start < 1.2) continue;
      sc.scrollLeft = Math.max(0, LABW + c.end * TL.px - 400);
      const el = [...document.querySelectorAll("#trkCuts .cutb.tk")].find(e => Math.abs(parseFloat(e.style.left) - c.start * TL.px) < 1);
      if (!el) continue;
      el.scrollIntoView({ block: "center", inline: "nearest" });
      const r = el.querySelector(".hnd.r").getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
      const hit = document.elementFromPoint(x, y);
      if (hit && hit.parentNode === el) return { i, id: c.id, end: c.end, x, y };
    }
    return null;
  });
  assert.ok(drag, "el asa de una toma no la tapa el silencio que termina con ella");
  await page.keyboard.down("Alt");                      // sin iman
  await page.mouse.move(drag.x, drag.y); await page.mouse.down(); await page.mouse.move(drag.x - 12, drag.y, { steps: 3 });
  await page.mouse.move(drag.x - 24, drag.y, { steps: 3 }); await page.mouse.up();
  await page.keyboard.up("Alt");
  st = await ev(i => ({ end: PLAN.cuts[i].end, user: PLAN.cuts[i].user, edges: PLAN.cuts[i].edges }), drag.i);
  log("borde", JSON.stringify(drag), JSON.stringify(st));
  assert.ok(st.end < drag.end - 0.1 && st.user === true && st.edges === true, "el borde movido queda del usuario");
  await page.click('#segMode [data-v="clean"]'); await page.waitForTimeout(250);
  const req3 = page.waitForResponse(r => /\/takes\//.test(r.url()) && r.request().method() === "POST", { timeout: 120000 });
  await page.click('#cSum [data-lv="a_fondo"]');
  await req3; await page.waitForFunction(() => !FX.clean.busy, null, { timeout: 120000 });
  assert.equal(await ev(id => PLAN.cuts.find(c => c.id === id).end, drag.id), st.end, "re-detectar no devuelve el borde detectado");
  await saved();
  assert.equal((await serverPlan("takesui_long")).cuts.find(c => c.id === drag.id).end, st.end);
  // 16:9: los shorts (verticales) no se piden y se avisa
  st = await ev(() => ({ shorts: FX.renderBody().shorts, note: FX.shortsNote() }));
  assert.equal(st.shorts, false, "con 16:9 no se piden shorts horizontales"); assert.match(st.note, /Exporta en 9:16/);
  // "Liberar espacio" dejo el video base de este proyecto: se vuelve a preparar al abrirlo
  const ana1 = anaPosts.length;
  await ev(() => FX.watchStatus({ analysis_state: "freed" }));
  await page.waitForTimeout(5600);
  assert.equal(anaPosts.length, ana1 + 1, "tras liberar espacio se relanza el analisis de fondo");
  // poco disco: aviso en el Studio
  await ev(() => FX.watchStatus({ low_space: true, low_space_message: "Poco espacio: libera espacio o elimina proyectos", disk_free_gb: 2.5 }));
  assert.match(await ev(() => document.querySelector(".toast.on").innerText), /quedan 2\.5 GB/);
  await shot("11_arreglos");
  // inicio: el pie con el uso del disco se lee (contraste AA)
  await page.goto(`${BASE}/`); await page.waitForSelector("#recent .rfoot", { timeout: 20000 });
  st = await ev(() => { const f = document.querySelector("#recent .rfoot"); return { color: getComputedStyle(f).color, free: document.querySelector("#rFree").disabled }; });
  assert.equal(st.color, "rgb(139, 149, 167)", "pie del inicio con --muted (5.6:1)");
  await shot("12_inicio");

  const bad = errs.filter(e => !/favicon/.test(e));
  assert.deepEqual(bad, [], "errores en la consola o en la red");
  console.log("OK limpieza e2e");
} catch (e) {
  console.error("FALLO:", e && e.message ? e.message : e);
  console.error(errs.join("\n"));
  await shot("zz_fallo").catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
