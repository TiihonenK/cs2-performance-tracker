import pandas as pd
import numpy as np
import sqlite3
from datetime import datetime
import math

# ============================================================================
# KORJAUS 3/4: kierrosmäärän arvonta bootstrapilla oikeasta datasta
# ============================================================================
# CS2:n MR12-formaatissa jatkoaika alkaa vasta 28. kierroksesta 6 kierroksen
# jaksoissa -> todellinen kierrosmääräjakauma on kaksihuippuinen (25-27 ja
# 31-33 kierrosta eivät esiinny KOSKAAN). Aiempi malli arpoi kierrosmäärän
# Normal(expected_rounds, scale=2.5) -jakaumasta, joka sekä levittää massaa
# mahdottomille väleille että aliarvioi todellisen hajonnan (oikea keskihajonta
# on n. 5.0 kierrosta, ei 2.5). Korjattu versio arpoo suoraan historiallisesta
# jakaumasta (bootstrap), jaettuna kolmeen "koriin" ottelun tasaisuuden mukaan,
# jotta suositun joukkueen ottelu ei arvo yhtä usein pitkää jatkoaikaottelua
# kuin täysin tasaväkinen ottelu.

_ROUND_DEVIATION_CACHE = None
_OVERALL_MEAN_ROUNDS = None


def _load_round_length_deviations():
    """Lataa ja välimuistittaa jokaisen historiallisen kartan poikkeaman
    (total_rounds - koko datan keskiarvo) sekä itse keskiarvon.

    HUOM (korjattu versio): aiempi versio jakoi kartat kolmeen koriin
    LOPPUTULOKSEN pistemarginaalin perusteella ja valitsi korin ENNAKKO-
    todennäköisyyden perusteella. Nämä eivät ole sama asia - 50/50-ennakko-
    ottelu EI tarkoita että lopputulos on todennäköisesti tasainen, se voi
    yhtä hyvin olla selvä voitto. Koska "tasainen lopputulos" -kori sisälsi
    paljon jatkoaikaotteluita, sen keskiarvo (26.4 kierrosta) oli 23 %
    korkeampi kuin datan oikea 21.5 kierroksen keskiarvo, mikä ylihinnoitteli
    tappoprojektiot systemaattisesti kaikissa suht. tasaisissa otteluissa.

    Tämä versio keskittää arvonnan oikein kalibroituun tavoitekeskiarvoon
    (ks. sample_match_lengths) ja lisää siihen bootstrapatun POIKKEAMAN koko
    datan jakaumasta - 50/50-ottelu palautuu takaisin oikeaan 21.5 kierroksen
    keskiarvoon, mutta jatkoajan aiheuttama epäjatkuva/kaksihuippuinen muoto
    säilyy (Normal-jakauman sijaan)."""
    global _ROUND_DEVIATION_CACHE, _OVERALL_MEAN_ROUNDS
    if _ROUND_DEVIATION_CACHE is not None:
        return _ROUND_DEVIATION_CACHE, _OVERALL_MEAN_ROUNDS

    conn = sqlite3.connect('hltv_data.db')
    df = pd.read_sql_query("SELECT score_team1, score_team2 FROM matches", conn)
    conn.close()

    df['total_rounds'] = df['score_team1'] + df['score_team2']
    df = df[df['total_rounds'] >= 13]  # siivotaan mahdolliset virheelliset/kesken jääneet rivit

    mean_rounds = float(df['total_rounds'].mean())
    deviations = (df['total_rounds'] - mean_rounds).to_numpy(dtype=float)

    _ROUND_DEVIATION_CACHE = deviations
    _OVERALL_MEAN_ROUNDS = mean_rounds
    return deviations, mean_rounds


def sample_match_lengths(win_prob, num_simulations=10000, skew_coefficient=8.5):
    """Arpoo kierrosmäärätaulukon: tavoitekeskiarvo lasketaan ennakko-
    todennäköisyyden perusteella (21.5 - skew * kerroin, sama periaate kuin
    alkuperäisessä koodissa), ja siihen lisätään bootstrapattu poikkeama
    oikeasta historiallisesta jakaumasta Normal-jakauman sijaan.

    win_prob: suositumman joukkueen voittotodennäköisyys (esim. 0.75).
    skew_coefficient: kuinka paljon ennakkotodennäköisyyden vinous lyhentää
    odotettua kierrosmäärää. Ei ole kalibroitu oikeaa historiallista
    ennakkokerroindataa vastaan (sitä ei ole kannassa) - jos alat tallentaa
    Pinnaclen kertoimet jokaiseen otteluun, tämän voi kalibroida regressiolla.

    HUOM: sama palautettu taulukko kannattaa antaa MOLEMPIEN joukkueiden
    simulate_team_kills-kutsuille, jotta obe joukkueen pelaajat "pelaavat"
    saman simuloidun ottelun pituuden kussakin simulaatiokierroksessa.
    """
    deviations, mean_rounds = _load_round_length_deviations()
    skew = abs(win_prob - 0.5) * 2  # 0 = täysin tasan, 1 = äärimmäinen favoriitti

    target_mean = mean_rounds - skew * skew_coefficient
    sampled_dev = np.random.choice(deviations, size=num_simulations, replace=True)
    sim_lengths = target_mean + sampled_dev

    return np.clip(sim_lengths, 13, None)


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
    df['weight'] = 1.0 + np.maximum(0, (90 - df['days_ago']) / 90) * 0.5

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


def simulate_team_kills(players_data, win_prob, sim_lengths, overdispersion=1.25, form_weight=0.3):
    """Simuloi koko joukkueen kerralla ja palauttaa datan taulukkoa varten.

    sim_lengths: KORJAUS 4 - valmiiksi arvottu kierrosmäärätaulukko
    (sample_match_lengths-funktiosta), EI enää Normal-jakauma. Sama taulukko
    tulisi antaa molemmille joukkueille samasta ottelusta.

    overdispersion: KORJAUS 3 - Poissonin sijaan käytetään negatiivista
    binomijakaumaa. Oikeasta datasta mitattuna (kontrolloitu kierrosmäärän
    suhteen) tappomäärien varianssi on n. 1.2-1.3x suurempi kuin Poisson
    ennustaisi - puhdas Poisson tekee simuloiduista P.over/P.under-luvuista
    liian itsevarmoja. 1.25 on järkevä oletus, säädettävissä.

    form_weight: KORJAUS - viimeisen 30 päivän muoto (recent_kpr) sekoitetaan
    nyt oikeasti simulaatioon, ei ole enää pelkkä kosmeettinen sarake.
    """
    eff_multiplier = 1.0 + (win_prob - 0.5) * 0.50
    sim_lengths = np.asarray(sim_lengths, dtype=float)
    n_sims = len(sim_lengths)

    results = []
    for p in players_data:
        base_kpr = p['kpr']
        if p.get('recent_kpr') is not None:
            base_kpr = (1 - form_weight) * base_kpr + form_weight * p['recent_kpr']

        eff_kpr = max(base_kpr, 0.001) * eff_multiplier
        expected_k = sim_lengths * eff_kpr

        # Negatiivinen binomijakauma: var = overdispersion * mean (vakio p-parametri)
        p_param = 1.0 / overdispersion
        r_param = expected_k / (overdispersion - 1)
        sim_kills = np.random.negative_binomial(r_param, p_param)

        proj_k = np.mean(sim_kills)
        std_k = np.std(sim_kills)

        # Mallin oma "reilu" linja - HUOM: tämä EI ole bookkerin tarjoama linja,
        # ks. kills_probability_at_line() arvioidaksesi todellista bookkerin lukua.
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
            'Line': line,
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