"""Генерация качественных «берущихся» фактов по канону тем.

Проблема: в контуре «Учить» досье факта показывало комментарии к вопросам —
фрагменты, привязанные к своему вопросу, не читаемые как самостоятельные факты.

Метод (см. docs/plans/2026-07-18-quality-facts.md): для каждого канон-ответа
собрать (A) выжимку Wikipedia (точное ЯДРО) + (B) корпусные углы — вопросы и
разборы, где ответ = этот (ходовые ЗАЦЕПКИ). LLM синтезирует чистую карточку
{ядро, зацепки[{факт, угол, цитата}]}. Антигаллюцинация без второй модели: модель
обязана дать ДОСЛОВНУЮ цитату-опору, а мы проверяем перекрытие её слов с сырьём —
факт без опоры в учебник не идёт.

Использование:
    python scripts/gen_facts.py --weak --per-cat 8            # демо по слабым темам
    python scripts/gen_facts.py --category-ids 7,6 --per-cat 12
    python scripts/gen_facts.py --answers "Герострат,гильотина" --force
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, PROJECT_ROOT
from database.db import get_readonly_connection
import app.study as study
from scripts.wikipedia_client import WikipediaClient

CARDS_PATH = PROJECT_ROOT / "data" / "facts" / "fact_cards.json"
WIKI_CACHE = PROJECT_ROOT / "data" / "facts" / "wiki_cache.json"
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it"
GROUND_THRESH = 0.6  # доля слов цитаты, которая должна найтись в сырье

SYSTEM = """Ты готовишь игрока ЧГК. По сущности собери КАРТОЧКУ ЗНАНИЙ: не пересказ \
энциклопедии, а то, что реально эксплуатируют в вопросах ЧГК про эту сущность, в \
чистой самостоятельной форме.

Дано: (A) справка Wikipedia — источник точности для ядра; (B) тексты вопросов ЧГК \
и авторские разборы — показывают, какие ЗАЦЕПКИ (ассоциации, факты, связи) \
используют авторы.

Верни СТРОГО JSON такого вида:
{"ядро":"1-2 предложения: кто/что это, самое базовое",
 "зацепки":[{"факт":"самостоятельный факт, понятный без вопроса",
             "угол":"как это обычно спрашивают (кратко)",
             "цитата":"ДОСЛОВНЫЙ фрагмент из (A) или (B), подтверждающий факт"}]}

ПРАВИЛА:
- Каждый факт ОБЯЗАН опираться на дословную «цитату» — копию куска текста (A) или
  (B), не пересказ. Нет дословной опоры в данных — не включай факт.
