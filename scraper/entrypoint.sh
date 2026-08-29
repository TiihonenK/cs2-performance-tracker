#!/bin/sh
# Sama linkitysperiaate kuin dashboard/entrypoint.sh - jaettu /app/db_data-volume
# linkitetään koodin odottamaan tiedostonimeen ilman koodimuutoksia.
set -e

mkdir -p /app/db_data
ln -sf /app/db_data/hltv_data.db /app/hltv_data.db

# xvfb-run käynnistää virtuaalinäytön ja ajaa komennon sen sisällä - Chromium
# näkee "oikean" ruudun eikä tarvitse --headless-lippua (ks. Dockerfile-kommentit).
exec xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" "$@"
