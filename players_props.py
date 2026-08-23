import pandas as pd
import numpy as np
import sqlite3
from datetime import datetime
import math

def get_team_players_overall_stats(team_name):
    """Hakee tilastot aktiiviselle rosterille ja painottaa KPR-laskennassa tuoreimpia otteluita."""
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
    
    # 2. Haetaan kaikkien pelattujen karttojen tappomäärät ja PÄIVÄMÄÄRÄT aktiivisille pelaajille
    query = """
    SELECT p.name, s.kills, m.match_date
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
    
    # 3. LASKETAAN AIKAPAINOTETTU KPR (Recent form)
    # Muutetaan päivämäärät Pandas-aikamuotoon
    df['match_date'] = pd.to_datetime(df['match_date'], errors='coerce')
    
    # Lasketaan kuinka monta päivää ottelusta on kulunut
    now = pd.to_datetime('today')
    df['days_ago'] = (now - df['match_date']).dt.days.fillna(90)
    
    # Luodaan painoarvo: Peruspaino 1.0, johon lisätään max 0.5 mitä tuoreempi peli (0-90 päivää)
    df['weight'] = 1.0 + np.maximum(0, (90 - df['days_ago']) / 90) * 0.5
    
    players = []
    # Ryhmitellään data pelaajakohtaisesti ja lasketaan painotetut tulokset
    for name, group in df.groupby('name'):
        weighted_kills = (group['kills'] * group['weight']).sum()
        weighted_maps = group['weight'].sum()
        actual_maps = len(group)
        
        n_rounds = int(actual_maps * 21.5)
        kpr = weighted_kills / (weighted_maps * 21.5) if weighted_maps > 0 else 0
        
        # --- UUSI: FORMIN LASKENTA ---
        # Lasketaan pitkän aikavälin (raaka) KPR
        overall_kpr = group['kills'].sum() / n_rounds if n_rounds > 0 else 0
        
        # Eristetään viimeisen 30 päivän pelit
        recent_group = group[group['days_ago'] <= 30]
        recent_maps = len(recent_group)
        
        # Määritetään formi, jos pelejä on vähintään 3 viimeisen kuukauden ajalta
        if recent_maps >= 3:
            recent_kpr = recent_group['kills'].sum() / (recent_maps * 21.5)
            form_diff = recent_kpr - overall_kpr
            
            if form_diff >= 0.02:
                form_str = f"🔥 +{form_diff:.2f}"
            elif form_diff <= -0.02:
                form_str = f"🧊 {form_diff:.2f}"
            else:
                form_str = f"➖ {form_diff:+.2f}"
        else:
            form_str = "N/A (Liian vähän pelejä)"
            
        players.append({'name': name, 'kpr': kpr, 'n': n_rounds, 'form': form_str})
        
    return sorted(players, key=lambda x: x['kpr'], reverse=True)

def simulate_team_kills(players_data, win_prob, expected_rounds, num_simulations=10000):
    """Simuloi koko joukkueen kerralla ja palauttaa datan taulukkoa varten."""
    
    # Voittajajoukkue saa pienen KPR-buustin (max +10%), altavastaaja miinusta
    eff_multiplier = 1.0 + (win_prob - 0.5) * 0.20
    
    results = []
    for p in players_data:
        eff_kpr = p['kpr'] * eff_multiplier
        
        sim_lengths = np.random.normal(loc=expected_rounds, scale=2.5, size=num_simulations)
        sim_lengths = np.clip(sim_lengths, 13, 36)
        
        expected_k = sim_lengths * eff_kpr
        sim_kills = np.random.poisson(expected_k)
        
        proj_k = np.mean(sim_kills)
        std_k = np.std(sim_kills)
        
        # Luodaan automaattinen linja (aina .5 päätteinen)
        base_line = int(math.floor(proj_k))
        line = base_line + 0.5
        
        p_over = np.sum(sim_kills > line) / num_simulations
        p_under = np.sum(sim_kills < line) / num_simulations
        
        results.append({
            'Pelaaja': p['name'],
            'N': p['n'],
            'KPR': round(p['kpr'], 3),
            'Proj.K': round(proj_k, 2),
            'stdK': round(std_k, 2),
            'Line': line,
            'P.over': f"{p_over*100:.1f}%",
            'P.under': f"{p_under*100:.1f}%",
            '_p_over_raw': p_over,
            '_p_under_raw': p_under,
            'Form (30d)': p['form']
        })
        
    # Järjestetään KPR:n mukaan laskevasti
    return sorted(results, key=lambda x: x['KPR'], reverse=True)