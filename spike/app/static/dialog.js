/* Reelfy Studio: dialogos propios (confirmar, pedir un texto, avisar, elegir entre opciones).
   Reemplazan a window.confirm / alert / prompt, que en la app de escritorio NO funcionan: la app es
   Tauri 2 + wry (WKWebView en macOS) y wry no implementa los paneles de JavaScript, asi que confirm()
   devuelve false al instante sin mostrar nada (p. ej. «Eliminar perfil» no hacia nada), alert() no se
   ve y prompt() devuelve null. tests/test_profile_e2e.py falla si vuelven a aparecer en el Studio.

   API (todas devuelven una Promise; el texto se escapa aqui, nunca va HTML del usuario):
     Dlg.confirm({title, text, ok, cancel, danger, icon})          -> true | false
     Dlg.prompt({title, text, label, value, placeholder, ok, cancel, maxLength, validate(v) -> error|null})
                                                                    -> texto (recortado) | null
     Dlg.alert({title, text, ok, icon})                             -> undefined
     Dlg.choose({title, text, choices: [{id, label, danger?, ghost?}], cancel, icon, focus}) -> id | null
     Dlg.isOpen()
   Un texto suelto tambien sirve: Dlg.confirm("¿Seguro?"). `focus` (id de un boton, "cancel" u "ok"):
   el que empieza enfocado (Enter lo activa); sin el, la primera opcion de `choose`, Cancelar si borra
   (danger) o Aceptar.

   Modal accesible: role alertdialog (dialog si pide texto), aria-modal, titulo y descripcion ligados;
   el teclado queda atrapado (Tab / Mayus+Tab dan la vuelta dentro), Escape cancela, Enter en el campo
   acepta (en un boton, lo activa), y al cerrar el foco vuelve a donde estaba. Si la accion borra algo
   (danger) el foco empieza en Cancelar, como en macOS. Los atajos del editor no corren detras.
   Simetrico: caja centrada, botones del mismo ancho y el grupo centrado. */
