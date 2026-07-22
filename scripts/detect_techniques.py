"""Вторая ось разметки: ПРИЁМ вопроса (как он устроен, а не про что).

Кластеризация эмбеддингов показала: крупнейшие оси корпуса — механика
(замены, пропуски, раздатки), а не темы. Тема требует LLM, приём — нет:
маркеры вроде «ИКС», «заполните пропуск», «раздаточный материал»
детектируются регулярками с высокой точностью.

Пишет таблицу question_techniques (мульти-метки + главный приём)
и печатает статистику: распределение, приём × категория, приём × беручесть.

    python scripts/detect_techniques.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from database.db import get_connection

# Порядок = приоритет выбора главного приёма (сверху вниз).
# Блиц первым: он определяет формат целиком. «Чистый» — когда ничего не нашли.
PATTERNS = [
    ("блиц", re.compile(r"\bблиц|\bдуплет|\bтриплет|пентаблиц", re.I)),
    ("раздатка", re.compile(r"раздаточн|розданн|перед вами (изображ|фотограф|картин|текст|список)", re.I)),
    ("замена", re.compile(r"\bикс[а-яё]{0,3}\b|\bигрек[а-яё]{0,3}\b|\bальф[а-яё]{1,2}\b|"
                          r"заменил[иао]?\b|заменяет|есть замен[аы]", re.I)),
    ("пропуск", re.compile(r"пропуск|пропущен|восстановите|закончите|заполните", re.I)),
    ("цитата", re.compile(r"цитат[аеуы]|стихотворени|прослушайте|эпиграф", re.I)),
]


def detect(text: str, has_razdatka: bool) -> list[str]:
    found = []
    for name, rx in PATTERNS:
        if rx.search(text):
            found.append(name)
    if has_razdatka and "раздатка" not in found:
        found.append("раздатка")
    return found or ["чистый"]


def main() -> None:
    conn = get_connection(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS question_techniques (
            question_id       INTEGER PRIMARY KEY
                              REFERENCES questions(id) ON DELETE CASCADE,
            techniques        TEXT NOT NULL,
            primary_technique TEXT NOT NULL,
            detected_at       TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_qtech_primary "
                 "ON question_techniques(primary_technique)")

    rows = conn.execute(
        "SELECT id, text, razdatka_text, razdatka_pic FROM questions"
    ).fetchall()
    print(f"вопросов: {len(rows)}")

    batch = []
    for r in rows:
        techs = detect(r["text"] or "", bool(r["razdatka_text"] or r["razdatka_pic"]))
        batch.append((r["id"], ",".join(techs), techs[0]))
    conn.execute("DELETE FROM question_techniques")
    conn.executemany(
        "INSERT INTO question_techniques (question_id, techniques, primary_technique) "
        "VALUES (?, ?, ?)", batch)
    conn.commit()

    dist = Counter(b[2] for b in batch)
    print("\n=== главный приём (по корпусу) ===")
    for t, n in dist.most_common():
        print(f"  {t:<10} {n:>7}  {n/len(batch)*100:5.1f}%")

    # приём × категория (@p2)
    print("\n=== доля приёма «замена» внутри категорий (@p2) ===")
    rows = conn.execute("""
        WITH main_cat AS (
            SELECT qt.question_id, c.name_ru AS cat,
                   ROW_NUMBER() OVER (PARTITION BY qt.question_id
                                      ORDER BY qt.confidence DESC) AS rn
            FROM question_topics qt
            JOIN subcategories s ON s.id = qt.subcategory_id
            JOIN categories c ON c.id = s.category_id
            WHERE qt.model_name = 'google/gemma-4-26b-a4b-it@p2'
        )
        SELECT m.cat, tech.primary_technique AS t, COUNT(*) AS n
        FROM main_cat m
        JOIN question_techniques tech ON tech.question_id = m.question_id
        WHERE m.rn = 1
        GROUP BY m.cat, t
    """).fetchall()
    by_cat: dict = defaultdict(Counter)
    for r in rows:
        by_cat[r["cat"]][r["t"]] = r["n"]
    for cat in sorted(by_cat, key=lambda c: -sum(by_cat[c].values())):
        total = sum(by_cat[cat].values())
        z = by_cat[cat].get("замена", 0) / total * 100
        ch = by_cat[cat].get("чистый", 0) / total * 100
        print(f"  {cat:<26} замена {z:4.1f}%  чистый {ch:4.1f}%   (n={total})")

    # приём × беручесть
    print("\n=== средняя беручесть по приёмам ===")
    rows = conn.execute("""
        SELECT tech.primary_technique AS t,
               AVG(s.take_rate) AS tr, COUNT(*) AS n
        FROM question_techniques tech
        JOIN question_result_stats s ON s.question_id = tech.question_id
        GROUP BY t ORDER BY tr DESC
    """).fetchall()
    for r in rows:
        print(f"  {r['t']:<10} take_rate {r['tr']*100:4.1f}%  (n={r['n']})")


if __name__ == "__main__":
    main()
