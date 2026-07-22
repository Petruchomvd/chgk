"""Запросы каталога вопросов.

Ключевое отличие от `dashboard.db_queries.search_questions`: там
`LEFT JOIN question_topics` без дедупликации, из-за чего вопрос,
классифицированный N моделями, возвращается N раз. Здесь используется
тот же приём «primary topic», что и в `training_queries._fetch_full_questions`:
ROW_NUMBER() ... PARTITION BY question_id — одна строка на вопрос.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

# Одна строка на вопрос: тема с наибольшей уверенностью.
_PRIMARY_TOPIC_CTE = """
WITH primary_topic AS (
    SELECT question_id, subcategory_id, confidence,
           ROW_NUMBER() OVER (
               PARTITION BY question_id
               ORDER BY confidence DESC, subcategory_id
           ) AS rn
    FROM question_topics
    {topic_where}
)
"""


def _topic_where(model_name: Optional[str]) -> tuple[str, list]:
    if model_name:
        return "WHERE model_name = ?", [model_name]
    return "", []


def parse_authors(raw: Optional[str]) -> List[str]:
    """Авторы хранятся то JSON-списком словарей, то простой строкой."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [raw.strip()] if raw.strip() else []
    if isinstance(parsed, list):
        names = []
        for item in parsed:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]).strip())
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
        return names
    if isinstance(parsed, str) and parsed.strip():
        return [parsed.strip()]
    return []


