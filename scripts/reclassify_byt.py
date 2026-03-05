"""Переклассификация вопросов, отнесённых к «Быт и повседневность» (кат. 12).

Логика:
1. Берём все вопросы, которые qwen-72b отнёс к категории 12 (Быт)
2. Перепрогоняем через тот же провайдер с обновлённым промптом
3. Если новая категория ≠ 12 → обновляем в БД
4. Если по-прежнему 12 → оставляем
5. Выводим отчёт об изменениях
"""

import os
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: F401

from database.db import get_connection, get_subcategory_id, insert_topic
from classifier.classifier import classify_question
from classifier.taxonomy import get_label

MODEL_NAME = "qwen/qwen-2.5-72b-instruct"
BYT_CATEGORY = 12


def get_byt_questions(conn):
    """Получить все вопросы, классифицированные как Быт (12) данной моделью."""
    rows = conn.execute("""
        SELECT q.id, q.text, q.answer, q.comment,
               qt.subcategory_id, qt.confidence, qt.id as topic_id
        FROM questions q
        JOIN question_topics qt ON q.id = qt.question_id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE qt.model_name = ?
          AND c.sort_order = ?
        ORDER BY q.id
    """, (MODEL_NAME, BYT_CATEGORY)).fetchall()
    return [dict(r) for r in rows]


def delete_topic(conn, topic_id):
    """Удалить запись классификации по ID."""
    conn.execute("DELETE FROM question_topics WHERE id = ?", (topic_id,))


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Переклассификация Быт-вопросов")
    parser.add_argument("--provider", default="openrouter", help="Провайдер (default: openrouter)")
    parser.add_argument("--model", default=None, help="Модель (default: из провайдера)")
    parser.add_argument("--limit", type=int, default=None, help="Макс. вопросов")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, не менять БД")
    parser.add_argument("--workers", type=int, default=1, help="Параллельные воркеры")
    args = parser.parse_args()

    from classifier.providers import create_provider
    provider = create_provider(args.provider, model=args.model)
    actual_model = provider.config.model

    conn = get_connection(config.DB_PATH)
    questions = get_byt_questions(conn)

    if args.limit:
        questions = questions[:args.limit]

    total = len(questions)
    print(f"\n{'═' * 60}")
    print(f"  Переклассификация «Быт и повседневность»")
    print(f"{'═' * 60}")
    print(f"  Модель (исходная):  {MODEL_NAME}")
    print(f"  Модель (повторная): {actual_model}")
    print(f"  Вопросов с Быт:    {total}")
    print(f"  Dry run:            {args.dry_run}")
    print(f"{'═' * 60}\n")

    if total == 0:
        print("Нет вопросов с категорией Быт. Выходим.")
        return

    # Результаты
    stayed_byt = []      # Остались Быт
    changed = []         # Изменились
    errors = []          # Ошибки

    start_time = time.time()

    for i, q in enumerate(questions):
        try:
            topics = classify_question(
                provider=provider,
                text=q["text"],
                answer=q["answer"],
                comment=q.get("comment", ""),
                twostage=True,
                few_shot=True,
            )
        except Exception as e:
            errors.append({"id": q["id"], "error": str(e)})
            print(f"  [{i+1}/{total}] #{q['id']} — ОШИБКА: {e}")
            continue

        if not topics:
            errors.append({"id": q["id"], "error": "no topics returned"})
            print(f"  [{i+1}/{total}] #{q['id']} — нет результата")
            continue

        new_cat = topics[0]["cat"]
        new_sub = topics[0]["sub"]
        new_conf = topics[0]["conf"]
        new_label = get_label(new_cat, new_sub)
        old_label = get_label(BYT_CATEGORY, None)

        text_short = q["text"][:60].replace("\n", " ")

        if new_cat == BYT_CATEGORY:
            stayed_byt.append({
                "id": q["id"],
                "text": text_short,
                "new_sub": new_sub,
                "conf": new_conf,
            })
            print(f"  [{i+1}/{total}] #{q['id']} — Быт → Быт (подтв.) | {text_short}")
        else:
            changed.append({
                "id": q["id"],
                "text": text_short,
                "old_topic_id": q["topic_id"],
                "new_cat": new_cat,
                "new_sub": new_sub,
                "new_conf": new_conf,
                "new_label": new_label,
                "topics": topics,
            })
            print(f"  [{i+1}/{total}] #{q['id']} — Быт → {new_label} ({new_conf:.0%}) | {text_short}")

            # Обновляем БД (если не dry-run)
            if not args.dry_run:
                # Удаляем старую запись (Быт)
                delete_topic(conn, q["topic_id"])
                # Вставляем новую
                for t in topics:
                    sub_id = get_subcategory_id(conn, t["cat"], t["sub"])
                    if sub_id:
                        insert_topic(conn, q["id"], sub_id, t["conf"], "openrouter", MODEL_NAME)
                conn.commit()

    elapsed = time.time() - start_time

    # ═══ ОТЧЁТ ═══
    print(f"\n{'═' * 60}")
    print(f"  ОТЧЁТ: Переклассификация Быт")
    print(f"{'═' * 60}")
    print(f"  Всего обработано:  {total}")
    print(f"  Остались Быт:     {len(stayed_byt)} ({len(stayed_byt)/max(total,1)*100:.0f}%)")
    print(f"  Изменились:       {len(changed)} ({len(changed)/max(total,1)*100:.0f}%)")
    print(f"  Ошибки:           {len(errors)}")
    print(f"  Время:            {elapsed:.0f}с")
    print(f"  Dry run:          {args.dry_run}")
    print(f"{'═' * 60}")

    if changed:
        print(f"\n  Куда ушли вопросы из Быт:")
        # Группируем по новой категории
        cat_counts = {}
        for c in changed:
            label = get_label(c["new_cat"], None)
            cat_counts[label] = cat_counts.get(label, 0) + 1

        for label, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
            print(f"    {label:35s} → {count} вопросов")

    # Сохраняем отчёт в файл
    report = {
        "model": MODEL_NAME,
        "total": total,
        "stayed_byt": len(stayed_byt),
        "changed": len(changed),
        "errors": len(errors),
        "elapsed_sec": round(elapsed),
        "dry_run": args.dry_run,
        "changes": [
            {"id": c["id"], "new_cat": c["new_cat"], "new_label": c["new_label"], "conf": c["new_conf"]}
            for c in changed
        ],
    }
    report_path = Path(__file__).parent.parent / "docs" / "reclassify_byt_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Отчёт сохранён: {report_path}")


if __name__ == "__main__":
    main()
