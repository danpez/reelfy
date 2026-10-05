// Pruebas de la logica pura de «Quién sale en el video» (perfiles guardados): la huella de los datos de
// un perfil (fxcore.profileDataKey: cambios sin guardar / otra version del guardado) y las piezas de
// app/static/profile.js (misma persona, completar con lo entendido, soltar la foto de otra cuenta).
// profile.js se carga en un contexto sin DOM (vm) como en el navegador. Datos FICTICIOS.
// Corre con: node --test spike/tests/js/profile.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const STATIC = path.join(__dirname, "..", "..", "app", "static");
const C = require(path.join(STATIC, "fxcore.js"));

function loadPF() {
  const window = { FXCore: C };
  const ctx = vm.createContext({ window, console, setTimeout, clearTimeout, JSON, Object, Array, String, Promise });
  vm.runInContext(fs.readFileSync(path.join(STATIC, "profile.js"), "utf8"), ctx, { filename: "profile.js" });
  return window.PF;
}
const PF = loadPF();
// los objetos hechos dentro del contexto vm tienen otro prototipo: se comparan como JSON
const J = x => JSON.parse(JSON.stringify(x));

const IG = (h, extra) => Object.assign({ url: `https://instagram.com/${h}`, platform: "instagram", kind: "social", handle: `@${h}`, display: `@${h}`, label: "Instagram", primary: true }, extra || {});
const PROF = () => ({ id: "dra-ana-demo", updated: 1700000000, media_status: "done", name: "Dra. Ana Demo", title: "Odontóloga", business: "", phone: "", whatsapp: "", address: "",
  links: [IG("dra.ana.demo", { photo: { sid: "auto1", type: "image" }, media_src: { photo: "auto" }, media_for: "https://instagram.com/dra.ana.demo" })],
  use: { lower_third: true, follow_cta: true, booking_cta: true, end_card: true, handle_watermark: false } });

test("profileDataKey: igual sin id, fecha, estado de descarga, campos del Studio ni foto automatica", () => {
  const a = PROF(), b = JSON.parse(JSON.stringify(a));
  delete b.id; delete b.updated; b.media_status = "fetching"; b.links[0]._mediaTried = true; b.links[0]._h0 = "@dra.ana.demo";
  b.links[0].photo = { sid: "otra_auto", type: "image" };                      // la cache trajo otra: no es un cambio del usuario
  b.links[0].url = "https://www.instagram.com/dra.ana.demo/";                   // misma url escrita distinto
  assert.equal(C.profileDataKey(a), C.profileDataKey(b));
  assert.equal(C.profileDataKey(null), "");
});

test("profileDataKey: detecta lo que el usuario cambia", () => {
  const k = C.profileDataKey(PROF());
  const ch = f => { const p = PROF(); f(p); return C.profileDataKey(p); };
  assert.notEqual(ch(p => { p.title = "Dentista"; }), k);
  assert.notEqual(ch(p => { p.links[0].handle = "@dra.ana.nueva"; }), k);
  assert.notEqual(ch(p => { p.links[0].display = "Ana en IG"; }), k);
  assert.notEqual(ch(p => { p.links[0].photo = { sid: "mia" }; p.links[0].media_src = { photo: "user" }; }), k);
  assert.notEqual(ch(p => { p.links[0].media_off = { photo: true }; }), k);
  assert.notEqual(ch(p => { p.links.push({ url: "https://paseo.club/business/dra-ana-demo", platform: "paseoclub" }); }), k);
  assert.notEqual(ch(p => { p.avatar = { sid: "cara" }; }), k);
  // los interruptores de «Dónde aparece»: cuentan en el panel, no al comparar con el video
  const u = ch(p => { p.use.handle_watermark = true; });
  assert.notEqual(u, k);
  const p2 = PROF(); p2.use.handle_watermark = true;
  assert.equal(C.profileDataKey(p2, { use: false }), C.profileDataKey(PROF(), { use: false }));
  // un enlace vacio (sin url ni usuario) no cuenta
  assert.equal(ch(p => { p.links.push({ platform: "web" }); }), k);
});

test("samePerson / otherPerson: con o sin titulo, acentos; otra persona no", () => {
  assert.equal(PF.samePerson({ name: "Dra. Ana Demo" }, { name: "Ana Demo" }), true);
  assert.equal(PF.samePerson({ name: "Dra. Ana Demo" }, { name: "dra. ana demo ruiz" }), true);
  assert.equal(PF.samePerson({ name: "Lucía Fernández" }, { name: "Lucia Fernandez" }), true);
  assert.equal(PF.samePerson({ name: "Dra. Ana Demo" }, { name: "Pedro Ficticio" }), false);
  assert.equal(PF.samePerson({ name: "" }, { name: "Ana Demo" }), false);        // sin nombre: no se adivina
  assert.equal(PF.otherPerson({ name: "Dra. Ana Demo" }, { name: "Pedro Ficticio" }), true);
  // un nombre de pila solo NO es la misma persona (antes «Ana» se ofrecia para pisar a «Dra. Ana Demo López»)
  assert.equal(PF.samePerson({ name: "Dra. Ana Demo López" }, { name: "Ana" }), false);
  assert.equal(PF.samePerson({ name: "Ana" }, { name: "Ana Lucía Pérez" }), false);
  assert.equal(PF.samePerson({ name: "Ana" }, { name: "Dra. Ana" }), true);       // el mismo nombre
});

