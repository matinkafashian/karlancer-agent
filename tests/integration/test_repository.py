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
    ProviderCircuitRow,
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
async def test_pacing_persists_across_repository_instances(repository, engine):
    project, _ = await repository.upsert_discovered(channel_project())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        session.add(
            SubmissionRow(
                project_id=project.id,
                idempotency_key="pacing-check",
                status="submitted",
                created_at=now - timedelta(minutes=4),
                submitted_at=now - timedelta(minutes=4),
            )
        )
    assert not await repository.auto_cycle_ready(daily_limit=26)
    assert not await ProjectRepository(sessions).auto_cycle_ready(daily_limit=26)
    async with sessions() as session, session.begin():
        await session.execute(
            update(SubmissionRow).values(submitted_at=now - timedelta(minutes=10))
        )
    assert await repository.auto_cycle_ready(daily_limit=26)
    assert not await repository.auto_cycle_ready(daily_limit=1)


@pytest.mark.asyncio
async def test_discovery_is_idempotent_by_message_and_canonical_url(
    repository: ProjectRepository,
) -> None:
    first, first_created = await repository.upsert_discovered(channel_project())
    duplicate_message, message_created = await repository.upsert_discovered(channel_project())
    duplicate_url, url_created = await repository.upsert_discovered(channel_project(message_id=102))

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
async def test_submission_limit_alert_is_durable_and_deduplicated(
    repository: ProjectRepository,
) -> None:
    project, _ = await repository.upsert_discovered(channel_project(150, "150/limit"))
    observed_at = datetime(2026, 9, 9, 21, 45, tzinfo=UTC)
    key = "submission-limit:platform_submission_limit:2026-09-10"

    assert await repository.queue_submission_limit_alert(
        project.id,
        notification_key=key,
        title=project.title,
        project_url=project.canonical_project_url,
        reason="platform_submission_limit",
        observed_at=observed_at,
    )
    assert not await repository.queue_submission_limit_alert(
        project.id,
        notification_key=key,
        title="پروژه دیگر",
        project_url="https://www.karlancer.com/projects/151/other",
        reason="platform_submission_limit",
        observed_at=observed_at,
    )

    report = await repository.submission_limit_report_data(key)
    assert report == {
        "project_id": project.id,
        "title": project.title,
        "project_url": project.canonical_project_url,
        "reason": "platform_submission_limit",
        "observed_at": observed_at,
        "delivered": False,
    }


@pytest.mark.asyncio
async def test_provider_circuit_state_survives_transitions(
    repository: ProjectRepository, engine: AsyncEngine
) -> None:
    now = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
    retry_at = now + timedelta(hours=5)

    initial = await repository.provider_circuit_state("codex_cli")
    assert initial.status == "closed"
    assert initial.consecutive_failures == 0

    assert (
        await repository.open_provider_cooldown("codex_cli", retry_at=retry_at, opened_at=now) == 1
    )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        stored = await session.get(ProviderCircuitRow, "codex_cli")
    assert stored is not None
    assert stored.status == "cooldown"
    assert stored.retry_at is not None
    assert stored.consecutive_failures == 1

    await repository.close_provider_circuit("codex_cli", updated_at=now + timedelta(hours=5))
    closed = await repository.provider_circuit_state("codex_cli")
    assert closed.status == "closed"
    assert closed.retry_at is None
    assert closed.consecutive_failures == 0


@pytest.mark.asyncio
async def test_system_alert_outbox_is_deduplicated_and_recoverable(
    repository: ProjectRepository,
) -> None:
    key = "provider-quota:2026-09-13T15:00:00+00:00"
    assert await repository.queue_system_alert(
        notification_key=key,
        title="توقف موقت",
        body="تلاش بعدی پنج ساعت دیگر است.",
    )
    assert not await repository.queue_system_alert(
        notification_key=key,
        title="تکراری",
        body="نباید جایگزین شود.",
    )
    assert await repository.system_alert_data(key) == {
        "title": "توقف موقت",
        "body": "تلاش بعدی پنج ساعت دیگر است.",
        "delivered": False,
    }
    delivered_at = datetime(2026, 9, 13, 10, 1, tzinfo=UTC)
    assert await repository.mark_notification_delivered(key, delivered_at=delivered_at)
    assert not await repository.mark_notification_delivered(key, delivered_at=delivered_at)


