"""Тренировочная сессия: загрузка вопросов, фиксация ответов, Leitner.

UI-agnostic: вызывается из бота, дашборда или CLI. Не зависит от Streamlit/aiogram.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from dashboard.training_queries import (
    _fetch_full_questions,
    get_training_questions_adaptive,
    get_training_questions_by_category,
    get_training_questions_random,
)
from database.training_db import (
    UNCATEGORIZED_LABEL,
    get_due_question_ids,
    get_recent_failed_ids,
    get_seen_question_ids,
    record_attempt,
)

Mode = Literal[
    "random", "category", "tournament", "review", "weak", "team_gap", "followup",
    "study_fact",
]

# ── Близнецы вопросов ────────────────────────────────────────────────
# Вопрос ЧГК одноразовый, но факты кочуют между пакетами: один факт,
# переспрошенный другими словами, — это фактически тот же вопрос.
# Список пар строит scripts/semantic_search.py --dupes (по эмбеддингам).
_TWIN_MAP: Optional[Dict[int, set]] = None


def _load_twin_map() -> Dict[int, set]:
    global _TWIN_MAP
    if _TWIN_MAP is None:
        from config import PROJECT_ROOT

        twin_path = PROJECT_ROOT / "data" / "embeddings" / "duplicates.tsv"
        mapping: Dict[int, set] = {}
        if twin_path.exists():
            with open(twin_path, encoding="utf-8") as f:
                next(f, None)
                for line in f:
                    try:
                        _sim, a, b = line.rstrip("\n").split("\t")
                        a, b = int(a), int(b)
                    except ValueError:
                        continue
                    mapping.setdefault(a, set()).add(b)
                    mapping.setdefault(b, set()).add(a)
        _TWIN_MAP = mapping
    return _TWIN_MAP


def expand_with_twins(question_ids: set) -> set:
    """Расширить множество виденных вопросов их близнецами."""
    twins = _load_twin_map()
    expanded = set(question_ids)
    for qid in question_ids:
        expanded |= twins.get(qid, set())
    return expanded


@dataclass
class TrainingSession:
    mode: Mode
    questions: List[Dict] = field(default_factory=list)
    index: int = 0
    started_at: float = field(default_factory=time.time)
    question_started_at: float = field(default_factory=time.time)
    user_answer: str = ""
    results: List[Dict] = field(default_factory=list)
    filters_repr: str = ""

    def current(self) -> Optional[Dict]:
        if 0 <= self.index < len(self.questions):
            return self.questions[self.index]
        return None

    def is_finished(self) -> bool:
        return self.index >= len(self.questions)

    def total(self) -> int:
        return len(self.questions)


def start_random(
    chgk_conn: sqlite3.Connection,
    count: int = 12,
    seed: Optional[int] = None,
    difficulty_range: Optional[tuple] = None,
    technique: Optional[str] = None,
) -> TrainingSession:
    questions = get_training_questions_random(
        chgk_conn, difficulty_range, count, seed, None, technique=technique
    )
    tech_str = f" · приём: {technique}" if technique else ""
    return TrainingSession(
        mode="random",
        questions=questions,
        filters_repr=f"случайные · {len(questions)} вопросов{tech_str}",
    )


def start_by_answer(
    chgk_conn: sqlite3.Connection,
    answer: str,
    count: int = 12,
    seed: Optional[int] = None,
) -> TrainingSession:
    """Тренировка одного факта: вопросы, где ответ — этот (петля учить→тренировать).

    Замыкает контур «Учить»: прочитал углы факта на /study → тут же прогоняешь
    их активным припоминанием. Трудные вопросы идут первыми (они учебнее).
    """
    from app import study

    ids = study.question_ids_for_answer(chgk_conn, answer, limit=max(count, 12))
    questions = _fetch_full_questions(chgk_conn, ids[:count])
    return TrainingSession(
        mode="study_fact",
        questions=questions,
        filters_repr=f"факт: {answer} · {len(questions)} вопросов",
    )


def start_by_category(
    chgk_conn: sqlite3.Connection,
    category_ids: List[int],
    count: int = 12,
    seed: Optional[int] = None,
    difficulty_range: Optional[tuple] = None,
    technique: Optional[str] = None,
) -> TrainingSession:
    questions = get_training_questions_by_category(
        chgk_conn, category_ids, None, None, difficulty_range, count, seed, None,
        technique=technique,
    )
    tech_str = f" · приём: {technique}" if technique else ""
    return TrainingSession(
        mode="category",
        questions=questions,
        filters_repr=f"по категориям · {len(questions)} вопросов{tech_str}",
    )


def start_weak_topics(
    chgk_conn: sqlite3.Connection,
    training_conn: sqlite3.Connection,
    user_id: int,
    category_ids: List[int],
    count: int = 12,
    seed: Optional[int] = None,
    difficulty_range: Optional[tuple] = None,
    technique: Optional[str] = None,
) -> TrainingSession:
    """Новые вопросы по слабым темам.

    Отличие от режима «повторение»: там снова показывается тот же вопрос, и это
    проверяет память о конкретном вопросе. Но вопрос ЧГК одноразовый — его
    больше никогда не спросят, а факт внутри него спросят. Поэтому тему
    подтягивают новыми вопросами по ней, а не повтором старых.
    """
    seen = expand_with_twins(get_seen_question_ids(training_conn, user_id))
    questions = get_training_questions_by_category(
        chgk_conn,
        category_ids,
        None,
        None,
        difficulty_range,
        count,
        seed,
        None,
        None,
        exclude_ids=seen,
        technique=technique,
    )
    tech_str = f" · приём: {technique}" if technique else ""
    return TrainingSession(
        mode="weak",
        questions=questions,
        filters_repr=f"слабые темы · {len(questions)} новых вопросов{tech_str}",
    )


def start_team_gap(
    chgk_conn: sqlite3.Connection,
    training_conn: sqlite3.Connection,
    user_id: int,
    count: int = 12,
    seed: Optional[int] = None,
    profile_path: Optional[str] = None,
    top_n: int = 5,
    take_rate_range: tuple = (0.35, 0.70),
) -> TrainingSession:
    """Тренировка по слабым темам КОМАНДЫ из профиля team_gap.

    Профиль (data/team/team-*.json, строится scripts/team_history.py) содержит
    дефицит команды к полю по каждой категории. Берём top_n худших тем с
    достаточной выборкой (n>=5), распределяем вопросы пропорционально дефициту
    и отбираем их в зоне сложности focus_band — полосе беручести, где команда по
    калибровочной кривой теряет больше всего очков (если её нет — take_rate_range).
    """
    import json as _json
    from pathlib import Path as _Path

    from config import PROJECT_ROOT

    path = _Path(profile_path) if profile_path else PROJECT_ROOT / "data" / "team" / "team-97700-gaps.json"
    profile = _json.loads(path.read_text(encoding="utf-8"))

    # Зона сложности берётся из профиля (focus_band, посчитан по калибровочной
    # кривой команды на всей истории), а не угадана. Fallback — переданный дефолт.
    band = profile.get("focus_band")
    if isinstance(band, (list, tuple)) and len(band) == 2:
        take_rate_range = (float(band[0]), float(band[1]))

    # Худшие темы: реальный дефицит, но с достаточной выборкой — n=1..2 это шум,
    # а не измеренная слабость. Если надёжных нет, откатываемся на любой дефицит.
    MIN_N = 5
    negatives = [c for c in profile["categories"] if c["per_question"] < 0]
    reliable = [c for c in negatives if c.get("questions", 0) >= MIN_N]
    weak = sorted(
        reliable or negatives,
        key=lambda c: c["per_question"],
    )[:top_n]
    if not weak:
        return TrainingSession(mode="team_gap", questions=[], filters_repr="дефицитов нет")

    name_to_id = {
        r["name_ru"]: r["id"]
        for r in chgk_conn.execute("SELECT id, name_ru FROM categories")
    }

    # Квоты пропорционально дефициту (худшей теме — больше вопросов)
    total_weight = sum(-c["per_question"] for c in weak)
    quotas = []
    for c in weak:
        cat_id = name_to_id.get(c["category"])
        if cat_id is None:
            continue
        share = -c["per_question"] / total_weight
        quotas.append((cat_id, c["category"], max(1, round(count * share))))

    seen = expand_with_twins(get_seen_question_ids(training_conn, user_id))
    rng_seed = seed
    questions: List[Dict] = []
    picked_ids: set = set()
    for cat_id, _cat_name, quota in quotas:
        batch = get_training_questions_adaptive(
            chgk_conn,
            category_ids=[cat_id],
            take_rate_range=take_rate_range,
            limit=quota,
            seed=rng_seed,
            exclude_ids=seen | picked_ids,
        )
        if len(batch) < quota:
            # Мало вопросов в зоне 35-70% — расширяем окно сложности
            extra = get_training_questions_adaptive(
                chgk_conn,
                category_ids=[cat_id],
                take_rate_range=(0.15, 0.85),
                limit=quota - len(batch),
                seed=rng_seed,
                exclude_ids=seen | picked_ids | {q["id"] for q in batch},
            )
            batch.extend(extra)
        questions.extend(batch)
        picked_ids.update(q["id"] for q in batch)

    questions = questions[:count]
    topics = ", ".join(c["category"] for c in weak)
    return TrainingSession(
        mode="team_gap",
        questions=questions,
        filters_repr=f"слабые темы команды ({topics}) · {len(questions)} вопросов",
    )


def start_followup(
    chgk_conn: sqlite3.Connection,
    training_conn: sqlite3.Connection,
    user_id: int,
    count: int = 12,
    per_fail: int = 2,
    band: tuple = (0.80, 0.95),
) -> TrainingSession:
    """Работа над ошибками: НОВЫЕ вопросы про факты недавних провалов.

    Повтор того же вопроса тренирует память о вопросе (это режим review).
    Здесь для каждого свежего провала берутся 1-2 семантических соседа из
    полосы 0.80-0.95: тот же факт или тема, но другой вопрос. Выше 0.95 —
    почти дубль (бессмысленно), ниже 0.80 — уже другая тема.
    """
    from app import semantic

    if not semantic.available():
        return TrainingSession(
            mode="followup", questions=[],
            filters_repr="нет векторов (scripts/build_embeddings.py)",
        )

    failed = get_recent_failed_ids(training_conn, user_id, limit=30)
    if not failed:
        return TrainingSession(
            mode="followup", questions=[], filters_repr="свежих провалов нет",
        )

    seen = expand_with_twins(get_seen_question_ids(training_conn, user_id))
    picked: List[int] = []
    picked_set: set = set()
    for qid in failed:
        if len(picked) >= count:
            break
        taken = 0
        for nid, sim in semantic.similar_ids(qid, top=15):
            if taken >= per_fail or len(picked) >= count:
                break
            if not (band[0] <= sim < band[1]):
                continue
            if nid in seen or nid in picked_set:
                continue
            picked.append(nid)
            picked_set.add(nid)
            taken += 1

    questions = _fetch_full_questions(chgk_conn, picked) if picked else []
    return TrainingSession(
        mode="followup",
        questions=questions,
        filters_repr=f"работа над ошибками · {len(questions)} вопросов "
                     f"по {min(len(failed), 30)} провалам",
    )


def start_by_tournament(
    chgk_conn: sqlite3.Connection,
    pack_id: int,
    count: Optional[int] = 12,
    seed: Optional[int] = None,
    tour_number: Optional[int] = None,
) -> TrainingSession:
    """Все вопросы конкретного пака (или семпл, если count указан)."""
    rows = chgk_conn.execute(
        """
        SELECT id
        FROM questions
        WHERE pack_id = ?
          AND (? IS NULL OR COALESCE(tour_number, 1) = ?)
        ORDER BY COALESCE(tour_number, 1), number, id
        """,
        (pack_id, tour_number, tour_number),
    ).fetchall()
    qids = [r["id"] for r in rows]
    if count is not None:
        qids = qids[:count]
    questions = _fetch_full_questions(chgk_conn, qids)

    pack = chgk_conn.execute(
        "SELECT title FROM packs WHERE id = ?", (pack_id,)
    ).fetchone()
    title = pack["title"] if pack else f"pack #{pack_id}"
    tour_suffix = f" · тур {tour_number}" if tour_number is not None else ""
    return TrainingSession(
        mode="tournament",
        questions=questions,
        filters_repr=f"{title}{tour_suffix} · {len(questions)} вопросов",
    )


def start_review(
    chgk_conn: sqlite3.Connection,
    training_conn: sqlite3.Connection,
    user_id: int,
    count: int = 12,
) -> TrainingSession:
    """Вопросы из Leitner-очереди, у которых наступило время повторения."""
    qids = get_due_question_ids(training_conn, user_id, limit=count)
    questions = _fetch_full_questions(chgk_conn, qids)
    return TrainingSession(
        mode="review",
        questions=questions,
        filters_repr=f"повторения · {len(questions)} вопросов",
    )


def get_pack_tours(chgk_conn: sqlite3.Connection, pack_id: int) -> List[Dict]:
    rows = chgk_conn.execute(
        """
        SELECT COALESCE(tour_number, 1) AS tour_number, COUNT(*) AS questions_count
        FROM questions
        WHERE pack_id = ?
        GROUP BY COALESCE(tour_number, 1)
        ORDER BY COALESCE(tour_number, 1)
        """,
        (pack_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_tournaments(chgk_conn: sqlite3.Connection, limit: int = 12) -> List[Dict]:
    rows = chgk_conn.execute(
        """
        SELECT p.id, p.title, p.difficulty, COUNT(q.id) AS questions_count
        FROM packs p
        JOIN questions q ON q.pack_id = p.id
        GROUP BY p.id
        HAVING COUNT(q.id) > 0
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def search_tournaments(chgk_conn: sqlite3.Connection, query: str, limit: int = 20) -> List[Dict]:
    """Поиск турниров по названию с Unicode-friendly ранжированием."""
    query_norm = query.strip().casefold()
    if not query_norm:
        return []

    rows = chgk_conn.execute(
        "SELECT p.id, p.title, p.difficulty, COUNT(q.id) AS questions_count "
        "FROM packs p LEFT JOIN questions q ON q.pack_id = p.id "
        "WHERE p.title IS NOT NULL "
        "GROUP BY p.id HAVING COUNT(q.id) > 0"
    ).fetchall()

    matches: List[Dict] = []
    for row in rows:
        pack = dict(row)
        title_norm = pack["title"].casefold()
        pos = title_norm.find(query_norm)
        if pos == -1:
            continue

        if title_norm == query_norm:
            rank = 0
        elif title_norm.startswith(query_norm):
            rank = 1
        else:
            rank = 2

        pack["_rank"] = rank
        pack["_pos"] = pos
        matches.append(pack)

    matches.sort(
        key=lambda p: (
            p["_rank"],
            p["_pos"],
            -p["questions_count"],
            p["title"].casefold(),
        )
    )

    return [
        {k: v for k, v in pack.items() if not k.startswith("_")}
        for pack in matches[:limit]
    ]


