"""Telegram-уведомления о прогрессе классификации."""

import os
import threading
import time
from typing import Optional

import requests


def _send_message(text: str, token: str, chat_id: str) -> bool:
    """Отправить сообщение в Telegram."""
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[TG] Ошибка отправки: {e}")
        return False


class TelegramNotifier:
    """Отправляет уведомления в Telegram о ходе классификации.

    Использование:
        notifier = TelegramNotifier(model="qwen2.5:14b", total=500)
        notifier.start()
        # ... в цикле классификации ...
        notifier.update(success=1, failed=0, current_question="Текст вопроса...", last_category="История → Древний мир")
        # ... по завершении ...
        notifier.finish()
    """

    def __init__(
        self,
        model: str,
        total: int,
        total_in_db: int = 0,
        method: str = "",
        twostage: bool = False,
        few_shot: bool = True,
        token: str = None,
        chat_id: str = None,
        interval: int = None,
    ):
        self.model = model
        self.total = total
        self.total_in_db = total_in_db
        self.method = method
        self.twostage = twostage
        self.few_shot = few_shot
        # Читаем из env в момент создания (а не при импорте модуля)
        self.token = token or os.environ.get("CHGK_TG_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("CHGK_TG_CHAT_ID", "")
        self.interval = interval or int(os.environ.get("CHGK_TG_INTERVAL", 1800))

        self.success = 0
        self.failed = 0
        self.processed = 0
        self.start_time = 0.0
        self.last_category = ""
        self.current_question = ""

        self._timer: Optional[threading.Timer] = None
        self._enabled = bool(self.token and self.chat_id)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        """Отправить стартовое сообщение и запустить периодическую отправку."""
        self.start_time = time.time()
        if not self._enabled:
            print("[TG] Уведомления отключены (нет CHGK_TG_BOT_TOKEN / CHGK_TG_CHAT_ID)")
            return

        mode_str = "двухэтапный" if self.twostage else "одноэтапный"
        eta_str = ""
        # Грубая оценка: ~17 сек/вопрос для 14B, ~9 для 7B
        est_sec_per_q = 17 if "14b" in self.model.lower() else 9
        est_total = self.total * est_sec_per_q
        eta_str = f"\n⏱ Примерное время: ~{_fmt_duration(est_total)}"

        db_info = ""
        if self.total_in_db:
            already = self.total_in_db - self.total
            db_info = f"\n📦 Всего в БД: {self.total_in_db} (уже классиф.: {already})"

        msg = (
            f"🚀 <b>Классификация ЧГК-вопросов запущена</b>\n\n"
            f"🤖 Модель: <code>{self.model}</code>\n"
            f"📋 К обработке: <b>{self.total}</b> вопросов{db_info}\n"
            f"⚙️ Режим: {mode_str}"
            f"{eta_str}"
        )
        _send_message(msg, self.token, self.chat_id)
        self._schedule_periodic()

    def update(
        self,
        success: int,
        failed: int,
        current_question: str = "",
        last_category: str = "",
    ) -> None:
        """Обновить счётчики (вызывать после каждого вопроса)."""
        self.success = success
        self.failed = failed
        self.processed = success + failed
        self.current_question = current_question
        self.last_category = last_category

    def _schedule_periodic(self) -> None:
        """Запланировать следующую периодическую отправку."""
        if not self._enabled:
            return
        self._timer = threading.Timer(self.interval, self._send_periodic)
        self._timer.daemon = True
        self._timer.start()

    def _send_periodic(self) -> None:
        """Отправить промежуточный отчёт."""
        elapsed = time.time() - self.start_time
        speed = self.processed / elapsed if elapsed > 0 else 0
        remaining = self.total - self.processed
        eta_sec = remaining / speed if speed > 0 else 0

        pct = (self.processed / self.total * 100) if self.total > 0 else 0
        bar = _progress_bar(pct)
        speed_str = f"{speed:.2f} в/сек ({1/speed:.1f} сек/вопрос)" if speed > 0 else "..."

        msg = (
            f"📊 <b>Прогресс классификации</b>\n"
            f"{bar} {pct:.1f}%\n"
            f"Обработано: {self.processed}/{self.total}\n"
            f"✅ {self.success}  ❌ {self.failed}\n"
            f"Скорость: {speed_str}\n"
            f"Прошло: {_fmt_duration(elapsed)}\n"
            f"Осталось: ~{_fmt_duration(eta_sec)}\n"
            f"Модель: <code>{self.model}</code>"
        )
        _send_message(msg, self.token, self.chat_id)
        self._schedule_periodic()

    def finish(self) -> None:
        """Отправить финальный отчёт и остановить таймер."""
        if self._timer:
            self._timer.cancel()
            self._timer = None

        elapsed = time.time() - self.start_time
        speed = self.processed / elapsed if elapsed > 0 else 0

        if not self._enabled:
            return

        speed_str = f"{1/speed:.1f} сек/вопрос" if speed > 0 else "N/A"
        msg = (
            f"✅ <b>Классификация завершена!</b>\n\n"
            f"Модель: <code>{self.model}</code>\n"
            f"Всего обработано: {self.processed}\n"
            f"Успешно: {self.success}\n"
            f"Неудачно: {self.failed}\n"
            f"Время: {_fmt_duration(elapsed)}\n"
            f"Средняя скорость: {speed_str}"
        )
        _send_message(msg, self.token, self.chat_id)


def _progress_bar(pct: float, width: int = 20) -> str:
    """Текстовый прогресс-бар: [████████░░░░░░░░░░░░]."""
    filled = int(width * pct / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


def _fmt_duration(seconds: float) -> str:
    """Форматирование длительности: 1ч 23м 45с."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}ч {m}м {s}с"
    if m > 0:
        return f"{m}м {s}с"
    return f"{s}с"
