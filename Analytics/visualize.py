"""Визуализация аналитики ЧГК-вопросов."""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from database.db import get_connection
from analytics.queries import (
    category_stats,
    top_categories,
    top_subcategories,
    trends_by_month,
)

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "charts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Настройка шрифтов для кириллицы
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["figure.figsize"] = (14, 8)
plt.rcParams["figure.dpi"] = 150


def plot_top_categories(conn):
    """Горизонтальная гистограмма: 14 категорий по частоте."""
    data = top_categories(conn)
    if not data:
        print("Нет данных для визуализации категорий")
        return

    df = pd.DataFrame(data)
    df = df.sort_values("count", ascending=True)

    fig, ax = plt.subplots(figsize=(12, 8))
    bars = ax.barh(df["category"], df["count"], color=plt.cm.Set3.colors[:len(df)])

    for bar, pct in zip(bars, df["pct"]):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height() / 2,
                f"{pct}%", va="center", fontsize=10)

    ax.set_xlabel("Количество вопросов")
    ax.set_title("Распределение вопросов ЧГК по категориям")
    plt.tight_layout()
    path = OUTPUT_DIR / "categories.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  Сохранено: {path}")


def plot_top_subcategories(conn, limit=20):
    """Горизонтальная гистограмма: ТОП подкатегорий."""
    data = top_subcategories(conn, limit=limit)
    if not data:
        print("Нет данных для визуализации подкатегорий")
        return

    df = pd.DataFrame(data)
    df["label"] = df["category"] + " → " + df["subcategory"]
    df = df.sort_values("count", ascending=True)

    fig, ax = plt.subplots(figsize=(14, 10))
    bars = ax.barh(df["label"], df["count"], color=plt.cm.tab20.colors[:len(df)])

    for bar, pct in zip(bars, df["pct"]):
        ax.text(bar.get_width() + 3, bar.get_y() + bar.get_height() / 2,
                f"{pct}%", va="center", fontsize=9)

    ax.set_xlabel("Количество вопросов")
    ax.set_title(f"ТОП-{limit} подкатегорий вопросов ЧГК")
    plt.tight_layout()
    path = OUTPUT_DIR / "subcategories.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  Сохранено: {path}")


def plot_trends(conn, top_n=6):
    """Line chart: тренды топ-N категорий по месяцам."""
    data = trends_by_month(conn)
    if not data:
        print("Нет данных для визуализации трендов")
        return

    df = pd.DataFrame(data)

    # Берём только топ-N категорий по общему числу
    top_cats = df.groupby("category")["count"].sum().nlargest(top_n).index.tolist()
    df = df[df["category"].isin(top_cats)]

    pivot = df.pivot_table(index="month", columns="category", values="count", fill_value=0)
    pivot = pivot.sort_index()

    fig, ax = plt.subplots(figsize=(14, 7))
    pivot.plot(ax=ax, marker="o", linewidth=2, markersize=4)

    ax.set_xlabel("Месяц")
    ax.set_ylabel("Количество вопросов")
    ax.set_title(f"Тренды ТОП-{top_n} категорий по месяцам")
    ax.legend(loc="upper left", fontsize=9)
    plt.xticks(rotation=45)
    plt.tight_layout()
    path = OUTPUT_DIR / "trends.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  Сохранено: {path}")


def generate_all_charts():
    """Сгенерировать все графики."""
    conn = get_connection(DB_PATH)
    stats = category_stats(conn)

    print(f"\n=== Статистика ===")
    print(f"Пакетов: {stats['total_packs']}")
    print(f"Вопросов: {stats['total_questions']}")
    print(f"Классифицировано: {stats['classified_questions']} ({stats['classification_pct']}%)")

    if stats["classified_questions"] == 0:
        print("\nНет классифицированных вопросов. Сначала запустите классификацию.")
        conn.close()
        return

    print(f"\nГенерация графиков...")
    plot_top_categories(conn)
    plot_top_subcategories(conn)
    plot_trends(conn)

    conn.close()
    print(f"\nВсе графики сохранены в {OUTPUT_DIR}")


if __name__ == "__main__":
    generate_all_charts()
