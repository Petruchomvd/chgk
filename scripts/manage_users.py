#!/usr/bin/env python3
"""Создать или обновить веб-пользователя CHGK.

Пароль читается через getpass или stdin и никогда не передаётся аргументом.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.auth import hash_password, validate_username
from database.training_db import _legacy_user_id, get_training_connection


def _password_from_input() -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Новый пароль: ")
    second = getpass.getpass("Повторите пароль: ")
    if first != second:
        raise ValueError("Пароли не совпадают")
    return first


def upsert_user(
    username: str,
    display_name: str,
    password: str,
    user_id: int,
    role: str,
) -> None:
    normalized = validate_username(username)
    digest, salt = hash_password(password)
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_training_connection()
    try:
        conn.execute(
            """
            INSERT INTO users (
                id, username, display_name, password_hash, password_salt,
                role, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                display_name = excluded.display_name,
                password_hash = excluded.password_hash,
                password_salt = excluded.password_salt,
                role = excluded.role,
                active = 1,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                normalized,
                display_name.strip(),
                digest,
                salt,
                role,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--role", choices=("owner", "player"), default="player")
    args = parser.parse_args()

    user_id = args.user_id
    if user_id is None:
        user_id = _legacy_user_id()
    if not user_id:
        raise SystemExit(
            "Не задан user_id и не найден CHGK_BOT_OWNER_TG_ID в окружении"
        )

    try:
        password = _password_from_input()
        upsert_user(args.username, args.display_name, password, user_id, args.role)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        password = ""
    print(f"Пользователь {args.username!r} готов.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
