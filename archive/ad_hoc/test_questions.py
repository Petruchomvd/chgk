from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
import sqlite3
import time

#Путь к chromedriver для корректной работы selenium
service = Service(r"C:/Users/User/.cache/selenium/chromedriver/win64/131.0.6778.204/chromedriver.exe")
#Переменная driver для запуска браузера Selenium
driver = webdriver.Chrome(service=service)

db_path = 'chgk1.db'

connection = sqlite3.connect(db_path)
cursor = connection.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS Questions (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    answer TEXT NOT NULL,
    comment TEXT,
    teams_answered INTEGER,
    image_link TEXT,
    FOREIGN KEY (game_id) REFERENCES Games (id) ON DELETE CASCADE
);
''')
connection.commit()
connection.close()

game_numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9]

url = f"https://gotquestions.online/pack/{2}"

driver.get(url)

wait = WebDriverWait(driver, 3)

button = wait.until(EC.presence_of_element_located((
    By.XPATH, "//button[text()='visibility_off']"  # Ищем кнопку с текстом
)))
button.click()

html_content = driver.page_source

bs = BeautifulSoup(html_content, 'html.parser')

questions_blocks = bs.find_all('div', class_ = 'min-w-full')
for block in questions_blocks:
    
    #number and id
    number_and_id_div = block.find('div', 'break-words block w-full qScroll')
    number = number_and_id_div.get('number') if number_and_id_div else "Номер не найден"
    id = number_and_id_div.get('id') if number_and_id_div else "Id не найдено"
    
    #text
    question_text_div = block.find('div', class_ = 'whitespace-pre-wrap')
    question_text = question_text_div.get_text(strip=True) if question_text_div else "Текст вопроса отсутствует"
    
    #OTHER BLOCK
    
    new_block = block.find('div', class_ = 'w-full')
    
    
    answer = new_block.find_next_sibling('div')
    print(answer)
    
    data = {
        'ID вопроса': id,
        'Номер вопроса в пакете': number,
        'Текст вопроса': question_text
        
    }
    
    print(data)
    print()
        
    
    
