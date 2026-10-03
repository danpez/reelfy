// Pruebas de la logica pura de la pestana Estilo (fxcore.js): resorte y pastilla del motor,
// contraste dentro de la pastilla, filas de color por estilo, cuadro y palabras de muestra y la copy
// de cada ajuste (scripts/motion/ui_copy.json). Datos FICTICIOS. Corre con: node --test spike/tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "..", "app", "static", "fxcore.js"));
const UI = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "scripts", "motion", "ui_copy.json"), "utf8"));

// orden de las filas de Estilo › Subtitulos (estilo.js CAPS_IDS)
const CAPS_IDS = ["captions.preset", "captions.color", "captions.accent", "captions.highlight", "captions.y", "captions.size",
  "captions.case", "captions.font", "captions.max_words", "captions.max_lines", "captions.kw_accent", "captions.recenter",
  "captions.stroke", "captions.shadow", "captions.hide_during_titles", "captions.x", "captions.box", "captions.enabled"];

test("spring: 0 al inicio, rebota por encima de 1 y se asienta en 1", () => {
  assert.equal(C.spring(0), 0);
  assert.equal(C.spring(-1), 0);
  let mx = 0;
  for (let t = 0; t < 2; t += 0.005) mx = Math.max(mx, C.spring(t));
  assert.ok(mx > 1.01, `sobrepaso ${mx}`);
  assert.ok(Math.abs(C.spring(2) - 1) < 0.01, C.spring(2));
  // envolvente monotona: |s - 1| <= exp(-z w t) / sqrt(1 - z^2)
  const z = 0.42, w = 2 * Math.PI * 3.2;
  for (let t = 0.01; t < 2; t += 0.01) assert.ok(Math.abs(C.spring(t) - 1) <= Math.exp(-z * w * t) / Math.sqrt(1 - z * z) + 1e-12, t);
  // amortiguado mas fuerte: no rebota tanto
  assert.ok(C.spring(0.3, 4.0, 0.78) > 0.9 && C.spring(0.3, 4.0, 0.78) < 1.05);
});

test("pillAt: aparece, crece y se DESLIZA entre palabras (no salta)", () => {
  const fs_ = 40, rects = [[100, 0, 200, 50], [260, 0, 380, 50], [440, 0, 520, 50]], st = [0.5, 0.8, 1.1];
  const cx = r => (r[0] + r[2]) / 2;
  assert.equal(C.pillAt(rects, st, 0.4, fs_), null);
  const p0 = C.pillAt(rects, st, 0.47, fs_);
  assert.equal(p0.i, 0);
  assert.ok(Math.abs(p0.w - 100 * 0.6) < 1e-9 && Math.abs(p0.r - 0.22 * fs_ * 0.6) < 1e-9, p0);
  const p1 = C.pillAt(rects, st, 0.85, fs_), c1 = p1.x + p1.w / 2;
  assert.equal(p1.i, 1);
  assert.ok(c1 > cx(rects[0]) && c1 < cx(rects[1]), `en medio: ${c1}`);
  const p2 = C.pillAt(rects, st, 1.05, fs_), c2 = p2.x + p2.w / 2, d = cx(rects[1]) - cx(rects[0]);
  assert.ok(Math.abs(c2 - cx(rects[1])) < 0.01 * d, `${c2} vs ${cx(rects[1])}`);
  assert.equal(p2.r, 0.22 * fs_);
  // la caja de cada palabra: tinta +- 0.2 em, cap + 0.19 em arriba, 0.24 em abajo
  assert.deepEqual(C.pillWordRect({ l: 2, r: 50 }, 100, 300, 30, 40), [100 + 2 - 8, 300 - 30 - 7.6, 100 + 50 + 8, 300 + 9.6]);
  // desplazamiento del grupo
  const g = C.pillAt(rects, st, 1.05, fs_, 12);
  assert.ok(Math.abs(g.y - p2.y - 12) < 1e-9);
});

test("contrastOn: letra negra o blanca con la formula del motor", () => {
  assert.equal(C.contrastOn("#FFD23F"), "#141414");
  assert.equal(C.contrastOn("#1F9D55"), "#FFFFFF");
  assert.equal(C.contrastOn("#FFFFFF"), "#141414");
  assert.equal(C.contrastOn("#111111"), "#FFFFFF");
  // umbral 0.55 de luminancia (0.2126 r + 0.7152 g + 0.0722 b): un gris de 0.55 exacto no pasa
  const v = Math.floor(0.55 * 255), hx = n => "#" + [n, n, n].map(x => x.toString(16).padStart(2, "0")).join("");
  assert.equal(C.contrastOn(hx(v)), "#FFFFFF");
  assert.equal(C.contrastOn(hx(v + 1)), "#141414");
});

test("captionColorRoles: exactamente la tabla 3.2", () => {
  const r = p => C.captionColorRoles(p).map(x => `${x.k}:${x.role}:${x.mode}`);
  assert.deepEqual(r("clean_bold"), ["color:text:swatches", "accent:active:linked"]);
  assert.deepEqual(r("karaoke"), ["color:text:swatches", "accent:fill:linked"]);
  assert.deepEqual(r("boxed"), ["highlight:pill:pill", "color:text:swatches"]);
  assert.deepEqual(r("minimal_small"), ["color:text:swatches"]);
  assert.deepEqual(r("bold_caps"), ["color:text:swatches", "accent:active:linked"]);
  assert.deepEqual(r("none"), []);
  assert.equal(C.captionStyleNote(UI, "boxed"), UI.notes["captions.boxed"]);
  assert.equal(C.captionStyleNote(UI, "karaoke"), null);
});

test("captionCardOf y highlightLinked", () => {
  assert.equal(C.captionCardOf({ enabled: false, preset: "boxed" }), "none");
  assert.equal(C.captionCardOf({ preset: "none" }), "none");
  assert.equal(C.captionCardOf({ preset: "karaoke" }), "karaoke");
  assert.equal(C.captionCardOf({}), "clean_bold");
  assert.equal(C.highlightLinked({ highlight: null }, "#1F9D55"), true);
  assert.equal(C.highlightLinked({}, "#1F9D55"), true);
  assert.equal(C.highlightLinked({ highlight: "#1f9d55" }, "#1F9D55"), true);
  assert.equal(C.highlightLinked({ highlight: "#FF5A3C" }, "#1F9D55"), false);
});

test("paintsAccent: los ejemplos de «Color de tu marca» usan elementos que SÍ toman tu color", () => {
  const G = "#1F9D55";
  // titulo: palabras destacadas sin color propio o con el de la marca (en cualquier caso de letra)
  assert.equal(C.paintsAccent("text", { preset: "title_pop", accent_words: ["simples"] }, G), true);
  assert.equal(C.paintsAccent("text", { preset: "title_pop", accent_words: ["simples"], accent: "#1f9d55" }, G), true);
  // con su propio color (blanco) no cambia al elegir otra marca: no sirve de ejemplo
  assert.equal(C.paintsAccent("text", { preset: "stack", accent_words: ["simples"], accent: "#FFFFFF" }, G), false);
  // sin palabras destacadas el color no se ve (salvo marcador y antetitulo)
  assert.equal(C.paintsAccent("text", { preset: "title_pop", accent_words: [], accent: G }, G), false);
  assert.equal(C.paintsAccent("text", { preset: "kicker", accent: G }, G), true);
  // graficos: algun color igual a la marca (lo que applyAccent recolorea)
  assert.equal(C.paintsAccent("graphics", { kind: "cta", color: "#FFFFFF", button_color: G }, G), true);
  assert.equal(C.paintsAccent("graphics", { kind: "lower_third", color: "#FF5A3C" }, G), false);
  assert.equal(C.paintsAccent("graphics", null, G), false);
  // y lo que paintsAccent elige es justo lo que applyAccent recolorea
  const plan = { fx: { accent: G, text: [{ preset: "stack", accent_words: ["a"], accent: "#FFFFFF" }, { preset: "title_pop", accent_words: ["b"], accent: G }] } };
  const pick = plan.fx.text.find(it => C.paintsAccent("text", it, G));
  C.applyAccent(plan, "#FFD23F");
  assert.equal(pick.accent, "#FFD23F");
  assert.equal(plan.fx.text[0].accent, "#FFFFFF");
});

