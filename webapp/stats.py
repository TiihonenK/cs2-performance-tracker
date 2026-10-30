"""Tilastot ja graafidata. Kaikki palauttaa JSON-yhteensopivia rakenteita."""
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import bet_calibration as bc
from . import db

SETTLED = ("Voitto", "Tappio", "Cashout")
DECIDED = ("Voitto", "Tappio")


def _f(x, nd=2):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return None
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def load_df():
    rows = db.all_bets()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Vanhoissa vedoissa rakenteelliset kentät voivat puuttua -> puretaan kuvauksesta
    parsed = df["description"].map(db.parse_description)
    for col in ("player", "direction", "line", "team1", "team2"):
        fallback = parsed.map(lambda d, c=col: d.get(c) if d else None)
        df[col] = df[col].where(df[col].notna(), fallback)
    df["dt"] = pd.to_datetime(df["date"], errors="coerce")
    df["day"] = df["dt"].dt.strftime("%Y-%m-%d")
    df["profit"] = _profit(df)
    df["settled"] = df["status"].isin(SETTLED)
    df["decided"] = df["status"].isin(DECIDED)
    df["win"] = (df["status"] == "Voitto").astype(int)
    df["implied"] = 1.0 / df["odds"]
    df["tournament"] = df["tournament"].fillna("Yleinen")
    return df


def _profit(df):
    stake, odds = df["stake"].astype(float), df["odds"].astype(float)
    cash = df["cashout_amount"].fillna(0.0).astype(float)
    return np.select(
        [df["status"] == "Voitto", df["status"] == "Tappio", df["status"] == "Cashout"],
        [stake * (odds - 1.0), -stake, cash - stake], default=0.0)


def bet_profit(row):
    s, st, o = row["status"], float(row["stake"] or 0), float(row["odds"] or 0)
    if s == "Voitto":
        return st * (o - 1.0)
    if s == "Tappio":
        return -st
    if s == "Cashout":
        return float(row.get("cashout_amount") or 0.0) - st
    return 0.0


RANGES = [("all", "Kaikki"), ("7", "7 pv"), ("30", "30 pv"), ("90", "90 pv")]


def apply_filter(df, range_key="all", tournament="all"):
    if df.empty:
        return df
    out = df
    if tournament and tournament != "all":
        out = out[out["tournament"] == tournament]
    if range_key and range_key != "all":
        try:
            cutoff = datetime.now() - timedelta(days=int(range_key))
            out = out[out["dt"] >= cutoff]
        except ValueError:
            pass
    return out


def summary(df):
    """Yhteenveto: ROI lasketaan vain ratkaistuista vedoista (kuten dashboard.py:ssä)."""
    if df.empty:
        return dict(n=0, settled=0, pending=0, profit=0.0, roi=0.0, staked=0.0, decided=0,
                    hit=None, breakeven=None, avg_odds=None)
    s = df[df["settled"]]
    d = df[df["decided"]]
    staked = float(s["stake"].sum())
    profit = float(df["profit"].sum())
    return dict(
        n=int(len(df)), settled=int(len(s)), pending=int((df["status"] == "Odottaa").sum()),
        profit=profit, staked=staked, roi=(profit / staked * 100.0) if staked > 0 else 0.0,
        decided=int(len(d)),
        hit=float(d["win"].mean() * 100) if len(d) else None,
        breakeven=float(d["implied"].mean() * 100) if len(d) else None,
        avg_odds=float(d["odds"].mean()) if len(d) else None,
    )


def _chrono(df):
    return df.sort_values(["dt", "id"], kind="stable")


def equity(df):
    s = _chrono(df[df["settled"]])
    if s.empty:
        return None
    cum = s["profit"].cumsum()
    return dict(labels=[d[5:10] if isinstance(d, str) else "" for d in s["day"]],
                dates=[d if isinstance(d, str) else "" for d in s["day"]],
                values=[round(float(v), 2) for v in cum],
                end=round(float(cum.iloc[-1]), 2))


