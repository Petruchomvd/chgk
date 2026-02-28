"""LLM-категоризация топ-ответов ЧГК для «Джентльменского набора».

Берёт `top_answers.json` из analyze_answers.py и раскладывает ответы
по 6 целевым категориям:
Люди, Места, Произведения, Наука, Выражения, Числа.

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

import config  # noqa: F401 - загрузит .env
from config import PROJECT_ROOT
from classifier.providers import create_provider, AVAILABLE_PROVIDERS

DATA_DIR = PROJECT_ROOT / "data" / "gentleman_set"

TARGET_CATEGORIES = {
    1: "Люди",
    2: "Места",
    3: "Произведения",
    4: "Наука",
    5: "Выражения",
    6: "Числа",
}
TARGET_CATEGORY_NAMES = set(TARGET_CATEGORIES.values())
NUMBER_WORD_ALLOWLIST = {"пи", "pi", "π", "ноль", "нуль"}
SCIENCE_MARKERS = {
    "принцип", "закон", "теорема", "эффект", "гипотеза", "парадокс",
    "тест", "формула", "уравнение", "распределение",
    "гравитац", "квант", "атом", "днк", "ген", "молекул",
    "чёрная дыра", "черная дыра", "болезнь", "синдром",
    "лента мёбиуса", "лента мебиуса", "кот шрёдингера", "кот шредингера",
}
WORK_SINGLE_WORD_WHITELIST = {
    "шахматы", "тетрис", "гамлет", "дюна", "щелкунчик", "герника",
    "джоконда", "джокер", "колобок", "хоббит", "пигмалион", "оскар",
    "крик", "касабланка", "золушка", "буратино",
}

LEGACY_CATEGORY_ALIASES = {
    "Наука и техника": "Наука",
    "Выражения и фразы": "Выражения",
    "Числа и даты": "Числа",
}

FIRST_PASS_PROMPT = """Ты категоризируешь ответы на вопросы игры «Что? Где? Когда?».

Для каждого ответа определи одну категорию:
1. Люди — реальные и вымышленные персоны
2. Места — география, города, страны, достопримечательности, сооружения
3. Произведения — книги, фильмы, сериалы, картины, музыка, игры, пьесы
4. Наука — научные понятия, законы, эффекты, принципы, тесты, теоремы
5. Выражения — крылатые фразы, пословицы, цитаты, устойчивые выражения
6. Числа — числа, годы, даты, числовые константы

Правила:
- возвращай только ответы, в которых уверен;
- если ответ неоднозначен и уверенности нет, пропусти его;
- если это просто предмет/животное/бытовая вещь без чёткого маркера 6 категорий, пропусти;
- не придумывай новые ответы.

Верни ТОЛЬКО валидный JSON-объект без markdown.
Формат: {"ответ": номер_категории, ...}
"""

SECOND_PASS_PROMPT = """Повторная категоризация ответов ЧГК.

Нужно выбрать категорию только из списка:
1. Люди
2. Места
3. Произведения
4. Наука
5. Выражения
6. Числа

Это второй проход по сомнительным ответам, поэтому правила строже:
- категоризируй только если есть явный маркер категории;
- если уверенности нет, пропусти ответ;
- не относить бытовые предметы, животных и общие слова к «Произведениям», «Науке» или «Выражениям» без чёткого основания.

