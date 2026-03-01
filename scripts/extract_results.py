"""Extract Haiku classification results from conversation transcript."""
import json
import re
import sys

TRANSCRIPT = r"C:\Users\User\.claude\projects\c--Users-User-Desktop-OurOs---------chgk\cd30f360-00dc-4453-8bac-e4a6d302610a.jsonl"

all_results = {}

with open(TRANSCRIPT, "r", encoding="utf-8") as f:
    for line_num, line in enumerate(f, 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue

        s = json.dumps(obj, ensure_ascii=False)

        if "task-notification" not in s:
            continue
        if "topics" not in s:
            continue

        # Find <result>...</result>
        result_matches = re.findall(r"<result>(.*?)</result>", s, re.DOTALL)
        for rm in result_matches:
            # Unescape JSON string escapes
            rm_clean = rm.replace("\\n", "\n").replace("\\\\", "\\").replace('\\"', '"')
            # Find JSON code blocks
            json_blocks = re.findall(r"```json\s*\n(.*?)```", rm_clean, re.DOTALL)
            for jb in json_blocks:
                jb = jb.strip()
                try:
                    data = json.loads(jb)
                    if isinstance(data, list) and len(data) > 0 and "id" in data[0]:
                        for item in data:
                            all_results[item["id"]] = item["topics"]
                        print(f"Line {line_num}: extracted {len(data)} items, first id={data[0]['id']}")
                except Exception as e:
                    # Try fixing common issues
                    pass

print(f"\nTotal unique IDs: {len(all_results)}")

# Save results
output_path = r"c:\Users\User\Desktop\OurOs\Проекты\chgk\scripts\haiku_results.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2)
print(f"Saved to {output_path}")
