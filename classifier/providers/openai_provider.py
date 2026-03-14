"""OpenAI LLM-провайдер (GPT-4o-mini и др.)."""

from typing import Optional

from classifier.providers.base import BaseLLMProvider, ProviderConfig


class OpenAIProvider(BaseLLMProvider):
    """Провайдер для OpenAI API (GPT-4o-mini, GPT-4o и др.)."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            if not self.config.api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY не задан. Укажите в .env или --api-key"
                )
            kwargs = {"api_key": self.config.api_key}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _chat_impl(self, messages: list, max_tokens: int, json_mode: bool = True) -> Optional[str]:
        client = self._get_client()
        kwargs = dict(
            model=self.config.model,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=max_tokens,
        )
        if json_mode and self.config.supports_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        # Disable thinking mode for Qwen3 models (OpenRouter)
        if "qwen3" in self.config.model.lower() or "qwen-3" in self.config.model.lower():
            kwargs["extra_body"] = {"reasoning": {"effort": "none"}}
        resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content
        if content is None:
            raise RuntimeError("Model returned empty content (None)")
        content = content.strip()

        if resp.usage:
            self._track_tokens(resp.usage.prompt_tokens, resp.usage.completion_tokens)

        return content

    def is_available(self) -> bool:
        return bool(self.config.api_key)
