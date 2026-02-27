"""Скрипт валидации: случайная выборка классифицированных вопросов для ручной проверки.

Использование:
    python scripts/validate_sample.py --sample 50
    python scripts/validate_sample.py --sample 30 --model qwen2.5:14b-instruct-q4_K_M
"""

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from database.db import get_connection
from classifier.taxonomy import get_label

OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_classified_sample(conn, sample_size: int, model_name: str = None):
    """Выбрать случайные классифицированные вопросы из разных пакетов."""
    params = []
    where = ""
    if model_name:
        where = "AND qt.model_name = ?"
        params.append(model_name)

    rows = conn.execute(f"""
        SELECT q.id, q.text, q.answer, q.comment,
               p.title AS pack_title, p.id AS pack_id,
               c.name_ru AS category, s.name_ru AS subcategory,
               qt.confidence, qt.model_name,
               c.sort_order AS cat_num, s.sort_order AS sub_num
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN packs p ON q.pack_id = p.id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE 1=1 {where}
        ORDER BY RANDOM()
        LIMIT ?
    """, params + [sample_size * 3]).fetchall()  # берём x3 для разнообразия пакетов

    # Отбираем так, чтобы было максимум разных пакетов
    result = []
    seen_packs = set()
    for r in rows:
        pack_id = r["pack_id"]
        if pack_id not in seen_packs or len(result) < sample_size:
            result.append(dict(r))
            seen_packs.add(pack_id)
        if len(result) >= sample_size:
            break

    return result


def display_for_validation(questions: list):
    """Показать вопросы для ручной проверки."""
    print(f"\n{'='*70}")
    print(f"  ВАЛИДАЦИЯ ВЫБОРКИ: {len(questions)} вопросов из {len(set(q['pack_id'] for q in questions))} пакетов")
    print(f"{'='*70}\n")

    for i, q in enumerate(questions, 1):
        print(f"--- Вопрос {i}/{len(questions)} (ID: {q['id']}, пакет: {q['pack_title']}) ---")
        print(f"Текст: {q['text'][:200]}")
        if q.get("comment"):
            print(f"Комментарий: {q['comment'][:100]}")
        print(f"Ответ: {q['answer']}")
        print(f"Классификация: {q['category']} → {q['subcategory']} "
              f"(conf: {q['confidence']:.2f}, модель: {q['model_name']})")
        print()


def save_validation_set(questions: list, output_path: Path):
    """Сохранить выборку в JSON для последующей разметки."""
    data = []
    for q in questions:
        data.append({
            "id": q["id"],
            "text": q["text"][:300],
            "answer": q["answer"],
            "comment": q.get("comment", ""),
            "pack": q["pack_title"],
            "model_category": q["category"],
            "model_subcategory": q["subcategory"],
            "model_confidence": q["confidence"],
            "model_name": q["model_name"],
            "correct": None,  # заполнить вручную: true/false
            "correct_category": None,  # если неправильно — указать правильную
        })

    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nВыборка сохранена: {output_path}")
    print("Откройте файл и заполните поля 'correct' (true/false) и 'correct_category' (если неправильно)")


def analyze_validation(path: Path):
    """Проанализировать заполненную валидацию."""
    data = json.loads(path.read_text(encoding="utf-8"))

    validated = [d for d in data if d.get("correct") is not None]
    if not validated:
        print("Нет заполненных записей. Заполните поле 'correct' в файле.")
        return

    correct = sum(1 for d in validated if d["correct"])
    total = len(validated)
    accuracy = correct / total * 100

    print(f"\n{'='*50}")
    print(f"  Результаты валидации")
    print(f"{'='*50}")
    print(f"  Проверено: {total}")
    print(f"  Правильно: {correct} ({accuracy:.1f}%)")
    print(f"  Ошибки: {total - correct}")

    # Ошибки по категориям
    errors = [d for d in validated if not d["correct"]]
    if errors:
        print(f"\n  Ошибки:")
        for e in errors:
            correct_cat = e.get("correct_category", "?")
            print(f"    ID {e['id']}: модель={e['model_category']}, правильно={correct_cat}")

    # Accuracy по confidence
    conf_groups = {"0.3-0.5": [], "0.5-0.7": [], "0.7-0.9": [], "0.9-1.0": []}
    for d in validated:
        c = d["model_confidence"]
        if c < 0.5:
            conf_groups["0.3-0.5"].append(d["correct"])
        elif c < 0.7:
            conf_groups["0.5-0.7"].append(d["correct"])
        elif c < 0.9:
            conf_groups["0.7-0.9"].append(d["correct"])
        else:
            conf_groups["0.9-1.0"].append(d["correct"])

    print(f"\n  Калибровка confidence:")
    for group, results in conf_groups.items():
        if results:
            acc = sum(results) / len(results) * 100
            print(f"    {group}: {acc:.0f}% accuracy ({len(results)} вопросов)")
        else:
            print(f"    {group}: нет данных")


def main():
    parser = argparse.ArgumentParser(description="Валидация классификации на случайной выборке")
    parser.add_argument("--sample", type=int, default=50, help="Размер выборки (по умолчанию 50)")
    parser.add_argument("--model", type=str, default=None, help="Фильтр по модели")
    parser.add_argument("--analyze", type=str, default=None,
                       help="Путь к заполненному JSON для анализа результатов")
    args = parser.parse_args()

    if args.analyze:
        analyze_validation(Path(args.analyze))
        return

    conn = get_connection(DB_PATH)
    questions = get_classified_sample(conn, args.sample, args.model)

    if not questions:
        print("Нет классифицированных вопросов для валидации.")
        conn.close()
        return

    display_for_validation(questions)

    output_path = OUTPUT_DIR / "validation_sample.json"
    save_validation_set(questions, output_path)

    conn.close()


if __name__ == "__main__":
    main()
