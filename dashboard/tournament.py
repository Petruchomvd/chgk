"""Страница подготовки к турниру IQ ПФО — Саранск."""

import time
from pathlib import Path
from typing import List, Optional

import pandas as pd
import streamlit as st

from dashboard.components import (
    CATEGORY_COLORS,
    author_radar_chart,
    category_bar_chart,
    category_pie_chart,
    comparison_bar_chart,
)
from dashboard.db_queries import (
    author_categories,
    count_search_results,
    get_all_categories,
    get_available_models,
    search_questions,
    top_categories,
    tournament_combined_categories,
    tournament_per_author_stats,
)
from dashboard.training_queries import (
    count_available_by_category,
    count_available_random,
    get_subcategories_for_categories,
    get_training_questions_by_category,
    get_training_questions_random,
)

# ── Пресет авторов турнира ────────────────────────────────────────

IQ_PFO_AUTHORS = [
    "Дмитрий Соловьёв",
    "Николай Федоренко",
    "Николай Быстров",
    "Евгения Ширяева",
    "Дарья Коровкина",
    "Павел Гонтарь",
    "Юрий Калякин",
    "Борис Белозёров",
    "Рахматулла Овезов",
    "Ариф Багапов",
]

TOURNAMENT_NAME = "IQ ПФО"

ss = st.session_state


# ══════════════════════════════════════════════════════════════════
#  Таб 1: Обзор
# ══════════════════════════════════════════════════════════════════

def _tab_overview(conn, model_filter):
    stats = tournament_per_author_stats(conn, IQ_PFO_AUTHORS, model_filter)
    total_q = sum(s["total"] for s in stats)
    total_c = sum(s["classified"] for s in stats)
    pct = round(100 * total_c / total_q, 1) if total_q > 0 else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Вопросов", f"{total_q:,}")
    col2.metric("Классифицировано", f"{total_c:,}")
    col3.metric("Покрытие", f"{pct}%")
    col4.metric("Авторов", len(IQ_PFO_AUTHORS))

    if pct < 100:
        unclassified = total_q - total_c
        st.warning(f"{unclassified} вопросов ещё не классифицированы. "
                   "Запустите `python scripts/classify.py --provider openrouter --author <имя>`")

    # Таблица авторов
    st.subheader("Авторы турнира")
    df = pd.DataFrame(stats)
    df.columns = ["Автор", "Вопросов", "Классиф.", "Покрытие %"]
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Распределение по категориям
    cats = tournament_combined_categories(conn, IQ_PFO_AUTHORS, model_filter)
    if cats:
        st.subheader("Тематическое распределение")
        df_cats = pd.DataFrame(cats)
        col_bar, col_pie = st.columns(2)
        with col_bar:
            st.plotly_chart(category_bar_chart(df_cats), use_container_width=True)
        with col_pie:
            st.plotly_chart(category_pie_chart(df_cats), use_container_width=True)
    else:
        st.info("Нет данных о классификации. Сначала классифицируйте вопросы авторов.")


# ══════════════════════════════════════════════════════════════════
#  Таб 2: Профили авторов
# ══════════════════════════════════════════════════════════════════

