"""Tappomallin taaksepäintesti: VANHA malli vs. UUDET korjaukset.

Ajo:  python backtest_kills.py [polku/hltv_data.db]

Mitä tämä tekee
---------------
1. Rakentaa jokaiselle pelaaja-kartalle ENNUSTEEN käyttäen vain sitä edeltävää
   dataa (ei vuotoa tulevaisuudesta): aikapainotettu KPR (puoliintumisaika 150 pv),
   shrinkage, 30 pv:n muoto sekä vastustajan viimeisten 25 kartan "sallitut tapot".
2. Sovittaa uudet kalibrointivakiot aineiston ENSIMMÄISELLE 55 %:lle ja testaa ne
   viimeisellä 45 %:lla (aito out-of-sample).
3. Vertaa VANHAA mallia (form 0,3, ei korjauksia) ja UUTTA mallia (form 0,1,
   KPR-kalibrointi, vastustajakorjaus, karttakerroin) saman simulaation läpi.
4. Tulostaa lopuksi vakiot, jotka sovitetaan KOKO aineistoon ja kopioidaan
   players_props.py:n alkuun (KPR_CAL_*, OPP_ALLOWED_*, MAP_KILL_FACTORS).

HUOM: ennuste on likiarvo tuotantomallista - shrinkage menee tässä koko
aineiston keskiarvoon (tuotannossa joukkueen keskiarvoon), eikä rosteri-
historiaa seurata. Ero on pieni, mutta älä tulkitse lukuja sadasosaprosentin
tarkkuudella. Aja uudestaan kun kantaan on kertynyt lisää otteluita.
"""
import sqlite3
import sys

import numpy as np
import pandas as pd

HALF_LIFE = 150
SHRINK = 200
TEST_FRACTION = 0.45
OPP_WINDOW, OPP_MIN = 25, 8
OVERDISP = 1.10
FORM_OLD, FORM_NEW = 0.3, 0.05
# MUOTO: 'kpr_rec30' = viimeiset 30 päivää (vähintään 3 karttaa) tai
#        'kpr_rec6'  = pelaajan viimeiset FORM_LAST_N karttaa (iästä riippumatta).
FORM_COL = 'kpr_rec6'
FORM_LAST_N = 6
OPP_CLIP = 0.04          # suurin sallittu KPR-korjaus vastustajasta
MAP_MIN_N = 300          # kartta saa oman kertoimen vain jos otosta riittävästi
KPR_INTERCEPT, KPR_SLOPE = 1.825, 2.937   # sama fallback kuin match_simulator.py:ssä


def load(db):
    c = sqlite3.connect(db)
    df = pd.read_sql_query(
        """SELECT s.match_id, s.player_id, s.kills, m.team1_id, m.team2_id,
                  m.score_team1 s1, m.score_team2 s2, m.map_name, m.match_date,
                  p.team_id pteam
           FROM player_stats s JOIN matches m ON m.id = s.match_id
           JOIN players p ON p.id = s.player_id""", c)
    c.close()
    df = df[df.match_date > '2020']
    df['dt'] = pd.to_datetime(df.match_date, errors='coerce')
    df = df.dropna(subset=['dt'])
    df['rounds'] = df.s1 + df.s2
    df = df[df.rounds >= 13].copy()
    df['kpr'] = df.kills / df.rounds
    df['share'] = np.where(df.pteam == df.team1_id, df.s1 / df.rounds,
                           np.where(df.pteam == df.team2_id, df.s2 / df.rounds, np.nan))
    df['opp'] = np.where(df.pteam == df.team1_id, df.team2_id,
                         np.where(df.pteam == df.team2_id, df.team1_id, None))
    return df.sort_values(['dt', 'match_id']).reset_index(drop=True)


