import streamlit as st
import sqlite3
import pandas as pd

from team_elo_calculator import calculate_map_elos, get_expected_score
from players_props import get_player_stats, simulate_player_kills
from players_props import get_player_stats, simulate_player_kills, simulate_player_headshots

# 1. Sivun perusasetukset
st.set_page_config(page_title="CS2 Vetomalli", layout="wide", page_icon="🎯")

# 2. Otsikko ja kuvaus
st.title("CS2 Vedonlyöntimalli")

# 3. Haetaan kaikki tiimit tietokannasta valikkoja varten
conn = sqlite3.connect('hltv_data.db')
teams_df = pd.read_sql_query("SELECT id, name FROM teams ORDER BY name", conn)
conn.close()
team_names_list = teams_df['name'].tolist()

# 4. Luodaan välilehdet
tab1, tab2, tab3 = st.tabs(["Otteluennusteet", "Pelaajavedot", "Pelaajavedot (Headshot)"])

# ==========================================
# VÄLILEHTI 1: JOUKKUEIDEN VOIMASUHTEET
# ==========================================
with tab1:
    st.header("Joukkueiden voimasuhteet")
    
    col1, col2 = st.columns(2)
    with col1:
        team1 = st.selectbox("Valitse Joukkue 1:", team_names_list, index=None, placeholder="Etsi joukkue...")
    with col2:
        team2 = st.selectbox("Valitse Joukkue 2:", team_names_list, index=None, placeholder="Etsi joukkue...")

    if team1 and team2 and team1 != team2:
        st.write("---")
        st.subheader(f"Ennuste: {team1} vs {team2}")
        
        with st.spinner('Lasketaan kartta-Eloja tuhansista otteluista...'):
            elos, names_dict = calculate_map_elos()
            
            t1_id = teams_df.loc[teams_df['name'] == team1, 'id'].values[0]
            t2_id = teams_df.loc[teams_df['name'] == team2, 'id'].values[0]
            
            results = []
            
            for map_name, map_data in elos.items():
                elo1 = map_data.get(t1_id, 1500)
                elo2 = map_data.get(t2_id, 1500)
                
                if elo1 == 1500 and elo2 == 1500:
                    continue
                    
                prob1 = get_expected_score(elo1, elo2)
                prob2 = get_expected_score(elo2, elo1)
                
                odds1 = 1 / prob1 if prob1 > 0 else 0
                odds2 = 1 / prob2 if prob2 > 0 else 0
                
                results.append({
                    "Kartta": map_name.capitalize(),
                    f"Voitto % ({team1})": f"{prob1*100:.1f} %",
                    f"Voitto % ({team2})": f"{prob2*100:.1f} %",
                    f"Kerroinraja ({team1})": round(odds1, 2),
                    f"Kerroinraja ({team2})": round(odds2, 2)
                })
            
            if results:
                df_results = pd.DataFrame(results)
                st.dataframe(df_results, use_container_width=True, hide_index=True)
            else:
                st.warning("Kumpikaan joukkue ei ole pelannut karttoja tietokannan historian aikana.")
                
    elif team1 and team2 and team1 == team2:
        st.warning("Valitse kaksi eri joukkuetta nähdäksesi ennusteen!")
    elif team1 or team2:
        st.info("Valitse vastustaja aloittaaksesi laskennan.")


