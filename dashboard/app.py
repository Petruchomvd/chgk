"""Streamlit-дашборд для анализа классификации ЧГК-вопросов.

Запуск:
    streamlit run dashboard/app.py
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import streamlit as st

from config import DB_PATH
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
    agreement_heatmap,
    author_radar_chart,
    category_bar_chart,
    category_pie_chart,
    cohens_kappa,
    confidence_box_by_category,
    confidence_histogram,
    difficulty_bar_chart,
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


@st.cache_resource
def get_conn():
    conn = get_connection(DB_PATH)
    conn.execute("PRAGMA query_only = ON")  # read-only safety
    # Streamlit reruns in different threads — allow cross-thread access
    import sqlite3
    conn2 = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn2.row_factory = sqlite3.Row
    conn2.execute("PRAGMA foreign_keys = ON")
    conn2.execute("PRAGMA query_only = ON")
    conn.close()
    return conn2


conn = get_conn()


# ─── Хелперы ─────────────────────────────────────────────────────

def get_ollama_models() -> list[str]:
    """Получить список моделей из Ollama."""
    try:
        import ollama
        return [m.model for m in ollama.list().models]
    except Exception:
        return []


def get_unclassified_count(model_name: str) -> int:
    """Посчитать неклассифицированные вопросы для модели."""
    row = conn.execute(
        """SELECT COUNT(*) FROM questions q
           WHERE NOT EXISTS (
               SELECT 1 FROM question_topics qt
               WHERE qt.question_id = q.id AND qt.model_name = ?
           )""",
        (model_name,),
    ).fetchone()
    return row[0] if row else 0


PROJECT_ROOT = Path(__file__).parent.parent


# ─── Sidebar ──────────────────────────────────────────────────────

st.sidebar.title("ЧГК Анализ")

models = get_available_models(conn)
model_options = ["Все модели"] + models
selected_model = st.sidebar.selectbox("Модель классификации", model_options)
model_filter = None if selected_model == "Все модели" else selected_model

st.sidebar.markdown("---")

stats = get_overview_stats(conn, model_filter)
st.sidebar.metric("Всего вопросов", f"{stats['total_questions']:,}")
st.sidebar.metric("Пакетов", f"{stats['total_packs']:,}")
st.sidebar.metric("Классифицировано", f"{stats['classified']:,}")
st.sidebar.metric("Покрытие", f"{stats['classification_pct']}%")

if model_filter:
    st.sidebar.markdown(f"**Фильтр:** `{model_filter}`")

# ─── Вкладки ──────────────────────────────────────────────────────

tab_run, tab1, tab2, tab_trends, tab_rec, tab_authors, tab3, tab4, tab5 = st.tabs([
    "Запуск", "Обзор", "Категории", "Тренды", "Рекомендации", "Авторы",
    "Сравнение моделей", "Уверенность", "Вопросы",
])

# ═══════════════ Вкладка: Запуск классификации ═════════════════════

# Инициализация session_state
if "classify_process" not in st.session_state:
    st.session_state.classify_process = None
if "classify_output" not in st.session_state:
    st.session_state.classify_output = ""
if "classify_running" not in st.session_state:
    st.session_state.classify_running = False

with tab_run:
    st.header("Запуск классификации")

    # ── Параметры ──
    col_model, col_strategy = st.columns(2)

    with col_model:
        ollama_models = get_ollama_models()
        model_choices = ollama_models + ["Groq (облако)"]
        if not model_choices:
            model_choices = ["Groq (облако)"]
        selected_run_model = st.selectbox(
            "Модель",
            model_choices,
            key="run_model",
            help="Локальные модели Ollama или облачный Groq API",
        )

    with col_strategy:
        strategy = st.radio(
            "Стратегия",
            ["Двухэтапная (рекомендуется)", "Одноэтапная"],
            key="run_strategy",
            help="Двухэтапная: сначала категория, потом подкатегория. Точнее, но в 2 раза больше запросов.",
        )

    col_limit, col_fewshot = st.columns(2)

    with col_limit:
        limit_val = st.number_input(
            "Количество вопросов",
            min_value=0,
            max_value=100000,
            value=0,
            step=100,
            key="run_limit",
            help="0 = все неклассифицированные",
        )

    with col_fewshot:
        few_shot = st.checkbox("Few-shot примеры", value=True, key="run_fewshot",
                               help="Добавить примеры классификации в промпт")

    # ── Инфо: сколько вопросов осталось ──
    is_groq = selected_run_model == "Groq (облако)"
    if not is_groq:
        unclassified = get_unclassified_count(selected_run_model)
        st.info(f"Неклассифицированных вопросов для **{selected_run_model}**: **{unclassified:,}**")

    # ── Собрать команду ──
    def build_command() -> list[str]:
        cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "classify.py"), "--no-dashboard"]
        if is_groq:
            cmd.append("--groq")
        else:
            cmd.extend(["--model", selected_run_model])
        if "Двухэтапная" in strategy:
            cmd.append("--twostage")
        if limit_val > 0:
            cmd.extend(["--limit", str(limit_val)])
        if not few_shot:
            cmd.append("--no-few-shot")
        return cmd

    # ── Показать команду ──
    cmd = build_command()
    cmd_display = " ".join(cmd).replace(sys.executable, "python").replace(str(PROJECT_ROOT) + "\\", "").replace(str(PROJECT_ROOT) + "/", "")
    st.code(cmd_display, language="bash")

    # ── Кнопки управления ──
    col_start, col_stop = st.columns(2)

    with col_start:
        start_clicked = st.button(
            "Запустить классификацию",
            type="primary",
            disabled=st.session_state.classify_running,
            width="stretch",
        )

    with col_stop:
        stop_clicked = st.button(
            "Остановить",
            disabled=not st.session_state.classify_running,
            width="stretch",
        )

    # ── Обработка кнопок ──
    if start_clicked and not st.session_state.classify_running:
        st.session_state.classify_output = ""
        st.session_state.classify_running = True
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(PROJECT_ROOT),
        )
        st.session_state.classify_process = proc

    if stop_clicked and st.session_state.classify_process is not None:
        st.session_state.classify_process.terminate()
        st.session_state.classify_running = False
        st.session_state.classify_output += "\n--- Остановлено пользователем ---\n"

    # ── Live-вывод ──
    if st.session_state.classify_running and st.session_state.classify_process is not None:
        proc = st.session_state.classify_process
        output_area = st.empty()

        # Читаем все доступные строки
        while True:
            retcode = proc.poll()
            line = proc.stdout.readline()
            if line:
                st.session_state.classify_output += line
            if not line and retcode is not None:
                # Процесс завершился
                st.session_state.classify_running = False
                remaining = proc.stdout.read()
                if remaining:
                    st.session_state.classify_output += remaining
                st.session_state.classify_output += f"\n--- Завершено (код: {retcode}) ---\n"
                break
            if not line:
                break

        # Показываем вывод (последние 100 строк)
        lines = st.session_state.classify_output.strip().split("\n")
        display_text = "\n".join(lines[-100:])
        output_area.code(display_text, language="text")

        # Авто-обновление пока процесс работает
        if st.session_state.classify_running:
            time.sleep(1)
            st.rerun()

    elif st.session_state.classify_output:
        lines = st.session_state.classify_output.strip().split("\n")
        display_text = "\n".join(lines[-100:])
        st.code(display_text, language="text")


# ═══════════════ Вкладка 1: Обзор ═════════════════════════════════

with tab1:
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
            st.plotly_chart(category_bar_chart(df_cats), width="stretch")
        with col_pie:
            st.plotly_chart(category_pie_chart(df_cats), width="stretch")
    else:
        st.info("Нет данных для отображения. Запустите классификацию.")

    # История запусков
    runs = get_classification_runs(conn)
    if runs:
        with st.expander("История запусков классификации"):
            df_runs = pd.DataFrame(runs)
            cols_to_show = [c for c in ["started_at", "finished_at", "method", "model_name",
                                         "questions_processed", "questions_failed"] if c in df_runs.columns]
            st.dataframe(df_runs[cols_to_show], width="stretch", hide_index=True)


# ═══════════════ Вкладка 2: Категории ═════════════════════════════

with tab2:
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
                width="stretch",
            )
    else:
        # Drill-down: подкатегории внутри выбранной категории
        cat_id = next(c["id"] for c in all_cats if c["name_ru"] == selected_cat)
        subs = top_subcategories(conn, model_filter, category_id=cat_id, limit=20)
        if subs:
            st.plotly_chart(
                subcategory_bar_chart(pd.DataFrame(subs)),
                width="stretch",
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
                width="stretch",
            )
        else:
            st.info("Нет данных о трендах")

    with col_diff:
        diff = difficulty_by_category(conn, model_filter)
        if diff:
            st.plotly_chart(
                difficulty_bar_chart(pd.DataFrame(diff)),
                width="stretch",
            )
        else:
            st.info("Нет данных о сложности")


# ═══════════════ Вкладка: Тренды ══════════════════════════════════

with tab_trends:
    st.header("Тренды по годам")

    yearly = trends_by_year(conn, model_filter)
    if yearly:
        df_yearly = pd.DataFrame(yearly)

        # Heatmap: год × категория
        st.plotly_chart(year_trends_heatmap(df_yearly), width="stretch")

        # Рост/падение
        growth = category_growth(conn, model_filter)
        if growth:
            df_growth = pd.DataFrame(growth)
            st.plotly_chart(growth_bar_chart(df_growth), width="stretch")

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


# ═══════════════ Вкладка: Рекомендации ═══════════════════════════

with tab_rec:
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
                width="stretch",
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


# ═══════════════ Вкладка: Авторы ═════════════════════════════════

with tab_authors:
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
            width="stretch",
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
                    width="stretch",
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
                    width="stretch",
                    hide_index=True,
                )
            elif profile:
                st.info(f"Недостаточно классифицированных вопросов для автора \"{selected_author}\"")
            else:
                st.info(f"Нет классифицированных вопросов для автора \"{selected_author}\"")
    else:
        st.info("Нет данных об авторах пакетов")


# ═══════════════ Вкладка 3: Сравнение моделей ═════════════════════

with tab3:
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
                    width="stretch",
                )

                # Сравнение уверенности
                st.plotly_chart(
                    model_confidence_comparison(df_common, model_a, model_b),
                    width="stretch",
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
                            width="stretch",
                            hide_index=True,
                            height=400,
                        )


# ═══════════════ Вкладка 4: Уверенность ══════════════════════════

with tab4:
    st.header("Анализ уверенности")

    conf_data = confidence_distribution(conn, model_filter)
    if conf_data:
        df_conf = pd.DataFrame(conf_data)

        col_hist, col_box = st.columns(2)
        with col_hist:
            st.plotly_chart(confidence_histogram(df_conf), width="stretch")
        with col_box:
            st.plotly_chart(confidence_box_by_category(df_conf), width="stretch")

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
                width="stretch",
                hide_index=True,
            )
    else:
        st.info("Нет данных об уверенности")


# ═══════════════ Вкладка 5: Вопросы ══════════════════════════════

with tab5:
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
