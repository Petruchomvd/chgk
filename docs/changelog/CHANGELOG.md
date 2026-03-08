# Changelog

Все значимые изменения проекта документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

---

## [2026-03-07] — Ручная проверка спорных вопросов

### Добавлено

- **`scripts/review_disputed.py`** — скрипт автоматического отбора спорных вопросов для ручной проверки
  - Система dispute_score: низкая уверенность, близкие confidence, confusion-пары, Логика как primary
  - Фильтры по категории, режиму (all/confused/low_conf), лимиту
  - Генерация Markdown-файлов для удобной проверки
- Выгрузки спорных вопросов в `output/`:
  - `review_disputed.md` — 150 самых спорных вопросов
  - `review_disputed_Логика.md`, `review_disputed_Кино.md`, `review_disputed_Быт.md` — по 50 вопросов

### Анализ

- Ручная проверка 150 вопросов из 3 категорий (qwen/qwen-2.5-72b-instruct, 2774 вопроса)
- Результаты: Логика 78%, Кино 72%, Быт 44%
- Быт — главная проблема: модель ставит категорию по поверхностным объектам, игнорируя контекст
- Подробности: `docs/plans/2026-03-07-disputed-questions-review.md`

---

## [2026-03-05] — Турнир IQ ПФО + поиск по автору

### Добавлено

- **Вкладка «Турнир IQ ПФО»** в дашборде — подготовка к турниру в Саранске:
  - Обзор: сводка по 10 авторам (вопросов, классифицировано, покрытие)
  - Профили авторов: радар-чарт + таблица по каждому автору
  - Сравнение с глобальным: grouped bar chart + дельта-таблица
  - Тренировка: встроенный квиз по вопросам авторов турнира
  - Браузер вопросов: отфильтрован по авторам турнира
- **Поиск по автору** в Аналитика → Вопросы (текстовый фильтр)
- `dashboard/tournament.py` — новый модуль
- `comparison_bar_chart()` в `dashboard/components.py`
- Мульти-автор фильтрация (`author_filters: List[str]`) в `db_queries.py` и `training_queries.py`

---

## [2026-02-28] — Мульти-провайдер, Джентльменский набор, Git LFS

### Добавлено

- **Мульти-провайдерная архитектура** — поддержка 5 LLM-провайдеров:
  - Ollama (локальный), Groq, OpenAI, Anthropic, Google Gemini
  - Единый интерфейс `classify_question()` для всех провайдеров
  - Автоматическая оценка стоимости API-вызовов
  - Параллельная классификация с `--workers N`
- `**scripts/classify_md.py`** — классификация вопросов из .md файлов
  - Парсинг нумерованных вопросов (`N. текст`)
  - Запись результата в блок-цитату `> **Классификация:** ...`
- **«Джентльменский набор»** — анализ частых ответов ЧГК:
  - `scripts/analyze_answers.py` — NER (natasha) + лемматизация (pymorphy3)
  - Извлечение именованных сущностей: люди, места, организации
  - Частотность ключевых слов и биграмм
  - Предвычисление в `data/gentleman_set/*.json`
- **Страница «Джентльменский набор» в дашборде** — 5 вкладок:
  - Люди (PER), Места (LOC), Организации (ORG), Ключевые слова, Биграммы
  - Горизонтальные bar chart + таблицы + drill-down до вопросов
- **Git LFS** для `.db` файлов (`.gitattributes`)

### Изменено

- `config.py` — `MIN_CONFIDENCE` с 0.3 → 0.4
- `requirements.txt` — добавлены `natasha>=1.6.0`, `pymorphy3>=2.0.0`
- `dashboard/components.py` — добавлен `gentleman_bar_chart()`
- `dashboard/db_queries.py` — добавлен `get_questions_by_ids()`

### Новые файлы


| Файл                         | Описание                                                          |
| ---------------------------- | ----------------------------------------------------------------- |
| `classifier/providers/`      | 7 файлов: base, ollama, groq, openai, anthropic, google, **init** |
| `classifier/classifier.py`   | Единый classify_question()                                        |
| `classifier/runner.py`       | Параллельная классификация                                        |
| `scripts/classify.py`        | Новый CLI                                                         |
| `scripts/classify_md.py`     | Классификация из .md                                              |
| `scripts/analyze_answers.py` | Анализ ответов (NER + леммы)                                      |
| `data/gentleman_set/*.json`  | Предвычисленные результаты анализа                                |
| `.gitattributes`             | Git LFS для .db                                                   |
| `.env.example`               | Шаблон переменных окружения                                       |


### Коммиты

- `318190d` — Add multi-provider architecture + classify_md script
- `90e4a4d` — Add Gentleman's Set: NER + keyword analysis of CHGK answers
- `48729be` — Migrate .db to Git LFS for large file support

