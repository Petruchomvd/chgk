"""Сравнение трёх моделей: Qwen 7B, Qwen 14B, Claude Haiku."""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_benchmark_ids import BENCHMARK_IDS
from config import DB_PATH
from database.db import get_connection

# Haiku results (from Claude Code agents)
HAIKU_RESULTS = [
    {"id": 394273, "topics": [{"cat": 2, "sub": 2, "conf": 0.8}]},
    {"id": 394274, "topics": [{"cat": 3, "sub": 5, "conf": 0.8}, {"cat": 11, "sub": 2, "conf": 0.6}]},
    {"id": 394275, "topics": [{"cat": 9, "sub": 2, "conf": 0.9}]},
    {"id": 394276, "topics": [{"cat": 3, "sub": 5, "conf": 0.8}]},
    {"id": 394277, "topics": [{"cat": 3, "sub": 2, "conf": 0.85}]},
    {"id": 394278, "topics": [{"cat": 9, "sub": 2, "conf": 0.8}]},
    {"id": 394279, "topics": [{"cat": 4, "sub": 2, "conf": 0.7}, {"cat": 11, "sub": 3, "conf": 0.7}]},
    {"id": 394280, "topics": [{"cat": 14, "sub": 1, "conf": 0.85}]},
    {"id": 394281, "topics": [{"cat": 5, "sub": 1, "conf": 0.8}]},
    {"id": 394282, "topics": [{"cat": 5, "sub": 1, "conf": 0.85}]},
    {"id": 394283, "topics": [{"cat": 2, "sub": 2, "conf": 0.85}]},
    {"id": 394284, "topics": [{"cat": 1, "sub": 2, "conf": 0.8}, {"cat": 1, "sub": 4, "conf": 0.75}]},
    {"id": 394285, "topics": [{"cat": 10, "sub": 3, "conf": 0.8}]},
    {"id": 394286, "topics": [{"cat": 7, "sub": 1, "conf": 0.85}, {"cat": 11, "sub": 1, "conf": 0.8}]},
    {"id": 394287, "topics": [{"cat": 7, "sub": 1, "conf": 0.85}]},
    {"id": 394288, "topics": [{"cat": 1, "sub": 3, "conf": 0.75}, {"cat": 11, "sub": 1, "conf": 0.8}]},
    {"id": 394289, "topics": [{"cat": 11, "sub": 1, "conf": 0.85}]},
    {"id": 394290, "topics": [{"cat": 5, "sub": 1, "conf": 0.85}]},
    {"id": 394291, "topics": [{"cat": 10, "sub": 1, "conf": 0.8}]},
    {"id": 394292, "topics": [{"cat": 2, "sub": 2, "conf": 0.8}]},
    {"id": 394293, "topics": [{"cat": 1, "sub": 3, "conf": 0.8}, {"cat": 3, "sub": 5, "conf": 0.6}]},
    {"id": 394294, "topics": [{"cat": 10, "sub": 2, "conf": 0.85}]},
    {"id": 394295, "topics": [{"cat": 10, "sub": 2, "conf": 0.9}]},
    {"id": 394296, "topics": [{"cat": 14, "sub": 2, "conf": 0.95}]},
    {"id": 394297, "topics": [{"cat": 1, "sub": 5, "conf": 0.9}]},
    {"id": 394298, "topics": [{"cat": 14, "sub": 2, "conf": 0.85}]},
    {"id": 394299, "topics": [{"cat": 9, "sub": 1, "conf": 0.8}]},
    {"id": 394300, "topics": [{"cat": 3, "sub": 1, "conf": 0.9}]},
    {"id": 394301, "topics": [{"cat": 3, "sub": 1, "conf": 0.75}, {"cat": 3, "sub": 5, "conf": 0.7}]},
    {"id": 394302, "topics": [{"cat": 3, "sub": 5, "conf": 0.85}]},
    {"id": 394303, "topics": [{"cat": 8, "sub": 4, "conf": 0.95}]},
    {"id": 394304, "topics": [{"cat": 3, "sub": 2, "conf": 0.9}, {"cat": 13, "sub": 1, "conf": 0.85}]},
    {"id": 394305, "topics": [{"cat": 10, "sub": 2, "conf": 0.85}]},
    {"id": 394306, "topics": [{"cat": 6, "sub": 1, "conf": 0.9}, {"cat": 7, "sub": 2, "conf": 0.7}]},
    {"id": 394307, "topics": [{"cat": 11, "sub": 3, "conf": 0.85}]},
    {"id": 394308, "topics": [{"cat": 1, "sub": 3, "conf": 0.9}]},
    {"id": 394309, "topics": [{"cat": 14, "sub": 2, "conf": 0.8}, {"cat": 2, "sub": 3, "conf": 0.6}]},
    {"id": 394310, "topics": [{"cat": 2, "sub": 3, "conf": 0.85}]},
    {"id": 394311, "topics": [{"cat": 3, "sub": 4, "conf": 0.9}, {"cat": 4, "sub": 1, "conf": 0.75}]},
    {"id": 394312, "topics": [{"cat": 2, "sub": 3, "conf": 0.9}, {"cat": 14, "sub": 2, "conf": 0.7}]},
    {"id": 394313, "topics": [{"cat": 2, "sub": 5, "conf": 0.85}]},
    {"id": 394314, "topics": [{"cat": 2, "sub": 2, "conf": 0.9}]},
    {"id": 394315, "topics": [{"cat": 4, "sub": 1, "conf": 0.8}]},
    {"id": 394316, "topics": [{"cat": 1, "sub": 3, "conf": 0.75}, {"cat": 3, "sub": 2, "conf": 0.6}]},
    {"id": 394317, "topics": [{"cat": 11, "sub": 1, "conf": 0.7}, {"cat": 7, "sub": 3, "conf": 0.5}]},
    {"id": 394318, "topics": [{"cat": 1, "sub": 5, "conf": 0.85}]},
    {"id": 394319, "topics": [{"cat": 1, "sub": 3, "conf": 0.75}, {"cat": 3, "sub": 5, "conf": 0.6}]},
    {"id": 394320, "topics": [{"cat": 7, "sub": 3, "conf": 0.65}]},
    {"id": 394321, "topics": [{"cat": 11, "sub": 2, "conf": 0.8}]},
    {"id": 394322, "topics": [{"cat": 11, "sub": 1, "conf": 0.65}, {"cat": 13, "sub": 3, "conf": 0.5}]},
]

