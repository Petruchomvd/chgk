-- Схема для хранения постов из Telegram-каналов

CREATE TABLE IF NOT EXISTS tg_channels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    title           TEXT,
    expected_category TEXT,
    last_parsed_id  INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    added_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tg_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id      INTEGER NOT NULL,
    post_id         INTEGER NOT NULL,
    text            TEXT NOT NULL,
    link            TEXT NOT NULL,
    post_date       TEXT,
    views           INTEGER DEFAULT 0,
    category        TEXT,
    confidence      REAL,
    model_name      TEXT,
    classified_at   TEXT,
    is_useful       INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (channel_id) REFERENCES tg_channels(id),
    UNIQUE(channel_id, post_id)
);

CREATE INDEX IF NOT EXISTS idx_tg_posts_channel ON tg_posts(channel_id);
CREATE INDEX IF NOT EXISTS idx_tg_posts_category ON tg_posts(category);
CREATE INDEX IF NOT EXISTS idx_tg_posts_date ON tg_posts(post_date);