def daily(df, limit=30):
    s = df[df["settled"] & df["day"].notna()]
    if s.empty:
        return None
    g = s.groupby("day").agg(profit=("profit", "sum"), n=("id", "size"),
                              staked=("stake", "sum")).sort_index().tail(limit)
    return dict(days=list(g.index), values=[round(float(v), 2) for v in g["profit"]],
                n=[int(v) for v in g["n"]],
                roi=[round(float(p / st * 100), 1) if st else 0.0 for p, st in zip(g["profit"], g["staked"])])


def by_tournament(df):
    s = df[df["settled"]]
    if s.empty:
        return []
    g = s.groupby("tournament").agg(n=("id", "size"), profit=("profit", "sum"),
                                     staked=("stake", "sum"), first=("dt", "min")).reset_index()
    g["roi"] = g["profit"] / g["staked"] * 100
    g = g.sort_values("first", ascending=False)
    return [dict(name=r.tournament, n=int(r.n), profit=round(float(r.profit), 2),
                 roi=round(float(r.roi), 1)) for r in g.itertuples()]


def by_direction(df):
    out = []
    for name in ("UNDER", "OVER"):
        sub = df[(df["direction"] == name) & df["settled"]]
        if sub.empty:
            continue
        d = sub[sub["decided"]]
        staked = float(sub["stake"].sum())
        out.append(dict(name=name, n=int(len(sub)),
                        hit=_f(d["win"].mean() * 100, 1) if len(d) else None,
                        breakeven=_f(d["implied"].mean() * 100, 1) if len(d) else None,
                        roi=_f(sub["profit"].sum() / staked * 100, 1) if staked else None,
                        profit=_f(sub["profit"].sum())))
    return out


ODDS_BINS = [1.0, 1.6, 1.7, 1.8, 1.9, 2.1, 100]
ODDS_LABELS = ["<1.60", "1.60–1.70", "1.70–1.80", "1.80–1.90", "1.90–2.10", ">2.10"]


def by_odds(df):
    s = df[df["settled"]].copy()
    if s.empty:
        return []
    s["bucket"] = pd.cut(s["odds"], ODDS_BINS, labels=ODDS_LABELS, right=True)
    out = []
    for lab, g in s.groupby("bucket", observed=True):
        staked = float(g["stake"].sum())
        out.append(dict(name=str(lab), n=int(len(g)),
                        roi=_f(g["profit"].sum() / staked * 100, 1) if staked else 0.0,
                        hit=_f(g[g["decided"]]["win"].mean() * 100, 1) if g["decided"].any() else None))
    return out


