# ruff: noqa: RUF001

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from karlancer_agent.api.dependencies import ApplicationServices
from karlancer_agent.api.main import create_app
from karlancer_agent.config import Settings
from karlancer_agent.db.base import Base
from karlancer_agent.db.models import ProjectRow, ProposalDraftRow
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.domain.models import ExtractedProject
from karlancer_agent.proposal.templates import FIXED_INTRO, FIXED_OUTRO, PRICE_SENTENCE
from karlancer_agent.proposal.validator import ProposalValidator
from karlancer_agent.services.approval import ApprovalService, ApprovalTokenSigner

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


class UnusedPipeline:
    async def ingest(self, *, idempotency_key: str):
        raise AssertionError("not used")

    async def analyze(self, project_id: UUID):
        raise AssertionError("not used")

    async def draft(self, project_id: UUID):
        raise AssertionError("not used")


class Ready:
    async def check(self):
        return {"database": True, "browser": True, "provider": True}


class RecordingTelegram:
    def __init__(self) -> None:
        self.sent = []

    async def send_assisted_report(self, report, *, notification_key: str):
        self.sent.append((report, notification_key))
        return type("Receipt", (), {"message_id": 77})()


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    value = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with value.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield value
    await value.dispose()


def extracted(project_id: str) -> ExtractedProject:
    return ExtractedProject(
        project_id=project_id,
        canonical_url=f"https://www.karlancer.com/projects/{project_id}/bot",
        title="ساخت ربات تلگرام با پایتون",
        description="دریافت پیام با وب‌هوک و ذخیره در PostgreSQL",
        skills=("Python", "Telegram Bot", "PostgreSQL"),
        budget_text="۱ تا ۳ میلیون تومان",
        budget_min=Decimal("1000000"),
        budget_max=Decimal("3000000"),
        currency="تومان",
        duration_text="۳ روز",
        duration_value=Decimal("3"),
        duration_unit="روز",
        is_open=True,
        proposal_form_url=f"https://www.karlancer.com/projects/{project_id}/bot#proposal",
    )


def valid_body() -> str:
    understanding = (
        "برای این ربات تلگرام، پیام‌ها با وب‌هوک دریافت و اطلاعات کاربران در PostgreSQL "
        "ذخیره می‌شود تا پس از راه‌اندازی دوباره داده‌ای از بین نرود."
    )
    approach = (
        "کار را با پایتون و ساختار ماژولار انجام می‌دهم؛ پردازش پیام و پایگاه داده جدا "
        "خواهند بود. مسیرهای تست و راهنمای استقرار همراه کد تحویل می‌شود."
    )
    return f"{FIXED_INTRO}\n\n{understanding}\n\n{approach}\n\n{PRICE_SENTENCE}\n\n{FIXED_OUTRO}"


async def seed_drafted(engine: AsyncEngine, suffix: str) -> UUID:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid4()
    data = extracted(suffix)
    validation = ProposalValidator().validate(body=valid_body(), project=data)
    assert validation.is_valid
    async with sessions() as session, session.begin():
        session.add(
            ProjectRow(
                id=project_id,
                channel_message_id=int(suffix),
                karlancer_project_id=suffix,
                canonical_project_url=str(data.canonical_url),
                title=data.title,
                raw_channel_data={},
                extracted_data=data.model_dump(mode="json"),
                state=ProjectState.DRAFTED.value,
            )
        )
        session.add(
            ProposalDraftRow(
                project_id=project_id,
                body=valid_body(),
                validator_result=validation.model_dump(mode="json"),
                version=1,
            )
        )
    return project_id


def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="secret",
        telegram_chat_id=1,
        internal_api_key="internal-secret-key",
        approval_signing_key="approval-signing-key-with-32-characters",
    )


def headers(key: str) -> dict[str, str]:
    return {
        "X-Internal-API-Key": "internal-secret-key",
        "Idempotency-Key": key,
    }


