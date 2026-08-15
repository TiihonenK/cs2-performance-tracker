import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import sqlite3
import time
import random
from datetime import datetime, timedelta

BASE_URL = "https://www.hltv.org"

def setup_database():
    """Luo SQLite-tietokannan ja tarvittavat taulut."""
    conn = sqlite3.connect('hltv_data.db')
    c = conn.cursor()
    
    # Joukkueet
    c.execute('''CREATE TABLE IF NOT EXISTS teams 
                 (id TEXT PRIMARY KEY, name TEXT, url TEXT)''')
    
    # Pelaajat
    c.execute('''CREATE TABLE IF NOT EXISTS players 
                 (id TEXT PRIMARY KEY, team_id TEXT, name TEXT)''')
    
    # Ottelut / Kartat
    c.execute('''CREATE TABLE IF NOT EXISTS matches
                 (id TEXT PRIMARY KEY, 
                  team1_id TEXT, 
                  team2_id TEXT, 
                  score_team1 INTEGER, 
                  score_team2 INTEGER,
                  map_name TEXT)''')
                  
    # Pelaajien tilastot yksittäisessä kartassa
    c.execute('''CREATE TABLE IF NOT EXISTS player_stats
                 (match_id TEXT, 
                  player_id TEXT, 
                  kills INTEGER, 
                  headshots INTEGER,
                  deaths INTEGER,
                  PRIMARY KEY (match_id, player_id))''')
                  
    conn.commit()
    return conn

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

def get_top_30_teams(driver, conn):
    print("Haetaan Top 30 joukkueet...")
    url = f"{BASE_URL}/ranking/teams"
    driver.get(url)
    time.sleep(6)
    
    check_cloudflare(driver)
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    teams_data = []
    teams = soup.find_all('div', class_='ranked-team')[:30]
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
    print(f"Löydettiin {len(teams_data)} joukkuetta (Top 30).")
    return teams_data

def get_team_match_links(driver, team_id, team_name):
    # MUUTOS 1: Päivitetty tulostus ja aika-ikkuna 180 päivään (n. 6kk)
    print(f"\nHaetaan joukkueen {team_name} (ID: {team_id}) kartat viimeiseltä 6 kuukaudelta...")
    end_date = datetime.now()
    start_date = end_date - timedelta(days=180) 
    
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
            if link_elem:
                match_id = next((part for part in link_elem['href'].split('/') if part.isdigit()), None)
                match_links.append({'id': match_id, 'url': f"{BASE_URL}{link_elem['href']}", 'map_name': map_name})
            
    print(f"  Löydettiin {len(match_links)} pelattua karttaa joukkueelle {team_name}.")
    return match_links

