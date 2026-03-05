"""Обогащение джентльменского набора описаниями из Wikipedia.

Берёт categorized_answers.json, для каждой сущности получает
краткое описание из русской Википедии (1-2 предложения).

Использование:
    python scripts/enrich_gentleman.py
    python scripts/enrich_gentleman.py --min-freq 10 --top-per-category 10
    python scripts/enrich_gentleman.py --force
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PROJECT_ROOT
from scripts.wikipedia_client import WikipediaClient

DATA_DIR = PROJECT_ROOT / "data" / "gentleman_set"
CACHE_PATH = DATA_DIR / "wiki_cache.json"
OUTPUT_PATH = DATA_DIR / "enriched_entities.json"


def load_categorized() -> dict:
    """Загрузить categorized_answers.json."""
    path = DATA_DIR / "categorized_answers.json"
    if not path.exists():
        print(f"Файл не найден: {path}")
        print("Сначала запустите: python scripts/categorize_gentleman.py")
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def load_existing_enrichment() -> dict:
    """Загрузить существующий enriched_entities.json."""
    if OUTPUT_PATH.exists():
        return json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    return {"entities": {}, "not_found": []}


def save_enrichment(data: dict):
    """Сохранить enriched_entities.json."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Обогащение джентльменского набора описаниями из Wikipedia"
    )
    parser.add_argument(
        "--min-freq", type=int, default=5,
        help="Мин. частота для обогащения (default: 5)",
    )
    parser.add_argument(
        "--top-per-category", type=int, default=50,
        help="Макс. сущностей на категорию (default: 50)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Перезаписать уже обогащённые сущности",
    )
    args = parser.parse_args()

    # Загрузка данных
    cat_data = load_categorized()
    categories = cat_data["categories"]
    display_forms = cat_data.get("display_forms", {})

    # Собрать сущности для обогащения
    entities_to_enrich = []
    for cat_name, items in categories.items():
        count = 0
        for key, freq in items:
            if freq < args.min_freq:
                break
            if count >= args.top_per_category:
                break
            entities_to_enrich.append({
                "key": key,
                "display_name": display_forms.get(key, key),
                "category": cat_name,
                "frequency": freq,
            })
            count += 1

    total = len(entities_to_enrich)
    print(f"\n{'═' * 60}")
    print(f"  Обогащение джентльменского набора из Wikipedia")
    print(f"{'═' * 60}")
    print(f"  Сущностей:          {total}")
    print(f"  Мин. частота:       {args.min_freq}")
    print(f"  Макс. на категорию: {args.top_per_category}")
    print(f"  Force:              {args.force}")
    print(f"{'═' * 60}\n")

    if total == 0:
        print("Нет сущностей для обогащения.")
        return

    # Загрузить существующие данные
    existing = load_existing_enrichment()
    enriched = existing.get("entities", {})
    not_found = set(existing.get("not_found", []))

    # Wikipedia клиент
    wiki = WikipediaClient(CACHE_PATH)

    enriched_count = 0
    skipped_count = 0
    not_found_count = 0

    try:
        for i, ent in enumerate(entities_to_enrich):
            key = ent["key"]
            display = ent["display_name"]

            # Пропустить уже обогащённые
            if not args.force and key in enriched:
                skipped_count += 1
                continue
            if not args.force and key in not_found:
                skipped_count += 1
                continue

            print(f"  [{i+1}/{total}] {display} ({ent['category']}, {ent['frequency']}x) ... ", end="", flush=True)

            result = wiki.fetch_entity(key, display, force=args.force)

            if result is None:
                not_found.add(key)
                not_found_count += 1
                print("не найдено")
                continue

            enriched[key] = {
                "display_name": display,
                "category": ent["category"],
                "frequency": ent["frequency"],
                "wiki_title": result["title"],
                "short_description": result["short_description"],
                "wiki_url": result["wiki_url"],
            }
            enriched_count += 1
            desc_short = result["short_description"][:80]
            print(f"✓ {desc_short}")

            # Сохраняем каждые 20 сущностей
            if enriched_count % 20 == 0:
                _save_all(enriched, not_found, wiki)

    except KeyboardInterrupt:
        print("\n\nПрервано пользователем. Сохраняю прогресс...")

    # Финальное сохранение
    _save_all(enriched, not_found, wiki)

    print(f"\n{'═' * 60}")
    print(f"  ОТЧЁТ")
    print(f"{'═' * 60}")
    print(f"  Обогащено:       {enriched_count}")
    print(f"  Пропущено:       {skipped_count}")
    print(f"  Не найдено:      {not_found_count}")
    print(f"  Всего в базе:    {len(enriched)}")
    print(f"{'═' * 60}")
    print(f"  Файл: {OUTPUT_PATH}")


def _save_all(enriched: dict, not_found: set, wiki: WikipediaClient):
    """Сохранить обогащённые данные и кэш."""
    output = {
        "generated_at": datetime.now().isoformat(),
        "total_enriched": len(enriched),
        "entities": enriched,
        "not_found": sorted(not_found),
    }
    save_enrichment(output)
    wiki.save()


if __name__ == "__main__":
    main()
