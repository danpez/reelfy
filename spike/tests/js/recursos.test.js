// Pruebas de la logica pura de los RECURSOS DEL GUION (imagen o video flotante mientras se dice una
// linea), los CAMBIOS DE CAMARA CON ACELERACION y el COLOR DE ACENTO del proyecto (app/static/fxcore.js).
// Corre con: node --test spike/tests/js/recursos.test.js   (todo sintetico e inventado: una panaderia
// ficticia; el repo es publico)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "..", "app", "static", "fxcore.js"));

const near = (a, b, eps) => assert.ok(Math.abs(a - b) < (eps || 1e-6), `${a} != ${b}`);

// ---------------------------------------------------------------- marcas del guion
test("lineRefs: separador + verbo al final, corchetes y llaves; el texto queda sin la marca", () => {
  const cases = [
    ["Ya abrimos la panadería. - Agregar video_horno", "Ya abrimos la panadería.", ["video_horno"]],
    ["Hola.- AÑADIR Foto Pan.jpg", "Hola.", ["Foto Pan.jpg"]],
    ["Masa madre — insertar foto_masa, video_horno", "Masa madre", ["foto_masa", "video_horno"]],
    ["Cada mañana | Muestra la foto_pan.", "Cada mañana", ["foto_pan"]],
    ["Lanzamos [imagen_cartel]", "Lanzamos", ["imagen_cartel"]],
    ["Dos {logo} [Poner foto uno]", "Dos", ["logo", "foto uno"]],
    ["Hola [pausa]", "Hola", []],                          // acotacion: no es recurso
    ["[pausa] Hola", "[pausa] Hola", []],                  // solo al final de la linea
    ["un e-mail bien", "un e-mail bien", []],              // guion dentro de una palabra
    ["foto1 y foto2 - Pon foto1 y foto2", "foto1 y foto2", ["foto1", "foto2"]],
    ["sin nada", "sin nada", []],
  ];
  cases.forEach(([raw, text, names]) => assert.deepEqual(C.lineRefs(raw), { text, names }, raw));
  // "[nombre]" sin verbo que no parece archivo: recurso solo si coincide con un archivo elegido
  assert.deepEqual(C.lineRefs("Hola [Horno]", [{ name: "horno.mp4" }]).names, ["Horno"]);
  assert.deepEqual(C.lineRefs("Hola [Horno]").names, []);
});

test("scriptRefs: recursos por linea (una linea que solo trae la marca se suma a la anterior)", () => {
  const s = "Uno. - Agregar video_horno\n- Agregar foto_masa\nDos.\n\n{cartel_final}\nTres [video_horno]";
  const r = C.scriptRefs(s);
  assert.deepEqual(r.names, ["video_horno", "foto_masa", "cartel_final"]);
  // (la marca sola tras la linea en blanco tambien se suma a la ultima linea, como montage.parse_script)
  assert.deepEqual(r.lines.map(L => [L.i, L.text, L.names]), [[0, "Uno.", ["video_horno", "foto_masa"]], [1, "Dos.", ["cartel_final"]], [2, "Tres", ["video_horno"]]]);
  assert.equal(C.stripRefs("Uno. - Agregar x_1\n\nDos [y_2]"), "Uno.\n\nDos");
  assert.equal(C.withInserts("Uno.", ["video_horno"]), "Uno. - Agregar video_horno");
  assert.equal(C.withInserts("Uno. [video_horno]", ["video_horno"]), "Uno. [video_horno]", "ya la trae");
  assert.equal(C.withInserts("Uno.", []), "Uno.");
  // un nombre que no parece archivo (coincidio con uno elegido): entre corchetes, se relee igual sin archivos
  assert.equal(C.withInserts("Uno.", ["foto del local"]), "Uno. [Agregar foto del local]");
  assert.deepEqual(C.lineRefs(C.withInserts("Uno.", ["foto del local", "video_x"])), { text: "Uno.", names: ["foto del local", "video_x"] });
});

test("assetNorm / matchAsset: normalizado, igual primero, luego prefijo unico (al reves solo imagenes) y por id", () => {
  assert.equal(C.assetNorm("Video Horno-Leña.MP4"), "video_horno_lena");
  assert.equal(C.assetNorm("  «foto__pan»  "), "foto_pan");
  const files = [{ name: "otro.mov", id: "a1" }, { name: "Video Horno.mp4", id: "b2" }, { name: "foto_pan_dulce.jpg", id: "c3" }];
  assert.equal(C.matchAsset("video_horno", files), 1);
  assert.equal(C.matchAsset("foto_pan", files), 2, "prefijo unico");
  // al reves (el archivo es el principio del pedido) solo una IMAGEN: un video puede ser una toma
  assert.equal(C.matchAsset("video_horno_de_lena_grande", files), -1, "video: nunca al reves");
  assert.equal(C.matchAsset("foto_pan_dulce_de_hoy", files), 2, "imagen: al reves");
  assert.equal(C.matchAsset("b2", files), 1, "por id");
  assert.equal(C.matchAsset("video", [{ name: "video_a.mp4" }, { name: "video_b.mp4" }]), -1, "prefijo no unico");
  assert.equal(C.matchAsset("nada", files), -1);
});

