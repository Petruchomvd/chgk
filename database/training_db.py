"""training.db — личный прогресс пользователя: попытки и Leitner-очередь.

Отдельная БД от chgk_analysis.db, чтобы не смешивать каталог вопросов
с пользовательским состоянием. При переезде на VPS переносится отдельно.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

from config import PROJECT_ROOT

TRAINING_DB_PATH = PROJECT_ROOT / "training.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id     INTEGER NOT NULL,
    attempted_at    TEXT NOT NULL,
    user_answer     TEXT,
    knew            INTEGER NOT NULL,
    time_seconds    REAL,
    mode            TEXT,
    category        TEXT
);

CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
CREATE INDEX IF NOT EXISTS idx_attempts_time     ON attempts(attempted_at);

CREATE TABLE IF NOT EXISTS leitner (
    question_id          INTEGER PRIMARY KEY,
    box                  INTEGER NOT NULL,
    next_review_at       TEXT NOT NULL,
    last_attempt_at      TEXT,
    consecutive_correct  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_leitner_review ON leitner(next_review_at);
"""

LEITNER_INTERVALS_DAYS = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
MAX_BOX = 5


def get_training_connection(db_path: Path | str = TRAINING_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record_attempt(
    conn: sqlite3.Connection,
    question_id: int,
    knew: bool,
    user_answer: str,
    time_seconds: float,
    mode: str,
    category: Optional[str],
) -> None:
    """Записать попытку и обновить leitner."""
    now = _now_iso()
    conn.execute(
        "INSERT INTO attempts (question_id, attempted_at, user_answer, knew, "
        "time_seconds, mode, category) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (question_id, now, user_answer, int(knew), time_seconds, mode, category),
    )
    _update_leitner(conn, question_id, knew, now)
    conn.commit()


def _update_leitner(
    conn: sqlite3.Connection, question_id: int, knew: bool, now_iso: str
) -> None:
    row = conn.execute(
        "SELECT box, consecutive_correct FROM leitner WHERE question_id = ?",
        (question_id,),
    ).fetchone()

    if knew:
        if row is None:
            new_box = 2
            consec = 1
        else:
            new_box = min(row["box"] + 1, MAX_BOX)
            consec = row["consecutive_correct"] + 1
    else:
        new_box = 1
        consec = 0

    days = LEITNER_INTERVALS_DAYS[new_box]
    next_review = (datetime.fromisoformat(now_iso) + timedelta(days=days)).isoformat(timespec="seconds")

    conn.execute(
        "INSERT INTO leitner (question_id, box, next_review_at, last_attempt_at, "
        "consecutive_correct) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(question_id) DO UPDATE SET "
        "box=excluded.box, next_review_at=excluded.next_review_at, "
        "last_attempt_at=excluded.last_attempt_at, "
        "consecutive_correct=excluded.consecutive_correct",
        (question_id, new_box, next_review, now_iso, consec),
    )


def get_due_question_ids(conn: sqlite3.Connection, limit: int = 50) -> List[int]:
    """ID вопросов, которые пора повторить (next_review_at <= now)."""
    now = _now_iso()
    rows = conn.execute(
        "SELECT question_id FROM leitner WHERE next_review_at <= ? "
        "ORDER BY next_review_at ASC LIMIT ?",
        (now, limit),
    ).fetchall()
    return [r["question_id"] for r in rows]


def count_due(conn: sqlite3.Connection) -> int:
    now = _now_iso()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM leitner WHERE next_review_at <= ?", (now,)
    ).fetchone()
    return row["c"]


def get_stats(conn: sqlite3.Connection) -> dict:
    """Сводка прогресса для /stats."""
    total = conn.execute("SELECT COUNT(*) AS c FROM attempts").fetchone()["c"]
    correct = conn.execute(
        "SELECT COUNT(*) AS c FROM attempts WHERE knew = 1"
    ).fetchone()["c"]
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT question_id) AS c FROM attempts"
    ).fetchone()["c"]
    due = count_due(conn)

    by_cat = conn.execute(
        "SELECT category, COUNT(*) AS total, SUM(knew) AS knew "
        "FROM attempts WHERE category IS NOT NULL "
        "GROUP BY category ORDER BY total DESC"
    ).fetchall()

    by_box = conn.execute(
        "SELECT box, COUNT(*) AS c FROM leitner GROUP BY box ORDER BY box"
    ).fetchall()

    return {
        "total_attempts": total,
        "correct_attempts": correct,
        "distinct_questions": distinct,
        "due_now": due,
        "by_category": [dict(r) for r in by_cat],
        "by_box": [dict(r) for r in by_box],
    }
