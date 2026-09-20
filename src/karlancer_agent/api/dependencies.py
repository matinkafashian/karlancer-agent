"""Typed application dependencies injected into the API."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from karlancer_agent.config import Settings
from karlancer_agent.services.approval import ApprovalCommandResult
from karlancer_agent.services.auto_cycle import AutoCycleResult
from karlancer_agent.services.notifications import SystemAlert
from karlancer_agent.services.pipeline import PipelineRunResult, ProjectStageResult
from karlancer_agent.services.reply_watcher import ReplyWatchRunResult


class PipelineService(Protocol):
    async def run_once(self, *, idempotency_key: str) -> PipelineRunResult: ...

    async def ingest(self, *, idempotency_key: str) -> PipelineRunResult: ...

    async def analyze(self, project_id: UUID) -> ProjectStageResult: ...

    async def draft(self, project_id: UUID) -> ProjectStageResult: ...


class ProjectReader(Protocol):
    async def project_view(self, project_id: UUID) -> dict[str, Any] | None: ...


class ReadinessChecker(Protocol):
    async def check(self) -> dict[str, bool]: ...


class ReplyWatchRunner(Protocol):
    async def run_once(self) -> ReplyWatchRunResult: ...


class AutoCycleRunner(Protocol):
    async def run_once(
        self, *, idempotency_key: str, bootstrap_limit: int | None
    ) -> AutoCycleResult: ...


class OperationalAlertNotifier(Protocol):
    async def queue_and_dispatch(self, alert: SystemAlert) -> bool: ...


class SystemPauseController(Protocol):
    async def is_system_paused(self) -> bool: ...

    async def pause_system(self, *, reason: str) -> bool: ...


class ApprovalCommands(Protocol):
    async def request_review(
        self, project_id: UUID, *, notification_key: str
    ) -> ApprovalCommandResult: ...

    async def decide(
        self, project_id: UUID, *, callback_data: str
    ) -> ApprovalCommandResult: ...

    async def edit(
        self, project_id: UUID, *, body: str, notification_key: str
    ) -> ApprovalCommandResult: ...


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    settings: Settings
    pipeline: PipelineService
    projects: ProjectReader
    readiness: ReadinessChecker
    approvals: ApprovalCommands | None = None
    reply_watch: ReplyWatchRunner | None = None
    auto_cycle: AutoCycleRunner | None = None
    operational_notifier: OperationalAlertNotifier | None = None
    pause_controller: SystemPauseController | None = None
