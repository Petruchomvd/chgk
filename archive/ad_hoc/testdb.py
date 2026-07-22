import sqlite3

# Шаг 1: Подключаемся к базе данных
connection = sqlite3.connect("chgk1.db")
cursor = connection.cursor()

# Шаг 2: Создаём новую таблицу CombinedGames
cursor.execute('''
    CREATE TABLE IF NOT EXISTS CombinedGames (
        id INTEGER PRIMARY KEY,
        name TEXT,
        number_of_questions INTEGER,
        start_date TEXT,
        end_date TEXT,
        published_date TEXT,
        teams_played INTEGER,
        difficulty REAL,
        authors TEXT,
        link TEXT,
        error_message TEXT
    )
''')
connection.commit()

# Шаг 3: Вставляем данные из таблицы Games
cursor.execute('''
    INSERT OR IGNORE INTO CombinedGames (id, name, number_of_questions, start_date, end_date, published_date, teams_played, difficulty, authors, link)
    SELECT id, name, number_of_questions, start_date, end_date, published_date, teams_played, difficulty, authors, link
    FROM Games
''')
connection.commit()

# Шаг 4: Вставляем данные из FailedGames (добавляем только id и error_message)
cursor.execute('''
    INSERT OR IGNORE INTO CombinedGames (id, error_message)
    SELECT id, error_message
    FROM FailedGames
    WHERE id NOT IN (SELECT id FROM CombinedGames)
''')
connection.commit()

# Шаг 5: Проверка результата
cursor.execute("SELECT * FROM CombinedGames")
combined_games = cursor.fetchall()

print(f"Число объединённых записей: {len(combined_games)}")
for game in combined_games:
    print(game)

# Шаг 6: Закрываем соединение
connection.close()