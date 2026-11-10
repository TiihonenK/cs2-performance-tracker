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

from . import db, pinimport

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
        total_under=0.0, n_sims=10000, epic_lines=None, epic_tournament=None, match_start=None):
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
    map_model = map_name if map_name in MAP_KILL_FACTORS else None
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
        median_rounds=ctx["median_rounds"], mean_rounds=ctx["mean_rounds"], p_ot=ctx["p_overtime"],
        total_line=ctx["total_line"], market_p_over=ctx["market_p_over"], model_p_over=ctx["model_p_over"],
        t1_rounds=float((ctx["rounds"] * ctx["share_t1"]).mean()),
        t2_rounds=float((ctx["rounds"] * ctx["share_t2"]).mean()),
        opp1=opp1, opp2=opp2, adj1=adj1 * 21.6, adj2=adj2 * 21.6, map_factor=mf,
        league=OPP_ALLOWED_LEAGUE,
        t1_players=rows(team1, t1_sim), t2_players=rows(team2, t2_sim), created=time.time(),
    )
    sim["players"] = {p["name"]: p for p in sim["t1_players"] + sim["t2_players"]}
    sim["match_start"] = match_start      # ottelun alkamisaika (ISO, UTC) kirjan JSON:sta, jos ottelu valittiin listasta
    attach_lines(sim, epic_lines or [], epic_tournament)
    return _put(sim), sim


def _line_options(sim, name, line, over, under, tournament):
    """Molemmat suunnat annetulla linjalla ja kertoimilla: mallin p, yhdistetty p, EV ja onko arvoa."""
    p = sim["players"][name]
    p_over, p_under, _, _ = kills_probability_at_line(p["kills"], line)
    cal = get_calibration()
    opts = []
    for direction, odds, mp in (("OVER", over, p_over), ("UNDER", under, p_under)):
        if not odds or odds <= 1:
            continue
        comb_p, ev, cal_p, _w = _combined(mp, odds, direction, tournament, cal)
        opts.append(dict(direction=direction, odds=float(odds), model_p=mp, comb_p=comb_p, ev=ev,
                         ok=bool(ev >= bc.MIN_EV and mp >= MODEL_P_FLOOR)))
    return opts


def attach_lines(sim, lines, tournament=None):
    """Liittää kirjan (Epicbet) pelaajalinjat simulaatioon: sim['epic'][pelaaja] = [{line, over, under, opts, best}],
    sim['epic_top'] = arvolinjat EV-järjestyksessä, sim['epic_missing'] = nimet joita ei löytynyt kokoonpanosta."""
    sim["epic"], sim["epic_top"], sim["epic_missing"], sim["epic_n"] = {}, [], [], 0
    sim["epic_tournament"] = tournament
    if not lines:
        return
    names = list(sim["players"])
    seen_missing = set()
    for ln in lines:
        nm = pinimport.match_player(ln.get("player", ""), names)
        if nm is None:
            seen_missing.add(str(ln.get("player")))
            continue
        line, over, under = ln.get("line"), ln.get("over"), ln.get("under")
        if line is None:
            continue
        opts = _line_options(sim, nm, float(line), over, under, tournament)
        oks = [o for o in opts if o["ok"]]
        best = max(oks, key=lambda o: o["ev"]) if oks else (max(opts, key=lambda o: o["ev"]) if opts else None)
        sim["epic"].setdefault(nm, []).append(dict(line=float(line), over=over, under=under, opts=opts, best=best))
        sim["epic_n"] += 1
    for nm, rows in sim["epic"].items():
        rows.sort(key=lambda r: r["line"])
        for r in rows:
            if r["best"] and r["best"]["ok"]:
                sim["epic_top"].append(dict(player=nm, team=sim["players"][nm]["team"], line=r["line"], **r["best"]))
    sim["epic_top"].sort(key=lambda r: r["ev"], reverse=True)
    sim["epic_missing"] = sorted(seen_missing)
    for nm, p in sim["players"].items():
        rows = sim["epic"].get(nm)
        p["ep"] = rows or []
        oks = [r["best"] for r in (rows or []) if r["best"] and r["best"]["ok"]]
        p["ep_best"] = max(oks, key=lambda o: o["ev"]) if oks else None
        p["ep_best_line"] = None
        if p["ep_best"]:
            for r in rows:
                if r["best"] is p["ep_best"]:
                    p["ep_best_line"] = r["line"]


