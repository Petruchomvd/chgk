"""Тренировочная сессия: загрузка вопросов, фиксация ответов, Leitner.

UI-agnostic: вызывается из бота, дашборда или CLI. Не зависит от Streamlit/aiogram.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from dashboard.training_queries import (
    _fetch_full_questions,
    get_training_questions_by_category,
    get_training_questions_random,
)
from database.training_db import (
    UNCATEGORIZED_LABEL,
    get_due_question_ids,
    record_attempt,
)

Mode = Literal["random", "category", "tournament", "review"]


@dataclass
class TrainingSession:
    mode: Mode
    questions: List[Dict] = field(default_factory=list)
    index: int = 0
    started_at: float = field(default_factory=time.time)
    question_started_at: float = field(default_factory=time.time)
    user_answer: str = ""
    results: List[Dict] = field(default_factory=list)
    filters_repr: str = ""

    def current(self) -> Optional[Dict]:
        if 0 <= self.index < len(self.questions):
            return self.questions[self.index]
        return None

    def is_finished(self) -> bool:
        return self.index >= len(self.questions)

    def total(self) -> int:
        return len(self.questions)


def start_random(
    chgk_conn: sqlite3.Connection,
    count: int = 12,
    seed: Optional[int] = None,
    difficulty_range: Optional[tuple] = None,
) -> TrainingSession:
    questions = get_training_questions_random(
        chgk_conn, difficulty_range, count, seed, None
    )
    return TrainingSession(
        mode="random",
        questions=questions,
        filters_repr=f"случайные · {len(questions)} вопросов",
    )


def start_by_category(
    chgk_conn: sqlite3.Connection,
    category_ids: List[int],
    count: int = 12,
    seed: Optional[int] = None,
    difficulty_range: Optional[tuple] = None,
) -> TrainingSession:
    questions = get_training_questions_by_category(
        chgk_conn, category_ids, None, None, difficulty_range, count, seed, None
    )
    return TrainingSession(
        mode="category",
        questions=questions,
        filters_repr=f"по категориям · {len(questions)} вопросов",
    )


def start_by_tournament(
    chgk_conn: sqlite3.Connection,
    pack_id: int,
    count: Optional[int] = 12,
    seed: Optional[int] = None,
    tour_number: Optional[int] = None,
) -> TrainingSession:
    """Все вопросы конкретного пака (или семпл, если count указан)."""
    rows = chgk_conn.execute(
        """
        SELECT id
        FROM questions
        WHERE pack_id = ?
          AND (? IS NULL OR COALESCE(tour_number, 1) = ?)
        ORDER BY COALESCE(tour_number, 1), number, id
        """,
        (pack_id, tour_number, tour_number),
    ).fetchall()
    qids = [r["id"] for r in rows]
    if count is not None:
        qids = qids[:count]
    questions = _fetch_full_questions(chgk_conn, qids)

    pack = chgk_conn.execute(
        "SELECT title FROM packs WHERE id = ?", (pack_id,)
    ).fetchone()
    title = pack["title"] if pack else f"pack #{pack_id}"
    tour_suffix = f" · тур {tour_number}" if tour_number is not None else ""
    return TrainingSession(
        mode="tournament",
        questions=questions,
        filters_repr=f"{title}{tour_suffix} · {len(questions)} вопросов",
    )


def start_review(
    chgk_conn: sqlite3.Connection,
    training_conn: sqlite3.Connection,
    user_id: int,
    count: int = 12,
) -> TrainingSession:
    """Вопросы из Leitner-очереди, у которых наступило время повторения."""
    qids = get_due_question_ids(training_conn, user_id, limit=count)
    questions = _fetch_full_questions(chgk_conn, qids)
    return TrainingSession(
        mode="review",
        questions=questions,
        filters_repr=f"повторения · {len(questions)} вопросов",
    )


def get_pack_tours(chgk_conn: sqlite3.Connection, pack_id: int) -> List[Dict]:
    rows = chgk_conn.execute(
        """
        SELECT COALESCE(tour_number, 1) AS tour_number, COUNT(*) AS questions_count
        FROM questions
        WHERE pack_id = ?
        GROUP BY COALESCE(tour_number, 1)
        ORDER BY COALESCE(tour_number, 1)
        """,
        (pack_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_tournaments(chgk_conn: sqlite3.Connection, limit: int = 12) -> List[Dict]:
    rows = chgk_conn.execute(
        """
        SELECT p.id, p.title, p.difficulty, COUNT(q.id) AS questions_count
        FROM packs p
        JOIN questions q ON q.pack_id = p.id
        GROUP BY p.id
        HAVING COUNT(q.id) > 0
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def search_tournaments(chgk_conn: sqlite3.Connection, query: str, limit: int = 20) -> List[Dict]:
    """Поиск турниров по названию с Unicode-friendly ранжированием."""
    query_norm = query.strip().casefold()
    if not query_norm:
        return []

    rows = chgk_conn.execute(
        "SELECT p.id, p.title, p.difficulty, COUNT(q.id) AS questions_count "
        "FROM packs p LEFT JOIN questions q ON q.pack_id = p.id "
        "GROUP BY p.id"
    ).fetchall()

    matches: List[Dict] = []
    for row in rows:
        pack = dict(row)
        title_norm = pack["title"].casefold()
        pos = title_norm.find(query_norm)
        if pos == -1:
            continue

        if title_norm == query_norm:
            rank = 0
        elif title_norm.startswith(query_norm):
            rank = 1
        else:
            rank = 2

        pack["_rank"] = rank
        pack["_pos"] = pos
        matches.append(pack)

    matches.sort(
        key=lambda p: (
            p["_rank"],
            p["_pos"],
            -p["questions_count"],
            p["title"].casefold(),
        )
    )

    return [
        {k: v for k, v in pack.items() if not k.startswith("_")}
        for pack in matches[:limit]
    ]


