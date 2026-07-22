-- Схема БД для анализа вопросов ЧГК
-- chgk_analysis.db

PRAGMA foreign_keys = ON;

-- Пакеты (турниры)
CREATE TABLE IF NOT EXISTS packs (
    id              INTEGER PRIMARY KEY,
    title           TEXT,
    question_count  INTEGER,
    start_date      TEXT,
    end_date        TEXT,
    published_date  TEXT,
    teams_played    INTEGER,
    difficulty      REAL,
    authors         TEXT,
    link            TEXT,
    parse_status    TEXT DEFAULT 'pending',
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_packs_published_date ON packs(published_date);
CREATE INDEX IF NOT EXISTS idx_packs_parse_status ON packs(parse_status);

-- Вопросы
CREATE TABLE IF NOT EXISTS questions (
    id              INTEGER PRIMARY KEY,
    pack_id         INTEGER NOT NULL,
    number          INTEGER,
    tour_number     INTEGER,
    text            TEXT NOT NULL,
    answer          TEXT NOT NULL,
    zachet          TEXT,
    nezachet        TEXT,
    comment         TEXT,
    source          TEXT,
    authors         TEXT,
    razdatka_text   TEXT,
    razdatka_pic    TEXT,
    position_in_pack INTEGER,
    difficulty      REAL,
    FOREIGN KEY (pack_id) REFERENCES packs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_questions_pack_id ON questions(pack_id);

-- Состояние получения результатов пакета. Оно отделено от parse_status,
-- потому что вопросы могут быть успешно разобраны, а результаты ещё не готовы.
CREATE TABLE IF NOT EXISTS pack_result_status (
    pack_id          INTEGER PRIMARY KEY,
    status           TEXT NOT NULL DEFAULT 'pending',
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    last_attempt_at  TEXT,
    next_retry_at    TEXT,
    teams_found      INTEGER,
    mask_count       INTEGER,
    mask_length      INTEGER,
    question_count   INTEGER,
    source_hash      TEXT,
    last_error       TEXT,
    FOREIGN KEY (pack_id) REFERENCES packs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pack_result_status_retry
    ON pack_result_status(status, next_retry_at);

-- Каноническая статистика вопроса по результатам команд.
-- questions.difficulty оставлено как совместимый кэш difficulty_raw.
CREATE TABLE IF NOT EXISTS question_result_stats (
    question_id      INTEGER PRIMARY KEY,
    pack_id          INTEGER NOT NULL,
    position_in_pack INTEGER NOT NULL,
    result_position  INTEGER NOT NULL,
    correct_teams    INTEGER NOT NULL CHECK (correct_teams >= 0),
    total_teams      INTEGER NOT NULL CHECK (total_teams > 0),
    take_rate        REAL NOT NULL CHECK (take_rate >= 0 AND take_rate <= 1),
    difficulty_raw   REAL NOT NULL CHECK (difficulty_raw >= 0 AND difficulty_raw <= 10),
    method           TEXT NOT NULL DEFAULT 'take_rate_v1',
    source_url       TEXT NOT NULL,
    source_hash      TEXT NOT NULL,
    calculated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    FOREIGN KEY (pack_id) REFERENCES packs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_question_result_stats_pack
    ON question_result_stats(pack_id);

-- Категории (уровень 1)
CREATE TABLE IF NOT EXISTS categories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    name_ru         TEXT NOT NULL,
    sort_order      INTEGER DEFAULT 0
);

-- Подкатегории (уровень 2)
CREATE TABLE IF NOT EXISTS subcategories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id     INTEGER NOT NULL,
    name            TEXT NOT NULL,
    name_ru         TEXT NOT NULL,
    sort_order      INTEGER DEFAULT 0,
    FOREIGN KEY (category_id) REFERENCES categories(id),
    UNIQUE(category_id, name)
);

CREATE INDEX IF NOT EXISTS idx_subcategories_category ON subcategories(category_id);

-- Классификация (many-to-many, до 2 тем на вопрос)
CREATE TABLE IF NOT EXISTS question_topics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id     INTEGER NOT NULL,
    subcategory_id  INTEGER NOT NULL,
    confidence      REAL,
    method          TEXT,
    model_name      TEXT,
    classified_at   TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    FOREIGN KEY (subcategory_id) REFERENCES subcategories(id),
    UNIQUE(question_id, subcategory_id, method, model_name)
);

CREATE INDEX IF NOT EXISTS idx_qt_question ON question_topics(question_id);
CREATE INDEX IF NOT EXISTS idx_qt_subcategory ON question_topics(subcategory_id);
CREATE INDEX IF NOT EXISTS idx_qt_model ON question_topics(model_name);

-- Лог запусков классификации
CREATE TABLE IF NOT EXISTS classification_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT DEFAULT (datetime('now')),
    finished_at     TEXT,
    method          TEXT NOT NULL,
    model_name      TEXT,
    questions_processed INTEGER DEFAULT 0,
    questions_failed    INTEGER DEFAULT 0,
    notes           TEXT
);

