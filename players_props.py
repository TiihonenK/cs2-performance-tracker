import pandas as pd
import numpy as np
import sqlite3
from datetime import datetime
import math

# ============================================================================
# KIERROSMÄÄRÄ: kierrostason simulaatio (korvaa vanhan bootstrap-kaavan)
# ============================================================================
# Vanha sample_match_lengths laski tavoitekeskiarvon kaavalla
#     21.5 - |p - 0.5| * 2 * 8.5
# jonka kerroin 8.5 ei ollut kalibroitu mihinkään. Se aliarvioi kierrosmäärän
# rajusti heti kun ottelu ei ollut 50/50: kertoimella 1.33 (p=0.75) kaava antoi
# 17.3 kierrosta, kun todellinen odotusarvo on 22.2. Koska tapot skaalautuvat
# suoraan kierroksiin, kaikki tappoprojektiot olivat ~20-25 % liian matalia.
#
# Nyt kierrosmäärä simuloidaan kierros kierrokselta (ks. match_simulator.py):
# kertoimista -> kartan voittotodennäköisyys -> kierrosvoittotodennäköisyys ->
# pelataan kartta MR12-säännöillä jatkoaikoineen. Ei viritettäviä maagisia
# kertoimia, ja jakauman muoto (mahdottomat 25-27 ja 31-33 puuttuvat, jatkoajan
# osuus) tulee oikein ilman bootstrappia.

from match_simulator import (
    simulate_match_context,
    kpr_share_multiplier,
    round_prob_from_map_prob,
    map_win_prob,
)


def sample_match_lengths(win_prob, num_simulations=10000, **kwargs):
    """VANHENTUNUT - säilytetty vain jotta vanha koodi ei kaadu.

    Palauttaa pelkät kierrosmäärät. Käytä mieluummin
    simulate_match_context(), joka palauttaa myös kummankin joukkueen
    kierrososuuden - simulate_team_kills tarvitsee sen."""
    return simulate_match_context(win_prob, num_simulations)['rounds'].astype(float)


# Painotuksen puoliintumisaika päivinä (ks. get_team_players_overall_stats).
#
# MITATTU TAAKSEPÄINTESTILLÄ (4691 pelaaja-karttaa, aito out-of-sample):
#   puoliintumisaika  60 pv -> r=0.271
#                    120 pv -> r=0.275
#                    180 pv -> r=0.275
#          ei painotusta    -> r=0.275
# Tuoreuspainotus ei siis paranna ennustetta käytännössä lainkaan, ja liian
# lyhyt puoliintumisaika HUONONTAA sitä (melu voittaa signaalin). 150 pv on
# käytännössä tasapaino: pitkä muisti, mutta rosterinvaihdos ehtii silti näkyä.
HALF_LIFE_DAYS = 150


def devig_two_way(odds_a, odds_b):
    """Poistaa marginaalin kaksipuolisesta markkinasta ja palauttaa
    (fair_p_a, fair_p_b, marginaali_prosentteina).

    MIKSI TÄMÄ ON TÄRKEÄ: pelaajien tappomarkkinoissa marginaali on tyypillisesti
    10-20 %, eli moninkertainen ottelun voittajaan (3-5 %) verrattuna. Kerroin 1.30
    NÄYTTÄÄ tarkoittavan 76.9 % todennäköisyyttä, mutta 15 %:n marginaalilla
    todellinen arvio on vain n. 67 %. Jos vertaat mallia raakaan 1/kerroin-lukuun,
    markkina näyttää aina paljon varmemmalta kuin se on - ja malli näyttää
    systemaattisesti "liian härältä" vaikka se olisi oikeassa.
    """
    ia, ib = 1.0 / odds_a, 1.0 / odds_b
    total = ia + ib
    return ia / total, ib / total, (total - 1.0) * 100.0


