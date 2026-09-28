// Prueba E2E del Studio (editor pro) en un navegador real, de inicio a fin, con entrada real
// (mouse y teclado de Playwright):
//  [--compose] pantalla inicial (proyectos recientes con UNA peticion, perfil por "Pegar
//  descripcion") -> ensamblado -> "Aplicar IA" (/compose con el perfil) -> editor ->
//  layout en tamanos reales de la app (minimo 1200x760) -> telefono -> titulo con `lines` ->
//  pestana estable -> bloquear + cambiar estilo sin duplicados -> regenerar (variante real + piezas
//  ligadas) -> iman monotono -> capa de "dirigiendo" -> opciones del proyecto (/settings: logo,
//  audio, aire opt-in, idiomas) -> subtitulos del render (/captions) y video proxy -> perfil en el
//  editor (pegar descripcion, aplicar al instante, deshacer) -> PiP propio con colocacion
//  automatica -> traduccion a PT (subtitulos + titulos; lo editado a mano se respeta y "volver"
//  recupera TODAS las lineas) -> recargar conserva idioma y logo -> musica IA que se sincroniza
//  SOLA (sin boton) sin ediciones y con el titulo del hook bloqueado, y vista previa que se
//  actualiza con ella -> SFX, cuadro exacto, zoom -> export cancelado y completo con avisos en
//  lenguaje humano (SFX desconocido marcado en rojo) -> "Nuevo video" guarda y "Proyectos
//  recientes" reabre -> consola sin errores y ninguna respuesta HTTP >= 400 inesperada.
//
// Es OPCIONAL: playwright-core no es dependencia del repo. Uso:
//   REELFY_PLAYWRIGHT=/ruta/a/node_modules/playwright-core REELFY_PW_CHANNEL=chrome \
//   node spike/tests/studio_smoke.mjs "http://127.0.0.1:8014/?job=ID" [--no-export] [--headed]
//   node spike/tests/studio_smoke.mjs "http://127.0.0.1:8014/" --compose DIR_CON_IMG_*.MOV
// Perfil: por defecto uno FICTICIO; REELFY_SMOKE_PROFILE_TEXT cambia el texto que se pega (datos
// reales solo por variable de entorno, nunca en el repo). REELFY_SMOKE_NO_LLM=1 acepta que la IA
// local no responda (traduccion y "Pegar descripcion" se prueban sin exigir el LLM).
// Sale con codigo 0 si todo pasa; imprime un resumen JSON y deja capturas en $REELFY_SMOKE_OUT
// (default: carpeta temporal del sistema). OJO: edita el plan del trabajo (usa uno de pruebas).
// "Proyectos recientes" exige al menos un proyecto en el DATA del servidor.
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
const ci = process.argv.indexOf("--compose");
const COMPOSE = ci > 0 ? process.argv[ci + 1] : null;
if (!url || (!/[?&]job=/.test(url) && !COMPOSE)) { console.error("Uso: node studio_smoke.mjs 'http://127.0.0.1:PUERTO/?job=ID' | 'http://127.0.0.1:PUERTO/' --compose DIR"); process.exit(2); }
let JOB = new URL(url).searchParams.get("job");
const EXPORT = !process.argv.includes("--no-export");
const NO_LLM = process.env.REELFY_SMOKE_NO_LLM === "1";
const OUT = process.env.REELFY_SMOKE_OUT || fs.mkdtempSync(path.join(os.tmpdir(), "reelfy-smoke-"));
fs.mkdirSync(OUT, { recursive: true });
// perfil FICTICIO (docs/MOTION-ENGINE.md 3.2): nunca datos de un cliente en el repo
const PROFILE_TEXT = process.env.REELFY_SMOKE_PROFILE_TEXT
  || "Hola, soy la Dra. Lucía Demo, odontóloga. Sígueme en Instagram @dra.lucia.demo y agenda tu cita en https://paseo.club/business/dra-lucia-demo";
// OTRA persona (ficticia): "Usar estos datos" no debe mezclarla con el perfil anterior
const OTHER_TEXT = "Soy el Lic. Pedro Ficticio, nutriólogo. Encuéntrame en TikTok @pedro.fit.demo";