Верни ТОЛЬКО JSON-объект формата {"ответ": номер_категории}.
"""


def normalize_text_key(text: str) -> str:
    """Привести текст к стабильному ключу для сопоставления."""
    return re.sub(r"\s+", " ", text.strip().lower())


def is_numeric_like_answer(text: str) -> bool:
    """Проверить, похож ли ответ на числовой формат."""
    text = normalize_text_key(text)
    if text in NUMBER_WORD_ALLOWLIST:
        return True
    if re.fullmatch(r"\d{1,4}", text):
        return True
    if re.fullmatch(r"\d{1,4}([./:-]\d{1,4})+", text):
        return True
    if re.fullmatch(r"\d+[,.]\d+", text):
        return True
    return False


def rule_based_category(answer: str) -> int | None:
    """Детерминированная категоризация простых кейсов."""
    if is_numeric_like_answer(answer):
        return 6
    return None


def _tokenize(answer: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9]+", normalize_text_key(answer), flags=re.IGNORECASE)


def is_valid_assignment(answer: str, category_num: int) -> bool:
    """Проверить, что присвоение категории выглядит правдоподобно."""
    normalized = normalize_text_key(answer)
    tokens = _tokenize(answer)

    if category_num == 6:  # Числа
        return is_numeric_like_answer(normalized)

    if category_num == 5:  # Выражения
        return len(tokens) >= 2

    if category_num == 4:  # Наука
        return any(marker in normalized for marker in SCIENCE_MARKERS)

    if category_num == 3:  # Произведения
        return len(tokens) >= 2 or normalized in WORK_SINGLE_WORD_WHITELIST

    return True


def load_existing_categorization() -> dict:
    """Загрузить существующую категоризацию (для идемпотентности)."""
    path = DATA_DIR / "categorized_answers.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def normalize_existing_mapping(existing_mapping: dict) -> dict[str, str]:
    """Нормализовать старую категоризацию к новым 6 категориям."""
    normalized: dict[str, str] = {}
    for answer, category in existing_mapping.items():
        answer_key = normalize_text_key(str(answer))

        mapped_category = None
        if isinstance(category, int):
            mapped_category = TARGET_CATEGORIES.get(category)
        elif isinstance(category, str):
            category_clean = category.strip()
            mapped_category = LEGACY_CATEGORY_ALIASES.get(category_clean, category_clean)

        if mapped_category in TARGET_CATEGORY_NAMES:
            normalized[answer_key] = mapped_category
    return normalized


def save_categorization(result: dict) -> None:
    """Сохранить категоризацию в JSON."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "categorized_answers.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_llm_response(response: str, expected_answers: list[str]) -> dict[str, int]:
    """Извлечь JSON из ответа LLM и вернуть валидные категории 1..6."""
    if not response:
        return {}

    cleaned = re.sub(r"```(?:json)?\s*", "", response).strip()
    cleaned = cleaned.rstrip("`").strip()

    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                print("    [!] Не удалось распарсить JSON")
                return {}
        else:
            print("    [!] JSON не найден в ответе")
            return {}

    if not isinstance(data, dict):
        print("    [!] Ответ не является JSON-объектом")
        return {}

    expected = {normalize_text_key(a) for a in expected_answers}
    result: dict[str, int] = {}

    for answer, cat_num in data.items():
        answer_key = normalize_text_key(str(answer))
        if answer_key not in expected:
            continue

        if isinstance(cat_num, int):
            num = cat_num
        elif isinstance(cat_num, str) and cat_num.strip().isdigit():
            num = int(cat_num.strip())
        else:
            continue

        if 1 <= num <= 6:
            result[answer_key] = num

    return result


def categorize_batch(provider, answers_batch: list[str], prompt: str) -> dict[str, int]:
    """Отправить батч ответов на категоризацию."""
    numbered = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(answers_batch))
    user_msg = f"Категоризируй эти ответы ЧГК:\n\n{numbered}"

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_msg},
    ]

    response = provider.chat(messages, max_tokens=2000)
    return parse_llm_response(response, answers_batch)


def resolve_category_number(
    llm_result: dict[str, int],
    normalized_answer: str,
    display_answer: str,
) -> int | None:
    """Найти номер категории в ответе LLM по двум вариантам ключа."""
    for key in (normalize_text_key(display_answer), normalize_text_key(normalized_answer)):
        if key in llm_result:
            return llm_result[key]
    return None


