"""SQL-запросы для Streamlit-дашборда (с фильтрацией по модели)."""

import sqlite3
from typing import Any, Dict, List, Optional


def get_available_models(conn: sqlite3.Connection) -> List[str]:
    """Список моделей, классифицировавших хотя бы 1 вопрос."""
    rows = conn.execute(
        "SELECT DISTINCT model_name FROM question_topics "
        "WHERE model_name IS NOT NULL ORDER BY model_name"
    ).fetchall()
    return [r[0] for r in rows]


def get_overview_stats(conn: sqlite3.Connection, model_name: Optional[str] = None) -> Dict[str, Any]:
    """KPI-метрики: всего вопросов, пакетов, классифицировано, %."""
    total_qs = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    total_packs = conn.execute(
        "SELECT COUNT(*) FROM packs WHERE parse_status = 'parsed'"
    ).fetchone()[0]

    if model_name:
        classified = conn.execute(
            "SELECT COUNT(DISTINCT question_id) FROM question_topics WHERE model_name = ?",
            (model_name,),
        ).fetchone()[0]
    else:
        classified = conn.execute(
            "SELECT COUNT(DISTINCT question_id) FROM question_topics"
        ).fetchone()[0]

    return {
        "total_questions": total_qs,
        "total_packs": total_packs,
        "classified": classified,
        "classification_pct": round(100 * classified / total_qs, 1) if total_qs > 0 else 0,
    }


def _model_filter(alias: str, model_name: Optional[str]) -> tuple:
    """Вернуть (SQL-фрагмент WHERE, параметры)."""
    if model_name:
        return f"AND {alias}.model_name = ?", (model_name,)
    return "", ()


def top_categories(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """14 категорий с количеством и процентом."""
    where, params = _model_filter("qt", model_name)
    rows = conn.execute(f"""
        SELECT c.id AS category_id, c.name_ru AS category, c.sort_order,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE 1=1 {where}
        GROUP BY c.id
        ORDER BY c.sort_order
    """, params).fetchall()

    total = sum(r["count"] for r in rows) if rows else 1
    return [
        {**dict(r), "pct": round(100 * r["count"] / total, 1)}
        for r in rows
    ]


def top_subcategories(
    conn: sqlite3.Connection,
    model_name: Optional[str] = None,
    category_id: Optional[int] = None,
    limit: int = 20,
) -> List[Dict]:
    """Подкатегории с count и pct. Можно фильтровать по категории."""
    where, params = _model_filter("qt", model_name)
    if category_id:
        where += " AND c.id = ?"
        params += (category_id,)

    rows = conn.execute(f"""
        SELECT c.name_ru AS category, s.name_ru AS subcategory, s.id AS subcategory_id,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE 1=1 {where}
        GROUP BY s.id
        ORDER BY count DESC
        LIMIT ?
    """, params + (limit,)).fetchall()

    total = sum(r["count"] for r in rows) if rows else 1
    return [
        {**dict(r), "pct": round(100 * r["count"] / total, 1)}
        for r in rows
    ]


def get_all_categories(conn: sqlite3.Connection) -> List[Dict]:
    """Все 14 категорий (id, name_ru) для фильтров."""
    return [dict(r) for r in conn.execute(
        "SELECT id, name_ru FROM categories ORDER BY sort_order"
    ).fetchall()]


def trends_by_month(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """Помесячные тренды по категориям."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        SELECT substr(p.published_date, 1, 7) AS month,
               c.name_ru AS category,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN packs p ON q.pack_id = p.id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE p.published_date IS NOT NULL {where}
        GROUP BY month, c.id
        ORDER BY month, count DESC
    """, params).fetchall()]


def difficulty_by_category(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """Средняя сложность вопросов по категориям (из questions.difficulty)."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        SELECT c.name_ru AS category,
               ROUND(AVG(q.difficulty), 2) AS avg_difficulty,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE q.difficulty IS NOT NULL {where}
        GROUP BY c.id
        HAVING count > 5
        ORDER BY avg_difficulty DESC
    """, params).fetchall()]


