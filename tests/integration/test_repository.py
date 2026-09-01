from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from karlancer_agent.db.base import Base
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.domain.models import ChannelProject, FirstReply


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture
async def repository(engine: AsyncEngine) -> ProjectRepository:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return ProjectRepository(sessions)


def channel_project(message_id: int = 101, url_suffix: str = "123/rag") -> ChannelProject:
    return ChannelProject(
        channel_message_id=message_id,
        title="پیاده‌سازی سامانه RAG",
        budget_text="۱۰,۰۰۰,۰۰۰ تا ۲۰,۰۰۰,۰۰۰ تومان",  # noqa: RUF001
        duration_text="۷ روز",  # noqa: RUF001
        hashtags=("#پایتون", "#RAG"),
        project_url=f"https://www.karlancer.com/projects/{url_suffix}",
    )


@pytest.mark.asyncio
async def test_discovery_is_idempotent_by_message_and_canonical_url(
    repository: ProjectRepository,
) -> None:
    first, first_created = await repository.upsert_discovered(channel_project())
    duplicate_message, message_created = await repository.upsert_discovered(channel_project())
    duplicate_url, url_created = await repository.upsert_discovered(
        channel_project(message_id=102)
    )

    assert isinstance(first.id, UUID)
    assert first.state == ProjectState.DISCOVERED.value
    assert first_created is True
    assert message_created is False
    assert url_created is False
    assert duplicate_message.id == first.id == duplicate_url.id


@pytest.mark.asyncio
async def test_project_claim_is_atomic(repository: ProjectRepository) -> None:
    project, _ = await repository.upsert_discovered(channel_project())

    first_claim = await repository.claim_for_processing(project.id)
    second_claim = await repository.claim_for_processing(project.id)

    assert first_claim is True
    assert second_claim is False


@pytest.mark.asyncio
async def test_only_one_submission_can_be_reserved_per_project(
    repository: ProjectRepository,
) -> None:
    project, _ = await repository.upsert_discovered(channel_project())

    assert await repository.reserve_submission(project.id, "submit:123:v1") is True
    assert await repository.reserve_submission(project.id, "submit:123:v1") is False
    assert await repository.reserve_submission(project.id, "submit:123:v2") is False


@pytest.mark.asyncio
async def test_first_reply_is_recorded_only_once(repository: ProjectRepository) -> None:
    project, _ = await repository.upsert_discovered(channel_project())
    reply = FirstReply(
        project_id=str(project.id),
        employer_message_id="message-900",
        text="سلام، لطفاً درباره زمان شروع توضیح دهید.",
        received_at=datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
        conversation_url="https://www.karlancer.com/messages/conversation-123",
    )
    later_reply = reply.model_copy(update={"employer_message_id": "message-901"})

    assert await repository.record_first_reply_once(reply) is True
    assert await repository.record_first_reply_once(reply) is False
    assert await repository.record_first_reply_once(later_reply) is False
