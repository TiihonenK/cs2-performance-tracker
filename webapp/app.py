"""CS2 Vetomalli - FastAPI + HTMX -käyttöliittymä.

Käynnistys (kansiosta jossa players_props.py ja kannat ovat):
    uvicorn webapp.app:app --host 0.0.0.0 --port 8502
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from players_props import MAP_NAMES

from . import db, sim, stats

BASE = Path(__file__).parent
app = FastAPI(title="CS2 Vetomalli", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

db.init_db()


# ---------------------------------------------------------------------------
# Mallinefiltterit
# ---------------------------------------------------------------------------
def _num(v):
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def eur(v, signed=True, nd=2):
    v = _num(v)
    if v is None:
        return "–"
    s = f"{v:+,.{nd}f}" if signed else f"{v:,.{nd}f}"
    return s.replace(",", " ").replace(".", ",") + " €"


def pct(v, nd=1, signed=False):
    v = _num(v)
    if v is None:
        return "–"
    s = f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"
    return s.replace(".", ",") + " %"


def num(v, nd=2):
    v = _num(v)
    return "–" if v is None else f"{v:.{nd}f}".replace(".", ",")


def line_fmt(v):
    v = _num(v)
    return "–" if v is None else f"{v:g}"


def tone(v):
    v = _num(v)
    if v is None or abs(v) < 1e-9:
        return "neutral"
    return "pos" if v > 0 else "neg"


def pretty_day(d):
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
    except (TypeError, ValueError):
        return d or "–"
    today = datetime.now().date()
    delta = (today - dt.date()).days
    names = ["ma", "ti", "ke", "to", "pe", "la", "su"]
    base = f"{names[dt.weekday()]} {dt.day}.{dt.month}."
    if delta == 0:
        return f"Tänään · {base}"
    if delta == 1:
        return f"Eilen · {base}"
    return base


# Selaimen välimuistin tyhjennys kun staattiset tiedostot muuttuvat
templates.env.globals["v"] = int(max(p.stat().st_mtime for p in (BASE / "static").iterdir()))

for fn in (eur, pct, num, line_fmt, tone, pretty_day):
    templates.env.filters[fn.__name__] = fn
templates.env.filters["tojson_attr"] = lambda v: json.dumps(v, separators=(",", ":"), default=float)


def render(request, name, status_code=200, **ctx):
    ctx.setdefault("request", request)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def trigger(resp, **events):
    """Asettaa HX-Trigger-otsakkeen (ASCII-turvallinen JSON, otsakkeet ovat latin-1)."""
    resp.headers["HX-Trigger"] = json.dumps(events, ensure_ascii=True)
    return resp


def empty(**events):
    return trigger(HTMLResponse(""), **events)


def toast(msg, kind="ok", undo=None):
    t = {"msg": msg, "kind": kind}
    if undo:
        t["undo"] = undo
    return t


def page_ctx(active):
    latest, age = db.data_freshness()
    return dict(active=active, latest=latest, age=age, stale=(age is None or age > db.STALE_DATA_DAYS))


def clean_records(df):
    if df.empty:
        return []
    return df.astype(object).where(df.notna(), None).to_dict("records")


# ---------------------------------------------------------------------------
# Laske
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return render(request, "calc.html", teams=db.team_names(), map_names=MAP_NAMES,
                  tournaments=db.active_tournament_names(), **page_ctx("calc"))


def _fl(v, default=0.0):
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else default
    except ValueError:
        return default


@app.post("/calc", response_class=HTMLResponse)
def calc(request: Request, team1: str = Form(""), team2: str = Form(""), odds1: str = Form("1.85"),
         odds2: str = Form("1.85"), kartta: str = Form("Kartta 1"), map_name: str = Form(""),
         total_line: str = Form("0"), total_over: str = Form("0"), total_under: str = Form("0")):
    teams = set(db.team_names())
    o1, o2 = _fl(odds1, 0), _fl(odds2, 0)
    problems = []
    if team1 not in teams:
        problems.append(f"Joukkue 1 ei löydy kannasta: “{team1 or 'tyhjä'}”. Valitse listasta.")
    if team2 not in teams:
        problems.append(f"Joukkue 2 ei löydy kannasta: “{team2 or 'tyhjä'}”. Valitse listasta.")
    if team1 and team1 == team2:
        problems.append("Valitse kaksi eri joukkuetta.")
    if o1 <= 1.0 or o2 <= 1.0:
        problems.append("Anna molemmille joukkueille voittokerroin (yli 1,00).")
    if problems:
        return render(request, "partials/error.html", problems=problems)
    sid, s = sim.run(team1, team2, o1, o2, kartta, map_name or None,
                     _fl(total_line), _fl(total_over), _fl(total_under))
    return render(request, "partials/sim_result.html", sid=sid, s=s)


@app.post("/pinnacle", response_class=HTMLResponse)
def pinnacle(request: Request, text: str = Form("")):
    teams = set(db.team_names())
    try:
        matches = json.loads(text).get("matches", []) if text.strip() else []
    except (json.JSONDecodeError, AttributeError) as e:
        return render(request, "partials/error.html", problems=[f"JSON ei kelpaa: {e}"])
    items = []
    for m in matches:
        for mp in m.get("maps", []):
            t1, t2 = m.get("team1", ""), m.get("team2", "")
            items.append(dict(t1=t1, t2=t2, map=mp.get("map", ""), odds1=mp.get("odds1"),
                              odds2=mp.get("odds2"), total=mp.get("total_line"),
                              over=mp.get("over_odds"), under=mp.get("under_odds"),
                              missing=[t for t in (t1, t2) if t not in teams]))
    if not items:
        return render(request, "partials/error.html",
                      problems=['JSON:sta ei löytynyt yhtään ottelua ("matches"-lista puuttuu tai on tyhjä).'])
    return render(request, "partials/pinnacle_list.html", items=items)


@app.get("/sim/{sid}/player", response_class=HTMLResponse)
def sim_player(request: Request, sid: str, name: str):
    s = sim.get(sid)
    if not s or name not in s["players"]:
        return render(request, "partials/expired.html")
    p = s["players"][name]
    ev = sim.evaluate(s, name, "UNDER" if p["p_under"] >= p["p_over"] else "OVER", p["line"], 1.85, None, None)
    return render(request, "partials/player_sheet.html", sid=sid, s=s, p=p, ev=ev,
                  tournaments=db.active_tournament_names(), today=datetime.now().strftime("%Y-%m-%d"),
                  default_dir=ev["direction"], default_line=p["line"])


@app.get("/sim/{sid}/eval", response_class=HTMLResponse)
def sim_eval(request: Request, sid: str, name: str, direction: str = "OVER", line: str = "14.5",
             odds: str = "1.85", opp_odds: str = "0", tournament: str = ""):
    s = sim.get(sid)
    if not s or name not in s["players"]:
        return render(request, "partials/expired.html")
    direction = "UNDER" if direction == "UNDER" else "OVER"
    ln, od, oo = _fl(line, 14.5), _fl(odds, 0), _fl(opp_odds, 0)
    ev = sim.evaluate(s, name, direction, ln, od, oo, tournament or None)
    n_map, n_same = db.pending_on_map(s["t1"], s["t2"], s["kartta"], direction)
    return render(request, "partials/eval.html", ev=ev, odds=od, n_map=n_map, n_same=n_same,
                  max_per_map=db.MAX_BETS_PER_MAP, kartta=s["kartta"])


@app.post("/bets", response_class=HTMLResponse)
def create_bet(request: Request, sid: str = Form(...), name: str = Form(...), direction: str = Form("OVER"),
               line: str = Form("14.5"), stake: str = Form("10"), odds: str = Form("1.85"),
               opp_odds: str = Form("0"), tournament: str = Form(""), bet_date: str = Form("")):
    s = sim.get(sid)
    if not s or name not in s["players"]:
        return trigger(HTMLResponse(""), toast=toast("Simulaatio vanheni - laske ottelu uudelleen.", "err"))
    if not tournament:
        return trigger(HTMLResponse(""), toast=toast("Valitse turnaus ennen tallennusta.", "err"))
    direction = "UNDER" if direction == "UNDER" else "OVER"
    ln, od, st, oo = _fl(line), _fl(odds), _fl(stake), _fl(opp_odds)
    if od <= 1.0 or st <= 0:
        return trigger(HTMLResponse(""), toast=toast("Tarkista panos ja kerroin.", "err"))
    ev = sim.evaluate(s, name, direction, ln, od, oo, tournament)
    today = datetime.now().strftime("%Y-%m-%d")
    if not bet_date or bet_date == today:
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    else:
        date_str = bet_date + " 12:00"
    db.add_bet(
        description=db.build_description(name, direction, ln, s["t1"], s["t2"], s["kartta"]),
        stake=st, odds=od, tournament=tournament, map_label=s["kartta"], model_prob=ev["model_p"],
        player=name, direction=direction, line=ln, team1=s["t1"], team2=s["t2"], bet_date=date_str,
        opp_odds=(oo if oo > 1 else None), map_pool=s["map_name"],
        cal_prob=(ev["verdict"]["cal_p"] if ev["verdict"] else None))
    return empty(toast=toast(f"Veto tallennettu: {name} {direction} {ln:g}"), closeSheet=True, betsChanged=True)


# ---------------------------------------------------------------------------
# Vedot
# ---------------------------------------------------------------------------
def _filter_text(df, q):
    if df.empty or not q:
        return df
    ql = q.strip().lower()
    mask = pd.Series(False, index=df.index)
    for col in ("description", "tournament", "map", "status", "player", "team1", "team2"):
        if col in df.columns:
            mask |= df[col].astype(str).str.lower().str.contains(re.escape(ql), na=False)
    return df[mask]


PAGE_DAYS = 6


def _bets_view(view, q, page):
    df = stats.load_df()
    counts = dict(active=0, settled=0)
    if not df.empty:
        counts["active"] = int((df["status"] == "Odottaa").sum())
        counts["settled"] = int((df["status"] != "Odottaa").sum())
    if df.empty:
        return dict(groups=[], counts=counts, more=False, page=page)
    sub = df[df["status"] == "Odottaa"] if view == "active" else df[df["status"] != "Odottaa"]
    sub = _filter_text(sub, q)
    groups = stats.day_groups(sub)
    more = len(groups) > (page + 1) * PAGE_DAYS
    groups = groups[: (page + 1) * PAGE_DAYS]
    for g in groups:
        g["bets"] = [{k: (None if (isinstance(v, float) and v != v) else v) for k, v in b.items()}
                     for b in g["bets"]]
    return dict(groups=groups, counts=counts, more=more, page=page)


@app.get("/bets", response_class=HTMLResponse)
def bets_page(request: Request, view: str = "active", q: str = ""):
    view = "settled" if view == "settled" else "active"
    data = _bets_view(view, q, 0)
    return render(request, "bets.html", view=view, q=q, **data, **page_ctx("bets"))


@app.get("/bets/list", response_class=HTMLResponse)
def bets_list(request: Request, view: str = "active", q: str = "", page: int = 0):
    view = "settled" if view == "settled" else "active"
    data = _bets_view(view, q, max(page, 0))
    return render(request, "partials/bet_list.html", view=view, q=q, **data)


@app.post("/bets/{bet_id}/status", response_class=HTMLResponse)
def bet_status(request: Request, bet_id: int, status: str = Form(...)):
    bet = db.get_bet(bet_id)
    if not bet or status not in ("Voitto", "Tappio", "Odottaa"):
        return empty(toast=toast("Vetoa ei löytynyt.", "err"))
    prev = bet["status"]
    db.set_status(bet_id, status)
    undo = None
    if prev == "Odottaa" and status != "Odottaa":
        undo = f"/bets/{bet_id}/status?status=Odottaa"
    label = {"Voitto": "Merkitty voitoksi", "Tappio": "Merkitty tappioksi", "Odottaa": "Palautettu vireille"}[status]
    return empty(toast=toast(f"{label}: {bet.get('player') or bet['description'][:28]}", undo=undo),
                 betsChanged=True)


@app.get("/bets/{bet_id}/edit", response_class=HTMLResponse)
def bet_edit(request: Request, bet_id: int):
    bet = db.get_bet(bet_id)
    if not bet:
        return render(request, "partials/expired.html")
    parsed = db.parse_description(bet.get("description"))
    for k in ("player", "direction", "line", "team1", "team2"):
        if bet.get(k) in (None, ""):
            bet[k] = parsed.get(k)
    tournaments = [t["name"] for t in db.list_tournaments()]
    if bet.get("tournament") not in tournaments:
        tournaments.insert(0, bet.get("tournament"))
    bet_date = (bet.get("date") or "")[:10]
    return render(request, "partials/edit_sheet.html", bet=bet, tournaments=tournaments, map_names=MAP_NAMES,
                  bet_date=bet_date, teams=db.team_names())


@app.post("/bets/{bet_id}", response_class=HTMLResponse)
def bet_update(request: Request, bet_id: int, action: str = Form("save"), tournament: str = Form(""),
               map_label: str = Form("Kartta 1"), player: str = Form(""), direction: str = Form("OVER"),
               line: str = Form("14.5"), stake: str = Form("10"), odds: str = Form("1.85"),
               opp_odds: str = Form("0"), team1: str = Form(""), team2: str = Form(""),
               map_pool: str = Form(""), bet_date: str = Form(""), status: str = Form("Odottaa"),
               cashout_amount: str = Form("0")):
    bet = db.get_bet(bet_id)
    if not bet:
        return empty(toast=toast("Vetoa ei löytynyt.", "err"))
    ln, st, od, oo = _fl(line), _fl(stake), _fl(odds), _fl(opp_odds)
    if od <= 1.0 or st <= 0:
        return empty(toast=toast("Tarkista panos ja kerroin.", "err"))
    old = bet.get("date") or ""
    time_part = old[11:16] if len(old) >= 16 else "12:00"
    new_date = f"{bet_date} {time_part}" if bet_date else old
    direction = "UNDER" if direction == "UNDER" else "OVER"
    fields = dict(
        tournament=tournament or bet["tournament"], map=map_label, player=player.strip(), direction=direction,
        line=ln, stake=st, odds=od, team1=team1.strip(), team2=team2.strip(),
        description=db.build_description(player.strip(), direction, ln, team1.strip(), team2.strip(), map_label),
        date=new_date, opp_odds=(oo if oo > 1 else None), map_pool=(map_pool or None))
    if action == "cashout":
        db.update_bet(bet_id, fields, status="Cashout", cashout_amount=_fl(cashout_amount))
        msg = f"Cashout {_fl(cashout_amount):.2f} € (tuotto {_fl(cashout_amount) - st:+.2f} €)"
    else:
        new_status = status if status in db.STATUSES else bet["status"]
        if new_status == "Cashout":
            db.update_bet(bet_id, fields, status="Cashout", cashout_amount=_fl(cashout_amount))
        elif new_status != bet["status"]:
            db.update_bet(bet_id, fields, status=new_status)
        else:
            db.update_bet(bet_id, fields)
        msg = "Veto päivitetty"
    return empty(toast=toast(msg), closeSheet=True, betsChanged=True)


@app.post("/bets/{bet_id}/delete", response_class=HTMLResponse)
def bet_delete(request: Request, bet_id: int):
    db.delete_bet(bet_id)
    return empty(toast=toast("Veto poistettu"), closeSheet=True, betsChanged=True)


# ---------------------------------------------------------------------------
# Tilastot
# ---------------------------------------------------------------------------
def _scoped(range_key, tournament):
    df = stats.load_df()
    return df, stats.apply_filter(df, range_key, tournament)


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, range: str = "all", tournament: str = "all", tab: str = "overview"):
    df = stats.load_df()
    names = sorted(df["tournament"].unique()) if not df.empty else []
    return render(request, "stats.html", ranges=stats.RANGES, range_key=range, tournament=tournament,
                  tournament_names=names, tab=tab, **page_ctx("stats"))


@app.get("/stats/overview", response_class=HTMLResponse)
def stats_overview(request: Request, range: str = "all", tournament: str = "all"):
    _, d = _scoped(range, tournament)
    if d.empty or not d["settled"].any():
        return render(request, "partials/stats_empty.html")
    return render(request, "partials/stats_overview.html", sm=stats.summary(d), equity=stats.equity(d),
                  daily=stats.daily(d), tournaments=stats.by_tournament(d), directions=stats.by_direction(d),
                  odds=stats.by_odds(d), rolling=stats.rolling(d), monthly=stats.monthly(d))


@app.get("/stats/model", response_class=HTMLResponse)
def stats_model(request: Request, range: str = "all", tournament: str = "all"):
    _, d = _scoped(range, tournament)
    cal = sim.get_calibration()
    import bet_calibration as bc
    calsaved = d[d["cal_prob"].notna()] if (not d.empty and "cal_prob" in d.columns) else d
    return render(request, "partials/stats_model.html", calib=stats.calibration(d) if not d.empty else None,
                  by_dir=stats.calibration_by_direction(d) if not d.empty else [],
                  mvm=stats.model_vs_market(d) if not d.empty else None,
                  cal_saved=(stats.calibration(calsaved, col="cal_prob") if (not calsaved.empty and len(calsaved[calsaved["decided"]]) >= 30) else None),
                  cal_text=bc.describe(cal))


@app.get("/stats/players", response_class=HTMLResponse)
def stats_players(request: Request, range: str = "all", tournament: str = "all", player: str = ""):
    _, d = _scoped(range, tournament)
    plist = stats.players(d) if not d.empty else []
    detail = stats.player_detail(d, player) if (player and not d.empty) else None
    recent = []
    if detail:
        recent = clean_records(d[d["player"] == player].sort_values("id", ascending=False).head(15))
    return render(request, "partials/stats_players.html", plist=plist, player=player, detail=detail, recent=recent)


# ---------------------------------------------------------------------------
# Turnaukset
# ---------------------------------------------------------------------------
def _tournament_rows():
    df = stats.load_df()
    out = []
    for t in db.list_tournaments():
        sub = df[df["tournament"] == t["name"]] if not df.empty else df
        sm = stats.summary(sub) if not sub.empty else stats.summary(pd.DataFrame())
        eq = stats.equity(sub) if (not sub.empty and sub["settled"].any()) else None
        spark = None
        if eq and len(eq["values"]) > 1:
            vals = eq["values"]
            step = max(1, len(vals) // 40)
            spark = vals[::step] + ([vals[-1]] if (len(vals) - 1) % step else [])
        out.append(dict(t=t, sm=sm, spark=spark, bets=sm["n"]))
    return out


@app.get("/tournaments", response_class=HTMLResponse)
def tournaments_page(request: Request):
    return render(request, "tournaments.html", rows=_tournament_rows(), **page_ctx("tournaments"))


@app.get("/tournaments/list", response_class=HTMLResponse)
def tournaments_list(request: Request):
    return render(request, "partials/tournament_list.html", rows=_tournament_rows())


@app.post("/tournaments", response_class=HTMLResponse)
def tournament_create(request: Request, name: str = Form("")):
    try:
        db.create_tournament(name)
    except ValueError as e:
        return trigger(render(request, "partials/tournament_list.html", rows=_tournament_rows()),
                       toast=toast(str(e), "err"))
    return trigger(render(request, "partials/tournament_list.html", rows=_tournament_rows()),
                   toast=toast(f"Turnaus luotu: {name.strip()}"), clearNewTournament=True)


@app.get("/tournaments/{tid}", response_class=HTMLResponse)
def tournament_detail(request: Request, tid: int):
    ts = {t["id"]: t for t in db.list_tournaments()}
    t = ts.get(tid)
    if not t:
        return render(request, "partials/expired.html")
    df = stats.load_df()
    sub = df[df["tournament"] == t["name"]] if not df.empty else df
    groups = stats.day_groups(sub) if not sub.empty else []
    for g in groups:
        g["bets"] = [{k: (None if (isinstance(v, float) and v != v) else v) for k, v in b.items()} for b in g["bets"]]
    return render(request, "partials/tournament_sheet.html", t=t, sm=stats.summary(sub),
                  equity=(stats.equity(sub) if (not sub.empty and sub["settled"].any()) else None), groups=groups)


@app.post("/tournaments/{tid}/status", response_class=HTMLResponse)
def tournament_status(request: Request, tid: int, status: str = Form(...)):
    if status in ("Aktiivinen", "Suljettu"):
        db.set_tournament_status(tid, status)
    return trigger(render(request, "partials/tournament_list.html", rows=_tournament_rows()),
                   toast=toast("Turnauksen tila päivitetty"), closeSheet=True)


@app.post("/tournaments/{tid}/rename", response_class=HTMLResponse)
def tournament_rename(request: Request, tid: int, name: str = Form("")):
    try:
        res = db.rename_tournament(tid, name)
    except ValueError as e:
        return trigger(render(request, "partials/tournament_list.html", rows=_tournament_rows()),
                       toast=toast(str(e), "err"))
    msg = {"yhdistetty": "Turnaukset yhdistetty - vedot siirrettiin", "nimetty": "Turnaus nimetty uudelleen",
           "ennallaan": "Nimi ennallaan"}[res]
    return trigger(render(request, "partials/tournament_list.html", rows=_tournament_rows()),
                   toast=toast(msg), closeSheet=True, betsChanged=True)


@app.post("/tournaments/{tid}/delete", response_class=HTMLResponse)
def tournament_delete(request: Request, tid: int):
    try:
        db.delete_tournament(tid)
    except ValueError as e:
        return trigger(render(request, "partials/tournament_list.html", rows=_tournament_rows()),
                       toast=toast(str(e), "err"))
    return trigger(render(request, "partials/tournament_list.html", rows=_tournament_rows()),
                   toast=toast("Turnaus poistettu"), closeSheet=True)


# ---------------------------------------------------------------------------
# Muut
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return JSONResponse({"ok": True})


@app.get("/manifest.webmanifest")
def manifest():
    data = {
        "name": "CS2 Vetomalli", "short_name": "Vetomalli", "start_url": "/", "display": "standalone",
        "background_color": "#1a1a19", "theme_color": "#1a1a19",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }
    return Response(json.dumps(data), media_type="application/manifest+json")
