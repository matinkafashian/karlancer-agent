# ruff: noqa: RUF001

import json

import httpx
import pytest

from karlancer_agent.notifications.telegram import (
    AssistedReport,
    TelegramBotClient,
    TelegramMessageTooLong,
)


def report() -> AssistedReport:
    return AssistedReport(
        title="ساخت ربات تلگرام <ویژه>",
        selection_reason="تطابق Python، Telegram Bot و PostgreSQL",
        budget="۱ تا ۳ میلیون تومان",
        duration="۳ روز",
        draft_body="سلام و احترام؛ متن کامل پیشنهاد",
        project_url="https://www.karlancer.com/projects/322263/example",
        approve_callback="approve:short.signed-token",
        reject_callback="reject:short.signed-token",
    )


@pytest.mark.asyncio
async def test_assisted_report_contains_details_link_and_inline_actions() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        receipt = await TelegramBotClient(
            token="123:secret", chat_id=42, http=http, timeout_seconds=4
        ).send_assisted_report(report(), notification_key="review:322263:v1")

    assert receipt.message_id == 77
    assert receipt.notification_key == "review:322263:v1"
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["chat_id"] == 42
    assert "ساخت ربات تلگرام &lt;ویژه&gt;" in payload["text"]
    assert "تطابق Python، Telegram Bot و PostgreSQL" in payload["text"]
    assert "۱ تا ۳ میلیون تومان" in payload["text"]
    assert "۳ روز" in payload["text"]
    assert "متن کامل پیشنهاد" in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"]
    assert buttons[0][0]["callback_data"] == "approve:short.signed-token"
    assert buttons[0][1]["callback_data"] == "reject:short.signed-token"
    assert buttons[1][0]["url"].startswith("https://www.karlancer.com/projects/")


@pytest.mark.asyncio
async def test_report_rejects_callback_data_over_telegram_limit() -> None:
    oversized = report().model_copy(update={"approve_callback": "a" * 65})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as http:
        client = TelegramBotClient(token="123:secret", chat_id=42, http=http)
        with pytest.raises(ValueError, match="64 bytes"):
            await client.send_assisted_report(oversized, notification_key="review:key")


@pytest.mark.asyncio
async def test_report_rejects_message_over_bot_api_limit_without_network_call() -> None:
    oversized = report().model_copy(update={"draft_body": "x" * 5000})
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramBotClient(token="123:secret", chat_id=42, http=http)
        with pytest.raises(TelegramMessageTooLong):
            await client.send_assisted_report(oversized, notification_key="review:key")
    assert calls == 0
