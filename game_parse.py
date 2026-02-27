import json
import re
import time
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from modules_pars.utils import parse_date

REQUEST_TIMEOUT = 10
INDEX_URL = "https://gotquestions.online/"
PACK_URL_TEMPLATE = "https://gotquestions.online/pack/{game_id}"
INDEX_EXTRACTION_TIMEOUT = 12

INFO_SUBSTRINGS = {
    "questions": ['\u0432\u043e\u043f\u0440'],
    "start_date": ['\u043d\u0430\u0447\u0430\u043b'],
    "end_date": ['\u043e\u043a\u043e\u043d', '\u0437\u0430\u0432\u0435\u0440\u0448'],
    "published_date": ['\u043e\u043f\u0443\u0431\u043b\u0438\u043a', '\u043f\u0443\u0431\u043b\u0438\u043a\u0430'],
    "teams_played": ['\u043a\u043e\u043c\u0430\u043d\u0434'],
    "difficulty": ['\u0441\u043b\u043e\u0436\u043d'],
}


def get_last_game(driver=None, session: Optional[requests.Session] = None) -> Optional[int]:
    """Return the latest available pack id using Selenium when possible."""
    if driver:
        metadata = _extract_index_metadata_with_driver(driver)
        if metadata:
            print(f'Latest available game id: {metadata["max_id"]} (total packs listed: {metadata["count"]})')
            return metadata["max_id"]
        print('Failed to extract latest game id via Selenium, falling back to HTTP probing.')

    if session:
        try:
            response = session.get(INDEX_URL, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            container = soup.find('div', class_='flex flex-col')
            if container:
                numbers = []
                for link in container.find_all('a', href=True):
                    parts = link['href'].strip('/').split('/')
                    if len(parts) >= 2 and parts[0] == 'pack' and parts[-1].isdigit():
                        numbers.append(int(parts[-1]))
                if numbers:
                    last_game = max(numbers)
                    print(f'Latest available game id: {last_game}')
                    return last_game
        except requests.RequestException:
            pass

    print('Unable to determine latest game id automatically.')
    return None


def game_parsing(
    game_number: int,
    driver,
    connection: Optional[Any] = None,
    cursor: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Parse pack page and return structured data."""
    url = PACK_URL_TEMPLATE.format(game_id=game_number)
    print(f'Parsing game #{game_number}')

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        _record_failed_game(game_number, f'HTTP error: {exc}', connection, cursor)
        return None

    if response.status_code == 404:
        _record_failed_game(game_number, 'HTTP 404: pack not found', connection, cursor)
        return None

    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'h1.text-2xl')))
    except TimeoutException as exc:
        _record_failed_game(game_number, f'Selenium timeout: {exc}', connection, cursor)
        return None

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    data = _extract_game_data(game_number, url, soup)
    if not data:
        _record_failed_game(game_number, 'Unable to extract game data', connection, cursor)
        return None

    print(data)
    return data


def _extract_index_metadata_with_driver(driver) -> Optional[Dict[str, int]]:
    try:
        driver.get(INDEX_URL)
    except Exception:
        return None

    deadline = time.time() + INDEX_EXTRACTION_TIMEOUT
    html = driver.page_source
    while time.time() < deadline:
        html = driver.page_source
        if '{"packs":' in html or '{\\"packs\\":' in html:
            metadata = _parse_index_blob(html)
            if metadata:
                return metadata
        time.sleep(0.5)
    return _parse_index_blob(html)


def _parse_index_blob(html: str) -> Optional[Dict[str, int]]:
    marker = '{\\"packs\\":'
    start = html.find(marker)
    if start == -1:
        return None
    tail = html[start:]
    try:
        decoded = bytes(tail, 'utf-8').decode('unicode_escape', errors='ignore')
    except Exception:
        return None

    match = re.search(r'({"packs":\[.*?"count":\d+.*?"page":".*?"})', decoded, re.S)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    packs = data.get("packs")
    if not packs:
        return None
    max_id = max((pack.get('id', 0) for pack in packs if isinstance(pack, dict) and pack.get('id')), default=None)
    if not max_id:
        return None
    return {"max_id": max_id, "count": data.get("count", len(packs))}


def _extract_game_data(game_number: int, url: str, soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    game_name_tag = soup.find('h1', class_='text-2xl font-comfortaa')
    game_name = game_name_tag.get_text(strip=True) if game_name_tag else None

    info_map = _build_info_map(soup)
    if not game_name and not info_map:
        return None

    questions_raw = _find_value(info_map, INFO_SUBSTRINGS["questions"])
    start_raw = _find_value(info_map, INFO_SUBSTRINGS["start_date"])
    end_raw = _find_value(info_map, INFO_SUBSTRINGS["end_date"])
    published_raw = _find_value(info_map, INFO_SUBSTRINGS["published_date"])
    teams_raw = _find_value(info_map, INFO_SUBSTRINGS["teams_played"])
    difficulty_raw = _find_value(info_map, INFO_SUBSTRINGS["difficulty"])

    number_of_questions = _extract_first_int(questions_raw)
    game_start_date = parse_date(start_raw) if start_raw else None
    game_end_date = parse_date(end_raw) if end_raw else None
    game_published_date = parse_date(published_raw) if published_raw else None
    commands_played = _extract_sum_of_ints(teams_raw)
    difficulty = _calculate_average(difficulty_raw)

    authors_block = soup.find('div', class_='flex flex-wrap gap-1')
    authors = []
    if authors_block:
        for tag in authors_block.find_all('a', href=lambda href: href and "/person" in href):
            author_name = tag.get_text(strip=True)
            if author_name:
                authors.append(author_name)

    return {
        "id": game_number,
        "name": game_name,
        "number_of_questions": number_of_questions,
        "start_date": game_start_date,
        "end_date": game_end_date,
        "published_date": game_published_date,
        "teams_played": commands_played,
        "difficulty": difficulty,
        "authors": ', '.join(authors) if authors else None,
        "link": url,
    }


def _build_info_map(soup: BeautifulSoup) -> Dict[str, str]:
    info: Dict[str, str] = {}
    for block in soup.select('div.flex.justify-between'):
        key_tag = block.find('div', class_='font-light')
        value_tag = key_tag.find_next_sibling('div') if key_tag else None
        if not key_tag or not value_tag:
            continue
        key_text = key_tag.get_text(separator=' ', strip=True).lower()
        value_text = value_tag.get_text(separator=' ', strip=True)
        info[key_text] = value_text
    return info


def _find_value(info_map: Dict[str, str], substrings: list[str]) -> Optional[str]:
    for key, value in info_map.items():
        if any(substring in key for substring in substrings):
            return value
    return None


def _extract_first_int(raw_value: Optional[str]) -> Optional[int]:
    if not raw_value:
        return None
    match = re.search(r'\d+', raw_value)
    return int(match.group()) if match else None


def _extract_sum_of_ints(raw_value: Optional[str]) -> Optional[int]:
    if not raw_value:
        return None
    numbers = [int(match) for match in re.findall(r'\d+', raw_value)]
    return sum(numbers) if numbers else None


def _calculate_average(raw_value: Optional[str]) -> Optional[float]:
    if not raw_value:
        return None
    numbers = [float(num.replace(',', '.')) for num in re.findall(r'\d+(?:[.,]\d+)?', raw_value)]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 2)


def _record_failed_game(
    game_number: int,
    message: str,
    connection: Optional[Any],
    cursor: Optional[Any],
) -> None:
    print(f'Game #{game_number} skipped: {message}')
    if connection and cursor:
        from modules_pars.db_utils import insert_failed_games

        insert_failed_games(connection, cursor, {"id": game_number}, message)
