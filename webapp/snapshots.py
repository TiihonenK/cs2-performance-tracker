"""Kirjojen pelaajalinjojen tallennus ja markkinan oma arvio pelaajan tapoista.

Joka kerta kun Lue JSON lukee Epicbet-/Unibet-dokumentin (napista tai kirjanmerkistä), sen jokainen linja tallennetaan tauluun
odds_snapshots (kerran per kirja + haun aikaleima: sama JSON luettuna uudelleen ei kasvata taulua). Samalla lasketaan jokaiselle
(kirja, ottelu, kartta, pelaaja) -tikkaalle markkinan arvio tappojen keskiarvosta ja hajonnasta tauluun market_est:
  1) kate pois suhteellisesti: p_yli = (1/yli) / (1/yli + 1/alle)
  2) P(tapot > linja) = p_yli  ->  z = Φ⁻¹(1 − p_yli) = (linja − μ) / σ ; sovitetaan suora z ~ linja pienimmällä neliösummalla
     linjoista joissa 0,04 <= p_yli <= 0,96 (reunojen ekstrapoloitu hinnoittelu ei kerro paljoa)  ->  σ = 1/kulmakerroin, μ = −vakio·σ
Vähintään 2 käyttökelpoista linjaa tarvitaan. /calc tallentaa lisäksi mallin oman ennusteen (model_proj) samalle pelaajalle ja kartalle,
joten mallia voi myöhemmin verrata markkinaan ja toteutuneisiin tappoihin.
"""
import re
from datetime import datetime, timezone
from statistics import NormalDist

import numpy as np

from . import db

_ND = NormalDist()
BOOKS = ("epicbet", "unibet")


