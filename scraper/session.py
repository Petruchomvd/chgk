"""HTTP-сессия с retry-логикой и вежливыми задержками."""

import random
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    SCRAPE_DELAY,
    SCRAPE_JITTER,
    SCRAPE_MAX_RETRIES,
    SCRAPE_TIMEOUT,
)


def create_session() -> requests.Session:
    """Создать HTTP-сессию с retry-логикой."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "ChGK-Analyzer/1.0 (research project)",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        "Accept": "text/html,application/xhtml+xml",
    })

    retry = Retry(
        total=SCRAPE_MAX_RETRIES,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def polite_get(
    session: requests.Session,
    url: str,
    timeout: Optional[int] = None,
) -> Optional[requests.Response]:
    """GET-запрос с вежливой задержкой."""
    delay = SCRAPE_DELAY + random.uniform(0, SCRAPE_JITTER)
    time.sleep(delay)

    try:
        resp = session.get(url, timeout=timeout or SCRAPE_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return e.response  # 404 — пакет не существует, не ошибка
        raise
