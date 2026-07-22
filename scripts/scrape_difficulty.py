"""Совместимая отдельная команда сложности.

Для ежедневного обновления предпочтительнее:
    python scripts/update_catalog.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from database.db import get_connection
from scraper.difficulty import (
    bootstrap_pack_result_statuses,
    calculate_question_results,
    extract_masks,
    get_due_pack_ids,
    process_pack_difficulty,
)
from scraper.session import create_session


def calculate_question_difficulties(masks: list[str]) -> list[float]:
    """Старый публичный helper поверх строгого расчёта take_rate_v1."""
    if not masks:
        return []
    question_ids = list(range(1, len(masks[0]) + 1))
    stats = calculate_question_results(question_ids, masks, pack_id=0)
    return [stat.difficulty_raw for stat in stats]


def scrape_pack_difficulty(session, conn, pack_id: int, force: bool = False) -> dict:
    """Совместимая обёртка без разрушающего поведения старого --force."""
    if not force:
        row = conn.execute(
            "SELECT status FROM pack_result_status WHERE pack_id = ?", (pack_id,)
        ).fetchone()
        if row and row[0] == "complete":
            return {"questions_updated": 0, "skipped": True, "status": "complete"}
    return process_pack_difficulty(session, conn, pack_id).as_dict()


def main() -> int:
    parser = argparse.ArgumentParser(description="Сложность вопросов ЧГК")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="Безопасно пересчитать выбранные пакеты")
    parser.add_argument("--pack", type=int, default=None)
    args = parser.parse_args()

    conn = get_connection(DB_PATH)
    session = create_session()
    try:
        bootstrap_pack_result_statuses(conn)
        if args.pack is not None:
            pack_ids = [args.pack]
        elif args.force:
            sql = """SELECT id FROM packs
                     WHERE parse_status = 'parsed' AND id < 1000000
                       AND COALESCE(teams_played, 0) > 0
                     ORDER BY id"""
            if args.limit:
                sql += " LIMIT ?"
                pack_ids = [row[0] for row in conn.execute(sql, (args.limit,)).fetchall()]
            else:
                pack_ids = [row[0] for row in conn.execute(sql).fetchall()]
        else:
            pack_ids = get_due_pack_ids(conn, backfill=True, limit=args.limit)

        counts: dict[str, int] = {}
        questions_updated = 0
        for index, pack_id in enumerate(pack_ids, start=1):
            result = process_pack_difficulty(session, conn, pack_id)
            counts[result.status] = counts.get(result.status, 0) + 1
            questions_updated += result.questions_updated
            print(f"[{index}/{len(pack_ids)}] pack/{pack_id}: {result.status}")

        print(f"Статусы: {counts}")
        print(f"Вопросов обновлено: {questions_updated}")
        return 0
    finally:
        session.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
