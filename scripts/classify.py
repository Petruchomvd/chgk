"""Точка входа: классификация вопросов ЧГК."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Загрузить .env до импорта config
import config  # noqa: F401 — загрузит .env

from classifier.providers import AVAILABLE_PROVIDERS


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Классификация вопросов ЧГК через LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python scripts/classify.py --twostage
  python scripts/classify.py --provider openai --model gpt-4o-mini --workers 5
  python scripts/classify.py --provider google --estimate-cost
  python scripts/classify.py --status
        """,
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="ollama",
        choices=AVAILABLE_PROVIDERS,
        help="LLM-провайдер (default: ollama)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Модель (по умолчанию — из пресета провайдера)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Макс. вопросов для классификации",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Параллельные воркеры (default: 1)",
    )
    parser.add_argument(
        "--no-few-shot",
        action="store_true",
        help="Без few-shot примеров",
    )
    parser.add_argument(
        "--twostage",
        action="store_true",
        default=True,
        help="Двухэтапная классификация (default: True)",
    )
    parser.add_argument(
        "--onestage",
        action="store_true",
        help="Одноэтапная классификация",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API-ключ (переопределяет .env)",
    )
    parser.add_argument(
        "--estimate-cost",
        action="store_true",
        help="Показать оценку стоимости и выйти",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Показать текущий прогресс классификации",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Отключить Rich-дашборд",
    )
    parser.add_argument(
        "--author",
        type=str,
        default=None,
        help="Фильтр по автору пакета (подстрока, напр. 'Рождествин')",
    )
    parser.add_argument(
        "--compare-with",
        type=str,
        default=None,
        dest="compare_with",
        help="Классифицировать только вопросы, уже обработанные этой моделью (для сравнения)",
    )
    # Legacy-параметры (обратная совместимость)
    parser.add_argument("--groq", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--groq-key", type=str, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--tg-token", type=str, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--tg-chat", type=str, default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()

    # Telegram env
    if args.tg_token:
        os.environ["CHGK_TG_BOT_TOKEN"] = args.tg_token
    if args.tg_chat:
        os.environ["CHGK_TG_CHAT_ID"] = args.tg_chat

    # API key: аргумент CLI → env переменная провайдера
    if args.api_key:
        from classifier.providers import PROVIDER_PRESETS

        env_key = PROVIDER_PRESETS.get(args.provider, {}).get("env_key")
        if env_key:
            os.environ[env_key] = args.api_key
    if args.groq_key:
        os.environ["GROQ_API_KEY"] = args.groq_key

    # Legacy: --groq → --provider groq
    if args.groq:
        args.provider = "groq"

    # --status: показать прогресс и выйти
    if args.status:
        from classifier.runner import show_status

        show_status()
        return

    # Создаём провайдер
    from classifier.providers import create_provider

    provider = create_provider(
        args.provider,
        model=args.model,
        api_key=args.api_key,
    )

    # --estimate-cost: показать прогноз и выйти
    if args.estimate_cost:
        from classifier.runner import estimate_cost

        estimate_cost(provider, limit=args.limit)
        return

    # Классификация
    twostage = not args.onestage

    from classifier.runner import run_classification

    run_classification(
        provider=provider,
        limit=args.limit,
        few_shot=not args.no_few_shot,
        twostage=twostage,
        use_dashboard=not args.no_dashboard,
        workers=args.workers,
        author_filter=args.author,
        source_model=args.compare_with,
    )


if __name__ == "__main__":
    main()
