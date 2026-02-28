"""Анализ ответов ЧГК для «Джентльменского набора».

Извлекает именованные сущности (NER) и частотные ключевые слова
из ответов вопросов, сохраняет результаты в JSON.

Использование:
    python scripts/analyze_answers.py
    python scripts/analyze_answers.py --limit 1000
"""

import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, PROJECT_ROOT

# Стоп-слова (на основе Analytics/top_words.py + дополнения)
ALL_STOPWORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как",
    "а", "то", "все", "она", "так", "его", "но", "из", "у", "к", "до",
    "за", "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня",
    "еще", "нет", "о", "же", "быть", "был", "них", "сейчас", "при",
    "ли", "сам", "себя", "свои", "мой", "эта", "этот", "эти", "тогда",
    "когда", "где", "есть", "будет", "для", "всех", "вы", "они",
    "мы", "тот", "там", "ты", "ни", "даже", "если", "ему", "вам",
    "их", "это", "или", "один", "два", "три", "под", "имя",
    "кто", "чем", "чего", "того", "которые", "который", "которой",
    "которая", "которое", "которую", "которым", "которых", "которого",
    "ответ", "вопрос", "назовите", "напишите", "ответьте", "слово",
    "слова", "словом", "словами", "можно", "была", "были", "стал",
    "свой", "свою", "своей", "своем", "себе", "него", "ней", "нее",
    "неё", "ним", "нему", "другой", "другая", "другое", "других",
    "каждый", "между", "против", "около", "вокруг", "вместо",
    "более", "очень", "уже", "без", "перед", "через", "после",
    "также", "однако", "например", "поэтому", "всего", "всё",
    "принимать", "зачёт", "зачет", "незачёт", "незачет",
    "первый", "второй", "является", "именно", "тоже",
}

OUTPUT_DIR = PROJECT_ROOT / "data" / "gentleman_set"

# Низкоинформативные ответы, которые не должны попадать в «джентльменский набор»
LOW_INFO_EXACT_ANSWERS = {
    "да", "нет", "не", "ничего", "никто", "всё", "все", "что", "кто",
    "он", "она", "они", "мы", "это", "то",
    "икс", "x", "игрек", "y", "зет", "z",
    "альфа", "бета", "гамма", "дельта",
}


