async function epicbetCS2(opts) {
  opts = opts || {};
  const CS2 = 258534;
  const q = o => encodeURIComponent(JSON.stringify(o));
  const get = async (path, input) => {
    const r = await fetch('/s/core-proxy/public/' + path + '?input=' + q(input), { credentials: 'same-origin' });
    if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status);
    const j = await r.json();
    return j.result.data;
  };
  const list = await get('sport-base/match.hotByCategory', {
    language: 'fi', country: 'FI', foCategoryId: CS2, period: 'all', hasStream: false,
    limit: 130, topMarketTypeCount: 2, shouldOrderByPopularity: true, isBetaEnabled: false
  });
  const all = (list && list.matches) || [];
  const wanted = all.filter(m => m.hasPlayersMarkets);

  const oddsOf = async ids => {
    const out = new Map();
    for (let i = 0; i < ids.length; i += 100) {
      const chunk = ids.slice(i, i + 100);
      const arr = await get('sport-odds/activeOdds.getPreMatchByMarketIds', chunk);
      (arr || []).forEach(o => { if (o && o.status === 'open' && typeof o.value === 'number') out.set(o.outcomeId, o.value); });
    }
    return out;
  };
  const groupsOf = d => Array.isArray(d.marketGroups) ? d.marketGroups : Object.values(d.marketGroups || {});
  const mapNo = s => { const m = /(\d)\s*\.?\s*kartta/i.exec(s || ''); return m ? parseInt(m[1], 10) : null; };

  const matches = [];
  for (const m of wanted) {
    const sb = await get('sport-base/match.getSidebets', { matchId: m.id, language: 'fi', country: 'FI', marketType: 'players' });
    const groups = groupsOf(sb).filter(g => g.viewType === 'PlayerTotals' && /tap/i.test(g.name) && !/pääosum|headshot/i.test(g.name) && mapNo(g.name));
    const markets = groups.flatMap(g => (g.markets || []).map(mk => ({ g, mk })));
    const odds = await oddsOf(markets.map(x => x.mk.id));
    const pby = sb.playersById || {};
    const players = [];
    for (const { g, mk } of markets) {
      const yli = (mk.outcomes || []).find(o => /^yli/i.test(o.name));
      const alle = (mk.outcomes || []).find(o => /^alle/i.test(o.name));
      const p = pby[mk.playerId] || {};
      const name = mk.playerName || p.name;
      if (!name || mk.line == null) continue;
      const ov = yli ? odds.get(yli.id) : undefined, un = alle ? odds.get(alle.id) : undefined;
      if (ov === undefined && un === undefined) continue;     // markkina kiinni
      players.push({
        player: name, team: p.team === 'away' ? m.awayTeamName : (p.team === 'home' ? m.homeTeamName : null),
        map: mapNo(g.name), line: Number(mk.line), over: ov === undefined ? null : ov, under: un === undefined ? null : un
      });
    }
    players.sort((a, b) => (a.map - b.map) || String(a.team).localeCompare(String(b.team)) || a.player.localeCompare(b.player) || a.line - b.line);

    const maps = {};
    if (opts.withMaps !== false) {
      const full = await get('sport-base/match.getSidebets', { matchId: m.id, language: 'fi', country: 'FI', marketType: 'all' });
      const gs = groupsOf(full);
      const ml = gs.filter(g => /^\d\.\s*kartta:\s*money line$/i.test((g.name || '').trim()));
      const tot = gs.filter(g => /^\d\.\s*kartta:\s*kierrokset\s+yhteensä$/i.test((g.name || '').trim()));
      const ids = [...ml, ...tot].flatMap(g => (g.markets || []).map(x => x.id));
      const od = await oddsOf(ids);
      ml.forEach(g => {
        const n = mapNo(g.name), mk = (g.markets || [])[0]; if (!n || !mk) return;
        const h = (mk.outcomes || []).find(o => o.teamId === m.homeTeamId), a = (mk.outcomes || []).find(o => o.teamId === m.awayTeamId);
        const o1 = h && od.get(h.id), o2 = a && od.get(a.id);
        if (o1 && o2) (maps[n] = maps[n] || { map: 'Kartta ' + n }), Object.assign(maps[n], { odds1: o1, odds2: o2 });
      });
      tot.forEach(g => {
        const n = mapNo(g.name); if (!n) return;
        let best = null;
        (g.markets || []).forEach(mk => {
          const y = (mk.outcomes || []).find(o => /^yli/i.test(o.name)), a = (mk.outcomes || []).find(o => /^alle/i.test(o.name));
          const ov = y && od.get(y.id), un = a && od.get(a.id);
          if (!ov || !un) return;
          const gap = Math.abs(ov - un);
          if (!best || gap < best.gap) best = { gap, line: Number(mk.line), over: ov, under: un };
        });
        if (best) (maps[n] = maps[n] || { map: 'Kartta ' + n }), Object.assign(maps[n], { total_line: best.line, over_odds: best.over, under_odds: best.under });
      });
    }
    matches.push({
      team1: m.homeTeamName, team2: m.awayTeamName, tournament: m.leagueName, start: String(m.startDate).replace(' ', 'T').replace(/\+00$/, 'Z'),
      source: 'epicbet', maps: Object.keys(maps).sort().map(k => maps[k]), players
    });
  }
  const upcoming = all.filter(m => !m.hasPlayersMarkets).map(m => ({ name: m.name, tournament: m.leagueName, start: String(m.startDate).replace(' ', 'T').replace(/\+00$/, 'Z') }))
    .sort((a, b) => a.start < b.start ? -1 : 1).slice(0, 8);
  return {
    source: 'epicbet', fetched: new Date().toISOString(),
    cs2_matches_total: all.length, matches_with_player_markets: wanted.length, matches, upcoming
  };
}


