"""Тест промпта на 24 вопросах (10 раунд 1 + 14 раунд 2)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from classifier.prompts import build_stage1_messages
from classifier.providers import create_provider
from classifier.taxonomy import TAXONOMY_MAP
from config import DB_PATH
from database.db import get_connection

TEST_IDS = [
    # Раунд 1: История vs Быт
    325332, 325333, 325336, 325382, 325391, 325412, 325416, 325433, 325494, 325562,
    # Раунд 2: Быт-магнит + другие категории
    349285, 395276, 342143, 394594, 393302, 325957, 371338, 395073,
    395611, 331837, 403829, 383839, 406951, 362180,
]

EXPECTED = {
    # Раунд 1
    325332: "История",
    325333: "История",
    325336: "История",
    325382: "Быт и повседневность",
    325391: "История",
    325412: "История",
    325416: "Быт и повседневность",
    325433: "Логика и wordplay",
    325494: "Быт и повседневность",
    325562: "История",
    # Раунд 2
    349285: "Быт и повседневность",
    395276: "Быт и повседневность",
    342143: "Язык и лингвистика",
    394594: "Быт и повседневность",
    393302: "Литература",
    325957: "Быт и повседневность",
    371338: "Быт и повседневность",
    395073: "Музыка",
    395611: "Кино и театр",
    331837: "Логика и wordplay",
    403829: "История",
    383839: "Общество и политика",
    406951: "Кино и театр",
    362180: "Язык и лингвистика",
}

# Category number → name
from database.seed_taxonomy import TAXONOMY
CAT_NAMES = {i: name_ru for i, (_, name_ru, _) in enumerate(TAXONOMY, 1)}


def main():
    import re

    provider = create_provider("openrouter", model="qwen/qwen-2.5-72b-instruct")

    conn = get_connection(DB_PATH)
    conn.row_factory = __import__("sqlite3").Row

    placeholders = ",".join("?" * len(TEST_IDS))
    rows = conn.execute(
        f"SELECT id, text, answer, comment FROM questions WHERE id IN ({placeholders})",
        TEST_IDS,
    ).fetchall()

    questions = {r["id"]: r for r in rows}

    correct = 0
    total = len(TEST_IDS)

    print(f"\n{'═' * 70}")
    print(f"  Тест промпта на {total} вопросах (Qwen 72B)")
    print(f"{'═' * 70}\n")

    for qid in TEST_IDS:
        q = questions.get(qid)
        if not q:
            print(f"  #{qid}: НЕ НАЙДЕН В БД")
            continue

        messages = build_stage1_messages(q["text"], q["answer"], q["comment"] or "")
        response = provider.chat(messages, max_tokens=50)

        # Parse
        cat_name = "?"
        if response:
            match = re.search(r"\{[^}]+\}", response)
            if match:
                try:
                    data = json.loads(match.group())
                    cats = data.get("cats", [])
                    if cats:
                        cat_name = CAT_NAMES.get(cats[0], f"?({cats[0]})")
                except json.JSONDecodeError:
                    cat_name = f"parse error: {response[:40]}"

        expected = EXPECTED.get(qid, "?")
        ok = cat_name == expected
        if ok:
            correct += 1

        mark = "✓" if ok else "✗"
        print(f"  {mark} #{qid}: {cat_name:30s} (ожидалось: {expected})")

    print(f"\n{'═' * 70}")
    print(f"  Результат: {correct}/{total} ({correct/total*100:.0f}%)")
    print(f"  Стоимость: ${provider.estimated_cost:.4f}")
    print(f"{'═' * 70}")

    conn.close()


if __name__ == "__main__":
    main()
