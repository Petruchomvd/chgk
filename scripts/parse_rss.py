#!/usr/bin/env python3
"""Парсинг RSS-лент образовательных сайтов + классификация + постинг."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper.rss_parser import run_parse


def main():
    parser = argparse.ArgumentParser(
        description="Парсинг RSS-лент для ЧГК дайджеста"
    )
    parser.add_argument(
        "--feed", type=str, default=None,
        help="Название фида или URL (по умолчанию все из rss_feeds.json)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Максимум записей на фид",
    )
    parser.add_argument(
        "--classify", action="store_true",
        help="Классифицировать новые записи через LLM",
    )
    parser.add_argument(
        "--provider", type=str, default="openrouter",
        help="LLM-провайдер для классификации",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Модель LLM",
    )
    parser.add_argument(
        "--post", action="store_true",
        help="Отправить классифицированные записи в ТГ-группу",
    )

    args = parser.parse_args()

    # Парсинг
    new_count = run_parse(
        single_feed=args.feed,
        limit_per_feed=args.limit,
    )

    # Классификация
    if args.classify and new_count > 0:
        from scraper.tg_runner import run_classify

        run_classify(
            provider_name=args.provider,
            model=args.model,
            limit=new_count,
        )

    # Постинг
    if args.post:
        from config import TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID
        from scripts.tg_bot_digest import DigestBot

        if TG_DIGEST_BOT_TOKEN and TG_DIGEST_CHAT_ID:
            bot = DigestBot(TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID)
            bot.post_classified_posts(limit=new_count)
        else:
            print("[RSS] Не настроен TG_DIGEST_BOT_TOKEN / TG_DIGEST_CHAT_ID")


if __name__ == "__main__":
    main()
