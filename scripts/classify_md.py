"""Классификация вопросов из .md файла через LLM.

Парсит markdown-файл с нумерованными вопросами ЧГК, классифицирует каждый
и дописывает результат рядом с вопросом.

Использование:
    python scripts/classify_md.py docs/клод.md
    python scripts/classify_md.py docs/клод.md --provider openai
    python scripts/classify_md.py docs/клод.md --output docs/клод_classified.md
    python scripts/classify_md.py docs/клод.md --dry-run
"""

import re
import sys
from pathlib import Path

# Фикс кодировки консоли Windows (cp1251 не поддерживает все символы)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: F401 — загрузит .env

from classifier.classifier import classify_question as classify_q
from classifier.taxonomy import get_label


# Паттерн нумерованного вопроса: "1. текст" или "1. **заголовок** текст"
_QUESTION_RE = re.compile(r"^\s*(\d+)\.\s+")


def extract_questions(md_text: str) -> list:
    """Извлечь нумерованные вопросы из markdown-текста.

    Находит строки вида "N. текст вопроса..." и собирает текст
    до пустой строки, разделителя (---) или следующего вопроса.

    Возвращает список dict: {num, text, line_idx}
    """
    questions = []
    lines = md_text.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _QUESTION_RE.match(line)
        if m:
            num = int(m.group(1))

            # Текст вопроса: убираем "N. " и возможный **заголовок**
            first_line = line[m.end():].strip()
            # Убираем **[...](...)**  или **текст** в начале
            first_line = re.sub(r"^\*\*.*?\*\*\s*", "", first_line).strip()

            text_parts = []
            if first_line:
                text_parts.append(first_line)

            # Читаем следующие строки до конца блока
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if not next_line or next_line.startswith("#") or next_line.startswith("---"):
                    break
                if _QUESTION_RE.match(next_line):
                    break
                # Пропускаем строки с существующей классификацией
                if next_line.startswith("> **Классификация"):
                    j += 1
                    continue
                text_parts.append(next_line)
                j += 1

            full_text = " ".join(text_parts).strip()
            if full_text:
                questions.append({
                    "num": num,
                    "text": full_text,
                    "line_idx": i,
                })

        i += 1

    return questions


def format_classification(topics: list) -> str:
    """Форматировать результат классификации для markdown."""
    if not topics:
        return "> **Классификация:** не удалось классифицировать"

    parts = []
    for t in topics:
        label = get_label(t["cat"], t["sub"])
        conf = t["conf"]
        parts.append(f"{label} ({conf:.0%})")

    return f"> **Классификация:** {' | '.join(parts)}"


def classify_md_file(
    input_path: str,
    output_path: str = None,
    provider_name: str = "ollama",
    model: str = None,
    api_key: str = None,
    dry_run: bool = False,
):
    """Классифицировать вопросы из .md файла."""
    from classifier.providers import create_provider

    input_path = Path(input_path)
    if not input_path.exists():
        print(f"Файл не найден: {input_path}")
        return

    md_text = input_path.read_text(encoding="utf-8")
    questions = extract_questions(md_text)

    if not questions:
        print("Вопросы не найдены в файле.")
        return

    print(f"Найдено вопросов: {len(questions)}")

    # Создаём провайдер
    provider = create_provider(provider_name, model=model, api_key=api_key)
    print(f"Провайдер: {provider.config.name}, модель: {provider.config.model}")

    if dry_run:
        print("\n--- Dry run: только парсинг ---")
        for q in questions:
            print(f"  #{q['num']}: {q['text'][:80]}...")
        return

    # Классифицируем каждый вопрос
    lines = md_text.split("\n")
    insertions = []  # (line_idx, classification_line)

    for i, q in enumerate(questions):
        print(f"  [{i+1}/{len(questions)}] {q['text'][:60]}...", end=" ", flush=True)

        topics = classify_q(
            provider=provider,
            text=q["text"],
            answer="",
            comment="",
            twostage=True,
        )

        classification_line = format_classification(topics)
        print(classification_line.replace("> **Классификация:** ", ""))

        # Определяем куда вставить строку классификации
        insert_after = q["line_idx"]
        j = q["line_idx"] + 1
        while j < len(lines):
            next_line = lines[j].strip()
            if not next_line or next_line.startswith("#") or next_line.startswith("---"):
                break
            if _QUESTION_RE.match(next_line):
                break
            # Если уже есть старая классификация — заменим
            if next_line.startswith("> **Классификация"):
                lines[j] = classification_line
                classification_line = None
                break
            insert_after = j
            j += 1

        if classification_line is not None:
            insertions.append((insert_after + 1, classification_line))

    # Вставляем классификации (с конца, чтобы индексы не сбились)
    for line_idx, cls_line in sorted(insertions, reverse=True):
        lines.insert(line_idx, cls_line)

    # Записываем результат
    result = "\n".join(lines)
    if output_path:
        out = Path(output_path)
    else:
        out = input_path

    out.write_text(result, encoding="utf-8")
    print(f"\nРезультат записан в: {out}")

    if provider.config.cost_per_1m_input > 0:
        print(f"Стоимость: ${provider.estimated_cost:.4f}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Классификация вопросов ЧГК из .md файла",
    )
    parser.add_argument("input", help="Путь к .md файлу с вопросами")
    parser.add_argument("--output", "-o", default=None, help="Выходной файл (по умолчанию — перезаписать входной)")
    parser.add_argument("--provider", default="ollama", help="LLM-провайдер")
    parser.add_argument("--model", default=None, help="Модель")
    parser.add_argument("--api-key", default=None, help="API-ключ")
    parser.add_argument("--dry-run", action="store_true", help="Только парсинг, без классификации")

    args = parser.parse_args()

    classify_md_file(
        input_path=args.input,
        output_path=args.output,
        provider_name=args.provider,
        model=args.model,
        api_key=args.api_key,
        dry_run=args.dry_run,
    )
