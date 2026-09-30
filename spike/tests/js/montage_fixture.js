// Montaje SINTETICO (inventado) para probar "Armar con IA" sin material real: una panaderia ficticia,
// 8 clips (A..H) grabados en desorden de nombre, 3 secciones y 6 lineas de guion. Tiene de todo:
// una parte intercalada entre dos angulos (s01), una parte con alternativas, confianza alta, media y
// baja, una linea sin toma (L05), descartadas de cada tipo y material extra que nutre el guion.
// Lo usan tests/js/montage.test.js (node) y tests/js/montage_e2e.mjs (navegador).
"use strict";

/** Clips de la grabacion: nombre, fecha de grabacion (ISO 8601) y duracion. El orden de SUBIDA
    (upload) es otro a proposito: el editor debe ordenarlos por `created`. */
const CLIPS = [
  { key: "A", name: "PAN_0098.MOV", created: "2026-03-14T10:02:05-06:00", dur: 9.0 },
  { key: "B", name: "PAN_0099.MOV", created: "2026-03-14T10:03:40-06:00", dur: 8.0 },
  { key: "C", name: "PAN_0100.MOV", created: "2026-03-14T10:05:12-06:00", dur: 7.5 },
  { key: "D", name: "PAN_0101.MOV", created: "2026-03-14T10:06:30-06:00", dur: 7.0 },
  { key: "E", name: "PAN_0102.MOV", created: "2026-03-14T10:11:02-06:00", dur: 9.5 },
  // el contador de la camara dio la vuelta: PAN_0001 se grabo DESPUES de PAN_0102
  { key: "F", name: "PAN_0001.MOV", created: "2026-03-14T10:15:47-06:00", dur: 6.5 },
  { key: "G", name: "PAN_0002.MOV", created: "2026-03-14T10:20:10-06:00", dur: 6.0 },
  { key: "H", name: "PAN_0003.MOV", created: "2026-03-14T10:21:55-06:00", dur: 6.0 },
];
const UPLOAD_ORDER = ["F", "C", "A", "H", "E", "B", "G", "D"];

const SCRIPT = [
  "Hace un año abrimos una panadería pequeña en el centro.",
  "Hoy horneamos más de 300 panes cada mañana.",
  "",
  "Nuestro secreto es la masa madre de 48 horas.",
  "Y cada pieza se forma a mano, sin prisas.",
  "",
  "Si pasas por la calle Olmo, el primer café va por nuestra cuenta.",
  "Síguenos para ver cómo lo hacemos.",
].join("\n");

