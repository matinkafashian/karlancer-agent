"""Source contract used by the ingestion workflow."""

from typing import Protocol

from karlancer_agent.domain.models import ChannelProject


class ProjectSource(Protocol):
    """Read ordered projects while allowing safe overlap polling."""

    async def poll(self, after_message_id: int | None) -> list[ChannelProject]: ...
