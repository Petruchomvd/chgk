"""SQL-запросы для тренировочного режима."""

import json
import random
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set


def get_subcategories_for_categories(
    conn: sqlite3.Connection, category_ids: List[int]
) -> List[Dict]:
    """Подкатегории для выбранных категорий."""
    if not category_ids:
        return []
    placeholders = ",".join("?" * len(category_ids))
    return [dict(r) for r in conn.execute(f"""
        SELECT s.id, s.name_ru, c.name_ru AS category_name
        FROM subcategories s
        JOIN categories c ON s.category_id = c.id
        WHERE c.id IN ({placeholders})
        ORDER BY c.sort_order, s.sort_order
    """, category_ids).fetchall()]


def _multi_author_clause(author_filters: List[str]) -> Tuple[str, list]:
    """OR-clause для нескольких авторов (по q.authors JSON)."""
    clauses = ["q.authors LIKE ?" for _ in author_filters]
    params = [f"%{a}%" for a in author_filters]
    return f"({' OR '.join(clauses)})", params


def count_available_by_category(
    conn: sqlite3.Connection,
    category_ids: Optional[List[int]] = None,
    subcategory_ids: Optional[List[int]] = None,
    model_name: Optional[str] = None,
    difficulty_range: Optional[Tuple[float, float]] = None,
    author_filter: Optional[str] = None,
    author_filters: Optional[List[str]] = None,
    technique: Optional[str] = None,
) -> int:
    """Количество доступных вопросов по категориям."""
    where_parts = ["1=1"]
    params: list = []

    if subcategory_ids:
        placeholders = ",".join("?" * len(subcategory_ids))
        where_parts.append(f"qt.subcategory_id IN ({placeholders})")
        params.extend(subcategory_ids)
    elif category_ids:
        placeholders = ",".join("?" * len(category_ids))
        where_parts.append(f"c.id IN ({placeholders})")
        params.extend(category_ids)

    if model_name:
        where_parts.append("qt.model_name = ?")
        params.append(model_name)

    if difficulty_range:
        where_parts.append("q.difficulty BETWEEN ? AND ?")
        params.extend(difficulty_range)

    if author_filter:
        where_parts.append("q.authors LIKE ?")
        params.append(f"%{author_filter}%")
    if author_filters:
        sql_frag, ap = _multi_author_clause(author_filters)
        where_parts.append(sql_frag)
        params.extend(ap)
    if technique:
        where_parts.append(
            "EXISTS (SELECT 1 FROM question_techniques te "
            "WHERE te.question_id = q.id AND te.primary_technique = ?)")
        params.append(technique)

    where_sql = " AND ".join(where_parts)
    return conn.execute(f"""
        SELECT COUNT(DISTINCT qt.question_id)
        FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        JOIN questions q ON qt.question_id = q.id
        JOIN packs p ON q.pack_id = p.id
        WHERE {where_sql}
    """, params).fetchone()[0]


def count_available_random(
    conn: sqlite3.Connection,
    difficulty_range: Optional[Tuple[float, float]] = None,
    author_filter: Optional[str] = None,
    author_filters: Optional[List[str]] = None,
    technique: Optional[str] = None,
) -> int:
    """Количество доступных вопросов (все)."""
    where_parts = ["1=1"]
    params: list = []
    need_pack_join = False

    if difficulty_range:
        where_parts.append("q.difficulty BETWEEN ? AND ?")
        params.extend(difficulty_range)
        need_pack_join = True

    if author_filter:
        where_parts.append("q.authors LIKE ?")
        params.append(f"%{author_filter}%")

    if author_filters:
        sql_frag, ap = _multi_author_clause(author_filters)
        where_parts.append(sql_frag)
        params.extend(ap)

    if technique:
        where_parts.append(
            "EXISTS (SELECT 1 FROM question_techniques te "
            "WHERE te.question_id = q.id AND te.primary_technique = ?)")
        params.append(technique)

    has_filters = len(where_parts) > 1
    if not has_filters:
        return conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]

    where_sql = " AND ".join(where_parts)
    pack_join = "JOIN packs p ON q.pack_id = p.id" if need_pack_join else ""
    return conn.execute(f"""
        SELECT COUNT(*)
        FROM questions q
        {pack_join}
        WHERE {where_sql}
    """, params).fetchone()[0]


def count_available_gentleman(
    data_dir: Path,
    gentleman_category: Optional[str] = None,
) -> int:
    """Количество question_ids в gentleman set."""
    top_path = data_dir / "top_answers.json"
    if not top_path.exists():
        return 0

    top_data = json.loads(top_path.read_text(encoding="utf-8"))
    answer_questions = top_data.get("answer_questions", {})

    if gentleman_category:
        cat_path = data_dir / "categorized_answers.json"
        if not cat_path.exists():
            return 0
        cat_data = json.loads(cat_path.read_text(encoding="utf-8"))
        answer_cat = cat_data.get("answer_category", {})
        matching = {a for a, c in answer_cat.items() if c == gentleman_category}
        qids = set()
        for ans, ids in answer_questions.items():
            if ans in matching:
                qids.update(ids)
        return len(qids)

    return len({qid for ids in answer_questions.values() for qid in ids})


