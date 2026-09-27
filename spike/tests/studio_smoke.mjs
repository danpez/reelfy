// Prueba E2E del Studio (editor pro) en un navegador real, de inicio a fin, con entrada real
// (mouse y teclado de Playwright):
//  abrir un trabajo analizado -> dirigir (si hace falta) -> layout en tamanos reales de la app ->
//  editar un titulo con `lines` (etiqueta del bloque + cuadro exacto) -> pestana estable ->
//  bloquear + cambiar estilo sin duplicados -> regenerar solo lo pedido -> iman monotono al
//  arrastrar un borde -> capa de "dirigiendo" que bloquea la edicion -> idiomas y marca en pro ->
//  agregar SFX -> cuadro exacto -> zoom -> vista previa real -> export cancelado y completo ->
//  "Nuevo video" guarda y "Proyectos recientes" reabre -> consola sin errores.
//
// Es OPCIONAL: playwright-core no es dependencia del repo. Uso:
//   REELFY_PLAYWRIGHT=/ruta/a/node_modules/playwright-core \
//   node spike/tests/studio_smoke.mjs "http://127.0.0.1:8014/?job=ID" [--no-export] [--headed]
// Sale con codigo 0 si todo pasa; imprime un resumen JSON y deja capturas en $REELFY_SMOKE_OUT
// (default: carpeta temporal del sistema). OJO: edita el plan del trabajo (usa uno de pruebas).
import { createRequire } from "node:module";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("Define REELFY_PLAYWRIGHT con la ruta de playwright-core."); process.exit(2); }
const { chromium } = require(PW);
const url = process.argv[2];
if (!url || !/[?&]job=/.test(url)) { console.error("Uso: node studio_smoke.mjs 'http://127.0.0.1:PUERTO/?job=ID'"); process.exit(2); }
const JOB = new URL(url).searchParams.get("job");
const EXPORT = !process.argv.includes("--no-export");
const OUT = process.env.REELFY_SMOKE_OUT || fs.mkdtempSync(path.join(os.tmpdir(), "reelfy-smoke-"));
fs.mkdirSync(OUT, { recursive: true });

const steps = [];
const pageErrors = [];
function ok(name, detail) { steps.push({ name, ok: true, detail }); console.log(`ok  ${name}${detail ? " · " + JSON.stringify(detail) : ""}`); }
function fail(name, detail) { steps.push({ name, ok: false, detail }); console.log(`MAL ${name} · ${JSON.stringify(detail)}`); }
async function check(name, fn) {
  try { const d = await fn(); ok(name, d); return d; } catch (e) { fail(name, String(e && e.message || e)); return null; }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

// REELFY_PW_CHANNEL=chrome usa el Chrome instalado (decodifica H.264: la vista en vivo muestra el video)
const browser = await chromium.launch({ headless: !process.argv.includes("--headed"), channel: process.env.REELFY_PW_CHANNEL || undefined });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
page.on("pageerror", e => pageErrors.push(String(e && e.message || e)));
page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) pageErrors.push(m.text()); });
const shot = async n => page.screenshot({ path: path.join(OUT, `${n}.png`) });
const idle = () => page.waitForFunction(() => !FX.busy(), null, { timeout: 600000 });
/** Espera el cuadro exacto del servidor y devuelve el hash de sus bytes. */
async function exactHash(t) {
  await page.evaluate(tt => { FX.setExact(true); V.pause(); FX.hideExact(); V.currentTime = tt; }, t);
  await page.waitForTimeout(150);
  await page.waitForFunction(tt => document.querySelector("#pvExact.on") && Math.abs(V.currentTime - tt) < 1e-3, t, { timeout: 120000 });
  const b64 = await page.evaluate(async () => {
    const src = document.querySelector("#pvExact").src; const buf = await (await fetch(src)).arrayBuffer();
    let s = ""; new Uint8Array(buf).forEach(x => { s += String.fromCharCode(x); }); return btoa(s);
  });
  return crypto.createHash("sha1").update(Buffer.from(b64, "base64")).digest("hex").slice(0, 12);
}
/** Geometria del editor: tamano del canvas y pistas visibles sin hacer scroll. */
const layout = () => page.evaluate(() => {
  const cv = document.querySelector("#cv").getBoundingClientRect(), sc = document.querySelector("#tlscroll").getBoundingClientRect();
  const rows = [...document.querySelectorAll("#tlinner .trk")].filter(r => r.getBoundingClientRect().height > 0 && r.querySelector(".tn"));
  const vis = rows.filter(r => { const b = r.getBoundingClientRect(); return b.top >= sc.top - 1 && b.bottom <= sc.bottom + 1; }).length;
  return { canvas: [Math.round(cv.width), Math.round(cv.height)], tracks: `${vis}/${rows.length}`, visible: vis };
});

