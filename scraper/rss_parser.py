"""Парсинг RSS-лент образовательных сайтов."""

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from config import DB_PATH
from database.db import get_connection
from database.tg_db import ensure_tg_tables, upsert_channel, insert_post

RSS_FEEDS_PATH = Path(__file__).parent.parent / "data" / "rss_feeds.json"
REQUEST_DELAY = 1.5  # секунды между запросами


def load_feeds(path: Path = None) -> List[dict]:
    """Загрузить список RSS-фидов из JSON."""
    path = path or RSS_FEEDS_PATH
    if not path.exists():
        print(f"[RSS] Файл фидов не найден: {path}")
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_html(html: str) -> str:
    """Убрать HTML-теги, оставить чистый текст."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    # Убрать множественные переносы строк
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_date(entry: dict) -> str:
    """Извлечь дату публикации из RSS-записи."""
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                dt = datetime(*parsed[:6])
                return dt.isoformat()
            except (TypeError, ValueError):
                pass
    return ""


def _entry_id(feed_title: str, entry: dict) -> int:
    """Сгенерировать уникальный числовой ID для записи RSS."""
    raw = entry.get("id") or entry.get("link") or entry.get("title", "")
    h = hashlib.md5(f"{feed_title}:{raw}".encode()).hexdigest()
    return int(h[:8], 16)  # 32-bit positive int


def fetch_feed(url: str, timeout: int = 15) -> Optional[feedparser.FeedParserDict]:
    """Загрузить и распарсить RSS-ленту."""
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "CHGKBot/1.0 (educational)"},
        )
        resp.raise_for_status()
        return feedparser.parse(resp.text)
    except Exception as e:
        print(f"[RSS] Ошибка загрузки {url}: {e}")
        return None


def parse_entries(
    feed: feedparser.FeedParserDict,
    feed_title: str,
    min_text_length: int = 100,
) -> List[dict]:
    """Извлечь записи из RSS-ленты.

    Returns:
        Список dict с ключами: post_id, title, text, link, date, summary.
    """
    results = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "")
        date = _parse_date(entry)

        # Текст: берём summary или content
        raw_text = ""
        if "content" in entry and entry["content"]:
            raw_text = entry["content"][0].get("value", "")
        if not raw_text:
            raw_text = entry.get("summary", "")

        text = _clean_html(raw_text)

        # Собираем полный текст: заголовок + тело
        full_text = f"{title}\n\n{text}" if text else title

        if len(full_text) < min_text_length:
            continue

        results.append({
            "post_id": _entry_id(feed_title, entry),
            "title": title,
            "text": full_text,
            "link": link,
            "date": date,
            "summary": text[:500] if text else "",
        })

    return results


def run_parse(
    feeds_file: Path = None,
    single_feed: str = None,
    limit_per_feed: int = None,
    min_text_length: int = 100,
) -> int:
    """Спарсить RSS-ленты и сохранить в БД (как tg_posts).

    Returns:
        Количество новых записей.
    """
    conn = get_connection(DB_PATH)
    ensure_tg_tables(conn)

    feeds = load_feeds(feeds_file)
    if single_feed:
        feeds = [f for f in feeds if f["title"].lower() == single_feed.lower()]
        if not feeds:
            # Прямой URL
            feeds = [{"url": single_feed, "title": single_feed, "category": ""}]

    if not feeds:
        print("[RSS] Нет фидов для парсинга")
        return 0

    total_new = 0

    for feed_info in feeds:
        url = feed_info["url"]
        title = feed_info["title"]
        category = feed_info.get("category", "")

        print(f"[RSS] Загружаю {title} ({url})...")
        feed = fetch_feed(url)
        if not feed or not feed.entries:
            print(f"[RSS] {title}: нет записей")
            continue

        # Создаём/получаем канал в БД (source_type отличает от TG)
        channel_id = upsert_channel(
            conn,
            username=f"rss_{title.lower().replace(' ', '_')}",
            title=f"[RSS] {title}",
            expected_category=category,
        )

        entries = parse_entries(feed, title, min_text_length)
        if limit_per_feed:
            entries = entries[:limit_per_feed]

        new_count = 0
        for entry in entries:
            inserted = insert_post(
                conn,
                channel_id=channel_id,
                post_id=entry["post_id"],
                text=entry["text"],
                link=entry["link"],
                post_date=entry["date"],
                views=0,
                is_useful=1,
            )
            if inserted:
                new_count += 1

        total_new += new_count
        print(f"[RSS] {title}: {new_count} новых из {len(entries)} записей")
        time.sleep(REQUEST_DELAY)

    conn.close()
    print(f"\n[RSS] Всего новых: {total_new}")
    return total_new
