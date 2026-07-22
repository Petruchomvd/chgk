"""Печатает пул в читаемом виде в 3 файла (по блокам) для ручного отбора."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "data" / "candidate_test_pool.json"

data = json.loads(POOL.read_text(encoding="utf-8"))

for block, lst in data.items():
    out = ROOT / "data" / f"pool_{block}.txt"
    lines = []
    for i, q in enumerate(lst, 1):
        lines.append(f"=== #{i} [id={q['id']}] cat={q['cat_name']} subcat={q['subcat_ru']} diff={q['difficulty']} ===")
        if q.get("razdatka_text") and q["razdatka_text"].strip() != q["text"].strip():
            lines.append(f"RAZDATKA: {q['razdatka_text']}")
        lines.append(f"Q: {q['text']}")
        lines.append(f"A: {q['answer']}")
        if q.get("comment"):
            c = q["comment"][:300]
            lines.append(f"CMT: {c}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{out}: {len(lst)} questions")
