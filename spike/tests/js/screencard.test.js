// Pruebas unitarias de app/static/screencard.js (window.ScreenCard): la grabacion de pantalla
// sincronizada del Studio. La paridad con motion/screen.py esta en tests/test_screen_card.py.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const SC = require(path.join(__dirname, "..", "..", "app", "static", "screencard.js"));
const FC = require(path.join(__dirname, "..", "..", "app", "static", "floatcard.js"));

const item = (o) => Object.assign({ id: "sc01", start: 2, end: 18, style: "screen", sid: "x", w: 670, h: 1436,
  sync: { offset: 5, rate: 1, pieces: [[2, 10, 1], [10, 18, 15]] } }, o || {});

test("sincronia: cada pedazo con su segundo de camara; en la union gana el siguiente", () => {
  const it = item();
  assert.equal(SC.syncTime(it, 2), 6);
  assert.ok(Math.abs(SC.syncTime(it, 9.999) - (5 + 1 + 7.999)) < 1e-9);
  assert.equal(SC.syncTime(it, 10), 20);
  assert.equal(SC.syncTime(it, 30), 28);          // fuera: el borde mas cercano
  assert.equal(SC.syncTime({ sync: {} }, 1), null);
  assert.deepEqual(SC.coverage(it), [2, 18]);
  assert.deepEqual(SC.coverage(item({ start: 0, end: 40 })), [2, 18]);
  assert.equal(SC.coverage(item({ start: 19, end: 30 })), null);
});

test("ventana centrada y simetrica al pixel, arriba de los subtitulos, desde TOP", () => {
  const c = SC.layout(670, 1436, 9 / 16, 0.66, 0.79, null, null);
  assert.equal(c.cx, 0.5);
  assert.ok(c.cy + c.h / 2 <= 0.66 - 0.015 + 1e-4);
  assert.ok(Math.abs(c.cy - c.h / 2 - SC.TOP) < 1e-4);      // ocupa toda la banda: de TOP a los subtitulos
  for (const bw of [216, 540, 1080, 1081, 2160]) {
    const [x0, , w] = FC.cardPx(c, bw, bw * 16 / 9);
    assert.equal(x0, Math.round(bw) - (x0 + w));
  }
  assert.ok(Math.abs(c.aspect - 670 / 1436) < 1e-4);
});

test("marco recortado: la proporcion es la de lo que hay dentro y el rect fuente sale del crop", () => {
  const crop = [19 / 670, 26 / 1436, 651 / 670, 1398 / 1436];
  const it = item({ crop });
  assert.deepEqual(SC.cropOf(it), crop);
  const c = SC.layout(670, 1436, 9 / 16, 0.66, 0.79, null, SC.cropOf(it));
  assert.ok(Math.abs(c.aspect - 632 / 1372) < 1e-4);
  const r = SC.srcRect(it, 670, 1436);
  assert.ok(Math.abs(r[0] - 19) < 1e-9 && Math.abs(r[1] - 26) < 1e-9 && Math.abs(r[2] - 632) < 1e-9 && Math.abs(r[3] - 1372) < 1e-9);
  assert.equal(SC.cropOf(item({ crop: [0, 0, 1, 1] })), null);       // completo = sin recorte
  assert.equal(SC.cropOf(item({ crop: [0.2, 0.2, 0.25, 0.9] })), null);   // un marco absurdo no se usa
  assert.deepEqual(SC.srcRect(item(), 670, 1436), [0, 0, 670, 1436]);
});

test("movimiento: con el corte en Minimal, tranquila en Elegante, con vida flota sin inclinarse", () => {
  assert.equal(SC.motionOf({}, "minimal"), "cut");
  assert.equal(SC.motionOf({}, null), "cut");
  assert.equal(SC.motionOf({}, "elegante"), "calm");
  assert.equal(SC.motionOf({}, "dinamico"), "lively");
  assert.equal(SC.motionOf({ motion: "lively" }, "minimal"), "lively");
  const c0 = SC.anim(0, 10, "cut");
  assert.deepEqual([c0.alpha, c0.scale, c0.dy, c0.bg], [1, 1, 0, 1]);     // en su lugar desde el primer cuadro
  for (let t = 0; t <= 10; t += 0.5) for (const m of SC.MOTIONS) assert.equal(SC.anim(t, 10, m).alpha, 1);
  const a = SC.anim(3, 10, "calm"), b = SC.anim(7, 10, "calm");
  assert.deepEqual([a.alpha, a.scale, a.dy, a.tilt], [1, 1, 0, 0]);
  assert.deepEqual([a.alpha, a.scale, a.dy], [b.alpha, b.scale, b.dy]);
  const ys = new Set();
  for (let t = 0.5; t < 9.9; t += 0.25) { const s = SC.anim(t, 10, "lively"); assert.equal(s.tilt, 0); assert.ok(s.scale > 0.97); ys.add(s.dy.toFixed(6)); }
  assert.ok(ys.size > 3);
});

test("fondo: filtro CSS del video con el desenfoque de la pantalla", () => {
  assert.equal(SC.bgFilter(item(), 0, 1080), "");
  assert.match(SC.bgFilter(item(), 1, 1080), /^blur\([0-9.]+px\) brightness\(0\.720\)$/);
});

test("dibujo: la ventana recorta la fuente a su marco", () => {
  const calls = [];
  const g = new Proxy({}, { get: (o, k) => (k in o ? o[k] : (k === "getTransform" ? undefined : (...a) => { calls.push([k, a]); })),
    set: (o, k, v) => { o[k] = v; return true; } });
  const it = item({ crop: [0.1, 0.05, 0.9, 0.95] });
  const B = { x: 0, y: 0, w: 1080, h: 1920 };
  const card = SC.layout(670, 1436, 9 / 16, 0.66, 0.79, null, SC.cropOf(it));
  SC.draw(g, { src: {}, w: 670, h: 1436 }, B, card, SC.anim(1, 10, "cut"), it, 1, 10);
  const di = calls.find(c => c[0] === "drawImage");
  assert.ok(di, "drawImage");
  const [, sx, sy, sw, sh] = di[1];
  assert.ok(sx >= 0.1 * 670 - 1e-6 && sy >= 0.05 * 1436 - 1e-6 && sx + sw <= 0.9 * 670 + 1e-6 && sy + sh <= 0.95 * 1436 + 1e-6, di[1]);
});
