from uuid import uuid4

import httpx
import pytest

from karlancer_agent.api.dependencies import ApplicationServices
from karlancer_agent.api.main import create_app
from karlancer_agent.config import Settings
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.services.notifications import SystemAlert
from karlancer_agent.services.pipeline import PipelineRunResult, ProjectStageResult


class FakeAutoCycle:
    def __init__(self) -> None:
        self.calls = []

    async def run_once(self, *, idempotency_key: str, bootstrap_limit: int | None):
        self.calls.append((idempotency_key, bootstrap_limit))
        return {
            "discovered": 4,
            "related": 2,
            "submitted": 1,
            "rejected": 1,
            "needs_review": 0,
            "errors": 0,
            "submitted_titles": ["ربات پایتون"],
            "ai_paused_until": None,
        }


class FakePipeline:
    ingest_calls = 0

    async def ingest(self, *, idempotency_key: str) -> PipelineRunResult:
        self.ingest_calls += 1
        return PipelineRunResult(discovered=1, drafted=0, needs_review=0, rejected=0)

    async def run_once(self, *, idempotency_key: str) -> PipelineRunResult:
        return PipelineRunResult(discovered=2, drafted=1, needs_review=1, rejected=0)

    async def analyze(self, project_id):
        return ProjectStageResult(project_id=project_id, state=ProjectState.ELIGIBLE)

    async def draft(self, project_id):
        return ProjectStageResult(project_id=project_id, state=ProjectState.DRAFTED)


class FakeProjects:
    async def project_view(self, project_id):
        return {
            "id": str(project_id),
            "state": ProjectState.DRAFTED.value,
            "title": "Test project",
        }


class FakePause:
    paused = False

    async def is_system_paused(self):
        return self.paused

    async def pause_system(self, *, reason):
        changed = not self.paused
        self.paused = True
        return changed


@pytest.mark.asyncio
async def test_fatal_reply_error_blocks_all_later_jobs_and_alerts_once():
    class BrokenReplies:
        async def run_once(self):
            raise RuntimeError("message page unavailable")

    pause = FakePause()
    notifier = FakeOperationalNotifier()
    pipeline = FakePipeline()
    app = create_app(ApplicationServices(
        settings=settings(), pipeline=pipeline, projects=FakeProjects(),
        readiness=FakeReadiness(), reply_watch=BrokenReplies(),
        auto_cycle=FakeAutoCycle(), pause_controller=pause,
        operational_notifier=notifier,
    ))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"X-Internal-API-Key": "internal-secret-key",
                 "Idempotency-Key": "guard-test-1"},
    ) as client:
        assert (await client.post("/v1/jobs/reply-watch")).status_code == 503
        for route in ("auto", "reply-watch", "ingest", "shadow"):
            response = await client.post(f"/v1/jobs/{route}")
            assert response.status_code == 200
            assert response.json()["paused"] is True
    assert pause.paused
    assert len(notifier.alerts) == 1
    assert pipeline.ingest_calls == 0


class FakeReadiness:
    async def check(self):
        return {"database": True, "browser": True, "provider": True}


class FakeReplyWatch:
    calls = 0

    async def run_once(self):
        self.calls += 1
        return {
            "projects_checked": 2,
            "notifications_sent": 1,
        }


class FakeOperationalNotifier:
    def __init__(self) -> None:
        self.alerts: list[SystemAlert] = []

    async def queue_and_dispatch(self, alert: SystemAlert) -> bool:
        self.alerts.append(alert)
        return True


def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="secret",
        telegram_chat_id=1,
        internal_api_key="internal-secret-key",
        approval_signing_key="approval-signing-key-with-32-characters",
    )


@pytest.fixture
def app_and_pipeline():
    pipeline = FakePipeline()
    reply_watch = FakeReplyWatch()
    auto_cycle = FakeAutoCycle()
    operational_notifier = FakeOperationalNotifier()
    app = create_app(
        ApplicationServices(
            settings=settings(),
            pipeline=pipeline,
            projects=FakeProjects(),
            readiness=FakeReadiness(),
            reply_watch=reply_watch,
            auto_cycle=auto_cycle,
            operational_notifier=operational_notifier,
        )
    )
    return app, pipeline


