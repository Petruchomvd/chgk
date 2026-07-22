"""Готовит пул кандидатов для ручного отбора (30 итоговых вопросов).

Семплируем ~20 вопросов на каждый из 3 блоков, диверсифицированных по категориям/субкатегориям.
Вывод — JSON, который потом читает Claude и отбирает с собственной оценкой сложности.
"""
from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "chgk_analysis.db"
OUT = ROOT / "data" / "candidate_test_pool.json"
SEED = 20260421

BLOCKS = {
    "profile_history": {
        "categories": [1],
        "per_subcat": 5,
    },
    "adjacent_humanities": {
        "categories": [2, 4, 5, 9, 10, 11],
        "per_subcat": 2,
    },
    "outside_comfort": {
        "categories": [3, 6, 7, 8, 12, 13, 14],
        "per_subcat": 2,
    },
}


def main() -> None:
    rng = random.Random(SEED)
    conn = sqlite3.connect(DB)
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
            s.category_id, s.id AS subcat_id, s.name_ru AS subcat_ru,
            c.name AS cat_name,
            pt.confidence
        FROM pt
        JOIN subcategories s ON s.id = pt.subcategory_id
        JOIN categories c ON c.id = s.category_id
        JOIN questions q ON q.id = pt.question_id
        JOIN packs p ON p.id = q.pack_id
        WHERE p.difficulty IS NOT NULL
          AND q.text IS NOT NULL AND length(q.text) BETWEEN 80 AND 900
          AND q.answer IS NOT NULL AND length(q.answer) BETWEEN 2 AND 200
          AND (q.razdatka_pic IS NULL OR q.razdatka_pic = '')
          AND pt.confidence >= 0.8
        """
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    # Group by subcategory
    by_subcat: dict[int, list[dict]] = {}
    for r in rows:
        by_subcat.setdefault(r["subcat_id"], []).append(r)

    # Get list of subcategories per category
    cur.execute("SELECT id, category_id, name_ru FROM subcategories")
    subcat_info = {sid: (cat_id, name) for sid, cat_id, name in cur.fetchall()}

    result: dict[str, list[dict]] = {}
    for block_name, cfg in BLOCKS.items():
        picked: list[dict] = []
        for cat_id in cfg["categories"]:
            # subcats of this category
            subs = [sid for sid, (cid, _) in subcat_info.items() if cid == cat_id]
            for sid in subs:
                pool = by_subcat.get(sid, [])[:]
                rng.shuffle(pool)
                picked.extend(pool[: cfg["per_subcat"]])
        result[block_name] = picked

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, lst in result.items():
        print(f"{name}: {len(lst)} candidates")


if __name__ == "__main__":
    main()
