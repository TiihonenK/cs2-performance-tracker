#!/usr/bin/env python3
"""Kertoimien haun yhteystesti: pääseekö tästä koneesta Pinnaclen, Epicbetin ja Unibetin CS2-rajapintoihin?

Aja siellä mistä haku halutaan tehdä (Raspberry Pi):   python3 api_testi.py   (vain Unibet: --only unibet)
- Vain Pythonin vakiokirjasto, ei asennettavaa. Python 3.6+.
- Tekee noin 20 GET-pyyntöä (Pinnacle ~6, Epicbet ~4, Unibet ~10), pieni tauko pyyntöjen välillä.
- Lukee vain julkisia kerroin-JSONeja. Ei kirjautumista, ei evästeitä, ei lähetä mitään muualle kuin näihin kolmeen palveluun.
- Tulostaa lopuksi yhteenvedon ja mahdollisen syyn jos haku ei onnistu. Poistumiskoodi 0 = kaikki OK, 1 = jokin ei toimi.
- Unibet: testaa myös mikä ottelulistan reitti toimii ja tulostaa pelaajan tappolinjan rakenteen (kenttien nimet),
  jotta palvelinpuolen haku voidaan rakentaa varmistetun rakenteen päälle.
"""
import argparse
import json
import platform
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "vetomalli-apitest/1.0 (python-urllib)"
CS2_EPIC = 258534
UNI_BASES = ["https://eu1.offering-api.kambicdn.com/offering/v2018/ubca/", "https://eu.offering-api.kambicdn.com/offering/v2018/ubca/"]
UNI_QS = "lang=fi_FI&market=FI&channel_id=1&client_id=2"
UNI_KILL = re.compile(r"^Map\s*(\d)\s*-\s*Total Kills by (?:the )?Player$", re.I)
UNI_KILL_FI = re.compile(r"^Kartta\s*(\d)\s*-\s*tappoja yhteensä$", re.I)


