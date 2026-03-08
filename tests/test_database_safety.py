import sqlite3
import threading

from database.db import get_connection, insert_questions, upsert_pack


def test_upsert_pack_does_not_delete_related_questions(tmp_path):
    db_path = tmp_path / "safety.db"
    conn = get_connection(db_path)
    try:
        assert upsert_pack(conn, {"id": 1, "title": "Pack 1", "parse_status": "parsed"})
        inserted = insert_questions(
            conn,
            [
                {
                    "id": 1001,
                    "pack_id": 1,
                    "text": "Question text",
                    "answer": "Answer text",
                }
            ],
        )
        assert inserted == 1

        before = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE pack_id = 1"
        ).fetchone()[0]

        assert upsert_pack(conn, {"id": 1, "parse_status": "pending"})

        after = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE pack_id = 1"
        ).fetchone()[0]
        pack_row = conn.execute(
            "SELECT title, parse_status FROM packs WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()

    assert before == 1
    assert after == 1
    assert pack_row["title"] == "Pack 1"
    assert pack_row["parse_status"] == "pending"


def test_question_topics_has_model_index(tmp_path):
    db_path = tmp_path / "indexes.db"
    conn = get_connection(db_path)
    try:
        index_rows = conn.execute("PRAGMA index_list('question_topics')").fetchall()
    finally:
        conn.close()

    index_names = {row[1] for row in index_rows}
    assert "idx_qt_model" in index_names


def test_get_connection_allows_cross_thread_when_disabled(tmp_path):
    db_path = tmp_path / "thread_ok.db"
    conn = get_connection(db_path, check_same_thread=False)
    errors = []

    def worker():
        try:
            conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    try:
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    finally:
        conn.close()

    assert not errors


def test_get_connection_rejects_cross_thread_by_default(tmp_path):
    db_path = tmp_path / "thread_default.db"
    conn = get_connection(db_path)
    errors = []

    def worker():
        try:
            conn.execute("SELECT 1").fetchone()
        except Exception as exc:
            errors.append(exc)

    try:
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    finally:
        conn.close()

    assert errors
    assert isinstance(errors[0], sqlite3.ProgrammingError)