try {
  await page.goto(url);
  await check("abre el editor del trabajo", async () => {
    await page.waitForSelector("#s-editor.on", { timeout: 600000 });
    return { pro: await page.evaluate(() => FX.isPro()) };
  });
  if (!(await page.evaluate(() => FX.isPro()))) {
    await check("dirige la edicion (v1 -> v2)", async () => {
      await page.click("#dirFirst");
      await page.waitForFunction(() => FX.isPro() && !FX.busy(), null, { timeout: 600000 });
      return await page.evaluate(() => ({ text: PLAN.fx.text.length, sfx: PLAN.fx.sfx.length }));
    });
  }
  await check("pistas fx dibujadas", async () => {
    await page.waitForFunction(() => document.querySelectorAll(".fxrow").length === 7, null, { timeout: 20000 });
    return await page.evaluate(() => [...document.querySelectorAll(".fxrow")].map(r => r.dataset.track));
  });
  await page.waitForTimeout(800);
  await shot("01_editor_1440x900");

  // ---------------------------------------------------------------- layout en tamanos reales
  await check("layout: vista previa y timeline legibles en 1340x940, 1440x900 y 980x680", async () => {
    const out = {};
    // minimo de pistas visibles sin scroll (depende de cuantas filas ocupa cada pista con este plan)
    for (const [w, h, minH, minTracks] of [[1340, 940, 400, 5], [1440, 900, 380, 5], [980, 680, 250, 3]]) {
      await page.setViewportSize({ width: w, height: h }); await page.waitForTimeout(500);
      const l = await layout(); out[`${w}x${h}`] = l;
      await shot(`02_layout_${w}x${h}`);
      if (l.canvas[1] < minH) throw new Error(`${w}x${h}: canvas ${l.canvas} < ${minH} px de alto`);
      if (l.visible < minTracks) throw new Error(`${w}x${h}: solo ${l.tracks} pistas visibles`);
    }
    await page.setViewportSize({ width: 1440, height: 900 }); await page.waitForTimeout(400);
    return out;
  });
  await check("telefono 390 px: ningun elemento visible se sale por la derecha", async () => {
    await page.setViewportSize({ width: 390, height: 844 }); await page.waitForTimeout(500);
    const r = await page.evaluate(async () => {
      const probe = () => {
        const W = innerWidth, out = [], tl = document.querySelector("#tlscroll");
        const vis = el => { for (let p = el; p; p = p.parentElement) { const cs = getComputedStyle(p); if (cs.display === "none" || cs.visibility === "hidden") return false; } return true; };
        document.querySelectorAll("#s-editor.on *, header *").forEach(el => {
          if (tl.contains(el) && el !== tl) return; const b = el.getBoundingClientRect();
          if (b.width >= 1 && b.height >= 1 && b.right > W + 0.5 && vis(el)) out.push((el.id || el.className || el.tagName) + ":" + Math.round(b.right));
        });
        return out;
      };
      const a = probe(); setPanelTab("elem"); FX.select("text", PLAN.fx.text[0].id); await new Promise(r => setTimeout(r, 250));
      const b = probe(); FX.selectMusic(); await new Promise(r => setTimeout(r, 250)); const c = probe();
      FX.select(null); setPanelTab("proj");
      return { proyecto: a, elemento: b, musica: c, docW: document.documentElement.scrollWidth, W: innerWidth };
    });
    await shot("03_telefono_390");
    await page.setViewportSize({ width: 1440, height: 900 }); await page.waitForTimeout(400);
    const n = r.proyecto.length + r.elemento.length + r.musica.length;
    if (n || r.docW > r.W) throw new Error(JSON.stringify(r).slice(0, 600));
    return { recortados: 0, docW: r.docW };
  });

  // ---------------------------------------------------------------- titulo con lines
  const title = await page.evaluate(() => {
    let it = PLAN.fx.text.find(t => t.preset !== "counter" && Array.isArray(t.lines) && t.lines.length >= 2 && t.enabled !== false);
    if (!it) { it = PLAN.fx.text.find(t => t.preset !== "counter"); FX.edit(() => { it.lines = [{ text: "LINEA UNO", scale: 1 }, { text: "linea dos", scale: 0.6 }]; }); }
    return { id: it.id, start: it.start, end: it.end, lines: it.lines.length };
  });
  await check("el campo Texto de un titulo con estilo por linea cambia la etiqueta, el vivo y el cuadro exacto (1 paso de deshacer)", async () => {
    const tMid = +(title.start + (title.end - title.start) * 0.8).toFixed(3);
    const h0 = await exactHash(tMid);
    await page.click(`[data-fx="text:${title.id}"]`);
    await page.waitForSelector('#tabElem.on #inspBody [data-k="title-text"]');
    const u0 = await page.evaluate(() => UNDO.length);
    const ta = page.locator('#inspBody [data-k="title-text"]');
    await ta.click(); await ta.press("ControlOrMeta+a");
    await ta.pressSequentially("HUMO UNO\nhumo dos", { delay: 15 });
    await ta.press("Tab");
    const r = await page.evaluate(id => {
      const it = PLAN.fx.text.find(x => x.id === id);
      return { undo: UNDO.length, lines: it.lines.map(l => l.text), label: document.querySelector(`[data-fx="text:${id}"] .lb`).textContent, locked: it.locked };
    }, title.id);
    if (r.undo !== u0 + 1) throw new Error(`se esperaba 1 paso de deshacer, hubo ${r.undo - u0}`);
    if (r.lines.join("|") !== "HUMO UNO|humo dos") throw new Error(`lines: ${r.lines}`);
    if (!/HUMO UNO humo dos/.test(r.label)) throw new Error(`etiqueta del bloque: ${r.label}`);
    const h1 = await exactHash(tMid);
    await shot("04_titulo_editado_exacto");
    if (h1 === h0) throw new Error("el cuadro exacto no cambio con el texto nuevo");
    await page.keyboard.press("Escape");
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press("ControlOrMeta+z");
    const back = await page.evaluate(id => PLAN.fx.text.find(x => x.id === id).lines.map(l => l.text).join("|"), title.id);
    if (back.includes("HUMO")) throw new Error("deshacer no restauro el titulo");
    return { label: r.label, autoBloqueo: r.locked, exacto: [h0, h1] };
  });

  // ---------------------------------------------------------------- pestana estable
  await check("cambiar estilo y deshacer NO saltan a la pestana Elemento", async () => {
    await page.evaluate(id => { FX.select("text", id); setPanelTab("proj"); }, title.id);
    const target = await page.evaluate(() => ["dinamico", "elegante"].find(s => s !== FX.dirStyle()));
    await page.click(`.dircard[data-v="${target}"]`);
    await page.waitForFunction(() => FX.busy(), null, { timeout: 5000 }).catch(() => {});
    await idle();
    const a = await page.evaluate(() => document.querySelector(".ptabs button.sel").dataset.tab);
    await page.keyboard.press("ControlOrMeta+z");
    const b = await page.evaluate(() => document.querySelector(".ptabs button.sel").dataset.tab);
    if (a !== "proj" || b !== "proj") throw new Error(`pestana tras estilo=${a}, tras deshacer=${b}`);
    await page.keyboard.press("ControlOrMeta+Shift+z");
    return { estilo: target, pestana: a };
  });

  // ---------------------------------------------------------------- bloquear + re-dirigir
  await check("bloquear un titulo y cambiar de estilo no lo duplica", async () => {
    const res = [];
    for (const st of ["energetico", "minimal", "dinamico"]) {
      await page.evaluate(() => {
        const t = PLAN.fx.text.find(x => x.preset !== "counter" && FXCore.titleText(x).trim());
        FX.select("text", t.id); if (!t.locked) FX.action("lock");
      });
      await page.click(`.dircard[data-v="${st}"]`);
      await page.waitForFunction(() => FX.busy(), null, { timeout: 5000 }).catch(() => {});
      await idle();
      const dup = await page.evaluate(() => {
        const L = PLAN.fx.text.filter(t => t.locked);
        return L.flatMap(l => PLAN.fx.text.filter(t => t !== l && FXCore.normText(FXCore.titleText(t)) === FXCore.normText(FXCore.titleText(l))
          && Math.min(t.end, l.end) - Math.max(t.start, l.start) > 0.5 * Math.min(t.end - t.start, l.end - l.start)).map(t => t.id));
      });
      res.push({ st, dup });
      if (dup.length) throw new Error(`${st}: duplicados de lo bloqueado: ${dup}`);
    }
    return res;
  });

  // ---------------------------------------------------------------- regenerar
  await check("Regenerar con IA solo toca el elemento pedido", async () => {
    const r = await page.evaluate(async () => {
      const it = PLAN.fx.graphics.find(g => !g.locked) || PLAN.fx.effects.find(g => !g.locked);
      const track = PLAN.fx.graphics.includes(it) ? "graphics" : "effects";
      const before = JSON.parse(JSON.stringify(PLAN.fx));
      FX.select(track, it.id);
      await FX.regenerate([it.id]);
      const touched = [];
      FXCore.TRACKS.forEach(t => {
        const a = before[t] || [], b = PLAN.fx[t] || [];
        b.forEach(x => { const y = a.find(z => z.id === x.id); if (!y || JSON.stringify(y) !== JSON.stringify(x)) touched.push(`${t}:${x.id}`); });
        a.forEach(x => { if (!b.some(z => z.id === x.id)) touched.push(`-${t}:${x.id}`); });
      });
      return { id: it.id, track, touched, toast: document.querySelector(".toast").textContent };
    });
    const other = r.touched.filter(x => !x.startsWith(`${r.track}:`) && x !== `-${r.track}:${r.id}`);
    if (other.length || r.touched.filter(x => x.startsWith(`${r.track}:`)).length > 1) throw new Error(`cambios ajenos: ${r.touched}`);
    return r;
  });

  // ---------------------------------------------------------------- iman al arrastrar un borde
  await check("arrastrar el borde de un bloque con el mouse: valores monotonos (el iman no se pega al playhead que se mueve)", async () => {
    await page.evaluate(() => { tlSetZoom(1.8); V.pause(); V.currentTime = 2; });
    await page.waitForTimeout(300);
    const id = await page.evaluate(() => { const t = PLAN.fx.text.filter(x => x.end - x.start > 1).sort((a, b) => a.start - b.start)[0]; document.querySelector(`[data-fx="text:${t.id}"]`).scrollIntoView({ block: "nearest", inline: "center" }); return t.id; });
    await page.waitForTimeout(200);
    await page.hover(`[data-fx="text:${id}"]`);
    const h = await page.locator(`[data-fx="text:${id}"] .hnd.r`).boundingBox();
    const u0 = await page.evaluate(() => UNDO.length);
    const x0 = h.x + h.width / 2, y0 = h.y + h.height / 2, vals = [];
    await page.mouse.move(x0, y0); await page.mouse.down();
    for (let i = 1; i <= 45; i++) { await page.mouse.move(x0 + i * 2, y0); vals.push(await page.evaluate(id => PLAN.fx.text.find(x => x.id === id).end, id)); }
    await page.mouse.up();
    const bad = vals.filter((v, i) => i && v < vals[i - 1] - 1e-9).length;
    const undo = await page.evaluate(() => UNDO.length) - u0;
    await page.keyboard.press("ControlOrMeta+z");
    if (bad) throw new Error(`${bad} retrocesos: ${vals.join(", ")}`);
    if (undo !== 1) throw new Error(`pasos de deshacer: ${undo}`);
    return { de: vals[0], a: vals[vals.length - 1], pasos: vals.length };
  });

  // ---------------------------------------------------------------- capa "dirigiendo"
  await check("mientras la IA dirige, el editor queda bloqueado (Supr y deshacer no cambian el plan)", async () => {
    await page.route("**/direct/**", async route => { await sleep(3500); await route.continue(); });
    try {
      const s0 = await page.evaluate(() => { FX.select("text", PLAN.fx.text[0].id); setPanelTab("proj"); return JSON.stringify(PLAN); });
      await page.click("#dirVar");
      await page.waitForSelector("#busyOv.on");
      await page.waitForTimeout(400);                        // termina el fundido de la capa
      await shot("05_dirigiendo_bloqueado");
      await page.keyboard.press("Delete"); await page.keyboard.press("ControlOrMeta+z");
      const same = await page.evaluate(s => JSON.stringify(PLAN) === s, s0);
      const disabled = await page.evaluate(() => document.querySelector("#render").disabled);
      await idle();
      if (!same) throw new Error("el plan cambio durante la direccion");
      return { exportarDeshabilitado: disabled };
    } finally { await page.unroute("**/direct/**"); }
  });

  // ---------------------------------------------------------------- idiomas y marca en pro
  await check("edicion IA: PT/FR/DE/IT deshabilitados, logo/aire/audio no prometen lo que el export no aplica", async () => {
    return await page.evaluate(() => {
      const langs = [...document.querySelectorAll("#segLang button")].filter(b => b.disabled).map(b => b.dataset.v);
      const opts = [...FX.proOpts()];
      const logoOff = document.querySelector("#logoOn").disabled;
      if (JSON.stringify(langs) !== JSON.stringify(["pt", "fr", "de", "it"])) throw new Error(`idiomas deshabilitados: ${langs}`);
      if (!opts.includes("logo") && !logoOff) throw new Error("el logo parece activo aunque el render pro no lo aplica");
      return { deshabilitados: langs, proOpts: opts, logoDeshabilitado: logoOff };
    });
  });

  // ---------------------------------------------------------------- flujo base
  await check("agrega un SFX en el playhead desde el menu", async () => {
    const n0 = await page.evaluate(() => PLAN.fx.sfx.length);
    await page.click("#addAny");
    await page.click('.fxmenu.on [data-t="sfx"]');
    await page.click(".fxmenu.on [data-k]");
    const n1 = await page.evaluate(() => PLAN.fx.sfx.length);
    if (n1 !== n0 + 1) throw new Error("no se agrego el SFX");
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press("ControlOrMeta+z");            // deja el plan como estaba
    const n2 = await page.evaluate(() => PLAN.fx.sfx.length);
    if (n2 !== n0) throw new Error("deshacer no quito el SFX");
    return { sfx: n1 };
  });
  await check("cuadro exacto del servidor al pausar", async () => {
    const h = await exactHash(Math.min(await page.evaluate(() => PLAN.duration) - 0.5, 5.2));
    return { badge: await page.textContent("#pvBadge"), hash: h };
  });
  await shot("06_exacto");
  await check("zoom del timeline en px/seg", async () => {
    await page.click("#tlFit");
    const px0 = await page.evaluate(() => TL.px);
    for (let i = 0; i < 3; i++) await page.click("#tlIn");
    const px1 = await page.evaluate(() => TL.px);
    if (!(px1 > px0 * 3)) throw new Error(`zoom insuficiente ${px0} -> ${px1}`);
    return { px0, px1 };
  });
  await check("vista previa real (render draft con audio)", async () => {
    await page.click("#btnReal");
    await page.waitForSelector("#pvmVid.on", { timeout: 900000 });
    const src = await page.getAttribute("#pvmVid", "src");
    const r = await page.request.get(new URL(src, url).href);
    if (!r.ok()) throw new Error(`el MP4 no responde: ${r.status()}`);
    await shot("07_vista_previa_real");
    await page.click("#pvmClose");
    return { src: src.split("?")[0] };
  });
  if (EXPORT) {
    await check("export: Cancelar vuelve al editor sin perder la edicion", async () => {
      const s0 = await page.evaluate(() => JSON.stringify(PLAN.fx));
      await page.click("#render");
      await page.waitForSelector("#s-rendering.on");
      await page.waitForFunction(() => document.querySelector("#r-stages .stg.cur"), null, { timeout: 120000 });
      await page.click("#r-cancel");
      await page.waitForSelector("#s-editor.on", { timeout: 300000 });
      const same = await page.evaluate(s => JSON.stringify(PLAN.fx) === s, s0);
      if (!same) throw new Error("la edicion cambio al cancelar");
      return { toast: await page.textContent(".toast") };
    });
    await check("export con etapas y resultados (video completo + shorts)", async () => {
      await page.click("#render");
      await page.waitForSelector("#s-rendering.on");
      const seen = new Set();
      const t0 = Date.now();
      while (!(await page.$("#s-results.on"))) {
        (await page.$$eval("#r-stages .stg.cur", e => e.map(x => x.textContent.trim()))).forEach(x => seen.add(x));
        if (Date.now() - t0 > 1800000) throw new Error("export demasiado lento");
        if (await page.$("#s-error.on")) throw new Error(await page.textContent("#err-msg"));
        await sleep(700);
      }
      const clips = await page.$$eval("#clips .clip b", bs => bs.map(b => b.textContent));
      if (!clips.length || !/completo/i.test(clips[0])) throw new Error(`resultados inesperados: ${clips}`);
      await shot("08_resultados");
      return { clips, etapas: [...seen], s: Math.round((Date.now() - t0) / 1000) };
    });
    await page.click("#backEditor");
    await page.waitForSelector("#s-editor.on");
  }
  await check("'Nuevo video' guarda la ultima edicion y 'Proyectos recientes' reabre el proyecto", async () => {
    const v = await page.evaluate(() => { const it = PLAN.fx.text[0]; const v = +(0.05 + Math.random() * 0.1).toFixed(4); FX.edit(() => { it.size = v; }); return v; });
    await page.click("#cancel");                              // enseguida: sin esperar el autoguardado
    await page.waitForSelector("#s-setup.on");
    const saved = await (await page.request.get(new URL(`/plan/${JOB}`, url).href)).json();
    if (Math.abs(saved.plan.fx.text[0].size - v) > 1e-6) throw new Error(`no se guardo: ${saved.plan.fx.text[0].size} != ${v}`);
    await page.waitForSelector(`#recent .rrow[data-id="${JOB}"]`, { timeout: 20000 });
    await shot("09_recientes");
    await page.click(`#recent .rrow[data-id="${JOB}"]`);
    await page.waitForSelector("#s-editor.on", { timeout: 60000 });
    return { size: v, reabierto: await page.evaluate(() => JOB) };
  });
  if (pageErrors.length) fail("consola sin errores de JS", pageErrors.slice(0, 5));
  else ok("consola sin errores de JS");
} finally {
  await browser.close();
}
const res = { ok: steps.every(s => s.ok), steps, out: OUT };
fs.writeFileSync(path.join(OUT, "resumen.json"), JSON.stringify(res, null, 2));
console.log(JSON.stringify({ ok: res.ok, out: OUT }));
process.exit(res.ok ? 0 : 1);
