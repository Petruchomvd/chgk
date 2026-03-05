"""Классификация сущностей джентльменского набора в 14 тематических категорий ЧГК.

Использует LLM для определения тематической категории каждой сущности
на основе имени, типа и Wikipedia-описания.

Использование:
    python scripts/classify_gentleman_entities.py
    python scripts/classify_gentleman_entities.py --limit 10
    python scripts/classify_gentleman_entities.py --force
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PROJECT_ROOT
from database.seed_taxonomy import TAXONOMY

DATA_DIR = PROJECT_ROOT / "data" / "gentleman_set"
OUTPUT_PATH = DATA_DIR / "thematic_mapping.json"

# Построить список категорий для промпта
CATEGORY_LIST = "\n".join(
    f"{i}. {name_ru}" for i, (_, name_ru, _) in enumerate(TAXONOMY, 1)
)

SYSTEM_PROMPT = f"""Ты классифицируешь сущности (ответы на вопросы ЧГК) по 14 тематическим категориям.

Категории:
{CATEGORY_LIST}

Для каждой сущности определи ОДНУ наиболее подходящую категорию.
Учитывай: за что эта сущность наиболее известна? В каких вопросах ЧГК она чаще всего встречается?

Верни JSON: {{"cat": N}}
где N — номер категории (1-14).
Без пояснений, только JSON."""

FEW_SHOT = [
    {"entity": "Пушкин — русский поэт, драматург и прозаик", "out": {"cat": 2}},
    {"entity": "Наполеон — император Франции, полководец", "out": {"cat": 1}},
    {"entity": "Швейцария — государство в Центральной Европе", "out": {"cat": 4}},
    {"entity": "Эйнштейн — физик-теоретик, создатель теории относительности", "out": {"cat": 3}},
    {"entity": "Прометей — титан из древнегреческой мифологии", "out": {"cat": 10}},
    {"entity": "Оскар — ежегодная кинопремия", "out": {"cat": 7}},
    {"entity": "Шахматы — настольная логическая игра", "out": {"cat": 8}},
]


def build_messages(entity_name: str, entity_type: str, description: str) -> list:
    """Построить сообщения для LLM."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Few-shot
    for ex in FEW_SHOT:
        messages.append({"role": "user", "content": ex["entity"]})
        messages.append({"role": "assistant", "content": json.dumps(ex["out"])})

    # Текущая сущность
    parts = [entity_name]
    if description:
        parts.append(f"— {description}")
    elif entity_type:
        parts.append(f"(тип: {entity_type})")
    messages.append({"role": "user", "content": " ".join(parts)})

    return messages


