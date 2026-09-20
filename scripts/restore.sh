#!/usr/bin/env sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "Usage: CONFIRM_RESTORE=YES $0 <postgres.sql.gz> <n8n.tar.gz>" >&2
  exit 2
fi
if [ "${CONFIRM_RESTORE:-}" != "YES" ]; then
  echo "Restore is destructive; set CONFIRM_RESTORE=YES explicitly." >&2
  exit 2
fi

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKUP_DIR="$PROJECT_ROOT/backups"
DB_ARCHIVE=$(readlink -f -- "$1")
N8N_ARCHIVE=$(readlink -f -- "$2")
COMPOSE="docker compose -f $PROJECT_ROOT/compose.yaml -f $PROJECT_ROOT/compose.production.yaml"

case "$DB_ARCHIVE" in "$BACKUP_DIR"/*) ;; *) echo "Database archive must be under $BACKUP_DIR" >&2; exit 2 ;; esac
case "$N8N_ARCHIVE" in "$BACKUP_DIR"/*) ;; *) echo "n8n archive must be under $BACKUP_DIR" >&2; exit 2 ;; esac
[ -f "$DB_ARCHIVE" ] || { echo "Missing $DB_ARCHIVE" >&2; exit 2; }
[ -f "$N8N_ARCHIVE" ] || { echo "Missing $N8N_ARCHIVE" >&2; exit 2; }

set -a
# shellcheck disable=SC1091
. "$PROJECT_ROOT/.env"
set +a
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"

case "$POSTGRES_DB$POSTGRES_USER" in *[!A-Za-z0-9_]*) echo "Unsafe database identifier" >&2; exit 2 ;; esac

$COMPOSE stop worker n8n
$COMPOSE exec -T postgres dropdb --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"
$COMPOSE exec -T postgres createdb -U "$POSTGRES_USER" "$POSTGRES_DB"
gzip -dc "$DB_ARCHIVE" | $COMPOSE exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$POSTGRES_DB"

$COMPOSE run --rm --no-deps --entrypoint sh -T n8n \
  -ec 'find /home/node/.n8n -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xzf - -C /home/node' \
  <"$N8N_ARCHIVE"
$COMPOSE up -d worker n8n

echo "Restore complete. Run scripts/smoke.sh now."
