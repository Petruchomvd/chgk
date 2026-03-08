#!/usr/bin/env python3
"""Generate personalized PDF reports — HTML+CSS → Playwright (Chromium).

Design: KPI Dashboard style, Montserrat font, 4-color palette.
Generates landscape A4 PDF for each team member.
"""

import asyncio
import base64
import io
import json
import sqlite3
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Patch
import numpy as np
import openpyxl

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "chgk_analysis.db"
SURVEY = ROOT / "результаты.xlsx"
GSET_THEMES = ROOT / "data" / "gentleman_set" / "thematic_mapping.json"
GSET_ANSWERS = ROOT / "data" / "gentleman_set" / "top_answers.json"
OUT = ROOT / "output" / "reports"

# ── Palette ────────────────────────────────────────────────────────────────────
GOLD = "#FFD700"
WHITE = "#FFFFFF"
BLACK = "#000000"
GRAY = "#808080"
GOLD_START, GOLD_END = "#FFD700", "#FFF8DC"
GRAY_START, GRAY_END = "#808080", "#C0C0C0"
BG = "#F5F5F5"

matplotlib.rcParams["font.family"] = "Montserrat"

# ── Categories ─────────────────────────────────────────────────────────────────
CAT14 = [
    "История", "Литература", "Наука и технологии", "География",
    "Искусство", "Музыка", "Кино и театр", "Спорт",
    "Язык и лингвистика", "Религия и мифология", "Общество и политика",
    "Быт и повседневность", "Природа и животные", "Логика и wordplay",
]
CSHORT = [
    "История", "Литература", "Наука", "География",
    "Искусство", "Музыка", "Кино", "Спорт",
    "Язык", "Религия", "Общество", "Быт", "Природа", "Логика",
]
SURV_COLS = list(range(3, 17))
IQ_AUTHORS = [
    "Дмитрий Соловьёв", "Николай Федоренко", "Николай Быстров",
    "Евгения Ширяева", "Дарья Коровкина", "Павел Гонтарь",
    "Юрий Калякин", "Борис Белозёров", "Рахматулла Овезов", "Ариф Багапов",
]

