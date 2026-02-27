"""Скрапинг сложности вопросов из результатов турниров на gotquestions.online.

Использование:
    python scripts/scrape_difficulty.py
    python scripts/scrape_difficulty.py --limit 50   # только первые 50 пакетов
    python scripts/scrape_difficulty.py --force       # перезаписать существующие

Для каждого пакета с результатами:
1. Загружает /table/{pack_id} — результаты турнира
2. Извлекает маски ответов команд (бинарная строка "110101...")
3. Считает difficulty каждого вопроса: (1 - correct/total) * 10
4. Обновляет questions.difficulty и packs.difficulty в БД
"""

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BASE_URL, DB_PATH, SCRAPE_DELAY, SCRAPE_JITTER
from database.db import get_connection
from scraper.session import create_session
from scraper.pack_parser import extract_pack_metadata_from_html

import random


def extract_masks(html: str) -> list[str]:
    """Извлечь маски ответов команд из HTML страницы результатов."""
    return re.findall(r'mask[^01]{1,20}([01]{10,})', html)


def calculate_question_difficulties(masks: list[str]) -> list[float]:
    """Посчитать сложность для каждой позиции вопроса.

    Возвращает список difficulty (0-10) для каждого вопроса.
    0 = все ответили, 10 = никто не ответил.
    """
    if not masks:
        return []

    num_questions = len(masks[0])
    num_teams = len(masks)
    difficulties = []

    for qi in range(num_questions):
        correct = sum(1 for m in masks if qi < len(m) and m[qi] == '1')
        diff = round((1 - correct / num_teams) * 10, 1)
        difficulties.append(diff)

    return difficulties


def scrape_pack_difficulty(session, conn, pack_id: int, force: bool = False) -> dict:
    """Скрапить и посчитать difficulty для одного пакета.

    Возвращает: {"questions_updated": N, "pack_difficulty": X, "teams": N, "error": str|None}
    """
    # Проверяем, нужно ли скрапить (если не force)
    if not force:
        row = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE pack_id = ? AND difficulty IS NOT NULL",
            (pack_id,),
        ).fetchone()
        total = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE pack_id = ?", (pack_id,)
        ).fetchone()
        if row[0] > 0 and row[0] == total[0]:
            return {"questions_updated": 0, "skipped": True}

    # Загружаем страницу результатов
    url = f"{BASE_URL}/table/{pack_id}"
    delay = SCRAPE_DELAY + random.uniform(0, SCRAPE_JITTER)
    time.sleep(delay)

    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 404:
            return {"questions_updated": 0, "error": "404"}
        resp.raise_for_status()
    except Exception as e:
        return {"questions_updated": 0, "error": str(e)}

    masks = extract_masks(resp.text)
    if not masks:
        return {"questions_updated": 0, "error": "no masks"}

    # Загружаем вопросы пакета в порядке маски
    questions = conn.execute(
        "SELECT id FROM questions WHERE pack_id = ? ORDER BY tour_number, number",
        (pack_id,),
    ).fetchall()

    if not questions:
        return {"questions_updated": 0, "error": "no questions in DB"}

    # Проверяем что длина маски совпадает с количеством вопросов
    mask_len = len(masks[0])
    if mask_len != len(questions):
        # Иногда маски включают дополнительные вопросы (разминочные и т.п.)
        # Пропускаем если несовпадение больше 20%
        if abs(mask_len - len(questions)) > max(mask_len, len(questions)) * 0.2:
            return {
                "questions_updated": 0,
                "error": f"mask_len={mask_len} != questions={len(questions)}",
            }

    difficulties = calculate_question_difficulties(masks)

    # Обновляем difficulty в БД
    updated = 0
    for i, q in enumerate(questions):
        if i < len(difficulties):
            conn.execute(
                "UPDATE questions SET difficulty = ? WHERE id = ?",
                (difficulties[i], q[0]),
            )
            updated += 1
    conn.commit()

    # Средняя сложность пакета
    avg_diff = round(sum(difficulties) / len(difficulties), 2) if difficulties else None

    # Также обновляем difficulty пакета из HTML основной страницы
    # (используя исправленный парсер)
    pack_url = f"{BASE_URL}/pack/{pack_id}"
    delay = SCRAPE_DELAY + random.uniform(0, SCRAPE_JITTER)
    time.sleep(delay)
    try:
        pack_resp = session.get(pack_url, timeout=15)
        if pack_resp.status_code == 200:
            meta = extract_pack_metadata_from_html(pack_resp.text, pack_id)
            truedl = meta.get("difficulty")
            if truedl:
                conn.execute(
                    "UPDATE packs SET difficulty = ? WHERE id = ?",
                    (truedl, pack_id),
                )
                conn.commit()
    except Exception:
        pass  # не критично, основная цель — per-question difficulty

    return {
        "questions_updated": updated,
        "teams": len(masks),
        "avg_difficulty": avg_diff,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Скрапинг сложности вопросов ЧГК")
    parser.add_argument("--limit", type=int, default=None, help="Макс. пакетов для обработки")
    parser.add_argument("--force", action="store_true", help="Перезаписать существующие difficulty")
    args = parser.parse_args()

    conn = get_connection(DB_PATH)
    session = create_session()

    # Получаем пакеты с результатами (teams_played > 0)
    packs = conn.execute(
        "SELECT id, title, teams_played FROM packs WHERE teams_played > 0 ORDER BY id"
    ).fetchall()

    if args.limit:
        packs = packs[:args.limit]

    total = len(packs)
    print(f"Пакетов с результатами: {total}")
    print(f"{'='*60}")

    success = 0
    skipped = 0
    failed = 0
    total_questions = 0

    for i, pack in enumerate(packs):
        pack_id = pack[0]
        title = pack[1] or f"Pack #{pack_id}"

        result = scrape_pack_difficulty(session, conn, pack_id, force=args.force)

        if result.get("skipped"):
            skipped += 1
            status = "SKIP"
        elif result.get("error"):
            failed += 1
            status = f"ERR: {result['error']}"
        else:
            success += 1
            total_questions += result["questions_updated"]
            teams = result.get("teams", 0)
            avg = result.get("avg_difficulty", 0)
            status = f"OK: {result['questions_updated']}q, {teams} teams, avg={avg}"

        print(f"  [{i+1}/{total}] {pack_id} {title[:40]:40s} {status}")

    print(f"\n{'='*60}")
    print(f"  Успешно:  {success}")
    print(f"  Пропущено: {skipped}")
    print(f"  Ошибок:  {failed}")
    print(f"  Вопросов обновлено: {total_questions}")

    # Статистика по БД
    row = conn.execute(
        "SELECT COUNT(*), AVG(difficulty) FROM questions WHERE difficulty IS NOT NULL"
    ).fetchone()
    print(f"\n  В БД: {row[0]} вопросов с difficulty, средняя = {row[1]:.1f}" if row[0] else "")

    conn.close()


if __name__ == "__main__":
    main()
