import sqlite3
import pandas as pd
import math

BASE_ELO = 1500
K_FACTOR = 40

"""laskee joukkueen A voittotodennäköisuyyden"""
def get_expected_score(rating_a, rating_b):
    return 1 / (1 + math.pow(10, (rating_b - rating_a) / 400))


"""laskee joukkueen A uuden ELO-luokituksen"""
def calculate_map_elos():
    conn = sqlite3.connect('hltv_data.db')

    query = """
    SELECT id, team1_id, team2_id, score_team1, score_team2, map_name
    FROM matches
    ORDER BY CAST(id AS INTEGER) ASC
    """
    matches = pd.read_sql_query(query, conn)

    teams_df = pd.read_sql_query("SELECT id, name FROM teams", conn)
    team_names = dict(zip(teams_df['id'], teams_df['name']))

    conn.close()

    map_elos = {}

    for index, row in matches.iterrows():
        t1 = row['team1_id']
        t2 = row['team2_id']
        s1 = row['score_team1']
        s2 = row['score_team2']
        map_name = row['map_name']

        if s1 == s2:
            continue

        if map_name not in map_elos:
            map_elos[map_name] = {}
        if t1 not in map_elos[map_name]:
            map_elos[map_name][t1] = BASE_ELO
        if t2 not in map_elos[map_name]:
            map_elos[map_name][t2] = BASE_ELO

        elo1 = map_elos[map_name][t1]
        elo2 = map_elos[map_name][t2]

        exp1 = get_expected_score(elo1, elo2)
        exp2 = get_expected_score(elo2, elo1)

        actual1 = 1 if s1 > s2 else 0
        actual2 = 1 if s2 > s1 else 0

        round_diff = abs(s1 - s2)
        mov_multiplier = math.log(round_diff + 1) if round_diff > 0 else 1

        map_elos[map_name][t1] = elo1 + (K_FACTOR * mov_multiplier * (actual1 - exp1))
        map_elos[map_name][t2] = elo2 + (K_FACTOR * mov_multiplier * (actual2 - exp2))

    return map_elos, team_names

if __name__ == "__main__":
    elos, names = calculate_map_elos()

    conn = sqlite3.connect('hltv_data.db')
    matches_df = pd.read_sql_query("SELECT team1_id, team2_id, map_name FROM matches", conn)
    conn.close()

    games_played = {}
    for index, row in matches_df.iterrows():
        t1, t2, m_name = row['team1_id'], row['team2_id'], row['map_name']
        if m_name not in games_played:
            games_played[m_name] = {}

        games_played[m_name][t1] = games_played[m_name].get(t1, 0) + 1
        games_played[m_name][t2] = games_played[m_name].get(t2, 0) + 1

    map_to_check = "Mirage"
    MIN_GAMES_REQUIRED = 5

    if map_to_check in elos:
        print(f"--- TOP 10 JOUKKUEET: {map_to_check.upper()} (Väh. {MIN_GAMES_REQUIRED} peliä) ---")

        luotettavat_tiimit = {
            team_id: score
            for team_id, score in elos[map_to_check].items()
            if games_played.get(map_to_check, {}).get(team_id, 0) >= MIN_GAMES_REQUIRED
        }

        sorted_teams = sorted(luotettavat_tiimit.items(), key=lambda x: x[1], reverse=True)

        for rank, (team_id, elo_score) in enumerate(sorted_teams[:10], 1):
            team_name = names.get(team_id, f"Tuntematon joukkue ({team_id})")
            pelatut_pelit = games_played[map_to_check][team_id]
            print(f"{rank}. {team_name:<20} | Elo: {int(elo_score)} (pelattu: {pelatut_pelit})")
