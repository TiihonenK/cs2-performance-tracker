import math
from team_elo_calculator import calculate_map_elos, get_expected_score

def find_team_id(team_name, names_dict):
    """Etsii joukkueen ID-numeron nimen perusteella."""
    for t_id, t_name in names_dict.items():
        if t_name.lower() == team_name.lower():
            return t_id
    return None

def calculate_odds(team1_name, team2_name):
    print("\nLasketaan tiimien Elot tietokannasta... Pieni hetki.")
    elos, names = calculate_map_elos()

    t1_id = find_team_id(team1_name, names)
    t2_id = find_team_id(team2_name, names)

    if not t1_id:
        print(f"Tiimiä '{team1_name}' ei löytynyt tietokannasta.")
        return
    if not t2_id:
        print(f"Tiimiä '{team2_name}' ei löytynyt tietokannasta.")
        return

    print(f"\n=========================================================================================")
    print(f"  OTTELUENNUSTE: {names[t1_id].upper()} vs {names[t2_id].upper()}")
    print(f"=========================================================================================")
    print(f"{'Kartta':<12} | {'Voitto % ' + names[t1_id]:<16} | {'Voitto % ' + names[t2_id]:<16} | {'Kerroinraja 1':<15} | {'Kerroinraja 2'}")
    print("-" * 89)

    for map_name, map_data in elos.items():
        elo1 = map_data.get(t1_id, 1500)
        elo2 = map_data.get(t2_id, 1500)

        if elo1 == 1500 and elo2 == 1500:
            continue

        prob1 = get_expected_score(elo1, elo2)
        prob2 = get_expected_score(elo2, elo1)

        odds1 = 1 / prob1
        odds2 = 1 / prob2

        print(f"{map_name:<12} | {prob1*100:>14.1f} % | {prob2*100:>14.1f} % | {odds1:>15.2f} | {odds2:>13.2f}")

    print("-" * 89)
    print("VINKKI: Etsi vedonvälittäjältä kertoimia, jotka ovat SUUREMPIA kuin yllä olevat kerroinrajat.\n")

if __name__ == "__main__":
    print("\n--- CS2 VEDONLYÖNTILASKURI ---")
    print("Vinkki: Kirjoita nimet niin kuin ne ovat HLTV:ssä (esim. 'Natus Vincere', ei 'Navi')")
    
    team_a = input("Anna 1. joukkueen nimi: ")
    team_b = input("Anna 2. joukkueen nimi: ")
    
    calculate_odds(team_a, team_b)