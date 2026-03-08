"""CRUD-функции для работы с Telegram-постами."""

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from database.db import get_connection

_TG_SCHEMA = Path(__file__).parent / "tg_schema.sql"


def ensure_tg_tables(conn: sqlite3.Connection) -> None:
    """Применить схему TG-таблиц (идемпотентно)."""
    schema_sql = _TG_SCHEMA.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


def upsert_channel(
    conn: sqlite3.Connection,
    username: str,
    title: str = "",
    expected_category: str = "",
) -> int:
    """Добавить или обновить канал. Возвращает channel_id."""
    row = conn.execute(
        "SELECT id FROM tg_channels WHERE username = ?", (username,)
    ).fetchone()
    if row:
        if title:
            conn.execute(
                "UPDATE tg_channels SET title = ? WHERE id = ?", (title, row["id"])
            )
            conn.commit()
        return row["id"]

    cur = conn.execute(
        "INSERT INTO tg_channels (username, title, expected_category) VALUES (?, ?, ?)",
        (username, title, expected_category),
    )
    conn.commit()
    return cur.lastrowid


def get_channel(conn: sqlite3.Connection, username: str) -> Optional[Dict]:
    """Получить данные канала по username."""
    row = conn.execute(
        "SELECT * FROM tg_channels WHERE username = ?", (username,)
    ).fetchone()
    return dict(row) if row else None


def get_active_channels(conn: sqlite3.Connection) -> List[Dict]:
    """Все активные каналы."""
    rows = conn.execute(
        "SELECT * FROM tg_channels WHERE is_active = 1"
    ).fetchall()
    return [dict(r) for r in rows]


def insert_post(
    conn: sqlite3.Connection,
    channel_id: int,
    post_id: int,
    text: str,
    link: str,
    post_date: str = "",
    views: int = 0,
    is_useful: int = 1,
) -> bool:
    """Вставить пост (игнорирует дубликаты). Возвращает True если вставлен."""
    try:
        conn.execute(
            """INSERT INTO tg_posts (channel_id, post_id, text, link, post_date, views, is_useful)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (channel_id, post_id, text, link, post_date, views, is_useful),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def update_last_parsed_id(
    conn: sqlite3.Connection, channel_id: int, last_id: int
) -> None:
    """Обновить last_parsed_id канала."""
    conn.execute(
        "UPDATE tg_channels SET last_parsed_id = ? WHERE id = ?",
        (last_id, channel_id),
    )
    conn.commit()


def get_unclassified_posts(
    conn: sqlite3.Connection, limit: int = 0
) -> List[Dict]:
    """Посты без классификации."""
    sql = """
        SELECT p.*, c.username AS channel_username
        FROM tg_posts p
        JOIN tg_channels c ON c.id = p.channel_id
        WHERE p.category IS NULL AND p.is_useful = 1
        ORDER BY p.id
    """
    if limit > 0:
        sql += f" LIMIT {limit}"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def update_post_category(
    conn: sqlite3.Connection,
    post_id: int,
    category: str,
    confidence: float = None,
    model_name: str = "",
) -> None:
    """Записать результат классификации поста."""
    conn.execute(
        """UPDATE tg_posts
           SET category = ?, confidence = ?, model_name = ?,
               classified_at = datetime('now')
           WHERE id = ?""",
        (category, confidence, model_name, post_id),
    )
    conn.commit()


def get_posts_by_categories(
    conn: sqlite3.Connection,
    categories: List[str],
    days: int = 30,
    limit_per_cat: int = 10,
) -> Dict[str, List[Dict]]:
    """Посты по категориям за последние N дней."""
    result = {}
    for cat in categories:
        rows = conn.execute(
            """SELECT p.*, c.username AS channel_username, c.title AS channel_title
               FROM tg_posts p
               JOIN tg_channels c ON c.id = p.channel_id
               WHERE p.category = ? AND p.is_useful = 1
                 AND p.post_date >= datetime('now', ?)
               ORDER BY p.views DESC
               LIMIT ?""",
            (cat, f"-{days} days", limit_per_cat),
        ).fetchall()
        if rows:
            result[cat] = [dict(r) for r in rows]
    return result


def get_tg_stats(conn: sqlite3.Connection) -> Dict:
    """Статистика по ТГ постам."""
    total = conn.execute("SELECT COUNT(*) FROM tg_posts").fetchone()[0]
    classified = conn.execute(
        "SELECT COUNT(*) FROM tg_posts WHERE category IS NOT NULL"
    ).fetchone()[0]
    useful = conn.execute(
        "SELECT COUNT(*) FROM tg_posts WHERE is_useful = 1"
    ).fetchone()[0]
    channels = conn.execute("SELECT COUNT(*) FROM tg_channels").fetchone()[0]

    by_cat = conn.execute(
        """SELECT category, COUNT(*) as cnt FROM tg_posts
           WHERE category IS NOT NULL
           GROUP BY category ORDER BY cnt DESC"""
    ).fetchall()

    return {
        "total_posts": total,
        "classified": classified,
        "useful": useful,
        "channels": channels,
        "by_category": {r["category"]: r["cnt"] for r in by_cat},
    }