def parse_response(text: str) -> dict | None:
    """Извлечь JSON из ответа LLM."""
    import re
    text = text.strip()
    # Попробовать найти JSON
    match = re.search(r"\{[^}]+\}", text)
    if match:
        try:
            data = json.loads(match.group())
            cat = data.get("cat")
            if isinstance(cat, int) and 1 <= cat <= 14:
                return {"cat": cat}
        except json.JSONDecodeError:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Классификация сущностей в 14 тематических категорий ЧГК"
    )
    parser.add_argument(
        "--provider", default="openrouter",
        help="Провайдер LLM (default: openrouter)",
    )
    parser.add_argument(
        "--model", default="qwen/qwen-2.5-72b-instruct",
        help="Модель (default: qwen/qwen-2.5-72b-instruct)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Макс. сущностей для классификации",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Перезаписать уже классифицированные",
    )
    args = parser.parse_args()

    # Загрузить данные
    cat_path = DATA_DIR / "categorized_answers.json"
    if not cat_path.exists():
        print(f"Файл не найден: {cat_path}")
        sys.exit(1)

    cat_data = json.loads(cat_path.read_text(encoding="utf-8"))
    categories = cat_data.get("categories", {})
    display_forms = cat_data.get("display_forms", {})

    # Загрузить Wikipedia-описания
    enriched = {}
    enriched_path = DATA_DIR / "enriched_entities.json"
    if enriched_path.exists():
        enriched = json.loads(enriched_path.read_text(encoding="utf-8")).get("entities", {})

    # Собрать все сущности
    entities = []
    for entity_type, items in categories.items():
        for key, freq in items:
            entities.append({
                "key": key,
                "display": display_forms.get(key, key),
                "type": entity_type,
                "frequency": freq,
                "description": enriched.get(key, {}).get("short_description", ""),
            })

    # Загрузить существующий маппинг
    existing_themes = {}
    if OUTPUT_PATH.exists():
        existing_data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        existing_themes = existing_data.get("entity_themes", {})

    # Фильтровать уже классифицированные
    if not args.force:
        entities = [e for e in entities if e["key"] not in existing_themes]

    if args.limit:
        entities = entities[:args.limit]

    total = len(entities)
    print(f"\n{'═' * 60}")
    print(f"  Классификация сущностей в 14 категорий ЧГК")
    print(f"{'═' * 60}")
    print(f"  Модель:    {args.model}")
    print(f"  Сущностей: {total}")
    print(f"  Force:     {args.force}")
    print(f"  Уже есть:  {len(existing_themes)}")
    print(f"{'═' * 60}\n")

    if total == 0:
        print("Нет сущностей для классификации.")
        return

    # Создать провайдер
    from classifier.providers import create_provider
    provider = create_provider(args.provider, model=args.model)

    # Маппинг номер → название категории
    cat_names = {i: name_ru for i, (_, name_ru, _) in enumerate(TAXONOMY, 1)}

    classified = 0
    errors = 0
    themes = dict(existing_themes)  # копия

    try:
        for i, ent in enumerate(entities):
            messages = build_messages(ent["display"], ent["type"], ent["description"])

            print(f"  [{i+1}/{total}] {ent['display']:30s} ", end="", flush=True)

            try:
                response = provider.chat(messages, max_tokens=30)
            except Exception as e:
                errors += 1
                print(f"ОШИБКА: {e}")
                continue

            if not response:
                errors += 1
                print("пустой ответ")
                continue

            parsed = parse_response(response)
            if not parsed:
                errors += 1
                print(f"не распарсился: {response[:50]}")
                continue

            cat_num = parsed["cat"]
            cat_name = cat_names.get(cat_num, "?")

            themes[ent["key"]] = {
                "category_num": cat_num,
                "category": cat_name,
                "entity_type": ent["type"],
                "confidence": 1.0,
            }
            classified += 1
            print(f"→ {cat_name}")

            # Промежуточное сохранение каждые 30
            if classified % 30 == 0:
                _save(themes, cat_names, args.model)

    except KeyboardInterrupt:
        print("\n\nПрервано. Сохраняю прогресс...")

    # Финальное сохранение
    _save(themes, cat_names, args.model)

    print(f"\n{'═' * 60}")
    print(f"  ОТЧЁТ")
    print(f"{'═' * 60}")
    print(f"  Классифицировано: {classified}")
    print(f"  Ошибок:           {errors}")
    print(f"  Всего в маппинге: {len(themes)}")
    print(f"  Стоимость:        ${provider.estimated_cost:.4f}")
    print(f"{'═' * 60}")
    print(f"  Файл: {OUTPUT_PATH}")

    # Распределение по категориям
    from collections import Counter
    dist = Counter(t["category"] for t in themes.values())
    print(f"\n  Распределение:")
    for cat_name, cnt in dist.most_common():
        print(f"    {cat_name:30s} {cnt}")


def _save(themes: dict, cat_names: dict, model: str):
    """Сохранить thematic_mapping.json."""
    # Построить by_category
    by_category = {}
    for key, theme in themes.items():
        cat_name = theme["category"]
        if cat_name not in by_category:
            by_category[cat_name] = []
        by_category[cat_name].append(key)

    output = {
        "generated_at": datetime.now().isoformat(),
        "model": model,
        "total_entities": len(themes),
        "entity_themes": themes,
        "by_category": by_category,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
