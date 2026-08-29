#!/bin/sh
# Linkittää jaetun /app/db_data-volumen tiedostot niihin nimiin joita
# dashboard.py/players_props.py odottavat (hltv_data.db, my_bets.db) -
# TÄYSIN ILMAN muutoksia itse Python-koodiin. Sama volume on scraper-
# kontilla, joten molemmat lukevat/kirjoittavat samoja tiedostoja.
set -e

mkdir -p /app/db_data
ln -sf /app/db_data/hltv_data.db /app/hltv_data.db
ln -sf /app/db_data/my_bets.db /app/my_bets.db

exec "$@"
