# Karlancer Auto Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Karlancer agent continuously on the VPS, discover and submit eligible projects every 30 minutes, generate proposals through the user's ChatGPT Plus Codex entitlement, report every operational failure, and alert the first employer reply within 60 seconds of its appearance.

**Architecture:** n8n triggers one idempotent auto cycle every 30 minutes. The FastAPI worker owns the single persistent Chromium profile, a Codex CLI provider protected by a durable quota circuit breaker, and a background reply watcher that wakes on message-page activity with a 30-second fallback. PostgreSQL remains the source of truth for submissions, cooldowns, errors, and Telegram outbox delivery.

**Tech Stack:** Python 3.12, FastAPI, Playwright 1.62, PostgreSQL 16, SQLAlchemy, Alembic, n8n 2.37.11, Codex CLI, Telegram Bot API, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-01-karlancer-agent-design.md`

## Global Constraints

- Project discovery and automatic submission run every 30 minutes.
- Reply notification is delivered within 60 seconds in normal operation; no employer reply is generated.
- Production model is `gpt-5.6-luna` with low reasoning through ChatGPT-authenticated Codex CLI; no paid API key is used.
- A quota error makes zero same-cycle retries, opens a five-hour cooldown when no reset time is supplied, and allows one probe after cooldown.
- Three consecutive failed probes hard-pause the provider until manual intervention.
- The first auto bootstrap submits at most 10 newest eligible projects that have no prior bid in the database or Karlancer page.
- Proposal text contains no external links, phone numbers, email addresses, Telegram IDs, or invitations to communicate outside Karlancer.
- Budget equals the project's maximum advertised amount; duration equals the advertised duration.
- Any ambiguous result after the final submit click is never retried automatically.

---

### Task 1: ChatGPT-authenticated Codex CLI provider

**Files:**
- Create: `src/karlancer_agent/llm/codex_cli.py`
- Modify: `src/karlancer_agent/llm/base.py`
- Modify: `src/karlancer_agent/llm/factory.py`
- Modify: `src/karlancer_agent/config.py`
- Modify: `.env.example`
- Test: `tests/unit/llm/test_codex_cli.py`
- Test: `tests/unit/llm/test_factory.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `CodexCLIProvider(model, executable, reasoning_effort, timeout_seconds, runner=None)` implementing `LLMProvider`.
- Produces: `ProviderQuotaError(retry_at: datetime | None)` and `ProviderAuthenticationError` that are not converted to ordinary `needs_review` results.
- Consumes: the existing `_analysis_messages`, `_draft_messages`, `SemanticAnalysis`, and `ProposalCore` contracts.

- [ ] **Step 1: Write failing provider tests**

```python
@pytest.mark.asyncio
async def test_codex_provider_sends_schema_constrained_ephemeral_read_only_run():
    runner = FakeCodexRunner(stdout=VALID_ANALYSIS_JSON)
    provider = CodexCLIProvider(model="gpt-5.6-luna", runner=runner)
    result = await provider.analyze(PROJECT, RULES)
    assert result.decision is AnalysisDecision.ELIGIBLE
    assert runner.command[:4] == ("codex", "exec", "--ephemeral", "--sandbox")
    assert "read-only" in runner.command
    assert "--output-schema" in runner.command
    assert runner.command[-1] == "-"

@pytest.mark.asyncio
async def test_codex_provider_does_not_retry_quota_error():
    runner = FakeCodexRunner(returncode=1, stderr="usage limit reached; try again in 5 hours")
    provider = CodexCLIProvider(model="gpt-5.6-luna", runner=runner)
    with pytest.raises(ProviderQuotaError):
        await provider.draft(PROJECT, ANALYSIS)
    assert runner.calls == 1
```

- [ ] **Step 2: Run the tests and verify the expected missing-provider failure**

Run: `uv run pytest tests/unit/llm/test_codex_cli.py tests/unit/llm/test_factory.py tests/unit/test_config.py -q`

Expected: collection fails because `karlancer_agent.llm.codex_cli` and `ModelProvider.CODEX_CLI` do not exist.

- [ ] **Step 3: Implement the minimal provider**

```python
class CodexCLIProvider(StructuredLLMProvider):
    async def _request(self, *, messages, schema):
        prompt = json.dumps({"messages": list(messages)}, ensure_ascii=False)
        result = await self._runner.run(
            prompt=prompt,
            schema=schema.model_json_schema(),
            model=self.model,
            reasoning_effort=self._reasoning_effort,
            timeout_seconds=self._timeout_seconds,
        )
        if result.returncode != 0:
            raise classify_codex_failure(result.stderr)
        return result.stdout.strip()
```

