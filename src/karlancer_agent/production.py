"""Production dependency wiring for the unified, non-submitting API."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI
from sqlalchemy import text

from karlancer_agent.api.dependencies import ApplicationServices
from karlancer_agent.api.main import create_app
from karlancer_agent.browser.replies import KarlancerReplyReader
from karlancer_agent.browser.session import BrowserPage, BrowserSession, LoginStatus
from karlancer_agent.browser.submitter import ProposalSubmitter
from karlancer_agent.classification.rules import RulesClassifier
from karlancer_agent.config import ModelProvider, Settings
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.db.session import create_engine, create_session_factory
from karlancer_agent.ingestion.telegram_preview import TelegramPreviewSource
from karlancer_agent.llm.factory import build_model_provider
from karlancer_agent.notifications.telegram import TelegramBotClient
from karlancer_agent.observability.health import ReadinessService
from karlancer_agent.proposal.generator import ProposalGenerator
from karlancer_agent.proposal.validator import ProposalValidator
from karlancer_agent.runtime import ReplyWatchApplication
from karlancer_agent.services.approval import ApprovalService, ApprovalTokenSigner
from karlancer_agent.services.auto_cycle import AutoCycleService
from karlancer_agent.services.live_reply_monitor import LiveReplyMonitor
from karlancer_agent.services.notifications import OperationalNotifier
from karlancer_agent.services.outbox import OutboxDispatcher
from karlancer_agent.services.pipeline import ShadowPipeline
from karlancer_agent.services.provider_circuit import (
    CircuitProtectedProvider,
    ProviderCircuit,
)
from karlancer_agent.services.reply_watcher import ReplyWatcher


class LazyBrowserController:
    """Start one shared browser only when a read-only job needs it."""

    def __init__(self, browser: BrowserSession) -> None:
        self._browser = browser
        self._start_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if not self._started:
                await self._browser.start()
                self._started = True

    async def login_status(self) -> LoginStatus:
        await self.start()
        return await self._browser.login_status()

    @asynccontextmanager
    async def new_page(self) -> AsyncIterator[BrowserPage]:
        await self.start()
        async with self._browser.new_page() as page:
            yield page

    async def close(self) -> None:
        await self._browser.close()
        self._started = False


def create_production_app(settings: Settings | None = None) -> FastAPI:
    """Build the VPS API without exposing any Karlancer submission endpoint."""

    resolved = settings or Settings()  # type: ignore[call-arg]
    engine = create_engine(resolved)
    repository = ProjectRepository(create_session_factory(engine))
    http = httpx.AsyncClient()
    browser = LazyBrowserController(
        BrowserSession(
            profile_dir=resolved.browser_profile_dir,
            headless=resolved.browser_headless,
            proxy=resolved.browser_proxy,
        )
    )
    model = build_model_provider(resolved, http)
    telegram = TelegramBotClient(
        token=resolved.telegram_bot_token.get_secret_value(),
        chat_id=resolved.telegram_chat_id,
        http=http,
    )
    validator = ProposalValidator()
    outbox = OutboxDispatcher(repository=repository, telegram=telegram)
    operational_notifier = OperationalNotifier(
        repository=repository,
        telegram=telegram,
        dispatch=outbox.dispatch,
    )
    provider = CircuitProtectedProvider(
        provider=model.provider,
        circuit=ProviderCircuit(
            provider=model.provider_name,
            repository=repository,
            notifier=operational_notifier,
        ),
    )
    pipeline = ShadowPipeline(
        source=TelegramPreviewSource(client=http),
        repository=repository,
        browser=browser,
        classifier=RulesClassifier.default(),
        provider=provider,
        provider_name=model.provider_name,
        model_name=model.model_name,
        proposal_generator=ProposalGenerator(),
        proposal_validator=validator,
    )
    watcher = ReplyWatcher(
        repository=repository,
        reader=KarlancerReplyReader(),
        telegram=telegram,
        pause_controller=repository,
        dispatch=outbox.dispatch,
    )
    reply_watch = ReplyWatchApplication(
        browser=browser,
        watcher=watcher,
        pause_controller=repository,
    )
    approvals = ApprovalService(
        repository=repository,
        telegram=telegram,
        signer=ApprovalTokenSigner(resolved.approval_signing_key.get_secret_value().encode()),
        validator=validator,
    )
    submitter = ProposalSubmitter(
        settings=resolved,
        repository=repository,
        validator=validator,
        # AutoCycleService owns the single terminal error notification.
        limit_alerts=None,
        pause_check=repository.is_system_paused,
    )
    auto_cycle = AutoCycleService(
        pipeline=pipeline,
        repository=repository,
        browser=browser,
        submitter=submitter,
        notifier=operational_notifier,
        ready_check=lambda: repository.auto_cycle_ready(
            daily_limit=resolved.daily_submission_limit
        ),
    )
    live_monitor = LiveReplyMonitor(
        browser=browser,
        watcher=watcher,
        notifier=operational_notifier,
        pause_controller=repository,
    )

    async def provider_probe() -> bool:
        return resolved.model_provider is not ModelProvider.DISABLED

    readiness = ReadinessService(
        database=repository,
        browser=browser,
        provider_probe=provider_probe,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Keep a dedicated PostgreSQL session lock until every owned task has stopped.
        # A second worker must never quarantine a live worker's reservation.
        runtime_connection = await engine.connect()
        monitor_task = outbox_task = None
        owns_runtime = False
        try:
            if engine.dialect.name == "postgresql":
                owns_runtime = bool(
                    await runtime_connection.scalar(text("SELECT pg_try_advisory_lock(4214338)"))
                )
                if not owns_runtime:
                    raise RuntimeError("another Karlancer worker owns the runtime")
            await repository.recover_interrupted_submissions()
            outbox_task = asyncio.create_task(outbox.run_until_cancelled(), name="karlancer-outbox")
            if resolved.live_reply_monitor_enabled:
                monitor_task = asyncio.create_task(
                    live_monitor.run_until_cancelled(), name="karlancer-live-reply-monitor"
                )
            yield
        finally:
            for task in (monitor_task, outbox_task):
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            try:
                await browser.close()
            finally:
                await http.aclose()
                if owns_runtime:
                    await runtime_connection.execute(text("SELECT pg_advisory_unlock(4214338)"))
                await runtime_connection.close()
                await engine.dispose()

    services = ApplicationServices(
        settings=resolved,
        pipeline=pipeline,
        projects=repository,
        readiness=readiness,
        approvals=approvals,
        reply_watch=reply_watch,
        auto_cycle=auto_cycle,
        operational_notifier=operational_notifier,
        pause_controller=repository,
    )
    return create_app(services, lifespan=lifespan)
