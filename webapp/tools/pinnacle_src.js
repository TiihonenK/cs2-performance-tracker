/* Pinnacle CS2 -> JSON (karttojen voittajakertoimet + kierrostotalit)
   Ajetaan pinnacle.com-sivulla (kirjanmerkki). Hakee Pinnaclen julkisen vieras-rajapinnan (ei kirjautumista, ei API-avainta)
   ja palauttaa objektin jonka voi liittää Laske-sivun JSON-laatikkoon.

   Rajapinnan rakenne (tarkistettu oikealla datalla 8.11.2026):
   - sports                          -> "E Sports" (id 12)
   - sports/12/leagues?all=false     -> liigat, CS2 = nimi alkaa "CS2 - "
   - leagues/{id}/matchups           -> ottelut: participants[home|away], periods, startTime, isLive, bestOfX
   - leagues/{id}/markets/straight   -> markkinat: matchupId, period (0 = ottelu, 1..N = kartta N), type (moneyline|spread|total),
                                        prices[{designation: home|away|over|under, price: AMERIKKALAINEN kerroin, points}], isAlternate, status
   Kartan kierrostotal = type "total", period >= 1 (period 0:n total on karttojen lukumäärä, esim. 2.5 -> ei käytetä).
   Pinnaclen sivu näyttää desimaalikertoimen katkaistuna kolmeen desimaaliin (-104 -> 1.961, ei 1.962); sama tehdään tässä. */
async function pinnacleCS2(opts) {
  opts = opts || {};
  const BASE = 'https://guest.api.arcadia.pinnacle.com/0.1/';
  const get = async path => {
    const r = await fetch(BASE + path, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status);
    return r.json();
  };
  // amerikkalainen -> desimaali, katkaistu 3 desimaaliin kuten Pinnaclen sivulla
  const dec = p => {
    if (typeof p !== 'number' || !isFinite(p) || p === 0) return null;
    if (p > 0) return Math.floor((100 + p) * 10) / 1000;
    const a = -p;
    return Math.floor(1000 * (a + 100) / a) / 1000;
  };
  const price = (mk, des) => { const x = ((mk && mk.prices) || []).find(p => p.designation === des); return x ? dec(x.price) : null; };
  const open = mk => mk && mk.status === 'open';

  const sports = await get('sports');
  const es = (sports || []).find(s => /e-?sport/i.test(s.name || ''));
  const leagues = await get('sports/' + (es ? es.id : 12) + '/leagues?all=false');
  const cs = (leagues || []).filter(l => /^cs2\b|counter-?strike/i.test(l.name || '') && (l.matchupCount || 0) > 0);

  const matches = [], skipped = [];
  let total = 0;
  const per = await Promise.all(cs.map(async l => ({ l, mu: await get('leagues/' + l.id + '/matchups'), mk: await get('leagues/' + l.id + '/markets/straight') })));
  for (const { l, mu, mk } of per) {
    const byMu = {};
    (mk || []).forEach(x => {
      const slot = byMu[x.matchupId] = byMu[x.matchupId] || {};
      (slot[x.period] = slot[x.period] || []).push(x);
    });
    for (const m of mu || []) {
      if (m.type !== 'matchup' || m.parentId != null || m.parent || m.isLive) continue;
      const home = (m.participants || []).find(p => p.alignment === 'home'), away = (m.participants || []).find(p => p.alignment === 'away');
      if (!home || !away) continue;
      total++;
      const periods = byMu[m.id] || {};
      const maps = [];
      // yhden kartan tiedot markkinalistasta: voittajakertoimet + kierrostotal
      // kierrostotal: ensisijaisesti pääviiva (isAlternate false), muuten se jonka yli/alle-kertoimet ovat lähimpänä toisiaan;
      // minPts > 0 hylkää karttojen lukumäärä -totalit (period 0 Bo3-ottelussa: 2.5), kierroksia on aina yli 8
      const buildMap = (n, list, minPts) => {
        const ml = list.find(x => x.type === 'moneyline');
        const o1 = price(ml, 'home'), o2 = price(ml, 'away');
        const ptsOf = x => (x.prices.find(p => p.designation === 'over') || {}).points;
        const tots = list.filter(x => x.type === 'total' && price(x, 'over') && price(x, 'under') && typeof ptsOf(x) === 'number' && ptsOf(x) > minPts);
        const main = tots.filter(x => !x.isAlternate);
        let best = null;
        (main.length ? main : tots).forEach(x => {
          const ov = price(x, 'over'), un = price(x, 'under'), gap = Math.abs(ov - un);
          if (!best || gap < best.gap) best = { gap, line: ptsOf(x), over: ov, under: un };
        });
        const mp = { map: 'Kartta ' + n };
        if (o1 && o2) { mp.odds1 = o1; mp.odds2 = o2; }
        if (best) { mp.total_line = best.line; mp.over_odds = best.over; mp.under_odds = best.under; }
        return (mp.odds1 || mp.total_line != null) ? mp : null;
      };
      Object.keys(periods).map(Number).filter(n => n >= 1).sort((a, b) => a - b).forEach(n => {
        const mp = buildMap(n, periods[n].filter(open), 0);
        if (mp) maps.push(mp);
      });
      // Bo1: ei erillisiä karttamarkkinoita, jolloin ottelu (period 0) on yksi kartta. Tunnistus: bestOfX = 1 tai
      // ottelutason totalin viiva on kierrosluku (> 8; Bo3-ottelun period 0 -total on karttojen lukumäärä, esim. 2.5).
      let bo1 = false;
      if (!maps.length && periods[0]) {
        const p0 = periods[0].filter(open);
        const hasRounds = p0.some(x => x.type === 'total' && typeof (x.prices.find(p => p.designation === 'over') || {}).points === 'number' && x.prices.find(p => p.designation === 'over').points > 8);
        if (m.bestOfX === 1 || hasRounds) {
          const mp = buildMap(1, p0, 8);
          if (mp) { maps.push(mp); bo1 = true; }
        }
      }
      if (!maps.length) {
        skipped.push({ name: home.name + ' - ' + away.name, tournament: l.name.replace(/^CS2\s*-\s*/i, ''), start: m.startTime,
          best_of: m.bestOfX == null ? null : m.bestOfX, markets: Object.keys(periods).map(k => 'p' + k + ':' + periods[k].map(x => x.type).join('+')).join(' ') });
        continue;
      }
      matches.push(Object.assign({ team1: home.name, team2: away.name, tournament: l.name.replace(/^CS2\s*-\s*/i, ''), start: m.startTime, source: 'pinnacle' }, bo1 ? { best_of: 1 } : {}, { maps, players: [] }));
    }
  }
  matches.sort((a, b) => a.start < b.start ? -1 : a.start > b.start ? 1 : 0);
  skipped.sort((a, b) => a.start < b.start ? -1 : 1);
  return {
    source: 'pinnacle', fetched: new Date().toISOString(),
    cs2_leagues: cs.length, cs2_matches_total: total, matches_with_map_markets: matches.length,
    matches, upcoming: skipped.slice(0, 8)
  };
}


