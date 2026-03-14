#!/usr/bin/env python3
"""Генерация фактов из Wikipedia для джентльменского набора.

Берёт топовые сущности из thematic_mapping.json, загружает статьи из Wikipedia,
LLM выжимает ключевые факты для ЧГК, и постит в группу с топиками.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID
from scripts.wikipedia_client import WikipediaClient

GENTLEMAN_SET_DIR = Path(__file__).parent.parent / "data" / "gentleman_set"
THEMATIC_PATH = GENTLEMAN_SET_DIR / "thematic_mapping.json"
WIKI_CACHE_PATH = GENTLEMAN_SET_DIR / "wiki_cache.json"
WIKI_HINTS_PATH = GENTLEMAN_SET_DIR / "wiki_hints.json"
FACTS_CACHE_PATH = GENTLEMAN_SET_DIR / "wiki_facts_cache.json"

# Общие бытовые слова — не интересны для ДН, пропускаем.
# Оставляем личности, произведения, понятия.
GENERIC_DENYLIST = {
    "зеркало", "яблоко", "тень", "борода", "кот", "лев", "роза", "мост",
    "замок", "конек", "близнец", "язык", "молния", "дом", "дерево", "река",
    "гора", "море", "луна", "солнце", "звезда", "огонь", "вода", "камень",
    "кольцо", "корона", "маска", "нож", "ключ", "часы", "лампа", "свеча",
    "палец", "рука", "глаз", "голова", "сердце", "кровь", "крест", "меч",
    "щит", "стена", "башня", "мяч", "колесо", "якорь", "цепь", "узел",
    "нить", "игла", "пуля", "бомба", "флаг", "шляпа", "перо", "крыло",
    "хвост", "рог", "лапа", "гнездо", "яйцо", "зерно", "цветок", "лист",
    "корень", "ветка", "трава", "мох", "гриб", "паук", "змея", "волк",
    "медведь", "орёл", "ворон", "сова", "ёж", "заяц", "лиса", "обезьяна",
    "слон", "конь", "собака", "кошка", "мышь", "рыба", "кит", "дельфин",
    "черепаха", "лягушка", "бабочка", "муравей", "пчела", "ворона", "голубь",
}

WIKI_FACTS_PROMPT = """Ты помогаешь команде ЧГК готовиться к турниру. Извлеки интересные факты из Wikipedia-статьи.

ПРАВИЛА:
1. Извлекай факты ТОЛЬКО из текста статьи. НЕ добавляй от себя.
2. 7-10 фактов: даты, числа, имена, этимология, необычные связи, рекорды.
3. НЕ пиши банальности.

ФОРМАТ — строго простой текст, нумерованный список:
1. Первый факт.
2. Второй факт.
3. Третий факт.

