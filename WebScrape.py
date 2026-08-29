import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.common.exceptions import WebDriverException, TimeoutException, SessionNotCreatedException
from bs4 import BeautifulSoup
import sqlite3
import time
import random
import re
import os
from datetime import datetime, timedelta

# Docker/ARM64-polku (Raspberry Pi): kun nämä on asetettu (ks. scraper/Dockerfile),
# käytetään Debianin omia chromium+chromium-driver-paketteja undetected_chromedriverin
# oman automaattilataajan sijaan - se olettaa aina x86-64:n eikä osaa hakea
# ARM64-Chromea. Windows-koneella nämä ympäristömuuttujat eivät ole asetettuja,
# joten käytös pysyy täysin ennallaan siellä.
CHROME_BIN = os.environ.get('CHROME_BIN')
CHROMEDRIVER_BIN = os.environ.get('CHROMEDRIVER_BIN')

BASE_URL = "https://www.hltv.org"

# ---------------------------------------------------------------------------
# ASETUKSET
# ---------------------------------------------------------------------------
# Kuinka monta joukkuetta rankingista otetaan mukaan.
TOP_N_TEAMS = 50

# Jos joukkueelle ei löydy aiempaa scrape-merkintää (eli sitä ei ole koskaan
# haettu), käytetään tätä oletushistoriaa ensimmäisellä ajokerralla.
DEFAULT_LOOKBACK_DAYS = 240

# Kuinka monta päivää taaksepäin viimeisimmästä scrape-päivästä varmuuden
# vuoksi vielä haetaan uudelleen (esim. jos ottelu lisättiin HLTV:hen viiveellä).
SAFETY_BUFFER_DAYS = 2

# HUOM: jos tietokannassa on jo dataa vanhemmalla (esim. 180 pv) ikkunalla,
# inkrementaalinen logiikka jatkaisi vain viimeisestä scrape-päivästä eteenpäin
# eikä 240 päivän ikkunan vanhempi osa tulisi koskaan haetuksi.
# Kun tämä on True, ottelulista haetaan aina koko DEFAULT_LOOKBACK_DAYS-ikkunalta.
# Se maksaa vain yhden ylimääräisen sivulatauksen per joukkue, koska jo
# tietokannassa olevat kartat ohitetaan joka tapauksessa.
# Aseta False, kun 240 päivän historia on kerran haettu ja haluat taas nopeat ajot.
FORCE_FULL_LOOKBACK = True

# Chromen pääversio, jota ajuri vastaa. None = undetected_chromedriver tunnistaa
# koneelle asennetun Chromen version itse ja lataa siihen sopivan ajurin.
# Tämä kannattaa pitää None:na, koska Chrome päivittyy itsestään taustalla ja
# kovakoodattu numero (esim. 150) hajoaa aina seuraavassa Chromen päivityksessä
# virheellä "This version of ChromeDriver only supports Chrome version X".
# Aseta numero vain, jos haluat tarkoituksella pinnata tietyn version.
CHROME_MAIN_VERSION = None


