"""Read-only client for Telegram's public channel preview."""

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from karlancer_agent.domain.models import ChannelProject
from karlancer_agent.ingestion.parser import parse_channel_page

Sleep = Callable[[float], Awaitable[None]]


class IngestionUnavailableError(ConnectionError):
    """Raised after all safe read retries have been exhausted."""


class TelegramPreviewSource:
    """Fetch projects with overlap and retry only read-only network failures."""

    def __init__(
        self,
        *,
        channel_url: str = "https://t.me/s/karlancer_projects",
        client: httpx.AsyncClient | None = None,
        overlap_messages: int = 3,
        retry_delays: tuple[float, ...] = (60.0, 300.0, 900.0),
        timeout_seconds: float = 20.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if overlap_messages < 0:
            raise ValueError("overlap_messages cannot be negative")
        self._channel_url = channel_url
        self._client = client
        self._overlap_messages = overlap_messages
        self._retry_delays = retry_delays
        self._timeout = httpx.Timeout(timeout_seconds)
        self._sleep = sleep

    async def poll(self, after_message_id: int | None) -> list[ChannelProject]:
        """Fetch, parse, and return new plus boundary-overlap projects."""

        if self._client is not None:
            projects = await self._fetch_and_parse(self._client)
        else:
            async with httpx.AsyncClient() as client:
                projects = await self._fetch_and_parse(client)
        return self._with_overlap(projects, after_message_id)

    async def _fetch_and_parse(self, client: httpx.AsyncClient) -> list[ChannelProject]:
        attempts = len(self._retry_delays) + 1
        for attempt in range(attempts):
            try:
                response = await client.get(
                    self._channel_url,
                    headers={"User-Agent": "KarlancerAgent/0.1 (+read-only-channel-ingestion)"},
                    timeout=self._timeout,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable Telegram preview response",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                if isinstance(error, httpx.HTTPStatusError) and not (
                    error.response.status_code == 429 or error.response.status_code >= 500
                ):
                    raise
                if attempt == attempts - 1:
                    message = f"Telegram preview unavailable after {attempts} attempts"
                    raise IngestionUnavailableError(message) from error
                await self._sleep(self._retry_delays[attempt])
                continue
            return parse_channel_page(response.text)

        raise AssertionError("retry loop exited unexpectedly")

    def _with_overlap(
        self, projects: list[ChannelProject], after_message_id: int | None
    ) -> list[ChannelProject]:
        if after_message_id is None:
            return projects
        previous = [
            project for project in projects if project.channel_message_id <= after_message_id
        ]
        new = [project for project in projects if project.channel_message_id > after_message_id]
        overlap = previous[-self._overlap_messages :] if self._overlap_messages else []
        return [*overlap, *new]
