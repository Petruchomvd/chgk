import sqlite3

from app.training_engine import (
    get_pack_tours,
    get_recent_tournaments,
    search_tournaments,
    start_by_tournament,
)
from bot.handlers import training


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE packs (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            difficulty REAL,
            link TEXT
        );
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY,
            pack_id INTEGER NOT NULL,
            tour_number INTEGER,
            number INTEGER,
            text TEXT NOT NULL,
            answer TEXT NOT NULL,
            zachet TEXT,
            nezachet TEXT,
            comment TEXT,
            source TEXT,
            authors TEXT,
            razdatka_text TEXT,
            razdatka_pic TEXT
        );
        CREATE TABLE question_topics (
            question_id INTEGER,
            subcategory_id INTEGER,
            confidence REAL,
            model_name TEXT
        );
        CREATE TABLE subcategories (
            id INTEGER PRIMARY KEY,
            category_id INTEGER,
            name_ru TEXT,
            sort_order INTEGER
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY,
            name_ru TEXT,
            sort_order INTEGER
        );
        """
    )
    return conn


def test_default_training_session_length_is_12():
    assert training.DEFAULT_COUNT == 12


def test_search_tournaments_prefers_exact_unicode_match():
    conn = _make_conn()
    try:
        conn.executemany(
            "INSERT INTO packs(id, title, difficulty, link) VALUES (?, ?, ?, ?)",
            [
                (1, "Балрог", 4.0, None),
                (2, "Балрог-2", 4.0, None),
                (3, "Супер Балрог", 4.0, None),
            ],
        )
        conn.executemany(
            "INSERT INTO questions(id, pack_id, tour_number, number, text, answer) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (11, 1, 1, 1, "Q1", "A1"),
                (21, 2, 1, 1, "Q2", "A2"),
                (31, 3, 1, 1, "Q3", "A3"),
            ],
        )

        results = search_tournaments(conn, "балрог", limit=10)
    finally:
        conn.close()

    assert [pack["title"] for pack in results[:3]] == [
        "Балрог",
        "Балрог-2",
        "Супер Балрог",
    ]


def test_get_recent_tournaments_returns_latest_by_id():
    conn = _make_conn()
    try:
        conn.executemany(
            "INSERT INTO packs(id, title, difficulty, link) VALUES (?, ?, ?, ?)",
            [
                (1, "Старый", 4.0, None),
                (5, "Новый", 4.0, None),
                (3, "Средний", 4.0, None),
            ],
        )
        conn.executemany(
            "INSERT INTO questions(id, pack_id, tour_number, number, text, answer) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (11, 1, 1, 1, "Q1", "A1"),
                (31, 3, 1, 1, "Q2", "A2"),
                (51, 5, 1, 1, "Q3", "A3"),
            ],
        )

        results = get_recent_tournaments(conn, limit=3)
    finally:
        conn.close()

    assert [pack["id"] for pack in results] == [5, 3, 1]


def test_get_pack_tours_groups_questions_by_tour():
    conn = _make_conn()
    try:
        conn.execute(
            "INSERT INTO packs(id, title, difficulty, link) VALUES (?, ?, ?, ?)",
            (1, "Турнир", 4.0, None),
        )
        conn.executemany(
            "INSERT INTO questions(id, pack_id, tour_number, number, text, answer) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (101, 1, 1, 1, "Q1", "A1"),
                (102, 1, 1, 2, "Q2", "A2"),
                (201, 1, 2, 1, "Q3", "A3"),
            ],
        )

        tours = get_pack_tours(conn, 1)
    finally:
        conn.close()

    assert tours == [
        {"tour_number": 1, "questions_count": 2},
        {"tour_number": 2, "questions_count": 1},
    ]


def test_start_by_tournament_uses_selected_tour_in_order_and_caps_to_12():
    conn = _make_conn()
    try:
        conn.execute(
            "INSERT INTO packs(id, title, difficulty, link) VALUES (?, ?, ?, ?)",
            (1, "Большой турнир", 4.0, None),
        )
        conn.executemany(
            "INSERT INTO questions(id, pack_id, tour_number, number, text, answer) VALUES (?, ?, ?, ?, ?, ?)",
            [(100 + i, 1, 1, i, f"Q{i}", f"A{i}") for i in range(1, 13)]
            + [(200 + i, 1, 2, i, f"T2-Q{i}", f"T2-A{i}") for i in range(1, 15)],
        )

        session = start_by_tournament(conn, 1, count=12, tour_number=2)
    finally:
        conn.close()

    assert [q["id"] for q in session.questions] == [201 + i for i in range(12)]
    assert session.total() == 12
    assert "тур 2" in session.filters_repr
