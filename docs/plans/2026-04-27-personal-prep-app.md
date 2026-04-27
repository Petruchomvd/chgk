# Личный тренажёр для подготовки к турнирам ЧГК (Telegram-бот)

**Дата:** 2026-04-27
**Статус:** Выполнен (v1)

## Контекст

Проект пивотнул с командных PDF-отчётов на персональную подготовку Матвея к турнирам. Цель — рабочий продукт, который тренирует **обе** ЧГК-мышцы:
- эрудиция (знания) — статьи по темам, заточенные под ЧГК-частотность;
- логика/раскрутка — тренировка вопросами с самопроверкой и спейсд-репетишн.

Решение: **Telegram-бот, запускаемый локально на ноуте**. VPS пока не делаем — когда продукт станет «рабочим» в смысле полезности, перенесём на VPS. Бот доступен только когда ноут включён, это ограничение принято.

Дашборд (`dashboard/`) **не трогаем и не развиваем** — заморожен.

## Архитектура: движок отдельно от UI

Чтобы при будущем переезде на VPS / при возможной замене UI ничего не переписывать, бизнес-логика выносится в `app/`-модуль, не зависящий от Streamlit/aiogram:

```
chgk/
├── app/                          NEW — UI-agnostic движок
│   ├── __init__.py
│   ├── training_engine.py        тренировочная сессия + Leitner
│   └── study_engine.py           генерация статей через OpenRouter
├── database/
│   └── training_db.py            NEW — training.db (attempts, leitner)
├── bot/                          NEW — слой aiogram
│   ├── __init__.py
│   ├── main.py                   точка входа
│   ├── keyboards.py              inline-клавиатуры
│   ├── states.py                 FSM-стейты
│   └── handlers/
│       ├── __init__.py
│       ├── common.py             /start /help /stats
│       ├── training.py           /train flow
│       └── study.py              /study /studies
├── studies/                      NEW — кэш сгенерированных статей
└── data/                         БД, источники
```

## training.db (новая БД)

```sql
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL,
    attempted_at TEXT NOT NULL,
    user_answer TEXT,
    knew INTEGER NOT NULL,           -- 0/1, самооценка
    time_seconds REAL,
    mode TEXT,                       -- 'category'|'tournament'|'random'|'gentleman'|'review'
    category TEXT
);

CREATE TABLE leitner (
    question_id INTEGER PRIMARY KEY,
    box INTEGER NOT NULL,            -- 1..5
    next_review_at TEXT NOT NULL,    -- ISO timestamp
    last_attempt_at TEXT,
    consecutive_correct INTEGER DEFAULT 0
);
```

**Leitner-интервалы:** box 1 → +1d, box 2 → +3d, box 3 → +7d, box 4 → +14d, box 5 → +30d.
Промах → сброс в box 1 (+1d). Попадание → box+=1, max 5.

## Бот: команды и сценарии

### Команды

- `/start` — приветствие + проверка авторизации (whitelist user_id).
- `/train` — старт тренировки, выбор режима:
  - случайные / по категории / по турниру / джентльменский набор / очередь повторений.
- `/study <тема>` — сгенерировать или показать кэш статьи.
- `/studies` — список ранее сгенерированных статей.
- `/stats` — статистика прогресса.
- `/cancel` — выйти из текущего сценария.

### Сценарий тренировки (FSM)

1. `/train` → inline-кнопки выбора режима.
2. После выбора режима — фильтры (категория/турнир/...) inline-кнопками.
3. Кнопка «Начать N вопросов» — выгружаем пачку.
4. Для каждого вопроса:
   - Сообщение с текстом вопроса (+ раздатка, если есть).
   - Поле ответа — пользователь пишет текстом.
   - Кнопка «Сдаться/показать ответ».
   - После ответа: правильный ответ + комментарий + источник.
   - Inline-кнопки «Знал ✅» / «Не знал ❌».
   - Запись в `attempts` + обновление `leitner`.
5. После N вопросов — отчёт по категориям + кнопка «Ещё».

### Сценарий «Знания»

1. `/study Магритт` → проверка кэша в `studies/magritt.md`.
2. Если нет — генерация:
   - SQL-поиск 30+ вопросов по теме (text/answer/comment LIKE).
   - Опционально подтянуть кратко Wikipedia.
   - LLM (`openai/gpt-4o-mini` через OpenRouter) → статья 600–1000 слов.
   - Сохранение в `studies/<slug>.md`.
