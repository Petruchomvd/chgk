"""Парсер страниц пакетов gotquestions.online (HTTP-only, без Selenium)."""

import json
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup


def _extract_push_blocks(html: str) -> List[str]:
    """Извлечь содержимое self.__next_f.push([...]) блоков.

    Не использует regex для содержимого — вместо этого парсит скобки
    с учётом строковых литералов, чтобы ]) внутри строк не обрезали блок.
    """
    marker = "self.__next_f.push(["
    blocks = []
    search_start = 0

    while True:
        idx = html.find(marker, search_start)
        if idx == -1:
            break

        content_start = idx + len(marker)
        # Используем _find_matching_bracket для поиска закрывающей ]
        end = _find_matching_bracket(html, content_start - 1)
        if end is None:
            search_start = content_start
            continue

        blocks.append(html[content_start:end])
        search_start = end + 1

    return blocks


def extract_questions_from_html(html: str) -> List[Dict[str, Any]]:
    """Извлечь все вопросы из HTML страницы пакета.

    Данные встроены в Next.js Server Component payload
    внутри self.__next_f.push() вызовов.
    """
    pushes = _extract_push_blocks(html)

    all_questions: List[Dict[str, Any]] = []

    for block in pushes:
        if "questions" not in block:
            continue

        # Декодируем push payload как JSON массив [type, "content"]
        try:
            data = json.loads("[" + block + "]")
        except json.JSONDecodeError:
            continue

        if len(data) < 2 or not isinstance(data[1], str):
            continue

        content = data[1]

        # Ищем все вхождения "questions":[...] (по одному на тур)
        start_pos = 0
        while True:
            match = re.search(r'"questions":\[', content[start_pos:])
            if not match:
                break

            arr_start = start_pos + match.end() - 1
            arr_end = _find_matching_bracket(content, arr_start)
            if arr_end is None:
                start_pos = arr_start + 1
                continue

            arr_str = content[arr_start : arr_end + 1]

            try:
                questions = json.loads(arr_str)
                if (
                    questions
                    and isinstance(questions[0], dict)
                    and "text" in questions[0]
                ):
                    all_questions.extend(questions)
            except json.JSONDecodeError:
                pass

            start_pos = arr_end + 1

    return all_questions


def extract_tour_info_from_html(html: str) -> List[Dict[str, Any]]:
    """Извлечь информацию о турах из HTML."""
    pushes = _extract_push_blocks(html)
    tours = []

    for block in pushes:
        if "tours" not in block:
            continue

        try:
            data = json.loads("[" + block + "]")
        except json.JSONDecodeError:
            continue

        if len(data) < 2 or not isinstance(data[1], str):
            continue

        content = data[1]

        # Ищем tours массив
        match = re.search(r'"tours":\[', content)
        if not match:
            continue

        arr_start = match.end() - 1
        arr_end = _find_matching_bracket(content, arr_start)
        if arr_end is None:
            continue

        try:
            tours_data = json.loads(content[arr_start : arr_end + 1])
            tours.extend(tours_data)
        except json.JSONDecodeError:
            pass

    return tours