test("invitación «Seguir»: el color del botón es el que pinta el motor y «Color» no es otro control", () => {
  // graphics._cta_follow: button_color, si no color, si no rojo; casi blanco -> rojo
  assert.equal(C.ctaButtonColor({ button_color: "#08a05c", color: "#FFD23F" }), "#08A05C");
  assert.equal(C.ctaButtonColor({ color: "#2EC4B6" }), "#2EC4B6");
  assert.equal(C.ctaButtonColor({}), C.CTA_BTN_FALLBACK);
  assert.equal(C.ctaButtonColor({ button_color: "#FFFFFF", color: "#2EC4B6" }), C.CTA_BTN_FALLBACK, "un botón blanco no se ve: rojo (no el otro color)");
  assert.equal(C.ctaButtonColor({ color: "#F4F4F4" }), C.CTA_BTN_FALLBACK);
  assert.ok(C.engineLum("#FFFFFF") > 0.85 && C.engineLum("#FFD23F") < 0.85 && C.engineLum("#111111") < 0.1);
  // «Seguir» esconde «Color» (solo respaldo del botón); los demás tipos de invitación lo usan
  assert.equal(C.paramHidden("graphics", "cta", { kind: "cta", variant: "follow" }, "color"), true);
  assert.equal(C.paramHidden("graphics", "cta", { kind: "cta" }, "color"), true, "sin variante = «Seguir»");
  assert.equal(C.paramHidden("graphics", "cta", { kind: "cta", variant: "follow" }, "button_color"), false);
  for (const v of ["link_below", "comment", "save", "share"]) assert.equal(C.paramHidden("graphics", "cta", { kind: "cta", variant: v }, "color"), false, v);
});

test("applyAccent conserva una pastilla propia y sigue a la marca si era la marca", () => {
  const mk = h => ({ fx: { accent: "#1F9D55", captions: { preset: "boxed", accent: "#1F9D55", highlight: h } } });
  const a = mk("#FF5A3C"); C.applyAccent(a, "#FFD23F");
  assert.equal(a.fx.captions.highlight, "#FF5A3C");
  assert.equal(a.fx.accent, "#FFD23F");
  const b = mk("#1F9D55"); C.applyAccent(b, "#FFD23F");
  assert.equal(b.fx.captions.highlight, "#FFD23F");
  const c = mk(null); C.applyAccent(c, "#FFD23F");
  assert.equal(c.fx.captions.highlight, null);
  assert.equal(c.fx.captions.accent, "#FFD23F");
});

test("los grupos aproximados respetan el estilo (Impacto: maximo 3 palabras)", () => {
  const words = "hoy te muestro como cuidar tus dientes en casa con tres pasos muy simples y rapidos".split(" ")
    .map((w, i) => ({ text: w, start: i * 0.3, end: i * 0.3 + 0.25 }));
  assert.equal(C.captionCfg({ preset: "bold_caps", max_words: 6 }).max_words, 3);
  const g = C.captionGroupsFor([words], { preset: "bold_caps", max_words: 6 });
  assert.ok(g.length > 3 && g.every(x => x.words.length <= 3), JSON.stringify(g.map(x => x.words.length)));
  const g2 = C.captionGroupsFor([words], { preset: "clean_bold", max_words: 6 });
  assert.ok(g2.some(x => x.words.length > 3));
});

function words(txt, t0, step) {
  return txt.split(" ").map((w, i) => ({ text: w, start: +(t0 + i * step).toFixed(3), end: +(t0 + i * step + step * 0.8).toFixed(3) }));
}

test("pickSampleShot: la toma mas oscura en la franja de los subtitulos", () => {
  const light = Array.from({ length: 16 }, (_, i) => (i >= 10 ? [208, 208, 208] : [90, 90, 90]));
  const dark = Array.from({ length: 16 }, (_, i) => (i >= 10 ? [48, 48, 48] : [200, 200, 200]));
  const plan = {
    duration: 12,
    phrases: [words("hoy te muestro como cuidar tus dientes en casa", 0.3, 0.5), words("con tres pasos muy simples y rapidos de hacer", 6.2, 0.5)],
    cuts: [], shots: [{ start: 0, end: 6, rows: light }, { start: 6, end: 12, rows: dark }],
  };
  assert.deepEqual(C.pickSampleShot(plan, 0.72), { index: 1, t0: 6.2 });
  // rows como texto JSON (asi llegan de planes viejos)
  const p2 = JSON.parse(JSON.stringify(plan)); p2.shots.forEach(s => { s.rows = JSON.stringify(s.rows); });
  assert.equal(C.pickSampleShot(p2, 0.72).index, 1);
  // arriba la toma A es la oscura
  assert.equal(C.pickSampleShot(plan, 0.2).index, 0);
  // sin rows: la primera toma con palabras
  const p3 = JSON.parse(JSON.stringify(plan)); p3.shots.forEach(s => { delete s.rows; });
  assert.equal(C.pickSampleShot(p3, 0.72).index, 0);
  // una toma tapada entera por un corte habilitado no cuenta
  const p4 = JSON.parse(JSON.stringify(plan)); p4.cuts = [{ start: 6, end: 12, enabled: true }];
  assert.equal(C.pickSampleShot(p4, 0.72).index, 0);
  const p5 = JSON.parse(JSON.stringify(p4)); p5.cuts[0].enabled = false;
  assert.equal(C.pickSampleShot(p5, 0.72).index, 1);
  // empate: la primera
  const p6 = JSON.parse(JSON.stringify(plan)); p6.shots[1].rows = light;
  assert.equal(C.pickSampleShot(p6, 0.72).index, 0);
});

test("sampleWindow: >= 6 palabras dichas, sin cortes, hasta 4 s y con sus tiempos", () => {
  const ph = [words("uno dos tres cuatro cinco seis siete ocho nueve diez once doce", 1.0, 0.45)];
  const cuts = [{ start: 1.4, end: 2.3, enabled: true }];       // tapa "dos" y "tres"
  const w = C.sampleWindow(ph, cuts, 1.0, 2.6);
  assert.ok(w.words.length >= 6, w.words.length);
  assert.ok(w.len <= 4 + 1e-9 && w.len > 2.6, w.len);
  assert.ok(!w.words.some(x => x.text === "dos" || x.text === "tres"));
  assert.equal(w.words[0].start, 1.0);
  assert.equal(w.t0, 1.0);
  assert.equal(w.t1, w.words[w.words.length - 1].end);
  const all = C.sampleWindow(ph, [], 1.0);
  assert.equal(all.len, 2.6);
  assert.ok(all.words.length >= 4);
});

test("nearestChoice y humanValue: palabras y unidades humanas, nunca decimales sueltos", () => {
  const y = UI.params["captions.y"], sz = UI.params["captions.size"];
  assert.equal(C.nearestChoice(y.basic_choices, 0.73)[1], "Abajo");
  assert.equal(C.humanValue(y.adv, 0.73), "73 % desde arriba");
  assert.equal(C.nearestChoice(sz.basic_choices, 0.064)[1], "Grande");
  assert.equal(C.humanValue(sz.adv, 0.064, { preset: "clean_bold" }), "6.4 % del ancho");
  assert.equal(C.humanValue(sz.adv, 0.064, { preset: "bold_caps" }), "8.2 % del ancho");
  assert.equal(C.humanValue({ scale: 100, fmt: "{v}", zero: "Normal", min: -100 }, 0), "Normal");
  assert.equal(C.humanValue({ scale: 100, fmt: "{v}", min: -100 }, 0.2), "+20");
  assert.equal(C.humanValue({ scale: 100, fmt: "{v}", min: -100 }, -0.17), "−17");
  const st = UI.params["captions.stroke"].basic_choices;
  assert.equal(C.nearestChoice(st, null)[1], "Según el estilo");
  assert.equal(C.nearestChoice(st, 4)[1], "Delgado");
  assert.equal(C.nearestChoice([["none", "Como lo dices"], ["upper", "MAYÚSCULAS"]], "upper")[1], "MAYÚSCULAS");
  const raw = /(?<![\d:,.])\d*\.\d+(?!\s*(%|s\b|×|x\b|GB|MB|min))/;
  for (const v of [0.1, 0.2, 0.5, 0.72, 0.9, 0.044, 0.052, 0.12]) {
    for (const a of [y.adv, sz.adv]) assert.ok(!raw.test(C.humanValue(a, v, { preset: "bold_caps" })), C.humanValue(a, v));
  }
});

