param(
    [Parameter(Mandatory = $true)]
    [string]$ServerAddress,
    [string]$KeyPath = ".secrets/karlancer_vps_ed25519_v4",
    [string]$KnownHostsPath = ".secrets/known_hosts",
    [string]$TokenPath = ".secrets/telegram_bot_token.txt"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $TokenPath)) {
    throw "Telegram token file is missing."
}
$token = (Get-Content -LiteralPath $TokenPath -Raw).Trim()
if ($token -eq "PASTE_BOTFATHER_TOKEN_HERE" -or $token -notmatch '^\d{6,12}:[A-Za-z0-9_-]{30,}$') {
    throw "Telegram token file still contains a placeholder or invalid token."
}

$bot = Invoke-RestMethod -Method Get -Uri "https://api.telegram.org/bot$token/getMe"
if (-not $bot.ok) {
    throw "Telegram getMe validation failed."
}
$updates = Invoke-RestMethod -Method Get -Uri "https://api.telegram.org/bot$token/getUpdates"
$start = $updates.result |
    Where-Object { $_.message.chat.type -eq "private" -and $_.message.text -eq "/start" } |
    Select-Object -Last 1
if ($null -eq $start) {
    throw "No private /start message was found for this bot."
}
$chatId = [long]$start.message.chat.id

$payload = @{ token = $token; chat_id = $chatId } | ConvertTo-Json -Compress
$remotePython = @'
import json
import os
import sys
import tempfile
from pathlib import Path

payload = json.loads(sys.stdin.read())
path = Path("/opt/karlancer-agent/.env")
lines = path.read_text(encoding="utf-8").splitlines()
if "RUN_MODE=shadow" not in lines or "SUBMISSION_ENABLED=false" not in lines:
    raise SystemExit("refusing Telegram update outside safe shadow mode")
updates = {
    "TELEGRAM_BOT_TOKEN": payload["token"],
    "TELEGRAM_CHAT_ID": str(payload["chat_id"]),
}
seen = set()
result = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        result.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        result.append(line)
for key, value in updates.items():
    if key not in seen:
        result.append(f"{key}={value}")
fd, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(result) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print("Telegram configuration updated in safe shadow mode")
'@
$encodedPython = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remotePython))
$resolvedKey = (Resolve-Path -LiteralPath $KeyPath).Path
$resolvedKnownHosts = (Resolve-Path -LiteralPath $KnownHostsPath).Path
$sshArguments = @(
    "-i", $resolvedKey,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UserKnownHostsFile=$resolvedKnownHosts",
    "root@$ServerAddress"
)
$remoteUpdate = "python3 -c `"import base64;exec(base64.b64decode('$encodedPython'))`""
$payload | & ssh.exe @sshArguments $remoteUpdate
if ($LASTEXITCODE -ne 0) {
    throw "Secure VPS Telegram configuration failed."
}

$remoteRestart = "cd /opt/karlancer-agent && docker compose -f compose.yaml -f compose.production.yaml up -d --force-recreate worker && ./scripts/smoke.sh"
& ssh.exe @sshArguments $remoteRestart
if ($LASTEXITCODE -ne 0) {
    throw "VPS restart or Telegram smoke test failed."
}

Write-Output ("Telegram configured for @" + $bot.result.username + "; chat_id=" + $chatId)
