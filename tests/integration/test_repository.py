from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from karlancer_agent.db.base import Base
from karlancer_agent.db.models import (
    OutboxEventRow,
    ProjectRow,
    ProposalDraftRow,
    ReplyEventRow,
    SubmissionRow,
)
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


@pytest.mark.asyncio
async def test_safe_reservation_requires_approved_submit_pending_and_is_unique(
    repository: ProjectRepository, engine: AsyncEngine
) -> None:
    project, _ = await repository.upsert_discovered(channel_project(201, "201/bot"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id == project.id)
            .values(state=ProjectState.SUBMIT_PENDING.value)
        )
        session.add(
            ProposalDraftRow(
                project_id=project.id,
                body="validated",
                validator_result={"is_valid": True},
                version=1,
            )
        )

    common = {
        "daily_limit": 30,
        "since": datetime.now(UTC) - timedelta(hours=24),
        "allowed_states": (ProjectState.SUBMIT_PENDING,),
        "form_budget": Decimal("3000000"),
        "duration_value": Decimal("3"),
        "duration_unit": "day",
    }
    assert (
        await repository.reserve_ready_submission(project.id, key="safe:201:v1", **common)
        == "state_conflict"
    )

    async with sessions() as session, session.begin():
        draft = await session.scalar(
            select(ProposalDraftRow).where(ProposalDraftRow.project_id == project.id)
        )
        assert draft is not None
        draft.approved_at = datetime.now(UTC)

    assert (
        await repository.reserve_ready_submission(project.id, key="safe:201:v1", **common)
        == "reserved"
    )
    assert (
        await repository.reserve_ready_submission(project.id, key="safe:201:v2", **common)
        == "duplicate"
    )


@pytest.mark.asyncio
async def test_safe_reservation_enforces_global_rolling_limit(
    repository: ProjectRepository, engine: AsyncEngine
) -> None:
    first, _ = await repository.upsert_discovered(channel_project(202, "202/first"))
    second, _ = await repository.upsert_discovered(channel_project(203, "203/second"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id.in_((first.id, second.id)))
            .values(state=ProjectState.DRAFTED.value)
        )

    common = {
        "daily_limit": 1,
        "since": datetime.now(UTC) - timedelta(hours=24),
        "allowed_states": (ProjectState.DRAFTED,),
        "form_budget": Decimal("3000000"),
        "duration_value": Decimal("3"),
        "duration_unit": "day",
    }
    assert (
        await repository.reserve_ready_submission(first.id, key="safe:202", **common)
        == "reserved"
    )
    assert (
        await repository.reserve_ready_submission(second.id, key="safe:203", **common)
        == "daily_limit"
    )


@pytest.mark.asyncio
async def test_completed_submission_persists_exact_form_values_and_state(
    repository: ProjectRepository, engine: AsyncEngine
) -> None:
    project, _ = await repository.upsert_discovered(channel_project(204, "204/complete"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id == project.id)
            .values(state=ProjectState.DRAFTED.value)
        )
    common = {
        "daily_limit": 30,
        "since": datetime.now(UTC) - timedelta(hours=24),
        "allowed_states": (ProjectState.DRAFTED,),
        "form_budget": Decimal("3000000"),
        "duration_value": Decimal("3"),
        "duration_unit": "day",
    }
    assert (
        await repository.reserve_ready_submission(project.id, key="safe:204", **common)
        == "reserved"
    )
    assert await repository.complete_submission(
        project.id,
        submitted_at=datetime.now(UTC),
        evidence_url="https://www.karlancer.com/messages/204",
        evidence_hash="a" * 64,
    )
    async with sessions() as session:
        stored_project = await session.get(ProjectRow, project.id)
        stored_submission = await session.scalar(
            select(SubmissionRow).where(SubmissionRow.project_id == project.id)
        )
    assert stored_project is not None
    assert stored_project.state == ProjectState.SUBMITTED.value
    assert stored_submission is not None
    assert stored_submission.form_budget == Decimal("3000000")
    assert stored_submission.duration_value == Decimal("3")
    assert stored_submission.duration_unit == "day"
    assert stored_submission.evidence_hash == "a" * 64


@pytest.mark.asyncio
async def test_first_reply_handoff_is_outbox_backed_and_terminal(
    repository: ProjectRepository, engine: AsyncEngine
) -> None:
    project, _ = await repository.upsert_discovered(channel_project(205, "205/reply"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    submitted_at = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    async with sessions() as session, session.begin():
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id == project.id)
            .values(
                state=ProjectState.SUBMITTED.value,
                extracted_data={
                    "description": "ساخت دستیار حقوقی محلی با مدل متن‌باز",
                },
            )
        )
        session.add(
            SubmissionRow(
                project_id=project.id,
                idempotency_key="safe:205",
                status="submitted",
                submitted_at=submitted_at,
            )
        )

    targets = await repository.claim_reply_watch_targets()
    assert len(targets) == 1
    assert targets[0]["project_id"] == project.id
    assert targets[0]["project_summary"] == "ساخت دستیار حقوقی محلی با مدل متن‌باز"

    conversation_url = (
        "https://www.karlancer.com/panel/messages?room=7062838&type=messages"
    )
    assert await repository.set_submission_conversation_url(
        project.id, conversation_url=conversation_url
    )
    reply = FirstReply(
        project_id=str(project.id),
        employer_message_id="27004106",
        text="سلام، مدل روی سرور خودمان اجرا می‌شود؟",
        received_at=datetime(2026, 9, 8, 10, 18, tzinfo=UTC),
        conversation_url=conversation_url,
    )
    key = f"first-reply:{project.id}:27004106"
    assert await repository.queue_first_reply_once(reply, notification_key=key)
    assert not await repository.queue_first_reply_once(reply, notification_key=key)
    assert await repository.pending_first_reply_notification_keys() == (key,)
    report = await repository.first_reply_report_data(key)
    assert report is not None
    assert report["conversation_url"] == conversation_url

    assert await repository.complete_first_reply_notification(
        key, notified_at=datetime(2026, 9, 8, 10, 19, tzinfo=UTC)
    )
    assert await repository.pending_first_reply_notification_keys() == ()
    async with sessions() as session:
        stored_project = await session.get(ProjectRow, project.id)
        stored_submission = await session.scalar(
            select(SubmissionRow).where(SubmissionRow.project_id == project.id)
        )
        stored_reply = await session.scalar(
            select(ReplyEventRow).where(ReplyEventRow.project_id == project.id)
        )
        stored_outbox = await session.scalar(
            select(OutboxEventRow).where(OutboxEventRow.deduplication_key == key)
        )
    assert stored_project is not None
    assert stored_project.state == ProjectState.HUMAN_HANDOFF.value
    assert stored_submission is not None
    assert stored_submission.conversation_url == conversation_url
    assert stored_reply is not None and stored_reply.notified_at is not None
    assert stored_outbox is not None and stored_outbox.delivered is True
