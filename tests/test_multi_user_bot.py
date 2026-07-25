import sqlite3

from bot.identity import resolve_telegram_training_user_id, telegram_identity_exists
from bot.main import get_bot_token, parse_allowed_user_ids
from database.training_db import (
    count_due,
    get_due_question_ids,
    get_stats,
    get_training_connection,
    record_attempt,
)
from vk_bot.main import (
    get_reminder_hour,
    main_keyboard,
    parse_allowed_user_ids as parse_allowed_vk_user_ids,
    resolve_training_user_id,
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


def test_parse_allowed_vk_user_ids_and_reminder_hour():
    env = {
        "CHGK_VK_ALLOWED_USER_IDS": "2001, 2002; 2003\n2004",
        "CHGK_VK_OWNER_USER_ID": "99",
        "CHGK_VK_REMINDER_HOUR": "20",
    }

    assert parse_allowed_vk_user_ids(env) == {99, 2001, 2002, 2003, 2004}
    assert get_reminder_hour(env) == 20


def test_vk_main_keyboard_has_training_controls_and_site_link():
    buttons = main_keyboard()["buttons"]

    labels = [
        button["action"]["label"]
        for row in buttons
        for button in row
    ]
    action_types = [
        button["action"]["type"]
        for row in buttons
        for button in row
    ]

    assert labels == ["Тренировка", "Повторение", "Статистика", "Открыть сайт"]
    assert action_types == ["text", "text", "text", "open_link"]


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
    # В очередь попадают только ошибки; правильный с первого раза вопрос ЧГК
    # незачем заучивать дословно.
    assert [(row["user_id"], row["question_id"], row["box"]) for row in leitner_rows] == [
        (1, 77, 1),
    ]


def test_vk_identity_resolves_to_shared_training_user(tmp_path, monkeypatch):
    db_path = tmp_path / "training.db"

    def connection():
        return get_training_connection(db_path)

    monkeypatch.setattr("vk_bot.main.get_training_connection", connection)

    conn = connection()
    try:
        conn.execute(
            """
            INSERT INTO users (
                id, username, display_name, password_hash, password_salt,
                role, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'player', 1, ?, ?)
            """,
            (-1, "anna", "Анна", b"digest", b"salt", "2026-07-24", "2026-07-24"),
        )
        conn.execute(
            """
            INSERT INTO user_identities (
                user_id, provider, provider_user_id, created_at
            ) VALUES (?, 'vk', ?, ?)
            """,
            (-1, "9001", "2026-07-24"),
        )
        conn.commit()
    finally:
        conn.close()

    assert resolve_training_user_id(9001) == -1
    assert resolve_training_user_id(9002) == 9002


def test_telegram_username_resolves_and_binds_numeric_id(tmp_path, monkeypatch):
    db_path = tmp_path / "training.db"

    def connection():
        return get_training_connection(db_path)

    monkeypatch.setattr("bot.identity.get_training_connection", connection)

    conn = connection()
    try:
        conn.execute(
            """
            INSERT INTO users (
                id, username, display_name, password_hash, password_salt,
                role, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'player', 1, ?, ?)
            """,
            (-1, "zhenya", "Женя", b"digest", b"salt", "2026-07-25", "2026-07-25"),
        )
        conn.execute(
            """
            INSERT INTO user_identities (
                user_id, provider, provider_user_id, created_at
            ) VALUES (?, 'telegram_username', ?, ?)
            """,
            (-1, "mtv3dd", "2026-07-25"),
        )
        conn.commit()
    finally:
        conn.close()

    assert telegram_identity_exists(777, "mtv3dd")
    assert resolve_telegram_training_user_id(777, "mtv3dd") == -1

    conn = connection()
    try:
        bound = conn.execute(
            """
            SELECT user_id
            FROM user_identities
            WHERE provider = 'telegram' AND provider_user_id = '777'
            """
        ).fetchone()
    finally:
        conn.close()

    assert bound is not None
    assert bound["user_id"] == -1


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
    # Старая запись Leitner сохраняется при миграции, но правильный без единой
    # ошибки вопрос больше не показывается в очереди.
    assert due == 0
    assert due_ids == []
    assert [row["name"] for row in pk_info if row["pk"]] == ["user_id", "question_id"]