def player_features(df):
    """Aikapainotettu+shrunk KPR ja 30 pv:n KPR vain aiemmasta datasta."""
    glob = df.kpr.mean()
    rows = []
    for _pid, g in df.groupby('player_id'):
        g = g.sort_values('dt')
        days = g.dt.values.astype('datetime64[D]').astype(int)
        k, r = g.kills.values, g.rounds.values
        for i in range(8, len(g)):
            d = days[i] - days[:i]
            w = 0.5 ** (d / HALF_LIFE)
            wk, wr = (k[:i] * w).sum(), (r[:i] * w).sum()
            kpr = wk / wr
            shr = (kpr * wr + glob * SHRINK) / (wr + SHRINK)
            rec = d <= 30
            rkpr30 = k[:i][rec].sum() / r[:i][rec].sum() if rec.sum() >= 3 else np.nan
            lo = max(0, i - FORM_LAST_N)
            rkpr6 = k[lo:i].sum() / r[lo:i].sum() if i - lo >= 3 else np.nan
            rows.append((g.index[i], shr, rkpr30, rkpr6))
    f = pd.DataFrame(rows, columns=['ix', 'kpr_shr', 'kpr_rec30', 'kpr_rec6']).set_index('ix')
    return df.join(f, how='inner')


def opponent_allowed(df):
    """Per (ottelu, vastustaja): vastustajan viimeisten OPP_WINDOW kartan
    'sallitut tapot' = niiden joukkueiden tappoja/kierros jotka pelasivat sitä vastaan."""
    x = df[df.opp.notna()]
    tm = x.groupby(['match_id', 'pteam']).agg(
        tk=('kills', 'sum'), n=('kills', 'size'), rounds=('rounds', 'first'),
        dt=('dt', 'first'), opp=('opp', 'first')).reset_index()
    tm = tm[tm.n == 5]
    tm['tkpr'] = tm.tk / tm.rounds
    league = tm.tkpr.mean()
    out = []
    for team, g in tm.groupby('opp'):
        g = g.sort_values('dt')
        s = g.tkpr.shift(1).rolling(OPP_WINDOW, min_periods=OPP_MIN).mean()
        out.append(pd.DataFrame({'match_id': g.match_id.values, 'opp': team,
                                 'opp_allowed': s.values}))
    return pd.concat(out), league


def blend(d, fw, col=None):
    rec = d[col or FORM_COL]
    return np.where(rec.notna(), (1 - fw) * d.kpr_shr + fw * rec, d.kpr_shr)


def fit_constants(tr, _league_unused=None):
    """Sovittaa uuden mallin vakiot annetulle aineistolle.

    Vastustajakorjaus keskitetään SOVITUSAINEISTON omaan keskiarvoon
    (opp_allowed-sarakkeen keskiarvo niillä riveillä joilla se on), jotta
    korjaus on keskimäärin nolla eikä sekoitu tasokertoimeen. Joukkueet joilla
    ei ole tarpeeksi dataa saavat korjauksen 0."""
    league = float(tr.opp_allowed.mean())
    base = blend(tr, FORM_NEW)
    slope, icpt = np.polyfit(base, tr.kpr.values, 1)
    cal = icpt + slope * base
    res = tr.kpr.values - cal
    ok = tr.opp_allowed.notna().values
    xo = (tr.opp_allowed.values - league)[ok]
    c = float(np.polyfit(xo, res[ok], 1)[0])
    adj = np.zeros(len(tr))
    adj[ok] = np.clip(c * xo, -OPP_CLIP, OPP_CLIP)
    pred = cal + adj
    num = tr.kills.groupby(tr.map_name).sum()
    den = pd.Series(pred * tr.rounds.values, index=tr.index).groupby(tr.map_name).sum()
    cnt = tr.groupby('map_name').size()
    mf = (num / den).where(cnt >= MAP_MIN_N, 1.0)

    # TASOKERROIN (kohta 3). KPR kalibroidaan kierros-kohtaisena keskiarvona, mutta
    # simulaatio kertoo sen KIERROKSILLA (kills = KPR x kierrokset x share_mult),
    # ja kierrosmäärä korreloi KPR:n kanssa. Seurauksena putken keskimääräinen tappo-
    # ennuste oli ~1,3 % liian korkea. Kerroin mitataan niin että putken ODOTUSARVO
    # (kierrokset ja shares otetaan samasta aineistosta kuin simulaatiossa) osuu
    # toteutuneisiin tappoihin.
    pl = tr[tr.share.notna()]
    e_rm = float((pl.rounds.values * share_mult(pl.share.values)).mean())
    pred_final = pred * tr.map_name.map(mf).fillna(1.0).values
    level = float(tr.kills.mean() / (pred_final.mean() * e_rm))
    return dict(slope=float(slope), intercept=float(icpt), opp_pass=c,
                league=float(league), level=level, map_factors=mf.round(4).to_dict())


