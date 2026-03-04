"""Оркестратор классификации: прогоняет вопросы через LLM и сохраняет результаты."""

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    CLASSIFICATION_BATCH_SIZE,
    DB_PATH,
    GROQ_MODEL,
    GROQ_RATE_LIMIT_DELAY,
    MIN_CONFIDENCE,
    OLLAMA_FALLBACK_MODEL,
    OLLAMA_MODEL,
)
from database.db import (
    get_connection,
    get_question_count,
    get_subcategory_id,
    get_unclassified_questions,
    insert_topic,
)
from classifier.classifier import classify_question
from classifier.taxonomy import get_label
from classifier.notifier import TelegramNotifier

# Попытка импорта Rich-дашборда (fallback на простой вывод)
try:
    from classifier.dashboard import ClassificationDashboard
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


# ─── Fallback: простой текстовый вывод (ANSI) ───────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"


def _truncate(text: str, max_len: int = 80) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _format_topics(topics: list) -> str:
    if not topics:
        return f"{RED}(не удалось классифицировать){RESET}"
    parts = []
    for t in topics:
        label = get_label(t["cat"], t["sub"])
        conf = t["conf"]
        color = GREEN if conf >= 0.7 else YELLOW if conf >= 0.4 else RED
        parts.append(f"{color}{label}{RESET} ({conf:.0%})")
    return " | ".join(parts)


def _progress_bar(current: int, total: int, width: int = 30) -> str:
    pct = current / total if total > 0 else 0
    filled = int(width * pct)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}] {pct:.1%}"


def _fmt_eta(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}ч {m}м"
    if m > 0:
        return f"{m}м {s}с"
    return f"{s}с"


def _format_cost(cost: float) -> str:
    if cost <= 0:
        return "бесплатно"
    return f"${cost:.2f}"


# ─── Главная функция ─────────────────────────────────────────────────

