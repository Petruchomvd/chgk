#!/usr/bin/env python3
"""Бот для постинга классифицированных постов в Telegram-группу с топиками.

Создаёт топик (вкладку) для каждой категории и отправляет туда посты.
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID
from database.db import get_connection
from database.tg_db import ensure_tg_tables

# Иконки для категорий (emoji для топиков)
CATEGORY_ICONS = {
    "История": "0x1F3DB",               # classical building
    "Литература": "0x1F4DA",            # books
    "Наука и технологии": "0x1F52C",    # microscope
    "География": "0x1F30D",             # globe
    "Искусство": "0x1F3A8",             # palette
    "Музыка": "0x1F3B5",                # music note
    "Кино и театр": "0x1F3AC",          # clapper
    "Спорт": "0x26BD",                  # football
    "Язык и лингвистика": "0x1F4AC",    # speech balloon
    "Религия и мифология": "0x1F54C",   # mosque (generic religious)
    "Общество и политика": "0x1F4F0",   # newspaper
    "Быт и повседневность": "0x2615",   # hot beverage
    "Природа и животные": "0x1F33F",    # herb
    "Логика и wordplay": "0x1F9E9",     # puzzle piece
}


class DigestBot:
    """Бот для постинга дайджестов в группу с топиками."""

    def __init__(self, token: str, chat_id: int):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._topic_cache: Dict[str, int] = {}  # category -> topic_id

    def _api(self, method: str, **kwargs) -> dict:
        """Вызов Telegram Bot API."""
        resp = requests.post(f"{self.base_url}/{method}", json=kwargs, timeout=30)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"TG API error: {data.get('description', data)}")
        return data["result"]

    def create_topic(self, name: str, icon_custom_emoji_id: str = None) -> int:
        """Создать топик (вкладку) в группе. Возвращает message_thread_id."""
        kwargs = {"chat_id": self.chat_id, "name": name}
        if icon_custom_emoji_id:
            kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id
        result = self._api("createForumTopic", **kwargs)
        return result["message_thread_id"]

    def get_or_create_topic(self, category: str) -> int:
        """Получить существующий или создать новый топик для категории."""
        if category in self._topic_cache:
            return self._topic_cache[category]

        # Проверяем в БД (таблица tg_topics)
        conn = get_connection(DB_PATH)
        ensure_tg_tables(conn)
        _ensure_topics_table(conn)

        row = conn.execute(
            "SELECT topic_id FROM tg_topics WHERE category = ?", (category,)
        ).fetchone()

        if row:
            self._topic_cache[category] = row["topic_id"]
            conn.close()
            return row["topic_id"]

        # Создаём новый топик
        topic_id = self.create_topic(category)
        conn.execute(
            "INSERT INTO tg_topics (category, topic_id) VALUES (?, ?)",
            (category, topic_id),
        )
        conn.commit()
        conn.close()

        self._topic_cache[category] = topic_id
        print(f"[Bot] Создан топик: {category} (id={topic_id})")
        return topic_id

    def send_post(self, topic_id: int, text: str, link: str, channel: str, views: int) -> bool:
        """Отправить пост в топик."""
        views_str = _format_views(views)

        # Заголовок: первая строка жирным
        first_line = text.split("\n")[0]
        if len(first_line) > 120:
            first_line = first_line[:117] + "..."

        body = text[len(text.split("\n")[0]):].strip()

        # Telegram лимит 4096 символов, оставляем запас на разметку
        if len(body) > 3500:
            body = body[:3497] + "..."

        # Формат сообщения
        parts = [f"<b>{_escape_html(first_line)}</b>"]
        if body:
            parts.append(f"\n\n{_escape_html(body)}")
        parts.append(f'\n\n<a href="{link}">Источник</a> | @{channel} | {views_str} просмотров')
        message = "".join(parts)

        try:
            self._api(
                "sendMessage",
                chat_id=self.chat_id,
                message_thread_id=topic_id,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
            return True
        except RuntimeError as e:
            print(f"[Bot] Ошибка отправки: {e}")
            return False

    def post_classified_posts(self, limit: int = 0, dry_run: bool = False) -> int:
        """Отправить неотправленные классифицированные посты в топики.

        Returns:
            Количество отправленных постов.
        """
        conn = get_connection(DB_PATH)
        ensure_tg_tables(conn)
        _ensure_topics_table(conn)
        _ensure_posted_column(conn)

        sql = """
            SELECT p.*, c.username AS channel_username
            FROM tg_posts p
            JOIN tg_channels c ON c.id = p.channel_id
            WHERE p.category IS NOT NULL AND p.is_useful = 1 AND p.posted_to_group = 0
            ORDER BY p.post_date DESC
        """
        if limit > 0:
            sql += f" LIMIT {limit}"

        posts = [dict(r) for r in conn.execute(sql).fetchall()]

        if not posts:
            print("[Bot] Нет новых постов для отправки")
            conn.close()
            return 0

        print(f"[Bot] Найдено {len(posts)} постов для отправки")

        sent = 0
        for i, post in enumerate(posts, 1):
            category = post["category"]

            if dry_run:
                print(f"[Bot] {i}/{len(posts)}: [{category}] {post['link']} (dry run)")
                continue

            topic_id = self.get_or_create_topic(category)

            ok = self.send_post(
                topic_id=topic_id,
                text=post["text"],
                link=post["link"],
                channel=post["channel_username"],
                views=post["views"],
            )

            if ok:
                conn.execute(
                    "UPDATE tg_posts SET posted_to_group = 1 WHERE id = ?",
                    (post["id"],),
                )
                conn.commit()
                sent += 1
                print(f"[Bot] {i}/{len(posts)}: [{category}] -> отправлен")
            else:
                print(f"[Bot] {i}/{len(posts)}: [{category}] -> ОШИБКА")

            time.sleep(0.5)  # rate limiting

        conn.close()
        print(f"\n[Bot] Отправлено: {sent}/{len(posts)}")
        return sent


def _ensure_topics_table(conn: sqlite3.Connection) -> None:
    """Создать таблицу для маппинга категорий на topic_id."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_topics (
            category TEXT PRIMARY KEY,
            topic_id INTEGER NOT NULL
        )
    """)
    conn.commit()


def _ensure_posted_column(conn: sqlite3.Connection) -> None:
    """Добавить колонку posted_to_group если её нет."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tg_posts'"
    ).fetchone()
    if row and "posted_to_group" not in row[0]:
        conn.execute("ALTER TABLE tg_posts ADD COLUMN posted_to_group INTEGER DEFAULT 0")
        conn.commit()


def _escape_html(text: str) -> str:
    """Экранировать HTML-спецсимволы для Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_views(views: int) -> str:
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    if views >= 1_000:
        return f"{views / 1_000:.1f}K"
    return str(views)


def main():
    parser = argparse.ArgumentParser(
        description="Постинг классифицированных постов в ТГ-группу с топиками"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Максимум постов для отправки (0 = все)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Только показать что будет отправлено, не отправлять",
    )
    parser.add_argument(
        "--token", type=str, default=None,
        help="Токен бота (по умолчанию из .env)",
    )
    parser.add_argument(
        "--chat-id", type=int, default=None,
        help="ID группы (по умолчанию из .env)",
    )

    args = parser.parse_args()

    token = args.token or TG_DIGEST_BOT_TOKEN
    chat_id = args.chat_id or TG_DIGEST_CHAT_ID

    if not token:
        print("Ошибка: укажи TG_DIGEST_BOT_TOKEN в .env или --token")
        sys.exit(1)
    if not chat_id:
        print("Ошибка: укажи TG_DIGEST_CHAT_ID в .env или --chat-id")
        sys.exit(1)

    bot = DigestBot(token, chat_id)
    bot.post_classified_posts(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
