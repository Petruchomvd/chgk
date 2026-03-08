"""Парсер страниц поиска db.chgk.info."""

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup


# Маппинг CSS-классов <strong> → имя поля
_FIELD_MAP = {
    "Answer": "answer",
    "PassCriteria": "zachet",
    "Comments": "comment",
    "Sources": "source",
    "Authors": "authors",
}


def parse_search_page(html: str) -> List[Dict[str, Any]]:
    """Извлечь вопросы со страницы поиска db.chgk.info.

    Структура: <dl> с <dt class="title"> (турнир) и <dd> с <div class="question">.
    Внутри div.question — <p> с <strong class="Question/Answer/...">
    """
    soup = BeautifulSoup(html, "html.parser")
    questions = []

    question_divs = soup.find_all("div", class_="question")

    for div in question_divs:
        q_data: Dict[str, Any] = {}

        # Турнир: ближайший <dt class="title"> перед этим div
        dt = _find_prev_dt(div)
        if dt:
            q_data["tournament"] = dt.get_text(strip=True)
            link = dt.find("a")
            if link:
                q_data["tournament_url"] = link.get("href", "")
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", q_data["tournament"])
            if date_match:
                q_data["tournament_date"] = date_match.group(1)

        # Поля вопроса из <p> блоков
        for p in div.find_all("p"):
            strong = p.find("strong")
            if not strong:
                continue

            cls_list = strong.get("class", [])
            cls = cls_list[0] if cls_list else ""

            if cls == "Question":
                # Текст вопроса — после <strong>
                q_link = strong.find("a", href=re.compile(r"/question/"))
                if q_link:
                    q_data["db_question_url"] = q_link.get("href", "")
                    q_data["question_number"] = _extract_int(q_link.get_text())

                # Текст = всё в <p> кроме <strong>
                strong.decompose()
                q_data["text"] = p.get_text(separator=" ", strip=True)

            elif cls in _FIELD_MAP:
                field = _FIELD_MAP[cls]
                strong.decompose()
                value = p.get_text(separator=" ", strip=True)
                if value:
                    q_data[field] = value

        if q_data.get("text"):
            questions.append(q_data)

    return questions


def get_total_pages(html: str) -> int:
    """Определить номер последней страницы пагинации (0-indexed)."""
    soup = BeautifulSoup(html, "html.parser")
    max_page = 0
    for link in soup.find_all("a", href=re.compile(r"\?page=\d+")):
        match = re.search(r"\?page=(\d+)", link.get("href", ""))
        if match:
            max_page = max(max_page, int(match.group(1)))
    return max_page


def _find_prev_dt(div) -> Optional[Any]:
    """Найти <dt class='title'> перед данным div.question."""
    # div.question внутри <dd>, а <dt> — его предыдущий sibling
    dd = div.find_parent("dd")
    if dd:
        prev = dd.find_previous_sibling("dt", class_="title")
        if prev:
            return prev
    return None


def _extract_int(s: str) -> Optional[int]:
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None
