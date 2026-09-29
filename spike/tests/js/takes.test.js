// Pruebas de la limpieza de tomas del Studio (logica pura de app/static/fxcore.js).
// Corre con: node --test spike/tests/js
// Datos 100 % inventados (el repo es publico): una resena ficticia de una bicicleta.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "..", "app", "static", "fxcore.js"));

/** Palabras con tiempos a partir de un texto: 0.3 s por palabra, pausa extra tras "|". */
function wordsOf(txt, t0) {
  const out = []; let t = t0 || 0;
  txt.split(/\s+/).filter(Boolean).forEach(tok => {
    if (tok === "|") { t += 1.0; return; }
    out.push({ text: tok, start: +t.toFixed(3), end: +(t + 0.26).toFixed(3) }); t += 0.3;
  });
  return out;
}
const SCRIPT = "Hoy les traigo la bici que uso | Hoy les traigo | Hoy les traigo la bici que uso para ir al trabajo. "
  + "Tiene diecinueve, no, veintiún cambios y frenos de disco. La la la cadena casi no suena. | "
  + "Bueno, eso es todo por hoy.";
const W = wordsOf(SCRIPT, 1.0);
const wi = (txt, from) => W.findIndex((w, i) => i >= (from || 0) && w.text === txt);
const span = (a, b, extra) => Object.assign({ start: +(W[a].start - 0.02).toFixed(3), end: +(W[b].end + 0.02).toFixed(3), enabled: true }, extra || {});
/** Tomas: 1a y 2a toma (grupo tg_1, la 3a se queda), autocorreccion, tartamudeo (sugerencia). */
function fixture() {
  const t3 = wi("Hoy", wi("traigo", wi("traigo") + 1));     // tercera toma (la elegida)
  const cuts = [
    { start: 0, end: 0.8, enabled: true },                                     // silencio inicial (sin palabras)
    span(0, wi("uso") , { id: "tk_a", src: "takes", kind: "retoma", conf: 0.92, level: "alta", group: "tg_1", reason: "Lo repites mejor despues", keep_ref: { start: W[t3].start, end: W[t3 + 6].end } }),
    span(wi("Hoy", 1), wi("traigo", wi("Hoy", 1)), { id: "tk_b", src: "takes", kind: "retoma", conf: 0.7, level: "media", group: "tg_1", keep_ref: { start: W[t3].start, end: W[t3 + 6].end } }),
    span(wi("diecinueve,"), wi("no,"), { id: "tk_c", src: "takes", kind: "autocorreccion", conf: 0.66, level: "media" }),
    span(wi("La"), wi("la", wi("La") + 1), { id: "tk_d", src: "takes", kind: "tartamudeo", conf: 0.3, level: "baja", enabled: false }),
    { start: W[wi("Bueno,")].start - 0.9, end: W[wi("Bueno,")].start - 0.05, enabled: true },   // silencio entre oraciones
  ];
  return { t3, cuts };
}

test("planWords: aplana las frases, descarta vacias y ordena por inicio", () => {
  const ws = C.planWords([[{ text: "hola", start: 1, end: 1.2 }, { text: " ", start: 1.2, end: 1.3 }], [{ text: "mundo", start: 0.5, end: 0.4 }]]);
  assert.deepEqual(ws.map(w => w.text), ["mundo", "hola"]);
  assert.equal(ws[0].end, 0.5);                    // end roto -> start
});

test("wordsInCuts: cada palabra es de su toma (por el punto medio) y las sugerencias no quitan", () => {
  const { cuts } = fixture();
  const { owner, removed } = C.wordsInCuts(W, cuts);
  assert.equal(owner[0], 1); assert.equal(removed[0], 1);
  assert.equal(owner[wi("diecinueve,")], 3);
  const la = wi("La");
  assert.equal(owner[la], 4); assert.equal(removed[la], 0);          // tartamudeo apagado: se oye
  assert.equal(owner[wi("Tiene")], -1); assert.equal(removed[wi("Tiene")], 0);
  // una toma gana a un silencio que la solapa, aunque el silencio empiece antes
  const over = cuts.concat([{ start: W[0].start - 0.1, end: W[2].end, enabled: true }]);
  assert.equal(C.wordsInCuts(W, over).owner[1], 1);
  // palabras con duracion enorme (whisper sin alinear) no rompen el barrido
  const ws = [{ text: "a", start: 0, end: 9 }, { text: "b", start: 1, end: 1.2 }, { text: "c", start: 5, end: 5.2 }];
  const r = C.wordsInCuts(ws, [{ start: 4, end: 6, enabled: true, src: "takes" }]);
  assert.deepEqual(r.owner, [0, -1, 0]);
});