def get_pack_by_id(chgk_conn: sqlite3.Connection, pack_id: int) -> Optional[Dict]:
    row = chgk_conn.execute(
        "SELECT p.id, p.title, p.difficulty, COUNT(q.id) AS questions_count "
        "FROM packs p LEFT JOIN questions q ON q.pack_id = p.id "
        "WHERE p.id = ? GROUP BY p.id",
        (pack_id,),
    ).fetchone()
    return dict(row) if row else None


def submit_answer(session: TrainingSession, user_answer: str) -> Dict:
    """Зафиксировать введённый текст ответа. Возвращает текущий вопрос."""
    session.user_answer = user_answer.strip()
    return session.current()


def record_and_advance(
    session: TrainingSession,
    training_conn: sqlite3.Connection,
    user_id: int,
    knew: bool,
) -> bool:
    """Записать самооценку, перейти к следующему. Возвращает True если есть следующий."""
    q = session.current()
    if q is None:
        return False

    elapsed = time.time() - session.question_started_at
    record_attempt(
        training_conn,
        user_id=user_id,
        question_id=q["id"],
        knew=knew,
        user_answer=session.user_answer,
        time_seconds=elapsed,
        mode=session.mode,
        category=q.get("category"),
    )
    session.results.append(
        {
            "question_id": q["id"],
            "user_answer": session.user_answer,
            "correct_answer": q["answer"],
            "knew": knew,
            "time_seconds": elapsed,
            "category": q.get("category"),
        }
    )
    session.index += 1
    session.user_answer = ""
    session.question_started_at = time.time()
    return not session.is_finished()


def session_summary(session: TrainingSession) -> Dict:
    total = len(session.results)
    correct = sum(1 for r in session.results if r["knew"])
    times = [r["time_seconds"] for r in session.results]
    avg_time = sum(times) / len(times) if times else 0.0
    by_cat: Dict[str, Dict[str, int]] = {}
    for r in session.results:
        cat = r.get("category") or UNCATEGORIZED_LABEL
        d = by_cat.setdefault(cat, {"total": 0, "correct": 0})
        d["total"] += 1
        if r["knew"]:
            d["correct"] += 1
    return {
        "total": total,
        "correct": correct,
        "pct": round(100 * correct / total) if total else 0,
        "avg_time": avg_time,
        "by_category": by_cat,
        "filters_repr": session.filters_repr,
    }