# ── Personalized AI recommendations ──────────────────────────────────────────
AI_RECS = {
    "Женя Дядченко": (
        "Женя, у тебя отличная база — 7 категорий на уровне 4, "
        "это очень широкий кругозор. Ты можешь спокойно подключаться "
        "к обсуждению почти любой темы и это здорово. "
        "Наука, Спорт и Природа — зоны, где можно немного подтянуться, "
        "но они в сумме ~15% вопросов, так что не стоит на них зацикливаться. "
        "Просто полистай джентльменский набор по этим категориям перед турниром. "
        "Главное — играй в удовольствие и доверяй своей интуиции, "
        "она у тебя хорошо работает."
    ),
    "Кирилл": (
        "Кирилл, твоя экспертиза в праве и обществе (5 из 5) — это ценный ресурс, "
        "но в ЧГК вопросы по праву встречаются редко. "
        "Зато категория «Общество и политика» — это 10.4% всех вопросов, и здесь ты лидер команды. "
        "Главная задача — расширить кругозор в 3–4 ключевых категориях: "
        "Литература (10.4%), История (6.5%, усилена на IQ ПФО), Кино (10.5%). "
        "Не нужно стать экспертом — достаточно знать основные произведения, "
        "ключевые исторические события и культовые фильмы. "
        "Твоя роль держателя формы вопроса важна — продолжай удерживать фокус "
        "на том, ЧТО именно спрашивают."
    ),
    "Егор": (
        "Егор, сильная сторона — Наука и техника (4), плюс уникальные знания "
        "о военной истории и танках. Наука встречается в 5.9% вопросов, "
        "а военная история часто пересекается с категорией «История» (6.5%), "
        "которая усилена на IQ ПФО (+1.4%). "
        "Самый большой потенциал роста — Литература (10.4%): это самая частая "
        "категория после Логики и Кино, а у тебя она на 1. "
        "Даже знание 20 ключевых произведений и авторов из джентльменского набора "
        "даст ощутимый эффект. Искусство (4.1%) и Музыка (4.3%) — менее частые, "
        "но базовые факты стоит выучить. Твоя энергия и быстрые версии — "
        "сила в обсуждении, используй её активнее."
    ),
    "Арсений": (
        "Арсений, твоя комбинация — редкая для команды: "
        "ты единственный, кто сильно разбирается в Географии (4) и Религии (4), "
        "плюс Логика (4) — самая частая категория (10.5%). "
        "На IQ ПФО География встречается чаще глобального (+1.3%), "
        "а Религия — на стабильном уровне (4.8%). Это значит, что твои знания "
        "будут востребованы. Основная зона роста — Музыка (1), но она редкая (4.3%), "
        "поэтому ROI невысокий. Лучше подтянуть Литературу (10.4%) и Кино (10.5%) — "
        "базовые произведения и фильмы. Твой стиль «тихий мыслитель» ценен — "
        "продолжай слушать обсуждение и выдавать точный ответ в конце."
    ),
    "Алина": (
        "Алина, ты закрываешь уникальную нишу в команде — Музыка (4), "
        "которую больше никто не покрывает. Плюс Кино (4), Логика (4), Быт (4) — "
        "это 4 сильные категории, покрывающие ~30% вопросов. "
        "Опыт капитана помогает быстро разбирать вопрос для команды. "
        "Критические пробелы: Спорт (1) — командная слепая зона, "
        "ни у кого нет больше 2, и Природа (1) — то же самое. "
        "Даже небольшие усилия здесь ценны, потому что закрывать эти категории некому. "
        "Знания по HP, Властелину Колец и детективам — отличная база "
        "для Литературы, но стоит расширить на классику (Пушкин, Шекспир, Дон Кихот)."
    ),
    "Матвей": (
        "Как капитан, главная сила — в координации, спокойствии и аналитике. "
        "Две сильные области — Общество (4) и Быт (4) — покрывают ~19% вопросов. "
        "Остальные 12 категорий на уровне 1–2 — самый большой разброс в команде. "
        "Стратегия: не пытаться выучить всё, а сфокусироваться на 2–3 категориях "
        "с высокой частотностью. Приоритет — История (6.5%, усилена на IQ ПФО) "
        "и Литература (10.4%). Знание английского — скрытое преимущество: "
        "многие вопросы содержат отсылки к англоязычной культуре. "
        "Твоя роль — направлять команду к ответу, а не знать всё самому. "
        "Используй аналитику из этого отчёта, чтобы понимать, "
        "кому из команды адресовать какой вопрос."
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

def load_survey():
    wb = openpyxl.load_workbook(str(SURVEY))
    ws = wb.active
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[1] is None: continue
        scores = [int(row[i]) if row[i] else 0 for i in SURV_COLS]
        out.append(dict(
            name=str(row[1]).strip(), role=str(row[2]).strip(),
            scores=dict(zip(CAT14, scores)), scores_list=scores,
            expertise=str(row[17] or ""), improve=str(row[18] or ""),
            style_pref=str(row[19] or ""), behavior=str(row[20] or ""),
        ))
    return out

def load_gset():
    """Load gentleman set: 1400+ entities mapped to 14 categories."""
    with open(GSET_THEMES, "r", encoding="utf-8") as f:
        tm = json.load(f)
    with open(GSET_ANSWERS, "r", encoding="utf-8") as f:
        ta = json.load(f)
    et = tm["entity_themes"]
    freq = dict(ta.get("top_answers", []))
    disp = ta.get("display_forms", {})
    # Build {category: [(display_name, frequency), ...]} sorted by freq
    cats = {}
    for key, theme in et.items():
        cat = theme.get("category", "")
        fr = freq.get(key, 0)
        if fr >= 3 and cat:
            cats.setdefault(cat, []).append((disp.get(key, key), fr))
    for c in cats:
        cats[c].sort(key=lambda x: -x[1])
    return cats

def cat_dist(conn, mf="%72b%"):
    rows = conn.execute("""
        SELECT c.name_ru, COUNT(*) FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id=s.id
        JOIN categories c ON s.category_id=c.id
        WHERE qt.model_name LIKE ? GROUP BY c.name_ru""", (mf,)).fetchall()
    t = sum(r[1] for r in rows)
    return {r[0]: r[1]/t*100 for r in rows}

def iq_dist(conn):
    af = " OR ".join(["q.authors LIKE ?" for _ in IQ_AUTHORS])
    ps = [f"%{a}%" for a in IQ_AUTHORS]
    rows = conn.execute(f"""
        SELECT c.name_ru, COUNT(*) FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id=s.id
        JOIN categories c ON s.category_id=c.id
        JOIN questions q ON qt.question_id=q.id
        WHERE qt.model_name LIKE '%72b%' AND ({af})
        GROUP BY c.name_ru""", ps).fetchall()
    t = sum(r[1] for r in rows)
    return {r[0]: r[1]/t*100 for r in rows}

def subcat_dist(conn, cat, mf="%72b%"):
    rows = conn.execute("""
        SELECT s.name_ru, COUNT(*) FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id=s.id
        JOIN categories c ON s.category_id=c.id
        WHERE c.name_ru=? AND qt.model_name LIKE ?
        GROUP BY s.name_ru ORDER BY COUNT(*) DESC""", (cat, mf)).fetchall()
    t = sum(r[1] for r in rows)
    return [(r[0], r[1], r[1]/t*100 if t else 0) for r in rows]

def iq_subcat_dist(conn, cat):
    af = " OR ".join(["q.authors LIKE ?" for _ in IQ_AUTHORS])
    ps = [cat]+[f"%{a}%" for a in IQ_AUTHORS]
    rows = conn.execute(f"""
        SELECT s.name_ru, COUNT(*) FROM question_topics qt
        JOIN subcategories s ON qt.subcategory_id=s.id
        JOIN categories c ON s.category_id=c.id
        JOIN questions q ON qt.question_id=q.id
        WHERE c.name_ru=? AND qt.model_name LIKE '%72b%' AND ({af})
        GROUP BY s.name_ru ORDER BY COUNT(*) DESC""", ps).fetchall()
    t = sum(r[1] for r in rows)
    return [(r[0], r[1], r[1]/t*100 if t else 0) for r in rows]

def ents_for(gs, cat, n=None):
    """Get gentleman set entities for a category. gs is {cat: [(name, freq), ...]}."""
    items = gs.get(cat, [])
    if n:
        return items[:n]
    return items


def author_profiles(conn):
    """Return [(author, total, [(cat, pct), ...top3]), ...] for IQ PFO authors."""
    results = []
    for author in IQ_AUTHORS:
        # Count distinct questions per category
        rows = conn.execute("""
            SELECT c.name_ru, COUNT(DISTINCT qt.question_id) as cnt
            FROM question_topics qt
            JOIN subcategories s ON qt.subcategory_id=s.id
            JOIN categories c ON s.category_id=c.id
            JOIN questions q ON qt.question_id=q.id
            WHERE qt.model_name LIKE '%72b%' AND q.authors LIKE ?
            GROUP BY c.id ORDER BY cnt DESC""", (f"%{author}%",)).fetchall()
        # Total unique questions for this author
        total = conn.execute("""
            SELECT COUNT(DISTINCT qt.question_id)
            FROM question_topics qt
            JOIN questions q ON qt.question_id=q.id
            WHERE qt.model_name LIKE '%72b%' AND q.authors LIKE ?""",
            (f"%{author}%",)).fetchone()[0]
        if total < 10:
            continue
        top3 = [(r[0], r[1] / total * 100) for r in rows[:3]]
        results.append((author, total, top3))
    results.sort(key=lambda x: -x[1])
    return results


# ══════════════════════════════════════════════════════════════════════════════
# CHARTS → base64 PNG
# ══════════════════════════════════════════════════════════════════════════════

def _to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode()


def _gradient_vbar(ax, x, height, width, c1, c2, bottom=0, steps=30):
    cmap = LinearSegmentedColormap.from_list('', [c1, c2])
    step_h = height / steps
    for i in range(steps):
        ax.bar(x, step_h, width=width, bottom=bottom + i * step_h,
               color=cmap(i/steps), edgecolor='none', zorder=2)

def _gradient_hbar(ax, y, width_val, height, c1, c2, steps=30):
    cmap = LinearSegmentedColormap.from_list('', [c1, c2])
    step_w = width_val / steps
    for i in range(steps):
        ax.barh(y, step_w, height=height, left=i * step_w,
                color=cmap(i/steps), edgecolor='none', zorder=2)


def chart_radar(pscores, pname):
    n = len(CSHORT)
    angles = np.linspace(0, 2*np.pi, n, endpoint=False).tolist()
    angles += angles[:1]
    vals = [pscores.get(c,0) for c in CAT14] + [pscores.get(CAT14[0],0)]

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    ax.set_theta_offset(np.pi/2); ax.set_theta_direction(-1)
    ax.set_ylim(0, 5.5); ax.set_yticks([1,2,3,4,5])
    ax.set_yticklabels(["1","2","3","4","5"], fontsize=9, color=GRAY)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(CSHORT, fontsize=10, fontweight="semibold", color=BLACK)
    ax.spines["polar"].set_color(GRAY); ax.spines["polar"].set_linewidth(0.3)
    ax.yaxis.grid(True, color=GRAY, linewidth=0.2, alpha=0.4)
    ax.xaxis.grid(True, color=GRAY, linewidth=0.2, alpha=0.4)

    ax.fill(angles, vals, alpha=0.15, color=GOLD)
    ax.plot(angles, vals, color=GOLD, linewidth=2.8, label=pname)
    ax.scatter(angles[:-1], vals[:-1], color=GOLD, s=50, zorder=5, edgecolors=WHITE, linewidth=1.5)
    ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.1), fontsize=10,
              frameon=True, facecolor=WHITE, edgecolor=GRAY, framealpha=0.9)
    ax.set_aspect("equal")
    return _to_b64(fig)



