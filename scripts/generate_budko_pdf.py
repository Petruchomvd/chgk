"""Generate PDF from Budko 'Что, где, когда произошло впервые' markdown."""
import re
import os
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT

# ── Fonts ──
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def find_font(*filenames: str) -> str:
    configured_dir = os.environ.get("CHGK_FONT_DIR")
    search_dirs = [
        Path(configured_dir).expanduser() if configured_dir else None,
        PROJECT_ROOT / "assets" / "fonts",
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/System/Library/Fonts/Supplemental"),
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    ]
    for directory in search_dirs:
        if directory is None:
            continue
        for filename in filenames:
            candidate = directory / filename
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(
        "Не найден шрифт с поддержкой кириллицы. "
        "Укажите папку со шрифтами через CHGK_FONT_DIR."
    )


pdfmetrics.registerFont(TTFont('DejaVu', find_font('DejaVuSans.ttf', 'Arial.ttf', 'arial.ttf')))
pdfmetrics.registerFont(TTFont('DejaVu-Bold', find_font('DejaVuSans-Bold.ttf', 'Arial Bold.ttf', 'arialbd.ttf')))
pdfmetrics.registerFont(TTFont('DejaVu-Oblique', find_font('DejaVuSans-Oblique.ttf', 'Arial Italic.ttf', 'ariali.ttf')))
pdfmetrics.registerFont(TTFont('DejaVu-BoldOblique', find_font('DejaVuSans-BoldOblique.ttf', 'Arial Bold Italic.ttf', 'arialbi.ttf')))
pdfmetrics.registerFontFamily(
    'DejaVu',
    normal='DejaVu',
    bold='DejaVu-Bold',
    italic='DejaVu-Oblique',
    boldItalic='DejaVu-BoldOblique',
)

# ── Colors ──
DARK_BLUE = HexColor('#1a3a5c')
ACCENT = HexColor('#2980b9')
DARK_TEXT = HexColor('#2c3e50')
MUTED = HexColor('#7f8c8d')
TITLE_BG = HexColor('#eaf2f8')

# ── Styles ──
styles = {
    'title': ParagraphStyle(
        'Title', fontName='DejaVu-Bold', fontSize=30,
        textColor=DARK_BLUE, alignment=TA_CENTER,
        spaceAfter=8*mm, leading=38,
    ),
    'subtitle': ParagraphStyle(
        'Subtitle', fontName='DejaVu', fontSize=11,
        textColor=MUTED, alignment=TA_CENTER,
        spaceAfter=3*mm, leading=14,
    ),
    'article_title': ParagraphStyle(
        'ArticleTitle', fontName='DejaVu-Bold', fontSize=11.5,
        textColor=DARK_BLUE, alignment=TA_LEFT,
        spaceBefore=6*mm, spaceAfter=2.5*mm, leading=14,
    ),
    'body': ParagraphStyle(
        'Body', fontName='DejaVu', fontSize=9.5,
        textColor=DARK_TEXT, alignment=TA_JUSTIFY,
        spaceAfter=2.5*mm, leading=13.5,
        firstLineIndent=7*mm,
    ),
    'body_first': ParagraphStyle(
        'BodyFirst', fontName='DejaVu', fontSize=9.5,
        textColor=DARK_TEXT, alignment=TA_JUSTIFY,
        spaceAfter=2.5*mm, leading=13.5,
        firstLineIndent=0,
    ),
    'toc_letter': ParagraphStyle(
        'TocLetter', fontName='DejaVu-Bold', fontSize=12,
        textColor=DARK_BLUE, spaceBefore=3*mm, spaceAfter=1*mm,
    ),
    'toc_item': ParagraphStyle(
        'TocItem', fontName='DejaVu', fontSize=8,
        textColor=DARK_TEXT, leading=11,
    ),
    'chrono_header': ParagraphStyle(
        'ChronoHeader', fontName='DejaVu-Bold', fontSize=16,
        textColor=DARK_BLUE, alignment=TA_CENTER,
        spaceBefore=10*mm, spaceAfter=6*mm,
    ),
}