The runner must invoke `codex exec --ephemeral --sandbox read-only --skip-git-repo-check --model gpt-5.6-luna -c model_reasoning_effort=low --output-schema <temp-json> --output-last-message <temp-json> -`, pass the prompt over stdin, enforce the configured timeout, and delete both temporary files in `finally`.

- [ ] **Step 4: Wire configuration and factory selection**

Add `CODEX_CLI = "codex_cli"`, `codex_executable="codex"`, `codex_reasoning_effort="low"`, and `codex_timeout_seconds=120`. `MODEL_API_KEY` must remain optional for Codex CLI while still required for `OPENAI_COMPATIBLE`.

- [ ] **Step 5: Run focused and full verification**

Run: `uv run pytest tests/unit/llm tests/unit/test_config.py -q`

Run: `uv run ruff check src tests`

Run: `uv run mypy src`

- [ ] **Step 6: Commit**

```bash
git add .env.example src/karlancer_agent/config.py src/karlancer_agent/llm tests/unit/llm tests/unit/test_config.py
git commit -m "feat: add ChatGPT-backed Codex CLI provider"
```

### Task 2: Durable usage circuit breaker and operational outbox

**Files:**
- Create: `migrations/versions/0004_provider_circuit.py`
- Create: `src/karlancer_agent/services/provider_circuit.py`
- Create: `src/karlancer_agent/services/notifications.py`
- Modify: `src/karlancer_agent/db/models.py`
- Modify: `src/karlancer_agent/db/repositories.py`
- Modify: `src/karlancer_agent/notifications/telegram.py`
- Test: `tests/unit/services/test_provider_circuit.py`
- Test: `tests/unit/services/test_notifications.py`
- Test: `tests/integration/test_repository.py`
- Test: `tests/integration/test_migration.py`

**Interfaces:**
- Produces: `ProviderCircuit.call(operation: Callable[[], Awaitable[T]]) -> T`.
- Produces: `ProjectRepository.provider_circuit_state()`, `open_provider_cooldown(...)`, `close_provider_circuit(...)`, and `hard_pause_provider(...)`.
- Produces: `OperationalNotifier.queue_and_dispatch(SystemAlert)` backed by `outbox_events`.

- [ ] **Step 1: Write failing circuit tests**

```python
@pytest.mark.asyncio
async def test_quota_error_opens_five_hour_cooldown_without_retry():
    calls = 0
    async def operation():
        nonlocal calls
        calls += 1
        raise ProviderQuotaError(retry_at=None)
    with pytest.raises(ProviderCoolingDown):
        await circuit.call(operation)
    assert calls == 1
    assert repository.state.retry_at == NOW + timedelta(hours=5)
    assert telegram.keys == ["provider-quota:2026-09-13T10:00:00+00:00"]

@pytest.mark.asyncio
async def test_third_failed_probe_hard_pauses_without_more_calls():
    repository.state.consecutive_failures = 2
    repository.state.retry_at = NOW
    with pytest.raises(ProviderHardPaused):
        await circuit.call(quota_limited_operation)
    with pytest.raises(ProviderHardPaused):
        await circuit.call(success_operation)
    assert operation_calls == 1
```

- [ ] **Step 2: Run the tests and verify failure for missing circuit state**

Run: `uv run pytest tests/unit/services/test_provider_circuit.py tests/integration/test_migration.py -q`

- [ ] **Step 3: Add provider state storage**

Create a singleton row keyed by provider name with `status`, `retry_at`, `consecutive_failures`, `opened_at`, and `updated_at`. The migration must restrict status to `closed`, `cooldown`, or `hard_paused` and set no secret fields.

- [ ] **Step 4: Implement circuit transitions and deduplicated alerts**

```python
if state.status == "hard_paused":
    raise ProviderHardPaused()
if state.retry_at is not None and now < state.retry_at:
    raise ProviderCoolingDown(state.retry_at)
try:
    value = await operation()
except ProviderQuotaError as error:
    retry_at = error.retry_at or now + timedelta(hours=5)
    failures = await repository.open_provider_cooldown(retry_at=retry_at)
    if failures >= 3:
        await repository.hard_pause_provider()
        raise ProviderHardPaused()
    raise ProviderCoolingDown(retry_at)
await repository.close_provider_circuit()
return value
```