test("fillExample: ejemplos con los datos reales, nunca con llaves", () => {
  const e = UI.params["director.intensity"];
  assert.equal(C.fillExample(e.ex_tpl, { nivel: "Media", dur: "1:30", titulos: 20, graficos: 12, efectos: 40 }, e.ex_ui),
    "Ej.: Con «Media», tu video de 1:30 lleva 20 títulos, 12 gráficos y 40 efectos");
  assert.equal(C.fillExample(e.ex_tpl, { nivel: "Media", dur: "1:30", titulos: 20 }, e.ex_ui), e.ex_ui);
  assert.ok(!/[{}]/.test(C.fillExample(e.ex_tpl, {}, e.ex_ui)));
  const m = UI.params["captions.max_words"];
  assert.equal(C.fillExample(m.ex_tpl, { grupos: "hoy te muestro · cómo cuidar" }, m.ex_ui), "Ej. con tu guion: «hoy te muestro · cómo cuidar»");
});

test("copyFor: etiqueta, ayuda y nivel segun el estilo, el kind o la pista", () => {
  assert.equal(C.copyFor(UI, "captions.accent", { preset: "karaoke" }).label, "Color de relleno");
  assert.equal(C.copyFor(UI, "captions.accent", { preset: "clean_bold" }).label, "Color de la palabra que dices");
  assert.equal(C.copyFor(UI, "text.accent", { preset: "highlight_marker" }).label, "Color del marcador");
  assert.equal(C.copyFor(UI, "common.intensity", { track: "effects" }).level, "basic");
  assert.equal(C.copyFor(UI, "common.intensity", { track: "text" }).level, "advanced");
  assert.equal(C.copyFor(UI, "captions.color", { preset: "boxed" }).help, "Las palabras que quedan fuera de la pastilla.");
  assert.equal(C.copyFor(UI, "captions.size", { preset: "bold_caps" }).note, UI.params["captions.size"].note_by.bold_caps);
  assert.equal(C.copyFor(UI, "captions.size", { preset: "clean_bold" }).note, null);
  assert.equal(C.copyFor(UI, "project.accent", {}).missing, false);
  assert.equal(C.copyFor(UI, "transcript.edit", {}).label, "Corregir el texto de los subtítulos");
  const miss = C.copyFor(UI, "nada.de.esto", { fallback: "X" });
  assert.equal(miss.missing, true); assert.equal(miss.label, "X");
});

test("visibleRows: Basico corto, solo los colores que usa el estilo y nunca lo oculto", () => {
  assert.deepEqual(C.visibleRows(UI, CAPS_IDS, "basic", { preset: "boxed" }),
    ["captions.preset", "captions.highlight", "captions.color", "captions.y", "captions.size", "captions.case"]);
  assert.deepEqual(C.visibleRows(UI, CAPS_IDS, "basic", { preset: "clean_bold" }),
    ["captions.preset", "captions.color", "captions.accent", "captions.y", "captions.size", "captions.case"]);
  const ms = C.visibleRows(UI, CAPS_IDS, "advanced", { preset: "minimal_small" });
  assert.ok(!ms.includes("captions.accent") && !ms.includes("captions.highlight") && ms.includes("captions.color"));
  assert.deepEqual(C.visibleRows(UI, CAPS_IDS, "basic", { preset: "none" }), ["captions.preset", "captions.y", "captions.size", "captions.case"]);
  for (const lv of ["basic", "advanced"]) for (const p of ["clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps", "none"]) {
    const r = C.visibleRows(UI, CAPS_IDS, lv, { preset: p });
    assert.ok(!r.includes("captions.x") && !r.includes("captions.box") && !r.includes("captions.enabled"), `${lv} ${p}`);
  }
  assert.ok(C.visibleRows(UI, CAPS_IDS, "advanced", { preset: "boxed" }).includes("captions.font"));
});

test("presetFromPlan guarda el color de la marca", () => {
  const p = C.presetFromPlan({ fx: { accent: "#1F9D55", captions: { preset: "boxed" }, grade: { look: "natural" }, music: { mood: "chill" } } }, { style: "minimal", intensity: 0.4 });
  assert.equal(p.accent, "#1F9D55");
  assert.equal(p.captions.preset, "boxed");
  assert.equal(p.style, "minimal");
  assert.equal(p.music.mood, "chill");
  assert.equal(C.presetFromPlan({ fx: {} }).accent, C.DEFAULT_ACCENT);
});

test("colorName: nombres en palabras", () => {
  assert.equal(C.colorName("#1F9D55"), "verde");
  assert.equal(C.colorName("#FFD23F"), "amarillo");
  assert.equal(C.colorName("#FF5A3C"), "rojo");
  assert.equal(C.colorName("#FFFFFF"), "blanco");
  assert.equal(C.colorName("#111111"), "negro");
  assert.equal(C.colorName("#7C5CFF"), "morado");
  assert.equal(C.colorName("nada"), "");
});

// ------------------------------------------------------------------ color del video (fase 2)
const ID = Array.from({ length: 256 }, (_, i) => i);
/** Tablas ficticias como las de grade.lut_payload (identidad salvo lo que se pida). */
const payload = o => Object.assign({ look: "natural", enabled: true, lut: { r: ID, g: ID, b: ID }, sat: null, mono: false, mix: null, bw: null,
  vig: { amount: 0, cx: 0.5, cy: 0.46, shape: "ellipse" }, base: { contrast: 0.18, vig: 0.16, clarity: 0.2 }, per_shot: [] }, o || {});
const shape = n => Array.from({ length: n }, (_, i) => 0.05 + 0.83 * Math.pow(i / (n - 1), 1.1));
const bwOf = () => ({ shape: shape(1024), contrast: 1.5, lift: 0.05, face: 0.64, scene: 0.46, face_k: 0.85, scene_k: 0.5, glow: new Array(256).fill(0) });

test("vignetteAt: el centro queda igual, las orillas se oscurecen y 0 no hace nada", () => {
  assert.equal(C.vignetteAt(0.5, 0.46, 0.3), 1);
  assert.equal(C.vignetteAt(0, 0, 0), 1);
  const corner = C.vignetteAt(0, 0, 0.3), edge = C.vignetteAt(0, 0.46, 0.3);
  assert.ok(corner < edge && edge <= 1 && corner >= 0, `${corner} ${edge}`);
  assert.ok(C.vignetteAt(0, 0, 0.3) < C.vignetteAt(0, 0, 0.16), "mas cantidad, mas oscuro");
  // simetrica a los lados del centro (regla de simetria)
  for (const v of [0.1, 0.46, 0.9]) assert.ok(Math.abs(C.vignetteAt(0.2, v, 0.3) - C.vignetteAt(0.8, v, 0.3)) < 1e-12);
  // B/N: centrada en la cara
  assert.equal(C.vignetteBwAt(0.4, 0.3, 0.5, 0.4, 0.3), 1);
  assert.ok(C.vignetteBwAt(0.95, 0.95, 0.5, 0.4, 0.3) < 0.8);
  assert.deepEqual(C.lookVigCenter({ vig: { shape: "bw", cx: 0.5, cy: 0.45, face_dy: 0.12, max_cy: 0.8 } }, [0.3, 0.75, 0.1, 0.1]), [0.3, 0.8]);
  assert.deepEqual(C.lookVigCenter({ vig: { shape: "bw", cx: 0.5, cy: 0.45 } }, null), [0.5, 0.45]);
  assert.deepEqual(C.lookVigCenter({ vig: { shape: "ellipse", cx: 0.5, cy: 0.46 } }, [0.3, 0.3, 0.1, 0.1]), [0.5, 0.46]);
});

test("lookTotal: lo del estilo de color + lo que sumas", () => {
  const b = { contrast: 0.18, vig: 0.16, clarity: 0.2 };
  assert.equal(+C.lookTotal(b, { contrast: 0.1 }, "contrast").toFixed(4), 0.28);
  assert.equal(C.lookTotal(b, {}, "contrast"), 0.18);
  assert.equal(C.lookTotal(b, { contrast: 0.9 }, "contrast"), 0.95, "tope del motor");
  assert.equal(+C.lookTotal(b, { vignette: 0.12 }, "vignette").toFixed(4), 0.28);
  assert.equal(C.lookTotal(b, { vignette: 0.95 }, "vignette"), 1);
  assert.equal(C.lookTotal(b, {}, "clarity"), 0.2, "vacio = la del estilo");
  assert.equal(C.lookTotal(b, { clarity: 0 }, "clarity"), 0);
  assert.equal(C.lookTotal(null, null, "contrast"), 0);
});

