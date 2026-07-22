"""Отчёт об упоминаниях советского кино.

Считает раздельно по полям text / answer / comment, показывает и число
упоминаний, и число уникальных вопросов, прикладывает доказательства и
объясняет отбраковку.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Sequence

FIELDS = ("text", "answer", "comment")
FIELD_TITLES = {
    "text": "Текст вопроса",
    "answer": "Ответ",
    "comment": "Комментарий",
}
CONFIDENCE_ORDER = ("high", "medium", "low")


def coverage(conn: sqlite3.Connection) -> Dict[str, int]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN text IS NOT NULL AND TRIM(text) <> '' THEN 1 ELSE 0 END) AS has_text,
            SUM(CASE WHEN answer IS NOT NULL AND TRIM(answer) <> '' THEN 1 ELSE 0 END) AS has_answer,
            SUM(CASE WHEN comment IS NOT NULL AND TRIM(comment) <> '' THEN 1 ELSE 0 END) AS has_comment
        FROM questions
        """
    ).fetchone()
    return dict(row)


def run_info(conn: sqlite3.Connection, run_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM cinema_runs WHERE id = ?", (run_id,)).fetchone()


def totals_by_field(conn: sqlite3.Connection, run_id: int) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT field, confidence, COUNT(*) AS mentions,
               COUNT(DISTINCT question_id) AS questions
        FROM cinema_mentions
        WHERE run_id = ?
        GROUP BY field, confidence
        ORDER BY field, confidence
        """,
        (run_id,),
    ).fetchall()


def top_entities(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    kind: str,
    field: str,
    confidences: Sequence[str] = ("high",),
    limit: int = 25,
) -> List[sqlite3.Row]:
    marks = ",".join("?" for _ in confidences)
    return conn.execute(
        f"""
        SELECT e.key, e.label, e.year, e.based_on, e.types, e.homonym_count, e.wiki_url,
               COUNT(*) AS mentions,
               COUNT(DISTINCT m.question_id) AS questions
        FROM cinema_mentions m
        JOIN cinema_entities e ON e.key = m.entity_key
        WHERE m.run_id = ? AND e.kind = ? AND m.field = ?
          AND m.confidence IN ({marks})
        GROUP BY e.key
        ORDER BY questions DESC, mentions DESC, e.label
        LIMIT ?
        """,
        (run_id, kind, field, *confidences, limit),
    ).fetchall()


def top_entities_overall(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    kind: str,
    confidences: Sequence[str] = ("high",),
    limit: int = 30,
) -> List[sqlite3.Row]:
    marks = ",".join("?" for _ in confidences)
    return conn.execute(
        f"""
        SELECT e.key, e.label, e.year, e.based_on, e.types, e.homonym_count,
               COUNT(DISTINCT m.question_id) AS questions,
               COUNT(*) AS mentions,
               -- Везде вопросы, а не упоминания: смешивать единицы в одной
               -- таблице нельзя, читатель складывает колонки
               COUNT(DISTINCT CASE WHEN m.field = 'text' THEN m.question_id END) AS in_text,
               COUNT(DISTINCT CASE WHEN m.field = 'answer' THEN m.question_id END) AS in_answer,
               COUNT(DISTINCT CASE WHEN m.field = 'comment' THEN m.question_id END) AS in_comment
        FROM cinema_mentions m
        JOIN cinema_entities e ON e.key = m.entity_key
        WHERE m.run_id = ? AND e.kind = ? AND m.confidence IN ({marks})
        GROUP BY e.key
        ORDER BY questions DESC, mentions DESC, e.label
        LIMIT ?
        """,
        (run_id, kind, *confidences, limit),
    ).fetchall()


def top_people(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    confidences: Sequence[str] = ("high",),
    limit: int = 30,
) -> List[sqlite3.Row]:
    """Люди кино с разбивкой «в киноконтексте» / «вне его».

    Разбивка нужна потому, что половина советских актёров знаменита не только
    кино: Высоцкого чаще вспоминают как барда, Никулина — как клоуна.
    """
    marks = ",".join("?" for _ in confidences)
    return conn.execute(
        f"""
        SELECT e.key, e.label, e.roles, e.homonym_count, e.wiki_url,
               COUNT(DISTINCT m.question_id) AS questions,
               COUNT(*) AS mentions,
               -- Всё в вопросах, а не в упоминаниях: иначе колонки не сходятся
               -- с общим числом и получается «104 из 76»
               COUNT(DISTINCT CASE WHEN m.cinema_context = 1 THEN m.question_id END)
                   AS in_cinema,
               COUNT(DISTINCT CASE WHEN m.cinema_context = 0 THEN m.question_id END)
                   AS outside_cinema,
               COUNT(DISTINCT CASE WHEN m.field = 'text' THEN m.question_id END) AS in_text,
               COUNT(DISTINCT CASE WHEN m.field = 'answer' THEN m.question_id END) AS in_answer,
               COUNT(DISTINCT CASE WHEN m.field = 'comment' THEN m.question_id END) AS in_comment
        FROM cinema_mentions m
        JOIN cinema_entities e ON e.key = m.entity_key
        WHERE m.run_id = ? AND e.kind = 'person' AND m.confidence IN ({marks})
        GROUP BY e.key
        ORDER BY questions DESC, mentions DESC, e.label
        LIMIT ?
        """,
        (run_id, *confidences, limit),
    ).fetchall()


def evidence_for(
    conn: sqlite3.Connection,
    run_id: int,
    entity_key: str,
    *,
    limit: int = 3,
    confidences: Sequence[str] = ("high",),
) -> List[sqlite3.Row]:
    marks = ",".join("?" for _ in confidences)
    return conn.execute(
        f"""
        SELECT question_id, field, rule, confidence, matched_text, context, flags
        FROM cinema_mentions
        WHERE run_id = ? AND entity_key = ? AND confidence IN ({marks})
        ORDER BY question_id
        LIMIT ?
        """,
        (run_id, entity_key, *confidences, limit),
    ).fetchall()


def rules_breakdown(conn: sqlite3.Connection, run_id: int) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT rule, confidence, COUNT(*) AS n
        FROM cinema_mentions
        WHERE run_id = ?
        GROUP BY rule, confidence
        ORDER BY n DESC
        """,
        (run_id,),
    ).fetchall()