def run_classification(
    model: str = None,
    limit: int = None,
    few_shot: bool = True,
    twostage: bool = False,
    use_dashboard: bool = True,
    use_groq: bool = False,
    # Новые параметры
    provider=None,
    workers: int = 1,
    author_filter: str = None,
    source_model: str = None,
):
    """Главный цикл классификации.

    Args:
        model: Имя модели (для обратной совместимости)
        limit: Максимальное количество вопросов
        few_shot: Использовать few-shot примеры
        twostage: Двухэтапная классификация
        use_dashboard: Использовать Rich-дашборд
        use_groq: Использовать Groq API (deprecated, используйте provider)
        provider: Экземпляр BaseLLMProvider (приоритет над model/use_groq)
        workers: Количество параллельных воркеров
    """
    conn = get_connection(DB_PATH)

    # ── Создание провайдера ──
    if provider is None:
        provider = _create_legacy_provider(model, use_groq)
        if provider is None:
            return

    model_name = provider.config.model
    method = _get_method_name(provider, twostage)

    # Ограничить воркеров максимумом провайдера
    workers = min(workers, provider.config.max_concurrent)

    # Получаем неклассифицированные вопросы ДЛЯ КОНКРЕТНОЙ МОДЕЛИ
    questions = get_unclassified_questions(
        conn, limit=limit, model_name=model_name,
        author_filter=author_filter, source_model=source_model,
    )
    total = len(questions)
    total_in_db = get_question_count(conn)

    if total == 0:
        print(f"Все вопросы уже классифицированы моделью {model_name}!")
        return

    # Лог запуска
    conn.execute(
        "INSERT INTO classification_runs (method, model_name) VALUES (?, ?)",
        (method, model_name),
    )
    conn.commit()
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Telegram-уведомления
    notifier = TelegramNotifier(
        model=model_name,
        total=total,
        total_in_db=total_in_db,
        method=method,
        twostage=twostage,
        few_shot=few_shot,
    )
    notifier.start()

    # Дашборд или простой вывод
    dashboard = None
    if use_dashboard and _HAS_RICH:
        dashboard = ClassificationDashboard(
            model=model_name,
            total=total,
            total_in_db=total_in_db,
            method=method,
            twostage=twostage,
            few_shot=few_shot,
            provider=provider,
        )
        dashboard.start()
    else:
        mode_str = "двухэтапный" if twostage else "одноэтапный"
        workers_str = f", {workers} воркеров" if workers > 1 else ""
        cost_str = _format_cost(provider.estimate_total_cost(total))
        print(f"\n{BOLD}{'═' * 60}{RESET}")
        print(f"{BOLD}  Классификация ЧГК-вопросов{RESET}")
        print(f"{BOLD}{'═' * 60}{RESET}")
        print(f"  Провайдер: {CYAN}{provider.config.name}{RESET}")
        print(f"  Модель:    {CYAN}{model_name}{RESET}")
        print(f"  Режим:     {mode_str}{workers_str}")
        print(f"  Вопросов:  {BOLD}{total}{RESET} из {total_in_db} (в БД)")
        if source_model:
            print(f"  Сравнение: {CYAN}{source_model}{RESET}")
        if author_filter:
            print(f"  Автор:     {CYAN}{author_filter}{RESET}")
        print(f"  Прогноз:   {cost_str}")
        print(f"{BOLD}{'═' * 60}{RESET}\n")

    success = 0
    failed = 0
    start_time = time.time()
    interrupted = False
    counter_lock = threading.Lock()
    db_lock = threading.Lock()

    def _classify_one(i: int, q: dict) -> tuple:
        """Классифицировать один вопрос (thread-safe)."""
        q_start = time.time()
        topics = classify_question(
            provider=provider,
            text=q["text"],
            answer=q["answer"],
            comment=q.get("comment", ""),
            twostage=twostage,
            few_shot=few_shot,
        )
        q_elapsed = time.time() - q_start

        # Сохранение в БД (сериализовано через lock)
        saved_topics = []
        if topics:
            for t in topics:
                if t["conf"] < MIN_CONFIDENCE:
                    continue
                with db_lock:
                    sub_id = get_subcategory_id(conn, t["cat"], t["sub"])
                    if sub_id:
                        insert_topic(conn, q["id"], sub_id, t["conf"], method, model_name)
                saved_topics.append(t)
            with db_lock:
                conn.commit()

        return i, q, topics, saved_topics, q_elapsed

    try:
        if workers <= 1:
            # Последовательная классификация (как раньше)
            for i, q in enumerate(questions):
                i, q, topics, saved_topics, q_elapsed = _classify_one(i, q)
                _on_question_done(
                    i, q, topics, saved_topics, q_elapsed,
                    success, failed, total, start_time,
                    dashboard, notifier, conn, run_id,
                    counter_lock,
                )
                if topics:
                    success += 1
                else:
                    failed += 1
        else:
            # Параллельная классификация
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_classify_one, i, q): (i, q)
                    for i, q in enumerate(questions)
                }
                processed_count = 0
                for future in as_completed(futures):
                    i, q, topics, saved_topics, q_elapsed = future.result()
                    if topics:
                        with counter_lock:
                            success += 1
                    else:
                        with counter_lock:
                            failed += 1

                    with counter_lock:
                        processed_count += 1
                        _on_question_done(
                            processed_count - 1, q, topics, saved_topics, q_elapsed,
                            success, failed, total, start_time,
                            dashboard, notifier, conn, run_id,
                            counter_lock,
                        )

    except KeyboardInterrupt:
        interrupted = True
    finally:
        # Финализация БД
        elapsed = time.time() - start_time
        conn.execute(
            """UPDATE classification_runs
               SET finished_at = datetime('now'),
                   questions_processed = ?,
                   questions_failed = ?
               WHERE id = ?""",
            (success, failed, run_id),
        )
        conn.commit()

        # Финальный отчёт
        cost = provider.estimated_cost
        if dashboard:
            dashboard.finish(interrupted=interrupted)
        else:
            if interrupted:
                print(f"\n\n{YELLOW}Прервано пользователем (Ctrl+C){RESET}")
            processed = success + failed
            print(f"\n{BOLD}{'═' * 60}{RESET}")
            print(f"{BOLD}  Итоги{RESET}")
            print(f"{BOLD}{'═' * 60}{RESET}")
            print(f"  Провайдер: {CYAN}{provider.config.name}{RESET}")
            print(f"  Модель:    {CYAN}{model_name}{RESET}")
            print(f"  Успешно:   {GREEN}{success}{RESET}")
            print(f"  Неудачно:  {RED}{failed}{RESET}")
            if processed > 0:
                print(f"  Время:     {_fmt_eta(elapsed)} ({elapsed/processed:.1f} с/вопрос)")
            print(f"  Стоимость: {_format_cost(cost)}")
            print(f"{BOLD}{'═' * 60}{RESET}\n")

        # Telegram — финальное уведомление
        notifier.finish()
        conn.close()


