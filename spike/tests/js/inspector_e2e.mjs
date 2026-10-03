// E2E del inspector y de las demas pantallas con la fila de claridad (fase 4), en un navegador real
// (Playwright + Chrome), con raton y teclado de verdad.
// uso: REELFY_PLAYWRIGHT=<ruta a playwright-core> node tests/js/inspector_e2e.mjs <url base> [capturas]
// El servidor (tests/devserver.sh) sirve dos proyectos SINTETICOS (tests/test_estilo.py):
//  - insp_demo: el plan de Estilo con un titulo con palabras destacadas, un rotulo, un boton, un golpe
//    de zoom, un fogonazo entre las dos tomas y un sonido;
//  - clean_demo: un video con tomas repetidas, arranques en falso, tartamudeos y silencios (Limpieza).
// Revisa, a 1440x900 y 1200x760:
//  B. Claridad (cada control con nombre y que hace, ejemplo donde aplica, sin decimales crudos ni
//     unidades tecnicas en Basico, sin ingles ni jerga, sin emojis, texto >= 11 px, ayuda AA) en Básico
//     y en Avanzado: el inspector del titulo, el rotulo, el boton, el golpe de zoom, el fogonazo, el
//     sonido, la pista de musica y los subtitulos; Inicio; Montar clips (el clip, la transicion entre dos
//     clips y la imagen encima); Limpieza de tomas. Un efecto en Básico tiene <= 5 filas.
//  Galerias: cada tipo con su ejemplo animado (no en blanco), anchos y huecos iguales y el ultimo renglon
//     centrado; elegir otro tipo es UN clic y un paso de deshacer; el menu «Agregar» en rejilla.
//  C. Ningun control muerto en el inspector: cada control de Básico de las filas «En el video» del
//     titulo, el rotulo, el boton y el efecto cambia la vista grande (o esta apagado y dice por que).
//  E. Escuchar: un sonido desde su fila; «Escuchar un ejemplo» de Limpieza suena el corte aunque este
//     quitado; el volumen de un clip del montaje.
//  F. Simetria: filas con el mismo margen a los lados, sin desborde horizontal.
// Consola sin errores. Sale con 1 al primer fallo.
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { HELPERS, LINT } from "./e2e_claridad.mjs";

const require = createRequire(import.meta.url);
const PW = process.env.REELFY_PLAYWRIGHT;
if (!PW) { console.error("define REELFY_PLAYWRIGHT (ruta a playwright-core)"); process.exit(2); }
const { chromium } = require(PW);
const BASE = (process.argv[2] || "http://127.0.0.1:8941").replace(/\/$/, "");
const OUT = process.argv[3] || null;
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log("·", ...a);
const report = { viewports: {} };
const CHROME = ".lvlsw,.ins-sh,.ins-head,.caps-more";

