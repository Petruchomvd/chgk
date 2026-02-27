"""Таксономия ЧГК-вопросов: удобный доступ к категориям и подкатегориям."""

from database.seed_taxonomy import TAXONOMY

# Построить словари для быстрого доступа
# {(cat_num, sub_num): (cat_name_ru, sub_name_ru), ...}
TAXONOMY_MAP = {}
# {"cat_num.sub_num": sub_name_ru, ...}
LABEL_MAP = {}
# Форматированная строка для промпта
_lines = []

for cat_idx, (cat_name, cat_name_ru, subs) in enumerate(TAXONOMY, start=1):
    for sub_idx, (sub_name, sub_name_ru) in enumerate(subs, start=1):
        TAXONOMY_MAP[(cat_idx, sub_idx)] = (cat_name_ru, sub_name_ru)
        LABEL_MAP[f"{cat_idx}.{sub_idx}"] = sub_name_ru

    sub_lines = [f"  {cat_idx}.{j}. {s[1]}" for j, s in enumerate(subs, start=1)]
    _lines.append(f"{cat_idx}. {cat_name_ru}\n" + "\n".join(sub_lines))

TAXONOMY_TEXT = "\n\n".join(_lines)


def get_label(cat: int, sub: int) -> str:
    """Человекочитаемая метка: 'История → Древний мир и Античность'."""
    pair = TAXONOMY_MAP.get((cat, sub))
    if pair:
        return f"{pair[0]} → {pair[1]}"
    return f"?({cat}.{sub})"