def _on_question_done(
    i, q, topics, saved_topics, q_elapsed,
    success, failed, total, start_time,
    dashboard, notifier, conn, run_id,
    counter_lock,
):
    """Обработка результата одного вопроса (вывод, уведомления, лог)."""
    q_text = _truncate(q["text"], 70)

    if dashboard:
        dashboard.update({
            "index": i,
            "question_id": q["id"],
            "text": q["text"],
            "classify_time": q_elapsed,
            "topics": topics,
            "saved_topics": saved_topics,
        })
    else:
        # Fallback: простой текстовый вывод
        elapsed_total = time.time() - start_time
        processed = success + failed + 1
        speed = processed / elapsed_total if elapsed_total > 0 else 0
        eta_sec = (total - processed) / speed if speed > 0 else 0

        topics_str = _format_topics(saved_topics if topics else None)
        icon = f"{GREEN}✓{RESET}" if topics else f"{RED}✗{RESET}"

        print(f"  {icon} {DIM}#{processed}/{total}{RESET}  {q_elapsed:.1f}с  {topics_str}")
        print(f"    {DIM}{q_text}{RESET}")

        if processed % 50 == 0:
            progress = _progress_bar(processed, total)
            print(f"\n  {BOLD}── Сводка ──{RESET}")
            print(f"  {progress}")
            print(
                f"  ✅ {GREEN}{success}{RESET}  "
                f"❌ {RED}{failed}{RESET}  "
                f"⏱ {1/speed:.1f} с/вопрос  "
                f"ETA: {_fmt_eta(eta_sec)}"
            )
            print()

    # Telegram notifier
    notifier.update(
        success=success,
        failed=failed,
        current_question=q_text,
        last_category=_format_topics(saved_topics) if saved_topics else "",
    )

    # Обновляем лог запуска периодически
    processed = success + failed
    if processed > 0 and processed % CLASSIFICATION_BATCH_SIZE == 0:
        conn.execute(
            """UPDATE classification_runs
               SET questions_processed = ?, questions_failed = ?
               WHERE id = ?""",
            (success, failed, run_id),
        )
        conn.commit()


def _create_legacy_provider(model, use_groq):
    """Создать провайдер из legacy-параметров (model, use_groq)."""
    from classifier.providers import create_provider

    if use_groq:
        from config import GROQ_API_KEY
        if not GROQ_API_KEY:
            print("GROQ_API_KEY не задан! Укажите в .env или переменной окружения.")
            return None
        return create_provider("groq", model=model)

    # Ollama: проверить доступность модели
    if model is None:
        provider = create_provider("ollama", model=OLLAMA_MODEL)
        if provider.is_available():
            return provider
        provider = create_provider("ollama", model=OLLAMA_FALLBACK_MODEL)
        if provider.is_available():
            print(f"Основная модель недоступна, используем fallback: {provider.config.model}")
            return provider
        print(f"Ни одна модель не найдена в Ollama!")
        print(f"Установите модель: ollama pull {OLLAMA_MODEL}")
        return None

    return create_provider("ollama", model=model)


