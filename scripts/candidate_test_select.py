"""Подбор вопросов для тестирования кандидата (историк, 2-3 курс).

14 категорий × 10 вопросов = 140 вопросов.
Распределение по сложности: 5 easy / 3 medium / 2 hard.
Для категории "История" — диверсификация по 5 субкатегориям.

Выход:
  data/candidate_test_questions.md — для кандидата (без ответов)
  data/candidate_test_answers.md   — для проверяющего (с ответами, комментариями)
"""
from __future__ import annotations

import random
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "chgk_analysis.db"
OUT_DIR = ROOT / "data"

SEED = 20260421
PER_CATEGORY = 10
SPLIT = {"easy": 5, "medium": 3, "hard": 2}
HISTORY_CAT_ID = 1
HISTORY_MAX_PER_SUBCAT = 3

CATEGORY_NAMES_RU = {
    1: "История",
    2: "Литература",
    3: "Наука и технологии",
    4: "География",
    5: "Искусство",
    6: "Музыка",
    7: "Кино и театр",
    8: "Спорт",
    9: "Язык и лингвистика",
    10: "Религия и мифология",
    11: "Общество и политика",
    12: "Быт и повседневность",
    13: "Природа и животные",
    14: "Логика и wordplay",
}

CATEGORY_ORDER = [1, 11, 10, 2, 5, 4, 9, 6, 7, 12, 13, 3, 8, 14]

TIER_LABEL = {"easy": "простой", "medium": "средний", "hard": "сложный"}


def fetch_pool(conn: sqlite3.Connection) -> dict[int, dict[str, list[dict]]]:
    cur = conn.cursor()
    cur.execute(
        """
        WITH primary_topic AS (
            SELECT question_id, MIN(id) AS min_id
            FROM question_topics
            WHERE method='openrouter_2stage'
            GROUP BY question_id
        ),
        pt AS (
            SELECT qt.question_id, qt.subcategory_id, qt.confidence
            FROM question_topics qt
            JOIN primary_topic p ON p.min_id = qt.id
        )
        SELECT
            q.id, q.text, q.answer, q.zachet, q.comment, q.source, q.authors,
            q.razdatka_text,
            p.difficulty, p.title AS pack_title,
            s.category_id, s.name_ru AS subcat_ru,
            pt.confidence
        FROM pt
        JOIN subcategories s ON s.id = pt.subcategory_id
        JOIN questions q ON q.id = pt.question_id
        JOIN packs p ON p.id = q.pack_id
        WHERE p.difficulty IS NOT NULL
          AND q.text IS NOT NULL AND length(q.text) BETWEEN 50 AND 900
          AND q.answer IS NOT NULL AND length(q.answer) BETWEEN 2 AND 200
          AND (q.razdatka_pic IS NULL OR q.razdatka_pic = '')
          AND pt.confidence >= 0.7
        """
    )
    cols = [d[0] for d in cur.description]
    pool: dict[int, dict[str, list[dict]]] = defaultdict(lambda: {"easy": [], "medium": [], "hard": []})
    for row in cur.fetchall():
        q = dict(zip(cols, row))
        diff = q["difficulty"]
        if diff < 3.5:
            tier = "easy"
        elif diff < 6.0:
            tier = "medium"
        else:
            tier = "hard"
        pool[q["category_id"]][tier].append(q)
    return pool


def pick_for_category(
    rng: random.Random,
    cat_id: int,
    buckets: dict[str, list[dict]],
) -> list[dict]:
    selected: list[dict] = []
    subcat_used: dict[int, int] = defaultdict(int)
    for tier, n in SPLIT.items():
        pool = buckets[tier][:]
        rng.shuffle(pool)
        chosen = []
        for q in pool:
            if len(chosen) >= n:
                break
            if cat_id == HISTORY_CAT_ID:
                if subcat_used[q["subcat_ru"]] >= HISTORY_MAX_PER_SUBCAT:
                    continue
            chosen.append(q)
            subcat_used[q["subcat_ru"]] += 1
        if len(chosen) < n:
            for q in pool:
                if q in chosen:
                    continue
                chosen.append(q)
                if len(chosen) >= n:
                    break
        for q in chosen:
            q["tier"] = tier
        selected.extend(chosen)
    return selected


def clean_text(t: str | None) -> str:
    if not t:
        return ""
    return " ".join(t.split())


