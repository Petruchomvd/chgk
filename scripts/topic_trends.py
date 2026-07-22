"""Тренды тем ЧГК по годам на основе LLM-классификации.

Метки берутся у ОДНОЙ модели (по умолчанию gemma@p2): раз классификатор
выбирает вопросы случайно, размеченное множество — несмещённая выборка
корпуса, и доли тем по годам можно сравнивать даже при неполном покрытии.

Использование:
    python scripts/topic_trends.py
    python scripts/topic_trends.py --model "google/gemma-4-26b-a4b-it@p2" --min-year 2012
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, PROJECT_ROOT
from database.db import get_readonly_connection

DEFAULT_MODEL = "google/gemma-4-26b-a4b-it@p2"
MIN_QUESTIONS_PER_YEAR = 150   # годы с меньшей выборкой не показываем
REPORT = PROJECT_ROOT / "docs" / "reports" / "topic-trends.md"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--min-year", type=int, default=2010)
    args = parser.parse_args()

    conn = get_readonly_connection(DB_PATH)
    rows = conn.execute(
        """
        SELECT CAST(SUBSTR(p.start_date, 1, 4) AS INTEGER) AS year,
               c.name_ru AS category,
               COUNT(DISTINCT qt.question_id) AS n
        FROM question_topics qt
        JOIN subcategories sc ON sc.id = qt.subcategory_id
        JOIN categories c ON c.id = sc.category_id
        JOIN questions q ON q.id = qt.question_id
        JOIN packs p ON p.id = q.pack_id
        WHERE qt.model_name = ?
          AND p.start_date IS NOT NULL AND LENGTH(p.start_date) >= 4
        GROUP BY year, category
        """,
        (args.model,),
    ).fetchall()

    by_year: dict[int, dict[str, int]] = defaultdict(dict)
    for r in rows:
        if r["year"] and r["year"] >= args.min_year:
            by_year[r["year"]][r["category"]] = r["n"]

    years = sorted(y for y, cats in by_year.items()
                   if sum(cats.values()) >= MIN_QUESTIONS_PER_YEAR)
    if len(years) < 3:
        sys.exit(f"мало данных: {len(years)} годов с выборкой >= {MIN_QUESTIONS_PER_YEAR}")

    cats = sorted({c for y in years for c in by_year[y]})
    shares: dict[str, list[float]] = {}
    for c in cats:
        shares[c] = [by_year[y].get(c, 0) / sum(by_year[y].values()) for y in years]

    def slope(vals: list[float]) -> float:
        n = len(vals)
        xs = list(range(n))
        mx, my = sum(xs) / n, sum(vals) / n
        num = sum((xs[i] - mx) * (vals[i] - my) for i in range(n))
        den = sum((x - mx) ** 2 for x in xs)
        return num / den if den else 0.0

    trends = sorted(((slope(v), c) for c, v in shares.items()), reverse=True)

    lines = []
    a = lines.append
    a("# Тренды тем ЧГК по годам")
    a("")
    a(f"Метки: `{args.model}`. Годы: {years[0]}–{years[-1]}. "
      f"Выборка несмещённая (классификатор берёт вопросы случайно).")
    a("")
    a("| Год | Вопросов в выборке |")
    a("|---|---:|")
    for y in years:
        a(f"| {y} | {sum(by_year[y].values())} |")
    a("")
    a("## Доли тем по годам (%)")
    a("")
    a("| Категория | " + " | ".join(str(y) for y in years) + " | тренд/год |")
    a("|---|" + "---:|" * (len(years) + 1))
    for sl, c in trends:
        cells = " | ".join(f"{v * 100:.1f}" for v in shares[c])
        a(f"| {c} | {cells} | {sl * 100:+.2f} п.п. |")
    a("")
    a("## Выводы")
    a("")
    rising = [(sl, c) for sl, c in trends if sl > 0.0015]
    falling = [(sl, c) for sl, c in trends if sl < -0.0015]
    if rising:
        a("**Растут:** " + ", ".join(f"{c} ({sl * 100:+.2f} п.п./год)" for sl, c in rising))
    if falling:
        a("**Угасают:** " + ", ".join(f"{c} ({sl * 100:+.2f} п.п./год)" for sl, c in falling))
    a("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nсохранено: {REPORT}")


if __name__ == "__main__":
    main()
