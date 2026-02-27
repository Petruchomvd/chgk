from pathlib import Path

html = Path('Text_files/index_page.html').read_text(encoding='utf-8')
needle = '\\\\\"count\\\\\":'
idx = html.find(needle)
print('index', idx)
if idx != -1:
    snippet = html[idx:idx + 200]
    print(snippet)
