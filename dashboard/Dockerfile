# Kevyt Python-image. Multi-arch: sama Dockerfile toimii sekä x86-64:llä (jos joskus
# testaat PC:llä) että ARM64:llä (Pi 5) - Docker valitsee oikean arkkitehtuurin
# automaattisesti build-koneen mukaan.
FROM python:3.11-slim

WORKDIR /app

COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vain oikeasti tarvittavat sovelluskooditiedostot, ei koko repoa (pienempi image,
# eikä .git tai kannat päädy vahingossa mukaan). betting_calculator.py EI ole mukana -
# se vaatii team_elo_calculator.py-tiedoston jota repossa ei ole, eikä dashboard.py
# käytä sitä mihinkään.
COPY dashboard.py players_props.py match_simulator.py ./
COPY dashboard/.streamlit ./.streamlit

COPY dashboard/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8501

# Streamlitin sisäänrakennettu terveystarkistus - Docker näkee jos sovellus jumiutuu.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["streamlit", "run", "dashboard.py"]
