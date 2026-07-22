"""Локальные эмбеддинги всех вопросов (на M-серии Apple — бесплатно и быстро).

Модель: intfloat/multilingual-e5-small (384-мерные векторы, хорошо держит
русский). Текст вопроса склеивается с ответом — так поиск и дедупликация
видят и формулировку, и суть.

Результат: data/embeddings/e5s/part_NNNN.npz (ids: int64, vecs: float16,
нормированные). Скрипт возобновляемый: готовые части пропускаются.

Использование:
    python scripts/build_embeddings.py              # весь корпус
    python scripts/build_embeddings.py --limit 500  # проба
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, PROJECT_ROOT
from database.db import get_readonly_connection

MODEL_NAME = "intfloat/multilingual-e5-small"
# v2: текст + ответ + комментарий. Комментарий несёт суть (разгадку, факт),
# без него близнецы с разными формулировками ловятся хуже. Усечение до 500
# символов: суть в начале, хвост с источниками — шум, плюс окно e5 = 512 токенов.
OUT_DIR = PROJECT_ROOT / "data" / "embeddings" / "e5s-qac"
COMMENT_CHARS = 500
CHUNK = 4096          # вопросов в одном .npz
BATCH = 128           # размер батча энкодера


def question_text(row) -> str:
    # e5 обучена с префиксами: passage для корпуса, query для запросов
    text = (row["text"] or "").strip()
    answer = (row["answer"] or "").strip()
    comment = (row["comment"] or "").strip()[:COMMENT_CHARS]
    parts = [f"passage: {text}", f"Ответ: {answer}"]
    if comment:
        parts.append(f"Комментарий: {comment}")
    return " ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL_NAME, device=device)
    print(f"модель {MODEL_NAME} на {device}")

    conn = get_readonly_connection(DB_PATH)
    sql = "SELECT id, text, answer, comment FROM questions ORDER BY id"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql).fetchall()
    print(f"вопросов: {len(rows)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done_parts = {p.name for p in OUT_DIR.glob("part_*.npz")}

    t0 = time.time()
    encoded = 0
    for part_idx in range(0, len(rows), CHUNK):
        part_name = f"part_{part_idx // CHUNK:04d}.npz"
        chunk = rows[part_idx : part_idx + CHUNK]
        if part_name in done_parts:
            continue
        texts = [question_text(r) for r in chunk]
        vecs = model.encode(
            texts,
            batch_size=BATCH,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype(np.float16)
        ids = np.array([r["id"] for r in chunk], dtype=np.int64)
        np.savez(OUT_DIR / part_name, ids=ids, vecs=vecs)
        encoded += len(chunk)
        rate = encoded / (time.time() - t0)
        remaining = (len(rows) - part_idx - len(chunk)) / rate if rate else 0
        print(
            f"{part_name}: +{len(chunk)} ({part_idx + len(chunk)}/{len(rows)}), "
            f"{rate:.0f} в/с, осталось ~{remaining / 60:.0f} мин",
            flush=True,
        )

    print(f"готово за {(time.time() - t0) / 60:.1f} мин")


if __name__ == "__main__":
    main()
