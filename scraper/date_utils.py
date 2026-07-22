"""Shared date parsing helpers for scrapers."""

from __future__ import annotations

import re
from typing import Optional

MONTHS_RU = {
    "января": "01",
    "февраля": "02",
    "марта": "03",
    "апреля": "04",
    "мая": "05",
    "июня": "06",
    "июля": "07",
    "августа": "08",
    "сентября": "09",
    "октября": "10",
    "ноября": "11",
    "декабря": "12",
}


def parse_russian_date(date_str: Optional[str]) -> Optional[str]:
    """Convert dates like '5 января 2026 г.' into '2026-01-05'."""
    if not date_str:
        return None

    normalized = date_str.strip().lower()
    normalized = re.sub(r"\s*г\.?$", "", normalized)

    match = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", normalized)
    if not match:
        return None

    day, month_name, year = match.groups()
    month = MONTHS_RU.get(month_name)
    if not month:
        return None

    return f"{year}-{month}-{day.zfill(2)}"
