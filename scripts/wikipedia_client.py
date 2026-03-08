"""Клиент для Russian Wikipedia API с кэшированием.

Используется для обогащения джентльменского набора описаниями сущностей.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

WIKI_API = "https://ru.wikipedia.org/w/api.php"
USER_AGENT = "CHGKBot/1.0 (chgk-analysis; educational)"
REQUEST_DELAY = 1.0  # секунды между запросами


class WikipediaClient:
    """Клиент Wikipedia API с файловым кэшем."""

    def __init__(self, cache_path: Path, delay: float = REQUEST_DELAY,
                 hints_path: Optional[Path] = None):
        self.cache_path = cache_path
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._cache = self._load_cache()
        self._hints = self._load_hints(hints_path)
        self._last_request_time = 0.0

    @staticmethod
    def _load_hints(hints_path: Optional[Path]) -> dict:
        if hints_path and hints_path.exists():
            return json.loads(hints_path.read_text(encoding="utf-8"))
        return {}

    def _load_cache(self) -> dict:
        if self.cache_path.exists():
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        return {}

    def _save_cache(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_time = time.time()

    def get_cached(self, key: str) -> Optional[dict]:
        """Получить запись из кэша."""
        return self._cache.get(key)

    def search(self, query: str, limit: int = 3) -> Optional[str]:
        """Поиск статьи в Wikipedia. Возвращает название статьи или None."""
        self._rate_limit()
        try:
            resp = self.session.get(WIKI_API, params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "format": "json",
            }, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("query", {}).get("search", [])
            if not results:
                return None
            return results[0]["title"]
        except Exception as e:
            print(f"  [wiki] Ошибка поиска '{query}': {e}")
            return None

    def get_extract(self, title: str, chars: int = 2000, intro_only: bool = True) -> Optional[str]:
        """Получить текст статьи (plaintext).

        Args:
            title: Название статьи.
            chars: Максимум символов (обрезается в Python; API exchars макс 1200).
            intro_only: Если True — только вступление, если False — полная статья.
        """
        self._rate_limit()
        try:
            params = {
                "action": "query",
                "titles": title,
                "prop": "extracts",
                "explaintext": 1,
                "format": "json",
            }
            if intro_only:
                params["exintro"] = 1
            # exchars max is 1200, so for longer texts we fetch full and truncate
            if chars <= 1200:
                params["exchars"] = chars
            resp = self.session.get(WIKI_API, params=params, timeout=10)
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
            for page_id, page in pages.items():
                if page_id == "-1":
                    return None
                text = page.get("extract", "")
                if chars and len(text) > chars:
                    text = text[:chars]
                return text
            return None
        except Exception as e:
            print(f"  [wiki] Ошибка extract '{title}': {e}")
            return None

    def get_url(self, title: str) -> str:
        """Сформировать URL статьи."""
        return f"https://ru.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"

    def fetch_entity(self, key: str, display_name: str, force: bool = False) -> Optional[dict]:
        """Получить данные о сущности: поиск + extract. Кэширует результат.

        Returns:
            dict с ключами: title, extract, short_description, wiki_url, fetched_at
            или None если статья не найдена.
        """
        if not force and key in self._cache:
            return self._cache[key]

        search_query = self._hints.get(key, display_name)
        title = self.search(search_query)
        if not title:
            self._cache[key] = None
            return None

        extract = self.get_extract(title)
        if not extract:
            self._cache[key] = None
            return None

        # Первые 2 предложения = short_description
        short = _first_sentences(extract, count=2)

        result = {
            "title": title,
            "extract": extract,
            "short_description": short,
            "wiki_url": self.get_url(title),
            "fetched_at": datetime.now().isoformat(),
        }
        self._cache[key] = result
        return result

    def save(self):
        """Сохранить кэш на диск."""
        self._save_cache()


def _clean_stress(text: str) -> str:
    """Убрать ударения (combining acute accent U+0301) из текста."""
    import unicodedata
    # NFD разобьёт "а́" на "а" + "\u0301", потом удалим \u0301
    nfd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfd if ch != "\u0301")


def _first_sentences(text: str, count: int = 2) -> str:
    """Извлечь первые N предложений из текста Wikipedia.

    Умеет пропускать сокращения (др.-греч., англ., итал., т. е., и т.д.)
    и скобки (содержащие переводы, транскрипции).
    """
    import re

    text = _clean_stress(text.strip())

    # Убрать скобки с переводами: "(англ. ...)", "(др.-греч. ...)" и т.п.
    text = re.sub(r"\([^)]{0,200}\)", "", text)
    # Убрать двойные пробелы после удаления скобок
    text = re.sub(r"\s{2,}", " ", text).strip()

    # Разбить по точкам, но не на сокращениях и инициалах
    # Негативный lookbehind: не разбивать если перед точкой одиночная заглавная (инициал)
    # Позитивный lookahead: после точки пробел + слово от 2+ символов с заглавной
    parts = re.split(r"(?<![А-ЯA-Z])\.(?=\s+[А-ЯA-Z][а-яa-z]|$)", text)

    sentences = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        sentences.append(part + ".")
        if len(sentences) >= count:
            break

    result = " ".join(sentences)
    if not result or len(result) < 10:
        # Fallback
        return text[:300] + ("…" if len(text) > 300 else "")
    return result
