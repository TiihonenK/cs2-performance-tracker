"""JSON-tuonti Laske-sivulle: Pinnaclen ottelu-JSON ja Epicbetin pelaajalinjat samassa laatikossa.

- Laatikkoon voi liittää yhden tai useamman JSON-dokumentin peräkkäin (esim. Pinnacle + Epicbet).
- Joukkueiden nimet muunnetaan kannan nimiksi ("Team Spirit" -> "Spirit", "FURIA Esports" -> "FURIA").
- Saman ottelun tiedot yhdistetään: karttakertoimet Pinnaclelta (jos on), pelaajalinjat Epicbetiltä.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches

_DROP = {"team", "esports", "gaming", "cs", "club"}


def norm(name):
    s = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
    toks = [t for t in s.split() if t not in _DROP]
    return "".join(toks) if toks else re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def resolve_team(name, teams):
    """Palauttaa kannan joukkuenimen tai None. teams = kannan nimet."""
    if not name:
        return None
    if name in teams:
        return name
    idx = {}
    for t in teams:
        idx.setdefault(norm(t), t)
    k = norm(name)
    if k in idx:
        return idx[k]
    near = get_close_matches(k, list(idx), n=1, cutoff=0.88)
    return idx[near[0]] if near else None


def _last_sunday(year, month):
    d = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1) if month < 12 else datetime(year, 12, 31, tzinfo=timezone.utc)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def fi_time(iso):
    """ISO-aika (UTC) -> 'su 8.11. klo 16.00' Suomen ajassa (EET/EEST ilman tz-tietokantaa)."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        y = dt.year
        dst_from = _last_sunday(y, 3).replace(hour=1)
        dst_to = _last_sunday(y, 10).replace(hour=1)
        loc = dt + timedelta(hours=3 if dst_from <= dt < dst_to else 2)
        wd = ["ma", "ti", "ke", "to", "pe", "la", "su"][loc.weekday()]
        return f"{wd} {loc.day}.{loc.month}. klo {loc.hour}.{loc.minute:02d}"
    except (ValueError, TypeError):
        return None


def parse_documents(text):
    """Kaikki JSON-dokumentit tekstistä (peräkkäin, välilyönnein/rivinvaihdoin erotettuina)."""
    dec, i, n, docs = json.JSONDecoder(), 0, len(text), []
    while i < n:
        while i < n and text[i] in " \t\r\n,;":
            i += 1
        if i >= n:
            break
        obj, j = dec.raw_decode(text, i)
        docs.append(obj)
        i = j
    return docs


def _map_no(v):
    m = re.search(r"(\d)", str(v or ""))
    return m.group(1) if m else None


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def merge_matches(docs, teams):
    """-> lista otteluita: dict(t1, t2, key, maps{no: {...}}, players[...], tournament, start, ...)."""
    order, by = [], {}
    for doc in docs:
        ms = doc.get("matches", []) if isinstance(doc, dict) else (doc if isinstance(doc, list) else [])
        for m in ms:
            if not isinstance(m, dict):
                continue
            raw1, raw2 = m.get("team1", ""), m.get("team2", "")
            t1 = resolve_team(raw1, teams) or raw1
            t2 = resolve_team(raw2, teams) or raw2
            key = "|".join(sorted([t1.lower(), t2.lower()]))
            src = str(m.get("source") or "pinnacle").lower()
            it = by.get(key)
            if it is None:
                it = dict(t1=t1, t2=t2, key=f"{t1} vs {t2}", maps={}, map_src={}, players=[], tournament=None,
                          start=None, raw=(raw1, raw2), missing=[x for x in (t1, t2) if x not in teams])
                by[key] = it
                order.append(key)
            swapped = (t1.lower() == it["t2"].lower() and t2.lower() == it["t1"].lower())
            for mp in m.get("maps", []) or []:
                no = _map_no(mp.get("map"))
                if not no:
                    continue
                if src == "epicbet" and no in it["maps"] and it["map_src"].get(no) != "epicbet":
                    continue          # Pinnacle voittaa Epicbetin
                o1, o2 = _num(mp.get("odds1")), _num(mp.get("odds2"))
                if swapped:
                    o1, o2 = o2, o1
                it["maps"][no] = dict(odds1=o1, odds2=o2, total=_num(mp.get("total_line")),
                                      over=_num(mp.get("over_odds")), under=_num(mp.get("under_odds")))
                it["map_src"][no] = src
            for p in m.get("players", []) or []:
                ln, name = _num(p.get("line")), p.get("player")
                no = _map_no(p.get("map"))
                if ln is None or not name or not no:
                    continue
                it["players"].append(dict(player=str(name), team=resolve_team(p.get("team"), teams) or p.get("team"),
                                          map=int(no), line=ln, over=_num(p.get("over")), under=_num(p.get("under"))))
            it["tournament"] = it["tournament"] or m.get("tournament")
            it["start"] = it["start"] or m.get("start")
    out = []
    for k in order:
        it = by[k]
        it["map_list"] = ", ".join(sorted(it["maps"]))
        it["has_total"] = any(v.get("total") for v in it["maps"].values())
        it["n_players"] = len({p["player"].lower() for p in it["players"]})
        it["n_lines"] = len(it["players"])
        it["pl_maps"] = ", ".join(sorted({str(p["map"]) for p in it["players"]}))
        it["start_fi"] = fi_time(it["start"]) if it["start"] else None
        it["maps_epic"] = bool(it["maps"]) and all(v == "epicbet" for v in it["map_src"].values())
        out.append(it)
    return out


def match_player(epic_name, candidates):
    """Epicbetin pelaajanimi -> sim:n pelaajanimi (tai None)."""
    k = re.sub(r"[^a-z0-9]+", "", str(epic_name).lower())
    idx = {}
    for c in candidates:
        idx.setdefault(re.sub(r"[^a-z0-9]+", "", c.lower()), c)
    if k in idx:
        return idx[k]
    near = get_close_matches(k, list(idx), n=1, cutoff=0.8)
    return idx[near[0]] if near else None
