"""Тесты пайплайна упоминаний советского кино.

Проверяют главное обещание пайплайна: совпадение строки само по себе не
считается упоминанием фильма, а всё засчитанное имеет проверяемое основание.

Словарь здесь синтетический — тесты не ходят в сеть и не зависят от снапшота.
"""

import json

import pytest

from cinema.dictionary import load_dictionary
from cinema.frequency import LemmaFrequency
from cinema.matcher import CinemaMatcher
from cinema.store import (
    drop_other_runs,
    finish_run,
    integrity_snapshot,
    save_matches,
    start_run,
    sync_entities,
)
from database.db import get_connection, insert_questions, upsert_pack

FILMS = {
    # Однозначный фильм: омонимов нет, первоисточника нет
    "Q1": {
        "qid": "Q1",
        "label": "Ирония судьбы, или С лёгким паром!",
        "year": 1975,
        "aliases": ["Ирония судьбы"],
        "types": ["телефильм"],
        "directors": ["Эльдар Рязанов"],
        "based_on": [],
        "wiki_url": None,
        "homonyms": [],
    },
    # Фильм по книге: название одинаково законно указывает на повесть
    "Q2": {
        "qid": "Q2",
        "label": "Собачье сердце",
        "year": 1988,
        "aliases": [],
        "types": ["телефильм"],
        "directors": ["Владимир Бортко"],
        "based_on": ["Собачье сердце"],
        "wiki_url": None,
        "homonyms": [{"qid": "Q900", "type": "повесть"}],
        "homonyms_by_form": {"Собачье сердце": [{"qid": "Q900", "type": "повесть"}]},
    },
    # Короткое название-омоним. Слово редкое в базе (0.042% вопросов),
    # поэтому с заглавной буквы оно уже кое-что значит.
    "Q3": {
        "qid": "Q3",
        "label": "Гараж",
        "year": 1979,
        "aliases": [],
        "types": ["фильм"],
        "directors": ["Эльдар Рязанов"],
        "based_on": [],
        "wiki_url": None,
        "homonyms": [{"qid": "Q901", "type": "здание"}],
        "homonyms_by_form": {"Гараж": [{"qid": "Q901", "type": "здание"}]},
    },
    # Название из по-настоящему частотного слова: «время» есть в 13% вопросов
    "Q6": {
        "qid": "Q6",
        "label": "Время",
        "year": 1983,
        "aliases": [],
        "types": ["фильм"],
        "directors": ["Эльдар Рязанов"],
        "based_on": [],
        "wiki_url": None,
        "homonyms": [{"qid": "Q902", "type": "телепередача"}],
        "homonyms_by_form": {"Время": [{"qid": "Q902", "type": "телепередача"}]},
    },
    # Название из служебного слова
    "Q4": {
        "qid": "Q4",
        "label": "Она",
        "year": 1970,
        "aliases": [],
        "types": ["фильм"],
        "directors": [],
        "based_on": [],
        "wiki_url": None,
        "homonyms": [],
    },
    # Советский фильм, одноимённый с чужим известным кино
    "Q7": {
        "qid": "Q7",
        "label": "Джокер",
        "year": 1991,
        "aliases": [],
        "types": ["фильм"],
        "directors": [],
        "based_on": [],
        "wiki_url": None,
        "homonyms": [
            {"qid": "Q903", "type": "фильм"},
            {"qid": "Q904", "type": "персонаж комиксов"},
        ],
        "homonyms_by_form": {
            "Джокер": [
                {"qid": "Q903", "type": "фильм"},
                {"qid": "Q904", "type": "персонаж комиксов"},
            ]
        },
    },
    # Фильм без омонимов у основного названия, но с омонимичным
    # альтернативным названием: «Первые огни» / «Маяк»
    "Q9": {
        "qid": "Q9",
        "label": "Первые огни",
        "year": 1925,
        "aliases": ["Маяк"],
        "types": ["фильм"],
        "directors": [],
        "based_on": [],
        "wiki_url": None,
        "homonyms": [],
        "homonyms_by_form": {
            "Маяк": [
                {"qid": "Q905", "type": "радиостанция"},
                {"qid": "Q906", "type": "производственное объединение"},
            ]
        },
    },
    # Название-поговорка: три слова, но одно из них — предлог
    "Q8": {
        "qid": "Q8",
        "label": "Смотри в корень",
        "year": 1930,
        "aliases": [],
        "types": ["фильм"],
        "directors": [],
        "based_on": [],
        "wiki_url": None,
        "homonyms": [],
    },
    # Длинное небанальное название
    "Q5": {
        "qid": "Q5",
        "label": "Место встречи изменить нельзя",
        "year": 1979,
        "aliases": [],
        "types": ["телефильм"],
        "directors": ["Станислав Говорухин"],
        "based_on": [],
        "wiki_url": None,
        "homonyms": [],
    },
}

