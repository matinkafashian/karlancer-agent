#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMPOSE="docker compose -f $PROJECT_ROOT/compose.yaml -f $PROJECT_ROOT/compose.production.yaml"

$COMPOSE config --quiet
$COMPOSE exec -T postgres pg_isready
$COMPOSE exec -T worker curl --fail --silent http://localhost:8000/health/live
echo
$COMPOSE exec -T n8n n8n --version
$COMPOSE exec -T worker karlancer-agent telegram-smoke

echo "Infrastructure and Telegram smoke checks passed."
