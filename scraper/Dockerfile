# HUOM ARKKITEHTUURISTA: tämä image rakennetaan Pi:llä itsellään
# ("docker compose build" ajettuna Pi:n omassa terminaalissa), jolloin Docker
# hakee automaattisesti ARM64-versiot kaikista paketeista. Ei cross-buildia,
# ei manuaalista arkkitehtuurivalintaa - tämä on tarkoituksella sama Dockerfile
# jota käytettäisiin x86-64:llekin, Debianin pakettivarasto hoitaa loput.
FROM python:3.11-slim

# chromium + chromium-driver: Debianin OMAT ARM64-buildit, aina keskenään
# versiosynkassa apt-päivitysten kautta. EI Google Chrome for Testingiä eikä
# undetected_chromedriverin automaattilatausta - ks. perustelu webScrape.py:n
# _build_driver()-funktion kommenteista.
# xvfb: virtuaalinäyttö, jotta Chromium ajetaan "headed"-tilassa ilman
# --headless-lippua (vähemmän havaittavissa Cloudflaren tarkistukselle).
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        xvfb \
        xauth \
        ca-certificates \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_BIN=/usr/bin/chromedriver
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