def init():
    with db.connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS odds_snapshots
            (id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT, fetched TEXT, saved TEXT, team1 TEXT, team2 TEXT, start TEXT,
             tournament TEXT, player TEXT, map INTEGER, line REAL, over_odds REAL, under_odds REAL, live INTEGER DEFAULT 0,
             UNIQUE(book, fetched, team1, team2, player, map, line))""")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_snap_match ON odds_snapshots(team1, team2, map, player)")
        conn.execute("""CREATE TABLE IF NOT EXISTS market_est
            (id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT, fetched TEXT, team1 TEXT, team2 TEXT, start TEXT, player TEXT, map INTEGER,
             mean REAL, sd REAL, n_lines INTEGER, UNIQUE(book, fetched, team1, team2, player, map))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS model_proj
            (id INTEGER PRIMARY KEY AUTOINCREMENT, saved TEXT, team1 TEXT, team2 TEXT, map INTEGER, player TEXT, proj REAL, sd REAL, start TEXT)""")


def implied(ladder):
    """ladder: [(linja, yli, alle)] -> (μ, σ, n) tai None."""
    pts = []
    for line, ov, un in ladder:
        try:
            if not ov or not un or ov <= 1.0 or un <= 1.0:
                continue
            p = (1 / ov) / (1 / ov + 1 / un)
            if 0.04 <= p <= 0.96:
                pts.append((float(line), _ND.inv_cdf(1 - p)))
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    if len({x for x, _ in pts}) < 2:
        return None
    x = np.array([a for a, _ in pts]); z = np.array([b for _, b in pts])
    slope, icpt = np.polyfit(x, z, 1)
    if slope <= 1e-6:
        return None
    sd = 1.0 / slope
    return float(-icpt * sd), float(sd), len(pts)


def _mapno(v):
    m = re.search(r"(\d)", str(v if v is not None else ""))
    return int(m.group(1)) if m else None


def save_docs(docs):
    """Tallentaa dokumenttien (source epicbet/unibet + fetched) pelaajalinjat. Palauttaa lisättyjen linjojen määrän."""
    added = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with db.connect() as conn:
            for d in docs:
                if not isinstance(d, dict) or d.get("source") not in BOOKS or not d.get("fetched"):
                    continue
                book, fetched = d["source"], str(d["fetched"])
                for m in d.get("matches") or []:
                    t1, t2, start = m.get("team1"), m.get("team2"), m.get("start")
                    ladders = {}
                    for p in m.get("players") or []:
                        mp, line = _mapno(p.get("map")), p.get("line")
                        if not p.get("player") or mp is None or line is None:
                            continue
                        cur = conn.execute("INSERT OR IGNORE INTO odds_snapshots (book, fetched, saved, team1, team2, start, tournament, player, map, line, over_odds, under_odds, live) "
                                           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                           (book, fetched, now, t1, t2, start, m.get("tournament"), p["player"], mp, float(line), p.get("over"), p.get("under"), 1 if m.get("live") else 0))
                        added += cur.rowcount
                        ladders.setdefault((p["player"], mp), []).append((float(line), p.get("over"), p.get("under")))
                    for (pl, mp), lad in ladders.items():
                        est = implied(lad)
                        if est:
                            conn.execute("INSERT OR IGNORE INTO market_est (book, fetched, team1, team2, start, player, map, mean, sd, n_lines) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                         (book, fetched, t1, t2, start, pl, mp, round(est[0], 3), round(est[1], 3), est[2]))
    except Exception:       # tallennus ei saa kaataa Lue JSON -toimintoa
        return 0
    return added


def save_model(team1, team2, kartta, players, start=None):
    """Tallentaa mallin ennusteen (sim['players']) kartalle. Sama ottelu+kartta+pelaaja korvataan uusimmalla."""
    mp = _mapno(kartta)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with db.connect() as conn:
            for name, p in (players or {}).items():
                if p.get("proj") is None:
                    continue
                conn.execute("DELETE FROM model_proj WHERE team1=? AND team2=? AND map=? AND player=?", (team1, team2, mp, name))
                conn.execute("INSERT INTO model_proj (saved, team1, team2, map, player, proj, sd, start) VALUES (?,?,?,?,?,?,?,?)",
                             (now, team1, team2, mp, name, float(p["proj"]), float(p["std"]) if p.get("std") is not None else None, start))
    except Exception:
        pass


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def overview(limit=40):
    """Yhteenveto Markkina-välilehdelle: tallennettujen määrät + malli vs markkina (uusin markkina-arvio per pelaaja/kartta)."""
    with db.connect(readonly=True) as conn:
        cnt = [dict(r) for r in conn.execute("SELECT book, COUNT(*) n, COUNT(DISTINCT fetched) fetches, MAX(fetched) last FROM odds_snapshots GROUP BY book")]
        est = [dict(r) for r in conn.execute("SELECT * FROM market_est ORDER BY fetched DESC, id DESC")]
        mod = [dict(r) for r in conn.execute("SELECT * FROM model_proj")]
    by_pm = {}
    for r in mod:
        by_pm.setdefault((r["map"], _norm(r["player"])), []).append(r)

    def same(a, b):
        a, b = _norm(a), _norm(b)
        return bool(a and b and (a in b or b in a))

    def find(e):     # sama pelaaja + kartta, ja ainakin toinen joukkue täsmää (kirjan "Team Spirit" vs kannan "Spirit")
        for m in by_pm.get((e["map"], _norm(e["player"])), []):
            if any(same(x, y) for x in (m["team1"], m["team2"]) for y in (e["team1"], e["team2"])):
                return m
        return None
    seen, rows = set(), []
    for e in est:
        k = (e["book"], _norm(e["team1"]), _norm(e["team2"]), e["map"], _norm(e["player"]))
        if k in seen:
            continue
        seen.add(k)
        m = find(e)
        if m:
            rows.append(dict(book=e["book"], match=f"{e['team1']} vs {e['team2']}", map=e["map"], player=e["player"], market=e["mean"], sd=e["sd"],
                             model=m["proj"], diff=m["proj"] - e["mean"], fetched=e["fetched"]))
    rows.sort(key=lambda r: abs(r["diff"]), reverse=True)
    n = len(rows)
    return dict(counts=cnt, rows=rows[:limit], n_compared=n, n_est=len(seen),
                mean_diff=(sum(r["diff"] for r in rows) / n) if n else None,
                mean_abs=(sum(abs(r["diff"]) for r in rows) / n) if n else None)
