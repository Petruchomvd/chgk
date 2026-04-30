import sqlite3

from bot.main import get_bot_token, parse_allowed_user_ids
from database.training_db import (
    count_due,
    get_due_question_ids,
    get_stats,
    get_training_connection,
    record_attempt,
)


def test_parse_allowed_user_ids_merges_allowlist_and_owner():
    env = {
        "CHGK_BOT_ALLOWED_TG_IDS": "1001, 1002; 1003\n1004",
        "CHGK_BOT_OWNER_TG_ID": "42",
    }

    assert parse_allowed_user_ids(env) == {42, 1001, 1002, 1003, 1004}


def test_get_bot_token_uses_only_chgk_bot_token():
    env = {
        "CHGK_BOT_TOKEN": "primary-token",
        "TG_DIGEST_BOT_TOKEN": "should-be-ignored",
    }

    assert get_bot_token(env) == "primary-token"


def test_training_progress_isolated_between_users(tmp_path):
    conn = get_training_connection(tmp_path / "training.db")
    try:
        record_attempt(
            conn,
            user_id=1,
            question_id=77,
            knew=False,
            user_answer="",
            time_seconds=10.0,
            mode="random",
            category="История",
        )
        record_attempt(
            conn,
            user_id=2,
            question_id=77,
            knew=True,
            user_answer="",
            time_seconds=8.0,
            mode="random",
            category="История",
        )

        stats_user_1 = get_stats(conn, 1)
        stats_user_2 = get_stats(conn, 2)
        leitner_rows = conn.execute(
            "SELECT user_id, question_id, box FROM leitner ORDER BY user_id"
        ).fetchall()
    finally:
        conn.close()

    assert stats_user_1["total_attempts"] == 1
    assert stats_user_1["correct_attempts"] == 0
    assert stats_user_2["total_attempts"] == 1
    assert stats_user_2["correct_attempts"] == 1
    assert [(row["user_id"], row["question_id"], row["box"]) for row in leitner_rows] == [
        (1, 77, 1),
        (2, 77, 2),
    ]


def test_legacy_training_db_is_migrated_to_owner(tmp_path, monkeypatch):
    db_path = tmp_path / "training.db"
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.executescript(
        """
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            attempted_at TEXT NOT NULL,
            user_answer TEXT,
            knew INTEGER NOT NULL,
            time_seconds REAL,
            mode TEXT,
            category TEXT
        );
        CREATE TABLE leitner (
            question_id INTEGER PRIMARY KEY,
            box INTEGER NOT NULL,
            next_review_at TEXT NOT NULL,
            last_attempt_at TEXT,
            consecutive_correct INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO attempts (
            question_id, attempted_at, user_answer, knew, time_seconds, mode, category
        ) VALUES (5, '2026-04-30T00:00:00', '', 1, 3.2, 'random', 'Наука');
        INSERT INTO leitner (
            question_id, box, next_review_at, last_attempt_at, consecutive_correct
        ) VALUES (5, 3, '2000-01-01T00:00:00', '2026-04-30T00:00:00', 2);
        """
    )
    legacy_conn.close()

    monkeypatch.setenv("CHGK_BOT_OWNER_TG_ID", "477617262")

    conn = get_training_connection(db_path)
    try:
        stats = get_stats(conn, 477617262)
        due = count_due(conn, 477617262)
        due_ids = get_due_question_ids(conn, 477617262)
        pk_info = conn.execute("PRAGMA table_info(leitner)").fetchall()
    finally:
        conn.close()

    assert stats["total_attempts"] == 1
    assert due == 1
    assert due_ids == [5]
    assert [row["name"] for row in pk_info if row["pk"]] == ["user_id", "question_id"]