test("lookVariants: ejemplos Menos · Así · Más alrededor del valor de ahora y dentro del rango", () => {
  const v = C.lookVariants({ exposure: -0.17, saturation: 1.9 }, { clarity: 0.2 });
  assert.deepEqual(v.exposure, [-0.67, 0.33]);
  assert.deepEqual(v.saturation, [1.4, 2]);
  assert.deepEqual(v.temperature, [-0.7, 0.7]);
  assert.deepEqual(v.strength, [0.5, 1.5]);
  assert.deepEqual(v.clarity, [0, 0.5]);
  const lo = C.lookVariants({ exposure: -1, contrast: -0.5 }, {});
  assert.deepEqual(lo.exposure, [-1, -0.5]);
  assert.deepEqual(lo.contrast, [-0.5, -0.25]);
  for (const k of Object.keys(v)) for (const x of v[k]) assert.ok(Number.isFinite(x) && String(x).length < 8, `${k} ${x}`);
});

test("lookShotAt / lookTablesAt: cada toma con sus tablas (igualar tomas)", () => {
  const T1 = { start: 0, end: 3, r: ID.map(i => Math.min(255, i + 10)), g: ID, b: ID }, T2 = { start: 3, end: 9, r: ID, g: ID, b: ID.map(i => Math.max(0, i - 10)) };
  const p = payload({ per_shot: [T1, T2] });
  assert.equal(C.lookShotAt(p, 1), 0);
  assert.equal(C.lookShotAt(p, 3), 1);
  assert.equal(C.lookShotAt(p, 12), -1);
  assert.equal(C.lookShotAt(p, null), -1);
  assert.equal(C.lookTablesAt(p, 1), T1);
  assert.equal(C.lookTablesAt(p, 12), p.lut);
  const pp = payload();
  assert.equal(C.lookTablesAt(pp, 1), pp.lut, "sin tablas por toma: las generales");
  // applyLook usa las de la toma
  const px = () => new Uint8ClampedArray([100, 100, 100, 255]);
  const a = C.applyLook(p, px(), 1, 1, { t: 1 }), b = C.applyLook(p, px(), 1, 1, { t: 5 }), c = C.applyLook(p, px(), 1, 1, { t: 20 });
  assert.deepEqual([...a], [110, 100, 100, 255]);
  assert.deepEqual([...b], [100, 100, 90, 255]);
  assert.deepEqual([...c], [100, 100, 100, 255]);
});

test("applyLook: identidad sin cambios, apagado no toca nada y la vinieta oscurece solo las orillas", () => {
  const W = 20, H = 36, mk = () => { const d = new Uint8ClampedArray(W * H * 4); for (let i = 0; i < d.length; i += 4) { d[i] = 180; d[i + 1] = 120; d[i + 2] = 60; d[i + 3] = 255; } return d; };
  assert.deepEqual([...C.applyLook(payload(), mk(), W, H)], [...mk()]);
  const off = mk(); C.applyLook(payload({ enabled: false, lut: { r: ID.map(() => 0), g: ID, b: ID } }), off, W, H);
  assert.deepEqual([...off], [...mk()]);
  const d = C.applyLook(payload({ vig: { amount: 0.3, cx: 0.5, cy: 0.46, shape: "ellipse" } }), mk(), W, H);
  const at = (x, y) => d[(y * W + x) * 4];
  assert.equal(at(10, 16), 180, "centro");
  assert.ok(at(0, 0) < 150, `esquina ${at(0, 0)}`);
  // saturacion: matriz identidad = sin cambio; gris = sin color
  const g = C.applyLook(payload({ sat: [[0.2126, 0.7152, 0.0722], [0.2126, 0.7152, 0.0722], [0.2126, 0.7152, 0.0722]] }), mk(), W, H);
  assert.equal(g[0], g[1]); assert.equal(g[1], g[2]);
});

test("B/N: ganancia de la cara o de la escena y su tabla", () => {
  const bw = bwOf(), W = 64, H = 64;
  const dark = new Uint8Array(W * H).fill(40), light = new Uint8Array(W * H).fill(200);
  assert.ok(C.bwGain(dark, W, H, null, bw) > 1, "escena oscura: aclara");
  assert.ok(C.bwGain(light, W, H, null, bw) < 1, "escena clara: oscurece");
  assert.ok(C.bwGain(dark, W, H, null, bw) <= Math.pow(1.8, 0.5) + 1e-9 && C.bwGain(light, W, H, null, bw) >= Math.pow(0.55, 0.5) - 0.01);
  // con cara: mide la cara (no la escena)
  const g = new Uint8Array(W * H).fill(200);
  for (let y = 20; y < 40; y++) for (let x = 20; x < 44; x++) g[y * W + x] = 50;
  const face = [0.5, 0.42, 0.4, 0.3];
  assert.ok(C.bwGain(g, W, H, face, bw) > 1 && C.bwGain(g, W, H, null, bw) < 1);
  assert.equal(C.bwGainOf(0.3, true, bw), C.bwGain(new Uint8Array(W * H).fill(Math.round(0.3 * 255)), W, H, face, bw));
  // la tabla es monotona y con mas ganancia, mas clara
  const t1 = C.bwTable(bw, 1), t2 = C.bwTable(bw, 1.4);
  for (let i = 1; i < 256; i++) assert.ok(t1[i] >= t1[i - 1] && t2[i] >= t2[i - 1], i);
  assert.ok(t2[100] > t1[100]);
  // shapeAt / shapeInv son inversas
  for (const y of [0.1, 0.3, 0.46, 0.64, 0.8]) assert.ok(Math.abs(C.shapeAt(C.shapeInv(y, bw.shape), bw.shape) - y) < 1e-9, y);
  assert.equal(C.percentileOf([1, 2, 3, 4], 50), 2.5);
  assert.equal(C.percentileOf([10, 20, 30, 40, 50], 60), 34);
});

test("otherShot: otra toma de 1 s o mas fuera de los cortes (para igualar tomas)", () => {
  const plan = { duration: 20, shots: [{ start: 0, end: 4 }, { start: 4, end: 4.5 }, { start: 4.5, end: 10 }, { start: 10, end: 20 }],
    cuts: [{ start: 4.5, end: 6, enabled: true }] };
  assert.deepEqual(C.otherShot(plan, 0), { index: 2, t: 6 }, "la siguiente de >= 1 s, fuera del corte");
  assert.deepEqual(C.otherShot(plan, 3), { index: 2, t: 6 });
  assert.equal(C.otherShot({ shots: [{ start: 0, end: 5 }] }, 0), null, "una sola toma");
  assert.equal(C.otherShot({ shots: [{ start: 0, end: 5 }, { start: 5, end: 9 }], cuts: [{ start: 5, end: 9 }] }, 0), null, "la otra esta cortada");
});

// ------------------------------------------------------------------ fase 3: edicion con IA, musica, quien sale, formato
test("estilos de edición: cuatro tarjetas con subtítulos, título y color propios", () => {
  assert.deepEqual(C.DIRECTOR_ORDER, ["dinamico", "elegante", "minimal", "energetico"]);
  for (const k of C.DIRECTOR_ORDER) {
    const c = C.DIRECTOR_CARDS[k];
    assert.ok(c && C.CAPTION_PRESETS[c.captions.preset], k);
    assert.ok(C.TEXT_PRESETS[c.title], `${k}: título ${c.title}`);
    assert.ok(C.LOOK_ORDER.includes(c.look), `${k}: color ${c.look}`);
    assert.ok(UI.options["director.style"][k] && UI.options["director.style"][k].que_cambia, `${k}: «Qué cambia»`);
  }
  // «Solo subtítulos y cortes» no es un estilo que se pueda aplicar a una edición ya hecha
  assert.ok(!C.DIRECTOR_ORDER.includes("none"));
  assert.equal(C.DIRECTOR_CARDS.energetico.captions.preset, "bold_caps");
});

test("Cantidad de efectos: el ejemplo con el conteo real del plan (en singular cuando es 1)", () => {
  const tpl = UI.params["director.intensity"].ex_tpl, fb = UI.params["director.intensity"].ex_ui;
  const plan = { fx: { text: [{}, { enabled: false }], graphics: [{}, {}], effects: [] } };
  assert.deepEqual(C.directorCounts(plan), { titulos: 1, graficos: 2, efectos: 0 });
  assert.equal(C.directorExample(tpl, Object.assign({ nivel: "Media", dur: "0:12" }, C.directorCounts(plan)), fb),
    "Ej.: Con «Media», tu video de 0:12 lleva 1 título, 2 gráficos y 0 efectos");
  assert.equal(C.directorExample(tpl, { nivel: "Mucha", dur: "1:30", titulos: 11, graficos: 1, efectos: 21 }, fb),
    "Ej.: Con «Mucha», tu video de 1:30 lleva 11 títulos, 1 gráfico y 21 efectos");
  // la plantilla del spec sigue igual (y sin llaves si falta un dato)
  assert.equal(C.directorExample(tpl, { nivel: "Media", dur: "1:30", titulos: 20, graficos: 12, efectos: 40 }, fb), fb);
  assert.equal(C.directorExample(tpl, { nivel: "Media" }, fb), fb);
  assert.deepEqual(C.directorCounts(null), { titulos: 0, graficos: 0, efectos: 0 });
});

