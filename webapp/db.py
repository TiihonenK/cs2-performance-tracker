"""Tietokantakerros: yhteydet, migraatiot ja vetojen/turnausten CRUD.

Käyttää SAMOJA tiedostoja kuin Streamlit-dashboard (my_bets.db ja hltv_data.db),
joten vanha ja uusi sovellus voivat toimia rinnakkain. Migraatiot ovat samat ja
idempotentteja.
"""
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime

BETS_DB = os.environ.get("BETS_DB", "my_bets.db")
HLTV_DB = os.environ.get("HLTV_DB", "hltv_data.db")

STALE_DATA_DAYS = 3
MAX_BETS_PER_MAP = 2
STATUSES = ("Odottaa", "Voitto", "Tappio", "Cashout")


@contextmanager
def connect(path=None, readonly=False):
    conn = sqlite3.connect(path or BETS_DB, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        if not readonly:
            conn.commit()
    finally:
        conn.close()


def init_db():
    """Luo taulut ja lisää puuttuvat sarakkeet (sama skeema kuin dashboard.py)."""
    with connect() as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS bets
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, type TEXT,
                      description TEXT, stake REAL, odds REAL, status TEXT)""")
        for col, ddl in [("tournament", "TEXT DEFAULT 'Yleinen'"), ("map", "TEXT DEFAULT 'Kartta 1'"),
                         ("model_prob", "REAL"), ("opp_odds", "REAL"), ("map_pool", "TEXT"),
                         ("cal_prob", "REAL"), ("player", "TEXT"), ("direction", "TEXT"),
                         ("line", "REAL"), ("team1", "TEXT"), ("team2", "TEXT"),
                         ("cashout_amount", "REAL")]:
            try:
                c.execute(f"ALTER TABLE bets ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
        existing = [r[1] for r in c.execute("PRAGMA table_info(bets)").fetchall()]
        if "closing_odds" in existing:
            try:
                c.execute("ALTER TABLE bets DROP COLUMN closing_odds")
            except sqlite3.OperationalError:
                pass
        c.execute("""CREATE TABLE IF NOT EXISTS tournaments
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, status TEXT)""")
        c.execute("UPDATE tournaments SET status = 'Suljettu' WHERE name = 'Yleinen' AND status = 'Aktiivinen'")


# ---------------------------------------------------------------------------
# HLTV-kanta (vain luku)
# ---------------------------------------------------------------------------
def team_names():
    try:
        with connect(HLTV_DB, readonly=True) as conn:
            return [r["name"] for r in conn.execute("SELECT name FROM teams ORDER BY name")]
    except sqlite3.Error:
        return []


POOL_SIZE = 7
_pool_cache = {"key": None, "maps": None}


def current_map_pool(fallback=()):
    """Aktiivinen karttapooli: 7 viimeksi pelattua eri karttaa hltv-datasta.

    Kartta lasketaan mukaan vain jos sitä on pelattu vähintään 5 kertaa datan
    viimeisen 120 päivän aikana (suojaa yksittäisiltä poikkeaviltä riveiltä).
    Tulos aakkosjärjestyksessä. Jos dataa ei ole, palautetaan fallback.
    """
    try:
        key = os.path.getmtime(HLTV_DB)
    except OSError:
        return list(fallback)
    if _pool_cache["key"] == key and _pool_cache["maps"]:
        return list(_pool_cache["maps"])
    maps = []
    try:
        with connect(HLTV_DB, readonly=True) as conn:
            rows = conn.execute(
                """SELECT map_name, MAX(match_date) AS last, COUNT(*) FROM matches
                   WHERE match_date > '2020' AND map_name IS NOT NULL AND map_name != ''
                     AND match_date >= date((SELECT MAX(match_date) FROM matches WHERE match_date > '2020'), '-120 days')
                   GROUP BY map_name HAVING COUNT(*) >= 5
                   ORDER BY last DESC LIMIT ?""", (POOL_SIZE,)).fetchall()
        maps = sorted(r[0] for r in rows)
    except sqlite3.Error:
        maps = []
    if len(maps) < 4:
        maps = list(fallback)
    _pool_cache.update(key=key, maps=maps)
    return list(maps)


def data_freshness():
    """(tuorein kartta 'YYYY-MM-DD' | None, ikä päivinä | None)."""
    try:
        with connect(HLTV_DB, readonly=True) as conn:
            latest = conn.execute(
                "SELECT MAX(match_date) FROM matches WHERE match_date > '2020'").fetchone()[0]
    except sqlite3.Error:
        return None, None
    try:
        age = (datetime.now() - datetime.strptime(latest[:10], "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return latest, None
    return latest, age


# ---------------------------------------------------------------------------
# Vedot
# ---------------------------------------------------------------------------
BET_COLUMNS = ("tournament", "map", "player", "direction", "line", "stake", "odds",
               "team1", "team2", "description", "date", "opp_odds", "map_pool")


def build_description(player, direction, line, team1, team2, map_label):
    return f"{player} {direction} {line:g} ({team1} vs {team2}, {map_label})"


def add_bet(*, description, stake, odds, tournament, map_label, model_prob=None, player=None,
            direction=None, line=None, team1=None, team2=None, bet_date=None, opp_odds=None,
            map_pool=None, cal_prob=None):
    date_str = bet_date or datetime.now().strftime("%Y-%m-%d %H:%M")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO bets (date, type, description, stake, odds, status, tournament, map,
                                 model_prob, player, direction, line, team1, team2, opp_odds,
                                 map_pool, cal_prob)
               VALUES (?, 'Tapot', ?, ?, ?, 'Odottaa', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (date_str, description, stake, odds, tournament, map_label, model_prob, player,
             direction, line, team1, team2, opp_odds, map_pool, cal_prob))
        return cur.lastrowid


def get_bet(bet_id):
    with connect(readonly=True) as conn:
        r = conn.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
        return dict(r) if r else None


def all_bets():
    with connect(readonly=True) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM bets ORDER BY id DESC")]


def set_status(bet_id, status):
    if status not in ("Voitto", "Tappio", "Odottaa"):
        raise ValueError(status)
    with connect() as conn:
        conn.execute("UPDATE bets SET status = ?, cashout_amount = NULL WHERE id = ?", (status, bet_id))


def update_bet(bet_id, fields, status=None, cashout_amount=None):
    sets, vals = [], []
    for k, v in fields.items():
        if k not in BET_COLUMNS:
            continue
        sets.append(f"{k} = ?")
        vals.append(v)
    if status is not None:
        sets.append("status = ?")
        vals.append(status)
        sets.append("cashout_amount = ?")
        vals.append(cashout_amount if status == "Cashout" else None)
    if not sets:
        return
    vals.append(bet_id)
    with connect() as conn:
        conn.execute(f"UPDATE bets SET {', '.join(sets)} WHERE id = ?", vals)


def delete_bet(bet_id):
    with connect() as conn:
        conn.execute("DELETE FROM bets WHERE id = ?", (bet_id,))


def pending_on_map(team1, team2, map_label, direction):
    """(vireillä olevia vetoja samalla kartalla, niistä samaan suuntaan)."""
    with connect(readonly=True) as conn:
        r = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(direction = ?), 0) FROM bets WHERE status = 'Odottaa' "
            "AND team1 = ? AND team2 = ? AND map = ?",
            (direction, team1, team2, map_label)).fetchone()
        return int(r[0] or 0), int(r[1] or 0)


# ---------------------------------------------------------------------------
# Turnaukset
# ---------------------------------------------------------------------------
def list_tournaments():
    with connect(readonly=True) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM tournaments ORDER BY id DESC")]


def active_tournament_names():
    with connect(readonly=True) as conn:
        return [r["name"] for r in conn.execute(
            "SELECT name FROM tournaments WHERE status = 'Aktiivinen' ORDER BY id DESC")]


def create_tournament(name):
    name = (name or "").strip()
    if not name:
        raise ValueError("Anna turnauksen nimi.")
    try:
        with connect() as conn:
            conn.execute("INSERT INTO tournaments (name, status) VALUES (?, 'Aktiivinen')", (name,))
    except sqlite3.IntegrityError:
        raise ValueError("Tämän niminen turnaus on jo olemassa.")


def set_tournament_status(tid, status):
    with connect() as conn:
        conn.execute("UPDATE tournaments SET status = ? WHERE id = ?", (status, tid))


def tournament_bet_count(name):
    with connect(readonly=True) as conn:
        return conn.execute("SELECT COUNT(*) FROM bets WHERE tournament = ?", (name,)).fetchone()[0]


def delete_tournament(tid):
    with connect() as conn:
        row = conn.execute("SELECT name FROM tournaments WHERE id = ?", (tid,)).fetchone()
        if not row:
            return
        n = conn.execute("SELECT COUNT(*) FROM bets WHERE tournament = ?", (row["name"],)).fetchone()[0]
        if n:
            raise ValueError(f"Turnaukseen on merkitty {n} vetoa. Siirrä tai yhdistä ne ensin.")
        conn.execute("DELETE FROM tournaments WHERE id = ?", (tid,))


def rename_tournament(tid, new_name):
    """Nimeää turnauksen uudelleen. Jos uusi nimi on jo olemassa, vedot siirretään siihen
    ja vanha turnaus poistetaan (yhdistäminen, esim. kirjoitusvirheellinen duplikaatti)."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("Anna uusi nimi.")
    with connect() as conn:
        row = conn.execute("SELECT name FROM tournaments WHERE id = ?", (tid,)).fetchone()
        if not row:
            raise ValueError("Turnausta ei löydy.")
        old = row["name"]
        if old == new_name:
            return "ennallaan"
        other = conn.execute("SELECT id FROM tournaments WHERE name = ?", (new_name,)).fetchone()
        conn.execute("UPDATE bets SET tournament = ? WHERE tournament = ?", (new_name, old))
        if other:
            conn.execute("DELETE FROM tournaments WHERE id = ?", (tid,))
            return "yhdistetty"
        conn.execute("UPDATE tournaments SET name = ? WHERE id = ?", (new_name, tid))
        return "nimetty"


_DESC_RE = re.compile(r"^(?P<player>.+?)\s+(?P<dir>OVER|UNDER)\s+(?P<line>[0-9]+(?:\.[0-9]+)?)\s*"
                      r"(?:\((?P<t1>.+?)\s+vs\s+(?P<t2>.+?)(?:,\s*(?P<map>[^)]*))?\))?", re.I)


def parse_description(desc):
    """Purkaa vanhan vedon kuvauksen kentiksi (pelaaja, suunta, raja, joukkueet)."""
    m = _DESC_RE.match(desc or "")
    if not m:
        return {}
    return {"player": m.group("player").strip(), "direction": m.group("dir").upper(),
            "line": float(m.group("line")), "team1": m.group("t1"), "team2": m.group("t2")}
