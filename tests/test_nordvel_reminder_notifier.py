from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path


def _load_module(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NORDVEL_REMINDERS_PATH", str(tmp_path / "reminders.json"))
    monkeypatch.setenv("NORDVEL_REMINDER_STATE_PATH", str(tmp_path / "state.json"))
    spec = importlib.util.spec_from_file_location(
        "nordvel_reminder_notifier",
        Path(__file__).parents[1] / "scripts" / "nordvel_reminder_notifier.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_due_reminders_use_lead_days(tmp_path, monkeypatch):
    module = _load_module(tmp_path, monkeypatch)
    reminders = [
        module.Reminder("1", "Кредитка", date(2026, 7, 24), "", "Финансы", "", 3, "due_day_repeat"),
        module.Reminder("2", "Сервер", date(2026, 7, 30), "", "Сервер", "", 3, "due_day_repeat"),
    ]

    due = module._due_reminders(reminders, date(2026, 7, 22))

    assert [item.title for item in due] == ["Кредитка"]


def test_digest_contains_due_status_and_planner_link(tmp_path, monkeypatch):
    module = _load_module(tmp_path, monkeypatch)
    text = module._build_digest(
        [
            module.Reminder(
                "1",
                "Оплатить сервер",
                date(2026, 7, 24),
                "09:00",
                "Сервер",
                "HOSTKEY",
                7,
                "due_day_repeat",
            )
        ],
        date(2026, 7, 23),
    )

    assert "Оплатить сервер" in text
    assert "завтра" in text
    assert "https://nordvel.ru/planner.html" in text


def test_send_due_notifications_marks_each_channel_once(tmp_path, monkeypatch):
    module = _load_module(tmp_path, monkeypatch)
    reminders_path = Path(module.REMINDERS_PATH)
    reminders_path.write_text(
        json.dumps(
            [
                {
                    "id": "abc",
                    "title": "Вернуть деньги на кредитку",
                    "due_date": "2026-07-24",
                    "category": "Финансы",
                    "lead_days": 3,
                    "notify_mode": "due_day_repeat",
                    "done": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    sent_messages: list[str] = []
    monkeypatch.setattr(module, "_send_telegram", lambda text: sent_messages.append(f"tg:{text}") or True)
    monkeypatch.setattr(module, "_send_vk", lambda text: sent_messages.append(f"vk:{text}") or True)

    first = module.send_due_notifications(date(2026, 7, 23), datetime(2026, 7, 23, 9, 0))
    second = module.send_due_notifications(date(2026, 7, 23), datetime(2026, 7, 23, 9, 30))

    assert first == 2
    assert second == 0
    assert len(sent_messages) == 2
    state = json.loads(Path(module.STATE_PATH).read_text(encoding="utf-8"))
    assert "telegram:abc:2026-07-24:lead:2026-07-23" in state["sent"]
    assert "vk:abc:2026-07-24:lead:2026-07-23" in state["sent"]


def test_due_day_repeat_mode_can_send_in_multiple_day_slots(tmp_path, monkeypatch):
    module = _load_module(tmp_path, monkeypatch)
    Path(module.REMINDERS_PATH).write_text(
        json.dumps(
            [
                {
                    "id": "abc",
                    "title": "Оплатить сервер",
                    "due_date": "2026-07-24",
                    "category": "Сервер",
                    "lead_days": 0,
                    "notify_mode": "due_day_repeat",
                    "done": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    sent_messages: list[str] = []
    monkeypatch.setattr(module, "_send_telegram", lambda text: sent_messages.append(f"tg:{text}") or True)
    monkeypatch.setattr(module, "_send_vk", lambda text: sent_messages.append(f"vk:{text}") or True)

    morning = module.send_due_notifications(date(2026, 7, 24), datetime(2026, 7, 24, 9, 0))
    same_slot = module.send_due_notifications(date(2026, 7, 24), datetime(2026, 7, 24, 9, 30))
    evening = module.send_due_notifications(date(2026, 7, 24), datetime(2026, 7, 24, 18, 0))

    assert morning == 2
    assert same_slot == 0
    assert evening == 2
    assert len(sent_messages) == 4


def test_notification_hours_are_configurable(tmp_path, monkeypatch):
    module = _load_module(tmp_path, monkeypatch)

    assert module._should_send_at(datetime(2026, 7, 24, 9, 15))
    assert not module._should_send_at(datetime(2026, 7, 24, 10, 15))

    monkeypatch.setenv("NORDVEL_REMINDER_HOURS", "8, 20")
    assert module._should_send_at(datetime(2026, 7, 24, 20, 0))
    assert not module._should_send_at(datetime(2026, 7, 24, 21, 0))
