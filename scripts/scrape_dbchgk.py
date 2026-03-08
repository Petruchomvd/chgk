"""Точка входа: парсинг вопросов с db.chgk.info по авторам."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper.dbchgk_runner import run_dbchgk_scraper

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Парсинг вопросов с db.chgk.info по авторам"
    )
    parser.add_argument(
        "--authors", type=str, required=True,
        help="Slug авторов через запятую (напр. dsolovev,ipetrov). "
             "Slug — часть URL после /person/ на db.chgk.info"
    )
    args = parser.parse_args()

    slugs = [s.strip() for s in args.authors.split(",") if s.strip()]
    if not slugs:
        print("Укажите хотя бы одного автора")
        sys.exit(1)

    run_dbchgk_scraper(slugs)
