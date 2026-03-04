"""Benchmark: классификация 140 вопросов из docs/примерчгк.md через LLM-провайдер.

Использование:
    python -m scripts.benchmark_examples --provider openrouter
    python -m scripts.benchmark_examples --provider openrouter --model google/gemini-2.5-flash
    python -m scripts.benchmark_examples --provider google --model gemini-2.0-flash
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Загрузка .env без зависимости от python-dotenv
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    import os
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from classifier.classifier import classify_question
from classifier.providers import create_provider
from database.seed_taxonomy import TAXONOMY


def get_category_name(cat: int) -> str:
    """Получить русское название категории по номеру."""
    if 1 <= cat <= len(TAXONOMY):
        return TAXONOMY[cat - 1][1]
    return f"Unknown({cat})"


def get_subcategory_name(cat: int, sub: int) -> str:
    """Получить русское название подкатегории."""
    if 1 <= cat <= len(TAXONOMY):
        subs = TAXONOMY[cat - 1][2]
        if 1 <= sub <= len(subs):
            return subs[sub - 1][1]
    return f"Unknown({sub})"


def parse_questions(filepath: str) -> list:
    """Парсинг вопросов из docs/примерчгк.md.

    Возвращает список словарей:
    [{"num": 1, "section": "Спорт", "answer": "...", "text": "...", "comment": "..."}, ...]
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    questions = []
    current_section = ""

    # Разделяем на блоки по ---
    blocks = re.split(r"\n---\n", content)

    for block in blocks:
        # Определяем секцию
        section_match = re.search(r"^## (.+)$", block, re.MULTILINE)
        if section_match:
            current_section = section_match.group(1).strip()

        # Ищем заголовок вопроса
        q_match = re.search(r"^### (\d+)\.\s+\(#\d+\)", block, re.MULTILINE)
        if not q_match:
            continue

        num = int(q_match.group(1))

        # Ответ
        answer_match = re.search(r"^\*\*Ответ:\*\*\s*(.+)$", block, re.MULTILINE)
        answer = answer_match.group(1).strip() if answer_match else ""

        # Текст вопроса (blockquote lines)
        text_lines = []
        for line in block.split("\n"):
            if line.startswith("> "):
                text_lines.append(line[2:])
            elif line.startswith(">") and text_lines:
                text_lines.append(line[1:].lstrip())
        text = " ".join(text_lines).strip()

        # Комментарий
        comment_match = re.search(
            r"\*Комментарий:\*\s*(.+?)(?=\n\n|\[Источник\]|\Z)",
            block,
            re.DOTALL,
        )
        comment = comment_match.group(1).strip() if comment_match else ""

        questions.append({
            "num": num,
            "section": current_section,
            "answer": answer,
            "text": text,
            "comment": comment,
        })

    return questions


def format_classification(topics: list) -> str:
    """Форматировать результат классификации в читаемую строку.

    Пример: 'Спорт (8)' или 'Спорт (8) + История (1)'
    """
    parts = []
    for t in topics:
        cat_name = get_category_name(t["cat"])
        parts.append(f"{cat_name} ({t['cat']})")
    return " + ".join(parts)


def format_classification_detailed(topics: list) -> str:
    """Форматировать результат с подкатегориями.

    Пример: 'Спорт (8.2 — Олимпийские виды спорта), conf 0.9'
    """
    parts = []
    for t in topics:
        cat_name = get_category_name(t["cat"])
        sub_name = get_subcategory_name(t["cat"], t["sub"])
        parts.append(f"{cat_name} ({t['cat']}.{t['sub']} — {sub_name}), conf {t['conf']}")
    return "; ".join(parts)


def run_benchmark(provider_name: str, model: str = None, twostage: bool = True):
    """Запустить benchmark на 140 вопросах."""
    # Парсим вопросы
    questions_file = Path(__file__).parent.parent / "docs" / "примерчгк.md"
    questions = parse_questions(str(questions_file))
    print(f"Распарсено вопросов: {len(questions)}")

    if len(questions) != 140:
        print(f"ВНИМАНИЕ: ожидалось 140 вопросов, получено {len(questions)}")

    # Создаём провайдер
    provider = create_provider(provider_name, model=model)
    model_name = provider.config.model
    print(f"Провайдер: {provider_name}, модель: {model_name}")
    print(f"Двухэтапная классификация: {twostage}")
    print()

    # Классифицируем
    results = []
    failed = 0
    start_time = time.time()

    for i, q in enumerate(questions):
        print(f"[{i+1}/{len(questions)}] Вопрос {q['num']}: ", end="", flush=True)

        try:
            topics = classify_question(
                provider,
                text=q["text"],
                answer=q["answer"],
                comment=q["comment"],
                twostage=twostage,
            )
        except Exception as e:
            print(f"ОШИБКА: {e}")
            topics = None
            failed += 1

        if topics:
            short = format_classification(topics)
            print(short)
            results.append({**q, "topics": topics, "short": short})
        else:
            print("FAILED (None)")
            results.append({**q, "topics": None, "short": "FAILED"})
            failed += 1

        # Небольшая пауза чтобы не превышать rate limits
        time.sleep(0.3)

    elapsed = time.time() - start_time
    print(f"\nГотово за {elapsed:.1f}с. Успешно: {len(results) - failed}/{len(results)}, ошибок: {failed}")

    # Сохраняем результаты
    safe_model_name = model_name.replace("/", "-").replace(":", "-")
    output_md = Path(__file__).parent.parent / "docs" / f"классификация_{safe_model_name}.md"
    output_json = Path(__file__).parent.parent / "docs" / f"классификация_{safe_model_name}.json"

    # JSON для машинной обработки
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"JSON: {output_json}")

    # Markdown для чтения
    write_markdown(results, model_name, output_md, elapsed)
    print(f"Markdown: {output_md}")

    return results


def write_markdown(results: list, model_name: str, output_path: Path, elapsed: float):
    """Записать результаты в markdown-файл."""
    lines = [
        f"# Независимая классификация 140 вопросов — {model_name}",
        "",
        f"Классификация выполнена автоматически через API ({model_name}).",
        "Формат: `cat` — номер категории, `sub` — номер подкатегории, `conf` — уверенность.",
        f"Время: {elapsed:.1f}с.",
        "",
        "**Таксономия:**",
        " | ".join(f"{i}. {TAXONOMY[i-1][1]}" for i in range(1, 15)),
        "",
        "---",
        "",
    ]

    for r in results:
        lines.append(f"## Вопрос {r['num']} ({r['section']})")

        if r["topics"]:
            detailed = format_classification_detailed(r["topics"])
            lines.append(f"**Классификация:** {detailed}")
        else:
            lines.append("**Классификация:** FAILED")

        # Краткий текст вопроса (первые 100 символов)
        short_text = r["text"][:100] + "..." if len(r["text"]) > 100 else r["text"]
        lines.append(f"> {short_text}")
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark: classify 140 questions")
    parser.add_argument("--provider", default="openrouter", help="Provider name")
    parser.add_argument("--model", default=None, help="Model name (default from preset)")
    parser.add_argument("--onestage", action="store_true", help="Use one-stage classification")
    args = parser.parse_args()

    run_benchmark(
        provider_name=args.provider,
        model=args.model,
        twostage=not args.onestage,
    )