const steps = [];
const pageErrors = [];
function ok(name, detail) { steps.push({ name, ok: true, detail }); console.log(`ok  ${name}${detail ? " · " + JSON.stringify(detail) : ""}`); }
function fail(name, detail) { steps.push({ name, ok: false, detail }); console.log(`MAL ${name} · ${JSON.stringify(detail)}`); }
async function check(name, fn) {
  const t0 = Date.now();
  try { const d = await fn(); ok(name, Object.assign({ s: +((Date.now() - t0) / 1000).toFixed(1) }, d || {})); return d; }
  catch (e) { fail(name, String(e && e.message || e)); return null; }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

// REELFY_PW_CHANNEL=chrome usa el Chrome instalado (decodifica H.264: la vista en vivo muestra el video)
const browser = await chromium.launch({ headless: !process.argv.includes("--headed"), channel: process.env.REELFY_PW_CHANNEL || undefined });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
page.on("pageerror", e => pageErrors.push(String(e && e.message || e)));
page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) pageErrors.push(m.text()); });
const reqs = [];
page.on("request", r => reqs.push(r.method() + " " + new URL(r.url()).pathname));
// respuestas HTTP >= 400: la consola filtra "Failed to load resource", asi que se registran aqui y la
// prueba falla si aparece alguna que no sea esperada (logo de marca aun sin subir; sin IA local en
// modo NO_LLM)
const badResp = [];
page.on("response", r => {
  const st = r.status(); if (st < 400) return;
  const u = new URL(r.url()), pth = u.pathname;
  if (/^\/brand\/logo$/.test(pth) && st === 404) return;
  if (NO_LLM && st === 503 && /^\/(translate|profile\/parse)/.test(pth)) return;
  badResp.push(`${r.request().method()} ${pth} ${st}`);
});
const shot = async n => page.screenshot({ path: path.join(OUT, `${n}.png`) });
const idle = () => page.waitForFunction(() => !FX.busy(), null, { timeout: 600000 });
const base = new URL(url).origin;
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
/** Elementos visibles que se salen por la derecha (telefono). */
const overflowProbe = sel => page.evaluate(sel => {
  const W = innerWidth, out = [], tl = document.querySelector("#tlscroll");
  const vis = el => { for (let p = el; p; p = p.parentElement) { const cs = getComputedStyle(p); if (cs.display === "none" || cs.visibility === "hidden") return false; } return true; };
  document.querySelectorAll(sel).forEach(el => {
    if (tl && tl.contains(el) && el !== tl) return; const b = el.getBoundingClientRect();
    if (b.width >= 1 && b.height >= 1 && b.right > W + 0.5 && vis(el)) out.push((el.id || el.className || el.tagName) + ":" + Math.round(b.right));
  });
  return out;
}, sel);
/** Pega un texto en "Pegar descripcion" del panel Perfil, lo entiende y usa los datos. */
async function profileFromText(text) {
  await page.waitForSelector("#pfm.on");
  await page.evaluate(() => { document.querySelector("#pfm details.pfparse").open = true; });
  await page.fill('#pfm [data-a="ptext"]', text);
  await page.click('#pfm [data-a="parse"]');
  await page.waitForSelector("#pfm .pfres", { timeout: 120000 });
  const res = await page.evaluate(() => ({ text: document.querySelector("#pfm .pfres").innerText, llm: /IA local/.test(document.querySelector("#pfm .pfres h6").textContent), ms: PF.lastParseMs }));
  await page.click('#pfm [data-a="use"]');
  const d = await page.evaluate(() => { const d = PF.draft(); return { name: d.name, title: d.title, links: (d.links || []).map(l => `${l.platform}:${l.handle || l.display}`) }; });
  if (!d.name || !d.links.length) throw new Error(`no se entendio el perfil: ${JSON.stringify(d)}`);
  if (!res.llm && !NO_LLM) throw new Error("la IA local no respondio al entender el perfil");
  return Object.assign(d, { llm: res.llm, ms: res.ms });
}