test("música: origen que se oye, ritmo efectivo y su cambio", () => {
  assert.equal(C.musicSourceOf({ enabled: false, source: "ai" }), "none");
  assert.equal(C.musicSourceOf({ source: "none" }), "none");
  assert.equal(C.musicSourceOf({ source: "library" }), "library");
  assert.equal(C.musicSourceOf({}), "ai");
  assert.equal(C.musicSourceOf({ source: "rara" }), "ai");
  // 110 sin bpm_lock cuenta como automatico (music._coerce_cfg); con candado, es fijo
  assert.equal(C.musicBpmOf({ bpm: 110 }), 0);
  assert.equal(C.musicBpmOf({ bpm: 110, bpm_lock: true }), 110);
  assert.equal(C.musicBpmOf({ bpm: 85 }), 85);
  assert.equal(C.musicBpmOf({ bpm: 0, bpm_lock: true }), 0);
  assert.deepEqual(C.musicBpmSet(110), { bpm: 110, bpm_lock: true });
  assert.deepEqual(C.musicBpmSet(0), { bpm: 0, bpm_lock: false });
  // Ritmo «Medio» (110) se lee como Medio, no como Automatico
  const ch = UI.params["music.bpm"].basic_choices;
  assert.equal(C.nearestChoice(ch, C.musicBpmOf(Object.assign({}, C.musicBpmSet(110))))[1], "Medio");
  assert.equal(C.nearestChoice(ch, C.musicBpmOf({ bpm: 110 }))[1], "Automático");
});

test("tonalidad en español (nunca «A major» a la vista)", () => {
  assert.equal(C.keyscaleEs("A major"), "La mayor");
  assert.equal(C.keyscaleEs("Bb major"), "Si bemol mayor");
  assert.equal(C.keyscaleEs("f#-min"), "Fa sostenido menor");
  assert.equal(C.keyscaleEs("C minor"), "Do menor");
  assert.equal(C.keyscaleEs("H# lydian"), "");
  assert.equal(C.keyscaleEs(null), "");
  assert.equal(C.KEYSCALES.length, 24);
  const names = C.KEYSCALES.map(C.keyscaleEs);
  assert.ok(names.every(n => n && !/major|minor/i.test(n)) && new Set(names).size === 24, names.join(", "));
});

test("muestra de un ambiente: URL con el estilo de edición; automático por defecto", () => {
  assert.equal(C.moodSampleUrl("upbeat", "minimal"), "/fx/mood_sample/upbeat?style=minimal");
  assert.equal(C.moodSampleUrl(null, null), "/fx/mood_sample/auto?style=dinamico");
  // cada ambiente de la copy tiene muestra (y «Tu canción» no se ofrece: no hay donde subirla)
  for (const m of Object.keys(UI.options["music.mood"])) assert.match(C.moodSampleUrl(m, "dinamico"), new RegExp(`/fx/mood_sample/${m}\\?`));
});

test("quién sale: dónde aparecen tus datos, con sus valores por defecto y su copy", () => {
  assert.deepEqual(C.profileUseOf(null), { lower_third: true, follow_cta: true, booking_cta: true, end_card: true, handle_watermark: false });
  assert.deepEqual(C.profileUseOf({ use: { follow_cta: false, handle_watermark: true } }),
    { lower_third: true, follow_cta: false, booking_cta: true, end_card: true, handle_watermark: true });
  for (const [k, id] of C.PROFILE_USE) assert.ok(UI.ui[id] && UI.ui[id].label && UI.ui[id].help.length >= 12, `${k}: ${id}`);
});

test("logo: misma caja que el reproductor y el motor, con el mismo margen en su esquina", () => {
  const W = 1080, H = 1920;
  const br = C.logoRect("br", 0.14, W, H, 400, 200), tl = C.logoRect("tl", 0.14, W, H, 400, 200);
  assert.ok(Math.abs(br.w - 151.2) < 1e-9 && Math.abs(br.h - 75.6) < 1e-9);
  assert.ok(Math.abs(tl.x - (W - br.x - br.w)) < 1e-9, "margen izquierdo = derecho");
  assert.ok(Math.abs(tl.y - (H - br.y - br.h)) < 1e-9, "margen de arriba = de abajo");
  assert.equal(tl.x, W * 0.03);
  assert.equal(C.logoRect("tr", 0, W, H, 1, 1).w, W * 0.14, "sin tamaño: el de siempre");
});

test("formato: el orden de Estilo y la forma de cada uno", () => {
  assert.deepEqual(C.FORMAT_ORDER, ["9:16", "4:5", "1:1", "16:9", "original"]);
  assert.deepEqual([...C.FORMAT_ORDER].sort(), [...C.FORMATS].sort());
  for (const f of C.FORMAT_ORDER.slice(0, 4)) { const [a, b] = C.FORMAT_SHAPE[f]; assert.equal(`${a}:${b}`, f); }
  for (const f of C.FORMAT_ORDER) assert.ok(UI.strings[`fmt.${f}`] && UI.strings[`fmt.${f}.title`], f);
});

test("Música y Edición con IA: cada fila de Estilo tiene su copy (nombre, qué hace, nivel)", () => {
  const ids = ["director.style", "director.intensity", "director.redirect", "director.variation", "director.music_sync", "director.notes",
    "music.source", "music.track", "music.mood", "music.gain_db", "music.preview", "music.prompt", "music.bpm", "music.keyscale", "music.ending",
    "music.intro_fade", "music.fade_out", "music.duck_db", "music.voice_carve_db", "music.seed", "setup.profile", "profile.use", "brand.logo",
    "brand.preset", "project.format", "project.lang", "project.air", "proc.enhance"];
  for (const id of ids) {
    const cp = C.copyFor(UI, id, {});
    assert.ok(!cp.missing && cp.label && cp.help.length >= 12, id);
  }
  // en Basico: origen, ambiente, volumen y la musica de tu video; lo demas es Avanzado
  const mus = ids.filter(i => i.startsWith("music."));
  assert.deepEqual(C.visibleRows(UI, mus, "basic", {}), ["music.source", "music.track", "music.mood", "music.gain_db", "music.preview"]);
  assert.deepEqual(C.visibleRows(UI, ["director.style", "director.intensity", "director.redirect", "director.variation", "director.notes", "director.seed"], "basic", {}),
    ["director.style", "director.intensity", "director.redirect", "director.variation"]);
});

// ------------------------------------------------------------------ fase 4: el inspector y las demas pantallas
test("tiempo del inspector: m:ss.d para leer y lo que se escribe a segundos", () => {
  assert.equal(C.fmtTime(5.24), "0:05.2");
  assert.equal(C.fmtTime(62), "1:02.0");
  assert.equal(C.fmtTime(59.97), "1:00.0");
  assert.equal(C.fmtTime(-3), "0:00.0");
  assert.equal(C.parseTime("1:02.5"), 62.5);
  assert.equal(C.parseTime("0:05"), 5);
  assert.equal(C.parseTime("5,2"), 5.2);
  assert.equal(C.parseTime("12 s"), 12);
  for (const bad of ["", "abc", "1:75", "1:2:3", "-1"]) assert.equal(C.parseTime(bad), null, bad);
  // ida y vuelta: lo que se muestra se vuelve a leer igual
  for (const v of [0, 0.4, 2.9, 61.3, 600.1]) assert.ok(Math.abs(C.parseTime(C.fmtTime(v)) - Math.round(v * 10) / 10) < 1e-9, v);
});

