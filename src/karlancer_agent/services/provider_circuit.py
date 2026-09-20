"""Persistent Codex usage circuit breaker with bounded probes."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar

from karlancer_agent.classification.rules import RuleAssessment
from karlancer_agent.domain.models import AnalysisResult, ExtractedProject
from karlancer_agent.llm.base import LLMProvider, ProviderQuotaError
from karlancer_agent.llm.schemas import ProposalCore
from karlancer_agent.notifications.telegram import TEHRAN
from karlancer_agent.services.notifications import SystemAlert

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProviderCircuitState:
    """Detached state shape used by tests and alternative persistence adapters."""

    provider: str
    status: str
    retry_at: datetime | None
    consecutive_failures: int
    opened_at: datetime | None
    updated_at: datetime


class CircuitState(Protocol):
    provider: str
    status: str
    retry_at: datetime | None
    consecutive_failures: int
    opened_at: datetime | None
    updated_at: datetime


class ProviderCoolingDown(RuntimeError):
    """The provider must not be called again before ``retry_at``."""

    def __init__(self, retry_at: datetime) -> None:
        super().__init__(f"provider cooling down until {retry_at.isoformat()}")
        self.retry_at = retry_at


class ProviderHardPaused(RuntimeError):
    """The bounded probe budget is exhausted; manual review is required."""


class CircuitRepository(Protocol):
    async def provider_circuit_state(self, provider: str) -> CircuitState: ...

    async def open_provider_cooldown(
        self, provider: str, *, retry_at: datetime, opened_at: datetime
    ) -> int: ...

    async def close_provider_circuit(
        self, provider: str, *, updated_at: datetime
    ) -> None: ...

    async def hard_pause_provider(
        self, provider: str, *, updated_at: datetime
    ) -> None: ...


class AlertNotifier(Protocol):
    async def queue_and_dispatch(self, alert: SystemAlert) -> bool: ...


class ProviderCircuit:
    """Permit at most two post-reset probes per durable quota episode."""

    def __init__(
        self,
        *,
        provider: str,
        repository: CircuitRepository,
        notifier: AlertNotifier,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        fallback_cooldown: timedelta = timedelta(hours=5),
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._notifier = notifier
        self._clock = clock
        self._fallback_cooldown = fallback_cooldown
        self._lock = asyncio.Lock()

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            return await self._call_serialized(operation)

    async def _call_serialized(self, operation: Callable[[], Awaitable[T]]) -> T:
        state = await self._repository.provider_circuit_state(self._provider)
        now = self._clock()
        if state.status == "hard_paused":
            raise ProviderHardPaused("provider probe budget exhausted; manual review required")
        if state.retry_at is not None and now < _as_utc(state.retry_at):
            retry_at = _as_utc(state.retry_at)
            raise ProviderCoolingDown(retry_at)

        was_open = state.status == "cooldown"
        try:
            value = await operation()
        except ProviderQuotaError as error:
            retry_at = error.retry_at or now + self._fallback_cooldown
            retry_at = _as_utc(retry_at)
            if retry_at <= now:
                retry_at = now + self._fallback_cooldown
            failures = await self._repository.open_provider_cooldown(
                self._provider,
                retry_at=retry_at,
                opened_at=now,
            )
            episode = _as_utc(state.opened_at or now)
            await self._notify_safely(_quota_alert(retry_at, episode))
            # Initial quota failure plus two post-reset probes; durable across restarts.
            if failures >= 3:
                await self._repository.hard_pause_provider(self._provider, updated_at=now)
                raise ProviderHardPaused("provider probe budget exhausted") from error
            raise ProviderCoolingDown(retry_at) from error

        if was_open:
            await self._repository.close_provider_circuit(
                self._provider,
                updated_at=now,
            )
        return value

    async def _notify_safely(self, alert: SystemAlert) -> None:
        try:
            await self._notifier.queue_and_dispatch(alert)
        except Exception:
            # The durable outbox retains the alert. Provider state must still transition.
            return


class CircuitProtectedProvider:
    """Apply the same persistent quota circuit to analysis and proposal drafting."""

    def __init__(self, *, provider: LLMProvider, circuit: ProviderCircuit) -> None:
        self._provider = provider
        self._circuit = circuit

    async def analyze(
        self, project: ExtractedProject, rules: RuleAssessment
    ) -> AnalysisResult:
        return await self._circuit.call(lambda: self._provider.analyze(project, rules))

    async def draft(
        self, project: ExtractedProject, analysis: AnalysisResult
    ) -> ProposalCore:
        return await self._circuit.call(lambda: self._provider.draft(project, analysis))


def _quota_alert(retry_at: datetime, episode: datetime) -> SystemAlert:
    local_retry = retry_at.astimezone(TEHRAN)
    return SystemAlert(
        notification_key=f"provider-quota:{episode.isoformat()}",
        title="کارلنسر: سهمیه Codex تمام شد",
        body=(
            "ارسال پیشنهاد جدید موقتاً متوقف شد و در این فاصله درخواست دیگری "
            "به Codex فرستاده نمی‌شود. سیستم به‌صورت خودکار در "
            f"{local_retry:%Y-%m-%d ساعت %H:%M} به وقت تهران دوباره تلاش می‌کند؛ "
            "حداکثر دو تلاش خودکار پس از موعد انجام می‌شود؛ "
            "در صورت ادامهٔ محدودیت، بررسی دستی لازم است."
        ),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