test("classifyFiles: imagenes y videos nombrados son recursos; lo demas, tomas; lo que falta se dice", () => {
  const files = [{ name: "PAN_0101.MOV", type: "video/quicktime" }, { name: "video_horno.mp4", type: "video/mp4" },
    { name: "foto_masa.HEIC", type: "" }, { name: "PAN_0102.MOV", type: "video/quicktime" }, { name: "notas.txt", type: "text/plain" }];
  const r = C.classifyFiles(files, "Uno. - Agregar video_horno\nDos. [cartel_final]");
  assert.deepEqual(r.clips, [0, 3]);
  assert.deepEqual(r.assets, [1, 2]);
  assert.deepEqual(r.refs, [{ name: "video_horno", idx: 1 }, { name: "cartel_final", idx: -1 }]);
  assert.equal(C.fileKind({ name: "a.jpeg" }), "image");
  assert.equal(C.fileKind({ name: "a.bin", dur: 3 }), "video");
  assert.equal(C.fileKind({ name: "a.bin" }), null);
  // sin guion: toda imagen sigue siendo recurso
  assert.deepEqual(C.classifyFiles(files, "").assets, [2]);
});

// ---------------------------------------------------------------- colocacion de recursos
test("insertFit: imagen fija; video de 0.8x a 1.4x se ajusta; mas largo se recorta; mas corto dura lo suyo", () => {
  assert.deepEqual(C.insertFit("image", 0, 3), { fit: "hold", speed: 1, src_in: 0 });
  assert.deepEqual(C.insertFit("video", 3.6, 3), { fit: "speed", speed: 1.2, src_in: 0 });
  assert.deepEqual(C.insertFit("video", 2.7, 3), { fit: "speed", speed: 0.9, src_in: 0 });
  assert.deepEqual(C.insertFit("video", 9, 3, 2), { fit: "trim", speed: 1, src_in: 2 });
  assert.deepEqual(C.insertFit("video", 1, 3), { fit: "trim", speed: 1, src_in: 0, len: 1 });
});

/** Montaje SINTETICO con recursos: 3 partes; la 1a partida entre dos camaras (A: setup 1, B: setup 2),
    candidatos con line_times (segundos de la fuente) y los recursos que calcularia el montaje. */
function montageR() {
  const lines = [
    { id: "L01", text: "Abrimos hace un año.", inserts: ["video_horno"] },
    { id: "L02", text: "Horneamos cada mañana.", inserts: [] },
    { id: "L03", text: "Síguenos.", inserts: ["foto_masa", "cartel_final"] },
  ];
  const slots = [
    { id: "s01", lines: ["L01", "L02"], chosen: "c11", cands: [
      { id: "c11", sid: "A", in: 1.0, out: 7.0, setup: 1, flags: ["intercalado"], score: 0.9, line_times: { L01: [1.2, 3.8], L02: [4.1, 6.8] } },
      { id: "c12", sid: "B", in: 0.5, out: 6.5, setup: 2, flags: ["intercalado"], score: 0.88, line_times: { L01: [0.7, 3.0], L02: [3.4, 6.3] } },
      { id: "c13", sid: "D", in: 2.0, out: 9.0, setup: 1, flags: [], score: 0.6, line_times: { L01: [2.3, 5.1], L02: [5.5, 8.7] } },
    ] },
    { id: "s02", lines: ["L03"], chosen: "c21", cands: [
      { id: "c21", sid: "C", in: 0.4, out: 3.4, setup: 1, flags: [], score: 0.8, line_times: { L03: [0.6, 3.1] } },
    ] },
  ];
  const timeline = [
    { slot: "s01", cand: "c11", sid: "A", in: 1.0, out: 4.0, start: 0, join: null },
    { slot: "s01", cand: "c12", sid: "B", in: 3.2, out: 6.5, start: 3.0, join: "splice" },
    { slot: "s02", cand: "c21", sid: "C", in: 0.4, out: 3.4, start: 6.3, join: "section" },
  ];
  return { version: 1, order: [{ sid: "A", name: "A.MOV", setup: 1 }, { sid: "B", name: "B.MOV", setup: 2 }, { sid: "C", name: "C.MOV", setup: 1 }, { sid: "D", name: "D.MOV", setup: 1 }],
    script: { source: "user", text: "", sections: [{ id: 1, lines: ["L01", "L02", "L03"] }], lines }, slots, timeline,
    inserts: [
      { id: "i01", line: "L01", name: "video_horno", sid: "V", kind: "video", start: 0.05, end: 2.95, fit: "speed", speed: 1.2, src_in: 0, status: "ok" },
      { id: "i02", line: "L03", name: "foto_masa", sid: "F", kind: "image", start: 6.35, end: 8.35, fit: "hold", speed: 1, src_in: 0, status: "ok" },
      { id: "i03", line: "L03", name: "cartel_final", sid: null, kind: null, start: 8.35, end: 9.55, fit: "hold", speed: 1, src_in: 0, status: "missing" },
    ], discarded: [], uncovered: [], extra: [], stats: {}, ms: 1 };
}
const SRC = [{ id: "A", type: "video", dur: 9 }, { id: "B", type: "video", dur: 8 }, { id: "C", type: "video", dur: 5 }, { id: "D", type: "video", dur: 10 },
  { id: "V", type: "video", dur: 3.48 }, { id: "F", type: "image", dur: 0 }, { id: "G", type: "video", dur: 20 }];
