// MatiPanel — tacómetros y gráficos de línea en SVG, sin dependencias.

const SVGNS = 'http://www.w3.org/2000/svg';

// ── Tacómetro ──
// gauge(el, {label}) → objeto con .set(valor 0-100, texto secundario)
// Arco de 270° (de 135° a 405°), marcas cada 10 %, aguja y arco de valor coloreado por nivel.
window.gauge = function (el, opts = {}) {
  const cx = 100, cy = 100, r = 78, start = 135, sweep = 270;
  const pt = (deg, rad) => {
    const a = deg * Math.PI / 180;
    return [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
  };
  const arc = (rad) => {
    const [x0, y0] = pt(start, rad), [x1, y1] = pt(start + sweep, rad);
    return `M ${x0} ${y0} A ${rad} ${rad} 0 1 1 ${x1} ${y1}`;
  };
  let ticks = '';
  for (let i = 0; i <= 50; i++) {
    const major = i % 5 === 0, deg = start + sweep * i / 50;
    const [x0, y0] = pt(deg, r - 13), [x1, y1] = pt(deg, r - (major ? 22 : 17));
    ticks += `<line class="g-tick${major ? ' major' : ''}" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"/>`;
  }
  const labels = [50].map(v => {
    const [x, y] = pt(start + sweep * v / 100, r - 33);
    return `<text class="g-scale" x="${x}" y="${y}">${v}</text>`;
  }).join('');
  const len = 2 * Math.PI * r * sweep / 360;
  el.innerHTML = `<svg viewBox="0 0 200 172" class="gauge-svg" role="img" aria-label="${escapeHtml(opts.label || '')}">
      <path class="g-track" d="${arc(r)}"/>
      <path class="g-value" d="${arc(r)}" stroke-dasharray="${len}" stroke-dashoffset="${len}"/>
      ${ticks}${labels}
      <g class="g-needle"><line x1="${cx}" y1="${cy}" x2="${cx + r - 26}" y2="${cy}"/><circle cx="${cx}" cy="${cy}" r="6"/></g>
      <text class="g-val" x="${cx}" y="${cy + 46}"><tspan class="g-num">—</tspan><tspan class="g-pct" dx="2">%</tspan></text>
      <text class="g-label" x="${cx}" y="${cy + 64}">${escapeHtml(opts.label || '')}</text>
    </svg>
    <div class="g-sub">—</div>`;
  const value = el.querySelector('.g-value'), needle = el.querySelector('.g-needle');
  const num = el.querySelector('.g-num'), sub = el.querySelector('.g-sub');
  needle.style.transform = `rotate(${start}deg)`;
  return {
    set(v, text) {
      v = Math.max(0, Math.min(100, Number(v) || 0));
      value.style.strokeDashoffset = len * (1 - v / 100);
      needle.style.transform = `rotate(${start + sweep * v / 100}deg)`;
      el.dataset.level = v >= (opts.err ?? 90) ? 'err' : v >= (opts.warn ?? 75) ? 'warn' : 'ok';
      num.textContent = Math.round(v);
      if (text !== undefined) sub.textContent = text;
    },
  };
};

// ── Gráfico de línea con ejes, cuadrícula y tooltip ──
// lineChart(el, {series: [{key, label, alt}], max: número | 'auto', min, fmt, height})
// .update(points, rangeSeconds)  — points: [{t, key1, key2…}] ordenados por t
window.lineChart = function (el, opts) {
  const H = opts.height || 150, padL = 62, padR = 8, padT = 8, padB = 20;
  const fmt = opts.fmt || (v => String(Math.round(v)));
  el.classList.add('chart');
  el.innerHTML = '<svg class="chart-svg"></svg><div class="chart-tip" hidden></div>';
  const svg = el.querySelector('svg'), tip = el.querySelector('.chart-tip');
  let pts = [], range = 900, geo = null;

  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  }

  function draw() {
    const W = Math.max(el.clientWidth, 200);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('height', H);
    const now = pts.length ? pts[pts.length - 1].t : Date.now() / 1000, t0 = now - range;
    const vis = pts.filter(p => p.t >= t0);
    let max = opts.max;
    if (max === 'auto' || max === undefined) {
      max = niceMax(Math.max(opts.min || 0, ...vis.flatMap(p => opts.series.map(s => p[s.key] || 0))) * 1.1);
    }
    const x = t => padL + (t - t0) / range * (W - padL - padR);
    const y = v => padT + (1 - Math.min(v / max, 1)) * (H - padT - padB);
    geo = { vis, x, y, W };

    let g = '';
    for (const f of [0, 0.5, 1]) {
      g += `<line class="c-grid" x1="${padL}" x2="${W - padR}" y1="${y(max * f)}" y2="${y(max * f)}"/>`
         + `<text class="c-ylab" x="${padL - 8}" y="${y(max * f) + 4}">${escapeHtml(fmt(max * f))}</text>`;
    }
    // Marcas de tiempo en minutos redondos
    const stepMin = [1, 2, 5, 10, 15, 30].find(m => range / 60 / m <= 6) || 30;
    for (let t = Math.ceil(t0 / (stepMin * 60)) * stepMin * 60; t <= now; t += stepMin * 60) {
      const lab = new Date(t * 1000).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' });
      g += `<line class="c-grid v" x1="${x(t)}" x2="${x(t)}" y1="${padT}" y2="${H - padB}"/>`
         + `<text class="c-xlab" x="${x(t)}" y="${H - 5}">${lab}</text>`;
    }
    opts.series.forEach((s, i) => {
      if (vis.length < 2) return;
      const line = vis.map(p => `${x(p.t).toFixed(1)},${y(p[s.key] || 0).toFixed(1)}`).join(' ');
      if (i === 0) g += `<polygon class="c-area" points="${x(vis[0].t).toFixed(1)},${y(0)} ${line} ${x(vis[vis.length - 1].t).toFixed(1)},${y(0)}"/>`;
      g += `<polyline class="c-line${s.alt ? ' alt' : ''}" points="${line}"/>`;
    });
    if (vis.length < 2) g += `<text class="c-empty" x="${(W + padL) / 2}" y="${H / 2}">Recolectando datos…</text>`;
    g += `<line class="c-cross" y1="${padT}" y2="${H - padB}" x1="0" x2="0" visibility="hidden"/>`;
    svg.innerHTML = g;
  }

  el.addEventListener('mousemove', e => {
    if (!geo || geo.vis.length < 2) return;
    const rect = svg.getBoundingClientRect(), mx = (e.clientX - rect.left) * geo.W / rect.width;
    let best = geo.vis[0];
    for (const p of geo.vis) if (Math.abs(geo.x(p.t) - mx) < Math.abs(geo.x(best.t) - mx)) best = p;
    const cx = geo.x(best.t), cross = svg.querySelector('.c-cross');
    cross.setAttribute('x1', cx); cross.setAttribute('x2', cx); cross.setAttribute('visibility', 'visible');
    tip.hidden = false;
    tip.innerHTML = `<div class="muted">${new Date(best.t * 1000).toLocaleTimeString('es')}</div>`
      + opts.series.map(s => `<div><i class="sw${s.alt ? ' alt' : ''}"></i>${escapeHtml(s.label)} <b>${escapeHtml(fmt(best[s.key] || 0))}</b></div>`).join('');
    const left = cx / geo.W * rect.width;
    tip.style.left = Math.min(left + 12, rect.width - tip.offsetWidth - 4) + 'px';
  });
  el.addEventListener('mouseleave', () => {
    tip.hidden = true;
    const cross = svg.querySelector('.c-cross');
    if (cross) cross.setAttribute('visibility', 'hidden');
  });
  new ResizeObserver(() => draw()).observe(el);

  return { update(points, rangeSeconds) { pts = points; range = rangeSeconds || range; draw(); } };
};

// Formatos compartidos
window.fmtRate = function (bps) {
  const u = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
  let i = 0;
  while (bps >= 1024 && i < u.length - 1) { bps /= 1024; i++; }
  return (bps < 10 && i ? bps.toFixed(1) : Math.round(bps)) + ' ' + u[i];
};
