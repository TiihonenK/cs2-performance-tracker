"""Unibetin CS2-pelaajalinjat palvelimelta (Python-versio kirjanmerkistä tools/unibet_cs2_src.js).

Unibet käyttää Kambi-tarjontarajapintaa (offering "ubca"). Sama julkinen JSON jota sivu itse lataa: ei evästeitä, ei kirjautumista.
Haetaan:
  1) CS2-ottelulista: group.json -> CS-ryhmä -> betoffer/group/{id}.json (tai event/group/{id}.json), varalla listView-arvaukset
  2) jokaiselle ottelulle (joilla on tarpeeksi markkinoita) betoffer/event/{id}.json -> "Map N - Total Kills by the Player"
Kertoimet ja viivat ovat tuhannesosina (2750 = 2,75; viiva 18500 = 18,5).

Palauttaa saman JSON-rakenteen kuin kirjanmerkki (source "unibet"; pelaajalinjat kartoittain, maps = []).
Jos Unibet/Kambi estää pyynnön (403/429, esim. bottisuojaus) haku epäonnistuu selkeään virheeseen — suojausta EI kierretä;
silloin käytetään kirjanmerkkiä selaimessa.

Pyyntöjä: 1-4 listaukseen + 1 / ottelu (max 25), 3 rinnakkain. Tulos välimuistissa 30 s.
Ympäristömuuttuja UNIBET_API_BASE (esim. http://127.0.0.1:9000/offering/v2018/ubca/) ylikirjoittaa osoitteet (testeille).
"""
import gzip
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

OFFER = "ubca"
_env = os.environ.get("UNIBET_API_BASE")
BASES = [_env if _env.endswith("/") else _env + "/"] if _env else [
    f"https://eu1.offering-api.kambicdn.com/offering/v2018/{OFFER}/",
    f"https://eu.offering-api.kambicdn.com/offering/v2018/{OFFER}/",
]
QS = "lang=fi_FI&market=FI&channel_id=1&client_id=2"
UA = "vetomalli/1.0 (oma vetomalli; python-urllib)"
TIMEOUT = 12
CACHE_SECONDS = 30
KILL_EN = re.compile(r"^Map\s*(\d)\s*-\s*Total Kills by (?:the )?Player$", re.I)      # "Map 1 - Total Kills by the Player"
KILL_FI = re.compile(r"^Kartta\s*(\d)\s*-\s*tappoja yhteensä$", re.I)                 # joukkueen tapot: "Kartta 1 - Joukkueen X tappoja yhteensä"
MIN_BO = 50            # ottelu, jolla on alle näin monta ennakkomarkkinaa, ei vielä sisällä pelaajamarkkinoita
MAX_EVENTS = 25
NO_COUNT_EVENTS = 15   # jos listaus ei kerro markkinoiden määrää: haetaan näin monta lähintä ottelua
GUESS_ROUTES = ["listView/esports/counter_strike/all/all/matches.json",
                "listView/counter_strike/all/all/all/matches.json",
                "listView/esports/counter_strike.json"]

_lock = threading.Lock()
_cache = {"ts": 0.0, "doc": None}
_last = {"diag": None, "fetched": None, "error": None}      # viimeisin haku (myös epäonnistunut) -> GET /hae/unibet/diag


class FetchError(Exception):
    """Käyttäjälle näytettävä virhe (suomeksi)."""


class _Err(Exception):
    """Yksittäisen pyynnön virhe: kind = blocked | http | net | timeout | json."""

    def __init__(self, kind, code=None, msg=""):
        super().__init__(msg)
        self.kind, self.code, self.msg = kind, code, msg


def _get_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise _Err("blocked" if e.code in (403, 429) else "http", e.code, f"HTTP {e.code}")
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise _Err("timeout", None, "aikakatkaisu")
        raise _Err("net", None, str(e.reason))
    except (TimeoutError, OSError) as e:
        raise _Err("timeout", None, str(e) or "aikakatkaisu")
    except ValueError:
        raise _Err("json", None, "vastaus ei ollut JSONia")