def chart_comparison_h(cats, gpcts, ipcts):
    fig, ax = plt.subplots(figsize=(11, 5.5))
    fig.patch.set_alpha(0); ax.set_facecolor("none")
    for sp in ax.spines.values(): sp.set_visible(False)
    y = np.arange(len(cats)); h = 0.32
    for i, val in enumerate(gpcts):
        if val > 0: _gradient_hbar(ax, y[i]+h/2, val, h, GRAY_START, GRAY_END)
    for i, val in enumerate(ipcts):
        if val > 0: _gradient_hbar(ax, y[i]-h/2, val, h, GOLD_START, GOLD_END)
    for i, (g, p) in enumerate(zip(gpcts, ipcts)):
        ax.text(g+0.4, i+h/2, f"{g:.1f}%", va="center", fontsize=9, color=GRAY)
        ax.text(p+0.4, i-h/2, f"{p:.1f}%", va="center", fontsize=9, color=BLACK, fontweight="medium")
    ax.set_yticks(y)
    ax.set_yticklabels(cats, fontsize=11, color=BLACK)
    ax.tick_params(axis='y', length=0); ax.tick_params(axis='x', colors=GRAY, labelsize=9)
    ax.xaxis.grid(True, color=GRAY, linewidth=0.12, alpha=0.4)
    ax.legend(handles=[Patch(facecolor=GOLD, alpha=0.8, label='IQ ПФО'),
                       Patch(facecolor=GRAY, alpha=0.3, label='Глобальное')],
              fontsize=10, frameon=True, facecolor=WHITE, edgecolor=GRAY, loc="lower right")
    ax.set_xlim(0, max(max(gpcts), max(ipcts))+4)
    plt.tight_layout()
    return _to_b64(fig)