def line_edge_table(sim_kills, book_lines):
    """Vertaa mallia bookkerin KOKO linjatikkaisiin kerralla.

    book_lines: lista (linja, kerroin_over, kerroin_under). Anna kerroin 0 tai None
    jos toista puolta ei ole tarjolla - silloin marginaalia ei voi poistaa ja
    rivi merkitään sen mukaan.

    Palauttaa riveittäin mallin todennäköisyyden, marginaalista puhdistetun
    markkinatodennäköisyyden ja odotusarvon (EV) molemmille puolille.
    """
    sim_kills = np.asarray(sim_kills)
    rows = []
    for line, o_over, o_under in book_lines:
        p_over, p_under, fair_over, fair_under = kills_probability_at_line(sim_kills, line)
        row = {'Linja': line, 'Malli P(over)': f"{p_over*100:.1f}%",
               'Mallin raja (over)': fair_over, 'Mallin raja (under)': fair_under}
        if o_over and o_under and o_over > 1 and o_under > 1:
            mp_over, mp_under, margin = devig_two_way(o_over, o_under)
            row['Markkina P(over)'] = f"{mp_over*100:.1f}%"
            row['Marginaali'] = f"{margin:.1f}%"
            row['EV over'] = f"{(p_over*o_over-1)*100:+.1f}%"
            row['EV under'] = f"{(p_under*o_under-1)*100:+.1f}%"
        else:
            row['Markkina P(over)'] = "-"
            row['Marginaali'] = "ei laskettavissa"
            row['EV over'] = f"{(p_over*o_over-1)*100:+.1f}%" if o_over and o_over > 1 else "-"
            row['EV under'] = f"{(p_under*o_under-1)*100:+.1f}%" if o_under and o_under > 1 else "-"
        rows.append(row)
    return rows


