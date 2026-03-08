#!/usr/bin/env python3
"""CLI для парсинга и классификации постов из Telegram-каналов."""

import argparse
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, TG_CHANNELS_FILE
from database.db import get_connection
from database.tg_db import ensure_tg_tables, get_tg_stats


def main():
    parser = argparse.ArgumentParser(
        description="Парсинг и классификация постов из Telegram-каналов"
    )
    parser.add_argument(
        "--channel", type=str, default=None,
        help="Парсить только один канал (username с @ или без)",
    )
    parser.add_argument(
        "--channels-file", type=str, default=None,
        help="Путь к JSON-файлу с каналами",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Максимум постов на канал",
    )
    parser.add_argument(
        "--no-classify", action="store_true",
        help="Только парсинг, без классификации",
    )
    parser.add_argument(
        "--classify-only", action="store_true",
        help="Только классификация (без парсинга новых постов)",
    )
    parser.add_argument(
        "--classify-limit", type=int, default=0,
        help="Максимум постов для классификации (0 = все)",
    )
    parser.add_argument(
        "--provider", type=str, default="openrouter",
        help="LLM-провайдер для классификации (default: openrouter)",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Модель LLM (по умолчанию — из пресета провайдера)",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Показать статистику по ТГ-постам",
    )

    args = parser.parse_args()

    channels_file = Path(args.channels_file) if args.channels_file else TG_CHANNELS_FILE

    if args.status:
        show_status()
        return

    from scraper.tg_runner import run_classify, run_parse

    conn = get_connection(DB_PATH)
    ensure_tg_tables(conn)

    try:
        if not args.classify_only:
            run_parse(
                conn=conn,
                channels_file=channels_file,
                single_channel=args.channel,
                limit_per_channel=args.limit,
            )

        if not args.no_classify:
            run_classify(
                conn=conn,
                provider_name=args.provider,
                model=args.model,
                limit=args.classify_limit,
            )
    finally:
        conn.close()


def show_status():
    """Показать статистику по ТГ-постам."""
    conn = get_connection(DB_PATH)
    ensure_tg_tables(conn)
    stats = get_tg_stats(conn)
    conn.close()

    print(f"\n{'='*50}")
    print(f"Telegram-посты: статистика")
    print(f"{'='*50}")
    print(f"Каналов:              {stats['channels']}")
    print(f"Всего постов:         {stats['total_posts']}")
    print(f"Полезных (is_useful): {stats['useful']}")
    print(f"Классифицировано:     {stats['classified']}")

    if stats["by_category"]:
        print(f"\nПо категориям:")
        for cat, cnt in stats["by_category"].items():
            print(f"  {cat:<30} {cnt:>5}")
    print()


if __name__ == "__main__":
    main()
