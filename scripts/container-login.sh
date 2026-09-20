#!/usr/bin/env sh
set -eu

: "${BROWSER_PROFILE_DIR:?BROWSER_PROFILE_DIR is required}"

Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
x11vnc -display :99 -localhost -forever -shared -nopw -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/novnc.log 2>&1 &

exec python -m karlancer_agent.browser.login
