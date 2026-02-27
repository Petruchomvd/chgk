"""Точка входа: парсинг пакетов ЧГК."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper.runner import run_scraper

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Парсинг пакетов ЧГК с gotquestions.online")
    parser.add_argument("--start", type=int, default=1, help="Начальный ID пакета")
    parser.add_argument("--end", type=int, default=None, help="Конечный ID пакета")
    parser.add_argument("--max", type=int, default=None, help="Максимум пакетов для парсинга")
    args = parser.parse_args()

    run_scraper(start_id=args.start, end_id=args.end, max_packs=args.max)
