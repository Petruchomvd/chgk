"""Безопасное получение статистики взятий вопросов с GotQuestions."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from config import BASE_URL, SCRAPE_TIMEOUT
from scraper.pack_parser import (
    _extract_push_blocks,
    extract_questions_from_html,
    extract_tour_info_from_html,
)
from scraper.session import polite_get


DIFFICULTY_METHOD = "take_rate_v1"
EMBEDDED_DIFFICULTY_METHOD = "embedded_question_stats_v1"
RECENT_RESULTS_WINDOW_DAYS = 180
RESULTS_RETRY_DAYS = 7
ERROR_RETRY_DAYS = 1
MAX_DIFFICULTY_TIMEOUT = 120


@dataclass(frozen=True)
class QuestionResult:
    question_id: int
    pack_id: int
    position_in_pack: int
    result_position: int
    correct_teams: int
    total_teams: int
    take_rate: float
    difficulty_raw: float


@dataclass
class PackDifficultyResult:
    pack_id: int
    status: str
    questions_updated: int = 0
    teams: int = 0
    mask_length: Optional[int] = None
    method: Optional[str] = None
    source_hash: Optional[str] = None
    error: Optional[str] = None
    stats: Optional[list[QuestionResult]] = None

    def as_dict(self) -> dict:
        data = asdict(self)
        if not self.stats:
            data.pop("stats", None)
        return data


def extract_masks(html: str) -> list[str]:
    """Извлечь маски из декодированных Next.js push-блоков.

    Одинаковые маски не дедуплицируются: две команды вправе дать одинаковый
    результат и обе должны участвовать в знаменателе.
    """
    masks: list[str] = []

    for block in _extract_push_blocks(html):
        if "mask" not in block:
            continue
        try:
            data = json.loads("[" + block + "]")
        except json.JSONDecodeError:
            continue
        if len(data) < 2 or not isinstance(data[1], str):
            continue
        masks.extend(re.findall(r'"mask":"([01]+)"', data[1]))

    if masks:
        return masks

    # Совместимый fallback для сохранённых страниц другого поколения Next.js.
    return re.findall(r'\\"mask\\":\\"([01]+)\\"', html)


def calculate_question_results(
    question_ids: Sequence[int],
    masks: Sequence[str],
    *,
    pack_id: int,
    source_positions: Optional[Sequence[int]] = None,
) -> list[QuestionResult]:
    """Рассчитать статистику только при точном соответствии позиций."""
    if not masks:
        raise ValueError("no masks")
    if not question_ids:
        raise ValueError("no questions")

    mask_lengths = {len(mask) for mask in masks}
    if len(mask_lengths) != 1:
        raise ValueError(f"masks have different lengths: {sorted(mask_lengths)}")

    mask_length = next(iter(mask_lengths))
    if mask_length != len(question_ids):
        raise ValueError(
            f"mask_len={mask_length} != questions={len(question_ids)}"
        )
    if source_positions is not None and len(source_positions) != len(question_ids):
        raise ValueError("source positions do not match question IDs")

    total_teams = len(masks)
    results: list[QuestionResult] = []
    for result_position, question_id in enumerate(question_ids, start=1):
        source_position = (
            source_positions[result_position - 1]
            if source_positions is not None
            else result_position
        )
        correct_teams = sum(mask[result_position - 1] == "1" for mask in masks)
        take_rate = round(correct_teams / total_teams, 6)
        difficulty_raw = round((1 - take_rate) * 10, 4)
        results.append(
            QuestionResult(
                question_id=question_id,
                pack_id=pack_id,
                position_in_pack=source_position,
                result_position=result_position,
                correct_teams=correct_teams,
                total_teams=total_teams,
                take_rate=take_rate,
                difficulty_raw=difficulty_raw,
            )
        )
    return results


def calculate_embedded_question_results(
    raw_questions: Sequence[dict],
    *,
    pack_id: int,
    expected_total_teams: int,
) -> tuple[list[QuestionResult], str]:
    """Рассчитать ID-привязанную статистику из payload вопросов.

    GotQuestions хранит у каждого вопроса массивы ``teams``,
    ``correct_answers`` и ``complexity`` по турнирам. Такой источник можно
    использовать при несовпадении длины маски только после проверки единой
    популяции команд и серверных процентов для каждой сохранённой строки.
    ``result_position=0`` означает, что позиционное сопоставление с маской не
    применялось; исходная позиция остаётся в ``position_in_pack``.
    """
    if not raw_questions:
        raise ValueError("embedded stats have no questions")
    if expected_total_teams <= 0:
        raise ValueError("embedded stats have no result rows to validate against")

    population_signatures: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
    for question in raw_questions:
        teams = question.get("teams")
        tournaments = question.get("tournaments")
        if not isinstance(teams, list) or not teams:
            raise ValueError("embedded stats have no common team population")
        if not isinstance(tournaments, list) or len(tournaments) != len(teams):
            raise ValueError("embedded tournament and team arrays do not align")
        if any(not isinstance(team_count, int) or team_count <= 0 for team_count in teams):
            raise ValueError("embedded team counts are invalid")

        tournament_ids: list[int] = []
        for tournament in tournaments:
            tournament_id = tournament.get("id") if isinstance(tournament, dict) else None
            if not isinstance(tournament_id, int):
                raise ValueError("embedded tournament IDs are invalid")
            tournament_ids.append(tournament_id)
        population_signatures.add((tuple(teams), tuple(tournament_ids)))

    if len(population_signatures) != 1:
        raise ValueError("embedded questions use different team populations")

    team_counts, tournament_ids = next(iter(population_signatures))
    total_teams = sum(team_counts)
    if total_teams != expected_total_teams:
        raise ValueError(
            f"embedded teams={total_teams} != result rows={expected_total_teams}"
        )

    results: list[QuestionResult] = []
    hash_questions: list[dict] = []
    for source_position, question in enumerate(raw_questions, start=1):
        correct = question.get("correct_answers")
        complexity = question.get("complexity")
        hash_questions.append(
            {
                "id": question.get("id"),
                "correct_answers": correct,
                "complexity": complexity,
            }
        )
        if not isinstance(correct, list) or len(correct) != len(team_counts):
            continue
        if not isinstance(complexity, list) or len(complexity) != len(team_counts):
            continue
        if any(
            not isinstance(correct_count, int)
            or correct_count < 0
            or correct_count > team_count
            for correct_count, team_count in zip(correct, team_counts)
        ):
            continue

        percentages_are_valid = True
        for correct_count, team_count, reported_percent in zip(
            correct, team_counts, complexity
        ):
            if not isinstance(reported_percent, (int, float)):
                percentages_are_valid = False
                break
            expected_percent = round(correct_count / team_count * 100, 2)
            if abs(float(reported_percent) - expected_percent) > 0.011:
                percentages_are_valid = False
                break
        if not percentages_are_valid:
            continue

        correct_teams = sum(correct)
        take_rate = round(correct_teams / total_teams, 6)
        difficulty_raw = round((1 - take_rate) * 10, 4)
        results.append(
            QuestionResult(
                question_id=int(question["id"]),
                pack_id=pack_id,
                position_in_pack=source_position,
                result_position=0,
                correct_teams=correct_teams,
                total_teams=total_teams,
                take_rate=take_rate,
                difficulty_raw=difficulty_raw,
            )
        )

    if not results:
        raise ValueError("embedded stats contain no validated questions")

    source_payload = {
        "tournament_ids": tournament_ids,
        "team_counts": team_counts,
        "questions": hash_questions,
    }
    source_hash = hashlib.sha256(
        json.dumps(
            source_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return results, source_hash


def status_for_missing_results(
    pack: sqlite3.Row,
    *,
    today: Optional[date] = None,
) -> tuple[str, Optional[str]]:
    """Разделить новые ожидающие результаты и старые пакеты без результатов."""
    today = today or date.today()
    raw_date = pack["end_date"] or pack["start_date"] or pack["published_date"]
    reference_date = _parse_date(raw_date)

    if reference_date and today - reference_date <= timedelta(
        days=RECENT_RESULTS_WINDOW_DAYS
    ):
        next_retry = today + timedelta(days=RESULTS_RETRY_DAYS)
        return "waiting_for_results", next_retry.isoformat()
    return "no_results", None


def bootstrap_pack_result_statuses(
    conn: sqlite3.Connection,
    *,
    pack_ids: Optional[Iterable[int]] = None,
    dry_run: bool = False,
    today: Optional[date] = None,
) -> dict[str, int]:
    """Назначить начальные статусы, не делая сетевых запросов."""
    where = ["p.parse_status = 'parsed'", "EXISTS (SELECT 1 FROM questions q WHERE q.pack_id = p.id)"]
    params: list[object] = []
    if pack_ids is not None:
        ids = list(dict.fromkeys(pack_ids))
        if not ids:
            return {}
        where.append(f"p.id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)

    rows = conn.execute(
        f"""
        SELECT p.*
        FROM packs p
        LEFT JOIN pack_result_status prs ON prs.pack_id = p.id
        WHERE {' AND '.join(where)} AND prs.pack_id IS NULL
        ORDER BY p.id
        """,
        params,
    ).fetchall()

    counts: dict[str, int] = {}
    for pack in rows:
        if pack["id"] >= 1_000_000 or "db.chgk.info" in (pack["link"] or ""):
            status, next_retry, error = "not_applicable", None, "external import"
        elif (pack["teams_played"] or 0) > 0:
            status, next_retry, error = "pending", None, None
        else:
            status, next_retry = status_for_missing_results(pack, today=today)
            error = "teams_played is missing or zero"

        counts[status] = counts.get(status, 0) + 1
        if not dry_run:
            _record_status(
                conn,
                pack["id"],
                status,
                next_retry_at=next_retry,
                last_error=error,
                increment_attempt=False,
                commit=False,
            )

    if rows and not dry_run:
        conn.commit()
    return counts


def get_due_pack_ids(
    conn: sqlite3.Connection,
    *,
    include_pack_ids: Optional[Iterable[int]] = None,
    backfill: bool = False,
    retry_mismatches: bool = False,
    limit: Optional[int] = None,
    today: Optional[date] = None,
) -> list[int]:
    """Получить очередь, не затягивая старый backfill в обычный запуск."""
    today = today or date.today()
    explicit_ids = list(dict.fromkeys(include_pack_ids or []))
    clauses: list[str] = []
    params: list[object] = []

    if explicit_ids:
        clauses.append(f"p.id IN ({','.join('?' for _ in explicit_ids)})")
        params.extend(explicit_ids)

    if backfill:
        clauses.append(
            "(prs.status = 'pending' OR "
            "(prs.status IN ('waiting_for_results', 'retryable_error') "
            "AND (prs.next_retry_at IS NULL OR prs.next_retry_at <= ?)) OR "
            "(prs.status IN ('complete', 'partial_complete') "
            "AND prs.next_retry_at IS NOT NULL "
            "AND prs.next_retry_at <= ?))"
        )
        params.extend([today.isoformat(), today.isoformat()])
    else:
        clauses.append(
            "(prs.status IN ('waiting_for_results', 'retryable_error', "
            "'complete', 'partial_complete') "
            "AND prs.next_retry_at IS NOT NULL AND prs.next_retry_at <= ?)"
        )
        params.append(today.isoformat())

    if retry_mismatches:
        clauses.append("prs.status IN ('mask_mismatch', 'content_mismatch')")

    if not clauses:
        return []

    sql = f"""
        SELECT DISTINCT p.id
        FROM packs p
        JOIN pack_result_status prs ON prs.pack_id = p.id
        WHERE p.parse_status = 'parsed'
          AND p.id < 1000000
          AND ({' OR '.join(clauses)})
        ORDER BY p.id
    """
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [row[0] for row in conn.execute(sql, params).fetchall()]


def process_pack_difficulty(
    session,
    conn: sqlite3.Connection,
    pack_id: int,
    *,
    pack_html: Optional[str] = None,
    table_html: Optional[str] = None,
    dry_run: bool = False,
    today: Optional[date] = None,
) -> PackDifficultyResult:
    """Получить и атомарно сохранить сложность одного пакета."""
    pack = conn.execute("SELECT * FROM packs WHERE id = ?", (pack_id,)).fetchone()
    if pack is None:
        return PackDifficultyResult(pack_id, "not_found", error="pack is absent in DB")

    if pack_id >= 1_000_000 or "db.chgk.info" in (pack["link"] or ""):
        return _finish_error(
            conn,
            pack,
            "not_applicable",
            "external import",
            dry_run=dry_run,
        )

    try:
        request_timeout = _request_timeout_for_pack(pack)
        if pack_html is None:
            response = polite_get(
                session,
                f"{BASE_URL}/pack/{pack_id}",
                timeout=request_timeout,
            )
            if response is None or response.status_code == 404:
                return _finish_error(
                    conn,
                    pack,
                    "content_mismatch",
                    "pack page is unavailable",
                    dry_run=dry_run,
                )
            pack_html = response.text

        raw_questions = extract_questions_from_html(pack_html)
        tour_info = extract_tour_info_from_html(pack_html)
        ordered_ids = [int(question["id"]) for question in raw_questions]
        if not ordered_ids or len(ordered_ids) != len(set(ordered_ids)):
            return _finish_error(
                conn,
                pack,
                "content_mismatch",
                "source question IDs are empty or duplicated",
                question_count=len(ordered_ids),
                dry_run=dry_run,
            )

        db_ids = {
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM questions WHERE pack_id = ?", (pack_id,)
            ).fetchall()
        }
        if db_ids != set(ordered_ids):
            return _finish_error(
                conn,
                pack,
                "content_mismatch",
                f"source_ids={len(set(ordered_ids))}, db_ids={len(db_ids)}",
                question_count=len(ordered_ids),
                dry_run=dry_run,
            )

        if table_html is None:
            response = polite_get(
                session,
                f"{BASE_URL}/table/{pack_id}",
                timeout=request_timeout,
            )
            if response is None or response.status_code == 404:
                return _missing_results(
                    conn, pack, len(ordered_ids), dry_run=dry_run, today=today
                )
            table_html = response.text

        masks = extract_masks(table_html)
        if not masks:
            return _missing_results(
                conn, pack, len(ordered_ids), dry_run=dry_run, today=today
            )

        mask_lengths = sorted({len(mask) for mask in masks})
        mask_length = mask_lengths[0] if len(mask_lengths) == 1 else None
        scored_questions, covers_all_game_questions = _select_scored_questions(
            raw_questions,
            mask_length,
            tour_info=tour_info,
        )
        scored_ids = [int(question["id"]) for _, question in scored_questions]
        source_positions = [position for position, _ in scored_questions]

        method = DIFFICULTY_METHOD
        source_url = f"{BASE_URL}/table/{pack_id}"
        completion_note: Optional[str] = None
        try:
            stats = calculate_question_results(
                scored_ids,
                masks,
                pack_id=pack_id,
                source_positions=source_positions,
            )
            source_hash = hashlib.sha256(
                "\n".join(masks).encode("ascii")
            ).hexdigest()
            unscored_count = len(raw_questions) - len(scored_questions)
            completion_status = (
                "complete" if covers_all_game_questions else "partial_complete"
            )
            if not covers_all_game_questions:
                completion_note = (
                    f"{unscored_count} positive-numbered questions "
                    "are outside the result mask"
                )
        except ValueError as exc:
            mask_error = str(exc)
            try:
                stats, source_hash = calculate_embedded_question_results(
                    raw_questions,
                    pack_id=pack_id,
                    expected_total_teams=len(masks),
                )
            except ValueError as embedded_exc:
                return _finish_error(
                    conn,
                    pack,
                    "mask_mismatch",
                    f"{mask_error}; embedded_stats={embedded_exc}",
                    teams_found=len(masks),
                    mask_count=len(masks),
                    mask_length=mask_length,
                    question_count=len(ordered_ids),
                    dry_run=dry_run,
                )

            method = EMBEDDED_DIFFICULTY_METHOD
            source_url = f"{BASE_URL}/pack/{pack_id}"
            unscored_count = len(raw_questions) - len(stats)
            completion_status = (
                "complete" if unscored_count == 0 else "partial_complete"
            )
            if unscored_count:
                completion_note = (
                    f"{unscored_count} questions have no validated embedded "
                    f"result statistics after mask mismatch: {mask_error}"
                )

        result = PackDifficultyResult(
            pack_id=pack_id,
            status=completion_status,
            questions_updated=len(stats),
            teams=len(masks),
            mask_length=mask_length,
            method=method,
            source_hash=source_hash,
            stats=stats if dry_run else None,
        )
        if dry_run:
            return result

        with conn:
            for source_position, question_id in enumerate(ordered_ids, start=1):
                conn.execute(
                    "UPDATE questions SET position_in_pack = ? "
                    "WHERE id = ? AND pack_id = ?",
                    (source_position, question_id, pack_id),
                )
            validated_ids = [stat.question_id for stat in stats]
            placeholders = ",".join("?" for _ in validated_ids)
            conn.execute(
                f"DELETE FROM question_result_stats WHERE pack_id = ? "
                f"AND question_id NOT IN ({placeholders})",
                (pack_id, *validated_ids),
            )
            conn.execute(
                f"UPDATE questions SET difficulty = NULL WHERE pack_id = ? "
                f"AND id NOT IN ({placeholders})",
                (pack_id, *validated_ids),
            )
            for stat in stats:
                conn.execute(
                    """
                    INSERT INTO question_result_stats
                        (question_id, pack_id, position_in_pack, result_position,
                         correct_teams,
                         total_teams, take_rate, difficulty_raw, method,
                         source_url, source_hash, calculated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(question_id) DO UPDATE SET
                        pack_id = excluded.pack_id,
                        position_in_pack = excluded.position_in_pack,
                        result_position = excluded.result_position,
                        correct_teams = excluded.correct_teams,
                        total_teams = excluded.total_teams,
                        take_rate = excluded.take_rate,
                        difficulty_raw = excluded.difficulty_raw,
                        method = excluded.method,
                        source_url = excluded.source_url,
                        source_hash = excluded.source_hash,
                        calculated_at = datetime('now')
                    """,
                    (
                        stat.question_id,
                        stat.pack_id,
                        stat.position_in_pack,
                        stat.result_position,
                        stat.correct_teams,
                        stat.total_teams,
                        stat.take_rate,
                        stat.difficulty_raw,
                        method,
                        source_url,
                        source_hash,
                    ),
                )
                conn.execute(
                    """UPDATE questions
                       SET position_in_pack = ?, difficulty = ?
                       WHERE id = ? AND pack_id = ?""",
                    (
                        stat.position_in_pack,
                        stat.difficulty_raw,
                        stat.question_id,
                        pack_id,
                    ),
                )
            _record_status(
                conn,
                pack_id,
                completion_status,
                teams_found=len(masks),
                mask_count=len(masks),
                mask_length=mask_length,
                question_count=len(ordered_ids),
                source_hash=source_hash,
                next_retry_at=_next_refresh_date(pack, today=today),
                last_error=completion_note,
                commit=False,
            )
        return result
    except Exception as exc:
        return _finish_error(
            conn,
            pack,
            "retryable_error",
            str(exc),
            next_retry_at=((today or date.today()) + timedelta(days=ERROR_RETRY_DAYS)).isoformat(),
            dry_run=dry_run,
        )


def _missing_results(
    conn: sqlite3.Connection,
    pack: sqlite3.Row,
    question_count: int,
    *,
    dry_run: bool,
    today: Optional[date],
) -> PackDifficultyResult:
    stats_table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='question_result_stats'"
    ).fetchone() is not None
    existing_stats = 0
    if stats_table_exists:
        existing_stats = conn.execute(
            "SELECT COUNT(*) FROM question_result_stats WHERE pack_id = ?",
            (pack["id"],),
        ).fetchone()[0]
    if existing_stats:
        next_retry = (today or date.today()) + timedelta(days=ERROR_RETRY_DAYS)
        return _finish_error(
            conn,
            pack,
            "retryable_error",
            "result masks temporarily unavailable; previous stats preserved",
            question_count=question_count,
            next_retry_at=next_retry.isoformat(),
            dry_run=dry_run,
        )

    status, next_retry = status_for_missing_results(pack, today=today)
    return _finish_error(
        conn,
        pack,
        status,
        "no result masks",
        question_count=question_count,
        next_retry_at=next_retry,
        dry_run=dry_run,
    )


def _finish_error(
    conn: sqlite3.Connection,
    pack: sqlite3.Row,
    status: str,
    error: str,
    *,
    teams_found: Optional[int] = None,
    mask_count: Optional[int] = None,
    mask_length: Optional[int] = None,
    question_count: Optional[int] = None,
    next_retry_at: Optional[str] = None,
    dry_run: bool,
) -> PackDifficultyResult:
    if not dry_run:
        _record_status(
            conn,
            pack["id"],
            status,
            teams_found=teams_found,
            mask_count=mask_count,
            mask_length=mask_length,
            question_count=question_count,
            next_retry_at=next_retry_at,
            last_error=error,
        )
    return PackDifficultyResult(
        pack_id=pack["id"],
        status=status,
        teams=teams_found or 0,
        mask_length=mask_length,
        error=error,
    )


def _record_status(
    conn: sqlite3.Connection,
    pack_id: int,
    status: str,
    *,
    teams_found: Optional[int] = None,
    mask_count: Optional[int] = None,
    mask_length: Optional[int] = None,
    question_count: Optional[int] = None,
    source_hash: Optional[str] = None,
    next_retry_at: Optional[str] = None,
    last_error: Optional[str] = None,
    increment_attempt: bool = True,
    commit: bool = True,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO pack_result_status
            (pack_id, status, attempt_count, last_attempt_at, next_retry_at,
             teams_found, mask_count, mask_length, question_count,
             source_hash, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pack_id) DO UPDATE SET
            status = excluded.status,
            attempt_count = pack_result_status.attempt_count + ?,
            last_attempt_at = COALESCE(excluded.last_attempt_at, pack_result_status.last_attempt_at),
            next_retry_at = excluded.next_retry_at,
            teams_found = excluded.teams_found,
            mask_count = excluded.mask_count,
            mask_length = excluded.mask_length,
            question_count = excluded.question_count,
            source_hash = COALESCE(excluded.source_hash, pack_result_status.source_hash),
            last_error = excluded.last_error
        """,
        (
            pack_id,
            status,
            1 if increment_attempt else 0,
            now if increment_attempt else None,
            next_retry_at,
            teams_found,
            mask_count,
            mask_length,
            question_count,
            source_hash,
            last_error,
            1 if increment_attempt else 0,
        ),
    )
    if commit:
        conn.commit()


