"""Выгрузка спорных вопросов для ручной проверки классификации.

Выбирает вопросы по нескольким критериям "спорности":
1. Низкая уверенность первой категории (conf < 0.7)
2. Близкие confidence двух категорий (разница < 0.1)
3. Категории из известных confusion-пар (Логика↔предметная, Природа↔Наука, и т.д.)
4. Категория "Логика и wordplay" как primary — часто ошибочна

Генерирует Markdown-файл для удобной ручной проверки.

Использование:
    python scripts/review_disputed.py
    python scripts/review_disputed.py --limit 200
    python scripts/review_disputed.py --category "Логика и wordplay" --limit 50
    python scripts/review_disputed.py --mode confused   # только confusion-пары
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from database.db import get_connection

MODEL = "qwen/qwen-2.5-72b-instruct"

OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Известные confusion-пары (expected → часто ошибочно ставится)
CONFUSION_PAIRS = {
    (14, 2),   # Логика → Литература
    (14, 6),   # Логика → Музыка
    (14, 7),   # Логика → Кино
    (14, 8),   # Логика → Спорт
    (13, 3),   # Природа → Наука
    (3, 13),   # Наука → Природа
    (1, 11),   # История → Общество
    (11, 1),   # Общество → История
    (1, 2),    # История → Литература
    (9, 4),    # Язык → География
    (6, 7),    # Музыка → Кино
    (7, 6),    # Кино → Музыка
    (12, 11),  # Быт → Общество
    (1, 12),   # История → Быт
}

CAT_NAMES = {
    1: "История", 2: "Литература", 3: "Наука и технологии",
    4: "География", 5: "Искусство", 6: "Музыка",
    7: "Кино и театр", 8: "Спорт", 9: "Язык и лингвистика",
    10: "Религия и мифология", 11: "Общество и политика",
    12: "Быт и повседневность", 13: "Природа и животные",
    14: "Логика и wordplay",
}


def fetch_questions_with_topics(conn):
    """Все вопросы с их topic assignments для данной модели."""
    rows = conn.execute("""
        SELECT q.id, q.text, q.answer, q.comment,
               c.id AS cat_id, c.name_ru AS cat_name,
               sc.name_ru AS sub_name, sc.sort_order AS sub_num,
               qt.confidence
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN subcategories sc ON qt.subcategory_id = sc.id
        JOIN categories c ON sc.category_id = c.id
        WHERE qt.model_name = ?
        ORDER BY q.id, qt.confidence DESC
    """, (MODEL,)).fetchall()

    # Группируем по question_id
    questions = {}
    for r in rows:
        qid = r["id"]
        if qid not in questions:
            questions[qid] = {
                "id": qid,
                "text": r["text"],
                "answer": r["answer"],
                "comment": r["comment"] or "",
                "topics": [],
            }
        questions[qid]["topics"].append({
            "cat_id": r["cat_id"],
            "cat_name": r["cat_name"],
            "sub_name": r["sub_name"],
            "sub_num": r["sub_num"],
            "conf": r["confidence"],
        })

    return questions


def score_dispute(q):
    """Оценка "спорности" вопроса. Чем выше — тем спорнее."""
    topics = q["topics"]
    if not topics:
        return 0

    score = 0
    reasons = []

    primary = topics[0]
    primary_cat = primary["cat_id"]
    primary_conf = primary["conf"]

    # 1. Низкая уверенность primary
    if primary_conf < 0.6:
        score += 3
        reasons.append(f"low_conf({primary_conf:.2f})")
    elif primary_conf < 0.7:
        score += 1
        reasons.append(f"med_conf({primary_conf:.2f})")

    # 2. Близкие confidence (если 2+ тем)
    if len(topics) >= 2:
        diff = primary_conf - topics[1]["conf"]
        if diff < 0.05:
            score += 3
            reasons.append(f"close_conf(Δ={diff:.2f})")
        elif diff < 0.1:
            score += 2
            reasons.append(f"near_conf(Δ={diff:.2f})")

        # 3. Confusion-пара
        secondary_cat = topics[1]["cat_id"]
        if (primary_cat, secondary_cat) in CONFUSION_PAIRS:
            score += 2
            reasons.append(f"confusion({CAT_NAMES.get(primary_cat, '?')}→{CAT_NAMES.get(secondary_cat, '?')})")

    # 4. Логика как primary — часто ошибка
    if primary_cat == 14:  # Логика и wordplay
        score += 1
        reasons.append("logic_primary")

    q["dispute_score"] = score
    q["dispute_reasons"] = reasons
    return score


def filter_by_category(questions, cat_name):
    """Фильтр: primary категория содержит cat_name."""
    result = {}
    for qid, q in questions.items():
        if q["topics"] and cat_name.lower() in q["topics"][0]["cat_name"].lower():
            result[qid] = q
    return result


def filter_confused_only(questions):
    """Только вопросы с confusion-парами."""
    result = {}
    for qid, q in questions.items():
        topics = q["topics"]
        if len(topics) >= 2:
            pair = (topics[0]["cat_id"], topics[1]["cat_id"])
            if pair in CONFUSION_PAIRS:
                result[qid] = q
    return result


def generate_markdown(questions, output_path, title="Спорные вопросы"):
    """Генерация MD-файла для ручной проверки."""
    # Группируем по primary категории
    by_cat = defaultdict(list)
    for q in questions:
        primary_cat = q["topics"][0]["cat_name"] if q["topics"] else "???"
        by_cat[primary_cat].append(q)

    lines = [
        f"# {title}",
        "",
        f"Модель: `{MODEL}`",
        f"Всего вопросов для проверки: **{len(questions)}**",
        "",
        "## Как проверять",
        "",
        "Для каждого вопроса указана классификация модели. Ваша задача:",
        "1. Прочитать вопрос + ответ + комментарий",
        "2. Если классификация **верна** — пропустить",
        "3. Если **неверна** — дописать `НЕВЕРНО → Правильная категория`",
        "",
        "---",
        "",
    ]

    for cat_name in sorted(by_cat.keys()):
        cat_questions = by_cat[cat_name]
        lines.append(f"## {cat_name} ({len(cat_questions)} вопросов)")
        lines.append("")

        for q in cat_questions:
            topics_str = " + ".join(
                f"{t['cat_name']} ({t['conf']:.2f})"
                for t in q["topics"]
            )
            reasons_str = ", ".join(q.get("dispute_reasons", []))

            lines.append(f"### ID {q['id']} [спорность: {q.get('dispute_score', 0)}, {reasons_str}]")
            lines.append(f"**Модель:** {topics_str}")
            lines.append(f"**Ответ:** {q['answer']}")
            text = q["text"][:500]
            lines.append(f"> {text}")
            if q["comment"]:
                comment = q["comment"][:200]
                lines.append(f"")
                lines.append(f"*Комментарий:* {comment}")
            lines.append("")
            lines.append("**Проверка:** ✅ / ❌ →")
            lines.append("")
            lines.append("---")
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Выгрузка спорных вопросов для ручной проверки")
    parser.add_argument("--limit", type=int, default=100,
                        help="Макс. кол-во вопросов (по умолчанию 100)")
    parser.add_argument("--category", type=str, default=None,
                        help="Фильтр по primary категории (подстрока)")
    parser.add_argument("--mode", choices=["all", "confused", "low_conf"], default="all",
                        help="all=все спорные, confused=только confusion-пары, low_conf=только низкая уверенность")
    parser.add_argument("--min-score", type=int, default=1,
                        help="Минимальный порог спорности (по умолчанию 1)")
    args = parser.parse_args()

    conn = get_connection(DB_PATH)
    questions = fetch_questions_with_topics(conn)
    conn.close()

    print(f"Всего вопросов модели {MODEL}: {len(questions)}")

    # Фильтры
    if args.category:
        questions = filter_by_category(questions, args.category)
        print(f"После фильтра по категории '{args.category}': {len(questions)}")

    if args.mode == "confused":
        questions = filter_confused_only(questions)
        print(f"После фильтра confusion-пар: {len(questions)}")

    # Оценка спорности
    for q in questions.values():
        score_dispute(q)

    if args.mode == "low_conf":
        questions = {qid: q for qid, q in questions.items()
                     if any("low_conf" in r or "med_conf" in r for r in q.get("dispute_reasons", []))}
        print(f"После фильтра по низкой уверенности: {len(questions)}")

    # Отбираем самые спорные
    disputed = [q for q in questions.values() if q["dispute_score"] >= args.min_score]
    disputed.sort(key=lambda q: -q["dispute_score"])
    disputed = disputed[:args.limit]

    if not disputed:
        print("Нет спорных вопросов по заданным критериям.")
        return

    # Статистика
    print(f"\nОтобрано для проверки: {len(disputed)}")
    score_dist = defaultdict(int)
    for q in disputed:
        score_dist[q["dispute_score"]] += 1
    print("Распределение по спорности:", dict(sorted(score_dist.items(), reverse=True)))

    reason_counts = defaultdict(int)
    for q in disputed:
        for r in q.get("dispute_reasons", []):
            tag = r.split("(")[0]
            reason_counts[tag] += 1
    print("Причины:", dict(sorted(reason_counts.items(), key=lambda x: -x[1])))

    # Генерация
    suffix = f"_{args.category}" if args.category else ""
    suffix += f"_{args.mode}" if args.mode != "all" else ""
    output_path = OUTPUT_DIR / f"review_disputed{suffix}.md"
    generate_markdown(disputed, output_path,
                      title=f"Спорные вопросы ({args.category or 'все категории'})")
    print(f"\nФайл сохранён: {output_path}")


if __name__ == "__main__":
    main()
