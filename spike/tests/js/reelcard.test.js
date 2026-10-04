// Pruebas unitarias de app/static/reelcard.js (window.ReelCard): los videos de apoyo en fragmentos del
// Studio (tiempo de cada toma, recortes y lo que se puede cambiar). La paridad con motion/reel.py esta en
// tests/test_reel_card.py y tests/test_brollplan.py.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const R = require(path.join(__dirname, "..", "..", "app", "static", "reelcard.js"));
const FC = require(path.join(__dirname, "..", "..", "app", "static", "floatcard.js"));

const shot = (i, a, b, o) => Object.assign({ i, a, b, cut_a: true, cut_b: true, subject: "ui", what: `toma ${i}`,
  ok_window: true, ok_full: true, cx: 0.5, win_crop: null }, o || {});
const plan = () => ({
  montage: { broll: { sources: [{ sid: "ap1", name: "apoyo.mp4", shots: [
    shot(0, 0, 2), shot(1, 2, 4, { subject: "person", ok_window: false, ok_full: false }), shot(2, 4, 6, { ok_window: false }),
    shot(3, 6, 8), shot(4, 8, 10, { ok_full: false }), shot(5, 10, 12), shot(6, 12, 12.3)] }] } },
  fx: { media: [
    { id: "br01", style: "reel", sid: "ap1", layout: "window", start: 1, end: 4, pieces: [
      { shot: 0, in: 0.08, out: 1.5, max: 1.92 }, { shot: 4, in: 8.08, out: 9.5, max: 9.92 }] },
    { id: "br02", style: "reel", sid: "ap1", layout: "full", start: 6, end: 9, pieces: [
      { shot: 3, in: 6.08, out: 7.5, max: 7.92, cx: 0.5 }, { shot: 5, in: 10.08, out: 11.5, max: 11.92, cx: 0.5 }] }] },
});

test("fit: las tomas se reparten el tramo final en cuadros enteros", () => {
  const ps = [{ in: 1, out: 3, max: 3.5 }, { in: 10, out: 12, max: 12.4 }];
  assert.deepEqual(R.fit(ps, 4, 30), [[0, 2, 1, 0, 3.5], [2, 4, 10, 1, 12.4]]);
  const r = R.fit(ps, 3, 30);
  assert.ok(Math.abs(r[0][1] - 1.5) < 1e-6 && Math.abs(r[1][1] - 3) < 1e-6);
  assert.equal(R.pieceAt(r, 1.5), 1);                       // en la union gana la siguiente
  assert.equal(R.srcTime(r, 0.5), 1.5);
  assert.deepEqual(R.fit([], 3), []);
});

test("fit: nunca se lee la fuente en o despues del fin de la toma (Quitar / Cambiar / Fin alargado)", () => {
  // Quitar la tercera pieza: el item conserva su largo y la 9 tendria que pasar de su max (41.42)
  const ps = [{ shot: 4, in: 12.08, out: 13.6, max: 13.92 }, { shot: 9, in: 40.08, out: 41.4, max: 41.42 }];
  for (const L of [4.87, 6, 9]) {
    const rows = R.fit(ps, L, 30);
    for (let k = 0; k < L * 30; k++) {
      const lt = k / 30, r = rows[R.pieceAt(rows, lt)], t = R.srcTime(rows, lt);
      assert.ok(t < r[4] - 1e-6, `L=${L} k=${k} t=${t} max=${r[4]}`);
    }
    // al final se queda el ultimo cuadro de la toma 9 (el mismo que el render: reel.hold_frames)
    const last = rows[rows.length - 1];
    assert.ok(Math.abs(R.srcTime(rows, L - 0.01) - (last[2] + (R.holdFrames(last, 30) - 1) / 30)) < 1e-9);
  }
  assert.equal(R.srcTime([[0, 2, 1, 0]], 1), 2);     // filas viejas sin tope
});

