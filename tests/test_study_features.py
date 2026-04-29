import asyncio
import sqlite3

import pytest

from app import study_engine
from bot.handlers import study as study_handler


def test_list_studies_reads_title_and_question_count(tmp_path, monkeypatch):
    monkeypatch.setattr(study_engine, "STUDIES_DIR", tmp_path)
    (tmp_path / "magritt.md").write_text(
        "# Магритт\n\n"
        "*Сгенерировано на основе 25 вопросов из базы. Модель: openai/gpt-4o-mini*\n\n"
        "---\n\n"
        "Текст статьи",
        encoding="utf-8",
    )

    items = study_engine.list_studies()

    assert len(items) == 1
    assert items[0]["slug"] == "magritt"
    assert items[0]["title"] == "Магритт"
    assert items[0]["questions_used"] == 25


def test_generate_article_raises_when_no_questions(monkeypatch):
    monkeypatch.setattr(study_engine, "find_questions_about", lambda *args, **kwargs: [])

    with pytest.raises(study_engine.StudyQuestionsNotFound):
        study_engine.generate_article(sqlite3.connect(":memory:"), "Исследования", save=False)


def test_msg_topic_routes_studies_command_to_list(monkeypatch):
    calls = []

    class DummyState:
        def __init__(self):
            self.cleared = 0

        async def clear(self):
            self.cleared += 1

    class DummyMessage:
        text = "/studies"

    async def fake_cmd_studies(message):
        calls.append(message.text)

    monkeypatch.setattr(study_handler, "cmd_studies", fake_cmd_studies)

    state = DummyState()
    message = DummyMessage()

    asyncio.run(study_handler.msg_topic(message, state))

    assert state.cleared == 1
    assert calls == ["/studies"]
