"""Тренировочный режим — интерактивный квиз на вопросах ЧГК."""

import time
from pathlib import Path

import streamlit as st

from dashboard.db_queries import all_authors_sorted, get_all_categories, get_available_models
from dashboard.training_queries import (
    count_available_by_category,
    count_available_gentleman,
    count_available_random,
    get_subcategories_for_categories,
    get_training_questions_by_category,
    get_training_questions_gentleman,
    get_training_questions_random,
)

GENTLEMAN_CATEGORIES = ["Люди", "Места", "Произведения", "Наука", "Выражения", "Числа"]

ss = st.session_state


def _reset_quiz():
    """Сбросить все quiz_* ключи."""
    for key in [k for k in ss.keys() if k.startswith("quiz_")]:
        del ss[key]


def _fmt_time(seconds: float) -> str:
    """Отформатировать секунды в M:SS."""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


# ══════════════════════════════════════════════════════════════════
#  Экран 1: Настройка
# ══════════════════════════════════════════════════════════════════

def _render_config(conn, project_root: Path):
    st.header("Тренировка")

    mode = st.radio(
        "Режим",
        ["По категории", "Джентльменский набор", "Случайный микс"],
        horizontal=True,
    )

    data_dir = project_root / "data" / "gentleman_set"

    # --- Режим-специфичные настройки ---
    category_ids = None
    subcategory_ids = None
    model_filter = None
    gentleman_cat = None

    if mode == "По категории":
        all_cats = get_all_categories(conn)
        cat_map = {c["name_ru"]: c["id"] for c in all_cats}
        selected_cats = st.multiselect(
            "Категории",
            list(cat_map.keys()),
            default=list(cat_map.keys()),
            key="train_categories",
        )
        category_ids = [cat_map[c] for c in selected_cats]

        if category_ids:
            subs = get_subcategories_for_categories(conn, category_ids)
            sub_labels = {f"{s['category_name']} → {s['name_ru']}": s["id"] for s in subs}
            selected_subs = st.multiselect(
                "Подкатегории (все, если не выбрано)", list(sub_labels.keys()),
                key="train_subcategories",
            )
            if selected_subs:
                subcategory_ids = [sub_labels[s] for s in selected_subs]

        models = get_available_models(conn)
        model_options = ["Все модели"] + models
        model_sel = st.selectbox("Модель классификации", model_options, key="train_model")
        if model_sel != "Все модели":
            model_filter = model_sel

    elif mode == "Джентльменский набор":
        gentleman_options = ["Все"] + GENTLEMAN_CATEGORIES
        gentleman_sel = st.selectbox("Категория джентльменского набора", gentleman_options, key="train_gentleman_cat")
        if gentleman_sel != "Все":
            gentleman_cat = gentleman_sel

        if not (data_dir / "top_answers.json").exists():
            st.warning("Данные джентльменского набора не найдены. Запустите `python scripts/analyze_answers.py`.")
            return

    # --- Фильтр по автору (для По категории и Случайный микс) ---
    author_filter = None
    if mode != "Джентльменский набор":
        author_list = ["Все авторы"] + all_authors_sorted(conn)
        author_sel = st.selectbox("Автор вопроса", author_list, key="train_author")
        if author_sel != "Все авторы":
            author_filter = author_sel

    # --- Общие настройки ---
    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        use_difficulty = st.checkbox("Фильтр по сложности пакета")
        difficulty_range = None
        if use_difficulty:
            difficulty_range = st.slider("Диапазон сложности", 0.0, 10.0, (2.0, 8.0), 0.5)

    with col2:
        num_questions = st.number_input("Количество вопросов", 1, 50, 10)
        seed_input = st.text_input("Seed (пусто = случайный)", "")
        seed = int(seed_input) if seed_input.strip().isdigit() else None

    # --- Счётчик доступных вопросов ---
    available = 0
    if mode == "По категории":
        available = count_available_by_category(
            conn, category_ids, subcategory_ids, model_filter, difficulty_range,
            author_filter,
        )
    elif mode == "Джентльменский набор":
        available = count_available_gentleman(data_dir, gentleman_cat)
    else:
        available = count_available_random(conn, difficulty_range, author_filter)

    st.info(f"Доступно вопросов: **{available:,}**")

    if available == 0:
        st.warning("Нет вопросов по выбранным фильтрам. Попробуйте расширить выбор.")
        return

    actual_count = min(num_questions, available)
    if actual_count < num_questions:
        st.caption(f"Будет загружено {actual_count} из {num_questions} запрошенных")

    # --- Кнопка старта ---
    if st.button("Начать тренировку", type="primary", use_container_width=True):
        questions = []
        if mode == "По категории":
            questions = get_training_questions_by_category(
                conn, category_ids, subcategory_ids, model_filter,
                difficulty_range, actual_count, seed, author_filter,
            )
        elif mode == "Джентльменский набор":
            questions = get_training_questions_gentleman(
                conn, data_dir, gentleman_cat,
                difficulty_range, actual_count, seed,
            )
        else:
            questions = get_training_questions_random(
                conn, difficulty_range, actual_count, seed, author_filter,
            )

        if not questions:
            st.error("Не удалось загрузить вопросы.")
            return

        ss.quiz_active = True
        ss.quiz_finished = False
        ss.quiz_mode = mode
        ss.quiz_questions = questions
        ss.quiz_index = 0
        ss.quiz_phase = "answering"
        ss.quiz_user_answer = ""
        ss.quiz_results = []
        ss.quiz_q_start_time = time.time()
        ss.quiz_config = {
            "mode": mode,
            "count": len(questions),
            "seed": seed,
        }
        st.rerun()