const idg = () => { let n = 0; return () => "k" + n++; };

test("placeInserts: con las tomas del montaje, la linea desde sus palabras (line_times) y los ajustes", () => {
  const m = montageR(), cs = C.montageClips(m, SRC, idg());
  const ins = C.placeInserts(m, cs, { sources: SRC });
  assert.deepEqual(ins.map(x => x.id), ["i01", "i02", "i03"]);
  // L01: palabras 1.2 s de A (tl 0.2) -> 3.8 s de A (tl 2.8): 0.05 .. 3.05
  near(ins[0].start, 0.05, 1e-3); near(ins[0].end, 3.05, 1e-3);
  assert.equal(ins[0].fit, "speed"); near(ins[0].speed, 3.48 / 3.0, 1e-3);
  // L03 con DOS recursos: se reparten su tramo (0.6..3.1 de C -> 6.5..9.0; -0.15/+0.25) y cada uno >= 2 s
  near(ins[1].start, 6.35, 1e-3); assert.ok(ins[1].end - ins[1].start >= 2 - 1e-6 || ins[1].end <= ins[2].start + 1e-6);
  assert.equal(ins[1].fit, "hold"); assert.equal(ins[1].kind, "image");
  assert.equal(ins[2].status, "missing"); assert.equal(ins[2].sid, null);
  assert.ok(ins.every(x => x.placed));
  for (let k = 0; k + 1 < ins.length; k++) assert.ok(ins[k].end <= ins[k + 1].start + 1e-6, "nunca encimados");
});

test("placeInserts: el recurso sigue a su linea al cambiar de toma y desaparece si se quita su parte", () => {
  const m = montageR(), cs = C.montageClips(m, SRC, idg());
  const r = C.swapTake(cs, m, "s01", "c13", { sources: SRC });      // una sola toma de D (2.0..9.0)
  const ins = C.placeInserts(m, r.clips, { sources: SRC });
  // L01 en D: 2.3..5.1 de la fuente -> 0.3..3.1 del ensamble
  near(ins[0].start, 0.15, 1e-3); near(ins[0].end, 3.35, 1e-3);
  near(ins[0].speed, 3.48 / 3.2, 1e-3);
  // la parte s02 se corrio 7 - 6.3 = 0.7 s: su recurso tambien
  near(ins[1].start, 6.35 + 0.7, 1e-3);
  const gone = C.removeSlot(r.clips, "s02");
  const ins2 = C.placeInserts(m, gone.clips, { sources: SRC });
  assert.equal(ins2[1].placed, false); assert.equal(ins2[2].placed, false);
  assert.equal(C.insertForCompose(ins2[1]).enabled, false, "sin su linea no se compone");
});