def get_team_players_overall_stats(team_name, shrinkage_rounds=200):
    """Hakee tilastot aktiiviselle rosterille ja painottaa KPR-laskennassa
    tuoreimpia otteluita.

    shrinkage_rounds: kuinka monen kierroksen verran "painoa" annetaan joukkueen
    painotetulle keskiarvo-KPR:lle pienen otoksen pelaajien tasoittamisessa
    (empiirinen Bayes -tyylinen shrinkage). 200 kierrosta vastaa n. 9-10 kartan
    otosta - pelaaja jolla on vain 1-2 karttaa taustalla vedetään voimakkaasti
    kohti joukkueen keskiarvoa, kokenut pelaaja tuskin ollenkaan.
    """
    conn = sqlite3.connect('hltv_data.db')
    c = conn.cursor()

    # 1. Etsitään joukkueen kaikkein viimeisin ottelu (match_id) rosterin tunnistamiseksi
    recent_match_query = """
    SELECT id FROM matches
    WHERE team1_id = (SELECT id FROM teams WHERE name = ?)
       OR team2_id = (SELECT id FROM teams WHERE name = ?)
    ORDER BY match_date DESC
    LIMIT 1
    """
    c.execute(recent_match_query, (team_name, team_name))
    row = c.fetchone()

    if not row:
        conn.close()
        return []

    last_match_id = row[0]

    # 2. Haetaan kaikkien pelattujen karttojen tappomäärät, PÄIVÄMÄÄRÄT ja
    #    KORJAUS 2: myös m.score_team1 / m.score_team2, jotta KPR voidaan laskea
    #    todellisilla pelatuilla kierroksilla kiinteän 21.5-oletuksen sijaan.
    query = """
    SELECT p.name, s.kills, m.match_date, m.score_team1, m.score_team2
    FROM players p
    JOIN player_stats s ON p.id = s.player_id
    JOIN matches m ON s.match_id = m.id
    WHERE p.id IN (
        SELECT player_id FROM player_stats WHERE match_id = ?
    )
    AND (p.team_name = ? OR p.team_id = (SELECT id FROM teams WHERE name = ?))
    """

    df = pd.read_sql_query(query, conn, params=(last_match_id, team_name, team_name))
    conn.close()

    if df.empty:
        return []

    # KORJAUS 2: oikea kierrosmäärä per kartta kiinteän 21.5:n sijaan
    df['total_rounds'] = df['score_team1'] + df['score_team2']
    df = df[df['total_rounds'] > 0]
    if df.empty:
        return []

    # 3. LASKETAAN AIKAPAINOTETTU KPR (Recent form)
    df['match_date'] = pd.to_datetime(df['match_date'], format='%Y-%m-%d', errors='coerce')
    now = pd.to_datetime('today')
    df['days_ago'] = (now - df['match_date']).dt.days.fillna(90)
    # KORJAUS: eksponentiaalinen puoliintumisaika lineaarisen 1.0-1.5 painon sijaan.
    # Vanha paino antoi 240 päivää vanhalle kartalle painon 1.0 ja tuoreimmalle 1.5 -
    # eli lähes tasapaino koko ikkunalle. Pelaajan rooli ja taso muuttuvat nopeammin
    # kuin se. HALF_LIFE_DAYS=60 tarkoittaa että 60 pv vanha kartta painaa puolet
    # tuoreesta ja 240 pv vanha enää 1/16 - malli seuraa nykyistä muotoa selvästi
    # tarkemmin. Nosta lukua jos projektiot heiluvat liikaa, laske jos ne laahaavat.
    df['weight'] = 0.5 ** (df['days_ago'] / HALF_LIFE_DAYS)

    raw_players = []
    for name, group in df.groupby('name'):
        weighted_kills = (group['kills'] * group['weight']).sum()
        weighted_rounds = (group['total_rounds'] * group['weight']).sum()
        actual_rounds_sum = group['total_rounds'].sum()

        raw_kpr = weighted_kills / weighted_rounds if weighted_rounds > 0 else 0
        overall_kpr = group['kills'].sum() / actual_rounds_sum if actual_rounds_sum > 0 else 0

        # --- FORMIN LASKENTA ---
        recent_group = group[group['days_ago'] <= 30]
        recent_maps = len(recent_group)
        recent_rounds = recent_group['total_rounds'].sum()

        if recent_maps >= 3 and recent_rounds > 0:
            recent_kpr = recent_group['kills'].sum() / recent_rounds
            form_diff = recent_kpr - overall_kpr

            if form_diff >= 0.02:
                form_str = f"🔥 +{form_diff:.2f}"
            elif form_diff <= -0.02:
                form_str = f"🧊 {form_diff:.2f}"
            else:
                form_str = f"➖ {form_diff:+.2f}"
        else:
            recent_kpr = None  # ei tarpeeksi tuoreita pelejä muodon arviointiin
            form_str = "N/A (Liian vähän pelejä)"

        raw_players.append({
            'name': name,
            'weighted_kills': weighted_kills,
            'weighted_rounds': weighted_rounds,
            'raw_kpr': raw_kpr,
            'recent_kpr': recent_kpr,
            'n': int(actual_rounds_sum),
            'form': form_str,
        })

    # KORJAUS 5 (osittainen): pienen otoksen shrinkage kohti joukkueen keskiarvoa.
    # Vastustajan tason huomiointi (Elo-painotus) vaatisi team_elo_calculator.py:n
    # dataa eikä ole vielä mukana tässä - ks. kommentti tiedoston lopussa.
    team_weighted_kills = sum(p['weighted_kills'] for p in raw_players)
    team_weighted_rounds = sum(p['weighted_rounds'] for p in raw_players)
    team_avg_kpr = team_weighted_kills / team_weighted_rounds if team_weighted_rounds > 0 else 0

    players = []
    for p in raw_players:
        n_eff = p['weighted_rounds']
        shrunk_kpr = (p['raw_kpr'] * n_eff + team_avg_kpr * shrinkage_rounds) / (n_eff + shrinkage_rounds)

        players.append({
            'name': p['name'],
            'kpr': shrunk_kpr,
            'recent_kpr': p['recent_kpr'],
            'n': p['n'],
            'form': p['form'],
        })

    return sorted(players, key=lambda x: x['kpr'], reverse=True)