def chart_subcats_h(subcats, title=""):
    subcats = subcats[:6]
    labels = [s[0] if len(s[0]) <= 24 else s[0][:23]+"…" for s in subcats]
    vals = [s[2] for s in subcats]
    fig, ax = plt.subplots(figsize=(9, max(2.2, len(labels)*0.7)))
    fig.patch.set_alpha(0); ax.set_facecolor("none")
    for sp in ax.spines.values(): sp.set_visible(False)
    for i, val in enumerate(vals):
        if val > 0: _gradient_hbar(ax, i, val, 0.5, GOLD_START, GOLD_END)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=11, color=BLACK)
    ax.invert_yaxis()
    ax.set_xlim(0, max(vals)*1.3 if vals else 10)
    ax.tick_params(axis='y', length=0); ax.tick_params(axis='x', colors=GRAY, labelsize=9)
    ax.xaxis.grid(True, color=GRAY, linewidth=0.12, alpha=0.4)
    for i, v in enumerate(vals):
        ax.text(v+0.8, i, f"{v:.0f}%", va="center", fontsize=10, color=GRAY, fontweight="medium")
    if title:
        ax.set_title(title, fontsize=13, fontweight="semibold", color=BLACK, pad=12, loc="left")
    plt.tight_layout()
    return _to_b64(fig)


# ══════════════════════════════════════════════════════════════════════════════
# CSS — full modern CSS (rendered by Chromium)
# ══════════════════════════════════════════════════════════════════════════════

