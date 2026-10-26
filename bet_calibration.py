"""Vetojen kalibrointikerros: mallin todennäköisyys -> todennäköisyys johon
voi oikeasti luottaa.

MIKSI TÄMÄ ON OLEMASSA
----------------------
my_bets.db:n 1 465 ratkaistua vetoa (25.8.-26.10.2026) näyttävät, ettei
tappomallin oma todennäköisyys erottele osuvia ja hukkuvia vetoja:

    mallin arvio   vetoja   osui
    50-55 %          290    59.7 %
    55-60 %          482    60.8 %
    60-65 %          409    58.7 %
    65-70 %           99    65.7 %

Osumaprosentti on käytännössä sama mallin arviosta riippumatta (korrelaatio
0.03). Sen sijaan kerroin, suunta ja turnauksen koko selittävät tuloksia:
pienissä turnauksissa / karsinnoissa ROI -13.6 %, alle 1.60 kertoimilla -10 %.

Tämä moduuli sovittaa OMISTA ratkaistuista vedoistasi logistisen mallin:

    logit P(osuu) = a + b * logit(1/kerroin) + c * ALI + d * pieni_turnaus
                    + e * (logit(mallin_p) - logit(1/kerroin))

ja antaa sen perusteella todennäköisyyden ja odotusarvon uudelle vedolle.
Viimeinen termi on "kuinka paljon mallin erimielisyyteen kertoimen kanssa kannattaa
luottaa" - data päättää sen itse. Tällä hetkellä e on ~0 (malli ei lisää tietoa).
Jos tappomalli joskus paranee, e kasvaa automaattisesti eikä koodia tarvitse muuttaa.

Kaksi suojausta ylisovitusta vastaan:
  * Priori: ilman dataa kerroin pätee sellaisenaan (b = 1, muut 0). Data saa
    siirtää arvoja vain jos sillä on siihen näyttöä (PRIOR_SD).
  * Tuoreuspaino: puoliintumisaika HALF_LIFE_DAYS. Kertoimet ja markkina
    muuttuvat (ALI-kertoimesi laskivat elokuun ~1.78:sta lokakuun ~1.65:een),
    joten vanhat vedot painavat vähemmän.

MITATTU (walk-forward: joka viikko sovitus vain sitä edeltäviin vetoihin,
1 013 testivetoa): vedot jotka tämä kerros olisi hylännyt (EV < 2 %) tuottivat
-12 % ROI:n, hyväksytyt +1 %. Ero on suuntaa antava, ei varma - mutta se on
ainoa sääntö jonka testasin ennen käyttöä ja joka ei huonontanut tulosta.
"""

import math
import re
import sqlite3
from datetime import datetime

import numpy as np

DB_PATH = 'my_bets.db'

# Turnaukset joissa kannassa on vähän dataa joukkueista (karsinnat, kakkostaso).
# Lisää tähän uusia nimiä tarpeen mukaan - haku ei välitä isoista kirjaimista.
SMALL_EVENT_PATTERN = r'1win|logitech|logitecg|qualifier|karsinta|closed|open qual|cct|esea|united21'

HALF_LIFE_DAYS = 30      # walk-forward-testissä 30 pv > 60 pv > ei painotusta
PRIOR_SD = 0.5           # kuinka paljon data saa siirtää kertoimia priorista
MIN_BETS = 150           # tätä pienemmällä otoksella ei sovitusta (palautetaan priori)
MIN_EV = 0.02            # vaadittu odotusarvo kalibroidulla todennäköisyydellä

FEATURES = ['vakio', 'logit(1/kerroin)', 'ALI', 'pieni turnaus', 'mallin erimielisyys']


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def is_small_event(tournament):
    return bool(tournament) and re.search(SMALL_EVENT_PATTERN, str(tournament), re.I) is not None


def _design(odds, direction, small, model_p):
    odds = np.asarray(odds, dtype=float)
    lbe = _logit(1.0 / odds)
    mp = np.asarray(model_p, dtype=float)
    # Puuttuva mallin arvio = ei erimielisyyttä kertoimen kanssa
    dev = np.where(np.isnan(mp), 0.0, _logit(mp) - lbe)
    under = (np.asarray(direction) == 'UNDER').astype(float)
    return np.column_stack([np.ones(len(odds)), lbe, under,
                            np.asarray(small, dtype=float), dev])


def _prior_mean():
    mu = np.zeros(len(FEATURES))
    mu[1] = 1.0
    return mu