(async function () {
  var ID = 'cs-pin-overlay';
  var old = document.getElementById(ID); if (old) old.remove();
  var box = document.createElement('div'); box.id = ID;
  box.style.cssText = 'position:fixed;z-index:2147483647;top:12px;right:12px;width:min(440px,calc(100vw - 24px));max-height:85vh;overflow:auto;background:#fff;color:#111;border:1px solid #888;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.35);font:14px/1.45 system-ui,sans-serif;padding:14px;box-sizing:border-box';
  function el(tag, css, text) { var e = document.createElement(tag); if (css) e.style.cssText = css; if (text) e.textContent = text; return e; }
  var head = el('div', 'display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px');
  head.appendChild(el('b', 'font-size:15px', 'CS2-kertoimet (Pinnacle) → Vetomalli'));
  var close = el('button', 'border:0;background:#eee;border-radius:8px;padding:4px 10px;cursor:pointer;font:inherit', 'Sulje');
  close.onclick = function () { box.remove(); };
  head.appendChild(close); box.appendChild(head);
  var status = el('div', 'margin-bottom:8px', 'Haetaan Pinnaclen CS2-otteluita…'); box.appendChild(status);
  document.body.appendChild(box);
  function fail(msg) { status.textContent = msg; status.style.color = '#b00020'; }
  if (!/(^|\.)pinnacle\.com$/.test(location.hostname)) { fail('Avaa ensin pinnacle.com ja paina kirjanmerkki uudelleen.'); return; }
  var data;
  try { data = await pinnacleCS2(); } catch (err) { fail('Haku epäonnistui: ' + (err && err.message || err)); return; }
  var fmt = function (iso) { try { return new Date(iso).toLocaleString('fi-FI', { weekday: 'short', day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (e) { return iso; } };
  if (!data.matches.length) {
    status.textContent = 'Yhdelläkään CS2-ottelulla ei ole karttamarkkinoita (' + data.cs2_matches_total + ' ottelua ' + data.cs2_leagues + ' liigassa).' + (data.upcoming.length ? ' Pelkät ottelukertoimet:' : '');
    var ul = el('div', 'margin-top:6px;font-size:13px;color:#444');
    data.upcoming.forEach(function (u) { ul.appendChild(el('div', '', fmt(u.start) + ' · ' + u.name + ' (' + u.tournament + ')')); });
    box.appendChild(ul); return;
  }
  var text = JSON.stringify(data);
  var skippedN = data.cs2_matches_total - data.matches.length;
  status.textContent = data.matches.length + ' ottelua' + (skippedN ? ' (' + skippedN + ' ottelua ilman karttamarkkinoita ohitettu)' : '') + '. ';
  var list = el('div', 'margin:4px 0 8px;font-size:13px;color:#333');
  data.matches.forEach(function (m) {
    var nos = m.maps.map(function (x) { return x.map.replace('Kartta ', ''); }).join(',');
    var tot = m.maps.some(function (x) { return x.total_line != null; });
    list.appendChild(el('div', '', m.team1 + ' vs ' + m.team2 + ' · ' + fmt(m.start) + (m.best_of === 1 ? ' · Bo1 (kartta 1)' : ' · kartat ' + nos) + (tot ? ' · totalit' : '')));
  });
  box.appendChild(list);
  if (data.upcoming.length) box.appendChild(el('div', 'margin:0 0 8px;font-size:12px;color:#666', 'Ohitettu (vain ottelukertoimet, ei karttamarkkinoita): ' + data.upcoming.slice(0, 5).map(function (u) { return u.name; }).join(', ') + (data.upcoming.length > 5 ? ' …' : '')));
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
