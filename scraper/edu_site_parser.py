"""Парсинг образовательных сайтов по сущностям из джентльменского набора.

Два режима поиска:
1. По каталогу (Arzamas) — матчим заголовки курсов/статей с сущностями ДН
2. По поиску (DuckDuckGo) — для сайтов без каталога
"""

import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_DELAY = 2.0

GENTLEMAN_SET_DIR = Path(__file__).parent.parent / "data" / "gentleman_set"
ARZAMAS_CATALOG_PATH = GENTLEMAN_SET_DIR / "arzamas_catalog.json"

# Слишком общие слова — не матчим по ним
_SKIP_ENTITIES = {
    "жизнь", "время", "вопрос", "роман", "история", "книга", "язык", "кино",
    "немец", "рука", "друг", "жена", "ключ", "карта", "город", "голос",
    "мост", "маска", "храм", "крест", "звезда", "смерть", "война", "земля",
    "король", "королева", "пара", "евро", "перси", "в сон", "художник",
    "человек", "россия", "любовь", "подпись", "на день", "тюрьма",
}

# Сайты и правила извлечения
SITES = {
    "arzamas.academy": {
        "title": "Arzamas",
        "content_selector": "article",
        "min_length": 500,
    },
}


def load_arzamas_catalog() -> List[dict]:
    """Загрузить каталог курсов/статей Arzamas."""
    if ARZAMAS_CATALOG_PATH.exists():
        return json.loads(ARZAMAS_CATALOG_PATH.read_text(encoding="utf-8"))
    return []


def match_entity_to_catalog(
    entity_key: str,
    catalog: List[dict],
) -> Optional[dict]:
    """Найти статью в каталоге по сущности.

    Returns:
        dict {url, title} или None.
    """
    if entity_key in _SKIP_ENTITIES:
        return None

    words = entity_key.split()

    # Слишком короткие одиночные слова — пропускаем
    if len(words) == 1 and len(words[0]) < 6:
        return None

    for item in catalog:
        title_lower = item["title"].lower()

        if len(words) >= 2:
            # Все значимые слова (>2 букв) должны быть в заголовке
            if all(w in title_lower for w in words if len(w) > 2):
                return {"url": item["url"], "title": item["title"]}
        else:
            word = words[0]
            if re.search(r"(?:^|\s)" + re.escape(word), title_lower):
                return {"url": item["url"], "title": item["title"]}

    return None


def fetch_article(url: str) -> Optional[str]:
    """Загрузить и извлечь текст статьи."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  [fetch] Ошибка загрузки {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    domain = urlparse(url).netloc.replace("www.", "")
    site_config = SITES.get(domain, {})
    selector = site_config.get("content_selector", "article")
    min_length = site_config.get("min_length", 300)

    content = soup.select_one(selector)
    if content:
        for tag in content.select("script, style, nav, header, footer"):
            tag.decompose()
        text = content.get_text(separator="\n", strip=True)
    else:
        paragraphs = soup.select("p")
        text = "\n".join(
            p.get_text(strip=True) for p in paragraphs
            if len(p.get_text(strip=True)) > 30
        )

    if len(text) < min_length:
        return None

    return text


def generate_facts_from_article(
    entity_name: str,
    article_text: str,
    source_title: str,
    provider_name: str = "openrouter",
    model: str = None,
) -> Optional[str]:
    """Извлечь факты из статьи через LLM."""
    from classifier.providers import create_provider
    from scripts.wiki_facts import WIKI_FACTS_PROMPT, _clean_facts_response

    provider = create_provider(provider_name, model=model)

    messages = [
        {"role": "system", "content": WIKI_FACTS_PROMPT},
        {
            "role": "user",
            "content": (
                f"Сущность: {entity_name}\n\n"
                f"Статья с сайта {source_title}:\n{article_text[:5000]}"
            ),
        },
    ]

    raw = provider.chat(messages, max_tokens=1200)
    if not raw:
        return None
    return _clean_facts_response(raw)