def razdatka_for(q: dict) -> str:
    rz = clean_text(q.get("razdatka_text"))
    if not rz:
        return ""
    if rz == clean_text(q.get("text")):
        return ""
    return rz


def render_candidate(selection: dict[int, list[dict]]) -> str:
    lines: list[str] = []
    lines.append("# Тест кандидата — ЧГК")
    lines.append("")
    lines.append("**Кандидат:** студент-историк, 2-3 курс")
    lines.append("**Всего вопросов:** 140 (14 категорий × 10)")
    lines.append("**Сложность:** 5 простых / 3 средних / 2 сложных в каждой категории")
    lines.append("")
    lines.append("---")
    lines.append("")
    q_num = 0
    for cat_id in CATEGORY_ORDER:
        lines.append(f"## {CATEGORY_NAMES_RU[cat_id]}")
        lines.append("")
        for q in selection[cat_id]:
            q_num += 1
            lines.append(f"### Вопрос {q_num} ({TIER_LABEL[q['tier']]})")
            lines.append("")
            rz = razdatka_for(q)
            if rz:
                lines.append(f"*Раздаточный материал:* {rz}")
                lines.append("")
            lines.append(clean_text(q["text"]))
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def render_answers(selection: dict[int, list[dict]]) -> str:
    lines: list[str] = []
    lines.append("# Тест кандидата — ответы")
    lines.append("")
    lines.append("**Кандидат:** студент-историк, 2-3 курс")
    lines.append("**Всего вопросов:** 140")
    lines.append("")
    lines.append("Формат: вопрос → ответ → комментарий → источник/автор → категория/подкатегория/сложность.")
    lines.append("")
    lines.append("---")
    lines.append("")
    q_num = 0
    for cat_id in CATEGORY_ORDER:
        lines.append(f"## {CATEGORY_NAMES_RU[cat_id]}")
        lines.append("")
        for q in selection[cat_id]:
            q_num += 1
            lines.append(f"### Вопрос {q_num} ({TIER_LABEL[q['tier']]}, difficulty={q['difficulty']:.1f})")
            lines.append("")
            rz = razdatka_for(q)
            if rz:
                lines.append(f"*Раздаточный материал:* {rz}")
                lines.append("")
            lines.append(f"**Вопрос:** {clean_text(q['text'])}")
            lines.append("")
            lines.append(f"**Ответ:** {clean_text(q['answer'])}")
            lines.append("")
            if q.get("zachet"):
                lines.append(f"**Зачёт:** {clean_text(q['zachet'])}")
                lines.append("")
            if q.get("comment"):
                lines.append(f"**Комментарий:** {clean_text(q['comment'])}")
                lines.append("")
            meta_bits = []
            if q.get("source"):
                meta_bits.append(f"источник: {clean_text(q['source'])}")
            if q.get("authors"):
                meta_bits.append(f"автор: {clean_text(q['authors'])}")
            meta_bits.append(f"подкатегория: {q['subcat_ru']}")
            meta_bits.append(f"пакет: {clean_text(q.get('pack_title') or '')}")
            meta_bits.append(f"id={q['id']}")
            lines.append("*" + " · ".join(meta_bits) + "*")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    rng = random.Random(SEED)
    conn = sqlite3.connect(DB)
    pool = fetch_pool(conn)

    for cat_id in CATEGORY_ORDER:
        for tier, n in SPLIT.items():
            have = len(pool.get(cat_id, {}).get(tier, []))
            if have < n:
                print(f"[warn] cat={cat_id} tier={tier}: need {n}, have {have}")

    selection: dict[int, list[dict]] = {}
    for cat_id in CATEGORY_ORDER:
        selection[cat_id] = pick_for_category(rng, cat_id, pool[cat_id])

    OUT_DIR.mkdir(exist_ok=True)
    candidate_path = OUT_DIR / "candidate_test_questions.md"
    answers_path = OUT_DIR / "candidate_test_answers.md"
    candidate_path.write_text(render_candidate(selection), encoding="utf-8")
    answers_path.write_text(render_answers(selection), encoding="utf-8")

    total = sum(len(v) for v in selection.values())
    print(f"Selected {total} questions across {len(selection)} categories.")
    print(f"  Candidate file: {candidate_path}")
    print(f"  Answers file:   {answers_path}")


if __name__ == "__main__":
    main()
