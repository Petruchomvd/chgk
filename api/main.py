"""FastAPI-слой для веб-приложения «Картотека».

Запуск:
    .venv/bin/uvicorn api.main:app --reload --port 8000

Логика обучения НЕ дублируется: используется `app.training_engine`
(тот же движок, что и у Telegram-бота), поэтому попытки и Leitner
сохраняются в `training.db` и общие для бота и веба.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api import catalog
from api.auth import (
    COOKIE_NAME,
    CSRF_HEADER,
    active_user,
    authenticate,
    cookie_secure,
    create_session,
    require_current_user,
    reset_current_user,
    session_from_request,
    set_current_user,
)
from app import study
from app import training_engine as engine
from config import DB_PATH, PROJECT_ROOT
from database.training_db import (
    TRAINING_DB_PATH,
    count_due,
    get_stats,
    get_training_connection,
)
from dashboard.db_queries import get_all_categories, get_available_models, get_overview_stats

app = FastAPI(title="ЧГК · Картотека")

# Vite dev-сервер.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Измеренные на турнирах слабости команды: файл готовит scripts/team_gap.py.
# ID команды в рейтинге ЧГК; если файла нет, интерфейс просто не покажет раздел.
TEAM_ID = int(os.environ.get("CHGK_TEAM_ID", "97700"))
TEAM_GAPS_PATH = PROJECT_ROOT / "data" / "team" / f"team-{TEAM_ID}-gaps.json"


def _ensure_training_schema() -> None:
    """training.db мигрируется лениво внутри get_training_connection().

    ATTACH в обход этого может подцепить старую схему (без user_id),
    поэтому прогоняем миграцию до первого запроса.
    """
    get_training_connection().close()


_ensure_training_schema()

_login_failures: dict[str, deque[float]] = defaultdict(deque)
_LOGIN_WINDOW_SECONDS = 10 * 60
_LOGIN_MAX_FAILURES = 5
_PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/session"}


def _client_key(request: Request) -> str:
    return request.headers.get("x-real-ip") or (
        request.client.host if request.client else "unknown"
    )


def _prune_failures(key: str) -> deque[float]:
    now = time.monotonic()
    failures = _login_failures[key]
    while failures and failures[0] < now - _LOGIN_WINDOW_SECONDS:
        failures.popleft()
    return failures


@app.middleware("http")
async def authenticate_api_request(request: Request, call_next):
    """Защитить API подписанной сессией и CSRF-токеном."""
    if not request.url.path.startswith("/api/") or request.url.path in _PUBLIC_API_PATHS:
        return await call_next(request)

    signed_user = session_from_request(request)
    user = active_user(signed_user) if signed_user else None
    if user is None:
        return JSONResponse({"detail": "Требуется войти"}, status_code=401)

    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets_compare(
            request.headers.get(CSRF_HEADER, ""), user.csrf_token
        ):
            return JSONResponse(
                {"detail": "Сессия устарела. Обновите страницу."},
                status_code=403,
            )

    token = set_current_user(user)
    try:
        return await call_next(request)
    finally:
        reset_current_user(token)


def secrets_compare(left: str, right: str) -> bool:
    """Сравнение без утечки времени; вынесено для простого тестирования."""
    import hmac

    return bool(left and right) and hmac.compare_digest(left, right)


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = True


def _user_payload(user, csrf_token: str) -> Dict[str, Any]:
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
        },
        "csrf_token": csrf_token,
    }


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request, response: Response):
    key = _client_key(request)
    failures = _prune_failures(key)
    if len(failures) >= _LOGIN_MAX_FAILURES:
        raise HTTPException(
            429, "Слишком много попыток. Попробуйте снова через 10 минут."
        )

    user = authenticate(req.username, req.password)
    if user is None:
        failures.append(time.monotonic())
        raise HTTPException(401, "Неверный логин или пароль")

    _login_failures.pop(key, None)
    session_token, csrf_token, max_age = create_session(user, req.remember)
    response.set_cookie(
        COOKIE_NAME,
        session_token,
        max_age=max_age if req.remember else None,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    return _user_payload(user, csrf_token)


@app.get("/api/auth/session")
def auth_session(request: Request):
    signed_user = session_from_request(request)
    user = active_user(signed_user) if signed_user else None
    if user is None:
        raise HTTPException(401, "Требуется войти")
    return _user_payload(user, user.csrf_token)


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=cookie_secure(),
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


def chgk_conn() -> sqlite3.Connection:
    """Соединение с базой вопросов + присоединённая training.db.

    ATTACH позволяет фильтровать каталог по статусу изучения и
    отдавать историю попыток одним запросом.
    """
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("ATTACH DATABASE ? AS t", (str(TRAINING_DB_PATH),))
    return conn


# ─── Сессии тренировки ────────────────────────────────────────────
# Однопользовательское приложение: сессии живут в памяти процесса.
# Результаты каждой попытки при этом сразу пишутся в training.db,
# поэтому перезапуск сервера теряет только незавершённую сессию.

_SESSIONS: Dict[str, tuple[int, engine.TrainingSession]] = {}


def _session_or_404(session_id: str) -> engine.TrainingSession:
    item = _SESSIONS.get(session_id)
    user_id = require_current_user().id
    if item is None or item[0] != user_id:
        raise HTTPException(404, "Сессия не найдена")
    return item[1]


def _question_payload(q: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(q)
    d["authors"] = catalog.parse_authors(d.get("authors"))
    return d


def _state(session_id: str, s: engine.TrainingSession) -> Dict[str, Any]:
    q = s.current()
    return {
        "session_id": session_id,
        "mode": s.mode,
        "filters_repr": s.filters_repr,
        "index": s.index,
        "total": s.total(),
        "finished": s.is_finished(),
        "question": _question_payload(q) if q else None,
        "elapsed": time.time() - s.question_started_at if q else 0,
        "results": s.results,
        "summary": engine.session_summary(s) if s.is_finished() else None,
    }


# ─── Мета ─────────────────────────────────────────────────────────

@app.get("/api/meta")
def meta():
    conn = chgk_conn()
    try:
        stats = get_overview_stats(conn)
        # Беручесть известна не у всех вопросов: она берётся из результатов
        # турниров, а у старых пакетов их нет. Фильтр по слою молча отбросит
        # остальные, поэтому число показывается рядом с фильтром.
        with_difficulty = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE difficulty IS NOT NULL"
        ).fetchone()[0]
        return {
            "categories": [dict(c) for c in get_all_categories(conn)],
            "models": get_available_models(conn),
            "total_questions": stats["total_questions"],
            "total_packs": stats["total_packs"],
            "classified": stats["classified"],
            "classification_pct": stats["classification_pct"],
            "with_difficulty": with_difficulty,
        }
    finally:
        conn.close()


@app.get("/api/overview")
def overview():
    user_id = require_current_user().id
    conn = chgk_conn()
    tconn = get_training_connection()
    try:
        stats = get_stats(tconn, user_id)
        base = get_overview_stats(conn)
        active = [
            {"session_id": sid, **_state(sid, s)}
            for sid, (owner_id, s) in _SESSIONS.items()
            if owner_id == user_id and not s.is_finished()
        ]
        return {
            "due_count": count_due(tconn, user_id),
            "stats": stats,
            "base": base,
            "weak_categories": catalog.weak_categories(conn, user_id),
            "recent": catalog.recent_attempts(conn, user_id),
            "activity": catalog.activity_by_day(conn, user_id),
            "active_session": active[0] if active else None,
        }
    finally:
        conn.close()
        tconn.close()


# ─── Каталог ──────────────────────────────────────────────────────

@app.get("/api/questions")
def questions(
    search: str = "",
    category_id: Optional[int] = None,
    subcategory_id: Optional[int] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    difficulty_min: Optional[float] = None,
    difficulty_max: Optional[float] = None,
    author: Optional[str] = None,
    status: Optional[str] = None,
    model_name: Optional[str] = None,
    sort: Optional[str] = None,
    limit: int = Query(40, le=100),
    offset: int = 0,
):
    user_id = require_current_user().id
    conn = chgk_conn()
    try:
        kwargs = dict(
            search=search.strip(), category_id=category_id,
            subcategory_id=subcategory_id, year_from=year_from, year_to=year_to,
            difficulty_min=difficulty_min, difficulty_max=difficulty_max,
            author=author, status=status, model_name=model_name,
        )
        items = catalog.search_catalog(
            conn, user_id, sort=sort, limit=limit, offset=offset, **kwargs
        )
        total = catalog.count_catalog(conn, user_id, **kwargs)
        return {"items": items, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


@app.get("/api/questions/{question_id}")
def question(question_id: int):
    user_id = require_current_user().id
    conn = chgk_conn()
    try:
        q = catalog.get_question(conn, user_id, question_id)
        if q is None:
            raise HTTPException(404, "Вопрос не найден")
        return q
    finally:
        conn.close()


def _enrich_semantic_hits(conn: sqlite3.Connection, pairs) -> list:
    """Собрать карточки вопросов для результатов семантики."""
    items = []
    for qid, sim in pairs:
        row = conn.execute(
            """
            SELECT q.id, q.text, q.answer,
                   q.difficulty AS question_difficulty,
                   p.title AS pack_title,
                   SUBSTR(p.start_date, 1, 4) AS year
            FROM questions q
            LEFT JOIN packs p ON p.id = q.pack_id
            WHERE q.id = ?
            """,
            (qid,),
        ).fetchone()
        if not row:
            continue
        cat = conn.execute(
            """
            SELECT c.name_ru FROM question_topics qt
            JOIN subcategories s ON s.id = qt.subcategory_id
            JOIN categories c ON c.id = s.category_id
            WHERE qt.question_id = ?
            ORDER BY qt.confidence DESC LIMIT 1
            """,
            (qid,),
        ).fetchone()
        items.append({
            "id": row["id"],
            "similarity": round(sim, 3),
            "question_difficulty": row["question_difficulty"],
            "text_preview": (row["text"] or "")[:220],
            "answer": row["answer"],
            "pack_title": row["pack_title"],
            "year": row["year"],
            "category": cat["name_ru"] if cat else None,
        })
    return items


@app.get("/api/questions/{question_id}/similar")
def question_similar(question_id: int, top: int = 10):
    """Соседи вопроса по смыслу (эмбеддинги). Модель не нужна — только векторы."""
    from app import semantic

    if not semantic.available():
        return {"available": False, "items": []}
    pairs = semantic.similar_ids(question_id, min(top, 30))
    conn = chgk_conn()
    try:
        return {"available": True, "items": _enrich_semantic_hits(conn, pairs)}
    finally:
        conn.close()


@app.get("/api/semantic/map")
def semantic_map():
    """Точки смысловой карты. Первый вызов считает проекцию (~10 с), дальше кэш."""
    from app import semantic

    if not semantic.available():
        return {"available": False, "points": []}
    return semantic.map_points()


@app.get("/api/search/semantic")
def semantic_search(q: str, top: int = 20):
    """Поиск по смыслу. Первый вызов грузит модель (несколько секунд)."""
    from app import semantic

    if not semantic.available():
        return {"available": False, "items": []}
    q = q.strip()
    if len(q) < 3:
        raise HTTPException(400, "Запрос слишком короткий")
    pairs = semantic.search_ids(q, min(top, 50))
    conn = chgk_conn()
    try:
        return {"available": True, "items": _enrich_semantic_hits(conn, pairs)}
    finally:
        conn.close()


@app.get("/api/weak-topics")
def weak_topics():
    """Слабые темы по измерению на турнирах, а не по самооценке.

    Самооценка «знал / не знал» — слабый инструмент: она не отличает «не знал»
    от «знал, но не связал», и ей нужны десятки попыток на тему. Здесь берётся
    сравнение с полем на реально сыгранных вопросах: не взять то, что взяли 70%
    команд, — измеримая потеря. Файл готовит scripts/team_gap.py.
    """
    path = TEAM_GAPS_PATH
    if not path.exists():
        return {"available": False, "categories": []}

    data = json.loads(path.read_text(encoding="utf-8"))
    avg = data.get("per_question_avg", 0)
    conn = chgk_conn()
    try:
        by_name = {c["name_ru"]: c["id"] for c in get_all_categories(conn)}
    finally:
        conn.close()

    categories = []
    for c in data.get("categories", []):
        # Тема считается слабой, если проседает сильнее собственного среднего
        # команды и вопросов по ней набралось достаточно, чтобы это не был шум.
        weak = c["questions"] >= 8 and c["per_question"] < avg - 0.05
        categories.append(
            {
                **c,
                "category_id": by_name.get(c["category"]),
                "weak": weak,
            }
        )

    return {
        "available": True,
        "team_title": data.get("team_title"),
        "questions_total": data.get("questions_total"),
        "per_question_avg": avg,
        "tournaments": data.get("tournaments", []),
        "categories": categories,
    }


@app.get("/api/team/dossier")
def team_dossier():
    """Полное досье команды: история турниров, калибровочная кривая, модель,
    разрез по темам и приёмам. Файл готовит scripts/team_history.py."""
    if not TEAM_GAPS_PATH.exists():
        return {"available": False}
    data = json.loads(TEAM_GAPS_PATH.read_text(encoding="utf-8"))
    return {
        "available": True,
        "team_title": data.get("team_title"),
        "team_id": data.get("team_id"),
        "questions_total": data.get("questions_total"),
        "took": data.get("took"),
        "expected": data.get("expected"),
        "per_question_avg": data.get("per_question_avg"),
        "tournaments": data.get("tournaments", []),
        "calibration": data.get("calibration", []),
        "focus_band": data.get("focus_band"),
        "model": data.get("calibration_model"),
        "matched_questions": data.get("matched_questions"),
        "categories": data.get("categories", []),
        "techniques": data.get("techniques", []),
    }


@app.get("/api/team/forecast")
def team_forecast(pack: int = Query(..., description="pack_id из БД")):
    """Прогноз счёта команды на пакете по калибровочной модели + swing-вопросы."""
    if not TEAM_GAPS_PATH.exists():
        raise HTTPException(status_code=404, detail="нет профиля команды")
    data = json.loads(TEAM_GAPS_PATH.read_text(encoding="utf-8"))
    model = data.get("calibration_model")
    if not model:
        raise HTTPException(status_code=400, detail="в профиле нет модели")
    focus = data.get("focus_band", [0.35, 0.70])
    from scripts.team_history import forecast_pack

    conn = chgk_conn()
    try:
        f = forecast_pack(conn, model, focus, pack)
        title_row = conn.execute("SELECT title FROM packs WHERE id = ?", (pack,)).fetchone()
    finally:
        conn.close()
    if not f["questions"]:
        raise HTTPException(status_code=404, detail="у пакета нет статистики беручести")

    def slim(i: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "pos": i["pos"], "qid": i["qid"],
            "p": round(i["p"], 3), "tr": round(i["tr"], 3),
            "category": i["category"],
            "text": (i["text"] or "")[:200],
            "answer": (i["answer"] or "")[:100],
        }

    return {
        "pack_id": f["pack_id"],
        "pack_title": title_row["title"] if title_row else str(pack),
        "questions": f["questions"],
        "expected": round(f["expected"], 1),
        "field_avg": round(f["field_avg"], 1),
        "focus_band": focus,
        "bankers": len(f["bankers"]),
        "swing": [slim(i) for i in f["swing"][:20]],
    }


@app.get("/api/study/canon")
def study_canon(category_id: Optional[int] = None, limit: int = 40):
    """Канон темы: повторяющиеся ответы, которые нужно знать (что учить)."""
    conn = chgk_conn()
    try:
        items = study.canon(conn, category_id, limit=limit)
        cats = {c["id"]: c["name_ru"] for c in get_all_categories(conn)}
    finally:
        conn.close()
    return {
        "category_id": category_id,
        "category": cats.get(category_id) if category_id else None,
        "items": items,
    }


FACT_CARDS_PATH = PROJECT_ROOT / "data" / "facts" / "fact_cards.json"


def _load_fact_card(answer: str) -> Optional[dict]:
    """Сгенерированная карточка (ядро + зацепки), если есть. Файл — gen_facts.py."""
    if not FACT_CARDS_PATH.exists():
        return None
    try:
        cards = json.loads(FACT_CARDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    card = cards.get(study.normalize_answer(answer))
    if not card:
        return None
    # В учебник — только подтверждённые заземлением зацепки.
    hooks = [h for h in card.get("hooks", []) if h.get("grounded")]
    return {"core": card.get("core", ""), "hooks": hooks}


@app.get("/api/study/fact")
def study_fact(answer: str = Query(..., description="ответ (канон-ключ)")):
    """Досье факта: карточка (ядро+зацепки) + все углы вопросов с разбором."""
    conn = chgk_conn()
    try:
        dossier = study.fact_dossier(conn, answer)
    finally:
        conn.close()
    dossier["card"] = _load_fact_card(answer)
    return dossier


@app.get("/api/topics")
def topics(model_name: Optional[str] = None):
    user_id = require_current_user().id
    conn = chgk_conn()
    try:
        return {"categories": catalog.topics_tree(conn, user_id, model_name)}
    finally:
        conn.close()


@app.get("/api/tournaments")
def tournaments(search: str = "", limit: int = 20):
    conn = chgk_conn()
    try:
        if search.strip():
            return {"items": engine.search_tournaments(conn, search, limit)}
        return {"items": engine.get_recent_tournaments(conn, limit)}
    finally:
        conn.close()


# ─── Тренировка ───────────────────────────────────────────────────

class StartRequest(BaseModel):
    mode: str = "random"
    count: int = 12
    seed: Optional[int] = None
    category_ids: Optional[List[int]] = None
    difficulty_min: Optional[float] = None
    difficulty_max: Optional[float] = None
    pack_id: Optional[int] = None
    tour_number: Optional[int] = None
    technique: Optional[str] = None
    answer: Optional[str] = None


@app.post("/api/training/start")
def training_start(req: StartRequest):
    user_id = require_current_user().id
    conn = chgk_conn()
    tconn = get_training_connection()
    try:
        rng = None
        if req.difficulty_min is not None and req.difficulty_max is not None:
            rng = (req.difficulty_min, req.difficulty_max)

        if req.mode == "random":
            s = engine.start_random(conn, req.count, req.seed, rng, technique=req.technique)
        elif req.mode == "category":
            if not req.category_ids:
                raise HTTPException(400, "Не выбрана ни одна категория")
            s = engine.start_by_category(conn, req.category_ids, req.count, req.seed, rng, technique=req.technique)
        elif req.mode == "tournament":
            if not req.pack_id:
                raise HTTPException(400, "Не выбран турнир")
            s = engine.start_by_tournament(conn, req.pack_id, req.count, req.seed, req.tour_number)
        elif req.mode == "weak":
            if not req.category_ids:
                raise HTTPException(400, "Не выбрана ни одна тема")
            s = engine.start_weak_topics(
                conn, tconn, user_id, req.category_ids, req.count, req.seed, rng,
                technique=req.technique,
            )
        elif req.mode == "team_gap":
            s = engine.start_team_gap(conn, tconn, user_id, req.count, req.seed)
        elif req.mode == "followup":
            s = engine.start_followup(conn, tconn, user_id, req.count)
        elif req.mode == "study_fact":
            if not req.answer:
                raise HTTPException(400, "Не указан факт для тренировки")
            s = engine.start_by_answer(conn, req.answer, req.count, req.seed)
        elif req.mode == "review":
            s = engine.start_review(conn, tconn, user_id, req.count)
        else:
            raise HTTPException(400, f"Неизвестный режим: {req.mode}")

        if not s.questions:
            raise HTTPException(
                404, "По выбранным фильтрам не нашлось вопросов"
            )

        session_id = uuid.uuid4().hex[:12]
        _SESSIONS[session_id] = (user_id, s)
        return _state(session_id, s)
    finally:
        conn.close()
        tconn.close()


@app.get("/api/training/{session_id}")
def training_state(session_id: str):
    return _state(session_id, _session_or_404(session_id))


class AnswerRequest(BaseModel):
    user_answer: str = ""


@app.post("/api/training/{session_id}/reveal")
def training_reveal(session_id: str, req: AnswerRequest):
    s = _session_or_404(session_id)
    engine.submit_answer(s, req.user_answer)
    return _state(session_id, s)


class GradeRequest(BaseModel):
    knew: bool


@app.post("/api/training/{session_id}/grade")
def training_grade(session_id: str, req: GradeRequest):
    """Записать самооценку. Пишет в training.db через тот же движок, что и бот."""
    s = _session_or_404(session_id)
    tconn = get_training_connection()
    try:
        engine.record_and_advance(
            s, tconn, require_current_user().id, req.knew
        )
        return _state(session_id, s)
    finally:
        tconn.close()


@app.post("/api/training/{session_id}/abort")
def training_abort(session_id: str):
    s = _session_or_404(session_id)
    s.index = len(s.questions)  # завершить: уже записанные попытки сохранены
    return _state(session_id, s)
