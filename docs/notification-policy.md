# Owner notification and pacing policy — 2026-09-21

- Only first employer replies and actionable operational errors go to Telegram in auto mode.
  Routine summaries, cycle success, daily cap and recovery messages remain silent.
- Discovery runs independently every 30 minutes. Auto cycles check their durable sending
  window every minute; quota/model cooldown never blocks discovery by itself.
- Cap: 26 reservations (including ambiguous attempts) in any rolling 24 hours.
  Per-attempt durable random delay is 300–540 seconds; the one-minute scheduler normally
  yields 5–10 minutes. Processing, downtime and quota can make it longer.
- Codex: one persisted episode alert, no calls before retry_at, initial quota failure plus
  at most two subsequent probes. After that the provider stays hard-paused until manual
  review; restart never resets it. Reply monitoring remains independent of provider cooldown.
- Serious failures persist a global pause. Outbox delivery alone continues while paused.
- First replies have a unique per-project record. System/reply notifications use atomic
  delivery claims, a two-minute lease and at most three total attempts including uncertain
  outcomes. Known failures wait 5 then 10 minutes; background recovery checks every 30 seconds.
  Success records the Telegram receipt and first-reply handoff in one DB transaction.
- A network timeout after Telegram accepted a message, or a crash before saving its receipt,
  can still cause a duplicate on retry. Exactly-once external delivery is not guaranteed.
  Exhausted undelivered rows require manual review; restarting does not reset their budget.
- Failed Telegram delivery pauses site/model work and queues one operational error.
  No bot token, raw network exception or report body is copied into retry logs.
- Historical assisted-review APIs are retained for compatibility, not part of auto workflows
  or the system/first-reply outbox recovery loop. Their API is blocked in auto mode.
