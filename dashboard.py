import streamlit as st
import sqlite3
import pandas as pd
from team_elo_calculator import calculate_map_elos, get_expected_score

# 1. Sivun perusasetukset (tämä pitää aina olla ensimmäisenä)
st.set_page_config(page_title="CS2 Vetomalli", layout="wide")

# 2. Otsikko ja kuvaus
st.title("CS2 Vedonlyöntimalli")

# 3. Haetaan kaikki tiimit tietokannasta valikkoja varten
conn = sqlite3.connect('hltv_data.db')
teams_df = pd.read_sql_query("SELECT id, name FROM teams ORDER BY name", conn)
conn.close()
team_names_list = teams_df['name'].tolist()

# 4. Luodaan välilehdet
tab1, tab2 = st.tabs(["Otteluvedot", "Pelaajavedot"])

with tab1:
    st.header("Valitse joukkueet")
    
    # Jaetaan näyttö kahteen sarakkeeseen
    col1, col2 = st.columns(2)
    
    with col1:
        # Ensimmäinen alasvetovalikko (oletuksena 1. listalla oleva tiimi)
        team1 = st.selectbox("Valitse Joukkue 1:", team_names_list, index=None, placeholder="Valitse joukkue")
        
    with col2:
        # Toinen alasvetovalikko (oletuksena 2. listalla oleva tiimi)
        team2 = st.selectbox("Valitse Joukkue 2:", team_names_list, index=None, placeholder="Valitse joukkue")

    # 5. Laskentalogiikka, joka aktivoituu kun joukkueet on valittu
    if team1 and team2 and team1 != team2:
        st.write("---")
        st.subheader(f"Ennuste: {team1} vs {team2}")
        
        with st.spinner('Lasketaan kartta-Eloja tuhansista otteluista...'):
            # Tuodaan laskurifunktiot aiemmasta tiedostostasi
            elos, names_dict = calculate_map_elos()
            
            # Etsitään ID:t pandasin avulla
            t1_id = teams_df.loc[teams_df['name'] == team1, 'id'].values[0]
            t2_id = teams_df.loc[teams_df['name'] == team2, 'id'].values[0]
            
            results = []
            
            for map_name, map_data in elos.items():
                elo1 = map_data.get(t1_id, 1500)
                elo2 = map_data.get(t2_id, 1500)
                
                # Jos kumpikaan ei ole pelannut karttaa, skipataan
                if elo1 == 1500 and elo2 == 1500:
                    continue
                    
                prob1 = get_expected_score(elo1, elo2)
                prob2 = get_expected_score(elo2, elo1)
                
                odds1 = 1 / prob1 if prob1 > 0 else 0
                odds2 = 1 / prob2 if prob2 > 0 else 0
                
                # Lisätään rivi taulukkoon
                results.append({
                    "Kartta": map_name.capitalize(),
                    f"Voitto % ({team1})": f"{prob1*100:.1f} %",
                    f"Voitto % ({team2})": f"{prob2*100:.1f} %",
                    f"Kerroinraja ({team1})": round(odds1, 2),
                    f"Kerroinraja ({team2})": round(odds2, 2)
                })
            
            # Jos tuloksia löytyi, piirretään upea Streamlit-taulukko
            if results:
                df_results = pd.DataFrame(results)
                st.dataframe(df_results, use_container_width=True, hide_index=True)
            else:
                st.warning("Kumpikaan joukkue ei ole pelannut karttoja tietokannan historian aikana.")
                
    elif team1 == team2:
        st.warning("Valitse kaksi eri joukkuetta nähdäksesi ennusteen!")

