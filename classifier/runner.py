"""Оркестратор классификации: прогоняет вопросы через LLM и сохраняет результаты."""

import sys
import time
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
from classifier.local_llm import (
    check_model_available,
    classify_question,
    classify_question_groq,
    classify_question_twostage,
    classify_question_twostage_groq,
)
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


# ─── Главная функция ─────────────────────────────────────────────────

def run_classification(
    model: str = None,
    limit: int = None,
    few_shot: bool = True,
    twostage: bool = False,
    use_dashboard: bool = True,
    use_groq: bool = False,
):
    """Главный цикл классификации."""
    conn = get_connection(DB_PATH)

    # Выбор модели
    if use_groq:
        from config import GROQ_API_KEY
        if not GROQ_API_KEY:
            print("GROQ_API_KEY не задан! Укажите в .env или переменной окружения.")
            return
        if model is None:
            model = GROQ_MODEL
    elif model is None:
        if check_model_available(OLLAMA_MODEL):
            model = OLLAMA_MODEL
        elif check_model_available(OLLAMA_FALLBACK_MODEL):
            model = OLLAMA_FALLBACK_MODEL
            print(f"Основная модель недоступна, используем fallback: {model}")
        else:
            print(f"Ни одна модель не найдена в Ollama!")
            print(f"Установите модель: ollama pull {OLLAMA_MODEL}")
            return

    # Получаем неклассифицированные вопросы ДЛЯ КОНКРЕТНОЙ МОДЕЛИ
    questions = get_unclassified_questions(conn, limit=limit, model_name=model)
    total = len(questions)
    total_in_db = get_question_count(conn)

    if total == 0:
        print(f"Все вопросы уже классифицированы моделью {model}!")
        return

    # Лог запуска
    if use_groq:
        method = "groq_2stage" if twostage else "groq"
    else:
        method = "llm_local_2stage" if twostage else "llm_local"
    conn.execute(
        "INSERT INTO classification_runs (method, model_name) VALUES (?, ?)",
        (method, model),
    )
    conn.commit()
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Telegram-уведомления
    notifier = TelegramNotifier(
        model=model,
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
            model=model,
            total=total,
            total_in_db=total_in_db,
            method=method,
            twostage=twostage,
            few_shot=few_shot,
        )
        dashboard.start()
    else:
        mode_str = "двухэтапный" if twostage else "одноэтапный"
        print(f"\n{BOLD}{'═' * 60}{RESET}")
        print(f"{BOLD}  Классификация ЧГК-вопросов{RESET}")
        print(f"{BOLD}{'═' * 60}{RESET}")
        print(f"  Модель:  {CYAN}{model}{RESET}")
        print(f"  Режим:   {mode_str}")
        print(f"  Вопросов: {BOLD}{total}{RESET} из {total_in_db} (в БД)")
        print(f"{BOLD}{'═' * 60}{RESET}\n")

    success = 0
    failed = 0
    start_time = time.time()
    interrupted = False

    try:
        for i, q in enumerate(questions):
            q_start = time.time()

            # ── Классификация ──
            if use_groq:
                if twostage:
                    topics = classify_question_twostage_groq(
                        text=q["text"],
                        answer=q["answer"],
                        comment=q.get("comment", ""),
                        model=model,
                    )
                else:
                    topics = classify_question_groq(
                        text=q["text"],
                        answer=q["answer"],
                        comment=q.get("comment", ""),
                        model=model,
                        few_shot=few_shot,
                    )
            elif twostage:
                topics = classify_question_twostage(
                    text=q["text"],
                    answer=q["answer"],
                    comment=q.get("comment", ""),
                    model=model,
                )
            else:
                topics = classify_question(
                    text=q["text"],
                    answer=q["answer"],
                    comment=q.get("comment", ""),
                    model=model,
                    few_shot=few_shot,
                )

            q_elapsed = time.time() - q_start

            # ── Сохранение ──
            saved_topics = []
            if topics:
                for t in topics:
                    if t["conf"] < MIN_CONFIDENCE:
                        continue
                    sub_id = get_subcategory_id(conn, t["cat"], t["sub"])
                    if sub_id:
                        insert_topic(conn, q["id"], sub_id, t["conf"], method, model)
                        saved_topics.append(t)
                conn.commit()
                success += 1
            else:
                failed += 1

            # ── Вывод ──
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
                speed = (i + 1) / elapsed_total if elapsed_total > 0 else 0
                eta_sec = (total - i - 1) / speed if speed > 0 else 0

                topics_str = _format_topics(saved_topics if topics else None)
                icon = f"{GREEN}✓{RESET}" if topics else f"{RED}✗{RESET}"

                print(f"  {icon} {DIM}#{i+1}/{total}{RESET}  {q_elapsed:.1f}с  {topics_str}")
                print(f"    {DIM}{q_text}{RESET}")

                if (i + 1) % 50 == 0:
                    progress = _progress_bar(i + 1, total)
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

            # Rate limit пауза для Groq (free tier ~30 req/min)
            if use_groq and i < len(questions) - 1:
                time.sleep(GROQ_RATE_LIMIT_DELAY)

            # Обновляем лог запуска периодически
            if (i + 1) % CLASSIFICATION_BATCH_SIZE == 0:
                conn.execute(
                    """UPDATE classification_runs
                       SET questions_processed = ?, questions_failed = ?
                       WHERE id = ?""",
                    (success, failed, run_id),
                )
                conn.commit()

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
        if dashboard:
            dashboard.finish(interrupted=interrupted)
        else:
            if interrupted:
                print(f"\n\n{YELLOW}Прервано пользователем (Ctrl+C){RESET}")
            processed = success + failed
            print(f"\n{BOLD}{'═' * 60}{RESET}")
            print(f"{BOLD}  Итоги{RESET}")
            print(f"{BOLD}{'═' * 60}{RESET}")
            print(f"  Модель:    {CYAN}{model}{RESET}")
            print(f"  Успешно:   {GREEN}{success}{RESET}")
            print(f"  Неудачно:  {RED}{failed}{RESET}")
            if processed > 0:
                print(f"  Время:     {_fmt_eta(elapsed)} ({elapsed/processed:.1f} с/вопрос)")
            print(f"{BOLD}{'═' * 60}{RESET}\n")

        # Telegram — финальное уведомление
        notifier.finish()
        conn.close()


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