CSS = f"""
@page {{
    size: A4 landscape;
    margin: 0;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Montserrat', 'Segoe UI', Arial, sans-serif;
    font-size: 11pt;
    color: {BLACK};
    line-height: 1.45;
    background: {BG};
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}}

/* ── Title page ─────────────────────────────────────── */
.title-page {{
    width: 297mm; height: 210mm;
    background: {BG};
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    position: relative;
    page-break-after: always;
}}
.title-page::before {{
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 6px;
    background: linear-gradient(90deg, {GOLD}, {WHITE});
}}
.title-page::after {{
    content: '';
    position: absolute; bottom: 0; left: 0; right: 0; height: 6px;
    background: linear-gradient(90deg, {WHITE}, {GOLD});
}}
.title-card {{
    background: rgba(255,255,255,0.92);
    border: 1px solid rgba(255,215,0,0.25);
    border-radius: 24px;
    padding: 50px 70px;
    text-align: center;
    box-shadow: 0 4px 24px rgba(128,128,128,0.1);
}}
.title-card h1 {{
    font-size: 34pt; font-weight: 700; margin: 0;
    letter-spacing: 2px;
}}
.title-card .gold {{ color: {GOLD}; }}
.title-card .divider {{
    width: 120px; height: 3px; background: {GOLD};
    border-radius: 2px; margin: 18px auto;
}}
.title-card .name {{ font-size: 20pt; font-weight: 600; margin: 6px 0; }}
.title-card .role {{ font-size: 12pt; font-weight: 300; color: {GRAY}; }}
.kpi-row {{
    display: flex; gap: 18px; margin-top: 28px; justify-content: center;
}}
.kpi-box {{
    background: {BG};
    border-radius: 14px;
    padding: 14px 26px;
    text-align: center;
    box-shadow: 0 2px 10px rgba(128,128,128,0.07);
    min-width: 120px;
}}
.kpi-box .val {{ font-size: 26pt; font-weight: 700; color: {GOLD}; }}
.kpi-box .label {{ font-size: 8pt; color: {GRAY}; margin-top: 3px; }}
.title-footer {{
    margin-top: 24px;
    font-size: 9pt; font-weight: 300; color: {GRAY};
}}

/* ── Section pages ──────────────────────────────────── */
.page {{
    width: 297mm; min-height: 210mm;
    padding: 14px 24px;
    page-break-before: always;
}}
.sec-header {{
    margin-bottom: 10px;
}}
.sec-header .num {{
    font-size: 12pt; font-weight: 700; color: {GOLD};
}}
.sec-header h2 {{
    font-size: 17pt; font-weight: 700; margin: 2px 0 5px 0;
}}
.sec-header .bar {{
    height: 3px; background: {GOLD}; border-radius: 2px;
}}

/* ── Cards ──────────────────────────────────────────── */
.card {{
    background: {WHITE};
    border-radius: 12px;
    padding: 12px 18px;
    margin: 8px 0;
    box-shadow: 0 2px 10px rgba(128,128,128,0.08);
    page-break-inside: avoid;
}}
.card-gold {{
    border-left: 4px solid {GOLD};
}}
.card-title {{
    font-size: 11pt; font-weight: 600; margin-bottom: 3px;
}}
.card-body {{
    font-size: 10pt; color: {GRAY}; line-height: 1.55;
}}

/* ── Charts ─────────────────────────────────────────── */
.chart {{
    text-align: center;
    margin: 8px 0;
}}
.chart img {{
    max-width: 100%;
    height: auto;
}}

/* ── Two column layout ──────────────────────────────── */
.two-col {{
    display: flex; gap: 20px;
}}
.two-col > .col {{ flex: 1; }}

/* ── Numbered blocks ────────────────────────────────── */
.num-block {{
    background: {WHITE};
    border-radius: 12px;
    padding: 12px 16px;
    margin: 7px 0;
    box-shadow: 0 2px 8px rgba(128,128,128,0.06);
    display: flex;
    gap: 14px;
    align-items: flex-start;
}}
.num-block .num {{
    font-size: 20pt; font-weight: 700; color: {GOLD};
    min-width: 40px;
}}
.num-block .content .nb-title {{
    font-size: 11pt; font-weight: 600;
}}
.num-block .content .nb-body {{
    font-size: 10pt; color: {GRAY}; margin-top: 2px;
}}

/* ── Entity table ───────────────────────────────────── */
.ent-table {{
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    border-radius: 10px;
    overflow: hidden;
    margin: 8px 0;
    box-shadow: 0 2px 8px rgba(128,128,128,0.06);
}}
.ent-table th {{
    background: rgba(255,215,0,0.12);
    padding: 7px 12px;
    text-align: left;
    font-size: 10pt; font-weight: 600;
}}
.ent-table td {{
    padding: 6px 12px;
    border-top: 1px solid rgba(128,128,128,0.1);
    font-size: 10pt;
    background: {WHITE};
}}

/* ── Diff cards ─────────────────────────────────────── */
.diff-row {{
    display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0;
}}
.diff-card {{
    background: {WHITE};
    border-radius: 10px;
    padding: 10px 14px;
    box-shadow: 0 1px 6px rgba(128,128,128,0.06);
    font-size: 10pt;
    flex: 1 1 46%;
}}
.diff-card.gold-accent {{ border-left: 3px solid {GOLD}; }}
.diff-card.gray-accent {{ border-left: 3px solid {GRAY}; }}
.warn {{ color: #E0A000; font-weight: 600; }}

/* ── Footer ─────────────────────────────────────────── */
.footer {{
    font-size: 8pt; color: {GRAY}; font-weight: 300;
    margin-top: 16px;
    text-align: center;
}}
.priority-high {{ color: {GOLD}; font-weight: 700; }}
.priority-mid {{ color: {GRAY}; font-weight: 600; }}
"""


