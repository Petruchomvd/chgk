#!/usr/bin/env python3
"""Send private Nordvel planner reminders through existing CHGK bots."""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

REMINDERS_PATH = Path(
    os.environ.get("NORDVEL_REMINDERS_PATH", "/var/lib/nordvel/reminders.json")
)
STATE_PATH = Path(
    os.environ.get("NORDVEL_REMINDER_STATE_PATH", "/var/lib/nordvel/reminder_notifications.json")
)
PLANNER_URL = os.environ.get("NORDVEL_REMINDER_PUBLIC_URL", "https://nordvel.ru/planner.html")
REQUEST_TIMEOUT = 20
REPEAT_NOTIFY_MODE = "due_day_repeat"
SINGLE_NOTIFY_MODE = "single"
DEFAULT_NOTIFICATION_SLOTS = {9, 13, 18, 21}


@dataclass(frozen=True)
class Reminder:
    id: str
    title: str
    due_date: date
    due_time: str
    category: str
    notes: str
    lead_days: int
    notify_mode: str


def _parse_id_list(raw: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    normalized = raw.replace(";", ",").replace("\n", ",")
    for part in normalized.split(","):
        token = part.strip()
        if not token.isdigit():
            continue
        value = int(token)
        if value not in seen:
            ids.append(value)
            seen.add(value)
    return ids


def _target_ids(primary_env: str, fallback_env: str) -> list[int]:
    explicit = _parse_id_list(os.environ.get(primary_env, ""))
    if explicit:
        return explicit
    return _parse_id_list(os.environ.get(fallback_env, ""))


def _notification_timezone() -> ZoneInfo:
    name = os.environ.get("NORDVEL_REMINDER_TIMEZONE", "Europe/Samara").strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _notification_slots() -> set[int]:
    raw = os.environ.get("NORDVEL_REMINDER_HOURS", "")
    if not raw:
        return DEFAULT_NOTIFICATION_SLOTS
    slots = {hour for hour in _parse_id_list(raw) if 0 <= hour <= 23}
    return slots or DEFAULT_NOTIFICATION_SLOTS


def _should_send_at(run_at: datetime) -> bool:
    return run_at.hour in _notification_slots()


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_reminders(path: Path = REMINDERS_PATH) -> list[Reminder]:
    raw = _load_json(path, [])
    reminders: list[Reminder] = []
    if not isinstance(raw, list):
        return reminders
    for item in raw:
        if not isinstance(item, dict) or item.get("done"):
            continue
        try:
            reminder = Reminder(
                id=str(item["id"]),
                title=str(item["title"]).strip(),
                due_date=date.fromisoformat(str(item["due_date"])),
                due_time=str(item.get("due_time") or "").strip(),
                category=str(item.get("category") or "Личное").strip(),
                notes=str(item.get("notes") or "").strip(),
                lead_days=int(item.get("lead_days") or 0),
                notify_mode=str(item.get("notify_mode") or REPEAT_NOTIFY_MODE).strip(),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if reminder.id and reminder.title:
            reminders.append(reminder)
    return sorted(reminders, key=lambda r: (r.due_date, r.due_time, r.title))


def _due_reminders(reminders: Iterable[Reminder], today: date) -> list[Reminder]:
    due: list[Reminder] = []
    for reminder in reminders:
        notify_from = reminder.due_date - timedelta(days=max(reminder.lead_days, 0))
        if notify_from <= today:
            due.append(reminder)
    return due


def _status_line(reminder: Reminder, today: date) -> str:
    delta = (reminder.due_date - today).days
    if delta < 0:
        when = f"просрочено на {abs(delta)} дн."
    elif delta == 0:
        when = "сегодня"
    elif delta == 1:
        when = "завтра"
    else:
        when = f"через {delta} дн."
    formatted = reminder.due_date.strftime("%d.%m.%Y")
    if reminder.due_time:
        formatted += f" в {reminder.due_time}"
    return f"{formatted} — {when}"


def _build_digest(reminders: list[Reminder], today: date) -> str:
    lines = ["Nordvel: напоминания"]
    for reminder in reminders:
        lines.extend([
            "",
            f"• {reminder.title}",
            f"  Срок: {_status_line(reminder, today)}",
            f"  Категория: {reminder.category}",
        ])
        if reminder.notes:
            lines.append(f"  Заметка: {reminder.notes[:240]}")
    lines.extend(["", f"Открыть планировщик: {PLANNER_URL}"])
    return "\n".join(lines)


def _notification_key(channel: str, reminder: Reminder, today: date, run_at: datetime) -> str:
    if reminder.notify_mode == SINGLE_NOTIFY_MODE:
        slot = "single"
    elif today < reminder.due_date:
        slot = f"lead:{today.isoformat()}"
    else:
        slot = f"due:{today.isoformat()}:{run_at.hour:02d}"
    return f"{channel}:{reminder.id}:{reminder.due_date.isoformat()}:{slot}"


def _filter_unsent(
    channel: str,
    reminders: list[Reminder],
    sent_state: dict[str, str],
    today: date,
    run_at: datetime,
) -> list[Reminder]:
    return [
        reminder
        for reminder in reminders
        if _notification_key(channel, reminder, today, run_at) not in sent_state
    ]


def _mark_sent(
    channel: str,
    reminders: list[Reminder],
    sent_state: dict[str, str],
    today: date,
    run_at: datetime,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for reminder in reminders:
        sent_state[_notification_key(channel, reminder, today, run_at)] = now


def _send_telegram(text: str) -> bool:
    token = os.environ.get("CHGK_BOT_TOKEN", "").strip()
    targets = _target_ids("NORDVEL_REMINDER_TG_IDS", "CHGK_BOT_OWNER_TG_ID")
    if not token or not targets:
        return False
    ok = True
    for chat_id in targets:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            ok = False
    return ok


def _send_vk(text: str) -> bool:
    token = os.environ.get("CHGK_VK_BOT_TOKEN", "").strip()
    targets = _target_ids("NORDVEL_REMINDER_VK_IDS", "CHGK_VK_OWNER_USER_ID")
    if not token or not targets:
        return False
    ok = True
    for peer_id in targets:
        response = requests.post(
            "https://api.vk.com/method/messages.send",
            data={
                "access_token": token,
                "v": "5.199",
                "peer_id": peer_id,
                "message": text[:4000],
                "random_id": int(time.time() * 1000) % 2_147_483_647,
            },
            timeout=REQUEST_TIMEOUT,
        )
        try:
            payload = response.json()
        except ValueError:
            ok = False
            continue
        if response.status_code >= 400 or "error" in payload:
            ok = False
    return ok


def send_due_notifications(today: date | None = None, run_at: datetime | None = None) -> int:
    run_at = run_at or datetime.now()
    today = today or run_at.date()
    due = _due_reminders(_load_reminders(), today)
    if not due:
        return 0

    state = _load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    sent = state.setdefault("sent", {})
    if not isinstance(sent, dict):
        sent = {}
        state["sent"] = sent

    delivered = 0
    for channel, sender in (("telegram", _send_telegram), ("vk", _send_vk)):
        unsent = _filter_unsent(channel, due, sent, today, run_at)
        if not unsent:
            continue
        text = _build_digest(unsent, today)
        if sender(text):
            _mark_sent(channel, unsent, sent, today, run_at)
            delivered += len(unsent)

    if delivered:
        _save_json(STATE_PATH, state)
    return delivered


def main() -> None:
    run_at = datetime.now(_notification_timezone())
    if not _should_send_at(run_at):
        print(f"Nordvel reminders skipped at {run_at.strftime('%H:%M')}")
        return
    try:
        delivered = send_due_notifications(run_at=run_at)
    except Exception as exc:
        print(f"Nordvel reminder notifier failed: {exc}", file=sys.stderr)
        raise
    print(f"Nordvel reminders delivered: {delivered}")


if __name__ == "__main__":
    main()
