"""Авторизация веб-приложения без внешнего провайдера.

Пароли хранятся как scrypt-хэши. Сессия подписывается HMAC и передаётся только
в HttpOnly-cookie; CSRF-токен требуется для всех изменяющих запросов.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from database.training_db import get_training_connection

COOKIE_NAME = "chgk_session"
CSRF_HEADER = "x-csrf-token"
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
SESSION_HOURS = 12
REMEMBER_DAYS = 30

_fallback_secret = secrets.token_bytes(32)
_current_user: ContextVar["AuthUser | None"] = ContextVar(
    "chgk_current_user", default=None
)


@dataclass(frozen=True)
class AuthUser:
    id: int
    username: str
    display_name: str
    role: str
    csrf_token: str = ""


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not USERNAME_RE.fullmatch(normalized):
        raise ValueError(
            "Логин: 3–32 символа, только латиница, цифры, точка, дефис или _"
        )
    return normalized


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    if len(password) < 10:
        raise ValueError("Пароль должен содержать не менее 10 символов")
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=actual_salt,
        n=2**15,
        r=8,
        p=1,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    return digest, actual_salt


def verify_password(password: str, expected: bytes, salt: bytes) -> bool:
    try:
        actual, _ = hash_password(password, salt)
    except (ValueError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def authenticate(username: str, password: str) -> AuthUser | None:
    normalized = normalize_username(username)
    conn = get_training_connection()
    try:
        row = conn.execute(
            """
            SELECT id, username, display_name, password_hash, password_salt, role
            FROM users
            WHERE username = ? COLLATE NOCASE AND active = 1
            """,
            (normalized,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not verify_password(
        password, bytes(row["password_hash"]), bytes(row["password_salt"])
    ):
        return None
    return AuthUser(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
    )


def _secret() -> bytes:
    raw = os.environ.get("CHGK_SESSION_SECRET", "")
    if raw:
        return raw.encode("utf-8")
    return _fallback_secret


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def create_session(user: AuthUser, remember: bool) -> tuple[str, str, int]:
    max_age = REMEMBER_DAYS * 86400 if remember else SESSION_HOURS * 3600
    csrf = secrets.token_urlsafe(24)
    payload = {
        "v": 1,
        "uid": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "csrf": csrf,
        "exp": int(time.time()) + max_age,
    }
    encoded = _b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature = _b64encode(
        hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{encoded}.{signature}", csrf, max_age


def parse_session(token: str | None) -> AuthUser | None:
    if not token:
        return None
    try:
        encoded, signature = token.split(".", 1)
        expected = _b64encode(
            hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return None
        payload: dict[str, Any] = json.loads(_b64decode(encoded))
        if payload.get("v") != 1 or int(payload["exp"]) <= int(time.time()):
            return None
        return AuthUser(
            id=int(payload["uid"]),
            username=str(payload["username"]),
            display_name=str(payload["display_name"]),
            role=str(payload["role"]),
            csrf_token=str(payload["csrf"]),
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def session_from_request(request: Request) -> AuthUser | None:
    return parse_session(request.cookies.get(COOKIE_NAME))


def active_user(user: AuthUser) -> AuthUser | None:
    """Проверить, что подписанная сессия всё ещё принадлежит активному аккаунту."""
    conn = get_training_connection()
    try:
        row = conn.execute(
            """
            SELECT id, username, display_name, role
            FROM users
            WHERE id = ? AND username = ? COLLATE NOCASE AND active = 1
            """,
            (user.id, user.username),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return AuthUser(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
        csrf_token=user.csrf_token,
    )


def require_current_user() -> AuthUser:
    user = _current_user.get()
    if user is None:
        raise HTTPException(401, "Требуется войти")
    return user


def require_owner() -> AuthUser:
    """Разрешить действие только владельцу пространства.

    Проверка живёт на сервере: скрытая ссылка в интерфейсе не считается
    защитой, потому что любой адрес API можно вызвать напрямую.
    """
    user = require_current_user()
    if user.role != "owner":
        raise HTTPException(403, "Раздел доступен только владельцу")
    return user


def set_current_user(user: AuthUser):
    return _current_user.set(user)


def reset_current_user(token) -> None:
    _current_user.reset(token)


def cookie_secure() -> bool:
    return os.environ.get("CHGK_COOKIE_SECURE", "0").strip().lower() not in {
        "0",
        "false",
        "no",
    }
