# Uusi käyttöliittymä (FastAPI + HTMX). Build-konteksti on Web_scrape-kansio (ks. docker-compose.yml).
# Sama jaettu data-volume kuin Streamlit-dashboardilla ja scraperilla.
FROM python:3.11-slim

WORKDIR /app

COPY webapp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vain tarvittavat tiedostot: malli ja kalibrointi jaetaan Streamlit-sovelluksen kanssa.
COPY players_props.py match_simulator.py bet_calibration.py ./
COPY webapp ./webapp
# Linkittää data-volumen tiedostot (hltv_data.db, my_bets.db) työhakemistoon
COPY dashboard/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONUNBUFFERED=1 TZ=Europe/Helsinki
EXPOSE 8502

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8502/health')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "webapp.app:app", "--host", "0.0.0.0", "--port", "8502"]
