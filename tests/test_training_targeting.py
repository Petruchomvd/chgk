"""Тесты прицельной тренировки: слой беручести и слабые темы.

Обе вещи появились после разбора: приложение фильтровало по пакетному trueDL
вместо вопросной беручести, а «повторение» показывало тот же вопрос ещё раз.
"""

import sqlite3

import pytest

from app import training_engine as engine
from dashboard.training_queries import get_training_questions_by_category
from database.db import get_connection, insert_questions, upsert_pack
from database.training_db import (
    get_seen_question_ids,
    get_training_connection,
    record_attempt,
)

USER = 1


@pytest.fixture
def chgk(tmp_path):
    conn = get_connection(tmp_path / "chgk.db")
    upsert_pack(conn, {"id": 1, "title": "Тестовый пакет", "difficulty": 2.0})

    # Категория и подкатегория для тематической выборки
    conn.execute("INSERT INTO categories (id, name, name_ru) VALUES (7, 'cinema', 'Кино и театр')")
    conn.execute(
        "INSERT INTO subcategories (id, category_id, name, name_ru)"
        " VALUES (25, 7, 'film', 'Кинематограф')"
    )

    # Вопросы с разной беручестью. q.difficulty = 10 × (1 − доля взявших):
    # 1.0 — взяли 90% команд, 5.0 — половина, 9.0 — почти никто.
    questions = [
        {"id": 100 + i, "pack_id": 1, "number": i, "text": f"Вопрос {i}", "answer": "О"}
        for i in range(1, 10)
    ]
    insert_questions(conn, questions)
    for i, diff in enumerate([1.0, 1.0, 5.0, 5.0, 5.0, 9.0, 9.0, 5.0, 5.0], start=1):
        conn.execute("UPDATE questions SET difficulty = ? WHERE id = ?", (diff, 100 + i))
        conn.execute(
            "INSERT INTO question_topics (question_id, subcategory_id, method, model_name)"
            " VALUES (?, 25, 'test', 'test')",
            (100 + i,),
        )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def training(tmp_path, monkeypatch):
    import database.training_db as tdb

    monkeypatch.setattr(tdb, "TRAINING_DB_PATH", tmp_path / "training.db")
    conn = get_training_connection(tmp_path / "training.db")
    yield conn
    conn.close()


# --- Слой беручести ---


def test_difficulty_filter_uses_question_take_rate(chgk):
    """Фильтр должен смотреть на беручесть вопроса, а не на trueDL пакета.

    Регрессия: раньше фильтровалось по p.difficulty — рейтинговой сложности
    пакета с поправкой на силу команд. У всего пакета она одна, поэтому выбрать
    «вопросы, которые берут все» было невозможно.
    """
    medium = engine.start_random(chgk, count=50, seed=1, difficulty_range=(3.0, 6.0))
    got = {q["id"] for q in medium.questions}
    assert got == {103, 104, 105, 108, 109}

    easy = engine.start_random(chgk, count=50, seed=1, difficulty_range=(0.0, 1.5))
    assert {q["id"] for q in easy.questions} == {101, 102}


def test_pack_difficulty_no_longer_leaks_into_filter(chgk):
    """У пакета trueDL = 2.0, но по слою «гробы» обязаны найтись только гробы."""
    brutal = engine.start_random(chgk, count=50, seed=1, difficulty_range=(8.5, 10.0))
    assert {q["id"] for q in brutal.questions} == {106, 107}


# --- Слабые темы: новые вопросы, а не повтор старых ---


def test_weak_topics_serve_only_unseen_questions(chgk, training):
    """Тему подтягивают новыми вопросами.

    Вопрос ЧГК одноразовый: показав его второй раз, мы проверим память об этом
    вопросе, а не знание темы.
    """
    first = engine.start_weak_topics(chgk, training, USER, [7], count=3, seed=1)
    seen_ids = [q["id"] for q in first.questions]
    assert len(seen_ids) == 3

    for qid in seen_ids:
        record_attempt(training, USER, qid, False, "", 1.0, "weak", None)

    again = engine.start_weak_topics(chgk, training, USER, [7], count=50, seed=1)
    got = {q["id"] for q in again.questions}
    assert got, "остальные вопросы темы должны остаться доступны"
    assert not (got & set(seen_ids)), "уже показанные вопросы не должны повторяться"


def test_weak_topics_respect_difficulty_layer(chgk, training):
    """Слабая тема + слой беручести должны работать вместе."""
    s = engine.start_weak_topics(
        chgk, training, USER, [7], count=50, seed=1, difficulty_range=(8.5, 10.0)
    )
    assert {q["id"] for q in s.questions} == {106, 107}


def test_weak_topics_exhausted_returns_empty(chgk, training):
    """Когда новых вопросов по теме не осталось, режим честно отдаёт пусто."""
    everything = engine.start_weak_topics(chgk, training, USER, [7], count=50, seed=1)
    for q in everything.questions:
        record_attempt(training, USER, q["id"], True, "", 1.0, "weak", None)

    empty = engine.start_weak_topics(chgk, training, USER, [7], count=10, seed=1)
    assert empty.questions == []


def test_seen_ids_tracks_attempts(chgk, training):
    assert get_seen_question_ids(training, USER) == set()
    record_attempt(training, USER, 101, True, "", 1.0, "random", None)
    record_attempt(training, USER, 102, False, "", 1.0, "random", None)
    assert get_seen_question_ids(training, USER) == {101, 102}


def test_exclude_ids_is_honoured_directly(chgk):
    """Нижний уровень: exclude_ids не должен молча игнорироваться."""
    all_q = get_training_questions_by_category(chgk, [7], limit=50, seed=1)
    assert len(all_q) == 9
    subset = get_training_questions_by_category(
        chgk, [7], limit=50, seed=1, exclude_ids={101, 102, 103}
    )
    ids = {q["id"] for q in subset}
    assert len(ids) == 6
    assert not (ids & {101, 102, 103})
