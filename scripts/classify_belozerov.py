"""Классификация вопросов Белозёрова (обход проблем с ё в PowerShell)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: F401

from classifier.providers import create_provider
from classifier.runner import run_classification

provider = create_provider("openrouter", model="qwen/qwen-2.5-72b-instruct")

run_classification(
    provider=provider,
    limit=None,
    few_shot=True,
    twostage=True,
    use_dashboard=True,
    workers=1,
    author_filter="Белозёров",
    source_model=None,
)
