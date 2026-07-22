import json
from datetime import date

import pytest

from database.db import get_connection, insert_questions, upsert_pack
from scraper.difficulty import (
    EMBEDDED_DIFFICULTY_METHOD,
    bootstrap_pack_result_statuses,
    calculate_embedded_question_results,
    calculate_question_results,
    extract_masks,
    get_due_pack_ids,
    process_pack_difficulty,
)


def _next_html(payload: dict) -> str:
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    push = json.dumps([1, content], ensure_ascii=False, separators=(",", ":"))
    return f"<html><script>self.__next_f.push({push})</script></html>"


def _pack_html(question_ids: list[int]) -> str:
    return _next_html(
        {
            "questions": [
                {"id": question_id, "number": index, "text": f"Q{index}"}
                for index, question_id in enumerate(question_ids, start=1)
            ]
        }
    )


def _table_html(masks: list[str]) -> str:
    return _next_html(
        {
            "results": [
                {"id": index, "mask": mask}
                for index, mask in enumerate(masks, start=1)
            ]
        }
    )


def _embedded_pack_html(
    question_ids: list[int],
    correct_answers: list[list[int | None]],
    complexities: list[list[float | None]],
    *,
    teams: list[int] | None = None,
    tournament_ids: list[int] | None = None,
) -> str:
    teams = teams or [2]
    tournament_ids = tournament_ids or [99]
    return _next_html(
        {
            "questions": [
                {
                    "id": question_id,
                    "number": index,
                    "text": f"Q{index}",
                    "teams": teams,
                    "correct_answers": correct_answers[index - 1],
                    "complexity": complexities[index - 1],
                    "tournaments": [
                        {"id": tournament_id} for tournament_id in tournament_ids
                    ],
                }
                for index, question_id in enumerate(question_ids, start=1)
            ]
        }
    )


def _seed_pack(conn, pack_id: int = 1, *, teams=2, end_date="2026-07-01"):
    assert upsert_pack(
        conn,
        {
            "id": pack_id,
            "title": f"Pack {pack_id}",
            "teams_played": teams,
            "end_date": end_date,
            "link": f"https://gotquestions.online/pack/{pack_id}",
            "parse_status": "parsed",
        },
    )
    inserted = insert_questions(
        conn,
        [
            {
                "id": pack_id * 1000 + 1,
                "pack_id": pack_id,
                "number": 1,
                "text": "Q1",
                "answer": "A1",
            },
            {
                "id": pack_id * 1000 + 2,
                "pack_id": pack_id,
                "number": 2,
                "text": "Q2",
                "answer": "A2",
            },
        ],
    )
    assert inserted == 2
    return [pack_id * 1000 + 1, pack_id * 1000 + 2]


def test_extract_masks_keeps_identical_team_results():
    html = _table_html(["10", "10", "11"])
    assert extract_masks(html) == ["10", "10", "11"]


def test_calculation_requires_exact_uniform_mask_length():
    with pytest.raises(ValueError, match="different lengths"):
        calculate_question_results([1, 2], ["10", "1"], pack_id=1)

    with pytest.raises(ValueError, match="mask_len=3"):
        calculate_question_results([1, 2], ["101", "111"], pack_id=1)


def test_embedded_stats_aggregate_multiple_validated_tournaments():
    stats, source_hash = calculate_embedded_question_results(
        [
            {
                "id": 1,
                "teams": [1, 2],
                "correct_answers": [1, 1],
                "complexity": [100.0, 50.0],
                "tournaments": [{"id": 10}, {"id": 20}],
            }
        ],
        pack_id=7,
        expected_total_teams=3,
    )

    assert len(source_hash) == 64
    assert len(stats) == 1
    assert stats[0].question_id == 1
    assert stats[0].result_position == 0
    assert stats[0].correct_teams == 2
    assert stats[0].total_teams == 3
    assert stats[0].take_rate == pytest.approx(0.666667)
    assert stats[0].difficulty_raw == pytest.approx(3.3333)


def test_mask_mismatch_uses_validated_id_linked_question_stats(tmp_path):
    conn = get_connection(tmp_path / "embedded.db")
    question_ids = _seed_pack(conn)
    try:
        result = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_embedded_pack_html(
                question_ids,
                [[2], [1]],
                [[100.0], [50.0]],
            ),
            table_html=_table_html(["101", "010"]),
        )
        rows = [
            tuple(row)
            for row in conn.execute(
                """SELECT question_id, result_position, correct_teams,
                          total_teams, difficulty_raw, method, source_url
                   FROM question_result_stats ORDER BY question_id"""
            ).fetchall()
        ]
    finally:
        conn.close()

    assert result.status == "complete"
    assert result.method == EMBEDDED_DIFFICULTY_METHOD
    assert result.questions_updated == 2
    assert rows == [
        (
            question_ids[0],
            0,
            2,
            2,
            0.0,
            EMBEDDED_DIFFICULTY_METHOD,
            "https://gotquestions.online/pack/1",
        ),
        (
            question_ids[1],
            0,
            1,
            2,
            5.0,
            EMBEDDED_DIFFICULTY_METHOD,
            "https://gotquestions.online/pack/1",
        ),
    ]


