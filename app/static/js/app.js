// MatiPanel — utilidades JS compartidas

// Helper para escapar HTML que viene del backend
window.escapeHtml = function(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
};

// Confirmación con un pequeño retardo para evitar tap accidental
window.confirmAction = function(msg) {
  return window.confirm(msg);
};
