from app.training_engine import TrainingSession
from bot.handlers import training


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