MODEL_WEIGHT = 0.30    # kuinka paljon mallin arvio painaa historiasuodattimen rinnalla (ennakko, ei datasta)
MODEL_CAP = 0.15       # malli voi siirtää todennäköisyyttä enintään tämän verran historiasta
MODEL_OUTLIER = 0.20   # malli ja historia näin kaukana toisistaan -> varoitus
MODEL_P_FLOOR = 0.44   # pienin mallin arvio jonka kalibroinnin aineistossa on (0,5 %-persentiili)

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


def _combined(model_p, odds, direction, tournament, cal):
    """Historiasuodatin (kalibroitu osuma) + mallin arvio -> (yhdistetty p, EV, historia-p, paino).

    Mallille ei saatu datasta painoa (sovituksessa 0,00 +- 0,28), joten se saa varovaisen
    ennakkopainon MODEL_WEIGHT ja sen vaikutus rajataan +-MODEL_CAP:iin. Jos kalibrointi
    oppii itse käyttämään mallia (paino > 0), se on jo mukana historia-p:ssä eikä lisätä uudelleen."""
    cal_p, _ = bc.calibrated_probability(model_p, odds, direction, tournament, cal)
    w = MODEL_WEIGHT if abs(float(cal["coef"][4])) < 1e-9 else 0.0
    m_cap = min(max(model_p, cal_p - MODEL_CAP), cal_p + MODEL_CAP)
    p = cal_p + w * (m_cap - cal_p)
    return p, p * odds - 1.0, cal_p, w


def _min_odds(model_p, direction, tournament, cal):
    for o in np.arange(1.30, 3.01, 0.01):
        if _combined(model_p, float(o), direction, tournament, cal)[1] >= bc.MIN_EV:
            return round(float(o), 2)
    return None


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
        comb_p, comb_ev, cal_p, w = _combined(model_p, odds, direction, tournament, cal)
        _, hist_ev = bc.calibrated_probability(model_p, odds, direction, tournament, cal)
        breakeven = 1.0 / odds
        min_o = _min_odds(model_p, direction, tournament, cal)
        gap = model_p - cal_p
        # Kalibrointi on sovitettu vetoihin joissa mallin arvio oli ~44-79 %. Alle sen malli
        # sanoo itse ettei veto osu: ei lyödä.
        out_of_range = model_p < MODEL_P_FLOOR
        ok = (comb_ev >= bc.MIN_EV) and not out_of_range
        if out_of_range:
            min_o = None
            why = (f"Malli arvioi osumatodennäköisyydeksi vain {model_p * 100:.1f} %. Kalibrointi on "
                   f"sovitettu vetoihin joissa mallin arvio oli vähintään {MODEL_P_FLOOR * 100:.0f} %, "
                   f"joten tuomio ei päde näin kaukana mallin omasta linjasta.")
        elif ok:
            why = (f"Yhdistetty osumatodennäköisyys {comb_p * 100:.1f} % (historia {cal_p * 100:.1f} %, "
                   f"malli {model_p * 100:.1f} %) > kertoimen vaatima {breakeven * 100:.1f} % "
                   f"→ EV {comb_ev * 100:+.1f} %.")
        else:
            why = (f"Yhdistetty osumatodennäköisyys {comb_p * 100:.1f} % (historia {cal_p * 100:.1f} %, "
                   f"malli {model_p * 100:.1f} %), kerroin vaatii {breakeven * 100:.1f} % "
                   f"→ EV {comb_ev * 100:+.1f} % (raja {bc.MIN_EV * 100:+.0f} %).")
            if bc.is_small_event(tournament):
                why += " Pienissä turnauksissa / karsinnoissa vetosi ovat hävinneet selvästi."
        out["verdict"] = dict(ok=ok, cal_p=cal_p, comb_p=comb_p, ev=comb_ev, hist_ev=hist_ev, why=why,
                              min_odds=min_o, min_ev=bc.MIN_EV * 100, breakeven=breakeven * 100,
                              small=bc.is_small_event(tournament), out_of_range=out_of_range,
                              model_weight=w, gap=gap * 100, outlier=abs(gap) > MODEL_OUTLIER,
                              capped=abs(gap) > MODEL_CAP)
        # Näytettävät luvut: yhdistetty arvio. Jos malli itse arvioi osuman alle rajan (punainen
        # tuomio sen takia), näytetään mallin oma luku, jotta luvut selittävät värin.
        out["verdict"]["show_p"] = model_p if out_of_range else comb_p
        out["verdict"]["show_ev"] = (model_p * odds - 1.0) if out_of_range else comb_ev
        if ok:
            b = odds - 1.0
            kelly = (comb_p * b - (1 - comb_p)) / b
            out["verdict"]["kelly_quarter"] = max(kelly * 25, 0.0)
        out["calib_text"] = bc.describe(cal)
    return out
