"""Build the configured model adapter without leaking credentials."""

from dataclasses import dataclass

import httpx

from karlancer_agent.config import ModelProvider, Settings
from karlancer_agent.llm.base import LLMProvider
from karlancer_agent.llm.codex_cli import CodexCLIProvider
from karlancer_agent.llm.disabled import DisabledProvider
from karlancer_agent.llm.ollama import OllamaProvider
from karlancer_agent.llm.openai_compatible import OpenAICompatibleProvider


@dataclass(frozen=True, slots=True)
class ModelProviderBundle:
    provider: LLMProvider
    provider_name: str
    model_name: str


def build_model_provider(
    settings: Settings, client: httpx.AsyncClient
) -> ModelProviderBundle:
    if settings.model_provider is ModelProvider.DISABLED:
        return ModelProviderBundle(
            provider=DisabledProvider(),
            provider_name=ModelProvider.DISABLED.value,
            model_name="none",
        )

    assert settings.model_name is not None
    provider: LLMProvider
    if settings.model_provider is ModelProvider.CODEX_CLI:
        provider = CodexCLIProvider(
            model=settings.model_name,
            executable=settings.codex_executable,
            reasoning_effort=settings.codex_reasoning_effort,
            timeout_seconds=settings.codex_timeout_seconds,
        )
    elif settings.model_provider is ModelProvider.OLLAMA:
        assert settings.model_base_url is not None
        provider = OllamaProvider(
            base_url=settings.model_base_url,
            model=settings.model_name,
            client=client,
        )
    else:
        assert settings.model_base_url is not None
        assert settings.model_api_key is not None
        provider = OpenAICompatibleProvider(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key.get_secret_value(),
            model=settings.model_name,
            client=client,
        )
    return ModelProviderBundle(
        provider=provider,
        provider_name=settings.model_provider.value,
        model_name=settings.model_name,
    )
