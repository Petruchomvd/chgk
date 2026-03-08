#!/usr/bin/env python3
"""Генерация фактов из образовательных сайтов (Arzamas и др.) для ДН.

Матчит сущности из thematic_mapping с каталогом курсов/статей Arzamas,
загружает текст, LLM извлекает факты.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID
from scraper.edu_site_parser import (
    fetch_article,
    generate_facts_from_article,
    load_arzamas_catalog,
    match_entity_to_catalog,
)
from scripts.wiki_facts import format_post, load_entities_by_category

EDU_FACTS_CACHE_PATH = (
    Path(__file__).parent.parent / "data" / "gentleman_set" / "edu_facts_cache.json"
)


def load_cache() -> dict:
    if EDU_FACTS_CACHE_PATH.exists():
        return json.loads(EDU_FACTS_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict):
    EDU_FACTS_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_generate(
    category: str = None,
    limit: int = 5,
    provider_name: str = "openrouter",
    model: str = None,
    post_to_group: bool = False,
    dry_run: bool = False,
) -> List[dict]:
    """Пайплайн: сущности ДН -> каталог Arzamas -> fetch -> LLM -> факты."""
    entities = load_entities_by_category(category)
    if not entities:
        print(f"[Edu] Нет сущностей для категории: {category}")
        return []

    catalog = load_arzamas_catalog()
    if not catalog:
        print("[Edu] Каталог Arzamas пуст. Запустите скрипт обновления каталога.")
        return []

    cache = load_cache()
    results = []
    count = 0

    for entity_key, entity_data in entities.items():
        if count >= limit:
            break

        cat = entity_data["category"]

        if entity_key in cache:
            continue

        # Матчим с каталогом
        match = match_entity_to_catalog(entity_key, catalog)
        if not match:
            continue

        if dry_run:
            print(f"[Edu] {entity_key} ({cat}) -> {match['title'][:60]}")
            count += 1
            continue

        # Загружаем статью
        print(f"[Edu] {entity_key} ({cat}): загружаю {match['url']}...")
        article_text = fetch_article(match["url"])
        if not article_text:
            print(f"[Edu] {entity_key}: не удалось загрузить статью")
            continue

        # LLM извлекает факты
        print(f"[Edu] {entity_key}: генерирую факты ({len(article_text)} символов)...")
        facts_text = generate_facts_from_article(
            entity_key, article_text, "Arzamas", provider_name, model
        )

        if not facts_text:
            print(f"[Edu] {entity_key}: LLM не вернул ответ")
            continue

        cache[entity_key] = {
            "category": cat,
            "facts": facts_text,
            "source_url": match["url"],
            "source_title": "Arzamas",
            "arzamas_article": match["title"][:120],
        }
        save_cache(cache)

        result = {
            "entity": entity_key,
            "category": cat,
            "facts": facts_text,
            "source_url": match["url"],
            "source_title": "Arzamas",
        }
        results.append(result)
        count += 1
        print(f"[Edu] {entity_key}: OK")

        time.sleep(1)  # rate limiting для сайта

    if post_to_group and results:
        _post_results(results)

    return results


def _post_results(results: List[dict]):
    """Отправить факты в ТГ-группу."""
    from scripts.tg_bot_digest import DigestBot

    if not TG_DIGEST_BOT_TOKEN or not TG_DIGEST_CHAT_ID:
        print("[Edu] Не настроен TG_DIGEST_BOT_TOKEN / TG_DIGEST_CHAT_ID")
        return

    bot = DigestBot(TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID)

    for r in results:
        topic_id = bot.get_or_create_topic(r["category"])
        message = format_post(
            r["entity"], r["category"], r["facts"], r["source_url"]
        )
        try:
            bot._api(
                "sendMessage",
                chat_id=bot.chat_id,
                message_thread_id=topic_id,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
            print(f"[Edu] Отправлен: {r['entity']} -> {r['category']}")
            time.sleep(0.5)
        except RuntimeError as e:
            print(f"[Edu] Ошибка отправки {r['entity']}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Факты из Arzamas для джентльменского набора"
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Фильтр по категории",
    )
    parser.add_argument(
        "--limit", type=int, default=5,
        help="Максимум сущностей (default: 5)",
    )
    parser.add_argument(
        "--provider", type=str, default="openrouter",
        help="LLM-провайдер",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Модель LLM",
    )
    parser.add_argument(
        "--post", action="store_true",
        help="Отправить в ТГ-группу",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать совпадения без загрузки",
    )

    args = parser.parse_args()

    results = run_generate(
        category=args.category,
        limit=args.limit,
        provider_name=args.provider,
        model=args.model,
        post_to_group=args.post,
        dry_run=args.dry_run,
    )

    if results:
        print(f"\n[Edu] Сгенерировано: {len(results)}")
        for r in results:
            print(f"  {r['entity']} ({r['category']})")
    elif not args.dry_run:
        print("[Edu] Нет новых фактов")


if __name__ == "__main__":
    main()
