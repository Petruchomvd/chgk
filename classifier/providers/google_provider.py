"""Google Gemini LLM-провайдер (google-genai SDK)."""

import itertools
import threading
import time
from typing import Optional

from classifier.providers.base import BaseLLMProvider, ProviderConfig


class _PerKeyTracker:
    """Per-key RPM трекер: отслеживает запросы по каждому ключу.

    Проактивно ждёт перед запросом, если ключ исчерпал RPM-лимит.
    Это ПРЕДОТВРАЩАЕТ 429-ошибки вместо того, чтобы их обрабатывать,
    и не тратит RPD-квоту на неудачные retry.
    """

    def __init__(self, keys: list, rpm_per_key: int = 7):
        self._rpm = rpm_per_key
        self._window = 60.0  # секунд
        self._lock = threading.Lock()
        self._requests: dict = {k: [] for k in keys}

    def wait_and_record(self, key: str):
        """Подождать если нужно, затем записать запрос."""
        while True:
            with self._lock:
                now = time.time()
                cutoff = now - self._window
                self._requests[key] = [t for t in self._requests[key] if t > cutoff]

                if len(self._requests[key]) < self._rpm:
                    self._requests[key].append(now)
                    return

                # Ждём пока самый старый запрос выйдет из окна
                wait = self._requests[key][0] + self._window - now + 1.0

            print(f"[google] Ключ #{key[:8]}... лимитирован, жду {wait:.0f}с...")
            time.sleep(max(wait, 1.0))

    def find_best_key(self, keys: list) -> str:
        """Найти ключ с наименьшим временем ожидания."""
        with self._lock:
            now = time.time()
            cutoff = now - self._window

            # Сначала ищем ключ, у которого есть свободная ёмкость
            for key in keys:
                self._requests[key] = [t for t in self._requests[key] if t > cutoff]
                if len(self._requests[key]) < self._rpm:
                    return key

            # Все ключи заняты — найти тот, который освободится раньше
            best_key = keys[0]
            best_wait = float("inf")
            for key in keys:
                if self._requests[key]:
                    wait = self._requests[key][0] + self._window - now
                    if wait < best_wait:
                        best_wait = wait
                        best_key = key
            return best_key


class GoogleProvider(BaseLLMProvider):
    """Провайдер для Google Gemini API (google-genai SDK).

    - Per-key RPM-трекер: проактивно ждёт вместо получения 429.
    - Ротация ключей round-robin с выбором наименее нагруженного.
    - Отключает thinking для gemini-2.5+.
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        from google import genai

        keys = config.api_keys if config.api_keys else ([config.api_key] if config.api_key else [])
        if not keys:
            raise RuntimeError("GOOGLE_API_KEY(S) не заданы. Укажите в .env")
        self._keys = keys
        self._key_idx = 0
        self._lock = threading.Lock()
        # Клиенты по ключу (переиспользуются)
        self._clients = {key: genai.Client(api_key=key) for key in keys}
        # Per-key RPM трекер: 7 RPM/ключ (запас 30% от лимита 10 RPM)
        self._tracker = _PerKeyTracker(keys, rpm_per_key=7)

    def _next_key(self) -> str:
        """Взять следующий ключ round-robin (thread-safe)."""
        with self._lock:
            self._key_idx = (self._key_idx + 1) % len(self._keys)
            return self._keys[self._key_idx]

    def _chat_impl(self, messages: list, max_tokens: int) -> Optional[str]:
        from google.genai import types

        # Извлечь system и собрать contents
        system_text = ""
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                system_text += msg["content"] + "\n"
            else:
                role = "model" if msg["role"] == "assistant" else "user"
                contents.append(
                    types.Content(role=role, parts=[types.Part(text=msg["content"])])
                )

        # Отключить thinking для gemini-2.5+ (кроме lite — у них нет thinking)
        thinking_config = None
        model_name = self.config.model.lower()
        if any(v in model_name for v in ("2.5", "3.", "3-")) and "lite" not in model_name:
            thinking_config = types.ThinkingConfig(thinking_budget=0)

        gen_config = types.GenerateContentConfig(
            temperature=self.config.temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            system_instruction=system_text.strip() if system_text else None,
            thinking_config=thinking_config,
        )

        # Выбрать наименее нагруженный ключ
        key = self._tracker.find_best_key(self._keys)

        max_429_retries = 3  # Макс попыток при неожиданных 429
        for attempt in range(max_429_retries):
            try:
                # Подождать пока ключ освободится по RPM
                self._tracker.wait_and_record(key)

                client = self._clients[key]
                resp = client.models.generate_content(
                    model=self.config.model,
                    contents=contents,
                    config=gen_config,
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

                key_short = key[:8]
                if attempt < max_429_retries - 1:
                    # 429 несмотря на трекер — подождать и сменить ключ
                    wait = 30
                    print(f"[google] 429 на {key_short}... (попытка {attempt+1}), жду {wait}с...")
                    time.sleep(wait)
                    key = self._next_key()
                else:
                    # Все retry исчерпаны — пробросить наверх для base retry
                    print(f"[google] 429 на всех попытках, пробрасываю ошибку")
                    raise

    def is_available(self) -> bool:
        return bool(self._keys)
