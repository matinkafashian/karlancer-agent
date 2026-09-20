"""Apply the approved cap while keeping live work disabled during acceptance."""

import shutil
from pathlib import Path

path = Path("/opt/karlancer-agent/.env")
backup = path.with_name(".env.before-acceptance-20260918")
if not backup.exists():
    shutil.copy2(path, backup)
values = {
    "DAILY_SUBMISSION_LIMIT": "26",
    "SUBMISSION_ENABLED": "false",
    "LIVE_REPLY_MONITOR_ENABLED": "false",
}
lines = path.read_text().splitlines()
seen = set()
for index, line in enumerate(lines):
    key = line.partition("=")[0].strip()
    if key in values:
        lines[index] = f"{key}={values[key]}"
        seen.add(key)
lines.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
path.write_text("\n".join(lines) + "\n")
print(values)