# ─── Уверенность (Confidence) ────────────────────────────────────

def confidence_distribution(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """Все значения confidence для гистограммы."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        SELECT qt.confidence, c.name_ru AS category, qt.model_name
        FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE qt.confidence IS NOT NULL {where}
    """, params).fetchall()]


def confidence_by_category(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """Средняя, мин, макс уверенность по категориям."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        SELECT c.name_ru AS category, c.sort_order,
               ROUND(AVG(qt.confidence), 3) AS avg_conf,
               ROUND(MIN(qt.confidence), 3) AS min_conf,
               ROUND(MAX(qt.confidence), 3) AS max_conf,
               COUNT(*) AS count
        FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE qt.confidence IS NOT NULL {where}
        GROUP BY c.id
        ORDER BY c.sort_order
    """, params).fetchall()]


# ─── Сравнение моделей ────────────────────────────────────────────

def get_common_questions(
    conn: sqlite3.Connection, model_a: str, model_b: str
) -> List[Dict]:
    """Вопросы, классифицированные обеими моделями (primary topic = max confidence)."""
    return [dict(r) for r in conn.execute("""
        WITH ranked AS (
            SELECT qt.question_id, qt.model_name,
                   c.id AS cat_id, c.name_ru AS category,
                   s.name_ru AS subcategory,
                   qt.confidence,
                   ROW_NUMBER() OVER (
                       PARTITION BY qt.question_id, qt.model_name
                       ORDER BY qt.confidence DESC
                   ) AS rn
            FROM question_topics qt
            JOIN subcategories s ON qt.subcategory_id = s.id
            JOIN categories c ON s.category_id = c.id
            WHERE qt.model_name IN (?, ?)
        )
        SELECT
            a.question_id,
            a.cat_id AS cat_id_a, a.category AS cat_a, a.subcategory AS sub_a, a.confidence AS conf_a,
            b.cat_id AS cat_id_b, b.category AS cat_b, b.subcategory AS sub_b, b.confidence AS conf_b
        FROM ranked a
        JOIN ranked b ON a.question_id = b.question_id
        WHERE a.model_name = ? AND a.rn = 1
          AND b.model_name = ? AND b.rn = 1
    """, (model_a, model_b, model_a, model_b)).fetchall()]


def agreement_matrix(conn: sqlite3.Connection, model_a: str, model_b: str) -> List[Dict]:
    """Матрица согласия 14x14 (агрегация get_common_questions)."""
    rows = get_common_questions(conn, model_a, model_b)
    from collections import Counter
    counts = Counter((r["cat_a"], r["cat_b"]) for r in rows)
    return [
        {"cat_a": ca, "cat_b": cb, "count": cnt}
        for (ca, cb), cnt in counts.items()
    ]


# ─── Браузер вопросов ─────────────────────────────────────────────