# Manual labels (my assessment)
MANUAL = {
    394273: (2, "Литература"), 394274: (11, "Общество"), 394275: (9, "Язык"),
    394276: (3, "Наука"), 394277: (3, "Наука"), 394278: (9, "Язык"),
    394279: (4, "География"), 394280: (8, "Спорт"), 394281: (5, "Искусство"),
    394282: (5, "Искусство"), 394283: (2, "Литература"), 394284: (1, "История"),
    394285: (10, "Религия"), 394286: (7, "Кино"), 394287: (7, "Кино"),
    394288: (3, "Наука"), 394289: (11, "Общество"), 394290: (5, "Искусство"),
    394291: (10, "Религия"), 394292: (2, "Литература"), 394293: (3, "Наука"),
    394294: (10, "Религия"), 394295: (10, "Религия"), 394296: (9, "Язык"),
    394297: (1, "История"), 394298: (14, "Логика"), 394299: (9, "Язык"),
    394300: (3, "Наука"), 394301: (6, "Музыка"), 394302: (3, "Наука"),
    394303: (8, "Спорт"), 394304: (13, "Природа"), 394305: (10, "Религия"),
    394306: (6, "Музыка"), 394307: (1, "История"), 394308: (11, "Общество"),
    394309: (2, "Литература"), 394310: (6, "Музыка"), 394311: (4, "География"),
    394312: (9, "Язык"), 394313: (7, "Кино"), 394314: (2, "Литература"),
    394315: (3, "Наука"), 394316: (12, "Быт"), 394317: (7, "Кино"),
    394318: (1, "История"), 394319: (9, "Язык"), 394320: (12, "Быт"),
    394321: (11, "Общество"), 394322: (12, "Быт"),
}


def calc_accuracy(model_map):
    correct = 0
    wrong = []
    for qid in BENCHMARK_IDS:
        expected = MANUAL[qid][0]
        cats = [t["cat"] for t in model_map[qid]["topics"]]
        if expected in cats:
            correct += 1
        else:
            idx = BENCHMARK_IDS.index(qid) + 1
            wrong.append((idx, qid, expected, cats))
    return correct, wrong


def cat_distribution(model_map):
    counts = {}
    for qid in BENCHMARK_IDS:
        for t in model_map[qid]["topics"]:
            c = t["cat"]
            counts[c] = counts.get(c, 0) + 1
    return counts