def rejections_breakdown(conn: sqlite3.Connection, run_id: int) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT reason, COUNT(*) AS n, COUNT(DISTINCT question_id) AS questions
        FROM cinema_rejections
        WHERE run_id = ?
        GROUP BY reason
        ORDER BY n DESC
        """,
        (run_id,),
    ).fetchall()


def rejection_examples(
    conn: sqlite3.Connection, run_id: int, reason: str, *, limit: int = 3
) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.question_id, r.matched_text, r.context, e.label
        FROM cinema_rejections r
        LEFT JOIN cinema_entities e ON e.key = r.entity_key
        WHERE r.run_id = ? AND r.reason = ?
        ORDER BY r.question_id
        LIMIT ?
        """,
        (run_id, reason, limit),
    ).fetchall()


# Сколько советских фильмов должно быть у человека, чтобы попасть в учебный
# список. Отсекает копродукции: Скорсезе снялся в «Анне Павловой», Ален Делон
# и Кристиан Бейл — в одной картине каждый. Учить их ради советского кино
# бессмысленно, хотя в словаре они стоят законно.
MIN_SOVIET_FILMS = 3


def study_targets(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    kind: Optional[str] = None,
    limit: int = 20,
    cinema_context_only: bool = False,
    min_films: int = 0,
    require_full_name: bool = False,
) -> List[sqlite3.Row]:
    """Что учить в первую очередь.

    Порядок — по числу ЛЁГКИХ вопросов (взяли ≥70% команд). Не взять такой
    вопрос — прямая потеря позиции: на трудном вопросе команда теряет вместе
    с полем, на лёгком — в одиночку. Сортировать по сложности было бы ошибкой:
    это список того, на чём сыпятся все.

    cinema_context_only нужен для людей: Евтушенко упомянут в 80 вопросах, но
    лишь в 5 из них речь о кино — учить его как киношника бессмысленно.
    """
    kind_filter = "AND e.kind = ?" if kind else ""
    ctx_filter = "AND m.cinema_context = 1" if cinema_context_only else ""
    params: List = [run_id]
    if kind:
        params.append(kind)
    # Берём с запасом: часть строк отсеется по числу советских фильмов
    params.append(limit * 4)
    rows = conn.execute(
        f"""
        SELECT e.key, e.label, e.kind, e.year, e.roles, e.based_on, e.films,
               COUNT(DISTINCT CASE WHEN s.difficulty_raw <= 3 THEN m.question_id END)
                   AS easy_questions,
               COUNT(DISTINCT CASE WHEN s.difficulty_raw <= 5 THEN m.question_id END)
                   AS medium_questions,
               COUNT(DISTINCT m.question_id) AS questions,
               ROUND(AVG(s.difficulty_raw), 1) AS avg_difficulty,
               SUM(CASE WHEN m.rule LIKE 'person_full_name%' THEN 1 ELSE 0 END)
                   AS full_name_hits
        FROM cinema_mentions m
        JOIN cinema_entities e ON e.key = m.entity_key
        LEFT JOIN question_result_stats s ON s.question_id = m.question_id
        WHERE m.run_id = ? AND m.confidence = 'high' {ctx_filter} {kind_filter}
        GROUP BY e.key
        ORDER BY easy_questions DESC, medium_questions DESC, questions DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    if min_films:
        rows = [
            row
            for row in rows
            if len([f for f in (row["films"] or "").split("|") if f]) >= min_films
        ]
    if require_full_name:
        # Если полное имя человека не написали ни разу, то совпадения по одной
        # его фамилии — почти наверняка про однофамильца. «Анна Стэн» так
        # забирала Стэна Ли, «Лео Мур» — Алана Мура и московский МУР.
        rows = [row for row in rows if (row["full_name_hits"] or 0) > 0]
    return rows[:limit]


def study_evidence(
    conn: sqlite3.Connection, run_id: int, entity_key: str, *, limit: int = 6
) -> List[sqlite3.Row]:
    """Фрагменты вопросов, начиная с самых лёгких: их берут все.

    Показывают не «посмотрите фильм», а что именно спрашивают — реплику,
    песню, деталь.
    """
    return conn.execute(
        """
        SELECT m.question_id, m.field, m.matched_text, m.context,
               ROUND(s.difficulty_raw, 1) AS difficulty,
               ROUND(100.0 * s.take_rate, 0) AS take_pct
        FROM cinema_mentions m
        LEFT JOIN question_result_stats s ON s.question_id = m.question_id
        WHERE m.run_id = ? AND m.entity_key = ? AND m.confidence = 'high'
        ORDER BY CASE WHEN s.difficulty_raw IS NULL THEN 99 ELSE s.difficulty_raw END,
                 m.question_id
        LIMIT ?
        """,
        (run_id, entity_key, limit),
    ).fetchall()


def films_of(conn: sqlite3.Connection, films_csv: Optional[str], limit: int = 4) -> str:
    if not films_csv:
        return ""
    keys = [k for k in films_csv.split("|") if k][:limit]
    if not keys:
        return ""
    marks = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"SELECT label, year FROM cinema_entities WHERE key IN ({marks}) AND kind='film'",
        keys,
    ).fetchall()
    return ", ".join(f"«{r['label']}»" + (f" ({r['year']})" if r["year"] else "") for r in rows)


def build_cheatsheet(
    conn: sqlite3.Connection, run_id: int, *, top_films: int = 20, top_people: int = 20
) -> str:
    """Шпаргалка: что смотреть и что при этом замечать."""
    out: List[str] = []
    a = out.append

    a("# Советское кино: что учить команде\n")
    a("Список собран не по известности фильмов, а по данным базы: чем выше")
    a("позиция, тем больше **лёгких** вопросов (взяли ≥70% команд) на этой теме.")
    a("Логика простая: не взять трудный вопрос — потерять вместе со всеми,")
    a("не взять лёгкий — потерять в одиночку. Учить надо второе.\n")
    a("К каждому пункту — реальные фрагменты вопросов из базы. Они показывают,")
    a("что именно спрашивают: обычно не сюжет, а реплику, песню или деталь.\n")
    a("Колонки: **лёгких** — вопросов, где взяли ≥70% команд; **средних** — ≥50%;")
    a("**всего** — все подтверждённые упоминания.\n")

    for kind, title, note in (
        (
            "person",
            "Часть 1. Кто есть кто",
            "Людей в вопросах больше, чем фильмов. Именно здесь был ваш промах"
            " с Этушем: спрашивали актёра, а не сюжет. Начинать стоит отсюда.",
        ),
        (
            "film",
            "Часть 2. Что смотреть",
            "Порядок просмотра. Двадцать фильмов — это выходные, и они"
            " закрывают большую часть киновопросов базы.",
        ),
    ):
        rows = study_targets(
            conn,
            run_id,
            kind=kind,
            limit=top_people if kind == "person" else top_films,
            # Для людей считаем только упоминания в киноконтексте и требуем
            # реальной кинобиографии: иначе в списке окажутся Евтушенко-поэт
            # и Ален Делон с одной советской картиной
            cinema_context_only=kind == "person",
            min_films=MIN_SOVIET_FILMS if kind == "person" else 0,
            require_full_name=kind == "person",
        )
        if not rows:
            continue
        a(f"## {title}\n")
        a(f"{note}\n")
        a("| # | Кто/что | Лёгких | Средних | Всего |")
        a("|---:|---|---:|---:|---:|")
        for i, row in enumerate(rows, 1):
            name = row["label"] + (f" ({row['year']})" if row["year"] else "")
            a(f"| {i} | {name} | {row['easy_questions']} | {row['medium_questions']} | {row['questions']} |")
        a("")

        a(f"### Что спрашивают\n")
        for row in rows:
            name = row["label"] + (f" ({row['year']})" if row["year"] else "")
            a(f"#### {name}\n")
            details = []
            if kind == "person" and row["roles"]:
                roles = {"actor": "актёр", "director": "режиссёр"}
                details.append(
                    ", ".join(roles.get(r, r) for r in row["roles"].split("|") if r)
                )
            if kind == "film" and row["based_on"]:
                details.append(f"снят по книге «{row['based_on'].split('|')[0]}»")
            if details:
                a(f"_{'; '.join(details)}_\n")
            for ev in study_evidence(conn, run_id, row["key"], limit=5):
                took = (
                    f"взяли {ev['take_pct']:.0f}% команд"
                    if ev["take_pct"] is not None
                    else "статистики нет"
                )
                a(f"- _{took}_, вопрос `{ev['question_id']}` ({FIELD_TITLES.get(ev['field'], ev['field'])}):")
                a(f"  > …{_clip(ev['context'], 260)}…")
            a("")

    a("## Как этим пользоваться\n")
    a("1. Часть 1 — за вечер. Это чистое знание «кто есть кто», и именно такие")
    a("   промахи стоят дороже всего: их берут почти все.")
    a("2. Часть 2 — по фильму за раз, сверху вниз. Смотреть, держа в голове")
    a("   фрагменты вопросов: видно, что цепляют реплики и песни, а не сюжет.")
    a("3. После каждого блока — проверка на самой базе, а не на ощущениях.\n")
    a("Важно: всё советское кино — около 3% базы, примерно 1–2 вопроса на турнир.")
    a("Это не универсальное лекарство, а одна закрываемая дыра. Зато закрываемая")
    a("полностью и проверяемо.\n")

    return "\n".join(out)


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "—"


def _clip(text: str, width: int = 150) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _row_get(row: sqlite3.Row, key: str):
    return row[key] if key in row.keys() else None


def _flag_note(row: sqlite3.Row) -> str:
    """Примечание, по которому видно, чему верить.

    Пользователь просил помечать литературный первоисточник, чтобы самому
    решать, засчитывать упоминание кино или книге.
    """
    notes = []
    if _row_get(row, "based_on"):
        notes.append(f"книжный первоисточник: {row['based_on'].split('|')[0]}")
    types = _row_get(row, "types")
    if types:
        # Для персонажа тип объясняет, что он не «родился» в советском кино:
        # литературный герой, мифический персонаж, персонаж комиксов
        foreign = [
            t
            for t in types.split("|")
            if any(
                marker in t.lower()
                for marker in ("литературн", "миф", "комикс", "оперн", "театральн", "библейск")
            )
        ]
        if foreign:
            notes.append(f"пришёл из: {', '.join(foreign[:2])}")
    if _row_get(row, "homonym_count"):
        notes.append(f"омонимов в Wikidata: {row['homonym_count']}")
    return "; ".join(notes) or "—"


def build_markdown(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    dict_stats: Optional[Dict] = None,
    top_limit: int = 25,
) -> str:
    info = run_info(conn, run_id)
    cov = coverage(conn)
    out: List[str] = []
    a = out.append

    a("# Советское кино в вопросах ЧГК: упоминания фильмов и персонажей\n")
    a(f"**Прогон:** #{run_id}  ")
    if info:
        a(f"**Дата:** {info['finished_at'] or info['started_at']}  ")
        a(f"**Версия словаря:** {info['dict_version']} (хэш `{info['dict_hash']}`)  ")
        a(f"**Просканировано вопросов:** {info['questions_scanned']:,}".replace(",", " ") + "  ")
    a("")
    a("Показатель — не «сколько раз встретилось слово», а «в скольких вопросах есть")
    a("подтверждённое упоминание». У каждого упоминания есть правило, уровень")
    a("уверенности и доказательство: точный фрагмент и контекст.\n")

    # --- Покрытие
    a("## 1. Покрытие исходных данных\n")
    a("Прежде чем читать рейтинги: по какому объёму данных они вообще посчитаны.\n")
    a("| Поле | Заполнено | Доля | Пусто |")
    a("|---|---:|---:|---:|")
    total = cov["total"]
    for field, key in (("Текст вопроса", "has_text"), ("Ответ", "has_answer"), ("Комментарий", "has_comment")):
        filled = cov[key] or 0
        a(f"| {field} | {filled:,} | {_pct(filled, total)} | {total - filled:,} |".replace(",", " "))
    a(f"\nВсего вопросов в базе: **{total:,}**.".replace(",", " "))
    a("")
    a("Комментарий заполнен не у всех вопросов, поэтому рейтинг по комментариям")
    a("считается по меньшей выборке — сравнивать абсолютные числа между полями")
    a("напрямую нельзя.\n")

    # --- Словарь
    if dict_stats:
        a("## 2. Словарь\n")
        a("Источник — Wikidata (снапшот, запросы сохранены в `data/soviet_cinema/dict_meta.json`).\n")
        a("| Показатель | Значение |")
        a("|---|---:|")
        a(f"| Сущностей в словаре | {dict_stats['entities']:,} |".replace(",", " "))
        a(f"| Фильмов | {dict_stats['films']:,} |".replace(",", " "))
        a(f"| Персонажей | {dict_stats['characters']:,} |".replace(",", " "))
        a(f"| Поисковых форм (алиасов) | {dict_stats['aliases']:,} |".replace(",", " "))
        a(f"| Фильмов с омонимами в Wikidata | {dict_stats['films_with_homonyms']:,} |".replace(",", " "))
        a(f"| Фильмов с книжным первоисточником | {dict_stats['films_with_literary_source']:,} |".replace(",", " "))
        a(f"| Записей отбраковано при сборке | {dict_stats['rejected']:,} |".replace(",", " "))
        a("")
        if dict_stats.get("rejected_by_reason"):
            a("Причины отбраковки записей словаря:\n")
            a("| Причина | Записей |")
            a("|---|---:|")
            for reason, n in sorted(
                dict_stats["rejected_by_reason"].items(), key=lambda x: -x[1]
            ):
                a(f"| `{reason}` | {n:,} |".replace(",", " "))
            a("")

    # --- Итоги
    a("## 3. Сколько нашлось\n")
    rows = totals_by_field(conn, run_id)
    a("| Поле | Уверенность | Упоминаний | Вопросов |")
    a("|---|---|---:|---:|")
    for row in rows:
        a(
            f"| {FIELD_TITLES.get(row['field'], row['field'])} | {row['confidence']} | "
            f"{row['mentions']:,} | {row['questions']:,} |".replace(",", " ")
        )
    a("")
    a("Дальше в рейтингах используются только совпадения уровня **high**.")
    a("Medium и low остаются в базе — их можно посмотреть отдельно, но в рейтинг")
    a("они не идут, чтобы не выдавать догадку за факт.\n")

    # --- Рейтинги
    section = 4
    for kind, human in (("film", "фильмы"), ("character", "персонажи")):
        a(f"## {section}. Топ: {human}\n")

        a(f"### {section}.1. Сводно по всем трём полям\n")
        a("Во всех колонках — число вопросов, а не упоминаний. Сумма по полям"
          " может превышать «Вопросов»: один вопрос может упоминать фильм и в"
          " тексте, и в комментарии.\n")
        overall = top_entities_overall(conn, run_id, kind=kind, limit=top_limit)
        if not overall:
            a("_Ничего не найдено._\n")
        else:
            a("| # | Название | Год | Вопросов | Текст | Ответ | Коммент. | Примечание |")
            a("|---:|---|---:|---:|---:|---:|---:|---|")
            for i, row in enumerate(overall, 1):
                a(
                    f"| {i} | {row['label']} | {row['year'] or '—'} | {row['questions']} | "
                    f"{row['in_text']} | {row['in_answer']} | {row['in_comment']} | "
                    f"{_flag_note(row)} |"
                )
            a("")

        for j, field in enumerate(FIELDS, start=2):
            a(f"### {section}.{j}. Только «{FIELD_TITLES[field]}»\n")
            rows = top_entities(conn, run_id, kind=kind, field=field, limit=top_limit)
            if not rows:
                a("_Ничего не найдено._\n")
                continue
            a("| # | Название | Год | Вопросов | Упоминаний | Примечание |")
            a("|---:|---|---:|---:|---:|---|")
            for i, row in enumerate(rows, 1):
                a(
                    f"| {i} | {row['label']} | {row['year'] or '—'} | {row['questions']} | "
                    f"{row['mentions']} | {_flag_note(row)} |"
                )
            a("")
        section += 1

    # --- Люди кино
    a(f"## {section}. Топ: актёры и режиссёры\n")
    a("Здесь два разных вопроса, и их важно не смешивать. **Вопросов** — в"
      " скольких вопросах человек вообще упомянут. **В киноконтексте** — в"
      " скольких из этих упоминаний рядом есть кино. Разница красноречива:"
      " Высоцкого чаще вспоминают как барда, Никулина — как клоуна, и упоминание"
      " человека само по себе не означает упоминания советского кино.\n")
    people = top_people(conn, run_id, limit=top_limit)
    if not people:
        a("_Ничего не найдено._\n")
    else:
        a("| # | Имя | Роль | Вопросов | В киноконтексте | Вне его | Текст | Ответ | Коммент. | Примечание |")
        a("|---:|---|---|---:|---:|---:|---:|---:|---:|---|")
        for i, row in enumerate(people, 1):
            roles = {"actor": "актёр", "director": "режиссёр"}
            role = ", ".join(
                roles.get(r, r) for r in (row["roles"] or "").split("|") if r
            ) or "—"
            note = (
                f"тёзок в Wikidata: {row['homonym_count']}"
                if row["homonym_count"]
                else "—"
            )
            a(
                f"| {i} | {row['label']} | {role} | {row['questions']} | "
                f"{row['in_cinema']} | {row['outside_cinema']} | {row['in_text']} | "
                f"{row['in_answer']} | {row['in_comment']} | {note} |"
            )
        a("")
    section += 1

    # --- Доказательства
    a(f"## {section}. Доказательства\n")
    a("Для верхних позиций — конкретные совпадения: ID вопроса, поле, правило, фрагмент.\n")
    for kind, human in (
        ("film", "Фильмы"),
        ("character", "Персонажи"),
        ("person", "Актёры и режиссёры"),
    ):
        top = top_entities_overall(conn, run_id, kind=kind, limit=10)
        if not top:
            continue
        a(f"### {human}\n")
        for row in top:
            year = f" ({row['year']})" if row["year"] else ""
            a(f"**{row['label']}**{year} — {row['questions']} вопросов\n")
            for ev in evidence_for(conn, run_id, row["key"], limit=3):
                a(
                    f"- Вопрос `{ev['question_id']}`, поле `{ev['field']}`, "
                    f"правило `{ev['rule']}`, нашлось: «{_clip(ev['matched_text'], 60)}»"
                )
                a(f"  > …{_clip(ev['context'])}…")
            a("")
    section += 1

    # --- Ограничения
    a(f"## {section}. Известные ограничения\n")
    a("Чего этот отчёт не умеет — чтобы верхние строчки не читались как истина"
      " в последней инстанции.\n")
    a("1. **Словарь персонажей беден.** В Wikidata у советских фильмов размечено"
      " всего около двух тысяч персонажей, и 671 из них — реальные люди"
      " (Ленин, Эйнштейн), которых пришлось исключить. Поэтому рейтинг"
      " персонажей заведомо неполон: многих известных героев в словаре просто нет.")
    a("2. **Лемматизация склеивает похожие названия.** «Происхождение видов»"
      " Дарвина и фильм «Происхождение вида» (1966) дают одну и ту же лемму."
      " Защита по омонимам тут не срабатывает: в Wikidata это разные строки.")
    a("3. **Мусор в альтернативных названиях Wikidata.** У фильма «Смотри в корень»"
      " (1930) альтернативное название — «Крестовый поход». Такие формы"
      " отключаются вручную в `data/soviet_cinema/hints.json`.")
    a("4. **Персонажи экранизаций считаются как киноперсонажи.** Шерлок Холмс и"
      " д’Артаньян попадают в рейтинг, потому что снимались советские"
      " экранизации. В колонке «Примечание» видно, что персонаж пришёл из книги —"
      " решение, считать ли это упоминанием кино, остаётся за читателем.")
    a("5. **Считается упоминание, а не тема вопроса.** Фильм, названный в"
      " комментарии вскользь, весит столько же, сколько фильм, которому"
      " посвящён весь вопрос.")
    a("6. **«В киноконтексте» — это про кино вообще, а не про советское кино.**"
      " Шон Коннери играл в «Красной палатке» (1969), Орсон Уэллс — в «Ватерлоо»"
      " (1970), Куросава снял «Дерсу Узала» (1975), Скорсезе снялся в «Анне"
      " Павловой» (1983) — все они законно попали в словарь советского кино."
      " Но в вопросах ЧГК их вспоминают из-за совсем других фильмов, и по"
      " колонке «в киноконтексте» это неотличимо.")
    a("7. **Человек в словаре — не всегда киношник по сути.** Высоцкий, Окуджава,"
      " Евтушенко, Пугачёва снимались, поэтому Wikidata числит их актёрами."
      " Колонка «вне киноконтекста» показывает правду: Евтушенко упомянут в 80"
      " вопросах, и лишь 5 из них про кино.\n")
    a("Если в рейтинге видно что-то заведомо ложное, это лечится не правкой"
      " снапшота, а строкой в `hints.json` (`blocked_aliases` или"
      " `exclude_entities`) и повторным прогоном.\n")
    section += 1

    # --- Правила
    a(f"## {section}. Какими правилами получены упоминания\n")
    a("| Правило | Уверенность | Совпадений |")
    a("|---|---|---:|")
    for row in rules_breakdown(conn, run_id):
        a(f"| `{row['rule']}` | {row['confidence']} | {row['n']:,} |".replace(",", " "))
    a("")
    section += 1

    # --- Отбраковка
    a(f"## {section}. Что отбраковано и почему\n")
    a("Это словарные совпадения, которые НЕ засчитаны. Они показывают, от чего")
    a("именно защищает пайплайн.\n")
    a("| Причина | Совпадений | Вопросов |")
    a("|---|---:|---:|")
    for row in rejections_breakdown(conn, run_id):
        a(f"| `{row['reason']}` | {row['n']:,} | {row['questions']:,} |".replace(",", " "))
    a("")
    a("Примеры:\n")
    for row in rejections_breakdown(conn, run_id):
        examples = rejection_examples(conn, run_id, row["reason"], limit=2)
        if not examples:
            continue
        a(f"**`{row['reason']}`**\n")
        for ex in examples:
            a(
                f"- Вопрос `{ex['question_id']}`: «{_clip(ex['matched_text'], 50)}» "
                f"→ словарь: {ex['label'] or '?'}"
            )
            a(f"  > …{_clip(ex['context'], 130)}…")
        a("")

    return "\n".join(out)
