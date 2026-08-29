"""
CS2-ottelun kierrosmäärän simulointi (MR12 + jatkoajat).

TAUSTA - miksi tämä korvaa vanhan sample_match_lengths-kaavan
------------------------------------------------------------
Vanha malli arvioi kierrosmäärän suoralla kaavalla:

    target_mean = 21.5 - |p - 0.5| * 2 * 8.5

Kerroin 8.5 ei ollut kalibroitu mihinkään (koodin kommentti myönsi tämän), ja
se on rajusti liian suuri. Suoraan kannan datasta ja kierrostason simulaatiosta
mitattuna todellinen yhteys on paljon loivempi:

    p(kartta)   kerroin   TODELLINEN E[kierr]   VANHA KAAVA   virhe
      0.50       2.00           21.8               21.6       -0.2
      0.65       1.54           21.7               19.0       -2.7
      0.75       1.33           21.5               17.3       -4.2
      0.85       1.18           21.0               15.6       -5.4
      0.90       1.11           20.6               14.8       -5.8

Huomaa miten LOIVA oikea käyrä on: koko realistisella kerroinvälillä
(2.00 -> 1.11) kierrosmäärä liikkuu vain 21.8 -> 20.6. Ennakkosuosikkius ei
juuri lyhennä karttaa. Tämä on varmistettu myös suoraan datasta: 1800 kartan
otoksessa toteutunut keskiarvo oli 21.75 / 21.89 / 21.59 kun kartat jaettiin
joukkueiden voimaeron mukaan koreihin - käytännössä vaakasuora.

Syy on yksinkertainen: kartta EI lopu nopeasti vaikka toinen olisi selvä
suosikki. Voittoon tarvitaan aina 13 kierrosta, ja häviäjä ottaa historiallisesti
keskimäärin 7.1 kierrosta myös hävityissä kartoissa. Alle ~19 kierroksen
keskiarvo edellyttäisi käytännössä 13-5-tason murskavoittoa joka ainoa kerta.

Koska tapot skaalautuvat suoraan kierrosmäärään, 5 kierroksen aliarvio 22:sta
tarkoittaa ~23 % liian matalia tappoprojektioita - juuri sitä mitä käytännössä
näkyi.

MITEN TÄMÄ TOIMII
-----------------
Kaavan sijaan simuloidaan itse kartta kierros kierrokselta:

1. Kertoimista puretaan marginaali -> kartan voittotodennäköisyys p.
2. p muunnetaan KIERROSVOITTOTODENNÄKÖISYYDEKSI r (analyyttinen inversio).
3. Kartta pelataan simulaatiossa: ensimmäinen 13:aan, 12-12 -> jatkoaika
   (MR3, ensimmäinen 4:ään jatkoajalla, 3-3 -> uusi jatkoaika).

Tämä tuottaa automaattisesti oikean muotoisen jakauman: mahdottomia
kokonaispistemääriä (25-27, 31-33) ei synny lainkaan, jatkoajan osuus tulee
oikein, eikä keskiarvoa tarvitse virittää käsin.

Kalibrointi kantaa vasten (2637 karttaa): keskiarvo 21.57, keskihajonta 4.98,
jatkoaikaprosentti 12.6. Simulaatio samoilla ehdoilla: 21.8 / 4.6 / 11.3.

Jos bookkeri tarjoaa kartalle kierrostotalin, anna se parametrina
target_mean_rounds - markkinan arvio kestosta voittaa mallin lähes aina.
"""

import sqlite3
from math import comb

import numpy as np

DB_PATH = 'hltv_data.db'

# Kartan sisäinen satunnaisuus: CS2:n kierrokset EIVÄT ole riippumattomia
# (momentum, talouskierre, puolikkaiden sivuvaihto), joten kartat ratkeavat
# selvästi useammin kuin kolikonheittomalli ennustaisi.
#
# KALIBROITU KANTAA VASTEN. 1800 kartalle laskettiin kummankin joukkueen
# kierrosvoitto-osuus leave-one-out -periaatteella, kartat jaettiin voimaeron
# mukaan koreihin ja toteutunutta kierrosmäärää verrattiin simulaatioon:
#
#   voimaero    toteutunut    sigma=0.04    sigma=0.10
#   0.00-0.02      21.75         22.84         21.75
#   0.02-0.04      21.89         22.70         21.69
#   0.04-0.06      21.59         22.48         21.56
#
# Ilman tätä malli yliarvioi kierrosmäärän noin yhdellä kierroksella
# (+5 % kaikkiin tappoprojektioihin).
#
# JAKAUMAN MUOTO: r arvotaan Studentin t-jakaumasta normaalijakauman sijaan.
# Normaali tuotti liian vähän lyhyitä karttoja (13-17 kierrosta) ja liikaa
# 21-23 kierroksen karttoja. Raskaampi häntä tuo murskavoitot mukaan oikeassa
# suhteessa. Koko kierrosjakauman poikkeama havaitusta (2637 karttaa):
#
#   normaali sigma=0.10 ...... 6.9 %   std 4.61   OT 11.5 %
#   t df=2.5 scale=0.070 ..... 5.9 %   std 4.75   OT 11.7 %   <- käytössä
#   (havaittu) ...............   -     std 4.98   OT 12.6 %
#
# Molemmat osuvat ehdolliseen keskiarvoon (21.76 vs. havaittu 21.75), joten
# t-jakauma on aidosti parempi ilman kompromissia tasossa.
DEFAULT_SIGMA_WITHIN = 0.070
R_DIST_DF = 2.5