const browser = await chromium.launch({ headless: true, channel: process.env.REELFY_PW_CHANNEL || "chrome" });
let exitCode = 0;
try {
  for (const [VW, VH] of [[1440, 900], [1200, 760]]) {
    const tag = `${VW}x${VH}`, rep = report.viewports[tag] = { lint: {}, rows: {} };
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
    const lint = async (name, root, level, chrome) => {
      // los ejemplos en su cuadro quieto (deterministas: un fogonazo no queda a medio destello)
      await ev(() => { if (window.Estilo && Estilo.Ticker) Estilo.Ticker.setTime(Estilo.Frames.win ? Estilo.Frames.win.words[1].start + 0.05 : 1); });
      await wait(250);
      // lo que esta fuera de la vista no se dibuja (rendimiento): cada ejemplo se mira una vez antes de revisarlo
      const pend = await ev(r => [...document.querySelectorAll(`${r} canvas`)].filter(c => c.offsetParent && (!c.width || __e2e.nonBg(c) < 0.02)).length, root);
      if (pend) {
        await ev(async r => { for (const c of document.querySelectorAll(`${r} canvas`)) { if (!c.offsetParent || (c.width && __e2e.nonBg(c) >= 0.02)) continue; c.scrollIntoView({ block: "center" }); await new Promise(q => setTimeout(q, 90)); } }, root);
        await wait(150);
      }
      const probs = await ev(LINT, { root, level, chrome: chrome || CHROME });
      await ev(() => { if (window.Estilo && Estilo.Ticker) Estilo.Ticker.setTime(null); });
      rep.lint[`${name}:${level}`] = probs.length;
      assert.deepEqual(probs, [], `B ${name} (${level}): ${probs.slice(0, 12).join(" | ")}`);
    };
    const sym = async root => {
      const s = await ev(r => {
        const R = document.querySelector(r), rows = [...R.querySelectorAll(".hrow")].filter(x => x.offsetParent);
        const pad = Math.max(0, ...rows.map(x => { const c = getComputedStyle(x); return Math.abs(parseFloat(c.paddingLeft) - parseFloat(c.paddingRight)); }));
        const g = __e2e.galSym(R);
        return { pad, gal: g, over: document.documentElement.scrollWidth - document.documentElement.clientWidth };
      }, root);
      assert.ok(s.pad <= 0.5 && s.gal.width <= 0.5 && s.gal.gap <= 0.5 && s.gal.lastRow <= 0.5 && s.over <= 0, `simetría ${root}: ${JSON.stringify(s)}`);
      return s;
    };

    // ---------------------------------------------------------------- I. Inicio
    await page.goto(`${BASE}/`);
    await page.waitForFunction(() => document.querySelector("#setupRows .hrow-help") && document.querySelector("#setupFilesRow .hrow-help"), null, { timeout: 30000 });
    await ev(HELPERS);
    await wait(1200);
    await lint("Inicio · archivos", "#setupFilesRow", "basic");
    await lint("Inicio", "#setupRows", "basic");
    rep.inicio = await sym("#s-setup .card");
    assert.deepEqual(await ev(() => __e2e.labels("#setupRows .hrow:not([hidden])")), ["Palabras difíciles de tu video", "¿Cuántos shorts sugerir?", "Quién sale en el video"]);
    await page.click('#hlSeg [data-v="0"]');
    assert.equal(await ev(() => document.querySelector("#highlights").value), "0", "«Ninguno» pide 0 shorts");
    await page.keyboard.press("ArrowRight");
    assert.equal(await ev(() => document.querySelector("#highlights").value), "1", "las flechas mueven la opción");
    await page.click('#hlSeg [data-v="2"]');
    assert.ok(await poll(() => __e2e.nonBg(document.querySelector("#homeProfileEx canvas")) > 0.02, null, 2000), "el ejemplo de «Quién sale» se ve");
    await shot("I_inicio");
    log(tag, "Inicio: filas con nombre, qué hace y ejemplo; «¿Cuántos shorts sugerir?» Ninguno · 1 · 2 · 3");

    // ---------------------------------------------------------------- II. Montar clips
    await ev(async () => {
      const b = await (await fetch("/source/insp_demo")).blob();
      await enterAssembly([new File([b], "demo.mp4", { type: "video/mp4" })]);
    });
    await page.waitForFunction(() => document.querySelector("#s-assembly.on") && ASM.clips.length >= 1 && ASM.sources.length >= 1, null, { timeout: 60000 });
    await ev(HELPERS);
    await ev(() => { asmAddClip(ASM.sources[0].id, true); asmAddOverlay(ASM.sources[0].id); asmSideMode("insp"); });
    for (const [what, sel] of [["clip", () => asmSelect("clip", ASM.clips[1]._id)], ["overlay", () => asmSelect("overlay", ASM.overlays[0]._id)]]) {
      await ev(sel);
      for (const lv of ["basic", "advanced"]) {
        await ev(l => { Estilo.setLevel(l); asmInspector(); }, lv);
        await wait(900);
        await lint(`Montar clips · ${what}`, "#asmInspector", lv);
        if (what === "clip" && lv === "advanced") {
          assert.ok(await ev(() => !!document.querySelector('#asmInspector [data-setting="asm.clip.trans"] canvas') && !!document.querySelector('#asmInspector [data-setting="asm.clip.fades"]')), "transición y fundidos con su ejemplo");
          await shot("II_montar_clip_avanzado");
        }
      }
    }
    // la transición entre dos clips: un clic la escribe (Fundido normal) y otro la quita (Corte)
    await ev(() => { asmSelect("clip", ASM.clips[1]._id); Estilo.setLevel("basic"); asmInspector(); });
    await wait(300);
    const trow = '#asmInspector [data-setting="asm.clip.trans"]';
    await page.click(`${trow} [role="radio"]:nth-child(3)`);
    assert.deepEqual(await ev(() => ASM.clips[1].trans), { type: "crossfade", dur: 0.6 });
    await page.click(`${trow} [role="radio"]:nth-child(1)`);
    assert.equal(await ev(() => ASM.clips[1].trans), undefined);
    // la imagen encima: «Arriba al centro» queda centrada (x = 0.5)
    await ev(() => { asmSelect("overlay", ASM.overlays[0]._id); asmInspector(); });
    await page.click('#asmInspector [data-setting="asm.overlay"] [role="radio"]:nth-child(1)');
    assert.ok(await ev(() => Math.abs(ASM.overlays[0].x - 0.5) < 1e-9 && ASM.overlays[0].y < 0.4), "arriba al centro, simétrica");
    await shot("II_montar_imagen");
    log(tag, "Montar clips: clip, transición e imagen encima en filas con ayuda y ejemplo");

    // ---------------------------------------------------------------- III. Inspector
    await page.goto(`${BASE}/?job=insp_demo`);
    await page.waitForFunction(() => window.FX && FX.isPro && FX.isPro() && FX.uiCopy && FX.uiCopy() && document.querySelector("#s-editor.on"), null, { timeout: 90000 });
    await ev(HELPERS);
    await ev(() => { FX.clean.setMode("edit"); FX.setExact(false); Estilo.setLevel("basic"); UNDO.length = 0; REDO.length = 0; try { localStorage.removeItem("reelfy.insp.closed"); } catch (e) { /* nada */ } });
    await wait(600);
    const at = async (track, id) => {
      await ev(([tk, id]) => {
        V.pause();
        if (tk === "music") FX.selectMusic(); else if (tk === "captions") { FX.select(null); SEL = { type: "captions" }; FX.inspect(SEL, true); } else FX.select(tk, id);
        FX.focusElem();
        const it = tk === "music" || tk === "captions" ? null : (PLAN.fx[tk] || []).find(x => x.id === id);
        // dentro del elemento: un titulo casi al final (todas sus palabras ya entraron con la voz)
        V.currentTime = it ? (it.start != null ? (tk === "effects" ? it.start + 0.3 : tk === "text" ? it.end - 0.25 : (it.start + it.end) / 2) : it.at) : 7.0;
      }, [track, id]);
      await wait(1500);
    };
    const SURF = [["text", "tx_demo", "titulo"], ["graphics", "gx_lt", "rotulo"], ["graphics", "gx_cta", "boton"], ["effects", "fx_punch", "golpe"],
      ["transitions", "tr_flash", "fogonazo"], ["sfx", "sf_pop", "sonido"], ["music", null, "musica"], ["captions", null, "subtitulos"]];
    for (const [track, id, name] of SURF) {
      await at(track, id);
      for (const lv of ["basic", "advanced"]) {
        await ev(l => Estilo.setLevel(l), lv);
        await wait(1300);
        await lint(`inspector ${name}`, "#inspBody", lv);
        const rows = await ev(() => [...document.querySelectorAll("#inspBody .hrow[data-setting]")].filter(r => r.offsetParent && !r.parentElement.closest(".hrow")).map(r => r.dataset.setting));
        rep.rows[`${name}:${lv}`] = rows;
        if (track === "effects" && lv === "basic") assert.ok(rows.length <= 5, `un efecto en Básico tiene ${rows.length} filas: ${rows}`);
        if (lv === "basic" && !["music", "captions"].includes(track)) assert.ok(rows.includes(`item.kind.${track}`) && rows.includes("item.actions"), `${name}: tipo y acciones arriba ${rows}`);
        if (lv === "basic") await sym("#inspBody");
        if (["text", "effects", "transitions", "sfx"].includes(track)) await shot(`III_${name}_${lv}`);
      }
      await ev(() => Estilo.setLevel("basic"));
      log(tag, `inspector ${name}: claridad en Básico y Avanzado`, JSON.stringify(rep.rows[`${name}:basic`]));
    }

    // galerias: tu elemento en cada tipo (no en blanco); un clic cambia el tipo con un paso de deshacer
    await at("text", "tx_demo");
    const gal = await poll(() => { const cs = [...document.querySelectorAll('#inspBody [data-setting="item.kind.text"] .kcard canvas')]; return cs.length === 10 && cs.every(c => c.width && __e2e.nonBg(c) > 0.02) ? cs.length : 0; }, null, 3000);
    assert.equal(gal, 10, "los diez estilos de título con su ejemplo");
    const u0 = await ev(() => UNDO.length);
    await page.click('#inspBody [data-setting="item.kind.text"] .kcard[data-v="stack"]');
    await wait(600);
    assert.equal(await ev(() => PLAN.fx.text[0].preset), "stack");
    assert.equal(await ev(() => UNDO.length), u0 + 1, "un clic = un paso de deshacer");
    assert.equal(await ev(() => document.querySelector('#inspBody .kcard[data-v="stack"]').getAttribute("aria-checked")), "true");
    assert.equal(await ev(() => document.activeElement && document.activeElement.dataset.v), "stack", "el foco sigue en la tarjeta");
    await blur(); await page.keyboard.press("Meta+z"); await wait(500);
    assert.equal(await ev(() => PLAN.fx.text[0].preset), "title_pop", "⌘Z vuelve al estilo de antes");
    // las animaciones: una opcion es un clic (la galeria de «Cómo entra»)
    await page.click('#inspBody [data-setting="text.anim_in"] .kcard[data-v="rise"]');
    await wait(300);
    assert.equal(await ev(() => PLAN.fx.text[0].anim_in), "rise");
    await blur(); await page.keyboard.press("Meta+z"); await wait(400);
    log(tag, "galerías: diez estilos de título con su ejemplo; un clic cambia el tipo (un paso de deshacer)");

    // C. ningun control muerto en las filas «En el video» de Básico
    let checked = 0;
    for (const [track, id] of [["text", "tx_demo"], ["graphics", "gx_lt"], ["graphics", "gx_cta"], ["effects", "fx_punch"]]) {
      await at(track, id);
      const Q = '.seg-h button, .cw-sw, .cw-none, input.toggle';
      const ctls = await ev(Q => {
        const S = FX.selection()[0], it = (PLAN.fx[S.track] || []).find(x => x.id === S.id), kind = FXCore.itemKind(FX.schema(), S.track, it), out = [];
        document.querySelectorAll("#inspBody .hrow[data-setting]").forEach(row => {
          const cp = FXCore.copyFor(FX.uiCopy(), row.dataset.setting, { kind, track: S.track, preset: kind });
          if (cp.ex !== "en_video" || !row.offsetParent) return;
          row.querySelectorAll(Q).forEach((e, i) => out.push({ row: row.dataset.setting, i }));
        });
        return out;
      }, Q);
      for (const c of ctls) {
        const info = await ev(({ c, Q }) => {
          const row = document.querySelector(`#inspBody .hrow[data-setting="${c.row}"]`); if (!row) return null;
          const e = row.querySelectorAll(Q)[c.i]; if (!e) return null;
          if (e.getAttribute("aria-checked") === "true" || e.classList.contains("on")) return { skip: true };
          document.querySelectorAll("[data-e2e]").forEach(x => x.removeAttribute("data-e2e")); e.dataset.e2e = "x";
          const n = row.querySelector(".hrow-note");
          return { dis: e.disabled, note: !!(n && n.offsetParent) };
        }, { c, Q });
        if (!info || info.skip) continue;
        const cid = `${track}:${c.row}#${c.i}`;
        if (info.dis) { assert.ok(info.note, `control apagado sin decir por qué (${cid})`); checked++; continue; }
        await ev(() => document.querySelector('[data-e2e="x"]').scrollIntoView({ block: "center" }));
        await page.mouse.move(2, VH - 2);
        await wait(120);
        const h0 = await ev(() => __e2e.hash(["#cv"]));
        await page.click('[data-e2e="x"]');
        await page.mouse.move(2, VH - 2);
        const changed = await poll(h0 => __e2e.hash(["#cv"]) !== h0, h0, 600, 60);
        assert.ok(changed, `el control ${cid} no cambia la vista`);
        await blur(); await page.keyboard.press("Meta+z"); await wait(300);
        checked++;
      }
    }
    rep.deadControlChecks = checked;
    assert.ok(checked >= 30, `controles revisados: ${checked}`);
    log(tag, `C ningún control muerto en el inspector (${checked} revisados)`);

    // E. escuchar un sonido desde su fila (el elegido)
    await at("sfx", "sf_pop");
    await page.click('#inspBody [data-setting="item.kind.sfx"] .mopt[data-v="pop"] .listen');
    assert.ok(await poll(() => { const i = Estilo.Audio.info(); return i && i.kind === "sfx" && i.id === "pop"; }, null, 3000, 50), "«Escuchar» de un sonido suena");
    // otro grupo y otro sonido: un clic lo elige (un paso de deshacer)
    const us = await ev(() => UNDO.length);
    await page.click('#inspBody [data-setting="item.kind.sfx"] .seg-h [role="radio"]:nth-child(5)');
    await page.click('#inspBody [data-setting="item.kind.sfx"] .mopt:first-child .mopt-pick');
    await wait(500);
    assert.ok(await ev(() => PLAN.fx.sfx[0].name !== "pop" && FX.sfxCat()[PLAN.fx.sfx[0].name].category === "impact"), "otro sonido del grupo «Golpes»");
    assert.equal(await ev(() => UNDO.length), us + 1);
    await blur(); await page.keyboard.press("Meta+z"); await wait(400);
    assert.equal(await ev(() => PLAN.fx.sfx[0].name), "pop");
    log(tag, "E sonidos: se escuchan desde su fila; elegir otro es un clic");

    // el menu «Agregar» en rejilla: cada tipo con su miniatura animada
    await ev(() => { V.pause(); V.currentTime = 4.5; });
    await page.click("#addAny"); await page.click('.fxmenu.on [data-t="effects"]');
    await page.waitForSelector(".fxmenu.on .am-card canvas");
    const am = await poll(() => { const cs = [...document.querySelectorAll(".fxmenu.on .am-card canvas")]; return cs.length >= 10 && cs.every(c => c.width && __e2e.nonBg(c) > 0.02) ? cs.length : 0; }, null, 3000);
    assert.ok(am >= 10, `«Agregar» con miniaturas: ${am}`);
    const ams = await ev(() => __e2e.galSym(document.querySelector(".fxmenu.on")));
    assert.ok(ams.width <= 0.5 && ams.gap <= 0.5 && ams.lastRow <= 0.5, `rejilla de «Agregar» simétrica ${JSON.stringify(ams)}`);
    await lint("Agregar", ".fxmenu.on", "basic", ".am-card,.am-own,.am-row button");
    await shot("III_agregar");
    await page.keyboard.press("Escape"); await ev(() => document.querySelector(".fxmenu").classList.remove("on"));
    log(tag, `«Agregar» en rejilla: ${am} tipos con su miniatura animada`);

    // ---------------------------------------------------------------- IV. Limpieza de tomas
    await page.goto(`${BASE}/?job=clean_demo`);
    await page.waitForFunction(() => window.FX && FX.uiCopy && FX.uiCopy() && document.querySelector("#s-editor.on") && FX.clean, null, { timeout: 60000 });
    await ev(HELPERS);
    await ev(() => FX.clean.setMode("clean"));
    await page.waitForSelector('#cSum [data-setting="clean.level"] .hrow-help');
    await wait(500);
    await lint("Limpieza", "#cSum", "basic");
    await lint("Limpieza · pie", "#cleanPane .gfoot", "basic", ".gf-c");
    assert.match(await ev(() => document.querySelector('#cSum [data-setting="clean.level"] .hrow-ex').textContent), /^Ej\.: Normal · quita \d+ tomas repetidas · queda \d+:\d\d$/);
    // cada tipo de corte muestra SU nombre y SU explicación (antes «Tomas repetidas» decía «Qué quitar»);
    // «Qué quitar» encabeza la lista con los suyos; el interruptor, el filtro, la leyenda y la etiqueta
    // del guion usan el mismo nombre de la copy (una sola palabra por cosa)
    const cats = await ev(() => {
      const own = (el, c) => [...el.children].find(x => x.classList.contains(c));
      const row = r => { const tx = own(r, "hrow-txt"); return { label: tx && tx.querySelector(".hrow-lb label") ? tx.querySelector(".hrow-lb label").textContent : null, help: tx && own(tx, "hrow-help") ? own(tx, "hrow-help").textContent : null }; };
      const ui = FX.uiCopy();
      return { wrap: Object.assign(row(document.querySelector('#cSum [data-setting="clean.cats"]')), { copy: FXCore.copyFor(ui, "clean.cats", {}).label }),
        rows: [...document.querySelectorAll('#cSum [data-setting^="clean.cat."]')].map(r => { const k = r.dataset.setting.slice(10), cp = FXCore.copyFor(ui, r.dataset.setting, {}), sw = r.querySelector('[role="switch"]');
          return Object.assign(row(r), { k, copy: cp.label, copyHelp: cp.help, sw: sw && sw.getAttribute("aria-label"), plural: FXCore.TAKE_PLURAL[k] }); }),
        chips: [...document.querySelectorAll("#gChips [data-f], #gChips button")].map(b => b.textContent.trim()) };
    });
    assert.equal(cats.wrap.label, cats.wrap.copy, `«Qué quitar» con su propio nombre ${JSON.stringify(cats.wrap)}`);
    assert.ok(cats.rows.length >= 4, JSON.stringify(cats.rows));
    for (const c of cats.rows) assert.ok(c.label === c.copy && c.help === c.copyHelp && c.sw === c.copy && c.plural === c.copy, `tipo ${c.k}: ${JSON.stringify(c)}`);
    assert.ok(!cats.chips.some(t => /^Errores/.test(t)), `filtro sin «Errores» ${JSON.stringify(cats.chips)}`);
    // «Escuchar un ejemplo»: el primer corte de ese tipo suena (aunque este quitado) y otro clic lo para
    const ci = await ev(() => PLAN.cuts.findIndex(c => c && +c.end > +c.start && FXCore.cutKind(c) === "retoma"));
    await ev(() => document.querySelector('#cSum [data-setting="clean.cat.retoma"] .listen').scrollIntoView({ block: "center" }));
    await page.click('#cSum [data-setting="clean.cat.retoma"] .listen');
    const heard = await poll(ci => { const c = PLAN.cuts[ci]; return !V.paused && FX.clean.through === ci && V.currentTime > +c.start + 0.1 && V.currentTime < +c.end; }, ci, 8000, 50);
    assert.ok(heard, "el ejemplo suena dentro del corte (no se lo salta)");
    await page.click('#cSum [data-setting="clean.cat.retoma"] .listen');
    await wait(300);
    assert.ok(await ev(() => V.paused && FX.clean.through == null && !Estilo.Audio.info()), "otro clic lo para");
    await page.click("#gSil");
    assert.equal(await ev(() => FX.clean.sil), true, "«Ver los silencios en el guion» sigue funcionando");
    await page.click("#gSil");
    await ev(() => document.querySelector("#cSum").scrollIntoView({ block: "start" }));
    await shot("IV_limpieza");
    log(tag, "Limpieza: filas con nombre, qué hace y ejemplo; el ejemplo de un tipo de corte se escucha");

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
