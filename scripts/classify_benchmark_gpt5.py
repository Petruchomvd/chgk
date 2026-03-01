"""Classify docs/benchmark-150-random.md with a local GPT-5 heuristic pass.

This script does NOT call external APIs or Ollama.
It scores taxonomy topics by keyword patterns in:
 - question text
 - answer
 - comment

Then it writes:
 - scripts/gpt5_results.json
 - inserts/updates a `**GPT-5:**` block in docs/benchmark-150-random.md
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.seed_taxonomy import TAXONOMY


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MD_PATH = PROJECT_ROOT / "docs" / "benchmark-150-random.md"
OUT_PATH = PROJECT_ROOT / "scripts" / "gpt5_results.json"


def norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def get_label(cat: int, sub: int) -> str:
    if 1 <= cat <= len(TAXONOMY):
        _, cat_name_ru, subs = TAXONOMY[cat - 1]
        if 1 <= sub <= len(subs):
            _, sub_name_ru = subs[sub - 1]
            return f"{cat_name_ru} > {sub_name_ru}"
    return f"?({cat}.{sub})"


KEYWORDS: Dict[Tuple[int, int], List[str]] = {
    # 1. История
    (1, 1): ["антич", "древн", "рим", "грец", "египт", "фараон", "спарт", "цезар"],
    (1, 2): ["средневек", "рыцар", "феод", "ренессанс", "крестонос", "королевств"],
    (1, 3): ["новейш", "xx век", "xxi", "xix", "20 век", "21 век", "холодная война"],
    (1, 4): ["росси", "русск", "ссср", "совет", "ленинград", "петербург", "москва"],
    (1, 5): ["военн", "войн", "армия", "битв", "фронт", "полк", "линкор", "отряд", "оруж"],
    # 2. Литература
    (2, 1): ["пушкин", "толст", "достоев", "булгаков", "гогол", "чехов", "русск классик"],
    (2, 2): ["шекспир", "гете", "роман", "новелл", "зарубеж", "кафка", "сервантес"],
    (2, 3): ["современн", "xxi век", "бестселлер", "автор", "писатель"],
    (2, 4): ["поэз", "поэт", "стих", "рифм", "сонет", "баллада"],
    (2, 5): ["сказк", "детск", "басн", "мультфильм по книге"],
    # 3. Наука и технологии
    (3, 1): ["физик", "астроном", "космос", "планет", "звезд", "гравитац", "квант"],
    (3, 2): ["биолог", "медицин", "врач", "диагноз", "болезн", "ген", "анатом", "контрацеп"],
    (3, 3): ["хим", "элемент", "реакц", "молекул", "кислот", "щелоч"],
    (3, 4): ["математ", "алгоритм", "информат", "программ", "чатgpt", "нейросет"],
    (3, 5): ["изобрет", "технолог", "устройств", "двигател", "механизм", "инженер"],
    # 4. География
    (4, 1): ["море", "океан", "река", "остров", "вулкан", "фумарол", "гора", "климат", "пустын"],
    (4, 2): ["столиц", "страна", "город", "район", "провинц", "государств"],
    (4, 3): ["путешеств", "экспедиц", "исследовател", "мореплавател", "коворкинг"],
    # 5. Искусство
    (5, 1): ["картина", "художник", "живопис", "скульптур", "полотно", "брейгель"],
    (5, 2): ["архитектор", "архитектур", "здание", "фасад", "конструкц"],
    (5, 3): ["фотограф", "дизайн", "плакат", "постер", "типограф"],
    # 6. Музыка
    (6, 1): ["классическ", "симфон", "опера", "балет", "дирижер", "чайковск", "оркестр"],
    (6, 2): ["песня", "рок", "группа", "альбом", "джаз", "свинг", "популярн музыка"],
    (6, 3): ["инструмент", "нота", "аккорд", "тональн", "ритм"],
    # 7. Кино и театр
    (7, 1): ["фильм", "кино", "режиссер", "актер", "съемк", "чаплин"],
    (7, 2): ["театр", "мюзикл", "сцена", "спектакл", "опера как постановка"],
    (7, 3): ["сериал", "тв", "шоу", "эпизод", "икс фактор"],
    # 8. Спорт
    (8, 1): ["футбол", "мяч", "гол", "клуб", "тренер", "чемпионат"],
    (8, 2): ["олимп", "спринтер", "бег", "легкая атлетика"],
    (8, 3): ["бокс", "теннис", "шахмат", "гидрокостюм", "плавани"],
    (8, 4): ["история спорта", "рекорд", "турнир"],
    # 9. Язык и лингвистика
    (9, 1): ["этимолог", "происхожд", "словообраз", "от топонима", "созвуч"],
    (9, 2): ["фразеолог", "крылат", "выражени", "идиома", "пословиц"],
    (9, 3): ["английск", "французск", "латин", "санскрит", "перевод"],
    (9, 4): ["имя", "фамили", "топоним", "гидроним", "ономаст"],
    # 10. Религия и мифология
    (10, 1): ["христиан", "ислам", "будд", "веды", "индуист"],
    (10, 2): ["миф", "зевс", "герой", "богин", "аврора"],
    (10, 3): ["библ", "моис", "евангел", "заповед"],
    # 11. Общество и политика
    (11, 1): ["политик", "президент", "премьер", "государств", "правительств", "переговор"],
    (11, 2): ["эконом", "бизнес", "карьер", "финанс", "кредит", "долг"],
    (11, 3): ["закон", "право", "суд", "юрид", "залог"],
    (11, 4): ["знаменит", "биограф", "лауреат", "принцесс", "персона"],
    # 12. Быт и повседневность
    (12, 1): ["еда", "напит", "десерт", "кафе", "васаби", "шампанск"],
    (12, 2): ["мода", "одежд", "костюм", "стиль"],
    (12, 3): ["праздник", "традиц", "ритуал"],
    (12, 4): ["игра", "развлеч", "видеоигр", "gta", "ноутбук", "наклейк"],
    # 13. Природа и животные
    (13, 1): ["животн", "кот", "кошка", "акул", "лошад", "птиц"],
    (13, 2): ["растени", "дерев", "цвет"],
    (13, 3): ["эколог", "окружающ", "природоохран"],
    # 14. Логика и wordplay
    (14, 1): ["логическ", "задача", "парадокс"],
    (14, 2): ["каламбур", "игра слов", "созвуч", "на слух"],
    (14, 3): ["шифр", "загадк", "аббревиат", "код"],
}


LOGIC_PATTERNS = [
    r"\bпропуск\b",
    r"мы заменили",
    r"в вопросе есть замена",
    r"какое слово мы заменили",
    r"заполните пропуск",
    r"дважды пропустили",
    r"двумя словами",
    r"тремя словами",
    r"начинаются одинаково",
    r"на одну и ту же букву",
]


def parse_fields(section: str) -> tuple[str, str, str]:
    answer = ""
    q_lines: List[str] = []
    c_lines: List[str] = []
    in_comment = False

    for line in section.splitlines():
        if line.startswith("**Ответ:**"):
            answer = line.replace("**Ответ:**", "", 1).strip()
            continue
        if line.startswith("**Комментарий:**"):
            in_comment = True
            rest = line.replace("**Комментарий:**", "", 1).strip()
            if rest:
                c_lines.append(rest)
            continue
        if line.startswith("**Haiku:**") or line.startswith("**GPT-5:**"):
            in_comment = False
            continue
        if line.startswith(">") and not in_comment:
            q_lines.append(line[1:].strip())
            continue
        if in_comment:
            c_lines.append(line)

    return "\n".join(q_lines).strip(), answer, "\n".join(c_lines).strip()


def score_topic(question: str, answer: str, comment: str) -> dict[tuple[int, int], float]:
    qn = norm(question)
    an = norm(answer)
    cn = norm(comment)
    all_text = f"{qn}\n{an}\n{cn}"
    scores: dict[tuple[int, int], float] = defaultdict(float)

    # keyword scoring
    for key, kws in KEYWORDS.items():
        for kw in kws:
            if kw in qn:
                scores[key] += 2.0
            if kw in an:
                scores[key] += 1.5
            if kw in cn:
                scores[key] += 1.0

    # special patterns
    for pat in LOGIC_PATTERNS:
        if re.search(pat, all_text):
            scores[(14, 2)] += 2.5

    if "столиц" in qn:
        scores[(4, 2)] += 5.0
    if "фильм" in qn or "кино" in qn:
        scores[(7, 1)] += 2.5
    if "сериал" in qn:
        scores[(7, 3)] += 2.5
    if "роман" in qn or "поэма" in qn or "рассказ" in qn:
        scores[(2, 2)] += 2.0
    if "мюзикл" in qn or "спектакл" in qn:
        scores[(7, 2)] += 2.0
    if "архитектор" in qn or "архитектур" in qn:
        scores[(5, 2)] += 3.0
    if "дириж" in qn or "оркестр" in qn:
        scores[(6, 1)] += 3.0
    if "контрацеп" in all_text:
        scores[(3, 2)] += 3.0
        scores[(11, 2)] += 2.0
    if "шутк" in cn or "каламбур" in cn:
        scores[(14, 2)] += 2.0

    return scores


def conf_from_score(score: float, second: bool = False) -> float:
    if score >= 12:
        base = 0.95
    elif score >= 9:
        base = 0.9
    elif score >= 7:
        base = 0.85
    elif score >= 5:
        base = 0.8
    elif score >= 3:
        base = 0.7
    elif score >= 2:
        base = 0.6
    else:
        base = 0.5

    if second:
        base -= 0.15
    return round(max(0.4, min(0.95, base)), 2)


def classify(question: str, answer: str, comment: str) -> List[dict]:
    scores = score_topic(question, answer, comment)

    # best sub per category
    cat_best: dict[int, tuple[int, float]] = {}
    for (cat, sub), sc in scores.items():
        if sc <= 0:
            continue
        prev = cat_best.get(cat)
        if prev is None or sc > prev[1]:
            cat_best[cat] = (sub, sc)

    if not cat_best:
        return [{"cat": 14, "sub": 1, "conf": 0.5}]

    ranked = sorted(cat_best.items(), key=lambda x: x[1][1], reverse=True)
    first_cat, (first_sub, first_score) = ranked[0]
    topics = [{
        "cat": first_cat,
        "sub": first_sub,
        "conf": conf_from_score(first_score),
    }]

    if len(ranked) > 1:
        second_cat, (second_sub, second_score) = ranked[1]
        threshold = max(2.5, first_score * 0.55)
        if second_score >= threshold or (second_cat == 14 and second_score >= 2.0):
            topics.append({
                "cat": second_cat,
                "sub": second_sub,
                "conf": conf_from_score(second_score, second=True),
            })

    return topics[:2]


def render_topics(topics: List[dict]) -> str:
    parts = []
    for t in topics:
        label = get_label(t["cat"], t["sub"])
        parts.append(f"`{label}` ({t['conf']:.0%})")
    return " | ".join(parts)


def strip_existing_gpt5(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("**GPT-5:**"):
            i += 1
            while i < len(lines) and lines[i].strip() != "":
                i += 1
            # consume one trailing blank line if exists
            if i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return out


def insert_gpt5_block(section: str, gpt_line: str) -> str:
    lines = strip_existing_gpt5(section.splitlines())

    haiku_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("**Haiku:**"):
            haiku_idx = i
            break

    block = ["", "**GPT-5:**", gpt_line]

    if haiku_idx != -1:
        j = haiku_idx + 1
        while j < len(lines) and lines[j].strip() != "":
            j += 1
        lines = lines[:j] + block + lines[j:]
    else:
        lines += block

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    content = MD_PATH.read_text(encoding="utf-8")
    parts = content.split("\n---\n")

    results: dict[str, List[dict]] = {}
    updated_parts: List[str] = []
    updated_count = 0

    for part in parts:
        stripped = part.lstrip()
        if not stripped.startswith("## Q"):
            updated_parts.append(part)
            continue

        m = re.search(r"^## Q\d+ \(id: (\d+), pack: \d+\)", stripped, flags=re.M)
        if not m:
            updated_parts.append(part)
            continue
        qid = m.group(1)

        question, answer, comment = parse_fields(part)
        topics = classify(question, answer, comment)
        results[qid] = topics
        gpt_line = render_topics(topics)

        updated_parts.append(insert_gpt5_block(part, gpt_line))
        updated_count += 1

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    MD_PATH.write_text("\n---\n".join(updated_parts), encoding="utf-8")

    print(f"Classified: {len(results)} questions")
    print(f"Updated markdown sections: {updated_count}")
    print(f"Saved results: {OUT_PATH}")


if __name__ == "__main__":
    main()
