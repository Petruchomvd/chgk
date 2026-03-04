"""Конвертирует примерчгк.xlsx в Markdown файл."""

import pandas as pd
from pathlib import Path


def convert_xlsx_to_md():
    """Читает Excel и сохраняет в Markdown."""

    # Читаем Excel
    xlsx_path = Path(__file__).parent.parent / "docs" / "примерчгк.xlsx"
    df = pd.read_excel(xlsx_path)

    # Вывод
    md_path = Path(__file__).parent.parent / "docs" / "примерчгк.md"

    # Генерируем Markdown
    md_content = "# Примеры вопросов ЧГК для классификации\n\n"
    md_content += f"**Всего вопросов:** {len(df) - 1}\n\n"

    current_category = None
    question_count = 0

    for idx, row in df.iterrows():
        # Пропускаем заголовок
        if idx == 0:
            continue

        # Проверяем не является ли это заголовком категории
        if pd.notna(row['№']) and isinstance(row['№'], str):
            current_category = row['№']
            md_content += f"\n## {current_category}\n\n"
            continue

        # Пропускаем пустые строки
        if pd.isna(row['Текст вопроса']):
            continue

        question_count += 1
        q_num = int(row['№']) if pd.notna(row['№']) else question_count

        # Добавляем вопрос
        md_content += f"### Вопрос {q_num}\n\n"

        if pd.notna(row['Текст вопроса']):
            md_content += f"**Вопрос:** {row['Текст вопроса']}\n\n"

        if pd.notna(row['Ответ']):
            md_content += f"**Ответ:** {row['Ответ']}\n\n"

        if pd.notna(row['Комментарий']):
            md_content += f"**Комментарий:** {row['Комментарий']}\n\n"

        if pd.notna(row['Ссылка']):
            md_content += f"**Источник:** {row['Ссылка']}\n\n"

        if pd.notna(row['id вопроса']):
            md_content += f"**ID:** {int(row['id вопроса'])}\n\n"

        md_content += "---\n\n"

    # Сохраняем
    md_path.write_text(md_content, encoding='utf-8')
    print(f"[OK] Saved: {md_path}")
    print(f"[INFO] Total questions: {question_count}")


if __name__ == "__main__":
    convert_xlsx_to_md()
