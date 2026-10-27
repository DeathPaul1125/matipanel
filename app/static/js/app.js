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

// ── Terminal de acciones: salida en vivo de lo que hace el servidor al pulsar un botón ──
// Cada POST del panel lleva X-Job-Id; si tarda más de un instante se abre una ventana tipo
// terminal que muestra, en tiempo real, los comandos y su salida (apt-get, systemctl, docker…).
(() => {
  const DELAY = 700;
  const ANSI = /\x1b\[[0-9;?]*[A-Za-z]/g;
  let box = null, active = null;

  function ensureBox() {
    if (box) return box;
    box = document.createElement('div');
    box.className = 'modal jobterm';
    box.innerHTML = `<div class="modal-box wide jt-box" role="dialog" aria-label="Salida de la acción">
        <div class="jt-head">
          <span class="led sm live warn" data-jt="led"></span>
          <b class="jt-title" data-jt="title"></b>
          <span class="jt-status" data-jt="status">Ejecutando…</span>
          <button class="btn btn-sm" type="button" data-jt="close">Cerrar</button>
        </div>
        <pre class="jt-out" data-jt="out"></pre>
        <div class="jt-foot small" data-jt="foot">Puedes cerrar esta ventana: la acción sigue en el servidor.</div>
      </div>`;
    document.body.appendChild(box);
    box.querySelector('[data-jt=close]').onclick = () => { box.classList.remove('open'); if (active) active.visible = false; };
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && box.classList.contains('open') && !document.querySelector('dialog[open]')) box.querySelector('[data-jt=close]').click(); });
    return box;
  }
  const el = k => ensureBox().querySelector(`[data-jt=${k}]`);

  function append(text) {
    const out = el('out'), atBottom = out.scrollHeight - out.scrollTop - out.clientHeight < 40;
    out.textContent += text.replace(ANSI, '').replace(/\r(?!\n)/g, '\n');
    if (atBottom) out.scrollTop = out.scrollHeight;
  }

  async function poll(job) {
    while (job.polling) {
      try {
        const d = await (await nativeFetch('/jobs/' + job.id + '?offset=' + job.offset)).json();
        if (d.text) { if (job === active) append(d.text); job.offset = d.offset; }
        if (job.finished && (!d.text || d.done)) break;
      } catch (e) { /* reintenta */ }
      await new Promise(r => setTimeout(r, job.finished ? 150 : 400));
    }
    job.polling = false;
  }

  function open(job) {
    ensureBox();
    active = job; job.visible = true;
    el('title').textContent = job.title;
    el('status').textContent = 'Ejecutando…';
    el('led').className = 'led sm live warn';
    el('out').textContent = '';
    el('foot').hidden = false;
    box.classList.add('open');
    job.polling = true;
    poll(job);
  }

  function finish(job, ok, errorText) {
    job.finished = true;
    if (!job.visible || job !== active) return;
    const settle = () => {
      if (job.polling) return setTimeout(settle, 120);
      if (!el('out').textContent.trim()) append(ok ? 'La acción terminó sin salida en pantalla.\n' : '');
      if (!ok && errorText) append('\n' + errorText + '\n');
      el('status').textContent = ok ? 'Completado' : 'Falló';
      el('led').className = 'led sm ' + (ok ? '' : 'err');
      el('foot').hidden = true;
    };
    settle();
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async function (input, init = {}) {
    const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    if (method === 'GET' || url.origin !== location.origin || url.pathname.startsWith('/jobs/')) return nativeFetch(input, init);
    const id = (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(16) + Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9-]/g, '');
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    if (headers.has('X-Job-Id')) return nativeFetch(input, init);
    headers.set('X-Job-Id', id);
    const btn = document.activeElement && document.activeElement.closest('button, a, .btn');
    const title = (btn && btn.textContent.trim().replace(/\s+/g, ' ').slice(0, 60)) || 'Acción en el servidor';
    const job = { id, title, offset: 0, polling: false, finished: false, visible: false };
    const timer = setTimeout(() => open(job), DELAY);
    try {
      const resp = await nativeFetch(input, { ...init, headers });
      clearTimeout(timer);
      let ok = resp.ok, err = '';
      if (job.visible) {
        try { const body = await resp.clone().json(); if (body && body.ok === false) { ok = false; err = body.error || ''; } } catch (e) {}
        if (!resp.ok && !err) err = 'HTTP ' + resp.status;
      }
      finish(job, ok, err);
      return resp;
    } catch (e) {
      clearTimeout(timer);
      finish(job, false, String(e));
      throw e;
    }
  };
})();