CHARACTERS = {
    # Редкая фамилия, не обычное слово
    "Q10": {
        "key": "Q10",
        "qid": "Q10",
        "label": "Штирлиц",
        "aliases": [],
        "films": ["Q5"],
        "actors": ["Вячеслав Тихонов"],
        "sources": ["P674"],
    },
    # Имя, которое является обычным словом
    "Q11": {
        "key": "Q11",
        "qid": "Q11",
        "label": "Трус",
        "aliases": [],
        "films": ["Q3"],
        "actors": ["Георгий Вицин"],
        "sources": ["P674"],
    },
    # Полное имя из двух слов
    "Q12": {
        "key": "Q12",
        "qid": "Q12",
        "label": "Ипполит Матвеевич Воробьянинов",
        "aliases": [],
        "films": ["Q2"],
        "actors": [],
        "sources": ["P674"],
    },
    # Строковая роль-должность: должна отбраковаться при сборке словаря
    "S:врач|Q3": {
        "key": "S:врач|Q3",
        "qid": None,
        "label": "врач",
        "aliases": [],
        "films": ["Q3"],
        "actors": [],
        "sources": ["P4633"],
    },
    # Должность с заглавной буквы — тоже не персонаж
    "S:милиционер|Q3": {
        "key": "S:милиционер|Q3",
        "qid": None,
        "label": "Милиционер",
        "aliases": [],
        "films": ["Q3"],
        "actors": [],
        "sources": ["P4633"],
    },
    # Строковая роль с именем: должна остаться
    "S:семён семёнович горбунков|Q3": {
        "key": "S:семён семёнович горбунков|Q3",
        "qid": None,
        "label": "Семён Семёнович Горбунков",
        "aliases": [],
        "films": ["Q3"],
        "actors": ["Юрий Никулин"],
        "sources": ["P4633"],
    },
    # Реальный человек, которого играл актёр: Wikidata числит его «персонажем»,
    # но вопрос про настоящего Эйнштейна — не про советское кино
    "Q13": {
        "key": "Q13",
        "qid": "Q13",
        "label": "Альберт Эйнштейн",
        "aliases": [],
        "films": ["Q5"],
        "actors": [],
        "sources": ["P453"],
        "types": ["человек"],
        "type_qids": ["Q5"],
    },
    # Персонаж, у которого имя — просто личное имя
    "Q14": {
        "key": "Q14",
        "qid": "Q14",
        "label": "Андрей",
        "aliases": [],
        "films": ["Q5"],
        "actors": [],
        "sources": ["P674"],
        "types": ["киноперсонаж"],
        "type_qids": ["Q15773317"],
    },
}


PEOPLE = {
    # Уникальная редкая фамилия + много фильмов: узнаётся без имени
    "P1": {
        "qid": "P1",
        "label": "Юрий Никулин",
        "aliases": [],
        "roles": ["actor"],
        "films": ["Q3", "Q5", "Q6", "Q7"],
        "wiki_url": None,
        "homonyms": [],
        "occupations": ["клоун", "певец", "актёр"],
    },
    # Режиссёр
    "P2": {
        "qid": "P2",
        "label": "Эльдар Рязанов",
        "aliases": [],
        "roles": ["director"],
        "films": ["Q1", "Q3", "Q6"],
        "wiki_url": None,
        "homonyms": [],
        "occupations": ["кинорежиссёр", "сценарист"],
    },
    # Тёзка: «Андрей Миронов» — не только актёр
    "P3": {
        "qid": "P3",
        "label": "Андрей Миронов",
        "aliases": [],
        "roles": ["actor"],
        "films": ["Q3"],
        "wiki_url": None,
        "homonyms": [{"qid": "P900", "type": "человек"}],
        "occupations": ["актёр"],
    },
    # Второй Миронов в словаре: фамилия перестаёт быть опознавательной
    "P4": {
        "qid": "P4",
        "label": "Евгений Миронов",
        "aliases": [],
        "roles": ["actor"],
        "films": ["Q5"],
        "wiki_url": None,
        "homonyms": [],
        "occupations": ["актёр"],
    },
    # Инвертированный формат имени в Wikidata
    "P5": {
        "qid": "P5",
        "label": "Гайдай, Леонид Иович",
        "aliases": [],
        "roles": ["director"],
        "films": ["Q3", "Q5", "Q6"],
        "wiki_url": None,
        "homonyms": [],
        "occupations": ["кинорежиссёр"],
    },
    # Реальный человек, чья хроника попала в документальный фильм.
    # Wikidata числит его «в ролях», но он не киношник
    "P6": {
        "qid": "P6",
        "label": "Иосиф Сталин",
        "aliases": [],
        "roles": ["actor"],
        "films": ["Q5"],
        "wiki_url": None,
        "homonyms": [],
        "occupations": ["политик", "революционер"],
    },
    # Режиссёр одного фильма с фамилией мировой знаменитости
    "P7": {
        "qid": "P7",
        "label": "Чаплин, Станислав Викторович",
        "aliases": [],
        "roles": ["director"],
        "films": ["Q5"],
        "wiki_url": None,
        "homonyms": [],
        "occupations": ["кинорежиссёр", "актёр"],
    },
}


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    path = tmp_path_factory.mktemp("cinema_dict")
    (path / "films.json").write_text(json.dumps(FILMS, ensure_ascii=False), encoding="utf-8")
    (path / "characters.json").write_text(
        json.dumps(CHARACTERS, ensure_ascii=False), encoding="utf-8"
    )
    (path / "people.json").write_text(json.dumps(PEOPLE, ensure_ascii=False), encoding="utf-8")
    (path / "dict_meta.json").write_text(
        json.dumps({"built_at": "2026-07-16T00:00:00+00:00"}), encoding="utf-8"
    )
    return path