def setup_database():
    """Luo SQLite-tietokannan ja tarvittavat taulut."""
    conn = sqlite3.connect('hltv_data.db')
    c = conn.cursor()

    # Joukkueet
    c.execute('''CREATE TABLE IF NOT EXISTS teams
                 (id TEXT PRIMARY KEY, name TEXT, url TEXT)''')

    # Pelaajat. team_name tallennetaan team_id:n sijaan/lisäksi luettavuuden vuoksi.
    # last_match_date kertoo minkä ottelun perusteella joukkue on viimeksi päivitetty -
    # näin vanhempi ottelu ei voi enää ylikirjoittaa tuoreempaa joukkuetietoa,
    # vaikka otteluita käsiteltäisiin sekaisin järjestyksessä eri ajokerroilla.
    c.execute('''CREATE TABLE IF NOT EXISTS players
                 (id TEXT PRIMARY KEY,
                  team_id TEXT,
                  team_name TEXT,
                  name TEXT,
                  last_match_date TEXT)''')

    # Ottelut / Kartat
    c.execute('''CREATE TABLE IF NOT EXISTS matches
                 (id TEXT PRIMARY KEY,
                  team1_id TEXT,
                  team2_id TEXT,
                  score_team1 INTEGER,
                  score_team2 INTEGER,
                  map_name TEXT,
                  match_date TEXT)''')

    # Pelaajien tilastot yksittäisessä kartassa
    c.execute('''CREATE TABLE IF NOT EXISTS player_stats
                 (match_id TEXT,
                  player_id TEXT,
                  kills INTEGER,
                  headshots INTEGER,
                  deaths INTEGER,
                  PRIMARY KEY (match_id, player_id))''')

    # Seurataan, minä päivänä kunkin joukkueen ottelut on viimeksi haettu.
    # Näin seuraavalla ajokerralla voidaan hakea vain uudet ottelut siitä eteenpäin.
    c.execute('''CREATE TABLE IF NOT EXISTS scrape_status
                 (team_id TEXT PRIMARY KEY,
                  last_scraped_date TEXT)''')

    conn.commit()
    return conn

def get_last_scraped_date(conn, team_id):
    """Palauttaa joukkueen viimeisimmän haetun päivämäärän, tai None jos ei ole haettu ennen."""
    c = conn.cursor()
    c.execute("SELECT last_scraped_date FROM scrape_status WHERE team_id = ?", (team_id,))
    row = c.fetchone()
    return row[0] if row else None

def update_scraped_date(conn, team_id, date_str):
    """Päivittää joukkueen viimeisimmän haetun päivämäärän."""
    c = conn.cursor()
    c.execute("""INSERT INTO scrape_status (team_id, last_scraped_date) VALUES (?, ?)
                 ON CONFLICT(team_id) DO UPDATE SET last_scraped_date=excluded.last_scraped_date""",
              (team_id, date_str))
    conn.commit()

def match_already_scraped(conn, match_id):
    """Tarkistaa, onko ottelu/kartta jo tietokannassa."""
    c = conn.cursor()
    c.execute("SELECT 1 FROM matches WHERE id = ?", (match_id,))
    return c.fetchone() is not None

def _build_driver(version_main):
    """Sisäinen apufunktio: rakentaa selaininstanssin annetulla versionumerolla
    (None = automaattinen tunnistus). Docker/ARM64-polulla version_main ei ole
    käytössä - ajuri on kiinteä Debian-paketti eikä sitä ladata automaattisesti."""

    if CHROME_BIN and CHROMEDRIVER_BIN:
        # Docker/ARM64-polku (Raspberry Pi). HUOM: käytetään TAVALLISTA
        # Seleniumin omaa webdriver.Chrome():a, EI undetected_chromedriveria.
        # uc.Chrome() yritti "patchata" annetun ajurin kopioimalla sen omaan
        # välimuistiinsa (~/.local/share/undetected_chromedriver/) poistaakseen
        # automaatiotunnisteita binääristä - tulos oli rikki ARM64:llä:
        # "OSError: [Errno 8] Exec format error" ajoa yritettäessä (testattu
        # oikealla Pi 5:llä, ks. keskusteluhistoria). Tavallinen Selenium
        # käyttää annettua /usr/bin/chromedriver-ajuria suoraan sellaisenaan
        # ilman patch-vaihetta, mikä toimii luotettavasti.
        # --disable-blink-features=AutomationControlled korvaa osan uc:n
        # piilotuksesta ilman että ajuria tarvitsee patchata.
        # Headless-tilasta: alun perin yritettiin "headed" Chromea Xvfb-
        # virtuaalinäytön kautta, mutta se jäi pysyvästi jumiin Pi 5:llä
        # (testattu käsin suoraan Chromiumilla - ei koskaan palauttanut
        # mitään, ei edes virhettä). --headless=new toimii luotettavasti, ja
        # on nykyaikaisessa Chromiumissa jo huomattavasti vaikeampi tunnistaa
        # botiksi kuin vanha --headless-lippu, joten Xvfb ei ole enää tarpeen.
        options = ChromeOptions()
        options.binary_location = CHROME_BIN
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        service = ChromeService(executable_path=CHROMEDRIVER_BIN)
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        return driver

    # Windows/paikallinen polku (ennallaan): undetected_chromedriver, joka
    # tunnistaa/lataa Chromelle sopivan ajurin automaattisesti.
    options = uc.ChromeOptions()
    kwargs = {'options': options, 'use_subprocess': True}
    if version_main is not None:
        kwargs['version_main'] = version_main
    driver = uc.Chrome(**kwargs)
    driver.set_page_load_timeout(60)  # estää yksittäistä sivulatausta jäämästä ikuisesti jumiin
    return driver

