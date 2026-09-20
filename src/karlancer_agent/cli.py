"""Small operational commands intended for Docker and n8n."""

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx

from karlancer_agent.config import Settings, TelegramSettings
from karlancer_agent.notifications.telegram import (
    SystemReport,
    TelegramBotClient,
)
from karlancer_agent.runtime import build_reply_watch_application


async def telegram_smoke() -> int:
    settings = TelegramSettings()  # type: ignore[call-arg]
    async with httpx.AsyncClient() as http:
        receipt = await TelegramBotClient(
            token=settings.telegram_bot_token.get_secret_value(),
            chat_id=settings.telegram_chat_id,
            http=http,
        ).send_system_report(
            SystemReport(
                title="تست اتصال ربات کارلنسر",
                body="اتصال ربات تلگرام برقرار است و اعلان‌ها به این گفتگو ارسال می‌شوند.",  # noqa: RUF001
            ),
            notification_key=f"smoke:{datetime.now(UTC).isoformat()}",
        )
    print(json.dumps({"ok": True, "message_id": receipt.message_id}))
    return 0


async def watch_replies_once() -> int:
    application = build_reply_watch_application(Settings())  # type: ignore[call-arg]
    try:
        result = await application.run_once()
    finally:
        await application.close()
    print(json.dumps(result.model_dump(), sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="karlancer-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("telegram-smoke", help="send one real Telegram test message")
    subparsers.add_parser(
        "watch-replies-once",
        help="check submitted projects and notify the first employer reply",
    )
    command = parser.parse_args(argv).command
    if command == "telegram-smoke":
        return asyncio.run(telegram_smoke())
    return asyncio.run(watch_replies_once())
