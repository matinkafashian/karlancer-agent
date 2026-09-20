"""Validated runtime configuration with safe submission defaults."""

from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunMode(StrEnum):
    """Operational safety level for the agent."""

    SHADOW = "shadow"
    ASSISTED = "assisted"
    AUTO = "auto"


class ModelProvider(StrEnum):
    """Runtime model backend; disabled keeps ingestion fail-safe until configured."""

    DISABLED = "disabled"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"
    CODEX_CLI = "codex_cli"


class TelegramSettings(BaseSettings):
    """Minimal secret set required by the standalone Telegram smoke test."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    telegram_chat_id: int


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
    daily_submission_limit: int = Field(default=26, ge=1, le=26)
    database_url: SecretStr
    telegram_bot_token: SecretStr
    telegram_chat_id: int
    internal_api_key: SecretStr = Field(min_length=16)
    approval_signing_key: SecretStr = Field(min_length=32)
    browser_profile_dir: Path = Path("/data/browser-profile")
    browser_headless: bool = False
    browser_proxy_server: str | None = None
    browser_proxy_username: SecretStr | None = None
    browser_proxy_password: SecretStr | None = None
    evidence_dir: Path = Path("/data/evidence")
    evidence_retention_days: int = Field(default=7, ge=1, le=30)
    model_provider: ModelProvider = ModelProvider.DISABLED
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    codex_executable: str = Field(default="codex", min_length=1)
    codex_reasoning_effort: str = Field(default="low", pattern=r"^(low|medium)$")
    codex_timeout_seconds: int = Field(default=120, ge=30, le=600)
    live_reply_monitor_enabled: bool = True

    @model_validator(mode="after")
    def validate_model_connection(self) -> "Settings":
        if not self.browser_proxy_server and (
            self.browser_proxy_username is not None
            or self.browser_proxy_password is not None
        ):
            raise ValueError("browser proxy credentials require a proxy server")
        if self.model_provider is ModelProvider.DISABLED:
            return self
        if not self.model_name:
            raise ValueError("enabled model provider requires a model name")
        if (
            self.model_provider is ModelProvider.OPENAI_COMPATIBLE
            and (not self.model_base_url or self.model_api_key is None)
        ):
            raise ValueError("OpenAI-compatible provider requires base URL and API key")
        if self.model_provider is ModelProvider.OLLAMA and not self.model_base_url:
            raise ValueError("Ollama provider requires a base URL")
        return self

    @property
    def browser_proxy(self) -> dict[str, str] | None:
        """Return Playwright proxy options without exposing secrets in logs."""

        if not self.browser_proxy_server:
            return None
        value = {"server": self.browser_proxy_server}
        if self.browser_proxy_username is not None:
            value["username"] = self.browser_proxy_username.get_secret_value()
        if self.browser_proxy_password is not None:
            value["password"] = self.browser_proxy_password.get_secret_value()
        return value

    @property
    def real_submission_allowed(self) -> bool:
        """Return whether global mode and kill switch permit a real submit."""

        return self.submission_enabled and self.run_mode is not RunMode.SHADOW
