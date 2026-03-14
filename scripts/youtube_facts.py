#!/usr/bin/env python3
"""Извлечение фактов из YouTube-видео (Своя игра и др.) для подготовки к ЧГК.

Пайплайн: YouTube URL → Whisper → LLM извлечение → LLM категоризация → markdown.

Использование:
    python scripts/youtube_facts.py --url "https://youtube.com/watch?v=..."
    python scripts/youtube_facts.py --url "..." --model openai/gpt-4o-mini
    python scripts/youtube_facts.py --list
    python scripts/youtube_facts.py --show VIDEO_ID
"""

import argparse
import json
import re
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

FACTS_DIR = Path(__file__).parent.parent / "data" / "youtube" / "facts"
GENTLEMAN_SET_DIR = Path(__file__).parent.parent / "data" / "gentleman_set"
YT_FACTS_CACHE_PATH = GENTLEMAN_SET_DIR / "youtube_facts_cache.json"

# 14 категорий таксономии
CATEGORIES = [
    "История", "Литература", "Наука и технологии", "География",
    "Искусство", "Музыка", "Кино и театр", "Спорт",
    "Язык и лингвистика", "Религия и мифология", "Общество и политика",
    "Быт и повседневность", "Природа и животные", "Логика и wordplay",
]

# --- Промпты ---

EXTRACT_PROMPT = """Ты помогаешь команде ЧГК готовиться к турниру.
Из фрагмента транскрипции телешоу "Своя игра" извлеки интересные факты.

ПРАВИЛА:
1. Извлекай ТОЛЬКО конкретные факты: даты, числа, имена, этимологию, необычные связи, рекорды.
2. Каждый факт — САМОДОСТАТОЧНОЕ предложение. Понятен без контекста видео.
3. Вопрос + ответ ведущего → переформулируй как утверждение-факт.
4. НЕ извлекай: ход игры, счёт, ставки, реплики ведущего, представления игроков, рекламу.
5. Минимум 5, максимум 20 фактов на фрагмент.

ФОРМАТ — строго нумерованный список, каждый факт на отдельной строке:
1. Факт первый.
2. Факт второй.
3. Факт третий."""

CATEGORIZE_PROMPT = """Ты эксперт по ЧГК (Что? Где? Когда?). Для каждого факта определи:
1. КАТЕГОРИЮ (одна из 14):
   1-История, 2-Литература, 3-Наука и технологии, 4-География, 5-Искусство,
   6-Музыка, 7-Кино и театр, 8-Спорт, 9-Язык и лингвистика,
   10-Религия и мифология, 11-Общество и политика, 12-Быт и повседневность,
   13-Природа и животные, 14-Логика и wordplay

2. ЗАЦЕПКУ для ЧГК — короткая фраза (5-15 слов): почему этот факт может встретиться в вопросе,
   какая необычная связь или подвох возможны.

3. Если факт содержит ОШИБКУ распознавания речи — исправь текст факта.
4. Если факт бессмысленный/неполный/о ходе игры — пометь категорией 0.

Ответь СТРОГО в формате JSON (массив объектов):
[
  {{"n": 1, "cat": 3, "hook": "связь физики и быта", "fix": null}},
  {{"n": 2, "cat": 1, "hook": "малоизвестный факт о войне", "fix": "Исправленный текст"}},
  {{"n": 3, "cat": 0, "hook": null, "fix": null}}
]

Факты:
{facts}"""


# --- Кэш ---

def load_cache() -> dict:
    if YT_FACTS_CACHE_PATH.exists():
        return json.loads(YT_FACTS_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict):
    YT_FACTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    YT_FACTS_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- Парсинг ---

def _parse_facts(raw: str) -> List[str]:
    """Парсинг фактов из ответа LLM — нумерованный список, JSON, markdown."""
    if not raw or raw.strip().upper() in ("НЕТ", "НЕТ.", "NO", "NONE"):
        return []

    stripped = raw.strip()

    # JSON массив или объект
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            strings = _extract_strings_from_json(parsed)
            if strings:
                return strings
        except (json.JSONDecodeError, ValueError):
            pass

    # Нумерованный список
    facts = []
    for line in stripped.split("\n"):
        line = line.strip()
        m = re.match(r"^\d+[.)]\s*(.+)", line)
        if m:
            fact = m.group(1).strip().strip('"')
            if len(fact) > 20:
                facts.append(fact)
            continue
        m = re.match(r"^[-•*]\s+(.+)", line)
        if m:
            fact = m.group(1).strip()
            if len(fact) > 20:
                facts.append(fact)

    return facts


