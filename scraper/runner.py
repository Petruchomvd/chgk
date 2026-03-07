"""Оркестратор парсинга: собирает пакеты и вопросы в БД."""

import sys
import time
from pathlib import Path

# Обход проблемы кодировки cp1251 на Windows при перенаправлении вывода
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BASE_URL, DB_PATH, SCRAPE_BATCH_PAUSE, SCRAPE_BATCH_SIZE
from database.db import (
    get_connection,
    get_parsed_pack_ids,
    get_question_count,
    insert_questions,
    mark_pack_status,
    upsert_pack,
)
from scraper.pack_parser import (
    extract_pack_metadata_from_html,
    extract_questions_from_html,
    normalize_question,
)
from scraper.session import create_session, polite_get


def get_last_pack_id(session) -> int:
    """Определить ID последнего пакета на сайте."""
    import re

    resp = polite_get(session, BASE_URL)
    if not resp or resp.status_code != 200:
        print("Не удалось загрузить главную страницу")
        return 0

    html = resp.text

    # Ищем /pack/ID во всём HTML (push-блоки + обычная разметка)
    max_id = 0
    for id_str in re.findall(r"/pack/(\d+)", html):
        max_id = max(max_id, int(id_str))

    if max_id > 0:
        print(f"Последний пакет на сайте: #{max_id}")
    return max_id


def scrape_pack(session, conn, pack_id: int,
                date_from: str = None, date_to: str = None) -> bool:
    """Спарсить один пакет: метаданные + вопросы."""
    url = f"{BASE_URL}/pack/{pack_id}"

    try:
        resp = polite_get(session, url)
    except Exception as e:
        mark_pack_status(conn, pack_id, "failed", str(e))
        print(f"ОШИБКА — {e}")
        return False

    if resp is None:
        mark_pack_status(conn, pack_id, "failed", "no response")
        print("нет ответа")
        return False

    if resp.status_code == 404:
        mark_pack_status(conn, pack_id, "skipped", "404")
        print("404")
        return False

    html = resp.text

    # Метаданные пакета
    metadata = extract_pack_metadata_from_html(html, pack_id)
    upsert_pack(conn, metadata)

    # Фильтр по дате
    pub_date = metadata.get("published_date") or ""
    if date_from and pub_date < date_from:
        mark_pack_status(conn, pack_id, "skipped", f"date {pub_date} < {date_from}")
        print(f"пропуск (дата {pub_date} < {date_from})")
        return False
    if date_to and pub_date > date_to:
        mark_pack_status(conn, pack_id, "skipped", f"date {pub_date} > {date_to}")
        print(f"пропуск (дата {pub_date} > {date_to})")
        return False

    # Вопросы
    raw_questions = extract_questions_from_html(html)
    if not raw_questions:
        mark_pack_status(conn, pack_id, "failed", "no questions extracted")
        print(f"0 вопросов (ошибка извлечения)")
        return False

    # Нормализуем и определяем номер тура
    questions = []
    tour_num = 1
    prev_number = 0
    for q in raw_questions:
        cur_number = q.get("number", 0)
        if cur_number <= prev_number:
            tour_num += 1
        prev_number = cur_number
        questions.append(normalize_question(q, pack_id, tour_num))

    inserted = insert_questions(conn, questions)
    mark_pack_status(conn, pack_id, "parsed")

    title = metadata.get("title", "?")
    print(f"{title[:50]} — {inserted} вопросов")
    return True


def run_scraper(start_id: int = 1, end_id: int = None, max_packs: int = None,
                force: bool = False, date_from: str = None, date_to: str = None):
    """Главный цикл парсинга."""
    conn = get_connection(DB_PATH)
    session = create_session()

    # Определяем диапазон
    if end_id is None:
        end_id = get_last_pack_id(session)
        if end_id == 0:
            print("Не удалось определить последний пакет")
            return

    already_parsed = get_parsed_pack_ids(conn)
    if force:
        pack_ids = list(range(start_id, end_id + 1))
    else:
        pack_ids = [i for i in range(start_id, end_id + 1) if i not in already_parsed]

    if max_packs:
        pack_ids = pack_ids[:max_packs]

    total = len(pack_ids)
    date_info = ""
    if date_from or date_to:
        date_info = f", даты {date_from or '...'} – {date_to or '...'}"
    print(f"\nПарсинг: {total} паков (ID {start_id}–{end_id}, пропущено {len(already_parsed)}{date_info})\n")

    success = 0
    failed = 0
    for i, pack_id in enumerate(pack_ids):
        # Предварительно создаём запись со статусом pending
        upsert_pack(conn, {"id": pack_id, "parse_status": "pending"})

        print(f"  [{i+1}/{total}] pack/{pack_id}...", end=" ", flush=True)
        if scrape_pack(session, conn, pack_id, date_from=date_from, date_to=date_to):
            success += 1
        else:
            failed += 1

        # Пауза между батчами
        if (i + 1) % SCRAPE_BATCH_SIZE == 0 and i + 1 < total:
            print(f"\n  --- {i+1}/{total} | ok:{success} fail:{failed} | "
                  f"БД: {get_question_count(conn)} вопросов ---\n")
            time.sleep(SCRAPE_BATCH_PAUSE)

    print(f"\nГотово: ok:{success} fail:{failed} из {total} | "
          f"БД: {get_question_count(conn)} вопросов")

    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Парсинг пакетов ЧГК")
    parser.add_argument("--start", type=int, default=1, help="Начальный ID пакета")
    parser.add_argument("--end", type=int, default=None, help="Конечный ID пакета")
    parser.add_argument("--max", type=int, default=None, help="Максимум пакетов")
    parser.add_argument("--force", action="store_true", help="Перепарсить уже спарсенные паки")
    parser.add_argument("--date-from", type=str, default=None, help="Мин. дата публикации (YYYY-MM-DD)")
    parser.add_argument("--date-to", type=str, default=None, help="Макс. дата публикации (YYYY-MM-DD)")
    args = parser.parse_args()

    run_scraper(start_id=args.start, end_id=args.end, max_packs=args.max,
                force=args.force, date_from=args.date_from, date_to=args.date_to)