def rolling(df):
    d = _chrono(df[df["decided"]])
    n = len(d)
    if n < 40:
        return None
    w = min(100, max(20, n // 3))
    hit = d["win"].rolling(w).mean() * 100
    be = d["implied"].rolling(w).mean() * 100
    keep = hit.notna()
    return dict(window=w, labels=[x[5:10] if isinstance(x, str) else "" for x in d["day"][keep]],
                hit=[round(float(v), 1) for v in hit[keep]],
                breakeven=[round(float(v), 1) for v in be[keep]])


def wilson(k, n, z=1.28):
    """80 %:n Wilson-luottamusväli osuusprosentille."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


CAL_BINS = [0, .5, .55, .6, .65, .7, 1.0]


def calibration(df, col="model_prob", bins=CAL_BINS):
    d = df[df["decided"] & df[col].notna()].copy()
    if d.empty:
        return None
    d["bin"] = pd.cut(d[col], bins)
    rows = []
    for b, g in d.groupby("bin", observed=True):
        k, n = int(g["win"].sum()), int(len(g))
        lo, hi = wilson(k, n)
        staked = float(g["stake"].sum())
        rows.append(dict(bin=str(b), n=n, pred=round(float(g[col].mean()) * 100, 1),
                         actual=round(k / n * 100, 1), lo=round(lo * 100, 1), hi=round(hi * 100, 1),
                         roi=_f(g["profit"].sum() / staked * 100, 1) if staked else None))
    return dict(rows=rows, n=int(len(d)),
                brier=round(float(((d[col] - d["win"]) ** 2).mean()), 4))


def calibration_by_direction(df):
    d = df[df["decided"] & df["model_prob"].notna() & df["direction"].isin(["OVER", "UNDER"])]
    out = []
    for name in ("UNDER", "OVER"):
        g = d[d["direction"] == name]
        if g.empty:
            continue
        staked = float(g["stake"].sum())
        out.append(dict(name=name, n=int(len(g)), pred=round(float(g["model_prob"].mean()) * 100, 1),
                        actual=round(float(g["win"].mean()) * 100, 1),
                        diff=round(float(g["win"].mean() - g["model_prob"].mean()) * 100, 1),
                        roi=_f(g["profit"].sum() / staked * 100, 1) if staked else None))
    return out


def model_vs_market(df):
    d = df[df["decided"] & df["model_prob"].notna() & df["opp_odds"].notna() & (df["opp_odds"] > 1)].copy()
    if d.empty:
        return None
    d["market"] = (1 / d["odds"]) / ((1 / d["odds"]) + (1 / d["opp_odds"]))
    b_model = float(((d["model_prob"] - d["win"]) ** 2).mean())
    b_market = float(((d["market"] - d["win"]) ** 2).mean())
    d["gap"] = (d["model_prob"] - d["market"]) * 100
    d["grp"] = pd.cut(d["gap"], [-100, -3, 3, 100], labels=["Malli alle markkinan", "Samaa luokkaa", "Malli yli markkinan"])
    groups = []
    for lab, g in d.groupby("grp", observed=True):
        staked = float(g["stake"].sum())
        groups.append(dict(name=str(lab), n=int(len(g)), model=round(float(g["model_prob"].mean()) * 100, 1),
                           market=round(float(g["market"].mean()) * 100, 1),
                           actual=round(float(g["win"].mean()) * 100, 1),
                           roi=_f(g["profit"].sum() / staked * 100, 1) if staked else None))
    return dict(n=int(len(d)), brier_model=round(b_model, 4), brier_market=round(b_market, 4),
                hit=round(float(d["win"].mean()) * 100, 1), groups=groups)


def players(df):
    p = df[df["player"].notna()]
    if p.empty:
        return []
    g = p.groupby("player").agg(n=("id", "size"), profit=("profit", "sum")).reset_index()
    g = g.sort_values(["n"], ascending=False)
    return [dict(name=r.player, n=int(r.n), profit=round(float(r.profit), 2)) for r in g.itertuples()]


def player_detail(df, name):
    p = df[df["player"] == name]
    if p.empty:
        return None
    out = dict(summary=summary(p), equity=equity(p))
    split = {}
    total_dir = int(p["direction"].isin(["OVER", "UNDER"]).sum())
    for d in ("UNDER", "OVER"):
        g = p[p["direction"] == d]
        gd = g[g["decided"]]
        split[d] = dict(n=int(len(g)), share=(len(g) / total_dir * 100) if total_dir else 0.0,
                        decided=int(len(gd)), hit=_f(gd["win"].mean() * 100, 1) if len(gd) else None)
    out["split"] = split
    return out


def day_groups(df):
    """Ryhmittelee vedot päivittäin (uusin ensin) päiväkohtaisine yhteenvetoineen."""
    out = []
    if df.empty:
        return out
    for day, g in sorted(df.groupby(df["day"].fillna("tuntematon")), key=lambda kv: kv[0], reverse=True):
        s = summary(g)
        out.append(dict(day=day, bets=g.sort_values("id", ascending=False).to_dict("records"),
                        profit=s["profit"], roi=s["roi"], settled=s["settled"], pending=s["pending"]))
    return out


def monthly(df):
    s = df[df["settled"] & df["dt"].notna()]
    if s.empty:
        return []
    g = s.groupby(s["dt"].dt.strftime("%Y-%m")).agg(n=("id", "size"), profit=("profit", "sum"),
                                                     staked=("stake", "sum")).sort_index()
    return [dict(month=m, n=int(r.n), profit=round(float(r.profit), 2),
                 roi=round(float(r.profit / r.staked * 100), 1) if r.staked else 0.0) for m, r in g.iterrows()]
