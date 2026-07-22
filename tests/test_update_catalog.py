import json

from database.db import get_connection, insert_questions, upsert_pack
from scripts import update_catalog


def _next_html(payload: dict) -> str:
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    push = json.dumps([1, content], ensure_ascii=False, separators=(",", ":"))
    return f"<script>self.__next_f.push({push})</script>"


class _Response:
    status_code = 200

    def __init__(self, text: str):
        self.text = text


class _Session:
    def close(self):
        pass


def _seed(db_path):
    conn = get_connection(db_path)
    try:
        upsert_pack(
            conn,
            {
                "id": 10,
                "title": "Pipeline pack",
                "teams_played": 2,
                "end_date": "2026-07-01",
                "link": "https://gotquestions.online/pack/10",
                "parse_status": "parsed",
            },
        )
        insert_questions(
            conn,
            [
                {"id": 1001, "pack_id": 10, "text": "Q1", "answer": "A1"},
                {"id": 1002, "pack_id": 10, "text": "Q2", "answer": "A2"},
            ],
        )
    finally:
        conn.close()


def test_one_command_backfill_is_safe_and_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "catalog.db"
    _seed(db_path)
    pack_html = _next_html(
        {
            "questions": [
                {"id": 1001, "number": 1, "text": "Q1"},
                {"id": 1002, "number": 2, "text": "Q2"},
            ]
        }
    )
    table_html = _next_html(
        {"results": [{"id": 1, "mask": "10"}, {"id": 2, "mask": "11"}]}
    )

    def fake_get(_session, url, timeout=None):
        return _Response(table_html if "/table/" in url else pack_html)

    monkeypatch.setattr(update_catalog, "create_session", _Session)
    monkeypatch.setattr("scraper.difficulty.polite_get", fake_get)
    args = update_catalog.build_parser().parse_args(
        [
            "--db",
            str(db_path),
            "--difficulty-only",
            "--backfill-difficulty",
            "--no-backup",
        ]
    )

    first = update_catalog.run_update(args)
    second = update_catalog.run_update(args)

    conn = get_connection(db_path)
    try:
        stats = conn.execute(
            "SELECT COUNT(*), SUM(total_teams) FROM question_result_stats"
        ).fetchone()
        difficulties = [
            row[0]
            for row in conn.execute(
                "SELECT difficulty FROM questions ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()

    assert first["difficulty_packs"] == 1
    assert first["questions_with_new_difficulty"] == 2
    assert second["difficulty_packs"] == 0
    assert tuple(stats) == (2, 4)
    assert difficulties == [0.0, 5.0]
    assert first["validation"]["before"]["questions"] == 2
    assert first["validation"]["after"]["questions"] == 2


def test_explicit_pack_dry_run_does_not_change_database(tmp_path, monkeypatch):
    db_path = tmp_path / "dry-catalog.db"
    _seed(db_path)
    pack_html = _next_html(
        {
            "questions": [
                {"id": 1001, "number": 1, "text": "Q1"},
                {"id": 1002, "number": 2, "text": "Q2"},
            ]
        }
    )
    table_html = _next_html(
        {"results": [{"id": 1, "mask": "10"}, {"id": 2, "mask": "11"}]}
    )

    def fake_get(_session, url, timeout=None):
        return _Response(table_html if "/table/" in url else pack_html)

    monkeypatch.setattr(update_catalog, "create_session", _Session)
    monkeypatch.setattr("scraper.difficulty.polite_get", fake_get)
    args = update_catalog.build_parser().parse_args(
        ["--db", str(db_path), "--pack", "10", "--dry-run"]
    )

    result = update_catalog.run_update(args)

    conn = get_connection(db_path)
    try:
        stats_count = conn.execute(
            "SELECT COUNT(*) FROM question_result_stats"
        ).fetchone()[0]
        difficulty_count = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE difficulty IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    assert result["pack"]["status"] == "complete"
    assert stats_count == difficulty_count == 0


def test_explicit_pack_can_be_calculated_without_prior_status(tmp_path, monkeypatch):
    db_path = tmp_path / "one-pack.db"
    _seed(db_path)
    pack_html = _next_html(
        {
            "questions": [
                {"id": 1001, "number": 1, "text": "Q1"},
                {"id": 1002, "number": 2, "text": "Q2"},
            ]
        }
    )
    table_html = _next_html(
        {"results": [{"id": 1, "mask": "10"}, {"id": 2, "mask": "11"}]}
    )

    def fake_get(_session, url, timeout=None):
        return _Response(table_html if "/table/" in url else pack_html)

    monkeypatch.setattr(update_catalog, "create_session", _Session)
    monkeypatch.setattr("scraper.difficulty.polite_get", fake_get)
    args = update_catalog.build_parser().parse_args(
        ["--db", str(db_path), "--pack", "10", "--no-backup"]
    )

    result = update_catalog.run_update(args)

    assert result["difficulty_packs"] == 1
    assert result["questions_with_new_difficulty"] == 2