def predict_new(d, k, league=None):
    league = k['league']
    base = blend(d, FORM_NEW)
    cal = k['intercept'] + k['slope'] * base
    ok = d.opp_allowed.notna().values
    adj = np.zeros(len(d))
    adj[ok] = np.clip(k['opp_pass'] * (d.opp_allowed.values - league)[ok], -OPP_CLIP, OPP_CLIP)
    mf = d.map_name.map(k['map_factors']).fillna(1.0).values
    return (cal + adj) * mf * k['level']


def predict_old(d):
    return blend(d, FORM_OLD, 'kpr_rec30')


def share_mult(s):
    return (KPR_INTERCEPT + KPR_SLOPE * s) / (KPR_INTERCEPT + KPR_SLOPE * 0.5)


def simulate(pred_kpr, R, M, rng):
    mean = pred_kpr[:, None] * R * M
    return rng.negative_binomial(np.maximum(mean / (OVERDISP - 1), 1e-6), 1 / OVERDISP)


def cluster_se(diff, groups):
    g = pd.Series(diff).groupby(np.asarray(groups)).sum()
    n = len(diff)
    return np.sqrt((g ** 2).sum()) / n


def main(db):
    rng = np.random.default_rng(11)
    df = load(db)
    d = player_features(df)
    oa, league = opponent_allowed(df)
    d = d.merge(oa, on=['match_id', 'opp'], how='left').sort_values('dt').reset_index(drop=True)
    cut = int(len(d) * (1 - TEST_FRACTION))
    tr, te = d.iloc[:cut].copy(), d.iloc[cut:].copy()

    print("\nMUODON PAINO: virhe (KPR, painotettu kierroksilla, x1e4) - pienempi = parempi")
    print("  (pelkät rivit joilla kumpikin muotoluku on olemassa; viimeiset 45 % = out-of-sample)")
    both = te[te.kpr_rec30.notna() & te.kpr_rec6.notna()]
    w = both.rounds.values
    for col, lab in (('kpr_rec30', '30 pv  '), ('kpr_rec6', f'{FORM_LAST_N} karttaa')):
        errs = []
        for fw in (0.0, 0.05, 0.1, 0.15, 0.2, 0.3):
            p_ = (1 - fw) * both.kpr_shr + fw * both[col]
            errs.append(f"w={fw:.2f}: {(w * (both.kpr - p_) ** 2).sum() / w.sum() * 1e4:.3f}")
        print(f"  {lab}  " + "  ".join(errs) + f"   (n={len(both)})")
    print(f"Pelaaja-karttoja: {len(d)} (train {len(tr)}, test {len(te)}); "
          f"aikaväli {d.dt.min().date()} - {d.dt.max().date()}")

    k = fit_constants(tr)
    print("\nTRAIN-sovitetut vakiot:", {a: (round(v, 4) if not isinstance(v, dict) else v)
                                      for a, v in k.items()})

    pool = d[d.share.notna()][['rounds', 'share']].values
    S = 120
    idx = rng.integers(0, len(pool), (len(te), S))
    R, M = pool[idx, 0], share_mult(pool[idx, 1])
    a = te.kills.values

    variants = {'VANHA': predict_old(te), 'UUSI': predict_new(te, k)}
    Ks = {n: simulate(v, R, M, rng) for n, v in variants.items()}

    print("\nOUT-OF-SAMPLE, tappojen keskineliövirhe (todelliset kierrokset):")
    for n, v in variants.items():
        print(f"  {n}: {((a - v * te.rounds.values) ** 2).mean():.3f}   "
              f"harha {np.mean(a - v * te.rounds.values):+.3f} tappoa")

    # REILU VERTAILU: molemmat mallit pisteytetään SAMOILLA kiinteillä linjoilla
    # (kuten oikeassa vedonlyönnissä bookkerin linja on annettu, ei mallin oma).
    # Aiempi versio valitsi linjan kunkin mallin omasta mediaanista, jolloin
    # mallit pisteytettiin eri vedoilla keskenään.
    print("\nUNDER-todennäköisyys KIINTEILLÄ linjoilla 10,5-19,5 (kaikille pelaajille samat):")
    lines = np.arange(10.5, 20.0, 1.0)
    brier = {n: np.zeros(len(te)) for n in variants}
    for L in lines:
        au = (a < L).astype(float)
        row = []
        for n in variants:
            pu = (Ks[n] < L).mean(1)
            brier[n] += (pu - au) ** 2 / len(lines)
            row.append(f"{n}: P={pu.mean():.3f}")
        print(f"  linja {L:4.1f}:  " + "  |  ".join(row) + f"  |  toteutunut={au.mean():.3f}")
    diff = brier['UUSI'] - brier['VANHA']
    print(f"\nKeskimääräinen Brier  VANHA {brier['VANHA'].mean():.5f}  UUSI {brier['UUSI'].mean():.5f}")
    print(f"Ero UUSI - VANHA (neg. = uusi parempi): {diff.mean():+.5f} "
          f"± {cluster_se(diff, te.match_id.values):.5f} (klusteroitu ottelun mukaan)")

    # Kuinka usein malli sanoo "arvoa" (>= 5 %-yks. markkinan/vertailun yli)? Ei laskettavissa
    # ilman bookkerin kertoimia, joten tässä vain kalibrointi.
    def table(name, key, labels=None):
        print(f"\nKalibrointi ({name}), UNDER VANHAN mallin keskilinjalla (sama linja molemmille): mallin P / toteutunut")
        Lref = np.floor(Ks['VANHA'].mean(1)) + 0.5     # sama linja molemmille
        for n in variants:
            K = Ks[n]
            L = Lref
            t = pd.DataFrame({'pu': (K < L[:, None]).mean(1), 'au': (a < L).astype(float), 'g': key})
            s = t.groupby('g', observed=True)[['pu', 'au']].mean()
            print(f"  {n}: " + "  ".join(f"{i}:{r.pu * 100:.0f}/{r.au * 100:.0f}" for i, r in s.iterrows()))

    table('KPR-viidennekset', pd.qcut(blend(te, FORM_NEW), 5,
                                      labels=['matala', '2', '3', '4', 'TOP']))
    oq = pd.qcut(te.opp_allowed, 5, labels=['antaa vähän', '2', '3', '4', 'antaa paljon'])
    table('vastustajan sallimat tapot', oq)
    mp = te.map_name.where(te.map_name.isin(k['map_factors'].keys()))
    table('kartta', mp)

    # Lopulliset vakiot koko aineistosta (tuotantoon)
    full = fit_constants(d)
    print("\n=== KOPIOI players_props.py:hin (sovitettu KOKO aineistoon) ===")
    print(f"KPR_CAL_INTERCEPT = {full['intercept']:.4f}")
    print(f"KPR_CAL_SLOPE = {full['slope']:.4f}")
    print(f"OPP_ALLOWED_PASS = {full['opp_pass']:.4f}")
    print(f"OPP_ALLOWED_LEAGUE = {full['league']:.4f}")
    print(f"LEVEL_ADJ = {full['level']:.4f}")
    print(f"MAP_KILL_FACTORS = {full['map_factors']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else 'hltv_data.db')
