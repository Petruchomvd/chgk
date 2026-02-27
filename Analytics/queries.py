"""SQL-запросы для аналитики ЧГК-вопросов."""

import sqlite3
from typing import Dict, List, Tuple


def top_categories(conn: sqlite3.Connection) -> List[Dict]:
    """ТОП категорий по количеству вопросов."""
    return [dict(r) for r in conn.execute("""
        SELECT c.name_ru AS category, COUNT(DISTINCT qt.question_id) AS count,
               ROUND(100.0 * COUNT(DISTINCT qt.question_id) /
                     (SELECT COUNT(DISTINCT question_id) FROM question_topics), 1) AS pct
        FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        GROUP BY c.id
        ORDER BY count DESC
    """).fetchall()]


def top_subcategories(conn: sqlite3.Connection, limit: int = 20) -> List[Dict]:
    """ТОП подкатегорий по количеству вопросов."""
    return [dict(r) for r in conn.execute(f"""
        SELECT c.name_ru AS category, s.name_ru AS subcategory,
               COUNT(DISTINCT qt.question_id) AS count,
               ROUND(100.0 * COUNT(DISTINCT qt.question_id) /
                     (SELECT COUNT(DISTINCT question_id) FROM question_topics), 1) AS pct
        FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        GROUP BY s.id
        ORDER BY count DESC
        LIMIT {limit}
    """).fetchall()]


def trends_by_month(conn: sqlite3.Connection) -> List[Dict]:
    """Тренды тем по месяцам (по дате публикации пакета)."""
    return [dict(r) for r in conn.execute("""
        SELECT substr(p.published_date, 1, 7) AS month,
               c.name_ru AS category,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN packs p ON q.pack_id = p.id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE p.published_date IS NOT NULL
        GROUP BY month, c.id
        ORDER BY month, count DESC
    """).fetchall()]


def difficulty_by_category(conn: sqlite3.Connection) -> List[Dict]:
    """Средняя сложность вопросов по категориям (из questions.difficulty)."""
    return [dict(r) for r in conn.execute("""
        SELECT c.name_ru AS category,
               ROUND(AVG(q.difficulty), 2) AS avg_difficulty,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE q.difficulty IS NOT NULL
        GROUP BY c.id
        HAVING count > 10
        ORDER BY avg_difficulty DESC
    """).fetchall()]


def category_stats(conn: sqlite3.Connection) -> Dict:
    """Общая статистика классификации."""
    total_qs = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    classified = conn.execute(
        "SELECT COUNT(DISTINCT question_id) FROM question_topics"
    ).fetchone()[0]
    total_packs = conn.execute(
        "SELECT COUNT(*) FROM packs WHERE parse_status = 'parsed'"
    ).fetchone()[0]

    return {
        "total_questions": total_qs,
        "classified_questions": classified,
        "classification_pct": round(100 * classified / total_qs, 1) if total_qs > 0 else 0,
        "total_packs": total_packs,
    }
