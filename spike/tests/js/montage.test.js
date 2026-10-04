// Pruebas de la logica pura de "Armar con IA" (app/static/fxcore.js, seccion montaje).
// Corre con: node --test spike/tests/js/montage.test.js   (datos sinteticos: tests/js/montage_fixture.js)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "..", "app", "static", "fxcore.js"));
const { CLIPS, UPLOAD_ORDER, synthMontage } = require(path.join(__dirname, "montage_fixture.js"));

const near = (a, b, eps) => assert.ok(Math.abs(a - b) < (eps || 1e-6), `${a} != ${b}`);
const SOURCES = () => UPLOAD_ORDER.map((k, i) => {
  const c = CLIPS.find(x => x.key === k);
  return { id: k, name: c.name, type: "video", dur: c.dur, thumb: `/tlsource/${k}/thumb`, created: c.created, _idx: i };
});
const idgen = () => { let n = 0; return () => "x" + n++; };
const starts = cs => cs.map(c => C.round3(c.start));
const contiguous = cs => { for (let i = 1; i < cs.length; i++) near(cs[i].start, cs[i - 1].start + (cs[i - 1].out - cs[i - 1].in), 1e-3); };

// ---------------------------------------------------------------- orden de grabacion
test("recordingOrder: por fecha de grabacion aunque el contador de la camara de la vuelta", () => {
  const got = C.recordingOrder(SOURCES()).map(s => s.name);
  assert.deepEqual(got, CLIPS.map(c => c.name));
  // PAN_0001 va DESPUES de PAN_0102 (se grabo despues), no por nombre
  assert.ok(got.indexOf("PAN_0001.MOV") > got.indexOf("PAN_0102.MOV"));
});

test("recordingOrder: misma fecha -> nombre natural; sin fecha -> al final; luego orden de subida", () => {
  const s = [
    { id: "1", name: "IMG_10.MOV", created: "2026-01-01T10:00:00Z" },
    { id: "2", name: "IMG_9.MOV", created: "2026-01-01T10:00:00Z" },
    { id: "3", name: "b.mov" },
    { id: "4", name: "a.mov" },
    { id: "5", name: "a.mov" },
    { id: "6", name: "IMG_1.MOV", created: "2026-01-01T09:00:00+0000" },
  ];
  assert.deepEqual(C.recordingOrder(s).map(x => x.id), ["6", "2", "1", "4", "5", "3"]);
  // no modifica la lista original
  assert.equal(s[0].id, "1");
});

test("createdMs: ISO con zona, sin dos puntos, con espacio y numeros", () => {
  const a = C.createdMs("2026-09-29T14:09:40-06:00");
  assert.equal(C.createdMs("2026-09-29T14:09:40-0600"), a);
  assert.equal(C.createdMs("2026-09-29T20:09:40Z"), a);
  assert.ok(isFinite(C.createdMs("2026-09-29 14:09:40")));
  assert.ok(Number.isNaN(C.createdMs(null)) && Number.isNaN(C.createdMs("")));
  assert.equal(C.createdMs(1790712580), 1790712580000);
});

test("natCmp: numeros como numeros y sin distinguir mayusculas", () => {
  assert.deepEqual(["IMG_10.MOV", "img_9.mov", "IMG_100.MOV"].sort(C.natCmp), ["img_9.mov", "IMG_10.MOV", "IMG_100.MOV"]);
});

// ---------------------------------------------------------------- linea de tiempo desde plan.montage
test("montageClips: la linea de tiempo del montaje con su slot, toma, union y confianza", () => {
  const m = synthMontage();
  const cs = C.montageClips(m, SOURCES(), idgen());
  assert.equal(cs.length, 5);
  assert.deepEqual(cs.map(c => c.sid), ["C", "D", "E", "F", "G"]);
  assert.deepEqual(starts(cs), [0, 2.4, 4.9, 8.4, 11.6]);
  contiguous(cs);
  // cam/ic: la camara real del tramo y si es del intercalado (cambios de camara con aceleracion)
  assert.deepEqual(cs[1].montage, { slot: "s01", cand: "c12", join: "splice", conf: 0.9, setup: 2, cam: 2, ic: false });
  assert.equal(cs[0].montage.join, null);
  assert.equal(cs[2].montage.join, "section");
  assert.equal(cs[3].vol, 1.3);
  assert.equal(cs[0].name, "PAN_0100.MOV");
  assert.equal(cs[0].thumb, "/tlsource/C/thumb");
  assert.equal(cs[0].srcdur, 7.5);
  assert.ok(cs.every(c => c.type === "video" && c.layer === 0 && !c.mute && c.fadeIn === 0));
  assert.equal(new Set(cs.map(c => c._id)).size, 5);
});