# Частотность лемм — как в реальной базе: доли взяты из фактического прогона
# по 212 779 вопросам. Именно она решает, банально ли слово.
FREQUENCY = LemmaFrequency(
    n_docs=100_000,
    rare_ratio=0.001,  # порог: 100 вопросов
    df={
        # обычные слова
        "он": 40_000, "она": 40_000, "они": 24_000, "оно": 2_500,
        "время": 13_000,
        "место": 9_000, "встреча": 3_000, "изменить": 2_000, "нельзя": 2_000,
        "ирония": 270, "судьба": 650, "легкий": 900, "пар": 800,
        "собачий": 300, "сердце": 3_000, "андрей": 800, "трус": 400,
        # редкие слова
        "гараж": 42, "штирлиц": 37, "ипполит": 20, "матвеевич": 5,
        "воробьянинов": 8, "семен": 60, "семенович": 12, "горбунков": 3,
        "джокер": 45, "смотреть": 9_000, "корень": 700, "маяк": 80, "первый": 20_000, "огонь": 1_500,
        # люди кино
        "никулин": 50, "гайдай": 25, "миронов": 60, "евгений": 700, "леонид": 300,
        "иович": 2, "цирк": 400, "работать": 5_000, "год": 30_000,
        "чаплин": 76, "станислав": 45, "викторович": 8, "сталин": 900, "иосиф": 200,
        "говорухин": 30, "рязанов": 55, "тихонов": 40, "вицин": 15,
        "бортко": 10, "эльдар": 25, "владимир": 90, "станислав": 45,
        "вячеслав": 30, "георгий": 70, "юрий": 95, "никулин": 50,
    },
)


@pytest.fixture(scope="module")
def dictionary(snapshot):
    return load_dictionary(snapshot, frequency=FREQUENCY)


@pytest.fixture(scope="module")
def matcher(dictionary):
    return CinemaMatcher(dictionary)


def _rules(result, entity_key=None):
    return {
        (m.entity_key, m.rule, m.confidence)
        for m in result.matches
        if entity_key is None or m.entity_key == entity_key
    }


def _reasons(result, entity_key=None):
    return {
        (r.entity_key, r.reason)
        for r in result.rejected
        if entity_key is None or r.entity_key == entity_key
    }


# --- Словарь ---


def test_generic_string_role_is_rejected(dictionary):
    """«врач» — должность, а не персонаж: в словарь попадать не должна."""
    assert "S:врач|Q3" not in dictionary.entities
    reasons = {r.key: r.reason for r in dictionary.rejections}
    assert reasons["S:врач|Q3"] == "generic_role_lowercase"


def test_capitalized_generic_role_is_rejected(dictionary):
    """«Милиционер» с заглавной — всё ещё должность, а не имя."""
    assert "S:милиционер|Q3" not in dictionary.entities
    reasons = {r.key: r.reason for r in dictionary.rejections}
    assert reasons["S:милиционер|Q3"] == "generic_role_no_name_token"


def test_named_string_role_is_kept(dictionary):
    assert "S:семён семёнович горбунков|Q3" in dictionary.entities


def test_real_person_is_not_a_character(dictionary):
    """Эйнштейна играли в кино, но он не персонаж советского фильма."""
    assert "Q13" not in dictionary.entities
    reasons = {r.key: r.reason for r in dictionary.rejections}
    assert reasons["Q13"] == "real_person"


def test_generic_given_name_is_not_counted_bare(matcher):
    """Персонаж «Андрей» не должен ловиться в каждом вопросе со словом «Андрей»."""
    result = matcher.match_field("Андрей Тарковский снял этот фильм в 1966 году.", "text")
    assert not [m for m in result.matches if m.entity_key == "Q14"]
    assert ("Q14", "generic_given_name_no_context") in _reasons(result)


def test_character_inherits_literary_source_from_film(dictionary):
    """Персонаж экранизации должен нести пометку книжного первоисточника."""
    entity = dictionary.get("Q12")
    assert entity.based_on == ["Собачье сердце"]


def test_alias_is_indexed(dictionary):
    """«Ирония судьбы» — алиас полного названия."""
    found = {e.key for e in dictionary.lookup("ирония судьба")}
    assert "Q1" in found


