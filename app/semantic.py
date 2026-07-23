"""Семантический слой API: похожие вопросы и поиск по смыслу.

Векторы строит scripts/build_embeddings.py (e5-small, локально).
Грузятся лениво при первом запросе и живут в памяти процесса (~320 МБ).
Модель нужна ТОЛЬКО для текстового поиска — «похожие вопросы» обходятся
одними векторами.
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from config import PROJECT_ROOT

EMB_DIR = PROJECT_ROOT / "data" / "embeddings" / "e5s-qac"
MODEL_NAME = "intfloat/multilingual-e5-small"
MAP_EXPORT_PATH = Path(
    os.environ.get(
        "CHGK_SEMANTIC_MAP_PATH",
        PROJECT_ROOT / "data" / "embeddings" / "semantic-map.json",
    )
).expanduser()

_lock = threading.Lock()
_ids: Optional[np.ndarray] = None
_vecs: Optional[np.ndarray] = None
_model = None


def available() -> bool:
    return any(EMB_DIR.glob("part_*.npz"))


def map_available() -> bool:
    """Карта может работать по готовой лёгкой выгрузке без векторов."""
    return MAP_EXPORT_PATH.is_file() or available()


def text_search_available() -> bool:
    """Произвольный поиск требует и векторов, и модели кодирования запроса."""
    return available() and importlib.util.find_spec("sentence_transformers") is not None


def _ensure_vectors():
    global _ids, _vecs
    with _lock:
        if _vecs is None:
            parts = sorted(EMB_DIR.glob("part_*.npz"))
            if not parts:
                raise FileNotFoundError(
                    "нет векторов: python scripts/build_embeddings.py"
                )
            ids_l, vec_l = [], []
            for p in parts:
                data = np.load(p)
                ids_l.append(data["ids"])
                vec_l.append(data["vecs"])
            _ids = np.concatenate(ids_l)
            _vecs = np.concatenate(vec_l).astype(np.float32)
    return _ids, _vecs


def _ensure_model():
    global _model
    with _lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            import torch

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            _model = SentenceTransformer(MODEL_NAME, device=device)
    return _model


def similar_ids(question_id: int, top: int = 10) -> List[Tuple[int, float]]:
    """Соседи вопроса по смыслу: (id, косинус), сам вопрос исключён."""
    ids, vecs = _ensure_vectors()
    idx = np.where(ids == question_id)[0]
    if len(idx) == 0:
        return []
    sims = vecs @ vecs[idx[0]]
    sims[idx[0]] = -1.0
    top_idx = np.argsort(-sims)[:top]
    return [(int(ids[i]), float(sims[i])) for i in top_idx]


_map_cache: dict = {}


def map_points(n: int = 30000,
               model_label: str = "google/gemma-4-26b-a4b-it@p2") -> dict:
    """Точки смысловой карты: PCA-проекция размеченных вопросов.

    Считается один раз на процесс и кэшируется: загрузка векторов — секунды,
    сама проекция — мгновенна. Выборка детерминирована (seed), чтобы карта
    не прыгала между перезагрузками страницы.
    """
    key = (n, model_label)
    if key in _map_cache:
        return _map_cache[key]

    # На небольшом сервере не держим ~320 МБ векторов ради уже построенной
    # двухмерной карты. Выгрузка готовится на Mac и содержит только точки.
    if MAP_EXPORT_PATH.is_file():
        result = json.loads(MAP_EXPORT_PATH.read_text(encoding="utf-8"))
        _map_cache[key] = result
        return result

    import re
    import sqlite3

    from config import DB_PATH

    ids, vecs = _ensure_vectors()
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
        (model_label,),
    ).fetchall()
    label = {r["question_id"]: r["cat"] for r in rows if r["rn"] == 1}
    lab = [(q, c) for q, c in label.items() if q in id2idx]

    rng = np.random.default_rng(7)
    if len(lab) > n:
        sel = rng.choice(len(lab), n, replace=False)
        lab = [lab[i] for i in sel]

    # Проекция кэшируется на диск: UMAP на 12k точек считается ~минуту,
    # а прыгать между запусками сервера карта не должна.
    cache = EMB_DIR.parent / f"map2d-umap-n{n}.npz"
    sel_ids = np.array([q for q, _ in lab])
    P = None
    if cache.exists():
        d = np.load(cache)
        if np.array_equal(d["ids"], sel_ids):
            P = d["xy"]
    if P is None:
        X = vecs[[id2idx[q] for q, _ in lab]]
        try:
            import umap  # нелинейная проекция: разворачивает кластеры,
                         # которые PCA сплющивает в конфетти (2D PCA — 3.7% дисперсии)
            P = umap.UMAP(
                n_neighbors=15, min_dist=0.08, metric="cosine", random_state=42
            ).fit_transform(X)
        except ImportError:
            Xc = X - X.mean(0)
            cov = Xc.T @ Xc / len(Xc)
            _, V = np.linalg.eigh(cov)
            P = Xc @ V[:, -2:]
        np.savez(cache, ids=sel_ids, xy=P.astype(np.float32))

    tech = {
        r["question_id"]: r["primary_technique"]
        for r in conn.execute(
            "SELECT question_id, primary_technique FROM question_techniques"
        )
    }

    points = []
    for j, (qid, cat) in enumerate(lab):
        r = conn.execute(
            "SELECT text, answer FROM questions WHERE id = ?", (qid,)
        ).fetchone()
        if not r:
            continue
        points.append({
            "id": qid,
            "x": round(float(P[j, 0]), 3),
            "y": round(float(P[j, 1]), 3),
            "c": cat,
            "h": tech.get(qid, "чистый"),
            "t": re.sub(r"\s+", " ", r["text"] or "")[:110],
            "a": (r["answer"] or "")[:40],
        })
    conn.close()

    result = {"available": True, "points": points}
    _map_cache[key] = result
    return result


def search_ids(query: str, top: int = 20) -> List[Tuple[int, float]]:
    """Поиск по смыслу. Первый вызов грузит модель (~5-10 с), дальше быстро."""
    model = _ensure_model()
    ids, vecs = _ensure_vectors()
    qv = model.encode(
        [f"query: {query}"], normalize_embeddings=True
    )[0].astype(np.float32)
    sims = vecs @ qv
    top_idx = np.argsort(-sims)[:top]
    return [(int(ids[i]), float(sims[i])) for i in top_idx]