def _tab_profiles(conn, model_filter):
    author = st.selectbox("Автор", IQ_PFO_AUTHORS, key="tournament_author_profile")

    data = author_categories(conn, author, model_filter)
    if not data or sum(d["count"] for d in data) < 3:
        st.info(f"Недостаточно классифицированных данных для {author}")
        return

    st.plotly_chart(author_radar_chart(data, author), use_container_width=True)

    total = sum(d["count"] for d in data)
    rows = []
    for d in sorted(data, key=lambda x: x["count"], reverse=True):
        rows.append({
            "Категория": d["category"],
            "Вопросов": d["count"],
            "Доля %": round(100 * d["count"] / total, 1),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════
#  Таб 3: Сравнение с глобальным
# ══════════════════════════════════════════════════════════════════

def _tab_comparison(conn, model_filter):
    t_cats = tournament_combined_categories(conn, IQ_PFO_AUTHORS, model_filter)
    g_cats = top_categories(conn, model_filter)

    if not t_cats:
        st.info("Нет данных о классификации авторов турнира.")
        return

    st.plotly_chart(
        comparison_bar_chart(t_cats, g_cats, "Авторы турнира", "Все вопросы"),
        use_container_width=True,
    )

    # Дельта-таблица
    g_pct = {d["category"]: d["pct"] for d in g_cats}
    rows = []
    for d in t_cats:
        cat = d["category"]
        t_val = d["pct"]
        g_val = g_pct.get(cat, 0)
        delta = round(t_val - g_val, 1)
        rows.append({
            "Категория": cat,
            "Турнир %": t_val,
            "Глобально %": g_val,
            "Разница": f"{delta:+.1f}",
        })
    rows.sort(key=lambda x: abs(float(x["Разница"])), reverse=True)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════
#  Таб 4: Тренировка
# ══════════════════════════════════════════════════════════════════

def _tq_reset():
    """Сбросить турнирный квиз."""
    for key in [k for k in ss.keys() if k.startswith("tq_")]:
        del ss[key]


def _tq_fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _tab_training_config(conn):
    """Экран настройки тренировки внутри турнирного таба."""
    mode = st.radio(
        "Режим", ["По категории", "Случайный микс"],
        horizontal=True, key="tq_mode_radio",
    )

    category_ids = None
    subcategory_ids = None
    model_filter = None

    if mode == "По категории":
        all_cats = get_all_categories(conn)
        cat_map = {c["name_ru"]: c["id"] for c in all_cats}
        selected_cats = st.multiselect(
            "Категории", list(cat_map.keys()),
            default=list(cat_map.keys()), key="tq_categories",
        )
        category_ids = [cat_map[c] for c in selected_cats]

        if category_ids:
            subs = get_subcategories_for_categories(conn, category_ids)
            sub_labels = {f"{s['category_name']} → {s['name_ru']}": s["id"] for s in subs}
            selected_subs = st.multiselect(
                "Подкатегории (все, если не выбрано)", list(sub_labels.keys()),
                key="tq_subcategories",
            )
            if selected_subs:
                subcategory_ids = [sub_labels[s] for s in selected_subs]

        models = get_available_models(conn)
        model_options = ["Все модели"] + models
        model_sel = st.selectbox("Модель классификации", model_options, key="tq_model")
        if model_sel != "Все модели":
            model_filter = model_sel

    st.caption(f"Авторы: {', '.join(IQ_PFO_AUTHORS[:3])} и ещё {len(IQ_PFO_AUTHORS) - 3}")

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        use_difficulty = st.checkbox("Фильтр по сложности", key="tq_use_diff")
        difficulty_range = None
        if use_difficulty:
            difficulty_range = st.slider("Сложность", 0.0, 10.0, (2.0, 8.0), 0.5, key="tq_diff")
    with col2:
        num_questions = st.number_input("Количество вопросов", 1, 50, 10, key="tq_num")
        seed_input = st.text_input("Seed (пусто = случайный)", "", key="tq_seed")
        seed = int(seed_input) if seed_input.strip().isdigit() else None

    if mode == "По категории":
        available = count_available_by_category(
            conn, category_ids, subcategory_ids, model_filter,
            difficulty_range, author_filters=IQ_PFO_AUTHORS,
        )
    else:
        available = count_available_random(
            conn, difficulty_range, author_filters=IQ_PFO_AUTHORS,
        )

    st.info(f"Доступно вопросов: **{available:,}**")
    if available == 0:
        st.warning("Нет вопросов. Классифицируйте вопросы авторов или расширьте фильтры.")
        return

    actual_count = min(num_questions, available)

    if st.button("Начать тренировку", type="primary", use_container_width=True, key="tq_start"):
        if mode == "По категории":
            questions = get_training_questions_by_category(
                conn, category_ids, subcategory_ids, model_filter,
                difficulty_range, actual_count, seed,
                author_filters=IQ_PFO_AUTHORS,
            )
        else:
            questions = get_training_questions_random(
                conn, difficulty_range, actual_count, seed,
                author_filters=IQ_PFO_AUTHORS,
            )

        if not questions:
            st.error("Не удалось загрузить вопросы.")
            return

        ss.tq_active = True
        ss.tq_finished = False
        ss.tq_questions = questions
        ss.tq_index = 0
        ss.tq_phase = "answering"
        ss.tq_user_answer = ""
        ss.tq_results = []
        ss.tq_start_time = time.time()
        st.rerun()


def _tab_training_quiz(conn):
    """Экран вопроса в турнирном квизе."""
    questions = ss.tq_questions
    idx = ss.tq_index
    total = len(questions)
    q = questions[idx]

    col_title, col_abort = st.columns([5, 1])
    with col_title:
        st.markdown(f"### Вопрос {idx + 1} / {total}")
    with col_abort:
        if st.button("Прервать", key="tq_abort"):
            ss.tq_finished = True
            ss.tq_active = False
            st.rerun()

    st.progress((idx + 1) / total)

    meta = []
    if q.get("category"):
        label = f"{q['category']} → {q['subcategory']}" if q.get("subcategory") else q["category"]
        meta.append(label)
    if q.get("pack_difficulty") is not None:
        meta.append(f"Сложность: {q['pack_difficulty']}")
    if q.get("pack_title"):
        meta.append(q["pack_title"])
    if meta:
        st.caption(" · ".join(meta))

    if q.get("razdatka_text"):
        st.info(f"**Раздатка:** {q['razdatka_text']}")
    if q.get("razdatka_pic"):
        pic = q["razdatka_pic"]
        if not pic.startswith("http"):
            pic = f"https://gotquestions.online{pic}"
        st.image(pic, caption="Раздатка")

    with st.container(border=True):
        st.markdown(q["text"])

    elapsed = time.time() - ss.tq_start_time
    st.caption(f"⏱ {_tq_fmt_time(elapsed)}")

    if ss.tq_phase == "answering":
        user_answer = st.text_input("Ваш ответ", key=f"tq_answer_{idx}", placeholder="Введите ответ...")
        if st.button("Проверить", type="primary", use_container_width=True, key=f"tq_check_{idx}"):
            ss.tq_user_answer = user_answer
            ss.tq_phase = "revealed"
            st.rerun()
    else:
        st.markdown(f"**Ваш ответ:** «{ss.tq_user_answer}»")
        st.success(f"**Правильный ответ:** {q['answer']}")
        if q.get("zachet"):
            st.markdown(f"**Зачёт:** {q['zachet']}")
        if q.get("nezachet"):
            st.markdown(f"**Незачёт:** {q['nezachet']}")
        if q.get("comment"):
            with st.expander("Комментарий", expanded=True):
                st.markdown(q["comment"])
        if q.get("source"):
            st.caption(f"Источник: {q['source']}")
        if q.get("pack_link"):
            st.markdown(f"[Открыть на сайте]({q['pack_link']})")

        col_knew, col_didnt = st.columns(2)
        with col_knew:
            if st.button("Знал ✅", type="primary", use_container_width=True, key=f"tq_knew_{idx}"):
                _tq_record(True)
        with col_didnt:
            if st.button("Не знал ❌", use_container_width=True, key=f"tq_didnt_{idx}"):
                _tq_record(False)


def _tq_record(knew: bool):
    q = ss.tq_questions[ss.tq_index]
    elapsed = time.time() - ss.tq_start_time

    ss.tq_results.append({
        "question_id": q["id"],
        "user_answer": ss.tq_user_answer,
        "correct_answer": q["answer"],
        "knew": knew,
        "time_seconds": elapsed,
        "category": q.get("category", "—"),
    })

    ss.tq_index += 1
    ss.tq_phase = "answering"
    ss.tq_user_answer = ""
    ss.tq_start_time = time.time()

    if ss.tq_index >= len(ss.tq_questions):
        ss.tq_finished = True
        ss.tq_active = False
    st.rerun()


def _tab_training_results():
    """Экран результатов турнирного квиза."""
    st.subheader("Результаты тренировки")

    results = ss.get("tq_results", [])
    if not results:
        st.info("Нет ответов.")
        if st.button("Новая тренировка", type="primary", key="tq_new_empty"):
            _tq_reset()
            st.rerun()
        return

    total = len(results)
    correct = sum(1 for r in results if r["knew"])
    pct = round(100 * correct / total) if total > 0 else 0
    times = [r["time_seconds"] for r in results]
    total_time = sum(times)
    avg_time = total_time / len(times) if times else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("Результат", f"{correct}/{total} ({pct}%)")
    col2.metric("Среднее время", _tq_fmt_time(avg_time))
    col3.metric("Общее время", _tq_fmt_time(total_time))

    st.progress(pct / 100)

    from collections import Counter
    cat_correct = Counter()
    cat_total = Counter()
    for r in results:
        cat = r.get("category", "—")
        cat_total[cat] += 1
        if r["knew"]:
            cat_correct[cat] += 1

    if len(cat_total) > 1:
        st.markdown("---")
        st.markdown("**По категориям:**")
        for cat in sorted(cat_total.keys()):
            c = cat_correct[cat]
            t = cat_total[cat]
            p = round(100 * c / t) if t > 0 else 0
            st.markdown(f"- **{cat}:** {c}/{t} ({p}%)")

    st.markdown("---")
    for i, r in enumerate(results):
        icon = "✅" if r["knew"] else "❌"
        user_ans = r.get("user_answer", "")
        correct_ans = r.get("correct_answer", "")
        detail = f"«{user_ans}» → {correct_ans}" if user_ans else correct_ans
        cat = r.get("category", "")
        cat_str = f" — {cat}" if cat and cat != "—" else ""
        st.markdown(f"{icon} **{i+1}.** {detail} — {_tq_fmt_time(r['time_seconds'])}{cat_str}")

    st.markdown("---")
    if st.button("Новая тренировка", type="primary", use_container_width=True, key="tq_new"):
        _tq_reset()
        st.rerun()


def _tab_training(conn):
    """Роутер тренировки в турнирном табе."""
    if ss.get("tq_finished"):
        _tab_training_results()
    elif ss.get("tq_active"):
        _tab_training_quiz(conn)
    else:
        _tab_training_config(conn)


# ══════════════════════════════════════════════════════════════════
#  Таб 5: Вопросы
# ══════════════════════════════════════════════════════════════════

def _tab_questions(conn, model_filter):
    col_search, col_cat = st.columns([3, 1])
    with col_search:
        search_text = st.text_input(
            "Поиск по тексту", placeholder="Ключевое слово...", key="tq_search",
        )
    with col_cat:
        all_cats = get_all_categories(conn)
        cat_options = ["Все"] + [c["name_ru"] for c in all_cats]
        cat_sel = st.selectbox("Категория", cat_options, key="tq_cat_filter")

    cat_id = None
    if cat_sel != "Все":
        cat_id = next(c["id"] for c in all_cats if c["name_ru"] == cat_sel)

    PAGE_SIZE = 30
    total_results = count_search_results(
        conn, model_filter, search_text, cat_id,
        author_filters=IQ_PFO_AUTHORS,
    )
    total_pages = max(1, (total_results + PAGE_SIZE - 1) // PAGE_SIZE)

    page_num = st.number_input(
        "Страница", 1, total_pages, 1, key="tq_page",
    )
    offset = (page_num - 1) * PAGE_SIZE

    st.caption(f"Найдено: {total_results:,} | Страница {page_num} из {total_pages}")

    questions = search_questions(
        conn, model_filter, search_text, cat_id,
        limit=PAGE_SIZE, offset=offset,
        author_filters=IQ_PFO_AUTHORS,
    )

    if questions:
        for q in questions:
            cat_label = q.get("category") or "—"
            sub_label = q.get("subcategory") or ""
            conf = q.get("confidence")
            conf_str = f" ({conf:.0%})" if conf else ""
            model_str = f" [{q.get('model_name', '')}]" if q.get("model_name") else ""

            header = f"**#{q['id']}** | {cat_label}"
            if sub_label:
                header += f" → {sub_label}"
            header += f"{conf_str}{model_str}"

            with st.expander(header):
                st.markdown(f"**Вопрос:** {q['text']}")
                st.markdown(f"**Ответ:** {q['answer']}")
                if q.get("comment"):
                    st.markdown(f"**Комментарий:** {q['comment']}")
                if q.get("pack_title"):
                    st.markdown(f"**Пакет:** {q['pack_title']}")
                if q.get("pack_difficulty"):
                    st.markdown(f"**Сложность:** {q['pack_difficulty']}")
    else:
        st.info("Ничего не найдено")


# ══════════════════════════════════════════════════════════════════
#  Главная точка входа
# ══════════════════════════════════════════════════════════════════

def render_tournament_page(conn, model_filter, project_root: Path):
    st.header(f"Турнир {TOURNAMENT_NAME} — Саранск")

    tab_overview, tab_profiles, tab_compare, tab_train, tab_questions = st.tabs([
        "Обзор", "Профили авторов", "Сравнение", "Тренировка", "Вопросы",
    ])

    with tab_overview:
        _tab_overview(conn, model_filter)
    with tab_profiles:
        _tab_profiles(conn, model_filter)
    with tab_compare:
        _tab_comparison(conn, model_filter)
    with tab_train:
        _tab_training(conn)
    with tab_questions:
        _tab_questions(conn, model_filter)