// ── Tablas: filtro, orden por columna y paginador ──────────────────
// <table class="lx" data-table>                → filtro + orden + paginador (10 por página)
// <table data-table="50" data-table-filter="off"> → otro tamaño; sin filtro (la página ya tiene el suyo)
// <th data-nosort> no ordena; <td data-sort="123"> fija el valor con que se ordena esa celda.
// Funciona aunque la página vuelva a pintar el <tbody> (refresco automático): se re-aplica sola.
window.enhanceTable = (() => {
  const SIZES = [10, 25, 50, 100, 0];  // 0 = todos
  const UNITS = { b: 1, k: 1e3, m: 1e6, g: 1e9, t: 1e12, p: 1e15 };
  const norm = s => String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const isMsg = r => r.cells.length === 1 && r.cells[0].colSpan > 1;  // "Cargando…", "Sin datos"

  function sortKey(td) {
    if (!td) return '';
    const raw = (td.dataset.sort ?? td.textContent).trim();
    let m;
    if (/^-?\d+([.,]\d+)?\s*%?$/.test(raw)) return parseFloat(raw.replace(',', '.'));
    if ((m = raw.match(/^([\d.,]+)\s*([KMGTP]?)i?B$/i))) return parseFloat(m[1].replace(',', '.')) * UNITS[(m[2] || 'b').toLowerCase()];
    if (/^(\d+\s*(d|h|min|s)\b\s*)+$/.test(raw)) {
      const mult = { d: 86400, h: 3600, min: 60, s: 1 };
      return [...raw.matchAll(/(\d+)\s*(d|h|min|s)\b/g)].reduce((t, x) => t + x[1] * mult[x[2]], 0);
    }
    if (/^\d{4}-\d{2}-\d{2}/.test(raw) && !isNaN(Date.parse(raw))) return Date.parse(raw);
    return norm(raw);
  }
  const cmp = (a, b) => (typeof a === 'number' && typeof b === 'number') ? a - b
    : String(a).localeCompare(String(b), 'es', { numeric: true, sensitivity: 'base' });

  function enhance(t) {
    if (t._tbl || !t.tHead || !t.tBodies[0]) return;
    const key = 'mp-tbl:' + location.pathname + ':' + (t.id || [...document.querySelectorAll('table[data-table]')].indexOf(t));
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(key)) || {}; } catch (e) {}
    const def = parseInt(t.dataset.table, 10);
    const st = t._tbl = { key, q: '', page: 1, size: SIZES.includes(saved.size) ? saved.size : (SIZES.includes(def) ? def : 10),
                          sort: saved.sort || null };
    const save = () => { try { localStorage.setItem(key, JSON.stringify({ size: st.size, sort: st.sort })); } catch (e) {} };

    if (t.dataset.tableFilter !== 'off') {
      const tools = document.createElement('div');
      tools.className = 'tbl-tools';
      tools.innerHTML = '<input type="search" class="input" placeholder="Filtrar…" aria-label="Filtrar la tabla">';
      t.before(tools);
      let timer;
      tools.querySelector('input').addEventListener('input', e => {
        clearTimeout(timer);
        timer = setTimeout(() => { st.q = e.target.value; st.page = 1; apply(t); }, 120);
      });
    }

    [...t.tHead.rows[0].cells].forEach((th, i) => {
      const label = th.textContent.trim();
      if (th.hasAttribute('data-nosort') || !label || /^acciones$/i.test(label)) return;
      th.classList.add('tbl-sortable');
      th.tabIndex = 0;
      const toggle = () => {
        st.sort = !st.sort || st.sort.col !== i ? { col: i, dir: 1 } : st.sort.dir === 1 ? { col: i, dir: -1 } : null;
        st.page = 1; save(); apply(t);
      };
      th.addEventListener('click', toggle);
      th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    });

    const pager = st.pager = document.createElement('div');
    pager.className = 'tbl-pager';
    t.after(pager);
    pager.addEventListener('click', e => {
      const b = e.target.closest('[data-pg]');
      if (b && !b.disabled) { st.page = +b.dataset.pg; apply(t); }
    });
    pager.addEventListener('change', e => {
      if (e.target.matches('select')) { st.size = +e.target.value; st.page = 1; save(); apply(t); }
    });

    let raf = 0;
    st.obs = new MutationObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => apply(t)); });
    apply(t);
  }

  function pagesHtml(page, pages) {
    const nums = new Set([1, pages, page - 1, page, page + 1].filter(n => n >= 1 && n <= pages));
    const list = [...nums].sort((a, b) => a - b);
    let out = `<button class="btn btn-sm" data-pg="${page - 1}" ${page <= 1 ? 'disabled' : ''} aria-label="Anterior">‹</button>`;
    list.forEach((n, i) => {
      if (i && n - list[i - 1] > 1) out += '<span class="muted">…</span>';
      out += `<button class="btn btn-sm${n === page ? ' btn-primary' : ''}" data-pg="${n}">${n}</button>`;
    });
    return out + `<button class="btn btn-sm" data-pg="${page + 1}" ${page >= pages ? 'disabled' : ''} aria-label="Siguiente">›</button>`;
  }

  function apply(t) {
    const st = t._tbl, tb = t.tBodies[0];
    st.obs.disconnect();
    const rows = [...tb.rows], data = rows.filter(r => !isMsg(r));
    if (st.sort && data.length > 1) {
      const sorted = data.map((r, i) => [sortKey(r.cells[st.sort.col]), i, r])
        .sort((a, b) => cmp(a[0], b[0]) * st.sort.dir || a[1] - b[1]).map(x => x[2]);
      if (sorted.some((r, i) => r !== data[i])) tb.append(...sorted, ...rows.filter(isMsg));
      data.splice(0, data.length, ...sorted);
    }
    const q = norm(st.q.trim());
    const match = q ? data.filter(r => norm(r.textContent).includes(q)) : data;
    const size = st.size || match.length || 1, pages = Math.max(1, Math.ceil(match.length / size));
    st.page = Math.min(Math.max(1, st.page), pages);
    const from = (st.page - 1) * size, shown = new Set(match.slice(from, from + size));
    data.forEach(r => { r.hidden = !shown.has(r); });

    [...t.tHead.rows[0].cells].forEach((th, i) => {
      if (!th.classList.contains('tbl-sortable')) return;
      const dir = st.sort && st.sort.col === i ? st.sort.dir : 0;
      th.setAttribute('aria-sort', dir === 1 ? 'ascending' : dir === -1 ? 'descending' : 'none');
      th.dataset.dir = dir === 1 ? '▲' : dir === -1 ? '▼' : '';
    });

    if (q && !match.length && data.length) {
      st.pager.hidden = false;
      st.pager.innerHTML = `<span class="small muted">Ningún resultado para «${escapeHtml(st.q.trim())}» de ${data.length}</span>`;
    } else if (data.length > SIZES[0] || q) {
      st.pager.hidden = false;
      const opts = SIZES.map(n => `<option value="${n}" ${n === st.size ? 'selected' : ''}>${n || 'Todos'}</option>`).join('');
      st.pager.innerHTML = `<label class="small muted">Mostrar <select class="input">${opts}</select></label>
        <span class="small muted">${match.length ? from + 1 : 0}–${Math.min(from + size, match.length)} de ${match.length}${q ? ` (filtrado de ${data.length})` : ''}</span>
        ${pages > 1 ? `<div class="tbl-pages">${pagesHtml(st.page, pages)}</div>` : ''}`;
    } else {
      st.pager.hidden = true;
    }
    st.obs.observe(t, { childList: true, subtree: true });
  }

  const init = () => document.querySelectorAll('table[data-table]').forEach(enhance);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
  return enhance;
})();
