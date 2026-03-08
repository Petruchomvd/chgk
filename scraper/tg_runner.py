"""Пайплайн: парсинг Telegram-каналов → фильтрация → классификация → сохранение."""

import json
import time
from pathlib import Path
from typing import List, Optional

from config import DB_PATH, TG_CHANNELS_FILE, TG_MIN_TEXT_LENGTH, TG_PARSE_DELAY
from database.db import get_connection
from database.tg_db import (
    ensure_tg_tables,
    get_active_channels,
    get_channel,
    get_unclassified_posts,
    insert_post,
    update_last_parsed_id,
    update_post_category,
    upsert_channel,
)
from scraper.tg_parser import TgWebParser


def load_channels_from_json(path: Path = None) -> List[dict]:
    """Загрузить список каналов из JSON-файла."""
    path = path or TG_CHANNELS_FILE
    if not path.exists():
        print(f"[TG] Файл каналов не найден: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sync_channels_to_db(conn, channels: List[dict]) -> None:
    """Синхронизировать каналы из JSON в БД."""
    for ch in channels:
        upsert_channel(
            conn,
            username=ch["username"],
            title=ch.get("title", ""),
            expected_category=ch.get("category", ""),
        )


def run_parse(
    conn=None,
    channels_file: Path = None,
    single_channel: str = None,
    limit_per_channel: int = None,
    min_text_length: int = TG_MIN_TEXT_LENGTH,
) -> int:
    """Спарсить посты из каналов и сохранить в БД.

    Returns:
        Количество новых постов.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection(DB_PATH)
    ensure_tg_tables(conn)

    # Синхронизировать каналы из JSON
    json_channels = load_channels_from_json(channels_file)
    if json_channels:
        sync_channels_to_db(conn, json_channels)

    # Определить какие каналы парсить
    if single_channel:
        username = single_channel.lstrip("@")
        ch = get_channel(conn, username)
        if not ch:
            upsert_channel(conn, username)
            ch = get_channel(conn, username)
        channels_to_parse = [ch]
    else:
        channels_to_parse = get_active_channels(conn)

    if not channels_to_parse:
        print("[TG] Нет каналов для парсинга")
        return 0

    parser = TgWebParser(delay=TG_PARSE_DELAY)
    total_new = 0

    for ch in channels_to_parse:
        username = ch["username"]
        after_id = ch["last_parsed_id"]

        posts = parser.parse_channel(
            username=username,
            after_id=after_id,
            limit=limit_per_channel,
        )

        if not posts:
            continue

        max_id = after_id
        new_count = 0

        for p in posts:
            is_useful = 1 if len(p["text"]) >= min_text_length else 0
            inserted = insert_post(
                conn,
                channel_id=ch["id"],
                post_id=p["post_id"],
                text=p["text"],
                link=p["link"],
                post_date=p["date"].isoformat() if p["date"] else "",
                views=p["views"],
                is_useful=is_useful,
            )
            if inserted:
                new_count += 1
            if p["post_id"] > max_id:
                max_id = p["post_id"]

        if max_id > after_id:
            update_last_parsed_id(conn, ch["id"], max_id)

        total_new += new_count
        print(f"[TG] @{username}: {new_count} новых постов сохранено")

    if own_conn:
        conn.close()

    print(f"\n[TG] Всего новых постов: {total_new}")
    return total_new


def run_classify(
    conn=None,
    provider_name: str = "openrouter",
    model: str = None,
    limit: int = 0,
) -> int:
    """Классифицировать неклассифицированные ТГ-посты.

    Returns:
        Количество классифицированных постов.
    """
    from classifier.providers import create_provider
    from classifier.tg_prompts import build_tg_classify_messages

    own_conn = conn is None
    if own_conn:
        conn = get_connection(DB_PATH)
    ensure_tg_tables(conn)

    posts = get_unclassified_posts(conn, limit=limit)
    if not posts:
        print("[TG] Нет постов для классификации")
        return 0

    provider = create_provider(provider_name, model=model)
    model_name = provider.config.model
    print(f"[TG] Классификация {len(posts)} постов через {provider_name}/{model_name}")

    classified = 0
    for i, post in enumerate(posts, 1):
        messages = build_tg_classify_messages(post["text"])
        response = provider.chat(messages, max_tokens=60)

        if not response:
            print(f"[TG] {i}/{len(posts)}: пустой ответ, пропускаю")
            continue

        # Парсим JSON из ответа
        category, confidence = _parse_classification(response)

        if category is None:
            # Пост не образовательный — помечаем как бесполезный
            conn.execute(
                "UPDATE tg_posts SET is_useful = 0 WHERE id = ?", (post["id"],)
            )
            conn.commit()
            print(f"[TG] {i}/{len(posts)}: не образовательный -> is_useful=0")
        else:
            update_post_category(
                conn,
                post_id=post["id"],
                category=category,
                confidence=confidence,
                model_name=model_name,
            )
            classified += 1
            print(f"[TG] {i}/{len(posts)}: {category} ({confidence:.0%})")

    if own_conn:
        conn.close()

    print(f"\n[TG] Классифицировано: {classified}/{len(posts)}")
    return classified


def _parse_classification(response: str) -> tuple:
    """Извлечь category и confidence из JSON-ответа LLM.

    Returns:
        (category, confidence) или (None, 0.0) если не распознано.
    """
    import re

    # Ищем JSON в ответе
    match = re.search(r"\{[^}]+\}", response)
    if not match:
        return None, 0.0

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None, 0.0

    category = data.get("category")
    confidence = float(data.get("confidence", 0.5))

    # null / None / пустая строка = не образовательный
    if not category or category == "null":
        return None, 0.0

    return category, confidence