def test_embedded_stats_require_same_population_as_result_table(tmp_path):
    conn = get_connection(tmp_path / "embedded-population.db")
    question_ids = _seed_pack(conn)
    try:
        result = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_embedded_pack_html(
                question_ids,
                [[3], [1]],
                [[100.0], [33.33]],
                teams=[3],
            ),
            table_html=_table_html(["101", "010"]),
        )
        stats_count = conn.execute(
            "SELECT COUNT(*) FROM question_result_stats"
        ).fetchone()[0]
    finally:
        conn.close()

    assert result.status == "mask_mismatch"
    assert "embedded teams=3 != result rows=2" in result.error
    assert stats_count == 0


def test_partial_embedded_refresh_removes_stale_derived_stats(tmp_path):
    conn = get_connection(tmp_path / "embedded-partial.db")
    question_ids = _seed_pack(conn)
    try:
        first = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_pack_html(question_ids),
            table_html=_table_html(["10", "11"]),
        )
        second = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_embedded_pack_html(
                question_ids,
                [[2], [None]],
                [[100.0], [None]],
            ),
            table_html=_table_html(["101", "010"]),
        )
        stats = [
            tuple(row)
            for row in conn.execute(
                "SELECT question_id, method FROM question_result_stats"
            ).fetchall()
        ]
        difficulties = [
            row[0]
            for row in conn.execute(
                "SELECT difficulty FROM questions ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()

    assert first.status == "complete"
    assert second.status == "partial_complete"
    assert second.questions_updated == 1
    assert stats == [(question_ids[0], EMBEDDED_DIFFICULTY_METHOD)]
    assert difficulties == [0.0, None]


def test_process_pack_writes_provenance_and_question_cache(tmp_path):
    conn = get_connection(tmp_path / "difficulty.db")
    question_ids = _seed_pack(conn)
    try:
        result = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_pack_html(question_ids),
            table_html=_table_html(["10", "11"]),
        )

        rows = conn.execute(
            """SELECT question_id, correct_teams, total_teams, take_rate,
                      difficulty_raw, method, source_hash
               FROM question_result_stats ORDER BY position_in_pack"""
        ).fetchall()
        question_rows = conn.execute(
            "SELECT position_in_pack, difficulty FROM questions ORDER BY id"
        ).fetchall()
        pack_status = conn.execute(
            "SELECT status, attempt_count, mask_count, mask_length "
            "FROM pack_result_status WHERE pack_id = 1"
        ).fetchone()
    finally:
        conn.close()

    assert result.status == "complete"
    assert result.questions_updated == 2
    assert [(row[1], row[2]) for row in rows] == [(2, 2), (1, 2)]
    assert rows[0][3:6] == (1.0, 0.0, "take_rate_v1")
    assert rows[1][3:6] == (0.5, 5.0, "take_rate_v1")
    assert rows[0][6] == rows[1][6] == result.source_hash
    assert [tuple(row) for row in question_rows] == [(1, 0.0), (2, 5.0)]
    assert tuple(pack_status) == ("complete", 1, 2, 2)


def test_mask_mismatch_never_partially_overwrites_previous_stats(tmp_path):
    conn = get_connection(tmp_path / "atomic.db")
    question_ids = _seed_pack(conn)
    try:
        first = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_pack_html(question_ids),
            table_html=_table_html(["10", "11"]),
        )
        before = [
            tuple(row)
            for row in conn.execute(
                "SELECT question_id, difficulty_raw, source_hash "
                "FROM question_result_stats ORDER BY question_id"
            ).fetchall()
        ]

        second = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_pack_html(question_ids),
            table_html=_table_html(["1", "11"]),
        )
        after = [
            tuple(row)
            for row in conn.execute(
                "SELECT question_id, difficulty_raw, source_hash "
                "FROM question_result_stats ORDER BY question_id"
            ).fetchall()
        ]
        status = conn.execute(
            "SELECT status FROM pack_result_status WHERE pack_id = 1"
        ).fetchone()[0]
    finally:
        conn.close()

    assert first.status == "complete"
    assert second.status == "mask_mismatch"
    assert before == after
    assert status == "mask_mismatch"