test("placeInserts: lo que edita el usuario (quitar, otro archivo) y el cuerpo de /compose", () => {
  const m = montageR(), cs = C.montageClips(m, SRC, idg());
  const ins = C.placeInserts(m, cs, { sources: SRC, edits: { i01: { enabled: false }, i03: { sid: "G", kind: "video", status: "ok" } } });
  assert.equal(ins[0].enabled, false);
  assert.equal(ins[2].sid, "G"); assert.equal(ins[2].status, "ok"); assert.equal(ins[2].fit, "trim", "20 s en un hueco corto: se recorta");
  const b = C.composeBody({ clips: cs, overlays: [], montageJob: "job1", script: "x", inserts: ins });
  assert.deepEqual(b.inserts.map(x => [x.id, x.enabled, x.status]), [["i01", false, "ok"], ["i02", true, "ok"], ["i03", true, "ok"]]);
  assert.ok(b.inserts.every(x => typeof x.start === "number" && x.end > x.start));
  // sin montaje no viajan recursos
  assert.equal(C.composeBody({ clips: cs, overlays: [], inserts: ins }).inserts, undefined);
  // guion editable con sus marcas (rearmar no las pierde)
  assert.equal(C.scriptText(m.script), "Abrimos hace un año. - Agregar video_horno\nHorneamos cada mañana.\nSíguenos. - Agregar foto_masa, cartel_final");
});

// ---------------------------------------------------------------- cambios de camara con aceleracion
/** Dos tramos de camaras distintas unidos a media linea (A: setup 1 -> B: setup 2). */
function joinAB(o) {
  const opt = o || {};
  const p = { _id: "p", sid: "A", type: "video", in: 1.0, out: 4.0, start: 0, layer: 0, srcdur: 9, montage: { slot: "s1", cand: "c1", join: null, cam: 1, setup: 1 } };
  const x = { _id: "x", sid: "B", type: "video", in: 3.2, out: 6.5, start: 3.0, layer: 0, srcdur: 8, montage: { slot: "s1", cand: "c2", join: opt.join || "splice", cam: opt.cam == null ? 2 : opt.cam, setup: 2 } };
  return [p, x];
}
const W = (pairs) => pairs.map(([a, b]) => ({ start: a, end: b }));

test("syncRamps: acelera la toma que SALE si termina en pausa y tiene cuadros despues de 'out'", () => {
  const cs = joinAB();
  // A habla hasta 3.6 y retoma en 4.9 (a media actuacion); B empieza a hablar justo en 3.3
  const words = { A: W([[1.2, 3.6], [4.9, 6.0]]), B: W([[3.3, 6.2]]) };
  const marks = C.syncRamps(cs, { words });
  assert.equal(marks.length, 1);
  assert.deepEqual([marks[0].on, marks[0].side, marks[0].eligible, marks[0].nextId], [true, "out", true, "x"]);
  assert.deepEqual(cs[0].ramp_out, { dur: 0.3, speed: 2 });
  assert.equal(cs[1].ramp_in, undefined);
  assert.equal(cs[1].rampJoin, true);
  const main = C.composeMain(cs);
  assert.deepEqual(main[0].ramp_out, { dur: 0.3, speed: 2 });
  assert.equal(main[1].montage.ramp, true); assert.equal(main[1].montage.ramp_user, undefined);
});

test("syncRamps: si la que sale habla hasta el corte, acelera la que ENTRA (empieza antes en su fuente)", () => {
  const cs = joinAB();
  const words = { A: W([[1.2, 3.98]]), B: W([[2.2, 2.85], [3.7, 6.2]]) };
  C.syncRamps(cs, { words });
  assert.equal(cs[0].ramp_out, undefined);
  assert.deepEqual(cs[1].ramp_in, { dur: 0.3, speed: 2 });
});

test("syncRamps: mas corta si no cabe la de 0.30 s; nada si hay voz en los dos lados", () => {
  const cs = joinAB();
  C.syncRamps(cs, { words: { A: W([[1.2, 3.8]]), B: W([[3.3, 6.2]]) } });
  assert.deepEqual(cs[0].ramp_out, { dur: 0.2, speed: 2 }, "la voz termina 0.2 s antes del corte: cabe la de 0.20 s (tolerancia de 40 ms)");
  const cs2 = joinAB();
  const mk = C.syncRamps(cs2, { words: { A: W([[1.2, 3.99]]), B: W([[3.2, 6.2]]) } });
  assert.equal(cs2[0].ramp_out, undefined); assert.equal(cs2[1].ramp_in, undefined);
  assert.equal(mk[0].on, false);
});

test("syncRamps: sin cambio de camara no hay marca; a media linea o A/B/A si; una union 'line' simple no", () => {
  const same = joinAB({ cam: 1 });
  assert.deepEqual(C.syncRamps(same, { words: { A: W([[1.2, 3.6]]), B: W([[3.3, 6.2]]) } }), []);
  const line = joinAB({ join: "line" });
  const mk = C.syncRamps(line, { words: { A: W([[1.2, 3.6], [4.9, 6]]), B: W([[3.3, 6.2]]) } });
  assert.deepEqual([mk[0].switch, mk[0].eligible, mk[0].on], [true, false, false]);
  // A/B/A: la camara que entra estuvo dos tramos antes
  const [p, x] = joinAB({ join: "line" });
  const a0 = { _id: "a0", sid: "A", type: "video", in: 0.2, out: 0.9, start: -0.7, layer: 0, srcdur: 9, montage: { slot: "s0", cand: "c0", join: null, cam: 2 } };
  a0.start = 0; p.start = 0.7; x.start = 3.7;
  const mk2 = C.syncRamps([a0, p, x], { words: { A: W([[1.2, 3.6], [4.9, 6]]), B: W([[3.3, 6.2]]) } });
  assert.equal(mk2.find(m => m.nextId === "x").eligible, true);
});

