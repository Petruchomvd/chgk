"""Поиск упоминаний советского кино в тексте вопроса ЧГК.

Главный принцип: совпадение строки — это ещё не факт упоминания фильма.
«Война и мир» — роман, «Гараж» — постройка, «Трус» — обычное слово.
Поэтому каждое совпадение получает правило и уровень уверенности, выведенные
из проверяемых признаков (кавычки, киномаркеры рядом, фамилия режиссёра,
наличие омонимов в Wikidata, наличие книжного первоисточника), а отбракованные
совпадения сохраняются с причиной.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from .dictionary import Alias, CinemaDictionary, Entity
from .normalize import Token, is_generic_proper, is_name_like, lemma, tokenize

# Окно, которое сохраняется в доказательство, символов в каждую сторону
CONTEXT_RADIUS = 160

# Окно, в котором маркер считается относящимся к совпадению.
# Шире брать нельзя: в вопросе ЧГК слово «фильм» почти всегда где-нибудь есть,
# и на радиусе 160 любое обычное слово, совпавшее с названием, получало «high».
NEAR_RADIUS = 70

# Сильные киномаркеры: рядом с ними речь почти наверняка про кино.
CINEMA_STRONG = {
    "фильм", "кинофильм", "кинокартина", "мультфильм", "телефильм",
    "кино", "кинематограф", "кинематографист", "экранизация", "экранизировать",
    "режиссер", "кинорежиссер", "сериал", "актер", "актриса", "киноактер",
    "мосфильм", "ленфильм", "союзмультфильм", "киностудия", "кинокомедия",
    "кинолента", "киногерой", "мультик", "мультипликация", "кинозал",
    "кинотеатр", "кинопремия", "киносказка", "кинороль", "дубляж",
}

# Слабые маркеры: сами по себе ничего не доказывают («картина» — ещё и живопись,
# «лента» — ещё и тесьма, «роль» — ещё и театр), но поддерживают среднюю уверенность.
CINEMA_WEAK = {
    "картина", "лента", "комедия", "кадр", "эпизод", "роль", "сыграть",
    "снять", "снимать", "съемка", "герой", "персонаж", "реплика", "цитата",
    "экран", "премьера", "сценарий", "сценарист", "оператор", "титры",
    "серия", "телевидение", "зритель",
}

# Маркеры литературного первоисточника: если они есть, а киномаркеров нет,
# то «Собачье сердце» — скорее повесть, чем фильм.
LITERARY_MARKERS = {
    "роман", "повесть", "рассказ", "книга", "поэма", "пьеса", "писатель",
    "новелла", "произведение", "глава", "страница", "литература", "литературный",
    "автор", "перевод", "издание", "рукопись", "сборник", "стихотворение",
}

# Кавычки, которыми в ЧГК-текстах выделяют названия
_QUOTED_RE = re.compile(r"[«\"„“](?P<inner>[^«»\"„“”]{1,150})[»\"“”]")

_STRESS_RE = re.compile("[̀́]")


@dataclass
class Match:
    """Подтверждённое упоминание с доказательствами."""

    entity_key: str
    entity_kind: str
    field: str
    rule: str
    confidence: str          # high | medium | low
    matched_text: str        # точный фрагмент из исходного текста
    start: int
    end: int
    context: str
    alias: str
    flags: List[str] = field(default_factory=list)
    # Есть ли рядом киномаркер. Отдельно от уверенности: уверенность — про то,
    # тот ли это человек/фильм, а это — про то, в какой роли его упомянули.
    # Высоцкого чаще вспоминают как барда, а не как актёра.
    cinema_context: bool = False


@dataclass
class Rejected:
    """Отбракованное совпадение — тоже сохраняем, с причиной."""

    entity_key: str
    entity_kind: str
    field: str
    reason: str
    matched_text: str
    context: str


@dataclass
class FieldResult:
    matches: List[Match] = field(default_factory=list)
    rejected: List[Rejected] = field(default_factory=list)


def _prepare(text: str) -> Tuple[str, Optional[List[int]]]:
    """Текст для сканирования: lower + ё→е, смещения 1:1 к исходному.

    Ударения приходится удалять (иначе «Ирони́я» распадётся на два токена),
    поэтому в этом редком случае возвращаем карту смещений.
    """
    scan = text.lower().replace("ё", "е")
    if not _STRESS_RE.search(scan):
        return scan, None
    chars: List[str] = []
    offsets: List[int] = []
    for i, ch in enumerate(scan):
        if _STRESS_RE.match(ch):
            continue
        chars.append(ch)
        offsets.append(i)
    return "".join(chars), offsets


def _quoted_spans(scan: str) -> List[Tuple[int, int]]:
    return [(m.start("inner"), m.end("inner")) for m in _QUOTED_RE.finditer(scan)]


def _in_quotes(start: int, end: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(s <= start and end <= e for s, e in spans)


def _exactly_quoted(start: int, end: int, spans: Sequence[Tuple[int, int]]) -> bool:
    """Совпадение занимает кавычки целиком: «Ирония судьбы», а не «Ирония судьбы, или…»."""
    return any(s == start and end == e for s, e in spans)


@dataclass
class Context:
    has_strong: bool
    has_weak: bool
    has_literary: bool
    creator_hits: set

    @property
    def cinema(self) -> bool:
        return self.has_strong or bool(self.creator_hits)


class CinemaMatcher:
    def __init__(self, dictionary: CinemaDictionary) -> None:
        self.dict = dictionary

    # --- контекст ---

    def _context_lemmas(
        self, tokens: Sequence[Token], lo: int, hi: int, scan: str
    ) -> Tuple[set, str]:
        """Леммы ближнего окна (для решения) + текст широкого окна (для доказательства)."""
        near_start = max(0, tokens[lo].start - NEAR_RADIUS)
        near_end = min(len(scan), tokens[hi].end + NEAR_RADIUS)
        lemmas = set()
        for idx, token in enumerate(tokens):
            if lo <= idx <= hi:
                continue
            if token.end > near_start and token.start < near_end:
                lemmas.add(token.lemma)
        wide = scan[
            max(0, tokens[lo].start - CONTEXT_RADIUS) : min(
                len(scan), tokens[hi].end + CONTEXT_RADIUS
            )
        ]
        return lemmas, wide

    def _analyze_context(
        self, ctx_lemmas: set, entity: Entity
    ) -> Context:
        return Context(
            has_strong=bool(ctx_lemmas & CINEMA_STRONG),
            has_weak=bool(ctx_lemmas & CINEMA_WEAK),
            has_literary=bool(ctx_lemmas & LITERARY_MARKERS),
            creator_hits=ctx_lemmas & self.dict.creator_surnames.get(entity.key, set()),
        )

    # --- правила ---

    def _judge_film(
        self,
        entity: Entity,
        alias: Alias,
        *,
        in_quotes: bool,
        exact_quotes: bool,
        whole_field: bool,
        capitalized: bool,
        ctx: Context,
    ) -> Tuple[Optional[str], Optional[str], List[str]]:
        """Вернуть (правило, уверенность, флаги) либо (None, причина отказа, флаги)."""
        flags: List[str] = []
        if alias.homonyms:
            flags.append("has_homonyms")
        if entity.has_literary_source:
            flags.append("has_literary_source")
        if ctx.has_literary:
            flags.append("literary_marker_nearby")
        if ctx.creator_hits:
            flags.append("creator_nearby")
        if alias.has_film_homonym:
            flags.append("homonym_is_also_a_film")

        # Название из служебного слова («Он», «Они», «Оно») не ищется никогда,
        # даже в кавычках: в ЧГК «ОНИ» — это почти всегда замена, а «Оно»
        # в кавычках — обычно Стивен Кинг, а не советский фильм 1989 года.
        if self.dict.is_trivial_title(alias):
            return None, "trivial_title", flags

        ambiguous = bool(alias.homonyms) or entity.has_literary_source
        # Первоисточник рядом назван, а кино — нет: почти наверняка речь о книге
        book_context = ctx.has_literary and not ctx.has_strong and not ctx.creator_hits

        n = alias.token_count
        # Название, целиком состоящее из обычных слов языка («Одна», «Время»,
        # «Первые огни»), совпадает с текстом по случайности постоянно.
        # Длина фразы — защита: случайно совпасть на 3+ словах почти невозможно.
        weak_form = not alias.distinctive and n <= 2

        # Название с маленькой буквы — это не название: «он поставил машину
        # в гараж» против «в фильме «Гараж»».
        if n == 1 and not capitalized:
            return None, "not_capitalized", flags

        if whole_field:
            if n == 1 and not alias.distinctive:
                return None, "common_word_answer", flags
            if not ctx.cinema:
                # Ответ сам по себе ничего не доказывает: «Юрий Гагарин» —
                # это ответ про космонавта, а не про одноимённый фильм 1969 года,
                # если в вопросе нет ни слова о кино.
                return None, "answer_without_cinema_context", flags
            if ambiguous:
                return "answer_exact_title_ambiguous", "medium", flags
            if n == 1:
                return "answer_exact_title_single_word", "medium", flags
            if weak_form:
                # Ответ «Крестовый поход» — это ответ про крестовый поход,
                # даже если так называется и какой-то фильм 1930 года.
                return "answer_exact_title_weak", "medium", flags
            return "answer_exact_title", "high", flags

        if in_quotes:
            if not exact_quotes:
                # Название найдено внутри более длинной цитаты — само по себе
                # это ничего не значит.
                if ctx.cinema and not weak_form:
                    return "quoted_title_partial_with_context", "medium", flags
                return None, "partial_quote_no_context", flags
            if ctx.cinema:
                if alias.has_film_homonym and not ctx.creator_hits:
                    # «В фильме „Джокер“…» — но одноимённых фильмов несколько,
                    # и киномаркер не говорит, о каком именно речь.
                    return "quoted_title_with_context_film_homonym", "medium", flags
                if book_context:
                    return "quoted_title_mixed_context", "medium", flags
                return "quoted_title_with_context", "high", flags
            if book_context and entity.has_literary_source:
                return "quoted_title_literary_context", "low", flags
            if n == 1 and not alias.distinctive:
                # «Время» в кавычках — скорее газета или программа, чем фильм 1983 года
                return None, "common_word_quoted_no_context", flags
            if ambiguous:
                return "quoted_title_ambiguous", "medium", flags
            return "quoted_title", "high", flags

        # Голое упоминание без кавычек
        if ctx.creator_hits:
            if weak_form:
                return "title_with_creator_context_weak", "medium", flags
            return "title_with_creator_context", "high", flags
        if ctx.has_strong:
            if weak_form:
                # Ровно этот случай раньше давал ложный топ: обычное слово +
                # слово «фильм» где-то рядом.
                return None, "common_phrase_bare_weak_evidence", flags
            if alias.has_film_homonym:
                return "title_with_cinema_context_film_homonym", "medium", flags
            if book_context:
                return "title_with_mixed_context", "medium", flags
            if ambiguous:
                # Без кавычек и при живых омонимах («Аврора», «Зеркало»)
                # киномаркер рядом — основание, но не доказательство.
                return "title_with_cinema_context_ambiguous", "medium", flags
            return "title_with_cinema_context", "high", flags
        if weak_form:
            return None, "common_phrase_bare_no_context", flags
        if n >= 3 and not ambiguous:
            return "bare_title_multiword", "medium", flags
        if n >= 3:
            return "bare_title_multiword_ambiguous", "low", flags
        if alias.distinctive and ctx.has_weak:
            return "bare_title_weak_context", "low", flags
        return None, "short_title_no_context", flags

    def _judge_character(
        self,
        entity: Entity,
        alias: Alias,
        *,
        whole_field: bool,
        exact_quotes: bool,
        capitalized: bool,
        ctx: Context,
    ) -> Tuple[Optional[str], Optional[str], List[str]]:
        flags: List[str] = []
        if entity.based_on:
            flags.append("has_literary_source")
        if ctx.has_literary:
            flags.append("literary_marker_nearby")
        if ctx.creator_hits:
            flags.append("creator_nearby")

        # Имя персонажа — имя собственное. «трус» с маленькой буквы — это
        # характеристика человека, а не герой «Кавказской пленницы».
        if not capitalized:
            return None, "not_capitalized", flags

        if alias.token_count >= 2 and alias.has_name_token:
            if ctx.cinema:
                return "character_full_name_with_context", "high", flags
            if whole_field:
                # Ответ равен имени персонажа, но вопрос не про кино
                return "character_full_name_answer", "medium", flags
            return "character_full_name", "medium", flags

        if alias.token_count == 1:
            token = alias.content_tokens[0]
            if is_generic_proper(token):
                # Просто личное имя или топоним: «Андрей», «Джон», «Джим».
                # Такой персонаж есть в каждом втором фильме, а имя — в каждом
                # втором вопросе. Редкость имени тут не помогает: «Джим» редок
                # в базе, но всё равно ничей не опознавательный знак.
                if exact_quotes and ctx.cinema:
                    return "character_given_name_quoted", "medium", flags
                return None, "generic_given_name_no_context", flags
            if alias.distinctive:
                # Уникальное имя: «Штирлиц», «Чебурашка», «Мимино»
                if ctx.cinema:
                    return "character_rare_name_with_context", "high", flags
                if whole_field:
                    return "character_rare_name_answer", "medium", flags
                return "character_rare_name", "medium", flags
            # Обычное слово в роли имени: «Трус», «Бывалый», «Балбес».
            # Без киноконтекста это просто слово.
            if ctx.has_strong:
                return "character_common_name_with_context", "medium", flags
            if ctx.creator_hits:
                return "character_common_name_with_creator", "medium", flags
            return None, "common_word_name_no_context", flags

        if ctx.cinema:
            return "character_alias_with_context", "medium", flags
        return None, "character_alias_no_context", flags

    def _judge_person(
        self,
        entity: Entity,
        alias: Alias,
        *,
        whole_field: bool,
        capitalized: bool,
        ctx: Context,
    ) -> Tuple[Optional[str], Optional[str], List[str]]:
        """Актёр или режиссёр.

        В отличие от фильмов, здесь уверенность отвечает только на вопрос
        «тот ли это человек». Про кино ли упоминание — отдельный признак
        (cinema_context), потому что половина советских актёров знаменита
        не только кино: Высоцкий — бард, Никулин — клоун.
        """
        flags: List[str] = []
        if alias.homonyms:
            flags.append("has_namesakes")

        if not capitalized:
            return None, "not_capitalized", flags

        n = alias.token_count
        if n >= 2 and alias.has_name_token:
            # Полное имя: «Юрий Никулин», «Леонид Гайдай»
            if alias.homonyms and not ctx.cinema:
                # Есть тёзки, и ничто не указывает, что речь о киношнике
                return "person_full_name_namesake", "medium", flags
            return "person_full_name", "high", flags

        if n == 1:
            token = alias.content_tokens[0]
            if entity.unique_surname and token == entity.unique_surname:
                # Фамилия уникальна в словаре и редка в базе: «Гайдай», «Никулин»
                if ctx.cinema:
                    return "person_surname_with_context", "high", flags
                return "person_surname", "medium", flags
            if is_generic_proper(token):
                return None, "generic_given_name_no_context", flags
            if ctx.has_strong:
                return "person_name_with_context", "medium", flags
            return None, "person_name_no_context", flags

        if ctx.cinema:
            return "person_alias_with_context", "medium", flags
        return None, "person_alias_no_context", flags

    # --- основной проход ---

    def match_field(
        self,
        text: str,
        field_name: str,
        *,
        extra_context_lemmas: Optional[set] = None,
    ) -> FieldResult:
        """Найти упоминания в одном поле.

        extra_context_lemmas — контекст из другого поля. Нужен для ответов:
        сам по себе ответ «Юрий Гагарин» ничего не говорит о кино, и понять,
        имеется ли в виду одноимённый фильм 1969 года, можно только по тексту
        вопроса.
        """
        result = FieldResult()
        if not text or not text.strip():
            return result

        scan, offsets = _prepare(text)
        tokens = tokenize(scan)
        if not tokens:
            return result

        spans = _quoted_spans(scan)
        whole_key = " ".join(t.lemma for t in tokens)

        def to_orig(index: int) -> int:
            return offsets[index] if offsets and index < len(offsets) else index

        i = 0
        max_len = self.dict.max_tokens
        while i < len(tokens):
            if tokens[i].lemma not in self.dict.first_lemmas:
                i += 1
                continue

            hit_len = 0
            for length in range(min(max_len, len(tokens) - i), 0, -1):
                key = " ".join(t.lemma for t in tokens[i : i + length])
                entities = self.dict.lookup(key)
                if not entities:
                    continue

                lo, hi = i, i + length - 1
                start_c, end_c = tokens[lo].start, tokens[hi].end
                ctx_lemmas, ctx_text = self._context_lemmas(tokens, lo, hi, scan)
                if extra_context_lemmas:
                    ctx_lemmas = ctx_lemmas | extra_context_lemmas
                in_q = _in_quotes(start_c, end_c, spans)
                exact_q = _exactly_quoted(start_c, end_c, spans)
                whole_field = key == whole_key

                matched_orig = text[to_orig(start_c) : to_orig(end_c - 1) + 1]
                capitalized = bool(matched_orig) and matched_orig[0].isupper()

                # ЧГК-замена: «ОНИ», «ИКС», «АЛЬФА» капсом — это подстановка
                # вместо настоящего слова, а не название фильма. Исключение —
                # если и в словаре название записано капсом.
                letters = [c for c in matched_orig if c.isalpha()]
                dict_forms_upper = any(
                    a.surface.isupper()
                    for e in entities
                    for a in e.aliases
                    if a.key == key
                )
                if (
                    len(letters) > 1
                    and all(c.isupper() for c in letters)
                    and not dict_forms_upper
                ):
                    for entity in entities:
                        result.rejected.append(
                            Rejected(
                                entity_key=entity.key,
                                entity_kind=entity.kind,
                                field=field_name,
                                reason="chgk_substitution_caps",
                                matched_text=matched_orig,
                                context=ctx_text,
                            )
                        )
                    hit_len = length
                    break

                for entity in entities:
                    alias = next(
                        (a for a in entity.aliases if a.key == key), entity.aliases[0]
                    )
                    ctx = self._analyze_context(ctx_lemmas, entity)
                    if entity.kind == "film":
                        rule, verdict, flags = self._judge_film(
                            entity,
                            alias,
                            in_quotes=in_q,
                            exact_quotes=exact_q,
                            whole_field=whole_field,
                            capitalized=capitalized,
                            ctx=ctx,
                        )
                    elif entity.kind == "person":
                        rule, verdict, flags = self._judge_person(
                            entity,
                            alias,
                            whole_field=whole_field,
                            capitalized=capitalized,
                            ctx=ctx,
                        )
                    else:
                        rule, verdict, flags = self._judge_character(
                            entity,
                            alias,
                            whole_field=whole_field,
                            exact_quotes=exact_q,
                            capitalized=capitalized,
                            ctx=ctx,
                        )

                    # Одно название на несколько фильмов («Мать», «Отец»):
                    # какой именно имелся в виду — неизвестно.
                    if rule and self.dict.shared_title(key):
                        flags = [*flags, "title_shared_by_several_films"]
                        if verdict == "high":
                            verdict = "medium"

                    if rule is None:
                        result.rejected.append(
                            Rejected(
                                entity_key=entity.key,
                                entity_kind=entity.kind,
                                field=field_name,
                                reason=verdict or "unknown",
                                matched_text=matched_orig,
                                context=ctx_text,
                            )
                        )
                    else:
                        result.matches.append(
                            Match(
                                entity_key=entity.key,
                                entity_kind=entity.kind,
                                field=field_name,
                                rule=rule,
                                confidence=verdict or "low",
                                matched_text=matched_orig,
                                start=to_orig(start_c),
                                end=to_orig(end_c - 1) + 1,
                                context=ctx_text,
                                alias=alias.surface,
                                flags=flags,
                                cinema_context=ctx.cinema or ctx.has_weak,
                            )
                        )
                hit_len = length
                break

            i += hit_len if hit_len else 1

        return result

    def match_question(self, row: Dict[str, Optional[str]]) -> FieldResult:
        """Пройти по трём полям вопроса. Поля считаются раздельно.

        Раздельно — в подсчёте, но не в понимании: ответ оценивается с оглядкой
        на текст вопроса, иначе ответ «Юрий Гагарин» неотличим от упоминания
        одноимённого фильма.
        """
        combined = FieldResult()
        question_text = row.get("text") or ""
        question_lemmas = (
            {t.lemma for t in tokenize(question_text.lower().replace("ё", "е"))}
            if question_text
            else set()
        )

        for field_name in ("text", "answer", "comment"):
            part = self.match_field(
                row.get(field_name) or "",
                field_name,
                extra_context_lemmas=question_lemmas if field_name == "answer" else None,
            )
            combined.matches.extend(part.matches)
            combined.rejected.extend(part.rejected)
        return combined
