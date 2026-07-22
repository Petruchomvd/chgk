from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_THIS_DIR = Path(__file__).parent
SCHEMA_PATH = _THIS_DIR / "schema.sql"
TG_SCHEMA_PATH = _THIS_DIR / "tg_schema.sql"


def get_connection(
    db_path: str | Path,
    *,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Создать подключение к БД и применить схему."""
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()

    if TG_SCHEMA_PATH.exists():
        tg_sql = TG_SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(tg_sql)
        conn.commit()

    _migrate_question_topics(conn)
    _migrate_question_difficulty(conn)
    _migrate_question_position(conn)
    _migrate_result_position(conn)
    _migrate_cinema_columns(conn)
    return conn


def get_readonly_connection(db_path: str | Path) -> sqlite3.Connection:
    """Открыть существующую БД без DDL и без возможности записи."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_cinema_columns(conn: sqlite3.Connection) -> None:
    """Миграция: колонки для людей кино (актёры и режиссёры).

    CREATE TABLE IF NOT EXISTS не добавляет колонки в уже созданную таблицу,
    поэтому в базах, где cinema_* появились раньше, их нужно дописать.
    """
    additions = [
        ("cinema_entities", "roles", "TEXT"),
        ("cinema_mentions", "cinema_context", "INTEGER DEFAULT 0"),
    ]
    for table, column, decl in additions:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            print(f"[migration] Добавляю колонку {column} в {table}...")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            conn.commit()


def _migrate_question_topics(conn: sqlite3.Connection) -> None:
    """Миграция: UNIQUE(question_id, subcategory_id, method) → включает model_name.

    Позволяет разным моделям классифицировать один и тот же вопрос.
    """
    # Проверяем текущий constraint через SQL таблицы
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='question_topics'"
    ).fetchone()
    if row is None:
        return

    ddl = row[0]
    # Если constraint уже содержит model_name — миграция не нужна
    if "method, model_name)" in ddl or "method,model_name)" in ddl:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qt_model ON question_topics(model_name)"
        )
        conn.commit()
        return

    print("[migration] Обновляю UNIQUE constraint в question_topics (добавляю model_name)...")
    conn.executescript("""
        BEGIN;
        CREATE TABLE question_topics_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id     INTEGER NOT NULL,
            subcategory_id  INTEGER NOT NULL,
            confidence      REAL,
            method          TEXT,
            model_name      TEXT,
            classified_at   TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY (subcategory_id) REFERENCES subcategories(id),
            UNIQUE(question_id, subcategory_id, method, model_name)
        );
        INSERT INTO question_topics_new
            (id, question_id, subcategory_id, confidence, method, model_name, classified_at)
        SELECT id, question_id, subcategory_id, confidence, method, model_name, classified_at
        FROM question_topics;
        DROP TABLE question_topics;
        ALTER TABLE question_topics_new RENAME TO question_topics;
        CREATE INDEX IF NOT EXISTS idx_qt_question ON question_topics(question_id);
        CREATE INDEX IF NOT EXISTS idx_qt_subcategory ON question_topics(subcategory_id);
        CREATE INDEX IF NOT EXISTS idx_qt_model ON question_topics(model_name);
        COMMIT;
    """)
    print("[migration] Готово.")


def _migrate_question_difficulty(conn: sqlite3.Connection) -> None:
    """Миграция: добавить колонку difficulty в questions."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='questions'"
    ).fetchone()
    if row is None:
        return
    if "difficulty" not in row[0]:
        print("[migration] Добавляю колонку difficulty в questions...")
        conn.execute("ALTER TABLE questions ADD COLUMN difficulty REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty)")
    conn.commit()


def _migrate_question_position(conn: sqlite3.Connection) -> None:
    """Миграция: добавить стабильную позицию вопроса внутри пакета."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='questions'"
    ).fetchone()
    if row is None:
        return
    if "position_in_pack" not in row[0]:
        print("[migration] Добавляю position_in_pack в questions...")
        conn.execute("ALTER TABLE questions ADD COLUMN position_in_pack INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_questions_pack_position "
        "ON questions(pack_id, position_in_pack)"
    )
    conn.commit()


