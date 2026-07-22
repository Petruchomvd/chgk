def save_game(game_number: int) -> None:
    with open("saved_games_db.txt", "a", encoding="utf-8") as handle:
        handle.write(f"{game_number}\n")


def checke_if_saved(game_number: int) -> bool:
    try:
        with open("saved_games_db.txt", "r", encoding="utf-8") as handle:
            saved_games = {int(line.strip()) for line in handle}
        return game_number in saved_games
    except FileNotFoundError:
        return False
