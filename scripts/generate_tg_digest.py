#!/usr/bin/env python3
"""Генератор персональных дайджестов из Telegram-каналов для команды."""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, TG_DIGEST_DIR
from database.db import get_connection
from database.tg_db import ensure_tg_tables, get_posts_by_categories

# Слабые категории участников (оценка 1-2 из опроса)
TEAM_WEAK_CATEGORIES = {
    "Женя": ["Наука и технологии", "Спорт", "Природа и животные"],
    "Кирилл": [
        "География", "Наука и технологии", "Искусство", "Музыка",
        "Кино и театр", "Спорт", "Язык и лингвистика",
        "Религия и мифология", "Природа и животные", "Логика и wordplay",
    ],
    "Егор": ["Литература", "Искусство", "Музыка"],
    "Арсений": ["Музыка"],
    "Алина": ["Спорт", "Природа и животные"],
    "Матвей": ["Искусство", "Музыка", "Религия и мифология"],
}


def generate_digest(
    member: str,
    categories: list,
    days: int = 30,
    limit_per_cat: int = 10,
    output_dir: Path = TG_DIGEST_DIR,
) -> Path:
    """Сгенерировать MD-дайджест для участника.

    Returns:
        Путь к созданному файлу.
    """
    conn = get_connection(DB_PATH)
    ensure_tg_tables(conn)

    posts_by_cat = get_posts_by_categories(conn, categories, days, limit_per_cat)
    conn.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = output_dir / f"{member}_digest_{today}.md"

    total_posts = sum(len(v) for v in posts_by_cat.values())

    lines = [
        f"# Дайджест для {member} -- {today}",
        "",
        f"Категории: {', '.join(categories)}",
        f"Всего постов: {total_posts}",
        "",
    ]

    if not posts_by_cat:
        lines.append("Постов пока нет. Добавьте каналы и запустите парсинг!")
        filepath.write_text("\n".join(lines), encoding="utf-8")
        return filepath

    for cat in categories:
        cat_posts = posts_by_cat.get(cat, [])
        lines.append(f"## {cat} ({len(cat_posts)} постов)")
        lines.append("")

        if not cat_posts:
            lines.append("_Нет постов в этой категории._")
            lines.append("")
            continue

        for p in cat_posts:
            # Заголовок: первые 80 символов текста
            title = p["text"][:80].replace("\n", " ").strip()
            if len(p["text"]) > 80:
                title += "..."

            lines.append(f"### [{title}]({p['link']})")
            lines.append("")

            # Текст поста (первые 500 символов)
            preview = p["text"][:500]
            if len(p["text"]) > 500:
                preview += "..."
            # Цитируем
            for line in preview.split("\n"):
                lines.append(f"> {line}")
            lines.append("")

            # Мета-информация
            channel = f"@{p['channel_username']}"
            date_str = p.get("post_date", "")[:10]
            views = _format_views(p.get("views", 0))
            lines.append(f"**Канал:** {channel} | **Дата:** {date_str} | **Просмотры:** {views}")
            lines.append("")
            lines.append("---")
            lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath


def _format_views(views: int) -> str:
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    if views >= 1_000:
        return f"{views / 1_000:.1f}K"
    return str(views)


def main():
    parser = argparse.ArgumentParser(
        description="Генерация персональных дайджестов из ТГ-каналов"
    )
    parser.add_argument(
        "--member", type=str, default=None,
        help="Имя участника (по умолчанию -- все)",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Посты за последние N дней (default: 30)",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Макс. постов на категорию (default: 10)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Папка для вывода",
    )
    parser.add_argument(
        "--list-members", action="store_true",
        help="Показать участников и их слабые категории",
    )

    args = parser.parse_args()

    if args.list_members:
        print("Участники и слабые категории:")
        for name, cats in TEAM_WEAK_CATEGORIES.items():
            print(f"  {name}: {', '.join(cats)}")
        return

    output_dir = Path(args.output) if args.output else TG_DIGEST_DIR
    members = {args.member: TEAM_WEAK_CATEGORIES[args.member]} if args.member else TEAM_WEAK_CATEGORIES

    for name, cats in members.items():
        filepath = generate_digest(
            member=name,
            categories=cats,
            days=args.days,
            limit_per_cat=args.limit,
            output_dir=output_dir,
        )
        print(f"[Digest] {name}: {filepath}")


if __name__ == "__main__":
    main()
