# Vaihe 1: Home Assistantin varmuuskopiointi ja Pi:n uudelleenasennus

Tämä on riskialttein vaihe koko projektissa (levy tyhjennetään kokonaan), joten
tee jokainen kohta järjestyksessä äläkä ohita varmuuskopio-osiota.

## 1.1 Ota Home Assistantista täysi varmuuskopio

1. Avaa Home Assistantin web-käyttöliittymä.
2. **Asetukset → Järjestelmä → Varmuuskopiot** (Settings → System → Backups).
3. Luo **uusi täysi varmuuskopio** ("Create backup" → "Full backup"). Anna sille
   selkeä nimi, esim. `ennen-pi-uudelleenasennusta`.
4. Kun varmuuskopio on valmis, **lataa se koneellesi** (kolme pistettä
   varmuuskopion kohdalla → Download). Tiedosto on `.tar`-muotoinen.
5. **Tärkeää:** siirrä tämä `.tar`-tiedosto pois itse Pi:ltä - esim. samaan
   `CS_betting`-kansioon OneDrivessa, jolloin se on turvassa vaikka SD-kortti
   tyhjennetään. Jos varmuuskopio ei ole ladattavissa tai lataus epäonnistuu,
   **älä jatka seuraavaan vaiheeseen** - selvitä ongelma ensin.

## 1.2 Lataa ja flashaa Raspberry Pi OS

1. Lataa [Raspberry Pi Imager](https://www.raspberrypi.com/software/) koneellesi.
2. Aseta Pi:n muistikortti/SSD koneeseen ja avaa Raspberry Pi Imager.
3. Valitse käyttöjärjestelmäksi **"Raspberry Pi OS Lite (64-bit)"** - ei
   Desktop-versiota, koska palvelin ei tarvitse graafista työpöytää (säästää
   RAM:ia 4 Gt:n Pi:llä, mikä on tärkeää kun HA + dashboard + skraperi jakavat
   saman muistin).
4. Imagerin asetuksista (rataskuvake ennen kirjoitusta):
   - Aseta isäntänimi, esim. `cs-server`
   - **Ota SSH käyttöön** ja aseta salasana (tai avain, jos käytät sellaista)
   - Aseta WiFi-tunnukset jos et käytä verkkokaapelia
   - Aseta käyttäjätunnus/salasana
5. Kirjoita levykuva kortille/levylle ja käynnistä Pi.

## 1.3 Ensimmäinen kirjautuminen ja päivitys

```bash
ssh <käyttäjätunnus>@cs-server.local
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

## 1.4 Asenna Docker ja Docker Compose

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Kirjaudu ulos ja takaisin sisään (`exit`, sitten `ssh` uudelleen), jotta
ryhmäjäsenyys astuu voimaan. Tarkista asennus:

```bash
docker --version
docker compose version
```

## 1.5 Palauta Home Assistant konttina

1. Siirrä 1.1-kohdan varmuuskopio-`.tar`-tiedosto Pi:lle (esim. `scp`:llä
   koneeltasi, kun molemmat ovat samassa verkossa - Tailscalen asennuksen
   jälkeen tämä onnistuu myös etänä, ks. `docs/02_tailscale.md`):

   ```bash
   scp ennen-pi-uudelleenasennusta.tar <käyttäjätunnus>@cs-server.local:~/
   ```

2. Luo projektikansio ja käynnistä Home Assistant ensin YKSIN (ei vielä koko
   pinoa), jotta pääset tekemään palautuksen sen omasta ensiasennus-ohjatusta
   toiminnosta:

   ```bash
   mkdir -p ~/cs2-performance-tracker/homeassistant
   cd ~/cs2-performance-tracker
   # docker-compose.yml tähän kansioon - ks. README.md päävaiheet
   docker compose up -d homeassistant
   ```

3. Avaa `http://cs-server.local:8123` selaimessa. Ensiasennussivulla on
   yläkulmassa/alareunassa vaihtoehto **"Restore from backup"** - valitse se
   ohitetaksesi tavallisen ensiasennuksen, ja lataa 5.1-kohdan `.tar`-tiedosto.
4. Odota palautuksen valmistumista (voi kestää useita minuutteja). HA
   käynnistyy uudelleen automaattisesti ja sinulla pitäisi olla kaikki vanhat
   automaatiot/integraatiot ennallaan.

## 1.6 Vasta tämän jälkeen: koko pino käyntiin

Kun Home Assistant on varmasti palautunut ja toimii, jatka README.md:n
pääohjeisiin (`docker compose up -d` koko pinolle, kansiorakenteen kopiointi
jne.) ja `docs/02_tailscale.md`:hen etäkäyttöä varten.

---

### Jos jokin menee pieleen

- **Palautus epäonnistuu / HA ei käynnisty:** älä formatoi mitään uudelleen -
  varmuuskopio-`.tar` on koskematon koneellasi, voit yrittää `docker compose
  down -v homeassistant && docker compose up -d homeassistant` ja palautuksen
  uudelleen puhtaalta pöydältä.
- **Et pääse SSH:lla sisään:** kokeile Pi:n IP-osoitetta `cs-server.local`:in
  sijaan (löytyy reitittimen laitelistasta), tai liitä näyttö+näppäimistö
  suoraan Pi:hin.
