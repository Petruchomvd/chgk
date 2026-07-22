"""Сборка воспроизводимого словаря советского кино из Wikidata.

Забирает фильмы производства СССР (игровые, мультфильмы, телефильмы) и их
персонажей, сохраняет снапшот вместе с текстами SPARQL-запросов и датой.

Снапшот руками не редактируется. Ручные правки — только в hints.json.

Использование:
    python scripts/build_cinema_dict.py
    python scripts/build_cinema_dict.py --skip-homonyms
    python scripts/build_cinema_dict.py --out data/soviet_cinema
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PROJECT_ROOT

WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "chgk-analysis/1.0 (ultrafig73@gmail.com) python-requests"
REQUEST_DELAY = 1.0
REQUEST_TIMEOUT = 180
MAX_RETRIES = 4

OUTPUT_DIR = PROJECT_ROOT / "data" / "soviet_cinema"

# Уточнение в скобках — артефакт Wikidata: «Аврора (мультфильм)», «Жить (фильм, 1933)».
# Омонимы обязаны искаться по очищенному названию: именно у таких названий
# омонимы и есть, а по строке «Аврора (мультфильм)» не найдётся ничего.
_DISAMBIG_RE = re.compile(r"\s*\([^()]*\)\s*$")


def clean_label(label: str) -> str:
    cleaned = _DISAMBIG_RE.sub("", label or "").strip()
    return cleaned or (label or "").strip()

# Q15180 — СССР, Q11424 — фильм (подклассы: мультфильм, телефильм, короткометражка)
USSR = "wd:Q15180"
FILM = "wd:Q11424"

# --- SPARQL-запросы.
# Каждый атрибут забирается отдельным простым запросом целиком, без LIMIT/OFFSET:
# property path (P31/P279*) вместе с GROUP BY и ORDER BY стабильно роняет
# публичный endpoint в 500, а плоский запрос отдаёт все 8.5k строк за ~6 секунд.
# Дедупликация и группировка — в Python.

Q_FILMS_BASE = """
SELECT ?film ?label ?date WHERE {
  ?film wdt:P31/wdt:P279* %(FILM)s ;
        wdt:P495 %(USSR)s ;
        rdfs:label ?label .
  FILTER(LANG(?label) = "ru")
  OPTIONAL { ?film wdt:P577 ?date }
}
"""

Q_FILM_ATTR = """
SELECT ?film ?v WHERE {
  ?film wdt:P31/wdt:P279* %(FILM)s ; wdt:P495 %(USSR)s .
  ?film %(path)s ?o .
  ?o rdfs:label ?v .
  FILTER(LANG(?v) = "ru")
}
"""

Q_FILM_ALIASES = """
SELECT ?film ?v WHERE {
  ?film wdt:P31/wdt:P279* %(FILM)s ; wdt:P495 %(USSR)s .
  ?film skos:altLabel ?v .
  FILTER(LANG(?v) = "ru")
}
"""

Q_FILM_WIKI = """
SELECT ?film ?v WHERE {
  ?film wdt:P31/wdt:P279* %(FILM)s ; wdt:P495 %(USSR)s .
  ?v schema:about ?film ; schema:isPartOf <https://ru.wikipedia.org/> .
}
"""

# Персонажи: три источника в Wikidata, объединяем.
Q_CHARS_P674 = """
SELECT ?film ?char ?charLabel WHERE {
  ?film wdt:P31/wdt:P279* %(FILM)s ; wdt:P495 %(USSR)s .
  ?film wdt:P674 ?char .
  ?char rdfs:label ?charLabel . FILTER(LANG(?charLabel) = "ru")
}
"""

Q_CHARS_P453 = """
SELECT ?film ?char ?charLabel ?actorLabel WHERE {
  ?film wdt:P31/wdt:P279* %(FILM)s ; wdt:P495 %(USSR)s .
  ?film p:P161 ?st .
  ?st ps:P161 ?actor ; pq:P453 ?char .
  ?char rdfs:label ?charLabel . FILTER(LANG(?charLabel) = "ru")
  OPTIONAL { ?actor rdfs:label ?actorLabel . FILTER(LANG(?actorLabel) = "ru") }
}
"""

Q_CHARS_P4633 = """
SELECT ?film ?name ?actorLabel WHERE {
  ?film wdt:P31/wdt:P279* %(FILM)s ; wdt:P495 %(USSR)s .
  ?film p:P161 ?st .
  ?st ps:P161 ?actor ; pq:P4633 ?name .
  OPTIONAL { ?actor rdfs:label ?actorLabel . FILTER(LANG(?actorLabel) = "ru") }
}
"""

Q_CHAR_ALIASES = """
SELECT ?char ?v WHERE {
  VALUES ?char { %(qids)s }
  ?char skos:altLabel ?v .
  FILTER(LANG(?v) = "ru")
}
"""

# Типы персонажей. Нужны, чтобы отделить вымышленных персонажей от реальных
# людей: Wikidata числит «персонажами» советских фильмов Ленина, Эйнштейна и
# Гитлера — их играли актёры. Но вопрос про настоящего Эйнштейна не является
# упоминанием советского кино.
Q_CHAR_TYPES = """
SELECT ?char ?type ?typeLabel WHERE {
  VALUES ?char { %(qids)s }
  ?char wdt:P31 ?type .
  OPTIONAL { ?type rdfs:label ?typeLabel . FILTER(LANG(?typeLabel) = "ru") }
}
"""

# Люди кино: актёры (P161) и режиссёры (P57) советских фильмов.
# В отличие от «персонажей», это реальные люди — и здесь это не помеха,
# а ровно то, что нужно: вопрос про Никулина — это вопрос про Никулина.
Q_PEOPLE = """
SELECT ?film ?p ?pLabel WHERE {
  ?film wdt:P31/wdt:P279* %(FILM)s ; wdt:P495 %(USSR)s .
  ?film %(path)s ?p .
  ?p rdfs:label ?pLabel . FILTER(LANG(?pLabel) = "ru")
}
"""

Q_PEOPLE_ALIASES = """
SELECT ?p ?v WHERE {
  VALUES ?p { %(qids)s }
  ?p skos:altLabel ?v .
  FILTER(LANG(?v) = "ru")
}
"""

Q_PEOPLE_WIKI = """
SELECT ?p ?v WHERE {
  VALUES ?p { %(qids)s }
  ?v schema:about ?p ; schema:isPartOf <https://ru.wikipedia.org/> .
}
"""

# Род занятий (P106). Wikidata числит «в ролях» всех, чья хроника попала в
# документальный фильм: Сталина, Геринга, Гагарина. По профессии видно, что
# они не киношники, и вопрос про них — не про советское кино.
Q_PEOPLE_OCCUPATIONS = """
SELECT ?p ?occ ?occLabel WHERE {
  VALUES ?p { %(qids)s }
  ?p wdt:P106 ?occ .
  OPTIONAL { ?occ rdfs:label ?occLabel . FILTER(LANG(?occLabel) = "ru") }
}
"""

# Омонимы: другие сущности Wikidata с той же русской меткой.
# Проверяемое основание считать название омонимичным («Война и мир» — ещё и роман),
# вместо ручного списка догадок.
Q_HOMONYMS = """
SELECT ?label ?other ?typeLabel WHERE {
  VALUES ?label { %(labels)s }
  ?other rdfs:label ?label .
  ?other wdt:P31 ?type .
  ?type rdfs:label ?typeLabel . FILTER(LANG(?typeLabel) = "ru")
}
"""


class SparqlError(RuntimeError):
    pass


def run_sparql(query: str, *, session: requests.Session, label: str = "") -> List[Dict[str, Any]]:
    """Выполнить SPARQL-запрос с повторами и вернуть bindings."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # POST, а не GET: запрос омонимов с сотней названий в VALUES
            # не помещается в URL и получает 414.
            resp = session.post(
                WDQS_ENDPOINT,
                data={"query": query},
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/sparql-results+json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 30))
                print(f"    429, жду {wait} c...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            rows = resp.json()["results"]["bindings"]
            if label:
                print(f"    {label}: {len(rows)} строк")
            return rows
        except Exception as exc:  # noqa: BLE001 — ретраим любую сетевую/парс-ошибку
            last_error = exc
            wait = min(60, 5 * 2 ** (attempt - 1))
            print(f"    попытка {attempt}/{MAX_RETRIES} не удалась ({exc}); жду {wait} c")
            time.sleep(wait)
    raise SparqlError(f"SPARQL не выполнился после {MAX_RETRIES} попыток: {last_error}")


def qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _val(row: Dict[str, Any], key: str) -> str | None:
    node = row.get(key)
    if not node:
        return None
    return node.get("value") or None


def fetch_films(session: requests.Session) -> Dict[str, Dict[str, Any]]:
    base = {"FILM": FILM, "USSR": USSR}
    films: Dict[str, Dict[str, Any]] = {}

    print("[1/6] Фильмы: базовый список...")
    for row in run_sparql(Q_FILMS_BASE % base, session=session, label="фильмы"):
        fid = qid(_val(row, "film") or "")
        if not fid:
            continue
        date = _val(row, "date")
        year = int(date[:4]) if date and date[:4].isdigit() else None
        entry = films.setdefault(
            fid,
            {
                "qid": fid,
                "label": _val(row, "label"),
                "year": year,
                "aliases": [],
                "types": [],
                "directors": [],
                "based_on": [],
                "wiki_url": None,
                "homonyms": [],
            },
        )
        # У фильма может быть несколько дат выхода — берём самую раннюю
        if year and (entry["year"] is None or year < entry["year"]):
            entry["year"] = year
    print(f"  уникальных фильмов: {len(films)}")
    time.sleep(REQUEST_DELAY)

    attrs = [
        ("[2/6] типы", "wdt:P31", "types"),
        ("[3/6] режиссёры", "wdt:P57", "directors"),
        ("[4/6] первоисточники", "wdt:P144", "based_on"),
    ]
    for title, path, field in attrs:
        print(f"{title}...")
        for row in run_sparql(
            Q_FILM_ATTR % {**base, "path": path}, session=session, label=field
        ):
            fid = qid(_val(row, "film") or "")
            value = _val(row, "v")
            if fid in films and value and value not in films[fid][field]:
                films[fid][field].append(value)
        time.sleep(REQUEST_DELAY)

    print("[5/6] альтернативные названия...")
    for row in run_sparql(Q_FILM_ALIASES % base, session=session, label="алиасы"):
        fid = qid(_val(row, "film") or "")
        value = _val(row, "v")
        if fid in films and value and value not in films[fid]["aliases"]:
            films[fid]["aliases"].append(value)
    time.sleep(REQUEST_DELAY)

    print("[6/6] ссылки на ru.wikipedia...")
    for row in run_sparql(Q_FILM_WIKI % base, session=session, label="вики"):
        fid = qid(_val(row, "film") or "")
        if fid in films and not films[fid]["wiki_url"]:
            films[fid]["wiki_url"] = _val(row, "v")

    return films


def fetch_characters(
    session: requests.Session, films: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    base = {"FILM": FILM, "USSR": USSR}
    chars: Dict[str, Dict[str, Any]] = {}

    def _touch(key: str, label: str, source: str) -> Dict[str, Any]:
        entry = chars.setdefault(
            key,
            {
                "key": key,
                "qid": key if key.startswith("Q") else None,
                "label": label,
                "aliases": [],
                "films": [],
                "actors": [],
                "sources": [],
                "types": [],
                "type_qids": [],
            },
        )
        if source not in entry["sources"]:
            entry["sources"].append(source)
        return entry

    print("[1/5] Персонажи: P674 (персонажи фильма)...")
    for row in run_sparql(Q_CHARS_P674 % base, session=session, label="P674"):
        cid = qid(_val(row, "char") or "")
        label = _val(row, "charLabel")
        fid = qid(_val(row, "film") or "")
        if not cid or not label:
            continue
        entry = _touch(cid, label, "P674")
        if fid in films and fid not in entry["films"]:
            entry["films"].append(fid)
    time.sleep(REQUEST_DELAY)

    print("[2/5] Персонажи: P453 (роль как сущность)...")
    for row in run_sparql(Q_CHARS_P453 % base, session=session, label="P453"):
        cid = qid(_val(row, "char") or "")
        label = _val(row, "charLabel")
        fid = qid(_val(row, "film") or "")
        actor = _val(row, "actorLabel")
        if not cid or not label:
            continue
        entry = _touch(cid, label, "P453")
        if fid in films and fid not in entry["films"]:
            entry["films"].append(fid)
        if actor and actor not in entry["actors"]:
            entry["actors"].append(actor)
    time.sleep(REQUEST_DELAY)

    print("[3/5] Персонажи: P4633 (имя роли строкой)...")
    for row in run_sparql(Q_CHARS_P4633 % base, session=session, label="P4633"):
        name = (_val(row, "name") or "").strip()
        fid = qid(_val(row, "film") or "")
        actor = _val(row, "actorLabel")
        if not name:
            continue
        # У строковых ролей нет QID. Ключуем по имени + фильму, иначе «Иван»
        # из разных фильмов слипнется в одну несуществующую сущность.
        key = f"S:{name.lower()}|{fid}"
        entry = _touch(key, name, "P4633")
        if fid in films and fid not in entry["films"]:
            entry["films"].append(fid)
        if actor and actor not in entry["actors"]:
            entry["actors"].append(actor)
    time.sleep(REQUEST_DELAY)

    print("[4/5] Персонажи: альтернативные имена...")
    qids = [c["qid"] for c in chars.values() if c["qid"]]
    for i in range(0, len(qids), 200):
        chunk = " ".join(f"wd:{q}" for q in qids[i : i + 200])
        for row in run_sparql(Q_CHAR_ALIASES % {"qids": chunk}, session=session):
            cid = qid(_val(row, "char") or "")
            value = _val(row, "v")
            if cid in chars and value and value not in chars[cid]["aliases"]:
                chars[cid]["aliases"].append(value)
        time.sleep(REQUEST_DELAY)

    print("[5/5] Персонажи: типы (вымышленный персонаж или реальный человек)...")
    for i in range(0, len(qids), 200):
        chunk = " ".join(f"wd:{q}" for q in qids[i : i + 200])
        for row in run_sparql(Q_CHAR_TYPES % {"qids": chunk}, session=session):
            cid = qid(_val(row, "char") or "")
            type_qid = qid(_val(row, "type") or "")
            type_label = _val(row, "typeLabel")
            if cid not in chars or not type_qid:
                continue
            if type_qid not in chars[cid]["type_qids"]:
                chars[cid]["type_qids"].append(type_qid)
            if type_label and type_label not in chars[cid]["types"]:
                chars[cid]["types"].append(type_label)
        time.sleep(REQUEST_DELAY)

    return chars


def fetch_people(
    session: requests.Session, films: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Актёры и режиссёры советских фильмов."""
    base = {"FILM": FILM, "USSR": USSR}
    people: Dict[str, Dict[str, Any]] = {}

    for step, (path, role, title) in enumerate(
        [("wdt:P161", "actor", "актёры"), ("wdt:P57", "director", "режиссёры")], start=1
    ):
        print(f"[{step}/5] Люди кино: {title}...")
        for row in run_sparql(
            Q_PEOPLE % {**base, "path": path}, session=session, label=title
        ):
            pid = qid(_val(row, "p") or "")
            label = _val(row, "pLabel")
            fid = qid(_val(row, "film") or "")
            if not pid or not label:
                continue
            entry = people.setdefault(
                pid,
                {
                    "qid": pid,
                    "label": label,
                    "aliases": [],
                    "roles": [],
                    "films": [],
                    "wiki_url": None,
                    "homonyms": [],
                    "occupations": [],
                    "occupation_qids": [],
                },
            )
            if role not in entry["roles"]:
                entry["roles"].append(role)
            if fid in films and fid not in entry["films"]:
                entry["films"].append(fid)
        time.sleep(REQUEST_DELAY)

    qids = sorted(people)
    print("[3/5] Люди кино: альтернативные имена...")
    for i in range(0, len(qids), 200):
        chunk = " ".join(f"wd:{q}" for q in qids[i : i + 200])
        for row in run_sparql(Q_PEOPLE_ALIASES % {"qids": chunk}, session=session):
            pid = qid(_val(row, "p") or "")
            value = _val(row, "v")
            if pid in people and value and value not in people[pid]["aliases"]:
                people[pid]["aliases"].append(value)
        time.sleep(REQUEST_DELAY)

    print("[4/5] Люди кино: ссылки на ru.wikipedia...")
    for i in range(0, len(qids), 200):
        chunk = " ".join(f"wd:{q}" for q in qids[i : i + 200])
        for row in run_sparql(Q_PEOPLE_WIKI % {"qids": chunk}, session=session):
            pid = qid(_val(row, "p") or "")
            if pid in people and not people[pid]["wiki_url"]:
                people[pid]["wiki_url"] = _val(row, "v")
        time.sleep(REQUEST_DELAY)

    print("[5/5] Люди кино: род занятий...")
    for i in range(0, len(qids), 200):
        chunk = " ".join(f"wd:{q}" for q in qids[i : i + 200])
        for row in run_sparql(Q_PEOPLE_OCCUPATIONS % {"qids": chunk}, session=session):
            pid = qid(_val(row, "p") or "")
            occ_qid = qid(_val(row, "occ") or "")
            occ_label = _val(row, "occLabel")
            if pid not in people or not occ_qid:
                continue
            if occ_qid not in people[pid]["occupation_qids"]:
                people[pid]["occupation_qids"].append(occ_qid)
            if occ_label and occ_label not in people[pid]["occupations"]:
                people[pid]["occupations"].append(occ_label)
        time.sleep(REQUEST_DELAY)

    return people


def fetch_homonyms(
    session: requests.Session, labels: List[str], *, batch: int = 100
) -> Dict[str, List[Dict[str, str]]]:
    """Для каждой метки найти другие сущности Wikidata с тем же названием."""
    result: Dict[str, List[Dict[str, str]]] = {}
    total = len(labels)
    for i in range(0, total, batch):
        chunk = labels[i : i + batch]
        values = " ".join(
            '"%s"@ru' % lbl.replace("\\", "\\\\").replace('"', '\\"') for lbl in chunk
        )
        rows = run_sparql(Q_HOMONYMS % {"labels": values}, session=session)
        for row in rows:
            label = _val(row, "label")
            other = qid(_val(row, "other") or "")
            type_label = _val(row, "typeLabel")
            if not label or not other:
                continue
            bucket = result.setdefault(label, [])
            if not any(h["qid"] == other for h in bucket):
                bucket.append({"qid": other, "type": type_label or "?"})
        print(f"  омонимы: {min(i + batch, total)}/{total}")
        time.sleep(REQUEST_DELAY)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка словаря советского кино из Wikidata")
    parser.add_argument("--out", default=str(OUTPUT_DIR), help="Папка для снапшота")
    parser.add_argument(
        "--skip-homonyms",
        action="store_true",
        help="Не запрашивать омонимы (быстро, но без автоматической защиты от омонимии)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    started = datetime.now(timezone.utc)

    films = fetch_films(session)
    print()
    chars = fetch_characters(session, films)
    print()
    people = fetch_people(session, films)

    # Тёзки: «Андрей Миронов» — это и актёр, и правозащитник, а «Сергей Миронов»
    # вообще политик. Без этого фамилия без имени будет считаться доказательством.
    if not args.skip_homonyms:
        print("\nТёзки людей кино: ищу других людей с теми же именами...")
        person_labels = sorted({p["label"] for p in people.values() if p["label"]})
        raw_people = fetch_homonyms(session, person_labels)
        for person in people.values():
            others = raw_people.get(person["label"], [])
            # Сам человек своим тёзкой не является
            person["homonyms"] = [h for h in others if h["qid"] != person["qid"]]

    homonyms: Dict[str, List[Dict[str, str]]] = {}
    if not args.skip_homonyms:
        print("\nОмонимы: ищу другие сущности Wikidata с теми же названиями...")
        film_qids = {f["qid"] for f in films.values()}
        # Омонимы нужны для КАЖДОЙ поисковой формы, а не только для основного
        # названия. Иначе альтернативное название остаётся без защиты:
        # у фильма «Первые огни» (1925) альтернативное название — «Маяк»,
        # и без омонимов оно ловило радиостанцию, химкомбинат и ресторан.
        forms = set()
        for film in films.values():
            if film["label"]:
                forms.add(clean_label(film["label"]))
            for alias in film["aliases"]:
                cleaned = clean_label(alias)
                if cleaned:
                    forms.add(cleaned)
        raw = fetch_homonyms(session, sorted(forms))
        # Свои же фильмы омонимом не считаем
        for label, others in raw.items():
            filtered = [h for h in others if h["qid"] not in film_qids]
            if filtered:
                homonyms[label] = filtered

    for film in films.values():
        label = clean_label(film["label"] or "")
        film["homonyms"] = homonyms.get(label, [])
        # Омонимы по каждой форме: словарь потом сопоставляет их с алиасом
        by_form = {}
        for form in [label, *(clean_label(a) for a in film["aliases"])]:
            if form and homonyms.get(form):
                by_form[form] = homonyms[form]
        film["homonyms_by_form"] = by_form

    finished = datetime.now(timezone.utc)
    meta = {
        "built_at": finished.isoformat(),
        "duration_sec": round((finished - started).total_seconds(), 1),
        "source": "Wikidata Query Service",
        "endpoint": WDQS_ENDPOINT,
        "scope": {
            "films": "P31/P279* = Q11424 (фильм и подклассы), P495 = Q15180 (СССР)",
            "characters": "P674, P161+P453, P161+P4633",
            "language": "ru",
        },
        "counts": {
            "films": len(films),
            "films_with_homonyms": sum(1 for f in films.values() if f["homonyms"]),
            "films_with_based_on": sum(1 for f in films.values() if f["based_on"]),
            "films_with_year": sum(1 for f in films.values() if f["year"]),
            "characters": len(chars),
            "characters_with_qid": sum(1 for c in chars.values() if c["qid"]),
            "characters_real_people": sum(
                1 for c in chars.values() if "Q5" in (c.get("type_qids") or [])
            ),
            "people": len(people),
            "actors": sum(1 for p in people.values() if "actor" in p["roles"]),
            "directors": sum(1 for p in people.values() if "director" in p["roles"]),
            "people_with_namesakes": sum(1 for p in people.values() if p["homonyms"]),
        },
        "queries": {
            "films_base": Q_FILMS_BASE,
            "film_attr": Q_FILM_ATTR,
            "film_aliases": Q_FILM_ALIASES,
            "film_wiki": Q_FILM_WIKI,
            "chars_p674": Q_CHARS_P674,
            "chars_p453": Q_CHARS_P453,
            "chars_p4633": Q_CHARS_P4633,
            "char_aliases": Q_CHAR_ALIASES,
            "char_types": Q_CHAR_TYPES,
            "people": Q_PEOPLE,
            "people_aliases": Q_PEOPLE_ALIASES,
            "people_wiki": Q_PEOPLE_WIKI,
            "homonyms": None if args.skip_homonyms else Q_HOMONYMS,
        },
        "note": "Снапшот генерируется скриптом. Руками не редактировать — правки только в hints.json.",
    }

    (out_dir / "films.json").write_text(
        json.dumps(films, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "characters.json").write_text(
        json.dumps(chars, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "people.json").write_text(
        json.dumps(people, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "dict_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print("\nГотово.")
    for key, value in meta["counts"].items():
        print(f"  {key:26} {value}")
    print(f"  снапшот: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
