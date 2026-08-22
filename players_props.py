import sqlite3
import pandas as pd
import numpy as np

def get_player_stats(player_name, map_name):
    """Hakee pelaajan kokonaistapot, headshotit ja pelatut kierrokset tiettyyn karttaan."""
    conn = sqlite3.connect('hltv_data.db')

    # Lisätty headshots haku
    query = """
        SELECT p.name,
               SUM(ps.kills) AS total_kills, 
               SUM(ps.headshots) AS total_headshots,
               SUM(m.score_team1 + m.score_team2) AS total_rounds
        FROM player_stats ps
        JOIN matches m ON ps.match_id = m.id
        JOIN players p ON ps.player_id = p.id
        WHERE LOWER(p.name) = LOWER(?) AND LOWER(m.map_name) = LOWER(?)
        GROUP BY p.name
    """

    df = pd.read_sql_query(query, conn, params=(player_name, map_name))
    conn.close()

    if df.empty:
        return None

    kills = df['total_kills'].iloc[0]
    headshots = df['total_headshots'].iloc[0]
    rounds = df['total_rounds'].iloc[0]
    
    if rounds == 0:
        return None
        
    kpr = kills / rounds
    hpr = headshots / rounds  # UUSI: Headshots per round
    hs_percent = (headshots / kills) * 100 if kills > 0 else 0 # HS %

    return {
        "name": df['name'].iloc[0], 
        "map": map_name, 
        "kpr": kpr, 
        "hpr": hpr, 
        "hs_percent": hs_percent,
        "rounds_played": rounds
    }

def simulate_player_kills(player_data, line, expected_rounds=21.5, num_simulations=10000):
    kpr = player_data['kpr']
    simulated_match_lengths = np.random.normal(loc=expected_rounds, scale=2.8, size=num_simulations)
    simulated_match_lengths = np.clip(simulated_match_lengths, 13, 36)

    expected_kills_array = simulated_match_lengths * kpr
    simulated_actual_kills = np.random.poisson(expected_kills_array)

    over_hits = np.sum(simulated_actual_kills > line)
    under_hits = num_simulations - over_hits

    return over_hits / num_simulations, under_hits / num_simulations

# --- UUSI FUNKTIO HEADSHOTTIEN SIMULOINTIIN ---
def simulate_player_headshots(player_data, line, expected_rounds=21.5, num_simulations=10000):
    hpr = player_data['hpr'] # Käytetään Headshots per Round -arvoa
    
    # Arvotaan ottelun pituus aivan kuten tapposimulaatiossa
    simulated_match_lengths = np.random.normal(loc=expected_rounds, scale=2.8, size=num_simulations)
    simulated_match_lengths = np.clip(simulated_match_lengths, 13, 36)

    # Lasketaan odotusarvo headshoteille (pituus * HPR)
    expected_hs_array = simulated_match_lengths * hpr
    
    # Simuloidaan todelliset headshotit Poisson-jakaumalla
    simulated_actual_hs = np.random.poisson(expected_hs_array)

    over_hits = np.sum(simulated_actual_hs > line)
    under_hits = num_simulations - over_hits

    return over_hits / num_simulations, under_hits / num_simulations