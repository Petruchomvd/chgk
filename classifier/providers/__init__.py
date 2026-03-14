"""Фабрика LLM-провайдеров для классификации ЧГК-вопросов.

Использование:
    from classifier.providers import create_provider

    provider = create_provider("openai")
    provider = create_provider("ollama", model="qwen2.5:14b-instruct-q4_K_M")
    provider = create_provider("google", api_key="...", model="gemini-2.0-flash")
"""

import os
from typing import Optional

from classifier.providers.base import BaseLLMProvider, ProviderConfig

# Ленивый импорт провайдеров — чтобы не требовать все SDK сразу
_PROVIDER_CLASSES = {
    "ollama": "classifier.providers.ollama_provider:OllamaProvider",
    "groq": "classifier.providers.groq_provider:GroqProvider",
    "openai": "classifier.providers.openai_provider:OpenAIProvider",
    "anthropic": "classifier.providers.anthropic_provider:AnthropicProvider",
    "google": "classifier.providers.google_provider:GoogleProvider",
    "openrouter": "classifier.providers.openai_provider:OpenAIProvider",
    "gigachat": "classifier.providers.gigachat_provider:GigaChatProvider",
}

# Пресеты по умолчанию для каждого провайдера
PROVIDER_PRESETS = {
    "ollama": {
        "default_model": "qwen2.5:14b-instruct-q4_K_M",
        "cost_per_1m_input": 0.0,
        "cost_per_1m_output": 0.0,
        "max_concurrent": 2,
        "rate_limit_delay": 0.0,
        "env_key": "",
        "supports_json_mode": True,
    },
    "groq": {
        "default_model": "llama-3.3-70b-versatile",
        "cost_per_1m_input": 0.0,
        "cost_per_1m_output": 0.0,
        "max_concurrent": 1,
        "rate_limit_delay": 3.0,
        "env_key": "GROQ_API_KEY",
        "supports_json_mode": True,
    },
    "openai": {
        "default_model": "gpt-4o-mini",
        "cost_per_1m_input": 0.15,
        "cost_per_1m_output": 0.60,
        "max_concurrent": 5,
        "rate_limit_delay": 0.0,
        "env_key": "OPENAI_API_KEY",
        "supports_json_mode": True,
    },
    "anthropic": {
        "default_model": "claude-haiku-4-5-20251001",
        "cost_per_1m_input": 1.00,
        "cost_per_1m_output": 5.00,
        "max_concurrent": 5,
        "rate_limit_delay": 0.0,
        "env_key": "ANTHROPIC_API_KEY",
        "supports_json_mode": False,
    },
    "google": {
        "default_model": "gemini-2.0-flash",
        "cost_per_1m_input": 0.10,
        "cost_per_1m_output": 0.40,
        "max_concurrent": 10,
        "rate_limit_delay": 0.0,
        "env_key": "GOOGLE_API_KEY",
        "supports_json_mode": True,
    },
    "openrouter": {
        "default_model": "google/gemini-2.5-flash",
        "cost_per_1m_input": 0.15,
        "cost_per_1m_output": 0.60,
        "max_concurrent": 5,
        "rate_limit_delay": 0.0,
        "env_key": "OPENROUTER_API_KEY",
        "supports_json_mode": True,
        "base_url": "https://openrouter.ai/api/v1",
    },
    "gigachat": {
        "default_model": "GigaChat",
        "cost_per_1m_input": 0.0,
        "cost_per_1m_output": 0.0,
        "max_concurrent": 1,
        "rate_limit_delay": 1.0,
        "env_key": "GIGACHAT_AUTH",
        "supports_json_mode": False,
    },
}

AVAILABLE_PROVIDERS = list(PROVIDER_PRESETS.keys())


def _import_class(dotted_path: str):
    """Импортировать класс по пути 'module:ClassName'."""
    module_path, class_name = dotted_path.rsplit(":", 1)
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def create_provider(
    provider_name: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.1,
) -> BaseLLMProvider:
    """Создать провайдер по имени.

    Args:
        provider_name: "ollama", "groq", "openai", "anthropic", "google"
        model: Имя модели (по умолчанию — из пресета)
        api_key: API-ключ (по умолчанию — из .env)
        temperature: Температура генерации

    Returns:
        Экземпляр BaseLLMProvider
    """
    if provider_name not in PROVIDER_PRESETS:
        raise ValueError(
            f"Неизвестный провайдер: {provider_name!r}. "
            f"Доступные: {', '.join(AVAILABLE_PROVIDERS)}"
        )

    preset = PROVIDER_PRESETS[provider_name]

    # Ключ(и) API: аргумент > переменная окружения
    api_keys = []
    if api_key:
        # Один ключ передан явно (может быть comma-separated)
        api_keys = [k.strip() for k in api_key.split(",") if k.strip()]
    elif preset["env_key"]:
        # Сначала ищем GOOGLE_API_KEYS (множество), потом GOOGLE_API_KEY
        multi_key_env = preset["env_key"] + "S"  # GOOGLE_API_KEYS
        raw = os.environ.get(multi_key_env, "") or os.environ.get(preset["env_key"], "")
        api_keys = [k.strip() for k in raw.split(",") if k.strip()]

    config = ProviderConfig(
        name=provider_name,
        model=model or preset["default_model"],
        api_key=api_keys[0] if api_keys else "",
        api_keys=api_keys,
        base_url=preset.get("base_url", ""),
        rate_limit_delay=preset["rate_limit_delay"],
        max_concurrent=preset["max_concurrent"],
        temperature=temperature,
        cost_per_1m_input=preset["cost_per_1m_input"],
        cost_per_1m_output=preset["cost_per_1m_output"],
        supports_json_mode=preset["supports_json_mode"],
    )

    cls = _import_class(_PROVIDER_CLASSES[provider_name])
    return cls(config)


def list_providers() -> list:
    """Список доступных провайдеров с информацией."""
    result = []
    for name, preset in PROVIDER_PRESETS.items():
        env_key = preset["env_key"]
        has_key = bool(os.environ.get(env_key, "")) if env_key else True
        result.append(
            {
                "name": name,
                "default_model": preset["default_model"],
                "cost_input": preset["cost_per_1m_input"],
                "cost_output": preset["cost_per_1m_output"],
                "max_concurrent": preset["max_concurrent"],
                "configured": has_key,
            }
        )
    return result
