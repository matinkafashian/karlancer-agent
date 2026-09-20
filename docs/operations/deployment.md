# VPS deployment

Target: Ubuntu 24.04, 4 vCPU, 8 GB RAM, 40 GB SSD. Only SSH is public. n8n and noVNC
listen on localhost and are reached through an SSH tunnel.

## 1. Base server

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ca-certificates curl git ufw unattended-upgrades
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
```

Log out and back in after joining the Docker group. Put this repository at
`/opt/karlancer-agent` and make your user its owner.

## 2. Secrets

```bash
cd /opt/karlancer-agent
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
```

Generate a different random value for `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`,
`INTERNAL_API_KEY`, and `APPROVAL_SIGNING_KEY`. Add the BotFather token and your numeric
Telegram chat ID. Keep `RUN_MODE=shadow` and `SUBMISSION_ENABLED=false` until assisted
acceptance tests pass.

## 3. Start and import the workflow

```bash
docker compose -f compose.yaml -f compose.production.yaml up -d --build
docker compose -f compose.yaml -f compose.production.yaml exec -T n8n \
  n8n import:workflow --separate --input=/workflows
```

Open an SSH tunnel from Windows:

```powershell
ssh -L 5678:127.0.0.1:5678 your-user@SERVER_IP
```

Visit `http://127.0.0.1:5678`, create the n8n owner, inspect the imported workflow, and
activate `ingest.json` (every 30 minutes), `auto.json` (checks each minute; submits
only when due), and `reply-watch.json`. Keep `shadow.json` inactive in automatic mode.
The automatic cycle consumes the stored queue and does not ingest. Importing updated
exports does not by itself verify or activate the schedules in an existing n8n instance.

CPU and memory limits are also present in the base `compose.yaml`, so plain
`docker compose` retains them. The production overlay carries the same limits.
These repository changes have not been deployed or verified on the VPS.

## 4. Create the Karlancer login profile

The normal worker and login browser must not use the profile simultaneously.

```bash
docker compose -f compose.yaml -f compose.production.yaml stop worker
docker compose -f compose.yaml -f compose.production.yaml --profile login up login
```

In a second Windows terminal, tunnel noVNC:

```powershell
ssh -L 6080:127.0.0.1:6080 your-user@SERVER_IP
```

Open `http://127.0.0.1:6080/vnc.html`, log in to Karlancer manually, return to the server
terminal and press Enter. Then stop the login container and restart the worker:

```bash
docker compose -f compose.yaml -f compose.production.yaml --profile login down
docker compose -f compose.yaml -f compose.production.yaml up -d worker n8n
```

## 5. Acceptance

```bash
chmod 700 scripts/*.sh
./scripts/smoke.sh
```

Add `scripts/backup.sh` to root's daily cron only after running one backup and one restore
drill. See `recovery.md` and `acceptance.md`.


## Audit fixes rollout gate (not performed by the local implementation task)

Use the existing project name and both files consistently:

```bash
docker compose -p karlancer -f compose.yaml -f compose.production.yaml config --quiet
```

After explicit rollout authorization and database backup/migration review, the recreate command is:

```bash
docker compose -p karlancer -f compose.yaml -f compose.production.yaml up -d --build worker n8n
```

Do not run it as a read-only check: it starts automated work when the environment enables sending.
Base Compose now also carries the resource ceilings so omitting the production overlay does not
silently remove them. Verify actual Memory/NanoCpus with `docker inspect` after a future recreate;
editing YAML alone does not change existing containers. Leave all ai-sales containers/networks/
volumes untouched. Review free disk space before building; do not use a global Docker prune.

Workflow exports are inactive by default. Import/review and activate Ingest (30 minutes), Auto
(1 minute) and First Reply Watch (2 minutes) only at the authorized activation step. Shadow remains
disabled. Check stored n8n workflows rather than assuming import changes their active state.
See `recovery.md` for migration, interrupted-reservation recovery and old-draft handling.
