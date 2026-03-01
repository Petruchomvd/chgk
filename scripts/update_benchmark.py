"""Update benchmark-150-random.md with Haiku classification results."""
import json
import re
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from database.seed_taxonomy import TAXONOMY

# Build label lookup: (cat, sub) -> "Категория > Подкатегория"
def get_label(cat: int, sub: int) -> str:
    if 1 <= cat <= len(TAXONOMY):
        _, cat_name_ru, subs = TAXONOMY[cat - 1]
        if 1 <= sub <= len(subs):
            _, sub_name_ru = subs[sub - 1]
            return f"{cat_name_ru} > {sub_name_ru}"
        return cat_name_ru
    return f"?({cat}.{sub})"


# Load results
with open("scripts/haiku_results.json", "r", encoding="utf-8") as f:
    results = json.load(f)

# Read markdown
md_path = "docs/benchmark-150-random.md"
with open(md_path, "r", encoding="utf-8") as f:
    content = f.read()

# For each question, add classification after the comment block
# Pattern: ## Q{N} (id: {ID}, pack: {PACK_ID})
# We insert classification before the --- separator

lines = content.split("\n")
new_lines = []
i = 0
inserted = 0

while i < len(lines):
    line = lines[i]

    # Check for question header
    m = re.match(r"^## Q\d+ \(id: (\d+), pack: \d+\)", line)
    if m:
        qid = m.group(1)
        # Collect all lines until next ---
        block = [line]
        i += 1
        while i < len(lines) and lines[i].strip() != "---":
            block.append(lines[i])
            i += 1

        # Add classification before ---
        if qid in results:
            topics = results[qid]
            block.append("")
            block.append("**Haiku:**")
            parts = []
            for t in topics:
                label = get_label(t["cat"], t["sub"])
                conf = t["conf"]
                parts.append(f"`{label}` ({conf:.0%})")
            block.append(" | ".join(parts))
            inserted += 1

        new_lines.extend(block)
        # Add the --- separator
        if i < len(lines):
            new_lines.append(lines[i])
            i += 1
    else:
        new_lines.append(line)
        i += 1

with open(md_path, "w", encoding="utf-8") as f:
    f.write("\n".join(new_lines))

print(f"Updated {inserted}/150 questions with Haiku classifications")