def get_pack_by_id(chgk_conn: sqlite3.Connection, pack_id: int) -> Optional[Dict]:
    row = chgk_conn.execute(
        "SELECT p.id, p.title, p.difficulty, COUNT(q.id) AS questions_count "
        "FROM packs p LEFT JOIN questions q ON q.pack_id = p.id "
        "WHERE p.id = ? GROUP BY p.id",
        (pack_id,),
    ).fetchone()
    return dict(row) if row else None


def submit_answer(session: TrainingSession, user_answer: str) -> Dict:
    """Зафиксировать введённый текст ответа. Возвращает текущий вопрос."""
    session.user_answer = user_answer.strip()
    return session.current()


def record_and_advance(
    session: TrainingSession,
    training_conn: sqlite3.Connection,
    user_id: int,
    knew: bool,
) -> bool:
    """Записать самооценку, перейти к следующему. Возвращает True если есть следующий."""
    q = session.current()
    if q is None:
        return False

    elapsed = time.time() - session.question_started_at
    record_attempt(
        training_conn,
        user_id=user_id,
        question_id=q["id"],
        knew=knew,
        user_answer=session.user_answer,
        time_seconds=elapsed,
        mode=session.mode,
        category=q.get("category"),
    )
    session.results.append(
        {
            "question_id": q["id"],
            "user_answer": session.user_answer,
            "correct_answer": q["answer"],
            "knew": knew,
            "time_seconds": elapsed,
            "category": q.get("category"),
        }
    )
    session.index += 1
    session.user_answer = ""
    session.question_started_at = time.time()
    return not session.is_finished()


def session_summary(session: TrainingSession) -> Dict:
    total = len(session.results)
    correct = sum(1 for r in session.results if r["knew"])
    times = [r["time_seconds"] for r in session.results]
    avg_time = sum(times) / len(times) if times else 0.0
    by_cat: Dict[str, Dict[str, int]] = {}
    for r in session.results:
        cat = r.get("category") or UNCATEGORIZED_LABEL
        d = by_cat.setdefault(cat, {"total": 0, "correct": 0})
        d["total"] += 1
        if r["knew"]:
            d["correct"] += 1
    return {
        "total": total,
        "correct": correct,
        "pct": round(100 * correct / total) if total else 0,
        "avg_time": avg_time,
        "by_category": by_cat,
        "filters_repr": session.filters_repr,
    }