# ══════════════════════════════════════════════════════════════════════════════
# HTML BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_html(person, people, gs, conn, gdist, iqdist):
    name = person["name"]
    tavg = {c: sum(p["scores"][c] for p in people)/len(people) for c in CAT14}
    expert = [c for c in CAT14 if person["scores"][c] >= 4]
    weak = [c for c in CAT14 if person["scores"][c] <= 2]

    imp_raw = person.get("improve","").split(",")
    imp = []
    for r in imp_raw:
        r = r.strip()
        for c in CAT14:
            if r in c or c in r:
                imp.append(c); break

    # ── Charts ───────────────────────────────────────────────────────────
    radar_b64 = chart_radar(person["scores"], name)

    sorted_c = sorted(CAT14, key=lambda c: -iqdist.get(c, 0))
    comp_b64 = chart_comparison_h(
        [CSHORT[CAT14.index(c)] for c in sorted_c],
        [gdist.get(c, 0) for c in sorted_c],
        [iqdist.get(c, 0) for c in sorted_c])

    # Classification overview chart (global distribution)
    dist_sorted = sorted(CAT14, key=lambda c: -gdist.get(c, 0))
    overview_b64 = chart_subcats_h(
        [(CSHORT[CAT14.index(c)], 0, gdist.get(c, 0)) for c in dist_sorted],
        title="Распределение категорий (22 000+ вопросов)")

    # ── AI recommendation ─────────────────────────────────────────────────
    ai_rec = AI_RECS.get(name, "")

    # ── Gentleman set — full tables per category ──────────────────────────
    # Show for expert cats + improvement cats
    gset_cats = list(dict.fromkeys(expert + imp + weak))  # unique, ordered
    gset_sections = ""
    for cat in gset_cats:
        sn = CSHORT[CAT14.index(cat)]
        items = ents_for(gs, cat, 50)
        if not items:
            continue
        sc_val = person["scores"][cat]
        label = "★ Экспертная" if sc_val >= 4 else "↑ Подтянуть"
        # Inline: "Сущность (Ч), Сущность (Ч), ..."
        inline = ", ".join(f"<span style='white-space:nowrap'>{ename} ({freq})</span>" for ename, freq in items)
        # Subcategories for this category
        subs = subcat_dist(conn, cat)
        sub_line = " · ".join(f"{s[0]} ({s[2]:.0f}%)" for s in subs[:4])
        gset_sections += f'''
        <div class="card" style="margin-bottom:8px">
            <div class="card-title">{sn} <span style="font-size:9pt;color:{GOLD if sc_val>=4 else GRAY};font-weight:400">({label} · {sc_val}/5)</span></div>
            {"<div style='font-size:9pt;color:" + BLACK + ";margin-bottom:4px'><b>Подкатегории:</b> " + sub_line + "</div>" if sub_line else ""}
            <div class="card-body" style="font-size:9pt;line-height:1.5">{inline}</div>
        </div>\n'''

    # ── Above/below ──────────────────────────────────────────────────────
    above = [CSHORT[CAT14.index(c)] for c in CAT14 if person["scores"][c] > tavg[c]+0.5]
    below = [CSHORT[CAT14.index(c)] for c in CAT14 if person["scores"][c] < tavg[c]-0.5]

    above_html = f'<span style="color:{GOLD};font-weight:700">ВЫШЕ СРЕДНЕГО:</span> {", ".join(above)}' if above else ""
    below_html = f'<span style="color:{GRAY}">Ниже среднего:</span> {", ".join(below)}' if below else ""

    # ── Expert cats text ─────────────────────────────────────────────────
    cats_txt = " · ".join(f'<b>{CSHORT[CAT14.index(c)]}</b> ({person["scores"][c]})' for c in expert) if expert else "Нет категорий 4–5"

    # ── Development ──────────────────────────────────────────────────────
    dev = list(dict.fromkeys(imp + weak))
    dev.sort(key=lambda c: -gdist.get(c, 0))
    dev_blocks = ""
    for idx, cat in enumerate(dev[:6], 1):
        sn = CSHORT[CAT14.index(cat)]
        sc_val = person["scores"][cat]
        fr = gdist.get(cat, 0)
        pr = "ВЫСОКИЙ" if fr > 8 and sc_val <= 2 else "СРЕДНИЙ" if fr > 5 else "НИЗКИЙ"
        pr_cls = "priority-high" if pr == "ВЫСОКИЙ" else "priority-mid"
        subs = subcat_dist(conn, cat)
        sub_t = " · ".join(f"{x[0]} ({x[2]:.0f}%)" for x in subs[:3])
        ents = ents_for(gs, cat, 5)
        ent_t = ", ".join(f'{e[0]} ({e[1]})' for e in ents)
        body_parts = []
        if sub_t: body_parts.append(f"Подкатегории: {sub_t}")
        if ent_t: body_parts.append(f"Ключевые: {ent_t}")
        dev_blocks += f'''<div class="num-block">
            <div class="num">{idx:02d}</div>
            <div class="content">
                <div class="nb-title">{sn} · {sc_val}/5 · {fr:.1f}% · <span class="{pr_cls}">ПРИОРИТЕТ: {pr}</span></div>
                <div class="nb-body">{"<br>".join(body_parts)}</div>
            </div>
        </div>\n'''

    # ── IQ PFO diffs ─────────────────────────────────────────────────────
    diffs = [(c, iqdist.get(c,0)-gdist.get(c,0), iqdist.get(c,0))
             for c in CAT14 if abs(iqdist.get(c,0)-gdist.get(c,0)) > 1]
    diffs.sort(key=lambda x: -abs(x[1]))
    diff_cards = ""
    for cat, d, pfo in diffs:
        sn = CSHORT[CAT14.index(cat)]
        sc_val = person["scores"][cat]
        arr = "▲" if d > 0 else "▼"
        accent = "gold-accent" if d > 0 else "gray-accent"
        warn = f' <span class="warn">⚠ СЛАБАЯ ОБЛАСТЬ</span>' if d > 0 and sc_val <= 2 else ""
        diff_cards += f'''<div class="diff-card {accent}">
            <b style="color:{'#FFD700' if d>0 else '#808080'}">{sn} {arr} {d:+.1f}%</b>
            (IQ ПФО: {pfo:.1f}%) · оценка: {sc_val}/5{warn}
        </div>\n'''

    # ── Tournament priorities ────────────────────────────────────────────
    pp = [(c, person["scores"][c], iqdist.get(c,0), iqdist.get(c,0)-gdist.get(c,0))
           for c in CAT14 if person["scores"][c] <= 2 and iqdist.get(c,0) > gdist.get(c,0)]
    pp.sort(key=lambda x: -x[3])
    pp_cards = ""
    for cat, sc_val, pfo_pct, boost in pp[:3]:
        sn = CSHORT[CAT14.index(cat)]
        isubs = iq_subcat_dist(conn, cat)
        st_ = ", ".join(f"{x[0]} ({x[2]:.0f}%)" for x in isubs[:3])
        pp_cards += f'''<div class="card card-gold">
            <div class="card-title">{sn} — на турнире чаще на {boost:.1f}%</div>
            <div class="card-body">Оценка: {sc_val}/5. У авторов IQ ПФО: {pfo_pct:.1f}%. Подкатегории: {st_}</div>
        </div>\n'''

    # ── Author profiles ───────────────────────────────────────────────
    aprofs = author_profiles(conn)
    weak_set = set(weak)
    author_cards = ""
    for aname, atotal, atop3 in aprofs:
        cats_line = " · ".join(f"{c} {p:.0f}%" for c, p in atop3)
        # Warn if author's top category is in person's weak areas
        warnings = [c for c, _ in atop3 if c in weak_set]
        warn_html = ""
        if warnings:
            ws = ", ".join(CSHORT[CAT14.index(w)] for w in warnings)
            warn_html = f'<div style="font-size:8pt;color:#E0A000;font-weight:600;margin-top:2px">⚠ Твоя слабая: {ws}</div>'
        author_cards += f'''<div class="card" style="margin-bottom:6px">
            <div class="card-title">{aname} <span style="font-size:9pt;color:{GRAY};font-weight:400">({atotal} вопр.)</span></div>
            <div class="card-body" style="font-size:9pt">{cats_line}</div>
            {warn_html}
        </div>\n'''

    # ── Assemble HTML ────────────────────────────────────────────────────
    # ── Strong / weak summary cards ──────────────────────────────────────
    strong_list = " · ".join(f"{CSHORT[CAT14.index(c)]} ({person['scores'][c]})" for c in expert) if expert else "—"
    weak_list = " · ".join(f"{CSHORT[CAT14.index(c)]} ({person['scores'][c]})" for c in weak) if weak else "—"
    avg_score = sum(person["scores_list"]) / len(person["scores_list"])
    total_gset = sum(len(ents_for(gs, c)) for c in expert + weak)

    summary_cards = f'''
        <div class="card card-gold" style="margin-bottom:6px">
            <div class="card-title">Сильные категории ({len(expert)})</div>
            <div class="card-body">{strong_list}</div>
        </div>
        <div class="card" style="margin-bottom:6px">
            <div class="card-title">Зоны роста ({len(weak)})</div>
            <div class="card-body">{weak_list}</div>
        </div>
        <div style="display:flex;gap:10px;margin:6px 0">
            <div class="card" style="flex:1;text-align:center;padding:10px">
                <div style="font-size:22pt;font-weight:700;color:{GOLD}">{avg_score:.1f}</div>
                <div style="font-size:8pt;color:{GRAY}">СРЕДНИЙ БАЛЛ</div>
            </div>
            <div class="card" style="flex:1;text-align:center;padding:10px">
                <div style="font-size:22pt;font-weight:700;color:{GOLD}">{total_gset}</div>
                <div style="font-size:8pt;color:{GRAY}">СУЩНОСТЕЙ<br>В ОТЧЁТЕ</div>
            </div>
        </div>
    '''

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><style>{CSS}</style></head>
<body>

