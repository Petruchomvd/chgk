#!/usr/bin/env python3
"""Извлечение фактов из статей chtv.ru (Чтиво) для подготовки к ЧГК.

Пайплайн: sitemap → _payload.json → LLM извлечение → категоризация → TG.

Использование:
    python scripts/chtv_facts.py --fetch-catalog
    python scripts/chtv_facts.py --run --limit 10
    python scripts/chtv_facts.py --run --limit 50 --model openai/gpt-4o-mini
    python scripts/chtv_facts.py --post --dry-run
    python scripts/chtv_facts.py --post
    python scripts/chtv_facts.py --stats
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: F401  — loads .env

GENTLEMAN_SET_DIR = Path(__file__).parent.parent / "data" / "gentleman_set"
CHTV_FACTS_CACHE_PATH = GENTLEMAN_SET_DIR / "chtv_facts_cache.json"

# 14 категорий таксономии (как в youtube_facts.py)
CATEGORIES = [
    "История", "Литература", "Наука и технологии", "География",
    "Искусство", "Музыка", "Кино и театр", "Спорт",
    "Язык и лингвистика", "Религия и мифология", "Общество и политика",
    "Быт и повседневность", "Природа и животные", "Логика и wordplay",
]

# --- Промпты ---

EXTRACT_PROMPT = """Ты помогаешь команде ЧГК готовиться к турниру.
Из статьи онлайн-журнала «Чтиво» извлеки интересные факты.

ПРАВИЛА:
1. Извлекай ТОЛЬКО конкретные факты из текста: даты, числа, имена, этимологию, необычные связи, рекорды, малоизвестные детали.
2. Каждый факт — 2-3 предложения. Излагай СУТЬ: кто, что, когда, какая связь.
3. НЕ добавляй пустых комментариев вроде «это подчёркивает важность...», «это свидетельствует о том, что...», «это показывает, как...», «что делает его...». Никакой воды и оценок — только голые факты и связи.
4. НЕ извлекай: мнения автора, рекламу, общие фразы без конкретики.
5. Минимум 5, максимум 15 фактов на статью.

ФОРМАТ — строго нумерованный список:
1. Конкретный факт с деталями.
2. Следующий факт.
3. Ещё один факт."""

CATEGORIZE_PROMPT = """Ты эксперт по ЧГК (Что? Где? Когда?). Для каждого факта определи:
1. КАТЕГОРИЮ (одна из 14):
   1-История, 2-Литература, 3-Наука и технологии, 4-География, 5-Искусство,
   6-Музыка, 7-Кино и театр, 8-Спорт, 9-Язык и лингвистика,
   10-Религия и мифология, 11-Общество и политика, 12-Быт и повседневность,
   13-Природа и животные, 14-Логика и wordplay

2. ЗАЦЕПКУ для ЧГК — короткая фраза (5-15 слов): почему этот факт может встретиться в вопросе,
   какая необычная связь или подвох возможны.

3. Если факт бессмысленный/неполный — пометь категорией 0.

Ответь СТРОГО в формате JSON (массив объектов):
[
  {{"n": 1, "cat": 3, "hook": "связь физики и быта"}},
  {{"n": 2, "cat": 1, "hook": "малоизвестный факт о войне"}},
  {{"n": 3, "cat": 0, "hook": null}}
]

