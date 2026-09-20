# Karlancer Agent

Private, VPS-ready automation for discovering relevant Karlancer projects, drafting tailored
Persian proposals, submitting them at a human pace, and notifying the owner when an employer
replies for the first time.

## What it does

- Imports public project announcements every 30 minutes and de-duplicates them.
- Classifies programming work against an explicit AI/Python/automation skill profile.
- Generates project-specific proposals through Codex CLI with deterministic validation.
- Uses the project's maximum listed budget and listed delivery time.
- Sends at most 26 proposals in a rolling 24-hour window, normally 5–10 minutes apart.
- Detects ambiguous submissions and stops safely instead of risking a duplicate bid.
- Watches submitted projects for the first employer reply and sends one clear Telegram alert.
- Sends Telegram messages only for first replies and actionable operational failures.
- Persists quota cooldowns, retries, idempotency keys, circuit state, and delivery outbox state.

Proposal text never includes phone numbers, Telegram IDs, portfolio links, or other external
contact details. Questions are optional and are included only when a real ambiguity changes
scope, architecture, time, or price.

## Architecture

- **Python 3.12 / FastAPI** — internal job API and runtime services
- **Playwright** — authenticated Karlancer reading and proposal submission
- **PostgreSQL / SQLAlchemy / Alembic** — durable workflow and safety state
- **n8n** — schedules ingestion, due submissions, and first-reply checks
- **Codex CLI** — proposal analysis and drafting using the authenticated ChatGPT account
- **Telegram Bot API** — owner-only reply and failure notifications
- **Docker Compose** — isolated VPS deployment with explicit CPU and memory limits

The Compose project name is `karlancer`, so its containers, network, and volumes remain
separate from unrelated applications on the same VPS.

## Safety and recovery

Real submission requires both controls below:

```dotenv
RUN_MODE=auto
SUBMISSION_ENABLED=true
```

If Codex quota is exhausted, the provider circuit saves the retry time, stays silent during the
cooldown, and later performs bounded probes. A serious site, browser, or delivery failure
persists a global pause and emits a single actionable alert. Restarting containers does not erase
these states.

The reply watcher is independent of proposal generation, so an AI quota cooldown does not stop
first-reply detection. See [notification-policy.md](docs/notification-policy.md) and
[recovery.md](docs/operations/recovery.md) for the exact operational rules.

## Local verification

Requirements: Python 3.12 and `uv`.

```bash
uv sync --dev
uv run pytest -q
uv run ruff check src tests scripts
uv run mypy src
docker compose -f compose.yaml -f compose.production.yaml config --quiet
```

Tests use temporary state and do not submit real proposals.

## VPS deployment

1. Copy `.env.example` to `.env` and replace every placeholder secret.
2. Keep `.env`, browser profiles, Codex credentials, evidence, and database backups outside Git.
3. Build and start the isolated stack:

```bash
docker compose -p karlancer -f compose.yaml -f compose.production.yaml up -d --build
docker compose -p karlancer -f compose.yaml -f compose.production.yaml exec -T n8n \
  n8n import:workflow --separate --input=/workflows
```

4. Activate only these workflows in n8n: Ingest, Auto Cycle, and First Reply Watch. Keep Shadow
   inactive in automatic mode.
5. Create the authenticated Karlancer browser profile and Codex login as described in
   [deployment.md](docs/operations/deployment.md).

Operational smoke tests, backup/restore steps, and failure recovery are documented under
[`docs/operations`](docs/operations/).

## Secrets

Never commit `.env`, `.secrets/`, browser profiles, Codex authentication, Telegram tokens,
screenshots, captured HTML, database dumps, or runtime evidence. The repository is intended to
remain private even though these files are excluded.

## Status

The automated test suite covers proposal validation, pacing, quota circuits, first-reply
deduplication, Telegram outbox behavior, migrations, APIs, and the n8n exports. Production health
must still be checked after each deployment; passing tests cannot guarantee that an external site
has not changed.
