"""Скрапер вопросов с db.chgk.info по авторам."""

import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Set

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from database.db import get_connection
from scraper.dbchgk_parser import get_total_pages, parse_search_page
from scraper.session import create_session, polite_get

DBCHGK_BASE = "https://db.chgk.info"
DBCHGK_SEARCH = f"{DBCHGK_BASE}/search/questions"

# ID-пространство для db.chgk.info (не пересекается с gotquestions)
DBCHGK_PACK_ID_START = 1_000_000
DBCHGK_QUESTION_ID_START = 1_000_000


def _normalize_text(text: str) -> str:
    """Нормализовать текст для сравнения (дедупликация)."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    # Убираем пунктуацию для fuzzy-сравнения
    text = re.sub(r"[^\w\s]", "", text)
    return text


def _get_existing_texts(conn) -> Set[str]:
    """Загрузить нормализованные тексты существующих вопросов."""
    rows = conn.execute("SELECT text FROM questions").fetchall()
    return {_normalize_text(r[0]) for r in rows}


def _get_next_question_id(conn) -> int:
    """Следующий свободный ID в пространстве db.chgk.info."""
    row = conn.execute(
        "SELECT MAX(id) FROM questions WHERE id >= ?",
        (DBCHGK_QUESTION_ID_START,)
    ).fetchone()
    max_id = row[0] if row[0] else DBCHGK_QUESTION_ID_START - 1
    return max_id + 1


def _get_next_pack_id(conn) -> int:
    """Следующий свободный pack ID в пространстве db.chgk.info."""
    row = conn.execute(
        "SELECT MAX(id) FROM packs WHERE id >= ?",
        (DBCHGK_PACK_ID_START,)
    ).fetchone()
    max_id = row[0] if row[0] else DBCHGK_PACK_ID_START - 1
    return max_id + 1


def scrape_author(
    session, conn, author_slug: str, existing_texts: Set[str]
) -> int:
    """Спарсить все вопросы автора с db.chgk.info.

    Возвращает количество новых вопросов.
    """
    url = f"{DBCHGK_SEARCH}/author_{author_slug}"
    print(f"\nПарсинг автора: {author_slug}")
    print(f"  URL: {url}")

    # Первая страница — определяем кол-во страниц
    resp = polite_get(session, url + "?page=0")
    if not resp or resp.status_code != 200:
        print(f"  ОШИБКА: не удалось загрузить страницу ({resp.status_code if resp else 'no response'})")
        return 0

    total_pages = get_total_pages(resp.text)
    first_page_questions = parse_search_page(resp.text)
    print(f"  Страниц: {total_pages + 1}, вопросов на 1-й: {len(first_page_questions)}")

    # Создаём пак для этого автора
    pack_id = _get_next_pack_id(conn)
    conn.execute(
        """INSERT OR IGNORE INTO packs (id, title, link, parse_status, authors)
           VALUES (?, ?, ?, 'parsed', ?)""",
        (pack_id, f"db.chgk.info — {author_slug}", url, author_slug),
    )
    conn.commit()

    all_questions = first_page_questions
    next_qid = _get_next_question_id(conn)

    # Остальные страницы
    for page in range(1, total_pages + 1):
        print(f"  Страница {page + 1}/{total_pages + 1}...", end=" ", flush=True)
        resp = polite_get(session, url + f"?page={page}")
        if not resp or resp.status_code != 200:
            print("ошибка")
            continue
        page_questions = parse_search_page(resp.text)
        print(f"{len(page_questions)} вопросов")
        all_questions.extend(page_questions)

    # Вставляем с дедупликацией
    inserted = 0
    duplicates = 0
    for q in all_questions:
        norm = _normalize_text(q.get("text", ""))
        if not norm:
            continue
        if norm in existing_texts:
            duplicates += 1
            continue

        # Формируем authors JSON (как в gotquestions)
        authors_str = None
        if q.get("authors"):
            authors_str = q["authors"]

        try:
            conn.execute(
                """INSERT OR IGNORE INTO questions
                   (id, pack_id, number, tour_number, text, answer,
                    zachet, nezachet, comment, source, authors,
                    razdatka_text, razdatka_pic)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    next_qid,
                    pack_id,
                    q.get("question_number"),
                    None,
                    q["text"],
                    q.get("answer", ""),
                    q.get("zachet"),
                    q.get("nezachet"),
                    q.get("comment"),
                    q.get("source"),
                    authors_str,
                    None,
                    None,
                ),
            )
            existing_texts.add(norm)
            next_qid += 1
            inserted += 1
        except Exception as e:
            print(f"  Ошибка вставки: {e}")

    conn.commit()

    # Обновляем question_count пака
    conn.execute(
        "UPDATE packs SET question_count = ? WHERE id = ?",
        (inserted, pack_id),
    )
    conn.commit()

    print(f"  Итого: {len(all_questions)} найдено, {inserted} новых, {duplicates} дубликатов")
    return inserted


def run_dbchgk_scraper(author_slugs: List[str]):
    """Главная функция: парсит вопросы нескольких авторов."""
    conn = get_connection(DB_PATH)
    session = create_session()

    print("Загружаю существующие тексты для дедупликации...")
    existing_texts = _get_existing_texts(conn)
    print(f"  {len(existing_texts)} вопросов в БД")

    total_new = 0
    for slug in author_slugs:
        new = scrape_author(session, conn, slug, existing_texts)
        total_new += new

    from database.db import get_question_count
    print(f"\nГотово: {total_new} новых вопросов добавлено")
    print(f"Всего в БД: {get_question_count(conn)} вопросов")
    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Парсинг вопросов с db.chgk.info по авторам")
    parser.add_argument(
        "--authors", type=str, required=True,
        help="Slug авторов через запятую (напр. dsolovev,ipetrov)"
    )
    args = parser.parse_args()

    slugs = [s.strip() for s in args.authors.split(",") if s.strip()]
    run_dbchgk_scraper(slugs)