/** plan.montage con la forma del contrato. `sid` = {A: sid, ...} (por defecto la letra misma). */
function synthMontage(sid, o) {
  const S = k => (sid && sid[k]) || k;
  const opt = o || {};
  const lines = [
    { id: "L01", text: "Hace un año abrimos una panadería pequeña en el centro." },
    { id: "L02", text: "Hoy horneamos más de 300 panes cada mañana." },
    { id: "L03", text: "Nuestro secreto es la masa madre de 48 horas." },
    { id: "L04", text: "Y cada pieza se forma a mano, sin prisas." },
    { id: "L05", text: "Si pasas por la calle Olmo, el primer café va por nuestra cuenta." },
    { id: "L06", text: "Síguenos para ver cómo lo hacemos." },
  ];
  const slots = [
    { id: "s01", lines: ["L01", "L02"], chosen: "c11", cands: [
      { id: "c11", sid: S("C"), in: 0.8, out: 6.2, w0: 0, w1: 18, text: lines[0].text + " " + lines[1].text, score: 0.93, fidelity: 1, coverage: 1, flags: [], reason: "Última toma completa y fiel al guion", setup: 1 },
      { id: "c12", sid: S("D"), in: 0.9, out: 6.0, w0: 0, w1: 18, text: lines[0].text + " " + lines[1].text, score: 0.9, fidelity: 1, coverage: 1, flags: ["angulo"], reason: "Misma frase desde el otro ángulo", setup: 2 },
      { id: "c13", sid: S("A"), in: 1.2, out: 7.4, w0: 0, w1: 19, text: "Hace unos años abrimos una panadería pequeña en el centro. Hoy horneamos más de 300 panes cada mañana.", score: 0.62, fidelity: 0.9, coverage: 1, flags: ["variante"], reason: "Dice «hace unos años» en vez de «hace un año»", setup: 1 },
      { id: "c14", sid: S("B"), in: 0.4, out: 4.6, w0: 0, w1: 10, text: lines[0].text, score: 0.38, fidelity: 0.95, coverage: 0.55, flags: ["sin_microfono", "incompleta"], reason: "Toma sin micrófono y sin la segunda línea", setup: 1 },
    ] },
    { id: "s02", lines: ["L03"], chosen: "c21", cands: [
      { id: "c21", sid: S("E"), in: 4.6, out: 8.1, w0: 11, w1: 20, text: lines[2].text, score: 0.88, fidelity: 1, coverage: 1, flags: [], reason: "Última toma completa y fiel al guion", setup: 3 },
      { id: "c22", sid: S("E"), in: 0.5, out: 4.0, w0: 0, w1: 9, text: "Nuestro secreto es la masa, la masa madre de 48 horas.", score: 0.55, fidelity: 0.82, coverage: 1, flags: ["tropiezo"], reason: "Se traba en «masa madre»", setup: 3 },
    ] },
    { id: "s03", lines: ["L04"], chosen: "c31", cands: [
      { id: "c31", sid: S("F"), in: 1.0, out: 4.2, w0: 0, w1: 9, text: lines[3].text, score: 0.66, fidelity: 0.86, coverage: 1, flags: ["variante"], reason: "Dice «se hace a mano» en vez de «se forma a mano»", setup: 4 },
    ] },
    { id: "s04", lines: ["L06"], chosen: "c41", cands: [
      { id: "c41", sid: S("G"), in: 0.5, out: 3.0, w0: 0, w1: 7, text: lines[5].text, score: 0.52, fidelity: 1, coverage: 1, flags: ["prisa"], reason: "Completa pero dicha con prisa", setup: 5 },
      { id: "c42", sid: S("H"), in: 0.7, out: 3.4, w0: 0, w1: 7, text: lines[5].text, score: 0.5, fidelity: 1, coverage: 1, flags: [], reason: "Igual de fiel; entonación más plana", setup: 5 },
    ] },
  ];
  const timeline = [
    { slot: "s01", cand: "c11", sid: S("C"), in: 0.8, out: 3.2, start: 0, vol: 1.0, join: null },
    { slot: "s01", cand: "c12", sid: S("D"), in: 3.5, out: 6.0, start: 2.4, vol: 1.12, join: "splice" },
    { slot: "s02", cand: "c21", sid: S("E"), in: 4.6, out: 8.1, start: 4.9, vol: 0.95, join: "section" },
    { slot: "s03", cand: "c31", sid: S("F"), in: 1.0, out: 4.2, start: 8.4, vol: 1.3, join: "line" },
    { slot: "s04", cand: "c41", sid: S("G"), in: 0.5, out: 3.0, start: 11.6, vol: 1.0, join: "section" },
  ];
  const order = CLIPS.map((c, i) => ({ sid: S(c.key), name: c.name, rec: i, created: c.created }));
  const extra = [{ sid: S("E"), in: 8.3, out: 9.3, after: "L03", why: "nutre el guion", text: "La receta es de mi abuela.", kept: false }];
  if (opt.keptExtra) {
    // como lo arma el servidor: el extra aprobado es una parte SIN lineas, en su lugar de la linea de tiempo
    extra[0].kept = true;
    slots.splice(2, 0, { id: "sx1", lines: [], text: extra[0].text, chosen: "cx1", extra: true, cands: [
      { id: "cx1", sid: S("E"), in: 8.3, out: 9.3, w0: 20, w1: 25, text: extra[0].text, score: 1, fidelity: 0, coverage: 0, flags: ["fuera_de_guion"],
        reason: "Fuera de guion pero aporta a la historia", setup: 3 }] });
    timeline.splice(3, 0, { slot: "sx1", cand: "cx1", sid: S("E"), in: 8.3, out: 9.3, start: 8.4, vol: 0.95, join: "line", conf: 0.5 });
    timeline[4].start = 9.4; timeline[5].start = 12.6;
  }
  if (opt.conf) timeline.forEach(t => { if (t.conf == null) t.conf = opt.conf[t.slot]; });
  return {
    version: 1,
    order,
    script: {
      source: opt.detected ? "detected" : "user",
      text: SCRIPT,
      sections: [{ id: 1, lines: ["L01", "L02"] }, { id: 2, lines: ["L03", "L04"] }, { id: 3, lines: ["L05", "L06"] }],
      lines,
    },
    slots,
    timeline,
    discarded: [
      { sid: S("A"), in: 7.6, out: 8.8, text: "Perdón, otra vez.", why: "retoma" },
      { sid: S("B"), in: 5.0, out: 7.4, text: "¡Ay, no! Se me olvidó.", why: "blooper" },
      { sid: S("G"), in: 3.4, out: 5.6, text: "¿Ya está grabando?", why: "fuera_de_guion" },
      { sid: S("H"), in: 3.6, out: 5.2, text: "Síguenos para...", why: "incompleta" },
      { sid: S("B"), in: 0.0, out: 0.4, text: "", why: "sin_audio" },
    ],
    uncovered: ["L05"],
    extra,
    stats: { clips: 8, slots: 4, target_lufs: -16 },
    ms: 412,
  };
}

module.exports = { CLIPS, UPLOAD_ORDER, SCRIPT, synthMontage };