def _extract_strings_from_json(obj) -> List[str]:
    """Рекурсивно извлечь строки > 20 символов из JSON."""
    results = []
    if isinstance(obj, str):
        s = re.sub(r"^\d+[.)]\s*", "", obj.strip())
        if len(s) > 20:
            results.append(s)
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_extract_strings_from_json(item))
    elif isinstance(obj, dict):
        for v in obj.values():
            results.extend(_extract_strings_from_json(v))
    return results


def _deduplicate_facts(facts: list, threshold: float = 0.6) -> list:
    """Дедупликация фактов через лемматизацию (Jaccard similarity).

    Принимает список строк или список dict с ключом 'text'.
    """
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


# --- LLM ---

def _create_provider(provider_name: str, model: Optional[str] = None):
    from classifier.providers import create_provider
    return create_provider(provider_name, model=model)


def extract_facts_from_chunks(
    chunks: List[Dict],
    provider_name: str = "openrouter",
    model: Optional[str] = None,
) -> List[str]:
    """Извлечь факты из чанков транскрипта. Возвращает плоский список строк."""
    from scraper.youtube_transcriber import format_time

    provider = _create_provider(provider_name, model=model)
    all_facts = []

    for i, chunk in enumerate(chunks):
        time_range = f"{format_time(chunk['start_time'])}-{format_time(chunk['end_time'])}"
        print(f"  Чанк {i + 1}/{len(chunks)} [{time_range}]... ", end="", flush=True)

        messages = [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": chunk["text"]},
        ]

        raw = provider.chat(messages, max_tokens=2000, json_mode=False)
        if not raw:
            print("пусто")
            continue

        facts = _parse_facts(raw)
        print(f"{len(facts)} фактов")
        all_facts.extend(facts)

    return all_facts


def categorize_facts(
    facts: List[str],
    provider_name: str = "openrouter",
    model: Optional[str] = None,
) -> List[Dict]:
    """Категоризировать факты + добавить ЧГК-зацепки. Возвращает enriched facts."""
    if not facts:
        return []

    provider = _create_provider(provider_name, model=model)
    enriched = []
    batch_size = 30

    for start in range(0, len(facts), batch_size):
        batch = facts[start:start + batch_size]
        batch_num = start // batch_size + 1
        total_batches = (len(facts) + batch_size - 1) // batch_size
        print(f"  Батч {batch_num}/{total_batches} ({len(batch)} фактов)... ", end="", flush=True)

        facts_text = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(batch))
        prompt = CATEGORIZE_PROMPT.replace("{facts}", facts_text)

        messages = [
            {"role": "system", "content": "Ты эксперт по ЧГК. Ответь ТОЛЬКО валидным JSON."},
            {"role": "user", "content": prompt},
        ]

        raw = provider.chat(messages, max_tokens=3000, json_mode=True)
        if not raw:
            # Fallback — сохранить без категорий
            for f in batch:
                enriched.append({"text": f, "category": "Разное", "hook": ""})
            print("fallback")
            continue

        try:
            # Найти JSON массив в ответе
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            if m:
                ratings = json.loads(m.group())
            else:
                ratings = json.loads(raw)

            ok_count = 0
            for r in ratings:
                idx = r.get("n", 0) - 1
                cat_num = r.get("cat", 0)
                if cat_num == 0 or idx < 0 or idx >= len(batch):
                    continue  # мусорный факт — пропускаем
                cat_name = CATEGORIES[cat_num - 1] if 1 <= cat_num <= 14 else "Разное"
                text = r.get("fix") or batch[idx]
                hook = r.get("hook", "")
                enriched.append({"text": text, "category": cat_name, "hook": hook})
                ok_count += 1
            print(f"{ok_count} категоризировано")

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            # Fallback
            for f in batch:
                enriched.append({"text": f, "category": "Разное", "hook": ""})
            print(f"parse error, fallback")

    return enriched


# --- Вывод ---