Queue one Telegram alert when cooldown opens, one when a successful probe closes it, and one when hard pause begins. Intermediate 30-minute cycles only include `AI paused until HH:MM` in their cycle summary.

- [ ] **Step 5: Verify migration, repository, and services**

Run: `uv run pytest tests/unit/services/test_provider_circuit.py tests/unit/services/test_notifications.py tests/integration/test_repository.py tests/integration/test_migration.py -q`

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0004_provider_circuit.py src/karlancer_agent/db src/karlancer_agent/services src/karlancer_agent/notifications/telegram.py tests
git commit -m "feat: persist model cooldown and operational alerts"
```

### Task 3: Idempotent automatic cycle, bootstrap limit, and Telegram summary

**Files:**
- Create: `src/karlancer_agent/services/auto_cycle.py`
- Modify: `src/karlancer_agent/services/pipeline.py`
- Modify: `src/karlancer_agent/db/repositories.py`
- Modify: `src/karlancer_agent/api/dependencies.py`
- Modify: `src/karlancer_agent/api/routes/projects.py`
- Modify: `src/karlancer_agent/production.py`
- Modify: `src/karlancer_agent/notifications/telegram.py`
- Test: `tests/e2e/test_auto_cycle.py`
- Test: `tests/integration/test_api.py`
- Test: `tests/unit/notifications/test_telegram.py`

**Interfaces:**
- Produces: `AutoCycleService.run_once(idempotency_key: str, bootstrap_limit: int | None) -> AutoCycleResult`.
- Produces: `POST /v1/jobs/auto` with `Idempotency-Key` and optional `bootstrap_limit` constrained to 1..10.
- Consumes: `ShadowPipeline`, `ProposalSubmitter`, shared `BrowserPages`, `ProviderCircuit`, and `TelegramBotClient`.

- [ ] **Step 1: Write failing end-to-end tests**

```python
@pytest.mark.asyncio
async def test_bootstrap_submits_only_ten_newest_eligible_unsubmitted_projects():
    result = await service.run_once(idempotency_key="bootstrap-001", bootstrap_limit=10)
    assert result.submitted == 10
    assert submitter.project_ids == NEWEST_ELIGIBLE_IDS[:10]
    assert not set(submitter.project_ids) & ALREADY_SUBMITTED_IDS
    assert telegram.cycle_reports[-1].submitted == 10

@pytest.mark.asyncio
async def test_project_failure_is_reported_and_does_not_abort_other_projects():
    submitter.fail_for = {BROKEN_ID: "login_required"}
    result = await service.run_once(idempotency_key="cycle-002", bootstrap_limit=None)
    assert result.errors == 1
    assert GOOD_ID in submitter.project_ids
    assert telegram.alerts[0].project_url == BROKEN_URL
