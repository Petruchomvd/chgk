from app.training_engine import TrainingSession, session_summary
from bot.handlers import training
from database.training_db import (
    UNCATEGORIZED_LABEL,
    get_stats,
    get_training_connection,
    record_attempt,
)


def test_build_question_text_hides_category_but_keeps_razdatka():
    session = TrainingSession(mode="category", questions=[{} for _ in range(10)])
    session.index = 2
    question = {
        "text": "Какой ответ?",
        "category": "История",
        "subcategory": "Военная история",
        "razdatka_text": "Фрагмент изображения",
    }

    text = training._build_question_text(session, question)

    assert "Вопрос 3 / 10" in text
    assert "Какой ответ?" in text
    assert "Раздатка:" in text
    assert "Фрагмент изображения" in text
    assert "История" not in text
    assert "Военная история" not in text


def test_razdatka_pic_url_supports_relative_and_absolute_urls():
    assert training._razdatka_pic_url("https://example.com/image.png") == "https://example.com/image.png"
    assert training._razdatka_pic_url("/uploads/image.png").endswith("/uploads/image.png")
    assert training._razdatka_pic_url(None) is None


def test_session_summary_labels_uncategorized_questions():
    session = TrainingSession(mode="random")
    session.results = [
        {
            "question_id": 1,
            "user_answer": "версии нет",
            "correct_answer": "ответ",
            "knew": False,
            "time_seconds": 23.6,
            "category": None,
        }
    ]

    summary = session_summary(session)

    assert summary["by_category"] == {
        UNCATEGORIZED_LABEL: {"total": 1, "correct": 0}
    }


def test_get_stats_groups_null_category_as_uncategorized(tmp_path):
    conn = get_training_connection(tmp_path / "training.db")
    try:
        record_attempt(
            conn,
            user_id=1,
            question_id=1,
            knew=False,
            user_answer="",
            time_seconds=12.3,
            mode="random",
            category=None,
        )
        stats = get_stats(conn, 1)
    finally:
        conn.close()

    assert stats["by_category"] == [
        {"category": UNCATEGORIZED_LABEL, "total": 1, "knew": 0}
    ]