test("montageClips: sin start los empaca uno tras otro; nombre desde montage.order si falta la fuente", () => {
  const m = synthMontage();
  m.timeline.forEach(t => { delete t.start; });
  const cs = C.montageClips(m, [], idgen());
  assert.deepEqual(starts(cs), [0, 2.4, 4.9, 8.4, 11.6]);
  assert.equal(cs[2].name, "PAN_0102.MOV");
  assert.equal(cs[2].thumb, null);
});

// ---------------------------------------------------------------- "Usar esta toma"
test("swapTake: cambia la toma, re-empaca lo que sigue y conserva la union de entrada", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  const r = C.swapTake(cs, m, "s02", "c22", { sources: src, idgen: idgen() });
  const neu = r.clips.find(c => c._id === r.first);
  assert.equal(neu._id, cs[2]._id, "el primer tramo conserva su id (seleccion estable)");
  assert.deepEqual([neu.sid, neu.in, neu.out], ["E", 0.5, 4.0]);
  assert.deepEqual(neu.montage, { slot: "s02", cand: "c22", join: "section", conf: 0.55, setup: 3, cam: 3, ic: false });
  near(r.delta, 3.5 - 3.5);
  // c22 dura lo mismo que c21 (3.5 s): nada se mueve
  assert.deepEqual(starts(r.clips), [0, 2.4, 4.9, 8.4, 11.6]);
  // otra toma mas larga: lo que sigue se corre
  const r2 = C.swapTake(cs, m, "s03", "c31", { sources: src });
  assert.equal(r2.delta, 0);
  const r3 = C.swapTake(r.clips, m, "s04", "c42", { sources: src });
  assert.equal(r3.clips[4].sid, "H"); near(r3.delta, 0.2);
  // no se tocan los clips originales (el deshacer guarda el estado anterior)
  assert.equal(cs[2].sid, "E"); assert.equal(cs[2].in, 4.6);
});

test("swapTake: una parte intercalada (dos angulos) se vuelve UNA toma y lo demas se corre", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  const r = C.swapTake(cs, m, "s01", "c13", { sources: src });
  assert.equal(r.clips.length, 4);
  assert.deepEqual([r.clips[0].sid, r.clips[0].in, r.clips[0].out], ["A", 1.2, 7.4]);
  near(r.delta, 6.2 - 4.9);
  assert.deepEqual(starts(r.clips), [0, 6.2, 9.7, 12.9]);
  contiguous(r.clips);
  assert.equal(r.clips[0].name, "PAN_0098.MOV");
  assert.equal(r.clips[0].vol, 1, "sin vol ni tramo del mismo clip: 1");
  // volver a la elegida por la IA restaura los tramos intercalados tal como venian
  const back = C.swapTake(r.clips, m, "s01", "c11", { sources: src });
  assert.deepEqual(back.clips.map(c => [c.sid, c.in, c.out, c.montage.join]),
    cs.map(c => [c.sid, c.in, c.out, c.montage.join]));
  assert.deepEqual(starts(back.clips), starts(cs));
  assert.equal(back.clips[1].vol, 1.12);
});

test("swapTake: conserva huecos (negro), corre capas y stickers de despues y respeta mute/fades", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  // hueco de 1 s antes de s04 y un clip en otra capa despues de s02
  cs[4].start = 12.6;
  cs.push({ _id: "top", sid: "H", type: "video", in: 0, out: 2, start: 9, layer: 1, vol: 1 });
  cs[2].mute = true; cs[2].fadeIn = 0.5; cs[2].fadeOut = 0.3;
  const ov = [{ sid: "logo", start: 1, dur: 2 }, { sid: "logo", start: 10, dur: 1 }, { sid: "logo", start: 12, dur: 1 }];
  const r = C.swapTake(cs, m, "s02", "c21", { sources: src, overlays: ov });
  assert.equal(r.delta, 0);
  const r2 = C.swapTake(cs, m, "s02", "c22", { sources: src, overlays: ov });
  assert.equal(r2.delta, 0);
  // una toma 1 s mas corta en s03 (c31 recortada a mano no aplica: forzamos una alternativa corta)
  const m2 = synthMontage();
  m2.slots[2].cands.push({ id: "c32", sid: "F", in: 1.0, out: 3.2, score: 0.4, reason: "Corta" });
  const r3 = C.swapTake(cs, m2, "s03", "c32", { sources: src, overlays: ov });
  near(r3.delta, -1);
  const byId = Object.fromEntries(r3.clips.map(c => [c._id, c]));
  near(byId[cs[4]._id].start, 11.6, 1e-3);            // el hueco de 1 s se conserva
  near(byId.top.start, 9, 1e-3);                     // empieza antes del fin de s03 (11.6): no se mueve
  assert.deepEqual(r3.overlays.map(o => o.start), [1, 10, 11], "solo se corre lo que empieza despues de la parte");
  const e = r2.clips.find(c => c.montage && c.montage.slot === "s02");
  assert.equal(e.mute, true); assert.equal(e.fadeIn, 0.5); assert.equal(e.fadeOut, 0.3);
});

