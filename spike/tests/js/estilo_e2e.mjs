// E2E de la pestaña Estilo en un navegador real (Playwright + Chrome), con raton y teclado de verdad.
// uso: REELFY_PLAYWRIGHT=<ruta a playwright-core> node tests/js/estilo_e2e.mjs <url base> <job> [capturas]
// El servidor (tests/devserver.sh) sirve un proyecto SINTETICO (tests/test_estilo.py::_plan_estilo):
// video 540x960 de 12 s, toma A con la franja de abajo clara y toma B oscura, marca verde ficticia
// #1F9D55, un titulo con palabras destacadas, un rotulo y un boton.
// Revisa, a 1440x900 y 1200x760:
//  A. El caso de Kevin medido en pixeles: tecla 3 -> Estilo; la tarjeta «Pastilla de color» se ve sin
//     desplazarse y ya anima la pastilla en su color; UN clic la elige (un paso de deshacer) y su fila
//     «Color de la pastilla» queda a la vista; quitar «Igual al color de tu marca» y elegir rojo; el
//     color de la marca recolorea titulo, tarjeta y boton sin tocar la pastilla roja; volver a la
//     marca; la pastilla SE DESLIZA (centro entre dos palabras); ⌘Z / ⇧⌘Z; el cuadro exacto del motor
//     trae la pastilla; clic en los subtitulos -> el inspector compacto comparte el estado.
//  B. Claridad: cada control con nombre y que hace, ejemplo donde aplica, sin decimales crudos ni
//     unidades tecnicas en Basico, sin ingles ni jerga, sin emojis, texto >= 11 px y ayuda AA, Basico corto.
//  C. Ningun control muerto: cada control de Basico (Subtitulos y Color de tu marca), en cada estilo,
//     cambia la vista (o esta apagado y su fila dice por que).
//  D. Estilos de color: las seis fotos del motor con el cuadro del usuario (en menos de 1 s), mantener
//     presionado muestra el original, «Comparar con el original» con la linea arrastrada al 30 %, el
//     color dibujado en el navegador (LookGL) igual a la foto del motor (<= 6/255) y el Brillo visible
//     MIENTRAS el video corre en Edición.
//  E. Escuchar: «Escuchar» de un ambiente suena (y avanza), un segundo clic lo para, empezar otro para
//     el primero, la voz antes y despues, la musica creada; pasar el mouse por un sonido de «Agregar» lo suena.
//  F. Simetria medida (margenes, tarjetas, opciones, columnas, centro de la letra) y sin desborde, en
//     las siete secciones.
//  Fase 3: Edición con IA (un clic aplica el estilo, un paso de deshacer, el ejemplo con el conteo real),
//     Música (origen, ambiente, la musica de tu video; claridad en Basico y Avanzado), Quién sale y tu
//     logo (sin datos: «Dónde aparece» apagado y dice por que; el logo en su ejemplo) y Formato (un clic
//     cambia el formato del proyecto y la vista grande). El panel Proyecto ya no tiene esos controles.
// Abrir Estilo no cambia el plan. Consola sin errores. Sale con 1 al primer fallo.
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { HELPERS, LINT } from "./e2e_claridad.mjs";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("define REELFY_PLAYWRIGHT (ruta a playwright-core)"); process.exit(2); }
const { chromium } = require(PW);
const BASE = (process.argv[2] || "http://127.0.0.1:8932").replace(/\/$/, "");
const JOB = process.argv[3] || "estilo_demo";
const OUT = process.argv[4] || null;
if (OUT) fs.mkdirSync(OUT, { recursive: true });

const GREEN = "#1F9D55", RED = "#FF5A3C", YELLOW = "#FFD23F";
const log = (...a) => console.log("·", ...a);
const report = { viewports: {} };

// ayudas dentro de la pagina (window.__e2e) y el lint de claridad (B): tests/js/e2e_claridad.mjs