def save_markdown(video_id: str, title: str, url: str, facts: List[Dict]):
    """Сохранить факты в структурированный markdown по категориям."""
    FACTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = FACTS_DIR / f"{video_id}.md"

    # Группировка по категориям
    by_category = defaultdict(list)
    for f in facts:
        by_category[f["category"]].append(f)

    # Сортировка категорий по количеству фактов (больше → выше)
    sorted_cats = sorted(by_category.items(), key=lambda x: -len(x[1]))

    lines = [
        f"# {title}",
        "",
        f"**Источник:** {url}",
        f"**Дата обработки:** {datetime.now().strftime('%Y-%m-%d')}",
        f"**Фактов:** {len(facts)} | **Категорий:** {len(by_category)}",
        "",
    ]

    # Оглавление
    lines.append("## Содержание")
    lines.append("")
    for cat, cat_facts in sorted_cats:
        anchor = cat.lower().replace(" ", "-").replace("и-", "и-")
        lines.append(f"- [{cat}](#{anchor}) ({len(cat_facts)})")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Факты по категориям
    for cat, cat_facts in sorted_cats:
        lines.append(f"## {cat}")
        lines.append("")
        for f in cat_facts:
            hook = f" *→ {f['hook']}*" if f.get("hook") else ""
            lines.append(f"- {f['text']}{hook}")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[MD] Сохранено: {md_path}")
    return md_path


# --- Пайплайн ---

def run_pipeline(
    url: str,
    provider_name: str = "openrouter",
    model: Optional[str] = None,
    force: bool = False,
) -> Optional[Dict]:
    """Полный пайплайн: скачать → транскрибировать → извлечь → категоризировать → markdown."""
    from scraper.youtube_transcriber import (
        download_audio,
        transcribe,
        chunk_transcript,
        _extract_video_id,
    )

    cache = load_cache()
    video_id = _extract_video_id(url)

    # Проверяем кэш
    if not force and video_id in cache:
        cached = cache[video_id]
        n = len(cached.get("facts", []))
        if n > 0:
            print(f"[YT] Уже в кэше: {n} фактов. Используйте --force для пересчёта.")
            return cached

    # 1. Транскрипт (кэш или скачать+Whisper)
    transcript_path = Path(__file__).parent.parent / "data" / "youtube" / "transcripts" / f"{video_id}.json"
    if transcript_path.exists():
        print(f"\n[Whisper] Транскрипт уже есть: {transcript_path.name}")
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        meta = {"video_id": video_id, "title": transcript.get("title", video_id)}
    else:
        print(f"\n[YT] Скачиваю аудио...")
        meta = download_audio(url)
        print(f"\n[Whisper] Транскрибирую...")
        transcript = transcribe(meta["audio_path"], meta)

    # 2. Чанки
    try:
        chunks = chunk_transcript(transcript["segments"])
    except Exception as e:
        print(f"\n[ERROR] Ошибка при разбиении на чанки: {e}")
        import traceback; traceback.print_exc()
        return None
    print(f"\n[1/3] Извлекаю факты из {len(chunks)} чанков ({model or 'default'})...")

    # 3. Извлечь сырые факты
    raw_facts = extract_facts_from_chunks(chunks, provider_name, model)
    print(f"\n[1/3] Извлечено: {len(raw_facts)} фактов")

    # 4. Дедупликация
    unique_facts = _deduplicate_facts(raw_facts)
    print(f"[2/3] После дедупликации: {len(unique_facts)}")

    # 5. Категоризация + ЧГК-зацепки + очистка мусора
    print(f"\n[3/3] Категоризирую и добавляю ЧГК-контекст...")
    enriched = categorize_facts(unique_facts, provider_name, model)

    # Ещё раз дедупликация (после исправлений)
    enriched = _deduplicate_facts(enriched)
    print(f"[3/3] Итого: {len(enriched)} фактов в {len(set(f['category'] for f in enriched))} категориях")

    # 6. Markdown
    md_path = save_markdown(video_id, meta["title"], url, enriched)

    # 7. Кэш
    result = {
        "title": meta["title"],
        "url": url,
        "channel": meta.get("channel", ""),
        "duration_seconds": meta.get("duration", 0),
        "facts": enriched,
        "extracted_at": datetime.now().isoformat(),
        "provider": provider_name,
        "model": model,
        "total_facts_raw": len(raw_facts),
        "total_facts_dedup": len(enriched),
        "markdown_path": str(md_path),
    }

    cache[video_id] = result
    save_cache(cache)
    print(f"[YT] Готово: {len(enriched)} фактов для '{meta['title']}'")
    return result


