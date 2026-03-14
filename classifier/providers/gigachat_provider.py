"""GigaChat (Sber) LLM-провайдер."""

import re
import time
import uuid
from typing import Optional

import requests

from classifier.providers.base import BaseLLMProvider, ProviderConfig

# GigaChat использует самоподписанные сертификаты
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"


class GigaChatProvider(BaseLLMProvider):
    """Провайдер для GigaChat API (Sber)."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def _get_access_token(self) -> str:
        """Получить OAuth2 access token (кэшируется до истечения)."""
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        resp = requests.post(
            AUTH_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "RqUID": str(uuid.uuid4()),
                "Authorization": f"Basic {self.config.api_key}",
            },
            data={"scope": "GIGACHAT_API_PERS"},
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = data["expires_at"] / 1000  # ms -> s
        return self._access_token

    def _chat_impl(self, messages: list, max_tokens: int, json_mode: bool = True) -> Optional[str]:
        token = self._get_access_token()

        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens,
        }

        resp = requests.post(
            CHAT_URL,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json=payload,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]
        if content is None:
            raise RuntimeError("GigaChat returned empty content")
        content = content.strip()

        # GigaChat может обернуть JSON в markdown ```json...```
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if m:
            content = m.group(1)
        elif not content.startswith("{"):
            # Попробовать найти JSON объект в тексте
            m = re.search(r"\{[^{}]*\}", content)
            if m:
                content = m.group(0)

        usage = data.get("usage", {})
        if usage:
            self._track_tokens(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )

        return content

    def is_available(self) -> bool:
        return bool(self.config.api_key)