test("wordsInCuts: un silencio que solo tapa lo estirado de una palabra dicha no la tacha", () => {
  // 'café.' estirada 2.3 s sobre la pausa que sigue (la voz real dura 0.4 s): el silencio de en
  // medio tiene su punto medio pero el render la conserva (Timeline.map_words) -> no sale tachada
  const ws = [{ text: "de", start: 27.73, end: 27.81 }, { text: "café.", start: 27.87, end: 30.19 },
    { text: "Cuando", start: 31.91, end: 32.17 }, { text: "eh", start: 40.1, end: 40.3 }];
  const cuts = [{ start: 28.33, end: 29.22, enabled: true }, { start: 30.7, end: 31.89, enabled: true },
    { start: 40.0, end: 40.4, enabled: true }];                     // este si tapa 'eh' entera
  const r = C.wordsInCuts(ws, cuts);
  assert.deepEqual(r.owner, [-1, -1, -1, 2]);
  assert.deepEqual(r.removed, [0, 0, 0, 1]);
  // una toma sigue por el punto medio aunque no la tape entera
  const t = C.wordsInCuts(ws, [{ start: 28.0, end: 30.0, enabled: true, src: "takes" }]);
  assert.equal(t.owner[1], 0); assert.equal(t.removed[1], 1);
  // y los silencios sin palabras quedan como pausas (Mostrar silencios), por su punto medio
  assert.deepEqual(C.wordlessAt(ws, cuts, C.cutSpans(r.owner)), { 1: [0], 2: [1] });
});

test("cutSpans y wordlessAt: cortes con palabras vs silencios entre palabras", () => {
  const { cuts } = fixture();
  const { owner } = C.wordsInCuts(W, cuts);
  const sp = C.cutSpans(owner);
  assert.deepEqual(sp[1], [0, wi("uso")]);
  assert.equal(sp[0], undefined); assert.equal(sp[5], undefined);
  const at = C.wordlessAt(W, cuts, sp);
  assert.deepEqual(at[0], [0]);                                  // antes de la primera palabra
  assert.deepEqual(at[wi("Bueno,")], [5]);
});

test("takeGroups + cutTag: 'Toma repetida · 1 de 3' y la toma elegida", () => {
  const { t3, cuts } = fixture();
  const { groups, ord } = C.takeGroups(cuts);
  assert.deepEqual(groups.tg_1.cuts, [1, 2]);
  assert.deepEqual(ord[1], { group: "tg_1", i: 1, n: 3 });
  assert.equal(C.cutTag(cuts[1], ord[1]), "Toma repetida · 1 de 3");
  assert.equal(C.cutTag(cuts[2], ord[2]), "2 de 3");
  assert.equal(C.cutTag(cuts[2], ord[2], true), "Toma repetida · 2 de 3");
  assert.equal(C.cutTag(cuts[3], ord[3]), "Corrección");
  assert.equal(C.cutTag(cuts[0], null), "Silencio");
  const { owner } = C.wordsInCuts(W, cuts);
  const marks = C.keptMarks(W, cuts, groups, owner);
  assert.equal(marks[t3], "tg_1");
  // sin ningun corte del grupo habilitado no hay "Toma elegida"
  cuts[1].enabled = false; cuts[2].enabled = false;
  assert.deepEqual(C.keptMarks(W, cuts, C.takeGroups(cuts).groups, owner), {});
});

test("takeStats y removedByCategory: sin contar dos veces lo solapado", () => {
  const cuts = [
    { start: 0, end: 2, enabled: true },                                                // silencio
    { start: 1, end: 4, enabled: true, src: "takes", kind: "retoma", level: "alta" },  // solapa 1 s
    { start: 3, end: 5, enabled: true, src: "takes", kind: "retoma", level: "alta" },  // solapa 1 s con la otra toma
    { start: 6, end: 7, enabled: false, src: "takes", kind: "error", level: "baja" },  // sugerencia
    { start: 8, end: 9, enabled: false, src: "takes", kind: "error", level: "alta", user: true },  // restaurada
    { start: 10, end: 11, enabled: true, manual: true },
  ];
  const st = C.takeStats(cuts);
  assert.equal(st.n, 4); assert.equal(st.enabled, 2); assert.equal(st.suggested, 1);
  assert.equal(st.removed_s, 4);                    // [1,5]
  assert.deepEqual(st.by_kind.retoma, { n: 2, enabled: 2, s: 4 });
  assert.deepEqual(st.by_kind.error, { n: 2, enabled: 0, s: 0 });
  const r = C.removedByCategory(cuts, 20);
  assert.equal(r.total, 6);                         // [0,5] + [10,11]
  assert.deepEqual(r.cats, { silencio: 1, retoma: 4, manual: 1 });
  // el total coincide con lo que quita la linea de tiempo sin reticula
  const tl = new C.Timeline(C.keepsFromCuts(cuts, 20), 20, null, 0);
  assert.ok(Math.abs(20 - tl.finalDuration - r.total) < 1e-9);
  assert.deepEqual(C.kindCounts(cuts), { silencio: 1, retoma: 2, error: 2, manual: 1 });
});

