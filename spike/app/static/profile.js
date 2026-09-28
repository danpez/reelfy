/* Reelfy Studio: perfil de la persona (quien sale en el video y sus enlaces; docs/MOTION-ENGINE.md 3.2).
   - Editor en un panel (el mismo en la pantalla inicial, el ensamblado y el editor): nombre, profesion,
     negocio, enlaces como chips (pegar URL o @usuario -> red detectada con su icono; usuario y texto
     editables; red principal; corregir la red si es ambigua), telefono/WhatsApp, direccion, foto
     (imagen propia por POST /sources) e interruptores de uso (rotulo, Seguir, reservas, tarjeta final,
     marca de agua).
   - "Pegar descripcion": texto libre -> POST /profile/parse (IA local) -> "Entendi esto" para confirmar.
   - Perfiles guardados reutilizables: GET/POST/DELETE /profiles.
   - Antes de analizar viaja en /compose (y /analyze); en el editor PUT /profile/{job} re-dirige AL
     INSTANTE (rotulo, Seguir, reservas y cierre) y queda un paso de deshacer.
   Depende de fx.js (FX.toast, FX.platforms, FX.adoptServerPlan) y de los globales de index.html
   (PLAN, JOB, mut) que solo se leen dentro de funciones. */
(function () {
  "use strict";
  const PF = (window.PF = {});
  const $ = s => document.querySelector(s);
  const ICO = n => `<svg class="ic" viewBox="0 0 24 24"><use href="#i-${n}"/></svg>`;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clone = o => (o == null ? o : JSON.parse(JSON.stringify(o)));
  const toast = (m, i, u) => (window.FX && FX.toast ? FX.toast(m, i, u) : null);
  const USE = [["lower_third", "Rótulo con nombre", "Nombre y profesión cuando habla por primera vez"],
    ["follow_cta", "Botón Seguir", "Con la red principal y el @usuario"],
    ["booking_cta", "Invitación a reservar", "Cuando invita al link o a agendar"],
    ["end_card", "Tarjeta final", "Redes, reservas y ubicación al cierre"],
    ["handle_watermark", "Marca de agua @usuario", "Pequeña, en una esquina libre"]];
  const KIND_ES = { social: "Redes", messaging: "Mensajes", booking: "Reservas", web: "Web", maps: "Ubicación", email: "Correo", phone: "Teléfono" };
  const FIELDS = [["name", "Nombre", "Dra. Ana Ruiz"], ["title", "Profesión o título", "Odontóloga"], ["business", "Negocio", "Clínica Dental Sonríe"],
    ["phone", "Teléfono", "+52 55 1234 5678"], ["whatsapp", "WhatsApp", "+52 55 1234 5678"], ["address", "Dirección", "Av. Demo 123, Guadalajara"]];

  let CAT = [], SAVED = [], LOADED = false;
  let SEL = null, SEL_ID = null;          // perfil elegido para el PROXIMO video (pantalla inicial / ensamblado)
  const LS = "reelfy.profile.sel";

  // ------------------------------------------------------------------ datos
  async function jreq(url, method, body) {
    const r = await fetch(url, { method: method || "GET", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    let d = null; try { d = await r.json(); } catch (e) { d = null; }
    if (!r.ok) throw Object.assign(new Error((d && d.detail) || `Error ${r.status}`), { status: r.status, data: d });
    return d || {};
  }
  let LOADING = null;
  /** Catalogo de redes y perfiles guardados (una sola peticion aunque lo pidan varios selectores). */
  PF.load = function () {
    if (!LOADING) LOADING = load().finally(() => { setTimeout(() => { LOADING = null; }, 1500); });
    return LOADING;
  };
  async function load() {
    const tasks = [];
    if (!CAT.length) tasks.push(jreq("/profile/platforms").then(d => { CAT = d.platforms || []; if (window.FX && FX.setPlatforms) FX.setPlatforms(CAT); }).catch(() => {}));
    tasks.push(jreq("/profiles").then(d => { SAVED = d.profiles || []; }).catch(() => {}));
    await Promise.all(tasks);
    if (!LOADED) {
      LOADED = true;
      // perfil elegido la ultima vez en este equipo (comodidad local: el dato vive en /profiles)
      let id = null; try { id = localStorage.getItem(LS); } catch (e) { id = null; }
      const p = id && SAVED.find(x => x.id === id);
      if (p) { SEL = clone(p); SEL_ID = p.id; }
    }
    return SAVED;
  }
  const WEB_ICON = "world";                // icono Tabler (no del sprite del Studio)
  const plat = id => CAT.find(p => p.id === id) || CAT.find(p => p.id === "web") || { id: "web", label: "Sitio web", icon: WEB_ICON, color: "#1E2330", kind: "web" };
  const isEmpty = p => !p || !(String(p.name || "").trim() || String(p.title || "").trim() || String(p.business || "").trim() || (p.links || []).length || p.phone || p.whatsapp);
  /** "Dra. Ana Ruiz · Odontóloga · 2 enlaces" */
  PF.summary = function (p) {
    if (isEmpty(p)) return "";
    const n = (p.links || []).length;
    return [p.name, p.title, p.business, n ? `${n} enlace${n > 1 ? "s" : ""}` : ""].filter(x => String(x || "").trim()).join(" · ");
  };
  /** Perfil que viaja en /compose y /analyze (el elegido para el proximo video) o null. */
  PF.forCompose = () => (isEmpty(SEL) ? null : clean(SEL));
  PF.selected = () => SEL;
  /** Sin campos internos del Studio (_picked...) y sin enlaces vacios. */
  function clean(p) {
    const o = clone(p) || {};
    delete o.updated;
    o.links = (o.links || []).filter(l => l && (l.url || l.handle)).map(l => { const x = Object.assign({}, l); Object.keys(x).forEach(k => { if (k[0] === "_") delete x[k]; }); return x; });
    return o;
  }
  function iconSvg(id, color) {
    const p = plat(id);
    const svg = String(p.svg || "").replace(/currentColor/g, color || "#fff");
    return `<span class="pfic" style="background:${esc(p.color || "#1E2330")}">${svg || ICO("link")}</span>`;
  }
  function setSel(p, id) {
    SEL = isEmpty(p) ? null : clone(p); SEL_ID = id || null;
    try { if (SEL_ID) localStorage.setItem(LS, SEL_ID); else localStorage.removeItem(LS); } catch (e) { /* sin storage */ }
    document.querySelectorAll("[data-pfpick]").forEach(el => PF.renderPicker(el));
  }

  // ------------------------------------------------------------------ selector compacto (inicio / ensamblado)
  /** Selector del perfil para el proximo video: perfiles guardados, "Sin perfil" y "Nuevo". */
  PF.mountPicker = function (el, compact) {
    if (!el) return;
    el.dataset.pfpick = compact ? "compact" : "full";
    PF.renderPicker(el);
    PF.load().then(() => PF.renderPicker(el));
  };
  PF.renderPicker = function (el) {
    const compact = el.dataset.pfpick === "compact";
    const opts = `<option value="">Sin perfil</option>` + SAVED.map(p => `<option value="${esc(p.id)}" ${p.id === SEL_ID ? "selected" : ""}>${esc(p.name || p.business || p.id)}</option>`).join("")
      + (SEL && !SEL_ID ? `<option value="__cur" selected>${esc(SEL.name || "Perfil sin guardar")} (sin guardar)</option>` : "")
      + `<option value="__new">Nuevo perfil…</option>`;
    const sum = PF.summary(SEL);
    el.innerHTML = compact
      ? `<span class="pfpick c">${ICO("user")}<select class="selinp" aria-label="Perfil de la persona">${opts}</select>${SEL ? `<button class="btn ghost mini" type="button" data-e="1" title="Editar el perfil">${ICO("insp")}</button>` : ""}</span>`
      : `<label>Perfil de la persona <span class="info" data-tip="Quién sale en el video y sus enlaces: la IA pone su nombre y profesión, el botón Seguir, la invitación a reservar y la tarjeta final.">i</span></label>`
        + `<div class="pfpick">${ICO("user")}<select class="selinp">${opts}</select><button class="btn ghost mini" type="button" data-e="1">${ICO(SEL ? "insp" : "plus")}${SEL ? "Editar" : "Crear perfil"}</button></div>`
        + (sum ? `<div class="hint pfsum">${esc(sum)}</div>` : `<div class="hint pfsum">Opcional. También puedes agregarlo después en el editor.</div>`);
    const sel = el.querySelector("select");
    sel.onchange = () => {
      const v = sel.value;
      if (v === "__new") { sel.value = SEL_ID || (SEL ? "__cur" : ""); PF.open({ mode: "next", profile: null }); return; }
      if (v === "__cur") return;
      const p = SAVED.find(x => x.id === v);
      setSel(p || null, p ? p.id : null);
    };
    const e = el.querySelector("[data-e]"); if (e) e.onclick = () => PF.open({ mode: "next", profile: SEL, savedId: SEL_ID });
  };

  // ------------------------------------------------------------------ grupo del editor (pestana Proyecto)
  PF.renderEditorGroup = function () {
    const box = $("#profBody"); if (!box || typeof PLAN === "undefined" || !PLAN) return;
    const p = PLAN.profile || null;
    const sum = PF.summary(p);
    // avisos del director sobre el perfil ("sin espacio para...", "tarjeta final como escena")
    const notes = ((PLAN.director && PLAN.director.notes) || []).map(String).filter(n => /^perfil: (sin espacio|tarjeta final)/i.test(n));
    box.innerHTML = (sum ? `<div class="pfcard">${p.avatar && p.avatar.sid ? `<img class="pfav" src="/tlsource/${encodeURIComponent(p.avatar.sid)}" alt="">` : `<span class="pfav ini">${esc(initials(p.name || p.business))}</span>`}<div class="pfcs"><b>${esc(p.name || p.business || "Perfil")}</b><span>${esc([p.title, p.business && p.name ? p.business : ""].filter(Boolean).join(" · "))}</span><span class="pflinks">${(p.links || []).slice(0, 6).map(l => iconSvg(l.platform, "#fff")).join("")}</span></div></div>`
      : `<div class="hint" style="margin-top:0">Sin perfil: la IA usa lo que entiende del video. Con un perfil pone el nombre y la profesión, el botón Seguir con la red, la invitación a reservar y la tarjeta final.</div>`)
      + (notes.length ? `<div class="dirnotes">${notes.map(n => `<div>${ICO("alert")}<span>${esc(String(n).replace(/^perfil:\s*/i, ""))}</span></div>`).join("")}</div>` : "")
      + `<div class="btnrow" style="margin-top:8px"><button class="btn mini" id="profEdit">${ICO(sum ? "insp" : "plus")}${sum ? "Editar perfil" : "Agregar perfil"}</button>`
      + `<select class="selinp" id="profUse" style="flex:1;min-width:0"><option value="">Usar un perfil guardado…</option>${SAVED.map(x => `<option value="${esc(x.id)}">${esc(x.name || x.business || x.id)}</option>`).join("")}</select></div>`;
    $("#profEdit").onclick = () => PF.open({ mode: "editor", profile: PLAN.profile || null, savedId: (PLAN.profile && PLAN.profile.id) || null });
    const u = $("#profUse");
    u.onchange = () => { const x = SAVED.find(s => s.id === u.value); u.value = ""; if (x) PF.applyToJob(clean(x)); };
    if (!LOADED || !CAT.length) PF.load().then(() => { if ($("#profBody") === box) PF.renderEditorGroup(); });
  };
  function initials(n) {
    const w = String(n || "").replace(/^(dra?|lic|ing|mtr[oa]|chef)\.?\s+/i, "").split(/\s+/).filter(Boolean);
    return ((w[0] || "?")[0] + (w[1] ? w[1][0] : "")).toUpperCase();
  }
  /** Aplica (o quita con null) el perfil al trabajo abierto: PUT /profile re-dirige al instante; un
      paso de deshacer (el autoguardado de la version anterior re-aplica el perfil anterior). */
  PF.applyToJob = async function (profile) {
    if (typeof JOB === "undefined" || !JOB || JOB === "demo") return false;
    if (window.FX && FX.busy && FX.busy()) { toast("Espera a que la IA termine de dirigir.", "alert"); return false; }
    try {
      if (window.FX && FX.saveNow) await FX.saveNow();
      const body = { profile: profile ? clean(profile) : null, plan: PLAN };
      if (FX.serverRev != null) body.base_rev = FX.serverRev;
      const t0 = performance.now();
      const d = await jreq(`/profile/${JOB}`, "PUT", body);
      if (!d.plan) throw new Error("el servidor no devolvió el plan");
      mut();                                                  // un paso de deshacer
      FX.adoptServerPlan(d.plan, d.plan_rev);
      PF.lastMs = Math.round(performance.now() - t0);
      toast(profile ? "Perfil aplicado: rótulo, Seguir, reservas y cierre al día. ⌘Z lo deshace." : "Perfil quitado de este video. ⌘Z lo deshace.", "user", true);
      PF.renderEditorGroup();
      return true;
    } catch (e) {
      toast(e.status === 409 ? "El proyecto cambió o la IA está trabajando: intenta de nuevo en un momento." : `No se pudo aplicar el perfil: ${e.message || e}`, "alert");
      return false;
    }
  };

  // ------------------------------------------------------------------ panel de edicion
  let M = null;       // {el, mode, draft, savedId, parsed}
  PF.isOpen = () => !!(M && M.el.classList.contains("on"));
  PF.draft = () => (M ? M.draft : null);
  PF.open = async function (o) {
    o = o || {};
    await PF.load();
    if (!M) {
      const el = document.createElement("div"); el.className = "pfm"; el.id = "pfm";
      el.addEventListener("keydown", e => { if (e.key === "Escape") PF.close(); e.stopPropagation(); });
      document.body.appendChild(el);
      M = { el };
    }
    const base = o.profile ? clone(o.profile) : {};
    M.mode = o.mode || "next"; M.savedId = o.savedId || base.id || null; M.parsed = null;
    M.draft = Object.assign({ name: "", title: "", business: "", phone: "", whatsapp: "", address: "", links: [], use: {} }, base);
    M.draft.use = Object.assign({ lower_third: true, follow_cta: true, booking_cta: true, end_card: true, handle_watermark: false }, base.use || {});
    render();
    M.el.classList.add("on");
    const f = M.el.querySelector('[data-f="name"]'); if (f && !M.draft.name) f.focus();
  };
  PF.close = function () { if (M) M.el.classList.remove("on"); };

  function render() {
    const d = M.draft, ed = M.mode === "editor";
    const savedOpts = `<option value="">${M.savedId ? "Perfil nuevo" : "—"}</option>` + SAVED.map(p => `<option value="${esc(p.id)}" ${p.id === M.savedId ? "selected" : ""}>${esc(p.name || p.business || p.id)}</option>`).join("");
    M.el.innerHTML = `<div class="box" role="dialog" aria-label="Perfil de la persona">
      <h3>${ICO("user")}Perfil de la persona<button class="xbtn x" data-a="close" title="Cerrar">${ICO("x")}</button></h3>
      <div class="pfrow top"><label>Perfiles guardados</label><select class="selinp" data-a="saved">${savedOpts}</select>
        ${M.savedId ? `<button class="btn ghost mini" data-a="del" title="Eliminar este perfil guardado">${ICO("trash")}</button>` : ""}</div>
      <details class="pfparse" ${isEmpty(d) ? "open" : ""}><summary>${ICO("spark")}Pegar descripción <span class="hint">la IA local la entiende (bio, firma, datos de contacto)</span></summary>
        <textarea class="ftext" data-a="ptext" rows="3" placeholder="Soy la Dra. Ana Ruiz, odontóloga. Instagram @dra.ana.demo · Agenda: paseo.club/business/dra-ana-demo · WhatsApp 55 1234 5678"></textarea>
        <div class="btnrow"><button class="btn mini" data-a="parse">${ICO("spark")}Entender</button></div>
        <div data-a="parsed"></div></details>
      <div class="pfgrid">${FIELDS.map(([k, l, ph]) => `<div><label>${esc(l)}</label><input type="text" class="ftext" data-f="${k}" value="${esc(d[k] || "")}" placeholder="${esc(ph)}"></div>`).join("")}</div>
      <div class="pfsec"><h5>${ICO("link")}Enlaces <span class="hint">la estrella marca la red del botón Seguir</span></h5><div data-a="links"></div>
        <div class="pfadd"><input type="text" class="ftext" data-a="lnew" placeholder="Pega un enlace o @usuario (Instagram, TikTok, WhatsApp, reservas, web…)"><button class="btn ghost mini" data-a="ladd">${ICO("plus")}Agregar</button></div></div>
      <div class="pfsec"><h5>${ICO("image")}Foto de perfil</h5><div data-a="avatar"></div></div>
      <div class="pfsec"><h5>${ICO("insp")}Dónde se usa</h5><div class="pfuse">${USE.map(([k, l, h]) => `<label class="swlabel" title="${esc(h)}"><input type="checkbox" class="toggle" data-u="${k}" ${d.use[k] ? "checked" : ""}>${esc(l)}</label>`).join("")}</div></div>
      <div class="pfmsg" data-a="msg"></div>
      <div class="btnrow pfbtns">
        <button class="btn ghost mini" data-a="save">${ICO("save")}${M.savedId ? "Guardar cambios" : "Guardar perfil"}</button>
        ${ed && typeof PLAN !== "undefined" && PLAN && PLAN.profile ? `<button class="btn ghost mini" data-a="remove">${ICO("trash")}Quitar del video</button>` : ""}
        ${!ed && SEL ? `<button class="btn ghost mini" data-a="none">Sin perfil</button>` : ""}
        <span style="flex:1"></span>
        <button class="btn" data-a="apply">${ICO("check")}${ed ? "Aplicar al video" : "Usar en el próximo video"}</button>
      </div></div>`;
    renderLinks(); renderAvatar();
    const q = s => M.el.querySelector(s);
    q('[data-a="close"]').onclick = PF.close;
    M.el.querySelectorAll("[data-f]").forEach(i => { i.oninput = () => { d[i.dataset.f] = i.value; }; });
    M.el.querySelectorAll("[data-u]").forEach(i => { i.onchange = () => { d.use[i.dataset.u] = i.checked; }; });
    q('[data-a="saved"]').onchange = e => {
      const p = SAVED.find(x => x.id === e.target.value);
      M.savedId = p ? p.id : null;
      M.draft = Object.assign({ links: [], use: {} }, clone(p) || { name: "", title: "", business: "", links: [] });
      M.draft.use = Object.assign({ lower_third: true, follow_cta: true, booking_cta: true, end_card: true, handle_watermark: false }, M.draft.use || {});
      render();
    };
    const del = q('[data-a="del"]'); if (del) del.onclick = delSaved;
    q('[data-a="parse"]').onclick = parse;
    q('[data-a="ladd"]').onclick = addLink;
    q('[data-a="lnew"]').onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); addLink(); } };
    q('[data-a="save"]').onclick = save;
    q('[data-a="apply"]').onclick = apply;
    const rm = q('[data-a="remove"]'); if (rm) rm.onclick = async () => { if (await PF.applyToJob(null)) PF.close(); };
    const nn = q('[data-a="none"]'); if (nn) nn.onclick = () => { setSel(null, null); PF.close(); };
  }
  function msg(t, bad) { const m = M.el.querySelector('[data-a="msg"]'); if (m) { m.textContent = t || ""; m.classList.toggle("bad", !!bad); } }

  function renderLinks() {
    const box = M.el.querySelector('[data-a="links"]'); if (!box) return;
    const L = M.draft.links || [];
    if (!L.length) { box.innerHTML = `<div class="hint">Sin enlaces todavía.</div>`; return; }
    const kinds = {}; CAT.forEach(p => { (kinds[p.kind] = kinds[p.kind] || []).push(p); });
    const selOpts = cur => Object.keys(kinds).map(k => `<optgroup label="${esc(KIND_ES[k] || k)}">${kinds[k].map(p => `<option value="${esc(p.id)}" ${p.id === cur ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</optgroup>`).join("");
    box.innerHTML = L.map((l, i) => {
      const p = plat(l.platform), social = (l.kind || p.kind) === "social";
      const amb = l.ambiguous && !l._picked;
      return `<div class="pflink ${amb ? "amb" : ""}" data-i="${i}">${iconSvg(l.platform, "#fff")}
        <select class="selinp pfplat" data-k="platform" title="Red o tipo de enlace">${selOpts(l.platform)}</select>
        <input type="text" class="ftext" data-k="${social ? "handle" : "label"}" value="${esc(social ? l.handle || "" : l.label || "")}" placeholder="${social ? "@usuario" : "Texto del botón"}" title="${social ? "Usuario" : "Texto del botón (p. ej. Agenda en PaseoClub)"}">
        <input type="text" class="ftext" data-k="display" value="${esc(l.display || "")}" placeholder="Cómo se muestra" title="Cómo se muestra en el video">
        ${social ? `<button class="xbtn pfstar ${l.primary ? "on" : ""}" data-a="primary" title="${l.primary ? "Red principal (botón Seguir)" : "Usar como red principal (botón Seguir)"}">${ICO("star")}</button>` : `<span class="pfstar-sp"></span>`}
        <button class="xbtn" data-a="rm" title="Quitar">${ICO("trash")}</button>
        ${amb ? `<div class="pfnote">${ICO("alert")}No dijiste la red de ${esc(l.handle || "este usuario")}: se tomó como ${esc(p.label)}. Elige otra si no es.</div>` : ""}
        <div class="pfurl">${esc(l.url || "")}</div></div>`;
    }).join("");
    box.querySelectorAll(".pflink").forEach(row => {
      const i = +row.dataset.i, l = L[i];
      row.querySelectorAll("[data-k]").forEach(inp => {
        const k = inp.dataset.k;
        if (k === "platform") inp.onchange = () => {
          // cambiar la red: el servidor rehace url, icono y etiqueta si era ambiguo o armado con el usuario
          const np = plat(inp.value);
          l.platform = np.id; l.kind = np.kind; l.icon = np.icon; l._picked = true;
          if (l.label && CAT.some(x => x.label === l.label || (x.cta && x.cta === l.label))) delete l.label;
          if (np.kind !== "social") l.primary = false;
          renderLinks();
        };
        else inp.oninput = () => { l[k] = inp.value; };
      });
      const st = row.querySelector('[data-a="primary"]'); if (st) st.onclick = () => { L.forEach((x, j) => { x.primary = j === i ? !x.primary : false; }); renderLinks(); };
      row.querySelector('[data-a="rm"]').onclick = () => { L.splice(i, 1); renderLinks(); };
    });
  }
  /** Pegar un enlace o @usuario: el servidor lo reconoce (misma regla que el motor, sin IA). */
  async function addLink() {
    const inp = M.el.querySelector('[data-a="lnew"]'); const txt = inp.value.trim(); if (!txt) return;
    try {
      const d = await jreq("/profile/parse", "POST", { text: txt, use_llm: false });
      const got = (d.profile && d.profile.links) || [];
      if (!got.length) {
        // un numero suelto: telefono / WhatsApp
        if (/^\+?[\d\s().-]{8,}$/.test(txt)) { if (!M.draft.whatsapp) M.draft.whatsapp = txt; else M.draft.phone = txt; render(); msg("Número agregado como WhatsApp o teléfono."); return; }
        msg("No reconocí un enlace ni un @usuario en ese texto.", true); return;
      }
      const L = M.draft.links = M.draft.links || [];
      let n = 0;
      got.forEach(g => {
        if (L.some(x => (x.url && x.url === g.url) || (x.handle && g.handle && x.handle === g.handle && x.platform === g.platform))) return;
        if (g.primary && L.some(x => x.primary)) g.primary = false;
        L.push(g); n++;
      });
      ["phone", "whatsapp", "email"].forEach(k => { if (d.profile[k] && !M.draft[k]) M.draft[k] = d.profile[k]; });
      inp.value = "";
      renderLinks();
      msg(n ? (d.notes || []).filter(x => /sin red|@/.test(x)).join(" ") || "Enlace agregado." : "Ese enlace ya estaba.");
    } catch (e) { msg(`No se pudo leer el enlace: ${e.message || e}`, true); }
  }
  function renderAvatar() {
    const box = M.el.querySelector('[data-a="avatar"]'); if (!box) return;
    const a = M.draft.avatar;
    box.innerHTML = `<div class="mediapick"><div class="mth">${a && a.sid ? `<img src="${esc(a.thumb || `/tlsource/${encodeURIComponent(a.sid)}`)}" alt="">` : ICO("user")}</div>`
      + `<div class="mnm"><b>${esc(a && a.sid ? a.name || "Foto de perfil" : "Sin foto")}</b><span>${a && a.sid ? "Va en el botón Seguir y la tarjeta final" : "Sin foto se usan las iniciales"}</span></div>`
      + `<button class="btn ghost mini" type="button" data-a="aup">${ICO("upload")}${a && a.sid ? "Cambiar" : "Subir foto"}</button>`
      + (a && a.sid ? `<button class="xbtn" type="button" data-a="arm" title="Quitar la foto">${ICO("trash")}</button>` : "") + `</div>`;
    box.querySelector('[data-a="aup"]').onclick = () => {
      const fi = Object.assign(document.createElement("input"), { type: "file", accept: "image/*" });
      fi.onchange = async () => {
        const f = fi.files && fi.files[0]; if (!f) return;
        msg("Subiendo la foto…");
        try {
          const fd = new FormData(); fd.append("files", f);
          const r = await fetch("/sources", { method: "POST", body: fd });
          const d = await r.json(); const s = d && d.sources && d.sources[0];
          if (!r.ok || !s || s.type !== "image") throw new Error("usa una imagen (JPG, PNG, WEBP o HEIC)");
          M.draft.avatar = { sid: s.id, type: "image", name: s.name, w: s.w, h: s.h, thumb: s.thumb || undefined };
          renderAvatar(); msg("Foto lista.");
        } catch (e) { msg(`No se pudo subir la foto: ${e.message || e}`, true); }
      };
      fi.click();
    };
    const rm = box.querySelector('[data-a="arm"]'); if (rm) rm.onclick = () => { delete M.draft.avatar; renderAvatar(); };
  }
  /** Nombre comparable: sin titulo (Dr., Dra., Lic....), acentos ni signos. */
  function nameKey(n) {
    return String(n || "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
      .replace(/^\s*(dra?|doctora?|lic|licenciad[oa]|ing|ingenier[oa]|mtr[oa]|maestr[oa]|chef|sr|sra|srta|prof|profesora?)\.?\s+/, "")
      .replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter(Boolean);
  }
  /** El texto entendido es de OTRA persona que el borrador: ambos tienen nombre y ninguno contiene
      al otro ("Laura Méndez" y "Dra. Laura Méndez" son la misma; "Pedro Ficticio" no). */
  PF.otherPerson = function (draft, parsed) {
    const a = nameKey(draft && draft.name), b = nameKey(parsed && parsed.name);
    if (!a.length || !b.length) return false;
    const inA = b.every(w => a.includes(w)), inB = a.every(w => b.includes(w));
    return !(inA || inB);
  };
  const isSocial = l => l && ((l.kind || plat(l.platform).kind) === "social");
  /** Borrador nuevo con SOLO lo entendido (conserva los interruptores de uso). */
  function replaced(p, use) {
    const d = { name: "", title: "", business: "", phone: "", whatsapp: "", address: "", links: [], use: Object.assign({}, use || {}) };
    FIELDS.forEach(([k]) => { if (p[k]) d[k] = p[k]; });
    if (p.email) d.email = p.email;
    d.links = clone(p.links || []);
    starFromParsed(d.links, p.links);
    return d;
  }
  /** Completa el borrador con lo entendido (misma persona): los campos entendidos ganan; un enlace de
      la misma red reemplaza al anterior y los demas se suman; la estrella pasa a la red del texto. */
  function merge(dr, p) {
    FIELDS.forEach(([k]) => { if (p[k]) dr[k] = p[k]; });
    if (p.email) dr.email = p.email;
    const L = dr.links = dr.links || [];
    (p.links || []).forEach(g => {
      if (L.some(x => x.url === g.url)) return;
      const k = L.findIndex(x => x.platform === g.platform);
      if (k >= 0) { L[k] = Object.assign({}, g); return; }
      L.push(Object.assign({}, g));
    });
    starFromParsed(L, p.links);
  }
  /** Red principal = la del texto entendido (su estrella o su primera red social); si el texto no
      trae redes, la que ya estaba. Siempre una sola estrella. */
  function starFromParsed(L, parsedLinks) {
    const P = (parsedLinks || []).filter(isSocial);
    const pick = P.find(l => l.primary) || P[0];
    if (pick) {
      const i = L.findIndex(x => (x.url && x.url === pick.url) || (x.platform === pick.platform && (x.handle || "") === (pick.handle || "")));
      if (i >= 0) L.forEach((x, j) => { x.primary = j === i; });
    }
    let seen = false; L.forEach(x => { if (x.primary) { if (seen || !isSocial(x)) x.primary = false; else seen = true; } });
  }
  /** Mensaje con un boton de accion (p. ej. "completar en su lugar"). */
  function msgAct(t, label, fn) {
    const m = M.el.querySelector('[data-a="msg"]'); if (!m) return;
    m.classList.remove("bad");
    m.innerHTML = `<span>${esc(t)}</span> <button class="btn ghost mini" data-a="undoReplace">${esc(label)}</button>`;
    m.querySelector('[data-a="undoReplace"]').onclick = fn;
  }
  /** Texto libre -> perfil (IA local) -> "Entendi esto" para confirmar antes de usarlo. */
  async function parse() {
    const ta = M.el.querySelector('[data-a="ptext"]'), out = M.el.querySelector('[data-a="parsed"]');
    const txt = ta.value.trim(); if (!txt) { ta.focus(); return; }
    const b = M.el.querySelector('[data-a="parse"]'); b.disabled = true; b.innerHTML = `<span class="spinner"></span>Entendiendo…`;
    out.innerHTML = "";
    try {
      const t0 = performance.now();
      const d = await jreq("/profile/parse", "POST", { text: txt });
      M.parsed = d.profile; PF.lastParseMs = Math.round(performance.now() - t0);
      const p = d.profile || {};
      const rows = FIELDS.filter(([k]) => p[k]).map(([k, l]) => `<div><span class="k">${esc(l)}</span><b>${esc(p[k])}</b></div>`).join("")
        + (p.email ? `<div><span class="k">Correo</span><b>${esc(p.email)}</b></div>` : "");
      out.innerHTML = `<div class="pfres"><h6>Entendí esto${d.llm ? " (con la IA local)" : ""}</h6>${rows || ""}`
        + `<div class="pflinks big">${(p.links || []).map(l => `<span class="pfchip">${iconSvg(l.platform, "#fff")}${esc(l.display || l.handle || l.label || l.url)}</span>`).join("")}</div>`
        + ((d.notes || []).length ? `<div class="pfnotes">${d.notes.map(n => `<div>${ICO("alert")}<span>${esc(n)}</span></div>`).join("")}</div>` : "")
        + `<div class="btnrow"><button class="btn mini" data-a="use">${ICO("check")}Usar estos datos</button><button class="btn ghost mini" data-a="drop">Descartar</button></div></div>`;
      out.querySelector('[data-a="use"]').onclick = () => {
        const prev = clone(M.draft), prevId = M.savedId;
        if (PF.otherPerson(M.draft, p)) {
          // OTRA persona: nada del perfil anterior se queda (enlaces, telefono, WhatsApp, direccion,
          // foto, id del perfil guardado): mezclar dos identidades sacaria el video con datos ajenos
          M.draft = replaced(p, M.draft.use); M.savedId = null;
          M.parsed = null; render();
          msgAct(`Es otra persona: se reemplazó todo el perfil de ${prev.name || "antes"} (enlaces, teléfono, WhatsApp, dirección y foto).`,
            "Era la misma: completar en su lugar", () => { M.draft = prev; M.savedId = prevId; merge(M.draft, p); render(); msg("Datos agregados al perfil: revísalos y aplica."); });
          return;
        }
        merge(M.draft, p);
        M.parsed = null; render(); msg("Datos agregados: revísalos y aplica.");
      };
      out.querySelector('[data-a="drop"]').onclick = () => { out.innerHTML = ""; M.parsed = null; };
    } catch (e) { out.innerHTML = `<div class="pfmsg bad">No se pudo entender el texto: ${esc(e.message || e)}</div>`; }
    finally { b.disabled = false; b.innerHTML = `${ICO("spark")}Entender`; }
  }
  async function save() {
    const d = clean(M.draft);
    if (isEmpty(d)) { msg("Agrega al menos el nombre, la profesión o un enlace.", true); return; }
    try {
      const r = await jreq("/profiles", "POST", Object.assign({ profile: d }, M.savedId ? { id: M.savedId } : {}));
      // el servidor lo normaliza (red cambiada, url armada, etiquetas): el panel muestra ese
      SAVED = r.profiles || SAVED; M.savedId = r.id;
      M.draft = Object.assign(clone(r.profile), { use: Object.assign({}, M.draft.use, (r.profile && r.profile.use) || {}) });
      if (M.mode !== "editor") setSel(r.profile, r.id);
      render(); msg(r.replaced ? "Perfil guardado (actualizado)." : `Perfil guardado como "${r.profile.name || r.id}".`);
      document.querySelectorAll("[data-pfpick]").forEach(el => PF.renderPicker(el));
      if (M.mode === "editor") PF.renderEditorGroup();
    } catch (e) { msg(`No se pudo guardar: ${e.message || e}`, true); }
  }
  async function delSaved() {
    if (!M.savedId) return;
    const p = SAVED.find(x => x.id === M.savedId);
    if (!confirm(`¿Eliminar el perfil guardado "${(p && (p.name || p.business)) || M.savedId}"? Los videos que ya lo usan no cambian.`)) return;
    try {
      const r = await jreq(`/profiles/${encodeURIComponent(M.savedId)}`, "DELETE");
      SAVED = r.profiles || [];
      if (SEL_ID === M.savedId) setSel(SEL, null);
      M.savedId = null; delete M.draft.id; render(); msg("Perfil guardado eliminado.");
      document.querySelectorAll("[data-pfpick]").forEach(el => PF.renderPicker(el));
    } catch (e) { msg(`No se pudo eliminar: ${e.message || e}`, true); }
  }
  async function apply() {
    const d = clean(M.draft);
    if (M.mode === "editor") {
      const b = M.el.querySelector('[data-a="apply"]'); b.disabled = true; b.innerHTML = `<span class="spinner"></span>Aplicando…`;
      const ok = await PF.applyToJob(isEmpty(d) ? null : d);
      if (ok) PF.close(); else { b.disabled = false; b.innerHTML = `${ICO("check")}Aplicar al video`; }
      return;
    }
    setSel(isEmpty(d) ? null : d, M.savedId && SAVED.some(x => x.id === M.savedId) ? M.savedId : null);
    PF.close();
    toast(isEmpty(d) ? "Sin perfil para el próximo video." : `Perfil listo: ${PF.summary(d)}.`, "user");
  }
})();