# Joukkueen (5 pelaajan) tapot per kierros kierrososuuden funktiona.
# Sovitettu suoraan kannan datasta, ks. _load_kpr_share_coeffs. Nämä ovat
# varafallbackit jos kannasta ei saada sovitetta.
FALLBACK_KPR_INTERCEPT = 1.825
FALLBACK_KPR_SLOPE = 2.937

_KPR_COEFFS = None


# ---------------------------------------------------------------------------
# 1. Kartan voittotodennäköisyys kierrosvoittotodennäköisyydestä (analyyttinen)
# ---------------------------------------------------------------------------

def map_win_prob(r):
    """Todennäköisyys voittaa MR12-kartta, kun yksittäisen kierroksen
    voittotodennäköisyys on r.

    Regulaatio: ensimmäinen 13:aan, maksimissaan 24 kierrosta (12-12 -> jatkoaika).
    Jatkoaika: MR3, ensimmäinen 4:ään, 3-3 -> uusi jatkoaika (geometrinen sarja).
    """
    q = 1.0 - r
    # Voitto regulaatiossa: 13. kierros voitetaan kun vastustajalla on b = 0..11
    p_reg = sum(comb(12 + b, b) * r ** 13 * q ** b for b in range(12))
    # 12-12 tasan
    p_tie = comb(24, 12) * r ** 12 * q ** 12
    # Yksi jatkoaikajakso
    p_ot_period = sum(comb(3 + b, b) * r ** 4 * q ** b for b in range(3))
    p_ot_tie = comb(6, 3) * r ** 3 * q ** 3
    p_ot = p_ot_period / (1.0 - p_ot_tie)
    return p_reg + p_tie * p_ot


def round_prob_from_map_prob(p_map, lo=0.05, hi=0.95, tol=1e-9):
    """Käänteisfunktio: mikä kierrosvoittotodennäköisyys r vastaa annettua
    kartan voittotodennäköisyyttä p_map. map_win_prob on aidosti kasvava,
    joten puolitushaku riittää."""
    p_map = float(np.clip(p_map, 1e-4, 1 - 1e-4))
    for _ in range(200):
        mid = (lo + hi) / 2
        if map_win_prob(mid) < p_map:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# 2. Kartan simulointi kierros kierrokselta
# ---------------------------------------------------------------------------

def simulate_map(r, rng):
    """Simuloi kartat vektoroidusti. r on taulukko kierrosvoittotodennäköisyyksiä
    (yksi per simulaatio). Palauttaa (kierrokset_a, kierrokset_b)."""
    r = np.asarray(r, dtype=float)
    n = len(r)
    a = np.zeros(n, dtype=int)
    b = np.zeros(n, dtype=int)

    # Regulaatio: tasan 24 kierrosta silmukassa riittää, koska kartta päättyy
    # viimeistään lukemiin 13-11 tai 12-12.
    for _ in range(24):
        live = (a < 13) & (b < 13)
        if not live.any():
            break
        won = rng.random(n) < r
        a += (live & won).astype(int)
        b += (live & ~won).astype(int)

    # Jatkoajat: MR3, ensimmäinen 4:ään. 3-3 -> uusi jakso.
    in_ot = (a == 12) & (b == 12)
    guard = 0
    while in_ot.any() and guard < 25:
        guard += 1
        oa = np.zeros(n, dtype=int)
        ob = np.zeros(n, dtype=int)
        for _ in range(6):
            live = in_ot & (oa < 4) & (ob < 4)
            if not live.any():
                break
            won = rng.random(n) < r
            oa += (live & won).astype(int)
            ob += (live & ~won).astype(int)
        a += oa
        b += ob
        in_ot = in_ot & (oa == 3) & (ob == 3)

    return a, b


def _draw_round_probs(r_mid, scale, n, rng):
    """Arpoo kierrosvoittotodennäköisyydet Studentin t-jakaumasta keskitettynä
    r_mid:iin. Raskas häntä tuottaa murskavoitot oikeassa suhteessa - normaali-
    jakauma antaa niitä liian vähän (ks. tiedoston alun taulukko)."""
    if not scale or scale <= 0:
        return np.full(n, float(r_mid))
    return np.clip(r_mid + rng.standard_t(R_DIST_DF, n) * scale, 0.15, 0.85)


