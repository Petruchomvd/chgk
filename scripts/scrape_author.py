"""Парсинг всех пакетов автора с gotquestions.online (без фильтра по дате)."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DB_PATH
from database.db import get_connection, get_parsed_pack_ids, get_question_count
from scraper.runner import scrape_pack
from scraper.session import create_session, polite_get


def get_author_pack_ids(session, person_id: int) -> set:
    """Собрать все pack_id со страниц поиска автора на gotquestions.online."""
    pack_ids = set()
    page = 1

    empty_streak = 0
    while True:
        url = f"https://gotquestions.online/search?author={person_id}&sSort=uDate&page={page}"
        print(f"  Поиск пакетов: страница {page}...", end=" ", flush=True)

        resp = polite_get(session, url)
        if not resp or resp.status_code != 200:
            print("стоп")
            break

        html = resp.text
        found = set(int(x) for x in re.findall(r"/pack/(\d+)", html))

        # Проверяем, есть ли вообще вопросы на странице
        has_questions = bool(re.search(r"/question/\d+", html))
        if not has_questions:
            print("нет вопросов — конец")
            break

        new = found - pack_ids
        pack_ids.update(found)
        print(f"{len(found)} пакетов (новых: {len(new)})")

        page += 1

    return pack_ids


def scrape_author_packs(person_ids: list, force: bool = False):
    """Спарсить все пакеты указанных авторов."""
    conn = get_connection(DB_PATH)
    session = create_session()

    already_parsed = get_parsed_pack_ids(conn)
    print(f"В БД: {len(already_parsed)} паков, {get_question_count(conn)} вопросов\n")

    all_pack_ids = set()
    for pid in person_ids:
        print(f"Автор person/{pid}:")
        author_packs = get_author_pack_ids(session, pid)
        print(f"  Итого пакетов: {len(author_packs)}\n")
        all_pack_ids.update(author_packs)

    if force:
        to_scrape = sorted(all_pack_ids)
    else:
        to_scrape = sorted(all_pack_ids - already_parsed)

    print(f"Всего уникальных пакетов: {len(all_pack_ids)}")
    print(f"Новых (не спарсенных): {len(to_scrape)}\n")

    if not to_scrape:
        print("Нечего парсить — все пакеты уже в БД")
        conn.close()
        return

    success = 0
    failed = 0
    total = len(to_scrape)

    for i, pack_id in enumerate(to_scrape):
        print(f"  [{i+1}/{total}] pack/{pack_id}...", end=" ", flush=True)
        if scrape_pack(session, conn, pack_id):
            success += 1
        else:
            failed += 1

    print(f"\nГотово: ok:{success} fail:{failed} из {total}")
    print(f"Всего в БД: {get_question_count(conn)} вопросов")
    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Парсинг всех пакетов авторов с gotquestions.online"
    )
    parser.add_argument(
        "--persons", type=str, required=True,
        help="Person ID через запятую (напр. 185,13494). "
             "ID из URL gotquestions.online/person/NNN"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Перепарсить уже спарсенные паки"
    )
    args = parser.parse_args()

    pids = [int(x.strip()) for x in args.persons.split(",") if x.strip()]
    if not pids:
        print("Укажите хотя бы один person ID")
        sys.exit(1)

    scrape_author_packs(pids, force=args.force)
