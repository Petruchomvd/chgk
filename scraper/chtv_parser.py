"""Парсинг статей с chtv.ru (Чтиво) через Nuxt _payload.json.

Сайт на Nuxt.js — HTML отдаётся без контента (SPA), но данные доступны
через /{slug}/_payload.json в виде плоского массива с индексами-ссылками.
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_DELAY = 1.5

GENTLEMAN_SET_DIR = Path(__file__).parent.parent / "data" / "gentleman_set"
CHTV_CATALOG_PATH = GENTLEMAN_SET_DIR / "chtv_catalog.json"


def fetch_catalog_from_sitemap() -> List[dict]:
    """Скачать каталог статей из sitemap_articles_1.xml.

    Returns:
        Список [{url, slug}]
    """
    sitemap_url = "https://chtv.ru/sitemap_articles_1.xml"
    resp = requests.get(sitemap_url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    articles = []
    for url_el in root.findall("sm:url", ns):
        loc = url_el.find("sm:loc", ns)
        if loc is None:
            continue
        url = loc.text.strip()
        # Пропускаем главную и good-news
        path = urlparse(url).path.strip("/")
        if not path or path in ("good-news",):
            continue
        articles.append({"url": url, "slug": path})

    # Кэшируем каталог
    GENTLEMAN_SET_DIR.mkdir(parents=True, exist_ok=True)
    CHTV_CATALOG_PATH.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[CHTV] Каталог: {len(articles)} статей → {CHTV_CATALOG_PATH.name}")
    return articles


def load_catalog() -> List[dict]:
    """Загрузить каталог из кэша или скачать."""
    if CHTV_CATALOG_PATH.exists():
        return json.loads(CHTV_CATALOG_PATH.read_text(encoding="utf-8"))
    return fetch_catalog_from_sitemap()


def _deref(data: list, idx):
    """Разыменовать индекс в Nuxt payload.

    Значения в dict'ах — это индексы в плоском массиве data.
    Если idx — int, возвращает data[idx]. Иначе — сам idx.
    """
    if isinstance(idx, int) and 0 <= idx < len(data):
        return data[idx]
    return idx


def _parse_payload(data) -> Dict:
    """Распарсить Nuxt _payload.json (плоский массив с индексами-ссылками).

    Формат: data[0] — служебный, data[3] — dict статьи, где значения — индексы
    в массив data. Блоки контента тоже ссылаются по индексам.

    Returns:
        {title, author, date, text, category}
    """
    if not isinstance(data, list) or len(data) < 10:
        return {}

    # Найти dict статьи (содержит ключи title, slug, dateTime, content)
    article_meta = None
    for item in data:
        if isinstance(item, dict) and all(k in item for k in ("title", "slug", "content")):
            article_meta = item
            break

    if not article_meta:
        return {}

    title = _deref(data, article_meta.get("title", "")) or ""
    date = _deref(data, article_meta.get("dateTime", "")) or ""
    category = _deref(data, article_meta.get("category", "")) or ""

    # Author — может быть вложенный dict с индексами
    author = ""
    author_ref = article_meta.get("author")
    if author_ref is not None:
        author_obj = _deref(data, author_ref)
        if isinstance(author_obj, dict):
            name_ref = author_obj.get("name")
            author = _deref(data, name_ref) if name_ref is not None else ""
        elif isinstance(author_obj, str):
            author = author_obj

    # Контент — список индексов блоков
    content_ref = article_meta.get("content")
    content_list = _deref(data, content_ref)
    if not isinstance(content_list, list):
        return {}

    text_blocks = []
    for block_idx in content_list:
        block = _deref(data, block_idx)
        if not isinstance(block, dict):
            continue

        block_type = _deref(data, block.get("type", ""))

        if block_type == "text" and "text" in block:
            html = _deref(data, block["text"])
            if isinstance(html, str):
                soup = BeautifulSoup(html, "html.parser")
                clean = soup.get_text(separator=" ", strip=True)
                if clean and len(clean) > 20:
                    text_blocks.append(clean)

        elif block_type == "title_style_1" and "title" in block:
            section_title = _deref(data, block["title"])
            if isinstance(section_title, str):
                text_blocks.append(f"\n## {section_title}\n")

    full_text = "\n".join(text_blocks)

    return {
        "title": title if isinstance(title, str) else "",
        "author": author if isinstance(author, str) else "",
        "date": date if isinstance(date, str) else "",
        "category": category if isinstance(category, str) else "",
        "text": full_text,
    }


def fetch_article(slug: str) -> Optional[Dict]:
    """Загрузить и распарсить статью через _payload.json.

    Returns:
        {title, author, date, text, slug, url} или None
    """
    url = f"https://chtv.ru/{slug}/_payload.json"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except Exception as e:
        print(f"  [CHTV] Ошибка загрузки {slug}: {e}")
        return None

    try:
        data = resp.json()
    except json.JSONDecodeError:
        print(f"  [CHTV] Невалидный JSON: {slug}")
        return None

    result = _parse_payload(data)
    if not result.get("text") or len(result["text"]) < 200:
        return None

    result["slug"] = slug
    result["url"] = f"https://chtv.ru/{slug}"
    return result
