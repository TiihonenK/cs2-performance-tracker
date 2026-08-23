import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime

from team_elo_calculator import calculate_map_elos, get_expected_score
from players_props import get_player_stats, simulate_player_kills, simulate_player_headshots

# --- UUSI: Tietokannan alustus vetoseurannalle ---
def init_betting_db():
    conn = sqlite3.connect('hltv_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS bets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  date TEXT, 
                  type TEXT, 
                  description TEXT, 
                  stake REAL, 
                  odds REAL, 
                  status TEXT)''')
    conn.commit()
    conn.close()

init_betting_db()

def save_bet(bet_type, description, stake, odds):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect('hltv_data.db')
    c = conn.cursor()
    c.execute("INSERT INTO bets (date, type, description, stake, odds, status) VALUES (?, ?, ?, ?, ?, ?)",
              (date_str, bet_type, description, stake, odds, "Odottaa"))
    conn.commit()
    conn.close()
    st.success("✅ Veto tallennettu onnistuneesti! Voit tarkastella sitä Vetoseuranta-välilehdellä.")

# 1. Sivun perusasetukset
st.set_page_config(page_title="CS2 Vetomalli", layout="wide", page_icon="🎯")

# 2. Otsikko ja kuvaus
st.title("CS2 Vedonlyöntimalli & Seuranta")

# 3. Haetaan kaikki tiimit tietokannasta valikkoja varten
conn = sqlite3.connect('hltv_data.db')
teams_df = pd.read_sql_query("SELECT id, name FROM teams ORDER BY name", conn)
conn.close()
team_names_list = teams_df['name'].tolist()

# 4. Luodaan NELJÄ välilehteä
tab1, tab2, tab3, tab4 = st.tabs(["⚔️ Otteluennusteet", "🔫 Pelaajavedot", "🎯 Pelaajavedot (Headshot)", "📈 Vetoseuranta"])

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
                
                # --- VEDON TALLENNUS (TAB 1) ---
                with st.expander("➕ Kirjaa veto tästä ottelusta", expanded=False):
                    with st.form("bet_form_match"):
                        b_desc = st.text_input("Vedon kuvaus", value=f"{team1} voittaa kohteessa {team2}")
                        c1, c2 = st.columns(2)
                        b_stake = c1.number_input("Panos (€)", min_value=1.0, value=10.0, step=1.0)
                        b_odds = c2.number_input("Kerroin", min_value=1.01, value=1.85, step=0.01)
                        if st.form_submit_button("Tallenna veto"):
                            save_bet("Ottelu", b_desc, b_stake, b_odds)
            else:
                st.warning("Kumpikaan joukkue ei ole pelannut karttoja tietokannan historian aikana.")
                
    elif team1 and team2 and team1 == team2:
        st.warning("Valitse kaksi eri joukkuetta nähdäksesi ennusteen!")
    elif team1 or team2:
        st.info("Valitse vastustaja aloittaaksesi laskennan.")


# ==========================================
# VÄLILEHTI 2: PELAAJAVEDOT (MONTE CARLO)
# ==========================================
# ==========================================
# VÄLILEHTI 2: PELAAJAVEDOT (MONTE CARLO)
# ==========================================
with tab2:
    st.header("Pelaajavedot (Tappolinja)")
    
    col1, col2 = st.columns(2)
    with col1:
        pelaajan_tiimi = st.selectbox("Pelaajan joukkue:", team_names_list, index=None, placeholder="Valitse joukkue...", key="t2_t1")
    with col2:
        vastustaja_tiimi = st.selectbox("Vastustajan joukkue:", team_names_list, index=None, placeholder="Valitse vastustaja...", key="t2_t2")
        
    player_list = []
    if pelaajan_tiimi:
        conn = sqlite3.connect('hltv_data.db')
        t1_id = int(teams_df.loc[teams_df['name'] == pelaajan_tiimi, 'id'].values[0])
        players_df = pd.read_sql_query("SELECT DISTINCT name FROM players WHERE team_id = ? ORDER BY name", conn, params=(t1_id,))
        if players_df.empty:
            players_df = pd.read_sql_query("SELECT DISTINCT name FROM players ORDER BY name", conn)
        conn.close()
        player_list = players_df['name'].tolist()

    p_col1, p_col2, p_col3 = st.columns(3)
    with p_col1:
        if not pelaajan_tiimi:
            pelaaja_nimi = st.selectbox("Valitse pelaaja:", ["Valitse ensin joukkue!"], disabled=True, key="t2_p_dis")
            pelaaja_nimi = None
        else:
            pelaaja_nimi = st.selectbox("Valitse pelaaja:", player_list, key="t2_p")
            
    with p_col2:
        kartta_nimi = st.selectbox("Kartta:", ["Mirage", "Dust2", "Nuke", "Inferno", "Anubis", "Vertigo", "Ancient"], key="t2_m")
    with p_col3:
        tapporaja = st.number_input("Tapporaja (esim. 18.5):", value=18.5, step=0.5, key="t2_r")

    # 1. Laske-painike tallentaa tulokset muistiin
    if st.button("Laske pelaajan todennäköisyydet", type="primary"):
        if pelaaja_nimi and pelaajan_tiimi and vastustaja_tiimi and pelaajan_tiimi != vastustaja_tiimi:
            with st.spinner("Lasketaan ottelun kestoa ja simuloidaan 10 000 skenaariota..."):
                elos, names_dict = calculate_map_elos()
                t1_id = teams_df.loc[teams_df['name'] == pelaajan_tiimi, 'id'].values[0]
                t2_id = teams_df.loc[teams_df['name'] == vastustaja_tiimi, 'id'].values[0]
                
                prob1 = get_expected_score(elos.get(kartta_nimi, {}).get(t1_id, 1500), elos.get(kartta_nimi, {}).get(t2_id, 1500))
                prob2 = get_expected_score(elos.get(kartta_nimi, {}).get(t2_id, 1500), elos.get(kartta_nimi, {}).get(t1_id, 1500))
                
                ennustetut_kierrokset = 22.5 - ((max(prob1, prob2) - 0.5) * 9.0)
                data = get_player_stats(pelaaja_nimi, kartta_nimi)
                
                if not data:
                    st.error(f"Tilastoja ei löytynyt.")
                else:
                    prob_over, prob_under = simulate_player_kills(data, tapporaja, ennustetut_kierrokset)
                    # TALLENNUS SESSION STATEEN
                    st.session_state['t2_data'] = {
                        'nimi': data['name'], 'kartta': data['map'], 'kpr': data['kpr'],
                        'rounds': ennustetut_kierrokset, 'over': prob_over, 'under': prob_under,
                        'raja': tapporaja, 'vastustaja': vastustaja_tiimi
                    }

    # 2. Näytetään tulokset muistista (Irrotettu napista!)
    # 2. Näytetään tulokset muistista (Tappolinjat)
    if 't2_data' in st.session_state:
        d = st.session_state['t2_data']
        odds_over = 1 / d['over'] if d['over'] > 0 else 0
        odds_under = 1 / d['under'] if d['under'] > 0 else 0
        
        st.write("---")
        st.subheader(f"Tulokset: {d['nimi']} @ {d['kartta'].capitalize()} vs {d['vastustaja']}")
        st.caption(f"Historiallinen KPR: {d['kpr']:.3f} | Ennustettu pituus: **{d['rounds']:.1f} kierrosta**")
        
        res_col1, res_col2 = st.columns(2)
        with res_col1:
            st.metric(label=f"OVER {d['raja']}", value=f"{d['over']*100:.1f} %", delta=f"Kerroinraja: {odds_over:.2f}", delta_color="off")
        with res_col2:
            st.metric(label=f"UNDER {d['raja']}", value=f"{d['under']*100:.1f} %", delta=f"Kerroinraja: {odds_under:.2f}", delta_color="off")

        # --- UUSI: EV-LASKURI ---
        st.write("📊 **EV-Laskuri (Etsi ylikertoimet)**")
        ev_col1, ev_col2 = st.columns(2)
        with ev_col1:
            bookie_over = st.number_input("Syötä bookkerin OVER-kerroin:", min_value=1.0, value=1.0, step=0.01, key="ev_over_t2")
            if bookie_over > 1.0:
                ev_over = (d['over'] * bookie_over - 1) * 100
                color = "green" if ev_over > 0 else "red"
                st.markdown(f"Odotusarvo: <strong style='color:{color}'>{ev_over:+.1f} %</strong>", unsafe_allow_html=True)
                
        with ev_col2:
            bookie_under = st.number_input("Syötä bookkerin UNDER-kerroin:", min_value=1.0, value=1.0, step=0.01, key="ev_under_t2")
            if bookie_under > 1.0:
                ev_under = (d['under'] * bookie_under - 1) * 100
                color = "green" if ev_under > 0 else "red"
                st.markdown(f"Odotusarvo: <strong style='color:{color}'>{ev_under:+.1f} %</strong>", unsafe_allow_html=True)
        st.write("---")

        with st.expander("➕ Kirjaa veto tästä kohteesta", expanded=True):
            # Valintanappi lomakkeen ulkopuolelle, jotta teksti päivittyy dynaamisesti
            suunta = st.radio("Vedon suunta:", ["OVER", "UNDER"], horizontal=True, key="suunta_t2")
            
            with st.form("bet_form_kills"):
                b_desc = st.text_input("Vedon kuvaus", value=f"{d['nimi']} {suunta} {d['raja']}, {d['kartta'].capitalize()}, {d['vastustaja']}")
                
                c1, c2 = st.columns(2)
                b_stake = c1.number_input("Panos (€)", min_value=1.0, value=10.0, step=1.0)
                
                # Valitaan oletuskerroin automaattisesti suunnan mukaan!
                if suunta == "OVER":
                    default_odds = bookie_over if bookie_over > 1.0 else round(odds_over, 2)
                else:
                    default_odds = bookie_under if bookie_under > 1.0 else round(odds_under, 2)
                    
                b_odds = c2.number_input("Kerroin", min_value=1.01, value=float(default_odds), step=0.01)
                
                if st.form_submit_button("Tallenna veto"):
                    save_bet("Tapot", b_desc, b_stake, b_odds)
                    del st.session_state['t2_data']
                    st.rerun()

# ==========================================
# VÄLILEHTI 3: PELAAJAVEDOT (HEADSHOTIT)
# ==========================================
with tab3:
    st.header("🎯 Pelaajavedot (Headshot-linjat)")
    st.markdown("Arvioi pelaajan pääosumien yli/alle -rajat yhdistämällä vastustajan taso ja Monte Carlo -simulaatio.")
    
    col1, col2 = st.columns(2)
    with col1:
        hs_pelaajan_tiimi = st.selectbox("Pelaajan joukkue (HS):", team_names_list, index=None, placeholder="Valitse joukkue...", key="t3_t1")
    with col2:
        hs_vastustaja_tiimi = st.selectbox("Vastustajan joukkue (HS):", team_names_list, index=None, placeholder="Valitse vastustaja...", key="t3_t2")
        
    hs_player_list = []
    if hs_pelaajan_tiimi:
        conn = sqlite3.connect('hltv_data.db')
        t1_id_hs = int(teams_df.loc[teams_df['name'] == hs_pelaajan_tiimi, 'id'].values[0])
        players_df_hs = pd.read_sql_query("SELECT DISTINCT name FROM players WHERE team_id = ? ORDER BY name", conn, params=(t1_id_hs,))
        if players_df_hs.empty:
            players_df_hs = pd.read_sql_query("SELECT DISTINCT name FROM players ORDER BY name", conn)
        conn.close()
        hs_player_list = players_df_hs['name'].tolist()

    hs_p_col1, hs_p_col2, hs_p_col3 = st.columns(3)
    with hs_p_col1:
        if not hs_pelaajan_tiimi:
            hs_pelaaja_nimi = st.selectbox("Valitse pelaaja (HS):", ["Valitse ensin joukkue!"], disabled=True, key="t3_p_dis")
            hs_pelaaja_nimi = None
        else:
            hs_pelaaja_nimi = st.selectbox("Valitse pelaaja (HS):", hs_player_list, key="t3_p")
            
    with hs_p_col2:
        hs_kartta_nimi = st.selectbox("Kartta (HS):", ["Mirage", "Dust2", "Nuke", "Inferno", "Anubis", "Vertigo", "Ancient"], key="t3_m")
    with hs_p_col3:
        hs_raja = st.number_input("Headshot-raja (esim. 7.5):", value=7.5, step=0.5, key="t3_r")

    if st.button("Laske headshot-todennäköisyydet", type="primary"):
        if hs_pelaaja_nimi and hs_pelaajan_tiimi and hs_vastustaja_tiimi and hs_pelaajan_tiimi != hs_vastustaja_tiimi:
            with st.spinner("Simuloidaan..."):
                elos, names_dict = calculate_map_elos()
                t1_id = teams_df.loc[teams_df['name'] == hs_pelaajan_tiimi, 'id'].values[0]
                t2_id = teams_df.loc[teams_df['name'] == hs_vastustaja_tiimi, 'id'].values[0]
                
                prob1 = get_expected_score(elos.get(hs_kartta_nimi, {}).get(t1_id, 1500), elos.get(hs_kartta_nimi, {}).get(t2_id, 1500))
                prob2 = get_expected_score(elos.get(hs_kartta_nimi, {}).get(t2_id, 1500), elos.get(hs_kartta_nimi, {}).get(t1_id, 1500))
                
                ennustetut_kierrokset = 22.5 - ((max(prob1, prob2) - 0.5) * 9.0)
                data = get_player_stats(hs_pelaaja_nimi, hs_kartta_nimi)
                
                if data:
                    prob_over, prob_under = simulate_player_headshots(data, hs_raja, ennustetut_kierrokset)
                    st.session_state['t3_data'] = {
                        'nimi': data['name'], 'kartta': data['map'], 'hpr': data['hpr'], 'hs_percent': data['hs_percent'],
                        'rounds': ennustetut_kierrokset, 'over': prob_over, 'under': prob_under,
                        'raja': hs_raja, 'vastustaja': hs_vastustaja_tiimi
                    }

    # Näytetään tulokset muistista (Headshotit)
    if 't3_data' in st.session_state:
        d = st.session_state['t3_data']
        odds_over = 1 / d['over'] if d['over'] > 0 else 0
        odds_under = 1 / d['under'] if d['under'] > 0 else 0
        
        st.write("---")
        st.subheader(f"🎯 Tulokset: {d['nimi']} @ {d['kartta'].capitalize()} vs {d['vastustaja']}")
        st.caption(f"Historiallinen HS%: **{d['hs_percent']:.1f}%** | HPR: {d['hpr']:.3f} | Ennustettu kesto: **{d['rounds']:.1f} kierrosta**")
        
        res_col1, res_col2 = st.columns(2)
        with res_col1:
            st.metric(label=f"OVER {d['raja']} HS", value=f"{d['over']*100:.1f} %", delta=f"Kerroinraja: {odds_over:.2f}", delta_color="off")
        with res_col2:
            st.metric(label=f"UNDER {d['raja']} HS", value=f"{d['under']*100:.1f} %", delta=f"Kerroinraja: {odds_under:.2f}", delta_color="off")
        
        # --- UUSI: EV-LASKURI ---
        st.write("📊 **EV-Laskuri (Etsi ylikertoimet)**")
        ev_col1, ev_col2 = st.columns(2)
        with ev_col1:
            bookie_over_hs = st.number_input("Syötä bookkerin OVER-kerroin:", min_value=1.0, value=1.0, step=0.01, key="ev_over_t3")
            if bookie_over_hs > 1.0:
                ev_over = (d['over'] * bookie_over_hs - 1) * 100
                color = "green" if ev_over > 0 else "red"
                st.markdown(f"Odotusarvo: <strong style='color:{color}'>{ev_over:+.1f} %</strong>", unsafe_allow_html=True)
                
        with ev_col2:
            bookie_under_hs = st.number_input("Syötä bookkerin UNDER-kerroin:", min_value=1.0, value=1.0, step=0.01, key="ev_under_t3")
            if bookie_under_hs > 1.0:
                ev_under = (d['under'] * bookie_under_hs - 1) * 100
                color = "green" if ev_under > 0 else "red"
                st.markdown(f"Odotusarvo: <strong style='color:{color}'>{ev_under:+.1f} %</strong>", unsafe_allow_html=True)
        st.write("---")

        with st.expander("➕ Kirjaa veto tästä kohteesta", expanded=True):
            # Valintanappi lomakkeen ulkopuolelle
            suunta_hs = st.radio("Vedon suunta:", ["OVER", "UNDER"], horizontal=True, key="suunta_t3")
            
            with st.form("bet_form_hs"):
                b_desc = st.text_input("Vedon kuvaus", value=f"{d['nimi']} {suunta_hs} {d['raja']} HS, {d['kartta'].capitalize()}, {d['vastustaja']}")
                
                c1, c2 = st.columns(2)
                b_stake = c1.number_input("Panos (€)", min_value=1.0, value=10.0, step=1.0)
                
                # Valitaan oletuskerroin automaattisesti suunnan mukaan
                if suunta_hs == "OVER":
                    default_odds_hs = bookie_over_hs if bookie_over_hs > 1.0 else round(odds_over, 2)
                else:
                    default_odds_hs = bookie_under_hs if bookie_under_hs > 1.0 else round(odds_under, 2)
                    
                b_odds = c2.number_input("Kerroin", min_value=1.01, value=float(default_odds_hs), step=0.01)
                
                if st.form_submit_button("Tallenna veto"):
                    save_bet("Headshotit", b_desc, b_stake, b_odds)
                    del st.session_state['t3_data']
                    st.rerun()

# ==========================================
# VÄLILEHTI 4: VETOSEURANTA (TULOKSET)
# ==========================================
with tab4:
    st.header("📈 Vetoseuranta ja Kassanhallinta")
    
    conn = sqlite3.connect('hltv_data.db')
    bets_df = pd.read_sql_query("SELECT * FROM bets ORDER BY id DESC", conn)
    
    if not bets_df.empty:
        # A. RATKAISE ODOTTAVAT VEDOT
        pending_bets = bets_df[bets_df['status'] == 'Odottaa']
        if not pending_bets.empty:
            st.info(f"Sinulla on {len(pending_bets)} ratkaisematonta vetoa.")
            with st.expander("📝 Merkitse vetojen tulokset", expanded=True):
                for index, row in pending_bets.iterrows():
                    with st.form(f"resolve_{row['id']}"):
                        st.write(f"**{row['date'][:10]} | {row['type']} | {row['description']}** (Panos: {row['stake']}€ @ {row['odds']})")
                        new_status = st.radio("Tulos:", ["Voitto", "Tappio", "Odottaa"], index=2, horizontal=True, key=f"rad_{row['id']}")
                        if st.form_submit_button("Päivitä tulos"):
                            if new_status != "Odottaa":
                                c = conn.cursor()
                                c.execute("UPDATE bets SET status = ? WHERE id = ?", (new_status, row['id']))
                                conn.commit()
                                st.rerun()
                                
        st.write("---")

        # --- UUSI OMINAISUUS: MUOKKAA TAI POISTA RATKAISTUJA VETOJA ---
        resolved_bets_for_edit = bets_df[bets_df['status'] != 'Odottaa']
        if not resolved_bets_for_edit.empty:
            with st.expander("✏️ Muokkaa tai poista aiempia vetoja", expanded=False):
                # Luodaan valikkoon nätisti muotoiltu lista ratkaistuista vedoista
                bet_dict = {f"[{row['date'][:10]}] {row['description']} ({row['status']}) - {row['stake']}€ @ {row['odds']}": row for _, row in resolved_bets_for_edit.iterrows()}
                
                selected_bet_key = st.selectbox("Valitse veto:", list(bet_dict.keys()), index=None, placeholder="Etsi muokattava veto...")
                
                if selected_bet_key:
                    bet = bet_dict[selected_bet_key]
                    
                    with st.form(f"edit_form_{bet['id']}"):
                        st.caption(f"Muokataan vetoa ID: {bet['id']}")
                        c1, c2, c3 = st.columns(3)
                        
                        new_stake = c1.number_input("Panos (€)", min_value=1.0, value=float(bet['stake']), step=1.0)
                        new_odds = c2.number_input("Kerroin", min_value=1.01, value=float(bet['odds']), step=0.01)
                        
                        # Etsitään nykyisen statuksen indeksi valikkoa varten
                        status_options = ["Voitto", "Tappio", "Odottaa"]
                        current_status_idx = status_options.index(bet['status'])
                        new_status = c3.selectbox("Tulos", status_options, index=current_status_idx)
                        
                        btn1, btn2 = st.columns(2)
                        save_clicked = btn1.form_submit_button("Tallenna muutokset", type="primary")
                        delete_clicked = btn2.form_submit_button("🗑️ Poista veto lopullisesti")
                        
                        if save_clicked:
                            cur = conn.cursor()
                            cur.execute("UPDATE bets SET stake = ?, odds = ?, status = ? WHERE id = ?", 
                                        (new_stake, new_odds, new_status, bet['id']))
                            conn.commit()
                            st.success("Vedon tiedot päivitetty!")
                            st.rerun()
                            
                        if delete_clicked:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM bets WHERE id = ?", (bet['id'],))
                            conn.commit()
                            st.warning("Veto poistettu tietokannasta!")
                            st.rerun()
        
        # B. TILASTOT JA GRAAFIT
        filter_type = st.radio("Näytä tilastot:", ["Yhteensä", "Ottelu", "Tapot", "Headshotit"], horizontal=True)
        
        filtered_df = bets_df.copy() if filter_type == "Yhteensä" else bets_df[bets_df['type'] == filter_type].copy()
            
        if not filtered_df.empty:
            def calculate_profit(row):
                if row['status'] == 'Voitto': return (row['stake'] * row['odds']) - row['stake']
                elif row['status'] == 'Tappio': return -row['stake']
                return 0.0
                
            filtered_df['Voitto/Tappio (€)'] = filtered_df.apply(calculate_profit, axis=1)
            # Järjestetään vanhimmasta uusimpaan graafia varten
            chronological_df = filtered_df.sort_values(by='id', ascending=True).copy()
            chronological_df['Kassan kehitys'] = chronological_df['Voitto/Tappio (€)'].cumsum()
            
            resolved_bets = chronological_df[chronological_df['status'] != 'Odottaa']
            total_profit = resolved_bets['Voitto/Tappio (€)'].sum()
            total_staked = resolved_bets['stake'].sum()
            
            win_count = len(resolved_bets[resolved_bets['status'] == 'Voitto'])
            total_resolved = len(resolved_bets)
            win_rate = (win_count / total_resolved * 100) if total_resolved > 0 else 0
            roi = ((total_profit / total_staked) * 100) if total_staked > 0 else 0
            
            # Mittaristot
            st.subheader(f"Tilastot: {filter_type}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Ratkenneet vedot", total_resolved)
            m2.metric("Osumis-%", f"{win_rate:.1f} %")
            m3.metric("Nettotuotto", f"{total_profit:+.2f} €", delta_color="normal" if total_profit >= 0 else "inverse")
            m4.metric("Palautus-% (ROI)", f"{100 + roi:.1f} %")
            
            # Kassan graafi
            # Kassan graafi
            st.write("📈 **Kassan kehitys (€)**")
            
            # Kopioidaan tarvittavat sarakkeet graafia varten
            chart_df = chronological_df[['date', 'Kassan kehitys']].copy()
            
            # Muutetaan tekstimalliset päivämäärät oikeaksi aikamuodoksi (datetime)
            chart_df['date'] = pd.to_datetime(chart_df['date'])
            
            # Asetetaan päivämäärä indeksiksi, jolloin Streamlit tajuaa laittaa sen X-akselille
            chart_df = chart_df.set_index('date')
            
            st.line_chart(chart_df)
            
            # Vetohistoria
            st.write("📋 **Kirjatut vedot**")
            display_df = filtered_df[['date', 'type', 'description', 'stake', 'odds', 'status', 'Voitto/Tappio (€)']]
            display_df.columns = ['Pvm', 'Tyyppi', 'Kohde', 'Panos (€)', 'Kerroin', 'Tila', 'Tuotto (€)']
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
        else:
            st.info(f"Ei kirjattuja vetoja kategoriassa: {filter_type}")
    else:
        st.info("Et ole vielä kirjannut yhtään vetoa. Tallenna ensimmäinen vetosi muiden välilehtien laskurien kautta!")
        
    conn.close()