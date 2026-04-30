"""Inline-клавиатуры бота."""
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Тренировка", callback_data="cmd:train")],
        [InlineKeyboardButton(text="📚 Знания", callback_data="cmd:study")],
        [InlineKeyboardButton(text="📊 Прогресс", callback_data="cmd:stats")],
    ])


def training_modes() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Случайные", callback_data="train_mode:random")],
        [InlineKeyboardButton(text="🏷 По категории", callback_data="train_mode:category")],
        [InlineKeyboardButton(text="🏆 По турниру", callback_data="train_mode:tournament")],
        [InlineKeyboardButton(text="🔁 Очередь повторений", callback_data="train_mode:review")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="train_mode:cancel")],
    ])


def categories_menu(categories: List[dict]) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for cat in categories:
        row.append(InlineKeyboardButton(
            text=cat["name_ru"], callback_data=f"train_cat:{cat['id']}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="train_cat:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tournaments_results(packs: List[dict]) -> InlineKeyboardMarkup:
    rows = []
    for p in packs[:12]:
        title = p["title"][:42]
        rows.append([InlineKeyboardButton(
            text=f"{title} · {p['questions_count']} вопр. · id {p['id']}",
            callback_data=f"train_pack:{p['id']}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="train_pack:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tournament_tours_menu(pack_id: int, tours: List[dict]) -> InlineKeyboardMarkup:
    rows = []
    for tour in tours:
        shown = min(tour["questions_count"], 12)
        if tour["questions_count"] > 12:
            label = f"Тур {tour['tour_number']} · {shown} из {tour['questions_count']}"
        else:
            label = f"Тур {tour['tour_number']} · {tour['questions_count']} вопр."
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"train_tour:{pack_id}:{tour['tour_number']}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="train_tour:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reveal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Показать ответ", callback_data="quiz:reveal")],
        [InlineKeyboardButton(text="🚪 Прервать", callback_data="quiz:abort")],
    ])


def self_assessment() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Знал", callback_data="quiz:knew"),
            InlineKeyboardButton(text="❌ Не знал", callback_data="quiz:didnt"),
        ],
        [InlineKeyboardButton(text="🚪 Прервать", callback_data="quiz:abort")],
    ])


def after_finish() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Ещё тренировку", callback_data="cmd:train")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="cmd:menu")],
    ])
