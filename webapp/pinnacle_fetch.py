"""Pinnaclen CS2-karttakertoimet palvelimelta (Python-versio kirjanmerkistä tools/pinnacle_src.js).

Hakee Pinnaclen julkisesta vieras-rajapinnasta (ei avainta, ei kirjautumista) kaikkien CS2-otteluiden karttojen
voittajakertoimet ja kierrostotalit, ja palauttaa saman JSON-rakenteen kuin kirjanmerkki (source "pinnacle").

Rajapinta (tarkistettu 8.11.2026, myös Pi:ltä):
  sports -> E Sports (id 12); sports/12/leagues?all=false -> CS2-liigat (nimi alkaa "CS2");
  leagues/{id}/matchups -> ottelut; leagues/{id}/markets/straight -> markkinat.
  period 0 = koko ottelu, period N = kartta N. Kertoimet amerikkalaisia -> desimaaliksi katkaistuna 3 desimaaliin kuten Pinnaclen sivulla.
Bo1: ei kartta-jaksoja -> period 0 on Kartta 1, kun bestOfX = 1 tai ottelutason totalin viiva on kierrosluku (> 8). OLETUS, ei varmistettu.

Pyynnöt: ~ 2 + 2 x CS2-liigat kpl, pieni rinnakkaisuus. Tulos välimuistissa 30 s (nappia voi painaa uudelleen kuormittamatta).
Ympäristömuuttuja PINNACLE_API_BASE ylikirjoittaa osoitteen (testeille).
"""
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

BASE = os.environ.get("PINNACLE_API_BASE", "https://guest.api.arcadia.pinnacle.com/0.1/")
UA = "vetomalli/1.0 (oma vetomalli; python-urllib)"
TIMEOUT = 12
CACHE_SECONDS = 30

_lock = threading.Lock()
_cache = {"ts": 0.0, "doc": None}


class FetchError(Exception):
    """Käyttäjälle näytettävä virhe (suomeksi)."""


def _get(path):
    url = BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise FetchError(f"Pinnacle esti tai rajoitti pyynnön (HTTP {e.code}). Yritä myöhemmin uudelleen tai käytä kirjanmerkkiä.")
        raise FetchError(f"Pinnacle vastasi virheellä (HTTP {e.code}, {path}).")
    except urllib.error.URLError as e:
        raise FetchError(f"Pinnaclen rajapintaan ei saatu yhteyttä ({e.reason}).")
    except (TimeoutError, OSError) as e:
        raise FetchError(f"Pinnaclen rajapinta ei vastannut ajoissa ({e}).")
    except ValueError:
        raise FetchError(f"Pinnaclen vastaus ei ollut JSONia ({path}); rajapinta on voinut muuttua.")


def dec(p):
    """Amerikkalainen kerroin -> desimaali, katkaistu kolmeen desimaaliin (-104 -> 1.961, kuten Pinnaclen sivulla)."""
    if isinstance(p, bool) or not isinstance(p, (int, float)) or p == 0 or p != p:
        return None
    if p > 0:
        return math.floor((100 + p) * 10) / 1000
    a = -p
    return math.floor(1000 * (a + 100) / a) / 1000


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _price(mk, des):
    for p in (mk or {}).get("prices") or []:
        if p.get("designation") == des:
            return dec(p.get("price"))
    return None


def _over_points(x):
    for p in x.get("prices") or []:
        if p.get("designation") == "over":
            return p.get("points")
    return None


def _open(mk):
    return bool(mk) and mk.get("status") == "open"


def _build_map(n, lst, min_pts):
    """Yhden kartan tiedot markkinalistasta: voittajakertoimet + kierrostotal (pääviiva, muuten lähinnä tasakertoimia)."""
    ml = next((x for x in lst if x.get("type") == "moneyline"), None)
    o1, o2 = _price(ml, "home"), _price(ml, "away")
    tots = [x for x in lst if x.get("type") == "total" and _price(x, "over") and _price(x, "under")
            and _num(_over_points(x)) and _over_points(x) > min_pts]
    main = [x for x in tots if not x.get("isAlternate")]
    best = None
    for x in (main or tots):
        ov, un = _price(x, "over"), _price(x, "under")
        gap = abs(ov - un)
        if best is None or gap < best["gap"]:
            best = {"gap": gap, "line": _over_points(x), "over": ov, "under": un}
    mp = {"map": f"Kartta {n}"}
    if o1 and o2:
        mp["odds1"], mp["odds2"] = o1, o2
    if best:
        mp["total_line"], mp["over_odds"], mp["under_odds"] = best["line"], best["over"], best["under"]
    return mp if ("odds1" in mp or "total_line" in mp) else None