test("copy del inspector: cada parametro de cada pista encuentra su fila (tipo, pista, comun, contador)", () => {
  assert.equal(C.paramCopyId(UI, { k: "y", track: "text", kind: "title_pop" }), "text.y");
  assert.equal(C.paramCopyId(UI, { k: "from", track: "text", kind: "counter" }), "text.counter.from");
  assert.equal(C.paramCopyId(UI, { k: "intensity", track: "effects", kind: "punch" }), "common.intensity");
  assert.equal(C.paramCopyId(UI, { k: "text.text", track: "cutaways", kind: "title_card" }), "cutaways.text.text");
  assert.equal(C.paramCopyId(UI, { k: "mood", group: "music" }), "music.mood");
  assert.equal(C.paramCopyId(UI, { k: "start", parent: "captions.y_ranges" }), "captions.y_ranges.start");
  // sin entrada: el ultimo id (copyFor la marca como faltante y el E2E la reporta)
  const miss = C.paramCopyId(UI, { k: "nada", track: "text", kind: "title_pop" });
  assert.equal(miss, "common.nada");
  assert.ok(C.copyFor(UI, miss, {}).missing);
  // «Fuerza» es Basico en efectos y Avanzado en lo demas
  assert.equal(C.copyFor(UI, "common.intensity", { kind: "punch", track: "effects" }).level, "basic");
  assert.equal(C.copyFor(UI, "common.intensity", { kind: "title_pop", track: "text" }).level, "advanced");
  // los selectores de tipo del inspector tienen su fila
  for (const tk of ["text", "graphics", "cutaways", "effects", "transitions", "sfx"]) {
    const cp = C.copyFor(UI, `item.kind.${tk}`, {});
    assert.ok(!cp.missing && cp.level === "basic" && cp.help.length >= 12, tk);
  }
});

test("solo los campos que usa cada tipo de tarjeta e invitación (graphics.py)", () => {
  assert.equal(C.variantShows("card", "price", "value"), true);
  assert.equal(C.variantShows("card", "price", "label"), false);
  assert.equal(C.variantShows("card", "booking", "platform"), true);
  assert.equal(C.variantShows("card", "stat", "icon"), false);
  assert.equal(C.variantShows("card", undefined, "value"), true);          // sin tipo: «Genérica»
  assert.equal(C.variantShows("card", "price", "title"), true);            // comunes a todas
  assert.equal(C.variantShows("cta", "follow", "handle"), true);
  assert.equal(C.variantShows("cta", "comment", "handle"), false);
  assert.equal(C.variantShows("cta", "comment", "label"), true);
  assert.equal(C.variantShows("cta", "link_below", "display"), true);
  assert.equal(C.variantShows("lower_third", null, "title"), true);
  // colores que el motor no lee en ese tipo: no se muestran (nunca un control que no hace nada)
  assert.equal(C.paramHidden("graphics", "card", { kind: "card" }, "color"), true);
  assert.equal(C.paramHidden("graphics", "card", { kind: "card" }, "accent"), false);
  assert.equal(C.paramHidden("graphics", "lower_third", { kind: "lower_third" }, "accent"), true);
  assert.equal(C.paramHidden("graphics", "lower_third", { kind: "lower_third" }, "color"), false);
  assert.equal(C.paramHidden("graphics", "sticker_text", { kind: "sticker_text" }, "color"), true);
  assert.equal(C.paramHidden("graphics", "cta", { kind: "cta", variant: "comment" }, "handle"), true);
  // un corte no dura ni tiene fuerza; un fogonazo si
  assert.equal(C.paramHidden("transitions", "cut", {}, "dur"), true);
  assert.equal(C.paramHidden("transitions", "flash", {}, "dur"), false);
  for (const v of Object.keys(C.VARIANT_FIELDS.card)) assert.ok(UI.options["graphics.variant"][v], `tarjeta ${v} con nombre`);
  for (const v of Object.keys(C.VARIANT_FIELDS.cta)) assert.ok(UI.options["graphics.variant"][v], `invitación ${v} con nombre`);
});

test("colores vacíos que pinta el motor con el color de tu marca", () => {
  for (const k of ["accent", "badge_color", "bg", "button_color"]) assert.equal(C.colorFromBrand("graphics", "card", k), true, k);
  assert.equal(C.colorFromBrand("graphics", "progress", "color"), true);
  assert.equal(C.colorFromBrand("graphics", "doodle", "color"), true);
  assert.equal(C.colorFromBrand("graphics", "icon", "color"), false);
  assert.equal(C.colorFromBrand("text", "title_pop", "color"), false);
  assert.equal(C.colorFromBrand("text", "title_pop", "accent"), true);
});

test("tamaños relativos al estilo (Chico · Mediano · Grande) en valores del motor", () => {
  assert.deepEqual(C.relChoices([[0.8, "Chico"], [1, "Mediano"], [1.25, "Grande"]], 0.09), [[0.072, "Chico"], [0.09, "Mediano"], [0.1125, "Grande"]]);
  assert.deepEqual(C.relChoices([[0.7, "Rápida"]], 0), [], "sin duración propia (un corte) no hay opciones");
  const t = UI.params["transitions.dur"].relative_choices;
  assert.deepEqual(C.relChoices(t, 0.3).map(x => x[1]), ["Rápida", "Normal", "Lenta"]);
  // la opcion que se ve elegida es la mas cercana (sin escribirla)
  assert.equal(C.nearestChoice(C.relChoices(t, 0.3), 0.33)[1], "Normal");
});

test("delante / detrás: arriba o abajo de los que se enciman", () => {
  const a = { id: "a", start: 0, end: 4, z: 0 }, b = { id: "b", start: 1, end: 3, z: 2 }, c = { id: "c", start: 5, end: 6, z: 9 };
  assert.equal(C.zStep([a, b, c], a, 1), 3);      // c no se encima: no cuenta
  assert.equal(C.zStep([a, b, c], b, -1), -1);
  assert.equal(C.zStep([a], a, 1), 1);
  assert.equal(C.zStep([a, b], b, 1), 2, "ya esta al frente: se queda");
});

test("secciones del inspector: la fuerza va en Animación; contenido, ubicación y estilo", () => {
  const P = ks => ks.map(k => ({ k }));
  const g = C.inspectorGroups(P(["text", "y", "size", "color", "anim_in", "anim_out", "intensity", "accent_words", "in_dur", "theme", "variant"]));
  assert.deepEqual(g.cont.map(x => x.k), ["text", "accent_words", "variant"]);
  assert.deepEqual(g.pos.map(x => x.k), ["y", "size"]);
  assert.deepEqual(g.anim.map(x => x.k), ["anim_in", "anim_out", "intensity", "in_dur"]);
  assert.deepEqual(g.style.map(x => x.k), ["color", "theme"]);
  for (const k of ["time", "content", "pos", "style", "anim", "sound"]) assert.ok(UI.strings[`insp.sec.${k}`], k);
});

test("valores de Avanzado en palabras y unidades en español (nunca Hz ni st)", () => {
  assert.equal(C.plainValue({ unit: "Hz" }, 12), "12 veces por segundo");
  assert.equal(C.plainValue({ unit: "st" }, -3), "−3 semitonos");
  assert.equal(C.plainValue({ unit: "x" }, 1.25), "×1.25");
  assert.equal(C.plainValue({ unit: "grados" }, 15), "15°");
  assert.equal(C.plainValue({ min: 0, max: 1 }, 0.35), "35 %");
  assert.equal(C.plainValue({ unit: "s" }, 0.4), "0.4 s");
  assert.equal(C.plainValue({ unit: "dB" }, -6), "−6 dB");
  assert.equal(C.plainValue({}, null), "");
  assert.equal(C.unitEs("Hz"), "veces por segundo");
  assert.equal(C.unitEs("px"), "px");
});

test("pantallas antes del editor: Inicio, Montar clips y Limpieza tienen su fila en la copy", () => {
  const ids = ["setup.files", "setup.guion", "setup.glossary", "setup.highlights", "setup.profile", "asm.takes", "asm.clip.volume", "asm.clip.mute",
    "asm.clip.fades", "asm.clip.trans", "asm.clip.ramp", "asm.image.dur", "asm.overlay", "clean.level", "clean.cats", "clean.sil", "clean.restore",
    "clean.cat.retoma", "clean.cat.arranque_fallido", "clean.cat.autocorreccion", "clean.cat.tartamudeo", "clean.cat.error", "clean.cat.digresion", "clean.cat.silencio"];
  for (const id of ids) { const cp = C.copyFor(UI, id, {}); assert.ok(!cp.missing && cp.help.length >= 12, id); }
  // el ejemplo del nivel de limpieza con los numeros reales
  const cp = C.copyFor(UI, "clean.level", {});
  assert.equal(C.fillExample(cp.ex_tpl, { nivel: "Normal", n: 4, dur: "2:54" }, cp.ex_ui), "Ej.: Normal · quita 4 tomas repetidas · queda 2:54");
  assert.equal(C.fillExample(cp.ex_tpl, { nivel: "Normal", dur: "2:54" }, cp.ex_ui), cp.ex_ui);
  // las opciones del montaje en la copy (sin decimales crudos: siempre con su unidad)
  for (const k of ["asm.fade.none", "asm.fade.soft", "asm.fade.slow", "asm.trans.cut", "asm.trans.fast", "asm.trans.normal", "asm.trans.slow", "asm.ov.tc", "asm.ov.bc", "hl.none"])
    assert.ok(UI.strings[k] && !/(?<![\d:,.])\d*\.\d+(?!\d)(?!\s*(%|s\b))/.test(UI.strings[k]), k);
});