test("niveles: suave / normal / a fondo y la vista previa de la duracion", () => {
  const { cuts } = fixture();
  const plan = { duration: W[W.length - 1].end + 0.5, cuts };
  const on = lv => C.applyLevel(cuts, lv).filter(C.isTakeCut).map(c => c.enabled);
  assert.deepEqual(on("suave"), [true, false, false, false]);
  assert.deepEqual(on("normal"), [true, true, true, false]);
  assert.deepEqual(on("a_fondo"), [true, true, true, true]);
  // lo que el usuario toco no cambia con el nivel
  cuts[4].user = true;
  assert.equal(C.applyLevel(cuts, "a_fondo")[4].enabled, false);
  assert.equal(cuts[4].enabled, false);             // applyLevel no muta
  const fs = C.levelFinal(plan, "suave", 30), fn = C.levelFinal(plan, "normal", 30);
  assert.ok(fs > fn, `${fs} > ${fn}`);
  // sin `level` se usa la confianza
  assert.equal(C.takeLevel({ conf: 0.85 }), "alta"); assert.equal(C.takeLevel({ conf: 0.55 }), "media"); assert.equal(C.takeLevel({}), "baja");
  assert.equal(C.confBars({ conf: 0.92 }), 4); assert.equal(C.confBars({ level: "media" }), 3);
});

test("restaurar / quitar: user:true en tomas, silencios sin marca; categoria completa", () => {
  const { cuts } = fixture();
  assert.equal(C.toggleCut(cuts[1]), false);
  assert.equal(cuts[1].user, true);
  assert.equal(C.toggleCut(cuts[0]), false);
  assert.equal(cuts[0].user, undefined);            // un silencio no lleva `user`
  assert.equal(C.setCutsEnabled(cuts, [0, 1, 2], true), 2);    // la 2 ya estaba habilitada
  // apagar tomas repetidas: todas fuera y marcadas; encender: vuelven al nivel sin marca
  assert.equal(C.setKindEnabled(cuts, "retoma", false, "normal"), 2);
  assert.ok(cuts.filter(c => C.cutKind(c) === "retoma").every(c => c.enabled === false && c.user === true));
  assert.equal(C.kindOn(cuts, "retoma"), false);
  C.setKindEnabled(cuts, "retoma", true, "suave");
  assert.deepEqual(cuts.filter(c => C.cutKind(c) === "retoma").map(c => [c.enabled, c.user]), [[true, undefined], [false, undefined]]);
  // encender una categoria que el nivel no corta: se cortan todas (decision del usuario)
  C.setKindEnabled(cuts, "tartamudeo", true, "normal");
  assert.deepEqual([cuts[4].enabled, cuts[4].user], [true, true]);
  // silencios: todos dentro o fuera, sin tocar tomas ni manuales
  C.setKindEnabled(cuts, "silencio", false, "normal");
  assert.deepEqual([cuts[0].enabled, cuts[5].enabled, cuts[3].enabled], [false, false, true]);
});

test("reviewItems: filtros, silencios opcionales y orden por inicio", () => {
  const { cuts } = fixture();
  const all = C.reviewItems(cuts, "todos", false);
  assert.deepEqual(all, [1, 2, 3, 4]);
  assert.deepEqual(C.reviewItems(cuts, "todos", true), [0, 1, 2, 3, 4, 5]);
  assert.deepEqual(C.reviewItems(cuts, "retoma"), [1, 2]);
  assert.deepEqual(C.reviewItems(cuts, "error"), []);
});