# --- Telegram ---

# Хештеги для разных источников
SOURCE_HASHTAGS = {
    "youtube": "#СвояИгра",
    "wiki": "#Википедия",
}

# Эмодзи для категорий в ТГ
CATEGORY_EMOJI = {
    "История": "\U0001F3DB",               # classical building
    "Литература": "\U0001F4DA",            # books
    "Наука и технологии": "\U0001F52C",    # microscope
    "География": "\U0001F30D",             # globe
    "Искусство": "\U0001F3A8",             # palette
    "Музыка": "\U0001F3B5",                # music note
    "Кино и театр": "\U0001F3AC",          # clapper
    "Спорт": "\u26BD",                     # football
    "Язык и лингвистика": "\U0001F4AC",    # speech balloon
    "Религия и мифология": "\U0001F54C",   # mosque
    "Общество и политика": "\U0001F4F0",   # newspaper
    "Быт и повседневность": "\u2615",      # hot beverage
    "Природа и животные": "\U0001F33F",    # herb
    "Логика и wordplay": "\U0001F9E9",     # puzzle piece
}


def _plural_facts(n: int) -> str:
    """Русское склонение: факт/факта/фактов."""
    if 11 <= n % 100 <= 19:
        return "ов"
    mod10 = n % 10
    if mod10 == 1:
        return ""
    if 2 <= mod10 <= 4:
        return "а"
    return "ов"


