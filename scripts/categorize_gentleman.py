"""LLM-категоризация топ-ответов ЧГК для «Джентльменского набора».

Берёт `top_answers.json` из analyze_answers.py и раскладывает ответы
по 6 целевым категориям:
Люди, Места, Произведения, Наука, Выражения, Числа.

Использование:
    python scripts/categorize_gentleman.py
    python scripts/categorize_gentleman.py --provider openai --top 500
    python scripts/categorize_gentleman.py --provider groq --batch-size 30
    python scripts/categorize_gentleman.py --force
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: F401 - загрузит .env
from config import PROJECT_ROOT
from classifier.providers import create_provider, AVAILABLE_PROVIDERS

DATA_DIR = PROJECT_ROOT / "data" / "gentleman_set"

TARGET_CATEGORIES = {
    1: "Люди",
    2: "Места",
    3: "Произведения",
    4: "Наука",
    5: "Выражения",
    6: "Числа",
}
TARGET_CATEGORY_NAMES = set(TARGET_CATEGORIES.values())
ENTITY_PER_HINTS: set[str] = set()
ENTITY_LOC_HINTS: set[str] = set()
NUMBER_WORD_ALLOWLIST = {"пи", "pi", "π", "ноль", "нуль"}
SCIENCE_MARKERS = {
    "принцип", "закон", "теорема", "эффект", "гипотеза", "парадокс",
    "тест", "формула", "уравнение", "распределение",
    "гравитац", "квант", "атом", "днк", "ген", "молекул",
    "чёрная дыра", "черная дыра", "болезнь", "синдром",
    "лента мёбиуса", "лента мебиуса", "кот шрёдингера", "кот шредингера",
    "сечение", "взрыв", "потеплени", "альцгеймер", "затмение", "тесей",
    "эволюци", "мутаци", "рентген", "бритва", "фибоначчи", "относительност",
    "доплер", "бабочк",
}
WORK_SINGLE_WORD_WHITELIST = {
    "шахматы", "тетрис", "гамлет", "дюна", "щелкунчик", "герника",
    "джоконда", "джокер", "колобок", "хоббит", "пигмалион", "оскар",
    "крик", "касабланка", "золушка", "буратино",
    "репка", "макбет", "отелло", "монополия", "челюсти", "вий",
    "аватар", "титаник", "ревизор", "фауст", "одиссея", "илиада",
    "библия", "домино", "покер", "бумеранг", "матрёшка", "матрешка",
    "пиноккио", "русалочка", "дюймовочка", "белоснежка",
    "левиафан", "франкенштейн", "носферату", "годзилла",
}
WORK_DENYLIST = {
    "кубик рубика",
}
PERSON_DENY_MARKERS = {
    "палач", "нимб", "занавес", "движение", "рулетка", "фейерверк",
    "башня", "стена", "мост", "пирамида", "площадь", "метро",
}
PLACE_KEYWORDS = {
    "город", "страна", "море", "океан", "остров", "гора", "река",
    "башня", "стена", "мост", "пирамида", "вокзал", "метро",
    "млечный путь", "планета", "луна", "марс", "земля", "путь",
}
PLACE_DENY_MARKERS = {
    "фейерверк", "нимб", "палач", "рулетка", "движение", "кубик",
    "тетрис", "шахматы",
}
KNOWN_PLACES = {
    "австралия", "япония", "ватикан", "плутон", "марс", "сатурн",
    "европа", "троя", "пиза", "израиль", "красное море", "чили",
    "панама", "мадагаскар", "атлантида", "эльдорадо", "колизей",
    "эверест", "килиманджаро", "байкал", "бермудский треугольник",
    "стоунхендж", "венеция", "рим", "иерусалим", "вавилон",
    "спарта", "карфаген", "помпеи", "чернобыль",
}
EXPRESSION_ALLOWLIST = {
    "железный занавес",
    "деньги не пахнут",
    "сухой закон",
    "предложение от которого невозможно отказаться",
    "поехали",
    "аб ово",
}
EXPRESSION_MARKERS = {
    " не ", " ни ", " если ", " когда ", " пусть ",
}

# --- SWAG: Denylist ложных срабатываний ---
# Ответы, которые LLM систематически присваивает неверным категориям.
# Они пропускаются при построении финального набора.
# Ответы, которые LLM систематически присваивает неверным категориям.
# Только бытовые предметы/явления, которые ТОЧНО не являются элементами gentleman set.
CATEGORY_DENYLIST = {
    "нимб", "палач", "дирижёр", "гробовщик",
    "коса", "муравейник", "фейерверк", "мир",
    "тень", "дождь", "зеркало", "гроб", "свеча", "мёд", "мед",
    "хвост", "маска", "перчатка", "колесо", "флаг", "меч",
}

# --- SWAG: Курированный whitelist ---
# Канонические ЧГК-ответы с детерминистической категорией.
# Проверяется ДО отправки в LLM. Ключ — нормализованный ответ, значение — номер категории.
CURATED_WHITELIST: dict[str, int] = {
    # Произведения (3)
    "ноев ковчег": 3, "машина времени": 3,
    "репка": 3, "красная шапочка": 3, "день сурка": 3,
    "бурлаки на волге": 3, "джоконда": 3, "монополия": 3,
    "король лир": 3, "макбет": 3, "отелло": 3, "вий": 3,
    "кинг-конг": 3, "челюсти": 3, "ледниковый период": 3,
    "мышеловка": 3, "аватар": 3, "титаник": 3,
    "война и мир": 3, "мастер и маргарита": 3, "ревизор": 3,
    "одиссея": 3, "илиада": 3, "библия": 3,
    "мона лиза": 3, "тайная вечеря": 3, "чёрный квадрат": 3,
    "черный квадрат": 3, "лебединое озеро": 3, "щелкунчик": 3,
    "пигмалион": 3, "фауст": 3, "дон кихот": 3,
    "алиса в стране чудес": 3, "маленький принц": 3,
    "451 градус по фаренгейту": 3, "1984": 3,
    "гамлет": 3, "ромео и джульетта": 3, "двенадцать стульев": 3,
    # Люди (1)
    "икар": 1, "венера": 1, "давид": 1, "чехов": 1,
    "барби": 1, "феникс": 1, "ленин": 1, "харон": 1,
    "сфинкс": 1, "русалка": 1, "фигаро": 1, "пеле": 1,
    "прометей": 1, "геракл": 1, "одиссей": 1, "ахиллес": 1,
    "робинзон крузо": 1, "дон жуан": 1, "мюнхгаузен": 1,
    "наполеон": 1, "цезарь": 1, "клеопатра": 1, "нерон": 1,
    "эдип": 1, "орфей": 1, "сизиф": 1, "нарцисс": 1,
    # Места (2)
    "австралия": 2, "япония": 2, "ватикан": 2,
    "плутон": 2, "марс": 2, "сатурн": 2, "европа": 2,
    "троя": 2, "пиза": 2, "израиль": 2, "красное море": 2,
    "чили": 2, "панама": 2, "мадагаскар": 2,
    "атлантида": 2, "эльдорадо": 2, "колизей": 2,
    "эверест": 2, "килиманджаро": 2, "байкал": 2,
    "бермудский треугольник": 2, "стоунхендж": 2,
    # Наука (4)
    "днк": 4, "золотое сечение": 4, "большой взрыв": 4,
    "глобальное потепление": 4, "болезнь альцгеймера": 4,
    "корабль тесея": 4, "рентген": 4, "солнечное затмение": 4,
    "кот шрёдингера": 4, "кот шредингера": 4,
    "лента мёбиуса": 4, "лента мебиуса": 4,
    "эффект доплера": 4, "эффект бабочки": 4,
    "бритва оккама": 4, "число фибоначчи": 4,
    "теория относительности": 4, "закон мерфи": 4,
    "чёрная дыра": 4, "черная дыра": 4,
    # Выражения (5)
    "железный занавес": 5, "сухой закон": 5,
    "камень ножницы бумага": 5, "медовый месяц": 5,
    "охота на ведьм": 5, "крестовый поход": 5,
    "золотая лихорадка": 5, "холодная война": 5,
    "белый слон": 5, "ящик пандоры": 5,
    "буриданов осёл": 5, "буриданов осел": 5,
    "дамоклов меч": 5, "ахиллесова пята": 5,
    "пиррова победа": 5, "гордиев узел": 5,
    "поехали": 5, "эврика": 5,
    "троянский конь": 5, "буря в стакане": 5, "вавилонская башня": 5,
    # Произведения — ранее в denylist, но могут быть полезны
    "чистилище": 3, "кубик рубика": 3,
    "лев": 1,
}

LEGACY_CATEGORY_ALIASES = {
    "Наука и техника": "Наука",
    "Выражения и фразы": "Выражения",
    "Числа и даты": "Числа",
}

FIRST_PASS_PROMPT = """Ты категоризируешь ответы на вопросы игры «Что? Где? Когда?».

