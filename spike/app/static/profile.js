/* Reelfy Studio: perfil de la persona (quien sale en el video y sus enlaces; docs/MOTION-ENGINE.md 3.2).
   - Editor en un panel (el mismo en la pantalla inicial, el ensamblado y el editor): nombre, profesion,
     negocio, enlaces como chips (pegar URL o @usuario -> red detectada con su icono; usuario y texto
     editables; red principal; corregir la red si es ambigua), telefono/WhatsApp, direccion, foto
     (imagen propia por POST /sources) e interruptores de uso (rotulo, Seguir, reservas, tarjeta final,
     marca de agua).
   - "Pegar descripcion": texto libre -> POST /profile/parse (IA local) -> "Entendi esto" para confirmar.
   - Foto y logo de cada enlace: el servidor los toma de la pagina (Instagram: su foto de perfil;
     reservas/web: la imagen de vista previa y el icono del sitio) con POST /profile/media/refresh;
     mientras profile.media_status es 'fetching' la fila dice "Obteniendo foto…". "Cambiar" sube una
     propia (POST /sources, media_src 'user'); "Quitar" la apaga (media_off: no se vuelve a bajar).
   - Perfiles guardados reutilizables: GET/POST/DELETE /profiles. Editar uno guardado y «Guardar cambios»
     lo ACTUALIZA (nunca una copia «-2»; doble clic: un solo guardado). Guardar uno sin ligar con el
     nombre de otro ya guardado pregunta (enfocado «Guardar otro aparte»; «Completar ese perfil» le
     agrega lo escrito y CONSERVA sus enlaces, telefonos y foto). «Cambios sin guardar» = lo editado
     desde que se abrio el panel (no la diferencia con el guardado); con ellos el boton principal dice
     «Guardar y usar…» / «Guardar y aplicar…». Si el panel tiene OTRA version que la guardada (la del
     video, o cambio en otra ventana) lo dice arriba y nunca la pisa sin preguntar (POST /profiles con
     base_updated: 409 -> «Usar la versión guardada» / «Reemplazar»). En el Studio los interruptores de
     «Dónde aparece» son del video: no cuentan como cambios del perfil guardado. Cerrar (Escape, X) o
     elegir otro perfil con cambios pregunta (enfocado «Seguir editando»). Eliminar pide confirmacion con
     el dialogo propio (dialog.js: en la app de escritorio window.confirm no existe). Guardar o eliminar
     (aqui, en otra ventana o al volver a la ventana) pone al dia TODAS las listas (Inicio, Montar clips
     y Estilo) sin recargar; dos perfiles con el mismo nombre se distinguen («Dra. Ana Demo · Abogada»).
   - Telefono y WhatsApp: el campo manda sobre su enlace (wa.me / tel:): cambiarlo lo rehace al
     instante (POST /profile/normalize) y vaciarlo lo quita; quitar el enlace vacia el campo.
   - Cambiar la red o el @usuario de un enlace: POST /profile/normalize rehace la url al instante y
     descarta la foto/logo de la cuenta anterior (llegan los de la nueva). «Volver a buscar en su
     pagina» (cada foto/logo) los vuelve a bajar aunque ya hubiera uno.
   - En el Studio, si el video usa otra version del perfil guardado: «Usar la versión guardada».
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
    ["handle_watermark", "Marca de agua @usuario", "Pequeña, centrada arriba"]];
  const KIND_ES = { social: "Redes", messaging: "Mensajes", booking: "Reservas", web: "Web", maps: "Ubicación", email: "Correo", phone: "Teléfono" };
  const FIELDS = [["name", "Nombre", "Dra. Ana Ruiz"], ["title", "Profesión o título", "Odontóloga"], ["business", "Negocio", "Clínica Dental Sonríe"],
    ["phone", "Teléfono", "+52 55 1234 5678"], ["whatsapp", "WhatsApp", "+52 55 1234 5678"], ["address", "Dirección", "Av. Demo 123, Guadalajara"]];

  // estilos de las miniaturas de foto/logo por enlace (propios de este modulo)
  (function css() {
    if (typeof document === "undefined" || document.getElementById("pfmedia-css")) return;
    const st = document.createElement("style"); st.id = "pfmedia-css";
    st.textContent = ".pflink .pfmedia{grid-column:1/-1;display:flex;gap:8px;flex-wrap:wrap}"
      + ".pfmed{display:flex;align-items:center;gap:7px;min-width:0;flex:1 1 210px;padding:4px 6px;border:1px dashed var(--line2);border-radius:8px}"
      + ".pfmed .pth{width:34px;height:34px;border-radius:50%;overflow:hidden;flex:none;background:var(--sunken);display:flex;align-items:center;justify-content:center;color:var(--faint)}"
      + ".pfmed.logo .pth{border-radius:9px}.pfmed.logo .pth.img{background:#fff}"
      + ".pfmed .pth img{width:100%;height:100%;object-fit:cover}.pfmed.logo .pth img{object-fit:contain;padding:3px;box-sizing:border-box}"
      + ".pfmed .pth .ic{width:15px;height:15px}"
      + ".pfmed .pmn{flex:1;min-width:0;display:flex;flex-direction:column;font-size:10.5px;color:var(--faint)}"
      + ".pfmed .pmn b{font-size:11.5px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
      + ".pfmed .btn.mini{padding:3px 7px;font-size:11px}";
    document.head.appendChild(st);
  })();

  const Core = () => window.FXCore || null;
  /** Huella de los datos (fxcore.profileDataKey; sin fxcore: el JSON tal cual). */
  const dataKey = (p, o) => (Core() && Core().profileDataKey ? Core().profileDataKey(p, o) : JSON.stringify(p || null));
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
  /** Catalogo de redes y perfiles guardados (una sola peticion aunque lo pidan varios selectores).
      `force`: vuelve a preguntar ya (la lista pudo cambiar en otra ventana). */
  PF.load = function (force) {
    if (force || !LOADING) {
      const mine = LOADING = load().finally(() => { setTimeout(() => { if (LOADING === mine) LOADING = null; }, 1500); });
    }
    return LOADING;
  };
  async function load() {
    const tasks = [], before = LOADED ? JSON.stringify(SAVED) : null;
    let got = false;
    if (!CAT.length) tasks.push(jreq("/profile/platforms").then(d => { CAT = d.platforms || []; if (window.FX && FX.setPlatforms) FX.setPlatforms(CAT); }).catch(() => {}));
    tasks.push(jreq("/profiles").then(d => { SAVED = d.profiles || []; got = true; }).catch(() => {}));
    await Promise.all(tasks);
    if (!LOADED) {
      LOADED = true;
      // perfil elegido la ultima vez en este equipo (comodidad local: el dato vive en /profiles)
      let id = null; try { id = localStorage.getItem(LS); } catch (e) { id = null; }
      const p = id && SAVED.find(x => x.id === id);
      if (p) { SEL = clone(p); SEL_ID = p.id; }
      return SAVED;
    }
    if (got && before !== JSON.stringify(SAVED)) {
      // la lista cambio (en otra ventana, otro video o la foto que bajo): al dia en todas partes
      if (SEL_ID) {
        const p = savedOf(SEL_ID);
        if (!p) setSel(null, null);                                      // se elimino: sin perfil
        else if (dataKey(p) !== dataKey(clean(SEL))) setSel(p, p.id);    // se edito: la version nueva
      }
      syncAll();
      if (PF.isOpen() && !M.opening) checkStale();
    }
    return SAVED;
  }
  // volver a la ventana (o a la app): la lista pudo cambiar en otra ventana
  if (typeof window !== "undefined" && window.addEventListener && typeof document !== "undefined") {
    const again = () => { if (LOADED && (!document.visibilityState || document.visibilityState === "visible")) PF.load(true); };
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
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
    delete o.updated; delete o.media_status;
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
    // el ejemplo de «Quién sale» en Inicio dibuja el nombre elegido: se vuelve a dibujar
    if (window.Estilo && Estilo.Ticker && Estilo.Ticker.kick) Estilo.Ticker.kick();
  }
  const savedOf = id => (id ? SAVED.find(x => x.id === id) || null : null);
  /** Nombre de un perfil en las listas; si otro guardado se llama igual, con lo que los distingue
      (profesion, negocio, @usuario, enlace o id): «Dra. Ana Demo · Abogada». */
  const nmOf = p => String((p && (p.name || p.business || p.id)) || "Perfil").trim();
  function hintsOf(p) {
    const L = (p && p.links) || [], soc = L.find(l => l && l.primary && l.handle) || L.find(l => l && l.handle);
    return [p.title, p.name ? p.business : "", soc ? soc.handle : "", L[0] ? L[0].display || L[0].url : "", p.id].map(x => String(x || "").trim());
  }
  PF.label = function (p, list) {
    if (!p) return "";
    const nm = nmOf(p), k = nm.toLowerCase();
    const tw = (list || SAVED).filter(x => x && x !== p && (!p.id || x.id !== p.id) && nmOf(x).toLowerCase() === k);
    if (!tw.length) return nm;
    const mine = hintsOf(p), th = tw.map(hintsOf);
    for (let i = 0; i < mine.length; i++) if (mine[i] && th.every(h => h[i].toLowerCase() !== mine[i].toLowerCase())) return `${nm} · ${mine[i]}`;
    return nm;
  };
  /** Nombre con su primer dato (siempre): para decir cual es en un dialogo. */
  const labelFull = p => { const h = hintsOf(p).find(x => x && x.toLowerCase() !== nmOf(p).toLowerCase()); return h ? `${nmOf(p)} · ${h}` : nmOf(p); };
  /** La lista de perfiles guardados cambio (guardar, actualizar, eliminar): al dia en TODAS partes sin
      recargar (Inicio, Montar clips y Estilo › Quién sale). */
  function syncAll() {
    document.querySelectorAll("[data-pfpick]").forEach(el => PF.renderPicker(el));
    if (document.getElementById("profBody")) PF.renderEditorGroup();
  }
  PF.syncAll = syncAll;
  const USE_DEF = () => ({ lower_third: true, follow_cta: true, booking_cta: true, end_card: true, handle_watermark: false });
  const blank = use => ({ name: "", title: "", business: "", phone: "", whatsapp: "", address: "", links: [], use: Object.assign(USE_DEF(), use || {}) });

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
    const opts = `<option value="">Sin perfil</option>` + SAVED.map(p => `<option value="${esc(p.id)}" ${p.id === SEL_ID ? "selected" : ""}>${esc(PF.label(p))}</option>`).join("")
      + (SEL && !SEL_ID ? `<option value="__cur" selected>${esc(SEL.name || "Perfil sin guardar")} (sin guardar)</option>` : "")
      + `<option value="__new">Nuevo perfil…</option>`;
    const sum = PF.summary(SEL);
    el.innerHTML = compact
      ? `<span class="pfpick c">${ICO("user")}<select class="selinp" aria-label="Perfil de la persona">${opts}</select>${SEL ? `<button class="btn ghost mini" type="button" data-e="1" title="Editar el perfil">${ICO("insp")}</button>` : ""}</span>`
      // (el nombre y que hace los pone la fila «Quién sale en el video» de Inicio)
      : `<div class="pfpick">${ICO("user")}<select class="selinp" id="homeProfSel" aria-label="Perfil guardado de quien sale en el video">${opts}</select><button class="btn ghost mini" type="button" data-e="1">${ICO(SEL ? "insp" : "plus")}${SEL ? "Editar" : "Crear perfil"}</button></div>`
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
    if (PF.jobMedia === "fetching") notes.unshift("perfil: Obteniendo la foto y el logo de sus páginas…");
    // proyecto abierto con enlaces sin foto/logo: el servidor los baja solo; se sigue una vez
    if (p && !PF.jobMedia && typeof JOB !== "undefined" && JOB && JOB !== "demo" && WATCHED !== JOB && (p.links || []).some(missingMedia)) { WATCHED = JOB; PF.watchJobMedia(0); }
    const face = PF.facePhoto(p);
    // el video usa OTRA version del perfil guardado (se edito despues en Inicio o en otro video): un
    // clic la pone (re-dirige rotulo, Seguir, reservas y cierre; los interruptores del video se quedan)
    const sv = p && p.id ? savedOf(p.id) : null;
    const other = !!sv && dataKey(sv, { use: false }) !== dataKey(p, { use: false });
    box.innerHTML = (sum ? `<div class="pfcard">${face ? `<img class="pfav" src="/tlsource/${encodeURIComponent(face.sid)}" alt="">` : `<span class="pfav ini">${esc(initials(p.name || p.business))}</span>`}<div class="pfcs"><b>${esc(p.name || p.business || "Perfil")}</b><span>${esc([p.title, p.business && p.name ? p.business : ""].filter(Boolean).join(" · "))}</span><span class="pflinks">${(p.links || []).slice(0, 6).map(l => iconSvg(l.platform, "#fff")).join("")}</span></div></div>`
      : `<div class="hint" style="margin-top:0">Sin perfil: la IA usa lo que entiende del video. Con un perfil pone el nombre y la profesión, el botón Seguir con la red, la invitación a reservar y la tarjeta final.</div>`)
      + (notes.length ? `<div class="dirnotes">${notes.map(n => `<div>${ICO("alert")}<span>${esc(String(n).replace(/^perfil:\s*/i, ""))}</span></div>`).join("")}</div>` : "")
      + (other ? `<div class="pfver" role="status"><span>${ICO("alert")}Este video usa otra versión de tu perfil guardado «${esc(PF.label(sv))}».</span>`
        + `<button class="btn ghost mini" type="button" id="profSync">${ICO("restore")}Usar la versión guardada</button></div>` : "")
      + `<div class="btnrow" style="margin-top:8px"><button class="btn ghost mini" id="profEdit">${ICO(sum ? "insp" : "plus")}${sum ? "Editar perfil" : "Agregar perfil"}</button>`
      + `<select class="selinp" id="profUse" style="flex:1;min-width:0"><option value="">Usar un perfil guardado…</option>${SAVED.map(x => `<option value="${esc(x.id)}">${esc(PF.label(x))}</option>`).join("")}</select></div>`;
    $("#profEdit").onclick = () => PF.open({ mode: "editor", profile: PLAN.profile || null, savedId: (PLAN.profile && PLAN.profile.id) || null });
    const sy = $("#profSync");
    if (sy) sy.onclick = () => PF.applyToJob(Object.assign(clean(sv), { use: Core() ? Core().profileUseOf(p) : p.use }));
    const u = $("#profUse");
    u.onchange = () => { const x = SAVED.find(s => s.id === u.value); u.value = ""; if (x) PF.applyToJob(clean(x)); };
    if (!LOADED || !CAT.length) PF.load().then(() => { if ($("#profBody") === box) PF.renderEditorGroup(); });
  };
  /** Prende o apaga un uso de los datos en el video abierto (Estilo › Quién sale › Dónde aparece:
      lower_third, follow_cta, booking_cta, end_card, handle_watermark): re-dirige al instante con el
      perfil del proyecto (PUT /profile), un paso de deshacer. -> true si se aplico. */
  PF.setUse = function (key, on) {
    const p = typeof PLAN !== "undefined" && PLAN ? PLAN.profile : null;
    if (!p) return Promise.resolve(false);
    const x = clean(p);
    x.use = Object.assign({}, window.FXCore ? FXCore.profileUseOf(p) : p.use, { [key]: !!on });
    return PF.applyToJob(x);
  };
  /** Foto de la persona: la que subio (avatar) o, si no, la de su red principal (Instagram) o la
      del primer enlace con foto (la misma precedencia que el director). */
  PF.facePhoto = function (p) {
    if (!p) return null;
    if (p.avatar && p.avatar.sid) return p.avatar;
    const ok = l => l && l.photo && l.photo.sid && !(l.media_off && l.media_off.photo);
    const L = p.links || [];
    const l = L.find(x => x.primary && ok(x)) || L.find(x => (x.kind || plat(x.platform).kind) === "social" && ok(x)) || L.find(ok);
    return l ? l.photo : null;
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
      // foto/logo de sus paginas aun bajando: llegan solos al plan (se sigue su estado)
      if (d.profile && d.profile.media_status === "fetching") PF.watchJobMedia(0);
      toast(profile ? "Perfil aplicado: rótulo, Seguir, reservas y cierre al día. ⌘Z lo deshace." : "Perfil quitado de este video. ⌘Z lo deshace.", "user", true);
      PF.renderEditorGroup();
      return true;
    } catch (e) {
      toast(e.status === 409 ? "El proyecto cambió o la IA está trabajando: intenta de nuevo en un momento." : `No se pudo aplicar el perfil: ${e.message || e}`, "alert");
      return false;
    }
  };

  // ------------------------------------------------------------------ panel de edicion
  // M: {el, mode, draft, savedId, open0, snap, baseUpdated, stale, staleWhy, useTouched, saving, applying, parsed}
  //  open0: lo que tenia el destino al abrir (el video / el proximo video); snap: el punto de partida de
  //  «Cambios sin guardar» (al abrir, elegir un guardado o guardar); baseUpdated: el `updated` de la
  //  version guardada de la que parte el panel (POST /profiles base_updated; null = otra version).
  let M = null;
  PF.isOpen = () => !!(M && M.el.classList.contains("on"));
  PF.draft = () => (M ? M.draft : null);
  /** En el Studio los interruptores de «Dónde aparece» son de cada video: no son datos del perfil. */
  const keyOpts = () => (M && M.mode === "editor" ? { use: false } : undefined);
  function syncSnap(updated) {
    M.snap = clean(M.draft);
    const sv = savedOf(M.savedId);
    M.baseUpdated = sv ? (updated != null ? updated : sv.updated) : null;
    M.stale = false; M.staleWhy = null;
  }
  /** El perfil guardado ligado tiene OTROS datos que el panel (y no porque el usuario editara): la del
      video es otra version, o se guardo en otra ventana despues de abrirlo. */
  function checkStale(atOpen) {
    if (!M || !M.draft || !M.savedId) return;
    const sv = savedOf(M.savedId);
    if (!sv) {
      // se elimino en otra ventana: el panel queda sin ligar (guardarlo lo crea de nuevo)
      M.savedId = null; delete M.draft.id; M.stale = false; render();
      msg("Este perfil se eliminó en otra ventana: si lo guardas, se crea de nuevo.", true);
      return;
    }
    if (!atOpen && M.baseUpdated != null && +sv.updated === +M.baseUpdated) return;
    if (dataKey(sv, keyOpts()) === dataKey(M.snap, keyOpts())) {
      M.baseUpdated = sv.updated;
      if (M.stale) { M.stale = false; M.staleWhy = null; if (!atOpen) render(); }
      return;
    }
    M.stale = true; M.staleWhy = atOpen && M.mode === "editor" ? "video" : "other"; M.baseUpdated = null;
    if (!atOpen) render();
  }
  PF.open = async function (o) {
    o = o || {};
    if (M) M.opening = true;
    await PF.load();
    if (!M) {
      const el = document.createElement("div"); el.className = "pfm"; el.id = "pfm";
      el.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); tryClose(); } e.stopPropagation(); });
      document.body.appendChild(el);
      M = { el };
      // con el panel abierto y el foco fuera de el (tras un dialogo, un clic en el fondo) los atajos
      // del editor no corren detras (⌘Z, Espacio, Suprimir) y Escape lo cierra
      document.addEventListener("keydown", e => {
        if (!PF.isOpen() || (window.Dlg && Dlg.isOpen()) || M.el.contains(e.target)) return;
        e.stopPropagation();
        if (e.key === "Escape") { e.preventDefault(); tryClose(); }
        else if (e.key === "Tab") { e.preventDefault(); focusIn(); }
      }, true);
    }
    const sid = o.savedId || (o.profile && o.profile.id) || null, live = savedOf(sid);
    M.mode = o.mode || "next"; M.parsed = null;
    // ligado a un perfil guardado solo si sigue en la lista (uno eliminado se guarda como nuevo); para
    // el proximo video siempre su version guardada mas nueva (el del video puede ser otra: lo dice)
    const src = M.mode !== "editor" && live ? live : o.profile;
    const base = src ? clone(src) : {};
    M.savedId = live ? live.id : null;
    if (!M.savedId) delete base.id;
    M.draft = Object.assign(blank(), base);
    M.draft.use = Object.assign(USE_DEF(), base.use || {});
    M.open0 = clean(M.draft);
    syncSnap();
    M.useTouched = false; M.saving = null; M.applying = false;
    checkStale(true);
    M.mediaSeq = (M.mediaSeq || 0) + 1;       // un pedido de fotos de otro borrador ya no aplica
    render();
    M.el.classList.add("on");
    M.opening = false;
    const f = M.el.querySelector('[data-f="name"]');
    if (f && !M.draft.name) f.focus(); else focusIn();
    PF.refreshMedia();
  };
  /** Cierra sin preguntar (lo usan «Usar…», «Aplicar…» y las pruebas). */
  PF.close = function () { if (M) M.el.classList.remove("on"); };
  /** El foco a un lugar estable del panel (no se queda en <body> tras redibujarlo). */
  function focusIn(sel) {
    if (!M || !M.el) return;
    const t = (sel && M.el.querySelector(sel)) || M.el.querySelector('[data-a="saved"]') || M.el.querySelector(".box");
    try { if (t) t.focus({ preventScroll: true }); } catch (e) { /* sin foco */ }
  }
  /** El panel tiene algo que aun no llega a su destino (el video o el proximo video). */
  function changed() { return !!(M && M.draft) && !isEmpty(M.draft) && dataKey(clean(M.draft)) !== dataKey(M.open0); }
  /** Cambios sin guardar en el perfil guardado ligado: lo editado desde que se abrio, se eligio o se
      guardo (no la diferencia con la version guardada: si el video usa otra, lo dice la nota). */
  function dirty() { return !!(M && M.draft && savedOf(M.savedId)) && dataKey(clean(M.draft), keyOpts()) !== dataKey(M.snap, keyOpts()); }
  PF.dirty = () => !!(M && M.draft && dirty());
  PF.changed = () => !!(M && M.draft && changed());
  /** Antes de cerrar o de elegir otro perfil con cambios: pregunta (enfocado «Seguir editando»).
      why 'close' | 'switch' -> 'go' (se hizo lo de guardar/aplicar), 'discard', o null (seguir editando). */
  async function guard(why) {
    if (!M || !changed() || !window.Dlg) return "discard";
    const dy = dirty(), ed = M.mode === "editor", fresh = !M.savedId;
    // elegir otro perfil: solo se pierde algo si se editaron DATOS (no los interruptores del video)
    if (why === "switch" && !dy && !(fresh && dataKey(clean(M.draft), { use: false }) !== dataKey(M.open0, { use: false }))) return "discard";
    let o;
    if (why === "switch") o = { title: dy ? "Tienes cambios sin guardar" : "Este perfil no está guardado", text: "Si eliges otro perfil ahora, se pierden.", go: ["save", M.savedId ? "Guardar cambios" : "Guardar perfil"], no: "Descartar cambios" };
    else if (dy) o = { title: "Tienes cambios sin guardar", text: "Si cierras ahora, se pierden.", go: ["apply", mainLabel()], no: "Descartar cambios" };
    else if (fresh && !ed) o = { title: "Tu perfil nuevo no está guardado", text: "Si cierras ahora, se pierde lo que escribiste.", go: ["save", "Guardar perfil"], no: "Descartar" };
    else if (ed) o = { title: "Los cambios no se aplicaron a este video", text: "Si cierras ahora, este video se queda con los datos de antes.", go: ["apply", "Aplicar al video"], no: "No aplicar" };
    else o = { title: "No elegiste este perfil para el próximo video", text: "Si cierras ahora, el próximo video se queda con el perfil de antes.", go: ["apply", "Usar en el próximo video"], no: "No usar" };
    const pick = await Dlg.choose({ title: o.title, text: o.text, icon: "alert", cancel: "Seguir editando", focus: "cancel",
      choices: [{ id: "go", label: o.go[1] }, { id: "discard", label: o.no, danger: true }] });
    if (pick !== "go") return pick === "discard" ? "discard" : null;
    if (o.go[0] === "save") return (await save()) ? "go" : null;
    await apply();
    return PF.isOpen() ? null : "go";
  }
  /** Escape, la X o un clic: cierra; con cambios pregunta primero. */
  async function tryClose() {
    // (con un dialogo abierto no: es modal; la pregunta de antes ya se esta contestando)
    if (!M || !PF.isOpen() || M.saving || M.applying || (window.Dlg && Dlg.isOpen())) return;
    const r = await guard("close");
    // «Descartar», o se guardo desde la pregunta (aplicar ya cerro el panel)
    if (r === "discard" || (r === "go" && PF.isOpen())) PF.close();
  }
  PF.tryClose = tryClose;
  /** Carga en el panel un perfil guardado (o uno nuevo con ""). */
  function loadSaved(id) {
    const p = savedOf(id);
    M.savedId = p ? p.id : null;
    M.draft = Object.assign(blank(), clone(p) || {});
    M.draft.use = Object.assign(USE_DEF(), M.draft.use || {});
    if (!p) delete M.draft.id;
    syncSnap(); M.useTouched = false; M.parsed = null;
    render(); PF.refreshMedia();
  }
  /** «Usar la versión guardada» (la nota de otra version o la pregunta al guardar): el panel toma la
      guardada; en el Studio los interruptores de este video se quedan. */
  function useSaved(sv) {
    if (!M || !sv) return;
    const use = M.draft.use;
    M.savedId = sv.id;
    M.draft = Object.assign(blank(), clone(sv));
    M.draft.use = M.mode === "editor" ? Object.assign(USE_DEF(), use) : Object.assign(USE_DEF(), sv.use || {});
    syncSnap(sv.updated); M.parsed = null;
    render(); PF.refreshMedia();
    msg(M.mode === "editor" ? "Versión guardada lista: aplícala al video." : "Versión guardada lista.");
  }

  function render() {
    const d = M.draft, ed = M.mode === "editor";
    // el foco no se pierde al redibujar (tras guardar o eliminar): al mismo control, o a uno estable
    const ae = document.activeElement, hadFocus = !!ae && M.el.contains(ae);
    const fsel = hadFocus ? (ae.dataset.f ? `[data-f="${ae.dataset.f}"]` : ae.dataset.a ? `[data-a="${ae.dataset.a}"]` : ae.dataset.u ? `[data-u="${ae.dataset.u}"]` : null) : null;
    // pie simetrico: las acciones secundarias del mismo ancho en un renglon y la principal a todo lo ancho
    const second = [`<button class="btn ghost mini" data-a="save">${ICO("save")}${M.savedId ? "Guardar cambios" : "Guardar perfil"}</button>`];
    if (ed && typeof PLAN !== "undefined" && PLAN && PLAN.profile) second.push(`<button class="btn ghost mini" data-a="remove">${ICO("trash")}Quitar del video</button>`);
    if (!ed && SEL) second.push(`<button class="btn ghost mini" data-a="none">Sin perfil</button>`);
    const savedOpts = `<option value="">${M.savedId ? "Perfil nuevo" : "—"}</option>` + SAVED.map(p => `<option value="${esc(p.id)}" ${p.id === M.savedId ? "selected" : ""}>${esc(PF.label(p))}</option>`).join("");
    const sv = savedOf(M.savedId);
    const note = M.stale && sv ? `<div class="pfver inpanel" role="status" data-a="ver"><span>${ICO("alert")}${M.staleWhy === "video" ? `Este video usa otra versión de tu perfil guardado «${esc(PF.label(sv))}».` : `Tu perfil guardado «${esc(PF.label(sv))}» cambió en otra ventana.`}</span>`
      + `<button class="btn ghost mini" type="button" data-a="vsaved">${ICO("restore")}Usar la versión guardada</button>`
      + `<button class="btn ghost mini" type="button" data-a="vkeep">${M.staleWhy === "video" ? "Mantener la de este video" : "Seguir con la de aquí"}</button></div>` : "";
    M.el.innerHTML = `<div class="box" role="dialog" aria-label="Perfil de la persona" tabindex="-1">
      <h3>${ICO("user")}Perfil de la persona<button class="xbtn x" data-a="close" title="Cerrar" aria-label="Cerrar">${ICO("x")}</button></h3>
      <div class="pfrow top"><label>Perfiles guardados</label><select class="selinp" data-a="saved" aria-label="Perfiles guardados">${savedOpts}</select>
        ${M.savedId ? `<button class="btn ghost mini" data-a="del" title="Eliminar este perfil guardado" aria-label="Eliminar este perfil guardado">${ICO("trash")}</button>` : ""}</div>
      ${note}
      <details class="pfparse" ${isEmpty(d) ? "open" : ""}><summary>${ICO("spark")}Pegar descripción <span class="hint">la IA local la entiende (bio, firma, datos de contacto)</span></summary>
        <textarea class="ftext" data-a="ptext" rows="3" placeholder="Soy la Dra. Ana Ruiz, odontóloga. Instagram @dra.ana.demo · Agenda: paseo.club/business/dra-ana-demo · WhatsApp 55 1234 5678"></textarea>
        <div class="btnrow"><button class="btn mini" data-a="parse">${ICO("spark")}Entender</button></div>
        <div data-a="parsed"></div></details>
      <div class="pfgrid">${FIELDS.map(([k, l, ph]) => `<div><label>${esc(l)}</label><input type="text" class="ftext" data-f="${k}" value="${esc(d[k] || "")}" placeholder="${esc(ph)}"></div>`).join("")}</div>
      <div class="pfsec"><h5>${ICO("link")}Enlaces <span class="hint">la estrella marca la red del botón Seguir</span></h5><div data-a="links"></div>
        <div class="pfadd"><input type="text" class="ftext" data-a="lnew" placeholder="Pega un enlace o @usuario (Instagram, TikTok, WhatsApp, reservas, web…)"><button class="btn ghost mini" data-a="ladd">${ICO("plus")}Agregar</button></div></div>
      <div class="pfsec"><h5>${ICO("image")}Foto de perfil</h5><div data-a="avatar"></div></div>
      <div class="pfsec"><h5>${ICO("insp")}Dónde se usa${ed ? ` <span class="hint">en este video</span>` : ""}</h5><div class="pfuse">${USE.map(([k, l, h]) => `<label class="swlabel" title="${esc(h)}"><input type="checkbox" class="toggle" data-u="${k}" ${d.use[k] ? "checked" : ""}>${esc(l)}</label>`).join("")}</div></div>
      <div class="pfmsg" data-a="msg"></div>
      <div class="pfbtns" style="--n:${second.length}">
        <span class="pfstate" data-a="state" role="status"></span>
        ${second.join("")}
        <button class="btn pfgo" data-a="apply">${ICO("check")}${ed ? "Aplicar al video" : "Usar en el próximo video"}</button>
      </div></div>`;
    renderLinks(); renderAvatar();
    const q = s => M.el.querySelector(s);
    q('[data-a="close"]').onclick = tryClose;
    M.el.querySelectorAll("[data-f]").forEach(i => {
      i.oninput = () => { d[i.dataset.f] = i.value; syncBtns(); };
      // telefono / WhatsApp: al terminar de escribirlo su enlace (wa.me, tel:) se rehace
      if (CONTACT[i.dataset.f]) i.onchange = () => refield(i.dataset.f);
    });
    M.el.querySelectorAll("[data-u]").forEach(i => { i.onchange = () => { d.use[i.dataset.u] = i.checked; M.useTouched = true; syncBtns(); }; });
    q('[data-a="saved"]').onchange = async e => {
      const sel = e.target, v = sel.value;
      const g = await guard("switch");
      if (!M) return;
      if (!g) { sel.value = M.savedId || ""; return; }
      loadSaved(v);
    };
    const vs = q('[data-a="vsaved"]'); if (vs) vs.onclick = () => useSaved(savedOf(M.savedId));
    const vk = q('[data-a="vkeep"]');
    if (vk) vk.onclick = () => { const s2 = savedOf(M.savedId); M.stale = false; M.staleWhy = null; M.baseUpdated = s2 ? s2.updated : null; render(); focusIn('[data-f="name"]'); };
    const del = q('[data-a="del"]'); if (del) del.onclick = delSaved;
    q('[data-a="parse"]').onclick = parse;
    q('[data-a="ladd"]').onclick = addLink;
    q('[data-a="lnew"]').onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); addLink(); } };
    q('[data-a="save"]').onclick = () => save();
    q('[data-a="apply"]').onclick = apply;
    const rm = q('[data-a="remove"]'); if (rm) rm.onclick = async () => { if (await PF.applyToJob(null)) PF.close(); };
    const nn = q('[data-a="none"]'); if (nn) nn.onclick = () => { setSel(null, null); PF.close(); };
    syncBtns();
    if (hadFocus || (PF.isOpen() && (!document.activeElement || document.activeElement === document.body))) focusIn(fsel);
  }
  /** Lo que hace el boton principal: con cambios sin guardar tambien los guarda. */
  function mainLabel() {
    const ed = M.mode === "editor", dy = dirty();
    return dy ? (ed ? "Guardar y aplicar al video" : "Guardar y usar en el próximo video") : (ed ? "Aplicar al video" : "Usar en el próximo video");
  }
  /** Botones y estado al dia: lo que hara el principal, «Guardando…» / «Aplicando…» (sin doble clic). */
  function syncBtns() {
    if (!M || !M.el) return;
    const ed = M.mode === "editor", busy = !!(M.saving || M.applying), q = s => M.el.querySelector(s);
    const ap = q('[data-a="apply"]'), sv = q('[data-a="save"]'), del = q('[data-a="del"]');
    if (ap) { ap.disabled = busy; ap.innerHTML = M.applying && ed ? `<span class="spinner"></span>Aplicando…` : `${ICO("check")}${mainLabel()}`; }
    if (sv) { sv.disabled = busy; sv.innerHTML = M.saving ? `<span class="spinner"></span>Guardando…` : `${ICO("save")}${M.savedId ? "Guardar cambios" : "Guardar perfil"}`; }
    if (del) del.disabled = busy;
    const st = q('[data-a="state"]');
    if (st) st.textContent = dirty() ? "Cambios sin guardar" : ed && changed() ? "Sin aplicar al video" : "";
  }
  function msg(t, bad) { const m = M.el.querySelector('[data-a="msg"]'); if (m) { m.textContent = t || ""; m.classList.toggle("bad", !!bad); } }

  // ------------------------------------------------------------------ telefono, WhatsApp y correo
  const CONTACT = { whatsapp: "whatsapp", phone: "phone", email: "email" };
  const digits = s => String(s || "").replace(/\D/g, "");
  /** El enlace es el del campo de contacto (profile.normalize_profile: from_field; uno anterior a la
      marca, si dice el mismo numero o correo). */
  function fieldIs(k, l) {
    if (l.from_field === true) return true;
    if (l.from_field === false) return false;
    const f = String(M.draft[k] || "").trim().toLowerCase(); if (!f) return false;
    if (k === "email") return String(l.url || "").toLowerCase().replace(/^mailto:/, "") === f;
    const a = digits(f), b = digits(l.url) || digits(l.display);
    return a.length >= 8 && b.length >= 8 && (a.endsWith(b.slice(-10)) || b.endsWith(a.slice(-10)));
  }
  /** El campo de telefono / WhatsApp cambio: el servidor rehace SU enlace como lo guardaria (POST
      /profile/normalize; vacio = sin enlace) y la fila lo muestra al instante. */
  async function refield(k) {
    if (!M || !M.draft || !CONTACT[k]) return;
    const draft = M.draft, plat = CONTACT[k], mine = (M.refSeq = (M.refSeq || 0) + 1), sent = draft[k] || "";
    const own = (draft.links || []).filter(l => l.platform === plat);
    let got;
    try {
      const d = await jreq("/profile/normalize", "POST", { profile: { [k]: sent, links: clean({ links: own }).links } });
      got = d.profile || {};
    } catch (e) {
      if (e.status !== 400) return;                      // sin servidor: lo hace al guardar
      got = { [k]: "", links: [] };                       // vacio: sin numero ni su enlace
    }
    // se siguio escribiendo en el campo mientras tanto: manda lo nuevo (llega otro cambio)
    if (!M || M.draft !== draft || M.refSeq !== mine || (draft[k] || "") !== sent) return;
    const L = draft.links || [], at = L.findIndex(l => l.platform === plat);
    const rest = L.filter(l => l.platform !== plat), nl = (got.links || []).filter(l => l.platform === plat);
    const before = at < 0 ? rest.length : L.slice(0, at).filter(l => l.platform !== plat).length;
    rest.splice(before, 0, ...nl);
    draft.links = rest;
    draft[k] = got[k] || "";                             // con su formato (+52 55 1234 5678)
    const fi = M.el.querySelector(`[data-f="${k}"]`);
    if (fi && fi.value.trim() === sent.trim()) fi.value = draft[k];
    renderLinks(); syncBtns();
  }

  function renderLinks() {
    const box = M.el.querySelector('[data-a="links"]'); if (!box) return;
    const L = M.draft.links || [];
    if (!L.length) { box.innerHTML = `<div class="hint">Sin enlaces todavía.</div>`; return; }
    const kinds = {}; CAT.forEach(p => { (kinds[p.kind] = kinds[p.kind] || []).push(p); });
    const selOpts = cur => Object.keys(kinds).map(k => `<optgroup label="${esc(KIND_ES[k] || k)}">${kinds[k].map(p => `<option value="${esc(p.id)}" ${p.id === cur ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</optgroup>`).join("");
    box.innerHTML = L.map((l, i) => {
      const p = plat(l.platform), social = (l.kind || p.kind) === "social";
      if (l._h0 == null) l._h0 = l.handle || "";
      const amb = l.ambiguous && !l._picked;
      return `<div class="pflink ${amb ? "amb" : ""}" data-i="${i}">${iconSvg(l.platform, "#fff")}
        <select class="selinp pfplat" data-k="platform" title="Red o tipo de enlace">${selOpts(l.platform)}</select>
        <input type="text" class="ftext" data-k="${social ? "handle" : "label"}" value="${esc(social ? l.handle || "" : l.label || "")}" placeholder="${social ? "@usuario" : "Texto del botón"}" title="${social ? "Usuario" : "Texto del botón (p. ej. Agenda en PaseoClub)"}">
        <input type="text" class="ftext" data-k="display" value="${esc(l.display || "")}" placeholder="Cómo se muestra" title="Cómo se muestra en el video">
        ${social ? `<button class="xbtn pfstar ${l.primary ? "on" : ""}" data-a="primary" title="${l.primary ? "Red principal (botón Seguir)" : "Usar como red principal (botón Seguir)"}">${ICO("star")}</button>` : `<span class="pfstar-sp"></span>`}
        <button class="xbtn" data-a="rm" title="Quitar">${ICO("trash")}</button>
        ${amb ? `<div class="pfnote">${ICO("alert")}No dijiste la red de ${esc(l.handle || "este usuario")}: se tomó como ${esc(p.label)}. Elige otra si no es.</div>` : ""}
        <div class="pfurl">${esc(l.url || "")}</div>${mediaRow(l)}</div>`;
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
          dropAutoMedia(l);
          renderLinks(); relink(l);
        };
        else {
          inp.oninput = () => { l[k] = inp.value; syncBtns(); };
          // el @usuario de una red: al terminar de escribirlo (Enter o salir del campo) la url se rehace
          // y la foto/logo de la cuenta anterior se descartan
          if (k === "handle") {
            inp.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); inp.blur(); } };
            inp.onchange = () => {
              const h0 = String(l._h0 == null ? "" : l._h0).replace(/^@/, "").toLowerCase(), h1 = String(inp.value || "").trim().replace(/^@/, "").toLowerCase();
              if (h1 && h1 !== h0) { dropAutoMedia(l); renderLinks(); relink(l, inp.value); }
            };
          }
        }
      });
      const st = row.querySelector('[data-a="primary"]'); if (st) st.onclick = () => { L.forEach((x, j) => { x.primary = j === i ? !x.primary : false; }); renderLinks(); syncBtns(); };
      row.querySelector('[data-a="rm"]').onclick = () => {
        // quitar el enlace de un campo de contacto (WhatsApp, telefono, correo) vacia ese campo (si no,
        // al guardar se volveria a crear con el numero del campo)
        const k = CONTACT[l.platform];
        if (k && fieldIs(k, l)) { M.draft[k] = ""; const fi = M.el.querySelector(`[data-f="${k}"]`); if (fi) fi.value = ""; }
        L.splice(i, 1); renderLinks(); syncBtns();
      };
      row.querySelectorAll(".pfmed.logo img").forEach(im => { if (im.complete && im.naturalWidth) logoPlate(im); else im.onload = () => logoPlate(im); });
      row.querySelectorAll("[data-m]").forEach(b => {
        const [act, slot] = b.dataset.m.split(":");
        b.onclick = () => (act === "up" ? uploadLinkMedia(l, slot) : act === "rm" ? PF.setLinkMedia(l, slot, null) : restoreLinkMedia(l, slot));
      });
    });
  }

  // ------------------------------------------------------------------ foto y logo de cada enlace
  const SLOT_ES = { photo: ["Foto", "Obteniendo foto…", "Sin foto"], logo: ["Logo", "Obteniendo logo…", "Sin logo"] };
  const NO_PREVIEW = ["email", "phone", "maps", "whatsapp"];      // profile.NO_PREVIEW: sin pagina que mirar
  const hasPage = l => !!l && !NO_PREVIEW.includes(l.platform) && /^https?:\/\//i.test(String(l.url || ""));
  const offOf = (l, slot) => !!(l && l.media_off && l.media_off[slot]);
  /** Redes y mensajeria llevan su icono de marca: no se les busca logo (linkmedia.OWN_ICON). */
  const ownIcon = l => ["social", "messaging"].includes(l.kind || plat(l.platform).kind);
  /** Un enlace con pagina al que le falta foto (o logo) que no subio ni quito el usuario
      (profile.media_wanted sin lo ya descargado). */
  function missingMedia(l) {
    if (!hasPage(l)) return false;
    return (!l.photo && !offOf(l, "photo")) || (!ownIcon(l) && !l.logo && !offOf(l, "logo"));
  }
  /** ...y que aun no se le pregunto al servidor en este panel. */
  const wantsMedia = l => missingMedia(l) && !l._mediaTried;
  /** Miniaturas de foto y logo de un enlace con su estado y acciones: Cambiar (subir una propia),
      Quitar (no se usa ni se vuelve a bajar) y Buscar de nuevo en la pagina. La foto usa la miniatura
      del servidor; el logo el archivo (PNG con transparencia) sobre la placa del video (logoPlate). */
  function mediaRow(l) {
    if (!hasPage(l) && !l.photo && !l.logo) return "";
    const st = M && M.draft ? M.draft.media_status : null;
    const slot = k => {
      const m = l[k], off = offOf(l, k), [lab, wait, none] = SLOT_ES[k];
      if (k === "logo" && ownIcon(l) && !m) return "";            // las redes llevan su icono de marca
      let th, info, btns, img = false;
      const againT = `Volver a buscar ${k === "logo" ? "el logo" : "la foto"} en su página`;
      const again = hasPage(l) ? `<button class="xbtn" type="button" data-m="auto:${k}" title="${againT}" aria-label="${againT}">${ICO("restore")}</button>` : "";
      if (m && m.sid && !off) {
        const src = k === "logo" ? `/tlsource/${encodeURIComponent(m.sid)}` : (m.thumb || `/tlsource/${encodeURIComponent(m.sid)}/thumb`);
        th = `<img src="${esc(src)}" alt="" data-sid="${esc(m.sid)}">`; img = true;
        info = `<b>${lab}</b><span>${(l.media_src && l.media_src[k]) === "user" ? "La tuya" : "De la página"}</span>`;
        btns = `<button class="btn ghost mini" type="button" data-m="up:${k}" title="Subir otra">${ICO("upload")}Cambiar</button>${again}<button class="xbtn" type="button" data-m="rm:${k}" title="Quitar (no se vuelve a tomar de la página)" aria-label="Quitar ${k === "logo" ? "el logo" : "la foto"}">${ICO("trash")}</button>`;
      } else if ((st === "fetching" || l._refetch) && !off && hasPage(l)) {
        th = `<span class="spinner"></span>`; info = `<b>${lab}</b><span>${wait}</span>`; btns = "";
      } else {
        th = ICO(k === "logo" ? "image" : "user");
        const why = off ? "Quitada: no se usa" : st === "offline" && hasPage(l) ? "La página no respondió: se reintenta sola" : none;
        info = `<b>${lab}</b><span>${why}</span>`;
        btns = `<button class="btn ghost mini" type="button" data-m="up:${k}">${ICO("upload")}Subir</button>` + again;
      }
      return `<div class="pfmed ${k}" data-slot="${k}"><span class="pth${img ? " img" : ""}">${th}</span><span class="pmn">${info}</span>${btns}</div>`;
    };
    const html = slot("photo") + slot("logo");
    return html ? `<div class="pfmedia">${html}</div>` : "";
  }
  /** Miniatura del logo como la vera el video (FXCore.logoLook, igual que graphics.logo_look): a
      sangre si es un icono de app, o sobre su placa (blanca, oscura si el logo es blanco, o el color
      del borde de un JPG). */
  const LOOKS = {};
  function logoPlate(im) {
    const C = window.FXCore, sid = im.dataset.sid; if (!C || !C.logoLook) return;
    let look = LOOKS[sid];
    if (!look) {
      try {
        const k = Math.min(1, 128 / Math.max(im.naturalWidth, im.naturalHeight)), w = Math.max(1, Math.round(im.naturalWidth * k)), h = Math.max(1, Math.round(im.naturalHeight * k));
        const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
        const g = cv.getContext("2d", { willReadFrequently: true }); g.drawImage(im, 0, 0, w, h);
        look = LOOKS[sid] = C.logoLook(g.getImageData(0, 0, w, h).data, w, h);
      } catch (e) { return; }
    }
    const box = im.parentNode;
    if (look.bleed) { im.style.objectFit = "cover"; im.style.padding = "0"; }
    else box.style.background = look.plate;
  }
  /** Fija (media {sid...}) o quita (null) la foto/logo de un enlace del borrador. Quitar la APAGA
      (media_off): el servidor no la vuelve a bajar. Subida por el usuario: media_src 'user'. */
  PF.setLinkMedia = function (l, slot, media) {
    l.media_src = Object.assign({}, l.media_src); l.media_off = Object.assign({}, l.media_off);
    if (media) { l[slot] = media; l.media_src[slot] = "user"; l.media_off[slot] = false; }
    else { delete l[slot]; delete l.media_src[slot]; l.media_off[slot] = true; }
    if (M && M.el) renderLinks();
    return l;
  };
  /** «Volver a buscar en su pagina»: la foto (o el logo) se vuelve a tomar de la pagina del enlace
      aunque ya hubiera una (una cuenta que cambio su foto; una subida propia que ya no se quiere; una
      quitada): se enciende, se descarta la actual y el servidor la vuelve a bajar (POST
      /profile/media/refresh, forzado) mientras la fila dice «Obteniendo…». */
  function restoreLinkMedia(l, slot) {
    l.media_off = Object.assign({}, l.media_off, { [slot]: false });
    l.media_src = Object.assign({}, l.media_src); delete l.media_src[slot];
    delete l[slot];
    if (!["photo", "logo"].some(k => l.media_src[k] === "auto")) delete l.media_for;
    l._mediaTried = false; l._refetch = true;
    renderLinks(); syncBtns(); PF.refreshMedia(true);
  }
  /** Foto/logo AUTOMATICOS fuera (los de otra cuenta tras cambiar la red o el @usuario); lo subido por
      el usuario se queda. */
  function dropAutoMedia(l) {
    const src = l.media_src || {};
    ["photo", "logo"].forEach(k => { if (l[k] && src[k] !== "user") { delete l[k]; delete src[k]; } });
    delete l.media_for; l._mediaTried = false;
    if (Object.keys(src).length) l.media_src = src; else delete l.media_src;
  }
  /** El enlace cambio de red o de @usuario: el servidor lo sanea como lo guardaria (POST
      /profile/normalize: url rehecha, texto nuevo, foto/logo de ESA cuenta) y la fila lo muestra; lo
      que aun no baja llega solo (refreshMedia). `typed`: el @usuario escrito (si no es valido se avisa). */
  async function relink(l, typed) {
    if (!M || !M.draft) return;
    const draft = M.draft, mine = (l._relink = (l._relink || 0) + 1);
    try {
      const x = clean({ links: [l] }).links[0];
      if (!x) return;
      const d = await jreq("/profile/normalize", "POST", { profile: { links: [x] } });
      const g = d.profile && (d.profile.links || [])[0];
      if (!M || M.draft !== draft || l._relink !== mine || !g || !(draft.links || []).includes(l)) return;
      const keep = { primary: !!l.primary, _picked: l._picked };
      Object.keys(l).forEach(k => { delete l[k]; });
      Object.assign(l, g, keep);
      l._h0 = l.handle || "";
      if (draft.media_status !== "fetching" && d.profile.media_status === "fetching") draft.media_status = "fetching";
      renderLinks(); syncBtns();
      const want = String(typed || "").trim().replace(/^@/, "").toLowerCase();
      if (want && String(l.handle || "").replace(/^@/, "").toLowerCase() !== want) msg(`«${typed}» no parece un usuario de ${plat(l.platform).label}: usa solo letras, números, puntos o guiones bajos.`, true);
      else msg("");
      PF.refreshMedia();
    } catch (e) { msg(`No se pudo actualizar el enlace: ${e.message || e}`, true); }
  }
  function uploadLinkMedia(l, slot) {
    const fi = Object.assign(document.createElement("input"), { type: "file", accept: "image/*" });
    fi.onchange = async () => {
      const f = fi.files && fi.files[0]; if (!f) return;
      msg(slot === "logo" ? "Subiendo el logo…" : "Subiendo la foto…");
      try {
        const fd = new FormData(); fd.append("files", f); fd.append("transcribe", "0");
        const r = await fetch("/sources", { method: "POST", body: fd });
        const d = await r.json(); const x = d && d.sources && d.sources[0];
        if (!r.ok || !x || x.type !== "image") throw new Error("usa una imagen (JPG, PNG, WEBP o HEIC)");
        PF.setLinkMedia(l, slot, { sid: x.id, type: "image", name: x.name, w: x.w, h: x.h, thumb: x.thumb || undefined });
        msg(slot === "logo" ? "Logo listo." : "Foto lista.");
      } catch (e) { msg(`No se pudo subir: ${e.message || e}`, true); }
    };
    fi.click();
  }
  const urlKey = u => String(u || "").trim().toLowerCase().replace(/^https?:\/\/(www\.)?/, "").replace(/\/+$/, "");
  /** Mismo enlace en otra copia del perfil (url sin esquema/www/barra final, o red + usuario). */
  const sameLink = (a, b) => (!!a.url && urlKey(a.url) === urlKey(b.url)) || (!!a.handle && a.platform === b.platform && a.handle === b.handle);
  /** Toma del perfil del servidor la foto/logo AUTOMATICOS de cada enlace; lo que el usuario eligio
      (media_src 'user') o quito (media_off) en el borrador manda. */
  PF.adoptLinkMedia = function (draft, prof) {
    const got = (prof && prof.links) || [];
    const busy = prof && prof.media_status === "fetching";
    (draft.links || []).forEach(l => {
      const g = got.find(x => x && sameLink(l, x));
      l._mediaTried = true;
      // «Volver a buscar»: hasta que termine la descarga no se toma la de antes (de la cache)
      if (l._refetch) { if (busy) return; delete l._refetch; }
      if (!g) return;
      ["photo", "logo"].forEach(k => {
        if (offOf(l, k) || (l.media_src && l.media_src[k] === "user")) return;
        if (g[k] && g[k].sid) {
          l[k] = g[k];
          l.media_src = Object.assign({}, l.media_src, { [k]: (g.media_src && g.media_src[k]) || "auto" });
          if (g.media_for) l.media_for = g.media_for;
        }
      });
    });
    return draft;
  };
  /** Fotos/logos de los enlaces del borrador. Lo que falta lo baja el servidor en segundo plano
      (profile.media_wanted): se pregunta con POST /profile/parse de sus urls (sin IA; nunca vuelve
      a bajar lo que ya tiene) y se sigue preguntando mientras media_status sea 'fetching' (el panel
      dice "Obteniendo foto…"). `force` (el usuario pide buscarla de nuevo): POST
      /profile/media/refresh con el borrador y luego el mismo sondeo. Sin red o sin la funcion en el
      servidor: quedan las iniciales / iconos. */
  PF.refreshMedia = async function (force, tries) {
    if (!M || !M.draft) return;
    const draft = M.draft;
    const links = (draft.links || []).filter(force || tries ? missingMedia : wantsMedia);
    if (!links.length) {
      // nada que pedir (ya llegaron o se apagaron): el aviso "Obteniendo…" no se queda colgado
      if (draft.media_status === "fetching") { draft.media_status = "done"; if (M.el.classList.contains("on")) renderLinks(); }
      return;
    }
    const seq = M.mediaSeq = (M.mediaSeq || 0) + 1;
    draft.media_status = "fetching"; if (M.el.classList.contains("on")) renderLinks();
    let again = false;
    try {
      const re = links.filter(l => l._refetch);
      const d = force && !tries ? await jreq("/profile/media/refresh", "POST", { profile: clean(re.length ? { links: re } : draft) })
        : await jreq("/profile/parse", "POST", { text: links.map(l => l.url).join("\n"), use_llm: false });
      if (!M || seq !== M.mediaSeq || M.draft !== draft) return;      // otro pedido u otro borrador manda
      const prof = (d && d.profile) || {};
      PF.adoptLinkMedia(draft, prof);
      draft.media_status = prof.media_status || "done";
      again = draft.media_status === "fetching" && (tries || 0) < 16;
    } catch (e) {
      if (!M || seq !== M.mediaSeq || M.draft !== draft) return;
      (draft.links || []).forEach(l => { l._mediaTried = true; delete l._refetch; });
      draft.media_status = null;
    }
    if (!again && draft.media_status === "fetching") draft.media_status = "done";
    if (!again) (draft.links || []).forEach(l => { delete l._refetch; });
    if (M.el.classList.contains("on")) renderLinks();
    if (again) setTimeout(() => { if (M && M.draft === draft) PF.refreshMedia(false, (tries || 0) + 1); }, 1500);
  };
  /** Proyecto abierto: la foto/logo que aun bajan llegan SOLAS al plan del servidor (profile_media
      'applied' con su plan_rev). Se pregunta GET /profile/{job} y se adopta ese plan si el Studio no
      tiene cambios; si los tiene, el autoguardado los junta en el servidor (profile_applied). */
  let JOB_WATCH = null, WATCHED = null;
  PF.jobMedia = null;
  PF.watchJobMedia = function (n) {
    clearTimeout(JOB_WATCH);
    if (typeof JOB === "undefined" || !JOB || JOB === "demo" || (n || 0) > 20) { if (PF.jobMedia) { PF.jobMedia = null; PF.renderEditorGroup(); } return; }
    const was = PF.jobMedia; PF.jobMedia = "fetching";
    if (!was) PF.renderEditorGroup();
    JOB_WATCH = setTimeout(async () => {
      let d = null; try { d = await jreq(`/profile/${JOB}`); } catch (e) { d = null; }
      const st = d && d.profile && d.profile.media_status, pm = (d && d.profile_media) || {};
      if (pm.state === "applied" && pm.plan_rev != null && window.FX && FX.serverRev != null && +pm.plan_rev > +FX.serverRev && FX.isClean && FX.isClean()) {
        try {
          const p = await jreq(`/plan/${JOB}`);
          if (p.plan && FX.isClean()) { FX.adoptServerPlan(p.plan, p.plan_rev); toast("Foto y logo del perfil listos en el video.", "image"); }
        } catch (e) { /* el siguiente autoguardado lo junta */ }
      }
      if (st === "fetching") PF.watchJobMedia((n || 0) + 1);
      else { PF.jobMedia = null; PF.renderEditorGroup(); }
    }, 2000);
  };
  /** Pegar un enlace o @usuario: el servidor lo reconoce (misma regla que el motor, sin IA). */
  async function addLink() {
    const inp = M.el.querySelector('[data-a="lnew"]'); const txt = inp.value.trim(); if (!txt) return;
    try {
      const d = await jreq("/profile/parse", "POST", { text: txt, use_llm: false });
      const got = (d.profile && d.profile.links) || [];
      if (!got.length) {
        // un numero suelto: telefono / WhatsApp (su enlace lo arma el servidor)
        if (/^\+?[\d\s().-]{8,}$/.test(txt)) {
          const k = !M.draft.whatsapp ? "whatsapp" : "phone";
          M.draft[k] = txt; inp.value = ""; render(); refield(k);
          msg(k === "whatsapp" ? "Número agregado como WhatsApp." : "Número agregado como teléfono."); return;
        }
        msg("No reconocí un enlace ni un @usuario en ese texto.", true); return;
      }
      const L = M.draft.links = M.draft.links || [];
      let n = 0, fill = false;
      got.forEach(g => {
        if (L.some(x => (x.url && x.url === g.url) || (x.handle && g.handle && x.handle === g.handle && x.platform === g.platform))) return;
        if (g.primary && L.some(x => x.primary)) g.primary = false;
        // WhatsApp / telefono / correo: es el del campo si el campo estaba vacio; si no, uno aparte
        const k = CONTACT[g.platform];
        if (k) { g.from_field = !M.draft[k] && !!d.profile[k] && !L.some(x => x.platform === g.platform && x.from_field === true); if (g.from_field) { M.draft[k] = d.profile[k]; fill = true; } }
        L.push(g); n++;
      });
      inp.value = "";
      if (fill) render(); else renderLinks();
      msg(n ? (d.notes || []).filter(x => /sin red|@/.test(x)).join(" ") || "Enlace agregado." : "Ese enlace ya estaba.");
      if (n) PF.refreshMedia();
    } catch (e) { msg(`No se pudo leer el enlace: ${e.message || e}`, true); }
  }
  function renderAvatar() {
    const box = M.el.querySelector('[data-a="avatar"]'); if (!box) return;
    const a = M.draft.avatar;
    box.innerHTML = `<div class="mediapick"><div class="mth">${a && a.sid ? `<img src="${esc(a.thumb || `/tlsource/${encodeURIComponent(a.sid)}`)}" alt="">` : ICO("user")}</div>`
      + `<div class="mnm"><b>${esc(a && a.sid ? a.name || "Foto de perfil" : "Sin foto")}</b><span>${a && a.sid ? "Va en el botón Seguir y la tarjeta final (manda sobre la de sus redes)" : "Sin foto se usa la de su Instagram o su página (si la hay) o las iniciales"}</span></div>`
      + `<button class="btn ghost mini" type="button" data-a="aup">${ICO("upload")}${a && a.sid ? "Cambiar" : "Subir foto"}</button>`
      + (a && a.sid ? `<button class="xbtn" type="button" data-a="arm" title="Quitar la foto">${ICO("trash")}</button>` : "") + `</div>`;
    box.querySelector('[data-a="aup"]').onclick = () => {
      const fi = Object.assign(document.createElement("input"), { type: "file", accept: "image/*" });
      fi.onchange = async () => {
        const f = fi.files && fi.files[0]; if (!f) return;
        msg("Subiendo la foto…");
        try {
          const fd = new FormData(); fd.append("files", f); fd.append("transcribe", "0");
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
      if (L.some(x => urlKey(x.url) === urlKey(g.url))) return;
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
          M.parsed = null; render(); PF.refreshMedia();
          msgAct(`Es otra persona: se reemplazó todo el perfil de ${prev.name || "antes"} (enlaces, teléfono, WhatsApp, dirección y foto).`,
            "Era la misma: completar en su lugar", () => { M.draft = prev; M.savedId = prevId; merge(M.draft, p); render(); msg("Datos agregados al perfil: revísalos y aplica."); });
          return;
        }
        merge(M.draft, p);
        M.parsed = null; render(); msg("Datos agregados: revísalos y aplica.");
        PF.refreshMedia();
      };
      out.querySelector('[data-a="drop"]').onclick = () => { out.innerHTML = ""; M.parsed = null; };
    } catch (e) { out.innerHTML = `<div class="pfmsg bad">No se pudo entender el texto: ${esc(e.message || e)}</div>`; }
    finally { b.disabled = false; b.innerHTML = `${ICO("spark")}Entender`; }
  }
  /** Misma persona: el mismo nombre (sin titulo, acentos ni signos: «Ana Demo» y «Dra. Ana Demo»), o
      uno contiene al otro con al menos DOS palabras en comun («Dra. Ana Demo» y «Ana Demo Ruiz»). Un
      nombre de pila solo («Ana») no basta: puede ser otra persona. */
  PF.samePerson = function (a, b) {
    const x = nameKey(a && a.name), y = nameKey(b && b.name);
    if (!x.length || !y.length) return false;
    if (x.join(" ") === y.join(" ")) return true;
    return x.filter(w => y.includes(w)).length >= 2 && !PF.otherPerson(a, b);
  };
  /** «Completar ese perfil»: el guardado `twin` con lo que el borrador trae (los campos escritos ganan;
      un enlace igual o de la misma red social se reemplaza y los demas se suman) SIN perder lo que el
      borrador no trae (enlaces, telefonos, direccion, foto). `withUse`: los interruptores que el
      borrador cambio respecto a los de siempre tambien pasan. */
  function mergeInto(twin, draft, withUse) {
    const out = clean(twin), d = clean(draft);
    FIELDS.forEach(([k]) => { if (String(d[k] || "").trim()) out[k] = d[k]; });
    ["email", "tagline"].forEach(k => { if (String(d[k] || "").trim()) out[k] = d[k]; });
    if (d.avatar && d.avatar.sid) out.avatar = d.avatar;
    const L = out.links = out.links || [];
    (d.links || []).forEach(g => {
      let i = L.findIndex(x => sameLink(x, g));
      if (i < 0 && isSocial(g)) i = L.findIndex(x => x.platform === g.platform);
      if (i >= 0) L[i] = Object.assign({}, g); else L.push(Object.assign({}, g));
    });
    const star = (d.links || []).find(l => l.primary && isSocial(l));
    if (star) L.forEach(x => { x.primary = sameLink(x, star); });
    const def = USE_DEF(), du = Object.assign(USE_DEF(), d.use || {});
    out.use = Object.assign(USE_DEF(), twin.use || {});
    if (withUse !== false) Object.keys(def).forEach(k => { if (du[k] !== def[k]) out.use[k] = du[k]; });
    out.id = twin.id;
    return out;
  }
  /** Pregunta al guardar uno sin ligar con el nombre de otro(s) ya guardado(s). Enfocado (Enter) lo
      que no toca nada guardado: «Guardar otro aparte». */
  function twinDialog(d, twins) {
    const T = twins.slice(0, 3), one = T.length === 1;
    return {
      title: one ? `Ya tienes guardado el perfil de «${nmOf(T[0])}»` : `Ya tienes perfiles guardados de «${nmOf(d)}»`,
      text: (one ? `Guardado: ${PF.summary(T[0]) || labelFull(T[0])}.` : "Guardados:\n" + T.map(t => `· ${PF.summary(t) || labelFull(t)}`).join("\n"))
        + `\n\n«Guardar otro aparte» crea un perfil nuevo con lo que escribiste. ${one ? "«Completar ese perfil» le agrega" : "«Completar…» le agrega al perfil que elijas"} lo que escribiste aquí y conserva sus enlaces, teléfonos y foto.`,
      choices: [{ id: "new", label: "Guardar otro aparte" }].concat(T.map(t => ({ id: "upd:" + t.id, label: one ? "Completar ese perfil" : `Completar «${labelFull(t)}»`, ghost: true }))),
      icon: "user",
    };
  }
  /** El perfil guardado tiene OTROS datos que el panel: nunca se pisa sin preguntar. -> 'saved' (el
      panel toma la guardada), 'replace' o null. */
  async function conflict(sv) {
    if (!window.Dlg || !sv || !M) return null;
    const pick = await Dlg.choose({
      title: `Tu perfil guardado «${PF.label(sv)}» tiene otros datos`,
      text: M.staleWhy === "video" ? "Este panel tiene la versión de este video, que es distinta de la guardada.\n\nSi la reemplazas, los datos guardados se pierden."
        : "Se guardó con otros datos (en otra ventana o en otro video) después de abrir este panel.\n\nSi la reemplazas, esos datos se pierden.",
      choices: [{ id: "saved", label: "Usar la versión guardada" }, { id: "replace", label: "Reemplazar con la de aquí", danger: true }],
      icon: "alert",
    });
    if (pick === "saved" && M) useSaved(sv);
    return pick;
  }
  /** Guarda el borrador (sin doble envio: un segundo clic espera al primero): con un perfil guardado
      ligado lo ACTUALIZA; sin ligar, si ya hay uno guardado de la misma persona pregunta (nunca una
      copia «-2» muda ni un reemplazo que borre datos). Despues todas las listas quedan al dia (syncAll)
      y, si era el elegido para el proximo video, tambien esa eleccion. -> true si se guardo. */
  function save(opt) {
    if (!M || !M.draft) return Promise.resolve(false);
    if (M.saving) return M.saving;
    const p = M.saving = doSave(opt || {}).finally(() => { if (M && M.saving === p) { M.saving = null; syncBtns(); } });
    syncBtns();
    return p;
  }
  async function doSave(opt) {
    const d = clean(M.draft), ed = M.mode === "editor";
    if (isEmpty(d)) { msg("Agrega al menos el nombre, la profesión o un enlace.", true); return false; }
    const draft = M.draft;
    let id = savedOf(M.savedId) ? M.savedId : null, base = id ? M.baseUpdated : null, payload = d, merged = false;
    if (!id) {
      const twins = SAVED.filter(x => PF.samePerson(x, d));
      if (twins.length && window.Dlg) {
        const pick = await Dlg.choose(twinDialog(d, twins));
        if (!pick || !M || M.draft !== draft) return false;
        if (pick !== "new") {
          const tw = savedOf(pick.slice(4)); if (!tw) return false;
          id = tw.id; base = tw.updated; merged = true;
          payload = mergeInto(tw, d, !ed || M.useTouched);
        }
      }
    } else {
      // el panel tiene otra version que la guardada (la del video, o cambio en otra ventana)
      if (M.stale && !opt.force) { const r = await conflict(savedOf(id)); if (r !== "replace" || !M || M.draft !== draft) return false; base = null; }
      // en el Studio los interruptores son de este video: al perfil guardado solo si se tocaron aqui
      if (ed && !M.useTouched) payload.use = Object.assign(USE_DEF(), (savedOf(id) || {}).use || {});
    }
    if (!id) delete payload.id;
    try {
      const body = Object.assign({ profile: payload }, id ? { id } : {}, id && base != null && !opt.force ? { base_updated: base } : {});
      const r = await jreq("/profiles", "POST", body);
      // el servidor lo normaliza (red cambiada, url armada, etiquetas): el panel muestra ese
      SAVED = r.profiles || SAVED;
      if (!M || M.draft !== draft) { syncAll(); return true; }
      const panelUse = M.draft.use;
      M.savedId = r.id;
      M.draft = clone(r.profile);
      // en el Studio el panel sigue con los interruptores de este video
      M.draft.use = ed ? Object.assign(USE_DEF(), panelUse) : Object.assign(USE_DEF(), (r.profile && r.profile.use) || {});
      syncSnap(r.profile && r.profile.updated); M.useTouched = false;
      // el elegido para el proximo video es este (Inicio) o se acaba de actualizar (desde el Studio)
      if (!ed || SEL_ID === r.id) setSel(r.profile, r.id);
      if (!ed) M.open0 = clean(M.draft);
      render();
      msg(merged ? `Perfil «${nmOf(r.profile)}» completado: se conservaron sus enlaces y teléfonos.` : r.replaced ? "Perfil guardado (actualizado)." : `Perfil guardado como «${PF.label(r.profile) || r.id}».`);
      syncAll();
      PF.refreshMedia();
      return true;
    } catch (e) {
      if (e.status === 409 && e.data && e.data.profile && M && M.draft === draft) {
        SAVED = e.data.profiles || SAVED; syncAll();
        // «Completar ese perfil» y el guardado cambio justo antes: se vuelve a preguntar con el de ahora
        if (merged) return (opt.retry || 0) < 2 ? doSave(Object.assign({}, opt, { retry: (opt.retry || 0) + 1 })) : false;
        // otra ventana u otro video lo guardo despues de abrir el panel: se pregunta
        M.stale = true; M.staleWhy = "other"; M.baseUpdated = null; render();
        const r = await conflict(savedOf(e.data.id) || e.data.profile);
        return r === "replace" && M && M.draft === draft ? doSave({ force: true }) : false;
      }
      msg(`No se pudo guardar: ${e.message || e}`, true); return false;
    }
  }
  /** Eliminar el perfil guardado: confirmacion con el dialogo propio (window.confirm no existe en la app
      de escritorio). Sale de todas las listas; si era el elegido para el proximo video, queda sin perfil.
      Los videos que ya lo usan conservan sus datos. */
  async function delSaved() {
    if (!M || !M.savedId || M.saving || M.applying) return;
    const id = M.savedId, p = savedOf(id), name = (p && PF.label(p)) || id;
    const ok = window.Dlg ? await Dlg.confirm({
      title: `¿Eliminar el perfil «${name}»?`,
      text: "Se quita de tus perfiles guardados. Los videos que ya lo usan no cambian.",
      ok: "Eliminar perfil", danger: true,
    }) : false;
    if (!ok || !M) return;
    try {
      const r = await jreq(`/profiles/${encodeURIComponent(id)}`, "DELETE");
      SAVED = r.profiles || [];
    } catch (e) {
      if (e.status !== 404) { msg(`No se pudo eliminar: ${e.message || e}`, true); return; }
      SAVED = SAVED.filter(x => x.id !== id);          // ya no estaba (otra ventana): igual sale de la lista
    }
    const wasNext = SEL_ID === id, ed = M.mode === "editor";
    if (wasNext) setSel(null, null);
    if (M.savedId === id) {
      M.savedId = null; delete M.draft.id; M.stale = false; M.baseUpdated = null;
      // Inicio: el panel queda listo para otro perfil; en el editor el borrador es el del video (se queda)
      if (!ed) { M.draft = blank(M.draft.use); M.open0 = clean(M.draft); }
      M.snap = clean(M.draft);
    }
    render();
    msg(`Perfil «${name}» eliminado.` + (ed ? ` Este video conserva sus datos.${wasNext ? " Tu próximo video ya no lo usa." : ""}` : wasNext ? " El próximo video queda sin perfil." : ""));
    syncAll();
  }
  /** «Usar en el próximo video» / «Aplicar al video» (sin doble clic). Con cambios sin guardar en un
      perfil guardado primero se guardan (el boton dice «Guardar y…»); sin editar nada nunca guarda (un
      video con otra version del perfil no pisa la guardada). */
  async function apply() {
    if (!M || M.applying || M.saving) return;
    M.applying = true; syncBtns();
    try {
      if (dirty() && !(await save())) return;
      if (!M) return;
      const d = clean(M.draft);
      if (M.mode === "editor") {
        syncBtns();
        if (await PF.applyToJob(isEmpty(d) ? null : d)) { M.open0 = clean(M.draft); PF.close(); }
        return;
      }
      setSel(isEmpty(d) ? null : d, savedOf(M.savedId) ? M.savedId : null);
      M.open0 = clean(M.draft);
      PF.close();
      toast(isEmpty(d) ? "Sin perfil para el próximo video." : `Perfil listo: ${PF.summary(d)}.`, "user");
    } finally { if (M) { M.applying = false; syncBtns(); } }
  }
  // piezas puras para las pruebas (tests/js/profile.test.js)
  PF._t = { clean, merge, replaced, sameLink, dropAutoMedia, nameKey, isEmpty, mergeInto, twinDialog, labelFull };
})();
