"""
Веб-парсер для Telegram каналов.
Парсит публичные каналы через t.me БЕЗ API credentials.
Адаптировано из Личное/Проекты/telegram_parser.
"""

import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup


class TgWebParser:
    """Парсер публичных Telegram-каналов через веб-интерфейс."""

    def __init__(self, delay: float = 1.5):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

    def parse_channel(
        self,
        username: str,
        after_id: int = 0,
        limit: Optional[int] = None,
        max_pages: int = 200,
    ) -> List[Dict]:
        """Парсит публичный канал через t.me.

        Args:
            username: Username канала (с @ или без).
            after_id: Пропускать посты с id <= after_id (инкрементальный парсинг).
            limit: Максимальное количество постов.
            max_pages: Защита от бесконечного цикла.

        Returns:
            Список словарей с данными постов.
        """
        channel = username.lstrip("@")
        print(f"[TG] Парсинг канала @{channel} (after_id={after_id})")

        posts = []
        before_id = None
        page_count = 0

        while page_count < max_pages:
            page_count += 1

            url = f"https://t.me/s/{channel}"
            if before_id:
                url += f"?before={before_id}"

            try:
                resp = self.session.get(url, timeout=15)
                resp.raise_for_status()
            except requests.RequestException as e:
                print(f"[TG] Ошибка загрузки: {e}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            messages = soup.find_all("div", class_="tgme_widget_message")

            if not messages:
                print(f"[TG] Конец канала (страница {page_count})")
                break

            oldest_id_on_page = None
            new_on_page = 0

            for msg in messages:
                post = self._extract_post(msg, channel)
                if not post:
                    continue

                if oldest_id_on_page is None or post["post_id"] < oldest_id_on_page:
                    oldest_id_on_page = post["post_id"]

                # Инкрементальный фильтр
                if post["post_id"] <= after_id:
                    continue

                posts.append(post)
                new_on_page += 1

                if limit and len(posts) >= limit:
                    print(f"[TG] Достигнут лимит ({limit} постов)")
                    return posts

            print(f"[TG] Стр.{page_count}: +{new_on_page} постов (всего {len(posts)})")

            # Все посты на странице старее after_id — останавливаемся
            if new_on_page == 0 and after_id > 0:
                print(f"[TG] Все посты на странице старее after_id={after_id}")
                break

            if oldest_id_on_page is None:
                break

            # Защита от зацикливания
            if before_id and oldest_id_on_page >= before_id:
                break

            before_id = oldest_id_on_page
            time.sleep(self.delay)

        print(f"[TG] Итого @{channel}: {len(posts)} постов")
        return posts

    def _extract_post(self, message_div, channel: str) -> Optional[Dict]:
        """Извлечь данные поста из HTML-элемента."""
        try:
            data_post = message_div.get("data-post")
            if not data_post:
                return None

            post_id = int(data_post.split("/")[-1])

            # Дата
            time_el = message_div.find("time")
            if not time_el:
                return None
            date_str = time_el.get("datetime", "")
            if not date_str:
                return None
            post_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

            # Текст
            text_div = message_div.find("div", class_="tgme_widget_message_text")
            text = text_div.get_text(separator="\n", strip=True) if text_div else ""

            # Просмотры
            views_span = message_div.find("span", class_="tgme_widget_message_views")
            views = self._parse_views(views_span.get_text(strip=True)) if views_span else 0

            # Тип медиа
            media_type = None
            if message_div.find("a", class_="tgme_widget_message_photo_wrap"):
                media_type = "photo"
            elif message_div.find("video", class_="tgme_widget_message_video"):
                media_type = "video"
            elif message_div.find("div", class_="tgme_widget_message_document"):
                media_type = "document"

            return {
                "post_id": post_id,
                "channel": channel,
                "text": text,
                "link": f"https://t.me/{channel}/{post_id}",
                "date": post_date,
                "views": views,
                "media_type": media_type,
            }
        except Exception as e:
            print(f"[TG] Ошибка парсинга поста: {e}")
            return None

    @staticmethod
    def _parse_views(views_text: str) -> int:
        """Парсит '1.2K' → 1200, '5M' → 5000000."""
        text = views_text.strip().upper().replace(" ", "")
        multipliers = {"K": 1000, "M": 1_000_000, "B": 1_000_000_000}
        for suffix, mult in multipliers.items():
            if suffix in text:
                try:
                    return int(float(text.replace(suffix, "")) * mult)
                except ValueError:
                    return 0
        try:
            return int(text)
        except ValueError:
            return 0