Для каждого ответа определи одну категорию:
1. Люди — реальные и вымышленные персоны
2. Места — география, города, страны, достопримечательности, сооружения
3. Произведения — книги, фильмы, сериалы, картины, музыка, игры, пьесы
4. Наука — научные понятия, законы, эффекты, принципы, тесты, теоремы
5. Выражения — крылатые фразы, пословицы, цитаты, устойчивые выражения
6. Числа — числа, годы, даты, числовые константы

Правила:
- возвращай только ответы, в которых уверен;
- если ответ неоднозначен и уверенности нет, пропусти его;
- если это просто предмет/животное/бытовая вещь без чёткого маркера 6 категорий, пропусти;
- не придумывай новые ответы.

Верни ТОЛЬКО валидный JSON-объект без markdown.
Формат: {"ответ": номер_категории, ...}
"""

CONTEXT_PASS_PROMPT = """Категоризация ответов ЧГК с контекстом вопросов.

Для каждого ответа приведены 1-3 вопроса, где он встречался.
Используй контекст вопросов, чтобы точнее определить категорию.

Категории:
1. Люди — реальные и вымышленные персоны
2. Места — география, города, страны, достопримечательности
3. Произведения — книги, фильмы, картины, музыка, игры, пьесы
4. Наука — научные понятия, законы, эффекты, принципы
5. Выражения — крылатые фразы, пословицы, цитаты
6. Числа — числа, годы, даты, константы