def get_match_stats(driver, conn, match_id, match_url, map_name):
    print(f"  -> Haetaan tilastot kartalle: {map_name} (ID: {match_id})...")
    driver.get(match_url)
    
    time.sleep(random.uniform(4.0, 7.0))
    check_cloudflare(driver)
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    c = conn.cursor()
    
    try:
        match_info = soup.find('div', class_='match-info-box')
        
        if not match_info:
            print(f"     [VIRHE] Sivulta ei löytynyt tuloksia. Sivu voi olla rikki tai Cloudflare blokkasi botin.")
            return

        team_ids = []
        team_left = match_info.find('div', class_='team-left')
        team_right = match_info.find('div', class_='team-right')
        
        if team_left and team_right:
            t1_id = next((p for p in team_left.find('a')['href'].split('/') if p.isdigit()), None)
            t2_id = next((p for p in team_right.find('a')['href'].split('/') if p.isdigit()), None)
            team_ids = [t1_id, t2_id]
            
            s1 = int(team_left.find('div', class_='bold').text.strip()) if team_left.find('div', class_='bold') else 0
            s2 = int(team_right.find('div', class_='bold').text.strip()) if team_right.find('div', class_='bold') else 0
            
            c.execute("INSERT OR IGNORE INTO matches (id, team1_id, team2_id, score_team1, score_team2, map_name) VALUES (?, ?, ?, ?, ?, ?)",
                      (match_id, t1_id, t2_id, s1, s2, map_name))
            print(f"     DEBUG: Tallennettu ottelu - Tiimi 1: {t1_id} ({s1}) vs Tiimi 2: {t2_id} ({s2})")
    
        stats_tables = soup.find_all('table', class_='stats-table')
        valid_table_count = 0
        
        for table in stats_tables:
            rows = table.find_all('tr')
            if not rows: continue
            
            headers = rows[0].find_all(['th', 'td'])
            kills_idx, deaths_idx = -1, -1
            
            for idx, cell in enumerate(headers):
                text = cell.text.strip().lower()
                if text in ['k', 'kills'] or text.startswith('k ('):
                    kills_idx = idx
                if text in ['d', 'deaths'] or text.startswith('d ('):
                    deaths_idx = idx
            
            if kills_idx == -1 or deaths_idx == -1 or len(headers) < 8:
                continue
                
            current_team_id = team_ids[valid_table_count] if valid_table_count < len(team_ids) else None
            valid_table_count += 1
            
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) <= max(kills_idx, deaths_idx): continue
                    
                player_link = cols[0].find('a', href=True)
                if not player_link or 'player' not in player_link['href'].lower(): continue
                
                player_id = next((p for p in player_link['href'].split('/') if p.isdigit()), None)
                player_name = player_link.text.strip()
                if not player_id: continue
                
                if current_team_id:
                    c.execute("""INSERT INTO players (id, team_id, name) VALUES (?, ?, ?)
                                 ON CONFLICT(id) DO UPDATE SET team_id=excluded.team_id, name=excluded.name""", 
                              (player_id, current_team_id, player_name))
                else:
                    c.execute("INSERT OR IGNORE INTO players (id, name) VALUES (?, ?)", (player_id, player_name))
                
                try:
                    k_full = cols[kills_idx].text.strip()
                    d_full = cols[deaths_idx].text.strip()
                    
                    k_str = k_full.split()[0]
                    d_str = d_full.split()[0]
                    
                    if not k_str.lstrip('-').isdigit() or not d_str.lstrip('-').isdigit():
                        continue
                        
                    kills = int(k_str)
                    deaths = int(d_str)
                    
                    headshots = 0
                    if '(' in k_full:
                        hs_str = k_full.split('(')[1].replace(')', '').strip()
                        if hs_str.isdigit():
                            headshots = int(hs_str)
                    
                    c.execute("INSERT OR IGNORE INTO player_stats (match_id, player_id, kills, headshots, deaths) VALUES (?, ?, ?, ?, ?)",
                              (match_id, player_id, kills, headshots, deaths))
                    # Poistettu printtaus jokaisesta pelaajasta erikseen, jotta terminaali ei täyty liikaa isossa ajossa
                except Exception as e:
                    pass

        conn.commit()
    except Exception as e:
        print(f"  Virhe karttadatan parsimisessa: {e}")

if __name__ == "__main__":
    print("Käynnistetään selain...")
    options = uc.ChromeOptions()
    driver = uc.Chrome(options=options, use_subprocess=True, version_main=150) 
    db_conn = setup_database()
    
    try:
        teams = get_top_30_teams(driver, db_conn)
        if teams:
            # MUUTOS 2: Poistettu [:3] rajoitus, käy nyt läpi KAIKKI löydetyt joukkueet
            for team in teams: 
                matches = get_team_match_links(driver, team['id'], team['name'])
                for match in matches: 
                    get_match_stats(driver, db_conn, match['id'], match['url'], match['map_name'])
                    
                    wait_time = random.uniform(6.0, 10.0)
                    print(f"  Odotetaan {wait_time:.1f} sekuntia ennen seuraavaa karttaa...")
                    time.sleep(wait_time)
                    
        print("\nKaikki valmista! Data on tietokannassa 'hltv_data.db'.")
    finally:
        try: driver.quit()
        except OSError: pass
        db_conn.close()