def _build_filters(
    search: str,
    category_id: Optional[int],
    subcategory_id: Optional[int],
    year_from: Optional[int],
    year_to: Optional[int],
    difficulty_min: Optional[float],
    difficulty_max: Optional[float],
    author: Optional[str],
    status: Optional[str],
) -> tuple[list[str], list]:
    where: list[str] = []
    params: list = []

    if search:
        where.append("(q.text LIKE ? OR q.answer LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if category_id:
        where.append("c.id = ?")
        params.append(category_id)
    if subcategory_id:
        where.append("s.id = ?")
        params.append(subcategory_id)
    if year_from:
        where.append("CAST(substr(p.published_date, 1, 4) AS INTEGER) >= ?")
        params.append(year_from)
    if year_to:
        where.append("CAST(substr(p.published_date, 1, 4) AS INTEGER) <= ?")
        params.append(year_to)
    # Сложность вопроса, а не пакета. p.difficulty — это пакетный trueDL с
    # поправкой на силу команд; q.difficulty — наблюдаемая доля взятий,
    # 10 × (1 − доля). Именно она отвечает на вопрос «насколько этот вопрос
    # берут», и именно в ней измерены слабые места команды.
    if difficulty_min is not None:
        where.append("q.difficulty >= ?")
        params.append(difficulty_min)
    if difficulty_max is not None:
        where.append("q.difficulty <= ?")
        params.append(difficulty_max)
    if author:
        where.append("q.authors LIKE ?")
        params.append(f"%{author}%")

    # Статус изучения — из присоединённой training.db.
    if status == "new":
        where.append("att.question_id IS NULL")
    elif status == "seen":
        where.append("att.question_id IS NOT NULL")
    elif status == "failed":
        where.append("att.knew_any = 0")
    elif status == "known":
        where.append("att.knew_any = 1")
    elif status == "due":
        where.append("lt.question_id IS NOT NULL")

    return where, params


_ATTEMPT_AGG = """
LEFT JOIN (
    SELECT question_id,
           COUNT(*) AS attempts_count,
           MAX(knew) AS knew_any,
           MAX(attempted_at) AS last_attempt_at
    FROM t.attempts
    WHERE user_id = ?
    GROUP BY question_id
) att ON att.question_id = q.id
"""

_DUE_JOIN = """
LEFT JOIN (
    SELECT question_id FROM t.leitner
    WHERE user_id = ? AND next_review_at <= datetime('now', 'localtime')
) lt ON lt.question_id = q.id
"""


# Порядок выдачи. По умолчанию — свежие турниры.
# Сортировка по q.id выносит наверх поздний импорт без дат
# (пак «db.chgk.info — yukalyakin», ~938 вопросов), что бесполезно как витрина.
_SORTS = {
    "recent": "p.published_date DESC, q.id DESC",
    "oldest": "p.published_date ASC, q.id ASC",
    # По сложности ВОПРОСА: у trueDL пакета одно значение на весь турнир,
    # и сортировка по нему просто группировала вопросы по пакетам.
    "difficulty_desc": "q.difficulty DESC NULLS LAST, q.id DESC",
    "difficulty_asc": "q.difficulty ASC NULLS LAST, q.id DESC",
}
DEFAULT_SORT = "recent"


def _order_by(sort: Optional[str]) -> str:
    return _SORTS.get(sort or DEFAULT_SORT, _SORTS[DEFAULT_SORT])


def search_catalog(
    conn: sqlite3.Connection,
    user_id: int,
    search: str = "",
    category_id: Optional[int] = None,
    subcategory_id: Optional[int] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    difficulty_min: Optional[float] = None,
    difficulty_max: Optional[float] = None,
    author: Optional[str] = None,
    status: Optional[str] = None,
    model_name: Optional[str] = None,
    sort: Optional[str] = None,
    limit: int = 40,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    topic_where_sql, topic_params = _topic_where(model_name)
    cte = _PRIMARY_TOPIC_CTE.format(topic_where=topic_where_sql)
    where, params = _build_filters(
        search, category_id, subcategory_id, year_from, year_to,
        difficulty_min, difficulty_max, author, status,
    )
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        {cte}
        SELECT q.id, q.text, q.answer, q.authors,
               q.tour_number, q.number,
               c.id AS category_id, c.name_ru AS category,
               s.name_ru AS subcategory,
               pt.confidence,
               p.id AS pack_id, p.title AS pack_title,
               p.difficulty AS pack_difficulty,
               q.difficulty AS question_difficulty,
               substr(p.published_date, 1, 4) AS year,
               COALESCE(att.attempts_count, 0) AS attempts_count,
               att.knew_any, att.last_attempt_at,
               CASE WHEN lt.question_id IS NOT NULL THEN 1 ELSE 0 END AS is_due
        FROM questions q
        LEFT JOIN packs p ON q.pack_id = p.id
        LEFT JOIN primary_topic pt ON pt.question_id = q.id AND pt.rn = 1
        LEFT JOIN subcategories s ON pt.subcategory_id = s.id
        LEFT JOIN categories c ON s.category_id = c.id
        {_ATTEMPT_AGG}
        {_DUE_JOIN}
        {where_sql}
        ORDER BY {_order_by(sort)}
        LIMIT ? OFFSET ?
    """
    args = topic_params + [user_id, user_id] + params + [limit, offset]
    rows = conn.execute(sql, args).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        d["authors"] = parse_authors(d.pop("authors", None))
        d["text_preview"] = (d["text"] or "")[:260]
        out.append(d)
    return out


def count_catalog(
    conn: sqlite3.Connection,
    user_id: int,
    search: str = "",
    category_id: Optional[int] = None,
    subcategory_id: Optional[int] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    difficulty_min: Optional[float] = None,
    difficulty_max: Optional[float] = None,
    author: Optional[str] = None,
    status: Optional[str] = None,
    model_name: Optional[str] = None,
) -> int:
    topic_where_sql, topic_params = _topic_where(model_name)
    cte = _PRIMARY_TOPIC_CTE.format(topic_where=topic_where_sql)
    where, params = _build_filters(
        search, category_id, subcategory_id, year_from, year_to,
        difficulty_min, difficulty_max, author, status,
    )
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        {cte}
        SELECT COUNT(*) AS c
        FROM questions q
        LEFT JOIN packs p ON q.pack_id = p.id
        LEFT JOIN primary_topic pt ON pt.question_id = q.id AND pt.rn = 1
        LEFT JOIN subcategories s ON pt.subcategory_id = s.id
        LEFT JOIN categories c ON s.category_id = c.id
        {_ATTEMPT_AGG}
        {_DUE_JOIN}
        {where_sql}
    """
    args = topic_params + [user_id, user_id] + params
    return conn.execute(sql, args).fetchone()["c"]


def get_question(
    conn: sqlite3.Connection, user_id: int, question_id: int
) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        WITH primary_topic AS (
            SELECT question_id, subcategory_id, confidence,
                   ROW_NUMBER() OVER (
                       PARTITION BY question_id ORDER BY confidence DESC
                   ) AS rn
            FROM question_topics
        )
        SELECT q.id, q.text, q.answer, q.zachet, q.nezachet, q.comment,
               q.source, q.authors, q.razdatka_text, q.razdatka_pic,
               q.number, q.tour_number, q.difficulty AS question_difficulty,
               c.name_ru AS category, s.name_ru AS subcategory, pt.confidence,
               p.id AS pack_id, p.title AS pack_title, p.link AS pack_link,
               p.difficulty AS pack_difficulty,
               substr(p.published_date, 1, 4) AS year
        FROM questions q
        LEFT JOIN packs p ON q.pack_id = p.id
        LEFT JOIN primary_topic pt ON pt.question_id = q.id AND pt.rn = 1
        LEFT JOIN subcategories s ON pt.subcategory_id = s.id
        LEFT JOIN categories c ON s.category_id = c.id
        WHERE q.id = ?
    """, (question_id,)).fetchone()
    if row is None:
        return None

    d = dict(row)
    d["authors"] = parse_authors(d.pop("authors", None))

    # Все темы вопроса (по всем моделям) — показываем как теги.
    d["topics"] = [dict(r) for r in conn.execute("""
        SELECT c.name_ru AS category, s.name_ru AS subcategory,
               qt.confidence, qt.model_name
        FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE qt.question_id = ?
        ORDER BY qt.confidence DESC
    """, (question_id,)).fetchall()]

    d["attempts"] = [dict(r) for r in conn.execute("""
        SELECT attempted_at, user_answer, knew, time_seconds, mode
        FROM t.attempts
        WHERE user_id = ? AND question_id = ?
        ORDER BY attempted_at DESC
        LIMIT 30
    """, (user_id, question_id)).fetchall()]

    leitner = conn.execute("""
        SELECT box, next_review_at, consecutive_correct
        FROM t.leitner WHERE user_id = ? AND question_id = ?
    """, (user_id, question_id)).fetchone()
    d["leitner"] = dict(leitner) if leitner else None

    return d


def topics_tree(
    conn: sqlite3.Connection, user_id: int, model_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Категории → подкатегории с покрытием и результатами пользователя.

    Проценты успеха считаются по `attempts.category` (так их пишет движок),
    поэтому они доступны только на уровне категории.
    """
    topic_where_sql, topic_params = _topic_where(model_name)
    cte = _PRIMARY_TOPIC_CTE.format(topic_where=topic_where_sql)

    rows = conn.execute(f"""
        {cte}
        SELECT c.id AS category_id, c.name_ru AS category,
               s.id AS subcategory_id, s.name_ru AS subcategory,
               COUNT(pt.question_id) AS questions_count
        FROM categories c
        JOIN subcategories s ON s.category_id = c.id
        LEFT JOIN primary_topic pt
               ON pt.subcategory_id = s.id AND pt.rn = 1
        GROUP BY c.id, s.id
        ORDER BY c.sort_order, c.name_ru, s.name_ru
    """, topic_params).fetchall()

    stats = {
        r["category"]: dict(r)
        for r in conn.execute("""
            SELECT category, COUNT(*) AS attempts_count,
                   SUM(knew) AS knew_count,
                   COUNT(DISTINCT question_id) AS distinct_questions
            FROM t.attempts
            WHERE user_id = ? AND category IS NOT NULL
            GROUP BY category
        """, (user_id,)).fetchall()
    }

    by_cat: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        cat = by_cat.setdefault(r["category_id"], {
            "category_id": r["category_id"],
            "category": r["category"],
            "questions_count": 0,
            "subcategories": [],
        })
        cat["questions_count"] += r["questions_count"] or 0
        if r["subcategory_id"] is not None:
            cat["subcategories"].append({
                "subcategory_id": r["subcategory_id"],
                "subcategory": r["subcategory"],
                "questions_count": r["questions_count"] or 0,
            })

    out = []
    for cat in by_cat.values():
        st = stats.get(cat["category"])
        if st and st["attempts_count"]:
            cat["attempts_count"] = st["attempts_count"]
            cat["distinct_questions"] = st["distinct_questions"]
            cat["success_pct"] = round(
                100 * (st["knew_count"] or 0) / st["attempts_count"]
            )
        else:
            cat["attempts_count"] = 0
            cat["distinct_questions"] = 0
            cat["success_pct"] = None
        cat["subcategories"].sort(key=lambda s: -s["questions_count"])
        out.append(cat)

    out.sort(key=lambda c: -c["questions_count"])
    return out


def recent_attempts(
    conn: sqlite3.Connection, user_id: int, limit: int = 8
) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute("""
        SELECT a.question_id, a.attempted_at, a.knew, a.category, a.mode,
               substr(q.text, 1, 160) AS text_preview, q.answer
        FROM t.attempts a
        JOIN questions q ON q.id = a.question_id
        WHERE a.user_id = ?
        ORDER BY a.attempted_at DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()]


def weak_categories(
    conn: sqlite3.Connection, user_id: int, min_attempts: int = 3, limit: int = 6
) -> List[Dict[str, Any]]:
    """Категории с худшим процентом — только там, где хватает попыток."""
    return [dict(r) for r in conn.execute("""
        SELECT category, COUNT(*) AS attempts_count, SUM(knew) AS knew_count,
               ROUND(100.0 * SUM(knew) / COUNT(*)) AS success_pct
        FROM t.attempts
        WHERE user_id = ? AND category IS NOT NULL AND category != ''
        GROUP BY category
        HAVING COUNT(*) >= ?
        ORDER BY success_pct ASC, attempts_count DESC
        LIMIT ?
    """, (user_id, min_attempts, limit)).fetchall()]


def activity_by_day(
    conn: sqlite3.Connection, user_id: int, days: int = 30
) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute("""
        SELECT substr(attempted_at, 1, 10) AS day,
               COUNT(*) AS total, SUM(knew) AS knew
        FROM t.attempts
        WHERE user_id = ? AND attempted_at >= date('now', ?)
        GROUP BY day
        ORDER BY day
    """, (user_id, f"-{days} days")).fetchall()]
