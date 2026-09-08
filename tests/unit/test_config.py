from pathlib import Path

import pytest
from pydantic import ValidationError

from karlancer_agent.config import RunMode, Settings

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
    assert settings.daily_submission_limit == 30
    assert settings.browser_profile_dir == Path("/data/browser-profile")
    assert settings.evidence_dir == Path("/data/evidence")
    assert settings.evidence_retention_days == 7
    assert settings.real_submission_allowed is False


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