test("swapTake: una parte que se quito del video vuelve despues de la parte anterior", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen()).filter(c => c.montage.slot !== "s03");
  // se borro s03 (8.4-11.6): queda un hueco; vuelve al final de s02 y lo llena (s04 no se mueve)
  const r = C.swapTake(cs, m, "s03", "c31", { sources: src });
  assert.deepEqual(r.clips.map(c => c.montage.slot), ["s01", "s01", "s02", "s03", "s04"]);
  near(r.clips[3].start, 8.4); near(r.delta, 0);
  near(r.clips[4].start, 11.6);
  // sin hueco (se cerro): lo de despues se corre todo el largo
  const closed = C.removeSlot(C.montageClips(m, src, idgen()), "s03").clips;
  const rc = C.swapTake(closed, m, "s03", "c31", { sources: src });
  near(rc.delta, 3.2); near(rc.clips[4].start, 11.6);
  assert.equal(r.clips[3].montage.join, "line", "la union de la parte viene del montaje");
  // sin partes anteriores en el video: al inicio
  const only = C.montageClips(m, src, idgen()).filter(c => c.montage.slot === "s04");
  const r2 = C.swapTake(only, m, "s01", "c14", { sources: src });
  assert.equal(r2.clips[0].montage.slot, "s01"); assert.equal(r2.clips[0].start, 0);
  near(r2.clips[1].start, 11.6, 1e-6);   // cabe en el hueco del inicio: s04 no se mueve
  assert.equal(C.swapTake(cs, m, "nada", "c1"), null);
  assert.equal(C.swapTake(cs, m, "s01", "no-existe"), null);
});

test("candVol: la suya, la del mismo clip en el montaje o la ganancia hasta el objetivo LUFS", () => {
  const m = synthMontage();
  assert.equal(C.candVol(m, { id: "z", sid: "D", vol: 0.8 }), 0.8);
  assert.equal(C.candVol(m, { id: "c12", sid: "D" }), 1.12);
  assert.equal(C.candVol(m, { id: "z", sid: "F" }), 1.3);
  near(C.candVol(m, { id: "z", sid: "Q", lufs: -22 }), Math.pow(10, 6 / 20), 1e-3);
  assert.equal(C.candVol(m, { id: "z", sid: "Q", lufs: -60 }), 4, "limitado");
  assert.equal(C.candVol(m, { id: "z", sid: "Q" }), 1);
});

// ---------------------------------------------------------------- panel "Tomas"
test("montageRows: secciones, partes en orden del guion, sin toma y extra en su lugar", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  const rows = C.montageRows(m, cs);
  assert.deepEqual(rows.map(r => r.type), ["section", "slot", "section", "slot", "extra", "slot", "section", "uncovered", "slot"]);
  const s1 = rows[1];
  assert.equal(s1.n, 1); assert.equal(s1.cur, "c11"); assert.equal(s1.level, "alta");
  assert.deepEqual(s1.mixed, ["c12"], "intercalada con el otro angulo");
  assert.deepEqual(s1.alts.map(c => c.id), ["c12", "c13", "c14"]);
  assert.equal(s1.lines.length, 2); assert.match(s1.text, /^Hace un año .* mañana\.$/);
  assert.equal(rows[5].level, "media"); assert.equal(rows[8].level, "baja");
  assert.equal(rows[7].line.id, "L05");
  assert.equal(rows[4].after, "L03");
  // tras cambiar una toma: la elegida por la IA pasa a alternativa y se marca editada
  const r = C.swapTake(cs, m, "s04", "c42", { sources: src });
  const s4 = C.montageRows(m, r.clips).find(x => x.id === "s04");
  assert.equal(s4.cur, "c42"); assert.equal(s4.edited, true); assert.deepEqual(s4.alts.map(c => c.id), ["c41"]);
  // parte quitada del video
  const s3 = C.montageRows(m, cs.filter(c => c.montage.slot !== "s03")).find(x => x.id === "s03");
  assert.equal(s3.inVideo, false); assert.equal(s3.cur, null);
});

test("montageSummary: partes, sin toma, cambiadas, quitadas y confianza", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  assert.deepEqual(C.montageSummary(m, cs), { slots: 4, extras: 0, uncovered: 1, edited: 0, removed: 0, alta: 2, media: 1, baja: 1, discarded: 5 });
  const r = C.swapTake(cs, m, "s04", "c42", { sources: src });
  const s = C.montageSummary(m, r.clips.filter(c => c.montage.slot !== "s03"));
  assert.equal(s.edited, 1); assert.equal(s.removed, 1); assert.equal(s.media, 0);
});

test("montageConf: umbrales alta >= 0.8, media >= 0.6", () => {
  assert.deepEqual([0.95, 0.8, 0.79, 0.6, 0.59, null, "x"].map(C.montageConf), ["alta", "alta", "media", "media", "baja", "baja", "baja"]);
});