# ==========================================
# VÄLILEHTI 2: PELAAJAVEDOT (MONTE CARLO)
# ==========================================
with tab2:
    st.header("Pelaajavedot (Tappolinja)")
    
    # 1. Valitaan joukkueet ottelun pituuden arviointia varten
    col1, col2 = st.columns(2)
    with col1:
        pelaajan_tiimi = st.selectbox("Pelaajan joukkue:", team_names_list, index=None, placeholder="Valitse joukkue...")
    with col2:
        vastustaja_tiimi = st.selectbox("Vastustajan joukkue:", team_names_list, index=None, placeholder="Valitse vastustaja...")
        
    # --- UUSI ÄLYKÄS DYYNAAMINEN PELAAJAHAKU ---
    player_list = []
    fallback_kaytossa = False
    
    if pelaajan_tiimi:
        conn = sqlite3.connect('hltv_data.db')
        # Etsitään valitun tiimin ID tietokannasta
        t1_id = int(teams_df.loc[teams_df['name'] == pelaajan_tiimi, 'id'].values[0])
        
        # 1. Yritetään hakea vain tämän tiimin pelaajat
        players_df = pd.read_sql_query("SELECT DISTINCT name FROM players WHERE team_id = ? ORDER BY name", conn, params=(t1_id,))
        
        # 2. Jos scraper ei ole tallentanut tiimilinkkiä (lista on tyhjä), ladataan kaikki pelaajat
        if players_df.empty:
            players_df = pd.read_sql_query("SELECT DISTINCT name FROM players ORDER BY name", conn)
            fallback_kaytossa = True
            
        conn.close()
        player_list = players_df['name'].tolist()

    # 2. Valitaan pelaaja, kartta ja raja
    p_col1, p_col2, p_col3 = st.columns(3)
    with p_col1:
        if not pelaajan_tiimi:
            pelaaja_nimi = st.selectbox("Valitse pelaaja:", ["Valitse ensin joukkue!"], disabled=True)
            pelaaja_nimi = None
        else:
            pelaaja_nimi = st.selectbox("Valitse pelaaja:", player_list)
            # Jos tiimilinkki puuttui tietokannasta, näytetään pieni varoitus
            if fallback_kaytossa:
                st.caption("⚠️ Tietokannasta puuttuu joukkuelinkki. Näytetään kaikki pelaajat.")
            
    with p_col2:
        kartta_nimi = st.selectbox("Kartta:", ["Mirage", "Dust2", "Nuke", "Inferno", "Anubis", "Vertigo", "Ancient"])
    with p_col3:
        tapporaja = st.number_input("Tapporaja (esim. 18.5):", value=18.5, step=0.5)

    # 3. Painike laskennalle
    if st.button("Laske pelaajan todennäköisyydet", type="primary"):
        if pelaaja_nimi and pelaajan_tiimi and vastustaja_tiimi and pelaajan_tiimi != vastustaja_tiimi:
            with st.spinner("Lasketaan ottelun kestoa ja simuloidaan 10 000 skenaariota..."):
                
                elos, names_dict = calculate_map_elos()
                t1_id = teams_df.loc[teams_df['name'] == pelaajan_tiimi, 'id'].values[0]
                t2_id = teams_df.loc[teams_df['name'] == vastustaja_tiimi, 'id'].values[0]
                
                elo1 = elos.get(kartta_nimi, {}).get(t1_id, 1500)
                elo2 = elos.get(kartta_nimi, {}).get(t2_id, 1500)
                
                prob1 = get_expected_score(elo1, elo2)
                prob2 = get_expected_score(elo2, elo1)
                
                max_prob = max(prob1, prob2)
                ennustetut_kierrokset = 22.5 - ((max_prob - 0.5) * 9.0)
                
                data = get_player_stats(pelaaja_nimi, kartta_nimi)
                
                if not data:
                    st.error(f"Pelaajalta '{pelaaja_nimi}' ei löytynyt tilastoja kartasta '{kartta_nimi}'.")
                else:
                    if data['rounds_played'] < 100:
                        st.warning(f"⚠️ Varoitus: Pelaajalla on vain {data['rounds_played']} pelattua kierrosta tässä kartassa. Malli ei ole vielä täysin luotettava.")
                    
                    prob_over, prob_under = simulate_player_kills(data, tapporaja, ennustetut_kierrokset)
                    
                    odds_over = 1 / prob_over if prob_over > 0 else 0
                    odds_under = 1 / prob_under if prob_under > 0 else 0
                    
                    st.write("---")
                    st.subheader(f"Tulokset: {data['name']} @ {data['map'].capitalize()} vs {vastustaja_tiimi}")
                    st.caption(f"Historiallinen KPR: {data['kpr']:.3f} | Mallin ennustama ottelun pituus: **{ennustetut_kierrokset:.1f} kierrosta**")
                    
                    res_col1, res_col2 = st.columns(2)
                    with res_col1:
                        st.metric(label=f"OVER {tapporaja}", value=f"{prob_over*100:.1f} %", delta=f"Kerroinraja: {odds_over:.2f}", delta_color="off")
                    with res_col2:
                        st.metric(label=f"UNDER {tapporaja}", value=f"{prob_under*100:.1f} %", delta=f"Kerroinraja: {odds_under:.2f}", delta_color="off")
                        
        elif pelaajan_tiimi == vastustaja_tiimi:
            st.warning("Valitse kaksi eri joukkuetta!")
        else:
            st.warning("Täytä pelaaja ja molemmat joukkueet.")

