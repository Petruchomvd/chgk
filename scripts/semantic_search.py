"""Семантический поиск и поиск дублей по эмбеддингам вопросов.

Требует заранее построенных векторов (scripts/build_embeddings.py).

Использование:
    python scripts/semantic_search.py "вопросы про Наполеона и его шляпу"
    python scripts/semantic_search.py --dupes --threshold 0.95 --top 200
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, PROJECT_ROOT
from database.db import get_readonly_connection

EMB_DIR = PROJECT_ROOT / "data" / "embeddings" / "e5s-qac"
MODEL_NAME = "intfloat/multilingual-e5-small"


def load_corpus():
    parts = sorted(EMB_DIR.glob("part_*.npz"))
    if not parts:
        sys.exit("нет векторов: сначала python scripts/build_embeddings.py")
    ids_list, vec_list = [], []
    for p in parts:
        data = np.load(p)
        ids_list.append(data["ids"])
        vec_list.append(data["vecs"])
    ids = np.concatenate(ids_list)
    vecs = np.concatenate(vec_list).astype(np.float32)

    # Вопросы могли быть удалены после сборки векторов (чистка дублей) —
    # отбрасываем мёртвые ID, чтобы поиск и дедуп не возвращали призраков.
    conn = get_readonly_connection(DB_PATH)
    alive = {r[0] for r in conn.execute("SELECT id FROM questions")}
    conn.close()
    mask = np.array([int(i) in alive for i in ids])
    if not mask.all():
        ids, vecs = ids[mask], vecs[mask]
    return ids, vecs


def show_questions(conn, qids, scores=None):
    for i, qid in enumerate(qids):
        row = conn.execute(
            "SELECT q.id, q.text, q.answer, p.title FROM questions q "
            "JOIN packs p ON p.id = q.pack_id WHERE q.id = ?",
            (int(qid),),
        ).fetchone()
        if not row:
            continue
        score = f" [{scores[i]:.3f}]" if scores is not None else ""
        print(f"\n#{row['id']}{score} ({row['title'][:50]})")
        print(f"  {row['text'][:180]}")
        print(f"  Ответ: {row['answer'][:80]}")


def search(query: str, top: int):
    from sentence_transformers import SentenceTransformer
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL_NAME, device=device)
    qv = model.encode([f"query: {query}"], normalize_embeddings=True)[0].astype(np.float32)

    ids, vecs = load_corpus()
    sims = vecs @ qv
    top_idx = np.argsort(-sims)[:top]
    conn = get_readonly_connection(DB_PATH)
    show_questions(conn, ids[top_idx], sims[top_idx])


def like(question_id: int, top: int):
    """Соседи конкретного вопроса: близнецы, тот же факт, та же тема.

    Ориентир по косинусу: >0.95 — близнец; 0.88-0.95 — тот же факт,
    другой вопрос; 0.80-0.88 — соседняя тема.
    """
    ids, vecs = load_corpus()
    idx = np.where(ids == question_id)[0]
    if len(idx) == 0:
        sys.exit(f"вопроса #{question_id} нет в векторах")
    qv = vecs[idx[0]]
    sims = vecs @ qv
    sims[idx[0]] = -1.0

    conn = get_readonly_connection(DB_PATH)
    src = conn.execute(
        "SELECT text, answer FROM questions WHERE id = ?", (question_id,)
    ).fetchone()
    print(f"ИСХОДНЫЙ #{question_id}: {src['text'][:160]}")
    print(f"  Ответ: {src['answer'][:60]}")

    top_idx = np.argsort(-sims)[:top]
    show_questions(conn, ids[top_idx], sims[top_idx])


def find_dupes(threshold: float, top: int):
    """Пары почти одинаковых вопросов (вопросы-близнецы из разных пакетов)."""
    ids, vecs = load_corpus()
    n = len(ids)
    print(f"корпус: {n} векторов, ищу пары с косинусом >= {threshold}")

    pairs = []
    block = 2048
    for start in range(0, n, block):
        chunk = vecs[start : start + block]
        sims = chunk @ vecs.T                       # (block, n)
        # зануляем нижний треугольник и диагональ, чтобы пара считалась один раз
        for r in range(sims.shape[0]):
            sims[r, : start + r + 1] = 0.0
        hits = np.argwhere(sims >= threshold)
        for r, c in hits:
            pairs.append((float(sims[r, c]), int(ids[start + r]), int(ids[c])))
        if start % (block * 10) == 0:
            print(f"  {start}/{n}, найдено пар: {len(pairs)}", flush=True)

    pairs.sort(reverse=True)
    print(f"\nвсего пар с косинусом >= {threshold}: {len(pairs)}")
    conn = get_readonly_connection(DB_PATH)
    for sim, a, b in pairs[:top]:
        ra = conn.execute("SELECT text, answer FROM questions WHERE id=?", (a,)).fetchone()
        rb = conn.execute("SELECT text, answer FROM questions WHERE id=?", (b,)).fetchone()
        print(f"\n=== {sim:.3f}  #{a} <-> #{b}")
        print(f"  A: {ra['text'][:120]} | {ra['answer'][:40]}")
        print(f"  B: {rb['text'][:120]} | {rb['answer'][:40]}")

    out = PROJECT_ROOT / "data" / "embeddings" / "duplicates.tsv"
    with open(out, "w", encoding="utf-8") as f:
        f.write("similarity\tquestion_a\tquestion_b\n")
        for sim, a, b in pairs:
            f.write(f"{sim:.4f}\t{a}\t{b}\n")
    print(f"\nполный список: {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--dupes", action="store_true")
    parser.add_argument("--like", type=int, default=None,
                        help="ID вопроса: показать его соседей по смыслу")
    parser.add_argument("--threshold", type=float, default=0.95)
    args = parser.parse_args()

    if args.dupes:
        find_dupes(args.threshold, args.top)
    elif args.like:
        like(args.like, args.top)
    elif args.query:
        search(args.query, args.top)
    else:
        parser.error("нужен запрос или --dupes")


if __name__ == "__main__":
    main()
