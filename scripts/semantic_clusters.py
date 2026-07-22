"""Кластеризация корпуса без таксономии: какие темы выделяет сам корпус.

Сферический k-means по эмбеддингам размеченных вопросов. Для каждого кластера:
размер, доминирующая категория классификатора (и её доля), характерные слова,
вопросы ближе всего к центру. Итог — docs/reports/semantic-clusters.md.

    python scripts/semantic_clusters.py --k 36
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from config import DB_PATH, PROJECT_ROOT
from semantic_search import load_corpus

REPORT = PROJECT_ROOT / "docs" / "reports" / "semantic-clusters.md"
LABEL = "google/gemma-4-26b-a4b-it@p2"

# Слова-паразиты жанра: встречаются везде, тему не выделяют
STOP = {
    "вопрос", "вопроса", "вопросе", "назовите", "ответ", "ответить", "слово",
    "слова", "словом", "двумя", "словами", "этого", "этому", "этот", "этой",
    "него", "неё", "example", "внимание", "который", "которая", "которые",
    "которого", "однажды", "иногда", "например", "именно", "своей", "свою",
    "перед", "после", "время", "первый", "первая", "также", "более", "менее",
    "человек", "человека", "название", "известный", "известного", "года", "году",
}


def words_of(text: str) -> set:
    return {w for w in re.findall(r"[а-яё]{4,}", text.lower()) if w not in STOP}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=36)
    parser.add_argument("--iters", type=int, default=25)
    args = parser.parse_args()

    ids, vecs = load_corpus()
    id2idx = {int(q): i for i, q in enumerate(ids)}

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT qt.question_id, c.name_ru AS cat,
               ROW_NUMBER() OVER (PARTITION BY qt.question_id
                                  ORDER BY qt.confidence DESC) AS rn
        FROM question_topics qt
        JOIN subcategories s ON s.id = qt.subcategory_id
        JOIN categories c ON c.id = s.category_id
        WHERE qt.model_name = ?
        """,
        (LABEL,),
    ).fetchall()
    label = {r["question_id"]: r["cat"] for r in rows if r["rn"] == 1}
    lab_ids = np.array([q for q in label if q in id2idx])
    X = vecs[[id2idx[q] for q in lab_ids]]
    cats = np.array([label[q] for q in lab_ids])
    n = len(X)
    print(f"кластеризую {n} вопросов, k={args.k}")

    # сферический k-means
    rng = np.random.default_rng(42)
    C = X[rng.choice(n, args.k, replace=False)].copy()
    for it in range(args.iters):
        sims = X @ C.T                          # косинусы (всё нормировано)
        assign = sims.argmax(1)
        newC = np.zeros_like(C)
        for j in range(args.k):
            members = X[assign == j]
            if len(members) == 0:
                newC[j] = X[rng.integers(n)]
            else:
                m = members.mean(0)
                newC[j] = m / np.linalg.norm(m)
        shift = float(np.abs(newC - C).max())
        C = newC
        if shift < 1e-4:
            print(f"  сошлось на итерации {it}")
            break

    sims = X @ C.T
    assign = sims.argmax(1)

    # тексты понадобятся для слов и примеров
    texts = {}
    for qid in lab_ids:
        r = conn.execute("SELECT text, answer FROM questions WHERE id=?", (int(qid),)).fetchone()
        texts[int(qid)] = (r["text"] or "", r["answer"] or "") if r else ("", "")

    global_df = Counter()
    doc_words = {}
    for qid in lab_ids:
        w = words_of(texts[int(qid)][0])
        doc_words[int(qid)] = w
        global_df.update(w)

    clusters = []
    for j in range(args.k):
        mask = assign == j
        size = int(mask.sum())
        if size == 0:
            continue
        member_ids = lab_ids[mask]
        cat_share = Counter(cats[mask])
        top_cat, top_n = cat_share.most_common(1)[0]

        df = Counter()
        for qid in member_ids:
            df.update(doc_words[int(qid)])
        scored = []
        for w, c in df.items():
            if c < max(3, size * 0.02):
                continue
            p_c = c / size
            p_g = global_df[w] / n
            if p_c > p_g * 2:
                scored.append((p_c * np.log(p_c / p_g), w))
        top_words = [w for _, w in sorted(scored, reverse=True)[:7]]

        member_sims = sims[mask, j]
        order = np.argsort(-member_sims)[:2]
        examples = [(int(member_ids[o]), texts[int(member_ids[o])]) for o in order]

        clusters.append({
            "size": size, "cat": top_cat, "cat_share": top_n / size,
            "words": top_words, "examples": examples,
            "purity_cats": cat_share.most_common(3),
        })

    clusters.sort(key=lambda c: -c["size"])

    lines = ["# Темы, которые корпус выделил сам", "",
             f"Сферический k-means, k={args.k}, {n} вопросов. Кластер описан характерными "
             "словами, долей главной категории классификатора и вопросами у центра.", ""]
    for i, c in enumerate(clusters, 1):
        share = f"{c['cat_share']*100:.0f}%"
        others = ", ".join(f"{k} {v}" for k, v in c["purity_cats"][1:3])
        lines.append(f"## {i}. {', '.join(c['words'][:4]) or '(без явных слов)'} — {c['size']} вопросов")
        lines.append(f"Категория: **{c['cat']}** ({share}); дальше: {others}")
        lines.append(f"Слова: {', '.join(c['words'])}")
        for qid, (t, a) in c["examples"]:
            t_short = re.sub(r"\s+", " ", t)[:160]
            lines.append(f"- #{qid}: {t_short}… → **{a[:50]}**")
        lines.append("")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"отчёт: {REPORT}")

    # сводка в консоль: категория -> сколько кластеров она возглавляет
    heads = defaultdict(int)
    for c in clusters:
        heads[c["cat"]] += 1
    print("\nкатегория -> кластеров, где она главная:")
    for cat, cnt in sorted(heads.items(), key=lambda x: -x[1]):
        print(f"  {cat:<26} {cnt}")
    mono = sum(1 for c in clusters if c["cat_share"] >= 0.5)
    print(f"\nкластеров с доминантой >=50%: {mono} из {len(clusters)}")


if __name__ == "__main__":
    main()
