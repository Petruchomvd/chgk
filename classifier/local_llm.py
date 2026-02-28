"""Интеграция с Ollama и Groq для классификации ЧГК-вопросов.

DEPRECATED: Этот модуль сохранён для обратной совместимости со скриптами
benchmark.py, compare_models.py и т.д. Новый код использует
classifier.providers и classifier.classifier.
"""

import json
import time
from typing import Any, Dict, List, Optional

import ollama

from classifier.prompts import (
    SYSTEM_PROMPT,
    build_few_shot_messages,
    build_stage1_messages,
    build_stage2_messages,
    build_user_message,
)
from config import CLASSIFICATION_TEMPERATURE, GROQ_RATE_LIMIT_DELAY, OLLAMA_MODEL
from database.seed_taxonomy import TAXONOMY

_RETRY_DELAYS = [2, 5, 15]  # секунды между попытками


def _ollama_chat_with_retry(model: str, messages: list, **kwargs) -> Optional[dict]:
    """Вызов ollama.chat() с 3 попытками при transient-ошибках."""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return ollama.chat(model=model, messages=messages, **kwargs)
        except Exception as e:
            if attempt < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[attempt]
                print(f"Ollama error (attempt {attempt+1}): {e}. Retry in {delay}s...")
                time.sleep(delay)
            else:
                print(f"Ollama error (all retries exhausted): {e}")
                return None
    return None


def classify_question(
    text: str,
    answer: str,
    comment: str = "",
    model: str = OLLAMA_MODEL,
    few_shot: bool = True,
) -> Optional[List[Dict[str, Any]]]:
    """Классифицировать один вопрос через Ollama.

    Возвращает список тем: [{"cat": N, "sub": M, "conf": 0.X}, ...]
    или None при ошибке.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if few_shot:
        messages.extend(build_few_shot_messages())

    messages.append({
        "role": "user",
        "content": build_user_message(text, answer, comment),
    })

    response = _ollama_chat_with_retry(
        model=model,
        messages=messages,
        format="json",
        options={"temperature": CLASSIFICATION_TEMPERATURE},
    )
    if response is None:
        return None

    raw = response["message"]["content"].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"Invalid JSON from LLM: {raw[:200]}")
        return None

    topics = data.get("topics")
    if not isinstance(topics, list):
        print(f"Missing 'topics' key in response: {raw[:200]}")
        return None

    # Валидация
    valid = []
    for t in topics[:2]:
        cat = t.get("cat")
        sub = t.get("sub")
        conf = t.get("conf", 0.5)
        if isinstance(cat, int) and isinstance(sub, int) and 1 <= cat <= 14:
            valid.append({"cat": cat, "sub": sub, "conf": round(float(conf), 2)})

    return valid if valid else None


def classify_batch(
    questions: List[Dict[str, Any]],
    model: str = OLLAMA_MODEL,
    few_shot: bool = True,
) -> List[Dict[str, Any]]:
    """Классифицировать список вопросов.

    Каждый вопрос — dict с ключами: id, text, answer, comment (опционально).
    Возвращает список результатов: [{id, topics: [...]}, ...]
    """
    results = []
    for q in questions:
        topics = classify_question(
            text=q["text"],
            answer=q["answer"],
            comment=q.get("comment", ""),
            model=model,
            few_shot=few_shot,
        )
        results.append({
            "id": q["id"],
            "topics": topics or [],
        })
    return results


def classify_question_twostage(
    text: str,
    answer: str,
    comment: str = "",
    model: str = OLLAMA_MODEL,
) -> Optional[List[Dict[str, Any]]]:
    """Двухэтапная классификация: сначала категория, потом подкатегория.

    Этап 1: выбор из 14 категорий (простая задача).
    Этап 2: для каждой категории выбор подкатегории из 3-5 вариантов.
    """
    # --- Этап 1: категория ---
    messages1 = build_stage1_messages(text, answer, comment)
    resp1 = _ollama_chat_with_retry(
        model=model,
        messages=messages1,
        format="json",
        options={"temperature": CLASSIFICATION_TEMPERATURE},
    )
    if resp1 is None:
        return None

    raw1 = resp1["message"]["content"].strip()
    try:
        data1 = json.loads(raw1)
    except json.JSONDecodeError:
        print(f"Invalid JSON stage1: {raw1[:200]}")
        return None

    cats = data1.get("cats", [])
    if isinstance(cats, int):
        cats = [cats]
    if not isinstance(cats, list) or not cats:
        print(f"No cats in stage1: {raw1[:200]}")
        return None

    # Ограничить до 2 категорий, валидировать
    cats = [c for c in cats[:2] if isinstance(c, int) and 1 <= c <= 14]
    if not cats:
        return None

    # --- Этап 2: подкатегория для каждой категории ---
    results = []
    for cat_num in cats:
        max_sub = len(TAXONOMY[cat_num - 1][2])
        messages2 = build_stage2_messages(cat_num, text, answer, comment)
        resp2 = _ollama_chat_with_retry(
            model=model,
            messages=messages2,
            format="json",
            options={"temperature": CLASSIFICATION_TEMPERATURE},
        )
        if resp2 is None:
            results.append({"cat": cat_num, "sub": 1, "conf": 0.5})
            continue

        raw2 = resp2["message"]["content"].strip()
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


def check_model_available(model: str = OLLAMA_MODEL) -> bool:
    """Проверить, доступна ли модель в Ollama."""
    try:
        models = ollama.list()
        available = [m.model for m in models.models]
        for name in available:
            if model.split(":")[0] in name:
                return True
        return False
    except Exception as e:
        print(f"Ollama connection error: {e}")
        return False


# ─── Groq API ────────────────────────────────────────────────────────

def _get_groq_client():
    """Создать Groq-клиент (ленивый импорт)."""
    from groq import Groq
    from config import GROQ_API_KEY
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY не задан в .env или переменных окружения")
    return Groq(api_key=GROQ_API_KEY)


_groq_client = None


def _groq_chat(messages: list, model: str, max_tokens: int = 100) -> Optional[str]:
    """Отправить запрос в Groq API, вернуть content или None."""
    global _groq_client
    if _groq_client is None:
        _groq_client = _get_groq_client()
    try:
        resp = _groq_client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=CLASSIFICATION_TEMPERATURE,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"Groq error: {e}")
        return None


def classify_question_groq(
    text: str,
    answer: str,
    comment: str = "",
    model: str = "llama-3.3-70b-versatile",
    few_shot: bool = True,
) -> Optional[List[Dict[str, Any]]]:
    """Одноэтапная классификация через Groq API."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if few_shot:
        messages.extend(build_few_shot_messages())
    messages.append({"role": "user", "content": build_user_message(text, answer, comment)})

    raw = _groq_chat(messages, model)
    if raw is None:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"Invalid JSON from Groq: {raw[:200]}")
        return None

    topics = data.get("topics")
    if not isinstance(topics, list):
        return None

    valid = []
    for t in topics[:2]:
        cat = t.get("cat")
        sub = t.get("sub")
        conf = t.get("conf", 0.5)
        if isinstance(cat, int) and isinstance(sub, int) and 1 <= cat <= 14:
            valid.append({"cat": cat, "sub": sub, "conf": round(float(conf), 2)})

    return valid if valid else None


