"""Typed application dependencies injected into the API."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from karlancer_agent.config import Settings
from karlancer_agent.services.approval import ApprovalCommandResult
from karlancer_agent.services.pipeline import PipelineRunResult, ProjectStageResult


class PipelineService(Protocol):
    async def ingest(self, *, idempotency_key: str) -> PipelineRunResult: ...

    async def analyze(self, project_id: UUID) -> ProjectStageResult: ...

    async def draft(self, project_id: UUID) -> ProjectStageResult: ...


class ProjectReader(Protocol):
    async def project_view(self, project_id: UUID) -> dict[str, Any] | None: ...


class ReadinessChecker(Protocol):
    async def check(self) -> dict[str, bool]: ...


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
