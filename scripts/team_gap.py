"""Тематические провалы конкретной команды.

Берёт маску команды со страницы результатов gotquestions, связывает её с
вопросами и классификацией и показывает, на каких темах команда отстаёт
от поля.

Ключевая мысль: сравнивать надо не «сколько взяли», а «сколько взяли по
сравнению с полем на тех же вопросах». Не взять вопрос, который не взял никто,
— не потеря. Не взять вопрос, который взяли все, — потеря позиции.

Источники масок:
  - gotquestions /table/{pack} — для турниров, сыгранных через gotquestions;
  - api.rating.chgk.info — для очных турниров, которых там нет.

Соответствие «турнир рейтинга ↔ пакет базы» не берётся на веру: оно проверяется
корреляцией беручести по вопросам. Совпадение пакета даёт ~0.96, чужой пакет —
около нуля.

Использование:
    python scripts/team_gap.py --team-id 97700 --packs 6666,6862
    python scripts/team_gap.py --team-id 97700 --packs 6666,6862 --rating 11844:6602
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BASE_URL, DB_PATH, PROJECT_ROOT, SCRAPE_DELAY
from database.db import get_readonly_connection

USER_AGENT = "Mozilla/5.0 (chgk-analysis; research)"
REPORT_DIR = PROJECT_ROOT / "docs" / "reports"
DATA_DIR = PROJECT_ROOT / "data" / "team"

# Порог «лёгкого» вопроса: взяли не менее 70% команд. Такой же, как в анализе
# команды в korolevskie_osobi_handoff — чтобы цифры были сопоставимы.
EASY_TAKE_RATE = 0.70

# Какими метками пользоваться. None — любыми (как раньше); имя модели —
# только её метками. Ставится из --label-model, чтобы не мешать метки разных
# моделей на одном вопросе.
LABEL_MODEL: Optional[str] = None


def fetch_table(pack_id: int, *, session: requests.Session) -> str:
    url = f"{BASE_URL}/table/{pack_id}"
    resp = session.get(url, timeout=120, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def find_team_mask(html: str, team_id: int) -> Optional[Tuple[str, str, int]]:
    """Найти маску команды: (название, маска, заявленный счёт).

    Страница — Next.js payload с экранированным JSON, поэтому ищем запись
    команды по её ID и берём ближайшую маску.
    """
    text = html.replace('\\"', '"')
    anchor = f'"team":{{"id":{team_id},'
    start = text.find(anchor)
    if start == -1:
        return None

    window = text[start : start + 700]
    title_match = re.search(r'"title":"([^"]+)"', window)
    mask_match = re.search(r'"mask":"([01]+)"', window)
    total_match = re.search(r'"questionsTotal":(\d+)', window)
    if not mask_match:
        return None
    return (
        title_match.group(1) if title_match else f"team {team_id}",
        mask_match.group(1),
        int(total_match.group(1)) if total_match else -1,
    )


RATING_API = "https://api.rating.chgk.info"


def _corr(a: List[float], b: List[float]) -> float:
    n = len(a)
    if n < 3:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    sa = sum((v - ma) ** 2 for v in a) ** 0.5
    sb = sum((v - mb) ** 2 for v in b) ** 0.5
    return cov / (sa * sb) if sa and sb else 0.0


def fetch_rating_results(tournament_id: int, *, session: requests.Session) -> List[dict]:
    resp = session.get(
        f"{RATING_API}/tournaments/{tournament_id}/results",
        params={"includeMasksAndControversials": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def analyse_rating_pack(
    conn, tournament_id: int, pack_id: int, team_id: int, *, session
) -> Optional[dict]:
    """Маска из рейтинга + вопросы из базы, с проверкой, что это тот же пакет."""
    data = fetch_rating_results(tournament_id, session=session)
    mine = next((t for t in data if (t.get("team") or {}).get("id") == team_id), None)
    if not mine or not mine.get("mask"):
        return None

    questions = pack_questions(conn, pack_id)
    mask = mine["mask"]
    if len(mask) != len(questions):
        print(f"  длина маски {len(mask)} != вопросов {len(questions)} — пропускаю")
        return None

    # Проверка соответствия: беручесть по маскам рейтинга против беручести базы
    masks = [t["mask"] for t in data if t.get("mask") and len(t["mask"]) == len(mask)]
    rating_rates, gq_rates = [], []
    for i in range(len(mask)):
        played = [m[i] for m in masks if m[i] in "01"]
        if not played or (i + 1) not in questions:
            continue
        rating_rates.append(sum(c == "1" for c in played) / len(played))
        gq_rates.append(questions[i + 1]["take_rate"])
    correlation = _corr(rating_rates, gq_rates)
    if correlation < 0.85:
        print(
            f"  корреляция беручести {correlation:+.3f} — это другой пакет, пропускаю"
        )
        return None

    pack = conn.execute(
        "SELECT title, start_date FROM packs WHERE id = ?", (pack_id,)
    ).fetchone()

    results = []
    for pos, question in questions.items():
        symbol = mask[pos - 1]
        if symbol not in "01":
            continue  # вопрос командой не игрался или снят
        results.append({**question, "position": pos, "took": symbol == "1"})

    return {
        "pack_id": pack_id,
        "pack_title": pack["title"] if pack else str(pack_id),
        "date": (pack["start_date"] or "")[:10] if pack else "",
        "team_title": (mine.get("current") or mine["team"]).get("name", str(team_id)),
        "mask": mask,
        "declared_total": mine.get("questionsTotal", -1),
        "score": sum(1 for r in results if r["took"]),
        "results": results,
        "correlation": correlation,
    }


def pack_questions(conn, pack_id: int) -> Dict[int, dict]:
    """Вопросы пакета по позиции в маске результатов."""
    label_clause = " AND qt.model_name = ?" if LABEL_MODEL else ""
    params: list = [pack_id] if not LABEL_MODEL else [LABEL_MODEL, pack_id]
    rows = conn.execute(
        f"""
        SELECT s.result_position, s.question_id, s.take_rate, s.correct_teams,
               s.total_teams, q.text, q.answer,
               c.name_ru AS category, sc.name_ru AS subcategory
        FROM question_result_stats s
        JOIN questions q ON q.id = s.question_id
        LEFT JOIN question_topics qt ON qt.question_id = q.id{label_clause}
        LEFT JOIN subcategories sc ON sc.id = qt.subcategory_id
        LEFT JOIN categories c ON c.id = sc.category_id
        WHERE s.pack_id = ?
        """,
        params,
    ).fetchall()

    by_position: Dict[int, dict] = {}
    for row in rows:
        pos = row["result_position"]
        entry = by_position.setdefault(
            pos,
            {
                "question_id": row["question_id"],
                "take_rate": row["take_rate"],
                "correct_teams": row["correct_teams"],
                "total_teams": row["total_teams"],
                "text": row["text"],
                "answer": row["answer"],
                "topics": [],
            },
        )
        # У вопроса может быть до двух тем
        if row["category"]:
            label = f"{row['category']} / {row['subcategory']}"
            if label not in entry["topics"]:
                entry["topics"].append(label)
    return by_position


def analyse_pack(conn, pack_id: int, team_id: int, *, session) -> Optional[dict]:
    html = fetch_table(pack_id, session=session)
    found = find_team_mask(html, team_id)
    if not found:
        return None
    title, mask, declared_total = found

    questions = pack_questions(conn, pack_id)
    if not questions:
        return None
    if len(mask) != len(questions):
        print(
            f"  пакет {pack_id}: длина маски {len(mask)} != вопросов {len(questions)} — пропускаю"
        )
        return None

    pack = conn.execute(
        "SELECT title, start_date FROM packs WHERE id = ?", (pack_id,)
    ).fetchone()

    results = []
    for pos, question in questions.items():
        took = mask[pos - 1] == "1"
        results.append({**question, "position": pos, "took": took})

    return {
        "pack_id": pack_id,
        "pack_title": pack["title"] if pack else str(pack_id),
        "date": (pack["start_date"] or "")[:10] if pack else "",
        "team_title": title,
        "mask": mask,
        "declared_total": declared_total,
        "score": sum(1 for r in results if r["took"]),
        "results": results,
    }


def _fmt_pct(value: float) -> str:
    return f"{100 * value:.0f}%"


def build_comparison(mine: List[dict], theirs: List[dict], their_name: str) -> str:
    """Сравнение двух команд на одних и тех же вопросах.

    Ценно именно совпадение пакетов: разница в счёте на разных турнирах ничего
    не значит, а на одних и тех же вопросах она измерима.
    """
    out: List[str] = []
    a = out.append

    common = {p["pack_id"] for p in mine} & {p["pack_id"] for p in theirs}
    if not common:
        return ""

    my_rows, their_rows = [], []
    for pack in mine:
        if pack["pack_id"] in common:
            my_rows.extend(pack["results"])
    for pack in theirs:
        if pack["pack_id"] in common:
            their_rows.extend(pack["results"])

    their_by_q = {r["question_id"]: r for r in their_rows}
    pairs = [(r, their_by_q[r["question_id"]]) for r in my_rows if r["question_id"] in their_by_q]
    if not pairs:
        return ""

    a(f"## Сравнение с командой «{their_name}»\n")
    titles = [p["pack_title"] for p in mine if p["pack_id"] in common]
    a(f"Общие турниры: {', '.join(titles)} — {len(pairs)} одних и тех же вопросов.")
    a("Сравнивать счёт на разных турнирах бессмысленно; здесь вопросы совпадают.\n")

    my_score = sum(1 for m, _ in pairs if m["took"])
    their_score = sum(1 for _, t in pairs if t["took"])
    expected = sum(m["take_rate"] for m, _ in pairs)
    a(f"| | Взято | Отклонение от поля |")
    a("|---|---:|---:|")
    a(f"| Вы | {my_score} | {my_score - expected:+.1f} |")
    a(f"| {their_name} | {their_score} | {their_score - expected:+.1f} |")
    a(f"| **Разница** | **{their_score - my_score:+d}** | |")
    a("")

    # Где именно возникает разрыв
    a("### Откуда берётся разрыв\n")
    layers = [
        ("Очень лёгкие (≥85%)", lambda r: r["take_rate"] >= 0.85),
        ("Лёгкие (70–85%)", lambda r: 0.70 <= r["take_rate"] < 0.85),
        ("Средние (40–70%)", lambda r: 0.40 <= r["take_rate"] < 0.70),
        ("Трудные (15–40%)", lambda r: 0.15 <= r["take_rate"] < 0.40),
        ("Гробы (<15%)", lambda r: r["take_rate"] < 0.15),
    ]
    a("| Слой | Вопросов | Вы | Соперник | Разница |")
    a("|---|---:|---:|---:|---:|")
    for name, pred in layers:
        sel = [(m, t) for m, t in pairs if pred(m)]
        if not sel:
            continue
        mine_n = sum(1 for m, _ in sel if m["took"])
        their_n = sum(1 for _, t in sel if t["took"])
        a(f"| {name} | {len(sel)} | {mine_n} | {their_n} | {their_n - mine_n:+d} |")
    a("")

    # Вопросы, которые взяли они, а вы нет
    a("### Что взяли они, а вы нет\n")
    only_theirs = [(m, t) for m, t in pairs if t["took"] and not m["took"]]
    a(f"Таких вопросов: **{len(only_theirs)}**. Обратных (взяли вы, а они нет): "
      f"**{sum(1 for m, t in pairs if m['took'] and not t['took'])}**.\n")
    by_cat: Dict[str, int] = defaultdict(int)
    for m, _ in only_theirs:
        for topic in m["topics"] or ["(без классификации)"]:
            by_cat[topic.split(" / ")[0]] += 1
    if by_cat:
        a("| Категория | Вопросов, где они взяли, а вы нет |")
        a("|---|---:|")
        for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
            a(f"| {cat} | {n} |")
        a("")
    a("| Взяли в поле | Вопрос | Ответ |")
    a("|---:|---|---|")
    for m, _ in sorted(only_theirs, key=lambda x: -x[0]["take_rate"])[:12]:
        text = " ".join((m["text"] or "").split())[:95]
        answer = " ".join((m["answer"] or "").split())[:40]
        a(f"| {_fmt_pct(m['take_rate'])} | {text}… | {answer} |")
    a("")
    return "\n".join(out)


def build_report(packs: List[dict], team_id: int, comparison: str = "") -> str:
    out: List[str] = []
    a = out.append

    all_results = [r for p in packs for r in p["results"]]
    total = len(all_results)
    took = sum(1 for r in all_results if r["took"])
    field_expected = sum(r["take_rate"] for r in all_results)

    a(f"# Тематические провалы: команда {packs[0]['team_title']} (ID {team_id})\n")
    a("Считается не «сколько взяли», а **насколько это отличается от поля на тех же")
    a("вопросах**. Не взять вопрос, который не взял никто, — не потеря. Не взять")
    a("вопрос, который взяли все, — прямая потеря позиции.\n")

    a("## Что вошло в анализ\n")
    a("| Турнир | Дата | Вопросов | Взято | Ожидание по полю |")
    a("|---|---|---:|---:|---:|")
    for p in packs:
        expected = sum(r["take_rate"] for r in p["results"])
        a(
            f"| {p['pack_title']} | {p['date']} | {len(p['results'])} | "
            f"{p['score']} | {expected:.1f} |"
        )
    a(f"| **Итого** | | **{total}** | **{took}** | **{field_expected:.1f}** |")
    a("")
    # Знак — от лица команды: минус означает недобор, а не «перебор»
    a(f"Отклонение от среднего поля: **{took - field_expected:+.1f}** ответа.\n")

    unclassified = sum(1 for r in all_results if not r["topics"])
    if unclassified:
        a(
            f"⚠️ Из {total} вопросов **{unclassified} не классифицированы** —"
            " разбор по темам построен только по остальным. Это ограничение"
            " покрытия классификации, а не свойство команды.\n"
        )

    # --- Провал по слоям сложности
    a("## Где теряется: по слоям сложности\n")
    layers = [
        ("Очень лёгкие (взяли ≥85%)", lambda r: r["take_rate"] >= 0.85),
        ("Лёгкие (70–85%)", lambda r: 0.70 <= r["take_rate"] < 0.85),
        ("Средние (40–70%)", lambda r: 0.40 <= r["take_rate"] < 0.70),
        ("Трудные (15–40%)", lambda r: 0.15 <= r["take_rate"] < 0.40),
        ("Гробы (<15%)", lambda r: r["take_rate"] < 0.15),
    ]
    a("| Слой | Вопросов | Взято | Ожидание по полю | Разница |")
    a("|---|---:|---:|---:|---:|")
    for name, predicate in layers:
        rows = [r for r in all_results if predicate(r)]
        if not rows:
            continue
        exp = sum(r["take_rate"] for r in rows)
        got = sum(1 for r in rows if r["took"])
        a(f"| {name} | {len(rows)} | {got} | {exp:.1f} | {got - exp:+.1f} |")
    a("")

    # --- Провал по категориям: подкатегорий 52, и в каждой слишком мало
    # вопросов, чтобы делать выводы. Категорий 14 — там выборка уже что-то значит.
    a("## Где теряется: по крупным темам\n")
    a("Главная таблица. Подкатегорий 52, и в каждой два-три вопроса — это шум.")
    a("Категорий 14, и в них уже есть на что смотреть.\n")
    by_cat: Dict[str, List[dict]] = defaultdict(list)
    for r in all_results:
        for topic in r["topics"] or ["(без классификации)"]:
            by_cat[topic.split(" / ")[0]].append(r)

    cat_rows = []
    for cat, items in by_cat.items():
        exp = sum(i["take_rate"] for i in items)
        got = sum(1 for i in items if i["took"])
        cat_rows.append((cat, len(items), got, exp, got - exp, (got - exp) / len(items)))
    cat_rows.sort(key=lambda x: x[4])

    overall = (took - field_expected) / total if total else 0
    a("| Категория | Вопросов | Взято | Ожидание | Недобор | На вопрос |")
    a("|---|---:|---:|---:|---:|---:|")
    for cat, n, got, exp, diff, rate in cat_rows:
        mark = " ⚠️" if n >= 8 and rate < overall - 0.05 else ""
        a(f"| {cat}{mark} | {n} | {got} | {exp:.1f} | {diff:+.1f} | {rate:+.2f} |")
    a("")
    a(f"Ваше среднее по всем вопросам — **{overall:+.2f}** на вопрос. Категории с ⚠️")
    a("проседают сильнее собственного среднего, и вопросов в них хватает, чтобы")
    a("это не было случайностью.\n")

    # --- Провал по подтемам
    a("## Детализация: по подтемам\n")
    by_topic: Dict[str, List[dict]] = defaultdict(list)
    for r in all_results:
        for topic in r["topics"] or ["(без классификации)"]:
            by_topic[topic].append(r)

    rows = []
    for topic, items in by_topic.items():
        exp = sum(i["take_rate"] for i in items)
        got = sum(1 for i in items if i["took"])
        rows.append((topic, len(items), got, exp, got - exp, (got - exp) / len(items)))
    rows.sort(key=lambda x: x[5])

    overall_rate = (took - field_expected) / total if total else 0
    a("Отсортировано по провалу **на вопрос** — иначе темы с разным числом")
    a("вопросов несравнимы. Колонка «на вопрос» показывает, насколько тема хуже")
    a(f"или лучше вашего собственного среднего ({overall_rate:+.2f} на вопрос).\n")
    a("| Тема | Вопросов | Взято | Ожидание | Разница | На вопрос |")
    a("|---|---:|---:|---:|---:|---:|")
    for topic, n, got, exp, diff, rate in rows:
        marker = ""
        if n >= 4 and rate < overall_rate - 0.15:
            marker = " ⚠️"
        a(f"| {topic}{marker} | {n} | {got} | {exp:.1f} | {diff:+.1f} | {rate:+.2f} |")
    a("")
    a("⚠️ — тема, где команда проседает заметно сильнее собственного среднего")
    a("(и где вопросов хотя бы четыре).\n")
    a("**Осторожно с выводами:** на такой выборке в каждой теме всего несколько")
    a("вопросов. Разница в один-два ответа здесь — шум. Смотреть надо не на")
    a("отдельные строки, а на форму таблицы целиком.\n")

    # --- Самые дорогие промахи
    a("## Самые дорогие промахи\n")
    a("Вопросы, которые взяло большинство поля, а команда — нет. Именно их стоит")
    a("разобрать вручную и определить причину: не знали, не распознали, не докрутили.\n")
    misses = sorted(
        (r for r in all_results if not r["took"]),
        key=lambda r: -r["take_rate"],
    )[:20]
    a("| Взяли в поле | Вопрос | Тема | Ответ |")
    a("|---:|---|---|---|")
    for r in misses:
        text = " ".join((r["text"] or "").split())[:110]
        answer = " ".join((r["answer"] or "").split())[:45]
        topics = "; ".join(r["topics"]) or "—"
        a(f"| {_fmt_pct(r['take_rate'])} | `{r['question_id']}` {text}… | {topics} | {answer} |")
    a("")

    if comparison:
        a(comparison)

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Тематические провалы команды")
    parser.add_argument("--team-id", type=int, required=True)
    parser.add_argument(
        "--packs", default="", help="ID пакетов gotquestions через запятую"
    )
    parser.add_argument(
        "--rating",
        default="",
        help="Пары «турнир рейтинга:пакет базы» через запятую, напр. 11844:6602",
    )
    parser.add_argument(
        "--external",
        default="",
        help=(
            "JSON-файлы с турнирами, которых нет в базе (через запятую). "
            "Нужны, когда вопросы есть только у команды: в БД они не попадают, "
            "но в статистику входят."
        ),
    )
    parser.add_argument(
        "--vs", type=int, default=None, help="ID второй команды для сравнения"
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument(
        "--label-model",
        default=None,
        help="Использовать метки только этой модели (например "
             "'google/gemma-4-26b-a4b-it@p2'). По умолчанию — любые.",
    )
    parser.add_argument("--report-path", default=None)
    parser.add_argument(
        "--json-path", default=None, help="Куда положить машиночитаемый срез для приложения"
    )
    args = parser.parse_args()

    global LABEL_MODEL
    LABEL_MODEL = args.label_model

    pack_ids = [int(p) for p in args.packs.split(",") if p.strip()]
    conn = get_readonly_connection(args.db)
    session = requests.Session()

    packs: List[dict] = []
    try:
        for pack_id in pack_ids:
            print(f"Пакет {pack_id}...")
            try:
                data = analyse_pack(conn, pack_id, args.team_id, session=session)
            except Exception as exc:  # noqa: BLE001 — сеть, пропускаем пакет
                print(f"  ошибка: {exc}")
                continue
            if not data:
                print("  команда не найдена в таблице")
                continue
            print(
                f"  {data['pack_title']}: {data['score']}/{len(data['results'])}"
                + (
                    f" (на сайте заявлено {data['declared_total']})"
                    if data["declared_total"] >= 0
                    else ""
                )
            )
            packs.append(data)
            time.sleep(SCRAPE_DELAY)

        for pair in [p for p in args.rating.split(",") if p.strip()]:
            tid, pid = (int(x) for x in pair.split(":"))
            if any(p["pack_id"] == pid for p in packs):
                # Тот же пакет уже взят с gotquestions: посчитать его дважды
                # значит удвоить и вопросы, и недобор
                print(f"Рейтинг {tid} → пакет {pid}: уже собран, пропускаю")
                continue
            print(f"Рейтинг {tid} → пакет {pid}...")
            try:
                data = analyse_rating_pack(conn, tid, pid, args.team_id, session=session)
            except Exception as exc:  # noqa: BLE001
                print(f"  ошибка: {exc}")
                continue
            if not data:
                print("  команда не найдена или пакет не совпал")
                continue
            print(
                f"  {data['pack_title']}: {data['score']}/{len(data['results'])}"
                f" (заявлено {data['declared_total']}, совпадение пакета"
                f" {data['correlation']:+.3f})"
            )
            packs.append(data)
            time.sleep(SCRAPE_DELAY)
    finally:
        conn.close()

    for path in [p.strip() for p in args.external.split(",") if p.strip()]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        print(
            f"Внешний источник: {data['pack_title']}: "
            f"{data['score']}/{len(data['results'])}"
        )
        packs.append(data)

    if not packs:
        print("Ни в одном пакете команда не найдена.")
        return 1

    comparison = ""
    if args.vs:
        print(f"\nСоперник {args.vs}: собираю те же пакеты...")
        conn2 = get_readonly_connection(args.db)
        rival: List[dict] = []
        try:
            for pack in packs:
                if pack["pack_id"] < 0:
                    continue  # внешний источник: чужой маски у нас нет
                try:
                    data = analyse_pack(conn2, pack["pack_id"], args.vs, session=session)
                except Exception as exc:  # noqa: BLE001
                    print(f"  пакет {pack['pack_id']}: ошибка {exc}")
                    continue
                if data:
                    print(f"  {data['pack_title']}: {data['score']}/{len(data['results'])}")
                    rival.append(data)
                time.sleep(SCRAPE_DELAY)
        finally:
            conn2.close()
        if rival:
            comparison = build_comparison(packs, rival, rival[0]["team_title"])
        else:
            print("  общих пакетов с этой командой нет")

    markdown = build_report(packs, args.team_id, comparison)
    path = Path(args.report_path or (REPORT_DIR / f"team-{args.team_id}-gaps.md"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    print(f"\nОтчёт: {path}")

    # Машиночитаемый срез для приложения: иначе «слабые темы» в интерфейсе
    # выводятся заново из трёх самооценок, хотя они уже измерены на турнирах
    # со сравнением с полем.
    json_path = Path(args.json_path or (DATA_DIR / f"team-{args.team_id}-gaps.json"))
    json_path.parent.mkdir(parents=True, exist_ok=True)
    all_results = [r for p in packs for r in p["results"]]
    by_cat: Dict[str, List[dict]] = defaultdict(list)
    for r in all_results:
        for topic in r["topics"] or []:
            by_cat[topic.split(" / ")[0]].append(r)

    total = len(all_results)
    took = sum(1 for r in all_results if r["took"])
    expected = sum(r["take_rate"] for r in all_results)

    # Вторая ось: приёмы (question_techniques строит detect_techniques.py)
    tconn = get_readonly_connection(args.db)
    tech_map = {
        r["question_id"]: r["primary_technique"]
        for r in tconn.execute(
            "SELECT question_id, primary_technique FROM question_techniques"
        )
    }
    tconn.close()
    by_tech: Dict[str, List[dict]] = defaultdict(list)
    for r in all_results:
        by_tech[tech_map.get(r["question_id"], "чистый")].append(r)
    techniques = []
    for tech, items in by_tech.items():
        exp = sum(i["take_rate"] for i in items)
        got = sum(1 for i in items if i["took"])
        techniques.append({
            "technique": tech,
            "questions": len(items),
            "took": got,
            "expected": round(exp, 2),
            "deficit": round(got - exp, 2),
            "per_question": round((got - exp) / len(items), 3),
        })
    techniques.sort(key=lambda c: c["per_question"])

    categories = []
    for cat, items in by_cat.items():
        exp = sum(i["take_rate"] for i in items)
        got = sum(1 for i in items if i["took"])
        categories.append(
            {
                "category": cat,
                "questions": len(items),
                "took": got,
                "expected": round(exp, 2),
                "deficit": round(got - exp, 2),
                "per_question": round((got - exp) / len(items), 3),
            }
        )
    categories.sort(key=lambda c: c["per_question"])

    json_path.write_text(
        json.dumps(
            {
                "team_id": args.team_id,
                "team_title": packs[0]["team_title"],
                "tournaments": [
                    {"title": p["pack_title"], "date": p["date"], "score": p["score"],
                     "questions": len(p["results"])}
                    for p in packs
                ],
                "questions_total": total,
                "took": took,
                "expected": round(expected, 2),
                "per_question_avg": round((took - expected) / total, 3) if total else 0,
                "categories": categories,
                "techniques": techniques,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"Данные для приложения: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