<!-- TITLE PAGE -->
<div class="title-page">
    <div class="title-card">
        <h1>ПЕРСОНАЛЬНЫЙ</h1>
        <h1 class="gold">ОТЧЁТ</h1>
        <div class="divider"></div>
        <div class="name">{name.upper()}</div>
        <div class="role">{person["role"]}</div>
        <div class="kpi-row">
            <div class="kpi-box"><div class="val">{len(expert)}</div><div class="label">СИЛЬНЫХ<br>КАТЕГОРИЙ</div></div>
            <div class="kpi-box"><div class="val">{len(weak)}</div><div class="label">СЛАБЫХ<br>КАТЕГОРИЙ</div></div>
            <div class="kpi-box"><div class="val">{max(person["scores"].values())}</div><div class="label">МАКС.<br>ОЦЕНКА</div></div>
        </div>
        <div class="title-footer">IQ ПФО · Саранск 2026<br>На основе анализа 18 000+ классифицированных вопросов ЧГК</div>
    </div>
</div>

<!-- 01 OVERVIEW — radar + AI recommendation -->
<div class="page">
    <div class="sec-header">
        <div class="num">01</div>
        <h2>ОБЩИЙ ОБЗОР</h2>
        <div class="bar"></div>
    </div>
    <div class="two-col">
        <div class="col">
            <div class="chart"><img src="{radar_b64}" style="height:360px"></div>
        </div>
        <div class="col" style="padding-top:10px">
            {summary_cards}
            {"<div class='card card-gold'><div class='card-title'>Персональная рекомендация</div><div class='card-body'>" + ai_rec + "</div></div>" if ai_rec else ""}
        </div>
    </div>
