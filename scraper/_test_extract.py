"""Тест извлечения вопросов из HTML страницы пакета."""
import requests
import re
import json


def extract_questions_from_pack_html(html: str) -> list[dict]:
    """Извлечь вопросы из HTML страницы пакета gotquestions.online."""
    pushes = re.findall(r'self\.__next_f\.push\(\[(.*?)\]\)', html, re.DOTALL)

    # Символы экранирования в React Server Components: \ + "
    BS = chr(92)   # backslash
    QT = chr(34)   # double quote
    ESCAPED_QUOTE = BS + QT   # \"

    for block in pushes:
        if "questions" not in block:
            continue

        # Убираем экранирование: \" -> "
        unescaped = block.replace(ESCAPED_QUOTE, QT).replace(BS + "n", "\n")

        # Ищем массив questions
        match = re.search(r'"questions":\[', unescaped)
        if not match:
            continue

        # Считаем скобки, чтобы найти конец массива
        arr_start = match.end() - 1
        depth = 0
        pos = arr_start
        in_string = False
        prev_ch = ""
        while pos < len(unescaped):
            ch = unescaped[pos]
            if ch == QT and prev_ch != BS:
                in_string = not in_string
            elif not in_string:
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        break
            prev_ch = ch
            pos += 1
        else:
            continue

        arr_str = unescaped[arr_start : pos + 1]

        try:
            questions = json.loads(arr_str)
            return questions
        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            continue

    return []


def extract_pack_metadata_from_html(html: str) -> dict | None:
    """Извлечь метаданные пакета из HTML."""
    pushes = re.findall(r'self\.__next_f\.push\(\[(.*?)\]\)', html, re.DOTALL)

    for block in pushes:
        if "questions" not in block:
            continue

        unescaped = block.replace('\\"', '"').replace("\\n", "\n")

        # Ищем объект с полями пакета: title, editors, questions и т.д.
        # Паттерн: {"id":NNN,"title":"...","editors":[...],"questions":[...],...}
        match = re.search(r'"tours":\[', unescaped)
        if not match:
            continue

        # Вернёмся назад, чтобы найти начало объекта тура
        # Ищем {"id":... перед tours
        # На самом деле нам нужен пакетный объект
        # Попробуем найти packTitle
        title_match = re.search(r'"packTitle":"([^"]*)"', unescaped)
        pack_id_match = re.search(r'"packId":(\d+)', unescaped)

        if title_match and pack_id_match:
            return {
                "title": title_match.group(1),
                "pack_id": int(pack_id_match.group(1)),
            }

    return None


if __name__ == "__main__":
    print("Тестируем парсинг pack/6545...")
    resp = requests.get("https://gotquestions.online/pack/6545", timeout=15)
    html = resp.text
    print(f"HTML размер: {len(html)} символов")

    questions = extract_questions_from_pack_html(html)
    print(f"\nИзвлечено вопросов: {len(questions)}")

    if questions:
        q = questions[0]
        print(f"\nПервый вопрос:")
        print(f"  id: {q['id']}")
        print(f"  number: {q['number']}")
        print(f"  text: {q['text'][:120]}...")
        print(f"  answer: {q['answer']}")
        print(f"  comment: {q.get('comment', '')[:80]}")
        print(f"  source: {q.get('source', '')[:80]}")
        print(f"  authors: {q.get('authors')}")

        ql = questions[-1]
        print(f"\nПоследний вопрос:")
        print(f"  id: {ql['id']}")
        print(f"  number: {ql['number']}")
        print(f"  answer: {ql['answer']}")

        print(f"\nПоля вопроса: {list(q.keys())}")

    # Тест на другом пакете
    print("\n\nТестируем pack/6500...")
    resp2 = requests.get("https://gotquestions.online/pack/6500", timeout=15)
    questions2 = extract_questions_from_pack_html(resp2.text)
    print(f"Извлечено вопросов: {len(questions2)}")
    if questions2:
        print(f"  IDs: {questions2[0]['id']} - {questions2[-1]['id']}")

    # Тест на pack/1 (маленький)
    print("\n\nТестируем pack/1...")
    resp3 = requests.get("https://gotquestions.online/pack/1", timeout=15)
    questions3 = extract_questions_from_pack_html(resp3.text)
    print(f"Извлечено вопросов: {len(questions3)}")
    if questions3:
        print(f"  IDs: {questions3[0]['id']} - {questions3[-1]['id']}")