Правила:
- если ответ — просто предмет/животное/бытовая вещь без связи с 6 категориями, пропусти;
- если контекст помогает понять, что ответ — произведение/выражение/научный термин, категоризируй;
- верни ТОЛЬКО валидный JSON: {"ответ": номер_категории, ...}
"""

SECOND_PASS_PROMPT = """Повторная категоризация ответов ЧГК.

Нужно выбрать категорию только из списка:
1. Люди
2. Места
3. Произведения
4. Наука
5. Выражения
6. Числа

Это второй проход по сомнительным ответам, поэтому правила строже:
- категоризируй только если есть явный маркер категории;
- если уверенности нет, пропусти ответ;
- не относить бытовые предметы, животных и общие слова к «Произведениям», «Науке» или «Выражениям» без чёткого основания.

Верни ТОЛЬКО JSON-объект формата {"ответ": номер_категории}.
"""


def normalize_text_key(text: str) -> str:
    """Привести текст к стабильному ключу для сопоставления."""
    return re.sub(r"\s+", " ", text.strip().lower())


def is_numeric_like_answer(text: str) -> bool:
    """Проверить, похож ли ответ на числовой формат."""
    text = normalize_text_key(text)
    if text in NUMBER_WORD_ALLOWLIST:
        return True
    if re.fullmatch(r"\d{1,4}", text):
        return True
    if re.fullmatch(r"\d{1,4}([./:-]\d{1,4})+", text):
        return True
    if re.fullmatch(r"\d+[,.]\d+", text):
        return True
    return False


def rule_based_category(answer: str) -> int | None:
    """Детерминированная категоризация простых кейсов."""
    if is_numeric_like_answer(answer):
        return 6
    return None


def _tokenize(answer: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9]+", normalize_text_key(answer), flags=re.IGNORECASE)


def _has_capitalized_component(answer: str) -> bool:
    for token in re.split(r"[\s\-]+", answer.strip()):
        if token and token[0].isalpha() and token[0].isupper():
            return True
    return False


def load_entity_hints() -> None:
    """Загрузить подсказки PER/LOC из entities.json для валидации."""
    global ENTITY_PER_HINTS, ENTITY_LOC_HINTS
    path = DATA_DIR / "entities.json"
    if not path.exists():
        ENTITY_PER_HINTS = set()
        ENTITY_LOC_HINTS = set()
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        ENTITY_PER_HINTS = set()
        ENTITY_LOC_HINTS = set()
        return

    ENTITY_PER_HINTS = {
        normalize_text_key(name) for name, _ in data.get("PER", []) if isinstance(name, str)
    }
    ENTITY_LOC_HINTS = {
        normalize_text_key(name) for name, _ in data.get("LOC", []) if isinstance(name, str)
    }


def is_valid_assignment(answer: str, category_num: int) -> bool:
    """Проверить, что присвоение категории выглядит правдоподобно."""
    normalized = normalize_text_key(answer)
    tokens = _tokenize(answer)

    # Denylist блокирует любую категорию
    if normalized in CATEGORY_DENYLIST:
        return False

    # Whitelist разрешает только соответствующую категорию
    wl_cat = CURATED_WHITELIST.get(normalized)
    if wl_cat is not None:
        return wl_cat == category_num

    if category_num == 1:  # Люди
        if any(marker in normalized for marker in PERSON_DENY_MARKERS):
            return False
        if normalized in ENTITY_LOC_HINTS:
            return False
        if normalized in ENTITY_PER_HINTS:
            return True
        # 2+ слова или заглавная буква в display-форме → принимаем
        return len(tokens) >= 2 or _has_capitalized_component(answer)

    if category_num == 2:  # Места
        if any(marker in normalized for marker in PLACE_DENY_MARKERS):
            return False
        if normalized in ENTITY_PER_HINTS:
            return False
        if normalized in ENTITY_LOC_HINTS:
            return True
        if normalized in KNOWN_PLACES:
            return True
        if any(keyword in normalized for keyword in PLACE_KEYWORDS):
            return True
        # Заглавная буква или 2+ слова → вероятно топоним
        return _has_capitalized_component(answer) or len(tokens) >= 2

    if category_num == 6:  # Числа
        return is_numeric_like_answer(normalized)

    if category_num == 5:  # Выражения
        if normalized in EXPRESSION_ALLOWLIST:
            return True
        if len(tokens) < 2:
            return False
        # SWAG: ослабили маркерное требование — 3+ словные ответы проходят
        # без маркера, если LLM уверен (т.к. длинные фразы чаще реальные выражения)
        if len(tokens) >= 3:
            return True
        padded = f" {normalized} "
        return any(marker in padded for marker in EXPRESSION_MARKERS)

    if category_num == 4:  # Наука
        # Маркер или 2+ слова → принимаем (LLM решил что наука)
        if any(marker in normalized for marker in SCIENCE_MARKERS):
            return True
        return len(tokens) >= 2

    if category_num == 3:  # Произведения
        if normalized in WORK_DENYLIST:
            return False
        return len(tokens) >= 2 or normalized in WORK_SINGLE_WORD_WHITELIST

    return True


def load_existing_categorization() -> dict:
    """Загрузить существующую категоризацию (для идемпотентности)."""
    path = DATA_DIR / "categorized_answers.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def normalize_existing_mapping(existing_mapping: dict) -> dict[str, str]:
    """Нормализовать старую категоризацию к новым 6 категориям."""
    normalized: dict[str, str] = {}
    for answer, category in existing_mapping.items():
        answer_key = normalize_text_key(str(answer))

        mapped_category = None
        if isinstance(category, int):
            mapped_category = TARGET_CATEGORIES.get(category)
        elif isinstance(category, str):
            category_clean = category.strip()
            mapped_category = LEGACY_CATEGORY_ALIASES.get(category_clean, category_clean)

        if mapped_category in TARGET_CATEGORY_NAMES:
            normalized[answer_key] = mapped_category
    return normalized


def save_categorization(result: dict) -> None:
    """Сохранить категоризацию в JSON."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "categorized_answers.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_llm_response(response: str, expected_answers: list[str]) -> dict[str, int]:
    """Извлечь JSON из ответа LLM и вернуть валидные категории 1..6."""
    if not response:
        return {}

    cleaned = re.sub(r"```(?:json)?\s*", "", response).strip()
    cleaned = cleaned.rstrip("`").strip()

    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                print("    [!] Не удалось распарсить JSON")
                return {}
        else:
            print("    [!] JSON не найден в ответе")
            return {}

    if not isinstance(data, dict):
        print("    [!] Ответ не является JSON-объектом")
        return {}

    expected = {normalize_text_key(a) for a in expected_answers}
    result: dict[str, int] = {}

    for answer, cat_num in data.items():
        answer_key = normalize_text_key(str(answer))
        if answer_key not in expected:
            continue

        if isinstance(cat_num, int):
            num = cat_num
        elif isinstance(cat_num, str) and cat_num.strip().isdigit():
            num = int(cat_num.strip())
        else:
            continue

        if 1 <= num <= 6:
            result[answer_key] = num

    return result


