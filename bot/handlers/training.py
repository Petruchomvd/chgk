"""/train — тренировка вопросами."""
from __future__ import annotations

import logging
from html import escape
from typing import Dict, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.training_engine import (
    TrainingSession,
    get_pack_by_id,
    record_and_advance,
    search_tournaments,
    session_summary,
    start_by_category,
    start_by_tournament,
    start_random,
    start_review,
    submit_answer,
)
from bot.keyboards import (
    after_finish,
    categories_menu,
    main_menu,
    reveal_keyboard,
    self_assessment,
    training_modes,
    tournaments_results,
)
from bot.states import TrainingFlow
from config import DB_PATH
from dashboard.db_queries import get_all_categories
from database.db import get_connection
from database.training_db import (
    count_due,
    get_training_connection,
)

router = Router()
log = logging.getLogger(__name__)

DEFAULT_COUNT = 10

# Сессии в памяти процесса. Для одного пользователя достаточно.
_sessions: Dict[int, TrainingSession] = {}


def _get_chgk_conn():
    return get_connection(DB_PATH)


def _get_session(user_id: int) -> Optional[TrainingSession]:
    return _sessions.get(user_id)


def _set_session(user_id: int, session: TrainingSession) -> None:
    _sessions[user_id] = session


def _clear_session(user_id: int) -> None:
    _sessions.pop(user_id, None)


# ── /train ────────────────────────────────────────────────────────


@router.message(Command("train"))
async def cmd_train(message: Message, state: FSMContext) -> None:
    await state.clear()
    _clear_session(message.from_user.id)

    tconn = get_training_connection()
    due = count_due(tconn)
    tconn.close()

    suffix = f" (повторений готово: {due})" if due else ""
    await message.answer(
        f"Выбери режим тренировки{suffix}:",
        reply_markup=training_modes(),
    )
    await state.set_state(TrainingFlow.choosing_mode)


@router.callback_query(F.data == "cmd:train")
async def cb_train(cb: CallbackQuery, state: FSMContext) -> None:
    await cmd_train(cb.message, state)
    await cb.answer()


# ── выбор режима ──────────────────────────────────────────────────


@router.callback_query(F.data.startswith("train_mode:"), TrainingFlow.choosing_mode)
async def cb_mode(cb: CallbackQuery, state: FSMContext) -> None:
    mode = cb.data.split(":", 1)[1]

    if mode == "cancel":
        await state.clear()
        await cb.message.edit_text("Отменено.")
        await cb.message.answer("Главное меню:", reply_markup=main_menu())
        await cb.answer()
        return

    if mode == "random":
        await _start_session_random(cb, state)
    elif mode == "category":
        await _ask_category(cb, state)
    elif mode == "tournament":
        await _ask_tournament(cb, state)
    elif mode == "review":
        await _start_session_review(cb, state)
    await cb.answer()


# ── рандом ────────────────────────────────────────────────────────


async def _start_session_random(cb: CallbackQuery, state: FSMContext) -> None:
    chgk_conn = _get_chgk_conn()
    session = start_random(chgk_conn, count=DEFAULT_COUNT)
    chgk_conn.close()

    if not session.questions:
        await cb.message.edit_text("Не нашлось подходящих вопросов.")
        await state.clear()
        return

    _set_session(cb.from_user.id, session)
    await cb.message.edit_text(f"Старт: {session.filters_repr}")
    await _show_question(cb.message, state, cb.from_user.id)


# ── категория ─────────────────────────────────────────────────────


async def _ask_category(cb: CallbackQuery, state: FSMContext) -> None:
    chgk_conn = _get_chgk_conn()
    cats = get_all_categories(chgk_conn)
    chgk_conn.close()
    await cb.message.edit_text(
        "Выбери категорию:", reply_markup=categories_menu(cats)
    )
    await state.set_state(TrainingFlow.choosing_category)


