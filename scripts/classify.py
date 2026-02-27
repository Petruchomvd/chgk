"""Точка входа: классификация вопросов ЧГК."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from classifier.runner import run_classification

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Классификация вопросов ЧГК через LLM")
    parser.add_argument("--model", type=str, default=None, help="Модель (Ollama или Groq)")
    parser.add_argument("--limit", type=int, default=None, help="Макс. вопросов для классификации")
    parser.add_argument("--no-few-shot", action="store_true", help="Без few-shot примеров")
    parser.add_argument("--twostage", action="store_true", help="Двухэтапная классификация")
    parser.add_argument("--groq", action="store_true", help="Использовать Groq API вместо Ollama")
    parser.add_argument(
        "--groq-key", type=str, default=None,
        help="Groq API key (или env GROQ_API_KEY)",
    )
    parser.add_argument(
        "--tg-token", type=str, default=None,
        help="Telegram Bot Token (или env CHGK_TG_BOT_TOKEN)",
    )
    parser.add_argument(
        "--tg-chat", type=str, default=None,
        help="Telegram Chat ID (или env CHGK_TG_CHAT_ID)",
    )
    parser.add_argument(
        "--no-dashboard", action="store_true",
        help="Отключить Rich-дашборд (простой текстовый вывод)",
    )
    args = parser.parse_args()

    # Если переданы аргументы — записать в env
    if args.tg_token:
        os.environ["CHGK_TG_BOT_TOKEN"] = args.tg_token
    if args.tg_chat:
        os.environ["CHGK_TG_CHAT_ID"] = args.tg_chat
    if args.groq_key:
        os.environ["GROQ_API_KEY"] = args.groq_key

    run_classification(
        model=args.model,
        limit=args.limit,
        few_shot=not args.no_few_shot,
        twostage=args.twostage,
        use_dashboard=not args.no_dashboard,
        use_groq=args.groq,
    )
