/* Unibet CS2 -> Vetomalli. Aja fi.unibet.com-sivulla (kirjanmerkki).
   Lukee Unibetin oman Kambi-tarjontarajapinnan julkista JSONia (sama jota sivu käyttää): ei evästeitä, ei kirjautumista,
   ei mitään lähetetä minnekään. Tulos: sama JSON-muoto kuin Epicbet-kirjanmerkillä (source "unibet"), pelaajien tappolinjat kartoittain. */
(function () {
  'use strict';
  var OFFER = 'ubca', QS = 'lang=fi_FI&market=FI&channel_id=1&client_id=2';
  var BASES = ['https://eu1.offering-api.kambicdn.com/offering/v2018/' + OFFER + '/', 'https://eu.offering-api.kambicdn.com/offering/v2018/' + OFFER + '/'];
  var KILL_EN = /^Map\s*(\d)\s*-\s*Total Kills by (?:the )?Player$/i;     // "Map 1 - Total Kills by the Player"
  var KILL_FI = /^Kartta\s*(\d)\s*-\s*tappoja yhteensä$/i;              // "Kartta 1 - tappoja yhteensä" (joukkueen tapot ovat "Kartta 1 - Joukkueen X tappoja yhteensä")
  var MIN_BO = 50, MAX_EVENTS = 25, NO_COUNT_EVENTS = 15;

  async function unibetCS2() {
    var diag = { attempts: [], labels: {}, raw: [], skipped: [], events: [], errors: [] };
    var nowIso = new Date().toISOString();

    async function getUrl(url) {
      var r = await fetch(url, { credentials: 'omit' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }
    async function getRel(rel) {
      var last = null, i, url;
      for (i = 0; i < BASES.length; i++) {
        url = BASES[i] + rel + (rel.indexOf('?') < 0 ? '?' : '&') + QS + '&ncid=' + Date.now();
        try { return await getUrl(url); } catch (e) { last = e; }
      }
      throw last || new Error('virhe');
    }
    function evOf(x) { return (x && x.event) ? x.event : x; }
    function eventsOf(j) {
      var arr = (j && (j.events || j.liveEvents)) || [], out = [];
      arr.forEach(function (x) { var e = evOf(x); if (e && e.id) out.push(e); });
      return out;
    }
    function isCS(e) {
      if (e.sport === 'COUNTER_STRIKE') return true;
      return (e.path || []).some(function (p) { return p && (p.termKey === 'counter_strike' || /counter/i.test(p.englishName || p.name || '')); });
    }
    function upcomingCS(list) {
      var seen = {}, out = [];
      list.forEach(function (e) {
        if (!e || !e.id || seen[e.id] || !isCS(e) || (e.state && e.state !== 'NOT_STARTED')) return;
        seen[e.id] = 1; out.push(e);
      });
      return out;
    }

    /* 1) CS2-otteluiden löytäminen: sivun oma aiempi listahaku, sitten tunnetut Kambi-polut */
    var cands = [], perf = [];
    try {
      perf = performance.getEntriesByType('resource').map(function (r) { return r.name; })
        .filter(function (u) { return /\/offering\/v2018\/[^/]+\/(listView|betoffer\/group|event\/group)\//.test(u) && /counter|esports/i.test(u.split('?')[0]); });
    } catch (e) { /* ei suorituskykylokia */ }
    perf.reverse().slice(0, 3).forEach(function (u) { cands.push({ how: 'sivun oma haku', url: u }); });
    try {
      var gj = await getRel('group.json'), found = [];
      (function walk(g) {
        if (!g) return;
        if (g.sport === 'COUNTER_STRIKE' || g.termKey === 'counter_strike' || /^counter-?strike/i.test(g.englishName || g.name || '')) { found.push(g); return; }
        (g.groups || []).forEach(walk);
      })(gj.group || gj);
      found.slice(0, 2).forEach(function (g) {
        cands.push({ how: 'ryhmä ' + g.id, rel: 'betoffer/group/' + g.id + '.json' });
        cands.push({ how: 'ryhmä ' + g.id + ' (event)', rel: 'event/group/' + g.id + '.json' });
      });
      diag.cs_groups = found.map(function (g) { return [g.id, g.name, g.termKey, g.eventCount]; });
    } catch (e) { diag.errors.push('group.json: ' + (e && e.message || e)); }
    ['listView/esports/counter_strike/all/all/matches.json', 'listView/counter_strike/all/all/all/matches.json', 'listView/esports/counter_strike.json']
      .forEach(function (p) { cands.push({ how: 'arvaus', rel: p }); });

    var events = [], c, j, evs;
    for (var i = 0; i < cands.length && !events.length; i++) {
      c = cands[i];
      try {
        j = c.url ? await getUrl(c.url) : await getRel(c.rel);
        evs = upcomingCS(eventsOf(j));
        diag.attempts.push({ how: c.how, path: (c.url || c.rel).replace(/^https?:\/\/[^/]+/, '').split('?')[0], ok: true, events: evs.length });
        if (evs.length) events = evs;
      } catch (e) {
        diag.attempts.push({ how: c.how, path: (c.url || c.rel).replace(/^https?:\/\/[^/]+/, '').split('?')[0], ok: false, err: String(e && e.message || e) });
      }
    }
    events.sort(function (a, b) { return String(a.start) < String(b.start) ? -1 : 1; });
    var withCount = events.some(function (e) { return typeof e.nonLiveBoCount === 'number'; });
    var todo = withCount ? events.filter(function (e) { return (e.nonLiveBoCount || 0) >= MIN_BO; }).slice(0, MAX_EVENTS) : events.slice(0, NO_COUNT_EVENTS);

    /* 2) yhden ottelun pelaajien tappolinjat */
    function nameOf(e) { return [e.homeName, e.awayName]; }
    function parseEvent(det, ev) {
      var rows = {}, order = [];
      (det.betOffers || []).forEach(function (bo) {
        var cr = bo.criterion || {}, en = String(cr.englishLabel || '').trim(), fi = String(cr.label || '').trim();
        if (/kill|tapp/i.test(en + ' ' + fi)) { var lk = en || fi; if (diag.labels[lk] || Object.keys(diag.labels).length < 60) diag.labels[lk] = (diag.labels[lk] || 0) + 1; }
        var m = KILL_EN.exec(en) || KILL_FI.exec(fi);
        if (!m) return;
        if (diag.raw.length < 3) diag.raw.push(bo);
        (bo.outcomes || []).forEach(function (o) {
          var lab = String(o.englishLabel || o.label || ''), side = null;
          if (o.type === 'OT_OVER' || /^(over|yli)\b/i.test(lab)) side = 'over';
          else if (o.type === 'OT_UNDER' || /^(under|alle)\b/i.test(lab)) side = 'under';
          if (!side) return;
          var name = String(o.participant || bo.participant || '').trim(), team = null, pm;
          if (!name) name = lab.replace(/^(over|under|yli|alle)\s*/i, '').replace(/[\d.,]+\s*$/, '').replace(/^[\s\-–:]+|[\s\-–:]+$/g, '');
          pm = /^(.*?)\s*\(([^)]+)\)\s*$/.exec(name);
          if (pm) { name = pm[1].trim(); team = pm[2].trim(); }
          if (!name || /^(over|under|yli|alle)$/i.test(name) || typeof o.line !== 'number') { if (diag.skipped.length < 5) diag.skipped.push({ label: lab, participant: o.participant, line: o.line }); return; }
          var line = o.line / 1000, key = name.toLowerCase() + '|' + m[1] + '|' + line;
          var r = rows[key];
          if (!r) { r = rows[key] = { player: name, team: team, map: parseInt(m[1], 10), line: line, over: null, under: null }; order.push(key); }
          if ((!o.status || o.status === 'OPEN') && typeof o.odds === 'number' && o.odds > 1000) r[side] = o.odds / 1000;
        });
      });
      return order.map(function (k) { return rows[k]; }).filter(function (r) { return r.over !== null || r.under !== null; });
    }

    var matches = [], done = 0, k = 0;
    async function worker() {
      while (k < todo.length) {
        var ev = todo[k++], players = [];
        try {
          var det = await getRel('betoffer/event/' + ev.id + '.json?includeParticipants=true');
          var e2 = eventsOf(det)[0] || ev, nm = nameOf(e2);
          if (!nm[0] || !nm[1]) nm = String(e2.name || ev.name || '').split(/\s+-\s+/);
          players = parseEvent(det, e2);
          diag.events.push({ id: ev.id, name: ev.name, offers: (det.betOffers || []).length, lines: players.length });
          if (players.length) {
            players.sort(function (a, b) { return (a.map - b.map) || a.player.localeCompare(b.player) || (a.line - b.line); });
            matches.push({ team1: nm[0], team2: nm[1], tournament: e2.group || ev.group || null, start: e2.start || ev.start, source: 'unibet', maps: [], players: players });
          }
        } catch (e) { diag.errors.push(ev.id + ': ' + (e && e.message || e)); }
        done++;
        if (unibetCS2.onProgress) unibetCS2.onProgress(done, todo.length);
      }
    }
    await Promise.all([worker(), worker(), worker()]);
    matches.sort(function (a, b) { return String(a.start) < String(b.start) ? -1 : 1; });
    var upcoming = events.map(function (e) { return { name: e.name, tournament: e.group || null, start: e.start, bo: e.nonLiveBoCount }; }).slice(0, 8);
    diag.cs_events = events.length; diag.fetched_events = todo.length;
    return { source: 'unibet', fetched: nowIso, cs2_matches_total: events.length, matches_with_player_markets: matches.length, matches: matches, upcoming: upcoming, diag: diag };
  }

  /* ---------- ikkuna sivun päällä ---------- */
  (async function () {
    var ID = 'cs-unibet-overlay', old = document.getElementById(ID); if (old) old.remove();
    var box = document.createElement('div'); box.id = ID;
    box.style.cssText = 'position:fixed;z-index:2147483647;top:12px;right:12px;width:min(440px,calc(100vw - 24px));max-height:85vh;overflow:auto;background:#fff;color:#111;border:1px solid #888;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.35);font:14px/1.45 system-ui,sans-serif;padding:14px;box-sizing:border-box';
    function el(tag, css, text) { var e = document.createElement(tag); if (css) e.style.cssText = css; if (text) e.textContent = text; return e; }
    var head = el('div', 'display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px');
    head.appendChild(el('b', 'font-size:15px', 'Unibet CS2-pelaajalinjat → Vetomalli'));
    var close = el('button', 'border:0;background:#eee;border-radius:8px;padding:4px 10px;cursor:pointer;font:inherit', 'Sulje');
    close.onclick = function () { box.remove(); };
    head.appendChild(close); box.appendChild(head);
    var status = el('div', 'margin-bottom:8px', 'Haetaan Unibetin CS2-otteluita…'); box.appendChild(status);
    document.body.appendChild(box);
    function fail(msg) { status.textContent = msg; status.style.color = '#b00020'; }
    if (!/(^|\.)unibet\.[a-z.]+$/.test(location.hostname)) { fail('Avaa ensin fi.unibet.com ja paina kirjanmerkki uudelleen.'); return; }
    unibetCS2.onProgress = function (d, n) { status.textContent = 'Haetaan pelaajalinjoja… ' + d + ' / ' + n + ' ottelua'; };
    var data;
    try { data = await unibetCS2(); } catch (err) { fail('Haku epäonnistui: ' + (err && err.message || err)); return; }
    var fmt = function (iso) { try { return new Date(iso).toLocaleString('fi-FI', { weekday: 'short', day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (e) { return iso; } };
    function diagButton() {
      var dtxt = JSON.stringify({ probe: 'unibet_cs2', page: location.pathname, diag: data.diag, upcoming: data.upcoming }, null, 1).slice(0, 60000);
      var b = el('button', 'margin-top:8px;width:100%;border:1px solid #bbb;background:#f6f6f6;border-radius:10px;padding:8px;font:13px system-ui;cursor:pointer', 'Kopioi diagnostiikka (jos jokin ei toimi)');
      b.onclick = function () {
        var ta2 = el('textarea', 'width:100%;height:60px;font:12px ui-monospace,monospace;box-sizing:border-box;margin-top:6px'); ta2.readOnly = true; ta2.value = dtxt; box.appendChild(ta2);
        ta2.focus(); ta2.select(); var ok = false;
        try { ok = document.execCommand('copy'); } catch (e) { }
        if (!ok && navigator.clipboard) navigator.clipboard.writeText(dtxt).then(function () { b.textContent = 'Diagnostiikka kopioitu ✓'; }, function () { });
        else b.textContent = ok ? 'Diagnostiikka kopioitu ✓' : 'Valitse ja kopioi alla oleva teksti käsin';
      };
      box.appendChild(b);
    }
    if (!data.matches.length) {
      status.textContent = data.cs2_matches_total
        ? 'Yhdelläkään CS2-ottelulla ei ole vielä pelaajojen tappomarkkinoita. Lähimmät ottelut:'
        : 'CS2-otteluita ei löytynyt. Avaa Unibetin CS2-ottelulista (Esports → Counter-Strike) ja paina kirjanmerkkiä uudelleen.';
      var ul = el('div', 'margin-top:6px;font-size:13px;color:#444');
      data.upcoming.forEach(function (u) { ul.appendChild(el('div', '', fmt(u.start) + ' · ' + u.name + (u.tournament ? ' (' + u.tournament + ')' : ''))); });
      box.appendChild(ul); diagButton(); return;
    }
    var text = JSON.stringify(data);
    var nLines = data.matches.reduce(function (a, m) { return a + m.players.length; }, 0);
    status.textContent = data.matches.length + ' ottelua, ' + nLines + ' pelaajalinjaa. ';
    var list = el('div', 'margin:4px 0 8px;font-size:13px;color:#333');
    data.matches.forEach(function (m) { list.appendChild(el('div', '', m.team1 + ' vs ' + m.team2 + ' · ' + fmt(m.start) + ' · ' + m.players.length + ' linjaa')); });
    box.appendChild(list);
    var ta = el('textarea', 'width:100%;height:90px;font:16px ui-monospace,monospace;box-sizing:border-box'); ta.readOnly = true; ta.value = text; box.appendChild(ta);
    var btn = el('button', 'margin-top:8px;width:100%;border:0;background:#2a78d6;color:#fff;border-radius:10px;padding:10px;font:600 14px system-ui;cursor:pointer', 'Kopioi JSON leikepöydälle');
    function done(ok) { status.textContent += ok ? 'Kopioitu leikepöydälle – liitä Vetomallin Laske-sivulle.' : 'Paina alla olevaa Kopioi-nappia.'; btn.textContent = ok ? 'Kopioitu ✓' : 'Kopioi uudelleen'; }
    function copy() {
      ta.focus(); ta.select(); try { ta.setSelectionRange(0, ta.value.length); } catch (e) { }
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function () { done(true); }, function () { var ok = false; try { ok = document.execCommand('copy'); } catch (e) { } done(ok); });
      else { var ok = false; try { ok = document.execCommand('copy'); } catch (e) { } done(ok); }
    }
    btn.onclick = copy; box.appendChild(btn); diagButton(); copy();
  })();
})();