def _migrate_result_position(conn: sqlite3.Connection) -> None:
    """Миграция: отделить позицию в маске от позиции на странице пакета."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='question_result_stats'"
    ).fetchone()
    if row is None or "result_position" in row[0]:
        return
    conn.execute(
        "ALTER TABLE question_result_stats ADD COLUMN result_position INTEGER"
    )
    conn.execute(
        "UPDATE question_result_stats SET result_position = position_in_pack "
        "WHERE result_position IS NULL"
    )
    conn.commit()


# --------------- Пакеты ---------------

def upsert_pack(
    conn: sqlite3.Connection,
    data: Dict[str, Any],
    *,
    commit: bool = True,
) -> bool:
    """Вставить или обновить пакет."""
    try:
        conn.execute(
            """INSERT INTO packs
               (id, title, question_count, start_date, end_date,
                published_date, teams_played, difficulty, authors, link,
                parse_status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   title = COALESCE(excluded.title, packs.title),
                   question_count = COALESCE(excluded.question_count, packs.question_count),
                   start_date = COALESCE(excluded.start_date, packs.start_date),
                   end_date = COALESCE(excluded.end_date, packs.end_date),
                   published_date = COALESCE(excluded.published_date, packs.published_date),
                   teams_played = COALESCE(excluded.teams_played, packs.teams_played),
                   difficulty = COALESCE(excluded.difficulty, packs.difficulty),
                   authors = COALESCE(excluded.authors, packs.authors),
                   link = COALESCE(excluded.link, packs.link),
                   parse_status = excluded.parse_status,
                   error_message = COALESCE(excluded.error_message, packs.error_message)""",
            (
                data["id"],
                data.get("title"),
                data.get("question_count"),
                data.get("start_date"),
                data.get("end_date"),
                data.get("published_date"),
                data.get("teams_played"),
                data.get("difficulty"),
                data.get("authors"),
                data.get("link"),
                data.get("parse_status", "parsed"),
                data.get("error_message"),
            ),
        )
        if commit:
            conn.commit()
        return True
    except Exception as e:
        if commit:
            conn.rollback()
        print(f"Ошибка upsert_pack #{data['id']}: {e}")
        return False


def mark_pack_status(
    conn: sqlite3.Connection,
    pack_id: int,
    status: str,
    error: Optional[str] = None,
    *,
    commit: bool = True,
) -> None:
    """Обновить статус парсинга пакета."""
    conn.execute(
        "UPDATE packs SET parse_status = ?, error_message = ? WHERE id = ?",
        (status, error, pack_id),
    )
    if commit:
        conn.commit()


def get_pending_pack_ids(conn: sqlite3.Connection) -> List[int]:
    """Получить ID пакетов, которые ещё не спарсены."""
    rows = conn.execute(
        "SELECT id FROM packs WHERE parse_status = 'pending' ORDER BY id"
    ).fetchall()
    return [r["id"] for r in rows]


def get_parsed_pack_ids(conn: sqlite3.Connection) -> set:
    """Получить множество ID уже спарсенных пакетов."""
    rows = conn.execute(
        "SELECT id FROM packs WHERE parse_status = 'parsed'"
    ).fetchall()
    return {r["id"] for r in rows}


# --------------- Вопросы ---------------

def insert_questions(
    conn: sqlite3.Connection,
    questions: List[Dict[str, Any]],
    *,
    commit: bool = True,
    strict: bool = False,
) -> int:
    """Вставить список вопросов (пропускает дубликаты)."""
    inserted = 0
    for q in questions:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO questions
                   (id, pack_id, number, tour_number, text, answer,
                    zachet, nezachet, comment, source, authors,
                    razdatka_text, razdatka_pic, position_in_pack)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    q["id"],
                    q["pack_id"],
                    q.get("number"),
                    q.get("tour_number"),
                    q["text"],
                    q["answer"],
                    q.get("zachet"),
                    q.get("nezachet"),
                    q.get("comment"),
                    q.get("source"),
                    q.get("authors"),
                    q.get("razdatka_text"),
                    q.get("razdatka_pic"),
                    q.get("position_in_pack"),
                ),
            )
            inserted += max(cursor.rowcount, 0)
        except Exception as e:
            if strict:
                raise
            print(f"Ошибка insert question #{q.get('id')}: {e}")
    if commit:
        conn.commit()
    return inserted


def get_unclassified_questions(
    conn: sqlite3.Connection,
    limit: Optional[int] = None,
    model_name: Optional[str] = None,
    random_order: bool = True,
    author_filter: Optional[str] = None,
    source_model: Optional[str] = None,
    question_author: Optional[str] = None,
    year: Optional[int] = None,
    pack_ids: Optional[List[int]] = None,
    with_stats_only: bool = False,
) -> List[Dict[str, Any]]:
    """Получить вопросы для классификации.

    Уже классифицированные данной моделью пропускаются.
    random_order=True — случайный порядок (равномерное покрытие пакетов).
    random_order=False — по ID (для детерминистичного сравнения моделей).
    author_filter — фильтр по автору пакета p.authors (LIKE '%..%').
    question_author — фильтр по автору вопроса q.authors (LIKE '%..%').
    source_model — только вопросы, уже классифицированные этой моделью.
    year — фильтр по году пакета (p.start_date).
    pack_ids — только вопросы этих пакетов: нужно, когда разбирают конкретный
    турнир и классифицировать всю базу ради него незачем.
    with_stats_only — только вопросы с турнирной статистикой (take_rate):
    именно они питают адаптивные тренировки, поэтому при ограниченном
    бюджете классификации их размечают в первую очередь.
    """
    order = "RANDOM()" if random_order else "q.id"

    need_pack_join = bool(author_filter or year)
    pack_join = "JOIN packs p ON q.pack_id = p.id" if need_pack_join else ""

    extra_where = []
    extra_params: list = []

    if author_filter:
        extra_where.append("p.authors LIKE ?")
        extra_params.append(f"%{author_filter}%")
    if question_author:
        extra_where.append("q.authors LIKE ?")
        extra_params.append(f"%{question_author}%")
    if year:
        if not need_pack_join:
            pack_join = "JOIN packs p ON q.pack_id = p.id"
        extra_where.append("p.start_date >= ? AND p.start_date < ?")
        extra_params.extend([f"{year}-01-01", f"{year + 1}-01-01"])
    if pack_ids:
        marks = ",".join("?" for _ in pack_ids)
        extra_where.append(f"q.pack_id IN ({marks})")
        extra_params.extend(pack_ids)
    if with_stats_only:
        extra_where.append(
            "EXISTS (SELECT 1 FROM question_result_stats st WHERE st.question_id = q.id)"
        )
    if source_model:
        extra_where.append(
            "q.id IN (SELECT DISTINCT question_id FROM question_topics WHERE model_name = ?)"
        )
        extra_params.append(source_model)

    extra_sql = (" AND " + " AND ".join(extra_where)) if extra_where else ""

    if model_name:
        sql = f"""
            SELECT q.id, q.text, q.answer, q.comment
            FROM questions q
            {pack_join}
            WHERE q.id NOT IN (
                SELECT DISTINCT question_id FROM question_topics WHERE model_name = ?
            )
            {extra_sql}
            ORDER BY {order}
        """
        params: list = [model_name] + extra_params
    else:
        sql = f"""
            SELECT q.id, q.text, q.answer, q.comment
            FROM questions q
            {pack_join}
            WHERE q.id NOT IN (
                SELECT DISTINCT question_id FROM question_topics
            )
            {extra_sql}
            ORDER BY {order}
        """
        params = extra_params

    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_question_count(conn: sqlite3.Connection) -> int:
    """Общее количество вопросов."""
    return conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]


# --------------- Классификация ---------------

def insert_topic(
    conn: sqlite3.Connection,
    question_id: int,
    subcategory_id: int,
    confidence: float,
    method: str,
    model_name: str,
) -> None:
    """Сохранить результат классификации."""
    conn.execute(
        """INSERT OR IGNORE INTO question_topics
           (question_id, subcategory_id, confidence, method, model_name)
           VALUES (?, ?, ?, ?, ?)""",
        (question_id, subcategory_id, confidence, method, model_name),
    )


def get_subcategory_id(
    conn: sqlite3.Connection, cat_num: int, sub_num: int
) -> Optional[int]:
    """Получить ID подкатегории по номерам категории и подкатегории."""
    row = conn.execute(
        """SELECT s.id FROM subcategories s
           JOIN categories c ON s.category_id = c.id
           WHERE c.sort_order = ? AND s.sort_order = ?""",
        (cat_num, sub_num),
    ).fetchone()
    return row["id"] if row else None


# --------------- Миграция ---------------

def migrate_from_legacy(conn: sqlite3.Connection, legacy_path: str | Path) -> int:
    """Мигрировать данные из старой chgk1.db в новую БД."""
    legacy = sqlite3.connect(str(legacy_path))
    legacy.row_factory = sqlite3.Row

    rows = legacy.execute("SELECT * FROM Games").fetchall()
    migrated = 0
    for row in rows:
        data = {
            "id": row["id"],
            "title": row["name"],
            "question_count": row["number_of_questions"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "published_date": row["published_date"],
            "teams_played": row["teams_played"],
            "difficulty": row["difficulty"],
            "authors": row["authors"],
            "link": row["link"],
            "parse_status": "metadata_only",
        }
        if upsert_pack(conn, data):
            migrated += 1

    legacy.close()
    print(f"Мигрировано {migrated} пакетов из старой БД")
    return migrated
