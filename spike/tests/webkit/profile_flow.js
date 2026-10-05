// Flujo E2E de «Quién sale en el video» (perfiles guardados) DENTRO de la pagina: el mismo texto corre en
// el WKWebView de la app de escritorio (tests/webkit/wkflow.swift: confirm/alert/prompt no muestran nada,
// como en Tauri/wry) y en Chrome con esos dialogos sustituidos por el comportamiento de WebKit
// (tests/js/profile_e2e.mjs). Es el CUERPO de una funcion async que devuelve un JSON {ok, steps, ...}.
// El servidor es tests/profile_devserver.py (red ficticia: la foto de cada @usuario es un PNG liso de un
// color propio) con el proyecto sintetico pf_demo. Solo datos ficticios.
//  - En «/» (Inicio): crear (pegar descripcion) -> guardar -> foto de su Instagram -> usar en el proximo
//    video -> reabrir -> editar -> «Guardar cambios» (sin copia) -> el ejemplo de Inicio al dia -> cambiar
//    el @usuario (url rehecha, foto de la cuenta nueva, nunca la vieja) -> «Guardar y usar…» con cambios
//    sin guardar -> mismo nombre sin ligar (el dialogo dice cual es; enfocado «Guardar otro aparte»;
//    Escape cancela) -> «Completar ese perfil» (conserva enlaces, negocio y foto) -> dos perfiles con el
//    mismo nombre distinguidos en las listas -> doble clic = un solo guardado -> Escape / X con cambios
//    preguntan («Seguir editando» / «Descartar cambios») -> borrados en otra ventana: listas al dia al
//    volver -> eliminar con la confirmacion propia (Escape cancela, el foco vuelve y se queda en el panel)
//    -> todas las listas al dia.
//  - En «/?job=pf_demo» (Studio): usar un perfil guardado -> un interruptor del video no es «Cambios sin
//    guardar» -> editar (nombre, @usuario, WhatsApp, pagina de reservas) y «Guardar y aplicar al video»
//    (rotulo, Seguir, marca de agua, chips del cierre y fotos re-generados con los datos nuevos, el
//    proyecto sigue ligado) -> «Volver a buscar la foto» -> el guardado cambia en otra ventana: aviso al
//    volver, nota en el panel (simetrica), nunca se pisa sin preguntar («Usar la versión guardada»;
//    409 -> «Reemplazar con la de aquí») -> eliminar desde el Studio (el video conserva sus datos; ⌘Z no
//    deshace detras; Escape cierra) -> mismos nombres en «Usar un perfil guardado…» -> plantillas (pedir
//    nombre con «/», confirmar), salir con cambios sin guardar, Montar clips sin clips y la clave de
//    Pexels: todo con el dialogo propio. Ningun dialogo nativo en todo el flujo.
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (Date.now() - t0 < (ms || 5000)) { try { const v = await f(); if (v) return v; } catch (e) { /* aun no */ } await sleep(100); } return null; };
const rep = { ua: navigator.userAgent, page: location.pathname + location.search, steps: [] };
const ok = (name, cond, extra) => { rep.steps.push({ name, ok: !!cond, extra: extra == null ? null : extra }); if (!cond) throw new Error("falla: " + name + " " + JSON.stringify(extra == null ? null : extra).slice(0, 600)); };
const $ = s => document.querySelector(s);
const setVal = (el, v, change) => { el.focus(); el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); if (change !== false) el.dispatchEvent(new Event("change", { bubbles: true })); };
const api = async (u, m, b) => { const r = await fetch(u, { method: m || "GET", headers: b ? { "Content-Type": "application/json" } : {}, body: b ? JSON.stringify(b) : undefined }); let d = {}; try { d = await r.json(); } catch (e) { d = {}; } return d; };
const snap = async n => { if (window.__snap) { try { await window.__snap(n); } catch (e) { /* sin capturas */ } } };
const key = (k, o) => (document.activeElement || document.body).dispatchEvent(new KeyboardEvent("keydown", Object.assign({ key: k, bubbles: true, cancelable: true }, o || {})));
const M = () => $("#pfm");
const mq = s => M() && M().querySelector(s);
const saved = async () => (await api("/profiles")).profiles || [];
const igRow = () => [...(M() ? M().querySelectorAll(".pflink") : [])].find(r => r.querySelector('select[data-k="platform"]').value === "instagram");
const igLink = () => (PF.draft() && PF.draft().links || []).find(l => l.platform === "instagram");
const dlg = () => $(".rdlgw");
const dlgBtn = a => dlg() && dlg().querySelector(`[data-a="${a}"]`);
const natives = () => (window.__nativeDialogs || []).slice();
const opts = sel => [...document.querySelectorAll(sel + " option")].map(o => [o.value, o.textContent.trim()]);
/** Color medio del centro de una imagen ya cargada -> "r,g,b" (la foto ficticia es lisa). */
const imgColor = async im => {
  if (!im) return null;
  if (!im.complete || !im.naturalWidth) await new Promise(r => { im.onload = r; im.onerror = r; setTimeout(r, 3000); });
  const c = document.createElement("canvas"); c.width = 8; c.height = 8; const g = c.getContext("2d");
  g.drawImage(im, im.naturalWidth / 2 - 4, im.naturalHeight / 2 - 4, 8, 8, 0, 0, 8, 8);
  const d = g.getImageData(0, 0, 8, 8).data; let r = 0, gg = 0, b = 0; for (let i = 0; i < d.length; i += 4) { r += d[i]; gg += d[i + 1]; b += d[i + 2]; }
  const n = d.length / 4; return [r / n, gg / n, b / n].map(Math.round).join(",");
};
const near = (a, b) => { if (!a || !b) return false; const x = a.split(",").map(Number), y = b.split(",").map(Number); return x.every((v, i) => Math.abs(v - y[i]) <= 24); };
const DESC = "Soy la Dra. Ana Demo, odontóloga. Instagram https://instagram.com/dra.ana.demo · Agenda: https://paseo.club/business/dra-ana-demo";
const canvasHash = cv => { if (!cv || !cv.width) return 0; const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data; let h = 2166136261 >>> 0; for (let i = 0; i < d.length; i += 16) { h ^= d[i] ^ (d[i + 1] << 8) ^ (d[i + 2] << 16); h = Math.imul(h, 16777619) >>> 0; } return h; };

