import streamlit as st
import sqlite3
import json
import pandas as pd
import numpy as np
from datetime import datetime

from players_props import (
    get_team_players_overall_stats,
    simulate_team_kills,
    kills_probability_at_line,
    devig_two_way,
    get_team_kills_allowed,
    kpr_corrections,
    MAP_NAMES,
    MAP_KILL_FACTORS,
    OPP_ALLOWED_LEAGUE,
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
    for col, ddl in [("model_prob", "REAL"), ("opp_odds", "REAL"), ("map_pool", "TEXT")]:
        try:
            c.execute(f"ALTER TABLE bets ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass

    # UUSI: opp_odds = vastakkaisen puolen kerroin vedon hetkellä (esim. UNDER-
    # vedolla saman linjan OVER-kerroin). Siitä saa marginaalin poistetun
    # markkinatodennäköisyyden (devig_two_way) ja voi verrata ennustaako malli
    # tuloksia paremmin kuin markkina itse. Korvaa aiemman closing_odds-sarakkeen,
    # joka ei sovi kartta-/pelaajamarkkinoihin (linja liikkuu tai katoaa ennen
    # kuin sulkeutumiskertoimen ehtii kirjata) eikä koskaan ollut käytössä.
    #
    # Kertamigraatio: poistetaan tyhjä closing_odds-sarake. Idempotentti -
    # ajetaan vain jos sarake vielä on. DROP COLUMN vaatii SQLite >= 3.35; jos
    # versio on vanhempi, sarake jää rauhaan (ei haittaa, sitä ei enää käytetä).
    existing_cols = [r[1] for r in c.execute("PRAGMA table_info(bets)").fetchall()]
    if "closing_odds" in existing_cols:
        try:
            c.execute("ALTER TABLE bets DROP COLUMN closing_odds")
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

    # UUSI: cashout-summa. Kun veto ratkaistaan cashoutilla kesken ottelun
    # (status = 'Cashout'), tähän tallennetaan mikä summa siitä saatiin -
    # tuotto lasketaan sitten (saatu summa - panos), ei alkuperäisen
    # kertoimen mukaan, koska cashout-hinta on eri kuin lähtökerroin.
    try:
        c.execute("ALTER TABLE bets ADD COLUMN cashout_amount REAL")
    except sqlite3.OperationalError:
        pass

    # UUSI: Luodaan turnaustaulu
    c.execute('''CREATE TABLE IF NOT EXISTS tournaments
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, status TEXT)''')

    # UUSI: ei enää luoda "Yleinen"-oletusturnausta automaattisesti tyhjään
    # kantaan - turnaus on PAKKO valita (ks. tab1: vedon voi tallentaa vain
    # jos aktiivinen turnaus on valittuna), joten yleistä "roskakori"-turnausta
    # ei enää tarvita uusille vedoille.
    #
    # Kertamigraatio: jos "Yleinen" on jo olemassa VANHASTA (ennen tätä
    # muutosta kirjatuista vedoista), suljetaan se automaattisesti niin ettei
    # se enää näy valittavana turnauksena uusille vedoille. Idempotentti -
    # ei haittaa vaikka tämä ajetaan joka käynnistyksellä. Sille jo merkityt
    # vanhat vedot säilyvät ennallaan ja ovat siirrettävissä oikeaan
    # turnaukseen "Muokkaa tai poista ratkaistuja vetoja" -osiosta, ja koko
    # turnauksen voi poistaa pysyvästi "Poista turnaus kokonaan" -napista
    # sen jälkeen kun sille ei ole enää yhtään vetoa merkittynä.
    c.execute(
        "UPDATE tournaments SET status = 'Suljettu' WHERE name = 'Yleinen' AND status = 'Aktiivinen'"
    )

    conn.commit()
    conn.close()

# UUSI: save_bet ottaa nyt vastaan myös turnauksen nimen JA kartan, sekä (jos
# saatavilla) rakenteelliset pelaaja/suunta/raja/joukkue-kentät myöhempää
# muokkausta varten - ks. init_betting_db():n kommentti näistä sarakkeista.
def save_bet(bet_type, description, stake, odds, tournament, map_name, model_prob=None,
             player=None, direction=None, line=None, team1=None, team2=None, bet_date=None,
             opp_odds=None, map_pool=None):
    # UUSI: bet_date antaa kutsujan vahvistaa itse minkä päivän veto on (oletuksena
    # tämä päivä, mutta jälkikäteen kirjattavalle vedolle voi valita toisen päivän -
    # ks. tab1:n "Turnaus ja päivämäärä" -lohko). Jos sitä ei anneta, käyttäydytään
    # kuten ennen (nykyhetki).
    date_str = bet_date or datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect('my_bets.db')
    c = conn.cursor()
    c.execute("""INSERT INTO bets (date, type, description, stake, odds, status, tournament, map,
                                    model_prob, player, direction, line, team1, team2, opp_odds, map_pool)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (date_str, bet_type, description, stake, odds, "Odottaa", tournament, map_name,
               model_prob, player, direction, line, team1, team2, opp_odds, map_pool))
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

def bet_profit(row):
    """Yhden vedon tuotto.

    Voitto/Tappio: panos x (kerroin - 1) voitosta, -panos tappiosta.
    Cashout: saatu summa miinus alkuperäinen panos - eri kertoimella kuin
    lähtökerroin, koska cashout-hinta tarjotaan kesken ottelun eikä vastaa
    enää alkuperäistä kerrointa. Voi olla plus- tai miinusmerkkinen riippuen
    siitä ehditäänkö kassaan ulos ennen vai jälkeen sen kun veto olisi
    muutenkin kääntynyt voitolliseksi/tappiolliseksi.
    """
    status = row['status']
    stake = row['stake']
    if status == 'Voitto':
        return stake * (row['odds'] - 1)
    if status == 'Tappio':
        return -stake
    if status == 'Cashout':
        cashout = row.get('cashout_amount')
        cashout = float(cashout) if pd.notna(cashout) else 0.0
        return cashout - stake
    return 0.0

def show_bet_summary(df):
    """Näyttää yhteenvedon (ROI %, vetojen määrä, tulos €, vireillä olevat) annetusta
    vetojoukosta. ROI lasketaan vain RATKAISTUISTA vedoista (Voitto/Tappio/Cashout),
    koska odottavien vetojen panos ei vielä ole tuottanut mitään tulosta - sen mukaan
    ottaminen nimittäjään vääristäisi lukua turhaan huonompaan suuntaan."""
    total_bets = len(df)
    settled = df[df['status'].isin(['Voitto', 'Tappio', 'Cashout'])]
    pending = df[df['status'] == 'Odottaa']
    total_staked_settled = settled['stake'].sum()

    profit_per_row = df.apply(bet_profit, axis=1)
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

    # ==========================================
    # UUSI: PINNACLEN KERTOIMIEN LIITTÄMINEN (JSON)
    # ==========================================
    # Sen sijaan että kertoimet, kartan kesto ja Yli/Alle-linja näppäillään
    # käsin kuvasta katsoen, tähän voi liittää Claudelta saadun JSON-pätkän
    # (poimittu suoraan Pinnaclen kerroinkuvasta) ja ladata yhden
    # ottelun/kartan tiedot suoraan alla oleviin kenttiin napin painalluksella.
    # Kaikki alla olevat kentät saavat tästä syystä key=-parametrin, jotta
    # "Lataa kertoimet" -nappi voi asettaa niiden arvot session_statesta.
    with st.expander("📋 Liitä Pinnaclen kertoimet (JSON)", expanded=False):
        st.caption(
            "Liitä tähän JSON, jossa on Pinnaclen kertoimet (joukkueet, "
            "voittajan kertoimet, kartan Yli/Alle-kierrosraja kertoimineen). "
            "Valitse sen jälkeen alta ladattava ottelu/kartta."
        )
        pinnacle_json_text = st.text_area(
            "JSON:", height=150, key="pinnacle_json_text",
            placeholder='{"matches": [{"team1": "...", "team2": "...", "maps": '
                        '[{"map": "Kartta 1", "odds1": 1.85, "odds2": 1.95, '
                        '"total_line": 21.5, "over_odds": 1.9, "under_odds": 1.9}]}]}',
        )

        parsed_matches = []
        if pinnacle_json_text.strip():
            try:
                parsed_matches = json.loads(pinnacle_json_text).get("matches", [])
            except (json.JSONDecodeError, AttributeError) as e:
                st.error(f"JSON ei kelpaa: {e}")

        if pinnacle_json_text.strip() and not parsed_matches:
            st.warning('JSON:sta ei löytynyt yhtään ottelua ("matches"-lista puuttuu tai on tyhjä).')

        if parsed_matches:
            # HUOM: käytetään listaindeksiä avaimena (ei pelkkää tekstiä), koska
            # kaksi eri ottelua saman turnauksen sisällä voisi muuten tuottaa
            # identtisen näköisen valinnan (esim. sama kartta molemmissa).
            load_options = {}
            for mi, m in enumerate(parsed_matches):
                for mpi, mp in enumerate(m.get("maps", [])):
                    label = f"{m.get('team1', '?')} vs {m.get('team2', '?')} - {mp.get('map', '?')}"
                    load_options[f"{label}##{mi}.{mpi}"] = (m, mp)

            selected_load_key = st.selectbox(
                "Ottelu / kartta:", list(load_options.keys()),
                index=None, placeholder="Valitse ladattava kartta...",
                format_func=lambda k: k.split("##")[0],
                key="pinnacle_load_select",
            )

            if st.button("Lataa kertoimet", type="primary") and selected_load_key:
                m, mp = load_options[selected_load_key]
                t1_name, t2_name = m.get("team1", ""), m.get("team2", "")

                missing = [t for t in (t1_name, t2_name) if t not in team_names_list]
                if missing:
                    st.warning(
                        f"⚠️ Joukkuetta/joukkueita ei löytynyt kannasta täsmälleen: "
                        f"**{', '.join(missing)}**. Pinnacle saattaa käyttää eri "
                        f"nimeä kuin kanta (esim. \"BB Team\" vs. \"BetBoom\") - "
                        f"korjaa nimi JSON:iin tai valitse joukkue(et) käsin alta. "
                        f"Muut kentät (kerroin, kartta, total) ladattiin silti."
                    )
                for t_key, t_name in (("team1_select", t1_name), ("team2_select", t2_name)):
                    if t_name in team_names_list:
                        st.session_state[t_key] = t_name

                if mp.get("odds1") is not None:
                    st.session_state["odds1_input"] = float(mp["odds1"])
                if mp.get("odds2") is not None:
                    st.session_state["odds2_input"] = float(mp["odds2"])
                if mp.get("map") in [f"Kartta {i}" for i in range(1, 6)]:
                    st.session_state["selected_map"] = mp["map"]

                # Pinnaclen oma Yli/Alle-kierrosraja on markkinatietoa - parempi
                # arvio kartan kestosta kuin mallin oma, kertoimien vinouteen
                # perustuva arvaus (ks. kommentti alempana "Ankkurointi"-kohdassa).
                total_line_val = mp.get("total_line")
                if total_line_val:
                    st.session_state["total_line_input"] = float(total_line_val)
                    if mp.get("over_odds") is not None:
                        st.session_state["total_over_input"] = float(mp["over_odds"])
                    if mp.get("under_odds") is not None:
                        st.session_state["total_under_input"] = float(mp["under_odds"])

                st.success(f"✅ Kertoimet ladattu: {selected_load_key.split('##')[0]}")
                st.rerun()

    # UUSI: turnauslista haetaan tässä valmiiksi (käytetään alempana sekä
    # varoitusviestissä että itse turnaus+päivämäärä-lohkossa lomakkeen
    # alareunassa - ks. "UUSI: turnaus ja päivämäärä" alla).
    conn_t = sqlite3.connect('my_bets.db')
    active_tournaments = pd.read_sql_query(
        "SELECT name FROM tournaments WHERE status = 'Aktiivinen'", conn_t
    )['name'].tolist()
    conn_t.close()

    # Yläosan valikot ja kertoimet - näitä TÄYTETÄÄN JSON:lla ("Lataa
    # kertoimet" yllä), joten turnaus ja päivämäärä EIVÄT ole tässä joukossa:
    # ne vahvistetaan aina erikseen käsin lomakkeen alareunassa (ks. alla),
    # jotta ne eivät huku automaattisesti täyttyvien kenttien sekaan ja jää
    # vahingossa vaihtamatta edellisestä kerrasta.
    c1, c2, c3, c4, c5, c6 = st.columns([2, 1, 2, 1, 1, 1.3])
    team1 = c1.selectbox("Joukkue 1:", team_names_list, index=None, key="team1_select")
    odds1 = c2.number_input(f"Kerroin (Joukkue 1)", min_value=1.01, value=1.85, step=0.01, key="odds1_input")

    team2 = c3.selectbox("Joukkue 2:", team_names_list, index=None, key="team2_select")
    odds2 = c4.number_input(f"Kerroin (Joukkue 2)", min_value=1.01, value=1.85, step=0.01, key="odds2_input")

    valittu_kartta = c5.selectbox("Kartta:", [f"Kartta {i}" for i in range(1, 6)], key="selected_map")

    # UUSI: kartan NIMI (Inferno, Dust2...). Malli käyttää yleistä karttakerrointa
    # (sama kaikille pelaajille): esim. Infernolla tapot ovat ~2,5 % ja Dust2:lla
    # ~1 % alle keskiarvon, Ancientilla ~1,6 % yli. Vaikuttaa tappoennusteeseen,
    # joten valinta tehdään ENNEN "Laske arviot" -nappia. Valinta nollataan
    # automaattisesti kun joukkueet tai kartan numero vaihtuvat, ettei edellisen
    # ottelun kartta jää vahingossa voimaan.
    NO_MAP = "(ei tiedossa)"
    _map_ctx = (team1, team2, valittu_kartta)
    if st.session_state.get("map_name_ctx") != _map_ctx:
        st.session_state["map_name_select"] = NO_MAP
        st.session_state["map_name_ctx"] = _map_ctx
    valittu_mapname = c6.selectbox("Kartan nimi:", [NO_MAP] + MAP_NAMES, key="map_name_select")
    map_for_model = None if valittu_mapname == NO_MAP else valittu_mapname

    # PINNACLEN KIERROSTOTAL. Markkinan arvio kartan kestosta on parempi kuin
    # historiaan sovitettu malli: siinä on mukana kartta, kokoonpanot, LAN/online
    # ja kaikki mitä kanta ei tunne. Simulaatiota ei silti voi poistaa - totalista
    # saa vain keskikohdan, ja tappotodennäköisyyksiin tarvitaan koko jakauma.
    # Ankkurointi ottaa markkinalta TASON ja jättää mallille MUODON.
    t1c, t2c, t3c = st.columns(3)
    total_line = t1c.number_input("Pinnaclen kierrostotal (0 = ei käytössä)",
                                  min_value=0.0, max_value=40.0, value=0.0, step=0.5, key="total_line_input")
    total_over = t2c.number_input("Kerroin YLI", min_value=0.0, value=0.0, step=0.01, key="total_over_input",
                                  help="Valinnainen. Molemmat kertoimet antamalla marginaali poistetaan ja ankkurointi on tarkin.")
    total_under = t3c.number_input("Kerroin ALLE", min_value=0.0, value=0.0, step=0.01, key="total_under_input")
    if total_line > 0:
        st.caption("📡 Kierrosmäärä kalibroidaan Pinnaclen linjaan (ei mallin omaan arvioon).")

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

            # UUSI: vastustajan "sallitut tapot" (kuinka paljon tappoja joukkue
            # keskimäärin antaa vastustajalleen) + yleinen karttakerroin. Joukkue 1:n
            # pelaajia korjataan Joukkue 2:n sallimilla tapoilla ja päinvastoin.
            opp_allowed_t1, _n1 = get_team_kills_allowed(team2)   # t1:n vastustaja = team2
            opp_allowed_t2, _n2 = get_team_kills_allowed(team1)
            t1_sim = simulate_team_kills(t1_players, ctx['rounds'], ctx['share_t1'],
                                         opp_allowed=opp_allowed_t1, map_name=map_for_model)
            t2_sim = simulate_team_kills(t2_players, ctx['rounds'], ctx['share_t2'],
                                         opp_allowed=opp_allowed_t2, map_name=map_for_model)

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
                'map_name': map_for_model,
                'opp_allowed_t1': opp_allowed_t1, 'opp_allowed_t2': opp_allowed_t2,
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

        # UUSI: näytetään mitkä korjaukset tappoennusteessa ovat käytössä, jotta
        # lukujen taustan näkee (ja näkee jos jokin korjaus puuttuu).
        _adj1, _mf = kpr_corrections(res.get('opp_allowed_t1'), res.get('map_name'))
        _adj2, _ = kpr_corrections(res.get('opp_allowed_t2'), res.get('map_name'))
        _map_txt = (f"**{res['map_name']}** (kerroin ×{_mf:.3f})" if res.get('map_name')
                    else "**ei valittu** (ei karttakorjausta)")

        def _opp_txt(team, val, adj):
            if val is None:
                return f"{team}: ei tarpeeksi dataa (ei korjausta)"
            return f"{team} antaa {val:.2f} tappoa/kierros ({adj*21.6:+.2f} tappoa/kartta vastustajan pelaajille)"

        st.caption(
            f"🧮 Tappoennusteen korjaukset — kartta: {_map_txt} · vastustajan sallimat tapot "
            f"(vertailutaso {OPP_ALLOWED_LEAGUE:.2f}): {_opp_txt(res['t2'], res.get('opp_allowed_t1'), _adj1)}; "
            f"{_opp_txt(res['t1'], res.get('opp_allowed_t2'), _adj2)}"
        )

        # Kootaan hakemisto pelaajan nimi -> raaka simulaatiotaulukko, jotta
        # sitä voidaan käyttää alempana mielivaltaisen (esim. bookkerin) linjan
        # todennäköisyyden laskemiseen.
        all_players_data = res['t1_data'] + res['t2_data']
        sim_kills_lookup = {p['Pelaaja']: p['_sim_kills'] for p in all_players_data}
        # UUSI: mallin oma keskilinja per pelaaja (taulukon 'Mallin linja' -sarake).
        # Käytetään raja-kentän SUUNTAA ANTAVANA oletusarvona, kun pelaaja vaihtuu.
        model_line_lookup = {p['Pelaaja']: p['Mallin linja'] for p in all_players_data}

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

        player_names = list(sim_kills_lookup.keys())

        # HUOM: tämä osio EI ole enää st.form:in sisällä, koska Streamlit-formit
        # eivät päivity reaaliaikaisesti - haluamme näyttää mallin arvion heti
        # kun käyttäjä vaihtaa pelaajaa/linjaa, ennen tallennusta.
        c1, c2, c3 = st.columns(3)
        valittu_pelaaja = c1.selectbox("Pelaaja:", player_names, key="bet_player")
        suunta = c2.radio("Suunta:", ["OVER", "UNDER"], horizontal=True, key="bet_direction")

        # UUSI: kun pelaaja (tai simulaatio) vaihtuu, raja-kenttä asetetaan mallin
        # omaan linjaan lähtöarvoksi, jotta sitä ei tarvitse etsiä käsin. Se on vain
        # SUUNTAA ANTAVA oletus: kenttää voi yhä muuttaa vapaasti (bookkerin oikea
        # linja on usein eri), eikä arvoa ylikirjoiteta uudelleen ennen kuin pelaaja
        # tai simulaatio vaihtuu. Arvo pitää asettaa ENNEN number_input-widgetin
        # luomista, muuten Streamlit ei salli session_staten muuttamista.
        _line_ctx = f"{res['t1']}|{res['t2']}|{valittu_pelaaja}|{res['rounds']:.2f}"
        mallin_linja = model_line_lookup.get(valittu_pelaaja)
        if mallin_linja is not None and st.session_state.get("bet_line_ctx") != _line_ctx:
            st.session_state["bet_line"] = float(mallin_linja)
            st.session_state["bet_line_ctx"] = _line_ctx
        st.session_state.setdefault("bet_line", 14.5)
        custom_line = c3.number_input("Raja (esim 14.5)", step=0.5, key="bet_line")
        st.caption(
            f"🗺️ Kartta: **{valittu_kartta}** · 🎯 Mallin linja: **{mallin_linja}** "
            f"(suuntaa antava lähtöarvo - muokkaa rajaa vapaasti bookkerin linjaan)"
        )

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
        # UUSI: vastakkaisen puolen kerroin samalta linjalta (OVER-vedolla UNDER-
        # kerroin ja päinvastoin). Valinnainen: 0 = ei kirjata. Siitä lasketaan
        # marginaalin poistettu markkinatodennäköisyys vedon hetkellä, jota
        # verrataan malliin ja toteumaan Vetoseurannan "Malli vs markkina" -osiossa.
        vastakerroin_raw = c6.number_input(
            "Vastakkaisen puolen kerroin (0 = ei kirjata)", min_value=0.0, value=0.0, step=0.01,
            key="bet_opp_odds",
            help="Saman linjan toisen puolen kerroin (OVER-vedolla UNDER ja päinvastoin). "
                 "Käytetään markkinan oman arvion laskemiseen marginaali poistettuna.",
        )
        vastakerroin = vastakerroin_raw if vastakerroin_raw > 1.0 else None
        if vastakerroin and model_odds:
            fair_self, _fair_opp, _marg = devig_two_way(kerroin, vastakerroin)
            st.caption(
                f"📏 Markkina (marginaali {_marg:.1f} % poistettu): **{fair_self*100:.1f} %** "
                f"vs. malli **{model_p*100:.1f} %** ({model_p*100 - fair_self*100:+.1f} %-yks.)"
            )

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

        # UUSI: turnaus ja päivämäärä vahvistetaan AINA käsin tässä, aivan
        # lomakkeen alareunassa, ERILLÄÄN kaikista yllä automaattisesti JSON:lla
        # täytettävistä kentistä (joukkueet, kertoimet, kartta, total) - näin ne
        # eivät huku autofill-kenttien sekaan ja jää vahingossa vaihtamatta
        # edellisen vedon arvoista. Turnaus on PAKKO valita - ei enää oletus-/
        # roskakoriturnausta ("Yleinen") johon veto voisi vahingossa jäädä ilman
        # turnausta. Jos aktiivisia turnauksia ei ole yhtään (esim. kaikki
        # suljettu/poistettu), valikko näytetään poissa käytöstä eikä vetoa
        # pääse tallentamaan ennen kuin uusi turnaus on luotu "📈 Vetoseuranta"
        # -välilehdeltä. Päivämäärä on oletuksena tämä päivä, mutta sen voi
        # vaihtaa (esim. jälkikäteen kirjattava veto) - tätä käytetään
        # "Aktiiviset turnaukset" -välilehden päiväkohtaisissa yhteenvetokorteissa.
        st.write("---")
        st.caption("🏆 Turnaus ja 📅 päivämäärä — vahvista aina itse:")
        tc1, tc2 = st.columns(2)
        if active_tournaments:
            valittu_turnaus = tc1.selectbox("Turnaus:", active_tournaments, key="bet_tournament")
        else:
            valittu_turnaus = None
            tc1.selectbox("Turnaus:", ["⚠️ Luo turnaus ensin"], disabled=True)
        valittu_pvm = tc2.date_input("Päivämäärä:", value=datetime.now().date(), key="bet_date")

        if not active_tournaments:
            st.warning(
                "⚠️ Ei yhtään aktiivista turnausta - vetoa ei voi tallentaa ennen kuin "
                "olet luonut turnauksen '📈 Vetoseuranta' -välilehden 'Luo uusi turnaus' "
                "-osiosta."
            )

        if st.button("Tallenna veto", type="primary"):
            if not valittu_turnaus:
                st.error(
                    "⚠️ Turnaus on pakko valita ennen vedon tallentamista - luo "
                    "turnaus '📈 Vetoseuranta' -välilehdeltä ja yritä sen jälkeen "
                    "uudelleen."
                )
            else:
                desc = f"{valittu_pelaaja} {suunta} {custom_line} ({res['t1']} vs {res['t2']}, {valittu_kartta})"
                # Jos valittu päivämäärä on tämä päivä, kirjataan myös tarkka
                # kellonaika (kuten ennenkin) - muuten (jälkikäteen kirjattava
                # veto) kellonajaksi laitetaan kiinteä 12:00, koska tarkkaa
                # kellonaikaa ei silloin tiedetä.
                if valittu_pvm == datetime.now().date():
                    bet_date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                else:
                    bet_date_str = valittu_pvm.strftime("%Y-%m-%d") + " 12:00"
                save_bet("Tapot", desc, panos, kerroin, valittu_turnaus, valittu_kartta,
                         model_prob=(model_p if model_odds else None),
                         player=valittu_pelaaja, direction=suunta, line=custom_line,
                         team1=res['t1'], team2=res['t2'], bet_date=bet_date_str,
                         opp_odds=vastakerroin, map_pool=res.get('map_name'))

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
    subtab1, subtab2, subtab3, subtab4 = st.tabs(
        ["⚙️ Hallinta & Ratkaisu", "📊 Aktiiviset Turnaukset", "📚 Turnaushistoria & Kaikki", "🎮 Pelaajat"]
    )

    with subtab1:
        # 1+2. AKTIIVISET VEDOT: pikaratkaisu ja muokkaus samassa kortissa
        # UUSI: aiemmin nämä olivat kaksi erillistä osiota - yksi pelkkää
        # ratkaisua varten (Voitto/Tappio-radio), toinen erillinen pudotus-
        # valikollinen "Muokkaa aktiivisia vetoja" -osio kaikkien kenttien
        # muokkaukseen. Nyt jokainen vireillä oleva veto näkyy omana korttinaan:
        # pikanapit Voitto/Tappio suoraan kortissa, ja "✏️ Muokkaa"-avattavasta
        # löytyy kaikkien kenttien muokkaus, poisto sekä cashout samasta
        # paikasta - ei tarvitse etsiä vetoa uudelleen toisesta pudotusvalikosta.
        st.subheader("Aktiiviset vedot")

        edit_active_tournaments = tournaments_df[tournaments_df['status'] == 'Aktiivinen']['name'].tolist()

        active_bets_for_edit = bets_df[bets_df['status'] == 'Odottaa']
        if search_query:
            active_bets_for_edit = filter_bets(active_bets_for_edit, search_query)

        if active_bets_for_edit.empty:
            if search_query:
                st.caption(f"Ei aktiivisia vetoja haulla \"{search_query}\".")
            else:
                st.caption("Ei aktiivisia vetoja.")
        else:
            for _, row in active_bets_for_edit.iterrows():
                bid = row['id']
                with st.container(border=True):
                    st.markdown(
                        f"**[{row['tournament']}]** {row['date'][:10]} | {row['description']}  \n"
                        f"Panos: {row['stake']:.2f}€ @ {row['odds']:.2f}"
                    )

                    qc1, qc2, qc3 = st.columns([1, 1, 4])
                    if qc1.button("✅ Voitto", key=f"win_{bid}"):
                        cur = conn.cursor()
                        cur.execute("UPDATE bets SET status = ? WHERE id = ?", ("Voitto", bid))
                        conn.commit()
                        st.rerun()
                    if qc2.button("❌ Tappio", key=f"lose_{bid}"):
                        cur = conn.cursor()
                        cur.execute("UPDATE bets SET status = ? WHERE id = ?", ("Tappio", bid))
                        conn.commit()
                        st.rerun()

                    with st.expander("✏️ Muokkaa"):
                        # HUOM: ennen tätä ominaisuutta tallennetuissa vedoissa
                        # player/direction/line/team1/team2 -sarakkeet voivat olla
                        # tyhjiä (NULL), koska ne olivat vain osa description-
                        # tekstiä. Käytetään silloin varovaisia oletusarvoja -
                        # tallennus täyttää sarakkeet oikein tästä eteenpäin.
                        cur_player = row['player'] if pd.notna(row.get('player')) else ""
                        cur_direction = row['direction'] if pd.notna(row.get('direction')) else "OVER"
                        cur_line = float(row['line']) if pd.notna(row.get('line')) else 14.5
                        cur_team1 = row['team1'] if pd.notna(row.get('team1')) else ""
                        cur_team2 = row['team2'] if pd.notna(row.get('team2')) else ""
                        # UUSI: myös päivämäärää voi nyt muokata jälkikäteen (esim. jos
                        # veto ehdittiin kirjata vasta seuraavana päivänä). Kellonaika
                        # säilytetään alkuperäisenä - vain päivä vaihtuu - koska tarkkaa
                        # uutta kellonaikaa ei yleensä tiedetä jälkikäteen.
                        cur_date = pd.to_datetime(row['date']).date() if pd.notna(row.get('date')) else datetime.now().date()
                        cur_time_str = str(row['date'])[11:16] if pd.notna(row.get('date')) and len(str(row['date'])) >= 16 else "12:00"

                        with st.form(f"edit_active_form_{bid}"):
                            row1c1, row1c2, row1c3, row1c4 = st.columns(4)
                            new_a_tournament = row1c1.selectbox(
                                "Turnaus", edit_active_tournaments,
                                index=(edit_active_tournaments.index(row['tournament'])
                                       if row['tournament'] in edit_active_tournaments else 0),
                                key=f"a_tournament_{bid}",
                            )
                            map_options = [f"Kartta {i}" for i in range(1, 6)]
                            new_a_map = row1c2.selectbox(
                                "Kartta", map_options,
                                index=(map_options.index(row['map']) if row['map'] in map_options else 0),
                                key=f"a_map_{bid}",
                            )
                            new_a_player = row1c3.text_input("Pelaaja", value=cur_player, key=f"a_player_{bid}")
                            new_a_date = row1c4.date_input("Päivämäärä", value=cur_date, key=f"a_date_{bid}")

                            row2c1, row2c2, row2c3 = st.columns(3)
                            new_a_direction = row2c1.radio(
                                "Suunta", ["OVER", "UNDER"],
                                index=(0 if cur_direction == "OVER" else 1), horizontal=True,
                                key=f"a_direction_{bid}",
                            )
                            new_a_line = row2c2.number_input("Raja (esim 14.5)", value=cur_line, step=0.5,
                                                              key=f"a_line_{bid}")
                            new_a_stake = row2c3.number_input(
                                "Panos (€)", min_value=1.0, value=float(row['stake']), step=1.0,
                                key=f"a_stake_{bid}",
                            )

                            row3c1, row3c2, row3c3 = st.columns(3)
                            new_a_odds = row3c1.number_input(
                                "Kerroin", min_value=1.01, value=float(row['odds']), step=0.01,
                                key=f"a_odds_{bid}",
                            )
                            new_a_team1 = row3c2.text_input("Joukkue 1", value=cur_team1, key=f"a_team1_{bid}")
                            new_a_team2 = row3c3.text_input("Joukkue 2", value=cur_team2, key=f"a_team2_{bid}")

                            cur_opp = float(row['opp_odds']) if pd.notna(row.get('opp_odds')) else 0.0
                            oc1, oc2 = st.columns(2)
                            new_a_opp_raw = oc1.number_input(
                                "Vastakkaisen puolen kerroin (0 = ei kirjattu)", min_value=0.0,
                                value=cur_opp, step=0.01, key=f"a_opp_{bid}",
                            )
                            _mp_opts = ["(ei tiedossa)"] + MAP_NAMES
                            _cur_mp = row['map_pool'] if pd.notna(row.get('map_pool')) and row['map_pool'] in MAP_NAMES else "(ei tiedossa)"
                            new_a_mappool_sel = oc2.selectbox(
                                "Kartan nimi", _mp_opts, index=_mp_opts.index(_cur_mp), key=f"a_mappool_{bid}",
                            )

                            st.caption(
                                "💰 Cashout: syötä alle summa jonka bookkeri tarjoaa vedon "
                                "sulkemisesta kesken ottelun, ja paina Cashout. Veto ratkeaa "
                                "tuloksella (saatu summa − panos)."
                            )
                            cashout_amount = st.number_input(
                                "Cashoutissa saatu summa (€)", min_value=0.0, value=0.0, step=1.0,
                                key=f"a_cashout_{bid}",
                            )

                            btn_a1, btn_a2, btn_a3 = st.columns(3)
                            save_active_clicked = btn_a1.form_submit_button("💾 Tallenna", type="primary")
                            cashout_clicked = btn_a2.form_submit_button("💰 Cashout")
                            delete_active_clicked = btn_a3.form_submit_button("🗑️ Poista")

                            # description rakennetaan uudelleen samalla kaavalla kuin
                            # "Kirjaa veto" -lomakkeessa, jotta se pysyy yhtenäisenä
                            # muualla dashboardissa (haku, listaukset) käytetyn tekstin
                            # kanssa.
                            new_desc = (
                                f"{new_a_player} {new_a_direction} {new_a_line} "
                                f"({new_a_team1} vs {new_a_team2}, {new_a_map})"
                            )
                            new_a_date_str = new_a_date.strftime("%Y-%m-%d") + " " + cur_time_str
                            new_a_opp = new_a_opp_raw if new_a_opp_raw > 1.0 else None
                            new_a_mappool = None if new_a_mappool_sel == "(ei tiedossa)" else new_a_mappool_sel

                            if save_active_clicked:
                                cur = conn.cursor()
                                cur.execute(
                                    """UPDATE bets SET tournament = ?, map = ?, player = ?, direction = ?,
                                           line = ?, stake = ?, odds = ?, team1 = ?, team2 = ?, description = ?,
                                           date = ?, opp_odds = ?, map_pool = ?
                                       WHERE id = ?""",
                                    (new_a_tournament, new_a_map, new_a_player, new_a_direction, new_a_line,
                                     new_a_stake, new_a_odds, new_a_team1, new_a_team2, new_desc,
                                     new_a_date_str, new_a_opp, new_a_mappool, bid),
                                )
                                conn.commit()
                                st.success("Veto päivitetty!")
                                st.rerun()

                            if cashout_clicked:
                                cur = conn.cursor()
                                cur.execute(
                                    """UPDATE bets SET tournament = ?, map = ?, player = ?, direction = ?,
                                           line = ?, stake = ?, odds = ?, team1 = ?, team2 = ?, description = ?,
                                           date = ?, opp_odds = ?, map_pool = ?, status = ?, cashout_amount = ?
                                       WHERE id = ?""",
                                    (new_a_tournament, new_a_map, new_a_player, new_a_direction, new_a_line,
                                     new_a_stake, new_a_odds, new_a_team1, new_a_team2, new_desc,
                                     new_a_date_str, new_a_opp, new_a_mappool, "Cashout", cashout_amount, bid),
                                )
                                conn.commit()
                                st.success(
                                    f"Cashoutattu {cashout_amount:.2f}€ "
                                    f"(tuotto {cashout_amount - new_a_stake:+.2f}€)."
                                )
                                st.rerun()

                            if delete_active_clicked:
                                cur = conn.cursor()
                                cur.execute("DELETE FROM bets WHERE id = ?", (bid,))
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

                        # UUSI: turnausta voi muokata myös jälkikäteen, ratkaistun
                        # vedon kohdalla - esim. kun turnaus unohtui merkitä
                        # oikein vedon kirjaushetkellä ja veto jäi "Yleinen"-
                        # turnauksen alle. Lista sisältää KAIKKI turnaukset
                        # (myös suljetut), koska veto voi kuulua jo historiaan
                        # siirrettyyn turnaukseen.
                        all_tournament_names = tournaments_df['name'].tolist()
                        c0, c1, c2 = st.columns(3)
                        new_tournament = c0.selectbox(
                            "Turnaus", all_tournament_names,
                            index=(all_tournament_names.index(bet['tournament'])
                                   if bet['tournament'] in all_tournament_names else 0),
                        )
                        new_stake = c1.number_input("Panos (€)", min_value=1.0, value=float(bet['stake']), step=1.0)
                        new_odds = c2.number_input("Kerroin", min_value=1.01, value=float(bet['odds']), step=0.01)

                        # UUSI: myös ratkaistun vedon päivämäärää voi muokata jälkikäteen
                        # (esim. veto kirjattiin vasta seuraavana päivänä eikä sitä
                        # huomattu silloin korjata). Kellonaika säilyy alkuperäisenä.
                        cur_r_date = pd.to_datetime(bet['date']).date() if pd.notna(bet.get('date')) else datetime.now().date()
                        cur_r_time_str = str(bet['date'])[11:16] if pd.notna(bet.get('date')) and len(str(bet['date'])) >= 16 else "12:00"
                        rd1, rd2 = st.columns(2)
                        new_r_date = rd1.date_input("Päivämäärä", value=cur_r_date, key=f"r_date_{bet['id']}")
                        cur_r_opp = float(bet['opp_odds']) if pd.notna(bet.get('opp_odds')) else 0.0
                        new_r_opp_raw = rd2.number_input(
                            "Vastakkaisen puolen kerroin (0 = ei kirjattu)", min_value=0.0,
                            value=cur_r_opp, step=0.01, key=f"r_opp_{bet['id']}",
                        )

                        c3, c4 = st.columns(2)
                        status_options = ["Voitto", "Tappio", "Cashout", "Odottaa"]
                        current_status_idx = status_options.index(bet['status']) if bet['status'] in status_options else 0
                        new_status = c3.selectbox("Tulos", status_options, index=current_status_idx)

                        cur_cashout = float(bet['cashout_amount']) if pd.notna(bet.get('cashout_amount')) else 0.0
                        new_cashout_amount = c4.number_input(
                            "Cashoutissa saatu summa (€) — koskee vain jos Tulos = Cashout",
                            min_value=0.0, value=cur_cashout, step=1.0,
                        )

                        btn1, btn2 = st.columns(2)
                        save_clicked = btn1.form_submit_button("Tallenna muutokset", type="primary")
                        delete_clicked = btn2.form_submit_button("🗑️ Poista veto lopullisesti")

                        if save_clicked:
                            new_r_date_str = new_r_date.strftime("%Y-%m-%d") + " " + cur_r_time_str
                            new_r_opp = new_r_opp_raw if new_r_opp_raw > 1.0 else None
                            cur = conn.cursor()
                            cur.execute(
                                "UPDATE bets SET tournament = ?, stake = ?, odds = ?, status = ?, cashout_amount = ?, date = ?, opp_odds = ? WHERE id = ?",
                                (new_tournament, new_stake, new_odds, new_status, new_cashout_amount, new_r_date_str, new_r_opp, bet['id']))
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

        st.write("---")

        # 6. UUSI: POISTA TURNAUS KOKONAAN
        # Toisin kuin "Sulje", tämä poistaa turnaus-rivin pysyvästi - tarkoitettu
        # esim. oletusturnaukselle "Yleinen", jota ei enää tarvita sen jälkeen
        # kun turnaus valitaan aina heti joukkuevalinnan yhteydessä (ei siis pitäisi
        # jäädä unohdetuksi). Estetty jos turnaukseen on yhä merkitty vetoja -
        # muuten niiden turnaus-tieto jäisi orvoksi/näkymättömäksi. Siirrä vedot
        # ensin oikeaan turnaukseen yllä olevasta "Muokkaa tai poista ratkaistuja
        # vetoja" -osiosta (tai aktiivisen vedon ✏️ Muokkaa-napista), niin poisto
        # onnistuu.
        st.subheader("Poista turnaus kokonaan")
        all_tournament_names_del = tournaments_df['name'].tolist()
        if all_tournament_names_del:
            del_t = st.selectbox("Valitse poistettava turnaus", all_tournament_names_del,
                                  key="del_tournament_select")
            bets_in_t = int((bets_df['tournament'] == del_t).sum())
            if bets_in_t > 0:
                st.warning(
                    f"⚠️ Turnaukseen '{del_t}' on merkitty {bets_in_t} vetoa - niitä ei voi "
                    f"poistaa turnauksen mukana. Siirrä ne ensin oikeaan turnaukseen "
                    f"yllä olevasta 'Muokkaa tai poista ratkaistuja vetoja' -osiosta "
                    f"(tai aktiivisen vedon ✏️ Muokkaa-napista), niin poisto onnistuu."
                )
            else:
                if st.button(f"🗑️ Poista '{del_t}' pysyvästi"):
                    c = conn.cursor()
                    c.execute("DELETE FROM tournaments WHERE name = ?", (del_t,))
                    conn.commit()
                    st.success(f"Turnaus '{del_t}' poistettu.")
                    st.rerun()

    with subtab2:
        st.subheader("Aktiivisten turnausten seuranta")
        if active_list:
            view_t = st.selectbox("Näytä data turnaukselle:", active_list, key="view_active")
            t_bets = bets_df[bets_df['tournament'] == view_t].copy()

            if not t_bets.empty:
                # Laske kassan kehitys kronologisessa järjestyksessä
                t_bets = t_bets.sort_values('id')
                t_bets['Tuotto'] = t_bets.apply(bet_profit, axis=1)
                t_bets['Kassa (€)'] = t_bets['Tuotto'].cumsum()

                # UUSI: ROI %, vetojen määrä ja tulos euroina graafin yläpuolella
                show_bet_summary(t_bets)

                # Piirretään graafi kassan kehityksestä
                st.line_chart(t_bets['Kassa (€)'].reset_index(drop=True))

                # Näytetään taulukko (piilotetaan laskennalliset apusarakkeet)
                st.dataframe(t_bets.drop(columns=['Tuotto', 'Kassa (€)']), width='stretch', hide_index=True)

                # UUSI: päiväkohtaiset yhteenvetokortit - kullekin päivälle (vedon
                # 'date'-kentän perusteella, josta otetaan vain YYYY-MM-DD-osa)
                # omat ROI/tulos/vetomäärä-mittarit, uusin päivä ylimpänä. Näin
                # näkee suoraan miten kukin päivä on mennyt, ei vain turnauksen
                # kokonaistulosta.
                st.write("---")
                st.markdown("**Päivä kerrallaan**")
                t_bets_pvm = t_bets.copy()
                t_bets_pvm['pvm'] = t_bets_pvm['date'].str[:10]
                for pvm in sorted(t_bets_pvm['pvm'].unique(), reverse=True):
                    day_df = t_bets_pvm[t_bets_pvm['pvm'] == pvm]
                    with st.container(border=True):
                        st.markdown(f"**{pvm}**")
                        show_bet_summary(day_df)
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
                hist_bets['Tuotto'] = hist_bets.apply(bet_profit, axis=1)
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

        # UUSI: kalibrointi OVER/UNDER-suunnittain, valittavalla aikavälillä. Koko
        # historian keskiarvo peittää helposti sen että malli on ajautunut vain
        # toiselle suunnalle (esim. OVER yliarvioitu) tai vain viime aikoina.
        st.write("---")
        st.subheader("🎯 Kalibrointi suunnittain (OVER / UNDER)")
        cal_range = st.radio(
            "Aikaväli:", ["Kaikki", "Viimeiset 300", "Viimeiset 150"],
            horizontal=True, key="cal_dir_range",
        )
        cal_d = bets_df[bets_df['status'].isin(['Voitto', 'Tappio'])].copy() if not bets_df.empty else pd.DataFrame()
        if not cal_d.empty:
            cal_d = cal_d[cal_d['model_prob'].notna() & cal_d['direction'].isin(['OVER', 'UNDER'])]
        if not cal_d.empty:
            cal_d = cal_d.sort_values('id')
            if cal_range == "Viimeiset 300":
                cal_d = cal_d.iloc[-300:]
            elif cal_range == "Viimeiset 150":
                cal_d = cal_d.iloc[-150:]
            cal_d['osui'] = (cal_d['status'] == 'Voitto').astype(int)
            cal_d['tuotto'] = cal_d.apply(bet_profit, axis=1)

            rows = []
            for dname in ['UNDER', 'OVER']:
                sub = cal_d[cal_d['direction'] == dname]
                if sub.empty:
                    continue
                rows.append({
                    'Suunta': dname, 'Todennäköisyyskori': 'YHTEENSÄ', 'Vetoja': len(sub),
                    'Mallin arvio': f"{sub['model_prob'].mean()*100:.1f} %",
                    'Toteutunut': f"{sub['osui'].mean()*100:.1f} %",
                    'Ero (%-yks.)': f"{(sub['osui'].mean() - sub['model_prob'].mean())*100:+.1f}",
                    'ROI %': f"{sub['tuotto'].sum() / sub['stake'].sum() * 100:+.1f}",
                })
                sub = sub.assign(kori=pd.cut(sub['model_prob'], [0, .5, .55, .6, .65, .7, 1.0]))
                for kori, g in sub.groupby('kori', observed=True):
                    rows.append({
                        'Suunta': dname, 'Todennäköisyyskori': str(kori), 'Vetoja': len(g),
                        'Mallin arvio': f"{g['model_prob'].mean()*100:.1f} %",
                        'Toteutunut': f"{g['osui'].mean()*100:.1f} %",
                        'Ero (%-yks.)': f"{(g['osui'].mean() - g['model_prob'].mean())*100:+.1f}",
                        'ROI %': f"{g['tuotto'].sum() / g['stake'].sum() * 100:+.1f}",
                    })
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
            st.caption(
                "Ero = toteutunut − mallin arvio. Selvästi negatiivinen ero tarkoittaa että "
                "malli yliarvioi kyseisen suunnan todennäköisyydet. Alle ~50 vedon koreissa "
                "luvut heiluvat paljon, ja samaan otteluun kasautuneet vedot korreloivat "
                "keskenään, joten pieniä eroja ei kannata tulkita."
            )
        else:
            st.info("Ei vielä ratkaistuja vetoja joissa on sekä mallin arvio että suunta tallessa.")

        # UUSI: malli vs markkina. Vaatii vedot joille vastakkaisen puolen kerroin
        # on kirjattu (opp_odds) - markkinatodennäköisyys lasketaan siitä marginaali
        # poistettuna vedon hetkellä. Korvaa closing odds -vertailun, joka ei sovi
        # kartta-/pelaajamarkkinoihin (linja liikkuu tai katoaa ennen sulkeutumista).
        st.write("---")
        st.subheader("📏 Malli vs markkina")
        st.caption(
            "Ennustaako malli tuloksia paremmin kuin markkina itse? Markkinan arvio = "
            "bookkerin kertoimet marginaali poistettuna vedon hetkellä (vaatii vastakkaisen "
            "puolen kertoimen kirjaamisen). Pienempi Brier-luku on parempi."
        )
        mm = bets_df[bets_df['status'].isin(['Voitto', 'Tappio'])].copy() if not bets_df.empty else pd.DataFrame()
        if not mm.empty:
            mm = mm[mm['model_prob'].notna() & mm['opp_odds'].notna() & (mm['opp_odds'] > 1.0)]
        if mm.empty:
            st.info("Ei vielä ratkaistuja vetoja joissa vastakkaisen puolen kerroin olisi kirjattu.")
        else:
            mm['markkina_p'] = (1 / mm['odds']) / ((1 / mm['odds']) + (1 / mm['opp_odds']))
            mm['osui'] = (mm['status'] == 'Voitto').astype(int)
            mm['tuotto'] = mm.apply(bet_profit, axis=1)
            brier_model = ((mm['model_prob'] - mm['osui']) ** 2).mean()
            brier_market = ((mm['markkina_p'] - mm['osui']) ** 2).mean()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Vetoja", f"{len(mm)}")
            m2.metric("Brier: malli", f"{brier_model:.4f}")
            m3.metric("Brier: markkina", f"{brier_market:.4f}",
                      delta=f"{(brier_market - brier_model):+.4f}", delta_color="off",
                      help="Positiivinen = malli ennusti paremmin kuin markkina")
            m4.metric("Toteutunut voitto-%", f"{mm['osui'].mean()*100:.1f} %",
                      help=f"Malli keskim. {mm['model_prob'].mean()*100:.1f} %, markkina {mm['markkina_p'].mean()*100:.1f} %")

            mm['ero'] = (mm['model_prob'] - mm['markkina_p']) * 100
            mm['ryhma'] = pd.cut(mm['ero'], [-100, -3, 3, 100],
                                 labels=['Malli alle markkinan (< -3 %-yks.)', 'Samaa luokkaa (±3 %-yks.)',
                                         'Malli yli markkinan (> +3 %-yks.)'])
            grp = mm.groupby('ryhma', observed=True).agg(
                Vetoja=('osui', 'size'),
                Mallin_arvio=('model_prob', lambda x: f"{x.mean()*100:.1f} %"),
                Markkina=('markkina_p', lambda x: f"{x.mean()*100:.1f} %"),
                Toteutunut=('osui', lambda x: f"{x.mean()*100:.1f} %"),
            ).reset_index()
            sums = mm.groupby('ryhma', observed=True)[['tuotto', 'stake']].sum()
            roi_by = (sums['tuotto'] / sums['stake'] * 100).map(lambda v: f"{v:+.1f}")
            grp['ROI %'] = grp['ryhma'].map(roi_by)
            st.dataframe(grp.rename(columns={'ryhma': 'Mallin ero markkinaan'}),
                         width='stretch', hide_index=True)
            if len(mm) < 100:
                st.caption("⚠️ Alle 100 vetoa - tulokset ovat vasta suuntaa antavia.")

        st.write("---")
        st.subheader("Kaikki vedot (All-time)")
        if not bets_df.empty:
            all_bets = bets_df.copy().sort_values('id')
            all_bets['Tuotto'] = all_bets.apply(bet_profit, axis=1)
            all_bets['Kassa (€)'] = all_bets['Tuotto'].cumsum()

            # UUSI: ROI %, vetojen määrä ja tulos euroina graafin yläpuolella
            show_bet_summary(all_bets)

            st.line_chart(all_bets['Kassa (€)'].reset_index(drop=True))
            st.dataframe(all_bets.drop(columns=['Tuotto', 'Kassa (€)']), width='stretch', hide_index=True)

    with subtab4:
        # UUSI: pelaajakohtainen tilastointi. Käyttää 'player'/'direction'-sarakkeita,
        # jotka tallentuvat automaattisesti uusista vedoista - vanhoissa (ennen tätä
        # ominaisuutta kirjatuissa) vedoissa nämä voivat olla NULL, jolloin ne eivät
        # näy tässä pelaajavalikossa, kunnes ne on kertaalleen muokattu "✏️ Muokkaa"
        # tai "Etsi ja muokkaa vanhoja vetoja" -lomakkeella (ks. subtab1).
        st.subheader("🎮 Pelaajakohtainen tilastointi")
        players_list = sorted(bets_df['player'].dropna().unique().tolist()) if 'player' in bets_df.columns else []

        if not players_list:
            st.info(
                "Ei vielä vetoja joihin pelaaja on tallennettu rakenteellisena kenttänä. "
                "Vanhat vedot saa näkyviin tähän muokkaamalla ne kertaalleen (katso "
                "'⚙️ Hallinta & Ratkaisu' -välilehti)."
            )
        else:
            valittu_pelaaja_stat = st.selectbox("Valitse pelaaja:", players_list, key="player_stat_select")
            p_bets = bets_df[bets_df['player'] == valittu_pelaaja_stat].copy()

            # ROI %, vetoja yhteensä, tulos €, vireillä - sama yhteenveto kuin
            # turnaus- ja päiväkorteissa.
            show_bet_summary(p_bets)

            # UNDER/OVER-jakauma ja kummankin suunnan oma voittoprosentti erikseen.
            # Voittoprosentti lasketaan vain RATKAISTUISTA Voitto/Tappio-vedoista -
            # Cashout jätetään pois, koska sen tulos ei ole yksiselitteisesti
            # voitto tai tappio (ks. bet_profit-funktion kommentti).
            dir_bets = p_bets[p_bets['direction'].isin(['OVER', 'UNDER'])]
            total_dir = len(dir_bets)
            under_df = p_bets[p_bets['direction'] == 'UNDER']
            over_df = p_bets[p_bets['direction'] == 'OVER']
            under_settled = under_df[under_df['status'].isin(['Voitto', 'Tappio'])]
            over_settled = over_df[over_df['status'].isin(['Voitto', 'Tappio'])]

            pct_under = (len(under_df) / total_dir * 100) if total_dir > 0 else 0.0
            pct_over = (len(over_df) / total_dir * 100) if total_dir > 0 else 0.0
            winrate_under = ((under_settled['status'] == 'Voitto').mean() * 100) if not under_settled.empty else 0.0
            winrate_over = ((over_settled['status'] == 'Voitto').mean() * 100) if not over_settled.empty else 0.0

            st.write("---")
            dc1, dc2 = st.columns(2)
            with dc1:
                st.metric("UNDER-vetoja", f"{len(under_df)} ({pct_under:.0f} %)")
                st.metric("UNDER voitto-%", f"{winrate_under:.1f} %",
                          help=f"{len(under_settled)} ratkaistua UNDER-vetoa")
            with dc2:
                st.metric("OVER-vetoja", f"{len(over_df)} ({pct_over:.0f} %)")
                st.metric("OVER voitto-%", f"{winrate_over:.1f} %",
                          help=f"{len(over_settled)} ratkaistua OVER-vetoa")

            st.write("---")
            st.dataframe(
                p_bets.sort_values('id', ascending=False),
                width='stretch', hide_index=True,
            )

    conn.close()
