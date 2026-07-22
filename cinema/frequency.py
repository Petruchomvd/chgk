"""Частотность лемм в самой базе вопросов.

Зачем: чтобы решить, банально ли название, нужно знать, часто ли слово
встречается в обычном тексте. Морфология для этого не годится — pymorphy знает
«чебурашка» как обычное существительное, а «спасибо» считает редким словом.

Поэтому редкость слова берётся из самой базы: в скольких вопросах оно вообще
встречается. «Один» — в каждом четвёртом, «чебурашка» — в сотне из двухсот тысяч.
Это проверяемо, воспроизводимо и не зависит от чужих словарей.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from .normalize import tokenize

# Доля вопросов, выше которой слово считается обычным.
# 0.1% от 212k вопросов — примерно 213 вопросов.
# Калибровка на реальной базе (доля вопросов со словом):
#   один 37.8% | они 24.1% | время 13.1% | оно 2.5% | джон 1.4% | москва 1.07%
#   андрей 0.80% | зеркало 0.44% | ирония 0.27% | спасибо 0.18%   — обычные
#   карлсон 0.087% | трус 0.080% | чапаев 0.055% | шурик 0.048%
#   чебурашка 0.042% | гараж 0.042% | штирлиц 0.037% | мимино 0.011% — редкие
# Порог 0.2% оставлял «спасибо» редким, и фильм «Спасибо» (1973) ловился на
# обычном «спасибо» в кавычках.
DEFAULT_RARE_RATIO = 0.001


@dataclass
class LemmaFrequency:
    """Сколько вопросов содержит каждую лемму."""

    df: Dict[str, int]
    n_docs: int
    rare_ratio: float = DEFAULT_RARE_RATIO

    @property
    def rare_threshold(self) -> float:
        return self.rare_ratio * self.n_docs

    def document_frequency(self, token: str) -> int:
        return self.df.get(token, 0)

    def is_rare(self, token: str) -> bool:
        """Редко ли слово встречается в базе вопросов."""
        return self.df.get(token, 0) < self.rare_threshold

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {"n_docs": self.n_docs, "rare_ratio": self.rare_ratio, "df": self.df},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "LemmaFrequency":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            df=raw["df"],
            n_docs=raw["n_docs"],
            rare_ratio=raw.get("rare_ratio", DEFAULT_RARE_RATIO),
        )


def build(
    conn: sqlite3.Connection,
    *,
    rare_ratio: float = DEFAULT_RARE_RATIO,
    progress: bool = False,
) -> LemmaFrequency:
    """Посчитать, в скольких вопросах встречается каждая лемма.

    Считается document frequency (вопрос, а не вхождение), чтобы одно длинное
    перечисление не сделало слово «частотным».
    """
    df: Dict[str, int] = {}
    n_docs = 0
    rows = conn.execute("SELECT text, answer, comment FROM questions")
    for text, answer, comment in rows:
        n_docs += 1
        seen = set()
        for field in (text, answer, comment):
            if not field:
                continue
            for token in tokenize(field.lower().replace("ё", "е")):
                seen.add(token.lemma)
        for lemma_value in seen:
            df[lemma_value] = df.get(lemma_value, 0) + 1
        if progress and n_docs % 50000 == 0:
            print(f"  частотность: {n_docs:,} вопросов".replace(",", " "))
    return LemmaFrequency(df=df, n_docs=n_docs, rare_ratio=rare_ratio)


def load_or_build(
    path: str | Path,
    conn: sqlite3.Connection,
    *,
    rebuild: bool = False,
    progress: bool = False,
) -> LemmaFrequency:
    path = Path(path)
    if path.exists() and not rebuild:
        return LemmaFrequency.load(path)
    freq = build(conn, progress=progress)
    path.parent.mkdir(parents=True, exist_ok=True)
    freq.save(path)
    return freq
