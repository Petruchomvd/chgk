"""Бенчмарк: прогон 50 вопросов через Groq API (Llama 3.3 70B)."""

import sys
import os
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from groq import Groq

from config import DB_PATH, CLASSIFICATION_TEMPERATURE
from database.db import get_connection
from database.seed_taxonomy import TAXONOMY
from classifier.prompts import (
    STAGE1_SYSTEM_PROMPT,
    STAGE1_FEW_SHOT,
    build_stage2_prompt,
    build_user_message,
)
from test_benchmark_ids import BENCHMARK_IDS


GROQ_MODEL = "llama-3.3-70b-versatile"


def build_stage1_messages(text: str, answer: str, comment: str = "") -> list:
    """Сообщения для этапа 1 (определение категории)."""
    messages = [{"role": "system", "content": STAGE1_SYSTEM_PROMPT}]
    for ex in STAGE1_FEW_SHOT:
        messages.append({"role": "user", "content": f"Вопрос: {ex['q']}\nОтвет: {ex['a']}"})
        messages.append({"role": "assistant", "content": json.dumps(ex["out"])})
    messages.append({"role": "user", "content": build_user_message(text, answer, comment)})
    return messages


def build_stage2_messages(cat_num: int, text: str, answer: str, comment: str = "") -> list:
    """Сообщения для этапа 2 (определение подкатегории)."""
    messages = [{"role": "system", "content": build_stage2_prompt(cat_num)}]
    messages.append({"role": "user", "content": build_user_message(text, answer, comment)})
    return messages


def classify_question_groq(
    client: Groq,
    text: str,
    answer: str,
    comment: str = "",
) -> list | None:
    """Двухэтапная классификация одного вопроса через Groq."""

    # --- Этап 1: категория ---
    messages1 = build_stage1_messages(text, answer, comment)
    try:
        resp1 = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages1,
            response_format={"type": "json_object"},
            temperature=CLASSIFICATION_TEMPERATURE,
            max_tokens=50,
        )
    except Exception as e:
        print(f"  Groq error (stage1): {e}")
        return None

    raw1 = resp1.choices[0].message.content.strip()
    try:
        data1 = json.loads(raw1)
    except json.JSONDecodeError:
        print(f"  Invalid JSON stage1: {raw1[:200]}")
        return None

    cats = data1.get("cats", [])
    if isinstance(cats, int):
        cats = [cats]
    if not isinstance(cats, list) or not cats:
        print(f"  No cats in stage1: {raw1[:200]}")
        return None

    cats = [c for c in cats[:2] if isinstance(c, int) and 1 <= c <= 14]
    if not cats:
        return None

    # --- Этап 2: подкатегория ---
    results = []
    for cat_num in cats:
        max_sub = len(TAXONOMY[cat_num - 1][2])
        messages2 = build_stage2_messages(cat_num, text, answer, comment)
        try:
            resp2 = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages2,
                response_format={"type": "json_object"},
                temperature=CLASSIFICATION_TEMPERATURE,
                max_tokens=50,
            )
        except Exception as e:
            print(f"  Groq error (stage2, cat={cat_num}): {e}")
            results.append({"cat": cat_num, "sub": 1, "conf": 0.5})
            continue

        raw2 = resp2.choices[0].message.content.strip()
        try:
            data2 = json.loads(raw2)
        except json.JSONDecodeError:
            results.append({"cat": cat_num, "sub": 1, "conf": 0.5})
            continue

        sub = data2.get("sub", 1)
        conf = data2.get("conf", 0.5)
        if not isinstance(sub, int) or sub < 1 or sub > max_sub:
            sub = 1
        results.append({"cat": cat_num, "sub": sub, "conf": round(float(conf), 2)})

    return results if results else None


def run_benchmark(api_key: str):
    client = Groq(api_key=api_key)
    conn = get_connection(DB_PATH)

    # Загрузить вопросы
    placeholders = ",".join("?" * len(BENCHMARK_IDS))
    rows = conn.execute(
        f"SELECT id, text, answer, comment FROM questions WHERE id IN ({placeholders})",
        BENCHMARK_IDS,
    ).fetchall()
    questions = {r["id"]: dict(r) for r in rows}

    print(f"Модель: {GROQ_MODEL} (через Groq)")
    print(f"Вопросов: {len(BENCHMARK_IDS)}")
    print(f"{'=' * 70}")

    results = []
    start = time.time()
    errors = 0

    for i, qid in enumerate(BENCHMARK_IDS, 1):
        q = questions[qid]
        topics = classify_question_groq(
            client=client,
            text=q["text"],
            answer=q["answer"],
            comment=q["comment"] or "",
        )

        if topics is None:
            errors += 1

        # Резолвим названия
        topic_strs = []
        if topics:
            for t in topics:
                row = conn.execute(
                    """SELECT c.name_ru, s.name_ru FROM subcategories s
                       JOIN categories c ON s.category_id = c.id
                       WHERE c.sort_order = ? AND s.sort_order = ?""",
                    (t["cat"], t["sub"]),
                ).fetchone()
                if row:
                    topic_strs.append(f"{row[0]}>{row[1]}({t['conf']})")

        ans = (q["answer"] or "")[:40]
        print(f"{i:2}. [{ans}] => {' | '.join(topic_strs)}")
        results.append({"id": qid, "answer": ans, "topics": topics or [], "labels": topic_strs})

        # Rate limiting: ~30 req/min free tier → пауза между вопросами
        # Каждый вопрос = 2-3 запроса → пауза 3с чтобы не превысить лимит
        if i < len(BENCHMARK_IDS):
            time.sleep(3)

    elapsed = time.time() - start
    print(f"\n{'=' * 70}")
    print(f"Время: {elapsed / 60:.1f} мин ({elapsed / len(BENCHMARK_IDS):.1f} с/вопрос)")
    if errors:
        print(f"Ошибки: {errors}")

    # Сохранить результаты
    out_path = Path(__file__).parent.parent / "output" / "benchmark_groq_llama70b.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"model": GROQ_MODEL, "elapsed_sec": elapsed, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"Результаты: {out_path}")

    conn.close()


if __name__ == "__main__":
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        # Попробуем из аргумента
        import argparse
        parser = argparse.ArgumentParser(description="Бенчмарк через Groq (Llama 3.3 70B)")
        parser.add_argument("--key", type=str, help="Groq API key")
        args = parser.parse_args()
        api_key = args.key

    if not api_key:
        print("Укажи API-ключ: --key gsk_... или GROQ_API_KEY=...")
        sys.exit(1)

    run_benchmark(api_key)
