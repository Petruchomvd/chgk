"""Поиск упоминаний советского кино и персонажей в вопросах ЧГК.

Считает раздельно по полям text / answer / comment, сохраняет доказательства
каждого совпадения и причины отбраковки. Исходные вопросы не изменяются.

Использование:
    python scripts/analyze_cinema.py --dry-run --limit 2000
    python scripts/analyze_cinema.py --limit 20000
    python scripts/analyze_cinema.py                  # полный прогон
    python scripts/analyze_cinema.py --report-only    # только отчёт из БД
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from cinema import frequency as cinema_frequency
from cinema import report as cinema_report
from cinema import store
from cinema.dictionary import load_dictionary
from cinema.matcher import CinemaMatcher
from config import DB_PATH, PROJECT_ROOT
from database.db import get_connection

DICT_DIR = PROJECT_ROOT / "data" / "soviet_cinema"
HINTS_PATH = DICT_DIR / "hints.json"
FREQ_PATH = DICT_DIR / "lemma_df.json"
REPORT_DIR = PROJECT_ROOT / "docs" / "reports"


def create_backup(db_path: Path) -> Path:
    """Резервная копия перед первой массовой записью.

    Специально НЕ удаляет старые копии: в .backups лежат копии предыдущего
    этапа (пайплайн сложности), на которые ссылается передача сессии.
    """
    backup_dir = db_path.parent / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}-{timestamp}-precinema.db"
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Упоминания советского кино в вопросах ЧГК"
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--dict", default=str(DICT_DIR), help="Папка снапшота словаря")
    parser.add_argument("--hints", default=str(HINTS_PATH))
    parser.add_argument("--freq", default=str(FREQ_PATH), help="Кэш частотности лемм")
    parser.add_argument(
        "--rebuild-freq", action="store_true", help="Пересчитать частотность лемм заново"
    )
    parser.add_argument("--limit", type=int, help="Обработать только первые N вопросов")
    parser.add_argument(
        "--dry-run", action="store_true", help="Ничего не писать в БД, только посчитать"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="Только собрать отчёт из последнего прогона"
    )
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument(
        "--keep-history",
        action="store_true",
        help="Не удалять результаты прошлых прогонов (по умолчанию остаётся один актуальный)",
    )
    parser.add_argument("--top", type=int, default=25, help="Размер топа в отчёте")
    parser.add_argument(
        "--cheatsheet",
        action="store_true",
        help="Собрать шпаргалку для команды: что учить и что при этом замечать",
    )
    parser.add_argument("--report-path", default=None)
    parser.add_argument("--json", action="store_true", help="Итог машиночитаемо")
    args = parser.parse_args()

    db_path = Path(args.db)
    started = time.time()

    if args.cheatsheet:
        conn = get_connection(db_path)
        try:
            run_id = store.latest_run_id(conn)
            if not run_id:
                print("Завершённых прогонов нет. Сначала запустите без --cheatsheet.")
                return 1
            markdown = cinema_report.build_cheatsheet(conn, run_id)
        finally:
            conn.close()
        path = Path(args.report_path or (REPORT_DIR / "2026-07-16-cinema-cheatsheet.md"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"Шпаргалка: {path}")
        return 0

    if args.report_only:
        conn = get_connection(db_path)
        try:
            run_id = store.latest_run_id(conn)
            if not run_id:
                print("Завершённых прогонов нет. Сначала запустите без --report-only.")
                return 1
            # Словарь нужен и здесь: без него в отчёте не будет раздела о том,
            # чем вообще искали и что отбраковано при сборке
            dict_stats = None
            if Path(args.freq).exists():
                freq = cinema_frequency.LemmaFrequency.load(args.freq)
                dict_stats = load_dictionary(args.dict, args.hints, frequency=freq).stats()
            markdown = cinema_report.build_markdown(
                conn, run_id, dict_stats=dict_stats, top_limit=args.top
            )
        finally:
            conn.close()
        path = Path(args.report_path or (REPORT_DIR / "2026-07-16-soviet-cinema.md"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"Отчёт: {path}")
        return 0

    # Частотность лемм считается по самой базе: она решает, банально ли слово
    freq_conn = get_connection(db_path)
    try:
        print("Частотность лемм по базе вопросов...")
        freq = cinema_frequency.load_or_build(
            args.freq, freq_conn, rebuild=args.rebuild_freq, progress=True
        )
        print(
            f"  вопросов: {freq.n_docs:,}, лемм: {len(freq.df):,}, "
            f"порог редкости: {freq.rare_threshold:.0f} вопросов".replace(",", " ")
        )
    finally:
        freq_conn.close()

    print("Загружаю словарь...")
    dictionary = load_dictionary(args.dict, args.hints, frequency=freq)
    stats = dictionary.stats()
    print(
        f"  сущностей: {stats['entities']} "
        f"(фильмов {stats['films']}, персонажей {stats['characters']}), "
        f"алиасов: {stats['aliases']}, отбраковано: {stats['rejected']}"
    )
    dict_version = (dictionary.meta.get("built_at") or "unknown")[:19]
    dict_hash_value = store.dict_hash(args.dict)

    backup_path = None
    if not args.dry_run and not args.no_backup:
        print("Резервная копия базы...")
        backup_path = create_backup(db_path)
        print(f"  {backup_path}")

    conn = get_connection(db_path)
    matcher = CinemaMatcher(dictionary)
    counters = {"questions": 0, "matches": 0, "rejected": 0}

    try:
        before = store.integrity_snapshot(conn)

        run_id = -1
        if not args.dry_run:
            store.sync_entities(conn, dictionary, dict_version=dict_version)
            run_id = store.start_run(
                conn,
                dict_version=dict_version,
                dict_hash_value=dict_hash_value,
                params={"limit": args.limit, "dict": str(args.dict)},
            )
            conn.commit()
            print(f"Прогон #{run_id}")

        print("Сканирую вопросы...")
        for row in store.iter_questions(conn, limit=args.limit):
            result = matcher.match_question(
                {"text": row["text"], "answer": row["answer"], "comment": row["comment"]}
            )
            counters["questions"] += 1
            counters["matches"] += len(result.matches)
            counters["rejected"] += len(result.rejected)

            if not args.dry_run and (result.matches or result.rejected):
                store.save_matches(conn, run_id, row["id"], result.matches, result.rejected)

            if counters["questions"] % 20000 == 0:
                if not args.dry_run:
                    conn.commit()
                elapsed = time.time() - started
                rate = counters["questions"] / elapsed if elapsed else 0
                print(
                    f"  {counters['questions']:,} вопросов, "
                    f"{counters['matches']:,} упоминаний "
                    f"({rate:.0f} вопр/с)".replace(",", " ")
                )

        if not args.dry_run:
            store.finish_run(
                conn,
                run_id,
                questions_scanned=counters["questions"],
                mentions_found=counters["matches"],
                rejected_count=counters["rejected"],
                notes="partial" if args.limit else "full",
            )
            dropped = 0
            if not args.keep_history:
                dropped = store.drop_other_runs(conn, run_id)
            conn.commit()
            if dropped:
                print(f"  удалено прошлых прогонов: {dropped}")

        # Исходные таблицы обязаны остаться нетронутыми
        after = store.integrity_snapshot(conn)
        if before != after:
            print("\nОШИБКА: исходные таблицы изменились. Откатываю.")
            print(f"  было:  {before}")
            print(f"  стало: {after}")
            conn.rollback()
            return 2

        markdown = None
        if not args.dry_run:
            markdown = cinema_report.build_markdown(
                conn, run_id, dict_stats=stats, top_limit=args.top
            )
    finally:
        conn.close()

    elapsed = time.time() - started
    if markdown:
        path = Path(args.report_path or (REPORT_DIR / "2026-07-16-soviet-cinema.md"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"\nОтчёт: {path}")

    summary = {
        "dry_run": args.dry_run,
        "questions_scanned": counters["questions"],
        "mentions": counters["matches"],
        "rejected": counters["rejected"],
        "dict_version": dict_version,
        "dict_hash": dict_hash_value,
        "backup": str(backup_path) if backup_path else None,
        "elapsed_sec": round(elapsed, 1),
        "integrity_unchanged": True,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"\nГотово за {elapsed:.0f} c: {counters['questions']:,} вопросов, "
            f"{counters['matches']:,} упоминаний, "
            f"{counters['rejected']:,} отбраковано".replace(",", " ")
        )
        if args.dry_run:
            print("Это был --dry-run: база не изменялась.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