НЕ используй JSON, markdown, HTML или другую разметку. Только простой нумерованный список."""


def _clean_facts_response(raw: str) -> str:
    """Очистить ответ LLM от JSON-обёрток и мусора."""
    import re

    text = raw.strip()

    # Попытка распарсить JSON (модели часто оборачивают)
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)

            if isinstance(data, dict):
                # {"response": "1. Факт..."} или {"facts": ["1. ...", "2. ..."]}
                # или {"1": "Факт", "2": "Факт"}
                for key, val in data.items():
                    if isinstance(val, str) and "1." in val:
                        text = val
                        break
                    if isinstance(val, list):
                        # {"facts": ["1. Факт", "2. Факт"]}
                        items = [str(item) for item in val if str(item).strip() not in (".", ". ", "")]
                        if items:
                            text = "\n".join(items)
                            break
                else:
                    # {"1": "Факт", "2": "Факт"} — numbered dict keys
                    numbered = []
                    for k in sorted(data.keys(), key=lambda x: str(x)):
                        v = data[k]
                        if isinstance(v, str) and len(v) > 10:
                            s = v if v.startswith(str(k)) else f"{k}. {v}"
                            numbered.append(s)
                    if numbered:
                        text = "\n".join(numbered)

            elif isinstance(data, list):
                items = [str(item) for item in data if str(item).strip() not in (".", ". ", "")]
                if items:
                    text = "\n".join(items)

        except json.JSONDecodeError:
            pass

    # Убрать schema.org мусор
    if "@context" in text or "schema.org" in text:
        match = re.search(r'"articleBody"\s*:\s*"(.*?)"', text, re.DOTALL)
        if match:
            text = match.group(1)
        else:
            return ""

    text = text.strip()

    # Проверить что остался осмысленный текст
    if len(text) < 50 or "1." not in text:
        return ""

    return text


def _is_generic(name: str) -> bool:
    """Проверить, является ли сущность слишком общим бытовым словом."""
    return name.lower().strip() in GENERIC_DENYLIST


def load_entities_by_category(
    category: str = None, skip_generic: bool = True,
) -> Dict[str, dict]:
    """Загрузить сущности из thematic_mapping, опционально фильтр по категории."""
    data = json.loads(THEMATIC_PATH.read_text(encoding="utf-8"))
    entities = data["entity_themes"]
    if category:
        entities = {k: v for k, v in entities.items() if v["category"] == category}
    if skip_generic:
        before = len(entities)
        entities = {k: v for k, v in entities.items() if not _is_generic(k)}
        skipped = before - len(entities)
        if skipped:
            print(f"[Wiki] Пропущено {skipped} общих слов (зеркало, яблоко и т.п.)")
    return entities


def load_facts_cache() -> dict:
    """Загрузить кэш фактов."""
    if FACTS_CACHE_PATH.exists():
        return json.loads(FACTS_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_facts_cache(cache: dict):
    """Сохранить кэш фактов."""
    FACTS_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


RETRY_PROMPT = (
    "Выдели 7-10 интересных фактов из текста ниже. "
    "Ответь ТОЛЬКО нумерованным списком на русском языке. "
    "НЕ используй JSON. Пример формата:\n"
    "1. Первый факт.\n2. Второй факт.\n3. Третий факт."
)


def generate_facts(
    entity_name: str,
    wiki_extract: str,
    provider_name: str = "openrouter",
    model: str = None,
    max_retries: int = 2,
) -> Optional[str]:
    """Сгенерировать факты через LLM из Wikipedia-статьи с retry."""
    from classifier.providers import create_provider

    provider = create_provider(provider_name, model=model)

    for attempt in range(max_retries):
        system = WIKI_FACTS_PROMPT if attempt == 0 else RETRY_PROMPT

        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Сущность: {entity_name}\n\nСтатья Wikipedia:\n{wiki_extract[:4000]}",
            },
        ]

        raw = provider.chat(messages, max_tokens=1200)
        if not raw:
            continue

        result = _clean_facts_response(raw)
        if result:
            return result

    return None


def format_post(entity_name: str, category: str, facts: str, wiki_url: str) -> str:
    """Форматировать пост для отправки в группу."""
    title = entity_name.title()
    return (
        f"<b>{title}</b>\n\n"
        f"{facts}\n\n"
        f'<a href="{wiki_url}">Wikipedia</a>'
    )


def run_generate(
    category: str = None,
    limit: int = 5,
    provider_name: str = "openrouter",
    model: str = None,
    post_to_group: bool = False,
) -> List[dict]:
    """Основной пайплайн: загрузить сущности -> Wikipedia -> LLM -> факты."""
    entities = load_entities_by_category(category)
    if not entities:
        print(f"[Wiki] Нет сущностей для категории: {category}")
        return []

    wiki = WikipediaClient(
        cache_path=WIKI_CACHE_PATH,
        hints_path=WIKI_HINTS_PATH if WIKI_HINTS_PATH.exists() else None,
    )
    facts_cache = load_facts_cache()

    results = []
    count = 0

    for entity_key, entity_data in entities.items():
        if count >= limit:
            break

        cat = entity_data["category"]

        # Пропускаем если факты уже есть
        if entity_key in facts_cache:
            print(f"[Wiki] {entity_key}: уже в кэше, пропускаю")
            continue

        # Получить статью из Wikipedia
        wiki_data = wiki.fetch_entity(entity_key, entity_key)
        if not wiki_data or not wiki_data.get("extract"):
            print(f"[Wiki] {entity_key}: не найдено в Wikipedia")
            continue

        # Загрузить полный extract (не только intro, а всю статью до 4000 символов)
        full_extract = wiki.get_extract(wiki_data["title"], chars=4000, intro_only=False)
        if not full_extract or len(full_extract) < 100:
            print(f"[Wiki] {entity_key}: статья слишком короткая")
            continue

        # Сгенерировать факты через LLM
        print(f"[Wiki] {entity_key} ({cat}): генерирую факты...")
        facts_text = generate_facts(entity_key, full_extract, provider_name, model)

        if not facts_text:
            print(f"[Wiki] {entity_key}: LLM не вернул ответ")
            continue

        # Кэшируем
        facts_cache[entity_key] = {
            "category": cat,
            "facts": facts_text,
            "wiki_url": wiki_data["wiki_url"],
            "wiki_title": wiki_data["title"],
        }
        save_facts_cache(facts_cache)

        result = {
            "entity": entity_key,
            "category": cat,
            "facts": facts_text,
            "wiki_url": wiki_data["wiki_url"],
        }
        results.append(result)
        count += 1

        print(f"[Wiki] {entity_key}: OK")

    wiki.save()

    # Постинг в группу
    if post_to_group and results:
        _post_results(results)

    return results


def _post_results(results: List[dict]):
    """Отправить результаты в ТГ-группу."""
    from scripts.tg_bot_digest import DigestBot

    if not TG_DIGEST_BOT_TOKEN or not TG_DIGEST_CHAT_ID:
        print("[Wiki] Не настроен TG_DIGEST_BOT_TOKEN / TG_DIGEST_CHAT_ID")
        return

    bot = DigestBot(TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID)

    for r in results:
        topic_id = bot.get_or_create_topic(r["category"])
        message = format_post(r["entity"], r["category"], r["facts"], r["wiki_url"])

        try:
            bot._api(
                "sendMessage",
                chat_id=bot.chat_id,
                message_thread_id=topic_id,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
            print(f"[Wiki] Отправлен: {r['entity']} -> {r['category']}")
            time.sleep(0.5)
        except RuntimeError as e:
            print(f"[Wiki] Ошибка отправки {r['entity']}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Генерация Wikipedia-фактов для джентльменского набора"
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Фильтр по категории (например, 'Литература')",
    )
    parser.add_argument(
        "--limit", type=int, default=5,
        help="Максимум сущностей (default: 5)",
    )
    parser.add_argument(
        "--provider", type=str, default="openrouter",
        help="LLM-провайдер (default: openrouter)",
    )
    parser.add_argument(
        "--model", type=str, default="openai/gpt-4o-mini",
        help="Модель LLM (default: openai/gpt-4o-mini)",
    )
    parser.add_argument(
        "--no-filter", action="store_true",
        help="Не фильтровать общие бытовые слова",
    )
    parser.add_argument(
        "--post", action="store_true",
        help="Отправить в ТГ-группу после генерации",
    )
    parser.add_argument(
        "--list-categories", action="store_true",
        help="Показать категории и количество сущностей",
    )

    args = parser.parse_args()

    if args.list_categories:
        from collections import Counter
        entities = load_entities_by_category()
        cats = Counter(v["category"] for v in entities.values())
        for cat, cnt in cats.most_common():
            print(f"  {cat}: {cnt} сущностей")
        return

    results = run_generate(
        category=args.category,
        limit=args.limit,
        provider_name=args.provider,
        model=args.model,
        post_to_group=args.post,
    )

    if results:
        print(f"\n[Wiki] Сгенерировано фактов: {len(results)}")
        for r in results:
            print(f"  {r['entity']} ({r['category']})")
    else:
        print("[Wiki] Нет новых фактов для генерации")


if __name__ == "__main__":
    main()