test("scriptText: lineas por renglon y linea en blanco entre secciones", () => {
  const m = synthMontage({}, { detected: true });
  const t = C.scriptText(m.script);
  assert.equal(t.split("\n\n").length, 3);
  assert.equal(t.split("\n").filter(Boolean).length, 6);
  assert.equal(C.scriptText({ text: "  hola  " }), "hola");
  assert.equal(C.scriptText({ lines: [{ id: "a", text: "Uno." }, { id: "b", text: "Dos." }] }), "Uno.\nDos.");
  assert.equal(C.scriptText(null), "");
});

// ---------------------------------------------------------------- POST /compose
test("composeBody: montage_job, guion y montage por item; sin montaje igual que antes", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  cs.push({ _id: "img", sid: "P", type: "image", dur: 2, start: 14.1, layer: 0 });
  const b = C.composeBody({ clips: cs, overlays: [{ sid: "logo", type: "image", start: 1, dur: 2, pos: "tr", size: 0.2 }],
    glossary: "Calle Olmo", highlights: "3", director: { style: "dinamico", intensity: 0.75 }, montageJob: "mt123", script: " Uno.\n\nDos. " });
  assert.equal(b.montage_job, "mt123");
  assert.equal(b.script, "Uno.\n\nDos.");
  assert.equal(b.main.length, 6);
  assert.deepEqual(b.main[1], { sid: "D", type: "video", in: 3.5, out: 6.0, vol: 1.12, mute: false, start: 2.4, layer: 0, fadeIn: 0, fadeOut: 0,
    trans: undefined, montage: { slot: "s01", cand: "c12", join: "splice", conf: 0.9, setup: 2 } });
  assert.equal(b.main[5].type, "image"); assert.equal(b.main[5].montage, undefined);
  assert.equal(b.highlights, 3); assert.equal(b.glossary, "Calle Olmo");
  assert.deepEqual(b.overlays[0], { sid: "logo", type: "image", start: 1, dur: 2, pos: "tr", size: 0.2 });
  // armado a mano: sin campos de montaje
  const hand = C.composeBody({ clips: [{ sid: "A", type: "video", in: 0, out: 3, vol: 1, mute: false, start: 0 }], director: false });
  assert.equal("montage_job" in hand, false); assert.equal("script" in hand, false);
  assert.equal(hand.main[0].montage, undefined); assert.equal(hand.director, false); assert.equal(hand.highlights, 2);
  // «¿Cuántos shorts sugerir?» = Ninguno viaja como 0 (antes se volvia 2); invalido -> 2; tope 5
  assert.equal(C.composeBody({ clips: cs, overlays: [], highlights: "0" }).highlights, 0);
  assert.equal(C.composeBody({ clips: cs, overlays: [], highlights: 0 }).highlights, 0);
  assert.equal(C.composeBody({ clips: cs, overlays: [], highlights: "1" }).highlights, 1);
  assert.equal(C.composeBody({ clips: cs, overlays: [], highlights: "" }).highlights, 2);
  assert.equal(C.composeBody({ clips: cs, overlays: [], highlights: "x" }).highlights, 2);
  assert.equal(C.composeBody({ clips: cs, overlays: [], highlights: "9" }).highlights, 5);
  assert.equal(C.composeBody({ clips: cs, overlays: [], highlights: "-3" }).highlights, 0);
  // guion vacio -> null
  assert.equal(C.composeBody({ clips: cs, montageJob: "j", script: "  " }).script, null);
  // JSON: `trans: undefined` no viaja
  assert.equal(JSON.parse(JSON.stringify(b)).main[0].trans, undefined);
});

// ---------------------------------------------------------------- estado del trabajo y progreso
test("montageStatus: en cola, escuchando n/N, eligiendo, listo y error", () => {
  assert.deepEqual(C.montageStatus({ phase: "assembly", state: "queued", message: "En cola…" }).stage, "queued");
  const w = C.montageStatus({ phase: "assembly", state: "running", message: "Escuchando cada clip 12/39", pct: 20 });
  assert.equal(w.state, "running"); assert.equal(w.stage, "words"); assert.equal(w.n, 12); assert.equal(w.N, 39);
  assert.equal(C.montageStatus({ phase: "assembly", state: "running", message: "Eligiendo las mejores tomas" }).stage, "match");
  assert.equal(C.montageStatus({ phase: "assembly", state: "running", stage: "match", message: "Escuchando" }).stage, "match");
  // viendo un video de apoyo: sigue en "Eligiendo..." con su mensaje (el n/m de tomas no son clips escuchados)
  const b = C.montageStatus({ phase: "assembly", state: "running", stage: "broll", message: "Viendo tu video de apoyo «a.mov» (3/20 tomas)…" });
  assert.equal(b.stage, "match"); assert.equal(b.broll, true); assert.equal(b.n, null); assert.match(b.message, /apoyo/);
  const m = synthMontage();
  const r = C.montageStatus({ phase: "assembly", state: "review", has_plan: true, plan: { montage: m } });
  assert.equal(r.state, "ready"); assert.equal(r.montage, m);
  assert.equal(C.montageStatus({ phase: "assembly", state: "done", has_plan: true }).state, "ready");
  assert.equal(C.montageStatus({ phase: "error", error: "sin audio" }).error, "sin audio");
  assert.equal(C.montageStatus({ phase: "assembly", state: "canceled" }).state, "error");
  assert.equal(C.montageStatus({ phase: "analyzing", state: "running" }).state, "other");
  assert.equal(C.montageStatus(null).state, "other");
});