def _tournament(name):
    return re.sub(r"^CS2\s*-\s*", "", name or "", flags=re.I)


def fetch_cs2():
    """Hakee tuoreen datan rajapinnasta (ei välimuistia). Palauttaa dokumentin: dict(source, fetched, matches, upcoming, ...)."""
    sports = _get("sports")
    if not isinstance(sports, list):
        raise FetchError("Pinnaclen sports-vastauksen rakenne on muuttunut.")
    es = next((s for s in sports if re.search(r"e-?sport", str(s.get("name") or ""), re.I)), None)
    leagues = _get(f"sports/{es['id'] if es else 12}/leagues?all=false")
    if not isinstance(leagues, list):
        raise FetchError("Pinnaclen liigalistan rakenne on muuttunut.")
    cs = [l for l in leagues if re.search(r"^cs2\b|counter-?strike", str(l.get("name") or ""), re.I) and (l.get("matchupCount") or 0) > 0]

    def load(l):
        return l, _get(f"leagues/{l['id']}/matchups"), _get(f"leagues/{l['id']}/markets/straight")

    with ThreadPoolExecutor(max_workers=4) as ex:
        per = list(ex.map(load, cs))

    matches, skipped, total = [], [], 0
    for l, mu, mk in per:
        if not isinstance(mu, list) or not isinstance(mk, list):
            raise FetchError("Pinnaclen ottelu- tai markkinalistan rakenne on muuttunut.")
        by_mu = {}
        for x in mk:
            by_mu.setdefault(x.get("matchupId"), {}).setdefault(x.get("period"), []).append(x)
        for m in mu:
            if m.get("type") != "matchup" or m.get("parentId") is not None or m.get("parent") or m.get("isLive"):
                continue
            parts = m.get("participants") or []
            home = next((p for p in parts if p.get("alignment") == "home"), None)
            away = next((p for p in parts if p.get("alignment") == "away"), None)
            if not home or not away:
                continue
            total += 1
            periods = by_mu.get(m.get("id")) or {}
            maps = []
            for n in sorted(k for k in periods if isinstance(k, int) and k >= 1):
                mp = _build_map(n, [x for x in periods[n] if _open(x)], 0)
                if mp:
                    maps.append(mp)
            bo1 = False
            if not maps and 0 in periods:
                p0 = [x for x in periods[0] if _open(x)]
                has_rounds = any(x.get("type") == "total" and _num(_over_points(x)) and _over_points(x) > 8 for x in p0)
                if m.get("bestOfX") == 1 or has_rounds:
                    mp = _build_map(1, p0, 8)
                    if mp:
                        maps.append(mp)
                        bo1 = True
            tour = _tournament(l.get("name"))
            if not maps:
                skipped.append({"name": f"{home.get('name')} - {away.get('name')}", "tournament": tour, "start": m.get("startTime"),
                                "best_of": m.get("bestOfX"),
                                "markets": " ".join(f"p{k}:" + "+".join(str(x.get("type")) for x in periods[k])
                                                    for k in sorted(k for k in periods if isinstance(k, int)))})
                continue
            item = {"team1": home.get("name"), "team2": away.get("name"), "tournament": tour, "start": m.get("startTime"), "source": "pinnacle"}
            if bo1:
                item["best_of"] = 1
            item["maps"], item["players"] = maps, []
            matches.append(item)
    matches.sort(key=lambda x: x["start"] or "")
    skipped.sort(key=lambda x: x["start"] or "")
    return {"source": "pinnacle", "fetched": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "cs2_leagues": len(cs), "cs2_matches_total": total, "matches_with_map_markets": len(matches),
            "matches": matches, "upcoming": skipped[:8]}


def get_cs2(max_age=CACHE_SECONDS):
    """fetch_cs2 lyhyellä välimuistilla; yksi haku kerrallaan."""
    with _lock:
        if _cache["doc"] is not None and time.time() - _cache["ts"] < max_age:
            return _cache["doc"]
        doc = fetch_cs2()
        _cache["doc"], _cache["ts"] = doc, time.time()
        return doc