def test_prepositions_do_not_count_as_title_length(dictionary):
    """«Смотри в корень» — два знаменательных слова, а не три.

    Иначе поговорка выглядит длинным небанальным названием и проходит
    по правилу «на трёх словах случайно не совпадёшь».
    """
    entity = dictionary.get("Q8")
    alias = entity.aliases[0]
    assert alias.tokens == ("смотреть", "в", "корень")
    assert alias.token_count == 2
    assert alias.content_tokens == ("смотреть", "корень")


def test_long_title_with_prepositions_still_searchable(dictionary):
    """При этом окно поиска обязано покрывать всю фразу вместе с предлогами."""
    assert dictionary.max_tokens >= 3


# --- Нормализация и падежи ---


def test_matches_inflected_form(matcher):
    """«в Иронии судьбы» — родительный падеж, лемматизация обязана справиться."""
    result = matcher.match_field("Речь идёт о фильме «Ирония судьбы».", "text")
    assert ("Q1", "quoted_title_with_context", "high") in _rules(result)


def test_yo_is_normalized(matcher):
    result = matcher.match_field("В фильме «Место встречи изменить нельзя»…", "text")
    assert any(m.entity_key == "Q5" for m in result.matches)


# --- Омонимы: главное обещание пайплайна ---


def test_lowercase_title_is_rejected(matcher):
    """«гараж» с маленькой буквы — это гараж, а не фильм Рязанова."""
    result = matcher.match_field("Он поставил машину в гараж и ушёл.", "text")
    assert not [m for m in result.matches if m.entity_key == "Q3"]
    assert ("Q3", "not_capitalized") in _reasons(result)


def test_common_word_title_is_not_saved_by_distant_cinema_marker(matcher):
    """Слово «фильм» где-то в вопросе не превращает частотное слово в название.

    Регрессия: на широком окне фильм «Одна» (1931) собирал сотни ложных
    совпадений со словом «одна» в вопросах про любое кино.
    """
    text = (
        "В этом фильме, снятом в СССР, показана жизнь большого дома, "
        "где у каждого жильца есть свои тайны, и Время расставило всё "
        "по своим местам."
    )
    result = matcher.match_field(text, "text")
    assert not [m for m in result.matches if m.entity_key == "Q6"]
    # Отбраковано либо как «обычная фраза без контекста» (маркер дальше ближнего
    # окна), либо как «обычная фраза со слабым основанием» — важно, что не засчитано.
    reasons = {reason for key, reason in _reasons(result, "Q6")}
    assert reasons <= {"common_phrase_bare_no_context", "common_phrase_bare_weak_evidence"}
    assert reasons


def test_cinema_marker_right_next_to_common_title_is_still_not_counted(matcher):
    """Даже вплотную стоящий киномаркер не спасает частотное слово без кавычек."""
    result = matcher.match_field("В фильме Время сыграло важную роль.", "text")
    assert not [m for m in result.matches if m.entity_key == "Q6"]
    assert ("Q6", "common_phrase_bare_weak_evidence") in _reasons(result)


def test_common_word_quoted_without_context_is_rejected(matcher):
    """«Время» в кавычках без кино — скорее телепрограмма, чем фильм 1983 года."""
    result = matcher.match_field("Передача «Время» выходила по вечерам.", "text")
    assert not [m for m in result.matches if m.entity_key == "Q6"]
    assert ("Q6", "common_word_quoted_no_context") in _reasons(result)


def test_capitalized_rare_title_with_cinema_context_counts(matcher):
    """«Гараж» с заглавной и словом «фильм» рядом — уже упоминание.

    Слово редкое (0.042% вопросов базы), так что заглавная буква тут значима.
    Но у названия есть омоним («здание»), а кавычек нет — поэтому medium.
    """
    result = matcher.match_field("В фильме Гараж действие идёт за одну ночь.", "text")
    matches = [m for m in result.matches if m.entity_key == "Q3"]
    assert matches[0].rule == "title_with_cinema_context_ambiguous"
    assert matches[0].confidence == "medium"


def test_unambiguous_bare_title_with_cinema_context_is_high(matcher):
    """У названия без омонимов голое упоминание рядом с «фильм» — это high."""
    result = matcher.match_field(
        "В фильме Место встречи изменить нельзя это показано.", "text"
    )
    assert ("Q5", "title_with_cinema_context", "high") in _rules(result)


def test_chgk_substitution_in_caps_is_rejected(matcher):
    """Слово капсом — это ЧГК-замена, а не название фильма."""
    result = matcher.match_field(
        "В этом фильме ГАРАЖ всё время мешает героям. Что мы заменили?", "text"
    )
    assert not [m for m in result.matches if m.entity_key == "Q3"]
    assert ("Q3", "chgk_substitution_caps") in _reasons(result)


def test_short_homonym_with_cinema_context_is_counted(matcher):
    result = matcher.match_field("В фильме «Гараж» Рязанова есть эта сцена.", "text")
    assert ("Q3", "quoted_title_with_context", "high") in _rules(result)


