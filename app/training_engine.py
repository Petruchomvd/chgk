"""Тренировочная сессия: загрузка вопросов, фиксация ответов, Leitner.

UI-agnostic: вызывается из бота, дашборда или CLI. Не зависит от Streamlit/aiogram.
"""
from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from dashboard.training_queries import (
    _fetch_full_questions,
    get_training_questions_by_category,
    get_training_questions_random,
)
from database.training_db import get_due_question_ids, record_attempt

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
    count: int = 10,
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
    count: int = 10,
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
    count: Optional[int] = None,
    seed: Optional[int] = None,
) -> TrainingSession:
    """Все вопросы конкретного пака (или семпл, если count указан)."""
    rows = chgk_conn.execute(
        "SELECT id FROM questions WHERE pack_id = ? ORDER BY tour_number, number",
        (pack_id,),
    ).fetchall()
    qids = [r["id"] for r in rows]
    if count is not None and count < len(qids):
        rng = random.Random(seed)
        qids = rng.sample(qids, count)
    questions = _fetch_full_questions(chgk_conn, qids)

    pack = chgk_conn.execute(
        "SELECT title FROM packs WHERE id = ?", (pack_id,)
    ).fetchone()
    title = pack["title"] if pack else f"pack #{pack_id}"
    return TrainingSession(
        mode="tournament",
        questions=questions,
        filters_repr=f"{title} · {len(questions)} вопросов",
    )


def start_review(
    chgk_conn: sqlite3.Connection,
    training_conn: sqlite3.Connection,
    count: int = 20,
) -> TrainingSession:
    """Вопросы из Leitner-очереди, у которых наступило время повторения."""
    qids = get_due_question_ids(training_conn, limit=count)
    questions = _fetch_full_questions(chgk_conn, qids)
    return TrainingSession(
        mode="review",
        questions=questions,
        filters_repr=f"повторения · {len(questions)} вопросов",
    )


def search_tournaments(chgk_conn: sqlite3.Connection, query: str, limit: int = 20) -> List[Dict]:
    """Поиск паков по подстроке в названии."""
    rows = chgk_conn.execute(
        "SELECT p.id, p.title, p.difficulty, COUNT(q.id) AS questions_count "
        "FROM packs p LEFT JOIN questions q ON q.pack_id = p.id "
        "WHERE p.title LIKE ? "
        "GROUP BY p.id "
        "ORDER BY questions_count DESC LIMIT ?",
        (f"%{query}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


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
    knew: bool,
) -> bool:
    """Записать самооценку, перейти к следующему. Возвращает True если есть следующий."""
    q = session.current()
    if q is None:
        return False

    elapsed = time.time() - session.question_started_at
    record_attempt(
        training_conn,
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
        cat = r.get("category") or "—"
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
