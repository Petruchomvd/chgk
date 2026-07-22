"""Идемпотентная запись производных данных о кино в БД.

Исходные таблицы (questions, packs, question_topics, question_result_stats)
здесь не изменяются ни при каких обстоятельствах — только cinema_*.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .dictionary import CinemaDictionary
from .matcher import Match, Rejected

# Таблицы, которые пайплайн имеет право трогать. Всё остальное — только чтение.
OWNED_TABLES = ("cinema_entities", "cinema_mentions", "cinema_rejections", "cinema_runs")


def dict_hash(snapshot_dir: str | Path) -> str:
    """Хэш снапшота словаря: позволяет понять, тем ли словарём считали."""
    digest = hashlib.sha256()
    for name in ("films.json", "characters.json", "people.json"):
        path = Path(snapshot_dir) / name
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def sync_entities(
    conn: sqlite3.Connection, dictionary: CinemaDictionary, *, dict_version: str
) -> int:
    """Записать сущности словаря. Полная замена: словарь — источник истины."""
    rows = []
    for entity in dictionary.entities.values():
        rows.append(
            (
                entity.key,
                entity.kind,
                entity.label,
                entity.year,
                "|".join(entity.types) or None,
                "|".join(entity.directors) or None,
                "|".join(entity.actors) or None,
                "|".join(entity.based_on) or None,
                "|".join(entity.homonym_types) or None,
                len(entity.homonyms),
                "|".join(entity.films) or None,
                "|".join(entity.sources) or None,
                "|".join(entity.roles) or None,
                entity.wiki_url,
                dict_version,
            )
        )
    conn.executemany(
        """
        INSERT INTO cinema_entities
            (key, kind, label, year, types, directors, actors, based_on,
             homonym_types, homonym_count, films, sources, roles, wiki_url, dict_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            kind = excluded.kind,
            label = excluded.label,
            year = excluded.year,
            types = excluded.types,
            directors = excluded.directors,
            actors = excluded.actors,
            based_on = excluded.based_on,
            homonym_types = excluded.homonym_types,
            homonym_count = excluded.homonym_count,
            films = excluded.films,
            sources = excluded.sources,
            roles = excluded.roles,
            wiki_url = excluded.wiki_url,
            dict_version = excluded.dict_version
        """,
        rows,
    )

    # Сущности, выпавшие из словаря, обязаны исчезнуть и из таблицы: иначе
    # в ней навсегда останутся те, кого отсеяли новые фильтры (Сталин, Геринг).
    # Их упоминания удалятся каскадом — они всё равно больше не подтверждены.
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _dict_keys (key TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _dict_keys")
    conn.executemany(
        "INSERT OR IGNORE INTO _dict_keys (key) VALUES (?)",
        [(key,) for key in dictionary.entities],
    )
    conn.execute(
        "DELETE FROM cinema_entities WHERE key NOT IN (SELECT key FROM _dict_keys)"
    )
    return len(rows)


def start_run(
    conn: sqlite3.Connection,
    *,
    dict_version: str,
    dict_hash_value: str,
    params: Dict,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO cinema_runs (started_at, dict_version, dict_hash, params)
        VALUES (?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            dict_version,
            dict_hash_value,
            json.dumps(params, ensure_ascii=False),
        ),
    )
    return int(cursor.lastrowid)


def save_matches(
    conn: sqlite3.Connection,
    run_id: int,
    question_id: int,
    matches: Sequence[Match],
    rejected: Sequence[Rejected],
) -> None:
    if matches:
        conn.executemany(
            """
            INSERT OR IGNORE INTO cinema_mentions
                (run_id, question_id, entity_key, field, rule, confidence,
                 matched_text, start_offset, end_offset, context, alias, flags,
                 cinema_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    question_id,
                    m.entity_key,
                    m.field,
                    m.rule,
                    m.confidence,
                    m.matched_text,
                    m.start,
                    m.end,
                    m.context,
                    m.alias,
                    "|".join(m.flags) or None,
                    1 if m.cinema_context else 0,
                )
                for m in matches
            ],
        )
    if rejected:
        conn.executemany(
            """
            INSERT INTO cinema_rejections
                (run_id, question_id, entity_key, field, reason, matched_text, context)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    question_id,
                    r.entity_key,
                    r.field,
                    r.reason,
                    r.matched_text,
                    r.context,
                )
                for r in rejected
            ],
        )


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    questions_scanned: int,
    mentions_found: int,
    rejected_count: int,
    notes: str = "",
) -> None:
    conn.execute(
        """
        UPDATE cinema_runs
        SET finished_at = ?, questions_scanned = ?, mentions_found = ?,
            rejected_count = ?, notes = ?
        WHERE id = ?
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            questions_scanned,
            mentions_found,
            rejected_count,
            notes,
            run_id,
        ),
    )


def drop_other_runs(conn: sqlite3.Connection, keep_run_id: int) -> int:
    """Удалить результаты прошлых прогонов.

    Так полный прогон остаётся идемпотентным: в таблицах всегда ровно один
    актуальный набор упоминаний, а не накопленные дубли за все запуски.
    Вызывается только после успешного полного прогона.
    """
    cursor = conn.execute("SELECT id FROM cinema_runs WHERE id != ?", (keep_run_id,))
    stale = [row[0] for row in cursor.fetchall()]
    if not stale:
        return 0
    marks = ",".join("?" for _ in stale)
    conn.execute(f"DELETE FROM cinema_mentions WHERE run_id IN ({marks})", stale)
    conn.execute(f"DELETE FROM cinema_rejections WHERE run_id IN ({marks})", stale)
    conn.execute(f"DELETE FROM cinema_runs WHERE id IN ({marks})", stale)
    return len(stale)


def latest_run_id(conn: sqlite3.Connection) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM cinema_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else None


def iter_questions(
    conn: sqlite3.Connection, *, limit: Optional[int] = None
) -> Iterable[sqlite3.Row]:
    sql = "SELECT id, text, answer, comment FROM questions ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    yield from conn.execute(sql)


def integrity_snapshot(conn: sqlite3.Connection) -> Dict[str, int]:
    """Счётчики исходных таблиц — до и после прогона они обязаны совпасть."""
    return {
        "packs": conn.execute("SELECT COUNT(*) FROM packs").fetchone()[0],
        "questions": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
        "question_topics": conn.execute("SELECT COUNT(*) FROM question_topics").fetchone()[0],
        "question_result_stats": conn.execute(
            "SELECT COUNT(*) FROM question_result_stats"
        ).fetchone()[0],
    }
