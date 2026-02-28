# Оптимизация CHGK: скорость, качество, мультипровайдеры

**Дата:** 2026-02-28
**Статус:** Выполнен

## Контекст

Проект классифицирует ~37,500 вопросов ЧГК по 14 категориям / 52 подкатегориям через LLM.
Нужно было: ускорить классификацию, добавить поддержку платных моделей (OpenAI, Anthropic, Google),
улучшить архитектуру (убрать дублирование кода).

## План

### Фаза 1: Provider Abstraction Layer
- Создать `classifier/providers/` с абстрактным `BaseLLMProvider`
- Реализовать 5 провайдеров: Ollama, Groq, OpenAI, Anthropic, Google
- Единая функция классификации `classifier/classifier.py`
- Фабрика `create_provider("openai", model="gpt-4o-mini")`

### Фаза 2: Параллельная классификация
- `ThreadPoolExecutor` в `runner.py` с `--workers N`
- Thread-safe SQLite (lock), thread-safe dashboard

### Фаза 3: CLI и оценка стоимости
- `--provider`, `--workers`, `--estimate-cost`, `--status`
- Live cost tracking в Rich-дашборде

## Затронутые файлы

**Новые:**
- `classifier/providers/base.py` — BaseLLMProvider + ProviderConfig
- `classifier/providers/ollama_provider.py`
- `classifier/providers/groq_provider.py`
- `classifier/providers/openai_provider.py`
- `classifier/providers/anthropic_provider.py`
- `classifier/providers/google_provider.py`
- `classifier/providers/__init__.py` — фабрика + пресеты
- `classifier/classifier.py` — единая classify_question()
- `.env.example`

**Изменённые:**
- `config.py` — OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY
- `classifier/runner.py` — параллелизм, провайдеры, show_status(), estimate_cost()
- `classifier/dashboard.py` — provider info, cost display, thread-safety
- `classifier/local_llm.py` — пометка deprecated
- `scripts/classify.py` — новый CLI
- `requirements.txt` — +openai, anthropic, google-generativeai

## Результат

Реализовано:
- 5 LLM-провайдеров с единым интерфейсом
- Параллельная классификация (--workers N)
- Оценка стоимости перед запуском (--estimate-cost)
- Отображение прогресса (--status)
- Live cost tracking в дашборде
- Полная обратная совместимость
