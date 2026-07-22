"""Нормализация русского текста для сопоставления со словарём кино.

Выделено в отдельный модуль, потому что нормализация должна быть одинаковой
для словаря и для текста вопросов: любое расхождение здесь молча ломает
сопоставление.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import List, NamedTuple

# Комбинируемые знаки ударения, которые встречаются в вики-названиях
_STRESS_MARKS = {"́", "̀"}

# Кавычки всех сортов приводим к одному виду, чтобы «...», "...", „...“ ловились одинаково
_QUOTE_CHARS = "«»\"“”„‟‘’`"

_TOKEN_RE = re.compile(r"[а-яa-z0-9]+")

# Служебные слова. Не несут смысла и не должны считаться за «длину названия»:
# «Смотри в корень» — это два знаменательных слова и известная поговорка,
# а не небанальная трёхсловная фраза, случайно совпасть с которой невозможно.
FUNCTION_WORDS = frozenset(
    {
        "и", "в", "во", "на", "с", "со", "о", "об", "обо", "от", "ото", "до",
        "за", "по", "у", "к", "ко", "из", "изо", "не", "ни", "а", "но", "или",
        "то", "же", "бы", "ли", "для", "при", "про", "под", "подо", "над",
        "надо", "без", "через", "между", "как", "что", "это", "да", "тоже",
        "уже", "ещё", "еще", "их", "его", "её", "ее", "их", "мой", "твой",
        "свой", "весь", "всё", "все", "тот", "этот", "такой",
    }
)

_morph = None


def _get_morph():
    """Ленивая инициализация pymorphy3 (загрузка словарей ~1 c)."""
    global _morph
    if _morph is None:
        import pymorphy3

        _morph = pymorphy3.MorphAnalyzer()
    return _morph


def strip_stress(text: str) -> str:
    """Убрать комбинируемые ударения (Wikidata: «Ирони́я судьбы»)."""
    decomposed = unicodedata.normalize("NFD", text)
    cleaned = "".join(ch for ch in decomposed if ch not in _STRESS_MARKS)
    return unicodedata.normalize("NFC", cleaned)


def normalize_text(text: str) -> str:
    """Привести текст к единому виду: lower, ё→е, без ударений, один пробел."""
    text = strip_stress(text).lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@lru_cache(maxsize=500_000)
def lemma(token: str) -> str:
    """Лемма слова. Числа и латиница остаются как есть.

    Кэш обязателен: без него прогон по 212k вопросов упирается в pymorphy.
    """
    if token.isdigit() or token.isascii():
        return token
    parsed = _get_morph().parse(token)
    if not parsed:
        return token
    return parsed[0].normal_form.replace("ё", "е")


@lru_cache(maxsize=200_000)
def is_name_like(token: str) -> bool:
    """Похоже ли слово на имя/фамилию/отчество по морфологии.

    Нужно, чтобы отличить «Штирлиц» (фамилия) от «Трус» (обычное
    существительное, хотя это и имя персонажа Никулина).
    """
    for parse in _get_morph().parse(token):
        if {"Name", "Surn", "Patr"} & set(parse.tag.grammemes):
            return True
    return False


@lru_cache(maxsize=200_000)
def is_generic_proper(token: str) -> bool:
    """Личное имя или географическое название.

    Такие слова — собственные, но высокочастотные: «Андрей», «Джон», «Москва»
    встречаются в вопросах постоянно и сами по себе не указывают на кино,
    даже если так называется какой-нибудь фильм 1927 года.
    """
    for parse in _get_morph().parse(token):
        grammemes = set(parse.tag.grammemes)
        if "Surn" in grammemes:
            continue
        if {"Name", "Geox"} & grammemes:
            return True
    return False


@lru_cache(maxsize=200_000)
def is_common_word(token: str) -> bool:
    """Является ли слово обычным словом языка (не только именем собственным)."""
    parses = _get_morph().parse(token)
    if not parses:
        return False
    for parse in parses:
        grammemes = set(parse.tag.grammemes)
        if {"Name", "Surn", "Patr", "Geox", "Orgn"} & grammemes:
            continue
        if parse.score >= 0.1:
            return True
    return False


class Token(NamedTuple):
    surface: str
    lemma: str
    start: int
    end: int


def tokenize(text: str) -> List[Token]:
    """Разбить нормализованный текст на токены с сохранением смещений.

    Смещения указывают в переданный текст, поэтому вызывать нужно на том же
    тексте, что потом идёт в доказательства.
    """
    tokens: List[Token] = []
    for match in _TOKEN_RE.finditer(text):
        surface = match.group(0)
        tokens.append(Token(surface, lemma(surface), match.start(), match.end()))
    return tokens


def normalize_phrase(phrase: str) -> str:
    """Ключ фразы: нормализация + лемматизация каждого слова.

    «Иронии судьбы» и «Ирония судьбы» дают один ключ.
    """
    normalized = normalize_text(phrase)
    tokens = _TOKEN_RE.findall(normalized)
    if not tokens:
        return ""
    return " ".join(lemma(t) for t in tokens)


def strip_quotes(text: str) -> str:
    """Убрать обрамляющие кавычки и финальную пунктуацию."""
    return text.strip().strip(_QUOTE_CHARS).strip().strip(".!?,;:").strip()
