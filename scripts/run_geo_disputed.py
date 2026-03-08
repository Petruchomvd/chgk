"""Прогон спорных вопросов Географии через обновлённый промпт."""
import re
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: F401
from classifier.prompts import build_stage1_messages
from classifier.providers import create_provider
from database.db import get_connection
from config import DB_PATH
from database.seed_taxonomy import TAXONOMY

CAT_NAMES = {i: name_ru for i, (_, name_ru, _) in enumerate(TAXONOMY, 1)}

# Extract IDs from the review file
md_path = Path(__file__).parent.parent / "output" / "review_disputed_География.md"
content = md_path.read_text(encoding="utf-8")
ids = [int(m) for m in re.findall(r"### ID (\d+)", content)]
print(f"Found {len(ids)} question IDs")

conn = get_connection(DB_PATH)
provider = create_provider("openrouter", model="qwen/qwen-2.5-72b-instruct")
print(f"Provider ready\n")

geo_primary = 0
geo_top2 = 0
total = 0

for qid in ids:
    row = conn.execute("SELECT text, answer, comment FROM questions WHERE id = ?", (qid,)).fetchone()
    if not row:
        print(f"  ID {qid}: not found in DB")
        continue

    text, answer, comment = row
    messages = build_stage1_messages(text, answer or "", comment or "")
    response = provider.chat(messages, max_tokens=50)

    cats = []
    if response:
        match = re.search(r"\{[^}]+\}", response)
        if match:
            try:
                parsed = json.loads(match.group())
                cats = parsed.get("cats", [])
            except json.JSONDecodeError:
                pass

    total += 1
    is_geo_primary = len(cats) > 0 and cats[0] == 4
    is_geo_top2 = 4 in cats

    if is_geo_primary:
        geo_primary += 1
    if is_geo_top2:
        geo_top2 += 1

    cat_labels = [CAT_NAMES.get(c, f"?({c})") for c in cats]
    mark = "GEO" if is_geo_primary else ("geo" if is_geo_top2 else "   ")
    print(f"  [{total:2d}/{len(ids)}] {mark} ID {qid:6d} -> {' | '.join(cat_labels)}")

    if total % 10 == 0:
        print(f"    --- geo_primary: {geo_primary}/{total}, geo_top2: {geo_top2}/{total} ---")

print(f"\n{'=' * 60}")
print(f"ИТОГО: {total} вопросов")
print(f"География primary: {geo_primary}/{total} ({geo_primary/total*100:.1f}%)")
print(f"География в top-2: {geo_top2}/{total} ({geo_top2/total*100:.1f}%)")
print(f"Стоимость: ${provider.estimated_cost:.4f}")
