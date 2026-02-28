"""Groq LLM-провайдер (облачный API)."""

from typing import Optional

from classifier.providers.base import BaseLLMProvider, ProviderConfig


class GroqProvider(BaseLLMProvider):
    """Провайдер для Groq Cloud API."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from groq import Groq

            if not self.config.api_key:
                raise RuntimeError(
                    "GROQ_API_KEY не задан. Укажите в .env или --api-key"
                )
            self._client = Groq(api_key=self.config.api_key)
        return self._client

    def _chat_impl(self, messages: list, max_tokens: int) -> Optional[str]:
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=self.config.temperature,
            max_tokens=max_tokens,
        )
        content = resp.choices[0].message.content.strip()

        if resp.usage:
            self._track_tokens(resp.usage.prompt_tokens, resp.usage.completion_tokens)

        return content

    def is_available(self) -> bool:
        return bool(self.config.api_key)