test("toggleRamp: el usuario la apaga o la fuerza (el lado con mas aire); se deshace volviendo al estado", () => {
  const cs = joinAB();
  const words = { A: W([[1.2, 3.6], [4.9, 6.0]]), B: W([[3.3, 6.2]]) };
  C.syncRamps(cs, { words });
  let r = C.toggleRamp(cs, "x", false, { words });
  assert.equal(r.ok, true); assert.equal(cs[0].ramp_out, undefined); assert.equal(r.marks[0].user, true);
  // ramp_user = LO QUE ELIGIO (= montage.plan_ramps): false = corte seco
  assert.equal(C.composeMain(cs)[1].montage.ramp, false); assert.equal(C.composeMain(cs)[1].montage.ramp_user, false);
  r = C.toggleRamp(cs, "x", true, { words });
  assert.equal(r.ok, true); assert.deepEqual(cs[0].ramp_out, { dur: 0.3, speed: 2 });
  // una union "line" (no automatica) que el usuario pide: se hace donde haya aire aunque roce la voz
  const ln = joinAB({ join: "line" });
  const w2 = { A: W([[1.2, 3.85]]), B: W([[3.3, 6.2]]) };
  C.syncRamps(ln, { words: w2 });
  assert.equal(ln[0].ramp_out, undefined);
  r = C.toggleRamp(ln, "x", true, { words: w2 });
  assert.equal(r.ok, true); assert.ok(ln[0].ramp_out || ln[1].ramp_in);
  // sin cuadros de sobra en ningun lado: no se puede
  const tight = joinAB(); tight[0].srcdur = 4.05; tight[1].in = 0.1; tight[1].out = 3.4;
  r = C.toggleRamp(tight, "x", true, { words: { A: W([[1.2, 3.6]]), B: W([[0.6, 3.0]]) } });
  assert.equal(r.ok, false);
});

test("syncRamps: sin palabras aun conserva la del montaje; al cambiar un vecino se revisa", () => {
  const cs = joinAB();
  cs[0].ramp_out = { dur: 0.3, speed: 2 };
  let mk = C.syncRamps(cs, {});
  assert.deepEqual(cs[0].ramp_out, { dur: 0.3, speed: 2 }); assert.equal(mk[0].on, true);
  // el vecino se movio: ya no es una union
  cs[1].start = 5;
  mk = C.syncRamps(cs, {});
  assert.equal(cs[0].ramp_out, undefined); assert.deepEqual(mk, []);
  // un crossfade tampoco lleva aceleracion (compose no la hace)
  const cf = joinAB(); cf[1].trans = { type: "crossfade", dur: 0.5 };
  assert.deepEqual(C.syncRamps(cf, { words: { A: W([[1.2, 3.6]]), B: W([[3.3, 6.2]]) } }), []);
});

test("montageClips: la camara real sale de montage.order y el intercalado de las banderas del candidato", () => {
  const m = montageR(), cs = C.montageClips(m, SRC, idg());
  assert.deepEqual(cs.map(c => [c.montage.cam, c.montage.ic]), [[1, true], [2, true], [1, false]]);
  // la aceleracion que decidio el montaje llega al clip (y a /compose)
  m.timeline[0].ramp_out = { dur: 0.25, speed: 2 };
  const cs2 = C.montageClips(m, SRC, idg());
  assert.deepEqual(cs2[0].ramp_out, { dur: 0.25, speed: 2 });
  assert.deepEqual(C.composeMain(cs2)[0].ramp_out, { dur: 0.25, speed: 2 });
  // "Usar esta toma" con la elegida por la IA restaura tramos y aceleraciones
  const r = C.swapTake(cs2, m, "s01", "c13", { sources: SRC });
  const back = C.swapTake(r.clips, m, "s01", "c11", { sources: SRC });
  assert.deepEqual(back.clips[0].ramp_out, { dur: 0.25, speed: 2 });
});

