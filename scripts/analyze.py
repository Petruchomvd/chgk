"""Точка входа: аналитика ЧГК-вопросов."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from analytics.visualize import generate_all_charts
from analytics.report import generate_report

if __name__ == "__main__":
    generate_all_charts()
    print()
    generate_report()
