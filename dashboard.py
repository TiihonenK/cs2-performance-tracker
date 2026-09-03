import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

from players_props import (
    get_team_players_overall_stats,
    simulate_team_kills,
    kills_probability_at_line,
)
from match_simulator import simulate_match_context

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

    # UUSI JA TÄRKEIN: mallin oma todennäköisyys vedon hetkellä + sulkeutumiskerroin.
    # Ilman näitä et voi JÄLKIKÄTEEN tarkistaa onko malli kalibroitu (osuuko 60 %:n
    # vedoista oikeasti 60 %) etkä laskea CLV:tä (voititko markkinan liikkeen).
    # Pelkkä voitto/tappio-historia vaatii satoja vetoja ennen kuin siitä näkee
    # mitään; kalibrointi ja CLV kertovat saman asian kymmenesosalla otoksesta.
    for col, ddl in [("model_prob", "REAL"), ("closing_odds", "REAL")]:
        try:
            c.execute(f"ALTER TABLE bets ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass

    # UUSI: rakenteelliset kentät aktiivisten vetojen muokkausta varten. Aiemmin
    # pelaaja/suunta/raja/joukkueet olivat vain upotettuna description-tekstiin
    # ("Pelaaja OVER 14.5 (T1 vs T2, Kartta 2)"), josta niitä ei voinut luotettavasti
    # purkaa takaisin muokkauslomakkeeseen. Vanhoissa vedoissa nämä ovat NULL kunnes
    # vetoa muokataan kertaalleen - description pysyy silti aina ajan tasalla, koska
    # se rakennetaan uudelleen näistä kentistä joka tallennuksella.
    for col, ddl in [("player", "TEXT"), ("direction", "TEXT"), ("line", "REAL"),
                      ("team1", "TEXT"), ("team2", "TEXT")]:
        try:
            c.execute(f"ALTER TABLE bets ADD COLUMN {col} {ddl}")
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

# UUSI: save_bet ottaa nyt vastaan myös turnauksen nimen JA kartan, sekä (jos
# saatavilla) rakenteelliset pelaaja/suunta/raja/joukkue-kentät myöhempää
# muokkausta varten - ks. init_betting_db():n kommentti näistä sarakkeista.
def save_bet(bet_type, description, stake, odds, tournament, map_name, model_prob=None,
             player=None, direction=None, line=None, team1=None, team2=None):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect('my_bets.db')
    c = conn.cursor()
    c.execute("""INSERT INTO bets (date, type, description, stake, odds, status, tournament, map,
                                    model_prob, player, direction, line, team1, team2)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (date_str, bet_type, description, stake, odds, "Odottaa", tournament, map_name,
               model_prob, player, direction, line, team1, team2))
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
    c1, c2, c3, c4, c5 = st.columns([2, 1, 2, 1, 1])
    team1 = c1.selectbox("Joukkue 1:", team_names_list, index=None)
    odds1 = c2.number_input(f"Kerroin (Joukkue 1)", min_value=1.01, value=1.85, step=0.01)

    team2 = c3.selectbox("Joukkue 2:", team_names_list, index=None)
    odds2 = c4.number_input(f"Kerroin (Joukkue 2)", min_value=1.01, value=1.85, step=0.01)

    # UUSI: kartta valitaan jo tässä, joukkueiden ja kertoimien yhteydessä - ei
    # enää erikseen vetolomakkeella. Streamlit säilyttää valinnan (key=) yli
    # koko sivun uudelleenajojen, joten sama arvo kulkee automaattisesti mukaan
    # alempana "Kirjaa veto" -lomakkeeseen ilman että sitä tarvitsee muistaa
    # vaihtaa erikseen ennen vedon tallennusta.
    valittu_kartta = c5.selectbox("Kartta:", [f"Kartta {i}" for i in range(1, 6)], key="selected_map")

    # PINNACLEN KIERROSTOTAL. Markkinan arvio kartan kestosta on parempi kuin
    # historiaan sovitettu malli: siinä on mukana kartta, kokoonpanot, LAN/online
    # ja kaikki mitä kanta ei tunne. Simulaatiota ei silti voi poistaa - totalista
    # saa vain keskikohdan, ja tappotodennäköisyyksiin tarvitaan koko jakauma.
    # Ankkurointi ottaa markkinalta TASON ja jättää mallille MUODON.
    t1c, t2c, t3c = st.columns(3)
    total_line = t1c.number_input("Pinnaclen kierrostotal (0 = ei käytössä)",
                                  min_value=0.0, max_value=40.0, value=0.0, step=0.5)
    total_over = t2c.number_input("Kerroin YLI", min_value=0.0, value=0.0, step=0.01,
                                  help="Valinnainen. Molemmat kertoimet antamalla marginaali poistetaan ja ankkurointi on tarkin.")
    total_under = t3c.number_input("Kerroin ALLE", min_value=0.0, value=0.0, step=0.01)

    if st.button("Laske arviot", type="primary") and team1 and team2 and team1 != team2:
        with st.spinner("Simuloidaan joukkueita..."):
            # Laske todennäköisyydet ilman marginaalia
            p1_implied = 1 / odds1
            p2_implied = 1 / odds2
            margin = p1_implied + p2_implied
            prob1 = p1_implied / margin
            prob2 = p2_implied / margin

            # KORJAUS: kartta simuloidaan kierros kierrokselta MR12-säännöillä
            # (ks. match_simulator.py). Vanha kaava 21.5 - |p1-p2|*8.5 aliarvioi
            # kierrosmäärän 5-6 kierroksella heti kun ottelussa oli selvä suosikki.
            # Sama konteksti annetaan molemmille joukkueille, jotta kaikki pelaajat
            # pelaavat saman simuloidun kartan.
            ctx = simulate_match_context(
                prob1,
                num_simulations=10000,
                total_line=(total_line if total_line > 0 else None),
                total_over_odds=(total_over if total_over > 1 else None),
                total_under_odds=(total_under if total_under > 1 else None),
            )

            t1_players = get_team_players_overall_stats(team1)
            t2_players = get_team_players_overall_stats(team2)

            t1_sim = simulate_team_kills(t1_players, ctx['rounds'], ctx['share_t1'])
            t2_sim = simulate_team_kills(t2_players, ctx['rounds'], ctx['share_t2'])

            st.session_state['sim_results'] = {
                't1': team1, 't2': team2, 'p1': prob1, 'p2': prob2,
                # KORJAUS: näytetään MEDIAANI, ei keskiarvo. Jatkoaikahäntä
                # (kartat jotka venyvät 28-40+ kierrokseen) vetää keskiarvoa
                # ylöspäin vaikka ENEMMISTÖ karttoja päättyisi lyhyempään -
                # kun kierrostotal on ankkuroitu markkinaan (esim. Pinnaclen
                # linja 21.5, ALLE suosikkina), keskiarvo saattoi näyttää
                # LUVUN LINJAN YLÄPUOLELLA vaikka malli oli juuri sovitettu
                # osoittamaan ALLE:n olevan todennäköisempi - ristiriitainen
                # näky vaikka matematiikka oli oikein. Mediaani ei kärsi
                # tästä ja vastaa suoraan sitä mistä puolesta linjaa suurin
                # osa todennäköisyysmassasta on.
                'rounds': ctx['median_rounds'],
                'anchored': total_line > 0,
                # Kuinka monta kierrosta kumpikin joukkue simulaatiossa keskimäärin
                # voittaa - suoraan samasta kierros-kierrokselta-simulaatiosta josta
                # tappoprojektiotkin lasketaan (share_t1/share_t2 kertaa kierrokset).
                't1_rounds_won': float((ctx['rounds'] * ctx['share_t1']).mean()),
                't2_rounds_won': float((ctx['rounds'] * ctx['share_t2']).mean()),
                't1_data': t1_sim, 't2_data': t2_sim,
            }

    # Tulosten näyttäminen
    if 'sim_results' in st.session_state:
        res = st.session_state['sim_results']
        st.write("---")
        _lahde = "Pinnaclen totalista" if res.get('anchored') else "mallin oma arvio"
        st.markdown(
            f"### **{res['t1']} vs {res['t2']}** — Arvioitu kesto: **{res['rounds']:.1f}** kierrosta "
            f"<span style='font-size:0.6em;opacity:0.6'>({_lahde})</span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"*{res['t1']} ({res['p1']*100:.1f}%) | {res['t2']} ({res['p2']*100:.1f}%)*")
        # HUOM: tämä on KESKIARVO (ei mediaani, kuten otsikon kesto) ja voi siksi
        # summautua eri lukuun kuin otsikon kierrosmäärä - molemmat ovat oikein,
        # vinossa jakaumassa mediaani ja keskiarvo vain eroavat toisistaan
        # (ks. otsikon yllä oleva kommentti tästä samasta ilmiöstä).
        st.caption(
            f"Simuloitu kierrosjako (keskiarvo): {res['t1']} {res['t1_rounds_won']:.1f} — "
            f"{res['t2']} {res['t2_rounds_won']:.1f}"
        )

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
        c1, c2, c3 = st.columns(3)
        valittu_pelaaja = c1.selectbox("Pelaaja:", player_names, key="bet_player")
        suunta = c2.radio("Suunta:", ["OVER", "UNDER"], horizontal=True, key="bet_direction")
        custom_line = c3.number_input("Raja (esim 14.5)", value=14.5, step=0.5, key="bet_line")
        st.caption(f"🗺️ Kartta: **{valittu_kartta}** (valitaan yllä joukkuevalinnan yhteydessä)")

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

            # Kelly-panossuositus. Täysi Kelly on käytännössä liian aggressiivinen,
            # koska mallin todennäköisyys on itsekin arvio - neljäsosa-Kelly on
            # tavallinen kompromissi. Jos suositus on negatiivinen, vetoa ei ole.
            b = kerroin - 1.0
            kelly = (model_p * b - (1 - model_p)) / b if b > 0 else 0.0
            if kelly > 0:
                st.caption(
                    f"Kelly: täysi {kelly*100:.1f} % kassasta, "
                    f"**neljäsosa-Kelly {kelly*25:.2f} %** (1000 € kassalla {kelly*250:.2f} €). "
                    f"Mallin EV {(model_p*kerroin-1)*100:+.1f} %."
                )
            else:
                st.caption("Kelly: ei panosta — mallin mukaan negatiivinen odotusarvo.")

        if st.button("Tallenna veto", type="primary"):
            desc = f"{valittu_pelaaja} {suunta} {custom_line} ({res['t1']} vs {res['t2']}, {valittu_kartta})"
            save_bet("Tapot", desc, panos, kerroin, valittu_turnaus, valittu_kartta,
                     model_prob=(model_p if model_odds else None),
                     player=valittu_pelaaja, direction=suunta, line=custom_line,
                     team1=res['t1'], team2=res['t2'])

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

        # 2. MUOKKAA AKTIIVISIA (VIREILLÄ OLEVIA) VETOJA
        # UUSI: aiemmin vain ratkaistuja vetoja pystyi muokkaamaan jälkikäteen -
        # vireillä olevan vedon ainoa muokkaus oli sen ratkaiseminen yllä. Tässä
        # voi korjata KAIKKI kirjausvaiheen tiedot (turnaus, kartta, pelaaja,
        # suunta, raja, panos, kerroin, joukkueet) ennen kuin veto ratkeaa -
        # esim. jos näppäiliit väärän linjan tai valitsit väärän turnauksen.
        st.subheader("Muokkaa aktiivisia vetoja")
        active_bets_for_edit = bets_df[bets_df['status'] == 'Odottaa']
        # HUOM: EI käytetä tab1:n "active_tournaments"-muuttujaa - se määritellään
        # siellä vain jos käyttäjä on jo ajanut simulaation tällä istunnolla
        # (session_state['sim_results']), joten se voi puuttua kokonaan kun tätä
        # välilehteä käytetään. tournaments_df on sen sijaan ladattu heti tab2:n
        # alussa (rivi 330) ja on siis aina käytettävissä täällä.
        edit_active_tournaments = tournaments_df[tournaments_df['status'] == 'Aktiivinen']['name'].tolist()

        if not active_bets_for_edit.empty:
            with st.expander("✏️ Etsi ja muokkaa vireillä olevaa vetoa", expanded=False):
                # HUOM: avaimessa mukana vedon ID, ettei kaksi muuten identtisen
                # näköistä vetoa (sama pelaaja/linja/päivä) sekoitu keskenään
                # valikossa.
                active_bet_dict = {
                    f"[{row['tournament']}] {row['date'][:10]} | {row['description']} (ID: {row['id']})": row
                    for _, row in active_bets_for_edit.iterrows()
                }

                selected_active_key = st.selectbox(
                    "Valitse veto:", list(active_bet_dict.keys()), index=None,
                    placeholder="Etsi muokattava veto...", key="active_bet_select"
                )

                if selected_active_key:
                    abet = active_bet_dict[selected_active_key]

                    with st.form(f"edit_active_form_{abet['id']}"):
                        st.caption(f"Muokataan vireillä olevaa vetoa (ID: {abet['id']})")

                        # HUOM: ennen tätä ominaisuutta tallennetuissa vedoissa
                        # player/direction/line/team1/team2 -sarakkeet voivat olla
                        # tyhjiä (NULL), koska ne olivat vain osa description-
                        # tekstiä. Käytetään silloin varovaisia oletusarvoja -
                        # tallennus täyttää sarakkeet oikein tästä eteenpäin.
                        cur_player = abet['player'] if pd.notna(abet.get('player')) else ""
                        cur_direction = abet['direction'] if pd.notna(abet.get('direction')) else "OVER"
                        cur_line = float(abet['line']) if pd.notna(abet.get('line')) else 14.5
                        cur_team1 = abet['team1'] if pd.notna(abet.get('team1')) else ""
                        cur_team2 = abet['team2'] if pd.notna(abet.get('team2')) else ""

                        row1c1, row1c2, row1c3 = st.columns(3)
                        new_a_tournament = row1c1.selectbox(
                            "Turnaus", edit_active_tournaments,
                            index=(edit_active_tournaments.index(abet['tournament'])
                                   if abet['tournament'] in edit_active_tournaments else 0),
                        )
                        map_options = [f"Kartta {i}" for i in range(1, 6)]
                        new_a_map = row1c2.selectbox(
                            "Kartta", map_options,
                            index=(map_options.index(abet['map']) if abet['map'] in map_options else 0),
                        )
                        new_a_player = row1c3.text_input("Pelaaja", value=cur_player)

                        row2c1, row2c2, row2c3 = st.columns(3)
                        new_a_direction = row2c1.radio(
                            "Suunta", ["OVER", "UNDER"],
                            index=(0 if cur_direction == "OVER" else 1), horizontal=True,
                        )
                        new_a_line = row2c2.number_input("Raja (esim 14.5)", value=cur_line, step=0.5)
                        new_a_stake = row2c3.number_input(
                            "Panos (€)", min_value=1.0, value=float(abet['stake']), step=1.0
                        )

                        row3c1, row3c2, row3c3 = st.columns(3)
                        new_a_odds = row3c1.number_input(
                            "Kerroin", min_value=1.01, value=float(abet['odds']), step=0.01
                        )
                        new_a_team1 = row3c2.text_input("Joukkue 1", value=cur_team1)
                        new_a_team2 = row3c3.text_input("Joukkue 2", value=cur_team2)

                        btn_a1, btn_a2 = st.columns(2)
                        save_active_clicked = btn_a1.form_submit_button("Tallenna muutokset", type="primary")
                        delete_active_clicked = btn_a2.form_submit_button("🗑️ Poista veto lopullisesti")

                        if save_active_clicked:
                            # description rakennetaan uudelleen samalla kaavalla kuin
                            # "Kirjaa veto" -lomakkeessa, jotta se pysyy yhtenäisenä
                            # muualla dashboardissa (haku, listaukset) käytetyn tekstin
                            # kanssa.
                            new_desc = (
                                f"{new_a_player} {new_a_direction} {new_a_line} "
                                f"({new_a_team1} vs {new_a_team2}, {new_a_map})"
                            )
                            cur = conn.cursor()
                            cur.execute(
                                """UPDATE bets SET tournament = ?, map = ?, player = ?, direction = ?,
                                       line = ?, stake = ?, odds = ?, team1 = ?, team2 = ?, description = ?
                                   WHERE id = ?""",
                                (new_a_tournament, new_a_map, new_a_player, new_a_direction, new_a_line,
                                 new_a_stake, new_a_odds, new_a_team1, new_a_team2, new_desc, abet['id']),
                            )
                            conn.commit()
                            st.success("Aktiivinen veto päivitetty!")
                            st.rerun()

                        if delete_active_clicked:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM bets WHERE id = ?", (abet['id'],))
                            conn.commit()
                            st.warning("Veto poistettu!")
                            st.rerun()

        st.write("---")

        # 3. MUOKKAA TAI POISTA RATKAISTUJA VETOJA
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

        # 4. LUO UUSI TURNAUS
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

        # 5. SULJE TURNAUS
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
        st.subheader("🎯 Mallin kalibrointi")
        st.caption(
            "Osuuko malli oikeaan? Jos mallin mukaan 60 %:n vedoista pitäisi voittaa 60 %, "
            "toteutuneen osumaprosentin pitää olla lähellä sitä. Tämä kertoo mallin kunnon "
            "kymmenesosalla siitä otoksesta jonka ROI vaatisi. Vaatii että vedot on tallennettu "
            "mallin arvion kanssa (uudet vedot tallentuvat automaattisesti)."
        )
        cal = bets_df[bets_df['status'].isin(['Voitto', 'Tappio'])].copy() if not bets_df.empty else pd.DataFrame()
        if not cal.empty and 'model_prob' in cal.columns:
            cal = cal[cal['model_prob'].notna()]
        if not cal.empty:
            cal['osui'] = (cal['status'] == 'Voitto').astype(int)
            cal['kori'] = pd.cut(cal['model_prob'], [0, .45, .5, .55, .6, .7, 1.0])
            summary = cal.groupby('kori', observed=True).agg(
                vetoja=('osui', 'size'),
                mallin_arvio=('model_prob', lambda x: f"{x.mean()*100:.1f} %"),
                toteutunut=('osui', lambda x: f"{x.mean()*100:.1f} %"),
            ).reset_index()
            st.dataframe(summary, width='stretch', hide_index=True)
            st.caption(f"Yhteensä {len(cal)} ratkaistua vetoa mallin arvion kanssa. "
                       "Alle ~50 vedolla luvut heiluvat vielä paljon.")
        else:
            st.info("Ei vielä ratkaistuja vetoja joissa mallin arvio olisi tallessa.")

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
