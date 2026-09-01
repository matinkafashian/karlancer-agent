"""Validated runtime configuration with safe submission defaults."""

from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunMode(StrEnum):
    """Operational safety level for the agent."""

    SHADOW = "shadow"
    ASSISTED = "assisted"
    AUTO = "auto"


class Settings(BaseSettings):
    """Application settings loaded from environment variables or ``.env``.

    A non-shadow mode is not sufficient to permit a real submission. The
    independent kill switch must also be enabled. Project-scoped approval in
    assisted mode is enforced later by the submitter.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    run_mode: RunMode = RunMode.SHADOW
    submission_enabled: bool = False
    daily_submission_limit: int = Field(default=30, ge=1, le=30)
    database_url: SecretStr
    telegram_bot_token: SecretStr
    telegram_chat_id: int
    browser_profile_dir: Path = Path("/data/browser-profile")

    @property
    def real_submission_allowed(self) -> bool:
        """Return whether global mode and kill switch permit a real submit."""

        return self.submission_enabled and self.run_mode is not RunMode.SHADOW