@router.callback_query(F.data.startswith("train_cat:"), TrainingFlow.choosing_category)
async def cb_category(cb: CallbackQuery, state: FSMContext) -> None:
    payload = cb.data.split(":", 1)[1]
    if payload == "cancel":
        await state.clear()
        await cb.message.edit_text("Отменено.")
        await cb.answer()
        return

    cat_id = int(payload)
    chgk_conn = _get_chgk_conn()
    session = start_by_category(chgk_conn, [cat_id], count=DEFAULT_COUNT)
    chgk_conn.close()

    if not session.questions:
        await cb.message.edit_text("Не нашлось вопросов в этой категории.")
        await state.clear()
        await cb.answer()
        return

    _set_session(cb.from_user.id, session)
    await cb.message.edit_text(f"Старт: {session.filters_repr}")
    await _show_question(cb.message, state, cb.from_user.id)
    await cb.answer()


# ── турнир ────────────────────────────────────────────────────────


async def _ask_tournament(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.edit_text(
        "Введи название турнира (часть) или его ID числом."
    )
    await state.set_state(TrainingFlow.entering_tournament_query)


@router.message(TrainingFlow.entering_tournament_query)
async def msg_tournament_query(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Пустой запрос. Попробуй ещё раз или /cancel.")
        return

    chgk_conn = _get_chgk_conn()

    # Если число — пробуем как pack_id
    if query.isdigit():
        pack = get_pack_by_id(chgk_conn, int(query))
        if pack and pack["questions_count"] > 0:
            chgk_conn.close()
            await _start_tournament_session(message, state, int(query))
            return
        await message.answer(f"Пак с ID {query} не найден или пуст.")
        chgk_conn.close()
        return

    # Иначе — поиск по названию
    packs = search_tournaments(chgk_conn, query, limit=10)
    chgk_conn.close()

    if not packs:
        await message.answer(
            "Ничего не нашлось. Попробуй другой запрос или /cancel."
        )
        return

    await message.answer(
        f"Найдено {len(packs)}, выбери:",
        reply_markup=tournaments_results(packs),
    )
    await state.set_state(TrainingFlow.choosing_tournament)


@router.callback_query(F.data.startswith("train_pack:"), TrainingFlow.choosing_tournament)
async def cb_tournament(cb: CallbackQuery, state: FSMContext) -> None:
    payload = cb.data.split(":", 1)[1]
    if payload == "cancel":
        await state.clear()
        await cb.message.edit_text("Отменено.")
        await cb.answer()
        return

    await _start_tournament_session(cb.message, state, int(payload), edit=True)
    await cb.answer()


async def _start_tournament_session(
    message: Message, state: FSMContext, pack_id: int, edit: bool = False
) -> None:
    chgk_conn = _get_chgk_conn()
    session = start_by_tournament(chgk_conn, pack_id, count=DEFAULT_COUNT)
    chgk_conn.close()

    if not session.questions:
        await message.answer("В этом турнире не оказалось вопросов.")
        await state.clear()
        return

    user_id = message.chat.id
    _set_session(user_id, session)
    text = f"Старт: {session.filters_repr}"
    if edit:
        await message.edit_text(text)
    else:
        await message.answer(text)
    await _show_question(message, state, user_id)


# ── повторения ────────────────────────────────────────────────────


async def _start_session_review(cb: CallbackQuery, state: FSMContext) -> None:
    chgk_conn = _get_chgk_conn()
    tconn = get_training_connection()
    session = start_review(chgk_conn, tconn, count=DEFAULT_COUNT)
    chgk_conn.close()
    tconn.close()

    if not session.questions:
        await cb.message.edit_text(
            "Очередь повторений пуста. Сначала прогони обычную тренировку."
        )
        await state.clear()
        return

    _set_session(cb.from_user.id, session)
    await cb.message.edit_text(f"Старт: {session.filters_repr}")
    await _show_question(cb.message, state, cb.from_user.id)


# ── показ вопроса ─────────────────────────────────────────────────


async def _show_question(message: Message, state: FSMContext, user_id: int) -> None:
    session = _get_session(user_id)
    if session is None or session.is_finished():
        await _show_summary(message, user_id)
        await state.clear()
        return

    q = session.current()
    text_parts = [f"<b>Вопрос {session.index + 1} / {session.total()}</b>\n"]
    cat = q.get("category")
    sub = q.get("subcategory")
    if cat:
        text_parts.append(f"<i>{cat}{(' → ' + sub) if sub else ''}</i>\n")
    if q.get("razdatka_text"):
        text_parts.append(f"<b>Раздатка:</b> {escape(q['razdatka_text'])}\n")
    text_parts.append("\n" + escape(q["text"]))

    full = "\n".join(text_parts)
    if len(full) > 4000:
        full = full[:3990] + "…"

    await message.answer(full, reply_markup=reveal_keyboard())
    await state.set_state(TrainingFlow.in_question)


# ── ввод ответа текстом или /reveal ───────────────────────────────


@router.message(TrainingFlow.in_question)
async def msg_user_answer(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    session = _get_session(user_id)
    if session is None:
        await message.answer("Сессия не найдена. /train для старта.")
        await state.clear()
        return
    submit_answer(session, message.text or "")
    await _reveal(message, state, user_id)


@router.callback_query(F.data == "quiz:reveal", TrainingFlow.in_question)
async def cb_reveal(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id
    session = _get_session(user_id)
    if session is None:
        await cb.message.answer("Сессия не найдена. /train для старта.")
        await state.clear()
        await cb.answer()
        return
    if not session.user_answer:
        submit_answer(session, "")
    await _reveal(cb.message, state, user_id)
    await cb.answer()


async def _reveal(message: Message, state: FSMContext, user_id: int) -> None:
    session = _get_session(user_id)
    q = session.current()

    parts = []
    if session.user_answer:
        parts.append(f"<b>Твой ответ:</b> «{escape(session.user_answer)}»\n")
    parts.append(f"<b>✅ Правильный ответ:</b> {escape(q['answer'])}")
    if q.get("zachet"):
        parts.append(f"<b>Зачёт:</b> {escape(q['zachet'])}")
    if q.get("nezachet"):
        parts.append(f"<b>Незачёт:</b> {escape(q['nezachet'])}")
    if q.get("comment"):
        parts.append(f"\n<b>Комментарий:</b>\n{escape(q['comment'])}")
    if q.get("source"):
        parts.append(f"\n<i>Источник: {escape(q['source'][:300])}</i>")

    msg = "\n".join(parts)
    if len(msg) > 4000:
        msg = msg[:3990] + "…"
    await message.answer(msg, reply_markup=self_assessment())
    await state.set_state(TrainingFlow.in_reveal)


# ── самооценка ────────────────────────────────────────────────────


@router.callback_query(F.data.in_({"quiz:knew", "quiz:didnt"}), TrainingFlow.in_reveal)
async def cb_self_assess(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id
    session = _get_session(user_id)
    if session is None:
        await cb.message.answer("Сессия не найдена.")
        await state.clear()
        await cb.answer()
        return

    knew = cb.data == "quiz:knew"
    tconn = get_training_connection()
    has_next = record_and_advance(session, tconn, knew)
    tconn.close()

    await cb.answer("Записано ✅" if knew else "Записано ❌")

    if has_next:
        await _show_question(cb.message, state, user_id)
    else:
        await _show_summary(cb.message, user_id)
        await state.clear()


# ── прервать ──────────────────────────────────────────────────────


@router.callback_query(F.data == "quiz:abort")
async def cb_abort(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id
    if _get_session(user_id):
        await _show_summary(cb.message, user_id)
    _clear_session(user_id)
    await state.clear()
    await cb.answer("Прервано")


# ── итоги ─────────────────────────────────────────────────────────


async def _show_summary(message: Message, user_id: int) -> None:
    session = _get_session(user_id)
    if session is None or not session.results:
        await message.answer("Нет ответов для отчёта.", reply_markup=main_menu())
        _clear_session(user_id)
        return
    s = session_summary(session)
    lines = [
        "<b>📊 Итоги тренировки</b>",
        f"Режим: {s['filters_repr']}",
        f"Результат: <b>{s['correct']}/{s['total']} ({s['pct']}%)</b>",
        f"Среднее время: {s['avg_time']:.1f}с",
    ]
    if s["by_category"]:
        lines.append("\nПо категориям:")
        for cat, d in sorted(s["by_category"].items()):
            t = d["total"]
            c = d["correct"]
            p = round(100 * c / t) if t else 0
            lines.append(f"  • {cat}: {c}/{t} ({p}%)")
    await message.answer("\n".join(lines), reply_markup=after_finish())
    _clear_session(user_id)
