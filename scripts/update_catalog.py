"""Единое безопасное обновление каталога и статистики сложности.

Обычный запуск:
    python scripts/update_catalog.py

Первоначальный расчёт сложности для исторических пакетов:
    python scripts/update_catalog.py --backfill-difficulty
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from database.db import get_connection, get_readonly_connection
from scraper.difficulty import (
    bootstrap_pack_result_statuses,
    get_due_pack_ids,
    process_pack_difficulty,
)
from scraper.runner import get_last_pack_id, run_scraper
from scraper.session import create_session


def create_backup(db_path: Path, *, keep: int = 3) -> Path:
    """Сделать согласованную SQLite-копию и оставить несколько последних."""
    backup_dir = db_path.parent / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}-{timestamp}.db"

    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    backups = sorted(backup_dir.glob(f"{db_path.stem}-*.db"), reverse=True)
    for old_backup in backups[keep:]:
        old_backup.unlink()
    return backup_path


def snapshot_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "packs": conn.execute("SELECT COUNT(*) FROM packs").fetchone()[0],
        "questions": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
        "topics": conn.execute("SELECT COUNT(*) FROM question_topics").fetchone()[0],
    }


def validate_database(
    conn: sqlite3.Connection,
    before: dict[str, int],
) -> dict[str, object]:
    after = snapshot_counts(conn)
    decreased = {
        key: (before[key], after[key])
        for key in before
        if after[key] < before[key]
    }
    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
    if decreased:
        raise RuntimeError(f"Защитная проверка: количество записей уменьшилось: {decreased}")
    if foreign_key_errors:
        raise RuntimeError(
            f"Защитная проверка: нарушены внешние ключи ({len(foreign_key_errors)})"
        )
    if quick_check != "ok":
        raise RuntimeError(f"SQLite quick_check: {quick_check}")
    return {"before": before, "after": after, "quick_check": quick_check}


def run_update(args: argparse.Namespace) -> dict:
    db_path = Path(args.db).resolve()
    session = create_session()
    try:
        if args.dry_run:
            return _run_dry(args, db_path, session)

        backup_path = None
        if not args.no_backup:
            print("Создаю резервную копию...")
            backup_path = create_backup(db_path)
            print(f"  {backup_path}")

        conn = get_connection(db_path)
        before = snapshot_counts(conn)
        conn.close()

        parsed_pack_ids: list[int] = []
        scrape_summary: dict = {"success": 0, "failed": 0, "parsed_pack_ids": []}
        if not args.difficulty_only and args.pack is None:
            conn = get_connection(db_path)
            max_known_id = conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM packs WHERE id < 1000000"
            ).fetchone()[0]
            conn.close()
            start_id = args.start if args.start is not None else max_known_id + 1
            scrape_summary = run_scraper(
                start_id=start_id,
                end_id=args.end,
                max_packs=args.max_packs,
                force=False,
                date_from=args.date_from,
                date_to=args.date_to,
                db_path=db_path,
                session=session,
            )
            parsed_pack_ids = scrape_summary.get("parsed_pack_ids", [])

        conn = get_connection(db_path)
        if args.pack is not None:
            bootstrap_ids = [args.pack]
        elif args.backfill_difficulty:
            bootstrap_ids = None
        else:
            bootstrap_ids = parsed_pack_ids
        bootstrap_counts = bootstrap_pack_result_statuses(
            conn,
            pack_ids=bootstrap_ids,
        )

        include_pack_ids = [args.pack] if args.pack is not None else parsed_pack_ids
        difficulty_pack_ids = get_due_pack_ids(
            conn,
            include_pack_ids=include_pack_ids,
            backfill=args.backfill_difficulty,
            retry_mismatches=args.retry_mismatches,
            limit=args.difficulty_limit,
        )
        print(f"Пакетов в очереди сложности: {len(difficulty_pack_ids)}")

        difficulty_results = []
        for index, pack_id in enumerate(difficulty_pack_ids, start=1):
            result = process_pack_difficulty(session, conn, pack_id)
            difficulty_results.append(result)
            details = ""
            if result.status in {"complete", "partial_complete"}:
                details = f", {result.questions_updated} вопросов, {result.teams} команд"
            elif result.error:
                details = f", {result.error}"
            print(
                f"  [{index}/{len(difficulty_pack_ids)}] pack/{pack_id}: "
                f"{result.status}{details}"
            )

        validation = validate_database(conn, before)
        conn.close()

        status_counts = Counter(result.status for result in difficulty_results)
        return {
            "dry_run": False,
            "backup": str(backup_path) if backup_path else None,
            "scrape": scrape_summary,
            "bootstrap_statuses": bootstrap_counts,
            "difficulty_packs": len(difficulty_pack_ids),
            "difficulty_statuses": dict(status_counts),
            "questions_with_new_difficulty": sum(
                result.questions_updated for result in difficulty_results
            ),
            "validation": validation,
        }
    finally:
        session.close()


def _run_dry(args: argparse.Namespace, db_path: Path, session) -> dict:
    """Проверка плана запуска без DDL, резервной копии и записей."""
    conn = get_readonly_connection(db_path)
    try:
        counts = snapshot_counts(conn)
        if args.pack is not None:
            result = process_pack_difficulty(
                session,
                conn,
                args.pack,
                dry_run=True,
            )
            pack_summary = result.as_dict()
            pack_summary.pop("stats", None)
            print(json.dumps(pack_summary, ensure_ascii=False, indent=2))
            return {
                "dry_run": True,
                "counts": counts,
                "pack": pack_summary,
            }

        max_known_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM packs WHERE id < 1000000"
        ).fetchone()[0]
        last_pack_id = get_last_pack_id(session) if not args.difficulty_only else max_known_id
        start_id = args.start if args.start is not None else max_known_id + 1
        new_candidates = max(0, last_pack_id - start_id + 1)

        has_status_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pack_result_status'"
        ).fetchone() is not None
        if args.backfill_difficulty:
            if has_status_table:
                difficulty_candidates = conn.execute(
                    """SELECT COUNT(*) FROM packs p
                       LEFT JOIN pack_result_status prs ON prs.pack_id = p.id
                       WHERE p.parse_status = 'parsed' AND p.id < 1000000
                         AND COALESCE(p.teams_played, 0) > 0
                         AND COALESCE(prs.status, 'pending') != 'complete'"""
                ).fetchone()[0]
            else:
                difficulty_candidates = conn.execute(
                    """SELECT COUNT(*) FROM packs p
                       WHERE p.parse_status = 'parsed' AND p.id < 1000000
                         AND COALESCE(p.teams_played, 0) > 0"""
                ).fetchone()[0]
        else:
            difficulty_candidates = new_candidates

        result = {
            "dry_run": True,
            "counts": counts,
            "max_known_pack_id": max_known_id,
            "last_site_pack_id": last_pack_id,
            "content_candidates": new_candidates,
            "difficulty_candidates_estimate": difficulty_candidates,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Безопасно обновить вопросы и статистику сложности"
    )
    parser.add_argument("--db", default=str(DB_PATH), help="Путь к SQLite базе")
    parser.add_argument("--dry-run", action="store_true", help="Ничего не записывать")
    parser.add_argument("--difficulty-only", action="store_true", help="Не парсить новые пакеты")
    parser.add_argument(
        "--backfill-difficulty",
        action="store_true",
        help="Обработать историческую очередь пакетов с результатами",
    )
    parser.add_argument("--difficulty-limit", type=int, default=None)
    parser.add_argument(
        "--retry-mismatches",
        action="store_true",
        help="Повторно проверить ранее отложенные несовпадения",
    )
    parser.add_argument("--pack", type=int, default=None, help="Только один существующий пакет")
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    parser.add_argument("--max-packs", type=int, default=None)
    parser.add_argument("--date-from", type=str, default=None)
    parser.add_argument("--date-to", type=str, default=None)
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--json", action="store_true", help="Напечатать итог как JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_update(args)
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    if args.json or not args.dry_run:
        print("\nИтог:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
