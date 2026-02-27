import re
import time


def count_1_word(filename, search_word=None):
    count = 0

    start_time = time.time()

    with open(filename, 'r', encoding='utf-8') as f:
        for line in f:
            line = re.sub(r'[^\w\s]', '', line).lower()
            words = line.split()
            count += words.count(search_word)
    end_time = time.time()
    elapsed_time = end_time - start_time

    return count, elapsed_time


path = "C:/Users/User/Desktop/ПарсингЧГК/Игры"
word = "вопрос"

for i in range(5):
    word_count, execution_time = count_1_word(path, word)
    print(f'{i+1} тест. Слово {word} встречается {word_count} раз. Время выполнения {execution_time:.4f}')