test("item nuevo con ortografía (Sígueme, Título, Descripción)", () => {
  const S = { tracks: { graphics: { key: "kind", items: { cta: { params: [] }, card: { params: [] }, lower_third: { params: [] } } }, cutaways: { key: "kind", items: { title_card: { params: [] } } } } };
  assert.equal(C.newItem(S, "graphics", "cta", 0, 10, {}).label, "Sígueme");
  assert.equal(C.newItem(S, "graphics", "card", 0, 10, {}).title, "Título");
  assert.equal(C.newItem(S, "graphics", "lower_third", 0, 10, {}).subtitle, "Descripción");
  assert.equal(C.getPath(C.newItem(S, "cutaways", "title_card", 0, 10, {}), "text.text"), "Título");
});

// ================================================================ revision de claridad (hallazgos de la revision)
test("igualar tomas: se sigue preguntando por las tablas de cada toma (8 s y luego 30 s, sin rendirse)", () => {
  for (let n = 0; n < 40; n++) assert.equal(C.lookPollDelay(n), 8000, n);
  for (const n of [40, 41, 100, 1000]) assert.equal(C.lookPollDelay(n), 30000, n);
  assert.equal(C.lookPollDelay(undefined), 8000);
  // las primeras 40 cubren ~5 min y despues nunca deja de haber un siguiente intento
  let s = 0; for (let n = 0; n < 40; n++) s += C.lookPollDelay(n);
  assert.equal(s, 320000);
});

test("Limpieza: un solo nombre por tipo de corte (fila, interruptor, filtro, leyenda y etiqueta del guion)", () => {
  for (const k of C.TAKE_KINDS.concat(["silencio"])) {
    const lb = UI.ui["clean.cat." + k].label;
    assert.equal(C.TAKE_PLURAL[k], lb, k);
    // el filtro usa el mismo nombre o su comienzo (nunca otro)
    if (C.TAKE_CHIP[k]) assert.ok(lb.startsWith(C.TAKE_CHIP[k]), `${k}: «${C.TAKE_CHIP[k]}» / «${lb}»`);
    // la etiqueta del guion (singular) empieza como el nombre de la fila
    assert.equal(C.TAKE_LABEL[k].split(" ")[0].slice(0, 4), lb.split(" ")[0].slice(0, 4), k);
  }
  assert.ok(!/Error/.test(JSON.stringify([C.TAKE_LABEL.error, C.TAKE_PLURAL.error, C.TAKE_CHIP.error])));
});

test("«La del estilo» (letra vacía): la letra del estilo de edición, como el motor", () => {
  for (const [st, f] of Object.entries(C.STYLE_FONTS)) {
    const F = C.captionFonts({ director: { style: st } });
    assert.deepEqual(F, f, st);
    assert.equal(C.captionCfg({ preset: "clean_bold", font: null }, F).font, f.caption, st);
    assert.equal(C.captionCfg({ preset: "boxed" }, F).font, f.caption, st);
    assert.equal(C.captionCfg({ preset: "bold_caps", font: null }, F).font, f.title, st);
    assert.equal(C.captionCfg({ preset: "minimal_small", font: null }, F).font, "Poppins-SemiBold", st);
    assert.equal(C.captionCfg({ preset: "clean_bold", font: "Anton" }, F).font, "Anton", st);
  }
  assert.equal(C.captionFonts({}).caption, "Poppins-ExtraBold");
  assert.equal(C.captionFonts({ director: { style: "elegante" } }).caption, "Poppins-SemiBold");
  // llamadas de antes (solo la letra de titulo): igual que siempre
  assert.equal(C.captionCfg({ preset: "bold_caps" }, "Anton").font, "Anton");
  assert.equal(C.captionCfg({ preset: "clean_bold" }, "Anton").font, "Poppins-ExtraBold");
  assert.equal(C.captionCfg({ preset: "clean_bold" }).font, "Poppins-ExtraBold");
});

test("pastilla propia: se guarda en el plan, la marca nueva no la recolorea y no pasa a otro proyecto", () => {
  const mk = (h, own) => ({ fx: { accent: "#1F9D55", captions: { preset: "boxed", highlight: h } }, studio: own ? { pillOwn: true } : {} });
  // quitar «Igual al color de tu marca» deja la marca como color propio: el cambio de marca no la toca
  const a = mk("#1F9D55", true); C.applyAccent(a, "#7C5CFF");
  assert.equal(a.fx.captions.highlight, "#1F9D55");
  assert.equal(C.highlightLinked(a.fx.captions, C.projectAccent(a), C.pillOwn(a)), false);
  // sin marcarla propia, la misma pastilla sigue a la marca
  const b = mk("#1F9D55", false); C.applyAccent(b, "#7C5CFF");
  assert.equal(b.fx.captions.highlight, "#7C5CFF");
  assert.equal(C.highlightLinked(b.fx.captions, C.projectAccent(b), C.pillOwn(b)), true);
  // vacia = sigue a la marca aunque hubiera quedado la marca de antes
  assert.equal(C.highlightLinked({ highlight: null }, "#1F9D55", true), true);
  // otro proyecto (otro plan) no hereda la decision
  assert.equal(C.pillOwn({ fx: {} }), false);
  const p = { studio: { fmt: "9:16" } };
  C.setPillOwn(p, true); assert.deepEqual(p.studio, { fmt: "9:16", pillOwn: true });
  C.setPillOwn(p, false); assert.deepEqual(p.studio, { fmt: "9:16" });
});

test("«Color del texto» en Discreto pinta las tres palabras del dibujito (el estilo no resalta ninguna)", () => {
  assert.deepEqual(C.colorWhereWords("text", "minimal_small"), [0, 1, 2]);
  for (const p of ["clean_bold", "karaoke", "boxed", "bold_caps"]) assert.deepEqual(C.colorWhereWords("text", p), [0, 2], p);
  assert.deepEqual(C.colorWhereWords("active", "clean_bold"), [1]);
  assert.deepEqual(C.colorWhereWords("pill", "boxed"), [1]);
  assert.deepEqual(C.colorWhereWords("fill", "karaoke"), [0, 1]);
  // y coincide con la ayuda de la copy de ese estilo
  assert.match(C.copyFor(UI, "captions.color", { preset: "minimal_small" }).help, /Todas las palabras/);
});

test("ajustes que el motor ignora con un estilo: apagados con su nota (centrar en Karaoke y Pastilla; palabra clave en Karaoke)", () => {
  const want = { clean_bold: [true, true], karaoke: [false, false], boxed: [false, true], minimal_small: [true, true], bold_caps: [true, true] };
  for (const [p, [rc, kw]] of Object.entries(want)) {
    assert.equal(C.captionOptionApplies(p, "recenter"), rc, `${p} recenter`);
    assert.equal(C.captionOptionApplies(p, "kw_accent"), kw, `${p} kw_accent`);
    // cada uno que no aplica trae su nota en la copy (la fila dice por que)
    if (!rc) assert.ok(C.copyFor(UI, "captions.recenter", { preset: p }).note, `${p}: nota de centrar`);
    if (!kw) assert.ok(C.copyFor(UI, "captions.kw_accent", { preset: p }).note, `${p}: nota de palabra clave`);
  }
  assert.equal(C.captionOptionApplies("boxed", "stroke"), true);
  assert.equal(C.captionOptionApplies("no_existe", "recenter"), true);
});

test("Blanco y negro: Calidez, Tinte e Intensidad de los colores no hacen nada (apagados con su nota)", () => {
  for (const k of ["temperature", "tint", "saturation"]) {
    assert.equal(C.gradeOptionApplies("bw_contrast", k), false, k);
    assert.ok(C.copyFor(UI, "grade." + k, { kind: "bw_contrast" }).note, k);
    for (const l of C.LOOK_ORDER.filter(x => x !== "bw_contrast")) assert.equal(C.gradeOptionApplies(l, k), true, `${l} ${k}`);
  }
  for (const k of ["exposure", "contrast", "strength", "clarity", "vignette", "grain", "sharpen", "match_shots"]) assert.equal(C.gradeOptionApplies("bw_contrast", k), true, k);
});