def classify_question_twostage_groq(
    text: str,
    answer: str,
    comment: str = "",
    model: str = "llama-3.3-70b-versatile",
) -> Optional[List[Dict[str, Any]]]:
    """Двухэтапная классификация через Groq API."""
    # --- Этап 1: категория ---
    messages1 = build_stage1_messages(text, answer, comment)
    raw1 = _groq_chat(messages1, model, max_tokens=50)
    if raw1 is None:
        return None

    try:
        data1 = json.loads(raw1)
    except json.JSONDecodeError:
        print(f"Invalid JSON stage1 (Groq): {raw1[:200]}")
        return None

    cats = data1.get("cats", [])
    if isinstance(cats, int):
        cats = [cats]
    if not isinstance(cats, list) or not cats:
        return None

    cats = [c for c in cats[:2] if isinstance(c, int) and 1 <= c <= 14]
    if not cats:
        return None

    # Пауза для rate limit между stage1 и stage2
    time.sleep(GROQ_RATE_LIMIT_DELAY)

    # --- Этап 2: подкатегория ---
    results = []
    for i, cat_num in enumerate(cats):
        max_sub = len(TAXONOMY[cat_num - 1][2])
        messages2 = build_stage2_messages(cat_num, text, answer, comment)
        raw2 = _groq_chat(messages2, model, max_tokens=50)

        if raw2 is None:
            results.append({"cat": cat_num, "sub": 1, "conf": 0.5})
            continue

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

        # Пауза между запросами stage2 (если > 1 категории)
        if i < len(cats) - 1:
            time.sleep(GROQ_RATE_LIMIT_DELAY)

    return results if results else None