def test_zero_numbered_warmup_is_not_mapped_to_result_mask(tmp_path):
    conn = get_connection(tmp_path / "warmup.db")
    try:
        upsert_pack(
            conn,
            {
                "id": 1,
                "title": "Warmup pack",
                "teams_played": 2,
                "link": "https://gotquestions.online/pack/1",
                "parse_status": "parsed",
            },
        )
        insert_questions(
            conn,
            [
                {"id": 1000, "pack_id": 1, "number": 0, "text": "Warmup", "answer": "A0"},
                {"id": 1001, "pack_id": 1, "number": 1, "text": "Q1", "answer": "A1"},
                {"id": 1002, "pack_id": 1, "number": 2, "text": "Q2", "answer": "A2"},
            ],
        )
        result = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_next_html(
                {
                    "questions": [
                        {"id": 1000, "number": 0, "text": "Warmup"},
                        {"id": 1001, "number": 1, "text": "Q1"},
                        {"id": 1002, "number": 2, "text": "Q2"},
                    ]
                }
            ),
            table_html=_table_html(["10", "11"]),
        )
        question_rows = [
            tuple(row)
            for row in conn.execute(
                "SELECT id, position_in_pack, difficulty FROM questions ORDER BY id"
            ).fetchall()
        ]
        stats_rows = [
            tuple(row)
            for row in conn.execute(
                "SELECT question_id, position_in_pack, result_position "
                "FROM question_result_stats ORDER BY result_position"
            ).fetchall()
        ]
    finally:
        conn.close()

    assert result.status == "complete"
    assert result.questions_updated == 2
    assert question_rows == [(1000, 1, None), (1001, 2, 0.0), (1002, 3, 5.0)]
    assert stats_rows == [(1001, 2, 1), (1002, 3, 2)]


def test_global_questions_after_mask_are_saved_as_exact_partial_result(tmp_path):
    conn = get_connection(tmp_path / "zamin.db")
    try:
        upsert_pack(
            conn,
            {
                "id": 1,
                "title": "Pack with zamin",
                "teams_played": 2,
                "link": "https://gotquestions.online/pack/1",
                "parse_status": "parsed",
            },
        )
        insert_questions(
            conn,
            [
                {
                    "id": 1000 + number,
                    "pack_id": 1,
                    "number": number,
                    "text": f"Q{number}",
                    "answer": f"A{number}",
                }
                for number in range(1, 4)
            ],
        )
        result = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_next_html(
                {
                    "questions": [
                        {"id": 1000 + number, "number": number, "text": f"Q{number}"}
                        for number in range(1, 4)
                    ]
                }
            ),
            table_html=_table_html(["10", "11"]),
        )
        difficulties = [
            row[0]
            for row in conn.execute(
                "SELECT difficulty FROM questions ORDER BY number"
            ).fetchall()
        ]
        status = conn.execute(
            "SELECT status, last_error FROM pack_result_status WHERE pack_id = 1"
        ).fetchone()
    finally:
        conn.close()

    assert result.status == "partial_complete"
    assert result.questions_updated == 2
    assert difficulties == [0.0, 5.0, None]
    assert status[0] == "partial_complete"
    assert "1 positive-numbered" in status[1]


def test_dry_run_does_not_write_any_difficulty_state(tmp_path):
    conn = get_connection(tmp_path / "dry.db")
    question_ids = _seed_pack(conn)
    try:
        result = process_pack_difficulty(
            None,
            conn,
            1,
            pack_html=_pack_html(question_ids),
            table_html=_table_html(["10", "11"]),
            dry_run=True,
        )
        stats_count = conn.execute(
            "SELECT COUNT(*) FROM question_result_stats"
        ).fetchone()[0]
        status_count = conn.execute(
            "SELECT COUNT(*) FROM pack_result_status"
        ).fetchone()[0]
        cached_count = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE difficulty IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    assert result.status == "complete"
    assert result.stats is not None
    assert stats_count == status_count == cached_count == 0


def test_bootstrap_separates_pending_waiting_and_old_no_results(tmp_path):
    conn = get_connection(tmp_path / "statuses.db")
    _seed_pack(conn, 1, teams=10, end_date="2026-07-01")
    _seed_pack(conn, 2, teams=None, end_date="2026-07-01")
    _seed_pack(conn, 3, teams=None, end_date="2010-01-01")
    try:
        counts = bootstrap_pack_result_statuses(
            conn, today=date(2026, 7, 15)
        )
        statuses = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT pack_id, status, next_retry_at FROM pack_result_status"
            ).fetchall()
        }
        due_without_backfill = get_due_pack_ids(
            conn, today=date(2026, 7, 15)
        )
        due_with_backfill = get_due_pack_ids(
            conn, backfill=True, today=date(2026, 7, 15)
        )
    finally:
        conn.close()

    assert counts == {"pending": 1, "waiting_for_results": 1, "no_results": 1}
    assert statuses[1] == ("pending", None)
    assert statuses[2] == ("waiting_for_results", "2026-07-22")
    assert statuses[3] == ("no_results", None)
    assert due_without_backfill == []
    assert due_with_backfill == [1]
