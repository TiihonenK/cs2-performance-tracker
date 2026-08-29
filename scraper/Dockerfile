# HUOM ARKKITEHTUURISTA: tämä image rakennetaan Pi:llä itsellään
# ("docker compose build" ajettuna Pi:n omassa terminaalissa), jolloin Docker
# hakee automaattisesti ARM64-versiot kaikista paketeista. Ei cross-buildia,
# ei manuaalista arkkitehtuurivalintaa - tämä on tarkoituksella sama Dockerfile
# jota käytettäisiin x86-64:llekin, Debianin pakettivarasto hoitaa loput.
FROM python:3.11-slim

# HUOM: chromium/chromium-driver EIVÄT ole enää mukana tässä imagessa.
# Kokeilimme pitkään ajaa HLTV:n scrapea suoraan Seleniumilla/chromedriverilla
# containerissa (Xvfb, --headless=new, chromedriverin cdc_-tunnistemerkin
# manuaalinen patchaus, kattava navigator/WebGL-stealth-skripti ym. - ks.
# WebScrape.py:n git-historia) - mikään näistä ei riittänyt läpäisemään
# HLTV:n /stats/-sivujen Cloudflare Managed Challenge / Turnstile-suojausta,
# vaikka /ranking/teams meni aina läpi ongelmitta. Sen sijaan HLTV-sivut
# haetaan nyt FlareSolverr-sivukontin kautta (ks. docker-compose.yml) - se
# ajaa oman, aktiivisesti ylläpidetyn selainautomaationsa erillisessä
# kontissa, joten tämä image ei tarvitse omaa selainta lainkaan.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Estää Streamlit/pandas-tyylisten kirjastojen "onko uudempi versio" -verkkokutsuja
# ja pitää Python-tulosteen reaaliaikaisena journalctl-lokeissa (ks. systemd/).
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY scraper/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY WebScrape.py ./

COPY scraper/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "WebScrape.py"]