try {
  if (!/[?&]job=/.test(location.search)) {
    // ================================================================ Inicio
    ok("Inicio listo", await until(() => window.PF && window.Dlg && $("#homeProfile select") && $("#asmProfile select"), 30000));
    await api("/__fake/reset", "POST", {});
    for (const p of await saved()) await fetch("/profiles/" + p.id, { method: "DELETE" });
    await PF.load(true); PF.syncAll();
    try { localStorage.removeItem("reelfy.profile.sel"); } catch (e) { /* sin storage */ }
    $("#homeProfile [data-e]").click();
    ok("A1 «Crear perfil» abre el panel", await until(() => PF.isOpen(), 5000));
    setVal(mq('[data-a="ptext"]'), DESC, false);
    mq('[data-a="parse"]').click();
    ok("A2 entiende la descripción", await until(() => mq('[data-a="use"]'), 15000));
    mq('[data-a="use"]').click();
    ok("A3 usa los datos", PF.draft().name === "Dra. Ana Demo" && igLink(), PF.draft().name);
    mq('[data-a="save"]').click();
    const s1 = await until(async () => { const s = await saved(); return s.length === 1 && s; }, 6000);
    ok("A4 guarda el perfil", !!s1, s1 && s1.map(x => x.id));
    const id = s1[0].id;
    ok("A5 ya ligado: «Guardar cambios» y eliminar", /Guardar cambios/.test(mq('[data-a="save"]').textContent) && !!mq('[data-a="del"]'), mq('[data-a="save"]').textContent);
    const ph1 = await until(() => igRow() && igRow().querySelector(".pfmed.photo img"), 15000);
    ok("A6 la foto de su Instagram llega sola al panel", !!ph1);
    const c1 = await imgColor(ph1), sid1 = igLink().photo.sid;
    ok("A7 «Volver a buscar la foto» está a la mano con la foto puesta", !!igRow().querySelector('.pfmed.photo [data-m="auto:photo"]'));
    await snap("A_inicio_panel");
    mq('[data-a="apply"]').click();
    ok("A8 «Usar en el próximo video»", await until(() => !PF.isOpen() && $("#homeProfSel").value === id && $("#asmProfile select").value === id, 3000), opts("#homeProfSel"));
    ok("A9 el resumen de Inicio lo dice", /Odontóloga/.test($("#homeProfile .pfsum").textContent), $("#homeProfile .pfsum").textContent);
    const exCv = $("#homeProfileEx canvas"); if (window.Estilo && Estilo.Ticker) Estilo.Ticker.flush();
    const ex0 = canvasHash(exCv);

    // reabrir y editar: «Guardar cambios» actualiza (sin copia «-2»)
    $("#homeProfile [data-e]").click();
    await until(() => PF.isOpen(), 3000);
    ok("A10 reabrir: ligado al guardado", /Guardar cambios/.test(mq('[data-a="save"]').textContent) && !!mq('[data-a="del"]') && mq('[data-a="saved"]').value === id);
    setVal(mq('[data-f="title"]'), "Dentista");
    ok("A11 avisa los cambios sin guardar y el botón principal los guarda", mq('[data-a="state"]').textContent === "Cambios sin guardar" && /Guardar y usar en el próximo video/.test(mq('[data-a="apply"]').textContent), mq('[data-a="apply"]').textContent);
    mq('[data-a="save"]').click();
    const s2 = await until(async () => { const s = await saved(); return s.length === 1 && s[0].title === "Dentista" && s; }, 5000);
    ok("A12 «Guardar cambios» actualiza el mismo (sin copia)", !!s2 && s2[0].id === id, (await saved()).map(x => [x.id, x.title]));
    ok("A13 sin cambios pendientes tras guardar", mq('[data-a="state"]').textContent === "" && /Usar en el próximo video/.test(mq('[data-a="apply"]').textContent));
    ok("A14 el resumen de Inicio al día sin recargar", /Dentista/.test($("#homeProfile .pfsum").textContent) && PF.selected().title === "Dentista", $("#homeProfile .pfsum").textContent);
    if (window.Estilo && Estilo.Ticker) Estilo.Ticker.flush();
    ok("A15 el ejemplo de Inicio se vuelve a dibujar", !exCv || canvasHash(exCv) !== ex0);

    // cambiar el @usuario: url rehecha al instante, la foto vieja fuera y la nueva llega
    const hIn = igRow().querySelector('[data-k="handle"]');
    setVal(hIn, "@dra.ana.nueva");
    ok("A16 el enlace se rehace con el @usuario nuevo", await until(() => igLink() && /instagram\.com\/dra\.ana\.nueva$/.test(igLink().url) && igLink().display === "@dra.ana.nueva", 5000), igLink());
    ok("A17 nunca queda la foto de la cuenta vieja", !(igLink().photo && igLink().photo.sid === sid1) && ![...igRow().querySelectorAll("img")].some(i => i.dataset.sid === sid1));
    const ph2 = await until(() => igRow() && igLink().photo && igLink().photo.sid !== sid1 && igRow().querySelector(".pfmed.photo img"), 15000);
    const c2 = await imgColor(ph2);
    ok("A18 llega la foto de la cuenta nueva (otra imagen)", !!ph2 && /dra\.ana\.nueva/.test(igLink().photo.src || "") && c2 && !near(c1, c2), { c1, c2, src: igLink().photo && igLink().photo.src });
    await snap("A_inicio_usuario_nuevo");
    mq('[data-a="save"]').click();
    const s3 = await until(async () => { const s = await saved(); const l = s[0] && s[0].links.find(x => x.platform === "instagram"); return s.length === 1 && l && l.handle === "@dra.ana.nueva" && l; }, 5000);
    ok("A19 guardado con el @usuario nuevo", !!s3 && /dra\.ana\.nueva/.test(s3.url) && (!s3.photo || /dra\.ana\.nueva/.test(s3.photo.src || "")), s3);

    // cambios sin guardar + el boton principal: se guardan (no se pierden al recargar)
    setVal(mq('[data-f="business"]'), "Clínica Demo");
    ok("A20 «Guardar y usar en el próximo video»", /Guardar y usar en el próximo video/.test(mq('[data-a="apply"]').textContent));
    mq('[data-a="apply"]').click();
    ok("A21 se guardó y se usa", await until(async () => !PF.isOpen() && (await saved())[0].business === "Clínica Demo" && PF.selected().business === "Clínica Demo", 5000));

    // mismo nombre sin ligar: el dialogo pregunta y dice cual es; ENFOCADO «Guardar otro aparte» (Enter
    // nunca pisa nada guardado); Escape cancela
    const hp = $("#homeProfSel"); setVal(hp, "__new");
    ok("A22 «Nuevo perfil…» abre un panel en blanco", await until(() => PF.isOpen() && !mq('[data-a="del"]') && PF.draft().name === "", 3000));
    setVal(mq('[data-f="name"]'), "Ana Demo"); setVal(mq('[data-f="title"]'), "Ortodoncista");
    mq('[data-a="save"]').click();
    ok("A23 pregunta y dice cuál perfil guardado es", await until(() => dlg() && dlg().dataset.kind === "choose" && /Dra\. Ana Demo/.test(dlg().textContent) && /Dentista/.test(dlg().textContent), 3000), dlg() && dlg().textContent);
    ok("A24 diálogo accesible", dlg().getAttribute("role") === "alertdialog" && dlg().getAttribute("aria-modal") === "true" && document.getElementById(dlg().getAttribute("aria-labelledby")) && dlg().contains(document.activeElement));
    ok("A24b enfocado «Guardar otro aparte» (no toca nada guardado); «Completar ese perfil» aparte", document.activeElement === dlgBtn("new") && /Completar ese perfil/.test((dlgBtn("upd:" + id) || {}).textContent || ""), document.activeElement && document.activeElement.textContent);
    await snap("A_inicio_mismo_nombre");
    key("Escape");
    ok("A25 Escape cancela (no guarda nada)", await until(() => !dlg(), 2000) && (await saved()).length === 1);
    mq('[data-a="save"]').click();
    await until(() => dlg(), 2000);
    dlgBtn("new").click();
    const s4 = await until(async () => { const s = await saved(); return s.length === 2 && s; }, 5000);
    ok("A26 «Guardar otro aparte» crea otro (con su propio nombre, no «-2»)", !!s4 && s4.some(x => x.id === "ana-demo") && s4.find(x => x.id === id).title === "Dentista", s4 && s4.map(x => [x.id, x.title]));
    ok("A27 las listas lo ofrecen sin recargar", opts("#homeProfSel").some(o => o[0] === "ana-demo") && opts("#asmProfile select").some(o => o[0] === "ana-demo"));

    // «Completar ese perfil»: le agrega lo escrito y CONSERVA sus enlaces, negocio y foto (antes
    // «Actualizar ese perfil» lo reemplazaba entero con un Enter)
    const before = (await saved()).find(x => x.id === id);
    setVal($("#homeProfSel"), "__new");
    await until(() => PF.isOpen() && PF.draft().name === "", 3000);
    setVal(mq('[data-f="name"]'), "Dra. Ana Demo"); setVal(mq('[data-f="title"]'), "Endodoncista");
    mq('[data-a="save"]').click();
    ok("A27b dos guardados con ese nombre: cada opción dice cuál es", await until(() => dlg() && dlgBtn("upd:" + id) && dlgBtn("upd:ana-demo"), 3000)
      && /Dentista/.test(dlgBtn("upd:" + id).textContent) && /Ortodoncista/.test(dlgBtn("upd:ana-demo").textContent) && document.activeElement === dlgBtn("new"), dlg() && [...dlg().querySelectorAll("button")].map(b => b.textContent));
    await snap("A_inicio_completar");
    dlgBtn("upd:" + id).click();
    const done = await until(async () => { const x = (await saved()).find(p => p.id === id); return x && x.title === "Endodoncista" && x; }, 5000);
    ok("A27c completado: la profesión nueva y NADA del guardado se pierde", !!done && done.business === "Clínica Demo" && done.name === "Dra. Ana Demo"
      && done.links.some(l => l.handle === "@dra.ana.nueva") && done.links.some(l => l.platform === "paseoclub") && (await saved()).length === 2
      && (await saved()).find(x => x.id === "ana-demo").title === "Ortodoncista", done);
    ok("A27d el panel queda ligado a ese perfil con todos sus datos", /Guardar cambios/.test(mq('[data-a="save"]').textContent) && mq('[data-a="saved"]').value === id && PF.draft().business === "Clínica Demo" && !!igLink(), mq('[data-a="msg"]').textContent);
    ok("A27e lo dice", /conservaron sus enlaces/.test(mq('[data-a="msg"]').textContent), mq('[data-a="msg"]').textContent);
    ok("A27f la foto de su Instagram sigue", before.links.some(l => l.photo) ? done.links.some(l => l.platform === "instagram" && l.photo && l.photo.sid) : true);

    // dos perfiles con el MISMO nombre: las listas los distinguen
    setVal($("#homeProfSel"), "__new");
    await until(() => PF.isOpen() && PF.draft().name === "", 3000);
    setVal(mq('[data-f="name"]'), "Dra. Ana Demo"); setVal(mq('[data-f="title"]'), "Abogada");
    mq('[data-a="save"]').click();
    await until(() => dlg() && dlgBtn("new"), 3000);
    dlgBtn("new").click();
    const twin = await until(async () => (await saved()).find(x => x.id === id + "-2"), 5000);
    ok("A27g «Guardar otro aparte» con el mismo nombre exacto", !!twin && twin.title === "Abogada" && (await saved()).find(x => x.id === id).title === "Endodoncista");
    const lab = (sel, v) => (opts(sel).find(o => o[0] === v) || [])[1] || "";
    ok("A27h las listas distinguen a los dos «Dra. Ana Demo»", ["#homeProfSel", "#asmProfile select", '#pfm [data-a="saved"]'].every(sel => lab(sel, id) === "Dra. Ana Demo · Endodoncista" && lab(sel, id + "-2") === "Dra. Ana Demo · Abogada"),
      ["#homeProfSel", "#asmProfile select", '#pfm [data-a="saved"]'].map(sel => [lab(sel, id), lab(sel, id + "-2")]));
    await snap("A_inicio_mismo_nombre_listas");

    // doble clic en «Guardar perfil»: un solo guardado (antes, una copia «-2» muda)
    setVal($("#homeProfSel"), "__new");
    await until(() => PF.isOpen() && PF.draft().name === "", 3000);
    setVal(mq('[data-f="name"]'), "Dr. Doble Demo");
    const sb = mq('[data-a="save"]'); sb.click(); sb.click(); (mq('[data-a="save"]') || sb).click();
    await until(async () => (await saved()).some(x => x.name === "Dr. Doble Demo"), 5000); await sleep(700);
    ok("A27i doble clic: un solo perfil guardado", (await saved()).filter(x => x.name === "Dr. Doble Demo").length === 1, (await saved()).map(x => x.id));

    // Escape / X con cambios: pregunta (enfocado «Seguir editando») y no se pierde lo escrito sin querer
    setVal(mq('[data-f="title"]'), "Cardiólogo");
    key("Escape");
    ok("A27j Escape con cambios pregunta antes de cerrar", await until(() => dlg() && dlg().dataset.kind === "choose" && /cambios sin guardar/i.test(dlg().textContent), 2000) && document.activeElement === dlgBtn("cancel") && PF.isOpen(), dlg() && dlg().textContent);
    await snap("A_inicio_salir_con_cambios");
    key("Escape");
    ok("A27k «Seguir editando»: el panel sigue con lo escrito", await until(() => !dlg(), 2000) && PF.isOpen() && mq('[data-f="title"]').value === "Cardiólogo" && PF.draft().title === "Cardiólogo");
    mq('[data-a="close"]').click();
    await until(() => dlg(), 2000);
    dlgBtn("discard").click();
    ok("A27l «Descartar cambios» cierra sin guardar", await until(() => !dlg() && !PF.isOpen(), 2000) && (await saved()).find(x => x.name === "Dr. Doble Demo").title === "");

    // borrados en OTRA ventana: al volver a esta, las listas se ponen al dia solas
    for (const x of await saved()) if (x.id === id + "-2" || x.name === "Dr. Doble Demo") await fetch("/profiles/" + x.id, { method: "DELETE" });
    window.dispatchEvent(new Event("focus"));
    ok("A27m al volver a la ventana las listas se ponen al día", await until(() => !opts("#homeProfSel").some(o => o[0] === id + "-2") && !opts("#asmProfile select").some(o => /Doble/.test(o[1])) && lab("#homeProfSel", id) === "Dra. Ana Demo", 4000), opts("#homeProfSel"));

    // eliminar con la confirmacion propia
    setVal($("#homeProfSel"), "ana-demo");
    $("#homeProfile [data-e]").click();
    await until(() => PF.isOpen() && mq('[data-a="del"]'), 3000);
    const del = mq('[data-a="del"]');
    ok("A28 eliminar visible", !!del && mq('[data-a="saved"]').value === "ana-demo");
    del.focus(); del.click();
    ok("A29 confirmación propia (no la del sistema)", await until(() => dlg() && dlg().dataset.kind === "confirm", 2000) && natives().length === 0, natives());
    ok("A30 el foco empieza en Cancelar (borrar no es lo de por defecto)", document.activeElement === dlgBtn("cancel"));
    key("Tab"); ok("A31 Tab: a «Eliminar perfil»", document.activeElement === dlgBtn("ok"));
    key("Tab"); ok("A32 Tab da la vuelta dentro del diálogo", document.activeElement === dlgBtn("cancel"));
    key("Tab", { shiftKey: true }); ok("A33 Mayús+Tab hacia atrás", document.activeElement === dlgBtn("ok"));
    await snap("A_inicio_confirmar_eliminar");
    key("Escape");
    ok("A34 Escape cancela y el foco vuelve al botón", await until(() => !dlg(), 2000) && (await saved()).length === 2 && document.activeElement === del);
    del.click(); await until(() => dlg(), 2000);
    dlgBtn("ok").click();
    const s5 = await until(async () => { const s = await saved(); return s.length === 1 && s; }, 5000);
    ok("A35 eliminado", !!s5 && s5[0].id === id, s5 && s5.map(x => x.id));
    ok("A35b tras eliminar el foco sigue en el panel (Escape y ⌘Z no llegan al editor)", await until(() => M().contains(document.activeElement), 1000), document.activeElement && document.activeElement.tagName);
    ok("A36 el panel lo dice y queda listo para otro", /eliminado/.test(mq('[data-a="msg"]').textContent) && !mq('[data-a="del"]') && /Guardar perfil/.test(mq('[data-a="save"]').textContent), mq('[data-a="msg"]').textContent);
    ok("A37 sale de todas las listas sin recargar", !opts("#homeProfSel").some(o => o[0] === "ana-demo") && !opts("#asmProfile select").some(o => o[0] === "ana-demo") && !opts('#pfm [data-a="saved"]').some(o => o[0] === "ana-demo"));
    // eliminar el elegido para el proximo video: Inicio queda sin perfil
    PF.close();
    setVal($("#homeProfSel"), id);
    ok("A38a elegido para el próximo video", PF.selected() && PF.selected().name === "Dra. Ana Demo" && $("#homeProfSel").value === id);
    $("#homeProfile [data-e]").click();
    await until(() => PF.isOpen() && mq('[data-a="del"]'), 3000);
    mq('[data-a="del"]').click(); await until(() => dlg(), 2000); dlgBtn("ok").click();
    ok("A38 eliminar el elegido: el próximo video queda sin perfil", await until(async () => (await saved()).length === 0 && $("#homeProfSel").value === "" && !PF.selected(), 5000), [$("#homeProfSel").value, opts("#homeProfSel")]);
    ok("A39 el panel avisa", /El próximo video queda sin perfil/.test(mq('[data-a="msg"]').textContent), mq('[data-a="msg"]').textContent);
    await snap("A_inicio_eliminado");
    key("Escape");
    ok("A40 Escape cierra el panel (sin cambios que perder)", await until(() => !PF.isOpen() && !dlg(), 2000));
  } else {
    // ================================================================ Studio
    for (const p of await saved()) await fetch("/profiles/" + p.id, { method: "DELETE" });
    const parsed = await api("/profile/parse", "POST", { text: DESC, use_llm: false });
    const prof = parsed.profile; delete prof.media_status; prof.use.handle_watermark = true; prof.whatsapp = "+52 55 0000 0001";
    const sv = await api("/profiles", "POST", { profile: prof });
    const id = sv.id;
    ok("S0 Studio listo", await until(() => window.FX && FX.isPro && FX.isPro() && $("#s-editor.on") && window.Estilo && window.Dlg && window.PF, 60000));
    await sleep(800);
    await PF.load(true); PF.syncAll();
    Estilo.open("stWho");
    ok("S1 «Quién sale» con la lista de perfiles guardados", await until(() => $("#profUse") && opts("#profUse").some(o => o[0] === id), 8000), $("#profUse") && opts("#profUse"));
    const G = () => PLAN.fx.graphics || [];
    const lt = () => G().find(g => g.kind === "lower_third"), fol = () => G().find(g => g.kind === "cta" && g.variant === "follow");
    const wm = () => G().filter(g => g.kind === "socials" && g.variant === "watermark");
    const chips = () => G().filter(g => g.kind === "socials" && g.variant === "chips").flatMap(g => (g.links || []).map(l => l.text));
    setVal($("#profUse"), id);
    ok("S2 usar un perfil guardado en el video", await until(() => PLAN.profile && PLAN.profile.name === "Dra. Ana Demo" && PLAN.profile.title === "Odontóloga" && PLAN.profile.use.handle_watermark && lt() && lt().subtitle === "Odontóloga", 10000), PLAN.profile);
    ok("S3 el video queda ligado al guardado", PLAN.profile.id === id, PLAN.profile.id);
    ok("S4 rótulo, Seguir y marca de agua con sus datos", lt().title === "Dra. Ana Demo" && lt().subtitle === "Odontóloga" && fol() && fol().handle === "@dra.ana.demo" && wm().length > 0, [lt().title, lt().subtitle, fol() && fol().handle, wm().length]);
    const oldPhoto = await until(() => { const l = (PLAN.profile.links || []).find(x => x.platform === "instagram"); return l && l.photo && l.photo.sid; }, 15000);
    ok("S5 la foto de su Instagram llega sola al video", !!oldPhoto && await until(() => fol() && fol().media && fol().media.sid === oldPhoto, 15000), fol() && fol().media);
    ok("S5a el cierre lleva su WhatsApp", chips().includes("+52 55 0000 0001"), chips());

    // un interruptor de «Dónde aparece» es de ESTE video: no es un cambio del perfil guardado (antes el
    // panel decía «Cambios sin guardar» y su botón lo escribía en el guardado para todos los videos)
    await PF.setUse("handle_watermark", false);
    ok("S5b el interruptor se aplica al video", await until(() => wm().length === 0 && PLAN.profile.use.handle_watermark === false, 10000));
    $("#profEdit").click(); await until(() => PF.isOpen(), 3000);
    ok("S5c el panel no lo marca como «Cambios sin guardar»", mq('[data-a="state"]').textContent === "" && mq('[data-a="apply"]').textContent.trim() === "Aplicar al video" && !mq(".pfver") && !mq('[data-u="handle_watermark"]').checked,
      [mq('[data-a="state"]').textContent, mq('[data-a="apply"]').textContent]);
    key("Escape");
    ok("S5d sin cambios: Escape cierra sin preguntar", await until(() => !PF.isOpen(), 2000) && !dlg());
    ok("S5e el perfil guardado conserva su interruptor", (await saved())[0].use.handle_watermark === true);
    await PF.setUse("handle_watermark", true);
    await until(() => wm().length > 0, 10000);

    // editar desde el Studio y «Guardar y aplicar al video»
    $("#profEdit").click();
    await until(() => PF.isOpen(), 3000);
    ok("S6 Editar perfil: ligado al guardado", /Guardar cambios/.test(mq('[data-a="save"]').textContent) && !!mq('[data-a="del"]') && mq('[data-a="saved"]').value === id);
    setVal(mq('[data-f="name"]'), "Dra. Ana Demo Ruiz");
    setVal(mq('[data-f="title"]'), "Dentista");
    setVal(igRow().querySelector('[data-k="handle"]'), "@dra.ana.nueva");
    ok("S7 el enlace se rehace al instante", await until(() => igLink() && /dra\.ana\.nueva$/.test(igLink().url) && !(igLink().photo && igLink().photo.sid === oldPhoto), 5000), igLink());
    // el WhatsApp cambia: su enlace (wa.me) se rehace al instante con el numero nuevo
    setVal(mq('[data-f="whatsapp"]'), "55 0000 0002");
    const waLinks = () => PF.draft().links.filter(l => l.platform === "whatsapp");
    ok("S7c el enlace de WhatsApp se rehace con el número nuevo", await until(() => waLinks().length === 1 && /wa\.me\/525500000002$/.test(waLinks()[0].url) && mq('[data-f="whatsapp"]').value === "+52 55 0000 0002", 5000), [waLinks(), mq('[data-f="whatsapp"]').value]);
    // la pagina de reservas cambia (otra url): se quita la vieja y se pega la nueva
    const pcRow = () => [...M().querySelectorAll(".pflink")].find(r => r.querySelector('select[data-k="platform"]').value === "paseoclub");
    const pcOldSid = await until(() => { const l = (PLAN.profile.links || []).find(x => x.platform === "paseoclub"); return l && l.photo && l.photo.sid; }, 15000);
    ok("S7a la foto de la página de reservas ya estaba en el video", !!pcOldSid, (PLAN.profile.links || []).map(l => l.photo));
    pcRow().querySelector('[data-a="rm"]').click();
    setVal(mq('[data-a="lnew"]'), "https://paseo.club/business/dra-ana-ruiz", false);
    mq('[data-a="ladd"]').click();
    ok("S7b la página de reservas nueva entra en lugar de la vieja", await until(() => { const L = PF.draft().links.filter(l => l.platform === "paseoclub"); return L.length === 1 && /dra-ana-ruiz$/.test(L[0].url); }, 5000), PF.draft().links.map(l => l.url));
    ok("S8 «Guardar y aplicar al video»", /Guardar y aplicar al video/.test(mq('[data-a="apply"]').textContent), mq('[data-a="apply"]').textContent);
    await snap("S_studio_panel_cambios");
    mq('[data-a="apply"]').click();
    ok("S9 rótulo re-generado con los datos nuevos", await until(() => !PF.isOpen() && lt() && lt().title === "Dra. Ana Demo Ruiz" && lt().subtitle === "Dentista", 10000), lt() && [lt().title, lt().subtitle]);
    ok("S10 Seguir con el @usuario nuevo", fol() && fol().handle === "@dra.ana.nueva", fol() && fol().handle);
    ok("S11 marca de agua con el @usuario nuevo (nada del viejo)", wm().length > 0 && wm().every(g => JSON.stringify(g.links).includes("dra.ana.nueva") && !JSON.stringify(g.links).includes("dra.ana.demo")), wm().map(g => g.links));
    const lb = () => G().find(g => g.kind === "cta" && g.variant === "link_below");
    ok("S11b la invitación a reservar con la página nueva (nada de la vieja)", lb() && /dra-ana-ruiz/.test(lb().display) && !JSON.stringify(G()).includes("dra-ana-demo"), lb() && lb().display);
    ok("S11c el cierre con el WhatsApp nuevo (nada del viejo)", chips().includes("+52 55 0000 0002") && !JSON.stringify(PLAN.fx).includes("0000 0001"), chips());
    const s1 = await saved();
    ok("S12 se guardó el mismo perfil (sin copia)", s1.length === 1 && s1[0].id === id && s1[0].name === "Dra. Ana Demo Ruiz" && s1[0].links.some(l => l.handle === "@dra.ana.nueva"), s1.map(x => [x.id, x.name]));
    ok("S12b guardado con el WhatsApp nuevo (campo y enlace)", s1[0].whatsapp === "+52 55 0000 0002" && s1[0].links.some(l => l.url === "https://wa.me/525500000002") && !JSON.stringify(s1[0]).includes("0000 0001") && s1[0].use.handle_watermark === true, s1[0]);
    ok("S13 el video sigue ligado al guardado", PLAN.profile.id === id);
    ok("S14 todas las listas al día sin recargar", [opts("#homeProfSel"), opts("#asmProfile select"), opts("#profUse")].every(L => L.some(o => o[0] === id && /Ruiz/.test(o[1]))));
    const newPhoto = await until(() => { const l = (PLAN.profile.links || []).find(x => x.platform === "instagram"); return l && l.photo && /dra\.ana\.nueva/.test(l.photo.src || "") && l.photo.sid; }, 20000);
    ok("S15 la foto del video es la de la cuenta nueva", !!newPhoto && newPhoto !== oldPhoto && await until(() => fol() && fol().media && fol().media.sid === newPhoto, 15000), [oldPhoto, newPhoto, fol() && fol().media]);
    const pcNew = await until(() => { const l = (PLAN.profile.links || []).find(x => x.platform === "paseoclub"); return l && l.photo && /dra-ana-ruiz/.test(l.photo.src || "") && l.photo.sid; }, 20000);
    ok("S15b la foto de la página de reservas nueva llega al video", !!pcNew && pcNew !== pcOldSid && await until(() => lb() && lb().media && lb().media.sid === pcNew, 15000), lb() && lb().media);
    ok("S16 nada en el video usa las fotos viejas", !JSON.stringify(PLAN.fx).includes(oldPhoto) && !JSON.stringify(PLAN.fx).includes(pcOldSid));
    await sleep(300); await snap("S_studio_aplicado");

    // «Volver a buscar la foto»: se vuelve a bajar de su pagina aunque ya hubiera una
    const hits0 = (await api("/__fake/hits"))["/ig/dra.ana.nueva"] || 0;
    $("#profEdit").click(); await until(() => PF.isOpen(), 3000);
    await until(() => igRow() && igRow().querySelector(".pfmed.photo img"), 10000);
    igRow().querySelector('.pfmed.photo [data-m="auto:photo"]').click();
    ok("S17 «Obteniendo foto…» mientras la vuelve a buscar", !!(igRow().querySelector(".pfmed.photo .spinner")) || await until(() => igRow().querySelector(".pfmed.photo .spinner"), 1000));
    ok("S18 la vuelve a bajar de su página", await until(async () => ((await api("/__fake/hits"))["/ig/dra.ana.nueva"] || 0) > hits0, 8000));
    ok("S19 la foto vuelve al panel", await until(() => igRow() && igRow().querySelector(".pfmed.photo img") && igLink().photo && igLink().photo.sid === newPhoto, 15000), igLink() && igLink().photo);
    PF.close();

    // el perfil guardado cambia en OTRA ventana: al volver a esta, el Studio lo dice; el panel no lo marca
    // como «Cambios sin guardar» y nunca pisa la versión guardada sin preguntar
    const cur = (await saved())[0];
    await api("/profiles", "POST", { id, profile: Object.assign({}, cur, { title: "Ortodoncista", phone: "55 5123 4567" }) });
    window.dispatchEvent(new Event("focus"));
    ok("S20 al volver a la ventana avisa que el video usa otra versión", await until(() => $("#profSync"), 4000));
    await snap("S_studio_otra_version");
    $("#profEdit").click(); await until(() => PF.isOpen(), 3000);
    ok("S20b el panel lo dice arriba, sin «Cambios sin guardar»", !!mq(".pfver") && /otra versión/.test(mq(".pfver").textContent) && mq('[data-a="state"]').textContent === "" && mq('[data-a="apply"]').textContent.trim() === "Aplicar al video",
      [mq(".pfver") && mq(".pfver").textContent, mq('[data-a="state"]').textContent, mq('[data-a="apply"]').textContent]);
    const vr = mq(".pfver").getBoundingClientRect(), vbs = [...mq(".pfver").querySelectorAll(".btn")], vb = vbs.map(b => b.getBoundingClientRect());
    ok("S20b2 la nota es simétrica: dos opciones del mismo ancho, mismos márgenes, sin texto cortado", vb.length === 2 && Math.abs(vb[0].width - vb[1].width) <= 1 && Math.abs(vb[0].top - vb[1].top) <= 1
      && Math.abs((vb[0].left - vr.left) - (vr.right - vb[1].right)) <= 1 && vbs.every(b => b.scrollWidth <= b.clientWidth + 1), vb.map(r => [Math.round(r.left - vr.left), Math.round(vr.right - r.right), Math.round(r.width)]));
    await snap("S_studio_panel_otra_version");
    setVal(mq('[data-f="business"]'), "Clínica Demo Centro");
    ok("S20c al editar, sí: «Cambios sin guardar»", mq('[data-a="state"]').textContent === "Cambios sin guardar" && /Guardar y aplicar al video/.test(mq('[data-a="apply"]').textContent));
    mq('[data-a="apply"]').click();
    ok("S20d antes de pisar la versión guardada pregunta", await until(() => dlg() && dlg().dataset.kind === "choose" && /tiene otros datos/.test(dlg().textContent), 3000) && document.activeElement === dlgBtn("saved"), dlg() && dlg().textContent);
    await snap("S_studio_pisar_guardado");
    dlgBtn("saved").click();
    ok("S20e «Usar la versión guardada»: el panel la toma y nada se pisó", await until(async () => PF.isOpen() && PF.draft().title === "Ortodoncista" && !mq(".pfver") && (await saved())[0].title === "Ortodoncista", 4000)
      && (await saved())[0].phone === "55 5123 4567" && mq('[data-a="state"]').textContent === "Sin aplicar al video", [PF.draft().title, mq('[data-a="state"]').textContent]);
    mq('[data-a="apply"]').click();
    ok("S21 aplicarla re-genera el rótulo con la versión guardada", await until(() => !PF.isOpen() && lt() && lt().subtitle === "Ortodoncista" && !$("#profSync"), 10000), lt() && lt().subtitle);
    // con el panel abierto, otra ventana guarda: el servidor lo detecta (409) y pregunta
    $("#profEdit").click(); await until(() => PF.isOpen() && !mq(".pfver"), 3000);
    const cur2 = (await saved())[0];
    await api("/profiles", "POST", { id, profile: Object.assign({}, cur2, { business: "Otra ventana" }) });
    setVal(mq('[data-f="title"]'), "Periodoncista");
    mq('[data-a="save"]').click();
    ok("S21b guardar encima de una versión más nueva: pregunta", await until(() => dlg() && /tiene otros datos/.test(dlg().textContent) && /otra ventana/.test(dlg().textContent), 3000), dlg() && dlg().textContent);
    dlgBtn("replace").click();
    ok("S21c «Reemplazar con la de aquí» guarda lo del panel", await until(async () => { const x = (await saved())[0]; return x.title === "Periodoncista" && x.business !== "Otra ventana"; }, 4000)
      && /Guardar cambios/.test(mq('[data-a="save"]').textContent) && mq('[data-a="state"]').textContent === "Sin aplicar al video");
    mq('[data-a="apply"]').click();
    ok("S21d y se aplica al video", await until(() => !PF.isOpen() && lt() && lt().subtitle === "Periodoncista" && !$("#profSync"), 10000));

    // eliminar desde el Studio el perfil de este video que TAMBIEN es el elegido para el proximo
    setVal($("#homeProfSel"), id);
    $("#profEdit").click(); await until(() => PF.isOpen(), 3000);
    mq('[data-a="del"]').click();
    ok("S22 confirmación propia", await until(() => dlg() && dlg().dataset.kind === "confirm" && /Dra\. Ana Demo Ruiz/.test(dlg().textContent), 2000), natives());
    dlgBtn("ok").click();
    ok("S23 eliminado", await until(async () => (await saved()).length === 0, 5000));
    ok("S24 sale de todas las listas sin recargar", ![opts("#homeProfSel"), opts("#asmProfile select"), opts("#profUse"), opts('#pfm [data-a="saved"]')].some(L => L.some(o => o[0] === id)));
    ok("S25 el video conserva sus datos (y lo dice primero; el próximo video también)", PLAN.profile && PLAN.profile.name === "Dra. Ana Demo Ruiz" && /Este video conserva sus datos\. Tu próximo video ya no lo usa\./.test(mq('[data-a="msg"]').textContent)
      && !/El próximo video queda sin perfil/.test(mq('[data-a="msg"]').textContent) && /Guardar perfil/.test(mq('[data-a="save"]').textContent) && $("#homeProfSel").value === "", mq('[data-a="msg"]').textContent);
    // el foco sigue en el panel: ⌘Z no deshace detras (ni con el foco en el fondo) y Escape lo cierra
    ok("S25b tras eliminar el foco sigue en el panel", await until(() => M().contains(document.activeElement), 1000), document.activeElement && document.activeElement.tagName);
    const prof0 = JSON.stringify(PLAN.profile);
    key("z", { metaKey: true }); key("z", { ctrlKey: true });
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "z", metaKey: true, bubbles: true, cancelable: true }));
    await sleep(400);
    ok("S25c ⌘Z con el panel abierto no deshace nada en el editor", JSON.stringify(PLAN.profile) === prof0 && PF.isOpen());
    key("Escape");
    ok("S25d Escape cierra el panel", await until(() => !PF.isOpen() && !dlg(), 2000));
    $("#profEdit").click(); await until(() => PF.isOpen(), 3000);
    ok("S26 reabrir: ya no se ofrece eliminar un perfil que no existe", !mq('[data-a="del"]') && /Guardar perfil/.test(mq('[data-a="save"]').textContent));
    PF.close();
    // dos perfiles guardados con el mismo nombre: «Usar un perfil guardado…» los distingue
    const e1 = await api("/profiles", "POST", { profile: { name: "Dra. Eva Demo", title: "Odontóloga" } });
    const e2 = await api("/profiles", "POST", { profile: { name: "Dra. Eva Demo", title: "Abogada" } });
    window.dispatchEvent(new Event("focus"));
    const lab = (sel, v) => (opts(sel).find(o => o[0] === v) || [])[1] || "";
    ok("S26b dos perfiles con el mismo nombre se distinguen en las listas", await until(() => lab("#profUse", e1.id) === "Dra. Eva Demo · Odontóloga" && lab("#profUse", e2.id) === "Dra. Eva Demo · Abogada" && lab("#homeProfSel", e2.id) === "Dra. Eva Demo · Abogada", 4000), opts("#profUse"));
    for (const x of [e1, e2]) await fetch("/profiles/" + x.id, { method: "DELETE" });
    window.dispatchEvent(new Event("focus"));
    await until(() => !opts("#profUse").some(o => o[0] === e1.id), 4000);

    // plantillas de tu marca: pedir el nombre y confirmar con el dialogo propio
    const pr0 = Object.keys(await api("/presets"));
    $("#presetSave").click();
    ok("S27 pide el nombre de la plantilla", await until(() => dlg() && dlg().dataset.kind === "prompt" && document.activeElement === dlg().querySelector("input"), 2000) && dlg().getAttribute("role") === "dialog");
    key("Enter");
    ok("S28 sin nombre no guarda y lo dice", !!dlg() && /Escribe un nombre/.test(dlg().querySelector(".rdl-err").textContent) && dlg().querySelector("input").getAttribute("aria-invalid") === "true");
    setVal(dlg().querySelector("input"), "Plantilla 1/2", false);
    await snap("S_studio_plantilla");
    key("Enter");
    ok("S29 Enter guarda la plantilla", await until(async () => !dlg() && Object.keys(await api("/presets")).includes("Plantilla 1/2"), 4000) && $("#presetSel").value === "Plantilla 1/2");
    $("#presetDel").click();
    ok("S30 confirma antes de eliminar la plantilla", await until(() => dlg() && dlg().dataset.kind === "confirm" && /Plantilla 1\/2/.test(dlg().textContent), 2000));
    dlgBtn("ok").click();
    ok("S31 plantilla eliminada (también con «/» en el nombre) y fuera de la lista", await until(async () => !Object.keys(await api("/presets")).includes("Plantilla 1/2") && !opts("#presetSel").some(o => o[1] === "Plantilla 1/2"), 4000) && !dlg(), pr0);

    // salir con cambios que no se pudieron guardar: se pregunta (Quedarme)
    const sv0 = FX.saveNow, dy0 = FX.isDirty, href0 = location.href;
    FX.saveNow = async () => false; FX.isDirty = () => true;
    try {
      $("#cancel").click();
      ok("S32 salir sin guardar pregunta", await until(() => dlg() && dlg().dataset.kind === "confirm" && /no se guardaron/.test(dlg().textContent), 3000));
      dlgBtn("cancel").click();
      ok("S33 «Quedarme» se queda en el editor", await until(() => !dlg() && !$("#cancel").disabled, 2000) && location.href === href0);
    } finally { FX.saveNow = sv0; FX.isDirty = dy0; }

    // Montar clips sin clips: aviso propio
    const clips0 = ASM.clips; ASM.clips = [];
    try {
      asmApply();
      ok("S34 Montar clips sin clips avisa", await until(() => dlg() && dlg().dataset.kind === "alert" && /Falta un clip/.test(dlg().textContent), 2000));
      key("Escape");
      ok("S35 el aviso se cierra", await until(() => !dlg(), 2000));
    } finally { ASM.clips = clips0; }

    // clave de Pexels: se pide con el dialogo propio; cancelar apaga los videos de apoyo de stock
    const st = await api("/settings");
    if (!st.pexels && $("#broll")) {
      $("#broll").checked = true; $("#broll").dispatchEvent(new Event("change", { bubbles: true }));
      ok("S36 pide la clave de Pexels", await until(() => dlg() && dlg().dataset.kind === "prompt" && /Pexels/.test(dlg().textContent), 3000));
      key("Escape");
      ok("S37 sin clave queda apagado", await until(() => !dlg() && !$("#broll").checked, 2000));
    }
  }
  ok("ningún diálogo nativo", natives().length === 0, natives());
  rep.ok = true;
} catch (e) { rep.ok = false; rep.error = String(e && e.message || e); rep.natives = natives(); }
return JSON.stringify(rep);