// ------------------------------------------------------------------ flujo
const browser = await chromium.launch({ headless: true, channel: process.env.REELFY_PW_CHANNEL || "chrome" });
let exitCode = 0;
try {
  for (const [VW, VH] of [[1440, 900], [1200, 760]]) {
    const tag = `${VW}x${VH}`, rep = report.viewports[tag] = {};
    const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const errs = [];
    page.on("pageerror", e => errs.push("pageerror " + e.message));
    page.on("console", m => { if (m.type() === "error" && !/status of 404|status of 50\d|status of 4\d\d/.test(m.text())) errs.push("console " + m.text()); });
    page.on("dialog", d => { errs.push("dialog " + d.message()); d.dismiss(); });
    const ev = (fn, arg) => page.evaluate(fn, arg);
    const shot = async n => { if (OUT) await page.screenshot({ path: path.join(OUT, `${n}_${tag}.png`) }); };
    const wait = ms => page.waitForTimeout(ms);
    const poll = async (fn, arg, ms = 1500, step = 100) => { const t0 = Date.now(); let v; do { v = await ev(fn, arg); if (v) return v; await wait(step); } while (Date.now() - t0 < ms); return v; };
    const blur = () => ev(() => { if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur(); });

    await page.goto(`${BASE}/?job=${JOB}`);
    await page.waitForFunction(() => window.FX && FX.isPro && FX.isPro() && FX.uiCopy && FX.uiCopy() && document.querySelector("#s-editor.on"), null, { timeout: 90000 });
    await ev(HELPERS);
    // mismo punto de partida en cada tamano (el plan se guarda en el servidor entre corridas)
    await ev(({ G }) => {
      FX.edit(() => { Object.assign(PLAN.fx.captions, { enabled: true, preset: "clean_bold", highlight: null, color: "#FFFFFF", case: "none", y: 0.72, size: 0.052 });
        Object.assign(PLAN.fx.grade, { enabled: true, look: "natural", match_shots: false, exposure: 0, temperature: 0, vignette: 0, grain: 0, sharpen: 0 }); });
      FX.setAccent(G);
      UNDO.length = 0; REDO.length = 0;
    }, { G: GREEN });
    await ev(() => FX.clean.setMode("edit"));
    await wait(400);
    await blur();

    // A.1 tecla 3 -> Estilo, Subtitulos primero; abrir Estilo no cambia el plan
    const plan0 = await ev(() => JSON.stringify(PLAN));
    await page.keyboard.press("3");
    await page.waitForFunction(() => document.querySelector("#stylePane") && getComputedStyle(document.querySelector("#stylePane")).display !== "none" && document.querySelector("#stCaps .gal"), null, { timeout: 15000 });
    assert.equal(await ev(() => [...document.querySelectorAll("#stCol .st-sec")].find(s => !s.hidden).id), "stCaps");
    await page.waitForFunction(() => Estilo.Frames.img && Estilo.Frames.win && Estilo.Frames.win.words.length >= 6, null, { timeout: 20000 });
    await wait(1200);
    assert.equal(await ev(() => JSON.stringify(PLAN)), plan0, "abrir Estilo no cambia el plan");
    assert.equal(await ev(() => UNDO.length), 0);
    log(tag, "A.1 Estilo con la tecla 3, Subtítulos primero, sin cambiar el plan");

    // A.2 la tarjeta de la pastilla se ve sin desplazarse y ya anima la pastilla en el color de la marca
    const card = '#stCaps .gcard[data-k="boxed"]';
    const cr = await ev(s => __e2e.rect(s), card), col = await ev(() => __e2e.rect("#stCol"));
    assert.ok(cr.y >= col.y - 0.5 && cr.b <= Math.min(VH, col.b) + 0.5 && cr.x >= 0 && cr.r <= VW, `tarjeta visible: ${JSON.stringify(cr)}`);
    const g0 = await poll(({ s, G }) => __e2e.count(s, G) >= 200, { s: card + " canvas", G: GREEN }, 1500);
    assert.ok(g0, "la tarjeta anima la pastilla verde en 1.5 s");
    const lumB = await ev(s => __e2e.luma(s, [0, 0.72, 1, 1], 200), card + " canvas");
    assert.ok(lumB < 90, `la tarjeta usa la toma oscura (luma ${lumB.toFixed(1)})`);
    rep.cardLuma = +lumB.toFixed(1);
    log(tag, "A.2 tarjeta visible, pastilla verde animada, toma oscura");

    // A.3 UN clic: estilo, seleccion neutra, filas de color, fila a la vista, un paso de deshacer
    const u0 = await ev(() => UNDO.length);
    await page.click(card);
    await wait(900);
    assert.equal(await ev(() => PLAN.fx.captions.preset), "boxed");
    assert.equal(await ev(s => document.querySelector(s).getAttribute("aria-checked"), card), "true");
    assert.match(await ev(s => getComputedStyle(document.querySelector(s)).boxShadow, card), /215, 220, 230/);
    assert.deepEqual(await ev(() => __e2e.labels("#stCaps .hrow.cw")), ["Color de la pastilla", "Color del texto"]);
    const pr = await ev(() => __e2e.rect('#stCaps [data-setting="captions.highlight"]'));
    assert.ok(pr.y >= 0 && pr.b <= VH, `fila de la pastilla a la vista: ${JSON.stringify(pr)}`);
    assert.equal(await ev(() => UNDO.length), u0 + 1, "un clic = un paso de deshacer");
    // tiempo quieto con la pastilla en la segunda palabra
    await ev(() => Estilo.Ticker.setTime(Estilo.Frames.win.words[1].start + 0.06));
    await wait(300);
    const band = [0, 0.5, 1, 1];
    const gPv = await ev(({ G, b }) => __e2e.count("#stPvCv", G, 30, b), { G: GREEN, b: band });
    assert.ok(gPv >= 300, `pastilla verde en la vista grande (${gPv} px)`);
    await shot("A3_pastilla_elegida");
    log(tag, "A.3 un clic elige la pastilla; su color queda a la vista; un paso de deshacer");

    // A.4 quitar «Igual al color de tu marca» (sin cambio visible) y elegir rojo
    await page.click('#stCaps [data-setting="captions.highlight"] [data-a="same"]');
    await wait(300);
    assert.equal(await ev(() => PLAN.fx.captions.highlight), GREEN);
    const gPv2 = await ev(({ G, b }) => __e2e.count("#stPvCv", G, 30, b), { G: GREEN, b: band });
    assert.ok(Math.abs(gPv2 - gPv) < 40, `sin cambio visible (${gPv} -> ${gPv2})`);
    await page.click(`#stCaps [data-setting="captions.highlight"] .cw-sw[data-c="${RED}"]`);
    await wait(400);
    assert.equal(await ev(() => PLAN.fx.captions.highlight), RED);
    const rPv = await ev(({ R, b }) => __e2e.count("#stPvCv", R, 30, b), { R: RED, b: band });
    const gPv3 = await ev(({ G, b }) => __e2e.count("#stPvCv", G, 30, b), { G: GREEN, b: band });
    assert.ok(rPv >= 300 && gPv3 < 50, `pastilla roja (${rPv} rojo, ${gPv3} verde)`);
    assert.ok(await ev(({ s, R }) => __e2e.count(s, R) >= 60, { s: card + " canvas", R: RED }), "la tarjeta tambien se pinta de rojo");
    log(tag, "A.4 pastilla propia roja, en la vista y en la tarjeta");

    // A.5 Color de tu marca: amarillo en titulo, tarjeta y boton; la pastilla sigue roja
    const minis = ["title", "card", "btn"].map(k => `#stBrand .ap-mini[data-k="${k}"] canvas`);
    await page.click(`#stBrand .cw-sw[data-c="${YELLOW}"]`);
    await wait(700);
    assert.equal(await ev(() => PLAN.fx.accent), YELLOW);
    for (const m of minis) {
      const n = await poll(({ m, Y }) => __e2e.count(m, Y) >= 30, { m, Y: YELLOW }, 1500);
      assert.ok(n, `${m}: amarillo de la marca`);
    }
    const rPv2 = await ev(({ R, b }) => __e2e.count("#stPvCv", R, 30, b), { R: RED, b: band });
    assert.ok(rPv2 >= 300, `la pastilla sigue roja (${rPv2})`);
    await ev(() => document.querySelector("#stBrand").scrollIntoView({ block: "start" }));
    await wait(400);
    await shot("A5_color_de_tu_marca");
    log(tag, "A.5 la marca recolorea título, tarjeta y botón; la pastilla propia se queda");

    // A.6 volver a «Igual al color de tu marca»: la pastilla sigue a la marca (amarilla)
    await ev(() => document.querySelector('#stCaps [data-setting="captions.highlight"]').scrollIntoView({ block: "center" }));
    await wait(300);
    await page.click('#stCaps [data-setting="captions.highlight"] [data-a="same"]');
    await wait(400);
    assert.equal(await ev(() => PLAN.fx.captions.highlight), null);
    const yPv = await ev(({ Y, b }) => __e2e.count("#stPvCv", Y, 30, b), { Y: YELLOW, b: band });
    assert.ok(yPv >= 300, `pastilla amarilla (${yPv})`);
    log(tag, "A.6 la pastilla vuelve a seguir a la marca");

    // A.7 se DESLIZA: a 0.06 s de la tercera palabra el centro esta entre la segunda y la tercera
    const slide = await ev(() => {
      const gs = Estilo.Groups.get("boxed").groups.filter(g => g.words.length >= 3);
      const g = gs[0]; const t = +g.words[2].start + 0.06;
      Estilo.Ticker.setTime(t);
      return t;
    });
    await wait(300);
    const pi = await ev(() => Estilo.previewInfo());
    assert.ok(pi.pill && pi.words.length >= 3, JSON.stringify(pi).slice(0, 300));
    const c1 = pi.words[1].cx, c2 = pi.words[2].cx, pc = pi.pill.x + pi.pill.w / 2, dd = c2 - c1;
    assert.ok(pc > c1 + 0.15 * dd && pc < c2 - 0.15 * dd, `desliza: ${c1.toFixed(1)} < ${pc.toFixed(1)} < ${c2.toFixed(1)} (t ${slide})`);
    rep.slide = { c1: +c1.toFixed(1), pill: +pc.toFixed(1), c2: +c2.toFixed(1) };
    log(tag, "A.7 la pastilla se desliza entre palabras (no salta)");

    // A.8 ⌘Z / ⇧⌘Z: un paso por gesto
    await ev(() => Estilo.Ticker.setTime(Estilo.Frames.win.words[1].start + 0.06));
    await blur();
    await page.keyboard.press("Meta+z");
    await wait(500);
    assert.equal(await ev(() => PLAN.fx.captions.highlight), RED, "deshacer vuelve a la pastilla roja");
    assert.ok(await ev(({ R, b }) => __e2e.count("#stPvCv", R, 30, b) >= 300, { R: RED, b: band }), "la vista vuelve a rojo");
    await page.keyboard.press("Meta+Shift+z");
    await wait(500);
    assert.equal(await ev(() => PLAN.fx.captions.highlight), null, "rehacer vuelve a la marca");
    assert.ok(await ev(({ Y, b }) => __e2e.count("#stPvCv", Y, 30, b) >= 300, { Y: YELLOW, b: band }), "la vista vuelve a amarillo");
    log(tag, "A.8 ⌘Z y ⇧⌘Z, un paso por gesto");

    // A.8b quitar «Igual al color de tu marca» deja la pastilla PROPIA (del color de la marca de ahora):
    // cambiar la marca ya no la recolorea y la casilla sigue sin palomita; se guarda en el plan y ⌘Z la deshace
    await ev(() => document.querySelector('#stCaps [data-setting="captions.highlight"]').scrollIntoView({ block: "center" })); await wait(300);
    await page.click('#stCaps [data-setting="captions.highlight"] [data-a="same"]');
    await wait(300);
    const own0 = await ev(() => ({ h: PLAN.fx.captions.highlight, own: !!(PLAN.studio && PLAN.studio.pillOwn), acc: FXCore.projectAccent(PLAN) }));
    assert.ok(own0.h === own0.acc && own0.own, `pastilla propia del color de la marca ${JSON.stringify(own0)}`);
    await ev(() => document.querySelector("#stBrand").scrollIntoView({ block: "start" })); await wait(300);
    await page.click('#stBrand .cw-sw[data-c="#2EC4B6"]');
    await wait(500);
    const own1 = await ev(() => ({ h: PLAN.fx.captions.highlight, acc: PLAN.fx.accent, chk: document.querySelector('#stCaps [data-setting="captions.highlight"] [data-a="same"]').checked }));
    assert.ok(own1.acc === "#2EC4B6" && own1.h === own0.h && !own1.chk, `la marca nueva no recolorea la pastilla propia ${JSON.stringify(own1)}`);
    await blur(); await page.keyboard.press("Meta+z"); await wait(300); await page.keyboard.press("Meta+z"); await wait(400);
    const own2 = await ev(() => ({ h: PLAN.fx.captions.highlight, own: !!(PLAN.studio && PLAN.studio.pillOwn), acc: FXCore.projectAccent(PLAN) }));
    assert.ok(own2.h === null && !own2.own && own2.acc === own0.acc, `⌘Z deshace la marca y la pastilla propia ${JSON.stringify(own2)}`);
    log(tag, "A.8b pastilla propia: se guarda en el plan y la marca nueva no la recolorea");

    // teclado: las flechas mueven el foco en la galeria y Espacio elige (sin reproducir el video)
    await ev(() => document.querySelector("#stCol").scrollTo(0, 0));
    await page.focus(card);
    await page.keyboard.press("ArrowRight");
    assert.equal(await ev(() => document.activeElement.dataset.k), "minimal_small");
    await page.keyboard.press("Space");
    await wait(300);
    assert.equal(await ev(() => PLAN.fx.captions.preset), "minimal_small");
    assert.equal(await ev(() => V.paused), true, "Espacio no reproduce en Estilo");
    await blur(); await page.keyboard.press("Meta+z"); await wait(300);
    assert.equal(await ev(() => PLAN.fx.captions.preset), "boxed");
    log(tag, "teclado: flechas y Espacio en la galería");

    // F. simetria medida en Estilo
    await ev(() => document.querySelector("#stCol").scrollTo(0, 0));
    await wait(300);
    const sym = await ev(() => {
      const r = s => document.querySelector(s).getBoundingClientRect();
      const W = window.innerWidth, pv = r("#stPreview"), col = r("#stCol"), seg = r("#segMode"), mb = r("#modeBar");
      const cards = [...document.querySelectorAll("#stCaps .gcard")].map(c => c.getBoundingClientRect());
      const ws = cards.map(c => c.width), gaps = [cards[1].left - cards[0].right, cards[2].left - cards[1].right];
      const segs = [...document.querySelectorAll("#stCol .seg-h")].filter(s => s.offsetParent).map(s => { const b = [...s.children].map(c => c.getBoundingClientRect().width); return Math.max(...b) - Math.min(...b); });
      const secs = [...document.querySelectorAll("#stCol .st-body")].filter(s => !s.hidden).map(s => { const cs = getComputedStyle(s); return Math.abs(parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)); });
      const ap = [...document.querySelectorAll("#stBrand .ap-mini")].map(m => m.getBoundingClientRect().width);
      const lc = [...document.querySelectorAll("#stLook .lcard")].map(c => c.getBoundingClientRect()), lw = lc.map(c => c.width);
      const lgap = Math.abs((lc[1].left - lc[0].right) - (lc[2].left - lc[1].right));
      // Edicion con IA (2 x 2), Musica (opciones de 2 en 2), logo (2 columnas) y las opciones fijas movidas
      const dc = [...document.querySelectorAll("#stDirector .dcard")].map(c => c.getBoundingClientRect());
      const dw = dc.map(c => c.width), dgap = Math.abs((dc[1].left - dc[0].right) - (dc[3].left - dc[2].right)), dvgap = Math.abs((dc[2].top - dc[0].bottom) - (dc[3].top - dc[1].bottom));
      const mo = [...document.querySelectorAll("#stMusic .mopts.two .mopt:not(.wide)")].map(c => c.getBoundingClientRect().width);
      const lg = [...document.querySelectorAll("#brandLogoRow .logo-ctl > *")].map(c => c.getBoundingClientRect());
      const fixed = ["#segFmt", "#segLang", "#segLogo"].map(q => { const b = [...document.querySelectorAll(q + " button")].map(x => x.getBoundingClientRect().width); return Math.max(...b) - Math.min(...b); });
      return { margin: Math.abs(pv.left - (W - col.right)), mode: Math.abs((seg.left + seg.right) / 2 - (mb.left + mb.right) / 2), modeWin: Math.abs((seg.left + seg.right) / 2 - W / 2),
        cardW: Math.max(...ws) - Math.min(...ws), gap: Math.abs(gaps[0] - gaps[1]), seg: Math.max(0, ...segs), sec: Math.max(0, ...secs), ap: Math.max(...ap) - Math.min(...ap),
        lookW: Math.max(...lw) - Math.min(...lw), lookGap: lgap, dirW: Math.max(...dw) - Math.min(...dw), dirGap: dgap, dirVGap: dvgap,
        musW: mo.length ? Math.max(...mo) - Math.min(...mo) : 0, logoCols: Math.abs(lg[2].width - lg[3].width),
        fixedSeg: Math.max(...fixed), sections: [...document.querySelectorAll("#stCol .st-sec")].filter(x => !x.hidden).length,
        over: document.documentElement.scrollWidth - document.documentElement.clientWidth, colOver: document.querySelector("#stCol").scrollWidth - document.querySelector("#stCol").clientWidth };
    });
    rep.symmetry = sym;
    for (const k of ["margin", "mode", "modeWin", "cardW", "gap", "seg", "sec", "ap", "lookW", "lookGap", "dirW", "dirGap", "dirVGap", "musW", "fixedSeg", "logoCols"]) assert.ok(sym[k] <= 0.5, `simetría ${k}: ${sym[k]}`);
    assert.equal(sym.sections, 7, "las siete secciones de Estilo");
    assert.ok(sym.over <= 0 && sym.colOver <= 0, `sin desborde horizontal ${JSON.stringify(sym)}`);
    // la letra de los subtitulos centrada en la vista grande (Karaoke: la frase completa, sin crecer
    // la palabra que se dice)
    await ev(() => { Estilo.editCaps(c => { c.preset = "karaoke"; }); });
    await wait(300);
    // la frase completa a la vista (todas las palabras ya salieron y el grupo aun no se va)
    await ev(() => { const g = Estilo.Groups.get("karaoke").groups.find(x => x.words.length >= 3); Estilo.Ticker.setTime(Math.min(+g.start + 0.5, (+g.start + +g.end) / 2)); });
    await wait(300);
    const ink = await ev(() => __e2e.inkX("#stPvCv", 0.6, 0.86));
    const dInk = Math.abs(ink.l - (ink.w - 1 - ink.r)) / ink.w;
    rep.inkCenter = +(dInk * 100).toFixed(2);
    assert.ok(ink.r > ink.l && dInk <= 0.005 + 1 / ink.w, `letra centrada: ${JSON.stringify(ink)}`);
    await blur(); await page.keyboard.press("Meta+z"); await wait(300);
    assert.equal(await ev(() => PLAN.fx.captions.preset), "boxed");
    log(tag, "F simetría medida", JSON.stringify(sym), `centro de la letra ${rep.inkCenter} %`);

    // B. claridad en Estilo, en Basico y en Avanzado
    for (const lv of ["basic", "advanced"]) {
      await ev(l => Estilo.setLevel(l), lv);
      await wait(1500);
      const probs = await ev(LINT, { root: "#stCol", level: lv, chrome: ".lvlsw,.st-sh,.st-head" });
      assert.deepEqual(probs, [], `B Estilo (${lv}): ${probs.slice(0, 12).join(" | ")}`);
      if (lv === "basic") {
        const n = await ev(() => [...document.querySelectorAll("#stCaps .hrow[data-setting]")].filter(r => r.offsetParent && r.dataset.setting !== "captions.preset").length);
        assert.ok(n <= 6, `Básico corto: ${n} filas`);
        assert.ok(!(await ev(() => !!document.querySelector('#stCol [data-setting="captions.x"],#stCol [data-setting="captions.box"],#stCol [data-setting="captions.enabled"]'))), "sin Caja de fondo, Posición X ni Mostrar subtítulos");
      } else {
        await ev(() => document.querySelector('#stCaps [data-setting="captions.stroke"]').scrollIntoView({ block: "center" }));
        await wait(500);
        await shot("B_avanzado");
      }
    }
    await ev(() => Estilo.setLevel("basic"));
    await wait(500);
    log(tag, "B claridad en Estilo (Básico y Avanzado)");

    // Avanzado › Subtítulos: cada ejemplo muestra SU ajuste (opciones lado a lado, distintas entre si y
    // de las otras filas); lo que el motor ignora con un estilo (centrar en Karaoke y Pastilla; la
    // palabra clave en Karaoke) esta apagado con su nota y una sola foto. «Discreto»: el color del texto
    // pinta las tres palabras del dibujito.
    await ev(() => Estilo.setLevel("advanced")); await wait(900);
    const ADV = ["captions.max_lines", "captions.kw_accent", "captions.recenter", "captions.stroke", "captions.shadow"];
    rep.advTiles = {};
    for (const p of ["clean_bold", "karaoke", "boxed", "minimal_small"]) {
      await ev(p => { Estilo.editCaps(c => { c.preset = p; c.enabled = true; }); }, p);
      await wait(500);
      await ev(() => Estilo.Ticker.setTime(Estilo.Frames.win.words[1].start + 0.05));
      // (lo que esta fuera de la vista no se dibuja: cada ejemplo se mira antes de medirlo)
      for (const id of ADV) { await ev(id => document.querySelector(`#stCaps .hrow[data-setting="${id}"]`).scrollIntoView({ block: "center" }), id); await wait(160); }
      const t = await ev(({ ADV, p }) => ADV.map(id => {
        const r = document.querySelector(`#stCaps .hrow[data-setting="${id}"]`), k = id.split(".")[1];
        const cvs = [...r.querySelectorAll(".hrow-ex canvas")], n = r.querySelector(".hrow-note");
        return { id, k, applies: FXCore.captionOptionApplies(p, k), panels: cvs.length, hashes: cvs.map(c => __e2e.hashCv(c)), blank: cvs.some(c => !c.width || __e2e.nonBg(c) < 0.02),
          dis: [...r.querySelectorAll('.seg-h [role="radio"]')].every(b => b.disabled), note: n && n.offsetParent ? n.textContent : "", labels: [...r.querySelectorAll("figcaption")].map(f => f.textContent) };
      }), { ADV, p });
      rep.advTiles[p] = t.map(x => `${x.k}:${x.panels}${x.dis ? "/apagado" : ""}`);
      for (const x of t) {
        assert.ok(!x.blank, `${p} ${x.id}: ejemplo en blanco`);
        if (x.applies) {
          assert.ok(x.panels === 2 && x.hashes[0] !== x.hashes[1] && !x.dis, `${p} ${x.id}: las dos opciones se ven distintas ${JSON.stringify(x)}`);
        } else {
          assert.ok(x.panels === 1 && x.dis && x.note.length > 12, `${p} ${x.id}: no aplica -> apagado, con nota y una foto ${JSON.stringify(x)}`);
        }
      }
      // ningun ejemplo es copia de otro (antes: cinco veces la misma foto)
      const firsts = t.filter(x => x.applies).map(x => x.hashes.join("|"));
      assert.equal(new Set(firsts).size, firsts.length, `${p}: ejemplos repetidos ${JSON.stringify(rep.advTiles[p])}`);
      if (p === "karaoke") { await ev(() => document.querySelector('#stCaps .hrow[data-setting="captions.recenter"]').scrollIntoView({ block: "center" })); await wait(200); await shot("B_avanzado_karaoke"); }
      if (p === "minimal_small") {
        const sv = await ev(() => { const r = document.querySelector('#stCaps .hrow[data-setting="captions.color"]'); const col = (PLAN.fx.captions.color || "#FFFFFF").toUpperCase();
          return { col, fills: [...r.querySelectorAll(".hrow-ex svg text")].map(x => x.getAttribute("fill").toUpperCase()), help: r.querySelector(".hrow-help").textContent }; });
        assert.ok(sv.fills.length === 3 && sv.fills.every(f => f === sv.col) && /Todas las palabras/.test(sv.help), `Discreto: el color del texto pinta las tres palabras ${JSON.stringify(sv)}`);
      }
      if (p === "clean_bold") {
        const sv = await ev(() => [...document.querySelectorAll('#stCaps .hrow[data-setting="captions.color"] .hrow-ex svg text')].map(x => x.getAttribute("fill").toUpperCase()));
        assert.ok(sv[0] !== sv[1] && sv[0] === sv[2], `Palabra en color: el texto pinta la 1 y la 3 ${JSON.stringify(sv)}`);
      }
    }
    await ev(() => { Estilo.editCaps(c => { c.preset = "boxed"; }); Estilo.Ticker.setTime(null); Estilo.setLevel("basic"); });
    await wait(500);
    log(tag, "Avanzado › Subtítulos: ejemplos con su ajuste y reglas del motor", JSON.stringify(rep.advTiles));

    // C. ningun control muerto en Basico, en cada estilo de subtitulos Y en cada estilo de color
    // (antes: Calidez en «Blanco y negro» no hacia nada); en Avanzado, el color sin color tambien
    let checked = 0;
    const Q = '.seg-h button, .cw-sw, .cw-other input, .lcard, input[type="range"], input.toggle';
    /** Cada control de las filas de `secs` cambia la vista (o esta apagado y su fila dice por que). */
    const deadCheck = async (secs, tagC, only) => {
      // cada control por (fila, indice): se vuelve a buscar en cada vuelta (deshacer redibuja la fila)
      const ctls = await ev(({ Q, secs, only }) => {
        const out = [];
        document.querySelectorAll(secs.map(x => `#${x} .hrow[data-setting]`).join(",")).forEach(row => {
          const id = row.dataset.setting; if (["captions.preset", "transcript.edit", "grade.look"].includes(id) || !row.offsetParent || (only && !only.includes(id))) return;
          row.querySelectorAll(Q).forEach((e, i) => out.push({ sec: row.closest(".st-sec").id, row: id, i }));
        });
        return out;
      }, { Q, secs, only: only || null });
      for (const c of ctls) {
        const info = await ev(({ c, Q }) => {
          const row = document.querySelector(`#${c.sec} .hrow[data-setting="${c.row}"]`); if (!row) return null;
          const e = row.querySelectorAll(Q)[c.i]; if (!e) return null;
          if (e.getAttribute("aria-checked") === "true" || e.classList.contains("on")) return { skip: true };
          document.querySelectorAll("[data-e2e]").forEach(x => x.removeAttribute("data-e2e")); e.dataset.e2e = "x";
          const n = row.querySelector(".hrow-note");
          return { dis: e.disabled, note: !!(n && n.offsetParent), color: e.type === "color", range: e.type === "range", max: +e.max, val: +e.value };
        }, { c, Q });
        if (!info || info.skip) continue;
        const id = `${c.row}#${c.i}`;
        if (info.dis) { assert.ok(info.note, `${tagC}: control apagado sin decir por qué (${id})`); checked++; continue; }
        const q = '[data-e2e="x"]';
        await ev(q => document.querySelector(q).scrollIntoView({ block: "center" }), q);
        await page.mouse.move(2, VH - 2);           // sin el mouse encima: lo que cambie es por el control
        await wait(80);
        const hs = ["#stPvCv", "#stBrand .ap-mini canvas", `#${c.sec} .hrow[data-setting="${c.row}"] .hrow-ex canvas`];
        const h0 = await ev(hs => __e2e.hash(hs), hs);
        if (info.color) await ev(q => { const e = document.querySelector(q); e.value = "#3366cc"; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, q);
        else if (info.range) { await page.focus(q); await page.keyboard.press(info.val >= info.max ? "Home" : "End"); }
        else await page.click(q);
        await page.mouse.move(2, VH - 2);            // (la vista al pasar el mouse no cuenta)
        const changed = await poll(({ h0, hs }) => __e2e.hash(hs) !== h0, { h0, hs }, 600, 60);
        if (!changed && c.row === "grade.match_shots") {
          // igualar tomas sin el analisis de las tomas todavia: la fila lo dice (no es un control muerto);
          // o la toma de muestra ya venia pareja (su correccion es la identidad)
          await poll(() => FX.lookFresh(), null, 2000, 50);
          const m = await ev(() => {
            const n = document.querySelector('#stLook .hrow[data-setting="grade.match_shots"] .hrow-note');
            const lp = FX.lookPayload(), si = lp ? FXCore.lookShotAt(lp, Estilo.Frames.t) : -1, c0 = si >= 0 ? lp.per_shot[si].corr : null;
            return { pend: !!(n && n.offsetParent && n.textContent === FX.uiCopy().strings["match.pending"]), state: lp && lp.shots_state,
              ident: !!c0 && c0.gains.every(g => Math.abs(g - 1) < 0.01) && Math.abs(c0.black) < 0.005 && Math.abs(c0.white - 1) < 0.005 };
          });
          assert.ok(m.pend || m.ident, `${tagC}: «Igualar el color entre tomas» no cambia la vista ni dice por qué ${JSON.stringify(m)}`);
          rep.matchNote = m.pend ? "pendiente" : "toma ya pareja";
          await blur(); await page.keyboard.press("Meta+z"); await wait(250); checked++; continue;
        }
        assert.ok(changed, `${tagC}: el control ${id} no cambia la vista`);
        await blur();
        await page.keyboard.press("Meta+z");
        await wait(250);
        checked++;
      }
    };
    const presets = ["clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps", "none"];
    for (const p of presets) {
      await ev(() => document.querySelector("#stCol").scrollTo(0, 0));
      await page.click(`#stCaps .gcard[data-k="${p}"]`);
      await wait(500);
      // un instante con dos palabras a la vista: la que se dice (color de la marca) y una que no
      await ev(p => {
        const g = p === "none" ? null : Estilo.Groups.get(p).groups.find(x => x.words.length >= 2);
        Estilo.Ticker.setTime(g ? +g.words[1].start + 0.05 : Estilo.Frames.win.words[1].start + 0.05);
      }, p);
      await wait(300);
      await deadCheck(["stCaps", "stBrand", "stLook"], p);
    }
    const checkedCaps = checked;
    // cada estilo de color con los ajustes de Basico (Igualar tomas, Brillo, Calidez) y, en «Blanco y
    // negro» y «Natural», tambien los de Avanzado (Tinte, Intensidad de los colores...)
    await ev(() => document.querySelector("#stCol").scrollTo(0, 0));
    await page.click('#stCaps .gcard[data-k="clean_bold"]');
    await wait(300);
    const looksDone = [];
    for (const lk of ["natural", "warm_clean", "cinematic", "bright_pop", "moody", "bw_contrast"]) {
      for (const lv of lk === "bw_contrast" || lk === "natural" ? ["basic", "advanced"] : ["basic"]) {
        await ev(l => Estilo.setLevel(l), lv); await wait(lv === "advanced" ? 1200 : 500);
        await ev(() => document.querySelector("#stLook").scrollIntoView({ block: "start" }));
        if (await ev(k => PLAN.fx.grade.look !== k, lk)) { await page.click(`#stLook .lcard[data-k="${lk}"]`); await page.mouse.move(2, VH - 2); }
        await poll(k => PLAN.fx.grade.look === k && FX.lookFresh(), lk, 3000, 50);
        await wait(300);
        if (lk === "bw_contrast") {
          // Blanco y negro: Calidez (Basico), Tinte e Intensidad (Avanzado) apagados con su nota y una sola foto
          const bw = await ev(() => ["grade.temperature", "grade.tint", "grade.saturation"].map(id => {
            const r = document.querySelector(`#stLook .hrow[data-setting="${id}"]`); if (!r) return null;
            const n = r.querySelector(".hrow-note");
            return { id, dis: r.querySelector('input[type="range"]').disabled, note: n && n.offsetParent ? n.textContent : "", figs: r.querySelectorAll(".hrow-ex figure").length };
          }).filter(Boolean));
          assert.ok(bw.length === (lv === "advanced" ? 3 : 1) && bw.every(x => x.dis && /Blanco y negro/.test(x.note) && x.figs === 1), `B/N sin color: ${JSON.stringify(bw)}`);
          rep.bwNotes = bw;
          if (lv === "basic") { await ev(() => document.querySelector('#stLook [data-setting="grade.temperature"]').scrollIntoView({ block: "center" })); await wait(300); await shot("C_blanco_y_negro_calidez"); }
        }
        // (en Avanzado, los ajustes de color que la vista grande dibuja al instante: grano, nitidez y
        // definicion se ven en sus recortes al 200 % y en el cuadro exacto al pausar)
        await deadCheck(["stLook"], `${lk}/${lv}`, lv === "advanced" ? ["grade.temperature", "grade.tint", "grade.saturation", "grade.exposure", "grade.contrast", "grade.strength"] : null);
        looksDone.push(`${lk}/${lv}`);
      }
    }
    await ev(() => Estilo.setLevel("basic")); await wait(400);
    await page.click('#stLook .lcard[data-k="natural"]'); await wait(300);
    rep.lookDeadChecks = { looks: looksDone, controls: checked - checkedCaps };
    rep.deadControlChecks = checked;
    assert.ok(checkedCaps > 40, `controles revisados: ${checkedCaps}`);
    assert.ok(checked - checkedCaps >= 20, `controles de color revisados: ${checked - checkedCaps}`);
    log(tag, `C ningún control muerto (${checkedCaps} en 6 estilos de subtítulos, ${checked - checkedCaps} en 6 estilos de color)`);

    // D. estilos de color: fotos del motor, mantener presionado, comparar, mismo color que el motor
    await ev(() => { Estilo.Ticker.setTime(null); document.querySelector("#stLook").scrollIntoView({ block: "start" }); });
    await page.mouse.move(2, VH - 2);
    const tD = Date.now();
    await ev(() => Estilo.lookReload());
    // las seis tarjetas YA muestran la foto del motor (no solo llego la respuesta). Igual "al pixel" =
    // error medio < 0.5 (el lienzo visible y uno aparte pueden reescalar con 1-2 niveles de diferencia)
    const ready = await poll(() => Estilo.lookInfo().st === "ready" && [...document.querySelectorAll("#stLook .lcard")].every(b =>
      __e2e.mae(b.querySelector("canvas"), Estilo.lookCardRef(b.dataset.k, false)) < 0.5), null, 3000, 25);
    rep.lookThumbsMs = Date.now() - tD;
    assert.ok(ready && rep.lookThumbsMs <= 1000, `las seis fotos del motor se ven en menos de 1 s (${rep.lookThumbsMs} ms)`);
    const lk = await ev(() => [...document.querySelectorAll("#stLook .lcard")].map(b => { const cv = b.querySelector("canvas"), k = b.dataset.k;
      return { k, nb: __e2e.nonBg(cv), shown: __e2e.mae(cv, Estilo.lookCardRef(k, false)), vsOrig: __e2e.mae(Estilo.lookCardRef(k, true), Estilo.lookCardRef(k, false)) }; }));
    assert.equal(lk.length, 6);
    for (const x of lk) { assert.ok(x.nb > 0.02 && x.shown < 0.5, `tarjeta ${x.k}: foto del motor (${JSON.stringify(x)})`); }
    assert.ok(lk.filter(x => x.vsOrig > 2).length >= 5, `las fotos traen el color de cada estilo ${JSON.stringify(lk)}`);
    assert.equal(await ev(() => document.querySelector('#stLook .lcard[data-k="natural"] .lbadge').textContent), "Recomendado");
    assert.equal(await ev(() => document.querySelectorAll("#stLook .lbadge").length), 1, "solo Natural lleva «Recomendado»");
    assert.ok(await ev(() => document.querySelector("#stLook .lp-load").hidden && document.querySelector("#stLook .lp").getAttribute("aria-busy") === "false"), "listas: sin línea de espera");
    // D.1b la primera vez, mientras llegan: el original con un brillo que pasa y la línea de estado;
    // si fallan: el original, «No se pudo hacer la vista previa del color» y «Reintentar» (que sí vuelve a pedirlas)
    const held1 = [];
    await page.route("**/fx/look_thumbs/**", route => { held1.push(route); });
    await ev(() => { Estilo.LP.thumbs = {}; Estilo.lookReload(); });
    const firstLoad = await poll(() => { const w = document.querySelector("#stLook .lp-wrap"), l = w.querySelector(".lp-load"); Estilo.Ticker.flush();
      return Estilo.lookInfo().st === "loading" && !l.hidden && w.querySelector(".lp").classList.contains("loading") && w.querySelector(".lp").getAttribute("aria-busy") === "true"
        && [...w.querySelectorAll(".lcard")].every(b => __e2e.mae(b.querySelector("canvas"), Estilo.lookCardRef(b.dataset.k, true)) < 0.5) && l.textContent; }, null, 3000, 50);
    assert.equal(firstLoad, await ev(() => FX.uiCopy().strings["look.loading"]), "mientras llegan: el original y la línea de espera");
    await page.unroute("**/fx/look_thumbs/**");
    await page.route("**/fx/look_thumbs/**", route => route.fulfill({ status: 500, contentType: "text/plain", body: "falla de prueba" }));
    held1.forEach(r => r.continue().catch(() => null));
    await ev(() => { Estilo.LP.thumbs = {}; Estilo.lookReload(); });
    const failed = await poll(() => { const w = document.querySelector("#stLook .lp-wrap"), e = w.querySelector(".lp-err"); Estilo.Ticker.flush();
      return Estilo.lookInfo().st === "error" && !e.hidden && w.querySelector(".lp-load").hidden && w.querySelector(".lp").getAttribute("aria-busy") === "false"
        && [...w.querySelectorAll(".lcard")].every(b => __e2e.mae(b.querySelector("canvas"), Estilo.lookCardRef(b.dataset.k, true)) < 0.5) && e.querySelector("span").textContent; }, null, 3000, 50);
    assert.equal(failed, await ev(() => FX.uiCopy().strings["look.error"]), "si fallan: el original y la línea de error");
    // el aviso queda a la vista junto con la galería (todas las fotos son iguales: sin él no se entiende)
    assert.ok(await ev(() => { const e = document.querySelector("#stLook .lp-err").getBoundingClientRect(), g = document.querySelector("#stLook .lp").getBoundingClientRect(), c = document.querySelector("#stCol").getBoundingClientRect();
      return e.bottom <= g.top && e.top >= c.top && e.bottom <= Math.min(c.bottom, innerHeight); }), "el aviso de error se ve arriba de la galería");
    await shot("D1b_error_reintentar");
    await page.unroute("**/fx/look_thumbs/**");
    await page.click('#stLook .lp-err [data-a="retry"]');
    // (el clic desplaza la columna hasta el botón: las tarjetas que quedan fuera de la vista no se
    // dibujan hasta volver a verse, así que se vuelve a mostrar la galería antes de medir)
    await ev(() => document.querySelector("#stLook").scrollIntoView({ block: "start" }));
    const retried = await poll(() => Estilo.lookInfo().st === "ready" && document.querySelector("#stLook .lp-err").hidden && [...document.querySelectorAll("#stLook .lcard")].every(b =>
      __e2e.mae(b.querySelector("canvas"), Estilo.lookCardRef(b.dataset.k, false)) < 0.5), null, 3000, 50);
    assert.ok(retried, `«Reintentar» trae las fotos del motor ${JSON.stringify(await ev(() => ({ st: Estilo.lookInfo().st, err: document.querySelector("#stLook .lp-err").hidden,
      cards: [...document.querySelectorAll("#stLook .lcard")].map(b => [b.dataset.k, +__e2e.mae(b.querySelector("canvas"), Estilo.lookCardRef(b.dataset.k, false)).toFixed(2),
        +__e2e.mae(b.querySelector("canvas"), Estilo.lookCardRef(b.dataset.k, true)).toFixed(2), Math.round(b.getBoundingClientRect().top)]) })))}`);
    await page.mouse.move(2, VH - 2);
    log(tag, "D.1b mientras llegan, el original con la línea de espera; si fallan, «Reintentar»");
    // D.2 mantener presionado: el original (y al soltar no elige)
    const lkCard = '#stLook .lcard[data-k="bw_contrast"]', lkBox = await page.locator(lkCard).boundingBox();
    const u1 = await ev(() => UNDO.length), look1 = await ev(() => PLAN.fx.grade.look);
    await page.mouse.move(lkBox.x + lkBox.width / 2, lkBox.y + lkBox.width * 0.3);
    await page.mouse.down(); await wait(500);
    const held = await ev(s => { const cv = document.querySelector(s + " canvas"); return { orig: __e2e.mae(cv, Estilo.lookCardRef("bw_contrast", true)), chip: getComputedStyle(document.querySelector(s + " .lorig")).visibility,
      pv: __e2e.mae(document.querySelector("#stPvCv"), Estilo.previewRef()), state: document.querySelector("#stPvState").textContent }; }, lkCard);
    await shot("D2_mantener_presionado");
    await page.mouse.up(); await wait(200);
    await page.mouse.move(2, VH - 2); await wait(150);
    assert.ok(held.orig < 0.5 && held.chip === "visible", `mantener presionado muestra el original ${JSON.stringify(held)}`);
    assert.ok(held.pv < 1 && held.state === "Así salió de la cámara", `la vista grande tambien muestra el original ${JSON.stringify(held)}`);
    assert.ok(await ev(s => __e2e.mae(document.querySelector(s + " canvas"), Estilo.lookCardRef("bw_contrast", false)) < 0.5, lkCard), "al soltar vuelve la foto");
    assert.equal(await ev(() => PLAN.fx.grade.look), look1, "mantener presionado no elige");
    assert.equal(await ev(() => UNDO.length), u1);
    // con el teclado: Espacio sostenido = el original
    await page.focus(lkCard); await page.keyboard.down(" "); await wait(450);
    assert.ok(await ev(s => __e2e.mae(document.querySelector(s + " canvas"), Estilo.lookCardRef("bw_contrast", true)) < 0.5, lkCard), "Espacio sostenido muestra el original");
    await page.keyboard.up(" "); await wait(200);
    assert.equal(await ev(() => PLAN.fx.grade.look), look1, "Espacio sostenido no elige");
    await blur();
    log(tag, `D.1-2 seis fotos del motor (${rep.lookThumbsMs} ms); mantener presionado muestra el original`);
    // D.3 elegir «Oscuro dramático» y comparar con el original: la linea al 30 %
    await page.click('#stLook .lcard[data-k="moody"]');
    await page.mouse.move(2, VH - 2);
    await poll(() => PLAN.fx.grade.look === "moody" && FX.lookFresh(), null, 3000, 50);
    if (await ev(() => document.querySelector("#stCompare").getAttribute("aria-pressed") === "true")) await page.click("#stCompare");
    assert.ok(await ev(() => document.querySelector("#stAb").hidden), "comparar apagado");
    await page.click("#stCompare");
    await wait(200);
    assert.ok(await ev(() => !document.querySelector("#stAb").hidden && !!document.querySelector(".ab-line").offsetParent), "«Comparar con el original» muestra la línea");
    const hb = await page.locator("#stAbH").boundingBox(), pvb = await page.locator("#stPvBox").boundingBox();
    await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2); await page.mouse.down();
    await page.mouse.move(pvb.x + pvb.width * 0.3, hb.y + hb.height / 2, { steps: 6 }); await page.mouse.up();
    await page.mouse.move(2, VH - 2);
    await ev(() => Estilo.Ticker.setTime(Estilo.Frames.win.words[1].start + 0.06));
    await wait(400);
    const ab = await ev(() => { const cv = document.querySelector("#stPvCv"), ref = Estilo.previewRef(), x = Estilo.lookInfo().ab;
      return { ab: x, left: __e2e.mae(cv, ref, 0, x - 4 / cv.width), right: __e2e.mae(cv, ref, x + 4 / cv.width, 1) }; });
    rep.compare = { ab: +ab.ab.toFixed(3), left: +ab.left.toFixed(2), right: +ab.right.toFixed(2) };
    assert.ok(Math.abs(ab.ab - 0.3) < 0.02 && ab.left < 3 && ab.right > 3, `a la izquierda el original, a la derecha el color ${JSON.stringify(rep.compare)}`);
    // los chips «Antes» y «Despues», a la misma distancia de cada orilla
    const chips = await ev(() => { const b = document.querySelector("#stPvBox").getBoundingClientRect(), a = document.querySelector("#stAbA").getBoundingClientRect(), z = document.querySelector("#stAbB").getBoundingClientRect(); return Math.abs((a.left - b.left) - (b.right - z.right)); });
    assert.ok(chips <= 0.5, `chips simétricos ${chips}`);
    await shot("D3_comparar");
    log(tag, "D.3 «Comparar con el original»: línea al 30 %", JSON.stringify(rep.compare));
    // D.4 el color dibujado en el navegador es el del motor
    await poll(() => Object.keys(Estilo.lookParity()).length === 6, null, 4000, 100);
    const par = await ev(() => Estilo.lookParity());
    rep.lookParity = par;
    assert.equal(Object.keys(par).length, 6, JSON.stringify(par));
    for (const [k, v] of Object.entries(par)) assert.ok(v <= 6, `${k}: vista ${v}/255 contra la foto del motor`);
    log(tag, "D.4 color en el navegador = motor", JSON.stringify(par));
    await ev(() => Estilo.Ticker.setTime(null));
    await blur(); await page.keyboard.press("Meta+z"); await wait(500);
    assert.equal(await ev(() => PLAN.fx.grade.look), "natural", "⌘Z vuelve a Natural");
    await page.click("#stCompare"); await wait(150);

    // dejar la pastilla elegida para lo que sigue
    await ev(() => document.querySelector("#stCol").scrollTo(0, 0));
    await page.click(card);
    await wait(400);
    assert.equal(await ev(() => PLAN.fx.captions.preset), "boxed");
    await ev(() => Estilo.Ticker.setTime(null));
    await shot("A_estilo_pastilla");

    // A.9 el cuadro exacto del motor trae la pastilla del color de ahora
    await blur();
    await page.keyboard.press("2");
    await wait(600);
    // un cuadro exacto NUEVO (el de antes pudo quedar de otro instante)
    const prevSrc = await ev(() => { FX.hideExact(); return document.querySelector("#pvExact").src; });
    const tExact = await ev(() => { const w = Estilo.Frames.win.words[1]; V.pause(); V.currentTime = w.start + 0.12; return w.start + 0.12; });
    const exactOk = await page.waitForFunction(prev => { const im = document.querySelector("#pvExact"); return im.classList.contains("on") && im.src !== prev && im.complete && im.naturalWidth > 0; }, prevSrc, { timeout: 120000 }).then(() => true, () => false);
    assert.ok(exactOk, "llega el cuadro exacto del motor");
    const want = await ev(() => FXCore.projectAccent(PLAN));
    const nExact = await ev(({ want }) => { const im = document.querySelector("#pvExact"); const cv = document.createElement("canvas"); cv.width = im.naturalWidth; cv.height = im.naturalHeight; cv.getContext("2d").drawImage(im, 0, 0); return __e2e.count(cv, want, 40, [0, 0.55, 1, 0.9]); }, { want });
    rep.exactPillPx = nExact;
    assert.ok(nExact >= 150, `el export dibuja la pastilla (${nExact} px de ${want} en t=${tExact.toFixed(2)})`);
    await shot("A9_cuadro_exacto");
    log(tag, `A.9 el cuadro exacto del motor trae la pastilla (${nExact} px)`);

    // A.10 clic en los subtitulos del video -> inspector compacto con el mismo estado
    await ev(() => FX.setExact(false));
    await wait(400);
    const hit = await ev(() => { const h = FX.hits().hits.find(x => x.track === "captions"); if (!h) return null; const r = CV.getBoundingClientRect(); return { x: r.left + (h.bb.x + h.bb.w / 2) / CW * r.width, y: r.top + (h.bb.y + h.bb.h / 2) / CH * r.height }; });
    assert.ok(hit, "hay subtítulos en el video para tocar");
    await page.mouse.click(hit.x, hit.y);
    await wait(900);
    assert.ok(await ev(() => !!document.querySelector('#inspBody .gal') && document.querySelector('#inspBody .gal').style.getPropertyValue("--cols") === "2"), "galería de 2 columnas en el inspector");
    assert.equal(await ev(() => document.querySelector('#inspBody .gcard[data-k="boxed"]').getAttribute("aria-checked")), "true");
    assert.deepEqual(await ev(() => __e2e.labels("#inspBody .hrow.cw")), ["Color de la pastilla", "Color del texto"]);
    // los ejemplos del inspector, en un instante con palabras a la vista
    await ev(() => { const g = Estilo.Groups.get("boxed").groups.find(x => x.words.length >= 2); Estilo.Ticker.setTime(+g.words[1].start + 0.05); });
    for (const lv of ["basic", "advanced"]) {
      await ev(l => Estilo.setLevel(l), lv);
      await wait(1300);
      const probs = await ev(LINT, { root: "#inspBody", level: lv, chrome: ".lvlsw,.caps-more,.ins-head" });
      assert.deepEqual(probs, [], `B inspector (${lv}): ${probs.slice(0, 12).join(" | ")}`);
      if (lv === "advanced") assert.ok(await ev(() => !!document.querySelector('#inspBody [data-setting="captions.y_ranges"]')), "tramos en Avanzado");
    }
    await ev(() => Estilo.setLevel("basic"));
    await wait(400);
    // desde el video: otro estilo, la tarjeta de la pastilla arriba del panel y UN clic -> su fila de
    // color queda a la vista dentro del panel que se desplaza (no solo dentro de la ventana)
    await page.click('#inspBody .gcard[data-k="clean_bold"]'); await wait(500);
    await ev(() => { const c = document.querySelector('#inspBody .gcard[data-k="boxed"]'), pn = c.closest(".panel"); pn.scrollTop += c.getBoundingClientRect().top - pn.getBoundingClientRect().top - 60; });
    await wait(300);
    await page.click('#inspBody .gcard[data-k="boxed"]');
    await wait(900);
    const iv = await ev(() => Estilo.inView(document.querySelector('#inspBody [data-setting="captions.highlight"]')));
    rep.inspectorPillRow = iv;
    assert.ok(iv.ok && (await ev(() => PLAN.fx.captions.preset)) === "boxed", `«Color de la pastilla» a la vista en el inspector ${JSON.stringify(iv)}`);
    await shot("A10_inspector_pastilla");
    await ev(() => { document.querySelector("#inspBody").closest(".panel").scrollTop = 0; });
    await shot("A10_inspector");
    await ev(() => Estilo.Ticker.setTime(null));
    await page.click('#inspBody .gcard[data-k="karaoke"]');
    await wait(400);
    assert.equal(await ev(() => PLAN.fx.captions.preset), "karaoke");
    await blur();
    await page.keyboard.press("3");
    await wait(600);
    assert.equal(await ev(() => document.querySelector('#stCaps .gcard[data-k="karaoke"]').getAttribute("aria-checked")), "true", "estado compartido");
    log(tag, "A.10 clic en los subtítulos -> inspector compacto con el mismo estado");

    // resumen en Proyecto con su enlace «Cambiar»
    await blur();
    await page.keyboard.press("2");
    await ev(() => setPanelTab("proj"));
    await wait(300);
    const sum = await ev(() => document.querySelector("#styleSummary").innerText);
    assert.match(sum, /Subtítulos/); assert.match(sum, /Karaoke/); assert.match(sum, /Color de tu marca/); assert.match(sum, /Color del video/); assert.match(sum, /Natural/);
    await page.click('#styleSummary [data-go="stBrand"]');
    await wait(800);
    assert.equal(await ev(() => FX.clean.mode), "style");
    assert.ok(await ev(() => document.querySelector("#stBrand").contains(document.activeElement)), "«Cambiar» lleva a Color de tu marca");
    log(tag, "resumen del estilo en Proyecto con «Cambiar»");

    // «Corregir el texto de los subtítulos» lleva a la transcripción (Edición › Proyecto)
    await ev(() => document.querySelector('#stCaps [data-setting="transcript.edit"]').scrollIntoView({ block: "center" }));
    await page.click('#stCaps [data-setting="transcript.edit"] button');
    await wait(700);
    assert.equal(await ev(() => FX.clean.mode), "edit");
    assert.ok(await ev(() => document.querySelector("#tabProj").classList.contains("on") && (r => r.top >= 0 && r.top < window.innerHeight)(document.querySelector("#subs").getBoundingClientRect())), "la transcripción a la vista");
    log(tag, "«Corregir el texto de los subtítulos» abre la transcripción");

    // ---------------------------------------------------------------- fase 3: el resto de Estilo
    await blur(); await page.keyboard.press("3"); await wait(500);
    assert.deepEqual(await ev(() => [...document.querySelectorAll("#stCol .st-sec")].filter(x => !x.hidden).map(x => x.id)),
      ["stCaps", "stBrand", "stLook", "stDirector", "stMusic", "stWho", "stFormat"], "las siete secciones, en su orden");
    assert.deepEqual(await ev(() => ["segFmt", "segLang", "logoOn", "presetSel", "enhance", "air", "dirFull", "dirVar", "profEdit", "dirMusic"]
      .filter(i => document.querySelector("#tabProj #" + i))), [], "el panel Proyecto ya no tiene los controles movidos");
    const toSec = async id => { await ev(i => { document.querySelector("#stCol").scrollTo(0, 0); document.getElementById(i).scrollIntoView({ block: "start" }); }, id); await wait(600); };

    // Edición con IA: las cuatro tarjetas con tu cuadro, su título y sus subtítulos; UN clic aplica el
    // estilo (un paso de deshacer) y el ejemplo de «Cantidad de efectos» lleva el conteo real del plan
    await toSec("stDirector");
    await poll(() => [...document.querySelectorAll("#stDirector .dcard canvas")].every(c => c.width && __e2e.nonBg(c) > 0.02), null, 3000);
    rep.directorCards = await ev(() => [...document.querySelectorAll("#stDirector .dcard canvas")].map(c => +__e2e.nonBg(c).toFixed(3)));
    assert.ok(rep.directorCards.length === 4 && rep.directorCards.every(x => x > 0.02), `tarjetas de edición: ${rep.directorCards}`);
    await shot("E1_edicion_ia");
    const du0 = await ev(() => UNDO.length), st0 = await ev(() => FX.dirStyle());
    const target = st0 === "elegante" ? "dinamico" : "elegante";
    // lo que la tarjeta y «Qué cambia» prometen (tus subtítulos y tu color se quedan si los cambiaste)
    await page.hover(`#stDirector .dcard[data-k="${target}"]`); await wait(200);
    const promise = await ev(t => ({ o: FXCore.directorOutcome(PLAN, t), qc: document.querySelector("#stDirector .dqc p").textContent }), target);
    await page.mouse.move(2, VH - 2);
    const tq = Date.now();
    await page.click(`#stDirector .dcard[data-k="${target}"]`);
    await page.waitForFunction(t => !FX.busy() && PLAN.director.style === t, target, { timeout: 60000 });
    rep.directorMs = Date.now() - tq;
    await wait(400);
    assert.equal(await ev(() => UNDO.length), du0 + 1, "un paso de deshacer");
    assert.equal(await ev(() => document.querySelector('#stDirector .dcard[aria-checked="true"]').dataset.k), target);
    const dEx = await ev(() => ({ t: document.querySelector('#stDirector [data-setting="director.intensity"] .hrow-ex').textContent, n: FXCore.directorCounts(PLAN),
      qc: document.querySelector("#stDirector .dqc summary").textContent, dur: FXCore.fmtClock(FX.finalDuration()) }));
    assert.ok(dEx.t.startsWith(`Ej.: Con «Media», tu video de ${dEx.dur} lleva ${dEx.n.titulos} título`) && dEx.t.includes(`${dEx.n.efectos} efecto`), JSON.stringify(dEx));
    assert.match(dEx.qc, new RegExp(`Qué cambia con «${target === "elegante" ? "Elegante" : "Dinámico"}»`));
    rep.directorExample = dEx.t;
    // el director dejo exactamente lo que la tarjeta mostraba (y «Qué cambia» lo decia)
    const after = await ev(() => ({ c: PLAN.fx.captions, g: PLAN.fx.grade }));
    const nv = (k, v) => (v == null ? (k === "color" ? "#FFFFFF" : null) : v);
    for (const k of ["preset", "highlight", "size", "font", "case", "color"]) assert.deepEqual(nv(k, after.c[k]), nv(k, promise.o.captions[k]), `subtítulos.${k}: ${JSON.stringify([after.c, promise.o.captions])}`);
    assert.equal(after.g.look, promise.o.grade.look, `color: ${JSON.stringify([after.g, promise.o.grade])}`);
    if (promise.o.capsKept.includes("preset")) assert.match(promise.qc, /^Tus subtítulos se quedan en «/, promise.qc);
    if (promise.o.gradeKept) assert.match(promise.qc, /tu color se queda en «/, promise.qc);
    rep.directorPromise = { kept: promise.o.capsKept, gradeKept: promise.o.gradeKept, qc: promise.qc };
    await blur(); await page.keyboard.press("Meta+z"); await wait(500);
    assert.equal(await ev(() => FX.dirStyle()), st0, "⌘Z vuelve al estilo anterior");
    log(tag, `Edición con IA: un clic aplica «${target}» (${rep.directorMs} ms, un paso de deshacer); «${dEx.t}»`);

    // Música (E): «Rápida (sin IA)» en un clic; cada ambiente se escucha antes de elegirlo; un segundo clic
    // lo para; empezar otro para el primero; la música de tu video se crea y se escucha
    await toSec("stMusic");
    const mu0 = await ev(() => UNDO.length);
    await page.click('#stMusic .mopt[data-v="procedural"] .mopt-pick');
    await page.waitForFunction(() => PLAN.fx.music.source === "procedural" && PLAN.fx.music.enabled !== false && document.querySelectorAll('#stMusic [data-setting="music.mood"] .mopt').length === 7, null, { timeout: 5000 });
    assert.equal(await ev(() => UNDO.length), mu0 + 1);
    // «Creada con IA» con otra fuente (o sin crearla): no toca la misma muestra que «Rápida»; esta
    // apagado con su razon y la nota junto a la lista lo explica
    const srcL = await ev(() => { const a = document.querySelector('#stMusic [data-setting="music.source"] .mopt[data-v="ai"] .listen'), p = document.querySelector('#stMusic [data-setting="music.source"] .mopt[data-v="procedural"] .listen'), n = document.querySelector("#stMusic .src-note");
      return { ai: a.disabled, aiTitle: a.title, proc: p.disabled, note: n && !n.hidden ? n.textContent : "" }; });
    assert.ok(srcL.ai && srcL.aiTitle.length > 10 && !srcL.proc && /Creada con IA/.test(srcL.note), `escuchar «Creada con IA» ${JSON.stringify(srcL)}`);
    rep.sourceListen = srcL;
    const lis = m => `#stMusic .mopt[data-v="${m}"] .listen`;
    await ev(q => document.querySelector(q).scrollIntoView({ block: "center" }), lis("upbeat"));
    await page.click(lis("upbeat"));
    const e1 = await poll(() => { const i = Estilo.Audio.info(); return i && i.kind === "mood" && i.id === "upbeat" && i.t > 0.3 ? i : null; }, null, 8000, 100);
    assert.ok(e1, "«Escuchar» de un ambiente suena y avanza");
    assert.equal(await ev(q => [document.querySelector(q).getAttribute("aria-pressed"), document.querySelector(q).textContent].join("|"), lis("upbeat")), "true|Detener");
    await page.click(lis("upbeat"));
    await wait(200);
    assert.equal(await ev(() => Estilo.Audio.info()), null, "un segundo clic lo para");
    await page.click(lis("upbeat"));
    await poll(() => { const i = Estilo.Audio.info(); return i && i.t > 0.1; }, null, 8000, 100);
    await page.click(lis("chill"));
    const e2 = await poll(() => { const i = Estilo.Audio.info(); return i && i.id === "chill" && i.t > 0.2 ? i : null; }, null, 8000, 100);
    assert.ok(e2, "el segundo ambiente suena");
    assert.equal(await ev(q => document.querySelector(q).getAttribute("aria-pressed"), lis("upbeat")), "false", "y el primero se paró");
    await page.click(lis("chill")); await wait(200);
    rep.listen = { mood: e1.t, second: e2.id };
    await shot("E2_musica");
    // la música de tu video: se crea con tus ajustes y se escucha
    await ev(() => document.querySelector('#stMusic [data-setting="music.preview"]').scrollIntoView({ block: "center" }));
    await page.click("#musGen");
    await page.waitForFunction(() => FX.music.file && !FX.music.busy, null, { timeout: 120000 });
    await wait(300);
    // en Basico solo «Lista.»: el ritmo y el golpe principal son de Avanzado (ahi estan sus controles)
    assert.equal(await ev(() => document.querySelector('#stMusic [data-setting="music.preview"] .mus-st').textContent), "Lista.");
    await page.click('#stMusic [data-setting="music.preview"] .listen');
    assert.ok(await poll(() => { const i = Estilo.Audio.info(); return i && i.kind === "file" && i.t > 0.3; }, null, 6000, 100), "la música creada se escucha");
    await page.click('#stMusic [data-setting="music.preview"] .listen'); await wait(200);
    // claridad de Música con un origen elegido, en Básico y en Avanzado (y la biblioteca con sus canciones)
    for (const lv of ["basic", "advanced"]) {
      await ev(l => Estilo.setLevel(l), lv); await wait(900);
      const probs = await ev(LINT, { root: "#stMusic", level: lv, chrome: ".st-sh" });
      assert.deepEqual(probs, [], `B Música (${lv}): ${probs.slice(0, 10).join(" | ")}`);
      if (lv === "advanced") {
        await ev(() => document.querySelector('#stMusic [data-setting="music.bpm"]').scrollIntoView({ block: "center" })); await wait(300); await shot("E3_musica_avanzado");
        const ms = await ev(() => ({ t: document.querySelector('#stMusic [data-setting="music.preview"] .mus-st').textContent, bpm: FX.musicState().bpm }));
        if (ms.bpm) assert.match(ms.t, /pulsos por minuto/, JSON.stringify(ms));
      }
    }
    await ev(() => Estilo.setLevel("basic")); await wait(600);
    await ev(() => document.querySelector("#stMusic").scrollIntoView({ block: "start" })); await wait(300);
    await page.click('#stMusic .mopt[data-v="library"] .mopt-pick');
    await page.waitForFunction(() => document.querySelectorAll('#stMusic [data-setting="music.track"] .mopt .listen').length >= 1, null, { timeout: 10000 });
    const libProbs = await ev(LINT, { root: "#stMusic", level: "basic", chrome: ".st-sh" });
    assert.deepEqual(libProbs, [], `B Música, biblioteca: ${libProbs.slice(0, 10).join(" | ")}`);
    // de vuelta a «Sin música» (dos pasos de deshacer)
    await blur(); await page.keyboard.press("Meta+z"); await wait(250); await page.keyboard.press("Meta+z"); await wait(400);
    assert.equal(await ev(() => FXCore.musicSourceOf(PLAN.fx.music)), "none");
    log(tag, `E música: el ambiente suena (${e1.t} s), un segundo clic lo para, otro para el primero; la música creada se escucha`);

    // E: pasar el mouse 300 ms por un sonido de «Agregar» lo suena (Edición)
    await blur(); await page.keyboard.press("2"); await wait(400);
    await page.click("#addAny");
    await page.click('.fxmenu.on [data-t="sfx"]');
    await page.waitForSelector(".fxmenu.on [data-k]");
    const sfxK = await ev(() => document.querySelector(".fxmenu.on [data-k]").dataset.k);
    const uS = await ev(() => UNDO.length);
    await page.hover(`.fxmenu.on [data-k="${sfxK}"]`);
    const e3 = await poll(k => { const i = Estilo.Audio.info(); return i && i.kind === "sfx" && i.id === k && i.t > 0 ? i : null; }, sfxK, 2000, 50);
    assert.ok(e3, `pasar el mouse por «${sfxK}» lo suena`);
    await page.mouse.move(2, VH - 2); await page.keyboard.press("Escape"); await ev(() => document.querySelector(".fxmenu").classList.remove("on"));
    assert.equal(await ev(() => UNDO.length), uS, "escuchar no agrega nada");
    rep.listen.sfx = sfxK;
    log(tag, `E sonidos: «${sfxK}» suena al pasar el mouse`);

    // Quién sale y tu logo: sin datos de la persona, «Dónde aparece» está apagado y dice por qué; el
    // logo prendido cambia su ejemplo y su tamaño se lee en palabras
    await page.keyboard.press("3"); await wait(400);
    await toSec("stWho");
    const who = await ev(() => ({ rows: [...document.querySelectorAll("#stWho .hrow[data-setting]")].filter(r => r.offsetParent).map(r => r.dataset.setting),
      off: [...document.querySelectorAll('#stWho [data-setting="profile.use"] input')].every(i => i.disabled),
      note: (document.querySelector('#stWho [data-setting="profile.use"] .hrow-note') || {}).textContent || "", hv: document.querySelector("#logoSizeLbl").textContent }));
    assert.deepEqual(who.rows, ["setup.profile", "profile.use", "brand.logo", "brand.preset"]);
    assert.ok(who.off && who.note === "Primero agrega tus datos en «Quién sale en el video».", JSON.stringify(who));
    assert.match(who.hv, /^\d+ % del ancho$/);
    const logoSt = () => ev(() => ({ has: !!logoImg, on: document.querySelector("#logoOn").disabled === false, size: !document.querySelector("#logoSize").disabled,
      corners: [...document.querySelectorAll("#segLogo button")].every(b => !b.disabled), need: !document.querySelector("#logoNeed").hidden,
      notes: [...document.querySelectorAll("#brandLogoRow .hrow-note")].filter(n => n.offsetParent).map(n => n.textContent) }));
    let ls = await logoSt();
    if (!ls.has) {
      // sin logo subido: prenderlo no haria nada -> apagado, la esquina y el tamaño tambien, y la nota pide subirlo
      assert.ok(!ls.on && !ls.size && !ls.corners && ls.need && ls.notes.includes("Sube tu logo para mostrarlo."), `sin logo ${JSON.stringify(ls)}`);
      await shot("E4_logo_sin_subir");
      // se sube uno (lo mismo que hace «Subir tu logo»)
      await ev(async () => { const c = document.createElement("canvas"); c.width = 240; c.height = 120; const x = c.getContext("2d"); x.fillStyle = "#FF5A3C"; x.fillRect(0, 0, 240, 120);
        const b = await new Promise(r => c.toBlob(r, "image/png")); const fd = new FormData(); fd.append("logo", new File([b], "logo.png", { type: "image/png" }));
        await fetch("/brand/logo", { method: "POST", body: fd }); loadLogo(); });
      assert.ok(await poll(() => !!logoImg && !document.querySelector("#logoOn").disabled, null, 4000, 100), "con logo, «Mostrar el logo» se prende");
      ls = await logoSt();
    }
    assert.ok(ls.on && ls.size && ls.corners && !ls.need && !ls.notes.some(n => /simétricas/.test(n)), `con logo ${JSON.stringify(ls)}`);
    // (se empieza apagado: el ejemplo tenue; prendido, el logo a la vista)
    if (await ev(() => document.querySelector("#logoOn").checked)) { await page.click("#brandLogoRow .logo-on"); await wait(400); }
    const lh0 = await ev(() => __e2e.hash(["#brandLogoRow .logo-ex"]));
    await page.click("#brandLogoRow .logo-on");
    assert.ok(await poll(h => __e2e.hash(["#brandLogoRow .logo-ex"]) !== h, lh0, 1500, 60), "el logo prendido cambia su ejemplo");
    assert.ok(await ev(() => LOGO_ON && document.querySelector("#logoOn").checked), "logo prendido");
    await wait(400);
    // el ejemplo se lee: el logo ocupa al menos 14 px de ancho en la esquina elegida (antes ~5 px)
    const lx = await ev(() => { const cv = document.querySelector("#brandLogoRow .logo-ex"), r = cv.getBoundingClientRect(); return { w: r.width, red: __e2e.count(cv, "#FF5A3C", 40), dpr: cv.width / r.width }; });
    assert.ok(lx.red / (lx.dpr * lx.dpr) >= 14 * 7, `logo legible en su ejemplo ${JSON.stringify(lx)}`);
    await shot("E4_logo");
    await page.click("#brandLogoRow .logo-on");
    await shot("E4_quien_sale");
    log(tag, "Quién sale y tu logo: «Dónde aparece» apagado con su razón; el logo en su ejemplo");

    // Formato: un clic cambia el formato del proyecto, su ejemplo, la vista grande y el resumen
    await toSec("stFormat");
    await page.click('#segFmt [data-v="1:1"]');
    await page.waitForFunction(() => FMT === "1:1" && PLAN.studio.fmt === "1:1", null, { timeout: 5000 });
    await wait(400);
    const fm = await ev(() => ({ chk: document.querySelector('#segFmt [data-v="1:1"]').getAttribute("aria-checked"), sel: document.querySelector("#segFmt .sel").dataset.v,
      ar: (r => +(r.width / r.height).toFixed(3))(document.querySelector("#stPvBox").getBoundingClientRect()), sum: [...document.querySelectorAll("#styleSummary .ss-line")].map(l => `${l.querySelector(".ss-k").textContent}=${l.querySelector(".ss-v").textContent}`) }));
    assert.ok(fm.chk === "true" && fm.sel === "1:1" && Math.abs(fm.ar - 1) < 0.02 && fm.sum.includes("Formato=Cuadrado 1:1 · Español"), JSON.stringify(fm));
    await shot("E5_formato_cuadrado");
    await page.click('#segFmt [data-v="9:16"]');
    await page.waitForFunction(() => FMT === "9:16", null, { timeout: 5000 });
    // «Margen desenfocado» (Avanzado): el video de prueba es vertical, ya llena el cuadro -> el control
    // se apaga, la nota lo dice y el ejemplo no promete un margen que no sale
    await ev(() => Estilo.setLevel("advanced")); await wait(800);
    const air = await ev(() => { const n = document.querySelector("#airNote"), sv = document.querySelector("#airRow .air-ex svg");
      return { dis: document.querySelector("#air").disabled, note: n && !n.hidden ? n.textContent : "", applied: FX.airApplied(), dim: sv && sv.dataset.dim === "1", m: sv ? +sv.dataset.air : -1 }; });
    assert.ok(air.dis && /ya llena el cuadro/.test(air.note) && air.applied === 0 && air.dim && air.m === 0, `margen con video vertical ${JSON.stringify(air)}`);
    rep.air = air;
    await ev(() => document.querySelector("#airRow").scrollIntoView({ block: "center" })); await wait(200);
    await shot("E5_margen_video_vertical");
    await ev(() => Estilo.setLevel("basic")); await wait(500);
    // la voz antes y después de mejorarla: se escucha y el video vuelve a donde estaba
    const vt0 = await ev(() => V.currentTime);
    await page.click('#enhanceRow .listen:first-child');
    const e4 = await poll(() => { const i = Estilo.Audio.info(); return i && i.kind === "voice" && i.t > 0.3 && !V.paused ? i : null; }, null, 5000, 100);
    assert.ok(e4, "la voz sin mejorar se escucha");
    await page.click('#enhanceRow .listen:first-child'); await wait(300);
    assert.ok(await ev(t => V.paused && Math.abs(V.currentTime - t) < 0.05, vt0), "el video vuelve a donde estaba");
    rep.format = fm;
    log(tag, "Formato: «Cuadrado 1:1» en un clic (vista grande 1:1); la voz antes y después se escucha");

    // D.5 el Brillo se ve MIENTRAS el video corre (Edición), no solo al pausar
    const playLuma = async () => {
      await blur(); await page.keyboard.press("2"); await wait(500);
      await ev(() => { V.pause(); V.currentTime = 6.4; });
      await wait(500);
      await page.click("#play");
      const ls = [];
      for (let i = 0; i < 5; i++) { await wait(180); ls.push(await ev(() => ({ l: __e2e.lumaCv(document.querySelector("#cv")), p: !V.paused, d: LookGL.stats().draws }))); }
      await ev(() => V.pause());
      return ls;
    };
    await ev(() => FX.edit(() => { PLAN.fx.grade.exposure = 0; }));
    await poll(() => FX.lookFresh(), null, 3000, 50);
    const la = await playLuma();
    await blur(); await page.keyboard.press("3"); await wait(400);
    await ev(() => Estilo.setLevel("advanced")); await wait(1200);
    const ex = '#stLook [data-setting="grade.exposure"] input[type="range"]';
    await ev(s => document.querySelector(s).scrollIntoView({ block: "center" }), ex);
    await ev(s => { const e = document.querySelector(s); e.value = "50"; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, ex);
    assert.equal(await ev(() => PLAN.fx.grade.exposure), 0.5);
    assert.match(await ev(s => document.querySelector(s).parentElement.querySelector(".hv").textContent, ex), /50 % más claro/);
    await poll(() => FX.lookFresh(), null, 3000, 50);
    const lb = await playLuma();
    const avg = a => a.reduce((x, y) => x + y.l, 0) / a.length;
    rep.playLuma = { exp0: +avg(la).toFixed(1), exp05: +avg(lb).toFixed(1), playing: lb.every(x => x.p), lookglDraws: lb[lb.length - 1].d - lb[0].d };
    assert.ok(lb.every(x => x.p) && la.every(x => x.p), "el video estaba corriendo");
    assert.ok(rep.playLuma.lookglDraws > 0, "el reproductor dibuja con las tablas del motor (LookGL)");
    assert.ok(avg(lb) - avg(la) >= 8, `el Brillo se ve mientras corre: ${JSON.stringify(rep.playLuma)}`);
    await shot("D5_brillo_reproduciendo");
    log(tag, "D.5 Brillo +50 visible mientras se reproduce", JSON.stringify(rep.playLuma));
    await blur(); await page.keyboard.press("Meta+z"); await wait(300);
    await ev(() => { FX.edit(() => { PLAN.fx.grade.exposure = 0; }); Estilo.setLevel("basic"); });

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
