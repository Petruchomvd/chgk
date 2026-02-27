"""Генерация разнообразного набора вопросов для бенчмарка.

Использование:
    python scripts/build_benchmark.py                  # 50 вопросов из разных пакетов
    python scripts/build_benchmark.py --size 100       # 100 вопросов
    python scripts/build_benchmark.py --seed 42        # фиксированный seed

Выбирает вопросы из максимально разных пакетов.
Сохраняет ID в test_benchmark_ids.py для использования в benchmark.py.
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from database.db import get_connection

OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def build_diverse_sample(conn, size: int = 50, seed: int = None) -> list[dict]:
    """Выбрать вопросы из максимально разных пакетов.

    Стратегия: из каждого пакета берём максимум 1 вопрос,
    пока не наберём нужное количество.
    """
    if seed is not None:
        random.seed(seed)

    # Все пакеты с вопросами
    packs = conn.execute("""
        SELECT p.id, p.title, COUNT(q.id) as q_count
        FROM packs p
        JOIN questions q ON q.pack_id = p.id
        WHERE p.parse_status = 'parsed'
        GROUP BY p.id
        HAVING q_count >= 3
        ORDER BY RANDOM()
    """).fetchall()

    selected = []
    used_packs = set()

    # Раунд 1: по 1 вопросу из каждого пакета
    for pack in packs:
        if len(selected) >= size:
            break

        pack_id = pack[0]
        if pack_id in used_packs:
            continue

        question = conn.execute("""
            SELECT q.id, q.text, q.answer, q.comment, q.pack_id, p.title as pack_title
            FROM questions q
            JOIN packs p ON q.pack_id = p.id
            WHERE q.pack_id = ?
            ORDER BY RANDOM()
            LIMIT 1
        """, (pack_id,)).fetchone()

        if question:
            selected.append(dict(question))
            used_packs.add(pack_id)

    # Раунд 2: если пакетов не хватило, берём ещё из уже использованных
    if len(selected) < size:
        remaining = size - len(selected)
        extra = conn.execute(f"""
            SELECT q.id, q.text, q.answer, q.comment, q.pack_id, p.title as pack_title
            FROM questions q
            JOIN packs p ON q.pack_id = p.id
            WHERE q.id NOT IN ({','.join('?' * len(selected))})
            ORDER BY RANDOM()
            LIMIT ?
        """, [s["id"] for s in selected] + [remaining]).fetchall()
        selected.extend(dict(r) for r in extra)

    return selected[:size]


def save_benchmark_ids(ids: list[int], path: Path = None):
    """Сохранить ID в test_benchmark_ids.py."""
    if path is None:
        path = Path(__file__).parent.parent / "test_benchmark_ids.py"

    # Форматируем по 8 ID в строку
    lines = []
    for i in range(0, len(ids), 8):
        chunk = ids[i:i+8]
        lines.append("    " + ", ".join(str(x) for x in chunk) + ",")

    content = (
        '"""Фиксированный набор вопросов для бенчмарка (из разных пакетов)."""\n\n'
        f"BENCHMARK_IDS = [\n"
        + "\n".join(lines)
        + "\n]\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Генерация бенчмарк-набора ЧГК")
    parser.add_argument("--size", type=int, default=50, help="Размер выборки (default: 50)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed для воспроизводимости")
    args = parser.parse_args()

    conn = get_connection(DB_PATH)
    questions = build_diverse_sample(conn, size=args.size, seed=args.seed)

    if not questions:
        print("Нет вопросов в БД!")
        conn.close()
        return

    ids = [q["id"] for q in questions]
    packs = set(q["pack_id"] for q in questions)

    print(f"Выбрано {len(questions)} вопросов из {len(packs)} разных пакетов")
    print()

    # Показать примеры
    for i, q in enumerate(questions[:5], 1):
        answer = (q["answer"] or "")[:40]
        pack = (q["pack_title"] or "")[:30]
        print(f"  {i}. [{answer}] — {pack}")
    if len(questions) > 5:
        print(f"  ... ещё {len(questions) - 5}")

    # Сохранить
    ids_path = save_benchmark_ids(ids)
    print(f"\nID сохранены: {ids_path}")

    # JSON-бэкап
    backup_path = OUTPUT_DIR / "benchmark_ids.json"
    backup_path.write_text(
        json.dumps({"size": len(ids), "packs": len(packs), "ids": ids},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"JSON-бэкап: {backup_path}")

    conn.close()


if __name__ == "__main__":
    main()