def _get_rel(rel):
    """Kokeilee osoitteita järjestyksessä; palauttaa ensimmäisen onnistuneen. Heittää viimeisen virheen."""
    last = None
    for base in BASES:
        url = f"{base}{rel}{'&' if '?' in rel else '?'}{QS}&ncid={int(time.time() * 1000)}"
        try:
            return _get_url(url)
        except _Err as e:
            last = e
            if e.kind == "http" and e.code == 404:      # polkua ei ole -> toinen palvelin ei auta
                break
    raise last


def _fe(err):
    """_Err -> käyttäjälle näytettävä FetchError."""
    if err.kind == "blocked":
        return FetchError(f"Unibet esti tai rajoitti pyynnön (HTTP {err.code}). Palvelimen haku ei onnistu juuri nyt – käytä Unibet-kirjanmerkkiä selaimessa.")
    if err.kind == "timeout":
        return FetchError("Unibetin rajapinta ei vastannut ajoissa. Yritä uudelleen.")
    if err.kind == "net":
        return FetchError(f"Unibetin rajapintaan ei saatu yhteyttä ({err.msg}).")
    if err.kind == "json":
        return FetchError("Unibetin vastaus ei ollut JSONia; rajapinta on voinut muuttua.")
    return FetchError(f"Unibet vastasi virheellä ({err.msg}).")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _ev_of(x):
    return x["event"] if isinstance(x, dict) and isinstance(x.get("event"), dict) else x


def _events_of(j):
    arr = (j.get("events") or j.get("liveEvents") or []) if isinstance(j, dict) else []
    out = []
    for x in arr:
        e = _ev_of(x)
        if isinstance(e, dict) and e.get("id"):
            out.append(e)
    return out


def _is_cs(e):
    if e.get("sport") == "COUNTER_STRIKE":
        return True
    return any(isinstance(p, dict) and (p.get("termKey") == "counter_strike" or re.search("counter", str(p.get("englishName") or p.get("name") or ""), re.I))
               for p in e.get("path") or [])


def _upcoming_cs(lst):
    seen, out = set(), []
    for e in lst:
        if not e or not e.get("id") or e["id"] in seen or not _is_cs(e) or (e.get("state") and e["state"] not in ("NOT_STARTED", "STARTED")):
            continue
        seen.add(e["id"])
        out.append(e)
    return out


def _find_cs_groups(g, found):
    if not isinstance(g, dict):
        return
    if g.get("sport") == "COUNTER_STRIKE" or g.get("termKey") == "counter_strike" or re.match(r"counter-?strike", str(g.get("englishName") or g.get("name") or ""), re.I):
        found.append(g)
        return
    for x in g.get("groups") or []:
        _find_cs_groups(x, found)


def _strip_side(lab):
    lab = re.sub(r"^(over|under|yli|alle)\s*", "", lab, flags=re.I)
    lab = re.sub(r"[\d.,]+\s*$", "", lab)
    return re.sub(r"^[\s\-–:]+|[\s\-–:]+$", "", lab)


