"""Benchmark на 140 вопросах: прогон Stage 1 через OpenRouter и сравнение с ручной разметкой."""

import json
import re
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: F401
from classifier.prompts import build_stage1_messages
from classifier.providers import create_provider
from database.seed_taxonomy import TAXONOMY

CAT_NAMES = {i: name_ru for i, (_, name_ru, _) in enumerate(TAXONOMY, 1)}
NAME_TO_NUM = {name_ru: i for i, (_, name_ru, _) in enumerate(TAXONOMY, 1)}

BENCHMARK_PATH = Path(__file__).parent.parent / "docs" / "классификация_qwen-qwen-2.5-72b-instruct.json"


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen/qwen-2.5-72b-instruct")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--dry-run", action="store_true", help="Только загрузить, не классифицировать")
    args = parser.parse_args()

    # Загружаем benchmark
    data = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    print(f"Загружено {len(data)} вопросов из benchmark")

    if args.dry_run:
        sections = defaultdict(int)
        for q in data:
            sections[q["section"]] += 1
        for s, c in sorted(sections.items(), key=lambda x: -x[1]):
            print(f"  {s}: {c}")
        return

    provider = create_provider(args.provider, model=args.model)
    print(f"Провайдер: {args.provider}, модель: {args.model}\n")

    correct_primary = 0
    correct_top2 = 0
    total = 0
    errors_by_cat = defaultdict(list)

    for i, q in enumerate(data):
        section = q["section"]
        expected_num = NAME_TO_NUM.get(section)
        if expected_num is None:
            print(f"  Неизвестная секция: {section}")
            continue

        text = q["text"]
        answer = q["answer"]
        comment = q.get("comment", "")

        messages = build_stage1_messages(text, answer, comment)
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
        primary_ok = len(cats) > 0 and cats[0] == expected_num
        top2_ok = expected_num in cats

        if primary_ok:
            correct_primary += 1
        if top2_ok:
            correct_top2 += 1

        mark = "✓" if top2_ok else "✗"
        cat_labels = [CAT_NAMES.get(c, f"?({c})") for c in cats]
        print(f"  {mark} [{i+1:3d}/{len(data)}] {section:25s} → {', '.join(cat_labels):40s}", end="")
        if not top2_ok:
            print(f"  ✗✗✗")
            errors_by_cat[section].append({
                "num": q["num"],
                "got": cat_labels,
                "text": text[:80],
            })
        else:
            print()

        # Промежуточные результаты каждые 20 вопросов
        if total % 20 == 0:
            print(f"\n  --- Промежуточно: primary {correct_primary}/{total} ({correct_primary/total*100:.1f}%), "
                  f"top-2 {correct_top2}/{total} ({correct_top2/total*100:.1f}%) ---\n")

    print(f"\n{'═' * 70}")
    print(f"  ИТОГО: {total} вопросов")
    print(f"  Primary accuracy: {correct_primary}/{total} ({correct_primary/total*100:.1f}%)")
    print(f"  Top-2 accuracy:   {correct_top2}/{total} ({correct_top2/total*100:.1f}%)")
    if hasattr(provider, 'estimated_cost'):
        print(f"  Стоимость: ${provider.estimated_cost:.4f}")
    print(f"{'═' * 70}")

    if errors_by_cat:
        print(f"\nОшибки по категориям:")
        for cat, errs in sorted(errors_by_cat.items(), key=lambda x: -len(x[1])):
            print(f"  {cat}: {len(errs)} ошибок")

    # Сохраняем результаты
    output = {
        "model": args.model,
        "total": total,
        "primary_accuracy": round(correct_primary / total * 100, 1),
        "top2_accuracy": round(correct_top2 / total * 100, 1),
        "errors": {cat: len(errs) for cat, errs in errors_by_cat.items()},
    }
    out_path = Path(__file__).parent.parent / "output" / "benchmark_140_results.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nРезультаты сохранены: {out_path}")


if __name__ == "__main__":
    main()
