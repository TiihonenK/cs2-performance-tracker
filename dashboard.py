import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime

from players_props import get_team_players_overall_stats, simulate_team_kills

def init_betting_db():
    conn = sqlite3.connect('my_bets.db')
    c = conn.cursor()
    
    # Luodaan vetotaulu (jos ei ole jo)
    c.execute('''CREATE TABLE IF NOT EXISTS bets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, type TEXT, description TEXT, stake REAL, odds REAL, status TEXT)''')
    
    # UUSI: Yritetään lisätä turnaus-sarake vanhaan vetotauluun (ei kaadu, jos se on jo siellä)
    try:
        c.execute("ALTER TABLE bets ADD COLUMN tournament TEXT DEFAULT 'Yleinen'")
    except sqlite3.OperationalError:
        pass
        
    # UUSI: Luodaan turnaustaulu
    c.execute('''CREATE TABLE IF NOT EXISTS tournaments
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, status TEXT)''')
                 
    # Lisätään oletusturnaus, jos taulu on tyhjä
    c.execute("SELECT COUNT(*) FROM tournaments")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO tournaments (name, status) VALUES ('Yleinen', 'Aktiivinen')")
        
    conn.commit()
    conn.close()

# UUSI: save_bet ottaa nyt vastaan myös turnauksen nimen
def save_bet(bet_type, description, stake, odds, tournament):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect('my_bets.db')
    c = conn.cursor()
    c.execute("INSERT INTO bets (date, type, description, stake, odds, status, tournament) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (date_str, bet_type, description, stake, odds, "Odottaa", tournament))
    conn.commit()
    conn.close()
    st.success(f"✅ Veto tallennettu turnaukseen: {tournament}!")

init_betting_db()

st.set_page_config(page_title="CS2 Vetomalli", layout="wide", page_icon="🎯")
st.title("🎯 CS2 Vedonlyöntimalli PRO")

conn = sqlite3.connect('hltv_data.db')
teams_df = pd.read_sql_query("SELECT id, name FROM teams ORDER BY name", conn)
conn.close()
team_names_list = teams_df['name'].tolist()

tab1, tab2 = st.tabs(["🔫 Tappovedot", "📈 Vetoseuranta"])

# ==========================================
# VÄLILEHTI 1: TAPPOVEDOT
# ==========================================
with tab1:
    st.header("Pinnacle-pohjainen Monte Carlo")
    
    # Yläosan valikot ja kertoimet
    c1, c2, c3, c4 = st.columns(4)
    team1 = c1.selectbox("Joukkue 1:", team_names_list, index=None)
    odds1 = c2.number_input(f"Kerroin (Joukkue 1)", min_value=1.01, value=1.85, step=0.01)
    
    team2 = c3.selectbox("Joukkue 2:", team_names_list, index=None)
    odds2 = c4.number_input(f"Kerroin (Joukkue 2)", min_value=1.01, value=1.85, step=0.01)

    if st.button("Laske arviot", type="primary") and team1 and team2 and team1 != team2:
        with st.spinner("Simuloidaan joukkueita..."):
            # Laske todennäköisyydet ilman marginaalia
            p1_implied = 1 / odds1
            p2_implied = 1 / odds2
            margin = p1_implied + p2_implied
            prob1 = p1_implied / margin
            prob2 = p2_implied / margin
            
            # Arvioi kierrokset. 50/50 = 21.5 rounds. 90/10 stomp = ~15 rounds.
            expected_rounds = 21.5 - (abs(prob1 - prob2) * 8.5)
            
            t1_players = get_team_players_overall_stats(team1)
            t2_players = get_team_players_overall_stats(team2)
            
            t1_sim = simulate_team_kills(t1_players, prob1, expected_rounds)
            t2_sim = simulate_team_kills(t2_players, prob2, expected_rounds)
            
            st.session_state['sim_results'] = {
                't1': team1, 't2': team2, 'p1': prob1, 'p2': prob2, 
                'rounds': expected_rounds, 't1_data': t1_sim, 't2_data': t2_sim
            }

    # Tulosten näyttäminen
    if 'sim_results' in st.session_state:
        res = st.session_state['sim_results']
        st.write("---")
        st.markdown(f"### **{res['t1']} vs {res['t2']}** — Arvioitu kesto: **{res['rounds']:.2f}** kierrosta")
        st.markdown(f"*{res['t1']} ({res['p1']*100:.1f}%) | {res['t2']} ({res['p2']*100:.1f}%)*")
        
        # Näytetään Joukkue 1
        st.subheader(f"**{res['t1']}**")
        df1 = pd.DataFrame(res['t1_data'])
        st.dataframe(df1.drop(columns=['_p_over_raw', '_p_under_raw']), use_container_width=True, hide_index=True)
        
        # Näytetään Joukkue 2
        st.write("<br>", unsafe_allow_html=True)
        st.subheader(f"**{res['t2']}**")
        df2 = pd.DataFrame(res['t2_data'])
        st.dataframe(df2.drop(columns=['_p_over_raw', '_p_under_raw']), use_container_width=True, hide_index=True)
        
        # VEDONLYÖNTILOMAKE
        # VEDONLYÖNTILOMAKE
        st.write("---")
        st.subheader("➕ Kirjaa veto")
        
        # Haetaan aktiiviset turnaukset tietokannasta
        conn = sqlite3.connect('my_bets.db')
        active_tournaments = pd.read_sql_query("SELECT name FROM tournaments WHERE status = 'Aktiivinen'", conn)['name'].tolist()
        conn.close()
        
        all_players_data = res['t1_data'] + res['t2_data']
        player_names = [p['Pelaaja'] for p in all_players_data]
        
        with st.form("bet_form"):
            c1, c2, c3 = st.columns(3)
            valittu_pelaaja = c1.selectbox("Pelaaja:", player_names)
            suunta = c2.radio("Suunta:", ["OVER", "UNDER"], horizontal=True)
            custom_line = c3.number_input("Raja (esim 14.5)", value=14.5, step=0.5)
            
            c4, c5, c6 = st.columns(3)
            panos = c4.number_input("Panos (€)", min_value=1.0, value=10.0, step=1.0)
            kerroin = c5.number_input("Bookkerin kerroin", min_value=1.01, value=1.85, step=0.01)
            # UUSI: Turnauksen valinta
            valittu_turnaus = c6.selectbox("Turnaus", active_tournaments)
            
            if st.form_submit_button("Tallenna veto"):
                desc = f"{valittu_pelaaja} {suunta} {custom_line} ({res['t1']} vs {res['t2']})"
                # Lähetetään valittu_turnaus tallennusfunktiolle
                save_bet("Tapot", desc, panos, kerroin, valittu_turnaus)

# ==========================================
# VÄLILEHTI 2: VETOSEURANTA JA TURNAUKSET
# ==========================================
with tab2:
    st.header("📈 Vetoseuranta & Turnaukset")
    
    conn = sqlite3.connect('my_bets.db')
    bets_df = pd.read_sql_query("SELECT * FROM bets ORDER BY id DESC", conn)
    tournaments_df = pd.read_sql_query("SELECT * FROM tournaments", conn)
    
    # Luodaan alivälilehdet navigoinnin helpottamiseksi
    subtab1, subtab2, subtab3 = st.tabs(["⚙️ Hallinta & Ratkaisu", "📊 Aktiiviset Turnaukset", "📚 Turnaushistoria & Kaikki"])
    
    with subtab1:
        st.subheader("Luo uusi turnaus")
        with st.form("new_tournament_form"):
            col_t1, col_t2 = st.columns([3, 1])
            new_t_name = col_t1.text_input("Turnauksen nimi (esim. IEM Cologne 2024)")
            if col_t2.form_submit_button("Luo turnaus"):
                if new_t_name:
                    try:
                        c = conn.cursor()
                        c.execute("INSERT INTO tournaments (name, status) VALUES (?, ?)", (new_t_name, "Aktiivinen"))
                        conn.commit()
                        st.success(f"Turnaus '{new_t_name}' luotu!")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Tämän niminen turnaus on jo olemassa!")
                        
        st.write("---")
        st.subheader("Sulje turnaus")
        active_list = tournaments_df[tournaments_df['status'] == 'Aktiivinen']['name'].tolist()
        if active_list:
            close_t = st.selectbox("Valitse suljettava turnaus", active_list)
            if st.button(f"🔒 Siirrä '{close_t}' historiaan"):
                c = conn.cursor()
                c.execute("UPDATE tournaments SET status = 'Suljettu' WHERE name = ?", (close_t,))
                conn.commit()
                st.rerun()
                
        st.write("---")
        st.subheader("Ratkaise odottavat vedot")
        if not bets_df.empty:
            pending_bets = bets_df[bets_df['status'] == 'Odottaa']
            for index, row in pending_bets.iterrows():
                with st.form(f"resolve_{row['id']}"):
                    st.write(f"**[{row['tournament']}]** {row['date'][:10]} | {row['description']} (Panos: {row['stake']}€ @ {row['odds']})")
                    new_status = st.radio("Tulos:", ["Voitto", "Tappio", "Odottaa"], index=2, horizontal=True)
                    if st.form_submit_button("Päivitä"):
                        if new_status != "Odottaa":
                            c = conn.cursor()
                            c.execute("UPDATE bets SET status = ? WHERE id = ?", (new_status, row['id']))
                            conn.commit()
                            st.rerun()

    with subtab2:
        st.subheader("Aktiivisten turnausten seuranta")
        if active_list:
            view_t = st.selectbox("Näytä data turnaukselle:", active_list, key="view_active")
            t_bets = bets_df[bets_df['tournament'] == view_t]
            st.write(f"Vetoja yhteensä: **{len(t_bets)}**")
            st.dataframe(t_bets, use_container_width=True, hide_index=True)
            # Tähän voit halutessasi lisätä myöhemmin kassan graafin, esim. st.line_chart()
        else:
            st.info("Ei aktiivisia turnauksia.")

    with subtab3:
        st.subheader("Turnaushistoria (Suljetut)")
        closed_list = tournaments_df[tournaments_df['status'] == 'Suljettu']['name'].tolist()
        if closed_list:
            hist_t = st.selectbox("Tarkastele mennyttä turnausta:", closed_list, key="view_closed")
            hist_bets = bets_df[bets_df['tournament'] == hist_t]
            st.dataframe(hist_bets, use_container_width=True, hide_index=True)
        else:
            st.info("Ei suljettuja turnauksia historiassa.")
            
        st.write("---")
        st.subheader("Kaikki vedot (All-time)")
        st.dataframe(bets_df, use_container_width=True, hide_index=True)

    conn.close()