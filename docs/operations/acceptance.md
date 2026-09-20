# Pre-production acceptance

Do not enable automatic proposal submission until every earlier stage passes.

- Infrastructure: all containers are healthy after reboot; only SSH is publicly reachable.
- Telegram: `scripts/smoke.sh` delivers one real message to the intended private chat.
- Login: a headless worker reports `signed_in`; expiry produces an error and submits nothing.
- Reply watcher: a controlled first employer reply creates exactly one Telegram report with
  project title, short summary, reply summary, response time, and direct conversation button.
- Handoff: repeated scans do not send the same report again and never reply to the employer.
- Shadow stage: at least ten matching and ten rejected projects are reviewed manually.
- Assisted stage: at least five proposals are approved manually and match the selected maximum
  budget and advertised duration; no phone number, Telegram ID, or other contact data appears.
- Recovery: one database and n8n backup has been restored on a disposable Compose project.

Only after those checks may `.env` move from `shadow` to `assisted`; `auto` is a separate,
explicit final decision.
