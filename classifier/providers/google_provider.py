"""Google Gemini LLM-провайдер (Gemini 2.0 Flash и др.)."""

from typing import Optional

from classifier.providers.base import BaseLLMProvider, ProviderConfig


class GoogleProvider(BaseLLMProvider):
    """Провайдер для Google Gemini API.

    Нюансы:
    - Другой формат сообщений (parts, role='model' вместо 'assistant')
    - Поддерживает response_mime_type="application/json"
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._model = None

    def _get_model(self):
        if self._model is None:
            import google.generativeai as genai

            if not self.config.api_key:
                raise RuntimeError(
                    "GOOGLE_API_KEY не задан. Укажите в .env или --api-key"
                )
            genai.configure(api_key=self.config.api_key)

            # Извлекаем system instruction при первом вызове
            self._genai = genai
        return self._genai

    def _chat_impl(self, messages: list, max_tokens: int) -> Optional[str]:
        genai = self._get_model()

        # Извлечь system из сообщений
        system_text = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text += msg["content"] + "\n"
            else:
                role = "model" if msg["role"] == "assistant" else "user"
                chat_messages.append({"role": role, "parts": [msg["content"]]})

        model = genai.GenerativeModel(
            model_name=self.config.model,
            system_instruction=system_text.strip() if system_text else None,
            generation_config=genai.types.GenerationConfig(
                temperature=self.config.temperature,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
            ),
        )

        # Gemini chat: отправляем все сообщения кроме последнего как history
        if len(chat_messages) > 1:
            chat = model.start_chat(history=chat_messages[:-1])
            resp = chat.send_message(chat_messages[-1]["parts"][0])
        else:
            resp = model.generate_content(
                chat_messages[0]["parts"][0] if chat_messages else ""
            )

        content = resp.text.strip()

        # Подсчёт токенов
        if hasattr(resp, "usage_metadata") and resp.usage_metadata:
            self._track_tokens(
                getattr(resp.usage_metadata, "prompt_token_count", 0),
                getattr(resp.usage_metadata, "candidates_token_count", 0),
            )

        return content

    def is_available(self) -> bool:
        return bool(self.config.api_key)
