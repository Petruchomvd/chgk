"""Абстрактный базовый класс для всех LLM-провайдеров."""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ProviderConfig:
    """Конфигурация LLM-провайдера."""

    name: str
    model: str
    api_key: str = ""
    api_keys: List[str] = field(default_factory=list)  # для ротации ключей
    base_url: str = ""
    rate_limit_delay: float = 0.0
    max_concurrent: int = 1
    max_tokens: int = 100
    temperature: float = 0.1
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0
    supports_json_mode: bool = True
    retry_delays: List[float] = field(default_factory=lambda: [2, 5, 15])


class BaseLLMProvider(ABC):
    """Абстрактный LLM-провайдер с retry-логикой и подсчётом стоимости."""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._request_count = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._last_request_time = 0.0

    @abstractmethod
    def _chat_impl(self, messages: list, max_tokens: int, json_mode: bool = True) -> Optional[str]:
        """Отправить запрос к API. Возвращает текст ответа или None."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Проверить доступность провайдера (ключ задан, сервер отвечает)."""
        ...

    def chat(self, messages: list, max_tokens: int = None, json_mode: bool = True) -> Optional[str]:
        """Отправить запрос с retry-логикой и rate limiting.

        Args:
            json_mode: Если True — запрашивать JSON-ответ. Если False — свободный текст.
        """
        if max_tokens is None:
            max_tokens = self.config.max_tokens

        # Rate limiting
        if self.config.rate_limit_delay > 0 and self._last_request_time > 0:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.config.rate_limit_delay:
                time.sleep(self.config.rate_limit_delay - elapsed)

        result = self._chat_with_retry(messages, max_tokens, json_mode)
        self._last_request_time = time.time()
        self._request_count += 1
        return result

    def _chat_with_retry(self, messages: list, max_tokens: int, json_mode: bool = True) -> Optional[str]:
        """Retry-обёртка для _chat_impl."""
        delays = self.config.retry_delays
        for attempt in range(len(delays) + 1):
            try:
                return self._chat_impl(messages, max_tokens, json_mode)
            except Exception as e:
                if attempt < len(delays):
                    delay = delays[attempt]
                    print(
                        f"[{self.config.name}] error (attempt {attempt + 1}): {e}. "
                        f"Retry in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    print(f"[{self.config.name}] error (all retries exhausted): {e}")
                    return None
        return None

    def _track_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Обновить счётчики токенов."""
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens

    @property
    def estimated_cost(self) -> float:
        """Текущая стоимость на основе подсчитанных токенов."""
        input_cost = (
            self._total_input_tokens * self.config.cost_per_1m_input / 1_000_000
        )
        output_cost = (
            self._total_output_tokens * self.config.cost_per_1m_output / 1_000_000
        )
        return input_cost + output_cost

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def request_count(self) -> int:
        return self._request_count

    def estimate_total_cost(
        self,
        question_count: int,
        avg_input_tokens: int = 1600,
        avg_output_tokens: int = 40,
    ) -> float:
        """Прогноз стоимости для N вопросов."""
        input_cost = (
            question_count
            * avg_input_tokens
            * self.config.cost_per_1m_input
            / 1_000_000
        )
        output_cost = (
            question_count
            * avg_output_tokens
            * self.config.cost_per_1m_output
            / 1_000_000
        )
        return input_cost + output_cost

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.config.model!r})"
