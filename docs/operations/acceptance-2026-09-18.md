> Historical record. Timing/quota statements below describe 2026-09-18, not the current local implementation. See notification-policy.md and recovery.md.

# Acceptance check — 2026-09-18

Status: **not approved for automatic sending**. No proposals were submitted during this check.

## Verified

- `ai-sales` and `karlancer` use different named volumes, Docker networks and host ports
  (5680 and 5678 respectively). No ai-sales files, containers or settings were modified.
- The production system pause was already persisted. Zero submission attempts in the preceding
  24 hours. Three first-reply events existed, all with `notified_at` set.
- Production configuration now caps attempts at 26 per rolling 24 hours. The durable reservation
  check enforces a 30-minute minimum interval, including ambiguous attempts.
- Pause checks cover automatic cycles, subsequent stages, HTTP jobs, live reply scans,
  reply notification dispatch, reservation, and the final proposal click.
- Codex quota exhaustion pauses all automated work; manual review is required to resume.
- Routine cycle and configured daily-cap notifications are suppressed in production.
- Room discovery is shared across targets in a scan instead of repeated for every project.
- Full suite: 319 passed, 1 skipped. Six auto-cycle tests rerun successfully after the final
  added check before drafting. Selected changed application files passed Ruff.
- A single real error alert was delivered to Telegram successfully, using persistent key
  `acceptance-message-timeout-20260918`.

## Unresolved production blocker

Browser navigation to Karlancer messages intermittently times out after 30 seconds, including
when waiting only for navigation commit. Separate bounded probes returned HTTP 200 and rendered
10 message rooms. A host HTTP probe returned 200 in approximately 0.75 seconds. These observations
do not establish IP blocking or account restriction as the cause. Changing the page-load waiting
condition did not eliminate the failure.

No fresh first-employer-reply delivery was verified in this session. Database records for previous
notifications and the successful error-alert delivery are not substitutes for that acceptance test.

## Deployment and recovery

- Source backup: `/opt/karlancer-agent/before-acceptance-20260918.tar.gz`.
- Environment backup: `/opt/karlancer-agent/.env.before-acceptance-20260918`.
- Updated files are in `/opt/karlancer-agent`; the worker image is rebuilt for the changes.
- `SUBMISSION_ENABLED=false`, `LIVE_REPLY_MONITOR_ENABLED=false`; global DB pause remains set.
- Stop all three Karlancer containers after completion; retain volumes and data.
- Next work: diagnose intermittent browser navigation with bounded evidence collection. Then
  perform a real first-reply delivery and duplicate-suppression check before enabling submission.
  Do not automatically clear the pause, run unbounded retries, or restart shared VPS services.