@pytest.mark.asyncio
async def test_auto_candidates_are_newest_first_and_exclude_any_submission(
    repository: ProjectRepository, engine: AsyncEngine
) -> None:
    oldest, _ = await repository.upsert_discovered(channel_project(160, "160/oldest"))
    submitted, _ = await repository.upsert_discovered(channel_project(161, "161/submitted"))
    newest, _ = await repository.upsert_discovered(channel_project(162, "162/newest"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id == oldest.id)
            .values(
                state=ProjectState.DRAFTED.value,
                created_at=datetime(2026, 9, 13, 8, 0, tzinfo=UTC),
            )
        )
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id == submitted.id)
            .values(
                state=ProjectState.DRAFTED.value,
                created_at=datetime(2026, 9, 13, 9, 0, tzinfo=UTC),
            )
        )
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id == newest.id)
            .values(
                state=ProjectState.DRAFTED.value,
                created_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
            )
        )
        session.add(
            SubmissionRow(
                project_id=submitted.id,
                idempotency_key="existing:161",
                status="reserved",
            )
        )

    assert await repository.list_auto_submission_candidates() == (
        newest.id,
        oldest.id,
    )


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
        await repository.reserve_ready_submission(first.id, key="safe:202", **common) == "reserved"
    )
    assert (
        await repository.reserve_ready_submission(second.id, key="safe:203", **common)
        == "daily_limit"
    )
    common["daily_limit"] = 10
    # A new repository/process still observes the persisted attempt.
    restarted = ProjectRepository(sessions)
    assert (
        await restarted.reserve_ready_submission(second.id, key="safe:203", **common)
        == "submission_interval"
    )
    async with sessions() as session, session.begin():
        await session.execute(
            update(SubmissionRow)
            .where(SubmissionRow.project_id == first.id)
            .values(
                status="submitted",
                created_at=datetime.now(UTC) - timedelta(minutes=31),
            )
        )
    assert (
        await restarted.reserve_ready_submission(second.id, key="safe:203", **common) == "reserved"
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

    conversation_url = "https://www.karlancer.com/panel/messages?room=7062838&type=messages"
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
    assert report["received_at"] == datetime(2026, 9, 8, 10, 18, tzinfo=UTC)

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


@pytest.mark.asyncio
async def test_ambiguous_clicked_submission_is_reconciled_from_real_conversation(
    repository: ProjectRepository, engine: AsyncEngine
) -> None:
    project, _ = await repository.upsert_discovered(channel_project(206, "206/ambiguous"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    attempted_at = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
    async with sessions() as session, session.begin():
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id == project.id)
            .values(
                state=ProjectState.PAUSED_ERROR.value,
                last_error="RuntimeError: ambiguous_after_click",
                extracted_data={"description": "ربات تلگرام"},
            )
        )
        session.add(
            SubmissionRow(
                project_id=project.id,
                idempotency_key="safe:206",
                status="paused_error",
                created_at=attempted_at,
            )
        )

    targets = await repository.claim_reply_watch_targets()
    assert len(targets) == 1
    assert targets[0]["requires_reconciliation"] is True
    room = "https://www.karlancer.com/panel/messages?room=7233983&type=messages"
    assert await repository.confirm_submission_from_conversation(project.id, conversation_url=room)

    async with sessions() as session:
        stored_project = await session.get(ProjectRow, project.id)
        stored_submission = await session.scalar(
            select(SubmissionRow).where(SubmissionRow.project_id == project.id)
        )
    assert stored_project is not None
    assert stored_project.state == ProjectState.REPLY_WATCH.value
    assert stored_project.last_error is None
    assert stored_submission is not None
    assert stored_submission.status == "submitted"
    assert stored_submission.submitted_at.replace(tzinfo=UTC) == attempted_at
    assert stored_submission.conversation_url == room


@pytest.mark.asyncio
async def test_global_pause_survives_repository_restart_and_blocks_reservations(
    repository: ProjectRepository,
    engine: AsyncEngine,
) -> None:
    project, _ = await repository.upsert_discovered(channel_project(207, "207/paused"))
    assert await repository.pause_system(reason="test terminal stop")
    assert not await repository.pause_system(reason="repeated failure")
    restarted = ProjectRepository(async_sessionmaker(engine, expire_on_commit=False))
    assert await restarted.is_system_paused()
    assert (
        await restarted.reserve_ready_submission(
            project.id,
            key="paused-attempt",
            daily_limit=26,
            since=datetime.now(UTC) - timedelta(hours=24),
            allowed_states=(ProjectState.DRAFTED,),
            form_budget=Decimal("3000000"),
            duration_value=Decimal("3"),
            duration_unit="day",
        )
        == "system_paused"
    )


@pytest.mark.asyncio
async def test_26_attempts_in_rolling_24_hours_prevent_attempt_27(
    repository: ProjectRepository,
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    for index in range(27):
        project, _ = await repository.upsert_discovered(
            channel_project(300 + index, f"quota/{300 + index}")
        )
        async with sessions() as session, session.begin():
            await session.execute(
                update(ProjectRow)
                .where(
                    ProjectRow.id == project.id,
                )
                .values(state=ProjectState.DRAFTED.value)
            )
            if index < 26:
                session.add(
                    SubmissionRow(
                        project_id=project.id,
                        idempotency_key=f"quota-{index}",
                        status="submitted",
                        created_at=now - timedelta(minutes=40 + index * 30),
                    )
                )
    assert (
        await repository.reserve_ready_submission(
            project.id,
            key="attempt-27",
            daily_limit=26,
            since=now - timedelta(hours=24),
            allowed_states=(ProjectState.DRAFTED,),
            form_budget=Decimal("3000000"),
            duration_value=Decimal("3"),
            duration_unit="day",
        )
        == "daily_limit"
    )


@pytest.mark.asyncio
async def test_recovery_quarantines_reserved_without_allowing_duplicate(repository, engine):
    project, _ = await repository.upsert_discovered(channel_project())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        row = await session.get(ProjectRow, project.id)
        row.state = "submit_pending"
        session.add(SubmissionRow(project_id=project.id, idempotency_key="interrupted"))
    assert await repository.recover_interrupted_submissions() == 1
    assert await repository.recover_interrupted_submissions() == 0
    assert await repository.is_system_paused()
    async with sessions() as session:
        row = await session.scalar(select(SubmissionRow))
        assert row.status == "paused_error"
        assert await session.scalar(select(OutboxEventRow.deduplication_key))
    targets = await repository.claim_reply_watch_targets()
    assert len(targets) == 1
    assert targets[0]["requires_reconciliation"]
    assert await repository.confirm_submission_from_conversation(
        project.id, conversation_url="https://www.karlancer.com/panel/messages?room=123"
    )
    assert not await repository.list_auto_submission_candidates()


@pytest.mark.asyncio
async def test_outbox_attempt_budget_persists_and_claim_is_exclusive(repository, engine):
    key = "bounded-delivery"
    await repository.queue_system_alert(notification_key=key, title="Error", body="Review needed")
    now = datetime.now(UTC)
    for attempt in range(1, 4):
        stamp = now + timedelta(minutes=attempt * 10)
        assert await repository.claim_notification(key, now=stamp) == attempt
        assert await repository.claim_notification(key, now=stamp) is None
        await repository.finish_notification_attempt(
            key, attempt=attempt, now=stamp, message_id=None
        )
    assert (
        await ProjectRepository(
            async_sessionmaker(engine, expire_on_commit=False)
        ).claim_notification(key, now=now + timedelta(days=1))
        is None
    )


@pytest.mark.asyncio
async def test_delivery_receipt_prevents_replay_after_handoff_crash(repository, engine):
    key = "receipt-delivery"
    await repository.queue_system_alert(notification_key=key, title="Error", body="Review needed")
    now = datetime.now(UTC)
    attempt = await repository.claim_notification(key, now=now)
    await repository.finish_notification_attempt(key, attempt=attempt, now=now, message_id=42)
    assert await repository.claim_notification(key, now=now + timedelta(days=1)) is None
    async with async_sessionmaker(engine)() as session:
        row = await session.scalar(select(OutboxEventRow))
        assert row.telegram_message_id == 42
        assert row.delivered


@pytest.mark.asyncio
async def test_outbox_recovers_during_pause_and_stops_after_three_failures(repository):
    from karlancer_agent.services.outbox import OutboxDispatcher

    await repository.pause_system(reason="site unavailable")
    await repository.queue_system_alert(notification_key="offline", title="Error", body="Review")

    class OfflineTelegram:
        calls = 0

        async def send_system_report(self, report, *, notification_key):
            self.calls += 1
            raise RuntimeError("offline")

    telegram = OfflineTelegram()
    now = datetime.now(UTC)
    dispatcher = OutboxDispatcher(repository=repository, telegram=telegram, clock=lambda: now)
    for _index in range(5):
        now += timedelta(hours=1)
        await dispatcher.run_once()
    assert telegram.calls == 3
    assert await repository.is_system_paused()


@pytest.mark.asyncio
async def test_notification_failure_pauses_work_and_keeps_bounded_retry(repository):
    from karlancer_agent.services.outbox import OutboxDispatcher

    class OfflineTelegram:
        async def send_system_report(self, report, *, notification_key):
            raise RuntimeError("offline")

    await repository.queue_system_alert(
        notification_key="delivery-fault", title="Error", body="Review"
    )
    dispatcher = OutboxDispatcher(repository=repository, telegram=OfflineTelegram())
    assert not await dispatcher.dispatch("delivery-fault")
    assert await repository.is_system_paused()


@pytest.mark.asyncio
async def test_real_notifier_without_explicit_dispatch_cannot_bypass_exhaustion(repository):
    from karlancer_agent.services.notifications import OperationalNotifier, SystemAlert

    class MustNotSend:
        async def send_system_report(self, *args, **kwargs):
            raise AssertionError("exhausted outbox was sent")

    key = "legacy-script-exhausted"
    await repository.queue_system_alert(notification_key=key, title="Error", body="Review")
    now = datetime.now(UTC)
    for i in range(3):
        stamp = now - timedelta(hours=4 - i)
        attempt = await repository.claim_notification(key, now=stamp)
        await repository.finish_notification_attempt(
            key, attempt=attempt, now=stamp, message_id=None
        )
    notifier = OperationalNotifier(repository=repository, telegram=MustNotSend())
    assert not await notifier.queue_and_dispatch(
        SystemAlert(notification_key=key, title="Error", body="Review")
    )


@pytest.mark.asyncio
async def test_first_reply_receipt_handoff_survives_dispatcher_restart(repository, engine):
    from karlancer_agent.notifications.telegram import TelegramReceipt
    from karlancer_agent.services.outbox import OutboxDispatcher

    project, _ = await repository.upsert_discovered(channel_project())
    async with async_sessionmaker(engine)() as session, session.begin():
        await session.execute(
            update(ProjectRow).where(ProjectRow.id == project.id).values(state="reply_watch")
        )
    now = datetime.now(UTC)
    await repository.queue_first_reply_once(
        FirstReply(
            project_id=str(project.id),
            employer_message_id="synthetic-message",
            text="A synthetic first reply",
            received_at=now,
            conversation_url="https://www.karlancer.com/panel/messages?room=123",
        ),
        notification_key="first-reply-test",
    )

    class Telegram:
        calls = 0

        async def send_first_reply_report(self, report, *, notification_key):
            self.calls += 1
            return TelegramReceipt(message_id=99, notification_key=notification_key)

    telegram = Telegram()
    assert await OutboxDispatcher(repository=repository, telegram=telegram).run_once() == 1
    assert await OutboxDispatcher(repository=repository, telegram=telegram).run_once() == 0
    assert telegram.calls == 1
    async with async_sessionmaker(engine)() as session:
        event = await session.scalar(select(OutboxEventRow))
        reply = await session.scalar(select(ReplyEventRow))
        row = await session.get(ProjectRow, project.id)
        assert event.telegram_message_id == 99
        assert reply.notified_at is not None
        assert row.state == "human_handoff"


@pytest.mark.asyncio
async def test_closed_draft_leaves_candidate_queue(repository, engine):
    project, _ = await repository.upsert_discovered(channel_project())
    async with async_sessionmaker(engine)() as session, session.begin():
        await session.execute(
            update(ProjectRow).where(ProjectRow.id == project.id).values(state="drafted")
        )
    assert await repository.reject_unavailable_project(project.id, reason="project_closed")
    assert await repository.list_auto_submission_candidates() == ()
