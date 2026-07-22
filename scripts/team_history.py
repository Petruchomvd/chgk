"""Полное досье команды из rating.chgk — автоматически, по всем турнирам состава.

Отличие от team_gap.py: тот берёт руками указанные пакеты (--packs/--rating) и
считает разрез по темам только на них (n=2..12 на категорию — шатко). Здесь мы
тянем ВСЕ турниры команды из api.rating.chgk.info и считаем главное — дефицит
к полю — прямо по маскам всех команд, без привязки к нашей БД. Это даёт втрое
больше сыгранных вопросов и статистически надёжную калибровочную кривую.

Что считается:
  - по каждому турниру: счёт, ожидание поля (сумма беручести по позициям),
    дефицит; беручесть поля берётся из масок всех команд рейтинга;
  - калибровочная кривая: дефицит команды по полосам беручести поля — где именно
    команда теряет очки (лёгкие/средние/трудные вопросы);
  - focus_band: полоса сложности, куда попадает основной дефицит (для тренировок);
  - разрез по темам/приёмам — только для турниров, чей пакет нашёлся в нашей БД
    (матчинг по корреляции беручести, порог 0.85).

Пишет:
  - data/team/team-{id}-gaps.json — совместимо со старой схемой (tournaments,
    categories, techniques, ...) плюс блоки calibration, focus_band, per_tournament;
  - docs/reports/team-{id}-history.md — человекочитаемый отчёт.

Использование:
    python scripts/team_history.py --team-id 97700
    python scripts/team_history.py --team-id 97700 --label-model "google/gemma-4-26b-a4b-it@p2"
    python scripts/team_history.py --team-id 97700 --map 12916:6666,12781:6862,10288:6602
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BASE_URL, DB_PATH, PROJECT_ROOT
from database.db import get_readonly_connection

RATING_API = "https://api.rating.chgk.info"
USER_AGENT = "Mozilla/5.0 (chgk-analysis; research)"
REPORT_DIR = PROJECT_ROOT / "docs" / "reports"
DATA_DIR = PROJECT_ROOT / "data" / "team"
DEFAULT_LABEL_MODEL = "google/gemma-4-26b-a4b-it@p2"

# Порог корреляции беручести для признания «это тот же пакет».
MATCH_CORR = 0.85
# Полосы беручести поля для калибровочной кривой.
BANDS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]


# ── rating.chgk ────────────────────────────────────────────────────────────

def _get(session: requests.Session, path: str, **params) -> object:
    resp = session.get(
        f"{RATING_API}{path}",
        params=params or None,
        headers={"User-Agent": USER_AGENT},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def team_tournaments(session: requests.Session, team_id: int) -> List[int]:
    data = _get(session, f"/teams/{team_id}/tournaments")
    if isinstance(data, list):
        return [t["idtournament"] for t in data if "idtournament" in t]
    return []


def tournament_info(session: requests.Session, tid: int) -> dict:
    d = _get(session, f"/tournaments/{tid}")
    return d if isinstance(d, dict) else {}


def tournament_results(session: requests.Session, tid: int) -> List[dict]:
    d = _get(session, f"/tournaments/{tid}/results", includeMasksAndControversials=1)
    return d if isinstance(d, list) else []


# ── gotquestions: маска пакета, если рейтинг её больше не отдаёт ──────────────

def gq_took_by_position(session: requests.Session, pack_id: int, team_id: int) -> Dict[int, bool]:
    """position (1-based) -> взят ли вопрос, из таблицы gotquestions /table/{pack}."""
    resp = session.get(f"{BASE_URL}/table/{pack_id}", timeout=120,
                       headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    text = resp.text.replace('\\"', '"')
    anchor = f'"team":{{"id":{team_id},'
    start = text.find(anchor)
    if start == -1:
        return {}
    m = re.search(r'"mask":"([01]+)"', text[start:start + 700])
    if not m:
        return {}
    mask = m.group(1)
    return {i + 1: c == "1" for i, c in enumerate(mask)}


# ── анализ одного турнира по маскам ──────────────────────────────────────────

def analyse_tournament(session: requests.Session, tid: int, team_id: int) -> Optional[dict]:
    """Дефицит команды к полю по маскам. Возвращает None, если масок нет."""
    info = tournament_info(session, tid)
    res = tournament_results(session, tid)
    masks = [t["mask"] for t in res if t.get("mask") and set(t["mask"]) <= set("01X")]
    mine = next((t for t in res if (t.get("team") or {}).get("id") == team_id), None)
    if not masks or not mine or not mine.get("mask"):
        return None

    mm = mine["mask"]
    L = max(len(m) for m in masks)
    mask_len = len(mm)
    per_q: List[dict] = []  # {position, field_rate, took}
    for i in range(min(len(mm), L)):
        if mm[i] not in "01":
            continue  # вопрос командой не игрался или снят
        col = [m[i] for m in masks if i < len(m) and m[i] in "01"]
        if len(col) < 5:
            continue  # мало данных поля на этой позиции
        rate = sum(c == "1" for c in col) / len(col)
        per_q.append({"position": i + 1, "field_rate": rate, "took": mm[i] == "1"})

    if not per_q:
        return None
    took = sum(1 for q in per_q if q["took"])
    expected = sum(q["field_rate"] for q in per_q)
    return {
        "tid": tid,
        "name": (info.get("name") or "").strip() or str(tid),
        "date": (info.get("dateStart") or "")[:10],
        "mask_len": mask_len,
        "played": len(per_q),
        "took": took,
        "expected": expected,
        "deficit": took - expected,
        "per_q": per_q,
    }


# ── матчинг турнира к пакету БД ──────────────────────────────────────────────

def _corr(a: List[float], b: List[float]) -> float:
    n = len(a)
    if n < 3:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    sa = sum((v - ma) ** 2 for v in a) ** 0.5
    sb = sum((v - mb) ** 2 for v in b) ** 0.5
    return cov / (sa * sb) if sa and sb else 0.0


def db_pack_take_rates(conn, pack_id: int) -> Dict[int, float]:
    """result_position -> take_rate для пакета БД."""
    rows = conn.execute(
        "SELECT result_position, take_rate FROM question_result_stats WHERE pack_id = ?",
        (pack_id,),
    ).fetchall()
    return {r["result_position"]: r["take_rate"] for r in rows}


def _days_between(a: str, b: str) -> int:
    """Грубая разница дат YYYY-MM-DD в днях; при кривом вводе — большое число."""
    try:
        ya, ma, da = (int(x) for x in a[:10].split("-"))
        yb, mb, db = (int(x) for x in b[:10].split("-"))
        return abs((ya * 365 + ma * 30 + da) - (yb * 365 + mb * 30 + db))
    except Exception:
        return 10 ** 6


def candidate_packs(conn, mask_len: int, date: str, limit: int = 60) -> List[int]:
    """Пакеты с тем же числом вопросов (±3), ближайшие по дате.

    Язык-независимо (не зависит от кириллического LIKE); решает корреляция.
    """
    rows = conn.execute(
        """
        SELECT s.pack_id AS pid, COUNT(*) AS c, MIN(p.start_date) AS d
        FROM question_result_stats s JOIN packs p ON p.id = s.pack_id
        GROUP BY s.pack_id
        HAVING c BETWEEN ? AND ?
        """,
        (mask_len - 3, mask_len + 3),
    ).fetchall()
    rows.sort(key=lambda r: _days_between(r["d"] or "", date))
    return [r["pid"] for r in rows[:limit]]


def find_matching_pack(conn, tourn: dict, forced: Optional[int]) -> Optional[Tuple[int, float]]:
    """Ищет пакет БД, чья беручесть по позициям коррелирует с полем турнира."""
    field = {q["position"]: q["field_rate"] for q in tourn["per_q"]}

    if forced is not None:
        candidates = [forced]
    else:
        candidates = candidate_packs(conn, tourn["mask_len"], tourn["date"])

    best = None
    for pid in candidates:
        db_rates = db_pack_take_rates(conn, pid)
        common = sorted(set(field) & set(db_rates))
        if len(common) < max(10, int(0.5 * len(field))):
            continue
        c = _corr([field[p] for p in common], [db_rates[p] for p in common])
        if c >= MATCH_CORR and (best is None or c > best[1]):
            best = (pid, c)
    return best


def pack_labels(conn, pack_id: int, label_model: Optional[str]) -> Dict[int, dict]:
    """result_position -> {question_id, categories:[..], take_rate}."""
    clause = " AND qt.model_name = ?" if label_model else ""
    params: list = [pack_id]
    if label_model:
        params = [label_model, pack_id]
    rows = conn.execute(
        f"""
        SELECT s.result_position AS pos, s.question_id AS qid, s.take_rate AS tr,
               c.name_ru AS category
        FROM question_result_stats s
        LEFT JOIN question_topics qt ON qt.question_id = s.question_id{clause}
        LEFT JOIN subcategories sc ON sc.id = qt.subcategory_id
        LEFT JOIN categories c ON c.id = sc.category_id
        WHERE s.pack_id = ?
        """,
        params,
    ).fetchall()
    by_pos: Dict[int, dict] = {}
    for r in rows:
        e = by_pos.setdefault(r["pos"], {"question_id": r["qid"], "take_rate": r["tr"], "categories": []})
        if r["category"] and r["category"] not in e["categories"]:
            e["categories"].append(r["category"])
    return by_pos


# ── калибровка и focus_band ──────────────────────────────────────────────────

def calibration(points: List[dict]) -> List[dict]:
    """points: [{field_rate, took}]. Дефицит команды по полосам беручести."""
    out = []
    for lo, hi in BANDS:
        sub = [p for p in points if lo <= p["field_rate"] < hi]
        if not sub:
            continue
        n = len(sub)
        fr = sum(p["field_rate"] for p in sub) / n
        tr = sum(1 for p in sub if p["took"]) / n
        out.append({
            "band": [round(lo, 2), round(min(hi, 1.0), 2)],
            "questions": n,
            "field_rate": round(fr, 3),
            "team_rate": round(tr, 3),
            "lift": round(tr - fr, 3),
            "deficit": round(sum((1 if p["took"] else 0) - p["field_rate"] for p in sub), 2),
        })
    return out


def _logit(x: float, eps: float = 1e-3) -> float:
    x = min(1 - eps, max(eps, x))
    import math
    return math.log(x / (1 - x))


def fit_calibration_model(points: List[dict]) -> Optional[dict]:
    """Логистическая регрессия P(команда берёт) = sigmoid(a + b·logit(беручесть)).

    Одна фича — беручесть поля в логит-шкале. b>1 значит, что команда «острее»
    поля (лучше на лёгких, хуже на трудных), a<0 — общий недобор. Newton/IRLS.
    """
    try:
        import numpy as np
    except Exception:
        return None
    if len(points) < 30:
        return None
    fl = np.array([_logit(p["field_rate"]) for p in points])
    y = np.array([1.0 if p["took"] else 0.0 for p in points])
    X = np.column_stack([np.ones_like(fl), fl])
    w = np.zeros(2)
    for _ in range(50):
        p = 1 / (1 + np.exp(-X @ w))
        Wd = p * (1 - p) + 1e-9
        grad = X.T @ (p - y)
        H = X.T @ (X * Wd[:, None])
        try:
            w = w - np.linalg.solve(H + 1e-6 * np.eye(2), grad)
        except Exception:
            break
    return {"a": round(float(w[0]), 4), "b": round(float(w[1]), 4), "n": len(points)}


def predict_take(model: dict, field_rate: float) -> float:
    """P(команда берёт вопрос) по беручести поля."""
    import math
    z = model["a"] + model["b"] * _logit(field_rate)
    return 1 / (1 + math.exp(-z))


def compute_focus_band(points: List[dict]) -> List[float]:
    """Наименьшая полоса из дециль-краёв, вбирающая >=60% отрицательного дефицита."""
    edges = [i / 10 for i in range(11)]
    dec_def = []
    for k in range(10):
        lo, hi = edges[k], edges[k + 1] + (0.01 if k == 9 else 0)
        sub = [p for p in points if lo <= p["field_rate"] < hi]
        d = sum((1 if p["took"] else 0) - p["field_rate"] for p in sub)
        dec_def.append(min(d, 0.0))  # только недобор
    total = sum(dec_def)
    if total >= 0:
        return [0.35, 0.70]  # дефицита нет — дефолт
    best = None
    for i in range(10):
        for j in range(i, 10):
            s = sum(dec_def[i:j + 1])
            if s <= 0.60 * total:  # оба отрицательные: <= значит вобрали >=60%
                width = j - i
                if best is None or width < best[0]:
                    best = (width, edges[i], edges[j + 1])
                break
    if not best:
        return [0.20, 0.65]
    lo = max(0.10, round(best[1], 2))
    hi = min(0.90, round(best[2], 2))
    return [lo, hi]


# ── сборка JSON и отчёта ─────────────────────────────────────────────────────

def build_profile(conn, team_id: int, tourns: List[dict],
                  category_sources: List[Tuple[int, Dict[int, bool]]],
                  label_model: Optional[str]) -> dict:
    team_title = ""
    # все mask-вопросы для калибровки
    all_points = [q for t in tourns for q in t["per_q"]]
    calib = calibration(all_points)
    focus = compute_focus_band(all_points)
    model = fit_calibration_model(all_points)

    total = len(all_points)
    took = sum(1 for p in all_points if p["took"])
    expected = sum(p["field_rate"] for p in all_points)

    # категории/приёмы — по пакетам, чью маску удалось получить (нужна
    # классификация из БД): рейтинг-матчи + gotquestions-фолбэк.
    tech_map = {
        r["question_id"]: r["primary_technique"]
        for r in conn.execute("SELECT question_id, primary_technique FROM question_techniques")
    }
    by_cat: Dict[str, List[dict]] = defaultdict(list)
    by_tech: Dict[str, List[dict]] = defaultdict(list)
    matched_q = 0
    seen_qid: set = set()
    for pid, took_by_pos in category_sources:
        labels = pack_labels(conn, pid, label_model)
        for pos, lab in labels.items():
            if pos not in took_by_pos or lab["question_id"] in seen_qid:
                continue
            seen_qid.add(lab["question_id"])
            rec = {"take_rate": lab["take_rate"], "took": took_by_pos[pos], "qid": lab["question_id"]}
            matched_q += 1
            for cat in lab["categories"] or []:
                by_cat[cat].append(rec)
            by_tech[tech_map.get(lab["question_id"], "чистый")].append(rec)

    def agg(groups: Dict[str, List[dict]], key: str) -> List[dict]:
        out = []
        for name, items in groups.items():
            exp = sum(i["take_rate"] for i in items)
            got = sum(1 for i in items if i["took"])
            out.append({
                key: name,
                "questions": len(items),
                "took": got,
                "expected": round(exp, 2),
                "deficit": round(got - exp, 2),
                "per_question": round((got - exp) / len(items), 3),
            })
        out.sort(key=lambda c: c["per_question"])
        return out

    return {
        "team_id": team_id,
        "team_title": team_title,
        "source": "rating.chgk masks (team_history.py)",
        "tournaments": [
            {"title": t["name"], "date": t["date"], "score": t["took"],
             "questions": t["played"], "expected": round(t["expected"], 1),
             "deficit": round(t["deficit"], 1),
             "matched_pack": t.get("matched_pack")}
            for t in tourns
        ],
        "questions_total": total,
        "took": took,
        "expected": round(expected, 2),
        "per_question_avg": round((took - expected) / total, 3) if total else 0,
        "calibration": calib,
        "calibration_model": model,
        "focus_band": focus,
        "matched_questions": matched_q,
        "categories": agg(by_cat, "category"),
        "techniques": agg(by_tech, "technique"),
    }


def build_report(profile: dict) -> str:
    a: List[str] = []
    a.append(f"# История команды {profile['team_id']} — {profile.get('team_title') or ''}\n")
    a.append(f"Источник: {profile['source']}. Дефицит = взято − ожидание поля "
             f"(беручесть всех команд на тех же вопросах).\n")
    a.append(f"**Итого:** {profile['questions_total']} вопросов, взято {profile['took']}, "
             f"ожидание поля {profile['expected']:.1f}, "
             f"общий дефицит **{profile['took'] - profile['expected']:+.1f}** "
             f"({profile['per_question_avg']:+.3f} на вопрос).\n")

    a.append("## По турнирам\n")
    a.append("| Турнир | Дата | Сыграно | Взято | Поле | Дефицит | Пакет БД |")
    a.append("|---|---|--:|--:|--:|--:|--:|")
    for t in profile["tournaments"]:
        a.append(f"| {t['title'][:44]} | {t['date']} | {t['questions']} | {t['score']} | "
                 f"{t['expected']:.1f} | {t['deficit']:+.1f} | {t['matched_pack'] or '—'} |")

    a.append("\n## Калибровочная кривая (где теряем очки)\n")
    a.append("| Полоса беручести | Вопросов | Поле | Команда | Отклонение | Дефицит |")
    a.append("|---|--:|--:|--:|--:|--:|")
    for c in profile["calibration"]:
        a.append(f"| {c['band'][0]:.1f}–{c['band'][1]:.1f} | {c['questions']} | "
                 f"{c['field_rate']*100:.0f}% | {c['team_rate']*100:.0f}% | "
                 f"{c['lift']*100:+.1f} | {c['deficit']:+.1f} |")
    fb = profile["focus_band"]
    a.append(f"\n**Зона фокуса тренировок (focus_band):** {fb[0]:.2f}–{fb[1]:.2f} беручести — "
             f"сюда попадает основной недобор.\n")

    if profile["categories"]:
        a.append(f"## По темам (на {profile['matched_questions']} вопросах из пакетов в БД)\n")
        a.append("| Тема | Вопросов | Взято | Поле | Дефицит | На вопрос |")
        a.append("|---|--:|--:|--:|--:|--:|")
        for c in profile["categories"][:12]:
            a.append(f"| {c['category']} | {c['questions']} | {c['took']} | "
                     f"{c['expected']:.1f} | {c['deficit']:+.1f} | {c['per_question']:+.3f} |")

    if profile["techniques"]:
        a.append("\n## По приёмам\n")
        a.append("| Приём | Вопросов | Взято | Поле | Дефицит | На вопрос |")
        a.append("|---|--:|--:|--:|--:|--:|")
        for c in profile["techniques"]:
            a.append(f"| {c['technique']} | {c['questions']} | {c['took']} | "
                     f"{c['expected']:.1f} | {c['deficit']:+.1f} | {c['per_question']:+.3f} |")

    return "\n".join(a) + "\n"


# ── прогноз счёта на пакет и валидация модели ────────────────────────────────

def forecast_pack(conn, model: dict, focus_band: List[float], pack_id: int) -> dict:
    """Прогноз счёта команды на пакете по калибровочной модели."""
    rows = conn.execute(
        """
        SELECT s.result_position AS pos, s.question_id AS qid, s.take_rate AS tr,
               q.text AS text, q.answer AS answer, c.name_ru AS category
        FROM question_result_stats s
        JOIN questions q ON q.id = s.question_id
        LEFT JOIN question_topics qt ON qt.question_id = s.question_id
            AND qt.model_name = ?
        LEFT JOIN subcategories sc ON sc.id = qt.subcategory_id
        LEFT JOIN categories c ON c.id = sc.category_id
        WHERE s.pack_id = ?
        ORDER BY s.result_position
        """,
        (DEFAULT_LABEL_MODEL, pack_id),
    ).fetchall()
    seen = set()
    items = []
    for r in rows:
        if r["qid"] in seen:
            continue
        seen.add(r["qid"])
        p = predict_take(model, r["tr"])
        items.append({"pos": r["pos"], "qid": r["qid"], "tr": r["tr"], "p": p,
                      "category": r["category"], "text": r["text"], "answer": r["answer"]})
    expected = sum(i["p"] for i in items)
    field_avg = sum(i["tr"] for i in items)
    lo, hi = focus_band
    swing = sorted([i for i in items if lo <= i["tr"] <= hi and 0.30 <= i["p"] <= 0.70],
                   key=lambda i: abs(i["p"] - 0.5))
    bankers = [i for i in items if i["p"] >= 0.85]
    return {
        "pack_id": pack_id, "questions": len(items),
        "expected": expected, "field_avg": field_avg,
        "swing": swing, "bankers": bankers,
    }


def validate_loo(tourns: List[dict]) -> None:
    """Leave-one-tournament-out: обучаемся на остальных, предсказываем счёт турнира."""
    print("\nВалидация модели (leave-one-tournament-out):")
    print(f"{'турнир':<44} {'факт':>5} {'прогноз':>8} {'ошибка':>7}")
    errs = []
    for held in tourns:
        train = [q for t in tourns if t["tid"] != held["tid"] for q in t["per_q"]]
        m = fit_calibration_model(train)
        if not m:
            continue
        pred = sum(predict_take(m, q["field_rate"]) for q in held["per_q"])
        actual = held["took"]
        errs.append(abs(pred - actual))
        print(f"{held['name'][:44]:<44} {actual:>5} {pred:>8.1f} {pred-actual:>+7.1f}")
    if errs:
        print(f"Средняя абсолютная ошибка прогноза: {sum(errs)/len(errs):.2f} вопроса "
              f"на турнир ({len(errs)} турниров)")


def _run_forecast(args) -> int:
    profile_path = Path(args.json_path or (DATA_DIR / f"team-{args.team_id}-gaps.json"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    model = profile.get("calibration_model")
    if not model:
        print("В профиле нет calibration_model — сначала прогони без --forecast.")
        return 1
    focus = profile.get("focus_band", [0.35, 0.70])
    conn = get_readonly_connection(args.db)
    try:
        f = forecast_pack(conn, model, focus, args.forecast)
    finally:
        conn.close()
    print(f"Прогноз на пакет {f['pack_id']} ({f['questions']} вопросов):")
    print(f"  ожидаемый счёт команды: {f['expected']:.1f}")
    print(f"  средняя команда поля:   {f['field_avg']:.1f}  "
          f"(наш перевес {f['expected']-f['field_avg']:+.1f})")
    print(f"  «банкеры» (P>=85%): {len(f['bankers'])} — терять нельзя")
    print(f"\n  Swing-вопросы (решают исход, {len(f['swing'])} шт., "
          f"P~50% в зоне фокуса {focus[0]}–{focus[1]}):")
    for i in f["swing"][:12]:
        cat = i["category"] or "—"
        print(f"    #{i['pos']:>2} P={i['p']*100:>4.0f}% [{cat[:14]:<14}] "
              f"{i['text'][:60]}… → {i['answer'][:30]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-id", type=int, required=True)
    ap.add_argument("--label-model", default=DEFAULT_LABEL_MODEL)
    ap.add_argument("--map", default="", help="ручной матчинг tid:pack_id через запятую")
    ap.add_argument("--gq-packs", default="6602",
                    help="доп. пакеты БД, чью маску тянуть с gotquestions (для разреза "
                         "по темам, если рейтинг масок не отдаёт); pack_id через запятую")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--json-path", default="")
    ap.add_argument("--report-path", default="")
    ap.add_argument("--forecast", type=int, default=0,
                    help="прогноз счёта на пакет БД по сохранённой модели (не тянет рейтинг)")
    ap.add_argument("--validate", action="store_true",
                    help="leave-one-tournament-out проверка прогноза")
    args = ap.parse_args()

    if args.forecast:
        return _run_forecast(args)

    forced: Dict[int, int] = {}
    for pair in filter(None, args.map.split(",")):
        tid, pid = pair.split(":")
        forced[int(tid)] = int(pid)

    session = requests.Session()
    conn = get_readonly_connection(args.db)
    try:
        tids = team_tournaments(session, args.team_id)
        print(f"Турниров у команды: {len(tids)} — {tids}")
        tourns: List[dict] = []
        matched: Dict[int, Tuple[int, float]] = {}
        team_title = ""
        for tid in tids:
            t = analyse_tournament(session, tid, args.team_id)
            if not t:
                print(f"  {tid}: масок нет — пропуск")
                continue
            res = tournament_results(session, tid)
            mine = next((x for x in res if (x.get("team") or {}).get("id") == args.team_id), None)
            if mine:
                team_title = (mine.get("current") or mine.get("team") or {}).get("name", team_title)
            m = find_matching_pack(conn, t, forced.get(tid))
            if m:
                matched[tid] = m
                t["matched_pack"] = m[0]
                print(f"  {tid}: {t['name'][:40]:<40} {t['took']}/{t['played']} "
                      f"деф {t['deficit']:+.1f}  → пакет {m[0]} (corr {m[1]:.3f})")
            else:
                print(f"  {tid}: {t['name'][:40]:<40} {t['took']}/{t['played']} "
                      f"деф {t['deficit']:+.1f}  (пакет в БД не найден)")
            tourns.append(t)

        if not tourns:
            print("Нет турниров с масками — нечего анализировать.")
            return 1

        # Источники для разреза по темам: рейтинг-матчи + gotquestions-фолбэк.
        category_sources: List[Tuple[int, Dict[int, bool]]] = []
        for t in tourns:
            if t.get("matched_pack"):
                category_sources.append(
                    (t["matched_pack"], {q["position"]: q["took"] for q in t["per_q"]})
                )
        gq_ids = [int(x) for x in filter(None, args.gq_packs.split(","))]
        matched_pids = {t.get("matched_pack") for t in tourns}
        for pid in gq_ids:
            if pid in matched_pids:
                continue  # уже есть из рейтинга
            try:
                took_by_pos = gq_took_by_position(session, pid, args.team_id)
            except Exception as e:
                print(f"  gq-пакет {pid}: ошибка {e}")
                continue
            if took_by_pos:
                category_sources.append((pid, took_by_pos))
                print(f"  gq-пакет {pid}: маска получена ({sum(took_by_pos.values())}/"
                      f"{len(took_by_pos)}) — добавлен в разрез по темам")
            else:
                print(f"  gq-пакет {pid}: маску команды не нашёл")

        profile = build_profile(conn, args.team_id, tourns, category_sources, args.label_model)
        profile["team_title"] = team_title
        if args.validate:
            validate_loo(tourns)
    finally:
        conn.close()

    json_path = Path(args.json_path or (DATA_DIR / f"team-{args.team_id}-gaps.json"))
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")

    report_path = Path(args.report_path or (REPORT_DIR / f"team-{args.team_id}-history.md"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(profile), encoding="utf-8")

    print(f"\nПрофиль: {json_path}")
    print(f"Отчёт:   {report_path}")
    print(f"focus_band: {profile['focus_band']}  matched_q: {profile['matched_questions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