3. Отправка статьи в чат (split на куски по 4096 символов).

## Авторизация

В `.env` добавить `CHGK_BOT_OWNER_TG_ID=<id>`. Бот игнорирует все сообщения не от владельца. На первом `/start` от неизвестного user_id бот отвечает «твой ID: X, добавь в .env».

## Реюз существующего бота

Используем `TG_DIGEST_BOT_TOKEN`. Скрипт `scripts/tg_bot_digest.py` останавливаем (фактически не использовался регулярно). Бот тот же, функционал меняется с постилки на интерактив.

## Зависимости

Добавить в `requirements.txt`:
- `aiogram>=3.4.0`

## Запуск

```powershell
python -m bot.main
```

Запускается из терминала, пока ноут включён. Логи в stdout.

## Затронутые файлы

- `app/training_engine.py` (новый)
- `app/study_engine.py` (новый)
- `database/training_db.py` (новый)
- `bot/main.py` (новый)
- `bot/keyboards.py` (новый)
- `bot/states.py` (новый)
- `bot/handlers/common.py` (новый)
- `bot/handlers/training.py` (новый)
- `bot/handlers/study.py` (новый)
- `requirements.txt` (+aiogram)
- `.env` (+CHGK_BOT_OWNER_TG_ID)
- `studies/` (новый каталог)
- `dashboard/training.py` — **НЕ трогаем**, замораживаем.
- `scripts/tg_bot_digest.py` — **НЕ удаляем**, оставляем как референс.

## Ограничения v1

- Бот живёт только когда ноут онлайн.
- AI-проверка ответа отложена (ставим только самопроверку).
- Wikipedia в статьях — опционально, можно отключить если медленно.
- Один пользователь (Матвей).

## Следующие шаги после v1

- VPS + перенос (миграция БД, systemd-сервис, deploy.sh).
- AI-оценка ответа.
- Спейсд-репетишн для тем (не только вопросов) — ревью статей.
- Аналитика паттернов промахов (на каких триггерах валишься).

## Результат

v1 реализован 2026-04-27.

**Создано:**
- `database/training_db.py` — БД пользовательского прогресса (attempts + leitner с 5 коробками).
- `app/training_engine.py` — UI-agnostic движок: 4 режима (случайный/категория/турнир/повторения), поиск турниров по названию или ID, итоги сессии.
- `app/study_engine.py` — генератор статей: ищет 25 вопросов про тему в БД, отправляет в `openai/gpt-4o-mini` через OpenRouter, сохраняет в `studies/<slug>.md`.
- `bot/states.py`, `bot/keyboards.py` — FSM и inline-клавиатуры.
- `bot/handlers/common.py` — `/start`, `/help`, `/menu`, `/cancel`, `/stats`.
- `bot/handlers/training.py` — `/train` flow с режимами и FSM.
- `bot/handlers/study.py` — `/study <тема>`, `/studies`.
- `bot/main.py` — точка входа, авторизация владельца через `CHGK_BOT_OWNER_TG_ID`.

**Проверено:**
- `getMe` → токен валиден, бот `@chgk_korolevskieosobi_bot`.
- `python -m bot.main` стартует, polling работает (warn про owner_id ожидаемый).
- Импорты, dispatcher с 3 роутерами компонуется.
- training_engine: random + tournament search smoke-test пройден.
- study_engine: поиск вопросов по теме (`Магритт` → 10 находок) пройден без LLM-вызова.

**Что осталось пользователю:**
1. Узнать свой Telegram ID: запустить `python -m bot.main`, написать `/start` боту, скопировать ID из ответа.
2. Добавить в `.env`: `CHGK_BOT_OWNER_TG_ID=<id>` и перезапустить — бот станет личным.
3. Обкатать `/train`, `/study`, `/stats`. Сообщить, что бесит / что мешает.

**Следующие шаги (вне v1):**
- AI-проверка ответа (LLM решает, считать ли «сан-Антоний» равным «Святой Антоний»).
- Перенос на VPS (когда продукт пройдёт «тест полезностью»).
- Аналитика паттернов промахов через LLM.
- Спейсд-репетишн для статей (а не только вопросов).