def post_facts_to_telegram(
    facts: List[Dict],
    source_tag: str = "#СвояИгра",
    source_url: str = "",
    categories: Optional[List[str]] = None,
    dry_run: bool = False,
) -> int:
    """Отправить факты в ТГ-группу с топиками по категориям.

    Args:
        facts: Список фактов [{text, category, hook}, ...]
        source_tag: Хештег источника (#СвояИгра, #Википедия)
        source_url: Ссылка на источник
        categories: Фильтр категорий (None = все)
        dry_run: Не отправлять, только показать

    Returns:
        Количество отправленных сообщений.
    """
    from scripts.tg_bot_digest import DigestBot, _escape_html, _ensure_topics_table
    from config import TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID
    from database.db import get_connection
    from config import DB_PATH

    if not TG_DIGEST_BOT_TOKEN or not TG_DIGEST_CHAT_ID:
        print("[TG] Ошибка: TG_DIGEST_BOT_TOKEN / TG_DIGEST_CHAT_ID не заданы в .env")
        return 0

    # Фильтруем по категориям
    if categories:
        filtered = [f for f in facts if f.get("category") in categories]
    else:
        filtered = facts

    if not filtered:
        print("[TG] Нет фактов для отправки")
        return 0

    # Группируем по категориям
    by_cat = defaultdict(list)
    for f in filtered:
        by_cat[f.get("category", "Разное")].append(f)

    bot = DigestBot(TG_DIGEST_BOT_TOKEN, TG_DIGEST_CHAT_ID)
    sent = 0

    for cat, cat_facts in sorted(by_cat.items()):
        if dry_run:
            print(f"  [{cat}] {len(cat_facts)} фактов (dry run)")
            for f in cat_facts:
                print(f"    • {f['text']}")
            continue

        topic_id = bot.get_or_create_topic(cat)

        emoji = CATEGORY_EMOJI.get(cat, "\U0001F4CC")  # pushpin fallback

        # Формируем сообщение с красивым форматированием
        header = f"{emoji} <b>{cat}</b>  {source_tag}"
        lines = [header, ""]
        for i, f in enumerate(cat_facts, 1):
            fact_text = _escape_html(f["text"])
            hook = f.get("hook", "")
            if hook:
                lines.append(f"\u25AA\uFE0F {fact_text}")
                lines.append(f"    <i>\U0001F517 {_escape_html(hook)}</i>")
            else:
                lines.append(f"\u25AA\uFE0F {fact_text}")
            lines.append("")  # пустая строка между фактами

        # Футер
        lines.append("\u2014" * 15)
        count_str = f"{len(cat_facts)} факт" + _plural_facts(len(cat_facts))
        footer_parts = [f"\U0001F4CA {count_str}"]
        if source_url:
            footer_parts.append(f'<a href="{source_url}">\U0001F3AC Источник</a>')
        lines.append(" | ".join(footer_parts))

        message = "\n".join(lines)

        # Telegram лимит 4096, разбиваем если нужно
        messages_to_send = []
        if len(message) > 4000:
            chunk_lines = [header, ""]
            part = 1
            for i, f in enumerate(cat_facts, 1):
                fact_text = _escape_html(f["text"])
                hook = f.get("hook", "")
                new_lines = [f"\u25AA\uFE0F {fact_text}"]
                if hook:
                    new_lines.append(f"    <i>\U0001F517 {_escape_html(hook)}</i>")
                new_lines.append("")
                test = "\n".join(chunk_lines + new_lines)
                if len(test) > 3800:
                    messages_to_send.append("\n".join(chunk_lines))
                    part += 1
                    chunk_lines = [f"{emoji} <b>{cat}</b> (ч.{part})  {source_tag}", ""]
                chunk_lines.extend(new_lines)
            # Остаток + футер
            chunk_lines.append("\u2014" * 15)
            count_str = f"{len(cat_facts)} факт" + _plural_facts(len(cat_facts))
            footer_parts = [f"\U0001F4CA {count_str}"]
            if source_url:
                footer_parts.append(f'<a href="{source_url}">\U0001F3AC Источник</a>')
            chunk_lines.append(" | ".join(footer_parts))
            messages_to_send.append("\n".join(chunk_lines))
        else:
            messages_to_send.append(message)

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
                    time.sleep(1.5)  # 1.5с между сообщениями — безопасно для TG
                    break
                except RuntimeError as e:
                    err = str(e)
                    if "Too Many Requests" in err:
                        # Извлекаем retry_after из ошибки
                        import re as _re
                        m = _re.search(r"retry after (\d+)", err)
                        wait = int(m.group(1)) + 1 if m else 30
                        print(f"  [{cat}] Rate limit, жду {wait}с...")
                        time.sleep(wait)
                    else:
                        print(f"  [{cat}] Ошибка: {e}")
                        break

        print(f"  {emoji} [{cat}] {len(cat_facts)} фактов → отправлено")

        time.sleep(1)

    if dry_run:
        print(f"\n[TG] Dry run: {len(filtered)} фактов в {len(by_cat)} категориях")
    else:
        print(f"\n[TG] Отправлено: {sent} сообщений")

    return sent


# --- CLI ---

def show_cached():
    cache = load_cache()
    if not cache:
        print("Кэш пуст")
        return
    for vid, data in cache.items():
        dur = data.get("duration_seconds", 0) // 60
        n = len(data.get("facts", []))
        print(f"  {vid} | {data.get('title', '?')[:50]:50s} | {dur} мин | {n} фактов")


def show_facts(video_id: str):
    cache = load_cache()
    if video_id not in cache:
        print(f"Видео {video_id} не найдено в кэше")
        return
    data = cache[video_id]
    print(f"\n{data.get('title', '?')}")
    print(f"Фактов: {len(data.get('facts', []))}\n")

    by_cat = defaultdict(list)
    for f in data.get("facts", []):
        cat = f.get("category", "Разное") if isinstance(f, dict) else "Разное"
        by_cat[cat].append(f)

    for cat, cat_facts in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        print(f"\n  === {cat} ({len(cat_facts)}) ===")
        for f in cat_facts:
            text = f["text"] if isinstance(f, dict) else f
            hook = f.get("hook", "") if isinstance(f, dict) else ""
            hook_str = f" → {hook}" if hook else ""
            print(f"    • {text}{hook_str}")


