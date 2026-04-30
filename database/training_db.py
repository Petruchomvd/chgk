"""Per-user training progress storage for the Telegram bot."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from config import PROJECT_ROOT

TRAINING_DB_PATH = PROJECT_ROOT / "training.db"
UNCATEGORIZED_LABEL = "Без категории"

_ATTEMPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    question_id     INTEGER NOT NULL,
    attempted_at    TEXT NOT NULL,
    user_answer     TEXT,
    knew            INTEGER NOT NULL,
    time_seconds    REAL,
    mode            TEXT,
    category        TEXT
);

CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
CREATE INDEX IF NOT EXISTS idx_attempts_time ON attempts(attempted_at);
CREATE INDEX IF NOT EXISTS idx_attempts_user_question ON attempts(user_id, question_id);
CREATE INDEX IF NOT EXISTS idx_attempts_user_time ON attempts(user_id, attempted_at);
"""

_LEITNER_SCHEMA = """
CREATE TABLE IF NOT EXISTS leitner (
    user_id              INTEGER NOT NULL,
    question_id          INTEGER NOT NULL,
    box                  INTEGER NOT NULL,
    next_review_at       TEXT NOT NULL,
    last_attempt_at      TEXT,
    consecutive_correct  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_leitner_review ON leitner(next_review_at);
CREATE INDEX IF NOT EXISTS idx_leitner_user_review ON leitner(user_id, next_review_at);
"""

LEITNER_INTERVALS_DAYS = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
MAX_BOX = 5


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    pk_rows = sorted((row for row in rows if row["pk"]), key=lambda row: row["pk"])
    return [row["name"] for row in pk_rows]


def _parse_allowed_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    normalized = raw.replace(";", ",").replace("\n", ",")
    for part in normalized.split(","):
        token = part.strip()
        if token.isdigit():
            ids.add(int(token))
    return ids


def _legacy_user_id() -> int:
    owner_raw = os.environ.get("CHGK_BOT_OWNER_TG_ID", "").strip()
    if owner_raw.isdigit():
        return int(owner_raw)

    allowed_ids = _parse_allowed_ids(os.environ.get("CHGK_BOT_ALLOWED_TG_IDS", ""))
    if len(allowed_ids) == 1:
        return next(iter(allowed_ids))

    return 0


def _migrate_attempts(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "attempts"):
        conn.executescript(_ATTEMPTS_SCHEMA)
        return

    columns = _column_names(conn, "attempts")
    if "user_id" not in columns:
        conn.execute("ALTER TABLE attempts ADD COLUMN user_id INTEGER")
        conn.execute(
            "UPDATE attempts SET user_id = ? WHERE user_id IS NULL",
            (_legacy_user_id(),),
        )
    else:
        conn.execute(
            "UPDATE attempts SET user_id = ? WHERE user_id IS NULL",
            (_legacy_user_id(),),
        )

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_time ON attempts(attempted_at);
        CREATE INDEX IF NOT EXISTS idx_attempts_user_question ON attempts(user_id, question_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_user_time ON attempts(user_id, attempted_at);
        """
    )


def _migrate_leitner(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "leitner"):
        conn.executescript(_LEITNER_SCHEMA)
        return

    columns = _column_names(conn, "leitner")
    pk_columns = _pk_columns(conn, "leitner")
    if "user_id" in columns and pk_columns == ["user_id", "question_id"]:
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_leitner_review ON leitner(next_review_at);
            CREATE INDEX IF NOT EXISTS idx_leitner_user_review ON leitner(user_id, next_review_at);
            """
        )
        return

    legacy_user_id = _legacy_user_id()
    conn.execute("DROP TABLE IF EXISTS leitner_legacy_migrate")
    conn.execute("ALTER TABLE leitner RENAME TO leitner_legacy_migrate")
    conn.executescript(_LEITNER_SCHEMA)

    if "user_id" in columns:
        conn.execute(
            """
            INSERT OR REPLACE INTO leitner (
                user_id, question_id, box, next_review_at, last_attempt_at, consecutive_correct
            )
            SELECT
                COALESCE(user_id, ?),
                question_id,
                box,
                next_review_at,
                last_attempt_at,
                consecutive_correct
            FROM leitner_legacy_migrate
            """,
            (legacy_user_id,),
        )
    else:
        conn.execute(
            """
            INSERT OR REPLACE INTO leitner (
                user_id, question_id, box, next_review_at, last_attempt_at, consecutive_correct
            )
            SELECT
                ?,
                question_id,
                box,
                next_review_at,
                last_attempt_at,
                consecutive_correct
            FROM leitner_legacy_migrate
            """,
            (legacy_user_id,),
        )

    conn.execute("DROP TABLE leitner_legacy_migrate")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    _migrate_attempts(conn)
    _migrate_leitner(conn)
    conn.commit()