test("scriptParagraphs: parrafos por oracion y seccion, sin partir una toma", () => {
  const { cuts } = fixture();
  const { owner } = C.wordsInCuts(W, cuts);
  const ps = C.scriptParagraphs(W, owner, cuts, [], { minWords: 4, maxWords: 90, gap: 5 });
  assert.equal(ps[0].w0, 0); assert.equal(ps[ps.length - 1].w1, W.length);
  ps.forEach((p, k) => { if (k) assert.equal(p.w0, ps[k - 1].w1); });                  // contiguos
  ps.forEach(p => { if (p.w0 > 0 && owner[p.w0] >= 0) assert.notEqual(owner[p.w0], owner[p.w0 - 1]); });
  assert.ok(ps.some(p => W[p.w0].text === "Tiene"));                                    // tras "trabajo."
  // secciones: cada una abre parrafo y lo marca
  const secs = [{ start: 0, title: "Intro" }, { start: W[wi("Bueno,")].start, title: "Cierre" }];
  const ps2 = C.scriptParagraphs(W, owner, cuts, secs, { minWords: 400 });
  assert.deepEqual(ps2.map(p => p.sec), [0, 1]);
  assert.equal(W[ps2[1].w0].text, "Bueno,");
  // la seccion de la IA cae una palabra tarde: el parrafo empieza con la oracion
  const late = [{ start: 0, title: "Intro" }, { start: W[wi("Bueno,") + 1].start, title: "Cierre" }];
  assert.equal(W[C.scriptParagraphs(W, owner, cuts, late, { minWords: 400 })[1].w0].text, "Bueno,");
  // cae a mitad de una oracion sin fin cercano atras: abre al terminar la oracion en curso
  const mid = [{ start: 0, title: "Intro" }, { start: W[wi("casi")].start, title: "Cierre" }];
  assert.equal(W[C.scriptParagraphs(W, owner, cuts, mid, { minWords: 400 })[1].w0].text, "Bueno,");
  assert.deepEqual(C.scriptParagraphs([], [], [], []), []);
});

test("listenRange: 2 s antes y despues de la union, en tiempo final", () => {
  const cuts = [{ start: 10, end: 14, enabled: true }];
  const tl = C.timelineFromPlan({ duration: 30, cuts }, null);
  const [a, b] = C.listenRange(tl, cuts[0], 2);
  assert.ok(Math.abs(a - 8) < 1e-9 && Math.abs(b - 16) < 1e-9, `${a} ${b}`);
  // restaurado: se escucha el tramo completo con 2 s de contexto
  const tl2 = C.timelineFromPlan({ duration: 30, cuts: [{ start: 10, end: 14, enabled: false }] }, null);
  assert.deepEqual(C.listenRange(tl2, { start: 10, end: 14 }, 2), [8, 16]);
  // al inicio del video no pasa de 0
  const tl3 = C.timelineFromPlan({ duration: 30, cuts: [{ start: 0, end: 3, enabled: true }] }, null);
  assert.deepEqual(C.listenRange(tl3, { start: 0, end: 3 }, 2), [3, 5]);
  assert.equal(C.fmtLen(8.44), "8.4 s"); assert.equal(C.fmtLen(125), "2:05");
});

test("formato por defecto: 16:9 para un solo video horizontal de mas de 3 min", () => {
  const long = { duration: 1146, dims: [3840, 2160], shots: [{ start: 0, src: "clip.mov" }] };
  assert.equal(C.defaultFormat(long), "16:9");
  assert.equal(C.defaultFormat(Object.assign({}, long, { duration: 90 })), "9:16");            // corto
  assert.equal(C.defaultFormat(Object.assign({}, long, { dims: [1080, 1920] })), "9:16");      // vertical
  // el reproductor manda (rotacion): dims codificados horizontales, display vertical
  assert.equal(C.defaultFormat(long, { w: 1080, h: 1920 }), "9:16");
  assert.equal(C.defaultFormat({ duration: 400, dims: [1080, 1920] }, { w: 1920, h: 1080 }), "16:9");
  // varios clips distintos (montaje): no es "un solo video"
  assert.equal(C.defaultFormat(Object.assign({}, long, { shots: [{ start: 0, src: "a.mov" }, { start: 50, src: "b.mov" }] })), "9:16");
  // la eleccion del usuario gana
  assert.equal(C.defaultFormat(Object.assign({}, long, { studio: { fmt: "9:16" } })), "9:16");
  assert.equal(C.defaultFormat({ duration: 20, dims: [1080, 1920], studio: { fmt: "1:1" } }), "1:1");
  assert.equal(C.defaultFormat({ duration: 20, studio: { fmt: "raro" } }), "9:16");
  assert.equal(C.defaultFormat(null), "9:16");
});