test("mergeInto («Completar ese perfil»): lo escrito gana y NADA del guardado se pierde", () => {
  // reporte de la revision: «Actualizar ese perfil» con un borrador de solo nombre y profesion borraba
  // Instagram, la pagina de reservas, WhatsApp y direccion del guardado
  const twin = Object.assign(PROF(), { business: "Clínica Demo", whatsapp: "+52 55 0000 0001", phone: "+52 33 0000 0001", address: "Av. Demo 1",
    avatar: { sid: "cara_demo" }, use: { lower_third: true, follow_cta: true, booking_cta: false, end_card: true, handle_watermark: true } });
  twin.links.push({ url: "https://paseo.club/business/dra-ana-demo", platform: "paseoclub", kind: "booking", label: "Agenda en PaseoClub" },
    { url: "https://wa.me/525500000001", platform: "whatsapp", kind: "messaging", display: "+52 55 0000 0001", from_field: true });
  const draft = { name: "Dra. Ana Demo", title: "Ortodoncista", business: "", phone: "", whatsapp: "", address: "", links: [],
    use: { lower_third: true, follow_cta: true, booking_cta: true, end_card: true, handle_watermark: false } };
  const m = PF._t.mergeInto(twin, draft);
  assert.equal(m.id, "dra-ana-demo"); assert.equal(m.title, "Ortodoncista");
  assert.equal(m.business, "Clínica Demo"); assert.equal(m.whatsapp, "+52 55 0000 0001"); assert.equal(m.phone, "+52 33 0000 0001");
  assert.equal(m.address, "Av. Demo 1"); assert.deepEqual(J(m.avatar), { sid: "cara_demo" });
  assert.deepEqual(J(m.links.map(l => l.platform)), ["instagram", "paseoclub", "whatsapp"]);
  assert.equal(m.links[0].photo.sid, "auto1");                                     // su foto se queda
  assert.deepEqual(J(m.use), twin.use);                                               // el borrador traia los de siempre
  assert.equal(m.updated, undefined); assert.equal(m.media_status, undefined);
  // lo que el borrador SI trae: su Instagram (otra cuenta) reemplaza al de la misma red; un enlace nuevo
  // se suma; un interruptor cambiado pasa
  const d2 = Object.assign({}, draft, { whatsapp: "+52 55 0000 0002", links: [IG("dra.ana.nueva"), { url: "https://tiktok.com/@dra.ana.tt", platform: "tiktok", kind: "social", handle: "@dra.ana.tt" }],
    use: Object.assign({}, draft.use, { handle_watermark: true, booking_cta: false }) });
  const m2 = PF._t.mergeInto(twin, d2);
  assert.deepEqual(J(m2.links.map(l => [l.platform, l.handle || ""])), [["instagram", "@dra.ana.nueva"], ["paseoclub", ""], ["whatsapp", ""], ["tiktok", "@dra.ana.tt"]]);
  assert.equal(m2.links[0].photo, undefined);                                       // nunca la foto de la cuenta vieja
  assert.equal(m2.whatsapp, "+52 55 0000 0002");                                    // el servidor rehace su enlace (from_field)
  assert.equal(m2.links.filter(l => l.primary).length, 1);
  assert.equal(m2.use.handle_watermark, true); assert.equal(m2.use.booking_cta, false);
  // en el Studio sin tocar los interruptores (withUse false): los del guardado se quedan
  assert.deepEqual(J(PF._t.mergeInto(twin, d2, false).use), twin.use);
  assert.equal(twin.title, "Odontóloga");                                           // no toca el original
});

test("dialogo de mismo nombre: enfocado lo que no toca nada guardado; dice cual es cual", () => {
  const a = Object.assign(PROF(), { title: "Odontóloga" }), b = Object.assign(PROF(), { id: "dra-ana-demo-2", title: "Abogada", links: [] });
  const one = PF._t.twinDialog({ name: "Dra. Ana Demo" }, [a]);
  assert.equal(one.choices[0].id, "new");                                           // primero (y enfocado): «Guardar otro aparte»
  assert.deepEqual(J(one.choices.map(c => c.id)), ["new", "upd:dra-ana-demo"]);
  assert.match(one.text, /Odontóloga/); assert.match(one.text, /conserva sus enlaces/);
  const two = PF._t.twinDialog({ name: "Dra. Ana Demo" }, [a, b]);
  assert.deepEqual(J(two.choices.map(c => c.label)), ["Guardar otro aparte", "Completar «Dra. Ana Demo · Odontóloga»", "Completar «Dra. Ana Demo · Abogada»"]);
  assert.match(two.text, /Abogada/);
});

