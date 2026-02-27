"""Генерация расширенного отчёта для подготовки к турниру ЧГК."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from database.db import get_connection
from analytics.queries import (
    category_stats,
    difficulty_by_category,
    top_categories,
    top_subcategories,
)

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "reports"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _trend_arrow(delta: float) -> str:
    if delta > 0.5:
        return f"↑ +{delta:.1f}%"
    if delta < -0.5:
        return f"↓ {delta:.1f}%"
    return "→"


def _compute_year_growth(conn) -> dict:
    """Рост/падение категорий: последний год vs предпоследний."""
    rows = conn.execute("""
        SELECT substr(p.published_date, 1, 4) AS year,
               c.name_ru AS category,
               COUNT(DISTINCT qt.question_id) AS count
        FROM question_topics qt
        JOIN questions q ON qt.question_id = q.id
        JOIN packs p ON q.pack_id = p.id
        JOIN subcategories s ON qt.subcategory_id = s.id
        JOIN categories c ON s.category_id = c.id
        WHERE p.published_date IS NOT NULL
        GROUP BY year, c.id
    """).fetchall()

    if not rows:
        return {}

    year_data = {}
    for r in rows:
        year = r["year"]
        if year not in year_data:
            year_data[year] = {}
        year_data[year][r["category"]] = r["count"]

    years = sorted(year_data.keys())
    if len(years) < 2:
        return {}

    last_year, prev_year = years[-1], years[-2]
    total_last = sum(year_data[last_year].values())
    total_prev = sum(year_data[prev_year].values())
    if total_last == 0 or total_prev == 0:
        return {}

    growth = {}
    for cat in set(year_data[last_year]) | set(year_data[prev_year]):
        pct_last = 100 * year_data[last_year].get(cat, 0) / total_last
        pct_prev = 100 * year_data[prev_year].get(cat, 0) / total_prev
        growth[cat] = round(pct_last - pct_prev, 1)
    return growth


def _compute_paired_categories(conn) -> list:
    """Парные категории (встречаются вместе в одном вопросе)."""
    rows = conn.execute("""
        SELECT c1.name_ru AS cat_a, c2.name_ru AS cat_b,
               COUNT(DISTINCT qt1.question_id) AS count
        FROM question_topics qt1
        JOIN question_topics qt2 ON qt1.question_id = qt2.question_id
            AND qt1.subcategory_id < qt2.subcategory_id
        JOIN subcategories s1 ON qt1.subcategory_id = s1.id
        JOIN subcategories s2 ON qt2.subcategory_id = s2.id
        JOIN categories c1 ON s1.category_id = c1.id
        JOIN categories c2 ON s2.category_id = c2.id
        WHERE c1.id != c2.id
        GROUP BY c1.id, c2.id
        HAVING count >= 3
        ORDER BY count DESC
        LIMIT 10
    """).fetchall()
    return [dict(r) for r in rows]


def generate_report():
    """Сгенерировать расширенный Markdown-отчёт с рекомендациями."""
    conn = get_connection(DB_PATH)

    stats = category_stats(conn)
    cats = top_categories(conn)
    subs = top_subcategories(conn, limit=30)
    growth = _compute_year_growth(conn)
    pairs = _compute_paired_categories(conn)
    diff = difficulty_by_category(conn)

    lines = []

    # Заголовок
    lines.append(f"# Анализ тематик ЧГК ({stats['total_questions']:,} вопросов)")
    lines.append("")

    # Ключевые выводы
    lines.append("## Ключевые выводы")
    lines.append("")
    if cats and len(cats) >= 3:
        top3_pct = sum(c["pct"] for c in cats[:3])
        lines.append(f"1. **Тройка лидеров:** {cats[0]['category']} ({cats[0]['pct']}%), "
                     f"{cats[1]['category']} ({cats[1]['pct']}%), "
                     f"{cats[2]['category']} ({cats[2]['pct']}%) — "
                     f"**{top3_pct:.0f}%** всех вопросов")
    if growth:
        rising = sorted(growth.items(), key=lambda x: x[1], reverse=True)
        falling = sorted(growth.items(), key=lambda x: x[1])
        if rising[0][1] > 0:
            lines.append(f"2. **Растущий тренд:** {rising[0][0]} (+{rising[0][1]}% за год)")
        if falling[0][1] < 0:
            lines.append(f"3. **Падающий тренд:** {falling[0][0]} ({falling[0][1]}% за год)")
    lines.append("")

    # Общая статистика
    lines.append("## Общая статистика")
    lines.append("")
    lines.append(f"- **Пакетов:** {stats['total_packs']}")
    lines.append(f"- **Вопросов в базе:** {stats['total_questions']:,}")
    lines.append(f"- **Классифицировано:** {stats['classified_questions']:,} ({stats['classification_pct']}%)")
    lines.append("")

    # Рекомендации по подготовке
    lines.append("## Рекомендации по подготовке")
    lines.append("")
    if cats:
        lines.append("### Обязательные темы (ТОП-5)")
        lines.append("")
        for c in cats[:5]:
            trend = ""
            if c["category"] in growth:
                trend = f" {_trend_arrow(growth[c['category']])}"
            lines.append(f"- **{c['category']}** — {c['pct']}% ({c['count']:,} вопросов){trend}")
        lines.append("")

        lines.append("### Высокий ROI")
        lines.append("")
        lines.append("Темы, которые часто застают врасплох, но встречаются регулярно:")
        lines.append("")
        for c in cats[5:10]:
            lines.append(f"- **{c['category']}** — {c['pct']}%")
        lines.append("")

        if len(cats) > 10:
            lines.append("### Редкие темы (знать минимально)")
            lines.append("")
            for c in cats[10:]:
                lines.append(f"- {c['category']} ({c['pct']}%)")
            lines.append("")

    # Детализация по категориям
    lines.append("## Детализация по категориям")
    lines.append("")
    lines.append("| # | Категория | Вопросов | % | Тренд | Сложность |")
    lines.append("|---|-----------|----------|---|-------|-----------|")
    diff_map = {d["category"]: d["avg_difficulty"] for d in diff} if diff else {}
    for i, c in enumerate(cats, 1):
        trend = _trend_arrow(growth.get(c["category"], 0)) if growth else "—"
        difficulty = f"{diff_map[c['category']]:.2f}" if c["category"] in diff_map else "—"
        lines.append(f"| {i} | {c['category']} | {c['count']:,} | {c['pct']}% | {trend} | {difficulty} |")
    lines.append("")

    # ТОП подкатегорий
    lines.append("## ТОП-20 подкатегорий")
    lines.append("")
    lines.append("| # | Категория → Подкатегория | Вопросов | % |")
    lines.append("|---|--------------------------|----------|---|")
    for i, s in enumerate(subs[:20], 1):
        label = f"{s['category']} → {s['subcategory']}"
        lines.append(f"| {i} | {label} | {s['count']:,} | {s['pct']}% |")
    lines.append("")

    # Парные тематики
    if pairs:
        lines.append("## Связанные тематики")
        lines.append("")
        lines.append("Категории, которые чаще встречаются вместе в одном вопросе:")
        lines.append("")
        lines.append("| Категория A | Категория B | Совместных вопросов |")
        lines.append("|-------------|-------------|---------------------|")
        for p in pairs:
            lines.append(f"| {p['cat_a']} | {p['cat_b']} | {p['count']} |")
        lines.append("")
        lines.append("**Вывод:** если готовитесь к одной из связанных тем, "
                     "подтяните и вторую — они часто пересекаются.")
        lines.append("")

    lines.append("---")
    lines.append("*Отчёт сгенерирован автоматически на основе анализа вопросов с gotquestions.online*")

    report = "\n".join(lines)

    path = OUTPUT_DIR / "report.md"
    path.write_text(report, encoding="utf-8")
    print(f"Отчёт сохранён: {path}")

    conn.close()
    return report


if __name__ == "__main__":
    generate_report()