test("recortes: pantalla completa al ancho de la caja centrado en cx; ventana con la proporcion de la fuente", () => {
  const c = R.fullCrop(0.3, 1712, 960, 9 / 16);
  assert.ok(Math.abs((c[2] - c[0]) * 1712 / 960 - 9 / 16) < 1e-4 && c[1] === 0 && c[3] === 1);
  assert.ok(Math.abs((c[0] + c[2]) / 2 - 0.3) < 1e-4);
  assert.deepEqual(R.fullCrop(0.0, 1712, 960, 9 / 16)[0], 0);        // pegado a la orilla, nunca fuera
  assert.equal(R.fullCrop(0.5, 1080, 1920, 9 / 16), null);          // ya vertical: entera
  const it = { pieces: [{ in: 0, out: 1 }] };
  const card = R.windowLayout(it, 1712, 960, 9 / 16, 0.66, 0.79);
  assert.equal(card.cx, 0.5);
  assert.ok(Math.abs(card.aspect - FC.ASPECT_MAX) < 1e-4 && card.cy + card.h / 2 <= 0.66);
  assert.equal(R.motionOf({ layout: "full", motion: "lively" }, "dinamico"), "cut");
  assert.equal(R.motionOf({ layout: "window" }, "minimal"), "cut");
});

test("cambiar una toma: solo las que sirven en el formato y no salen en otro fragmento", () => {
  const p = plan(), it = p.fx.media[0];
  const ids = R.options(p, it).map(s => s.i);
  // ventana: no la persona (1), no la que solo sirve en pantalla completa (2), no las de br02 (3, 5) ni las
  // propias (0, 4), no la de 0.3 s (6)
  assert.deepEqual(ids, []);
  const full = R.options(p, p.fx.media[1]).map(s => s.i);
  assert.deepEqual(full.sort(), [2]);
  const ps = R.swap(p.fx.media[1], 0, p.montage.broll.sources[0].shots[2], "La elegiste tú.");
  assert.equal(ps[0].shot, 2);
  assert.ok(Math.abs(ps[0].in - 4.08) < 1e-6 && ps[0].out <= ps[0].max && ps[0].max <= 6 - R.MARGIN + 1e-9);
  assert.equal(ps[0].why, "La elegiste tú.");
  assert.equal(ps[1].shot, 5);                               // la otra no cambia
});

test("cambiar de formato: lo que no sirve ahi se cambia por la toma libre mas cercana (o no se puede)", () => {
  const p = plan();
  // br01 a pantalla completa: la toma 4 no sirve ahi; la 2 (libre) si
  const got = R.relayout(p, p.fx.media[0], "full", "x");
  assert.deepEqual(got.map(q => q.shot), [0, 2]);
  // br02 a ventana: la 3 y la 5 sirven; nada cambia
  assert.deepEqual(R.relayout(p, p.fx.media[1], "window").map(q => q.shot), [3, 5]);
  // si no alcanzan, null (el Studio desactiva esa opcion)
  p.montage.broll.sources[0].shots[2].ok_full = false;
  assert.equal(R.relayout(p, p.fx.media[0], "full"), null);
  assert.equal(R.relayout({ fx: { media: [] } }, p.fx.media[0], "full"), null);
});

test("lo usado por otros fragmentos y los margenes del corte", () => {
  const p = plan();
  assert.deepEqual([...R.usedShots(p, "br01")].sort(), ["ap1|3", "ap1|5"]);
  assert.deepEqual(R.avail({ a: 2, b: 4 }), [2.08, 3.92]);
  assert.deepEqual(R.avail({ a: 2, b: 4, cut_a: false, cut_b: false }), [2, 4]);
});

test("la pista Recursos nombra cada fragmento por lo que se dice", () => {
  const C = require(path.join(__dirname, "..", "..", "app", "static", "fxcore.js"));
  assert.equal(C.itemLabel(null, "media", { style: "reel", name: "apoyo.mp4", text: "Se llama   Nubis y es un asistente nuevo" }),
    "Apoyo: Se llama Nubis y es");
  assert.equal(C.itemLabel(null, "media", { style: "reel", name: "apoyo.mp4" }), "apoyo.mp4");
  assert.equal(C.itemLabel(null, "media", { style: "float_card", name: "foto" }), "foto");
});