test("PF.label: dos perfiles con el mismo nombre se distinguen en las listas", () => {
  const a = Object.assign(PROF(), { title: "Odontóloga" }), b = Object.assign(PROF(), { id: "dra-ana-demo-2", title: "Abogada", links: [] });
  const c = { id: "pedro-demo", name: "Pedro Ficticio" };
  assert.equal(PF.label(a, [a, b, c]), "Dra. Ana Demo · Odontóloga");
  assert.equal(PF.label(b, [a, b, c]), "Dra. Ana Demo · Abogada");
  assert.equal(PF.label(c, [a, b, c]), "Pedro Ficticio");                          // sin otro igual: solo el nombre
  assert.equal(PF.label(a, [a, c]), "Dra. Ana Demo");
  // misma profesion: el siguiente dato que los distingue (negocio, @usuario...)
  const d = Object.assign(PROF(), { id: "dra-ana-demo-3", links: [IG("otra.ana")] });
  assert.equal(PF.label(d, [a, d]), "Dra. Ana Demo · @otra.ana");
});

test("clean: sin campos del Studio, fecha, estado ni enlaces vacios", () => {
  const p = PROF(); p.links[0]._mediaTried = true; p.links[0]._refetch = true; p.links.push({ platform: "web" });
  const c = PF._t.clean(p);
  assert.equal(c.updated, undefined); assert.equal(c.media_status, undefined);
  assert.equal(c.links.length, 1);
  assert.deepEqual(Object.keys(c.links[0]).filter(k => k[0] === "_"), []);
  assert.equal(c.id, "dra-ana-demo");                                          // el id liga al guardado
  assert.equal(p.links[0]._mediaTried, true);                                  // no toca el original
});

test("dropAutoMedia: la foto/logo de otra cuenta fuera; lo subido por el usuario se queda", () => {
  const l = IG("x", { photo: { sid: "auto1" }, logo: { sid: "mio" }, media_src: { photo: "auto", logo: "user" }, media_for: "https://instagram.com/x", _mediaTried: true });
  PF._t.dropAutoMedia(l);
  assert.equal(l.photo, undefined); assert.equal(l.media_for, undefined); assert.equal(l._mediaTried, false);
  assert.deepEqual(l.logo, { sid: "mio" }); assert.deepEqual(l.media_src, { logo: "user" });
  const l2 = IG("y", { photo: { sid: "a" }, media_src: { photo: "auto" } });
  PF._t.dropAutoMedia(l2);
  assert.equal(l2.media_src, undefined); assert.equal(l2.photo, undefined);
});

test("merge (misma persona): lo entendido gana; otra cuenta de la misma red reemplaza el enlace y su foto", () => {
  const d = PROF();
  PF._t.merge(d, { name: "Dra. Ana Demo", title: "Dentista", links: [IG("dra.ana.nueva"), { url: "https://paseo.club/business/dra-ana-demo", platform: "paseoclub", kind: "booking" }] });
  assert.equal(d.title, "Dentista");
  assert.deepEqual(d.links.map(l => [l.platform, l.handle || ""]), [["instagram", "@dra.ana.nueva"], ["paseoclub", ""]]);
  assert.equal(d.links[0].photo, undefined);                                   // la foto de la cuenta vieja no se arrastra
  assert.equal(d.links.filter(l => l.primary).length, 1);
  // la misma url escrita distinto (www, barra final) no se duplica ni pierde su foto
  const e = PROF();
  PF._t.merge(e, { links: [IG("dra.ana.demo", { url: "https://www.instagram.com/dra.ana.demo/" })] });
  assert.equal(e.links.length, 1); assert.equal(e.links[0].photo.sid, "auto1");
});

test("replaced (otra persona): solo lo entendido, sin el id del guardado, con los interruptores", () => {
  const r = PF._t.replaced({ name: "Pedro Ficticio", title: "Chef", links: [IG("pedro.demo")] }, { handle_watermark: true });
  assert.equal(r.name, "Pedro Ficticio"); assert.equal(r.id, undefined); assert.equal(r.phone, "");
  assert.equal(r.use.handle_watermark, true); assert.equal(r.links[0].primary, true);
});

test("dialog.js sin DOM (node) no hace nada ni truena", () => {
  // (la GUARDA contra confirm/alert/prompt nativos en el Studio y la app: tests/test_profile_e2e.py)
  const ctx = vm.createContext({ window: {} });
  vm.runInContext(fs.readFileSync(path.join(STATIC, "dialog.js"), "utf8"), ctx);
  assert.equal(ctx.window.Dlg, undefined);
});
