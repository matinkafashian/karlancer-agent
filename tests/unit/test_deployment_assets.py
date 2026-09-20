import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_compose_keeps_private_services_on_localhost_or_internal_network() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert "ports" not in compose["services"]["postgres"]
    assert "ports" not in compose["services"]["worker"]
    assert compose["services"]["n8n"]["ports"] == ["127.0.0.1:5678:5678"]
    assert compose["services"]["login"]["ports"] == ["127.0.0.1:6080:6080"]


def test_reply_watch_workflow_uses_private_authenticated_endpoint() -> None:
    workflow = json.loads(
        (ROOT / "workflows/n8n/reply-watch.json").read_text(encoding="utf-8")
    )
    nodes = {node["name"]: node for node in workflow["nodes"]}
    request = nodes["Check first employer replies"]["parameters"]

    assert request["url"] == "http://worker:8000/v1/jobs/reply-watch"
    assert request["headerParameters"]["parameters"] == [
        {
            "name": "X-Internal-API-Key",
            "value": "={{ $env.KARLANCER_INTERNAL_API_KEY }}",
        }
    ]
    assert "replace-with-bot-token" not in json.dumps(workflow)
    assert workflow["active"] is False


def test_playwright_runtime_matches_locked_python_package() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    lockfile = (ROOT / "uv.lock").read_text(encoding="utf-8")

    assert "playwright/python:v1.62.0-noble" in dockerfile
    assert 'name = "playwright"\nversion = "1.62.0"' in lockfile


def test_daily_backup_timer_uses_verified_backup_script() -> None:
    service = (ROOT / "deploy/karlancer-backup.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy/karlancer-backup.timer").read_text(encoding="utf-8")

    assert "ExecStart=/opt/karlancer-agent/scripts/backup.sh" in service
    assert "OnCalendar=*-*-* 03:17:00 Asia/Tehran" in timer
    assert "Persistent=true" in timer


def test_telegram_configuration_script_keeps_token_off_command_line() -> None:
    script = (ROOT / "scripts/configure-telegram.ps1").read_text(encoding="utf-8")

    assert "telegram_bot_token.txt" in script
    assert "/getUpdates" in script
    assert "sys.stdin.read()" in script
    assert "RUN_MODE=shadow" in script
    assert "SUBMISSION_ENABLED=false" in script
    assert "smoke.sh" in script
    assert "Write-Output $token" not in script


def test_worker_has_persistent_codex_auth_and_no_api_key_requirement() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]
    auth = compose["services"]["codex-login"]

    assert "codex-auth:/data/codex" in worker["volumes"]
    assert worker["environment"]["CODEX_HOME"] == "/data/codex"
    assert "OPENAI_API_KEY" not in worker["environment"]
    assert auth["profiles"] == ["codex-auth"]
    assert "codex-auth:/data/codex" in auth["volumes"]
    assert auth["command"] == ["/usr/local/bin/codex-login"]


def test_codex_cli_is_pinned_and_login_never_prints_or_copies_auth() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    login_script = (ROOT / "scripts/codex-login.sh").read_text(encoding="utf-8")

    assert "@openai/codex@0.149.1" in dockerfile
    assert "codex login --device-auth" in login_script
    assert "cli_auth_credentials_store" in login_script
    assert "auth.json" not in login_script
    assert "cat " not in login_script


def test_base_compose_enforces_all_production_resource_limits() -> None:
    base = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    production = yaml.safe_load((ROOT / "compose.production.yaml").read_text(encoding="utf-8"))
    for name, service in production["services"].items():
        assert base["services"][name]["mem_limit"] == service["mem_limit"]
        assert base["services"][name]["cpus"] == service["cpus"]