def test_trivial_title_is_rejected_even_in_quotes(matcher):
    """Фильм «Она»: местоимение не ищется никогда.

    В ЧГК «ОНА» в кавычках — почти всегда замена, а «Оно» — Стивен Кинг.
    """
    bare = matcher.match_field("Она пришла домой поздно вечером.", "text")
    assert not [m for m in bare.matches if m.entity_key == "Q4"]
    assert ("Q4", "trivial_title") in _reasons(bare)

    quoted = matcher.match_field("В фильме «Она» есть эта сцена.", "text")
    assert not [m for m in quoted.matches if m.entity_key == "Q4"]
    assert ("Q4", "trivial_title") in _reasons(quoted)


def test_literary_context_downgrades_film(matcher):
    """«Собачье сердце» рядом со словом «повесть» — это книга, не фильм."""
    result = matcher.match_field(
        "В повести «Собачье сердце» автор описывает эту сцену.", "text"
    )
    matches = [m for m in result.matches if m.entity_key == "Q2"]
    assert matches, "совпадение должно сохраниться, но с низкой уверенностью"
    assert matches[0].confidence == "low"
    assert matches[0].rule == "quoted_title_literary_context"
    assert "literary_marker_nearby" in matches[0].flags


def test_cinema_context_beats_literary_marker(matcher):
    """«Экранизация повести «Собачье сердце»» — всё-таки про фильм."""
    result = matcher.match_field(
        "Экранизация повести «Собачье сердце» вышла на телеэкраны.", "text"
    )
    matches = [m for m in result.matches if m.entity_key == "Q2"]
    assert matches[0].confidence == "high"


def test_director_surname_supports_common_word_title_but_not_to_high(matcher):
    """Фамилия режиссёра рядом — основание, но «Время» остаётся частотным словом.

    Поэтому такое совпадение получает medium, а не high: в рейтинг оно не пойдёт,
    но и потеряно не будет.
    """
    result = matcher.match_field("У Рязанова Время стало главным героем.", "text")
    rules = _rules(result, "Q6")
    assert ("Q6", "title_with_creator_context_weak", "medium") in rules


def test_director_surname_gives_high_for_distinctive_title(matcher):
    """А вот у небанального названия фамилия режиссёра рядом — уже high."""
    result = matcher.match_field(
        "У Говорухина место встречи изменить нельзя было принципиально.", "text"
    )
    rules = _rules(result, "Q5")
    assert ("Q5", "title_with_creator_context", "high") in rules


def test_homonyms_are_tracked_per_alias_not_per_film(dictionary):
    """Омонимы принадлежат форме названия, а не фильму.

    Регрессия: у «Первых огней» (1925) альтернативное название «Маяк».
    Омонимы искались только для основного названия, поэтому «Маяк» оставался
    «однозначным» и ловил радиостанцию, химкомбинат и ресторан.
    """
    entity = dictionary.get("Q9")
    primary = next(a for a in entity.aliases if a.surface == "Первые огни")
    secondary = next(a for a in entity.aliases if a.surface == "Маяк")
    assert primary.homonyms == []
    assert len(secondary.homonyms) == 2


def test_ambiguous_alias_does_not_get_high_without_context(matcher):
    """Кавычки вокруг «Маяка» без кино — это радиостанция, а не фильм 1925 года."""
    result = matcher.match_field(
        'В сентябре 1957 года после аварии на химкомбинате "Маяк" появились сообщения.',
        "text",
    )
    matches = [m for m in result.matches if m.entity_key == "Q9"]
    assert matches
    assert matches[0].confidence == "medium"
    assert matches[0].rule == "quoted_title_ambiguous"
    assert "has_homonyms" in matches[0].flags


def test_film_homonym_blocks_high_confidence(matcher):
    """«В фильме „Джокер“» — но одноимённых фильмов много, и это не наш.

    Слово «фильм» рядом не доказывает, что речь о советском «Джокере» 1991 года:
    оно с тем же успехом относится к чужому фильму с тем же названием.
    """
    result = matcher.match_field(
        "Заглавного героя фильма «Джокер» зовут Артур Флек.", "text"
    )
    matches = [m for m in result.matches if m.entity_key == "Q7"]
    assert matches[0].confidence == "medium"
    assert matches[0].rule == "quoted_title_with_context_film_homonym"
    assert "homonym_is_also_a_film" in matches[0].flags


def test_film_homonym_bare_with_context_is_not_high(matcher):
    result = matcher.match_field("В фильме «Тёмный рыцарь» Джокер сообщает адрес.", "text")
    matches = [m for m in result.matches if m.entity_key == "Q7"]
    assert matches
    assert all(m.confidence != "high" for m in matches)


def test_unambiguous_quoted_title_needs_no_context(matcher):
    result = matcher.match_field("Всё как в «Место встречи изменить нельзя».", "text")
    assert ("Q5", "quoted_title", "high") in _rules(result)


