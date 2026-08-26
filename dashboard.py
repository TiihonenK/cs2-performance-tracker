import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

from players_props import (
    get_team_players_overall_stats,
    simulate_team_kills,
    sample_match_lengths,
    kills_probability_at_line,
)

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

    # UUSI: kartta-sarake (Kartta 1-5), jotta voidaan merkitä millä kartalla veto on lyöty
    try:
        c.execute("ALTER TABLE bets ADD COLUMN map TEXT DEFAULT 'Kartta 1'")
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

# UUSI: save_bet ottaa nyt vastaan myös turnauksen nimen JA kartan
def save_bet(bet_type, description, stake, odds, tournament, map_name):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect('my_bets.db')
    c = conn.cursor()
    c.execute("INSERT INTO bets (date, type, description, stake, odds, status, tournament, map) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              (date_str, bet_type, description, stake, odds, "Odottaa", tournament, map_name))
    conn.commit()
    conn.close()
    st.success(f"✅ Veto tallennettu turnaukseen: {tournament} ({map_name})!")

init_betting_db()

def filter_bets(df, query):
    """Suodattaa vetotaulukon hakusanalla (pelaaja/joukkue, turnaus, kartta, tyyppi, tila)."""
    if not query:
        return df
    q = query.strip().lower()
    search_cols = [c for c in ['description', 'tournament', 'map', 'type', 'status'] if c in df.columns]
    mask = pd.Series(False, index=df.index)
    for col in search_cols:
        mask = mask | df[col].astype(str).str.lower().str.contains(q, na=False)
    return df[mask]

def show_bet_summary(df):
    """Näyttää yhteenvedon (ROI %, vetojen määrä, tulos €, vireillä olevat) annetusta
    vetojoukosta. ROI lasketaan vain RATKAISTUISTA vedoista (Voitto/Tappio), koska
    odottavien vetojen panos ei vielä ole tuottanut mitään tulosta - sen mukaan
    ottaminen nimittäjään vääristäisi lukua turhaan huonompaan suuntaan."""
    total_bets = len(df)
    settled = df[df['status'].isin(['Voitto', 'Tappio'])]
    pending = df[df['status'] == 'Odottaa']
    total_staked_settled = settled['stake'].sum()

    profit_per_row = df.apply(
        lambda x: (x['stake'] * (x['odds'] - 1)) if x['status'] == 'Voitto'
        else (-x['stake'] if x['status'] == 'Tappio' else 0),
        axis=1
    )
    total_profit = profit_per_row.sum()
    roi = (total_profit / total_staked_settled * 100) if total_staked_settled > 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ROI % (ratkaistuista)", f"{roi:+.1f} %")
    c2.metric("Vetoja yhteensä", f"{total_bets}")
    c3.metric("Tulos", f"{total_profit:+.2f} €")
    c4.metric("Vireillä", f"{len(pending)}")

st.set_page_config(page_title="CS2 Vetomalli", layout="wide", page_icon="🎯")
st.title("CS2 Vedonlyöntimalli 2.1")

conn = sqlite3.connect('hltv_data.db')
teams_df = pd.read_sql_query("SELECT id, name FROM teams ORDER BY name", conn)
conn.close()
team_names_list = teams_df['name'].tolist()

tab1, tab2 = st.tabs(["Vedonlyönti", "📈 Vetoseuranta"])

# ==========================================
# VÄLILEHTI 1: TAPPOVEDOT
# ==========================================
with tab1:
    st.header("Vedonlyönti")
    
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

            # KORJAUS: kierrosmäärä arvotaan bootstrapilla oikeasta historiallisesta
            # jakaumasta (ei enää Normal-jakaumalla laskettu 21.5 - |p1-p2|*8.5).
            # Sama taulukko annetaan molemmille joukkueille, jotta obe joukkueen
            # pelaajat "pelaavat" saman simuloidun ottelun pituuden.
            sim_lengths = sample_match_lengths(prob1, num_simulations=10000)

            t1_players = get_team_players_overall_stats(team1)
            t2_players = get_team_players_overall_stats(team2)

            t1_sim = simulate_team_kills(t1_players, prob1, sim_lengths)
            t2_sim = simulate_team_kills(t2_players, prob2, sim_lengths)

            st.session_state['sim_results'] = {
                't1': team1, 't2': team2, 'p1': prob1, 'p2': prob2,
                'rounds': float(np.mean(sim_lengths)),
                't1_data': t1_sim, 't2_data': t2_sim,
            }

    # Tulosten näyttäminen
    if 'sim_results' in st.session_state:
        res = st.session_state['sim_results']
        st.write("---")
        st.markdown(f"### **{res['t1']} vs {res['t2']}** — Arvioitu kesto: **{res['rounds']:.2f}** kierrosta")
        st.markdown(f"*{res['t1']} ({res['p1']*100:.1f}%) | {res['t2']} ({res['p2']*100:.1f}%)*")

        # Kootaan hakemisto pelaajan nimi -> raaka simulaatiotaulukko, jotta
        # sitä voidaan käyttää alempana mielivaltaisen (esim. bookkerin) linjan
        # todennäköisyyden laskemiseen.
        all_players_data = res['t1_data'] + res['t2_data']
        sim_kills_lookup = {p['Pelaaja']: p['_sim_kills'] for p in all_players_data}

        # Näytetään Joukkue 1
        st.subheader(f"**{res['t1']}**")
        df1 = pd.DataFrame(res['t1_data']).drop(columns=['_sim_kills'])
        st.dataframe(df1, width='stretch', hide_index=True)

        # Näytetään Joukkue 2
        st.write("<br>", unsafe_allow_html=True)
        st.subheader(f"**{res['t2']}**")
        df2 = pd.DataFrame(res['t2_data']).drop(columns=['_sim_kills'])
        st.dataframe(df2, width='stretch', hide_index=True)

        st.caption(
            "'Line'-sarake on mallin oma automaattisesti generoitu keskikohta - "
            "EI bookkerin tarjoamaa linjaa. Käytä alla olevaa vetolomaketta nähdäksesi "
            "mallin arvion juuri sille linjalle, jonka bookkeri oikeasti tarjoaa."
        )

        # VEDONLYÖNTILOMAKE
        st.write("---")
        st.subheader("Kirjaa veto")
        
        # Haetaan aktiiviset turnaukset tietokannasta
        conn = sqlite3.connect('my_bets.db')
        active_tournaments = pd.read_sql_query("SELECT name FROM tournaments WHERE status = 'Aktiivinen'", conn)['name'].tolist()
        conn.close()

        player_names = list(sim_kills_lookup.keys())

        # HUOM: tämä osio EI ole enää st.form:in sisällä, koska Streamlit-formit
        # eivät päivity reaaliaikaisesti - haluamme näyttää mallin arvion heti
        # kun käyttäjä vaihtaa pelaajaa/linjaa, ennen tallennusta.
        c1, c2, c3, c4 = st.columns(4)
        valittu_pelaaja = c1.selectbox("Pelaaja:", player_names, key="bet_player")
        suunta = c2.radio("Suunta:", ["OVER", "UNDER"], horizontal=True, key="bet_direction")
        custom_line = c3.number_input("Raja (esim 14.5)", value=14.5, step=0.5, key="bet_line")
        valittu_kartta = c4.selectbox("Kartta:", [f"Kartta {i}" for i in range(1, 6)], key="bet_map")

        # KORJAUS 1 (ydinkorjaus): mallin arvio lasketaan AINA juuri sille linjalle
        # jonka käyttäjä syöttää tähän - ei mallin omalle auto-generoidulle linjalle.
        model_odds = None
        if valittu_pelaaja in sim_kills_lookup:
            p_over, p_under, odds_over, odds_under = kills_probability_at_line(
                sim_kills_lookup[valittu_pelaaja], custom_line
            )
            model_p = p_over if suunta == "OVER" else p_under
            model_odds = odds_over if suunta == "OVER" else odds_under
            if model_odds:
                st.info(
                    f"📊 Mallin arvio linjalle **{custom_line}**: **{suunta}** {model_p*100:.1f}% "
                    f"→ mallin kerroinraja **{model_odds:.2f}**"
                )

        c4, c5, c6 = st.columns(3)
        panos = c4.number_input("Panos (€)", min_value=1.0, value=10.0, step=1.0, key="bet_stake")
        kerroin = c5.number_input("Bookkerin kerroin", min_value=1.01, value=1.85, step=0.01, key="bet_odds")
        # UUSI: Turnauksen valinta
        valittu_turnaus = c6.selectbox("Turnaus", active_tournaments, key="bet_tournament")

        if model_odds:
            if kerroin > model_odds:
                st.success(f"✅ Bookkerin kerroin ({kerroin}) ylittää mallin kerroinrajan ({model_odds:.2f}) - mallin mukaan mahdollista arvoa.")
            else:
                st.warning(f"⚠️ Bookkerin kerroin ({kerroin}) EI ylitä mallin kerroinrajaa ({model_odds:.2f}).")

        if st.button("Tallenna veto", type="primary"):
            desc = f"{valittu_pelaaja} {suunta} {custom_line} ({res['t1']} vs {res['t2']}, {valittu_kartta})"
            save_bet("Tapot", desc, panos, kerroin, valittu_turnaus, valittu_kartta)

# ==========================================
# VÄLILEHTI 2: VETOSEURANTA JA TURNAUKSET
# ==========================================
with tab2:
    st.header("📈 Vetoseuranta & Turnaukset")
    
    conn = sqlite3.connect('my_bets.db')
    bets_df = pd.read_sql_query("SELECT * FROM bets ORDER BY id DESC", conn)
    tournaments_df = pd.read_sql_query("SELECT * FROM tournaments", conn)

    # UUSI: hakukenttä lyödyille vedoille. Pidetään omana hakutuloslistanaan eikä
    # suodateta bets_df:ää globaalisti, koska alempien osioiden kassakäyrät
    # pitää laskea koko historiasta - suodatettu osajoukko vääristäisi ne.
    search_query = st.text_input(
        "🔍 Hae vetoja (esim. pelaajan nimi, joukkue, kartta, turnaus)", key="bet_search"
    )
    if search_query:
        search_results = filter_bets(bets_df, search_query)
        st.caption(f"Löytyi {len(search_results)} vetoa haulla \"{search_query}\".")
        st.dataframe(search_results, width='stretch', hide_index=True)
        st.write("---")

    # Luodaan alivälilehdet navigoinnin helpottamiseksi
    subtab1, subtab2, subtab3 = st.tabs(["⚙️ Hallinta & Ratkaisu", "📊 Aktiiviset Turnaukset", "📚 Turnaushistoria & Kaikki"])
    
    with subtab1:
        # 1. RATKAISE ODOTTAVAT VEDOT
        st.subheader("Ratkaise odottavat vedot")
        if not bets_df.empty:
            pending_bets = bets_df[bets_df['status'] == 'Odottaa']
            # UUSI: sama yläreunan hakukenttä suodattaa myös tämän listan, jotta
            # yksittäisen vedon löytää ja ratkaisee helpommin isommastakin joukosta.
            if search_query:
                pending_bets = filter_bets(pending_bets, search_query)
                if pending_bets.empty:
                    st.caption(f"Ei odottavia vetoja haulla \"{search_query}\".")
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

        st.write("---")
        
        # 2. MUOKKAA TAI POISTA RATKAISTUJA VETOJA
        st.subheader("Muokkaa tai poista ratkaistuja vetoja")
        resolved_bets_for_edit = bets_df[bets_df['status'] != 'Odottaa']
        
        if not resolved_bets_for_edit.empty:
            with st.expander("✏️ Etsi ja muokkaa vanhoja vetoja", expanded=False):
                # Luodaan valikkoon lista vedoista
                bet_dict = {f"[{row['tournament']}] {row['date'][:10]} | {row['description']} ({row['status']})": row for _, row in resolved_bets_for_edit.iterrows()}
                
                selected_bet_key = st.selectbox("Valitse veto:", list(bet_dict.keys()), index=None, placeholder="Etsi muokattava veto...")
                
                if selected_bet_key:
                    bet = bet_dict[selected_bet_key]
                    
                    with st.form(f"edit_form_{bet['id']}"):
                        st.caption(f"Muokataan vetoa (ID: {bet['id']})")
                        c1, c2, c3 = st.columns(3)
                        
                        new_stake = c1.number_input("Panos (€)", min_value=1.0, value=float(bet['stake']), step=1.0)
                        new_odds = c2.number_input("Kerroin", min_value=1.01, value=float(bet['odds']), step=0.01)
                        
                        status_options = ["Voitto", "Tappio", "Odottaa"]
                        current_status_idx = status_options.index(bet['status']) if bet['status'] in status_options else 0
                        new_status = c3.selectbox("Tulos", status_options, index=current_status_idx)
                        
                        btn1, btn2 = st.columns(2)
                        save_clicked = btn1.form_submit_button("Tallenna muutokset", type="primary")
                        delete_clicked = btn2.form_submit_button("🗑️ Poista veto lopullisesti")
                        
                        if save_clicked:
                            cur = conn.cursor()
                            cur.execute("UPDATE bets SET stake = ?, odds = ?, status = ? WHERE id = ?", 
                                        (new_stake, new_odds, new_status, bet['id']))
                            conn.commit()
                            st.success("Päivitetty!")
                            st.rerun()
                            
                        if delete_clicked:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM bets WHERE id = ?", (bet['id'],))
                            conn.commit()
                            st.warning("Veto poistettu!")
                            st.rerun()
                            
        st.write("---")

        # 3. LUO UUSI TURNAUS
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
        
        # 4. SULJE TURNAUS
        st.subheader("Sulje turnaus")
        active_list = tournaments_df[tournaments_df['status'] == 'Aktiivinen']['name'].tolist()
        if active_list:
            close_t = st.selectbox("Valitse suljettava turnaus", active_list)
            if st.button(f"🔒 Siirrä '{close_t}' historiaan"):
                c = conn.cursor()
                c.execute("UPDATE tournaments SET status = 'Suljettu' WHERE name = ?", (close_t,))
                conn.commit()
                st.rerun()

    with subtab2:
        st.subheader("Aktiivisten turnausten seuranta")
        if active_list:
            view_t = st.selectbox("Näytä data turnaukselle:", active_list, key="view_active")
            t_bets = bets_df[bets_df['tournament'] == view_t].copy()

            if not t_bets.empty:
                # Laske kassan kehitys kronologisessa järjestyksessä
                t_bets = t_bets.sort_values('id')
                t_bets['Tuotto'] = t_bets.apply(lambda x: (x['stake'] * (x['odds'] - 1)) if x['status'] == 'Voitto' else (-x['stake'] if x['status'] == 'Tappio' else 0), axis=1)
                t_bets['Kassa (€)'] = t_bets['Tuotto'].cumsum()

                # UUSI: ROI %, vetojen määrä ja tulos euroina graafin yläpuolella
                show_bet_summary(t_bets)

                # Piirretään graafi kassan kehityksestä
                st.line_chart(t_bets['Kassa (€)'].reset_index(drop=True))
                
                # Näytetään taulukko (piilotetaan laskennalliset apusarakkeet)
                st.dataframe(t_bets.drop(columns=['Tuotto', 'Kassa (€)']), width='stretch', hide_index=True)
        else:
            st.info("Ei aktiivisia turnauksia.")

    with subtab3:
        st.subheader("Turnaushistoria (Suljetut)")
        closed_list = tournaments_df[tournaments_df['status'] == 'Suljettu']['name'].tolist()
        if closed_list:
            hist_t = st.selectbox("Tarkastele mennyttä turnausta:", closed_list, key="view_closed")
            hist_bets = bets_df[bets_df['tournament'] == hist_t].copy()
            
            if not hist_bets.empty:
                hist_bets = hist_bets.sort_values('id')
                hist_bets['Tuotto'] = hist_bets.apply(lambda x: (x['stake'] * (x['odds'] - 1)) if x['status'] == 'Voitto' else (-x['stake'] if x['status'] == 'Tappio' else 0), axis=1)
                hist_bets['Kassa (€)'] = hist_bets['Tuotto'].cumsum()

                # UUSI: ROI %, vetojen määrä ja tulos euroina graafin yläpuolella
                show_bet_summary(hist_bets)

                st.line_chart(hist_bets['Kassa (€)'].reset_index(drop=True))
                st.dataframe(hist_bets.drop(columns=['Tuotto', 'Kassa (€)']), width='stretch', hide_index=True)
        else:
            st.info("Ei suljettuja turnauksia historiassa.")
            
        st.write("---")
        st.subheader("Kaikki vedot (All-time)")
        if not bets_df.empty:
            all_bets = bets_df.copy().sort_values('id')
            all_bets['Tuotto'] = all_bets.apply(lambda x: (x['stake'] * (x['odds'] - 1)) if x['status'] == 'Voitto' else (-x['stake'] if x['status'] == 'Tappio' else 0), axis=1)
            all_bets['Kassa (€)'] = all_bets['Tuotto'].cumsum()

            # UUSI: ROI %, vetojen määrä ja tulos euroina graafin yläpuolella
            show_bet_summary(all_bets)

            st.line_chart(all_bets['Kassa (€)'].reset_index(drop=True))
            st.dataframe(all_bets.drop(columns=['Tuotto', 'Kassa (€)']), width='stretch', hide_index=True)

    conn.close()