def search_questions(
    conn: sqlite3.Connection,
    model_name: Optional[str] = None,
    search_text: str = "",
    category_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict]:
    """Поиск вопросов с их классификациями."""
    where_parts = []
    params = []

    if search_text:
        where_parts.append("q.text LIKE ?")
        params.append(f"%{search_text}%")
    if model_name:
        where_parts.append("qt.model_name = ?")
        params.append(model_name)
    if category_id:
        where_parts.append("c.id = ?")
        params.append(category_id)

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    return [dict(r) for r in conn.execute(f"""
        SELECT q.id, q.text, q.answer, q.comment,
               p.title AS pack_title, p.difficulty AS pack_difficulty,
               q.difficulty AS question_difficulty,
               c.name_ru AS category, s.name_ru AS subcategory,
               qt.confidence, qt.model_name
        FROM questions q
        LEFT JOIN question_topics qt ON q.id = qt.question_id
        LEFT JOIN subcategories s ON qt.subcategory_id = s.id
        LEFT JOIN categories c ON s.category_id = c.id
        LEFT JOIN packs p ON q.pack_id = p.id
        {where_sql}
        ORDER BY q.id
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()]


def count_search_results(
    conn: sqlite3.Connection,
    model_name: Optional[str] = None,
    search_text: str = "",
    category_id: Optional[int] = None,
) -> int:
    """Количество результатов для пагинации."""
    where_parts = []
    params = []

    if search_text:
        where_parts.append("q.text LIKE ?")
        params.append(f"%{search_text}%")
    if model_name:
        where_parts.append("qt.model_name = ?")
        params.append(model_name)
    if category_id:
        where_parts.append("c.id = ?")
        params.append(category_id)

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    return conn.execute(f"""
        SELECT COUNT(DISTINCT q.id)
        FROM questions q
        LEFT JOIN question_topics qt ON q.id = qt.question_id
        LEFT JOIN subcategories s ON qt.subcategory_id = s.id
        LEFT JOIN categories c ON s.category_id = c.id
        {where_sql}
    """, params).fetchone()[0]


# ─── История запусков ─────────────────────────────────────────────

def get_classification_runs(conn: sqlite3.Connection) -> List[Dict]:
    """Все запуски классификации."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM classification_runs ORDER BY started_at DESC"
    ).fetchall()]


# ─── Тренды (рост/падение) ───────────────────────────────────────

def trends_by_year(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """Годовые тренды по категориям (для анализа роста/падения)."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        SELECT substr(p.published_date, 1, 4) AS year,
               c.name_ru AS category, c.sort_order,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN packs p ON q.pack_id = p.id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE p.published_date IS NOT NULL {where}
        GROUP BY year, c.id
        ORDER BY year, c.sort_order
    """, params).fetchall()]


def category_growth(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """Рост/падение категорий: сравнение последнего года с предыдущим."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        WITH yearly AS (
            SELECT substr(p.published_date, 1, 4) AS year,
                   c.name_ru AS category, c.sort_order,
                   COUNT(DISTINCT qt.question_id) AS count
            FROM question_topics qt
            JOIN questions q ON qt.question_id = q.id
            JOIN packs p ON q.pack_id = p.id
            JOIN subcategories s ON qt.subcategory_id = s.id
            JOIN categories c ON s.category_id = c.id
            WHERE p.published_date IS NOT NULL {where}
            GROUP BY year, c.id
        ),
        totals AS (
            SELECT year, SUM(count) AS total FROM yearly GROUP BY year
        ),
        pcts AS (
            SELECT y.year, y.category, y.sort_order,
                   ROUND(100.0 * y.count / t.total, 1) AS pct
            FROM yearly y JOIN totals t ON y.year = t.year
        ),
        last_two AS (
            SELECT year FROM (SELECT DISTINCT year FROM pcts ORDER BY year DESC LIMIT 2)
        )
        SELECT cur.category, cur.sort_order,
               cur.pct AS current_pct,
               COALESCE(prev.pct, 0) AS prev_pct,
               ROUND(cur.pct - COALESCE(prev.pct, 0), 1) AS delta
        FROM pcts cur
        LEFT JOIN pcts prev ON cur.category = prev.category
            AND prev.year = (SELECT MIN(year) FROM last_two)
        WHERE cur.year = (SELECT MAX(year) FROM last_two)
        ORDER BY delta DESC
    """, params).fetchall()]


def subcategory_trends_by_year(
    conn: sqlite3.Connection, model_name: Optional[str] = None
) -> List[Dict]:
    """Годовые тренды по подкатегориям."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        SELECT substr(p.published_date, 1, 4) AS year,
               c.name_ru AS category, s.name_ru AS subcategory,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN packs p ON q.pack_id = p.id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE p.published_date IS NOT NULL {where}
        GROUP BY year, s.id
        ORDER BY year, count DESC
    """, params).fetchall()]


# ─── Авторы ──────────────────────────────────────────────────────

