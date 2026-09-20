import asyncio

import httpx

from karlancer_agent.config import Settings
from karlancer_agent.llm.codex_cli import CodexCLIProvider
from karlancer_agent.llm.disabled import DisabledProvider
from karlancer_agent.llm.factory import build_model_provider
from karlancer_agent.llm.ollama import OllamaProvider
from karlancer_agent.llm.openai_compatible import OpenAICompatibleProvider

BASE = {
    "database_url": "sqlite+aiosqlite:///:memory:",
    "telegram_bot_token": "secret",
    "telegram_chat_id": 1,
    "internal_api_key": "internal-secret-key",
    "approval_signing_key": "approval-signing-key-with-32-characters",
}


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **(BASE | overrides))


def test_disabled_model_provider_is_safe_default() -> None:
    bundle = build_model_provider(settings(), httpx.AsyncClient())

    assert isinstance(bundle.provider, DisabledProvider)
    assert bundle.provider_name == "disabled"
    assert bundle.model_name == "none"


def test_configured_model_provider_uses_runtime_settings() -> None:
    client = httpx.AsyncClient()
    try:
        hosted = build_model_provider(
            settings(
                model_provider="openai_compatible",
                model_base_url="https://api.example/v1",
                model_name="hosted-model",
                model_api_key="model-key",
            ),
            client,
        )
        local = build_model_provider(
            settings(
                model_provider="ollama",
                model_base_url="http://ollama:11434",
                model_name="local-model",
            ),
            client,
        )
    finally:
        asyncio.run(client.aclose())

    assert isinstance(hosted.provider, OpenAICompatibleProvider)
    assert hosted.provider_name == "openai_compatible"
    assert hosted.model_name == "hosted-model"
    assert isinstance(local.provider, OllamaProvider)
    assert local.provider_name == "ollama"
    assert local.model_name == "local-model"


def test_codex_cli_provider_uses_subscription_without_api_key() -> None:
    client = httpx.AsyncClient()
    try:
        bundle = build_model_provider(
            settings(
                model_provider="codex_cli",
                model_name="gpt-5.6-luna",
                codex_reasoning_effort="low",
            ),
            client,
        )
    finally:
        asyncio.run(client.aclose())

    assert isinstance(bundle.provider, CodexCLIProvider)
    assert bundle.provider_name == "codex_cli"
    assert bundle.model_name == "gpt-5.6-luna"
