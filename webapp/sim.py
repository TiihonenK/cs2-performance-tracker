"""Simulointi: ottelu -> pelaajakohtaiset tappojakaumat, välimuistissa vedon kirjausta varten."""
import os
import threading
import time
import uuid
from collections import OrderedDict

import numpy as np

import bet_calibration as bc
from match_simulator import simulate_match_context
from players_props import (
    FORM_LAST_N_MAPS, MAP_KILL_FACTORS, MAP_NAMES, OPP_ALLOWED_LEAGUE, devig_two_way,
    get_team_kills_allowed, get_team_players_overall_stats, kills_probability_at_line,
    kpr_corrections, simulate_team_kills,
)

from . import db

NO_MAP = ""
_CACHE = OrderedDict()
_LOCK = threading.Lock()
_MAX_SIMS = 24
_TTL = 6 * 3600


def _put(sim):
    sid = uuid.uuid4().hex[:12]
    with _LOCK:
        _CACHE[sid] = (time.time(), sim)
        while len(_CACHE) > _MAX_SIMS:
            _CACHE.popitem(last=False)
    return sid


def get(sid):
    with _LOCK:
        item = _CACHE.get(sid)
        if not item or time.time() - item[0] > _TTL:
            return None
        _CACHE.move_to_end(sid)
        return item[1]


def run(team1, team2, odds1, odds2, kartta, map_name=None, total_line=0.0, total_over=0.0,
        total_under=0.0, n_sims=10000):
    """Palauttaa (sim_id, sim_dict). Sama logiikka kuin dashboard.py:n 'Laske arviot'."""
    p1, p2 = 1 / odds1, 1 / odds2
    prob1, prob2 = p1 / (p1 + p2), p2 / (p1 + p2)
    anchored = bool(total_line and total_line > 0)
    ctx = simulate_match_context(
        prob1, num_simulations=n_sims,
        total_line=(total_line if anchored else None),
        total_over_odds=(total_over if total_over and total_over > 1 else None),
        total_under_odds=(total_under if total_under and total_under > 1 else None))

    t1_players = get_team_players_overall_stats(team1)
    t2_players = get_team_players_overall_stats(team2)
    opp1, _ = get_team_kills_allowed(team2)
    opp2, _ = get_team_kills_allowed(team1)
    map_model = map_name if map_name in MAP_NAMES else None
    t1_sim = simulate_team_kills(t1_players, ctx["rounds"], ctx["share_t1"], opp_allowed=opp1, map_name=map_model)
    t2_sim = simulate_team_kills(t2_players, ctx["rounds"], ctx["share_t2"], opp_allowed=opp2, map_name=map_model)

    adj1, mf = kpr_corrections(opp1, map_model)
    adj2, _ = kpr_corrections(opp2, map_model)
    form_key = f"Form ({FORM_LAST_N_MAPS} krt)"

    def rows(team, data):
        out = []
        for r in data:
            out.append(dict(
                name=r["Pelaaja"], team=team, n=r["N"], kpr=r["KPR"], proj=r["Proj.K"], std=r["stdK"],
                line=r["Mallin linja"], p_over=float(r["P.over"].rstrip("%")), p_under=float(r["P.under"].rstrip("%")),
                form=("–" if str(r.get(form_key, "")).startswith("N/A") else r.get(form_key, "")), kills=r["_sim_kills"]))
        return out

    sim = dict(
        t1=team1, t2=team2, p1=prob1, p2=prob2, kartta=kartta, map_name=map_model, anchored=anchored,
        median_rounds=ctx["median_rounds"], p_ot=ctx["p_overtime"],
        t1_rounds=float((ctx["rounds"] * ctx["share_t1"]).mean()),
        t2_rounds=float((ctx["rounds"] * ctx["share_t2"]).mean()),
        opp1=opp1, opp2=opp2, adj1=adj1 * 21.6, adj2=adj2 * 21.6, map_factor=mf,
        league=OPP_ALLOWED_LEAGUE,
        t1_players=rows(team1, t1_sim), t2_players=rows(team2, t2_sim), created=time.time(),
    )
    sim["players"] = {p["name"]: p for p in sim["t1_players"] + sim["t2_players"]}
    return _put(sim), sim


_cal_cache = {"mtime": None, "cal": None}


def get_calibration():
    """Kalibrointi sovitetaan uudelleen vain kun my_bets.db on muuttunut."""
    try:
        m = os.path.getmtime(db.BETS_DB)
    except OSError:
        m = 0
    if _cal_cache["mtime"] != m:
        _cal_cache["cal"] = bc.fit_calibration(db.BETS_DB)
        _cal_cache["mtime"] = m
    return _cal_cache["cal"]


def evaluate(sim, player, direction, line, odds, opp_odds=None, tournament=None):
    """Mallin arvio valitulle pelaajalle/linjalle/suunnalle + Lyö/Älä lyö -tuomio."""
    p = sim["players"].get(player)
    if p is None:
        return None
    kills = p["kills"]
    p_over, p_under, odds_over, odds_under = kills_probability_at_line(kills, line)
    model_p = p_over if direction == "OVER" else p_under
    fair_odds = odds_over if direction == "OVER" else odds_under

    # Jakauma histogrammiin: tappomäärä -> todennäköisyys, väritys linjan mukaan
    lo, hi = int(np.percentile(kills, 0.5)), int(np.percentile(kills, 99.5))
    lo = min(lo, int(line) - 1)
    hi = max(hi, int(line) + 2)
    counts = np.bincount(np.clip(kills, 0, hi + 1).astype(int), minlength=hi + 2)
    xs = list(range(max(lo, 0), hi + 1))
    probs = [round(float(counts[x]) / len(kills) * 100, 2) for x in xs]
    side = ["over" if x > line else ("under" if x < line else "push") for x in xs]

    out = dict(player=player, team=p["team"], direction=direction, line=line, model_p=model_p,
               p_over=p_over, p_under=p_under, fair_odds=fair_odds, proj=p["proj"], std=p["std"],
               hist=dict(x=xs, p=probs, side=side), market=None, verdict=None)

    if opp_odds and opp_odds > 1 and odds and odds > 1:
        fair_self, _fo, marg = devig_two_way(odds, opp_odds)
        out["market"] = dict(p=fair_self, margin=marg, diff=(model_p - fair_self) * 100)

    if odds and odds > 1:
        cal = get_calibration()
        ok, cal_p, cal_ev, why = bc.bet_verdict(model_p, odds, direction, tournament, cal)
        min_o = bc.min_odds_for_value(model_p, direction, tournament, cal)
        out["verdict"] = dict(ok=ok, cal_p=cal_p, ev=cal_ev, why=why, min_odds=min_o,
                              min_ev=bc.MIN_EV * 100, breakeven=1 / odds * 100,
                              small=bc.is_small_event(tournament))
        if ok:
            b = odds - 1.0
            kelly = (cal_p * b - (1 - cal_p)) / b
            out["verdict"]["kelly_quarter"] = max(kelly * 25, 0.0)
        out["calib_text"] = bc.describe(cal)
    return out