# ==========================================
# VÄLILEHTI 3: PELAAJAVEDOT (HEADSHOTIT)
# ==========================================
with tab3:
    st.header("🎯 Pelaajavedot (Headshot-linjat)")
    st.markdown("Arvioi pelaajan pääosumien yli/alle -rajat yhdistämällä vastustajan taso ja Monte Carlo -simulaatio.")
    
    col1, col2 = st.columns(2)
    with col1:
        hs_pelaajan_tiimi = st.selectbox("Pelaajan joukkue (HS):", team_names_list, index=None, placeholder="Valitse joukkue...")
    with col2:
        hs_vastustaja_tiimi = st.selectbox("Vastustajan joukkue (HS):", team_names_list, index=None, placeholder="Valitse vastustaja...")
        
    hs_p_col1, hs_p_col2, hs_p_col3 = st.columns(3)
    with hs_p_col1:
        hs_pelaaja_nimi = st.selectbox("Valitse pelaaja (HS):", player_list, index=None, placeholder="Esim. b1t")
    with hs_p_col2:
        hs_kartta_nimi = st.selectbox("Kartta (HS):", ["Mirage", "Dust2", "Nuke", "Inferno", "Anubis", "Vertigo", "Ancient"])
    with hs_p_col3:
        # Headshot-rajat ovat yleensä paljon matalampia, esim 7.5
        hs_raja = st.number_input("Headshot-raja (esim. 7.5):", value=7.5, step=0.5)

    if st.button("Laske headshot-todennäköisyydet", type="primary"):
        if hs_pelaaja_nimi and hs_pelaajan_tiimi and hs_vastustaja_tiimi and hs_pelaajan_tiimi != hs_vastustaja_tiimi:
            with st.spinner("Lasketaan ottelun kestoa ja simuloidaan 10 000 skenaariota..."):
                
                # Arvioidaan ottelun pituus Elosta
                elos, names_dict = calculate_map_elos()
                t1_id = teams_df.loc[teams_df['name'] == hs_pelaajan_tiimi, 'id'].values[0]
                t2_id = teams_df.loc[teams_df['name'] == hs_vastustaja_tiimi, 'id'].values[0]
                
                elo1 = elos.get(hs_kartta_nimi, {}).get(t1_id, 1500)
                elo2 = elos.get(hs_kartta_nimi, {}).get(t2_id, 1500)
                
                prob1 = get_expected_score(elo1, elo2)
                prob2 = get_expected_score(elo2, elo1)
                
                max_prob = max(prob1, prob2)
                ennustetut_kierrokset = 22.5 - ((max_prob - 0.5) * 9.0)
                
                # Haetaan statsit (Nyt sisältää myös HPR!)
                data = get_player_stats(hs_pelaaja_nimi, hs_kartta_nimi)
                
                if not data:
                    st.error(f"Pelaajalta '{hs_pelaaja_nimi}' ei löytynyt tilastoja kartasta '{hs_kartta_nimi}'.")
                else:
                    if data['rounds_played'] < 100:
                        st.warning(f"⚠️ Varoitus: Pelaajalla on vain {data['rounds_played']} pelattua kierrosta tässä kartassa.")
                    
                    # Simuloidaan HEADSHOTIT (ei normi tappoja)
                    prob_over, prob_under = simulate_player_headshots(data, hs_raja, ennustetut_kierrokset)
                    
                    odds_over = 1 / prob_over if prob_over > 0 else 0
                    odds_under = 1 / prob_under if prob_under > 0 else 0
                    
                    st.write("---")
                    st.subheader(f"🎯 Tulokset: {data['name']} @ {data['map'].capitalize()} vs {hs_vastustaja_tiimi}")
                    st.caption(f"Historiallinen HS%: **{data['hs_percent']:.1f}%** | HPR: {data['hpr']:.3f} | Ennustettu kesto: **{ennustetut_kierrokset:.1f} kierrosta**")
                    
                    res_col1, res_col2 = st.columns(2)
                    with res_col1:
                        st.metric(label=f"OVER {hs_raja} HS", value=f"{prob_over*100:.1f} %", delta=f"Kerroinraja: {odds_over:.2f}", delta_color="off")
                    with res_col2:
                        st.metric(label=f"UNDER {hs_raja} HS", value=f"{prob_under*100:.1f} %", delta=f"Kerroinraja: {odds_under:.2f}", delta_color="off")
                        
        elif hs_pelaajan_tiimi == hs_vastustaja_tiimi:
            st.warning("Valitse kaksi eri joukkuetta!")
        else:
            st.warning("Täytä pelaaja ja molemmat joukkueet.")