def _parse_event(det, diag):
    """Yhden ottelun betoffer-vastaus -> pelaajalinjat [{player, team, map, line, over, under}] (sama logiikka kuin kirjanmerkissä)."""
    rows, order = {}, []
    for bo in det.get("betOffers") or []:
        cr = bo.get("criterion") or {}
        en, fi = str(cr.get("englishLabel") or "").strip(), str(cr.get("label") or "").strip()
        if re.search(r"kill|tapp", en + " " + fi, re.I):
            lk = en or fi
            if lk in diag["labels"] or len(diag["labels"]) < 60:
                diag["labels"][lk] = diag["labels"].get(lk, 0) + 1
        m = KILL_EN.match(en) or KILL_FI.match(fi)
        if not m:
            continue
        if len(diag["raw"]) < 3:
            diag["raw"].append(bo)
        for o in bo.get("outcomes") or []:
            lab = str(o.get("englishLabel") or o.get("label") or "")
            side = None
            if o.get("type") == "OT_OVER" or re.match(r"(over|yli)\b", lab, re.I):
                side = "over"
            elif o.get("type") == "OT_UNDER" or re.match(r"(under|alle)\b", lab, re.I):
                side = "under"
            if not side:
                continue
            name = str(o.get("participant") or bo.get("participant") or "").strip()
            team = None
            if not name:
                name = _strip_side(lab)
            pm = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", name)
            if pm:
                name, team = pm.group(1).strip(), pm.group(2).strip()
            if not name or re.match(r"^(over|under|yli|alle)$", name, re.I) or not _num(o.get("line")):
                if len(diag["skipped"]) < 5:
                    diag["skipped"].append({"label": lab, "participant": o.get("participant"), "line": o.get("line")})
                continue
            line = o["line"] / 1000
            key = f"{name.lower()}|{m.group(1)}|{line}"
            r = rows.get(key)
            if r is None:
                r = rows[key] = {"player": name, "team": team, "map": int(m.group(1)), "line": line, "over": None, "under": None}
                order.append(key)
            if (not o.get("status") or o["status"] == "OPEN") and _num(o.get("odds")) and o["odds"] > 1000:
                r[side] = o["odds"] / 1000
    return [rows[k] for k in order if rows[k]["over"] is not None or rows[k]["under"] is not None]


def _discover(diag):
    """Löytää CS2-ottelut: group.json -> CS-ryhmän ottelulistat, sitten arvattavat listView-polut."""
    cands = []
    try:
        gj = _get_rel("group.json")
        found = []
        _find_cs_groups(gj.get("group") if isinstance(gj, dict) and gj.get("group") else gj, found)
        for g in found[:2]:
            cands.append({"how": f"ryhmä {g.get('id')}", "rel": f"betoffer/group/{g.get('id')}.json"})
            cands.append({"how": f"ryhmä {g.get('id')} (event)", "rel": f"event/group/{g.get('id')}.json"})
        diag["cs_groups"] = [[g.get("id"), g.get("name"), g.get("termKey"), g.get("eventCount")] for g in found]
    except _Err as e:
        diag["errors"].append({"path": "group.json", "kind": e.kind, "err": e.msg})
        diag["_errs"].append(e)
        if e.kind == "blocked":      # esto koskee koko palvelinta: ei jatketa muiden polkujen kokeilua
            _last["error"] = str(_fe(e))
            raise _fe(e)
    for p in GUESS_ROUTES:
        cands.append({"how": "arvaus", "rel": p})
    events, n_ok = [], 0
    for c in cands:
        if events:
            break
        try:
            evs = _upcoming_cs(_events_of(_get_rel(c["rel"])))
            n_ok += 1
            diag["attempts"].append({"how": c["how"], "path": c["rel"], "ok": True, "events": len(evs)})
            if evs:
                events = evs
        except _Err as e:
            diag["attempts"].append({"how": c["how"], "path": c["rel"], "ok": False, "err": e.msg})
            diag["_errs"].append(e)
    # käynnissä olevat ottelut voivat puuttua ennakkolistasta: Kambin live-lista täydentää (virheet ohitetaan)
    try:
        lv = [e for e in _upcoming_cs(_events_of(_get_rel("event/live/open.json"))) if e.get("state") == "STARTED"]
        diag["attempts"].append({"how": "live", "path": "event/live/open.json", "ok": True, "events": len(lv)})
        have = {e["id"] for e in events}
        events += [e for e in lv if e["id"] not in have]
        n_ok += 1 if lv else 0
    except _Err as e:
        diag["attempts"].append({"how": "live", "path": "event/live/open.json", "ok": False, "err": e.msg})
    return events, n_ok


