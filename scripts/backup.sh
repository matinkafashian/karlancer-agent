#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKUP_DIR="$PROJECT_ROOT/backups"
COMPOSE="docker compose -f $PROJECT_ROOT/compose.yaml -f $PROJECT_ROOT/compose.production.yaml"

if [ ! -f "$PROJECT_ROOT/.env" ]; then
  echo "Missing $PROJECT_ROOT/.env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. "$PROJECT_ROOT/.env"
set +a

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"

mkdir -p "$BACKUP_DIR"
RESOLVED_BACKUP_DIR=$(CDPATH= cd -- "$BACKUP_DIR" && pwd)
if [ "$RESOLVED_BACKUP_DIR" != "$PROJECT_ROOT/backups" ]; then
  echo "Refusing unexpected backup directory: $RESOLVED_BACKUP_DIR" >&2
  exit 1
fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DB_ARCHIVE="$BACKUP_DIR/postgres-$STAMP.sql.gz"
N8N_ARCHIVE="$BACKUP_DIR/n8n-$STAMP.tar.gz"

$COMPOSE exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip >"$DB_ARCHIVE"

$COMPOSE stop n8n
restart_n8n() {
  $COMPOSE up -d n8n >/dev/null
}
trap restart_n8n EXIT INT TERM
$COMPOSE run --rm --no-deps --entrypoint sh n8n \
  -ec 'tar -C /home/node -czf - .n8n' >"$N8N_ARCHIVE"
restart_n8n
trap - EXIT INT TERM

sha256sum "$DB_ARCHIVE" "$N8N_ARCHIVE" >"$BACKUP_DIR/checksums-$STAMP.sha256"
find "$BACKUP_DIR" -maxdepth 1 -type f -mtime +6 -delete

echo "Backup complete: $STAMP"