def top_authors(conn: sqlite3.Connection, limit: int = 20) -> List[Dict]:
    """Топ-авторы по количеству пакетов."""
    return [dict(r) for r in conn.execute("""
        SELECT p.authors, COUNT(*) AS pack_count,
               SUM(p.question_count) AS question_count
        FROM packs p
        WHERE p.authors IS NOT NULL AND p.authors != ''
          AND p.parse_status = 'parsed'
        GROUP BY p.authors
        ORDER BY pack_count DESC
        LIMIT ?
    """, (limit,)).fetchall()]


def author_categories(
    conn: sqlite3.Connection,
    author: str,
    model_name: Optional[str] = None,
) -> List[Dict]:
    """Тематический профиль автора: распределение категорий."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        SELECT c.name_ru AS category, c.sort_order,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN packs p ON q.pack_id = p.id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE p.authors LIKE ? {where}
        GROUP BY c.id
        ORDER BY count DESC
    """, (f"%{author}%",) + params).fetchall()]


# ─── Парные категории и редкие темы ──────────────────────────────

def paired_categories(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """Какие категории чаще встречаются вместе (у вопросов с 2+ тегами)."""
    where, params = _model_filter("qt1", model_name)
    where2 = where.replace("qt1", "qt2") if where else ""
    return [dict(r) for r in conn.execute(f"""
        SELECT c1.name_ru AS category_a, c2.name_ru AS category_b,
               COUNT(DISTINCT qt1.question_id) AS count
        FROM question_topics qt1
        JOIN question_topics qt2 ON qt1.question_id = qt2.question_id
            AND qt1.subcategory_id < qt2.subcategory_id
        JOIN subcategories s1 ON qt1.subcategory_id = s1.id
        JOIN subcategories s2 ON qt2.subcategory_id = s2.id
        JOIN categories c1 ON s1.category_id = c1.id
        JOIN categories c2 ON s2.category_id = c2.id
        WHERE c1.id != c2.id {where} {where2}
        GROUP BY c1.id, c2.id
        HAVING count >= 3
        ORDER BY count DESC
        LIMIT 20
    """, params + params).fetchall()]


def difficulty_distribution(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """Распределение сложности вопросов (для гистограммы)."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        SELECT q.difficulty, c.name_ru AS category
        FROM questions q
        JOIN question_topics qt ON q.id = qt.question_id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE q.difficulty IS NOT NULL {where}
    """, params).fetchall()]


def rare_subcategories(conn: sqlite3.Connection, model_name: Optional[str] = None) -> List[Dict]:
    """Редкие подкатегории (< 1% от всех классификаций)."""
    where, params = _model_filter("qt", model_name)
    return [dict(r) for r in conn.execute(f"""
        WITH counts AS (
            SELECT s.id, c.name_ru AS category, s.name_ru AS subcategory,
                   COUNT(DISTINCT qt.question_id) AS count
            FROM question_topics qt
            JOIN subcategories s ON qt.subcategory_id = s.id
            JOIN categories c ON s.category_id = c.id
            WHERE 1=1 {where}
            GROUP BY s.id
        ),
        total AS (SELECT SUM(count) AS total FROM counts)
        SELECT c.category, c.subcategory, c.count,
               ROUND(100.0 * c.count / t.total, 2) AS pct
        FROM counts c, total t
        WHERE 100.0 * c.count / t.total < 1.0
        ORDER BY c.count ASC
    """, params).fetchall()]


# ── Джентльменский набор ─────────────────────────────────────────

def get_questions_by_ids(
    conn,
    question_ids: list,
    limit: int = 20,
):
    """Получить вопросы по списку ID (для drill-down)."""
    if not question_ids:
        return []
    ids = question_ids[:limit]
    placeholders = ",".join("?" * len(ids))
    return [dict(r) for r in conn.execute(f"""
        SELECT q.id, q.text, q.answer, q.comment,
               p.title AS pack_title
        FROM questions q
        LEFT JOIN packs p ON q.pack_id = p.id
        WHERE q.id IN ({placeholders})
        ORDER BY q.id
    """, ids).fetchall()]
