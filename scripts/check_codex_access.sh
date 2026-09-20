#!/bin/sh
set -eu
exec codex exec \
  --model gpt-5.6-luna \
  --sandbox read-only \
  --ephemeral \
  --skip-git-repo-check \
  --json \
  "Reply with only OK" </dev/null
