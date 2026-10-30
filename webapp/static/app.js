/* CS2 Vetomalli - käyttöliittymälogiikka (HTMX täydentää palvelimen kanssa). */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  function css(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

  /* ---------- Zoomauksen esto (iOS Safari ohittaa osan meta-asetuksista) ---------- */
  ['gesturestart', 'gesturechange', 'gestureend'].forEach(function (ev) {
    document.addEventListener(ev, function (e) { e.preventDefault(); }, { passive: false });
  });

  /* ---------- Teema ---------- */
  var themeBtn = $('#themebtn');
  if (themeBtn) themeBtn.addEventListener('click', function () {
    var root = document.documentElement, cur = root.getAttribute('data-theme');
    if (!cur) cur = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    var next = cur === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next); store('theme', next);
    renderCharts(document);
  });

  /* ---------- Toast ---------- */
  function toast(d) {
    var box = $('#toasts'); if (!box) return;
    var el = document.createElement('div');
    el.className = 'toast' + (d.kind === 'err' ? ' err' : '');
    var span = document.createElement('span'); span.textContent = d.msg; el.appendChild(span);
    if (d.undo) {
      var b = document.createElement('button'); b.type = 'button'; b.textContent = 'Kumoa';
      b.addEventListener('click', function () {
        el.remove();
        htmx.ajax('POST', d.undo.split('?')[0], { values: { status: 'Odottaa' }, swap: 'none' });
      });
      el.appendChild(b);
    }
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, d.undo ? 7000 : 3200);
  }
  document.body.addEventListener('toast', function (e) { toast(e.detail); });
  document.body.addEventListener('htmx:responseError', function () { toast({ msg: 'Palvelinvirhe - yritä uudelleen.', kind: 'err' }); });
  document.body.addEventListener('htmx:sendError', function () { toast({ msg: 'Ei yhteyttä palvelimeen.', kind: 'err' }); });

  /* ---------- Sheet ---------- */
  var sheet = $('#sheet'), sheetBody = $('#sheet-body');
  function openSheet() { if (sheet && !sheet.open) sheet.showModal(); }
  function closeSheet() { if (sheet && sheet.open) sheet.close(); }
  if (sheet) {
    sheet.addEventListener('click', function (e) { if (e.target === sheet) closeSheet(); });
    sheet.addEventListener('close', function () {
      destroyCharts(sheetBody); sheetBody.innerHTML = '';
    });
  }
  document.body.addEventListener('closeSheet', closeSheet);
  document.body.addEventListener('clearNewTournament', function () { var i = $('#newt-name'); if (i) i.value = ''; });
  document.body.addEventListener('htmx:afterSwap', function (e) {
    var t = e.detail.target;
    if (t && t.id === 'sheet-body') {
      openSheet();
      var st = $('#s-stake'), tn = $('#s-tournament');
      if (st && store('stake')) st.value = store('stake');
      if (tn && store('tournament')) { var v = store('tournament'); $$('option', tn).forEach(function (o) { if (o.value === v) tn.value = v; }); }
      if (st) st.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (t && t.id === 'bet-list') updateCounts();
  });
  document.addEventListener('click', function (e) {
    var c = e.target.closest('[data-close]'); if (c) closeSheet();
  });
  document.addEventListener('change', function (e) {
    if (e.target.id === 's-stake') store('stake', e.target.value);
    if (e.target.id === 's-tournament') store('tournament', e.target.value);
  });

  /* ---------- Pienet apurit ---------- */
  document.addEventListener('click', function (e) {
    var s = e.target.closest('[data-step]');
    if (s) {
      var inp = document.getElementById(s.dataset.target); if (!inp) return;
      var v = (parseFloat(inp.value) || 0) + parseFloat(s.dataset.step);
      inp.value = Math.max(0, Math.round(v * 100) / 100);
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    var f = e.target.closest('[data-fill]');
    if (f) {
      var d = JSON.parse(f.dataset.fill);
      function set(id, v) { var el = document.getElementById(id); if (el && v !== null && v !== undefined) el.value = v; }
      set('f-team1', d.t1); set('f-team2', d.t2); set('f-odds1', d.odds1); set('f-odds2', d.odds2);
      set('f-total', d.total || 0); set('f-over', d.over || 0); set('f-under', d.under || 0);
      var m = (d.map || '').match(/(\d)/); if (m) { var r = document.getElementById('k' + m[1]); if (r) r.checked = true; }
      set('f-mapname', '');
      var fold = $('#pin-fold'); if (fold) fold.open = false;
      toast({ msg: 'Ladattu: ' + d.t1 + ' vs ' + d.t2 + (d.map ? ' · ' + d.map : '') });
      return;
    }
    var v = e.target.closest('[data-view]');
    if (v) {
      $$('[data-view]').forEach(function (b) { b.classList.toggle('on', b === v); });
      $('#bets-view').value = v.dataset.view;
      document.body.dispatchEvent(new Event('viewchange'));
      return;
    }
    var tb = e.target.closest('[data-tab]');
    if (tb) {
      $$('[data-tab]').forEach(function (b) { b.classList.toggle('on', b === tb); });
      var c = $('#stats-content'); c.setAttribute('hx-get', '/stats/' + tb.dataset.tab);
      htmx.process(c); htmx.trigger(c, 'refresh');
    }
  });
  var swap = $('#swap-teams');
  if (swap) swap.addEventListener('click', function () {
    ['team', 'odds'].forEach(function (k) {
      var a = $('#f-' + k + '1'), b = $('#f-' + k + '2'), t = a.value; a.value = b.value; b.value = t;
    });
  });
  function updateCounts() {
    var c = $('#counts-oob'); if (!c) return;
    var a = $('#cnt-active'), s = $('#cnt-settled');
    if (a) a.textContent = c.dataset.active; if (s) s.textContent = c.dataset.settled;
  }

  /* ---------- Graafit ---------- */
  var registry = new Map();
  function destroyCharts(root) {
    $$('canvas[data-chart]', root).forEach(function (cv) { var c = registry.get(cv); if (c) { c.destroy(); registry.delete(cv); } });
  }
  function eur0(v) { return (v < 0 ? '−' : '') + Math.abs(Math.round(v)).toLocaleString('fi-FI') + ' €'; }
  function eur2(v) { return (v < 0 ? '−' : v > 0 ? '+' : '') + Math.abs(v).toFixed(2).replace('.', ',') + ' €'; }
  function fpct(v, d) { return v.toFixed(d === undefined ? 1 : d).replace('.', ',') + ' %'; }

  function theme() {
    return { accent: css('--accent'), over: css('--over'), under: css('--under'), pos: css('--pos-bar'), neg: css('--neg-bar'),
      line: css('--line'), lineStrong: css('--line-strong'), text: css('--text'), text2: css('--text-2'), muted: css('--muted'),
      surface: css('--surface'), wash: css('--accent-wash') };
  }
  function tooltip(c) {
    return { backgroundColor: c.surface, titleColor: c.text2, bodyColor: c.text, borderColor: c.lineStrong, borderWidth: 1,
      padding: 10, cornerRadius: 10, boxWidth: 12, boxHeight: 2, boxPadding: 4, usePointStyle: false,
      titleFont: { weight: '500', size: 12 }, bodyFont: { weight: '600', size: 13 }, displayColors: true };
  }
  function crosshair(c) {
    return { id: 'crosshair', afterDatasetsDraw: function (chart) {
      var a = chart.tooltip && chart.tooltip._active;
      if (!a || !a.length) return;
      var x = a[0].element.x, area = chart.chartArea, ctx = chart.ctx;
      ctx.save(); ctx.beginPath(); ctx.moveTo(x, area.top); ctx.lineTo(x, area.bottom);
      ctx.lineWidth = 1; ctx.strokeStyle = c.lineStrong; ctx.stroke(); ctx.restore();
    } };
  }
  function baseOpts(c, extra) {
    var o = { responsive: true, maintainAspectRatio: false, animation: { duration: 220 },
      interaction: { mode: 'index', intersect: false }, layout: { padding: { top: 4, right: 4 } },
      plugins: { legend: { display: false }, tooltip: tooltip(c) } };
    return Object.assign(o, extra || {});
  }
  function xAxis(c, limit) {
    return { grid: { display: false }, border: { color: c.line }, ticks: { color: c.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: limit || 5 } };
  }
  function yAxis(c, cb) {
    return { grid: { color: function (ctx) { return ctx.tick.value === 0 ? c.lineStrong : c.line; }, lineWidth: 1 }, border: { display: false },
      ticks: { color: c.muted, callback: cb, maxTicksLimit: 5 } };
  }

  var builders = {
    equity: function (cv, d, c) {
      return new Chart(cv, { type: 'line', plugins: [crosshair(c)], options: baseOpts(c, {
        plugins: { legend: { display: false }, tooltip: Object.assign(tooltip(c), { callbacks: {
          title: function (it) { return d.dates[it[0].dataIndex]; },
          label: function (it) { return eur2(it.parsed.y); } }, displayColors: false }) },
        scales: { x: xAxis(c, 5), y: yAxis(c, eur0) } }),
        data: { labels: d.labels, datasets: [{ data: d.values, borderColor: c.accent, borderWidth: 2, tension: 0, pointRadius: 0,
          pointHoverRadius: 5, pointHoverBackgroundColor: c.accent, pointHoverBorderColor: c.surface, pointHoverBorderWidth: 2,
          fill: 'origin', backgroundColor: c.wash }] } });
    },
    daily: function (cv, d, c) {
      return new Chart(cv, { type: 'bar', options: baseOpts(c, {
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: Object.assign(tooltip(c), { displayColors: false, callbacks: {
          title: function (it) { return d.days[it[0].dataIndex]; },
          label: function (it) { return eur2(it.parsed.y); },
          afterLabel: function (it) { var i = it.dataIndex; return d.n[i] + ' vetoa · ROI ' + (d.roi[i] > 0 ? '+' : '') + fpct(d.roi[i]); } } }) },
        scales: { x: Object.assign(xAxis(c, 6), { ticks: { color: c.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 6,
          callback: function (v) { return d.days[v].slice(5); } } }), y: yAxis(c, eur0) } }),
        data: { labels: d.days, datasets: [{ data: d.values, maxBarThickness: 18, borderRadius: 4, borderSkipped: 'start',
          backgroundColor: d.values.map(function (v) { return v >= 0 ? c.pos : c.neg; }) }] } });
    },
    rolling: function (cv, d, c) {
      function ds(label, data, color) { return { label: label, data: data, borderColor: color, borderWidth: 2, tension: 0, pointRadius: 0,
        pointHoverRadius: 5, pointHoverBackgroundColor: color, pointHoverBorderColor: c.surface, pointHoverBorderWidth: 2 }; }
      return new Chart(cv, { type: 'line', plugins: [crosshair(c)], options: baseOpts(c, {
        plugins: { legend: { display: false }, tooltip: Object.assign(tooltip(c), { callbacks: {
          label: function (it) { return it.dataset.label + ': ' + fpct(it.parsed.y); } }, boxHeight: 2 }) },
        scales: { x: xAxis(c, 5), y: yAxis(c, function (v) { return v + ' %'; }) } }),
        data: { labels: d.labels, datasets: [ds('Toteutunut osuma', d.hit, c.accent), ds('Kertoimen vaatima', d.breakeven, c.over)] } });
    },
    hist: function (cv, d, c) {
      var hi = d.dir === 'OVER' ? 'over' : 'under', hiColor = d.dir === 'OVER' ? c.over : c.under;
      var marker = { id: 'linemark', afterDatasetsDraw: function (chart) {
        var xs = chart.scales.x, ctx = chart.ctx, area = chart.chartArea, i0 = -1;
        d.x.forEach(function (x, i) { if (x < d.line) i0 = i; });
        if (i0 < 0 || i0 >= d.x.length - 1) return;
        var px = (xs.getPixelForValue(i0) + xs.getPixelForValue(i0 + 1)) / 2;
        ctx.save(); ctx.strokeStyle = c.text; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(px, area.top); ctx.lineTo(px, area.bottom); ctx.stroke();
        ctx.fillStyle = c.text; ctx.font = '600 11px sans-serif'; ctx.textAlign = px > area.right - 50 ? 'right' : 'left';
        ctx.fillText('Linja ' + String(d.line).replace('.', ','), px + (ctx.textAlign === 'left' ? 5 : -5), area.top + 10); ctx.restore(); } };
      return new Chart(cv, { type: 'bar', plugins: [marker], options: baseOpts(c, {
        plugins: { legend: { display: false }, tooltip: Object.assign(tooltip(c), { displayColors: false, callbacks: {
          title: function (it) { return d.x[it[0].dataIndex] + ' tappoa'; },
          label: function (it) { return fpct(it.parsed.y, 1); } } }) },
        scales: { x: Object.assign(xAxis(c, 9), { ticks: { color: c.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 9 } }),
          y: Object.assign(yAxis(c, function (v) { return v + ' %'; }), { beginAtZero: true }) } }),
        data: { labels: d.x, datasets: [{ data: d.p, borderRadius: 3, borderSkipped: 'bottom', categoryPercentage: 1, barPercentage: 0.9,
          backgroundColor: d.side.map(function (s) { return s === hi ? hiColor : c.muted; }) }] } });
    },
    calib: function (cv, d, c) {
      var all = []; d.rows.forEach(function (r) { all.push(r.lo, r.hi, r.pred); });
      var lo = Math.floor(Math.min.apply(null, all) / 5) * 5 - 5, hi = Math.ceil(Math.max.apply(null, all) / 5) * 5 + 5;
      lo = Math.max(lo, 0); hi = Math.min(hi, 100);
      var err = { id: 'errbars', afterDatasetsDraw: function (chart) {
        var ds = chart.data.datasets[1], meta = chart.getDatasetMeta(1), ys = chart.scales.y, ctx = chart.ctx;
        ctx.save(); ctx.strokeStyle = c.accent; ctx.globalAlpha = .55; ctx.lineWidth = 2; ctx.lineCap = 'round';
        meta.data.forEach(function (pt, i) { var r = ds.data[i]; ctx.beginPath(); ctx.moveTo(pt.x, ys.getPixelForValue(r.lo)); ctx.lineTo(pt.x, ys.getPixelForValue(r.hi)); ctx.stroke(); });
        ctx.restore(); } };
      return new Chart(cv, { data: { datasets: [
          { type: 'line', label: 'Täydellinen', data: [{ x: lo, y: lo }, { x: hi, y: hi }], borderColor: c.lineStrong, borderWidth: 1.5, pointRadius: 0, order: 2 },
          { type: 'scatter', label: 'Vetoluokka', order: 1, data: d.rows.map(function (r) { return { x: r.pred, y: r.actual, n: r.n, lo: r.lo, hi: r.hi, bin: r.bin }; }),
            pointRadius: d.rows.map(function (r) { return Math.min(14, 5 + Math.sqrt(r.n) / 2.2); }), pointHitRadius: 20,
            backgroundColor: c.accent, borderColor: c.surface, borderWidth: 2, pointHoverBorderWidth: 2 }] },
        plugins: [err], options: baseOpts(c, {
        interaction: { mode: 'nearest', intersect: false },
        plugins: { legend: { display: false }, tooltip: Object.assign(tooltip(c), { filter: function (it) { return it.datasetIndex === 1; }, displayColors: false, callbacks: {
          title: function (it) { return 'Luokka ' + it[0].raw.bin + ' · ' + it[0].raw.n + ' vetoa'; },
          label: function (it) { var r = it.raw; return ['Malli ' + fpct(r.x), 'Toteuma ' + fpct(r.y), 'Väli ' + fpct(r.lo, 0) + '–' + fpct(r.hi, 0)]; } } }) },
        scales: {
          x: { type: 'linear', min: lo, max: hi, grid: { color: c.line }, border: { color: c.line }, title: { display: true, text: 'Mallin arvio', color: c.muted },
            ticks: { color: c.muted, callback: function (v) { return v + ' %'; }, maxTicksLimit: 6 } },
          y: { type: 'linear', min: lo, max: hi, grid: { color: c.line }, border: { display: false }, title: { display: true, text: 'Toteutunut', color: c.muted },
            ticks: { color: c.muted, callback: function (v) { return v + ' %'; }, maxTicksLimit: 6 } } } }) });
    }
  };

  function renderCharts(root) {
    if (typeof Chart === 'undefined') return;
    var c = theme();
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;
    $$('canvas[data-chart]', root).forEach(function (cv) {
      var old = registry.get(cv); if (old) { old.destroy(); registry.delete(cv); }
      var b = builders[cv.dataset.chart]; if (!b) return;
      try { registry.set(cv, b(cv, JSON.parse(cv.dataset.payload), c)); } catch (err) { console.error('Graafivirhe', cv.dataset.chart, err); }
    });
  }
  window.renderCharts = renderCharts;
  document.body.addEventListener('htmx:load', function (e) { renderCharts(e.detail.elt); });
  window.addEventListener('load', function () { renderCharts(document); });
})();