def get_training_questions_by_category(
    conn: sqlite3.Connection,
    category_ids: Optional[List[int]] = None,
    subcategory_ids: Optional[List[int]] = None,
    model_name: Optional[str] = None,
    difficulty_range: Optional[Tuple[float, float]] = None,
    limit: int = 10,
    seed: Optional[int] = None,
    author_filter: Optional[str] = None,
    author_filters: Optional[List[str]] = None,
    exclude_ids: Optional[Set[int]] = None,
    technique: Optional[str] = None,
) -> List[Dict]:
    """Случайные вопросы по категориям.

    exclude_ids — вопросы, которые уже показывали. Нужен для тренировки темы:
    вопрос ЧГК одноразовый, и повторный показ проверяет память об этом вопросе,
    а не знание темы. Чтобы подтянуть тему, нужны новые вопросы по ней.
    """
    where_parts = ["1=1"]
    params: list = []

    if subcategory_ids:
        placeholders = ",".join("?" * len(subcategory_ids))
        where_parts.append(f"qt.subcategory_id IN ({placeholders})")
        params.extend(subcategory_ids)
    elif category_ids:
        placeholders = ",".join("?" * len(category_ids))
        where_parts.append(f"c.id IN ({placeholders})")
        params.extend(category_ids)

    if model_name:
        where_parts.append("qt.model_name = ?")
        params.append(model_name)

    if difficulty_range:
        where_parts.append("q.difficulty BETWEEN ? AND ?")
        params.extend(difficulty_range)

    if author_filter:
        where_parts.append("q.authors LIKE ?")
        params.append(f"%{author_filter}%")
    if author_filters:
        sql_frag, ap = _multi_author_clause(author_filters)
        where_parts.append(sql_frag)
        params.extend(ap)
    if technique:
        where_parts.append(
            "EXISTS (SELECT 1 FROM question_techniques te "
            "WHERE te.question_id = q.id AND te.primary_technique = ?)")
        params.append(technique)

    where_sql = " AND ".join(where_parts)
    rows = conn.execute(f"""
        SELECT DISTINCT qt.question_id
        FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        JOIN questions q ON qt.question_id = q.id
        JOIN packs p ON q.pack_id = p.id
        WHERE {where_sql}
    """, params).fetchall()

    all_ids = [r[0] for r in rows]
    if exclude_ids:
        all_ids = [qid for qid in all_ids if qid not in exclude_ids]
    if not all_ids:
        return []

    rng = random.Random(seed)
    sample_ids = rng.sample(all_ids, min(limit, len(all_ids)))
    return _fetch_full_questions(conn, sample_ids)


def get_training_questions_adaptive(
    conn: sqlite3.Connection,
    category_ids: Optional[List[int]] = None,
    take_rate_range: Tuple[float, float] = (0.35, 0.70),
    limit: int = 10,
    seed: Optional[int] = None,
    exclude_ids: Optional[Set[int]] = None,
    model_name: Optional[str] = None,
    technique: Optional[str] = None,
) -> List[Dict]:
    """Вопросы по темам с ЭМПИРИЧЕСКОЙ сложностью.

    Отличие от get_training_questions_by_category: сложность — не редакторская
    оценка пакета (q.difficulty), а фактическая беручесть поля по
    question_result_stats.take_rate. Вопросы, которые поле берёт в 35–70%
    случаев, — зона ближайшего развития: не гробы и не разминка.
    """
    where_parts = ["s.take_rate BETWEEN ? AND ?"]
    params: list = [take_rate_range[0], take_rate_range[1]]

    if category_ids:
        placeholders = ",".join("?" * len(category_ids))
        where_parts.append(f"c.id IN ({placeholders})")
        params.extend(category_ids)
    if model_name:
        where_parts.append("qt.model_name = ?")
        params.append(model_name)
    if technique:
        where_parts.append(
            "EXISTS (SELECT 1 FROM question_techniques te "
            "WHERE te.question_id = qt.question_id AND te.primary_technique = ?)")
        params.append(technique)

    where_sql = " AND ".join(where_parts)
    rows = conn.execute(f"""
        SELECT DISTINCT qt.question_id
        FROM question_topics qt
        JOIN subcategories sc ON qt.subcategory_id = sc.id
        JOIN categories c ON sc.category_id = c.id
        JOIN question_result_stats s ON s.question_id = qt.question_id
        WHERE {where_sql}
    """, params).fetchall()

    all_ids = [r[0] for r in rows]
    if exclude_ids:
        all_ids = [qid for qid in all_ids if qid not in exclude_ids]
    if not all_ids:
        return []

    rng = random.Random(seed)
    sample_ids = rng.sample(all_ids, min(limit, len(all_ids)))
    return _fetch_full_questions(conn, sample_ids)


