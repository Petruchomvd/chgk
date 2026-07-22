"""Контур «Учить»: корпус вопросов как учебник.

Тренировка проверяет память, но не даёт набирать эрудицию. А эрудиция — это
знание канона: в ЧГК десятки ответов повторяются из турнира в турнир (Ной,
Венеция, зеркало, Пушкин…), и каждый спрашивают с разных сторон. Знать канон
и его «зацепки» = брать эти вопросы.

Здесь корпус превращается в учебник без нового LLM: объяснение факта уже лежит
в комментариях к вопросам (89% вопросов их имеют). «Досье факта» = все вопросы
с этим ответом = все углы, с которых его спрашивают, плюс готовые разборы.

Приоритет — слабые темы команды (data/team/*-gaps.json): учить то, где реально
отстаём от поля.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Dict, List, Optional

# Метки, по которым берём тему вопроса (та же модель, что и в остальном коде).
LABEL_MODEL = "google/gemma-4-26b-a4b-it@p2"

# Мусорные «ответы», которые не факт, а тип ответа — не учат ничему.
_STOP_ANSWERS = {
    "да", "нет", "да.", "нет.", "0", "1", "ноль", "один", "нисколько",
    "не", "он", "она", "оно", "они", "это",
}


def normalize_answer(answer: str) -> str:
    """Ответ → каноничная форма для группировки: первая строка, без пунктуации."""
    first = (answer or "").split("\n")[0]
    return first.strip().strip(" .!?«»\"'").lower()


def canon(
    conn: sqlite3.Connection,
    category_id: Optional[int] = None,
    limit: int = 40,
    min_count: int = 4,
) -> List[Dict]:
    """Канон: повторяющиеся ответы (что нужно знать), с частотой и примером.

    Если задана категория — только вопросы этой темы (по меткам LABEL_MODEL).
    Частота считается по всему корпусу: если ответ встречается часто вообще,
    его стоит знать, даже если в конкретной теме он мелькнул пару раз.
    """
    if category_id is not None:
        rows = conn.execute(
            """
            SELECT DISTINCT q.id, q.answer
            FROM questions q
            JOIN question_topics qt ON qt.question_id = q.id AND qt.model_name = ?
            JOIN subcategories sc ON sc.id = qt.subcategory_id
            WHERE sc.category_id = ? AND LENGTH(q.answer) < 40
            """,
            (LABEL_MODEL, category_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, answer FROM questions WHERE LENGTH(answer) < 40"
        ).fetchall()

    freq: Dict[str, int] = {}
    example: Dict[str, int] = {}
    display: Dict[str, str] = {}
    for r in rows:
        key = normalize_answer(r["answer"])
        if len(key) < 2 or key in _STOP_ANSWERS or key.isdigit():
            continue
        freq[key] = freq.get(key, 0) + 1
        if key not in example:
            example[key] = r["id"]
            display[key] = (r["answer"] or "").split("\n")[0].strip().strip(" .")
    items = [
        {"answer": display[k], "key": k, "count": n, "example_id": example[k]}
        for k, n in freq.items()
        if n >= min_count
    ]
    items.sort(key=lambda x: -x["count"])
    return items[:limit]


def question_ids_for_answer(conn: sqlite3.Connection, answer_key: str, limit: int = 40) -> List[int]:
    """ID вопросов с этим ответом (для тренировки факта). Трудные — первыми."""
    key = normalize_answer(answer_key)
    conn.create_function("pylower", 1, lambda s: s.lower() if s else s, deterministic=True)
    needle = re.sub(r"[%_]", "", key[:20])
    rows = conn.execute(
        """
        SELECT q.id, q.answer, s.take_rate AS take_rate
        FROM questions q
        LEFT JOIN question_result_stats s ON s.question_id = q.id
        WHERE LENGTH(q.answer) < 40 AND pylower(q.answer) LIKE ?
        """,
        ("%" + needle + "%",),
    ).fetchall()
    hits = [r for r in rows if normalize_answer(r["answer"]) == key]
    hits.sort(key=lambda r: (r["take_rate"] is None, r["take_rate"] or 1.0))
    return [r["id"] for r in hits[:limit]]


def fact_dossier(conn: sqlite3.Connection, answer_key: str, limit: int = 12) -> Dict:
    """Досье факта: все вопросы с этим ответом — углы вопроса + разбор из комментов."""
    key = normalize_answer(answer_key)
    # SQL-предфильтр по префиксу режет 211k до десятков. SQLite LOWER/LIKE не
    # сворачивают регистр кириллицы, поэтому регистрируем Python-lower; точное
    # совпадение — в Python по нормализованной форме.
    conn.create_function("pylower", 1, lambda s: s.lower() if s else s, deterministic=True)
    # contains, а не prefix: ведущие кавычки/артикли в ответе не должны рушить
    # совпадение («Белое солнце пустыни»). Точность добирает Python-normalize ниже.
    needle = re.sub(r"[%_]", "", key[:20])
    rows = conn.execute(
        """
        SELECT q.id, q.text, q.answer, q.comment,
               p.title AS pack_title, substr(p.start_date, 1, 4) AS year,
               s.take_rate AS take_rate
        FROM questions q
        LEFT JOIN packs p ON p.id = q.pack_id
        LEFT JOIN question_result_stats s ON s.question_id = q.id
        WHERE LENGTH(q.answer) < 40 AND pylower(q.answer) LIKE ?
        """,
        ("%" + needle + "%",),
    ).fetchall()
    hits = [r for r in rows if normalize_answer(r["answer"]) == key]
    hits.sort(key=lambda r: (r["take_rate"] is None, r["take_rate"] or 1.0))

    angles = []
    for r in hits[:limit]:
        angles.append({
            "id": r["id"],
            "text": r["text"],
            "comment": r["comment"] or "",
            "pack_title": r["pack_title"],
            "year": r["year"],
            "take_rate": r["take_rate"],
        })
    return {
        "answer": (hits[0]["answer"].split("\n")[0].strip() if hits else answer_key),
        "total": len(hits),
        "angles": angles,
    }
