"""/start, /help, /stats, /menu, /cancel."""
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import main_menu
from database.training_db import get_stats, get_training_connection

router = Router()


HELP_TEXT = (
    "<b>Что я умею:</b>\n\n"
    "🎯 /train — тренировка вопросами:\n"
    "   • случайные / по категории / по турниру / повторения\n"
    "📚 /study &lt;тема&gt; — обучающая статья по теме (по вопросам из базы)\n"
    "   например: /study Магритт\n"
    "📚 /studies — список ранее сгенерированных статей\n"
    "📊 /stats — твой прогресс\n"
    "🏠 /menu — главное меню\n"
    "❌ /cancel — отменить текущее действие"
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    await message.answer(
        f"Привет, {user.first_name}! Я тренажёр для подготовки к ЧГК-турнирам.\n\n"
        f"Твой Telegram ID: <code>{user.id}</code>\n\n" + HELP_TEXT,
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Текущее действие отменено.", reply_markup=main_menu())


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    await _send_stats(message)


@router.callback_query(F.data == "cmd:stats")
async def cb_stats(cb: CallbackQuery) -> None:
    await _send_stats(cb.message)
    await cb.answer()


@router.callback_query(F.data == "cmd:menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.answer("Главное меню:", reply_markup=main_menu())
    await cb.answer()


async def _send_stats(message: Message) -> None:
    tconn = get_training_connection()
    s = get_stats(tconn)
    tconn.close()

    if s["total_attempts"] == 0:
        await message.answer(
            "Пока статистики нет — начни тренировку командой /train.",
            reply_markup=main_menu(),
        )
        return

    pct = round(100 * s["correct_attempts"] / s["total_attempts"]) if s["total_attempts"] else 0

    lines = [
        "<b>📊 Твой прогресс</b>\n",
        f"• Всего ответов: <b>{s['total_attempts']}</b>",
        f"• Правильных: <b>{s['correct_attempts']}</b> ({pct}%)",
        f"• Уникальных вопросов: <b>{s['distinct_questions']}</b>",
        f"• Сейчас пора повторить: <b>{s['due_now']}</b>",
    ]
    if s["by_category"]:
        lines.append("\n<b>По категориям:</b>")
        for r in s["by_category"][:14]:
            cat = r["category"]
            t = r["total"]
            k = r["knew"] or 0
            p = round(100 * k / t) if t else 0
            lines.append(f"  {cat}: {k}/{t} ({p}%)")
    if s["by_box"]:
        lines.append("\n<b>Leitner-коробки:</b>")
        for r in s["by_box"]:
            lines.append(f"  Box {r['box']}: {r['c']} вопр.")

    await message.answer("\n".join(lines), reply_markup=main_menu())
