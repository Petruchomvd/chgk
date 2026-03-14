"""Anthropic LLM-провайдер (Claude Haiku, Sonnet и др.)."""

from typing import Optional

from classifier.providers.base import BaseLLMProvider, ProviderConfig


class AnthropicProvider(BaseLLMProvider):
    """Провайдер для Anthropic API.

    Нюансы:
    - system передаётся отдельным параметром, а не как сообщение
    - Нет JSON mode → используем prefill (начинаем ответ с '{')
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic

            if not self.config.api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY не задан. Укажите в .env или --api-key"
                )
            self._client = Anthropic(api_key=self.config.api_key)
        return self._client

    def _chat_impl(self, messages: list, max_tokens: int, json_mode: bool = True) -> Optional[str]:
        client = self._get_client()

        # Извлечь system из сообщений
        system_text = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text += msg["content"] + "\n"
            else:
                chat_messages.append(msg)

        # Prefill: начинаем ответ ассистента с '{' для JSON
        chat_messages.append({"role": "assistant", "content": "{"})

        resp = client.messages.create(
            model=self.config.model,
            system=system_text.strip(),
            messages=chat_messages,
            max_tokens=max_tokens,
            temperature=self.config.temperature,
        )
        content = resp.content[0].text.strip()
        # Prefill: добавляем '{' обратно, если модель его не вернула
        if not content.startswith("{"):
            content = "{" + content

        if resp.usage:
            self._track_tokens(resp.usage.input_tokens, resp.usage.output_tokens)

        return content

    def is_available(self) -> bool:
        return bool(self.config.api_key)
