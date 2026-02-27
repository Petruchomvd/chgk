import sqlite3
from typing import Any, Dict, Tuple

DB_PATH = 'chgk1.db'

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS Games (
    id INTEGER PRIMARY KEY,
    name TEXT,
    number_of_questions INTEGER,
    start_date TEXT,
    end_date TEXT,
    published_date TEXT,
    teams_played INTEGER,
    difficulty REAL,
    authors TEXT,
    link TEXT
);

CREATE TABLE IF NOT EXISTS FailedGames (
    id INTEGER PRIMARY KEY,
    error_message TEXT
);
"""


def innit_db() -> Tuple[sqlite3.Connection, sqlite3.Cursor]:
    """Initialise the SQLite database and ensure all tables exist."""
    connection = sqlite3.connect(DB_PATH)
    connection.execute('PRAGMA foreign_keys = ON;')
    cursor = connection.cursor()
    cursor.executescript(_SCHEMA_SQL)
    connection.commit()
    return connection, cursor


def insert_game(connection: sqlite3.Connection, cursor: sqlite3.Cursor, data: Dict[str, Any]) -> bool:
    """Insert or update a game record."""
    try:
        cursor.execute(
            """
            INSERT OR REPLACE INTO Games (
                id,
                name,
                number_of_questions,
                start_date,
                end_date,
                published_date,
                teams_played,
                difficulty,
                authors,
                link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["id"],
                data["name"],
                data["number_of_questions"],
                data["start_date"],
                data["end_date"],
                data["published_date"],
                data["teams_played"],
                data["difficulty"],
                data["authors"],
                data["link"],
            ),
        )
        connection.commit()
        return True
    except Exception as exc:
        print(f'Failed to persist game #{data["id"]} into Games: {exc}')
        return False


def insert_failed_games(connection: sqlite3.Connection, cursor: sqlite3.Cursor, data: Dict[str, Any], error_message: str) -> None:
    """Store information about games that were not parsed."""
    try:
        cursor.execute('SELECT 1 FROM FailedGames WHERE id = ?', (data["id"],))
        if cursor.fetchone():
            print(f'Game with id {data["id"]} already exists in FailedGames')
            return

        cursor.execute(
            """
            INSERT INTO FailedGames (id, error_message)
            VALUES (?, ?)
            """,
            (data["id"], error_message),
        )
        connection.commit()
    except Exception as exc:
        print(f'Failed to insert row into FailedGames: {exc}')