Факты:
{facts}"""


# --- Кэш ---

def load_cache() -> dict:
    if CHTV_FACTS_CACHE_PATH.exists():
        return json.loads(CHTV_FACTS_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict):
    CHTV_FACTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHTV_FACTS_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- LLM ---

def _create_provider(provider_name: str, model: Optional[str] = None):
    from classifier.providers import create_provider
    return create_provider(provider_name, model=model)


def _parse_facts(raw: str) -> List[str]:
    """Парсинг фактов из ответа LLM."""
    import re

    if not raw or raw.strip().upper() in ("НЕТ", "НЕТ.", "NO", "NONE"):
        return []

    stripped = raw.strip()
    facts = []
    for line in stripped.split("\n"):
        line = line.strip()
        m = re.match(r"^\d+[.)]\s*(.+)", line)
        if m:
            fact = m.group(1).strip().strip('"')
            if len(fact) > 30:
                facts.append(fact)
            continue
        m = re.match(r"^[-•*]\s+(.+)", line)
        if m:
            fact = m.group(1).strip()
            if len(fact) > 30:
                facts.append(fact)

    return facts


def extract_facts_from_article(
    article_text: str,
    article_title: str,
    provider_name: str = "openrouter",
    model: Optional[str] = None,
) -> List[str]:
    """Извлечь факты из текста статьи."""
    provider = _create_provider(provider_name, model=model)

    # Ограничиваем текст (длинные статьи)
    text = article_text[:8000]

    messages = [
        {"role": "system", "content": EXTRACT_PROMPT},
        {"role": "user", "content": f"Статья: «{article_title}»\n\n{text}"},
    ]

    raw = provider.chat(messages, max_tokens=3000, json_mode=False)
    if not raw:
        return []

    return _parse_facts(raw)


def categorize_facts(
    facts: List[str],
    provider_name: str = "openrouter",
    model: Optional[str] = None,
) -> List[Dict]:
    """Категоризировать факты + добавить ЧГК-зацепки."""
    import re

    if not facts:
        return []

    provider = _create_provider(provider_name, model=model)
    facts_text = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts))
    prompt = CATEGORIZE_PROMPT.replace("{facts}", facts_text)

    messages = [
        {"role": "system", "content": "Ты эксперт по ЧГК. Ответь ТОЛЬКО валидным JSON."},
        {"role": "user", "content": prompt},
    ]

    raw = provider.chat(messages, max_tokens=2000, json_mode=True)
    if not raw:
        return [{"text": f, "category": "Разное", "hook": ""} for f in facts]

    try:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            ratings = json.loads(m.group())
        else:
            ratings = json.loads(raw)

        enriched = []
        for r in ratings:
            idx = r.get("n", 0) - 1
            cat_num = r.get("cat", 0)
            if cat_num == 0 or idx < 0 or idx >= len(facts):
                continue
            cat_name = CATEGORIES[cat_num - 1] if 1 <= cat_num <= 14 else "Разное"
            hook = r.get("hook", "")
            enriched.append({"text": facts[idx], "category": cat_name, "hook": hook})
        return enriched

    except (json.JSONDecodeError, KeyError, IndexError):
        return [{"text": f, "category": "Разное", "hook": ""} for f in facts]


def _deduplicate_facts(facts: list, threshold: float = 0.6) -> list:
    """Дедупликация фактов (Jaccard similarity по леммам)."""
    def get_text(f):
        return f["text"] if isinstance(f, dict) else f

    try:
        import pymorphy3
        morph = pymorphy3.MorphAnalyzer()
    except ImportError:
        seen = set()
        result = []
        for f in facts:
            key = get_text(f).lower()[:80]
            if key not in seen:
                seen.add(key)
                result.append(f)
        return result

    def lemmatize(text: str) -> set:
        words = set()
        for w in text.lower().split():
            w = w.strip(".,!?;:()\"'«»—-")
            if len(w) > 2:
                parsed = morph.parse(w)
                if parsed:
                    words.add(parsed[0].normal_form)
        return words

    result = []
    seen_lemmas = []
    for fact in facts:
        lemmas = lemmatize(get_text(fact))
        if not lemmas:
            continue
        is_dup = False
        for prev_lemmas in seen_lemmas:
            intersection = lemmas & prev_lemmas
            union = lemmas | prev_lemmas
            if union and len(intersection) / len(union) > threshold:
                is_dup = True
                break
        if not is_dup:
            result.append(fact)
            seen_lemmas.append(lemmas)
    return result


# --- Пайплайн ---

def process_article(
    slug: str,
    provider_name: str = "openrouter",
    model: Optional[str] = None,
    force: bool = False,
) -> Optional[Dict]:
    """Обработать одну статью: fetch → extract → categorize."""
    from scraper.chtv_parser import fetch_article

    cache = load_cache()

    if not force and slug in cache:
        n = len(cache[slug].get("facts", []))
        if n > 0:
            return cache[slug]

    # 1. Загрузить статью
    article = fetch_article(slug)
    if not article:
        print(f"  [{slug}] Не удалось загрузить")
        return None

    title = article.get("title", slug)
    text = article.get("text", "")
    print(f"  [{slug}] «{title[:60]}» ({len(text)} симв)... ", end="", flush=True)

    if len(text) < 200:
        print("слишком короткая")
        return None

    # 2. Извлечь факты
    raw_facts = extract_facts_from_article(text, title, provider_name, model)
    if not raw_facts:
        print("0 фактов")
        return None

    # 3. Категоризировать
    enriched = categorize_facts(raw_facts, provider_name, model)
    enriched = _deduplicate_facts(enriched)
    print(f"{len(enriched)} фактов")

    # 4. Кэш
    result = {
        "title": title,
        "url": article["url"],
        "author": article.get("author", ""),
        "date": article.get("date", ""),
        "facts": enriched,
        "extracted_at": datetime.now().isoformat(),
        "provider": provider_name,
        "model": model,
    }

    # Сохраняем posted_to_tg если статья уже была отправлена
    if slug in cache and cache[slug].get("posted_to_tg"):
        result["posted_to_tg"] = cache[slug]["posted_to_tg"]
    cache[slug] = result
    save_cache(cache)
    return result


def run_batch(
    limit: int = 10,
    provider_name: str = "openrouter",
    model: Optional[str] = None,
    force: bool = False,
    offset: int = 0,
):
    """Обработать пакет статей из каталога."""
    from scraper.chtv_parser import load_catalog, REQUEST_DELAY

    catalog = load_catalog()
    cache = load_cache()

    # Пропускаем уже обработанные (если не force)
    if not force:
        todo = [a for a in catalog if a["slug"] not in cache]
    else:
        # При --force пропускаем уже опубликованные (они уже переобработаны)
        todo = [a for a in catalog
                if not cache.get(a["slug"], {}).get("posted_to_tg")]

    todo = todo[offset:offset + limit]
    print(f"[CHTV] Обработка {len(todo)} статей (всего в каталоге: {len(catalog)}, "
          f"обработано: {len(cache)})")

    total_facts = 0
    for i, article in enumerate(todo):
        print(f"\n[{i + 1}/{len(todo)}]", end="")
        result = process_article(article["slug"], provider_name, model, force)
        if result:
            total_facts += len(result.get("facts", []))
        time.sleep(REQUEST_DELAY)

    print(f"\n[CHTV] Готово: {total_facts} фактов из {len(todo)} статей")


# --- Telegram ---

def post_to_telegram(
    dry_run: bool = False,
    limit: Optional[int] = None,
):
    """Отправить неотправленные факты в ТГ по категориям.

    Формат: по категориям → внутри разбивка по статьям со ссылками.
    """
    from scripts.youtube_facts import CATEGORY_EMOJI, _plural_facts
    from scripts.tg_bot_digest import DigestBot, _escape_html
    from config import TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID

    cache = load_cache()

    # Собираем неотправленные статьи
    slugs_to_post = []
    for slug, data in cache.items():
        if data.get("posted_to_tg"):
            continue
        if data.get("facts"):
            slugs_to_post.append(slug)

    if limit:
        slugs_to_post = slugs_to_post[:limit]

    if not slugs_to_post:
        print("[TG] Нет новых фактов для отправки")
        return 0

    # Группируем: категория → список (статья, факт)
    by_cat: Dict[str, List[dict]] = defaultdict(list)
    for slug in slugs_to_post:
        data = cache[slug]
        article_info = {
            "title": data.get("title", slug),
            "url": data.get("url", f"https://chtv.ru/{slug}"),
        }
        for fact in data.get("facts", []):
            by_cat[fact.get("category", "Разное")].append({
                "text": fact["text"],
                "hook": fact.get("hook", ""),
                "article_title": article_info["title"],
                "article_url": article_info["url"],
            })

    total_facts = sum(len(facts) for facts in by_cat.values())
    print(f"[TG] К отправке: {total_facts} фактов из {len(slugs_to_post)} статей, "
          f"{len(by_cat)} категорий")

    if dry_run:
        for cat, facts in sorted(by_cat.items()):
            print(f"  [{cat}] {len(facts)} фактов")
            # Показываем по статьям
            articles = defaultdict(list)
            for f in facts:
                articles[f["article_title"]].append(f)
            for title, afacts in articles.items():
                print(f"    📰 {title}")
                for f in afacts:
                    print(f"      • {f['text'][:80]}...")
        print(f"\n[TG] Dry run: {total_facts} фактов в {len(by_cat)} категориях")
        return 0

    if not TG_DIGEST_BOT_TOKEN or not TG_DIGEST_CHAT_ID:
        print("[TG] Ошибка: TG_DIGEST_BOT_TOKEN / TG_DIGEST_CHAT_ID не заданы")
        return 0

    bot = DigestBot(TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID)
    sent = 0

    for cat, facts in sorted(by_cat.items()):
        topic_id = bot.get_or_create_topic(cat)
        emoji = CATEGORY_EMOJI.get(cat, "\U0001F4CC")

        # Группируем факты по статьям (сохраняя порядок)
        articles_order = []
        articles_facts: Dict[str, list] = defaultdict(list)
        for f in facts:
            key = f["article_url"]
            if key not in articles_facts:
                articles_order.append({
                    "title": f["article_title"],
                    "url": f["article_url"],
                })
            articles_facts[key].append(f)

        # Формируем сообщения с разбивкой по статьям
        header = f"{emoji} <b>{_escape_html(cat)}</b>  #Чтиво"
        messages_to_send = []
        chunk_lines = [header, ""]
        part = 1

        for article in articles_order:
            article_header = (
                f'\U0001F4F0 <a href="{article["url"]}">'
                f'{_escape_html(article["title"])}</a>'
            )

            for fi, f in enumerate(articles_facts[article["url"]]):
                fact_lines = []
                if fi == 0:
                    fact_lines.append(article_header)
                fact_text = _escape_html(f["text"])
                fact_lines.append(f"\u25AA\uFE0F {fact_text}")
                if f.get("hook"):
                    fact_lines.append(
                        f"    <i>\U0001F517 {_escape_html(f['hook'])}</i>"
                    )

                # Проверяем лимит 4096
                test = "\n".join(chunk_lines + fact_lines)
                if len(test) > 3800 and len(chunk_lines) > 2:
                    messages_to_send.append("\n".join(chunk_lines))
                    part += 1
                    chunk_lines = [
                        f"{emoji} <b>{_escape_html(cat)}</b> (ч.{part})  #Чтиво",
                        "",
                        article_header,
                    ]
                chunk_lines.extend(fact_lines)

            chunk_lines.append("")  # пустая строка между статьями

        # Футер последнего чанка
        chunk_lines.append("\u2014" * 15)
        count_str = f"{len(facts)} факт" + _plural_facts(len(facts))
        chunk_lines.append(f"\U0001F4CA {count_str} из {len(articles_order)} статей")
        messages_to_send.append("\n".join(chunk_lines))

        # Отправляем
        for msg in messages_to_send:
            for attempt in range(3):
                try:
                    bot._api(
                        "sendMessage",
                        chat_id=bot.chat_id,
                        message_thread_id=topic_id,
                        text=msg,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    sent += 1
                    time.sleep(1.5)
                    break
                except RuntimeError as e:
                    err = str(e)
                    if "Too Many Requests" in err:
                        import re as _re
                        m = _re.search(r"retry after (\d+)", err)
                        wait = int(m.group(1)) + 1 if m else 30
                        print(f"  [{cat}] Rate limit, жду {wait}с...")
                        time.sleep(wait)
                    else:
                        print(f"  [{cat}] Ошибка: {e}")
                        break

        print(f"  {emoji} [{cat}] {len(facts)} фактов, "
              f"{len(messages_to_send)} сообщений → отправлено")
        time.sleep(1)

    # Помечаем статьи как отправленные
    for slug in slugs_to_post:
        cache[slug]["posted_to_tg"] = datetime.now().isoformat()
    save_cache(cache)

    print(f"\n[TG] Отправлено: {sent} сообщений")
    return sent


def show_stats():
    """Показать статистику обработки."""
    from scraper.chtv_parser import load_catalog

    catalog = load_catalog()
    cache = load_cache()

    total_facts = 0
    posted = 0
    by_cat = defaultdict(int)
    for slug, data in cache.items():
        facts = data.get("facts", [])
        total_facts += len(facts)
        if data.get("posted_to_tg"):
            posted += 1
        for f in facts:
            by_cat[f.get("category", "Разное")] += 1

    print(f"[CHTV] Каталог: {len(catalog)} статей")
    print(f"[CHTV] Обработано: {len(cache)} статей, {total_facts} фактов")
    print(f"[CHTV] Отправлено в ТГ: {posted} статей")
    print(f"\nПо категориям:")
    for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="chtv.ru → факты для ЧГК")
    parser.add_argument("--fetch-catalog", action="store_true",
                        help="Скачать каталог из sitemap")
    parser.add_argument("--run", action="store_true",
                        help="Обработать статьи")
    parser.add_argument("--limit", type=int, default=10,
                        help="Сколько статей обработать (default: 10)")
    parser.add_argument("--offset", type=int, default=0,
                        help="Пропустить первые N необработанных")
    parser.add_argument("--provider", default="openrouter",
                        help="LLM-провайдер")
    parser.add_argument("--model", default="openai/gpt-4o-mini",
                        help="Модель LLM (default: gpt-4o-mini)")
    parser.add_argument("--force", action="store_true",
                        help="Пересчитать даже если есть в кэше")
    parser.add_argument("--post", action="store_true",
                        help="Отправить неотправленные факты в ТГ")
    parser.add_argument("--post-limit", type=int, default=None,
                        help="Максимум статей для постинга")
    parser.add_argument("--dry-run", action="store_true",
                        help="Показать что будет отправлено")
    parser.add_argument("--stats", action="store_true",
                        help="Показать статистику")

    args = parser.parse_args()

    if args.fetch_catalog:
        from scraper.chtv_parser import fetch_catalog_from_sitemap
        fetch_catalog_from_sitemap()
        return

    if args.stats:
        show_stats()
        return

    if args.post:
        post_to_telegram(dry_run=args.dry_run, limit=args.post_limit)
        return

    if args.run:
        run_batch(
            limit=args.limit,
            provider_name=args.provider,
            model=args.model,
            force=args.force,
            offset=args.offset,
        )
        return

    parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