test("wordsProgress y uploadPct", () => {
  assert.deepEqual(C.wordsProgress([{ words: "ready" }, { words: "pending" }, { words: "error" }, {}, { type: "image" }]),
    { n: 4, ready: 1, error: 1, pending: 2 });
  assert.equal(C.uploadPct([{ loaded: 50, total: 100 }, { loaded: 0, total: 100 }]), 25);
  assert.equal(C.uploadPct([{ loaded: 500, total: 100 }]), 100);
  assert.equal(C.uploadPct([]), 0);
});

test("projectState: proyectos recientes con armado con IA", () => {
  assert.deepEqual(C.projectState({ phase: "assembly", state: "running", has_plan: false }), { open: true, label: "armando con IA" });
  assert.deepEqual(C.projectState({ phase: "assembly", state: "review", has_plan: true }), { open: true, label: "tomas por revisar" });
  assert.deepEqual(C.projectState({ phase: "review", has_plan: true }), { open: true, label: "en edición" });
  assert.deepEqual(C.projectState({ phase: "done", has_plan: true }), { open: true, label: "exportado" });
  assert.deepEqual(C.projectState({ phase: "error", has_plan: false }), { open: false, label: "no se terminó" });
});

test("fmtSrc: minutos, segundos y decimas", () => {
  assert.equal(C.fmtSrc(4.13), "0:04.1");
  assert.equal(C.fmtSrc(65.96), "1:06.0");
  assert.equal(C.fmtSrc(-1), "0:00.0");
});

// ---------------------------------------------------------------- material extra (nutre el guion)
test("addExtra / extraInVideo / removeClip: entra despues de su linea, se corre lo demas y se quita sin hueco", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  const x = m.extra[0];                              // despues de L03 (parte s02: 4.9-8.4)
  assert.equal(C.extraInVideo(cs, x), -1);
  const r = C.addExtra(cs, m, x, { sources: src, idgen: () => "ex1", overlays: [{ start: 9, dur: 1 }] });
  assert.deepEqual(r.clips.map(c => c._id === "ex1" ? "X" : c.montage.slot), ["s01", "s01", "s02", "X", "s03", "s04"]);
  const e = r.clips[3];
  near(e.start, 8.4); near(r.delta, 1.0);
  assert.equal(e.extra, true); assert.equal(e.vol, 0.95, "mismo clip que s02: su volumen");
  assert.equal(e.name, "PAN_0102.MOV");
  near(r.clips[4].start, 9.4); near(r.clips[5].start, 12.6);
  assert.deepEqual(r.overlays.map(o => o.start), [10]);
  assert.equal(C.extraInVideo(r.clips, x), 3);
  // regresion: el extra viajaba sin union y el director le ponia transicion con whoosh a media seccion
  assert.deepEqual(C.composeMain(r.clips)[3].montage, { slot: null, cand: null, join: "line", conf: null }, "el extra entra con corte seco");
  assert.equal(e.montage.join, "line"); assert.deepEqual(C.slotClipIdx(r.clips, "s02"), [2], "no es parte de ninguna toma");
  // quitarlo cierra el hueco
  const q = C.removeClip(r.clips, "ex1");
  assert.deepEqual(starts(q.clips), starts(cs)); near(q.delta, -1);
  assert.equal(C.removeClip(r.clips, "nada"), null);
  // dos extras en el mismo punto: el segundo va despues del primero
  const x2 = Object.assign({}, x, { in: 0.2, out: 0.7 });
  const r2 = C.addExtra(r.clips, m, x2, { sources: src, idgen: () => "ex2" });
  const i2 = r2.clips.findIndex(c => c._id === "ex2");
  near(r2.clips[i2].start, 9.4); assert.equal(r2.clips[i2 - 1]._id, "ex1");
  // linea sin parte (L05, sin toma): despues de la parte anterior en el guion (s03)
  const r3 = C.addExtra(cs, m, Object.assign({}, x, { after: "L05" }), { sources: src, idgen: () => "ex3" });
  near(r3.clips.find(c => c._id === "ex3").start, 11.6);
  // linea desconocida: al final
  const r4 = C.addExtra(cs, m, Object.assign({}, x, { after: "L99" }), { sources: src, idgen: () => "ex4" });
  near(r4.clips.find(c => c._id === "ex4").start, 14.1);
  // un extra que ya venia en la linea de tiempo del montaje (sin slot) se marca extra
  const m2 = synthMontage();
  m2.timeline.push({ slot: null, cand: null, sid: "E", in: 8.3, out: 9.3, start: 14.1, vol: 0.95, join: "line" });
  const c2 = C.montageClips(m2, src, idgen());
  assert.equal(c2[5].extra, true); assert.equal(c2[5].montage, undefined);
  assert.equal(C.extraInVideo(c2, x), 5);
  assert.equal(C.composeMain(c2)[5].montage.join, "line");
  // partido en dos ("S"): la segunda mitad es un empalme de la misma toma
  const sp = C.composeMain([Object.assign({}, e, { montage: Object.assign({}, e.montage, { join: "splice" }) })])[0];
  assert.equal(sp.montage.join, "splice");
});

