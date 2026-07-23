"""Per-user training progress storage for the Telegram bot."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from config import PROJECT_ROOT

_configured_training_db_path = Path(
    os.environ.get("CHGK_TRAINING_DB_PATH", "training.db")
).expanduser()
TRAINING_DB_PATH = (
    _configured_training_db_path
    if _configured_training_db_path.is_absolute()
    else PROJECT_ROOT / _configured_training_db_path
)
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

_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    username        TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name    TEXT NOT NULL,
    password_hash   BLOB NOT NULL,
    password_salt   BLOB NOT NULL,
    role            TEXT NOT NULL DEFAULT 'player',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_identities (
    user_id              INTEGER NOT NULL,
    provider             TEXT NOT NULL,
    provider_user_id     TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    PRIMARY KEY (provider, provider_user_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_identities_user
ON user_identities(user_id);
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
    conn.executescript(_USERS_SCHEMA)
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
            # Правильный с первой попытки вопрос не надо заучивать дословно:
            # в ЧГК он больше не встретится. Очередь нужна только для ошибок.
            return
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


def get_seen_question_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    """Все вопросы, которые пользователь уже видел.

    Нужно для тренировки темы новыми вопросами: показывать заново тот же вопрос
    ЧГК бессмысленно — его не спросят второй раз, а ответ вспомнится по
    формулировке, а не по знанию.
    """
    rows = conn.execute(
        "SELECT DISTINCT question_id FROM attempts WHERE user_id = ?", (user_id,)
    ).fetchall()
    return {r[0] for r in rows}


def get_recent_failed_ids(
    conn: sqlite3.Connection, user_id: int, limit: int = 30
) -> list[int]:
    """Вопросы, где ПОСЛЕДНЯЯ попытка — провал, свежие первыми.

    Исправленные ошибки (провалил, потом взял на повторении) не считаются:
    смотрим только на актуальное состояние знания.
    """
    rows = conn.execute(
        """
        SELECT a.question_id
        FROM attempts a
        WHERE a.user_id = ?
          AND a.attempted_at = (
              SELECT MAX(b.attempted_at) FROM attempts b
              WHERE b.user_id = a.user_id AND b.question_id = a.question_id
          )
          AND a.knew = 0
        ORDER BY a.attempted_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [r["question_id"] for r in rows]


def get_due_question_ids(
    conn: sqlite3.Connection,
    user_id: int,
    limit: int = 50,
) -> List[int]:
    """Question ids due for review for one user."""
    now = _now_iso()
    rows = conn.execute(
        """
        SELECT l.question_id
        FROM leitner l
        WHERE l.user_id = ? AND l.next_review_at <= ?
          AND EXISTS (
              SELECT 1 FROM attempts a
              WHERE a.user_id = l.user_id
                AND a.question_id = l.question_id
                AND a.knew = 0
          )
        ORDER BY l.next_review_at ASC
        LIMIT ?
        """,
        (user_id, now, limit),
    ).fetchall()
    return [r["question_id"] for r in rows]


def count_due(conn: sqlite3.Connection, user_id: int) -> int:
    now = _now_iso()
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM leitner l
        WHERE l.user_id = ? AND l.next_review_at <= ?
          AND EXISTS (
              SELECT 1 FROM attempts a
              WHERE a.user_id = l.user_id
                AND a.question_id = l.question_id
                AND a.knew = 0
          )
        """,
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
        """
        SELECT l.box, COUNT(*) AS c
        FROM leitner l
        WHERE l.user_id = ?
          AND EXISTS (
              SELECT 1 FROM attempts a
              WHERE a.user_id = l.user_id
                AND a.question_id = l.question_id
                AND a.knew = 0
          )
        GROUP BY l.box
        ORDER BY l.box
        """,
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


def get_progress_metrics(conn: sqlite3.Connection, user_id: int) -> dict:
    """Небольшие показатели динамики, которые дополняют общий процент."""
    rows = conn.execute(
        """
        SELECT substr(attempted_at, 1, 10) AS day
        FROM attempts
        WHERE user_id = ?
        GROUP BY substr(attempted_at, 1, 10)
        ORDER BY day DESC
        """,
        (user_id,),
    ).fetchall()
    active_days = {row["day"] for row in rows if row["day"]}
    today = datetime.now().date()
    streak = 0
    cursor = today
    while cursor.isoformat() in active_days:
        streak += 1
        cursor -= timedelta(days=1)

    recent = conn.execute(
        """
        SELECT knew, time_seconds
        FROM attempts
        WHERE user_id = ?
        ORDER BY attempted_at DESC, id DESC
        LIMIT 20
        """,
        (user_id,),
    ).fetchall()
    recent_success = (
        round(100 * sum(row["knew"] for row in recent) / len(recent))
        if recent
        else None
    )
    timed = [row["time_seconds"] for row in recent if row["time_seconds"] is not None]
    return {
        "active_days_30": sum(
            1
            for day in active_days
            if day >= (today - timedelta(days=29)).isoformat()
        ),
        "current_streak": streak,
        "recent_success_pct": recent_success,
        "recent_sample": len(recent),
        "recent_avg_seconds": round(sum(timed) / len(timed), 1) if timed else None,
    }