def create_driver():
    """Luo (tai uudelleenluo) selaininstanssin. Käytetään myös silloin, kun
    selain jumiutuu/kaatuu kesken ajon ja tarvitaan uusi istunto.

    Jos ajurin ja Chromen versiot eivät täsmää, Selenium kertoo virheviestissään
    koneella olevan Chromen version ("Current browser version is 152.0.7977.64").
    Poimitaan se ja yritetään heti uudelleen oikealla pääversiolla, jottei
    skriptiä tarvitse käydä käsin muokkaamassa joka kerta kun Chrome päivittyy."""
    global CHROME_MAIN_VERSION
    try:
        return _build_driver(CHROME_MAIN_VERSION)
    except SessionNotCreatedException as e:
        message = str(e)
        found = re.search(r'Current browser version is (\d+)', message)
        if not found:
            print("[VIRHE] Selaimen käynnistys epäonnistui eikä Chromen versiota saatu luettua virheviestistä.")
            raise
        detected = int(found.group(1))
        print(f"[i] Ajurin ja Chromen versiot eivät täsmänneet. Havaittu Chrome-versio: {detected}.")
        print(f"[i] Ladataan sopiva ajuri ja yritetään uudelleen...")
        CHROME_MAIN_VERSION = detected  # muistetaan loppuajon ajaksi
        return _build_driver(detected)

def check_cloudflare(driver):
    """Tarkistaa, onko selain jumissa Cloudflaren tarkistusruudussa ja yrittää odottaa."""
    for _ in range(4):
        title = driver.title.lower()
        if "moment" in title or "cloudflare" in title or "just a moment" in title:
            print("     [!] Cloudflare-estoruutu havaittu, odotetaan 5 sekuntia ylimääräistä...")
            time.sleep(5)
        else:
            return True
    return False

def get_top_teams(driver, conn, top_n=TOP_N_TEAMS):
    """Hakee rankingista enintään top_n joukkuetta."""
    print(f"Haetaan Top {top_n} joukkueet...")
    url = f"{BASE_URL}/ranking/teams"
    driver.get(url)
    time.sleep(6)

    check_cloudflare(driver)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    teams_data = []
    all_ranked = soup.find_all('div', class_='ranked-team')
    teams = all_ranked[:top_n]
    c = conn.cursor()

    for team in teams:
        name_element = team.find('span', class_='name')
        link_element = team.find('a', href=lambda href: href and '/team/' in href)

        if name_element and link_element:
            name = name_element.text.strip()
            href = link_element['href']

            try:
                team_id = next((part for part in href.split('/') if part.isdigit()), None)
                if not team_id: continue
            except Exception:
                continue

            full_url = f"{BASE_URL}{href}"
            teams_data.append({'id': team_id, 'name': name, 'url': full_url})
            c.execute("INSERT OR IGNORE INTO teams (id, name, url) VALUES (?, ?, ?)", (team_id, name, full_url))

    conn.commit()
    print(f"Löydettiin {len(teams_data)} joukkuetta (sivulla oli yhteensä {len(all_ranked)} rankattua joukkuetta).")

    # HLTV:n ranking-sivu on perinteisesti listannut vain 30 joukkuetta yhdellä
    # sivulla. Jos tähän osutaan, top 50 ei yksinkertaisesti ole saatavilla
    # tältä sivulta - silloin pitää käyttää eri lähdettä (esim. Valve-ranking
    # /valve-ranking/teams tai /stats/teams -listaus suodattimilla).
    if len(teams_data) < top_n:
        print(f"[!] HUOM: pyydettiin {top_n} joukkuetta, mutta sivulta löytyi vain {len(teams_data)}.")
        print("[!] HLTV:n /ranking/teams näyttää usein vain 30 joukkuetta. Jos tarvitset")
        print("[!] oikeasti 50, vaihda lähteeksi esim. /valve-ranking/teams tai stats-listaus.")

    return teams_data

