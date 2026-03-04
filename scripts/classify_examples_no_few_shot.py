"""Классификация примеров БЕЗ few-shot примеров (только описание категорий)."""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import re
import json
from pathlib import Path
import ollama


CATEGORIES = [
    "История", "Литература", "Наука и технологии", "География",
    "Искусство", "Музыка", "Кино и театр", "Спорт",
    "Язык и лингвистика", "Религия и мифология", "Общество и политика",
    "Быт и повседневность", "Природа и животные", "Логика и wordplay"
]

# МИНИМАЛЬНЫЙ ПРОМПТ - только описание категорий, БЕЗ примеров
SYSTEM_PROMPT_NO_FEWSHOT = """Ты классификатор ЧГК-вопросов.

Классифицируй вопрос по одной из 14 категорий:
1. История
2. Литература
3. Наука и технологии
4. География
5. Искусство
6. Музыка
7. Кино и театр
8. Спорт
9. Язык и лингвистика
10. Религия и мифология
11. Общество и политика
12. Быт и повседневность
13. Природа и животные
14. Логика и wordplay

Ответь в формате JSON:
{"category": "название_категории", "confidence": "высокая|средняя|низкая"}"""


def parse_markdown_questions(md_path):
    """Парсит вопросы из Markdown файла."""
    content = Path(md_path).read_text(encoding='utf-8')

    questions = []
    pattern = r'### Вопрос \d+\n\n\*\*Вопрос:\*\*\s+(.*?)\n\n\*\*Ответ:\*\*\s+(.*?)(?:\n\n\*\*Комментарий|\n\n---|\Z)'

    for match in re.finditer(pattern, content, re.DOTALL):
        q_text = match.group(1).strip()
        answer = match.group(2).strip()

        questions.append({
            'text': q_text,
            'answer': answer
        })

    return questions


def classify_question(question_text, answer_text):
    """Классифицирует один вопрос БЕЗ few-shot."""
    user_message = f"""Вопрос: {question_text}

Ответ: {answer_text}

Какая категория?"""

    try:
        response = ollama.chat(
            model="qwen2.5:14b-instruct-q4_K_M",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_NO_FEWSHOT},
                {"role": "user", "content": user_message}
            ],
            format="json",
            options={"temperature": 0.1},
            stream=False
        )

        result_text = response['message']['content']
        result_json = json.loads(result_text)
        return result_json

    except Exception as e:
        return {"category": "Error", "confidence": "низкая", "error": str(e)}


def main():
    """Главный скрипт классификации БЕЗ few-shot."""
    md_path = Path(__file__).parent.parent / "docs" / "примерчгк.md"

    print("[*] Loading questions from примерчгк.md...")
    questions = parse_markdown_questions(md_path)
    print(f"[+] Loaded {len(questions)} questions")
    print(f"[*] Mode: БЕЗ few-shot примеров\n")
    print("=" * 100)

    results = []

    for i, q in enumerate(questions, 1):
        print(f"\n[{i}/{len(questions)}] Classifying question...")
        print(f"\n  Вопрос: {q['text'][:120]}{'...' if len(q['text']) > 120 else ''}")
        print(f"  Ответ: {q['answer'][:80]}{'...' if len(q['answer']) > 80 else ''}")

        result = classify_question(q['text'], q['answer'])
        result['id'] = i
        result['question'] = q['text']
        result['answer'] = q['answer']

        category = result.get('category', 'Unknown')
        confidence = result.get('confidence', 'unknown')

        print(f"\n  >>> Категория: {category}")
        print(f"  >>> Уверенность: {confidence}")
        print(f"\n" + "-" * 100)

        results.append(result)

    # Сохраняем результаты
    output_path = Path(__file__).parent.parent / "output" / "examples_classification_no_fewshot.json"
    output_path.parent.mkdir(exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Статистика
    categories_count = {}
    for r in results:
        cat = r.get('category', 'Unknown')
        categories_count[cat] = categories_count.get(cat, 0) + 1

    print("\n" + "=" * 100)
    print("\n[ИТОГИ КЛАССИФИКАЦИИ БЕЗ FEW-SHOT]")
    print(f"[+] Всего вопросов классифицировано: {len(results)}")
    print(f"[+] Результаты сохранены в: output/examples_classification_no_fewshot.json\n")

    print("[СТАТИСТИКА ПО КАТЕГОРИЯМ]")
    for cat in sorted(categories_count.keys()):
        count = categories_count[cat]
        percent = (count / len(results)) * 100
        print(f"  {cat:30} - {count:3} вопросов ({percent:5.1f}%)")


if __name__ == "__main__":
    main()