test("tomas separadas de los silencios: restaurar una toma no restaura el silencio que contiene", () => {
  const cuts = [
    { start: 5, end: 6, enabled: true },                                         // silencio dentro de la toma
    { start: 3, end: 9, enabled: true, src: "takes", kind: "retoma", level: "alta" },
  ];
  const plan = { duration: 20, cuts };
  assert.equal(C.timelineFromPlan(plan, null).finalDuration, 14);
  C.toggleCut(cuts[1]);                                                          // restaurar la toma
  assert.equal(C.timelineFromPlan(plan, null).finalDuration, 19);               // sigue fuera el silencio
});

test("cleanSections: la primera desde 0 y sin capitulos pegados", () => {
  const got = C.cleanSections([{ start: 69.01, title: "B" }, { start: 5.8, title: " A " }, { start: 68.54, title: "C" },
    { start: 78.1, title: "D" }, { start: 121.1, title: "E" }, { start: "x" }, null]);
  assert.deepEqual(got, [{ start: 0, title: "A" }, { start: 68.54, title: "C" }, { start: 121.1, title: "E" }]);
  assert.deepEqual(C.cleanSections([{ start: 45, title: "X" }]), [{ start: 45, title: "X" }]);   // tarde: no se mueve
  assert.deepEqual(C.cleanSections(null), []);
});

test("bordes movidos de una toma: sobreviven al nivel, a la categoria y a re-detectar (adoptTakeCuts)", () => {
  const { cuts } = fixture();
  // el usuario arrastra el fin de la 1a toma para no comerse una palabra
  const c = cuts[1], det = Object.assign({}, c);
  c.end = +(c.end - 0.5).toFixed(3);
  C.markEdges(c);
  assert.deepEqual([c.user, c.edges], [true, true]);
  assert.equal(C.markEdges(cuts[0]).edges, undefined);                          // un silencio no se marca
  // cambiar el nivel no la toca; apagar y volver a encender la categoria no le quita la marca
  const lv = C.applyLevel(cuts, "suave");
  assert.equal(lv[1].end, c.end);
  C.setKindEnabled(cuts, "retoma", false, "normal");
  C.setKindEnabled(cuts, "retoma", true, "normal");
  assert.deepEqual([c.enabled, c.user, c.edges], [true, true, true]);
  assert.equal(cuts[2].user, undefined);                                        // la otra si vuelve al nivel
  // el motor re-detecta (POST /takes) y devuelve la toma con sus bordes detectados
  const server = cuts.filter(C.isTakeCut).map(x => (x.id === c.id ? det : Object.assign({}, x, { conf: 0.5 })));
  const got = C.adoptTakeCuts(cuts, server);
  const mine = got.find(x => x.id === c.id);
  assert.equal(mine.end, c.end);
  assert.equal(got.filter(x => x.id === c.id).length, 1);
  assert.equal(got.find(x => x.id === "tk_c").conf, 0.5);                       // las no tocadas se reemplazan
  assert.equal(got.filter(x => !C.isTakeCut(x)).length, 2);                     // silencios intactos
  // si el motor ya no la devuelve, la del usuario se queda
  assert.ok(C.adoptTakeCuts(cuts, server.filter(x => x.id !== c.id)).some(x => x.id === c.id && x.end === c.end));
  for (let k = 1; k < got.length; k++) assert.ok(+got[k - 1].start <= +got[k].start);
});

test("toma apagada por seguridad (guard): ningun nivel ni la categoria la encienden solas", () => {
  const { cuts } = fixture();
  cuts[3] = Object.assign({}, cuts[3], { enabled: false, guard: true });        // autocorreccion media
  assert.equal(C.levelOn(cuts[3], "a_fondo"), false);
  assert.equal(C.applyLevel(cuts, "a_fondo")[3].enabled, false);
  assert.equal(C.applyLevel(cuts, "normal")[2].enabled, true);
  // la categoria sola: si es la unica, encenderla es decision del usuario
  C.setKindEnabled(cuts, "autocorreccion", false, "normal");
  C.setKindEnabled(cuts, "autocorreccion", true, "normal");
  assert.deepEqual([cuts[3].enabled, cuts[3].user], [true, true]);
  // con otras de su tipo: esas se encienden y la protegida sigue apagada
  const two = [Object.assign({}, cuts[3], { enabled: false, user: undefined }),
    { id: "tk_x", src: "takes", kind: "autocorreccion", level: "media", start: 30, end: 31, enabled: false }];
  C.setKindEnabled(two, "autocorreccion", false, "suave");
  C.setKindEnabled(two, "autocorreccion", true, "suave");                        // suave no corta 'media'
  assert.deepEqual(two.map(x => x.enabled), [false, true]);
  const plan = { duration: W[W.length - 1].end + 0.5, cuts: C.applyLevel(fixture().cuts, "a_fondo") };
  const g = Object.assign({}, plan, { cuts: plan.cuts.map((x, i) => (i === 3 ? Object.assign({}, x, { guard: true }) : x)) });
  assert.ok(C.levelFinal(g, "a_fondo", 30) > C.levelFinal(plan, "a_fondo", 30));  // la vista previa tambien
});

