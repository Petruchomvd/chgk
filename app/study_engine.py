"""Генератор обучающих статей по теме на основе вопросов из БД.

LLM получает вопросы из БД, упоминающие тему, и пишет статью 600-1000 слов,
заточенную под ЧГК-частотность (что именно про эту тему спрашивают).
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import List, Optional

from classifier.providers import create_provider
from config import PROJECT_ROOT

STUDIES_DIR = PROJECT_ROOT / "studies"
DEFAULT_MODEL = "openai/gpt-4o-mini"  # через OpenRouter
MAX_QUESTIONS_IN_PROMPT = 25
MAX_TOKENS = 2500


def _slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:80] or "topic"


def list_studies() -> List[dict]:
    """Список ранее сгенерированных статей."""
    if not STUDIES_DIR.exists():
        return []
    items = []
    for p in sorted(STUDIES_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        items.append({
            "slug": p.stem,
            "path": str(p),
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        })
    return items


def find_existing(topic: str) -> Optional[Path]:
    slug = _slugify(topic)
    p = STUDIES_DIR / f"{slug}.md"
    return p if p.exists() else None


def find_questions_about(
    chgk_conn: sqlite3.Connection, topic: str, limit: int = MAX_QUESTIONS_IN_PROMPT
) -> List[dict]:
    """Поиск вопросов, упоминающих тему в text/answer/comment."""
    pattern = f"%{topic}%"
    rows = chgk_conn.execute(
        """
        SELECT q.id, q.text, q.answer, q.comment, q.source, q.authors,
               p.title AS pack_title
        FROM questions q
        LEFT JOIN packs p ON q.pack_id = p.id
        WHERE q.text LIKE ? OR q.answer LIKE ? OR q.comment LIKE ?
        LIMIT ?
        """,
        (pattern, pattern, pattern, limit * 3),
    ).fetchall()
    # Префильтр: убрать ложные срабатывания подстроки (Магритт vs. имажинист и пр.)
    # упрощённо: убедиться, что слово topic встречается как отдельное слово.
    word_re = re.compile(rf"(?<![\wА-Яа-яёЁ]){re.escape(topic)}", re.IGNORECASE)
    filtered = []
    for r in rows:
        blob = " ".join(s or "" for s in (r["text"], r["answer"], r["comment"]))
        if word_re.search(blob):
            filtered.append(dict(r))
            if len(filtered) >= limit:
                break
    return filtered


def _build_prompt(topic: str, questions: List[dict]) -> List[dict]:
    blocks = []
    for i, q in enumerate(questions, 1):
        block = f"### Вопрос {i} (id {q['id']})\n"
        if q.get("pack_title"):
            block += f"Пак: {q['pack_title']}\n"
        block += f"Вопрос: {q['text']}\n"
        block += f"Ответ: {q['answer']}\n"
        if q.get("comment"):
            block += f"Комментарий: {q['comment']}\n"
        blocks.append(block)
    questions_text = "\n".join(blocks) if blocks else "(в базе не нашлось вопросов про эту тему)"

    system = (
        "Ты — эксперт по игре «Что? Где? Когда?» и помогаешь готовиться к турнирам. "
        "Ты получаешь тему и список реальных ЧГК-вопросов про неё из исторической базы. "
        "Твоя задача — написать обучающую статью 600-1000 слов на русском языке, "
        "заточенную именно под ЧГК-подготовку.\n\n"
        "Структура статьи:\n"
        "1. **Кратко о теме** (1 абзац) — что это, ключевые факты.\n"
        "2. **Что у этой темы спрашивают в ЧГК** (главная часть) — выводы из приведённых "
        "вопросов: какие аспекты темы повторяются, какие триггеры/ассоциации/каламбуры "
        "используются, на что часто ловят. Цитируй конкретные вопросы по id, когда уместно.\n"
        "3. **Связанные сущности** — кого/что часто упоминают рядом с темой в этих вопросах.\n"
        "4. **Что точно стоит запомнить** — bullet-список фактов и ассоциаций.\n\n"
        "Не пересказывай Википедию полностью. Фокусируйся на том, что реально пригодится "
        "за игровым столом. Если в вопросах есть смешные/неочевидные связки — обязательно "
        "упомяни. Используй markdown."
    )
    user = f"Тема: **{topic}**\n\nВопросы из базы:\n\n{questions_text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def generate_article(
    chgk_conn: sqlite3.Connection,
    topic: str,
    model: str = DEFAULT_MODEL,
    save: bool = True,
) -> dict:
    """Сгенерировать статью по теме.

    Возвращает {'topic', 'slug', 'path', 'content', 'questions_used', 'cost'}.
    """
    questions = find_questions_about(chgk_conn, topic)
    messages = _build_prompt(topic, questions)

    provider = create_provider("openrouter", model=model)
    content = provider.chat(messages, max_tokens=MAX_TOKENS, json_mode=False)
    if content is None:
        raise RuntimeError("Не удалось получить ответ от LLM")

    slug = _slugify(topic)
    STUDIES_DIR.mkdir(parents=True, exist_ok=True)
    path = STUDIES_DIR / f"{slug}.md"

    if save:
        header = f"# {topic}\n\n*Сгенерировано на основе {len(questions)} вопросов из базы. Модель: {model}*\n\n---\n\n"
        path.write_text(header + content, encoding="utf-8")

    return {
        "topic": topic,
        "slug": slug,
        "path": str(path) if save else None,
        "content": content,
        "questions_used": len(questions),
        "cost": provider.estimated_cost,
    }


def read_existing(topic_or_slug: str) -> Optional[str]:
    """Прочитать ранее сгенерированную статью."""
    slug = _slugify(topic_or_slug)
    p = STUDIES_DIR / f"{slug}.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None
