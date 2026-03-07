"""Классификация топ-ответов ЧГК в 14 тематических категорий.

Берёт ВСЕ top_answers.json напрямую и раскладывает по 14 категориям,
без промежуточного слоя из 6 категорий.

Использование:
    python scripts/classify_gentleman_entities.py --force
    python scripts/classify_gentleman_entities.py --provider openrouter --model openai/gpt-4.1-mini
    python scripts/classify_gentleman_entities.py --top 500
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PROJECT_ROOT
from database.seed_taxonomy import TAXONOMY

DATA_DIR = PROJECT_ROOT / "data" / "gentleman_set"
OUTPUT_PATH = DATA_DIR / "thematic_mapping.json"

# Категории для промпта
CATEGORY_LIST = "\n".join(
    f"{i}. {name_ru}" for i, (_, name_ru, _) in enumerate(TAXONOMY, 1)
)
CAT_NAMES = {i: name_ru for i, (_, name_ru, _) in enumerate(TAXONOMY, 1)}
NUM_CATEGORIES = len(TAXONOMY)

BATCH_PROMPT = f"""Ты классифицируешь ответы на вопросы ЧГК по {NUM_CATEGORIES} тематическим категориям.

Категории:
{CATEGORY_LIST}

Правила:
- Для каждого ответа определи ОДНУ наиболее подходящую категорию.
- Классифицируй по ПРЕДМЕТНОЙ ОБЛАСТИ ответа, не по форме вопроса.
- Если ответ — конкретный человек, животное, предмет, место и т.д., определи категорию по тому, за что этот ответ наиболее известен.
- Примеры: «слон» → Природа и животные, «зеркало» → Быт и повседневность, «Пушкин» → Литература, «Венеция» → География, «радуга» → Наука и технологии, «Титаник» → Кино и театр.
- НЕ пропускай ответы — каждый ответ ДОЛЖЕН получить категорию.

Верни ТОЛЬКО валидный JSON-объект: {{"ответ": номер_категории, ...}}
Без пояснений, без markdown."""

CONTEXT_PROMPT = f"""Ты классифицируешь ответы ЧГК по {NUM_CATEGORIES} тематическим категориям С КОНТЕКСТОМ вопросов.

Категории:
{CATEGORY_LIST}

Для каждого ответа даны 1-2 вопроса, где он встречался. Используй контекст для точности.

Правила:
- Каждый ответ ДОЛЖЕН получить категорию.
- Классифицируй по предметной области.

