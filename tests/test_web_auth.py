from __future__ import annotations

import sqlite3
import time
from datetime import datetime

from fastapi.testclient import TestClient

from api import auth
from database.training_db import get_training_connection


def test_password_hash_is_salted_and_verifiable():
    digest_a, salt_a = auth.hash_password("long-enough password")
    digest_b, salt_b = auth.hash_password("long-enough password")

    assert digest_a != digest_b
    assert salt_a != salt_b
    assert auth.verify_password("long-enough password", digest_a, salt_a)
    assert not auth.verify_password("wrong password", digest_a, salt_a)


def test_signed_session_rejects_tampering(monkeypatch):
    monkeypatch.setenv("CHGK_SESSION_SECRET", "test-secret-that-is-not-production")
    user = auth.AuthUser(42, "matvey", "Матвей", "owner")

    token, csrf, max_age = auth.create_session(user, remember=True)
    parsed = auth.parse_session(token)

    assert parsed is not None
    assert parsed.id == 42
    assert parsed.csrf_token == csrf
    assert max_age == auth.REMEMBER_DAYS * 86400
    assert auth.parse_session(token + "x") is None


def test_signed_session_expires(monkeypatch):
    monkeypatch.setenv("CHGK_SESSION_SECRET", "test-secret-that-is-not-production")
    user = auth.AuthUser(42, "matvey", "Матвей", "owner")
    token, _, _ = auth.create_session(user, remember=False)

    monkeypatch.setattr(time, "time", lambda: 10**12)
    assert auth.parse_session(token) is None


def test_training_schema_contains_users(tmp_path):
    conn = get_training_connection(tmp_path / "training.db")
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"users", "user_identities"} <= tables
    finally:
        conn.close()


def test_login_cookie_and_csrf_protect_api(tmp_path, monkeypatch):
    from api import main

    db_path = tmp_path / "training.db"

    def connection():
        return get_training_connection(db_path)

    monkeypatch.setattr(auth, "get_training_connection", connection)
    monkeypatch.setenv("CHGK_SESSION_SECRET", "integration-test-secret")
    monkeypatch.setenv("CHGK_COOKIE_SECURE", "0")

    digest, salt = auth.hash_password("correct horse battery")
    player_digest, player_salt = auth.hash_password("player password 123")
    now = datetime.now().isoformat(timespec="seconds")
    conn = connection()
    try:
        conn.execute(
            """
            INSERT INTO users (
                id, username, display_name, password_hash, password_salt,
                role, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (42, "matvey", "Матвей", digest, salt, "owner", now, now),
        )
        conn.execute(
            """
            INSERT INTO users (
                id, username, display_name, password_hash, password_salt,
                role, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                43,
                "player",
                "Игрок",
                player_digest,
                player_salt,
                "player",
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with TestClient(main.app) as client:
        assert client.post("/api/auth/logout").status_code == 401

        login = client.post(
            "/api/auth/login",
            json={
                "username": "matvey",
                "password": "correct horse battery",
                "remember": True,
            },
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        assert csrf
        assert login.json()["user"]["id"] == 42

        assert client.get("/api/auth/session").status_code == 200
        assert client.post("/api/auth/logout").status_code == 403
        assert (
            client.post(
                "/api/auth/logout", headers={"X-CSRF-Token": csrf}
            ).status_code
            == 200
        )
        assert client.get("/api/auth/session").status_code == 401

        player_login = client.post(
            "/api/auth/login",
            json={
                "username": "player",
                "password": "player password 123",
                "remember": False,
            },
        )
        assert player_login.status_code == 200
        assert player_login.json()["user"]["role"] == "player"
        assert client.get("/api/team/dossier").status_code == 403
        assert client.get("/api/team/forecast?pack=1").status_code == 403
        assert client.get("/api/semantic/map").status_code == 403
        assert client.get("/api/search/semantic?q=наполеон").status_code == 403
