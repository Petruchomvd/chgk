"""Plotly-графики для Streamlit-дашборда ЧГК."""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Фиксированные цвета для 14 категорий
CATEGORY_COLORS = {
    "История": "#e6194b",
    "Литература": "#3cb44b",
    "Наука и технологии": "#4363d8",
    "География": "#f58231",
    "Искусство": "#911eb4",
    "Музыка": "#42d4f4",
    "Кино и театр": "#f032e6",
    "Спорт": "#bfef45",
    "Язык и лингвистика": "#fabed4",
    "Религия и мифология": "#469990",
    "Общество и политика": "#dcbeff",
    "Быт и повседневность": "#9A6324",
    "Природа и животные": "#800000",
    "Логика и wordplay": "#aaffc3",
}

_LAYOUT = dict(
    template="plotly_white",
    font=dict(family="Arial, sans-serif"),
    margin=dict(l=20, r=20, t=40, b=20),
)


def category_bar_chart(df: pd.DataFrame) -> go.Figure:
    """Горизонтальная гистограмма по категориям."""
    df = df.sort_values("count")
    fig = px.bar(
        df, x="count", y="category", orientation="h",
        color="category", color_discrete_map=CATEGORY_COLORS,
        text=df["pct"].apply(lambda x: f"{x}%"),
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(
        **_LAYOUT,
        showlegend=False,
        yaxis_title="",
        xaxis_title="Количество вопросов",
        title="Распределение по категориям",
        height=450,
    )
    fig.update_layout(margin=dict(l=20, r=60, t=40, b=20))
    return fig


def category_pie_chart(df: pd.DataFrame) -> go.Figure:
    """Donut-диаграмма категорий."""
    fig = px.pie(
        df, values="count", names="category",
        color="category", color_discrete_map=CATEGORY_COLORS,
        hole=0.4,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(
        **_LAYOUT,
        showlegend=False,
        title="Доли категорий",
        height=450,
    )
    return fig


def subcategory_bar_chart(df: pd.DataFrame) -> go.Figure:
    """Горизонтальная гистограмма подкатегорий."""
    if "category" in df.columns:
        # Группируем по категориям: сортируем по суммарному count категории,
        # внутри категории — по count подкатегории
        cat_totals = df.groupby("category")["count"].sum().rename("cat_total")
        df = df.merge(cat_totals, on="category")
        df = df.sort_values(["cat_total", "count"], ascending=[True, True])
        df = df.drop(columns="cat_total")
        label = df["category"] + " → " + df["subcategory"]
    else:
        df = df.sort_values("count")
        label = df["subcategory"]
    df = df.assign(label=label)

    fig = px.bar(
        df, x="count", y="label", orientation="h",
        text=df["pct"].apply(lambda x: f"{x}%"),
        color="category" if "category" in df.columns else None,
        color_discrete_map=CATEGORY_COLORS,
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        **_LAYOUT,
        showlegend=True if "category" in df.columns else False,
        yaxis_title="",
        xaxis_title="Количество вопросов",
        title="Подкатегории",
        height=max(350, len(df) * 28),
    )
    return fig


def trends_line_chart(df: pd.DataFrame, top_n: int = 6) -> go.Figure:
    """Линейный график трендов по месяцам."""
    top_cats = df.groupby("category")["count"].sum().nlargest(top_n).index.tolist()
    df = df[df["category"].isin(top_cats)]

    fig = px.line(
        df, x="month", y="count", color="category",
        color_discrete_map=CATEGORY_COLORS,
        markers=True,
    )
    fig.update_layout(
        **_LAYOUT,
        xaxis_title="Месяц",
        yaxis_title="Вопросов",
        title=f"Тренды ТОП-{top_n} категорий по месяцам",
        legend_title="Категория",
        height=400,
    )
    fig.update_xaxes(tickangle=45)
    return fig


def difficulty_bar_chart(df: pd.DataFrame) -> go.Figure:
    """Средняя сложность по категориям."""
    df = df.sort_values("avg_difficulty")
    fig = px.bar(
        df, x="avg_difficulty", y="category", orientation="h",
        text="avg_difficulty",
        color="avg_difficulty",
        color_continuous_scale="RdYlGn_r",
    )
    fig.update_traces(texttemplate="%{text:.2f}", textposition="outside")
    fig.update_layout(
        **_LAYOUT,
        showlegend=False,
        coloraxis_showscale=False,
        yaxis_title="",
        xaxis_title="Средняя сложность",
        title="Сложность пакетов по категориям",
        height=450,
    )
    return fig


# ─── Уверенность ──────────────────────────────────────────────────

def confidence_histogram(df: pd.DataFrame) -> go.Figure:
    """Гистограмма значений confidence."""
    fig = px.histogram(
        df, x="confidence", nbins=25,
        color_discrete_sequence=["#4363d8"],
    )
    fig.update_layout(
        **_LAYOUT,
        xaxis_title="Уверенность",
        yaxis_title="Количество",
        title="Распределение уверенности классификатора",
        height=400,
    )
    return fig


def confidence_box_by_category(df: pd.DataFrame) -> go.Figure:
    """Box-plot уверенности по категориям."""
    fig = px.box(
        df, x="confidence", y="category",
        color="category", color_discrete_map=CATEGORY_COLORS,
    )
    fig.update_layout(
        **_LAYOUT,
        showlegend=False,
        yaxis_title="",
        xaxis_title="Уверенность",
        title="Уверенность по категориям",
        height=500,
    )
    return fig


# ─── Сравнение моделей ────────────────────────────────────────────

def agreement_heatmap(
    matrix_data: list,
    categories: list,
    model_a: str,
    model_b: str,
) -> go.Figure:
    """Heatmap 14x14: матрица согласия двух моделей."""
    # Построить матрицу
    cat_idx = {c: i for i, c in enumerate(categories)}
    n = len(categories)
    matrix = np.zeros((n, n), dtype=int)

    for row in matrix_data:
        i = cat_idx.get(row["cat_a"])
        j = cat_idx.get(row["cat_b"])
        if i is not None and j is not None:
            matrix[i][j] = row["count"]

    fig = px.imshow(
        matrix,
        x=categories,
        y=categories,
        text_auto=True,
        color_continuous_scale="Blues",
        aspect="auto",
    )
    fig.update_layout(
        **_LAYOUT,
        xaxis_title=model_b,
        yaxis_title=model_a,
        title="Матрица согласия моделей",
        height=550,
    )
    fig.update_xaxes(tickangle=45)
    return fig


def model_confidence_comparison(df: pd.DataFrame, model_a: str, model_b: str) -> go.Figure:
    """Box-plot сравнения уверенности двух моделей."""
    df_a = df[["conf_a"]].rename(columns={"conf_a": "confidence"}).assign(model=model_a)
    df_b = df[["conf_b"]].rename(columns={"conf_b": "confidence"}).assign(model=model_b)
    combined = pd.concat([df_a, df_b], ignore_index=True)

    fig = px.box(combined, x="model", y="confidence", color="model")
    fig.update_layout(
        **_LAYOUT,
        showlegend=False,
        xaxis_title="",
        yaxis_title="Уверенность",
        title="Сравнение уверенности моделей",
        height=400,
    )
    return fig


def year_trends_heatmap(df: pd.DataFrame) -> go.Figure:
    """Heatmap: год × категория (интенсивность = количество вопросов)."""
    pivot = df.pivot_table(index="category", columns="year", values="count", fill_value=0)
    fig = px.imshow(
        pivot.values,
        x=pivot.columns.tolist(),
        y=pivot.index.tolist(),
        text_auto=True,
        color_continuous_scale="YlOrRd",
        aspect="auto",
    )
    fig.update_layout(
        **_LAYOUT,
        xaxis_title="Год",
        yaxis_title="",
        title="Тематический ландшафт по годам",
        height=500,
    )
    return fig


def growth_bar_chart(df: pd.DataFrame) -> go.Figure:
    """Горизонтальная гистограмма роста/падения категорий (delta %)."""
    df = df.sort_values("delta")
    colors = ["#e6194b" if d < 0 else "#3cb44b" for d in df["delta"]]
    fig = go.Figure(go.Bar(
        x=df["delta"],
        y=df["category"],
        orientation="h",
        marker_color=colors,
        text=df["delta"].apply(lambda x: f"{x:+.1f}%"),
        textposition="outside",
    ))
    fig.update_layout(
        **_LAYOUT,
        xaxis_title="Изменение доли (%)",
        yaxis_title="",
        title="Рост / падение категорий (год к году)",
        height=450,
    )
    return fig


def author_radar_chart(data: list, author: str) -> go.Figure:
    """Radar-chart тематического профиля автора."""
    categories = [d["category"] for d in data]
    counts = [d["count"] for d in data]
    # Замкнуть фигуру
    categories = categories + [categories[0]]
    counts = counts + [counts[0]]

    fig = go.Figure(go.Scatterpolar(
        r=counts,
        theta=categories,
        fill="toself",
        name=author,
        line_color="#4363d8",
    ))
    fig.update_layout(
        **_LAYOUT,
        polar=dict(radialaxis=dict(visible=True)),
        title=f"Тематический профиль: {author}",
        height=450,
    )
    return fig


def cohens_kappa(matrix: np.ndarray) -> float:
    """Cohen's Kappa из confusion-матрицы."""
    n = matrix.sum()
    if n == 0:
        return 0.0
    po = matrix.diagonal().sum() / n
    row_sums = matrix.sum(axis=1) / n
    col_sums = matrix.sum(axis=0) / n
    pe = (row_sums * col_sums).sum()
    if pe >= 1:
        return 1.0
    return (po - pe) / (1 - pe)