test("volumen sin medir: la toma entra con vol 'auto' (el servidor mide la voz) hasta que el usuario lo mueve", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  assert.ok(cs.every(c => !c.volAuto), "el montaje trae el vol de cada tramo");
  const r = C.swapTake(cs, m, "s01", "c13", { sources: src });   // clip A: sin vol, sin tramo del mismo clip, sin LUFS
  assert.equal(r.clips[0].volAuto, true); assert.equal(r.clips[0].vol, 1);
  assert.equal(C.composeMain(r.clips)[0].vol, "auto");
  assert.equal(C.candVolKnown(m, { id: "z", sid: "Q" }), null);
  // el usuario lo movio: numero
  delete r.clips[0].volAuto; r.clips[0].vol = 0.8;
  assert.equal(C.composeMain(r.clips)[0].vol, 0.8);
  // un clip suelto (sin montaje) nunca manda "auto"
  assert.equal(C.composeMain([{ sid: "Z", type: "video", in: 0, out: 1, vol: 1, volAuto: true }])[0].vol, 1);
  // montaje con vol "auto" en su linea de tiempo
  const m2 = synthMontage(); m2.timeline[0].vol = "auto";
  const c2 = C.montageClips(m2, src, idgen());
  assert.equal(c2[0].volAuto, true); assert.equal(C.composeMain(c2)[0].vol, "auto");
});

test("montageStatus: etapas del servidor (words/montage) y progreso de la tarea lateral words", () => {
  const s = C.montageStatus({ phase: "assembly", state: "running", stage: "words", message: "Transcribiendo tus clips (3/8)…", words: { done: 3, total: 8, errors: 1 } });
  assert.deepEqual([s.stage, s.n, s.N, s.errors], ["words", 3, 8, 1]);
  const s2 = C.montageStatus({ phase: "assembly", state: "running", stage: "montage", message: "Eligiendo las mejores tomas…" });
  assert.equal(s2.stage, "match");
  const s3 = C.montageStatus({ phase: "assembly", state: "running", message: "Transcribiendo tus clips (5/8)…" });
  assert.deepEqual([s3.stage, s3.n, s3.N], ["words", 5, 8]);
  assert.equal(C.montageStatus({ phase: "assembly", state: "review", stage: "assembly", has_plan: true }).state, "ready");
});

test("recordingOrder: la fecha del ARCHIVO (mtime) no es de grabacion; created_ts manda si viene", () => {
  const s = [
    { id: "copia", name: "IMG_0001.MOV", created: "2026-01-01T08:00:00Z", created_src: "mtime" },
    { id: "b", name: "IMG_0300.MOV", created: "2026-01-01T10:00:00Z", created_src: "creation_time" },
    { id: "a", name: "IMG_0400.MOV", created: "2026-01-01T12:00:00Z", created_src: "apple", created_ts: Date.parse("2026-01-01T09:00:00Z") / 1000 },
  ];
  assert.deepEqual(C.recordingOrder(s).map(x => x.id), ["a", "b", "copia"]);
  assert.ok(Number.isNaN(C.recordedMs(s[0])));
});

test("extra aprobado por el montaje (parte sin lineas): en su lugar, sin numero, se quita y vuelve", () => {
  const m = synthMontage(null, { keptExtra: true }), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  assert.deepEqual(cs.map(c => c.montage.slot), ["s01", "s01", "s02", "sx1", "s03", "s04"]);
  assert.deepEqual(C.slotNumbers(m), { s01: 1, s02: 2, s03: 3, s04: 4 }, "los extras no cuentan como parte del guion");
  assert.equal(C.isExtraSlot(m.slots[2]), true); assert.equal(C.isExtraSlot(m.slots[0]), false);
  const rows = C.montageRows(m, cs);
  assert.deepEqual(rows.map(r => r.type + (r.extra ? "+" : "")), ["section", "slot", "section", "slot", "slot+", "slot", "section", "uncovered", "slot"]);
  const ex = rows[4];
  assert.equal(ex.n, null); assert.equal(ex.text, "La receta es de mi abuela."); assert.deepEqual(ex.alts, []); assert.equal(ex.inVideo, true);
  assert.equal(C.montageSummary(m, cs).extras, 1); assert.equal(C.montageSummary(m, cs).slots, 4);
  // quitarlo cierra el hueco; volver a ponerlo (su toma elegida) lo deja donde estaba
  const r = C.removeSlot(cs, "sx1");
  assert.deepEqual(starts(r.clips), [0, 2.4, 4.9, 8.4, 11.6]); near(r.delta, -1);
  assert.equal(C.montageRows(m, r.clips).find(x => x.id === "sx1").inVideo, false);
  const back = C.swapTake(r.clips, m, "sx1", "cx1", { sources: src });
  assert.deepEqual(back.clips.map(c => c.montage.slot), ["s01", "s01", "s02", "sx1", "s03", "s04"]);
  assert.deepEqual(starts(back.clips), starts(cs));
  assert.equal(C.removeSlot(cs, "nada"), null);
  // una parte intercalada se quita entera
  const r2 = C.removeSlot(cs, "s01");
  assert.equal(r2.clips.length, 4); near(r2.delta, -4.9); assert.equal(r2.clips[0].start, 0);
});

