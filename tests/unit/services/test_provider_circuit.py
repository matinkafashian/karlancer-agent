from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from karlancer_agent.llm.base import ProviderQuotaError
from karlancer_agent.services.provider_circuit import (
    ProviderCircuit,
    ProviderCircuitState,
    ProviderCoolingDown,
    ProviderHardPaused,
)

NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.state = ProviderCircuitState(
            provider="codex_cli",
            status="closed",
            retry_at=None,
            consecutive_failures=0,
            opened_at=None,
            updated_at=NOW,
        )

    async def provider_circuit_state(self, provider: str) -> ProviderCircuitState:
        assert provider == "codex_cli"
        return self.state

    async def open_provider_cooldown(
        self, provider: str, *, retry_at: datetime, opened_at: datetime
    ) -> int:
        failures = self.state.consecutive_failures + 1
        self.state = ProviderCircuitState(
            provider=provider,
            status="cooldown",
            retry_at=retry_at,
            consecutive_failures=failures,
            opened_at=self.state.opened_at or opened_at,
            updated_at=opened_at,
        )
        return failures

    async def close_provider_circuit(self, provider: str, *, updated_at: datetime) -> None:
        self.state = ProviderCircuitState(
            provider=provider,
            status="closed",
            retry_at=None,
            consecutive_failures=0,
            opened_at=None,
            updated_at=updated_at,
        )

    async def hard_pause_provider(self, provider: str, *, updated_at: datetime) -> None:
        self.state = ProviderCircuitState(
            provider=provider,
            status="hard_paused",
            retry_at=None,
            consecutive_failures=self.state.consecutive_failures,
            opened_at=self.state.opened_at,
            updated_at=updated_at,
        )


class FakeNotifier:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def queue_and_dispatch(self, alert: Any) -> bool:
        self.keys.append(alert.notification_key)
        return True


def circuit(
    repository: FakeRepository, notifier: FakeNotifier
) -> ProviderCircuit:
    return ProviderCircuit(
        provider="codex_cli",
        repository=repository,
        notifier=notifier,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_quota_error_opens_five_hour_cooldown_without_retry() -> None:
    repository = FakeRepository()
    notifier = FakeNotifier()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ProviderQuotaError("limit", retry_at=None)

    with pytest.raises(ProviderCoolingDown) as caught:
        await circuit(repository, notifier).call(operation)

    assert calls == 1
    assert caught.value.retry_at == NOW + timedelta(hours=5)
    assert repository.state.retry_at == NOW + timedelta(hours=5)
    assert notifier.keys == [f"provider-quota:{NOW.isoformat()}"]


@pytest.mark.asyncio
async def test_cooldown_skips_operation_until_retry_time() -> None:
    repository = FakeRepository()
    repository.state = ProviderCircuitState(
        provider="codex_cli",
        status="cooldown",
        retry_at=NOW + timedelta(minutes=30),
        consecutive_failures=1,
        opened_at=NOW - timedelta(minutes=5),
        updated_at=NOW,
    )
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "unused"

    with pytest.raises(ProviderCoolingDown):
        await circuit(repository, FakeNotifier()).call(operation)

    assert calls == 0


@pytest.mark.asyncio
async def test_initial_failure_and_two_probes_then_durable_hard_pause() -> None:
    repository = FakeRepository()
    notifier = FakeNotifier()
    now = NOW
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ProviderQuotaError("limit", retry_at=None)

    for attempt in range(3):
        # Recreate the service each time to model worker restarts.
        service = ProviderCircuit(
            provider="codex_cli", repository=repository, notifier=notifier,
            clock=lambda now=now: now,
        )
        expected = ProviderHardPaused if attempt == 2 else ProviderCoolingDown
        with pytest.raises(expected):
            await service.call(operation)
        with pytest.raises(expected):
            await service.call(operation)
        now += timedelta(hours=5)

    assert calls == 3
    assert repository.state.status == "hard_paused"
    assert repository.state.consecutive_failures == 3
    assert repository.state.opened_at == NOW
    assert set(notifier.keys) == {f"provider-quota:{NOW.isoformat()}"}


@pytest.mark.asyncio
async def test_legacy_hard_pause_is_never_automatically_reset() -> None:
    repository = FakeRepository()
    repository.state = ProviderCircuitState(
        provider="codex_cli", status="hard_paused", retry_at=None,
        consecutive_failures=3, opened_at=NOW - timedelta(hours=15), updated_at=NOW,
    )

    async def operation() -> str:
        raise AssertionError("hard pause must not call provider")

    with pytest.raises(ProviderHardPaused):
        await circuit(repository, FakeNotifier()).call(operation)
    assert repository.state.status == "hard_paused"


@pytest.mark.asyncio
async def test_successful_probe_closes_circuit_and_reports_recovery() -> None:
    repository = FakeRepository()
    opened_at = NOW - timedelta(hours=5)
    repository.state = ProviderCircuitState(
        provider="codex_cli",
        status="cooldown",
        retry_at=NOW,
        consecutive_failures=1,
        opened_at=opened_at,
        updated_at=opened_at,
    )
    notifier = FakeNotifier()

    async def operation() -> str:
        return "ok"

    assert await circuit(repository, notifier).call(operation) == "ok"
    assert repository.state.status == "closed"
    assert repository.state.consecutive_failures == 0
    assert notifier.keys == []


@pytest.mark.asyncio
async def test_expired_provider_reset_time_uses_fallback_cooldown() -> None:
    repository = FakeRepository()
    async def operation() -> str:
        raise ProviderQuotaError("limit", retry_at=NOW - timedelta(minutes=1))
    with pytest.raises(ProviderCoolingDown) as caught:
        await circuit(repository, FakeNotifier()).call(operation)
    assert caught.value.retry_at == NOW + timedelta(hours=5)


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_post_cooldown_probe():
    import asyncio
    repository = FakeRepository()
    notifier = FakeNotifier()
    guard = circuit(repository, notifier)
    calls = 0
    async def operation():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        raise ProviderQuotaError("quota", retry_at=NOW + timedelta(hours=1))
    outcomes = await asyncio.gather(guard.call(operation), guard.call(operation),
                                    return_exceptions=True)
    assert all(isinstance(item, ProviderCoolingDown) for item in outcomes)
    assert calls == 1
