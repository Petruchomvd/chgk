import re
from typing import Optional

MONTHS = {
    '\u044f\u043d\u0432\u0430\u0440\u044f': '01',
    '\u0444\u0435\u0432\u0440\u0430\u043b\u044f': '02',
    '\u043c\u0430\u0440\u0442\u0430': '03',
    '\u0430\u043f\u0440\u0435\u043b\u044f': '04',
    '\u043c\u0430\u044f': '05',
    '\u0438\u044e\u043d\u044f': '06',
    '\u0438\u044e\u043b\u044f': '07',
    '\u0430\u0432\u0433\u0443\u0441\u0442\u0430': '08',
    '\u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044f': '09',
    '\u043e\u043a\u0442\u044f\u0431\u0440\u044f': '10',
    '\u043d\u043e\u044f\u0431\u0440\u044f': '11',
    '\u0434\u0435\u043a\u0430\u0431\u0440\u044f': '12',
}


def parse_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None

    normalized = date_str.strip().lower()
    normalized = re.sub(r'\s*\u0433\.?$', '', normalized)  # drop trailing "\u0433." (year marker)

    match = re.match(r'(\d{1,2})\s+(\w+)\s+(\d{4})', normalized)
    if not match:
        return None

    day, month_str, year = match.groups()
    month = MONTHS.get(month_str)
    if not month:
        return None

    return f'{year}-{month}-{day.zfill(2)}'


def save_game(game_number: int) -> None:
    with open('saved_games_db.txt', 'a', encoding='utf-8') as handle:
        handle.write(f'{game_number}\n')


def checke_if_saved(game_number: int) -> bool:
    try:
        with open('saved_games_db.txt', 'r', encoding='utf-8') as handle:
            saved_games = {int(line.strip()) for line in handle}
        return game_number in saved_games
    except FileNotFoundError:
        return False