test("confianza: la del montaje por tramo (timeline.conf) manda sobre el puntaje de la toma", () => {
  const m = synthMontage(null, { conf: { s01: 0.55, s02: 0.95, s03: 0.61, s04: 0.2 } }), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  assert.deepEqual(cs.map(c => c.montage.conf), [0.55, 0.55, 0.95, 0.61, 0.2]);
  assert.deepEqual(C.montageRows(m, cs).filter(r => r.type === "slot").map(r => r.level), ["baja", "alta", "media", "baja"]);
  // otra toma: su puntaje; volver a la de la IA: la confianza del montaje
  const r = C.swapTake(cs, m, "s02", "c22", { sources: src });
  assert.equal(r.clips[2].montage.conf, 0.55);
  const b = C.swapTake(r.clips, m, "s02", "c21", { sources: src });
  assert.equal(b.clips[2].montage.conf, 0.95);
  assert.equal(C.itemConf(m, { slot: "s02", cand: "c22" }), 0.55);
});

test("montageStatus: un rearmado o una composicion que fallo vuelve a las tomas con su error", () => {
  const r = C.montageStatus({ phase: "assembly", state: "review", has_plan: true, error: "Montaje falló: sin voz" });
  assert.equal(r.state, "ready"); assert.equal(r.note, "Montaje falló: sin voz");
  assert.equal(C.montageStatus({ phase: "assembly", state: "review", has_plan: true }).note, null);
});

test("volver a poner una parte (o un extra) llena el hueco que dejo: lo de despues no se corre si cabe", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  // se borro s03 (8.4-11.6) sin cerrar el hueco (Eliminar del timeline): s04 sigue en 11.6
  const holed = cs.filter(c => c.montage.slot !== "s03");
  const r = C.swapTake(holed, m, "s03", "c31", { sources: src });
  assert.deepEqual(starts(r.clips), [0, 2.4, 4.9, 8.4, 11.6]); assert.equal(r.delta, 0);
  // hueco mas chico que la toma: se corre solo lo que falta
  const tight = holed.map(c => (c.montage.slot === "s04" ? Object.assign({}, c, { start: 10.4 }) : c));
  const r2 = C.swapTake(tight, m, "s03", "c31", { sources: src });
  near(r2.delta, 1.2); near(r2.clips[4].start, 11.6);
  // extra en un hueco del mismo tamano
  const gap = cs.map(c => (["s03", "s04"].includes(c.montage.slot) ? Object.assign({}, c, { start: c.start + 1 }) : c));
  const r3 = C.addExtra(gap, m, m.extra[0], { sources: src, idgen: () => "g1" });
  assert.equal(r3.delta, 0); near(r3.clips.find(c => c._id === "g1").start, 8.4); near(r3.clips[4].start, 9.4);
});

// ---------------------------------------------------------------- revision: volumen, confianza, partes partidas, avisos
test("Usar esta toma: una alternativa de un clip que no estaba en el video entra con el vol del montaje (no 'auto')", () => {
  const m = synthMontage(), src = SOURCES();
  const cs = C.montageClips(m, src, idgen());
  // el montaje trae el vol de CADA toma con la misma referencia que la linea de tiempo
  m.slots[0].cands.find(c => c.id === "c13").vol = 1.41;
  const r = C.swapTake(cs, m, "s01", "c13", { sources: src, idgen: () => "n1" });
  const c = r.clips.find(x => x.sid === "A");
  assert.equal(c.vol, 1.41); assert.equal(c.volAuto, undefined);
  const main = C.composeMain(r.clips);
  assert.equal(main.find(x => x.sid === "A").vol, 1.41, "regresion: iba como 'auto' y compose la dejaba a 1.0");
  // sin vol en la toma (servidor viejo): sigue el camino de antes ("auto", lo mide el servidor)
  const m2 = synthMontage();
  const r2 = C.swapTake(C.montageClips(m2, src, idgen()), m2, "s01", "c13", { sources: src, idgen: () => "n2" });
  assert.equal(C.composeMain(r2.clips).find(x => x.sid === "A").vol, "auto");
});

