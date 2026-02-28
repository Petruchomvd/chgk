"""Единая функция классификации ЧГК-вопросов через любого LLM-провайдера.

Заменяет дублированную логику из local_llm.py (4 отдельные функции)
одной универсальной функцией, работающей с любым провайдером.
"""

import json
from typing import Any, Dict, List, Optional

from classifier.prompts import (
    SYSTEM_PROMPT,
    build_few_shot_messages,
    build_stage1_messages,
    build_stage2_messages,
    build_user_message,
)
from classifier.providers.base import BaseLLMProvider
from database.seed_taxonomy import TAXONOMY


def classify_question(
    provider: BaseLLMProvider,
    text: str,
    answer: str,
    comment: str = "",
    twostage: bool = True,
    few_shot: bool = True,
) -> Optional[List[Dict[str, Any]]]:
    """Классифицировать один вопрос через любого провайдера.

    Args:
        provider: LLM-провайдер (Ollama, OpenAI, Anthropic, Google, Groq)
        text: Текст вопроса
        answer: Ответ
        comment: Комментарий (опционально)
        twostage: Двухэтапная классификация (по умолчанию True)
        few_shot: Использовать few-shot примеры

    Returns:
        Список тем: [{"cat": N, "sub": M, "conf": 0.X}, ...] или None
    """
    if twostage:
        return _classify_twostage(provider, text, answer, comment)
    else:
        return _classify_onestage(provider, text, answer, comment, few_shot)


def _classify_onestage(
    provider: BaseLLMProvider,
    text: str,
    answer: str,
    comment: str,
    few_shot: bool,
) -> Optional[List[Dict[str, Any]]]:
    """Одноэтапная классификация: все 52 подкатегории сразу."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if few_shot:
        messages.extend(build_few_shot_messages())
    messages.append({"role": "user", "content": build_user_message(text, answer, comment)})

    raw = provider.chat(messages)
    if raw is None:
        return None

    return _parse_onestage_response(raw)


def _classify_twostage(
    provider: BaseLLMProvider,
    text: str,
    answer: str,
    comment: str,
) -> Optional[List[Dict[str, Any]]]:
    """Двухэтапная классификация: категория → подкатегория."""
    # --- Этап 1: выбор категории из 14 ---
    messages1 = build_stage1_messages(text, answer, comment)
    raw1 = provider.chat(messages1, max_tokens=50)
    if raw1 is None:
        return None

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

    cats = [c for c in cats[:2] if isinstance(c, int) and 1 <= c <= 14]
    if not cats:
        return None

    # --- Этап 2: подкатегория для каждой категории ---
    results = []
    for cat_num in cats:
        max_sub = len(TAXONOMY[cat_num - 1][2])
        messages2 = build_stage2_messages(cat_num, text, answer, comment)
        raw2 = provider.chat(messages2, max_tokens=50)

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

    return results if results else None


def _parse_onestage_response(raw: str) -> Optional[List[Dict[str, Any]]]:
    """Парсинг ответа одноэтапной классификации."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"Invalid JSON from LLM: {raw[:200]}")
        return None

    topics = data.get("topics")
    if not isinstance(topics, list):
        print(f"Missing 'topics' key in response: {raw[:200]}")
        return None

    valid = []
    for t in topics[:2]:
        cat = t.get("cat")
        sub = t.get("sub")
        conf = t.get("conf", 0.5)
        if isinstance(cat, int) and isinstance(sub, int) and 1 <= cat <= 14:
            valid.append({"cat": cat, "sub": sub, "conf": round(float(conf), 2)})

    return valid if valid else None
