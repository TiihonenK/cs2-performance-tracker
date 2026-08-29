# Vaihe 2: Tailscale - etäkäyttö ilman porttien avaamista

Tailscale luo yksityisen verkon ("tailnet") laitteidesi välille. Kun Pi ja
puhelimesi/kannettavasi ovat molemmat samassa tailnetissa, pääset dashboardiin
täsmälleen kuin olisit kotiverkossa - ei porttien avaamista reitittimestä, ei
julkista IP-osoitetta, ei dynaamista DNS:ää.

## 2.1 Luo tili ja asenna Pi:lle

1. Luo tili osoitteessa [tailscale.com](https://tailscale.com) (ilmainen
   henkilökohtainen käyttö riittää tähän - max 3 laitetta ilmaiseksi, tämä
   käyttää kahta: Pi + oma laitteesi).
2. Asenna Pi:lle SSH-istunnossa:

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```

3. Komento tulostaa linkin - avaa se selaimessa ja kirjaudu Tailscale-tilillesi
   hyväksyäksesi Pi:n verkkoon.

## 2.2 Asenna omille laitteillesi

Asenna Tailscale samalla tilillä:
- **Puhelin:** Tailscale App Storesta/Play Storesta
- **Kannettava/toinen PC:** [tailscale.com/download](https://tailscale.com/download)

## 2.3 Löydä Pi:n tailnet-osoite

```bash
tailscale ip -4
```

Tämä antaa Pi:lle pysyvän osoitteen (esim. `100.x.x.x`), joka toimii
täsmälleen samoin oli sitten kotiverkossa tai mobiilidatalla missä tahansa.
Vaihtoehtoisesti Tailscale antaa myös nimen muotoa `cs-server.<tailnet-nimi>.ts.net`
- tarkista tarkka muoto Tailscalen admin-konsolista (login.tailscale.com/admin/machines).

## 2.4 Avaa dashboard

Kun Tailscale on päällä puhelimessa/kannettavassa:

```
http://100.x.x.x:8501
```

tai vastaava `.ts.net`-osoite. Tämä toimii yhtä lailla kotona kuin liikkeellä -
Tailscale hoitaa reitityksen taustalla, eikä ero näy sinulle mitenkään.

## 2.5 (Valinnainen) Siisti HTTPS-osoite Tailscale Servellä

Jos haluat `https://`-osoitteen ilman porttinumeroa:

```bash
sudo tailscale serve --bg https / http://localhost:8501
```

Tämän jälkeen dashboard löytyy osoitteesta
`https://cs-server.<tailnet-nimi>.ts.net` - vain tailnetissäsi oleville
laitteille, ei julkisesti netissä.

## 2.6 (Valinnainen mutta suositeltava) Salasanasuojaus

Tailscale itsessään jo rajaa pääsyn vain sinun laitteillesi, joten tämä ei ole
pakollinen - mutta jos joskus jaat tailnetin muille (esim. perheenjäsenille
muihin tarkoituksiin), kannattaa lisätä kevyt salasanasuojaus dashboardiin:

```bash
pip install streamlit-authenticator
```

ja pieni lisäys `dashboard.py`:n alkuun. Kerro jos haluat että teen tämän -
jätin sen pois nyt, koska Tailscale-yksinään on jo riittävä suoja henkilökohtaiseen
käyttöön eikä kannata monimutkaistaa sovellusta turhaan.
