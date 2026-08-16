import sqlite3
import pandas as pd
import numpy as np

def get_player_stats(player_name, map_name):
    """Hakee pelaajan kokonaistapot ja pelatut kierrokset TIETTYYN KARTTAAN."""
    conn = sqlite3.connect('hltv_data.db')

    # Lisätty kartan suodatus (AND LOWER(m.map_name) = LOWER(?))
    query = """
        SELECT p.name,
               SUM(ps.kills) AS total_kills, 
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
    rounds = df['total_rounds'].iloc[0]
    
    # Varmistetaan ettei jaeta nollalla (jos data on jotenkin viallista)
    if rounds == 0:
        return None
        
    kpr = kills / rounds

    return {"name": df['name'].iloc[0], "map": map_name, "kpr": kpr, "rounds_played": rounds}

def simulate_player_kills(player_data, line, num_simulations=10000):
    kpr = player_data['kpr']

    # Arvotaan ottelun pituus
    simulated_match_lengths = np.random.normal(loc=21.5, scale=3.0, size=num_simulations)
    simulated_match_lengths = np.clip(simulated_match_lengths, 13, 36)

    expected_kills_array = simulated_match_lengths * kpr

    simulated_actual_kills = np.random.poisson(expected_kills_array)

    over_hits = np.sum(simulated_actual_kills > line)
    under_hits = num_simulations - over_hits

    prob_over = over_hits / num_simulations
    prob_under = under_hits / num_simulations

    return prob_over, prob_under

if __name__ == "__main__":
    print("\n--- MONTE CARLO - KARTTAKOHTAISET PELAAJAVEDOT ---")
    pelaaja = input("Anna pelaajan nimi (esim. m0NESY): ")
    kartta = input("Anna kartan nimi (esim. Mirage, Dust2, Nuke): ")
    
    print(f"\nHaetaan pelaajan {pelaaja} dataa kartasta {kartta}...")
    data = get_player_stats(pelaaja, kartta)
    
    if not data:
        print(f"Virhe: Pelaajalta '{pelaaja}' ei löytynyt tilastoja kartasta '{kartta}'. Varmista oikeinkirjoitus!")
    else:
        # Varoitus, jos otoskoko on liian pieni
        if data['rounds_played'] < 100:
            print(f"\n[!] VAROITUS: Pelaajalla on vain {data['rounds_played']} pelattua kierrosta tässä kartassa.")
            print("[!] Mallin KPR ei välttämättä ole vielä täysin luotettava.")
            
        raja_str = input("Anna vedonvälittäjän tapporaja (esim. 18.5): ")
        try:
            raja = float(raja_str.replace(',', '.'))
            
            print(f"\nAnalysoidaan: {data['name']} @ {data['map'].capitalize()}")
            print(f"Karttakohtainen KPR: {data['kpr']:.3f} (Perustuu {data['rounds_played']} pelattuun kierrokseen)")
            
            print(f"\nSuoritetaan 10 000 Monte Carlo -simulaatiota...")
            prob_over, prob_under = simulate_player_kills(data, raja)
            
            odds_over = 1 / prob_over if prob_over > 0 else 0
            odds_under = 1 / prob_under if prob_under > 0 else 0
            
            print("\n==================================================")
            print(f" TULOKSET: YLI / ALLE {raja} TAPPOA ({data['map'].capitalize()})")
            print("==================================================")
            print(f"OVER  {raja} : {prob_over*100:>5.1f} % todennäköisyys | Kerroinraja: {odds_over:.2f}")
            print(f"UNDER {raja} : {prob_under*100:>5.1f} % todennäköisyys | Kerroinraja: {odds_under:.2f}")
            print("==================================================")
            
        except ValueError:
            print("Virhe: Syötä raja numerona (esim. 18.5).")