def extract_pack_metadata_from_html(html: str, pack_id: int) -> Dict[str, Any]:
    """Извлечь метаданные пакета из HTML.

    Стратегия: сначала из JSON (Next.js payload), затем BS4 fallback.
    """
    # --- JSON-подход (из вопросов берём packTitle, pubDate, endDate, startDate) ---
    json_title = None
    json_pub_date = None
    json_end_date = None
    json_start_date = None

    pushes = _extract_push_blocks(html)
    for block in pushes:
        if "packTitle" not in block or "packId" not in block:
            continue
        try:
            data = json.loads("[" + block + "]")
        except json.JSONDecodeError:
            continue
        if len(data) < 2 or not isinstance(data[1], str):
            continue
        content = data[1]
        title_m = re.search(r'"packTitle":"([^"]*)"', content)
        pub_m = re.search(r'"pubDate":"([^"]*)"', content)
        end_m = re.search(r'"endDate":"([^"]*)"', content)
        start_m = re.search(r'"startDate":"([^"]*)"', content)
        if title_m:
            json_title = title_m.group(1)
        if pub_m:
            json_pub_date = pub_m.group(1)[:10]  # YYYY-MM-DD
        if end_m:
            json_end_date = end_m.group(1)[:10]
        if start_m:
            json_start_date = start_m.group(1)[:10]
        break

    # --- BS4 fallback (для teams_played, difficulty, start_date) ---
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("h1", class_="text-2xl font-comfortaa")
    bs4_title = title_tag.get_text(strip=True) if title_tag else None

    info_map = {}
    for block in soup.select("div.flex.justify-between"):
        key_tag = block.find(class_="font-light")
        if not key_tag:
            continue
        # value — второй прямой потомок-div блока flex justify-between
        children = [ch for ch in block.find_all("div", recursive=False)]
        if len(children) >= 2:
            key = key_tag.get_text(separator=" ", strip=True).lower()
            value = children[-1].get_text(separator=" ", strip=True)
            info_map[key] = value

    from modules_pars.utils import parse_date

    def _find(substrings):
        for k, v in info_map.items():
            if any(s in k for s in substrings):
                return v
        return None

    def _first_int(raw):
        if not raw:
            return None
        m = re.search(r"\d+", raw)
        return int(m.group()) if m else None

    def _sum_ints(raw):
        if not raw:
            return None
        nums = [int(x) for x in re.findall(r"\d+", raw)]
        return sum(nums) if nums else None

    def _avg_floats(raw):
        if not raw:
            return None
        nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+[.,]?\d*", raw)]
        return round(sum(nums) / len(nums), 2) if nums else None

    authors_block = soup.find("div", class_="flex flex-wrap gap-1")
    authors = []
    if authors_block:
        for tag in authors_block.find_all(
            "a", href=lambda h: h and "/person" in h
        ):
            name = tag.get_text(strip=True)
            if name:
                authors.append(name)

    return {
        "id": pack_id,
        "title": json_title or bs4_title,
        "question_count": _first_int(_find(["вопр"])),
        "start_date": json_start_date or parse_date(_find(["начал"])),
        "end_date": json_end_date or parse_date(_find(["окон", "заверш"])),
        "published_date": json_pub_date or parse_date(_find(["опублик", "публика"])),
        "teams_played": _sum_ints(_find(["команд"])),
        "difficulty": _avg_floats(_find(["сложн"])),
        "authors": ", ".join(authors) if authors else None,
        "link": f"https://gotquestions.online/pack/{pack_id}",
        "parse_status": "parsed",
    }


def normalize_question(q: Dict[str, Any], pack_id: int, tour_number: int = 0) -> Dict[str, Any]:
    """Привести вопрос к формату БД."""
    authors_str = None
    if q.get("authors"):
        authors_str = json.dumps(q["authors"], ensure_ascii=False)

    return {
        "id": q["id"],
        "pack_id": pack_id,
        "number": q.get("number"),
        "tour_number": tour_number,
        "text": q.get("text", ""),
        "answer": q.get("answer", ""),
        "zachet": q.get("zachet") or None,
        "nezachet": q.get("nezachet") or None,
        "comment": q.get("comment") or None,
        "source": q.get("source") or None,
        "authors": authors_str,
        "razdatka_text": q.get("razdatkaText") or None,
        "razdatka_pic": q.get("razdatkaPic") or None,
    }


def _find_matching_bracket(s: str, start: int) -> Optional[int]:
    """Найти позицию закрывающей скобки с учётом строк."""
    depth = 0
    pos = start
    in_str = False
    escape = False

    while pos < len(s):
        ch = s[pos]
        if escape:
            escape = False
            pos += 1
            continue
        if ch == "\\":
            escape = True
            pos += 1
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return pos
        pos += 1

    return None
