"""Patchaa chromedriver-binaarin (build-aikana, Dockerfile kutsuu tata):
korvaa Selenium/ChromeDriverin jokaiselle sivulle injektoiman
window.cdc_... -tunnistemerkin harmittomalla tekstilla.

Monet bottitunnistusjarjestelmat (mm. Cloudflaren tiukemmat suojaustasot,
kuten HLTV:n /stats/-polulla havaittiin) tarkistavat taman JS-muuttujan
olemassaolon sivulla tunnistaakseen automatisoidun selaimen. Tama on sama
patch jonka undetected_chromedriver normaalisti tekisi automaattisesti -
mutta sen oma lataus/patch-logiikka ei toimi ARM64:lla, koska se yrittaa
hakea x86-64-ajurin Googlen arkistosta (ks. webScrape.py:n kommentit ja
GitHub-issue ultrafunkamsterdam/undetected-chromedriver#917). Patchaamme
saman asian itse suoraan Debianin valmiiksi asentamaan ARM64-ajuriin.
"""
import re

PATH = "/usr/bin/chromedriver"

with open(PATH, "rb") as f:
    content = f.read()

match = re.search(rb"\{window\.cdc.*?;\}", content)
if not match:
    raise SystemExit(
        "[VIRHE] cdc-tunnistemerkkia ei loytynyt chromedriverista - "
        "Debianin chromium-driver-paketin versio on saattanut muuttua "
        "tavalla joka rikkoo tamas patchin. Tarkista kasin."
    )

target = match.group(0)
replacement = b'{console.log("patched")}'.ljust(len(target), b" ")
content = content.replace(target, replacement)

with open(PATH, "wb") as f:
    f.write(content)

print(f"[i] chromedriver patchattu onnistuneesti ({PATH})")