def load_question_contexts(answer_questions: dict[str, list[int]], max_per_answer: int = 3) -> dict[str, list[str]]:
    """Загрузить тексты вопросов из БД для контекстной категоризации."""
    import sqlite3
    from config import DB_PATH

    if not answer_questions:
        return {}

    conn = sqlite3.connect(str(DB_PATH))
    contexts: dict[str, list[str]] = {}

    for answer, qids in answer_questions.items():
        sample_ids = qids[:max_per_answer]
        if not sample_ids:
            continue
        placeholders = ",".join("?" * len(sample_ids))
        rows = conn.execute(
            f"SELECT text FROM questions WHERE id IN ({placeholders})",
            sample_ids,
        ).fetchall()
        texts = [row[0][:200] for row in rows if row[0]]  # обрезаем длинные
        if texts:
            contexts[answer] = texts

    conn.close()
    return contexts


def categorize_batch_with_context(
    provider,
    answers_batch: list[str],
    answer_contexts: dict[str, list[str]],
) -> dict[str, int]:
    """Отправить батч ответов на категоризацию с контекстом вопросов."""
    lines = []
    for i, answer in enumerate(answers_batch):
        lines.append(f"{i + 1}. {answer}")
        ctx = answer_contexts.get(normalize_text_key(answer), [])
        for q_text in ctx[:2]:
            lines.append(f'   - "{q_text}"')

    user_msg = f"Категоризируй эти ответы ЧГК (с контекстом вопросов):\n\n" + "\n".join(lines)

    messages = [
        {"role": "system", "content": CONTEXT_PASS_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    response = provider.chat(messages, max_tokens=2000)
    return parse_llm_response(response, answers_batch)


def categorize_batch(provider, answers_batch: list[str], prompt: str) -> dict[str, int]:
    """Отправить батч ответов на категоризацию."""
    numbered = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(answers_batch))
    user_msg = f"Категоризируй эти ответы ЧГК:\n\n{numbered}"

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_msg},
    ]

    response = provider.chat(messages, max_tokens=2000)
    return parse_llm_response(response, answers_batch)