test("La música de tu video: en Básico sin pulsos por minuto ni golpe principal (son de Avanzado)", () => {
  const T = (k, v) => C.fillExample("Ej.: " + UI.strings[k], v || {}, "").replace(/^Ej\.: /, "");
  const st = { file: "m.mp3", bpm: 88, drop: "0:30" };
  assert.equal(C.musicReadyText(st, "basic", T), "Lista.");
  assert.equal(C.musicReadyText(st, "advanced", T), "Lista: 88 pulsos por minuto. El golpe principal cae en 0:30.");
  assert.equal(C.musicReadyText({ file: "m.mp3" }, "advanced", T), "Lista.");
});

test("el texto de la música: solo el tuyo se ve; el del director (en inglés) nunca", () => {
  const plan = { fx: { music: { source: "ai", prompt: "warm elegant instrumental, soft piano" } }, studio: {} };
  assert.equal(C.musicPromptShown(plan), "");
  C.setMusicPrompt(plan, "cumbia alegre con acordeón");
  assert.equal(plan.fx.music.prompt, "cumbia alegre con acordeón");
  assert.equal(plan.studio.musicPrompt, "cumbia alegre con acordeón");
  assert.equal(C.musicPromptShown(plan), "cumbia alegre con acordeón");
  // otro estilo de edicion sin conservarlo: el campo queda vacio, no en ingles
  plan.fx.music.prompt = "upbeat modern pop instrumental";
  assert.equal(C.musicPromptShown(plan), "");
  // la marca vieja (un booleano) ya no cuenta: nunca muestra el texto del director
  assert.equal(C.musicPromptShown({ fx: { music: { prompt: "lofi beats" } }, studio: { musicPromptByUser: true } }), "");
  const p2 = { fx: {}, studio: { musicPromptByUser: true } }; C.setMusicPrompt(p2, "piano");
  assert.deepEqual(p2.studio, { musicPrompt: "piano" });
});

test("margen desenfocado: solo cambia un video más ancho que el formato", () => {
  assert.equal(C.airApplies({ w: 1920, h: 1080 }, "9:16"), true);
  assert.equal(C.airApplies({ w: 1080, h: 1920 }, "9:16"), false);
  assert.equal(C.airApplies({ w: 1080, h: 1080 }, "9:16"), true);
  assert.equal(C.airApplies({ w: 1080, h: 1920 }, "4:5"), false);
  assert.equal(C.airApplies({ w: 1920, h: 1080 }, "16:9"), false);
  assert.equal(C.airApplies({ w: 1920, h: 1080 }, "original"), false);
  assert.equal(C.airApplies(null, "9:16"), true);
  // +-5 % es «igual»: no deja margen
  assert.equal(C.airApplies({ w: 1000, h: 1700 }, "9:16"), false);
});

test("Edición con IA: las tarjetas y «Qué cambia» muestran lo que el director conserva", () => {
  // el caso de la revision: Pastilla roja y un Natural ajustado, dirigido como Dinámico -> Elegante
  const plan = { director: { style: "dinamico" }, fx: {
    captions: { enabled: true, preset: "boxed", highlight: "#FF5A3C", font: "Poppins-ExtraBold", color: "#FFFFFF", case: "none", max_words: 3, y: 0.73, size: 0.064 },
    grade: { enabled: true, look: "natural", match_shots: true, vignette: 0.15, grain: 0.022, sharpen: 0.15 } } };
  const o = C.directorOutcome(plan, "elegante");
  assert.equal(o.captions.preset, "boxed"); assert.equal(o.captions.highlight, "#FF5A3C");
  assert.equal(o.captions.font, "Poppins-Bold"); assert.equal(o.captions.size, 0.058);   // lo del estilo nuevo
  assert.deepEqual(o.capsKept.sort(), ["highlight", "preset"]);
  assert.equal(o.gradeKept, true); assert.equal(o.grade.look, "natural");
  const names = { preset: k => UI.options["captions.preset"][k].label, look: k => UI.options["grade.look"][k].label };
  const T = (k, v) => C.fillExample("Ej.: " + UI.strings[k], v || {}, "").replace(/^Ej\.: /, "");
  const qc = C.directorQueCambia(UI.options["director.style"].elegante, o, names, T);
  assert.ok(qc.startsWith("Tus subtítulos se quedan en «Pastilla de color»"), qc);
  assert.ok(qc.includes("tu color se queda en «Natural»") && !qc.includes("color Cine") && !qc.includes("«Palabra en color»"), qc);
  // sin cambios propios: lo del estilo nuevo, y «Qué cambia» tal cual
  const plain = { director: { style: "dinamico" }, fx: { captions: { enabled: true, preset: "clean_bold", font: "Poppins-ExtraBold", color: "#FFFFFF", case: "none", max_words: 3 },
    grade: { enabled: true, look: "warm_clean", match_shots: true, vignette: 0.15, grain: 0.022, sharpen: 0.15, exposure: 0 } } };
  const o2 = C.directorOutcome(plain, "elegante");
  assert.equal(o2.captions.preset, "clean_bold"); assert.equal(o2.gradeKept, false); assert.equal(o2.grade.look, "cinematic");
  assert.deepEqual(o2.capsKept, []);
  assert.equal(C.directorQueCambia(UI.options["director.style"].elegante, o2, names, T), UI.options["director.style"].elegante.que_cambia);
  // el estilo de ahora con color propio: «Qué cambia con Dinámico» ya no promete «color Cálido»
  const o3 = C.directorOutcome(plan, "dinamico");
  assert.ok(!C.directorQueCambia(UI.options["director.style"].dinamico, o3, names, T).includes("color Cálido"));
  // subtitulos con candado: se quedan completos
  const lk = C.directorOutcome(Object.assign({}, plan, { director: { style: "dinamico", locked: ["captions"] } }), "energetico");
  assert.equal(lk.captions.preset, "boxed"); assert.equal(lk.captions.size, 0.064);
  // cada «Qué cambia» trae sus renglones de subtitulos y de color
  for (const k of C.DIRECTOR_ORDER) { const op = UI.options["director.style"][k]; assert.ok(op.que_cambia.split(" · ").includes(op.qc_caps) && op.que_cambia.split(" · ").includes(op.qc_look), k); }
});

test("subtítulos en vivo con las curvas del motor (capdraw.js): resortes, entrada out_expo y salida", () => {
  global.window = { FXCore: C };
  require(path.join(__dirname, "..", "..", "app", "static", "capdraw.js"));
  const CD = global.window.CapDraw, fs = 56;
  // pop / pop_strong: resorte del motor (no out_back)
  for (const u of [0.02, 0.05, 0.1, 0.2, 0.4]) {
    assert.ok(Math.abs(CD.wordAnim("pop", u, fs).sc - (0.8 + 0.2 * C.spring(u, 3.4, 0.6))) < 1e-12, u);
    assert.ok(Math.abs(CD.wordAnim("pop_strong", u, fs).sc - (0.7 + 0.3 * C.spring(u, 3.0, 0.62))) < 1e-12, u);
  }
  assert.ok(Math.abs(CD.wordAnim("pop", 0.05, fs).sc - 0.871) < 0.002, CD.wordAnim("pop", 0.05, fs).sc);
  // entrada del grupo: 1 - out_expo
  assert.ok(Math.abs(CD.groupIn(0.03, fs).dy - (1 - (1 - Math.pow(2, -10 * 0.03 / 0.35))) * 0.22 * fs) < 1e-9);
  assert.equal(CD.groupIn(0.35, fs).dy, 0);
  // salida: se apaga con in_quad y encoge 4 %
  assert.deepEqual(CD.groupOut(0), { a: 1, sc: 1 });
  assert.ok(Math.abs(CD.groupOut(0.5).a - 0.75) < 1e-12 && Math.abs(CD.groupOut(1).sc - 0.96) < 1e-12);
  // recentrado con resorte: empieza centrado en la primera palabra y llega a 0 con la frase completa
  const L = { M: [{ l: 0, r: 40 }, { l: 0, r: 60 }, { l: 0, r: 30 }], xs: [0, 60, 140], words: [{ w: { start: 1.0 } }, { w: { start: 1.3 } }, { w: { start: 1.6 } }] };
  const r = { a: 0, b: 3, sc: 1, x0: 100 };
  // renglon de 100 a 270 (centro 185): con la primera palabra (100-140) se corre 65; con dos (100-220), 25
  const first = CD.recenterDx(L, r, 1.0);
  assert.ok(Math.abs(first - 65) < 1e-9, first);
  assert.ok(Math.abs(CD.recenterDx(L, r, 5.0)) < 0.01);
  const mid = CD.recenterDx(L, r, 1.33);       // a mitad del resorte: entre el centro de 1 y el de 2 palabras
  assert.ok(mid < first && mid > 25, mid);
});
