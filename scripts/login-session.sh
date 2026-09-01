#!/usr/bin/env bash
set -euo pipefail

if [[ "${BROWSER_PROFILE_DIR:-}" == "" || "${BROWSER_PROFILE_DIR}" == "/" ]]; then
  echo "BROWSER_PROFILE_DIR must be an explicit non-root directory." >&2
  exit 2
fi

exec python -m karlancer_agent.browser.login
