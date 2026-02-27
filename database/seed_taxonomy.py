"""Инициализация таксономии (14 категорий, 52 подкатегории) в БД."""

import sqlite3
from typing import Dict, List, Tuple

TAXONOMY: List[Tuple[str, str, List[Tuple[str, str]]]] = [
    # (name, name_ru, [(sub_name, sub_name_ru), ...])
    ("history", "История", [
        ("ancient", "Древний мир и Античность"),
        ("medieval", "Средневековье и Новое время"),
        ("modern", "Новейшая история (19-21 в.)"),
        ("russia", "История России и СССР"),
        ("military", "Военная история"),
    ]),
    ("literature", "Литература", [
        ("russian_classic", "Русская классика"),
        ("world_classic", "Зарубежная классика"),
        ("contemporary", "Современная литература"),
        ("poetry", "Поэзия"),
        ("children", "Детская литература и сказки"),
    ]),
    ("science", "Наука и технологии", [
        ("physics_astro", "Физика и астрономия"),
        ("biology_medicine", "Биология и медицина"),
        ("chemistry", "Химия"),
        ("math_it", "Математика и информатика"),
        ("technology", "Технологии и изобретения"),
    ]),
    ("geography", "География", [
        ("physical", "Физическая география"),
        ("countries_cities", "Страны и города"),
        ("travel", "Путешествия и исследования"),
    ]),
    ("art", "Искусство", [
        ("painting_sculpture", "Живопись и скульптура"),
        ("architecture", "Архитектура"),
        ("photo_design", "Фотография и дизайн"),
    ]),
    ("music", "Музыка", [
        ("classical", "Классическая музыка"),
        ("popular", "Популярная музыка и рок"),
        ("instruments_theory", "Инструменты и теория"),
    ]),
    ("cinema_theater", "Кино и театр", [
        ("cinema", "Кинематограф"),
        ("theater_opera", "Театр и опера"),
        ("tv_series", "ТВ и сериалы"),
    ]),
    ("sports", "Спорт", [
        ("football", "Футбол"),
        ("olympic", "Олимпийские виды спорта"),
        ("other_sports", "Другие виды спорта"),
        ("sports_history", "История спорта"),
    ]),
    ("language", "Язык и лингвистика", [
        ("etymology", "Этимология и словообразование"),
        ("idioms", "Фразеологизмы и крылатые выражения"),
        ("foreign_languages", "Иностранные языки"),
        ("onomastics", "Ономастика — имена и названия"),
    ]),
    ("religion_mythology", "Религия и мифология", [
        ("world_religions", "Мировые религии"),
        ("mythology_folklore", "Мифология и фольклор"),
        ("biblical", "Библейские и религиозные сюжеты"),
    ]),
    ("society", "Общество и политика", [
        ("politics", "Политика и государство"),
        ("economics", "Экономика и бизнес"),
        ("law", "Право и законы"),
        ("famous_people", "Знаменитые люди"),
    ]),
    ("everyday", "Быт и повседневность", [
        ("food_drinks", "Еда и напитки"),
        ("fashion", "Мода и одежда"),
        ("holidays", "Праздники и традиции"),
        ("games_entertainment", "Игры и развлечения"),
    ]),
    ("nature", "Природа и животные", [
        ("animals", "Животные"),
        ("plants", "Растения"),
        ("ecology", "Экология и окружающая среда"),
    ]),
    ("logic_wordplay", "Логика и wordplay", [
        ("logic_puzzles", "Логические задачи"),
        ("wordplay_puns", "Игра слов и каламбуры"),
        ("ciphers_riddles", "Шифры и загадки"),
    ]),
]


def seed_taxonomy(conn: sqlite3.Connection) -> None:
    """Заполнить таблицы categories и subcategories."""
    existing = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    if existing > 0:
        print(f"Таксономия уже загружена ({existing} категорий)")
        return

    for cat_order, (cat_name, cat_name_ru, subs) in enumerate(TAXONOMY, start=1):
        conn.execute(
            "INSERT INTO categories (name, name_ru, sort_order) VALUES (?, ?, ?)",
            (cat_name, cat_name_ru, cat_order),
        )
        cat_id = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (cat_name,)
        ).fetchone()[0]

        for sub_order, (sub_name, sub_name_ru) in enumerate(subs, start=1):
            conn.execute(
                """INSERT INTO subcategories
                   (category_id, name, name_ru, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (cat_id, sub_name, sub_name_ru, sub_order),
            )

    conn.commit()
    total_subs = conn.execute("SELECT COUNT(*) FROM subcategories").fetchone()[0]
    print(f"Таксономия загружена: {len(TAXONOMY)} категорий, {total_subs} подкатегорий")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from config import DB_PATH
    from database.db import get_connection

    conn = get_connection(DB_PATH)
    seed_taxonomy(conn)
    conn.close()
