from pathlib import Path

import pytest
from pydantic import ValidationError

from karlancer_agent.config import ModelProvider, RunMode, Settings

BASE_SETTINGS = {
    "database_url": "postgresql+asyncpg://agent:secret@localhost:5432/karlancer",
    "telegram_bot_token": "123456:super-secret-token",
    "telegram_chat_id": 123456789,
    "internal_api_key": "internal-super-secret",
    "approval_signing_key": "approval-signing-key-with-32-characters",
}


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **(BASE_SETTINGS | overrides))


def test_defaults_are_safe() -> None:
    settings = make_settings()

    assert settings.run_mode is RunMode.SHADOW
    assert settings.submission_enabled is False
    assert settings.daily_submission_limit == 26
    assert settings.browser_profile_dir == Path("/data/browser-profile")
    assert settings.evidence_dir == Path("/data/evidence")
    assert settings.evidence_retention_days == 7
    assert settings.real_submission_allowed is False
    assert settings.model_provider is ModelProvider.DISABLED
    assert settings.browser_proxy is None


def test_browser_proxy_credentials_are_masked_and_scoped() -> None:
    settings = make_settings(
        browser_proxy_server="socks5://iran-egress.example:1080",
        browser_proxy_username="proxy-user",
        browser_proxy_password="proxy-password",
    )
    assert settings.browser_proxy == {
        "server": "socks5://iran-egress.example:1080",
        "username": "proxy-user",
        "password": "proxy-password",
    }
    assert "proxy-password" not in repr(settings)


def test_browser_proxy_credentials_require_server() -> None:
    with pytest.raises(ValidationError):
        make_settings(browser_proxy_password="proxy-password")


@pytest.mark.parametrize(
    "overrides",
    (
        {"model_provider": "openai_compatible"},
        {
            "model_provider": "openai_compatible",
            "model_base_url": "https://api.example.com/v1",
            "model_name": "example-model",
        },
        {"model_provider": "ollama"},
    ),
)
def test_enabled_model_provider_requires_its_connection_settings(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_openai_compatible_and_ollama_model_settings_are_validated() -> None:
    hosted = make_settings(
        model_provider="openai_compatible",
        model_base_url="https://api.example.com/v1",
        model_name="example-model",
        model_api_key="model-secret",
    )
    local = make_settings(
        model_provider="ollama",
        model_base_url="http://ollama:11434",
        model_name="qwen-model",
    )

    assert hosted.model_provider is ModelProvider.OPENAI_COMPATIBLE
    assert local.model_provider is ModelProvider.OLLAMA


def test_codex_cli_uses_chatgpt_subscription_without_api_connection_fields() -> None:
    settings = make_settings(
        model_provider="codex_cli",
        model_name="gpt-5.6-luna",
        codex_reasoning_effort="low",
    )

    assert settings.model_provider is ModelProvider.CODEX_CLI
    assert settings.model_api_key is None
    assert settings.model_base_url is None
    assert settings.codex_timeout_seconds == 120


@pytest.mark.parametrize(
    ("mode", "enabled", "expected"),
    [
        (RunMode.SHADOW, False, False),
        (RunMode.SHADOW, True, False),
        (RunMode.ASSISTED, False, False),
        (RunMode.ASSISTED, True, True),
        (RunMode.AUTO, False, False),
        (RunMode.AUTO, True, True),
    ],
)
def test_submission_requires_non_shadow_mode_and_kill_switch(
    mode: RunMode, enabled: bool, expected: bool
) -> None:
    settings = make_settings(run_mode=mode, submission_enabled=enabled)

    assert settings.real_submission_allowed is expected


def test_required_connections_cannot_be_omitted() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("limit", [0, -1, 31])
def test_daily_limit_must_remain_inside_safety_boundary(limit: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(daily_submission_limit=limit)


def test_secrets_are_masked_in_repr() -> None:
    settings = make_settings()
    rendered = repr(settings)

    assert "super-secret-token" not in rendered
    assert "agent:secret" not in rendered
    assert "internal-super-secret" not in rendered
    assert "approval-signing-key-with-32-characters" not in rendered


@pytest.mark.parametrize("key", ("", "too-short"))
def test_internal_api_key_must_not_be_weak(key: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(internal_api_key=key)


@pytest.mark.parametrize("key", ("", "too-short"))
def test_approval_signing_key_must_not_be_weak(key: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(approval_signing_key=key)


@pytest.mark.parametrize("days", (0, 31))
def test_evidence_retention_is_bounded(days: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(evidence_retention_days=days)


@pytest.mark.parametrize("limit", (0, 27, 100))
def test_daily_submission_limit_cannot_exceed_approved_cap(limit: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(daily_submission_limit=limit)
