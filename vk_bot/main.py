"""VK bot entrypoint for CHGK team training."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from typing import Any, Optional

import requests

import config  # loads .env  # noqa: F401
from app.training_engine import (
    TrainingSession,
    record_and_advance,
    session_summary,
    start_followup,
    start_marked,
    start_random,
    start_review,
    start_team_gap,
    submit_answer,
)
from config import DB_PATH
from database.db import get_connection
from database.training_db import count_due, get_stats, get_training_connection

log = logging.getLogger("chgk_vk_bot")

API_VERSION = "5.199"
DEFAULT_COUNT = 12
PUBLIC_URL = os.environ.get("CHGK_PUBLIC_URL", "https://play.nordvel.ru").rstrip("/")
REMINDER_TEXT = (
    "Сегодняшняя тренировка ждёт. 12 вопросов хватит, чтобы держать форму."
)


@dataclass
class VkMessage:
    peer_id: int
    user_id: int
    text: str
    payload: dict[str, Any]


@dataclass
class UserState:
    state: str = "idle"
    session: Optional[TrainingSession] = None


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
    allowed = _parse_id_list(env.get("CHGK_VK_ALLOWED_USER_IDS", ""))

    owner_raw = env.get("CHGK_VK_OWNER_USER_ID", "").strip()
    if owner_raw.isdigit():
        allowed.add(int(owner_raw))

    return allowed


def get_bot_token(env: Mapping[str, str] | None = None) -> str:
    env = env or os.environ
    return env.get("CHGK_VK_BOT_TOKEN", "").strip()


def get_group_id(env: Mapping[str, str] | None = None) -> str:
    env = env or os.environ
    return env.get("CHGK_VK_GROUP_ID", "").strip()


def get_reminder_hour(env: Mapping[str, str] | None = None) -> Optional[int]:
    env = env or os.environ
    raw = env.get("CHGK_VK_REMINDER_HOUR", "").strip()
    if not raw:
        return None
    try:
        hour = int(raw)
    except ValueError:
        raise ValueError("CHGK_VK_REMINDER_HOUR должен быть числом от 0 до 23")
    if not 0 <= hour <= 23:
        raise ValueError("CHGK_VK_REMINDER_HOUR должен быть числом от 0 до 23")
    return hour


def resolve_training_user_id(vk_user_id: int) -> int:
    """Map VK identity to the shared training user id."""
    conn = get_training_connection()
    try:
        row = conn.execute(
            """
            SELECT user_id
            FROM user_identities
            WHERE provider = 'vk' AND provider_user_id = ?
            """,
            (str(vk_user_id),),
        ).fetchone()
    finally:
        conn.close()
    return int(row["user_id"]) if row else vk_user_id


def reminder_vk_user_ids(allowed_ids: set[int]) -> list[int]:
    ids = set(allowed_ids)
    conn = get_training_connection()
    try:
        rows = conn.execute(
            """
            SELECT provider_user_id
            FROM user_identities
            WHERE provider = 'vk'
            """
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        raw = str(row["provider_user_id"])
        if raw.isdigit():
            ids.add(int(raw))
    return sorted(ids)


class VkApi:
    def __init__(self, token: str, group_id: str):
        self.token = token
        self.group_id = group_id
        self.session = requests.Session()

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        payload = {
            "access_token": self.token,
            "v": API_VERSION,
            **params,
        }
        response = self.session.post(
            f"https://api.vk.com/method/{method}",
            data=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"VK API error {data['error']}")
        return data["response"]

    def get_long_poll_server(self) -> dict[str, Any]:
        return self.call("groups.getLongPollServer", group_id=self.group_id)

    def send_message(
        self,
        peer_id: int,
        text: str,
        keyboard: Optional[dict[str, Any]] = None,
    ) -> None:
        params: dict[str, Any] = {
            "peer_id": peer_id,
            "message": text[:4000],
            "random_id": random.randint(1, 2_147_483_647),
        }
        if keyboard is not None:
            params["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
        try:
            self.call("messages.send", **params)
        except RuntimeError as exc:
            if keyboard is None or "'error_code': 912" not in str(exc):
                raise
            log.warning("VK keyboard is disabled in community settings; sending text only.")
            params.pop("keyboard", None)
            params["random_id"] = random.randint(1, 2_147_483_647)
            self.call("messages.send", **params)


def _text_button(label: str, command: str, color: str = "secondary") -> dict[str, Any]:
    return {
        "action": {
            "type": "text",
            "label": label,
            "payload": json.dumps({"cmd": command}, ensure_ascii=False),
        },
        "color": color,
    }


def _link_button(label: str, link: str) -> dict[str, Any]:
    return {
        "action": {
            "type": "open_link",
            "label": label,
            "link": link,
        }
    }


def keyboard(rows: list[list[dict[str, Any]]], one_time: bool = False) -> dict[str, Any]:
    return {
        "one_time": one_time,
        "inline": False,
        "buttons": rows,
    }


def main_keyboard() -> dict[str, Any]:
    return keyboard([
        [_text_button("Тренировка", "train", "primary")],
        [
            _text_button("Повторение", "review", "secondary"),
            _text_button("Статистика", "stats", "secondary"),
        ],
        [_link_button("Открыть сайт", PUBLIC_URL)],
    ])


def modes_keyboard() -> dict[str, Any]:
    return keyboard([
        [
            _text_button("Случайные", "mode_random", "primary"),
            _text_button("Размеченные", "mode_marked", "secondary"),
        ],
        [
            _text_button("Повторение", "mode_review", "secondary"),
            _text_button("Ошибки", "mode_followup", "secondary"),
        ],
        [_text_button("Слабые темы", "mode_team_gap", "secondary")],
        [
            _text_button("Меню", "menu", "secondary"),
            _text_button("Отмена", "cancel", "negative"),
        ],
    ])


def reveal_keyboard() -> dict[str, Any]:
    return keyboard([
        [
            _text_button("Показать ответ", "reveal", "primary"),
            _text_button("Прервать", "abort", "negative"),
        ],
    ])


def assessment_keyboard() -> dict[str, Any]:
    return keyboard([
        [
            _text_button("Знал", "knew", "positive"),
            _text_button("Не знал", "didnt", "negative"),
        ],
        [_text_button("Прервать", "abort", "negative")],
    ])


def finish_keyboard() -> dict[str, Any]:
    return keyboard([
        [
            _text_button("Ещё тренировку", "train", "primary"),
            _text_button("Меню", "menu", "secondary"),
        ],
    ])


def _normalize_command(message: VkMessage) -> str:
    payload_cmd = message.payload.get("cmd")
    if isinstance(payload_cmd, str):
        return payload_cmd

    text = message.text.strip().lower()
    if text.startswith("/"):
        text = text[1:]
    aliases = {
        "start": "menu",
        "начать": "menu",
        "меню": "menu",
        "помощь": "menu",
        "help": "menu",
        "управление": "menu",
        "тренировка": "train",
        "train": "train",
        "повторение": "review",
        "review": "review",
        "статистика": "stats",
        "stats": "stats",
        "прогресс": "stats",
        "отмена": "cancel",
        "cancel": "cancel",
    }
    return aliases.get(text, "")


def _strip_html(raw: Optional[str]) -> str:
    if not raw:
        return ""
    text = str(raw).replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text)


def _build_question_text(session: TrainingSession, q: dict[str, Any]) -> str:
    parts = [f"Вопрос {session.index + 1} / {session.total()}"]
    razdatka = _strip_html(q.get("razdatka_text"))
    if razdatka:
        parts.append(f"Раздатка: {razdatka}")
    parts.append(_strip_html(q.get("text")))
    return "\n\n".join(parts)


def _build_reveal_text(session: TrainingSession, q: dict[str, Any]) -> str:
    parts = []
    if session.user_answer:
        parts.append(f"Твой ответ: {session.user_answer}")
    parts.append(f"Правильный ответ: {_strip_html(q.get('answer'))}")
    if q.get("zachet"):
        parts.append(f"Зачёт: {_strip_html(q.get('zachet'))}")
    if q.get("nezachet"):
        parts.append(f"Незачёт: {_strip_html(q.get('nezachet'))}")
    if q.get("comment"):
        parts.append(f"Комментарий:\n{_strip_html(q.get('comment'))}")
    if q.get("source"):
        parts.append(f"Источник: {_strip_html(q.get('source'))[:300]}")
    return "\n\n".join(parts)


class ChgkVkBot:
    def __init__(self, api: VkApi, allowed_user_ids: set[int], reminder_hour: Optional[int]):
        self.api = api
        self.allowed_user_ids = allowed_user_ids
        self.reminder_hour = reminder_hour
        self.user_states: dict[int, UserState] = {}
        self._last_reminder_date = ""

    def user_allowed(self, vk_user_id: int) -> bool:
        return not self.allowed_user_ids or vk_user_id in self.allowed_user_ids

    def training_user_id(self, vk_user_id: int) -> int:
        return resolve_training_user_id(vk_user_id)

    def _state(self, vk_user_id: int) -> UserState:
        return self.user_states.setdefault(vk_user_id, UserState())

    def handle(self, message: VkMessage) -> None:
        if not self.user_allowed(message.user_id):
            self.api.send_message(
                message.peer_id,
                "Доступ ограничен. Передай организатору свой VK ID: "
                f"{message.user_id}.",
            )
            return

        command = _normalize_command(message)
        state = self._state(message.user_id)

        if command in {"menu", ""} and state.state == "in_question" and message.text:
            self._handle_answer(message, state)
            return

        if command == "menu":
            state.state = "idle"
            state.session = None
            self._send_menu(message)
        elif command == "train":
            self._send_modes(message)
        elif command == "stats":
            self._send_stats(message)
        elif command == "review":
            self._start_session(message, "review")
        elif command.startswith("mode_"):
            self._start_session(message, command.removeprefix("mode_"))
        elif command == "reveal":
            self._reveal(message, state)
        elif command in {"knew", "didnt"}:
            self._assess(message, state, knew=(command == "knew"))
        elif command == "abort":
            self._abort(message, state)
        elif command == "cancel":
            state.state = "idle"
            state.session = None
            self.api.send_message(message.peer_id, "Отменено.", main_keyboard())
        elif state.state == "in_question":
            self._handle_answer(message, state)
        else:
            self._send_menu(message)

    def _send_menu(self, message: VkMessage) -> None:
        training_id = self.training_user_id(message.user_id)
        tconn = get_training_connection()
        try:
            due = count_due(tconn, training_id)
        finally:
            tconn.close()
        suffix = f"\nК повторению сейчас: {due}" if due else ""
        self.api.send_message(
            message.peer_id,
            "Картотека ЧГК\n\n"
            "Здесь можно тренироваться, повторять ошибки и смотреть прогресс."
            f"{suffix}\n\n"
            "Выбери действие:",
            main_keyboard(),
        )

    def _send_modes(self, message: VkMessage) -> None:
        self.api.send_message(
            message.peer_id,
            "Выбери режим. По умолчанию бот даст 12 вопросов.",
            modes_keyboard(),
        )

    def _start_session(self, message: VkMessage, mode: str) -> None:
        training_id = self.training_user_id(message.user_id)
        chgk_conn = get_connection(DB_PATH)
        tconn = get_training_connection()
        try:
            if mode == "random":
                session = start_random(chgk_conn, count=DEFAULT_COUNT)
            elif mode == "marked":
                session = start_marked(chgk_conn, count=DEFAULT_COUNT)
            elif mode == "review":
                session = start_review(chgk_conn, tconn, training_id, count=DEFAULT_COUNT)
            elif mode == "followup":
                session = start_followup(chgk_conn, tconn, training_id, count=DEFAULT_COUNT)
            elif mode == "team_gap":
                session = start_team_gap(chgk_conn, tconn, training_id, count=DEFAULT_COUNT)
            else:
                self._send_modes(message)
                return
        except FileNotFoundError:
            self.api.send_message(message.peer_id, "Профиль слабых тем команды пока не найден.")
            return
        finally:
            chgk_conn.close()
            tconn.close()

        if not session.questions:
            self.api.send_message(message.peer_id, f"Не удалось собрать: {session.filters_repr}.")
            return

        state = self._state(message.user_id)
        state.state = "in_question"
        state.session = session
        self.api.send_message(message.peer_id, f"Старт: {session.filters_repr}")
        self._show_question(message, state)

    def _show_question(self, message: VkMessage, state: UserState) -> None:
        session = state.session
        if session is None or session.is_finished():
            self._show_summary(message, state)
            return

        q = session.current()
        state.state = "in_question"
        self.api.send_message(message.peer_id, _build_question_text(session, q), reveal_keyboard())

        pic = q.get("razdatka_pic")
        if pic:
            self.api.send_message(
                message.peer_id,
                f"Раздатка: {pic}",
            )

    def _handle_answer(self, message: VkMessage, state: UserState) -> None:
        session = state.session
        if session is None:
            self._send_menu(message)
            return
        submit_answer(session, message.text)
        self._reveal(message, state)

    def _reveal(self, message: VkMessage, state: UserState) -> None:
        session = state.session
        if session is None:
            self.api.send_message(message.peer_id, "Сессия не найдена.", main_keyboard())
            return
        if not session.user_answer:
            submit_answer(session, "")
        q = session.current()
        state.state = "in_reveal"
        self.api.send_message(message.peer_id, _build_reveal_text(session, q), assessment_keyboard())

    def _assess(self, message: VkMessage, state: UserState, knew: bool) -> None:
        session = state.session
        if session is None or state.state != "in_reveal":
            self.api.send_message(message.peer_id, "Сессия не найдена.", main_keyboard())
            return

        training_id = self.training_user_id(message.user_id)
        tconn = get_training_connection()
        try:
            has_next = record_and_advance(session, tconn, training_id, knew)
        finally:
            tconn.close()

        if has_next:
            self._show_question(message, state)
        else:
            self._show_summary(message, state)

    def _abort(self, message: VkMessage, state: UserState) -> None:
        if state.session and state.session.results:
            self._show_summary(message, state)
        else:
            state.state = "idle"
            state.session = None
            self.api.send_message(message.peer_id, "Тренировка прервана.", main_keyboard())

    def _show_summary(self, message: VkMessage, state: UserState) -> None:
        session = state.session
        if session is None or not session.results:
            state.state = "idle"
            state.session = None
            self.api.send_message(message.peer_id, "Нет ответов для отчёта.", main_keyboard())
            return

        summary = session_summary(session)
        lines = [
            "Итоги тренировки",
            f"Режим: {summary['filters_repr']}",
            f"Результат: {summary['correct']}/{summary['total']} ({summary['pct']}%)",
            f"Среднее время: {summary['avg_time']:.1f}с",
        ]
        if summary["by_category"]:
            lines.append("\nПо категориям:")
            for cat, data in sorted(summary["by_category"].items()):
                total = data["total"]
                correct = data["correct"]
                pct = round(100 * correct / total) if total else 0
                lines.append(f"{cat}: {correct}/{total} ({pct}%)")

        state.state = "idle"
        state.session = None
        self.api.send_message(message.peer_id, "\n".join(lines), finish_keyboard())

    def _send_stats(self, message: VkMessage) -> None:
        training_id = self.training_user_id(message.user_id)
        tconn = get_training_connection()
        try:
            stats = get_stats(tconn, training_id)
        finally:
            tconn.close()

        if stats["total_attempts"] == 0:
            self.api.send_message(
                message.peer_id,
                "Пока статистики нет. Начни тренировку.",
                main_keyboard(),
            )
            return

        pct = (
            round(100 * stats["correct_attempts"] / stats["total_attempts"])
            if stats["total_attempts"]
            else 0
        )
        lines = [
            "Твой прогресс",
            f"Всего ответов: {stats['total_attempts']}",
            f"Правильных: {stats['correct_attempts']} ({pct}%)",
            f"Уникальных вопросов: {stats['distinct_questions']}",
            f"Пора повторить: {stats['due_now']}",
        ]
        if stats["by_category"]:
            lines.append("\nПо категориям:")
            for row in stats["by_category"][:10]:
                total = row["total"]
                knew = row["knew"] or 0
                cat_pct = round(100 * knew / total) if total else 0
                lines.append(f"{row['category']}: {knew}/{total} ({cat_pct}%)")

        self.api.send_message(message.peer_id, "\n".join(lines), main_keyboard())

    def send_due_reminders(self) -> None:
        if self.reminder_hour is None:
            return
        now = time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        if now.tm_hour != self.reminder_hour or self._last_reminder_date == today:
            return
        targets = reminder_vk_user_ids(self.allowed_user_ids)
        for vk_user_id in targets:
            try:
                self.api.send_message(vk_user_id, REMINDER_TEXT, main_keyboard())
            except Exception as exc:  # pragma: no cover - VK/network behavior
                log.warning("Failed to send reminder to vk user %s: %s", vk_user_id, exc)
        self._last_reminder_date = today


def parse_message_event(update: dict[str, Any]) -> Optional[VkMessage]:
    if update.get("type") != "message_new":
        return None
    message = update.get("object", {}).get("message", {})
    if not message:
        return None
    from_id = int(message.get("from_id", 0))
    peer_id = int(message.get("peer_id", from_id))
    if from_id <= 0 or peer_id <= 0:
        return None

    payload: dict[str, Any] = {}
    raw_payload = message.get("payload")
    if isinstance(raw_payload, str) and raw_payload:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            payload = {}

    return VkMessage(
        peer_id=peer_id,
        user_id=from_id,
        text=str(message.get("text") or ""),
        payload=payload,
    )


def run_polling(bot: ChgkVkBot) -> None:
    server = bot.api.get_long_poll_server()
    key = server["key"]
    ts = server["ts"]
    server_url = server["server"]

    log.info("VK bot started")
    while True:
        try:
            response = requests.get(
                server_url,
                params={"act": "a_check", "key": key, "ts": ts, "wait": 25},
                timeout=35,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("failed"):
                log.warning("VK long poll failed: %s", data)
                server = bot.api.get_long_poll_server()
                key = server["key"]
                ts = server["ts"]
                server_url = server["server"]
                continue
            ts = data["ts"]
            bot.send_due_reminders()
            for update in data.get("updates", []):
                message = parse_message_event(update)
                if message is not None:
                    bot.handle(message)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # pragma: no cover - network loop
            log.exception("VK polling error: %s", exc)
            time.sleep(5)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    token = get_bot_token()
    if not token:
        log.error("VK bot token is missing: set CHGK_VK_BOT_TOKEN in .env")
        sys.exit(1)
    group_id = get_group_id()
    if not group_id:
        log.error("VK group id is missing: set CHGK_VK_GROUP_ID in .env")
        sys.exit(1)

    try:
        reminder_hour = get_reminder_hour()
    except ValueError as exc:
        log.error(str(exc))
        sys.exit(1)

    allowed_user_ids = parse_allowed_user_ids()
    if allowed_user_ids:
        log.info("VK allowlist enabled for %s users", len(allowed_user_ids))
    else:
        log.warning("No VK allowlist configured - bot will accept all private chats.")

    run_polling(ChgkVkBot(VkApi(token, group_id), allowed_user_ids, reminder_hour))


if __name__ == "__main__":
    main()
