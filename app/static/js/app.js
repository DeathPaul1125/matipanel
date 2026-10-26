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