Верни ТОЛЬКО JSON: {{"ответ": номер_категории, ...}}"""


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def parse_batch_response(response: str, expected: list[str]) -> dict[str, int]:
    """Извлечь JSON из ответа LLM."""
    if not response:
        return {}

    cleaned = re.sub(r"```(?:json)?\s*", "", response).strip().rstrip("`").strip()
    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return {}

    if not isinstance(data, dict):
        return {}

    expected_keys = {normalize_key(a) for a in expected}
    result: dict[str, int] = {}
    for answer, cat_num in data.items():
        key = normalize_key(str(answer))
        if key not in expected_keys:
            continue
        if isinstance(cat_num, int):
            num = cat_num
        elif isinstance(cat_num, str) and cat_num.strip().isdigit():
            num = int(cat_num.strip())
        else:
            continue
        if 1 <= num <= NUM_CATEGORIES:
            result[key] = num

    return result


def classify_batch(provider, answers: list[str], prompt: str) -> dict[str, int]:
    """Классифицировать батч ответов."""
    numbered = "\n".join(f"{i+1}. {a}" for i, a in enumerate(answers))
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Классифицируй эти ответы ЧГК:\n\n{numbered}"},
    ]
    response = provider.chat(messages, max_tokens=2000)
    return parse_batch_response(response, answers)


def classify_batch_with_context(
    provider, answers: list[str], contexts: dict[str, list[str]]
) -> dict[str, int]:
    """Классифицировать с контекстом вопросов."""
    lines = []
    for i, answer in enumerate(answers):
        lines.append(f"{i+1}. {answer}")
        ctx = contexts.get(normalize_key(answer), [])
        for q in ctx[:2]:
            lines.append(f'   - "{q}"')

    messages = [
        {"role": "system", "content": CONTEXT_PROMPT},
        {"role": "user", "content": f"Классифицируй ответы ЧГК:\n\n" + "\n".join(lines)},
    ]
    response = provider.chat(messages, max_tokens=2000)
    return parse_batch_response(response, answers)


def load_question_contexts(answer_questions: dict, max_per: int = 2) -> dict[str, list[str]]:
    """Загрузить тексты вопросов из БД."""
    import sqlite3
    from config import DB_PATH

    if not answer_questions:
        return {}

    conn = sqlite3.connect(str(DB_PATH))
    contexts: dict[str, list[str]] = {}
    for answer, qids in answer_questions.items():
        sample = qids[:max_per]
        if not sample:
            continue
        ph = ",".join("?" * len(sample))
        rows = conn.execute(
            f"SELECT text FROM questions WHERE id IN ({ph})", sample
        ).fetchall()
        texts = [r[0][:200] for r in rows if r[0]]
        if texts:
            contexts[normalize_key(answer)] = texts

    conn.close()
    return contexts


def main():
    parser = argparse.ArgumentParser(
        description="Классификация топ-ответов ЧГК в 14 тематических категорий"
    )
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="openai/gpt-4.1-mini")
    parser.add_argument("--top", type=int, default=1500)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    # Загрузить top_answers
    top_path = DATA_DIR / "top_answers.json"
    if not top_path.exists():
        print("top_answers.json не найден. Запустите: python scripts/analyze_answers.py")
        return

    top_data = json.loads(top_path.read_text(encoding="utf-8"))
    all_answers = top_data["top_answers"][:args.top]
    display_forms = top_data.get("display_forms", {})
    answer_questions = top_data.get("answer_questions", {})

    print(f"Загружено ответов: {len(all_answers)}")

    # Загрузить существующий маппинг
    existing_themes: dict[str, dict] = {}
    if OUTPUT_PATH.exists() and not args.force:
        existing_data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        existing_themes = existing_data.get("entity_themes", {})

    # Определить что нужно классифицировать
    to_classify: list[tuple[str, int]] = []  # (norm_key, freq)
    already_done = 0
    for norm_key, freq in all_answers:
        if norm_key in existing_themes:
            already_done += 1
        else:
            to_classify.append((norm_key, freq))

    print(f"Уже классифицировано: {already_done}")
    print(f"Осталось: {len(to_classify)}")

    if not to_classify:
        print("Все ответы уже классифицированы.")
        _save(existing_themes, args.model, all_answers, display_forms)
        return

    # Загрузить контексты
    contexts = load_question_contexts(answer_questions)
    if contexts:
        print(f"Контексты вопросов: {len(contexts)}")

    # Создать провайдер
    from classifier.providers import create_provider
    provider = create_provider(args.provider, model=args.model)
    print(f"Провайдер: {args.provider}, модель: {args.model}")

    n_batches = (len(to_classify) + args.batch_size - 1) // args.batch_size
    if provider.config.cost_per_1m_input > 0:
        # 2 прохода максимум
        est = provider.estimate_total_cost(
            n_batches * 2, avg_input_tokens=800, avg_output_tokens=400
        )
        print(f"Оценка стоимости: ${est:.4f} ({n_batches} батчей)")

    themes = dict(existing_themes)
    classified = 0
    failed = 0

    try:
        for batch_idx in range(n_batches):
            start = batch_idx * args.batch_size
            end = min(start + args.batch_size, len(to_classify))
            batch = to_classify[start:end]

            # Используем display forms для LLM
            batch_displays = [display_forms.get(norm, norm) for norm, _ in batch]

            print(f"\n  Батч {batch_idx+1}/{n_batches} ({len(batch)} ответов)...", end=" ", flush=True)

            # 1-й проход
            result = classify_batch(provider, batch_displays, BATCH_PROMPT)

            assigned = 0
            unresolved: list[tuple[str, str]] = []
            for norm_key, _ in batch:
                display = display_forms.get(norm_key, norm_key)
                # Ищем по обоим ключам
                cat_num = result.get(normalize_key(display)) or result.get(normalize_key(norm_key))
                if cat_num:
                    themes[norm_key] = {
                        "category_num": cat_num,
                        "category": CAT_NAMES.get(cat_num, "?"),
                    }
                    assigned += 1
                else:
                    unresolved.append((norm_key, display))

            # 2-й проход с контекстом для неразрешённых
            assigned_ctx = 0
            if unresolved and contexts:
                ctx_displays = [d for _, d in unresolved]
                ctx_result = classify_batch_with_context(provider, ctx_displays, contexts)

                still_failed = []
                for norm_key, display in unresolved:
                    cat_num = ctx_result.get(normalize_key(display)) or ctx_result.get(normalize_key(norm_key))
                    if cat_num:
                        themes[norm_key] = {
                            "category_num": cat_num,
                            "category": CAT_NAMES.get(cat_num, "?"),
                        }
                        assigned_ctx += 1
                    else:
                        still_failed.append(norm_key)
                failed += len(still_failed)
            else:
                failed += len(unresolved)

            classified += assigned + assigned_ctx
            skip = len(batch) - assigned - assigned_ctx
            print(f"1-й: {assigned}, контекст: {assigned_ctx}, пропущено: {skip}")

            # Сохранение каждые 5 батчей
            if (batch_idx + 1) % 5 == 0:
                _save(themes, args.model, all_answers, display_forms)

    except KeyboardInterrupt:
        print("\n\nПрервано. Сохраняю...")

    _save(themes, args.model, all_answers, display_forms)

    print(f"\n{'=' * 50}")
    print(f"Классифицировано: {classified}")
    print(f"Пропущено: {failed}")
    print(f"Всего в маппинге: {len(themes)}")
    if hasattr(provider, 'estimated_cost'):
        print(f"Стоимость: ${provider.estimated_cost:.4f}")
    print(f"{'=' * 50}")

    dist = Counter(t["category"] for t in themes.values())
    print(f"\nРаспределение:")
    for cat, cnt in dist.most_common():
        print(f"  {cat:30s} {cnt}")


def _save(themes: dict, model: str, all_answers: list, display_forms: dict):
    """Сохранить thematic_mapping.json."""
    by_category: dict[str, list] = {}
    for key, theme in themes.items():
        cat = theme["category"]
        by_category.setdefault(cat, []).append(key)

    output = {
        "generated_at": datetime.now().isoformat(),
        "model": model,
        "total_entities": len(themes),
        "entity_themes": themes,
        "by_category": by_category,
        "display_forms": display_forms,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
