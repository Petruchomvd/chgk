import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_THIS_DIR = Path(__file__).parent
SCHEMA_PATH = _THIS_DIR / "schema.sql"


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Создать подключение к БД и применить схему."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()

    _migrate_question_topics(conn)
    _migrate_question_difficulty(conn)
    return conn


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
    if "difficulty" in row[0]:
        return

    print("[migration] Добавляю колонку difficulty в questions...")
    conn.execute("ALTER TABLE questions ADD COLUMN difficulty REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty)")
    conn.commit()
    print("[migration] Готово.")


# --------------- Пакеты ---------------

def upsert_pack(conn: sqlite3.Connection, data: Dict[str, Any]) -> bool:
    """Вставить или обновить пакет."""
    try:
        conn.execute(
            """INSERT OR REPLACE INTO packs
               (id, title, question_count, start_date, end_date,
                published_date, teams_played, difficulty, authors, link, parse_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Ошибка upsert_pack #{data['id']}: {e}")
        return False


def mark_pack_status(
    conn: sqlite3.Connection, pack_id: int, status: str, error: Optional[str] = None
) -> None:
    """Обновить статус парсинга пакета."""
    conn.execute(
        "UPDATE packs SET parse_status = ?, error_message = ? WHERE id = ?",
        (status, error, pack_id),
    )
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

def insert_questions(conn: sqlite3.Connection, questions: List[Dict[str, Any]]) -> int:
    """Вставить список вопросов (пропускает дубликаты)."""
    inserted = 0
    for q in questions:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO questions
                   (id, pack_id, number, tour_number, text, answer,
                    zachet, nezachet, comment, source, authors,
                    razdatka_text, razdatka_pic)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                ),
            )
            inserted += 1
        except Exception as e:
            print(f"Ошибка insert question #{q.get('id')}: {e}")
    conn.commit()
    return inserted


def get_unclassified_questions(
    conn: sqlite3.Connection,
    limit: Optional[int] = None,
    model_name: Optional[str] = None,
    random_order: bool = True,
    author_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Получить вопросы для классификации.

    Уже классифицированные данной моделью пропускаются.
    random_order=True — случайный порядок (равномерное покрытие пакетов).
    random_order=False — по ID (для детерминистичного сравнения моделей).
    author_filter — фильтр по имени автора пакета (LIKE '%<author_filter>%').
    """
    order = "RANDOM()" if random_order else "q.id"

    author_join = "JOIN packs p ON q.pack_id = p.id" if author_filter else ""
    author_where = "AND p.authors LIKE ?" if author_filter else ""

    if model_name:
        sql = f"""
            SELECT q.id, q.text, q.answer, q.comment
            FROM questions q
            {author_join}
            WHERE q.id NOT IN (
                SELECT DISTINCT question_id FROM question_topics WHERE model_name = ?
            )
            {author_where}
            ORDER BY {order}
        """
        params: list = [model_name]
    else:
        sql = f"""
            SELECT q.id, q.text, q.answer, q.comment
            FROM questions q
            {author_join}
            WHERE q.id NOT IN (
                SELECT DISTINCT question_id FROM question_topics
            )
            {author_where}
            ORDER BY {order}
        """
        params = []

    if author_filter:
        params.append(f"%{author_filter}%")

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