test("reviewItems 'todos' = cortes de limpieza: sin manuales ni recortes de la IA", () => {
  const { cuts } = fixture();
  cuts.push({ start: 20, end: 21, enabled: true, manual: true }, { start: 22, end: 23, enabled: true, src: "ai", id: "cut_ai_1" });
  assert.deepEqual(C.reviewItems(cuts, "todos", false), [1, 2, 3, 4]);
  assert.deepEqual(C.reviewItems(cuts, "todos", true), [0, 1, 2, 3, 4, 5]);
  // la cuenta de "Todos" coincide con la suma de los chips de tipos de toma
  const kc = C.kindCounts(cuts), chips = C.TAKE_KINDS.reduce((n, k) => n + (kc[k] || 0), 0);
  assert.equal(C.reviewItems(cuts, "todos", false).length, chips);
  // "Restaurar todo" no toca los manuales ni los de la IA
  C.setCutsEnabled(cuts, C.reviewItems(cuts, "todos", false), false);
  assert.deepEqual([cuts[6].enabled, cuts[7].enabled], [true, true]);
});

test("takeReason: la hora de la toma que se queda en el reloj que se muestra", () => {
  const c = { src: "takes", kind: "retoma", reason: "Lo repites mejor en 16:05", keep_ref: { start: 965.2, end: 970 } };
  assert.equal(C.takeReason(c, t => t - 381), "Lo repites mejor en 9:44");
  assert.equal(C.takeReason(c), "Lo repites mejor en 16:05");                  // sin reloj: la de la fuente
  assert.equal(C.takeReason(Object.assign({}, c, { reason: "Arranque que dejas a medias; lo retomas en 1:02:03" }), t => t - 900),
    "Arranque que dejas a medias; lo retomas en 1:05");
  assert.equal(C.takeReason(Object.assign({}, c, { keep_ref: null }), t => 0), "Lo repites mejor en 16:05");
  assert.equal(C.takeReason({ src: "takes", reason: "Palabras repetidas de seguido", keep_ref: { start: 5 } }, t => t), "Palabras repetidas de seguido");
  assert.equal(C.takeReason({ reason: "silencio al final", src: "ai" }, t => t), "silencio al final");
  assert.equal(C.takeReason({ src: "takes" }, t => t), "");
});

test("formato al abrir: los proyectos de antes se quedan en 9:16; los shorts siempre verticales", () => {
  const long = { duration: 1146, dims: [3840, 2160], shots: [{ start: 0, src: "clip.mov" }] };
  assert.deepEqual(C.projectFormat(long), { fmt: "9:16", legacy: true });            // de antes: sin studio.fmt ni takes
  assert.deepEqual(C.projectFormat(Object.assign({ takes: { version: 1 } }, long)), { fmt: "16:9", legacy: false });
  assert.deepEqual(C.projectFormat(Object.assign({ studio: { fmt: "16:9" } }, long)), { fmt: "16:9", legacy: false });
  assert.deepEqual(C.projectFormat(Object.assign({ studio: { fmt: "1:1" }, takes: {} }, long)), { fmt: "1:1", legacy: false });
  assert.deepEqual(C.projectFormat(null), { fmt: "9:16", legacy: false });
  assert.equal(C.shortsFor("9:16"), true); assert.equal(C.shortsFor("4:5"), true); assert.equal(C.shortsFor("1:1"), true);
  assert.equal(C.shortsFor("16:9"), false);
  assert.equal(C.shortsFor("original", { w: 3840, h: 2160 }), false);
  assert.equal(C.shortsFor("original", { w: 1080, h: 1920 }), true);
});