// ---------------------------------------------------------------- color de acento
test("color de acento: amarillo por defecto; uno propio pasa a subtitulos, titulos y tarjetas", () => {
  assert.equal(C.DEFAULT_ACCENT, "#FFD23F");
  assert.equal(C.projectAccent({ fx: {} }), "#FFD23F");
  assert.equal(C.projectAccent({ fx: { captions: { accent: "#ff0" } } }), "#FFFF00");
  assert.equal(C.normHex("zzz"), null);
  const plan = { version: 2, fx: {
    captions: { accent: "#FFD23F" },
    text: [{ id: "t1", accent_words: ["pan"], lines: [{ text: "Pan", color: "#ffd23f" }, { text: "rico", color: "#FFFFFF" }] }],
    graphics: [{ id: "g1", kind: "card", accent: "#FFD23F", color: "#FFFFFF" }, { id: "g2", kind: "cta", bg: "#E8C27A" }],
    cutaways: [{ id: "c1", text: { text: "x", accent: "#FFD23F" }, card: { accent: "#ffd23f" } }] } };
  const n = C.applyAccent(plan, "#3366ff", { palette: "#FFD23F" });
  assert.ok(n >= 5);
  assert.equal(plan.fx.accent, "#3366FF"); assert.equal(plan.fx.captions.accent, "#3366FF");
  assert.equal(plan.fx.text[0].lines[0].color, "#3366FF"); assert.equal(plan.fx.text[0].lines[1].color, "#FFFFFF");
  assert.equal(plan.fx.text[0].accent, "#3366FF", "palabras de acento sin color propio");
  assert.equal(plan.fx.graphics[0].accent, "#3366FF"); assert.equal(plan.fx.graphics[0].color, "#FFFFFF");
  assert.equal(plan.fx.graphics[1].bg, "#E8C27A", "no es el acento de este estilo");
  assert.equal(plan.fx.cutaways[0].text.accent, "#3366FF"); assert.equal(plan.fx.cutaways[0].card.accent, "#3366FF");
  // volver al amarillo deshace el cambio
  C.applyAccent(plan, C.DEFAULT_ACCENT);
  assert.equal(plan.fx.graphics[0].accent, "#FFD23F"); assert.equal(C.projectAccent(plan), "#FFD23F");
  // un acento blanco (estilo Minimal) no se busca en los items: solo los campos de acento
  const p2 = { version: 2, fx: { captions: { accent: "#FFFFFF" }, graphics: [{ id: "g", color: "#FFFFFF" }] } };
  C.applyAccent(p2, "#FF0000", { palette: "#FFFFFF" });
  assert.equal(p2.fx.graphics[0].color, "#FFFFFF"); assert.equal(p2.fx.captions.accent, "#FF0000");
  // el glitch de las transiciones y los efectos tenidos con el acento tambien cambian (= servidor)
  const p3 = { version: 2, fx: { captions: { accent: "#FFD23F" },
    transitions: [{ id: "tr1", kind: "glitch", color: "#FFD23F" }, { id: "tr2", kind: "flash", color: "white" }],
    effects: [{ id: "fx1", kind: "subject_outline", color: "#ffd23f" }] } };
  C.applyAccent(p3, "#08794d", { palette: "#FFD23F" });
  assert.equal(p3.fx.transitions[0].color, "#08794D"); assert.equal(p3.fx.transitions[1].color, "white");
  assert.equal(p3.fx.effects[0].color, "#08794D");
});

test("pista media: ids propios, etiqueta del recurso y dentro de las pistas del plan", () => {
  assert.ok(C.TRACKS.includes("media"));
  assert.match(C.newId("media", []), /^md_u/);
  assert.equal(C.itemLabel(null, "media", { name: "video_horno" }), "video_horno");
  assert.equal(C.itemLabel(null, "media", {}), "Recurso");
  const fx = { media: [{ id: "md1", locked: true, start: 1, end: 3 }] };
  assert.deepEqual(C.lockedIds(fx), ["md1"]);
});

// ---------------------------------------------------------------- revision 2: regresiones
test("lineRefs: una frase con guion largo y verbo se DICE (no es marca); con archivo si lo es", () => {
  [
    "Nuestra meta — agregar valor a cada familia del barrio.",
    "Súmate — agrega tu negocio hoy.",
    "Tenemos un reto - mostrar resultados reales.",
    "Hay una regla | pon tu marca primero.",
    "Te damos una muestra - muestra gratis para ti.",
    "La clave — poner al cliente primero",
    "Súmate — agrega 3 negocios hoy",
  ].forEach(l => assert.deepEqual(C.lineRefs(l), { text: l, names: [] }, l));
  assert.deepEqual(C.lineRefs("Hola. - Agregar foto_1, la tienda de mi barrio").names, []);
  assert.deepEqual(C.lineRefs("Hola. — Mostrar Foto Local.jpg"), { text: "Hola.", names: ["Foto Local.jpg"] });
  // sin _ ni extension: solo si coincide con un archivo elegido
  assert.deepEqual(C.lineRefs("Abrimos temprano - agregar foto del local").names, []);
  assert.deepEqual(C.lineRefs("Abrimos temprano - agregar foto del local", [{ name: "Foto del local.HEIC" }]),
    { text: "Abrimos temprano", names: ["foto del local"] });
  assert.deepEqual(C.lineRefs("Somos tres socios. [1]"), { text: "Somos tres socios.", names: [] });
  assert.equal(C.stripRefs("Súmate — agrega tu negocio hoy."), "Súmate — agrega tu negocio hoy.");
});