test("confianza de una toma elegida por el usuario: la que el montaje calcula para ella (misma escala)", () => {
  const m = synthMontage(), src = SOURCES();
  m.timeline[2].conf = 0.62;                                  // s02: la IA eligio c21 en un casi empate
  m.slots[1].cands[1].conf = 0.6;                             // c22 si fuera la elegida
  const cs = C.montageClips(m, src, idgen());
  const r = C.swapTake(cs, m, "s02", "c22", { sources: src, idgen: () => "k1" });
  const c = r.clips.find(x => x.montage && x.montage.slot === "s02");
  assert.equal(c.montage.conf, 0.6);
  assert.equal(C.montageConf(c.montage.conf), C.montageConf(0.62), "las dos: media");
  assert.equal(C.candConf({ score: 0.9 }), 0.9); assert.equal(C.candConf({ score: 0.9, conf: 0.55 }), 0.55); assert.equal(C.candConf(null), 0);
  // la fila muestra el puntaje de la toma en uso (la misma medida que las alternativas)
  const row = C.montageRows(m, r.clips).find(x => x.type === "slot" && x.id === "s02");
  assert.equal(row.score, 0.55); assert.equal(row.said, "Nuestro secreto es la masa, la masa madre de 48 horas.");
});

test("una linea partida en dos partes (empalme): cada tarjeta muestra SU pedazo y 1/2, 2/2", () => {
  const m = synthMontage(), src = SOURCES();
  // s02 y s03 cubren la MISMA linea L03 (dos tomas empalmadas a media linea)
  m.slots[1].text = "Nuestro secreto es la masa madre";
  m.slots[2].lines = ["L03"]; m.slots[2].text = "de 48 horas.";
  const cs = C.montageClips(m, src, idgen());
  const rows = C.montageRows(m, cs).filter(r => r.type === "slot");
  const a = rows.find(r => r.id === "s02"), b = rows.find(r => r.id === "s03");
  assert.equal(a.text, "Nuestro secreto es la masa madre"); assert.deepEqual(a.part, { i: 1, n: 2 });
  assert.equal(b.text, "de 48 horas."); assert.deepEqual(b.part, { i: 2, n: 2 });
  assert.notEqual(a.text, b.text, "regresion: las dos mostraban la linea completa");
  // una parte normal: la linea completa y sin marca
  const s1 = rows.find(r => r.id === "s01");
  assert.equal(s1.part, null); assert.ok(s1.text.startsWith("Hace un año"));
  // L04 (que ya no tiene parte) sale como "sin toma"
  assert.ok(C.montageRows(m, cs).some(r => r.type === "uncovered" && r.line.id === "L04"));
});

test("motivo de la toma en uso: el de 'perdedora' comparativo se vuelve neutro si el usuario la elige", () => {
  assert.equal(C.usedReason({ reason: "Igual de buena; se usa la otra cámara para intercalar ángulos" }, true),
    "Toma completa y fiel al guion (igual de buena que la de la IA)");
  assert.equal(C.usedReason({ reason: "Toma anterior (en empate se usa la última)" }, true),
    "Toma completa y fiel al guion (igual de buena que la de la IA)");
  assert.equal(C.usedReason({ reason: "Igual de buena; se usa la otra cámara para intercalar ángulos" }, false),
    "Igual de buena; se usa la otra cámara para intercalar ángulos");
  assert.equal(C.usedReason({ reason: "Dice «x» en vez de «y»" }, true), "Dice «x» en vez de «y»", "sus defectos se conservan");
  const m = synthMontage(), src = SOURCES();
  m.slots[0].cands[1].reason = "Igual de buena; se usa la otra cámara para intercalar ángulos";
  const r = C.swapTake(C.montageClips(m, src, idgen()), m, "s01", "c12", { sources: src, idgen: () => "u1" });
  const row = C.montageRows(m, r.clips).find(x => x.type === "slot" && x.id === "s01");
  assert.ok(row.edited && !/intercalar/.test(row.reason), row.reason);
});

test("montageStatus: la composicion interrumpida (la app se cerro) avisa al reabrir aunque no traiga error", () => {
  const m = synthMontage();
  const st = C.montageStatus({ phase: "assembly", state: "review", has_plan: true, error: null,
    message: "La composición se interrumpió (la app se cerró). Tu montaje sigue aquí.", plan: { montage: m } });
  assert.equal(st.state, "ready"); assert.match(st.note, /se interrumpió/);
  const ok = C.montageStatus({ phase: "assembly", state: "review", has_plan: true, error: null, message: "Montaje listo: revisa las tomas" });
  assert.equal(ok.note, null, "el aviso normal no se muestra");
  const bad = C.montageStatus({ phase: "assembly", state: "review", has_plan: true, error: "La composición falló: x", message: "..." });
  assert.equal(bad.note, "La composición falló: x");
});