@pytest.mark.asyncio
async def test_operational_alert_is_authenticated_and_delivered(app_and_pipeline) -> None:
    app, _ = app_and_pipeline
    payload = {
        "notification_key": "n8n-auto-error-42",
        "title": "خطای اجرای خودکار کارلنسر",
        "body": "چرخه خودکار n8n ناموفق بود.",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        unauthorized = await client.post("/v1/alerts/operational", json=payload)
        delivered = await client.post(
            "/v1/alerts/operational",
            json=payload,
            headers={"X-Internal-API-Key": "internal-secret-key"},
        )

    assert unauthorized.status_code == 401
    assert delivered.status_code == 200
    assert delivered.json() == {"delivered": True}
    assert app.state.services.operational_notifier.alerts == [SystemAlert(**payload)]


@pytest.mark.asyncio
async def test_auto_job_requires_keys_and_bounds_bootstrap_limit(app_and_pipeline) -> None:
    app, _ = app_and_pipeline
    headers = {
        "X-Internal-API-Key": "internal-secret-key",
        "Idempotency-Key": "bootstrap-2026-09-13",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        invalid = await client.post(
            "/v1/jobs/auto?bootstrap_limit=11", headers=headers
        )
        valid = await client.post(
            "/v1/jobs/auto?bootstrap_limit=10", headers=headers
        )

    assert invalid.status_code == 422
    assert valid.status_code == 200
    assert valid.json()["submitted"] == 1
    assert app.state.services.auto_cycle.calls == [("bootstrap-2026-09-13", 10)]


@pytest.mark.asyncio
async def test_liveness_needs_no_auth_or_external_dependency(app_and_pipeline) -> None:
    app, _ = app_and_pipeline
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_mutating_endpoint_requires_internal_key_and_idempotency_key(
    app_and_pipeline,
) -> None:
    app, _ = app_and_pipeline
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        no_auth = await client.post("/v1/jobs/ingest")
        no_idempotency = await client.post(
            "/v1/jobs/ingest", headers={"X-Internal-API-Key": "internal-secret-key"}
        )
        valid = await client.post(
            "/v1/jobs/ingest",
            headers={
                "X-Internal-API-Key": "internal-secret-key",
                "Idempotency-Key": "ingest-2026-09-08T10:00",
            },
        )

    assert no_auth.status_code == 401
    assert no_idempotency.status_code == 422
    assert valid.status_code == 200
    assert valid.json()["discovered"] == 1


@pytest.mark.asyncio
async def test_shadow_job_runs_full_read_analyze_draft_cycle_without_submission(
    app_and_pipeline,
) -> None:
    app, _ = app_and_pipeline
    headers = {
        "X-Internal-API-Key": "internal-secret-key",
        "Idempotency-Key": "shadow-2026-09-11T18:00",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/jobs/shadow", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "discovered": 2,
        "drafted": 1,
        "needs_review": 1,
        "rejected": 0,
    }


@pytest.mark.asyncio
async def test_reply_watch_is_available_on_unified_internal_api(app_and_pipeline) -> None:
    app, _ = app_and_pipeline
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        unauthorized = await client.post("/v1/jobs/reply-watch")
        response = await client.post(
            "/v1/jobs/reply-watch",
            headers={"X-Internal-API-Key": "internal-secret-key"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "projects_checked": 2,
        "notifications_sent": 1,
    }


@pytest.mark.asyncio
async def test_project_stage_and_read_endpoints_have_stable_shapes(app_and_pipeline) -> None:
    app, _ = app_and_pipeline
    project_id = uuid4()
    headers = {
        "X-Internal-API-Key": "internal-secret-key",
        "Idempotency-Key": "project-command-1",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        analyzed = await client.post(f"/v1/projects/{project_id}/analyze", headers=headers)
        drafted = await client.post(f"/v1/projects/{project_id}/draft", headers=headers)
        fetched = await client.get(
            f"/v1/projects/{project_id}",
            headers={"X-Internal-API-Key": "internal-secret-key"},
        )

    assert analyzed.json() == {
        "project_id": str(project_id),
        "state": "eligible",
        "skipped": False,
    }
    assert drafted.json()["state"] == "drafted"
    assert fetched.json()["title"] == "Test project"


@pytest.mark.asyncio
async def test_readiness_reports_each_dependency_and_uses_503_when_any_fail(
    app_and_pipeline,
) -> None:
    app, _ = app_and_pipeline
    app.state.services = ApplicationServices(
        settings=settings(),
        pipeline=app.state.services.pipeline,
        projects=app.state.services.projects,
        readiness=UnavailableReadiness(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"database": True, "browser": False, "provider": True},
    }


class UnavailableReadiness:
    async def check(self):
        return {"database": True, "browser": False, "provider": True}


@pytest.mark.asyncio
async def test_missing_project_returns_stable_404(app_and_pipeline) -> None:
    app, _ = app_and_pipeline
    missing = uuid4()
    app.state.services = ApplicationServices(
        settings=settings(),
        pipeline=MissingPipeline(),
        projects=MissingProjects(),
        readiness=app.state.services.readiness,
    )
    headers = {
        "X-Internal-API-Key": "internal-secret-key",
        "Idempotency-Key": "missing-project-1",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        analyzed = await client.post(f"/v1/projects/{missing}/analyze", headers=headers)
        fetched = await client.get(
            f"/v1/projects/{missing}",
            headers={"X-Internal-API-Key": "internal-secret-key"},
        )

    assert analyzed.status_code == 404
    assert fetched.status_code == 404


class MissingPipeline(FakePipeline):
    async def analyze(self, project_id):
        raise LookupError(f"project {project_id} was not found")


class MissingProjects:
    async def project_view(self, project_id):
        return None
