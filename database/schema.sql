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
    FOREIGN KEY (pack_id) REFERENCES packs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_questions_pack_id ON questions(pack_id);

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
