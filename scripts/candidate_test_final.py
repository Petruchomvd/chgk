"""Финальный тест кандидата: 3 блока × 10 вопросов = 30, сложность оценена Claude вручную.

Блок 1 — профиль (история).
Блок 2 — соседние гуманитарные.
Блок 3 — вне зоны комфорта.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "chgk_analysis.db"
OUT_DIR = ROOT / "data"

# (question_id, claude_tier)
# tier: "easy" / "medium" / "hard" — оценка Claude
BLOCK_1_HISTORY = [
    (433962, "easy"),       # Античность — Тетрадь смерти / Сулла-проскрипции
    (394305, "medium"),     # Античность — Гильгамеш / зодиак / Весы
    (425502, "easy"),       # Средневековье — флаг Австрии / Леопольд V (бел.)
    (416903, "hard"),       # Средневековье/Новое — стрелка часов / Карл XII
    (367719, "easy"),       # Новейшая — секрет Виктории / императрица Индии
    (431990, "medium"),     # Новейшая — Тайберн/таверна / последний бокал
    (419834, "easy"),       # Россия/СССР — купчиха за чаем / Краснов
    (432645, "hard"),       # Россия/СССР — Разрусение / обрусение Финляндии
    (421047, "easy"),       # Военная — Непал пал / HoI4
    (427712, "medium"),     # Военная — Праведник народов мира / О'Флаэрти
]

BLOCK_2_ADJACENT = [
    (407445, "easy"),       # Литература — Шинель / Transformer
    (61016,  "easy"),       # Литература — часы / счастливые часов не наблюдают
    (430588, "medium"),     # География — шлюз / Спаарндам
    (418735, "hard"),       # География — Лондон / Гарри Бек
    (423394, "easy"),       # Искусство — футуризм/туризм Италия
    (422253, "hard"),       # Искусство — пьета / Тетрадь смерти / Ван Гог
    (428034, "easy"),       # Язык — Габа / габардин
    (34233,  "medium"),     # Религия — Ной / молоток и клещи
    (72692,  "medium"),     # Общество — санкюлоты / Варуфакис
    (416433, "easy"),       # Общество — сапёр / пацифисты
]

BLOCK_3_OUTSIDE = [
    (426739, "easy"),       # Наука — прививок от / комары-малярия
    (10127,  "easy"),       # Музыка — Венера / Холст / Плутон
    (395822, "hard"),       # Музыка — Реквием / Окегем
    (422629, "easy"),       # Кино — экрана / Гондри
    (420876, "medium"),     # Кино — трип / бэд-трип
    (8884,   "easy"),       # Спорт — выше головы не прыгнешь / Хольм
    (430446, "easy"),       # Быт — чеснок
    (418752, "hard"),       # Природа — рожать двойню / летучие мыши
    (25409,  "medium"),     # Логика — акт о помиловании / электростул
    (386176, "medium"),     # Логика — ответы кроссворда / Джон Грэм
]

BLOCKS = [
    ("Блок 1 — Профиль: История", BLOCK_1_HISTORY, "История — твой профиль. 5 простых / 3 средних / 2 сложных. Все 5 субкатегорий представлены."),
    ("Блок 2 — Соседние гуманитарные", BLOCK_2_ADJACENT, "Литература, география, искусство, язык, религия, общество — где историку обычно легче."),
    ("Блок 3 — Вне зоны комфорта", BLOCK_3_OUTSIDE, "Наука, музыка, кино, спорт, быт, природа, логика — проверка широты эрудиции и логики."),
]

TIER_LABEL = {"easy": "простой", "medium": "средний", "hard": "сложный"}


def fetch_questions(conn: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    placeholders = ",".join("?" * len(ids))
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT q.id, q.text, q.answer, q.zachet, q.comment, q.source, q.authors,
               q.razdatka_text, p.title AS pack_title
        FROM questions q JOIN packs p ON p.id = q.pack_id
        WHERE q.id IN ({placeholders})
        """,
        ids,
    )
    cols = [d[0] for d in cur.description]
    return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def clean(t: str | None) -> str:
    if not t:
        return ""
    return " ".join(t.split())