def test_bare_multiword_title_is_medium(matcher):
    """Длинное название без кавычек и контекста — medium, не high."""
    result = matcher.match_field(
        "Место встречи изменить нельзя, поэтому все приехали вовремя.", "text"
    )
    matches = [m for m in result.matches if m.entity_key == "Q5"]
    assert matches[0].confidence == "medium"
    assert matches[0].rule == "bare_title_multiword"


# --- Персонажи ---


def test_rare_surname_counts_without_context(matcher):
    """«Штирлиц» — уникальная фамилия, обычным словом не является."""
    result = matcher.match_field("Штирлиц шёл по коридору.", "text")
    rules = _rules(result, "Q10")
    assert ("Q10", "character_rare_name", "medium") in rules


def test_rare_surname_inflected(matcher):
    result = matcher.match_field("В фильме роль Штирлица сыграл Тихонов.", "text")
    assert any(
        m.entity_key == "Q10" and m.confidence == "high" for m in result.matches
    )


def test_lowercase_character_name_is_rejected(matcher):
    """«трус» с маленькой буквы — характеристика человека, а не герой Вицина."""
    result = matcher.match_field("Он трус и не пошёл на дуэль.", "text")
    assert not [m for m in result.matches if m.entity_key == "Q11"]
    assert ("Q11", "not_capitalized") in _reasons(result)


def test_capitalized_common_word_name_still_needs_cinema_context(matcher):
    """«Трус» с заглавной, но без кино рядом, — всё ещё не персонаж.

    Заглавная буква бывает и в начале предложения, поэтому одной её мало.
    """
    result = matcher.match_field("Трус не пошёл на дуэль и был осмеян.", "text")
    assert not [m for m in result.matches if m.entity_key == "Q11"]
    assert ("Q11", "common_word_name_no_context") in _reasons(result)


def test_common_word_name_with_cinema_context_is_medium(matcher):
    result = matcher.match_field("В этом фильме Трус, Балбес и Бывалый крадут…", "text")
    matches = [m for m in result.matches if m.entity_key == "Q11"]
    assert matches[0].confidence == "medium"
    assert matches[0].rule == "character_common_name_with_context"


def test_full_name_is_matched(matcher):
    result = matcher.match_field("Ипполит Матвеевич Воробьянинов сидел молча.", "text")
    assert any(m.entity_key == "Q12" for m in result.matches)


# --- Актёры и режиссёры ---


def test_unique_surname_becomes_searchable(dictionary):
    """В ЧГК пишут «Никулин», а не «Юрий Никулин» — фамилия нужна как форма."""
    entity = dictionary.get("P1")
    assert entity.unique_surname == "никулин"
    assert "никулин" in {a.key for a in entity.aliases}


def test_non_cinema_person_is_rejected(dictionary):
    """Сталин «снимался» лишь в том смысле, что его хроника попала в документалку.

    По роду занятий он политик, и вопрос про него — не про советское кино.
    """
    assert "P6" not in dictionary.entities
    reasons = {r.key: r.reason for r in dictionary.rejections}
    assert reasons["P6"] == "not_a_cinema_person"


def test_obscure_person_does_not_claim_famous_surname(dictionary):
    """Регрессия: «Чаплин, Станислав Викторович» перехватывал Чарли Чаплина.

    Режиссёр одного фильма не может опознаваться по одной фамилии — иначе он
    забирает себе все вопросы про однофамильца мировой известности.
    """
    entity = dictionary.get("P7")
    assert entity is not None, "сам человек остаётся в словаре"
    assert entity.unique_surname is None
    assert "чаплин" not in dictionary.by_alias


def test_famous_surname_still_matches_by_full_name(matcher):
    """При этом по полному имени малоизвестный человек по-прежнему находится."""
    result = matcher.match_field("Фильм снял Станислав Викторович Чаплин.", "text")
    assert any(m.entity_key == "P7" for m in result.matches)


def test_shared_surname_is_not_searchable(dictionary):
    """Мироновых в словаре двое — по фамилии человека не опознать."""
    assert dictionary.get("P3").unique_surname is None
    assert dictionary.get("P4").unique_surname is None
    assert dictionary.surname_conflicts.get("миронов") == 2


def test_inverted_wikidata_name_format(dictionary):
    """«Гайдай, Леонид Иович» — фамилия до запятой, а не в конце."""
    assert dictionary.get("P5").unique_surname == "гайдай"


def test_inverted_name_is_flipped_to_natural_order(dictionary):
    """В вопросах пишут «Леонид Гайдай», а не «Гайдай, Леонид Иович».

    Без разворота такие люди находились бы только по голой фамилии.
    """
    entity = dictionary.get("P5")
    assert entity.label == "Леонид Иович Гайдай"
    assert "леонид иович гайдай" in {a.key for a in entity.aliases}


def test_inverted_name_matches_in_text(matcher):
    result = matcher.match_field("Эту комедию снял Леонид Иович Гайдай.", "text")
    assert ("P5", "person_full_name", "high") in _rules(result)