# ══════════════════════════════════════════════════════════════════
#  Экран 2: Вопрос
# ══════════════════════════════════════════════════════════════════

def _render_quiz(conn):
    questions = ss.quiz_questions
    idx = ss.quiz_index
    total = len(questions)
    q = questions[idx]

    # --- Заголовок + прогресс ---
    col_title, col_abort = st.columns([5, 1])
    with col_title:
        st.markdown(f"### Вопрос {idx + 1} / {total}")
    with col_abort:
        if st.button("Прервать"):
            ss.quiz_finished = True
            ss.quiz_active = False
            st.rerun()

    st.progress((idx + 1) / total)

    # --- Метаданные ---
    meta = []
    cat = q.get("category")
    sub = q.get("subcategory")
    if cat:
        label = f"{cat} → {sub}" if sub else cat
        meta.append(label)
    diff = q.get("pack_difficulty")
    if diff is not None:
        meta.append(f"Сложность: {diff}")
    pack = q.get("pack_title")
    if pack:
        meta.append(pack)
    q_authors = q.get("authors")
    if q_authors:
        try:
            import json
            alist = json.loads(q_authors)
            if isinstance(alist, list):
                names = [a["name"] for a in alist if isinstance(a, dict) and "name" in a]
                if names:
                    meta.append(f"Автор: {', '.join(names)}")
        except (json.JSONDecodeError, TypeError):
            meta.append(f"Автор: {q_authors}")
    if meta:
        st.caption(" · ".join(meta))

    # --- Раздатка ---
    razdatka_text = q.get("razdatka_text")
    razdatka_pic = q.get("razdatka_pic")
    if razdatka_text:
        st.info(f"**Раздатка:** {razdatka_text}")
    if razdatka_pic:
        pic_url = razdatka_pic if razdatka_pic.startswith("http") else f"https://gotquestions.online{razdatka_pic}"
        st.image(pic_url, caption="Раздатка")

    # --- Текст вопроса ---
    with st.container(border=True):
        st.markdown(q["text"])

    # --- Таймер ---
    elapsed = time.time() - ss.quiz_q_start_time
    st.caption(f"⏱ {_fmt_time(elapsed)}")

    # --- Фаза: ввод ответа ---
    if ss.quiz_phase == "answering":
        user_answer = st.text_input(
            "Ваш ответ",
            key=f"answer_input_{idx}",
            placeholder="Введите ответ...",
        )

        if st.button("Проверить", type="primary", use_container_width=True):
            ss.quiz_user_answer = user_answer
            ss.quiz_phase = "revealed"
            st.rerun()

    # --- Фаза: показ ответа ---
    else:
        st.markdown(f"**Ваш ответ:** «{ss.quiz_user_answer}»")

        st.success(f"**Правильный ответ:** {q['answer']}")
        zachet = q.get("zachet")
        nezachet = q.get("nezachet")
        if zachet:
            st.markdown(f"**Зачёт:** {zachet}")
        if nezachet:
            st.markdown(f"**Незачёт:** {nezachet}")
        comment = q.get("comment")
        if comment:
            with st.expander("Комментарий", expanded=True):
                st.markdown(comment)
        source = q.get("source")
        if source:
            st.caption(f"Источник: {source}")
        pack_link = q.get("pack_link")
        if pack_link:
            st.markdown(f"[Открыть на сайте]({pack_link})")

        col_knew, col_didnt = st.columns(2)
        with col_knew:
            if st.button("Знал ✅", type="primary", use_container_width=True):
                _record_and_advance(True)
        with col_didnt:
            if st.button("Не знал ❌", use_container_width=True):
                _record_and_advance(False)


