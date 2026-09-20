# Backup and recovery

`scripts/backup.sh` creates a live PostgreSQL dump and briefly stops n8n while archiving its
encrypted configuration volume. Archives remain only under `backups/`; files older than seven
days are removed. The Karlancer browser profile is deliberately not copied while Chromium may
hold it open. Recreate that profile through the documented login flow after a server loss.

Daily schedule example:

```cron
17 3 * * * cd /opt/karlancer-agent && ./scripts/backup.sh >> /var/log/karlancer-backup.log 2>&1
```

Restore is intentionally destructive and requires an explicit confirmation variable:

```bash
CONFIRM_RESTORE=YES ./scripts/restore.sh \
  backups/postgres-YYYYMMDDTHHMMSSZ.sql.gz \
  backups/n8n-YYYYMMDDTHHMMSSZ.tar.gz
./scripts/smoke.sh
```

Keep an encrypted off-server copy. A backup that has never passed a restore drill is not a
verified backup.


## Local audit fixes — deployment pending (2026-09-21)

No production state was changed while implementing these fixes. Before a separately authorized
rollout, keep worker/n8n stopped, back up the database and preserve the current source and image.
Apply migration `0005_outbox_delivery` in the controlled rollout; it adds retry counters, next
attempt time and Telegram receipt ID without resetting existing delivered rows.

The unified worker takes PostgreSQL session advisory lock 4214338 for its entire lifetime.
A second unified worker fails startup. Startup quarantines leftover `reserved` submissions,
keeps their unique reservation, persists a global pause and queues an error. It never retries
an uncertain proposal click. Only one unified worker is supported; do not launch independent
submission scripts against it. SQLite is for tests, not this process-ownership guarantee.

Reconciliation is read-only on Karlancer, but updates the local submission receipt in DB.
While globally paused, a separately authorized bounded diagnostic such as
`scripts/verify_reply_delivery.py` can inspect conversations. A matched project bid can resolve
ambiguous/interrupted/success-evidence failures and records an audit event; absence of a bid
is not proof of non-submission. The original evidence and estimated send timestamp remain
explicit. A reservation is never deleted to make automatic resubmission possible. Reconciliation
does not clear the global pause; review before manually resuming.

Existing drafts use the old identity template and must be reviewed/regenerated before enabling
sending. Existing proposals and rows were not rewritten by this change. Do not bypass validation
or rewrite submitted proposals to make them match the new template.

If the public Telegram preview no longer overlaps the saved cursor, ingestion raises a history
gap and does not advance the cursor. Backfill requires bounded manual investigation. No unverified
pagination or scrolling selector was invented. Reply discovery uses stored room URLs first;
an unmatched target past the known 30-room discovery bound raises an error rather than silently
claiming full coverage. Hidden/unloaded historical rooms still require live-site verification.

Outbox checks continue during a global pause, but only for system errors and first replies. After
three failed/uncertain delivery attempts inspect the outbox and actual Telegram conversation before
any explicit reset. Never bulk-clear the attempt counter or delivered flags on restart.