def test_person_full_name_is_high(matcher):
    result = matcher.match_field("Об этом вспоминал Юрий Никулин.", "text")
    assert ("P1", "person_full_name", "high") in _rules(result)


def test_namesake_without_cinema_context_is_medium(matcher):
    """«Андрей Миронов» — не только актёр, поэтому без кино рядом это medium."""
    result = matcher.match_field("Андрей Миронов выступил с заявлением.", "text")
    matches = [m for m in result.matches if m.entity_key == "P3"]
    assert matches[0].rule == "person_full_name_namesake"
    assert matches[0].confidence == "medium"
    assert "has_namesakes" in matches[0].flags


def test_person_surname_counts_and_marks_cinema_context(matcher):
    """Ключевая развилка: упоминание человека и упоминание кино — разное.

    Никулин-клоун считается, но помечается как «вне киноконтекста».
    """
    circus = matcher.match_field("Никулин много лет работал в цирке.", "text")
    circus_match = next(m for m in circus.matches if m.entity_key == "P1")
    assert circus_match.rule == "person_surname"
    assert circus_match.cinema_context is False

    cinema = matcher.match_field("В этом фильме Никулин сыграл главную роль.", "text")
    cinema_match = next(m for m in cinema.matches if m.entity_key == "P1")
    assert cinema_match.rule == "person_surname_with_context"
    assert cinema_match.confidence == "high"
    assert cinema_match.cinema_context is True


def test_lowercase_person_is_rejected(matcher):
    result = matcher.match_field("этот никулин мне незнаком", "text")
    assert not [m for m in result.matches if m.entity_key == "P1"]
    assert ("P1", "not_capitalized") in _reasons(result)


def test_director_is_matched(matcher):
    result = matcher.match_field("Фильм снял Эльдар Рязанов.", "text")
    assert ("P2", "person_full_name", "high") in _rules(result)


# --- Поля ---


def test_answer_counts_only_when_question_is_about_cinema(matcher):
    """Ответ, равный названию, засчитывается по контексту вопроса.

    Ответ сам по себе — это просто ответ. Понять, о фильме ли речь, можно
    только по тексту вопроса.
    """
    result = matcher.match_question(
        {
            "text": "В каком фильме Говорухина прозвучала эта фраза?",
            "answer": "Место встречи изменить нельзя",
            "comment": None,
        }
    )
    answers = [m for m in result.matches if m.field == "answer" and m.entity_key == "Q5"]
    assert answers[0].rule == "answer_exact_title"
    assert answers[0].confidence == "high"


def test_answer_without_cinema_context_is_rejected(matcher):
    """Регрессия: фильм «Юрий Гагарин» (1969) собирал ответы про космонавта.

    Если в вопросе нет ни слова про кино, ответ — это ответ, а не упоминание
    одноимённого фильма.
    """
    result = matcher.match_question(
        {
            "text": "Кто первым совершил орбитальный полёт вокруг Земли?",
            "answer": "Место встречи изменить нельзя",
            "comment": None,
        }
    )
    assert not [m for m in result.matches if m.field == "answer"]
    reasons = {(r.entity_key, r.reason) for r in result.rejected if r.field == "answer"}
    assert ("Q5", "answer_without_cinema_context") in reasons


def test_answer_equal_to_common_phrase_title_is_only_medium(matcher):
    """Ответ из частотных слов — medium, даже если он дословно равен названию.

    Размен осознанный: иначе ответ «Крестовый поход» (историческая тема)
    засчитывался бы как упоминание одноимённого фильма 1930 года. Плата —
    «Ирония судьбы» в ответе тоже не попадает в high, но фильм всё равно
    набирает high-упоминания в тексте и комментариях.
    """
    result = matcher.match_question(
        {
            "text": "Назовите фильм Рязанова, который показывают каждый Новый год.",
            "answer": "Ирония судьбы",
            "comment": None,
        }
    )
    answers = [m for m in result.matches if m.field == "answer" and m.entity_key == "Q1"]
    assert answers[0].rule == "answer_exact_title_weak"
    assert answers[0].confidence == "medium"


def test_ambiguous_answer_is_medium(matcher):
    """Ответ «Собачье сердце» может означать и повесть — не high."""
    result = matcher.match_question(
        {
            "text": "Назовите фильм Бортко, снятый в 1988 году.",
            "answer": "Собачье сердце",
            "comment": None,
        }
    )
    matches = [m for m in result.matches if m.field == "answer" and m.entity_key == "Q2"]
    assert matches[0].confidence == "medium"
    assert matches[0].rule == "answer_exact_title_ambiguous"


def test_fields_are_counted_separately(matcher):
    result = matcher.match_question(
        {
            "text": "В каком фильме «Гараж» это происходит?",
            "answer": "Ирония судьбы",
            "comment": "Режиссёр — Эльдар Рязанов.",
        }
    )
    fields = {m.field for m in result.matches}
    assert "text" in fields and "answer" in fields


