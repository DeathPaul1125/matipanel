// MatiPanel — utilidades JS compartidas

// Escapa texto que viene del backend antes de meterlo en innerHTML
window.escapeHtml = function(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
};

// Argumento seguro para onclick="fn(${jsArg(x)})": literal JS + escape HTML
window.jsArg = function(x) {
  return escapeHtml(JSON.stringify(x));
};

// POST JSON; devuelve el JSON de respuesta (o {ok:false,error} si falla la red)
window.postJSON = async function(url, data) {
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data || {}),
    });
    return await r.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
};

// Menú móvil: cerrar al tocar el fondo, al elegir una sección o con Escape
document.addEventListener('click', e => {
  if (!document.body.classList.contains('nav-open')) return;
  if (e.target === document.body || e.target.closest('.nav-item')) document.body.classList.remove('nav-open');
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.body.classList.remove('nav-open');
});

// ── Diálogos y notificaciones (reemplazo de alert/confirm/prompt) ──
// ui.alert(texto, {title, icon})                      → Promise<void>
// ui.confirm(texto, {title, icon, danger, confirmText, cancelText, typed}) → Promise<bool>
//   typed: palabra que hay que escribir para habilitar el botón (acciones destructivas)
// ui.prompt(texto, {title, value, placeholder, type, confirmText, validate}) → Promise<string|null>
// ui.form({title, text, fields:[{name,label,type,value,options,placeholder,required,help}], confirmText, danger}) → Promise<obj|null>
// ui.toast(texto, tipo = 'success'|'error'|'info'|'warning', ms)
window.ui = (() => {
  const ICON = {
    info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    success: '<circle cx="12" cy="12" r="10"/><polyline points="8 12.5 11 15.5 16 9.5"/>',
    warning: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    error: '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
    question: '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  };
  const svg = (name, size = 22) =>
    `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[name] || ICON.info}</svg>`;

  function fieldHtml(f, i) {
    const id = 'dlg-f' + i, req = f.required ? ' required' : '';
    const ph = f.placeholder ? ` placeholder="${escapeHtml(f.placeholder)}"` : '';
    const help = f.help ? `<span class="dlg-help">${escapeHtml(f.help)}</span>` : '';
    if (f.type === 'checkbox') {
      return `<label class="dlg-check"><input type="checkbox" id="${id}" ${f.value ? 'checked' : ''}> ${escapeHtml(f.label || '')}</label>${help}`;
    }
    let input;
    if (f.type === 'select') {
      input = `<select class="input" id="${id}"${req}>` + (f.options || []).map(o => {
        const [v, l] = Array.isArray(o) ? o : [o, o];
        return `<option value="${escapeHtml(v)}" ${String(v) === String(f.value ?? '') ? 'selected' : ''}>${escapeHtml(l)}</option>`;
      }).join('') + '</select>';
    } else if (f.type === 'textarea') {
      input = `<textarea class="input" id="${id}" rows="${f.rows || 5}"${ph}${req}>${escapeHtml(f.value ?? '')}</textarea>`;
    } else {
      input = `<input class="input" id="${id}" type="${f.type || 'text'}" value="${escapeHtml(f.value ?? '')}"${ph}${req} autocomplete="off">`;
    }
    return `<label class="form-row"><span>${escapeHtml(f.label || '')}</span>${input}${help}</label>`;
  }

  function open(o) {
    return new Promise(resolve => {
      const d = document.createElement('dialog');
      d.className = 'dlg';
      const fields = o.fields || [];
      d.innerHTML = `<form method="dialog" novalidate>
        <div class="dlg-head">
          ${o.icon ? `<span class="dlg-icon ${o.icon}">${svg(o.icon)}</span>` : ''}
          <div class="dlg-titles">
            ${o.title ? `<h3 class="dlg-title">${escapeHtml(o.title)}</h3>` : ''}
            ${o.text ? `<div class="dlg-text">${escapeHtml(o.text)}</div>` : ''}
          </div>
        </div>
        ${fields.length ? `<div class="dlg-body">${fields.map(fieldHtml).join('')}</div>` : ''}
        ${o.typed ? `<label class="form-row dlg-body"><span>Escribe <b class="mono">${escapeHtml(o.typed)}</b> para confirmar</span><input class="input" id="dlg-typed" autocomplete="off"></label>` : ''}
        <div class="dlg-error" hidden></div>
        <div class="dlg-actions">
          ${o.cancelText !== false ? `<button type="button" class="btn" value="cancel">${escapeHtml(o.cancelText || 'Cancelar')}</button>` : ''}
          <button type="submit" class="btn ${o.danger ? 'btn-danger-solid' : 'btn-primary'}" value="ok">${escapeHtml(o.confirmText || 'Aceptar')}</button>
        </div>
      </form>`;
      document.body.appendChild(d);
      const form = d.querySelector('form'), okBtn = d.querySelector('button[value=ok]');
      const err = d.querySelector('.dlg-error');
      let result = o.cancelValue ?? null;

      const typed = d.querySelector('#dlg-typed');
      if (typed) {
        okBtn.disabled = true;
        typed.addEventListener('input', () => { okBtn.disabled = typed.value !== o.typed; });
      }
      d.querySelector('button[value=cancel]')?.addEventListener('click', () => d.close());
      form.addEventListener('submit', e => {
        e.preventDefault();
        if (okBtn.disabled) return;
        let value = true;
        if (fields.length) {
          value = {};
          for (const [i, f] of fields.entries()) {
            const el = d.querySelector('#dlg-f' + i);
            const v = f.type === 'checkbox' ? el.checked : el.value;
            if (f.required && f.type !== 'checkbox' && !String(v).trim()) {
              err.textContent = `Completa "${f.label || f.name}".`; err.hidden = false; el.focus(); return;
            }
            value[f.name || i] = v;
          }
          const msg = o.validate && o.validate(value);
          if (msg) { err.textContent = msg; err.hidden = false; return; }
        }
        result = o.map ? o.map(value) : value;
        d.close();
      });
      d.addEventListener('close', () => { d.remove(); resolve(result); });
      d.showModal();
      (d.querySelector('.dlg-body input, .dlg-body select, .dlg-body textarea, #dlg-typed') || okBtn).focus();
    });
  }

  const alert = (text, o = {}) => open({ icon: 'info', ...o, text, cancelText: false, confirmText: o.confirmText || 'Entendido' }).then(() => {});
  const confirm = (text, o = {}) => open({ icon: o.danger ? 'warning' : 'question', ...o, text, cancelValue: false }).then(Boolean);
  const prompt = (text, o = {}) => open({
    ...o, text, fields: [{ name: 'v', label: o.label || '', type: o.type, value: o.value, placeholder: o.placeholder, required: true }],
    validate: o.validate ? v => o.validate(v.v) : null, map: v => v.v,
  });
  const form = o => open(o);

  function toast(text, type = 'success', ms = 3500) {
    let stack = document.querySelector('.toast-stack');
    if (!stack) { stack = document.createElement('div'); stack.className = 'toast-stack'; document.body.appendChild(stack); }
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.setAttribute('role', type === 'error' ? 'alert' : 'status');
    t.innerHTML = `<span class="dlg-icon ${type}">${svg(type, 18)}</span><span>${escapeHtml(text)}</span>`;
    t.onclick = () => t.remove();
    stack.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 200); }, ms);
  }

  // Red de seguridad: cualquier alert() que quede usa el diálogo del panel
  window.alert = msg => { alert(String(msg), { icon: /^error/i.test(String(msg)) ? 'error' : 'info' }); };

  return { alert, confirm, prompt, form, toast };
})();
