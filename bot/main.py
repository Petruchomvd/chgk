"""Точка входа Telegram-бота для персональной подготовки к ЧГК.

Запуск:
    python -m bot.main

Требуется в .env:
    TG_DIGEST_BOT_TOKEN=<токен>
    CHGK_BOT_OWNER_TG_ID=<твой Telegram ID>     # узнаётся из /start

Если CHGK_BOT_OWNER_TG_ID не задан — бот будет логировать ID любого, кто
напишет, но всё равно ответит. Это нормальный режим для первого знакомства.
После того, как ID известен, добавь его в .env и перезапусти.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

import config  # подгружает .env  # noqa: F401
from bot.handlers import common, study, training

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("chgk_bot")


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="train", description="Тренировка вопросами"),
        BotCommand(command="study", description="Статья по теме (например, /study Магритт)"),
        BotCommand(command="studies", description="Список сохранённых статей"),
        BotCommand(command="stats", description="Прогресс"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
        BotCommand(command="help", description="Помощь"),
    ])


def _make_owner_filter(owner_id: int):
    """Возвращает middleware, отфильтровывающее всех, кроме владельца."""
    async def _middleware(handler, event, data):
        from aiogram.types import CallbackQuery, Message
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user
        if user is None or user.id != owner_id:
            log.warning(
                "blocked: user_id=%s username=%s — not the owner (%s)",
                getattr(user, "id", None),
                getattr(user, "username", None),
                owner_id,
            )
            if isinstance(event, Message):
                await event.answer(
                    f"Этот бот — личный. Если он твой, добавь свой ID "
                    f"<code>{user.id if user else '???'}</code> в .env как "
                    f"CHGK_BOT_OWNER_TG_ID и перезапусти."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("Личный бот", show_alert=True)
            return
        return await handler(event, data)
    return _middleware


async def main() -> None:
    token = os.environ.get("TG_DIGEST_BOT_TOKEN") or os.environ.get("CHGK_BOT_TOKEN", "")
    if not token:
        log.error("Не найден токен бота: ожидается TG_DIGEST_BOT_TOKEN или CHGK_BOT_TOKEN в .env")
        sys.exit(1)

    owner_raw = os.environ.get("CHGK_BOT_OWNER_TG_ID", "").strip()
    owner_id = int(owner_raw) if owner_raw.isdigit() else None
    if owner_id is None:
        log.warning(
            "CHGK_BOT_OWNER_TG_ID не задан — бот пустит ВСЕХ. "
            "Добавь свой ID в .env после первого /start."
        )

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(common.router)
    dp.include_router(training.router)
    dp.include_router(study.router)

    if owner_id is not None:
        dp.message.middleware(_make_owner_filter(owner_id))
        dp.callback_query.middleware(_make_owner_filter(owner_id))

    await _set_commands(bot)

    me = await bot.get_me()
    log.info("Bot @%s started (id=%s)", me.username, me.id)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
