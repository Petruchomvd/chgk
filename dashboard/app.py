"""Streamlit-дашборд для анализа классификации ЧГК-вопросов.

Запуск:
    streamlit run dashboard/app.py
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import streamlit as st

from config import DB_PATH


# ─── Git LFS: подтянуть реальные файлы если на Streamlit Cloud ────
def _ensure_lfs():
    """Если БД — LFS-pointer, выполнить git lfs pull."""
    if not DB_PATH.exists():
        return
    # LFS pointer — текстовый файл < 1 КБ, начинается с "version "
    if DB_PATH.stat().st_size < 1024:
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                if f.read(8) == "version ":
                    subprocess.run(
                        ["git", "lfs", "pull"], cwd=str(DB_PATH.parent.parent),
                        timeout=120, check=False,
                    )
        except (UnicodeDecodeError, OSError):
            pass  # Настоящий бинарный файл — всё ок

_ensure_lfs()
from database.db import get_connection
from dashboard.db_queries import (
    agreement_matrix,
    author_categories,
    category_growth,
    confidence_by_category,
    confidence_distribution,
    count_search_results,
    difficulty_by_category,
    get_all_categories,
    get_available_models,
    get_classification_runs,
    get_common_questions,
    get_overview_stats,
    get_questions_by_ids,
    paired_categories,
    rare_subcategories,
    search_questions,
    top_authors,
    top_categories,
    top_subcategories,
    trends_by_month,
    trends_by_year,
)
from dashboard.components import (
    CATEGORY_COLORS,
    GENTLEMAN_CATEGORY_COLORS,
    agreement_heatmap,
    author_radar_chart,
    category_bar_chart,
    category_pie_chart,
    cohens_kappa,
    confidence_box_by_category,
    confidence_histogram,
    difficulty_bar_chart,
    gentleman_bar_chart,
    growth_bar_chart,
    model_confidence_comparison,
    subcategory_bar_chart,
    trends_line_chart,
    year_trends_heatmap,
)

# ─── Настройка страницы ───────────────────────────────────────────

st.set_page_config(
    page_title="ЧГК Анализ",
    page_icon="\U0001f9e0",
    layout="wide",
)


def get_conn():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    return conn


if "conn" not in st.session_state:
    st.session_state.conn = get_conn()
conn = st.session_state.conn


# ─── Хелперы ─────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
GENTLEMAN_CATEGORY_ORDER = [
    "Люди",
    "Места",
    "Произведения",
    "Наука",
    "Выражения",
    "Числа",
]
GENTLEMAN_CATEGORY_ALIASES = {
    "Наука и техника": "Наука",
    "Выражения и фразы": "Выражения",
    "Числа и даты": "Числа",
}


def _normalize_gentleman_categories(raw_categories: dict) -> dict[str, list[list]]:
    """Нормализовать категории «джентльменского набора» к 6 целевым."""
    merged: dict[str, dict[str, int]] = {
        category: {} for category in GENTLEMAN_CATEGORY_ORDER
    }

    if not isinstance(raw_categories, dict):
        return {category: [] for category in GENTLEMAN_CATEGORY_ORDER}

    for raw_name, items in raw_categories.items():
        normalized_name = GENTLEMAN_CATEGORY_ALIASES.get(raw_name, raw_name)
        if normalized_name not in merged or not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            answer, count = item
            answer_key = str(answer).strip().lower()
            if not answer_key:
                continue
            try:
                count_int = int(count)
            except (TypeError, ValueError):
                continue

            prev = merged[normalized_name].get(answer_key, 0)
            if count_int > prev:
                merged[normalized_name][answer_key] = count_int

    result: dict[str, list[list]] = {}
    for category in GENTLEMAN_CATEGORY_ORDER:
        items = [[answer, count] for answer, count in merged[category].items()]
        items.sort(key=lambda x: x[1], reverse=True)
        result[category] = items
    return result


# ─── Sidebar ──────────────────────────────────────────────────────

st.sidebar.title("ЧГК Анализ")

SECTIONS = ["Обзор", "Аналитика", "Тренировка"]
section = st.sidebar.radio("Раздел", SECTIONS, label_visibility="collapsed")

ANALYTICS_PAGES = [
    "Категории", "Тренды", "Рекомендации", "Авторы",
    "Сравнение моделей", "Уверенность", "Вопросы", "Джентльменский набор",
]

model_filter = None
page = section  # default

if section == "Аналитика":
    st.sidebar.markdown("---")
    page = st.sidebar.radio("Страница", ANALYTICS_PAGES, label_visibility="collapsed")

if section != "Тренировка":
    st.sidebar.markdown("---")
    models = get_available_models(conn)
    model_options = ["Все модели"] + models
    selected_model = st.sidebar.selectbox("Модель классификации", model_options)
    model_filter = None if selected_model == "Все модели" else selected_model

    stats = get_overview_stats(conn, model_filter)
    st.sidebar.metric("Всего вопросов", f"{stats['total_questions']:,}")
    st.sidebar.metric("Пакетов", f"{stats['total_packs']:,}")
    st.sidebar.metric("Классифицировано", f"{stats['classified']:,}")
    st.sidebar.metric("Покрытие", f"{stats['classification_pct']}%")

    if model_filter:
        st.sidebar.markdown(f"**Фильтр:** `{model_filter}`")

# ═══════════════ Обзор ═════════════════════════════════════════════

if page == "Обзор":
    st.header("Обзор классификации")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Вопросов", f"{stats['total_questions']:,}")
    col2.metric("Пакетов", f"{stats['total_packs']:,}")
    col3.metric("Классифицировано", f"{stats['classified']:,}")
    col4.metric("Покрытие", f"{stats['classification_pct']}%")

    cats = top_categories(conn, model_filter)
    if cats:
        df_cats = pd.DataFrame(cats)
        col_bar, col_pie = st.columns(2)
        with col_bar:
            st.plotly_chart(category_bar_chart(df_cats), use_container_width=True)
        with col_pie:
            st.plotly_chart(category_pie_chart(df_cats), use_container_width=True)
    else:
        st.info("Нет данных для отображения. Запустите классификацию.")

    # История запусков
    runs = get_classification_runs(conn)
    if runs:
        with st.expander("История запусков классификации"):
            df_runs = pd.DataFrame(runs)
            cols_to_show = [c for c in ["started_at", "finished_at", "method", "model_name",
                                         "questions_processed", "questions_failed"] if c in df_runs.columns]
            st.dataframe(df_runs[cols_to_show], use_container_width=True)


# ═══════════════ Категории ═════════════════════════════════════════

elif page == "Категории":
    st.header("Категории и подкатегории")

    all_cats = get_all_categories(conn)
    cat_names = {c["id"]: c["name_ru"] for c in all_cats}
    cat_options = ["Все категории"] + [c["name_ru"] for c in all_cats]
    selected_cat = st.selectbox("Выберите категорию", cat_options)

    if selected_cat == "Все категории":
        # ТОП подкатегорий (все)
        subs = top_subcategories(conn, model_filter, limit=30)
        if subs:
            st.plotly_chart(
                subcategory_bar_chart(pd.DataFrame(subs)),
                use_container_width=True,
            )
    else:
        # Drill-down: подкатегории внутри выбранной категории
        cat_id = next(c["id"] for c in all_cats if c["name_ru"] == selected_cat)
        subs = top_subcategories(conn, model_filter, category_id=cat_id, limit=20)
        if subs:
            st.plotly_chart(
                subcategory_bar_chart(pd.DataFrame(subs)),
                use_container_width=True,
            )
        else:
            st.info(f"Нет данных по категории \"{selected_cat}\"")

    st.markdown("---")

    # Тренды
    col_trends, col_diff = st.columns(2)

    with col_trends:
        trends = trends_by_month(conn, model_filter)
        if trends:
            st.plotly_chart(
                trends_line_chart(pd.DataFrame(trends)),
                use_container_width=True,
            )
        else:
            st.info("Нет данных о трендах")

    with col_diff:
        diff = difficulty_by_category(conn, model_filter)
        if diff:
            st.plotly_chart(
                difficulty_bar_chart(pd.DataFrame(diff)),
                use_container_width=True,
            )
        else:
            st.info("Нет данных о сложности")


# ═══════════════ Тренды ════════════════════════════════════════════

elif page == "Тренды":
    st.header("Тренды по годам")

    yearly = trends_by_year(conn, model_filter)
    if yearly:
        df_yearly = pd.DataFrame(yearly)

        # Heatmap: год × категория
        st.plotly_chart(year_trends_heatmap(df_yearly), use_container_width=True)

        # Рост/падение
        growth = category_growth(conn, model_filter)
        if growth:
            df_growth = pd.DataFrame(growth)
            st.plotly_chart(growth_bar_chart(df_growth), use_container_width=True)

            # Таблица: топ-5 растущих и топ-5 падающих
            col_up, col_down = st.columns(2)
            with col_up:
                st.subheader("Растущие тематики")
                top_up = df_growth.nlargest(5, "delta")
                for _, row in top_up.iterrows():
                    delta = row["delta"]
                    if delta > 0:
                        st.markdown(f"- **{row['category']}**: {row['current_pct']}% (+{delta}%)")
            with col_down:
                st.subheader("Падающие тематики")
                top_down = df_growth.nsmallest(5, "delta")
                for _, row in top_down.iterrows():
                    delta = row["delta"]
                    if delta < 0:
                        st.markdown(f"- **{row['category']}**: {row['current_pct']}% ({delta}%)")
    else:
        st.info("Нет данных для анализа трендов. Нужна классификация с привязкой к датам пакетов.")


# ═══════════════ Рекомендации ══════════════════════════════════════

elif page == "Рекомендации":
    st.header("Рекомендации по подготовке")

    cats = top_categories(conn, model_filter)
    if cats:
        df_cats = pd.DataFrame(cats)
        total_classified = df_cats["count"].sum()

        # Топ-10 тематик
        st.subheader("Топ-10 тематик для подготовки")
        for i, row in df_cats.head(10).iterrows():
            bar_len = int(row["pct"] / df_cats["pct"].max() * 20)
            bar = "█" * bar_len
            st.markdown(f"**{i+1}. {row['category']}** — {row['pct']}% ({row['count']:,} вопросов) `{bar}`")

        st.markdown("---")

        # Парные категории
        st.subheader("Связанные тематики")
        st.caption("Категории, которые чаще встречаются вместе в одном вопросе")
        pairs = paired_categories(conn, model_filter)
        if pairs:
            for p in pairs[:10]:
                st.markdown(f"- **{p['category_a']}** + **{p['category_b']}** — {p['count']} совместных вопросов")
        else:
            st.info("Недостаточно данных о парных категориях (нужны вопросы с 2+ тегами)")

        st.markdown("---")

        # Редкие темы
        st.subheader("Редкие темы (< 1%)")
        st.caption("Потенциальные «сюрпризы» — темы, которые встречаются редко, но всё же попадаются")
        rare = rare_subcategories(conn, model_filter)
        if rare:
            df_rare = pd.DataFrame(rare)
            st.dataframe(
                df_rare[["category", "subcategory", "count", "pct"]].rename(columns={
                    "category": "Категория",
                    "subcategory": "Подкатегория",
                    "count": "Вопросов",
                    "pct": "Доля %",
                }),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Нет данных о редких темах")

        st.markdown("---")

        # Тренды в рекомендациях
        growth = category_growth(conn, model_filter)
        if growth:
            df_growth = pd.DataFrame(growth)
            rising = df_growth[df_growth["delta"] > 0.5].sort_values("delta", ascending=False)
            if not rising.empty:
                st.subheader("Растущие тренды")
                for _, row in rising.head(5).iterrows():
                    st.markdown(f"- **{row['category']}** растёт: {row['prev_pct']}% → {row['current_pct']}% (+{row['delta']}%)")
    else:
        st.info("Нет данных для рекомендаций. Запустите классификацию.")


# ═══════════════ Авторы ════════════════════════════════════════════

elif page == "Авторы":
    st.header("Анализ авторов")

    authors = top_authors(conn, limit=30)
    if authors:
        st.subheader("Топ авторов по количеству пакетов")
        df_authors = pd.DataFrame(authors)
        st.dataframe(
            df_authors.rename(columns={
                "authors": "Автор(ы)",
                "pack_count": "Пакетов",
                "question_count": "Вопросов",
            }),
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("---")

        # Выбор автора для профиля
        author_list = df_authors["authors"].tolist()
        selected_author = st.selectbox("Выберите автора для тематического профиля", author_list)

        if selected_author:
            profile = author_categories(conn, selected_author, model_filter)
            if profile and len(profile) >= 3:
                st.plotly_chart(
                    author_radar_chart(profile, selected_author),
                    use_container_width=True,
                )

                # Таблица детализации
                df_profile = pd.DataFrame(profile)
                total = df_profile["count"].sum()
                df_profile["pct"] = (100 * df_profile["count"] / total).round(1)
                st.dataframe(
                    df_profile[["category", "count", "pct"]].rename(columns={
                        "category": "Категория",
                        "count": "Вопросов",
                        "pct": "Доля %",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
            elif profile:
                st.info(f"Недостаточно классифицированных вопросов для автора \"{selected_author}\"")
            else:
                st.info(f"Нет классифицированных вопросов для автора \"{selected_author}\"")
    else:
        st.info("Нет данных об авторах пакетов")


# ═══════════════ Сравнение моделей ═════════════════════════════════

elif page == "Сравнение моделей":
    st.header("Сравнение моделей")

    if len(models) < 2:
        st.warning("Для сравнения нужны данные минимум от 2 моделей. "
                    "Сейчас классификация выполнена только одной моделью.")
    else:
        col_a, col_b = st.columns(2)
        with col_a:
            model_a = st.selectbox("Модель A", models, index=0)
        with col_b:
            model_b = st.selectbox("Модель B", models, index=min(1, len(models) - 1))

        if model_a == model_b:
            st.warning("Выберите две разные модели для сравнения.")
        else:
            common = get_common_questions(conn, model_a, model_b)

            if not common:
                st.warning("Нет вопросов, классифицированных обеими моделями.")
            else:
                df_common = pd.DataFrame(common)

                # Метрики
                total_common = len(df_common)
                agreed = (df_common["cat_id_a"] == df_common["cat_id_b"]).sum()
                agree_pct = round(100 * agreed / total_common, 1) if total_common > 0 else 0

                # Cohen's Kappa
                cat_list = [c["name_ru"] for c in get_all_categories(conn)]
                matrix_data = agreement_matrix(conn, model_a, model_b)
                cat_idx = {c: i for i, c in enumerate(cat_list)}
                n = len(cat_list)
                matrix = np.zeros((n, n), dtype=int)
                for row in matrix_data:
                    i = cat_idx.get(row["cat_a"])
                    j = cat_idx.get(row["cat_b"])
                    if i is not None and j is not None:
                        matrix[i][j] = row["count"]
                kappa = cohens_kappa(matrix)

                m1, m2, m3 = st.columns(3)
                m1.metric("Общих вопросов", f"{total_common:,}")
                m2.metric("Совпадение категорий", f"{agree_pct}%")
                m3.metric("Cohen's Kappa", f"{kappa:.3f}")

                # Heatmap
                st.plotly_chart(
                    agreement_heatmap(matrix_data, cat_list, model_a, model_b),
                    use_container_width=True,
                )

                # Сравнение уверенности
                st.plotly_chart(
                    model_confidence_comparison(df_common, model_a, model_b),
                    use_container_width=True,
                )

                # Таблица разногласий
                disagreements = df_common[df_common["cat_id_a"] != df_common["cat_id_b"]].copy()
                if not disagreements.empty:
                    with st.expander(f"Разногласия ({len(disagreements)} вопросов)"):
                        # Подтянуть текст вопроса
                        q_ids = disagreements["question_id"].tolist()
                        placeholders = ",".join("?" * len(q_ids))
                        q_rows = conn.execute(
                            f"SELECT id, substr(text, 1, 120) AS text_short FROM questions WHERE id IN ({placeholders})",
                            q_ids,
                        ).fetchall()
                        q_texts = {r["id"]: r["text_short"] for r in q_rows}
                        disagreements["text"] = disagreements["question_id"].map(q_texts)

                        st.dataframe(
                            disagreements[["question_id", "text", "cat_a", "conf_a", "cat_b", "conf_b"]].rename(
                                columns={
                                    "question_id": "ID",
                                    "text": "Вопрос",
                                    "cat_a": model_a,
                                    "conf_a": f"Увер. {model_a[:10]}",
                                    "cat_b": model_b,
                                    "conf_b": f"Увер. {model_b[:10]}",
                                }
                            ),
                            use_container_width=True,
                            hide_index=True,
                            height=400,
                        )


# ═══════════════ Уверенность ═══════════════════════════════════════

elif page == "Уверенность":
    st.header("Анализ уверенности")

    conf_data = confidence_distribution(conn, model_filter)
    if conf_data:
        df_conf = pd.DataFrame(conf_data)

        col_hist, col_box = st.columns(2)
        with col_hist:
            st.plotly_chart(confidence_histogram(df_conf), use_container_width=True)
        with col_box:
            st.plotly_chart(confidence_box_by_category(df_conf), use_container_width=True)

        # Таблица средних значений
        conf_cats = confidence_by_category(conn, model_filter)
        if conf_cats:
            st.subheader("Средняя уверенность по категориям")
            df_cc = pd.DataFrame(conf_cats)
            st.dataframe(
                df_cc[["category", "avg_conf", "min_conf", "max_conf", "count"]].rename(
                    columns={
                        "category": "Категория",
                        "avg_conf": "Средняя",
                        "min_conf": "Мин.",
                        "max_conf": "Макс.",
                        "count": "Кол-во",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )
    else:
        st.info("Нет данных об уверенности")


# ═══════════════ Вопросы ═══════════════════════════════════════════

elif page == "Вопросы":
    st.header("Браузер вопросов")

    col_search, col_cat_filter = st.columns([3, 1])
    with col_search:
        search_text = st.text_input("Поиск по тексту вопроса", placeholder="Введите ключевое слово...")
    with col_cat_filter:
        all_cats_list = get_all_categories(conn)
        cat_filter_options = ["Все"] + [c["name_ru"] for c in all_cats_list]
        cat_filter_selected = st.selectbox("Категория", cat_filter_options, key="q_cat_filter")

    cat_id_filter = None
    if cat_filter_selected != "Все":
        cat_id_filter = next(c["id"] for c in all_cats_list if c["name_ru"] == cat_filter_selected)

    PAGE_SIZE = 30
    total_results = count_search_results(conn, model_filter, search_text, cat_id_filter)
    total_pages = max(1, (total_results + PAGE_SIZE - 1) // PAGE_SIZE)

    page = st.number_input("Страница", min_value=1, max_value=total_pages, value=1, step=1)
    offset = (page - 1) * PAGE_SIZE

    st.caption(f"Найдено: {total_results:,} | Страница {page} из {total_pages}")

    questions = search_questions(
        conn, model_filter, search_text, cat_id_filter,
        limit=PAGE_SIZE, offset=offset,
    )

    if questions:
        for q in questions:
            text_preview = (q["text"] or "")[:150]
            if len(q["text"] or "") > 150:
                text_preview += "..."

            cat_label = q.get("category") or "—"
            sub_label = q.get("subcategory") or ""
            conf = q.get("confidence")
            conf_str = f" ({conf:.0%})" if conf else ""
            model_str = f" [{q.get('model_name', '')}]" if q.get("model_name") else ""

            header = f"**#{q['id']}** | {cat_label}"
            if sub_label:
                header += f" \u2192 {sub_label}"
            header += f"{conf_str}{model_str}"

            with st.expander(header):
                st.markdown(f"**Вопрос:** {q['text']}")
                st.markdown(f"**Ответ:** {q['answer']}")
                if q.get("comment"):
                    st.markdown(f"**Комментарий:** {q['comment']}")
                if q.get("pack_title"):
                    st.markdown(f"**Пакет:** {q['pack_title']}")
                if q.get("difficulty"):
                    st.markdown(f"**Сложность:** {q['difficulty']}")
    else:
        st.info("Ничего не найдено")

# ═══════════════ Джентльменский набор ════════════════════════════

elif page == "Джентльменский набор":
    import json

    st.header("Джентльменский набор")
    st.caption("Самые частые ответы в ЧГК: люди, места, произведения, понятия — всё, что должен знать игрок")

    data_dir = PROJECT_ROOT / "data" / "gentleman_set"

    has_categorized = (data_dir / "categorized_answers.json").exists()
    has_top_answers = (data_dir / "top_answers.json").exists()
    has_entities = (data_dir / "entities.json").exists()
    has_context = (data_dir / "entities_context.json").exists()

    if not has_entities and not has_top_answers:
        st.warning("Данные ещё не сгенерированы. Запустите:\n\n"
                   "`python scripts/analyze_answers.py`")
        st.stop()

    # Загрузка метаданных
    if (data_dir / "meta.json").exists():
        meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
        st.sidebar.markdown("---")
        st.sidebar.markdown("**Gentleman Set**")
        st.sidebar.markdown(f"Обновлён: {meta['generated_at'][:10]}")
        st.sidebar.markdown(f"Вопросов: {meta['total_questions']:,}")

    # Общие контролы
    min_freq = st.slider("Минимальная частота упоминания", 2, 30, 3)
    top_n = st.slider("Показать топ-N", 10, 100, 30)

    def _show_tab(data_list, name_label, count_label, title, color,
                  entity_questions, display_forms=None, pack_counts=None):
        """Отобразить вкладку с графиком, таблицей и drill-down."""
        filtered = [(name, cnt) for name, cnt in data_list if cnt >= min_freq]
        if not filtered:
            st.info("Нет данных с указанной частотой")
            return

        # Подставить display-формы если есть
        if display_forms:
            display_data = [(display_forms.get(n, n), cnt) for n, cnt in filtered]
        else:
            display_data = filtered

        # Собрать DataFrame с опциональной колонкой "Пакетов"
        if pack_counts:
            rows = []
            for (name, cnt), (orig_name, _) in zip(display_data, filtered):
                packs = pack_counts.get(orig_name, 0)
                rows.append((name, cnt, packs))
            df = pd.DataFrame(rows, columns=[name_label, count_label, "Пакетов"])
        else:
            df = pd.DataFrame(display_data, columns=[name_label, count_label])

        shown_n = min(top_n, len(df))
        if shown_n < top_n:
            st.caption(
                f"В категории доступно {shown_n} элементов при текущей "
                f"минимальной частоте ({min_freq})."
            )

        col_chart, col_table = st.columns([2, 1])
        with col_chart:
            fig = gentleman_bar_chart(
                df, name_label, count_label, title, top_n=top_n, color=color
            )
            st.plotly_chart(fig, use_container_width=True)
        with col_table:
            st.dataframe(df.head(shown_n), use_container_width=True, hide_index=True)

        # Drill-down
        drill_options = filtered[:50]
        if display_forms:
            option_labels = [""] + [display_forms.get(n, n) for n, _ in drill_options]
        else:
            option_labels = [""] + [n for n, _ in drill_options]
        selected_label = st.selectbox(
            "Выберите для просмотра вопросов", option_labels, key=f"drill_{title}"
        )

        if selected_label:
            # Найти normalized ключ
            if display_forms:
                norm_key = next(
                    (n for n, _ in drill_options
                     if display_forms.get(n, n) == selected_label),
                    selected_label
                )
            else:
                norm_key = selected_label

            qids = entity_questions.get(norm_key, [])
            if qids:
                total_qs = len(qids)
                per_page = 20
                total_pages = max(1, (total_qs + per_page - 1) // per_page)

                col_info, col_nav = st.columns([1, 1])
                with col_info:
                    st.caption(f"Вопросов с «{selected_label}» в ответе: {total_qs}")
                with col_nav:
                    if total_pages > 1:
                        page_num = st.number_input(
                            f"Страница (из {total_pages})",
                            min_value=1, max_value=total_pages, value=1,
                            key=f"page_{title}",
                        )
                    else:
                        page_num = 1

                page_start = (page_num - 1) * per_page
                page_qids = qids[page_start:page_start + per_page]
                qs = get_questions_by_ids(conn, page_qids, limit=per_page)
                for q in qs:
                    with st.expander(f"#{q['id']} — {(q['text'] or '')[:100]}..."):
                        st.markdown(f"**Вопрос:** {q['text']}")
                        st.markdown(f"**Ответ:** {q['answer']}")
                        if q.get("comment"):
                            st.markdown(f"**Комментарий:** {q['comment']}")

    # ── Верхний уровень: По ответам / По контексту ──
    source_options = ["По ответам"]
    if has_context:
        source_options.append("По контексту (вопрос + ответ + комментарий)")

    source = st.radio("Источник данных", source_options, horizontal=True)

    # ════════════════════════════════════════════════════════════
    # По ответам
    # ════════════════════════════════════════════════════════════
    if source == "По ответам":
        sub_views = []
        if has_categorized:
            sub_views.append("По категориям")
        if has_top_answers:
            sub_views.append("Все ответы")
        if has_entities:
            sub_views.append("NER и ключевые слова")

        view = st.radio("Режим", sub_views, horizontal=True, key="answers_view")

        if view == "По категориям":
            cat_data = json.loads(
                (data_dir / "categorized_answers.json").read_text(encoding="utf-8")
            )
            top_data = json.loads(
                (data_dir / "top_answers.json").read_text(encoding="utf-8")
            )

            display_forms = {
                **top_data.get("display_forms", {}),
                **cat_data.get("display_forms", {}),
            }
            answer_questions = top_data.get("answer_questions", {})
            pack_counts = top_data.get("pack_counts", {})

            cat_meta = cat_data.get("meta", {})
            st.sidebar.markdown(f"Модель: {cat_data.get('model', '?')}")
            st.sidebar.markdown(f"Категоризировано: {cat_data.get('total_categorized', 0)}")
            if cat_meta.get("whitelist_categorized"):
                st.sidebar.markdown(f"  whitelist: {cat_meta['whitelist_categorized']}")
            if cat_meta.get("llm_categorized"):
                st.sidebar.markdown(f"  LLM: {cat_meta['llm_categorized']}")

            raw_categories = cat_data.get("categories", {})
            categories = _normalize_gentleman_categories(raw_categories)

            cat_cols = st.columns(len(GENTLEMAN_CATEGORY_ORDER))
            for col, cat_name in zip(cat_cols, GENTLEMAN_CATEGORY_ORDER):
                items = categories.get(cat_name, [])
                col.metric(cat_name, len(items))

            tab_names = [name for name in GENTLEMAN_CATEGORY_ORDER if categories.get(name)]
            if not tab_names:
                st.info("Нет категоризированных данных")
            else:
                tabs = st.tabs(tab_names)
                for tab, cat_name in zip(tabs, tab_names):
                    with tab:
                        color = GENTLEMAN_CATEGORY_COLORS.get(cat_name, "#666666")
                        _show_tab(
                            categories[cat_name],
                            "Ответ", "Вопросов",
                            f"Топ: {cat_name}", color,
                            answer_questions, display_forms,
                            pack_counts=pack_counts,
                        )

        elif view == "Все ответы":
            top_data = json.loads(
                (data_dir / "top_answers.json").read_text(encoding="utf-8")
            )
            display_forms = top_data.get("display_forms", {})
            answer_questions = top_data.get("answer_questions", {})

            _show_tab(
                top_data["top_answers"],
                "Ответ", "Вопросов",
                "Топ ответов ЧГК", "#4363d8",
                answer_questions, display_forms,
            )

        elif view == "NER и ключевые слова":
            entities = json.loads(
                (data_dir / "entities.json").read_text(encoding="utf-8")
            )
            keywords = json.loads(
                (data_dir / "keywords.json").read_text(encoding="utf-8")
            )

            tab_per, tab_loc, tab_org, tab_kw, tab_bg = st.tabs([
                "Люди", "Места", "Организации", "Ключевые слова", "Биграммы"
            ])

            with tab_per:
                _show_tab(entities["PER"], "Персона", "Вопросов",
                          "Топ людей в ответах", "#e6194b",
                          entities.get("entity_questions", {}))
            with tab_loc:
                _show_tab(entities["LOC"], "Место", "Вопросов",
                          "Топ мест в ответах", "#f58231",
                          entities.get("entity_questions", {}))
            with tab_org:
                _show_tab(entities["ORG"], "Организация", "Вопросов",
                          "Топ организаций в ответах", "#4363d8",
                          entities.get("entity_questions", {}))
            with tab_kw:
                _show_tab(keywords["lemmas"], "Слово", "Вопросов",
                          "Топ ключевых слов", "#3cb44b",
                          keywords.get("keyword_questions", {}))
            with tab_bg:
                _show_tab(keywords["bigrams"], "Биграмма", "Вопросов",
                          "Топ биграмм", "#911eb4", {})

    # ════════════════════════════════════════════════════════════
    # По контексту
    # ════════════════════════════════════════════════════════════
    else:
        ctx_entities = json.loads(
            (data_dir / "entities_context.json").read_text(encoding="utf-8")
        )
        ctx_keywords = json.loads(
            (data_dir / "keywords_context.json").read_text(encoding="utf-8")
        )

        if (data_dir / "meta_context.json").exists():
            ctx_meta = json.loads(
                (data_dir / "meta_context.json").read_text(encoding="utf-8")
            )
            st.caption(
                f"NER по полному контексту (вопрос + ответ + комментарий) · "
                f"Вопросов: {ctx_meta.get('total_questions', '?'):,}"
            )

        tab_per, tab_loc, tab_org, tab_kw, tab_bg = st.tabs([
            "Люди", "Места", "Организации", "Ключевые слова", "Биграммы"
        ])

        with tab_per:
            _show_tab(ctx_entities["PER"], "Персона", "Вопросов",
                      "Топ людей (контекст)", "#e6194b",
                      ctx_entities.get("entity_questions", {}))
        with tab_loc:
            _show_tab(ctx_entities["LOC"], "Место", "Вопросов",
                      "Топ мест (контекст)", "#f58231",
                      ctx_entities.get("entity_questions", {}))
        with tab_org:
            _show_tab(ctx_entities["ORG"], "Организация", "Вопросов",
                      "Топ организаций (контекст)", "#4363d8",
                      ctx_entities.get("entity_questions", {}))
        with tab_kw:
            _show_tab(ctx_keywords["lemmas"], "Слово", "Вопросов",
                      "Топ ключевых слов (контекст)", "#3cb44b",
                      ctx_keywords.get("keyword_questions", {}))
        with tab_bg:
            _show_tab(ctx_keywords["bigrams"], "Биграмма", "Вопросов",
                      "Топ биграмм (контекст)", "#911eb4", {})

elif page == "Тренировка":
    from dashboard.training import render_training_page
    render_training_page(conn, PROJECT_ROOT)
