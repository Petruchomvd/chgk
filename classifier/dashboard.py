"""Rich-дашборд для визуализации прогресса классификации ЧГК-вопросов."""

import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

# Принудительно UTF-8 для stdout (Windows cp1251 не поддерживает Unicode рамки)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich import box
from rich.console import Console, Group
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from database.seed_taxonomy import TAXONOMY
from classifier.taxonomy import get_label

# ─── Цвета для 14 категорий ─────────────────────────────────────────
CATEGORY_COLORS = {
    1: "red",
    2: "green",
    3: "blue",
    4: "yellow",
    5: "magenta",
    6: "cyan",
    7: "bright_red",
    8: "bright_green",
    9: "bright_blue",
    10: "bright_yellow",
    11: "bright_magenta",
    12: "bright_cyan",
    13: "dark_green",
    14: "orange3",
}

CATEGORY_NAMES = [cat[1] for cat in TAXONOMY]


class ClassificationDashboard:
    """Обновляемый Rich-дашборд для классификации ЧГК-вопросов.

    Использует Group вместо Layout — фиксированная высота, без растягивания.
    """

    MAX_RECENT = 5
    REFRESH_PER_SEC = 2

    def __init__(
        self,
        model: str,
        total: int,
        total_in_db: int = 0,
        method: str = "",
        twostage: bool = False,
        few_shot: bool = True,
        provider=None,
    ):
        self.model = model
        self.total = total
        self.total_in_db = total_in_db
        self.method = method
        self.twostage = twostage
        self.few_shot = few_shot
        self.provider = provider

        self.success = 0
        self.failed = 0
        self.processed = 0
        self.start_time = 0.0

        self.category_counts: dict[int, int] = {i: 0 for i in range(1, 15)}
        self.recent: deque[dict] = deque(maxlen=self.MAX_RECENT)

        self.total_classify_time = 0.0
        self.total_confidence = 0.0
        self.confidence_count = 0

        self._lock = threading.Lock()
        self.console = Console(force_terminal=True)
        self.live: Optional[Live] = None

    # ─── Публичный API ───────────────────────────────────────────

    def start(self) -> None:
        self.start_time = time.time()
        self.live = Live(
            self._build(),
            console=self.console,
            refresh_per_second=self.REFRESH_PER_SEC,
            screen=True,
        )
        self.live.start()

    def update(self, data: dict) -> None:
        with self._lock:
            topics = data.get("topics")
            saved = data.get("saved_topics", [])
            self.total_classify_time += data.get("classify_time", 0)

            if topics:
                self.success += 1
                for t in saved:
                    self.category_counts[t["cat"]] = self.category_counts.get(t["cat"], 0) + 1
                    self.total_confidence += t["conf"]
                    self.confidence_count += 1
            else:
                self.failed += 1

            self.processed = self.success + self.failed
            self.recent.append(data)

        if self.live:
            self.live.update(self._build())

    def finish(self, interrupted: bool = False) -> None:
        if self.live:
            self.live.stop()
            self.live = None
        self._print_final_summary(interrupted)

    # ─── Построение дашборда (Group, не Layout) ──────────────────

    def _build(self) -> Group:
        """Собрать дашборд как Group — фиксированная высота."""
        return Group(
            self._render_header(),
            self._render_progress(),
            self._render_main_table(),
            self._render_footer(),
        )

    def _render_header(self) -> Panel:
        mode = "двухэтапный" if self.twostage else "одноэтапный"
        provider_name = self.provider.config.name if self.provider else "ollama"
        header_text = Text.assemble(
            ("ЧГК Классификация", "bold white"),
            ("  ", ""),
            (f"[{provider_name}] ", "dim"),
            (self.model, "bold cyan"),
            ("  ", ""),
            (mode, "italic"),
        )
        return Panel(header_text, style="dark_blue", expand=True)

    def _render_progress(self) -> Panel:
        elapsed = time.time() - self.start_time if self.start_time else 0
        speed = self.processed / elapsed if elapsed > 0 else 0
        eta = (self.total - self.processed) / speed if speed > 0 else 0
        pct = self.processed / self.total if self.total > 0 else 0

        bar = ProgressBar(total=self.total, completed=self.processed, width=50)
        speed_str = f"{1/speed:.1f} с/в" if speed > 0 else "..."

        stats = Text.assemble(
            (f" {pct:.1%}", "bold"),
            (f"  {self.processed}/{self.total}", ""),
            ("  |  ", "dim"),
            (speed_str, "cyan"),
            ("  |  ", "dim"),
            ("Прошло: ", "dim"),
            (self._fmt_duration(elapsed), ""),
            ("  |  ", "dim"),
            ("ETA: ", "dim"),
            (self._fmt_duration(eta), "yellow bold"),
        )

        return Panel(Group(bar, stats), title="Прогресс", border_style="bright_blue")

    def _render_main_table(self) -> Table:
        """Категории и последние вопросы в одной таблице бок о бок."""
        outer = Table(show_header=False, box=None, padding=0, expand=True)
        outer.add_column("left", ratio=2)
        outer.add_column("right", ratio=3)

        outer.add_row(
            Panel(self._render_categories(), title="Категории", border_style="blue"),
            Panel(self._render_recent(), title="Последние вопросы", border_style="green"),
        )
        return outer

    def _render_categories(self) -> Table:
        table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
        table.add_column("cat", width=14, justify="right", no_wrap=True)
        table.add_column("bar", ratio=1)
        table.add_column("n", width=4, justify="right")

        max_count = max(self.category_counts.values()) or 1
        bar_w = 15

        for cat_num in range(1, 15):
            count = self.category_counts[cat_num]
            name = CATEGORY_NAMES[cat_num - 1]
            w = int((count / max_count) * bar_w) if max_count > 0 else 0
            color = CATEGORY_COLORS[cat_num]

            table.add_row(
                Text(name, style="bold"),
                Text("█" * w, style=color),
                Text(str(count), style="bold") if count > 0 else Text("·", style="dim"),
            )

        return table

    def _render_recent(self) -> Table:
        table = Table(
            show_header=True, box=box.SIMPLE_HEAD,
            padding=(0, 1), expand=True, show_edge=False,
        )
        table.add_column("", width=1)
        table.add_column("#", width=8, style="dim")
        table.add_column("ID", width=6, style="dim")
        table.add_column("t", width=5)
        table.add_column("Категория", ratio=2, no_wrap=True)
        table.add_column("Вопрос", ratio=2)

        for item in reversed(self.recent):
            topics = item.get("topics")
            saved = item.get("saved_topics", [])
            idx = item.get("index", 0) + 1
            q_id = item.get("question_id", "")
            t = item.get("classify_time", 0)

            if topics and saved:
                icon = Text("✓", style="green bold")
                parts = []
                for tp in saved:
                    label = get_label(tp["cat"], tp["sub"])
                    conf = tp["conf"]
                    c = "green" if conf >= 0.7 else "yellow" if conf >= 0.4 else "red"
                    parts.append(f"[{c}]{label}[/] ({conf:.0%})")
                cat_str = Text.from_markup(" | ".join(parts))
            else:
                icon = Text("✗", style="red bold")
                cat_str = Text("—", style="dim")

            table.add_row(
                icon,
                f"{idx}/{self.total}",
                str(q_id),
                f"{t:.0f}с",
                cat_str,
                Text(self._truncate(item.get("text", ""), 40), style="dim"),
            )

        return table

    def _render_footer(self) -> Text:
        avg_conf = self.total_confidence / self.confidence_count if self.confidence_count > 0 else 0
        avg_time = self.total_classify_time / self.processed if self.processed > 0 else 0

        parts = [
            (" ✅ ", ""),
            (str(self.success), "bold green"),
            ("  ❌ ", ""),
            (str(self.failed), "bold red"),
            ("  |  ", "dim"),
            ("Ср.уверенность: ", "dim"),
            (f"{avg_conf:.2f}", "bold"),
            ("  |  ", "dim"),
            ("Ср.время: ", "dim"),
            (f"{avg_time:.1f}с", "bold cyan"),
        ]

        # Стоимость (для платных провайдеров)
        if self.provider and self.provider.config.cost_per_1m_input > 0:
            cost = self.provider.estimated_cost
            parts.extend([
                ("  |  ", "dim"),
                ("Стоимость: ", "dim"),
                (f"${cost:.2f}", "bold yellow"),
            ])

        return Text.assemble(*parts)

    # ─── Финальный отчёт ─────────────────────────────────────────

    def _print_final_summary(self, interrupted: bool = False) -> None:
        elapsed = time.time() - self.start_time if self.start_time else 0
        avg_time = self.total_classify_time / self.processed if self.processed > 0 else 0
        avg_conf = self.total_confidence / self.confidence_count if self.confidence_count > 0 else 0

        status = "[yellow]ПРЕРВАНО[/]" if interrupted else "[green]ЗАВЕРШЕНО[/]"

        summary = Table(
            title=f"\n Итоги — {status}",
            box=box.ROUNDED, show_header=False,
            title_style="bold", padding=(0, 2),
        )
        summary.add_column("key", style="bold", width=20)
        summary.add_column("value")

        if self.provider:
            summary.add_row("Провайдер", f"[dim]{self.provider.config.name}[/]")
        summary.add_row("Модель", f"[cyan]{self.model}[/]")
        summary.add_row("Обработано", f"{self.processed} из {self.total}")
        summary.add_row("Успешно", f"[green]{self.success}[/]")
        summary.add_row("Неудачно", f"[red]{self.failed}[/]")
        summary.add_row("Время", self._fmt_duration(elapsed))
        summary.add_row("Скорость", f"{avg_time:.1f} сек/вопрос")
        summary.add_row("Ср. уверенность", f"{avg_conf:.2f}")
        if self.provider and self.provider.config.cost_per_1m_input > 0:
            cost = self.provider.estimated_cost
            summary.add_row("Стоимость", f"[yellow]${cost:.2f}[/]")

        self.console.print(summary)

        if any(self.category_counts.values()):
            top = sorted(self.category_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            top_table = Table(title=" Топ-5 категорий", box=box.SIMPLE, title_style="bold")
            top_table.add_column("Категория", width=25)
            top_table.add_column("Вопросов", justify="right")
            top_table.add_column("Доля", justify="right")

            for cat_num, count in top:
                if count == 0:
                    break
                name = CATEGORY_NAMES[cat_num - 1]
                color = CATEGORY_COLORS[cat_num]
                pct = count / self.processed * 100 if self.processed > 0 else 0
                top_table.add_row(f"[{color}]{name}[/]", str(count), f"{pct:.1f}%")
            self.console.print(top_table)

    # ─── Утилиты ─────────────────────────────────────────────────

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}ч {m}м"
        if m > 0:
            return f"{m}м {s}с"
        return f"{s}с"

    @staticmethod
    def _truncate(text: str, max_len: int = 60) -> str:
        text = text.replace("\n", " ").strip()
        return text if len(text) <= max_len else text[:max_len - 1] + "…"
