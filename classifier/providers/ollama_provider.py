"""Ollama LLM-провайдер (локальные модели)."""

from typing import Optional

import ollama

from classifier.providers.base import BaseLLMProvider, ProviderConfig


class OllamaProvider(BaseLLMProvider):
    """Провайдер для локальных моделей через Ollama."""

    def _chat_impl(self, messages: list, max_tokens: int, json_mode: bool = True) -> Optional[str]:
        response = ollama.chat(
            model=self.config.model,
            messages=messages,
            format="json",
            options={"temperature": self.config.temperature},
        )
        content = response["message"]["content"].strip()

        # Ollama возвращает данные о токенах
        input_tokens = response.get("prompt_eval_count", 0)
        output_tokens = response.get("eval_count", 0)
        self._track_tokens(input_tokens, output_tokens)

        return content

    def is_available(self) -> bool:
        try:
            models = ollama.list()
            available = [m.model for m in models.models]
            model_base = self.config.model.split(":")[0]
            return any(model_base in name for name in available)
        except Exception:
            return False
