from pathlib import Path

import httpx
import pytest

from karlancer_agent.ingestion.parser import ChannelParseError
from karlancer_agent.ingestion.telegram_preview import (
    IngestionUnavailableError,
    TelegramPreviewSource,
)

FIXTURE_HTML = Path("tests/fixtures/channel_page.html").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_source_uses_get_and_returns_overlap_in_ascending_order() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=FIXTURE_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = TelegramPreviewSource(client=client, overlap_messages=1)
        projects = await source.poll(after_message_id=7001)

    assert [project.channel_message_id for project in projects] == [7001, 7003]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].headers["User-Agent"].startswith("KarlancerAgent/")


@pytest.mark.asyncio
async def test_zero_overlap_returns_only_messages_after_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FIXTURE_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = TelegramPreviewSource(client=client, overlap_messages=0)
        projects = await source.poll(after_message_id=7001)

    assert [project.channel_message_id for project in projects] == [7003]


@pytest.mark.asyncio
async def test_source_retries_only_retryable_http_failures() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        status = 503 if attempts < 3 else 200
        return httpx.Response(status, text=FIXTURE_HTML)

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = TelegramPreviewSource(
            client=client,
            retry_delays=(1.0, 5.0, 15.0),
            sleep=fake_sleep,
        )
        projects = await source.poll(after_message_id=None)

    assert len(projects) == 2
    assert attempts == 3
    assert delays == [1.0, 5.0]


@pytest.mark.asyncio
async def test_source_does_not_retry_parser_failure() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, text="<html>changed DOM</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = TelegramPreviewSource(client=client)
        with pytest.raises(ChannelParseError):
            await source.poll(after_message_id=None)

    assert attempts == 1


@pytest.mark.asyncio
async def test_source_raises_after_network_retry_budget_is_exhausted() -> None:
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = TelegramPreviewSource(
            client=client,
            retry_delays=(0.1, 0.2, 0.3),
            sleep=fake_sleep,
        )
        with pytest.raises(IngestionUnavailableError, match="4 attempts"):
            await source.poll(after_message_id=None)

    assert sleeps == [0.1, 0.2, 0.3]