(function (root) {
  "use strict";
  if (typeof document === "undefined") return;
  const D = (root.Dlg = {});
  const ICO = n => `<svg class="ic" viewBox="0 0 24 24"><use href="#i-${n}"/></svg>`;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const STACK = [];
  let SEQ = 0;

  (function css() {
    if (document.getElementById("rdlg-css")) return;
    const st = document.createElement("style"); st.id = "rdlg-css";
    st.textContent = ".modal.rdlgw{z-index:95;padding:16px;box-sizing:border-box}"
      + ".rdlgw .rdlg{text-align:center;box-sizing:border-box;max-height:calc(100vh - 32px);overflow:auto}"
      + ".rdlgw .rdlg h3{justify-content:center;text-align:center}"
      + ".rdlgw .rdlg p{white-space:pre-line}"
      + ".rdlgw .rdlg p+p{margin-top:8px}"
      + ".rdlgw .rdi.info{background:rgba(255,176,32,.12);border-color:rgba(255,176,32,.38);color:var(--brand2)}"
      + ".rdlgw .rdl-f{margin-top:16px;text-align:left}"
      + ".rdlgw .rdl-f label{display:block;font-size:11.5px;color:var(--muted);margin:0 0 6px;font-weight:600;text-transform:none;letter-spacing:0}"
      + ".rdlgw .rdl-f input{width:100%;box-sizing:border-box}"
      + ".rdlgw .rdl-err{margin:8px 0 0;color:#fca5a5;font-size:12px;text-align:left}"
      + ".rdlgw .rdl-err:empty{display:none}"
      + ".rdlgw .rbtns.n1{grid-template-columns:minmax(0,calc(50% - 5px));justify-content:center}"
      + ".rdlgw .rbtns.n3{grid-template-columns:minmax(0,1fr)}"
      + ".rdlgw .rbtns .btn:focus-visible{outline:none;box-shadow:0 0 0 2px var(--panel),0 0 0 4px rgba(255,176,32,.9)}";
    (document.head || document.documentElement).appendChild(st);
  })();

  const norm = (o, kind) => Object.assign({ kind }, typeof o === "string" ? { text: o } : o || {});

  /** Abre un dialogo -> Promise<{pick: 'ok' | 'cancel' | id de una opcion, value: texto del campo | null}>. */
  D.open = function (o) {
    o = norm(o, (o && o.kind) || "confirm");
    const kind = o.kind;
    return new Promise(res => {
      const id = ++SEQ, prev = document.activeElement;
      const tid = `rdlgT${id}`, did = `rdlgD${id}`, iid = `rdlgI${id}`, eid = `rdlgE${id}`;
      let btns;
      if (kind === "alert") btns = [{ id: "ok", label: o.ok || "Entendido" }];
      else if (kind === "choose") btns = (o.choices || []).map(c => ({ id: String(c.id), label: c.label, danger: !!c.danger, ghost: !!c.ghost }))
        .concat([{ id: "cancel", label: o.cancel || "Cancelar", ghost: true }]);
      else btns = [{ id: "cancel", label: o.cancel || "Cancelar", ghost: true },
        { id: "ok", label: o.ok || (kind === "prompt" ? "Guardar" : "Aceptar"), danger: !!o.danger }];
      const paras = String(o.text || "").split(/\n{2,}/).map(t => t.trim()).filter(Boolean);
      const w = document.createElement("div");
      w.className = "modal on rdlgw";
      w.dataset.kind = kind;
      w.setAttribute("role", kind === "prompt" ? "dialog" : "alertdialog");
      w.setAttribute("aria-modal", "true");
      w.setAttribute("aria-labelledby", tid);
      if (paras.length) w.setAttribute("aria-describedby", did);
      const icon = o.icon || (o.danger ? "trash" : kind === "prompt" ? "save" : "alert");
      w.innerHTML = `<div class="box rdlg${o.danger ? " danger" : ""}">`
        + `<div class="rdi${o.danger ? "" : " info"}" aria-hidden="true">${ICO(icon)}</div>`
        + `<h3 id="${tid}">${esc(o.title || "")}</h3>`
        + (paras.length ? `<div id="${did}">${paras.map(p => `<p>${esc(p)}</p>`).join("")}</div>` : "")
        + (kind === "prompt" ? `<div class="rdl-f"><label for="${iid}">${esc(o.label || "Nombre")}</label>`
          + `<input type="text" class="ftext" id="${iid}" aria-label="${esc(o.label || "Nombre")}" maxlength="${+o.maxLength || 80}" autocomplete="off" spellcheck="false" `
          + `value="${esc(o.value || "")}" placeholder="${esc(o.placeholder || "")}" aria-describedby="${eid}">`
          + `<p class="rdl-err" id="${eid}" role="alert"></p></div>` : "")
        + `<div class="rbtns n${btns.length === 1 ? 1 : btns.length === 2 ? 2 : 3}" role="group" aria-label="${esc(o.title || "Opciones")}">`
        + btns.map(b => `<button type="button" class="btn${b.ghost ? " ghost" : ""}${b.danger ? " rdel" : ""}" data-a="${esc(b.id)}">${esc(b.label)}</button>`).join("")
        + `</div></div>`;
      const inp = w.querySelector("input");
      const err = w.querySelector(".rdl-err");
      const me = { w, id };
      const focusables = () => [...w.querySelectorAll("input,button")].filter(e => !e.disabled && e.offsetParent !== null);
      const done = pick => {
        if (me.closed) return;
        me.closed = true;
        window.removeEventListener("keydown", key, true);
        const i = STACK.indexOf(me); if (i >= 0) STACK.splice(i, 1);
        const value = inp ? inp.value.trim() : null;
        w.remove();
        try { if (prev && prev.isConnected && prev.focus) prev.focus({ preventScroll: true }); } catch (e) { /* sin foco previo */ }
        res({ pick, value });
      };
      const accept = () => {
        if (kind === "prompt") {
          const v = inp.value.trim();
          const bad = !v ? (o.required === false ? null : o.emptyMsg || "Escribe un nombre.") : (typeof o.validate === "function" ? o.validate(v) : null);
          if (bad) { err.textContent = bad; inp.setAttribute("aria-invalid", "true"); inp.focus(); return; }
        }
        done(kind === "choose" ? String(btns[0].id) : "ok");
      };
      const key = e => {
        if (STACK[STACK.length - 1] !== me) return;            // solo el de hasta arriba
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(kind === "alert" ? "ok" : "cancel"); return; }
        if (e.key === "Tab") {
          const f = focusables(); if (!f.length) return;
          const i = f.indexOf(document.activeElement);
          e.preventDefault(); e.stopPropagation();
          f[(i < 0 ? 0 : i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
          return;
        }
        if (e.key === "Enter" && !e.isComposing) {
          const a = document.activeElement;
          if (a && a.tagName === "BUTTON" && w.contains(a)) { e.stopPropagation(); return; }   // el boton enfocado se activa solo
          e.preventDefault(); e.stopPropagation();
          if (a === inp || kind === "alert" || (!o.danger && kind === "confirm")) accept();
          return;
        }
        e.stopPropagation();          // escribir sigue igual; los atajos del editor no corren detras
      };
      w.addEventListener("click", e => {
        const b = e.target.closest("button[data-a]");
        if (b && w.contains(b)) { if (b.dataset.a === "ok") accept(); else done(b.dataset.a); return; }
        // clic fuera de la caja: cancela (salvo si se esta escribiendo un texto: no se pierde)
        if (e.target === w && kind !== "prompt") done(kind === "alert" ? "ok" : "cancel");
      });
      if (inp) inp.addEventListener("input", () => { if (err.textContent) { err.textContent = ""; inp.removeAttribute("aria-invalid"); } });
      window.addEventListener("keydown", key, true);
      STACK.push(me);
      document.body.appendChild(w);
      const want = o.focus != null ? [...w.querySelectorAll("button[data-a]")].find(b => b.dataset.a === String(o.focus)) : null;
      const first = kind === "prompt" ? inp : want
        || w.querySelector(o.danger || kind === "choose" ? (kind === "choose" ? "button[data-a]" : '[data-a="cancel"]') : '[data-a="ok"]');
      try { first.focus({ preventScroll: true }); if (first === inp) inp.select(); } catch (e) { /* sin foco */ }
    });
  };
  D.confirm = o => D.open(norm(o, "confirm")).then(r => r.pick === "ok");
  D.prompt = o => D.open(norm(o, "prompt")).then(r => (r.pick === "ok" ? r.value : null));
  D.alert = o => D.open(norm(o, "alert")).then(() => undefined);
  D.choose = o => D.open(norm(o, "choose")).then(r => (r.pick === "cancel" ? null : r.pick));
  D.isOpen = () => STACK.length > 0;
})(typeof window !== "undefined" ? window : this);