def test_empty_fields_are_safe(matcher):
    result = matcher.match_question({"text": None, "answer": "", "comment": None})
    assert not result.matches


def test_offsets_point_at_matched_text(matcher):
    """Смещения обязаны указывать в исходный текст: это доказательство."""
    text = "Речь о фильме «Гараж» Рязанова."
    result = matcher.match_field(text, "text")
    match = next(m for m in result.matches if m.entity_key == "Q3")
    assert text[match.start : match.end].lower() == match.matched_text.lower()
    assert match.matched_text.lower() == "гараж"


# --- Хранение ---


@pytest.fixture
def db(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    upsert_pack(conn, {"id": 1, "title": "Тестовый пакет"})
    insert_questions(
        conn,
        [
            {
                "id": 101,
                "pack_id": 1,
                "number": 1,
                "text": "В фильме «Гараж»…",
                "answer": "Гараж",
            },
            {
                "id": 102,
                "pack_id": 1,
                "number": 2,
                "text": "Штирлиц шёл по коридору.",
                "answer": "Штирлиц",
            },
        ],
    )
    conn.commit()
    yield conn
    conn.close()


def test_store_roundtrip_and_idempotency(db, dictionary, matcher):
    """Второй полный прогон заменяет первый, а не удваивает данные."""
    sync_entities(db, dictionary, dict_version="v1")

    def do_run():
        run_id = start_run(db, dict_version="v1", dict_hash_value="h", params={})
        total = 0
        for row in db.execute("SELECT id, text, answer, comment FROM questions"):
            result = matcher.match_question(
                {"text": row["text"], "answer": row["answer"], "comment": row["comment"]}
            )
            save_matches(db, run_id, row["id"], result.matches, result.rejected)
            total += len(result.matches)
        finish_run(
            db, run_id, questions_scanned=2, mentions_found=total, rejected_count=0
        )
        drop_other_runs(db, run_id)
        db.commit()
        return run_id, total

    run1, total1 = do_run()
    count1 = db.execute("SELECT COUNT(*) FROM cinema_mentions").fetchone()[0]

    run2, total2 = do_run()
    count2 = db.execute("SELECT COUNT(*) FROM cinema_mentions").fetchone()[0]

    assert total1 == total2
    assert count1 == count2
    assert db.execute("SELECT COUNT(DISTINCT run_id) FROM cinema_mentions").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM cinema_runs").fetchone()[0] == 1
    assert run2 != run1


def test_pipeline_does_not_touch_source_tables(db, dictionary, matcher):
    """Главное ограничение: исходные вопросы не меняются."""
    before = integrity_snapshot(db)
    sync_entities(db, dictionary, dict_version="v1")
    run_id = start_run(db, dict_version="v1", dict_hash_value="h", params={})
    for row in db.execute("SELECT id, text, answer, comment FROM questions"):
        result = matcher.match_question(
            {"text": row["text"], "answer": row["answer"], "comment": row["comment"]}
        )
        save_matches(db, run_id, row["id"], result.matches, result.rejected)
    finish_run(db, run_id, questions_scanned=2, mentions_found=0, rejected_count=0)
    db.commit()
    assert integrity_snapshot(db) == before


def test_sync_removes_entities_dropped_from_dictionary(db, dictionary):
    """Таблица сущностей — зеркало словаря, а не свалка всех прошлых версий.

    Иначе отсеянные новыми фильтрами (Сталин, Чаплин) остаются в базе навсегда.
    """
    db.execute(
        """
        INSERT INTO cinema_entities (key, kind, label, dict_version)
        VALUES ('OBSOLETE', 'person', 'Кто-то из прошлого словаря', 'v0')
        """
    )
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) FROM cinema_entities WHERE key = 'OBSOLETE'"
    ).fetchone()[0] == 1

    sync_entities(db, dictionary, dict_version="v1")
    db.commit()

    assert db.execute(
        "SELECT COUNT(*) FROM cinema_entities WHERE key = 'OBSOLETE'"
    ).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM cinema_entities").fetchone()[0] == len(
        dictionary.entities
    )


def test_evidence_is_stored(db, dictionary, matcher):
    sync_entities(db, dictionary, dict_version="v1")
    run_id = start_run(db, dict_version="v1", dict_hash_value="h", params={})
    row = db.execute("SELECT id, text, answer, comment FROM questions WHERE id = 101").fetchone()
    result = matcher.match_question(
        {"text": row["text"], "answer": row["answer"], "comment": row["comment"]}
    )
    save_matches(db, run_id, 101, result.matches, result.rejected)
    db.commit()

    stored = db.execute(
        "SELECT * FROM cinema_mentions WHERE question_id = 101 AND entity_key = 'Q3'"
    ).fetchall()
    assert stored, "упоминание должно сохраниться с доказательством"
    first = stored[0]
    assert first["matched_text"]
    assert first["context"]
    assert first["rule"]
    assert first["field"] in {"text", "answer", "comment"}