test("matchAsset/classifyFiles: '[1]' o '{a}' nunca toman una toma; ids solo iguales; al reves solo imagenes", () => {
  const files = [{ sid: "1f3a9c0d2b4e", name: "IMG_0375.MOV" }, { sid: "a07c55e1d9f2", name: "IMG_0376.MOV" },
    { sid: "2b2b2b2b2b2b", name: "20240518_101010_x.mp4" }];
  const r = C.classifyFiles(files, "Somos tres socios. [1]\nY nos va bien. {a}\nCerramos. {2}");
  assert.deepEqual(r.clips, [0, 1, 2]); assert.deepEqual(r.assets, []);
  assert.deepEqual(r.refs, [{ name: "a", idx: -1 }, { name: "2", idx: -1 }]);
  assert.equal(C.matchAsset("1f3a", files), -1);
  assert.equal(C.matchAsset("1f3a9c0d2b4e", files), 0);
  assert.equal(C.matchAsset("img", files), -1); assert.equal(C.matchAsset("img_03", files), -1);
  // 'Marta.mov' es toma: el guion pide 'marta_en_el_taller' (no elegido) -> sigue siendo toma
  const mf = [{ name: "Marta.mov", type: "video/quicktime" }, { name: "Marta 2.mov", type: "video/quicktime" }];
  const m = C.classifyFiles(mf, "Hola, soy Marta. - Agregar marta_en_el_taller");
  assert.deepEqual([m.clips, m.assets, m.refs], [[0, 1], [], [{ name: "marta_en_el_taller", idx: -1 }]]);
  assert.equal(C.matchAsset("logo_nuevo", [{ name: "logo.png" }]), 0);
  assert.equal(C.matchAsset("logo_nuevo", [{ name: "logo.mov" }]), -1);
  // un parecido solo por prefijo nunca se lleva una toma con voz
  const t = C.classifyFiles([{ name: "toma_pan_1.mov", type: "video/quicktime", has_words: true }], "Hola. {toma_pan}");
  assert.deepEqual([t.clips, t.refs[0].idx], [[0], -1]);
});

test("placeInserts: un recurso QUITADO no le quita lugar a su hermano ni recorta a otro; sin lugar va con null", () => {
  const m = montageR(), cs = C.montageClips(m, SRC, idg());
  const both = C.placeInserts(m, cs, { sources: SRC });
  const off = C.placeInserts(m, cs, { sources: SRC, edits: { i03: { enabled: false } } });
  // L03 tenia dos recursos (se repartian su tramo): quitado uno, el otro cubre toda la linea
  near(off[1].start, both[1].start, 1e-3);
  assert.ok(off[1].end > both[1].end + 0.5, `${off[1].end} vs ${both[1].end}`);
  near(off[1].end, 9.25, 1e-3);                                              // toda L03: 6.5..9.0 de la linea -0.15/+0.25
  assert.equal(off[2].enabled, false); assert.equal(off[2].placed, true);
  // uno quitado encima de otro visible no lo recorta
  const m2 = montageR();
  m2.inserts[0].line = "L01"; m2.inserts[1].line = "L01"; m2.inserts[2].line = "L03";
  const cs2 = C.montageClips(m2, SRC, idg());
  const a = C.placeInserts(m2, cs2, { sources: SRC, edits: { i02: { enabled: false } } });
  const b = C.placeInserts(m2, cs2, { sources: SRC, edits: { i02: { enabled: false }, i01: {} } });
  assert.deepEqual(a.map(x => [x.id, x.start, x.end]), b.map(x => [x.id, x.start, x.end]));
  const lone = C.placeInserts(Object.assign(montageR(), { inserts: [montageR().inserts[0]] }), cs2, { sources: SRC });
  near(a[0].start, lone[0].start, 1e-3); near(a[0].end, lone[0].end, 1e-3);
  // sin lugar (su parte ya no esta en el ensamble): a /compose con start/end null
  const only = cs.filter(c => c.montage && c.montage.slot === "s02");
  only.forEach(c => { c.start = 0; });
  const un = C.placeInserts(m, only, { sources: SRC });
  assert.equal(un[0].placed, false);
  const body = C.insertForCompose(un[0]);
  assert.equal(body.start, null); assert.equal(body.end, null); assert.equal(body.enabled, false);
});