</div>

<!-- 02 CLASSIFICATION OVERVIEW -->
<div class="page">
    <div class="sec-header">
        <div class="num">02</div>
        <h2>ОБЗОР КЛАССИФИКАЦИИ</h2>
        <div class="bar"></div>
    </div>
    <div class="card" style="margin-bottom:8px">
        <div class="card-body">Распределение 22 000+ классифицированных вопросов ЧГК по 14 категориям. Чем больше доля — тем чаще эта тема встречается на играх.</div>
    </div>
    <div class="chart"><img src="{overview_b64}" style="max-width:95%"></div>
</div>

<!-- 03 GENTLEMAN SET -->
<div class="page">
    <div class="sec-header">
        <div class="num">03</div>
        <h2>ДЖЕНТЛЬМЕНСКИЙ НАБОР</h2>
        <div class="bar"></div>
    </div>
    <div class="card" style="margin-bottom:8px">
        <div class="card-body">Самые частые ответы в ЧГК по твоим категориям. Ч. — количество вопросов, где эта сущность встречалась.</div>
    </div>
    <div style="column-count:2;column-gap:16px">
    {gset_sections}
    </div>
</div>

<!-- 04 DEVELOPMENT -->
<div class="page">
    <div class="sec-header">
        <div class="num">04</div>
        <h2>ОБЛАСТИ ДЛЯ РАЗВИТИЯ</h2>
        <div class="bar"></div>
    </div>
    {dev_blocks}
</div>

<!-- 05 IQ PFO -->
<div class="page">
    <div class="sec-header">
        <div class="num">05</div>
        <h2>IQ ПФО — СРАВНЕНИЕ С ГЛОБАЛЬНЫМ</h2>
        <div class="bar"></div>
    </div>
    <div class="card" style="margin-bottom:8px">
        <div class="card-body">Распределение категорий у авторов турнира (золотой) vs глобальное (серый).</div>
    </div>
    <div class="chart" style="margin-top:10px"><img src="{comp_b64}" style="max-width:95%"></div>
</div>

<!-- 06 AUTHOR PROFILES -->
<div class="page">
    <div class="sec-header">
        <div class="num">06</div>
        <h2>ПРОФИЛИ АВТОРОВ IQ ПФО</h2>
        <div class="bar"></div>
    </div>
    <div class="card" style="margin-bottom:8px">
        <div class="card-body">Топ-3 категории каждого автора турнира. ⚠ — категория совпадает с твоей слабой областью.</div>
    </div>
    <div style="column-count:2;column-gap:16px">
    {author_cards}
    </div>
    <div class="footer">
        Сгенерировано на основе 22 000+ классифицированных вопросов ЧГК,
        3 333 вопросов от авторов IQ ПФО.
    </div>
</div>

</body></html>"""

    return html


# ══════════════════════════════════════════════════════════════════════════════
# PDF via Playwright (headless Chromium)
# ══════════════════════════════════════════════════════════════════════════════

async def html_to_pdf(html_path: Path, pdf_path: Path):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(html_path.as_uri())
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            landscape=True,
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        await browser.close()


async def batch_html_to_pdf(pairs: list[tuple[Path, Path]]):
    """Convert multiple HTML→PDF reusing one browser instance."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for html_path, pdf_path in pairs:
            page = await browser.new_page()
            await page.goto(html_path.as_uri())
            await page.pdf(
                path=str(pdf_path),
                format="A4",
                landscape=True,
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            await page.close()
            print(f"    PDF → {pdf_path.name}")
        await browser.close()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)

    print("Загрузка данных...")
    people = load_survey()
    gs = load_gset()
    conn = sqlite3.connect(str(DB))
    gd = cat_dist(conn)
    iq = iq_dist(conn)

    print(f"Участников: {len(people)}")
    print("Генерация HTML...")
    pairs = []
    for p in people:
        name = p["name"]
        safe = name.replace(" ", "_")
        print(f"  → {name}...", end=" ")
        try:
            html = build_html(p, people, gs, conn, gd, iq)
            html_path = OUT / f"{safe}_report.html"
            html_path.write_text(html, encoding="utf-8")
            pdf_path = OUT / f"{safe}_report.pdf"
            pairs.append((html_path, pdf_path))
            print("✓")
        except Exception as e:
            print(f"ОШИБКА: {e}")
            import traceback; traceback.print_exc()

    conn.close()

    print(f"\nHTML готов. Конвертация в PDF через Chromium...")
    asyncio.run(batch_html_to_pdf(pairs))

    print(f"\nГотово! → {OUT}")


if __name__ == "__main__":
    main()