def normalize_answer_key(text: str) -> str:
    """Нормализовать ответ в стабильный ключ для подсчёта."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return normalized


def is_numeric_like_answer(text: str) -> bool:
    """Проверить, похож ли ответ на число/дату/числовую константу."""
    text = text.strip().lower()
    if text in {"π", "пи", "pi"}:
        return True
    if re.fullmatch(r"\d{1,4}", text):
        return True
    if re.fullmatch(r"\d{1,4}([./:-]\d{1,4})+", text):
        return True
    if re.fullmatch(r"\d+[,.]\d+", text):
        return True
    return False


def low_info_reason(normalized: str) -> str | None:
    """Вернуть причину отбраковки низкоинформативного ответа."""
    if not normalized:
        return "empty"

    if re.fullmatch(r"[\W_]+", normalized):
        return "punctuation_only"

    if normalized in LOW_INFO_EXACT_ANSWERS:
        return "stop_answer"

    # Не отбрасываем короткие числовые ответы вроде «42»
    if is_numeric_like_answer(normalized):
        return None

    tokens = re.findall(r"[а-яёa-z0-9]+", normalized, flags=re.IGNORECASE)
    if not tokens:
        return "no_tokens"

    if len(tokens) == 1:
        token = tokens[0]
        if len(token) <= 1:
            return "too_short_single_token"
        if token in LOW_INFO_EXACT_ANSWERS:
            return "stop_single_token"

    return None


# ── Очистка ответов ──────────────────────────────────────────────

def clean_answer(text: str) -> str:
    """Очистить текст ответа для анализа."""
    # Убрать скобки [], оставив содержимое
    text = re.sub(r"\[([^\]]*)\]", r"\1", text)
    # Убрать ударения (combining acute accent)
    text = text.replace("\u0301", "")
    # Заменить неразрывные пробелы
    text = text.replace("\xa0", " ")
    # Убрать кавычки
    text = re.sub(r'[«»""„\u201c\u201d\u201e]', "", text)
    # Убрать точку в конце
    text = text.strip().rstrip(".")
    return text.strip()


def split_answer(text: str) -> list[str]:
    """Разбить дуплеты/триплеты на отдельные ответы.

    Форматы: "1) Пупин. 2) Лупин." или "Моисей, Красное море"
    """
    # Дуплет: "1) ... 2) ..."
    parts = re.split(r"\d+\)\s*", text)
    parts = [p.strip().rstrip(".").strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return parts

    # Через точку-пробел: "Пупин. Лупин"
    if ". " in text and not text[0].isdigit():
        dot_parts = [p.strip() for p in text.split(". ") if p.strip()]
        if len(dot_parts) > 1 and all(len(p) < 50 for p in dot_parts):
            return dot_parts

    return [text]


# ── Уровень 0: Частотность полных ответов ────────────────────────

def count_full_answers(
    answers: list[tuple[int, str]],
    top_n: int = 1000,
) -> dict:
    """Подсчитать частотность полных ответов.

    Нормализует регистр для подсчёта, но сохраняет оригинальное
    написание самого частого варианта (display form).
    """
    raw_answer_counter = Counter()
    filtered_answer_counter = Counter()
    filtered_answer_reason: dict[str, str] = {}
    answer_counter = Counter()
    answer_questions: dict[str, list[int]] = {}
    # normalized -> Counter(original_forms)
    original_forms: dict[str, Counter] = {}

    for qid, text in answers:
        normalized = normalize_answer_key(text)
        if not normalized:
            continue

        original = text.strip()
        original_forms.setdefault(normalized, Counter())[original] += 1
        raw_answer_counter[normalized] += 1

        reason = low_info_reason(normalized)
        if reason:
            filtered_answer_counter[normalized] += 1
            filtered_answer_reason[normalized] = reason
            continue

        answer_counter[normalized] += 1
        answer_questions.setdefault(normalized, []).append(qid)

    # Display form: самый частый вариант написания
    display_forms = {}
    for norm, forms in original_forms.items():
        display_forms[norm] = forms.most_common(1)[0][0]

    top = answer_counter.most_common(top_n)
    filtered_top = filtered_answer_counter.most_common(200)

    print(f"  Уникальных ответов (до фильтра): {len(raw_answer_counter)}")
    print(f"  Отфильтровано как низкоинформативные: {len(filtered_answer_counter)}")
    print(f"  Уникальных ответов (после фильтра): {len(answer_counter)}")
    print(f"  Топ-{top_n}: от {top[-1][1] if top else 0} до {top[0][1] if top else 0} упоминаний")

    return {
        "top_answers": top,
        "display_forms": {k: display_forms[k] for k, _ in top},
        "answer_questions": {
            k: v for k, v in answer_questions.items() if len(v) >= 2
        },
        "filtered_out": [
            [display_forms.get(k, k), cnt, filtered_answer_reason.get(k, "filtered")]
            for k, cnt in filtered_top
        ],
        "stats": {
            "unique_raw_answers": len(raw_answer_counter),
            "unique_filtered_answers": len(filtered_answer_counter),
            "unique_kept_answers": len(answer_counter),
            "filtered_mentions": int(sum(filtered_answer_counter.values())),
        },
    }


# ── Уровень 1: NER (natasha) ────────────────────────────────────

def extract_entities(answers: list[tuple[int, str]]) -> dict:
    """Извлечь именованные сущности из ответов через natasha."""
    from natasha import (
        Doc,
        MorphVocab,
        NewsEmbedding,
        NewsNERTagger,
        Segmenter,
    )

    print("  Загрузка NER-модели...", flush=True)
    segmenter = Segmenter()
    morph_vocab = MorphVocab()
    emb = NewsEmbedding()
    ner_tagger = NewsNERTagger(emb)

    per_counter = Counter()  # люди
    loc_counter = Counter()  # места
    org_counter = Counter()  # организации

    # entity -> [question_ids]
    entity_questions: dict[str, list[int]] = {}

    total = len(answers)
    for idx, (qid, text) in enumerate(answers):
        if idx % 5000 == 0:
            print(f"  NER: {idx}/{total}...", flush=True)

        doc = Doc(text)
        doc.segment(segmenter)
        doc.tag_ner(ner_tagger)

        for span in doc.spans:
            span.normalize(morph_vocab)
            normal = span.normal or span.text
            normal = normal.strip()
            if not normal or len(normal) < 2:
                continue

            if span.type == "PER":
                per_counter[normal] += 1
            elif span.type == "LOC":
                loc_counter[normal] += 1
            elif span.type == "ORG":
                org_counter[normal] += 1

            entity_questions.setdefault(normal, []).append(qid)

    print(f"  NER завершен: {len(per_counter)} людей, "
          f"{len(loc_counter)} мест, {len(org_counter)} организаций")

    return {
        "PER": per_counter.most_common(300),
        "LOC": loc_counter.most_common(300),
        "ORG": org_counter.most_common(300),
        "entity_questions": {
            k: v for k, v in entity_questions.items() if len(v) >= 2
        },
    }


# ── Уровень 2: Леммы + биграммы ─────────────────────────────────

def extract_keywords(answers: list[tuple[int, str]]) -> dict:
    """Лемматизация ответов, частотность слов и биграмм."""
    import pymorphy3

    print("  Загрузка морфоанализатора...", flush=True)
    morph = pymorphy3.MorphAnalyzer()

    lemma_counter = Counter()
    bigram_counter = Counter()
    keyword_questions: dict[str, list[int]] = {}

    total = len(answers)
    for idx, (qid, text) in enumerate(answers):
        if idx % 5000 == 0:
            print(f"  Леммы: {idx}/{total}...", flush=True)

        words = re.findall(r"[а-яёА-ЯЁa-zA-Z]{3,}", text.lower())
        lemmas = []
        for w in words:
            parsed = morph.parse(w)[0]
            lemma = parsed.normal_form
            if lemma in ALL_STOPWORDS or len(lemma) < 3:
                continue
            lemmas.append(lemma)
            lemma_counter[lemma] += 1
            keyword_questions.setdefault(lemma, []).append(qid)

        for i in range(len(lemmas) - 1):
            bg = f"{lemmas[i]} {lemmas[i+1]}"
            bigram_counter[bg] += 1

    print(f"  Леммы завершены: {len(lemma_counter)} уникальных слов, "
          f"{len(bigram_counter)} биграмм")

    return {
        "lemmas": lemma_counter.most_common(500),
        "bigrams": bigram_counter.most_common(300),
        "keyword_questions": {
            k: v for k, v in keyword_questions.items() if len(v) >= 3
        },
    }


# ── Main ─────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Анализ ответов ЧГК для «Джентльменского набора»",
    )
    parser.add_argument("--limit", type=int, default=None, help="Ограничить кол-во вопросов")
    parser.add_argument("--top-n", type=int, default=1000, help="Топ-N полных ответов (по умолчанию 1000)")
    args = parser.parse_args()

    # Загрузка ответов из БД
    conn = sqlite3.connect(str(DB_PATH))

    sql = "SELECT id, answer FROM questions WHERE answer IS NOT NULL AND answer != ''"
    if args.limit:
        sql += f" LIMIT {args.limit}"

    rows = conn.execute(sql).fetchall()
    conn.close()
    print(f"Загружено ответов: {len(rows)}")

    # Очистка: answer → список (qid, clean_text)
    answers = []
    for qid, answer in rows:
        for part in split_answer(clean_answer(answer)):
            if part:
                answers.append((qid, part))

    print(f"После очистки и разбиения: {len(answers)} фрагментов")

    # Уровень 0: Полные ответы
    print("\n=== Уровень 0: Частотность полных ответов ===")
    top_answers = count_full_answers(answers, top_n=args.top_n)

    # Уровень 1: NER
    print("\n=== Уровень 1: NER (natasha) ===")
    entities = extract_entities(answers)

    # Уровень 2: Леммы
    print("\n=== Уровень 2: Леммы + биграммы ===")
    keywords = extract_keywords(answers)

    # Сохранение
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    (OUTPUT_DIR / "top_answers.json").write_text(
        json.dumps(top_answers, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "entities.json").write_text(
        json.dumps(entities, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "keywords.json").write_text(
        json.dumps(keywords, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    meta = {
        "generated_at": datetime.now().isoformat(),
        "total_questions": len(rows),
        "total_fragments": len(answers),
        "unique_top_answers": len(top_answers["top_answers"]),
        "candidates_before_filter": top_answers.get("stats", {}).get("unique_raw_answers"),
        "candidates_after_filter": top_answers.get("stats", {}).get("unique_kept_answers"),
        "filtered_unique_answers": top_answers.get("stats", {}).get("unique_filtered_answers"),
        "filtered_mentions": top_answers.get("stats", {}).get("filtered_mentions"),
        "unique_persons": len(entities["PER"]),
        "unique_locations": len(entities["LOC"]),
        "unique_orgs": len(entities["ORG"]),
        "unique_keywords": len(keywords["lemmas"]),
        "unique_bigrams": len(keywords["bigrams"]),
    }
    (OUTPUT_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== Готово ===")
    print(f"Результаты: {OUTPUT_DIR}")
    print(f"  Топ ответов: {meta['unique_top_answers']}")
    print(f"  Людей: {meta['unique_persons']}")
    print(f"  Мест: {meta['unique_locations']}")
    print(f"  Организаций: {meta['unique_orgs']}")
    print(f"  Ключевых слов: {meta['unique_keywords']}")
    print(f"  Биграмм: {meta['unique_bigrams']}")

    # Превью
    print("\n--- Топ-20 ответов ---")
    for norm, cnt in top_answers["top_answers"][:20]:
        display = top_answers["display_forms"].get(norm, norm)
        print(f"  {display}: {cnt}")

    print("\n--- Топ-10 людей (NER) ---")
    for name, cnt in entities["PER"][:10]:
        print(f"  {name}: {cnt}")

    print("\n--- Топ-10 мест (NER) ---")
    for name, cnt in entities["LOC"][:10]:
        print(f"  {name}: {cnt}")


if __name__ == "__main__":
    main()