def get_training_questions_gentleman(
    conn: sqlite3.Connection,
    data_dir: Path,
    gentleman_category: Optional[str] = None,
    difficulty_range: Optional[Tuple[float, float]] = None,
    limit: int = 10,
    seed: Optional[int] = None,
) -> List[Dict]:
    """Случайные вопросы из gentleman set."""
    top_path = data_dir / "top_answers.json"
    if not top_path.exists():
        return []

    top_data = json.loads(top_path.read_text(encoding="utf-8"))
    answer_questions = top_data.get("answer_questions", {})

    if gentleman_category:
        cat_path = data_dir / "categorized_answers.json"
        if not cat_path.exists():
            return []
        cat_data = json.loads(cat_path.read_text(encoding="utf-8"))
        answer_cat = cat_data.get("answer_category", {})
        matching = {a for a, c in answer_cat.items() if c == gentleman_category}
        filtered = {a: ids for a, ids in answer_questions.items() if a in matching}
    else:
        filtered = answer_questions

    all_ids = list({qid for ids in filtered.values() for qid in ids})
    if not all_ids:
        return []

    if difficulty_range:
        placeholders = ",".join("?" * len(all_ids))
        rows = conn.execute(f"""
            SELECT q.id FROM questions q
            WHERE q.id IN ({placeholders})
              AND q.difficulty BETWEEN ? AND ?
        """, all_ids + list(difficulty_range)).fetchall()
        all_ids = [r[0] for r in rows]
        if not all_ids:
            return []

    rng = random.Random(seed)
    sample_ids = rng.sample(all_ids, min(limit, len(all_ids)))
    return _fetch_full_questions(conn, sample_ids)


def get_training_questions_random(
    conn: sqlite3.Connection,
    difficulty_range: Optional[Tuple[float, float]] = None,
    limit: int = 10,
    seed: Optional[int] = None,
    author_filter: Optional[str] = None,
    author_filters: Optional[List[str]] = None,
    technique: Optional[str] = None,
) -> List[Dict]:
    """Случайные вопросы из всей базы."""
    where_parts = ["1=1"]
    params: list = []
    need_pack_join = False

    if difficulty_range:
        where_parts.append("q.difficulty BETWEEN ? AND ?")
        params.extend(difficulty_range)
        need_pack_join = True

    if author_filter:
        where_parts.append("q.authors LIKE ?")
        params.append(f"%{author_filter}%")

    if author_filters:
        sql_frag, ap = _multi_author_clause(author_filters)
        where_parts.append(sql_frag)
        params.extend(ap)

    if technique:
        where_parts.append(
            "EXISTS (SELECT 1 FROM question_techniques te "
            "WHERE te.question_id = q.id AND te.primary_technique = ?)")
        params.append(technique)

    has_filters = len(where_parts) > 1
    if has_filters:
        where_sql = " AND ".join(where_parts)
        pack_join = "JOIN packs p ON q.pack_id = p.id" if need_pack_join else ""
        rows = conn.execute(f"""
            SELECT q.id FROM questions q
            {pack_join}
            WHERE {where_sql}
        """, params).fetchall()
    else:
        rows = conn.execute("SELECT id FROM questions").fetchall()

    all_ids = [r[0] for r in rows]
    if not all_ids:
        return []

    rng = random.Random(seed)
    sample_ids = rng.sample(all_ids, min(limit, len(all_ids)))
    return _fetch_full_questions(conn, sample_ids)


def _fetch_full_questions(
    conn: sqlite3.Connection, question_ids: List[int]
) -> List[Dict]:
    """Полные данные вопросов по ID (с primary topic)."""
    if not question_ids:
        return []
    placeholders = ",".join("?" * len(question_ids))
    rows = conn.execute(f"""
        WITH primary_topic AS (
            SELECT qt.question_id, qt.subcategory_id, qt.confidence,
                   ROW_NUMBER() OVER (
                       PARTITION BY qt.question_id
                       ORDER BY qt.confidence DESC
                   ) AS rn
            FROM question_topics qt
        )
        SELECT q.id, q.text, q.answer, q.zachet, q.nezachet,
               q.comment, q.source, q.authors,
               q.razdatka_text, q.razdatka_pic,
               p.title AS pack_title, p.difficulty AS pack_difficulty,
               q.difficulty AS question_difficulty,
               p.link AS pack_link,
               c.name_ru AS category, s.name_ru AS subcategory,
               pt.confidence
        FROM questions q
        LEFT JOIN packs p ON q.pack_id = p.id
        LEFT JOIN primary_topic pt ON q.id = pt.question_id AND pt.rn = 1
        LEFT JOIN subcategories s ON pt.subcategory_id = s.id
        LEFT JOIN categories c ON s.category_id = c.id
        WHERE q.id IN ({placeholders})
    """, question_ids).fetchall()

    # Сохраняем порядок sample_ids
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[qid] for qid in question_ids if qid in by_id]