def devig_two_way(odds_a, odds_b):
    """Poistaa marginaalin kaksipuolisesta markkinasta -> (p_a, p_b, marginaali %)."""
    ia, ib = 1.0 / odds_a, 1.0 / odds_b
    tot = ia + ib
    return ia / tot, ib / tot, (tot - 1.0) * 100.0


def _solve_scale(r_mid, objective, lo=0.0, hi=0.35, iters=20):
    """Etsii hajontaparametrin, jolla objective(scale) = 0.

    objective on laskeva scalen suhteen (isompi hajonta -> ratkeavampia karttoja
    -> vähemmän kierroksia). Jokainen arvio käyttää SAMAA satunnaislukusiementä,
    jolloin funktio on sileä scalen suhteen eikä puolitushaku hyppele Monte
    Carlo -kohinan takia.
    """
    if objective(lo) < 0:
        return lo          # markkina odottaa PIDEMPÄÄ karttaa kuin malli pystyy
    if objective(hi) > 0:
        return hi          # ...tai lyhyempää kuin malli pystyy
    for _ in range(iters):
        mid = (lo + hi) / 2
        if objective(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _sigma_for_target_mean(r_mid, target_mean, seed=0, n=20000):
    """Hajonta, jolla simulaation KESKIARVO osuu tavoitteeseen."""
    def obj(sg):
        rng = np.random.default_rng(seed)
        a, b = simulate_map(_draw_round_probs(r_mid, sg, n, rng), rng)
        return float((a + b).mean()) - target_mean
    return _solve_scale(r_mid, obj)


def _sigma_for_total_line(r_mid, line, p_over, seed=0, n=20000):
    """Hajonta, jolla P(kierroksia > line) osuu markkinan (marginaalittomaan)
    todennäköisyyteen. Tämä on tarkempi tapa ankkuroida kuin pelkkä keskiarvo,
    koska se käyttää sekä linjaa ETTÄ kertoimia."""
    def obj(sg):
        rng = np.random.default_rng(seed)
        a, b = simulate_map(_draw_round_probs(r_mid, sg, n, rng), rng)
        return float(((a + b) > line).mean()) - p_over
    return _solve_scale(r_mid, obj)


def simulate_match_context(win_prob, num_simulations=10000,
                           sigma_within=DEFAULT_SIGMA_WITHIN, seed=None,
                           target_mean_rounds=None,
                           total_line=None, total_over_odds=None, total_under_odds=None):
    """Simuloi kartan num_simulations kertaa annetulla joukkueen 1
    voittotodennäköisyydellä.

    Palauttaa sanakirjan:
      'rounds'      - kokonaiskierrokset per simulaatio (int-taulukko)
      'share_t1'    - joukkueen 1 kierrososuus per simulaatio (0..1)
      'share_t2'    - joukkueen 2 kierrososuus per simulaatio
      'round_prob'  - käytetty kierrosvoittotodennäköisyys
      'mean_rounds', 'median_rounds', 'p_overtime', 'p10', 'p90'

    HUOM: sama palautettu konteksti annetaan MOLEMPIEN joukkueiden
    simulate_team_kills-kutsuille, jotta pelaajat pelaavat saman simuloidun
    ottelun samassa simulaatiokierroksessa.
    """
    rng = np.random.default_rng(seed)
    r_mid = round_prob_from_map_prob(win_prob)

    # ANKKUROINTI MARKKINAAN. Jos Pinnacle (tai muu terävä kirja) tarjoaa kartalle
    # kierrostotalin, se on parempi arvio kestosta kuin mikään historiaan sovitettu
    # malli: siinä on mukana kartta, kokoonpanot, LAN/online ja kaikki mitä kanta ei
    # tunne. Simulaatiota ei kuitenkaan voi poistaa - totalista saa vain KESKIKOHDAN,
    # kun taas tappotodennäköisyyksiin tarvitaan koko kierrosjakauma (jatkoaikahäntä
    # mukaan lukien). Ankkurointi ottaa markkinalta tason ja jättää mallille muodon.
    #
    # Tarkin tapa on antaa linja JA molemmat kertoimet: silloin marginaali poistetaan
    # ja simulaatio viritetään osumaan markkinan todelliseen P(yli)-arvioon.
    # Pelkkä linja tulkitaan mediaaniksi (P(yli) = 50 %).
    if total_line is not None:
        if total_over_odds and total_under_odds and total_over_odds > 1 and total_under_odds > 1:
            p_over, _p_under, _margin = devig_two_way(total_over_odds, total_under_odds)
        else:
            p_over = 0.5
        sigma_within = _sigma_for_total_line(r_mid, float(total_line), p_over, seed or 0)
    elif target_mean_rounds is not None:
        sigma_within = _sigma_for_target_mean(r_mid, float(target_mean_rounds), seed or 0)

    r = _draw_round_probs(r_mid, sigma_within, num_simulations, rng)

    a, b = simulate_map(r, rng)
    total = a + b

    return {
        'rounds': total,
        'share_t1': a / total,
        'share_t2': b / total,
        'round_prob': r_mid,
        'mean_rounds': float(total.mean()),
        'median_rounds': float(np.median(total)),
        'p_overtime': float((total >= 28).mean()),
        'p10': float(np.percentile(total, 10)),
        'p90': float(np.percentile(total, 90)),
        'sigma_within': float(sigma_within),
    }


# ---------------------------------------------------------------------------
# 3. Joukkueen tappotaso kierrososuuden funktiona (sovitetaan omasta kannasta)
# ---------------------------------------------------------------------------

def _load_kpr_share_coeffs(db_path=DB_PATH):
    """Sovittaa kannasta suoran: joukkueen (5 pelaajan) tapot per kierros
    = intercept + slope * kierrososuus.

    Tämä korvaa vanhan käsin asetetun kertoimen
        eff_multiplier = 1 + (win_prob - 0.5) * 0.50
    joka a) perustui ENNAKKOtodennäköisyyteen eikä toteutuneeseen kierrososuuteen
    ja b) oli noin kaksi kertaa liian jyrkkä mitattuun dataan verrattuna.

    Kun kerroin sidotaan simulaation TOTEUTUNEESEEN kierrososuuteen, saadaan myös
    oikea korrelaatio: kartassa jossa joukkue murskataan 13-4 pelaajilla on sekä
    vähän kierroksia että matala tappotahti - vanha malli käsitteli nämä erillisinä.
    """
    global _KPR_COEFFS
    if _KPR_COEFFS is not None:
        return _KPR_COEFFS

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT s.match_id, p.team_id, SUM(s.kills), COUNT(*),
                   m.score_team1, m.score_team2, m.team1_id, m.team2_id
            FROM player_stats s
            JOIN players p ON p.id = s.player_id
            JOIN matches m ON m.id = s.match_id
            GROUP BY s.match_id, p.team_id
        """)
        rows = cur.fetchall()
        conn.close()

        shares, kprs = [], []
        for _mid, team_id, kills, n_players, s1, s2, t1, t2 in rows:
            if n_players != 5:
                continue
            if team_id == t1:
                won = s1
            elif team_id == t2:
                won = s2
            else:
                continue
            total = (s1 or 0) + (s2 or 0)
            if total < 13:
                continue
            shares.append(won / total)
            kprs.append(kills / total)

        if len(shares) < 200:
            raise ValueError("liian pieni otos")

        slope, intercept = np.polyfit(np.array(shares), np.array(kprs), 1)
        _KPR_COEFFS = (float(intercept), float(slope))
    except Exception:
        _KPR_COEFFS = (FALLBACK_KPR_INTERCEPT, FALLBACK_KPR_SLOPE)

    return _KPR_COEFFS


def kpr_share_multiplier(share, db_path=DB_PATH):
    """Kerroin jolla pelaajan perus-KPR skaalataan, kun joukkueen kierrososuus
    simulaatiossa on `share`. Normalisoitu niin että osuudella 0.5 kerroin = 1.0."""
    intercept, slope = _load_kpr_share_coeffs(db_path)
    base = intercept + slope * 0.5
    if base <= 0:
        return np.ones_like(np.asarray(share, dtype=float))
    return (intercept + slope * np.asarray(share, dtype=float)) / base


# ---------------------------------------------------------------------------
# EI VIELÄ KALIBROITAVISSA: sigma_within
# ---------------------------------------------------------------------------
# sigma_within kuvaa sitä osaa kartan epävarmuudesta jota kertoimet eivät selitä.
# Sitä ei voi erottaa datasta ennen kuin kannassa on JOKAISEN kartan kertoimet.
# Käytännön askel: lisää matches-tauluun sarakkeet odds_team1 / odds_team2 ja
# tallenna ne otteluita kirjatessa. Sen jälkeen sigma_within voidaan hakea
# suoraan: etsi arvo, jolla simuloitu kierrosjakauma vastaa toteutunutta,
# kun jokaisen kartan p lasketaan sen omista kertoimista.

if __name__ == "__main__":
    print("p(kartta)  kerroin   r(kierros)   E[kierroksia]   jatkoaika-%")
    for p in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
        ctx = simulate_match_context(p, 40000, seed=1)
        print(f"  {p:.2f}     {1/p:6.2f}      {ctx['round_prob']:.4f}"
              f"        {ctx['mean_rounds']:6.2f}         {ctx['p_overtime']*100:5.1f} %")