def get_training_connection(
    db_path: Path | str = TRAINING_DB_PATH,
) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    _ensure_schema(conn)
    return conn


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record_attempt(
    conn: sqlite3.Connection,
    user_id: int,
    question_id: int,
    knew: bool,
    user_answer: str,
    time_seconds: float,
    mode: str,
    category: Optional[str],
) -> None:
    """Persist an attempt and update the user's Leitner queue."""
    now = _now_iso()
    conn.execute(
        "INSERT INTO attempts (user_id, question_id, attempted_at, user_answer, knew, "
        "time_seconds, mode, category) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, question_id, now, user_answer, int(knew), time_seconds, mode, category),
    )
    _update_leitner(conn, user_id, question_id, knew, now)
    conn.commit()


def _update_leitner(
    conn: sqlite3.Connection,
    user_id: int,
    question_id: int,
    knew: bool,
    now_iso: str,
) -> None:
    row = conn.execute(
        "SELECT box, consecutive_correct FROM leitner WHERE user_id = ? AND question_id = ?",
        (user_id, question_id),
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
    next_review = (
        datetime.fromisoformat(now_iso) + timedelta(days=days)
    ).isoformat(timespec="seconds")

    conn.execute(
        "INSERT INTO leitner (user_id, question_id, box, next_review_at, last_attempt_at, "
        "consecutive_correct) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, question_id) DO UPDATE SET "
        "box = excluded.box, next_review_at = excluded.next_review_at, "
        "last_attempt_at = excluded.last_attempt_at, "
        "consecutive_correct = excluded.consecutive_correct",
        (user_id, question_id, new_box, next_review, now_iso, consec),
    )


def get_due_question_ids(
    conn: sqlite3.Connection,
    user_id: int,
    limit: int = 50,
) -> List[int]:
    """Question ids due for review for one user."""
    now = _now_iso()
    rows = conn.execute(
        "SELECT question_id FROM leitner WHERE user_id = ? AND next_review_at <= ? "
        "ORDER BY next_review_at ASC LIMIT ?",
        (user_id, now, limit),
    ).fetchall()
    return [r["question_id"] for r in rows]


def count_due(conn: sqlite3.Connection, user_id: int) -> int:
    now = _now_iso()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM leitner WHERE user_id = ? AND next_review_at <= ?",
        (user_id, now),
    ).fetchone()
    return row["c"]


def get_stats(conn: sqlite3.Connection, user_id: int) -> dict:
    """Per-user progress summary for /stats."""
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM attempts WHERE user_id = ?",
        (user_id,),
    ).fetchone()["c"]
    correct = conn.execute(
        "SELECT COUNT(*) AS c FROM attempts WHERE user_id = ? AND knew = 1",
        (user_id,),
    ).fetchone()["c"]
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT question_id) AS c FROM attempts WHERE user_id = ?",
        (user_id,),
    ).fetchone()["c"]
    due = count_due(conn, user_id)

    by_cat = conn.execute(
        "SELECT COALESCE(NULLIF(category, ''), ?) AS category, "
        "COUNT(*) AS total, SUM(knew) AS knew "
        "FROM attempts WHERE user_id = ? "
        "GROUP BY COALESCE(NULLIF(category, ''), ?) ORDER BY total DESC",
        (UNCATEGORIZED_LABEL, user_id, UNCATEGORIZED_LABEL),
    ).fetchall()

    by_box = conn.execute(
        "SELECT box, COUNT(*) AS c FROM leitner WHERE user_id = ? GROUP BY box ORDER BY box",
        (user_id,),
    ).fetchall()

    return {
        "total_attempts": total,
        "correct_attempts": correct,
        "distinct_questions": distinct,
        "due_now": due,
        "by_category": [dict(r) for r in by_cat],
        "by_box": [dict(r) for r in by_box],
    }
