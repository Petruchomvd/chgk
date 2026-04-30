"""Telegram bot entrypoint for CHGK training."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Mapping
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, CallbackQuery, Message

import config  # loads .env  # noqa: F401
from bot.handlers import common, study, training

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("chgk_bot")


def _parse_id_list(raw: str) -> set[int]:
    ids: set[int] = set()
    normalized = raw.replace(";", ",").replace("\n", ",")
    for part in normalized.split(","):
        token = part.strip()
        if token.isdigit():
            ids.add(int(token))
    return ids


def parse_allowed_user_ids(env: Mapping[str, str] | None = None) -> set[int]:
    env = env or os.environ
    allowed = _parse_id_list(env.get("CHGK_BOT_ALLOWED_TG_IDS", ""))

    owner_raw = env.get("CHGK_BOT_OWNER_TG_ID", "").strip()
    if owner_raw.isdigit():
        allowed.add(int(owner_raw))

    return allowed


def _event_user_and_chat_type(
    event: Message | CallbackQuery,
) -> tuple[Optional[int], Optional[str], Optional[str]]:
    if isinstance(event, Message):
        user = event.from_user
        return (
            getattr(user, "id", None),
            getattr(user, "username", None),
            getattr(event.chat, "type", None),
        )

    user = event.from_user
    chat = getattr(event.message, "chat", None)
    return (
        getattr(user, "id", None),
        getattr(user, "username", None),
        getattr(chat, "type", None),
    )


def _make_access_filter(allowed_user_ids: set[int] | None):
    async def _middleware(handler, event, data):
        user_id, username, chat_type = _event_user_and_chat_type(event)

        if chat_type is not None and chat_type != ChatType.PRIVATE:
            log.warning(
                "blocked non-private chat: chat_type=%s user_id=%s username=%s",
                chat_type,
                user_id,
                username,
            )
            if isinstance(event, Message):
                await event.answer(
                    "Бот работает только в личных сообщениях. Открой диалог с ботом и напиши /start."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("Бот работает только в личке.", show_alert=True)
            return

        if allowed_user_ids and (user_id is None or user_id not in allowed_user_ids):
            log.warning(
                "blocked by allowlist: user_id=%s username=%s allowed=%s",
                user_id,
                username,
                sorted(allowed_user_ids),
            )
            if isinstance(event, Message):
                await event.answer(
                    "Доступ к этому боту ограничен. Передай организатору свой Telegram ID: "
                    f"<code>{user_id if user_id is not None else '???'}</code>."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ к боту ограничен.", show_alert=True)
            return

        return await handler(event, data)

    return _middleware


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="train", description="Тренировка вопросами"),
        BotCommand(command="study", description="Статья по теме (например, /study Магритт)"),
        BotCommand(command="studies", description="Список сохранённых статей"),
        BotCommand(command="stats", description="Твой прогресс"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
        BotCommand(command="help", description="Помощь"),
    ])


async def main() -> None:
    token = os.environ.get("TG_DIGEST_BOT_TOKEN") or os.environ.get("CHGK_BOT_TOKEN", "")
    if not token:
        log.error("Bot token is missing: set TG_DIGEST_BOT_TOKEN or CHGK_BOT_TOKEN in .env")
        sys.exit(1)

    allowed_user_ids = parse_allowed_user_ids()
    if allowed_user_ids:
        log.info("Allowlist enabled for %s Telegram users", len(allowed_user_ids))
    else:
        log.warning("No Telegram allowlist configured - bot will accept all private chats.")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(common.router)
    dp.include_router(training.router)
    dp.include_router(study.router)

    access_filter = _make_access_filter(allowed_user_ids or None)
    dp.message.middleware(access_filter)
    dp.callback_query.middleware(access_filter)

    await _set_commands(bot)

    me = await bot.get_me()
    log.info("Bot @%s started (id=%s)", me.username, me.id)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
