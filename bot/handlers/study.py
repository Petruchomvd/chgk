"""/study <тема> — генерация или показ обучающей статьи."""
from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.study_engine import (
    find_existing,
    generate_article,
    list_studies,
    read_existing,
)
from bot.keyboards import main_menu
from bot.states import StudyFlow
from config import DB_PATH
from database.db import get_connection

router = Router()
log = logging.getLogger(__name__)

TG_LIMIT = 4000  # запас от 4096


def _split(text: str) -> list[str]:
    chunks = []
    while len(text) > TG_LIMIT:
        cut = text.rfind("\n", 0, TG_LIMIT)
        if cut < TG_LIMIT // 2:
            cut = TG_LIMIT
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


@router.message(Command("study"))
async def cmd_study(message: Message, command: CommandObject, state: FSMContext) -> None:
    topic = (command.args or "").strip()
    if not topic:
        await message.answer(
            "Укажи тему: <code>/study Магритт</code>\n"
            "или просто пришли тему следующим сообщением."
        )
        await state.set_state(StudyFlow.entering_topic)
        return
    await _study(message, topic)


@router.message(StudyFlow.entering_topic)
async def msg_topic(message: Message, state: FSMContext) -> None:
    topic = (message.text or "").strip()
    await state.clear()
    if not topic:
        await message.answer("Пустая тема. /cancel чтобы выйти.")
        return
    await _study(message, topic)


@router.callback_query(F.data == "cmd:study")
async def cb_study(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.answer(
        "Какая тема? Пришли одним сообщением, например: <code>Магритт</code>"
    )
    await state.set_state(StudyFlow.entering_topic)
    await cb.answer()


async def _study(message: Message, topic: str) -> None:
    cached = find_existing(topic)
    if cached:
        text = read_existing(topic) or ""
        await message.answer(f"📖 Найдена сохранённая статья по теме «{escape(topic)}»:")
        for chunk in _split(text):
            await message.answer(chunk)
        return

    status = await message.answer(
        f"⏳ Генерирую статью по теме «{escape(topic)}»… (10–30 сек)"
    )

    def _work():
        # Соединение создаём и закрываем внутри потока:
        # SQLite не пускает курсоры между потоками.
        conn = get_connection(DB_PATH)
        try:
            return generate_article(conn, topic)
        finally:
            conn.close()

    try:
        result = await asyncio.to_thread(_work)
    except Exception as e:
        log.exception("generate_article failed")
        await status.edit_text(f"❌ Ошибка генерации: {e}")
        return

    cost_str = f"≈${result['cost']:.4f}" if result["cost"] else ""
    await status.edit_text(
        f"✅ Готово. Использовано вопросов из базы: {result['questions_used']}. {cost_str}"
    )

    full = (
        f"# {result['topic']}\n\n"
        f"_На основе {result['questions_used']} вопросов из базы._\n\n"
        f"---\n\n{result['content']}"
    )
    for chunk in _split(full):
        await message.answer(chunk)


@router.message(Command("studies"))
async def cmd_studies(message: Message) -> None:
    items = list_studies()
    if not items:
        await message.answer("Сохранённых статей пока нет. Сделай /study &lt;тема&gt;.")
        return
    lines = ["<b>📚 Твои статьи:</b>\n"]
    for it in items[:30]:
        lines.append(f"• /study {escape(it['slug'])}")
    await message.answer("\n".join(lines), reply_markup=main_menu())