(async function () {
  var ID = 'cs-epic-overlay';
  var old = document.getElementById(ID); if (old) old.remove();
  var box = document.createElement('div'); box.id = ID;
  box.style.cssText = 'position:fixed;z-index:2147483647;top:12px;right:12px;width:min(440px,calc(100vw - 24px));max-height:85vh;overflow:auto;background:#fff;color:#111;border:1px solid #888;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.35);font:14px/1.45 system-ui,sans-serif;padding:14px;box-sizing:border-box';
  function el(tag, css, text) { var e = document.createElement(tag); if (css) e.style.cssText = css; if (text) e.textContent = text; return e; }
  var head = el('div', 'display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px');
  head.appendChild(el('b', 'font-size:15px', 'CS2-pelaajalinjat → Vetomalli'));
  var close = el('button', 'border:0;background:#eee;border-radius:8px;padding:4px 10px;cursor:pointer;font:inherit', 'Sulje');
  close.onclick = function () { box.remove(); };
  head.appendChild(close); box.appendChild(head);
  var status = el('div', 'margin-bottom:8px', 'Haetaan Epicbetin CS2-otteluita…'); box.appendChild(status);
  document.body.appendChild(box);
  function fail(msg) { status.textContent = msg; status.style.color = '#b00020'; }
  if (!/(^|\.)epicbet\.com$/.test(location.hostname)) { fail('Avaa ensin epicbet.com ja paina kirjanmerkki uudelleen.'); return; }
  var data;
  try { data = await epicbetCS2(); } catch (err) { fail('Haku epäonnistui: ' + (err && err.message || err)); return; }
  var fmt = function (iso) { try { return new Date(iso).toLocaleString('fi-FI', { weekday: 'short', day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (e) { return iso; } };
  if (!data.matches.length) {
    status.textContent = 'Yhdelläkään CS2-ottelulla ei ole vielä pelaajamarkkinoita (ne aukeavat noin 12 h ennen alkua). Lähimmät ottelut:';
    var ul = el('div', 'margin-top:6px;font-size:13px;color:#444');
    data.upcoming.forEach(function (u) { ul.appendChild(el('div', '', fmt(u.start) + ' · ' + u.name + ' (' + u.tournament + ')')); });
    box.appendChild(ul); return;
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
  btn.onclick = copy; box.appendChild(btn); copy();
})();
