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
        """Взять следующий ключ из цикла (thread-safe)."""
        with self._lock:
            self._current_key = next(self._key_cycle)
            return self._current_key

    def _chat_impl(self, messages: list, max_tokens: int) -> Optional[str]:
        import time
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

        # Round-robin: каждый запрос берёт следующий ключ сразу
        key = self._next_key()

        consecutive_429 = 0
        while True:
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
                if not is_rate_limit:
                    raise

                consecutive_429 += 1
                key_idx = self._keys.index(key) + 1

                if consecutive_429 < len(self._keys):
                    # Есть ещё ключи — пробуем следующий
                    key = self._next_key()
                    new_idx = self._keys.index(key) + 1
                    print(f"[google] 429 на ключе #{key_idx}, переключаюсь на ключ #{new_idx}")
                else:
                    # Все ключи исчерпаны — ждём минуту
                    print(f"[google] Все {len(self._keys)} ключей исчерпаны, жду 60с...")
                    time.sleep(60)
                    consecutive_429 = 0
                    key = self._next_key()

    def is_available(self) -> bool:
        return bool(self._keys)