test("ramp_user: la eleccion del usuario va y vuelve (composeMain -> montageClips -> syncRamps)", () => {
  const W2 = { A: W([[1.2, 3.6], [4.9, 6.0]]), B: W([[3.3, 6.2]]) }, srcdur = { A: 9, B: 8 };
  const mont = extra => ({ version: 1, order: [{ sid: "A", name: "A.MOV", setup: 1 }, { sid: "B", name: "B.MOV", setup: 2 }],
    script: { source: "user", text: "", sections: [{ id: 1, lines: ["L01"] }], lines: [{ id: "L01", text: "x" }] },
    slots: [{ id: "s01", lines: ["L01"], chosen: "c11", cands: [
      { id: "c11", sid: "A", in: 1.0, out: 7.0, setup: 1, flags: ["intercalado"], score: 0.9 },
      { id: "c12", sid: "B", in: 0.5, out: 6.5, setup: 2, flags: ["intercalado"], score: 0.88 }] }],
    timeline: [{ slot: "s01", cand: "c11", sid: "A", in: 1.0, out: 4.0, start: 0, join: null },
      Object.assign({ slot: "s01", cand: "c12", sid: "B", in: 3.2, out: 6.5, start: 3.0, join: "splice" }, extra || {})],
    inserts: [], discarded: [], uncovered: [], extra: [], stats: {}, ms: 1 });
  const S2 = [{ id: "A", type: "video", dur: 9 }, { id: "B", type: "video", dur: 8 }];
  let cs = C.montageClips(mont(), S2, idg());
  C.syncRamps(cs, { words: W2, srcdur });
  assert.deepEqual(cs[0].ramp_out, { dur: 0.3, speed: 2 });                  // automatica
  C.toggleRamp(cs, cs[1]._id, false, { words: W2, srcdur });               // el usuario: corte seco
  const mm = C.composeMain(cs)[1].montage;
  assert.deepEqual([mm.ramp, mm.ramp_user], [false, false]);
  // compose.montage_plan guarda ramp/ramp_user en la linea de tiempo; al volver al ensamble:
  cs = C.montageClips(mont({ ramp: mm.ramp, ramp_user: mm.ramp_user }), S2, idg());
  assert.equal(cs[1].rampUser, false);
  C.syncRamps(cs, { words: W2, srcdur });
  assert.equal(cs[0].ramp_out, undefined); assert.equal(cs[1].ramp_in, undefined);
  // y forzada (true) tambien se conserva
  cs = C.montageClips(mont({ ramp: true, ramp_user: true }), S2, idg());
  assert.equal(cs[1].rampUser, true);
});

test("insertFit y compose._media_fit: un video un poco mas corto se alenta (x0.8..x1); mas corto dura lo suyo", () => {
  assert.deepEqual(C.insertFit("video", 4.25, 5), { fit: "speed", speed: 0.85, src_in: 0 });
  assert.deepEqual(C.insertFit("video", 3.9, 5), { fit: "trim", speed: 1, src_in: 0, len: 3.9 });
  assert.equal(C.INS.slowMin, 0.8); assert.equal(C.INS.speedMax, 1.4);
});

test("newItem: con acento propio del proyecto, los colores de acento del catalogo nacen con el", () => {
  const SC = { common: { range: [{ k: "start", type: "num" }, { k: "end", type: "num" }] }, tracks: {
    text: { key: "preset", common: "range", items: { title_pop: { label: "T", params: [{ k: "text", type: "text", default: "" },
      { k: "accent", type: "color", default: "#FFD23F" }, { k: "color", type: "color", default: "#FFFFFF" }] } } },
    graphics: { key: "kind", common: "range", items: { sticker_text: { label: "S", params: [{ k: "bg", type: "color", default: "#FFD23F" }] } } } } };
  const blue = { accent: "#3366FF", text: [], graphics: [] };
  const t = C.newItem(SC, "text", "title_pop", 1, 10, blue), g = C.newItem(SC, "graphics", "sticker_text", 1, 10, blue);
  assert.deepEqual([t.accent, t.color, g.bg], ["#3366FF", "#FFFFFF", "#3366FF"]);
  const y = C.newItem(SC, "text", "title_pop", 1, 10, { text: [] });
  assert.equal(y.accent, "#FFD23F");                                         // sin acento propio: amarillo
});
