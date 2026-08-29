#!/bin/sh
# HUOM: Xvfb-kaarinta poistettu - webScrape.py kaynnistaa Chromen suoraan
# --headless=new -tilassa Docker/ARM64-polulla (ks. webScrape.py:n
# _build_driver()-funktion kommentit), koska Xvfb+"headed" Chrome jai
# pysyvasti jumiin Pi 5:lla.
set -e

mkdir -p /app/db_data
ln -sf /app/db_data/hltv_data.db /app/hltv_data.db

exec "$@"
