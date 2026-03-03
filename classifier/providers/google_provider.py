"""Google Gemini LLM-провайдер (Gemini 2.0 Flash и др.)."""

import itertools
import threading
from typing import Optional

from classifier.providers.base import BaseLLMProvider, ProviderConfig


class GoogleProvider(BaseLLMProvider):
    """Провайдер для Google Gemini API.

    Нюансы:
    - Другой формат сообщений (parts, role='model' вместо 'assistant')
    - Поддерживает response_mime_type="application/json"
    - Поддерживает ротацию нескольких API-ключей (GOOGLE_API_KEYS)
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        # Список ключей: api_keys имеет приоритет, иначе api_key
        keys = config.api_keys if config.api_keys else ([config.api_key] if config.api_key else [])
        if not keys:
            raise RuntimeError("GOOGLE_API_KEY(S) не заданы. Укажите в .env")
        self._keys = keys
        self._key_cycle = itertools.cycle(keys)
        self._current_key = next(self._key_cycle)
        self._lock = threading.Lock()
        self._genai = None

    def _next_key(self) -> str:
        with self._lock:
            self._current_key = next(self._key_cycle)
            return self._current_key

    def _get_genai(self):
        import google.generativeai as genai
        self._genai = genai
        return genai

    def _chat_impl(self, messages: list, max_tokens: int) -> Optional[str]:
        import google.generativeai as genai

        # Извлечь system из сообщений
        system_text = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text += msg["content"] + "\n"
            else:
                role = "model" if msg["role"] == "assistant" else "user"
                chat_messages.append({"role": role, "parts": [msg["content"]]})

        # Пробуем все ключи по кругу при 429
        for attempt in range(len(self._keys) + 1):
            key = self._current_key
            try:
                genai.configure(api_key=key)
                model = genai.GenerativeModel(
                    model_name=self.config.model,
                    system_instruction=system_text.strip() if system_text else None,
                    generation_config=genai.types.GenerationConfig(
                        temperature=self.config.temperature,
                        max_output_tokens=max_tokens,
                        response_mime_type="application/json",
                    ),
                )

                if len(chat_messages) > 1:
                    chat = model.start_chat(history=chat_messages[:-1])
                    resp = chat.send_message(chat_messages[-1]["parts"][0])
                else:
                    resp = model.generate_content(
                        chat_messages[0]["parts"][0] if chat_messages else ""
                    )

                content = resp.text.strip()

                if hasattr(resp, "usage_metadata") and resp.usage_metadata:
                    self._track_tokens(
                        getattr(resp.usage_metadata, "prompt_token_count", 0),
                        getattr(resp.usage_metadata, "candidates_token_count", 0),
                    )
                return content

            except Exception as e:
                err_str = str(e).lower()
                is_rate_limit = "429" in err_str or "resource exhausted" in err_str or "quota" in err_str
                if is_rate_limit and len(self._keys) > 1 and attempt < len(self._keys):
                    new_key = self._next_key()
                    key_idx = self._keys.index(new_key) + 1
                    print(f"[google] 429 на ключе #{self._keys.index(key) + 1}, переключаюсь на ключ #{key_idx}")
                    continue
                raise

        return None

    def is_available(self) -> bool:
        return bool(self._keys)
