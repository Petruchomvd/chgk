"""LLM-категоризация топ-ответов ЧГК для «Джентльменского набора».

Берёт top_answers.json (из analyze_answers.py), отправляет батчами
в LLM для категоризации по типам (люди, места, произведения и т.д.),
сохраняет результат в categorized_answers.json.

Использование:
    python scripts/categorize_gentleman.py
    python scripts/categorize_gentleman.py --provider openai --top 500
    python scripts/categorize_gentleman.py --provider groq --batch-size 30
    python scripts/categorize_gentleman.py --force
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: F401 — загрузит .env
from config import PROJECT_ROOT
from classifier.providers import create_provider, AVAILABLE_PROVIDERS

DATA_DIR = PROJECT_ROOT / "data" / "gentleman_set"

CATEGORIES = {
    1: "Люди",
    2: "Места",
    3: "Произведения",
    4: "Наука и техника",
    5: "Выражения и фразы",
    6: "Числа и даты",
    7: "Другое",
}

SYSTEM_PROMPT = """Ты категоризируешь ответы на вопросы игры «Что? Где? Когда?».

Для каждого ответа определи одну категорию:
1. Люди — реальные и вымышленные персоны (Пушкин, Шерлок Холмс, Наполеон)
2. Места — география, города, страны, достопримечательности (Москва, Эверест, Колизей)
3. Произведения — книги, фильмы, картины, музыка, скульптуры, пьесы (Мона Лиза, Гамлет, Война и мир)
4. Наука и техника — научные понятия, законы, изобретения, единицы (кот Шрёдингера, числа Фибоначчи)
5. Выражения и фразы — крылатые выражения, пословицы, цитаты, устойчивые фразы
6. Числа и даты — числа, годы, математические константы (42, 1984, число пи)
7. Другое — всё, что не подходит к категориям выше (животные, предметы, явления, абстракции)

