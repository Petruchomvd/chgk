"""Загрузка снапшота словаря советского кино и построение индекса алиасов.

Здесь же происходит отбраковка непригодных для сопоставления записей.
Каждая отбраковка сохраняется с причиной: словарь должен быть объяснимым,
а не «почему-то не нашлось».
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .frequency import LemmaFrequency
from .normalize import FUNCTION_WORDS, is_name_like, normalize_phrase, normalize_text

# Wikidata числит «персонажами» советских фильмов и реальных людей, которых
# играли актёры (Ленин, Эйнштейн, Гитлер). Вопрос про настоящего Эйнштейна —
# не упоминание советского кино, поэтому реальных людей в словарь не берём.
_HUMAN_TYPE = "Q5"

# Профессии, по которым человек считается человеком кино.
# Wikidata числит «в ролях» всех, чья хроника попала в документальный фильм:
# у Сталина и Геринга в профессиях только политика, и вопрос про них — не про кино.
_CINEMA_OCCUPATIONS = ("актер", "актриса", "режиссер")

# Сколько фильмов должно быть у человека, чтобы его узнавали по одной фамилии.
# Иначе безвестный режиссёр одного фильма перехватывает знаменитого однофамильца:
# «Чаплин, Станислав Викторович» (1 фильм) собирал все вопросы про Чарли Чаплина.
MIN_FILMS_FOR_SURNAME = 3


def _is_cinema_person(occupations: Iterable[str]) -> bool:
    for occupation in occupations:
        normalized = (occupation or "").lower().replace("ё", "е")
        if any(marker in normalized for marker in _CINEMA_OCCUPATIONS):
            return True
    return False

# Названия из одного служебного слова невозможно искать без кавычек
_TRIVIAL_TITLES = {
    "он", "она", "они", "оно", "я", "ты", "мы", "вы", "это", "тот", "все",
    "да", "нет", "и", "а", "но", "как", "что", "кто", "где", "когда",
}

# Уточнение в скобках — артефакт Wikidata: «Аврора (мультфильм)», «Жить (фильм, 1933)».
# В тексте вопроса так не пишут, поэтому ищем по названию без уточнения.
_DISAMBIG_RE = re.compile(r"\s*\([^()]*\)\s*$")


def clean_label(label: str) -> str:
    cleaned = _DISAMBIG_RE.sub("", label or "").strip()
    return cleaned or (label or "").strip()


def natural_name(label: str) -> str:
    """Развернуть инвертированное имя Wikidata в обычный порядок.

    «Гайдай, Леонид Иович» → «Леонид Иович Гайдай». В вопросах пишут только так,
    поэтому без разворота такие люди находятся лишь по голой фамилии.
    """
    label = (label or "").strip()
    if "," not in label:
        return label
    surname, _, rest = label.partition(",")
    surname, rest = surname.strip(), rest.strip()
    if not surname or not rest:
        return label
    return f"{rest} {surname}"


def surname_of(label: str) -> Optional[str]:
    """Фамилия из имени человека, как оно записано в Wikidata.

    Форматы разные: «Юрий Никулин», «Юрий Владимирович Никулин» и
    инвертированный «Зевин, Яков Давидович». В последнем фамилия стоит
    до запятой, а не в конце.
    """
    label = (label or "").strip()
    if not label:
        return None
    if "," in label:
        head = normalize_phrase(label.split(",", 1)[0])
        tokens = head.split()
        return tokens[-1] if tokens else None
    tokens = normalize_phrase(label).split()
    if len(tokens) < 2:
        return None
    return tokens[-1]


@dataclass
class Alias:
    """Одна поисковая форма сущности."""

    surface: str          # как написано в Wikidata
    key: str              # нормализованный лемматизированный ключ
    tokens: Tuple[str, ...]
    has_name_token: bool
    distinctive: bool     # есть хотя бы одно редкое в базе слово
    # Омонимы именно этой формы. Хранятся на уровне алиаса, а не фильма:
    # у «Первых огней» (1925) альтернативное название «Маяк», и омонимы
    # («Маяк» — радиостанция, комбинат, маяк) относятся к форме, а не к фильму.
    homonyms: List[Dict[str, str]] = field(default_factory=list)

    @property
    def has_film_homonym(self) -> bool:
        """Так же называется другое кино — киномаркер рядом ничего не доказывает."""
        markers = ("фильм", "кино", "сериал")
        return any(
            any(marker in (h.get("type") or "").lower() for marker in markers)
            for h in self.homonyms
        )

    @property
    def content_tokens(self) -> Tuple[str, ...]:
        """Знаменательные слова названия, без предлогов и союзов."""
        content = tuple(t for t in self.tokens if t not in FUNCTION_WORDS)
        return content or self.tokens

    @property
    def token_count(self) -> int:
        """Число знаменательных слов.

        Именно оно защищает от случайных совпадений: чем длиннее фраза, тем
        невероятнее совпасть с ней случайно. Предлоги в этот счёт не идут —
        иначе «Смотри в корень» выглядит трёхсловным названием, хотя это
        поговорка из двух слов.
        """
        return len(self.content_tokens)


@dataclass
class Entity:
    """Фильм, персонаж или человек кино (актёр/режиссёр) из словаря."""

    key: str              # QID или синтетический ключ
    kind: str             # "film" | "character" | "person"
    label: str
    aliases: List[Alias] = field(default_factory=list)
    year: Optional[int] = None
    types: List[str] = field(default_factory=list)
    directors: List[str] = field(default_factory=list)
    based_on: List[str] = field(default_factory=list)
    homonyms: List[Dict[str, str]] = field(default_factory=list)
    films: List[str] = field(default_factory=list)
    actors: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    wiki_url: Optional[str] = None
    roles: List[str] = field(default_factory=list)   # actor | director
    # Фамилия, по которой человека можно узнать без имени. Заполняется только
    # если она уникальна в словаре и редка в базе вопросов.
    unique_surname: Optional[str] = None

    @property
    def role_label(self) -> str:
        names = {"actor": "актёр", "director": "режиссёр"}
        return ", ".join(names.get(r, r) for r in self.roles) or "—"

    @property
    def has_literary_source(self) -> bool:
        """Есть ли у фильма книжный первоисточник (Wikidata P144).

        Ключевой признак омонимии «фильм или книга»: «Собачье сердце»
        одинаково законно указывает и на повесть, и на фильм.
        """
        return bool(self.based_on)

    @property
    def homonym_types(self) -> List[str]:
        return sorted({h.get("type", "?") for h in self.homonyms})

    @property
    def has_film_homonym(self) -> bool:
        """Так же называется другое кино.

        Это худший вид омонимии для нашей задачи: советский «Джокер» (1991)
        против «Джокера» (2019), «Расплата» против четырёх одноимённых фильмов.
        Киномаркер рядом («в фильме „Джокер“») тогда ничего не доказывает —
        он с равным успехом относится к чужому фильму.
        """
        markers = ("фильм", "кино", "сериал", "мультипликационный фильм")
        return any(
            any(marker in (h.get("type") or "").lower() for marker in markers)
            for h in self.homonyms
        )


@dataclass
class Rejection:
    key: str
    kind: str
    label: str
    reason: str


class CinemaDictionary:
    """Словарь + индекс алиасов для поиска по лемматизированным n-граммам."""

    def __init__(self, frequency: Optional[LemmaFrequency] = None) -> None:
        self.entities: Dict[str, Entity] = {}
        self.by_alias: Dict[str, List[str]] = {}
        self.first_lemmas: set[str] = set()
        self.max_tokens: int = 1
        self.rejections: List[Rejection] = []
        self.meta: Dict = {}
        self.creator_surnames: Dict[str, set[str]] = {}
        self.frequency = frequency
        # Фамилии, которые носят несколько людей кино сразу: по ним без имени
        # опознать человека нельзя
        self.surname_conflicts: Dict[str, int] = {}

    def _is_distinctive_token(self, token: str) -> bool:
        """Редкое ли слово по данным самой базы вопросов.

        Морфология тут бесполезна: pymorphy знает «чебурашка» как обычное
        существительное. А в 212 тысячах вопросов слово встречается 90 раз —
        это и есть настоящий признак небанальности.
        """
        if self.frequency is None:
            return True
        return self.frequency.is_rare(token)

    # --- построение ---

    def _add_entity(
        self,
        entity: Entity,
        alias_surfaces: Iterable[str],
        homonyms_by_form: Optional[Dict[str, List[Dict[str, str]]]] = None,
    ) -> None:
        homonyms_by_form = homonyms_by_form or {}
        seen: set[str] = set()
        for surface in alias_surfaces:
            if not surface or not surface.strip():
                continue
            key = normalize_phrase(surface)
            if not key or key in seen:
                continue
            seen.add(key)
            tokens = tuple(key.split())
            entity.aliases.append(
                Alias(
                    surface=surface.strip(),
                    key=key,
                    tokens=tokens,
                    has_name_token=any(is_name_like(t) for t in tokens),
                    distinctive=any(self._is_distinctive_token(t) for t in tokens),
                    homonyms=homonyms_by_form.get(surface.strip(), []),
                )
            )
        if not entity.aliases:
            self.rejections.append(
                Rejection(entity.key, entity.kind, entity.label, "no_usable_alias")
            )
            return

        self.entities[entity.key] = entity
        for alias in entity.aliases:
            self.by_alias.setdefault(alias.key, []).append(entity.key)
            self.first_lemmas.add(alias.tokens[0])
            # Окно поиска считается по всем словам, включая предлоги:
            # искать нужно всю фразу целиком, а token_count — это только
            # мера небанальности названия.
            self.max_tokens = max(self.max_tokens, len(alias.tokens))

    def load(
        self,
        snapshot_dir: str | Path,
        *,
        hints: Optional[Dict] = None,
    ) -> "CinemaDictionary":
        snapshot_dir = Path(snapshot_dir)
        films_raw = json.loads((snapshot_dir / "films.json").read_text(encoding="utf-8"))
        chars_raw = json.loads((snapshot_dir / "characters.json").read_text(encoding="utf-8"))
        people_path = snapshot_dir / "people.json"
        people_raw = (
            json.loads(people_path.read_text(encoding="utf-8"))
            if people_path.exists()
            else {}
        )
        meta_path = snapshot_dir / "dict_meta.json"
        if meta_path.exists():
            self.meta = json.loads(meta_path.read_text(encoding="utf-8"))

        hints = hints or {}
        exclude = set(hints.get("exclude_entities", []))
        extra_aliases: Dict[str, List[str]] = hints.get("extra_aliases", {})
        blocked_aliases = {normalize_phrase(a) for a in hints.get("blocked_aliases", [])}

        for fid, raw in films_raw.items():
            raw_label = (raw.get("label") or "").strip()
            label = clean_label(raw_label)
            if not label:
                self.rejections.append(Rejection(fid, "film", "", "no_label"))
                continue
            if fid in exclude:
                self.rejections.append(Rejection(fid, "film", label, "excluded_by_hints"))
                continue
            entity = Entity(
                key=fid,
                kind="film",
                label=label,
                year=raw.get("year"),
                types=raw.get("types") or [],
                directors=raw.get("directors") or [],
                based_on=raw.get("based_on") or [],
                homonyms=raw.get("homonyms") or [],
                wiki_url=raw.get("wiki_url"),
            )
            surfaces = [
                label,
                *(clean_label(a) for a in raw.get("aliases") or []),
                *extra_aliases.get(fid, []),
            ]
            self._add_entity(entity, surfaces, raw.get("homonyms_by_form") or {})

        for cid, raw in chars_raw.items():
            label = clean_label((raw.get("label") or "").strip())
            if not label:
                self.rejections.append(Rejection(cid, "character", "", "no_label"))
                continue
            if cid in exclude:
                self.rejections.append(Rejection(cid, "character", label, "excluded_by_hints"))
                continue

            sources = raw.get("sources") or []
            has_qid = bool(raw.get("qid"))
            type_qids = raw.get("type_qids") or []

            # Реальный человек, которого играл актёр, — не персонаж для этой задачи
            if _HUMAN_TYPE in type_qids:
                self.rejections.append(Rejection(cid, "character", label, "real_person"))
                continue

            # Роли, записанные строкой (P4633), — сплошь и рядом не персонажи,
            # а должности: «врач», «сосед», «милиционер». Без имени в составе
            # такая запись даст только ложные срабатывания.
            if not has_qid:
                if not label[0].isupper():
                    self.rejections.append(
                        Rejection(cid, "character", label, "generic_role_lowercase")
                    )
                    continue
                if not any(is_name_like(t) for t in normalize_phrase(label).split()):
                    self.rejections.append(
                        Rejection(cid, "character", label, "generic_role_no_name_token")
                    )
                    continue

            entity = Entity(
                key=cid,
                kind="character",
                label=label,
                films=raw.get("films") or [],
                actors=raw.get("actors") or [],
                sources=sources,
                types=raw.get("types") or [],
            )
            surfaces = [label, *(raw.get("aliases") or []), *extra_aliases.get(cid, [])]
            self._add_entity(entity, surfaces)

        # В ЧГК пишут «Никулин», а не «Юрий Владимирович Никулин», поэтому
        # фамилия нужна как отдельная поисковая форма. Но только если она
        # однозначна: на «Миронова» претендуют и Андрей, и Евгений, а ещё
        # политик, которого в словаре вообще нет.
        surname_owners: Counter = Counter()
        for raw in people_raw.values():
            surname = surname_of(clean_label(raw.get("label") or ""))
            if surname:
                surname_owners[surname] += 1

        for pid, raw in people_raw.items():
            raw_label = clean_label((raw.get("label") or "").strip())
            # Хранится и показывается имя в обычном порядке
            label = natural_name(raw_label)
            if not label:
                self.rejections.append(Rejection(pid, "person", "", "no_label"))
                continue
            if pid in exclude:
                self.rejections.append(Rejection(pid, "person", label, "excluded_by_hints"))
                continue

            occupations = raw.get("occupations") or []
            if not _is_cinema_person(occupations):
                # Сталин и Геринг «снимались» только в том смысле, что их
                # хроника попала в документальные фильмы
                self.rejections.append(
                    Rejection(pid, "person", label, "not_a_cinema_person")
                )
                continue

            entity = Entity(
                key=pid,
                kind="person",
                label=label,
                roles=raw.get("roles") or [],
                films=raw.get("films") or [],
                homonyms=raw.get("homonyms") or [],
                wiki_url=raw.get("wiki_url"),
                types=occupations,
            )
            # Тёзки относятся к полному имени, а не к фамилии отдельно
            homonyms_by_form = (
                {label: entity.homonyms, raw_label: entity.homonyms}
                if entity.homonyms
                else {}
            )
            surfaces = [
                label,
                raw_label,
                *(clean_label(a) for a in raw.get("aliases") or []),
                *extra_aliases.get(pid, []),
            ]

            surname = surname_of(label)
            # Лемматизатор портит фамилии: «Жаков» он считает формой слова «жак»
            # и превращает фамилию в чужой корень, который ловит французов.
            # Если лемма не совпала с самой фамилией — форме верить нельзя.
            surname_intact = bool(surname) and surname == normalize_text(
                (label.split(",")[0] if "," in label else label.split()[-1])
            ).replace("ё", "е")

            if surname and surname_owners[surname] > 1:
                self.surname_conflicts[surname] = surname_owners[surname]
            elif (
                surname
                and surname_intact
                and is_name_like(surname)
                and self._is_distinctive_token(surname)
                and len(entity.films) >= MIN_FILMS_FOR_SURNAME
            ):
                # Фамилия уникальна в словаре, редка в базе, и человек снял или
                # сыграл достаточно, чтобы его узнавали без имени: «Никулин», «Гайдай»
                entity.unique_surname = surname
                surfaces.append(surname.capitalize())

            self._add_entity(entity, surfaces, homonyms_by_form)

        # Явно заблокированные алиасы убираем из индекса, сущность остаётся
        for alias_key in blocked_aliases:
            if alias_key in self.by_alias:
                del self.by_alias[alias_key]

        self._link_characters_to_films(films_raw)
        self._build_creator_index()
        return self

    def _link_characters_to_films(self, films_raw: Dict) -> None:
        """Перенести на персонажа первоисточник его фильмов.

        Нужно, чтобы Остап Бендер честно нёс пометку «есть книжный
        первоисточник», хотя сам по себе персонаж её в Wikidata не имеет.
        """
        for entity in self.entities.values():
            if entity.kind != "character":
                continue
            for fid in entity.films:
                film = films_raw.get(fid) or {}
                for source in film.get("based_on") or []:
                    if source not in entity.based_on:
                        entity.based_on.append(source)

    def _build_creator_index(self) -> None:
        """Фамилии режиссёров и актёров — сильный контекстный признак.

        Если рядом с названием стоит фамилия режиссёра этого фильма,
        совпадение почти наверняка про кино.
        """
        for entity in self.entities.values():
            surnames: set[str] = set()
            for person in [*entity.directors, *entity.actors]:
                for token in normalize_phrase(person).split():
                    if len(token) >= 4 and is_name_like(token):
                        surnames.add(token)
            if surnames:
                self.creator_surnames[entity.key] = surnames

    # --- доступ ---

    def get(self, key: str) -> Optional[Entity]:
        return self.entities.get(key)

    def lookup(self, alias_key: str) -> List[Entity]:
        return [self.entities[k] for k in self.by_alias.get(alias_key, []) if k in self.entities]

    def is_trivial_title(self, alias: Alias) -> bool:
        return alias.token_count == 1 and alias.content_tokens[0] in _TRIVIAL_TITLES

    def shared_title(self, alias_key: str) -> bool:
        """Название носят несколько фильмов сразу («Мать», «Отец»).

        Тогда непонятно, о каком именно фильме речь, и высокой уверенности быть не может.
        """
        return len(self.by_alias.get(alias_key, [])) > 1

    def stats(self) -> Dict:
        kinds = Counter(e.kind for e in self.entities.values())
        reasons = Counter(r.reason for r in self.rejections)
        return {
            "entities": len(self.entities),
            "films": kinds.get("film", 0),
            "characters": kinds.get("character", 0),
            "aliases": sum(len(e.aliases) for e in self.entities.values()),
            "alias_keys": len(self.by_alias),
            "max_alias_tokens": self.max_tokens,
            "films_with_homonyms": sum(
                1 for e in self.entities.values() if e.kind == "film" and e.homonyms
            ),
            "films_with_literary_source": sum(
                1 for e in self.entities.values() if e.kind == "film" and e.has_literary_source
            ),
            "rejected": len(self.rejections),
            "rejected_by_reason": dict(reasons),
        }


def load_dictionary(
    snapshot_dir: str | Path,
    hints_path: Optional[str | Path] = None,
    frequency: Optional[LemmaFrequency] = None,
) -> CinemaDictionary:
    hints = None
    if hints_path and Path(hints_path).exists():
        hints = json.loads(Path(hints_path).read_text(encoding="utf-8"))
    return CinemaDictionary(frequency=frequency).load(snapshot_dir, hints=hints)