def _parse_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (TypeError, ValueError):
        return None


def _request_timeout_for_pack(pack: sqlite3.Row) -> int:
    """Дать большим пакетам время загрузить многомегабайтный Next.js payload."""
    question_count = pack["question_count"] or 0
    scaled_timeout = int(question_count) // 5
    return max(SCRAPE_TIMEOUT, min(MAX_DIFFICULTY_TIMEOUT, scaled_timeout))


def _select_scored_questions(
    raw_questions: Sequence[dict],
    mask_length: Optional[int],
    *,
    tour_info: Optional[Sequence[dict]] = None,
) -> tuple[list[tuple[int, dict]], bool]:
    """Исключить явные нулевые/разминочные вопросы только при точном совпадении."""
    positioned = list(enumerate(raw_questions, start=1))
    if mask_length is None or len(positioned) == mask_length:
        return positioned, True

    positive_numbered = [
        (position, question)
        for position, question in positioned
        if isinstance(question.get("number"), int) and question["number"] > 0
    ]
    if len(positive_numbered) == mask_length:
        return positive_numbered, True

    # На сайте разминка часто оформлена отдельным туром №0, но её вопросы
    # могут иметь положительные номера, совпадающие с основной игрой.
    if tour_info:
        game_ids: list[int] = []
        for tour in tour_info:
            tour_number = tour.get("number")
            if not isinstance(tour_number, int) or tour_number <= 0:
                continue
            for question in tour.get("questions") or []:
                if "id" in question:
                    game_ids.append(int(question["id"]))
        if len(game_ids) == mask_length and len(game_ids) == len(set(game_ids)):
            by_id = {
                int(question["id"]): (position, question)
                for position, question in positioned
            }
            if all(question_id in by_id for question_id in game_ids):
                return [by_id[question_id] for question_id in game_ids], True

    # Некоторые пакеты содержат дополнительные заминки после основной игры.
    # Сопоставление безопасно только если для каждой позиции 1..N существует
    # ровно один вопрос с таким глобальным номером.
    by_number: dict[int, list[tuple[int, dict]]] = {}
    for position, question in positive_numbered:
        number = question["number"]
        if number <= mask_length:
            by_number.setdefault(number, []).append((position, question))
    expected_numbers = set(range(1, mask_length + 1))
    if set(by_number) == expected_numbers and all(
        len(items) == 1 for items in by_number.values()
    ):
        return [by_number[number][0] for number in range(1, mask_length + 1)], False
    return positioned, False


def _next_refresh_date(
    pack: sqlite3.Row,
    *,
    today: Optional[date],
) -> Optional[str]:
    """Недавно сыгранные результаты периодически обновляются после апелляций."""
    today = today or date.today()
    raw_date = pack["end_date"] or pack["start_date"] or pack["published_date"]
    reference_date = _parse_date(raw_date)
    if reference_date and today - reference_date <= timedelta(
        days=RECENT_RESULTS_WINDOW_DAYS
    ):
        return (today + timedelta(days=RESULTS_RETRY_DAYS)).isoformat()
    return None