Верни ТОЛЬКО валидный JSON-объект без markdown-разметки.
Формат: {"ответ": номер_категории, ...}
Пример: {"Пушкин": 1, "Москва": 2, "Мона Лиза": 3, "42": 6}"""


def load_existing_categorization() -> dict:
    """Загрузить существующую категоризацию (для идемпотентности)."""
    path = DATA_DIR / "categorized_answers.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_categorization(result: dict) -> None:
    """Сохранить категоризацию в JSON."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "categorized_answers.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_llm_response(response: str, expected_answers: list[str]) -> dict[str, int]:
    """Извлечь JSON из ответа LLM, валидировать категории."""
    if not response:
        return {}

    # Убрать markdown code block если есть
    cleaned = re.sub(r"```(?:json)?\s*", "", response).strip()
    cleaned = cleaned.rstrip("`")

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Попробовать найти JSON в ответе
        match = re.search(r"\{[^}]+\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                print(f"    [!] Не удалось распарсить JSON")
                return {}
        else:
            print(f"    [!] JSON не найден в ответе")
            return {}

    result = {}
    for answer, cat_num in data.items():
        if isinstance(cat_num, int) and 1 <= cat_num <= 7:
            result[answer.strip().lower()] = cat_num
        elif isinstance(cat_num, str) and cat_num.isdigit():
            num = int(cat_num)
            if 1 <= num <= 7:
                result[answer.strip().lower()] = num

    return result


def categorize_batch(provider, answers_batch: list[str]) -> dict[str, int]:
    """Отправить батч ответов на категоризацию."""
    numbered = "\n".join(f"{i+1}. {a}" for i, a in enumerate(answers_batch))
    user_msg = f"Категоризируй эти ответы ЧГК:\n\n{numbered}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    response = provider.chat(messages, max_tokens=2000)
    return parse_llm_response(response, answers_batch)


def build_categorized_output(
    answer_category: dict[str, str],
    top_answers: list,
    display_forms: dict,
) -> dict:
    """Собрать финальный JSON с группировкой по категориям."""
    categories = {name: [] for name in CATEGORIES.values()}

    for norm_answer, count in top_answers:
        cat_name = answer_category.get(norm_answer)
        if cat_name:
            categories[cat_name].append([norm_answer, count])

    # Сортировка внутри каждой категории по убыванию частоты
    for cat_name in categories:
        categories[cat_name].sort(key=lambda x: x[1], reverse=True)

    return {
        "generated_at": datetime.now().isoformat(),
        "categories": categories,
        "answer_category": answer_category,
        "display_forms": display_forms,
        "total_categorized": len(answer_category),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="LLM-категоризация топ-ответов ЧГК",
    )
    parser.add_argument("--provider", default="openai",
                        help=f"LLM-провайдер ({', '.join(AVAILABLE_PROVIDERS)})")
    parser.add_argument("--model", default=None, help="Модель")
    parser.add_argument("--api-key", default=None, help="API-ключ")
    parser.add_argument("--top", type=int, default=500,
                        help="Кол-во топ-ответов для категоризации (по умолчанию 500)")
    parser.add_argument("--batch-size", type=int, default=40,
                        help="Размер батча (по умолчанию 40)")
    parser.add_argument("--force", action="store_true",
                        help="Пере-категоризировать даже уже обработанные")

    args = parser.parse_args()

    # Загрузка top_answers.json
    top_answers_path = DATA_DIR / "top_answers.json"
    if not top_answers_path.exists():
        print("Файл top_answers.json не найден. Сначала запустите:")
        print("  python scripts/analyze_answers.py")
        return

    top_data = json.loads(top_answers_path.read_text(encoding="utf-8"))
    all_answers = top_data["top_answers"][:args.top]
    display_forms = top_data.get("display_forms", {})

    print(f"Загружено ответов для категоризации: {len(all_answers)}")

    # Загрузить существующую категоризацию
    existing = load_existing_categorization()
    existing_mapping = existing.get("answer_category", {}) if not args.force else {}

    # Отфильтровать уже категоризированные
    to_categorize = []
    for norm_answer, count in all_answers:
        if norm_answer not in existing_mapping:
            to_categorize.append((norm_answer, count))

    if not to_categorize:
        print("Все ответы уже категоризированы. Используйте --force для повторной.")
        # Пересобрать выход с актуальным списком ответов
        result = build_categorized_output(existing_mapping, all_answers, display_forms)
        result["model"] = existing.get("model", "unknown")
        save_categorization(result)
        print("Файл обновлён (пересобраны категории).")
        return

    print(f"Новых для категоризации: {len(to_categorize)}")
    print(f"Уже категоризировано: {len(existing_mapping)}")

    # Создать провайдер
    provider = create_provider(args.provider, model=args.model, api_key=args.api_key)
    print(f"Провайдер: {provider.config.name}, модель: {provider.config.model}")

    # Прогноз стоимости
    n_batches = (len(to_categorize) + args.batch_size - 1) // args.batch_size
    if provider.config.cost_per_1m_input > 0:
        est = provider.estimate_total_cost(
            n_batches, avg_input_tokens=800, avg_output_tokens=400
        )
        print(f"Примерная стоимость: ${est:.4f} ({n_batches} батчей)")

    # Категоризация батчами
    answer_category = dict(existing_mapping)  # начинаем с существующих

    for batch_idx in range(n_batches):
        start = batch_idx * args.batch_size
        end = min(start + args.batch_size, len(to_categorize))
        batch = to_categorize[start:end]

        batch_answers = [display_forms.get(norm, norm) for norm, _ in batch]

        print(f"\n  Батч {batch_idx + 1}/{n_batches} "
              f"({len(batch)} ответов)...", end=" ", flush=True)

        result = categorize_batch(provider, batch_answers)

        # Сопоставить результаты
        categorized_count = 0
        for norm_answer, _ in batch:
            display = display_forms.get(norm_answer, norm_answer)
            # Ищем в ответе LLM: по normalized или по display
            cat_num = result.get(norm_answer) or result.get(display.lower())
            if cat_num:
                answer_category[norm_answer] = CATEGORIES[cat_num]
                categorized_count += 1

        print(f"✓ {categorized_count}/{len(batch)}")

        # Промежуточное сохранение после каждого батча
        interim = build_categorized_output(answer_category, all_answers, display_forms)
        interim["model"] = provider.config.model
        save_categorization(interim)

    # Итоговая статистика
    print(f"\n=== Готово ===")
    print(f"Всего категоризировано: {len(answer_category)}")
    if provider.config.cost_per_1m_input > 0:
        print(f"Стоимость: ${provider.estimated_cost:.4f}")

    # Статистика по категориям
    final = build_categorized_output(answer_category, all_answers, display_forms)
    final["model"] = provider.config.model
    save_categorization(final)

    print("\nКатегории:")
    for cat_name, items in final["categories"].items():
        if items:
            print(f"  {cat_name}: {len(items)}")
            for norm, cnt in items[:3]:
                d = display_forms.get(norm, norm)
                print(f"    {d}: {cnt}")


if __name__ == "__main__":
    main()
