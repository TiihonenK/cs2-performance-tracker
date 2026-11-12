"""Epicbetin CS2-pelaajalinjat ja karttakertoimet palvelimelta (Python-versio kirjanmerkistä tools/epicbet_cs2_src.js).

Epicbetin sivu lukee julkista JSONia osoitteesta /s/core-proxy/public/<reitti>?input=<JSON> (ei kirjautumista). Haetaan:
  sport-base/match.hotByCategory        CS2 (foCategoryId 258534) -> ottelulista; hasPlayersMarkets = pelaajamarkkinat auki (~12 h ennen alkua)
  sport-base/match.getSidebets          marketType "players" -> "N. kartta: X Tapot" (PlayerTotals); marketType "all" -> kartan Money Line + Kierrokset yhteensä
  sport-odds/activeOdds.getPreMatchByMarketIds   outcomeId -> kerroin (vain status open)
Tulos on sama JSON kuin kirjanmerkissä (source "epicbet").

HUOM Cloudflare: Epicbet on aiemmin (8.11.2026, Pi) vastannut palvelimen pyyntöihin Cloudflaren selaintarkistuksella (HTTP 403).
Tämä moduuli tekee VAIN tavallisia pyyntöjä rehellisellä User-Agentilla. Tarkistusta ei kierretä (ei selainta jäljittelevää
TLS-sormenjälkeä, ei FlareSolveria, ei selaimeksi tekeytymistä): jos Cloudflare estää, haku päättyy selkeään virheeseen ja käytetään
Epicbet-kirjanmerkkiä selaimessa.

Ympäristömuuttuja EPICBET_BASE (esim. http://127.0.0.1:9000) ylikirjoittaa osoitteen (testeille). Tulos välimuistissa 30 s.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

_env = os.environ.get("EPICBET_BASE")
BASES = [_env.rstrip("/")] if _env else ["https://epicbet.com", "https://www.epicbet.com"]
UA = "vetomalli/1.0 (oma vetomalli; python-urllib)"
TIMEOUT = 12
CACHE_SECONDS = 30
CS2 = 258534
SUBKILLS = re.compile(r"pääosum|headshot|\bawp\b|\bhe\b|pistoo|pistol|veitsi|knife|first|ensimm|puoli|half", re.I)

_lock = threading.Lock()
_cache = {"ts": 0.0, "doc": None}
_last = {"diag": None, "fetched": None, "error": None}


class FetchError(Exception):
    """Käyttäjälle näytettävä virhe (suomeksi)."""


class _Err(Exception):
    def __init__(self, kind, code=None, msg=""):
        super().__init__(msg)
        self.kind, self.code, self.msg = kind, code, msg


def _is_challenge(headers, body):
    b = (body or b"")[:3000].decode("utf-8", "replace").lower()
    mitigated = headers is not None and str(headers.get("cf-mitigated", "")).lower() == "challenge"
    return mitigated or "just a moment" in b or "cf-chl" in b or "challenge-platform" in b or "attention required" in b


def _get_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError:
                raise _Err("challenge" if _is_challenge(r.headers, raw) else "json", None, "vastaus ei ollut JSONia")
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
        except Exception:
            body = b""
        if _is_challenge(e.headers, body):
            raise _Err("challenge", e.code, f"HTTP {e.code} (Cloudflaren selaintarkistus)")
        raise _Err("blocked" if e.code in (403, 429) else "http", e.code, f"HTTP {e.code}")
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise _Err("timeout", None, "aikakatkaisu")
        raise _Err("net", None, str(e.reason))
    except (TimeoutError, OSError) as e:
        raise _Err("timeout", None, str(e) or "aikakatkaisu")


def _fe(err):
    if err.kind == "challenge":
        return FetchError("Epicbet vastasi Cloudflaren selaintarkistuksella (HTTP %s): palvelimen haku ei onnistu. Käytä Epicbet-kirjanmerkkiä selaimessa – "
                          "tarkistusta ei kierretä." % (err.code or "-"))
    if err.kind == "blocked":
        return FetchError(f"Epicbet esti tai rajoitti pyynnön (HTTP {err.code}). Palvelimen haku ei onnistu juuri nyt – käytä Epicbet-kirjanmerkkiä selaimessa.")
    if err.kind == "timeout":
        return FetchError("Epicbetin rajapinta ei vastannut ajoissa. Yritä uudelleen.")
    if err.kind == "net":
        return FetchError(f"Epicbetin rajapintaan ei saatu yhteyttä ({err.msg}).")
    if err.kind == "json":
        return FetchError("Epicbetin vastaus ei ollut JSONia; rajapinta on voinut muuttua.")
    return FetchError(f"Epicbet vastasi virheellä ({err.msg}).")


_state = {"base": None}


def _get(path, inp):
    """Hakee reitin; käyttää ensin sitä osoitetta joka viimeksi toimi. Palauttaa result.data."""
    qs = urllib.parse.quote(json.dumps(inp, separators=(",", ":"), ensure_ascii=False), safe="!'()*")
    bases = ([_state["base"]] if _state["base"] else []) + [b for b in BASES if b != _state["base"]]
    last = None
    for b in bases:
        try:
            j = _get_url(f"{b}/s/core-proxy/public/{path}?input={qs}")
        except _Err as e:
            last = e
            if e.kind in ("challenge", "blocked", "timeout"):
                break           # sama esto koskee www-osoitetta: ei jatketa
            continue
        try:
            data = j["result"]["data"]
        except (KeyError, TypeError):
            last = _Err("json", None, "result.data puuttuu")
            continue
        _state["base"] = b
        return data
    raise last


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _groups_of(d):
    mg = (d or {}).get("marketGroups") or []
    return mg if isinstance(mg, list) else list(mg.values())


def _map_no(s):
    m = re.search(r"(\d)\s*\.?\s*kartta", s or "", re.I)
    return int(m.group(1)) if m else None


def _odds_of(ids):
    out = {}
    for i in range(0, len(ids), 100):
        arr = _get("sport-odds/activeOdds.getPreMatchByMarketIds", ids[i:i + 100])
        for o in arr or []:
            if isinstance(o, dict) and o.get("status") == "open" and _num(o.get("value")):
                out[o.get("outcomeId")] = o["value"]
    return out


def _iso(s):
    return re.sub(r"\+00$", "Z", str(s).replace(" ", "T"))


def _find(outs, rx):
    return next((o for o in outs or [] if re.match(rx, str(o.get("name") or ""), re.I)), None)


def _players_of(m):
    sb = _get("sport-base/match.getSidebets", {"matchId": m["id"], "language": "fi", "country": "FI", "marketType": "players"})
    groups = [g for g in _groups_of(sb) if g.get("viewType") == "PlayerTotals" and re.search("tap", str(g.get("name") or ""), re.I)
              and not SUBKILLS.search(str(g.get("name") or "")) and _map_no(g.get("name"))]
    markets = [(g, mk) for g in groups for mk in (g.get("markets") or [])]
    odds = _odds_of([mk["id"] for _, mk in markets])
    pby = sb.get("playersById") or {}
    players = []
    for g, mk in markets:
        yli, alle = _find(mk.get("outcomes"), r"yli"), _find(mk.get("outcomes"), r"alle")
        p = pby.get(str(mk.get("playerId"))) or pby.get(mk.get("playerId")) or {}
        name = mk.get("playerName") or p.get("name")
        if not name or mk.get("line") is None:
            continue
        ov = odds.get(yli.get("id")) if yli else None
        un = odds.get(alle.get("id")) if alle else None
        if ov is None and un is None:
            continue            # markkina kiinni
        team = m.get("awayTeamName") if p.get("team") == "away" else (m.get("homeTeamName") if p.get("team") == "home" else None)
        players.append({"player": name, "team": team, "map": _map_no(g["name"]), "line": float(mk["line"]), "over": ov, "under": un})
    players.sort(key=lambda x: (x["map"], str(x["team"]).casefold(), x["player"].casefold(), x["line"]))
    return players


def _maps_of(m):
    full = _get("sport-base/match.getSidebets", {"matchId": m["id"], "language": "fi", "country": "FI", "marketType": "all"})
    gs = _groups_of(full)
    ml = [g for g in gs if re.match(r"^\d\.\s*kartta:\s*money line$", str(g.get("name") or "").strip(), re.I)]
    tot = [g for g in gs if re.match(r"^\d\.\s*kartta:\s*kierrokset\s+yhteensä$", str(g.get("name") or "").strip(), re.I)]
    od = _odds_of([x["id"] for g in ml + tot for x in (g.get("markets") or [])])
    maps = {}
    for g in ml:
        n, mks = _map_no(g.get("name")), g.get("markets") or []
        if not n or not mks:
            continue
        outs = mks[0].get("outcomes") or []
        h = next((o for o in outs if o.get("teamId") == m.get("homeTeamId")), None)
        a = next((o for o in outs if o.get("teamId") == m.get("awayTeamId")), None)
        o1, o2 = od.get(h.get("id")) if h else None, od.get(a.get("id")) if a else None
        if o1 and o2:
            maps.setdefault(n, {"map": f"Kartta {n}"}).update({"odds1": o1, "odds2": o2})
    for g in tot:
        n = _map_no(g.get("name"))
        if not n:
            continue
        best = None
        for mk in g.get("markets") or []:
            y, a = _find(mk.get("outcomes"), r"yli"), _find(mk.get("outcomes"), r"alle")
            ov, un = od.get(y.get("id")) if y else None, od.get(a.get("id")) if a else None
            if not ov or not un:
                continue
            gap = abs(ov - un)
            if best is None or gap < best["gap"]:
                best = {"gap": gap, "line": float(mk["line"]), "over": ov, "under": un}
        if best:
            maps.setdefault(n, {"map": f"Kartta {n}"}).update({"total_line": best["line"], "over_odds": best["over"], "under_odds": best["under"]})
    return [maps[k] for k in sorted(maps)]


def fetch_cs2(with_maps=True):
    """Hakee tuoreen datan (ei välimuistia). Heittää FetchError jos Epicbet estää / ei vastaa."""
    diag = {"errors": [], "matches": []}
    _last["diag"], _last["fetched"], _last["error"] = diag, None, None
    try:
        lst = _get("sport-base/match.hotByCategory", {"language": "fi", "country": "FI", "foCategoryId": CS2, "period": "all", "hasStream": False,
                                                       "limit": 130, "topMarketTypeCount": 2, "shouldOrderByPopularity": True, "isBetaEnabled": False})
    except _Err as e:
        _last["error"] = str(_fe(e))
        diag["errors"].append({"path": "match.hotByCategory", "kind": e.kind, "err": e.msg})
        raise _fe(e)
    allm = (lst or {}).get("matches") or []
    wanted = [m for m in allm if m.get("hasPlayersMarkets")]

    def load(m):
        try:
            return m, _players_of(m), (_maps_of(m) if with_maps else []), None
        except _Err as e:
            return m, None, None, e

    with ThreadPoolExecutor(max_workers=3) as ex:
        loaded = list(ex.map(load, wanted))
    matches, n_ok = [], 0
    for m, players, maps, err in loaded:
        if err is not None:
            diag["errors"].append({"path": f"ottelu {m.get('id')}", "kind": err.kind, "err": err.msg})
            continue
        n_ok += 1
        diag["matches"].append({"id": m.get("id"), "name": m.get("name"), "lines": len(players), "maps": len(maps)})
        matches.append({"team1": m.get("homeTeamName"), "team2": m.get("awayTeamName"), "tournament": m.get("leagueName"), "start": _iso(m.get("startDate")),
                        "source": "epicbet", "maps": maps, "players": players})
    if wanted and not n_ok:
        e = next(x[3] for x in loaded if x[3] is not None)
        _last["error"] = str(_fe(e))
        raise _fe(e)
    upcoming = sorted([{"name": m.get("name"), "tournament": m.get("leagueName"), "start": _iso(m.get("startDate"))} for m in allm if not m.get("hasPlayersMarkets")],
                      key=lambda x: x["start"])[:8]
    now_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    _last["fetched"] = now_iso
    return {"source": "epicbet", "fetched": now_iso, "cs2_matches_total": len(allm), "matches_with_player_markets": len(wanted),
            "matches": matches, "upcoming": upcoming, "diag": diag}


def get_cs2(max_age=CACHE_SECONDS):
    """fetch_cs2 lyhyellä välimuistilla; yksi haku kerrallaan."""
    with _lock:
        if _cache["doc"] is not None and time.time() - _cache["ts"] < max_age:
            return _cache["doc"]
        doc = fetch_cs2()
        _cache["doc"], _cache["ts"] = doc, time.time()
        return doc


def last_diag():
    d = _last["diag"]
    if d is None:
        return {"info": "Hakua ei ole vielä tehty tällä palvelimella (paina Hae Epicbet-kertoimet)."}
    return {**d, "fetched": _last["fetched"], "error": _last["error"]}