def main():
    parser = argparse.ArgumentParser(description="YouTube → факты для ЧГК")
    parser.add_argument("--url", type=str, help="YouTube URL")
    parser.add_argument("--batch", type=str, help="Файл с URL (по одному на строку)")
    parser.add_argument("--list", action="store_true", help="Показать обработанные видео")
    parser.add_argument("--show", type=str, help="Показать факты для video_id")
    parser.add_argument("--provider", default="openrouter", help="LLM-провайдер")
    parser.add_argument("--model", default="openai/gpt-4o-mini",
                        help="Модель LLM (default: gpt-4o-mini)")
    parser.add_argument("--force", action="store_true",
                        help="Пересчитать даже если есть в кэше")
    parser.add_argument("--no-tg", action="store_true",
                        help="НЕ постить в ТГ после обработки (по умолчанию постит)")
    parser.add_argument("--post", type=str, metavar="VIDEO_ID",
                        help="Запостить факты из кэша в ТГ-группу")
    parser.add_argument("--post-categories", type=str, nargs="+",
                        help="Категории для постинга (по умолчанию все)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Показать что будет отправлено, не отправлять")

    args = parser.parse_args()

    if args.post:
        cache = load_cache()
        if args.post not in cache:
            print(f"Видео {args.post} не найдено. Используйте --list")
            return
        data = cache[args.post]
        if data.get("posted_to_tg") and not args.force:
            print(f"[TG] Уже отправлено ранее ({data['posted_to_tg']}). --force для повтора.")
            return
        facts = data.get("facts", [])
        url = data.get("url", "")
        print(f"[TG] Постинг: {data.get('title', '?')} ({len(facts)} фактов)")
        sent = post_facts_to_telegram(
            facts=facts,
            source_tag="#СвояИгра",
            source_url=url,
            categories=args.post_categories,
            dry_run=args.dry_run,
        )
        if sent > 0 and not args.dry_run:
            data["posted_to_tg"] = datetime.now().isoformat()
            save_cache(cache)
        return

    if args.list:
        show_cached()
        return

    if args.show:
        show_facts(args.show)
        return

    if args.url:
        from scraper.youtube_transcriber import _extract_video_id
        video_id = _extract_video_id(args.url)

        try:
            result = run_pipeline(
                url=args.url,
                provider_name=args.provider,
                model=args.model,
                force=args.force,
            )
        except Exception as e:
            print(f"\n[ERROR] Пайплайн упал: {e}")
            import traceback; traceback.print_exc()
            return

        if result:
            print(f"\n{'='*60}")
            print(f"ИТОГО: {len(result['facts'])} фактов")
            print(f"Markdown: {result.get('markdown_path', '?')}")
            print(f"{'='*60}")

            # Автоматически постить в ТГ (если не --no-tg)
            if not args.no_tg:
                cache = load_cache()
                cached = cache.get(video_id, {})
                if cached.get("posted_to_tg") and not args.force:
                    print(f"\n[TG] Уже отправлено ранее ({cached['posted_to_tg']})")
                else:
                    print(f"\n[TG] Постинг в Telegram...")
                    try:
                        sent = post_facts_to_telegram(
                            facts=result["facts"],
                            source_tag="#СвояИгра",
                            source_url=result.get("url", args.url),
                            dry_run=args.dry_run,
                        )
                        if sent > 0 and not args.dry_run and video_id in cache:
                            cache[video_id]["posted_to_tg"] = datetime.now().isoformat()
                            save_cache(cache)
                    except Exception as e:
                        print(f"\n[ERROR] Постинг в ТГ упал: {e}")
                        import traceback; traceback.print_exc()
        return

    if args.batch:
        from scraper.youtube_transcriber import _extract_video_id
        batch_path = Path(args.batch)
        if not batch_path.exists():
            print(f"Файл не найден: {args.batch}")
            return
        urls = [
            line.strip()
            for line in batch_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        print(f"Обработка {len(urls)} видео...\n")
        for url in urls:
            try:
                video_id = _extract_video_id(url)
                result = run_pipeline(url=url, provider_name=args.provider, model=args.model)
                # Автопостинг в ТГ
                if result and not args.no_tg:
                    cache = load_cache()
                    cached = cache.get(video_id, {})
                    if not cached.get("posted_to_tg"):
                        print(f"\n[TG] Постинг в Telegram...")
                        sent = post_facts_to_telegram(
                            facts=result["facts"],
                            source_tag="#СвояИгра",
                            source_url=url,
                        )
                        if sent > 0 and video_id in cache:
                            cache[video_id]["posted_to_tg"] = datetime.now().isoformat()
                            save_cache(cache)
            except Exception as e:
                print(f"[ERROR] {url}: {e}")
        return

    parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
