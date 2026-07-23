#!/usr/bin/env python3
"""Подготовить лёгкую серверную выгрузку смысловой карты.

Запускается на Mac, где лежат эмбеддинги и кэш UMAP. Получившийся JSON
можно положить на сервер отдельно от Git и баз данных:

    python scripts/export_semantic_map.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import semantic
from config import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--points", type=int, default=30000)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "embeddings" / "semantic-map.json",
    )
    args = parser.parse_args()

    # Не читать старую выгрузку, если скрипт запускают для её обновления.
    if args.output.resolve() == semantic.MAP_EXPORT_PATH.resolve():
        semantic.MAP_EXPORT_PATH = args.output.with_suffix(".building")

    payload = semantic.map_points(n=args.points)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Готово: {len(payload['points'])} точек, {args.output.stat().st_size:,} байт")


if __name__ == "__main__":
    main()