async def build_app(engine: AsyncEngine, *, now=NOW, ttl=timedelta(minutes=15)):
    repository = ProjectRepository(async_sessionmaker(engine, expire_on_commit=False))
    telegram = RecordingTelegram()
    approval = ApprovalService(
        repository=repository,
        telegram=telegram,
        signer=ApprovalTokenSigner(b"approval-signing-key-with-32-characters"),
        validator=ProposalValidator(),
        token_ttl=ttl,
        clock=lambda: now,
    )
    app = create_app(
        ApplicationServices(
            settings=settings(),
            pipeline=UnusedPipeline(),
            projects=repository,
            readiness=Ready(),
            approvals=approval,
        )
    )
    return app, telegram


@pytest.mark.asyncio
async def test_report_is_idempotent_and_signed_approval_is_one_time(engine) -> None:
    project_id = await seed_drafted(engine, "401")
    app, telegram = await build_app(engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            f"/v1/projects/{project_id}/approval/report", headers=headers("report:401:v1")
        )
        again = await client.post(
            f"/v1/projects/{project_id}/approval/report", headers=headers("report:401:v1")
        )
        callback = telegram.sent[0][0].approve_callback
        tampered = await client.post(
            f"/v1/projects/{project_id}/approval/callback",
            headers=headers("approve:401:tampered"),
            json={"callback_data": callback[:-1] + ("A" if callback[-1] != "A" else "B")},
        )
        approved = await client.post(
            f"/v1/projects/{project_id}/approval/callback",
            headers=headers("approve:401:v1"),
            json={"callback_data": callback},
        )
        reused = await client.post(
            f"/v1/projects/{project_id}/approval/callback",
            headers=headers("approve:401:v1:again"),
            json={"callback_data": callback},
        )

    assert first.status_code == 200
    assert again.status_code == 200
    assert len(telegram.sent) == 1
    assert len(callback.encode()) <= 64
    assert tampered.status_code == 422
    assert approved.status_code == 200
    assert approved.json()["state"] == ProjectState.SUBMIT_PENDING.value
    assert reused.status_code == 409


@pytest.mark.asyncio
async def test_expired_and_wrong_project_callbacks_are_rejected(engine) -> None:
    first_project = await seed_drafted(engine, "402")
    second_project = await seed_drafted(engine, "403")
    app, telegram = await build_app(engine, ttl=timedelta(seconds=-1))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            f"/v1/projects/{first_project}/approval/report", headers=headers("report:402:v1")
        )
        callback = telegram.sent[0][0].approve_callback
        expired = await client.post(
            f"/v1/projects/{first_project}/approval/callback",
            headers=headers("approve:402:v1"),
            json={"callback_data": callback},
        )
        wrong = await client.post(
            f"/v1/projects/{second_project}/approval/callback",
            headers=headers("approve:403:v1"),
            json={"callback_data": callback},
        )

    assert expired.status_code == 410
    assert wrong.status_code == 409


@pytest.mark.asyncio
async def test_reject_and_valid_edit_have_explicit_state_results(engine) -> None:
    reject_id = await seed_drafted(engine, "404")
    edit_id = await seed_drafted(engine, "405")
    app, telegram = await build_app(engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            f"/v1/projects/{reject_id}/approval/report", headers=headers("report:404:v1")
        )
        rejected = await client.post(
            f"/v1/projects/{reject_id}/approval/callback",
            headers=headers("reject:404:v1"),
            json={"callback_data": telegram.sent[-1][0].reject_callback},
        )
        await client.post(
            f"/v1/projects/{edit_id}/approval/report", headers=headers("report:405:v1")
        )
        edited = await client.post(
            f"/v1/projects/{edit_id}/approval/edit",
            headers=headers("edit:405:v2"),
            json={"body": valid_body()},
        )

    assert rejected.status_code == 200
    assert rejected.json()["state"] == ProjectState.REJECTED.value
    assert edited.status_code == 200
    assert edited.json()["state"] == ProjectState.AWAITING_APPROVAL.value
    assert len(telegram.sent) == 3


def test_auto_mode_cannot_emit_assisted_review_notifications():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from karlancer_agent.api.routes.approvals import _approval_commands
    from karlancer_agent.config import RunMode
    with pytest.raises(HTTPException) as error:
        _approval_commands(SimpleNamespace(settings=SimpleNamespace(run_mode=RunMode.AUTO),
                                           approvals=object()))
    assert error.value.status_code == 409
