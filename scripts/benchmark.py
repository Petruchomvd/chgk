"""Бенчмарк: прогоняет вопросы через указанную модель и выводит результаты.

Использование:
    python scripts/benchmark.py --model qwen2.5:14b-instruct-q4_K_M
    python scripts/benchmark.py --model qwen2.5:14b-instruct-q4_K_M --no-few-shot
    python scripts/benchmark.py --model qwen2.5:14b-instruct-q4_K_M --random 50
"""

import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DB_PATH
from database.db import get_connection
from classifier.local_llm import classify_question, classify_question_twostage


def run_benchmark(
    model: str,
    twostage: bool = True,
    few_shot: bool = True,
    question_ids: list = None,
):
    conn = get_connection(DB_PATH)

    # Загрузить вопросы
    if question_ids is None:
        from test_benchmark_ids import BENCHMARK_IDS
        question_ids = BENCHMARK_IDS

    placeholders = ",".join("?" * len(question_ids))
    rows = conn.execute(
        f"SELECT id, text, answer, comment, pack_id FROM questions WHERE id IN ({placeholders})",
        question_ids,
    ).fetchall()
    questions = {r["id"]: dict(r) for r in rows}
    packs = set(questions[qid]["pack_id"] for qid in question_ids if qid in questions)

    mode = "двухэтапный" if twostage else "одноэтапный"
    fs = "с few-shot" if few_shot else "без few-shot"
    print(f"Модель: {model}")
    print(f"Режим: {mode}, {fs}")
    print(f"Вопросов: {len(question_ids)} из {len(packs)} пакетов")
    print(f"{'='*70}")

    results = []
    start = time.time()

    for i, qid in enumerate(question_ids, 1):
        if qid not in questions:
            print(f"{i:2}. [ID {qid} не найден] => SKIP")
            continue

        q = questions[qid]

        if twostage:
            topics = classify_question_twostage(
                text=q["text"],
                answer=q["answer"],
                comment=q["comment"] or "",
                model=model,
            )
        else:
            topics = classify_question(
                text=q["text"],
                answer=q["answer"],
                comment=q["comment"] or "",
                model=model,
                few_shot=few_shot,
            )

        # Резолвим названия
        topic_strs = []
        if topics:
            for t in topics:
                row = conn.execute(
                    """SELECT c.name_ru, s.name_ru FROM subcategories s
                       JOIN categories c ON s.category_id = c.id
                       WHERE c.sort_order = ? AND s.sort_order = ?""",
                    (t["cat"], t["sub"]),
                ).fetchone()
                if row:
                    topic_strs.append(f"{row[0]}>{row[1]}({t['conf']})")

        ans = (q["answer"] or "")[:40]
        print(f"{i:2}. [{ans}] => {' | '.join(topic_strs)}")
        results.append({"id": qid, "answer": ans, "topics": topics or [], "labels": topic_strs})

    elapsed = time.time() - start
    total = len(results)
    print(f"\n{'='*70}")
    print(f"Время: {elapsed/60:.1f} мин ({elapsed/total:.1f} с/вопрос)" if total else "")

    # Сохранить результаты в файл
    suffix = f"{'_twostage' if twostage else ''}{'_nofs' if not few_shot else ''}"
    model_safe = model.replace(":", "_").replace("/", "_")
    out_path = Path(__file__).parent.parent / "output" / f"benchmark_{model_safe}{suffix}.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "model": model,
            "twostage": twostage,
            "few_shot": few_shot,
            "packs": len(packs),
            "elapsed_sec": elapsed,
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"Результаты: {out_path}")

    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Бенчмарк классификации ЧГК")
    parser.add_argument("--model", type=str, required=True, help="Модель Ollama")
    parser.add_argument("--twostage", action="store_true", default=True, help="Двухэтапный (default)")
    parser.add_argument("--onestage", action="store_true", help="Одноэтапный режим")
    parser.add_argument("--no-few-shot", action="store_true", help="Без few-shot примеров")
    parser.add_argument("--random", type=int, default=None,
                       help="Сгенерировать случайную выборку из N вопросов")
    args = parser.parse_args()

    question_ids = None
    if args.random:
        # Генерируем случайную выборку
        from scripts.build_benchmark import build_diverse_sample, save_benchmark_ids
        conn = get_connection(DB_PATH)
        questions = build_diverse_sample(conn, size=args.random)
        question_ids = [q["id"] for q in questions]
        packs = set(q["pack_id"] for q in questions)
        print(f"Сгенерирована случайная выборка: {len(question_ids)} вопросов из {len(packs)} пакетов\n")
        save_benchmark_ids(question_ids)
        conn.close()

    run_benchmark(
        model=args.model,
        twostage=not args.onestage,
        few_shot=not args.no_few_shot,
        question_ids=question_ids,
    )