# Bold lead-in patterns — matches how the original book highlights key phrases
BOLD_LEAD_PATTERNS = [
    # "Первый/Первая/Первое/Первые/Первым..." + descriptive phrase up to verb/comma
    r'^(Перв\w+\s+(?:в\s+мире\s+|в\s+истории\s+|известн\w+\s+)?'
    r'(?:(?:из|на|с|в|по|для|за|без|между|среди|через|после|до|около|более|менее)\s+)?'
    r'(?:\w+\s+){0,8}?)'
    r'(?=был[аоие]?\s|стал[аоие]?\s|появил\w+\s|состоял\w+\s|открыл\w+\s|'
    r'изобре[лт]\w*\s|провел\w*\s|построил\w*\s|создал\w*\s|получил\w*\s|'
    r'основал\w*\s|выпустил\w*\s|установил\w*\s|начал\w*\s|ввел\w*\s|'
    r'сконструировал\w*\s|запатентовал\w*\s|совершил\w*\s|произвел\w*\s|'
    r'применил\w*\s|использовал\w*\s|предложил\w*\s|придумал\w*\s|'
    r'выпущен\w*\s|опубликован\w*\s|напечатан\w*\s|изготовлен\w*\s|'
    r'продемонстрировал\w*\s|организовал\w*\s|разработал\w*\s|'
    r'-\s|—\s|–\s)',
    # "Самый/Самая/Самое..." phrases
    r'^(Сам\w+\s+\w+\s+(?:\w+\s+){0,4}?)'
    r'(?=был[аоие]?\s|стал[аоие]?\s|появил\w+\s|—\s|–\s|-\s)',
    # "Единственный/Единственная..."
    r'^(Единственн\w+\s+(?:\w+\s+){0,6}?)'
    r'(?=был[аоие]?\s|стал[аоие]?\s|—\s|–\s|-\s)',
]


def parse_markdown(filepath):
    """Parse the markdown file into structured data."""
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()

    sections = []
    chronology_text = ''

    current_letter = None
    current_articles = []
    current_title = None
    current_paragraphs = []
    in_chronology = False

    for line in text.split('\n'):
        line = line.rstrip()

        if line.startswith('## Хронология'):
            if current_title:
                current_articles.append((current_title, list(current_paragraphs)))
            if current_letter and current_articles:
                sections.append((current_letter, list(current_articles)))
            in_chronology = True
            current_title = None
            current_paragraphs = []
            continue

        if in_chronology:
            if line.strip():
                chronology_text += line.strip() + '\n'
            continue

        m = re.match(r'^## ([А-ЯЁ])$', line)
        if m:
            if current_title:
                current_articles.append((current_title, list(current_paragraphs)))
            if current_letter and current_articles:
                sections.append((current_letter, list(current_articles)))
            current_letter = m.group(1)
            current_articles = []
            current_title = None
            current_paragraphs = []
            continue

        m = re.match(r'^### (.+)$', line)
        if m:
            if current_title:
                current_articles.append((current_title, list(current_paragraphs)))
            current_title = m.group(1)
            current_paragraphs = []
            continue

        if line.strip() and current_title:
            current_paragraphs.append(line.strip())

    if current_title:
        current_articles.append((current_title, list(current_paragraphs)))
    if current_letter and current_articles:
        sections.append((current_letter, list(current_articles)))

    return sections, chronology_text


def escape_xml(text):
    """Escape special XML characters for reportlab Paragraph."""
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    return text


def add_bold_leads(text):
    """Add <b>...</b> around lead-in phrases like 'Первый автомобиль с двигателем'.

    Works on already-escaped XML text.
    """
    for pattern in BOLD_LEAD_PATTERNS:
        m = re.match(pattern, text)
        if m:
            lead = m.group(1).rstrip()
            rest = text[m.end(1):]
            return f'<b>{lead}</b> {rest.lstrip()}'
    return text


