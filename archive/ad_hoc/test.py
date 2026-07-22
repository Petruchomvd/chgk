from bs4 import BeautifulSoup
import requests
import re
import lxml
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import sqlite3
from selenium.webdriver.chrome.service import Service


#ВСЯКИЕ СИСТЕМНЫЕ ШТУЧКИ

#Путь к chromedriver для корректной работы selenium
service = Service(r"C:/Users/User/.cache/selenium/chromedriver/win64/131.0.6778.204/chromedriver.exe")

#Переменная driver для запуска браузера Selenium
driver = webdriver.Chrome(service=service)


first_game = 6066
last_game = 6066

# def getting_missing_numbers():
#     with open('saved_games_db.txt', 'r', encoding='utf-8') as f:
#         s = f.readlines()

#     parsed_numbers = [int(number.strip()) for number in s]

#     full_range = set(range(1, 6139))

#     missing_numbers = sorted(full_range - set(parsed_numbers))
#     print(missing_numbers)

#     return missing_numbers

# missing_numbers = getting_missing_numbers()

count_404 = 0
count_errors = 0

for game_number in range(first_game, last_game+1):
    try:
        url = f"https://gotquestions.online/pack/{game_number}"
        print(f'Игра №{game_number}')
        
        response = requests.get(url)
        
        if response.status_code == 404:
            count_404 +=1
            print(f'Ошибка 404 при сохранении игры №{game_number}, количество ошибок {count_404}')
            print()
            with open('Problematic_games', 'a', encoding='utf-8') as f:
                f.write(str(f'Ошибка 404 при сохранении игры №{game_number}, количество ошибок {count_404}'))
                f.write(str('\n'))
            continue
        else:
            pass
        
        
        driver.get(url)
        # Явное ожидание до загрузки названия игры
        wait = WebDriverWait(driver, 10)  # Максимум 20 секунд
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "text-2xl")))
                    
        # Получение HTML контента
        html_content = driver.page_source
        bs_game = BeautifulSoup(html_content, 'html.parser')
                    
        
        
        
        
        # Извлечение названия игры
        game_name_tag = bs_game.find('h1', class_='text-2xl font-comfortaa')
        game_name = game_name_tag.get_text(strip=True) if game_name_tag else "Не найдено"
        print(game_name)
                    
        # Извлечение всех блоков с информацией
        info_blocks = bs_game.find_all('div', class_= 'flex justify-between')
        
        print(info_blocks)
                    
        # Инициализация переменных
        number_of_questions = "Не найдено"
        game_start_date = "Не найдено"
        game_end_date = "Не найдено"
        game_published_date = "Не найдено"
        commands_played = "Не найдено"
        difficulty = "Не найдено"
        author_game = "Не найдено"
        game_link = url  
        
        for block in info_blocks:
            print(block)
            key_tag = block.find('div', class_='font-light')
            if key_tag:
                key = key_tag.get_text(strip=True)
                value_tag = key_tag.find_next_sibling('div')
                value = value_tag.get_text(strip=True)
                print(key, value)
                if key == "Вопросов":
                    number_of_questions = int(value)
                elif key == 'Начало':
                    game_start_date = value
                elif key == "Окончание":
                    game_end_date = value
                elif key == 'Опубликован':
                    game_published_date = value
                elif key == 'Команд сыграло':
                    values = value.split('·')
                    numbers = [int(i) for i in values]
                    commands_played = sum(numbers)
                    
            #Отдельно парсим сложность игры. Иногда у игр бывает несколько указанных сложностей (в разные игровые дни играет разные команды, которые
            # соответственно по разному разыгрывают вопросы. Нам необходимо только одно значение, поэтому в случае наличия нескольких сложностей, получаем 
            #среднее арифметическое из них)
        print()
        print('Дошли до сложности')
            
        dif_key_tag = block.find('span', class_= 'font-light')
        if dif_key_tag and 'Сложность ' in dif_key_tag.get_text():
            # Если нашли нужный row, то переходим ко второму div, который содержит значения
            print("Получаю сложность")
            values_div = block.find_all('div')[1]
            print(values_div)
            print()
            # Ищем все span внутри этого div
            spans = values_div.find_all('span')
            print(spans)
            print()
            raw_dif = spans[0].text.strip()
            print(raw_dif)
            print()
            if len(raw_dif) > 3:
                print("мы здесь")
                list_dif = [float(a) for a in raw_dif.split("·")]
                print(list_dif)
                print()
                difficulty = sum(list_dif)/len(list_dif)
            else:
                print("мы там")
                try:
                    difficulty = float(raw_dif)
                except ValueError:
                    difficulty = "Не найдено"
        else:
            difficulty = "Не найдено"
                        
                # Извлечение редактора игры
        #editor_tag = bs_game.find('a', href=lambda href: href and "/person/" in href)
        #author_game = editor_tag.get_text(strip=True) if editor_tag else "Не найдено"
        print()
        print('Дошли до авторов')
        editor_html_tag = bs_game.find('div', class_='flex flex-wrap gap-1')

        # Проверяем, что div найден
        if editor_html_tag:
            # Если найден, ищем все 'a' элементы
            editor_html = editor_html_tag.find_all('a', href=lambda href: href and "/person" in href)
            # Извлекаем текст авторов
            authors = [tag.get_text(strip=True) for tag in editor_html]
        else:
            # Если не найден, задаем authors как пустой список
            authors = []

        # Преобразуем список авторов в строку или возвращаем "Не найдено"
        author_game = ', '.join(authors) if authors else "Не найдено"
        
        game_number = int(game_number)
        
        # Сбор данных в словарь
        data = {
            "id": game_number,
            "name": game_name,
            "number_of_questions": number_of_questions,
            "start_date": game_start_date,
            "end_date": game_end_date,
            "published_date": game_published_date,
            "teams_played": commands_played,
            "difficulty": difficulty,
            "authors": author_game,
            "link": game_link
        }
        
        print(data)
        print()
        time.sleep(3)
                
        
    except Exception as e:
        count_errors += 1
        print(f'Ошибка при обработке игры {game_number}: {e}, количество ошибок {count_errors}')
        print()
        
        with open('Problematic_games', 'a', encoding='utf-8') as f:
            f.write(str(f'Ошибка при обработке игры {game_number}: {e}, количество ошибок {count_errors}'))
            f.write(str('\n'))
    else:
        with open('Successful games', 'a', encoding='utf-8') as f:
            f.write(str(data))
            f.write(str('\n'))
            