with tab2:
    st.header("Pelaajavedot")
    st.markdown("Arvioi pelaajan tappojen yli/alle -rajat yhdistämällä vastustajan taso ja Monte Carlo -simulaatio.")
    
    # 1. Valitaan joukkueet
    col1, col2 = st.columns(2)
    with col1:
        pelaajan_tiimi = st.selectbox("Pelaajan joukkue:", team_names_list, index=None, placeholder="Valitse joukkue...")
    with col2:
        vastustaja_tiimi = st.selectbox("Vastustajan joukkue:", team_names_list, index=None, placeholder="Valitse vastustaja...")
        
    # 2. Valitaan pelaaja ja linja
    p_col1, p_col2, p_col3 = st.columns(3)
    with p_col1:
        pelaaja_nimi = st.text_input("Pelaajan nimi (esim. m0NESY):")
    with p_col2:
        kartta_nimi = st.selectbox("Kartta:", ["Mirage", "Dust2", "Nuke", "Inferno", "Anubis", "Vertigo", "Ancient"])
    with p_col3:
        tapporaja = st.number_input("Tapporaja (esim. 18.5):", value=18.5, step=0.5)

    # 3. Painike laskennalle
    if st.button("Laske pelaajan todennäköisyydet", type="primary"):
        if pelaaja_nimi and pelaajan_tiimi and vastustaja_tiimi and pelaajan_tiimi != vastustaja_tiimi:
            with st.spinner("Lasketaan ottelun kestoa ja simuloidaan 10 000 skenaariota..."):
                
                # A. Haetaan tiimien elot ja ennustetaan ottelun pituus
                elos, names_dict = calculate_map_elos()
                t1_id = teams_df.loc[teams_df['name'] == pelaajan_tiimi, 'id'].values[0]
                t2_id = teams_df.loc[teams_df['name'] == vastustaja_tiimi, 'id'].values[0]
                
                elo1 = elos.get(kartta_nimi, {}).get(t1_id, 1500)
                elo2 = elos.get(kartta_nimi, {}).get(t2_id, 1500)
                
                prob1 = get_expected_score(elo1, elo2)
                prob2 = get_expected_score(elo2, elo1)
                
                # ÄLYKÄS LOGIIKKA: Jos peli on 50-50, oletetaan n. 22.5 kierrosta. 
                # Jos peli on esim. 90-10 murskajaiset, kierrosmäärä tippuu rajusti (n. 18.5).
                max_prob = max(prob1, prob2)
                ennustetut_kierrokset = 22.5 - ((max_prob - 0.5) * 9.0)
                
                # B. Haetaan pelaajan karttakohtainen KPR
                data = get_player_stats(pelaaja_nimi, kartta_nimi)
                
                if not data:
                    st.error(f"Pelaajalta '{pelaaja_nimi}' ei löytynyt tilastoja kartasta '{kartta_nimi}'.")
                else:
                    if data['rounds_played'] < 100:
                        st.warning(f"⚠️ Varoitus: Pelaajalla on vain {data['rounds_played']} pelattua kierrosta tässä kartassa.")
                    
                    # C. Ajetaan simulaatio ENNUSTETULLA kierrosmäärällä
                    prob_over, prob_under = simulate_player_kills(data, tapporaja, ennustetut_kierrokset)
                    
                    odds_over = 1 / prob_over if prob_over > 0 else 0
                    odds_under = 1 / prob_under if prob_under > 0 else 0
                    
                    st.write("---")
                    st.subheader(f"Tulokset: {data['name']} @ {data['map'].capitalize()} vs {vastustaja_tiimi}")
                    st.caption(f"Historiallinen KPR: {data['kpr']:.3f} | Mallin ennustama ottelun pituus: **{ennustetut_kierrokset:.1f} kierrosta**")
                    
                    # D. Tulostetaan visuaalisesti
                    res_col1, res_col2 = st.columns(2)
                    with res_col1:
                        st.metric(label=f"OVER {tapporaja}", value=f"{prob_over*100:.1f} %", delta=f"Kerroinraja: {odds_over:.2f}", delta_color="off")
                    with res_col2:
                        st.metric(label=f"UNDER {tapporaja}", value=f"{prob_under*100:.1f} %", delta=f"Kerroinraja: {odds_under:.2f}", delta_color="off")
                        
        elif pelaajan_tiimi == vastustaja_tiimi:
            st.warning("Valitse kaksi eri joukkuetta!")
        else:
            st.warning("Täytä pelaaja ja molemmat joukkueet.")