def add_header_footer(canvas, doc):
    """Add page numbers and subtle header."""
    canvas.saveState()
    page_num = canvas.getPageNumber()
    if page_num > 1:
        # Page number at bottom center
        canvas.setFont('DejaVu', 7)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(A4[0] / 2, 12*mm, f'— {page_num} —')
        # Book title at top
        canvas.setFont('DejaVu-Oblique', 7)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(A4[0] / 2, A4[1] - 11*mm,
                                 'Что, где, когда произошло впервые  •  Будко А.И.')
        # Thin line under header
        canvas.setStrokeColor(HexColor('#dce6f0'))
        canvas.setLineWidth(0.3)
        canvas.line(18*mm, A4[1] - 13*mm, A4[0] - 18*mm, A4[1] - 13*mm)
    canvas.restoreState()


def build_pdf(sections, chronology_text, output_path):
    """Build the PDF document."""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=18*mm,
        bottomMargin=18*mm,
        leftMargin=20*mm,
        rightMargin=20*mm,
    )

    story = []
    W = A4[0] - 40*mm  # content width

    # ── Title page ──
    story.append(Spacer(1, 60*mm))
    story.append(Paragraph(
        'ЧТО, ГДЕ, КОГДА<br/>ПРОИЗОШЛО ВПЕРВЫЕ',
        styles['title']
    ))
    story.append(Spacer(1, 6*mm))

    # Decorative line
    line_table = Table([['']],  colWidths=[80*mm])
    line_table.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (0, 0), 1.5, ACCENT),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
    ]))
    # Center the line
    wrapper = Table([[line_table]], colWidths=[W])
    wrapper.setStyle(TableStyle([('ALIGN', (0, 0), (0, 0), 'CENTER')]))
    story.append(wrapper)

    story.append(Spacer(1, 8*mm))
    story.append(Paragraph('Справочник', ParagraphStyle(
        'Sub', fontName='DejaVu-Oblique', fontSize=13,
        textColor=MUTED, alignment=TA_CENTER, spaceAfter=12*mm,
    )))
    story.append(Paragraph('Будко А.И.', ParagraphStyle(
        'Author', fontName='DejaVu-Bold', fontSize=13,
        textColor=DARK_TEXT, alignment=TA_CENTER, spaceAfter=3*mm,
    )))
    story.append(Paragraph('Мэджик Бук / РИПОЛ КЛАССИК, 2001', styles['subtitle']))
    story.append(Spacer(1, 35*mm))

    total = sum(len(arts) for _, arts in sections)
    story.append(Paragraph(
        f'{total} статей от А до Я',
        ParagraphStyle('Count', fontName='DejaVu-Oblique', fontSize=10,
                       textColor=ACCENT, alignment=TA_CENTER)
    ))

    story.append(PageBreak())

    # ── Table of Contents ──
    story.append(Paragraph('Содержание', ParagraphStyle(
        'TocTitle', fontName='DejaVu-Bold', fontSize=18,
        textColor=DARK_BLUE, alignment=TA_CENTER,
        spaceAfter=6*mm,
    )))

    for letter, articles in sections:
        story.append(Paragraph(f'<b>{letter}</b>', styles['toc_letter']))
        titles = [escape_xml(a[0]) for a in articles]
        toc_line = '  •  '.join(titles)
        story.append(Paragraph(toc_line, styles['toc_item']))

    story.append(PageBreak())

    # ── Articles ──
    for letter, articles in sections:
        # Big letter with decorative underline
        letter_para = Paragraph(
            letter,
            ParagraphStyle('LetterBig', fontName='DejaVu-Bold', fontSize=32,
                           textColor=DARK_BLUE, alignment=TA_LEFT,
                           spaceBefore=4*mm, spaceAfter=1*mm)
        )
        story.append(letter_para)

        # Accent line under letter
        line_data = [['', '']]
        line_table = Table(line_data, colWidths=[W, 0])
        line_table.setStyle(TableStyle([
            ('LINEBELOW', (0, 0), (0, 0), 0.8, ACCENT),
        ]))
        story.append(line_table)
        story.append(Spacer(1, 1*mm))

        for title, paragraphs in articles:
            # Article title — UPPERCASE like the original book
            title_upper = escape_xml(title.upper())
            story.append(Paragraph(title_upper, styles['article_title']))

            # Article body with bold lead-ins
            for idx, para in enumerate(paragraphs):
                para_escaped = escape_xml(para)
                para_formatted = add_bold_leads(para_escaped)
                style = styles['body_first'] if idx == 0 else styles['body']
                story.append(Paragraph(para_formatted, style))

    # ── Chronology ──
    if chronology_text.strip():
        story.append(PageBreak())
        story.append(Paragraph(
            'ХРОНОЛОГИЯ ИЗОБРЕТЕНИЙ,<br/>ОТКРЫТИЙ И ПЕРВЫХ УПОМИНАНИЙ',
            styles['chrono_header']
        ))

        # Split chronology into year entries: "1590 Text... 1591 Text..."
        chrono_flat = ' '.join(chronology_text.strip().split())
        # Split on year boundaries (3-4 digit year at word boundary)
        entries = re.split(r'(?<=[.!?)»\]]) (?=\d{3,4} [А-ЯA-Z])', chrono_flat)

        chrono_year_style = ParagraphStyle(
            'ChronoYear', fontName='DejaVu-Bold', fontSize=9,
            textColor=ACCENT, alignment=TA_LEFT,
            spaceBefore=3*mm, spaceAfter=0.5*mm,
        )
        chrono_text_style = ParagraphStyle(
            'ChronoText', fontName='DejaVu', fontSize=8.5,
            textColor=DARK_TEXT, alignment=TA_JUSTIFY,
            spaceAfter=1*mm, leading=11.5,
            leftIndent=0,
        )

        current_year = None
        current_items = []

        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue
            # Extract year from beginning
            m = re.match(r'^(\d{3,4})\s+(.+)$', entry, re.DOTALL)
            if m:
                year = m.group(1)
                text_part = m.group(2).strip()

                # If new year, flush previous
                if year != current_year:
                    if current_year and current_items:
                        story.append(Paragraph(
                            f'<b>{current_year}</b>',
                            chrono_year_style
                        ))
                        for item in current_items:
                            story.append(Paragraph(
                                escape_xml(item),
                                chrono_text_style
                            ))
                    current_year = year
                    current_items = []

                # There might be multiple items in one entry separated by year
                # Split further if there are embedded years
                sub_parts = re.split(r'(?<=[.!?)»\]]) (?=\d{3,4} [А-ЯA-Z])', text_part)
                for sp in sub_parts:
                    sp = sp.strip()
                    sm = re.match(r'^(\d{3,4})\s+(.+)$', sp)
                    if sm and sm.group(1) != current_year:
                        # Flush current year
                        if current_year and current_items:
                            story.append(Paragraph(
                                f'<b>{current_year}</b>',
                                chrono_year_style
                            ))
                            for item in current_items:
                                story.append(Paragraph(
                                    escape_xml(item),
                                    chrono_text_style
                                ))
                        current_year = sm.group(1)
                        current_items = [sm.group(2).strip()]
                    else:
                        current_items.append(sp)
            else:
                # No year prefix — append to current
                if current_items:
                    current_items[-1] += ' ' + entry
                else:
                    current_items.append(entry)

        # Flush last year
        if current_year and current_items:
            story.append(Paragraph(
                f'<b>{current_year}</b>',
                chrono_year_style
            ))
            for item in current_items:
                story.append(Paragraph(
                    escape_xml(item),
                    chrono_text_style
                ))

    # Build
    doc.build(story, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
    return total


# ── Main ──
if __name__ == '__main__':
    md_path = PROJECT_ROOT / 'data' / 'budko_vpervye.md'
    pdf_path = PROJECT_ROOT / 'data' / 'budko_vpervye.pdf'

    print('Parsing markdown...')
    sections, chronology = parse_markdown(md_path)
    print(f'  {len(sections)} letters, {sum(len(a) for _, a in sections)} articles')

    print('Generating PDF...')
    total = build_pdf(sections, chronology, pdf_path)
    print(f'Done! {total} articles -> {pdf_path}')
