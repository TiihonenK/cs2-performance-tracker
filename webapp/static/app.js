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
      var tn = $('#s-tournament');
      if (tn && store('tournament')) { var v = store('tournament'); $$('option', tn).forEach(function (o) { if (o.value === v) tn.value = v; }); }
      if (tn) tn.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (t && t.id === 'bet-list') updateCounts();
  });
  document.addEventListener('click', function (e) {
    var c = e.target.closest('[data-close]'); if (c) closeSheet();
  });
  document.addEventListener('change', function (e) {
    if (e.target.id === 's-tournament') store('tournament', e.target.value);
  });

  /* ---------- Pienet apurit ---------- */
  document.addEventListener('click', function (e) {
    var ad = e.target.closest('[data-add]');
    if (ad) {   /* panoksen pikanapit: lisäävät nykyiseen panokseen (1 + 1 = 2) */
      var si = document.getElementById(ad.dataset.target); if (!si) return;
      si.value = Math.round(((parseFloat(si.value) || 0) + parseFloat(ad.dataset.add)) * 100) / 100;
      si.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    var s = e.target.closest('[data-step]');
    if (s) {
      var inp = document.getElementById(s.dataset.target); if (!inp) return;
      var v = (parseFloat(inp.value) || 0) + parseFloat(s.dataset.step);
      inp.value = Math.max(0, Math.round(v * 100) / 100);
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    var ac = e.target.closest('[data-alt]');
    if (ac) { altApply(ac); return; }
    var pm = e.target.closest('[data-match]');
    if (pm) { var md = null; try { md = JSON.parse(pm.dataset.match); } catch (err) { md = null; } if (md) pinSelect(md); return; }
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
  /* Pelaajasheetin Epicbet-linjat: linja + kertoimet kerralla, suunta vaihtaa kertoimet */
  var altSel = null;
  function setField(id, v) { var el = document.getElementById(id); if (el) el.value = v; }
  function altOdds() {   // kertoimet valitun suunnan mukaan
    if (!altSel) return;
    var dir = ($('#betform input[name="direction"]:checked') || {}).value || 'OVER';
    var own = dir === 'OVER' ? altSel.over : altSel.under, opp = dir === 'OVER' ? altSel.under : altSel.over;
    setField('s-odds', own || ''); setField('s-opp', opp || 0);
  }
  function altApply(chip) {
    var line = chip.dataset.line, over = parseFloat(chip.dataset.over) || 0, under = parseFloat(chip.dataset.under) || 0;
    $$('#alt-chips .altchip').forEach(function (c) { c.classList.toggle('on', c === chip); });
    var dir = chip.dataset.dir;
    if (dir) { var r = document.getElementById(dir === 'UNDER' ? 'd-under' : 'd-over'); if (r) r.checked = true; }
    altSel = { line: line, over: over, under: under };
    setField('s-line', line); altOdds();
    var l = document.getElementById('s-line'); if (l) l.dispatchEvent(new Event('change', { bubbles: true }));
  }
  document.addEventListener('change', function (e) {
    if (e.target.name === 'direction' && e.target.closest('#betform') && altSel) { altOdds(); }
  });
  document.addEventListener('input', function (e) {
    if (e.target.id === 's-line' && altSel && String(parseFloat(e.target.value)) !== String(parseFloat(altSel.line))) {
      altSel = null; $$('#alt-chips .altchip').forEach(function (c) { c.classList.remove('on'); });
    }
  });
  document.body.addEventListener('htmx:afterSwap', function (e) {   // uusi sheet: lue valittu linja chipistä
    if (!e.detail.target || e.detail.target.id !== 'sheet-body') return;
    var on = $('#alt-chips .altchip.on');
    altSel = on ? { line: on.dataset.line, over: parseFloat(on.dataset.over) || 0, under: parseFloat(on.dataset.under) || 0 } : null;
  });
  var swap = $('#swap-teams');
  if (swap) swap.addEventListener('click', function () {
    ['team', 'odds'].forEach(function (k) {
      var a = $('#f-' + k + '1'), b = $('#f-' + k + '2'), t = a.value; a.value = b.value; b.value = t;
    });
    if (pinSel) applyPin(); else calcSave();
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

  /* ---------- Pinnaclen otteluvalinta: kartan kertoimet haetaan valitun kartan mukaan ---------- */
  var pinWanted = '';  // tallennettu valinta, joka odottaa JSON:n palautumista
  var pinSel = null;   // valittu ottelu {t1, t2, key, maps: {"1": {odds1, odds2, total, over, under}, ...}}
  function pinNorm(s) { return String(s || '').trim().toLowerCase(); }
  function curMapNo() { var r = $('#calc-form input[name="kartta"]:checked'); var m = r && r.value.match(/(\d)/); return m ? m[1] : null; }
  function pinOrient() {   // 1 = samassa järjestyksessä kuin JSON:ssa, -1 = vaihdettu, 0 = eri joukkueet
    var f = $('#calc-form'); if (!f || !pinSel) return 0;
    var a = pinNorm(f.elements.team1.value), b = pinNorm(f.elements.team2.value), x = pinNorm(pinSel.t1), y = pinNorm(pinSel.t2);
    return (a === x && b === y) ? 1 : ((a === y && b === x) ? -1 : 0);
  }
  function pinLines() {   // valitun ottelun pelaajalinjat nykyiselle kartalle
    if (!pinSel || !pinSel.players) return [];
    var no = parseInt(curMapNo(), 10);
    return pinSel.players.filter(function (p) { return p.map === no; });
  }
  function setv(id, v, dflt) { var el = document.getElementById(id); if (el) el.value = (v === null || v === undefined || v === '') ? dflt : v; }
  function pinNote() {
    var n = $('#pin-note'); if (!n) return;
    if (!pinSel) { n.hidden = true; n.textContent = ''; return; }
    var no = curMapNo(), has = !!(pinSel.maps && pinSel.maps[no]);
    var pl = pinLines().length, plTxt = (pinSel.players && pinSel.players.length) ? (pl ? (' · ' + pl + ' pelaajalinjaa') : ' · ei pelaajalinjoja tälle kartalle') : '';
    var ms = pinSel.map_src && pinSel.map_src[no];
    var src = ms === 'epicbet' ? 'Epicbet' : (ms === 'mixed' ? 'Pinnacle + Epicbetin kierrostotal' : 'Pinnacle');
    var noMl = has && !pinSel.maps[no].odds1 ? ' (voittajakertoimia ei ole, oletus 1.85)' : '';
    n.hidden = false; n.className = 'muted' + (has ? '' : ' warnnote');
    n.textContent = has ? (src + ': ' + pinSel.t1 + ' vs ' + pinSel.t2 + ' · kartta ' + no + ' kertoimet käytössä' + noMl + plTxt)
                        : ('JSON:ssa ei ole kartan ' + no + ' kertoimia (kertoimet palautettu oletukseen)' + plTxt);
  }
  function applyPin() {
    var f = $('#calc-form'); if (!f || !pinSel) { pinNote(); return; }
    var d = (pinSel.maps || {})[curMapNo()], sw = pinOrient() === -1;
    if (d) {
      setv('f-odds1', sw ? d.odds2 : d.odds1, '1.85'); setv('f-odds2', sw ? d.odds1 : d.odds2, '1.85');
      setv('f-total', d.total, 0); setv('f-over', d.over, 0); setv('f-under', d.under, 0);
    } else {
      setv('f-odds1', null, '1.85'); setv('f-odds2', null, '1.85'); setv('f-total', null, 0); setv('f-over', null, 0); setv('f-under', null, 0);
    }
    pinNote(); calcSave();
  }
  function pinMarkItems() {
    $$('#pin-list .pin-item').forEach(function (i) {
      var d = null; try { d = JSON.parse(i.dataset.match); } catch (err) { d = null; }
      i.classList.toggle('on', !!(pinSel && d && d.key === pinSel.key));
    });
  }
  function pinSelect(m) {
    var f = $('#calc-form'); if (!f) return;
    pinSel = m; f.elements.team1.value = m.t1; f.elements.team2.value = m.t2; f.elements.map_name.value = '';
    pinMarkItems(); applyPin();
    var fold = $('#pin-fold'); if (fold) fold.open = false;
    toast({ msg: 'Ladattu: ' + m.t1 + ' vs ' + m.t2 + ' · kartta ' + curMapNo() });
  }
  function pinCheckTeams() {   // käsin muokatut joukkueet katkaisevat linkin valittuun otteluun
    if (pinSel && pinOrient() === 0) { pinSel = null; pinWanted = ''; pinMarkItems(); pinNote(); calcSave(); }
  }
  function pinReselect() {   // JSON luettu: valinta säilyy jos sama ottelu löytyy edelleen
    var key = pinSel ? pinSel.key : (pinWanted || null);
    pinSel = null;
    var hit = null;
    $$('#pin-list .pin-item').forEach(function (i) {
      var d = null; try { d = JSON.parse(i.dataset.match); } catch (err) { d = null; }
      if (d && key && d.key === key) hit = d;
    });
    if (hit) pinSel = hit; else pinWanted = '';
    pinMarkItems();
    if (pinSel && !pinRestoring) applyPin(); else { pinNote(); calcSave(); }
  }

  /* ---------- Laske-sivu muistaa valitun ottelun ja tuloksen ---------- */
  var CALC_FORM = 'calc_form', CALC_RES = 'calc_result', RES_TTL = 6 * 3600 * 1000;
  var CALC_FIELDS = ['team1', 'team2', 'odds1', 'odds2', 'map_name', 'total_line', 'total_over', 'total_under'];
  function calcSave() {
    var f = $('#calc-form'); if (!f) return;
    var o = {};
    CALC_FIELDS.forEach(function (n) { var el = f.elements[n]; if (el) o[n] = el.value; });
    var r = f.querySelector('input[name="kartta"]:checked'); if (r) o.kartta = r.value;
    o.pin = pinSel ? pinSel.key : (pinWanted || '');
    store(CALC_FORM, JSON.stringify(o));
    var clr = $('#calc-clear'); if (clr) clr.hidden = !(o.team1 || o.team2);
  }
  function calcRestore() {
    var f = $('#calc-form'); if (!f) return;
    var o = null; try { o = JSON.parse(store(CALC_FORM) || 'null'); } catch (e) { o = null; }
    if (o) {
      CALC_FIELDS.forEach(function (n) {
        var el = f.elements[n]; if (!el || o[n] === undefined || o[n] === null) return;
        if (el.tagName === 'SELECT' && !$$('option', el).some(function (op) { return (op.value || op.text) === o[n]; })) return;
        el.value = o[n];
      });
      if (o.kartta) { var r = f.querySelector('input[name="kartta"][value="' + o.kartta + '"]'); if (r) r.checked = true; }
      if (!pinSel) pinWanted = o.pin || '';
    }
    var res = $('#result'), saved = null;
    try { saved = JSON.parse(store(CALC_RES) || 'null'); } catch (e) { saved = null; }
    if (res && !res.children.length && saved && saved.html && Date.now() - saved.ts < RES_TTL) {
      res.innerHTML = saved.html;
      if (window.htmx) htmx.process(res);
      renderCharts(res);
    }
    calcSave();
  }
  function calcClear() {
    var f = $('#calc-form'); if (!f) return;
    ['team1', 'team2'].forEach(function (n) { f.elements[n].value = ''; });
    ['odds1', 'odds2'].forEach(function (n) { f.elements[n].value = '1.85'; });
    ['total_line', 'total_over', 'total_under'].forEach(function (n) { f.elements[n].value = '0'; });
    f.elements.map_name.value = '';
    var r1 = $('#k1'); if (r1) r1.checked = true;
    var res = $('#result'); if (res) res.innerHTML = '';
    pinSel = null; pinWanted = ''; pinMarkItems(); pinNote();
    try { localStorage.removeItem(CALC_FORM); localStorage.removeItem(CALC_RES); } catch (e) { /* ei tallennusta */ }
    calcSave();
  }
  (function () {
    var f = $('#calc-form'); if (!f) return;
    f.addEventListener('input', calcSave); f.addEventListener('change', calcSave);
    f.addEventListener('input', function (e) { if (e.target.name === 'team1' || e.target.name === 'team2') pinCheckTeams(); });
    f.addEventListener('change', function (e) { if (e.target.name === 'kartta') applyPin(); });
    var clr = $('#calc-clear'); if (clr) clr.addEventListener('click', calcClear);
    f.addEventListener('htmx:configRequest', function (e) {
      var L = pinLines();
      e.detail.parameters.lines = L.length ? JSON.stringify(L) : '';
      e.detail.parameters.epic_tournament = (L.length && pinSel.tournament) ? pinSel.tournament : '';
      e.detail.parameters.match_start = (pinSel && pinSel.start) ? pinSel.start : '';   // vedon päivämäärän oletus
    });
    document.body.addEventListener('htmx:afterSwap', function (e) {
      var t = e.detail.target;
      if (!t || t.id !== 'result') return;
      if (t.querySelector('.matchhead')) store(CALC_RES, JSON.stringify({ html: t.innerHTML, ts: Date.now() }));
      else { try { localStorage.removeItem(CALC_RES); } catch (err) { /* ei tallennusta */ } }
      calcSave();
    });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { setTimeout(calcRestore, 0); });
    else setTimeout(calcRestore, 0);
    window.addEventListener('pageshow', function (e) { if (e.persisted) calcRestore(); });
  })();

  /* ---------- Pinnaclen, Epicbetin ja Unibetin JSON: kunkin viimeisin tallentuu laitteelle ---------- */
  var BOXES = [
    { id: 'pin',  ta: '#pin-text',  meta: '#pin-meta',  clr: '#pin-clear',  key: 'pin_json',  ts: 'pin_json_ts',  name: 'Pinnacle', lines: false },
    { id: 'epic', ta: '#epic-text', meta: '#epic-meta', clr: '#epic-clear', key: 'epic_json', ts: 'epic_json_ts', name: 'Epicbet',  lines: true },
    { id: 'unibet', ta: '#unibet-text', meta: '#unibet-meta', clr: '#unibet-clear', key: 'unibet_json', ts: 'unibet_json_ts', name: 'Unibet', lines: true }
  ];
  var pinRestoring = false;

  /* Teksti -> JSON-dokumenttien lista (peräkkäin liitetyt dokumentit sallitaan, kuten palvelimella); null jos ei kelvollista JSONia. */
  function pinDocs(text) {
    var out = [], i = 0, n = text.length;
    while (i < n) {
      var c = text.charAt(i);
      if (c === ' ' || c === '\n' || c === '\r' || c === '\t' || c === ',' || c === ';') { i++; continue; }
      if (c !== '{' && c !== '[') return null;
      var depth = 0, j = i, inStr = false, done = false;
      for (; j < n; j++) {
        var ch = text.charAt(j);
        if (inStr) { if (ch === '\\') j++; else if (ch === '"') inStr = false; }
        else if (ch === '"') inStr = true;
        else if (ch === '{' || ch === '[') depth++;
        else if (ch === '}' || ch === ']') { depth--; if (depth === 0) { done = true; break; } }
      }
      if (!done) return null;
      try { out.push(JSON.parse(text.slice(i, j + 1))); } catch (err) { return null; }
      i = j + 1;
    }
    return out;
  }
  function pinWhen(d) { return d.getDate() + '.' + (d.getMonth() + 1) + '. klo ' + ('0' + d.getHours()).slice(-2) + '.' + ('0' + d.getMinutes()).slice(-2); }
  function pinStatus() {
    var items = $$('#pin-list .pin-item').length;
    var cnt = $('#pin-count');
    if (cnt) { cnt.hidden = !items; cnt.textContent = items ? String(items) : ''; }
    BOXES.forEach(function (b) {
      var ta = $(b.ta), meta = $(b.meta), clr = $(b.clr); if (!ta) return;
      var txt = ta.value.trim();
      if (clr) clr.hidden = !txt;
      if (!meta) return;
      meta.style.color = '';
      if (!txt) { meta.hidden = true; meta.textContent = ''; return; }
      meta.hidden = false;
      var docs = pinDocs(txt);
      if (!docs) { meta.style.color = 'var(--warn)'; meta.textContent = 'JSON ei näytä kelvolta – tarkista että koko teksti tuli liitettyä.'; return; }
      var m = 0, pl = 0, fetched = '';
      docs.forEach(function (d) {
        if (!d || !Array.isArray(d.matches)) return;
        m += d.matches.length;
        d.matches.forEach(function (x) { pl += ((x && x.players) || []).length; });
        if (d.fetched && String(d.fetched) > fetched) fetched = String(d.fetched);
      });
      var parts = [m + (m === 1 ? ' ottelu' : ' ottelua')];
      if (b.lines) parts.push(pl + ' pelaajalinjaa');
      var t = fetched ? new Date(fetched) : null, ts = store(b.ts);
      if (t && !isNaN(t)) {
        parts.push('haettu ' + pinWhen(t));
        var h = (Date.now() - t.getTime()) / 3600000;
        if (h >= 3) { parts.push('vanha (' + Math.floor(h) + ' h)'); meta.style.color = 'var(--warn)'; }
      } else if (ts) parts.push('liitetty ' + pinWhen(new Date(parseInt(ts, 10))));
      meta.textContent = b.name + ': ' + parts.join(' · ') + ' · tallessa tällä laitteella';
    });
  }
  function pinSave(b) {
    var ta = $(b.ta); if (!ta || !ta.value.trim()) return;
    if (store(b.key) !== ta.value) { store(b.key, ta.value); if (!pinRestoring) store(b.ts, String(Date.now())); }
  }
  function pinRead() { var btn = $('#pin-read'); if (btn && window.htmx) htmx.trigger(btn, 'click'); }
  function pinRestore() {
    if (!$('#pin-text') || !$('#epic-text')) return;
    /* Siirtymä: ennen yhteistä laatikkoa Pinnacle- ja Epicbet-dokumentit tallentuivat yhteen (pin_json) -> Epicbet siirretään omaan laatikkoonsa. */
    if (!store('epic_split')) {
      var sp = store('pin_json'), docs = sp && !store('epic_json') ? pinDocs(sp) : null;
      if (docs) {
        var isE = function (d) { return d && d.source === 'epicbet'; }, ep = docs.filter(isE), rest = docs.filter(function (d) { return !isE(d); });
        var join = function (a) { return a.map(function (d) { return JSON.stringify(d); }).join('\n'); };
        if (ep.length) {
          store('epic_json', join(ep)); store('epic_json_ts', store('pin_json_ts') || String(Date.now()));
          if (rest.length) store('pin_json', join(rest)); else { try { localStorage.removeItem('pin_json'); localStorage.removeItem('pin_json_ts'); } catch (err) { /* ei tallennusta */ } }
        }
      }
      store('epic_split', '1');
    }
    var any = false;
    BOXES.forEach(function (b) {
      var ta = $(b.ta), saved = store(b.key);
      if (ta && saved && !ta.value.trim()) { ta.value = saved; any = true; }
    });
    if (any) { pinRestoring = true; pinRead(); }
    pinStatus();
  }
  document.body.addEventListener('htmx:afterSwap', function (e) {
    var t = e.detail.target;
    if (!t || t.id !== 'pin-list') return;
    if ($('.pin-item', t)) BOXES.forEach(pinSave);
    pinReselect();
    pinRestoring = false;
    pinStatus();
  });
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('#pin-clear, #epic-clear, #unibet-clear'); if (!btn) return;
    var b = BOXES.filter(function (x) { return x.clr === '#' + btn.id; })[0];
    var ta = $(b.ta), l = $('#pin-list');
    var rest = BOXES.some(function (x) { var t = x !== b && $(x.ta); return !!(t && t.value.trim()); });
    if (ta) ta.value = '';
    try { localStorage.removeItem(b.key); localStorage.removeItem(b.ts); } catch (err) { /* ei tallennusta */ }
    if (rest) pinRead();                             // jokin toinen laatikko jää voimaan -> lista luetaan uudelleen
    else { if (l) l.innerHTML = ''; pinSel = null; pinWanted = ''; pinNote(); calcSave(); }
    pinStatus(); toast({ msg: b.name + '-JSON tyhjennetty' });
  });
  /* ---------- Hae kertoimet palvelimelta (Pinnacle: karttakertoimet, Unibet: pelaajalinjat) ---------- */
  var FETCHES = {
    'pin-fetch': {
      url: '/hae/pinnacle', ta: '#pin-text', st: '#pin-fetch-status', busy: 'Haetaan Pinnaclen CS2-otteluita…',
      done: function (j, hm) {
        if (!j.n) return { warn: true, msg: 'Pinnaclella ei ole nyt CS2-otteluita karttamarkkinoilla' + (j.total ? ' (' + j.total + ' ottelua pelkillä ottelukertoimilla)' : '') + '.' };
        return { msg: 'Haettu ' + j.n + ' ottelua klo ' + hm + (j.skipped ? ' · ' + j.skipped + ' ohitettu (vain ottelukertoimet: ' + (j.upcoming || []).slice(0, 3).join(', ') + (j.skipped > 3 ? ' …' : '') + ')' : '') + '.' };
      }
    },
    'epic-fetch': {
      url: '/hae/epicbet', ta: '#epic-text', st: '#epic-fetch-status', busy: 'Haetaan Epicbetin CS2-otteluita ja pelaajalinjoja (voi kestää 10–20 s)…',
      done: function (j, hm) {
        if (!j.n) {
          if (!j.total) return { warn: true, msg: 'Epicbetin CS2-otteluita ei löytynyt rajapinnasta.' };
          return { warn: true, msg: 'Epicbetillä on ' + j.total + ' CS2-ottelua, mutta pelaajamarkkinat eivät ole vielä auki' + ((j.upcoming || []).length ? ' (lähimmät: ' + j.upcoming.slice(0, 3).join(', ') + ')' : '') + '. Ne aukeavat n. 12 h ennen alkua.' };
        }
        return { msg: 'Haettu ' + j.n + ' ottelua, ' + j.lines + ' pelaajalinjaa klo ' + hm + (j.errors ? ' · ' + j.errors + ' ottelun haku epäonnistui' : '') + '.', warn: !!j.errors };
      }
    },
    'unibet-fetch': {
      url: '/hae/unibet', ta: '#unibet-text', st: '#unibet-fetch-status', busy: 'Haetaan Unibetin CS2-otteluita ja pelaajalinjoja (voi kestää 10–20 s)…',
      done: function (j, hm) {
        if (!j.n) {
          if (!j.total) return { warn: true, msg: 'Unibetin CS2-otteluita ei löytynyt rajapinnasta. Rajapintapolku on voinut muuttua – katso /hae/unibet/diag tai käytä kirjanmerkkiä.' };
          return { warn: true, msg: 'Unibetilla on ' + j.total + ' CS2-ottelua, mutta pelaajien tappomarkkinat eivät ole vielä auki' + ((j.upcoming || []).length ? ' (lähimmät: ' + j.upcoming.slice(0, 3).join(', ') + ')' : '') + '. Ne aukeavat vasta lähempänä ottelun alkua.' };
        }
        return { msg: 'Haettu ' + j.n + ' ottelua, ' + j.lines + ' pelaajalinjaa klo ' + hm + (j.errors ? ' · ' + j.errors + ' ottelun haku epäonnistui' : '') + '.', warn: !!j.errors };
      }
    }
  };
  document.addEventListener('click', function (e) {
    var b = e.target.closest('#pin-fetch, #epic-fetch, #unibet-fetch'); if (!b || b.disabled) return;
    var cfg = FETCHES[b.id], ta = $(cfg.ta), st = $(cfg.st), label = b.textContent;
    function say(msg, warn) { if (!st) return; st.hidden = false; st.textContent = msg; st.style.color = warn ? 'var(--warn)' : ''; }
    b.disabled = true; b.textContent = 'Haetaan…'; say(cfg.busy);
    var body = new URLSearchParams(); body.set('text', ta ? ta.value : '');
    fetch(cfg.url, { method: 'POST', body: body })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: 'Palvelin vastasi virheellisesti (HTTP ' + r.status + ').' }; }); })
      .then(function (j) {
        if (!j.ok) { say(j.error || 'Haku epäonnistui.', true); return; }
        var d = new Date(j.fetched), hm = ('0' + d.getHours()).slice(-2) + '.' + ('0' + d.getMinutes()).slice(-2), res = cfg.done(j, hm);
        say(res.msg, res.warn);
        if (j.n && ta) { ta.value = j.text; var rb = $('#pin-read'); if (rb && window.htmx) htmx.trigger(rb, 'click'); }
      })
      .catch(function () { say('Yhteys palvelimeen katkesi. Yritä uudelleen.', true); })
      .then(function () { b.disabled = false; b.textContent = label; });
  });
  BOXES.forEach(function (b) { var ta = $(b.ta); if (ta) ta.addEventListener('input', pinStatus); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { setTimeout(pinRestore, 0); });
  else setTimeout(pinRestore, 0);
  window.addEventListener('pageshow', function (e) { if (e.persisted) pinRestore(); });

  document.body.addEventListener('htmx:load', function (e) { renderCharts(e.detail.elt); });
  window.addEventListener('load', function () { renderCharts(document); });
})();