```

- [ ] **Step 2: Run the tests and verify the missing service failure**

Run: `uv run pytest tests/e2e/test_auto_cycle.py tests/integration/test_api.py -q`

- [ ] **Step 3: Implement newest-first candidate selection and per-project isolation**

The repository query must select `drafted` projects ordered by `created_at DESC, id DESC`, exclude every project with an existing `submissions` row, and cap only successful bootstrap submissions at 10. The service catches analysis, draft, and pre-click submission errors per project, persists `last_error`, emits one deduplicated project alert, and continues to the next candidate. `ambiguous_after_click` remains terminal and is never retried.

- [ ] **Step 4: Implement one concise summary per 30-minute cycle**

```text
بررسی کارلنسر: ۱۲ جدید | ۴ مرتبط | ۳ ارسال | ۸ رد | ۱ خطا
ارسال‌ها: عنوان کوتاه ۱، عنوان کوتاه ۲، عنوان کوتاه ۳
```

The message must remain under 4096 characters and must state `AI paused until <Tehran time>` when the provider circuit is open.

- [ ] **Step 5: Wire the guarded submitter into production**

Create `SubmissionLimitAlertService`, `ProposalSubmitter`, and `AutoCycleService` in `create_production_app`. Keep the two independent gates `RUN_MODE=auto` and `SUBMISSION_ENABLED=true`; do not add a second code path capable of clicking the final button.

- [ ] **Step 6: Verify auto behavior**

Run: `uv run pytest tests/e2e/test_auto_cycle.py tests/e2e/test_submission_guard.py tests/integration/test_api.py tests/unit/notifications/test_telegram.py -q`

- [ ] **Step 7: Commit**

```bash
git add src/karlancer_agent tests/e2e/test_auto_cycle.py tests/integration/test_api.py tests/unit/notifications/test_telegram.py
git commit -m "feat: run guarded automatic proposal cycles"
```

### Task 4: Near-live first-reply monitor and direct links

**Files:**
- Create: `src/karlancer_agent/services/live_reply_monitor.py`
- Modify: `src/karlancer_agent/browser/replies.py`
- Modify: `src/karlancer_agent/browser/session.py`
- Modify: `src/karlancer_agent/services/reply_watcher.py`
- Modify: `src/karlancer_agent/notifications/telegram.py`
- Modify: `src/karlancer_agent/production.py`
- Modify: `src/karlancer_agent/runtime.py`
- Test: `tests/unit/services/test_live_reply_monitor.py`
- Test: `tests/unit/browser/test_replies.py`
- Test: `tests/e2e/test_first_reply_handoff.py`

**Interfaces:**
- Produces: `LiveReplyMonitor.run_until_cancelled() -> None` as one background task owned by FastAPI lifespan.
- Produces: `ReplyActivitySignal.wait(timeout_seconds=30) -> Literal["event", "timeout"]` bound to one Playwright page.
- Extends: `FirstReplyReport` with `project_url` and two buttons: `مشاهده پروژه` and `باز کردن گفتگو`.

- [ ] **Step 1: Write failing event, fallback, and deduplication tests**

```python
@pytest.mark.asyncio
async def test_live_monitor_scans_immediately_after_message_event():
    signal.events = ["message"]
    await monitor.run_cycles(2)
    assert watcher.scan_times == [START, START + timedelta(seconds=2)]

@pytest.mark.asyncio
async def test_live_monitor_uses_thirty_second_fallback_without_busy_loop():
    signal.events = ["timeout"]
    await monitor.run_cycles(2)
    assert signal.wait_calls == [30]
    assert watcher.calls == 2

def test_first_reply_report_contains_project_and_conversation_links():
    payload = render_first_reply(REPORT)
    assert PROJECT_URL in payload["reply_markup"]["inline_keyboard"][0][0]["url"]
    assert CONVERSATION_URL in payload["reply_markup"]["inline_keyboard"][0][1]["url"]
```

- [ ] **Step 2: Run tests and verify the missing live monitor failure**

Run: `uv run pytest tests/unit/services/test_live_reply_monitor.py tests/e2e/test_first_reply_handoff.py -q`

- [ ] **Step 3: Implement event-first waiting with bounded fallback**

Bind Playwright `response` and `websocket`/`framereceived` events to one `asyncio.Event`. Any activity from a Karlancer messages URL sets the event. After every scan clear the event and wait with `asyncio.wait_for(..., timeout=30)`. On transport failure use backoff values 30, 60, then 300 seconds, send one deduplicated Telegram alert, and reset backoff after a successful scan.

- [ ] **Step 4: Start and cancel the monitor with application lifespan**

Create exactly one background task. On shutdown call `task.cancel()`, await `CancelledError`, then close BrowserSession, HTTP client, and database engine in that order. The monitor shares the existing `LazyBrowserController`; no second process may open the persistent profile.

- [ ] **Step 5: Verify reply handoff**

Run: `uv run pytest tests/unit/services/test_live_reply_monitor.py tests/unit/browser/test_replies.py tests/e2e/test_first_reply_handoff.py -q`

- [ ] **Step 6: Commit**

```bash
git add src/karlancer_agent/browser src/karlancer_agent/services src/karlancer_agent/notifications/telegram.py src/karlancer_agent/production.py src/karlancer_agent/runtime.py tests
git commit -m "feat: notify employer replies near real time"
```

### Task 5: n8n schedule and containerized Codex authentication

**Files:**
- Create: `workflows/n8n/auto.json`
- Create: `scripts/codex-login.sh`
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Modify: `compose.production.yaml`
- Modify: `.env.example`
- Modify: `tests/integration/test_n8n_exports.py`
- Modify: `tests/unit/test_deployment_assets.py`

**Interfaces:**
- Produces: one inactive source-controlled n8n workflow named `Karlancer - Auto Cycle` scheduled every 30 minutes and calling `/v1/jobs/auto`.
- Produces: `docker compose --profile codex-auth run --rm codex-login` for one-time ChatGPT device authentication.
- Produces: persistent volume `codex-auth:/data/codex` mounted read-write only for authentication and worker token refresh.

- [ ] **Step 1: Write failing deployment tests**

```python
def test_auto_workflow_runs_every_thirty_minutes():
    workflow = load_workflow("auto.json")
    assert schedule_minutes(workflow) == 30
    assert request_url(workflow).endswith("/v1/jobs/auto")

