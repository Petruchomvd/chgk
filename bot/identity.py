"""Связь Telegram-аккаунта с внутренним игроком Картотеки."""
from __future__ import annotations

import re
from typing import Optional

from database.training_db import get_training_connection


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def normalize_telegram_username(username: Optional[str]) -> Optional[str]:
    raw = (username or "").strip()
    if raw.startswith("@"):
        raw = raw[1:]
    if not raw or not _USERNAME_RE.fullmatch(raw):
        return None
    return raw.lower()


def resolve_telegram_training_user_id(
    telegram_user_id: int,
    username: Optional[str],
) -> int:
    """Вернуть user_id игрока для статистики.

    Приоритет:
    1. Уже привязанный числовой Telegram ID.
    2. Созданный владельцем @username.
    3. Сам Telegram ID как fallback для владельца/старых пользователей.

    Если нашли игрока по @username, сразу дописываем числовой Telegram ID:
    username может поменяться, а ID — стабильнее.
    """
    conn = get_training_connection()
    try:
        row = conn.execute(
            """
            SELECT user_id
            FROM user_identities
            WHERE provider = 'telegram' AND provider_user_id = ?
            """,
            (str(telegram_user_id),),
        ).fetchone()
        if row:
            return int(row["user_id"])

        normalized = normalize_telegram_username(username)
        if normalized:
            row = conn.execute(
                """
                SELECT user_id
                FROM user_identities
                WHERE provider = 'telegram_username' AND provider_user_id = ?
                """,
                (normalized,),
            ).fetchone()
            if row:
                user_id = int(row["user_id"])
                conn.execute(
                    """
                    INSERT OR IGNORE INTO user_identities (
                        user_id, provider, provider_user_id, created_at
                    ) VALUES (?, 'telegram', ?, datetime('now'))
                    """,
                    (user_id, str(telegram_user_id)),
                )
                conn.commit()
                return user_id
    finally:
        conn.close()

    return telegram_user_id


def telegram_identity_exists(telegram_user_id: Optional[int], username: Optional[str]) -> bool:
    """Проверить, разрешён ли пользователь через ID или @username из базы."""
    if telegram_user_id is None:
        return False

    conn = get_training_connection()
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM user_identities
            WHERE provider = 'telegram' AND provider_user_id = ?
            """,
            (str(telegram_user_id),),
        ).fetchone()
        if row:
            return True

        normalized = normalize_telegram_username(username)
        if not normalized:
            return False
        row = conn.execute(
            """
            SELECT 1
            FROM user_identities
            WHERE provider = 'telegram_username' AND provider_user_id = ?
            """,
            (normalized,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