def simulate_team_kills(players_data, sim_rounds, sim_share,
                        overdispersion=1.10, form_weight=0.3):
    """Simuloi koko joukkueen kerralla ja palauttaa datan taulukkoa varten.

    sim_rounds: simuloidut kokonaiskierrosmäärät (ctx['rounds']).
    sim_share:  TÄMÄN joukkueen kierrososuus samoissa simulaatioissa
                (ctx['share_t1'] tai ctx['share_t2']).

    Molemmat tulevat samasta simulate_match_context-kutsusta, joten kummankin
    joukkueen pelaajat pelaavat saman simuloidun kartan.

    KORJAUS: pelaajan tappotahti skaalataan nyt simulaation TOTEUTUNEELLA
    kierrososuudella, ei ennakkotodennäköisyydellä. Vanha kerroin
        1 + (win_prob - 0.5) * 0.50
    oli noin kaksinkertainen kannasta mitattuun nähden (mitattu: joukkueen
    tapot/kierros = 1.82 + 2.94 * kierrososuus, eli +8.9 % kun osuus nousee
    0.50 -> 0.60, ei +10 % pelkästä ennakkosuosikkiudesta). Tärkeämpää on että
    sidonta toteutuneeseen osuuteen tuottaa oikean korrelaation: kartassa jonka
    joukkue häviää 4-13 sillä on sekä vähän kierroksia ETTÄ matala tappotahti.

    overdispersion: Poissonin sijaan negatiivinen binomijakauma, var = k * mean.
    MITATTU TAAKSEPÄINTESTILLÄ (PIT-kalibrointi, 4691 pelaaja-karttaa):
        1.05 -> poikkeama tasajakaumasta 4.6 %
        1.10 -> 4.8 %   <- käytössä
        1.25 -> 7.3 %   (vanha arvo, liian leveä)
        1.50 -> 12.0 %
    Vanha 1.25 oli kaksinkertaista kirjanpitoa: kierrosmäärän ja kierrososuuden
    satunnaisuus tuo jo oman hajontansa, joten päälle ei tarvita paljoakaan.
    Liian leveä jakauma näyttää arvoa kaukaisilla linjoilla siellä missä sitä ei ole.
    """
    sim_rounds = np.asarray(sim_rounds, dtype=float)
    sim_share = np.asarray(sim_share, dtype=float)
    n_sims = len(sim_rounds)

    # Kerroin per simulaatio, ei enää yksi vakio koko ottelulle
    share_mult = np.asarray(kpr_share_multiplier(sim_share), dtype=float)

    results = []
    for p in players_data:
        base_kpr = p['kpr']
        if p.get('recent_kpr') is not None:
            base_kpr = (1 - form_weight) * base_kpr + form_weight * p['recent_kpr']

        eff_kpr = max(base_kpr, 0.001)
        expected_k = np.maximum(sim_rounds * eff_kpr * share_mult, 1e-6)

        # Negatiivinen binomijakauma: var = overdispersion * mean (vakio p-parametri)
        if overdispersion > 1.0:
            p_param = 1.0 / overdispersion
            r_param = np.maximum(expected_k / (overdispersion - 1.0), 1e-6)
            sim_kills = np.random.negative_binomial(r_param, p_param)
        else:
            sim_kills = np.random.poisson(expected_k)

        proj_k = np.mean(sim_kills)
        std_k = np.std(sim_kills)

        # Mallin oma keskikohta = projektion pyöristys alaspäin + 0.5. Tämä EI ole
        # linja johon voi lyödä eikä sen kuulu osua bookkerin lukuun - se on vain
        # luettava tapa näyttää mihin projektio asettuu. Varsinainen työkalu on
        # kills_probability_at_line(), joka hinnoittelee bookkerin OIKEAN linjan.
        base_line = int(math.floor(proj_k))
        line = base_line + 0.5

        p_over = np.sum(sim_kills > line) / n_sims
        p_under = np.sum(sim_kills < line) / n_sims

        results.append({
            'Pelaaja': p['name'],
            'N': p['n'],
            'KPR': round(p['kpr'], 3),
            'Proj.K': round(proj_k, 2),
            'stdK': round(std_k, 2),
            'Mallin linja': line,
            'P.over': f"{p_over * 100:.1f}%",
            'P.under': f"{p_under * 100:.1f}%",
            'Form (30d)': p['form'],
            '_sim_kills': sim_kills,  # raakadata jatkokäyttöön (pudota näyttötaulukosta)
        })

    return sorted(results, key=lambda x: x['KPR'], reverse=True)