def extract_match_date(cell):
    """Yrittää lukea tarkan ottelupäivän solusta. HLTV merkitsee päivämäärän usein
    'data-unix'-attribuuttiin (millisekunteina), mikä on luotettavin tapa lukea se.
    Jos sitä ei löydy, yritetään tulkita näkyvä teksti muutamalla yleisellä formaatilla."""
    if cell is None:
        return None

    # Tarkistetaan ensin itse solu, sitten kaikki lapsielementit (HLTV saattaa
    # laittaa data-unix joko suoraan <td>:hen tai sen sisällä olevaan <span>/<div>:iin)
    if cell.has_attr('data-unix'):
        try:
            unix_ms = int(cell['data-unix'])
            return datetime.utcfromtimestamp(unix_ms / 1000).strftime('%Y-%m-%d')
        except (ValueError, KeyError):
            pass

    unix_element = cell.find(attrs={'data-unix': True})
    if unix_element:
        try:
            unix_ms = int(unix_element['data-unix'])
            return datetime.utcfromtimestamp(unix_ms / 1000).strftime('%Y-%m-%d')
        except (ValueError, KeyError):
            pass

    raw_text = cell.text.strip()
    for fmt in ('%d/%m/%y', '%Y-%m-%d', '%d/%m/%Y', '%d.%m.%Y', '%d-%m-%Y', '%B %d %Y', '%d %B %Y', '%b %d %Y', '%d %b %Y'):
        try:
            return datetime.strptime(raw_text, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue

    return None

def get_team_match_links(driver, team_id, team_name, start_date, end_date):
    print(f"\nHaetaan joukkueen {team_name} (ID: {team_id}) kartat aikaväliltä {start_date.strftime('%Y-%m-%d')} - {end_date.strftime('%Y-%m-%d')}...")

    url = f"{BASE_URL}/stats/teams/matches/{team_id}/{team_name.replace(' ', '-').lower()}?startDate={start_date.strftime('%Y-%m-%d')}&endDate={end_date.strftime('%Y-%m-%d')}"
    driver.get(url)
    time.sleep(random.uniform(5.0, 7.0))

    check_cloudflare(driver)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    match_links = []

    table = soup.find('table', class_='stats-table')
    if not table:
        print(f"  [VIRHE] Ei löydetty ottelutaulukkoa joukkueelle {team_name}. Tarkista sivun latautuminen.")
        return []

    for row in table.find('tbody').find_all('tr'):
        cols = row.find_all('td')
        if len(cols) >= 6:
            map_name = cols[4].text.strip()
            link_elem = row.find('a', href=lambda href: href and '/stats/matches/mapstatsid/' in href)

            # Oletetaan päivämäärän olevan ensimmäisessä sarakkeessa (HLTV:n yleinen tapa).
            # Tarkista alla oleva DEBUG-tuloste - jos päivämäärä tulkitaan väärin tai
            # jää None:ksi, tarkista mistä sarakkeesta se todella löytyy ja muuta cols[0].
            match_date_iso = extract_match_date(cols[0])
            print(f"     DEBUG: Ottelurivin pvm-teksti: '{cols[0].text.strip()}' -> tulkittu: {match_date_iso}")

            if link_elem:
                match_id = next((part for part in link_elem['href'].split('/') if part.isdigit()), None)
                match_links.append({
                    'id': match_id,
                    'url': f"{BASE_URL}{link_elem['href']}",
                    'map_name': map_name,
                    'match_date': match_date_iso if match_date_iso else '0000-00-00'  # varmuuden vuoksi vanhin mahdollinen, jos pvm ei löydy
                })

    print(f"  Löydettiin {len(match_links)} pelattua karttaa joukkueelle {team_name}.")
    return match_links

def get_match_stats(driver, conn, match_id, match_url, map_name, match_date):
    print(f"  -> Haetaan tilastot kartalle: {map_name} (ID: {match_id})...")
    driver.get(match_url)

    time.sleep(random.uniform(4.0, 7.0))
    check_cloudflare(driver)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    c = conn.cursor()

    try:
        match_info = soup.find('div', class_='match-info-box')

        if not match_info:
            print(f"     [VIRHE] Sivulta ei löytynyt tuloksia.")
            return

        # Haetaan tulostaulun tiedot ottelua varten
        team_left = match_info.find('div', class_='team-left')
        team_right = match_info.find('div', class_='team-right')

        # Kootaan (joukkueen nimi -> team_id) -yhdistelmä, jotta taulukon otsikkoa
        # voidaan verrata luotettavasti oikeaan joukkueeseen. Tämä korvaa aiemman
        # find_all_previous-hakemisen, joka saattoi osua vahingossa mihin tahansa
        # sivun aiempaan '/stats/teams/'-linkkiin (esim. sivupalkkiin) ja antoi
        # siksi väärän team_id:n pelaajille.
        team_name_to_id = {}

        if team_left and team_right:
            t1_link = team_left.find('a')
            t2_link = team_right.find('a')

            t1_id = next((p for p in t1_link['href'].split('/') if p.isdigit()), None) if t1_link else None
            t2_id = next((p for p in t2_link['href'].split('/') if p.isdigit()), None) if t2_link else None

            t1_name = t1_link.text.strip() if t1_link else ""
            t2_name = t2_link.text.strip() if t2_link else ""

            # team_name_to_id: lower(nimi) -> (team_id, alkuperäinen nimi oikeilla kirjaimilla)
            if t1_id and t1_name:
                team_name_to_id[t1_name.lower()] = (t1_id, t1_name)
            if t2_id and t2_name:
                team_name_to_id[t2_name.lower()] = (t2_id, t2_name)

            s1 = int(team_left.find('div', class_='bold').text.strip()) if team_left.find('div', class_='bold') else 0
            s2 = int(team_right.find('div', class_='bold').text.strip()) if team_right.find('div', class_='bold') else 0

            c.execute("INSERT OR IGNORE INTO matches (id, team1_id, team2_id, score_team1, score_team2, map_name, match_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (match_id, t1_id, t2_id, s1, s2, map_name, match_date))

        stats_tables = soup.find_all('table', class_='stats-table')

        for table in stats_tables:
            rows = table.find_all('tr')
            if not rows: continue

            headers = rows[0].find_all(['th', 'td'])
            kills_idx, deaths_idx = -1, -1

            for idx, cell in enumerate(headers):
                text = cell.text.strip().lower()
                # HLTV käyttää nykyään otsikkoja "K (hs)" ja "D (t)" pelkän "K"/"D" sijaan
                if text in ['k', 'kills'] or text.startswith('k ('):
                    kills_idx = idx
                if text in ['d', 'deaths'] or text.startswith('d ('):
                    deaths_idx = idx

            # Varmistetaan, että taulukossa on tappojen ja kuolemien lisäksi riittävästi
            # sarakkeita (>8). Tämä jättää pois pienet First Kills ja Flashbang -taulukot.
            if kills_idx == -1 or deaths_idx == -1 or len(headers) < 8:
                continue

            # --- LUOTETTAVA JOUKKUELOGIIKKA ---
            # Taulukon ensimmäinen otsikkosolu on itse joukkueen nimi (esim. "Falcons"),
            # kuten debug-tulosteesta nähtiin. Verrataan sitä match-info-boxista
            # saatuihin nimiin, jolloin oikea team_id löytyy varmasti - ei arvausta
            # muiden sivulla olevien linkkien perusteella.
            current_team_id = None
            current_team_name = None
            table_team_name = headers[0].text.strip().lower() if headers else ""

            if table_team_name and table_team_name in team_name_to_id:
                current_team_id, current_team_name = team_name_to_id[table_team_name]
            elif table_team_name and len(table_team_name) > 2:
                # Varasuunnitelma: osittainen täsmäys, jos nimissä pieniä eroja
                # (esim. lyhenteet tai ylimääräiset merkit otsikossa).
                # HUOM: table_team_name tarkistetaan ensin ei-tyhjäksi ja riittävän
                # pitkäksi, koska tyhjä merkkijono "" täsmäisi Pythonissa aina
                # ensimmäiseen joukkueeseen ("" in mikä_tahansa_nimi == True),
                # mikä aiheutti yksittäisiä pelaajia väärällä team_id:llä.
                for name, (tid, tname) in team_name_to_id.items():
                    if name in table_team_name or table_team_name in name:
                        current_team_id, current_team_name = tid, tname
                        break

            if not current_team_id:
                print(f"     [!] Ei tunnistettu joukkuetta taulukon otsikosta '{headers[0].text.strip() if headers else '?'}', ohitetaan taulukko.")
                continue
            # ----------------------------------------

            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) <= max(kills_idx, deaths_idx): continue

                player_link = cols[0].find('a', href=True)
                if not player_link or 'player' not in player_link['href'].lower(): continue

                player_id = next((p for p in player_link['href'].split('/') if p.isdigit()), None)
                player_name = player_link.text.strip()
                if not player_id: continue

                # Päivitetään pelaajan joukkue vain, jos tämä ottelu on yhtä tuore tai
                # tuoreempi kuin viimeksi tallennettu - näin vanha ottelu ei voi enää
                # ylikirjoittaa tuoreempaa joukkuetietoa (esim. jos pelaaja on vaihtanut
                # joukkuetta 4kk sitten pelatun ottelun jälkeen).
                c.execute("SELECT last_match_date FROM players WHERE id = ?", (player_id,))
                existing = c.fetchone()
                should_update_team = True
                if existing and existing[0] and match_date and match_date < existing[0]:
                    should_update_team = False

                if should_update_team:
                    c.execute("""INSERT INTO players (id, team_id, team_name, name, last_match_date) VALUES (?, ?, ?, ?, ?)
                                 ON CONFLICT(id) DO UPDATE SET
                                    team_id=excluded.team_id,
                                    team_name=excluded.team_name,
                                    name=excluded.name,
                                    last_match_date=excluded.last_match_date""",
                              (player_id, current_team_id, current_team_name, player_name, match_date))
                else:
                    # Vanhempi ottelu: päivitetään vain nimi (jos muuttunut), ei joukkuetta
                    c.execute("""INSERT OR IGNORE INTO players (id, team_id, team_name, name, last_match_date)
                                 VALUES (?, ?, ?, ?, ?)""",
                              (player_id, current_team_id, current_team_name, player_name, match_date))

                try:
                    # HLTV:n teksti on esim "22 (5)" -> kokonaistapot ja headshotit erikseen
                    k_full = cols[kills_idx].text.strip()
                    d_full = cols[deaths_idx].text.strip()

                    k_str = k_full.split()[0]
                    d_str = d_full.split()[0]

                    if not k_str.lstrip('-').isdigit() or not d_str.lstrip('-').isdigit():
                        continue

                    kills = int(k_str)
                    deaths = int(d_str)

                    # Poimitaan headshot-luku sulkeiden sisältä, esim "22 (5)" -> 5
                    headshots = 0
                    if '(' in k_full:
                        hs_str = k_full.split('(')[1].replace(')', '').strip()
                        if hs_str.isdigit():
                            headshots = int(hs_str)

                    c.execute("INSERT OR IGNORE INTO player_stats (match_id, player_id, kills, headshots, deaths) VALUES (?, ?, ?, ?, ?)",
                              (match_id, player_id, kills, headshots, deaths))
                    print(f"     DEBUG: Tallennettu statsit -> {player_name} (team {current_team_id}): {kills} K ({headshots} HS) / {deaths} D")
                except Exception as e:
                    print(f"     [!] Virhe lukujen käsittelyssä pelaajalle {player_name}: {e}")

        conn.commit()
    except Exception as e:
        print(f"  Virhe karttadatan parsimisessa: {e}")


if __name__ == "__main__":
    print("Käynnistetään selain...")
    driver = create_driver()
    db_conn = setup_database()

    try:
        teams = get_top_teams(driver, db_conn, TOP_N_TEAMS)
        if teams:
            for team_index, team in enumerate(teams, start=1):
                print(f"\n===== Joukkue {team_index}/{len(teams)}: {team['name']} =====")
                today = datetime.now()
                last_scraped_str = get_last_scraped_date(db_conn, team['id'])

                if last_scraped_str and not FORCE_FULL_LOOKBACK:
                    # Jatketaan siitä mihin viime kerralla jäätiin (pienellä puskurilla)
                    last_scraped = datetime.strptime(last_scraped_str, '%Y-%m-%d')
                    start_date = last_scraped - timedelta(days=SAFETY_BUFFER_DAYS)
                    print(f"  [i] {team['name']} on skrapattu aiemmin ({last_scraped_str}), haetaan vain uudet ottelut.")
                else:
                    # Ensimmäinen kerta tälle joukkueelle, TAI FORCE_FULL_LOOKBACK on päällä
                    # -> haetaan koko ikkuna. Jo tallennetut kartat ohitetaan alempana,
                    # joten ylimääräistä työtä syntyy vain yksi ottelulistasivu per joukkue.
                    start_date = today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
                    if last_scraped_str:
                        print(f"  [i] {team['name']}: FORCE_FULL_LOOKBACK päällä, haetaan koko {DEFAULT_LOOKBACK_DAYS} päivän ikkuna.")
                    else:
                        print(f"  [i] {team['name']} ei ole skrapattu aiemmin, haetaan viimeiset {DEFAULT_LOOKBACK_DAYS} päivää.")

                matches = get_team_match_links(driver, team['id'], team['name'], start_date, today)

                for match in matches:
                    if match_already_scraped(db_conn, match['id']):
                        print(f"  [i] Kartta {match['map_name']} (ID: {match['id']}) on jo tietokannassa, ohitetaan.")
                        continue

                    # Yritetään hakea kartan tilastot. Jos selain jumiutuu tai kaatuu
                    # (esim. ReadTimeoutError, WebDriverException), käynnistetään
                    # selain uudelleen ja yritetään sama kartta vielä kerran ennen
                    # kuin se lopulta ohitetaan - näin koko ajo ei kaadu yhteen
                    # yksittäiseen ongelmalliseen sivulataukseen.
                    for attempt in range(2):
                        try:
                            get_match_stats(driver, db_conn, match['id'], match['url'], match['map_name'], match['match_date'])
                            break
                        except (WebDriverException, TimeoutException, Exception) as e:
                            print(f"  [VIRHE] Selain jumiutui/kaatui kartalla {match['map_name']} (yritys {attempt + 1}/2): {e}")
                            print("  Käynnistetään selain uudelleen...")
                            try:
                                driver.quit()
                            except Exception:
                                pass
                            time.sleep(5)
                            driver = create_driver()
                    else:
                        print(f"  [!] Kartta {match['map_name']} (ID: {match['id']}) epäonnistui kahdesti, ohitetaan lopullisesti.")

                    wait_time = random.uniform(3.0, 7.0)
                    print(f"  Odotetaan {wait_time:.1f} sekuntia ennen seuraavaa karttaa...")
                    time.sleep(wait_time)

                # Merkitään joukkue haetuksi tähän päivään asti seuraavaa ajoa varten
                update_scraped_date(db_conn, team['id'], today.strftime('%Y-%m-%d'))

        print("\nKaikki valmista! Data on tietokannassa 'hltv_data.db'.")
    finally:
        try: driver.quit()
        except Exception: pass
        db_conn.close()