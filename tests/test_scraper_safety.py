import json

import scraper.runner as runner
from database.db import get_connection, insert_questions, upsert_pack


class _Response:
    status_code = 200

    def __init__(self, text: str):
        self.text = text


def _pack_html(question_ids: list[int]) -> str:
    payload = {
        "questions": [
            {
                "id": question_id,
                "number": index,
                "text": f"Q{index}",
                "answer": f"A{index}",
            }
            for index, question_id in enumerate(question_ids, start=1)
        ]
    }
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    push = json.dumps([1, content], ensure_ascii=False, separators=(",", ":"))
    return f"<script>self.__next_f.push({push})</script>"


def test_source_question_removal_rolls_back_without_deleting_local_data(
    tmp_path, monkeypatch
):
    conn = get_connection(tmp_path / "scraper-atomic.db")
    try:
        upsert_pack(
            conn,
            {"id": 1, "title": "Existing", "parse_status": "parsed"},
        )
        insert_questions(
            conn,
            [
                {"id": 1001, "pack_id": 1, "text": "Old Q1", "answer": "A1"},
                {"id": 1002, "pack_id": 1, "text": "Old Q2", "answer": "A2"},
            ],
        )
        category_id = conn.execute(
            "INSERT INTO categories(name, name_ru) VALUES ('test', 'Тест')"
        ).lastrowid
        subcategory_id = conn.execute(
            """INSERT INTO subcategories(category_id, name, name_ru)
               VALUES (?, 'test-sub', 'Тест')""",
            (category_id,),
        ).lastrowid
        conn.execute(
            """INSERT INTO question_topics
               (question_id, subcategory_id, confidence, method, model_name)
               VALUES (1002, ?, 0.9, 'test', 'test-model')""",
            (subcategory_id,),
        )
        conn.commit()

        monkeypatch.setattr(
            runner,
            "polite_get",
            lambda _session, _url: _Response(_pack_html([1001])),
        )
        result = runner.scrape_pack(object(), conn, 1)

        questions = [
            tuple(row)
            for row in conn.execute(
                "SELECT id, text FROM questions WHERE pack_id = 1 ORDER BY id"
            ).fetchall()
        ]
        topics = conn.execute(
            "SELECT COUNT(*) FROM question_topics WHERE question_id = 1002"
        ).fetchone()[0]
        status = conn.execute(
            "SELECT parse_status FROM packs WHERE id = 1"
        ).fetchone()[0]
    finally:
        conn.close()

    assert result is False
    assert questions == [(1001, "Old Q1"), (1002, "Old Q2")]
    assert topics == 1
    assert status == "failed"