# ============================================================================
# KORJAUS 1: todennäköisyys/kerroinraja NIMENOMAISELLE (esim. bookkerin) linjalle
# ============================================================================
# Tämä on koko korjauspaketin tärkein osa. Aiempi malli laski P.over/P.under
# vain omalle automaattisesti generoidulle linjalleen ("Line"-sarake yllä),
# mikä ei kerro mitään siitä onko BOOKKERIN oikea linja väärin hinnoiteltu.
# Näillä kahdella funktiolla lasketaan mallin arvio mille tahansa linjalle.

def kills_probability_at_line(sim_kills, line):
    """Palauttaa mallin arvion tietylle NIMENOMAISELLE linjalle (esim. bookkerin
    oikea tarjoama luku 15.5), ei mallin omalle auto-generoidulle linjalle.

    Palauttaa: (p_over, p_under, kerroinraja_over, kerroinraja_under)
    Kerroinraja = "reilu" kerroin (1 / todennäköisyys). Jos bookkerin oikea
    kerroin on TÄTÄ SUUREMPI, kyseessä on mallin mukaan arvoveto.
    """
    sim_kills = np.asarray(sim_kills)
    n = len(sim_kills)
    p_over = float(np.sum(sim_kills > line)) / n
    p_under = float(np.sum(sim_kills < line)) / n
    odds_over = round(1 / p_over, 2) if p_over > 0 else None
    odds_under = round(1 / p_under, 2) if p_under > 0 else None
    return p_over, p_under, odds_over, odds_under


def generate_line_table(sim_kills, spread=3.5, step=0.5):
    """Taulukko kerroinrajoista usealle linjalle kerralla mallin projektion
    ympärillä - sama periaate kuin betting_calculator.py:n joukkuevedoissa:
    etsi bookkerilta kerroin joka on SUUREMPI kuin taulukon 'Kerroinraja'."""
    sim_kills = np.asarray(sim_kills)
    center = float(np.mean(sim_kills))
    start = math.floor(center - spread) + 0.5
    end = math.floor(center + spread) + 0.5

    rows = []
    line = start
    while line <= end:
        p_over, p_under, odds_over, odds_under = kills_probability_at_line(sim_kills, line)
        rows.append({
            'Linja': line,
            'P(over)': f"{p_over * 100:.1f}%",
            'Kerroinraja (over)': odds_over,
            'P(under)': f"{p_under * 100:.1f}%",
            'Kerroinraja (under)': odds_under,
        })
        line += step
    return rows


# ============================================================================
# EI VIELÄ KORJATTU: vastustajan tason (Strength-of-Schedule) huomiointi
# ============================================================================
# get_team_players_overall_stats laskee KPR:n edelleen riippumatta siitä keitä
# vastaan tapot on tehty. team_elo_calculator.py:n Elo-data (jota
# betting_calculator.py käyttää joukkuetason kertoimiin) olisi luonteva tapa
# painottaa/normalisoida historialliset tapot vastustajan vahvuuden mukaan,
# mutta kyseistä tiedostoa ei ollut ladattujen tiedostojen joukossa, joten
# tätä ei voitu toteuttaa tässä. Jos jaat sen, tämän voi lisätä.