def razdatka_for(q: dict) -> str:
    rz = clean(q.get("razdatka_text"))
    if not rz:
        return ""
    if rz == clean(q.get("text")):
        return ""
    return rz


def render_candidate(data: list[tuple[str, list[tuple[int, str]], str, dict[int, dict]]]) -> str:
    lines: list[str] = []
    lines.append("# Тест кандидата — ЧГК (30 вопросов)")
    lines.append("")
    lines.append("**Кандидат:** студент-историк, 2-3 курс")
    lines.append("**Формат:** 3 блока × 10 вопросов. В каждом блоке: 5 простых, 3 средних, 2 сложных.")
    lines.append("**Сложность** указана субъективно (оценка составителя, не из БД).")
    lines.append("")
    lines.append("---")
    lines.append("")
    q_num = 0
    for title, spec, intro, by_id in data:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"*{intro}*")
        lines.append("")
        for qid, tier in spec:
            q = by_id[qid]
            q_num += 1
            lines.append(f"### Вопрос {q_num} ({TIER_LABEL[tier]})")
            lines.append("")
            rz = razdatka_for(q)
            if rz:
                lines.append(f"*Раздаточный материал:* {rz}")
                lines.append("")
            lines.append(clean(q["text"]))
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def render_answers(data: list[tuple[str, list[tuple[int, str]], str, dict[int, dict]]]) -> str:
    lines: list[str] = []
    lines.append("# Тест кандидата — ответы (30 вопросов)")
    lines.append("")
    lines.append("**Кандидат:** студент-историк, 2-3 курс")
    lines.append("")
    lines.append("---")
    lines.append("")
    q_num = 0
    for title, spec, intro, by_id in data:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"*{intro}*")
        lines.append("")
        for qid, tier in spec:
            q = by_id[qid]
            q_num += 1
            lines.append(f"### Вопрос {q_num} ({TIER_LABEL[tier]})")
            lines.append("")
            rz = razdatka_for(q)
            if rz:
                lines.append(f"*Раздаточный материал:* {rz}")
                lines.append("")
            lines.append(f"**Вопрос:** {clean(q['text'])}")
            lines.append("")
            lines.append(f"**Ответ:** {clean(q['answer'])}")
            lines.append("")
            if q.get("zachet"):
                lines.append(f"**Зачёт:** {clean(q['zachet'])}")
                lines.append("")
            if q.get("comment"):
                lines.append(f"**Комментарий:** {clean(q['comment'])}")
                lines.append("")
            meta = []
            if q.get("source"):
                meta.append(f"источник: {clean(q['source'])}")
            if q.get("authors"):
                meta.append(f"автор: {clean(q['authors'])}")
            meta.append(f"пакет: {clean(q.get('pack_title') or '')}")
            meta.append(f"id={q['id']}")
            lines.append("*" + " · ".join(meta) + "*")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    conn = sqlite3.connect(DB)
    all_ids = [qid for _, spec, _ in BLOCKS for qid, _ in spec]
    by_id = fetch_questions(conn, all_ids)

    missing = [qid for qid in all_ids if qid not in by_id]
    if missing:
        raise SystemExit(f"Missing IDs: {missing}")

    data = [(title, spec, intro, by_id) for title, spec, intro in BLOCKS]

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "candidate_test30_questions.md").write_text(render_candidate(data), encoding="utf-8")
    (OUT_DIR / "candidate_test30_answers.md").write_text(render_answers(data), encoding="utf-8")

    print(f"Generated {sum(len(spec) for _, spec, _ in BLOCKS)} questions across {len(BLOCKS)} blocks.")
    print(f"  Candidate: {OUT_DIR / 'candidate_test30_questions.md'}")
    print(f"  Answers:   {OUT_DIR / 'candidate_test30_answers.md'}")


if __name__ == "__main__":
    main()