def fit_calibration(db_path=DB_PATH, half_life_days=HALF_LIFE_DAYS, prior_sd=PRIOR_SD,
                    as_of=None):
    """Sovittaa kertoimet ratkaistuihin vetoihin. Palauttaa sanakirjan
    {'coef': np.array, 'n': vetoja, 'n_eff': painotettu määrä, 'fitted': bool}.

    Newton-iteraatio painotetulle logistiselle regressiolle normaalipriorilla
    (= ridge kohti prioria). Ei scipy-riippuvuutta."""
    mu = _prior_mean()
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """SELECT date, odds, status, tournament, model_prob, direction, description
               FROM bets WHERE status IN ('Voitto', 'Tappio') AND odds > 1""").fetchall()
        conn.close()
    except sqlite3.Error:
        rows = []

    data = []
    for date, odds, status, tournament, model_p, direction, desc in rows:
        if not direction:   # vanhimmissa vedoissa suunta vain kuvauksessa
            m = re.search(r'\b(OVER|UNDER)\b', desc or '')
            direction = m.group(1) if m else None
        if direction not in ('OVER', 'UNDER'):
            continue
        try:
            dt = datetime.strptime(date[:16], '%Y-%m-%d %H:%M')
        except (TypeError, ValueError):
            continue
        data.append((dt, odds, status == 'Voitto', is_small_event(tournament),
                     np.nan if model_p is None else model_p, direction))

    if len(data) < MIN_BETS:
        return {'coef': mu, 'n': len(data), 'n_eff': float(len(data)), 'fitted': False}

    now = as_of or max(d[0] for d in data)
    age = np.array([max((now - d[0]).total_seconds() / 86400.0, 0.0) for d in data])
    w = 0.5 ** (age / half_life_days) if half_life_days else np.ones(len(data))
    X = _design([d[1] for d in data], [d[5] for d in data], [d[3] for d in data],
                [d[4] for d in data])
    y = np.array([d[2] for d in data], dtype=float)
    P = np.eye(len(mu)) / prior_sd ** 2

    b = _newton(X, y, w, mu, P)
    # Mallin erimielisyyden paino ei saa olla negatiivinen: negatiivinen arvo
    # tarkoittaisi "lyö sitä enemmän mitä vähemmän malli uskoo vetoon", mikä on
    # kohinaa eikä mitään mihin kannattaa luottaa. Silloin termi pudotetaan pois.
    if b[4] < 0:
        X[:, 4] = 0.0
        b = _newton(X, y, w, mu, P)
        b[4] = 0.0
    return {'coef': b, 'n': len(data), 'n_eff': float(w.sum()), 'fitted': True}


def _newton(X, y, w, mu, P, iters=50):
    """Painotettu logistinen regressio normaalipriorilla (Newton-Raphson)."""
    b = mu.copy()
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(X @ b)))
        grad = X.T @ (w * (y - p)) - P @ (b - mu)
        H = X.T @ (X * (w * p * (1 - p))[:, None]) + P
        step = np.linalg.solve(H, grad)
        b = b + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return b


def calibrated_probability(model_p, odds, direction, tournament, cal):
    """Kalibroitu osumatodennäköisyys ja odotusarvo yhdelle vedolle."""
    X = _design([odds], [direction], [is_small_event(tournament)],
                [np.nan if model_p is None else model_p])
    p = float(1.0 / (1.0 + np.exp(-(X @ cal['coef'])[0])))
    return p, p * odds - 1.0


def bet_verdict(model_p, odds, direction, tournament, cal, min_ev=MIN_EV):
    """(lyödäänkö, kalibroitu_p, ev, perustelu)"""
    p, ev = calibrated_probability(model_p, odds, direction, tournament, cal)
    breakeven = 1.0 / odds
    if ev >= min_ev:
        why = (f"Kalibroitu osumatodennäköisyys {p*100:.1f} % > kertoimen vaatima "
               f"{breakeven*100:.1f} % (EV {ev*100:+.1f} %).")
        return True, p, ev, why
    why = (f"Kalibroitu osumatodennäköisyys {p*100:.1f} %, kerroin vaatii {breakeven*100:.1f} % "
           f"→ EV {ev*100:+.1f} % (raja {min_ev*100:+.0f} %).")
    if is_small_event(tournament):
        why += " Pienissä turnauksissa / karsinnoissa vetosi ovat hävinneet selvästi."
    return False, p, ev, why


def min_odds_for_value(model_p, direction, tournament, cal, min_ev=MIN_EV):
    """Pienin kerroin jolla veto ylittää EV-rajan (kätevä kun kerroin heiluu)."""
    for o in np.arange(1.30, 3.01, 0.01):
        _, ev = calibrated_probability(model_p, o, direction, tournament, cal)
        if ev >= min_ev:
            return round(float(o), 2)
    return None


def describe(cal):
    """Luettava selitys siitä mitä kalibrointi on vedoistasi oppinut."""
    b = cal['coef']
    lines = [f"Sovitettu {cal['n']} ratkaistuun vetoon (tuoreuspainotettuna ~{cal['n_eff']:.0f})."
             if cal['fitted'] else
             f"Vetoja vasta {cal['n']} - kalibrointi käyttää prioria (kerroin sellaisenaan)."]
    lines.append(f"• Kertoimen paino {b[1]:.2f} (1.00 = markkinan hinta pätee sellaisenaan)")
    lines.append(f"• ALI-vedot {b[2]:+.2f} logit-yks. suhteessa YLI-vetoihin")
    lines.append(f"• Pieni turnaus / karsinta {b[3]:+.2f} logit-yks.")
    lines.append(f"• Mallin erimielisyyden paino {b[4]:+.2f} (0 = tappomalli ei lisää tietoa "
                 f"kertoimen päälle, 1 = mallin arvio pätee täysin)")
    return "\n".join(lines)