CAT_NAMES = {
    1: "Ист", 2: "Лит", 3: "Наука", 4: "Геогр", 5: "Искус",
    6: "Муз", 7: "Кино", 8: "Спорт", 9: "Язык", 10: "Религ",
    11: "Общ", 12: "Быт", 13: "Прир", 14: "Логика",
}

# Load Ollama results
with open("output/benchmark_qwen2.5_7b-instruct-q4_K_M.json", encoding="utf-8") as f:
    r7b = json.load(f)
with open("output/benchmark_qwen2.5_14b-instruct-q4_K_M.json", encoding="utf-8") as f:
    r14b = json.load(f)

map7 = {r["id"]: r for r in r7b["results"]}
map14 = {r["id"]: r for r in r14b["results"]}
maph = {r["id"]: r for r in HAIKU_RESULTS}

c7, w7 = calc_accuracy(map7)
c14, w14 = calc_accuracy(map14)
ch, wh = calc_accuracy(maph)

print("=" * 70)
print("ИТОГОВОЕ СРАВНЕНИЕ ТРЁХ МОДЕЛЕЙ (50 вопросов)")
print("=" * 70)
print(f"  Qwen 7B:       {c7}/50 = {c7*2}%")
print(f"  Qwen 14B:      {c14}/50 = {c14*2}%")
print(f"  Claude Haiku:  {ch}/50 = {ch*2}%")
print()

# Distribution
print("Распределение по категориям (сколько раз модель выбрала каждую):")
print(f"  {'Кат':>8}", end="")
for label in ["7B", "14B", "Haiku", "Верно"]:
    print(f"  {label:>6}", end="")
print()

d7 = cat_distribution(map7)
d14 = cat_distribution(map14)
dh = cat_distribution(maph)

# Ground truth distribution
dgt = {}
for qid in BENCHMARK_IDS:
    c = MANUAL[qid][0]
    dgt[c] = dgt.get(c, 0) + 1

for cat in range(1, 15):
    name = CAT_NAMES[cat]
    print(f"  {cat:2}. {name:<5}", end="")
    for d in [d7, d14, dh, dgt]:
        print(f"  {d.get(cat, 0):>6}", end="")
    print()

print()
print("--- Haiku ошибся: ---")
# Resolve names
conn = get_connection(DB_PATH)
for idx, qid, expected, cats in wh:
    exp_name = MANUAL[qid][1]
    cats_str = ", ".join(f"{c}({CAT_NAMES.get(c, '?')})" for c in cats)
    ans = conn.execute("SELECT answer FROM questions WHERE id = ?", (qid,)).fetchone()[0]
    ans = (ans or "")[:30]
    print(f"  #{idx:2} [{ans}] ожидал: {expected}({exp_name}), Haiku: [{cats_str}]")

print()
print("--- Вопросы, где Haiku прав а 14B нет: ---")
for qid in BENCHMARK_IDS:
    expected = MANUAL[qid][0]
    cats14 = [t["cat"] for t in map14[qid]["topics"]]
    catsh = [t["cat"] for t in maph[qid]["topics"]]
    if expected in catsh and expected not in cats14:
        idx = BENCHMARK_IDS.index(qid) + 1
        ans = conn.execute("SELECT answer FROM questions WHERE id = ?", (qid,)).fetchone()[0]
        ans = (ans or "")[:30]
        print(f"  #{idx:2} [{ans}] ожидал: {expected}({MANUAL[qid][1]})")

print()
print("--- Вопросы, где 14B прав а Haiku нет: ---")
for qid in BENCHMARK_IDS:
    expected = MANUAL[qid][0]
    cats14 = [t["cat"] for t in map14[qid]["topics"]]
    catsh = [t["cat"] for t in maph[qid]["topics"]]
    if expected in cats14 and expected not in catsh:
        idx = BENCHMARK_IDS.index(qid) + 1
        ans = conn.execute("SELECT answer FROM questions WHERE id = ?", (qid,)).fetchone()[0]
        ans = (ans or "")[:30]
        print(f"  #{idx:2} [{ans}] ожидал: {expected}({MANUAL[qid][1]})")

conn.close()

# Save Haiku results
out_path = Path(__file__).parent.parent / "output" / "benchmark_claude_haiku.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"model": "claude-haiku-4-5", "results": HAIKU_RESULTS}, f, ensure_ascii=False, indent=2)
print(f"\nHaiku результаты сохранены: {out_path}")
