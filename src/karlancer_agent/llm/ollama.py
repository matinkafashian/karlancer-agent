"""Structured adapter for a local or private Ollama server."""

from collections.abc import Mapping, Sequence

import httpx

from karlancer_agent.llm.base import (
    ProviderUnavailableError,
    StructuredLLMProvider,
    StructuredModel,
)


class OllamaProvider(StructuredLLMProvider):
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60,
    ) -> None:
        super().__init__(model=model)
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._timeout = timeout_seconds

    async def _request(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        schema: type[StructuredModel],
    ) -> str:
        response = await self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self.model,
                "messages": list(messages),
                "stream": False,
                "format": schema.model_json_schema(),
            },
            timeout=self._timeout,
        )
        try:
            response.raise_for_status()
            payload = response.json()
            if payload.get("done") is not True:
                raise ValueError("incomplete Ollama response")
            content = payload["message"]["content"]
        except httpx.HTTPStatusError as error:
            raise ProviderUnavailableError(
                f"Ollama endpoint returned HTTP {response.status_code}"
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("incomplete Ollama response") from error
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty Ollama response")
        return content