def fetch(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return dict(status=r.status, body=r.read(), headers=r.headers, secs=time.time() - t0, error=None)
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
        except Exception:
            body = b""
        return dict(status=e.code, body=body, headers=e.headers, secs=time.time() - t0, error=None)
    except Exception as e:          # DNS, aikakatkaisu, TLS, yhteys katkesi ...
        return dict(status=None, body=b"", headers={}, secs=time.time() - t0, error="%s: %s" % (type(e).__name__, e))


def hint(res):
    """Selkokielinen syy ja ehdotus, kun pyyntö ei onnistunut."""
    st, body = res["status"], res["body"][:2000].decode("utf-8", "replace").lower()
    if st is None:
        return "Yhteys ei onnistunut (%s). Tarkista verkko/DNS; aikakatkaisu voi tarkoittaa palomuuria tai IP-estoa." % res["error"]
    if "just a moment" in body or "cf-chl" in body or "challenge-platform" in body or "attention required" in body:
        return "Cloudflaren tarkistussivu: palvelu vaatii selaimen (ei pelkkä HTTP-pyyntö). Tarvittaisiin FlareSolverr tai selainpohjainen haku."
    if st in (401,):
        return "HTTP 401: rajapinta vaatii avaimen tai kirjautumisen."
    if st == 403:
        return "HTTP 403: pääsy estetty (bot-/IP-/maaesto tai puuttuva otsake)."
    if st == 429:
        return "HTTP 429: liikaa pyyntöjä (rajoitus). Kokeile myöhemmin."
    if st == 451:
        return "HTTP 451: estetty alueellisesti."
    if st >= 500:
        return "HTTP %d: palvelun virhe tai huolto." % st
    if st == 200:
        return "Vastaus ei ollut odotettua JSONia (ehkä HTML-sivu)."
    return "HTTP %s." % st


def step(label, url, timeout, pause=0.3):
    """Hakee url:n, tulostaa rivin, palauttaa (json-data tai None, ongelmateksti tai None, vastaus)."""
    time.sleep(pause)
    res = fetch(url, timeout)
    size = "%.1f kB" % (len(res["body"]) / 1024.0)
    data = None
    if res["status"] == 200:
        try:
            data = json.loads(res["body"].decode("utf-8"))
        except ValueError:
            data = None
    ok = data is not None
    st = res["status"] if res["status"] is not None else "-"
    print("  %-34s HTTP %-4s %-9s %.2f s  %s" % (label, st, size, res["secs"], "OK" if ok else "VIRHE"))
    problem = None if ok else hint(res)
    if problem:
        print("      -> " + problem)
    return data, problem, res


def server_info(res):
    try:
        s = res["headers"].get("Server")
        return s
    except Exception:
        return None


# ---------------------------------------------------------------- Pinnacle
def test_pinnacle(base, timeout):
    print("\n[Pinnacle] %s" % base)
    out = dict(ok=False, note="")
    sports, prob, res = step("sports", base + "sports", timeout)
    if not isinstance(sports, list):
        out["note"] = prob or "sports-haku ei palauttanut listaa"
        return out
    es = next((s for s in sports if re.search(r"e-?sports?", str(s.get("name", "")), re.I)), None)
    sid = es["id"] if es else 12
    leagues, prob, _ = step("sports/%s/leagues" % sid, base + "sports/%s/leagues?all=false" % sid, timeout)
    if not isinstance(leagues, list):
        out["note"] = prob or "liigahaku ei palauttanut listaa"
        return out
    cs = [l for l in leagues if str(l.get("name", "")).upper().startswith("CS2") and (l.get("matchupCount") or 0) > 0]
    print("      CS2-liigoja nyt: %d" % len(cs))
    n_matches = n_maps = 0
    for l in cs[:3]:
        mu, prob, _ = step("leagues/%s/matchups" % l["id"], base + "leagues/%s/matchups" % l["id"], timeout)
        mk, prob2, _ = step("leagues/%s/markets/straight" % l["id"], base + "leagues/%s/markets/straight" % l["id"], timeout)
        if not isinstance(mu, list) or not isinstance(mk, list):
            out["note"] = prob or prob2 or "ottelu- tai markkinahaku epäonnistui"
            return out
        with_maps = set(x.get("matchupId") for x in mk if isinstance(x.get("period"), int) and x["period"] >= 1)
        tops = [m for m in mu if m.get("type") == "matchup" and not m.get("parentId") and not m.get("isLive")]
        n_matches += len(tops)
        n_maps += len([m for m in tops if m.get("id") in with_maps])
    out["ok"] = True
    out["note"] = "CS2-liigoja %d, otteluita %d (tarkistetut liigat), karttamarkkinoilla %d" % (len(cs), n_matches, n_maps)
    if not cs:
        out["note"] += " – nyt ei ole CS2-otteluita, mutta rajapinta vastaa"
    return out


# ---------------------------------------------------------------- Epicbet
def epic_url(base, path, inp):
    return "%s/s/core-proxy/public/%s?input=%s" % (base, path, urllib.parse.quote(json.dumps(inp, separators=(",", ":")), safe=""))


def test_epicbet(bases, timeout):
    out = dict(ok=False, note="")
    inp = dict(language="fi", country="FI", foCategoryId=CS2_EPIC, period="all", hasStream=False, limit=130,
               topMarketTypeCount=2, shouldOrderByPopularity=True, isBetaEnabled=False)
    base = data = None
    last_problem = None
    for b in bases:
        print("\n[Epicbet] %s" % b)
        d, prob, res = step("match.hotByCategory", epic_url(b, "sport-base/match.hotByCategory", inp), timeout)
        if d is not None:
            base, data = b, d
            break
        last_problem = prob
    if data is None:
        out["note"] = last_problem or "ottelulista ei vastannut"
        return out
    try:
        matches = data["result"]["data"]["matches"]
    except (KeyError, TypeError):
        out["note"] = "Vastauksen rakenne on muuttunut (result.data.matches puuttuu)."
        return out
    wanted = [m for m in matches if m.get("hasPlayersMarkets")]
    print("      CS2-otteluita %d, joista pelaajamarkkinoilla %d" % (len(matches), len(wanted)))
    if not matches:
        out["ok"] = True
        out["note"] = "rajapinta vastaa, mutta CS2-otteluita ei nyt listalla"
        return out
    m = wanted[0] if wanted else matches[0]
    mtype = "players" if wanted else "all"
    sb, prob, _ = step("match.getSidebets (%s)" % mtype, epic_url(base, "sport-base/match.getSidebets",
                       dict(matchId=m["id"], language="fi", country="FI", marketType=mtype)), timeout)
    if sb is None:
        out["note"] = prob or "getSidebets epäonnistui"
        return out
    try:
        d = sb["result"]["data"]
        groups = d["marketGroups"]
        groups = groups if isinstance(groups, list) else list(groups.values())
    except (KeyError, TypeError):
        out["note"] = "getSidebets-vastauksen rakenne on muuttunut (marketGroups puuttuu)."
        return out
    ids = [mk["id"] for g in groups for mk in (g.get("markets") or [])][:20]
    print("      markkinaryhmiä %d, kokeillaan %d markkinan kertoimia" % (len(groups), len(ids)))
    if not ids:
        out["ok"] = True
        out["note"] = "ottelulista ja markkinat vastaavat (ei markkinoita tälle ottelulle juuri nyt)"
        return out
    od, prob, _ = step("activeOdds.getPreMatchByMarketIds", epic_url(base, "sport-odds/activeOdds.getPreMatchByMarketIds", ids), timeout)
    if od is None:
        out["note"] = prob or "kerroinhaku epäonnistui"
        return out
    try:
        arr = od["result"]["data"]
        n_open = len([o for o in arr if o and o.get("status") == "open" and isinstance(o.get("value"), (int, float))])
    except (KeyError, TypeError):
        out["note"] = "kerroinvastauksen rakenne on muuttunut."
        return out
    out["ok"] = True
    out["note"] = "CS2-otteluita %d, pelaajamarkkinoilla %d; kertoimia haettu (%d avointa %d:sta)" % (len(matches), len(wanted), n_open, len(ids))
    return out


# ---------------------------------------------------------------- Unibet (Kambi)
def uni_url(base, rel):
    return "%s%s%s%s&ncid=%d" % (base, rel, "&" if "?" in rel else "?", UNI_QS, int(time.time() * 1000))


def uni_events(j):
    """Ottelut vastauksesta: sekä {events:[{...}]} että käärity {events:[{event:{...}, betOffers:[]}]}."""
    arr = (j.get("events") or j.get("liveEvents") or []) if isinstance(j, dict) else []
    out = []
    for x in arr:
        e = x.get("event") if isinstance(x, dict) and isinstance(x.get("event"), dict) else x
        if isinstance(e, dict) and e.get("id"):
            out.append(e)
    return out


def uni_cs_upcoming(evs):
    out, seen = [], set()
    for e in evs:
        if e["id"] in seen:
            continue
        paths = e.get("path") or []
        cs = e.get("sport") == "COUNTER_STRIKE" or any(
            isinstance(p, dict) and (p.get("termKey") == "counter_strike" or re.search("counter", str(p.get("englishName") or p.get("name") or ""), re.I))
            for p in paths)
        if cs and (not e.get("state") or e.get("state") == "NOT_STARTED"):
            seen.add(e["id"])
            out.append(e)
    return out


def uni_cs_groups(gj):
    """Ylimmät CS-ryhmät group.json-puusta."""
    found = []

    def walk(g):
        if not isinstance(g, dict):
            return
        if g.get("sport") == "COUNTER_STRIKE" or g.get("termKey") == "counter_strike" or re.match(r"counter-?strike", str(g.get("englishName") or g.get("name") or ""), re.I):
            found.append(g)
            return
        for c in g.get("groups") or []:
            walk(c)
    walk(gj.get("group") if isinstance(gj, dict) and isinstance(gj.get("group"), dict) else gj)
    return found


def test_unibet(bases, timeout, event_id=None):
    out = dict(ok=False, note="")
    base = gj = None
    last = None
    for b in bases:
        print("\n[Unibet/Kambi] %s" % b)
        d, prob, res = step("group.json", uni_url(b, "group.json"), timeout)
        if d is not None:
            base, gj = b, d
            break
        last = prob
    if gj is None:
        out["note"] = last or "group.json ei vastannut"
        return out
    groups = uni_cs_groups(gj)
    print("      CS-ryhmiä group.json:ssa: %s" % ([(g.get("id"), g.get("name"), g.get("termKey"), g.get("eventCount")) for g in groups[:3]] or "ei löytynyt"))
    cands = []
    for g in groups[:2]:
        cands.append(("betoffer/group/%s" % g["id"], "betoffer/group/%s.json" % g["id"]))
        cands.append(("event/group/%s" % g["id"], "event/group/%s.json" % g["id"]))
    for p in ("listView/esports/counter_strike/all/all/matches.json", "listView/counter_strike/all/all/all/matches.json", "listView/esports/counter_strike.json"):
        cands.append((p.replace(".json", ""), p))
    events, used = [], None
    for label, rel in cands:
        d, prob, _ = step(label[:34], uni_url(base, rel), timeout)
        if d is None:
            continue
        evs = uni_cs_upcoming(uni_events(d))
        print("      -> CS-otteluita (NOT_STARTED): %d" % len(evs))
        if evs:
            events, used = evs, rel
            break
    ids = []
    if event_id:
        ids = [str(event_id)]
    elif events:
        events.sort(key=lambda e: -(e.get("nonLiveBoCount") or 0))
        print("      ottelulista: %s (%d ottelua)" % (used, len(events)))
        ids = [str(e["id"]) for e in events[:3]]
    else:
        out["note"] = ("group.json toimii mutta yksikään ottelulistareitti ei antanut CS-otteluita (ks. rivit yllä); "
                       "anna ottelun numero: --event NUMERO (fi.unibet.com/betting/sports/event/NUMERO)")
        out["ok"] = True
        return out
    kills_found = False
    for eid in ids:
        det, prob, _ = step("betoffer/event/%s" % eid, uni_url(base, "betoffer/event/%s.json?includeParticipants=true" % eid), timeout)
        if det is None:
            out["note"] = prob or "ottelun tarjonta ei vastannut"
            return out
        offers = det.get("betOffers") or []
        kill = [bo for bo in offers if UNI_KILL.match(str((bo.get("criterion") or {}).get("englishLabel") or "").strip())
                or UNI_KILL_FI.match(str((bo.get("criterion") or {}).get("label") or "").strip())]
        names = set()
        for bo in kill:
            for o in bo.get("outcomes") or []:
                if o.get("participant"):
                    names.add(o["participant"])
        print("      betOffereita %d, pelaajan tappolinjoja %d, eri nimiä (outcomes[].participant) %d" % (len(offers), len(kill), len(names)))
        if kill:
            kills_found = True
            print("      --- tappolinjan rakenne (3 ensimmäistä), kopioi nämä Claudelle ---")
            for bo in kill[:3]:
                cr = bo.get("criterion") or {}
                print("      criterion: %s | %s | betOfferType: %s | bo.participant: %s | tags: %s" % (
                    cr.get("englishLabel"), cr.get("label"), (bo.get("betOfferType") or {}).get("englishName"), bo.get("participant"), bo.get("tags")))
                for o in bo.get("outcomes") or []:
                    print("         outcome: label=%r englishLabel=%r participant=%r type=%s line=%s odds=%s status=%s" % (
                        o.get("label"), o.get("englishLabel"), o.get("participant"), o.get("type"), o.get("line"), o.get("odds"), o.get("status")))
            break
    out["ok"] = True
    out["note"] = ("Kambi-rajapinta vastaa; ottelulista: %s; pelaajien tappolinjoja %s" % (
        used or "(annettu --event)", "LÖYTYI" if kills_found else "ei löytynyt testatuista otteluista (markkinat aukeavat lähempänä ottelua)"))
    return out


def main():
    ap = argparse.ArgumentParser(description="Pinnacle/Epicbet/Unibet-yhteystesti")
    ap.add_argument("--only", choices=["pinnacle", "epicbet", "unibet"], help="testaa vain yksi")
    ap.add_argument("--event", help="Unibet: ottelun numero (fi.unibet.com/betting/sports/event/NUMERO), jos listareitti ei löydy")
    ap.add_argument("--unibet-base", action="append", help="oletus: Kambin eu1- ja eu-osoitteet")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--pinnacle-base", default="https://guest.api.arcadia.pinnacle.com/0.1/")
    ap.add_argument("--epic-base", action="append", help="oletus: https://epicbet.com ja https://www.epicbet.com")
    a = ap.parse_args()
    print("Kertoimien haun yhteystesti · Python %s · %s" % (platform.python_version(), time.strftime("%Y-%m-%d %H:%M:%S")))
    results = {}
    if a.only in (None, "pinnacle"):
        results["Pinnacle"] = test_pinnacle(a.pinnacle_base, a.timeout)
    if a.only in (None, "epicbet"):
        results["Epicbet"] = test_epicbet(a.epic_base or ["https://epicbet.com", "https://www.epicbet.com"], a.timeout)
    if a.only in (None, "unibet"):
        results["Unibet"] = test_unibet(a.unibet_base or UNI_BASES, a.timeout, a.event)
    print("\n=== YHTEENVETO ===")
    for name, r in results.items():
        print("%-9s %s  %s" % (name, "OK   " if r["ok"] else "EI TOIMI", r["note"]))
    return 0 if all(r["ok"] for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
