"""Small Telegram Bot API adapter for assisted-review reports."""

from html import escape

import httpx
from pydantic import BaseModel, ConfigDict, Field

TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CALLBACK_LIMIT = 64


class TelegramError(RuntimeError):
    """Telegram rejected or could not receive a report."""


class TelegramMessageTooLong(TelegramError):
    """A report cannot fit in one Bot API text message."""


class AssistedReport(BaseModel):
    """All decision context shown before the user approves or rejects."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    budget: str = Field(min_length=1)
    duration: str = Field(min_length=1)
    draft_body: str = Field(min_length=1)
    project_url: str = Field(pattern=r"^https://")
    approve_callback: str = Field(min_length=1)
    reject_callback: str = Field(min_length=1)


class TelegramReceipt(BaseModel):
    """Stable result used to complete the durable outbox record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: int
    notification_key: str


class TelegramBotClient:
    """Send a single report with bounded timeout and no implicit retries."""

    def __init__(
        self,
        *,
        token: str,
        chat_id: int,
        http: httpx.AsyncClient,
        timeout_seconds: float = 15,
    ) -> None:
        self._endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._http = http
        self._timeout = timeout_seconds

    async def send_assisted_report(
        self, report: AssistedReport, *, notification_key: str
    ) -> TelegramReceipt:
        """Send one decision message; durable deduplication is handled by the outbox."""

        _validate_callback(report.approve_callback)
        _validate_callback(report.reject_callback)
        text = _render_report(report)
        if len(text) > TELEGRAM_TEXT_LIMIT:
            raise TelegramMessageTooLong(
                f"assisted report is {len(text)} characters; Telegram limit is 4096"
            )
        try:
            response = await self._http.post(
                self._endpoint,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "link_preview_options": {"is_disabled": True},
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "✅ تأیید",
                                    "callback_data": report.approve_callback,
                                },
                                {
                                    "text": "❌ رد",
                                    "callback_data": report.reject_callback,
                                },
                            ],
                            [{"text": "مشاهده پروژه", "url": report.project_url}],
                        ]
                    },
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as error:
            raise TelegramError("Telegram transport failed") from error
        if response.status_code >= 400:
            raise TelegramError(f"Telegram returned HTTP {response.status_code}")
        try:
            payload = response.json()
            if payload.get("ok") is not True:
                raise TelegramError("Telegram rejected the report")
            message_id = int(payload["result"]["message_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise TelegramError("Telegram returned an invalid response") from error
        return TelegramReceipt(message_id=message_id, notification_key=notification_key)


def _render_report(report: AssistedReport) -> str:
    return (
        "<b>پیشنهاد آمادهٔ بررسی</b>\n\n"
        f"<b>پروژه:</b> {escape(report.title)}\n"
        f"<b>دلیل انتخاب:</b> {escape(report.selection_reason)}\n"
        f"<b>بودجه:</b> {escape(report.budget)}\n"
        f"<b>زمان:</b> {escape(report.duration)}\n\n"
        f"<b>متن کامل پیشنهاد:</b>\n{escape(report.draft_body)}"
    )


def _validate_callback(value: str) -> None:
    if len(value.encode("utf-8")) > TELEGRAM_CALLBACK_LIMIT:
        raise ValueError("Telegram callback_data must be at most 64 bytes")