def _get_method_name(provider, twostage: bool) -> str:
    """Сформировать method для записи в БД."""
    name = provider.config.name
    if name == "ollama":
        return "llm_local_2stage" if twostage else "llm_local"
    return f"{name}_2stage" if twostage else name


def show_status():
    """Показать текущий статус классификации."""
    conn = get_connection(DB_PATH)
    total = get_question_count(conn)

    rows = conn.execute("""
        SELECT model_name, COUNT(DISTINCT question_id) as cnt
        FROM question_topics
        GROUP BY model_name
        ORDER BY cnt DESC
    """).fetchall()

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Статус классификации{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")
    print(f"  Всего вопросов: {BOLD}{total}{RESET}")
    print()

    classified_ids = set()
    for row in rows:
        model = row["model_name"]
        cnt = row["cnt"]
        pct = cnt / total * 100 if total > 0 else 0
        print(f"  {CYAN}{model}{RESET}: {cnt}/{total} ({pct:.1f}%)")
        # Собираем все ID для подсчёта уникальных
        ids = conn.execute(
            "SELECT DISTINCT question_id FROM question_topics WHERE model_name = ?",
            (model,),
        ).fetchall()
        classified_ids.update(r[0] for r in ids)

    if rows:
        unique = len(classified_ids)
        remaining = total - unique
        print(f"\n  Уникальных классифицированных: {BOLD}{unique}{RESET}")
        print(f"  Осталось: {BOLD}{remaining}{RESET}")
    else:
        print(f"  Ни одного вопроса не классифицировано.")

    print(f"{BOLD}{'═' * 60}{RESET}\n")
    conn.close()


def estimate_cost(provider, limit: int = None):
    """Показать оценку стоимости классификации."""
    conn = get_connection(DB_PATH)
    questions = get_unclassified_questions(
        conn, limit=limit, model_name=provider.config.model
    )
    total = len(questions)
    conn.close()

    if total == 0:
        print(f"Все вопросы уже классифицированы моделью {provider.config.model}!")
        return

    cost = provider.estimate_total_cost(total)
    cost_str = _format_cost(cost)

    # Примерное время
    if provider.config.name == "ollama":
        time_est = f"~{total * 17 / 3600:.1f} часов (1 воркер)"
    else:
        workers = provider.config.max_concurrent
        time_est = f"~{total * 0.5 / workers / 60:.0f} минут ({workers} воркеров)"

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Оценка стоимости{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")
    print(f"  Провайдер: {CYAN}{provider.config.name}{RESET}")
    print(f"  Модель:    {CYAN}{provider.config.model}{RESET}")
    print(f"  Вопросов:  {BOLD}{total}{RESET}")
    print()
    print(f"  Примерные токены:")
    print(f"    Input:   ~{total * 1600 / 1_000_000:.1f}M ({total} * 1600 avg)")
    print(f"    Output:  ~{total * 40 / 1_000_000:.1f}M ({total} * 40 avg)")
    print()
    print(f"  Стоимость: {BOLD}{cost_str}{RESET}")
    if provider.config.cost_per_1m_input > 0:
        print(f"    Input:   ${total * 1600 * provider.config.cost_per_1m_input / 1_000_000:.2f}")
        print(f"    Output:  ${total * 40 * provider.config.cost_per_1m_output / 1_000_000:.2f}")
    print(f"  Время:     {time_est}")
    print(f"{BOLD}{'═' * 60}{RESET}\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Классификация ЧГК-вопросов")
    parser.add_argument("--model", type=str, default=None, help="Модель Ollama")
    parser.add_argument("--limit", type=int, default=None, help="Макс. вопросов")
    parser.add_argument("--no-few-shot", action="store_true", help="Без few-shot")
    parser.add_argument("--twostage", action="store_true", help="Двухэтапная классификация")
    parser.add_argument("--no-dashboard", action="store_true", help="Без Rich-дашборда")
    args = parser.parse_args()

    run_classification(
        model=args.model,
        limit=args.limit,
        few_shot=not args.no_few_shot,
        twostage=args.twostage,
        use_dashboard=not args.no_dashboard,
    )