def fetch_cs2():
    """Hakee tuoreen datan (ei välimuistia). Palauttaa dokumentin: dict(source, fetched, matches, upcoming, diag, ...)."""
    diag = {"attempts": [], "labels": {}, "raw": [], "skipped": [], "events": [], "errors": [], "_errs": []}
    _last["diag"], _last["fetched"], _last["error"] = diag, None, None
    now_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    events, n_ok = _discover(diag)
    if not events and not n_ok and diag["_errs"]:
        # ei yhtään onnistunutta pyyntöä: esto / verkko-ongelma -> selkeä virhe (404-polut sen sijaan jäävät "ei löytynyt" -tilaan)
        errs = diag["_errs"]
        pick = next((e for e in errs if e.kind == "blocked"), None) or next((e for e in errs if e.kind in ("net", "timeout", "json")), None)
        if pick:
            _last["error"] = str(_fe(pick))
            raise _fe(pick)
    events.sort(key=lambda e: str(e.get("start") or ""))
    with_count = any(_num(e.get("nonLiveBoCount")) for e in events)
    live = [e for e in events if e.get("state") == "STARTED"]       # käynnissä olevat: haetaan aina (live-markkinoiden määrä ei näy nonLiveBoCountissa)
    pre = [e for e in events if e.get("state") != "STARTED"]
    todo = live + ([e for e in pre if (e.get("nonLiveBoCount") or 0) >= MIN_BO][:MAX_EVENTS] if with_count else pre[:NO_COUNT_EVENTS])

    def load(ev):
        try:
            return ev, _get_rel(f"betoffer/event/{ev['id']}.json?includeParticipants=true"), None
        except _Err as e:
            return ev, None, e

    with ThreadPoolExecutor(max_workers=3) as ex:
        loaded = list(ex.map(load, todo))

    matches, n_det_ok = [], 0
    for ev, det, err in loaded:
        if err is not None:
            diag["errors"].append({"path": f"event {ev['id']}", "kind": err.kind, "err": err.msg})
            diag["_errs"].append(err)
            continue
        n_det_ok += 1
        e2 = (_events_of(det) or [ev])[0]
        nm = [e2.get("homeName"), e2.get("awayName")]
        if not nm[0] or not nm[1]:
            nm = re.split(r"\s+-\s+", str(e2.get("name") or ev.get("name") or ""))
            nm = (nm + ["", ""])[:2]
        players = _parse_event(det, diag)
        diag["events"].append({"id": ev["id"], "name": ev.get("name"), "offers": len(det.get("betOffers") or []), "lines": len(players)})
        if players:
            players.sort(key=lambda p: (p["map"], p["player"].casefold(), p["line"]))
            matches.append({"team1": nm[0], "team2": nm[1], "tournament": e2.get("group") or ev.get("group"), "start": e2.get("start") or ev.get("start"),
                            "source": "unibet", "maps": [], "players": players,
                            **({"live": True} if (e2.get("state") or ev.get("state")) == "STARTED" else {})})
    if todo and not n_det_ok:
        errs = [x for x in diag["_errs"] if x.kind != "http" or x.code != 404] or diag["_errs"]
        pick = next((e for e in errs if e.kind == "blocked"), None) or errs[-1]
        _last["error"] = str(_fe(pick))
        raise _fe(pick)
    matches.sort(key=lambda m: str(m["start"] or ""))
    upcoming = [{"name": e.get("name"), "tournament": e.get("group"), "start": e.get("start"), "bo": e.get("nonLiveBoCount")} for e in events[:8]]
    diag["cs_events"], diag["fetched_events"] = len(events), len(todo)
    diag.pop("_errs", None)
    _last["fetched"] = now_iso
    return {"source": "unibet", "fetched": now_iso, "cs2_matches_total": len(events), "matches_with_player_markets": len(matches),
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
    """Viimeisimmän haun diagnostiikka (GET /hae/unibet/diag): reitit, kill-markkinoiden nimet, näyte raakadatasta, virheet."""
    d = _last["diag"]
    if d is None:
        return {"info": "Hakua ei ole vielä tehty tällä palvelimella (paina Hae Unibet-kertoimet)."}
    out = {k: v for k, v in d.items() if k != "_errs"}
    out["fetched"], out["error"] = _last["fetched"], _last["error"]
    return out
