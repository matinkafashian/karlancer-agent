#!/usr/bin/env sh
set -eu

: "${CODEX_HOME:?CODEX_HOME is required}"

mkdir -p "$CODEX_HOME"
config_path="$CODEX_HOME/config.toml"
if [ ! -f "$config_path" ]; then
    printf '%s\n' 'cli_auth_credentials_store = "file"' > "$config_path"
fi

exec codex login --device-auth
