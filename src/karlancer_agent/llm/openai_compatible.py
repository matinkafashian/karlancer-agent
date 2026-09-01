"""Structured adapter for OpenAI-compatible Chat Completions endpoints."""

from collections.abc import Mapping, Sequence

import httpx

from karlancer_agent.llm.base import (
    ProviderUnavailableError,
    StructuredLLMProvider,
    StructuredModel,
)


class OpenAICompatibleProvider(StructuredLLMProvider):
    """Use an API key and endpoint; interactive Codex usage is intentionally unsupported."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        super().__init__(model=model)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient()
        self._timeout = timeout_seconds

    async def _request(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        schema: type[StructuredModel],
    ) -> str:
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self.model,
                "messages": list(messages),
                "stream": False,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__.lower(),
                        "strict": True,
                        "schema": schema.model_json_schema(),
                    },
                },
            },
            timeout=self._timeout,
        )
        try:
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as error:
            raise ProviderUnavailableError(
                f"OpenAI-compatible endpoint returned HTTP {response.status_code}"
            ) from error
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError("incomplete OpenAI-compatible response") from error
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty OpenAI-compatible response")
        return content

