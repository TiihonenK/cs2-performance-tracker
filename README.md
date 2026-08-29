# CS2-vedonlyöntimalli → Raspberry Pi -kotiserveri

Tämä paketti siirtää koko `cs2-performance-tracker`-projektin (skraperi +
kierros-/tappomalli + Streamlit-dashboard) Raspberry Pi 5:lle, jotta pääset
dashboardiin käsiksi mistä tahansa Tailscalen kautta - myös kun oma koneesi ei
ole päällä.

## Ennen kuin aloitat

Tein nämä tiedostot ilman suoraa yhteyttä Pi:hin (vain Windows-koneesi oli
yhdistetty), joten jokainen komento alla on tarkoitettu SINUN ajettavaksesi
Pi:n SSH-istunnossa - en ole voinut testata näitä oikealla Pi:llä. Etene
rauhassa vaihe kerrallaan ja pysähdy jos jokin virheilmoitus ei täsmää tähän
ohjeeseen.

**Tee vaiheet tässä järjestyksessä:**

1. **`docs/01_ha_varmuuskopiointi_ja_pi_asennus.md`** - Home Assistantin
   varmuuskopiointi (ÄLÄ ohita tätä) ja Pi:n uudelleenasennus Raspberry Pi
   OS:lla + Docker.
2. Tämän README:n vaiheet 1-4 alla - koko pino käyntiin.
3. **`docs/02_tailscale.md`** - etäkäyttö.

## Miksi juuri näin

- **HAOS vaihtuu Raspberry Pi OS + Dockeriin.** Home Assistant OS omistaa koko
  levyn eikä sen päälle voi lisätä muuta - siksi HA palautetaan varmuuskopiosta
  Docker-konttina tavallisen Linuxin päällä (docs/01).
- **Skraperi ajetaan kontissa Debianin omalla Chromium+chromedriver-parilla**,
  ei `undetected_chromedriver`:in automaattilataajalla - se olettaa aina
  x86-64:n eikä osaa hakea ARM64-Chromea (Google julkaisi sen ARM64-tuen vasta
  11.8.2026, ja `undetected_chromedriver`-kirjaston lataajalogiikka ei vielä
  osaa hyödyntää sitä). Tämä on koko projektin epävarmin osa - ks. "Jos
  skraperi ei toimi" alla.
- **Skraperi ajetaan systemd-ajastimella kerran päivässä**, ei taustapalveluna
  24/7 - Chromium syö muistia, ja 4 Gt riittää paremmin kun se on käynnissä
  vain ajon ajan.

## 1. Kopioi tiedostot Pi:lle

Kaksi tapaa - valitse jompikumpi:

**A) Git (suositeltu, koodi on jo GitHubissa: `TiihonenK/cs2-performance-tracker`)**

Windows-koneellasi: committaa ja pushaa nämä uudet tiedostot repoon tavalliseen
tapaan (VS Code / `git add . && git commit -m "Pi-deployment" && git push`).

Pi:llä:
```bash
git clone https://github.com/TiihonenK/cs2-performance-tracker.git ~/cs2-performance-tracker
cd ~/cs2-performance-tracker
```

Tietokannat (`hltv_data.db`, `my_bets.db`) EIVÄT tule mukaan gitistä (ne ovat
todennäköisesti `.gitignore`ssa) - kopioi ne erikseen kohdassa 2.

**B) Suora kopiointi (jos et halua käyttää gitiä)**

```bash
# Koneeltasi, kun Pi on samassa verkossa tai Tailscalessa:
scp -r "C:\Users\kimal\OneDrive\CS_betting\Web_scrape" pi@cs-server.local:~/cs2-performance-tracker
```

## 2. Siirrä olemassa oleva data mukaan

Näin säilytät jo kerätyn HLTV-datan ja vetohistoriasi:

```bash
# Koneeltasi:
scp "C:\Users\kimal\OneDrive\CS_betting\Web_scrape\hltv_data.db" pi@cs-server.local:~/cs2-performance-tracker/data/
scp "C:\Users\kimal\OneDrive\CS_betting\Web_scrape\my_bets.db" pi@cs-server.local:~/cs2-performance-tracker/data/
```

(Luo `data`-kansio ensin Pi:llä jos se ei ole vielä olemassa: `mkdir -p
~/cs2-performance-tracker/data`)

## 3. Käynnistä koko pino

```bash
cd ~/cs2-performance-tracker
docker compose up -d --build
```

Ensimmäinen build kestää muutaman minuutin (Docker lataa ja kääntää kaiken
ARM64:lle). Tarkista että kaikki on pystyssä:

```bash
docker compose ps
```

Dashboard on nyt paikallisverkossa osoitteessa `http://cs-server.local:8501`.

## 4. Ota skraperin ajastus käyttöön

```bash
sudo cp systemd/cs-scraper.service systemd/cs-scraper.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cs-scraper.timer
```

Muista muokata `cs-scraper.service`:n `WorkingDirectory`-riviä, jos kloonasit
projektin muuhun kuin `~/cs2-performance-tracker`-kansioon.

Testaa ajo heti (älä odota huomiseen 6:aan):

```bash
sudo systemctl start cs-scraper.service
journalctl -u cs-scraper.service -f
```

## Jos skraperi ei toimi (todennäköisin kompastuskivi)

Xvfb+Chromium-yhdistelmä kontissa on tässä paketissa testaamaton koodipolku.
Jos `journalctl -u cs-scraper.service` näyttää virheen:

- **"Exec format error"** → `CHROME_BIN`/`CHROMEDRIVER_BIN`-ympäristömuuttujat
  eivät välity kontille oikein, tai `chromium`/`chromium-driver` ei asentunut -
  tarkista `docker compose run --rm scraper which chromium chromedriver`.
- **Jää jumiin Cloudflare-tarkistukseen ("Just a moment")** → kokeile poistaa
  `--window-size=1920,1080`-rivi `webScrape.py`:n `_build_driver()`-funktiosta,
  tai nosta `check_cloudflare()`-funktion odotusaikaa. Tämä on `check_cloudflare()`
  jonka alkuperäinen koodisi jo sisälsi - se oli jo silloinkin merkki siitä että
  HLTV testaa Cloudflarella.
- **Selain kaatuu muistin loppumiseen (OOM)** → aja skraperi ja dashboard eri
  aikaan (systemd-ajastin jo tekee tämän, mutta jos testaat manuaalisesti,
  pysäytä dashboard hetkeksi: `docker compose stop dashboard`).

Kerro mitä `journalctl` näyttää, niin korjaan koodin sen mukaan - tätä ei voi
tietää etukäteen ilman oikeaa Pi:tä edessä.

## Päivitysten tekeminen jatkossa

Kun teet muutoksia koodiin (esim. tässä keskustelussa jatkossa) Windows-
koneellasi ja pushaat GitHubiin:

```bash
cd ~/cs2-performance-tracker
git pull
docker compose up -d --build
```

## Tiedostorakenne tämän paketin sisällä

```
docker-compose.yml          Koko pinon määrittely
dashboard/                  Streamlit-dashboardin Docker-image
  Dockerfile
  requirements.txt
  entrypoint.sh
  .streamlit/config.toml
scraper/                    HLTV-skraperin Docker-image (ARM64 Chromium)
  Dockerfile
  requirements.txt
  entrypoint.sh
systemd/                    Skraperin päivittäinen ajastus
  cs-scraper.service
  cs-scraper.timer
docs/
  01_ha_varmuuskopiointi_ja_pi_asennus.md
  02_tailscale.md
```