def build_categorized_output(
    answer_category: dict[str, str],
    top_answers: list,
    display_forms: dict,
    uncategorized: list[list],
) -> dict:
    """Собрать итоговый JSON: только 6 категорий + список пропущенных."""
    categories = {name: [] for name in TARGET_CATEGORIES.values()}

    for norm_answer, count in top_answers:
        category_name = answer_category.get(norm_answer)
        if category_name in categories:
            categories[category_name].append([norm_answer, count])

    for category_name in categories:
        categories[category_name].sort(key=lambda item: item[1], reverse=True)

    return {
        "generated_at": datetime.now().isoformat(),
        "categories": categories,
        "answer_category": answer_category,
        "display_forms": display_forms,
        "total_categorized": len(answer_category),
        "uncategorized": uncategorized,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="LLM-категоризация топ-ответов ЧГК (6 категорий)",
    )
    parser.add_argument(
        "--provider",
        default="openai",
        help=f"LLM-провайдер ({', '.join(AVAILABLE_PROVIDERS)})",
    )
    parser.add_argument("--model", default=None, help="Модель")
    parser.add_argument("--api-key", default=None, help="API-ключ")
    parser.add_argument(
        "--top",
        type=int,
        default=500,
        help="Количество топ-ответов для категоризации (по умолчанию 500)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=40,
        help="Размер батча (по умолчанию 40)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перекатегоризировать даже уже обработанные ответы",
    )

    args = parser.parse_args()

    top_answers_path = DATA_DIR / "top_answers.json"
    if not top_answers_path.exists():
        print("Файл top_answers.json не найден. Сначала запустите:")
        print("  python scripts/analyze_answers.py")
        return

    top_data = json.loads(top_answers_path.read_text(encoding="utf-8"))
    all_answers = top_data["top_answers"][:args.top]
    display_forms = top_data.get("display_forms", {})

    print(f"Загружено ответов для категоризации: {len(all_answers)}")

    existing = load_existing_categorization()
    existing_mapping_raw = existing.get("answer_category", {}) if not args.force else {}
    existing_mapping = normalize_existing_mapping(existing_mapping_raw)

    # Привязываем старую категоризацию к текущему списку top_answers
    answer_category: dict[str, str] = {}
    for norm_answer, _ in all_answers:
        answer_key = normalize_text_key(norm_answer)
        existing_cat = existing_mapping.get(norm_answer) or existing_mapping.get(answer_key)
        if existing_cat in TARGET_CATEGORY_NAMES:
            answer_category[norm_answer] = existing_cat
    matched_existing_count = len(answer_category)

    to_categorize: list[tuple[str, int]] = []
    rule_based_count = 0

    for norm_answer, count in all_answers:
        if norm_answer in answer_category:
            continue

        display = display_forms.get(norm_answer, norm_answer)
        rule_cat_num = rule_based_category(display) or rule_based_category(norm_answer)
        if rule_cat_num:
            answer_category[norm_answer] = TARGET_CATEGORIES[rule_cat_num]
            rule_based_count += 1
        else:
            to_categorize.append((norm_answer, count))

    print(f"Уже категоризировано (валидные 6 категорий): {matched_existing_count}")
    print(f"Категоризировано правилами (без LLM): {rule_based_count}")
    print(f"Осталось для LLM: {len(to_categorize)}")

    uncategorized: list[list] = []
    llm_categorized = 0
    model_name = existing.get("model", "unknown")

    if to_categorize:
        provider = create_provider(args.provider, model=args.model, api_key=args.api_key)
        model_name = provider.config.model
        print(f"Провайдер: {provider.config.name}, модель: {provider.config.model}")

        n_batches = (len(to_categorize) + args.batch_size - 1) // args.batch_size

        if provider.config.cost_per_1m_input > 0:
            est = provider.estimate_total_cost(
                n_batches, avg_input_tokens=800, avg_output_tokens=400
            )
            print(f"Примерная стоимость: ${est:.4f} ({n_batches} батчей)")

        for batch_idx in range(n_batches):
            start = batch_idx * args.batch_size
            end = min(start + args.batch_size, len(to_categorize))
            batch = to_categorize[start:end]
            batch_count_map = {norm: cnt for norm, cnt in batch}

            batch_answers = [display_forms.get(norm, norm) for norm, _ in batch]

            print(
                f"\n  Батч {batch_idx + 1}/{n_batches} ({len(batch)} ответов)...",
                end=" ",
                flush=True,
            )

            first_pass = categorize_batch(provider, batch_answers, FIRST_PASS_PROMPT)

            assigned_first = 0
            unresolved: list[tuple[str, str]] = []
            for norm_answer, _ in batch:
                display = display_forms.get(norm_answer, norm_answer)
                cat_num = resolve_category_number(first_pass, norm_answer, display)
                if cat_num and is_valid_assignment(display, cat_num):
                    answer_category[norm_answer] = TARGET_CATEGORIES[cat_num]
                    assigned_first += 1
                else:
                    unresolved.append((norm_answer, display))

            assigned_second = 0
            if unresolved:
                second_input = [display for _, display in unresolved]
                second_pass = categorize_batch(provider, second_input, SECOND_PASS_PROMPT)

                unresolved_after_second: list[str] = []
                for norm_answer, display in unresolved:
                    cat_num = resolve_category_number(second_pass, norm_answer, display)
                    if cat_num and is_valid_assignment(display, cat_num):
                        answer_category[norm_answer] = TARGET_CATEGORIES[cat_num]
                        assigned_second += 1
                    else:
                        unresolved_after_second.append(norm_answer)

                for norm_answer in unresolved_after_second:
                    uncategorized.append([norm_answer, batch_count_map[norm_answer]])

            llm_categorized += assigned_first + assigned_second
            print(f"✓ 1-й проход: {assigned_first}, 2-й: {assigned_second}, пропущено: {len(unresolved) - assigned_second}")

            interim = build_categorized_output(
                answer_category, all_answers, display_forms, uncategorized
            )
            interim["model"] = provider.config.model
            interim["meta"] = {
                "source_candidates": len(all_answers),
                "already_categorized": matched_existing_count,
                "rule_based_categorized": rule_based_count,
                "llm_categorized": llm_categorized,
                "uncategorized_count": len(uncategorized),
            }
            save_categorization(interim)

    final = build_categorized_output(answer_category, all_answers, display_forms, uncategorized)
    final["model"] = model_name
    final["meta"] = {
        "source_candidates": len(all_answers),
        "already_categorized": matched_existing_count,
        "rule_based_categorized": rule_based_count,
        "llm_categorized": llm_categorized,
        "uncategorized_count": len(uncategorized),
    }
    save_categorization(final)

    print("\n=== Готово ===")
    print(f"Всего категоризировано: {len(answer_category)}")
    print(f"Пропущено (вне 6 категорий/неуверенные): {len(uncategorized)}")

    print("\nКатегории:")
    for category_name, items in final["categories"].items():
        if items:
            print(f"  {category_name}: {len(items)}")
            for norm, cnt in items[:3]:
                display = display_forms.get(norm, norm)
                print(f"    {display}: {cnt}")


if __name__ == "__main__":
    main()