def _record_and_advance(knew: bool):
    """Записать результат и перейти к следующему вопросу."""
    q = ss.quiz_questions[ss.quiz_index]
    elapsed = time.time() - ss.quiz_q_start_time

    ss.quiz_results.append({
        "question_id": q["id"],
        "user_answer": ss.quiz_user_answer,
        "correct_answer": q["answer"],
        "knew": knew,
        "time_seconds": elapsed,
        "category": q.get("category", "—"),
    })

    ss.quiz_index += 1
    ss.quiz_phase = "answering"
    ss.quiz_user_answer = ""
    ss.quiz_q_start_time = time.time()

    if ss.quiz_index >= len(ss.quiz_questions):
        ss.quiz_finished = True
        ss.quiz_active = False

    st.rerun()


# ══════════════════════════════════════════════════════════════════
#  Экран 3: Результаты
# ══════════════════════════════════════════════════════════════════

def _render_results():
    st.header("Результаты тренировки")

    results = ss.get("quiz_results", [])
    questions = ss.get("quiz_questions", [])

    if not results:
        st.info("Нет ответов для показа.")
        if st.button("Новая тренировка", type="primary"):
            _reset_quiz()
            st.rerun()
        return

    total = len(results)
    correct = sum(1 for r in results if r["knew"])
    pct = round(100 * correct / total) if total > 0 else 0
    times = [r["time_seconds"] for r in results]
    total_time = sum(times)
    avg_time = total_time / len(times) if times else 0

    # --- KPI ---
    col1, col2, col3 = st.columns(3)
    col1.metric("Результат", f"{correct}/{total} ({pct}%)")
    col2.metric("Среднее время", f"{_fmt_time(avg_time)}")
    col3.metric("Общее время", f"{_fmt_time(total_time)}")

    st.progress(pct / 100)

    # --- По категориям ---
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
        st.subheader("По категориям")
        for cat in sorted(cat_total.keys()):
            c = cat_correct[cat]
            t = cat_total[cat]
            p = round(100 * c / t) if t > 0 else 0
            st.markdown(f"- **{cat}:** {c}/{t} ({p}%)")

    # --- Детали ---
    st.markdown("---")
    st.subheader("Детали")

    for i, r in enumerate(results):
        icon = "✅" if r["knew"] else "❌"
        user_ans = r.get("user_answer", "")
        correct_ans = r.get("correct_answer", "")
        time_str = _fmt_time(r["time_seconds"])
        cat = r.get("category", "")

        if user_ans:
            detail = f"«{user_ans}» → {correct_ans}"
        else:
            detail = correct_ans

        parts = [f"{icon} **{i + 1}.** {detail} — {time_str}"]
        if cat and cat != "—":
            parts.append(f"— {cat}")
        st.markdown(" ".join(parts))

    # --- Новая тренировка ---
    st.markdown("---")
    if st.button("Новая тренировка", type="primary", use_container_width=True):
        _reset_quiz()
        st.rerun()


# ══════════════════════════════════════════════════════════════════
#  Роутер
# ══════════════════════════════════════════════════════════════════

def render_training_page(conn, project_root: Path):
    """Главная точка входа — вызывается из app.py."""
    if ss.get("quiz_finished"):
        _render_results()
    elif ss.get("quiz_active"):
        _render_quiz(conn)
    else:
        _render_config(conn, project_root)