try {
  if (COMPOSE) {
    // ------------------------------------------------------------ inicio -> ensamblado -> /compose
    await page.goto(url);
    await check("inicio: 'Proyectos recientes' con una sola peticion (GET /jobs, sin GET /plan por proyecto)", async () => {
      await page.waitForTimeout(1500);
      const plans = reqs.filter(r => /^GET \/plan\//.test(r)).length, jobs = reqs.filter(r => /^GET \/jobs/.test(r)).length;
      const rows = await page.$$eval("#recent .rrow", rs => rs.map(r => r.innerText.split("\n").slice(0, 2).join(" | ")));
      if (plans) throw new Error(`${plans} GET /plan al abrir el inicio`);
      if (jobs !== 1) throw new Error(`${jobs} GET /jobs`);
      if (!rows.length) throw new Error("'Proyectos recientes' vacio: el DATA del servidor necesita al menos un proyecto");
      return { recientes: rows.length, primero: rows[0] || null };
    });
    const prof = await check("inicio: perfil por 'Pegar descripcion' (IA local) -> 'Usar en el proximo video'", async () => {
      await page.click("#homeProfile [data-e]");
      const d = await profileFromText(PROFILE_TEXT);
      await shot("00_perfil_entendido");
      await page.click('#pfm [data-a="apply"]');
      await page.waitForSelector("#pfm.on", { state: "detached", timeout: 5000 }).catch(() => {});
      const sum = await page.textContent("#homeProfile .pfsum");
      if (!sum.includes(d.name)) throw new Error(`resumen: ${sum}`);
      return Object.assign(d, { resumen: sum });
    });
    await check("ensamblado con los clips y 'Aplicar IA' lleva el perfil en /compose hasta el editor", async () => {
      const files = fs.readdirSync(COMPOSE).filter(f => /^IMG_.*\.MOV$/i.test(f)).sort().slice(0, 6).map(f => path.join(COMPOSE, f));
      if (!files.length) throw new Error(`sin IMG_*.MOV en ${COMPOSE}`);
      const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.click("#drop")]);
      await fc.setFiles(files);
      await page.click("#go");
      await page.waitForSelector("#s-assembly.on");
      await page.waitForFunction(n => ASM.clips.length === n, files.length, { timeout: 600000 });
      const chipTxt = await page.evaluate(() => document.querySelector("#asmProfile select").selectedOptions[0].textContent);
      await shot("00_ensamblado_con_perfil");
      let body = null;
      page.on("request", r => { if (/\/compose$/.test(r.url())) body = JSON.parse(r.postData() || "{}"); });
      const t0 = Date.now();
      await page.click("#asmApply");
      await page.waitForSelector("#s-editor.on", { timeout: 1800000 });
      JOB = await page.evaluate(() => JOB);
      const got = await page.evaluate(() => ({ name: PLAN.profile && PLAN.profile.name, lt: PLAN.fx.graphics.filter(g => g.kind === "lower_third").map(g => [g.title, g.subtitle]),
        follow: PLAN.fx.graphics.filter(g => g.kind === "cta" && g.variant === "follow").map(g => g.handle) }));
      if (!body || !body.profile || body.profile.name !== prof.name) throw new Error(`/compose sin el perfil: ${JSON.stringify(body && body.profile)}`);
      if (got.name !== prof.name || !got.lt.some(x => x[0] === prof.name)) throw new Error(`el editor no trae el perfil: ${JSON.stringify(got)}`);
      return { clips: files.length, selector: chipTxt, job: JOB, review_s: Math.round((Date.now() - t0) / 1000), rotulo: got.lt, seguir: got.follow };
    });
    if (!JOB) throw new Error("sin trabajo: no se puede seguir");
  } else await page.goto(url);

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
    return { pistas: await page.evaluate(() => [...document.querySelectorAll(".fxrow")].map(r => r.dataset.track)) };
  });
  await page.waitForTimeout(800);
  await shot("01_editor_1440x900");

  // ---------------------------------------------------------------- layout en tamanos reales
  await check("layout: vista previa y timeline legibles en 1340x940, 1440x900 y 1200x760 (minimo de la app)", async () => {
    const out = {};
    // minimo de pistas visibles sin scroll (depende de cuantas filas ocupa cada pista con este plan)
    for (const [w, h, minH, minTracks] of [[1340, 940, 400, 5], [1440, 900, 380, 5], [1200, 760, 300, 4]]) {
      await page.setViewportSize({ width: w, height: h }); await page.waitForTimeout(500);
      const l = await layout(); out[`${w}x${h}`] = l;
      await shot(`02_layout_${w}x${h}`);
      if (l.canvas[1] < minH) throw new Error(`${w}x${h}: canvas ${l.canvas} < ${minH} px de alto`);
      if (l.visible < minTracks) throw new Error(`${w}x${h}: solo ${l.tracks} pistas visibles`);
    }
    await page.setViewportSize({ width: 1440, height: 900 }); await page.waitForTimeout(400);
    return out;
  });
  await check("telefono 390 px: ningun elemento visible se sale por la derecha (proyecto, elemento, musica y panel Perfil)", async () => {
    await page.setViewportSize({ width: 390, height: 844 }); await page.waitForTimeout(500);
    const a = await overflowProbe("#s-editor.on *, header *");
    await page.evaluate(async () => { setPanelTab("elem"); FX.select("text", PLAN.fx.text[0].id); await new Promise(r => setTimeout(r, 250)); });
    const b = await overflowProbe("#s-editor.on *, header *");
    await page.evaluate(async () => { FX.selectMusic(); await new Promise(r => setTimeout(r, 250)); });
    const c = await overflowProbe("#s-editor.on *, header *");
    await page.evaluate(async () => { FX.select(null); setPanelTab("proj"); await PF.open({ mode: "editor", profile: PLAN.profile || null }); await new Promise(r => setTimeout(r, 300)); });
    const d = await overflowProbe("#pfm *");
    await shot("03_telefono_390_perfil");
    await page.evaluate(() => PF.close());
    const docW = await page.evaluate(() => document.documentElement.scrollWidth);
    await shot("03_telefono_390");
    await page.setViewportSize({ width: 1440, height: 900 }); await page.waitForTimeout(400);
    const n = a.length + b.length + c.length + d.length;
    if (n || docW > 390) throw new Error(JSON.stringify({ proyecto: a, elemento: b, musica: c, perfil: d, docW }).slice(0, 600));
    return { recortados: 0, docW };
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
    return { estilos: res };
  });

  // ---------------------------------------------------------------- regenerar
  await check("Regenerar con IA: variante real del elemento pedido; fuera de el solo cambian sus piezas ligadas", async () => {
    const out = [];
    for (const track of ["transitions", "text", "graphics"]) {
      const r = await page.evaluate(async tk => {
        const it = PLAN.fx[tk].find(g => !g.locked && g.src !== "user" && (tk !== "text" || g.preset !== "counter") && (tk !== "graphics" || ["doodle", "icon", "sticker_text", "card"].includes(g.kind)));
        if (!it) return null;
        const comps = [...FXCore.regenCompanions(PLAN.fx, [it.id]).keys()];
        const before = JSON.parse(JSON.stringify(PLAN.fx));
        FX.select(tk, it.id);
        await FX.regenerate([it.id]);
        const touched = [];
        FXCore.TRACKS.forEach(t => {
          const a = before[t] || [], b = PLAN.fx[t] || [];
          b.forEach(x => { const y = a.find(z => z.id === x.id); if (!y || JSON.stringify(y) !== JSON.stringify(x)) touched.push(`${t}:${x.id}`); });
          a.forEach(x => { if (!b.some(z => z.id === x.id)) touched.push(`-${t}:${x.id}`); });
        });
        const now = PLAN.fx[tk].find(z => z.id === it.id) || {};
        return { tk, id: it.id, comps, touched, antes: it.kind || it.preset, ahora: now.kind || now.preset, toast: document.querySelector(".toast").textContent };
      }, track);
      if (!r) continue;
      const ajenos = r.touched.filter(x => !x.endsWith(`:${r.id}`) && !r.comps.some(c => x.endsWith(`:${c}`)));
      if (ajenos.length) throw new Error(`${track}: cambios ajenos ${ajenos}`);
      out.push({ tk: r.tk, antes: r.antes, ahora: r.ahora, cambio: r.touched.length > 0, ligadas: r.touched.filter(x => !x.endsWith(`:${r.id}`)) });
    }
    if (!out.some(x => x.cambio)) throw new Error(`ninguna variante real: ${JSON.stringify(out)}`);
    return { regenerados: out };
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

  // ---------------------------------------------------------------- opciones del proyecto (/settings)
  await check("opciones del proyecto: logo y audio de estudio activos, aire OPT-IN (air_user solo al moverlo), todos los idiomas", async () => {
    return await page.evaluate(async () => {
      const s = await (await fetch("/settings")).json();
      const langsOff = [...document.querySelectorAll("#segLang button")].filter(b => b.disabled).map(b => b.dataset.v);
      const want = (s.pro_langs || []).filter(l => !FXCore.PRO_LANGS.includes(l));
      if (want.length) throw new Error(`idiomas del servidor sin habilitar: ${want}`);
      if (langsOff.some(l => (s.pro_langs || []).includes(l))) throw new Error(`idiomas deshabilitados: ${langsOff}`);
      if ((s.pro_opts || []).includes("logo") && document.querySelector("#logoOn").disabled) throw new Error("logo deshabilitado aunque el render pro lo aplica");
      if ((s.pro_opts || []).includes("enhance_audio") && document.querySelector("#enhance").disabled) throw new Error("audio de estudio deshabilitado");
      const airOpt = (s.pro_opts_optin || []).includes("air");
      const airUser0 = !!FX.engineOpts().air_user;
      if (airOpt && !FX.airUser && airUser0) throw new Error("se manda air_user sin que el usuario moviera el aire");
      if (airOpt && document.querySelector("#air").disabled) throw new Error("el control de aire quedo deshabilitado");
      return { proOpts: s.pro_opts, optin: s.pro_opts_optin, idiomas: FXCore.PRO_LANGS, airUser: airUser0, nota: document.querySelector("#airNote").textContent.slice(0, 60) };
    });
  });

  // ---------------------------------------------------------------- subtitulos del render y proxy
  await check("subtitulos en vivo con los grupos REALES del export (/captions) y video por proxy 720p", async () => {
    await page.waitForFunction(() => FX.captionSource() === "render", null, { timeout: 60000 });
    // el proxy de un proyecto recien compuesto tarda unos segundos: el editor cambia a el solo
    await page.waitForFunction(() => FX.srcState() === "ready" && /proxy=1/.test(V.currentSrc) && V.readyState >= 1, null, { timeout: 180000 });
    const r = await page.evaluate(async () => {
      const srv = await (await fetch(`/captions/${JOB}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan: FX.planForServer(), fmt: FMT }) })).json();
      const loc = FX.captionGroupsNow() || [];
      return { n: loc.length, same: JSON.stringify(loc.map(g => [g.text, g.start])) === JSON.stringify(srv.map(g => [g.text, g.start])), src: V.currentSrc, proxy: FX.srcState() };
    });
    if (!r.same || !r.n) throw new Error(`grupos distintos al render: ${JSON.stringify(r)}`);
    if (!/proxy=1/.test(r.src)) throw new Error(`el editor no usa el proxy: ${r.src}`);
    // seeks: 6 saltos, tiempo hasta 'seeked'
    const seek = await page.evaluate(async () => {
      const ts = [3.1, 11.7, 1.2, 19.4, 7.3, 15.9].map(t => Math.min(t, PLAN.duration - 0.2)), out = [];
      V.pause(); FX.setExact(false);
      for (const t of ts) {
        const t0 = performance.now();
        // con tope: un video que no carga (p. ej. 416 por cambiar de archivo bajo la misma URL) no cuelga la prueba
        const ok = await Promise.race([new Promise(r => { V.addEventListener("seeked", () => r(true), { once: true }); V.currentTime = t; }), new Promise(r => setTimeout(() => r(false), 15000))]);
        if (!ok) return { error: `seek a ${t} sin 'seeked' en 15 s (readyState ${V.readyState}, error ${V.error && V.error.code}, src ${V.currentSrc})` };
        out.push(Math.round(performance.now() - t0));
      }
      FX.setExact(true);
      return out;
    });
    if (seek.error) throw new Error(seek.error);
    // geometria: el ancho de 3 grupos en vivo = el `rect` del export (calibrado, +-1 %) y el port
    // SIN calibrar, con la fuente real del navegador, dentro de +-5 % (el critico midio -29 %)
    const par = await page.evaluate(async () => {
      const gs = (FX.captionGroupsNow() || []).filter(g => g.rect && g.end - g.start > 0.5 && !FXCore.captionsHiddenAt(PLAN.fx, (g.start + g.end) / 2, 9 / 16)
        && FXCore.captionRangeAlpha(PLAN.fx.captions || {}, (g.start + g.end) / 2) > 0.99);
      const pick = [0.2, 0.5, 0.8].map(q => gs[Math.floor(gs.length * q)]).filter(Boolean);
      const out = [];
      V.pause(); FX.setExact(false);
      for (const g of pick) {
        const t = (g.start + g.end) / 2;
        V.currentTime = t;
        for (let i = 0; i < 40; i++) { const d = FX.captionDebug(t); if (d && d.fontReady) break; await new Promise(r => setTimeout(r, 150)); }
        out.push(Object.assign({ t: +t.toFixed(2) }, FX.captionDebug(t)));
      }
      return out;
    });
    if (!par.length) throw new Error("sin grupos de subtitulos visibles para medir");
    for (const d of par) {
      if (!d.srv || !d.rectW) throw new Error(`grupo sin rect del render: ${JSON.stringify(d)}`);
      if (Math.abs(d.liveW - d.rectW) > 0.01 * d.rectW) throw new Error(`ancho en vivo ${d.liveW} != export ${d.rectW}: ${JSON.stringify(d)}`);
      if (d.fontReady && Math.abs(d.portW - d.rectW) > 0.05 * d.rectW) throw new Error(`geometria sin calibrar ${d.portW} vs export ${d.rectW}: ${JSON.stringify(d)}`);
    }
    await page.waitForTimeout(500); await shot("13_subtitulos_vivo");
    await exactHash(par[par.length - 1].t); await shot("13_subtitulos_exacto");
    return { grupos: r.n, proxy: r.proxy, seek_ms: seek, geometria: par.map(d => ({ t: d.t, texto: d.text, lineas: d.lines, export: d.rectW, vivo: +d.liveW.toFixed(4), sin_calibrar: d.portW != null ? +d.portW.toFixed(4) : null, fuente: d.font, lista: d.fontReady })) };
  });

  // ---------------------------------------------------------------- perfil en el editor
  await check("perfil en el editor: pegar descripcion -> aplicar cambia rotulo y Seguir AL INSTANTE; deshacer vuelve (tambien en el servidor)", async () => {
    const before = await page.evaluate(() => ({ prof: PLAN.profile ? PLAN.profile.name : null, lt: PLAN.fx.graphics.filter(g => g.kind === "lower_third").map(g => g.title), undo: UNDO.length }));
    await page.evaluate(() => setPanelTab("proj"));
    await page.click("#profEdit");
    // otro nombre para ver el cambio aunque el trabajo ya traiga este perfil
    const d = await profileFromText(PROFILE_TEXT);
    await page.fill('#pfm [data-f="name"]', d.name + " Demo");
    await page.evaluate(() => { PF.draft().name = document.querySelector('#pfm [data-f="name"]').value; });
    const t0 = Date.now();
    await page.click('#pfm [data-a="apply"]');
    await page.waitForFunction(n => PLAN.profile && PLAN.profile.name === n, d.name + " Demo", { timeout: 30000 });
    const ms = Date.now() - t0;
    const after = await page.evaluate(() => ({ lt: PLAN.fx.graphics.filter(g => g.kind === "lower_third").map(g => g.title),
      follow: PLAN.fx.graphics.filter(g => g.kind === "cta" && g.variant === "follow").map(g => [g.title, g.handle, g.platform]), undo: UNDO.length, grupo: document.querySelector("#profBody").innerText.split("\n")[1] }));
    if (!after.lt.includes(d.name + " Demo")) throw new Error(`rotulo sin el nombre nuevo: ${after.lt}`);
    if (after.undo !== before.undo + 1) throw new Error(`pasos de deshacer: ${after.undo - before.undo}`);
    await page.evaluate(async () => { const t = PLAN.fx.graphics.find(g => g.kind === "lower_third"); if (t) { V.pause(); V.currentTime = t.start + 0.8; } await new Promise(r => setTimeout(r, 900)); });
    await shot("10_perfil_aplicado_vivo");
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press("ControlOrMeta+z");
    await page.evaluate(async () => { await FX.saveNow(); await new Promise(r => setTimeout(r, 800)); await FX.saveNow(); });
    const srv = await (await page.request.get(`${base}/plan/${JOB}`)).json();
    const back = await page.evaluate(() => ({ prof: PLAN.profile ? PLAN.profile.name : null }));
    if (back.prof !== before.prof || ((srv.plan.profile || {}).name || null) !== before.prof) throw new Error(`deshacer: local ${back.prof}, servidor ${(srv.plan.profile || {}).name}, antes ${before.prof}`);
    return { entendido: d, aplicar_ms: ms, rotulo: after.lt, seguir: after.follow, restaurado: back.prof };
  });
  await check("perfil: 'Usar estos datos' con OTRA persona reemplaza TODO (ni enlaces, ni estrella, ni telefono del anterior); 'Era la misma' completa", async () => {
    await page.evaluate(() => { setPanelTab("proj"); });
    await page.click("#profEdit");
    await page.waitForSelector("#pfm.on");
    const before = await page.evaluate(() => { const d = PF.draft(); return { name: d.name, links: (d.links || []).map(l => String(l.handle || l.url || "").toLowerCase()), phone: d.phone || d.whatsapp || "" }; });
    await page.evaluate(() => { document.querySelector("#pfm details.pfparse").open = true; });
    await page.fill('#pfm [data-a="ptext"]', OTHER_TEXT);
    await page.click('#pfm [data-a="parse"]');
    await page.waitForSelector("#pfm .pfres", { timeout: 120000 });
    await page.click('#pfm [data-a="use"]');
    const d = await page.evaluate(() => { const d = PF.draft(); return { name: d.name, title: d.title, phone: d.phone || "", whatsapp: d.whatsapp || "", avatar: !!d.avatar,
      links: (d.links || []).map(l => ({ p: l.platform, h: String(l.handle || l.url || "").toLowerCase(), primary: !!l.primary })), msg: document.querySelector('#pfm [data-a="msg"]').innerText,
      completar: !!document.querySelector('#pfm [data-a="undoReplace"]') }; });
    await shot("10b_perfil_otra_persona");
    if (!/pedro/i.test(d.name || "")) throw new Error(`nombre: ${d.name}`);
    const ajenos = d.links.filter(l => before.links.includes(l.h));
    if (ajenos.length) throw new Error(`quedaron enlaces de ${before.name}: ${JSON.stringify(ajenos)}`);
    if (!d.links.some(l => l.primary && /pedro/.test(l.h))) throw new Error(`la estrella no es de la red de Pedro: ${JSON.stringify(d.links)}`);
    if (d.phone || d.whatsapp || d.avatar) throw new Error(`quedaron datos del anterior: ${JSON.stringify(d)}`);
    if (!d.completar) throw new Error("sin la opcion 'Era la misma: completar'");
    // "Era la misma": vuelve el perfil anterior completado (sus enlaces + los nuevos)
    await page.click('#pfm [data-a="undoReplace"]');
    const merged = await page.evaluate(() => { const d = PF.draft(); return { name: d.name, links: (d.links || []).map(l => String(l.handle || l.url || "").toLowerCase()), star: (d.links || []).filter(l => l.primary).map(l => l.handle) }; });
    await page.evaluate(() => PF.close());
    const kept = before.links.filter(h => merged.links.includes(h)).length;
    if (!kept && before.links.length) throw new Error(`'completar' perdio los enlaces anteriores: ${JSON.stringify(merged)}`);
    return { antes: before.name, otra: { nombre: d.name, enlaces: d.links }, completado: merged };
  });

  // ---------------------------------------------------------------- PiP propio
  await check("medio propio: imagen como PiP con colocacion automatica lejos de la cara (vivo = contrato del render) y en el cuadro exacto", async () => {
    const t = await page.evaluate(() => { const sh = PLAN.shots.find(s => s.face && s.end - s.start > 3) || PLAN.shots[0]; return +(sh.start + 1).toFixed(2); });
    const h0 = await exactHash(t + 0.6);
    const r = await page.evaluate(async t => {
      const cv = document.createElement("canvas"); cv.width = 800; cv.height = 1000; const x = cv.getContext("2d");
      const g = x.createLinearGradient(0, 0, 800, 1000); g.addColorStop(0, "#2EC4B6"); g.addColorStop(1, "#FF9F1C"); x.fillStyle = g; x.fillRect(0, 0, 800, 1000);
      x.fillStyle = "#fff"; x.font = "bold 90px sans-serif"; x.fillText("DEMO", 250, 520);
      const blob = await new Promise(r => cv.toBlob(r, "image/jpeg", 0.9));
      const it = await FX.addMedia("graphics", "media", new File([blob], "demo_pip.jpg", { type: "image/jpeg" }), t);
      const pl = FX.pipPlace(it), face = FXCore.faceMedian(PLAN.shots, it.start, it.end);
      const [w, h] = FXCore.pipCard(pl.size, null, 800, 1000, 1080);
      const r = [pl.x - w / 2 / 1080, pl.y - h / 2 / 1920, pl.x + w / 2 / 1080, pl.y + h / 2 / 1920];
      const z = face ? [face[0] - face[2] / 2, face[1] - face[3] / 2, face[0] + face[2] / 2, face[1] + face[3] / 2] : null;
      const inter = z ? Math.max(0, Math.min(r[2], z[2]) - Math.max(r[0], z[0])) * Math.max(0, Math.min(r[3], z[3]) - Math.max(r[1], z[1])) : 0;
      return { id: it.id, sid: it.media.sid, xy: [it.x, it.y], place: pl, face, inter };
    }, t);
    if (r.xy[0] != null || r.xy[1] != null) throw new Error("el PiP nuevo no nacio automatico (x/y vacios)");
    if (r.inter > 1e-6) throw new Error(`el PiP tapa la cara: ${JSON.stringify(r)}`);
    await page.waitForTimeout(600);
    const h1 = await exactHash(t + 0.6);
    await shot("11_pip_exacto");
    await page.evaluate(() => FX.setExact(false)); await page.waitForTimeout(700); await shot("11_pip_vivo"); await page.evaluate(() => FX.setExact(true));
    if (h1 === h0) throw new Error("el cuadro exacto no muestra el medio");
    await page.evaluate(() => { document.activeElement && document.activeElement.blur(); });
    await page.keyboard.press("ControlOrMeta+z");
    const gone = await page.evaluate(id => !PLAN.fx.graphics.some(g => g.id === id), r.id);
    if (!gone) throw new Error("deshacer no quito el PiP");
    return { lugar: r.place, cara: r.face, exacto: [h0, h1] };
  });

  // ---------------------------------------------------------------- idiomas
  await check("traducir a PT: subtitulos y titulos en un paso de deshacer; lo editado a mano se respeta; 'volver' recupera TODAS las lineas; aviso de idiomas mezclados junto a Exportar", async () => {
    const r = await page.evaluate(async () => {
      const C = FXCore, snap = () => JSON.stringify(C.TRACKS.map(t => (PLAN.fx[t] || []).map(i => [i.id, i.text, i.lines, i.title, i.subtitle, i.label, i.text && i.text.text])));
      const orig = snap();
      const t0 = performance.now();
      await setLang("pt");
      const t1 = performance.now();
      if (LANG !== "pt") return { error: "no cambio el idioma" };
      const src = C.fxTexts(PLAN.fx).map(x => x.text);
      const u0 = UNDO.length;
      await FX.translateFx();
      const t2 = performance.now();
      const now = C.fxTexts(PLAN.fx).map(x => x.text);
      const st = PLAN.studio && PLAN.studio.texts;
      const res = { subs_ms: Math.round(t1 - t0), textos_ms: Math.round(t2 - t1), frases: (PLAN.phrases_pt || []).length,
        cambiaron: now.filter((x, i) => x !== src[i]).length, total: now.length, undo: UNDO.length - u0, ejemplo: now[0], lineas_ok: now.length === src.length,
        sin_traducir: st && st.untranslated ? st.untranslated.length : null };
      // el usuario corrige a mano un titulo traducido de 2+ lineas y lo deja en UNA
      const it = PLAN.fx.text.find(t => st && st.out && st.out[t.id] && Array.isArray(t.lines) && t.lines.length >= 2) || PLAN.fx.text.find(t => st && st.out && st.out[t.id]);
      if (it) {
        const nl = (it.lines || []).length;
        FX.edit(() => { C.setTitleText(it, "corregido a mano"); });
        res.editado = { id: it.id, lineas_antes: nl, nota: document.querySelector("#langNote").innerText.replace(/\s+/g, " ").trim() };
        await FX.translateFx();                                   // re-traducir: lo editado se respeta
        res.editado.respetado = C.titleText(it) === "corregido a mano";
      }
      // idiomas mezclados: subtitulos en espanol con titulos en portugues -> aviso junto a Exportar
      await setLang("es");
      res.aviso = document.querySelector("#langWarn.on") ? document.querySelector("#langWarn").innerText.trim() : null;
      FX.restoreFxTexts();
      res.aviso_despues = !!document.querySelector("#langWarn.on");
      res.vuelta = snap() === orig;
      return res;
    });
    if (r.error) throw new Error(r.error);
    if (!r.frases) throw new Error("sin subtitulos en PT");
    if (!NO_LLM && (r.cambiaron < Math.min(3, r.total) || r.undo !== 1 || !r.lineas_ok)) throw new Error(JSON.stringify(r));
    if (r.editado && (!/editado a mano/.test(r.editado.nota) || /siguen en/.test(r.editado.nota) || !r.editado.respetado)) throw new Error(`edicion a mano: ${JSON.stringify(r.editado)}`);
    if (!NO_LLM && (!r.aviso || r.aviso_despues)) throw new Error(`aviso de idiomas mezclados: ${JSON.stringify(r)}`);
    if (!r.vuelta) throw new Error("no volvieron los textos originales completos (con todas sus lineas)");
    return r;
  });

  // ---------------------------------------------------------------- recargar conserva idioma y logo
  await check("recargar la pagina conserva el idioma elegido (plan.studio.lang) y el logo del proyecto", async () => {
    await page.evaluate(async () => { await setLang("pt"); document.querySelector("#logoOn").checked = true; document.querySelector("#logoOn").dispatchEvent(new Event("change")); await FX.saveNow(); });
    // el servidor guarda las opciones del proyecto con el cuadro exacto (/frame lleva el logo)
    await exactHash(await page.evaluate(() => Math.min(PLAN.duration - 0.5, 4.4)));
    await page.evaluate(async () => { await FX.saveNow(); });
    await page.reload();
    await page.waitForSelector("#s-editor.on", { timeout: 120000 });
    await page.waitForFunction(() => FX.isPro() && document.querySelectorAll(".fxrow").length === 7, null, { timeout: 60000 });
    const r = await page.evaluate(() => ({ lang: LANG, seg: document.querySelector("#segLang button.on, #segLang button.sel") ? document.querySelector("#segLang button.on, #segLang button.sel").dataset.v : null,
      subs: FX.planForServer().phrases === PLAN.phrases_pt, logo: LOGO_ON, check: document.querySelector("#logoOn").checked, aviso: !!document.querySelector("#langWarn.on") }));
    await page.evaluate(async () => { await setLang("es"); document.querySelector("#logoOn").checked = false; document.querySelector("#logoOn").dispatchEvent(new Event("change")); await FX.saveNow(); });
    await exactHash(await page.evaluate(() => Math.min(PLAN.duration - 0.5, 4.6)));
    if (r.lang !== "pt" || !r.subs) throw new Error(`idioma tras recargar: ${JSON.stringify(r)}`);
    if (!r.logo || !r.check) throw new Error(`logo tras recargar: ${JSON.stringify(r)}`);
    if (r.aviso) throw new Error("aviso de idiomas mezclados sin titulos traducidos");
    return r;
  });

  // ---------------------------------------------------------------- musica IA y vista previa que se actualiza
  /** "Otra variacion" (semilla nueva) hasta que la pista IA de la propuesta no este en cache: asi la
      prueba siempre recorre la generacion y la sincronia automatica (nunca se la salta). */
  async function variantWithNewMusic() {
    for (let i = 0; i < 8; i++) {
      const rev0 = await page.evaluate(() => FX.serverRev);
      const k0 = reqs.length;
      await page.click("#dirVar");
      await page.waitForFunction(r => !FX.busy() && FX.serverRev !== r, rev0, { timeout: 120000 });
      if (await page.evaluate(() => FX.musicSide.state === "generating")) return k0;
    }
    throw new Error("8 variaciones seguidas con la pista ya en cache");
  }
  /** Espera la sincronia AUTOMATICA con la musica IA: estado synced, el plan del Studio es la version
      sincronizada (rejilla de beats, sin cambios pendientes), sin aviso ni boton de "Sincronizar", y
      ningun PUT /plan desde el /direct (quitar algo de la propuesta haria que no fuera automatica). */
  async function autoSynced(k0, label) {
    // el aviso de "componiendo" lleva un spinner redondo (no una barra estirada)
    const sp = await page.evaluate(() => { const e = document.querySelector("#dirMusic .spinner"); if (!e) return null; const b = e.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]; });
    await page.waitForFunction(() => ["synced", "superseded", "error", "canceled"].includes(FX.musicSide.state) || FX.musicSide.resync === "available", null, { timeout: 300000 });
    await page.waitForFunction(() => FX.musicSide.state !== "synced" || (FX.isClean() && FX.gridSynced()), null, { timeout: 20000 }).catch(() => {});
    const m = await page.evaluate(() => ({ estado: FX.musicSide.state, resync: FX.musicSide.resync, mensaje: FX.musicSide.message, grid: !!(PLAN.director && PLAN.director.music_grid), limpio: FX.isClean(),
      boton: !!document.querySelector("#dirResync"), aviso: (document.querySelector("#dirMusic") || {}).innerText || "", rev: FX.serverRev, bpm: PLAN.director && PLAN.director.music_grid ? PLAN.director.music_grid.bpm : null }));
    const puts = reqs.slice(k0).filter(r => /^PUT \/plan\//.test(r)).length;
    const out = Object.assign({ caso: label, spinner: sp, puts }, m);
    if (sp && (sp[0] > 20 || sp[1] > 20 || Math.abs(sp[0] - sp[1]) > 2)) throw new Error(`spinner deformado ${sp}: ${JSON.stringify(out)}`);
    // el SERVIDOR no alcanzo a sincronizar (su re-direccion con la pista no trajo rejilla a tiempo):
    // no es del Studio (nada cambio el plan); se reintenta con otra variacion y queda anotado
    if (m.estado === "ready" && m.resync === "available" && !puts && m.limpio && /sin sincron/i.test(m.mensaje || "")) return Object.assign(out, { reintentar: true });
    if (m.estado !== "synced" || !m.grid || !m.limpio || m.boton || puts) throw new Error(`${label}: sin sincronia automatica ${JSON.stringify(out)}`);
    return out;
  }
  /** Variacion nueva + sincronia automatica (hasta 2 intentos si el servidor no alcanzo a sincronizar). */
  async function variantAutoSynced(label, before) {
    const intentos = [];
    for (let i = 0; i < 2; i++) {
      const k0 = await variantWithNewMusic();
      if (before) await before();
      const r = await autoSynced(k0, label);
      intentos.push(r);
      if (!r.reintentar) return Object.assign(r, { intentos: intentos.length, fallos_servidor: intentos.filter(x => x.reintentar).map(x => x.mensaje) });
    }
    throw new Error(`${label}: el servidor no sincronizo en 2 intentos ${JSON.stringify(intentos)}`);
  }
  await check("musica IA SIN ediciones: 'Otra variacion' -> la pista se compone en segundo plano -> la edicion se sincroniza SOLA (sin boton ni PUT); la vista previa real se actualiza con ella", async () => {
    // sin nada bloqueado ni editado desde la propuesta
    await page.evaluate(async () => { setPanelTab("proj"); if (FXCore.lockedIds(PLAN.fx).length) FX.edit(() => FXCore.TRACKS.forEach(t => (PLAN.fx[t] || []).forEach(i => { i.locked = false; }))); await FX.saveNow(); });
    const out = {};
    // vista previa real mientras la musica IA se compone: suena la provisional y se actualiza sola
    out.musica = await variantAutoSynced("sin ediciones", async () => { if (!out.msg) await previewRefresh(out); });
    await shot("12b_musica_sincronizada_sola");
    return out;
  });
  async function previewRefresh(out) {
    await page.click("#btnReal");
    await page.waitForSelector("#pvmVid.on", { timeout: 900000 });
    const f1 = await page.evaluate(() => document.querySelector("#pvmVid").src);
    out.provisional = await page.textContent("#pvmMsg");
    await shot("12_vista_previa_provisional");
    const t0 = Date.now();
    await page.waitForFunction(f => document.querySelector("#pvmVid").src !== f || /provisional \(/.test(document.querySelector("#pvmMsg").textContent), f1, { timeout: 600000 });
    const f2 = await page.evaluate(() => document.querySelector("#pvmVid").src);
    out.actualizada = f2 !== f1; out.msg = await page.textContent("#pvmMsg"); out.refresco_s = Math.round((Date.now() - t0) / 1000);
    await shot("12_vista_previa_con_musica_ia");
    await page.click("#pvmClose");
    if (!out.actualizada) throw new Error(`la vista previa no se actualizo con la musica IA: ${out.msg}`);
  }
  await check("musica IA con el titulo del hook BLOQUEADO: la edicion tambien se sincroniza SOLA (el director re-emite su fondo y el Studio no lo toca)", async () => {
    const hook = await page.evaluate(async () => {
      const t = PLAN.fx.text.filter(x => x.enabled !== false && x.preset !== "counter" && FXCore.titleText(x).trim()).sort((a, b) => a.start - b.start)[0];
      FX.select("text", t.id); if (!t.locked) FX.action("lock"); await FX.saveNow(); return { id: t.id, texto: FXCore.titleText(t) };
    });
    const out = await variantAutoSynced("hook bloqueado");
    const st = await page.evaluate(id => { const t = PLAN.fx.text.find(x => x.id === id); return { locked: !!(t && t.locked), bg: PLAN.fx.effects.filter(e => /^fx_hookbg_/.test(e.id)).map(e => e.id) }; }, hook.id);
    if (!st.locked) throw new Error("el titulo bloqueado se perdio");
    await page.evaluate(async id => { const t = PLAN.fx.text.find(x => x.id === id); if (t && t.locked) { FX.select("text", id); FX.action("lock"); } await FX.saveNow(); }, hook.id);
    return Object.assign(out, { hook: hook.texto, fondos_hook: st.bg });
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
    await check("export con etapas y resultados (video completo + shorts) y avisos del render en lenguaje humano con 'Ver'", async () => {
      // un SFX con un nombre que no existe: el export lo omite y lo avisa (sfx:<id>:nombre_desconocido)
      const bad = await page.evaluate(() => { let id; FX.edit(() => { const it = { id: "sf_smoke_x", name: "sonido_que_no_existe", at: 1.5, gain_db: -8, enabled: true, src: "user", locked: false }; PLAN.fx.sfx.push(it); id = it.id; }); return id; });
      // el Studio ya lo marca antes de exportar: rombo rojo en la pista y aviso en el inspector
      const ui = await page.evaluate(async id => { FX.select("sfx", id); setPanelTab("elem"); await new Promise(r => setTimeout(r, 200));
        return { pista: !!document.querySelector(`[data-fx="sfx:${id}"].warn`), inspector: !!document.querySelector("#sfxUnknown") }; }, bad);
      if (!ui.pista || !ui.inspector) throw new Error(`SFX desconocido sin marcar en el Studio: ${JSON.stringify(ui)}`);
      await page.evaluate(() => { FX.select(null); setPanelTab("proj"); });
      await page.evaluate(() => FX.saveNow());
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
      const warns = await page.$$eval("#resWarn .wrow", ws => ws.map(w => w.innerText.replace(/\s+/g, " ").trim()));
      await shot("08_resultados");
      if (!warns.some(w => /no existe en el catálogo/.test(w))) throw new Error(`sin aviso humano del SFX omitido: ${warns}`);
      await page.click(`#resWarn [data-go="sfx:${bad}"]`);
      await page.waitForSelector("#s-editor.on");
      const sel = await page.evaluate(() => FX.selection().map(s => s.id));
      if (!sel.includes(bad)) throw new Error(`'Ver' no selecciono el SFX: ${sel}`);
      await page.evaluate(() => FX.action("del"));
      return { clips, etapas: [...seen], avisos: warns, marcado: ui, s: Math.round((Date.now() - t0) / 1000) };
    });
  }
  await check("'Nuevo video' guarda la ultima edicion y 'Proyectos recientes' reabre el proyecto (con tema y miniatura)", async () => {
    const v = await page.evaluate(() => { const it = PLAN.fx.text[0]; const v = +(0.05 + Math.random() * 0.1).toFixed(4); FX.edit(() => { it.size = v; }); return v; });
    await page.click("#cancel");                              // enseguida: sin esperar el autoguardado
    await page.waitForSelector("#s-setup.on");
    const saved = await (await page.request.get(new URL(`/plan/${JOB}`, url).href)).json();
    if (Math.abs(saved.plan.fx.text[0].size - v) > 1e-6) throw new Error(`no se guardo: ${saved.plan.fx.text[0].size} != ${v}`);
    await page.waitForSelector(`#recent .rrow[data-id="${JOB}"]`, { timeout: 20000 });
    const row = await page.evaluate(id => { const r = document.querySelector(`#recent .rrow[data-id="${id}"]`); return { txt: r.innerText.split("\n").slice(0, 2).join(" | "), thumb: !!r.querySelector("img.rth") }; }, JOB);
    await shot("09_recientes");
    await page.click(`#recent .rrow[data-id="${JOB}"]`);
    await page.waitForSelector("#s-editor.on", { timeout: 60000 });
    return { size: v, reabierto: await page.evaluate(() => JOB), fila: row };
  });
  if (pageErrors.length) fail("consola sin errores de JS", pageErrors.slice(0, 5));
  else ok("consola sin errores de JS");
  if (badResp.length) fail("ninguna respuesta HTTP >= 400 inesperada", badResp.slice(0, 10));
  else ok("ninguna respuesta HTTP >= 400 inesperada");
} finally {
  await browser.close();
}
const res = { ok: steps.every(s => s.ok), job: JOB, steps, out: OUT };
fs.writeFileSync(path.join(OUT, "resumen.json"), JSON.stringify(res, null, 2));
console.log(JSON.stringify({ ok: res.ok, out: OUT }));
process.exit(res.ok ? 0 : 1);
