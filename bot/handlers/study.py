"""/study <topic> - generation or display of a study article."""
from __future__ import annotations

import asyncio
import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.study_engine import (
    StudyQuestionsNotFound,
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

TG_LIMIT = 4000  # safe margin from Telegram 4096-char limit
STUDY_CMD_RE = re.compile(r"^/study(?:@[A-Za-z0-9_]+)?(?:\s|$)", re.IGNORECASE)
STUDIES_CMD_RE = re.compile(r"^/studies(?:@[A-Za-z0-9_]+)?$", re.IGNORECASE)


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


def _is_studies_command(text: str) -> bool:
    return bool(STUDIES_CMD_RE.fullmatch(text.strip()))


def _render_studies_list(items: list[dict]) -> str:
    lines = ["<b>📚 Готовые статьи</b>", ""]
    for idx, item in enumerate(items[:30], 1):
        title = escape(item.get("title") or item["slug"])
        slug = escape(item["slug"])
        used = item.get("questions_used")
        suffix = f" — {used} вопр." if used is not None else ""
        lines.append(f"{idx}. {title}{suffix}")
        lines.append(f"<code>/study {slug}</code>")
    lines.append("")
    lines.append("Открыть статью можно командой из списка.")
    return "\n".join(lines)


@router.message(F.text.regexp(STUDIES_CMD_RE.pattern), Command("studies"))
async def cmd_studies(message: Message) -> None:
    items = list_studies()
    if not items:
        await message.answer(
            "Сохранённых статей пока нет. Сделай /study &lt;тема&gt;.",
            reply_markup=main_menu(),
        )
        return
    await message.answer(_render_studies_list(items), reply_markup=main_menu())


@router.message(F.text.regexp(STUDY_CMD_RE.pattern), Command("study"))
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
    if not topic:
        await state.clear()
        await message.answer("Пустая тема. /cancel чтобы выйти.")
        return
    if _is_studies_command(topic):
        await state.clear()
        await cmd_studies(message)
        return
    if topic.startswith("/"):
        await state.clear()
        await message.answer(
            "Жду обычное название темы без /-команды. Например: <code>Магритт</code>.",
            reply_markup=main_menu(),
        )
        return
    await state.clear()
    await _study(message, topic)


@router.callback_query(F.data == "cmd:study")
async def cb_study(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.answer(
        "Какая тема? Пришли одним сообщением, например: <code>Магритт</code>"
    )
    await state.set_state(StudyFlow.entering_topic)
    await cb.answer()


async def _study(message: Message, topic: str) -> None:
    topic = topic.strip()

    cached = find_existing(topic)
    if cached:
        text = read_existing(topic) or ""
        await message.answer(f"📖 Найдена сохранённая статья по теме «{escape(topic)}»:")
        for chunk in _split(text):
            await message.answer(chunk)
        return

    status = await message.answer(
        f"⏳ Генерирую статью по теме «{escape(topic)}»… (10-30 сек)"
    )

    def _work():
        # Create and close SQLite connection inside the worker thread.
        conn = get_connection(DB_PATH)
        try:
            return generate_article(conn, topic)
        finally:
            conn.close()

    try:
        result = await asyncio.to_thread(_work)
    except StudyQuestionsNotFound:
        await status.edit_text(
            "Не нашёл в базе вопросов по этой теме. Попробуй более конкретную тему."
        )
        return
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