-- ---------------------------------------------------------------------------
-- Упоминания советского кино (производные данные, пересобираются идемпотентно).
-- Исходные вопросы эти таблицы не изменяют.
-- ---------------------------------------------------------------------------

-- Сущность словаря: фильм или персонаж. Снапшот из Wikidata.
CREATE TABLE IF NOT EXISTS cinema_entities (
    key             TEXT PRIMARY KEY,     -- QID или синтетический ключ строковой роли
    kind            TEXT NOT NULL,        -- film | character
    label           TEXT NOT NULL,
    year            INTEGER,
    types           TEXT,                 -- через |
    directors       TEXT,
    actors          TEXT,
    based_on        TEXT,                 -- литературный первоисточник (P144)
    homonym_types   TEXT,                 -- типы других сущностей с тем же названием
    homonym_count   INTEGER DEFAULT 0,
    films           TEXT,                 -- для персонажей и людей: QID фильмов
    sources         TEXT,                 -- P674 | P453 | P4633
    roles           TEXT,                 -- для людей кино: actor | director
    wiki_url        TEXT,
    dict_version    TEXT
);

CREATE INDEX IF NOT EXISTS idx_cinema_entities_kind ON cinema_entities(kind);

-- Прогон анализа: нужен для воспроизводимости и идемпотентности
CREATE TABLE IF NOT EXISTS cinema_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT DEFAULT (datetime('now')),
    finished_at     TEXT,
    dict_version    TEXT,                 -- built_at снапшота словаря
    dict_hash       TEXT,                 -- хэш файлов словаря
    questions_scanned   INTEGER DEFAULT 0,
    mentions_found      INTEGER DEFAULT 0,
    rejected_count      INTEGER DEFAULT 0,
    params          TEXT,
    notes           TEXT
);

-- Одно упоминание с доказательствами
CREATE TABLE IF NOT EXISTS cinema_mentions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL,
    question_id     INTEGER NOT NULL,
    entity_key      TEXT NOT NULL,
    field           TEXT NOT NULL,        -- text | answer | comment
    rule            TEXT NOT NULL,
    confidence      TEXT NOT NULL,        -- high | medium | low
    matched_text    TEXT NOT NULL,        -- точный фрагмент из исходного поля
    start_offset    INTEGER,
    end_offset      INTEGER,
    context         TEXT,                 -- окно вокруг совпадения
    alias           TEXT,                 -- форма из словаря, давшая совпадение
    flags           TEXT,                 -- has_homonyms | has_literary_source | ...
    cinema_context  INTEGER DEFAULT 0,    -- был ли рядом киномаркер (для людей кино)
    FOREIGN KEY (run_id) REFERENCES cinema_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_key) REFERENCES cinema_entities(key) ON DELETE CASCADE,
    UNIQUE(run_id, question_id, entity_key, field, start_offset)
);

CREATE INDEX IF NOT EXISTS idx_cinema_mentions_entity ON cinema_mentions(entity_key);
CREATE INDEX IF NOT EXISTS idx_cinema_mentions_question ON cinema_mentions(question_id);
CREATE INDEX IF NOT EXISTS idx_cinema_mentions_field ON cinema_mentions(field, confidence);
CREATE INDEX IF NOT EXISTS idx_cinema_mentions_run ON cinema_mentions(run_id);

-- Отбракованные совпадения: почему словарное совпадение не засчитано
CREATE TABLE IF NOT EXISTS cinema_rejections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL,
    question_id     INTEGER NOT NULL,
    entity_key      TEXT NOT NULL,
    field           TEXT NOT NULL,
    reason          TEXT NOT NULL,
    matched_text    TEXT,
    context         TEXT,
    FOREIGN KEY (run_id) REFERENCES cinema_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cinema_rejections_reason ON cinema_rejections(reason);
CREATE INDEX IF NOT EXISTS idx_cinema_rejections_run ON cinema_rejections(run_id);