def test_worker_has_persistent_codex_auth_and_no_api_key_requirement():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    assert "codex-auth:/data/codex" in compose["services"]["worker"]["volumes"]
    assert compose["services"]["worker"]["environment"]["CODEX_HOME"] == "/data/codex"
```

- [ ] **Step 2: Run tests and verify missing workflow/auth service**

Run: `uv run pytest tests/integration/test_n8n_exports.py tests/unit/test_deployment_assets.py -q`

- [ ] **Step 3: Add pinned Codex CLI and auth volume**

Install a pinned `@openai/codex` CLI in the runtime image, create `/data/codex` owned by `pwuser`, and mount its named volume into the worker and the one-shot auth service. The login script runs `codex login --device-auth` and never prints or copies `auth.json`.

- [ ] **Step 4: Replace production scheduling**

Import and activate only `auto.json`. Keep `reply-watch.json` inactive because reply monitoring is owned by the worker background task. The HTTP node must have a 15-minute timeout, an execution-derived idempotency key, and an error branch that calls the worker's operational-alert endpoint before stopping.

- [ ] **Step 5: Verify Docker and workflow assets**

Run: `uv run pytest tests/integration/test_n8n_exports.py tests/unit/test_deployment_assets.py -q`

Run: `docker compose config --quiet`

- [ ] **Step 6: Commit**

```bash
git add Dockerfile compose.yaml compose.production.yaml .env.example scripts/codex-login.sh workflows/n8n tests
git commit -m "feat: schedule auto cycles and persist Codex auth"
```

### Task 6: Full verification, VPS deployment, and controlled 10-project bootstrap

**Files:**
- Modify: `docs/operations/deployment.md`
- Modify: `docs/operations/acceptance.md`
- Modify: `scripts/smoke.sh`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified VPS containers, ChatGPT-authenticated Codex CLI, active 30-minute workflow, live reply watcher, and one audited bootstrap result.

- [ ] **Step 1: Run the complete local verification gate**

Run: `uv run pytest -q`

Run: `uv run ruff check src tests`

Run: `uv run mypy src`

Run: `docker compose config --quiet`

Expected: every command exits zero.

- [ ] **Step 2: Deploy with submission still disabled**

Transfer only tracked application files to `/opt/karlancer-agent`, preserve the remote `.env`, build the image, run migrations, and start containers with `RUN_MODE=shadow` and `SUBMISSION_ENABLED=false`.

- [ ] **Step 3: Complete one-time ChatGPT device login**

Run `docker compose --profile codex-auth run --rm codex-login`, present the device URL and one-time code to the user, and wait for the user to finish the simple browser confirmation. Verify with `codex login status`; never display `auth.json`.

- [ ] **Step 4: Run shadow acceptance without external submissions**

Verify `/health/ready`, one real Telegram system message, a Codex structured analysis, one generated proposal passing the no-contact validator, the 30-minute workflow import, and live-reply monitor liveness. A failure at any point keeps submission disabled and sends a Telegram alert.

- [ ] **Step 5: Enable auto gates and execute exactly one bootstrap**

Set `RUN_MODE=auto` and `SUBMISSION_ENABLED=true`, restart only the worker, then call `/v1/jobs/auto?bootstrap_limit=10` with one idempotency key. Do not retry the bootstrap command if its result is lost; inspect PostgreSQL submissions first.

- [ ] **Step 6: Verify external results and activate schedule**

Confirm each successful project has exactly one `submissions` row, matching evidence, maximum budget, advertised duration, and no external contact text. Confirm at most 10 were sent. Activate `Karlancer - Auto Cycle`, verify its next execution time, and confirm one concise Telegram cycle summary.

- [ ] **Step 7: Verify failure controls**

Inject a local fake quota error in test mode and confirm one Telegram warning, a five-hour `retry_at`, no repeated Codex process during cooldown, and one post-cooldown probe. Confirm the worker cancels the live watcher cleanly during restart.

- [ ] **Step 8: Commit operations documentation**

```bash
git add docs/operations/deployment.md docs/operations/acceptance.md scripts/smoke.sh
git commit -m "docs: record production acceptance and recovery"
```