def resolve_category_number(
    llm_result: dict[str, int],
    normalized_answer: str,
    display_answer: str,
) -> int | None:
    """Найти номер категории в ответе LLM по двум вариантам ключа."""
    for key in (normalize_text_key(display_answer), normalize_text_key(normalized_answer)):
        if key in llm_result:
            return llm_result[key]
    return None


def build_categorized_output(
    answer_category: dict[str, str],
    top_answers: list,
    display_forms: dict,
    uncategorized: list[list],
) -> dict:
    """Собрать итоговый JSON: только 6 категорий + список пропущенных."""
    categories = {name: [] for name in TARGET_CATEGORIES.values()}

    for norm_answer, count in top_answers:
        if normalize_text_key(norm_answer) in CATEGORY_DENYLIST:
            continue
        category_name = answer_category.get(norm_answer)
        if category_name in categories:
            categories[category_name].append([norm_answer, count])

    for category_name in categories:
        categories[category_name].sort(key=lambda item: item[1], reverse=True)

    return {
        "generated_at": datetime.now().isoformat(),
        "categories": categories,
        "answer_category": answer_category,
        "display_forms": display_forms,
        "total_categorized": len(answer_category),
        "uncategorized": uncategorized,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="LLM-категоризация топ-ответов ЧГК (6 категорий)",
    )
    parser.add_argument(
        "--provider",
        default="openai",
        help=f"LLM-провайдер ({', '.join(AVAILABLE_PROVIDERS)})",
    )
    parser.add_argument("--model", default=None, help="Модель")
    parser.add_argument("--api-key", default=None, help="API-ключ")
    parser.add_argument(
        "--top",
        type=int,
        default=1000,
        help="Количество топ-ответов для категоризации (по умолчанию 1000)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=40,
        help="Размер батча (по умолчанию 40)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перекатегоризировать даже уже обработанные ответы",
    )

    args = parser.parse_args()
    load_entity_hints()

    top_answers_path = DATA_DIR / "top_answers.json"
    if not top_answers_path.exists():
        print("Файл top_answers.json не найден. Сначала запустите:")
        print("  python scripts/analyze_answers.py")
        return

    top_data = json.loads(top_answers_path.read_text(encoding="utf-8"))
    all_answers = top_data["top_answers"][:args.top]
    display_forms = top_data.get("display_forms", {})

    print(f"Загружено ответов для категоризации: {len(all_answers)}")

    existing = load_existing_categorization()
    existing_mapping_raw = existing.get("answer_category", {}) if not args.force else {}
    existing_mapping = normalize_existing_mapping(existing_mapping_raw)

    # Привязываем старую категоризацию к текущему списку top_answers
    answer_category: dict[str, str] = {}
    for norm_answer, _ in all_answers:
        answer_key = normalize_text_key(norm_answer)
        existing_cat = existing_mapping.get(norm_answer) or existing_mapping.get(answer_key)
        if existing_cat in TARGET_CATEGORY_NAMES:
            answer_category[norm_answer] = existing_cat
    matched_existing_count = len(answer_category)

    to_categorize: list[tuple[str, int]] = []
    rule_based_count = 0
    whitelist_count = 0

    for norm_answer, count in all_answers:
        if norm_answer in answer_category:
            continue
        # Denylist — пропустить заведомо ложные
        if normalize_text_key(norm_answer) in CATEGORY_DENYLIST:
            continue

        normalized_key = normalize_text_key(norm_answer)
        display = display_forms.get(norm_answer, norm_answer)

        # SWAG whitelist — детерминистическая категория для канонических ответов
        wl_cat = CURATED_WHITELIST.get(normalized_key)
        if wl_cat is None:
            wl_cat = CURATED_WHITELIST.get(normalize_text_key(display))
        if wl_cat:
            answer_category[norm_answer] = TARGET_CATEGORIES[wl_cat]
            whitelist_count += 1
            continue

        # Rule-based (числа)
        rule_cat_num = rule_based_category(display) or rule_based_category(norm_answer)
        if rule_cat_num:
            answer_category[norm_answer] = TARGET_CATEGORIES[rule_cat_num]
            rule_based_count += 1
        else:
            to_categorize.append((norm_answer, count))

    print(f"Уже категоризировано (валидные 6 категорий): {matched_existing_count}")
    print(f"SWAG whitelist: {whitelist_count}")
    print(f"Категоризировано правилами (без LLM): {rule_based_count}")
    print(f"Осталось для LLM: {len(to_categorize)}")

    uncategorized: list[list] = []
    llm_categorized = 0
    model_name = existing.get("model", "unknown")

    # SWAG: загрузить контексты вопросов для 3-го прохода
    answer_questions = top_data.get("answer_questions", {})
    answer_contexts = load_question_contexts(answer_questions) if answer_questions else {}
    if answer_contexts:
        print(f"Загружено контекстов вопросов: {len(answer_contexts)}")

    if to_categorize:
        provider = create_provider(args.provider, model=args.model, api_key=args.api_key)
        model_name = provider.config.model
        print(f"Провайдер: {provider.config.name}, модель: {provider.config.model}")

        n_batches = (len(to_categorize) + args.batch_size - 1) // args.batch_size

        if provider.config.cost_per_1m_input > 0:
            est = provider.estimate_total_cost(
                n_batches, avg_input_tokens=800, avg_output_tokens=400
            )
            print(f"Примерная стоимость: ${est:.4f} ({n_batches} батчей)")

        for batch_idx in range(n_batches):
            start = batch_idx * args.batch_size
            end = min(start + args.batch_size, len(to_categorize))
            batch = to_categorize[start:end]
            batch_count_map = {norm: cnt for norm, cnt in batch}

            batch_answers = [display_forms.get(norm, norm) for norm, _ in batch]

            print(
                f"\n  Батч {batch_idx + 1}/{n_batches} ({len(batch)} ответов)...",
                end=" ",
                flush=True,
            )

            first_pass = categorize_batch(provider, batch_answers, FIRST_PASS_PROMPT)

            assigned_first = 0
            unresolved: list[tuple[str, str]] = []
            for norm_answer, _ in batch:
                display = display_forms.get(norm_answer, norm_answer)
                cat_num = resolve_category_number(first_pass, norm_answer, display)
                if cat_num and is_valid_assignment(display, cat_num):
                    answer_category[norm_answer] = TARGET_CATEGORIES[cat_num]
                    assigned_first += 1
                else:
                    unresolved.append((norm_answer, display))

            assigned_second = 0
            assigned_context = 0
            if unresolved:
                second_input = [display for _, display in unresolved]
                second_pass = categorize_batch(provider, second_input, SECOND_PASS_PROMPT)

                unresolved_after_second: list[tuple[str, str]] = []
                for norm_answer, display in unresolved:
                    cat_num = resolve_category_number(second_pass, norm_answer, display)
                    if cat_num and is_valid_assignment(display, cat_num):
                        answer_category[norm_answer] = TARGET_CATEGORIES[cat_num]
                        assigned_second += 1
                    else:
                        unresolved_after_second.append((norm_answer, display))

                # SWAG: 3-й проход — контекстная категоризация с текстами вопросов
                if unresolved_after_second and answer_contexts:
                    context_input = [display for _, display in unresolved_after_second]
                    context_pass = categorize_batch_with_context(
                        provider, context_input, answer_contexts
                    )

                    still_unresolved: list[str] = []
                    for norm_answer, display in unresolved_after_second:
                        cat_num = resolve_category_number(context_pass, norm_answer, display)
                        if cat_num and is_valid_assignment(display, cat_num):
                            answer_category[norm_answer] = TARGET_CATEGORIES[cat_num]
                            assigned_context += 1
                        else:
                            still_unresolved.append(norm_answer)

                    for norm_answer in still_unresolved:
                        uncategorized.append([norm_answer, batch_count_map[norm_answer]])
                else:
                    for norm_answer, _ in unresolved_after_second:
                        uncategorized.append([norm_answer, batch_count_map[norm_answer]])

            llm_categorized += assigned_first + assigned_second + assigned_context
            skipped = len(unresolved) - assigned_second - assigned_context
            print(f"✓ 1-й: {assigned_first}, 2-й: {assigned_second}, контекст: {assigned_context}, пропущено: {skipped}")

            interim = build_categorized_output(
                answer_category, all_answers, display_forms, uncategorized
            )
            interim["model"] = provider.config.model
            interim["meta"] = {
                "source_candidates": len(all_answers),
                "already_categorized": matched_existing_count,
                "whitelist_categorized": whitelist_count,
                "rule_based_categorized": rule_based_count,
                "llm_categorized": llm_categorized,
                "uncategorized_count": len(uncategorized),
            }
            save_categorization(interim)

    final = build_categorized_output(answer_category, all_answers, display_forms, uncategorized)
    final["model"] = model_name
    final["meta"] = {
        "source_candidates": len(all_answers),
        "already_categorized": matched_existing_count,
        "whitelist_categorized": whitelist_count,
        "rule_based_categorized": rule_based_count,
        "llm_categorized": llm_categorized,
        "uncategorized_count": len(uncategorized),
    }
    save_categorization(final)

    print("\n=== Готово ===")
    print(f"Всего категоризировано: {len(answer_category)}")
    print(f"Пропущено (вне 6 категорий/неуверенные): {len(uncategorized)}")

    print("\nКатегории:")
    for category_name, items in final["categories"].items():
        if items:
            print(f"  {category_name}: {len(items)}")
            for norm, cnt in items[:3]:
                display = display_forms.get(norm, norm)
                print(f"    {display}: {cnt}")


if __name__ == "__main__":
    main()