- НЕ выдумывай. Факт самостоятелен: «Герострат сжёг храм Артемиды», а не «он сжёг».
- 5-8 зацепок, приоритет ходовым (встречаются в (B) не по одному разу).
- Только русский, без markdown."""


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _words(s: str) -> set:
    return set(re.findall(r"[а-яёa-z0-9]{4,}", (s or "").lower()))


def grounded(citation: str, evidence: str) -> bool:
    cw = _words(citation)
    if not cw:
        return False
    return len(cw & _words(evidence)) / len(cw) >= GROUND_THRESH


def _pick_field(d: dict, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n):
            return d[n]
    return None


def build_evidence(conn, wiki: WikipediaClient, answer: str) -> tuple:
    """Возвращает (display_answer, user_prompt, evidence_text, n_questions)."""
    d = study.fact_dossier(conn, answer, limit=10)
    title = wiki.search(d["answer"])
    extract = wiki.get_extract(title, chars=1500, intro_only=False) if title else ""
    angles = [
        f"- Q: {a['text'][:180]}\n  Разбор: {(a['comment'] or '')[:220]}"
        for a in d["angles"]
        if len((a["comment"] or "").strip()) >= 15
    ][:8]
    evidence = (extract or "") + "\n" + "\n".join(angles)
    user = (
        f"Сущность: {d['answer']}\n\n"
        f"(A) Wikipedia:\n{extract or '(нет статьи)'}\n\n"
        f"(B) Как спрашивают в ЧГК ({d['total']} вопросов):\n" + "\n".join(angles)
    )
    return d["answer"], user, evidence, d["total"]


def synthesize(provider, user: str, evidence: str) -> Optional[dict]:
    raw = provider.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        max_tokens=1100, json_mode=True,
    )
    if not raw:
        return None
    try:
        j = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        return None
    core = _pick_field(j, "ядро", "core")
    raw_hooks = _pick_field(j, "зацепки", "hooks", "facts") or []
    hooks = []
    for h in raw_hooks:
        if not isinstance(h, dict):
            continue
        fact = _pick_field(h, "факт", "fact")
        if not fact:
            continue
        citation = _pick_field(h, "цитата", "citation", "quote") or ""
        hooks.append({
            "fact": fact,
            "angle": _pick_field(h, "угол", "angle") or "",
            "grounded": grounded(citation, evidence),
        })
    if not core and not hooks:
        return None
    return {"core": core or "", "hooks": hooks}


def resolve_answers(conn, args) -> List[str]:
    if args.answers:
        return [a.strip() for a in args.answers.split(",") if a.strip()]
    cat_ids = _resolve_category_ids(conn, args)
    answers: List[str] = []
    seen = set()
    for cid in cat_ids:
        for item in study.canon(conn, cid, limit=args.per_cat):
            k = item["key"]
            if k not in seen:
                seen.add(k)
                answers.append(item["answer"])
    return answers


def _resolve_category_ids(conn, args) -> List[int]:
    if args.category_ids:
        return [int(x) for x in args.category_ids.split(",") if x.strip()]
    if args.weak:
        prof = _load(PROJECT_ROOT / "data" / "team" / "team-97700-gaps.json")
        weak = sorted(
            (c for c in prof.get("categories", [])
             if c.get("per_question", 0) < 0 and c.get("questions", 0) >= 5),
            key=lambda c: c["per_question"],
        )
        names = [c["category"] for c in weak]
        name_to_id = {r["name_ru"]: r["id"]
                      for r in conn.execute("SELECT id, name_ru FROM categories")}
        return [name_to_id[n] for n in names if n in name_to_id]
    # по умолчанию — весь корпус (одна «категория» None)
    return [None]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weak", action="store_true", help="канон слабых тем команды")
    ap.add_argument("--category-ids", default="", help="id категорий через запятую")
    ap.add_argument("--answers", default="", help="явный список ответов через запятую")
    ap.add_argument("--per-cat", type=int, default=8, help="сколько канон-ответов на тему")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--force", action="store_true", help="перегенерировать даже закэшированные")
    ap.add_argument("--delay", type=float, default=0.6, help="пауза между вызовами, сек")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from classifier.providers import create_provider

    conn = get_readonly_connection(DB_PATH)
    wiki = WikipediaClient(cache_path=WIKI_CACHE)
    cards = _load(CARDS_PATH)
    provider = None if args.dry_run else create_provider("openrouter", model=args.model)

    answers = resolve_answers(conn, args)
    print(f"К обработке: {len(answers)} сущностей. Модель: {args.model}. "
          f"Уже в кэше: {len(cards)}.")
    if args.dry_run:
        print("Список:", ", ".join(answers))
        return 0

    fails = 0
    done = 0
    for i, answer in enumerate(answers, 1):
        key = study.normalize_answer(answer)
        if key in cards and not args.force:
            continue
        try:
            display, user, evidence, n = build_evidence(conn, wiki, answer)
            card = synthesize(provider, user, evidence)
        except Exception as e:
            print(f"  [{i}/{len(answers)}] {answer}: ошибка {e}")
            fails += 1
            if fails >= 8:
                print("Слишком много ошибок подряд — останавливаюсь.")
                break
            time.sleep(2)
            continue
        if not card:
            print(f"  [{i}/{len(answers)}] {answer}: пустой ответ модели")
            fails += 1
            time.sleep(args.delay)
            continue
        fails = 0
        n_ok = sum(1 for h in card["hooks"] if h["grounded"])
        cards[key] = {
            "answer": display,
            "core": card["core"],
            "hooks": card["hooks"],
            "n_questions": n,
            "model": args.model,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        done += 1
        print(f"  [{i}/{len(answers)}] {display}: {len(card['hooks'])} зацепок "
              f"({n_ok} подтв.)")
        if done % 5 == 0:
            _save(CARDS_PATH, cards)
        time.sleep(args.delay)

    _save(CARDS_PATH, cards)
    wiki.save()
    conn.close()
    print(f"\nГотово. Карточек всего: {len(cards)}. Новых: {done}. Файл: {CARDS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
